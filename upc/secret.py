# -*- coding: utf-8 -*-
"""本地密钥的安全存取（Windows DPAPI）。

设计目标：**硬盘上不出现明文密钥**。
  - 用 Windows DPAPI（CryptProtectData）加密，绑当前用户 + 当前机器
  - 换机器/换用户都解不开，别人拷走文件也没用
  - 日志里永远不打印密钥内容，只报长度/是否就绪

用法：
    from upc.secret import save_secret, load_secret
    save_secret("llm_key", "sk-xxx")     # 加密落盘
    k = load_secret("llm_key")           # 解密读取
"""
import base64
import ctypes
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SECRET_DIR = ROOT / "state"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes) -> _BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _protect(data: bytes) -> bytes:
    """DPAPI 加密（当前用户作用域）。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = _make_blob(data)
    blob_out = _BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0,
        ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(data: bytes) -> bytes:
    """DPAPI 解密。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = _make_blob(data)
    blob_out = _BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0,
        ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _path(name: str) -> Path:
    return _SECRET_DIR / f".{name}.dpapi"


def save_secret(name: str, value: str) -> bool:
    """加密保存。返回是否成功。不打印任何密钥内容。"""
    if not value:
        return False
    try:
        _SECRET_DIR.mkdir(parents=True, exist_ok=True)
        enc = _protect(value.encode("utf-8"))
        _path(name).write_bytes(base64.b64encode(enc))
        return True
    except Exception:  # noqa: BLE001
        return False


def load_secret(name: str) -> str:
    """解密读取；不存在或解不开返回空串。"""
    p = _path(name)
    if not p.exists():
        return ""
    try:
        enc = base64.b64decode(p.read_bytes())
        return _unprotect(enc).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def has_secret(name: str) -> bool:
    return bool(load_secret(name))


def clear_secret(name: str) -> bool:
    p = _path(name)
    try:
        if p.exists():
            p.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False
