# -*- coding: utf-8 -*-
"""数据筛选 Skill 的密封契约测试。

这里故意只使用合成 ``PanelData``，不读取真实行情、股票池或公司主表。测试锁定
均线筛选的数学语义、有效横截面以及 direct/workflow 两个薄适配层的审计关联。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from financial_analyst.factors.zoo.panel import PanelData
from guanlan_v2.screen import data_screen as ds
from guanlan_v2.screen.api import build_screen_router
import guanlan_v2.workflow.api as wapi
import guanlan_v2.workflow.executor as wex


def _panel(
    closes_by_code: dict[str, list[float]],
    *,
    dates_by_code: dict[str, pd.DatetimeIndex] | None = None,
    start: str = "2026-01-05",
) -> PanelData:
    """Build a finite, liquid daily panel with an optional ragged calendar."""
    records: list[dict[str, object]] = []
    for code, closes in closes_by_code.items():
        dates = (
            dates_by_code[code]
            if dates_by_code is not None and code in dates_by_code
            else pd.bdate_range(start, periods=len(closes))
        )
        assert len(dates) == len(closes)
        for dt, close in zip(dates, closes):
            value = float(close)
            records.append({
                "datetime": dt,
                "code": code,
                "open": value,
                "high": value * 1.01,
                "low": value * 0.99,
                "close": value,
                "volume": 10_000_000.0,
                "amount": 200_000_000.0,
            })
    frame = pd.DataFrame.from_records(records).set_index(["datetime", "code"])
    return PanelData(frame)


def _request(conditions: list[dict[str, object]], *, as_of: str | None = None) -> dict:
    request = {
        "universe": "all_a",
        "selector": {"conditions": conditions},
        "top_n": 200,
        "filters": {
            "exclude_st": False,
            "require_asof_trade": True,
            "min_avg_amount_yuan": 0,
            "amount_window": 1,
            "amount_min_observations": 1,
        },
    }
    if as_of is not None:
        request["as_of"] = as_of
    return request


def _evaluate(panel: PanelData, request: dict) -> dict:
    spec = ds.compile_data_screen_spec(request)
    codes = [str(code) for code in panel.codes()]
    return ds.evaluate_data_screen(
        panel,
        spec,
        universe_meta={
            "id": "sealed",
            "label": "密封股票池",
            "n_resolved": len(codes),
            "source": "test_fixture",
            "strict_pit": True,
        },
        metadata={code: {"name": code, "industry": "测试"} for code in codes},
        metadata_provenance={
            "source": "test_fixture",
            "available": True,
            "strict_pit": True,
            "snapshot_date": "2026-01-31",
        },
        load_meta={"loader": "sealed_fixture", "amount_unit": "yuan"},
    )


def test_ma_trend_preset_compiles_to_the_exact_closed_rules():
    spec = ds.compile_data_screen_spec({
        "selector": {"preset": "ma_trend", "params": {}},
        "filters": {"min_avg_amount_yuan": 0, "amount_window": 1},
    })

    assert [rule["id"] for rule in spec["rules"]] == [
        "price_above_ma",
        "ma_strict_rise",
        "no_consecutive_break",
        "ma_bias_range",
    ]
    price, rising, breaks, bias = spec["rules"]
    assert price["op"] == ">"
    assert price["right"] == {"type": "rolling_mean", "field": "close", "window": 5}
    assert rising["transitions"] == 3
    assert rising["series"] == price["right"]
    assert breaks["lookback"] == 5 and breaks["max_count"] == 1
    assert breaks["predicate"]["op"] == "<"
    assert bias["min"] == 0.0 and bias["max"] == 0.03 and bias["inclusive"] is True


def test_three_strict_rising_transitions_need_four_ma_values_and_equality_fails():
    dates = pd.bdate_range("2026-01-05", periods=8)
    panel = _panel(
        {
            "SH600001": [1, 2, 3, 4, 5, 6, 7, 8],
            # Seven observations produce only three complete MA5 values.
            "SH600002": [2, 3, 4, 5, 6, 7, 8],
            # Last four MA5 values are 3, 4, 5, 5: equality is not a rise.
            "SH600003": [1, 2, 3, 4, 5, 6, 7, 3],
        },
        dates_by_code={
            "SH600001": dates,
            "SH600002": dates[1:],
            "SH600003": dates,
        },
    )
    result = _evaluate(panel, _request([{
        "id": "rise",
        "type": "strict_rise",
        "series": {"type": "rolling_mean", "field": "close", "window": 5},
        "transitions": 3,
    }], as_of=str(dates[-1].date())))

    assert [row["code"] for row in result["candidates"]] == ["SH600001"]
    evidence = result["candidates"][0]["evidence"]["rise"]
    assert evidence["passed"] is True
    assert evidence["values"] == pytest.approx([3.0, 4.0, 5.0, 6.0])
    assert evidence["transitions"] == 3 and len(evidence["deltas"]) == 3
    assert result["rejection_counts"]["rule:rise"] == 2


def test_max_consecutive_below_is_a_streak_inside_the_last_five_days():
    panel = _panel({
        # close < MA2 iff today's close < yesterday's. The last-five predicate
        # sequence is F,T,F,T,F: two breaks, but never consecutive.
        "SH600010": [10, 11, 10, 11, 10, 11],
        # Last-five predicate is F,T,T,F,F: one two-day consecutive break.
        "SH600011": [10, 11, 10, 9, 10, 11],
    })
    result = _evaluate(panel, _request([{
        "id": "breaks",
        "type": "max_consecutive",
        "predicate": {
            "type": "compare",
            "left": {"type": "field", "field": "close"},
            "op": "<",
            "right": {"type": "rolling_mean", "field": "close", "window": 2},
        },
        "lookback": 5,
        "max_count": 1,
    }]))

    assert [row["code"] for row in result["candidates"]] == ["SH600010"]
    evidence = result["candidates"][0]["evidence"]["breaks"]
    assert evidence["values"] == [False, True, False, True, False]
    assert evidence["max_streak"] == 1 and evidence["allowed"] == 1
    assert result["rejection_counts"]["rule:breaks"] == 1


def test_three_percent_ma_bias_boundary_is_inclusive_but_excess_is_rejected():
    def last_close_for_bias(bias: float) -> float:
        # With the preceding four closes all 100, solve x / mean(100,100,100,100,x)-1=bias.
        return 400.0 * (1.0 + bias) / (4.0 - bias)

    panel = _panel({
        "SH600020": [100, 100, 100, 100, last_close_for_bias(0.03)],
        "SH600021": [100, 100, 100, 100, last_close_for_bias(0.0300001)],
    })
    result = _evaluate(panel, _request([{
        "id": "bias",
        "type": "range",
        "value": {
            "type": "bias",
            "field": "close",
            "baseline": {"type": "rolling_mean", "field": "close", "window": 5},
        },
        "min": 0.0,
        "max": 0.03,
        "inclusive": True,
    }]))

    assert [row["code"] for row in result["candidates"]] == ["SH600020"]
    evidence = result["candidates"][0]["evidence"]["bias"]
    assert evidence["passed"] is True and evidence["value"] == pytest.approx(0.03)
    assert result["rejection_counts"]["rule:bias"] == 1


def test_effective_asof_ignores_a_partial_future_singleton_update():
    full_dates = pd.bdate_range("2026-01-05", periods=7)
    partial_date = pd.bdate_range(full_dates[-1] + pd.Timedelta(days=1), periods=1)[0]
    codes = [f"SH60010{i}" for i in range(5)]
    dates_by_code = {code: full_dates for code in codes}
    dates_by_code[codes[0]] = full_dates.append(pd.DatetimeIndex([partial_date]))
    closes = {code: [10 + i for i in range(7)] for code in codes}
    closes[codes[0]].append(99)
    panel = _panel(closes, dates_by_code=dates_by_code)

    result = _evaluate(panel, _request([{
        "id": "up",
        "type": "compare",
        "left": {"type": "field", "field": "close"},
        "op": ">",
        "right": {"type": "rolling_mean", "field": "close", "window": 2},
    }], as_of=str(partial_date.date())))

    assert result["requested_as_of"] == str(partial_date.date())
    assert result["as_of"] == str(full_dates[-1].date())
    assert result["as_of_coverage"] == {
        "valid_bars": 5,
        "peak_valid_bars": 5,
        "minimum_valid_bars": 4,
        "minimum_ratio": 0.80,
        "basis": "loaded_peak",
    }
    assert result["n_matched"] == 5
    assert result["data_snapshot"]["as_of"] == str(full_dates[-1].date())
    assert any("已回落到最新有效交易日" in warning for warning in result["warnings"])


def test_direct_runs_persist_complete_and_invalid_spec_records(tmp_path: Path):
    store = ds.DataScreenRunStore(tmp_path / "runs")
    panel = _panel({"SH600001": [10, 11]})
    request = _request([{
        "id": "positive",
        "type": "range",
        "value": {"type": "field", "field": "close"},
        "min": 0,
        "max": 100,
    }])
    complete = ds.run_data_screen(
        request,
        panel=panel,
        universe_meta={"id": "sealed", "n_resolved": 1, "strict_pit": True},
        metadata={"SH600001": {"name": "密封样本"}},
        metadata_provenance={"source": "fixture", "strict_pit": True},
        load_meta={"loader": "fixture", "amount_unit": "yuan", "verified": True},
        store=store,
        persist=True,
    )

    assert complete["ok"] is True and complete["run_id"].startswith("ds_")
    complete_record = store.get(complete["run_id"])
    assert complete_record is not None
    assert complete_record["status"] == "complete"
    assert complete_record["result"]["run_id"] == complete["run_id"]
    # Strict JSON persistence: no non-standard NaN token is allowed into evidence.
    json.loads((store.root / f"{complete['run_id']}.json").read_text(encoding="utf-8"))
    assert "NaN" not in (store.root / f"{complete['run_id']}.json").read_text(encoding="utf-8")

    invalid = ds.run_data_screen(
        {"selector": {"preset": "not_a_real_preset"}},
        store=store,
        persist=True,
    )
    assert invalid["ok"] is False and invalid["spec_digest"] is None
    invalid_record = store.get(invalid["run_id"])
    assert invalid_record is not None
    assert invalid_record["status"] == "error"
    assert invalid_record["request"] == {"selector": {"preset": "not_a_real_preset"}}
    assert invalid_record["result"]["run_id"] == invalid["run_id"]
    assert "DataScreenSpecError" in invalid_record["error"]


def test_returns_hidden_prior_close_is_included_in_the_snapshot_digest():
    request = _request([{
        "id": "mean_return",
        "type": "range",
        "value": {"type": "rolling_mean", "field": "returns", "window": 5},
        "min": 0.0,
        "max": 1.0,
    }])
    # The final five OHLCVA rows are byte-for-byte identical. Only the close
    # immediately before that tail changes, and it changes the first return in
    # the five-return mean; therefore it must also change the snapshot digest.
    positive = _evaluate(_panel({"SH600001": [1, 2, 2, 2, 2, 2]}), request)
    negative = _evaluate(_panel({"SH600001": [4, 2, 2, 2, 2, 2]}), request)

    assert positive["n_matched"] == 1
    assert negative["n_matched"] == 0
    assert positive["data_snapshot"]["rows"] == 6
    assert negative["data_snapshot"]["rows"] == 6
    assert positive["data_snapshot"]["digest"] != negative["data_snapshot"]["digest"]


def test_all_a_requires_80_percent_of_the_resolved_pool_and_discloses_ratios():
    codes = [f"SH{600000 + i:06d}" for i in range(100)]
    panel = _panel({code: [10, 11, 12, 13, 14] for code in codes})
    spec = ds.compile_data_screen_spec(_request([{
        "id": "positive",
        "type": "range",
        "value": {"type": "field", "field": "close"},
        "min": 0,
        "max": 100,
    }]))
    kwargs = {
        "metadata": {},
        "metadata_provenance": {"source": "fixture", "available": False,
                                "strict_pit": False},
        "load_meta": {"loader": "fixture", "amount_unit": "yuan", "verified": True},
    }

    with pytest.raises(RuntimeError, match="全A行情加载覆盖不足.*100/5000"):
        ds.evaluate_data_screen(
            panel, spec,
            universe_meta={"id": "all_a", "n_resolved": 5000, "strict_pit": False},
            **kwargs,
        )

    boundary = ds.evaluate_data_screen(
        panel, spec,
        universe_meta={"id": "all_a", "n_resolved": 125, "strict_pit": False},
        **kwargs,
    )
    assert boundary["universe_coverage"] == {
        "enforced": True,
        "n_resolved": 125,
        "n_loaded": 100,
        "loaded_ratio": 0.8,
        "minimum_ratio": 0.8,
        "effective_valid_bars": 100,
        "effective_valid_ratio": 0.8,
    }
    assert boundary["as_of_coverage"]["basis"] == "resolved_all_a"


def test_historical_index_pool_warns_when_constituents_are_not_pit_but_explicit_does_not():
    panel = _panel({"SH600001": [10, 11, 12, 13, 14]})
    request = _request([{
        "id": "positive", "type": "range",
        "value": {"type": "field", "field": "close"}, "min": 0, "max": 100,
    }], as_of="2026-01-09")
    request["universe"] = "csi300"
    spec = ds.compile_data_screen_spec(request)
    common = {
        "metadata": {"SH600001": {"name": "正常样本"}},
        "metadata_provenance": {"source": "fixture", "available": True,
                                "strict_pit": True, "snapshot_date": "2026-01-09"},
        "load_meta": {"loader": "fixture", "amount_unit": "yuan", "verified": True},
    }

    index_result = ds.evaluate_data_screen(
        panel, spec,
        universe_meta={"id": "csi300", "label": "沪深300", "n_resolved": 1,
                       "strict_pit": False},
        **common,
    )
    assert any("沪深300成分" in warning and "不是历史成分 PIT" in warning
               for warning in index_result["warnings"])
    assert index_result["data_snapshot"]["strict_pit"] is False

    explicit_result = ds.evaluate_data_screen(
        panel, spec,
        universe_meta={"id": "explicit", "label": "指定股票", "n_resolved": 1,
                       "strict_pit": True},
        **common,
    )
    assert not any("成分来自当前/静态配置" in warning
                   for warning in explicit_result["warnings"])
    assert explicit_result["data_snapshot"]["strict_pit"] is True


def test_tushare_daily_units_are_normalised_before_liquidity_filter(monkeypatch):
    from financial_analyst.data.loaders.tushare import TushareLoader

    class SealedTushareLoader(TushareLoader):
        def __init__(self):
            pass

        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.bdate_range("2026-01-05", periods=5)
            return pd.DataFrame({
                "trade_date": dates,
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.0] * 5,
                # Tushare raw daily contract: 手 and 千元.
                "vol": [10_000.0] * 5,
                "amount": [100_000.0] * 5,
            })

    monkeypatch.setattr(ds, "_stock_basic_snapshot", lambda: (
        {"SH600001": {"name": "密封样本", "list_status": "L"}},
        {"source": "fixture", "available": True, "strict_pit": False,
         "snapshot_date": "2026-01-09"},
    ))
    result = ds.run_data_screen({
        "codes": ["SH600001"],
        "as_of": "2026-01-09",
        "selector": {"conditions": [
            {
                "id": "price", "type": "range",
                "value": {"type": "field", "field": "close"},
                "min": 0, "max": 200,
            },
            {
                "id": "volume", "type": "range",
                "value": {"type": "field", "field": "volume"},
                "min": 900_000, "max": 1_100_000,
            },
        ]},
        "filters": {
            "exclude_st": True,
            "require_asof_trade": True,
            "min_avg_amount_yuan": 100_000_000,
            "amount_window": 5,
            "amount_min_observations": 5,
        },
    }, loader=SealedTushareLoader(), persist=False)

    assert result["ok"] is True and result["n_matched"] == 1
    assert result["candidates"][0]["avg_amount_yuan"] == 100_000_000
    assert result["candidates"][0]["filter_evidence"]["asof_trade"]["volume"] == 1_000_000
    assert result["candidates"][0]["evidence"]["volume"]["passed"] is True
    assert result["data_load"]["source_amount_unit"] == "thousand_yuan"
    assert result["data_load"]["amount_unit"] == "yuan"
    assert result["data_load"]["verified"] is True


def test_exclude_st_fails_closed_for_unavailable_or_partial_metadata():
    panel = _panel({"SH600001": [10, 11], "SH600002": [10, 11]})
    request = _request([{
        "id": "positive", "type": "range",
        "value": {"type": "field", "field": "close"}, "min": 0, "max": 100,
    }])
    request["filters"]["exclude_st"] = True
    spec = ds.compile_data_screen_spec(request)
    common = {
        "universe_meta": {"id": "sealed", "n_resolved": 2, "strict_pit": True},
        "load_meta": {"loader": "fixture", "amount_unit": "yuan", "verified": True},
    }

    with pytest.raises(RuntimeError, match="公司主表元数据不可用"):
        ds.evaluate_data_screen(
            panel, spec, metadata={},
            metadata_provenance={"available": False, "strict_pit": False}, **common,
        )

    partial = ds.evaluate_data_screen(
        panel, spec,
        metadata={"SH600001": {"name": "正常样本"}},
        metadata_provenance={"available": True, "strict_pit": True}, **common,
    )
    assert [row["code"] for row in partial["candidates"]] == ["SH600001"]
    assert partial["rejection_counts"]["metadata_unknown_st"] == 1
    assert any("无法确认 ST 状态已排除" in warning for warning in partial["warnings"])


def test_snapshot_digest_binds_metadata_that_can_change_st_selection():
    panel = _panel({"SH600001": [10, 11]})
    request = _request([{
        "id": "positive", "type": "range",
        "value": {"type": "field", "field": "close"}, "min": 0, "max": 100,
    }])
    request["filters"]["exclude_st"] = True
    spec = ds.compile_data_screen_spec(request)
    common = {
        "universe_meta": {"id": "sealed", "n_resolved": 1, "strict_pit": True},
        "metadata_provenance": {"source": "fixture", "available": True,
                                "strict_pit": True, "snapshot_date": "2026-01-06"},
        "load_meta": {"loader": "fixture", "amount_unit": "yuan", "verified": True},
    }
    normal = ds.evaluate_data_screen(
        panel, spec,
        metadata={"SH600001": {"name": "正常样本", "industry": "电子",
                                "list_status": "L"}}, **common,
    )
    st = ds.evaluate_data_screen(
        panel, spec,
        metadata={"SH600001": {"name": "*ST样本", "industry": "电子",
                                "list_status": "L"}}, **common,
    )

    assert normal["n_matched"] == 1 and st["n_matched"] == 0
    assert normal["data_snapshot"]["market_digest"] == st["data_snapshot"]["market_digest"]
    assert normal["data_snapshot"]["metadata_digest"] != st["data_snapshot"]["metadata_digest"]
    assert normal["data_snapshot"]["digest"] != st["data_snapshot"]["digest"]


def test_liquidity_uses_a_fixed_recent_window_without_backfilling_old_amounts():
    panel = _panel({
        "SH600001": [10, 11, 12, 13, 14],
        "SH600002": [10, 11, 12, 13, 14],
    })
    dates = panel.df.index.get_level_values("datetime").unique()
    panel.df.loc[(dates, "SH600001"), "amount"] = [1e9, 1e9, 1e9, 0.0, 1e8]
    panel.df.loc[(dates, "SH600002"), "amount"] = [1e9, 1e9, 1e9, float("nan"), 1e8]
    request = _request([{
        "id": "positive", "type": "range",
        "value": {"type": "field", "field": "close"}, "min": 0, "max": 100,
    }])
    request["filters"].update({
        "min_avg_amount_yuan": 500_000_000,
        "amount_window": 3,
        "amount_min_observations": 3,
    })
    result = _evaluate(panel, request)

    assert result["n_matched"] == 0
    assert result["rejection_counts"]["liquidity"] == 2


@pytest.mark.parametrize("payload", [
    {"selector": {"preset": "ma_trend", "params": {}}, "top_nn": 5},
    {"selector": {"preset": "ma_trend", "params": {}, "paramz": {}}},
    {"selector": {"preset": "ma_trend", "params": {"ma_windw": 5}}},
    {"selector": {"conditions": [{
        "id": "r", "type": "range", "value": {"type": "field", "field": "close"},
        "min": 0, "max": 1, "incluzive": True,
    }]}},
    {"selector": {"conditions": [{
        "id": "r", "type": "range",
        "value": {"type": "field", "field": "close", "windw": 2},
        "min": 0, "max": 1,
    }]}},
    {"selector": {"preset": "ma_trend", "params": {}},
     "filters": {"exclude_st": True, "excl_st": True}},
    {"selector": {"preset": "ma_trend", "params": {}},
     "score": {"type": "ma_fit", "direction": "desc"}},
])
def test_closed_dsl_rejects_unknown_keys_at_every_level(payload):
    with pytest.raises(ds.DataScreenSpecError, match="未知字段"):
        ds.compile_data_screen_spec(payload)


@pytest.mark.parametrize("payload, pattern", [
    ({"selector": {"preset": "ma_trend", "params": {"ma_window": 5.9}}}, "必须是整数"),
    ({"selector": {"preset": "ma_trend", "params": False}}, "params 必须是对象"),
    ({"selector": {"preset": "ma_trend", "params": {}}, "filters": 0}, "filters 必须是对象"),
    ({"selector": {"preset": "ma_trend", "params": {}}, "codes": False}, "codes 必须是数组"),
    ({"selector": {"preset": "ma_trend", "params": {}}, "as_of": 0}, "as_of 必须是字符串"),
    ({"selector": {"preset": "ma_trend", "params": {}},
      "as_of": "2026-01-01garbage"}, "as_of 必须是 YYYY-MM-DD"),
    ({"selector": {"preset": "ma_trend", "params": {}},
      "top_n": 5, "topN": 6}, "top_n 与 topN"),
    ({"selector": {"preset": "ma_trend", "params": {}},
      "filters": {"exclude_st": "false"}}, "必须是布尔值"),
    ({"selector": {"conditions": [{
        "id": "r", "type": "range", "value": {"type": "field", "field": "close"},
        "min": 0, "max": 1, "inclusive": "false",
    }]}}, "必须是布尔值"),
])
def test_closed_dsl_rejects_silent_numeric_and_boolean_coercions(payload, pattern):
    with pytest.raises(ds.DataScreenSpecError, match=pattern):
        ds.compile_data_screen_spec(payload)


def test_nan_rejection_still_persists_a_readable_ds_error_record(tmp_path: Path):
    store = ds.DataScreenRunStore(tmp_path / "runs")
    result = ds.run_data_screen({
        "selector": {"preset": "ma_trend", "params": {"ma_window": float("nan")}},
    }, store=store, persist=True)

    assert result["ok"] is False and result["run_id"].startswith("ds_")
    record = store.get(result["run_id"])
    assert record is not None and record["status"] == "error"
    assert record["rejected_request"]["unserializable_type"] == "dict"
    assert "nan" in record["rejected_request"]["preview"].lower()
    raw = (store.root / f"{result['run_id']}.json").read_text(encoding="utf-8")
    json.loads(raw)
    assert ":NaN" not in raw and ":Infinity" not in raw


def test_workflow_adapter_keeps_wr_to_ds_parent_link(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(request, *, persist, parent_run_id):
        seen.update({
            "request": request,
            "persist": persist,
            "parent_run_id": parent_run_id,
        })
        return {
            "ok": True,
            "status": "complete",
            "run_id": "ds_0123456789abcdef",
            "parent_run_id": parent_run_id,
            "as_of": "2026-01-30",
            "candidates": [{"rank": 1, "code": "SH600001", "score": 99.0}],
        }

    monkeypatch.setattr(ds, "run_data_screen", fake_run)
    graph = {
        "nodes": [
            {"id": "src", "type": "source", "x": 0, "y": 0,
             "params": {"universe": "all_a", "end": "2026-01-30"}},
            {"id": "screen", "type": "data_screen", "x": 1, "y": 0,
             "params": {"preset": "ma_trend", "top_n": 5}},
        ],
        "edges": [{"from": ["src", "data"], "to": ["screen", "src"]}],
    }
    workflow_run_id = "wr_0123456789abcdef"
    outcome = wex.run_graph(graph, overrides={"_workflow_run_id": workflow_run_id})

    assert outcome["ok"] is True
    assert outcome["terminal"]["kind"] == "data_screen"
    payload = outcome["terminal"]["payload"]
    assert payload["run_id"] == "ds_0123456789abcdef"
    assert payload["screen_run_id"] == "ds_0123456789abcdef"
    assert payload["parent_run_id"] == workflow_run_id
    assert payload["__dt"] == "data_screen"
    assert seen["persist"] is True and seen["parent_run_id"] == workflow_run_id
    assert seen["request"]["universe"] == "all_a"
    assert seen["request"]["as_of"] == "2026-01-30"
    assert seen["request"]["selector"] == {
        "preset": "ma_trend",
        "params": {
            "ma_window": 5,
            "rising_transitions": 3,
            "below_lookback": 5,
            "max_consecutive_below": 1,
            "min_bias": 0.0,
            "max_bias": 0.03,
        },
    }


def test_workflow_failure_keeps_the_persisted_ds_run_id_visible(monkeypatch):
    monkeypatch.setattr(ds, "run_data_screen", lambda request, *, persist, parent_run_id: {
        "ok": False,
        "run_id": "ds_fedcba9876543210",
        "parent_run_id": parent_run_id,
        "reason": "历史元数据 PIT 不足",
    })
    graph = {
        "nodes": [
            {"id": "src", "type": "source", "params": {"universe": "all_a"}},
            {"id": "screen", "type": "data_screen", "params": {"preset": "ma_trend"}},
        ],
        "edges": [{"from": ["src", "data"], "to": ["screen", "src"]}],
    }

    outcome = wex.run_graph(
        graph, overrides={"_workflow_run_id": "wr_fedcba9876543210"},
    )

    assert outcome["ok"] is False
    assert outcome["node_errors"][0]["type"] == "data_screen"
    assert "ds_fedcba9876543210" in outcome["node_errors"][0]["error"]
    assert "PIT 不足" in outcome["node_errors"][0]["error"]


def test_direct_http_endpoint_and_status_reader_preserve_the_ds_contract(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(payload):
        seen["payload"] = payload
        return {
            "ok": True, "status": "complete", "run_id": "ds_aaaaaaaaaaaaaaaa",
            "as_of": "2026-08-21", "candidates": [],
        }

    monkeypatch.setattr(ds, "run_data_screen", fake_run)
    monkeypatch.setattr(ds, "read_data_screen_run", lambda run_id: {
        "run_schema_version": "guanlan.data_screen_run.v1",
        "run_id": run_id, "status": "complete",
        "result": {"ok": True, "run_id": run_id},
    })
    app = FastAPI()
    app.include_router(build_screen_router())
    client = TestClient(app)

    started = client.post("/screen/data-screen", json={
        "universe": "csi_fast", "as_of": "2026-08-23", "top_n": 5,
        "selector": {"preset": "ma_trend", "params": {"ma_window": 5}},
    })
    assert started.status_code == 200
    assert started.json()["run_id"] == "ds_aaaaaaaaaaaaaaaa"
    assert seen["payload"] == {
        "universe": "csi_fast", "codes": [], "freq": "day",
        "as_of": "2026-08-23", "top_n": 5,
        "selector": {"preset": "ma_trend", "params": {"ma_window": 5}},
        "filters": {},
    }

    status = client.get("/screen/data-screen/status/ds_aaaaaaaaaaaaaaaa")
    assert status.status_code == 200
    assert status.json()["ok"] is True
    assert status.json()["run_id"] == "ds_aaaaaaaaaaaaaaaa"
    assert status.json()["result"]["run_id"] == "ds_aaaaaaaaaaaaaaaa"


def test_http_forbids_top_level_typos_but_keeps_nested_nan_for_core_audit(
        monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GUANLAN_DATA_SCREEN_RUN_DIR", str(tmp_path / "http-runs"))
    app = FastAPI()
    app.include_router(build_screen_router())
    client = TestClient(app)

    unknown = client.post("/screen/data-screen", json={
        "selector": {"preset": "ma_trend", "params": {}},
        "top_nn": 5,
    })
    assert unknown.status_code == 422
    assert "extra_forbidden" in str(unknown.json())
    for bad_payload in (
        {"selector": {"preset": "ma_trend", "params": {}}, "top_n": 5.9},
        {"selector": {"preset": "ma_trend", "params": {}}, "universe": 1},
        {"selector": {"preset": "ma_trend", "params": {}}, "codes": "SH600001"},
    ):
        rejected = client.post("/screen/data-screen", json=bad_payload)
        assert rejected.status_code == 422
    assert not list((tmp_path / "http-runs").glob("ds_*.json"))

    # Starlette's parser accepts this legacy non-standard token; the core must
    # still reject it and return a strict-JSON-safe, durable failure record.
    nan = client.post(
        "/screen/data-screen",
        content='{"selector":{"preset":"ma_trend","params":{"ma_window":NaN}}}',
        headers={"content-type": "application/json"},
    )
    assert nan.status_code == 200
    nan_body = nan.json()
    assert nan_body["ok"] is False and nan_body["run_id"].startswith("ds_")
    assert "NaN/Infinity" in nan_body["reason"]

    status = client.get(f"/screen/data-screen/status/{nan_body['run_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "error"


def _screen_graph(source_params: dict, screen_params: dict | None = None) -> dict:
    return {
        "nodes": [
            {"id": "src", "type": "source", "params": source_params},
            {"id": "screen", "type": "data_screen",
             "params": screen_params or {"preset": "ma_trend"}},
        ],
        "edges": [{"from": ["src", "data"], "to": ["screen", "src"]}],
    }


def test_workflow_contract_requires_explicit_source_and_reserves_all_a_for_screen():
    # The new all_a value must not silently change old factor-workflow scope
    # resolution; legacy "全市场 + 自动" remains the index-union pool.
    assert wex._universe_of({"scope": "全市场", "universe": "自动"}) == "all"

    bare = {
        "nodes": [{"id": "screen", "type": "data_screen",
                   "params": {"preset": "ma_trend"}}],
        "edges": [],
    }
    ok, errors = wapi.validate_graph(bare)
    assert ok is False and any("source.data" in error for error in errors)

    auto = _screen_graph({"universe": "自动"})
    ok, errors = wapi.validate_graph(auto)
    assert ok is False and any("空代码" in error for error in errors)

    explicit_codes = _screen_graph({"universe": "自动",
                                    "codes": ["SH600001", "SZ000002"]})
    assert wapi.validate_graph(explicit_codes) == (True, [])
    assert wapi.validate_graph(_screen_graph({"universe": "all_a"})) == (True, [])
    assert wapi.validate_graph(_screen_graph({"universe": "csi300"})) == (True, [])
    assert wapi.validate_graph(_screen_graph({"universe": "csi1000"})) == (True, [])

    bad_consumer = {
        "nodes": [
            {"id": "src", "type": "source", "params": {"universe": "all_a"}},
            {"id": "feature", "type": "feature", "params": {}},
            {"id": "formula", "type": "formula", "params": {"expr": "close"}},
            {"id": "analysis", "type": "analysis", "params": {}},
        ],
        "edges": [
            {"from": ["src", "data"], "to": ["feature", "src"]},
            {"from": ["formula", "out"], "to": ["analysis", "factor"]},
        ],
    }
    ok, errors = wapi.validate_graph(bad_consumer)
    assert ok is False and any("仅供 data_screen.src" in error for error in errors)


def test_workflow_explicit_source_codes_reach_core_without_list_stringification(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(request, *, persist, parent_run_id):
        seen.update(request)
        return {"ok": True, "run_id": "ds_codes", "candidates": []}

    monkeypatch.setattr(ds, "run_data_screen", fake_run)
    outcome = wex.run_graph(_screen_graph({
        "universe": "自动", "codes": ["SH600001", "SZ000002"],
    }))

    assert outcome["ok"] is True
    assert seen["universe"] == "all_a"
    assert seen["codes"] == ["SH600001", "SZ000002"]


@pytest.mark.parametrize("screen_params, expected", [
    ({"preset": "ma_trend", "selctor": {}}, "未知参数"),
    ({"preset": "ma_trend", "top_n": 5.9}, "必须是整数"),
    ({"preset": "ma_trend", "min_avg_amount_yi": "1"}, "必须是有限数值"),
    ({"preset": "ma_trend", "exclude_st": "false"}, "必须是布尔值"),
])
def test_workflow_adapter_rejects_typos_and_coercions_before_core(
        monkeypatch, screen_params, expected):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True, "run_id": "ds_should_not_run", "candidates": []}

    monkeypatch.setattr(ds, "run_data_screen", fake_run)
    outcome = wex.run_graph(_screen_graph({"universe": "all_a"}, screen_params))

    assert outcome["ok"] is False and called is False
    assert expected in outcome["node_errors"][0]["error"]


def test_parallel_terminal_keeps_data_screen_evidence_without_duplicating_single(
        monkeypatch):
    monkeypatch.setattr(ds, "run_data_screen", lambda *args, **kwargs: {
        "ok": True, "run_id": "ds_parallel", "as_of": "2026-08-21",
        "n_matched": 12, "candidates": [{"code": "SH600001"}],
    })
    monkeypatch.setitem(wex._DISPATCH, "analysis", lambda inputs, params, ctx: {
        "report": {"ok": True, "ic": {"rank_ic_mean": 0.02}},
    })
    graph = _screen_graph({"universe": "all_a"})
    graph["nodes"].append({"id": "analysis", "type": "analysis", "params": {}})

    outcome = wex.run_graph(graph)

    assert outcome["terminal"]["kind"] == "analysis"
    assert {(row["kind"], row["node_id"])
            for row in outcome["all_terminals"]} == {
        ("data_screen", "screen"), ("analysis", "analysis"),
    }
    screen_terminal = next(row for row in outcome["all_terminals"]
                           if row["kind"] == "data_screen")
    assert screen_terminal["payload"]["run_id"] == "ds_parallel"

    single = wex.run_graph(_screen_graph({"universe": "all_a"}))
    assert single["terminal"]["kind"] == "data_screen"
    assert "all_terminals" not in single


def test_parallel_diagnostic_payload_is_retained_but_does_not_replace_screen_main(
        monkeypatch):
    monkeypatch.setattr(ds, "run_data_screen", lambda *args, **kwargs: {
        "ok": True, "run_id": "ds_with_risk", "candidates": [],
    })
    monkeypatch.setitem(wex._DISPATCH, "risk", lambda inputs, params, ctx: {
        "risk": {"ok": True, "method": "risk", "var": 0.02},
    })
    graph = _screen_graph({"universe": "all_a"})
    graph["nodes"].extend([
        {"id": "formula", "type": "formula", "params": {"expr": "close"}},
        {"id": "risk", "type": "risk", "params": {}},
    ])
    graph["edges"].append({"from": ["formula", "out"],
                           "to": ["risk", "factor"]})

    outcome = wex.run_graph(graph)

    assert outcome["terminal"]["kind"] == "data_screen"
    by_kind = {row["kind"]: row["payload"] for row in outcome["all_terminals"]}
    assert by_kind["data_screen"]["run_id"] == "ds_with_risk"
    assert by_kind["risk"] == {"ok": True, "method": "risk", "var": 0.02}
