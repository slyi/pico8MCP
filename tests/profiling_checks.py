"""Native VM checks shared by CI and local cached-runtime validation."""

def run_profiler_checks(client):
    source='''
local function child()
 local n=0
 for i=1,40 do n+=i*.01 end
 return 42,nil,7,nil
end
local function parent()
 -- @profile begin work
 local a,b,c,d=child()
 -- @profile end work
 return a,b,c,d
end
valid=true
local function check(...)
 valid=valid and select("#",...)==4
 return ...
end
function _update()
 local a,b,c,d=check(parent())
 valid=valid and a==42 and b==nil and c==7 and d==nil
end
function _draw()
 -- @profile begin raster
 cls(1) rectfill(20,20,40,40,8)
 -- @profile end raster
end
'''
    client.tool('load',lua=source,profile_functions=['parent','child','missing_function'],profile_sections=True)
    client.tool('run',paused=True)
    stepped=client.tool('profile',frames=12,sample_every=3,out='debug/ci/profile-stepped.json')
    detail=stepped['detail'];rows={r['name']:r for r in detail['hotspots']}
    assert detail['updateCalls']==12,detail
    for name in ['parent','child','work']:
        assert rows[name]['calls']==12,rows
    assert rows['missing_function']['missing'] and rows['missing_function']['calls']==0,rows
    assert rows['parent']['inclusiveCpu']>rows['child']['inclusiveCpu']>0,rows
    assert abs(sum(r['selfSharePercent'] for r in rows.values())+detail['unattributedSharePercent']-100)<.1,detail
    assert detail['invalidSpans']==0,detail
    assert client.tool('get',name='valid')['value'] is True
    realtime=client.tool('profile_realtime',seconds=2,sample_ms=50,out='debug/ci/profile-realtime.json')
    assert 20<realtime['rates']['drawsPerSecond']<40,realtime
    assert realtime['detail']['renderFrames']>30,realtime
    assert client.tool('state')['bridge']['paused'] is True
    # A known over-budget draw must be distinguished from a nominal VM target.
    client.tool('load',lua='''function _update() end
function _draw()
 local n=0
 for j=1,12 do
  for i=1,18000 do n=(n+i)%100 end
 end
 cls(n%16)
end''',profiling=True)
    client.tool('run',paused=True)
    heavy=client.tool('profile_realtime',seconds=2,sample_ms=50,out='debug/ci/profile-heavy.json')
    assert heavy['detail']['nativeFrameCpu']['overBudgetFrames']>0,heavy
    assert heavy['detail']['nativeFrameCpu']['max']>3,heavy
    assert max(s['cpu'] for s in heavy['samples'])==3,heavy
    assert heavy['rates']['drawsPerSecond']<realtime['rates']['drawsPerSecond']*.85,heavy
    # Start the coroutine during the capture, rather than during run startup.
    client.tool('load',lua='''co=nil finished=false
function work()
 -- @profile begin across_yield
 yield()
 -- @profile end across_yield
 finished=true
end
function _update()
 if co and costatus(co)!="dead" then coresume(co) end
end
function _draw() cls() end
p8mcp_exports={start={get=function() return co!=nil end,
 set=function(v) if v then co=cocreate(work) end end}}''',profile_sections=True)
    client.tool('run',paused=True)
    client.tool('set',name='start',value=True)
    invalid=client.tool('profile',frames=4)
    assert invalid['detail']['invalidSpans']>0,invalid
    assert client.tool('get',name='finished')['value'] is True
    client.tool('load',lua='function _draw() cls() end')
    client.tool('run',paused=True)
    plain=client.tool('profile_realtime',seconds=1)
    assert plain['detail'] is None and plain['rates']['drawsPerSecond']>20,plain
    client.tool('load',lua='function _draw() cls() end',profiling=True)
    client.tool('run',paused=True)
    failed=client.request('tools/call',dict(name='pico8_profile',arguments=dict(frames=600,sample_every=60,timeout_ms=100)))
    assert failed.get('isError'),failed
    assert client.tool('state')['bridge']['paused'] is True
    recovered=client.tool('profile',frames=3)
    assert recovered['detail']['updateCalls']==3,recovered
    return dict(stepped=stepped,realtime=realtime,heavy=heavy,invalid=invalid,plain=plain)
