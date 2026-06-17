"""推荐日志 parquet 读写 + 人工确认 (ack) — Realtime Watch Task 2.

Persists every ``WatchRec`` the盯盘 loop produces to a single Parquet log so the
推荐 feed survives a ``fa serve`` restart and can be replayed for后事复盘 /
未来 agent 准确率学习 (spec §9).

Schema (``RECS_COLUMNS``, 10 cols, order is part of the contract)::

    ts              str    "YYYY-MM-DD HH:MM:SS"  推荐生成时刻 (dedup key)
    code            str    "SH600519"                            (dedup key)
    trigger_kind    str    "breakout_high" / "stop_break" / ...  (dedup key)
    action          str    buy/add/hold/reduce/sell
    target_price    float
    stop_loss       float
    reason          str
    confidence      float  0..1
    user_action     str    "none" / "confirm" / "ignore"
    user_action_ts  str    "YYYY-MM-DD HH:MM:SS" or ""

Dedup is on ``(ts, code, trigger_kind)`` with ``keep="last"`` — re-emitting the
same推荐 (e.g. loop re-run within the same minute) overwrites rather than
duplicates.

**Single-process append only** — mirrors the project data-write rule (no
concurrent writers to one parquet). The盯盘 loop is the sole writer; the only
mutation from elsewhere is :func:`ack_rec`, invoked synchronously from the
``/watch/ack`` endpoint.

API::

    >>> from financial_analyst.watch.store import append_rec, load_recs, ack_rec
    >>> append_rec(path, rec)              # path optional -> default_recs_path()
    >>> df = load_recs(path, day="2026-06-02")
    >>> ack_rec(path, ts=..., code=..., user_action="confirm")
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from financial_analyst.watch.models import WatchRec

log = logging.getLogger(__name__)

# ──────────────────────── schema ───────────────────────────────────────────────

#: Persisted column order — part of the §9 data contract. Do not reorder.
RECS_COLUMNS = [
    "ts",
    "code",
    "trigger_kind",
    "action",
    "target_price",
    "stop_loss",
    "reason",
    "confidence",
    "user_action",
    "user_action_ts",
]

#: Rows are unique on this key; a later write with the same key overwrites.
_DEDUP_COLS = ["ts", "code", "trigger_kind"]

_DEFAULT_FILENAME = "watch_recommendations.parquet"


# ──────────────────────── path resolution ──────────────────────────────────────


def default_recs_path() -> Path:
    """Default recommendation-log location: ``parquet_root/watch_recommendations.parquet``.

    Resolved via :func:`financial_analyst.data.paths.get_data_paths` (env >
    loaders.yaml > ~/.financial-analyst > G:/stocks dev fallback) — never
    hardcode the parquet root (CLAUDE.md single-entry rule).
    """
    from financial_analyst.data.paths import get_data_paths

    return get_data_paths().parquet_root / _DEFAULT_FILENAME


def _resolve(path: Union[str, Path, None]) -> Path:
    return Path(path) if path is not None else default_recs_path()


# ──────────────────────── helpers ──────────────────────────────────────────────


def _empty_frame() -> pd.DataFrame:
    """A zero-row frame carrying the full schema (so ``columns`` is stable)."""
    return pd.DataFrame(columns=RECS_COLUMNS)


def _rec_to_row(rec: WatchRec) -> dict:
    """Project a ``WatchRec`` onto the persisted schema (drops ``error``)."""
    return {
        "ts": rec.ts,
        "code": rec.code,
        "trigger_kind": rec.trigger_kind,
        "action": rec.action,
        "target_price": float(rec.target_price),
        "stop_loss": float(rec.stop_loss),
        "reason": rec.reason,
        "confidence": float(rec.confidence),
        "user_action": "none",
        "user_action_ts": "",
    }


def _read_existing(path: Path) -> pd.DataFrame:
    """Read the log, returning an empty schema-frame on missing/corrupt file."""
    if not path.exists():
        return _empty_frame()
    try:
        df = pd.read_parquet(path)
    except Exception as exc:  # pragma: no cover — corrupt file is rare
        log.warning("watch.store: cannot read %s (%s); treating as empty", path, exc)
        return _empty_frame()
    # Backfill any missing columns (forward-compat with older logs).
    for col in RECS_COLUMNS:
        if col not in df.columns:
            df[col] = "none" if col == "user_action" else ("" if col == "user_action_ts" else None)
    return df[RECS_COLUMNS]


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    """Write via a sibling ``.tmp`` then rename — same pattern as the updaters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df[RECS_COLUMNS].reset_index(drop=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)


# ──────────────────────── public API ───────────────────────────────────────────


def append_rec(path: Union[str, Path, None], rec: WatchRec) -> None:
    """Append one ``WatchRec`` to the log, deduping on ``(ts, code, trigger_kind)``.

    Args:
        path: Parquet log path. ``None`` -> :func:`default_recs_path`.
        rec: The recommendation to persist. ``user_action`` is initialised to
            ``"none"`` (a later :func:`ack_rec` flips it).

    Single-process only — concurrent writers will lose rows (see module docstring).
    """
    p = _resolve(path)
    old = _read_existing(p)
    new = pd.DataFrame([_rec_to_row(rec)], columns=RECS_COLUMNS)
    combined = pd.concat([old, new], ignore_index=True) if len(old) else new
    combined = combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
    combined = combined.sort_values("ts", kind="stable").reset_index(drop=True)
    _atomic_write(combined, p)


def load_recs(path: Union[str, Path, None] = None, day: Optional[str] = None) -> pd.DataFrame:
    """Load the recommendation log.

    Args:
        path: Parquet log path. ``None`` -> :func:`default_recs_path`.
        day: Optional ``"YYYY-MM-DD"`` filter — keeps rows whose ``ts`` starts
            with that date.

    Returns:
        DataFrame with exactly :data:`RECS_COLUMNS`. Empty (but well-formed) if
        the file is missing or no row matches ``day``.
    """
    p = _resolve(path)
    df = _read_existing(p)
    if day is not None and len(df):
        df = df[df["ts"].astype(str).str.startswith(str(day))].reset_index(drop=True)
    return df


def ack_rec(
    path: Union[str, Path, None],
    ts: str,
    code: str,
    user_action: str,
) -> bool:
    """Stamp ``user_action`` (+ timestamp) on the row(s) matching ``(ts, code)``.

    Args:
        path: Parquet log path. ``None`` -> :func:`default_recs_path`.
        ts: Recommendation timestamp to match.
        code: Stock code to match.
        user_action: ``"confirm"`` / ``"ignore"`` (or any label).

    Returns:
        ``True`` if at least one row was updated; ``False`` if the file is
        missing or no row matched (never raises on a no-op).
    """
    p = _resolve(path)
    if not p.exists():
        return False
    df = _read_existing(p)
    if df.empty:
        return False

    mask = (df["ts"].astype(str) == str(ts)) & (df["code"].astype(str) == str(code))
    if not mask.any():
        return False

    df.loc[mask, "user_action"] = user_action
    df.loc[mask, "user_action_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write(df, p)
    return True


__all__ = [
    "RECS_COLUMNS",
    "default_recs_path",
    "append_rec",
    "load_recs",
    "ack_rec",
]
