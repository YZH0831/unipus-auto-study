# -*- coding: utf-8 -*-
"""大模型密钥设置（图形界面）。

命令行也能用：
    python tools/set_key_gui.py --status-line   # 给 start.bat 菜单显示状态
    python tools/set_key_gui.py --has-key       # 有可用密钥则退出码 0
    python tools/set_key_gui.py --set-key sk-xxx
    python tools/set_key_gui.py --show
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CFG = ROOT / "config.yaml"

PRESETS = {
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "Kimi (Moonshot)": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "智谱 GLM": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "本地 Ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
}


# ------------------------------------------------------------------ YAML IO
def _load_cfg_text():
    if not CFG.exists():
        return ""
    return CFG.read_text(encoding="utf-8")


def _get_llm_block(text):
    """从 yaml 文本里粗略提取 quiz.llm 段的值（不引入 yaml 依赖，GUI 也能跑）。"""
    info = {"enabled": False, "base_url": "", "api_key": "", "model": ""}
    m = re.search(r"^\s*llm:\s*$(.*?)(?=^\S|\Z)", text, re.M | re.S)
    if not m:
        return info
    block = m.group(1)
    for key in ("enabled", "base_url", "api_key", "model"):
        mm = re.search(rf"^\s*{key}:\s*(.+?)\s*$", block, re.M)
        if not mm:
            continue
        val = mm.group(1).strip().strip('"').strip("'")
        if key == "enabled":
            info[key] = val.lower() in ("true", "yes", "1")
        else:
            info[key] = val
    return info


def _set_llm_block(text, enabled, base_url, api_key, model):
    """就地改写 quiz.llm 段的四个字段，保留其它内容与注释。"""
    if not text:
        text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")

    lines = text.splitlines()
    # 找 llm: 起点
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^\s*llm:\s*$", ln):
            start = i
            break
    if start is None:
        # 没有就追加在 quiz 段末尾（退化处理：放文件末尾）
        lines += ["", "quiz:", "  llm:", f"    enabled: {str(enabled).lower()}",
                  f'    base_url: "{base_url}"', f'    api_key: "{api_key}"',
                  f'    model: "{model}"']
        return "\n".join(lines) + "\n"

    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    pad = " " * indent
    values = {
        "enabled": str(bool(enabled)).lower(),
        "base_url": f'"{base_url}"',
        "api_key": f'"{api_key}"',
        "model": f'"{model}"',
    }
    # 在 llm 段范围内替换
    i = start + 1
    seen = set()
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and (len(ln) - len(ln.lstrip())) < indent:
            break
        m = re.match(rf"^(\s*)(\w+):(.*)$", ln)
        if m and m.group(2) in values:
            key = m.group(2)
            lines[i] = f"{m.group(1)}{key}: {values[key]}"
            seen.add(key)
        i += 1
    # 缺的补进去
    missing = [k for k in values if k not in seen]
    if missing:
        insert_at = start + 1
        for k in missing:
            lines.insert(insert_at, f"{pad}{k}: {values[k]}")
            insert_at += 1
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 状态行
def _effective_key(info):
    """取实际生效的密钥：配置里有就用配置，否则从 DPAPI 加密存储读。

    加密存储见 upc/secret.py —— 硬盘上不落明文，只有本机本用户能解。
    """
    k = str(info.get("api_key") or "")
    if k:
        return k
    try:
        from upc.secret import load_secret
        return load_secret("llm_key") or ""
    except Exception:  # noqa: BLE001
        return ""


def status_line():
    """给 start.bat 菜单用的状态。**故意只用 ASCII**。

    原因：cmd 的 `for /f` 捕获子进程输出时按当前代码页（936/GBK）解码，
    而 Python 在管道里可能输出 UTF-8，中文会变成乱码并破坏 batch 解析。
    所以这里只吐 `ON model masked` / `READY` / `OFF`，中文由 bat 自己拼。
    """
    info = _get_llm_block(_load_cfg_text())
    key = _effective_key(info)
    if info["enabled"] and key:
        # 不输出任何密钥片段（连前 6 位/后 4 位也不给），只报模型名
        model = (info["model"] or "?").replace(" ", "")
        return f"ON {model}"
    if key:
        return "READY"
    return "OFF"


def status_text():
    """给人看的中文状态（用于终端直接运行，不走管道）。"""
    info = _get_llm_block(_load_cfg_text())
    if info["enabled"] and info["api_key"]:
        key = info["api_key"]
        masked = key[:6] + "***" + key[-4:] if len(key) > 12 else "***"
        return f"已启用 · {info['model'] or '?'} · {masked}"
    if info["api_key"]:
        return "[已填密钥但未启用] 打开设置勾选启用"
    return "[未配置] 按 K 填写密钥"


def has_key():
    info = _get_llm_block(_load_cfg_text())
    return bool(info["enabled"] and _effective_key(info))


# ------------------------------------------------------------------ 测活
def test_key(base_url, api_key, model, timeout=20):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "回复OK两个字"}],
        "max_tokens": 8,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        msg = data["choices"][0]["message"]["content"]
        return True, f"连通正常，模型回复：{msg[:30]}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:160]


# ------------------------------------------------------------------ GUI
def run_gui():
    import tkinter as tk
    from tkinter import messagebox, ttk

    text = _load_cfg_text()
    info = _get_llm_block(text)

    root = tk.Tk()
    root.title("U校园助手 - 大模型答题设置")
    root.geometry("620x430")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="选择服务商（会自动填 base_url 和 model）").grid(
        row=0, column=0, sticky="w")
    preset = tk.StringVar(value="DeepSeek")
    combo = ttk.Combobox(frm, textvariable=preset, values=list(PRESETS), state="readonly",
                         width=28)
    combo.grid(row=1, column=0, sticky="w", pady=(2, 12))

    ttk.Label(frm, text="API Base URL").grid(row=2, column=0, sticky="w")
    base_var = tk.StringVar(value=info["base_url"] or PRESETS["DeepSeek"][0])
    ttk.Entry(frm, textvariable=base_var, width=64).grid(
        row=3, column=0, sticky="w", pady=(2, 10))

    ttk.Label(frm, text="API Key").grid(row=4, column=0, sticky="w")
    key_var = tk.StringVar(value=info["api_key"])
    ttk.Entry(frm, textvariable=key_var, width=64, show="*").grid(
        row=5, column=0, sticky="w", pady=(2, 10))

    ttk.Label(frm, text="模型名").grid(row=6, column=0, sticky="w")
    model_var = tk.StringVar(value=info["model"] or PRESETS["DeepSeek"][1])
    ttk.Entry(frm, textvariable=model_var, width=32).grid(
        row=7, column=0, sticky="w", pady=(2, 10))

    enabled_var = tk.BooleanVar(value=info["enabled"])
    ttk.Checkbutton(frm, text="启用大模型答题（答不出题时调用）",
                    variable=enabled_var).grid(row=8, column=0, sticky="w", pady=(4, 10))

    status = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status, foreground="#0a7").grid(
        row=9, column=0, sticky="w", pady=(0, 8))

    def on_preset(*_):
        p = PRESETS.get(preset.get())
        if p:
            base_var.set(p[0])
            model_var.set(p[1])

    combo.bind("<<ComboboxSelected>>", on_preset)

    def do_test():
        status.set("测试中…")
        root.update_idletasks()
        ok, msg = test_key(base_var.get().strip(), key_var.get().strip(),
                           model_var.get().strip())
        status.set(("✓ " if ok else "✗ ") + msg)

    def do_save():
        cur = _load_cfg_text()
        new = _set_llm_block(cur, enabled_var.get(), base_var.get().strip(),
                             key_var.get().strip(), model_var.get().strip())
        try:
            CFG.write_text(new, encoding="utf-8")
            messagebox.showinfo("已保存", "配置已写入 config.yaml\n下次运行生效。")
            root.destroy()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc))

    btns = ttk.Frame(frm)
    btns.grid(row=10, column=0, sticky="w", pady=(6, 0))
    ttk.Button(btns, text="测试连通性", command=do_test).pack(side="left", padx=(0, 8))
    ttk.Button(btns, text="保存", command=do_save).pack(side="left", padx=(0, 8))
    ttk.Button(btns, text="取消", command=root.destroy).pack(side="left")

    tip = ("提示：不填 Key 也能用——答不出的题会走兜底策略，不会留空被平台拦截。\n"
           "DeepSeek 密钥申请：https://platform.deepseek.com/")
    ttk.Label(frm, text=tip, foreground="#888", justify="left").grid(
        row=11, column=0, sticky="w", pady=(14, 0))

    root.mainloop()


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status-line", action="store_true",
                    help="机器可读状态（纯 ASCII，供 start.bat 菜单使用）")
    ap.add_argument("--status-text", action="store_true", help="中文状态（给人看）")
    ap.add_argument("--has-key", action="store_true")
    ap.add_argument("--set-key", default="")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    if args.status_line:
        print(status_line())
        return 0
    if args.status_text:
        print(status_text())
        return 0
    if args.has_key:
        return 0 if has_key() else 1
    if args.show:
        info = _get_llm_block(_load_cfg_text())
        print("大模型配置：")
        for k, v in info.items():
            if k == "api_key" and v:
                v = v[:6] + "***" + v[-4:] if len(v) > 12 else "***"
            print(f"  {k:<10} = {v}")
        return 0
    if args.set_key:
        base, model = PRESETS["DeepSeek"]
        new = _set_llm_block(_load_cfg_text(), True, base, args.set_key.strip(), model)
        CFG.write_text(new, encoding="utf-8")
        print("已写入 config.yaml 并启用大模型。")
        return 0

    try:
        run_gui()
    except Exception as exc:  # noqa: BLE001
        print(f"图形界面启动失败（可能缺 tkinter）：{exc}")
        print("改用命令行：")
        print("  .venv\\Scripts\\python.exe tools\\set_key_gui.py --set-key sk-你的密钥")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
