# -*- coding: utf-8 -*-
"""抓真实提交请求的 headers，再用它发 submitType=2 标记已看。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
EX_PATH = "74112ed2a002c50/706f6b62001277d/706f74aee00277d/706f74aee01277d"
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
    hdrs = {}

    def on_req(r):
        if "/newExploration/submit" in (r.url or ""):
            hdrs.update({k: v for k, v in (r.headers or {}).items()
                         if k.lower() not in ("content-length", "host")})
            hdrs["__body__"] = (r.post_data or "")[:800]

    p.on("request", on_req)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])

        # ① 在选择题页触发一次真实提交，抓 headers
        s.goto(url_of(EX_PATH), context="Exercise 1")
        p.wait_for_timeout(10000)
        _clear_modals(p, log)
        groups = p.evaluate(JS_QUIZ_STATE) or []
        print(f"题块 {len(groups)}")
        if groups:
            # 用题库里的正确答案，避免浪费一次错误提交
            bank = {}
            try:
                bank = json.loads((ROOT / "data" / "answer_bank.json")
                                  .read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
            letters = (bank.get("706f74aee01277d") or {}).get("letters") or []
            idxs = {ord(x.upper()) - 65 for x in letters
                    if x and x[0].isalpha()} if letters else {0, 2, 4, 5}
            _apply_choice(p, log, groups[0], idxs)
            p.wait_for_timeout(500)
        r = p.evaluate(JS_FIND_SUBMIT) or {}
        if r.get("hit"):
            b = p.query_selector('[data-upc-submit="1"]')
            if b:
                b.click(timeout=6000)
        p.wait_for_timeout(6000)

        print("\n=== 真实提交 headers ===")
        for k, v in hdrs.items():
            if k == "__body__":
                print(f"  body: {v[:300]}")
            else:
                print(f"  {k}: {str(v)[:90]}")

        if not hdrs:
            print("没抓到 headers")
            return 1

        # ② 用同样的 headers 去 discussion 页发 submitType=2
        hdr = {k: v for k, v in hdrs.items() if k != "__body__"}
        s.goto(url_of(DISC_PATH), context="Discussion 1")
        p.wait_for_timeout(9000)
        _clear_modals(p, log)
        out = p.evaluate(r"""
        async (args) => {
          const [hdr, group, inst] = args;
          const res = [];
          const OID = "729181953498463d9723ad0860b4625f";
          const base = {openId: OID, courseId: inst, course_id: inst,
                        groupId: group, group_id: group,
                        thirdPartyJudges: "[]", hideLoading: false,
                        associationGroupId: "", associationUnitId: ""};
          const bodies = [
            {...base, quesDatas: [], isCompleted: [], submitType: 2},
            {...base, quesDatas: [], isCompleted: [true], submitType: 2},
            {...base, quesDatas: [], isCompleted: [], submitType: 1},
            {...base, quesDatas: [], isCompleted: [true], submitType: 1},
          ];
          for (let i = 0; i < bodies.length; i++) {
            try {
              const r = await fetch('/course/api/v3/newExploration/submit', {
                method: 'POST', credentials: 'include', headers: hdr,
                body: JSON.stringify(bodies[i]),
              });
              const t = await r.text();
              res.push({i, status: r.status, body: t.slice(0, 600)});
            } catch (e) { res.push({i, error: String(e).slice(0, 150)}); }
          }
          return res;
        }
        """, [hdr, DISC_GROUP]) or []
        print("\n=== 标记已看结果 ===")
        for x in out:
            print(f"\n[{x.get('i')}] status={x.get('status')}")
            print("  ", (x.get("body") or x.get("error") or "")[:450])
        (ROOT / "output" / "markseen.json").write_text(
            json.dumps({"hdrs": hdr, "res": out}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
