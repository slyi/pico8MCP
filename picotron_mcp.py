"""Tool definitions for `pico8_mcp.py --platform picotron`."""
from pico8_mcp import tool,BOOL,STR,INT,TIMEOUT,FRAMES

def native(name,*args,**kwargs):
    result=tool(name,*args,**kwargs);result['name']='picotron_'+name;return result

TOOLS=[
 native('setup','Cache and SHA-256 verify the pinned official Picotron 0.3.0d JS/WASM player. No native executable or browser launch.',{'update':BOOL}),
 native('doctor','Inspect the Picotron runtime cache and existing browser.',readonly=True),
 native('start','Start/reuse the owned localhost Picotron browser. Load a source, then run it.',{'headless':BOOL,'browser':{'type':'string','enum':['auto','edge','chrome','chromium']},'timeout_ms':TIMEOUT}),
 native('load','Prepare root-contained Lua or a .p64.rom without running. Includes expand within --root. instrument=true enables filesystem bridge commands; source files are unchanged.',{'cart':STR,'lua':STR,'instrument':BOOL,'timeout_ms':TIMEOUT}),
 native('run','Boot the selected native Picotron cart. paused=true requires instrumentation.',{'paused':BOOL,'timeout_ms':TIMEOUT}),
 native('state','Read browser, framebuffer dimensions and optional Lua bridge/phase CPU state. CPU uses 60-Hz budget units.',readonly=True),
 native('pause','Pause instrumented application callbacks. Native sound remains independent.'),
 native('resume','Resume instrumented application callbacks and native input.'),
 native('step','Advance exact instrumented application updates with held button bits; returns paused.',{'frames':FRAMES,'buttons':{'type':'integer','minimum':0,'maximum':65535},'timeout_ms':TIMEOUT}),
 native('buttons','Set native digital/analogue player-0 button arrays.',{'mask':{'type':'integer','minimum':0,'maximum':65535}}),
 native('get','Read a numeric/boolean/nil global or dotted table field.',{'name':STR},['name'],True),
 native('set','Write a numeric or boolean global/table field. create=true permits new fields.',{'name':STR,'value':{'type':['number','boolean']},'create':BOOL},['name','value']),
 native('screenshot','Capture the real Picotron framebuffer at its actual size; return native MCP image content.',{'out':STR},readonly=True),
 native('audio_state','Inspect actual browser PCM output peak/RMS and audio activation. reset=true starts a fresh measurement window.',{'reset':BOOL},readonly=True),
 native('build','Pack root-contained Lua into a compressed native .p64.rom, using dependency-free Python LZ4. Does not launch a browser.',{'cart':STR,'out':STR},['cart']),
 native('logs','Read bounded browser/runtime logs.',{'clear':BOOL},readonly=True),
 native('close','Close only this adapter\'s owned browser and localhost server.')
]
