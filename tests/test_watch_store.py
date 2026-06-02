"""Tests for watch/store.py — 推荐日志 parquet 读写 + ack (Task 2).

Single-process append with dedup on (ts, code, trigger_kind). All tests use
``tmp_path`` so they never touch the real parquet_root.
"""
from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst.watch.models import WatchRec
from financial_analyst.watch.store import (
    RECS_COLUMNS,
    ack_rec,
    append_rec,
    load_recs,
)


def _make_rec(**overrides) -> WatchRec:
    base = dict(
        code="SH600519",
        action="hold",
        reason="放量突破确认",
        trigger_kind="breakout_high",
        ts="2026-06-02 10:05:00",
        target_price=1800.0,
        stop_loss=1650.0,
        confidence=0.6,
    )
    base.update(overrides)
    return WatchRec(**base)


def test_columns_contract():
    """Schema must be exactly the 10 contracted columns, in order."""
    assert RECS_COLUMNS == [
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


def test_append_then_load_roundtrip(tmp_path):
    """append_rec writes a row; load_recs reads it back with all columns."""
    path = tmp_path / "watch_recommendations.parquet"
    rec = _make_rec()

    append_rec(path, rec)

    df = load_recs(path)
    assert len(df) == 1
    assert list(df.columns) == RECS_COLUMNS
    row = df.iloc[0]
    assert row["code"] == "SH600519"
    assert row["action"] == "hold"
    assert row["trigger_kind"] == "breakout_high"
    assert row["ts"] == "2026-06-02 10:05:00"
    assert row["target_price"] == 1800.0
    assert row["stop_loss"] == 1650.0
    assert row["confidence"] == 0.6
    assert row["reason"] == "放量突破确认"
    # user_action defaults to "none" before any ack
    assert row["user_action"] == "none"
    assert row["user_action_ts"] in ("", None) or pd.isna(row["user_action_ts"])


def test_append_creates_parent_dir(tmp_path):
    """append_rec should create missing parent directories."""
    path = tmp_path / "nested" / "deeper" / "watch_recommendations.parquet"
    append_rec(path, _make_rec())
    assert path.exists()
    assert len(load_recs(path)) == 1


def test_append_appends_multiple_rows(tmp_path):
    """Distinct recs accumulate (different code or trigger or ts)."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(code="SH600519"))
    append_rec(path, _make_rec(code="SZ002594", action="buy"))
    append_rec(path, _make_rec(code="SH600519", trigger_kind="volume_surge"))

    df = load_recs(path)
    assert len(df) == 3
    assert set(df["code"]) == {"SH600519", "SZ002594"}


def test_append_dedup_on_key(tmp_path):
    """Same (ts, code, trigger_kind) → dedup, keep last write."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(action="hold", reason="第一次"))
    # Same key, different payload — should overwrite, not add a row.
    append_rec(path, _make_rec(action="sell", reason="第二次"))

    df = load_recs(path)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["action"] == "sell"
    assert row["reason"] == "第二次"


def test_load_recs_missing_file_returns_empty(tmp_path):
    """Reading a non-existent log returns an empty, well-formed frame."""
    path = tmp_path / "does_not_exist.parquet"
    df = load_recs(path)
    assert df.empty
    assert list(df.columns) == RECS_COLUMNS


def test_load_recs_day_filter(tmp_path):
    """day= filters rows whose ts date-part matches."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(ts="2026-06-01 14:00:00", trigger_kind="t1"))
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", trigger_kind="t2"))
    append_rec(path, _make_rec(ts="2026-06-02 13:30:00", trigger_kind="t3"))

    df = load_recs(path, day="2026-06-02")
    assert len(df) == 2
    assert set(df["trigger_kind"]) == {"t2", "t3"}

    df_all = load_recs(path)
    assert len(df_all) == 3


def test_ack_rec_sets_user_action(tmp_path):
    """ack_rec stamps user_action + user_action_ts on the matching row."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", code="SH600519",
                               trigger_kind="breakout_high"))

    ok = ack_rec(path, ts="2026-06-02 10:05:00", code="SH600519",
                 user_action="confirm")
    assert ok is True

    df = load_recs(path)
    row = df.iloc[0]
    assert row["user_action"] == "confirm"
    # user_action_ts now populated (non-empty)
    assert isinstance(row["user_action_ts"], str) and row["user_action_ts"] != ""


def test_ack_rec_only_targets_matching_rows(tmp_path):
    """ack must not touch other (code/ts) rows."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", code="SH600519",
                               trigger_kind="breakout_high"))
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", code="SZ002594",
                               trigger_kind="volume_surge", action="buy"))

    ack_rec(path, ts="2026-06-02 10:05:00", code="SH600519", user_action="confirm")

    df = load_recs(path).set_index("code")
    assert df.loc["SH600519", "user_action"] == "confirm"
    assert df.loc["SZ002594", "user_action"] == "none"


def test_ack_rec_ignore_action(tmp_path):
    """user_action can be 'ignore' too."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", code="SH600519",
                               trigger_kind="breakout_high"))
    ack_rec(path, ts="2026-06-02 10:05:00", code="SH600519", user_action="ignore")
    df = load_recs(path)
    assert df.iloc[0]["user_action"] == "ignore"


def test_ack_rec_no_match_returns_false(tmp_path):
    """ack on a row that doesn't exist returns False and changes nothing."""
    path = tmp_path / "recs.parquet"
    append_rec(path, _make_rec(ts="2026-06-02 10:05:00", code="SH600519"))

    ok = ack_rec(path, ts="2026-06-02 10:05:00", code="SZ000001",
                 user_action="confirm")
    assert ok is False
    df = load_recs(path)
    assert df.iloc[0]["user_action"] == "none"


def test_ack_rec_missing_file_returns_false(tmp_path):
    """ack on a non-existent log returns False, does not raise."""
    path = tmp_path / "nope.parquet"
    assert ack_rec(path, ts="t", code="X", user_action="confirm") is False
