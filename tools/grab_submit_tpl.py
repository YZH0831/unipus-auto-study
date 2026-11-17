# -*- coding: utf-8 -*-
"""抓一次完整 submit body，再照抄结构给讨论页发 submitType=2。

原则：全程只提交两次（1 次抓模板 + 1 次讨论页），避免再触发限频。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
EX_PATH = "74112ed2a002c50/706f6b62001277d/706f74aee00277d/706f74aee01277d"
EX_GROUP = "706f74aee01277d"
DISC_GROUP = "706f6b62003277d"
DISC_PATH = "74112ed2a002c50/706f6b62001277d/706f6b62002277d/706f6b62003277d"


def url_of(path):
    return ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
            f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{path}")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import (JS_FIND_SUBMIT, JS_QUIZ_STATE, _apply_choice,
                          _clear_modals)

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    captured = {}

    def on_req(r):
        if "/newExploration/submit" in (r.url or "") and "body" not in captured:
            captured["body"] = r.post_data or ""
            captured["hdr"] = {k: v for k, v in (r.headers or {}).items()
                               if k.lower() not in ("content-length", "host")}

    def on_resp(r):
        if "/newExploration/submit" in (r.url or ""):
            try:
                captured.setdefault("resp", []).append(r.json())
            except Exception:  # noqa: BLE001
                pass

    p.on("request", on_req)
    p.on("response", on_resp)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])

        # ---- ① 抓完整 body（用题库答案，提交一次） ----
        s.goto(url_of(EX_PATH), context="Exercise 1")
        p.wait_for_timeout(10000)
        _clear_modals(p, log)
        body_txt = p.evaluate("() => document.body ? document.body.innerText : ''") or ""
        if "密码登录" in body_txt:
            print("!! 会话过期，需要重新登录")
            return 2

        groups = p.evaluate(JS_QUIZ_STATE) or []
        print(f"题块 {len(groups)}")
        if groups:
            _apply_choice(p, log, groups[0], {0, 2, 4, 5})
            p.wait_for_timeout(500)
        r = p.evaluate(JS_FIND_SUBMIT) or {}
        if r.get("hit"):
            b = p.query_selector('[data-upc-submit="1"]')
            if b:
                b.click(timeout=6000)
        p.wait_for_timeout(6000)

        full = captured.get("body") or ""
        print(f"\n=== 完整 body（{len(full)} 字符）===")
        print(full)
        resp = (captured.get("resp") or [{}])[0]
        print("\n=== 提交响应 code ===", resp.get("code"), "|", resp.get("message"))

        if not full:
            print("没抓到 body")
            return 1
        try:
            tpl = json.loads(full)
        except Exception as exc:  # noqa: BLE001
            print("body 解析失败:", exc)
            return 1
        print("\n=== body 顶层字段 ===")
        for k, v in tpl.items():
            s2 = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            print(f"  {k}: {s2[:160]}")

        (ROOT / "output" / "submit_tpl.json").write_text(
            json.dumps({"body": tpl, "hdr": captured.get("hdr"),
                        "resp": resp}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("\n模板已存 output/submit_tpl.json")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
