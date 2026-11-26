# -*- coding: utf-8 -*-
"""前端 JS 静态分析（独立探针，只读，不碰业务模块）。

目标：在课程前端 bundle 里定位
  1) 「该题型暂不支持pc端」这句文案的**判定条件**（是 UA？appid？skin？）
  2) 皮肤包/端的命名规律（_explorationXXX）
  3) ai-people 是否有 app/mobile 版 SDK

产物：output/mobile_js_probe.json
用法：.venv\\Scripts\\python.exe tools/mobile_probe2.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from upc.config import load_config  # noqa: E402

OUT = ROOT / "output"
CID = "1847183133920289074"
COURSE = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
BASE = "https://ucontent.unipus.cn/_explorationpc_default/"

# 从 pc.html 外壳里读到的真实 bundle 名
BUNDLES = [
    "index-78694d84.js",
    "common-edd53069.js",
    "question-20c34c40.js",
    "question-pc-556fc926.js",
    "question-common-d5b6c687.js",
    "question-data-f35b6260.js",
    "vendor-513ad95d.js",
]

APIS = [
    f"https://ucontent.unipus.cn/course/api/course/{COURSE}/default",
    f"https://ucontent.unipus.cn/course/api/v2/course_progress/{COURSE}",
]

SDK = [
    "https://ucontentapi.unipus.cn/spe/ai-people-pc/aipeople.js",
    "https://ucontentapi.unipus.cn/spe/ai-people-app/aipeople.js",
    "https://ucontentapi.unipus.cn/spe/ai-people-mobile/aipeople.js",
    "https://ucontentapi.unipus.cn/spe/ai-people/aipeople.js",
]

PATTERNS = {
    "skin": r"_exploration[a-z0-9_\-]*",
    "unsupported_text": r".{200}暂不支持\s*[Pp][Cc]\s*端.{200}",
    "app_hint": r".{120}(请在\s*[Aa]pp\s*端|请使用\s*[Aa]pp).{120}",
    "appid_ctx": r".{80}appid.{80}",
    "ai_people_path": r".{60}ai-people[a-z\-]*.{60}",
    "is_app_flag": r".{60}(isApp|is_app|fromApp|from_app|clientType|client_type|platform).{60}",
}


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    state = Path(getattr(cfg, "state_dir", ROOT / "state")) / "storage_state.json"
    report: dict = {"bundles": {}, "apis": {}, "sdk": {}, "skins": []}

    with sync_playwright() as pw:
        for ch in ("msedge", "chrome", "chromium"):
            try:
                browser = pw.chromium.launch(channel=ch, headless=True)
                break
            except Exception:  # noqa: BLE001
                browser = None
        if browser is None:
            print("浏览器启动失败")
            return 2
        ctx = browser.new_context(locale="zh-CN",
                                  **({"storage_state": str(state)} if state.exists() else {}))
        req = ctx.request

        skins: set[str] = set()

        for name in BUNDLES:
            url = BASE + name
            try:
                r = req.get(url, timeout=45000)
                if r.status != 200:
                    report["bundles"][name] = {"status": r.status}
                    print(f"[bundle] {name} status={r.status}")
                    continue
                js = r.text()
                hits: dict[str, list[str]] = {}
                for key, pat in PATTERNS.items():
                    found = re.findall(pat, js, re.S)
                    if found:
                        hits[key] = [re.sub(r"\s+", " ", str(x))[:600] for x in found[:6]]
                for s in re.findall(PATTERNS["skin"], js):
                    skins.add(s)
                # 顺带列出该 bundle 里出现的其他分端文件
                siblings = sorted(set(re.findall(r"[a-z\-]*-(?:pc|mobile|app|h5)-[0-9a-f]{8}\.js", js)))
                report["bundles"][name] = {"status": 200, "size": len(js),
                                           "hits": hits, "siblings": siblings[:40]}
                print(f"[bundle] {name} size={len(js)} 命中={list(hits)}")
            except Exception as exc:  # noqa: BLE001
                report["bundles"][name] = {"error": str(exc)[:150]}
                print(f"[bundle] {name} 失败 {str(exc)[:90]}")

        report["skins"] = sorted(skins)
        print(f"[skins] {sorted(skins)[:25]}")

        for url in APIS:
            try:
                r = req.get(url, timeout=30000)
                body = r.text()
                report["apis"][url] = {"status": r.status, "len": len(body), "head": body[:3000]}
                print(f"[api] {url.split('/')[-2:]} status={r.status} len={len(body)}")
            except Exception as exc:  # noqa: BLE001
                report["apis"][url] = {"error": str(exc)[:150]}

        for url in SDK:
            try:
                r = req.get(url, timeout=25000)
                report["sdk"][url] = {"status": r.status, "len": len(r.text()) if r.status == 200 else 0}
                print(f"[sdk] {url.split('/spe/')[-1]} status={r.status}")
            except Exception as exc:  # noqa: BLE001
                report["sdk"][url] = {"error": str(exc)[:120]}

        ctx.close()
        browser.close()

    (OUT / "mobile_js_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果: {OUT / 'mobile_js_probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
