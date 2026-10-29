"""Small dependency-free P64 ROM / typed POD writer; no external executables.

Container/PXU layout reference: shrinko8's picotron_cart.py/picotron_fs.py
https://github.com/thisismypassport/shrinko8 (MIT).
LZ4 block format: https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md
This is a local implementation of those formats, not a new asset codec.
"""
import struct
from pathlib import PurePosixPath

MAX_RAW=16*1024*1024
ROM_LIMIT=256*1024

def lz4_compress(raw):
    raw=bytes(raw);out=bytearray();table={};pos=anchor=0;size=len(raw)
    def extra(value):
        while value>=255:out.append(255);value-=255
        out.append(value)
    def emit(end,offset=0,length=0):
        literal=end-anchor;match=max(0,length-4)
        out.append(min(15,literal)*16+min(15,match))
        if literal>=15:extra(literal-15)
        out.extend(raw[anchor:end])
        if offset:
            out.extend(struct.pack('<H',offset))
            if match>=15:extra(match-15)
    while pos<=size-12:
        key=raw[pos:pos+4];previous=table.get(key);table[key]=pos
        if previous is None or pos-previous>65535:pos+=1;continue
        length=4
        while pos+length<size-5 and raw[previous+length]==raw[pos+length]:length+=1
        emit(pos,pos-previous,length)
        end=pos+length
        for i in range(pos+1,end):table[raw[i:i+4]]=i
        pos=anchor=end
    emit(size)
    return bytes(out)

def lz4_decompress(data,limit=MAX_RAW):
    data=bytes(data);out=bytearray();pos=0
    def length(value):
        nonlocal pos
        if value==15:
            while True:
                if pos>=len(data):raise ValueError('Truncated LZ4 length')
                byte=data[pos];pos+=1;value+=byte
                if byte<255:break
        return value
    while pos<len(data):
        token=data[pos];pos+=1;n=length(token>>4)
        if pos+n>len(data) or len(out)+n>limit:raise ValueError('Invalid LZ4 literal size')
        out.extend(data[pos:pos+n]);pos+=n
        if pos==len(data):break
        if pos+2>len(data):raise ValueError('Truncated LZ4 offset')
        offset=struct.unpack_from('<H',data,pos)[0];pos+=2;n=length(token&15)+4
        if not 0<offset<=len(out) or len(out)+n>limit:raise ValueError('Invalid LZ4 match')
        for _ in range(n):out.append(out[-offset])
    return bytes(out)

def valid_path(name):
    if not isinstance(name,str) or not name or '\x00' in name or '\\' in name or ':' in name:
        raise ValueError('Invalid cartridge path')
    p=PurePosixPath(name)
    if p.is_absolute() or any(part in ('.','..') for part in name.rstrip('/').split('/')):
        raise ValueError('Cartridge paths must be relative and contained')
    return name

def pack_rom(files,limit=ROM_LIMIT):
    if 'main.lua' not in files:raise ValueError('Picotron project requires main.lua')
    raw=bytearray()
    for name,data in sorted(files.items()):
        valid_path(name);raw.extend(name.encode('utf-8')+b'\0')
        if name.endswith('/'):
            if data is not None:raise ValueError('Directory has file data')
        else:
            data=bytes(data)
            raw.extend(bytes([len(data)]) if len(data)<255 else b'\xff'+struct.pack('<I',len(data)))
            raw.extend(data)
        if len(raw)>MAX_RAW:raise ValueError('Cartridge exceeds 16 MiB unpacked limit')
    compressed=lz4_compress(raw)
    result=b'p64\x02'+struct.pack('<I',len(compressed))+compressed
    if len(result)>limit:raise ValueError(f'P64 ROM is {len(result)} bytes; limit is {limit}')
    return result

def unpack_rom(data):
    data=bytes(data)
    if len(data)<8 or data[:4]!=b'p64\x02' or struct.unpack_from('<I',data,4)[0]!=len(data)-8:
        raise ValueError('Invalid or unsupported P64 ROM header')
    if len(data)>ROM_LIMIT:raise ValueError('P64 ROM exceeds 256 KiB')
    raw=lz4_decompress(data[8:]);files={};pos=0
    while pos<len(raw):
        end=raw.find(0,pos)
        if end<0:raise ValueError('Unterminated cartridge filename')
        name=valid_path(raw[pos:end].decode('utf-8'));pos=end+1
        if name in files:raise ValueError('Duplicate cartridge file')
        if name.endswith('/'):files[name]=None;continue
        if pos>=len(raw):raise ValueError('Missing cartridge file size')
        size=raw[pos];pos+=1
        if size==255:
            if pos+4>len(raw):raise ValueError('Truncated cartridge file size')
            size=struct.unpack_from('<I',raw,pos)[0];pos+=4
        if pos+size>len(raw):raise ValueError('Truncated cartridge file')
        files[name]=raw[pos:pos+size];pos+=size
    if 'main.lua' not in files:raise ValueError('Missing main.lua')
    return files

def userdata_pod(kind,width,values,height=None):
    if kind not in ('u8','i16'):raise ValueError('Supported userdata types: u8, i16')
    if type(width) is not int or width<=0 or (height is not None and (type(height) is not int or height<=0)):
        raise ValueError('Invalid userdata dimensions')
    count=width*(height or 1)
    if kind=='u8':raw=bytes(values)
    else:
        values=list(values);raw=struct.pack('<'+'h'*len(values),*values)
    if len(raw)!=(1 if kind=='u8' else 2)*count:raise ValueError('Userdata size does not match dimensions')
    # Picotron 0.3.0d's uncompressed PXU i16 path copies element-count
    # bytes rather than element-count * 2. Standard PXU RLE avoids that
    # truncation; this matters for the second half of a large motion buffer.
    flags=(3 if kind=='u8' else 12)|0x800|(0x1000 if kind=='u8' else 0x8000)|(0x40 if height is not None else 0)
    pxu=b'pxu\0'+struct.pack('<HI',flags,width)
    if height is not None:pxu+=struct.pack('<I',height)
    if kind=='u8':pxu+=raw
    else:
        encoded=bytearray([0])  # RLE bit width: all values explicit.
        i=0
        while i<len(values):
            end=i+1
            while end<len(values) and values[end]==values[i]:end+=1
            run=end-i
            encoded.append(min(255,run-1));encoded.extend(struct.pack('<h',values[i]))
            if run>=256:
                remaining=run-256
                while remaining>=255:encoded.append(255);remaining-=255
                encoded.append(remaining)
            i=end
        pxu+=encoded
    compressed=lz4_compress(pxu)
    pod=b'lz4\0'+struct.pack('<II',len(compressed),len(pxu))+compressed
    return b'--[[pod]]'+(pod if len(pod)<len(pxu) else pxu)
