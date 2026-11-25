# -*- coding: utf-8 -*-
"""零依赖 PDF 生成（只处理纯英文作品，够 Unit project 上传用）。

为什么不装 reportlab：项目一直保持「只依赖 playwright/PyYAML」，而且
沙箱网络对 pip 不友好；手写一个 Helvetica 单字体、自动换行分页的
PDF 只要一百来行，够用且不会有额外依赖风险。

限制（有意为之）：
  - 只支持 ASCII 可见字符（U校园的作品要求都是英文写作，足够）；
    非 ASCII 一律折成空格，避免字体缺字形出现豆腐块。
  - 单字体 Helvetica、固定 11pt、A4。
"""
from __future__ import annotations

import textwrap
from pathlib import Path

PAGE_W, PAGE_H = 595, 842          # A4，单位 pt
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 62, 72, 66
FONT_SIZE, LEADING = 11.0, 15.6
WRAP_COLS = 88                     # Helvetica 11pt 在 471pt 宽里约 88 字符


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _ascii(s: str) -> str:
    out = []
    for ch in s:
        if ch in ("\n", "\t"):
            out.append(ch)
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        elif ord(ch) > 127:
            out.append(" ")
        else:
            out.append(" ")
    return "".join(out)


def _layout(text: str, wrap_cols: int = WRAP_COLS) -> list:
    """按段落折行，空行保留。"""
    lines = []
    for para in _ascii(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        para = para.rstrip()
        if not para.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=wrap_cols,
                                  break_long_words=True,
                                  break_on_hyphens=False) or [""])
    return lines


def build_pdf_bytes(text: str) -> bytes:
    usable_h = PAGE_H - MARGIN_TOP - MARGIN_BOT
    per_page = max(1, int(usable_h // LEADING))
    lines = _layout(text)
    if not lines:
        lines = [""]
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)]

    # 对象编号：1 Catalog / 2 Pages / 3 Font / 之后每页两对象（Page + Contents）
    objs = {}
    kids = []
    for pi, page_lines in enumerate(pages):
        page_no = 4 + pi * 2
        cont_no = page_no + 1
        kids.append(f"{page_no} 0 R")

        ops = ["BT", f"/F1 {FONT_SIZE} Tf", f"{LEADING} TL",
               f"{MARGIN_X} {PAGE_H - MARGIN_TOP} Td"]
        for ln in page_lines:
            ops.append(f"({_esc(ln)}) Tj")
            ops.append("T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")

        objs[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cont_no} 0 R >>"
        ).encode("latin-1")
        objs[cont_no] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream + b"\nendstream"
        )

    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = (f"<< /Type /Pages /Kids [{' '.join(kids)}] "
               f"/Count {len(pages)} >>").encode("latin-1")
    objs[3] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for no in sorted(objs):
        offsets[no] = len(out)
        out += f"{no} 0 obj\n".encode("latin-1")
        out += objs[no]
        out += b"\nendobj\n"

    xref_at = len(out)
    total = max(objs) + 1
    out += f"xref\n0 {total}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for no in range(1, total):
        off = offsets.get(no, 0)
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {total} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode("latin-1")
    return bytes(out)


def write_pdf(text: str, path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(build_pdf_bytes(text))
    return p
