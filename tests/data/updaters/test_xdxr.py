"""Unit tests for financial_analyst.data.updaters.xdxr.

4 tests per spec §8:
  1. Happy path — mock PytdxClient, assert parquet written with correct schema.
  2. Empty codes list — stats total=0, no parquet written.
  3. Pytdx fail — RuntimeError captured, stats failed++, no crash.
  4. Schema contract — write + re-read parquet, verify all 18 columns + dtypes.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from financial_analyst.data.updaters.xdxr import XDXR_FIELDS, update_xdxr

# ──────────────────────── fixture data ────────────────────────────────────────

FIXTURE_ROWS_600519 = [
    OrderedDict(
        year=2002, month=7, day=25, category=1,
        name="除权除息", fenhong=6.0, songzhuangu=1.0,
        peigu=0.0, peigujia=0.0, suogu=None,
        panqianliutong=None, panhouliutong=None,
        qianzongguben=18500000.0, houzongguben=37000000.0,
        fenshu=18500000.0, xingquanjia=0.0,
    ),
    OrderedDict(
        year=2002, month=7, day=26, category=2,
        name="送配股上市", fenhong=None, songzhuangu=None,
        peigu=None, peigujia=None, suogu=None,
        panqianliutong=None, panhouliutong=None,
        qianzongguben=None, houzongguben=None,
        fenshu=18500000.0, xingquanjia=None,
    ),
    OrderedDict(
        year=2021, month=6, day=24, category=1,
        name="除权除息", fenhong=170.27, songzhuangu=0.0,
        peigu=0.0, peigujia=0.0, suogu=0.0,
        panqianliutong=1256197745.0, panhouliutong=1256197745.0,
        qianzongguben=1256197745.0, houzongguben=1256197745.0,
        fenshu=0.0, xingquanjia=0.0,
    ),
]

FIXTURE_ROWS_002594 = [
    OrderedDict(
        year=2020, month=7, day=1, category=1,
        name="除权除息", fenhong=2.5, songzhuangu=0.0,
        peigu=0.0, peigujia=0.0, suogu=0.0,
        panqianliutong=2324891000.0, panhouliutong=2324891000.0,
        qianzongguben=2324891000.0, houzongguben=2324891000.0,
        fenshu=0.0, xingquanjia=0.0,
    ),
]


def _make_client_mock(side_effects: dict):
    """Return a mock PytdxClient context manager.

    ``side_effects`` maps (mkt, code_num) tuples to return values or
    exceptions. E.g. {(1, '600519'): FIXTURE_ROWS_600519}.
    """
    mock_client = MagicMock()

    def _call(method, mkt, code_num):
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


# ──────────────────────── tests ───────────────────────────────────────────────


def test_xdxr_happy_path(tmp_path):
    """Mock PytdxClient to return fixture rows; assert parquet written with right schema."""
    mock_client = _make_client_mock({
        (1, "600519"): FIXTURE_ROWS_600519,
        (0, "002594"): FIXTURE_ROWS_002594,
    })

    with patch(
        "financial_analyst.data.updaters.xdxr.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_xdxr(tmp_path, ["SH600519", "SZ002594"], log_progress=False)

    assert stats["total"] == 2
    assert stats["ok"] == 2
    assert stats["failed"] == 0
    assert stats["new_rows"] == len(FIXTURE_ROWS_600519) + len(FIXTURE_ROWS_002594)

    out_path = tmp_path / "xdxr.parquet"
    assert out_path.exists(), "xdxr.parquet should have been written"

    df = pd.read_parquet(out_path)
    assert len(df) == stats["new_rows"]
    assert set(df["code"].unique()) == {"SH600519", "SZ002594"}

    # event_date must be a valid date string for well-formed rows
    dated = df[df["event_date"].notna()]
    assert (dated["event_date"].str.match(r"\d{4}-\d{2}-\d{2}")).all()


def test_xdxr_empty_codes(tmp_path):
    """Empty codes list → stats total=0, no parquet written, no crash."""
    with patch("financial_analyst.data.updaters.xdxr.PytdxClient") as mock_cls:
        stats = update_xdxr(tmp_path, [], log_progress=False)

    # PytdxClient should not even be instantiated for zero codes
    mock_cls.assert_not_called()

    assert stats["total"] == 0
    assert stats["ok"] == 0
    assert stats["failed"] == 0
    assert stats["new_rows"] == 0

    out_path = tmp_path / "xdxr.parquet"
    assert not out_path.exists(), "no parquet should be written for empty codes"


def test_xdxr_pytdx_fail(tmp_path):
    """Mock PytdxClient.call raises RuntimeError → stats failed++, function does not crash."""
    mock_client = _make_client_mock({
        (1, "600519"): RuntimeError("mock network failure"),
        (0, "002594"): FIXTURE_ROWS_002594,
    })

    with patch(
        "financial_analyst.data.updaters.xdxr.PytdxClient",
        return_value=mock_client,
    ):
        stats = update_xdxr(tmp_path, ["SH600519", "SZ002594"], log_progress=False)

    assert stats["total"] == 2
    assert stats["failed"] == 1
    assert stats["ok"] == 1
    # Partial success: 002594 rows should still be written
    out_path = tmp_path / "xdxr.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)
    assert len(df) == len(FIXTURE_ROWS_002594)
    assert list(df["code"].unique()) == ["SZ002594"]


def test_xdxr_schema_contract(tmp_path):
    """Write then re-read xdxr.parquet; verify all 18 columns and basic dtype constraints."""
    mock_client = _make_client_mock({
        (1, "600519"): FIXTURE_ROWS_600519,
    })

    with patch(
        "financial_analyst.data.updaters.xdxr.PytdxClient",
        return_value=mock_client,
    ):
        update_xdxr(tmp_path, ["SH600519"], log_progress=False)

    out_path = tmp_path / "xdxr.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)

    # All 18 columns must be present in the right order
    assert list(df.columns) == XDXR_FIELDS, (
        f"Column mismatch.\nExpected: {XDXR_FIELDS}\nGot:      {list(df.columns)}"
    )

    # dtype contracts per §4.1
    assert df["code"].dtype == object, "code should be string/object"
    assert df["year"].dtype in (int, "int64", "int32"), "year should be int"
    assert df["month"].dtype in (int, "int64", "int32"), "month should be int"
    assert df["day"].dtype in (int, "int64", "int32"), "day should be int"
    assert df["category"].dtype in (int, "int64", "int32"), "category should be int"
    assert df["name"].dtype == object, "name should be string/object"

    float_cols = [
        "fenhong", "peigujia", "songzhuangu", "peigu", "suogu",
        "panqianliutong", "panhouliutong", "qianzongguben", "houzongguben",
        "fenshu", "xingquanjia",
    ]
    for col in float_cols:
        assert df[col].dtype == float, f"{col} should be float, got {df[col].dtype}"

    # event_date must be object (string) column
    assert df["event_date"].dtype == object, "event_date should be string/object"

    # Spot-check known values from the first fixture row
    row0 = df[df["event_date"] == "2002-07-25"].iloc[0]
    assert row0["code"] == "SH600519"
    assert row0["fenhong"] == 6.0
    assert row0["songzhuangu"] == 1.0
    assert row0["category"] == 1
