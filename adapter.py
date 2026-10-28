"""Standalone local browser adapter. Python 3.10+, standard library only."""
from __future__ import annotations
import base64
from collections import deque
import hashlib
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from cdp import CDPError, connect, list_targets
import runtime
from pico8_paths import app_home,browser_profile
from installation import install_common
from profiles import ProfileLease,ProfileBusy

HERE=Path(__file__).resolve().parent
BRIDGE_BASE=64
MAGIC=[80,56,65,49]
DRAW_APIS=('cls','pset','line','rect','rectfill','circ','circfill','oval','ovalfill',
           'rrect','rrectfill','spr','sspr','map','tline','print')
ERROR_RE=re.compile(r'syntax error|runtime error|assertion failed|out of memory|attempt to ',re.I)

class AdapterError(RuntimeError):
    def __init__(self,code,message,details=None):
        super().__init__(message);self.code=code;self.details=details or {}

def browser_path(preference='auto'):
    explicit=os.environ.get('PICO8_BROWSER')
    if explicit and Path(explicit).is_file():return explicit
    names={'edge':['msedge','microsoft-edge','microsoft-edge-stable'],
           'chrome':['google-chrome','google-chrome-stable'],
           'chromium':['chromium','chromium-browser']}
    choices=names.keys() if preference=='auto' else [preference]
    for choice in choices:
        for name in names[choice]:
            found=shutil.which(name)
            if found:return found
    candidates=[]
    if sys.platform=='win32':
        for base in [os.environ.get('PROGRAMFILES'),os.environ.get('PROGRAMFILES(X86)'),os.environ.get('LOCALAPPDATA')]:
            if not base:continue
            if preference in ('auto','edge'):candidates.append(Path(base)/'Microsoft/Edge/Application/msedge.exe')
            if preference in ('auto','chrome'):candidates.append(Path(base)/'Google/Chrome/Application/chrome.exe')
    elif sys.platform=='darwin':
        for base in [Path('/Applications'),Path.home()/'Applications']:
            if preference in ('auto','chrome'):candidates.append(base/'Google Chrome.app/Contents/MacOS/Google Chrome')
            if preference in ('auto','edge'):candidates.append(base/'Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
            if preference in ('auto','chromium'):candidates.append(base/'Chromium.app/Contents/MacOS/Chromium')
    return next((str(p) for p in candidates if p.is_file()),None)

def prepare_cart(data: bytes, filename: str, instrument=True, count_draws=False):
    if filename.lower().endswith('.png'):
        if instrument:raise AdapterError('UNSUPPORTED_CART','Use a text .p8/.lua cart for bridge injection, or instrument=false for PNG carts.')
        return data,dict(instrumented=False,countDraws=False)
    try:text=data.decode('utf-8-sig').replace('\r\n','\n')
    except UnicodeDecodeError as exc:raise AdapterError('INVALID_CART','Text carts must be UTF-8.') from exc
    if filename.lower().endswith('.lua'):
        text='pico-8 cartridge // http://www.pico-8.com\nversion 41\n__lua__\n'+text
    match=re.search(r'(?m)^__lua__\s*\n',text)
    if not match:raise AdapterError('INVALID_CART','Missing __lua__ section in .p8 cart.')
    end=re.search(r'(?m)^__[a-z0-9_]+__\s*$',text[match.end():])
    body_end=match.end()+end.start() if end else len(text)
    lua=text[match.end():body_end].rstrip()
    if instrument:
        if '-- PICO-8 MCP bridge v1.' in lua:raise AdapterError('ALREADY_INSTRUMENTED','Source already contains this bridge; load with instrument=false.')
        prefix='local __p8mcp_draws=0\n'
        if count_draws:
            for name in DRAW_APIS:
                prefix+=f'local __p8mcp_native_{name}={name}\nlocal function {name}(...) __p8mcp_draws+=1 return __p8mcp_native_{name}(...) end\n'
        lua=prefix+lua+'\n'+(HERE/'bridge.lua').read_text()
    tail=text[body_end:] if end else '__gfx__\n'+'0'*128+'\n'
    text=text[:match.end()]+lua+'\n'+tail.rstrip()+'\n'
    return text.encode('utf-8'),dict(instrumented=instrument,countDraws=instrument and count_draws)

class Adapter:
    def __init__(self,root,cache=None,profile=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.cache=Path(cache).resolve() if cache else app_home()/'runtime'
        self.profile_path=Path(profile).resolve() if profile else browser_profile()
        self.client=None;self.process=None;self.http=None;self.port=None;self.loaded=None
        self.sequence=0;self.profile_dir=None;self.headless=None;self.profile_lease=None
        self.browser_logs=deque(maxlen=40)

    def path(self,value,output=False):
        candidate=(self.root/value).resolve()
        if not candidate.is_relative_to(self.root):raise AdapterError('PATH_OUTSIDE_ROOT','Path must stay within the configured --root.',{'path':str(candidate)})
        if output:candidate.parent.mkdir(parents=True,exist_ok=True)
        elif not candidate.is_file():raise AdapterError('FILE_NOT_FOUND','File does not exist.',{'path':str(candidate)})
        return candidate

    def setup(self,update=False):
        try:
            common=install_common(HERE)
            # Reuse the verified official download from the old source-local
            # cache when moving to the shared default runtime directory.
            legacy=HERE/'.runtime'
            if self.cache==app_home()/'runtime' and not (self.cache/'manifest.json').exists() and (legacy/'manifest.json').exists():
                manifest=json.loads((legacy/'manifest.json').read_text())
                data=(legacy/'runtime.js').read_bytes()
                if hashlib.sha256(data).hexdigest()==manifest.get('sha256'):
                    self.cache.mkdir(parents=True,exist_ok=True)
                    (self.cache/'runtime.js').write_bytes(data)
                    (self.cache/'manifest.json').write_text(json.dumps(manifest,indent=2))
            return dict(runtime.setup(self.cache,update),installation=common,profile=str(self.profile_path))
        except Exception as exc:raise AdapterError('SETUP_FAILED',str(exc)) from exc

    def doctor(self):
        installed=False;integrity=False;manifest=None
        if (self.cache/'manifest.json').exists():
            try:
                manifest=json.loads((self.cache/'manifest.json').read_text())
                installed=(self.cache/'runtime.js').is_file()
                integrity=installed and hashlib.sha256((self.cache/'runtime.js').read_bytes()).hexdigest()==manifest['sha256']
            except (ValueError,OSError,KeyError):pass
        return dict(python=sys.version.split()[0],platform=sys.platform,root=str(self.root),cache=str(self.cache),
                    browser=browser_path(),runtimeInstalled=installed,runtimeIntegrity=integrity,
                    runtime=manifest,running=self.client is not None,adminRequired=False,
                    home=str(app_home()),entrypoint=str(app_home()/'pico8_mcp.py'),
                    profile=str(self.profile_path),profilePolicy='persistent-single-owner')

    def _serve(self):
        adapter=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path=urlsplit(self.path).path
                if path in ('/','/index.html'):file=HERE/'host.html';kind='text/html; charset=utf-8'
                elif path=='/runtime.js':file=adapter.cache/'runtime.js';kind='application/javascript'
                elif path=='/favicon.ico':self.send_response(204);self.end_headers();return
                else:self.send_error(404);return
                data=file.read_bytes();self.send_response(200)
                self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        return f'http://127.0.0.1:{self.http.server_port}/'

    def start(self,headless=True,browser='auto',timeout_ms=45000):
        if self.client:
            if headless!=self.headless:raise AdapterError('MODE_CONFLICT','Close the current browser before changing headless mode.')
            return dict(reused=True,**self.state())
        lease=ProfileLease(self.profile_path)
        try:lease.acquire()
        except ProfileBusy as exc:raise AdapterError('PROFILE_IN_USE',str(exc),{'profile':str(self.profile_path)}) from exc
        self.profile_lease=lease;self.profile_dir=self.profile_path
        try:
            self.setup()
            executable=browser_path(browser)
            if not executable:raise AdapterError('BROWSER_NOT_FOUND','Install Chrome/Edge/Chromium separately, or set PICO8_BROWSER. Setup does not install a browser.')
            self.headless=headless
            url=self._serve()
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
            flags=[executable,f'--remote-debugging-port={self.port}',f'--user-data-dir={self.profile_dir}',
                   '--no-first-run','--no-default-browser-check','--disable-extensions','--disable-sync',
                   '--disable-background-networking','--disable-component-update',
                   '--disk-cache-size=16777216','--media-cache-size=1048576',
                   '--disable-background-timer-throttling','--disable-renderer-backgrounding',
                   '--disable-backgrounding-occluded-windows','--autoplay-policy=no-user-gesture-required',
                   '--window-size=900,750']
            if headless:flags.append('--headless=new')
            flags.append(url)
            creation=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' and headless else 0
            self.process=subprocess.Popen(flags,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE,creationflags=creation)
            lease.record(status='running',browserPid=self.process.pid,debugPort=self.port,browser=executable)
            stream=self.process.stderr
            self.browser_logs.clear()
            def drain():
                for line in stream:self.browser_logs.append(line.strip()[-1000:])
            threading.Thread(target=drain,daemon=True).start()
            deadline=time.monotonic()+timeout_ms/1000
            last=None
            while time.monotonic()<deadline:
                try:
                    targets=list_targets(port=self.port,timeout=1)
                    target=next((t for t in targets if t.get('type')=='page' and t.get('url','').startswith(url)),None)
                    if target:self.client=connect(target);break
                except (OSError,ValueError,CDPError) as exc:last=str(exc)
                if self.process.poll() not in (None,0):raise AdapterError('BROWSER_EXITED','Browser exited during launch.',{'exitCode':self.process.returncode,'stderr':list(self.browser_logs)})
                time.sleep(.05)
            if not self.client:raise AdapterError('START_TIMEOUT','No local browser page became available.',{'lastError':last,'stderr':list(self.browser_logs)})
            self.client.call('Page.enable');self.client.call('Runtime.enable')
            while time.monotonic()<deadline:
                try:
                    if self.client.evaluate("!!(window.__p8agent && window.Module && Module.calledRun && typeof Module.pico8Reset==='function')"):
                        self._wait_js('true',500,settle_frames=3)
                        return dict(reused=False,port=self.port,headless=headless,**self.state())
                except CDPError as exc:last=str(exc)
                time.sleep(.05)
            raise AdapterError('START_TIMEOUT','Education VM did not finish booting.',{'lastError':last,'stderr':list(self.browser_logs)})
        except Exception:self.close();raise

    def _eval(self,js,timeout=15):
        if not self.client:raise AdapterError('NOT_STARTED','Call pico8_start first.')
        try:return self.client.evaluate(js,await_promise=True,timeout=timeout)
        except (CDPError,OSError,TimeoutError) as exc:raise AdapterError('BROWSER_ERROR',str(exc)) from exc

    def _wait_js(self,predicate,timeout_ms=10000,settle_frames=0,check_errors=True):
        js='''new Promise((resolve,reject)=>{
          const start=performance.now();let frames=0;
          function tick(){
            const errors=(pico8_state.system_messages||[]).filter(x=>/syntax error|runtime error|assertion failed|out of memory|attempt to /i.test(String(x)));
            if(CHECK && errors.length){reject(Error(errors.join(' | ')));return;}
            if((PREDICATE)&&frames++>=SETTLE){resolve(true);return;}
            if(performance.now()-start>TIMEOUT){reject(Error('Timed out waiting for VM condition'));return;}
            setTimeout(tick,5);
          }tick();})'''
        return self._eval(js.replace('CHECK',str(check_errors).lower()).replace('PREDICATE',predicate)
                          .replace('SETTLE',str(settle_frames)).replace('TIMEOUT',str(timeout_ms)),timeout_ms/1000+3)

    def state(self):
        if not self.client:return dict(started=False,root=str(self.root),profile=str(self.profile_path))
        data=self._eval('''({started:true,url:location.href,vm:{...pico8_state,system_messages:(pico8_state.system_messages||[]).slice(-40).map(x=>String(x).slice(0,4000))},gpio:pico8_gpio.slice(),
          canvas:{width:canvas.width,height:canvas.height},logs:__p8agent.logs.slice(-20)})''')
        g=data['gpio'];ready=g[64:68]==MAGIC
        word=lambda o:(g[64+o] or 0)+256*(g[65+o] or 0)
        data['bridge']=dict(ready=ready,version=g[68] if ready else None)
        if ready:
            data['bridge'].update(paused=g[70]==1,accepted=g[71],completed=g[88],
                                  updates=word(8),stepsRemaining=word(22),cpu=word(10)/10000,
                                  cpuUser=word(13)/10000,fps=g[76],
                                  drawCalls=word(26) if self.loaded and self.loaded['metadata']['countDraws'] else None,
                                  cpuSaturated=word(10)>=30000,errorCode=g[79])
        data['cart']=self.loaded['metadata'] if self.loaded else None
        data['errors']=[m for m in data['vm'].get('system_messages',[]) if ERROR_RE.search(str(m))]
        data['profile']=str(self.profile_path)
        return data

    def _load_bytes(self,payload,name,timeout_ms):
        encoded=base64.b64encode(payload).decode('ascii')
        self._eval('''(()=>{
          pico8_buttons.fill(0);pico8_gpio.fill(0);pico8_state.system_messages=[];__p8agent.logs=[];
          const data=Uint8Array.from(atob(DATA),c=>c.charCodeAt(0));
          const file=new File([data],NAME,{type:'application/octet-stream'}),generation=__p8agent.loadGeneration;
          window.__p8agent.expectedGeneration=generation+1;
          p8_drop_file({dataTransfer:{files:[file]},preventDefault(){},stopPropagation(){}});
          return true;})()'''.replace('DATA',json.dumps(encoded)).replace('NAME',json.dumps(name)))
        self._wait_js('__p8agent.loadGeneration>=__p8agent.expectedGeneration && codo_command===0',timeout_ms,settle_frames=2)

    def load(self,cart=None,lua=None,instrument=True,count_draws=False,timeout_ms=15000):
        if (cart is None)==(lua is None):raise AdapterError('INVALID_ARGUMENT','Provide exactly one of cart or lua.')
        if cart:
            path=self.path(cart);data=path.read_bytes();name=path.name;origin=str(path)
            if not name.lower().endswith(('.p8','.lua','.p8.png')):raise AdapterError('INVALID_CART','Expected .p8, .lua or .p8.png.')
        else:data=lua.encode('utf-8');name='agent.lua';origin='inline Lua'
        if len(data)>1024*1024:raise AdapterError('CART_TOO_LARGE','Maximum cartridge input is 1 MiB.')
        payload,metadata=prepare_cart(data,name,instrument,count_draws)
        if name.endswith('.lua'):name=name[:-4]+'.p8'
        self._load_bytes(payload,name,timeout_ms)
        metadata.update(source=origin,name=name,sha256=hashlib.sha256(data).hexdigest(),loadedBytes=len(payload))
        self.loaded=dict(payload=payload,name=name,metadata=metadata)
        return dict(loaded=True,**metadata)

    def run(self,paused=False,timeout_ms=15000):
        if not self.loaded:raise AdapterError('NO_CART','Load a cart before running.')
        self._load_bytes(self.loaded['payload'],self.loaded['name'],timeout_ms)
        self.sequence=0
        self._eval('codo_key_buffer.push(114,117,110,13);true')
        if self.loaded['metadata']['instrumented']:
            self._wait_js('pico8_gpio[64]===80 && pico8_gpio[65]===56 && pico8_gpio[66]===65 && pico8_gpio[67]===49',timeout_ms)
            if paused:self.pause()
        else:
            self._wait_js('codo_key_buffer.length===0 && (pico8_state.frame_number||0)>0',timeout_ms,settle_frames=6)
        return self.state()

    def build(self,cart,out=None,label_frames=10,timeout_ms=30000):
        """Build an uninstrumented PNG cartridge with a native captured label."""
        source=self.path(cart)
        if source.suffix.lower() not in ('.lua','.p8'):
            raise AdapterError('INVALID_CART','Build expects a .lua or text .p8 source.')
        if type(label_frames) is not int or not 1<=label_frames<=600:
            raise AdapterError('INVALID_ARGUMENT','label_frames must be 1..600.')
        name=out or str(source.relative_to(self.root).parent/'debug'/(source.stem+'.p8.png'))
        if not str(name).lower().endswith('.p8.png'):
            raise AdapterError('INVALID_ARGUMENT','Build output must end with .p8.png.')
        target=self.path(name,output=True)
        self.start(headless=True,timeout_ms=timeout_ms)
        self.load(cart=cart,instrument=False,timeout_ms=timeout_ms)
        self.run(timeout_ms=timeout_ms)
        self.wait_frames(frames=label_frames,timeout_ms=timeout_ms)
        self._eval('canvas.focus();__p8agent.downloads=[];true')
        def key(kind,value,code,vk,modifiers=0):
            self.client.call('Input.dispatchKeyEvent',dict(type=kind,key=value,code=code,
                             windowsVirtualKeyCode=vk,nativeVirtualKeyCode=vk,modifiers=modifiers))
        # Capture the framebuffer through PICO-8's Ctrl+7 label shortcut.
        key('keyDown','Control','ControlLeft',17,2)
        try:
            key('keyDown','7','Digit7',55,2)
            self._wait_js('true',timeout_ms,settle_frames=6)
            key('keyUp','7','Digit7',55,2)
        finally:key('keyUp','Control','ControlLeft',17)
        self._wait_js('true',timeout_ms,settle_frames=4)
        key('keyDown','Escape','Escape',27)
        key('keyUp','Escape','Escape',27)
        # Native frame_number stops in the console; use browser render ticks.
        self._wait_js('true',timeout_ms,settle_frames=4)
        command=[ord(c) for c in 'save pico8mcp-build.p8.png']+[13]
        self._eval('codo_key_buffer.push(...'+json.dumps(command)+');true')
        self._wait_js('__p8agent.downloads.some(d=>d.name.toLowerCase()==="pico8mcp-build.p8.png")',timeout_ms)
        download=self._eval('__p8agent.downloads.find(d=>d.name.toLowerCase()==="pico8mcp-build.p8.png")')
        encoded=download['contents']
        if not isinstance(encoded,str):
            raise AdapterError('BUILD_FAILED','Native PNG download has an unsupported encoding.')
        if encoded.startswith('data:'):encoded=encoded.split(',',1)[1]
        try:data=base64.b64decode(encoded,validate=True)
        except ValueError as exc:raise AdapterError('BUILD_FAILED','Native PNG download is not valid base64.') from exc
        if data[:8]!=b'\x89PNG\r\n\x1a\n' or len(data)<33:
            raise AdapterError('BUILD_FAILED','The VM did not export a PNG cartridge.')
        width,height=struct.unpack('>II',data[16:24])
        if (width,height)!=(160,205):
            raise AdapterError('BUILD_FAILED','Unexpected native cartridge dimensions.',dict(width=width,height=height))
        target.write_bytes(data)
        # Prove the actual encoded PNG loads and executes, rather than returning
        # an ordinary screenshot renamed as a cartridge.
        self.load(cart=str(target.relative_to(self.root)),instrument=False,timeout_ms=timeout_ms)
        self.run(timeout_ms=timeout_ms)
        return dict(built=True,path=str(target),bytes=len(data),width=width,height=height,
                    labelCaptured=True,labelFrames=label_frames,verified=True,
                    instrumented=False,sha256=hashlib.sha256(data).hexdigest())

    def _command(self,opcode,params=None,timeout_ms=10000,complete=True):
        if not self.state().get('bridge',{}).get('ready'):raise AdapterError('BRIDGE_REQUIRED','Load with instrument=true and run the cart for this operation.')
        self.sequence=self.sequence%255+1;seq=self.sequence
        writes={16:opcode,15:0,**(params or {}),17:seq}
        # Sequence is written last; the VM sees one complete request.
        self._eval('(()=>{const writes=WRITES;for(const [k,v] of writes)pico8_gpio[64+k]=v;return true;})()'
                   .replace('WRITES',json.dumps(list(writes.items()))))
        offset=24 if complete else 7
        self._wait_js(f'pico8_gpio[{64+offset}]==={seq}',timeout_ms)
        state=self.state()
        if state['bridge']['errorCode']:
            raise AdapterError('CART_VALUE_ERROR','Cart rejected the variable operation.',{'errorCode':state['bridge']['errorCode'],'codes':{'1':'missing variable','2':'unsupported value type','3':'invalid table path'}})
        return state

    def pause(self):return self._command(1)
    def resume(self):return self._command(2)

    def buttons(self,mask=0,controlled=True):
        if type(mask) is not int or not 0<=mask<=63:raise AdapterError('INVALID_ARGUMENT','Button mask must be an integer from 0 to 63.')
        state=self.state()
        if state.get('bridge',{}).get('ready'):
            return self._command(4,{20:mask,21:int(controlled)})
        self._eval(f'pico8_buttons[0]={mask};true')
        return self.state()

    def step(self,frames=1,buttons=0,timeout_ms=20000):
        if type(frames) is not int or not 1<=frames<=600:raise AdapterError('INVALID_ARGUMENT','frames must be 1..600.')
        if type(buttons) is not int or not 0<=buttons<=63:raise AdapterError('INVALID_ARGUMENT','buttons must be 0..63.')
        self.pause();before=self.state()['bridge']['updates']
        state=self._command(3,{18:frames%256,19:frames//256,20:buttons,21:1},timeout_ms)
        actual=(state['bridge']['updates']-before)%32768
        if actual!=frames:raise AdapterError('STEP_MISMATCH','Unexpected simulation update count.',{'requested':frames,'actual':actual})
        return dict(requestedUpdates=frames,actualUpdates=actual,state=state)

    def wait_frames(self,frames=1,timeout_ms=10000):
        if type(frames) is not int or not 1<=frames<=600:raise AdapterError('INVALID_ARGUMENT','frames must be 1..600.')
        self._eval('window.__p8agent.waitFrame={last:pico8_state.frame_number||0,count:0};true')
        # A freshly run cart may reset its native frame counter after run()
        # returns. Count observed deltas across that reset, not one absolute
        # target inherited from the previous cartridge.
        self._wait_js('''(()=>{const w=__p8agent.waitFrame,n=pico8_state.frame_number||0;
          w.count+=n>=w.last?n-w.last:n;w.last=n;return w.count>=FRAMES;})()'''.replace('FRAMES',str(frames)),timeout_ms)
        return self.state()

    def variable(self,name,value=None,write=False,create=False):
        if not isinstance(name,str) or len(name)>23 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*',name):
            raise AdapterError('INVALID_ARGUMENT','Variable name must be an ASCII scalar/table path of at most 23 characters.')
        raw=name.encode('ascii')+b'\0'*(24-len(name));params={32+i:b for i,b in enumerate(raw)}
        params[31]=int(create)
        if write:
            if isinstance(value,bool):number=int(value);kind=1
            elif isinstance(value,(int,float)) and math.isfinite(value) and -32768<=value<32768:number=value;kind=0
            else:raise AdapterError('INVALID_ARGUMENT','Value must be boolean or a finite PICO-8 number in [-32768,32768).')
            fixed=math.floor(number*65536)&0xffffffff
            params.update({56+i:b for i,b in enumerate(struct.pack('<I',fixed))});params[60]=kind
        state=self._command(6 if write else 5,params)
        g=state['gpio'];kind=g[124]
        number=struct.unpack('<i',bytes(g[120:124]))[0]/65536
        return dict(name=name,value=None if kind==2 else bool(number) if kind==1 else number,type=['number','boolean','nil'][kind])

    def gpio(self,offset=0,count=64,values=None):
        if type(offset) is not int or type(count) is not int or offset<0 or count<0 or offset+count>128:raise AdapterError('INVALID_ARGUMENT','GPIO range must stay within 0..127.')
        if values is not None:
            if offset+len(values)>64:raise AdapterError('RESERVED_GPIO','Writes are limited to cart-owned GPIO 0..63; 64..127 is reserved for the bridge.')
            if any(type(v) is not int or not 0<=v<=255 for v in values):raise AdapterError('INVALID_ARGUMENT','GPIO values must be bytes.')
            self._eval(f'(()=>{{const a={json.dumps(values)};a.forEach((v,i)=>pico8_gpio[{offset}+i]=v);return true;}})()')
        return dict(offset=offset,values=self.state()['gpio'][offset:offset+count])

    def screenshot(self,out=None):
        png=self._eval("new Promise(resolve=>requestAnimationFrame(()=>resolve(canvas.toDataURL('image/png'))))")
        data=base64.b64decode(png.split(',',1)[1])
        if data[:8]!=b'\x89PNG\r\n\x1a\n':raise AdapterError('CAPTURE_FAILED','Canvas did not return a PNG.')
        width,height=struct.unpack('>II',data[16:24])
        if (width,height)!=(128,128):raise AdapterError('CAPTURE_FAILED','Expected native 128x128 framebuffer.',{'width':width,'height':height})
        name=out or 'debug/pico8/'+datetime.now(timezone.utc).strftime('frame-%Y%m%d-%H%M%S-%f.png')
        path=self.path(name,output=True);path.write_bytes(data)
        return dict(path=str(path),width=width,height=height,mimeType='image/png',data=base64.b64encode(data).decode())

    def profile(self,frames=60,buttons=0,sample_every=1,timeout_ms=45000):
        if not 1<=frames<=600 or not 1<=sample_every<=60:raise AdapterError('INVALID_ARGUMENT','frames must be 1..600 and sample_every 1..60.')
        start=time.monotonic();self.pause();rows=[];left=frames
        while left:
            n=min(left,sample_every)
            state=self.step(n,buttons,min(20000,timeout_ms))['state'];b=state['bridge']
            rows.append({k:b[k] for k in ['updates','cpu','cpuUser','fps','drawCalls']});left-=n
            if time.monotonic()-start>timeout_ms/1000:raise AdapterError('PROFILE_TIMEOUT','Profile exceeded its bounded timeout.',{'samples':rows})
        import statistics
        summary={k:dict(mean=statistics.mean(r[k] for r in rows),min=min(r[k] for r in rows),max=max(r[k] for r in rows)) for k in ['cpu','cpuUser']}
        summary['drawCalls']=(dict(mean=statistics.mean(r['drawCalls'] for r in rows),min=min(r['drawCalls'] for r in rows),max=max(r['drawCalls'] for r in rows)) if self.loaded['metadata']['countDraws'] else None)
        return dict(frames=frames,sampleEvery=sample_every,samples=rows,summary=summary,
                    drawCounting=self.loaded['metadata']['countDraws'],
                    note='CPU fractions include development instrumentation; cpuUser brackets user update/draw callbacks. Draw counts are API calls, not GPU submissions. Simulation is paused after capture.')

    def close(self):
        cleanup=None
        if self.client:
            try:self.client.call('Browser.close',timeout=5)
            except (OSError,CDPError,TimeoutError):pass
            self.client.close();self.client=None
        if self.process:
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Only terminate the process created by this Adapter instance.
                self.process.terminate()
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:pass
            self.process=None
        if self.http:self.http.shutdown();self.http.server_close();self.http=None
        if self.profile_lease:
            status='closed'
            if self.port:
                try:
                    with socket.create_connection(('127.0.0.1',self.port),timeout=.3):status='browser-still-running'
                except OSError:pass
            self.profile_lease.record(status=status)
            self.profile_lease.release();self.profile_lease=None
            cleanup='preserved'
        self.profile_dir=None
        self.loaded=None
        return dict(closed=True,profileCleanup=cleanup,profile=str(self.profile_path))
