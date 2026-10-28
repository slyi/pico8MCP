"""User-owned shared installation paths; no filesystem writes on import."""
import os,sys
from pathlib import Path

def app_home():
    override=os.environ.get('PICO8_MCP_HOME')
    if override:return Path(override).expanduser().resolve()
    if sys.platform=='win32':
        base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
    elif sys.platform=='darwin':base=Path.home()/'Library/Application Support'
    else:base=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    return (base/'pico8mcp').resolve()

def browser_profile():
    override=os.environ.get('PICO8_MCP_PROFILE_DIR')
    return Path(override).expanduser().resolve() if override else app_home()/'profiles/localhost'
