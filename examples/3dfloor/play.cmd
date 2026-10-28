@echo off
setlocal
set "PICO8_COMMON_HOME=%LOCALAPPDATA%\pico8mcp"
if defined PICO8_MCP_HOME set "PICO8_COMMON_HOME=%PICO8_MCP_HOME%"
python "%PICO8_COMMON_HOME%\play.py" "%~dp0floor.lua" --telemetry floor %*
if errorlevel 1 pause
