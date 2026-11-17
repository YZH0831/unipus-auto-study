# -*- coding: utf-8 -*-
"""程序自己解决不了时，唤起 WorkBuddy 判断（脱敏 + 节流 + 双通道）。

用法：
    python tools/wb_helper.py --reason "课程结构为空" --rc 1 [--log logs/xxx.log]
    python tools/wb_helper.py --reason "测试" --dry      # 只生成问题包，不唤起
    from wb_helper import wake
    wake("课程结构为空", detail="main() 返回 1", rc=1)

做三件事：
1. 生成脱敏问题包 output/wb_help/request_<时间戳>.md（含最近日志尾部，脱敏）；
2. 把问题摘要复制到剪贴板 + 唤起 WorkBuddy 窗口，切过去 Ctrl+V 即可让 WorkBuddy 判断
   （默认通道：本机桌面端占着 CLI 端口 48079，无界面 CLI 实测启动即失败，
    而用户本来就在 WorkBuddy 对话里，粘贴过去判断最稳、也不额外消耗模型额度）；
3. 可选自动通道：WB_HELP_CLI=1 时唤起无界面 CLI 自动判断，写回 response_<时间戳>.json。

安全：不读取 config.yaml / state/ / Cookie / 账号密钥；日志里的 URL、jwt、token、
ticket、cookie、手机号、长串凭证一律打码；问题包落在已被 .gitignore 忽略的 output/ 下。
10 分钟内只唤起一次，避免刷课循环里反复弹窗。

环境变量：
    WB_HELP=0            完全关闭（问题包仍会生成）
    WB_HELP_CLI=1        额外唤起无界面 CLI 自动判断（默认关：桌面端占用
                         CLI 端口 48079 时必失败；开启后会用随机端口避开）
    WB_HELP_CLI_WAIT=20  等 CLI 给判断结论的秒数（超时即终止，不留野进程）
    WB_HELP_GUI=0        不唤起窗口（无人值守时用）
    WB_HELP_CLIP=0       不改写剪贴板
    WB_HELP_THROTTLE=600 节流秒数
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output" / "wb_help"
LOG_DIR = ROOT / "logs"

_PRIVACY = (
    "不要上传任何内容；不要读取或提交 config.yaml、state/、登录态、Cookie、密钥；"
    "不要把账号、手机号、密钥写进任何文件。"
)


# ------------------------------------------------------------------ 脱敏
_RE_URL = re.compile(r"https?://\S+")
_RE_BEARER = re.compile(r"(?i)Bearer\s+\S+")
_RE_KV = re.compile(
    r"(?i)\b(jwt|token|ticket|cookie|secret|password|passwd|key|authorization|"
    r"openid|mobile|phone|session|enc|auth|sign|tgt|grantingticket|serviceticket)"
    r"\b\s*[:=]\s*[\"']?([^\s,;\"'}]+)"
)
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_RE_LONG = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_RE_COOKIE_LIST = re.compile(r"(?i)(cookies?\s*[:=]\s*)\[[^\]]*\]")


def redact(text: str, limit: int = 9000) -> str:
    """把日志里可能带身份/凭证的内容打码。"""
    lines = []
    for line in str(text or "").splitlines():
        line = _RE_URL.sub("[URL]", line)
        line = _RE_BEARER.sub("Bearer [REDACTED]", line)
        line = _RE_COOKIE_LIST.sub(r"\1[REDACTED]", line)
        line = _RE_KV.sub(lambda m: f"{m.group(1)}=[REDACTED]", line)
        line = _RE_PHONE.sub("[PHONE]", line)
        line = _RE_LONG.sub("[REDACTED]", line)
        lines.append(line.rstrip())
    return "\n".join(lines)[:limit]


def _log_tail(log_path: str | None, max_lines: int = 60) -> tuple[str, str]:
    """返回 (日志文件名, 脱敏后的尾部)。"""
    try:
        p = Path(log_path) if log_path else None
        if p is None or not p.is_file():
            candidates = sorted(LOG_DIR.glob("run_*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            p = candidates[0] if candidates else None
        if p is None or not p.is_file():
            return "(无)", "(没找到运行日志)"
        raw = p.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(raw.splitlines()[-max_lines:])
        return p.name, redact(tail)
    except Exception as exc:  # noqa: BLE001
        return "(读取失败)", f"(日志读取失败: {type(exc).__name__})"


# ------------------------------------------------------------------ 本机路径
def _home() -> Path:
    return Path.home()


def _workbuddy_exe() -> Path | None:
    env = os.environ.get("WORKBUDDY_EXE")
    if env and Path(env).is_file():
        return Path(env)
    res = os.environ.get("WORKBUDDY_RESOURCES_PATH")
    if res:
        p = Path(res).parent / "WorkBuddy.exe"
        if p.is_file():
            return p
    p = _home() / "AppData/Local/Programs/WorkBuddy/WorkBuddy.exe"
    return p if p.is_file() else None


def _cli_paths() -> tuple[Path | None, Path | None]:
    """返回 (node.exe, codebuddy-lite-wb.mjs)。"""
    res = Path(os.environ.get("WORKBUDDY_RESOURCES_PATH")
               or (_home() / "AppData/Local/Programs/WorkBuddy/resources"))
    cli_root = res / "app.asar.unpacked/cli"
    cli = cli_root / "dist/codebuddy-lite-wb.mjs"
    if not cli.is_file():
        cli = cli_root / "bin/codebuddy"
    if not cli.is_file():
        return None, None
    env_node = os.environ.get("CODEBUDDY_NODE_BIN")
    if env_node and Path(env_node).is_file():
        return Path(env_node), cli
    versions = _home() / ".workbuddy/binaries/node/versions"
    if versions.is_dir():
        for node in sorted(versions.glob("*/node.exe"), reverse=True):
            if node.is_file():
                return node, cli
    return None, None


# ------------------------------------------------------------------ 唤起通道
def _to_clipboard(text: str) -> bool:
    """Windows 剪贴板（cmd 默认 GBK）；失败不影响主流程。"""
    if os.name != "nt" or os.environ.get("WB_HELP_CLIP") == "0":
        return False
    try:
        data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk", errors="replace")
        r = subprocess.run(["clip.exe"], input=data, timeout=4,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _workbuddy_running() -> bool:
    """桌面端已经在跑就别再启一个实例（Electron 多开会抢焦点）。"""
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe", "/NH"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=3, check=False)
        return b"workbuddy.exe" in (r.stdout or b"").lower()
    except Exception:  # noqa: BLE001
        return False


def _wake_gui() -> tuple[bool, str]:
    if os.environ.get("WB_HELP_GUI") == "0":
        return False, "已禁用（WB_HELP_GUI=0）"
    exe = _workbuddy_exe()
    if exe is None:
        return False, "没找到 WorkBuddy.exe"
    if _workbuddy_running():
        return True, "WorkBuddy 窗口已在运行，切过去即可"
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                         close_fds=True)
        return True, "已启动 WorkBuddy 窗口"
    except Exception as exc:  # noqa: BLE001
        return False, f"窗口启动失败：{type(exc).__name__}"


def _cli_failure_hint(log_path: Path) -> str:
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:].lower()
    except Exception:  # noqa: BLE001
        return ""
    if "authentication required" in tail or "please use /login" in tail:
        return "WorkBuddy CLI 未登录"
    if "module_not_found" in tail or "cannot find module" in tail:
        return "CLI 入口缺失（客户端升级后路径变了）"
    if "invalid api key" in tail or "unauthorized" in tail:
        return "账号授权失效"
    if "eaddrinuse" in tail or "address already in use" in tail:
        return "WorkBuddy 桌面端占用了 CLI 端口"
    return "CLI 提前退出且未写回结果"


def _wake_cli(prompt: str, response_path: Path, log_path: Path) -> tuple[bool, str]:
    if os.environ.get("WB_HELP_CLI") != "1":
        return False, "默认走对话通道（WB_HELP_CLI=1 可开启自动判断）"
    node, cli = _cli_paths()
    if node is None:
        return False, "本机没找到 WorkBuddy 无界面 CLI"
    # 桌面端常占着 48079，CLI 会直接 EADDRINUSE；换一个随机端口避开。
    port = str(os.environ.get("WB_HELP_CLI_PORT")
               or 49000 + int(time.time() * 1000) % 9000)
    parts = [str(node), str(cli), "-p", "--output-format", "json",
             "--port", port,
             "--permission-mode", "acceptEdits", "--tools", "Read,Edit,Write",
             "-y", "--max-turns", "6", "--no-session-persistence", prompt]
    try:
        with log_path.open("ab") as out:
            proc = subprocess.Popen(parts, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    close_fds=True)
    except Exception as exc:  # noqa: BLE001
        return False, f"CLI 启动失败: {type(exc).__name__}"

    # 6 秒快速失败：CLI 未登录/入口坏了就别干等，立刻转剪贴板通道。
    for _ in range(12):
        if response_path.exists() or proc.poll() is not None:
            break
        time.sleep(0.5)
    if proc.poll() is not None and not response_path.exists():
        return False, _cli_failure_hint(log_path)

    # 再等一小会儿拿判断结论（默认 20 秒），避免后台留野进程。
    try:
        wait = float(os.environ.get("WB_HELP_CLI_WAIT", "20"))
    except ValueError:
        wait = 20.0
    deadline = time.time() + max(0.0, wait)
    while time.time() < deadline:
        if response_path.exists():
            try:
                obj = json.loads(response_path.read_text(encoding="utf-8") or "{}")
                note = redact(str(obj.get("note") or obj.get("reason") or ""), 300)
                return True, f"WorkBuddy 判断：{note or '已写回 response 文件'}"
            except Exception:  # noqa: BLE001
                return True, "WorkBuddy 已写回结果（解析失败，见 response 文件）"
        if proc.poll() is not None:
            return False, _cli_failure_hint(log_path)
        time.sleep(0.5)
    if proc.poll() is None:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    return True, f"WorkBuddy CLI 已唤起，{int(wait)} 秒内未给出结论（已停止等待）"


def _throttled(seconds: float) -> bool:
    """10 分钟内只唤起一次；返回 True 表示这次应跳过唤起。"""
    try:
        marker = OUT_DIR / ".last_wake"
        now = time.time()
        if marker.is_file():
            last = float(marker.read_text(encoding="utf-8").strip() or "0")
            if now - last < seconds:
                return True
        marker.write_text(str(now), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return False


# ------------------------------------------------------------------ 主入口
def wake(reason: str, detail: str = "", rc: int | None = None,
         log_path: str | None = None, dry: bool = False) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    req_md = OUT_DIR / f"request_{ts}.md"
    resp_json = OUT_DIR / f"response_{ts}.json"
    cli_log = OUT_DIR / f"cli_{ts}.log"
    log_name, tail = _log_tail(log_path)

    head = (
        f"# U校园自动化助手 · 求助 WorkBuddy 判断\n\n"
        f"- 时间：{stamp}\n"
        f"- 问题：{redact(reason, 200)}\n"
        f"- 退出码：{rc if rc is not None else '未知'}\n"
        f"- 说明：{redact(detail, 600)}\n"
        f"- 日志文件：{log_name}（下面只附脱敏尾部）\n\n"
        f"请你判断原因并给出修复办法；能直接改代码就改。{_PRIVACY}\n\n"
        f"## 最近日志（已脱敏）\n\n```text\n{tail}\n```\n"
    )
    req_md.write_text(head, encoding="utf-8")

    brief = (
        f"【U校园自动化助手 · 请 WorkBuddy 判断】{stamp}\n"
        f"问题：{redact(reason, 200)}\n"
        f"退出码：{rc if rc is not None else '未知'}；说明：{redact(detail, 300)}\n"
        f"问题包（含脱敏日志）：{req_md}\n\n"
        f"最近日志尾部（已脱敏）：\n{tail[-1800:]}\n\n"
        f"请判断原因并给出修复办法；能直接改代码就改。{_PRIVACY}\n"
    )

    result = {"request": str(req_md), "response": str(resp_json),
              "clipboard": False, "gui": False, "cli": False, "note": ""}
    if dry:
        result["note"] = "dry：只生成问题包，未唤起"
        print(f"[WorkBuddy 兜底] 问题包已生成：{req_md}")
        return result
    if os.environ.get("WB_HELP") == "0":
        result["note"] = "已禁用（WB_HELP=0）"
        print(f"[WorkBuddy 兜底] {result['note']}；问题包：{req_md}")
        return result

    try:
        seconds = float(os.environ.get("WB_HELP_THROTTLE", "600"))
    except ValueError:
        seconds = 600.0
    if _throttled(seconds):
        result["note"] = f"{int(seconds)} 秒内已唤起过，本次只记录"
        print(f"[WorkBuddy 兜底] {result['note']}；问题包：{req_md}")
        return result

    cli_prompt = head + (
        f"\n## 响应要求\n\n把判断结果写成 JSON 对象并写到：{resp_json}\n"
        f'格式：{{"handled": true, "note": "原因与修复办法"}}。\n'
        f"handled 表示你是否已经把问题修好；判断为需要人工时写 false。\n"
        f"{_PRIVACY}\n"
    )
    cli_ok, cli_note = _wake_cli(cli_prompt, resp_json, cli_log)
    result["cli"] = cli_ok
    result["clipboard"] = _to_clipboard(brief)
    result["gui"], gui_note = _wake_gui()
    result["note"] = cli_note

    print()
    print("=" * 60)
    print("[WorkBuddy 兜底] 程序遇到自己解决不了的问题，已唤起 WorkBuddy 判断")
    print(f"  问题包  ：{req_md}")
    print(f"  自动判断：{cli_note}")
    print(f"  剪贴板  ：{'已复制问题摘要' if result['clipboard'] else '复制失败，请打开问题包'}")
    print(f"  窗口    ：{gui_note}")
    if result["clipboard"]:
        print("  → 切到 WorkBuddy 按 Ctrl+V，它会看到完整问题并给出判断。")
    print("=" * 60)
    print()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="唤起 WorkBuddy 判断本地问题")
    ap.add_argument("--reason", default="程序未能完成")
    ap.add_argument("--detail", default="")
    ap.add_argument("--rc", type=int, default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--dry", action="store_true", help="只生成问题包，不唤起")
    a = ap.parse_args()
    try:
        wake(a.reason, detail=a.detail, rc=a.rc, log_path=a.log, dry=a.dry)
    except Exception as exc:  # noqa: BLE001
        # 兜底模块绝不能把主程序带崩
        print(f"[WorkBuddy 兜底] 自身异常（已忽略）：{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
