# -*- coding: utf-8 -*-
"""回归测试：讨论区「真发帖」（离线，不碰浏览器）。

实测结论（2026-11-25，重要）：
    U校园 **PC 端** Discussion 页的「发表」按钮是死的——JS dispatchEvent、
    Playwright 真实点击、先点「发表回复」再点「发表」三种方式全是
    0 个网络请求；内容确实进了编辑器（.dis-content，101 字符，键盘逐字打也试过），
    但点完不消失、不提示、不发请求。同页每次打开都弹「请检查网络」。
    → 只标记已看时叶子 pass=1，但节圈永远不蓝；这不是程序能力问题。

本测试只验证「发帖这套判定逻辑本身是对的」，保证平台修好后加
`--quiz-discussion` 就能直接用：

  1. 输入框没内容 → 不发，(False, 'no-text')
  2. 找不到「发表」按钮 → (False, 'no-btn')
  3. 点完抓到 code=0 → (True, 'code=0')
  4. 抓不到响应但输入框被清空 → (True, '输入框已清空')
  5. 抓不到响应、内容还在 → (False, '没抓到提交响应')
  6. `--quiz-discussion` 默认关，主流程按参数透传

用法：python tests/test_discussion_post.py
"""
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

SRC = (ROOT / "tools" / "study_ai.py").read_text(encoding="utf-8")
PASS, FAIL = 0, 0

# 直接 import study_ai 会连带跑 main 的依赖，这里用 ast 截出需要的函数
# 和 JS_* 常量单独 exec（不触发模块级副作用）。
_ns = {"os": os, "json": json}
_keep = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in (
            "_post_discussion", "_page_alive"):
        _keep.append(node)
    elif isinstance(node, ast.Assign):
        for _t in node.targets:
            if isinstance(_t, ast.Name) and _t.id.startswith("JS_"):
                _keep.append(node)
                break
exec(compile(ast.Module(body=_keep, type_ignores=[]),
             "<study_ai>", "exec"), _ns)
_post_discussion = _ns["_post_discussion"]


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓ {name}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  ✗ {name} — {type(exc).__name__}: {exc}")


class FakeLog:
    def info(self, *a, **k):
        pass

    warning = debug = info


class FakeEl:
    def __init__(self, page):
        self.page = page

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def click(self, timeout=None):
        self.page.fire()


class FakeKB:
    def press(self, k):
        pass

    def type(self, t, delay=0):
        pass


class FakePage:
    """按 JS 特征分派 evaluate，够跑通 _post_discussion 全流程。"""

    def __init__(self, text_len=101, after_len=101, find_btn=True,
                 resp=None):
        self.text_len = text_len
        self.after_len = after_len
        self.find_btn = find_btn
        self.resp = resp
        self.reads = 0
        self.handlers = {}
        self.keyboard = FakeKB()
        self.js_clicks = 0

    def on(self, ev, cb):
        self.handlers[ev] = cb

    def remove_listener(self, ev, cb):
        self.handlers.pop(ev, None)

    def fire(self):
        cb = self.handlers.get("response")
        if cb and self.resp is not None:
            class R:
                url = "https://ucontent.unipus.cn/course/api/v3/newExploration/submit"

                def json(self_inner):
                    return self.resp
            cb(R())

    def evaluate(self, js, arg=None):
        if "let best" in js:
            self.reads += 1
            return self.text_len if self.reads == 1 else self.after_len
        if "data-upc-publish" in js:
            return {"tag": "a", "cls": "button"} if self.find_btn else None
        if "return v.trim()" in js:
            return "A long enough opinion text for the discussion."
        if "const cands" in js:
            self.js_clicks += 1
            self.fire()
            return "button"
        return {}

    def query_selector(self, sel):
        if sel == '[data-upc-publish="1"]':
            return FakeEl(self) if self.find_btn else None
        if sel == ".dis-content":
            return FakeEl(self)
        return None

    def wait_for_timeout(self, ms):
        pass


class FakePopup:
    def sweep(self, page, rounds=1):
        pass


def t_no_text():
    pg = FakePage(text_len=5)
    ok, why = _post_discussion(pg, FakeLog(), FakePopup(), {"id": "a"})
    assert (ok, why) == (False, "no-text"), (ok, why)


def t_no_btn():
    pg = FakePage(find_btn=False)
    ok, why = _post_discussion(pg, FakeLog(), FakePopup(), {"id": "a"})
    assert (ok, why) == (False, "no-btn"), (ok, why)


def t_code_zero():
    pg = FakePage(resp={"code": 0})
    ok, why = _post_discussion(pg, FakeLog(), FakePopup(), {"id": "a"})
    assert ok is True and why == "code=0", (ok, why)


def t_cleared():
    pg = FakePage(resp=None, after_len=0)
    ok, why = _post_discussion(pg, FakeLog(), FakePopup(), {"id": "a"})
    assert ok is True and why == "输入框已清空", (ok, why)


def t_no_resp():
    pg = FakePage(resp=None, after_len=101)
    ok, why = _post_discussion(pg, FakeLog(), FakePopup(), {"id": "a"})
    assert ok is False and why == "没抓到提交响应", (ok, why)
    assert pg.js_clicks >= 1, "应当走过多路点击"


def t_flag_wiring():
    assert '"--quiz-discussion"' in SRC
    assert "post_discussion=False" in SRC.replace("_depth=0, ", "")
    seg = SRC[SRC.find('if args.quiz == "fill":'):]
    seg = seg[:seg.find("pc_skip = bool(")]
    assert "post_discussion=bool(" in seg, "主流程未把开关透传下去"
    assert "quiz_discussion" in seg
    ast.parse(SRC)


def main():
    print("=" * 56)
    print("测试：讨论区真发帖判定（离线）")
    print("=" * 56)
    check("输入框空 → 不发", t_no_text)
    check("无「发表」按钮 → 不发", t_no_btn)
    check("抓到 code=0 → 成功", t_code_zero)
    check("输入框被清空 → 成功", t_cleared)
    check("无响应且内容还在 → 失败", t_no_resp)
    check("开关默认关且已透传", t_flag_wiring)
    print("=" * 56)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全绿 ✓" if not FAIL else "，有失败 ✗"))
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
