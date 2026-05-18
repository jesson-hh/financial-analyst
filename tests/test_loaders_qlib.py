"""Tests for QlibBinaryLoader — reads local Qlib binary data without network."""
import struct

import numpy as np
import pandas as pd
import pytest

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_qlib_dir(root):
    """Create a minimal Qlib-shaped directory with 1 stock and 10 trading days."""
    # Calendar: 10 business days starting 2026-05-01 (indices 0-9)
    (root / "calendars").mkdir(parents=True)
    dates = pd.date_range("2026-05-01", periods=10, freq="B").strftime("%Y-%m-%d").tolist()
    (root / "calendars" / "day.txt").write_text("\n".join(dates), encoding="utf-8")

    (root / "instruments").mkdir(parents=True)
    (root / "instruments" / "all.txt").write_text(
        "SH600519\t2026-05-01\t2026-05-14\n", encoding="utf-8"
    )

    stock_dir = root / "features" / "sh600519"
    stock_dir.mkdir(parents=True)

    def _write_bin(field: str, start_idx: int, values: list) -> None:
        """Write a Qlib-format .bin file: float32 header + float32 array."""
        with open(stock_dir / f"{field}.day.bin", "wb") as f:
            f.write(struct.pack("<f", float(start_idx)))
            f.write(np.array(values, dtype=np.float32).tobytes())

    # start_index=2 → skip first 2 calendar days, then 8 values covers days 2-9
    _write_bin("close",         2, [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7])
    _write_bin("open",          2, [9.9,  10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6])
    _write_bin("high",          2, [10.5] * 8)
    _write_bin("low",           2, [9.5] * 8)
    _write_bin("volume",        2, [1e6] * 8)
    _write_bin("amount",        2, [1e7] * 8)
    _write_bin("pe_ttm",        2, [25.0] * 8)
    _write_bin("pb",            2, [3.0]  * 8)
    _write_bin("total_mv",      2, [800_000.0] * 8)
    _write_bin("circ_mv",       2, [500_000.0] * 8)
    _write_bin("turnover_rate", 2, [3.5]  * 8)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_qlib_loader_reads_quote(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
    # start_index=2 → first 2 days skipped; remaining 8 days all within range
    assert len(df) == 8
    assert df["close"].iloc[0] == pytest.approx(10.0)
    assert df["close"].iloc[-1] == pytest.approx(10.7)
    assert "trade_date" in df.columns
    # Output field must be "vol", not "volume" — matches TushareLoader convention
    assert "vol" in df.columns
    assert "volume" not in df.columns


def test_qlib_loader_reads_daily_basic(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_daily_basic("SH600519", "2026-05-01", "2026-05-15")
    assert "pe_ttm" in df.columns
    assert "total_mv" in df.columns
    assert "turnover_rate" in df.columns
    assert df["pe_ttm"].iloc[-1] == pytest.approx(25.0)


def test_qlib_loader_missing_field_returns_subset(tmp_path):
    _setup_qlib_dir(tmp_path)
    # Remove one daily_basic field
    (tmp_path / "features" / "sh600519" / "pe_ttm.day.bin").unlink()
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_daily_basic("SH600519", "2026-05-01", "2026-05-15")
    assert "pe_ttm" not in df.columns
    assert "total_mv" in df.columns


def test_qlib_loader_unknown_stock_returns_empty(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_quote("SZ999999", "2026-05-01", "2026-05-15")
    assert df.empty


def test_qlib_loader_invalid_root_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        QlibBinaryLoader(provider_uri=str(tmp_path / "nonexistent"))


def test_qlib_loader_supports_a_share(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    assert loader.supports("a_share") is True
    assert loader.supports("us") is False


def test_qlib_loader_fetch_financials_returns_empty(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_financials("SH600519")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_qlib_loader_fetch_news_returns_empty_list(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    news = loader.fetch_news("SH600519", days=30)
    assert news == []


def test_qlib_loader_date_filter_respected(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    # Request only the first 3 trading days from start_index=2 (2026-05-05, 06, 07)
    df = loader.fetch_quote("SH600519", "2026-05-05", "2026-05-07")
    assert len(df) == 3
    assert df["close"].tolist() == pytest.approx([10.0, 10.1, 10.2])


def test_qlib_loader_trade_date_column_is_datetime(tmp_path):
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-15")
    assert pd.api.types.is_datetime64_any_dtype(df["trade_date"])


# ---------------------------------------------------------------------------
# 5min / multi-freq tests
# ---------------------------------------------------------------------------


def _setup_5min_data(root, dates_5min, code="sh600519", fields=None):
    """Build a 5min Qlib root with given timestamps and per-field values."""
    (root / "calendars").mkdir(parents=True, exist_ok=True)
    (root / "calendars" / "5min.txt").write_text(
        "\n".join(dates_5min), encoding="utf-8"
    )
    (root / "instruments").mkdir(parents=True, exist_ok=True)
    (root / "instruments" / "all.txt").write_text(
        f"{code.upper()}\t{dates_5min[0][:10]}\t{dates_5min[-1][:10]}\n",
        encoding="utf-8",
    )
    stock_dir = root / "features" / code
    stock_dir.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = {
            "open":   [10.0] * len(dates_5min),
            "high":   [10.5] * len(dates_5min),
            "low":    [9.5]  * len(dates_5min),
            "close":  [10.0 + 0.01 * i for i in range(len(dates_5min))],
            "volume": [1e5]  * len(dates_5min),
            "amount": [1e6]  * len(dates_5min),
        }
    for field, vals in fields.items():
        with open(stock_dir / f"{field}.5min.bin", "wb") as f:
            f.write(struct.pack("<f", float(0)))
            f.write(np.array(vals, dtype=np.float32).tobytes())


def test_qlib_loader_5min_freq(tmp_path):
    """fetch_quote with freq='5min' returns intraday bars."""
    day_root = tmp_path / "day"
    min_root = tmp_path / "5min"
    _setup_qlib_dir(day_root)
    timestamps = [
        "2026-05-01 09:30:00",
        "2026-05-01 09:35:00",
        "2026-05-01 09:40:00",
        "2026-05-01 10:00:00",
        "2026-05-01 15:00:00",
    ]
    _setup_5min_data(min_root, timestamps)

    loader = QlibBinaryLoader(provider_uri={"day": str(day_root), "5min": str(min_root)})

    # day still works
    df_day = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-10", freq="day")
    assert not df_day.empty

    # 5min returns all 5 bars on 2026-05-01
    df_5m = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-01", freq="5min")
    assert len(df_5m) == 5
    assert "close" in df_5m.columns
    assert "vol" in df_5m.columns        # output uses "vol", not "volume"
    assert "volume" not in df_5m.columns
    assert "trade_date" in df_5m.columns
    assert pd.api.types.is_datetime64_any_dtype(df_5m["trade_date"])


def test_qlib_loader_5min_date_filter(tmp_path):
    """5min bars from two different dates are correctly filtered by date."""
    day_root = tmp_path / "day"
    min_root = tmp_path / "5min"
    _setup_qlib_dir(day_root)
    timestamps = [
        "2026-05-01 09:30:00",
        "2026-05-01 15:00:00",
        "2026-05-06 09:30:00",
        "2026-05-06 15:00:00",
    ]
    _setup_5min_data(min_root, timestamps)

    loader = QlibBinaryLoader(provider_uri={"day": str(day_root), "5min": str(min_root)})
    df = loader.fetch_quote("SH600519", "2026-05-06", "2026-05-06", freq="5min")
    assert len(df) == 2
    # All returned bars belong to 2026-05-06
    assert (df["trade_date"].dt.date == pd.Timestamp("2026-05-06").date()).all()


def test_qlib_loader_5min_missing_freq_gracefully_empty(tmp_path):
    """str provider_uri (day-only) returns empty df for freq='5min', no error."""
    day_root = tmp_path / "day"
    _setup_qlib_dir(day_root)
    loader = QlibBinaryLoader(provider_uri=str(day_root))   # no 5min configured
    df_5m = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-01", freq="5min")
    assert isinstance(df_5m, pd.DataFrame)
    assert df_5m.empty


def test_qlib_loader_str_provider_only_day_works(tmp_path):
    """Backward compat: str provider_uri → day-only mode still works."""
    _setup_qlib_dir(tmp_path)
    loader = QlibBinaryLoader(provider_uri=str(tmp_path))
    df = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-10")
    assert not df.empty
    assert "close" in df.columns


def test_qlib_loader_dict_provider_requires_day(tmp_path):
    """dict provider_uri without 'day' key raises ValueError."""
    min_root = tmp_path / "5min"
    _setup_5min_data(min_root, ["2026-05-01 09:30:00"])
    with pytest.raises(ValueError, match="day"):
        QlibBinaryLoader(provider_uri={"5min": str(min_root)})


def test_qlib_loader_5min_missing_stock_returns_empty(tmp_path):
    """5min fetch for a stock that has no bin files returns empty df."""
    day_root = tmp_path / "day"
    min_root = tmp_path / "5min"
    _setup_qlib_dir(day_root)
    _setup_5min_data(min_root, ["2026-05-01 09:30:00"])
    loader = QlibBinaryLoader(provider_uri={"day": str(day_root), "5min": str(min_root)})
    df = loader.fetch_quote("SZ999999", "2026-05-01", "2026-05-01", freq="5min")
    assert df.empty


def test_tushare_loader_5min_returns_empty():
    """TushareLoader.fetch_quote with freq='5min' returns empty, no API call."""
    from financial_analyst.data.loaders.tushare import TushareLoader
    loader = TushareLoader(token="fake", enable_cache=False)
    df = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-01", freq="5min")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
