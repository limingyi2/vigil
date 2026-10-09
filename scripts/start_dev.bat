@echo off
echo ========================================
echo   Vigil 开发环境启动
echo ========================================
echo.

cd /d F:\Vigil

echo [1/2] 启动 Brain (FastAPI)...
start "Vigil Brain" cmd /k "venv\Scripts\python.exe brain\server_launch.py"

timeout /t 3 /nobreak >nul

echo [2/2] 启动 Client (Electron Dev)...
cd client
start "Vigil Client" cmd /k "npm run dev"

echo.
echo ========================================
echo   开发环境已启动！
echo   Brain: http://127.0.0.1:8788
echo ========================================
pause
