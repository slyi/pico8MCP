"""Play a local cart in PICO-8 Education or Picotron JS/WASM."""
import argparse,sys,time
from pathlib import Path
from adapter import Adapter,AdapterError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cart',type=Path,help='Path to a local .p8/.lua/.p8.png cart.')
    parser.add_argument('-target','--target',choices=['pico8','picotron'],default='pico8')
    parser.add_argument('--telemetry',choices=['none','floor'],default='none')
    parser.add_argument('--headless',action='store_true',help='Run without a visible browser (testing).')
    parser.add_argument('--duration',type=float,help='Optional test run length in seconds.')
    parser.add_argument('--extra-cart',action='append',default=[],metavar='PATH[=NAME]',help='Stage a dependency with native SAVE; repeat for multicart. Relative paths use --root or the entry folder. Optional NAME is its native .p8 filename.')
    parser.add_argument('--entry-name',help='Native .p8 name for a bundle entry, e.g. main.p8.')
    parser.add_argument('--root',type=Path,help='Allowed bundle source root (default entry cart folder).')
    args=parser.parse_args();cart=args.cart.expanduser().resolve()
    if not cart.is_file():parser.error('Cartridge does not exist: '+str(cart))
    if args.target=='picotron':
        if args.extra_cart or args.entry_name:parser.error('Picotron uses one P64 project, rather than a PICO-8 bundle.')
        from play_picotron import main as native_main
        native_args=[str(cart)]
        if args.headless:native_args.append('--headless')
        if args.root:native_args+=['--root',str(args.root)]
        if args.duration is not None:native_args+=['--duration',str(args.duration)]
        return native_main(native_args)
    adapter=Adapter(args.root.expanduser().resolve() if args.root else cart.parent)
    carts=[]
    for raw in args.extra_cart:
        source,separator,name=raw.partition('=')
        carts.append({'cart':source,'name':name} if separator else source)
    bundle=bool(carts or args.entry_name)
    try:
        if bundle:adapter._plan_bundle(str(cart),carts,args.entry_name)
        adapter.start(headless=args.headless)
        if bundle:
            result=adapter.load_bundle(entry=str(cart),carts=carts,entry_name=args.entry_name)
            print(f"Staged {result['stagedCount']} carts; entry {result['entry']}.")
        else:adapter.load(cart=str(cart),instrument=False)
        adapter.run()
        if args.telemetry=='floor':
            print('WASD move; arrows yaw/pitch; 1-4 resolution; B width; [/] crop; G grid; T dither; H CPU; R reset.')
        print('Close the browser or press Ctrl+C here to exit.')
        started=time.monotonic()
        while adapter.process and adapter.process.poll() is None:
            if args.duration is not None and time.monotonic()-started>=args.duration:break
            if args.telemetry=='floor':
                g=adapter.gpio(offset=0,count=14)['values']
                print(f'\rCPU {(g[0]+256*g[1])/100:5.2f}% | {g[2]:2d} FPS | RES {g[3]} | yaw {g[4]+256*g[5]:3d} | pitch {g[9]-45:3d} | clip {g[10]:3d} | grid {"on " if g[7] else "off"} | dither {"on " if g[8] else "off"}',end='',flush=True)
            time.sleep(.25)
        return 0
    except KeyboardInterrupt:return 0
    except AdapterError as exc:
        # A browser closed between the process poll and the telemetry read.
        if exc.code=='BROWSER_ERROR' and adapter.process:
            try:adapter.process.wait(timeout=3);return 0
            except __import__('subprocess').TimeoutExpired:pass
        print(f'PICO-8: {exc}',file=sys.stderr);return 1
    finally:adapter.close()

if __name__=='__main__':raise SystemExit(main())
