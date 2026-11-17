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
    "必须先读完这些材料、根据题意判断出正确答案，再给结论；"
    "材料里有依据时按材料答，不要凭印象瞎猜，也不要照抄本提示词里的格式示例。\n"
    "只输出最后一行结论，格式如下（B 只是示例，不是要你抄的内容）：\n"
    "  单选：ANSWER: B\n"
    "  多选：ANSWER: A,C\n"
    "  填空/翻译：ANSWER: 要填的内容（多个空用 | 分隔）\n"
    "严禁输出空答案、单独一个 ANSWER 单词、<答案> 之类的占位符，"
    "也不要复述题目或写解题过程。\n"
    "确实不确定时，也必须基于材料给出你的最佳判断，不能只回格式标签。"
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


class LLMClient:
    def __init__(self, cfg, log):
        self.cfg = cfg.quiz.llm
        self.log = log
        self.calls = 0

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
        return bool(self.cfg.enabled and self._key)

    def ask(self, stem, options=None, qtype="choice", transcript="",
            material=""):
        """返回 {'answer': str, 'raw': str} 或 None。

        transcript：听力题的音频转录，必须一起给模型，
        否则模型只能瞎猜（之前的 prompt 漏了它，等于让模型蒙答案）。
        """
        if not self.available:
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
                {"role": "system", "content": SYSTEM_PROMPT},
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
        answer = self._parse(content)
        if not answer:
            self.log.warning(f"没能从模型输出里解析出答案: {content[:120]}")
            return None
        return {"answer": answer, "raw": content}

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
