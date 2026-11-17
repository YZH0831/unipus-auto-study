# -*- coding: utf-8 -*-
"""提交前密钥扫描：防止 api_key / 密码 / cookie 误传上公开仓库。

用法：python tools/check_secrets.py
退出码：0 = 干净，1 = 发现疑似密钥
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".venv", ".git", "__pycache__", "logs", "output", "state",
             "dist", "node_modules", ".idea", ".vscode"}
SKIP_EXT = {".pyc", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".ico",
            ".woff", ".woff2", ".ttf", ".wav", ".mp4", ".mp3"}

PATTERNS = [
    ("OpenAI/DeepSeek 密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("通用 api_key 赋值", re.compile(
        r"(?i)(api[_-]?key|apikey|access[_-]?token|secret[_-]?key)\s*[:=]\s*"
        r"['\"]([A-Za-z0-9_\-]{16,})['\"]")),
    ("疑似密码", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("Cookie 串", re.compile(r"(?i)(cookie\s*[:=]\s*['\"][^'\"]{40,})")),
]

# 这些行是示例/占位，不算泄露
WHITELIST = (
    "your_token", "your-key", "your_key", "你的token", "你的密钥",
    "xxx", "sk-你的", "${", "os.environ", "getenv", "example",
)

# 测试文件里用的是假数据，不该误报
SKIP_FILES = {"check_secrets.py", "config.example.yaml",
              "test_offline.py", "smoke_browser.py"}


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_EXT:
            continue
        if p.name in SKIP_FILES:
            continue
        yield p


def main():
    hits = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(w.lower() in line.lower() for w in WHITELIST):
                continue
            if line.strip().startswith("#") and "sk-" not in line:
                continue
            for name, pat in PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append((path.relative_to(ROOT), lineno, name,
                                 line.strip()[:100]))
                    break

    if not hits:
        print("[ OK ] 没发现疑似密钥，可以安全提交。")
        return 0

    print(f"[WARN] 发现 {len(hits)} 处疑似敏感信息：\n")
    for rel, lineno, name, snippet in hits:
        print(f"  {rel}:{lineno}  [{name}]")
        print(f"      {snippet}")
    print()
    print("请确认这些是不是真密钥。是真的就删掉/改成占位再提交。")
    print("注意：config.yaml 已在 .gitignore 里，通常不会误传；")
    print("      但如果你改过 .gitignore，务必复查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
