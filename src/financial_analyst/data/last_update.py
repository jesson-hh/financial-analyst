"""Track when each data type was last updated.

Persists to ``<workspace>/.last-update.json``. Used by:

  * ``fa data status``        — show "updated N hours ago" per data type
  * ``fa data refresh``       — decide what's stale + worth re-pulling
  * ``fa start`` ready panel  — show a banner if day data is > 24h stale

Format::

    {
      "day":          "2026-05-25T16:33:00+08:00",
      "5min":         "2026-05-25T16:34:00+08:00",
      "daily_basic":  "2026-05-25T16:35:00+08:00",
      "financials":   "2026-05-20T00:00:00+08:00",
      "f10":          "2026-05-25T12:00:00+08:00"
    }

Values are ISO 8601 strings. Unknown / missing data types are absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


# ──────────────────────── data type catalogue ────────────────────────


DATA_TYPES = (
    "day",           # daily OHLCV (pytdx)
    "5min",          # 5min OHLCV (pytdx)
    "daily_basic",   # PE/PB/MV/turnover_rate (Tencent realtime)
    "financials",    # income/balance/cashflow (Tushare opt-in)
    "f10",           # 公司大事 / 龙虎榜 / 主力追踪 (pytdx F10, zero token)
    "concepts",      # 同花顺 concept stocks + constituents (adata, zero token)
    "stock_basic",   # company master list (Tushare opt-in)
    "northbound",    # 沪深股通持仓快照 (akshare 东财, zero token)
)


# Subset that the public package can actually refresh today. Used by
# `fa data refresh` / staleness banners so they don't perpetually flag
# data types that have no updater yet.
IMPLEMENTED_TYPES = (
    "day",
    "5min",
    "daily_basic",
    "f10",           # since v1.0.7 (fa data update --include-f10)
    "concepts",      # since v1.0.7 (fa data update --include-concepts)
    "financials",    # since v1.0.7 (fa data update --include-financial, Tushare opt-in)
    "stock_basic",   # since v1.0.7 (fa data update --include-stock-basic, Tushare opt-in)
    "northbound",    # since v1.0.7 (fa data update --include-northbound, zero token)
)


# Staleness thresholds — hours after which we say "needs refresh"
STALE_THRESHOLD_HOURS = {
    "day":         24,   # trading days only — overnight stale
    "5min":        24,
    "daily_basic": 24,
    "financials":  24 * 30,  # quarterly cadence
    "f10":         24 * 3,   # event-driven, weekly OK
    "concepts":    24 * 7,   # weekly refresh sufficient
    "stock_basic": 24 * 30,  # company master list, monthly OK
    "northbound":  24 * 2,   # daily snapshot — stale after ~2 trading days
}


# ──────────────────────── paths ────────────────────────


def _file() -> Path:
    """The last-update tracker file inside the active workspace."""
    from financial_analyst.workspace import get_workspace
    return get_workspace() / ".last-update.json"


# ──────────────────────── read / write ────────────────────────


def read() -> Dict[str, str]:
    """Return ``{data_type: iso_timestamp}``. Empty dict if file missing / unreadable."""
    p = _file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def mark_updated(data_type: str) -> None:
    """Record ``now`` as the last-update timestamp for ``data_type``."""
    if data_type not in DATA_TYPES:
        # accept arbitrary tags too — caller may want custom
        pass
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state = read()
    state[data_type] = now
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    except Exception:
        pass


def mark_many(data_types) -> None:
    """Batch version — record ``now`` for several data types in one write."""
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state = read()
    for dt in data_types:
        state[dt] = now
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    except Exception:
        pass


# ──────────────────────── staleness queries ────────────────────────


def hours_since(data_type: str) -> Optional[float]:
    """Hours since ``data_type`` was last marked updated, or None if never."""
    state = read()
    ts = state.get(data_type)
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        return None
    now = datetime.now(when.tzinfo or timezone.utc)
    return (now - when).total_seconds() / 3600.0


def is_stale(data_type: str) -> bool:
    """True if ``data_type`` has never been updated OR is past its threshold."""
    threshold = STALE_THRESHOLD_HOURS.get(data_type, 24)
    h = hours_since(data_type)
    if h is None:
        return True
    return h >= threshold


def stale_types(implemented_only: bool = True) -> list:
    """Return the subset of DATA_TYPES that are currently stale.

    ``implemented_only=True`` (default) restricts to types we can actually
    refresh today — so callers like ``fa data refresh`` don't perpetually
    flag financials/f10 as needing work when no updater exists for them yet.
    """
    pool = IMPLEMENTED_TYPES if implemented_only else DATA_TYPES
    return [dt for dt in pool if is_stale(dt)]


# ──────────────────────── human-readable formatters ────────────────────────


def _format_age(hours: Optional[float]) -> str:
    """'5h ago' / '2d ago' / 'never'."""
    if hours is None:
        return "never"
    if hours < 1:
        m = max(1, int(hours * 60))
        return f"{m}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def status_summary(lang: str = "zh") -> list:
    """Return rows ``[(data_type, age_str, is_stale)]`` for rendering as a table."""
    state = read()
    rows = []
    for dt in DATA_TYPES:
        ts = state.get(dt)
        if ts is None:
            rows.append((dt, "never", True))
        else:
            h = hours_since(dt)
            rows.append((dt, _format_age(h), is_stale(dt)))
    return rows


__all__ = [
    "DATA_TYPES", "IMPLEMENTED_TYPES", "STALE_THRESHOLD_HOURS",
    "read", "mark_updated", "mark_many",
    "hours_since", "is_stale", "stale_types",
    "status_summary",
]
