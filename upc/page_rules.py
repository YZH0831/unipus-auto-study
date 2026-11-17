# -*- coding: utf-8 -*-
"""页面知识与运行策略（外置配置，零配置可用）。

平台改版最容易失效的是「提示语」和「失败阈值」，把它们从业务代码里抽到
data/page_rules.yaml；yaml 缺失 / PyYAML 未装 / 内容损坏都自动回退内置默认值，
保证刷课主流程不会因为一个配置文件起不来。
"""
from __future__ import annotations

import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = ROOT / "data" / "page_rules.yaml"

DEFAULTS = {
    "version": 1,
    "pc_unsupported_marks": [
        "该题型暂不支持pc端", "暂不支持pc端", "不支持pc端", "请在app端查看",
    ],
    "roleplay_stuck_marks": [
        "choosearole", "nowit'syourturntorole-play",
    ],
    "resume": {"enabled": True, "ttl_days": 7, "path": "state/progress.json"},
    "navigation": {"goto_retries": 2, "backoff_ms": 1500,
                   "max_consecutive_failures": 3},
}

_CACHE = None


def _merge(base: dict, over: dict) -> dict:
    """深合并，只接受 base 里已有的键（防止 yaml 写错键污染结构）。"""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(force: bool = False) -> dict:
    """读取规则（带进程内缓存）；force=True 用于测试或运行时改完 yaml 后重载。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    data = copy.deepcopy(DEFAULTS)
    try:
        import yaml  # 项目已有依赖；缺了就静默用默认值
        if RULES_FILE.is_file():
            user = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(user, dict):
                data = _merge(data, user)
    except Exception:  # noqa: BLE001
        pass
    # 归一化：匹配时统一「去空白 + 小写」
    for group in ("pc_unsupported_marks", "roleplay_stuck_marks"):
        marks = data.get(group) or DEFAULTS[group]
        data[group] = [
            "".join(str(m).split()).lower() for m in marks if str(m).strip()
        ] or DEFAULTS[group]
    # 数值兜底，避免 yaml 写成字符串导致比较/乘法出错
    nav = data.setdefault("navigation", {})
    for key in ("goto_retries", "backoff_ms", "max_consecutive_failures"):
        try:
            nav[key] = max(0, int(nav.get(key, DEFAULTS["navigation"][key])))
        except (TypeError, ValueError):
            nav[key] = DEFAULTS["navigation"][key]
    res = data.setdefault("resume", {})
    try:
        res["ttl_days"] = max(0.0, float(res.get("ttl_days", 7)))
    except (TypeError, ValueError):
        res["ttl_days"] = 7.0
    res["enabled"] = bool(res.get("enabled", True))
    res["path"] = str(res.get("path") or "state/progress.json")
    _CACHE = data
    return data


def _normalized(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def is_pc_unsupported(text: str, rules: dict | None = None) -> bool:
    """页面文本里是否出现「该题型暂不支持 PC 端」这类平台提示。"""
    r = rules or load()
    t = _normalized(text)
    return any(m in t for m in r["pc_unsupported_marks"])


def is_roleplay_stuck(text: str, rules: dict | None = None) -> bool:
    """是否停在 Role-play 的 Step 1 选角页、推进不到 Step 2。"""
    r = rules or load()
    t = _normalized(text)
    return any(m in t for m in r["roleplay_stuck_marks"])
