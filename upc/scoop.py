# -*- coding: utf-8 -*-
"""下拉填空题（fe-scoop）：选项藏在 DOM 里，程序此前完全没识别。

现场：读写教程 Collocation/Practicing，题干 "choose do or make"，
每个空是 <span class="fe-scoop" data-scoop-index="N">，结构：

    <span class="fe-scoop" data-scoop-index="0">
      <span class="scoop-select-wrapper">
        <div style="visibility:hidden">        <- 选项池（真实候选）
          <i><p>do</p></i><i><p>make</p></i>
        </div>
        <span class="ant-dropdown-trigger user-answer empty">
          <div class="user-answer-text">点击选择</div>
        </span>
      </span>
    </span>

支持：① 从 DOM 抓每个空的候选选项与上下文；② 用大模型一次给全部空的答案
（省钱，一次调用）；③ 点开下拉并选中对应的选项。失败一律返回 0，不影响主流程。
"""
import re

# 扫描：只取"还没答"的空（user-answer 带 empty），返回每个空的候选词与上下文。
JS_SCAN_SCOOPS = r"""
() => {
  const out = [];
  const nodes = document.querySelectorAll('span.fe-scoop');
  for (const n of nodes) {
    const trigger = n.querySelector('.ant-dropdown-trigger.user-answer');
    if (!trigger) continue;
    // 已答过的空不动（避免覆盖人工答案）
    if (!trigger.classList.contains('empty')) continue;
    const wrap = n.querySelector('.scoop-select-wrapper');
    const opts = [];
    if (wrap) {
      // 隐藏选项池：visibility:hidden 的 div 里的 <i><p>词</p></i>
      for (const p of wrap.querySelectorAll('div > i > p, div > p')) {
        const t = (p.innerText || p.textContent || '').trim();
        if (t && t !== '点击选择' && t.length <= 40 && !opts.includes(t)) opts.push(t);
      }
    }
    if (!opts.length) continue;
    // 空前后各取一段文本当上下文
    let before = '', after = '';
    let s = n.previousSibling;
    let guard = 0;
    while (s && guard++ < 20) {
      if (s.nodeType === 3 || s.nodeType === 1) {
        const t = (s.innerText || s.nodeValue || '').replace(/\s+/g, ' ').trim();
        if (t) { before = t; break; }
      }
      s = s.previousSibling;
    }
    s = n.nextSibling; guard = 0;
    while (s && guard++ < 20) {
      if (s.nodeType === 3 || s.nodeType === 1) {
        const t = (s.innerText || s.nodeValue || '').replace(/\s+/g, ' ').trim();
        if (t) { after = t; break; }
      }
      s = s.nextSibling;
    }
    out.push({
      index: n.getAttribute('data-scoop-index') || String(out.length),
      options: opts,
      before: before.slice(-160),
      after: after.slice(0, 160),
    });
  }
  return out;
}
"""

# 点开某个空的下拉，读出真正弹出的候选项（ant-design 下拉菜单）。
JS_OPEN_SCOOP = r"""
(idx) => {
  const n = document.querySelector('span.fe-scoop[data-scoop-index="' + idx + '"]');
  if (!n) return {ok:false, why:'no scoop'};
  const trig = n.querySelector('.ant-dropdown-trigger.user-answer');
  if (!trig) return {ok:false, why:'no trigger'};
  trig.scrollIntoView({block:'center'});
  trig.click();
  return {ok:true};
}
"""

# 在已弹出的菜单里，按文本精确点击目标选项。
JS_PICK_SCOOP = r"""
(idx, answer) => {
  const wanted = String(answer || '').trim();
  let picked = false;
  const sel = '.ant-dropdown-menu-item, .ant-select-item-option, '
            + '[class*="dropdown"] li, [class*="option-item"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t === wanted) { el.click(); picked = true; break; }
  }
  if (picked) {
    const n = document.querySelector('span.fe-scoop[data-scoop-index="' + idx + '"]');
    const txt = n ? n.querySelector('.user-answer-text') : null;
    if (txt) txt.setAttribute('data-upc-scoop-picked', wanted);
  }
  return {ok: picked};
}
"""


def parse_scoops_from_html(html: str) -> list:
    """从离线 HTML 文本解析下拉空（供单测用，不依赖浏览器）。"""
    out = []
    for m in re.finditer(r'<span class="fe-scoop" data-scoop-index="(\d+)">(.*?)</span>\s*(?:<span[^>]*>)?',
                         html or "", re.S):
        idx, chunk = m.group(1), m.group(2)
        wrap_end = chunk.find('</span>')
        seg = chunk[:wrap_end] if wrap_end > 0 else chunk
        opts = re.findall(r'<i[^>]*>\s*<p>(.*?)</p>\s*</i>', seg, re.S)
        opts = [re.sub(r"<[^>]+>", "", o).strip() for o in opts]
        opts = [o for o in opts if o and o != "点击选择"]
        if opts:
            out.append({"index": idx, "options": opts})
    return out


def build_llm_prompt(scoops: list, page_text: str = "") -> str:
    """构造一次性的大模型提示：给所有空按顺序出答案，只在候选里选。"""
    lines = ["下面是英语课文里的下拉选词填空题。每个空只能从给定候选词里选一个。",
             "请根据上下文语法与搭配，按顺序给出每个空的答案。",
             "只输出答案本身，用 | 分隔，不要编号、不要解释。", ""]
    for s in scoops:
        lines.append(f"空{s['index']}：候选 {'/'.join(s['options'])} "
                     f"｜ 空前 ...{str(s.get('before') or '')[-80:]} "
                     f"｜ 空后 {str(s.get('after') or '')[:80]}...")
    prompt = "\n".join(lines)
    if page_text:
        prompt = prompt + "\n\n题目页面原文：\n" + page_text[:3000]
    return prompt


def parse_llm_answers(raw: str, scoops: list) -> list:
    """把大模型返回的 'do|make|...' 按空号解析；不在候选里的丢弃。"""
    if not raw:
        return []
    parts = [p.strip(" \t\r\n.,;:|'\"") for p in re.split(r"[|\n]", str(raw))]
    answers = []
    for i, s in enumerate(scoops):
        opts = [str(o).strip() for o in s.get("options") or []]
        ans = ""
        if i < len(parts):
            cand = parts[i]
            # 去掉模型可能带的 "答案:" 前缀
            cand = re.sub(r"^(?:答案|answer)\s*[:：]?\s*", "", cand, flags=re.I)
            for o in opts:
                if cand.lower() == o.lower():
                    ans = o
                    break
        answers.append(ans)
    return answers
