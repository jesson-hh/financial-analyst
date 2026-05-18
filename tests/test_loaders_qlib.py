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
