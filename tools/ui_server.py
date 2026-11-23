# -*- coding: utf-8 -*-
"""U校园助手 · 本地控制面板服务（Python 标准库，零新依赖）。

双击桌面唯一入口 → 启动本服务 → 自动打开浏览器。
只监听 127.0.0.1，不对外暴露；不读取也不返回 config.yaml / 登录态 / 密钥。

用法：
    python tools/ui_server.py                 # 起服务并自动开浏览器
    python tools/ui_server.py --no-browser    # 只起服务
    python tools/ui_server.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UI_FILE = ROOT / "ui" / "index.html"
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
OUTPUT_DIR = ROOT / "output"
PREFS_FILE = STATE_DIR / "ui_prefs.json"
PORT_FILE = STATE_DIR / "ui_port.json"
RULES_FILE = ROOT / "data" / "page_rules.yaml"
REPORT_FILE = OUTPUT_DIR / "study_ai_report.json"
PROGRESS_FILE = STATE_DIR / "progress.json"

DEFAULT_PREFS = {
    "auto_start": False, "minutes": 60, "passes": 2,
    "book": "all", "unit": "", "micro": "", "redo": False,
    "retry_manual": False,
}
PREF_KEYS = tuple(DEFAULT_PREFS)
# 单例识别标记：/api/health 里回报，用于区分"本项目的面板"和其它本地服务
APP_TAG = "unipus-panel"

_lock = threading.Lock()
_proc = None
_proc_started = 0.0
_proc_rc = None
_stdout_fp = None


# ---------------------------------------------------------------- 小工具
def _read_json(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return default


def _write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_prefs() -> dict:
    p = dict(DEFAULT_PREFS)
    got = _read_json(PREFS_FILE, {})
    if isinstance(got, dict):
        p.update({k: v for k, v in got.items() if k in PREF_KEYS})
    return p


def _rules() -> dict:
    try:
        from upc.page_rules import load
        return load(force=True)
    except Exception:  # noqa: BLE001
        return {}


def latest_log():
    try:
        xs = sorted(LOG_DIR.glob("run_*.log"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
        return xs[0] if xs else None
    except Exception:  # noqa: BLE001
        return None


_REDACT = (
    (re.compile(r"https?://\S+"), "[URL]"),
    (re.compile(r"(?i)\b(jwt|token|ticket|cookie|password|key|secret|authorization)"
                r"\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[REDACTED]"),
)


def _safe_line(s: str) -> str:
    for rx, rep in _REDACT:
        s = rx.sub(rep, s)
    return s


def log_tail(n: int = 160) -> dict:
    p = latest_log()
    if not p:
        return {"file": None, "lines": ["（还没有运行日志，点「开始刷课」后会出现在这里）"]}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = [_safe_line(x) for x in raw[-max(10, min(n, 800)):]]
    except Exception as exc:  # noqa: BLE001
        lines = [f"（日志读取失败：{type(exc).__name__}）"]
    return {"file": p.name, "lines": lines}


# ---------------------------------------------------------------- 状态
def build_state() -> dict:
    global _proc, _proc_started, _proc_rc
    with _lock:
        p, started, rc = _proc, _proc_started, _proc_rc
    running = bool(p and p.poll() is None)

    r = _rules()
    resume = r.get("resume") or {}
    nav = r.get("navigation") or {}
    prog = _read_json(PROGRESS_FILE, {}) or {}
    leaves = prog.get("leaves", {}) if isinstance(prog, dict) else {}
    cached = sum(1 for v in leaves.values()
                 if isinstance(v, dict) and v.get("status") == "unsupported_pc")

    rep = _read_json(REPORT_FILE, None)
    summary = {"has_data": False}
    if isinstance(rep, dict):
        dist = {}
        for t in (rep.get("tasks") or []):
            if isinstance(t, dict):
                s = str(t.get("status") or "done")
                dist[s] = dist.get(s, 0) + 1
        summary = {
            "has_data": True,
            "elapsed_min": rep.get("elapsed_min"),
            "done": rep.get("done"),
            "blue_gained": rep.get("blue_gained"),
            "micros_turned": rep.get("micros_turned") or [],
            "unsupported_pc": len(rep.get("unsupported_pc") or []),
            "cached_skips": rep.get("cached_skips", 0),
            "status_dist": dist,
        }
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        version = "1.1.0"
    lp = latest_log()
    return {
        "running": running,
        "pid": p.pid if running else None,
        "started_at": started if running else None,
        "elapsed_min": round((time.time() - started) / 60, 1) if running else 0,
        "last_rc": rc,
        "version": version,
        "prefs": load_prefs(),
        "rules": {
            "enabled": bool(resume.get("enabled", True)),
            "ttl_days": resume.get("ttl_days", 7),
            "goto_retries": nav.get("goto_retries", 2),
            "backoff_ms": nav.get("backoff_ms", 1500),
            "max_consecutive_failures": nav.get("max_consecutive_failures", 3),
            "pc_marks": r.get("pc_unsupported_marks", []),
            "stuck_marks": r.get("roleplay_stuck_marks", []),
        },
        "progress": {"cached_unsupported": cached, "tracked": len(leaves)},
        "report": summary,
        "log_file": lp.name if lp else None,
    }


# ---------------------------------------------------------------- 任务控制
def _clamp(v, lo, hi, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _watch(proc):
    """记录退出码；不阻塞 HTTP 线程。"""
    global _proc_rc
    rc = proc.wait()
    with _lock:
        _proc_rc = rc
    try:
        if _stdout_fp:
            _stdout_fp.write(f"===== 结束，退出码 {rc} =====\n")
            _stdout_fp.flush()
    except Exception:  # noqa: BLE001
        pass


def start_study(opts: dict) -> dict:
    global _proc, _proc_started, _proc_rc, _stdout_fp
    with _lock:
        if _proc and _proc.poll() is None:
            return {"ok": False, "msg": "已经有一个任务在跑了，先停止再启动"}
        minutes = _clamp(opts.get("minutes", 60), 1, 600, 60)
        passes = int(_clamp(opts.get("passes", 2), 1, 5, 2))
        book = (opts.get("book") if opts.get("book") in ("vls", "rw", "all")
                else "all")
        unit = str(opts.get("unit") or "").strip()
        micro = str(opts.get("micro") or "").strip()[:60]
        if unit and not re.fullmatch(r"[0-9,\-\s]{1,30}", unit):
            return {"ok": False, "msg": "Unit 只能填数字、逗号、连字符，如 1-3 或 1,3"}
        if unit and micro:
            return {"ok": False, "msg": "Unit 和节名二选一即可"}
        redo = bool(opts.get("redo"))
        retry_manual = bool(opts.get("retry_manual"))

        cmd = [sys.executable, "-u", "tools/study_ai.py",
               "--minutes", f"{minutes:g}", "--passes", str(passes),
               "--book", book]
        if unit:
            cmd += ["--unit", unit]
        if micro:
            cmd += ["--micro", micro]
        if redo:
            cmd += ["--redo"]
        if retry_manual:
            cmd += ["--retry-manual"]

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _stdout_fp:
            try:
                _stdout_fp.close()
            except Exception:  # noqa: BLE001
                pass
        _stdout_fp = (LOG_DIR / "ui_stdout.log").open(
            "a", encoding="utf-8", errors="replace")
        _stdout_fp.write(
            f"\n===== UI 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} :: "
            f"{' '.join(cmd[2:])} =====\n")
        _stdout_fp.flush()

        kw = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL,
              "stdout": _stdout_fp, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            _proc = subprocess.Popen(cmd, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": f"启动失败：{type(exc).__name__}: {exc}"}
        _proc_started, _proc_rc = time.time(), None
        pid = _proc.pid
        shown = " ".join(cmd[2:])
    threading.Thread(target=_watch, args=(_proc,), daemon=True).start()
    return {"ok": True, "pid": pid, "msg": "已启动", "cmd": shown}


def stop_study() -> dict:
    with _lock:
        p = _proc
    if not p or p.poll() is not None:
        return {"ok": False, "msg": "当前没有正在运行的任务"}
    # ★ 2026-11-23 修正：terminate() 在 Windows 上就是 TerminateProcess
    #   硬杀，本来就不会走任何"保存登录态"的清理逻辑（旧文案与行为不符）；
    #   且它杀不到 Playwright 拉起的浏览器子进程 → Windows 改用 taskkill
    #   连进程树清理，防浏览器野进程残留。
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            p.terminate()
    except Exception:  # noqa: BLE001
        pass
    try:
        p.wait(timeout=8)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "msg": "已强制结束任务（连同其拉起的浏览器进程）"}


# ---------------------------------------------------------------- 配置写入
_RULE_NUM_KEYS = ("ttl_days", "goto_retries", "backoff_ms",
                  "max_consecutive_failures")


def save_prefs(opts: dict) -> dict:
    p = load_prefs()
    for k in PREF_KEYS:
        if k in opts:
            p[k] = opts[k]
    p["minutes"] = _clamp(p.get("minutes"), 1, 600, 60)
    p["passes"] = int(_clamp(p.get("passes"), 1, 5, 2))
    p["book"] = (p.get("book") if p.get("book") in ("vls", "rw", "all")
                 else "all")
    p["auto_start"] = bool(p.get("auto_start"))
    p["redo"] = bool(p.get("redo"))
    p["retry_manual"] = bool(p.get("retry_manual"))
    _write_json(PREFS_FILE, p)
    return {"ok": True, "prefs": p}


def update_rules(opts: dict) -> dict:
    """只替换允许的数值键，保留 yaml 里的注释和结构。"""
    try:
        # 用 bytes 读写，保持原文件的换行风格（否则 Windows 上 LF 会被写成 CRLF）
        text = RULES_FILE.read_bytes().decode("utf-8")
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": "规则文件读不到"}
    for key in _RULE_NUM_KEYS:
        if key not in opts:
            continue
        try:
            num = float(opts[key])
        except (TypeError, ValueError):
            continue
        if num < 0:
            continue
        val = f"{num:g}"
        # [ \t] 而不是 \s：\s 会跨行，可能吃掉上一行的空行
        text = re.sub(rf"(?m)^([ \t]*{key}:[ \t]*)[^\s#]+",
                      lambda m, v=val: m.group(1) + v, text, count=1)
    if "enabled" in opts:
        val = "true" if bool(opts["enabled"]) else "false"
        text = re.sub(r"(?m)^([ \t]*enabled:[ \t]*)[^\s#]+",
                      lambda m, v=val: m.group(1) + v, text, count=1)
    try:
        tmp = RULES_FILE.with_name(RULES_FILE.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, RULES_FILE)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"写入失败：{type(exc).__name__}"}
    return {"ok": True, "msg": "规则已保存（下次运行生效）"}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "UnipusPanel/1.0"

    def log_message(self, fmt, *args):
        pass  # 别把每个轮询都打到控制台

    def _host_ok(self) -> bool:
        """拦 DNS 重绑定：Host 不是本机地址的一律拒。"""
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1")

    def _fetch_ok(self) -> bool:
        """拦浏览器跨站 POST（网页里的 no-cors 表单也能打到本地端口，
        会越权启动任务/改写规则）。Sec-Fetch-Site 主流浏览器全量发送，
        缺失视为老客户端放行；same-origin=面板自己；none=地址栏直接打开。
        """
        fs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return fs in ("", "same-origin", "same-site", "none")

    def _bytes(self, data: bytes, ctype: str, code: int = 200):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError,
                BrokenPipeError):
            pass  # 前端轮询时提前断开属正常，不打堆栈

    def _json(self, obj, code: int = 200):
        self._bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8", code)

    def do_GET(self):
        if not self._host_ok():
            self._json({"error": "forbidden host"}, 403)
            return
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            try:
                self._bytes(UI_FILE.read_bytes(), "text/html; charset=utf-8")
            except Exception:  # noqa: BLE001
                self._bytes(b"<h1>ui/index.html missing</h1>",
                            "text/html; charset=utf-8", 500)
        elif p == "/api/state":
            self._json(build_state())
        elif p == "/api/logs":
            try:
                n = int(parse_qs(u.query).get("n", ["160"])[0])
            except (TypeError, ValueError):
                n = 160
            self._json(log_tail(n))
        elif p == "/api/report":
            self._json(_read_json(REPORT_FILE, {"has_data": False}))
        elif p == "/api/health":
            self._json({"ok": True, "app": APP_TAG, "root": str(ROOT)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._host_ok() or not self._fetch_ok():
            self._json({"error": "forbidden"}, 403)
            return
        p = urlparse(self.path).path
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(ln, 65536)) if ln else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}
        if p == "/api/start":
            self._json(start_study(body))
        elif p == "/api/stop":
            self._json(stop_study())
        elif p == "/api/prefs":
            self._json(save_prefs(body))
        elif p == "/api/rules":
            self._json(update_rules(body))
        else:
            self._json({"error": "not found"}, 404)


_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _ensure_streams():
    """pythonw.exe 下没有控制台，stdout/stderr 是 None —— 重定向到日志文件。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = (LOG_DIR / "ui_server.log").open("a", encoding="utf-8",
                                            errors="replace")
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(line_buffering=True)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def _find_browser():
    for c in _BROWSER_CANDIDATES:
        if Path(c).is_file():
            return c
    return None


def launch_window(url: str, mode: str = "auto", on_close=None) -> str:
    """把面板装进独立窗口（没有地址栏 / 标签页 / 工具栏），而不是普通浏览器标签。

    优先 pywebview 原生窗口（装了才有），否则用 Edge/Chrome 的应用窗口模式，
    都没有才退回系统默认浏览器。
    """
    if mode in ("auto", "native"):
        try:
            import webview  # 可选依赖，没装就走下面
            try:
                webview.create_window("U校园自动化助手 · 控制面板", url,
                                      width=1280, height=880,
                                      min_size=(920, 620),
                                      background_color="#070c18")
            except TypeError:  # 老版本不支持这些参数
                webview.create_window("U校园自动化助手 · 控制面板", url,
                                      width=1280, height=880)
            webview.start()
            if on_close:
                on_close()
            return "native"
        except Exception as exc:  # noqa: BLE001
            if mode == "native":
                print(f"  原生窗口不可用（{type(exc).__name__}），改用应用窗口")

    if mode in ("auto", "app"):
        exe = _find_browser()
        if exe:
            # 独立 profile：新实例才由这个进程托管窗口，关窗口时能精确感知
            profile = STATE_DIR / "ui_profile"
            try:
                profile.mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            args = [exe, f"--app={url}", "--window-size=1280,880",
                    f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check",
                    "--disable-background-mode", "--disable-sync",
                    "--disable-features=msEdgeWelcomePage,msImplicitSignin"]
            try:
                proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
            except Exception as exc:  # noqa: BLE001
                print(f"  应用窗口启动失败（{type(exc).__name__}）")
            else:
                def _wait():
                    try:
                        proc.wait()
                    finally:
                        if on_close:
                            on_close()
                threading.Thread(target=_wait, daemon=True).start()
                return "app"

    webbrowser.open(url)
    return "browser"


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    # ★ Windows 上 SO_REUSEADDR 允许绑定一个「别人已经在监听」的端口：
    #   bind 成功、日志说启动成功，但所有请求都被对方接走（表现为 404）。
    #   必须关掉，宁可绑定失败换端口。
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        """前端轮询时连接被提前关掉是常态，别把堆栈写进日志。"""
        import sys as _sys
        if isinstance(_sys.exc_info()[1],
                      (ConnectionAbortedError, ConnectionResetError,
                       BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.35)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_running(start: int) -> int:
    """扫端口段找本项目的活面板（app 标记 + 项目根双重校验，不认错别的服务）。"""
    import urllib.request
    me = str(ROOT).lower()
    for port in range(start, start + 20):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=0.3) as r:
                d = json.loads(r.read().decode("utf-8"))
                if d.get("app") == APP_TAG and str(d.get("root", "")).lower() == me:
                    return port
        except Exception:  # noqa: BLE001
            continue
    return 0


def _focus_existing_window() -> bool:
    """已有面板窗口（Edge 应用模式/pywebview，标题同 ui/index.html）就唤到前台。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.FindWindowW(None, "U校园自动化助手 · 控制面板")
        if not h:
            return False
        u.ShowWindow(h, 9)          # SW_RESTORE
        u.SetForegroundWindow(h)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    _ensure_streams()
    ap = argparse.ArgumentParser(description="U校园助手本地控制面板")
    # 默认用冷门端口：8765 常被同类工具占，撞上了就会「假绑定成功 + 全 404」
    ap.add_argument("--port", type=int, default=51888)
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "native", "app", "browser"],
                    help="窗口形态：native=原生窗口，app=Edge/Chrome 应用窗口")
    ap.add_argument("--no-browser", action="store_true", help="只起服务不开窗口")
    a = ap.parse_args()

    # ★ 单例：已有本项目的面板在跑就聚焦已有窗口，不再起第二个服务。
    #   （双击两次 = 两个服务两个 Edge 窗口抢同一份 profile，互相干扰，
    #     端口文件还会被第二个实例覆盖。）
    running = _find_running(a.port)
    if running:
        url = f"http://127.0.0.1:{running}/"
        print(f"面板已在运行：{url}（不再重复起服务）")
        if not a.no_browser:
            if _focus_existing_window():
                print("已把面板窗口唤到前台")
            else:
                launch_window(url, a.mode)
        return 0

    httpd, port = None, a.port
    for i in range(20):
        p = a.port + i
        if _port_in_use(p):
            continue
        try:
            httpd = PanelServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"端口 {a.port}~{a.port + 19} 都被占用了，启动失败")
        return 1
    url = f"http://127.0.0.1:{port}/"
    _write_json(PORT_FILE, {"port": port, "url": url})
    print(f"[{time.strftime('%H:%M:%S')}] 面板服务已启动：{url}")

    # 服务放后台线程：原生窗口（pywebview）要求 GUI 占用主线程
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if load_prefs().get("auto_start"):
        def _auto():
            time.sleep(1.2)
            print("自动开始：", start_study(load_prefs()))
        threading.Thread(target=_auto, daemon=True).start()

    stop = threading.Event()
    channel = "none"
    if not a.no_browser:
        channel = launch_window(url, a.mode, on_close=stop.set)
        print(f"[{time.strftime('%H:%M:%S')}] 窗口通道：{channel}")

    try:
        if a.no_browser or channel == "browser":
            while True:          # 无窗口：常驻，Ctrl+C 退出
                time.sleep(0.6)
        else:
            stop.wait()          # 窗口关掉就收工，不留后台野进程
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
        httpd.server_close()
        print(f"[{time.strftime('%H:%M:%S')}] 面板已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
