"""Unit tests for financial_analyst.data.updaters.tick_history.

4 tests per spec §8:
  1. Happy path — mock PytdxClient, assert parquet written with correct schema.
  2. Empty codes list — stats total=0, no parquet written.
  3. Pytdx fail — RuntimeError captured, stats failed++, no crash.
  4. Schema contract — write + re-read parquet, verify 8 columns + dtypes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from financial_analyst.data.updaters.tick_history import TICK_FIELDS, update_tick_history

# ──────────────────────── fixture data ────────────────────────────────────────
# 4 native pytdx hist-tick fields (no `num` — that's today-only).
# vol values are in 手 (confirmed from Phase 0 recon sample: SH600519 hist vol=8,17,4).

FIXTURE_TICKS = [
    {"time": "09:30", "price": 100.50, "vol": 100, "buyorsell": 1},
    {"time": "09:31", "price": 100.55, "vol": 200, "buyorsell": 2},
    {"time": "09:32", "price": 100.48, "vol": 150, "buyorsell": 5},
]

# Two trading dates used in tests
DATE_A = "2026-05-27"
DATE_B = "2026-05-28"


def _make_client_mock(side_effects: dict):
    """Return a mock PytdxClient context manager.

    ``side_effects`` maps (method_name, mkt, code_num, start, count, date_int)
    tuples to return values or exceptions.  For simplicity we match only on
    (mkt, code_num, date_int) — the start and count arguments vary with
    pagination and are not used as keys here.

    The mock ``call`` inspects only the mkt / code_num / date_int positional
    args (args[1], args[2], args[5]) to look up the side-effect.
    """
    mock_client = MagicMock()

    def _call(method, mkt, code_num, start, count, date_int):
        key = (mkt, code_num, date_int)
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


def _date_int(date_str: str) -> int:
    return int(date_str.replace("-", ""))


# ──────────────────────── tests ───────────────────────────────────────────────


def test_tick_history_happy_path(tmp_path):
    """Mock PytdxClient to return fixture ticks; assert parquet written with right schema."""
    mock_client = _make_client_mock(
        {
            # SH600519 → (1, '600519'); two dates
            (1, "600519", _date_int(DATE_A)): FIXTURE_TICKS,
            (1, "600519", _date_int(DATE_B)): FIXTURE_TICKS,
        }
    )

    with patch(
        "financial_analyst.data.updaters.tick_history.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_tick_history(
            tmp_path,
            ["SH600519"],
            dates=[DATE_A, DATE_B],
            log_progress=False,
        )

    # Stats
    assert stats["total"] == 2, f"Expected 2 pairs (1 code × 2 dates), got {stats['total']}"
    assert stats["ok"] == 2
    assert stats["failed"] == 0
    assert stats["skipped"] == 0
    assert stats["new_rows"] == len(FIXTURE_TICKS) * 2  # 3 rows × 2 dates = 6

    # Parquet written
    out_path = tmp_path / "tick_history.parquet"
    assert out_path.exists(), "tick_history.parquet should have been written"

    df = pd.read_parquet(out_path)
    assert len(df) == stats["new_rows"]

    # code + date values
    assert set(df["code"].unique()) == {"SH600519"}
    assert set(df["date"].unique()) == {DATE_A, DATE_B}

    # idx is per-(code,date) 0-based sequence
    for date in [DATE_A, DATE_B]:
        subset = df[df["date"] == date].sort_values("idx")
        assert list(subset["idx"]) == list(range(len(FIXTURE_TICKS)))

    # vol stored as-is (already in 手, no /100 conversion)
    vols = df[df["date"] == DATE_A].sort_values("idx")["vol"].tolist()
    assert vols == [100, 200, 150]

    # num always 0 for hist ticks
    assert (df["num"] == 0).all(), "hist ticks should have num=0"


def test_tick_history_empty_codes(tmp_path):
    """codes=[] → stats total=0, no parquet written, PytdxClient never instantiated."""
    with patch("financial_analyst.data.updaters.tick_history.PytdxClient") as mock_cls:
        stats = update_tick_history(
            tmp_path,
            [],
            dates=[DATE_A],
            log_progress=False,
        )

    mock_cls.assert_not_called()

    assert stats["total"] == 0
    assert stats["ok"] == 0
    assert stats["failed"] == 0
    assert stats["skipped"] == 0
    assert stats["new_rows"] == 0

    out_path = tmp_path / "tick_history.parquet"
    assert not out_path.exists(), "no parquet should be written for empty codes"


def test_tick_history_pytdx_fail(tmp_path):
    """PytdxClient.call raises RuntimeError for one pair; that pair is failed, others continue."""
    mock_client = _make_client_mock(
        {
            # First code fails for DATE_A; succeeds for DATE_B
            (1, "600519", _date_int(DATE_A)): RuntimeError("mock network timeout"),
            (1, "600519", _date_int(DATE_B)): FIXTURE_TICKS,
        }
    )

    with patch(
        "financial_analyst.data.updaters.tick_history.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_tick_history(
            tmp_path,
            ["SH600519"],
            dates=[DATE_A, DATE_B],
            log_progress=False,
        )

    assert stats["total"] == 2
    assert stats["failed"] == 1
    assert stats["ok"] == 1
    assert stats["new_rows"] == len(FIXTURE_TICKS)  # only DATE_B succeeded

    # Partial write should still exist
    out_path = tmp_path / "tick_history.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert len(df) == len(FIXTURE_TICKS)
    assert set(df["date"].unique()) == {DATE_B}


def test_tick_history_schema_contract(tmp_path):
    """Write then re-read parquet; verify 8 columns + dtype constraints per spec §4.3."""
    mock_client = _make_client_mock(
        {
            (1, "600519", _date_int(DATE_A)): FIXTURE_TICKS,
        }
    )

    with patch(
        "financial_analyst.data.updaters.tick_history.PytdxClient",
        return_value=mock_client,
    ):
        update_tick_history(
            tmp_path,
            ["SH600519"],
            dates=[DATE_A],
            log_progress=False,
        )

    out_path = tmp_path / "tick_history.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)

    # All 8 columns present in the right order
    assert list(df.columns) == TICK_FIELDS, (
        f"Column mismatch.\nExpected: {TICK_FIELDS}\nGot:      {list(df.columns)}"
    )

    # dtype contracts per §4.3
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
    assert first["date"] == DATE_A
    assert first["time"] == "09:30"
    assert first["price"] == pytest.approx(100.50)
    assert first["vol"] == 100
    assert first["num"] == 0
    assert first["buyorsell"] == 1
    assert first["idx"] == 0
