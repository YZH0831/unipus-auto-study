# -*- coding: utf-8 -*-
"""课件目录「白圈/蓝圈」完成状态读取。

实测（2026-11-05，AI版课件 ucontent.unipus.cn）：
    .pc-slider-menu-status-circle-container       每个目录节点一个
      └─ .pc-slider-menu-status-circle-all        → 蓝圈，已完成
      └─ .pc-slider-menu-status-circle-half       → 白/半圈，未完成
    .pc-menu-node-name                            节点名（在祖先里）

注意：内外圈的 background-color 都是 rgb(50,100,250)，
**不能靠颜色判断**，必须看 class 是 all 还是 half。
"""
JS_SCAN = r"""
() => {
  const out = [];
  let i = 0;
  for (const c of document.querySelectorAll('.pc-slider-menu-status-circle-container')) {
    let p = c.parentElement, name = '';
    for (let k = 0; k < 7 && p; k++) {
      const n = p.querySelector && p.querySelector('.pc-menu-node-name');
      if (n && (n.innerText || '').trim()) { name = n.innerText.trim(); break; }
      p = p.parentElement;
    }
    const all = !!c.querySelector('.pc-slider-menu-status-circle-all');
    const half = !!c.querySelector('.pc-slider-menu-status-circle-half');
    out.push({i: i++, name: name.slice(0, 70),
              done: all, state: all ? 'all' : (half ? 'half' : '?')});
  }
  return out;
}
"""


def scan(page):
    """返回 [{i, name, done, state}]；页面没渲染好时返回 []。

    ★ 先探活再 evaluate：点过「发表/提交/上传」后页面偶尔会进入 JS 忙状态，
      此时 page.evaluate 会**永久挂起**（Playwright 的 evaluate 没有超时参数），
      整条流水线就静默卡死——用户报的"卡住不动"就是它。
      wait_for_function 有超时，当探针用；探不通就当"判断不出来"返回空。
    """
    try:
        page.wait_for_function(
            "() => document.readyState !== 'loading'", timeout=4000)
    except Exception:  # noqa: BLE001
        return []
    try:
        return page.evaluate(JS_SCAN) or []
    except Exception:  # noqa: BLE001
        return []


def count_done(page):
    """蓝圈数量。"""
    return sum(1 for r in scan(page) if r.get("done"))


def done_names(page):
    """已完成节点的名字集合（名字会重复，只做粗粒度跳过）。"""
    return {r["name"] for r in scan(page) if r.get("done") and r.get("name")}


def micro_done(page, idx):
    """查第 idx 个 micro 节的圈是不是蓝的。

    idx 是结构树里 micro 的顺序下标，和 DOM 里 79 个圈一一对应。
    找不到时返回 None（表示无法判断），不要当成"未完成"误报。
    """
    rows = scan(page)
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(rows):
        return None
    return bool(rows[idx].get("done"))


def micro_state(page, idx):
    """返回 (是否蓝圈, 节点名)；无法判断时 (None, '')。"""
    rows = scan(page)
    if idx is None or idx < 0 or idx >= len(rows):
        return None, ""
    return bool(rows[idx].get("done")), rows[idx].get("name", "")


def verify(page, before, wait_s=2.0):
    """处理完一个任务后复查蓝圈数有没有增加。返回 (新增数, 当前总数)。"""
    import time
    if wait_s:
        time.sleep(wait_s)
    # 目录状态可能是异步刷新的，多探几次取最大值
    best = before
    for _ in range(3):
        n = count_done(page)
        if n > best:
            best = n
        if n > before:
            break
        time.sleep(1.0)
    return best - before, best
