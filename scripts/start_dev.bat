@echo off
setlocal
echo ========================================
echo   Vigil development environment
echo ========================================
echo.

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PYTHON=python"
if exist "%ROOT%\venv\Scripts\python.exe" set "PYTHON=%ROOT%\venv\Scripts\python.exe"

if not exist "%ROOT%\brain\server_launch.py" (
  echo [x] Brain launcher not found under "%ROOT%\brain".
  exit /b 1
)
if not exist "%ROOT%\client\package.json" (
  echo [x] Electron client not found under "%ROOT%\client".
  exit /b 1
)

echo [1/2] Starting Brain...
start "Vigil Brain" cmd /k ""%PYTHON%" "%ROOT%\brain\server_launch.py""

timeout /t 3 /nobreak >nul

echo [2/2] Starting Electron client...
start "Vigil Client" cmd /k "cd /d ""%ROOT%\client"" && npm start"

echo.
echo Started Brain and Electron from "%ROOT%".
echo Brain selects an available local port and reports it in its window.
