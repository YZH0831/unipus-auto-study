# -*- coding: utf-8 -*-
"""打包：白名单方式打 zip（避免把 .venv / 日志 / 密钥打进去）。

用法：python tools/package.py
产物：dist/unipus-auto-study-v<版本>.zip
"""
import fnmatch
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INCLUDE = [
    "main.py",
    "config.example.yaml",
    "requirements.txt",
    "VERSION",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "setup.bat",
    "start.bat",
    "run.bat",
    "run_dry.bat",
    "SetDeepSeekKey.bat",
    "_gen_bat.py",
    "upc/*.py",
    "tools/*.py",
    "tests/*.py",
    "docs/*.md",
    "data/.gitkeep",
    "state/.gitkeep",
]

EXCLUDE = [
    "*.pyc", "__pycache__/*", ".venv/*", "logs/*", "output/*",
    "state/storage_state.json", "state/progress.json",
    "config.yaml", "data/answers.json", "dist/*",
]


def version():
    p = ROOT / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


def main():
    ver = version()
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"unipus-auto-study-v{ver}.zip"

    files = []
    for pattern in INCLUDE:
        for p in ROOT.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if any(fnmatch.fnmatch(rel, ex) or fnmatch.fnmatch(p.name, ex)
                   for ex in EXCLUDE):
                continue
            files.append((p, rel))

    # 保留空目录占位
    for d in ("data", "state", "logs", "output"):
        (ROOT / d).mkdir(exist_ok=True)
        keep = ROOT / d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
        rel = f"{d}/.gitkeep"
        if not any(r == rel for _, r in files):
            files.append((keep, rel))

    if not files:
        print("没有要打包的文件")
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, rel in sorted(files, key=lambda x: x[1]):
            zf.write(p, f"unipus-auto-study/{rel}")

    size = out.stat().st_size / 1024
    print(f"[ OK ] 打包完成：{out}")
    print(f"       共 {len(files)} 个文件，{size:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
