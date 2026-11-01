@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Please install Python 3.10+ from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Open http://127.0.0.1:8787 in your browser.
.venv\Scripts\python.exe app.py
pause
exit /b
:failed
echo Setup failed. Check Python and your internet connection, then try again.
pause
exit /b 1
