#!/usr/bin/env python3
"""PICO-8 Education Edition MCP server; newline-delimited JSON-RPC over stdio.

Python 3.10+, no pip dependencies. stdout is reserved for MCP messages.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from adapter import Adapter,AdapterError

NAME='pico8-education-mcp'
VERSION='0.3.0'
PROTOCOL='2025-06-18'

def tool(name,description,properties=None,required=None,readonly=False):
    return dict(name='pico8_'+name,description=description,
                inputSchema=dict(type='object',properties=properties or {},required=required or [],additionalProperties=False),
                annotations=dict(readOnlyHint=readonly,destructiveHint=False,openWorldHint=name in ['setup','start']))

BOOL={'type':'boolean'};INT={'type':'integer'};STR={'type':'string'}
TIMEOUT={'type':'integer','minimum':100,'maximum':60000}
MASK={'type':'integer','minimum':0,'maximum':63,'description':'0 neutral; 1 left, 2 right, 4 up, 8 down, 16 O, 32 X. Combine bits.'}
FRAMES={'type':'integer','minimum':1,'maximum':600}
TOOLS=[
 tool('setup','Download/cache only the official Education runtime. No browser, pip, system install or admin required; cached files are SHA-256 checked.',{'update':BOOL}),
 tool('doctor','Inspect Python, platform, browser discovery and runtime cache integrity. Does not launch a browser.',readonly=True),
 tool('start','Start one owned localhost browser/VM, or reuse it without reload. Requires an existing Chrome/Edge/Chromium. Setup is automatic.',
      {'headless':BOOL,'browser':{'type':'string','enum':['auto','edge','chrome','chromium']},'timeout_ms':TIMEOUT}),
 tool('load','Load a root-contained .p8/.lua cart or inline Lua; does not run. instrument=true injects a development bridge in memory, reserving GPIO 64..127. count_draws adds optional API-call counters. PNG requires instrument=false.',
      {'cart':STR,'lua':STR,'instrument':BOOL,'count_draws':BOOL,'timeout_ms':TIMEOUT}),
 tool('run','Reload the last loaded in-memory cartridge and run from a clean state in the same browser. paused=true freezes user simulation once running.',{'paused':BOOL,'timeout_ms':TIMEOUT}),
 tool('build','Build a real .p8.png cartridge from root-contained .lua/.p8 source. Loads and runs WITHOUT instrumentation, captures its native label, exports through the VM and reloads the result. Starts/reuses the owned browser; replaces the current cart. Output defaults to source-dir/debug/name.p8.png.',
      {'cart':STR,'out':STR,'label_frames':FRAMES,'timeout_ms':TIMEOUT},['cart']),
 tool('state','Read VM state, GPIO, bridge CPU/FPS/update counters and current errors. Never reloads.',readonly=True),
 tool('pause','Freeze user update/draw callbacks while keeping the bridge responsive. Requires an instrumented cart.'),
 tool('resume','Resume the instrumented cart simulation without resetting its state.'),
 tool('step','Advance exactly N user simulation updates with a virtual button mask, then pause. Requires instrumentation. Does not advance native time deterministically.',
      {'frames':FRAMES,'buttons':MASK,'timeout_ms':TIMEOUT}),
 tool('buttons','Set held controller-0 buttons. controlled=false restores native input in instrumented carts. No implicit waiting or reset.',{'mask':MASK,'controlled':BOOL}),
 tool('wait_frames','Wait for observed browser VM frames; this is not deterministic simulation stepping. Bounded timeout, no reload.',{'frames':FRAMES,'timeout_ms':TIMEOUT}),
 tool('get','Read a numeric/boolean/nil global, dotted table field, or registered p8mcp_exports getter through the Lua bridge. Unexported locals are not exposed.',{'name':STR},['name'],True),
 tool('set','Set an existing numeric/boolean global or dotted field. create=true permits new fields. PICO-8 fixed-point numeric range applies.',
      {'name':STR,'value':{'type':['number','boolean']},'create':BOOL},['name','value']),
 tool('gpio','Read GPIO bytes. Optional writes are limited to cart-owned GPIO 0..63, preserving bridge GPIO 64..127.',
      {'offset':{'type':'integer','minimum':0,'maximum':127},'count':{'type':'integer','minimum':0,'maximum':128},'values':{'type':'array','items':{'type':'integer','minimum':0,'maximum':255},'maxItems':64}}),
 tool('screenshot','Return the real 128x128 framebuffer as native MCP PNG image content and save a root-contained PNG. Captures the current cart without reload.',{'out':STR},readonly=True),
 tool('profile','Measure CPU fractions and optional draw API counts across exact simulation updates. Ends paused. sample_every=1 captures each update; higher values sample the last update of each batch.',
      {'frames':FRAMES,'buttons':MASK,'sample_every':{'type':'integer','minimum':1,'maximum':60},'timeout_ms':TIMEOUT}),
 tool('logs','Read bounded browser logs and VM system messages; clear=true clears current logs/messages.',{'clear':BOOL},readonly=True),
 tool('session','Run an ordered batch of adapter tools and assertions in the same browser. Stops at first failure and saves JSON/PNG evidence. Steps use {tool,arguments}; assert steps use {assert:{name,equals}} or {assert:{pixel:[x,y],rgba:[r,g,b,a]}}.',
      {'steps':{'type':'array','items':{'type':'object'},'minItems':1,'maxItems':100},'failure_dir':STR},['steps']),
 tool('close','Close only the browser and localhost server owned by this adapter instance. Keeps cartridges, runtime cache and evidence files.')
]

def validate(args,schema):
    if not isinstance(args,dict):raise AdapterError('INVALID_ARGUMENT','Tool arguments must be an object.')
    unknown=set(args)-set(schema['properties'])
    if unknown:raise AdapterError('INVALID_ARGUMENT','Unknown arguments: '+', '.join(sorted(unknown)))
    for name in schema.get('required',[]):
        if name not in args:raise AdapterError('INVALID_ARGUMENT','Missing required argument: '+name)
    for name,value in args.items():
        spec=schema['properties'][name];types=spec.get('type');types=types if isinstance(types,list) else [types]
        valid=any((kind=='boolean' and isinstance(value,bool)) or
                  (kind=='integer' and type(value) is int) or
                  (kind=='number' and type(value) in (int,float)) or
                  (kind=='string' and isinstance(value,str)) or
                  (kind=='array' and isinstance(value,list)) or
                  (kind=='object' and isinstance(value,dict)) for kind in types)
        if not valid:raise AdapterError('INVALID_ARGUMENT',f'{name} must have type {types}.')
        if 'enum' in spec and value not in spec['enum']:raise AdapterError('INVALID_ARGUMENT',f'Invalid {name}.')
        if type(value) in (int,float):
            if 'minimum' in spec and value<spec['minimum'] or 'maximum' in spec and value>spec['maximum']:raise AdapterError('INVALID_ARGUMENT',f'{name} is outside its supported range.')
        if isinstance(value,list):
            if len(value)<spec.get('minItems',0) or len(value)>spec.get('maxItems',10000):raise AdapterError('INVALID_ARGUMENT',f'Invalid {name} length.')

class Server:
    def __init__(self,adapter):self.adapter=adapter

    def invoke(self,name,args):
        entry=next((t for t in TOOLS if t['name']==name),None)
        if entry is None:raise AdapterError('UNKNOWN_TOOL','Unknown tool '+str(name))
        validate(args,entry['inputSchema']);a=self.adapter;short=name.removeprefix('pico8_')
        if short=='get':return a.variable(**args)
        if short=='set':return a.variable(write=True,**args)
        if short=='logs':
            state=a.state();result=dict(logs=state.get('logs',[]),systemMessages=state.get('vm',{}).get('system_messages',[]),errors=state.get('errors',[]))
            if args.get('clear'):a._eval('pico8_state.system_messages=[];__p8agent.logs=[];true')
            return result
        if short=='session':return self.session(**args)
        return getattr(a,short)(**args)

    def session(self,steps,failure_dir='debug/pico8/failures'):
        results=[]
        for index,step in enumerate(steps):
            try:
                if 'assert' in step:
                    assertion=step['assert']
                    if 'name' in assertion:
                        actual=self.adapter.variable(assertion['name'])['value']
                        expected=assertion['equals'];ok=actual==expected
                    elif 'pixel' in assertion:
                        x,y=assertion['pixel']
                        if type(x) is not int or type(y) is not int or not (0<=x<128 and 0<=y<128):raise AdapterError('INVALID_ARGUMENT','Pixel coordinates must be integers 0..127.')
                        actual=self.adapter._eval(f'Array.from(canvas.getContext("2d").getImageData({x},{y},1,1).data)')
                        expected=assertion['rgba'];ok=actual==expected
                    else:raise AdapterError('INVALID_ARGUMENT','Assertion needs name or pixel.')
                    if not ok:raise AdapterError('ASSERTION_FAILED','Session assertion failed.',{'expected':expected,'actual':actual})
                    result=dict(asserted=True,actual=actual)
                else:
                    child=step.get('tool','')
                    if not child.startswith('pico8_'):child='pico8_'+child
                    if child in ['pico8_session','pico8_setup','pico8_start','pico8_close']:raise AdapterError('INVALID_ARGUMENT','Lifecycle/nested tools are not allowed inside sessions.')
                    result=self.invoke(child,step.get('arguments',{}))
                    if isinstance(result,dict):result={k:v for k,v in result.items() if k!='data'}
                results.append(dict(index=index,result=result))
            except Exception as exc:
                failure=dict(ok=False,failedStep=index,step=step,error=error_info(exc),results=results)
                try:
                    stamp=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
                    base=failure_dir+'/'+stamp
                    failure['state']=self.adapter.state()
                    try:
                        shot=self.adapter.screenshot(base+'/frame.png');failure['screenshot']=shot['path']
                    except Exception as capture:failure['captureError']=str(capture)
                    path=self.adapter.path(base+'/failure.json',output=True)
                    failure['evidence']=str(path);path.write_text(json.dumps(failure,indent=2),encoding='utf-8')
                except Exception as bundle:failure['bundleError']=str(bundle)
                return failure
        return dict(ok=True,steps=len(results),results=results)

    def handle(self,request):
        ident=request.get('id');method=request.get('method');params=request.get('params') or {}
        if 'params' in request and not isinstance(request['params'],dict):return rpc_error(ident,-32602,'params must be an object')
        if method=='initialize':
            supported=['2024-11-05','2025-03-26',PROTOCOL]
            result=dict(protocolVersion=params.get('protocolVersion') if params.get('protocolVersion') in supported else PROTOCOL,
                        capabilities={'tools':{},'resources':{}},serverInfo={'name':NAME,'version':VERSION},
                        instructions='Start one browser and reuse it. Load text carts with instrumentation for exact simulation stepping, scalar get/set and CPU telemetry. Setup caches the official runtime; no installation of Python or browser. Paths stay under --root. Close owned browser when finished. See pico8://help.')
        elif method=='ping':result={}
        elif method=='tools/list':result={'tools':TOOLS}
        elif method=='tools/call':
            try:
                value=self.invoke(params.get('name'),params.get('arguments',{}))
                content=[]
                if isinstance(value,dict) and value.get('mimeType')=='image/png' and 'data' in value:
                    value=dict(value);encoded=value.pop('data');content.append({'type':'image','mimeType':'image/png','data':encoded})
                content.insert(0,{'type':'text','text':json.dumps(value,ensure_ascii=False,allow_nan=False)})
                result={'content':content,'structuredContent':value,'isError':isinstance(value,dict) and value.get('ok') is False}
            except Exception as exc:
                value={'ok':False,'error':error_info(exc)}
                result={'content':[{'type':'text','text':json.dumps(value)}],'structuredContent':value,'isError':True}
        elif method=='resources/list':result={'resources':[{'uri':'pico8://help','name':'Adapter workflow and limitations','mimeType':'text/markdown'}]}
        elif method=='resources/read':
            if params.get('uri')!='pico8://help':return rpc_error(ident,-32602,'Unknown resource')
            path=Path(__file__).with_name('readme.md')
            result={'contents':[{'uri':'pico8://help','mimeType':'text/markdown','text':path.read_text(encoding='utf-8') if path.exists() else 'See tools/list.'}]}
        elif isinstance(method,str) and method.startswith('notifications/'):return None
        else:return rpc_error(ident,-32601,'Method not found')
        if ident is None:return None
        return {'jsonrpc':'2.0','id':ident,'result':result}

def error_info(exc):
    return {'code':getattr(exc,'code','INTERNAL_ERROR'),'message':str(exc),'details':getattr(exc,'details',{})}

def rpc_error(ident,code,message):return {'jsonrpc':'2.0','id':ident,'error':{'code':code,'message':message}}

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',newline='\n')
    if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8',errors='backslashreplace')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd(),help='Allowed cart/artifact root (default current directory).')
    parser.add_argument('--cache',type=Path,help='Runtime cache (default shared user-data runtime directory).')
    parser.add_argument('--profile',type=Path,help='Explicit browser profile override; default is the shared localhost profile.')
    parser.add_argument('--setup',action='store_true',help='Install/verify runtime cache and exit; no browser needed.')
    parser.add_argument('--update',action='store_true',help='Refresh the official runtime during --setup.')
    parser.add_argument('--doctor',action='store_true',help='Print environment checks and exit.')
    parser.add_argument('--build',metavar='CART',help='Build a .lua/.p8 source into a labelled .p8.png using the native VM.')
    parser.add_argument('--out',help='Build output path within --root; defaults to source-dir/debug/name.p8.png.')
    args=parser.parse_args()
    if args.out and not args.build:parser.error('--out requires --build')
    if args.build and (args.setup or args.doctor):parser.error('--build cannot be combined with --setup or --doctor')
    adapter=Adapter(args.root,args.cache,args.profile)
    if args.build:
        try:print(json.dumps(adapter.build(args.build,args.out),indent=2));return 0
        except Exception as exc:print(json.dumps({'ok':False,'error':error_info(exc)}));return 1
        finally:adapter.close()
    if args.setup or args.doctor:
        try:print(json.dumps(adapter.setup(args.update) if args.setup else adapter.doctor(),indent=2));return 0
        except Exception as exc:print(json.dumps({'ok':False,'error':error_info(exc)}));return 1
    server=Server(adapter)
    try:
        # MCP stdio is one JSON-RPC object per line, never HTTP Content-Length framing.
        for line in sys.stdin.buffer:
            if len(line)>2*1024*1024:
                response=rpc_error(None,-32600,'Request exceeds 2 MiB')
            else:
                try:
                    request=json.loads(line)
                    if not isinstance(request,dict) or request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):
                        response=rpc_error(request.get('id') if isinstance(request,dict) else None,-32600,'Invalid JSON-RPC request')
                    else:response=server.handle(request)
                except (json.JSONDecodeError,UnicodeDecodeError):response=rpc_error(None,-32700,'Parse error')
                except Exception as exc:response=rpc_error(None,-32603,str(exc))
            if response is not None:
                sys.stdout.write(json.dumps(response,ensure_ascii=False,allow_nan=False)+'\n');sys.stdout.flush()
    except (BrokenPipeError,KeyboardInterrupt):pass
    finally:adapter.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
