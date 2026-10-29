"""Open the working four-cart demo in this PC's pico8mcp browser runtime."""
from pathlib import Path
import argparse
import os
import sys
import time

HERE = Path(__file__).resolve().parent
# build_lua selects this checkout or the configured shared helper path.
from build_lua import build
from adapter import Adapter
from pico8_paths import app_home
common = app_home()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-target","--target",choices=["pico8","picotron"],default="pico8")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, help="Seconds to run after the preload-ready signal.")
    args = parser.parse_args()
    if args.target=="picotron":
        from picotron_player import main as native_main
        native_args=[]
        if args.headless:native_args.append("--headless")
        if args.duration is not None:native_args += ["--duration",str(args.duration)]
        return native_main(native_args)
    if not hasattr(Adapter, "load_bundle"):
        raise SystemExit("Install pico8mcp 0.5.2 or newer with its --setup command first.")
    compiled = build()
    adapter = Adapter(Path(compiled['output']), common / "runtime", HERE / "debug" / ("headless-profile" if args.headless else "preview-profile"))
    try:
        adapter.start(headless=args.headless)
        result = adapter.load_bundle("miku.p8", ["miku_a.p8", "miku_b.p8", "miku_c.p8"])
        print(f"Staged {result['stagedCount']} carts with pico8mcp load_bundle.", flush=True)
        adapter.run(timeout_ms=60000)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = adapter.state()
            if state["errors"]:
                raise RuntimeError("; ".join(state["errors"]))
            if state["gpio"][:3] == [77,75,1]:
                break
            time.sleep(.05)
        else:
            raise RuntimeError("Miku preload did not become ready within 60 seconds.")
        print("Miku preload ready.", flush=True)
        expected=[value for pair in compiled['motion_checksums'] for value in pair]
        if state['gpio'][34:64]!=expected:
            raise RuntimeError('Motion checksums do not match the Lua source build.')
        print('All 15 motion checksums match the Lua build.',flush=True)
        print("Miku demo is running. Press X to replay; close its browser window to finish.", flush=True)
        started = time.monotonic()
        while adapter.process.poll() is None:
            if args.duration is not None and time.monotonic() - started >= args.duration:
                break
            time.sleep(.5)
        if args.headless and adapter.process.poll() is None:
            state=adapter.state()
            if state['errors'] or state['gpio'][6]:raise RuntimeError('Playback error or motion underflow.')
            adapter.screenshot(out='preview.png')
            print('Headless playback verified; zero motion underflows.',flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        adapter.close()

if __name__=='__main__':main()
