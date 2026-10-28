# PICO-8 Education MCP / 0.3.0

[![Cross-platform tests](https://github.com/slyi/pico8MCP/actions/workflows/test.yml/badge.svg)](https://github.com/slyi/pico8MCP/actions/workflows/test.yml)

A standalone, agent-friendly Python MCP adapter for the official PICO-8
Education Edition. Uses one warm Chromium browser and a localhost-only
Python HTTP server. Python 3.10+; standard library only, no pip packages.

## Example

[3D floor](examples/3dfloor/readme.md) contains `floor.lua`, `play.cmd`,
`play.sh` and `readme.md`. Run setup below once, then launch the example.
Its optional outputs belong in the ignored `examples/3dfloor/debug/` folder.

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
with PICO-8's native Ctrl+7 label shortcut (Cmd+7 on macOS), and executes native `save`.
It then reloads and runs the generated PNG to verify the cartridge.
It replaces the current cart and leaves the generated cart running.
CLI builds close their owned browser when complete.

This is a real 160 × 205 PNG cartridge with embedded game data and label,
not a renamed text file or screenshot. It can be uploaded to the
Lexaloffle cartridge form. Input and output paths must stay within `--root`.
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

setup / doctor / start / load / run / build / state / pause / resume / step /
buttons / wait_frames / get / set / gpio / screenshot / profile / logs /
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
reloads the PNG cartridge, and checks the platform launcher at 30 FPS. Reports and PNGs
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
