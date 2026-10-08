@echo off
rem Cloud brain launcher. Started by the VigilBrain scheduled task.
rem
rem ASCII only and CRLF, because cmd. Everything the box needs is spelled out
rem here rather than assumed, since this file is the one that has to work when
rem nobody is watching.
rem
rem The skill host is started from HERE, not from its own scheduled task.
rem Reason: the two must come up together. A separate ONSTART task would race
rem this one -- the brain could finish loading plugins and find the host still
rem down, print "host not alive", and then never look again. Inside this file
rem the host is up before server.py is even invoked, so the first thing the
rem brain does is find it.
rem
rem The host launcher is idempotent (it checks port 8790 first), so running
rem this script repeatedly is safe.
chcp 65001 >NUL
timeout /t 20 /nobreak >NUL
cd /d C:\Vigil
title LinZhixia Server
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set BRAIN_MUTEX=Global\VigilBrainSingleInstance
if not exist C:\Vigil\logs mkdir C:\Vigil\logs

rem --- skill host first, so the brain loads with skills available -----------
call C:\Vigil\skill_host\start_host.bat

C:\Vigil\.venv\Scripts\python.exe server.py > C:\Vigil\logs\cloud_live.log 2>&1
echo [LinZhixia server exited]
