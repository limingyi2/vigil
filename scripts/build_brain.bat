@echo off
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "BUILD_PYTHON=python"
if exist "%ROOT%\venv\Scripts\python.exe" set "BUILD_PYTHON=%ROOT%\venv\Scripts\python.exe"
if exist "%ROOT%\.venv\Scripts\python.exe" set "BUILD_PYTHON=%ROOT%\.venv\Scripts\python.exe"
"%BUILD_PYTHON%" "%ROOT%\scripts\build_release.py" --brain-only %*
exit /b %errorlevel%
