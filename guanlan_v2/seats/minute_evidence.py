# -*- coding: utf-8 -*-
"""Live minute evidence adapter shared by Seats UI and the decision Agent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from .realtime_store import RealtimeBarStore, RealtimeStoreError, normalize_freq


class MinuteEvidenceError(RuntimeError):
    """Minute evidence could not be fetched, validated, or persisted."""


def _frame_from_snapshot(snapshot: Optional[dict]) -> pd.DataFrame:
    if not snapshot or not snapshot.get("bars"):
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "vol"])
    rows = [{
        "trade_date": row.get("bar_time"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "vol": row.get("volume"),
    } for row in snapshot["bars"]]
    frame = pd.DataFrame(rows)
    if len(frame):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        frame = frame.dropna(subset=["trade_date"]).sort_values("trade_date")
        frame = frame.drop_duplicates(subset=["trade_date"], keep="last").reset_index(drop=True)
    return frame


def fetch_live_snapshot(code: str, freq: str, *, n: Optional[int] = None,
                        store: Optional[RealtimeBarStore] = None,
                        feed_factory: Optional[Any] = None,
                        pulled_at: Optional[Any] = None) -> dict:
    """Fetch pytdx bars, persist validated rows, then read the exact snapshot.

    Returning the durable snapshot (instead of the transient DataFrame) makes
    the Agent and later replay use one canonical evidence set.
    """
    canonical_freq = normalize_freq(freq)
    count = int(n or (800 if canonical_freq == "1min" else 480))
    count = max(60, min(800, count))
    if feed_factory is None:
        from financial_analyst.watch.feed import WatchFeed
        feed_factory = WatchFeed
    target_store = store or RealtimeBarStore()
    feed = None
    try:
        feed = feed_factory()
        fn = feed.bars1 if canonical_freq == "1min" else feed.bars5
        frame = fn(code, count)
        if frame is None or len(frame) == 0:
            raise MinuteEvidenceError("minute_source_empty")
        stamp = pulled_at or datetime.now(timezone.utc)
        receipt = target_store.ingest(
            code, canonical_freq, frame, source="pytdx", pulled_at=stamp)
        if not receipt.get("ok") or not receipt.get("snapshot"):
            raise MinuteEvidenceError("minute_store_rejected_all_bars")
        snapshot = receipt["snapshot"]
        snapshot["ingest"] = {
            "accepted": receipt.get("accepted", 0),
            "rejected": receipt.get("rejected", 0),
            "quality_flagged": receipt.get("quality_flagged", 0),
            "rejections": receipt.get("rejections", []),
        }
        return snapshot
    except (RealtimeStoreError, MinuteEvidenceError):
        raise
    except Exception as exc:
        raise MinuteEvidenceError(f"minute_source_error:{type(exc).__name__}") from exc
    finally:
        if feed is not None:
            try:
                feed.close()
            except Exception:
                pass


def stored_snapshot(code: str, freq: str, *, limit: int = 800,
                    asof: Optional[Any] = None,
                    store: Optional[RealtimeBarStore] = None) -> Optional[dict]:
    target_store = store or RealtimeBarStore()
    return target_store.snapshot(code, freq, limit=limit, asof=asof)


def frame_from_snapshot(snapshot: Optional[dict], *, asof: Optional[Any] = None) -> pd.DataFrame:
    frame = _frame_from_snapshot(snapshot)
    if asof is not None and len(frame):
        anchor = pd.Timestamp(asof)
        if anchor.tzinfo is not None:
            anchor = anchor.tz_localize(None)
        frame = frame[frame["trade_date"] <= anchor].reset_index(drop=True)
    return frame


__all__ = [
    "MinuteEvidenceError",
    "fetch_live_snapshot",
    "frame_from_snapshot",
    "stored_snapshot",
]
