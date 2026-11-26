# -*- coding: utf-8 -*-
"""Talk with AI 通用自动对话（带状态机 + 失败重试）。

用法：
  python talk.py "句1|句2|句3" [每轮等待秒数]
  python talk.py -f segs.txt [等待秒数]

状态机（靠界面文字判定）：
  idle      点击录音      → 点麦克风开始录
  recording 正在录制      → 播放音频后点「点击完成」
  preview   录制完成/重录 → 点「重录」再录（会立即进入录制）
  error     录音失败      → 点「我知道了」，丢弃后重试
"""
from __future__ import annotations

import subprocess
import sys
import time
import winsound
from pathlib import Path

BASE = Path(__file__).resolve().parent
SHOTS = BASE / "shots"
import adbenv

ADB = adbenv.ADB
PY = adbenv.PY
FFMPEG = adbenv.FFMPEG
FFPROBE = adbenv.FFPROBE

TAP_REC = (606, 2200)      # 麦克风圆钮 / 点击完成
TAP_SEND = (943, 2315)     # 发送
TAP_REDO = (272, 2315)     # 重录
TAP_OK = (607, 1431)       # 我知道了


def sh(args: list[str], timeout: int = 40) -> bytes:
    return subprocess.run([ADB] + args, capture_output=True, timeout=timeout).stdout


def tap(pt: tuple[int, int]) -> None:
    sh(["shell", "input", "tap", str(pt[0]), str(pt[1])])


def dump_texts() -> str:
    r = subprocess.run([PY, "-X", "utf8", str(BASE / "mobile_uia.py"), "texts"],
                       capture_output=True, timeout=60)
    return (r.stdout or b"").decode("utf-8", "replace")


def detect() -> str:
    t = dump_texts()
    if "录音失败" in t:
        return "error"
    if "正在录制" in t:
        return "recording"
    if "录制完成" in t or "重录" in t:
        return "preview"
    if "点击录音" in t:
        return "idle"
    return "unknown"


def make_audio(text: str, tag: str) -> tuple[Path, float]:
    mp3 = SHOTS / f"{tag}.mp3"
    wav = SHOTS / f"{tag}.wav"
    subprocess.run([PY, "-m", "edge_tts", "--voice", "en-US-AriaNeural",
                    "--text", text, "--write-media", str(mp3)],
                   check=True, timeout=90, capture_output=True)
    subprocess.run([FFMPEG, "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1",
                    "-acodec", "pcm_s16le", str(wav)],
                   check=True, capture_output=True, timeout=90)
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1", str(wav)],
                         capture_output=True, text=True).stdout.strip()
    return wav, float(out or 0)


def ensure_ready(max_round: int = 4) -> str:
    """把界面弄到可以开始录音的状态。"""
    for _ in range(max_round):
        st = detect()
        print(f"   [状态] {st}")
        if st == "idle":
            return st
        if st == "error":
            tap(TAP_OK)
            time.sleep(1.5)
            continue
        if st == "recording":
            return st          # 已经在录，直接用
        if st == "preview":
            tap(TAP_REDO)      # 重录 → 立即开始录
            time.sleep(1.2)
            return detect()
        time.sleep(1.5)
    return detect()


def send_seg(wav: Path, idx: int, total: int) -> bool:
    print(f"\n--- 第 {idx}/{total} 句 ---")
    st = ensure_ready()
    if st == "idle":
        tap(TAP_REC)
        time.sleep(1.2)
        st = detect()
        print(f"   点录音后状态={st}")
    if st != "recording":
        print(f"   [!] 未能进入录制状态（{st}），跳过")
        return False

    t0 = time.time()
    winsound.PlaySound(str(wav), winsound.SND_FILENAME)   # 同步播放
    print(f"   播放 {time.time() - t0:.1f}s")
    time.sleep(0.5)

    tap(TAP_REC)               # 点击完成
    time.sleep(2.0)
    st = detect()
    if st == "error":
        tap(TAP_OK)
        time.sleep(1.2)
        print("   [!] 录音失败，重试一次")
        return send_seg(wav, idx, total)
    if st != "preview":
        print(f"   [!] 结束后状态异常({st})")
        return False

    tap(TAP_SEND)
    time.sleep(4.0)
    st = detect()
    if st == "error":
        tap(TAP_OK)
        time.sleep(1.0)
        print("   [!] 发送后报录音失败")
        return False
    print(f"   已发送，状态={st}")
    return True


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == "-f":
        segs = [ln.strip() for ln in Path(a[1]).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
        wait = float(a[2]) if len(a) > 2 else 14.0
    else:
        segs = [s.strip() for s in a[0].split("|") if s.strip()]
        wait = float(a[1]) if len(a) > 1 else 14.0

    print(f"共 {len(segs)} 句，轮间等 {wait}s，预生成音频…")
    items = []
    for i, s in enumerate(segs, 1):
        wav, dur = make_audio(s, f"t{i}_{int(time.time()) % 100000}")
        print(f"  [{i}] {dur:.1f}s")
        items.append(wav)

    for i, wav in enumerate(items, 1):
        send_seg(wav, i, len(items))
        if i < len(items):
            time.sleep(wait)

    print("\n=== 结束状态 ===")
    print(dump_texts()[-800:])
    sh(["exec-out", "screencap", "-p"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
