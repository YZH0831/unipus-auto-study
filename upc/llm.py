# -*- coding: utf-8 -*-
"""大模型答题客户端（OpenAI 兼容接口）。

只依赖标准库 urllib，不额外装 requests/openai，减少安装摩擦。
兼容 DeepSeek / Kimi / 通义 / 智谱 / Ollama / 任意 OpenAI 兼容网关。
"""
import json
import re
import time
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "你是一个英语课程答题助手。用户会给你题干、选项，"
    "可能还有网页上的阅读材料、听力音频转录。\n"
    "在内部完成分析与核对，不要输出解题过程：\n"
    "1. 读题定标：先读全题干、选项与全部材料，标出关键限定词（时间/主体/范围/否定词）"
    "与隐含限制，判断题型和考查目标；材料里有依据时按材料答，不要凭印象瞎猜，"
    "也不要照抄本提示词里的格式示例。\n"
    "2. 多解交叉验证：不局限于单一解法——先独立判断，再逐项核对每个选项对在哪、错在哪；"
    "多个选项看似都对时，选与材料最贴合、最准确的那个。\n"
    "3. 按题型作答：严格遵循用户消息给出的题型；单选一个字母，多选给全部正确字母，"
    "填空/翻译按空给内容，听力题以转录为依据、阅读题以材料为依据。\n"
    "4. 交付前核对：逐步核对关键步骤与答案格式——字母是否在选项范围内、"
    "多选分隔符是否为英文逗号、填空是否漏空或多带内容，杜绝臆测与漏答。\n"
    "5. 不确定就核查：信息不足或确实拿不准时，不要硬猜、不要只回格式标签；"
    "回到题干与材料反复核对、换角度再判断一次，给出最有把握的结论，"
    "把准确率放在第一位。\n"
    "只输出两行：第一行是结论，第二行是置信度；格式如下（B 只是示例，不要照抄内容）：\n"
    "  单选：ANSWER: B\n"
    "  多选：ANSWER: A,C\n"
    "  填空/翻译：ANSWER: 要填的内容（多个空用 | 分隔）\n"
    "  第二行固定为：CONF: 0.85   （0~1 的小数，表示你对答案的把握；越没把握给越低）\n"
    "严禁输出空答案、单独一个 ANSWER 单词、<答案> 之类的占位符，"
    "也不要复述题目或写解题过程。"
)

# 二次复核专用：换个"独立复核员"视角重推，避免沿用第一次的思维定势。
RECHECK_SYSTEM_PROMPT = (
    "你是一道英语题的独立复核员，只会看到题干、选项与材料。"
    "请完全独立地重新作答，不要假设之前任何结论是对的。\n"
    "方法：逐字读题干抓限定词与否定词，回材料定位依据，逐项排除后给结论；"
    "听力题以转录为准、阅读题以原文为准。信息不足时如实给低置信度。\n"
    "只输出两行：第一行 ANSWER: 结论；第二行 CONF: 0到1的小数（把握程度）。"
    "不要输出解题过程或 Markdown。"
)

# 模型偷懒照抄格式模板时会吐出这些"空壳答案"，必须挡掉，
# 否则上层会把 ANSWER 的首字母 A 当成选项，出现"每题都选 A"。
_PLACEHOLDERS = {
    "ANSWER", "ANSWER:", "ANSWER：", "<答案>", "<答案1>,<答案2>",
    "答案", "无", "N/A", "NA", "NONE", "UNKNOWN", "?", "？", "-", "--",
}


def _is_placeholder(s):
    t = (s or "").strip().strip("。.；;\"' <>：:")
    if not t:
        return True
    if t.upper() in _PLACEHOLDERS:
        return True
    return bool(re.fullmatch(r"<\s*(答案|answer)[^>]*>", t, re.IGNORECASE))


def _explain_http(code, detail=""):
    """把常见 HTTP 错误翻译成可执行的建议（与学习通项目同名实现对齐）。"""
    d = (detail or "").lower()
    if code == 401:
        return "Key 无效或未生效（检查 api_key 是否填对、是否多了空格）"
    if code == 402 or "insufficient balance" in d:
        return "账户余额不足 → 到平台充值后重跑"
    if code == 403:
        return "无权限（Key 与模型不匹配，或账号被限制）"
    if code == 429:
        return "触发限流 → 稍后再试，或调小 max_calls"
    if code >= 500:
        return "服务端异常 → 稍后重试"
    return f"HTTP {code}"


def _parse_conf(content):
    """从模型输出里解析 CONF: 0~1；缺失/越界回退 0.7（兼容旧格式）。"""
    m = re.search(r"CONF\s*[:：]\s*(0?(?:\.\d+)?|1(?:\.0+)?)", str(content or ""),
                  re.IGNORECASE)
    if m:
        try:
            v = float(m.group(1))
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            pass
    return 0.7


class LLMClient:
    # 整体就是"字母列表"的格式（"B" / "A,C" / "ABD"），_same_answer 用
    _LETTERS_FMT = re.compile(r"^[A-Ha-h](?:[\s,，、;；/]*[A-Ha-h])*$")

    def __init__(self, cfg, log):
        self.cfg = cfg.quiz.llm
        self.log = log
        self.calls = 0
        self.fatal = ""      # 401/402/403 等"再试也没用"的错误 → 本轮熔断

    @property
    def _key(self):
        """优先用配置里的 key；留空则从 DPAPI 加密存储读（硬盘无明文）。"""
        if getattr(self, "_key_cache", None):
            return self._key_cache
        k = str(getattr(self.cfg, "api_key", "") or "")
        if not k:
            try:
                from upc.secret import load_secret
                k = load_secret("llm_key") or ""
            except Exception:  # noqa: BLE001
                k = ""
        self._key_cache = k
        return k

    @property
    def available(self):
        if self.fatal:
            return False
        return bool(self.cfg.enabled and self._key)

    def ask(self, stem, options=None, qtype="choice", transcript="",
            material="", free_text=False, system=None):
        """返回 {'answer': str, 'raw': str, 'confidence': float} 或 None。

        system：传入 RECHECK_SYSTEM_PROMPT 即为二次复核调用；默认用主提示词。
        confidence：从模型输出的 CONF: 行解析（0~1）；模型未给时回退 0.7，
                    保持对旧模型/旧缓存的兼容。
        transcript：听力题的音频转录，必须一起给模型，
                    否则模型只能瞎猜（之前的 prompt 漏了它，等于让模型蒙答案）。
        free_text：口语稿/开放式简答用 True——直接返回模型原文，
                   不走 _parse 的「抽 A-H 选项字母」逻辑。否则一段英文稿里的
                   独立冠词 "a" 会被抽成答案 'A'，稿子被判过短弃用（口语 0 分源头）。
        """
        if not self.available:
            if self.fatal:
                self.log.debug(f"大模型已熔断，本轮跳过: {self.fatal}")
            return None
        if int(self.cfg.max_calls) and self.calls >= int(self.cfg.max_calls):
            self.log.warning("大模型调用次数已达上限，跳过")
            return None
        # 题干、选项、材料、转录全空 = 没把题目交给模型，
        # 此时调用只会让模型照抄格式模板（实测会吐 "ANSWER"），直接拒绝。
        if not any((str(stem or "").strip(), options,
                    str(transcript or "").strip(), str(material or "").strip())):
            self.log.warning("题目/选项/材料全为空，拒绝让模型凭空作答")
            return None

        prompt = self._build_prompt(stem, options, qtype, transcript, material)
        self.log.debug(f"大模型 prompt: {prompt[:300]!r}")
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system or SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(self.cfg.temperature),
            "stream": False,
        }
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.timeout_s)) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            self.log.warning(f"大模型接口 HTTP {exc.code}: {detail}")
            # ★ 熔断：Key 无效 / 余额不足 / 无权限时，后续每道题都会同样
            #   失败，白撞到 max_calls 才停纯属浪费——直接封死本轮调用。
            if exc.code in (401, 402, 403):
                self.fatal = _explain_http(exc.code, detail)
                self.log.warning(f"已熔断：本轮剩余题目不再调用大模型（{self.fatal}）")
            return None
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"大模型接口调用失败: {str(exc)[:120]}")
            return None

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            self.log.warning(f"大模型返回结构异常: {str(data)[:150]}")
            return None

        # 留原文，方便排查"模型只回了个格式标签"这类问题
        self.log.debug(f"大模型原文: {content[:160]!r}")
        if free_text:
            # 口语稿/开放问答：要完整文本。只剥掉模型照抄的格式标签，
            # 绝不能抽选项字母——英文稿里到处是独立的 a，会被抽成 'A'。
            answer = re.sub(r"^\s*ANSWER\s*[:：]\s*", "", content,
                            flags=re.IGNORECASE).strip()
            if _is_placeholder(answer):
                answer = ""
        else:
            answer = self._parse(content)
        if not answer:
            self.log.warning(f"没能从模型输出里解析出答案: {content[:120]}")
            return None
        conf = _parse_conf(content)
        return {"answer": answer, "raw": content, "confidence": conf}

    @staticmethod
    def _same_answer(a, b):
        """二次复核：判断两次答案是否一致（字母集合 / 填空文本）。

        ★ 2026-11-23 修复：旧版对任意两个能抽出 A-H 字母的文本都按
        "字母集合"比较——英文填空 "cat"/"act" 都抽出 {A,C} 会误判一致
        （复核假阳），纯文本比较被整体跳过。现在只有**整体就是字母
        列表格式**（"B" / "A,C" / "ABD"）才走字母集合比较；其余一律
        文本归一比较。
        """
        sa, sb = str(a or "").strip(), str(b or "").strip()
        if not sa or not sb:
            return False
        if (len(sa) <= 24 and len(sb) <= 24
                and LLMClient._LETTERS_FMT.match(sa)
                and LLMClient._LETTERS_FMT.match(sb)):
            la = "".join(sorted(set(re.findall(r"[A-H]", sa.upper()))))
            lb = "".join(sorted(set(re.findall(r"[A-H]", sb.upper()))))
            return la == lb and bool(la)
        norm = lambda s: re.sub(r"[\s，,。.；;|]", "", s).lower()
        return norm(sa) == norm(sb) and bool(norm(sa))

    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(stem, options, qtype, transcript="", material=""):
        lines = [f"题型：{qtype}", f"题干：{stem}"]
        if material:
            # ★ 阅读/讨论题的正文只在网页上，不在音频里。
            #   不给它，模型只能瞎猜（用户吐槽过"不看文章就答题"）。
            lines.append("")
            lines.append("【网页上的材料/文章（答题依据）】")
            lines.append(material[:4000])
        if options:
            lines.append("选项：")
            for i, opt in enumerate(options):
                letter = chr(ord("A") + i)
                lines.append(f"  {letter}. {opt}")
        if transcript:
            lines.append("")
            lines.append("【音频/视频转录（答题依据）】")
            lines.append(transcript[:6000])
        return "\n".join(lines)

    @staticmethod
    def _parse(content):
        if not content:
            return None
        text = content.strip()
        m = re.search(r"ANSWER\s*[:：]\s*(.+)", text, re.IGNORECASE)
        if m:
            raw = m.group(1)
        else:
            # 模型没按格式回时，直接从回答里抽选项字母（A-H）
            cand = re.findall(r"\b([A-H])\b", text.upper())
            raw = ",".join(dict.fromkeys(cand)) if cand else text.splitlines()[0]
        raw = raw.strip().strip("。.；;\"' ")
        # 归一化分隔符
        raw = re.sub(r"\s*[,，、;；]\s*", ",", raw)
        raw = raw.strip(", ")
        # 关键：模板词/占位符不是答案。挡掉它，上层才不会把
        # "ANSWER" 当成一个 A 选项填进去。
        if _is_placeholder(raw):
            return None
        return raw


def probe_model(cfg, log):
    """连通性自检：发一个极短请求。"""
    client = LLMClient(cfg, log)
    if not client.available:
        return False, "未启用或未填 api_key"
    t0 = time.time()
    got = client.ask("1+1=?", ["A. 1", "B. 2"], "choice")
    cost = time.time() - t0
    if got:
        return True, f"可用，耗时 {cost:.1f}s，返回: {got['answer'][:40]}"
    return False, "调用失败（检查 base_url / api_key / model）"
