# 3D floor example

![Three-second rotating 3D floor preview](assets/preview.png)

## One Lua source, two native resolutions

`floor.lua` runs on both PICO-8 and Picotron. It detects the VM at startup
and selects the native viewport and update timing:

| VM | Render resolution | Application updates |
| --- | --- | --- |
| PICO-8 Education 0.2.7 | **128×128** | 30 Hz |
| Picotron 0.3.0d JS/WASM | **480×270** | 60 Hz |

Both use the same DDA checker floor and projected grid lines. Projection,
scanline limits and grid clipping derive from the viewport dimensions;
Picotron renders at 480×270 directly. Small platform branches handle
keyboard input, window setup and PICO-8 GPIO telemetry. Portable arithmetic
replaces PICO-8-specific 16.16 bit masks. No macro preprocessor, includes or
separate platform versions of the renderer are required.

The optional dither-colour format is currently PICO-8-only; both default to
solid grid lines. At RES 4, three extra one-pixel-offset strokes cover the
coarse checker-band edges: the default base width of two produces a
five-pixel grid. Other resolutions keep their usual width.

The launchers below start PICO-8. Picotron's JS/WASM path
has been tested separately with a local MCP proof covering scalar inspection,
variable writes, exact update stepping, controller input and screenshots.

## Run in PICO-8

Open `play.cmd`. It calls the shared PICO-8 player with `floor.lua` directly.
There is no build step or generated cartridge required for playing.

Shared installation: `%LOCALAPPDATA%\pico8mcp` on Windows;
`$XDG_DATA_HOME/pico8mcp` (normally `~/.local/share/pico8mcp`) on Linux/WSL.
On macOS, the shared installation is `~/Library/Application Support/pico8mcp`.
Python 3.12 is supported. Install or update the common player once from
this example directory:

```sh
python ../../pico8_mcp.py --setup
```

Use `python3` on POSIX. Setup downloads the official Education runtime
into the shared installation; it keeps this example directory clean.
An existing Chrome, Chromium or Edge installation is required to play.

On Linux, WSL or macOS, run:

```sh
sh ./play.sh
```

## Build a PNG cartridge

From this example directory:

```sh
python ../../pico8_mcp.py --build floor.lua
```

Use `python3` on POSIX. This creates `debug/floor.p8.png` using the native
VM, captures a label, and reloads the PNG to check that it runs. Renaming
a text `.p8` file to `.p8.png` does not convert it. This AI-assisted example
is for personal learning; see the [project's submission-policy note](../../readme.md).

## Picotron target

Run **`play.cmd -target picotron`**, or `sh play.sh -target picotron`.
Use `-target pico8` for the existing PICO-8 version; PICO-8 remains the default.
The same `floor.lua` runs in both VMs. The SDK's native loader provides
`picotron.lua` for reusable platform methods, with a direct-window fallback
when copying the floor source into Picotron manually.

The Picotron browser target defaults to **480×270, 60 Hz updates and RES 4**.
Its wider grid coverage hides checker-band protrusions during movement.
PICO-8 keeps its 128×128, 30 Hz, RES 2 defaults. Native screenshots and
profile evidence go in ignored `debug/`; no additional Picotron README
PNG is generated. The CPU tables below describe PICO-8, not Picotron.

## Controls

| Input | Action |
|---|---|
| W / S | Forward / reverse |
| A / D | Strafe |
| Left / Right | Yaw |
| Up / Down | Pitch |
| 1–4 or X | Scanline resolution |
| G | Grid on/off; automatically skipped at RES 1 |
| B | Base grid width (RES 4 adds three coverage pixels) |
| [ / ] | More / fewer visible rows |
| T | PICO-8 dithering; off by default |
| H | CPU label |
| R | Reset camera |

PICO-8 defaults: **30 FPS**, about six checker rows in the bottom half of the screen,
RES 2, solid 2-pixel grid. CPU percentages measure PICO-8's VM budget.

## PICO-8 CPU usage and drawing counts

RES 1–4 set the scanline band height in pixels; all use the same horizontal
tile-crossing DDA renderer. RES 1 draws individual pixel rows and skips the
grid. RES 2–4 draw thicker bands with a projected grid overlaid.

Remeasured after the shared-source changes in **PICO-8 Education 0.2.7** at
**128×128**, with the default view, **HUD enabled**, solid **2-pixel grid**,
and dithering off. The camera stays at `(0.25, 0.25)`, pitch 0, while yaw
rotates at **90°/second**. Each resolution gets a one-second warm-up, then
**239–240 captured frames over eight seconds** (two complete turns). CPU comes
from the cart's native `stat(1)` telemetry, without an MCP/profiler bridge.
These VM frame-budget percentages include update, view preparation, floor,
grid and HUD work.

| RES / band height | Bands per frame | Grid | Mean CPU | Peak CPU | FPS |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 64 | Off (automatic) | 9.37% | 10.07% | 30 |
| 2 | 32 | 2-pixel solid | 7.31% | 7.72% | 30 |
| 3 | 22 | 2-pixel solid | 6.09% | 6.42% | 30 |
| 4 | 16 | 5-pixel coverage | 6.52% | 6.82% | 30 |

Drawing counts are **mean / peak per rendered frame**:

| RES | Checker `rectfill()` spans | Grid segments | Grid `line()` calls |
| --- | ---: | ---: | ---: |
| 1 | 153.1 / 179 | 0.0 / 0 | 0.0 / 0 |
| 2 | 76.7 / 90 | 14.4 / 15 | 28.8 / 30 |
| 3 | 52.5 / 62 | 14.4 / 15 | 28.8 / 30 |
| 4 | 38.8 / 45 | 14.4 / 15 | 72.1 / 75 |

The checker count covers white spans; `cls(0)` supplies black tiles. Each
grid segment takes two `line()` calls at the default width, or five at RES 4
to cover protrusions from the four-pixel checker bands. The count table
excludes frame-clear and HUD calls; the CPU figures include their cost.

With the camera stationary, the cached floor is reused and mean CPU falls
to about **0.30%** at every resolution. Turning or moving redraws the floor,
so the rotating measurements are the useful comparison. RES 2 remains the
default compromise between sharpness and drawing cost. These figures are
for the PICO-8 viewport; they are not Picotron 480×270 benchmarks.

`debug/` is reserved for optional screenshots and profiling outputs, and is ignored by Git.
The example includes source files and an animated README preview; generated cartridges stay in `debug/`.
The shared browser profile is reused at `pico8mcp/profiles/localhost`.
