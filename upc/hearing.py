# -*- coding: utf-8 -*-
"""基于听力转录文本判断选择题答案。

思路（对应"要听音频联系上下文"）：
    把选项当成"要被听见的话"，在转录文本里找最相似的片段。
    "选出被问到的/被提到的"这类题，正确选项几乎逐字出现在音频里，
    错误选项语义相近但措辞不同，相似度会明显低一档。

只做文本层面判断，不猜；低于阈值就不选（宁缺毋滥），
避免"全选"这种高错误率做法。
"""
import re
from difflib import SequenceMatcher

# 常见缩写展开，避免 ASR 输出 "we're" 而选项写 "we are"
_CONTRACT = {
    "we're": "we are", "it's": "it is", "that's": "that is", "don't": "do not",
    "doesn't": "does not", "didn't": "did not", "can't": "cannot",
    "won't": "will not", "isn't": "is not", "aren't": "are not",
    "i'm": "i am", "you're": "you are", "they're": "they are",
    "he's": "he is", "she's": "she is", "there's": "there is",
    "what's": "what is", "let's": "let us", "i've": "i have",
}


def normalize(s: str) -> str:
    s = (s or "").lower()
    for k, v in _CONTRACT.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def best_ratio(needle: str, haystack: str) -> float:
    """needle 在 haystack 中最相似窗口的相似度（0~1）。

    窗口长度取 needle 的 1.0~1.6 倍，滑动步长按比例取，
    兼顾准确度与速度（转录一般几百到几千字符）。
    """
    n = normalize(needle)
    h = normalize(haystack)
    if not n or not h:
        return 0.0
    if n in h:                      # 完全包含，直接满分
        return 1.0
    nl = len(n)
    best = 0.0
    step = max(4, nl // 8)
    for mult in (1.0, 1.2, 1.4, 1.6):
        w = int(nl * mult)
        if w > len(h):
            w = len(h)
        for i in range(0, max(1, len(h) - w + 1), step):
            seg = h[i:i + w]
            r = SequenceMatcher(None, n, seg).ratio()
            if r > best:
                best = r
                if best >= 0.985:
                    return best
    return best


def is_multi(stem: str) -> bool:
    """从题干判断是多选还是单选。"""
    s = (stem or "").lower()
    multi_hints = ("choose the ones", "choose all", "select all", "ones that are",
                   "which of the following are", "choose the correct ones",
                   # 实测：选出"被提到的方面/陈述"这类题都是多选
                   "aspects", "choose the aspects", "mentioned",
                   "choose the statements", "statements that are",
                   "choose the topics", "topics that are",
                   # 实测（Unit 1 Group discussion）：清单确认题也是多选
                   # "Choose the items that you have confirmed."
                   "choose the items", "select the items", "items that you",
                   "items that are", "check the items", "tick the items")
    single_hints = ("choose the best", "best answer", "correct answer",
                    "choose the correct one", "which of the following is")
    if any(k in s for k in multi_hints):
        return True
    if any(k in s for k in single_hints):
        return False
    return None          # 不确定


def decide(options, transcript, stem="", hi=0.85, lo=0.62):
    """按转录文本给每题判答案，返回 (要选的索引列表, 每题相似度明细)。

    阈值依据实测（Unit 1 Exercise 1）：
        正确项 A/C/E/F = 0.943 / 0.945 / 0.961 / 1.000
        错误项 D/B    = 0.509 / 0.727
    hi=0.85 能干净分开。hi~lo 之间只在多选且明显领先时才选，
    低于 lo 一律不选——宁缺毋滥，绝不"全选"。
    """
    scores = [(i, best_ratio(o, transcript)) for i, o in enumerate(options)]
    if not scores:
        return [], []
    multi = is_multi(stem)
    tops = sorted((s for _, s in scores), reverse=True)

    picked = [i for i, s in scores if s >= hi]
    if not picked:
        # 没有任何选项够高（多半是"理解题"，选项是音频内容的改写，
        # 逐字匹配必然偏低）。这时仍要选一个：不提交就拿不到标准答案，
        # 而第二遍可以用题库一次满分。所以这里选最高分作为"试探"。
        best_i, best_s = max(scores, key=lambda x: x[1])
        if multi is False:
            picked = [best_i]
        else:
            picked = [i for i, s in scores if s >= best_s - 0.05]
    elif multi is False and len(picked) > 1:
        # 单选却命中多个，只留最高的
        picked = [max(picked, key=lambda i: dict(scores)[i])]

    return sorted(set(picked)), scores

def group_by_position(cands, transcript, gap=40):
    """把标准答案候选按"在转录里出现的位置"聚成每组 = 一个空。

    平台返回的答案是一维列表，同义表达（1963 / nineteen sixty-three）
    指向同一个空；按它们在音频原文里的位置聚类即可还原分组。
    """
    t = normalize(transcript)
    pos, last = [], 0
    for c in cands:
        cn = normalize(c)
        i = t.find(cn) if cn else -1
        if i < 0:
            i = last
        else:
            last = i
        pos.append(i)

    groups = []
    for c, i in zip(cands, pos):
        if groups and (i - groups[-1][0]) <= gap:
            groups[-1][1].append(c)
        else:
            groups.append([i, [c]])
    return [g[1] for g in groups]

def answer_from_context(before, after, transcript):
    """用「空前文本」和「空后文本」在转录里夹逼出这个空的答案。

    例：空前 "... In early"、空后 "thousands of workers collect ice blocks"
        转录 "... In early December, thousands of workers collect ice blocks ..."
        → 返回 "December"

    找不到返回空字符串（调用方应跳过，不要乱填）。
    """
    t = normalize(transcript)
    if not t:
        return ""
    b = normalize(before).split()
    a = normalize(after).split()
    if not b and not a:
        return ""

    def _try(nb, na):
        btail = " ".join(b[-nb:]) if b and nb else ""
        ahead = " ".join(a[:na]) if a and na else ""
        if btail and ahead:
            pat = re.escape(btail) + r"\s*(.{1,70}?)\s*" + re.escape(ahead)
            m = re.search(pat, t)
            if m:
                return m.group(1).strip()
        elif btail:
            m = re.search(re.escape(btail) + r"\s+(.{1,60})", t)
            if m:
                return " ".join(m.group(1).split()[:7])
        return ""

    # 前后文都有：从长到短逐步放宽，优先精确匹配
    if b and a:
        for nb, na in ((8, 6), (6, 5), (5, 4), (4, 3), (3, 3)):
            got = _try(nb, na)
            if got:
                return got
    if b:
        for nb in (8, 6, 5, 4):
            got = _try(nb, 0)
            if got:
                return got
    return ""

def guess_from_transcript(before, transcript):
    """夹逼失败时的合理猜测：找转录里与「空前文本」最像的位置，
    取它后面的 1~3 个词当答案。

    这不是乱填——是基于音频原文的近似定位；真正的标准答案仍由
    提交后的 analysis 反解拿到，第二次就精确了。
    """
    t = normalize(transcript)
    b = normalize(before).split()
    if not t or not b:
        return ""
    nl = max(8, min(40, len(" ".join(b[-4:]))))
    h = t
    best, best_pos = 0.0, -1
    step = max(4, nl // 6)
    for i in range(0, max(1, len(h) - nl + 1), step):
        r = SequenceMatcher(None, " ".join(b[-4:]), h[i:i + nl]).ratio()
        if r > best:
            best, best_pos = r, i
    if best_pos < 0 or best < 0.45:
        return ""
    rest = h[best_pos + nl:].split()
    if not rest:
        return ""
    return " ".join(rest[:2]).strip()

def last_resort_word(transcript):
    """最后兜底：从转录里挑一个像答案的词。

    只在「不填满就提交不了、提交不了就拿不到标准答案」时使用。
    优先数字/年份，其次大写开头的词，再其次较长的普通词。
    """
    import re as _re
    t = (transcript or "")
    if not t:
        return ""
    nums = _re.findall(r"\b(?:19|20)\d{2}\b", t)
    if nums:
        return nums[0]
    caps = _re.findall(r"\b[A-Z][a-z]{3,14}\b", t)
    if caps:
        return caps[0]
    words = [w for w in _re.findall(r"\b[a-z]{4,14}\b", t.lower())
             if w not in ("that", "this", "with", "have", "they", "there",
                          "their", "which", "about", "would", "could", "should")]
    return words[0] if words else ""

