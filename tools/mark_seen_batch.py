# -*- coding: utf-8 -*-
"""批量给讨论页发 submitType=2 标记已看（间隔 2.5s，避免限频）。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
URL = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
       f"?cid=1847183133920289074&appid=116#/{INST}/courseware/"
       "74112ed2a002c50/706f6b62001277d/706f6b62002277d/706f6b62003277d")

JS_ONE = r"""
async (args) => {
  const [hdr, base, group] = args;
  const body = {...base, groupId: group, quesDatas: [], isCompleted: [true],
                thirdPartyJudges: "[]", submitType: 2};
  try {
    const r = await fetch('/course/api/v3/newExploration/submit', {
      method: 'POST', credentials: 'include', headers: hdr,
      body: JSON.stringify(body),
    });
    const t = await r.text();
    let j = {};
    try { j = JSON.parse(t); } catch (e) {}
    return {group, code: j.code, msg: j.message || j.msg || '', raw: t.slice(0, 200)};
  } catch (e) { return {group, error: String(e).slice(0, 120)}; }
}
"""


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import _clear_modals, build_leaves

    tpl = json.loads((ROOT / "output" / "submit_tpl.json").read_text(encoding="utf-8"))
    hdr = tpl["hdr"]
    base = {k: v for k, v in tpl["body"].items()
            if k in ("courseId", "openId", "version", "hideLoading",
                     "associationGroupId")}

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto(URL, context="Discussion")
        p.wait_for_timeout(9000)
        _clear_modals(p, log)

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
        # micro 3 里所有 discussion 叶
        targets = [x for x in leaves
                   if x.get("micro_idx") == 3 and x.get("tab_type") == "discussion"]
        print(f"micro 3 讨论页 {len(targets)} 个")

        done = 0
        for i, x in enumerate(targets, 1):
            r = p.evaluate(JS_ONE, [hdr, base, x["id"]]) or {}
            ok = r.get("code") == 0
            print(f"  [{i}/{len(targets)}] {x['name'][:14]:<16} {x['id']} "
                  f"→ code={r.get('code')} {r.get('msg') or r.get('error') or ''}")
            if ok:
                done += 1
            elif r.get("code") in (600001, 600002):
                print("     触发限频，停止")
                break
            time.sleep(2.5)
        print(f"\n成功 {done}/{len(targets)}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
