# -*- coding: utf-8 -*-
"""U校园自动化助手 — 命令行入口。

用法：
    python main.py                        # 正式运行
    python main.py --dry-run              # 只扫描目录结构，不实际学习
    python main.py --only video           # 只播视频
    python main.py --only quiz            # 只做单元测试
    python main.py --units 1-3            # 只学 Unit 1~3
    python main.py --pick                 # 交互勾选单元
    python main.py --list                 # 只列目录
    python main.py --version
"""
import argparse
import re


def parse_args():
    p = argparse.ArgumentParser(
        description="U校园（Unipus）课程自动化助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="store_true", help="打印版本号后退出")
    p.add_argument("--config", default="config.yaml", help="配置文件路径")
    p.add_argument("--dry-run", action="store_true", help="只扫描目录结构，不实际学习")
    p.add_argument("--list", action="store_true", help="只列出目录项后退出")
    p.add_argument("--pick", action="store_true", help="交互勾选要学习的单元")
    p.add_argument("--units", default="", help='只学指定单元，如 "1-3,5"')
    p.add_argument("--url", default="", help="覆盖配置里的课程地址")
    p.add_argument("--only", default="", choices=["", "video", "quiz", "speaking", "dwell"],
                   help="只执行指定类型的动作")
    p.add_argument("--minutes", type=float, default=0,
                   help="覆盖 duration.total_minutes（本次运行的总时长）")
    p.add_argument("--headless", action="store_true", help="无界面运行（登录/刷时长不建议）")
    p.add_argument("--attach", action="store_true",
                   help="接管已开着的浏览器（需先带 --remote-debugging-port=9222 启动）")
    return p.parse_args()


def _apply_overrides(cfg, args):
    if args.url:
        cfg.course.url = args.url
    if args.units:
        cfg.course.units = args.units
    if args.pick:
        cfg.course.pick_interactively = True
    if args.headless:
        cfg.browser.headless = True
    if args.attach:
        cfg.browser.mode = "attach"
    if args.minutes:
        cfg.duration.total_minutes = float(args.minutes)
        if cfg.duration.mode != "total":
            cfg.duration.mode = "total"

    if args.only:
        if args.only == "video":
            cfg.video.enabled = True
            cfg.quiz.enabled = False
            cfg.speaking.enabled = False
        elif args.only == "quiz":
            cfg.video.enabled = False
            cfg.quiz.enabled = True
        elif args.only == "speaking":
            cfg.video.enabled = False
            cfg.quiz.enabled = False
            cfg.speaking.enabled = True
        elif args.only == "dwell":
            cfg.video.enabled = False
            cfg.quiz.enabled = False
            cfg.speaking.enabled = False


def main():
    args = parse_args()
    if args.version:
        from upc.version import __release__, __title__, __version__
        print(f"{__title__} v{__version__}（{__release__}）")
        return 0

    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(args.config)
    _apply_overrides(cfg, args)
    log = setup_logging(cfg.log_dir, cfg.log_level)

    # ---- 只列目录 ----
    if args.list or args.dry_run:
        return _list_only(cfg, log)

    from upc.runner import Runner
    return Runner(cfg, log, args).run()


def _list_only(cfg, log):
    """登录 → 进课程 → 打印目录结构。"""
    from upc import login as L
    from upc import menu as M
    from upc.browser import BrowserSession
    from upc.humanize import Humanizer

    session = BrowserSession(cfg, log)
    try:
        if not L.ensure_login(session, cfg, log):
            log.error("登录未完成")
            return 1
        page = session.page
        url = cfg.course.url
        if url:
            if not session.goto(url, context="课程页"):
                return 1
            page.wait_for_timeout(3000)
        leaves = M.discover_leaves(page, log, Humanizer(cfg, log))
        if not leaves:
            log.warning("没扫到目录项。请确认已进入课程学习页，且左侧目录已展开。")
            return 1
        print("\n" + "=" * 70)
        print(f"课程：{page.title()[:60]}")
        print(f"地址：{page.url[:100]}")
        print("=" * 70)
        for i, lf in enumerate(leaves, 1):
            print(f"  [{i:>3}] {lf.display}")
        print("=" * 70)
        print(f"共 {len(leaves)} 项\n")
        return 0
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
