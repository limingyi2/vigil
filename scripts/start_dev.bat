@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "DEV_PYTHON=python"
if exist "%ROOT%\venv\Scripts\python.exe" set "DEV_PYTHON=%ROOT%\venv\Scripts\python.exe"
if exist "%ROOT%\.venv\Scripts\python.exe" set "DEV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
"%DEV_PYTHON%" "%ROOT%\scripts\build_release.py" --verify --candidate >nul 2>&1
if errorlevel 1 (
  "%DEV_PYTHON%" "%ROOT%\scripts\build_release.py" --candidate --brain-only
  if errorlevel 1 exit /b 1
)
set "VIGIL_USER_DATA=%ROOT%\.vigil-dev"
cd /d "%ROOT%\client"
call npm start
exit /b %errorlevel%
