# -*- coding: utf-8 -*-
"""回归测试：口语题 SOE 评分在日志/报告里单独可见（离线）。

验证：
  1. _handle_quiz 拿到 score 时，口语题走专用日志格式（含「口语题评分」字样），
     客观题仍走旧的「判分 scorePct」格式；
  2. 报告汇总从 quiz.voice + quiz.score 提取口语分，转百分制；
     非口语题 / 无分数的条目被正确排除。

用法：python tests/test_voice_score_report.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "tools" / "study_ai.py").read_text(encoding="utf-8")
PASS, FAIL = 0, 0


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓ {name}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  ✗ {name} — {type(exc).__name__}: {exc}")


def t_syntax():
    ast.parse(SRC)


def t_voice_score_log_branch():
    seg = SRC[SRC.find("_pct = f\"{score * 100:.0f}\""):]
    seg = seg[:seg.find("else:")]
    assert "口语题评分" in seg, "口语题分数未用专用日志文案"
    assert "SOE" in seg, "口语日志未标明 SOE 评测来源"


def t_objective_still_old_format():
    assert "判分：scorePct={score} 标准答案" in SRC, "客观题判分日志格式丢失"


def t_voice_score_report_field():
    assert '"voice_scores": voice_scores' in SRC, "报告未写入 voice_scores 字段"
    seg = SRC[SRC.find("voice_scores = []"):SRC.find("out.write_text")]
    assert 'q.get("voice")' in seg, "汇总未筛选口语题"
    assert 'q.get("score")' in seg, "汇总未读取分数"
    assert "* 100" in seg, "分数未按百分制转换"


# 行为模拟：把报告汇总逻辑用可控数据跑一遍
def _simulate(report):
    voice_scores = []
    for r in report:
        q = r.get("quiz") or {}
        if q.get("voice") and isinstance(q.get("score"), (int, float)):
            voice_scores.append({
                "name": r.get("name"),
                "unit": r.get("unit"),
                "score": round(float(q["score"]) * 100, 1),
            })
    return voice_scores


def t_behavior_extracts_only_voice():
    report = [
        {"name": "跟读A", "unit": "Unit1", "quiz": {"voice": True, "score": 0.956}},
        {"name": "选择B", "unit": "Unit1", "quiz": {"voice": False, "score": 0.5}},
        {"name": "口语C", "unit": "Unit2", "quiz": {"voice": True, "score": 0}},
        {"name": "口语D", "unit": "Unit2", "quiz": {"voice": True}},
        {"name": "无quiz", "quiz": None},
    ]
    got = _simulate(report)
    assert len(got) == 2, f"应只提取 2 题口语分，实际 {got}"
    assert got[0]["score"] == 95.6, f"首个分数换算错误: {got[0]}"
    assert got[1]["score"] == 0.0, f"0 分应保留而非丢弃: {got[1]}"


def t_behavior_log_format():
    def fmt(voice, score):
        if voice:
            pct = f"{score * 100:.0f}" if isinstance(score, (int, float)) else "?"
            return f"★ 口语题评分：{pct} 分"
        return f"判分：scorePct={score}"
    assert fmt(True, 0.956) == "★ 口语题评分：96 分"
    assert fmt(True, None) == "★ 口语题评分：? 分"
    assert fmt(False, 0.5) == "判分：scorePct=0.5"


print("[口语题评分可见性 · 回归测试]")
check("源码语法正确", t_syntax)
check("口语分数专用日志文案", t_voice_score_log_branch)
check("客观题判分格式保留", t_objective_still_old_format)
check("报告写入 voice_scores 字段", t_voice_score_report_field)
check("行为：仅提取口语题且换算百分制", t_behavior_extracts_only_voice)
check("行为：口语/客观日志格式分流", t_behavior_log_format)

print("\n" + "=" * 60)
print(f"结果：{PASS}/{PASS + FAIL} 通过" + (f"，{FAIL} 失败" if FAIL else "，全绿 ✓"))
print("=" * 60)
raise SystemExit(1 if FAIL else 0)
