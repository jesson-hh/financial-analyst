from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from guanlan_v2.datafeed import company_context_live as live
from guanlan_v2.reports import evidence
from guanlan_v2.research.company_hub import build_company_research_hub


def _holder(code="600519", venue="SH", **kwargs):
    return {"SECURITY_CODE": code, "SECUCODE": f"{code}.{venue}",
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 1,
            "HOLDER_NAME": "Example holder", "HOLD_NUM": 1000,
            "HOLD_NUM_RATIO": 12.5, **kwargs}


def test_disclosure_period_subject_and_ratio_basis_are_preserved(monkeypatch):
    monkeypatch.setattr(live, "_json", lambda *a, **k: {
        "sdgd": [_holder(), _holder("000001", "SZ"),
                 _holder(END_DATE="2099-06-30")],
        "sdltgd": [_holder(FREE_HOLDNUM_RATIO=15.4)],
        "gdrs": [_holder(HOLDER_TOTAL_NUM=1234, TOTAL_NUM_RATIO=-3.2)],
    })
    out = live.query_shareholder_disclosures("600519.SH")
    assert out["status"] == "partial"
    assert out["as_of"] == "2026-06-30"
    assert out["coverage"]["rejected_rows"] == 2
    assert out["holders"]["a_share_holders"] == 1234
    top = out["holders"]["top_holders"][0]
    assert top["shares"] == 1000 and top["pct"] == 12.5
    assert top["pct_basis"] == "total_shares"
    assert out["top_float_holders"][0]["pct_basis"] == "float_shares"
    assert top["disclosure_date"] is None
    assert out["point_in_time"] is False
    assert out["retrieved_at"] != out["as_of"]


def test_mixed_ownership_dates_do_not_mix_holder_count_into_other_period(monkeypatch):
    monkeypatch.setattr(live, "_json", lambda *a, **k: {
        "sdgd": [_holder()], "sdltgd": [],
        "gdrs": [_holder(END_DATE="2026-03-31", HOLDER_TOTAL_NUM=200)],
    })
    out = live.query_shareholder_disclosures("SH600519")
    assert out["holders"]["a_share_holders"] is None
    assert out["holder_counts"][0]["report_date"] == "2026-03-31"


def test_empty_response_is_distinct_from_bad_schema_and_network_failure(monkeypatch):
    monkeypatch.setattr(live, "_json", lambda *a, **k: {"sdgd": [], "gdrs": []})
    empty = live.query_shareholder_disclosures("SH600519")
    assert empty["status"] == "empty" and empty["as_of"] is None
    monkeypatch.setattr(live, "_json", lambda *a, **k: {})
    assert live.query_shareholder_disclosures("SH600519")["status"] == "unavailable"
    monkeypatch.setattr(live, "_json", lambda *a, **k: {"sdgd": [_holder("000001", "SZ")]})
    mismatched = live.query_shareholder_disclosures("SH600519")
    assert mismatched["status"] == "unavailable" and mismatched["holders"] is None

    def failed(*a, **k):
        raise live.requests.Timeout("secret-bearing URL must not escape")
    monkeypatch.setattr(live, "_json", failed)
    failed_out = live.query_shareholder_disclosures("SH600519")
    assert failed_out["error"] == "Timeout"
    assert "secret-bearing" not in json.dumps(failed_out)


@pytest.mark.parametrize("code", ["SH000001.SZ", "600519,000001", "../600519", "https://evil/",
                                   "SH920001", "SZ600519", "SH000001"])
def test_subject_query_is_one_valid_code(code):
    with pytest.raises(ValueError):
        live.query_shareholder_disclosures(code)


def test_beijing_920_uses_platform_symbol_normalisation():
    assert live._code("920001") == "BJ920001"
    assert live._code("920001.BJ") == "BJ920001"


def test_news_exact_match_links_and_dates_without_fake_judgment(monkeypatch):
    def fetch(url, params, **kwargs):
        assert url == live._NEWS_URL
        query = json.loads(params["param"])
        assert query["keyword"] == "600519"
        assert query["param"]["cmsArticleWebOld"]["pageSize"] == 20
        return {"code": 0, "result": {"cmsArticleWebOld": [
            {"title": "<em>600519</em> results", "date": "2026-08-15 10:00:00",
             "content": "External text", "mediaName": "Example media",
             "url": "https://finance.eastmoney.com/a/202608151234.html"},
            {"title": "600519 later news", "date": "2026-09-01 12:00:00",
             "url": "https://evil.invalid/"},
            {"title": "16005190", "date": "2026-09-01"},
            {"title": "600519", "date": "2099-09-01"},
        ]}}
    monkeypatch.setattr(live, "_json", fetch)
    out = live.query_company_news("SH600519", limit=99)
    assert out["status"] == "ok" and out["coverage"]["rejected_rows"] == 2
    assert len(out["items"]) == 2
    assert out["as_of"] == "2026-09-01 12:00:00"
    assert out["items"][0]["source_ref"] == out["source_ref"]
    assert out["items"][1]["title"] == "600519 results"
    assert all(row["issuer_fact"] is False for row in out["items"])
    assert out["tag"] is None and out["judgment_status"] == "not_run"


def test_current_company_facts_can_use_live_ownership_without_local_stocks(monkeypatch):
    def no_local(*a, **k):
        raise ImportError("F10 unavailable")
    monkeypatch.setattr(evidence, "_engine_data_module", no_local)
    monkeypatch.setattr(live, "query_shareholder_disclosures", lambda code: {
        "status": "ok", "as_of": "2026-06-30", "holders": {"top_holders": [
            {"name": "holder", "shares": 200}], "report_date": "2026-06-30"},
        "holder_counts": [], "source": "eastmoney_shareholder_disclosures",
    })
    result = evidence._sec_company_facts("SH600519")
    assert result["holders"]["top_holders"][0]["shares"] == 200
    assert result["valuation"] is None
    assert result["ownership_live"]["source"] == "eastmoney_shareholder_disclosures"
    assert result["unavailable_sources"][0]["source"] == "local_f10"


def test_historical_facts_never_fetch_current_ownership(monkeypatch):
    monkeypatch.setattr(evidence, "_engine_data_module", lambda name: SimpleNamespace(
        load_facts=lambda *a, **k: SimpleNamespace(to_dict=lambda: {
            "holders": {"report_date": "2026-03-31", "top_holders": [{"name": "old"}]},
        })))
    def forbidden(*a, **k):
        raise AssertionError("current endpoint called during historical run")
    monkeypatch.setattr(live, "query_shareholder_disclosures", forbidden)
    result = evidence._sec_company_facts("SH600519", asof="2026-06-30")
    assert "ownership_live" not in result
    assert result["holders"]["top_holders"][0]["name"] == "old"


def test_sentiment_market_read_does_not_fill_company_tag(monkeypatch, tmp_path):
    from guanlan_v2.datafeed import sentiment
    monkeypatch.setattr(sentiment, "_ROOT", tmp_path)
    monkeypatch.setattr(sentiment, "read_summary", lambda code: {
        "date": "2026-09-08", "judged": False, "judgment": None,
        "market": {"market_read": "market only", "market_tilt": "positive"},
    })
    monkeypatch.setattr(live, "query_company_news", lambda *a, **k: {
        "status": "empty", "items": [], "as_of": None,
        "retrieved_at": "2026-09-08T12:00:00+08:00", "warnings": [],
    })
    result = evidence._sec_sentiment("SH600519")
    assert result["status"] == "partial"
    assert result["tag"] is None and result["read"] is None
    assert result["as_of"] is None
    assert result["judgment_status"] == "not_run"
    assert result["market_context"]["market_read"] == "market only"


def test_personal_holding_absence_states_and_hub_preserves_them(monkeypatch, tmp_path):
    from guanlan_v2.seats import api
    monkeypatch.setattr(api, "_LEDGER_LOG", tmp_path / "missing-ledger.jsonl")
    monkeypatch.setattr(api, "_ledger_events", lambda: [])
    missing = evidence._sec_holding("SH600519")
    assert missing["status"] == "not_configured" and missing["held"] is None
    monkeypatch.setattr(api, "_ledger_events", lambda: [{"kind": "open", "cash": 100}])
    absent = evidence._sec_holding("SH600519")
    assert absent["status"] == "not_applicable" and absent["held"] is False
    for section, status in [(missing, "not_configured"), (absent, "not_applicable")]:
        hub = build_company_research_hub(sections={"holding": section}, as_of="2026-09-08")
        source = next(row for row in hub["source_register"] if row["source_id"] == "personal_portfolio_ledger")
        assert source["status"] == status
        assert source["evidence_id"] is None
        assert source["type"] == "context"
        assert any(gap["kind"] == "source_not_configured" for gap in hub["gaps"]) == (status == "not_configured")


def test_empty_sections_are_not_counted_as_evidence_and_absent_holding_not_a_gap(monkeypatch, tmp_path):
    for name in evidence._SECTION_NAMES:
        monkeypatch.setattr(evidence, f"_sec_{name}", lambda code: {"as_of": "2026-09-08"})
    monkeypatch.setattr(evidence, "_sec_sentiment", lambda code: {"status": "empty", "as_of": None})
    monkeypatch.setattr(evidence, "_sec_holding", lambda code: {"status": "not_applicable", "held": False})
    result = evidence.build_evidence_pack("SH600519", tmp_path)
    assert "sentiment" in result["coverage"]["missing"]
    assert "holding" not in result["coverage"]["missing"]
    assert "holding" not in result["coverage"]["present"]
    assert result["receipts"]["sentiment"]["data_asof"] is None
    assert result["section_status"]["holding"] == "not_applicable"
