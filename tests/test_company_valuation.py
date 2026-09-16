from __future__ import annotations

from copy import deepcopy
import json

import pytest

from guanlan_v2.research.company_valuation import (
    CORE_CALC_IDS,
    SCHEMA_VERSION,
    build_valuation_dag,
    build_valuation_dag_from_evidence,
    extract_valuation_inputs,
)


AS_OF = "2026-08-30"


def _metric(value, unit, period, ref, *, as_of=AS_OF, currency=None):
    row = {
        "value": value,
        "unit": unit,
        "period": period,
        "as_of": as_of,
        "ref": ref,
    }
    if currency is not None:
        row["currency"] = currency
    return row


def _complete_inputs():
    return {
        "valuation_as_of": AS_OF,
        "facts": {
            "current_price": _metric(10, "CNY/share", "spot", "quote.price"),
            "shares_outstanding": _metric(
                100, "shares", "2026-06-30", "f10.total_shares",
            ),
            "total_debt": _metric(300, "CNY", "2026-06-30", "bs.debt"),
            "cash_and_equivalents": _metric(100, "CNY", "2026-06-30", "bs.cash"),
            "eps_ttm": _metric(1, "CNY/share", "TTM@2026-06-30", "is.eps_ttm"),
            "net_income_ttm": _metric(100, "CNY", "TTM@2026-06-30", "is.ni_ttm"),
            "forward_eps": _metric(1.25, "CNY/share", "FY2027E", "consensus.eps"),
            "book_value_per_share": _metric(
                2.5, "CNY/share", "2026-06-30", "bs.bvps",
            ),
            "book_value": _metric(250, "CNY", "2026-06-30", "bs.equity"),
        },
        "valuation_history": {
            "metric": "ttm_pe",
            "points": [
                _metric(
                    value, "x", "TTM", f"history.{value}",
                    as_of=f"2024-01-{value:02d}",
                )
                for value in range(1, 21)
            ],
        },
        "scenarios": {
            "bull": {
                "method": "forward_pe",
                "target_multiple": _metric(15, "x", "FY2027E", "scenario.bull.multiple"),
            },
            "base": {
                "method": "forward_pe",
                "target_multiple": _metric(10, "x", "FY2027E", "scenario.base.multiple"),
            },
            "bear": {
                "method": "forward_pe",
                "target_multiple": _metric(6, "x", "FY2027E", "scenario.bear.multiple"),
            },
        },
    }


def _nodes(result):
    return {row["calc_id"]: row for row in result["nodes"]}


def _gap_codes(node):
    return {row["code"] for row in node["gaps"]}


def test_full_dag_calculates_base_multiples_percentile_and_scenarios():
    result = build_valuation_dag(_complete_inputs())
    nodes = _nodes(result)

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["status"] == "complete"
    assert nodes["valuation.market_cap"]["result"] == 1_000
    assert nodes["valuation.market_cap"]["unit"] == "CNY"
    assert nodes["valuation.enterprise_value"]["result"] == 1_200
    assert nodes["valuation.ttm_pe"]["result"] == 10
    assert nodes["valuation.forward_pe"]["result"] == 8
    assert nodes["valuation.pb"]["result"] == 4
    assert nodes["valuation.historical_percentile"]["result"] == 50
    assert nodes["valuation.scenario.bull.implied_price"]["result"] == 18.75
    assert nodes["valuation.scenario.base.implied_price"]["result"] == 12.5
    assert nodes["valuation.scenario.bear.implied_price"]["result"] == 7.5
    assert nodes["valuation.scenario.bull.implied_return"]["result"] == 87.5
    assert nodes["valuation.scenario.base.implied_return"]["result"] == 25
    assert nodes["valuation.scenario.bear.implied_return"]["result"] == -25
    assert {
        (edge["from"], edge["to"]) for edge in result["edges"]
    } >= {
        ("valuation.market_cap", "valuation.enterprise_value"),
        ("valuation.scenario.base.implied_price", "valuation.scenario.base.implied_return"),
        ("input:quote.price", "valuation.market_cap"),
    }


def test_vibe_main_chain_mode_omits_all_price_and_return_scenario_nodes():
    result = build_valuation_dag(
        _complete_inputs(), include_price_scenarios=False,
    )

    assert result["guardrails"]["price_scenario_nodes_included"] is False
    assert result["guardrails"]["vibe_company_main_chain_safe"] is True
    assert all(not row["calc_id"].startswith("valuation.scenario.") for row in result["nodes"])
    assert all("implied_price" not in edge["from"] + edge["to"] for edge in result["edges"])
    assert all("implied_return" not in edge["from"] + edge["to"] for edge in result["edges"])


def test_every_requested_node_is_emitted_with_audit_fields_even_when_empty():
    result = build_valuation_dag({"valuation_as_of": AS_OF, "facts": {}})
    nodes = _nodes(result)

    expected = {
        "valuation.market_cap", "valuation.enterprise_value", "valuation.ttm_pe",
        "valuation.forward_pe", "valuation.pb", "valuation.historical_percentile",
        *(f"valuation.scenario.{case}.{kind}" for case in ("bull", "base", "bear")
          for kind in ("implied_price", "implied_return")),
    }
    assert set(nodes) == expected
    for node in nodes.values():
        assert {
            "calc_id", "formula", "input_refs", "depends_on", "status", "result",
            "unit", "period", "warnings", "gaps",
        } <= set(node)
    assert all(nodes[item]["status"] != "complete" for item in CORE_CALC_IDS)
    assert nodes["valuation.pb"]["status"] == "not_available"
    assert "optional_input_missing" in _gap_codes(nodes["valuation.pb"])


@pytest.mark.parametrize("bad", [0, -1])
def test_zero_or_negative_earnings_never_divides_or_emits_infinity(bad):
    inputs = _complete_inputs()
    inputs["facts"].pop("net_income_ttm")
    inputs["facts"]["eps_ttm"]["value"] = bad

    result = build_valuation_dag(inputs)
    node = _nodes(result)["valuation.ttm_pe"]

    assert node["status"] == "invalid"
    assert node["result"] is None
    assert "non_positive_denominator" in _gap_codes(node)
    assert "Infinity" not in json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_ev_rejects_mixed_currency_and_mixed_balance_period():
    currency_inputs = _complete_inputs()
    currency_inputs["facts"]["total_debt"]["unit"] = "USD"
    currency_result = build_valuation_dag(currency_inputs)
    currency_ev = _nodes(currency_result)["valuation.enterprise_value"]
    assert currency_ev["status"] == "invalid"
    assert "mixed_currency" in _gap_codes(currency_ev)

    period_inputs = _complete_inputs()
    period_inputs["facts"]["cash_and_equivalents"]["period"] = "2025-12-31"
    period_result = build_valuation_dag(period_inputs)
    period_ev = _nodes(period_result)["valuation.enterprise_value"]
    assert period_ev["status"] == "invalid"
    assert "mixed_balance_period" in _gap_codes(period_ev)


def test_scaled_or_ambiguous_units_are_rejected_without_conversion_guess():
    inputs = _complete_inputs()
    inputs["facts"]["total_debt"]["unit"] = "CNY mn"

    result = build_valuation_dag(inputs)
    ev = _nodes(result)["valuation.enterprise_value"]

    assert ev["status"] == "invalid"
    assert "invalid_or_ambiguous_unit" in _gap_codes(ev)
    assert ev["result"] is None


def test_period_contracts_reject_reported_eps_as_ttm_and_ttm_as_forward():
    inputs = _complete_inputs()
    inputs["facts"].pop("net_income_ttm")
    inputs["facts"]["eps_ttm"]["period"] = "FY2026"
    inputs["facts"]["forward_eps"]["period"] = "TTM@2026-06-30"

    nodes = _nodes(build_valuation_dag(inputs))

    assert "period_not_ttm" in _gap_codes(nodes["valuation.ttm_pe"])
    assert "period_not_forward" in _gap_codes(nodes["valuation.forward_pe"])


def test_alternative_pe_methods_must_tie_instead_of_silently_choosing_one():
    inputs = _complete_inputs()
    inputs["facts"]["net_income_ttm"]["value"] = 50

    node = _nodes(build_valuation_dag(inputs))["valuation.ttm_pe"]

    assert node["status"] == "invalid"
    assert node["result"] is None
    assert "conflicting_alternative_calculations" in _gap_codes(node)


def test_ttm_pe_supports_each_explicit_raw_denominator_branch_independently():
    per_share = _complete_inputs()
    per_share["facts"].pop("net_income_ttm")
    per_share_node = _nodes(build_valuation_dag(per_share))["valuation.ttm_pe"]
    assert per_share_node["status"] == "complete"
    assert per_share_node["formula"] == "current_price / eps_ttm"

    total = _complete_inputs()
    total["facts"].pop("eps_ttm")
    total_node = _nodes(build_valuation_dag(total))["valuation.ttm_pe"]
    assert total_node["status"] == "complete"
    assert total_node["formula"] == "market_cap / net_income_ttm"
    assert total_node["depends_on"] == ["valuation.market_cap"]


def test_pb_supports_per_share_or_total_book_value_without_inference():
    per_share = _complete_inputs()
    per_share["facts"].pop("book_value")
    per_share_node = _nodes(build_valuation_dag(per_share))["valuation.pb"]
    assert per_share_node["status"] == "complete"
    assert per_share_node["formula"] == "current_price / book_value_per_share"

    total = _complete_inputs()
    total["facts"].pop("book_value_per_share")
    total_node = _nodes(build_valuation_dag(total))["valuation.pb"]
    assert total_node["status"] == "complete"
    assert total_node["formula"] == "market_cap / book_value"


def test_history_requires_raw_valid_comparable_series():
    empty = _complete_inputs()
    empty["valuation_history"] = {"metric": "ttm_pe", "points": []}
    empty_node = _nodes(build_valuation_dag(empty))["valuation.historical_percentile"]
    assert empty_node["status"] == "incomplete"
    assert "empty_history_series" in _gap_codes(empty_node)

    mixed = _complete_inputs()
    mixed["valuation_history"]["points"][1]["unit"] = "percent"
    mixed_node = _nodes(build_valuation_dag(mixed))["valuation.historical_percentile"]
    assert mixed_node["status"] == "invalid"
    assert "invalid_or_ambiguous_unit" in _gap_codes(mixed_node)

    duplicated = _complete_inputs()
    for point in duplicated["valuation_history"]["points"]:
        point["as_of"] = "2024-01-01"
    duplicate_node = _nodes(build_valuation_dag(duplicated))["valuation.historical_percentile"]
    assert duplicate_node["status"] == "invalid"
    assert "duplicate_history_dates" in _gap_codes(duplicate_node)


def test_scenarios_reject_period_and_currency_mix():
    inputs = _complete_inputs()
    inputs["scenarios"]["base"]["target_multiple"]["period"] = "FY2028E"
    period_node = _nodes(build_valuation_dag(inputs))["valuation.scenario.base.implied_price"]
    assert "mixed_scenario_period" in _gap_codes(period_node)

    inputs = _complete_inputs()
    inputs["scenarios"]["base"] = {
        "method": "direct_price",
        "implied_price": _metric(12, "USD/share", "FY2027E", "scenario.base.price"),
    }
    nodes = _nodes(build_valuation_dag(inputs))
    assert nodes["valuation.scenario.base.implied_price"]["status"] == "complete"
    assert nodes["valuation.scenario.base.implied_return"]["status"] == "invalid"
    assert "mixed_currency" in _gap_codes(nodes["valuation.scenario.base.implied_return"])


def test_scenario_order_warning_keeps_math_but_flags_assumptions():
    inputs = _complete_inputs()
    inputs["scenarios"]["bull"]["target_multiple"]["value"] = 5

    result = build_valuation_dag(inputs)
    scenario_nodes = [node for node in result["nodes"] if node["calc_id"].startswith("valuation.scenario")]

    assert all(any(w["code"] == "scenario_order_inconsistent" for w in node["warnings"])
               for node in scenario_nodes)
    assert _nodes(result)["valuation.scenario.bull.implied_price"]["result"] == 6.25


def test_optional_ev_adjustments_are_used_only_when_explicitly_supplied():
    inputs = _complete_inputs()
    inputs["facts"].update({
        "preferred_equity": _metric(20, "CNY", "2026-06-30", "bs.preferred"),
        "minority_interest": _metric(10, "CNY", "2026-06-30", "bs.minority"),
        "non_operating_investments": _metric(40, "CNY", "2026-06-30", "bs.investments"),
    })

    ev = _nodes(build_valuation_dag(inputs))["valuation.enterprise_value"]

    assert ev["status"] == "complete"
    assert ev["result"] == 1_190
    assert ev["formula"] == (
        "market_cap + total_debt - cash_and_equivalents + preferred_equity "
        "+ minority_interest - non_operating_investments"
    )


def test_future_dates_missing_refs_and_nonfinite_values_are_visible_gaps():
    inputs = _complete_inputs()
    inputs["facts"]["current_price"]["as_of"] = "2026-09-01"
    inputs["facts"]["shares_outstanding"].pop("ref")
    inputs["facts"]["total_debt"]["value"] = float("nan")

    result = build_valuation_dag(inputs)
    codes = {gap["code"] for gap in result["gaps"]}

    assert {"future_dated_input", "missing_input_ref", "invalid_numeric_value"} <= codes
    assert json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_function_is_deterministic_json_safe_and_does_not_mutate_inputs():
    inputs = _complete_inputs()
    before = deepcopy(inputs)

    first = build_valuation_dag(inputs)
    second = build_valuation_dag(inputs)

    assert inputs == before
    assert first == second
    assert json.loads(json.dumps(first, ensure_ascii=False, allow_nan=False)) == first


def test_conservative_extractor_never_reverses_provider_pe_or_summary_percentile():
    pack = {
        "schema_version": "company-research-evidence@2",
        "generated_at": AS_OF,
        "sections": {
            "quote_live": {"price": 10, "as_of": AS_OF},  # no currency/unit
            "company_facts": {
                "as_of": "2026-06-30",
                "valuation": {
                    "pe_ttm": 10, "total_shares": 100, "report_period": "2026-06-30",
                },
            },
            "valuation": {
                "as_of": AS_OF,
                "history_5y": {"pe_ttm": {"current": 10, "percentile": 50}},
            },
        },
    }

    extraction = extract_valuation_inputs(pack)
    facts = extraction["inputs"]["facts"]

    assert "shares_outstanding" in facts
    assert "current_price" not in facts
    assert "eps_ttm" not in facts
    assert "net_income_ttm" not in facts
    assert "valuation_history" not in extraction["inputs"]
    assert {warning["code"] for warning in extraction["warnings"]} >= {
        "provider_multiple_not_reversed", "summary_percentile_not_raw_series",
    }
    dag = build_valuation_dag_from_evidence(pack)
    assert _nodes(dag)["valuation.ttm_pe"]["result"] is None


def test_extractor_maps_only_explicit_raw_pack_fields_and_builds_complete_core_dag():
    pack = {
        "schema_version": "company-research-evidence@2",
        "generated_at": AS_OF,
        "currency": "CNY",
        "sections": {
            "quote_live": {"price": 10, "unit": "CNY", "as_of": AS_OF},
            "company_facts": {
                "as_of": "2026-06-30",
                "valuation": {"total_shares": 100, "report_period": "2026-06-30"},
            },
            "financials": {
                "as_of": AS_OF,
                "unit": "CNY",
                "rows": [{
                    "report_date": "2026-06-30",
                    "total_debt": 300,
                    "cash_and_equivalents": 100,
                    "net_income_ttm": 100,
                    "eps_ttm": 1,
                    "book_value": 250,
                    "book_value_per_share": 2.5,
                }],
            },
            "expectations": {
                "as_of": "2026-08-25", "currency": "CNY",
                "items": [{"year": "2027", "eps": 1.25, "unit": "CNY/share"}],
            },
            "valuation": {
                "as_of": AS_OF,
                "history_series": {
                    "metric": "ttm_pe",
                    "points": [
                        _metric(
                            value, "x", "TTM", f"history.{value}",
                            as_of=f"2024-01-{value:02d}",
                        )
                        for value in range(1, 21)
                    ],
                },
            },
        },
    }

    extraction = extract_valuation_inputs(pack)
    result = build_valuation_dag_from_evidence(pack)
    nodes = _nodes(result)

    assert extraction["status"] == "complete"
    assert extraction["inputs"]["facts"]["forward_eps"]["period"] == "FY2027E"
    assert all(nodes[calc_id]["status"] == "complete" for calc_id in CORE_CALC_IDS)
    assert nodes["valuation.pb"]["status"] == "complete"
    assert nodes["valuation.historical_percentile"]["status"] == "complete"
    assert result["extraction"]["status"] == "complete"


def test_extractor_prefers_newer_financial_share_count_over_lagging_f10():
    pack = {
        "schema_version": "company-research-evidence@2",
        "generated_at": AS_OF,
        "currency": "CNY",
        "sections": {
            "quote_live": {"price": 10, "unit": "CNY", "as_of": AS_OF},
            "company_facts": {
                "as_of": "2026-03-31",
                "valuation": {"total_shares": 99, "report_period": "2026-03-31"},
            },
            "financials": {
                "as_of": "2026-06-30", "unit": "CNY",
                "rows": [{
                    "period": "2026-06-30", "shares_outstanding": 100,
                    "total_debt": 300, "cash_and_equivalents": 100,
                    "book_value": 250,
                }],
            },
            "company_financials_contract": {
                "metrics": {"ttm_net_profit_parent": {
                    "value": 100, "period": "TTM@2026-06-30",
                    "calculation_id": "CAL-TTM-PARENT",
                }},
            },
            "expectations": {
                "as_of": "2026-08-25", "currency": "CNY",
                "items": [{"year": "2027", "eps": 1.25, "unit": "CNY/share"}],
            },
        },
    }

    extraction = extract_valuation_inputs(pack)
    result = build_valuation_dag_from_evidence(pack)

    assert extraction["inputs"]["facts"]["shares_outstanding"]["value"] == 100
    assert extraction["inputs"]["facts"]["shares_outstanding"]["period"] == "2026-06-30"
    assert _nodes(result)["valuation.ttm_pe"]["status"] == "complete"
    assert _nodes(result)["valuation.pb"]["status"] == "complete"


def test_malformed_top_level_input_is_total_instead_of_raising():
    result = build_valuation_dag(None)

    assert result["status"] == "invalid"
    assert result["valuation_as_of"] is None
    assert {gap["code"] for gap in result["gaps"]} >= {
        "missing_valuation_as_of", "invalid_facts_shape",
    }


def _unitless_f10_bvps_pack():
    receipt = {
        "source": "f10:最新提示", "status": "available",
        "data_asof": "2026-08-29", "content_sha256": "a" * 64,
        "historical_version_proven": False, "point_in_time": False,
        "logical_locator": {"code": "SH600667", "source": "f10:最新提示"},
    }
    valuation = {"bvps": 4.1934, "total_shares": 2_091_540_000,
                 "report_period": "2026-06-30"}
    return {
        "code": "SH600667", "generated_at": AS_OF,
        "sections": {
            "quote_live": {"price": 17.22, "unit": "CNY/share", "as_of": AS_OF},
            "company_facts": {"as_of": "2026-06-30", "valuation": deepcopy(valuation),
                              "snapshot_receipts": [deepcopy(receipt)]},
            "valuation": {"as_of": AS_OF, "f10": deepcopy(valuation),
                          "snapshot_receipts": [deepcopy(receipt)],
                          "history_5y": {"pe_ttm": {"n": 913, "percentile": 96.6}}},
            "financials": {"unit": "CNY", "as_of": "2026-06-30", "rows": [
                {"report_date": "2026-06-30", "revenue_cum": 100},
            ]},
        },
    }


def test_dated_a_share_f10_restores_bvps_units_without_inventing_ev_or_history():
    pack = _unitless_f10_bvps_pack()
    before = deepcopy(pack)
    extraction = extract_valuation_inputs(pack)
    facts = extraction["inputs"]["facts"]
    assert facts["book_value_per_share"]["unit"] == "CNY/share"
    assert facts["book_value_per_share"]["value"] == 4.1934
    assert facts["book_value_per_share"]["period"] == "2026-06-30"
    assert facts["book_value_per_share"]["as_of"] == "2026-08-29"
    assert facts["book_value_per_share"]["ref"] == "pack.sections.company_facts.valuation.bvps"
    assert "typed_f10_bvps_unit_restored" in {row["code"] for row in extraction["warnings"]}
    nodes = _nodes(build_valuation_dag_from_evidence(pack))
    assert nodes["valuation.pb"]["status"] == "complete"
    assert nodes["valuation.pb"]["result"] == pytest.approx(17.22 / 4.1934)
    assert nodes["valuation.enterprise_value"]["result"] is None
    assert "total_debt" not in facts and "cash_and_equivalents" not in facts
    assert "valuation_history" not in extraction["inputs"]
    assert nodes["valuation.historical_percentile"]["result"] is None
    assert pack == before


@pytest.mark.parametrize("change", [
    "foreign_security", "wrong_company", "missing_receipt", "failed_receipt",
    "unusable_receipt", "future_snapshot", "snapshot_before_period", "unknown_unit",
    "unusable_section", "invalid_digest",
])
def test_f10_bvps_unit_fallback_requires_same_company_usable_dated_contract(change):
    pack = _unitless_f10_bvps_pack()
    del pack["sections"]["company_facts"]
    section = pack["sections"]["valuation"]
    receipt = section["snapshot_receipts"][0]
    if change == "foreign_security":
        pack["code"] = "HK00700"
        receipt["logical_locator"]["code"] = "HK00700"
    elif change == "wrong_company":
        receipt["logical_locator"]["code"] = "SH600000"
    elif change == "missing_receipt":
        section.pop("snapshot_receipts")
    elif change == "failed_receipt":
        receipt["status"] = "failed"
    elif change == "unusable_receipt":
        receipt["usable"] = False
    elif change == "future_snapshot":
        receipt["data_asof"] = "2026-09-01"
    elif change == "snapshot_before_period":
        receipt["data_asof"] = "2026-06-01"
    elif change == "unknown_unit":
        section["f10"]["unit"] = "万元/股"
    elif change == "unusable_section":
        section["usable"] = False
    elif change == "invalid_digest":
        receipt["content_sha256"] = "not-a-digest"
    facts = extract_valuation_inputs(pack)["inputs"]["facts"]
    assert "book_value_per_share" not in facts
    assert _nodes(build_valuation_dag_from_evidence(pack))["valuation.pb"]["result"] is None


def test_same_period_conflicting_f10_bvps_does_not_silently_choose_one():
    pack = _unitless_f10_bvps_pack()
    pack["sections"]["valuation"]["f10"]["bvps"] = 5
    extraction = extract_valuation_inputs(pack)
    assert "book_value_per_share" not in extraction["inputs"]["facts"]
    assert "conflicting_book_value_per_share" in {row["code"] for row in extraction["gaps"]}


def test_explicit_foreign_bvps_currency_is_not_relabelled_as_cny():
    pack = _unitless_f10_bvps_pack()
    del pack["sections"]["company_facts"]
    pack["sections"]["valuation"]["f10"]["currency"] = "USD"
    facts = extract_valuation_inputs(pack)["inputs"]["facts"]
    assert facts["book_value_per_share"]["unit"] == "USD/share"
    pb = _nodes(build_valuation_dag_from_evidence(pack))["valuation.pb"]
    assert pb["result"] is None
    assert "mixed_currency" in _gap_codes(pb)
