@echo off
rem ---------------------------------------------------------------
rem Colorink - emergency hotkey reset
rem
rem Use when a mouse-button hotkey (e.g. right click) makes the tray
rem menu / settings unreachable. Reinstalling does NOT help, because
rem the settings live in %APPDATA%\Colorink and survive reinstall.
rem
rem This script is NON-DESTRUCTIVE: it only renames the config file
rem to hotkey-config.json.bak. Nothing is deleted.
rem ---------------------------------------------------------------
setlocal
chcp 65001 >nul 2>&1

echo =============================================================
echo   Colorink - Emergency hotkey reset
echo =============================================================
echo.
echo   This will stop Colorink and rename your hotkey config to a
echo   backup file. Your old settings stay in that backup.
echo.
echo   After this, Colorink starts with all DEFAULT settings.
echo.
pause

echo.
echo [1/3] Stopping Colorink ...
taskkill /IM Colorink.exe /F >nul 2>&1
if errorlevel 1 (
  echo       Colorink was not running.
) else (
  echo       Stopped.
)

echo [2/3] Locating config ...
set "CFGDIR=%APPDATA%\Colorink"
set "CFG=%CFGDIR%\hotkey-config.json"

if not exist "%CFG%" (
  echo       Not found: %CFG%
  echo.
  echo       Nothing to reset. If Colorink still misbehaves, the
  echo       config may live elsewhere - check with the author.
  echo.
  pause
  exit /b 1
)
echo       Found: %CFG%

echo [3/3] Renaming to backup ...
if exist "%CFG%.bak" del /F /Q "%CFG%.bak" >nul 2>&1
ren "%CFG%" "hotkey-config.json.bak" >nul 2>&1

if exist "%CFG%" (
  echo.
  echo   FAILED - the config is locked or read-only.
  echo   Please rename this file manually:
  echo     %CFG%
  echo   ^(make sure Colorink is not running, then try again^)
) else (
  echo.
  echo   DONE.
  echo.
  echo   Backup saved as:
  echo     %CFG%.bak
  echo.
  echo   Start Colorink again - every hotkey is back to default:
  echo     pick color ....... Ctrl + Alt + Q
  echo     hide window ...... Ctrl + Alt + Y
  echo     follow mouse ..... Ctrl + Alt + J
  echo     title bar ........ Ctrl + Alt + K
  echo     grayscale ........ Ctrl + Alt + D
  echo.
  echo   Keep at least one KEYBOARD hotkey bound. Binding "hide
  echo   window" to a bare mouse button can lock you out again.
)

echo.
pause
exit /b 0
