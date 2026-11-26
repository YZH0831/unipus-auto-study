# -*- coding: utf-8 -*-
"""安卓手机操作助手（ADB + uiautomator）。

用法：
  python mobile_uia.py texts                 列出当前界面所有可见文本
  python mobile_uia.py dump                  列出可点击节点（文本+坐标）
  python mobile_uia.py tap "学习"            点击文本/描述包含该串的节点
  python mobile_uia.py tapb "学习" 1         点击第 2 个匹配项（索引从0）
  python mobile_uia.py xy 640 2560           直接点坐标
  python mobile_uia.py shot name             截图到 shots/name.png
  python mobile_uia.py back / home / unlock  返回键 / 回桌面 / 亮屏解锁
  python mobile_uia.py swipe x1 y1 x2 y2 300 滑动
  python mobile_uia.py wait 2                等待 N 秒
  python mobile_uia.py launch               启动 U校园
  python mobile_uia.py appinfo               当前前台与分辨率
"""
from __future__ import annotations

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import adbenv

ADB = adbenv.ADB
BASE = Path(__file__).resolve().parent
SHOTS = BASE / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)


def sh(args: list[str], timeout: int = 40) -> str:
    p = subprocess.run([ADB] + args, capture_output=True, timeout=timeout)
    return (p.stdout or b"").decode("utf-8", "replace")


def dump_xml() -> ET.Element | None:
    """拿当前界面的 uiautomator 层级。"""
    for _ in range(2):
        raw = sh(["exec-out", "uiautomator", "dump", "/dev/tty"])
        i = raw.find("<?xml")
        j = raw.rfind("</hierarchy>")
        if i >= 0 and j > i:
            frag = raw[i:j + len("</hierarchy>")]
            try:
                return ET.fromstring(frag)
            except ET.ParseError as exc:
                print(f"[dump] XML 解析失败: {exc}")
        time.sleep(1)
    return None


def _center(bounds: str) -> tuple[int, int] | None:
    try:
        a, b = bounds.split("][")
        x1, y1 = (int(v) for v in a.lstrip("[").split(","))
        x2, y2 = (int(v) for v in b.rstrip("]").split(","))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:  # noqa: BLE001
        return None


def nodes(root: ET.Element):
    for n in root.iter("node"):
        lbl = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        rid = (n.get("resource-id") or "").strip()
        b = n.get("bounds") or ""
        c = _center(b)
        yield lbl, desc, rid, b, c, n


def cmd_texts() -> None:
    root = dump_xml()
    if root is None:
        print("拿不到界面")
        return
    seen = set()
    for lbl, desc, rid, b, c, n in nodes(root):
        t = lbl or desc
        if not t or t in seen:
            continue
        seen.add(t)
        flag = "可点" if n.get("clickable") == "true" else "  "
        print(f"{flag} {t[:60]:<60} {b}")


def cmd_dump() -> None:
    root = dump_xml()
    if root is None:
        print("拿不到界面")
        return
    for lbl, desc, rid, b, c, n in nodes(root):
        if n.get("clickable") != "true":
            continue
        t = (lbl or desc or rid or "").strip()
        if not t:
            continue
        print(f"{t[:55]:<55} {b}  rid={rid[-30:]}")


def find_nodes(root: ET.Element, key: str):
    out = []
    for lbl, desc, rid, b, c, n in nodes(root):
        if key in lbl or key in desc or key in rid:
            out.append((lbl or desc or rid, b, c, n))
    return out


def cmd_tap(key: str, idx: int = 0) -> None:
    root = dump_xml()
    if root is None:
        print("拿不到界面")
        return
    hits = find_nodes(root, key)
    if not hits:
        print(f"[tap] 找不到「{key}」")
        cmd_texts()
        return
    if idx >= len(hits):
        idx = 0
    label, b, c, n = hits[idx]
    print(f"[tap] 命中 {len(hits)} 个，用第 {idx} 个「{label[:40]}」 {b}")
    if c:
        sh(["shell", "input", "tap", str(c[0]), str(c[1])])
        time.sleep(1.2)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    a = sys.argv[1:]

    if a[0] == "texts":
        cmd_texts()
    elif a[0] == "dump":
        cmd_dump()
    elif a[0] == "tap":
        cmd_tap(a[1], int(a[2]) if len(a) > 2 else 0)
    elif a[0] == "tapb":
        cmd_tap(a[1], int(a[2]))
    elif a[0] == "xy":
        sh(["shell", "input", "tap", a[1], a[2]])
        time.sleep(1.0)
        print(f"[tap] {a[1]},{a[2]}")
    elif a[0] == "shot":
        name = a[1] if len(a) > 1 else "shot"
        p = SHOTS / f"{name}.png"
        data = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=40).stdout
        p.write_bytes(data)
        print(f"[shot] {p} {len(data)} bytes")
    elif a[0] == "back":
        sh(["shell", "input", "keyevent", "4"])
        time.sleep(1.0)
    elif a[0] == "home":
        sh(["shell", "input", "keyevent", "3"])
    elif a[0] == "unlock":
        sh(["shell", "input", "keyevent", "224"])   # 亮屏
        time.sleep(0.6)
        sh(["shell", "input", "swipe", "600", "2000", "600", "900", "300"])
        time.sleep(0.8)
        print("[unlock] 已尝试亮屏+上滑")
    elif a[0] == "swipe":
        sh(["shell", "input", "swipe"] + a[1:])
        time.sleep(0.8)
    elif a[0] == "wait":
        time.sleep(float(a[1]))
    elif a[0] == "launch":
        sh(["shell", "monkey", "-p", "cn.unipus.cloud",
            "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(6)
        print("[launch] U校园已启动")
    elif a[0] == "appinfo":
        print(sh(["shell", "dumpsys", "window"]).split("mCurrentFocus")[-1][:120])
        print(sh(["shell", "wm", "size"]), sh(["shell", "wm", "density"]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
