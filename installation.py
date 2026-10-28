"""Install the common adapter files into the current user's data directory."""
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from pico8_paths import app_home

FILES=('pico8_mcp.py','play.py','adapter.py','cdp.py','runtime.py','pico8_paths.py',
       'installation.py','profiles.py','host.html','bridge.lua','readme.md','LICENSE')

def install_common(source=None):
    source=Path(source or __file__).resolve()
    if source.is_file():source=source.parent
    target=app_home();target.mkdir(parents=True,exist_ok=True)
    changed=[];hashes={}
    for name in FILES:
        content=(source/name).read_bytes();dest=target/name
        hashes[name]=hashlib.sha256(content).hexdigest()
        if dest.exists() and dest.read_bytes()==content:continue
        staging=dest.with_name(dest.name+f'.{os.getpid()}.tmp')
        try:staging.write_bytes(content);os.replace(staging,dest)
        finally:staging.unlink(missing_ok=True)
        changed.append(name)
    manifest={'installedAt':datetime.now(timezone.utc).isoformat(),'files':hashes}
    (target/'install.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {'home':str(target),'entrypoint':str(target/'pico8_mcp.py'),'player':str(target/'play.py'),
            'commonFiles':list(FILES),'updatedFiles':changed}
