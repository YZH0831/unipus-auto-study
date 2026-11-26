# -*- coding: utf-8 -*-
"""下载并分析 ai-people SDK（PC 版 vs 移动版），找对话接口。

只读探针。目的：
  1) 两个 SDK 的差异（移动版多出哪些接口）
  2) 里面出现的 API / WebSocket 端点
  3) 初始化参数（是否需要端标识、会话 id 从哪来）
产物：output/aipeople_pc.js、output/aipeople_mobile.js、output/aipeople_diff.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

OUT = ROOT / "output"
URLS = {
    "pc": "https://ucontentapi.unipus.cn/spe/ai-people-pc/aipeople.js",
    "mobile": "https://ucontentapi.unipus.cn/spe/ai-people-mobile/aipeople.js",
}

PATTERNS = {
    "http_api": r"[\"'](?:https?://[^\"']{4,140}|/api/[^\"']{2,120})[\"']",
    "ws": r"[\"'](?:wss?://[^\"']{4,140})[\"']",
    "endpoint_like": r"[\"'](/[a-z0-9_\-]+(?:/[a-z0-9_\-{}:.]+){1,6})[\"']",
}


def main() -> int:
    report: dict = {}
    with sync_playwright() as pw:
        browser = None
        for ch in ("msedge", "chrome", "chromium"):
            try:
                browser = pw.chromium.launch(channel=ch, headless=True)
                break
            except Exception:  # noqa: BLE001
                browser = None
        if browser is None:
            return 2
        ctx = browser.new_context()
        req = ctx.request
        for tag, url in URLS.items():
            r = req.get(url, timeout=40000)
            body = r.text() if r.status == 200 else ""
            p = OUT / f"aipeople_{tag}.js"
            p.write_text(body, encoding="utf-8")
            hits: dict[str, list[str]] = {}
            for k, pat in PATTERNS.items():
                vals = sorted(set(re.findall(pat, body)))
                vals = [v for v in vals if not re.search(r"\.(js|css|png|jpg|svg|woff2?)$", v)]
                if vals:
                    hits[k] = vals[:60]
            report[tag] = {"status": r.status, "size": len(body), "hits": hits}
            print(f"[{tag}] status={r.status} size={len(body)} 命中={ {k: len(v) for k, v in hits.items()} }")
        ctx.close()
        browser.close()

    (OUT / "aipeople_diff.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 移动版独有的端点
    try:
        pm = set(report.get("pc", {}).get("hits", {}).get("endpoint_like", []))
        mm = set(report.get("mobile", {}).get("hits", {}).get("endpoint_like", []))
        print(f"\n[端点] pc={len(pm)} mobile={len(mm)} mobile独有={sorted(mm - pm)[:25]}")
        print(f"[端点数] 交集={sorted(pm & mm)[:10]}")
    except Exception:  # noqa: BLE001
        pass
    print(f"结果: {OUT / 'aipeople_diff.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
