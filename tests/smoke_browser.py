# -*- coding: utf-8 -*-
"""浏览器连通性冒烟测试（不需要登录）。

验证：Playwright 能拉起本机 Edge/Chrome、能打开 U校园、会话探测逻辑正常。
用法：python tests/smoke_browser.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc.browser import BrowserSession          # noqa: E402
from upc.config import load_config              # noqa: E402
from upc.logutil import setup_logging           # noqa: E402
from upc import menu as M                       # noqa: E402
from upc import selectors as S                  # noqa: E402


def main():
    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True            # 冒烟测试不开窗口
    cfg.state_dir = str(ROOT / "state")
    log = setup_logging(ROOT / "logs", "WARNING")
    print("=" * 60)
    print("浏览器冒烟测试")
    print("=" * 60)

    session = None
    ok = True
    try:
        print("[1] 启动浏览器…", end=" ", flush=True)
        session = BrowserSession(cfg, log)
        print(f"OK  channel={session._channel_used}")

        print("[2] 打开 U校园首页…", end=" ", flush=True)
        if session.goto(S.HOME_URL, context="首页"):
            print(f"OK  {session.page.url[:70]}")
        else:
            print("失败")
            ok = False

        print("[3] 会话探测…", end=" ", flush=True)
        status = session.probe_session()
        print(f"{status}（未登录时应为 logged_out）")

        print("[4] 目录探测脚本…", end=" ", flush=True)
        present = M.menu_present(session.page)
        print(f"menu_present={present}（首页没有目录是正常的）")

        print("[5] 页面结构读取…", end=" ", flush=True)
        title = session.page.title()
        n_frames = len(session.page.frames)
        print(f"title={title[:40]!r}  frames={n_frames}")

        print("[6] 选择器工具函数…", end=" ", flush=True)
        el = S.first_in(session.page, ["body"], timeout_s=3)
        print("OK" if el is not None else "失败")
        if el is None:
            ok = False

        print()
        print("=" * 60)
        print("结论：" + ("冒烟测试通过，浏览器链路正常。" if ok else "有步骤失败，见上。"))
        print("=" * 60)
        return 0 if ok else 1
    finally:
        if session:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
