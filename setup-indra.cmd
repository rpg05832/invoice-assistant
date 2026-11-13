@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.cmd first. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -r requirements-ocr-extra.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\setup_indra.py
pause
