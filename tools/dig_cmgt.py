# -*- coding: utf-8 -*-
"""挖 course-management 微前端的接口清单。

`unicloud.unipus.cn/coursemgt/umicro/cmgt/cmgt.html` 是课程管理的微应用，
它的 JS 里写死了课程详情 / 任务点 / 学习进度的接口路径。
把这些路径挖出来，就知道"刷时长"该调哪个接口。

用法：python tools/dig_cmgt.py
结果：output/cmgt_api_paths.txt
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENTRY = "https://unicloud.unipus.cn/coursemgt/umicro/cmgt/cmgt.html"
COURSE_PAGE = "https://uai.unipus.cn/app/cmgt/course-management"
KEYS = ("course", "unit", "point", "task", "study", "learn", "duration",
        "progress", "record", "content", "activity", "heartbeat",
        "section", "lesson", "resource", "tutorial", "studyrecord", "point")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    import json

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    out = Path(cfg.output_dir)

    session = BrowserSession(cfg, log)
    page = session.page
    try:
        # 先注入 API cookie 才能进课程应用
        sess_file = Path(cfg.state_dir) / "api_session.json"
        if sess_file.exists():
            sess = json.loads(sess_file.read_text(encoding="utf-8"))
            to_add = [{"name": c["name"], "value": c["value"],
                       "domain": c.get("domain") or "",
                       "path": c.get("path") or "/"}
                      for c in (sess.get("cookies") or []) if c.get("domain")]
            try:
                session.context.add_cookies(to_add)
                print(f"注入 {len(to_add)} 个 cookie")
            except Exception as exc:  # noqa: BLE001
                print("注入失败:", str(exc)[:100])

        print(f"打开微前端入口: {ENTRY}")
        session.goto(ENTRY, context="cmgt")
        page.wait_for_timeout(4000)

        # 收集所有 script 与 link
        scripts = page.evaluate(r"""
        () => {
          const out = new Set();
          document.querySelectorAll('script[src]').forEach(s => out.add(s.src));
          document.querySelectorAll('link[rel="modulepreload"], link[rel="preload"]')
            .forEach(l => { if (l.href) out.add(l.href); });
          return Array.from(out);
        }
        """) or []
        print(f"发现 {len(scripts)} 个脚本资源")

        blob_parts = []
        for src in scripts[:60]:
            try:
                t = page.evaluate(
                    "async (u) => { const r = await fetch(u); return await r.text(); }",
                    src)
                if t:
                    blob_parts.append(t)
                    print(f"  读取 {src.split('/')[-1][:50]}  ({len(t)} 字符)")
            except Exception as exc:  # noqa: BLE001
                print(f"  失败 {src[:70]}: {str(exc)[:50]}")

        blob = "\n".join(blob_parts)
        print(f"脚本总长度 {len(blob)}")

        paths = set()
        for pat in (r'["\'`](/api/[A-Za-z0-9_\-./{}$:]{2,140})',
                    r'["\'`](https://uai\.unipus\.cn/api/[A-Za-z0-9_\-./{}$:]{2,140})',
                    r'["\'`](https://[a-z.]*unipus[a-z.]*/[A-Za-z0-9_\-./{}$:]{2,140})'):
            for m in re.finditer(pat, blob):
                p = m.group(1)
                if p.startswith("https://uai.unipus.cn"):
                    p = p[len("https://uai.unipus.cn"):]
                paths.add(p)

        all_paths = sorted(paths)
        interesting = [p for p in all_paths if any(k in p.lower() for k in KEYS)]
        print(f"\n共 {len(all_paths)} 个路径，其中课程/学习相关 {len(interesting)} 个：")
        for p in interesting:
            print("  ", p)

        (out / "cmgt_api_paths.txt").write_text(
            "# 全部\n" + "\n".join(all_paths) +
            "\n\n# 课程/学习相关\n" + "\n".join(interesting), encoding="utf-8")
        print(f"\n已保存 {out / 'cmgt_api_paths.txt'}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
