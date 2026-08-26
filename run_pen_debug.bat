@echo off
rem 带着笔悬停调试输出启动 Colorink（仅排查笔光标问题用）
cd /d "%~dp0"
set COLORINK_DEBUG_PEN=1
python main.py
pause
