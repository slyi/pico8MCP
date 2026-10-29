"""Real JS/WASM Picotron MCP, floor and complete Miku playback integration."""
import base64,hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from ci import Client,png_pixels,run,temporary_installation
REPO=Path(__file__).resolve().parents[1]

def main():
    out=REPO/'debug/ci-picotron';out.mkdir(parents=True,exist_ok=True)
    report={'ok':False,'nativeExecutableUsed':False}
    try:
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        with temporary_installation() as folder:
            env['PICO8_MCP_HOME']=str(folder/'common')
            run([sys.executable,'-B',str(REPO/'tests/test_picotron.py')],env=env,cwd=REPO)
            setup=[sys.executable,'-B',str(REPO/'pico8_mcp.py'),'--platform','picotron','--setup']
            first=json.loads(run(setup,env=env));assert first['cached'] is False
            assert json.loads(run(setup,env=env))['cached'] is True
            common=Path(first['installation']['home'])
            report['runtimeSha256']=first['sha256']
            floor=REPO/'examples/3dfloor'
            client=Client(common/'pico8_mcp.py',floor,env,platform='picotron')
            try:
                initialized=client.request('initialize',{'protocolVersion':'2025-06-18'})
                assert initialized['serverInfo']['name']=='picotron-mcp'
                names={t['name'] for t in client.request('tools/list')['tools']}
                assert 'picotron_step' in names and not any(n.startswith('pico8_') for n in names)
                client.tool('load',cart='floor.lua',instrument=True)
                client.tool('start',headless=True)
                client.tool('run',paused=True)
                assert client.tool('get',name='PICOTRON')['value'] is True
                assert client.tool('get',name='screen_w')['value']==480
                assert client.tool('get',name='screen_h')['value']==270
                assert client.tool('get',name='res')['value']==4
                before=client.tool('state')['bridge']['updates'];time.sleep(.15)
                assert client.tool('state')['bridge']['updates']==before
                hashes=[]
                for yaw in (0,60):
                    client.tool('set',name='yaw',value=yaw)
                    client.tool('step',frames=2,buttons=0)
                    shot=client.tool('screenshot',out=f'debug/ci-picotron/yaw-{yaw}.png')
                    data=base64.b64decode(shot['data']);pixels=png_pixels(data,(480,270))
                    ground=[p for row in pixels[150:260] for p in row[10:470]]
                    assert sum(max(p)<20 for p in ground)>len(ground)*.1
                    assert sum(min(p)>200 for p in ground)>len(ground)*.1
                    (out/f'floor-{yaw}.png').write_bytes(data);hashes.append(hashlib.sha256(data).hexdigest())
                assert hashes[0]!=hashes[1]
                client.tool('set',name='yaw',value=0)
                stepped=client.tool('step',frames=40,buttons=2)
                assert abs(client.tool('get',name='yaw')['value']-60)<.00001
                assert stepped['state']['bridge']['drawCpu']>0
                client.tool('resume');client.tool('buttons',mask=2);time.sleep(.25)
                client.tool('buttons',mask=0);client.tool('pause')
                assert client.tool('get',name='yaw')['value']>60
                report.update(mcpStdio=True,framebuffer=[480,270],pause=True,exactStep=True,nativeInput=True)
            finally:client.close()
            print('Native Picotron MCP, 480x270 floor, stepping and input passed.',flush=True)
            # Exercise the user-facing unified target selection, not a test-only loader.
            launcher=['cmd.exe','/d','/c',str(floor/'play.cmd')] if os.name=='nt' else ['sh',str(floor/'play.sh')]
            output=run([*launcher,'-target','picotron','--headless','--duration','1'],env=env,cwd=floor)
            assert 'Picotron JS/WASM running' in output,output
            miku=REPO/'examples/miku'
            output=run([sys.executable,'-B',str(miku/'play.py'),'-target','picotron','--headless'],env=env,cwd=miku,timeout=180)
            print(output,flush=True)
            assert 'audible PCM, zero motion underflows' in output
            result=json.loads((miku/'debug/picotron/play-report.json').read_text())
            assert result['final']['ended'] and result['final']['elapsed']==60
            assert result['final']['checks']==15 and result['final']['underflows']==0
            assert {s['chunk'] for s in result['states']}==set(range(1,16))
            assert result['audio']['rms']>.001
            manifest=json.loads((miku/'build-picotron/manifest.json').read_text())
            assert manifest['motionSha256']=='6aa35582948718817f4b744f5c0ed0a2f3c32291fbcb0266dd0855ad8c12dd7b'
            assert manifest['musicWordsSha256']=='d032dac2fe8a3e254d98ffa4490120ec2d33c0943ebfc040b5dbf728e26bc9b0'
            assert manifest['romBytes']<=262144
            (out/'miku.png').write_bytes((miku/'build-picotron/preview.png').read_bytes())
            (out/'miku-playback.json').write_text(json.dumps(result,indent=2)+'\n')
            report.update(mikuFullClip=True,motionDataUnchanged=True,musicDataUnchanged=True,audiblePcm=True,
                          romBytes=manifest['romBytes'],wallSeconds=result['wallSeconds'])
        report['ok']=True
    finally:
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
