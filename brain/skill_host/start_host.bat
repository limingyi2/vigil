@echo off
rem Skill host launcher.
rem
rem ASCII only. cmd reads this file byte by byte and a multi-byte character
rem splits the line, so no Chinese in here even though the rest of the project
rem is full of it. CRLF line endings for the same reason: the copy that shipped
rem LF-only and cmd was none too happy about it.
rem
rem Paths are derived from this file's own location so the same bat works in
rem the repo and on the box. Hardcoding C:\Vigil meant the local copy could
rem never be used to test anything.
rem
rem Idempotent by design: the brain's launcher calls this on every boot, and
rem you restart the brain more than once. Two node processes both binding 8790
rem means the second one throws and the first one's answers start interleaving
rem with a dead socket -- so check the port first and skip if it answers.
setlocal

set "ZX_SKILL_DIR=%~dp0skills"
if "%ZX_SKILL_DIR:~-1%"=="\" set "ZX_SKILL_DIR=%ZX_SKILL_DIR:~0,-1%"
cd /d "%~dp0"

if not exist "%ZX_SKILL_DIR%" mkdir "%ZX_SKILL_DIR%"
if not exist "C:\Vigil\logs" mkdir "C:\Vigil\logs" 2>NUL

rem --- already up? then do nothing ------------------------------------------
rem findstr on netstat output is the portable way in plain cmd. Empty result
rem means nothing is listening, which is the case we want to start.
set "ZX_PORT=8790"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%ZX_PORT% "') do set "ZX_BUSY=1"

if defined ZX_BUSY (
    echo [skill-host] port %ZX_PORT% already in use, not starting a second copy
    exit /b 0
)

set "ZX_SKILL_DIR=%ZX_SKILL_DIR%"
set "ZX_SKILL_PORT=%ZX_PORT%"
set "ZX_SKILL_HOST=127.0.0.1"

rem Detached on purpose: the brain's launcher must not wait on this, and if the
rem brain is restarted the host has to survive independently of it.
start "" /b "C:\Program Files\nodejs\node.exe" server.js >> "C:\Vigil\logs\skill_host.log" 2>&1
echo [skill-host] started
exit /b 0
