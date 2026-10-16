@echo off
echo ========================================
echo   Vigil Client 推送更新脚本
echo ========================================
echo.
echo 用法: deploy_client.bat [版本号] [更新说明]
echo 示例: deploy_client.bat 9 "修复bug"
echo.

cd /d "%~dp0.."

set CODE=%1
set NOTES=%2

if "%CODE%"=="" (
    echo 错误: 请指定版本号
    echo 示例: deploy_client.bat 9 "修复bug"
    pause
    exit /b 1
)

if "%NOTES%"=="" set NOTES=常规更新

echo 版本号: %CODE%
echo 更新说明: %NOTES%
echo.

echo [1/3] 复制安装包...
copy /y "client\dist\Vigil Setup *.exe" "dist\client\VigilSetup.exe"

echo [2/3] 推送到云端...
venv\Scripts\python.exe deploy\client\deploy_client.py --code %CODE% --notes "%NOTES%"

echo [3/3] 验证云端版本...
if not defined VIGIL_DEPLOY_URL exit /b 1
if not defined VIGIL_DEPLOY_TOKEN exit /b 1
venv\Scripts\python.exe -c "import os,requests; r=requests.get(os.environ['VIGIL_DEPLOY_URL'].rstrip('/')+'/api/client/version', headers={'Authorization':'Bearer '+os.environ['VIGIL_DEPLOY_TOKEN']}, timeout=10); r.raise_for_status(); print(r.text)"

echo.
echo ========================================
echo   推送完成！
echo ========================================
pause
