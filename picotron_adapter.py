"""Native Picotron browser backend; reuses the PICO-8 browser/CDP lifecycle."""
import base64,hashlib,json,math,re,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from adapter import Adapter,AdapterError
from pico8_paths import app_home
from source_build import expand_includes
from picotron_cart import pack_rom,unpack_rom
import picotron_runtime

HERE=Path(__file__).resolve().parent

class PicotronAdapter(Adapter):
    def __init__(self,root,cache=None,profile=None):
        super().__init__(root,cache or app_home()/'picotron-runtime',profile or app_home()/'profiles/picotron')
        self.home='pico8mcp-'+hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        self.base=f'/picotron_exp/{self.home}/appdata/'
        self.rom=None;self.generation=0;self.booted=False;self.url=None
    def setup(self,update=False):
        try:
            from installation import install_common
            installation=install_common(HERE)
            return dict(picotron_runtime.setup(self.cache,update),installation=installation,profile=str(self.profile_path))
        except Exception as exc:raise AdapterError('SETUP_FAILED',str(exc)) from exc
    def doctor(self):
        result=super().doctor();result['platform']='picotron';result['supportedVersion']='0.3.0d'
        result['runtimeIntegrity']=result.get('runtimeIntegrity',False) and result.get('runtime',{}).get('sha256')==picotron_runtime.SHA256
        return result
    def _page(self):
        host=(HERE/'host.html').read_text(encoding='utf-8')
        host=host.replace('PICO-8 MCP local host','Picotron MCP local host')
        host=host.replace('PICO-8 Education Edition · local agent host','Picotron 0.3.0d · local agent host')
        host=host.replace('width:512px;height:512px','width:960px;height:auto;max-width:100%')
        host=host.replace('width="128" height="128"','width="480" height="270"')
        host=host.replace('<canvas id="canvas"','<button id="sound" type="button">Enable sound</button>\n<canvas id="canvas"')
        host=host.replace('var pico8_gpio=','var picotron_buttons=Array.from({length:8},()=>new Array(16).fill(0));\nvar pico8_gpio=')
        fallback=pack_rom({'main.lua':b'function _draw() cls(0) end'})
        host=host.replace('var Module={','window.__picotron_generation='+str(self.generation)+';var p64cart_str='+json.dumps((self.rom or fallback).hex())+';var export_home_str='+json.dumps(self.home)+';\nvar Module={')
        host=host.replace('</script>\n<script src="/runtime.js">','''
window.__enableSound=async()=>{
 const audio=typeof node!=='undefined'?node:null;
 if(audio?.context?.state==='suspended')await audio.context.resume();
 if(pico8_state.sound_volume===0&&Module.pico8ToggleSound)Module.pico8ToggleSound();
 document.getElementById('sound').textContent='Sound enabled';
};
document.getElementById('sound').addEventListener('click',()=>__enableSound());
canvas.addEventListener('pointerdown',()=>__enableSound());
window.__pcm={buffers:0,squares:0,peak:0,count:0};
</script>
<script src="/runtime.js">''')
        return host.encode('utf-8')
    def _serve(self):
        adapter=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split('?')[0] in ('/','/index.html'):data=adapter._page();kind='text/html; charset=utf-8'
                elif self.path=='/runtime.js':data=(adapter.cache/'runtime.js').read_bytes();kind='application/javascript'
                elif self.path=='/favicon.ico':self.send_response(204);self.end_headers();return
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.http.server_port}/';return self.url
    def load(self,cart=None,lua=None,instrument=True,timeout_ms=45000):
        if (cart is None)==(lua is None):raise AdapterError('INVALID_ARGUMENT','Supply cart or lua, exactly one.')
        if lua is not None:
            if len(lua.encode())>1024*1024:raise AdapterError('INVALID_CART','Lua exceeds 1 MiB')
            files={'main.lua':lua.encode('utf-8')};name='inline.lua'
        else:
            path=self.path(cart);name=path.name
            if path.suffix=='.lua':
                files={'main.lua':expand_includes(path.read_text(encoding='utf-8'),path,self.root).encode('utf-8')}
            else:
                try:files=unpack_rom(path.read_bytes())
                except ValueError as exc:raise AdapterError('INVALID_CART',str(exc)) from exc
        if lua is not None or (cart and str(cart).endswith('.lua')):
            files['main.lua']=(HERE/'picotron.lua').read_bytes()+b'\n'+files['main.lua']
        if instrument:files['main.lua']+=b'\n'+(HERE/'picotron_bridge.lua').read_bytes()
        try:self.rom=pack_rom(files)
        except ValueError as exc:raise AdapterError('INVALID_CART',str(exc)) from exc
        self.generation+=1;self.booted=False;self.sequence=0
        self.loaded={'metadata':dict(name=name,instrumented=instrument,platform='picotron',romBytes=len(self.rom))}
        if self.client:
            self.client.call('Page.navigate',{'url':self.url+'?generation='+str(self.generation)})
            self._wait_js('window.__picotron_generation==='+str(self.generation)+'&&Module.calledRun',timeout_ms)
        return self.loaded['metadata']
    def run(self,paused=False,timeout_ms=45000):
        if not self.loaded:raise AdapterError('NO_CART','Load a Lua source or P64 ROM first.')
        if not self.client:self.start(headless=True,timeout_ms=timeout_ms)
        if not self.booted:
            self._wait_js('__p8agent.logs.some(l=>l.text==="@@ enter_mainloop")',timeout_ms)
            self._eval('try{FS.unlink('+json.dumps(self.base+'picotron_mcp/state.json')+')}catch(e){};Module.pico8Boot();true')
            self.booted=True
            self._wait_js('typeof node!=="undefined" && !!node.context',timeout_ms)
            self._eval('if(typeof node!=="undefined"&&!window.__audioMonitored){window.__audioMonitored=true;const f=node.onaudioprocess;node.onaudioprocess=e=>{f(e);const a=e.outputBuffer.getChannelData(0);__pcm.buffers++;for(let i=0;i<a.length;i++){__pcm.squares+=a[i]*a[i];__pcm.peak=Math.max(__pcm.peak,Math.abs(a[i]));__pcm.count++;}};}__enableSound();true')
        if self.loaded['metadata']['instrumented']:
            self._wait_js('(()=>{try{return JSON.parse(FS.readFile('+json.dumps(self.base+'picotron_mcp/state.json')+',{encoding:"utf8"})).ready}catch(e){return false}})()',timeout_ms)
            self._command('pause' if paused else 'resume',timeout_ms=timeout_ms)
        elif paused:raise AdapterError('NOT_INSTRUMENTED','Pause requires the Lua bridge.')
        return dict(running=True,state=self.state())
    def read_app_file(self,name):
        from picotron_cart import valid_path
        try:valid_path(name)
        except ValueError as exc:raise AdapterError('INVALID_ARGUMENT',str(exc)) from exc
        return bytes(self._eval('Array.from(FS.readFile('+json.dumps(self.base+name)+'))'))
    def read_json(self,name):return json.loads(self.read_app_file(name))
    def state(self):
        data=super().state()
        if not self.client:return data
        data['platform']='picotron'
        if self.loaded and self.loaded['metadata']['instrumented'] and self.booted:
            try:data['bridge']=self.read_json('picotron_mcp/state.json')
            except Exception:data['bridge']={'ready':False}
            if data['bridge'].get('error'):data['errors'].append(data['bridge']['error'])
        return data
    def _command(self,operation,*args,timeout_ms=15000):
        if not self.loaded or not self.loaded['metadata']['instrumented']:raise AdapterError('NOT_INSTRUMENTED','Load with instrument=true.')
        if not self.booted:raise AdapterError('NOT_RUNNING','Run the loaded project before bridge commands.')
        self.sequence+=1;sequence=self.sequence
        text='|'.join(map(str,(sequence,operation,*args)))
        self._eval('FS.writeFile('+json.dumps(self.base+'picotron_mcp/command.txt')+','+json.dumps(text)+');true')
        state='JSON.parse(FS.readFile('+json.dumps(self.base+'picotron_mcp/state.json')+',{encoding:"utf8"}))'
        self._wait_js('(()=>{const s='+state+';return s.seq==='+str(sequence)+'&&s.pending===0&&s.drawnUpdate===s.updates})()',timeout_ms)
        result=self.read_json('picotron_mcp/state.json')
        if result['error']:raise AdapterError('LUA_ERROR',result['error'])
        return result
    def pause(self):return self._command('pause')
    def resume(self):return self._command('resume')
    def step(self,frames=1,buttons=0,timeout_ms=20000):
        if type(frames) is not int or not 1<=frames<=600:raise AdapterError('INVALID_ARGUMENT','frames must be 1..600')
        if type(buttons) is not int or not 0<=buttons<=65535:raise AdapterError('INVALID_ARGUMENT','buttons must be 0..65535')
        before=self.pause()['updates'];result=self._command('step',frames,buttons,timeout_ms=timeout_ms)
        if result['updates']-before!=frames:raise AdapterError('STEP_MISMATCH','Unexpected update count')
        return dict(stepped=frames,state=self.state())
    def variable(self,name,value=None,write=False,create=False):
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',name):raise AdapterError('INVALID_ARGUMENT','Use a global or dotted table field')
        if write:
            if type(value) not in (int,float,bool) or (type(value) is float and not math.isfinite(value)):
                raise AdapterError('INVALID_ARGUMENT','Finite numeric or boolean values only')
            encoded=str(value).lower();result=self._command('set',name,encoded,str(bool(create)).lower())
        else:result=self._command('get',name)
        return dict(name=name,value=result['value'])
    def buttons(self,mask=0,controlled=False):
        if type(mask) is not int or not 0<=mask<=65535:raise AdapterError('INVALID_ARGUMENT','mask must be 0..65535')
        if controlled:raise AdapterError('INVALID_ARGUMENT','Use step(buttons=mask) for controlled input')
        self._eval('pico8_buttons[0]='+str(mask)+';for(let i=0;i<16;i++)picotron_buttons[0][i]=('+str(mask)+'&(1<<i))?32767:0;true')
        return dict(mask=mask)
    def screenshot(self,out=None):
        data=self._eval("new Promise(resolve=>requestAnimationFrame(()=>resolve(canvas.toDataURL('image/png'))))").split(',',1)[1]
        payload=base64.b64decode(data);path=self.path(out or 'debug/picotron/frame.png',output=True);path.write_bytes(payload)
        return dict(path=str(path),bytes=len(payload),mimeType='image/png',data=data,canvas=self.state()['canvas'])
    def audio_state(self,reset=False):
        result=self._eval('({context:typeof node!=="undefined"?node.context.state:null,volume:pico8_state.sound_volume,buffers:__pcm.buffers,peak:__pcm.peak,rms:Math.sqrt(__pcm.squares/Math.max(1,__pcm.count))})')
        if reset:self._eval('__pcm={buffers:0,squares:0,peak:0,count:0};true')
        return result
    def build(self,cart,out=None,**kwargs):
        path=self.path(cart);source=expand_includes(path.read_text(encoding='utf-8'),path,self.root)
        rom=pack_rom({'main.lua':source.encode('utf-8')});target=self.path(out or 'debug/'+path.stem+'.p64.rom',output=True)
        target.write_bytes(rom);return dict(path=str(target),romBytes=len(rom),limitBytes=262144,platform='picotron')
    def close(self):
        result=super().close();self.booted=False;return result
