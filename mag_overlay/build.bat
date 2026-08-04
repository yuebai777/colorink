@echo off
setlocal
set "SRC=%~dp0"
set "OUT=%SRC%build"
if not exist "%OUT%" mkdir "%OUT%"
set "MSVC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.42.34433"
set "SDK=C:\Program Files (x86)\Windows Kits\10"
for /f "delims=" %%i in ('dir /b /on "%SDK%\Include" 2^>nul') do set "SDKVER=%%i"
set "INCLUDE=%MSVC%\include;%SDK%\Include\%SDKVER%\um;%SDK%\Include\%SDKVER%\shared;%SDK%\Include\%SDKVER%\ucrt"
set "LIB=%MSVC%\lib\x64;%SDK%\Lib\%SDKVER%\um\x64;%SDK%\Lib\%SDKVER%\ucrt\x64"
set "PATH=%MSVC%\bin\Hostx64\x64;%PATH%"
echo Compiling...
if exist "%OUT%\mag_filter.exe" del "%OUT%\mag_filter.exe"
cl.exe /nologo /std:c++17 /O2 /EHsc /MT /W3 /Fe:"%OUT%\mag_filter.exe" "%SRC%main.cpp" user32.lib /link /SUBSYSTEM:WINDOWS /MACHINE:X64 2>&1
if exist "%OUT%\mag_filter.exe" (echo BUILD SUCCESS: %OUT%\mag_filter.exe) else (echo BUILD FAILED & exit /b 1)
