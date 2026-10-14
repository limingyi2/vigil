@echo off
setlocal
rem Local Management Agent launcher. Credentials must be injected by the
rem process environment or an approved secret store; never put them in this file.

if not defined VIGIL_MANAGEMENT_URL (
  echo [x] Set VIGIL_MANAGEMENT_URL in the process environment.
  exit /b 1
)
if not defined VIGIL_MANAGEMENT_TOKEN (
  echo [x] Set VIGIL_MANAGEMENT_TOKEN through an approved secret store.
  exit /b 1
)
if not defined VIGIL_MANAGEMENT_NODE_ID set "VIGIL_MANAGEMENT_NODE_ID=local-pc"
if not defined VIGIL_MANAGEMENT_INTERVAL set "VIGIL_MANAGEMENT_INTERVAL=15"

pushd "%~dp0brain" || exit /b 1
echo [Vigil] Starting Management Agent: node=%VIGIL_MANAGEMENT_NODE_ID%
echo [Vigil] Close this window to stop reporting.
python management_agent.py
set "AGENT_EXIT=%ERRORLEVEL%"
popd
exit /b %AGENT_EXIT%
