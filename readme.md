# PICO-8 / Picotron MCP / 0.6.0

[![Cross-platform tests](https://github.com/slyi/pico8MCP/actions/workflows/test.yml/badge.svg)](https://github.com/slyi/pico8MCP/actions/workflows/test.yml)

This project is a personal experiment in recreating some of my Scratch
projects inside PICO-8's restricted virtual machine. It uses a similar
MCP approach to [gobo-agent](https://github.com/slyi/gobo-agent), so an agent
can edit a project, run it, inspect its state and measure its performance.
Building this adapter even helped me find issues in gobo-agent's architecture.

I had always envied how much faster PICO-8's VM seemed than Scratch's VM.
I particularly like its fixed virtual CPU budget: roughly 128K VM cycles
per frame at 30 FPS. That gives projects a consistent performance budget
across platforms, and the HTML player includes on-screen touch controls
for mobile play. The [PICO-8 manual](https://www.lexaloffle.com/dl/docs/pico-8_manual.html)
describes the virtual CPU as about four million instructions per second.

Storage is a much tighter constraint. Scratch's **5 MB uncompressed
`project.json` limit** covers code, lists and project metadata; costumes and
sounds are separate assets, so it is not a 5 MB limit on the whole project.
PICO-8 instead has a **32 KiB cartridge ROM budget**, containing compressed
Lua code and fixed asset regions. PICO-8 0.2.7 supports **up to 32 cartridges
in an HTML or binary export**: about **1 MiB of aggregate cartridge ROM**.
See the [Scratch size-limit explanation](https://scratch.mit.edu/discuss/post/5450520/)
and [PICO-8 0.2.7 multicart release notes](https://www.lexaloffle.com/bbs/?pid=172274).

**Important — generative AI:** This is an AI-assisted project for my own
learning. Lexaloffle's [Terms of Use](https://www.lexaloffle.com/info.php?page=tos)
prohibit BBS and podnet submissions containing output from AI models
trained on uncredited authors' work, including cartridge source code,
graphics, audio and labels. These experiments are not intended for submission
to the PICO-8 Gallery/BBS.

## Examples

The first test was a [DDA 3D floor](examples/3dfloor/readme.md). Scratch
has poor options for 3D floor rendering, although our DDA approach with
projected grid lines works reasonably well there. PICO-8 runs this approach
with plenty of CPU headroom; its 128 × 128 display is the main limitation
on visual detail.
The example includes measured CPU and drawing-count tables for RES 1–4.
Run setup below once, then launch `play.cmd` or `play.sh`.

The second test, [Miku multicart](examples/miku/readme.md), has excellent
performance compared with my Scratch version, but fitting the data into
small cartridges takes much more work. **Four cartridges play one minute
of an MMD skeletal dance with a MIDI-derived instrumental chiptune soundtrack.**
The example includes documented Lua model, music and motion data. Its
launchers build the carts with pure-Python ZX0, stage them through
`LOAD`/`CSTORE`/`RELOAD`, and verify all 15 motion checksums. Shared code uses
`#include`; generated carts and caches stay in ignored folders.

## Picotron JS/WASM support

The same Python host now supports the official **Picotron 0.3.0d browser
VM**, alongside PICO-8 Education. It runs the JavaScript/WASM export player;
no `picotron.exe` is installed or launched. The two backends share browser
discovery, localhost hosting, CDP input and screenshots, with separate
runtime caches and browser profiles.

```text
python pico8_mcp.py --platform picotron --setup
python pico8_mcp.py --platform picotron --root examples/3dfloor
```

The Picotron MCP server exposes `picotron_*` tools: load/build Lua or a
`.p64.rom`, start/run, pause/resume, exact callback stepping, scalar get/set,
controller input, native-size screenshots, audio output peak/RMS and logs.
`picotron://help` serves this README. The injected development bridge uses
Picotron's virtual filesystem rather than PICO-8 GPIO. Normal example
playback runs without that bridge; native sound continues independently
when only application callbacks are paused.

From either example directory, use one launcher:

```text
play.cmd -target pico8
play.cmd -target picotron
sh play.sh -target picotron
```

PICO-8 is the default. `picotron.lua` contains reusable viewport, material
palette, synth-clock and frame-pacing helpers. The floor keeps one
`floor.lua`; Miku shares `player.lua` for bone transforms, interpolation,
skinning, triangle filling, sorting and Verlet physics. Its thin native
adapter changes asset I/O, viewport/palette setup and music-clock access.
The original motion samples and four-channel note data are preserved.

Picotron cartridges use a **256 KiB compressed ROM limit**. The native Miku
build is about **169 KiB** and needs one cartridge. `picotron_cart.py` writes
standard P64, typed POD and LZ4 formats using Python's standard library;
there is no vendor executable, Shrinko8 installation or new motion codec.
Generated ROMs and browser/runtime caches are ignored by Git.

The public JS/WASM player is downloaded on demand from Lexaloffle and
checked against the supported version's pinned SHA-256. It is not bundled
in this repository. `PICOTRON_RUNTIME_JS` can select a local copy of that
same unmodified player for offline setup.

Picotron reports `stat(1)` in 60-Hz budget units: a reading of 1.2 is roughly
120% of a 60-FPS budget, or 60% of a 30-FPS budget. Miku targets 30 FPS and
scales its CPU monitor accordingly. Bridge phase measurements retain the
native units and report `cpuBaseHz=60`.

The GitHub Actions Picotron job checks fresh/cached setup, real 480×270
floor screenshots, MCP stepping and input, unified launchers, and the
complete Miku sequence with non-silent native PCM output. It verifies all
15 motion checksums, the source data hashes and the ROM size. Reports and
screenshots are uploaded; runtime files and browser profiles are excluded.

## Adapter overview

A standalone, agent-friendly Python MCP adapter for the official PICO-8
Education Edition. Uses one warm Chromium browser and a localhost-only
Python HTTP server. Python 3.10+; standard library only, no pip packages.

## Setup (no admin / sudo)

    python pico8_mcp.py --setup
    python pico8_mcp.py --doctor

## Posix / Wsl:
    python3 pico8_mcp.py --setup
    python3 pico8_mcp.py --doctor

Setup downloads the official Education player from:
    https://www.pico-8-edu.com/
Currently pico8_edu_0207.js is 2,002,331 bytes and embeds its boot data.
Setup installs common adapter files and the official runtime in one
user-owned data directory:
Windows: %LOCALAPPDATA%\pico8mcp
Linux/WSL: $XDG_DATA_HOME/pico8mcp (default ~/.local/share/pico8mcp)
macOS: ~/Library/Application Support/pico8mcp
PICO8_MCP_HOME overrides that directory. The installed MCP entrypoint is
pico8_mcp.py directly inside it, with play.py, runtime/ and
profiles/localhost/. Play a cart with:
    python /installed/pico8mcp/play.py /path/to/cart.p8
The floor play.cmd/play.sh wrappers call that installed player directly.
--cache PATH overrides the runtime directory; --profile PATH or
PICO8_MCP_PROFILE_DIR explicitly selects another profile for isolated tests. No Windows installer, registry changes, sudo,
PowerShell, pip dependency or browser download is used by setup.
Setup stores source URL, version, size, UTC timestamp and SHA-256 in
runtime/manifest.json inside the shared installation. install.json records
common-file hashes. Setup copies only changed common files and migrates
a verified old .runtime cache when present. Repeated setup checks integrity and reuses files.
Use --setup --update to deliberately refresh the version. A corrupt cache
fails explicitly instead of silently replacing it.

The runtime is fetched directly from Lexaloffle, not a GitHub mirror.
The adapter source can be copied/distributed; do not bundle the downloaded
runtime directory or browser profile.
PICO-8 remains the property of Lexaloffle. This adapter is not affiliated
with or endorsed by Lexaloffle.

## Build a labelled `.p8.png` cartridge

From the repository directory:

```sh
python pico8_mcp.py --root examples/3dfloor --build floor.lua
```

Use `python3` on POSIX. The output is
`examples/3dfloor/debug/floor.p8.png`. Override its location with
`--out debug/my-floor.p8.png` (relative to `--root`).

The equivalent MCP tool is:

```json
{"name":"pico8_build","arguments":{"cart":"floor.lua","out":"debug/floor.p8.png","label_frames":10}}
```

Build starts or reuses the owned VM, loads the source without development
instrumentation, runs it for the requested frames, captures the screen
with the Education VM's native Ctrl+7 label shortcut, and executes native `save`.
It then reloads and runs the generated PNG to verify the cartridge.
It replaces the current cart and leaves the generated cart running.
CLI builds close their owned browser when complete.

This is a real 160 × 205 PNG cartridge with embedded game data and label,
not a renamed text file or screenshot. The format supports cartridge sharing;
the AI-assisted examples here are for personal learning as explained above.
Input and output paths must stay within `--root`.
An existing Chrome/Edge/Chromium browser is required. No external Python
packages or desktop PICO-8 installation are needed.

## Run The Mcp Server

    python pico8_mcp.py --root /absolute/path/to/your/cart-project

Windows also accepts ordinary Windows absolute paths. MCP transport is
stdio, one JSON-RPC message per line. stdout contains MCP responses only;
no shell command strings are constructed. Tools return structuredContent,
human-readable JSON text, and isError on operational failures.
MCP protocol versions: 2024-11-05, 2025-03-26, 2025-06-18.
Only tools/resources are advertised. This version has no HTTP transport.
Calls are serialized and bounded; cancellation is not preemptive.

Set PICO8_BROWSER to a browser executable if automatic discovery fails.
Chrome, Chromium and Edge are supported. A browser is required only for
live VM tools, not setup, doctor or MCP startup. Live Linux/macOS browser
execution was not tested here; WSL setup/stdio was tested without one.

MCP client registration uses:
    command: python (python3 on POSIX)
    args: ["/absolute/path/to/pico8_mcp.py", "--root", "/your/project"]
See mcp-server.example.json. Replace its paths for your machine/client.
The adapter is not automatically registered in your Codex configuration.

## Normal Agent Loop

1. pico8_setup {}                  once; later setup calls reuse the cache
2. pico8_start {}                  starts headless, or reuses the warm VM
3. pico8_load {"cart":"floor.p8", "count_draws":true}
4. pico8_run {"paused":true}       clean reload/run in the same browser
5. pico8_set {"name":"yaw", "value":15}
6. pico8_step {"frames":120, "buttons":4}
7. pico8_get {"name":"camera.x"}
8. pico8_profile {"frames":60, "buttons":4}
9. pico8_screenshot {"out":"debug/yaw15.png"}
10. Edit the local source, then load/run in the SAME browser.
11. pico8_close {}                 close only this adapter's browser/server

Button masks: 1 left, 2 right, 4 up, 8 down, 16 O, 32 X. Add bits for
combinations; 0 means neutral. These are PICO-8 controller buttons, not
the example's WASD mapping. The bundled floor example supports WASD in addition to controller input.

## Tools

setup / doctor / start / load / load_bundle / run / build / pack_source / cart_size / state / pause / resume / step /
buttons / wait_frames / get / set / gpio / screenshot / profile / profile_realtime / logs /
session / close (all tool names have the pico8_ prefix).

There is no arbitrary host-JavaScript eval tool. Cart input and screenshots
stay under the configured --root, including symlink resolution. The HTTP
server binds 127.0.0.1 on an ephemeral port and serves only the host HTML
and cached player. It does not expose project files.

start reuses an existing owned browser without reload. run explicitly
restarts the last loaded cart. load accepts a text .p8/.lua file or a Lua
source string. PNG carts run with instrument=false. Source files are not
modified by instrumentation; injection exists only in the browser copy.
A missing __gfx__ footer is normalized in memory for Education's loader.

## Building from Lua sources

Keep generated `.p8` and `.p8.png` files out of Git. The multicart example's
launchers build its Lua sources into an ignored `build/` folder before playing.

`pico8_pack_source {"cart":"main.lua"}` builds a text cart offline, defaulting
to `build/main.p8`. The optional data prefix contains literal `asset_bytes`
(starting at address 0) or `asset_regions` (`{address,bytes}` records). Separate
it from executable code with `-- ACTUAL CARTRIDGE LUA CODE`. Plain Lua without
that marker is accepted as code with empty assets. Project Lua is copied,
never executed by the packer. Source and include paths remain under `--root`.

`#include file.lua` shares source code. Includes are flattened into the built
cart, with root containment, cycle detection and size/depth limits. Shared
code still counts toward each output cart's normal limits.

The installed `source_build.py` module provides `read_source`, `include_files`,
`dump_lua`, `cart_text`, `read_cart_assets`, `build_cart`, and `compress_zx0`.
The last helper uses the bundled `zx0_codec.py` port of Einar Saukas'
standard v2 optimal compressor and decoder. It works with the Python standard
library alone. Optional `pip install numpy` accelerates larger streams; optional
`pip install numba` enables a faster Python-authored JIT parser. No separate
executable or C compiler is required. NumPy and Numba wheels contain native
libraries; Numba also depends on llvmlite/LLVM. Small inputs use the original
parser. Larger inputs select installed Numba, then NumPy, then standard Python.
`compress(data, backend="numpy")` selects explicitly; accepted backends are
`python`, `numpy`, `numba`, and `auto` (the default).
All backends preserve the reference bitstream, including equal-cost choices.
Numba's initial compilation adds startup time; its kernel is cached on disk.

Verified results are cached. `compress_zx0_many` runs independent streams in
worker processes; call it under a Python main guard. Unchanged streams reuse
the cache. Miku's mesh and motion rules remain in its project packer. Pillow
is not needed by these helpers. The upstream BSD notice is preserved in both
codec modules; the native PICO-8 runtime remains unchanged.

`artifacts.py` provides `png_rom`, `cart_sizes`, and `package_files`. The
read-only `pico8_cart_size` tool reports a native PNG's file size, decoded
32 KiB ROM and compressed Lua bytes without launching a browser. This
inspector supports modern PXA/raw native carts, not legacy pre-PXA encoding.

## Local multicart bundles

Start one VM, then stage every dependency and the entry in one operation:

```json
{"name":"pico8_load_bundle","arguments":{
  "entry":"miku.p8",
  "carts":["miku_a.p8","miku_b.p8","miku_c.p8"]
}}
```

Call `pico8_run {}` afterward. The tool does not run the entry itself.
It stops an active game, drops each dependency into the native editor and
executes native `SAVE`, waiting for the exact filename's save acknowledgement.
It confirms replacement when that owned VM already contains the same name.
The entry is saved last and remains selected. Native `LOAD` resolves the
staged names. The tested Education preload sequence is `LOAD` the data cart,
`CSTORE` a named cache, `LOAD` the player, then `RELOAD` from the cache.
Direct `RELOAD` from a freshly staged input cart did not populate data on
this runtime; the examples use the cache sequence. The official runtime
is unchanged, project files are not rewritten, and no project HTTP routes
or arbitrary JavaScript tool are added.

Inputs may be `.p8`, `.lua` or `.p8.png`. PNG names normalize from
`miku_a.p8.png` to `miku_a.p8`: the VM serializes each PNG to text, which is
normalized and registered under that native name. Lua names normalize to `.p8`. A bare Lua file
has no sprite/map/sound assets: use the accompanying `.p8` when the game
needs encoded asset data. For aliases or source files in different folders:

```json
{"name":"pico8_load_bundle","arguments":{
  "entry":"src/player.lua", "entry_name":"main.p8",
  "carts":[{"cart":"data/source-a.p8.png","name":"data_a.p8"}]
}}
```

Native names are flat ASCII `.p8` filenames, at most 64 characters. Each
dependency is a path or `{cart,name}` object. Duplicate native names,
including the entry, are rejected. All source paths are resolved under
`--root`, including symlinks. Every file, name and source is validated
before VM changes begin. The adapter accepts up to 64 carts per operation,
including the entry, and at most 1 MiB per source file; these are adapter
bounds, not a statement of PICO-8 export limits. `timeout_ms` is a total
staging deadline, defaulting to 60,000 ms.

Successful results and `pico8_state.bundle` include the entry, staged
native names, original source SHA-256 values and instrumentation metadata.
`pico8_run` restarts that entry without restaging. Ordinary `pico8_load`
clears the selected bundle metadata. Native files remain in the owned
profile; saving an existing native name replaces that profile's copy.
On an operational failure, `BUNDLE_STAGE_FAILED` reports `failedCart` and
the successfully `staged` files. Partial saves are not rolled back; no
complete bundle remains selected. Cart-managed caches retain normal
PICO-8 semantics.

Instrumentation defaults to **false** for bundles, so PNGs and carts using
all GPIO can run directly. `instrument`, `count_draws`, `profiling`,
`profile_functions` and `profile_sections` apply only to the entry;
dependencies remain uninstrumented. Text entries can opt in with
`instrument:true`. A `LOAD` into another cart does not carry the entry's
Lua bridge along: debugging commands are available when that instrumented
entry is active. `run` confirms execution started, not that an application's
own preload has finished; use its GPIO or exported readiness signal.

The shared command-line player uses the same API:

```sh
python /installed/pico8mcp/play.py /path/to/miku.p8 \
  --extra-cart miku_a.p8 --extra-cart miku_b.p8 --extra-cart miku_c.p8
```

Dependency paths are relative to the entry folder, or to `--root` when
provided. Absolute paths are accepted only inside that root. Repeat
`--extra-cart PATH=NAME` for aliases; `--entry-name main.p8` aliases the
entry. Existing single-cart usage is unchanged. See the Miku
[multicart example](examples/miku/readme.md) for readable Lua sources
and reproducible carts.

This feature stages **local files**. Loading public BBS identifiers still
needs the BBS network routes, which the localhost host does not provide.
Multicart staging does not fix that separate public-BBS routing limitation.

screenshot returns native MCP image content AND a PNG artifact path. It
captures the current framebuffer without rebuilding/loading/stopping.
state/logs include bounded VM messages and browser errors. Large data-URL
cart payloads are redacted from logs. A failed session saves JSON state
and a PNG to debug/pico8/failures/ and stops at the failing step.

Session example:
{"steps":[
 {"tool":"set","arguments":{"name":"x","value":20}},
 {"tool":"step","arguments":{"frames":5,"buttons":2}},
 {"assert":{"name":"x","equals":25}},
 {"tool":"screenshot","arguments":{"out":"debug/step.png"}}
]}
Optional pixel assertions use {"assert":{"pixel":[25,88],
"rgba":[255,0,77,255]}}. Sessions do not roll back prior steps on failure.

## Simulation And Profiling

Instrumented text carts gain a small appended Lua bridge. GPIO bytes
64..127 (PICO-8 memory 0x5fc0..0x5fff) are reserved; bytes 0..63 remain
cart-owned. Commands use sequence/acknowledgement bytes. A step completes
only after the requested user updates and final user draw. Pause freezes
both user callbacks while the browser VM and bridge stay responsive.

step is deterministic for update COUNTS, not wall-clock time(). Use fixed
_update/_update60 deltas in deterministic cart tests. wait_frames observes
native VM render frames; it is deliberately distinct from step.
Virtual input overrides btn/btnp for controller 0 while controlled=true.
btnp implements first-press plus default 15/4 repeats (doubled at 60Hz);
custom hardware repeat settings are not mirrored. Other controllers and
controlled=false use native input. Directly cached API references or
callbacks replaced later by the cart can bypass wrappers.

CPU values are PICO-8 stat(1) fractions, not Scratch opcode counts.
cpu is sampled after user drawing; cpuUser brackets user update/draw.
Both are DEVELOPMENT measurements: wrappers consume CPU and tokens.
CPU telemetry saturates at 3.0 (300%); cpuSaturated reports that condition.
fps is stat(7) from the VM, not a measurement of physical screen refresh.

count_draws is opt-in. When enabled, drawing API calls are wrapped and
counted, including cls and print; these are NOT Scratch pen/GPU draws.
When disabled, drawCalls and its profile summary are null, not zero.
Profile ends paused. sample_every >1 samples only the final update in
each batch; use 1 for per-update samples. CPU fractions already contain
native drawing costs. The CPU counter and optional wrappers are not a
full Lua bytecode/procedure profiler.

### Playback rates and named CPU scopes

`pico8_profile_realtime {"seconds":5,"sample_ms":100,"out":"debug/profile.json"}`
runs the cart freely and counts update/draw callbacks against the browser's
monotonic wall clock. It ends paused. `drawsPerSecond` and
`updatesPerSecond` are measured rates; `vmFps` remains the separate stat(7)
value. The window includes the initial resume scheduling delay. Poll gaps
describe sampling delays, not exact frame times or physical display refresh.
Use this tool to assess playback speed: exact stepping changes the normal
update/draw schedule and cannot establish real-time performance.

For aggregate update/draw CPU and named timings, opt in while loading:

```json
{"cart":"game.p8","profiling":true,
 "profile_functions":["pose","skin","renderer.draw"],"profile_sections":true}
```

Either function names or section instrumentation also enables profiling.
The ordinary bridge does not include the larger scope profiler by default.
Both profiling tools return `detail` when enabled, and accept an optional
root-contained JSON `out` path. Detailed CPU statistics cover every captured
render frame even when the stepped tool uses `sample_every` greater than 1.
The older GPIO CPU samples still saturate at 3.0; detailed aggregates do not
use that clipped representation.

Function wrappers support globals, dotted table functions, and top-level
locals visible at the end of the cart chunk. Missing functions are reported.
References captured before wrapping, functions installed later, and nested
locals cannot be intercepted this way. Wrappers preserve multiple returns,
including nils. At most 16 function/section scopes may be configured together.
For loops that call hundreds of tiny functions, bracket the loop instead of
wrapping each call: measurement overhead can materially change performance.

Add standalone comments to bracket sections; they remain harmless comments
in the release cart and are replaced only in the in-memory development copy:

```lua
-- @profile begin raster
for face in all(visible) do triangle(face) end
-- @profile end raster
```

Sections must be balanced and nested within a single update/draw callback.
Do not bracket a yield or an early return; callbacks using manual `flip()`
loops are unsupported. Spans crossing callback boundaries
are discarded and reported as invalid, rather than charged to another frame.
Initialization is excluded; a capture resets the counters at the current
paused state, without restarting the game.

`inclusiveCpu` includes nested work; `selfCpu` excludes measured children.
`selfSharePercent` uses **all captured user callback CPU** as its denominator.
Its rows plus `unattributedSharePercent` sum to 100%; inclusive times must not
be added. CPU quantities are fractions of the PICO-8 frame budget: 1.0 is
one full budget, so a frame may consume more than 100%. They are neither
normalized shares nor milliseconds. Native frame CPU also includes bridge
and profiler work; user callback totals include instrumentation within those
callbacks. CPU detail spans resume/pause command handling, while wall-rate
samples cover the requested browser-time window. Compare instrumented and
ordinary-bridge runs before treating small differences as optimizations.

## Variables

get/set support numeric, boolean or nil globals and dotted table paths
of at most 23 ASCII characters. Numbers use PICO-8's signed 16.16 range.
Setting an absent variable requires create=true. Functions/tables cannot
be replaced by set. PICO-8 has no Scratch-style sprite variable context;
entities are ordinary Lua tables.

True lexical locals require explicit closure exports, for example:
    local height=100
    p8mcp_exports={
      height={get=function() return height end,
              set=function(v) height=v end}
    }
Then the same pico8_get/pico8_set tools use name="height". Exports take
precedence over globals. Omit set for a read-only export. Our floor port
can simply use globals; local exports are optional.

The Lua bridge adds code tokens and callback overhead. Near-limit carts
may need instrument=false or a purpose-built cart-side bridge. The source
is never silently minified or altered to make room.

## Persistent Localhost Profile

The default browser profile is profiles/localhost/ inside the shared
installation directory. It is reused across roots, runs and launches.
Closing the adapter closes its own browser and HTTP server, releases its
OS process lock, and preserves the profile. profileCleanup="preserved"
reports that policy. No random browser-* folders are created.
Only one adapter may own this profile at a time. A second start returns
PROFILE_IN_USE before launching a browser. OS locks release after a crash;
if an orphan browser is still running, its debug port is detected and the
next launch asks for it to be closed. No unrelated browser is terminated.
An explicit --profile / PICO8_MCP_PROFILE_DIR override is available for
isolated tests; it is never chosen automatically as a fallback.

## Validation

GitHub Actions runs on pushes, pull requests and manual dispatches:

| Platform | Python | Account |
|---|---|---|
| Windows x64 | 3.12 | Standard local user |
| Windows ARM64 | 3.12, native ARM64 | Standard local user |
| Linux x64 | 3.12 | Non-root runner user; no sudo |
| macOS ARM64 | 3.12, native ARM64 | Non-root runner user; no sudo |

Each job runs the offline regression suite, downloads and verifies the
official runtime in a fresh user-owned location, repeats cached setup,
starts the **installed** MCP server, runs `examples/3dfloor/floor.lua`,
and checks native 128 × 128 screenshots at yaw 0° and 45°. It verifies
controller strafe, builds a native labelled `.p8.png`, validates the label,
reloads the PNG cartridge, and checks the floor launcher at 30 FPS. It also
builds and runs the four-cart Miku launcher, verifies all 15 motion checksums
and checks for underflows. Cold pure-Python compression gets a longer test
deadline; subsequent runs reuse verified cached streams. Reports and PNGs
are uploaded for seven days; runtime caches and browser profiles are
never included in those artifacts.

[GitHub-hosted Windows runners start as administrators](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges).
The Windows CI fixture creates a temporary standard account and loads its
user profile. The Python tests assert that their actual token is
non-administrative before setup or browser startup. Only account provisioning
uses the runner's administrator privileges; users of this adapter do not
need those privileges. The provisioning script refuses to run outside
GitHub Actions.

Run the checks locally using an existing Chromium browser:

```sh
python -B tests/test_adapter.py
python -B tests/ci.py --expect-non-admin
```

The full check downloads the official runtime temporarily, runs headless,
and saves reports/screenshots to the ignored `debug/ci/` directory.

## Provenance

cdp.py is adapted from gobo-agent/tools/cdp.py, MIT, copyright 2026 slyi.
LICENSE retains the MIT notice. The adapter does not import gobo-agent.
host.html uses the Education shell's public Module, input, file-drop,
frame-state and GPIO contracts. The player is downloaded unmodified.

Official runtime: https://www.pico-8-edu.com/
Manual: https://www.lexaloffle.com/dl/docs/pico-8_manual.html
MCP stdio: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
MCP tools: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
