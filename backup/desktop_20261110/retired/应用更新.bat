@echo off
chcp 936 >nul
title 应用 U校园助手更新
cd /d "C:\Users\YZH\WorkBuddy\2026-11-04-20-32-32\unipus-auto-study"
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 运行环境缺失，请先双击 setup.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" _gen_bat.py --apply
echo.
pause
