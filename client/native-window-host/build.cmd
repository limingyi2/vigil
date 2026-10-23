@echo off
setlocal
set "ROOT=%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VSROOT="
if exist "%VSWHERE%" (
  for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -version "[17.0,18.0)" -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
)
if not defined VSROOT set "VSROOT=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools"
if not exist "%VSROOT%\Common7\Tools\VsDevCmd.bat" (
  echo Visual Studio 2022 with the MSVC x64 C++ tools was not found. 1>&2
  exit /b 2
)
if not exist "%ROOT%build\Release" mkdir "%ROOT%build\Release"
call "%VSROOT%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul
if errorlevel 1 exit /b %errorlevel%
cl.exe /nologo /std:c++17 /EHsc /W4 /D_WIN32_WINNT=0x0A00 /Fo"%ROOT%build\Release\native_window_host.obj" /Fe"%ROOT%build\Release\native-window-host.exe" "%ROOT%native_window_host.cpp" /link user32.lib shell32.lib
exit /b %errorlevel%
