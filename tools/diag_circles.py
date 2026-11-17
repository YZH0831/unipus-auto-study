# -*- coding: utf-8 -*-
"""诊断：把目录里每个「圈」和节点名/状态对应起来。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

URL = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
       "?cid=1847183133920289074&appid=116#/"
       "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205/courseware/"
       "71e31dc39002af7/71e31df040024c9/71e31e2c1002af7/71e31e4d20024c9")

JS = r"""
() => {
  const out = [];
  for (const c of document.querySelectorAll('.pc-slider-menu-status-circle-container')) {
    let p = c.parentElement, name = '', nodeEl = null;
    for (let i = 0; i < 7 && p; i++) {
      const n = p.querySelector && p.querySelector('.pc-menu-node-name');
      if (n && (n.innerText || '').trim()) { name = n.innerText.trim(); nodeEl = p; break; }
      p = p.parentElement;
    }
    const cls = (typeof c.className === 'string' ? c.className : '');
    const all = c.querySelector('.pc-slider-menu-status-circle-all');
    const half = c.querySelector('.pc-slider-menu-status-circle-half');
    const inner = c.querySelector('.pc-slider-menu-status-circle-circle1');
    let bg = '', bd = '';
    try {
      const cs = getComputedStyle(inner || c);
      bg = cs.backgroundColor; bd = cs.borderColor;
    } catch (e) {}
    const attrs = {};
    if (nodeEl) {
      for (const a of Array.from(nodeEl.attributes).slice(0, 12)) {
        if (a.name.startsWith('data-') || ['id','title','href'].includes(a.name))
          attrs[a.name] = String(a.value).slice(0, 40);
      }
    }
    out.push({
      name: name.slice(0, 42),
      state: all ? 'all' : (half ? 'half' : '?'),
      cls: cls.slice(0, 70), bg, bd, attrs,
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
        s.goto(URL, context="课件")
        p.wait_for_timeout(12000)
        rows = p.evaluate(JS) or []
        (ROOT / "output" / "progress_map.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"共 {len(rows)} 个节点；已存 output/progress_map.json")
        done = [r for r in rows if r["state"] == "all"]
        print(f"蓝圈(完成) {len(done)} 个:")
        for r in done:
            print(f"   {r['name'][:36]:<38} bg={r['bg'][:28]:<30} attrs={r['attrs']}")
        print("\n前 16 个节点:")
        for i, r in enumerate(rows[:16], 1):
            print(f"  {i:>2}. [{r['state']:<4}] {r['name'][:34]:<36} "
                  f"bg={r['bg'][:24]:<26} attrs={str(r['attrs'])[:50]}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
