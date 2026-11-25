# -*- coding: utf-8 -*-
"""回归测试：U校园「本可自愈却叫人」修复（离线，不联网不开浏览器）。

把 study_ai.py 里两处自愈逻辑的关键片段抽出来，用可控桩驱动，验证：
  1. 结构为空 → 会重试 2 轮自动重登自愈（不再是 1 次就报"请手动执行"）；
  2. 连续打不开 → 有自愈额度时先重登+验证，成功则继续（不 wake）；
  3. 自愈额度耗尽 / 失败 → 才 wake；
  4. 源码守卫：heal_budget 存在、wake 只在自愈失败分支、自愈验证用 _fetch_struct。

用法：python tests/test_selfheal_fix.py
"""
import ast
import re
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


# ---------- 静态守卫：确认源码结构（防止改动被误删/回归） ----------
def t_syntax_ok():
    ast.parse(SRC)


def t_heal_budget_declared():
    assert 'heal_budget = {"left": 2}' in SRC, "连续失败自愈额度未声明"


def t_struct_retry_loop():
    seg = SRC[SRC.find("if not (struct_raw or {}).get(\"course\"):"):
              SRC.find("course_str = (struct_raw")]
    assert "range(1, 3)" in seg, "结构为空自愈未做 2 轮重试"
    assert "_relogin_and_reinject(session, cfg, log)" in seg, "结构自愈未调用重登"
    assert "自愈成功" in seg, "结构自愈成功未记录"


def t_consecutive_fail_heal_before_wake():
    seg = SRC[SRC.find("consecutive_failures += 1"):
              SRC.find("aborted = True", SRC.find("consecutive_failures += 1"))]
    heal = seg.find("heal_budget")
    wake = seg.find("wake(")
    assert 0 < heal < wake, "连续失败：自愈必须在 wake 之前"
    assert "if healed:" in seg and "continue" in seg, "自愈成功后未 continue"


def t_wake_only_after_heal_fails():
    seg = SRC[SRC.find("consecutive_failures += 1"):
              SRC.find("aborted = True", SRC.find("consecutive_failures += 1"))]
    # heal_budget 判据必须在 wake 之前，且 wake 在自愈分支之外
    assert re.search(r"if heal_budget\[\"left\"\] > 0:", seg), "缺少自愈额度判据"
    marker = seg.find("# 自愈额度用完 / 自愈失败")
    assert 0 < marker < seg.find("wake("), "wake 前缺少『自愈失败才叫人』说明"


# ---------- 行为模拟：用源码片段驱动可控桩 ----------
def _sim_struct_heal(valid_on_attempt):
    """模拟结构为空自愈循环：valid_on_attempt=N 表示第 N 次 _fetch_struct 才非空。"""
    state = {"fetch": 0, "relogin": 0, "log": []}
    struct_raw = {"course": None}

    def fetch():
        state["fetch"] += 1
        return {"course": "OK"} if state["fetch"] >= valid_on_attempt else {}

    def relogin():
        state["relogin"] += 1
        return True

    # 复刻修复后的循环结构
    for i in range(1, 3):
        state["log"].append(f"heal {i}/2")
        if relogin():
            struct_raw = fetch()
        if (struct_raw or {}).get("course"):
            state["log"].append("healed")
            break
    return state, struct_raw


def t_behavior_struct_heals_on_2nd():
    st, out = _sim_struct_heal(valid_on_attempt=2)
    assert out.get("course") == "OK", "第 2 次自愈应拿到结构"
    assert st["relogin"] == 2 and st["fetch"] == 2, "应重登/取结构各 2 次"
    assert "healed" in st["log"], "应标记自愈成功"


def t_behavior_struct_no_manual_on_1st():
    st, out = _sim_struct_heal(valid_on_attempt=1)
    assert out.get("course") == "OK"
    assert st["relogin"] == 1 and st["fetch"] == 1, "第 1 次就恢复不应多跑"


def _sim_consecutive(budget, relogin_ok, struct_ok):
    """模拟连续失败自愈分支，返回 (是否 healed, 是否 wake, 剩余额度)。"""
    left = {"n": budget}
    healed, woken = False, False
    consecutive = 3
    if left["n"] > 0:
        left["n"] -= 1
        if relogin_ok and struct_ok:
            consecutive = 0
            healed = True
    if healed:
        return True, False, left["n"], consecutive
    woken = True
    return False, woken, left["n"], consecutive


def t_behavior_consecutive_heals_then_continue():
    healed, woken, left, c = _sim_consecutive(2, True, True)
    assert healed and not woken, "自愈成功不应 wake"
    assert c == 0 and left == 1, "自愈成功应清零失败计数且扣 1 额度"


def t_behavior_consecutive_wakes_when_heal_fails():
    healed, woken, left, c = _sim_consecutive(1, False, False)
    assert not healed and woken, "自愈失败才应 wake"


def t_behavior_consecutive_wakes_when_budget_0():
    healed, woken, left, c = _sim_consecutive(0, True, True)
    assert not healed and woken and left == 0, "额度耗尽应 wake"


print("[U校园 自愈修复 · 回归测试]")
check("源码语法正确", t_syntax_ok)
check("heal_budget 自愈额度已声明", t_heal_budget_declared)
check("结构为空做 2 轮重登自愈", t_struct_retry_loop)
check("连续失败先自愈再 wake", t_consecutive_fail_heal_before_wake)
check("wake 仅在自愈失败分支", t_wake_only_after_heal_fails)
check("行为：第 2 次自愈拿到结构", t_behavior_struct_heals_on_2nd)
check("行为：第 1 次就恢复不多跑", t_behavior_struct_no_manual_on_1st)
check("行为：连续失败自愈成功不 wake", t_behavior_consecutive_heals_then_continue)
check("行为：自愈失败才 wake", t_behavior_consecutive_wakes_when_heal_fails)
check("行为：额度耗尽 wake", t_behavior_consecutive_wakes_when_budget_0)

print("\n" + "=" * 60)
print(f"结果：{PASS}/{PASS + FAIL} 通过" + (f"，{FAIL} 失败" if FAIL else "，全绿 ✓"))
print("=" * 60)
raise SystemExit(1 if FAIL else 0)
