# -*- coding: utf-8 -*-
"""用 faster-whisper 本地转录课件音频（离线、免费）。

输入 data/audio_index.json
输出 data/transcripts.json  {叶子id: {name, audio, text}}

只转录每页"最大体积"的那个音频（主音频），选项发音的小音频跳过。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pick_main(session, urls):
    """按 Content-Length 挑主音频（选项发音的小音频只有几十 KB）。"""
    best, best_len = None, 0
    for u in urls:
        try:
            r = session.head(u, timeout=15, verify=False, allow_redirects=True)
            n = int(r.headers.get("content-length") or 0)
            if n > best_len:
                best, best_len = u, n
        except Exception:  # noqa: BLE001
            continue
    if best is None and urls:
        best = urls[0]
    return best, best_len


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="base")
    ap.add_argument("--micro-from", type=int, default=0)
    ap.add_argument("--micro-to", type=int, default=13)
    ap.add_argument("--force", action="store_true", help="已转录过的也重转")
    args = ap.parse_args()

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    import requests

    # --- PyAV 兼容层 ---------------------------------------------------
    # faster-whisper 1.2.1 会给 av.open 传 metadata_errors="ignore"，
    # 但 PyAV 19 已移除该参数，会抛 TypeError。这里静默丢弃该参数，
    # 避免为一个小参数去降级整条依赖链。
    import av as _av
    _orig_open = _av.open

    def _compat_open(*a, **kw):
        kw.pop("metadata_errors", None)
        return _orig_open(*a, **kw)

    _av.open = _compat_open
    # ------------------------------------------------------------------

    from faster_whisper import WhisperModel

    idx = json.loads((ROOT / "data" / "audio_index.json").read_text(encoding="utf-8"))
    dst = ROOT / "data" / "transcripts.json"
    done = {}
    if dst.exists():
        try:
            done = json.loads(dst.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            done = {}

    todo = [k for k, v in idx.items()
            if v.get("audio")
            and args.micro_from <= (v.get("micro_idx") or 0) <= args.micro_to
            and (args.force or k not in done)]
    print(f"待转录 {len(todo)} 个页面（已缓存 {len(done)}）")
    if not todo:
        return 0

    cache = ROOT / "data" / "audio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"加载模型 {args.model} …")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    print("模型就绪")

    session = requests.Session()
    session.verify = False
    ok = 0
    for i, k in enumerate(todo, 1):
        rec = idx[k]
        url, size = pick_main(session, rec["audio"])
        if not url:
            print(f"  [{i}/{len(todo)}] {rec['name']} 无可用音频")
            continue
        ext = ".mp3"
        local = cache / (k + ext)
        try:
            if not local.exists():
                r = session.get(url, timeout=120, verify=False)
                r.raise_for_status()
                local.write_bytes(r.content)
            segments, info = model.transcribe(
                str(local), language="en", beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            done[k] = {"name": rec.get("name"), "micro_idx": rec.get("micro_idx"),
                       "type": rec.get("type"), "audio": url,
                       "size": size, "text": text,
                       "options": rec.get("options") or []}
            ok += 1
            print(f"  [{i}/{len(todo)}] {rec['name'][:20]:<22} "
                  f"{len(text):>5} 字符 | {text[:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(todo)}] {rec['name'][:20]} 失败: {str(exc)[:80]}")
        # 每轮落盘，防中断丢进度
        if i % 3 == 0:
            dst.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")

    dst.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {ok} 个，累计 {len(done)} → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
