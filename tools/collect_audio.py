# -*- coding: utf-8 -*-
"""遍历指定 micro 范围内的叶子，抓每个页面的音频 URL 与选项文本。

产出 data/audio_index.json：{叶子id: {name, audio:[url], options:[...], stem}}
供后续逐条转录 + 答题使用。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
CID = "1847183133920289074"
CW = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
      f"?cid={CID}&appid=116#/{INST}/courseware/{{path}}")

JS_GRAB = r"""
() => {
  const out = {audio: [], video: [], options: [], text: ''};
  for (const el of document.querySelectorAll('audio,video')) {
    const s = el.currentSrc || el.getAttribute('src') || '';
    if (s) out.audio.push(s);
    for (const so of el.querySelectorAll('source')) {
      if (so.src) out.audio.push(so.src);
    }
  }
  for (const el of document.querySelectorAll('.option')) {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t) out.options.push(t.slice(0, 200));
  }
  out.text = (document.querySelector(
      '.ant-layout-content, .pc-content-container, body')?.innerText || '')
      .replace(/\s+/g, ' ').slice(0, 1200);
  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--micro-from", type=int, default=0)
    ap.add_argument("--micro-to", type=int, default=13)
    args = ap.parse_args()

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
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        # 先落到课件域拿结构
        s.goto(CW.format(path="71e31dc39002af7/71e31df040024c9/"
                              "71e31e2c1002af7/71e31e4d20024c9"), context="课件")
        p.wait_for_timeout(6000)
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

        out = {}
        todo = [x for x in leaves
                if args.micro_from <= (x.get("micro_idx") or 0) <= args.micro_to
                and x.get("tab_type") in ("task", "video", "check", "text")]
        print(f"待扫描 {len(todo)} 个叶子")
        for i, x in enumerate(todo, 1):
            try:
                p.goto("about:blank", timeout=10000)
                p.goto(CW.format(path=x["path"]), wait_until="domcontentloaded", timeout=45000)
                p.wait_for_timeout(3500)
                p.evaluate("""() => document.querySelectorAll(
                    '.ant-modal-mask,.ant-modal-wrap').forEach(e=>e.remove())""")
                g = p.evaluate(JS_GRAB) or {}
                rec = {"name": x.get("name"), "micro_idx": x.get("micro_idx"),
                       "type": x.get("tab_type"), "path": x["path"],
                       "audio": list(dict.fromkeys(g.get("audio") or [])),
                       "options": g.get("options") or [],
                       "text": (g.get("text") or "")[:800]}
                out[x["id"]] = rec
                n_a = len(rec["audio"])
                n_o = len(rec["options"])
                print(f"  [{i}/{len(todo)}] m{x.get('micro_idx'):<2} "
                      f"{str(x.get('name'))[:20]:<22} 音频{n_a} 选项{n_o}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}] {x.get('name')} 失败: {str(exc)[:70]}")

        dst = ROOT / "data" / "audio_index.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        na = sum(1 for v in out.values() if v["audio"])
        print(f"\n完成：{len(out)} 个叶子，其中 {na} 个含音频 → {dst}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
