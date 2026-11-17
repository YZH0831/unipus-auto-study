# -*- coding: utf-8 -*-
"""U校园 AI 版内容/解析的加解密（复刻前端 K()）。

实测（2026-11-06）：
    content 接口的 content 字段     → 密文，解密后是题目 JSON
    submit 接口返回的 analysis 字段 → 密文，解密后含标准答案

前端原文：
    const K = (e, t) => {
      if (!e.startsWith("unipus.")) return e;
      const s = e.slice(7);
      const n = CryptoJS.enc.Utf8.parse("1a2b3c4d" + t);
      const r = CryptoJS.enc.Hex.parse(s);
      return CryptoJS.AES.decrypt(CryptoJS.enc.Base64.stringify(r), n,
               {mode: ECB, padding: ZeroPadding}).toString(Utf8);
    }

key = "1a2b3c4d" + t，t 是接口返回的 k（content 用 k，analysis 用 dayKey），
长度补齐到 16 字节即 AES-128-ECB。
"""
import json

_PREFIX = "unipus."


def decrypt(text, k=""):
    """解密 unipus.<hex>；失败返回 None。"""
    if not text or not isinstance(text, str):
        return None
    if not text.startswith(_PREFIX):
        return text                      # 明文直接返回
    key = ("1a2b3c4d" + (k or "")).encode("utf-8")
    if len(key) not in (16, 24, 32):
        return None
    try:
        raw = bytes.fromhex(text[len(_PREFIX):])
    except ValueError:
        return None
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return None
    try:
        plain = AES.new(key, AES.MODE_ECB).decrypt(raw).rstrip(b"\x00")
        return plain.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def parse_answers(analysis_cipher, day_key):
    """从 submit 返回的 analysis 解出标准答案。

    返回 {quesId: {"answers": [...]}}；解析不出返回 {}。
    实测明文形如：
        [{"id":..., "answer":"{\\"children\\":[{\\"answers\\":[\\"A\\",\\"C\\"]}]}",
          "quesId":"1314..."}]
    """
    plain = decrypt(analysis_cipher, day_key)
    if not plain:
        return {}
    try:
        items = json.loads(plain)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for it in items if isinstance(items, list) else []:
        qid = str(it.get("quesId") or it.get("id") or "")
        raw = it.get("answer")
        ans = []
        if isinstance(raw, str) and raw.strip():
            try:
                obj = json.loads(raw)
            except Exception:  # noqa: BLE001
                obj = None
            if isinstance(obj, dict):
                for child in (obj.get("children") or []):
                    a = child.get("answers")
                    if isinstance(a, list):
                        ans.extend(str(x) for x in a)
                    elif isinstance(a, str) and a:
                        ans.append(a)
            elif isinstance(obj, list):
                ans = [str(x) for x in obj]
        if not ans and isinstance(it.get("answers"), list):
            ans = [str(x) for x in it["answers"]]
        if qid or ans:
            out[qid or f"idx{len(out)}"] = {"answers": ans}
    return out


def first_answer_list(answers_map):
    """取第一个含答案的列表（单题页面够用）。"""
    for v in (answers_map or {}).values():
        if v.get("answers"):
            return v["answers"]
    return []

def parse_sequence_order(analysis_cipher, day_key):
    """解析排序/人物匹配题的标准答案。

    明文形如：
      {"children":[{"answers":["A"]},{"answers":["C"]}, ...]}
    → 返回 ["A", "C", "D", "B", "E"]（位置 i 应该放的选项字母）
    """
    plain = decrypt(analysis_cipher, day_key)
    if not plain:
        return []
    import json as _json
    try:
        items = _json.loads(plain)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        raw = it.get("answer")
        try:
            obj = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001
            obj = None
        for child in (obj or {}).get("children") or [] if isinstance(obj, dict) else []:
            a = (child or {}).get("answers") or []
            if a:
                out.append(str(a[0]).strip().upper()[:1])
        if out:
            break
    return out

