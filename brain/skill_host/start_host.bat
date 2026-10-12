@echo off
rem Skill host launcher.
rem
rem ASCII only. cmd reads this file byte by byte and a multi-byte character
rem splits the line, so no Chinese in here even though the rest of the project
rem is full of it. CRLF line endings for the same reason: the copy that shipped
rem LF-only and cmd was none too happy about it.
rem
rem Paths are derived from this file's own location so the same bat works in
rem the repo and on the box. Hardcoding C:\vigil meant the local copy could
rem never be used to test anything.
rem
rem Restart the host when server.js is NEWER than the running one.
rem
rem The first version was "is the port in use? then do nothing" -- and that made
rem every code change a lie. Push a fixed server.js, restart the brain, and the
rem old host is still there answering on 8790 with the old code:
rem     files on disk    mtime 213s ago  (has the fix)
rem     host process     created 22:39:27 (does not)
rem "the sha1 verified" and "the change is live" are different statements and
rem only the second one matters. The symptom is a fix that deploys cleanly,
rem restarts cleanly, and changes nothing.
rem
rem How staleness is decided WITHOUT powershell: copying a file to a name gives
rem that copy the SOURCE's timestamp, so `copy /b server.js stamp.bin` followed
rem by comparing... no, cmd cannot compare times either. So use the one thing
rem cmd can do: let the DEPLOY delete the stamp. The stamp only exists to say
rem "a host was started after the last deploy", and the deploy already writes
rem every file it touches.
rem
rem Simpler contract, no comparison needed:
rem     deploy  -> deletes .host_started
rem     boot    -> no stamp means "no live host of this build", so start one
rem A hand-started host still gets skipped, which is what we want, and after any
rem deploy the next brain restart necessarily starts the new code.
rem Set ZX_SKILL_FORCE=1 to restart even when one is running.
setlocal

if not defined ZX_SKILL_AUTH_TOKEN (
    echo [skill-host] ZX_SKILL_AUTH_TOKEN is required; refusing to start without authentication
    exit /b 1
)

set "ZX_SKILL_DIR=%~dp0skills"
if "%ZX_SKILL_DIR:~-1%"=="\" set "ZX_SKILL_DIR=%ZX_SKILL_DIR:~0,-1%"
cd /d "%~dp0"

if not exist "%ZX_SKILL_DIR%" mkdir "%ZX_SKILL_DIR%"
if not exist "C:\vigil\logs" mkdir "C:\vigil\logs" 2>NUL

set "ZX_PORT=8790"
set "ZX_STAMP=%~dp0.host_started"

rem --- who is on the port, and is this build the one they are running? -----
set "ZX_BUSY="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%ZX_PORT% "') do set "ZX_BUSY=%%P"

if not defined ZX_BUSY goto :start_it

rem Someone is listening. If the stamp is gone the deploy wiped it, so what is
rem running predates the current files -> replace it.
if not exist "%ZX_STAMP%" goto :kill_it
if "%ZX_SKILL_FORCE%"=="1" goto :kill_it

echo [skill-host] port %ZX_PORT% in use and already current, leaving it alone
exit /b 0

:kill_it
echo [skill-host] replacing the host on port %ZX_PORT% with the current build
rem Kill by PID from netstat, never by image name: there are other node
rem processes on this box and blanket-killing by name takes them out too.
if defined ZX_BUSY taskkill /F /PID %ZX_BUSY% >NUL 2>&1
rem Wait for the socket to actually go away. A reported kill is not a released
rem port -- the next start would fail to bind and the brain would report the
rem host as dead for no visible reason.
for /l %%I in (1,1,10) do (
    set "ZX_STILL="
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%ZX_PORT% "') do set "ZX_STILL=%%P"
    if not defined ZX_STILL goto :start_it
    ping -n 2 127.0.0.1 >NUL
)

:start_it
set "ZX_SKILL_DIR=%ZX_SKILL_DIR%"
set "ZX_SKILL_PORT=%ZX_PORT%"
set "ZX_SKILL_HOST=127.0.0.1"

rem Stamp BEFORE starting so a slow start cannot be read as "still no stamp"
rem and cause a restart loop on the next brain boot.
echo started> "%ZX_STAMP%"

rem Detached on purpose: the brain's launcher must not wait on this, and if the
rem brain is restarted the host has to survive independently of it.
start "" /b "C:\Program Files\nodejs\node.exe" server.js >> "C:\vigil\logs\skill_host.log" 2>&1
echo [skill-host] started
exit /b 0
