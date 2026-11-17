# -*- coding: utf-8 -*-
"""口语题自动化实验：TTS 合成 → 劫持 getUserMedia 喂给平台录音 → 提交。

原理：
  平台用 Html5Recorder + TencentSOE 录麦。我们劫持
  navigator.mediaDevices.getUserMedia，返回一个
  AudioContext.createMediaStreamDestination() 的流，
  里面播放 edge-tts 合成的音频。平台录到的就是这段 TTS。
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"

# 页面加载前注入：劫持麦克风
INIT_JS = r"""
(() => {
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;
  const orig = md.getUserMedia.bind(md);
  let ctx = null, buf = null;

  md.getUserMedia = async (constraints) => {
    try {
      if (!window.__ttsB64) return orig(constraints);
      const needAudio = !constraints || constraints.audio;
      if (!needAudio) return orig(constraints);

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
      src.connect(dest);
      src.connect(ctx.destination);   // 同时也放出来，便于人工判断
      src.start();
      window.__ttsPlaying = true;
      window.__ttsDur = buf.duration;
      return dest.stream;
    } catch (e) {
      window.__ttsErr = String(e).slice(0, 200);
      return orig(constraints);
    }
  };
})();
"""

JS_FIND_REC = r"""
() => {
  const out = {btns: [], hook: {}, all: []};
  out.hook = {
    hasB64: !!window.__ttsB64,
    gumPatched: !!(navigator.mediaDevices &&
                   String(navigator.mediaDevices.getUserMedia).includes('__ttsB64')),
    playing: !!window.__ttsPlaying,
    dur: window.__ttsDur || 0,
    err: window.__ttsErr || '',
  };
  for (const e of document.querySelectorAll('*')) {
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    const cls = typeof e.className === 'string' ? e.className : '';
    const r = e.getBoundingClientRect();
    const visible = r.width > 8 && r.height > 8;
    // 录音相关：class 命中 或 文本含"录音"
    if (/record|voice|speak|mic/i.test(cls) && visible) {
      out.all.push({tag: e.tagName.toLowerCase(), cls: cls.slice(0, 56),
                    t: t.slice(0, 26), w: Math.round(r.width), h: Math.round(r.height)});
    }
    if (/录音/.test(t) && t.length < 40 && visible) {
      out.btns.push({tag: e.tagName.toLowerCase(), cls: cls.slice(0, 50),
                     t: t.slice(0, 30), w: Math.round(r.width)});
    }
    if (out.all.length > 25) break;
  }
  return out;
}
"""


def tts(text, out_mp3, voice="en-US-AriaNeural"):
    """在独立线程里跑 asyncio —— Playwright 同步 API 内部已有事件循环，
    直接 asyncio.run 会报 "cannot be called from a running event loop"。"""
    import threading
    import edge_tts

    err = []

    def _run():
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(str(out_mp3)))
        except Exception as exc:  # noqa: BLE001
            err.append(exc)

    th = threading.Thread(target=_run)
    th.start()
    th.join(timeout=90)
    if err:
        print("TTS 出错:", str(err[0])[:120])
        return False
    return out_mp3.exists() and out_mp3.stat().st_size > 1000


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import (_clear_modals, build_leaves, JS_FIND_SUBMIT,
                          JS_QUIZ_STATE)

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    p.add_init_script(INIT_JS)
    subs = []
    p.on("response", lambda r: subs.append(r.json())
         if "/newExploration/submit" in (r.url or "") else None)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid=1847183133920289074&appid=116#/{INST}/courseware/"
               "71e31dc39002af7/71e31df040024c9/71e31e2c1002af7/71e31e4d20024c9",
               context="课件")
        p.wait_for_timeout(7000)
        raw = p.evaluate(r"""
        async (inst) => {
          const r = await fetch(`/course/api/course/${inst}/default`,
                                {credentials:'include'});
          return await r.json();
        }
        """, INST)
        cs = (raw or {}).get("course")
        struct = json.loads(cs) if isinstance(cs, str) else cs
        leaves, _m = build_leaves(struct)
        # 口语跟读叶（实测 id）；m4 里有多个同名 Exercise，
        # 7190e54750124c9 其实是"茶"的选择题，不是口语
        WANT_ID = "7190eb77c002af7"
        tgt = next((x for x in leaves if str(x.get("id")) == WANT_ID), None)
        if tgt is None:
            tgt = next((x for x in leaves
                        if x.get("micro_idx") == 4 and x.get("tab_type") == "task"
                        and str(x.get("name")) == "Exercise 1"), None)
        if not tgt:
            print("找不到 m4 Exercise 1")
            return 1
        print("目标:", tgt["name"], tgt["id"])

        # 用 about:blank 强制重建，避免 hash 路由不进页
        url = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{tgt['path']}")
        p.goto("about:blank")
        p.wait_for_timeout(500)
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(12000)
        _clear_modals(p, log)
        p.wait_for_timeout(800)

        body = p.evaluate("() => document.body ? document.body.innerText : ''") or ""
        print("页面是否含读题指令:", "read" in body.lower() and "aloud" in body.lower())
        i = body.find("Directions:")
        seg = body[i:i + 700].replace("\n", " ") if i >= 0 else body[:500]
        print("\n=== 题目区 ===\n", seg[:520])

        rec = p.evaluate(JS_FIND_REC) or {}
        print("\n=== 劫持状态 ===", json.dumps(rec.get("hook"), ensure_ascii=False))
        print("=== 含「录音」的元素 ===")
        for b in (rec.get("btns") or [])[:8]:
            print(f"  {b['tag']:<5} {b['w']:>4} {b['cls'][:40]:<42} {b['t'][:24]}")
        print("=== class 含 record/voice 的元素 ===")
        for b in (rec.get("all") or [])[:14]:
            print(f"  {b['tag']:<5} {b['w']:>4}x{b['h']:<4} {b['cls'][:44]:<46} {b['t'][:22]}")

        # 合成 TTS：把 Directions 之后的英文句子念出来
        import re as _re
        eng = _re.sub(r"[\u4e00-\u9fff]+", " ", seg)
        eng = _re.sub(r"\s+", " ", eng).strip()
        if len(eng) < 20:
            print("\n没提取到足够英文文本，退出")
            return 1
        text = eng[:600]
        mp3 = ROOT / "output" / "tts_leaf.mp3"
        if not tts(text, mp3):
            print("TTS 失败")
            return 1
        b64 = base64.b64encode(mp3.read_bytes()).decode()
        print(f"\nTTS 就绪（{len(b64)//1024} KB base64），待念文本: {text[:120]}")
        p.evaluate("(b) => { window.__ttsB64 = b; }", b64)

        (ROOT / "output" / "voice_try.json").write_text(json.dumps({
            "leaf": tgt["id"], "text": text,
            "rec_btns": rec.get("btns"), "tips": rec.get("tips"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== 已注入劫持，等待人工在地验证（本脚本只做探测）===")
        p.wait_for_timeout(2000)
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
