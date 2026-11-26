# -*- coding: utf-8 -*-
"""音频喂入实验：电脑扬声器播放英文 → 手机麦克风收音。

用法：
  python try_voice.py "英文文本" [录音秒数]

流程：
  文本 → edge-tts 生成 mp3 → ffmpeg 转 wav → 点手机录音 → 播放 → 停止 → 截图
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

REC_BTN = (606, 2200)   # 麦克风圆钮


def tts(text: str, tag: str) -> tuple[Path, float]:
    mp3 = SHOTS / f"{tag}.mp3"
    wav = SHOTS / f"{tag}.wav"
    subprocess.run([PY, "-m", "edge_tts", "--voice", "en-US-AriaNeural",
                    "--text", text, "--write-media", str(mp3)],
                   check=True, timeout=90, capture_output=True)
    subprocess.run([FFMPEG, "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1",
                    "-acodec", "pcm_s16le", str(wav)],
                   check=True, capture_output=True, timeout=90)
    dur = float(subprocess.run(
        [FFPROBE,
         "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(wav)],
        capture_output=True, text=True).stdout.strip() or 0)
    print(f"[tts] {wav.name} {dur:.1f}s")
    return wav, dur


def tap(x: int, y: int) -> None:
    subprocess.run([ADB, "shell", "input", "tap", str(x), str(y)], timeout=30)


def shot(name: str) -> Path:
    p = SHOTS / f"{name}.png"
    data = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                          capture_output=True, timeout=40).stdout
    p.write_bytes(data)
    print(f"[shot] {p}")
    return p


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "I went hiking last weekend."
    rec_s = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    tag = f"feed_{int(time.time()) % 100000}"

    wav, dur = tts(text, tag)

    print("[1] 点录音")
    tap(*REC_BTN)
    time.sleep(1.2)

    print("[2] 电脑扬声器播放")
    t0 = time.time()
    winsound.PlaySound(str(wav), winsound.SND_FILENAME)   # 同步播放
    print(f"[2] 播放结束，用时 {time.time() - t0:.1f}s")

    remain = max(0.0, rec_s - dur)
    if remain:
        time.sleep(remain)

    print("[3] 点停止录音")
    tap(*REC_BTN)
    time.sleep(1.5)
    tap(*REC_BTN)   # 有些版本要再点一次确认发送
    time.sleep(2.5)

    shot(f"{tag}_after")
    print("[4] 当前界面文本：")
    subprocess.run([PY, "-X", "utf8", str(BASE / "mobile_uia.py"), "texts"],
                   timeout=60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
