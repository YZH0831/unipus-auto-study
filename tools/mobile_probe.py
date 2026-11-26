# -*- coding: utf-8 -*-
"""移动端皮肤探测（独立探针，不改任何现有模块）。

目的：确认「Talk with AI」这类被判定 unsupported_pc 的题，
能否通过 **移动皮肤包 / 移动 UA / appid 变体** 在 PC 浏览器里直接渲染出来。

思路来源：真实课程 URL 形如
  https://ucontent.unipus.cn/_explorationpc_default/pc.html?cid=...&appid=116#/course-v2:...
路径里 `_explorationpc_default` 是「PC 皮肤包」——说明皮肤可切换。

本脚本只读：只 GET/打开页面并 dump 现场，不点提交、不改进度。
用法：
  .venv\\Scripts\\python.exe tools/mobile_probe.py
产物：
  output/mobile_probe.json      探测结果
  output/mobile_probe_*.png     每个候选页的截图
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
OUT.mkdir(parents=True, exist_ok=True)

# 已知真实页面（从 logs 里抓到的原始 URL）
CID = "1847183133920289074"
COURSE = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
HASH = f"#/{COURSE}"

# 手写安卓移动参数（本机 playwright 版本取不到 devices 表）
MOBILE = dict(
    user_agent=("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    viewport={"width": 412, "height": 915},
    device_scale_factor=2.625,
    is_mobile=True,
    has_touch=True,
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)

# 待探测的皮肤/入口候选
CANDIDATES = [
    ("pc基线-移动UA", f"https://ucontent.unipus.cn/_explorationpc_default/pc.html?cid={CID}&appid=116{HASH}"),
    ("mobile皮肤-mobile.html", f"https://ucontent.unipus.cn/_explorationmobile_default/mobile.html?cid={CID}&appid=116{HASH}"),
    ("mobile皮肤-pc.html", f"https://ucontent.unipus.cn/_explorationmobile_default/pc.html?cid={CID}&appid=116{HASH}"),
    ("mobile皮肤-无hash", f"https://ucontent.unipus.cn/_explorationmobile_default/mobile.html?cid={CID}&appid=116"),
    ("wap皮肤", f"https://ucontent.unipus.cn/_explorationwap_default/mobile.html?cid={CID}&appid=116{HASH}"),
    ("app皮肤", f"https://ucontent.unipus.cn/_explorationapp_default/app.html?cid={CID}&appid=116{HASH}"),
]

PC_BAD_MARKS = ("不支持", "请在App", "请在 App", "手机端", "客户端")
AI_MARKS = ("Talk with AI", "Role-play", "roleplay", "口语", "录音")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    state = Path(getattr(cfg, "state_dir", ROOT / "state")) / "storage_state.json"
    print(f"[探针] 登录态: {state} 存在={state.exists()}")
    has_state = {"storage_state": str(state)} if state.exists() else {}

    report = {"state_found": state.exists(), "candidates": []}

    with sync_playwright() as pw:
        browser = None
        for ch in ("msedge", "chrome", "chromium"):
            try:
                browser = pw.chromium.launch(channel=ch, headless=True,
                                             args=["--disable-blink-features=AutomationControlled"])
                print(f"[探针] 浏览器: {ch}")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[探针] {ch} 启动失败: {str(exc)[:90]}")
        if browser is None:
            print("[探针] 浏览器全部启动失败")
            return 2

        ctx = browser.new_context(**{**MOBILE, **has_state})
        ctx.set_default_timeout(20000)

        # ---- 阶段 1：拿 pc.html 原文，抽出所有皮肤包名字 ----
        skins: list[str] = []
        try:
            resp = ctx.request.get(
                f"https://ucontent.unipus.cn/_explorationpc_default/pc.html?cid={CID}&appid=116",
                timeout=20000)
            html = resp.text()
            skins = sorted(set(re.findall(r"_exploration[a-z0-9_]*", html)))
            print(f"[探针] pc.html status={resp.status} 长度={len(html)} 皮肤名={skins}")
            (OUT / "mobile_probe_pc.html").write_text(html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[探针] 取 pc.html 失败: {str(exc)[:120]}")
        report["skins_in_html"] = skins

        # ---- 阶段 2：逐个候选打开，记录现场 ----
        page = ctx.new_page()
        for name, url in CANDIDATES:
            item = {"name": name, "url": url}
            try:
                r = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                item["status"] = r.status if r else None
                page.wait_for_timeout(3500)
                item["final_url"] = page.url
                txt = _norm(page.evaluate("() => document.body ? document.body.innerText : ''"))
                item["text_len"] = len(txt)
                item["head"] = txt[:400]
                item["pc_bad_marks"] = [m for m in PC_BAD_MARKS if m in txt]
                item["ai_marks"] = [m for m in AI_MARKS if m in txt]
                shot = OUT / f"mobile_probe_{len(report['candidates'])}.png"
                try:
                    page.screenshot(path=str(shot), full_page=False)
                    item["shot"] = str(shot.relative_to(ROOT))
                except Exception:  # noqa: BLE001
                    pass
                print(f"[{name}] status={item['status']} 文本={item['text_len']} "
                      f"AI词={item['ai_marks']} PC不支持词={item['pc_bad_marks']}")
            except Exception as exc:  # noqa: BLE001
                item["error"] = str(exc)[:160]
                print(f"[{name}] 打开失败: {item['error']}")
            report["candidates"].append(item)

        ctx.close()
        browser.close()

    (OUT / "mobile_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[探针] 结果已写 {OUT / 'mobile_probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
