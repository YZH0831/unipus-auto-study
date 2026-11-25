# -*- coding: utf-8 -*-
"""回归测试：上传作品题（Unit project）全自动（离线，不碰浏览器）。

背景：Unit project 这类页面有个隐藏的 <input type="file">，平台只校验
「有没有作品文件」——不传就点提交会弹「还有1道题没有作答」，
于是被判定 needs_upload 永久跳过（实测 4 个单元全是这类）。

现在的固定流程：
    检测上传区 → 大模型写作品 → 零依赖 pdfgen 打 PDF
    → set_input_files 塞进隐藏控件 → 轮询等列表项 → 再走提交

验证：
  1. pdfgen 产出的字节流是合法 PDF 骨架（xref/trailer/页数）；
  2. 长文本会分页，非 ASCII 折成空格，括号被转义；
  3. upload.detect 正确识别「无上传区 / 已有作品 / 空上传区」；
  4. fill_work_upload：空上传区时投递文件并等到列表项出现 → True；
     已有作品 → 直接 True 不重复投递；无上传区 → None（不是这类题）；
  5. study_ai.py 的接入点在上传成功后仍继续提交，失败则不提交。

用法：python tests/test_upload_flow.py
"""
import ast
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc import pdfgen  # noqa: E402
from upc import upload as UP  # noqa: E402

PASS, FAIL = 0, 0
SRC = (ROOT / "tools" / "study_ai.py").read_text(encoding="utf-8")


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


class FakePage:
    """够用的假页面：state 列表按调用次数依次返回。"""

    def __init__(self, states, items_after=1):
        self.states = list(states)
        self.items_after = items_after
        self.calls = 0
        self.files = []

    def evaluate(self, js, *a):
        if "question-multi-file-upload" in js and "items" in js:
            idx = min(self.calls, len(self.states) - 1)
            self.calls += 1
            return dict(self.states[idx])
        return {}

    def set_input_files(self, sel, path):
        self.files.append((sel, path))

    def wait_for_timeout(self, ms):
        pass


def t_pdf_skeleton():
    b = pdfgen.build_pdf_bytes("Hello world")
    assert b.startswith(b"%PDF-1.4"), "缺少 PDF 头"
    assert b.rstrip().endswith(b"%%EOF"), "缺少 EOF"
    assert b"startxref" in b and b"xref" in b
    assert b"/Type /Catalog" in b and b"/Type /Font" in b
    assert b"/BaseFont /Helvetica" in b


def t_pdf_pagination():
    one = pdfgen.build_pdf_bytes("short line")
    many = pdfgen.build_pdf_bytes("\n".join(["line"] * 400))
    assert one.count(b"/Type /Page ") == 1
    assert many.count(b"/Type /Page ") >= 3, "长文本应分多页"


def t_pdf_escape_and_ascii():
    b = pdfgen.build_pdf_bytes("a(b)c 中文 test")
    assert rb"a\(b\)c" in b, "括号未转义"
    assert "中文".encode("utf-8") not in b, "非 ASCII 应被折掉"
    assert b"test" in b


def t_detect_kinds():
    assert UP.detect(FakePage([{"has": False}]))["has"] is False
    got = UP.detect(FakePage([{"has": True, "input": True, "items": 2}]))
    assert got["has"] and got["items"] == 2


def t_upload_ok():
    pg = FakePage([{"has": True, "input": True, "items": 0},
                   {"has": True, "input": True, "items": 0},
                   {"has": True, "input": True, "items": 1}])
    with tempfile.TemporaryDirectory() as td:
        r = UP.fill_work_upload(pg, FakeLog(), {"id": "leaf1",
                                                "name": "Unit project"},
                                "Write a letter.", cli=None, out_dir=td,
                                max_wait_s=8)
        assert r is True, f"应成功，实际 {r}"
        assert pg.files and pg.files[0][1].endswith(".pdf")
        assert Path(pg.files[0][1]).exists(), "作品文件应真实存在"
        assert "question-multi-file-upload" in pg.files[0][0]


def t_upload_skip_existing():
    pg = FakePage([{"has": True, "input": True, "items": 3}])
    r = UP.fill_work_upload(pg, FakeLog(), {"id": "x"}, "", out_dir=".")
    assert r is True and not pg.files, "已有作品时不应重复投递"


def t_not_upload_page():
    pg = FakePage([{"has": False}])
    assert UP.fill_work_upload(pg, FakeLog(), {"id": "y"}, "") is None


def t_upload_fail_no_input():
    pg = FakePage([{"has": True, "input": False, "items": 0}])
    assert UP.fill_work_upload(pg, FakeLog(), {"id": "z"}, "") is False


def t_wired_before_submit():
    i_up = SRC.find("②b 上传作品题")
    i_sub = SRC.find("③ 提交 + 判分闭环")
    assert i_up > 0 and i_sub > i_up, "上传必须发生在提交之前"
    seg = SRC[i_up:i_sub]
    assert "fill_work_upload" in seg
    assert "needs_upload" in seg, "上传失败应标 needs_upload 且不提交"
    assert ast.parse(SRC)


def main():
    print("=" * 56)
    print("测试：上传作品题全自动（离线）")
    print("=" * 56)
    check("PDF 骨架合法", t_pdf_skeleton)
    check("长文本自动分页", t_pdf_pagination)
    check("括号转义 / 非 ASCII 折掉", t_pdf_escape_and_ascii)
    check("detect 区分三种状态", t_detect_kinds)
    check("空上传区 → 投递并成功", t_upload_ok)
    check("已有作品 → 不重复投递", t_upload_skip_existing)
    check("非上传题 → None", t_not_upload_page)
    check("找不到 file 控件 → False", t_upload_fail_no_input)
    check("接入点在提交之前且失败不提交", t_wired_before_submit)
    print("=" * 56)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全绿 ✓" if not FAIL else "，有失败 ✗"))
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
