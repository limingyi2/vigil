@echo off
REM ============================================================
REM  Zhixia tunnel: keep127.0.0.1:18787 -> brain:8788 alive.
REM  The brain API port is not open to the public, so the local
REM  bridge needs this tunnel. Started detached, survives reboots
REM  only if you add it to Task Scheduler yourself (see run_tunnel.ps1).
REM  Keep this window OPEN while running -- closing it stops the tunnel.
REM ============================================================
cd /d "F:\Vigil"

echo Starting tunnel 127.0.0.1:18787 -> 203.0.113.10:8788 ...

start "Zhixia tunnel" /min "%SystemRoot%\System32\OpenSSH\ssh.exe" ^
  -i "F:\Vigil\deploy\sshkeys\linzhixia_deploy" ^
  -o StrictHostKeyChecking=no ^
  -o ServerAliveInterval=15 ^
  -o ServerAliveCountMax=3 ^
  -o ExitOnForwardFailure=yes ^
  -N -L 18787:127.0.0.1:8788 ^
  administrator@203.0.113.10

echo Waiting for the tunnel...
set /a T=0
:WAIT
timeout /t 2 /nobreak >nul
set /a T+=1
.venv-wx\Scripts\python.exe -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',18787))==0 else 1)"
if %T% lss 10 goto WAIT
echo   Tunnel is up.
echo.
echo   Now run ZhixiaSelfTest.bat in another window.
echo   OR: just run the bridge directly:
echo     .venv-wx\Scripts\python.exe brain\wechat_bot\wxauto_bridge.py --with "??????"
echo.
echo   Close this window to stop the tunnel.
echo.
pause