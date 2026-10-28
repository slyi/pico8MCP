#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${PICO8_MCP_HOME:-}" ]; then
    common_home=$PICO8_MCP_HOME
elif [ "$(uname -s)" = Darwin ]; then
    common_home="$HOME/Library/Application Support/pico8mcp"
else
    common_home="${XDG_DATA_HOME:-$HOME/.local/share}/pico8mcp"
fi
exec python3 "$common_home/play.py" "$script_dir/floor.lua" --telemetry floor "$@"
