# -*- coding: utf-8 -*-
"""一次性测试：用已知正确的学号姓名走一遍「U校园认证」，判断是填写问题还是数据问题。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc import portal as P          # noqa: E402
from upc import selectors as S       # noqa: E402
from upc.browser import BrowserSession   # noqa: E402
from upc.config import load_config       # noqa: E402
from upc.humanize import Humanizer       # noqa: E402
from upc.logutil import setup_logging    # noqa: E402
from upc.popups import PopupGuard        # noqa: E402


def main():
    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False
    log = setup_logging(ROOT / "logs", "INFO")
    h = Humanizer(cfg, log)

    session = BrowserSession(cfg, log)
    try:
        page = session.page
        log.info("打开门户…")
        session.goto(S.HOME_URL, context="门户")
        page.wait_for_timeout(3500)
        log.info(f"当前页面：{page.url[:100]}")

        popup = PopupGuard(cfg, log, h)
        # 先看有没有"选择学校"
        if P.school_modal_present(page):
            log.info(">>> 有「选择学校」弹窗，自动选")
            P.handle_school_select(session, cfg, log, h)
            page.wait_for_timeout(2000)
        else:
            log.info(">>> 没有「选择学校」弹窗")

        # 再看有没有"学号认证"
        if P.student_modal_present(page):
            log.info(">>> 有「U校园认证」弹窗，用配置里的学号姓名自动填")
            ok = P.handle_student_verify(session, cfg, log, h)
            log.info(f">>> 认证结果：{'成功' if ok else '失败'}")
        else:
            log.info(">>> 没有「U校园认证」弹窗")

        page.wait_for_timeout(2500)
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        page.screenshot(path=str(out / "verify_result.png"))
        log.info(f"结果截图：{out / 'verify_result.png'}")
        log.info(f"最终页面：{page.url[:120]}")

        # 把页面文本前半部分打出来，便于判断
        try:
            txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            log.info("页面文本片段：")
            for line in [x.strip() for x in txt.splitlines() if x.strip()][:25]:
                log.info(f"   | {line[:60]}")
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1500)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
