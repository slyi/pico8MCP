"""Build/play the existing Miku minute in the native Picotron JS/WASM VM."""
import argparse,json,time
from pathlib import Path
from build_picotron import build,ROOT,helper_root
from picotron_adapter import PicotronAdapter

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--headless',action='store_true')
    p.add_argument('--duration',type=float)
    args=p.parse_args(argv)
    rom=build();a=PicotronAdapter(ROOT,profile=ROOT/'debug/picotron'/('headless-profile' if args.headless else 'play-profile'))
    report={'states':[]}
    try:
        a.load(cart=str(rom.relative_to(ROOT)),instrument=False)
        a.start(headless=args.headless)
        a._wait_js('__p8agent.logs.some(l=>l.text==="@@ enter_mainloop")',15000)
        a._eval('try{FS.unlink('+json.dumps(a.base+'mcp_poc/state.json')+')}catch(e){};true')
        a.run()
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:state=a.read_json('mcp_poc/state.json')
            except Exception:time.sleep(.05);continue
            if state.get('error'):raise RuntimeError(state['error'])
            if state.get('prepared'):break
            time.sleep(.05)
        else:raise RuntimeError('Picotron Miku preload timed out')
        assert state['checks']==15,state
        print('Picotron: all 15 motion checksums verified; original four-channel music.',flush=True)
        started=time.monotonic();last=-1;audio_reset=False
        while a.process.poll() is None:
            state=a.read_json('mcp_poc/state.json')
            if state['error'] or state['underflows']:raise RuntimeError('Playback error: '+json.dumps(state))
            second=int(state['elapsed'])
            if second!=last:
                last=second;report['states'].append(state)
                print(json.dumps({k:state[k] for k in ('elapsed','chunk','fps','meanCpu','musicPattern','musicRow','ended')}),flush=True)
                if second in (10,20,30,40,50,59,60):a.screenshot(f'debug/picotron/miku-{second:02}.png')
            if second>=2 and not audio_reset:a.audio_state(reset=True);audio_reset=True
            if args.duration is not None and time.monotonic()-started>=args.duration:break
            if args.headless and state['ended']:break
            time.sleep(.1)
        report['final']=state;report['audio']=a.audio_state();report['wallSeconds']=time.monotonic()-started
        assert state['checks']==15 and not state['underflows']
        if args.headless:
            assert report['audio']['rms']>0.001,'Native MIDI output is silent'
            a.screenshot('build-picotron/preview.png')
            print('Picotron playback verified; audible PCM, zero motion underflows.',flush=True)
        output=ROOT/'debug/picotron/play-report.json';output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('Report:',output,flush=True)
    except Exception:
        if a.client:
            a.screenshot('debug/picotron/failure.png')
            path=ROOT/'debug/picotron/failure.json';path.write_text(json.dumps(a.state(),indent=2)+'\n',encoding='utf-8')
        raise
    finally:a.close()
if __name__=='__main__':main()
