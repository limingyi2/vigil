@echo off
echo ========================================
echo   Vigil Brain 打包脚本
echo ========================================
echo.

cd /d F:\Vigil

echo [1/1] PyInstaller 打包到独立发布目录...
venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist\brain-release --workpath build\brain-release deploy\brain\vigil-brain.spec
if errorlevel 1 exit /b 1

echo.
echo ========================================
echo   打包完成！
echo   产物: dist\brain-release\vigil-brain\vigil-brain.exe
echo ========================================
pause
