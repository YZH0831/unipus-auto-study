# -*- coding: utf-8 -*-
"""独立登录弹窗（只用 tkinter，零其它依赖）。

由 api_run.py 调用：项目的 .venv 里没有 tkinter（精简版 Python），
所以拿系统带 tkinter 的解释器跑这个文件，把结果写进临时文件。

用法：python ask_auth.py <输出json路径> [预填账号]
"""
import json
import sys


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "auth.json"
    default_user = sys.argv[2] if len(sys.argv) > 2 else ""

    import tkinter as tk
    from tkinter import ttk

    result = {"username": "", "password": "", "cancelled": True}

    root = tk.Tk()
    root.title("U校园 - 账号登录")
    root.geometry("450x250")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass

    frm = ttk.Frame(root, padding=20)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="U校园 · 纯接口登录",
              font=("Microsoft YaHei", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frm, text="不启浏览器，不走网页「U校园认证」",
              foreground="#666").grid(row=1, column=0, columnspan=2,
                                      sticky="w", pady=(2, 16))

    ttk.Label(frm, text="手机号 / 账号").grid(row=2, column=0, sticky="w")
    u_var = tk.StringVar(value=default_user)
    u_entry = ttk.Entry(frm, textvariable=u_var, width=34)
    u_entry.grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 12))

    ttk.Label(frm, text="密码").grid(row=4, column=0, sticky="w")
    p_var = tk.StringVar()
    p_entry = ttk.Entry(frm, textvariable=p_var, width=34, show="*")
    p_entry.grid(row=5, column=0, columnspan=2, sticky="w", pady=(3, 12))

    tip = tk.StringVar(value="密码只用于本次登录，不会保存")
    ttk.Label(frm, textvariable=tip, foreground="#0a7").grid(
        row=6, column=0, columnspan=2, sticky="w")

    def on_ok(_e=None):
        u, p = u_var.get().strip(), p_var.get()
        if not u or not p:
            tip.set("账号和密码都要填写")
            return
        result.update(username=u, password=p, cancelled=False)
        root.destroy()

    def on_cancel():
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))
    ttk.Button(btns, text="取消", command=on_cancel).pack(side="right", padx=(8, 0))
    ttk.Button(btns, text="登录", command=on_ok).pack(side="right")

    root.bind("<Return>", on_ok)
    root.bind("<Escape>", lambda _e: on_cancel())
    (p_entry if default_user else u_entry).focus_set()

    root.mainloop()

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return 0 if not result["cancelled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
