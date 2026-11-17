@echo off
chcp 936 >nul
title U校园自动化助手
rem ============================================================
rem  桌面唯一入口：双击 = 直接打开面板窗口（独立窗口，无地址栏/标签页/黑框）
rem  命令行参数：run = 直接 AI 刷课；doctor / ai / pick / list / probe ...
rem              原样透传给项目里的 start.bat
rem  真正逻辑在 tools/ui_server.py 与 start.bat，本文件只负责跳转。
rem  换项目位置只改下面 PROJ 一行。
rem  本文件由项目内 _gen_bat.py 生成，勿手改（下次生成会覆盖）。
rem ============================================================
setlocal
set "PROJ=C:\Users\YZH\WorkBuddy\2026-11-04-20-32-32\unipus-auto-study"

if not exist "%PROJ%\start.bat" (
  echo.
  echo [错误] 找不到项目目录：
  echo        %PROJ%
  echo.
  echo 解决办法：右键本文件 - 编辑，把 PROJ= 后面改成实际路径。
  echo.
  pause
  exit /b 1
)

cd /d "%PROJ%"
if errorlevel 1 (
  echo [错误] 无法进入项目目录：%PROJ%
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo [!] 还没安装运行环境（缺 .venv）。
  echo     请双击这个文件完成一次安装：
  echo     %PROJ%\setup.bat
  echo.
  pause
  exit /b 1
)

rem 双击 / ui = 打开控制面板
if "%~1"=="" goto ui
if /i "%~1"=="ui" goto ui
if /i "%~1"=="run" goto direct

rem 其它参数原样透传给 start.bat
call "%PROJ%\start.bat" %*
set "RC=%ERRORLEVEL%"
goto check

:direct
rem run = 不开面板，直接 AI 刷课（保留原来的「双击直跑」用法）
call "%PROJ%\start.bat" ai auto
set "RC=%ERRORLEVEL%"
goto check

:ui
rem 启动自检：清掉上次残留的旧刷题进程，保证用最新代码（不动画面板进程）。
if exist "tools\bat_guard.py" (
  ".venv\Scripts\python.exe" tools\bat_guard.py
)
rem 用 pythonw 起窗口：双击后直接出现面板窗口，不留黑框。
rem 面板窗口关掉时，后台服务会自己退出，不留野进程。
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" tools\ui_server.py
  exit /b 0
)
echo [!] 缺 pythonw.exe，退回控制台模式（关掉这个黑框即停止面板）
".venv\Scripts\python.exe" -u tools\ui_server.py
set "RC=%ERRORLEVEL%"

:check
rem 正常结束 / 用户主动中断 → 直接退出，不误唤起兜底
if "%RC%"=="0" goto finish
if "%RC%"=="1" goto finish
if "%RC%"=="2" goto finish
if "%RC%"=="130" goto finish
if "%RC%"=="-1073741510" goto finish
if "%RC%"=="3221225786" goto finish
echo.
echo [兜底] 入口异常退出（退出码 %RC%），正在唤起 WorkBuddy 判断...
if exist "tools\wb_helper.py" (
  ".venv\Scripts\python.exe" -u tools\wb_helper.py --rc %RC% --reason "桌面入口异常退出" --detail "入口返回退出码 %RC%"
) else (
  echo   兜底模块缺失，请在 WorkBuddy 里描述：退出码 %RC%
)
echo.
pause

:finish
endlocal & exit /b %RC%
