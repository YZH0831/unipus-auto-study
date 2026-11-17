# -*- coding: utf-8 -*-
"""生成 .bat 启动脚本（GBK + CRLF，避免 cmd 中文乱码）。

单独跑：python _gen_bat.py
"""
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _write(path: Path, text: str):
    """按 GBK 编码 + CRLF 换行写 bat（cmd 中文不乱码的必要条件）。"""
    norm = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = norm.encode("gbk", errors="replace")
    path.write_bytes(data)
    print(f"  wrote {path.name}  ({len(data)} bytes)  -> {path}")


def w(name, text):
    _write(ROOT / name, text)


def patch_start(text: str) -> str:
    """给每条 `set /p` 前补一行清空变量（幂等）。

    cmd 的 set /p 在直接回车时**不清空变量**，会沿用上一次输入：
    菜单里第二次直接回车会重复执行上一个选项，分钟数/遍数也会沿用旧值。
    :relogin 分支没跑过 Python，RC 是空值，exit /b %RC% 也会变成怪值。
    """
    out = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"\s*set\s+/p\s+([A-Za-z_]\w*)\s*=", ln, re.I)
        if m:
            guard = f'set "{m.group(1)}="'
            if not out or out[-1].strip().lower() != guard.lower():
                indent = ln[:len(ln) - len(ln.lstrip())]
                out.append(indent + guard)
        out.append(ln)
    patched = "\n".join(out)
    patched = patched.replace(
        'if "%DIRECT%"=="1" exit /b %RC%\ngoto menu\n\n:setkey',
        'if "%DIRECT%"=="1" exit /b 0\ngoto menu\n\n:setkey', 1)
    return patched


def study_running() -> bool:
    """最近 90 秒内有日志写入 = 刷课还在跑。

    cmd 是按字节偏移逐行读 .bat 的，运行中改写文件会让那个窗口
    从错误偏移继续执行命令（甚至可能再拉起一次刷课）。所以覆盖前先查。
    """
    try:
        logs = sorted((ROOT / "logs").glob("run_*.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        return bool(logs) and (time.time() - logs[0].stat().st_mtime) < 90
    except Exception:  # noqa: BLE001
        return False


def desktop_dir() -> Path | None:
    d = Path.home() / "Desktop"
    return d if d.is_dir() else None


# ---------------------------------------------------------------- setup.bat
SETUP = r"""@echo off
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
"""

# ---------------------------------------------------------------- start.bat
# 【已废弃】这是 11-08 之前的历史菜单模板，缺双遍满分 / 选单元 / aiauto。
# 现网 start.bat 独立维护（含大量后续修复），生成器只对其做 patch_start() 修补，
# 绝不能用本模板覆盖，否则功能直接回退。保留仅供追溯。
START = r"""@echo off
chcp 936 >nul
title U校园自动化助手
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [!] 还没安装运行环境。
  echo     请先双击 setup.bat 完成一次安装。
  echo.
  pause
  exit /b 1
)

set "DIRECT=0"
if /i "%~1"=="run"    set "DIRECT=1"
if /i "%~1"=="dry"    set "DIRECT=1"
if /i "%~1"=="quiz"   set "DIRECT=1"
if /i "%~1"=="video"  set "DIRECT=1"
if /i "%~1"=="pick"   set "DIRECT=1"
if /i "%~1"=="list"   set "DIRECT=1"
if /i "%~1"=="key"    set "DIRECT=1"
if /i "%~1"=="ai"     set "DIRECT=1"
if /i "%~1"=="api"    set "DIRECT=1"
if /i "%~1"=="doctor" set "DIRECT=1"
if /i "%~1"=="probe"  set "DIRECT=1"

if /i "%~1"=="run"    goto run
if /i "%~1"=="dry"    goto dry
if /i "%~1"=="quiz"   goto quiz
if /i "%~1"=="video"  goto vid
if /i "%~1"=="pick"   goto pick
if /i "%~1"=="list"   goto list
if /i "%~1"=="key"    goto setkey
if /i "%~1"=="ai"     goto aistudy
if /i "%~1"=="api"    goto apirun
if /i "%~1"=="doctor" goto doctor
if /i "%~1"=="probe"  goto probe

:menu
set "VER="
if exist "VERSION" set /p VER=<VERSION
if not defined VER set "VER=1.0.0"
set "LLMSTAT="
set "ST="
set "MODEL="
set "MASK="
for /f "tokens=1,2,3 delims= " %%A in ('"%PY%" tools\set_key_gui.py --status-line 2^>nul') do (
  set "ST=%%A"
  set "MODEL=%%B"
  set "MASK=%%C"
)
if /i "%ST%"=="ON" set "LLMSTAT=已启用 · %MODEL% · %MASK%"
if /i "%ST%"=="READY" set "LLMSTAT=[已填密钥但未启用] 按 K 去启用"
if not defined LLMSTAT set "LLMSTAT=[未配置] 按 K 填写密钥"
cls
echo ============================================================
echo                U校园自动化助手  v%VER%
echo ============================================================
echo    大模型答题：%LLMSTAT%
echo ------------------------------------------------------------
echo    [S] AI版刷时长  ：真实播放课件视频，自动刷学习时长 ★推荐
echo    [A] API模式     ：纯接口方式，不启浏览器（登录/查课程用）
echo    [1] 试运行      ：只扫描目录结构，不实际学习
echo    [2] 正式运行    ：浏览器模式，按 config.yaml 全部自动执行
echo    [3] 只刷时长    ：不播视频、不答题，纯挂学习时长
echo    [4] 只播视频/音频
echo    [5] 只做单元测试
echo    [6] 选择单元    ：多选要学哪几个 Unit
echo    [7] 列出目录    ：先看清课程有哪些条目
echo    [8] 打开报告目录
echo    [9] 编辑配置    ：课程地址、时长、答题、口语等
echo    [C] 清除登录态  ：换账号或登录异常时用
echo    [P] DOM 探针    ：页面结构变了、识别不到东西时用它校准
echo    [K] 设置大模型密钥（DeepSeek / Kimi / 本地 Ollama）
echo    [D] 环境自检    ：依赖/浏览器/配置一次查清
echo    [0] 退出
echo ============================================================
echo    提示：网页登录被「U校园认证」卡住时，请用 [A]。
echo          运行期间不要最小化浏览器窗口，否则可能不计学习时长。
echo ============================================================
set /p opt=请输入序号后回车：
if not defined opt goto end

if /i "%opt%"=="s" goto aistudy
if /i "%opt%"=="a" goto apirun
if "%opt%"=="1" goto dry
if "%opt%"=="2" goto run
if "%opt%"=="3" goto onlydwell
if "%opt%"=="4" goto vid
if "%opt%"=="5" goto quiz
if "%opt%"=="6" goto pick
if "%opt%"=="7" goto list
if "%opt%"=="8" goto rep
if "%opt%"=="9" goto cfg
if /i "%opt%"=="c" goto relogin
if /i "%opt%"=="p" goto probe
if /i "%opt%"=="k" goto setkey
if /i "%opt%"=="d" goto doctor
if "%opt%"=="0" goto end
goto menu

:aistudy
echo.
echo === AI版课件刷时长（真实播放，走进入课程后的课件页）===
echo    全程自动：注入会话 -^> 进课件 -^> 逐个播放视频/停留 -^> 下一项
echo    运行期间请不要关闭浏览器窗口。
echo.
set /p MINS=本次刷多少分钟（直接回车 = 60）:
if not defined MINS set "MINS=60"
echo.
set /p BOOK=哪本书？ 1=视听说  2=读写  （回车 = 视听说）:
set "BK=vls"
if "%BOOK%"=="2" set "BK=rw"
echo.
echo 开始：%MINS% 分钟，教材=%BK%
echo 按 Ctrl+C 可随时中断；中断后重新运行会从第 1 项开始。
echo.
if "%BK%"=="rw" (
  "%PY%" tools\study_ai.py --minutes %MINS% --book rw
) else (
  "%PY%" tools\study_ai.py --minutes %MINS%
)
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:apirun
echo.
echo === API 模式刷时长（不启浏览器、不过网页认证）===
echo    会用账号密码走官方登录接口，成功后直接上报学习时长。
echo    账号密码没写在 config.yaml 时会当场询问（不落盘）。
echo.
"%PY%" tools\api_run.py
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:dry
echo.
echo === 试运行：只扫描目录 ===
"%PY%" main.py --config config.yaml --dry-run
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:run
echo.
echo === 正式运行 ===
"%PY%" main.py --config config.yaml
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:onlydwell
echo.
echo === 只刷时长 ===
"%PY%" main.py --config config.yaml --only dwell
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:vid
echo.
echo === 只播视频/音频 ===
"%PY%" main.py --config config.yaml --only video
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:quiz
echo.
echo === 只做单元测试 ===
"%PY%" main.py --config config.yaml --only quiz
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:pick
echo.
echo === 选择要学习的单元 ===
"%PY%" main.py --config config.yaml --pick
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:list
echo.
echo === 课程目录 ===
"%PY%" main.py --config config.yaml --list
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:probe
echo.
echo === DOM 探针 / 结构校准 ===
echo    会打印当前页面的目录/Tab/Task/题目结构，
echo    并把结果保存到 output\probe_*.json
echo.
"%PY%" tools\probe.py
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:rep
if not exist output mkdir output
start "" "%CD%\output"
goto menu

:cfg
notepad "%CD%\config.yaml"
goto menu

:relogin
if exist "state\storage_state.json" (
  del /f /q "state\storage_state.json"
  echo 已清除登录态，下次运行需重新登录。
) else (
  echo 当前没有已保存的登录态。
)
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:setkey
echo.
echo === 设置大模型答题密钥 ===
call "%CD%\SetDeepSeekKey.bat"
set "RC=%ERRORLEVEL%"
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:doctor
echo.
echo === 环境自检 ===
"%PY%" tools\doctor.py
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:end
exit /b 0
"""

# ---------------------------------------------------------------- run/dry
RUN = r"""@echo off
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
"""

RUN_DRY = r"""@echo off
chcp 936 >nul
title U校园自动化助手 - 试运行
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] 请先双击 setup.bat 安装环境
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py --config config.yaml --dry-run
pause
exit /b %ERRORLEVEL%
"""

# ---------------------------------------------------------------- 密钥
SETKEY = r"""@echo off
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
"""

# ---------------------------------------------------------------- 桌面唯一入口
# 桌面只允许存在这一个 bat：双击 = 打开本地控制面板；
# 带参数可直达旧菜单命令（run / doctor / ai / pick ...），方便命令行或快捷方式。
# 生成器不再往桌面写 v2 / 应用更新 之类的第二个文件。
DESKTOP = r"""@echo off
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
"""


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="生成 / 修补 U校园助手的 bat 启动器")
    ap.add_argument("--preview", action="store_true",
                    help="只生成待替换文件，不覆盖任何在用的 bat（默认）")
    ap.add_argument("--apply", action="store_true",
                    help="正式覆盖 start.bat 与桌面入口")
    ap.add_argument("--force", action="store_true",
                    help="刷课仍在跑也强制覆盖（刷课本体不受影响）")
    a = ap.parse_args(argv)

    # ★ start.bat 以磁盘上的现网版本为准，只做补丁，绝不用下面的旧模板覆盖。
    #   START 是历史版本（没有双遍满分/选单元/aiauto），覆盖 = 功能回退。
    cur = (ROOT / "start.bat").read_bytes().decode("gbk", errors="replace")
    start_text = patch_start(cur)
    desk = desktop_dir()

    if a.apply and study_running() and not a.force:
        print("[拒绝] 刷课日志仍在更新（程序还在跑）。")
        print("       cmd 按字节偏移逐行读 bat，运行中覆盖会让窗口错乱。")
        print("       等这轮结束再执行：python _gen_bat.py --apply")
        print("       确实要现在覆盖：加 --force（不影响正在跑的 Python 刷课）")
        return 1

    if a.apply:
        print("应用更新（覆盖在用的 bat）:")
        w("start.bat", start_text)
        if desk:
            _write(desk / "U校园自动化助手.bat", DESKTOP)
        print("  → start.bat + 桌面唯一入口已更新，下次双击打开控制面板")
    else:
        print("预览模式（只生成待替换文件，未动任何在用的 bat）:")
        w("start.next.bat", start_text)
        print("  → 确认后执行：python _gen_bat.py --apply")

    # 项目内保留一份模板副本；桌面始终只写一个入口，不再生成第二个 bat。
    w("_desktop_launcher.bat", DESKTOP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
