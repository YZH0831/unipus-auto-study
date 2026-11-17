# -*- coding: utf-8 -*-
"""勘探「我的课程」入口：dump 门户可点元素 + 点进班课并抓接口。

与 explore_ai.py 的区别：这个会先把页面上所有可点元素列出来（含坐标），
并截图，便于精确找到「我的课程 → 某门课」的真实入口，
而不是靠猜选择器。

用法：python tools/explore_courses.py
结果：output/portal_*.png / output/portal_dom.json / output/course_api.json
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "https://uai.unipus.cn/portal"
API_HINTS = ("api", "cmgt", "portal_api", "course", "unit", "study",
             "learn", "duration", "progress", "task", "record", "content",
             "activity", "heartbeat", "lesson", "section", "shelf")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map", ".webp")

JS_DOM = r"""
() => {
  const out = [];
  const all = document.querySelectorAll('a, button, [role="button"], li, div, span, img');
  const seen = new Set();
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 30 || r.height < 16) continue;
    if (r.top < 0 || r.top > 3000) continue;
    const txt = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    const alt = el.getAttribute && (el.getAttribute('alt') || '');
    const label = txt || alt;
    if (!label) continue;
    if (el.children.length > 6) continue;          // 跳过超大容器
    const key = label + '|' + Math.round(r.left) + ',' + Math.round(r.top);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      tag: (el.tagName || '').toLowerCase(),
      text: label.slice(0, 30),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 70),
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.top + r.height / 2),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  }
  return out.slice(0, 150);
}
"""


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "INFO")
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    session = BrowserSession(cfg, log)
    page = session.page
    records = []

    def on_resp(resp):
        try:
            url = resp.url or ""
            low = url.lower()
            if any(low.endswith(e) for e in SKIP_EXT):
                return
            if "unipus" not in low:
                return
            if not any(h in low for h in API_HINTS):
                return
            item = {"method": resp.request.method, "url": url[:300],
                    "status": resp.status}
            ct = (resp.headers or {}).get("content-type", "") or ""
            if "json" in ct:
                try:
                    item["resp"] = (resp.text() or "")[:2500]
                except Exception:  # noqa: BLE001
                    pass
            records.append(item)
            log.info(f"API {item['method']} {url[:105]} → {item['status']}")
        except Exception:  # noqa: BLE001
            pass

    page.on("response", on_resp)

    def shot(tag):
        try:
            page.screenshot(path=str(out / f"portal_{tag}.png"), timeout=10000)
        except Exception:  # noqa: BLE001
            pass

    try:
        session.goto(PORTAL, context="portal")
        page.wait_for_timeout(5000)
        shot("01_home")
        log.info(f"标题：{page.title()} | URL：{page.url}")

        dom = page.evaluate(JS_DOM) or []
        (out / "portal_dom.json").write_text(
            json.dumps({"url": page.url, "items": dom},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"页面可点元素 {len(dom)} 个（已存 portal_dom.json）")
        for d in dom[:40]:
            log.info(f"  {d['tag']:6} ({d['x']:>4},{d['y']:>4}) {d['w']}x{d['h']}  {d['text'][:24]}")

        # ---- 找「我的课程」入口并点击 ----
        target = None
        for d in dom:
            if any(k in d["text"] for k in ("我的课程", "课程", "书架", "学习")):
                target = d
                break
        if target:
            log.info(f"点击「{target['text']}」({target['x']},{target['y']})")
            try:
                page.mouse.click(target["x"], target["y"])
            except Exception as exc:  # noqa: BLE001
                log.warning(f"点击失败: {exc}")
            page.wait_for_timeout(5000)
            shot("02_after_courses")
            log.info(f"点击后 URL：{page.url}")

            dom2 = page.evaluate(JS_DOM) or []
            (out / "portal_dom2.json").write_text(
                json.dumps({"url": page.url, "items": dom2},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            log.info(f"新页面可点元素 {len(dom2)} 个")
            for d in dom2[:40]:
                log.info(f"  {d['tag']:6} ({d['x']:>4},{d['y']:>4}) {d['w']}x{d['h']}  {d['text'][:24]}")

            # ---- 再点课程名 ----
            for d in dom2:
                if "视听说" in d["text"] or "听说一级" in d["text"] or "大学英语" in d["text"]:
                    log.info(f"点击课程「{d['text']}」({d['x']},{d['y']})")
                    try:
                        page.mouse.click(d["x"], d["y"])
                    except Exception as exc:  # noqa: BLE001
                        log.warning(f"点击失败: {exc}")
                    page.wait_for_timeout(6000)
                    shot("03_course")
                    log.info(f"课程页 URL：{page.url}")
                    break

        (out / "course_api.json").write_text(json.dumps(
            {"updated": datetime.now().isoformat(timespec="seconds"),
             "url": page.url, "count": len(records), "records": records},
            ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"共记录 {len(records)} 条接口 → output/course_api.json")
        time.sleep(2)
        return 0
    finally:
        try:
            (out / "course_api.json").write_text(json.dumps(
                {"updated": datetime.now().isoformat(timespec="seconds"),
                 "url": page.url, "count": len(records), "records": records},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            session.save_state()
        except Exception:  # noqa: BLE001
            pass
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
