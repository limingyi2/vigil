@echo off
rem Skill host launcher. ASCII only -- cmd reads this file byte by byte and
rem a multi-byte character splits the line (see the bat/cmd note in memory).
rem Single quotes on the paths would be wrong here: this is cmd, not python.
setlocal
cd /d C:\Vigil\skill_host
set ZX_SKILL_DIR=C:\Vigil\skill_host\skills
set ZX_SKILL_PORT=8790
set ZX_SKILL_HOST=127.0.0.1
"C:\Program Files\nodejs\node.exe" server.js >> C:\Vigil\logs\skill_host.log 2>&1
