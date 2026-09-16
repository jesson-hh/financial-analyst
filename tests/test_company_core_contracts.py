from __future__ import annotations

import pytest

from guanlan_v2.research.company_core_contracts import (
    evaluate_estimates_contract,
    evaluate_financials_contract,
    evaluate_profile_contract,
)


def _financial_rows():
    quarters = {
        2024: [(100, 10, 8), (120, 12, 9), (130, 13, 10), (150, 15, 12)],
        2025: [(140, 14, 11), (150, 15, 12), (160, 16, 13), (170, 17, 14)],
        2026: [(180, 18, 15), (190, 19, 16)],
    }
    suffixes = ("03-31", "06-30", "09-30", "12-31")
    rows = []
    for year, values in quarters.items():
        cumulative = [0.0, 0.0, 0.0]
        for suffix, trio in zip(suffixes, values):
            cumulative = [left + right for left, right in zip(cumulative, trio)]
            period = f"{year}-{suffix}"
            rows.append({
                "period": period,
                "revenue_cum": cumulative[0],
                "net_profit_parent_cum": cumulative[1],
                "net_profit_deducted_cum": cumulative[2],
                "unit": "CNY",
                "source_ref": f"official:{period}",
            })
    return rows


def _pack():
    return {
        "code": "SH603986",
        "sections": {
            "company_profile": {
                "code": "SH603986", "name": "兆易创新", "market": "SSE",
                "listing_status": "listed", "listing_date": "2016-08-18",
                "source_authority": "上海证券交易所",
            },
            "quote_live": {"price": 101.2, "as_of": "2026-08-30T09:30:00+08:00"},
            "financials": {
                "basis": "cumulative_ytd", "unit": "CNY", "rows": _financial_rows(),
            },
            "expectations": {
                "source": "ths_eps_forecast",
                "items": [
                    {"year": "2026", "n_org": 21, "eps_min": 4.2,
                     "eps_mean": 13.4, "eps_max": 22.9},
                    {"year": "2027", "n_org": 21, "eps_min": 5.3,
                     "eps_mean": 16.5, "eps_max": 25.5},
                    {"year": "2028", "n_org": 21, "eps_min": 6.8,
                     "eps_mean": 18.3, "eps_max": 30.7},
                ],
            },
        },
    }


def _calendar():
    return {
        "asset": {"id": "wa_calendar"},
        "snapshot": {
            "dataset_id": "research.trade_disclosure_calendar.live",
            "rows": [
                {"record_type": "trading_day", "date": "2026-08-28"},
                {"record_type": "trading_day", "date": "2026-08-31"},
            ],
        },
    }


def test_latest_ytd_yoy_has_exact_same_period_lineage_not_quarter_or_ttm():
    result = evaluate_financials_contract(_pack())
    metric = result["metrics"]["revenue_ytd_yoy"]
    assert metric["value"] == pytest.approx(370 / 290 - 1)
    assert metric["period"] == "2026-06-30"
    calc = next(row for row in result["calculations"]
                if row["calculation_id"] == metric["calculation_id"])
    assert calc["status"] == "complete"
    assert [row["period"] for row in calc["inputs"]] == ["2026-06-30", "2025-06-30"]
    assert [row["value"] for row in calc["inputs"]] == [370, 290]
    assert all(row["field"] == "revenue_cum" for row in calc["inputs"])
    assert all(row["ref"].startswith("pack.sections.financials.rows[")
               for row in calc["inputs"])


@pytest.mark.parametrize("base", [0, -10])
def test_ytd_yoy_nonpositive_base_is_not_fabricated(base):
    pack = _pack()
    prior = next(row for row in pack["sections"]["financials"]["rows"]
                 if row["period"] == "2025-06-30")
    prior["net_profit_parent_cum"] = base
    metric = evaluate_financials_contract(pack)["metrics"]["net_profit_parent_ytd_yoy"]
    assert metric["value"] is None
    assert metric["status"] == "incomplete"


def test_ytd_yoy_missing_prior_does_not_substitute_adjacent_period():
    pack = _pack()
    section = pack["sections"]["financials"]
    section["rows"] = [r for r in section["rows"] if r["period"] != "2025-06-30"]
    result = evaluate_financials_contract(pack)
    assert "revenue_ytd_yoy" not in result["metrics"]
    assert any(g["code"] == "cumulative_yoy_prior_period_missing" for g in result["gaps"])


@pytest.mark.parametrize("bad_input", ["basis", "unit", "failed", "conflict"])
def test_ytd_yoy_rejects_unverified_or_conflicting_statements(bad_input):
    pack = _pack()
    section = pack["sections"]["financials"]
    if bad_input == "basis":
        section["basis"] = "unknown"
    elif bad_input == "unit":
        section["unit"] = "USD"
    elif bad_input == "failed":
        section["status"] = "failed"
    else:
        section["rows"].append(section["rows"][-1] | {"revenue_cum": 999})
    assert "revenue_ytd_yoy" not in evaluate_financials_contract(pack)["metrics"]


@pytest.mark.parametrize("bad", [{"usable": False}, {"future": True}, {"status": "future"},
                                  {"status": "error"}, {"stale": True}, {"ok": False},
                                  {"status": "unknown"}])
@pytest.mark.parametrize("scope", ["section", "current", "prior"])
def test_ytd_yoy_rejects_unusable_section_and_input_rows(bad, scope):
    pack = _pack()
    section = pack["sections"]["financials"]
    if scope == "section":
        section.update(bad)
    else:
        period = "2026-06-30" if scope == "current" else "2025-06-30"
        next(row for row in section["rows"] if row["period"] == period).update(bad)
    assert "revenue_ytd_yoy" not in evaluate_financials_contract(pack)["metrics"]


def test_ytd_yoy_rejects_record_after_pack_cutoff():
    pack = _pack()
    pack["as_of"] = "2026-03-31"
    assert "revenue_ytd_yoy" not in evaluate_financials_contract(pack)["metrics"]


def test_profile_accepts_live_adapter_trading_calendar_record_type():
    pack = _pack()
    calendar = _calendar()
    for row in calendar["snapshot"]["rows"]:
        row["record_type"] = "trading_calendar"
        row["trading"] = True

    result = evaluate_profile_contract(
        pack, [calendar], subject={"code": "SH603986", "name": "兆易创新"},
        as_of="2026-08-30",
    )

    assert result["status"] == "complete"
    assert result["calendar"]["trading_days"]


def test_profile_does_not_treat_closed_calendar_rows_or_retrieval_as_market_time():
    pack = _pack()
    pack["sections"]["quote_live"] = {
        "price": 101.2,
        "retrieved_at": "2026-09-05T13:10:34+08:00",
    }
    calendar = {
        "asset": {"id": "wa_calendar"},
        "snapshot": {
            "dataset_id": "research.trade_disclosure_calendar.live",
            "rows": [
                {"record_type": "trading_calendar", "date": "2026-09-05",
                 "trading": False, "reason": "exchange_closed"},
                {"record_type": "trading_calendar", "date": "2026-09-07",
                 "trading": True, "reason": "scheduled_session"},
            ],
        },
    }

    result = evaluate_profile_contract(
        pack, [calendar], subject={"code": "SH603986", "name": "兆易创新"},
        as_of="2026-09-05",
    )

    assert result["status"] == "incomplete"
    assert result["quote"]["as_of"] is None
    assert result["calendar"]["trading_days"] == ["2026-09-07"]
    assert any(gap["code"] == "quote_missing_or_stale" for gap in result["gaps"])


def test_profile_contract_requires_verified_listing_quote_and_calendar():
    result = evaluate_profile_contract(
        _pack(), [_calendar()],
        subject={"code": "SH603986", "name": "兆易创新"},
        as_of="2026-08-30",
    )

    assert result["status"] == "complete"
    assert result["subject"]["market"] == "SSE"
    assert result["subject"]["moat_label"] == "待补"
    assert any(gap["code"] == "moat_label_pending" and not gap["critical"]
               for gap in result["gaps"])


def test_profile_contract_does_not_infer_listing_state_from_quote():
    pack = _pack()
    pack["sections"]["company_profile"] = {}

    result = evaluate_profile_contract(
        pack, [_calendar()], subject={"code": "SH603986", "name": "兆易创新"},
        as_of="2026-08-30",
    )

    assert result["status"] == "incomplete"
    assert {gap["code"] for gap in result["gaps"]} >= {
        "issuer_name_not_verified", "listing_status_unverified",
    }


def test_financial_contract_quarterises_and_builds_traceable_ttm_metrics():
    result = evaluate_financials_contract(_pack())

    assert result["status"] == "complete"
    assert len(result["single_quarters"]) == 10
    assert result["metrics"]["latest_quarter"]["quarter"] == "2026Q2"
    assert result["metrics"]["latest_quarter"]["net_profit_deducted"] == 16
    assert result["metrics"]["ttm_net_profit_parent"]["value"] == 70
    assert result["metrics"]["ttm_net_profit_deducted"]["value"] == 58
    assert result["metrics"]["net_profit_deducted_qoq"]["value"] == pytest.approx(1 / 15)
    ids = [row["calculation_id"] for row in result["calculations"]]
    assert len(ids) == len(set(ids))
    assert all(row["inputs"] for row in result["calculations"])


def test_financial_contract_rejects_partial_fallback_without_deducted_profit():
    pack = _pack()
    for row in pack["sections"]["financials"]["rows"]:
        row.pop("net_profit_deducted_cum")

    result = evaluate_financials_contract(pack)

    assert result["status"] == "incomplete"
    assert any(gap["code"] == "insufficient_cumulative_periods" for gap in result["gaps"])


def test_financial_contract_rejects_eight_sparse_quarters_as_a_ttm_window():
    pack = _pack()
    pack["sections"]["financials"]["rows"] = [
        {
            "period": f"{year}-03-31",
            "revenue_cum": 100 + year,
            "net_profit_parent_cum": 10 + year,
            "net_profit_deducted_cum": 8 + year,
            "unit": "CNY",
            "source_ref": f"official:{year}-03-31",
        }
        for year in range(2019, 2027)
    ]

    result = evaluate_financials_contract(pack)

    assert result["status"] == "incomplete"
    assert len(result["single_quarters"]) == 8
    assert len(result["latest_consecutive_quarters"]) == 1
    assert "ttm_net_profit_parent" not in result["metrics"]
    assert any(gap["code"] == "non_contiguous_single_quarters" for gap in result["gaps"])


def test_estimates_contract_closes_t_to_t2_and_reports_dispersion():
    result = evaluate_estimates_contract(_pack(), as_of="2026-08-30")

    assert result["status"] == "complete"
    assert result["required_years"] == [2026, 2027, 2028]
    assert result["consensus_dispersion"] == {
        "year": 2028,
        "min": 6.8,
        "mean": 18.3,
        "max": 30.7,
        "ratio": pytest.approx(30.7 / 6.8),
        "unit": "CNY/share",
        "ratio_unit": "倍",
        "range_over_mean": pytest.approx((30.7 - 6.8) / 18.3),
        "source_refs": ["pack.sections.expectations.items[2]"],
        "calculation_id": result["years"][2]["dispersion_calculation_id"],
    }
    assert result["years"][2]["dispersion"] == pytest.approx(30.7 / 6.8)
    assert next(row for row in result["calculations"] if row["metric"] == "consensus_dispersion")[
        "formula"
    ] == "consensus_dispersion = eps_max / eps_min"
    assert result["forward_cagr_t_to_t2"]["calculation_id"].startswith("CAL-EST-")
    assert all(row["dispersion_calculation_id"] for row in result["years"])
    assert not result["warnings"]


def test_estimates_contract_does_not_use_broker_reports_as_consensus():
    pack = _pack()
    pack["sections"]["expectations"] = None
    pack["sections"]["research_views"] = {
        "items": [{"year": 2026, "eps": 12.0, "institution": "one broker"}],
    }

    result = evaluate_estimates_contract(pack, as_of="2026-08-30")

    assert result["status"] == "incomplete"
    assert set(result["excluded_sources"]) >= {"research_views", "peer_estimates"}
    assert len([gap for gap in result["gaps"]
                if gap["code"] == "consensus_year_incomplete"]) == 3
