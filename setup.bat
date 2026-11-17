@echo off
chcp 936 >nul
title U校园自动化助手 - 安装
cd /d "%~dp0"

echo ============================================================
echo              U校园自动化助手 - 首次安装
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 没找到 python 命令。
  echo        请先安装 Python 3.10 或更高版本，安装时勾选
  echo        "Add python.exe to PATH"，然后重开本窗口。
  echo        下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [1/3] 虚拟环境已存在，跳过。
)

echo [2/3] 升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com >nul 2>&1

echo [3/3] 安装依赖（playwright / PyYAML）...
echo       镜像1: 阿里云
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if errorlevel 1 (
  echo.
  echo [!] 阿里云镜像失败，改用腾讯云镜像重试 ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple/ --trusted-host mirrors.cloud.tencent.com
)
if errorlevel 1 (
  echo.
  echo [!] 腾讯云也失败，改用官方 PyPI ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.org/simple/
)

echo.
echo ---------------- 环境自检 ----------------
".venv\Scripts\python.exe" tools\doctor.py
echo.
echo 安装完成。以后双击 start.bat 使用。
echo.
pause
exit /b 0
