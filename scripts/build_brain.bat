@echo off
echo ========================================
echo   Vigil Brain 打包脚本
echo ========================================
echo.

cd /d F:\Vigil

echo [1/2] 清理旧打包产物...
if exist dist\vigil-brain rmdir /s /q dist\vigil-brain
if exist build rmdir /s /q build

echo [2/2] PyInstaller 打包...
venv\Scripts\python.exe -m PyInstaller --noconfirm deploy\brain\vigil-brain.spec

echo.
echo ========================================
echo   打包完成！
echo   产物: dist\vigil-brain\vigil-brain.exe
echo ========================================
pause
