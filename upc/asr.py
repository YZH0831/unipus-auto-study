# -*- coding: utf-8 -*-
"""本地语音转录（faster-whisper），按需调用。

用途：进到听力页时如果还没有转录文本，就抓页面里的音频直链、
本地转录一次并缓存到 data/transcripts.json，供答题使用。

关键坑：
  - PyAV 19 移除了 av.open 的 metadata_errors 参数，
    而 faster-whisper 1.2.1 还在传 → 这里做兼容层
  - 模型走 hf-mirror.com 下载（国内可达）
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODEL = None
_MEM = {}


def _model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    import av as _av
    _orig = _av.open

    def _compat(*a, **kw):
        kw.pop("metadata_errors", None)
        return _orig(*a, **kw)

    _av.open = _compat
    from faster_whisper import WhisperModel
    _MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    return _MODEL


def _pick_main(session, urls):
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
    return (best or (urls[0] if urls else None)), best_len


def transcripts_path():
    return ROOT / "data" / "transcripts.json"


def load_cache():
    try:
        return json.loads(transcripts_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_cache(d):
    p = transcripts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def transcribe_leaf(page, log, leaf_id, name="", max_seconds=240):
    """抓当前页音频并转录；成功返回文本，失败返回 ""。

    转录结果按音频 URL 复用：同一段音频被多个练习共用时不会重复转。
    """
    if leaf_id in _MEM:
        return _MEM[leaf_id]
    cache = load_cache()
    if leaf_id in cache and (cache[leaf_id] or {}).get("text"):
        _MEM[leaf_id] = cache[leaf_id]["text"]
        return _MEM[leaf_id]

    try:
        urls = page.evaluate(
            "() => Array.from(document.querySelectorAll('audio,video'))"
            ".map(e => e.currentSrc || e.src || '').filter(Boolean)") or []
    except Exception:  # noqa: BLE001
        urls = []
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return ""

    # 同一段音频已经转过就直接复用
    known = {v.get("audio"): v.get("text") for v in cache.values() if v.get("audio")}
    import requests
    session = requests.Session()
    session.verify = False
    url, size = _pick_main(session, urls)
    if url and known.get(url):
        text = known[url]
        cache[leaf_id] = {"name": name, "audio": url, "text": text}
        save_cache(cache)
        _MEM[leaf_id] = text
        log.info(f"    音频已转录过（复用），{len(text)} 字符")
        return text

    if not url:
        return ""
    log.info(f"    开始本地转录（{size // 1024 if size else '?'} KB）…")
    try:
        cache_dir = ROOT / "data" / "audio_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local = cache_dir / f"{abs(hash(url)) & 0xFFFFFFFF:x}.mp3"
        if not local.exists():
            r = session.get(url, timeout=180, verify=False)
            r.raise_for_status()
            local.write_bytes(r.content)
        model = _model()
        segments, _info = model.transcribe(
            str(local), language="en", beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            cache[leaf_id] = {"name": name, "audio": url, "text": text}
            save_cache(cache)
            _MEM[leaf_id] = text
            log.info(f"    转录完成 {len(text)} 字符：{text[:60]}")
        return text
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    转录失败: {str(exc)[:90]}")
        return ""
