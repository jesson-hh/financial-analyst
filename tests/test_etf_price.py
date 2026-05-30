"""Tests for etf_price updater (pytdx → cn_data_etf bin, single-process)."""
import numpy as np
import pytest

from financial_analyst.data.updaters import etf_price
from financial_analyst.data import bin_writer


def test_update_one_writes_bin(tmp_path, monkeypatch):
    etf_uri = tmp_path / "cn_data_etf"
    (etf_uri / "calendars").mkdir(parents=True)
    (etf_uri / "calendars" / "day.txt").write_text(
        "2026-05-28\n2026-05-29\n", encoding="utf-8"
    )
    bars = [
        {
            "datetime": "2026-05-28 15:00",
            "open": 4.9,
            "high": 5.0,
            "low": 4.8,
            "close": 4.92,
            "vol": 100,
            "amount": 49000,
        },
        {
            "datetime": "2026-05-29 15:00",
            "open": 4.92,
            "high": 4.95,
            "low": 4.90,
            "close": 4.94,
            "vol": 120,
            "amount": 59000,
        },
    ]
    monkeypatch.setattr(
        etf_price, "fetch_daily", lambda client, code, n_bars=800: bars
    )
    etf_price.update_etf_one(str(etf_uri), client=None, code="SH510300")
    si, arr = bin_writer.read_bin("SH510300", "close", "day", str(etf_uri))
    assert round(float(arr[-1]), 2) == 4.94


def test_update_one_returns_zero_on_empty(tmp_path, monkeypatch):
    """Empty bars (delisted / unknown ETF) → returns 0 without error."""
    etf_uri = tmp_path / "cn_data_etf"
    (etf_uri / "calendars").mkdir(parents=True)
    (etf_uri / "calendars" / "day.txt").write_text(
        "2026-05-28\n2026-05-29\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        etf_price, "fetch_daily", lambda client, code, n_bars=800: []
    )
    result = etf_price.update_etf_one(str(etf_uri), client=None, code="SH510300")
    assert result == 0


def test_update_one_vol_unit_conversion(tmp_path, monkeypatch):
    """vol in pytdx is shares; written bin must be in hands (÷100)."""
    etf_uri = tmp_path / "cn_data_etf"
    (etf_uri / "calendars").mkdir(parents=True)
    (etf_uri / "calendars" / "day.txt").write_text(
        "2026-05-28\n", encoding="utf-8"
    )
    bars = [
        {
            "datetime": "2026-05-28 15:00",
            "open": 4.9,
            "high": 5.0,
            "low": 4.8,
            "close": 4.92,
            "vol": 10000,   # 10000 shares = 100 hands
            "amount": 49000,
        }
    ]
    monkeypatch.setattr(
        etf_price, "fetch_daily", lambda client, code, n_bars=800: bars
    )
    etf_price.update_etf_one(str(etf_uri), client=None, code="SH510300")
    si, arr = bin_writer.read_bin("SH510300", "volume", "day", str(etf_uri))
    assert round(float(arr[0]), 2) == 100.0


def test_batch_aggregates_stats(tmp_path, monkeypatch):
    """update_etf_daily_batch returns correct ok/empty/total counts."""
    etf_uri = tmp_path / "cn_data_etf"
    (etf_uri / "calendars").mkdir(parents=True)
    (etf_uri / "calendars" / "day.txt").write_text(
        "2026-05-28\n2026-05-29\n", encoding="utf-8"
    )

    def fake_fetch(client, code, n_bars=800):
        if code == "SH510300":
            return [
                {"datetime": "2026-05-28 15:00", "open": 4.9, "high": 5.0,
                 "low": 4.8, "close": 4.92, "vol": 100, "amount": 49000}
            ]
        return []   # SZ159001 returns empty

    monkeypatch.setattr(etf_price, "fetch_daily", fake_fetch)
    stats = etf_price.update_etf_daily_batch(
        str(etf_uri), codes=["SH510300", "SZ159001"], client=None, progress=False
    )
    assert stats["total"] == 2
    assert stats["ok"] == 1
    assert stats["empty"] == 1
    assert stats["failed"] == 0
