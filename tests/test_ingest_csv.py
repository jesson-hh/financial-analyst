"""Tests for financial_analyst.data.ingest.csv_ingester (CsvIngester)."""
import struct

import numpy as np
import pandas as pd
import pytest

from financial_analyst.data.ingest.csv_ingester import CsvIngester


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_long_csv(path, codes=("SH600519", "SZ000858"), n_days=10):
    rows = []
    for code in codes:
        for d in pd.date_range("2026-05-01", periods=n_days, freq="B"):
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": d.strftime("%Y%m%d"),
                    "open": 100,
                    "high": 105,
                    "low": 95,
                    "close": 102,
                    "volume": 1e6,
                    "amount": 1e8,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_csv_ingester_raises_without_code_info():
    with pytest.raises(ValueError, match="code_col"):
        CsvIngester(path_glob="*.csv", date_col="trade_date")


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_csv_ingester_long_format_discover(tmp_path):
    csv = tmp_path / "data.csv"
    _make_long_csv(csv)
    ing = CsvIngester(
        path_glob=str(csv),
        code_col="ts_code",
        date_col="trade_date",
        date_format="%Y%m%d",
    )
    info = ing.discover()
    assert info["n_codes"] == 2
    assert "SH600519" in info["codes_sample"]
    assert info["n_files"] == 1
    assert info["n_rows"] == 20


def test_csv_ingester_missing_ohlcv_field_in_discover(tmp_path):
    csv = tmp_path / "data.csv"
    df = pd.DataFrame(
        {
            "ts_code": ["SH600519"],
            "trade_date": ["2026-05-01"],
            "open": [100],
            "high": [105],
            "low": [95],
            "close": [102],
            # 'volume' and 'amount' intentionally absent
        }
    )
    df.to_csv(csv, index=False)
    ing = CsvIngester(path_glob=str(csv), code_col="ts_code", date_col="trade_date")
    info = ing.discover()
    # DEFAULT_OHLCV_MAP maps 'vol' -> 'volume' and 'amount' -> 'amount'
    missing = info["fields_missing"]
    assert "vol" in missing or "amount" in missing


def test_csv_ingester_raises_on_no_files(tmp_path):
    ing = CsvIngester(
        path_glob=str(tmp_path / "nonexistent_*.csv"),
        code_col="ts_code",
        date_col="trade_date",
    )
    with pytest.raises(FileNotFoundError):
        ing.discover()


# ---------------------------------------------------------------------------
# convert()
# ---------------------------------------------------------------------------


def test_csv_ingester_long_format_convert(tmp_path):
    csv = tmp_path / "data.csv"
    _make_long_csv(csv)
    target = tmp_path / "out"
    ing = CsvIngester(
        path_glob=str(csv),
        code_col="ts_code",
        date_col="trade_date",
        date_format="%Y%m%d",
    )
    result = ing.convert(target_root=target)
    assert result.n_instruments == 2
    assert result.n_dates == 10
    assert (target / "calendars" / "day.txt").exists()
    assert (target / "instruments" / "all.txt").exists()
    assert (target / "features" / "sh600519" / "close.day.bin").exists()
    assert (target / "features" / "sz000858" / "close.day.bin").exists()


def test_csv_ingester_glob_multi_files(tmp_path):
    _make_long_csv(tmp_path / "part1.csv", codes=("SH600519",))
    _make_long_csv(tmp_path / "part2.csv", codes=("SZ000858",))
    target = tmp_path / "out"
    ing = CsvIngester(
        path_glob=str(tmp_path / "part*.csv"),
        code_col="ts_code",
        date_col="trade_date",
        date_format="%Y%m%d",
    )
    result = ing.convert(target_root=target)
    assert result.n_instruments == 2
    assert (target / "features" / "sh600519" / "close.day.bin").exists()
    assert (target / "features" / "sz000858" / "close.day.bin").exists()


def test_csv_ingester_per_code_filenames(tmp_path):
    for code in ("SH600519", "SZ000858"):
        df = pd.DataFrame(
            {
                "trade_date": ["2026-05-01", "2026-05-02"],
                "open": [100, 101],
                "high": [105, 106],
                "low": [95, 96],
                "close": [102, 103],
                "volume": [1e6, 1e6],
                "amount": [1e8, 1e8],
            }
        )
        df.to_csv(tmp_path / f"{code}.csv", index=False)
    target = tmp_path / "out"
    # glob only SH* -> only SH600519 matches
    ing = CsvIngester(
        path_glob=str(tmp_path / "SH*.csv"),
        per_code_filenames=True,
        date_col="trade_date",
    )
    result = ing.convert(target_root=target)
    assert result.n_instruments == 1
    assert (target / "features" / "sh600519" / "close.day.bin").exists()
    assert not (target / "features" / "sz000858").exists()


def test_csv_ingester_missing_fields_partial_success(tmp_path):
    """CSV with only open/close should still produce those two .bin files."""
    csv = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "ts_code": ["SH600519"] * 3,
            "trade_date": ["2026-05-01", "2026-05-02", "2026-05-05"],
            "open": [100, 101, 102],
            "close": [102, 103, 104],
            # high/low/volume/amount absent
        }
    ).to_csv(csv, index=False)
    target = tmp_path / "out"
    ing = CsvIngester(path_glob=str(csv), code_col="ts_code", date_col="trade_date")
    result = ing.convert(target_root=target)
    assert result.n_instruments == 1
    # open and close should be written
    assert (target / "features" / "sh600519" / "open.day.bin").exists()
    assert (target / "features" / "sh600519" / "close.day.bin").exists()
    # high should NOT exist
    assert not (target / "features" / "sh600519" / "high.day.bin").exists()


def test_csv_ingester_bin_readable_by_struct(tmp_path):
    """Verify the binary header is readable the same way QlibBinaryLoader reads it."""
    csv = tmp_path / "data.csv"
    _make_long_csv(csv, codes=("SH600519",), n_days=5)
    target = tmp_path / "out"
    ing = CsvIngester(
        path_glob=str(csv),
        code_col="ts_code",
        date_col="trade_date",
        date_format="%Y%m%d",
    )
    ing.convert(target_root=target)
    bin_path = target / "features" / "sh600519" / "close.day.bin"
    with open(bin_path, "rb") as f:
        start_index = int(struct.unpack("<f", f.read(4))[0])
        data = np.frombuffer(f.read(), dtype=np.float32)
    assert start_index == 0
    assert len(data) == 5
    assert not np.any(np.isnan(data))
