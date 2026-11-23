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
import re
import time
from pathlib import Path


class RunState:
    # 本地判定「程序永远做不完」的叶子（重做纯浪费时长，直接跳过）：
    #   unsupported_pc = 平台要求 App 端完成
    #   needs_upload   = 需手动上传作品文件
    #   needs_manual   = 同一页面反复失败（题型暂不支持），需手动完成
    SKIP_STATUSES = ("unsupported_pc", "needs_upload", "needs_manual")

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

    def cached_status(self, leaf_id) -> str:
        """返回有效期内缓存的跳过状态（''=没缓存）。

        只认 SKIP_STATUSES 两类「程序永远做不完」的结论：
        unsupported_pc（平台要求 App 端）/ needs_upload（需手动上传作品）。
        其余状态（done/failed）一律以平台自己的 pass 为准，绝不因缓存漏刷。
        """
        if not self.enabled or self.ttl <= 0:
            return ""
        rec = self.data["leaves"].get(str(leaf_id))
        if not isinstance(rec, dict) or rec.get("status") not in self.SKIP_STATUSES:
            return ""
        try:
            ts = float(rec.get("ts") or 0)
        except (TypeError, ValueError):
            return ""
        if ts > 0 and (time.time() - ts) < self.ttl:
            return str(rec.get("status"))
        return ""

    def is_cached_unsupported(self, leaf_id) -> bool:
        """这个叶子是否在有效期内被判定过「本地做不完」（跳过类）。"""
        return bool(self.cached_status(leaf_id))

    def mark(self, leaf_id, status: str, name: str = ""):
        """每处理完一个叶子就增量落盘，崩溃也能从本地状态续跑。"""
        if not self.enabled:
            return
        rec = {
            "status": str(status),
            "ts": time.time(),
            "name": str(name or "")[:60],
        }
        old = self.data["leaves"].get(str(leaf_id))
        if isinstance(old, dict) and old.get("fails"):
            rec["fails"] = int(old["fails"])      # 保留历史失败计数
        self.data["leaves"][str(leaf_id)] = rec
        self.data["updated_at"] = time.time()
        self._save()

    def count_fail(self, leaf_id, name: str = "") -> int:
        """记一次「未完成」失败，返回累计失败次数（三振出局的核心计数）。

        满分 3 振：历史失败现场（dump）预置 2 振 + 运行中每次失败 +1，
        累计 ≥ 3 由主循环判 needs_manual，跳过不再无限重做。
        """
        key = str(leaf_id)
        rec = self.data["leaves"].get(key) if isinstance(
            self.data["leaves"].get(key), dict) else {}
        rec = dict(rec)
        rec["status"] = "incomplete"
        rec["ts"] = time.time()
        rec["name"] = str(name or rec.get("name") or "")[:60]
        rec["fails"] = int(rec.get("fails") or 0) + 1
        self.data["leaves"][key] = rec
        self.data["updated_at"] = time.time()
        self._save()
        return int(rec["fails"])

    def seed_fails_from_dumps(self, dump_dir, log=None) -> int:
        """把历史失败现场（output/unknown_*.json、incomplete_*.json）折算成失败计数。

        现场 dump 是过去失败的铁证：这些叶子已经反复重做过、圈仍不蓝。
        给它们预置 2 振在身——本次运行若再次失败立即三振出局（needs_manual）；
        若题型已被新代码修复则正常做完、不计振、不受影响。
        幂等：重复调用不会叠加（只升到 2，不往上加）。
        """
        if not self.enabled:
            return 0
        pat = re.compile(r"^(?:unknown|incomplete)_(.+)\.json$")
        n = 0
        try:
            for f in Path(dump_dir).glob("*.json"):
                m = pat.match(f.name)
                if not m:
                    continue
                key = m.group(1)
                old = self.data["leaves"].get(key)
                rec = dict(old) if isinstance(old, dict) else {}
                if int(rec.get("fails") or 0) >= 2:
                    continue
                rec["fails"] = 2
                rec.setdefault("status", "incomplete")
                rec.setdefault("name", "")
                rec["ts"] = time.time()
                self.data["leaves"][key] = rec
                n += 1
        except Exception as exc:  # noqa: BLE001  现场折算失败不影响刷课
            if log:
                log.debug(f"现场 dump 折算失败（忽略）：{type(exc).__name__}")
            return n
        if n:
            self.data["updated_at"] = time.time()
            self._save()
            if log:
                log.info(f"检测到 {n} 个历史失败现场：已预置 2 振在身，"
                         "再失败 1 次即出局跳过")
        return n

    def reset_manual(self) -> int:
        """解除全部 needs_manual（三振出局）标记，让这些叶子重新尝试。

        修好对应题型后用（--retry-manual）；失败计数保留，
        若再次失败会很快重新出局，不会回到无限重做。
        """
        n = 0
        for key, rec in self.data["leaves"].items():
            if isinstance(rec, dict) and rec.get("status") == "needs_manual":
                rec["status"] = "incomplete"
                rec["ts"] = time.time()
                n += 1
        if n:
            self.data["updated_at"] = time.time()
            self._save()
        return n

    def clear(self):
        self.data = {"version": 1, "updated_at": time.time(), "leaves": {}}
        self._save()

    @property
    def cached_unsupported_count(self) -> int:
        return sum(1 for r in self.data["leaves"].values()
                   if isinstance(r, dict) and r.get("status") == "unsupported_pc")
