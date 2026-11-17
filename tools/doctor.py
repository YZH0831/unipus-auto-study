# -*- coding: utf-8 -*-
"""环境自检：Python / 依赖 / 浏览器 / 配置 / 网络 / 登录态，一次查清。

用法：python tools/doctor.py
"""
import json
import os
import platform
import socket
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"
INFO = "[INFO]"

results = []


def add(level, title, detail=""):
    results.append((level, title, detail))
    print(f"{level} {title}")
    if detail:
        for line in str(detail).splitlines():
            print(f"       {line}")


def check_python():
    v = sys.version_info
    if v < (3, 9):
        add(FAIL, f"Python 版本 {v.major}.{v.minor}.{v.micro}（需要 3.9+）",
            "请升级 Python：https://www.python.org/downloads/")
        return False
    add(OK, f"Python {v.major}.{v.minor}.{v.micro}（{platform.machine()}）")
    return True


def check_deps():
    ok = True
    try:
        import yaml  # noqa: F401
        add(OK, f"PyYAML {getattr(yaml, '__version__', '?')}")
    except Exception:  # noqa: BLE001
        add(FAIL, "缺少 PyYAML", "pip install PyYAML")
        ok = False
    try:
        from importlib.metadata import version as _v
        ver = _v("playwright")
        add(OK, f"playwright {ver}")
    except ImportError:
        try:
            import playwright  # noqa: F401
            add(OK, "playwright (版本未知)")
        except Exception:  # noqa: BLE001
            add(FAIL, "缺少 playwright", "pip install playwright")
            ok = False
    except Exception:  # noqa: BLE001
        add(WARN, "playwright 版本读取失败（不影响使用）")
    return ok


def check_browsers():
    """找本机 Edge / Chrome 可执行文件。"""
    candidates = {
        "Edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "Chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
    }
    found = []
    for name, paths in candidates.items():
        for p in paths:
            if Path(p).exists():
                found.append((name, p))
                break
    if found:
        for name, p in found:
            add(OK, f"{name}: {p}")
        return True
    add(WARN, "没找到 Edge / Chrome",
        "playwright 需要本机浏览器。若都没有，执行：python -m playwright install chromium")
    return False


def check_config():
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        add(FAIL, "config.yaml 不存在", "从 config.example.yaml 复制一份")
        return None
    try:
        from upc.config import load_config
        cfg = load_config(cfg_path, strict=False)
        add(OK, "config.yaml 解析成功")
    except Exception as exc:  # noqa: BLE001
        add(FAIL, f"config.yaml 解析失败: {str(exc)[:150]}")
        return None

    # 逐项提示
    if cfg.course.url:
        add(OK, f"课程地址: {cfg.course.url[:90]}")
    elif cfg.course.name_contains:
        add(WARN, f"未填 course.url，将按课程名『{cfg.course.name_contains}』查找",
            "更稳的做法：直接粘贴课程学习页地址到 course.url")
    else:
        add(FAIL, "course.url 与 course.name_contains 都是空的",
            "至少填一个，否则程序不知道要学哪门课")

    add(INFO, f"时长模式: {cfg.duration.mode} | total_minutes={cfg.duration.total_minutes}")
    # 大模型是否可用要看「配置开关 + 实际 key」。
    # key 允许留空、由 DPAPI 加密存在 state/.llm_key.dpapi，
    # 直接看 cfg.quiz.llm.api_key 会误报「未启用」（明明刷题时能用）。
    # 这里和运行时 LLMClient.available 用同一套判断，避免误导。
    try:
        import logging
        from upc.llm import LLMClient
        _llm_ok = LLMClient(cfg, logging.getLogger("doctor")).available
    except Exception:  # noqa: BLE001
        _llm_ok = bool(cfg.quiz.llm.enabled and cfg.quiz.llm.api_key)
    add(INFO, f"答题策略: {cfg.quiz.strategy} | 大模型: "
              f"{'启用' if _llm_ok else '未启用'}")
    add(INFO, f"口语模块: {'启用' if cfg.speaking.enabled else '关闭'}")
    add(INFO, f"浏览器模式: {cfg.browser.mode} | headless={cfg.browser.headless}")

    if cfg.browser.headless:
        add(WARN, "headless=True", "刷学习时长时平台可能按页面可见性判断，建议改成 false")
    if cfg.duration.mode == "total" and float(cfg.duration.total_minutes) <= 0:
        add(FAIL, "total_minutes <= 0", "改成大于 0 的分钟数")
    return cfg


def check_network():
    """能不能连上 U校园。"""
    hosts = [("u.unipus.cn", 443), ("ucontent.unipus.cn", 443)]
    ok = True
    for host, port in hosts:
        try:
            socket.create_connection((host, port), timeout=8).close()
            add(OK, f"可连接 {host}:{port}")
        except Exception as exc:  # noqa: BLE001
            add(WARN, f"连不上 {host}:{port} — {str(exc)[:60]}",
                "检查网络 / 代理 / DNS；校园网有时要连 VPN")
            ok = False
    # HTTP 探测
    try:
        req = urllib.request.Request("https://u.unipus.cn/",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            add(OK, f"HTTPS 访问正常（HTTP {resp.status}）")
    except Exception as exc:  # noqa: BLE001
        add(WARN, f"HTTPS 访问异常: {str(exc)[:80]}")
    return ok


def check_state():
    p = ROOT / "state" / "storage_state.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            n = len(data.get("cookies") or [])
            size = p.stat().st_size
            add(OK, f"登录态文件存在（{n} 条 cookie，{size} 字节）")
        except Exception as exc:  # noqa: BLE001
            add(WARN, f"登录态文件损坏: {str(exc)[:80]}", "删掉 state/storage_state.json 重新登录")
    else:
        add(INFO, "还没有登录态文件（首次运行会让你登录一次）")


def check_dirs():
    for d in ("logs", "output", "data", "state"):
        p = ROOT / d
        p.mkdir(parents=True, exist_ok=True)
        add(OK, f"目录就绪: {d}/")
    ans = ROOT / "data" / "answers.json"
    if ans.exists():
        try:
            n = len(json.loads(ans.read_text(encoding="utf-8")) or {})
            add(INFO, f"本地题库已有 {n} 条答案")
        except Exception:  # noqa: BLE001
            add(WARN, "data/answers.json 读取失败（不影响运行，会被重建）")
    else:
        add(INFO, "本地题库为空（第一次答题后会自动积累）")


def main():
    print("=" * 62)
    print("            U校园自动化助手 - 环境自检")
    print("=" * 62)
    print()

    print("--- Python 与依赖 ---")
    check_python()
    deps_ok = check_deps()
    print()

    print("--- 浏览器 ---")
    check_browsers()
    print()

    print("--- 配置文件 ---")
    cfg = check_config()
    print()

    print("--- 网络 ---")
    check_network()
    print()

    print("--- 目录与登录态 ---")
    check_dirs()
    check_state()
    print()

    print("=" * 62)
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    if fails:
        print(f"结论：有 {len(fails)} 项必须修复，{len(warns)} 项建议检查")
        for _, title, _ in fails:
            print(f"  ✗ {title}")
        print()
        print("修完上面这些再运行 start.bat。")
        return 1
    if warns:
        print(f"结论：可以用，但有 {len(warns)} 项建议看看")
        for _, title, _ in warns:
            print(f"  ! {title}")
        return 0
    print("结论：一切正常，双击 start.bat 开始。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
