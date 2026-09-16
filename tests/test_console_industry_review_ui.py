# -*- coding: utf-8 -*-
"""Research Dock industry-news batch review surface guards."""
from pathlib import Path

from datetime import date
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console import workspace_tools
from guanlan_v2.research.company_industry_news import annotate_newsradar_snapshot


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "console"


def test_research_dock_exposes_multi_select_decisions_and_cas_conflict():
    dock = (UI / "console-workspace-dock.jsx").read_text(encoding="utf-8")

    assert "function WwIndustryReviewPanel" in dock
    assert 'type="checkbox"' in dock
    assert "[['related', '相关'], ['unrelated', '不相关'], ['uncertain', '不确定']]" in dock
    assert "expected_revision: view.revision" in dock
    assert "confirmed: true" in dock
    assert "industry_review_revision_conflict" in dock
    assert "审核版本冲突" in dock
    assert 'data-review-conflict={notice.conflict ? \'true\' : \'false\'}' in dock
    assert "始终不会升级为公司事实" in dock


def test_dock_api_client_uses_workspace_scoped_review_routes():
    data = (UI / "console-data.jsx").read_text(encoding="utf-8")
    assert "async function wwWorkspaceIndustryReviews" in data
    assert "async function wwWorkspaceIndustryReviewSubmit" in data
    assert "'/industry-reviews?'" in data
    assert "'/industry-reviews'" in data
    assert "workspaceIndustryReviews: wwWorkspaceIndustryReviews" in data
    assert "workspaceIndustryReviewSubmit: wwWorkspaceIndustryReviewSubmit" in data


def test_research_asset_projection_exposes_only_dataset_identifier_needed_for_ui():
    source = (ROOT / "guanlan_v2" / "console" / "workspace_tools.py").read_text(
        encoding="utf-8"
    )
    assert '"dataset_id",' in source
    assert "def public_asset_rows" in source


def test_dock_review_route_batches_and_surfaces_revision_conflict(tmp_path):
    store = ConsoleStore(root=tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock", "code": "SH603986", "name": "兆易创新",
        "display_name": "兆易创新", "root_mode": "managed", "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    today = date.today().isoformat()
    snapshot = annotate_newsradar_snapshot({
        "ok": True, "dataset_id": "research.newsradar.live", "status": "ok",
        "query": {"sectors": ["semi"]}, "rows": [{
            "title": "Foundry update", "source": "Example",
            "url": "https://example.test/foundry", "sector": "semi",
            "source_time": f"{today}T09:00:00+08:00",
        }], "sources": [{"authority": "Example", "status": "ok"}],
        "pulled_at": f"{today}T09:01:00+08:00", "as_of": today, "warnings": [],
    }, {
        "schema_version": "company-industry-news-context@1", "status": "ready",
        "subject_code": "SH603986", "subject_name": "兆易创新", "industry": "半导体",
        "radar_sectors": ["semi"], "research_scope": "industry_context",
        "relation_status": "unreviewed", "user_judgement_required": True,
        "may_close_issuer_gate": False,
    })
    asset = workspace_tools.save_workspace_data_snapshot(
        store, workspace["id"], workspace,
        dataset_id="research.newsradar.live", title="semi candidates",
        query={"sectors": ["semi"]}, snapshot=snapshot,
        source_tool="ww_workspace_newsradar",
    )
    app = FastAPI()
    app.include_router(build_console_router(store=store, agent_factory=lambda _sid: None))
    client = TestClient(app)
    params = {"session_id": sid, "snapshot_asset_id": asset["id"]}

    listed = client.get(
        f"/console/workspaces/{workspace['id']}/industry-reviews", params=params,
    )
    assert listed.status_code == 200
    payload = listed.json()
    candidate_id = payload["candidates"][0]["candidate_id"]
    assert payload["batch_review"]["expected_revision"] == 0

    body = {
        **params, "expected_revision": 0, "idempotency_key": "dock-route-review-1",
        "reviews": [{"candidate_id": candidate_id, "decision": "related"}],
        "confirmed": True,
    }
    saved = client.post(
        f"/console/workspaces/{workspace['id']}/industry-reviews", json=body,
    )
    assert saved.status_code == 200
    assert saved.json()["review_state"]["revision"] == 1
    assert saved.json()["reviews"][0]["issuer_fact"] is False

    stale = dict(body)
    stale["idempotency_key"] = "dock-route-review-2"
    stale["reviews"] = [{"candidate_id": candidate_id, "decision": "unrelated"}]
    conflict = client.post(
        f"/console/workspaces/{workspace['id']}/industry-reviews", json=stale,
    )
    assert conflict.status_code == 409
    assert conflict.json()["reason_code"] == "industry_review_revision_conflict"
