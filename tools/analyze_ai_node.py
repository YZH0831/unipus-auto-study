# -*- coding: utf-8 -*-
"""分析课程结构 JSON 里 Talk with AI 节点的真实字段（只读探针）。

先把课程 default 接口整包存到 output/course_default.json，
再按「名字/文案匹配 Talk with AI」定位节点，打印它的全部字段与祖先链。
产物：output/course_default.json、output/ai_node.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from upc.config import load_config  # noqa: E402

COURSE = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
URL = f"https://ucontent.unipus.cn/course/api/course/{COURSE}/default"
KEY = "Talk with AI"


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    state = Path(getattr(cfg, "state_dir", ROOT / "state")) / "storage_state.json"
    raw_path = ROOT / "output" / "course_default.json"

    if raw_path.exists():
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        print(f"[cache] 用已存 {raw_path} ({raw_path.stat().st_size} bytes)")
    else:
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
            ctx = browser.new_context(
                locale="zh-CN",
                **({"storage_state": str(state)} if state.exists() else {}))
            r = ctx.request.get(URL, timeout=40000)
            print(f"[api] status={r.status} bytes={len(r.text())}")
            data = r.json()
            raw_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            ctx.close()
            browser.close()

    print("[顶层键]", list(data.keys())[:20])

    found = []

    def walk(node, trail):
        if len(trail) > 14:
            return
        if isinstance(node, dict):
            # 任一字符串字段含关键词即命中
            if any(isinstance(v, str) and KEY in v for v in node.values()):
                found.append({"trail": list(trail), "node": node})
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, trail + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, trail + [i])

    walk(data, [])
    print(f"[命中节点] {len(found)}")

    for f in found[:3]:
        n = f["node"]
        print("=== trail:", "/".join(str(x) for x in f["trail"]))
        for k, v in n.items():
            sv = v if isinstance(v, (str, int, float, bool, type(None))) else f"<{type(v).__name__} len={len(v)}>"
            print(f"    {k} = {str(sv)[:120]}")

    out = ROOT / "output" / "ai_node.json"
    out.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
