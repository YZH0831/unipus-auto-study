# -*- coding: utf-8 -*-
"""把 API 会话的 cookie 注入浏览器，直接闯进课程应用。

思路：API 登录拿到了 TGC / jwt / ucamUserToken 等 cookie，
如果把它们塞进浏览器上下文，SSO 那层应该会放行，
从而打开 /app/cmgt/course-management 而不再跳登录页。

用法：python tools/inject_session.py
结果：output/inject_*.png + output/inject_api.json
"""
import json
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COURSE_NAME = "新视野大学英语（第四版）视听说教程1"
COURSE_URL = ("https://uai.unipus.cn/app/cmgt/course-management?tutorialName="
              + urllib.parse.quote(COURSE_NAME))
PORTAL = "https://uai.unipus.cn/portal"
API_HINTS = ("api", "cmgt", "course", "unit", "study", "learn", "duration",
             "progress", "task", "content", "activity", "heartbeat", "edx",
             "section", "lesson")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map", ".webp")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "INFO")
    out = Path(cfg.output_dir)

    sess_file = Path(cfg.state_dir) / "api_session.json"
    if not sess_file.exists():
        log.error("没有 API 会话缓存（state/api_session.json），先跑 tools/api_run.py")
        return 1
    sess = json.loads(sess_file.read_text(encoding="utf-8"))
    cookies = sess.get("cookies") or []
    log.info(f"准备注入 {len(cookies)} 个 API cookie")

    session = BrowserSession(cfg, log)
    page = session.page
    records = []

    def on_resp(r):
        try:
            u = r.url or ""
            low = u.lower()
            if any(low.endswith(e) for e in SKIP_EXT):
                return
            if "unipus" not in low:
                return
            if not any(h in low for h in API_HINTS):
                return
            item = {"m": r.request.method, "u": u[:300], "s": r.status,
                    "post": (r.request.post_data or "")[:400]}
            if "json" in ((r.headers.get("content-type") or "")):
                item["r"] = (r.text() or "")[:2000]
            records.append(item)
            log.info(f"API {item['m']} {u[:105]} → {item['s']}")
        except Exception:  # noqa: BLE001
            pass

    page.on("response", on_resp)

    def shot(tag):
        try:
            page.screenshot(path=str(out / f"inject_{tag}.png"), timeout=10000)
        except Exception:  # noqa: BLE001
            pass

    try:
        # 先把 cookie 注入浏览器上下文
        to_add = []
        for c in cookies:
            dom = c.get("domain") or ""
            if not dom:
                continue
            to_add.append({
                "name": c["name"], "value": c["value"],
                "domain": dom if dom.startswith(".") else dom,
                "path": c.get("path") or "/",
                "secure": bool(c.get("secure")),
            })
        try:
            session.context.add_cookies(to_add)
            log.info(f"已注入 {len(to_add)} 个 cookie 到浏览器")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"注入失败: {str(exc)[:120]}")

        # 先去 portal 让站点自检
        session.goto(PORTAL, context="portal")
        page.wait_for_timeout(4000)
        shot("01_portal")
        log.info(f"portal URL: {page.url[:110]}")

        # 再直奔课程应用
        log.info(f"访问课程页: {COURSE_URL[:120]}")
        try:
            page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"打开异常: {str(exc)[:100]}")
        page.wait_for_timeout(8000)
        shot("02_course")
        log.info(f"课程页 URL: {page.url[:140]}")

        # 等 SSO 自行跳回
        for i in range(10):
            cur = page.url or ""
            if "course-management" in cur and "sso" not in cur:
                log.info(f"已进入课程应用 ✓ {cur[:130]}")
                break
            log.info(f"  等待跳转…（{i + 1}/10）{cur[:90]}")
            page.wait_for_timeout(3000)

        shot("03_after")

        # --- 进课程：点「大学英语听说一级」---
        try:
            el = page.query_selector('text=大学英语听说一级')
            if el is None:
                el = page.query_selector('text=大学英语一级')
            if el is not None and el.is_visible():
                el.click(timeout=8000)
                log.info("已点击课程标题")
                page.wait_for_timeout(7000)
                shot("04_course_opened")
                log.info(f"进课后 URL: {page.url[:140]}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"点课程失败: {str(exc)[:100]}")

        # --- 学习页（菜单里的「视听说」路由 /my-activity）---
        for route in ("https://uai.unipus.cn/app/cmgt/my-activity",
                      "https://uai.unipus.cn/app/cmgt/my-activity?courseId=395341",
                      "https://uai.unipus.cn/app/cmgt/my-activity?courseId=395150"):
            try:
                log.info(f"尝试学习页: {route}")
                page.goto(route, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(7000)
                shot("05_activity")
                txt = page.evaluate(
                    "() => document.body ? document.body.innerText : ''") or ""
                lines = [x.strip() for x in txt.splitlines() if x.strip()]
                log.info(f"  页面文本 {len(lines)} 行：")
                for ln in lines[:30]:
                    log.info(f"    | {ln[:70]}")
                if len(lines) > 5:
                    break
            except Exception as exc:  # noqa: BLE001
                log.warning(f"  打开失败: {str(exc)[:90]}")

        # 页面文本
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            log.info("最终页面文本片段：")
            for ln in [x.strip() for x in txt.splitlines() if x.strip()][:30]:
                log.info(f"  | {ln[:70]}")
        except Exception:  # noqa: BLE001
            pass

        (out / "inject_api.json").write_text(json.dumps(
            {"updated": datetime.now().isoformat(timespec="seconds"),
             "url": page.url, "count": len(records), "records": records},
            ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"共 {len(records)} 条接口 → output/inject_api.json")
        time.sleep(2)
        return 0
    finally:
        try:
            session.save_state()
        except Exception:  # noqa: BLE001
            pass
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
