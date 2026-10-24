@echo off
setlocal
set "ROOT=%~dp0"
set "VSROOT="
if not defined VSROOT (
  for %%E in (Enterprise Professional Community BuildTools) do (
    if not defined VSROOT if exist "%ProgramFiles%\Microsoft Visual Studio\2022\%%E\Common7\Tools\VsDevCmd.bat" set "VSROOT=%ProgramFiles%\Microsoft Visual Studio\2022\%%E"
    if not defined VSROOT if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\%%E\Common7\Tools\VsDevCmd.bat" set "VSROOT=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\%%E"
  )
)
if not defined VSROOT set "VSROOT=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools"
if not exist "%VSROOT%\Common7\Tools\VsDevCmd.bat" (
  echo Visual Studio 2022 with the MSVC x64 C++ tools was not found. 1>&2
  exit /b 2
)
if not exist "%ROOT%build\Release" mkdir "%ROOT%build\Release"
call "%VSROOT%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul
if errorlevel 1 exit /b %errorlevel%
cl.exe /nologo /std:c++17 /EHsc /W4 /utf-8 /D_WIN32_WINNT=0x0A00 /Fo"%ROOT%build\Release\native_window_host.obj" /Fe"%ROOT%build\Release\native-window-host.exe" "%ROOT%native_window_host.cpp" /link user32.lib shell32.lib
exit /b %errorlevel%
