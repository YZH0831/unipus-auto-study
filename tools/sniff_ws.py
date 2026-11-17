# -*- coding: utf-8 -*-
"""监听课件页 WebSocket 帧，找出 pass/完成上报协议。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
PATH = "74112ed2a002c50/706f6b62001277d/706f74aee00277d/706f74aee01277d"
URL = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
       f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{PATH}")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import JS_FIND_SUBMIT

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    frames = []
    ws_urls = []

    def on_ws(ws):
        ws_urls.append(ws.url)
        print("WS 建立:", ws.url[:150])
        ws.on("framesent", lambda payload: frames.append({"dir": "SEND", "data": str(payload)[:1200]}))
        ws.on("framereceived", lambda payload: frames.append({"dir": "RECV", "data": str(payload)[:1200]}))
        ws.on("close", lambda _: print("WS 关闭"))

    p.on("websocket", on_ws)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto(URL, context="Exercise 1")
        p.wait_for_timeout(10000)
        print("WS 数量:", len(ws_urls))
        print("初始帧数:", len(frames))

        mark = len(frames)
        # 点选项 + 提交
        for o in p.query_selector_all(".option"):
            try:
                o.click(timeout=2500)
            except Exception:  # noqa: BLE001
                pass
        p.wait_for_timeout(500)
        r = p.evaluate(JS_FIND_SUBMIT) or {}
        print("提交按钮:", r.get("hit"))
        if r.get("hit"):
            b = p.query_selector('[data-upc-submit="1"]')
            if b:
                try:
                    b.click(timeout=6000)
                    print("已提交")
                except Exception as exc:  # noqa: BLE001
                    print("提交失败:", str(exc)[:100])
        p.wait_for_timeout(8000)

        print("\n=== 提交后新增帧 ===")
        for f in frames[mark:]:
            print(f"  {f['dir']}: {f['data'][:500]}")
        print("\n=== 全部帧（最多 30）===")
        for f in frames[:30]:
            print(f"  {f['dir']}: {f['data'][:300]}")
        (ROOT / "output" / "ws_frames.json").write_text(
            json.dumps({"urls": ws_urls, "frames": frames},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
