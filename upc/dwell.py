# -*- coding: utf-8 -*-
"""刷学习时长：计时引擎。

U校园的核心机制
--------------
平台记录的是"在本 Task 页面上停留的累计时间"。它不像超星那样校验视频播放，
而是靠页面活跃 + 时长累加。所以刷时长的本质是：
    **一个个 Task 切过去，每个停留够时间，过程中保持页面"活的"。**

时长怎么分配
------------
- total   ：总分钟数 ÷ 叶子数 ÷ Tab 数 ÷ Task 数（等效油猴脚本的做法，可预测）
- per_item：每个 Task 固定秒数（简单粗暴）
- remaining：读平台显示的"还需学习 X 分钟"，把缺口摊到剩余 Task 上（最准）

避免"假活跃"被识破
----------------
- 计时期间随机移动鼠标、偶尔滚动（纯挂机容易被判无效）
- 随机抖动每个 Task 的停留时长（±15%），避免每个都精确 60 秒的机器特征
- 每 N 个 Task 插一次长暂停
"""
import random
import re
import time

from . import selectors as S
from .humanize import jitter

# "还需学习 12 分钟" / "剩余学习时长：05:30" / "本单元学习时长 30 分钟"
JS_PAGE_TEXT = "() => (document.body ? document.body.innerText : '')"

REMAIN_PATTERNS = [
    re.compile(r"(?:还需|剩余|尚需|还差)\s*(?:学习)?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(分钟|min|分)"),
    re.compile(r"(?:还需|剩余|尚需|还差)\s*(?:学习)?\s*[:：]?\s*(\d{1,2}):(\d{2})"),
    re.compile(r"(?:需|应|要求)\s*学习\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(分钟|min|分)"),
]


class TimeBudget:
    """时长预算：把总时长拆到每个 Task。"""

    def __init__(self, cfg, log):
        self.cfg = cfg.duration
        self.log = log
        self.per_task_s = 0.0
        self.mode = self.cfg.mode
        self.total_planned_s = 0.0
        self.spent_s = 0.0

    # ------------------------------------------------------------------
    def plan(self, leaf_count, plan_items=None):
        """计算每个 Task 的停留秒数。

        plan_items: [(leaf, tabs, tasks), ...]，用于 total 模式精确均分。
        """
        d = self.cfg
        if self.mode == "per_item":
            self.per_task_s = float(d.per_item_seconds)
            self.log.info(f"时长模式 per_item：每个 Task 停留 {self.per_task_s:.0f} 秒")
        elif self.mode == "total":
            total_s = float(d.total_minutes) * 60.0
            if plan_items:
                units = 0
                for _, n_tabs, n_tasks in plan_items:
                    units += max(1, n_tabs) * max(1, n_tasks)
                units = max(1, units)
            else:
                units = max(1, leaf_count)
            self.per_task_s = total_s / units
            self.log.info(
                f"时长模式 total：总 {d.total_minutes} 分钟 ÷ {units} 个 Task "
                f"= 每个约 {self.per_task_s:.0f} 秒"
            )
        else:  # remaining
            self.per_task_s = float(d.min_per_item_s)
            self.log.info("时长模式 remaining：以平台显示的剩余时长为准，逐单元刷到满足")

        lo = float(d.min_per_item_s)
        hi = float(d.max_per_item_s)
        if self.per_task_s < lo:
            self.log.warning(
                f"  均分后单点仅 {self.per_task_s:.0f} 秒（低于下限 {lo:.0f}s），"
                f"按 {lo:.0f}s 执行；如需总时长更长请加大 total_minutes"
            )
            self.per_task_s = lo
        if self.per_task_s > hi:
            self.log.warning(f"  单点时长 {self.per_task_s:.0f}s 超过上限，按 {hi:.0f}s 执行")
            self.per_task_s = hi
        return self.per_task_s

    def next_seconds(self, factor=1.0):
        """取本次要停留的秒数（带随机抖动，避免机器节奏）。"""
        base = self.per_task_s * factor
        secs = jitter(base, 0.15)
        return max(3.0, secs)


def read_remaining_minutes(page):
    """尝试从页面读出"还需学习 X 分钟"。读不到返回 None。"""
    try:
        text = page.evaluate(JS_PAGE_TEXT) or ""
    except Exception:  # noqa: BLE001
        return None
    text = text.replace("\u3000", " ")
    for pat in REMAIN_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            if m.lastindex and m.lastindex >= 2 and m.group(2) and m.group(2).isdigit():
                return float(m.group(1)) + float(m.group(2)) / 60.0
            return float(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def dwell(session, cfg, log, page, seconds, label="", popup=None,
          humanizer=None, keepalive=None, video_probe=None):
    """在当前页面停留指定秒数。

    video_probe: 可选回调，返回 True 表示"正在播视频"，此时暂停计时
                 （对齐 U校园 规则：视频播放时间与学习时长分开计）。

    返回 (status, actual_seconds)。status: ok / interrupted
    """
    d = cfg.duration
    tick = max(0.5, float(d.tick_s))
    total = max(0.0, float(seconds))
    start = time.time()
    remaining = total
    last_activity = 0.0
    last_scroll = 0.0
    last_report = 0.0
    pause_video = bool(d.pause_on_video and video_probe is not None)
    move_every = float(d.activity_move_every_s or 45)
    scroll_every = float(d.activity_scroll_every_s or 120)

    while remaining > 0:
        # 会话健康
        if keepalive is not None:
            try:
                if not keepalive(label or "刷时长"):
                    log.warning("  会话中断，停止本任务计时")
                    return "interrupted", time.time() - start
            except Exception:  # noqa: BLE001
                pass

        if popup:
            popup.tick(page)
        session.keep_alive()

        # 视频播放中 → 不计时
        if pause_video:
            try:
                if video_probe(page):
                    if remaining == total or int(remaining) % 30 == 0:
                        log.info("  [视频] 播放中，暂停计时")
                    time.sleep(1.0)
                    continue
            except Exception:  # noqa: BLE001
                pass

        elapsed = time.time() - start

        # 拟人化保活
        if humanizer is not None:
            if move_every > 0 and elapsed - last_activity >= move_every:
                last_activity = elapsed
                humanizer.nudge(page)
            if scroll_every > 0 and elapsed - last_scroll >= scroll_every:
                last_scroll = elapsed
                humanizer.maybe_scroll(page)

        # 进度日志（每 30 秒一条，别刷屏）
        if elapsed - last_report >= 30:
            last_report = elapsed
            log.info(f"  ⏳ {label} 剩余 {remaining:.0f}s / 共 {total:.0f}s")

        time.sleep(min(tick, max(0.1, remaining)))
        remaining -= tick

    spent = time.time() - start
    # 切页前额外停留，让心跳上报
    settle = float(d.settle_after_switch_s or 0)
    if settle > 0:
        if humanizer is not None:
            humanizer.nudge(page)
        time.sleep(settle)
    return "ok", spent


def compute_task_plan(page, log, max_tabs=30, max_tasks=30):
    """探测当前叶子下的 Tab 数 / Task 数（用于 total 模式均分）。"""
    from . import tabs as T

    tab_list = T.list_tabs(page)
    n_tabs = max(1, len(tab_list))
    task_list = T.list_tasks(page)
    n_tasks = max(1, len(task_list))
    log.debug(f"    探测到 {len(tab_list)} 个 Tab / {len(task_list)} 个 Task")
    return min(n_tabs, max_tabs), min(n_tasks, max_tasks)


def has_media(page):
    """当前页是否有正在播放的媒体。用于 video_probe。"""
    try:
        for fr in page.frames:
            try:
                hit = fr.evaluate(r"""
                () => {
                  const els = document.querySelectorAll('video, audio');
                  for (const el of els) {
                    if (!el.paused && !el.ended && el.readyState > 2) return true;
                  }
                  return false;
                }
                """)
                if hit:
                    return True
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return False
