"""Tests for financial_analyst.data.ingest.bin_writer."""
import struct

import numpy as np
import pandas as pd
import pytest

from financial_analyst.data.ingest.bin_writer import (
    write_calendar,
    write_field_bin,
    write_instruments,
)


def test_write_calendar_dedup_and_sort(tmp_path):
    dates = [
        pd.Timestamp("2026-05-15"),
        pd.Timestamp("2026-05-13"),
        pd.Timestamp("2026-05-15"),  # duplicate
        pd.Timestamp("2026-05-14"),
    ]
    n = write_calendar(tmp_path, dates)
    assert n == 3
    text = (tmp_path / "calendars" / "day.txt").read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert lines == ["2026-05-13", "2026-05-14", "2026-05-15"]


def test_write_calendar_creates_directory(tmp_path):
    root = tmp_path / "nested" / "root"
    write_calendar(root, [pd.Timestamp("2026-05-01")])
    assert (root / "calendars" / "day.txt").exists()


def test_write_instruments(tmp_path):
    ranges = [
        ("SH600519", pd.Timestamp("2010-01-04"), pd.Timestamp("2026-05-15")),
        ("SZ000858", pd.Timestamp("2015-01-05"), pd.Timestamp("2026-05-15")),
    ]
    n = write_instruments(tmp_path, ranges)
    assert n == 2
    text = (tmp_path / "instruments" / "all.txt").read_text(encoding="utf-8")
    assert "SH600519\t2010-01-04\t2026-05-15" in text
    assert "SZ000858\t2015-01-05\t2026-05-15" in text


def test_write_field_bin_roundtrip(tmp_path):
    calendar = [pd.Timestamp(d) for d in pd.date_range("2026-05-01", periods=10, freq="B")]
    series = pd.Series(
        [10.0, 10.1, 10.2, 10.3],
        index=pd.DatetimeIndex([calendar[2], calendar[3], calendar[4], calendar[5]]),
    )
    write_field_bin(tmp_path, "SH600519", "close", calendar, series)
    bin_path = tmp_path / "features" / "sh600519" / "close.day.bin"
    assert bin_path.exists()
    with open(bin_path, "rb") as f:
        start = int(struct.unpack("<f", f.read(4))[0])
        data = np.frombuffer(f.read(), dtype=np.float32)
    assert start == 2
    assert len(data) == 4
    assert data[0] == pytest.approx(10.0, abs=1e-5)


def test_write_field_bin_handles_gaps(tmp_path):
    """Missing dates between first and last get NaN."""
    calendar = [pd.Timestamp(d) for d in pd.date_range("2026-05-01", periods=10, freq="B")]
    # series has gap: only dates at index 2 and 5
    series = pd.Series([10.0, 13.0], index=pd.DatetimeIndex([calendar[2], calendar[5]]))
    write_field_bin(tmp_path, "SH600519", "close", calendar, series)
    with open(tmp_path / "features" / "sh600519" / "close.day.bin", "rb") as f:
        f.read(4)  # skip header
        data = np.frombuffer(f.read(), dtype=np.float32)
    # index 2..5 inclusive = 4 values
    assert len(data) == 4
    assert data[0] == pytest.approx(10.0, abs=1e-5)
    assert np.isnan(data[1])
    assert np.isnan(data[2])
    assert data[3] == pytest.approx(13.0, abs=1e-5)


def test_empty_series_writes_nothing(tmp_path):
    calendar = [pd.Timestamp("2026-05-01")]
    write_field_bin(tmp_path, "SH600519", "close", calendar, pd.Series(dtype=float))
    assert not (tmp_path / "features" / "sh600519" / "close.day.bin").exists()


def test_write_field_bin_code_lowercased(tmp_path):
    """Directory name should be code.lower() regardless of input case."""
    calendar = [pd.Timestamp(d) for d in pd.date_range("2026-05-01", periods=3, freq="B")]
    series = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(calendar))
    write_field_bin(tmp_path, "SH600519", "open", calendar, series)
    assert (tmp_path / "features" / "sh600519" / "open.day.bin").exists()
