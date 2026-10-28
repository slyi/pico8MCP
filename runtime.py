"""Official Education VM cache; no PICO-8 binaries are bundled with this adapter."""
from __future__ import annotations
import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORIGIN='https://www.pico-8-edu.com'
MAX_BYTES=16*1024*1024

def download(url: str) -> bytes:
    request=urllib.request.Request(url,headers={
        'User-Agent':'Mozilla/5.0 (PICO-8 MCP local setup)', 'Referer':ORIGIN+'/'})
    with urllib.request.urlopen(request,timeout=40) as response:
        data=response.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('Runtime download exceeds 16 MiB limit')
    return data

def setup(cache: Path, update: bool=False) -> dict:
    manifest_path=cache/'manifest.json'
    if manifest_path.exists() and not update:
        info=json.loads(manifest_path.read_text())
        payload=cache/'runtime.js'
        if payload.exists() and hashlib.sha256(payload.read_bytes()).hexdigest()==info['sha256']:
            return dict(info,cached=True)
        raise ValueError('Runtime cache is incomplete or corrupt; run setup with update=true')
    html=download(ORIGIN+'/').decode('utf-8')
    match=re.search(r'/play/(pico8_edu_[0-9]+\.js)',html)
    if not match:raise ValueError('Official page no longer exposes a recognized Education player')
    url=ORIGIN+'/play/'+match.group(1)
    payload=download(url)
    if b'var Module' not in payload[:500]:raise ValueError('Downloaded player is not the expected JavaScript runtime')
    # Current Education player embeds its boot files. Fail clearly if a future
    # player needs external packages, rather than silently claiming offline support.
    external=re.findall(rb'["\']([^"\'\s]+\.(?:wasm|mem|data))["\']',payload)
    if external:raise ValueError('This player requires external runtime packages: '+str(external))
    info=dict(source=url,version=match.group(1),bytes=len(payload),
              sha256=hashlib.sha256(payload).hexdigest(),
              fetchedAt=datetime.now(timezone.utc).isoformat(),
              publisher='Lexaloffle',offlineFiles=['runtime.js'],
              notice='PICO-8 Education Edition remains the property of Lexaloffle. Cache is local; not redistributed.')
    cache.mkdir(parents=True,exist_ok=True)
    temp=cache/f'runtime.js.{os.getpid()}.part';temp.write_bytes(payload);temp.replace(cache/'runtime.js')
    temp=cache/f'manifest.json.{os.getpid()}.part';temp.write_text(json.dumps(info,indent=2));temp.replace(manifest_path)
    return dict(info,cached=False)
