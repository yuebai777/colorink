@echo off
rem Colorink 测试向导启动器 —— 双击本文件即可使用
chcp 65001 >nul
cd /d "%~dp0"
python tools\test_wizard.py -h
echo.
echo ──────────────────────────────────────────
echo  直接回车 = 一条龙测试（推荐，改完代码后用这个）
echo  输入 auto    = 只跑自动测试（约 1 分钟，不用你动手）
echo  输入 manual  = 只打勾手测（上次测到哪接着来）
echo  输入 report  = 只出报告
echo  输入 panel   = 开网页版打勾面板（浏览器操作）
echo  输入 q       = 退出
echo ──────────────────────────────────────────
set /p WHAT=要跑什么？ 
if "%WHAT%"=="" set WHAT=all
if /i "%WHAT%"=="q" goto end
python tools\test_wizard.py %WHAT%
:end
echo.
pause
