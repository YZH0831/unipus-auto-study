# -*- coding: utf-8 -*-
"""课程目录树解析：Unit → Section → Micro。

难点
----
1. **多层折叠**：没展开的节点在 DOM 里根本不存在子节点，必须先点开才能扫到叶子。
2. **ElementHandle 会失效**：点一个叶子会触发课件 iframe 重载，之前抓的 handle 全变 stale。
   → 解决办法：扫描时给每个节点打上 `data-upc-id` 属性，后续用这个属性重新定位。
3. **结构有多套**：pc-slider-menu 体系、AI 版 u3menu、通用 antd 树。按策略依次尝试。
"""
import json
import time

from . import selectors as S

# ---------------------------------------------------------------------------
# 扫描脚本：在页面里跑，给节点打 id 并返回扁平化结构
# ---------------------------------------------------------------------------
JS_SCAN = r"""
(sel) => {
  const {containers, unitSel, sectionSel, microSel, nodeSel, nameSel, clickSel} = sel;

  const q = (root, list) => {
    for (const s of list) {
      try {
        const els = root.querySelectorAll(s);
        if (els.length) return Array.from(els);
      } catch (e) {}
    }
    return [];
  };

  // 1) 找容器
  let container = null;
  for (const s of containers) {
    try {
      const els = document.querySelectorAll(s);
      for (const el of els) {
        if (el.offsetParent !== null || el.getClientRects().length) { container = el; break; }
      }
      if (container) break;
    } catch (e) {}
  }
  if (!container) return {ok: false, reason: 'no_container', items: []};

  // 2) 收集所有节点（去重 + DOM 顺序）
  const seen = new Set();
  const nodes = [];
  const pushAll = (list, level) => {
    const els = q(container, list);
    for (const el of els) {
      if (seen.has(el)) continue;
      seen.add(el);
      nodes.push({el, level});
    }
  };
  pushAll(unitSel, 0);
  pushAll(sectionSel, 1);
  pushAll(nodeSel, 1);
  pushAll(microSel, 2);

  // 通用树兜底（aria-level / 缩进）
  if (nodes.length === 0) {
    const gens = q(container, ['.ant-tree-treenode', '[role="treeitem"]',
                              '.ant-menu-item', '.ant-menu-submenu-title',
                              'li[role="menuitem"]']);
    for (const el of gens) {
      const aria = el.getAttribute('aria-level');
      let lv = aria ? parseInt(aria, 10) - 1 : null;
      if (lv === null) {
        const ind = el.querySelector('.ant-tree-indent');
        lv = ind ? ind.children.length : 0;
      }
      nodes.push({el, level: Math.max(0, lv)});
    }
  }

  // 3) 按 DOM 顺序排序
  nodes.sort((a, b) => {
    const pos = a.el.compareDocumentPosition(b.el);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });

  // 4) 判定叶子：自己不包含任何其它节点
  const nodeSet = new Set(nodes.map(n => n.el));
  const textOf = (el) => {
    let t = '';
    for (const s of nameSel) {
      try {
        const n = el.querySelector(s);
        if (n && n.innerText && n.innerText.trim()) { t = n.innerText.trim(); break; }
      } catch (e) {}
    }
    if (!t) t = (el.innerText || '').trim();
    return t.replace(/\s+/g, ' ').slice(0, 120);
  };
  const clickableOf = (el) => {
    for (const s of clickSel) {
      try {
        const n = el.querySelector(s);
        if (n) return n;
      } catch (e) {}
    }
    return el;
  };

  const items = [];
  let idx = 0;
  const unitStack = [], sectionStack = [];
  for (const n of nodes) {
    let hasChild = false;
    for (const other of nodeSet) {
      if (other !== n.el && n.el.contains(other)) { hasChild = true; break; }
    }
    const leaf = !hasChild;
    const title = textOf(n.el);

    // 维护祖先标题
    if (n.level === 0) { unitStack[0] = title; sectionStack.length = 0; }
    else if (n.level === 1) { sectionStack[0] = title; }

    if (!leaf) continue;
    if (!title) continue;

    const uid = 'upc-' + (idx++);
    n.el.setAttribute('data-upc-id', uid);
    const clk = clickableOf(n.el);
    if (clk !== n.el) clk.setAttribute('data-upc-id', uid);

    items.push({
      uid,
      title,
      level: n.level,
      unit: unitStack[0] || '',
      section: sectionStack[0] || '',
      visible: (n.el.offsetParent !== null || n.el.getClientRects().length > 0),
      expanded: n.el.getAttribute('aria-expanded'),
    });
  }

  return {ok: true, total: nodes.length, items};
}
"""

# 展开全部折叠节点
JS_EXPAND = r"""
(sel) => {
  let clicked = 0;
  const tryClick = (el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      el.click();
      return true;
    } catch (e) { return false; }
  };
  // 1) 明确的展开箭头
  const arrows = document.querySelectorAll(
    '[class*="ant-tree-switcher"]:not(.ant-tree-switcher-noop), ' +
    '[class*="switcher"], [class*="expand-icon"], [class*="expandIcon"], ' +
    '[aria-expanded="false"]'
  );
  for (const a of arrows) {
    const cls = a.className || '';
    if (String(cls).includes('noop')) continue;
    if (a.getAttribute('aria-expanded') === 'true') continue;
    if (tryClick(a)) clicked++;
  }
  // 2) 单元级节点：折叠态通常没有子节点，直接点它本身
  for (const s of sel.unitSel) {
    try {
      for (const el of document.querySelectorAll(s)) {
        if (el.querySelector(sel.microSel.join(','))) continue;
        if (tryClick(el)) clicked++;
      }
    } catch (e) {}
  }
  return clicked;
}
"""


class Leaf:
    """一个可学习的叶子节点。"""

    __slots__ = ("uid", "title", "level", "unit", "section", "visible", "status", "note")

    def __init__(self, uid, title, level=0, unit="", section="", visible=True):
        self.uid = uid
        self.title = title
        self.level = level
        self.unit = unit
        self.section = section
        self.visible = visible
        self.status = "pending"      # pending / done / failed / skipped
        self.note = ""

    @property
    def display(self):
        parts = [p for p in (self.unit, self.section, self.title) if p]
        # 去掉重复（有时 unit 与 title 相同）
        out = []
        for p in parts:
            if not out or out[-1] != p:
                out.append(p)
        return " / ".join(out)

    def __repr__(self):
        return f"<Leaf {self.uid} {self.display!r} {self.status}>"

    def to_dict(self):
        return {"uid": self.uid, "title": self.title, "unit": self.unit,
                "section": self.section, "status": self.status, "note": self.note}


def _sel_payload():
    return {
        "containers": S.MENU_CONTAINER + S.MENU_CONTAINER_U3,
        "unitSel": S.NODE_UNIT + S.NODE_U3_UNIT,
        "sectionSel": S.NODE_SECTION,
        "microSel": S.NODE_MICRO + S.NODE_U3_GROUP,
        "nodeSel": S.NODE_PLAIN,
        "nameSel": S.NODE_NAME + S.NODE_U3_LINK + S.UNIT_LABEL,
        "clickSel": S.NODE_CLICKABLE,
    }


def menu_present(page):
    """目录是否已经渲染出来。"""
    for sel in S.MENU_PROBE:
        try:
            if page.query_selector(sel) is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def open_menu_if_needed(page, log, humanizer=None):
    """有些课程目录默认收起，先尝试把它叫出来。"""
    if menu_present(page):
        return True
    for sel in S.MENU_TOGGLE:
        try:
            el = page.query_selector(sel)
            if el is not None and el.is_visible():
                if humanizer:
                    humanizer.click(page, el, timeout=4000)
                else:
                    el.click(timeout=4000)
                log.info(f"  已点击目录开关 ({sel})")
                page.wait_for_timeout(1500)
                if menu_present(page):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return menu_present(page)


def expand_all(page, log, rounds=6, humanizer=None):
    """反复点击折叠节点，直到叶子数量不再增长。"""
    last_count = -1
    for r in range(1, rounds + 1):
        try:
            clicked = page.evaluate(JS_EXPAND, _sel_payload())
        except Exception as exc:  # noqa: BLE001
            log.debug(f"  展开脚本异常: {str(exc)[:80]}")
            break
        page.wait_for_timeout(900)
        try:
            n = len(page.evaluate(
                "() => document.querySelectorAll('.pc-slider-menu-micro, "
                ".pc-slider-menu-node, [role=\"treeitem\"]').length"
            ) or [])
        except Exception:  # noqa: BLE001
            n = 0
        log.debug(f"  展开第 {r} 轮：点击 {clicked} 个，可见节点 {n}")
        if n == last_count and clicked == 0:
            break
        last_count = n
    return True


def discover_leaves(page, log, humanizer=None, auto_expand=True):
    """扫描目录，返回 Leaf 列表（已按 DOM 顺序）。"""
    if not open_menu_if_needed(page, log, humanizer):
        log.warning("未找到课程目录（可能页面还没加载完，或目录需要手动展开）")
        return []
    if auto_expand:
        expand_all(page, log, humanizer=humanizer)

    try:
        raw = page.evaluate(JS_SCAN, _sel_payload())
    except Exception as exc:  # noqa: BLE001
        log.error(f"扫描目录失败: {str(exc)[:150]}")
        return []

    if not raw or not raw.get("ok"):
        reason = (raw or {}).get("reason", "unknown")
        log.warning(f"目录结构未识别（{reason}）")
        return []

    items = raw.get("items") or []
    leaves = [Leaf(i["uid"], i["title"], i["level"], i.get("unit", ""),
                   i.get("section", ""), i.get("visible", True)) for i in items]
    log.info(f"目录扫描完成：共 {raw.get('total', 0)} 个节点，识别出 {len(leaves)} 个可学习项")
    return leaves


def filter_leaves(leaves, spec, log):
    """按 units 配置过滤。spec 形如 "1-3,5"，按 Unit 在列表中的序号（从 1 开始）或标题里的数字匹配。"""
    spec = (spec or "").strip()
    if not spec:
        return leaves

    wanted = set()
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                wanted.update(range(int(a), int(b) + 1))
            except Exception:  # noqa: BLE001
                continue
        else:
            try:
                wanted.add(int(part))
            except Exception:  # noqa: BLE001
                continue
    if not wanted:
        return leaves

    # 给每个 Unit 编号
    order = {}
    for lf in leaves:
        u = lf.unit or "(默认)"
        if u not in order:
            order[u] = len(order) + 1

    kept, dropped = [], 0
    for lf in leaves:
        u = lf.unit or "(默认)"
        num = order[u]
        # 同时也匹配 Unit 标题中显式的数字（Unit 3 / 第三单元）
        hit = num in wanted
        if not hit:
            for w in wanted:
                if f"unit {w}" in u.lower() or f"unit{w}" in u.lower() or f"第{w}单元" in u:
                    hit = True
                    break
        if hit:
            kept.append(lf)
        else:
            dropped += 1
    log.info(f"按单元筛选 {spec}：保留 {len(kept)} 项，排除 {dropped} 项")
    return kept


def locate_leaf(page, uid, timeout_s=8):
    """用 data-upc-id 重新定位叶子节点（应对 handle 失效）。"""
    deadline = time.time() + timeout_s
    sel = f'[data-upc-id="{uid}"]'
    while time.time() < deadline:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                try:
                    if el.is_visible():
                        return el
                except Exception:  # noqa: BLE001
                    continue
            # 目录可能在 iframe 里
            for fr in page.frames:
                if fr is page.main_frame:
                    continue
                try:
                    el = fr.query_selector(sel)
                    if el is not None and el.is_visible():
                        return el
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return None


def click_leaf(page, leaf, log, humanizer=None, settle_s=2.5):
    """点击一个叶子节点，触发课件加载。"""
    el = locate_leaf(page, leaf.uid)
    if el is None:
        log.warning(f"  定位不到节点 {leaf.display}（可能目录被重绘），尝试重扫")
        return False
    try:
        if humanizer:
            humanizer.click(page, el, timeout=8000)
        else:
            el.click(timeout=8000)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  点击 {leaf.display} 失败: {str(exc)[:80]}")
        return False
    page.wait_for_timeout(int(settle_s * 1000))
    return True


def dump_tree(page, path, log):
    """把目录结构导出成 JSON，便于排查/校准。"""
    leaves = discover_leaves(page, log, auto_expand=False)
    data = {"url": page.url, "count": len(leaves), "items": [l.to_dict() for l in leaves]}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"目录结构已导出: {path}")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"导出目录结构失败: {str(exc)[:80]}")
    return data
