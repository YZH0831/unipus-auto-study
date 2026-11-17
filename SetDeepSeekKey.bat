@echo off
chcp 936 >nul
title 设置大模型答题密钥
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] 请先双击 setup.bat 安装环境
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\set_key_gui.py
exit /b %ERRORLEVEL%
