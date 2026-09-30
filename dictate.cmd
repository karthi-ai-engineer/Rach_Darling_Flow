@echo off
rem Double-click to start dictation. Click in any text box, press Ctrl+Alt+D, speak, press it again.
cd /d "%~dp0"
uv run sst dictate
pause
