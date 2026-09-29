@echo off
rem Double-click to start dictation. Click in any text box, hold Ctrl+Win, speak, let go.
cd /d "%~dp0"
uv run sst dictate
pause
