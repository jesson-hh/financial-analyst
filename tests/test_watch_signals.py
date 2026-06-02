"""Tests for watch/signals.py — load_negative_warnings (B1, tdx_f10 warnings parquet).

全用 tmp parquet (不碰真实 parquet_root). 断言: max severity per code / 缺文件→{} /
坏 schema→{} / 空表→{}.
"""
from __future__ import annotations

import pandas as pd

from financial_analyst.watch.signals import load_negative_warnings


def _write(tmp_path, rows):
    p = tmp_path / "tdx_f10_warnings_latest.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_loads_dict_keyed_by_code(tmp_path):
    p = _write(tmp_path, [
        {"code": "SH600310", "event_date": "2026-05-26", "title": "立案调查", "severity": 3, "scanned_at": "2026-05-27"},
        {"code": "SH600052", "event_date": "2026-05-23", "title": "股东减持", "severity": 2, "scanned_at": "2026-05-27"},
    ])
    d = load_negative_warnings(p)
    assert d["SH600310"]["severity"] == 3
    assert d["SH600310"]["title"] == "立案调查"
    assert d["SH600310"]["event_date"] == "2026-05-26"
    assert d["SH600052"]["severity"] == 2


def test_max_severity_per_code(tmp_path):
    p = _write(tmp_path, [
        {"code": "SH600000", "event_date": "2026-05-20", "title": "风险提示", "severity": 1, "scanned_at": "x"},
        {"code": "SH600000", "event_date": "2026-05-26", "title": "立案", "severity": 3, "scanned_at": "x"},
    ])
    d = load_negative_warnings(p)
    assert d["SH600000"]["severity"] == 3      # highest severity kept
    assert "立案" in d["SH600000"]["title"]


def test_missing_file_returns_empty(tmp_path):
    assert load_negative_warnings(tmp_path / "nope.parquet") == {}


def test_bad_schema_returns_empty(tmp_path):
    p = tmp_path / "tdx_f10_warnings_latest.parquet"
    pd.DataFrame([{"foo": 1, "bar": 2}]).to_parquet(p, index=False)
    assert load_negative_warnings(p) == {}


def test_empty_df_returns_empty(tmp_path):
    p = tmp_path / "tdx_f10_warnings_latest.parquet"
    pd.DataFrame(columns=["code", "severity", "title", "event_date"]).to_parquet(p, index=False)
    assert load_negative_warnings(p) == {}


# ==========================================================================
# B2 — compute_vol_regime (ported pure) + RegimeProvider
# ==========================================================================
from financial_analyst.watch.signals import compute_vol_regime, RegimeProvider  # noqa: E402


def _distr_daily():
    # ret_20d ≈ +19% (close[-1]=119 vs close[-21]=100) AND tr_surge_60 high.
    close = pd.Series([100.0] * 41 + [100.0 + i for i in range(20)])   # 61 days
    tr = pd.Series([1.0] * 56 + [5.0] * 5)                             # ma5≫ma60 → surge
    return close, tr


def test_vol_regime_distr_from_daily():
    close, tr = _distr_daily()
    r = compute_vol_regime(close, tr, None)
    assert r["r9_distr"] is True
    assert r["regime_label"] == "distr"
    assert r["expected_spread_pp"] == -1.42


def test_vol_regime_neutral_insufficient_history():
    r = compute_vol_regime(pd.Series([100.0] * 10), pd.Series([1.0] * 10), None)
    assert r["regime_label"] == "neutral"
    assert r["super_distr"] is False


def test_vol_regime_super_distr_with_tail():
    close, tr = _distr_daily()
    # today's 48 5min bars: last-30min (idx 41→47) +5% on concentrated volume.
    closes = [10.0] * 42 + [10.1, 10.2, 10.3, 10.4, 10.45, 10.5]
    vols = [1.0] * 42 + [10.0] * 6
    bars = pd.DataFrame({"close": closes, "volume": vols})
    r = compute_vol_regime(close, tr, bars)
    assert r["r11_tail_surge"] is True
    assert r["super_distr"] is True
    assert r["regime_label"] == "super_distr"
    assert r["expected_spread_pp"] == -4.20


class _StubLoader:
    """Minimal loader: fetch_quote(day)→close, fetch_daily_basic→turnover_rate."""

    def __init__(self, close, tr):
        n = len(close)
        dates = pd.date_range("2026-01-01", periods=n).astype(str)
        self._q = pd.DataFrame({"trade_date": dates, "close": close})
        self._b = pd.DataFrame({"trade_date": dates, "turnover_rate": tr})

    def fetch_quote(self, code, start, end, freq="day"):
        return self._q

    def fetch_daily_basic(self, code, start, end):
        return self._b


def test_regime_provider_distr_via_stub_loader():
    close, tr = _distr_daily()
    rp = RegimeProvider(loader=_StubLoader(list(close), list(tr)))
    assert rp("SH600000", None)["regime_label"] == "distr"


def test_regime_provider_insufficient_neutral():
    rp = RegimeProvider(loader=_StubLoader([100.0] * 10, [1.0] * 10))
    assert rp("SH600000", None)["regime_label"] == "neutral"


def test_regime_provider_loader_error_neutral():
    class _Boom:
        def fetch_quote(self, *a, **k):
            raise RuntimeError("loader down")

        def fetch_daily_basic(self, *a, **k):
            raise RuntimeError("loader down")

    rp = RegimeProvider(loader=_Boom())
    assert rp("SH600000", None)["regime_label"] == "neutral"
