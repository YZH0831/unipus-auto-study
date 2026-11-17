# -*- coding: utf-8 -*-
"""本地断点续跑快照（state/progress.json，已在 .gitignore 里）。

只记住一类结论：**平台明确说 PC 端不支持的叶子**。这类项无论跑多少遍都不会
变蓝，每轮都要重新打开页面扫一遍才判定；本地记住后下次连页面都不打开。
其余状态一律以平台自己的 pass 为准，绝不因为本地缓存而漏刷。

写盘用 tmp + os.replace 原子替换，Ctrl+C / 崩溃都不会留下半截 JSON。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class RunState:
    def __init__(self, path, ttl_days: float = 7.0, enabled: bool = True,
                 log=None):
        self.path = Path(path)
        self.ttl = float(ttl_days) * 86400
        self.enabled = bool(enabled)
        self.log = log
        self.data = {"version": 1, "updated_at": 0, "leaves": {}}
        self._load()

    def _load(self):
        try:
            if not self.path.is_file():
                return
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("leaves"), dict):
                self.data = d
                self.data.setdefault("leaves", {})
        except Exception as exc:  # noqa: BLE001  坏文件当空，绝不让主流程起不来
            if self.log:
                self.log.debug(f"本地进度缓存损坏，已忽略：{type(exc).__name__}")

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)  # 原子替换
        except Exception as exc:  # noqa: BLE001
            if self.log:
                self.log.debug(f"进度缓存写入失败（不影响刷课）：{type(exc).__name__}")

    def is_cached_unsupported(self, leaf_id) -> bool:
        """这个叶子是否在有效期内被判定过「PC 端不支持」。"""
        if not self.enabled or self.ttl <= 0:
            return False
        rec = self.data["leaves"].get(str(leaf_id))
        if not isinstance(rec, dict) or rec.get("status") != "unsupported_pc":
            return False
        try:
            ts = float(rec.get("ts") or 0)
        except (TypeError, ValueError):
            return False
        return ts > 0 and (time.time() - ts) < self.ttl

    def mark(self, leaf_id, status: str, name: str = ""):
        """每处理完一个叶子就增量落盘，崩溃也能从本地状态续跑。"""
        if not self.enabled:
            return
        self.data["leaves"][str(leaf_id)] = {
            "status": str(status),
            "ts": time.time(),
            "name": str(name or "")[:60],
        }
        self.data["updated_at"] = time.time()
        self._save()

    def clear(self):
        self.data = {"version": 1, "updated_at": time.time(), "leaves": {}}
        self._save()

    @property
    def cached_unsupported_count(self) -> int:
        return sum(1 for r in self.data["leaves"].values()
                   if isinstance(r, dict) and r.get("status") == "unsupported_pc")
