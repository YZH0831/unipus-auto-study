# -*- coding: utf-8 -*-
"""响应嗅探：从"试答提交"后返回的数据里提取正确答案。

原理
----
U校园的单元测试在提交后会返回判题结果（对/错 + 正确答案）。所以不需要任何题库：
    1. 先随便填一个答案 → 提交
    2. 监听所有 ajax/api 响应，从 JSON / HTML 里捞"正确答案"
    3. 拿正确答案重新作答（two_pass）
    4. 沉淀到本地题库，下次同类题直接命中

风险与对策
----------
- **会消耗一次作答机会**：所以默认 two_pass=true（第二遍按正确答案重做），
  且 strategy 里给了 cache/llm 这些"不试答"的路径。
- 部分接口走加密 / WebSocket：嗅探不到就返回 None，交给上层回退，不硬撑。
"""
import json
import re
import time

# 只关心这些 URL 特征
URL_KEYWORDS = ("api", "ajax", "submit", "check", "answer", "question",
                "exercise", "quiz", "test", "judge", "score", "result",
                "unit", "task", "paper")

# 答案键分级（小写、去 _/- 后匹配）：
#   强键 = 语义上明确就是"正确答案"的字段，优先采信；
#   弱键 = 语义含糊——answer/answers 可能是"用户自己提交的答案"（提交响应里
#          常与正确答案同字段名共存），key/correct/right 也常见于无关字段
#          （分页 key、判对布尔）。旧实现强弱混排、按 dict 插入序命中，
#          拿到哪算哪，实测会把本次提交的错误答案当"标准答案"捞回来；
#          现在强键全层未命中才轮到弱键。
STRONG_ANSWER_KEYS = (
    "correctanswer", "rightanswer", "standardanswer",
    "correctoption", "correctoptions", "rightoption", "rightoptionlist",
    "correctkey", "solution",
)
WEAK_ANSWER_KEYS = (
    "answer", "answers", "key", "correct", "right",
)
ANSWER_KEYS = STRONG_ANSWER_KEYS + WEAK_ANSWER_KEYS   # 兼容旧引用

# 明确不是答案的键（避免把 answerStatus / answerTime 之类当答案）
NEGATIVE_KEYS = ("answerstatus", "answertime", "answerid", "answercount",
                 "answerurl", "correctcount", "correctrate", "correctnum")

HTML_PATTERNS = [
    re.compile(r"正确答案[：:]\s*([A-H](?:\s*[,，、]\s*[A-H])*)"),
    re.compile(r"参考答案[：:]\s*([A-H](?:\s*[,，、]\s*[A-H])*)"),
    re.compile(r"正确答案[：:]\s*([^\n\r<]{1,120})"),
    re.compile(r'"correctAnswer"\s*:\s*"([^"]{1,120})"'),
    re.compile(r'"rightAnswer"\s*:\s*"([^"]{1,120})"'),
    re.compile(r'"answer"\s*:\s*"([A-H][,，、\s]*[A-H,\s，、]*)"'),
    re.compile(r'"correct"\s*:\s*"([^"]{1,120})"'),
]

MAX_HTML_LEN = 300_000       # 超大 HTML 直接丢（列表页、框架页）
MAX_KEEP = 60                # 只留最近 N 条响应，避免内存膨胀


class ResponseSniffer:
    """挂在 page 上的响应收集器。"""

    def __init__(self, log, timeout_s=20):
        self.log = log
        self.timeout_s = timeout_s
        self.records = []
        self._enabled = False

    # ------------------------------------------------------------------
    def attach(self, page):
        """开始监听（幂等）。"""
        if self._enabled:
            return
        self._enabled = True
        page.on("response", self._on_response)
        self.log.debug("响应嗅探已挂载")

    def detach(self, page):
        try:
            page.remove_listener("response", self._on_response)
        except Exception:  # noqa: BLE001
            pass
        self._enabled = False

    def clear(self):
        self.records.clear()

    # ------------------------------------------------------------------
    def _on_response(self, response):
        try:
            url = response.url or ""
            if not any(k in url.lower() for k in URL_KEYWORDS):
                return
            ctype = (response.headers or {}).get("content-type", "") or ""
            if "json" in ctype:
                try:
                    data = response.json()
                except Exception:  # noqa: BLE001
                    return
                self.records.append({"url": url, "kind": "json", "data": data,
                                     "ts": time.time()})
            elif "html" in ctype or "text" in ctype:
                try:
                    text = response.text()
                except Exception:  # noqa: BLE001
                    return
                if not text or len(text) > MAX_HTML_LEN:
                    return
                self.records.append({"url": url, "kind": "text", "data": text,
                                     "ts": time.time()})
            if len(self.records) > MAX_KEEP:
                del self.records[: len(self.records) - MAX_KEEP]
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def wait_and_extract(self, settle_s=3.0, expect_min=1):
        """等一会儿再提取（给响应到达留时间）。"""
        time.sleep(settle_s)
        return self.extract_all()

    def extract_all(self):
        """返回 {答案字符串, 来源} 或 None。按置信度从高到低尝试。"""
        # ① JSON：结构化答案最可信
        for rec in reversed(self.records):
            if rec["kind"] != "json":
                continue
            ans = _find_in_json(rec["data"], depth=0)
            if ans:
                return {"answer": ans, "from": f"json:{_short_url(rec['url'])}",
                        "confidence": 0.9}
        # ② 文本：正则
        for rec in reversed(self.records):
            if rec["kind"] != "text":
                continue
            ans = _find_in_text(rec["data"])
            if ans:
                return {"answer": ans, "from": f"html:{_short_url(rec['url'])}",
                        "confidence": 0.6}
        return None

    def dump(self, path):
        """把抓到的响应落盘，便于排查接口结构。"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [{"url": r["url"], "kind": r["kind"],
                      "data": r["data"] if r["kind"] == "json"
                              else str(r["data"])[:4000]}
                     for r in self.records],
                    f, ensure_ascii=False, indent=2,
                )
            self.log.info(f"嗅探到的响应已导出: {path}")
        except Exception as exc:  # noqa: BLE001
            self.log.debug(f"导出响应失败: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
def _short_url(url):
    try:
        return url.split("?")[0][-60:]
    except Exception:  # noqa: BLE001
        return str(url)[:60]


def _normalize_answer(value):
    """把各种形态的答案统一成字符串 / 列表。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        v = value.strip()
        return v or None
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, (str, int)):
                s = str(item).strip()
                if s:
                    parts.append(s)
            elif isinstance(item, dict):
                for k in ("option", "key", "value", "answer", "text", "content"):
                    if k in item:
                        s = str(item[k]).strip()
                        if s:
                            parts.append(s)
                            break
        return ",".join(parts) if parts else None
    if isinstance(value, dict):
        # 先看常见的"单个选项"形态，再退回递归查找
        for k in ("option", "key", "value", "answer", "text", "content", "letter"):
            if k in value:
                got = _normalize_answer(value[k])
                if got:
                    return got
        return _find_in_json(value, depth=0)
    return None


def _looks_like_answer(s):
    if not s:
        return False
    if len(s) > 300:
        return False
    # 纯字母选项 / 含中文的填空答案 / 判断
    if re.fullmatch(r"[A-Ha-h](?:\s*[,，、]\s*[A-Ha-h])*", s.strip()):
        return True
    if s.strip() in ("对", "错", "正确", "错误", "true", "false", "T", "F", "√", "×"):
        return True
    if 1 <= len(s.strip()) <= 200:
        return True
    return False


def _norm_key(raw_key):
    """键名归一：小写、去 _/-（与键表统一形态后比对）。"""
    return str(raw_key).lower().replace("_", "").replace("-", "")


def _find_in_json(data, depth=0, max_depth=4):
    """递归找答案。强键 → 弱键 → 关键词包含 → 递归，逐级降权。"""
    if depth > max_depth:
        return None

    if isinstance(data, dict):
        # ① 强键（语义明确就是"正确答案"）
        for raw_key, value in data.items():
            if _norm_key(raw_key) in STRONG_ANSWER_KEYS:
                ans = _normalize_answer(value)
                if ans and _looks_like_answer(ans):
                    return ans
        # ② 弱键：answer/key 可能装的是用户本次提交的答案，
        #    同层强键都没命中才考虑，且继续排除 answerStatus 之类状态键
        for raw_key, value in data.items():
            key = _norm_key(raw_key)
            if key in NEGATIVE_KEYS or key in STRONG_ANSWER_KEYS:
                continue
            if key in WEAK_ANSWER_KEYS:
                ans = _normalize_answer(value)
                if ans and _looks_like_answer(ans):
                    return ans
        # ③ 关键词包含
        for raw_key, value in data.items():
            key = _norm_key(raw_key)
            if key in NEGATIVE_KEYS:
                continue
            if any(k in key for k in ("correct", "rightanswer", "standard")):
                ans = _normalize_answer(value)
                if ans and _looks_like_answer(ans):
                    return ans
        # ④ 递归
        for value in data.values():
            got = _find_in_json(value, depth + 1, max_depth)
            if got:
                return got

    elif isinstance(data, list):
        for item in data:
            got = _find_in_json(item, depth + 1, max_depth)
            if got:
                return got

    return None


def _find_in_text(text):
    if not text:
        return None
    for pat in HTML_PATTERNS:
        m = pat.search(text)
        if m:
            ans = (m.group(1) or "").strip()
            if ans and _looks_like_answer(ans):
                return ans
    return None
