# -*- coding: utf-8 -*-
"""对照 79 个 micro 的蓝圈状态与各自叶子的 pass，找出蓝圈判定规则。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
URL0 = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
        "?cid=1847183133920289074&appid=116#/"
        f"{INST}/courseware/71e31dc39002af7/71e31df040024c9/"
        "71e31e2c1002af7/71e31e4d20024c9")


def main():
    from upc import progress as PG
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import build_leaves

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    leaf_states = {}

    def on_resp(r):
        if "course_progress" not in (r.url or ""):
            return
        try:
            lf = ((r.json().get("rt") or {}).get("leafs") or {})
            if lf:
                leaf_states.update(lf)
        except Exception:  # noqa: BLE001
            pass

    p.on("response", on_resp)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto(URL0, context="课件")
        p.wait_for_timeout(12000)

        raw = p.evaluate(r"""
        async (inst) => {
          const r = await fetch(`/course/api/course/${inst}/default`,
                                {credentials:'include'});
          return await r.json();
        }
        """, INST)
        cs = (raw or {}).get("course")
        struct = json.loads(cs) if isinstance(cs, str) else cs
        leaves, micros = build_leaves(struct)
        rows = PG.scan(p)

        print(f"蓝圈 {sum(1 for r in rows if r['done'])}/{len(rows)}，"
              f"叶子 pass {sum(1 for v in leaf_states.values() if (v.get('state') or {}).get('pass'))}/{len(leaf_states)}")
        print()
        print(" idx | 圈   | 叶数 | pass | 明细")
        print("-----|------|------|------|--------------------------------")
        for j, m in enumerate(micros):
            mine = [x for x in leaves if x.get("micro_idx") == j]
            st = rows[j].get("state") if j < len(rows) else "?"
            det = []
            for x in mine:
                v = leaf_states.get(x["id"]) or {}
                ok = (v.get("state") or {}).get("pass", 0)
                plat = str(v.get("tab_type") or x.get("tab_type") or "?")[:4]
                det.append(f"{plat}:{ok}")
            npass = sum(1 for x in mine
                        if ((leaf_states.get(x["id"]) or {}).get("state") or {}).get("pass"))
            mark = "●" if st == "all" else "○"
            if j < 16 or npass:
                print(f" {j:>3} |  {mark}   | {len(mine):>4} | {npass:>4} | "
                      f"{m[:22]:<23} {' '.join(det)[:70]}")
        (ROOT / "output" / "blue_analysis.json").write_text(json.dumps(
            {"micros": micros, "circles": rows, "leaf_states": leaf_states},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
