@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.cmd first. & pause & exit /b 1)
.venv\Scripts\python.exe scripts\run_vision.py --profile multilingual
if errorlevel 1 pause
