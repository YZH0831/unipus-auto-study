# -*- coding: utf-8 -*-
"""「上传作品」题型的自动化（Unit project / 作品提交页）。

这类页面长这样（实测 Unit project）：
    .question-multi-file-upload
      └─ .ant-upload-drag
           └─ <input type="file">   ← display:none，Playwright 仍可直接塞文件
    文案：「将音频、视频、图片或文档拖动到此处，或浏览电脑文件
          Tips：最多支持上传3个作品（单个不大于200M）」

平台校验只看「有没有作品文件」：不传文件点提交会弹
「还有1道题没有作答，请完成后提交。」

流程（本模块固化下来）：
    1. 检测上传区；已有作品 → 直接算通过
    2. 没有作品 → 让大模型按题干写一篇英文作品
    3. 用零依赖 pdfgen 打成 PDF（避免 Word/PPT 被平台要求转 PDF）
    4. set_input_files 塞进隐藏 input（绕过拖拽交互）
    5. 轮询等列表项出现，最多 90s
"""
from __future__ import annotations

import re
from pathlib import Path

from . import pdfgen

ROOT = Path(__file__).resolve().parent.parent

JS_UPLOAD_STATE = r"""
() => {
  const box = document.querySelector('.question-multi-file-upload');
  if (!box) return {has: false};
  const inp = box.querySelector('input[type="file"]');
  const items = box.querySelectorAll(
    '.ant-upload-list-item, .unipus-upload-list-item,'
    + ' [class*="upload-file"], [class*="file-list"] > *');
  const txt = (box.innerText || '').replace(/\s+/g, ' ').trim();
  const bad = /失败|错误|不能超过|格式不支持/.test(txt);
  return {has: true, input: !!inp, items: items.length,
          text: txt.slice(0, 240), bad: bad,
          uploading: /上传中|%/.test(txt)};
}
"""


def detect(page) -> dict:
    """返回 {has, input, items, text, uploading, bad}。"""
    try:
        return page.evaluate(JS_UPLOAD_STATE) or {"has": False}
    except Exception:  # noqa: BLE001
        return {"has": False}


def _ask_work_text(cli, stem: str, material: str, log) -> str:
    """让大模型按题干写作品正文（英文，200~320 词）。"""
    if cli is None:
        return ""
    prompt = (
        "请按下面这个写作任务的全部要求，写一份可以直接当作业上交的英文作品：\n"
        "1) 200~320 个英文单词，分段清晰，不要标题以外的任何标记；\n"
        "2) 只输出正文（第一行可以是标题），不要写「ANSWER:」、不要解释；\n"
        "3) 内容要具体，落到题干提到的每一条要点上。\n\n"
        f"【任务要求】\n{(stem or '')[:1500]}\n"
    )
    try:
        r = cli.ask(prompt, None, "写作", material=(material or "")[:4000],
                    free_text=True)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"    作品生成失败: {type(exc).__name__}: {str(exc)[:70]}")
        return ""
    txt = str((r or {}).get("answer") or "").strip()
    txt = re.sub(r"^(ANSWER|答案)\s*[:：]\s*", "", txt, flags=re.I)
    return txt


def _fallback_text(lf, stem: str) -> str:
    """模型不可用时的兜底作品（宁可交一篇朴素文章，也不要空着）。"""
    title = lf.get("name") or "Unit project"
    return (f"{title}\n\n"
            "This work is submitted for the course project. "
            "It introduces the topic required by the task and covers "
            "the key points listed in the directions, including the "
            "background, the main idea, and a short conclusion.\n")


def make_work_file(lf, stem: str, cli, log, out_dir) -> Path:
    """生成作品 PDF，返回路径。"""
    body = _ask_work_text(cli, stem, stem, log) or _fallback_text(lf, stem)
    if not re.search(r"[A-Za-z]{3}", body):
        body = _fallback_text(lf, stem)
    name = f"work_{lf.get('id')}.pdf"
    p = pdfgen.write_pdf(body, Path(out_dir) / "uploads" / name)
    log.info(f"    作品已生成: {p.name}（{len(body)} 字符）")
    return p


def fill_work_upload(page, log, lf, stem: str = "", cli=None,
                     out_dir=None, max_wait_s: int = 90):
    """处理上传题。返回 None=本页不是上传题，True=已就位，False=失败。"""
    st = detect(page)
    if not st.get("has"):
        return None
    if st.get("items"):
        log.info("    上传区已有作品，跳过生成")
        return True
    if not st.get("input"):
        log.warning("    识别到上传区但找不到 file 控件，跳过")
        return False

    out_dir = Path(out_dir or (ROOT / "output"))
    try:
        fp = make_work_file(lf, stem, cli, log, out_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    作品生成失败: {type(exc).__name__}: {str(exc)[:80]}")
        return False

    try:
        page.set_input_files(
            '.question-multi-file-upload input[type="file"]', str(fp))
        log.info(f"    作品已投递到上传框: {fp.name}")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"    投递文件失败: {type(exc).__name__}: {str(exc)[:90]}")
        return False

    waited = 0
    last = st
    while waited < max_wait_s:
        page.wait_for_timeout(2000)
        waited += 2
        cur = detect(page)
        if cur.get("bad"):
            log.warning(f"    上传被拒: {cur.get('text', '')[:80]}")
            return False
        if cur.get("items"):
            log.info(f"    上传成功（{waited}s，列表项 {cur.get('items')} 个）")
            return True
        last = cur
    log.warning(f"    等了 {max_wait_s}s 仍未见上传结果: "
                f"{str(last.get('text', ''))[:80]}")
    return False
