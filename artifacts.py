"""Dependency-free native PICO-8 PNG metrics and explicit-file ZIP packaging."""
from pathlib import Path
import struct
import zipfile
import zlib


def png_rom(path):
    data=Path(path).read_bytes()
    if len(data)>8*1024*1024 or data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Expected a PICO-8 PNG cart')
    position=8;compressed=[];palette=None;alpha=b'';header=None
    while position+12<=len(data):
        size=struct.unpack_from('>I',data,position)[0];kind=data[position+4:position+8]
        value=data[position+8:position+8+size]
        if len(value)!=size:raise ValueError('Truncated PNG chunk')
        if kind==b'IHDR':header=struct.unpack('>IIBBBBB',value)
        elif kind==b'IDAT':compressed.append(value)
        elif kind==b'PLTE':palette=[tuple(value[i:i+3]) for i in range(0,len(value),3)]
        elif kind==b'tRNS':alpha=value
        position+=12+size
        if kind==b'IEND':break
    if not header:raise ValueError('Missing PNG header')
    width,height,depth,color,compression,filters,interlace=header
    if (width,height)!=(160,205) or depth!=8 or color not in (2,3,6) or compression or filters or interlace:
        raise ValueError('Expected a non-interlaced 160x205, 8-bit RGB/RGBA/indexed cart PNG')
    channels={2:3,3:1,6:4}[color];stride=width*channels;expected=height*(stride+1)
    raw=zlib.decompressobj().decompress(b''.join(compressed),expected+1)
    if len(raw)!=expected:raise ValueError('Invalid PNG scanline size')
    prior=bytearray(stride);rom=bytearray();position=0
    def paeth(a,b,c):
        p=a+b-c;da,db,dc=abs(p-a),abs(p-b),abs(p-c)
        return a if da<=db and da<=dc else b if db<=dc else c
    for _ in range(height):
        mode=raw[position];position+=1;row=bytearray(raw[position:position+stride]);position+=stride
        if mode>4:raise ValueError('Invalid PNG filter')
        for i in range(stride):
            left=row[i-channels] if i>=channels else 0;up=prior[i];corner=prior[i-channels] if i>=channels else 0
            predictor=(0,left,up,(left+up)//2,paeth(left,up,corner))[mode]
            row[i]=(row[i]+predictor)&255
        for i in range(0,stride,channels):
            if color==3:
                if palette is None:raise ValueError('Missing PNG palette')
                index=row[i];r,g,b=palette[index];a=alpha[index] if index<len(alpha) else 255
            else:
                r,g,b=row[i:i+3];a=row[i+3] if channels==4 else 255
            rom.append((b&3)|((g&3)<<2)|((r&3)<<4)|((a&3)<<6))
        prior=row
    return bytes(rom[:32768])


def cart_sizes(path):
    path=Path(path);rom=png_rom(path);code=rom[0x4300:]
    if code[:4]==b'\x00pxa':
        characters=int.from_bytes(code[4:6],'big');size=int.from_bytes(code[6:8],'big');encoding='pxa'
    elif code[:4]==b':c:\0':raise ValueError('Legacy pre-PXA compression is not supported by this inspector')
    else:characters=size=len(code.split(b'\0',1)[0]);encoding='raw'
    return dict(pngFileBytes=path.stat().st_size,romPayloadBytes=len(rom),luaCharacters=characters,
                compressedLuaBytes=size,compressedLuaCapacity=0x8000-0x4300,encoding=encoding)


def package_files(root,files,out):
    root=Path(root).resolve();target=Path(out).resolve();sources=[]
    for name in files:
        path=(root/name).resolve()
        if not path.is_relative_to(root) or path==target:raise ValueError('Package inputs must stay under root and exclude output')
        if not path.is_file():raise ValueError('Package input missing: '+str(path))
        sources.append(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(set(sources)):archive.write(path,path.relative_to(root).as_posix())
    return dict(zip=str(target),bytes=target.stat().st_size,files=len(set(sources)))
