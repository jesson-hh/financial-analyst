from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from guanlan_v2.console.quant_chart import (
    QuantChartDataError,
    _qfq_from_xdxr,
    build_workspace_quant_chart,
)


def _ohlc(dates: pd.DatetimeIndex, closes: np.ndarray) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "trade_date": dates,
        "open": closes * 0.998,
        "high": closes * 1.012,
        "low": closes * 0.988,
        "close": closes,
    })


def _xdxr_coverage(code: str = "SH600667") -> pd.DataFrame:
    # A category-5 row proves the code is covered while applying no price event.
    return pd.DataFrame([{
        "code": code,
        "event_date": "2020-01-01",
        "category": 5,
        "fenhong": 0.0,
        "peigujia": 0.0,
        "songzhuangu": 0.0,
        "peigu": 0.0,
    }])


def _series_fixture(n: int = 150):
    dates = pd.bdate_range(end="2026-08-14", periods=n)
    bench_ret = np.array([0.001 + ((i % 9) - 4) * 0.0012 for i in range(1, n)])
    stock_ret = 0.0007 + 1.8 * bench_ret
    bench = np.r_[100.0, 100.0 * np.cumprod(1.0 + bench_ret)]
    stock = np.r_[20.0, 20.0 * np.cumprod(1.0 + stock_ret)]
    return dates, stock, bench


def test_xdxr_qfq_removes_cash_and_split_discontinuity():
    dates = pd.to_datetime(["2026-08-12", "2026-08-13", "2026-08-14"])
    # 10送10 + 每10股派1元: ex-right reference=(10-.1)/2=4.95.
    raw = _ohlc(dates, np.array([10.0, 4.95, 5.049]))
    events = pd.DataFrame([{
        "code": "SH600667",
        "event_date": "2026-08-13",
        "category": 1,
        "fenhong": 1.0,
        "peigujia": 0.0,
        "songzhuangu": 10.0,
        "peigu": 0.0,
    }])
    adjusted, meta = _qfq_from_xdxr(raw, events)
    returns = adjusted["qfq_close"].pct_change()
    assert returns.iloc[1] == pytest.approx(0.0, abs=1e-12)
    assert returns.iloc[2] == pytest.approx(0.02, abs=1e-12)
    assert adjusted.iloc[-1]["qfq_close"] == pytest.approx(raw.iloc[-1]["close"])
    assert meta["events_applied"] == 1


def test_qfq_fails_closed_without_xdxr_coverage():
    dates = pd.bdate_range(end="2026-08-14", periods=5)
    with pytest.raises(QuantChartDataError, match="refusing to label raw prices as adjusted"):
        _qfq_from_xdxr(_ohlc(dates, np.arange(10.0, 15.0)), pd.DataFrame())


def test_strict_windows_png_json_and_exact_regression(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)

    result = build_workspace_quant_chart(
        "太极实业",
        "600667.SH",
        output_dir=tmp_path,
        reference_date="2026-08-15",  # Saturday -> expected latest session Friday 08-14
        stock_loader=lambda _code, _start, _end: _ohlc(dates, stock),
        local_benchmark_loader=lambda _benchmark: pd.DataFrame({
            "trade_date": dates,
            "close": bench,
        }),
        xdxr_loader=lambda _code: _xdxr_coverage(),
    )

    assert result["subject"]["code"] == "SH600667"
    assert result["analysis_asof"] == "2026-08-14"
    assert result["benchmark_asof"] == "2026-08-14"
    assert result["common_cutoff"] == "2026-08-14"
    assert result["current_ready"] is True
    assert result["position_price"] == pytest.approx(stock[-1])
    assert result["position_asof"] == "2026-08-14"
    assert result["position_source"] == "formal_adjusted_daily_close"
    assert result["position_levels"]["available"] is True
    assert len(result["position_levels"]["supports"]) <= 2
    assert len(result["position_levels"]["resistances"]) <= 2
    assert all(row["available"] for row in result["levels"])
    assert all(row["available"] for row in result["regressions"])
    for row in result["regressions"]:
        assert row["n"] == row["window"]
        assert row["alpha_daily"] == pytest.approx(0.0007, abs=1e-12)
        assert row["beta"] == pytest.approx(1.8, abs=1e-12)
        assert row["r2"] == pytest.approx(1.0, abs=1e-12)

    png = tmp_path / result["artifacts"]["png"]
    js = tmp_path / result["artifacts"]["json"]
    assert png.is_file() and js.is_file()
    assert Image.open(png).size == (1600, 980)
    saved = json.loads(js.read_text(encoding="utf-8"))
    assert saved["methodology"]["risk_free_rate"] == 0.0
    assert saved["artifacts"] == {"png": "SH600667_quant.png", "json": "SH600667_quant.json"}


def test_insufficient_samples_are_unavailable_not_mislabeled(tmp_path: Path):
    dates, stock, bench = _series_fixture(51)
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        windows=(20, 60, 120), reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame({"trade_date": dates, "close": bench}),
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["levels"][0]["label"] == "20-session adjusted range"
    assert result["levels"][0]["available"] is True
    assert result["levels"][1]["available"] is False
    assert result["levels"][1]["label"] == "unavailable (51/60 formal sessions)"
    assert result["levels"][2]["label"] == "unavailable (51/120 formal sessions)"
    # 51 prices produce 50 aligned returns: 20 works; 60/120 must not be named available.
    assert [row["available"] for row in result["regressions"]] == [True, False, False]
    assert result["regressions"][1]["reason"] == "insufficient_aligned_returns:50/60"


def test_trusted_tdx_benchmark_fallback_and_unsynchronised_stock_is_not_current(tmp_path: Path):
    dates, stock, bench = _series_fixture(140)
    stock_dates = dates[:-1]  # formal stock stops 08-13
    local_dates = dates[:-55]  # stale local CSI300
    calls: list[tuple[str, int]] = []

    def tdx_loader(name: str, count: int) -> pd.DataFrame:
        calls.append((name, count))
        return pd.DataFrame({"trade_date": dates, "close": bench})

    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(stock_dates, stock[:-1]),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": local_dates, "close": bench[:len(local_dates)],
        }),
        tdx_benchmark_loader=tdx_loader,
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert calls and calls[0][0] == "csi300"
    assert result["provenance"]["benchmark"] == "injected_tdx_benchmark_loader"
    assert result["provenance"]["benchmark_identity"]["verified"] is True
    assert result["provenance"]["benchmark_identity"]["overlap_n"] >= 5
    assert result["analysis_asof"] == "2026-08-13"
    assert result["benchmark_asof"] == "2026-08-14"
    assert result["common_cutoff"] == "2026-08-13"
    assert result["current_ready"] is False
    assert result["freshness"]["sources_synchronized"] is False
    assert any("formal stock as-of" in warning for warning in result["freshness"]["warnings"])


def test_tdx_benchmark_identity_mismatch_is_rejected(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)
    local_dates = dates[:-1]
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": local_dates, "close": bench[:-1],
        }),
        tdx_benchmark_loader=lambda *_: pd.DataFrame({
            "trade_date": dates, "close": bench * 2.0,
        }),
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["benchmark_asof"] == "2026-08-13"
    assert result["provenance"]["benchmark"] == "injected_local_benchmark_loader"
    assert result["current_ready"] is False
    assert any("benchmark identity check failed" in x
               for x in result["freshness"]["warnings"])


def test_tdx_benchmark_without_canonical_overlap_cannot_be_current(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame(columns=["trade_date", "close"]),
        tdx_benchmark_loader=lambda *_: pd.DataFrame({
            "trade_date": dates, "close": bench,
        }),
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["benchmark_asof"] == "2026-08-14"
    assert result["provenance"]["benchmark"] == "injected_tdx_benchmark_loader"
    assert result["provenance"]["benchmark_identity"]["verified"] is False
    assert result["current_ready"] is False
    assert any("identity is unverified" in x for x in result["freshness"]["warnings"])


def test_tdx_stock_tail_extends_only_after_raw_overlap_identity_check(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates[:-1], stock[:-1]),
        tdx_stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": dates, "close": bench,
        }),
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    extension = result["provenance"]["stock_extension"]
    assert extension["status"] == "extended"
    assert extension["rows_added"] == 1
    assert extension["overlap_n"] >= 5
    assert extension["max_ratio_error"] == pytest.approx(0.0)
    assert result["analysis_asof"] == "2026-08-14"
    assert result["position_price"] == pytest.approx(stock[-1])
    assert result["position_source"] == "formal_daily_plus_verified_tdx_tail_adjusted_close"
    assert result["data_quality"]["stock_formal_rows"] == len(dates) - 1
    assert result["data_quality"]["stock_tdx_rows_added"] == 1
    assert result["data_quality"]["stock_total_rows"] == len(dates)
    assert result["provenance"]["stock_adjustment"]["price_anchor"] == "latest_trusted_raw_close"
    assert result["current_ready"] is True


def test_tdx_stock_tail_with_different_adjustment_is_rejected(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)
    incompatible = stock * 1.05
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates[:-1], stock[:-1]),
        tdx_stock_loader=lambda *_: _ohlc(dates, incompatible),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": dates, "close": bench,
        }),
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["analysis_asof"] == "2026-08-13"
    assert result["provenance"]["stock_extension"] is None
    assert result["current_ready"] is False
    assert any("raw-price identity check failed" in item
               for item in result["freshness"]["warnings"])


def test_no_fallback_does_not_call_network_and_stale_result_is_badged(tmp_path: Path):
    dates, stock, bench = _series_fixture(80)
    old_dates = dates[:-10]

    def forbidden(*_args):
        raise AssertionError("TDX fallback must be disabled")

    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15", allow_tdx_index_fallback=False,
        stock_loader=lambda *_: _ohlc(old_dates, stock[:len(old_dates)]),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": old_dates, "close": bench[:len(old_dates)],
        }),
        tdx_benchmark_loader=forbidden,
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["current_ready"] is False
    assert result["freshness"]["benchmark_lag_calendar_days"] > 4
    assert result["provenance"]["benchmark"] == "injected_local_benchmark_loader"


def test_equal_old_tdx_asof_cannot_prove_a_weekday_holiday(tmp_path: Path):
    _dates, stock, bench = _series_fixture(80)
    dates = pd.bdate_range(end="2026-08-13", periods=80)
    local = pd.DataFrame({"trade_date": dates, "close": bench})
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: local,
        tdx_benchmark_loader=lambda *_: local,
        xdxr_loader=lambda _: _xdxr_coverage(),
    )
    assert result["freshness"]["benchmark_latest_verified_by_tdx"] is False
    assert result["current_ready"] is False
    assert any("not expected latest weekday" in x
               for x in result["freshness"]["warnings"])
    # Equal-asof fallback does not replace the longer canonical local series.
    assert result["provenance"]["benchmark"] == "injected_local_benchmark_loader"


def test_tdx_xdxr_refresh_merges_new_dividend_and_verifies_current(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)
    local_xdxr = _xdxr_coverage()
    verified_xdxr = pd.concat([local_xdxr, pd.DataFrame([{
        "code": "SH600667", "event_date": "2026-06-30", "category": 1,
        "fenhong": 0.65, "peigujia": 0.0, "songzhuangu": 0.0, "peigu": 0.0,
    }])], ignore_index=True)
    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": dates, "close": bench,
        }),
        xdxr_loader=lambda _: local_xdxr,
        tdx_xdxr_loader=lambda _: verified_xdxr,
    )
    assert result["freshness"]["xdxr_latest_verified_by_tdx"] is True
    assert result["provenance"]["stock_adjustment"]["events_applied"] == 1
    assert result["current_ready"] is True


def test_tdx_xdxr_failure_keeps_historical_result_but_blocks_current_badge(tmp_path: Path):
    dates, stock, bench = _series_fixture(150)

    def broken(_code):
        raise RuntimeError("xdxr offline")

    result = build_workspace_quant_chart(
        "太极实业", "SH600667", output_dir=tmp_path,
        reference_date="2026-08-15",
        stock_loader=lambda *_: _ohlc(dates, stock),
        local_benchmark_loader=lambda _: pd.DataFrame({
            "trade_date": dates, "close": bench,
        }),
        xdxr_loader=lambda _: _xdxr_coverage(),
        tdx_xdxr_loader=broken,
    )
    assert result["freshness"]["xdxr_latest_verified_by_tdx"] is False
    assert result["current_ready"] is False
    assert any("tdx_xdxr_error" in x for x in result["freshness"]["warnings"])
    assert all(row["available"] for row in result["regressions"])
