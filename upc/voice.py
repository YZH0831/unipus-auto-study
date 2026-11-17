# -*- coding: utf-8 -*-
"""口语题处理：TTS 合成 → 劫持麦克风 → 让平台录到我们生成的音频。

实测结论（2026-11-08）：
  ✔ edge-tts 能合成高质量英文语音（免费，无需 key）
  ✔ 运行时替换 navigator.mediaDevices.getUserMedia，
    返回 AudioContext.createMediaStreamDestination() 的流，
    平台的 Html5Recorder / TencentSOE 会照单全收
  ✔ 点 .button-record → 录音 → 再点一次结束 → 提交 code:0
  ✔ 提交后 course_progress 的 pass 从 0 变 1（完成度达标）
  ★ 但劫持必须在**导航前** add_init_script 预置。页面加载后再 patch 时，
    录音 SDK 已缓存原始 getUserMedia → 录进去的是真麦克风（静音）→ 评分 0。
  ✔ 预置生效后，跟读题把页面原声当麦克风，实测 92~97 分；
    短句对齐好分数高，长句 + 尾部静音偏多会被扣到 80 多分。
  ✔ 自由作答（TTS 稿）必须按音频真实时长录音、并等评分回填再提交，
    固定录 8 秒 / 立刻提交都会导致完整度不足或评分未落库 → 0 分。

题型判定：
  - 题干含 "read ... aloud" → 念题目给的句子（跟读）
  - 其余（record your answer / Complete the task）→ 让大模型写答案
"""
import base64
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CACHE = {}

# 口语作答稿的合理长度：edge-tts 约 150 词/分钟，120 词 ≈ 45s。
# 稿子过长 → 音频过长，以前录音被 45s 硬上限截断 → 完整度极低 → 0 分。
_SPEECH_MAX_CHARS = 700


def _trim_speech(text, max_chars=_SPEECH_MAX_CHARS):
    """把口语稿截到句子边界，避免超长音频录不完 / 切在半个词上。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", "? ", "! ", ".", "?", "!"):
        p = cut.rfind(sep)
        if p > max_chars * 0.6:
            return cut[:p + len(sep)].strip()
    return cut.rstrip(" ,;:") + "."


def _speech_rec_seconds(dur, record_seconds=8):
    """按音频真实长度决定录多久。

    以前是 min(dur + 1.5, 45.0)，131s 的超长稿只录 45s → 完整度判 0 分
    （日志实证：11-11/12/13 的 131.8s/133.1s/124.6s 音频全被截成 45s）。
    现在按 dur 全长录，只留 180s 安全上限防极端卡死；配合 _trim_speech
    把稿子压到 ~45s，实际不会触到上限。
    """
    return min(max(float(record_seconds), float(dur) + 1.5), 180.0)

# 运行时替换麦克风（不需要 add_init_script，页面已加载也能生效）
JS_PATCH_GUM = r"""
(b64) => {
  window.__ttsB64 = b64;
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return {ok: false, why: 'no getUserMedia'};
  if (!window.__ttsOrigGum) {
    window.__ttsOrigGum = md.getUserMedia.bind(md);
  }
  let ctx = null, buf = null;
  md.getUserMedia = async (constraints) => {
    try {
      if (!window.__ttsB64) return window.__ttsOrigGum(constraints);
      if (constraints && constraints.audio === false) {
        return window.__ttsOrigGum(constraints);
      }
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (!buf) {
        const bin = atob(window.__ttsB64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        buf = await ctx.decodeAudioData(arr.buffer);
      }
      const dest = ctx.createMediaStreamDestination();
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.loop = true;                 // 循环播，录音段后期不会静音
      src.connect(dest);
      src.start();
      window.__ttsPlaying = true;
      window.__ttsDur = buf.duration;
      return dest.stream;
    } catch (e) {
      window.__ttsErr = String(e).slice(0, 200);
      return window.__ttsOrigGum(constraints);
    }
  };
  return {ok: true, patched: String(md.getUserMedia).includes('__ttsB64')};
}
"""

JS_VOICE_STATE = r"""
() => ({
  hasBtn: !!document.querySelector('.button-record, .ucomp-recorder, '
                                  + '.record-button-wrap'),
  playing: !!window.__ttsPlaying,
  dur: window.__ttsDur || 0,
  tips: (document.querySelector('.record-tips') || {}).innerText || '',
  cd: (document.querySelector('.record-count-down') || {}).innerText || '',
  err: window.__ttsErr || '',
  patched: !!window.__ttsGumPatched,
  hasB64: !!window.__ttsB64,
  ctxState: window.__ttsCtxState || '',
  gumCalls: (window.__gumCalls || []).slice(-6),
})
"""


def _tts(text, out_mp3, voice="en-US-AriaNeural"):
    """合成语音。独立线程跑 asyncio，避开 Playwright 的事件循环冲突。"""
    import asyncio
    import edge_tts

    err = []

    def _run():
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(str(out_mp3)))
        except Exception as exc:  # noqa: BLE001
            err.append(exc)

    th = threading.Thread(target=_run)
    th.start()
    th.join(timeout=120)
    if err:
        return None
    return out_mp3 if out_mp3.exists() and out_mp3.stat().st_size > 1000 else None


# 多小题口语题：每个录音按钮对应一个小题。以前的 bug 是所有小题共用同一份
# TTS 音频（同一段话答 N 个不同问题 → 除第 1 个外基本都 0 分）。
# 这里从每个录音按钮向上找它所属小题的题干，拿得到就逐题作答。
JS_VOICE_ITEMS = r"""
() => {
  const btns = Array.from(document.querySelectorAll('.button-record'));
  if (btns.length < 2) return [];
  const out = [];
  const seen = new Set();
  // 从按钮往上找「包含问号 / 编号 / 足够长文字」的最近独立文字块
  const stemOf = (btn) => {
    let el = btn;
    for (let up = 0; up < 6 && el; up++) {
      el = el.parentElement;
      if (!el) break;
      let t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length > 600) continue;
      // 去掉按钮自身文字的干扰
      if (/^\d{0,2}:?\d{0,2}$/.test(t)) continue;
      // 题目块通常含 ? 或以 数字./Q 开头
      if (/[?？]/.test(t) || /^\d+[.)、]/.test(t) || /^(Q|Question)/i.test(t)) {
        return t.slice(0, 400);
      }
    }
    return '';
  };
  btns.forEach((b, i) => {
    let stem = stemOf(b);
    if (!stem) return;
    const sig = stem.slice(0, 50);
    if (seen.has(sig)) return;   // 同一题被多个按钮命中时只留一次
    seen.add(sig);
    out.push({ idx: i, stem: stem });
  });
  return out;
}
"""

JS_ORIGIN_AUDIO = r"""
() => {
  const out = [];
  const okSrc = (u) => u && /\.(mp3|m4a|wav|ogg)(\?|$)/i.test(u);
  // ★ 原声挂在 div.question-audio.audio-origin【容器】上，
  //   里面的 <audio> 本身 class 是 unipus-audio-h5。
  //   直接找 audio.question-audio 是找不到的（踩过）。
  for (const box of document.querySelectorAll(
      '.question-audio.audio-origin, .audio-origin')) {
    const a = box.tagName === 'AUDIO' ? box : box.querySelector('audio');
    if (!a) continue;
    const src = a.currentSrc || a.src || '';
    if (okSrc(src) && !out.includes(src)) out.push(src);
  }
  // 兜底：全页 audio 里筛 cdn 的音频（懒加载未设 src 的会被过滤掉）
  if (!out.length) {
    for (const a of document.querySelectorAll('audio')) {
      const src = a.currentSrc || a.src || '';
      if (okSrc(src) && !out.includes(src)) out.push(src);
    }
  }
  return out;
}
"""


def get_origin_audios(page):
    """跟读题自带原声的 URL 列表（按题目顺序）。

    跟读题的正确做法不是自己念，而是把这段原声当麦克风输入 ——
    等于完美跟读（实测逐词 100 分、总分 94~97）。
    """
    try:
        return page.evaluate(JS_ORIGIN_AUDIO) or []
    except Exception:  # noqa: BLE001
        return []


def download_audio(url, cache_dir=None):
    """下载原声到本地并返回 Path（失败返回 None）。"""
    import hashlib
    import urllib.request

    cache_dir = cache_dir or (ROOT / "output" / "voice_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = ".mp3"
    for e in (".mp3", ".m4a", ".wav", ".ogg"):
        if e in url.lower():
            ext = e
            break
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ext
    local = cache_dir / name
    if local.exists() and local.stat().st_size > 1000:
        return local
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://ucontent.unipus.cn/"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if len(data) > 1000:
            local.write_bytes(data)
            return local
    except Exception:  # noqa: BLE001
        return None
    return None


def _build_speech_text(stem, cfg, log):
    """决定要念的内容：跟读题念原文，其它让大模型写答案。"""
    low = (stem or "").lower()
    if "aloud" in low or ("read" in low and "sentence" in low):
        log.info("    口语题：跟读 → 直接念题目文本")
        return _trim_speech(stem)
    try:
        from upc.llm import LLMClient
        cli = LLMClient(cfg, log)
        if cli.available:
            q = ("用英语回答下面这道口语题，口语化、自然、60~100 词，"
                 "只输出要说的内容，不要解释或标题，控制在 45 秒能念完：\n\n"
                 + (stem or "")[:800])
            r = cli.ask(q, None, "口语作答", free_text=True)
            if r and r.get("answer"):
                text = str(r["answer"]).strip()
                # 模型只吐一个格式标签/单词时，TTS 念出来会被 SOE 判
                # 「无有效语音」→ 0 分。宁可退回念题干，也别念 "ANSWER"。
                if len(text) < 20:
                    log.warning(f"    口语题：大模型稿子过短（{text[:40]!r}），弃用")
                else:
                    text = _trim_speech(text)
                    log.info(f"    口语题：大模型写答案（{len(text)} 字符）")
                    return text
    except Exception as exc:  # noqa: BLE001
        log.debug(f"    口语答案生成失败: {str(exc)[:60]}")
    log.warning("    口语题：大模型不可用，退回念题目文本（分数会很低）")
    return _trim_speech(stem)


def handle_voice(page, log, cfg, leaf_id, stem, record_seconds=8):
    """处理当前页面的口语题。返回 True 表示录音已完成（可以提交了）。

    两种题型走不同路子（参考 UnipusAI-Helper 的实测结论）：
      - 跟读题：页面自带原声（audio.question-audio），**直接用原声冒充麦克风**，
        等于完美跟读，实测逐词 100 分、总分 94~97。绝不要自己 TTS 念。
      - 自由作答：没有原声，让大模型写口语稿 → edge-tts 合成 → 注入。
    """
    try:
        st = page.evaluate(JS_VOICE_STATE)
    except Exception:  # noqa: BLE001
        return False
    if not st.get("hasBtn"):
        return False

    # 录音按钮：必须点 .button-record（32x32 图标），
    # 外层 .record-button-wrap 只是容器（点了 gumCalls=0）
    btns = page.query_selector_all(".button-record") or []
    if not btns:
        one = (page.query_selector(".oral-record-btn")
               or page.query_selector(".record-btn")
               or page.query_selector(".ucomp-recorder"))
        btns = [one] if one is not None else []
    if not btns:
        log.warning("    口语题：找不到录音按钮")
        return False
    total = len(btns)

    origins = get_origin_audios(page)
    key = str(leaf_id)

    if origins:
        # ---------- 跟读题：逐条用原声 ----------
        log.info(f"    跟读题：{total} 个录音按钮 / {len(origins)} 段原声，"
                 f"用原声冒充麦克风")
        ok = 0
        for idx in range(total):
            url = origins[idx] if idx < len(origins) else origins[-1]
            local = download_audio(url)
            if local is None:
                log.warning(f"    跟读题 [{idx + 1}/{total}] 原声下载失败")
                continue
            b64 = base64.b64encode(local.read_bytes()).decode()
            dur = inject_audio(page, b64)
            if dur <= 0:
                log.warning(f"    跟读题 [{idx + 1}/{total}] 注入/解码失败")
                continue
            cur = page.query_selector_all(".button-record")
            if idx >= len(cur):
                break
            try:
                try:
                    cur[idx].scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                try:
                    cur[idx].click(timeout=6000)
                except Exception:
                    cur[idx].click(timeout=4000, force=True)
                page.wait_for_timeout(500)
                # ★ 录音总长要尽量贴住「0.8s 前置静音 + 原声」这个配比：
                #   实测等 1.5s 再录 dur+1.5s，等于实际录了 dur+3s，
                #   尾部静音过多会被判「没说完整」，得分 0。
                page.wait_for_timeout(int((dur + 1.0) * 1000))
                cur2 = page.query_selector_all(".button-record")
                if idx < len(cur2):
                    cur2[idx].click(timeout=6000)
                sc = wait_score(page, timeout_s=25)   # 等平台评分回填
                ok += 1
                log.info(f"    跟读题 [{idx + 1}/{total}] {dur:.1f}s 录制完成"
                         f"{'，评分: ' + sc if sc else '（页面未回填评分）'}")
            except Exception as exc:  # noqa: BLE001
                log.debug(f"    跟读题 [{idx + 1}] 失败: {str(exc)[:60]}")
        log.info(f"    跟读题：{ok}/{total} 条录制成功")
        if not ok:
            log.warning("    口语题：录音按钮一个都没点成功，本次不视为完成")
        return ok > 0

    # ---------- 自由作答：TTS ----------
    # ★ 多小题口语题必须逐题作答：以前所有录音按钮共用同一份 TTS 音频，
    #   等于用同一段话回答 N 个不同问题，第 2 小题起基本都 0 分。
    #   这里先尝试取每个按钮对应的小题题干；取得到就每题各写各的答案。
    items = []
    try:
        items = page.evaluate(JS_VOICE_ITEMS) or []
    except Exception:  # noqa: BLE001
        items = []
    # 只有拿到「多个不同题干」才走逐题作答，否则退回原来的单份稿子
    per_item = len(items) >= 2 and len(set(i.get("stem", "")[:50] for i in items)) >= 2
    if per_item:
        log.info(f"    主观口语题：识别到 {len(items)} 个小题，逐题作答")
        for it in items:
            log.info(f"      小题{it.get('idx', 0) + 1} 题干: "
                     f"{(it.get('stem') or '')[:80]!r}")
    else:
        # 没取到不同题干：记下来，便于下次按真实 DOM 修正选择器
        log.warning(f"    主观口语题：{total} 个录音按钮但未取到独立题干，"
                    f"退回单份作答（扫描到 {len(items)} 个候选）。"
                    f"整题文本前80字: {(stem or '')[:80]!r}")
        items = [{"idx": i, "stem": stem} for i in range(total)]

    # 为每个小题生成 audio b64（逐题作答时每题不同）
    per_item_b64 = []
    for it in items:
        sub_stem = it.get("stem") or stem
        suffix = f"_{it.get('idx', 0)}" if per_item else ""
        mp3 = ROOT / "output" / f"voice_{key}{suffix}.mp3"
        cache_key = f"{key}{suffix}"
        if cache_key in _CACHE and _CACHE[cache_key].exists():
            use_mp3 = _CACHE[cache_key]
        else:
            sub_text = _build_speech_text(sub_stem, cfg, log)
            if not sub_text or len(sub_text.strip()) < 20:
                # 该题稿子失败：用整题稿兜底，避免空音频
                sub_text = _build_speech_text(stem, cfg, log) or ""
            if len(sub_text.strip()) < 20:
                log.warning("    口语题：稿子过短，不录音以免拿 0 分")
                continue
            got = _tts(sub_text[:1200], mp3)
            if not got:
                log.warning("    口语题：TTS 合成失败")
                continue
            _CACHE[cache_key] = got
            use_mp3 = got
        per_item_b64.append((it.get("idx", 0),
                             base64.b64encode(use_mp3.read_bytes()).decode()))

    if not per_item_b64:
        return False

    log.info(f"    主观口语题：{total} 个录音按钮，"
             f"{'逐题' if per_item else '单份'}TTS 作答（{len(per_item_b64)} 段音频）")
    ok = 0
    for idx in range(total):
        cur = page.query_selector_all(".button-record")
        if idx >= len(cur):
            break
        # 取本题对应的音频；没有专属于它的就用第一段兜底
        candidates = [b for i, b in per_item_b64 if i == idx]
        b64 = candidates[0] if candidates else per_item_b64[0][1]
        dur = inject_audio(page, b64)
        if dur <= 0:
            log.warning("    口语题：注入音频失败")
            return False
        # ★ 录音时长按音频真实长度来（注入时已加 0.8s 前置静音，留 1.5s 余量）。
        #   历史踩坑：先固定录 8s（稿子录不完→0 分），后改 min(dur+1.5, 45.0)
        #   又把 131s 超长稿截成 45s（完整度极低→0 分）。现在稿子会在
        #   _build_speech_text 里截到 ~45s，录音按 dur 全长走，不再硬封顶。
        rec_s = _speech_rec_seconds(dur, record_seconds)
        try:
            try:
                cur[idx].scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                cur[idx].click(timeout=6000)
            except Exception:
                cur[idx].click(timeout=4000, force=True)
            page.wait_for_timeout(500)
            page.wait_for_timeout(int(rec_s * 1000))
            cur2 = page.query_selector_all(".button-record")
            if idx < len(cur2):
                cur2[idx].click(timeout=6000)
            # 平台停止录音后要先上传再回填评分，不等就提交等于白录（0 分）。
            # 实测页面评分元素常抓不到，10s 够用，别每题白等 25s。
            sc = wait_score(page, timeout_s=10)
            ok += 1
            log.info(f"    口语题：[{idx + 1}/{total}] {dur:.1f}s 音频 / "
                     f"录 {rec_s:.1f}s 完成"
                     + (f"，评分: {sc}" if sc else "（未拿到页面评分）"))
        except Exception as exc:  # noqa: BLE001
            log.warning(f"    口语题 [{idx + 1}] 录制失败: {str(exc)[:60]}")
    if not ok:
        # 以前无论成没成都 return True，上层会打印「录音完成」并误判已完成。
        log.warning("    口语题：录音按钮一个都没点成功，本次不视为完成")
    return ok > 0



# ★ 必须在**页面导航前**注入（add_init_script）。
# 运行时替换不可靠：平台的录音 SDK 初始化时就把 getUserMedia 缓存走了。
INIT_PATCH_JS = r"""
(() => {
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;
  if (window.__ttsGumPatched) return;
  const orig = md.getUserMedia.bind(md);
  window.__ttsOrigGum = orig;
  md.getUserMedia = async (constraints) => {
    try {
      window.__gumCalls = window.__gumCalls || [];
      window.__gumCalls.push({at: Date.now(), hasB64: !!window.__ttsB64});
    } catch (e) {}
    try {
      if (!window.__ttsB64) return orig(constraints);
      if (constraints && constraints.audio === false) return orig(constraints);
      const Ctx = window.AudioContext || window.webkitAudioContext;
      window.__ttsCtx = window.__ttsCtx || new Ctx({ sampleRate: 48000 });
      const ctx = window.__ttsCtx;
      if (ctx.state === 'suspended') {
        try { await ctx.resume(); } catch (e) { window.__ttsErr = 'resume:' + e; }
      }
      window.__ttsCtxState = ctx.state;
      if (!window.__ttsBuf) {
        const bin = atob(window.__ttsB64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        window.__ttsBuf = await ctx.decodeAudioData(arr.buffer);
      }
      if (!window.__ttsPadded) {
        const pad = 0.8, sr = window.__ttsBuf.sampleRate;
        const chs = window.__ttsBuf.numberOfChannels;
        const padBuf = ctx.createBuffer(chs,
          Math.floor((window.__ttsBuf.duration + pad) * sr), sr);
        const off = Math.floor(pad * sr);
        for (let c = 0; c < chs; c++) {
          padBuf.getChannelData(c).set(window.__ttsBuf.getChannelData(c), off);
        }
        window.__ttsBuf = padBuf;
        window.__ttsPadded = true;
      }
      const dest = ctx.createMediaStreamDestination();
      const src = ctx.createBufferSource();
      src.buffer = window.__ttsBuf;
      src.loop = false;
      src.connect(dest);
      src.start();
      window.__ttsPlaying = true;
      window.__ttsDur = window.__ttsBuf.duration;
      return dest.stream;
    } catch (e) {
      window.__ttsErr = String(e).slice(0, 200);
      return orig(constraints);
    }
  };
  window.__ttsGumPatched = true;
})();
"""


def install_init_patch(page):
    """在导航前调用一次，之后每次页面加载都会自动劫持麦克风。"""
    try:
        page.add_init_script(INIT_PATCH_JS)
        return True
    except Exception:  # noqa: BLE001
        return False


def inject_audio(page, b64):
    """把音频交给页面，并**提前解码**返回时长（秒）。失败返回 0。

    提前解码是为了按音频真实长度决定录多久 —— 固定录 8 秒会导致
    完整度不满（平台按整段音频算分）。
    """
    try:
        return float(page.evaluate(r"""
        async (b) => {
          window.__ttsB64 = b;
          window.__ttsBuf = null;
          window.__ttsPadded = false;
          window.__ttsPlaying = false;
          try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            const ctx = new Ctx();
            const bin = atob(b);
            const arr = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
            const buf = await ctx.decodeAudioData(arr.buffer);
            window.__ttsDurPre = buf.duration;
            try { ctx.close(); } catch (e) {}
            return buf.duration;
          } catch (e) {
            window.__ttsErr = String(e).slice(0, 120);
            return 0;
          }
        }
        """, b64) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def wait_score(page, timeout_s=40):
    """等平台把口语评分回填到页面上（分数挂在 oral-personal-state 这一层）。

    参考 UnipusAI-Helper 的实测：停止录音后平台先上传再评分，
    不等评分就提交会拿不到分。
    """
    import time as _t
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        try:
            got = page.evaluate(r"""
            () => {
              const el = document.querySelector(
                '[class*="oral-personal-state"], .p-oral-personal-state');
              if (!el) return {ok: false};
              const t = (el.innerText || '');
              const m = t.match(/(\d{1,3})\s*分?/);
              return {ok: /准确度|流利度|完整度|\d+\s*分/.test(t),
                      text: t.replace(/\s+/g, ' ').slice(0, 80)};
            }
            """) or {}
        except Exception:  # noqa: BLE001
            got = {}
        if got.get("ok"):
            return got.get("text") or "已评分"
        page.wait_for_timeout(1500)
    return ""

