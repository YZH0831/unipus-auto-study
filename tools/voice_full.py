# -*- coding: utf-8 -*-
"""口语题完整链路验证：TTS → 劫持麦克风 → 点录音 → 结束 → 提交。

实验目标只有一个：确认平台能不能"接受"我们喂进去的合成音频。
内容对不对先不管（先用题目文本占位），链路通了再换成 LLM 生成的答案。
"""
import base64
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
TARGET_ID = "7190eb77c002af7"      # 录音题：Complete the task and record your answer

INIT_JS = r"""
(() => {
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;
  const orig = md.getUserMedia.bind(md);
  let ctx = null, buf = null;
  md.getUserMedia = async (constraints) => {
    try {
      if (!window.__ttsB64) return orig(constraints);
      if (constraints && constraints.audio === false) return orig(constraints);
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
      src.loop = true;                 // 循环播，避免录音段后期静音
      src.connect(dest);
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


def tts(text, out_mp3, voice="en-US-AriaNeural"):
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
        print("TTS 出错:", str(err[0])[:120])
        return False
    return out_mp3.exists() and out_mp3.stat().st_size > 1000


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import _clear_modals, build_leaves, JS_FIND_SUBMIT

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    p.add_init_script(INIT_JS)
    subs = []

    def _on(r):
        if "/newExploration/submit" in (r.url or ""):
            try:
                subs.append(r.json())
            except Exception:  # noqa: BLE001
                pass

    p.on("response", _on)
    # 允许麦克风权限（虽然是假设备，权限还是要过）
    try:
        s.context.grant_permissions(["microphone"])
    except Exception:  # noqa: BLE001
        pass
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
        tgt = next((x for x in leaves if str(x.get("id")) == TARGET_ID), None)
        if not tgt:
            print("找不到目标叶子")
            return 1

        url = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid=1847183133920289074&appid=116#/{INST}/courseware/{tgt['path']}")
        p.goto("about:blank")
        p.wait_for_timeout(400)
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(12000)
        _clear_modals(p, log)
        p.wait_for_timeout(800)

        body = p.evaluate("() => document.body ? document.body.innerText : ''") or ""
        i = body.find("Directions:")
        seg = body[i:i + 800].replace("\n", " ") if i >= 0 else ""
        print("题目:", seg[:200])

        # 决定念什么：
        #  - "read ... aloud"（跟读）→ 直接念题目给的句子
        #  - 其他（record your answer / Complete the task）→ 让大模型写答案
        speak_text = ""
        low = seg.lower()
        if "read" in low and "aloud" in low:
            speak_text = seg
            print("题型：跟读 → 念题目文本")
        else:
            print("题型：自由作答 → 让大模型写答案")
            from upc.config import load_config as _lc
            from upc.llm import LLMClient
            _cfg = _lc(ROOT / "config.yaml", strict=False)
            _cli = LLMClient(_cfg, log)
            if _cli.available:
                _q = ("用英语回答下面这道口语题，要求口语化、自然、80~120 词，"
                      "只输出要说的内容，不要任何解释或标题：\n\n" + seg[:800])
                _r = _cli.ask(_q, None, "口语作答")
                if _r and _r.get("answer"):
                    speak_text = str(_r["answer"])
                    print("大模型答案:", speak_text[:160])
            if not speak_text:
                print("大模型不可用，退回念题目文本（分数会很低）")
                speak_text = seg

        mp3 = ROOT / "output" / "voice_ans.mp3"
        if not tts(speak_text[:1200] or "This is a test answer.", mp3):
            return 1
        print(f"\n待念内容（{len(speak_text)} 字符）: {speak_text[:200]}")
        b64 = base64.b64encode(mp3.read_bytes()).decode()
        p.evaluate("(b) => { window.__ttsB64 = b; }", b64)
        hook1 = p.evaluate("() => ({b64: !!window.__ttsB64, patched: "
                           "String(navigator.mediaDevices.getUserMedia)"
                           ".includes('__ttsB64')})")
        print("注入后:", json.dumps(hook1, ensure_ascii=False))

        # 点录音
        btn = p.query_selector(".button-record") or p.query_selector(".ucomp-recorder")
        if not btn:
            print("找不到录音按钮")
            return 1
        print("点击录音…")
        btn.click(timeout=8000)
        p.wait_for_timeout(3000)
        st = p.evaluate("() => ({playing: !!window.__ttsPlaying, "
                        "dur: window.__ttsDur||0, err: window.__ttsErr||''})")
        print("录音中状态:", json.dumps(st, ensure_ascii=False))

        # 录 15 秒（让平台有足够音频）
        p.wait_for_timeout(15000)
        st2 = p.evaluate(r"""
        () => ({playing: !!window.__ttsPlaying, err: window.__ttsErr||'',
                btnCls: (document.querySelector('.button-record')||{}).className || '',
                tips: (document.querySelector('.record-tips')||{}).innerText || '',
                cd: (document.querySelector('.record-count-down')||{}).innerText || ''})
        """)
        print("15 秒后:", json.dumps(st2, ensure_ascii=False))

        # 结束录音：点 record-button-wrap（比 icon 更稳）
        print("结束录音…")
        btn2 = (p.query_selector(".record-button-wrap")
                or p.query_selector(".button-record")
                or p.query_selector(".ucomp-recorder"))
        if btn2:
            try:
                btn2.click(timeout=8000)
            except Exception as exc:  # noqa: BLE001
                print("  点击失败:", str(exc)[:60])
        p.wait_for_timeout(10000)
        st3 = p.evaluate(r"""
        () => ({btnCls: (document.querySelector('.button-record')||{}).className || '',
                tips: (document.querySelector('.record-tips')||{}).innerText || '',
                hasAudio: !!(document.querySelector('audio')),
                body: (document.body?document.body.innerText:'').slice(0,0)})
        """)
        print("结束后状态:", json.dumps(st3, ensure_ascii=False))
        after = p.evaluate("() => document.body ? document.body.innerText : ''") or ""
        j = after.find("Directions:")
        print("结束后页面:", after[j:j + 200].replace("\n", " ") if j >= 0 else "")

        # 提交
        r = p.evaluate(JS_FIND_SUBMIT) or {}
        print("提交按钮:", r.get("hit"))
        if r.get("hit"):
            b = p.query_selector('[data-upc-submit="1"]')
            if b:
                b.click(timeout=6000)
        p.wait_for_timeout(5000)
        modal = p.evaluate(r"""
        () => {
          const out = [];
          for (const m of document.querySelectorAll('.ant-modal,[class*="message"]')) {
            const rr = m.getBoundingClientRect();
            if (rr.width < 40) continue;
            const t = (m.innerText || '').replace(/\s+/g, ' ').trim();
            if (t) out.push(t.slice(0, 160));
          }
          return Array.from(new Set(out));
        }
        """) or []
        print("提交后弹窗:", modal[:3])
        if subs:
            d = subs[0] or {}
            print("submit code:", d.get("code"), "| scorePct:",
                  ((d.get("data") or {}).get("record_grade") or {}).get("scorePct"))
        (ROOT / "output" / "voice_result.json").write_text(
            json.dumps({"subs": subs, "hook": hook1, "state": st},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n结果已存 output/voice_result.json")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
