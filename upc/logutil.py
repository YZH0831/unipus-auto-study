# -*- coding: utf-8 -*-
"""日志：控制台 + 文件双写，自动脱敏（密钥/手机号）。"""
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+"), r"\1***"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;'\"]+"), r"\1***"),
    (re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;'\"]+"), r"\1***"),
    (re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;'\"]+"), r"\1***"),
    (re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2"),
]


def mask(text):
    """脱敏：密钥、token、密码、手机号。"""
    s = str(text)
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s


class _MaskFormatter(logging.Formatter):
    def format(self, record):
        return mask(super().format(record))


def setup_logging(log_dir, level="INFO"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"run_{stamp}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = _MaskFormatter("%(asctime)s [%(levelname).1s] %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    console.setFormatter(fmt)
    root.addHandler(console)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_MaskFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(fh)

    # 第三方库降噪
    for noisy in ("urllib3", "playwright", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log = logging.getLogger("upc")
    log.info(f"日志文件: {log_file}")
    return log
