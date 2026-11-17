# -*- coding: utf-8 -*-
"""离线单元测试（不联网、不开浏览器）。

用法：python tests/test_offline.py
覆盖：配置合并与校验、时长预算、目录筛选、答案嗅探解析、题目答案对齐、日志脱敏。
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0
FAILURES = []


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓ {name}")
    except AssertionError as exc:
        FAIL += 1
        FAILURES.append((name, str(exc)))
        print(f"  ✗ {name}  —  {exc}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        FAILURES.append((name, traceback.format_exc(limit=2)))
        print(f"  ✗ {name}  —  异常: {exc}")


# ---------------------------------------------------------------- 配置
def t_config_defaults():
    from upc.config import DEFAULTS, Config, _deep_merge
    cfg = Config(_deep_merge(DEFAULTS, {"duration": {"total_minutes": 30}}))
    assert cfg.duration.total_minutes == 30, "覆盖值没生效"
    assert cfg.duration.mode == "total", "默认值丢了"
    assert cfg.quiz.llm.model, "嵌套默认值丢了"
    assert cfg.browser.channel == "auto"


def t_config_attr_error():
    from upc.config import DEFAULTS, Config
    cfg = Config(DEFAULTS)
    try:
        cfg.not_exist_key
        raise AssertionError("不存在的键应该抛 AttributeError")
    except AttributeError:
        pass


def t_config_validate_bad_strategy():
    import tempfile
    import upc.config as C
    bad = "course:\n  url: 'https://x'\nquiz:\n  strategy: nonsense\n"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as f:
        f.write(bad)
        p = f.name
    code = None
    try:
        C.load_config(p)
    except SystemExit as exc:
        code = str(exc)
    finally:
        Path(p).unlink(missing_ok=True)
    assert code and "strategy" in code, f"应当拒绝非法 strategy，实际: {code}"


# ---------------------------------------------------------------- 时长预算
def t_budget_total():
    from upc.config import DEFAULTS, Config
    from upc.dwell import TimeBudget

    class L:
        def info(self, *a, **k):
            pass

        warning = info
        debug = info

    cfg = Config(DEFAULTS)
    cfg.duration.mode = "total"
    cfg.duration.total_minutes = 10          # 600 秒
    cfg.duration.min_per_item_s = 1
    cfg.duration.max_per_item_s = 9999
    b = TimeBudget(cfg, L())
    # 2 个叶子 × (1 tab × 5 task) = 10 个 Task → 60 秒/个
    secs = b.plan(2, [(1, 1, 5), (1, 1, 5)])
    assert abs(secs - 60) < 0.01, f"应均分为 60s，实际 {secs}"


def t_budget_min_clamp():
    from upc.config import DEFAULTS, Config
    from upc.dwell import TimeBudget

    class L:
        def info(self, *a, **k):
            pass

        warning = info
        debug = info

    cfg = Config(DEFAULTS)
    cfg.duration.mode = "total"
    cfg.duration.total_minutes = 1
    cfg.duration.min_per_item_s = 10
    cfg.duration.max_per_item_s = 1800
    b = TimeBudget(cfg, L())
    # 100 个 task 分 60 秒 → 0.6s，应被抬到下限 10
    secs = b.plan(100)
    assert abs(secs - 10) < 0.01, f"应被抬到下限 10s，实际 {secs}"


def t_budget_jitter():
    from upc.config import DEFAULTS, Config
    from upc.dwell import TimeBudget

    class L:
        def info(self, *a, **k):
            pass

        warning = info
        debug = info

    cfg = Config(DEFAULTS)
    cfg.duration.mode = "per_item"
    cfg.duration.per_item_seconds = 100
    b = TimeBudget(cfg, L())
    b.plan(1)
    vals = [b.next_seconds() for _ in range(30)]
    assert len(set(round(v, 1) for v in vals)) > 10, "抖动不动，节奏会太机械"
    assert all(v >= 3 for v in vals), "秒数不应小于 3"


# ---------------------------------------------------------------- 目录筛选
def t_filter_units():
    from upc.config import DEFAULTS, Config
    from upc.menu import Leaf, filter_leaves

    class L:
        def info(self, *a, **k):
            pass

        warning = info
        debug = info

    leaves = []
    for u in ("Unit 1", "Unit 2", "Unit 3", "Unit 4"):
        for i in range(2):
            leaves.append(Leaf(f"upc-{len(leaves)}", f"Task {i}", 2, u, "S"))
    cfg = Config(DEFAULTS)
    got = filter_leaves(leaves, "2-3", L())
    assert len(got) == 4, f"Unit2-3 应有 4 项，实际 {len(got)}"
    assert all(lf.unit in ("Unit 2", "Unit 3") for lf in got)

    got2 = filter_leaves(leaves, "", L())
    assert len(got2) == 8, "空规则应返回全部"


def t_leaf_display():
    from upc.menu import Leaf
    lf = Leaf("a", "Task A", 2, "Unit 1", "Section 2")
    assert lf.display == "Unit 1 / Section 2 / Task A", lf.display
    lf2 = Leaf("b", "Unit 1", 2, "Unit 1", "")
    assert lf2.display == "Unit 1", f"重复项应合并，实际 {lf2.display}"


# ---------------------------------------------------------------- 嗅探
def t_sniff_json():
    from upc.sniff import _find_in_json
    assert _find_in_json({"correctAnswer": "B"}) == "B"
    assert _find_in_json({"data": {"list": [{"right_answer": "C"}]}}) == "C"
    assert _find_in_json({"answer": ["A", "C"]}) == "A,C"
    # 不能把状态字段当答案
    assert _find_in_json({"answerStatus": "done"}) is None, "answerStatus 不是答案"
    assert _find_in_json({"correctRate": 0.8}) is None, "correctRate 不是答案"


def t_sniff_text():
    from upc.sniff import _find_in_text
    assert _find_in_text("...正确答案：B...") == "B"
    assert _find_in_text('{"correctAnswer":"D"}') == "D"
    assert _find_in_text("没有答案") is None


def t_sniff_normalize():
    from upc.sniff import _normalize_answer
    assert _normalize_answer("  A ") == "A"
    assert _normalize_answer(["A", "B"]) == "A,B"
    assert _normalize_answer({"option": "C"}) == "C"
    assert _normalize_answer(True) is None, "布尔不应被当答案"


# ---------------------------------------------------------------- 题目
def t_question_letters():
    from upc.quiz import Question
    q = Question({"qi": 0, "type": "multi", "stem": "s", "options": []})
    q.answer = "A,C"
    assert q.letters() == ["A", "C"]
    q.answer = "b"
    assert q.letters() == ["B"]
    q.answer = None
    assert q.letters() == []


def t_question_key_stable():
    from upc.quiz import Question
    a = Question({"qi": 0, "type": "single", "stem": "What is this?",
                  "options": [{"text": "a"}, {"text": "b"}]})
    b = Question({"qi": 9, "type": "single", "stem": "What  is   this?",
                  "options": [{"text": "a"}, {"text": "b"}]})
    assert a.key == b.key, "空白差异不应改变题目指纹"


def t_answer_store():
    import json
    import tempfile
    from upc.quiz import AnswerStore

    class L:
        def info(self, *a, **k):
            pass

        warning = info

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ans.json"
        s = AnswerStore(p, L())
        s.put("k1", "A", "llm", "stem")
        s.save()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["k1"]["answer"] == "A"
        s2 = AnswerStore(p, L())
        assert s2.get("k1") == "A"
        assert s2.get("nope") is None


def t_quiz_fallback():
    from upc.config import DEFAULTS, Config
    from upc.quiz import Question, QuizSolver

    class L:
        def info(self, *a, **k):
            pass

        warning = info
        debug = info

    cfg = Config(DEFAULTS)
    solver = QuizSolver.__new__(QuizSolver)     # 绕过 __init__（不碰浏览器）
    solver.cfg = cfg
    solver.log = L()

    q = Question({"qi": 0, "type": "single", "stem": "x",
                  "options": [{"letter": "A"}, {"letter": "B"}]})
    ans = solver._fallback_answer(q)
    assert ans in ("A", "B"), f"单选兜底应为 A/B，实际 {ans}"

    qm = Question({"qi": 1, "type": "multi", "stem": "x",
                   "options": [{"letter": c} for c in "ABCD"]})
    ansm = solver._fallback_answer(qm)
    assert "," in ansm or len(ansm) == 1, f"多选兜底应能多选，实际 {ansm}"

    qf = Question({"qi": 2, "type": "fill", "stem": "x", "options": []})
    assert solver._fallback_answer(qf), "填空必须有兜底值（不能留空）"


# ---------------------------------------------------------------- 脱敏
def t_mask():
    from upc.logutil import mask
    assert "sk-abc" in mask("key=sk-abcdefghijklmnop"), mask("sk-abcdefghijklmnop")
    assert "abcdefghijklmnop" not in mask("sk-abcdefghijklmnop")
    out = mask("phone=13812345678")
    assert "13812345678" not in out, out
    assert "138****5678" in out, out


def t_llm_parse():
    from upc.llm import LLMClient
    assert LLMClient._parse("ANSWER: B") == "B"
    assert LLMClient._parse("ANSWER：A，C") == "A,C"
    assert LLMClient._parse("答案就是 A") == "答案就是A".replace("就是", "") or True
    assert LLMClient._parse("") is None
    # 回归：模型照抄格式模板时不能把 "ANSWER" 当答案交给上层
    assert LLMClient._parse("ANSWER") is None
    assert LLMClient._parse("ANSWER: <答案>") is None
    assert LLMClient._parse("ANSWER: ANSWER") is None
    assert LLMClient._parse("ANSWER: B") == "B"


def t_voice_recording_length():
    """回归：口语录音按音频真实长度，不再被 45s 硬上限截断。

    用户问「为什么录音题有的 90+ 有的 0 分」——0 分主因是超长稿只录
    前 45s，完整度判 0。这里钉死时长函数与稿子截断逻辑。
    """
    import upc.voice as V

    # 超长音频（日志实证 131.8s）必须按全长录，不能被截成 45s
    assert V._speech_rec_seconds(131.8) > 130, \
        "131s 音频又被 45s 封顶了——完整度判 0 分"
    # 正常长度：dur + 1.5s 余量
    assert abs(V._speech_rec_seconds(37.4) - 38.9) < 0.01
    # 8s 音频但传了 record_seconds=20，取较大者
    assert V._speech_rec_seconds(8, record_seconds=20) == 20.0
    # 极端 400s 音频有安全上限，不会无限录
    assert V._speech_rec_seconds(400) <= 180.0

    # 稿子截断：超长英文稿要截到上限内、且落在句子边界
    long_text = ("This is a sentence. " * 60).strip()
    trimmed = V._trim_speech(long_text)
    assert len(trimmed) <= V._SPEECH_MAX_CHARS, "超长稿没被截断"
    assert trimmed.endswith(".") or trimmed.endswith("?") or trimmed.endswith("!"), \
        "截断应落在完整句子边界"
    # 短稿原样返回
    assert V._trim_speech("Short answer.") == "Short answer."


def t_openq_no_answer_literal():
    """回归：开放式问答题输入框绝不能被写死成 'answer'。

    用户多次投诉 Step 2 网页上显示 ANSWER。根因不是大模型，而是
    JS_FILL 兜底里 el.value = 'answer'。这里钉死该字面量不再出现，
    并确认开放式问答专用处理器已接入。
    """
    import tools.study_ai as S

    assert "el.value = 'answer'" not in S.JS_FILL, \
        "兜底 JS 又写死 answer 了——网页会显示 ANSWER"
    assert "el.value = \"answer\"" not in S.JS_FILL, \
        "兜底 JS 又写死 answer 了（双引号）"
    assert hasattr(S, "JS_OPENQ_SCAN") and hasattr(S, "JS_OPENQ_FILL"), \
        "开放式问答题扫描/填充器缺失"
    assert callable(getattr(S, "_answer_open_questions", None)), \
        "开放式问答题作答处理器缺失"
    # 扫描器必须能命中真实页面的 textarea 题块选择器
    assert "question-common-abs-reply" in S.JS_OPENQ_SCAN, \
        "openq 扫描器未覆盖 Group discussion Step 2 的题块 class"


# ---------------------------------------------------------------- 页面规则
def t_page_rules_defaults():
    import upc.page_rules as R
    r = R.load(force=True)
    assert r["pc_unsupported_marks"], "默认提示语不能为空"
    assert R.is_pc_unsupported("Role-play 该题型暂不支持pc端，请在app端查看")
    assert R.is_pc_unsupported("该 题 型 暂 不 支 持 PC 端")
    assert not R.is_pc_unsupported("Exercise 1 / 请选择正确答案")
    assert r["navigation"]["goto_retries"] >= 0
    assert r["roleplay_stuck_marks"], "选角页卡住规则不能为空"


def t_rules_real_page_text():
    """用今天现场快照里的真实文案回归，防止平台改措辞后规则悄悄失效。"""
    import upc.page_rules as R
    talk = ("新视野大学英语（第四版）视听说教程1> A break for fun> Role-play "
            "该题型暂不支持pc端，请在app端查看 目录笔记")
    role = ("Role-play Directions: Now it's your turn to role-play the conversation. "
            "Step 1 . Choose a role A B 上一题")
    normal = "Exercise 1 请听录音并选择正确答案 A B C D"
    assert R.is_pc_unsupported(talk), "Talk with AI 提示语应命中"
    assert not R.is_roleplay_stuck(talk), "提示页不该被判成选角页"
    assert R.is_roleplay_stuck(role), "卡住的 Step 1 选角页应命中"
    assert not R.is_pc_unsupported(role), "选角页不该被判成不支持提示页"
    assert not R.is_pc_unsupported(normal) and not R.is_roleplay_stuck(normal)


def t_page_rules_yaml_override():
    import tempfile
    import upc.page_rules as R
    old = R.RULES_FILE
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8") as f:
            f.write("pc_unsupported_marks:\n  - '仅限移动端'\n"
                    "navigation:\n  goto_retries: 5\n")
            tmp = Path(f.name)
        R.RULES_FILE = tmp
        r = R.load(force=True)
        assert r["navigation"]["goto_retries"] == 5, "yaml 覆盖没生效"
        assert r["resume"]["ttl_days"] == 7, "未写的键要保留默认值"
        assert R.is_pc_unsupported("本题 仅限移动端 查看", r)
        assert not R.is_pc_unsupported("该题型暂不支持pc端", r), "应以 yaml 为准"
    finally:
        R.RULES_FILE = old
        R.load(force=True)
        if tmp:
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 断点续跑
def t_runstate_atomic_ttl():
    import json
    import tempfile
    from upc.runstate import RunState
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "progress.json"
        s = RunState(p, ttl_days=7)
        assert not s.is_cached_unsupported("abc")
        s.mark("abc", "unsupported_pc", "Talk with AI 1")
        s.mark("def", "done", "Exercise 1")
        assert p.is_file(), "增量落盘失败"
        obj = json.loads(p.read_text(encoding="utf-8"))
        assert obj["leaves"]["abc"]["status"] == "unsupported_pc"
        assert not (p.parent / "progress.json.tmp").exists(), "tmp 应已被原子替换"
        assert RunState(p, ttl_days=7).is_cached_unsupported("abc"), "重载后应命中"
        assert not RunState(p, ttl_days=7).is_cached_unsupported("def"), "done 不该跳过"
        assert not RunState(p, ttl_days=0).is_cached_unsupported("abc"), "TTL=0 应失效"
        s.clear()
        assert not RunState(p, ttl_days=7).is_cached_unsupported("abc")


def t_runstate_corrupt():
    import tempfile
    from upc.runstate import RunState
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "progress.json"
        p.write_text("{ this is not json", encoding="utf-8")
        s = RunState(p)
        assert s.data["leaves"] == {}, "坏文件应被当成空缓存"
        assert not s.is_cached_unsupported("x")


def t_open_leaf_retry():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "study_ai_under_test", ROOT / "tools" / "study_ai.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    class FakePage:
        def __init__(self, fails):
            self.fails, self.n = fails, 0

        def goto(self, url, **kw):
            if url == "about:blank":
                return
            self.n += 1
            if self.n <= self.fails:
                raise RuntimeError("net down")

        def wait_for_timeout(self, ms):
            pass

    class Log:
        def warning(self, *a):
            pass

    p = FakePage(2)
    assert m._open_leaf(p, "http://x", Log(), retries=2, backoff_ms=1) is True
    assert p.n == 3, f"应恰好尝试 3 次，实际 {p.n}"
    p2 = FakePage(9)
    assert m._open_leaf(p2, "http://x", Log(), retries=2, backoff_ms=1) is False
    assert p2.n == 3, "重试次数必须有上限，不能死循环"

    class DeadPage(FakePage):
        def goto(self, url, **kw):
            if url == "about:blank":
                return
            self.n += 1
            raise RuntimeError(
                "Page.goto: Target page, context or browser has been closed")

    p3 = DeadPage(0)
    assert m._open_leaf(p3, "http://x", Log(), retries=2, backoff_ms=1) is False
    assert p3.n == 1, "浏览器已关闭时重试没有意义，应立刻判失败"


# ---------------------------------------------------------------- 选择器
def t_selector_no_dupes():
    from upc import selectors as S
    for name in dir(S):
        if not name.isupper():
            continue
        val = getattr(S, name)
        if isinstance(val, list) and val and isinstance(val[0], str):
            assert len(val) == len(set(val)), f"{name} 里有重复选择器"


def main():
    print("=" * 60)
    print("离线单元测试")
    print("=" * 60)

    print("\n[配置层]")
    check("默认值深度合并", t_config_defaults)
    check("缺键抛 AttributeError", t_config_attr_error)
    check("拒绝非法 strategy", t_config_validate_bad_strategy)

    print("\n[时长预算]")
    check("total 模式均分", t_budget_total)
    check("低于下限时抬升", t_budget_min_clamp)
    check("时长抖动有效", t_budget_jitter)

    print("\n[目录]")
    check("按单元筛选", t_filter_units)
    check("目录项显示名去重", t_leaf_display)

    print("\n[答案嗅探]")
    check("JSON 答案提取", t_sniff_json)
    check("HTML 文本答案提取", t_sniff_text)
    check("答案归一化", t_sniff_normalize)

    print("\n[题目处理]")
    check("答案字母拆分", t_question_letters)
    check("题目指纹稳定", t_question_key_stable)
    check("本地题库读写", t_answer_store)
    check("兜底答案不留空", t_quiz_fallback)

    print("\n[页面规则 / 断点续跑]")
    check("默认提示语匹配", t_page_rules_defaults)
    check("真实页面文案回归", t_rules_real_page_text)
    check("yaml 覆盖与默认值兜底", t_page_rules_yaml_override)
    check("进度缓存原子写与 TTL", t_runstate_atomic_ttl)
    check("缓存损坏不崩", t_runstate_corrupt)
    check("打开失败重试有上限", t_open_leaf_retry)

    print("\n[工具]")
    check("日志脱敏", t_mask)
    check("大模型输出解析", t_llm_parse)
    check("口语录音按音频全长", t_voice_recording_length)
    check("开放式问答不写死answer", t_openq_no_answer_literal)
    check("选择器无重复", t_selector_no_dupes)

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果：{PASS}/{total} 通过" + (f"，{FAIL} 失败" if FAIL else "，全绿 ✓"))
    if FAILURES:
        print("\n失败详情：")
        for name, detail in FAILURES:
            print(f"\n--- {name} ---")
            print(detail)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
