# -*- coding: utf-8 -*-
"""视频 / 音频播放。

U校园的坑（实测/公开资料归纳）
------------------------------
1. 播放器在 `ipub.unipus.cn` 的 iframe 里，主页面 `query_selector('video')` 抓不到，
   必须遍历 frame。
2. 播放器会挂 `vjs-controls-disabled` 类，阻止脚本控制播放条 / 倍速。
3. 浏览器自动播放策略：不静音直接 `play()` 会被拒绝 → 先 muted 再播，播起来后再决定是否放开。
4. 平台会检测倍速：设了 >1 的倍速，学习时长可能不被计入。默认 1.0。
5. 有些 Task 是**音频**（听力题，纯 audio），逻辑跟视频一样，只是标签不同。
"""
import time

from . import selectors as S

JS_UNLOCK = r"""
(el) => {
  // 移除播放器的"禁用控制"类
  const unlock = ['vjs-controls-disabled', 'vjs-user-inactive', 'vjs-lock-showing'];
  const root = el.closest('.video-js') || el.parentElement;
  if (root) {
    unlock.forEach(c => root.classList.remove(c));
    root.classList.remove('vjs-controls-disabled');
    const disabled = root.querySelectorAll('.vjs-controls-disabled');
    disabled.forEach(n => n.classList.remove('vjs-controls-disabled'));
  }
  el.removeAttribute('controls');
  return true;
}
"""

JS_INFO = r"""
(el) => {
  const src = (el.currentSrc || el.getAttribute('src') || '').trim();
  let dur = 0;
  try { dur = isFinite(el.duration) ? el.duration : 0; } catch (e) { dur = 0; }
  return {
    src,
    dur,
    ready: el.readyState || 0,
    tag: (el.tagName || '').toLowerCase(),
    paused: !!el.paused,
    ended: !!el.ended,
    ct: el.currentTime || 0,
    rate: el.playbackRate || 1,
  };
}
"""


def _frames(page):
    try:
        return list(page.frames)
    except Exception:  # noqa: BLE001
        return [page]


def find_media(page, timeout_s=15, want=("video", "audio")):
    """在所有 frame 里找可用的视频/音频元素。

    返回 (frame, element, tag)；找不到返回 (None, None, None)。

    宽松策略（U校园 AI 版实测）：课件用流式播放，`src` 属性常常为空
    （靠 <source> / blob / MSE），所以**不能因为 src 为空就跳过**。
    判据改成：有时长 / 有 readyState / 能读到 currentTime 就算数。
    """
    deadline = time.time() + timeout_s
    best = (None, None, None)
    best_score = -1
    while time.time() < deadline:
        for fr in _frames(page):
            for tag in want:
                try:
                    els = fr.query_selector_all(tag)
                except Exception:  # noqa: BLE001
                    continue
                for el in els:
                    try:
                        info = el.evaluate(JS_INFO)
                    except Exception:  # noqa: BLE001
                        continue
                    dur = float(info.get("dur") or 0)
                    ready = int(info.get("ready") or 0)
                    src = (info.get("src") or "").strip()
                    # 打分：有时长最优先，其次 readyState，其次有 src
                    score = 0
                    if dur > 0:
                        score += 100
                    if ready > 0:
                        score += 10
                    if src:
                        score += 1
                    # 被隐藏的空占位（dur=0 且 ready=0 且无 src）直接跳
                    if score == 0:
                        continue
                    if score > best_score:
                        best_score = score
                        best = (fr, el, tag)
                    if dur > 0:
                        return fr, el, tag      # 最好情况，直接返回
        if best[1] is not None and time.time() > deadline - timeout_s + min(6, timeout_s):
            return best
        time.sleep(0.4)
    return best


def detect_no_speed(page):
    """页面是否声明"禁止倍速 / 必须真实观看"。"""
    for fr in _frames(page):
        try:
            text = fr.evaluate("() => (document.body ? document.body.innerText : '')") or ""
        except Exception:  # noqa: BLE001
            continue
        t = text.replace(" ", "")
        if any(h in t for h in S.NOSPEED_HINTS):
            return True
    return False


def _set_rate(el, rate, mute):
    try:
        el.evaluate(
            """(el) => {
              el.muted = true;
              el.defaultPlaybackRate = 1;
              el.playbackRate = 1;
              const p = el.play(); if (p && p.catch) p.catch(()=>{});
            }""", el
        )
    except Exception:  # noqa: BLE001
        pass
    # 播起来后再设倍速（有些播放器初始化时才接受）
    time.sleep(0.6)
    try:
        el.evaluate(
            """(el, args) => {
              const [rate, mute] = args;
              try { el.defaultPlaybackRate = rate; } catch(e){}
              try { el.playbackRate = rate; } catch(e){}
              try { el.muted = mute; } catch(e){}
            }""", [rate, mute]
        )
    except Exception:  # noqa: BLE001
        pass


def _click_play_button(frame):
    for sel in S.VIDEO_PLAY_BUTTON:
        try:
            el = frame.query_selector(sel)
            if el is not None and el.is_visible():
                el.click(timeout=3000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def play_video(session, cfg, log, page, popup=None, humanizer=None, keepalive=None):
    """播放当前页的视频/音频，返回 (status, note)。

    status: done / skipped / failed / manual
    """
    vcfg = cfg.video
    if not vcfg.enabled:
        return "skipped", "配置里关闭了视频播放"

    # 先清弹窗，否则播放按钮常被遮住
    if popup:
        popup.sweep(page, rounds=2)

    frame, el, tag = find_media(page, timeout_s=15)
    if el is None:
        return "none", "本页没有视频/音频"

    try:
        info = el.evaluate(JS_INFO)
    except Exception:  # noqa: BLE001
        info = {}
    dur = float(info.get("dur") or 0)
    log.info(f"  发现 {tag}（时长 {dur:.0f}s）")

    # 解锁控制 + 关闭遮挡
    try:
        el.evaluate(JS_UNLOCK, el)
    except Exception:  # noqa: BLE001
        pass
    if info.get("paused"):
        if not _click_play_button(frame):
            log.debug("  未找到播放按钮，直接调用 play()")

    no_speed = detect_no_speed(page)
    rate = 1.0 if no_speed else float(vcfg.speed)
    if no_speed and float(vcfg.speed) > 1.0:
        log.info("  平台声明禁止倍速/需真实观看 → 强制 1.0x")

    _set_rate(el, rate, bool(vcfg.mute))
    if rate != 1.0:
        log.info(f"  播放倍速 {rate}x")

    if not vcfg.wait_end:
        return "done", f"已开始播放（不等待结束）rate={rate}"

    # ---------------- 等待播放结束 ----------------
    limit_s = float(vcfg.timeout_per_video_min) * 60
    poll = max(0.8, float(vcfg.poll_interval_s))
    start = time.time()
    last_ct = -1.0
    stall = 0
    paused_cnt = 0
    re_play = 0
    last_log = 0.0

    while True:
        if keepalive is not None:
            try:
                if not keepalive("视频播放"):
                    return "manual", "播放期间会话中断"
            except Exception:  # noqa: BLE001
                pass
        if popup:
            popup.tick(page)
        session.keep_alive()

        try:
            st = el.evaluate(JS_INFO)
        except Exception:  # noqa: BLE001
            # 元素失效（iframe 重载）→ 重新找
            frame, el, tag = find_media(page, timeout_s=8)
            if el is None:
                return "failed", "播放元素丢失（页面可能跳转）"
            _set_rate(el, rate, bool(vcfg.mute))
            continue

        ct = float(st.get("ct") or 0)
        dur = float(st.get("dur") or 0)
        elapsed = time.time() - start

        if st.get("ended") or (dur > 0 and ct >= dur - 0.5):
            log.info(f"  {tag} 播放完成（用时 {elapsed / 60:.1f} 分钟）")
            break

        if st.get("paused"):
            paused_cnt += 1
            if paused_cnt in (3, 8) and re_play < int(vcfg.retry_play) * 3:
                re_play += 1
                if not _click_play_button(frame):
                    _set_rate(el, rate, bool(vcfg.mute))
        else:
            paused_cnt = 0

        if abs(ct - last_ct) < 0.01:
            stall += 1
            if stall in (4, 12):
                log.debug("  进度停滞，尝试恢复播放")
                _set_rate(el, rate, bool(vcfg.mute))
            if stall >= 20:
                _click_play_button(frame)
                stall = 0
        else:
            stall = 0
        last_ct = ct

        if elapsed - last_log >= 60 and dur > 0:
            last_log = elapsed
            log.info(f"  进度 {ct:.0f}/{dur:.0f}s ({ct / dur * 100:.0f}%)")

        if elapsed > limit_s:
            return "failed", f"播放超时（{vcfg.timeout_per_video_min} 分钟），进度 {ct:.0f}s"

        time.sleep(poll)

    # 播放后停留：让平台的上报/心跳送达（否则常常"播完了但没记录"）
    if humanizer:
        try:
            page.wait_for_timeout(2500)
            humanizer.nudge(page)
        except Exception:  # noqa: BLE001
            pass
    page.wait_for_timeout(2000)
    return "done", f"播放完成 rate={rate}"
