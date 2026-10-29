"""Pinned official Picotron JS/WASM export player, cached locally on demand."""
import hashlib,json,os,urllib.request
from pathlib import Path

URL='https://www.lexaloffle.com/play/picotron_0300d.js'
SHA256='a6980e5c4345a8181788ba745f7787cb9ffdd050e3cd882d7b459838ae3eb548'

def setup(cache,update=False):
    cache=Path(cache);payload=cache/'runtime.js';manifest=cache/'manifest.json'
    if payload.exists() and not update:
        data=payload.read_bytes()
        if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Corrupt Picotron cache; repeat --setup --update')
        return dict(source=URL,version='0.3.0d',sha256=SHA256,bytes=len(data),cached=True)
    override=os.environ.get('PICOTRON_RUNTIME_JS')
    if override:data=Path(override).read_bytes()
    else:
        request=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0 (pico8mcp Picotron host)'})
        with urllib.request.urlopen(request,timeout=45) as response:data=response.read(8*1024*1024+1)
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Picotron player SHA-256 does not match the supported 0.3.0d runtime')
    cache.mkdir(parents=True,exist_ok=True)
    stage=cache/f'runtime.{os.getpid()}.part';stage.write_bytes(data);stage.replace(payload)
    info=dict(source=URL,version='0.3.0d',sha256=SHA256,bytes=len(data),cached=False,
              notice='Official Lexaloffle player; cached locally, not bundled in the repository.')
    manifest.write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
    return info
