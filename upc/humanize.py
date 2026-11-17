# -*- coding: utf-8 -*-
"""拟人化操作：随机延时、贝塞尔鼠标轨迹、随机滚动、长暂停。

目的不是"骗过指纹检测"，而是避免明显的机器特征（瞬间点击、零延迟、
永远不动的鼠标、整齐划一的滚动），这类特征最容易触发平台的行为风控。
"""
import math
import random
import time


def _rand_range(pair, default=(200, 600)):
    try:
        lo, hi = pair
        lo, hi = float(lo), float(hi)
    except Exception:  # noqa: BLE001
        lo, hi = default
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


class Humanizer:
    def __init__(self, cfg, log):
        self.cfg = cfg.humanize
        self.log = log
        self._task_count = 0

    # ------------------------------------------------------------- 延时
    def before_click(self):
        if not self.cfg.enabled:
            return
        lo, hi = _rand_range(self.cfg.click_before_ms, (200, 900))
        time.sleep(random.uniform(lo, hi) / 1000.0)

    def after_click(self):
        if not self.cfg.enabled:
            return
        lo, hi = _rand_range(self.cfg.click_after_ms, (300, 1200))
        time.sleep(random.uniform(lo, hi) / 1000.0)

    def short_sleep(self, base=0.4, spread=0.8):
        if not self.cfg.enabled:
            time.sleep(base * 0.5)
            return
        time.sleep(base + random.uniform(0, spread))

    # ------------------------------------------------------------- 鼠标
    def move_mouse(self, page, x=None, y=None):
        """沿二次贝塞尔曲线移动鼠标（带轻微过冲），比直线更像人。"""
        if not self.cfg.enabled:
            return
        try:
            box = page.viewport_size or {"width": 1280, "height": 720}
            w, h = int(box["width"]), int(box["height"])
            if x is None or y is None:
                x = random.randint(int(w * 0.15), int(w * 0.85))
                y = random.randint(int(h * 0.15), int(h * 0.85))
            steps = random.randint(*[int(v) for v in _rand_range(self.cfg.mouse_steps, (8, 20))])
            # 起点取一个随机位置，终点是 (x, y)，控制点随机偏移
            sx = random.randint(int(w * 0.1), int(w * 0.9))
            sy = random.randint(int(h * 0.1), int(h * 0.9))
            cx = (sx + x) / 2 + random.randint(-120, 120)
            cy = (sy + y) / 2 + random.randint(-90, 90)
            for i in range(steps + 1):
                t = i / steps
                bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * x
                by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * y
                page.mouse.move(bx, by)
                time.sleep(random.uniform(0.008, 0.028))
        except Exception:  # noqa: BLE001
            pass

    def click(self, page, element, timeout=10000):
        """拟人化点击：先移动鼠标到元素中心，再点击。"""
        if not self.cfg.enabled:
            element.click(timeout=timeout)
            return
        try:
            box = element.bounding_box()
            if box:
                tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                self.move_mouse(page, tx, ty)
            else:
                self.move_mouse(page)
        except Exception:  # noqa: BLE001
            self.move_mouse(page)
        self.before_click()
        try:
            element.click(timeout=timeout)
        except Exception:  # noqa: BLE001
            # 被遮挡时退化为 JS 点击
            try:
                element.evaluate("el => el.click()")
            except Exception:  # noqa: BLE001
                raise
        self.after_click()

    # ------------------------------------------------------------- 滚动
    def maybe_scroll(self, page):
        if not self.cfg.enabled:
            return
        try:
            times = random.randint(*[int(v) for v in _rand_range(self.cfg.scroll_times, (1, 3))])
            lo, hi = _rand_range(self.cfg.scroll_step_px, (200, 600))
            for _ in range(times):
                page.evaluate("(d) => window.scrollBy(0, d)", random.randint(int(lo), int(hi)))
                time.sleep(random.uniform(0.3, 0.9))
            if random.random() < 0.5:
                page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:  # noqa: BLE001
            pass

    def nudge(self, page):
        """轻微鼠标移动（用于计时期间的"人还在"信号，不点击）。"""
        try:
            box = page.viewport_size or {"width": 1280, "height": 720}
            w, h = int(box["width"]), int(box["height"])
            page.mouse.move(
                random.randint(int(w * 0.2), int(w * 0.8)),
                random.randint(int(h * 0.2), int(h * 0.8)),
                steps=random.randint(6, 14),
            )
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------- 长暂停
    def maybe_long_pause(self, log=None):
        """每处理 N 个任务插入一次长暂停，打断稳定的机器节奏。"""
        if not self.cfg.enabled:
            return
        every = int(self.cfg.long_pause_every or 0)
        if every <= 0:
            return
        self._task_count += 1
        if self._task_count % every != 0:
            return
        lo, hi = _rand_range(self.cfg.long_pause_s, (15, 45))
        seconds = random.uniform(lo, hi)
        if log:
            log.info(f"  [拟人] 插入长暂停 {seconds:.0f} 秒")
        time.sleep(seconds)


def jitter(base, ratio=0.25):
    """给数值加比例抖动，返回浮点数。"""
    return float(base) * (1.0 + random.uniform(-ratio, ratio))


def human_delay_ms(pair, default=(300, 900)):
    lo, hi = _rand_range(pair, default)
    return random.uniform(lo, hi) / 1000.0
