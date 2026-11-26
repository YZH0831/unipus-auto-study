# -*- coding: utf-8 -*-
"""回归测试：运行中「需要人工」环节的三类误判（离线，不碰浏览器）。

2026-11-26 实测留下的三个坑：

1. **作品/录音上传题被三振出局**（8 项）
   Group discussion / Step 2 / Discussion 这类要上传作品或录音的题，
   平台收卷后不即时判分（scorePct=0，等老师评分）。旧逻辑把 0 分当失败，
   第二遍重做再判 0，累计三振 → needs_manual，实际早就交上去了。
   → `subjective_accepted`：上传成功 + 提交成功 + 平台没拦 → 按已交卷处理，
     不进三振、第二遍不重做、后续轮次直接跳过。

2. **下拉选词把菜单点没**（Unit 2 Before you view / Exercise 1、Unit 5 Exercise 5）
   旧逻辑先 hover（antd 已打开菜单）再 click（又关掉），选项永远点不到。
   → 先读 `menuOpen`，只有没开才点一次；选项用真实点击并回读确认，失败重开重试。

3. **Role-play 选角页被误判 PC 端不支持**
   旧逻辑点 `.role` 大容器，可能被 swiper 当成翻页翻到空白 slide；
   整页文本又一直含离屏的 "Choose a role"，于是被判「卡在选角页」。
   → 只点 label/span 最内层；用可见矩形判断选角页是否还在；支持 swiper 回退。

用法：python tests/test_manual_fixes.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from upc import scoop  # noqa: E402
from upc.runstate import RunState  # noqa: E402
import study_ai  # noqa: E402

PASS, FAIL = 0, 0
SRC = (ROOT / "tools" / "study_ai.py").read_text(encoding="utf-8")
SCOOP_SRC = (ROOT / "upc" / "scoop.py").read_text(encoding="utf-8")


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

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class FakeEl:
    def __init__(self, text, onclick=None, visible=True):
        self.text = text
        self.onclick = onclick
        self.visible = visible
        self.clicks = 0

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def scroll_into_view_if_needed(self, timeout=0):
        pass

    def click(self, timeout=0):
        self.clicks += 1
        if self.onclick:
            self.onclick()


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "Escape":
            self.page.menu_open = False


class ScoopPage:
    """模拟一个 fe-scoop 空：菜单开/关 + 选项点击生效。"""

    def __init__(self, empty=True, menu_open=False, select_on_click=1):
        self.empty = empty
        self.menu_open = menu_open
        self.select_on_click = select_on_click
        self.item_clicks = 0
        self.trigger_clicks = 0
        self.trigger = FakeEl("点击选择", self._toggle)
        self.item = FakeEl("make", self._pick)
        self.keyboard = FakeKeyboard(self)

    def _toggle(self):
        self.trigger_clicks += 1
        self.menu_open = not self.menu_open

    def _pick(self):
        self.item_clicks += 1
        if self.item_clicks >= self.select_on_click:
            self.empty = False
            self.menu_open = False

    def evaluate(self, js, *a):
        if js is scoop.JS_SCOOP_STATE:
            return {"found": True, "empty": self.empty,
                    "text": "点击选择" if self.empty else "make",
                    "menuOpen": self.menu_open}
        if js is scoop.JS_PICK_SCOOP:
            return {"ok": False}  # 真实点击路径已覆盖；JS 兜底不参与本测试
        return {}

    def query_selector(self, sel):
        return self.trigger

    def query_selector_all(self, sel):
        return [self.item] if self.menu_open else []

    def wait_for_timeout(self, ms):
        pass

    def press(self, key):
        if key == "Escape":
            self.menu_open = False


class RolePage:
    """按顺序返回 JS_ROLE_STATE 的结果，模拟 swiper/选角页变化。"""

    def __init__(self, states, can_pick=True):
        self.states = list(states)
        self.i = 0
        self.calls = []
        self.can_pick = can_pick

    def evaluate(self, js):
        if js is study_ai.JS_ROLE_STATE:
            st = self.states[min(self.i, len(self.states) - 1)]
            self.i += 1
            return dict(st)
        if js is study_ai.JS_ROLE_PICK:
            self.calls.append("pick")
            return {"found": True, "n": 2,
                    "picked": "A" if self.can_pick else ""}
        if js is study_ai.JS_SWIPER_BACK:
            self.calls.append("back")
            return {"ok": True}
        return {}

    def wait_for_timeout(self, ms):
        pass

    def query_selector_all(self, sel):
        return []


def t_runstate_status():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "progress.json"
        st = RunState(p, ttl_days=7)
        assert "submitted_subjective" in st.SKIP_STATUSES
        st.mark("leaf1", "submitted_subjective", "Interviewing")
        st2 = RunState(p, ttl_days=7)
        assert st2.cached_status("leaf1") == "submitted_subjective", \
            "交卷标记必须能跨进程续跑"


def t_subjective_accepted_source():
    i = SRC.find('info["subjective_accepted"] = True')
    assert i > 0, "缺少 subjective_accepted 标记"
    seg = SRC[max(0, i - 700):i]
    assert 'info.get("uploaded")' in seg, "只有真的上传成功才算"
    assert 'submitted != "标记已看"' in seg, "标记已看不等于交卷"
    assert 'not info.get("incomplete")' in seg, "平台拦未作答不能算交卷"
    assert 'and not quiz_info.get("subjective_accepted")' in SRC, \
        "scorePct=0 的三振判定必须排除已交卷作品题"
    assert '"submitted_subjective" if _sub_accepted' in SRC, \
        "状态落盘要记 submitted_subjective"


def t_quiz_failed_initialized():
    i = SRC.find("quiz_failed = False")
    j = SRC.find("if not pc_skip and not up_skip:")
    assert 0 < i < j, \
        "每个叶子都要先初始化 quiz_failed，否则 PC/上传跳过时会用到上一个叶子的值"


def t_subjective_skip_and_report():
    assert '跳过已交卷待评的主观题' in SRC
    assert '"skipped_submitted_subjective"' in SRC
    assert '已交卷（等老师评分）' in SRC
    assert 'submitted_subjective_skips' in SRC


def t_scoop_pick_closed():
    pg = ScoopPage(empty=True, menu_open=False)
    ok = scoop.pick_scoop(pg, FakeLog(), "0", "make")
    assert ok is True
    assert pg.trigger_clicks == 1, "菜单没开时应点一次触发器"
    assert pg.item_clicks == 1


def t_scoop_skip_trigger_when_open():
    pg = ScoopPage(empty=True, menu_open=True)
    ok = scoop.pick_scoop(pg, FakeLog(), "0", "make")
    assert ok is True
    assert pg.trigger_clicks == 0, "菜单已开还点触发器会把它关掉"


def t_scoop_retry_after_miss():
    pg = ScoopPage(empty=True, menu_open=False, select_on_click=2)
    ok = scoop.pick_scoop(pg, FakeLog(), "0", "make", tries=2)
    assert ok is True
    assert pg.trigger_clicks == 2, "第一次没选上应重开菜单再试"
    assert pg.item_clicks == 2


def t_scoop_no_answer():
    pg = ScoopPage(empty=True, menu_open=False)
    assert scoop.pick_scoop(pg, FakeLog(), "0", "") is False
    assert pg.trigger_clicks == 0


def t_scoop_parse_trailing_dot():
    scoops = [
        {"index": "2", "options": ["Joseph Sekar", "Billy Ellis", "Elvira"]},
        {"index": "4", "options": ["a city in India",
                                   "mountains of Colorado in the U.S.A.",
                                   "a village in Peru"]},
        {"index": "5", "options": ["a city in India",
                                   "mountains of Colorado in the U.S.A.",
                                   "a village in Peru"]},
    ]
    raw = ("ANSWER: Joseph Sekar | mountains of Colorado in the U.S.A. "
           "| a city in India\nCONF: 0.95")
    got = scoop.parse_llm_answers(raw, scoops)
    assert got == ["Joseph Sekar", "mountains of Colorado in the U.S.A.",
                   "a city in India"], f"末尾句点不应导致漏空：{got}"


def t_scoop_state_js():
    js = scoop.JS_SCOOP_STATE
    assert "menuOpen" in js and "user-answer-text" in js
    assert "ant-dropdown" in js


def t_role_pick_js_targets_span():
    js = study_ai.JS_ROLE_PICK
    assert ".role-list .role" in js
    assert "label.label" in js
    assert "hit.span || hit.lab" in js, "必须优先点最内层 span/label"
    assert "hit.e.click()" not in js, "不能再点 .role 大容器"


def t_role_pick_success():
    pg = RolePage([
        {"picker": True, "pickerVisible": True},
        {"picker": True, "pickerVisible": False, "quiz": True},
    ])
    assert study_ai._pick_roleplay_role(pg, FakeLog()) is True
    assert pg.calls == ["pick"]


def t_role_already_step2():
    pg = RolePage([
        {"picker": True, "pickerVisible": False, "quiz": True},
    ])
    assert study_ai._pick_roleplay_role(pg, FakeLog()) is False
    assert pg.calls == [], "已在 Step 2 不能把 swiper 退回第 1 页"


def t_role_swiper_back():
    pg = RolePage([
        {"picker": True, "pickerVisible": False, "quiz": False},
        {"picker": True, "pickerVisible": True},
        {"picker": True, "pickerVisible": False, "quiz": True},
    ])
    assert study_ai._pick_roleplay_role(pg, FakeLog()) is True
    assert pg.calls == ["back", "pick"]


def t_role_gate_visible_only():
    i = SRC.find("_role_visible = False")
    assert i > 0, "未识别判定应改用可见性判断"
    seg = SRC[i:i + 520]
    assert "JS_ROLE_STATE" in seg
    assert 'is_roleplay_stuck(_page_text)' not in SRC, \
        "不能再按整页文本判「卡在选角页」"


def main():
    print("=" * 56)
    print("测试：需人工环节的三类误判（离线）")
    print("=" * 56)
    check("RunState 记住已交卷状态", t_runstate_status)
    check("作品题交卷判定（源码守卫）", t_subjective_accepted_source)
    check("quiz_failed 每叶初始化", t_quiz_failed_initialized)
    check("交卷项的跳过与报告", t_subjective_skip_and_report)
    check("下拉：菜单没开才点触发器", t_scoop_pick_closed)
    check("下拉：菜单已开不重复点", t_scoop_skip_trigger_when_open)
    check("下拉：没选上自动重开重试", t_scoop_retry_after_miss)
    check("下拉：没答案不空点", t_scoop_no_answer)
    check("下拉：候选末尾句点不丢空", t_scoop_parse_trailing_dot)
    check("下拉状态探针覆盖 menuOpen", t_scoop_state_js)
    check("选角只点 label/span", t_role_pick_js_targets_span)
    check("选角成功后进入 Step 2", t_role_pick_success)
    check("已在 Step 2 不误退", t_role_already_step2)
    check("空白 slide 自动退回后重选", t_role_swiper_back)
    check("选角判定只看可见性", t_role_gate_visible_only)
    print("=" * 56)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全绿 ✓" if not FAIL else "，有失败 ✗"))
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
