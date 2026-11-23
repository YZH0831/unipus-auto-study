# -*- coding: utf-8 -*-
"""单元测试 / 作业答题。

解析策略
--------
不靠猜"题目容器"的 class（那玩意每个课程都不一样），而是**从 input 反推结构**：
先找页面里所有 `input[type=radio|checkbox]`，按 `name` 分组，每组就是一道题，
组的公共祖先是题目容器，容器里每个 label 的文本是选项。
这个思路对 U校园这类 antd / 自研混合表单都成立，也天然免疫改版。

答题链路（strategy=hybrid 时逐级回退）
----------------------------------
    本地题库(answer cache) → 第三方题库 → 大模型 → 试答嗅探 → 兜底（不留空）
"""
import hashlib
import json
import random
import re
import time
from pathlib import Path

from . import selectors as S
from .sniff import ResponseSniffer


def _llm_suspicious(raw):
    """二次复核的信号之一：模型输出含占位符/空壳/超短答案，判为可疑。"""
    s = str(raw or "").strip()
    if not s:
        return True
    if re.search(r"<\s*(答案|answer)\s*>|placeholder|不知道|无法判断|不确定答案", s,
                 re.IGNORECASE):
        return True
    # 只有置信度行、没有实质答案
    lines = [x for x in s.splitlines() if x.strip()]
    if not lines:
        return True
    return len(lines) == 1 and bool(re.match(r"\s*CONF\s*[:：]", lines[0], re.IGNORECASE))


# ---------------------------------------------------------------------------
# 扫描：从 input 反推题目结构，并给元素打上定位标记
# ---------------------------------------------------------------------------
JS_SCAN = r"""
() => {
  const out = [];

  // ---------- 1) 选择类（radio / checkbox） ----------
  const inputs = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
  const groups = new Map();
  inputs.forEach((el, i) => {
    if (!el.name) {
      // 没有 name 的，用最近的题目容器兜底分组
      let p = el.parentElement, depth = 0;
      while (p && depth < 6 && !p.querySelectorAll('input[type="radio"],input[type="checkbox"]').length > 1) {
        p = p.parentElement; depth++;
      }
      const key = '__noname_' + (p ? Array.from(document.body.children).indexOf(p) : i);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(el);
      return;
    }
    if (!groups.has(el.name)) groups.set(el.name, []);
    groups.get(el.name).push(el);
  });

  const commonAncestor = (els) => {
    if (!els.length) return null;
    let node = els[0].parentElement;
    while (node) {
      if (els.every(e => node.contains(e))) return node;
      node = node.parentElement;
    }
    return els[0].parentElement;
  };

  let qi = 0;
  for (const [name, els] of groups) {
    if (!els.length) continue;
    const visible = els.filter(e => e.getClientRects().length > 0);
    if (!visible.length) continue;

    const box = commonAncestor(visible);
    if (!box) continue;

    const type = visible[0].type === 'checkbox' ? 'multi' : 'single';

    // 选项：优先 label[for] / 包裹的 label
    const options = [];
    visible.forEach((inp, oi) => {
      let labelEl = null;
      if (inp.id) {
        try { labelEl = box.querySelector('label[for="' + inp.id + '"]'); } catch (e) {}
      }
      if (!labelEl) labelEl = inp.closest('label');
      if (!labelEl) {
        // antd: input 在 span 里，文本在兄弟节点
        let p = inp.parentElement, d = 0;
        while (p && d < 4 && (!p.innerText || !p.innerText.trim())) { p = p.parentElement; d++; }
        labelEl = p;
      }
      const text = (labelEl ? labelEl.innerText : '') || '';
      const clean = text.replace(/\s+/g, ' ').trim();
      // 去掉开头的 A. / A、 / (A)
      const m = clean.match(/^[（(]?([A-Ha-h])[）)]?\s*[.．、:：]?\s*(.*)$/);
      const letter = m ? m[1].toUpperCase() : String.fromCharCode(65 + oi);
      const body = m ? m[2].trim() : clean;
      inp.setAttribute('data-upc-opt', qi + '_' + oi);
      if (labelEl && labelEl !== inp) labelEl.setAttribute('data-upc-opt', qi + '_' + oi);
      options.push({
        oi, letter, text: body || clean, full: clean,
        checked: !!inp.checked,
      });
    });

    // 题干：容器文本 减去 所有选项文本
    let stemText = (box.innerText || '').replace(/\s+/g, ' ').trim();
    for (const opt of options) {
      if (opt.full) stemText = stemText.split(opt.full).join(' ');
    }
    stemText = stemText.replace(/\s+/g, ' ').trim().slice(0, 800);

    box.setAttribute('data-upc-q', String(qi));
    out.push({
      qi, type, name: name || ('q' + qi),
      stem: stemText, options,
      label: '',
    });
    qi++;
  }

  // ---------- 2) 填空 / 翻译（text / textarea） ----------
  const blanks = Array.from(document.querySelectorAll(
    'input[type="text"], input:not([type]), textarea'));
  blanks.forEach((el, bi) => {
    if (el.getClientRects().length === 0) return;
    if (el.readOnly || el.disabled) return;
    const tag = (el.tagName || '').toLowerCase();
    // 排除搜索框之类
    const ph = (el.placeholder || '') + (el.className || '');
    if (/search|搜索|filter/i.test(ph)) return;

    let p = el.parentElement, d = 0;
    while (p && d < 5 && (!p.innerText || p.innerText.trim().length < 3)) { p = p.parentElement; d++; }
    const stem = p ? (p.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 800) : '';

    el.setAttribute('data-upc-blank', String(bi));
    out.push({
      qi: qi + bi, type: tag === 'textarea' ? 'essay' : 'fill',
      name: 'blank_' + bi, stem, options: [], label: '',
      blankIdx: bi,
    });
  });

  return out;
}
"""

JS_COLLECT_FEEDBACK = r"""
() => {
  const hits = [];
  const push = (t) => { if (t) hits.push(t); };

  // 1) 明确的反馈区
  const sel = ['.correct-answer', '.answer-display', '.result-content', '.feedback',
               '.explanation', '[class*="correct"]', '[class*="analysis"]',
               '[class*="answer"]', '[class*="result"]'];
  for (const s of sel) {
    let els = [];
    try { els = document.querySelectorAll(s); } catch (e) { continue; }
    for (const el of els) {
      const t = (el.innerText || '').trim();
      if (t && t.length < 500) push(t);
    }
  }
  // 2) 全页兜底
  const body = document.body ? document.body.innerText : '';
  const re = /(正确答案|参考答案|标准答案)\s*[:：]\s*([^\n\r]{1,120})/g;
  let m;
  while ((m = re.exec(body)) !== null) push(m[0]);
  return hits;
}
"""


# ---------------------------------------------------------------------------
class Question:
    __slots__ = ("qi", "qtype", "name", "stem", "options", "answer", "source",
                 "confidence", "frame", "blank_idx")

    def __init__(self, d):
        self.qi = d.get("qi", 0)
        self.qtype = d.get("type", "single")
        self.name = d.get("name", "")
        self.stem = d.get("stem", "")
        self.options = d.get("options") or []
        self.blank_idx = d.get("blankIdx")
        self.answer = None
        self.source = ""
        self.confidence = 0.0
        self.frame = None

    @property
    def key(self):
        raw = re.sub(r"\s+", "", (self.stem or "") + "|" + "|".join(
            (o.get("text") or "") for o in self.options))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def letters(self):
        """把答案字符串拆成字母列表。"""
        if not self.answer:
            return []
        return [x.strip().upper() for x in re.split(r"[,，、;；\s/]+", str(self.answer))
                if x.strip()]

    def __repr__(self):
        return f"<Q{self.qi} {self.qtype} {self.stem[:30]!r} opts={len(self.options)}>"


class AnswerStore:
    """本地题库：题干哈希 -> 答案。"""

    def __init__(self, path, log):
        self.path = Path(path)
        self.log = log
        self.data = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) or {}
            self.log.info(f"已加载本地题库 {len(self.data)} 条")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"题库读取失败（将重建）: {str(exc)[:80]}")
            self.data = {}

    def get(self, key):
        rec = self.data.get(key)
        if not rec:
            return None
        return rec.get("answer")

    def put(self, key, answer, source="", stem=""):
        if not key or not answer:
            return
        self.data[key] = {"answer": answer, "source": source,
                          "stem": (stem or "")[:200], "ts": int(time.time())}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.log.info(f"题库已保存（{len(self.data)} 条）: {self.path}")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"题库保存失败: {str(exc)[:80]}")


# ---------------------------------------------------------------------------
class QuizSolver:
    def __init__(self, session, cfg, log, humanizer=None, popup=None):
        self.session = session
        self.cfg = cfg
        self.log = log
        self.humanizer = humanizer
        self.popup = popup
        self.store = AnswerStore(cfg.quiz.answers_file, log)
        self.sniffer = ResponseSniffer(log)
        self.llm = None
        if cfg.quiz.llm.enabled:
            from .llm import LLMClient
            self.llm = LLMClient(cfg, log)
            if not self.llm.available:
                self.log.info("大模型未配置 api_key，跳过 LLM 答题")
                self.llm = None
        self.rechecks = 0
        self.solved = 0
        self.failed = 0

    # ------------------------------------------------------------------
    def _scopes(self, page):
        scopes = [page]
        try:
            for fr in page.frames:
                if fr is not page.main_frame:
                    scopes.append(fr)
        except Exception:  # noqa: BLE001
            pass
        return scopes

    def scan(self, page):
        """扫描所有 frame，返回 Question 列表（附带所属 frame）。"""
        found = []
        for scope in self._scopes(page):
            try:
                raw = scope.evaluate(JS_SCAN) or []
            except Exception:  # noqa: BLE001
                continue
            if not raw:
                continue
            for d in raw:
                q = Question(d)
                q.frame = scope
                found.append(q)
        # 过滤掉明显不是题目的（题干太短且无选项）
        found = [q for q in found
                 if q.options or len(q.stem) >= 6 or q.qtype in ("fill", "essay")]
        # 按 qi 去重（同一题可能在多个 frame 里被扫到）
        uniq, seen = [], set()
        for q in found:
            sig = (q.qtype, re.sub(r"\s+", "", q.stem)[:80],
                   tuple(o.get("text", "")[:20] for o in q.options))
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(q)
        return uniq

    # ------------------------------------------------------------------
    def answer_question(self, q, allow_sniff=False):
        """给一道题求答案。返回 (answer, source, confidence) 或 (None,'',0)。"""
        # ① 本地题库
        cached = self.store.get(q.key)
        if cached:
            return cached, "cache", 0.95

        # ② 大模型 + 程序自主判断的二次复核
        if self.llm is not None and self.llm.available:
            from .llm import RECHECK_SYSTEM_PROMPT
            opts = [o.get("full") or o.get("text", "") for o in q.options] or None
            got = self.llm.ask(q.stem, opts, q.qtype)
            if got:
                conf = float(got.get("confidence", 0.7) or 0.7)
                llm_cfg = self.cfg.quiz.get("llm", {}) or {}
                need, why = self._should_recheck(conf, got, llm_cfg)
                if need:
                    self.rechecks += 1
                    # 换独立复核视角再唤起一次 DeepSeek
                    got2 = self.llm.ask(q.stem, opts, q.qtype,
                                        system=RECHECK_SYSTEM_PROMPT)
                    if got2:
                        conf2 = float(got2.get("confidence", 0.7) or 0.7)
                        same = self.llm._same_answer(got["answer"], got2["answer"])
                        if same:
                            boosted = max(conf, conf2, 0.85)
                            self.log.info(f"    二次复核一致（{why}）→ {got['answer']}"
                                          f"（置信 {conf:.2f}→{boosted:.2f}）")
                            return got["answer"], "llm复核一致", boosted
                        # 冲突：不擅自选边，降置信交后续嗅探/兜底机制纠正
                        self.log.warning(f"    二次复核冲突：首答 {got['answer']} / "
                                         f"复核 {got2['answer']} → 保留首答并降置信")
                        return got["answer"], "llm复核冲突", min(conf, 0.5)
                    self.log.info(f"    二次复核调用失败，保留首答（{why}）")
                return got["answer"], "llm", conf

        return None, "", 0.0

    def _should_recheck(self, conf, got, llm_cfg):
        """程序自主判断：置信低 / 输出可疑时才二次唤起 DeepSeek，省 API。"""
        if not bool(llm_cfg.get("recheck_enabled", True)):
            return False, "复核已关闭"
        if self.rechecks >= int(llm_cfg.get("max_rechecks", 40) or 0):
            return False, "复核次数已用尽"
        below = float(llm_cfg.get("recheck_below", 0.80) or 0)
        # 信号1：模型输出里带可疑标记（占位符类、超短答案）
        raw = str(got.get("raw", ""))
        if _llm_suspicious(raw):
            return True, "模型输出可疑"
        # 信号2：自报置信低于阈值
        if conf < below:
            return True, f"置信 {conf:.2f} < {below:.2f}"
        return False, "高置信，无需复核"

    # ------------------------------------------------------------------
    def fill(self, page, q, answer):
        """把答案填进页面。返回是否成功。"""
        if not answer:
            return False
        scope = q.frame or page
        letters = q.letters()

        if q.qtype in ("single", "multi"):
            if not q.options:
                return False
            # 字母 → 下标（按页面顺序，而不是按字母本身，避免选项顺序差异）
            targets = []
            for letter in letters:
                idx = None
                for i, opt in enumerate(q.options):
                    if str(opt.get("letter", "")).upper() == letter:
                        idx = i
                        break
                if idx is None and letter.isdigit():
                    idx = int(letter) - 1
                if idx is not None and 0 <= idx < len(q.options):
                    targets.append(idx)
            if not targets:
                self.log.warning(f"    答案 {answer} 无法对到任何选项（选项 {[o.get('letter') for o in q.options]}）")
                return False
            ok = False
            for idx in targets:
                sel = f'[data-upc-opt="{q.qi}_{idx}"]'
                try:
                    els = scope.query_selector_all(sel)
                except Exception:  # noqa: BLE001
                    els = []
                el = next((e for e in els if e.is_visible()), None)
                if el is None:
                    continue
                try:
                    if self.humanizer:
                        self.humanizer.click(page, el, timeout=6000)
                    else:
                        el.click(timeout=6000)
                    ok = True
                    time.sleep(random.uniform(*self._delay()))
                except Exception as exc:  # noqa: BLE001
                    self.log.debug(f"    选项点击失败: {str(exc)[:60]}")
            return ok

        # 填空 / 作文
        if q.blank_idx is not None:
            sel = f'[data-upc-blank="{q.blank_idx}"]'
            try:
                el = scope.query_selector(sel)
            except Exception:  # noqa: BLE001
                el = None
            if el is None:
                return False
            try:
                text = str(answer).replace("|", " ").strip()
                el.fill(text, timeout=6000)
                return True
            except Exception as exc:  # noqa: BLE001
                self.log.debug(f"    填空失败: {str(exc)[:60]}")
                return False
        return False

    def _delay(self):
        pair = self.cfg.quiz.answer_delay_ms
        try:
            lo, hi = float(pair[0]) / 1000, float(pair[1]) / 1000
        except Exception:  # noqa: BLE001
            lo, hi = 0.4, 1.2
        return lo, max(lo, hi)

    # ------------------------------------------------------------------
    def find_submit(self, page):
        for scope in self._scopes(page):
            el = S.first_in(scope, S.SUBMIT_BUTTON, timeout_s=0)
            if el is not None:
                return scope, el
        return None, None

    def click_submit(self, page):
        """点击提交并处理确认框。返回是否点到。"""
        scope, el = self.find_submit(page)
        if el is None:
            self.log.warning("    未找到提交按钮")
            return False
        try:
            if self.humanizer:
                self.humanizer.click(page, el, timeout=8000)
            else:
                el.click(timeout=8000)
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"    点击提交失败: {str(exc)[:80]}")
            return False
        page.wait_for_timeout(1200)
        if self.cfg.quiz.submit_confirm and self.popup:
            self.popup.sweep(page, rounds=2)
        # 通用确认框
        for sc in self._scopes(page):
            for txt in ("确定提交", "确认提交", "确定", "确认", "交卷"):
                try:
                    btn = sc.query_selector(f'button:has-text("{txt}")')
                    if btn is not None and btn.is_visible():
                        btn.click(timeout=3000)
                        page.wait_for_timeout(800)
                        break
                except Exception:  # noqa: BLE001
                    continue
        return True

    def collect_feedback(self, page):
        """从 DOM 收集"正确答案：X"文本。"""
        texts = []
        for scope in self._scopes(page):
            try:
                got = scope.evaluate(JS_COLLECT_FEEDBACK) or []
                texts.extend(got)
            except Exception:  # noqa: BLE001
                continue
        answers = []
        for t in texts:
            m = re.search(r"(?:正确答案|参考答案|标准答案)\s*[:：]\s*(.+)", t)
            if m:
                ans = m.group(1).strip()
                if ans and len(ans) < 200:
                    answers.append(ans)
        return answers

    # ------------------------------------------------------------------
    def run(self, page, label=""):
        """在当前页面完成一轮答题。返回 {'questions','answered','submitted'}。"""
        cfg = self.cfg.quiz
        if not cfg.enabled:
            return {"questions": 0, "answered": 0, "submitted": False, "skipped": True}

        self.sniffer.attach(page)
        self.sniffer.clear()

        questions = self.scan(page)
        if not questions:
            self.log.info(f"  [{label}] 本页没识别到题目")
            return {"questions": 0, "answered": 0, "submitted": False}

        cap = int(cfg.max_questions or 0)
        if cap:
            questions = questions[:cap]
        self.log.info(f"  [{label}] 识别到 {len(questions)} 道题")

        # ---- 第一遍：作答 ----
        answered = 0
        pending = []
        for q in questions:
            ans, src, conf = self.answer_question(q, allow_sniff=False)
            if ans:
                if self.fill(page, q, ans):
                    answered += 1
                    q.answer, q.source, q.confidence = ans, src, conf
                    self.log.info(f"    Q{q.qi} ← {ans}（{src}）")
            else:
                pending.append(q)

        # ---- 没答案的题：先随便填，别留空 ----
        if pending:
            self.log.info(f"    {len(pending)} 道题没有可靠答案，先按兜底策略填满（不留空）")
            for q in pending:
                guess = self._fallback_answer(q)
                if guess and self.fill(page, q, guess):
                    q.answer, q.source, q.confidence = guess, "fallback", 0.2

        # ---- 提交 ----
        submitted = False
        if questions:
            submitted = self.click_submit(page)
            if submitted:
                self.log.info("    已提交，等待判题结果…")
                page.wait_for_timeout(2500)
                if self.popup:
                    self.popup.sweep(page, rounds=2)

        # ---- 收集正确答案 ----
        got = self.sniffer.wait_and_extract(settle_s=2.0)
        dom_answers = self.collect_feedback(page)
        self.log.info(f"    判题反馈：嗅探={'有' if got else '无'}，"
                      f"页面答案 {len(dom_answers)} 条")

        learned = 0
        if got and got.get("answer"):
            self.log.info(f"    嗅探到答案: {got['answer']}（{got.get('from')}）")
        # ★ 2026-11-23 防污染：停用多题页的 DOM 顺序对齐回填。
        #   旧实现把 dom_answers[i] 按出现顺序硬对到 questions[i]——只要有一道
        #   题没有反馈行、或页面多出一行"答案"文本，其后所有题就会整体错位入库；
        #   且 AnswerStore 无分级校验，cache 命中即以 0.95 置信直接使用，
        #   错答案等于永久污染题库。只保留单题页回填（唯一题目与
        #   dom_answers[0] 天然对应，无错位风险）；多题页的可靠答案获取
        #   走 AI 版链路 tools/study_ai.py 的解密判题（parse_answer_blocks 按题分组）。
        if dom_answers and len(questions) == 1:
            self.store.put(questions[0].key, dom_answers[0], "feedback", questions[0].stem)
            learned += 1
        elif dom_answers and len(questions) > 1:
            self.log.info(f"    多题页 DOM 反馈 {len(dom_answers)} 条与 {len(questions)} "
                          f"道题无法可靠对应，跳过回填（防错位污染题库）")
        if got and len(questions) == 1:
            self.store.put(questions[0].key, got["answer"], "sniff", questions[0].stem)
            learned += 1

        self.store.save()
        self.solved += answered
        return {"questions": len(questions), "answered": answered,
                "submitted": submitted, "learned": learned,
                "feedback": dom_answers, "sniffed": got}

    # 主观题兜底句池（2026-11-23）：旧版所有 essay 一律填同一句
    # "I think this is an important topic in our daily life."，全平台文本雷同，
    # 是典型机器作答特征（反作弊侧一个 GROUP BY 就能圈出来），改随机句池 + 随机尾句。
    _FILL_POOL = ("the", "a", "to", "in", "on", "it")
    _ESSAY_POOL = (
        "In my opinion, this is quite an interesting topic worth thinking about.",
        "I believe different people may hold different views on this question.",
        "From my perspective, it reminds me of some similar experiences in daily life.",
        "As far as I am concerned, we should think carefully before drawing a conclusion.",
        "This topic is closely related to what we have discussed in this unit.",
        "To be honest, I have never thought about this question before, but it sounds meaningful.",
    )
    _ESSAY_TAILS = (
        " Thanks for reading.",
        " That is all I want to say.",
        " I would be glad to hear other opinions.",
    )

    def _fallback_answer(self, q):
        """兜底：绝不留空（留空会被平台拦截"还有 X 题未作答"）。"""
        if q.qtype in ("single",):
            return random.choice("ABCD"[: max(1, len(q.options))] or "A") if q.options else "A"
        if q.qtype == "multi":
            n = max(1, len(q.options))
            k = 1 if n <= 2 else 2
            picks = random.sample([chr(ord("A") + i) for i in range(n)], min(k, n))
            return ",".join(sorted(picks))
        if q.qtype == "fill":
            return random.choice(self._FILL_POOL)
        if q.qtype == "essay":
            s = random.choice(self._ESSAY_POOL)
            if random.random() < 0.35:
                s += random.choice(self._ESSAY_TAILS)
            return s
        return "A"
