@echo off
chcp 936 >nul
title U校园自动化助手
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] 请先双击 setup.bat 安装环境
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py --config config.yaml %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
