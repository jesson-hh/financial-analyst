import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from financial_analyst.data.trusted_receipts import (
    KEY_ENV,
    KEY_ID_ENV,
    WORKSPACE_SNAPSHOT_DOMAIN,
    verify_payload,
    workspace_snapshot_auth_payload,
)
from guanlan_v2.console import tools as console_tools
from guanlan_v2.console import workspace_tools
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.datafeed import global_markets_live
from guanlan_v2.datafeed import strategic_live
from guanlan_v2.glmcp.tooltable import build_mcp_tools


LIVE_TOOLS = {
    "ww_workspace_trade_disclosure_calendar": (
        workspace_tools.workspace_trade_disclosure_calendar_impl,
        strategic_live,
        "query_trade_disclosure_calendar",
        "research.trade_disclosure_calendar.live",
        {},
    ),
    "ww_workspace_tw_monthly_revenue": (
        workspace_tools.workspace_tw_monthly_revenue_impl,
        strategic_live,
        "query_tw_monthly_revenue",
        "research.tw_monthly_revenue.live",
        {"company_ids": ["2330"]},
    ),
    "ww_workspace_cn_commodity_futures": (
        workspace_tools.workspace_cn_commodity_futures_impl,
        strategic_live,
        "query_cn_commodity_futures",
        "research.cn_commodity_futures.live",
        {"symbols": ["RB0"]},
    ),
    "ww_workspace_control_lists": (
        workspace_tools.workspace_control_lists_impl,
        strategic_live,
        "query_control_lists",
        "research.control_lists.live",
        {"query": "example entity"},
    ),
    "ww_workspace_sec_filings_xbrl": (
        workspace_tools.workspace_sec_filings_xbrl_impl,
        global_markets_live,
        "query_sec_filings_xbrl",
        "research.sec_filings_xbrl.live",
        {"ticker": "AAPL"},
    ),
    "ww_workspace_finra_short_volume": (
        workspace_tools.workspace_finra_short_volume_impl,
        global_markets_live,
        "query_finra_short_volume",
        "research.finra_short_volume.live",
        {"symbols": ["AAPL"]},
    ),
    "ww_workspace_us_treasury": (
        workspace_tools.workspace_us_treasury_impl,
        global_markets_live,
        "query_us_treasury",
        "research.us_treasury.live",
        {},
    ),
    "ww_workspace_cftc_cot": (
        workspace_tools.workspace_cftc_cot_impl,
        global_markets_live,
        "query_cftc_cot",
        "research.cftc_cot.live",
        {"contract_codes": ["043602"]},
    ),
    "ww_workspace_overseas_earnings_calendar": (
        workspace_tools.workspace_overseas_earnings_calendar_impl,
        global_markets_live,
        "query_overseas_earnings_calendar",
        "research.overseas_earnings_calendar.live",
        {"symbols": ["AAPL"]},
    ),
    "ww_workspace_newsradar": (
        workspace_tools.workspace_newsradar_impl,
        strategic_live,
        "query_newsradar_live",
        "research.newsradar.live",
        {"sectors": ["ai"]},
    ),
}


def _world(tmp_path: Path):
    store = ConsoleStore(root=tmp_path)
    first = store.workspace_store.create({
        "kind": "stock",
        "code": "SH600667",
        "name": "first",
        "display_name": "first",
        "root_mode": "managed",
        "agent_mode": "research",
    })
    second = store.workspace_store.create({
        "kind": "stock",
        "code": "SZ300750",
        "name": "second",
        "display_name": "second",
        "root_mode": "managed",
        "agent_mode": "research",
    })
    first_sid = store.create_session(workspace_id=first["id"])["id"]
    second_sid = store.create_session(workspace_id=second["id"])["id"]
    return store, first, first_sid, second, second_sid


def _invoke_bound(store, sid, impl, **kwargs):
    token = workspace_tools.CTX_WORKSPACE.set(
        workspace_tools.bind_workspace_context(store, sid)
    )
    try:
        return impl(**kwargs)
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def _snapshot(dataset_id, query):
    return {
        "ok": True,
        "dataset_id": dataset_id,
        "status": "ok",
        "query": dict(query),
        "rows": [{"marker": dataset_id, "value": 7}],
        "sources": [{
            "authority": "fixture authority",
            "url": "https://example.invalid/source",
            "retrieved_at": "2026-08-29T01:02:03Z",
            "status": "ok",
        }],
        "pulled_at": "2026-08-29T01:02:03Z",
        "as_of": "2026-08-29",
        "warnings": [],
    }


def test_control_wrapper_preserves_default_source_selection(monkeypatch):
    monkeypatch.setattr(workspace_tools, "_live_snapshot_result", lambda fetcher, **kwargs: kwargs)
    result = workspace_tools.workspace_control_lists_impl(query="Example Entity")
    assert result["fetch_args"]["jurisdictions"] is None
    assert result["fetch_args"]["list_types"] is None
    explicit = workspace_tools.workspace_control_lists_impl(
        query="Example Entity", jurisdictions=["us"], list_types=["sanctions"],
    )
    assert explicit["fetch_args"]["jurisdictions"] == ["us"]
    assert explicit["fetch_args"]["list_types"] == ["sanctions"]


def test_public_asset_projection_exposes_safe_snapshot_status_not_query_or_body(monkeypatch):
    from types import SimpleNamespace
    from guanlan_v2.workflow import store as workflow_store
    from guanlan_v2.reports import api as report_api

    item = {
        "id": "wa_example", "kind": "data", "dataset_id": "research.control_lists.live",
        "upstream_status": "unavailable", "record_count": 0,
        "pulled_at": "2026-09-09T08:09:25Z", "as_of": "2026-09-09",
        "query": {"private": "query"}, "body": "private body", "rows": [{"private": True}],
        "source_availability": [{"id": "bis_entity", "label": "private label", "status": "error",
                                 "reason_code": "HTTP_403", "url": "private url"}] * 18,
    }
    monkeypatch.setattr(workspace_tools, "list_workspace_assets", lambda *args, **kwargs: [item])
    monkeypatch.setattr(workflow_store, "WorkflowStore", lambda: SimpleNamespace(list=lambda **kwargs: []))
    monkeypatch.setattr(report_api, "list_report_summaries", lambda *args, **kwargs: [])
    rows = workspace_tools.public_asset_rows(object(), "rw_example")
    assert len(rows) == 1
    for key in ("upstream_status", "record_count", "pulled_at", "as_of"):
        assert rows[0][key] == item[key]
    assert not {"query", "body", "rows"} & rows[0].keys()
    assert len(rows[0]["source_availability"]) == 16
    assert rows[0]["source_availability"][0] == {
        "id": "bis_entity", "label": "BIS 实体清单", "status": "error", "reason_code": "HTTP_403",
    }
    assert "private" not in str(rows[0])


def test_control_snapshot_metadata_records_only_safe_actual_source_status(tmp_path, monkeypatch):
    store, _workspace, sid, _other, _other_sid = _world(tmp_path)
    def fetch(**query):
        result = _snapshot("research.control_lists.live", query)
        result.update(ok=False, status="unavailable", rows=[], sources=[{
            "url": strategic_live.BIS_ENTITY_URL, "authority": "private supplied label",
            "status": "error", "http_status": 403, "error": "private error",
        }, {
            "url": strategic_live.MOFCOM_POLICY_URL, "status": "error", "error_type": "ConnectError",
        }, {
            "url": strategic_live.CN_MARKET_ACCESS_NEGATIVE_LIST_URL, "status": "reference_only",
        }])
        return result
    monkeypatch.setattr(strategic_live, "query_control_lists", fetch)
    result = _invoke_bound(store, sid, workspace_tools.workspace_control_lists_impl, query="Example")
    assert result["asset"]["source_availability"] == [
        {"id": "bis_entity", "label": "BIS 实体清单", "status": "error", "reason_code": "HTTP_403"},
        {"id": "mofcom_policy", "label": "商务部安全与管制政策", "status": "error", "reason_code": "ConnectError"},
        {"id": "cn_market_access", "label": "中国市场准入负面清单", "status": "reference_only", "reason_code": "REFERENCE_ONLY"},
    ]
    assert "private" not in str(result["asset"]["source_availability"])


def test_old_or_other_dataset_asset_has_no_synthetic_source_availability(monkeypatch):
    from types import SimpleNamespace
    from guanlan_v2.workflow import store as workflow_store
    from guanlan_v2.reports import api as report_api
    items = [{"id": "wa_old", "dataset_id": "research.control_lists.live"},
             {"id": "wa_other", "dataset_id": "other", "source_availability": [{"id": "bis_entity"}]}]
    monkeypatch.setattr(workspace_tools, "list_workspace_assets", lambda *args, **kwargs: items)
    monkeypatch.setattr(workflow_store, "WorkflowStore", lambda: SimpleNamespace(list=lambda **kwargs: []))
    monkeypatch.setattr(report_api, "list_report_summaries", lambda *args, **kwargs: [])
    rows = workspace_tools.public_asset_rows(object(), "rw_example")
    assert all("source_availability" not in row for row in rows)


def test_stock_calendar_is_bound_to_current_workspace_subject(tmp_path, monkeypatch):
    store, workspace, sid, _other, _other_sid = _world(tmp_path)
    seen = {}

    def fake_query(**kwargs):
        seen.update(kwargs)
        return _snapshot("research.trade_disclosure_calendar.live", kwargs)

    monkeypatch.setattr(
        strategic_live, "query_trade_disclosure_calendar", fake_query,
    )
    result = _invoke_bound(
        store, sid, workspace_tools.workspace_trade_disclosure_calendar_impl,
        stock_code="600667",
    )
    assert result["ok"] is True
    assert seen["stock_code"] == workspace["code"]

    with pytest.raises(workspace_tools.WorkspaceToolError, match="当前空间标的"):
        _invoke_bound(
            store, sid, workspace_tools.workspace_trade_disclosure_calendar_impl,
            stock_code="SZ300750",
        )


def test_live_tools_are_registered_as_workspace_scoped_ungated_writes():
    console_by_name = {row["name"]: row for row in console_tools.WW_TOOL_TABLE}
    mcp_by_name = {row["name"]: row for row in build_mcp_tools()}

    assert LIVE_TOOLS.keys() <= workspace_tools.ALL_WORKSPACE_TOOLS
    for name in LIVE_TOOLS:
        declared = console_by_name[name]
        assert declared["impl"] is LIVE_TOOLS[name][0]
        assert declared["confirm"] is False
        assert declared["cost"] == "seconds"
        assert declared["reachable"] == []

        exposed = mcp_by_name[name]
        assert exposed["read_only"] is False
        assert exposed["destructive"] is False
        assert exposed["gated"] is False
        assert "workspace_id" in exposed["inputSchema"]["required"]
        workspace_schema = exposed["inputSchema"]["properties"]["workspace_id"]
        assert workspace_schema["pattern"] == r"^rw_(?:general|[0-9a-f]{12})$"


def test_quant_chart_is_fixed_input_and_returns_private_media_artifact(
        tmp_path, monkeypatch):
    from PIL import Image
    from guanlan_v2.console.media import MediaStore
    from guanlan_v2.console import quant_chart

    store, workspace, sid, _other, _other_sid = _world(tmp_path)
    declaration = {
        row["name"]: row for row in console_tools.WW_TOOL_TABLE
    }["ww_workspace_quant_chart"]
    assert set(declaration["input_schema"]["properties"]) == {"benchmark", "asof"}
    assert declaration["confirm"] is False

    seen = {}

    def fake_build(subject, code, *, output_dir, benchmark, windows,
                   reference_date):
        output = Path(output_dir)
        seen.update({
            "subject": subject, "code": code, "output": output,
            "benchmark": benchmark, "windows": tuple(windows),
            "reference_date": reference_date,
        })
        Image.new("RGB", (32, 24), "white").save(output / "fixed.png")
        (output / "fixed.json").write_text("{}", encoding="utf-8")
        return {
            "artifacts": {"png": "fixed.png", "json": "fixed.json"},
            "freshness": {"warnings": []},
            "current_ready": True,
            "position_price": 10.0,
            "position_asof": "2026-08-29",
            "analysis_asof": "2026-08-29",
            "benchmark_asof": "2026-08-29",
            "common_cutoff": "2026-08-29",
            "position_levels": {"supports": [], "resistances": []},
            "regressions": [],
        }

    monkeypatch.setattr(quant_chart, "build_workspace_quant_chart", fake_build)
    result = _invoke_bound(
        store, sid, workspace_tools.workspace_quant_chart_impl,
        benchmark="csi300", asof="2026-08-29",
    )

    assert result["ok"] is True
    assert seen["code"] == workspace["code"]
    assert seen["benchmark"] == "csi300"
    assert seen["windows"] == (20, 60, 120)
    root = workspace_tools._root_for(store, workspace["id"])
    seen["output"].resolve().relative_to(root.resolve())
    assert seen["output"].parent == root / "output"
    assert seen["output"].name.startswith("quant_")

    artifact = result["artifact"]
    assert artifact["kind"] == "media_image"
    payload = artifact["payload"]
    assert set(payload) == {
        "media_id", "name", "mime", "width", "height", "alt",
        "workspace_id",
    }
    assert payload["workspace_id"] == workspace["id"]
    assert payload["mime"] == "image/png"
    metadata, data = MediaStore(store).load(payload["media_id"])
    assert metadata["session_id"] == sid
    assert metadata["workspace_id"] == workspace["id"]
    assert metadata["size"] == len(data) <= 10 * 1024 * 1024


@pytest.mark.parametrize(
    ("tool_name", "case"),
    list(LIVE_TOOLS.items()),
    ids=list(LIVE_TOOLS),
)
def test_each_live_tool_saves_separate_metadata_and_payload(
        tmp_path, monkeypatch, tool_name, case):
    impl, source_module, fetch_name, dataset_id, call_args = case
    store, workspace, sid, _other, _other_sid = _world(tmp_path)
    seen = {}

    def fake_fetch(**query):
        seen.update(query)
        return _snapshot(dataset_id, query)

    monkeypatch.setattr(source_module, fetch_name, fake_fetch)
    result = _invoke_bound(store, sid, impl, **call_args)

    assert result["ok"] is True
    assert result["dataset_id"] == dataset_id
    assert result["rows"] == [{"marker": dataset_id, "value": 7}]
    assert result["asset"]["workspace_id"] == workspace["id"]
    assert result["asset"]["kind"] == "data"
    assert result["asset"]["source"] == tool_name
    assert result["asset"]["record_count"] == 1
    assert result["asset"]["query"] == seen
    assert result["artifact"]["payload"]["workspace_id"] == workspace["id"]
    assert "未写入 stocks" in result["content"]

    asset_id = result["asset"]["id"]
    metadata_path = (
        tmp_path / "workspace_assets" / workspace["id"] / f"{asset_id}.json"
    )
    payload_path = (
        tmp_path / "workspace_asset_payloads" / workspace["id"]
        / f"{asset_id}.json"
    )
    assert metadata_path.is_file()
    assert payload_path.is_file()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert metadata["payload_sha256"] == result["asset"]["payload_sha256"]
    assert payload["asset_id"] == asset_id
    assert payload["workspace_id"] == workspace["id"]
    assert payload["dataset_id"] == dataset_id
    assert payload["snapshot"]["rows"] == result["rows"]


def test_workspace_snapshot_writer_uses_external_hmac_when_configured(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-workspace-pit-hmac-key-material-32-bytes-minimum",
    )
    store, workspace, sid, _other, _other_sid = _world(tmp_path)
    dataset_id = "research.cn_commodity_futures.live"
    monkeypatch.setattr(
        strategic_live,
        "query_cn_commodity_futures",
        lambda **query: _snapshot(dataset_id, query),
    )
    result = _invoke_bound(
        store,
        sid,
        workspace_tools.workspace_cn_commodity_futures_impl,
        symbols=["CU0"],
    )
    asset = result["asset"]
    auth_payload = workspace_snapshot_auth_payload(
        asset_id=asset["id"],
        workspace_id=workspace["id"],
        dataset_id=asset["dataset_id"],
        payload_sha256=asset["payload_sha256"],
        acquired_at=asset["acquired_at"],
        pulled_at=asset["pulled_at"],
        as_of=asset["as_of"],
        source=asset["source"],
        subject=asset["subject"],
        asset_version=asset["version"],
    )
    assert verify_payload(
        WORKSPACE_SNAPSHOT_DOMAIN, auth_payload, asset["snapshot_auth"],
    ) is True


def test_workspace_acquisition_clock_is_hong_kong_aware_on_non_hk_host(
    tmp_path, monkeypatch,
):
    from guanlan_v2.research.company_workspace import _historical_snapshot_reason

    instant = datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc)

    class NonHongKongHostDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(workspace_tools, "datetime", NonHongKongHostDateTime)
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-workspace-pit-hmac-key-material-32-bytes-minimum",
    )
    store, workspace, _sid, _other, _other_sid = _world(tmp_path)
    snapshot = {
        "ok": True,
        "dataset_id": "research.trade_disclosure_calendar.live",
        "status": "ok",
        "query": {"stock_code": workspace["code"]},
        "rows": [{"date": "2026-08-29", "value": "provider-before-cutoff"}],
        "sources": [{"authority": "fixture", "status": "ok"}],
        "pulled_at": "2026-08-29T15:00:00Z",
        "as_of": "2026-08-29",
        "warnings": [],
    }
    asset = workspace_tools.save_workspace_data_snapshot(
        store, workspace["id"], workspace,
        dataset_id=snapshot["dataset_id"], title="calendar",
        query=snapshot["query"], snapshot=snapshot,
        source_tool="ww_workspace_trade_disclosure_calendar",
    )
    record = {
        "asset": asset,
        "snapshot": workspace_tools.get_workspace_data_snapshot(
            store, workspace["id"], asset["id"],
        ),
    }

    assert workspace_tools._hk_today() == "2026-08-30"
    assert asset["acquired_at"] == "2026-08-30T04:00:00+08:00"
    assert _historical_snapshot_reason(
        record, requested_asof="2026-08-29", expected_code=workspace["code"],
    ) == "workspace snapshot was not acquired at/before requested asof"


def test_asset_get_replays_only_for_owner_and_rejects_digest_tampering(
        tmp_path, monkeypatch):
    store, workspace, sid, _other, other_sid = _world(tmp_path)
    dataset_id = "research.cn_commodity_futures.live"

    def fake_fetch(**query):
        return _snapshot(dataset_id, query)

    monkeypatch.setattr(
        strategic_live, "query_cn_commodity_futures", fake_fetch,
    )
    saved = _invoke_bound(
        store,
        sid,
        workspace_tools.workspace_cn_commodity_futures_impl,
        symbols=["CU0"],
    )
    asset_id = saved["asset"]["id"]

    replayed = _invoke_bound(
        store, sid, workspace_tools.workspace_asset_get_impl, asset_id=asset_id,
    )
    assert replayed["ok"] is True
    assert replayed["snapshot"]["rows"] == saved["rows"]
    assert replayed["artifact"]["kind"] == "workspace_data"
    assert replayed["artifact"]["ref"] == asset_id

    cross_workspace = _invoke_bound(
        store, other_sid, workspace_tools.workspace_asset_get_impl, asset_id=asset_id,
    )
    assert cross_workspace["ok"] is False
    assert "不存在" in cross_workspace["content"]

    payload_path = (
        tmp_path / "workspace_asset_payloads" / workspace["id"]
        / f"{asset_id}.json"
    )
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["snapshot"]["rows"][0]["value"] = 999
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8",
    )

    tampered = _invoke_bound(
        store, sid, workspace_tools.workspace_asset_get_impl, asset_id=asset_id,
    )
    assert tampered["ok"] is False
    assert "完整性校验失败" in tampered["content"]


def test_gl_mcp_dispatch_binds_explicit_workspace_and_persists_snapshot(
        tmp_path, monkeypatch):
    from guanlan_v2.glmcp.server import dispatch_tool

    store, workspace, _sid, _other, _other_sid = _world(tmp_path)
    dataset_id = "research.cn_commodity_futures.live"

    def fake_fetch(**query):
        return _snapshot(dataset_id, query)

    monkeypatch.setattr(
        strategic_live, "query_cn_commodity_futures", fake_fetch,
    )
    result = asyncio.run(dispatch_tool(
        "ww_workspace_cn_commodity_futures",
        {"workspace_id": workspace["id"], "symbols": ["RB0"]},
        console_store=store,
    ))
    assert len(result) == 1
    assert "RB0" in result[0].text
    assert "已自动保存到当前研究空间" in result[0].text
    assets = workspace_tools.list_workspace_assets(store, workspace["id"])
    assert len(assets) == 1
    assert assets[0]["dataset_id"] == dataset_id


def test_diagnostic_lists_surface_source_health_but_cannot_enter_research(tmp_path):
    store, workspace, _sid, _other, _other_sid = _world(tmp_path)
    dataset = "diagnostic.control_lists"
    snapshot = _snapshot(dataset, {})
    snapshot["sources"] = [{"url": strategic_live.OFAC_SDN_URL, "status": "error", "http_status": 302}]
    asset = workspace_tools.save_workspace_data_snapshot(
        store, workspace["id"], workspace, dataset_id=dataset,
        title="连通性摘录（非研究证据）", query={}, snapshot=snapshot,
        source_tool="ww_workspace_control_lists",
    )
    rows = workspace_tools.public_asset_rows(store, workspace["id"])
    row = next(item for item in rows if item["id"] == asset["id"])
    assert row["source_availability"][0]["reason_code"] == "HTTP_302"
    with pytest.raises(workspace_tools.WorkspaceToolError, match="不是研究证据"):
        workspace_tools._verified_snapshot_record(store, workspace["id"], "SH600667", asset["id"])


def test_invalid_live_query_does_not_create_workspace_asset(tmp_path, monkeypatch):
    store, workspace, sid, _other, _other_sid = _world(tmp_path)

    def invalid_fetch(**query):
        return {
            "ok": False,
            "dataset_id": "research.cn_commodity_futures.live",
            "status": "invalid",
            "query": query,
            "rows": [],
            "sources": [],
            "pulled_at": "2026-08-29T01:02:03Z",
            "as_of": "",
            "warnings": ["symbol 非法"],
        }

    monkeypatch.setattr(
        strategic_live, "query_cn_commodity_futures", invalid_fetch,
    )
    result = _invoke_bound(
        store,
        sid,
        workspace_tools.workspace_cn_commodity_futures_impl,
        symbols=["bad"],
    )
    assert result["ok"] is False and result["status"] == "invalid"
    assert "未创建工作空间快照" in result["content"]
    assert workspace_tools.list_workspace_assets(store, workspace["id"]) == []
