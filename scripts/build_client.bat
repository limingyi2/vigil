@echo off
echo ========================================
echo   Vigil Client 打包脚本
echo ========================================
echo.

cd /d F:\Vigil\client

echo [1/1] electron-builder 打包...
call npm run dist

echo.
echo ========================================
echo   打包完成！
echo   产物: client\dist\
echo ========================================
pause
