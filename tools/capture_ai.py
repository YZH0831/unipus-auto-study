# -*- coding: utf-8 -*-
"""抓取 uai.unipus.cn/portal（AI版）的真实接口调用。

思路：AI 版既然能用你的账号登录，那它一定有一套不依赖「学校学籍认证」的接口。
用真实浏览器打开 portal，记录所有 XHR/fetch 的 URL、方法、请求体、响应片段，
就能逆推出 AI 版的课程/学习时长接口，从而摆脱网页认证墙。

用法：python tools/capture_ai.py [--timeout 900]
抓取结果：output/ai_api_log.json + output/ai_last.png
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "https://uai.unipus.cn/portal"

# 只记录这些"像接口"的请求，避免图片/css 刷屏
API_HINTS = ("api", "ajax", "graphql", "rpc", "query", "list", "detail",
             "course", "unit", "study", "learn", "duration", "progress",
             "task", "record", "user", "login", "token")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".ico", ".map")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()

    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    cfg.login.reuse_state = True
    log = setup_logging(ROOT / "logs", "WARNING")

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = out_dir / "ai_status.log"
    try:
        status.unlink()
    except Exception:  # noqa: BLE001
        pass

    def say(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        try:
            with open(status, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    session = BrowserSession(cfg, log)
    page = session.page
    records = []
    seen = set()

    def _on_response(resp):
        try:
            url = resp.url or ""
            low = url.lower()
            if any(low.endswith(e) for e in SKIP_EXT):
                return
            if not any(h in low for h in API_HINTS):
                return
            key = f"{resp.request.method} {url.split('?')[0]}"
            ctype = (resp.headers or {}).get("content-type", "") or ""
            item = {
                "method": resp.request.method,
                "url": url[:300],
                "status": resp.status,
                "ctype": ctype[:40],
                "post": (resp.request.post_data or "")[:800] if resp.request.method != "GET" else "",
            }
            # 响应体只留 JSON/短文本的前段
            if "json" in ctype or "text" in ctype:
                try:
                    body = resp.text()
                    if body and len(body) < 60000:
                        item["resp"] = body[:1500]
                except Exception:  # noqa: BLE001
                    pass
            records.append(item)
            sig = (key, item["status"], item.get("resp", "")[:80])
            if sig not in seen:
                seen.add(sig)
                say(f"API {item['method']} {url[:110]} → {item['status']}")
        except Exception:  # noqa: BLE001
            pass

    page.on("response", _on_response)

    try:
        say(f"打开 {PORTAL}")
        session.goto(PORTAL, context="AI版portal")
        page.wait_for_timeout(3000)
        say(f"当前标题：{page.title()[:60]}")
        say("=" * 60)
        say("请在浏览器里：① 登录（如果还没登录）")
        say("             ② 进入你的课程 / 点开一个学习页面")
        say("我会持续记录接口，你点得越全越好。")
        say("=" * 60)

        deadline = time.time() + args.timeout
        last_dump = 0.0
        while time.time() < deadline:
            time.sleep(3)
            now = time.time()
            # 每 20 秒落盘一次 + 截一张图，便于中途查看
            if now - last_dump > 20:
                last_dump = now
                try:
                    (out_dir / "ai_api_log.json").write_text(
                        json.dumps({"updated": datetime.now().isoformat(timespec="seconds"),
                                    "url": page.url, "count": len(records),
                                    "records": records},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.screenshot(path=str(out_dir / "ai_last.png"), timeout=8000)
                except Exception:  # noqa: BLE001
                    pass

        say(f"抓取结束，共 {len(records)} 条接口记录")
    except Exception as exc:  # noqa: BLE001
        say(f"出错：{exc}")
    finally:
        try:
            (out_dir / "ai_api_log.json").write_text(
                json.dumps({"updated": datetime.now().isoformat(timespec="seconds"),
                            "url": page.url, "count": len(records), "records": records},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            say("结果已保存 output/ai_api_log.json")
        except Exception:  # noqa: BLE001
            pass
        try:
            session.save_state()
        except Exception:  # noqa: BLE001
            pass
        if not args.keep_open:
            session.close()
        say("DONE")


if __name__ == "__main__":
    raise SystemExit(main())
