"""Hermetic regression of causal technical fields; never opens live provider data."""
from __future__ import annotations

import hashlib
import os
import struct

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader, TECH_FIELD_MAP
from financial_analyst.data.loaders.technical import (
    TECH_DEFINITIONS, TECH_VERSION, compute_daily_technical,
)
from financial_analyst.factors.zoo.panel import PanelData, _merge_tech, _TECH_FIELDS


def _quote(n=180):
    dates = pd.bdate_range("2025-01-02", periods=n)
    close = 20.0 + np.arange(n) * .015 + np.sin(np.arange(n) / 3.0)
    return pd.DataFrame({"close": close, "vol": 1_000_000., "amount": close * 1_000_000.}, index=dates)


def _write_bin(root, code, field, values, offset=0):
    path = root / "features" / code.lower() / f"{field}.day.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<f", offset) + np.asarray(values, dtype="<f4").tobytes())
    return path


def _provider(tmp_path, quote, code="SH600667", future_days=0, legacy=True):
    (tmp_path / "calendars").mkdir(exist_ok=True)
    dates = quote.index
    if future_days:
        dates = dates.append(pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=future_days))
    (tmp_path / "calendars" / "day.txt").write_text("\n".join(dates.strftime("%Y-%m-%d")), encoding="utf-8")
    for col, field in (("close", "close"), ("vol", "volume"), ("amount", "amount")):
        if col in quote:
            values = np.r_[quote[col].to_numpy(), np.full(future_days, np.nan)]
            _write_bin(tmp_path, code, field, values)
    if legacy:
        for field in TECH_FIELD_MAP:
            # Incorrect legacy future values must not enter any feature.
            _write_bin(tmp_path, code, field, np.full(len(dates), 999.0))
    return QlibBinaryLoader(str(tmp_path))


def _hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.bin")}


def test_amount_definition_exact_and_not_volume_proxy():
    q = _quote(30)
    actual = compute_daily_technical(q)
    ret = q.close / q.close.shift(1) - 1
    expected = (ret.abs() / (q.amount + 1e-10)).rolling(20, min_periods=5).mean()
    np.testing.assert_allclose(actual.amihud_20, expected, equal_nan=True)
    assert actual.amihud_20.iloc[:5].isna().all()
    assert actual.amihud_20.iloc[5] > 0
    proxy = (ret.abs() / q.vol).rolling(20, min_periods=5).mean()
    assert not np.isclose(actual.amihud_20.iloc[-1], proxy.iloc[-1], rtol=1e-5, atol=0)


def test_legacy_ema_and_momentum_definitions_exact():
    q = _quote()
    out = compute_daily_technical(q)
    delta = q.close.diff()
    gain = delta.where(delta > 0, 0.)
    loss = (-delta).where(delta < 0, 0.)
    expected_rsi = 100 - 100 / (1 + gain.ewm(span=14, adjust=False).mean() / (loss.ewm(span=14, adjust=False).mean() + 1e-10))
    dif = q.close.ewm(span=12, adjust=False).mean() - q.close.ewm(span=26, adjust=False).mean()
    np.testing.assert_allclose(out.rsi_14, expected_rsi)
    np.testing.assert_allclose(out.macd_signal, dif)
    assert not np.isclose(out.macd_signal.iloc[-1], dif.ewm(span=9, adjust=False).mean().iloc[-1])
    for n in (20, 60, 120):
        np.testing.assert_allclose(out[f"mom_{n}"], q.close / q.close.shift(n) - 1, equal_nan=True)


def test_helper_does_not_fill_gaps_or_synthesise_amount():
    q = _quote()
    q.loc[q.index[140], "close"] = np.nan
    q.loc[q.index[141], "amount"] = 0.
    q.loc[q.index[142], "amount"] = -1.
    before = q.copy(deep=True)
    out = compute_daily_technical(q)
    assert out.loc[q.index[140]].isna().all()
    assert out.amihud_20.iloc[141:143].isna().all()
    assert_frame_equal(q, before)
    absent = compute_daily_technical(q.drop(columns="amount"))
    assert absent.amihud_20.isna().all()
    assert np.isfinite(absent.rsi_14.iloc[-1])


@pytest.mark.parametrize("bad_index", ["duplicate", "unsorted"])
def test_helper_rejects_invalid_time_axis(bad_index):
    q = _quote(10)
    q = pd.concat([q, q.iloc[-1:]]) if bad_index == "duplicate" else q.iloc[::-1]
    with pytest.raises(ValueError, match="unique increasing"):
        compute_daily_technical(q)


def test_loader_ignores_stale_future_bins_and_preserves_raw_audit(tmp_path):
    q = _quote()
    loader = _provider(tmp_path, q, future_days=30)
    before = _hashes(tmp_path)
    out = loader.fetch_tech("SH600667", str(q.index[0]), "2027-01-01")
    assert out.trade_date.max() == q.index[-1]
    assert set(TECH_DEFINITIONS).issubset(out.columns)
    assert (loader._read_bin("SH600667", "amihud_20") == 999.).all()
    assert out.amihud_20.max() < 1e-6
    assert _hashes(tmp_path) == before
    prov = out.attrs["technical_provenance"]
    assert prov["schema"] == TECH_VERSION
    assert prov["last_source_date"] == str(q.index[-1].date())
    assert "actual amount" in prov["source"]
    assert "amount.day.bin" in " ".join(prov["source_file_signatures"])


def test_narrow_start_reuses_full_warmup_and_cache_is_copy_safe(tmp_path):
    q = _quote()
    loader = _provider(tmp_path, q)
    full = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    narrow = loader.fetch_tech("SH600667", str(q.index[-10]), str(q.index[-1]))
    assert narrow.attrs["technical_provenance"]["cache"] == "hit"
    assert_frame_equal(full.tail(10).reset_index(drop=True), narrow, check_flags=False)
    narrow.loc[:, "rsi_14"] = -999.
    narrow.attrs["technical_provenance"]["definitions"]["rsi_14"] = "tampered"
    clean = loader.fetch_tech("SH600667", str(q.index[-1]), str(q.index[-1]))
    assert clean.rsi_14.iloc[0] >= 0
    assert clean.attrs["technical_provenance"]["definitions"]["rsi_14"] != "tampered"


def test_future_perturbation_and_physical_truncation_do_not_change_past(tmp_path):
    q = _quote(200)
    cutoff = q.index[160]
    loader = _provider(tmp_path, q)
    before = loader.fetch_tech("SH600667", str(q.index[0]), str(cutoff))
    changed = q.copy()
    changed.loc[changed.index > cutoff, "close"] *= 9
    changed.loc[changed.index > cutoff, "amount"] *= 123
    for col, field in (("close", "close"), ("amount", "amount")):
        _write_bin(tmp_path, "SH600667", field, changed[col])
    after = loader.fetch_tech("SH600667", str(q.index[0]), str(cutoff))
    assert_frame_equal(before, after)
    truncated = tmp_path / "truncated"
    truncated.mkdir()
    loader2 = _provider(truncated, q.loc[:cutoff], legacy=False)
    assert_frame_equal(before, loader2.fetch_tech("SH600667", str(q.index[0]), str(cutoff)))


def test_cache_invalidates_revised_amount_and_is_bounded(tmp_path):
    q = _quote()
    loader = _provider(tmp_path, q)
    loader._tech_cache_max_entries = 1
    out = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    path = _write_bin(tmp_path, "SH600667", "amount", q.amount * 2)
    # Explicit ns advance makes same-size revision deterministic on every FS.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    revised = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert revised.attrs["technical_provenance"]["cache"] == "miss"
    assert revised.amihud_20.iloc[-1] == pytest.approx(out.amihud_20.iloc[-1] / 2, rel=1e-7)
    assert len(loader._tech_cache) == 1
    loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-2]))
    assert len(loader._tech_cache) == 1
    assert 0 < loader._tech_cache_bytes <= loader._tech_cache_max_bytes


def test_calendar_append_refreshes_existing_loader(tmp_path):
    q = _quote()
    loader = _provider(tmp_path, q.iloc[:-1])
    first = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert first.trade_date.max() == q.index[-2]
    _provider(tmp_path, q)
    latest = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert latest.trade_date.max() == q.index[-1]
    assert latest.attrs["technical_provenance"]["cache"] == "miss"


def test_loader_missing_amount_never_uses_panel_synthetic_proxy(tmp_path):
    q = _quote()
    loader = _provider(tmp_path, q.drop(columns="amount"))
    out = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert out.amihud_20.isna().all()
    assert np.isfinite(out.mom_120.iloc[-1])


def test_dual_units_correct_actual_amount_without_writing_source(tmp_path):
    q = _quote(30)
    q["vol"] /= 100
    q["amount"] /= 1000
    loader = _provider(tmp_path, q)
    before = _hashes(tmp_path)
    out = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    raw = loader._build_df("SH600667", {"close": "close", "amount": "amount"}, str(q.index[0]), str(q.index[-1])).set_index("trade_date")
    raw["amount"] *= 1000
    expected = compute_daily_technical(raw)
    np.testing.assert_allclose(out.amihud_20, expected.amihud_20, equal_nan=True)
    assert out.attrs["technical_provenance"]["amount_rows_normalised"] == len(q)
    assert _hashes(tmp_path) == before


def test_new_listing_has_missing_lookback_instead_of_fabricated_values(tmp_path):
    q = _quote(4)
    loader = _provider(tmp_path, q)
    out = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert out.amihud_20.isna().all()
    assert out.mom_120.isna().all()


def test_panel_exact_dates_no_cross_stock_fill_and_provenance_survives():
    dates = pd.bdate_range("2026-08-31", periods=3)
    idx = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["datetime", "code"])
    panel = pd.DataFrame({c: 2. for c in ("open", "high", "low", "close", "volume")}, index=idx)

    class Stub:
        def fetch_tech(self, code, start, end):
            out = pd.DataFrame({"trade_date": [dates[0]], "amihud_20": [1e-9 if code == "A" else 2e-9]})
            out.attrs["technical_provenance"] = {"schema": TECH_VERSION, "code": code}
            return out

    manifest = _merge_tech(panel, Stub(), ["A", "B"], str(dates[0]), str(dates[-1]))
    assert panel.loc[(dates[0], "A"), "amihud_20"] == 1e-9
    assert panel.loc[(dates[0], "B"), "amihud_20"] == 2e-9
    assert panel.loc[(dates[-1], "A"), list(_TECH_FIELDS)].isna().all()
    wrapped = PanelData(panel, technical_provenance_manifest=manifest)
    assert set(wrapped.technical_provenance_manifest["by_code"]) == {"A", "B"}
    assert wrapped.df.attrs["technical_provenance"]["provenance_code_count"] == 2
    assert "by_code" not in wrapped.df.attrs["technical_provenance"]


def test_panel_missing_loader_has_named_nan_columns_and_preserves_existing():
    idx = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-09-04"), "A")], names=["datetime", "code"])
    panel = pd.DataFrame({"close": 1., "rsi_14": 25.}, index=idx)
    _merge_tech(panel, object(), ["A"], "2026-09-04", "2026-09-04")
    assert panel.rsi_14.iloc[0] == 25.
    assert panel.amihud_20.isna().all()
    assert set(_TECH_FIELDS).issubset(panel.columns)


def test_panel_errors_are_explicit_not_silently_filled():
    idx = pd.MultiIndex.from_tuples([(pd.Timestamp("2026-09-04"), "A")], names=["datetime", "code"])
    panel = pd.DataFrame({"close": 1.}, index=idx)

    class Broken:
        def fetch_tech(self, *args):
            raise RuntimeError("source changed")

    manifest = _merge_tech(panel, Broken(), ["A"], "2026-09-04", "2026-09-04")
    assert panel.amihud_20.isna().all()
    assert manifest["errors"] == {"A": "source changed"}
    assert panel.attrs["technical_provenance"]["error_code_count"] == 1


def test_semantic_definition_hash_invalidates_both_caches(tmp_path, monkeypatch):
    from financial_analyst.data.loaders import technical
    from financial_analyst.factors.zoo import panel_cache
    q = _quote()
    loader = _provider(tmp_path, q)
    old = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    key = panel_cache._key(["SH600667"], "2025-01-01", "2026-01-01", "day", False)
    assert key[0] == panel_cache.PANEL_CACHE_VERSION
    monkeypatch.setitem(technical.TECH_DEFINITIONS, "amihud_20", "future-reviewed-definition")
    new = loader.fetch_tech("SH600667", str(q.index[0]), str(q.index[-1]))
    assert old.attrs["technical_provenance"]["definition_hash"] != new.attrs["technical_provenance"]["definition_hash"]
    assert new.attrs["technical_provenance"]["cache"] == "miss"
    assert key != panel_cache._key(["SH600667"], "2025-01-01", "2026-01-01", "day", False)
    assert panel_cache._key(["A", "B"], "s", "e", "day", False) == panel_cache._key(["B", "A"], "s", "e", "day", False)


def test_panel_cache_invalidates_source_revision_and_provider(tmp_path, monkeypatch):
    from financial_analyst.factors.zoo import panel_cache
    q = _quote()
    root1, root2 = tmp_path / "one", tmp_path / "two"
    root1.mkdir()
    root2.mkdir()
    loader1, loader2 = _provider(root1, q), _provider(root2, q)
    calls = []

    def fake_panel(*args, **kwargs):
        calls.append(args[0])
        return object()

    monkeypatch.setattr(panel_cache.PanelData, "from_loader", fake_panel)
    panel_cache.clear_panel_cache()
    try:
        first = panel_cache.load_panel_cached(loader1, ["SH600667"], "2025-01-01", "2026-01-01")
        assert panel_cache.load_panel_cached(loader1, ["SH600667"], "2025-01-01", "2026-01-01") is first
        assert len(calls) == 1
        assert panel_cache.load_panel_cached(loader2, ["SH600667"], "2025-01-01", "2026-01-01") is not first
        _write_bin(root1, "SH600667", "amount", np.r_[q.amount.to_numpy(), 1.])
        assert panel_cache.load_panel_cached(loader1, ["SH600667"], "2025-01-01", "2026-01-01") is not first
        assert len(calls) == 3
    finally:
        panel_cache.clear_panel_cache()


def test_unhashable_loader_identity_never_crosses_provider():
    from financial_analyst.factors.zoo.panel_cache import _source_key
    a, b = [], []
    assert _source_key(a, [], "day") == _source_key(a, [], "day")
    assert _source_key(a, [], "day") != _source_key(b, [], "day")
    assert isinstance(hash(_source_key(a, [], "day")), int)
