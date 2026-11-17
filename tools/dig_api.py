# -*- coding: utf-8 -*-
"""从前端 JS 里提取 AI 版的全部 API 路径。

SPA 的所有接口路径都硬编码在打包后的 JS 里。抓下来正则扫一遍，
比一个个点页面去猜快得多。

用法：python tools/dig_api.py
结果：output/ai_api_paths.txt
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "https://uai.unipus.cn/portal"

# 只关心这些关键词的路径
KEYS = ("course", "unit", "study", "learn", "duration", "progress", "task",
        "record", "content", "activity", "heartbeat", "lesson", "section",
        "chapter", "score", "answer", "exam", "homework", "book")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    session = BrowserSession(cfg, log)
    page = session.page
    try:
        session.goto(PORTAL, context="portal")
        page.wait_for_timeout(6000)

        # 收集页面里所有 script src + 内联脚本
        scripts = page.evaluate(r"""
        () => Array.from(document.querySelectorAll('script[src]'))
              .map(s => s.src).filter(Boolean)
        """) or []
        print(f"发现 {len(scripts)} 个外部脚本")

        texts = []
        for src in scripts[:40]:
            try:
                t = page.evaluate(
                    "async (u) => { const r = await fetch(u); return await r.text(); }",
                    src)
                if t:
                    texts.append(t)
            except Exception as exc:  # noqa: BLE001
                print(f"  读取失败 {src[:80]}: {str(exc)[:60]}")

        # 内联脚本
        try:
            inline = page.evaluate(
                "() => Array.from(document.querySelectorAll('script:not([src])'))"
                ".map(s => s.textContent).join('\\n')")
            if inline:
                texts.append(inline)
        except Exception:  # noqa: BLE001
            pass

        blob = "\n".join(texts)
        print(f"脚本总长度 {len(blob)}")

        # 提取 /api/... 路径
        paths = set()
        for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-./{}$:]{2,120})["\'`]', blob):
            paths.add(m.group(1))
        for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-./{}$:]{2,120})', blob):
            paths.add(m.group(1))

        # 也扫一下完整域名形式
        for m in re.finditer(r'https://uai\.unipus\.cn(/api/[A-Za-z0-9_\-./{}$:]{2,120})', blob):
            paths.add(m.group(1))

        all_paths = sorted(paths)
        print(f"提取到 {len(all_paths)} 个 API 路径")

        interesting = [p for p in all_paths
                       if any(k in p.lower() for k in KEYS)]
        print(f"其中与课程/学习相关 {len(interesting)} 个：")
        for p in interesting:
            print("  ", p)

        (out / "ai_api_paths.txt").write_text(
            "# 全部提取到的 API 路径\n" + "\n".join(all_paths)
            + "\n\n# 课程/学习相关\n" + "\n".join(interesting),
            encoding="utf-8")
        print(f"\n已保存: {out / 'ai_api_paths.txt'}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
