# -*- coding: utf-8 -*-
"""AI版（uai.unipus.cn）课程与学习接口勘探。

目标：找出 AI 版"进入课程 → 单元 → 学习页"的接口链路，
特别是学习时长上报机制，从而完全绕开老版网页的学校认证墙。

已知（2026-11-04 实测）：
    GET /api/account/user/info          → 用户含 school=8494、学号、姓名
    GET /api/cmgt/course/my/bookshelf   → 书架（含 resourceId / instanceId / classCourses）
    GET /api/portal_api/modules/student → 首页模块

用法：python tools/explore_ai.py
结果：output/ai_explore.json + output/ai_*.png
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "https://uai.unipus.cn/portal"
API_HINTS = ("api", "rpc", "graphql", "cmgt", "portal_api", "course",
             "unit", "study", "learn", "duration", "progress", "task",
             "record", "content", "activity", "heartbeat")
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
            if "unipus" not in low and "res.unipus" not in low:
                return
            if not any(h in low for h in API_HINTS):
                return
            item = {"method": resp.request.method, "url": url[:320],
                    "status": resp.status,
                    "post": (resp.request.post_data or "")[:600]}
            ct = (resp.headers or {}).get("content-type", "") or ""
            if "json" in ct:
                try:
                    item["resp"] = (resp.text() or "")[:2000]
                except Exception:  # noqa: BLE001
                    pass
            records.append(item)
            log.info(f"API {item['method']} {url[:110]} → {item['status']}")
        except Exception:  # noqa: BLE001
            pass

    page.on("response", on_resp)

    def dump(tag):
        try:
            (out / "ai_explore.json").write_text(json.dumps(
                {"tag": tag, "updated": datetime.now().isoformat(timespec="seconds"),
                 "url": page.url, "count": len(records), "records": records},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            page.screenshot(path=str(out / f"ai_{tag}.png"), timeout=8000)
        except Exception:  # noqa: BLE001
            pass

    try:
        log.info(f"打开 {PORTAL}")
        session.goto(PORTAL, context="portal")
        page.wait_for_timeout(4000)
        log.info(f"标题：{page.title()}")
        dump("portal")

        # ---- 直接在页面上下文里调书架接口，拿到课程清单 ----
        try:
            shelf = page.evaluate(r"""
            async () => {
              const r = await fetch('/api/cmgt/course/my/bookshelf', {credentials:'include'});
              return await r.json();
            }
            """)
            (out / "ai_bookshelf.json").write_text(
                json.dumps(shelf, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("书架接口调用成功，已存 output/ai_bookshelf.json")
            groups = ((shelf or {}).get("value") or {}).get("groups") or {}
            for gname, items in groups.items():
                for it in items or []:
                    log.info(f"  [{gname}] {it.get('name')}  instanceId={it.get('instanceId')}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"书架接口调用失败: {str(exc)[:120]}")

        # ---- 尝试点进第一本书 ----
        clicked = False
        for sel in ('text=新视野大学英语（第四版）视听说教程1',
                    'text=视听说教程1',
                    '[class*="book"] img',
                    '[class*="course"] img'):
            try:
                el = page.query_selector(sel)
                if el is not None and el.is_visible():
                    el.click(timeout=6000)
                    log.info(f"已点击课程入口（{sel}）")
                    page.wait_for_timeout(5000)
                    clicked = True
                    break
            except Exception:  # noqa: BLE001
                continue
        dump("after_click" if clicked else "no_click")
        if not clicked:
            log.warning("没点到课程卡片，请手动在窗口里点进课程")

        # ---- 再等一会儿，收集进入课程后的接口 ----
        for i in range(6):
            page.wait_for_timeout(4000)
            dump(f"step{i}")
            log.info(f"当前页面：{page.url[:110]}")
            # 试着找「教程学习 / 开始学习」按钮
            for txt in ("教程学习", "开始学习", "继续学习", "进入课程"):
                try:
                    el = page.query_selector(f'text={txt}')
                    if el is not None and el.is_visible():
                        el.click(timeout=5000)
                        log.info(f"已点击「{txt}」")
                        page.wait_for_timeout(5000)
                        break
                except Exception:  # noqa: BLE001
                    continue

        dump("final")
        log.info(f"共记录 {len(records)} 条接口，结果见 output/ai_explore.json")
        log.info(f"最终页面：{page.url[:120]}")
        page.wait_for_timeout(2000)
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
