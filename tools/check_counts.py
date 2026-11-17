# -*- coding: utf-8 -*-
"""读平台侧硬指标：已完成任务点数 / 蓝圈数，用于判定加速行为是否被计入。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc import progress as PG  # noqa: E402


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])

        # ① 平台侧任务点计数
        s.goto("https://uai.unipus.cn/app/cmgt/course-management", context="课程管理")
        p.wait_for_timeout(4000)
        try:
            r = s.context.request.get(
                "https://uai.unipus.cn/api/cmgt/course/getCourseListByStudent",
                timeout=25000)
            cl = r.json()
        except Exception as exc:  # noqa: BLE001
            print("课程列表读取失败:", exc)
            cl = {}
        print("=== 平台侧 finishPointNum ===")
        for c in ((cl.get("value") or {}).get("courseList") or []):
            for res in (c.get("courseResourceList") or []):
                print(f"  {c.get('name')} / {res.get('name')}: "
                      f"{res.get('finishPointNum')}/{res.get('totalPointNum')} "
                      f"readProcess={res.get('readProcess')}")

        # ② 课件目录蓝圈数
        url = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               "?cid=1847183133920289074&appid=116#/"
               "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205/courseware/"
               "71e31dc39002af7/71e31df040024c9/71e31e2c1002af7/71e31e4d20024c9")
        p.goto(url, wait_until="domcontentloaded", timeout=50000)
        p.wait_for_timeout(10000)
        rows = PG.scan(p)
        done = [r for r in rows if r["done"]]
        print(f"\n=== 课件目录蓝圈 ===")
        print(f"  节点 {len(rows)} 个，蓝圈 {len(done)} 个")
        for r in done:
            print(f"    [{r['i']:>2}] {r['name'][:40]}")
        print("\n  最近 10 个节点状态:")
        for r in rows[:10]:
            print(f"    [{r['i']:>2}] {r['state']:<5} {r['name'][:38]}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
