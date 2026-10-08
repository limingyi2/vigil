@echo off
rem ============================================================
rem  Vigil client launcher (fallback)
rem
rem  Double-click this file to start the client. It exists so that
rem  there is always a way in, even if the desktop shortcut is gone.
rem
rem  It runs the Electron inside node_modules, pointed at this folder
rem  -- i.e. the current source, not the packaged snapshot in dist/.
rem
rem  ELECTRON_RUN_AS_NODE is cleared on purpose: some terminals and
rem  sandboxes export it, and with it set Electron runs as plain Node
rem  and dies with "Cannot read properties of undefined (reading 'handle')".
rem ============================================================
set ELECTRON_RUN_AS_NODE=
cd /d "%~dp0"
if not exist "node_modules\electron\dist\electron.exe" (
  echo [x] node_modules\electron is missing. Run: npm install
  pause
  exit /b 1
)
start "" "node_modules\electron\dist\electron.exe" .
