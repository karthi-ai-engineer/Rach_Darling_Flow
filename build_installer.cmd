@echo off
rem Build dist\SST-Dictation-Setup-<version>.exe (see scripts\build_installer.py).
rem Uses its own environment, build\venv, so it also works while sst is running from .venv.
cd /d "%~dp0"
set UV_PROJECT_ENVIRONMENT=build\venv
uv run --group build python scripts\build_installer.py %*
if errorlevel 1 pause
