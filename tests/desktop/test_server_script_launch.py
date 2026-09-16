# -*- coding: utf-8 -*-
"""Desktop launch must preserve, never silently enable, the Telegram gate."""
from __future__ import annotations

from pathlib import Path

from guanlan_v2.desktop.supervisor import watchdog_env


_REPO = Path(__file__).resolve().parents[2]
_SERVER_PY = _REPO / "guanlan_v2" / "server.py"


def test_desktop_watchdog_preserves_an_explicit_telegram_opt_in_only() -> None:
    assert "GUANLAN_TELEGRAM_ENABLED" not in watchdog_env({"PATH": "x"})
    assert watchdog_env(
        {"PATH": "x", "GUANLAN_TELEGRAM_ENABLED": "1"}
    )["GUANLAN_TELEGRAM_ENABLED"] == "1"
    assert watchdog_env(
        {"PATH": "x", "GUANLAN_TELEGRAM_ENABLED": "0"}
    )["GUANLAN_TELEGRAM_ENABLED"] == "0"


def test_server_keeps_channel_imports_below_the_lazy_factory_boundary() -> None:
    source = _SERVER_PY.read_text(encoding="utf-8")
    prefix = source.split("def _build_telegram_channel_runtime", 1)[0]
    assert "guanlan_v2.channels" not in prefix
    assert "TelegramAdapter(" not in prefix
    assert "ChannelRuntime(" not in prefix
