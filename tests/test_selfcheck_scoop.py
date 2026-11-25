# -*- coding: utf-8 -*-
"""回归测试：自评表（Review & check）与下拉选词（Collocation）（离线）。

两个真实 bug（2026-11-25 实测发现并修复）：

1. **下拉选词 0/6 全失败**：`JS_PICK_SCOOP` 写成 `(idx, answer) =>`，
   而 `page.evaluate()` 只接受一个 arg，于是每次都抛
   `Page.evaluate() takes from 2 to 3 positional arguments but 4`，
   被 except 吞成 debug 日志，整题被判"未识别题型"三振出局。
   → 必须解构单参数 `([idx, answer]) =>`，调用处传 `[idx, ans]`。

2. **自评表把"未选中"当"已选中"**：未选圆圈的 class 是
   `anticon anticon-border primary-color`（未选也是蓝色 primary-color），
   原来用 `/primary-color/` 判"已选"，7 行全部跳过，一行都没点。
   → 只能靠 `anticon-check && !anticon-border` 判已选。

用法：python tests/test_selfcheck_scoop.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc import scoop  # noqa: E402

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


def t_pick_single_arg():
    js = scoop.JS_PICK_SCOOP
    assert js.lstrip().startswith("([idx, answer]) =>"), \
        "JS_PICK_SCOOP 必须是解构单参数（evaluate 只吃一个 arg）"
    assert "(idx, answer) =>" not in js, "不能再用两参数写法"


def t_call_packed():
    assert "JS_PICK_SCOOP" in SRC
    i = SRC.find("_scoop.JS_PICK_SCOOP,")
    assert i > 0, "没找到调用处"
    seg = SRC[i:i + 160]
    assert '[str(_s["index"]), _ans]' in seg, "调用处要把参数打包成 list"


def t_open_scoop_hover():
    js = scoop.JS_OPEN_SCOOP
    for ev in ("mouseenter", "mouseover"):
        assert ev in js, f"antd Dropdown 靠 hover 触发，JS_OPEN_SCOOP 要有 {ev}"
    assert "click()" in js


def t_pick_covers_wrapper():
    js = scoop.JS_PICK_SCOOP
    assert "scoop-select-wrapper" in js, "候选池在 scoop-select-wrapper 里"
    assert "visibility" in js, "要跳过 visibility:hidden 的隐藏池"


def t_selfcheck_class():
    assert "anticon-check" in SRC
    i = SRC.find("JS_SELFCHECK_FILL")
    seg = SRC[i:i + 1600]
    assert "anticon-check" in seg and "!/anticon-border/.test(cls)" in seg, \
        "已选判定必须是 check 且非 border"
    # 不能再单独用 primary-color 判已选
    assert "if (/primary-color/.test(cls)" not in seg, \
        "primary-color 在未选状态也存在，不能拿它判已选"


def t_selfcheck_wired():
    i_sc = SRC.find("①d Review & check 自评表")
    i_ne = SRC.find('log.warning("    页面上有题目但未能识别题型')
    assert 0 < i_sc < i_ne, "自评表要在「未识别」判定之前处理"
    seg = SRC[i_ne - 1400:i_ne]
    assert 'info.get("selfcheck")' in seg, "未识别判定要认可 selfcheck"
    assert 'info.get("scoop")' in seg, "未识别判定要认可 scoop"
    ast.parse(SRC)


def main():
    print("=" * 56)
    print("测试：自评表 + 下拉选词（离线）")
    print("=" * 56)
    check("JS_PICK_SCOOP 解构单参数", t_pick_single_arg)
    check("调用处打包成 list", t_call_packed)
    check("下拉用 hover 触发", t_open_scoop_hover)
    check("候选池选择器覆盖 wrapper", t_pick_covers_wrapper)
    check("自评「已选」不认 primary-color", t_selfcheck_class)
    check("自评表接入在未识别判定之前", t_selfcheck_wired)
    print("=" * 56)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全绿 ✓" if not FAIL else "，有失败 ✗"))
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
