# -*- coding: utf-8 -*-
"""一键捕获：打开浏览器 → 等你登录并进入课程页 → 自动抓取结构落盘。

与 probe.py 的区别：本脚本**不需要任何键盘输入**，全程自动轮询，
抓到目标页面就自己收工。适合被别的程序/助手直接调起。

用法：
    python tools/capture.py                 # 默认等 20 分钟
    python tools/capture.py --timeout 600
    python tools/capture.py --keep-open     # 抓完不关浏览器
"""
import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# URL 里的敏感参数（手机号、授权码、token）在日志里要遮掉
_SENSITIVE_QS = re.compile(
    r"(?i)\b(username|user|phone|mobile|code|ticket|token|password|service)\s*=\s*([^&\s]+)"
)
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")


def mask_url(url):
    """把 URL 里的手机号 / 授权码 / service 参数遮掉，避免日志泄露。"""
    s = str(url)

    def _q(m):
        key, val = m.group(1), m.group(2)
        if key.lower() == "service":
            # service 里包的是跳转地址，保留但截断
            return f"{key}={val[:40]}…"
        return f"{key}=***"

    s = _SENSITIVE_QS.sub(_q, s)
    s = _PHONE.sub(r"\1****\2", s)
    return s


def log_line(msg, status_file):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(status_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=1200, help="总等待秒数（默认 1200）")
    ap.add_argument("--keep-open", action="store_true", help="抓完不关浏览器")
    ap.add_argument("--poll", type=float, default=3.0, help="轮询间隔秒")
    args = ap.parse_args()

    from upc import menu as M
    from upc import selectors as S
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = False          # 必须可见，用户要在这里登录
    cfg.login.reuse_state = True
    logger = setup_logging(cfg.log_dir, "WARNING")

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_file = out_dir / "capture_status.log"
    try:
        status_file.unlink()
    except Exception:  # noqa: BLE001
        pass

    log_line("启动中…", status_file)
    session = None
    captured = False
    try:
        session = BrowserSession(cfg, logger)
        log_line(f"浏览器已启动（{session._channel_used}），打开登录页…", status_file)
        # 走官网首页 → 点右上角「登录」→ 网站自己跳 SSO。
        # 不能硬编码 SSO 的 service 参数：填错会导致登录后被打回宣传页。
        from upc import login as L
        if not L._open_login_page(session, logger):
            log_line("没能自动打开登录页，请手动在窗口里点「登录」", status_file)
        # 协议勾选框是最常见的"密码对但登录没反应"原因，必须提示
        try:
            if not L.check_agreement(session.page, logger, auto_check=False):
                log_line("⚠ 协议框【未勾选】—— 请先在浏览器里勾上，否则点登录没反应！",
                         status_file)
        except Exception:  # noqa: BLE001
            pass
        try:
            key = session.page.evaluate("() => location.href") or ""
            if "sso.unipus.cn" in key:
                log_line("已到达统一登录页（sso.unipus.cn）", status_file)
        except Exception:  # noqa: BLE001
            pass
        # 确认窗口在前台，并把标题写进日志，方便用户认出是哪个窗口
        try:
            session.page.bring_to_front()
            session.page.wait_for_timeout(800)
            title = session.page.title()
            log_line(f"窗口标题：{title or '(空)'}", status_file)
        except Exception:  # noqa: BLE001
            pass
        log_line("=" * 60, status_file)
        log_line("请在这个浏览器窗口里用【手机号 + 密码】登录", status_file)
        log_line("（不要用微信扫码：会卡在「第三方账号绑定」页）", status_file)
        log_line("登录后程序会自动选学校、进课程，无需你操作。", status_file)
        log_line("=" * 60, status_file)

        deadline = time.time() + args.timeout
        last_note = ""
        nav_tried = 0
        last_nav = 0.0
        last_save = 0.0
        last_shot = 0.0

        while time.time() < deadline:
            time.sleep(args.poll)

            # ---- ① 已经出现课程目录？（用户自己点到的，或导航成功后）----
            target = _find_courseware_page(session)
            if target is not None:
                log_line(f"检测到课程目录：{mask_url(target.url)[:90]}", status_file)
                target.bring_to_front()
                target.wait_for_timeout(2500)
                log_line("开始抓取结构…", status_file)
                _capture(session, target, cfg, out_dir, status_file)
                captured = True
                _write_course_url(target.url, status_file)
                break

            # ---- ② 已登录但还没到课程页 → 由程序自己导航 ----
            now = time.time()
            if now - last_nav > 45:            # 最多每 45 秒尝试一次导航
                last_nav = now
                if _looks_logged_in(session):
                    nav_tried += 1
                    log_line(f"检测到已登录，开始自动导航（第 {nav_tried} 次）…", status_file)
                    try:
                        _try_portal_nav(session, cfg, status_file)
                    except Exception as exc:  # noqa: BLE001
                        log_line(f"导航异常：{exc}", status_file)

                    target = _find_courseware_page(session)
                    if target is not None:
                        log_line(f"导航成功：{mask_url(target.url)[:90]}", status_file)
                        target.bring_to_front()
                        target.wait_for_timeout(2500)
                        _capture(session, target, cfg, out_dir, status_file)
                        captured = True
                        _write_course_url(target.url, status_file)
                        break

            # ---- ③ 定期保存登录态 + 页面截图（便于远程诊断）----
            if now - last_save > 30:
                last_save = now
                try:
                    session.save_state()
                except Exception:  # noqa: BLE001
                    pass
            if now - last_shot > 20:
                last_shot = now
                try:
                    p = session.context.pages[-1] if session.context.pages else session.page
                    p.screenshot(path=str(out_dir / "last.png"), timeout=8000)
                except Exception:  # noqa: BLE001
                    pass

            # ---- ④ 心跳提示 ----
            try:
                cur = session.context.pages[-1].url if session.context.pages else ""
            except Exception:  # noqa: BLE001
                cur = ""
            note = mask_url(cur)[:90]
            if note != last_note:
                last_note = note
                hint = ""
                low = (cur or "").lower()
                if "thirdpartybound" in low:
                    hint = "  ← 卡在「第三方账号绑定」页，建议改用手机号+密码登录"
                elif "findpw" in low:
                    hint = "  ← 这是找回密码页，返回上一步重新登录"
                elif "/login" in low or "sso" in low:
                    hint = "  ← 登录页，请完成登录"
                log_line(f"当前页面：{note}{hint}", status_file)

        if not captured:
            log_line("等待超时，没有检测到课程目录。", status_file)
            _capture_any(session, cfg, out_dir, status_file)
    except Exception as exc:  # noqa: BLE001
        log_line(f"出错：{exc}", status_file)
        traceback.print_exc()
    finally:
        try:
            if session:
                session.save_state()
                log_line("登录态已保存", status_file)
        except Exception:  # noqa: BLE001
            pass
        if session and not args.keep_open:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        log_line("DONE" if captured else "FAILED", status_file)
    return 0 if captured else 1


def _find_courseware_page(session):
    """在所有 unipus 标签页里找"已经有课程目录"的那一个。"""
    from upc import menu as M

    try:
        pages = list(session.context.pages)
    except Exception:  # noqa: BLE001
        return None
    for p in pages:
        try:
            url = p.url or ""
        except Exception:  # noqa: BLE001
            continue
        if "unipus.cn" not in url:
            continue
        found = False
        try:
            found = M.menu_present(p)
        except Exception:  # noqa: BLE001
            found = False
        if not found:
            for fr in p.frames:
                if fr is p.main_frame:
                    continue
                try:
                    if M.menu_present(fr):
                        found = True
                        break
                except Exception:  # noqa: BLE001
                    continue
        if found:
            return p
    return None


def _looks_logged_in(session):
    """粗判是否已登录（不看 cookie 名字，看页面/URL）。"""
    try:
        page = session.context.pages[-1] if session.context.pages else session.page
        url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if any(h in url for h in ("/login", "sso.", "findpw", "thirdparty")):
        return False
    if "unipus.cn" not in url:
        return False

    # 「选择学校」弹窗说明已经登录成功，只是还差一步认证
    for p in [page] + [x for x in getattr(session.context, "pages", []) if x is not page]:
        try:
            if "选择学校" in (p.inner_text("body") or ""):
                return True
        except Exception:  # noqa: BLE001
            continue

    # 门户首页出现"学习/我的"这类已登录导航，基本就是进了
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    except Exception:  # noqa: BLE001
        text = ""
    if not text:
        return False
    if any(k in text for k in ("登录", "注册")) and "退出" not in text and "我的" not in text:
        return False
    return any(k in text for k in ("学习", "我的", "退出", "课程"))


def _try_portal_nav(session, cfg, status_file):
    """调用门户导航模块，从主站点到课程目录。"""
    from upc import portal as P
    from upc.humanize import Humanizer
    from upc.popups import PopupGuard

    log = _QuietLog()
    humanizer = Humanizer(cfg, log)
    popup = PopupGuard(cfg, log, humanizer)

    # 确保在门户首页起步
    try:
        url = (session.page.url or "")
    except Exception:  # noqa: BLE001
        url = ""
    if "u.unipus.cn" not in url:
        from upc import selectors as S
        session.goto(S.HOME_URL, context="门户首页")
        session.page.wait_for_timeout(2500)

    ok = P.enter_courseware(session, cfg, log, humanizer, popup)
    log_line(f"门户导航结果：{'成功' if ok else '未成功'}", status_file)
    return ok


def _write_course_url(url, status_file):
    """把抓到的课程页地址回写进 config.yaml 的 course.url（保留注释与其它配置）。"""
    if not url or "unipus.cn" not in url:
        return
    cfg_path = ROOT / "config.yaml"
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log_line(f"读取 config.yaml 失败，课程地址请你手动填：{url[:90]}", status_file)
        return

    lines = text.splitlines()
    # 定位 course: 段
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^course:\s*$", ln):
            start = i
            break
    if start is None:
        log_line(f"config.yaml 里没找到 course 段，请手动填：{url[:90]}", status_file)
        return

    indent = None
    i = start + 1
    done = False
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln.startswith((" ", "\t")):
            break                      # 到了下一个顶层键
        m = re.match(r"^(\s+)url:.*$", ln)
        if m and m.group(1):
            indent = m.group(1)
            lines[i] = f'{indent}url: "{url}"'
            done = True
            break
        i += 1
    if not done:
        lines.insert(start + 1, f'  url: "{url}"')

    try:
        cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log_line(f"已自动写入 config.yaml → course.url = {url[:90]}", status_file)
    except Exception as exc:  # noqa: BLE001
        log_line(f"写 config.yaml 失败，请手动填：{url[:90]}（{exc}）", status_file)


def _capture(session, page, cfg, out_dir, status_file):
    """抓取目标页面的完整结构。"""
    from upc import selectors as S
    from upc import menu as M
    from upc import tabs as T

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 先尝试展开目录，确保叶子都在
    try:
        M.expand_all(page, _QuietLog(), humanizer=None, rounds=4)
        page.wait_for_timeout(1200)
    except Exception:  # noqa: BLE001
        pass

    data = {
        "generated_at": stamp,
        "course_url": page.url,
        "title": _safe(lambda: page.title(), ""),
        "selector_hits": {},
        "frames": [],
        "leaves": [],
        "tabs": [],
        "tasks": [],
        "menu_nodes": [],
        "media": [],
        "inputs": [],
        "buttons": [],
        "text_sample": "",
    }

    # --- 选择器命中统计（主页面 + 每个 frame）---
    checks = {
        "menu_container": S.MENU_CONTAINER,
        "menu_container_u3": S.MENU_CONTAINER_U3,
        "node_unit": S.NODE_UNIT,
        "node_section": S.NODE_SECTION,
        "node_micro": S.NODE_MICRO,
        "node_plain": S.NODE_PLAIN,
        "node_u3_group": S.NODE_U3_GROUP,
        "tree_item": S.TREE_ITEM,
        "tab_item": S.TAB_ITEM,
        "task_item": S.TASK_ITEM,
        "video": S.VIDEO_ELEMENT,
        "popup_btn": S.POPUP_BUTTONS,
        "submit_btn": S.SUBMIT_BUTTON,
        "record_btn": S.RECORD_BUTTON,
    }

    def hit_map(scope):
        out = {}
        for name, sels in checks.items():
            rows = []
            for sel in sels[:8]:
                try:
                    n = len(scope.query_selector_all(sel))
                except Exception:  # noqa: BLE001
                    continue
                if n > 0:
                    rows.append([sel, n])
            out[name] = rows
        return out

    data["selector_hits"]["main"] = hit_map(page)

    # --- 每个 frame ---
    for i, fr in enumerate(page.frames):
        item = {
            "index": i,
            "url": _safe(lambda f=fr: f.url, "")[:200],
            "is_main": fr is page.main_frame,
            "selector_hits": hit_map(fr),
        }
        # 每个 frame 的类名分布（找候选选择器用）
        item["classes"] = _safe(lambda f=fr: f.evaluate(_JS_CLASSES), []) or []
        item["text_sample"] = (_safe(
            lambda f=fr: f.evaluate("() => document.body ? document.body.innerText : ''"),
            "") or "")[:600]
        data["frames"].append(item)

    # --- 目录叶子（用正式扫描逻辑，看它能识别出什么）---
    try:
        leaves = M.discover_leaves(page, _QuietLog(), humanizer=None)
        data["leaves"] = [l.to_dict() | {"display": l.display, "level": l.level}
                          for l in leaves]
    except Exception as exc:  # noqa: BLE001
        data["leaves_error"] = str(exc)[:200]

    # --- 目录节点原始样本 ---
    try:
        data["menu_nodes"] = page.evaluate(_JS_MENU_SAMPLES) or []
    except Exception:  # noqa: BLE001
        pass

    # --- Tab / Task ---
    try:
        tabs = T.list_tabs(page, wait_ms=3000)
        data["tabs"] = [{"idx": t.idx, "name": t.name, "active": t.active} for t in tabs]
    except Exception as exc:  # noqa: BLE001
        data["tabs_error"] = str(exc)[:200]
    try:
        tasks = T.list_tasks(page, wait_ms=3000)
        data["tasks"] = [{"idx": t.idx, "name": t.name, "active": t.active} for t in tasks]
    except Exception as exc:  # noqa: BLE001
        data["tasks_error"] = str(exc)[:200]

    # --- 媒体 / 表单 / 按钮 ---
    data["media"] = _safe(lambda: page.evaluate(_JS_MEDIA), []) or []
    data["inputs"] = _safe(lambda: page.evaluate(_JS_INPUTS), []) or []
    data["buttons"] = _safe(lambda: page.evaluate(_JS_BUTTONS), []) or []
    data["text_sample"] = (_safe(
        lambda: page.evaluate("() => document.body ? document.body.innerText : ''"),
        "") or "")[:3000]

    json_path = out_dir / f"capture_{stamp}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log_line(f"结构已保存：{json_path}", status_file)

    try:
        html_path = out_dir / f"capture_{stamp}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        log_line(f"页面 HTML 已保存：{html_path}", status_file)
    except Exception as exc:  # noqa: BLE001
        log_line(f"保存 HTML 失败：{exc}", status_file)

    # 摘要
    log_line(f"识别到叶子节点：{len(data['leaves'])} 个", status_file)
    log_line(f"Tab：{len(data['tabs'])} 个 | Task：{len(data['tasks'])} 个", status_file)
    hit_summary = {k: v[0] for k, v in data["selector_hits"]["main"].items() if v}
    log_line(f"主页面选择器命中：{json.dumps(hit_summary, ensure_ascii=False)[:400]}",
             status_file)


def _capture_any(session, cfg, out_dir, status_file):
    """超时兜底：抓当前页面的结构，便于判断卡在哪。"""
    try:
        pages = [p for p in session.context.pages if "unipus.cn" in (p.url or "")]
        if not pages:
            log_line("当前没有 U校园 相关页面可抓", status_file)
            return
        page = pages[-1]
        log_line(f"兜底抓取当前页面：{mask_url(page.url)[:90]}", status_file)
        _capture(session, page, cfg, out_dir, status_file)
    except Exception as exc:  # noqa: BLE001
        log_line(f"兜底抓取失败：{exc}", status_file)


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


class _QuietLog:
    def info(self, *a, **k):
        pass

    warning = info
    error = info
    debug = info


# --------------------------------------------------------------- JS 片段
_JS_CLASSES = r"""
() => {
  const counts = {};
  for (const el of document.querySelectorAll('*')) {
    const c = el.className;
    if (typeof c !== 'string' || !c.trim()) continue;
    for (const k of c.trim().split(/\s+/)) {
      if (k.length > 2 && k.length < 60) counts[k] = (counts[k] || 0) + 1;
    }
  }
  return Object.entries(counts)
    .filter(([k, v]) => v >= 1 && v <= 300)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 120)
    .map(([k, v]) => ({cls: k, n: v}));
}
"""

_JS_MENU_SAMPLES = r"""
() => {
  const sels = ['.pc-slider-menu-unit', '.pc-slider-menu-node', '.pc-slider-menu-section',
                '.pc-slider-menu-micro', '.ant-tree-treenode', '[role="treeitem"]',
                'li.unit', 'li.group', '[class*="menu"] li', '[class*="menu"] [class*="item"]'];
  const out = [];
  for (const s of sels) {
    let list = [];
    try { list = document.querySelectorAll(s); } catch (e) { continue; }
    for (const el of Array.from(list).slice(0, 30)) {
      out.push({
        sel: s,
        cls: typeof el.className === 'string' ? el.className : '',
        text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        ariaLevel: el.getAttribute('aria-level'),
        ariaExpanded: el.getAttribute('aria-expanded'),
        dataAttrs: Array.from(el.attributes)
          .filter(a => a.name.startsWith('data-'))
          .map(a => a.name + '=' + String(a.value).slice(0, 24)),
      });
    }
  }
  return out;
}
"""

_JS_MEDIA = r"""
() => Array.from(document.querySelectorAll('video, audio')).map(el => ({
  tag: el.tagName.toLowerCase(),
  src: (el.currentSrc || el.getAttribute('src') || '').slice(0, 160),
  dur: (isFinite(el.duration) ? el.duration : 0),
  paused: !!el.paused,
  cls: typeof el.className === 'string' ? el.className : '',
}))
"""

_JS_INPUTS = r"""
() => Array.from(document.querySelectorAll(
  'input[type=radio], input[type=checkbox], input[type=text], textarea')).slice(0, 80)
  .map(el => ({
    type: el.type, name: el.name, id: el.id,
    cls: typeof el.className === 'string' ? el.className : '',
    placeholder: el.placeholder || '',
    visible: el.getClientRects().length > 0,
  }))
"""

_JS_BUTTONS = r"""
() => Array.from(document.querySelectorAll('button, [role="button"], .btn')).slice(0, 150)
  .map(el => ({
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    cls: typeof el.className === 'string' ? el.className : '',
    id: el.id,
    visible: el.getClientRects().length > 0,
  }))
  .filter(b => b.text || b.cls)
"""


if __name__ == "__main__":
    raise SystemExit(main())
