"""Reusable literal-Lua asset reading, text-cart assembly and upstream ZX0 caching.

These helpers do not launch a browser or execute project Lua/Python code.
Project-specific mesh and animation packing belongs in the project builder.
"""
from pathlib import Path
import json
import re
import struct

CODE_MARKER='-- ACTUAL CARTRIDGE LUA CODE\n'
HEX_MARKER='\n-- Exact physical ROM asset bytes;'
TOKEN=re.compile(r'\s+|--[^\n]*|"(?:\\.|[^"\\])*"|-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|[A-Za-z_]\w*|[{}\[\]=,;]')


class LiteralReader:
    """Small reader for the documented literal-only Lua asset tables."""
    def __init__(self,text):
        self.tokens=[];position=0
        for match in TOKEN.finditer(text):
            if text[position:match.start()].strip():raise ValueError('Unsupported Lua data near '+text[position:position+50])
            token=match.group();position=match.end()
            if token.isspace() or token.startswith('--'):continue
            self.tokens.append(token)
        if text[position:].strip():raise ValueError('Unsupported trailing Lua data')
        self.index=0
    def take(self,expected=None):
        token=self.tokens[self.index];self.index+=1
        if expected is not None and token!=expected:raise ValueError(f'Expected {expected}, found {token}')
        return token
    def value(self):
        token=self.take()
        if token=='{':
            result={};next_index=1;keyed=False
            while self.tokens[self.index]!='}':
                if self.tokens[self.index]=='[':
                    self.take('[');key=self.value();self.take(']');self.take('=');keyed=True
                elif self.index+1<len(self.tokens) and self.tokens[self.index+1]=='=':
                    key=self.take();self.take('=');keyed=True
                else:key=next_index;next_index+=1
                if key in result:raise ValueError('Duplicate Lua table key: '+str(key))
                result[key]=self.value()
                if self.tokens[self.index] in (',',';'):self.take()
                elif self.tokens[self.index]!='}':raise ValueError('Expected comma in Lua table')
            self.take('}')
            return result if keyed else list(result.values())
        if token.startswith('"'):return json.loads(token)
        if token in ('true','false','nil'):return {'true':True,'false':False,'nil':None}[token]
        try:return float(token) if any(c in token for c in '.eE') and not token.lower().startswith('0x') else int(token,0) if 'x' in token.lower() else int(token)
        except ValueError as exc:raise ValueError('Asset tables allow literal values only: '+token) from exc
    def assignments(self):
        result={}
        while self.index<len(self.tokens):
            name=self.take();self.take('=');result[name]=self.value()
            if self.index<len(self.tokens) and self.tokens[self.index]==';':self.take()
        return result


def include_files(path,root=None):
    path=Path(path).resolve();root=Path(root or path.parent).resolve();found=[]
    def visit(current,chain):
        if not current.is_relative_to(root):raise ValueError('Lua include outside source root: '+str(current))
        if current in chain:raise ValueError('Cyclic Lua include: '+str(current))
        if len(chain)>=32:raise ValueError('Lua include depth exceeds 32')
        if current.stat().st_size>1024*1024:raise ValueError('Lua source/include exceeds 1 MiB')
        found.append(current)
        for match in re.finditer(r'^\s*#include\s+([^\n]+)$',current.read_text(encoding='utf-8'),re.M|re.I):
            name=match[1].strip().strip('"\'')
            visit((current.parent/name).resolve(),chain+[current])
    visit(path,[])
    return list(dict.fromkeys(found))


def expand_includes(code,path,root=None,chain=None):
    path=Path(path).resolve();root=Path(root or path.parent).resolve();chain=list(chain or [path])
    def replace(match):
        name=match[1].strip().strip('"\'');child=(path.parent/name).resolve()
        if not child.is_relative_to(root):raise ValueError('Lua include outside source root: '+str(child))
        if child in chain or len(chain)>=32:raise ValueError('Cyclic or too-deep Lua include')
        if child.stat().st_size>1024*1024:raise ValueError('Lua include exceeds 1 MiB')
        return '-- included: '+name+'\n'+expand_includes(child.read_text(encoding='utf-8'),child,root,chain+[child])
    result=re.sub(r'^\s*#include\s+([^\n]+)$',replace,code,flags=re.M|re.I)
    if len(result.encode())>1024*1024:raise ValueError('Expanded Lua exceeds 1 MiB')
    return result


def read_source(path,root=None):
    text=Path(path).read_text(encoding='utf-8')
    if CODE_MARKER not in text:return {},expand_includes(text,path,root)
    assets,code=text.split(CODE_MARKER,1)
    code=code.split(HEX_MARKER,1)[0].rstrip()+'\n'
    return LiteralReader(assets).assignments(),expand_includes(code,path,root)



def compress_zx0(raw,cache_dir,tag):
    """Cache a verified pure-Python ZX0 v2 stream. No external tools."""
    from zx0_codec import compress,decompress
    if not re.fullmatch(r'[A-Za-z0-9_-]+',tag):raise ValueError('Invalid compression cache tag')
    raw=bytes(raw);folder=Path(cache_dir);folder.mkdir(parents=True,exist_ok=True)
    source=folder/(tag+'.bin');packed=source.with_suffix('.zx0')
    if source.exists() and packed.exists() and source.read_bytes()==raw:
        data=packed.read_bytes()
        try:
            if decompress(data,max_output=len(raw))==raw:return data
        except ValueError:pass
    data=compress(raw)
    if decompress(data,max_output=len(raw))!=raw:raise ValueError('ZX0 round-trip failed')
    source.write_bytes(raw);packed.write_bytes(data)
    return data


def _compress_job(job):
    tag,raw,folder=job
    return compress_zx0(raw,folder,tag)


def compress_zx0_many(items,cache_dir,workers=4):
    """Compress independent streams in worker processes; call under a main guard."""
    from concurrent.futures import ProcessPoolExecutor
    jobs=[(tag,bytes(raw),str(cache_dir)) for tag,raw in items]
    if not jobs:return []
    if workers==1:return [_compress_job(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=min(workers,len(jobs))) as pool:
        return list(pool.map(_compress_job,jobs))


def cart_text(code,ram):
    if len(ram)!=0x4300:raise ValueError('Asset RAM must contain exactly 0x4300 bytes')
    gfx=''.join(f'{v&15:x}{v>>4:x}' for v in ram[:0x2000])
    lines=['pico-8 cartridge // http://www.pico-8.com','version 42','__lua__',code.rstrip(),
           '__gfx__',*([gfx[i:i+128] for i in range(0,len(gfx),128)]),'__map__']
    lines += [ram[i:i+128].hex() for i in range(0x2000,0x3000,128)]
    lines += ['__gff__',*[ram[i:i+128].hex() for i in range(0x3000,0x3100,128)],'__sfx__']
    for sfx in range(64):
        block=ram[0x3200+sfx*68:0x3200+(sfx+1)*68];line=block[64:68].hex()
        for step in range(32):
            value=struct.unpack_from('<H',block,step*2)[0]
            line+=f'{value&63:02x}{((value>>6)&7)|((value>>12)&8):x}{(value>>9)&7:x}{(value>>12)&7:x}'
        lines.append(line)
    lines += ['__music__']
    for index in range(64):
        values=ram[0x3100+index*4:0x3104+index*4]
        flags=sum((1<<c) for c in range(3) if values[c]&128)
        lines.append(f'{flags:02x} '+''.join(f'{v&127:02x}' for v in values))
    return '\n'.join(lines)+'\n'


def build_cart(source,out,root=None):
    """Build one cart from literal asset_bytes/asset_regions and its Lua code."""
    assets,code=read_source(source,root)
    unknown=set(assets)-{'asset_bytes','asset_regions'}
    if unknown:raise ValueError('Use a project packer for custom asset tables: '+', '.join(sorted(unknown)))
    ram=bytearray(0x4300)
    regions=list(assets.get('asset_regions',[]))
    if 'asset_bytes' in assets:regions.insert(0,dict(address=0,bytes=assets['asset_bytes']))
    for region in regions:
        address=region['address'];data=bytes(region['bytes'])
        if type(address) is not int or address<0 or address+len(data)>len(ram):raise ValueError('Asset region outside cartridge RAM')
        ram[address:address+len(data)]=data
    target=Path(out);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(cart_text(code,ram),encoding='utf-8')
    return dict(source=str(source),out=str(target),bytes=target.stat().st_size)


def read_cart_assets(path):
    text=path.read_text(encoding='utf-8')
    sections={m.group(1):m.group(2) for m in re.finditer(
        r'^__([a-z0-9_]+)__\n(.*?)(?=^__[a-z0-9_]+__\n|\Z)',text,re.M|re.S)}
    ram=bytearray(0x4300)
    gfx=''.join(sections.get('gfx','').split())
    for i in range(0,len(gfx),2):ram[i//2]=int(gfx[i],16)|(int(gfx[i+1],16)<<4)
    data=bytes.fromhex(sections.get('map',''))
    ram[0x2000:0x2000+len(data)]=data
    data=bytes.fromhex(sections.get('gff',''))
    ram[0x3000:0x3000+len(data)]=data
    for index,line in enumerate(sections.get('sfx','').splitlines()):
        if not line.strip():continue
        address=0x3200+index*68
        ram[address+64:address+68]=bytes.fromhex(line[:8])
        for note in range(32):
            entry=line[8+note*5:13+note*5]
            pitch=int(entry[:2],16);wave=int(entry[2],16)
            volume=int(entry[3],16);effect=int(entry[4],16)
            value=pitch|((wave&7)<<6)|(volume<<9)|(effect<<12)|((wave&8)<<12)
            struct.pack_into('<H',ram,address+note*2,value)
    for index,line in enumerate(sections.get('music','').splitlines()):
        if not line.strip():continue
        flags,notes=line.split()
        values=bytearray.fromhex(notes)
        flags=int(flags,16)
        for channel in range(3):
            if flags&(1<<channel):values[channel]|=128
        ram[0x3100+index*4:0x3104+index*4]=values
    return bytes(ram)


def lua_scalar(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if value is None:return 'nil'
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    if isinstance(value,float):return format(value,'.12g')
    return str(value)


def dump_lua(value,indent=0):
    pad=' '*indent
    if isinstance(value,(list,tuple)):
        if not value:return '{}'
        if all(not isinstance(v,(dict,list,tuple)) for v in value):
            return '{'+', '.join(lua_scalar(v) for v in value)+'}'
        return '{\n'+''.join(' '*(indent+2)+dump_lua(v,indent+2)+',\n' for v in value)+pad+'}'
    if isinstance(value,dict):
        return '{\n'+''.join(' '*(indent+2)+'['+lua_scalar(k)+'] = '+dump_lua(v,indent+2)+',\n'
                              for k,v in value.items())+pad+'}'
    return lua_scalar(value)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Build a text .p8 from Lua code and literal asset tables.')
    parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    args=parser.parse_args()
    print(json.dumps(build_cart(args.source,args.out),indent=2))
