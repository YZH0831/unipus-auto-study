# -*- coding: utf-8 -*-
"""诊断口语题页面的录音实现，并试探能否劫持 getUserMedia。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"

JS_PROBE = r"""
() => {
  const out = {apis: {}, buttons: [], text: ''};
  out.apis.hasMediaRecorder = typeof MediaRecorder !== 'undefined';
  out.apis.hasGUM = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  out.apis.hasSOE = !!(window.SOE || window.TencentSOE || window.soe);
  out.apis.winKeys = Object.keys(window).filter(k =>
      /soe|record|speech|audio|recorder/i.test(k)).slice(0, 20);

  // 录音相关元素
  for (const e of document.querySelectorAll('*')) {
    const cls = typeof e.className === 'string' ? e.className : '';
    if (!/record|voice|speak|mic|audio-record/i.test(cls)) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) continue;
    out.buttons.push({
      tag: e.tagName.toLowerCase(), cls: cls.slice(0, 60),
      t: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      w: Math.round(r.width), h: Math.round(r.height),
    });
    if (out.buttons.length > 20) break;
  }
  const b = document.body ? document.body.innerText : '';
  const i = b.indexOf('Directions:');
  out.text = (i >= 0 ? b.slice(i, i + 500) : b.slice(0, 400)).replace(/\s+/g, ' ');
  return out;
}
"""


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import _clear_modals, build_leaves

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    reqs = []
    p.on("request", lambda r: reqs.append(r.url) if any(
        k in (r.url or "") for k in ("soe", "speech", "eval", "record")) else None)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid=1847183133920289074&appid=116#/{INST}/courseware/"
               "71e31dc39002af7/71e31df040024c9/71e31e2c1002af7/71e31e4d20024c9",
               context="课件")
        p.wait_for_timeout(7000)
        raw = p.evaluate(r"""
        async (inst) => {
          const r = await fetch(`/course/api/course/${inst}/default`,
                                {credentials:'include'});
          return await r.json();
        }
        """, INST)
        cs = (raw or {}).get("course")
        struct = json.loads(cs) if isinstance(cs, str) else cs
        leaves, _m = build_leaves(struct)

        # m4 的口语题：Vocabulary / Exercise 1 / Exercise 2
        targets = [x for x in leaves
                   if x.get("micro_idx") == 4 and x.get("tab_type") == "task"
                   and any(k in str(x.get("name")) for k in ("Vocabulary",
                                                             "Exercise 1", "Exercise 2"))]
        print(f"m4 口语候选 {len(targets)} 个")
        for t in targets:
            print("  ", t["name"], t["id"])

        if not targets:
            return 1
        tgt = targets[0]
        url = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{tgt['path']}")
        s.goto(url, context=tgt["name"])
        p.wait_for_timeout(10000)
        _clear_modals(p, log)
        p.wait_for_timeout(800)

        d = p.evaluate(JS_PROBE) or {}
        print("\n=== 录音 API ===")
        print(" ", json.dumps(d.get("apis"), ensure_ascii=False))
        print("\n=== 录音相关元素 ===")
        for x in (d.get("buttons") or [])[:12]:
            print(f"  {x['tag']:<5} {x['w']:>4}x{x['h']:<4} {x['cls'][:46]:<48} {x['t'][:26]}")
        print("\n=== 题目文本 ===")
        print(" ", (d.get("text") or "")[:320])
        print("\n=== 录音相关请求 ===")
        print(" ", reqs[:5] if reqs else "（暂未捕获）")
        (ROOT / "output" / "voice_probe.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
