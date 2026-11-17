# -*- coding: utf-8 -*-
"""点进「我的教程」里的指定教材，并抓取课程页接口。

上一版失败原因：点到了外层 1629px 宽的容器。这次改成
"在包含关键词的所有元素里挑尺寸最小的那个"点，命中率更高。

用法：python tools/open_course.py [关键词]    默认 "视听说"
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "https://uai.unipus.cn/portal"
API_HINTS = ("api", "cmgt", "portal_api", "course", "unit", "study",
             "learn", "duration", "progress", "task", "record", "content",
             "activity", "heartbeat", "lesson", "section", "shelf", "edx")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map", ".webp")

JS_FIND_SMALLEST = r"""
(key) => {
  const all = document.querySelectorAll('div, li, a, img, span');
  let best = null, bestArea = Infinity;
  for (const el of all) {
    const t = (el.innerText || '') + ' ' + (el.getAttribute('alt') || '');
    if (!t.includes(key)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 40) continue;
    if (r.width > 700 || r.height > 700) continue;   // 排除整块区域
    const area = r.width * r.height;
    if (area < bestArea) { bestArea = area; best = el; }
  }
  if (!best) return null;
  best.setAttribute('data-upc-hit', '1');
  const r = best.getBoundingClientRect();
  return {tag: best.tagName.toLowerCase(),
          cls: (typeof best.className === 'string' ? best.className : '').slice(0,80),
          text: (best.innerText || '').trim().slice(0,40),
          x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
          w: Math.round(r.width), h: Math.round(r.height), area: Math.round(bestArea)};
}
"""


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "视听说"

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
            item = {"method": resp.request.method, "url": url[:320],
                    "status": resp.status,
                    "post": (resp.request.post_data or "")[:500]}
            ct = (resp.headers or {}).get("content-type", "") or ""
            if "json" in ct:
                try:
                    item["resp"] = (resp.text() or "")[:3000]
                except Exception:  # noqa: BLE001
                    pass
            records.append(item)
            log.info(f"API {item['method']} {url[:110]} → {item['status']}")
        except Exception:  # noqa: BLE001
            pass

    page.on("response", on_resp)

    def dump(tag):
        try:
            page.screenshot(path=str(out / f"oc_{tag}.png"), timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        try:
            (out / "course_api.json").write_text(json.dumps(
                {"tag": tag, "updated": datetime.now().isoformat(timespec="seconds"),
                 "url": page.url, "count": len(records), "records": records},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    try:
        session.goto(PORTAL, context="portal")
        page.wait_for_timeout(5000)
        dump("01_home")

        # 滚到「我的教程」区域
        try:
            page.evaluate("() => window.scrollTo(0, 420)")
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

        hit = page.evaluate(JS_FIND_SMALLEST, key)
        log.info(f"命中的最小元素: {hit}")
        if not hit:
            log.error(f"页面上找不到含「{key}」的卡片")
            return 1

        # 用 JS 直接 click 元素（比坐标点击更稳）
        try:
            page.evaluate("() => { const e = document.querySelector('[data-upc-hit=\"1\"]'); "
                          "if (e) { e.scrollIntoView({block:'center'}); e.click(); } }")
            log.info("已 JS 点击卡片")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"JS 点击失败，改用坐标: {exc}")
            try:
                page.mouse.click(hit["x"], hit["y"])
            except Exception:  # noqa: BLE001
                pass

        # 点之前先记下已有标签，便于识别新开的
        before_pages = {id(p) for p in session.context.pages}

        page.wait_for_timeout(6000)
        dump("02_after_click")
        log.info(f"点击后 URL：{page.url}")

        # --- 切到新标签（课程页通常在新标签打开）---
        newp = None
        try:
            for p in session.context.pages:
                if id(p) not in before_pages:
                    newp = p
                    break
            if newp is not None:
                log.info(f"发现新标签页：{newp.url[:130]}")
                newp.bring_to_front()
                page = newp
        except Exception as exc:  # noqa: BLE001
            log.warning(f"切标签失败: {exc}")

        # --- 等它从 SSO 跳回来（会带 ticket 再跳回 course-management）---
        for i in range(20):
            try:
                cur = page.url or ""
            except Exception:  # noqa: BLE001
                cur = ""
            if "course-management" in cur or "/app/" in cur:
                log.info(f"已进入课程应用：{cur[:140]}")
                break
            if "sso.unipus.cn" in cur:
                log.info(f"  等 SSO 跳回…（{i + 1}/20）{cur[:90]}")
            page.wait_for_timeout(2000)

        try:
            dump("03_course")
            log.info(f"课程页 URL：{page.url}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"课程页截图失败: {exc}")

        # --- 在课程页里找「教程学习 / 开始学习」---
        for txt in ("教程学习", "开始学习", "继续学习", "进入教程", "课程学习", "开始上课"):
            try:
                el = page.query_selector(f'text={txt}')
                if el is not None and el.is_visible():
                    el.click(timeout=5000)
                    log.info(f"已点击「{txt}」")
                    page.wait_for_timeout(6000)
                    dump("04_entered")
                    break
            except Exception:  # noqa: BLE001
                continue

        # --- 再多观察一会儿，收集课件/时长相关接口 ---
        for i in range(5):
            try:
                page.wait_for_timeout(4000)
                dump(f"05_watch{i}")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"观察中断: {str(exc)[:80]}")
                break

        try:
            log.info(f"最终 URL：{page.url}")
        except Exception:  # noqa: BLE001
            pass
        log.info(f"共 {len(records)} 条接口 → output/course_api.json")
        return 0
    finally:
        dump("end")
        try:
            session.save_state()
        except Exception:  # noqa: BLE001
            pass
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
