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
import profiling
from bundles import bundle_specs
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

def prepare_cart(data: bytes, filename: str, instrument=True, count_draws=False,
                 profiling_enabled=False, profile_functions=None, profile_sections=False):
    detailed=bool(profiling_enabled or profile_functions or profile_sections)
    if detailed and not instrument:
        raise AdapterError('INVALID_ARGUMENT','Profiling requires instrument=true.')
    if filename.lower().endswith('.png'):
        if instrument:raise AdapterError('UNSUPPORTED_CART','Use a text .p8/.lua cart for bridge injection, or instrument=false for PNG carts.')
        return data,dict(instrumented=False,countDraws=False,profiling=False,profileScopes=[])
    try:text=data.decode('utf-8-sig').replace('\r\n','\n')
    except UnicodeDecodeError as exc:raise AdapterError('INVALID_CART','Text carts must be UTF-8.') from exc
    if filename.lower().endswith('.lua'):
        text='pico-8 cartridge // http://www.pico-8.com\nversion 41\n__lua__\n'+text
    match=re.search(r'(?m)^__lua__\s*\n',text)
    if not match:raise AdapterError('INVALID_CART','Missing __lua__ section in .p8 cart.')
    end=re.search(r'(?m)^__[a-z0-9_]+__\s*$',text[match.end():])
    body_end=match.end()+end.start() if end else len(text)
    lua=text[match.end():body_end].rstrip()
    profile_meta=dict(profiling=False,profileScopes=[])
    if instrument:
        if '-- PICO-8 MCP bridge v' in lua:raise AdapterError('ALREADY_INSTRUMENTED','Source already contains this bridge; load with instrument=false.')
        try:
            prefix,lua,suffix,profile_meta=profiling.instrument(lua,profiling_enabled,profile_functions,profile_sections)
        except ValueError as exc:
            raise AdapterError('INVALID_ARGUMENT',str(exc)) from exc
        prefix+='local __p8mcp_draws=0\n'
        if count_draws:
            for name in DRAW_APIS:
                prefix+=f'local __p8mcp_native_{name}={name}\nlocal function {name}(...) __p8mcp_draws+=1 return __p8mcp_native_{name}(...) end\n'
        lua=prefix+lua+'\n'+suffix+'\n'+(HERE/'bridge.lua').read_text()
    tail=text[body_end:] if end else '__gfx__\n'+'0'*128+'\n'
    text=text[:match.end()]+lua+'\n'+tail.rstrip()+'\n'
    return text.encode('utf-8'),dict(instrumented=instrument,countDraws=instrument and count_draws,**profile_meta)

class Adapter:
    def __init__(self,root,cache=None,profile=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.cache=Path(cache).resolve() if cache else app_home()/'runtime'
        self.profile_path=Path(profile).resolve() if profile else browser_profile()
        self.client=None;self.process=None;self.http=None;self.port=None;self.loaded=None;self.bundle=None
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
                                  draws=word(28) if g[68]>=2 else None,
                                  updateHz=g[94] if g[68]>=2 else None,
                                  drawCalls=word(26) if self.loaded and self.loaded['metadata']['countDraws'] else None,
                                  cpuSaturated=word(10)>=30000,errorCode=g[79])
        data['cart']=self.loaded['metadata'] if self.loaded else None
        data['bundle']=self.bundle
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
        self._wait_js('''__p8agent.loadGeneration>=__p8agent.expectedGeneration && codo_command===0 &&
          (pico8_state.system_messages||[]).some(x=>String(x).toLowerCase().startsWith("loaded "))''',timeout_ms,settle_frames=2)

    def load(self,cart=None,lua=None,instrument=True,count_draws=False,timeout_ms=15000,
             profiling=False,profile_functions=None,profile_sections=False):
        if (cart is None)==(lua is None):raise AdapterError('INVALID_ARGUMENT','Provide exactly one of cart or lua.')
        if cart:
            path=self.path(cart);data=path.read_bytes();name=path.name;origin=str(path)
            if not name.lower().endswith(('.p8','.lua','.p8.png')):raise AdapterError('INVALID_CART','Expected .p8, .lua or .p8.png.')
        else:data=lua.encode('utf-8');name='agent.lua';origin='inline Lua'
        if len(data)>1024*1024:raise AdapterError('CART_TOO_LARGE','Maximum cartridge input is 1 MiB.')
        payload,metadata=prepare_cart(data,name,instrument,count_draws,profiling,profile_functions,profile_sections)
        if name.endswith('.lua'):name=name[:-4]+'.p8'
        self._load_bytes(payload,name,timeout_ms)
        metadata.update(source=origin,name=name,sha256=hashlib.sha256(data).hexdigest(),loadedBytes=len(payload))
        self.loaded=dict(payload=payload,name=name,metadata=metadata)
        self.bundle=None
        return dict(loaded=True,**metadata)

    def _plan_bundle(self,entry,carts=None,entry_name=None,instrument=False,count_draws=False,
                     profiling=False,profile_functions=None,profile_sections=False):
        try:specs=bundle_specs(entry,carts,entry_name)
        except ValueError as exc:raise AdapterError('INVALID_ARGUMENT',str(exc)) from exc
        plan=[]
        for spec in specs:
            path=self.path(spec['cart']);data=path.read_bytes()
            if len(data)>1024*1024:raise AdapterError('CART_TOO_LARGE','Maximum cartridge input is 1 MiB.',{'source':str(path)})
            main=spec['entry']
            payload,meta=prepare_cart(data,path.name,instrument if main else False,
                 count_draws if main else False,profiling if main else False,
                 profile_functions if main else None,profile_sections if main else False)
            meta.update(source=str(path),name=spec['name'],sha256=hashlib.sha256(data).hexdigest(),loadedBytes=len(payload))
            drop_name=spec['name']+'.png' if path.name.lower().endswith('.p8.png') else spec['name']
            meta['inputName']=drop_name
            plan.append(dict(payload=payload,name=spec['name'],dropName=drop_name,metadata=meta,entry=main))
        return plan

    def _save_staged_cart(self,name,timeout_ms):
        # A native SAVE both writes the VM filesystem and emits a download.
        # The exact-name download acknowledges completion; no fixed sleep.
        start=self._eval('__p8agent.downloads.length')
        command=[ord(c) for c in 'save '+name]+[13]
        self._eval('__p8agent.stageSave={confirmed:false};codo_key_buffer.push(...'+json.dumps(command)+');true')
        # Replacing an existing name in this owned VM is part of staging.
        # Confirm only the native SAVE overwrite prompt for this request.
        predicate='''(()=>{
          if(!__p8agent.stageSave.confirmed && (pico8_state.system_messages||[]).some(x=>String(x).toLowerCase().includes("overwrite?"))){
            __p8agent.stageSave.confirmed=true;codo_key_buffer.push(121,13);
          }
          return codo_key_buffer.length===0 && __p8agent.downloads.slice(START).some(d=>d.name.toLowerCase()===NAME);
        })()'''
        self._wait_js(predicate.replace('START',str(start)).replace('NAME',json.dumps(name)),timeout_ms)
        return self._eval('''(()=>{const i=__p8agent.downloads.findIndex((d,i)=>i>=START&&d.name.toLowerCase()===NAME);
          return i>=0?__p8agent.downloads.splice(i,1)[0].contents:null;})()'''.replace('START',str(start)).replace('NAME',json.dumps(name)))

    def _stop_for_staging(self,timeout_ms):
        # Native frame_number advances during a game, including a paused Lua
        # bridge, but stops in the console/editor. Escape must not be sent
        # blindly: in an idle console it switches to the code editor.
        running=self._eval('''new Promise(resolve=>{
          const first=pico8_state.frame_number||0,start=performance.now();
          function tick(){
            if((pico8_state.frame_number||0)!==first){resolve(true);return;}
            if(performance.now()-start>=180){resolve(false);return;}
            requestAnimationFrame(tick);
          }requestAnimationFrame(tick);
        })''',min(timeout_ms/1000+1,5))
        self._eval('codo_key_buffer.length=0;true')
        if running:
            self.client.call('Input.dispatchKeyEvent',dict(type='keyDown',key='Escape',code='Escape',windowsVirtualKeyCode=27))
            try:self._wait_js('true',timeout_ms,settle_frames=6)
            finally:self.client.call('Input.dispatchKeyEvent',dict(type='keyUp',key='Escape',code='Escape',windowsVirtualKeyCode=27))

    def load_bundle(self,entry,carts=None,entry_name=None,instrument=False,count_draws=False,
                    profiling=False,profile_functions=None,profile_sections=False,timeout_ms=60000):
        if type(timeout_ms) is not int or not 100<=timeout_ms<=60000:
            raise AdapterError('INVALID_ARGUMENT','timeout_ms must be 100..60000 for bundle staging.')
        # Validate every path, alias and source before replacing any VM cart.
        plan=self._plan_bundle(entry,carts,entry_name,instrument,count_draws,profiling,profile_functions,profile_sections)
        if not self.client:raise AdapterError('NOT_STARTED','Call pico8_start before loading a bundle.')
        deadline=time.monotonic()+timeout_ms/1000;staged=[];current=None
        self.bundle=None;self.loaded=None
        def remaining():
            value=int((deadline-time.monotonic())*1000)
            if value<=0:raise AdapterError('BUNDLE_TIMEOUT','Bundle staging exceeded timeout_ms.')
            return value
        try:
            self._stop_for_staging(remaining())
            for current in plan:
                self._load_bytes(current['payload'],current['dropName'],remaining())
                saved=self._save_staged_cart(current['name'],remaining())
                if current['dropName'].lower().endswith('.png'):
                    # Re-drop the VM's own text serialization under the .p8
                    # alias, registering PNG dependencies consistently with
                    # text carts for native LOAD and the cache bootstrap.
                    if not isinstance(saved,str):raise AdapterError('BUNDLE_FORMAT_ERROR','Native SAVE did not return text-cart data.')
                    if saved.startswith('pico-8 cartridge'):canonical=saved.encode('utf-8')
                    else:
                        try:canonical=base64.b64decode(saved.split(',',1)[1] if saved.startswith('data:') else saved,validate=True)
                        except ValueError as exc:raise AdapterError('BUNDLE_FORMAT_ERROR','Native text-cart serialization was not valid base64.') from exc
                    canonical=canonical.removeprefix(b'\xef\xbb\xbf')
                    if len(canonical)>1024*1024 or not canonical.startswith(b'pico-8 cartridge'):
                        raise AdapterError('BUNDLE_FORMAT_ERROR','Native SAVE did not produce a supported .p8 text cart.')
                    # SAVE omits empty asset sections. The Education file-drop
                    # parser still needs a footer or it clips the final token.
                    canonical,_=prepare_cart(canonical,current['name'],False)
                    current['payload']=canonical;current['dropName']=current['name']
                    current['metadata']['loadedBytes']=len(canonical)
                    self._load_bytes(canonical,current['name'],remaining())
                    self._save_staged_cart(current['name'],remaining())
                staged.append(dict(**current['metadata'],entry=current['entry']))
        except Exception as exc:
            self.loaded=None;self.bundle=None
            details=dict(failedCart=current['name'] if current else None,staged=staged,
                         cause=getattr(exc,'code',type(exc).__name__),message=str(exc))
            raise AdapterError('BUNDLE_STAGE_FAILED','Could not stage the complete bundle; partial native saves remain in this owned VM.',details) from exc
        main=plan[-1]
        self.loaded=dict(payload=main['payload'],name=main['dropName'],metadata=main['metadata'])
        self.bundle=dict(entry=main['name'],carts=staged,stagedCount=len(staged),
                         method='native file drop and SAVE',
                         note='Entry selected, not run. Files are in this profile\'s native VM filesystem. Call run next; subsequent LOAD/RELOAD remain native.')
        return dict(loaded=True,**self.bundle)

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

    def pack_source(self,cart,out=None):
        from source_build import build_cart
        source=self.path(cart)
        if source.suffix.lower()!='.lua':raise AdapterError('INVALID_ARGUMENT','Source must be a .lua file.')
        if source.stat().st_size>1024*1024:raise AdapterError('CART_TOO_LARGE','Maximum Lua source input is 1 MiB.')
        target=self.path(out or str(source.relative_to(self.root).parent/'build'/(source.stem+'.p8')),output=True)
        if target.suffix.lower()!='.p8':raise AdapterError('INVALID_ARGUMENT','Source output must end with .p8.')
        try:return dict(built=True,**build_cart(source,target,self.root))
        except (ValueError,IndexError,KeyError,TypeError) as exc:raise AdapterError('INVALID_SOURCE',str(exc)) from exc

    def cart_size(self,cart):
        from artifacts import cart_sizes
        path=self.path(cart)
        try:return dict(path=str(path),**cart_sizes(path))
        except (ValueError,IndexError,KeyError) as exc:raise AdapterError('INVALID_CART',str(exc)) from exc

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
        try:self.wait_frames(frames=label_frames,timeout_ms=timeout_ms)
        except AdapterError as exc:raise AdapterError('BUILD_FAILED','Waiting for label frames failed.',{'cause':str(exc)}) from exc
        self._eval('canvas.focus();__p8agent.downloads=[];true')
        def key(kind,value,code,vk,modifiers=0):
            # windowsVirtualKeyCode is portable; nativeVirtualKeyCode is not.
            self.client.call('Input.dispatchKeyEvent',dict(type=kind,key=value,code=code,
                             windowsVirtualKeyCode=vk,modifiers=modifiers))
        # Education's web VM uses Ctrl+7, including when hosted on macOS.
        modifier,value,code,vk=2,'Control','ControlLeft',17
        key('keyDown',value,code,vk,modifier)
        try:
            key('keyDown','7','Digit7',55,modifier)
            self._wait_js('true',timeout_ms,settle_frames=6)
            key('keyUp','7','Digit7',55,modifier)
        finally:key('keyUp',value,code,vk)
        self._wait_js('true',timeout_ms,settle_frames=4)
        key('keyDown','Escape','Escape',27)
        self._wait_js('true',timeout_ms,settle_frames=4)
        key('keyUp','Escape','Escape',27)
        # Native frame_number stops in the console; use browser render ticks.
        self._wait_js('true',timeout_ms,settle_frames=4)
        try:encoded=self._save_staged_cart('pico8mcp-build.p8.png',timeout_ms)
        except AdapterError as exc:raise AdapterError('BUILD_FAILED','Native console did not export the PNG.',{'cause':str(exc),'messages':self.state().get('vm',{}).get('system_messages',[])}) from exc
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

    def _profile_reset(self):
        if self.loaded and self.loaded['metadata'].get('profiling'):
            self._command(8)

    def _profile_report(self):
        if not self.loaded or not self.loaded['metadata'].get('profiling'):return None
        self._eval('__p8agent.logs=__p8agent.logs.filter(x=>!x.text.includes("@@p8mcp_prof:"));true')
        self._command(7)
        logs=self._eval('__p8agent.logs')
        try:return profiling.decode_report(logs,self.sequence,self.loaded['metadata']['profileScopes'])
        except (ValueError,KeyError,TypeError) as exc:
            raise AdapterError('PROFILE_REPORT_ERROR',str(exc),{'logs':logs[-25:]}) from exc

    def _profile_abort(self):
        try:self.pause()
        except AdapterError:pass
        if self.loaded and self.loaded['metadata'].get('profiling'):
            try:self._command(9)
            except AdapterError:pass

    def _profile_save(self,report,target):
        if target:
            report['path']=str(target)
            target.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        return report

    def profile(self,frames=60,buttons=0,sample_every=1,timeout_ms=45000,out=None):
        if type(frames) is not int or type(sample_every) is not int or not 1<=frames<=600 or not 1<=sample_every<=60:
            raise AdapterError('INVALID_ARGUMENT','frames must be 1..600 and sample_every 1..60.')
        target=self.path(out,output=True) if out else None
        start=time.monotonic();self.pause();rows=[];left=frames
        self._profile_reset()
        try:
            while left:
                n=min(left,sample_every)
                state=self.step(n,buttons,min(20000,timeout_ms))['state'];b=state['bridge']
                rows.append({k:b[k] for k in ['updates','cpu','cpuUser','fps','drawCalls']});left-=n
                if time.monotonic()-start>timeout_ms/1000:raise AdapterError('PROFILE_TIMEOUT','Profile exceeded its bounded timeout.',{'samples':rows})
        except Exception:
            self._profile_abort()
            raise
        import statistics
        summary={k:dict(mean=statistics.mean(r[k] for r in rows),min=min(r[k] for r in rows),max=max(r[k] for r in rows)) for k in ['cpu','cpuUser']}
        summary['drawCalls']=(dict(mean=statistics.mean(r['drawCalls'] for r in rows),min=min(r['drawCalls'] for r in rows),max=max(r['drawCalls'] for r in rows)) if self.loaded['metadata']['countDraws'] else None)
        return self._profile_save(dict(mode='stepped',frames=frames,sampleEvery=sample_every,samples=rows,summary=summary,
                    detail=self._profile_report(),
                    drawCounting=self.loaded['metadata']['countDraws'],
                    note='CPU fractions include development instrumentation; 1.0 means one frame budget, not 100% of a normalized breakdown. '
                         'cpuUser brackets user update/draw callbacks. Draw counts are API calls. '
                         'Stepping changes update/draw scheduling; use profile_realtime to measure playback rates. '
                         'Simulation is paused after capture.'),target)

    def profile_realtime(self,seconds=5,sample_ms=100,buttons=0,out=None):
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 1<=seconds<=30:
            raise AdapterError('INVALID_ARGUMENT','seconds must be 1..30.')
        if type(sample_ms) is not int or not 20<=sample_ms<=1000:
            raise AdapterError('INVALID_ARGUMENT','sample_ms must be 20..1000.')
        if type(buttons) is not int or not 0<=buttons<=63:
            raise AdapterError('INVALID_ARGUMENT','buttons must be 0..63.')
        target=self.path(out,output=True) if out else None
        state=self.pause()
        if state['bridge'].get('version',0)<2:
            raise AdapterError('BRIDGE_REQUIRED','Reload the cart with the current bridge for real-time profiling.')
        self.buttons(buttons);self._profile_reset()
        # The sampler is passive: it never requests simulation steps. It starts
        # immediately before resume, including the initial scheduling delay.
        self._eval('''(()=>{
          const start=performance.now(),samples=[],capture={stop:false};
          const word=o=>(pico8_gpio[64+o]||0)+256*(pico8_gpio[65+o]||0);
          function sample(){return {ms:performance.now()-start,updates:word(8),draws:word(28),
            cpu:word(10)/10000,cpuUser:word(13)/10000,vmFps:pico8_gpio[76]};}
          samples.push(sample());
          capture.promise=new Promise(resolve=>{
            function tick(){
              samples.push(sample());
              const errors=(pico8_state.system_messages||[]).filter(x=>/syntax error|runtime error|assertion failed|out of memory|attempt to /i.test(String(x))).map(String);
              if(capture.stop||errors.length||performance.now()-start>=DURATION){resolve({samples,errors});return;}
              setTimeout(tick,INTERVAL);
            }
            setTimeout(tick,INTERVAL);
          });
          __p8agent.profileCapture=capture;return true;
        })()'''.replace('DURATION',str(seconds*1000)).replace('INTERVAL',str(sample_ms)))
        try:
            self.resume()
            capture=self._eval('__p8agent.profileCapture.promise',seconds+10)
            if capture['errors']:
                raise AdapterError('PROFILE_CART_ERROR','Cart failed during capture.',{'errors':capture['errors']})
            self.pause()
        except Exception:
            try:
                self._eval('__p8agent.profileCapture.stop=true;true')
                self.pause()
            except Exception:pass
            self._profile_abort()
            raise
        samples=capture['samples']
        result=dict(mode='realtime',requestedSeconds=seconds,sampleMs=sample_ms,
                    rates=profiling.rates(samples),samples=samples,detail=self._profile_report(),
                    note='Simulation runs freely during capture and is paused afterward. '
                         'Scope timings require profiling=true, profile_functions or profile_sections at load. '
                         'Instrumentation adds CPU cost. CPU detail includes resume/pause command latency; '
                         'wall-rate samples cover the requested browser-time window.')
        return self._profile_save(result,target)

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
        self.bundle=None
        return dict(closed=True,profileCleanup=cleanup,profile=str(self.profile_path))
