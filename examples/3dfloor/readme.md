# 3D floor example

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

## Build for upload or sharing

From this example directory:

```sh
python ../../pico8_mcp.py --build floor.lua
```

Use `python3` on POSIX. This creates `debug/floor.p8.png` using the native
VM, captures a label, and reloads the PNG to check that it runs. Upload
this `.p8.png` file to the Lexaloffle cartridge form; renaming a text
`.p8` file to `.p8.png` does not convert it.

## Controls

| Input | Action |
|---|---|
| W / S | Forward / reverse |
| A / D | Strafe |
| Left / Right | Yaw |
| Up / Down | Pitch |
| 1–4 or X | Scanline resolution |
| G | Grid on/off; automatically skipped at RES 1 |
| B | Grid width |
| [ / ] | More / fewer visible rows |
| T | Dithering; off by default |
| H | CPU label |
| R | Reset camera |

Defaults: **30 FPS**, about six checker rows in the bottom half of the screen,
RES 2, solid 2-pixel grid. CPU percentages measure PICO-8's VM budget.

`debug/` is reserved for optional screenshots and profiling outputs, and is ignored by Git.
Only the four project files are included; there is no generated cartridge or build script.
The shared browser profile is reused at `pico8mcp/profiles/localhost`.
