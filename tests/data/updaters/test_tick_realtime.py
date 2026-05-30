"""Unit tests for financial_analyst.data.updaters.tick_realtime.

4 tests per spec:
  1. Happy path — mock PytdxClient, assert parquet written with correct schema + num populated.
  2. Empty codes list — stats total=0, no parquet written.
  3. Pytdx fail — RuntimeError captured, failed code counted, others continue.
  4. Schema contract — write + re-read parquet, verify 8 columns + dtypes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from financial_analyst.data.updaters.tick_realtime import TICK_FIELDS, update_tick_realtime

# ──────────────────────── fixture data ────────────────────────────────────────
# 5 native pytdx realtime-tick fields (includes `num` — today-only field).
# vol values are in 手 (following tick_history Phase 0 recon convention).

FIXTURE_TODAY_TICKS = [
    {"time": "09:30", "price": 100.50, "vol": 100, "num": 15, "buyorsell": 1},
    {"time": "09:31", "price": 100.55, "vol": 200, "num": 22, "buyorsell": 2},
    {"time": "09:32", "price": 100.48, "vol": 150, "num": 18, "buyorsell": 5},
]

# Fixed today date injected via mock to make tests deterministic
MOCK_TODAY = "2026-05-30"


def _make_client_mock(side_effects: dict):
    """Return a mock PytdxClient context manager.

    ``side_effects`` maps (mkt, code_num) tuples to return values or exceptions.
    The mock ``call`` inspects positional args[1] (mkt) and args[2] (code_num).
    """
    mock_client = MagicMock()

    def _call(method, mkt, code_num, start, count):
        key = (mkt, code_num)
        val = side_effects.get(key, [])
        if isinstance(val, Exception):
            raise val
        if isinstance(val, type) and issubclass(val, Exception):
            raise val(f"mock error for {key}")
        return val

    mock_client.call.side_effect = _call
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


# ──────────────────────── tests ───────────────────────────────────────────────


def test_tick_realtime_happy_path(tmp_path):
    """Mock PytdxClient returning today's ticks; assert parquet has rows + num field populated."""
    mock_client = _make_client_mock(
        {
            # SH600519 → (1, '600519')
            (1, "600519"): FIXTURE_TODAY_TICKS,
        }
    )

    with (
        patch(
            "financial_analyst.data.updaters.tick_realtime.PytdxClient",
            return_value=mock_client,
        ),
        patch(
            "financial_analyst.data.updaters.tick_realtime._today_str",
            return_value=MOCK_TODAY,
        ),
    ):
        stats = update_tick_realtime(
            tmp_path,
            ["SH600519"],
            log_progress=False,
        )

    # Stats
    assert stats["total"] == 1, f"Expected 1 code, got {stats['total']}"
    assert stats["ok"] == 1
    assert stats["failed"] == 0
    assert stats["new_rows"] == len(FIXTURE_TODAY_TICKS)

    # Parquet written
    out_path = tmp_path / "tick_realtime.parquet"
    assert out_path.exists(), "tick_realtime.parquet should have been written"

    df = pd.read_parquet(out_path)
    assert len(df) == len(FIXTURE_TODAY_TICKS)

    # code + date values
    assert set(df["code"].unique()) == {"SH600519"}
    assert set(df["date"].unique()) == {MOCK_TODAY}

    # idx is per-code 0-based sequence
    subset = df.sort_values("idx")
    assert list(subset["idx"]) == list(range(len(FIXTURE_TODAY_TICKS)))

    # vol stored as-is (already in 手, no /100 conversion)
    vols = subset["vol"].tolist()
    assert vols == [100, 200, 150]

    # num is populated for realtime ticks (the key differentiator vs hist)
    nums = subset["num"].tolist()
    assert nums == [15, 22, 18], f"num should come from fixture, got {nums}"

    # buyorsell values
    bos = subset["buyorsell"].tolist()
    assert bos == [1, 2, 5]


def test_tick_realtime_empty_codes(tmp_path):
    """codes=[] → stats total=0, no parquet written, PytdxClient never instantiated."""
    with patch("financial_analyst.data.updaters.tick_realtime.PytdxClient") as mock_cls:
        stats = update_tick_realtime(
            tmp_path,
            [],
            log_progress=False,
        )

    mock_cls.assert_not_called()

    assert stats["total"] == 0
    assert stats["ok"] == 0
    assert stats["failed"] == 0
    assert stats["new_rows"] == 0

    out_path = tmp_path / "tick_realtime.parquet"
    assert not out_path.exists(), "no parquet should be written for empty codes"


def test_tick_realtime_pytdx_fail(tmp_path):
    """PytdxClient raises for one code; that code counted as failed, others continue."""
    mock_client = _make_client_mock(
        {
            # SH600519 fails
            (1, "600519"): RuntimeError("mock network timeout"),
            # SZ002594 succeeds → (0, '002594')
            (0, "002594"): FIXTURE_TODAY_TICKS,
        }
    )

    with (
        patch(
            "financial_analyst.data.updaters.tick_realtime.PytdxClient",
            return_value=mock_client,
        ),
        patch(
            "financial_analyst.data.updaters.tick_realtime._today_str",
            return_value=MOCK_TODAY,
        ),
    ):
        stats = update_tick_realtime(
            tmp_path,
            ["SH600519", "SZ002594"],
            log_progress=False,
        )

    assert stats["total"] == 2
    assert stats["failed"] == 1
    assert stats["ok"] == 1
    assert stats["new_rows"] == len(FIXTURE_TODAY_TICKS)  # only SZ002594 succeeded

    # Partial write should still exist
    out_path = tmp_path / "tick_realtime.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert len(df) == len(FIXTURE_TODAY_TICKS)
    assert set(df["code"].unique()) == {"SZ002594"}


def test_tick_realtime_schema_contract(tmp_path):
    """Re-read parquet; verify 8 columns + dtype constraints."""
    mock_client = _make_client_mock(
        {
            (1, "600519"): FIXTURE_TODAY_TICKS,
        }
    )

    with (
        patch(
            "financial_analyst.data.updaters.tick_realtime.PytdxClient",
            return_value=mock_client,
        ),
        patch(
            "financial_analyst.data.updaters.tick_realtime._today_str",
            return_value=MOCK_TODAY,
        ),
    ):
        update_tick_realtime(
            tmp_path,
            ["SH600519"],
            log_progress=False,
        )

    out_path = tmp_path / "tick_realtime.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)

    # All 8 columns present in the right order
    assert list(df.columns) == TICK_FIELDS, (
        f"Column mismatch.\nExpected: {TICK_FIELDS}\nGot:      {list(df.columns)}"
    )

    # dtype contracts
    assert df["code"].dtype == object, "code should be string/object"
    assert df["date"].dtype == object, "date should be string/object"
    assert df["time"].dtype == object, "time should be string/object"
    assert df["price"].dtype == float, f"price should be float, got {df['price'].dtype}"

    int_cols = ["vol", "num", "buyorsell", "idx"]
    for col in int_cols:
        assert pd.api.types.is_integer_dtype(df[col]), (
            f"{col} should be integer dtype, got {df[col].dtype}"
        )

    # Spot-check known values from the fixture rows
    first = df.sort_values("idx").iloc[0]
    assert first["code"] == "SH600519"
    assert first["date"] == MOCK_TODAY
    assert first["time"] == "09:30"
    assert first["price"] == pytest.approx(100.50)
    assert first["vol"] == 100
    assert first["num"] == 15   # realtime-specific: num populated
    assert first["buyorsell"] == 1
    assert first["idx"] == 0
