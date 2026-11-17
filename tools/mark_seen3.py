# -*- coding: utf-8 -*-
"""用真实模板给讨论页发 submitType=2「标记已看」。

模板来自 output/submit_tpl.json（真实提交抓包），只改：
  groupId → 讨论叶 id, quesDatas → [], thirdPartyJudges → "[]", submitType → 2
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
DISC_GROUP = "706f6b62003277d"
DISC_PATH = "74112ed2a002c50/706f6b62001277d/706f6b62002277d/706f6b62003277d"
URL = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
       f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{DISC_PATH}")

JS = r"""
async (args) => {
  const [hdr, base, group] = args;
  const out = [];
  const variants = [
    {name: 'submitType=2 空quesDatas',
     body: {...base, groupId: group, quesDatas: [], isCompleted: [true],
            thirdPartyJudges: "[]", submitType: 2}},
    {name: 'submitType=1 空quesDatas',
     body: {...base, groupId: group, quesDatas: [], isCompleted: [true],
            thirdPartyJudges: "[]", submitType: 1}},
  ];
  for (const v of variants) {
    try {
      const r = await fetch('/course/api/v3/newExploration/submit', {
        method: 'POST', credentials: 'include', headers: hdr,
        body: JSON.stringify(v.body),
      });
      const t = await r.text();
      out.push({name: v.name, status: r.status, body: t.slice(0, 500)});
      const j = (() => { try { return JSON.parse(t); } catch (e) { return {}; } })();
      if (j.code === 0) { out[out.length - 1].OK = true; break; }
      if (j.code === 600001 || j.code === 600002) break;   // 限频就停
    } catch (e) {
      out.push({name: v.name, error: String(e).slice(0, 150)});
    }
  }
  return out;
}
"""


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import _clear_modals

    tpl = json.loads((ROOT / "output" / "submit_tpl.json").read_text(encoding="utf-8"))
    hdr = tpl["hdr"]
    body = tpl["body"]
    base = {k: v for k, v in body.items()
            if k in ("courseId", "openId", "version", "hideLoading",
                     "associationGroupId")}
    print("基础字段:", json.dumps(base, ensure_ascii=False))

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
        s.goto(URL, context="Discussion 1")
        p.wait_for_timeout(9000)
        _clear_modals(p, log)
        body_txt = p.evaluate("() => document.body ? document.body.innerText : ''") or ""
        if "密码登录" in body_txt:
            print("!! 会话过期")
            return 2

        res = p.evaluate(JS, [hdr, base, DISC_GROUP]) or []
        print("\n=== 结果 ===")
        for r in res:
            print(f"\n[{r.get('name')}] status={r.get('status')} "
                  f"{'✓ OK' if r.get('OK') else ''}")
            print("  ", (r.get("body") or r.get("error") or "")[:420])
        (ROOT / "output" / "markseen_result.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
