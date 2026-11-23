@echo off
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
for /f "tokens=1,2 delims= " %%A in ('"%PY%" tools\set_key_gui.py --status-line 2^>nul') do set "ST=%%A"
for /f "tokens=1,2 delims= " %%A in ('"%PY%" tools\set_key_gui.py --status-line 2^>nul') do set "MODEL=%%B"
if /i "%ST%"=="ON" set "LLMSTAT=已启用 · %MODEL%"
if /i "%ST%"=="READY" set "LLMSTAT=[已填密钥但未启用] 按 K 去启用"
if not defined LLMSTAT set "LLMSTAT=[未配置] 按 K 填写密钥"
cls
echo ============================================================
echo                U校园自动化助手  v%VER%
echo ============================================================
echo    大模型答题：%LLMSTAT%
echo ------------------------------------------------------------
echo    [S] AI版刷课    ：选节/答题/刷时长一体，题库两遍满分 ★推荐
echo    [6] 选单元      ：按 Unit 选（1 或 1-3 或 1,3），走 AI 版
echo    [A] API模式     ：纯接口，不启浏览器（登录/查课程用）
echo    [L] 看节清单    ：列出所有节 + 哪些已完成
echo ------------------------------------------------------------
echo    [2] 全部跑一遍  ：AI 版，全部课程
echo    [3] 只刷时长    ：不答题，纯挂学习时长（旧版）
echo    [1] 试运行      ：只扫描结构，不实际学习（旧版）
echo    [9] 编辑配置    ：课程地址、时长、答题、口语等
echo    [8] 打开报告目录
echo ------------------------------------------------------------
echo    [K] 设置大模型密钥（加密存储，硬盘不留明文）
echo    [D] 环境自检    ：依赖/浏览器/配置一次查清
echo    [C] 清除登录态  ：换账号或登录异常时用
echo    [P] DOM 探针    ：页面结构变了、识别不到东西时用它校准
echo    [0] 退出
echo ============================================================
echo    提示：登录失效会自动重登；觉得卡住就看最后一行日志。
echo ============================================================
set "opt="
set /p opt=请输入序号后回车：
if not defined opt goto end

if /i "%opt%"=="s" goto aistudy
if /i "%opt%"=="l" goto listmicro
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

:aiauto
echo.
echo === AI版：双击直跑模式 ===
echo   默认：60 分钟 / 视听说+读写两本 / 全部课程 / 双遍补满分
echo   Ctrl+C 可中断；想指定单元/分钟请用菜单 [S]
echo.
"%PY%" -u tools\study_ai.py --minutes 60 --passes 2 --book all
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu
:aistudy
if /i "%~2"=="auto" goto aiauto
echo.
echo === AI版：课件学习 + 自动答题 ===
echo    真实播放视频、自动答题；第一遍摸标准答案，第二遍按题库满分
echo    运行期间不要关闭浏览器窗口
echo.
set "MINS="
set /p MINS=本次跑多少分钟（回车 = 60）:
if not defined MINS set "MINS=60"
echo.
set "BOOK="
set /p BOOK=哪本书？ 1=视听说  2=读写  （回车 = 视听说）:
set "BK=vls"
if "%BOOK%"=="2" set "BK=rw"
echo.
echo 做哪些节？
echo    [1] 全部
echo    [2] 按范围（如 0-13 = 第 0 到第 13 节）
echo    [3] 单个节（序号或名字，如 4 或 Listening to China）
echo    [4] 先列出节清单，不执行
set "SCOPE="
set /p SCOPE=请选择（回车 = 1）:
set "SARG="
set "PLAN="
if "%SCOPE%"=="2" call :askrange
if "%SCOPE%"=="3" call :askone
if "%SCOPE%"=="4" set "PLAN=--plan"
echo.
set "PASSES="
set /p PASSES=跑几遍（回车 = 2；1 = 只跑一遍，不补满分）:
if not defined PASSES set "PASSES=2"
echo.
set "REDO="
set /p REDO=要连已完成的节也重做吗？(y/N，回车 = 否):
set "RARG="
if /i "%REDO%"=="y" set "RARG=--redo"
echo.
echo ------------------------------------------------------------
echo   时长 %MINS% 分钟 / 教材 %BK%
echo   参数：%SARG% %RARG% --passes %PASSES% %PLAN%
echo ------------------------------------------------------------
echo.
"%PY%" -u tools\study_ai.py --minutes %MINS% --book %BK% --passes %PASSES% %SARG% %RARG% %PLAN%
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:askrange
set "RANGE="
set /p RANGE=输入范围（如 0-13）:
for /f "tokens=1,2 delims=-" %%a in ("%RANGE%") do (
  if "%%b"=="" (
    set "SARG=--micro-from %%a"
  ) else (
    set "SARG=--micro-from %%a --micro-to %%b"
  )
)
goto :eof

:askone
set "ONE="
set /p ONE=输入节序号或名字关键词:
set "SARG=--micro %ONE%"
goto :eof

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
echo === 全部跑一遍（AI 版）===
echo.
set "MINS="
set /p MINS=本次跑多少分钟（回车 = 60）:
if not defined MINS set "MINS=60"
set "PASSES="
set /p PASSES=跑几遍（回车 = 2）:
if not defined PASSES set "PASSES=2"
echo.
echo 开始：全部课程 / %MINS% 分钟 / %PASSES% 遍
echo.
"%PY%" -u tools\study_ai.py --minutes %MINS% --passes %PASSES%
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
echo === 选择 Unit（AI 版）===
echo    例： 1      只做 Unit 1
echo         1-3    做 Unit 1 到 3
echo         1,3    做 Unit 1 和 3
echo.
set "UNITSEL="
set /p UNITSEL=输入 Unit（直接回车返回菜单）:
if not defined UNITSEL goto menu
echo.
set "MINS="
set /p MINS=本次跑多少分钟（回车 = 60）:
if not defined MINS set "MINS=60"
echo.
set "PASSES="
set /p PASSES=跑几遍（回车 = 2；1 = 只跑一遍不补满分）:
if not defined PASSES set "PASSES=2"
echo.
set "REDO="
set /p REDO=要连已完成的节也重做吗？(y/N，回车 = 否):
set "RARG="
if /i "%REDO%"=="y" set "RARG=--redo"
echo.
echo ------------------------------------------------------------
echo   开始：Unit %UNITSEL% / %MINS% 分钟 / %PASSES% 遍 %RARG%
echo ------------------------------------------------------------
echo.
"%PY%" -u tools\study_ai.py --minutes %MINS% --passes %PASSES% --unit "%UNITSEL%" %RARG%
set "RC=%ERRORLEVEL%"
echo.
pause
if "%DIRECT%"=="1" exit /b %RC%
goto menu

:listmicro
echo.
echo === 所有小节（AI 版）===
"%PY%" -u tools\study_ai.py --list-micro
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
if "%DIRECT%"=="1" exit /b 0
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
