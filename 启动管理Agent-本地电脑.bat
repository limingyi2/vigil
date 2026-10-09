@echo off
REM ==================================================
REM  Vigil Management Agent - local PC
REM  Reports this machine heartbeat to cloud server A
REM  Keep this window open; close it to stop reporting
REM ==================================================
set VIGIL_MANAGEMENT_URL=http://203.0.113.10:8788
set VIGIL_MANAGEMENT_NODE_ID=local-pc
set VIGIL_MANAGEMENT_TOKEN=REPLACE_WITH_PRIVATE_TOKEN
set VIGIL_MANAGEMENT_INTERVAL=15
cd /d F:\Vigil\brain
echo [Vigil] Starting management agent: node=local-pc
echo [Vigil] Close this window to stop reporting.
python management_agent.py
pause
