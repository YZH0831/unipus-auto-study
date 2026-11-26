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
# ★ 2026-11-25：antd Dropdown 默认是 **hover 触发**，只调 trig.click()
#   弹不出来（实测 6 个空一个都没选上）。这里把鼠标事件全套派发一遍。
JS_OPEN_SCOOP = r"""
(idx) => {
  const n = document.querySelector('span.fe-scoop[data-scoop-index="' + idx + '"]');
  if (!n) return {ok:false, why:'no scoop'};
  const trig = n.querySelector('.ant-dropdown-trigger.user-answer');
  if (!trig) return {ok:false, why:'no trigger'};
  trig.scrollIntoView({block:'center'});
  const r = trig.getBoundingClientRect();
  const opt = {bubbles: true, cancelable: true, view: window,
               clientX: Math.round(r.left + r.width / 2),
               clientY: Math.round(r.top + r.height / 2)};
  for (const ev of ['pointerover', 'mouseenter', 'mouseover', 'mousemove']) {
    try { trig.dispatchEvent(new MouseEvent(ev, opt)); } catch (e) {}
  }
  try { trig.click(); } catch (e) {}
  return {ok: true};
}
"""

# 诊断：把当前页面上"看起来像下拉菜单"的可见容器列出来，用于校准选择器。
JS_DUMP_SCOOP_MENU = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll(
      '[class*="dropdown"], [class*="select"], [class*="option"], [role="listbox"], ul')) {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t) continue;
    out.push({cls: String(el.className).slice(0, 80),
              t: t.slice(0, 90), wh: Math.round(r.width) + 'x' + Math.round(r.height)});
  }
  return out.slice(0, 12);
}
"""

# 在已弹出的菜单里，按文本精确点击目标选项。
# ★ 2026-11-25 修：Playwright 的 evaluate 只能传 **一个** arg，
#   原来的 (idx, answer) => 两参数写法会抛
#   "Page.evaluate() takes from 2 to 3 positional arguments but 4"，
#   于是每个空都静默失败、整题被判"未识别"三振出局。改成解构单参数。
JS_PICK_SCOOP = r"""
([idx, answer]) => {
  const wanted = String(answer || '').trim();
  let picked = false;
  const sel = '.ant-dropdown-menu-item, .ant-select-item-option, '
            + '[class*="dropdown"] li, [class*="option-item"], '
            + '.scoop-select-wrapper p, .scoop-select-wrapper i, '
            + '[class*="dropdown"] [class*="item"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    // 隐藏的选项池（visibility:hidden / display:none）不能点
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const t = (el.innerText || el.textContent || '')
                .replace(/\s+/g, ' ').trim();
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


# 读某个空当前的状态：是否已选、是否已有菜单展开。
# ★ 2026-11-26：以前 hover + click 连着做，antd Dropdown 是 hover 触发、
#   click 又会把它关掉，于是「hover 已经打开 → click 关闭 → 选项点不到」。
#   现在先看状态，菜单没开才点一次，避免自己把菜单点没。
JS_SCOOP_STATE = r"""
(idx) => {
  const n = document.querySelector('span.fe-scoop[data-scoop-index="' + idx + '"]');
  if (!n) return {found: false};
  const trig = n.querySelector('.ant-dropdown-trigger.user-answer');
  const txt = trig ? trig.querySelector('.user-answer-text') : null;
  let menuOpen = false;
  for (const m of document.querySelectorAll('.ant-dropdown')) {
    const r = m.getBoundingClientRect();
    const st = getComputedStyle(m);
    if (r.width > 20 && r.height > 10 && st.display !== 'none'
        && st.visibility !== 'hidden' && st.opacity !== '0') {
      menuOpen = true;
      break;
    }
  }
  return {found: true,
          empty: !!trig && trig.classList.contains('empty'),
          text: ((txt && txt.innerText) || '').replace(/\s+/g, ' ').trim(),
          menuOpen: menuOpen};
}
"""


def state(page, idx) -> dict:
    """返回 {found, empty, text, menuOpen}；异常当没找到。"""
    try:
        return page.evaluate(JS_SCOOP_STATE, str(idx)) or {"found": False}
    except Exception:  # noqa: BLE001
        return {"found": False}


def _click_menu_item(page, answer) -> bool:
    """在已展开的 antd 菜单里按文本真实点击目标选项。

    JS click 对部分 React 组件不生效（合成事件目标不对），改用
    ElementHandle.click() 走真实鼠标事件；弹层在外面也能点到。
    """
    wanted = " ".join(str(answer or "").split())
    if not wanted:
        return False
    for sel in (".ant-dropdown-menu-item", ".ant-select-item-option",
                ".ant-dropdown li", '[class*="dropdown"] [class*="item"]'):
        try:
            items = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        for el in items:
            try:
                if not el.is_visible():
                    continue
                t = " ".join((el.inner_text() or "").split())
                if not t or t.lower() != wanted.lower():
                    continue
                el.scroll_into_view_if_needed(timeout=1500)
                el.click(timeout=2500)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def pick_scoop(page, log, idx, answer, tries: int = 2) -> bool:
    """把一个下拉空选中并回读确认。

    流程：读状态 → 菜单没开才点一次开 → 真实点击选项 → 回读确认。
    失败会关掉残留菜单重开再试；仍失败返回 False（由调用方计未作答）。
    """
    if not answer:
        return False
    for attempt in range(max(1, tries)):
        st = state(page, idx)
        if not st.get("found"):
            return False
        # 已经选上了（可能上一轮已成功）→ 直接算成功
        if not st.get("empty"):
            return True
        if not st.get("menuOpen"):
            try:
                trig = page.query_selector(
                    f'span.fe-scoop[data-scoop-index="{idx}"] '
                    ".ant-dropdown-trigger.user-answer")
                if trig is not None:
                    trig.scroll_into_view_if_needed(timeout=2000)
                    trig.click(timeout=3000)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"    下拉空{idx} 展开失败: {str(exc)[:60]}")
        page.wait_for_timeout(450 + 250 * attempt)
        if _click_menu_item(page, answer):
            page.wait_for_timeout(250)
            st2 = state(page, idx)
            if st2.get("found") and not st2.get("empty"):
                return True
        # 兜底：菜单项被透明遮罩挡住时，真实点击会超时，用 JS 选一次
        try:
            r = page.evaluate(JS_PICK_SCOOP, [str(idx), answer]) or {}
            if r.get("ok"):
                page.wait_for_timeout(250)
                st3 = state(page, idx)
                if st3.get("found") and not st3.get("empty"):
                    return True
        except Exception:  # noqa: BLE001
            pass
        # 关掉可能残留的菜单，下一轮重新点开
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(200)
    return False


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


def _norm_opt(text: str) -> str:
    """候选/答案归一化：压缩空白、小写、去掉首尾标点。

    ★ 2026-11-26 实测：候选是 "mountains of Colorado in the U.S.A."，
    模型答同样的词时末尾句点常被 `strip(".,;:")` 吃掉，严格相等会判不中，
    整个空被跳过（3 个空只选 2 个）。这里只忽略首尾标点，词内不动。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return s.strip(" .,;:!?'\"")


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
                if _norm_opt(cand) == _norm_opt(o):
                    ans = o
                    break
        answers.append(ans)
    return answers
