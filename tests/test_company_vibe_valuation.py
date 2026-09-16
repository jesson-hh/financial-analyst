from __future__ import annotations

import math

import pytest

from guanlan_v2.research.company_vibe_valuation import (
    ANCHORS,
    CALC_VERSION,
    FUNCTIONS,
    build_vibe_valuation_contract,
)


def _pack(*, history=True):
    valuation = {}
    if history:
        valuation["history_series"] = {
            "metric": "ttm_pe",
            "points": [
                {
                    "value": value, "unit": "倍", "date": f"2024-01-{index + 1:02d}",
                    "ref": f"hist:{index}",
                }
                for index, value in enumerate(range(10, 40))
            ],
        }
    return {
        "sections": {
            "quote_live": {
                "price": 100, "unit": "CNY/share", "as_of": "2026-08-30",
                "total_market_cap": {"value": 10_000_000_000, "unit": "元"},
            },
            "financials": {
                "rows": [{
                    "period": "2026-06-30", "shares_outstanding": 100_000_000,
                    "balance_source_ref": "balance:2026Q2",
                }],
            },
            "valuation": valuation,
        },
    }


def _financials():
    return {
        "metrics": {
            "latest_quarter": {
                "net_profit_deducted": 100_000_000,
                "unit": "CNY", "period": "2026-06-30",
                "calculation_ids": {"net_profit_deducted": "CAL-FIN-LATEST"},
            },
            "ttm_net_profit_parent": {
                "value": 400_000_000, "period": "TTM@2026-06-30",
                "unit": "CNY", "calculation_id": "CAL-FIN-TTM",
            },
            "ttm_net_profit_parent_yoy": {
                "value": 0.20, "period": "TTM@2026-06-30",
                "calculation_id": "CAL-FIN-YOY",
            },
        },
    }


def _estimates():
    return {
        "years": [{
            "year": 2026, "eps_mean": 5.0,
            "unit": "CNY/share",
            "source_refs": ["consensus:2026"],
        }],
        "forward_cagr_t_to_t2": {
            "value": 0.25, "unit": "ratio", "period": "2026..2028",
            "calculation_id": "CAL-EST-CAGR",
        },
    }


def _by_function(result):
    return {row["function"]: row for row in result["calculations"]}


def test_exact_vibe_cells_and_four_anchor_years_without_price_targets():
    result = build_vibe_valuation_contract(
        _pack(), _financials(), _estimates(), as_of="2026-08-30",
    )
    rows = _by_function(result)

    assert result["status"] == "complete"
    assert result["calc_version"] == CALC_VERSION
    assert set(FUNCTIONS) <= set(rows)
    assert rows["pe_deducted_annualized"]["output"]["value"] == 25
    assert rows["forward_pe"]["output"]["value"] == 20
    assert rows["pe_ttm_from_parts"]["output"]["value"] == 25
    assert rows["peg"]["output"]["value"] == 1
    scenarios = rows["pe_digestion_scenarios"]["output"]["details"]["scenarios"]
    assert rows["pe_digestion_scenarios"]["output"]["value"] == scenarios
    assert rows["pe_digestion_scenarios"]["result"]["value"] == scenarios
    assert set(scenarios) == set(ANCHORS)
    assert scenarios["景气延续"]["value"] == 0
    assert scenarios["周期重定级_下沿"]["value"] == pytest.approx(
        math.log(25 / 18) / math.log(1.25)
    )
    assert result["guardrails"]["emits_price_target"] is False
    assert all("implied_price" not in str(row) for row in result["calculations"])
    assert all(row["calc_id"].startswith("calc-") for row in result["calculations"])
    assert all(row["calc_version"] == CALC_VERSION for row in result["calculations"])
    assert rows["market_cap"]["output"]["details"]["source"] == "quote_total_market_cap"
    assert rows["pe_ttm_from_parts"]["formula"] == "market_cap / ttm_parent_profit"


def test_missing_raw_history_is_an_explicit_optional_gap_not_a_fake_percentile():
    result = build_vibe_valuation_contract(
        _pack(history=False), _financials(), _estimates(), as_of="2026-08-30",
    )
    percentile = _by_function(result)["percentile_rank"]

    assert result["status"] == "complete"
    assert percentile["output"]["status"] == "not_meaningful"
    assert percentile["output"]["value"] is None
    assert any(gap["operation"] == "percentile_rank" and not gap["critical"]
               for gap in result["gaps"])


def test_history_metric_dates_and_as_of_are_validated_before_percentile():
    pack = _pack()
    pack["sections"]["valuation"]["history_series"]["metric"] = "pb"

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    percentile = _by_function(result)["percentile_rank"]

    assert percentile["output"]["status"] == "not_meaningful"
    assert percentile["output"]["details"]["issues"][0]["reason_code"] == (
        "history_metric_mismatch"
    )
    assert result["status"] == "complete"


def test_history_skips_undated_duplicate_and_future_points_before_counting_samples():
    pack = _pack()
    points = pack["sections"]["valuation"]["history_series"]["points"]
    points[0].pop("date")
    points[1]["date"] = points[2]["date"]
    points[3]["date"] = "2026-09-01"

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    percentile = _by_function(result)["percentile_rank"]

    assert percentile["output"]["status"] == "ok"
    assert percentile["output"]["details"]["n"] == 27
    warning_codes = {row["reason_code"] for row in percentile["warnings"]}
    assert {
        "history_date_missing", "history_date_duplicate", "history_point_after_as_of",
    } <= warning_codes


def test_future_financial_periods_never_enter_vibe_pe_or_judgement_math():
    financials = _financials()
    financials["metrics"]["latest_quarter"]["period"] = "2026-09-30"
    financials["metrics"]["ttm_net_profit_parent"]["period"] = "TTM@2026-09-30"
    financials["metrics"]["ttm_net_profit_parent_yoy"]["period"] = "TTM@2026-09-30"

    result = build_vibe_valuation_contract(
        _pack(), financials, _estimates(), as_of="2026-08-30",
    )
    rows = _by_function(result)

    assert rows["pe_deducted_annualized"]["output"]["status"] == "error"
    assert rows["pe_ttm_from_parts"]["output"]["status"] == "error"
    assert rows["forward_vs_ttm_judgement"]["output"]["status"] == "error"
    assert result["status"] == "incomplete"


def test_quote_market_cap_has_priority_over_price_times_shares():
    pack = _pack()
    pack["sections"]["financials"]["rows"][0]["shares_outstanding"] = 1

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    rows = _by_function(result)

    assert rows["market_cap"]["output"]["value"] == 10_000_000_000
    assert rows["pe_deducted_annualized"]["output"]["value"] == 25


def test_missing_quote_market_cap_uses_auditable_price_times_shares_fallback():
    pack = _pack()
    pack["sections"]["quote_live"].pop("total_market_cap")

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    market_cap = _by_function(result)["market_cap"]

    assert market_cap["output"]["value"] == 10_000_000_000
    assert market_cap["output"]["details"] == {
        "source": "derived",
        "method": "current_price_times_shares_outstanding",
        "provider_shares_ref": "balance:2026Q2",
    }
    assert set(market_cap["inputs"]) == {"current_price", "shares_outstanding"}
    assert set(market_cap["input_refs"]) == {
        "pack.sections.quote_live.price",
        "pack.sections.financials.rows[0].shares_outstanding",
    }
    assert market_cap["warnings"][0]["reason_code"] == (
        "quote_market_cap_missing_derived_fallback"
    )


def test_missing_balance_shares_uses_typed_f10_total_shares_with_exact_provenance():
    pack = _pack()
    pack["sections"]["quote_live"].update({
        "price": 17.22, "unit": "CNY/share", "as_of": "2026-09-05",
    })
    pack["sections"]["quote_live"].pop("total_market_cap")
    pack["sections"]["financials"]["rows"] = [{"period": "2026-06-30"}]
    f10_valuation = {
        "total_shares": 2_091_540_000,
        "report_period": "2026-06-30",
    }
    pack["sections"]["company_facts"] = {
        "as_of": "2026-06-30", "valuation": dict(f10_valuation),
    }
    pack["sections"]["valuation"].update({
        "as_of": "2026-09-05", "f10": dict(f10_valuation),
    })

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-09-05",
    )
    market_cap = _by_function(result)["market_cap"]

    assert result["status"] == "complete"
    assert market_cap["output"]["value"] == 36_016_318_800
    assert market_cap["output"]["details"] == {
        "source": "derived",
        "method": "current_price_times_shares_outstanding",
        "provider_shares_ref": (
            "pack.sections.company_facts.valuation.total_shares"
        ),
    }
    assert market_cap["inputs"]["shares_outstanding"] == {
        "value": 2_091_540_000,
        "unit": "股",
        "period": "2026-06-30",
        "as_of": "2026-06-30",
    }
    assert set(market_cap["input_refs"]) == {
        "pack.sections.quote_live.price",
        "pack.sections.company_facts.valuation.total_shares",
    }
    assert not any(gap["reason_code"] == "market_cap_unavailable"
                   for gap in result["gaps"])


def test_conflicting_f10_total_share_sources_fail_closed():
    pack = _pack()
    pack["sections"]["quote_live"].pop("total_market_cap")
    pack["sections"]["financials"]["rows"] = [{"period": "2026-06-30"}]
    pack["sections"]["company_facts"] = {
        "as_of": "2026-06-30",
        "valuation": {"total_shares": 100_000_000, "report_period": "2026-06-30"},
    }
    pack["sections"]["valuation"].update({
        "as_of": "2026-08-30",
        "f10": {"total_shares": 101_000_000, "report_period": "2026-06-30"},
    })

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    market_cap = _by_function(result)["market_cap"]

    assert result["status"] == "incomplete"
    assert market_cap["output"]["status"] == "error"
    assert market_cap["output"]["value"] is None
    assert market_cap["input_refs"] == ["pack.sections.quote_live.price"]
    assert any(gap["reason_code"] == "market_cap_unavailable"
               for gap in result["gaps"])


def test_future_f10_total_share_period_never_enters_market_cap():
    pack = _pack()
    pack["sections"]["quote_live"].pop("total_market_cap")
    pack["sections"]["financials"]["rows"] = [{"period": "2026-06-30"}]
    pack["sections"]["company_facts"] = {
        "as_of": "2026-09-30",
        "valuation": {"total_shares": 100_000_000, "report_period": "2026-09-30"},
    }

    result = build_vibe_valuation_contract(
        pack, _financials(), _estimates(), as_of="2026-08-30",
    )
    market_cap = _by_function(result)["market_cap"]

    assert market_cap["output"]["status"] == "error"
    assert market_cap["output"]["value"] is None
    assert any(gap["reason_code"] == "market_cap_unavailable"
               for gap in result["gaps"])


def test_missing_consensus_keeps_valuation_incomplete_but_preserves_other_math():
    result = build_vibe_valuation_contract(
        _pack(), _financials(), {"years": [], "forward_cagr_t_to_t2": {}},
        as_of="2026-08-30",
    )
    rows = _by_function(result)

    assert result["status"] == "incomplete"
    assert rows["pe_deducted_annualized"]["output"]["status"] == "ok"
    assert rows["forward_pe"]["output"]["status"] == "error"
    assert rows["peg"]["output"]["status"] == "error"
