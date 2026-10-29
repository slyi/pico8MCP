"""Play a Lua source or P64 ROM in the local Picotron browser VM."""
import argparse,time
from pathlib import Path
from picotron_adapter import PicotronAdapter

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('cart',type=Path)
    p.add_argument('--root',type=Path,help='Allowed source/include root; default is the cart folder.')
    p.add_argument('--headless',action='store_true')
    p.add_argument('--duration',type=float)
    args=p.parse_args(argv);cart=args.cart.resolve()
    a=PicotronAdapter(args.root.expanduser().resolve() if args.root else cart.parent)
    try:
        a.load(cart=str(cart),instrument=False)
        a.start(headless=args.headless);a.run()
        a._wait_js('__p8agent.logs.some(l=>l.text.includes("main /ram/expcart/main.lua"))',30000)
        a.wait_frames(20)
        print('Picotron JS/WASM running at native 480x270; close its browser to finish.',flush=True)
        started=time.monotonic()
        while a.process.poll() is None:
            if args.duration is not None and time.monotonic()-started>=args.duration:break
            time.sleep(.2)
        if args.headless and a.process.poll() is None:a.screenshot('debug/picotron/preview.png')
    except KeyboardInterrupt:pass
    finally:a.close()
if __name__=='__main__':main()
