from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import pytest

from guanlan_v2.console import tools as console_tools
from guanlan_v2.console import workspace_tools
from guanlan_v2.console.media import MediaStore
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.glmcp.tooltable import build_mcp_tools
from guanlan_v2.research.company_workspace import (
    build_workspace_company_artifacts,
    normalise_company_events,
    render_readiness_report,
)
from guanlan_v2.research.company_report_gate import validate_company_research_report


# Keep fixture reads on the current-mode compatibility path.  A fixed date
# silently became historical after its authoring day and made unsigned current
# fixtures fail the strict PIT gate for the right reason.
ASOF = date.today().isoformat()


def test_readiness_report_does_not_label_unusable_fresh_source_available():
    report = render_readiness_report(
        subject={"code": "SH600667", "name": "太极实业"},
        hub={
            "status": "complete", "summary": {}, "gaps": [],
            "source_register": [{
                "source_id": "research.control_lists.live",
                "type": "context", "as_of": "2026-09-05",
                "stale": False, "usable": False, "status": "unavailable",
                "reliability": "official",
            }],
        },
        pipeline={"status": "incomplete", "stages": []},
        source_plan=[], as_of="2026-09-05",
    )

    assert "| research.control_lists.live | context | 2026-09-05 | 不可用 | official |" in report
    assert "| research.control_lists.live | context | 2026-09-05 | 可用 | official |" not in report


@pytest.fixture(autouse=True)
def _forbid_production_events_connection(monkeypatch):
    """Fail before opening production data; unit tests never scan the live DB.

    Comparing fixture markers by scanning the entire production database was
    both late (a write had already occurred) and unbounded.  Guard this module's
    SQLite connection boundary instead, including read-only/immutable URIs.
    All temporary databases and in-memory connections keep their real behavior.
    """
    production = (
        Path(__file__).resolve().parents[1] / "var" / "events" / "events.db"
    ).resolve()
    real_connect = sqlite3.connect

    def guarded_connect(*args, **kwargs):
        database = args[0] if args else kwargs.get("database")
        try:
            raw = os.fsdecode(os.fspath(database))
        except TypeError:
            raw = ""
        if raw.startswith("file:"):
            parsed = urlsplit(raw)
            raw = url2pathname(parsed.path)
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                raw = f"//{parsed.netloc}/{raw.lstrip('/')}"
        if raw and raw != ":memory:":
            if Path(raw).resolve() == production:
                pytest.fail("unit test attempted to connect to production events.db")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", guarded_connect)


def test_production_events_connection_guard_blocks_path_and_uri_but_allows_temp(tmp_path):
    production = (
        Path(__file__).resolve().parents[1] / "var" / "events" / "events.db"
    ).resolve()
    uri = production.as_uri()
    for database, use_uri in (
        (production, False),
        (str(production), False),
        (os.fsencode(production), False),
        (os.path.relpath(production, Path.cwd()), False),
        (uri + "?mode=ro&immutable=1", True),
        (uri.replace("file:///", "file://localhost/") + "?mode=ro", True),
        (uri.replace("events.db", "%65vents.db") + "?mode=rw", True),
        ("file:" + production.as_posix() + "?mode=ro", True),
    ):
        with pytest.raises(pytest.fail.Exception, match="production events.db"):
            sqlite3.connect(database=database, uri=use_uri)
    with pytest.raises(pytest.fail.Exception, match="production events.db"):
        sqlite3.dbapi2.connect(production)
    for database in (tmp_path / "events.db", ":memory:"):
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.close()


def _pack(code: str = "SH603986"):
    quarters = {
        2024: [(100, 10, 8), (120, 12, 9), (130, 13, 10), (150, 15, 12)],
        2025: [(140, 14, 11), (150, 15, 12), (160, 16, 13), (170, 17, 14)],
        2026: [(180, 18, 15), (190, 19, 16)],
    }
    financial_rows = []
    for year, values in quarters.items():
        cumulative = [0.0, 0.0, 0.0]
        for suffix, trio in zip(("03-31", "06-30", "09-30", "12-31"), values):
            cumulative = [left + right for left, right in zip(cumulative, trio)]
            period = f"{year}-{suffix}"
            financial_rows.append({
                "period": period, "report_date": period,
                "revenue_cum": cumulative[0],
                "net_profit_parent_cum": cumulative[1],
                "net_profit_deducted_cum": cumulative[2],
                "shares_outstanding": 100,
                "total_debt": 10, "cash_and_equivalents": 20, "book_value": 300,
                "balance_source_ref": f"balance:{period}",
                "source_ref": f"financials:{period}", "unit": "CNY",
            })
    sections = {
        "company_profile": {
            "source": "exchange_current_listing", "as_of": ASOF,
            "code": code, "name": "兆易创新", "market": "SSE",
            "listing_status": "listed", "listing_date": "2016-08-18",
        },
        "quote_live": {
            "as_of": ASOF, "price": 101.2,
            "unit": "CNY/share", "currency": "CNY",
        },
        "company_facts": {
            "as_of": "2026-06-30", "valuation": {"pe_ttm": 20.1},
            "events": [{"date": "2026-08-20", "title": "capacity update"}],
        },
        "financials": {
            "source": "financials_pit_loader", "as_of": "2026-06-30",
            "basis": "cumulative_ytd", "unit": "CNY", "rows": financial_rows,
        },
        "announcements": {
            "source": "cninfo_announcements", "as_of": "2026-08-28",
            "items": [{
                "date": "2026-08-28",
                "title": "interim report: customer acceptance postponed",
            }],
        },
        "expectations": {
            "source": "ths_eps_forecast", "as_of": "2026-08-25",
            "unit": "CNY/share", "currency": "CNY",
            "items": [
                {"year": "2026", "n_org": 21, "eps_min": 2.0,
                 "eps_mean": 2.1, "eps_max": 2.2, "unit": "CNY/share"},
                {"year": "2027", "n_org": 20, "eps_min": 2.2,
                 "eps_mean": 2.4, "eps_max": 2.6, "unit": "CNY/share"},
                {"year": "2028", "n_org": 18, "eps_min": 2.5,
                 "eps_mean": 2.8, "eps_max": 3.1, "unit": "CNY/share"},
            ],
        },
        "valuation": {
            "as_of": ASOF, "pe_ttm": 20.1,
            "history_series": {
                "metric": "ttm_pe",
                "points": [
                    {"value": value, "unit": "x", "period": f"2024-01-{index + 1:02d}",
                     "as_of": f"2024-01-{index + 1:02d}", "ref": f"pe-history:{index}"}
                    for index, value in enumerate(range(10, 40))
                ],
            },
        },
        "chain": {"as_of": "2026-08-20", "segment": "AI server"},
    }
    return {
        "schema_version": "company-research-evidence@2",
        "code": code,
        "generated_at": ASOF,
        "coverage": {
            "present": list(sections), "missing": [],
            "company_core": {
                "required": [
                    "company_facts", "financials", "announcements",
                    "expectations", "valuation",
                ],
                "missing": [], "status": "complete",
            },
        },
        "errors": {},
        "sections": sections,
    }


def _calendar(asset_id: str = "wa_1234567890abcdef"):
    snapshot = {
        "ok": True,
        "dataset_id": "research.trade_disclosure_calendar.live",
        "status": "ok",
        "query": {"stock_code": "SH603986"},
        "rows": [{"record_type": "trading_day", "date": "2026-08-31"}],
        "sources": [{"authority": "SZSE", "status": "ok"}],
        "pulled_at": "2026-08-30T01:02:03Z",
        # The row is a known future trading day; the evidence snapshot itself
        # was available on the run date and must carry that PIT anchor.
        "as_of": ASOF,
        "warnings": [],
    }
    return {
        "asset": {
            "id": asset_id,
            "dataset_id": snapshot["dataset_id"],
            "subject": {"kind": "stock", "code": "SH603986", "name": "fixture"},
        },
        "snapshot": snapshot,
    }


def test_company_disclosure_market_routes_bse_explicitly():
    assert workspace_tools._company_disclosure_market("BJ920059") == "bse"
    assert workspace_tools._company_disclosure_market("SH688981") == "shkcp"
    assert workspace_tools._company_disclosure_market("SZ300750") == "szcn"


def _world(tmp_path: Path):
    store = ConsoleStore(root=tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock", "code": "SH603986", "name": "兆易创新",
        "display_name": "兆易创新", "root_mode": "managed", "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    return store, workspace, sid


def _bound(store, sid):
    return workspace_tools.CTX_WORKSPACE.set(
        workspace_tools.bind_workspace_context(store, sid)
    )


def _prepare_saved_company(tmp_path: Path, monkeypatch):
    store, workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    snapshot = _calendar()["snapshot"]
    workspace_tools.save_workspace_data_snapshot(
        store, workspace["id"], workspace,
        dataset_id=snapshot["dataset_id"], title="calendar",
        query=snapshot["query"], snapshot=snapshot,
        source_tool="ww_workspace_trade_disclosure_calendar",
    )
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack",
        lambda code, asof="": (_pack(code), {"ok": True, "path": None}),
    )
    prepared = workspace_tools.workspace_company_research_prepare_impl(
        asof=ASOF,
        refresh_calendar=False,
        include_newsradar=False,
        include_latest_snapshots=True,
    )
    assert prepared["ok"] is True
    return store, workspace, sid, token, prepared


def test_company_prepare_survives_on_demand_announcement_events_failure(
        tmp_path, monkeypatch):
    from guanlan_v2.events import ingest as event_ingest
    from guanlan_v2.events import store as event_store

    console_store, _workspace, sid = _world(tmp_path / "console")
    token = _bound(console_store, sid)
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")
    isolated_events_db = event_store.db_path()
    assert isolated_events_db == tmp_path / "_isolated_events" / "events.db"
    assert isolated_events_db.is_relative_to(tmp_path)
    monkeypatch.setattr(
        event_ingest, "ingest_live_announcements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack",
        lambda code, asof="": (_pack(code), {"ok": True, "path": None}),
    )
    try:
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=ASOF, refresh_calendar=False, include_newsradar=False,
            include_latest_snapshots=False,
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    global_row = next(
        row for row in result["source_plan"]
        if row.get("dataset_id") == "events.global.announcements"
    )
    assert global_row["status"] == "failed"
    assert "provider down" in global_row["note"]
    assert isolated_events_db.is_file()


def test_codex_scoped_company_prepare_never_connects_global_events(
        tmp_path, monkeypatch):
    from guanlan_v2.events import store as event_store

    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")
    monkeypatch.setattr(
        event_store, "connect",
        lambda *args, **kwargs: pytest.fail("scoped prepare must not open events.db"),
    )
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack",
        lambda code, asof="": (_pack(code), {"ok": True, "path": None}),
    )
    store, _workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=date.today().isoformat(), refresh_calendar=False,
            include_newsradar=False, include_latest_snapshots=False,
            _global_event_write=False,
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    global_row = next(
        row for row in result["source_plan"]
        if row.get("dataset_id") == "events.global.announcements"
    )
    assert global_row["mode"] == "codex_scoped_private_only"
    assert "未连接或写入" in global_row["note"]
    assert "未连接或写入全局 events.db" in result["content"]
    assert "按当前公司写公告" not in result["content"]


def _approved_mapping_registry(path: Path):
    from guanlan_v2.research.company_mapping import CompanyMappingRegistry

    registry = CompanyMappingRegistry(path)
    registry.upsert_entity(
        entity_id="issuer-sh603986", entity_type="listed_issuer",
        canonical_name="兆易创新", jurisdiction="CN",
        identifiers={"security_code": "SH603986"},
        at="2026-08-30T01:00:00Z",
    )
    targets = [
        ("tw-6488", "listed_issuer", "环球晶圆", "TW",
         "tw_monthly_revenue", "6488", "industry_peer"),
        ("commodity-cu", "commodity_contract", "沪铜连续", "CN",
         "cn_commodity_futures", "CU0", "input_cost"),
        ("legal-giga", "legal_entity", "GigaDevice Semiconductor Inc.", "CN",
         "control_lists", "GigaDevice Semiconductor Inc.", "legal_name"),
    ]
    for entity_id, entity_type, name, jurisdiction, dataset, key, relationship in targets:
        registry.upsert_entity(
            entity_id=entity_id, entity_type=entity_type,
            canonical_name=name, jurisdiction=jurisdiction,
            at="2026-08-30T01:00:00Z",
        )
        proposal = registry.propose_mapping(
            source_entity_id="issuer-sh603986", target_entity_id=entity_id,
            target_dataset=dataset, target_key=key, relationship=relationship,
            evidence=[{"source_ref": "fixture:reviewed-disclosure"}],
            context={"scope": "context"}, at="2026-08-30T02:00:00Z",
        )
        registry.decide_mapping(
            proposal["edge_id"], decision="approve", decided_by="fixture-reviewer",
            at="2026-08-30T03:00:00Z",
        )
    return registry


def test_company_prepare_uses_only_approved_mappings_and_freezes_receipt(
        tmp_path, monkeypatch):
    registry = _approved_mapping_registry(tmp_path / "mapping" / "registry.db")
    monkeypatch.setenv("GUANLAN_COMPANY_MAPPING_DB", str(registry.path))
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack",
        lambda code, asof="": (_pack(code), {"ok": True, "path": None}),
    )
    observed = {}

    def live_result(dataset_id, asset_id, query):
        observed[dataset_id] = dict(query)
        snapshot = {
            "ok": True, "dataset_id": dataset_id, "status": "ok",
            "query": dict(query), "rows": [{"value": 1}],
            "sources": [{"authority": "fixture", "status": "ok"}],
            "pulled_at": "2026-08-30T04:00:00Z", "as_of": ASOF,
            "warnings": [],
        }
        return {
            "ok": True,
            "asset": {
                "id": asset_id, "dataset_id": dataset_id,
                "subject": {"kind": "stock", "code": "SH603986"},
            },
            "snapshot": snapshot,
        }

    monkeypatch.setattr(
        workspace_tools, "workspace_tw_monthly_revenue_impl",
        lambda company_ids=None, limit=50: live_result(
            "research.tw_monthly_revenue.live", "wa_aaaaaaaaaaaaaaaa",
            {"company_ids": list(company_ids or []), "limit": limit},
        ),
    )
    monkeypatch.setattr(
        workspace_tools, "workspace_cn_commodity_futures_impl",
        lambda symbols=None, limit=30: live_result(
            "research.cn_commodity_futures.live", "wa_bbbbbbbbbbbbbbbb",
            {"symbols": list(symbols or []), "limit": limit},
        ),
    )
    monkeypatch.setattr(
        workspace_tools, "workspace_control_lists_impl",
        lambda mode="search", query="", limit=50: live_result(
            "research.control_lists.live", "wa_cccccccccccccccc",
            {"mode": mode, "query": query, "limit": limit},
        ),
    )

    store, workspace, sid = _world(tmp_path / "console")
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=ASOF, refresh_calendar=False, include_newsradar=False,
            include_latest_snapshots=False,
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    assert observed["research.tw_monthly_revenue.live"]["company_ids"] == ["6488"]
    assert observed["research.cn_commodity_futures.live"]["symbols"] == ["CU0"]
    assert observed["research.control_lists.live"]["query"] == \
        "GigaDevice Semiconductor Inc."
    receipt = result["mapping_receipt"]
    assert receipt["status"] == "resolved"
    assert receipt["registry_status"] == "ok"
    assert all(
        row["mode"] == "approved_registry" and row["scope"] == "context"
        for row in receipt["targets"].values()
    )
    assert {
        asset_id
        for row in receipt["targets"].values()
        for asset_id in row["asset_ids"]
    } == {
        "wa_aaaaaaaaaaaaaaaa", "wa_bbbbbbbbbbbbbbbb", "wa_cccccccccccccccc",
    }
    run_root = Path(workspace["root_path"], result["asset"]["run_rel"])
    stored_receipt = json.loads(
        (run_root / "mapping_receipt.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
    evidence = json.loads((run_root / "evidence.json").read_text(encoding="utf-8"))
    engine_pack = json.loads(
        (run_root / "engine_evidence_pack.json").read_text(encoding="utf-8")
    )
    assert stored_receipt == receipt
    assert "mapping_receipt.json" in manifest["files"]
    assert manifest["source_mapping"]["receipt_id"] == receipt["receipt_id"]
    assert evidence["source_mapping_receipt"]["receipt_id"] == receipt["receipt_id"]
    assert engine_pack["sections"]["company_source_mapping"]["receipt_id"] \
        == receipt["receipt_id"]
    assert len(result["asset"]["attachments"]["documents"]) == 6


def test_company_mapping_explicit_values_replace_or_disable_registry(
        tmp_path, monkeypatch):
    registry = _approved_mapping_registry(tmp_path / "mapping" / "registry.db")
    monkeypatch.setenv("GUANLAN_COMPANY_MAPPING_DB", str(registry.path))

    receipt = workspace_tools._resolve_company_source_mapping(
        "SH603986", ASOF,
        taiwan_company_ids=["2330"], commodity_symbols=[], control_entity="",
    )

    tw = receipt["targets"]["research.tw_monthly_revenue.live"]
    commodity = receipt["targets"]["research.cn_commodity_futures.live"]
    control = receipt["targets"]["research.control_lists.live"]
    assert tw["mode"] == "explicit_override" and tw["values"] == ["2330"]
    assert tw["edge_ids"] == []
    assert commodity["mode"] == "explicit_disabled" and commodity["values"] == []
    assert control["mode"] == "explicit_disabled" and control["values"] == []
    assert all(
        row["context"]["persisted"] is False for row in tw["selected"]
    )


def test_company_mapping_reviewed_table_receipt_and_explicit_is_request_only(
        tmp_path, monkeypatch):
    missing_db = tmp_path / "registry" / "missing.db"
    monkeypatch.setenv("GUANLAN_COMPANY_MAPPING_DB", str(missing_db))
    today = date.today().isoformat()

    automatic = workspace_tools._resolve_company_source_mapping(
        "SZ300308", today,
        taiwan_company_ids=None, commodity_symbols=None, control_entity=None,
    )
    assert automatic["status"] == "resolved"
    assert automatic["registry_db_status"] == "not_configured"
    assert automatic["reviewed_table_status"] == "ok"
    assert automatic["reviewed_table"]["table_version"] == "2026-08-30.1"
    assert automatic["guardrails"]["exact_identity_only"] is True
    assert automatic["guardrails"]["llm_guessing_disabled"] is True
    assert automatic["targets"]["research.tw_monthly_revenue.live"]["mode"] \
        == "reviewed_table"
    assert automatic["targets"]["research.cn_commodity_futures.live"]["values"] \
        == ["AL0", "CU0", "NI0", "SI0", "SN0"]
    assert automatic["targets"]["research.control_lists.live"]["values"] \
        == ["Zhongji Innolight Co., Ltd."]
    assert not missing_db.exists()

    explicit = workspace_tools._resolve_company_source_mapping(
        "SZ300308", today,
        taiwan_company_ids=["2330"], commodity_symbols=[], control_entity="",
    )
    assert explicit["targets"]["research.tw_monthly_revenue.live"]["mode"] \
        == "explicit_override"
    assert explicit["targets"]["research.tw_monthly_revenue.live"]["values"] \
        == ["2330"]
    assert explicit["targets"]["research.cn_commodity_futures.live"]["mode"] \
        == "explicit_disabled"

    # The following automatic resolution returns the reviewed values again;
    # the one-off override was only recorded in its own receipt.
    repeated = workspace_tools._resolve_company_source_mapping(
        "SZ300308", today,
        taiwan_company_ids=None, commodity_symbols=None, control_entity=None,
    )
    assert repeated["targets"]["research.tw_monthly_revenue.live"]["values"] \
        == ["2368", "2383", "3081", "6274"]
    assert repeated["receipt_id"] == automatic["receipt_id"]
    assert not missing_db.exists()


def test_latest_conditional_snapshot_must_match_current_mapping(tmp_path, monkeypatch):
    registry = _approved_mapping_registry(tmp_path / "mapping" / "registry.db")
    monkeypatch.setenv("GUANLAN_COMPANY_MAPPING_DB", str(registry.path))
    receipt = workspace_tools._resolve_company_source_mapping(
        "SH603986", ASOF,
        taiwan_company_ids=None, commodity_symbols=None, control_entity=None,
    )
    matching = {
        "asset": {"acquired_at": "2026-08-30T04:00:00Z"},
        "snapshot": {
            "query": {"symbols": ["CU0"]},
            "pulled_at": "2026-08-30T04:00:00Z",
        },
    }
    stale_relation = {
        "asset": {"acquired_at": "2026-08-30T04:00:00Z"},
        "snapshot": {
            "query": {"symbols": ["RB0"]},
            "pulled_at": "2026-08-30T04:00:00Z",
        },
    }
    assert workspace_tools._snapshot_matches_mapping(
        matching, "research.cn_commodity_futures.live", receipt, ASOF,
    ) is True
    assert workspace_tools._snapshot_matches_mapping(
        stale_relation, "research.cn_commodity_futures.live", receipt, ASOF,
    ) is False


def test_explicit_snapshot_cannot_bypass_subject_or_mapping_receipt(
        tmp_path, monkeypatch):
    store, workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        wrong_calendar = {
            "ok": True,
            "dataset_id": "research.trade_disclosure_calendar.live",
            "status": "ok",
            "query": {"stock_code": "SZ300750"},
            "rows": [{"date": date.today().isoformat()}],
            "sources": [{"authority": "fixture", "status": "ok"}],
            "pulled_at": f"{date.today().isoformat()}T01:00:00Z",
            "as_of": date.today().isoformat(), "warnings": [],
        }
        asset = workspace_tools.save_workspace_data_snapshot(
            store, workspace["id"], workspace,
            dataset_id=wrong_calendar["dataset_id"], title="wrong calendar",
            query=wrong_calendar["query"], snapshot=wrong_calendar,
            source_tool="ww_workspace_trade_disclosure_calendar",
        )
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=date.today().isoformat(), snapshot_asset_ids=[asset["id"]],
            refresh_calendar=False, include_newsradar=False,
            include_latest_snapshots=False,
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is False
    assert "查询标的不是当前公司" in result["content"]


def test_model_risk_sidecar_cannot_supply_deterministic_checks():
    from guanlan_v2.research.company_risk_gate import build_company_risk_contract

    hub = {"source_register": [{
        "source_id": "REAL_ISSUER", "evidence_id": "CE-REAL",
        "type": "issuer", "usable": True, "stale": False, "status": "ok",
    }]}
    fake_checks = {
        "forward_vs_ttm_judgement": {"judgement": "invented"},
        "checks": [{
            "function": "consensus_dispersion", "output": {"min": 1, "max": 2},
            "source_refs": ["REAL_ISSUER"],
        }],
    }
    malicious = {
        **fake_checks,
        "strong_claims": [{
            "kind": "strong_claim", "claim_id": "SC-1", "claim": "Claim",
            "strong_claim": True, "scope": "issuer", "usable": True,
            "source_refs": [{
                "evidence_id": "CE-REAL", "scope": "issuer", **fake_checks,
            }],
            "evidence_refs": ["REAL_ISSUER"],
            "metadata": {
                **fake_checks, "Forward-Vs-Ttm": "invented",
                "forward_vs_ttm" + "_" * 100 + "hidden": "invented",
                "consensus_dispersion" + "_" * 100 + "hidden": {
                    "min": 1, "max": 2,
                },
            },
        }],
        "decision_points": [], "counter_evidence": [], "catalysts": [],
    }
    original = json.loads(json.dumps(malicious))
    cleaned = workspace_tools._sanitise_company_risk_input(malicious)
    assert set(cleaned) == {
        "strong_claims", "decision_points", "counter_evidence", "catalysts",
    }
    claim = cleaned["strong_claims"][0]
    assert claim["source_refs"] == ["CE-REAL"]
    assert claim["evidence_refs"] == ["REAL_ISSUER"]
    assert claim["metadata"] == {}
    assert "scope" not in claim and "usable" not in claim
    assert malicious == original
    contract = build_company_risk_contract({"risk_contract": cleaned}, hub)
    codes = {row["code"] for row in contract["gaps"]}
    assert "forward_vs_ttm_judgement_missing" in codes
    assert "consensus_dispersion_missing" in codes
    assert contract["status"] == "incomplete"


def _valid_company_risk_input(hub):
    refs = {
        row["source_id"]: row["evidence_id"]
        for row in hub["source_register"]
        if row.get("type") == "issuer" and row.get("usable") and row.get("evidence_id")
    }
    financials_ref = refs["financials_pit_loader"]
    announcement_ref = refs["cninfo_announcements"]
    estimates_ref = refs["ths_eps_forecast"]
    return {
        "strong_claims": [{
            "kind": "strong_claim",
            "claim_id": "claim-recovery-no-delay",
            "claim": "The observed revenue recovery can continue without a delivery delay.",
            "strong_claim": True,
            "source_refs": [financials_ref],
        }],
        "counter_evidence": [{
            "kind": "counter_evidence",
            "claim_id": "claim-recovery-no-delay",
            "relative_direction": "disconfirming",
            "counter_evidence": (
                "The 2026-08-28 interim-report announcement records postponed customer acceptance."
            ),
            "source_refs": [announcement_ref],
            "impact": "Downgrade the unconditional recovery claim to a delivery scenario.",
        }],
        "decision_points": [
            {
                "kind": "decision_point", "point_id": "delivery-close",
                "question": "Will postponed customer acceptance close this quarter?",
                "metric": "issuer-disclosed acceptance progress", "event": None,
                "threshold": "all postponed acceptance completed",
                "resolution_condition": None, "deadline": "2026-09-30",
                "status": "monitoring", "source_refs": [announcement_ref],
                "impact": "Determines whether the recovery case remains base or moves to bear.",
            },
            {
                "kind": "decision_point", "point_id": "eps-dispersion",
                "question": "Does the next consensus update retain the earnings recovery?",
                "metric": "2027 consensus EPS mean", "event": None,
                "threshold": "at least CNY 2.4 per share",
                "resolution_condition": None, "deadline": "2026-10-31",
                "status": "pending", "source_refs": [estimates_ref],
                "impact": "A miss weakens the forward valuation scenario.",
            },
            {
                "kind": "decision_point", "point_id": "revenue-confirmation",
                "question": "Do subsequent financials confirm the revenue recovery?",
                "metric": "reported quarterly revenue", "event": None,
                "threshold": "at least CNY 190 in fixture units",
                "resolution_condition": None, "deadline": "2026-12-31",
                "status": "scheduled", "source_refs": [financials_ref],
                "impact": "Failure invalidates the continuation claim.",
            },
        ],
        "catalysts": [],
    }


def test_pure_adapter_runs_connected_core_gates_and_keeps_structured_risk_open():
    result = build_workspace_company_artifacts(
        _pack(), [_calendar()],
        subject={"kind": "stock", "code": "SH603986", "name": "兆易创新"},
        source_plan=[{
            "dataset_id": "research.trade_disclosure_calendar.live",
            "status": "available", "mode": "fixture", "asset_id": "wa_1234567890abcdef",
        }],
        as_of=ASOF,
    )

    stages = result["manifest"]["pipeline"]["stages"]
    assert [row["status"] for row in stages[:4]] == [
        "complete", "complete", "complete", "complete",
    ]
    assert stages[4]["status"] == "incomplete"
    assert stages[5]["status"] == "skipped"
    assert result["status"] == result["completion_status"] == "incomplete"
    assert "valuation.vibe_seven_function_contract" in result["manifest"]["connected_nodes"]
    assert result["manifest"]["known_unconnected_nodes"] == [
        "events.global_events_db_normalisation",
    ]
    assert result["engine_evidence_pack"]["sections"]["workspace_live"]["datasets"]
    assert result["engine_evidence_pack"]["sections"]["company_research_manifest"]["status"] == "incomplete"
    pre_report = result["engine_evidence_pack"]["sections"]["company_research_manifest"]
    assert pre_report["pre_report_source_status"] == "complete"
    assert pre_report["lifecycle_pending_stages"] == ["risk", "report"]
    assert result["manifest"]["pre_report_source_status"] == "complete"
    valuation_dag = result["evidence"]["contracts"]["valuation_extended_dag"]
    assert valuation_dag["guardrails"]["price_scenario_nodes_included"] is False
    assert all(not row["calc_id"].startswith("valuation.scenario.")
               for row in valuation_dag["nodes"])
    assert all("implied_price" not in str(row) and "implied_return" not in str(row)
               for row in result["calculations"])
    vibe_calculations = result["evidence"]["contracts"]["valuation"]["calculations"]
    market_cap = next(row for row in vibe_calculations if row["function"] == "market_cap")
    assert market_cap["output"]["value"] == 10_120
    assert market_cap["output"]["details"]["source"] == "derived"
    assert set(market_cap["input_refs"]) == {
        "pack.sections.quote_live.price",
        "pack.sections.financials.rows[9].shares_outstanding",
    }
    calculation_ids = {
        str(row.get("calculation_id") or row.get("calc_id") or row.get("id"))
        for row in result["calculations"]
        if row.get("calculation_id") or row.get("calc_id") or row.get("id")
    }
    source_aliases = {
        str(row.get(key))
        for row in result["evidence"]["hub"]["source_register"]
        for key in ("source_id", "evidence_id", "source_ref")
        if row.get(key)
    }
    for source in result["evidence"]["hub"]["source_register"]:
        source_ref = str(source.get("source_ref") or "")
        if source_ref.startswith("section:"):
            section = source_ref.split(":", 1)[1].split("#", 1)[0]
            source_aliases.update({f"pack.{section}", f"pack.sections.{section}"})

    def lineage_refs(row):
        refs = list(row.get("input_refs") or [])
        raw_inputs = row.get("inputs") or []
        input_rows = raw_inputs if isinstance(raw_inputs, list) else [raw_inputs]
        for item in input_rows:
            if isinstance(item, str):
                refs.append(item)
            elif isinstance(item, dict):
                refs.extend(
                    str(item[key]) for key in ("ref", "evidence_id", "calculation_id")
                    if item.get(key)
                )
        refs.extend(row.get("depends_on") or [])
        return {str(ref) for ref in refs if ref}

    unresolved = []
    for calculation in result["calculations"]:
        for ref in lineage_refs(calculation):
            if ref in calculation_ids or ref in source_aliases:
                continue
            if ref.startswith("pack.sections."):
                section = ref.split(".", 3)[2]
                if f"pack.{section}" in source_aliases:
                    continue
            unresolved.append((calculation.get("calculation_id"), ref))
    assert unresolved == []
    lineage_gate = validate_company_research_report(
        "lineage audit fixture", evidence=result["evidence"], calculations=result["calculations"],
        upstream="complete",
    )
    derived_unknown = [
        row for row in lineage_gate["audit"]["inventory"]["calculation_lineage"]
        if row["usable"] and row["scope"] == "derived_unknown"
    ]
    assert derived_unknown == []
    assert "不是最终投资观点" in result["report_md"]
    assert any(row.get("kind") == "announcement" for row in result["events"])
    risk_gaps = {
        row["code"] for row in result["evidence"]["contracts"]["risk"]["gaps"]
        if row.get("severity") == "critical"
    }
    assert "forward_vs_ttm_judgement_missing" not in risk_gaps
    assert "consensus_dispersion_missing" not in risk_gaps
    assert {
        "strong_claim_inventory_missing", "true_counter_group_missing",
        "issuer_decision_points_insufficient",
    } <= risk_gaps
    chain = next(
        row for row in result["evidence"]["hub"]["source_register"]
        if row["source_id"] == "pack.chain"
    )
    assert chain["type"] == "context"


def test_workspace_status_uses_failed_stale_incomplete_complete_precedence():
    pack = _pack()
    for section in pack["sections"].values():
        if isinstance(section, dict):
            section["stale"] = True
    calendar = _calendar()
    calendar["snapshot"]["stale"] = True

    result = build_workspace_company_artifacts(
        pack, [calendar],
        subject={"kind": "stock", "code": "SH603986", "name": "兆易创新"},
        source_plan=[], as_of=ASOF,
    )

    assert result["completion_status"] == "incomplete"
    assert result["freshness_status"] == "stale"
    assert result["status"] == "stale"
    assert result["manifest"]["pipeline"]["status"] == "stale"
    # Lifecycle-pending risk/report stages are excluded, but a real stale
    # source stage must still cap the report candidate at stale.
    assert result["manifest"]["pre_report_source_status"] == "stale"
    assert result["engine_evidence_pack"]["sections"][
        "company_research_manifest"
    ]["pre_report_source_status"] == "stale"


def test_pre_report_source_status_keeps_real_required_source_gap_incomplete():
    pack = _pack()
    pack["sections"].pop("financials")
    pack["coverage"]["company_core"].update({
        "missing": ["financials"], "status": "incomplete",
    })

    result = build_workspace_company_artifacts(
        pack, [_calendar()],
        subject={"kind": "stock", "code": "SH603986", "name": "兆易创新"},
        source_plan=[], as_of=ASOF,
    )

    assert result["manifest"]["pre_report_source_status"] == "incomplete"
    stages = result["manifest"]["pipeline"]["stages"]
    assert next(row for row in stages if row["stage"] == "financials")["status"] == "incomplete"


def test_unreviewable_pack_newsrader_never_enters_company_events():
    pack = {
        "sections": {
            "newsradar": {"items": [
                {"title": "SH603986 兆易创新所在产业资讯",
                 "source_time": "2026-08-29T10:00:00Z",
                 "time_bucket": "recent"},
                {"title": "未来源时间", "source_time": "2026-09-01T10:00:00Z",
                 "time_bucket": "future_source_time"},
            ]},
        },
    }
    events = normalise_company_events(pack, [], subject_code="SH603986")
    assert events == []


def test_peripheral_calendars_and_control_lists_remain_unbound_candidates():
    snapshots = [
        {"snapshot": {
            "dataset_id": "research.overseas_earnings_calendar.live",
            "rows": [{"symbol": "AAPL", "expected_date": "2026-09-01"}],
        }},
        {"snapshot": {
            "dataset_id": "research.control_lists.live",
            "rows": [{"entity": "Example Holdings", "date": "2026-08-31"}],
        }},
    ]
    events = normalise_company_events({}, snapshots, subject_code="SH603986")

    assert len(events) == 2
    assert all(row["subject_code"] is None for row in events)
    assert all(row["payload"]["issuer_fact"] is False for row in events)
    assert all(row["payload"]["user_judgement_required"] is True for row in events)


def test_workspace_newsrader_auto_selects_industry_and_persists_candidates(
        tmp_path, monkeypatch):
    from guanlan_v2.datafeed import strategic_live
    from guanlan_v2.research import company_industry_news

    captured = {}
    monkeypatch.setattr(
        company_industry_news,
        "_industry_record",
        lambda code: {
            "status": "available", "name": "兆易创新", "industry": "半导体",
            "source_id": "tushare.stock_basic.industry",
            "taxonomy": "Tushare 基础行业（非申万/证监会分类）",
            "observed_at": "2026-08-28T19:32:34+08:00",
            "source_sha256": "a" * 64, "point_in_time": False,
        },
    )

    def fake_query(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True, "dataset_id": "research.newsradar.live", "status": "ok",
            "query": dict(kwargs),
            "rows": [{
                "title": "Semiconductor supply-chain update",
                "source": "Example", "url": "https://example.test/semi",
                "sector": "semi", "source_time": "2026-08-30T09:00:00Z",
            }],
            "sources": [{"authority": "Example", "status": "ok"}],
            "pulled_at": "2026-08-30T09:01:00Z",
            "as_of": "2026-08-30T09:00:00Z",
            "warnings": [],
        }

    monkeypatch.setattr(strategic_live, "query_newsradar_live", fake_query)
    store, _workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_newsradar_impl()
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    assert captured["sectors"] == ["semi"]
    assert result["asset"]["dataset_id"] == "research.newsradar.live"
    assert result["snapshot"]["industry_context"]["industry"] == "半导体"
    row = result["snapshot"]["rows"][0]
    relation = result["snapshot"]["industry_candidate_relations"]["rows"][0]
    assert "relation_status" not in row and "issuer_fact" not in row
    assert relation["relation_status"] == "unreviewed"
    assert relation["issuer_fact"] is False
    assert relation["row_index"] == 0
    assert "subject_code" not in row


def test_company_prepare_keeps_unreviewed_industry_news_out_of_report_events(
        tmp_path, monkeypatch):
    import guanlan_v2.server  # noqa: F401 - locks the report_seams import sequence

    assert os.environ.get("GUANLAN_EVENTS_INGEST") == "0"
    isolated_events_db = Path(os.environ["GUANLAN_EVENTS_DB"])
    assert isolated_events_db == tmp_path / "_isolated_events" / "events.db"
    assert isolated_events_db.is_relative_to(tmp_path)

    from guanlan_v2.datafeed import strategic_live
    from guanlan_v2.research import company_industry_news

    observed = {}
    monkeypatch.setattr(
        company_industry_news,
        "_industry_record",
        lambda code: {
            "status": "available", "name": "兆易创新", "industry": "半导体",
            "source_id": "tushare.stock_basic.industry",
            "taxonomy": "Tushare 基础行业（非申万/证监会分类）",
            "observed_at": "2026-08-28T19:32:34+08:00",
            "source_sha256": "b" * 64, "point_in_time": False,
        },
    )

    def fake_query(**kwargs):
        observed.update(kwargs)
        return {
            "ok": True, "dataset_id": "research.newsradar.live", "status": "ok",
            "query": dict(kwargs),
            "rows": [{
                "title": "Foundry capacity update", "source": "Example",
                "url": "https://example.test/foundry", "sector": "semi",
                "source_time": f"{date.today().isoformat()}T09:00:00Z",
            }],
            "sources": [{"authority": "Example", "status": "ok"}],
            "pulled_at": f"{date.today().isoformat()}T09:01:00Z",
            "as_of": f"{date.today().isoformat()}T09:00:00Z", "warnings": [],
        }

    monkeypatch.setattr(strategic_live, "query_newsradar_live", fake_query)
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack",
        lambda code, asof="": (_pack(code), {"ok": True, "path": None}),
    )
    store, workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=date.today().isoformat(), refresh_calendar=False,
            include_latest_snapshots=False, include_newsradar=True,
        )
        plan = next(
            row for row in result["source_plan"]
            if row["dataset_id"] == "research.newsradar.live"
        )
        events_path = Path(workspace["root_path"], result["asset"]["run_rel"], "events.json")
        events = json.loads(events_path.read_text(encoding="utf-8"))
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    assert observed["sectors"] == ["semi"]
    assert plan["status"] == "available"
    assert plan["industry_context"]["selection_mode"] == "automatic_exact_crosswalk"
    assert not any(
        str(row.get("kind") or "").startswith("industry_signal")
        for row in events
    )
    evidence = json.loads(Path(
        workspace["root_path"], result["asset"]["run_rel"], "evidence.json",
    ).read_text(encoding="utf-8"))
    gate = evidence["industry_review_gate"]
    assert gate["candidate_counts"]["unreviewed"] == 1
    assert gate["report_eligible_candidate_ids"] == []
    projected = next(
        row["snapshot"] for row in evidence["workspace_snapshots"]
        if row["snapshot"]["dataset_id"] == "research.newsradar.live"
    )
    assert projected["rows"] == []
    assert projected["industry_review_gate"]["status"] == "awaiting_review"
    assert Path(workspace["root_path"]).is_relative_to(tmp_path)
    assert not isolated_events_db.exists()


def test_workspace_newsrader_unmapped_industry_does_not_fetch_all_sectors(
        tmp_path, monkeypatch):
    from guanlan_v2.datafeed import strategic_live
    from guanlan_v2.research import company_industry_news

    monkeypatch.setattr(
        company_industry_news,
        "_industry_record",
        lambda code: {
            "status": "available", "name": "映射缺口样本",
            "industry": "测试未映射行业__唯一_9F3D",
            "source_id": "tushare.stock_basic.industry", "taxonomy": "Tushare 基础行业",
            "observed_at": "2026-08-28T19:32:34+08:00",
            "source_sha256": "c" * 64, "point_in_time": False,
        },
    )
    monkeypatch.setattr(
        strategic_live, "query_newsradar_live",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    store, workspace, _sid = _world(tmp_path)
    workspace = store.workspace_store.update(
        workspace["id"],
        {"code": "SH603929", "name": "亚翔集成", "display_name": "亚翔集成"},
    )
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_newsradar_impl()
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is False
    assert result["status"] == "industry_unmapped"
    assert result["industry_context"]["radar_sectors"] == []
    assert result["artifact"] is None


def test_workspace_newsradar_broad_context_fetches_visible_review_candidates(
        tmp_path, monkeypatch):
    from guanlan_v2.datafeed import strategic_live
    from guanlan_v2.research import company_industry_news

    captured = {}
    monkeypatch.setattr(
        company_industry_news,
        "_industry_record",
        lambda code: {
            "status": "available", "name": "亚翔集成", "industry": "装修装饰",
            "source_id": "tushare.stock_basic.industry", "taxonomy": "Tushare 基础行业",
            "observed_at": "2026-08-28T19:32:34+08:00",
            "source_sha256": "d" * 64, "point_in_time": False,
        },
    )

    def fake_query(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True, "dataset_id": "research.newsradar.live", "status": "ok",
            "query": dict(kwargs),
            "rows": [{
                "title": "宏观建材需求候选", "source": "Example",
                "url": "https://example.test/construction", "sector": "macro",
                "source_time": f"{date.today().isoformat()}T09:00:00Z",
            }],
            "sources": [{"authority": "Example", "status": "ok"}],
            "pulled_at": f"{date.today().isoformat()}T09:01:00Z",
            "as_of": f"{date.today().isoformat()}T09:00:00Z", "warnings": [],
        }

    monkeypatch.setattr(strategic_live, "query_newsradar_live", fake_query)
    store, workspace, _sid = _world(tmp_path)
    workspace = store.workspace_store.update(
        workspace["id"],
        {"code": "SH603929", "name": "亚翔集成", "display_name": "亚翔集成"},
    )
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    token = _bound(store, sid)
    try:
        result = workspace_tools.workspace_newsradar_impl()
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert result["ok"] is True
    assert captured["sectors"] == ["macro"]
    context = result["snapshot"]["industry_context"]
    assert context["selection_mode"] == "automatic_reviewed_broad_context"
    assert context["mapping_quality"] == "broad_context"
    assert context["user_judgement_required"] is True
    relation = result["snapshot"]["industry_candidate_relations"]["rows"][0]
    assert relation["relation_status"] == "unreviewed"
    assert relation["user_judgement_required"] is True
    assert relation["issuer_fact"] is False
    assert any("逐条判断" in warning for warning in result["snapshot"]["warnings"])


def test_prepare_tool_persists_private_bundle_and_detects_missing_attachment(
        tmp_path, monkeypatch):
    store, workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        snapshot = _calendar()["snapshot"]
        calendar_asset = workspace_tools.save_workspace_data_snapshot(
            store, workspace["id"], workspace,
            dataset_id=snapshot["dataset_id"], title="calendar",
            query=snapshot["query"], snapshot=snapshot,
            source_tool="ww_workspace_trade_disclosure_calendar",
        )
        observed_cutoff = {}

        def _fake_pack(code, asof=""):
            observed_cutoff["value"] = asof
            return _pack(code), {"ok": True, "path": None}

        monkeypatch.setattr(workspace_tools, "_build_company_pack", _fake_pack)
        result = workspace_tools.workspace_company_research_prepare_impl(
            asof=ASOF,
            refresh_calendar=False,
            include_newsradar=False,
            include_latest_snapshots=True,
        )
        assert result["ok"] is True
        assert observed_cutoff["value"] == ASOF
        assert result["status"] == "incomplete"
        assert "公司研究六阶段和最终七章节 Gate 已接通" in result["content"]
        assert "风险 Gate 需报告 Agent 产出结构化强结论、真实反证和 3 个裁决点" \
            in result["content"]
        assert "估值计算 DAG、风险裁决契约、最终七章节合规门" not in result["content"]
        asset = result["asset"]
        assert asset["workspace_id"] == workspace["id"]
        assert asset["subject"]["code"] == "SH603986"
        assert asset["snapshot_asset_ids"] == [calendar_asset["id"]]
        assert len(asset["attachments"]["documents"]) == 6
        assert Path(workspace["root_path"], "research", "company").is_dir()

        replay = workspace_tools.workspace_asset_get_impl(asset["id"])
        assert replay["ok"] is True
        assert len(replay["artifact"]["payload"]["documents"]) == 6
        context = workspace_tools.company_report_background_context("SH603986")
        assert context["prepared_asset_id"] == asset["id"]
        accepted = console_tools.report_run_impl("SH603986", name="兆易创新")
        assert accepted["ok"] is True
        assert accepted["background"]["workspace_id"] == workspace["id"]
        assert accepted["background"]["prepared_asset_id"] == asset["id"]

        engine_pack = workspace_tools.resolve_company_report_pack(
            store, sid, workspace["id"], asset["id"], "SH603986",
        )
        assert engine_pack.name == "engine_evidence_pack.json"
        final_md = tmp_path / "SH603986_final.md"
        final_md.write_text("# 最终研报\n\n证据缺口保持显形。\n", encoding="utf-8")
        final = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], asset["id"], "SH603986", str(final_md),
        )
        assert final["ok"] is True
        assert final["asset"]["source"] == "ww_workspace_company_research_run"
        assert final["asset"]["status"] == "incomplete"
        assert final["report_gate"]["status"] == "incomplete"
        assert final["risk_sidecar"]["status"] == "missing"
        assert final["risk_sidecar"]["reason_code"] == "risk_sidecar_missing"
        assert final["risk_contract"]["status"] == "incomplete"
        final_risk_stage = next(
            row for row in final["upstream_gate"]["stages"] if row["stage"] == "risk"
        )
        assert final_risk_stage["status"] == "incomplete"
        assert final["upstream_gate"]["status"] == "incomplete"
        assert len(final["asset"]["attachments"]["documents"]) == 11
        assert final["asset"]["report_gate_sha256"]
        persisted_risk = json.loads(Path(
            workspace["root_path"], final["asset"]["run_rel"],
            "risk_contract_final.json",
        ).read_text(encoding="utf-8"))
        assert persisted_risk["sidecar"]["status"] == "missing"

        first_document = asset["expected_documents"][0]
        MediaStore(store)._metadata_path(first_document).unlink()
        incomplete = workspace_tools.workspace_asset_get_impl(asset["id"])
        assert incomplete["ok"] is False
        assert incomplete["status"] == "incomplete"
        assert first_document in incomplete["missing_attachments"]
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_replays_valid_sidecar_and_closes_only_the_risk_stage(
        tmp_path, monkeypatch):
    store, workspace, sid, token, prepared = _prepare_saved_company(tmp_path, monkeypatch)
    try:
        prepared_asset = prepared["asset"]
        prepared_run = Path(workspace["root_path"], prepared_asset["run_rel"])
        ledger = json.loads((prepared_run / "evidence.json").read_text(encoding="utf-8"))
        prepared_manifest = json.loads(
            (prepared_run / "manifest.json").read_text(encoding="utf-8")
        )
        risk_input = _valid_company_risk_input(ledger["hub"])

        final_md = tmp_path / "SH603986_valid_final.md"
        final_md.write_text("# 最终研报\n\n证据缺口保持显形。\n", encoding="utf-8")
        final_md.with_suffix(".json").write_text(json.dumps(
            {"rating_overall": 5, "company_risk_input": risk_input},
            ensure_ascii=False,
        ), encoding="utf-8")

        final = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], prepared_asset["id"],
            "SH603986", str(final_md),
        )

        assert final["ok"] is True
        assert final["risk_sidecar"]["status"] == "loaded"
        assert final["risk_contract"]["status"] == "complete"
        assert final["risk_contract"]["gate"]["passed"] is True
        assert final["risk_contract"]["gate"]["counter_evidence"]["passed"] is True
        assert final["risk_contract"]["gate"]["decision_points"]["eligible_issuer"] == 3
        critical_codes = {
            row["code"] for row in final["risk_contract"]["gaps"]
            if row.get("severity") == "critical"
        }
        assert "forward_vs_ttm_judgement_missing" not in critical_codes
        assert "consensus_dispersion_missing" not in critical_codes
        risk_stage = next(
            row for row in final["upstream_gate"]["stages"] if row["stage"] == "risk"
        )
        assert risk_stage["status"] == "complete"
        assert risk_stage["evidence_ids"] == [final["risk_contract"]["contract_id"]]
        assert final["upstream_gate"]["status"] == "complete"
        assert final["upstream_gate"]["risk_contract"]["contract_id"] == \
            final["risk_contract"]["contract_id"]
        # The deliberately skeletal Markdown still fails the independent
        # seven-section report contract; closing risk must not fake report readiness.
        assert final["report_gate"]["status"] == "incomplete"
        assert final["status"] == "incomplete"
        assert len(final["asset"]["attachments"]["documents"]) == 12
        assert final["asset"]["risk_contract_status"] == "complete"

        final_run = Path(workspace["root_path"], final["asset"]["run_rel"])
        persisted_risk = json.loads(
            (final_run / "risk_contract_final.json").read_text(encoding="utf-8")
        )
        persisted_manifest = json.loads(
            (final_run / "manifest_final.json").read_text(encoding="utf-8")
        )
        assert persisted_risk["contract_id"] == final["risk_contract"]["contract_id"]
        assert persisted_risk["sidecar"]["status"] == "loaded"
        assert persisted_manifest["risk_contract"]["status"] == "complete"
        assert persisted_manifest["time_contract"] == \
            prepared_manifest["time_contract"]
        assert final["time_contract"] == prepared_manifest["time_contract"]
        assert final["asset"]["time_contract"] == prepared_manifest["time_contract"]
        assert "risk_contract_final.json" in persisted_manifest["files"]
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_effect_boundary_recovers_commit_and_refuses_ambiguous_replay(
        tmp_path, monkeypatch):
    store, workspace, sid, token, prepared = _prepare_saved_company(
        tmp_path, monkeypatch,
    )
    try:
        prepared_id = prepared["asset"]["id"]
        final_md = tmp_path / "SH603986_effect_final.md"
        final_md.write_text(
            "# 最终研报\n\n证据缺口保持显形。\n", encoding="utf-8",
        )
        report_sha = hashlib.sha256(final_md.read_bytes()).hexdigest()

        def authority(job_id):
            context = {
                "job_id": job_id,
                "workspace_id": workspace["id"],
                "prepared_asset_id": prepared_id,
                "code": "SH603986",
                "report_sha256": report_sha,
                "binding_sha256": "b" * 64,
                "pipeline_mode": "legacy",
                "typed_plan_digest": "",
            }
            key = hashlib.sha256(json.dumps(
                context, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            return key, context

        effect_key, effect_context = authority("bg_1313131313")
        first = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], prepared_id, "SH603986", str(final_md),
            _effect_key=effect_key, _effect_context=effect_context,
        )
        assert first["asset"]["finalizer_effect_key"] == effect_key
        assert first["finalizer_effect_recovered"] is False
        assert first["finalizer_replayed"] is False
        asset_files = set((
            store.root / "workspace_assets" / workspace["id"]
        ).glob("wa_*.json"))
        run_dirs = set(Path(workspace["root_path"], "research", "company").iterdir())

        recovered = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], prepared_id, "SH603986", str(final_md),
            _effect_key=effect_key, _effect_context=effect_context,
        )
        assert recovered["asset"]["id"] == first["asset"]["id"]
        assert recovered["finalizer_effect_recovered"] is True
        assert recovered["finalizer_replayed"] is False
        assert set((
            store.root / "workspace_assets" / workspace["id"]
        ).glob("wa_*.json")) == asset_files
        assert set(Path(
            workspace["root_path"], "research", "company",
        ).iterdir()) == run_dirs

        ambiguous_key, ambiguous_context = authority("bg_1414141414")
        root = workspace_tools._root_for(store, workspace["id"])
        with workspace_tools._single_writer(root):
            claim_path, claim, recovered_asset = (
                workspace_tools._claim_company_finalizer_effect(
                    store,
                    workspace_id=workspace["id"],
                    prepared_asset_id=prepared_id,
                    code="SH603986",
                    report_sha256=report_sha,
                    effect_key=ambiguous_key,
                    effect_context=ambiguous_context,
                )
            )
        assert claim["status"] == "in_progress"
        assert recovered_asset is None
        monkeypatch.setattr(
            workspace_tools, "_new_company_research_dir",
            lambda *_args, **_kwargs: pytest.fail(
                "ambiguous replay crossed the effect boundary"
            ),
        )
        with pytest.raises(
            workspace_tools.CompanyFinalizerReplayRefused,
            match="拒绝不安全重放",
        ):
            workspace_tools.finalize_company_research_report(
                store, sid, workspace["id"], prepared_id, "SH603986",
                str(final_md), _effect_key=ambiguous_key,
                _effect_context=ambiguous_context,
            )
        assert json.loads(claim_path.read_text(encoding="utf-8"))["status"] == (
            "in_progress"
        )
        # Same Markdown/effect key, new JSON: do not return an old asset with
        # a newly recomputed risk gate. No second asset or directory is created.
        final_md.with_suffix(".json").write_text(
            '{"company_risk_input":{},"assumptions":[{"value":99}]}', encoding="utf-8",
        )
        with pytest.raises(workspace_tools.CompanyFinalizerReplayRefused, match="拒绝混合两次结果"):
            workspace_tools.finalize_company_research_report(
                store, sid, workspace["id"], prepared_id, "SH603986", str(final_md),
                _effect_key=effect_key, _effect_context=effect_context,
            )
        assert set((store.root / "workspace_assets" / workspace["id"]).glob("wa_*.json")) == asset_files
        assert set(Path(workspace["root_path"], "research", "company").iterdir()) == run_dirs
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_closes_full_company_path_with_compliant_report_and_valid_sidecar(
        tmp_path, monkeypatch):
    store, workspace, sid, token, prepared = _prepare_saved_company(tmp_path, monkeypatch)
    try:
        prepared_asset = prepared["asset"]
        prepared_run = Path(workspace["root_path"], prepared_asset["run_rel"])
        ledger = json.loads((prepared_run / "evidence.json").read_text(encoding="utf-8"))
        calculations = json.loads(
            (prepared_run / "calculations.json").read_text(encoding="utf-8")
        )
        hub = ledger["hub"]
        refs = {
            row["source_id"]: row["evidence_id"]
            for row in hub["source_register"]
            if row.get("type") == "issuer" and row.get("usable") and row.get("evidence_id")
        }
        market_cap_calc = next(
            row for row in calculations
            if row.get("function") == "market_cap" and row.get("status") == "complete"
        )
        report = f"""<!-- company-research-status: complete -->
# 公司研究报告

本报告仅供研究参考，不构成任何投资建议；属于非个性化研究，未考虑个人财务状况、风险承受能力或投资目标。

## [section:company_profile] 结论摘要

公司主体及上市状态已经发行人证据核验。[evidence:{refs['exchange_current_listing']}]

## [section:financials] 事实

累计财务报表已进入本次点时证据账本。[evidence:{refs['financials_pit_loader']}]

## [section:expectations] 推断

一致预期只作为前瞻假设基线。[evidence:{refs['ths_eps_forecast']}]

## [section:valuation] 估值

估值节点已由可复算合同生成。[calculation:{market_cap_calc['calculation_id']}]

## [section:risk_catalysts] 风险与反证

经营修复能否延续的强结论及反证已由最终风险合同复核。
[claim:claim-recovery-no-delay] [evidence:{refs['cninfo_announcements']}]

## [section:scenarios_verdict] 裁决点

验收进度变化将更新判断。[decision:delivery-close]
一致预期变化将更新判断。[decision:eps-dispersion]
后续财务披露将更新判断。[decision:revenue-confirmation]
[evidence:{refs['cninfo_announcements']}]

## [section:sources_gaps] 数据缺口

本次证据覆盖与剩余缺口均以归档清单为准。[evidence:{refs['exchange_current_listing']}]
"""
        final_md = tmp_path / "SH603986_complete_final.md"
        report = report.replace(
            "## [section:sources_gaps] 数据缺口",
            "情景假设：收入增长 12%。[assumption:ASM-REVENUE]\n\n"
            "## [section:sources_gaps] 数据缺口",
        )
        final_md.write_text(report, encoding="utf-8")
        final_md.with_suffix(".json").write_text(json.dumps(
            {"company_risk_input": _valid_company_risk_input(hub), "assumptions": [{
                "assumption_id": "ASM-REVENUE", "section_id": "scenarios_verdict",
                "value": "12%",
            }]}, ensure_ascii=False,
        ), encoding="utf-8")
        review_bytes = json.dumps({
            "schema_version": "company-writer-draft-review@1", "final_acceptance": False,
            "selected": "initial", "drafts": {"initial": {"markdown_body": report}},
        }, ensure_ascii=False).encode("utf-8")
        final_md.with_suffix(".draft-review.json").write_bytes(review_bytes)

        final = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], prepared_asset["id"],
            "SH603986", str(final_md),
        )

        assert final["ok"] is True
        assert final["risk_contract"]["status"] == "complete"
        assert final["upstream_gate"]["status"] == "complete"
        assert final["report_gate"]["status"] == "complete", json.dumps(
            final["report_gate"]["violations"], ensure_ascii=False, indent=2,
        )
        assert final["status"] == final["completion_status"] == "complete"
        assert final["asset"]["status"] == "complete"
        final_run = Path(workspace["root_path"], final["asset"]["run_rel"])
        manifest = json.loads((final_run / "manifest_final.json").read_text(encoding="utf-8"))
        assert (final_run / "writer_draft_review.json").read_bytes() == review_bytes
        assert (final_run / "report_input.json").read_bytes() == final_md.with_suffix(".json").read_bytes()
        assert manifest["writer_diagnostics"]["writer_draft_review.json"]["diagnostic_only"] is True
        assert manifest["files"]["writer_draft_review.json"]["sha256"] == hashlib.sha256(review_bytes).hexdigest()
        review_notes = (final_run / "review_notes.md").read_bytes()
        assert manifest["files"]["review_notes.md"]["sha256"] == hashlib.sha256(review_notes).hexdigest()
        assert "公司报告审查清单" in review_notes.decode("utf-8")
        assert "反证方向正确" in review_notes.decode("utf-8")
        assert "assumption_not_registered" not in {
            row["code"] for row in final["report_gate"]["warnings"]
        }
        # The same scenario register is used by preflight and materialization;
        # changing its value must be caught, not silently treated as unregistered.
        sidecar = json.loads(final_md.with_suffix(".json").read_text(encoding="utf-8"))
        sidecar["assumptions"][0]["value"] = "99%"
        final_md.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
        changed = workspace_tools.evaluate_company_research_report_gates(
            store, sid, workspace["id"], prepared_asset["id"], "SH603986", str(final_md),
        )
        assert "assumption_value_mismatch" in {
            row["code"] for row in changed["report_gate"]["violations"]
        }
        assert changed["report_gate"]["status"] == "incomplete"
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_refuses_sidecar_change_between_risk_and_diagnostic_reads(
        tmp_path, monkeypatch):
    from guanlan_v2.research import company_report_audit

    store, workspace, sid, token, prepared = _prepare_saved_company(tmp_path, monkeypatch)
    try:
        final_md = tmp_path / "SH603986_changed_final.md"
        final_md.write_text("# Report", encoding="utf-8")
        final_md.with_suffix(".json").write_text('{"company_risk_input":{}}', encoding="utf-8")
        original_loader = company_report_audit.load_company_report_diagnostics

        def change_between_reads(source, **kwargs):
            source.with_suffix(".json").write_text(
                '{"company_risk_input":{},"assumptions":[{"value":99}]}', encoding="utf-8",
            )
            return original_loader(source, **kwargs)

        monkeypatch.setattr(company_report_audit, "load_company_report_diagnostics", change_between_reads)
        before = set(Path(workspace["root_path"], "research", "company").iterdir())
        with pytest.raises(workspace_tools.WorkspaceToolError, match="风险读取与审计归档之间变化"):
            workspace_tools.finalize_company_research_report(
                store, sid, workspace["id"], prepared["asset"]["id"], "SH603986", str(final_md),
            )
        assert set(Path(workspace["root_path"], "research", "company").iterdir()) == before
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_records_invalid_sidecar_as_incomplete_without_raising(
        tmp_path, monkeypatch):
    store, workspace, sid, token, prepared = _prepare_saved_company(tmp_path, monkeypatch)
    try:
        final_md = tmp_path / "SH603986_invalid_final.md"
        final_md.write_text("# 最终研报\n\n证据缺口保持显形。\n", encoding="utf-8")
        final_md.with_suffix(".json").write_text("{invalid", encoding="utf-8")

        final = workspace_tools.finalize_company_research_report(
            store, sid, workspace["id"], prepared["asset"]["id"],
            "SH603986", str(final_md),
        )

        assert final["ok"] is True
        assert final["status"] == "incomplete"
        assert final["risk_sidecar"]["status"] == "invalid"
        assert final["risk_sidecar"]["reason_code"] == "risk_sidecar_invalid_json"
        assert final["risk_contract"]["status"] == "incomplete"
        assert final["upstream_gate"]["status"] == "incomplete"
        risk_stage = next(
            row for row in final["upstream_gate"]["stages"] if row["stage"] == "risk"
        )
        assert risk_stage["status"] == "incomplete"
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_rejects_when_industry_review_changed_after_prepare(
        tmp_path, monkeypatch):
    from guanlan_v2.research.company_industry_news import annotate_newsradar_snapshot

    store, workspace, sid, token, prepared = _prepare_saved_company(tmp_path, monkeypatch)
    try:
        snapshot = annotate_newsradar_snapshot({
            "ok": True, "dataset_id": "research.newsradar.live", "status": "ok",
            "query": {"sectors": ["semi"]},
            "rows": [{
                "title": "Foundry capacity update", "source": "Example",
                "url": "https://example.test/foundry", "sector": "semi",
                "source_time": f"{ASOF}T09:00:00+08:00",
            }],
            "sources": [{"authority": "Example", "status": "ok"}],
            "pulled_at": f"{ASOF}T09:01:00+08:00", "as_of": ASOF,
            "warnings": [],
        }, {
            "industry": "半导体", "research_scope": "industry_context",
            "subject_code": "SH603986", "radar_sectors": ["semi"],
        })
        asset = workspace_tools.save_workspace_data_snapshot(
            store, workspace["id"], workspace,
            dataset_id="research.newsradar.live", title="candidate",
            query={"sectors": ["semi"]}, snapshot=snapshot,
            source_tool="ww_workspace_newsradar",
        )
        candidate_id = snapshot["industry_candidate_relations"]["rows"][0][
            "candidate_id"
        ]
        reviewed = workspace_tools.workspace_industry_review_impl(
            asset["id"], candidate_id, "related", 0,
            "late-review-change-01",
        )
        assert reviewed["ok"] is True
        final_md = tmp_path / "stale_review.md"
        final_md.write_text("# 最终研报\n", encoding="utf-8")
        with pytest.raises(workspace_tools.WorkspaceToolError, match="审核账本.*变化"):
            workspace_tools.finalize_company_research_report(
                store, sid, workspace["id"], prepared["asset"]["id"],
                "SH603986", str(final_md),
            )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_company_prepare_registry_and_mcp_permissions_are_explicit():
    console = {row["name"]: row for row in console_tools.WW_TOOL_TABLE}
    mcp = {row["name"]: row for row in build_mcp_tools()}
    for name in ("ww_workspace_newsradar", "ww_workspace_company_research_prepare"):
        assert name in workspace_tools.ALL_WORKSPACE_TOOLS
        assert console[name]["confirm"] is False
        assert console[name]["cost"] == "seconds"
        assert mcp[name]["read_only"] is False
        assert mcp[name]["destructive"] is False
        assert mcp[name]["gated"] is False
        assert "workspace_id" in mcp[name]["inputSchema"]["required"]
    assert console["ww_workspace_industry_review_list"]["confirm"] is False
    assert mcp["ww_workspace_industry_review_list"]["read_only"] is True
    assert mcp["ww_workspace_industry_review_list"]["gated"] is False
    assert console["ww_workspace_industry_review"]["confirm"] is True
    assert mcp["ww_workspace_industry_review"]["read_only"] is False
    assert mcp["ww_workspace_industry_review"]["destructive"] is True
    assert mcp["ww_workspace_industry_review"]["gated"] is True


def test_company_pack_builder_passes_explicit_pit_cutoff(monkeypatch):
    from guanlan_v2.reports import evidence as report_evidence

    observed = {}

    def _fake_build(code, out_dir=None, *, asof=None):
        observed.update({"code": code, "out_dir": out_dir, "asof": asof})
        return {
            "ok": True, "path": None, "coverage": {}, "errors": {},
        }

    monkeypatch.setattr(report_evidence, "build_evidence_pack", _fake_build)
    monkeypatch.setattr(
        workspace_tools, "_company_profile_section",
        lambda code: ({"code": code, "as_of": ASOF}, ""),
    )
    monkeypatch.setattr(
        workspace_tools, "_financials_section",
        lambda code: ({"rows": [], "as_of": ASOF}, ""),
    )

    pack, receipt = workspace_tools._build_company_pack("SH603986", ASOF)

    assert observed["code"] == "SH603986"
    assert observed["asof"] == ASOF
    assert observed["out_dir"] is not None
    assert pack["sections"]["company_profile"]["as_of"] == ASOF
    assert receipt["temporary_path_discarded"] is True


def test_company_report_context_rejects_cross_subject(tmp_path):
    store, _workspace, sid = _world(tmp_path)
    token = _bound(store, sid)
    try:
        try:
            workspace_tools.company_report_background_context("SZ300750")
        except workspace_tools.WorkspaceToolError as exc:
            assert "拒绝" in str(exc)
        else:  # pragma: no cover - fail closed contract
            raise AssertionError("cross-subject report context was accepted")
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_finalizer_status_precedence_matches_vibe_and_missing_never_erases_stale():
    worst = workspace_tools._company_final_worst_status

    assert worst("complete", "incomplete") == "incomplete"
    assert worst("incomplete", "stale") == "stale"
    assert worst("stale", "failed") == "failed"
    assert worst("stale", "incomplete") == "stale"
