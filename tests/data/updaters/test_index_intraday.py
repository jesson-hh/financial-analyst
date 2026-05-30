"""Unit tests for financial_analyst.data.updaters.index_intraday.

4 tests per spec §8:
  1. Happy path — mock PytdxClient, assert parquet rows have all 12 cols + 5 indices.
  2. Empty indices list — total=0, no parquet written.
  3. Pytdx fail — RuntimeError captured, that index marked failed, others continue.
  4. Schema contract — re-read parquet, verify all 12 columns + dtypes.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from financial_analyst.data.updaters.index_intraday import (
    INDEX_INTRADAY_FIELDS,
    INDICES,
    update_index_intraday,
)

# ──────────────────────── fixture data ────────────────────────────────────────
# Matching real Phase 0 recon sample: 上证指数 2026-05-29 rows.
# vol values are in 股 (shares) as returned by pytdx — the updater divides /100.

FIXTURE_BAR_1 = {
    "open": 4110.52, "close": 4105.69, "high": 4112.96, "low": 4105.69,
    "vol": 435063040, "amount": 43506307072.0,
    "year": 2026, "month": 5, "day": 29, "hour": 9, "minute": 31,
    "datetime": "2026-05-29 09:31",
    "up_count": 1199, "down_count": 948,
}
FIXTURE_BAR_2 = {
    "open": 4105.09, "close": 4100.90, "high": 4105.46, "low": 4100.51,
    "vol": 323706400, "amount": 32370642944.0,
    "year": 2026, "month": 5, "day": 29, "hour": 9, "minute": 32,
    "datetime": "2026-05-29 09:32",
    "up_count": 1201, "down_count": 998,
}
FIXTURE_BAR_3 = {
    "open": 4100.34, "close": 4104.50, "high": 4104.50, "low": 4099.85,
    "vol": 249264544, "amount": 24926455808.0,
    "year": 2026, "month": 5, "day": 29, "hour": 9, "minute": 33,
    "datetime": "2026-05-29 09:33",
    "up_count": 1325, "down_count": 893,
}
FIXTURE_BAR_4 = {
    "open": 4104.12, "close": 4101.77, "high": 4104.72, "low": 4101.11,
    "vol": 198000000, "amount": 19800000000.0,
    "year": 2026, "month": 5, "day": 29, "hour": 9, "minute": 34,
    "datetime": "2026-05-29 09:34",
    "up_count": 1010, "down_count": 870,
}
FIXTURE_BAR_5 = {
    "open": 4101.00, "close": 4099.50, "high": 4102.00, "low": 4098.00,
    "vol": 175000000, "amount": 17500000000.0,
    "year": 2026, "month": 5, "day": 29, "hour": 9, "minute": 35,
    "datetime": "2026-05-29 09:35",
    "up_count": 950, "down_count": 820,
}

FIXTURE_BARS = [FIXTURE_BAR_1, FIXTURE_BAR_2, FIXTURE_BAR_3, FIXTURE_BAR_4, FIXTURE_BAR_5]

# All 5 index qlib codes in registry order
ALL_CODES = [qc for qc, *_ in INDICES]


def _make_client_mock(side_effects: dict):
    """Return a mock PytdxClient context manager.

    ``side_effects`` maps (mkt, pytdx_code) tuples to return values or
    exceptions.  ``call`` inspects args[1] (mkt) and args[2] (pytdx_code).
    """
    mock_client = MagicMock()

    def _call(method, category, mkt, code_num, start, count):
        key = (mkt, code_num)
        val = side_effects.get(key, [])
        if isinstance(val, type) and issubclass(val, Exception):
            raise val(f"mock error for {key}")
        if isinstance(val, Exception):
            raise val
        return val

    mock_client.call.side_effect = _call
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


# Map from INDICES to (mkt, pytdx_code) for mock setup
_INDEX_MKT_CODE = {qc: (mkt, pc) for qc, _, mkt, pc in INDICES}


# ──────────────────────── tests ───────────────────────────────────────────────


def test_index_intraday_happy_path(tmp_path):
    """Mock PytdxClient to return 5 bars per index; verify parquet rows have all 12 cols."""
    side_effects = {_INDEX_MKT_CODE[qc]: FIXTURE_BARS for qc in ALL_CODES}
    mock_client = _make_client_mock(side_effects)

    with patch(
        "financial_analyst.data.updaters.index_intraday.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_index_intraday(
            tmp_path,
            indices=None,   # all 5
            n_days=1,
            bars_per_day=240,
            log_progress=False,
        )

    # Stats
    assert stats["total"] == 5, f"Expected 5 (5 indices × 1 day), got {stats['total']}"
    assert stats["ok"] == 5
    assert stats["failed"] == 0
    assert stats["new_rows"] == len(FIXTURE_BARS) * 5  # 5 bars × 5 indices = 25

    # Parquet written
    out_path = tmp_path / "index_intraday.parquet"
    assert out_path.exists(), "index_intraday.parquet should have been written"

    df = pd.read_parquet(out_path)
    assert len(df) == stats["new_rows"]

    # All 5 indices represented
    assert set(df["index_code"].unique()) == set(ALL_CODES)

    # All 12 columns present
    assert set(INDEX_INTRADAY_FIELDS).issubset(set(df.columns)), (
        f"Missing columns: {set(INDEX_INTRADAY_FIELDS) - set(df.columns)}"
    )

    # vol was converted from 股 → 手 (÷100)
    sh_rows = df[df["index_code"] == "SH000001"].sort_values("time")
    first_vol = sh_rows.iloc[0]["vol"]
    assert first_vol == FIXTURE_BAR_1["vol"] // 100, (
        f"Expected vol={FIXTURE_BAR_1['vol'] // 100} (÷100), got {first_vol}"
    )

    # date + time values
    assert (df["date"] == "2026-05-29").all()
    assert "09:31" in df["time"].values

    # up_count / down_count round-tripped
    row1 = df[(df["index_code"] == "SH000001") & (df["time"] == "09:31")].iloc[0]
    assert row1["up_count"] == 1199
    assert row1["down_count"] == 948

    # index_name present
    assert "上证指数" in df["index_name"].values


def test_index_intraday_empty_indices(tmp_path):
    """indices=[] → total=0, no parquet written, PytdxClient never instantiated."""
    with patch(
        "financial_analyst.data.updaters.index_intraday.PytdxClient"
    ) as mock_cls:
        stats = update_index_intraday(
            tmp_path,
            indices=[],
            n_days=1,
            log_progress=False,
        )

    mock_cls.assert_not_called()

    assert stats["total"] == 0
    assert stats["ok"] == 0
    assert stats["failed"] == 0
    assert stats["skipped"] == 0
    assert stats["new_rows"] == 0

    out_path = tmp_path / "index_intraday.parquet"
    assert not out_path.exists(), "no parquet should be written for empty indices"


def test_index_intraday_pytdx_fail(tmp_path):
    """PytdxClient.call raises for one index; that index is failed, others continue."""
    side_effects = {
        # SH000001 fails
        _INDEX_MKT_CODE["SH000001"]: RuntimeError("mock network timeout"),
        # SZ399001 succeeds
        _INDEX_MKT_CODE["SZ399001"]: FIXTURE_BARS,
    }
    mock_client = _make_client_mock(side_effects)

    with patch(
        "financial_analyst.data.updaters.index_intraday.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_index_intraday(
            tmp_path,
            indices=["SH000001", "SZ399001"],
            n_days=1,
            bars_per_day=240,
            log_progress=False,
        )

    assert stats["total"] == 2   # 2 indices × 1 day
    assert stats["failed"] == 1
    assert stats["ok"] == 1
    assert stats["new_rows"] == len(FIXTURE_BARS)  # only SZ399001 rows

    out_path = tmp_path / "index_intraday.parquet"
    assert out_path.exists(), "partial success should still write parquet"

    df = pd.read_parquet(out_path)
    assert len(df) == len(FIXTURE_BARS)
    assert set(df["index_code"].unique()) == {"SZ399001"}


def test_index_intraday_schema_contract(tmp_path):
    """Write then re-read parquet; verify all 12 columns + dtype constraints."""
    side_effects = {_INDEX_MKT_CODE["SH000001"]: FIXTURE_BARS}
    mock_client = _make_client_mock(side_effects)

    with patch(
        "financial_analyst.data.updaters.index_intraday.PytdxClient",
        return_value=mock_client,
    ):
        update_index_intraday(
            tmp_path,
            indices=["SH000001"],
            n_days=1,
            bars_per_day=240,
            log_progress=False,
        )

    out_path = tmp_path / "index_intraday.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)

    # All 12 columns in the right order
    assert list(df.columns) == INDEX_INTRADAY_FIELDS, (
        f"Column mismatch.\nExpected: {INDEX_INTRADAY_FIELDS}\nGot:      {list(df.columns)}"
    )

    # dtype contracts per §4.4
    assert df["index_code"].dtype == object, "index_code should be string/object"
    assert df["index_name"].dtype == object, "index_name should be string/object"
    assert df["date"].dtype == object, "date should be string/object"
    assert df["time"].dtype == object, "time should be string/object"

    float_cols = ["open", "high", "low", "close", "amount"]
    for col in float_cols:
        assert df[col].dtype == float, f"{col} should be float, got {df[col].dtype}"

    int_cols = ["vol", "up_count", "down_count"]
    for col in int_cols:
        assert pd.api.types.is_integer_dtype(df[col]), (
            f"{col} should be integer dtype, got {df[col].dtype}"
        )

    # Spot-check known values from fixture bar 1
    first = df.sort_values("time").iloc[0]
    assert first["index_code"] == "SH000001"
    assert first["index_name"] == "上证指数"
    assert first["date"] == "2026-05-29"
    assert first["time"] == "09:31"
    assert first["open"] == pytest.approx(4110.52)
    assert first["close"] == pytest.approx(4105.69)
    assert first["vol"] == FIXTURE_BAR_1["vol"] // 100   # 435063040 // 100 = 4350630
    assert first["up_count"] == 1199
    assert first["down_count"] == 948
