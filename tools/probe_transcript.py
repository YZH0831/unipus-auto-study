# -*- coding: utf-8 -*-
"""探测 CDN 上是否有音频对应的字幕/原文文件。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AUDIO = ("https://ucontent.cdn.unipus.cn/edx/course-v2:Unipus+nhce_4_vls_1+20240205/"
         "resource/audio/b2134a8612f6814cbd813c3ece1d6c711e4fe6de_96k.mp3")

BASE = ("https://ucontent.cdn.unipus.cn/edx/course-v2:Unipus+nhce_4_vls_1+20240205/"
        "resource/")
HASH = "b2134a8612f6814cbd813c3ece1d6c711e4fe6de"


def main():
    from upc.config import load_config
    from upc import api_client  # noqa: F401
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    log = setup_logging(ROOT / "logs", "WARNING")
    try:
        import requests
    except ImportError:
        print("缺 requests")
        return 1

    s = requests.Session()
    s.verify = False
    tries = [
        f"{BASE}audio/{HASH}_96k.vtt",
        f"{BASE}audio/{HASH}.vtt",
        f"{BASE}audio/{HASH}_96k.srt",
        f"{BASE}audio/{HASH}.srt",
        f"{BASE}audio/{HASH}_96k.txt",
        f"{BASE}transcript/{HASH}.vtt",
        f"{BASE}transcript/{HASH}_96k.vtt",
        f"{BASE}transcript/{HASH}.srt",
        f"{BASE}text/{HASH}.txt",
        f"{BASE}subtitle/{HASH}.vtt",
        f"{BASE}video/{HASH}.vtt",
        f"{BASE}audio/{HASH}_96k.json",
        f"{BASE}audio/{HASH}.json",
    ]
    for u in tries:
        try:
            r = s.get(u, timeout=20)
            tag = "HIT " if r.status_code == 200 else "    "
            print(f"{tag}{r.status_code}  {u.split('/resource/')[-1]}  len={len(r.content)}")
            if r.status_code == 200 and len(r.content) > 20:
                txt = r.text[:400].replace("\n", " ")
                print(f"      {txt}")
        except Exception as exc:  # noqa: BLE001
            print(f"    ERR {u.split('/resource/')[-1]}: {str(exc)[:60]}")

    # 顺带试试 edx 的资源列表接口
    print("\n=== 试 edx 资源清单 ===")
    for u in (
        "https://ucontent.unipus.cn/course/api/v3/content/"
        "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205/706f74aee01277d/default",
        "https://ucontent.unipus.cn/course/api/v3/transcript/"
        "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205/706f74aee01277d",
    ):
        try:
            r = s.get(u, timeout=20)
            print(f"  {r.status_code} {u[-60:]} len={len(r.text)}  {r.text[:160]}")
        except Exception as exc:  # noqa: BLE001
            print("  ERR", str(exc)[:70])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
