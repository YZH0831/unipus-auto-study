# -*- coding: utf-8 -*-
"""弹窗收一次账号密码，加密保存到本机，以后不再询问。

不碰 state/ 里已有的登录态，所以即使你取消也不影响当前会话。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from api_run import gui_auth, save_credentials  # noqa: E402


def main():
    print("=" * 56)
    print("把 U校园 账号密码加密保存到本机（DPAPI）")
    print("=" * 56)
    u, p = gui_auth("")
    if not u or not p:
        print("未输入，已取消（现有登录态未受影响）")
        return 1
    if save_credentials(ROOT, u, p):
        print(f"已保存：{u[:3]}****{u[-2:]}")
        print("以后运行不会再问账号密码。")
        return 0
    print("保存失败：本机凭据存储不可用（检查 DPAPI 权限）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
