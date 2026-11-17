# -*- coding: utf-8 -*-
"""弹窗与遮罩处理。

U校园的坑在于弹窗分散在多层：主页面（antd Modal）+ 课件 iframe（ipublish 自己的
Modal）+ 播放器（"我知道了"提示）。所以必须**遍历所有 frame**去找，不能只看主页。
"""
import time

from . import selectors as S


class PopupGuard:
    def __init__(self, cfg, log, humanizer=None):
        self.cfg = cfg.popup
        self.log = log
        self.humanizer = humanizer
        self._last_check = 0.0
        self._count = 0

    # ------------------------------------------------------------------
    def _scopes(self, page):
        """主页面 + 所有 iframe。"""
        scopes = [page]
        try:
            for fr in page.frames:
                if fr is not page.main_frame:
                    scopes.append(fr)
        except Exception:  # noqa: BLE001
            pass
        return scopes

    def _click_selector_buttons(self, scope):
        for sel in S.POPUP_BUTTONS:
            try:
                els = scope.query_selector_all(sel)
            except Exception:  # noqa: BLE001
                continue
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    if self.humanizer:
                        self.humanizer.click(scope_owner(scope), el, timeout=3000)
                    else:
                        el.click(timeout=3000)
                    self._count += 1
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    def _click_text_buttons(self, scope):
        """按文字找按钮（"我知道了 / 确定 / 确认"）。"""
        try:
            els = scope.query_selector_all(S.POPUP_TEXT_TAGS)
        except Exception:  # noqa: BLE001
            return False
        for el in els:
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if not label or len(label) > 8:
                continue
            if not any(t in label for t in S.POPUP_TEXTS):
                continue
            try:
                if self.humanizer:
                    self.humanizer.click(scope_owner(scope), el, timeout=2500)
                else:
                    el.click(timeout=2500)
                self._count += 1
                self.log.debug(f"  已关闭弹窗: {label}")
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ------------------------------------------------------------------
    def tick(self, page, force=False):
        """检查并关闭弹窗。返回关闭的数量。"""
        if not self.cfg.auto_close:
            return 0
        now = time.time()
        if not force and now - self._last_check < float(self.cfg.check_interval_s):
            return 0
        self._last_check = now
        closed = 0
        for scope in self._scopes(page):
            try:
                if self._click_selector_buttons(scope):
                    closed += 1
                    time.sleep(0.4)
                    continue
                if self._click_text_buttons(scope):
                    closed += 1
                    time.sleep(0.4)
            except Exception:  # noqa: BLE001
                continue
        if closed:
            self.log.info(f"  [弹窗] 关闭了 {closed} 个提示框")
        return closed

    def sweep(self, page, rounds=3, gap=0.6):
        """连续清几轮（有些弹窗是串行出现的）。"""
        total = 0
        for _ in range(rounds):
            n = self.tick(page, force=True)
            total += n
            if not n:
                break
            time.sleep(gap)
        return total


def scope_owner(scope):
    """Frame 没有 mouse，取它的 page 用于拟人化点击。"""
    return getattr(scope, "page", scope)
