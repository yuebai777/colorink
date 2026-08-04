@echo off
setlocal
set "SRC=%~dp0"
set "OUT=%SRC%build"
if not exist "%OUT%" mkdir "%OUT%"
set "MSVC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.42.34433"
set "SDK=C:\Program Files (x86)\Windows Kits\10"
for /f "delims=" %%i in ('dir /b /on "%SDK%\Include" 2^>nul') do set "SDKVER=%%i"
set "INCLUDE=%MSVC%\include;%SDK%\Include\%SDKVER%\um;%SDK%\Include\%SDKVER%\shared;%SDK%\Include\%SDKVER%\ucrt;%SDK%\Include\%SDKVER%\cppwinrt;%SDK%\Include\%SDKVER%\winrt"
set "LIB=%MSVC%\lib\x64;%SDK%\Lib\%SDKVER%\um\x64;%SDK%\Lib\%SDKVER%\ucrt\x64"
set "PATH=%MSVC%\bin\Hostx64\x64;%PATH%"
echo Compiling overlay (single-process WGC capture + tear-free present)...
rem CONSOLE subsystem: on this machine the DWM throttles fullscreen presents of
rem GUI-subsystem processes to ~20Hz but lets console-subsystem processes run at
rem full refresh (measured: 20 vs 60fps). CREATE_NO_WINDOW hides the console.
rem windowsapp.lib (WinRT/WGC activation) comes in via #pragma comment.
cl.exe /nologo /std:c++17 /O2 /EHsc /MT /W3 /Fe:"%OUT%\dcomp_overlay.exe" "%SRC%main.cpp" d3d11.lib d3dcompiler.lib dxgi.lib user32.lib dxguid.lib /link /SUBSYSTEM:CONSOLE /ENTRY:WinMainCRTStartup /MACHINE:X64 2>&1
if exist "%OUT%\dcomp_overlay.exe" (echo BUILD SUCCESS: %OUT%\dcomp_overlay.exe) else (echo BUILD FAILED & exit /b 1)
