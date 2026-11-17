# -*- coding: utf-8 -*-
"""诊断：点选选项后 class 如何变化 + 题块分组方式。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
PATH = "74112ed2a002c50/706f6b62001277d/706f74aee00277d/706f74aee01277d"
URL = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
       f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{PATH}")

JS = r"""
() => {
  const out = {groups: [], options: []};
  // 可能的题块容器
  for (const q of document.querySelectorAll(
      '.question-wrap, [class*="question-common-abs-question"], '
      + '[class*="question-container"], [class*="question-basic"]')) {
    const opts = q.querySelectorAll('.option');
    if (!opts.length) continue;
    out.groups.push({
      cls: String(q.className).slice(0, 70),
      n: opts.length,
      stem: (q.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),
    });
  }
  for (const o of document.querySelectorAll('.option')) {
    out.options.push({
      cls: String(o.className),
      t: (o.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50),
    });
  }
  return out;
}
"""


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

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
        s.goto(URL, context="Exercise 1")
        p.wait_for_timeout(9000)
        p.evaluate("""() => document.querySelectorAll('.ant-modal-mask,.ant-modal-wrap')
                       .forEach(e=>e.remove())""")
        p.wait_for_timeout(500)

        print("=== 点之前 ===")
        d = p.evaluate(JS) or {}
        print("题块:", json.dumps(d.get("groups"), ensure_ascii=False)[:600])
        print("选项 class 样例:", [o["cls"] for o in (d.get("options") or [])[:3]])

        # 点第一个选项
        opts = p.query_selector_all(".option")
        if opts:
            opts[0].click(timeout=4000)
            p.wait_for_timeout(1200)
        print("\n=== 点第一个后 ===")
        d2 = p.evaluate(JS) or {}
        for o in (d2.get("options") or [])[:6]:
            print(f"  cls={o['cls'][:60]:<62} {o['t'][:40]}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
