# -*- coding: utf-8 -*-
"""API 模式刷时长（不启浏览器、不过网页认证）。

用法：
    python tools/api_run.py                 # 交互：登录 → 选课程 → 刷时长
    python tools/api_run.py --list          # 只列课程
    python tools/api_run.py --course 1      # 直接选第 N 门课
    python tools/api_run.py --minutes 90    # 刷 90 分钟
    python tools/api_run.py --probe         # 只验证登录链路，不刷时长

前提：config.yaml 里填好 account.username / account.password
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from upc.api_client import UnipusAPI          # noqa: E402
from upc.config import load_config            # noqa: E402
from upc.logutil import setup_logging         # noqa: E402


def pick(options, prompt):
    for i, o in enumerate(options):
        print(f"  [{i + 1}] {o}")
    print(f"  [0] 全部")
    try:
        raw = input(f"{prompt}> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw in ("", "0"):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return int(raw) - 1
    return None


def _find_tkinter_python():
    """找一个带 tkinter 的解释器（项目 .venv 是精简 Python，没有 tkinter）。"""
    import subprocess
    cands = [sys.executable,
             r"C:\Users\YZH\AppData\Local\Programs\Python\Python314\python.exe",
             r"C:\Users\YZH\AppData\Local\Programs\Python\Python313\python.exe",
             r"C:\Users\YZH\miniconda3\python.exe",
             "python", "py"]
    for p in cands:
        try:
            r = subprocess.run(
                [p, "-c", "import tkinter;tkinter.Tk().destroy();print('ok')"],
                capture_output=True, timeout=25, text=True,
            )
            if r.returncode == 0 and "ok" in (r.stdout or ""):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


def gui_auth(default_user=""):
    """弹窗要账号密码。返回 (user, pwd) 或 (None, None)。

    先试进程内 tkinter；没有就找一个系统 Python 跑 ask_auth.py。
    密码通过临时文件传递，读完立刻删除。
    """
    import json
    import subprocess
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"upc_auth_{int(time.time())}.json"
    script = ROOT / "tools" / "ask_auth.py"

    # ① 进程内 tkinter
    try:
        import tkinter as tk  # noqa: F401
        sys.path.insert(0, str(ROOT / "tools"))
        import ask_auth  # noqa: F401
        import runpy
        sys.argv = ["ask_auth.py", str(tmp), default_user]
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit:
            pass
        if tmp.exists():
            d = json.loads(tmp.read_text(encoding="utf-8"))
            tmp.unlink(missing_ok=True)
            if not d.get("cancelled"):
                return d["username"], d["password"]
            return None, None
    except Exception:  # noqa: BLE001
        pass

    # ② 借系统 Python
    interp = _find_tkinter_python()
    if not interp:
        return None, None
    try:
        subprocess.run([interp, str(script), str(tmp), default_user], timeout=300)
        if tmp.exists():
            d = json.loads(tmp.read_text(encoding="utf-8"))
            tmp.unlink(missing_ok=True)
            if not d.get("cancelled"):
                return d["username"], d["password"]
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return None, None


def save_credentials(root, username, password):
    """把账号密码写回 config.yaml 的 account 段，下次不再弹窗。

    只在 account 段内替换，避免误改 llm 等其他段的同名字段。
    注意：这是**明文**保存在本机文件里（仅本机可读，不上传）。
    """
    import json as _json
    import re as _re

    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        return False
    lines = cfg_path.read_text(encoding="utf-8").split("\n")
    in_acc, done_u, done_p = False, False, False
    for i, ln in enumerate(lines):
        if _re.match(r"^account:\s*$", ln):
            in_acc = True
            continue
        if in_acc and _re.match(r"^\S", ln):
            break
        if not in_acc:
            continue
        if not done_u and _re.match(r"^\s+username:", ln):
            lines[i] = "  username: " + _json.dumps(username, ensure_ascii=False)
            done_u = True
        elif not done_p and _re.match(r"^\s+password:", ln):
            lines[i] = "  password: " + _json.dumps(password, ensure_ascii=False)
            done_p = True
    if done_u and done_p:
        cfg_path.write_text("\n".join(lines), encoding="utf-8")
    return done_u and done_p


def main():
    ap = argparse.ArgumentParser(description="U校园 API 模式刷时长")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--list", action="store_true", help="只列课程")
    ap.add_argument("--course", default="", help="课程序号（从 1 开始）或 tutorial_id")
    ap.add_argument("--minutes", type=float, default=0,
                    help="刷多少分钟（0=用 config 里的 total_minutes）")
    ap.add_argument("--interval", type=float, default=20,
                    help="上报周期秒数（默认 20，越小越激进）")
    ap.add_argument("--probe", action="store_true", help="只验证登录链路")
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存会话，强制重新登录")
    ap.add_argument("--session-only", action="store_true",
                    help="只刷新会话 cookie 并保存，不取 token（供 AI版课件流程使用）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg.log_dir, cfg.log_level)

    api = UnipusAPI(cfg, log)

    # ---------- 0. 先试缓存会话（省去反复登录）----------
    reused = False
    if not args.no_cache:
        log.info("检查是否有可复用的 API 会话…")
        reused = api.load_session()

    if not reused:
        # 缺账号密码时弹窗要（不落盘）
        if not str(cfg.account.get("username") or "") or not str(cfg.account.get("password") or ""):
            log.info("缺少账号密码，弹出输入框…")
            u, p = gui_auth(str(cfg.account.get("username") or ""))
            if not u or not p:
                try:
                    if not str(cfg.account.get("username") or ""):
                        u = input("手机号 / 账号: ").strip()
                    if not str(cfg.account.get("password") or ""):
                        import getpass
                        p = getpass.getpass("密码（输入不显示）: ")
                except (EOFError, KeyboardInterrupt):
                    log.error("未拿到账号密码，退出")
                    return 1
            if not u or not p:
                log.error("未拿到账号密码，退出")
                return 1
            cfg.account["username"] = u
            cfg.account["password"] = p
            log.info(f"已收到账号：{u[:3]}****{u[-2:] if len(u) > 5 else ''}")
            # ★ 立即写进 config.yaml，以后运行不再询问
            if save_credentials(ROOT, u, p):
                log.info("账号密码已保存到 config.yaml（明文，仅本机），下次不再询问")
            else:
                log.warning("账号密码保存失败（请手动填入 config.yaml 的 account 段）")

        log.info("=" * 58)
        log.info("U校园 API 模式（不走浏览器，无网页认证）")
        log.info("=" * 58)
        if not api.login():
            log.error("登录失败，退出")
            return 1
        api.save_session()

        api.bind_ticket()
        # 绑完立刻再存一次：这一步才会拿到 ucamUserToken / user_map 等关键 cookie，
        # 少存了后面接口全会 403
        api.save_session()

        api.fetch_user_info()
        if api.school_id:
            api.bind_ticket(api.school_id)
            api.save_session()

        # 只刷新会话的场景：老版 u.unipus.cn 的 getToken 对本账号不可用，
        # 但 AI 版课件只需要会话 cookie，所以这里就可以收工了。
        if args.session_only:
            log.info("会话已刷新并保存（跳过取 token）")
            log.info("接下来可运行：python tools/study_ai.py --list")
            return 0

        if not api.set_token():
            log.error("拿不到 token，无法刷时长。请看 output/api_get_token*.txt")
            log.error("会话已缓存，可直接重跑排查（不会重复要密码）")
            return 1
        api.save_session()
    else:
        if not api.school_id:
            api.fetch_user_info()

    token = api.token

    if args.probe:
        log.info("链路验证结束：登录 ✓  用户信息 ✓  token ✓")
        log.info(f"用户: {api.real_name} / 学号 {api.student_num} / schoolId={api.school_id}")
        log.info("（未刷时长）")
        return 0

    # ---------- 4. 课程 ----------
    courses = api.list_courses()
    if not courses:
        log.error("没拿到课程列表。可能是账号下没有课程，或页面结构变了")
        return 1

    print()
    print("=" * 58)
    print("我的课程：")
    for i, c in enumerate(courses, 1):
        print(f"  [{i}] {c['course_name']}   (tutorial_id={c['tutorial_id']})")
    print("=" * 58)

    if args.list:
        return 0

    # 选课
    chosen = None
    if args.course:
        want = args.course.strip()
        if want.isdigit() and 1 <= int(want) <= len(courses):
            chosen = courses[int(want) - 1]
        else:
            chosen = next((c for c in courses if c["tutorial_id"] == want), None)

    # 没指定就按 config 里的课程名关键词自动匹配（避免二次交互）
    if chosen is None:
        key = str(cfg.course.get("name_contains") or "").strip()
        if key:
            hits = [c for c in courses if key in c["course_name"]]
            if len(hits) == 1:
                chosen = hits[0]
                log.info(f"按关键词「{key}」自动选中：{chosen['course_name']}")
            elif len(hits) > 1:
                log.info(f"关键词「{key}」匹配到 {len(hits)} 门课，需要你选一门")
                courses = hits

    if chosen is None:
        idx = pick([f"{c['course_name']}" for c in courses], "选哪门课")
        if idx is None:
            if len(courses) == 1:
                chosen = courses[0]
            else:
                log.error("未选择课程")
                return 1
        else:
            chosen = courses[idx]

    tid = chosen["tutorial_id"]
    log.info(f"已选课程：{chosen['course_name']}  tutorial_id={tid}")

    # ---------- 5. 章节树 ----------
    paths = api.build_module_paths(tid)
    if not paths:
        log.error("没取到章节，无法上报")
        return 1

    # ---------- 6. 刷时长 ----------
    minutes = args.minutes or float(cfg.duration.total_minutes or 60)
    from upc import ws_duration
    log.info("=" * 58)
    log.info(f"开始上报学习时长：{minutes:.0f} 分钟，周期 {args.interval:.0f} 秒")
    log.info("期间不要在其他设备打开同一门课，否则时长可能不计入")
    log.info("按 Ctrl+C 可随时停止")
    log.info("=" * 58)
    try:
        spent = ws_duration.run(
            openid=api.openid, token=token, tutorial_id=tid, paths=paths,
            log=log, interval_s=args.interval, total_minutes=minutes,
        )
    except KeyboardInterrupt:
        log.warning("已手动停止")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.error(f"刷时长异常: {exc}")
        return 1

    log.info("=" * 58)
    log.info(f"结束，本次上报时长约 {spent / 60:.1f} 分钟")
    log.info("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
