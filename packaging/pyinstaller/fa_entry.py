"""PyInstaller entry shim for financial-analyst CLI.

PyInstaller 不能直接打 `pyproject.toml::project.scripts` 注册的入口,
所以 wrap 一层. ``--entry-point`` 指向这个文件.
"""
from __future__ import annotations

import sys


def main() -> int:
    # Make sure we don't carry over a stale frozen sys.path
    from financial_analyst.cli import app
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
