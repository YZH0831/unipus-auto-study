@echo off
chcp 936 >nul
title U校园自动化助手
rem ============================================================
rem  桌面入口（薄封装）：双击 = 直接 AI 刷课；带参数 = 透传给 start.bat
rem  真正的逻辑都在项目里的 start.bat，本文件只负责"跳过去"。
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
  echo 可能原因：项目被移动/改名，或者该磁盘没连上。
  echo 解决办法：右键本文件 - 编辑，把 PROJ= 后面改成实际路径，保存后再双击。
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

rem 双击（无参）= AI 直跑 60 分钟 / 双遍补满分
rem 带参数 = 透传（例：U校园自动化助手.bat doctor / ai / pick）
set "ARGS=%*"
if "%~1"=="" set "ARGS=ai auto"
call "%PROJ%\start.bat" %ARGS%
set "RC=%ERRORLEVEL%"

rem 正常结束 / 用户主动中断 / 常见系统中断 → 直接退出，不误唤起兜底
if "%RC%"=="0" goto done
if "%RC%"=="1" goto done
if "%RC%"=="2" goto done
if "%RC%"=="130" goto done
if "%RC%"=="-1073741510" goto done
if "%RC%"=="3221225786" goto done
if "%RC%"=="9009" goto nocmd
echo.
echo [兜底] 程序没能跑完（退出码 %RC%），正在唤起 WorkBuddy 判断...
if exist "tools\wb_helper.py" (
  ".venv\Scripts\python.exe" -u tools\wb_helper.py --rc %RC% --reason "启动器检测到异常退出" --detail "桌面入口调用 start.bat 返回退出码 %RC%"
) else (
  echo   兜底模块缺失，请在 WorkBuddy 里描述：退出码 %RC%
)
echo.
pause
goto done

:nocmd
echo.
echo [错误] 命令未找到（9009）：请检查 .venv 与 start.bat 是否完整。
echo        可先双击 %PROJ%\setup.bat 重新装一次环境。
echo.
pause

:done
endlocal & exit /b %RC%
