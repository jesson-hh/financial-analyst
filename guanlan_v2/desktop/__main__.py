# -*- coding: utf-8 -*-
"""入口:`pythonw -m guanlan_v2.desktop`。

仓根引导兜底:正常经 `-m` 启动时(快捷方式的「起始位置」= 仓根)仓根本就在
sys.path 上,但 2026-07-26 那次 9999 起不来的教训是——生产启动形态和测试
启动形态不一样时,没人会发现。这里无条件兜一手,代价是三行。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# import webview 失败,或 WebView2 运行时缺失,不该是双击图标后什么都不出的
# 静默失败 —— 那正是引导页本身要防的失败模式。原因必须落到这个文件上。
_CRASH_LOG = _REPO_ROOT / "var" / "desktop-shell-crash.log"


def main() -> int:
    def _body() -> None:
        import webview  # noqa: PLC0415 —— 延迟到 GUI 真要跑时才导

        from guanlan_v2.desktop.shell import create_shell

        create_shell(webview_module=webview).start()

    return _run_and_log_crashes(_body)


def _run_and_log_crashes(body: Callable[[], None]) -> int:
    try:
        body()
        return 0
    except Exception as exc:  # noqa: BLE001 —— 双击啥都不出正是要防的失败模式;必须留痕再退出
        _record_crash(exc)
        return 1


def _record_crash(exc: BaseException) -> None:
    import datetime
    import traceback

    try:
        _CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CRASH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- {datetime.datetime.now().isoformat(timespec='seconds')} ---\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:  # noqa: BLE001 —— 连崩溃日志都写不进去也不能让这个报告本身再崩一次
        pass


if __name__ == "__main__":
    raise SystemExit(main())
