# -*- coding: utf-8 -*-
"""双击 bat 时的启动自检：清理残留的旧刷题进程，保证用最新代码。

由桌面「U校园自动化助手.bat」在启动面板前自动调用，不需要手动运行。

背景：刷题进程 study_ai.py 是每次点「开始刷课」时新起的进程，天然加载最新
代码。唯一会用旧代码的情况是——上一次的刷题进程没结束、还在后台跑
（例如一次会话连续几小时，中途源码已更新）。双击 bat 说明用户要重新用，
此时先把这种残留进程清掉，避免它继续用旧逻辑、也避免和面板抢资源。

只结束 study_ai.py，绝不动面板 ui_server.py（面板本身不加载答题代码）。
"""
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# pwsh7 优先（用户规范），回退系统 powershell
PWSH = r"C:\Users\YZH\AppData\Local\Microsoft\WindowsApps\pwsh.exe"
PS = PWSH if Path(PWSH).exists() else "powershell.exe"

# 只匹配 study_ai.py 进程；命令行里的引号做宽松处理
FIND = (
    "Get-CimInstance Win32_Process | Where-Object { "
    "$_.CommandLine -match 'study_ai' -and $_.Name -match 'python' "
    "} | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json"
)


def find_stale():
    try:
        r = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-Command", FIND],
            capture_output=True, text=True, timeout=20,
        )
        import json
        txt = (r.stdout or "").strip()
        if not txt:
            return []
        data = json.loads(txt)
        if isinstance(data, dict):
            data = [data]
        return [d for d in data if d.get("ProcessId")]
    except Exception:  # noqa: BLE001
        return []


def main():
    procs = find_stale()
    if not procs:
        print("[自检] 无残留刷题进程，将用最新代码启动。")
        return 0

    print(f"[自检] 发现 {len(procs)} 个残留的旧刷题进程，正在清理：")
    for d in procs:
        pid = d.get("ProcessId")
        cmd = (d.get("CommandLine") or "").replace("\r", " ").replace("\n", " ")
        print(f"        PID {pid}: {cmd[:90]}")
        try:
            subprocess.run(
                [PS, "-NoProfile", "-NonInteractive", "-Command",
                 f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=10,
            )
        except Exception:  # noqa: BLE001
            pass
    print("[自检] 已清理，面板将用最新代码启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
