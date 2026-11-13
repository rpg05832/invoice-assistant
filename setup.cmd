@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 or newer from python.org, then run setup.cmd again.
  pause
  exit /b 1
)
py -3 -c "import sys; sys.exit(not ((3,12) <= sys.version_info[:2] < (3,15)))"
if errorlevel 1 (
  echo Python 3.12 through 3.14 is required. Install a supported Python version.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.lock
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\setup_ocr.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\setup_digits.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\setup_sequences.py
if errorlevel 1 exit /b 1
echo Setup complete. Local OCR also requires Tesseract 5 on PATH.
echo If missing, install it with: winget install --id UB-Mannheim.TesseractOCR --exact
echo Then double-click start.cmd.
pause

