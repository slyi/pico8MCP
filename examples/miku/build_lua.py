"""Miku-specific packing rules; reusable source/ZX0/cart helpers are in pico8mcp."""
from pathlib import Path
import hashlib,json,os,re,struct,sys
HERE=Path(__file__).resolve().parent
OUT=HERE/'build/carts'
CAPACITY=0x4100
# Prefer this checkout; standalone copies can use the shared installation.
repository=HERE.parents[1]
if os.environ.get('PICO8_MCP_SOURCE'):
    helper_root=Path(os.environ['PICO8_MCP_SOURCE']).expanduser().resolve()
elif (repository/'source_build.py').is_file():helper_root=repository
else:
    if sys.platform=='win32':base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
    elif sys.platform=='darwin':base=Path.home()/'Library/Application Support'
    else:base=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    helper_root=Path(os.environ.get('PICO8_MCP_HOME') or base/'pico8mcp')
sys.path.insert(0,str(helper_root))
from source_build import read_source,cart_text,compress_zx0_many,include_files

def model_bytes(model):
    out=bytearray()
    def byte(value):out.append(int(value)&255)
    def word(value):out.extend(struct.pack('<H',int(value)&65535))
    for row in model['bones']:byte(row[0])
    for row in model['bones']:
        for value in row[1:]:word(value)
    for value in model['root_offset']:word(value)
    previous={}
    for row in model['vertex_data']:
        if len(row)%5:raise ValueError('Vertex rows require groups of five integers')
        byte(len(row)//5)
        if sum(row[i+1] for i in range(0,len(row),5))!=255:raise ValueError('Vertex weights must sum to 255')
        for i in range(0,len(row),5):
            bone,weight,dx,dy,dz=row[i:i+5];old=previous.get(bone,[0,0,0])
            position=[a+b for a,b in zip(old,[dx,dy,dz])];previous[bone]=position
            byte(bone);byte(weight)
            for value in position:word(value)
    for row in model['helpers']:
        byte(row[0])
        for value in row[1:]:word(value)
    a=0
    for da,db,dc,material,flags in model['triangle_data']:
        a+=da;b=a+db;c=a+dc
        word(a-1|(512 if flags&1 else 0));word(b-1|((material-1)<<9));word(c-1)
    for row in model['ropes']:
        byte(row[0])
        for value in row[1:]:word(value)
    if len(out)!=9009:raise ValueError('Expected 9009 bytes of packed model data')
    return bytes(out)


def music_bytes(music):
    bank=bytearray(64*68);patterns=bytearray();speed=music['speed']
    if not 1<=speed<=255:raise ValueError('Music speed must be 1..255')
    if len(music['notes'])!=512:raise ValueError('Expected 512 four-channel note rows')
    for row_index,row in enumerate(music['notes']):
        if len(row)!=4:raise ValueError('Music rows require four note words')
        for channel,value in enumerate(row):
            address=((row_index//32)*4+channel)*68
            struct.pack_into('<H',bank,address+(row_index%32)*2,value)
            bank[address+65]=speed
    for s0,s1,s2,s3,flags in music['patterns']:
        values=[s0,s1,s2,s3]
        for channel in range(3):
            if flags&(1<<channel):values[channel]|=128
        patterns.extend(values)
    if len(patterns)!=64:raise ValueError('Expected 16 music patterns')
    return bytes(bank),bytes(patterns)



def build(force=False):
    names=['miku','miku_a','miku_b','miku_c']
    hashes={name:hashlib.sha256((HERE/(name+'.lua')).read_bytes()).hexdigest() for name in names}
    for name in names:
        for dependency in include_files(HERE/(name+'.lua')):
            hashes[str(dependency.relative_to(HERE))]=hashlib.sha256(dependency.read_bytes()).hexdigest()
    hashes['builder']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    hashes['pico8mcp_source_build']=hashlib.sha256(Path(cart_text.__code__.co_filename).read_bytes()).hexdigest()
    manifest=OUT/'build.json'
    if not force and manifest.exists():
        previous=json.loads(manifest.read_text())
        if previous['sources']==hashes and all((OUT/(name+'.p8')).exists() for name in names):
            return dict(previous,cached=True)
    print('Building four carts from Lua sources...',flush=True)
    sources={name:read_source(HERE/(name+'.lua')) for name in names}
    main,code=sources['miku'];chunks={}
    for name in names[1:]:
        for key,chunk in sources[name][0]['dance_data']['chunks'].items():
            if key in chunks:raise ValueError('Duplicate motion chunk '+str(key))
            chunks[key]=chunk
    if set(chunks)!=set(range(1,16)):raise ValueError('Expected chunks 1..15 exactly once')
    directory=[];checks=[];raw_parts=[];total=0
    for index in range(1,16):
        rows=chunks[index]['deltas']
        if len(rows)!=121 or any(len(row)!=63 for row in rows):raise ValueError('Each motion chunk needs 121 rows of 63 integer channels')
        raw=bytearray();values=[0]*63;crc1=crc2=0
        for channel in range(63):
            zs=[];value=0
            for row in rows:
                delta=row[channel]
                if type(delta) is not int:raise ValueError('Motion deltas must be integers')
                z=2*delta if delta>=0 else -2*delta-1
                if not 0<=z<=65535:raise ValueError('Motion delta exceeds 16-bit zigzag range')
                zs.append(z);value+=delta
                if channel>=3:value%=512
                crc1=(crc1+value)%251;crc2=(crc2+crc1)%251
            raw.extend(z&255 for z in zs);raw.extend(z>>8 for z in zs)
        raw_parts.append((f'motion-{index-1:02}',bytes(raw)));checks.append([crc1,crc2])
    packed_parts=compress_zx0_many(raw_parts,HERE/'build')
    for packed in packed_parts:
        directory.append([total//CAPACITY+1,total%CAPACITY,len(packed)])
        total+=len(packed)
    if total>3*CAPACITY:raise ValueError(f'Motion requires {total} compressed bytes; capacity is {3*CAPACITY}')
    stream=b''.join(packed_parts)
    code,count=re.subn(r'(?m)^motion_dir=.*$',
        'motion_dir='+json.dumps(directory,separators=(',',':')).replace('[','{').replace(']','}'),code,count=1)
    if count!=1:raise ValueError('Main Lua code must contain motion_dir assignment')
    main_ram=bytearray(0x4300);mesh=model_bytes(main['model_data']);audio,patterns=music_bytes(main['music_data'])
    main_ram[:len(mesh)]=mesh;main_ram[0x3200:0x4300]=audio;main_ram[0x3100:0x3140]=patterns
    outputs={'miku':cart_text(code,main_ram)}
    for index,letter in enumerate('abc'):
        payload=stream[index*CAPACITY:(index+1)*CAPACITY];ram=bytearray(0x4300)
        ram[:min(0x3000,len(payload))]=payload[:0x3000]
        if len(payload)>0x3000:ram[0x3200:0x3200+len(payload)-0x3000]=payload[0x3000:]
        outputs['miku_'+letter]=cart_text(sources['miku_'+letter][1],ram)
    OUT.mkdir(parents=True,exist_ok=True)
    for name,text in outputs.items():(OUT/(name+'.p8')).write_text(text,encoding='utf-8')
    result=dict(sources=hashes,carts=4,motion_bytes=total,motion_checksums=checks,output=str(OUT),cached=False)
    manifest.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'Built 4 carts from Lua; ZX0 motion {total} bytes.',flush=True)
    return result


if __name__=='__main__':print(json.dumps(build(),indent=2))
