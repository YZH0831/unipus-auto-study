# -*- coding: utf-8 -*-
"""录制「手动完成一个任务点」时的全部请求，逆向出真正让白圈变蓝的动作。

用法：
    python tools/capture_complete.py --micro 2
    （浏览器打开该 micro 的第一个叶子后，请你手动做一遍让它变蓝的操作，
      例如真实看完视频 / 正确答题 / 点某个按钮）
    Ctrl+C 或等 5 分钟结束，结果存 output/complete_trace.json

结束时会对比蓝圈前后状态，并高亮"变蓝前后 10 秒内"的请求。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map", ".webp", ".mp4", ".mp3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--micro", default="2", help="micro 序号或名字关键词")
    ap.add_argument("--seconds", type=int, default=300)
    args = ap.parse_args()

    from upc import progress as PG
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "INFO")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    out = Path(cfg.output_dir)

    s = BrowserSession(cfg, log)
    p = s.page
    records = []
    blue_events = []

    def on_resp(r):
        try:
            u = r.url or ""
            low = u.lower()
            if any(low.endswith(e) or e in low for e in SKIP_EXT):
                return
            if "unipus" not in low:
                return
            item = {"t": round(time.time(), 2), "m": r.request.method,
                    "u": u[:400], "s": r.status,
                    "post": (r.request.post_data or "")[:1500]}
            ct = (r.headers or {}).get("content-type", "") or ""
            if "json" in ct:
                try:
                    item["resp"] = (r.text() or "")[:3000]
                except Exception:  # noqa: BLE001
                    pass
            records.append(item)
            log.info(f"{item['m']} {u[:100]} → {item['s']}")
        except Exception:  # noqa: BLE001
            pass

    p.on("response", on_resp)
    s.context.on("page", lambda pg: pg.on("response", on_resp))

    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])

        s.goto("https://uai.unipus.cn/app/cmgt/course-management", context="课程管理")
        p.wait_for_timeout(4000)

        inst = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
        cid = "1847183133920289074"
        # 拿到结构树，定位目标 micro 的第一个叶子
        p.goto("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid={cid}&appid=116#/{inst}/courseware/"
               "71e31dc39002af7/71e31df040024c9/71e31e2c1002af7/71e31e4d20024c9",
               wait_until="domcontentloaded", timeout=50000)
        p.wait_for_timeout(7000)

        # 直接复用 study_ai 的结构解析
        sys.path.insert(0, str(ROOT / "tools"))
        from study_ai import build_leaves
        raw = p.evaluate(r"""
        async (inst) => {
          const r = await fetch(`/course/api/course/${inst}/default`,
                                {credentials:'include'});
          return await r.json();
        }
        """, inst)
        course_str = (raw or {}).get("course")
        struct = json.loads(course_str) if isinstance(course_str, str) else course_str
        leaves, micros = build_leaves(struct)

        want = args.micro.strip()
        idx = None
        if want.isdigit() and int(want) < len(micros):
            idx = int(want)
        else:
            for j, m in enumerate(micros):
                if want in m:
                    idx = j
                    break
        if idx is None:
            log.error(f"找不到 micro {want}")
            return 1

        mine = [l for l in leaves if l.get("micro_idx") == idx]
        log.info(f"目标 micro [{idx}] {micros[idx]}，共 {len(mine)} 个叶子")
        target = mine[0]
        url = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
               f"?cid={cid}&appid=116#/{inst}/courseware/{target['path']}")
        log.info("=" * 62)
        log.info(f"已打开：{target['name']}")
        log.info("请在这个窗口里【手动】做一遍能让它变蓝的操作")
        log.info("（真实看视频 / 正确答题 / 点提交 …）")
        log.info("=" * 62)
        p.goto("about:blank", timeout=15000)
        p.goto(url, wait_until="domcontentloaded", timeout=50000)
        p.wait_for_timeout(6000)

        before = PG.micro_done(p, idx)
        log.info(f"开始状态：{'蓝圈' if before else '白圈'}")
        deadline = time.time() + args.seconds
        flipped_at = None
        last_n = 0
        while time.time() < deadline:
            time.sleep(2)
            now = PG.micro_done(p, idx)
            if now and not before and flipped_at is None:
                flipped_at = time.time()
                log.info("★ 检测到白圈变蓝！继续录 15 秒…")
                blue_events.append(flipped_at)
                deadline = min(deadline, flipped_at + 15)
            if len(records) - last_n >= 5:
                last_n = len(records)
                (out / "complete_trace.json").write_text(json.dumps(
                    {"micro": micros[idx], "idx": idx, "flipped_at": flipped_at,
                     "before": before, "after": now,
                     "requests": records}, ensure_ascii=False, indent=2),
                    encoding="utf-8")

        after = PG.micro_done(p, idx)
        result = {"micro": micros[idx], "idx": idx,
                  "before": before, "after": after,
                  "flipped_at": flipped_at, "requests": records}
        (out / "complete_trace.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("=" * 62)
        log.info(f"结束：{'白圈变蓝 ✓' if after and not before else '未变蓝'}")
        log.info(f"共录制 {len(records)} 个请求 → output/complete_trace.json")
        if flipped_at:
            log.info("变蓝前后 10 秒的请求：")
            for r in records:
                if abs(r["t"] - flipped_at) <= 10:
                    log.info(f"  {r['m']} {r['u'][:110]} → {r['s']}")
        log.info("=" * 62)
        return 0
    finally:
        try:
            s.save_state()
        except Exception:  # noqa: BLE001
            pass
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
