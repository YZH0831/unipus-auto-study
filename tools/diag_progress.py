# -*- coding: utf-8 -*-
"""诊断：课件目录里任务点前面的「白圈/蓝圈」完成标记。

目标：找出圆圈元素的 class / 计算样式 / 状态属性，
供 study_ai.py 判断任务是否真的被平台记为完成。
"""
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
  const out = {circles: [], treeItems: [], classFreq: {}};
  const roundish = /circle|dot|point|status|progress|icon|node|state|finish|done|check|badge|mark|flag|tag/i;

  // 1) 所有小方块/圆形元素（可能是圈）
  for (const el of document.querySelectorAll('i,span,div,em,b,svg,circle')) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.width > 40 || r.height < 4 || r.height > 40) continue;
    const cls = (typeof el.className === 'string' ? el.className :
                 (el.className && el.className.baseVal) || '');
    const cs = getComputedStyle(el);
    const bg = cs.backgroundColor || '';
    const bd = cs.borderColor || '';
    const br = cs.borderRadius || '';
    const isRound = /50%|999px/.test(br) || (Math.abs(r.width - r.height) < 6);
    if (!isRound) continue;
    // 只保留像"圈"的（有边框或圆角或 class 命中）
    if (!roundish.test(cls) && !/rgb/.test(bd) && !/rgb/.test(bg)) continue;
    out.circles.push({
      cls: String(cls).slice(0, 90),
      w: Math.round(r.width), h: Math.round(r.height),
      bg, bd, color: cs.color, br,
      attrs: Array.from(el.attributes).slice(0, 8)
              .map(a => a.name + '=' + String(a.value).slice(0, 30)),
      parentText: (el.parentElement?.innerText || '').trim().slice(0, 50),
    });
  }
  out.circles = out.circles.slice(0, 60);

  // 2) 目录项：含 unit / task 文本的元素，看它旁边有什么标记
  const keys = ['Course preview', 'Learning objectives', 'Opening up',
                'Listening', 'Discussion', 'Exercise', 'Wrapping up'];
  for (const el of document.querySelectorAll('li,div,span,a')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 60) continue;
    if (!keys.some(k => t.includes(k))) continue;
    if (el.children.length > 4) continue;
    const kids = Array.from(el.querySelectorAll('*')).slice(0, 12).map(c => {
      const cl = (typeof c.className === 'string' ? c.className :
                  (c.className && c.className.baseVal) || '');
      const r = c.getBoundingClientRect();
      let bg = '', bd = '';
      try { const cs = getComputedStyle(c); bg = cs.backgroundColor; bd = cs.borderColor; } catch(e){}
      return {tag: c.tagName.toLowerCase(), cls: String(cl).slice(0, 50),
              w: Math.round(r.width), h: Math.round(r.height), bg, bd};
    });
    out.treeItems.push({
      tag: el.tagName.toLowerCase(),
      text: t.slice(0, 45),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
      kids,
    });
  }
  out.treeItems = out.treeItems.slice(0, 30);

  // 3) 统计所有含状态关键词的 class，找可能的"已完成"类名
  for (const el of document.querySelectorAll('[class]')) {
    const cl = typeof el.className === 'string' ? el.className : '';
    for (const c of cl.split(/\s+/)) {
      if (roundish.test(c) || /complete|finish|done|learned|passed|active|current|blue|unread/i.test(c)) {
        out.classFreq[c] = (out.classFreq[c] || 0) + 1;
      }
    }
  }
  out.classFreq = Object.entries(out.classFreq)
    .sort((a,b) => b[1]-a[1]).slice(0, 50);
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

        data = p.evaluate(JS) or {}
        (ROOT / "output" / "progress_dom.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("结果已存 output/progress_dom.json")
        print(f"圆圈候选 {len(data.get('circles') or [])} 个")
        for c in (data.get("circles") or [])[:14]:
            print(f"  {c['cls'][:52]:<54} {c['w']}x{c['h']} bg={c['bg'][:26]:<28} "
                  f"bd={c['bd'][:22]:<24} text={c['parentText'][:26]}")
        print(f"\n目录项 {len(data.get('treeItems') or [])} 个")
        for t in (data.get("treeItems") or [])[:8]:
            print(f"  {t['cls'][:44]:<46} :: {t['text'][:34]}")
            for k in t["kids"][:4]:
                print(f"      {k['tag']:<6} {k['cls'][:38]:<40} "
                      f"{k['w']}x{k['h']} bg={k['bg'][:22]}")
        print("\n状态类频率 TOP20:")
        for k, v in (data.get("classFreq") or [])[:20]:
            print(f"  {v:>4}  {k[:70]}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
