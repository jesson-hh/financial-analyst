# -*- coding: utf-8 -*-
"""入口:`pythonw -m guanlan_v2.desktop`。

仓根引导兜底:正常经 `-m` 启动时(快捷方式的「起始位置」= 仓根)仓根本就在
sys.path 上,但 2026-07-26 那次 9999 起不来的教训是——生产启动形态和测试
启动形态不一样时,没人会发现。这里无条件兜一手,代价是三行。
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    import webview  # noqa: PLC0415 —— 延迟到 GUI 真要跑时才导

    from guanlan_v2.desktop.shell import create_shell

    create_shell(webview_module=webview).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
