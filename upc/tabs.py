# -*- coding: utf-8 -*-
"""顶部 Tab 与 Task 枚举/点击。

U校园 AI 版的课程页有三层导航：
    目录叶子（左侧） → Tab（顶部横向） → Task（Tab 下方一排小方块）

每个 Task 才是一份真正的课件。刷时长的粒度最终落在 Task 上：
    per_task = 总时长 ÷ 叶子数 ÷ Tab 数 ÷ Task 数
"""
import time

from . import selectors as S

JS_LIST_TABS = r"""
(sel) => {
  const {tabItem, activeClasses} = sel;
  const q = (list) => {
    for (const s of list) {
      try {
        const els = document.querySelectorAll(s);
        if (els.length) return Array.from(els);
      } catch (e) {}
    }
    return [];
  };
  // 优先限定在 Tab 容器内
  let items = [];
  for (const cs of sel.tabContainer) {
    try {
      const c = document.querySelector(cs);
      if (!c) continue;
      const inner = q.call(null, tabItem);
      const inC = inner.filter(el => c.contains(el));
      if (inC.length) { items = inC; break; }
    } catch (e) {}
  }
  if (!items.length) items = q(tabItem);

  const out = [];
  items.forEach((el, i) => {
    const cls = String(el.className || '');
    const active = activeClasses.some(a => cls.includes(a)) ||
                   el.getAttribute('aria-selected') === 'true';
    const name = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    if (!name && !active) return;
    el.setAttribute('data-upc-tab', String(i));
    const inner = el.querySelector('[class*="tab-name"], [class*="tabName"], span, a');
    if (inner) inner.setAttribute('data-upc-tab', String(i));
    out.push({idx: i, name, active});
  });
  return out;
}
"""

JS_LIST_TASKS = r"""
(sel) => {
  const {taskItem, activeClasses} = sel;
  const q = (list) => {
    for (const s of list) {
      try {
        const els = document.querySelectorAll(s);
        if (els.length) return Array.from(els);
      } catch (e) {}
    }
    return [];
  };
  let items = [];
  for (const cs of sel.taskRow) {
    try {
      const c = document.querySelector(cs);
      if (!c) continue;
      const inner = q(taskItem).filter(el => c.contains(el));
      if (inner.length) { items = inner; break; }
    } catch (e) {}
  }
  if (!items.length) items = q(taskItem);

  const out = [];
  items.forEach((el, i) => {
    const cls = String(el.className || '');
    const active = activeClasses.some(a => cls.includes(a));
    const name = (el.innerText || el.getAttribute('title') || '')
                  .trim().replace(/\s+/g, ' ').slice(0, 60);
    el.setAttribute('data-upc-task', String(i));
    out.push({idx: i, name, active});
  });
  return out;
}
"""


class TabItem:
    __slots__ = ("idx", "name", "active")

    def __init__(self, idx, name, active=False):
        self.idx = idx
        self.name = name or f"Tab{idx + 1}"
        self.active = active

    def __repr__(self):
        return f"<Tab {self.idx} {self.name!r}>"


class TaskItem:
    __slots__ = ("idx", "name", "active")

    def __init__(self, idx, name, active=False):
        self.idx = idx
        self.name = name or f"Task{idx + 1}"
        self.active = active

    def __repr__(self):
        return f"<Task {self.idx} {self.name!r}>"


def _tab_payload():
    return {
        "tabContainer": S.TAB_CONTAINER,
        "tabItem": S.TAB_ITEM,
        "taskRow": S.TASK_ROW,
        "taskItem": S.TASK_ITEM,
        "activeClasses": list(S.TAB_ACTIVE_CLASSES),
    }


def list_tabs(page, wait_ms=None):
    """列出当前页面的 Tab。返回 [] 表示没有 Tab（单页课件）。"""
    wait_ms = S.TAB_APPEAR_TIMEOUT_MS if wait_ms is None else wait_ms
    deadline = time.time() + wait_ms / 1000.0
    while True:
        try:
            raw = page.evaluate(JS_LIST_TABS, _tab_payload())
        except Exception:  # noqa: BLE001
            raw = []
        if raw:
            return [TabItem(t["idx"], t["name"], t["active"]) for t in raw]
        if time.time() >= deadline:
            return []
        page.wait_for_timeout(400)


def list_tasks(page, wait_ms=None):
    """列出当前 Tab 下的 Task。"""
    wait_ms = S.TASK_APPEAR_TIMEOUT_MS if wait_ms is None else wait_ms
    deadline = time.time() + wait_ms / 1000.0
    while True:
        try:
            raw = page.evaluate(JS_LIST_TASKS, _tab_payload())
        except Exception:  # noqa: BLE001
            raw = []
        if raw:
            return [TaskItem(t["idx"], t["name"], t["active"]) for t in raw]
        if time.time() >= deadline:
            return []
        page.wait_for_timeout(400)


def _click_marked(page, attr, idx, timeout=8000):
    sel = f'[{attr}="{idx}"]'
    try:
        els = page.query_selector_all(sel)
    except Exception:  # noqa: BLE001
        return False
    for el in els:
        try:
            if not el.is_visible():
                continue
            el.click(timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def click_tab(page, tab, log, humanizer=None, settle_s=1.6):
    """切换 Tab。"""
    try:
        if humanizer:
            els = page.query_selector_all(f'[data-upc-tab="{tab.idx}"]')
            el = next((e for e in els if e.is_visible()), None)
            if el is None:
                return False
            humanizer.click(page, el, timeout=8000)
        elif not _click_marked(page, "data-upc-tab", tab.idx):
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug(f"  切换 Tab 失败 {tab.name}: {str(exc)[:70]}")
        return False
    page.wait_for_timeout(int(settle_s * 1000))
    return True


def click_task(page, task, log, humanizer=None, settle_s=1.8):
    """切换 Task（一个 Task = 一份课件）。"""
    try:
        if humanizer:
            els = page.query_selector_all(f'[data-upc-task="{task.idx}"]')
            el = next((e for e in els if e.is_visible()), None)
            if el is None:
                return False
            humanizer.click(page, el, timeout=8000)
        elif not _click_marked(page, "data-upc-task", task.idx):
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug(f"  切换 Task 失败 {task.name}: {str(exc)[:70]}")
        return False
    page.wait_for_timeout(int(settle_s * 1000))
    return True


def current_tab_index(page):
    try:
        raw = page.evaluate(JS_LIST_TABS, _tab_payload()) or []
    except Exception:  # noqa: BLE001
        return -1
    for t in raw:
        if t.get("active"):
            return t.get("idx", -1)
    return -1


def current_task_index(page):
    try:
        raw = page.evaluate(JS_LIST_TASKS, _tab_payload()) or []
    except Exception:  # noqa: BLE001
        return -1
    for t in raw:
        if t.get("active"):
            return t.get("idx", -1)
    return -1


def count_progress(page):
    """统计 Tab/Task 的完成进度（如果平台有角标/对勾）。

    返回 (done, total)；识别不出来时返回 (0, 0)，调用方不应据此做判断。
    """
    try:
        raw = page.evaluate(r"""
        () => {
          const els = Array.from(document.querySelectorAll(
            '.pc-task, [class*="task-item"], .pc-header-tabs-container .ant-col'));
          if (!els.length) return {done: 0, total: 0};
          let done = 0;
          for (const el of els) {
            const cls = String(el.className || '');
            const html = String(el.innerHTML || '');
            if (/finish|finished|done|complete|success|passed/i.test(cls) ||
                /✓|✔|完成/.test(el.innerText || '')) done++;
          }
          return {done, total: els.length};
        }
        """)
        return int(raw.get("done", 0)), int(raw.get("total", 0))
    except Exception:  # noqa: BLE001
        return 0, 0
