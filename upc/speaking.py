# -*- coding: utf-8 -*-
"""口语跟读 / 录音题。

现实说明（别抱太高期待）
--------------------
这类题平台通常会把录音送去做语音识别 + 跟原文比对打分，伪造录音基本拿不到分。
本模块的目标是**把流程走完**（录音 → 提交 → 任务点标记为已完成），
而不是拿高分。真要分数，还是自己读一遍。

做法
----
1. 浏览器以 `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream` 启动，
   麦克风权限自动授予，页面不会卡在"请允许录音"。
2. 想让它"有声音"：config 里 `browser.fake_audio_wav` 指一个 wav 文件，
   会以 `--use-file-for-fake-audio-capture` 喂给虚拟麦克风。
3. 依次点：录音 → 等待 → 停止 → 提交。
"""
import time

from . import selectors as S


def _scopes(page):
    scopes = [page]
    try:
        for fr in page.frames:
            if fr is not page.main_frame:
                scopes.append(fr)
    except Exception:  # noqa: BLE001
        pass
    return scopes


def find_button(page, selectors, timeout_s=6):
    """在页面和所有 iframe 里找按钮。"""
    import time as _t
    deadline = _t.time() + timeout_s
    while True:
        for scope in _scopes(page):
            el = S.first_in(scope, selectors, timeout_s=0)
            if el is not None:
                return scope, el
        if _t.time() >= deadline:
            return None, None
        time.sleep(0.4)


def has_speaking_task(page):
    """页面是否有口语题。"""
    scope, el = find_button(page, S.RECORD_BUTTON, timeout_s=1.5)
    return el is not None


def _click(page, el, humanizer, log, what=""):
    try:
        if humanizer:
            humanizer.click(page, el, timeout=8000)
        else:
            el.click(timeout=8000)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    点击{what}失败: {str(exc)[:70]}")
        return False


def handle_one(page, cfg, log, humanizer=None, popup=None, keepalive=None):
    """处理页面上的一处口语题。返回 (status, note)。"""
    scfg = cfg.speaking
    if not scfg.enabled:
        return "skipped", "口语模块未启用"

    scope, rec_btn = find_button(page, S.RECORD_BUTTON, timeout_s=5)
    if rec_btn is None:
        return "none", "未找到录音按钮"

    if popup:
        popup.sweep(page, rounds=1)

    log.info("  [口语] 找到录音按钮，开始录音")
    if not _click(page, rec_btn, humanizer, log, "录音按钮"):
        return "failed", "录音按钮点不动"

    # 录音时长（带一点抖动，别每次都是整数）
    secs = float(scfg.record_seconds)
    total = max(1.0, secs)
    waited = 0.0
    while waited < total:
        if keepalive is not None:
            try:
                if not keepalive("口语录音"):
                    return "manual", "录音期间会话中断"
            except Exception:  # noqa: BLE001
                pass
        if popup:
            popup.tick(page)
        time.sleep(1.0)
        waited += 1.0

    # 停止录音
    scope, stop_btn = find_button(page, S.STOP_RECORD_BUTTON, timeout_s=3)
    if stop_btn is not None:
        _click(page, stop_btn, humanizer, log, "停止录音")
    else:
        # 很多播放器是"再点一次同一个按钮"结束
        _click(page, rec_btn, humanizer, log, "录音按钮(结束)")
    page.wait_for_timeout(1200)

    if not scfg.submit:
        return "done", f"已录音 {total:.0f}s（未提交）"

    scope, sub_btn = find_button(page, S.RECORD_SUBMIT, timeout_s=5)
    if sub_btn is None:
        return "done", f"已录音 {total:.0f}s（未找到提交按钮）"
    if not _click(page, sub_btn, humanizer, log, "提交"):
        return "failed", "提交按钮点不动"

    page.wait_for_timeout(int(float(scfg.wait_after_submit_s) * 1000))
    if popup:
        popup.sweep(page, rounds=2)
    log.info("  [口语] 录音已提交")
    return "done", f"录音 {total:.0f}s 并提交"


def run(page, cfg, log, humanizer=None, popup=None, keepalive=None):
    """把当前页面的口语题依次处理（最多 max_items 个）。"""
    scfg = cfg.speaking
    if not scfg.enabled:
        log.info("  [口语] 未启用（config: speaking.enabled = false）")
        return {"handled": 0}

    handled = 0
    limit = int(scfg.max_items or 20)
    for i in range(limit):
        if not has_speaking_task(page):
            break
        status, note = handle_one(page, cfg, log, humanizer, popup, keepalive)
        log.info(f"  第 {i + 1} 段: {status} / {note}")
        if status in ("failed", "none"):
            break
        handled += 1
        page.wait_for_timeout(1500)
    return {"handled": handled}
