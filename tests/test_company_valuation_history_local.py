from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from guanlan_v2.reports import evidence
from guanlan_v2.research.company_valuation import build_valuation_dag_from_evidence
from guanlan_v2.research.company_vibe_valuation import _valuation_history


TODAY = "2026-09-08"


@pytest.fixture
def local_history(monkeypatch):
    monkeypatch.setattr(evidence, "_hk_today", lambda: date.fromisoformat(TODAY))
    rows = [{"trade_date": f"2026-08-{index:02d}", "pe_ttm": index, "pb": index / 10}
            for index in range(1, 26)]
    calls = []

    def reader(code, start, end):
        calls.append((code, start, end))
        return pd.DataFrame(rows), [{"path": "local-pe.bin", "size": 100, "mtime_ns": 1}]

    monkeypatch.setattr(evidence, "_read_local_valuation_frame", reader)
    return rows, calls


def test_local_history_clips_dates_keeps_missing_coverage_and_excludes_ambiguous_dates(local_history):
    rows, calls = local_history
    rows[1]["pe_ttm"] = None
    rows[2]["pe_ttm"] = -3
    rows.extend([
        {"trade_date": "2026-08-04", "pe_ttm": 999, "pb": 9},
        {"trade_date": "invalid", "pe_ttm": 999, "pb": 9},
        {"trade_date": "2020-09-07", "pe_ttm": 999, "pb": 9},
        {"trade_date": "2026-09-09", "pe_ttm": 999, "pb": 9},
    ])
    result = evidence._local_valuation_history("SH600667", TODAY)
    assert calls == [("SH600667", "2021-09-08", TODAY)]
    assert result["status"] == "available"
    assert result["point_in_time"] is False
    assert result["historical_version_proven"] is False
    pe = result["series"]["ttm_pe"]
    assert len(pe["points"]) == 22
    assert pe["coverage"] == {
        "calendar_rows": 24, "valid_positive_points": 22,
        "missing_dates": ["2026-08-02"], "nonpositive_dates": ["2026-08-03"],
        "duplicate_dates_excluded": ["2026-08-04"], "invalid_date_rows": 1,
        "outside_window_rows_excluded": 2,
    }
    assert len(result["series"]["pb"]["points"]) == 24
    assert all(point["value"] != 999 for point in pe["points"])
    assert {warning["code"] for warning in pe["warnings"]} >= {
        "current_mutable_history_not_pit", "local_history_incomplete_coverage",
    }


@pytest.mark.parametrize("code", ["../../SH600667", "HK00700", "AAPL", "SH600667/other"])
def test_invalid_or_non_a_share_code_never_reaches_local_reader(local_history, code):
    _, calls = local_history
    result = evidence._local_valuation_history(code, TODAY)
    assert result["status"] == "unavailable"
    assert calls == []


@pytest.mark.parametrize("asof", ["2026-09-07", "2026-09-09", "not-a-date"])
def test_mutable_bins_never_backfill_historical_or_future_availability(local_history, asof):
    _, calls = local_history
    result = evidence._local_valuation_history("SH600667", asof)
    assert result["status"] == "unavailable"
    assert calls == []


def _fake_f10(monkeypatch, valuation=None):
    facts = {"valuation": valuation, "snapshot_receipts": [], "unavailable_sources": []}
    monkeypatch.setattr(evidence, "_engine_data_module", lambda name: SimpleNamespace(
        load_facts=lambda code, asof=None: SimpleNamespace(to_dict=lambda: deepcopy(facts)),
    ))


def test_current_valuation_section_connects_pe_and_pb_when_remote_legs_have_no_data(local_history, monkeypatch):
    _fake_f10(monkeypatch)
    network_calls = []
    monkeypatch.setattr(evidence, "_valuation_bands", lambda code: network_calls.append(code))
    section = evidence._sec_valuation("SH600667", TODAY)
    assert network_calls == ["SH600667"]
    assert section["history_5y"] is None
    assert section["history_series"]["metric"] == "ttm_pe"
    assert section["history_pb_series"]["metric"] == "pb"
    assert section["history_local"]["current_research_only"] is True
    assert section["as_of"] == "2026-08-25"


def test_historical_valuation_section_keeps_existing_f10_path_without_reading_current_bins(local_history, monkeypatch):
    _fake_f10(monkeypatch, {"report_period": "2026-06-30", "bvps": 4.1934})
    monkeypatch.setattr(evidence, "_valuation_bands", lambda code: pytest.fail("historical network read"))
    monkeypatch.setattr(evidence, "_local_valuation_history", lambda *a, **kw: pytest.fail("historical local read"))
    section = evidence._sec_valuation("SH600667", "2026-09-07")
    assert section["f10"]["bvps"] == 4.1934
    assert "history_series" not in section
    assert "history_local" not in section


def test_raw_local_series_closes_dag_and_preserves_each_provider_ref_and_limitation(local_history):
    rows, _ = local_history
    rows[0]["pe_ttm"] = None
    local = evidence._local_valuation_history("SH600667", TODAY)
    pack = {
        "generated_at": TODAY, "currency": "CNY",
        "sections": {
            "quote_live": {"price": 10, "unit": "CNY/share", "as_of": TODAY},
            "financials": {"as_of": "2026-06-30", "unit": "CNY", "rows": [{
                "report_date": "2026-06-30", "shares_outstanding": 100,
                "net_income_ttm": 100, "total_debt": 0, "cash_and_equivalents": 0,
                "book_value_per_share": 10,
            }]},
            "valuation": {"history_series": local["series"]["ttm_pe"],
                          "history_pb_series": local["series"]["pb"]},
        },
    }
    dag = build_valuation_dag_from_evidence(pack, as_of=TODAY, include_price_scenarios=False)
    percentile = next(node for node in dag["nodes"] if node["calc_id"] == "valuation.historical_percentile")
    assert percentile["status"] == "complete"
    assert percentile["result"] == 100 * 9 / 24
    pb_percentile = next(node for node in dag["nodes"] if node["calc_id"] == "valuation.pb_historical_percentile")
    assert pb_percentile["status"] == "complete"
    assert pb_percentile["result"] == 40
    assert pb_percentile["depends_on"] == ["valuation.pb"]
    assert "local_history_incomplete_coverage" in {warning["code"] for warning in dag["warnings"]}
    values, refs, issues, warnings, observations = _valuation_history(pack, as_of=TODAY)
    assert len(values) == len(refs) == len(observations) == 24
    assert issues == []
    assert observations[0]["provider_ref"].endswith(":2026-08-02")
    assert observations[-1]["provider_ref"].endswith(":2026-08-25")
    assert "local_history_incomplete_coverage" in {warning["reason_code"] for warning in warnings}


def test_local_reader_failure_remains_explicit_and_does_not_trigger_network(local_history, monkeypatch):
    monkeypatch.setattr(evidence, "_read_local_valuation_frame",
                        lambda *args: (_ for _ in ()).throw(OSError("fixture unavailable")))
    result = evidence._local_valuation_history("SH600667", TODAY)
    assert result["status"] == "unavailable"
    assert result["series"] == {}
    assert "OSError: fixture unavailable" in result["reason"]


def test_pb_percentile_never_accepts_a_ttm_pe_series_with_wrong_label(local_history):
    local = evidence._local_valuation_history("SH600667", TODAY)
    pack = {"generated_at": TODAY, "sections": {
        "valuation": {"history_pb_series": local["series"]["ttm_pe"]},
    }}
    dag = build_valuation_dag_from_evidence(pack, as_of=TODAY, include_price_scenarios=False)
    pb = next(node for node in dag["nodes"] if node["calc_id"] == "valuation.pb_historical_percentile")
    assert pb["status"] == "invalid"
    assert pb["result"] is None
    assert any(gap["code"] == "unsupported_history_metric" for gap in pb["gaps"])
