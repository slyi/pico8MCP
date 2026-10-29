"""Real MCP/VM coverage for staging, aliases, LOAD/CSTORE/RELOAD and PNGs."""
from pathlib import Path
import time


def write_cart(path,code,values=()):
    path.parent.mkdir(parents=True,exist_ok=True)
    # GFX text stores the low nibble of each memory byte first.
    gfx=''.join(f'{value&15:x}{value>>4:x}' for value in values).ljust(128,'0')
    path.write_text('pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n'+code+
                    '\n__gfx__\n'+'\n'.join(gfx[i:i+128] for i in range(0,len(gfx),128))+'\n',encoding='utf-8')


def ready(client,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=client.tool('state')
        assert not state['errors'],state['errors']
        if state['gpio'][:3]==[77,67,1]:return state
        time.sleep(.03)
    raise AssertionError('Multicart app did not become ready: '+str(state))


def run_multicart_checks(client,root):
    root=Path(root)
    folder=root/'debug/ci/multicart';folder.mkdir(parents=True,exist_ok=True)
    main=folder/'main.lua'
    main.write_text('''function _init()
 if peek(0x4300)!=91 then load("data_a.p8") return end
 reload(0x8000,0,32,"cache_a.p8") reload(0x8100,0,32,"cache_b.p8")
 for i=0,31 do assert(peek(0x8000+i)==i) assert(peek(0x8100+i)==255-i) end
 ticks=0 poke(0x5f80,77,67,1,64)
end
function _update() ticks+=1 end
function _draw() cls(1) print("multicart ok",30,60,11) end
''',encoding='utf-8')
    write_cart(folder/'a/cart.p8','''function _init()
 cstore(0,0,0x2000,"cache_a.p8") load("data_b.p8")
end''',range(32))
    write_cart(folder/'b/cart.p8','''function _init()
 cstore(0,0,0x2000,"cache_b.p8") poke(0x4300,91) load("main.p8")
end''',(255-i for i in range(32)))
    relative=lambda path:str(path.relative_to(root))
    deps=[dict(cart=relative(folder/'a/cart.p8'),name='data_a.p8'),
          dict(cart=relative(folder/'b/cart.p8'),name='data_b.p8')]
    bundle=client.tool('load_bundle',entry=relative(main),carts=deps)
    assert bundle['stagedCount']==3 and bundle['entry']=='main.p8',bundle
    assert [row['name'] for row in bundle['carts']]==['data_a.p8','data_b.p8','main.p8'],bundle
    client.tool('run');state=ready(client)
    assert state['gpio'][3]==64,state
    client.tool('load_bundle',entry=relative(main),carts=deps)
    client.tool('run');ready(client)
    # A data-only PNG can be generated without triggering an application LOAD.
    data=folder/'pngdata.p8'
    write_cart(data,'''function _init()
 if peek(0x4301)==92 then
  cstore(0,0,0x2000,"pngcache.p8")
  poke(0x4301,93) load("png-main.p8")
 end
end
function _draw() cls(1) end''',range(64))
    client.tool('build',cart=relative(data),out=relative(folder/'pngdata.p8.png'),label_frames=1)
    program=folder/'png-main.p8'
    write_cart(program,'''function _init()
 if peek(0x4301)!=93 then
  poke(0x4301,92) load("pngdata.p8") return
 end
 memset(0x1000,255,64)
 reload(0x1000,0,64,"pngcache.p8")
 ticks=0 loaded=false
end
function _update()
 ticks+=1
 if peek(0x5f8c)==1 then poke(0x4301,0) poke(0x5f8c,0) end
 -- Education file I/O may finish on a later native frame.
 if not loaded and peek(0x103f)==63 then
  for i=0,63 do assert(peek(0x1000+i)==i,"png data byte "..i) end
  loaded=true poke(0x5f80,77,67,1,64)
 end
end
function _draw() cls(2) print("png bundle ok",25,60,10) end''')
    client.tool('load_bundle',entry=relative(program),carts=[relative(folder/'pngdata.p8.png')])
    client.tool('run');ready(client)
    client.tool('build',cart=relative(program),out=relative(folder/'png-main.p8.png'),label_frames=1)
    client.tool('gpio',offset=12,values=[1])
    client.tool('wait_frames',frames=3)
    png_bundle=client.tool('load_bundle',entry=relative(folder/'png-main.p8.png'),carts=[relative(folder/'pngdata.p8.png')])
    assert png_bundle['entry']=='png-main.p8',png_bundle
    client.tool('run');ready(client)
    instrumented=client.tool('load_bundle',entry=relative(program),carts=[relative(data)],instrument=True,profiling=True)
    assert not instrumented['carts'][0]['instrumented'] and instrumented['carts'][1]['instrumented']
    client.tool('run',paused=True)
    before=client.tool('get',name='ticks')['value']
    client.tool('step',frames=4)
    assert client.tool('get',name='ticks')['value']==before+4
    # Replacing the selected cart clears bundle metadata without deleting files.
    client.tool('load',lua='function _draw() cls() end')
    assert client.tool('state')['bundle'] is None
    return dict(textBundle=True,nativeSaveAcknowledged=True,aliases=True,
                loadCstoreReload=True,verifiedBytes=64,repeatedRun=True,
                pngEntryAndDependency=True,instrumentedEntry=True)
