# -*- coding: utf-8 -*-
"""Talk with AI 全流程一键完成（假定已打开题目详情页）。

用法：
  python auto_twai.py --content "句1|句2|句3|句4" [--role A] [--wait 13]

流程：
  选角色 → 开始对话 → 逐段录音发送 → 结束 → 确定 → 查看评价 → 提交 → 返回目录
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import talk  # 复用 talk.py 的发送/状态机

BASE = Path(__file__).resolve().parent
PY = talk.PY
ADB = talk.ADB

ROLE_A = (607, 1766)
ROLE_B = (607, 2052)
BTN_START = (607, 2323)
BTN_END = (1002, 2478)
DLG_OK = (809, 1519)        # 结束确认弹窗的「确定」
BTN_SUBMIT = (885, 2461)    # 评价页「提交」
BTN_RETRY_EVAL = (885, 2386)  # 「查看评价」/「再试一次」那排的右按钮


def tap(pt, w=1.0):
    subprocess.run([ADB, "shell", "input", "tap", str(pt[0]), str(pt[1])], timeout=30)
    time.sleep(w)


def state() -> str:
    t = talk.dump_texts()
    if "口语评价" in t:
        return "eval"
    if "查看评价" in t or "再试一次" in t:
        return "ended"
    if "正在录制" in t:
        return "recording"
    if "录制完成" in t or "重录" in t:
        return "preview"
    if "点击录音" in t:
        return "dialog"
    if "开始对话" in t:
        return "rolepick"
    if "录音失败" in t:
        return "error"
    return "unknown"


def main() -> int:
    a = sys.argv[1:]
    content = ""
    role = "A"
    wait = 13.0
    for i, v in enumerate(a):
        if v == "--content" and i + 1 < len(a):
            content = a[i + 1]
        elif v == "--role" and i + 1 < len(a):
            role = a[i + 1].upper()
        elif v == "--wait" and i + 1 < len(a):
            wait = float(a[i + 1])

    segs = [s.strip() for s in content.split("|") if s.strip()]
    if not segs:
        print("需要 --content \"句1|句2|...\"")
        return 1

    print(f"[1] 选角色 {role}")
    tap(ROLE_A if role == "A" else ROLE_B, 1.6)
    print(f"[2] 开始对话")
    tap(BTN_START, 2.0)
    st = state()
    if st == "rolepick":            # 没进去，再点一次（有时首次点击被吞）
        print("    重试开始对话")
        tap(ROLE_A if role == "A" else ROLE_B, 1.2)
        tap(BTN_START, 2.0)
    time.sleep(9)
    print(f"[3] 对话界面状态={state()}")

    print(f"[4] 预生成 {len(segs)} 段音频")
    wavs = []
    for i, s in enumerate(segs, 1):
        wav, dur = talk.make_audio(s, f"a{i}_{int(time.time()) % 100000}")
        print(f"    [{i}] {dur:.1f}s")
        wavs.append(wav)

    ok = 0
    for i, wav in enumerate(wavs, 1):
        if talk.send_seg(wav, i, len(wavs)):
            ok += 1
        if i < len(wavs):
            time.sleep(wait)
    print(f"[5] 成功发送 {ok}/{len(wavs)} 段")

    print("[6] 结束对话")
    tap(BTN_END, 2.5)
    tap(DLG_OK, 8.0)
    st = state()
    if st == "ended":
        print("    点查看评价")
        tap(BTN_RETRY_EVAL, 6.0)
        st = state()
    print(f"    状态={st}")

    if st == "eval":
        print("[7] 提交")
        tap(BTN_SUBMIT, 6.0)
        t = talk.dump_texts()
        done = "已作答 (1/1)" in t
        print(f"    已作答 1/1 = {done}")
        # 返回目录
        for key in ("返回目录", "继续学习"):
            r = subprocess.run([PY, "-X", "utf8", str(BASE / "mobile_uia.py"), "tap", key],
                               capture_output=True, timeout=60)
            if "命中" in (r.stdout or b"").decode("utf-8", "replace"):
                time.sleep(5)
                break
    else:
        print("    [!] 未进入评价页，需人工检查")
        subprocess.run([ADB, "exec-out", "screencap", "-p"],
                       stdout=open(BASE / "shots" / "twai_fail.png", "wb"), timeout=40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
