# -*- coding: utf-8 -*-
"""回归测试：不再对「真播重做也点不亮」的节白耗时长（离线）。

背景（2026-11-23 ~ 11-25 实测）：某节叶子全做完、目录圈仍是白的，
主流程会「回退真实播放重做本节」（rate=1.0 / 不拖动），一节白耗
1.5~16 分钟。历史 34 次回退、点亮 0 次——因为这类节的末尾是
「讨论页（只能标记已看）」或「自评表 Review & check」，
平台不把标记已看算进节完成度。

验证：
  1. RunState 新增的 retry_burned / burn_retry / reset_retry_burns
     能正确落盘、重新加载、且不被 mark() 冲掉；
  2. study_ai.py 的重做闸门确实先看「节末类型」，再看本地记账；
  3. 收尾提示对这类节给出「手动去讨论区发一帖」而不是笼统的仍白圈。

用法：python tests/test_retry_burn.py
"""
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc.runstate import RunState  # noqa: E402

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


def _tmp_state():
    p = Path(tempfile.gettempdir()) / "_rs_burn_test.json"
    if p.exists():
        p.unlink()
    return p


def t_syntax():
    ast.parse(SRC)


def t_burn_roundtrip():
    p = _tmp_state()
    try:
        s = RunState(p, ttl_days=7)
        assert s.retry_burned("m51") is False, "初始不该是已记账"
        s.burn_retry("m51", "Reading comprehension")
        assert s.retry_burned("m51") is True, "记账后应为 True"
        # 换一个实例重新读盘，验证真的写进去了
        s2 = RunState(p, ttl_days=7)
        assert s2.retry_burned("m51") is True, "重新加载后应仍然有效"
        assert s2.burned_count == 1
        # mark() 会整体落盘，不能把 micros 段冲掉
        s2.mark("leaf1", "done", "X")
        s3 = RunState(p, ttl_days=7)
        assert s3.retry_burned("m51") is True, "mark() 后 micros 不应丢失"
        assert "leaf1" in s3.data["leaves"]
        # 复位
        assert s3.reset_retry_burns() == 1
        assert s3.retry_burned("m51") is False
    finally:
        p.unlink(missing_ok=True)


def t_ttl_disabled():
    p = _tmp_state()
    try:
        s = RunState(p, ttl_days=7, enabled=False)
        s.burn_retry("m52", "X")
        assert s.retry_burned("m52") is False, "enabled=False 时不记账也不认账"
    finally:
        p.unlink(missing_ok=True)


def t_gate_order():
    """闸门顺序：先判节末类型，再判本地记账，最后才是真播重做。"""
    seg = SRC[SRC.find("_mkey = lf.get("):]
    seg = seg[:seg.find("done += 1")]
    i_tab = seg.find('if _mlast_tab in ("discussion", "check")')
    i_burn = seg.find("elif state.retry_burned(_mkey)")
    i_retry = seg.find("回退真实播放重做本节")
    assert i_tab > 0, "缺少「节末类型」闸门"
    assert i_burn > i_tab, "本地记账判断应在节末类型之后"
    assert i_retry > i_burn, "真播重做必须是最后一种情况"
    assert "state.burn_retry(_mkey" in seg, "回退仍白圈时应记账"


def t_mark_text():
    """这类节的收尾提示要说清「手动去讨论区发一帖」。"""
    seg = SRC[SRC.find("elif is_last and micro_now is False:"):]
    seg = seg[:seg.find("elif blue_delta > 0")]
    assert "white_blocked" in seg and "手动去讨论区发一帖" in seg


def main():
    print("=" * 56)
    print("测试：节级「真播重做点不亮」记账（离线）")
    print("=" * 56)
    check("study_ai.py 语法", t_syntax)
    check("RunState 记账 / 落盘 / 复位", t_burn_roundtrip)
    check("enabled=False 时不记账", t_ttl_disabled)
    check("闸门顺序：节末类型 → 本地记账 → 真播重做", t_gate_order)
    check("收尾提示指明需手动发表", t_mark_text)
    print("=" * 56)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全绿 ✓" if not FAIL else "，有失败 ✗"))
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
