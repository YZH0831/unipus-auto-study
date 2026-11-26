# -*- coding: utf-8 -*-
"""手机端自动化的外部依赖定位（adb / ffmpeg / python）。

优先用 PATH 里的命令；找不到再退回本机已知安装位置。
可用环境变量覆盖：ADB_PATH、FFMPEG_PATH、FFPROBE_PATH
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_KNOWN = {
    "adb": [r"C:\Users\YZH\Tools\platform-tools\adb.exe",
            r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"],
    "ffmpeg": [r"C:\Users\YZH\AppData\Local\Programs\ffmpeg\bin\ffmpeg.exe",
               r"%LOCALAPPDATA%\Programs\ffmpeg\bin\ffmpeg.exe"],
    "ffprobe": [r"C:\Users\YZH\AppData\Local\Programs\ffmpeg\bin\ffprobe.exe",
                r"%LOCALAPPDATA%\Programs\ffmpeg\bin\ffprobe.exe"],
}


def _resolve(name: str, env: str) -> str:
    v = os.environ.get(env)
    if v and Path(v).exists():
        return v
    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        return found
    for cand in _KNOWN.get(name, []):
        p = Path(os.path.expandvars(cand))
        if p.exists():
            return str(p)
    return name          # 交给调用处报错，信息更清楚


ADB = _resolve("adb", "ADB_PATH")
FFMPEG = _resolve("ffmpeg", "FFMPEG_PATH")
FFPROBE = _resolve("ffprobe", "FFPROBE_PATH")
PY = sys.executable          # 一律用当前解释器（venv 内就对了）


def check(verbose: bool = True) -> bool:
    ok = True
    for label, val in (("adb", ADB), ("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        exists = Path(val).exists() or shutil.which(val)
        if verbose:
            print(f"  {label:8s} {'OK ' if exists else '缺失'} {val}")
        ok = ok and bool(exists)
    if verbose:
        print(f"  python   OK  {PY}")
    return ok


if __name__ == "__main__":
    print("手机端依赖检查：")
    raise SystemExit(0 if check() else 1)
