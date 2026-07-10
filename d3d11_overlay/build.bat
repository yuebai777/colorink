@echo off
REM build.bat — compile d3d11_overlay.exe with MSVC
REM Requires Visual Studio with C++ tools + Windows SDK

setlocal
set "SRC=%~dp0"
set "OUT=%SRC%build"
if not exist "%OUT%" mkdir "%OUT%"

REM Set up MSVC environment
set "MSVC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.42.34433"
set "SDK=C:\Program Files (x86)\Windows Kits\10"

REM Find latest SDK version
for /f "delims=" %%i in ('dir /b /on "%SDK%\Include" 2^>nul') do set "SDKVER=%%i"
set "INCLUDE=%MSVC%\include;%SDK%\Include\%SDKVER%\um;%SDK%\Include\%SDKVER%\shared;%SDK%\Include\%SDKVER%\ucrt"
set "LIB=%MSVC%\lib\x64;%SDK%\Lib\%SDKVER%\um\x64;%SDK%\Lib\%SDKVER%\ucrt\x64"
set "PATH=%MSVC%\bin\Hostx64\x64;%PATH%"

echo Compiling...
cl.exe /nologo /std:c++17 /O2 /EHsc /MT /W3 /Fe:"%OUT%\d3d11_overlay.exe" "%SRC%main.cpp" ^
    d3d11.lib d3dcompiler.lib dxgi.lib dxguid.lib user32.lib /link /SUBSYSTEM:WINDOWS /MACHINE:X64 2>&1

if exist "%OUT%\d3d11_overlay.exe" (
    echo.
    echo BUILD SUCCESS: %OUT%\d3d11_overlay.exe
) else (
    echo BUILD FAILED
    exit /b 1
)
