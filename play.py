"""Play a local cart using the shared PICO-8 Education installation."""
import argparse,sys,time
from pathlib import Path
from adapter import Adapter,AdapterError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cart',type=Path,help='Path to a local .p8/.lua/.p8.png cart.')
    parser.add_argument('--telemetry',choices=['none','floor'],default='none')
    parser.add_argument('--headless',action='store_true',help='Run without a visible browser (testing).')
    parser.add_argument('--duration',type=float,help='Optional test run length in seconds.')
    args=parser.parse_args();cart=args.cart.expanduser().resolve()
    if not cart.is_file():parser.error('Cartridge does not exist: '+str(cart))
    adapter=Adapter(cart.parent)
    try:
        adapter.start(headless=args.headless)
        adapter.load(cart=cart.name,instrument=False);adapter.run()
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
