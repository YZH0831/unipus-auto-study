# -*- coding: utf-8 -*-
"""从课程管理页点进教材课件，抓「任务点 / 学习时长」接口。

前置：state/api_session.json 存在（跑过 tools/api_run.py）。
用法：python tools/enter_tutorial.py [资源关键词]   默认 nhce_4_vls_1（视听说）
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_HINTS = ("api", "cmgt", "course", "unit", "point", "task", "study",
             "learn", "duration", "progress", "record", "content",
             "activity", "heartbeat", "tutorial", "resource", "node")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map", ".webp")


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "nhce_4_vls_1"

    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "INFO")
    out = Path(cfg.output_dir)

    sess_file = Path(cfg.state_dir) / "api_session.json"
    if not sess_file.exists():
        log.error("缺少 state/api_session.json")
        return 1

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
            item = {"m": r.request.method, "u": u[:320], "s": r.status,
                    "post": (r.request.post_data or "")[:500]}
            if "json" in ((r.headers.get("content-type") or "")):
                item["r"] = (r.text() or "")[:2500]
            records.append(item)
            log.info(f"API {item['m']} {u[:110]} → {item['s']}")
        except Exception:  # noqa: BLE001
            pass

    def hook(pg):
        try:
            pg.on("response", on_resp)
        except Exception:  # noqa: BLE001
            pass

    hook(page)
    session.context.on("page", hook)

    def dump(tag, pg=None):
        pg = pg or page
        try:
            pg.screenshot(path=str(out / f"et_{tag}.png"), timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        try:
            (out / "tutorial_api.json").write_text(json.dumps(
                {"tag": tag, "updated": datetime.now().isoformat(timespec="seconds"),
                 "url": pg.url, "count": len(records), "records": records},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    try:
        sess = json.loads(sess_file.read_text(encoding="utf-8"))
        to_add = [{"name": c["name"], "value": c["value"],
                   "domain": c.get("domain") or "",
                   "path": c.get("path") or "/"}
                  for c in (sess.get("cookies") or []) if c.get("domain")]
        try:
            session.context.add_cookies(to_add)
            log.info(f"注入 {len(to_add)} 个 cookie")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"注入失败: {str(exc)[:100]}")

        session.goto("https://uai.unipus.cn/app/cmgt/course-management", context="课程管理")
        page.wait_for_timeout(8000)
        dump("01_courses")
        log.info(f"课程管理页: {page.url[:120]}")

        # 找封面图（src 含关键词）
        before = {id(p) for p in session.context.pages}
        clicked = False
        try:
            clicked = page.evaluate(r"""
            (key) => {
              const imgs = Array.from(document.querySelectorAll('img'));
              for (const im of imgs) {
                const src = im.src || '';
                if (!src.includes(key)) continue;
                im.setAttribute('data-upc-book', '1');
                return true;
              }
              return false;
            }
            """, key)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"找封面失败: {exc}")

        if clicked:
            log.info("找到教材封面，点击")
            try:
                page.evaluate("() => { const e=document.querySelector('[data-upc-book=\"1\"]');"
                              " if(e){e.scrollIntoView({block:'center'}); e.click();} }")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"点击失败: {exc}")
        else:
            log.warning("没找到封面图，尝试点书名")
            for txt in ("视听说教程1", "读写教程1", "已学任务"):
                try:
                    el = page.query_selector(f'text={txt}')
                    if el is not None and el.is_visible():
                        el.click(timeout=6000)
                        clicked = True
                        log.info(f"已点击「{txt}」")
                        break
                except Exception:  # noqa: BLE001
                    continue

        page.wait_for_timeout(7000)
        dump("02_after_click")
        log.info(f"点击后 URL: {page.url[:130]}")

        # 新标签
        try:
            for p in session.context.pages:
                if id(p) not in before:
                    p.bring_to_front()
                    page = p
                    log.info(f"新标签: {p.url[:130]}")
                    page.wait_for_timeout(6000)
                    dump("03_newtab")
                    break
        except Exception:  # noqa: BLE001
            pass

        # 观察 + 找「开始学习/继续学习」
        for i in range(4):
            for txt in ("开始学习", "继续学习", "进入教程", "教程学习", "开始"):
                try:
                    el = page.query_selector(f'text={txt}')
                    if el is not None and el.is_visible():
                        el.click(timeout=4000)
                        log.info(f"已点击「{txt}」")
                        page.wait_for_timeout(5000)
                        break
                except Exception:  # noqa: BLE001
                    continue
            page.wait_for_timeout(4000)
            dump(f"04_step{i}")

        dump("05_final")
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            log.info("页面文本：")
            for ln in [x.strip() for x in txt.splitlines() if x.strip()][:30]:
                log.info(f"  | {ln[:70]}")
        except Exception:  # noqa: BLE001
            pass
        log.info(f"共 {len(records)} 条接口 → output/tutorial_api.json")
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
