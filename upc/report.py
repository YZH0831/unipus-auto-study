# -*- coding: utf-8 -*-
"""运行报告：Markdown（给人看）+ JSON（给程序看）。"""
import json
import time
from datetime import datetime
from pathlib import Path


class Report:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.started = time.time()
        self.course = ""
        self.leaves = []
        self.quiz_stats = {"questions": 0, "answered": 0, "submitted": 0, "learned": 0}
        self.speaking_count = 0
        self.duration_spent = 0.0
        self.video_count = 0
        self.notes = []

    # ------------------------------------------------------------------
    def add_note(self, text):
        self.notes.append({"ts": datetime.now().strftime("%H:%M:%S"), "text": text})

    def elapsed(self):
        return time.time() - self.started

    # ------------------------------------------------------------------
    def to_dict(self):
        done = sum(1 for l in self.leaves if l.status == "done")
        failed = sum(1 for l in self.leaves if l.status == "failed")
        skipped = sum(1 for l in self.leaves if l.status == "skipped")
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "course": self.course,
            "elapsed_min": round(self.elapsed() / 60, 1),
            "duration_spent_min": round(self.duration_spent / 60, 1),
            "counts": {
                "total": len(self.leaves), "done": done,
                "failed": failed, "skipped": skipped,
                "pending": len(self.leaves) - done - failed - skipped,
            },
            "quiz": self.quiz_stats,
            "video_played": self.video_count,
            "speaking_handled": self.speaking_count,
            "items": [l.to_dict() if hasattr(l, "to_dict") else l for l in self.leaves],
            "notes": self.notes,
        }

    # ------------------------------------------------------------------
    def save(self):
        out_dir = Path(self.cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        data = self.to_dict()

        json_path = out_dir / f"report_{stamp}.json"
        md_path = out_dir / f"report_{stamp}.md"
        try:
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"写 JSON 报告失败: {str(exc)[:80]}")
        try:
            md_path.write_text(self.to_markdown(data), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"写 Markdown 报告失败: {str(exc)[:80]}")
        self.log.info(f"报告已生成: {md_path}")
        self.log.info(f"             {json_path}")
        return md_path

    # ------------------------------------------------------------------
    def to_markdown(self, data):
        c = data["counts"]
        lines = [
            "# U校园自动化运行报告",
            "",
            f"- 生成时间：{data['generated_at']}",
            f"- 课程：{data['course'] or '(未识别)'}",
            f"- 总耗时：{data['elapsed_min']} 分钟",
            f"- 累计学习时长：{data['duration_spent_min']} 分钟",
            "",
            "## 总览",
            "",
            "| 指标 | 数值 |",
            "|---|---|",
            f"| 可学习项 | {c['total']} |",
            f"| 已完成 | {c['done']} |",
            f"| 失败 | {c['failed']} |",
            f"| 跳过 | {c['skipped']} |",
            f"| 未处理 | {c['pending']} |",
            f"| 视频播放 | {data['video_played']} 个 |",
            f"| 口语处理 | {data['speaking_handled']} 处 |",
            "",
            "## 答题统计",
            "",
            "| 指标 | 数值 |",
            "|---|---|",
            f"| 识别题目 | {data['quiz']['questions']} |",
            f"| 已作答 | {data['quiz']['answered']} |",
            f"| 已提交 | {data['quiz']['submitted']} |",
            f"| 新学到答案 | {data['quiz']['learned']} |",
            "",
            "## 明细",
            "",
            "| # | 单元 / 小节 / 条目 | 状态 | 备注 |",
            "|---|---|---|---|",
        ]
        for i, item in enumerate(data["items"], 1):
            name = " / ".join(p for p in (
                item.get("unit", ""), item.get("section", ""), item.get("title", "")
            ) if p)
            status = {"done": "✅ 完成", "failed": "❌ 失败",
                      "skipped": "⏭️ 跳过", "pending": "⏸️ 未处理"}.get(
                item.get("status", ""), item.get("status", ""))
            note = (item.get("note") or "").replace("|", "\\|")[:60]
            lines.append(f"| {i} | {name} | {status} | {note} |")

        if data["notes"]:
            lines += ["", "## 过程记录", ""]
            for n in data["notes"]:
                lines.append(f"- `{n['ts']}` {n['text']}")

        lines.append("")
        return "\n".join(lines)
