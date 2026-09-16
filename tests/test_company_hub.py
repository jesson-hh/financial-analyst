from __future__ import annotations

from copy import deepcopy

from guanlan_v2.research.company_hub import (
    SCHEMA_VERSION,
    STAGES,
    build_company_research_hub,
)


AS_OF = "2026-08-30T12:00:00Z"


def _complete_pack():
    sections = {
        "company_facts": {
            "as_of": "2026-06-30",
            "valuation": {"pe_ttm": 19.2, "report_period": "2026-06-30"},
            "events": [{"date": "2026-07-20", "title": "capacity update"}],
        },
        "financials": {
            "source": "financials_pit_loader",
            "as_of": "2026-06-30",
            "rows": [{"report_date": "2026-06-30", "revenue": 100, "unit": "CNY"}],
        },
        "announcements": {
            "source": "cninfo_announcements",
            "as_of": "2026-08-25",
            "items": [{"date": "2026-08-25", "title": "interim report"}],
        },
        "expectations": {
            "source": "ths_eps_forecast",
            "as_of": "2026-08-20",
            "content_period": {"first_year": "2026", "last_year": "2028"},
            "items": [{"year": "2026", "eps": 1.7, "unit": "CNY/share"}],
        },
        "valuation": {
            "as_of": "2026-08-29",
            "pe_ttm": 20.1,
            "unit": "multiple",
        },
        "chain": {"as_of": "2026-08-10", "seg": "AI server", "therm": 0.7},
        "macro": {"as_of": "2026-08-29", "risk": "neutral"},
    }
    return {
        "schema_version": "company-research-evidence@2",
        "code": "SH603986",
        "generated_at": AS_OF,
        "coverage": {
            "present": list(sections),
            "missing": [],
            "company_core": {"required": [
                "company_facts", "financials", "announcements", "expectations", "valuation",
            ], "missing": [], "status": "complete"},
        },
        "errors": {},
        "sections": sections,
    }


def _workspace_snapshot(dataset_id, *, rows, status="ok", ok=True, query=None,
                        subject="SH603986", asset_id="wa_1234567890abcdef",
                        as_of="2026-08-29", sources=None):
    snapshot = {
        "ok": ok,
        "dataset_id": dataset_id,
        "status": status,
        "query": dict(query or {}),
        "rows": rows,
        "sources": sources or [{
            "authority": "fixture authority",
            "status": "ok",
            "retrieved_at": AS_OF,
        }],
        "pulled_at": AS_OF,
        "as_of": as_of,
        "warnings": [],
    }
    return {
        "asset": {
            "id": asset_id,
            "dataset_id": dataset_id,
            "subject": {"kind": "stock", "code": subject, "name": "fixture"},
        },
        "snapshot": snapshot,
    }


def _source(hub, source_id):
    return next(item for item in hub["source_register"] if item["source_id"] == source_id)


def test_complete_pack_builds_six_stage_hub_and_keeps_context_out_of_issuer_scope():
    hub = build_company_research_hub(_complete_pack(), as_of=AS_OF)

    assert hub["schema_version"] == SCHEMA_VERSION
    assert hub["status"] == "complete"
    assert tuple(hub["evidence_ids"]) == STAGES
    assert all(hub["evidence_ids"][stage] for stage in STAGES)
    assert all(value == "complete" for value in hub["stage_status"].values())
    assert hub["subject"] == {
        "code": "SH603986", "observed_codes": ["SH603986"],
    }

    chain = _source(hub, "pack.chain")
    macro = _source(hub, "pack.macro")
    facts = _source(hub, "pack.company_facts")
    assert chain["type"] == macro["type"] == "context"
    assert facts["type"] == "issuer"
    assert chain["evidence_id"] in hub["evidence_scope"]["context"]
    assert chain["evidence_id"] not in hub["evidence_scope"]["issuer"]
    assert facts["evidence_id"] in hub["evidence_scope"]["issuer"]
    assert _source(hub, "ths_eps_forecast")["period"] == "2026..2028"
    assert _source(hub, "ths_eps_forecast")["unit"] == "CNY/share"


def test_missing_required_stage_is_incomplete_and_explicit_gap():
    pack = _complete_pack()
    pack["sections"]["expectations"] = None
    pack["coverage"]["missing"] = ["expectations"]
    pack["coverage"]["company_core"]["missing"] = ["expectations"]
    pack["coverage"]["company_core"]["status"] = "incomplete"

    hub = build_company_research_hub(pack, as_of=AS_OF)

    assert hub["status"] == "incomplete"
    assert hub["stage_status"]["estimates"] == "incomplete"
    assert "estimates" in hub["summary"]["missing_stages"]
    assert any(
        gap["kind"] == "required_stage_missing" and gap["stage"] == "estimates"
        for gap in hub["gaps"]
    )
    assert any(
        gap["source_id"] == "pack.expectations" and gap["severity"] == "required"
        for gap in hub["gaps"]
    )


def test_required_stage_covered_only_by_old_data_marks_whole_hub_stale():
    pack = _complete_pack()
    pack["sections"]["expectations"]["as_of"] = "2025-01-01"

    hub = build_company_research_hub(pack, as_of=AS_OF)

    assert hub["status"] == "stale"
    assert hub["stage_status"]["estimates"] == "stale"
    assert "estimates" in hub["summary"]["stale_stages"]
    assert _source(hub, "ths_eps_forecast")["stale"] is True
    assert any(gap["kind"] == "required_stage_stale" for gap in hub["gaps"])


def test_future_issuer_evidence_is_pit_unusable_and_cannot_close_a_stage():
    baseline = build_company_research_hub(_complete_pack(), as_of=AS_OF)
    pack = _complete_pack()
    pack["sections"]["expectations"]["as_of"] = "2026-08-31T00:00:00Z"
    # An upstream freshness assertion must never override the PIT cutoff.
    pack["sections"]["expectations"]["stale"] = False

    hub = build_company_research_hub(pack, as_of=AS_OF)

    source = _source(hub, "ths_eps_forecast")
    assert source["future"] is True
    assert source["stale"] is None
    assert source["status"] == "future"
    assert source["upstream_status"] == "ok"
    assert source["usable"] is False
    assert source["evidence_id"] is None
    assert hub["status"] == "incomplete"
    assert hub["stage_status"]["estimates"] == "incomplete"
    assert hub["evidence_ids"]["estimates"] == []
    assert len(hub["evidence_scope"]["issuer"]) == len(
        baseline["evidence_scope"]["issuer"]
    ) - 1
    assert len(hub["evidence_ids"]["report"]) == len(
        baseline["evidence_ids"]["report"]
    ) - 1
    future_gap = next(
        gap for gap in hub["gaps"]
        if gap["kind"] == "future_source" and gap["source_id"] == "ths_eps_forecast"
    )
    assert future_gap["severity"] == "required"
    assert "2026-08-31" in future_gap["detail"]
    assert AS_OF in future_gap["detail"]
    assert hub["summary"]["future_sources"] == ["ths_eps_forecast"]
    assert hub["summary"]["future_issuer_sources"] == ["ths_eps_forecast"]


def test_later_timestamp_on_same_day_is_allowed_by_daily_pit_contract():
    pack = _complete_pack()
    pack["sections"]["expectations"]["as_of"] = "2026-08-30T23:59:59.999999Z"
    pack["sections"]["expectations"]["stale"] = False

    hub = build_company_research_hub(pack, as_of=AS_OF)

    source = _source(hub, "ths_eps_forecast")
    assert source["future"] is False
    assert source["usable"] is True
    assert source["status"] == source["upstream_status"] == "ok"
    assert source["evidence_id"] in hub["evidence_ids"]["estimates"]
    assert source["evidence_id"] in hub["evidence_ids"]["report"]
    assert not any(gap["kind"] == "future_source" for gap in hub["gaps"])


def test_future_context_is_visible_but_cannot_enter_context_or_report_evidence():
    baseline = build_company_research_hub(_complete_pack(), as_of=AS_OF)
    pack = _complete_pack()
    pack["sections"]["macro"]["as_of"] = "2026-09-01"
    pack["sections"]["macro"]["stale"] = False

    hub = build_company_research_hub(pack, as_of=AS_OF)

    macro = _source(hub, "pack.macro")
    assert hub["status"] == "complete"
    assert macro["type"] == "context"
    assert macro["future"] is True
    assert macro["status"] == "future"
    assert macro["usable"] is False
    assert macro["evidence_id"] is None
    assert len(hub["evidence_scope"]["context"]) == len(
        baseline["evidence_scope"]["context"]
    ) - 1
    assert len(hub["evidence_ids"]["report"]) == len(
        baseline["evidence_ids"]["report"]
    ) - 1
    assert any(
        gap["kind"] == "future_source"
        and gap["source_id"] == "pack.macro"
        and gap["severity"] == "optional"
        for gap in hub["gaps"]
    )
    assert "pack.macro" in hub["summary"]["future_sources"]
    assert "pack.macro" not in hub["summary"]["future_issuer_sources"]


def test_only_future_evidence_fails_closed_with_no_evidence_ids():
    hub = build_company_research_hub(
        sections={
            "financials": {
                "source": "financials_pit_loader",
                "as_of": "2026-09-01",
                "stale": False,
                "rows": [{"report_date": "2026-09-01", "revenue": 100}],
            },
        },
        as_of=AS_OF,
    )

    source = _source(hub, "financials_pit_loader")
    assert hub["status"] == "failed"
    assert source["future"] is True
    assert source["usable"] is False
    assert source["evidence_id"] is None
    assert all(not ids for ids in hub["evidence_ids"].values())
    assert hub["evidence_scope"] == {"issuer": [], "context": []}


def test_future_workspace_issuer_snapshot_cannot_enter_evidence_scope():
    baseline = build_company_research_hub(_complete_pack(), as_of=AS_OF)
    future_sec = _workspace_snapshot(
        "research.sec_filings_xbrl.live",
        rows=[{"filed": "2026-09-01", "concept": "Revenues", "unit": "USD"}],
        query={"ticker": "603986"},
        subject="SH603986",
        as_of="2026-09-01",
        sources=[{"authority": "U.S. SEC EDGAR", "status": "ok"}],
    )
    future_sec["snapshot"]["stale"] = False

    hub = build_company_research_hub(_complete_pack(), [future_sec], as_of=AS_OF)

    source = _source(hub, "research.sec_filings_xbrl.live")
    assert source["type"] == "issuer"
    assert source["future"] is True
    assert source["usable"] is False
    assert source["evidence_id"] is None
    assert hub["evidence_scope"] == baseline["evidence_scope"]
    assert hub["evidence_ids"] == baseline["evidence_ids"]
    assert "research.sec_filings_xbrl.live" in hub["summary"]["future_issuer_sources"]


def test_no_usable_evidence_is_failed_instead_of_fabricating_coverage():
    hub = build_company_research_hub(
        {
            "schema_version": "company-research-evidence@2",
            "errors": {"company_facts": "provider down"},
            "sections": {"company_facts": None},
        },
        [{
            "dataset_id": "research.us_treasury.live",
            "ok": False,
            "status": "unavailable",
            "rows": [],
            "warnings": ["network down"],
        }],
        as_of=AS_OF,
    )

    assert hub["status"] == "failed"
    assert all(not values for values in hub["evidence_ids"].values())
    assert any(
        gap["kind"] == "source_failed" and "provider down" in gap["detail"]
        for gap in hub["gaps"]
    )
    treasury = _source(hub, "research.us_treasury.live")
    assert treasury["type"] == "context"
    assert treasury["evidence_id"] is None


def test_workspace_peripheral_sources_are_context_and_failures_are_gaps():
    finra = _workspace_snapshot(
        "research.finra_short_volume.live",
        rows=[{"date": "2026-08-29", "symbol": "AAPL", "short_volume": 10}],
        query={"symbols": ["AAPL"]},
        sources=[{"authority": "FINRA", "status": "ok", "retrieved_at": AS_OF}],
    )
    treasury = _workspace_snapshot(
        "research.us_treasury.live",
        rows=[{"date": "2026-08-29", "ten_year": 4.1, "unit": "percent"}],
        sources=[{
            "authority": "U.S. Department of the Treasury", "status": "ok",
            "retrieved_at": AS_OF,
        }],
        asset_id="wa_2234567890abcdef",
    )
    unavailable = _workspace_snapshot(
        "research.cftc_cot.live", rows=[], status="unavailable", ok=False,
        sources=[{"authority": "CFTC", "status": "error", "retrieved_at": AS_OF}],
        asset_id="wa_3234567890abcdef",
    )

    hub = build_company_research_hub(
        _complete_pack(), [finra, treasury, unavailable], as_of=AS_OF,
    )

    assert hub["status"] == "complete"  # optional context outage cannot erase complete issuer evidence
    assert _source(hub, "research.finra_short_volume.live")["type"] == "context"
    assert _source(hub, "research.us_treasury.live")["type"] == "context"
    assert _source(hub, "research.cftc_cot.live")["type"] == "context"
    assert _source(hub, "research.cftc_cot.live")["evidence_id"] is None
    assert any(
        gap["source_id"] == "research.cftc_cot.live"
        and gap["kind"] == "source_failed" and gap["severity"] == "optional"
        for gap in hub["gaps"]
    )


def test_newsradar_review_projection_does_not_misreport_fetched_candidates_as_empty():
    newsradar = _workspace_snapshot(
        "research.newsradar.live",
        rows=[],
        sources=[{"authority": "DIGITIMES", "status": "ok"}],
    )
    newsradar["snapshot"]["industry_review_gate"] = {
        "status": "awaiting_review",
        "candidate_counts": {
            "total": 82,
            "related": 0,
            "unrelated": 0,
            "uncertain": 0,
            "unreviewed": 82,
        },
        "report_eligible_candidate_ids": [],
        "only_related_report_eligible": True,
        "issuer_fact": False,
    }

    hub = build_company_research_hub(_complete_pack(), [newsradar], as_of=AS_OF)

    gap = next(
        item for item in hub["gaps"]
        if item["source_id"] == "research.newsradar.live"
    )
    assert gap["kind"] == "industry_news_review_pending"
    assert "82 industry candidates were fetched" in gap["detail"]
    assert "0 are user-confirmed related/report-eligible" in gap["detail"]
    assert not any(
        item["kind"] == "source_empty"
        and item["source_id"] == "research.newsradar.live"
        for item in hub["gaps"]
    )


def test_sec_is_issuer_only_when_query_matches_bound_workspace_subject():
    matching = _workspace_snapshot(
        "research.sec_filings_xbrl.live",
        rows=[{"filed": "2026-08-20", "concept": "Revenues", "unit": "USD"}],
        query={"ticker": "AAPL"}, subject="AAPL",
        sources=[{"authority": "U.S. SEC EDGAR", "status": "ok"}],
    )
    peer = deepcopy(matching)
    peer["asset"]["id"] = "wa_2234567890abcdef"
    peer["asset"]["subject"]["code"] = "SH603986"

    issuer_hub = build_company_research_hub(
        sections={
            "expectations": _complete_pack()["sections"]["expectations"],
            "valuation": _complete_pack()["sections"]["valuation"],
            "announcements": _complete_pack()["sections"]["announcements"],
        },
        workspace_snapshots=[matching], as_of=AS_OF,
    )
    peer_hub = build_company_research_hub(
        _complete_pack(), [peer], as_of=AS_OF,
    )

    assert _source(issuer_hub, "research.sec_filings_xbrl.live")["type"] == "issuer"
    assert issuer_hub["status"] == "complete"
    assert _source(peer_hub, "research.sec_filings_xbrl.live")["type"] == "context"


def test_same_source_same_observation_with_different_bodies_is_conflict():
    first = _workspace_snapshot(
        "research.us_treasury.live",
        rows=[{"date": "2026-08-29", "ten_year": 4.1}],
        asset_id="wa_1234567890abcdef",
    )
    second = deepcopy(first)
    second["asset"]["id"] = "wa_2234567890abcdef"
    second["snapshot"]["rows"][0]["ten_year"] = 4.2

    hub = build_company_research_hub(
        _complete_pack(), [first, second], as_of=AS_OF,
    )

    versions = [
        item for item in hub["source_register"]
        if item["source_id"].startswith("research.us_treasury.live#")
    ]
    assert len(versions) == 2
    assert len({item["source_id"] for item in versions}) == 2
    assert any(item["kind"] == "conflicting_snapshots" for item in hub["conflicts"])
    assert hub["status"] == "complete"  # a context-only conflict is visible but non-blocking


def test_output_is_deterministic_when_workspace_input_order_changes():
    first = _workspace_snapshot(
        "research.us_treasury.live",
        rows=[{"date": "2026-08-29", "ten_year": 4.1}],
        asset_id="wa_1234567890abcdef",
    )
    second = _workspace_snapshot(
        "research.finra_short_volume.live",
        rows=[{"date": "2026-08-29", "symbol": "AAPL", "short_volume": 10}],
        asset_id="wa_2234567890abcdef",
    )

    forward = build_company_research_hub(
        _complete_pack(), [first, second], as_of=AS_OF,
    )
    reverse = build_company_research_hub(
        _complete_pack(), [second, first], as_of=AS_OF,
    )

    assert forward == reverse


def test_unknown_or_malformed_sources_are_retained_as_context_with_honest_gaps():
    hub = build_company_research_hub(
        _complete_pack(),
        [
            _workspace_snapshot(
                "research.future_signal.live",
                rows=[{"date": "2026-08-29", "value": 1}],
                asset_id="wa_1234567890abcdef",
            ),
            "not-a-snapshot",
        ],
        as_of=AS_OF,
    )

    unknown = _source(hub, "research.future_signal.live")
    assert unknown["type"] == "context"
    assert unknown["reliability"] == "unknown"
    assert any(
        gap["kind"] == "unclassified_source"
        and gap["source_id"] == "research.future_signal.live"
        for gap in hub["gaps"]
    )
    assert any(gap["kind"] == "invalid_snapshot" for gap in hub["gaps"])


def test_all_fifteen_legacy_sections_plus_financials_and_nine_workspace_datasets_are_accepted():
    pack = _complete_pack()
    for name in (
        "quote_live", "company_facts", "announcements", "expectations",
        "valuation", "research_views", "fundflow", "board_eco", "sentiment",
        "kuaixun", "chain", "quant", "mainline", "macro", "holding",
    ):
        pack["sections"].setdefault(name, {"as_of": "2026-08-29", "value": name})
    datasets = (
        "research.trade_disclosure_calendar.live",
        "research.tw_monthly_revenue.live",
        "research.cn_commodity_futures.live",
        "research.control_lists.live",
        "research.sec_filings_xbrl.live",
        "research.finra_short_volume.live",
        "research.us_treasury.live",
        "research.cftc_cot.live",
        "research.overseas_earnings_calendar.live",
    )
    records = []
    for index, dataset_id in enumerate(datasets, 1):
        records.append(_workspace_snapshot(
            dataset_id,
            rows=[{"date": "2026-08-29", "value": index}],
            query={"ticker": "AAPL"} if dataset_id == "research.sec_filings_xbrl.live" else {},
            subject="AAPL" if dataset_id == "research.sec_filings_xbrl.live" else "SH603986",
            asset_id=f"wa_{index:016x}",
        ))

    hub = build_company_research_hub(pack, records, as_of=AS_OF)

    source_ids = {item["source_id"] for item in hub["source_register"]}
    assert set(datasets) <= source_ids
    assert len(hub["source_register"]) == 25
    assert _source(hub, "research.sec_filings_xbrl.live")["type"] == "issuer"
    for dataset_id in set(datasets) - {"research.sec_filings_xbrl.live"}:
        assert _source(hub, dataset_id)["type"] == "context"


def test_one_raw_workspace_snapshot_mapping_is_not_mistaken_for_an_iterable_of_keys():
    raw = _workspace_snapshot(
        "research.us_treasury.live",
        rows=[{"date": "2026-08-29", "ten_year": 4.1}],
    )["snapshot"]

    hub = build_company_research_hub(
        _complete_pack(), raw, as_of=AS_OF,
    )

    assert _source(hub, "research.us_treasury.live")["record_count"] == 1
