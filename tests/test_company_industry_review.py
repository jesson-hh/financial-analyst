from __future__ import annotations

import json
import copy
from datetime import date
from pathlib import Path

import pytest

from guanlan_v2.console import workspace_tools
from guanlan_v2.console.industry_review import (
    IndustryReviewConflict,
    append_review,
    append_reviews,
    review_state,
)
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.research.company_industry_news import (
    annotate_newsradar_snapshot,
    newsradar_report_projection_is_valid,
    project_reviewed_newsradar_snapshot,
)
from guanlan_v2.research.company_workspace import normalise_company_events
from guanlan_v2.research.company_report_gate import _source_inventory


def _snapshot():
    today = date.today().isoformat()
    return annotate_newsradar_snapshot(
        {
            "ok": True,
            "dataset_id": "research.newsradar.live",
            "status": "ok",
            "query": {"sectors": ["semi"]},
            "rows": [{
                "title": "Foundry capacity update",
                "source": "Example",
                "url": "https://example.test/foundry",
                "sector": "semi",
                "source_time": f"{today}T09:00:00+08:00",
            }],
            "sources": [{"authority": "Example", "status": "ok"}],
            "pulled_at": f"{today}T09:01:00+08:00",
            "as_of": today,
            "warnings": [],
        },
        {
            "schema_version": "company-industry-news-context@1",
            "status": "ready",
            "subject_code": "SH603986",
            "subject_name": "兆易创新",
            "industry": "半导体",
            "radar_sectors": ["semi"],
            "research_scope": "industry_context",
            "relation_status": "unreviewed",
            "user_judgement_required": True,
            "may_close_issuer_gate": False,
        },
    )


def _snapshot_two():
    snapshot = _snapshot()
    snapshot["rows"].append({
        "title": "Advanced packaging demand update",
        "source": "Example Two",
        "url": "https://example.test/packaging",
        "sector": "semi",
        "source_time": f"{date.today().isoformat()}T10:00:00+08:00",
    })
    return annotate_newsradar_snapshot(snapshot, snapshot["industry_context"])


def _world(tmp_path: Path, snapshot=None):
    store = ConsoleStore(root=tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock", "code": "SH603986", "name": "兆易创新",
        "display_name": "兆易创新", "root_mode": "managed",
        "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    token = workspace_tools.CTX_WORKSPACE.set(
        workspace_tools.bind_workspace_context(store, sid)
    )
    asset = workspace_tools.save_workspace_data_snapshot(
        store, workspace["id"], workspace,
        dataset_id="research.newsradar.live",
        title="semi candidates",
        query={"sectors": ["semi"]},
        snapshot=snapshot or _snapshot(),
        source_tool="ww_workspace_newsradar",
    )
    return store, workspace, sid, token, asset


def test_atomic_batch_review_is_idempotent_and_all_or_nothing(tmp_path):
    snapshot = _snapshot_two()
    candidate_ids = [
        row["candidate_id"]
        for row in snapshot["industry_candidate_relations"]["rows"]
    ]
    reviews = [
        {"candidate_id": candidate_ids[0], "decision": "related", "note": "供给侧"},
        {"candidate_id": candidate_ids[1], "decision": "uncertain", "note": "待核验"},
    ]
    created = append_reviews(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        reviews=reviews, expected_revision=0,
        idempotency_key="batch-review-0001", reviewer_session="sid-private",
    )
    assert created["created"] is True
    assert [entry["revision"] for entry in created["entries"]] == [1, 2]
    assert created["state"]["revision"] == 2
    assert all(entry["issuer_fact"] is False for entry in created["entries"])

    replay = append_reviews(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        reviews=reviews, expected_revision=0,
        idempotency_key="batch-review-0001", reviewer_session="sid-private",
    )
    assert replay["created"] is False and replay["idempotent_replay"] is True
    assert [entry["entry_sha256"] for entry in replay["entries"]] == [
        entry["entry_sha256"] for entry in created["entries"]
    ]

    clean_root = tmp_path / "clean"
    with pytest.raises(Exception, match="candidate_id"):
        append_reviews(
            clean_root, "rw_123456789abc", "SH603986",
            snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
            reviews=[reviews[0], {"candidate_id": "INC-0000000000000000",
                                  "decision": "related"}],
            expected_revision=0, idempotency_key="batch-invalid-001",
            reviewer_session="sid-private",
        )
    assert not (clean_root / "workspace_industry_reviews").exists()


def test_workspace_batch_review_contract_and_projection(tmp_path):
    snapshot = _snapshot_two()
    store, _workspace, _sid, token, asset = _world(tmp_path, snapshot)
    try:
        listed = workspace_tools.workspace_industry_review_list_impl(asset["id"])
        assert listed["batch_review"] == {
            "supported": True, "max_items": 100, "expected_revision": 0,
            "decisions": ["related", "unrelated", "uncertain"],
            "atomic": True, "issuer_fact": False,
        }
        ids = [row["candidate_id"] for row in listed["candidates"]]
        result = workspace_tools.workspace_industry_review_impl(
            asset["id"], expected_revision=0,
            idempotency_key="workspace-batch-001",
            reviews=[
                {"candidate_id": ids[0], "decision": "related"},
                {"candidate_id": ids[1], "decision": "unrelated"},
            ],
        )
        assert result["ok"] is True and result["created"] is True
        assert len(result["reviews"]) == 2
        assert result["review_state"]["revision"] == 2
        listed_again = workspace_tools.workspace_industry_review_list_impl(asset["id"])
        assert listed_again["counts"] == {"related": 1, "unrelated": 1}
        assert sum(bool(row["report_eligible"]) for row in listed_again["candidates"]) == 1
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_projection_excludes_every_unreviewed_candidate():
    snapshot = _snapshot()
    projected = project_reviewed_newsradar_snapshot(snapshot, {
        "revision": 0, "head_sha256": "0" * 64, "decisions": [],
        "subject_code": "SH603986",
    }, snapshot_asset_id="wa_1234567890abcdef")

    assert snapshot["rows"]  # raw provider snapshot was not edited
    assert projected["rows"] == []
    assert projected["industry_review_gate"]["status"] == "awaiting_review"
    assert projected["industry_review_gate"]["candidate_counts"]["unreviewed"] == 1
    assert newsradar_report_projection_is_valid(projected) is False
    assert normalise_company_events(
        {}, [{"snapshot": projected}], subject_code="SH603986",
    ) == []


def test_hash_chain_cas_idempotency_and_related_projection(tmp_path):
    snapshot = _snapshot()
    candidate_id = snapshot["industry_candidate_relations"]["rows"][0]["candidate_id"]
    created = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="review-related-0001", reviewer_session="sid-private",
    )
    assert created["created"] is True
    assert created["entry"]["revision"] == 1
    assert created["entry"]["issuer_fact"] is False
    assert "sid-private" not in str(created)

    replay = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="review-related-0001", reviewer_session="sid-private",
    )
    assert replay["created"] is False
    assert replay["idempotent_replay"] is True
    assert replay["entry"]["entry_sha256"] == created["entry"]["entry_sha256"]

    with pytest.raises(IndustryReviewConflict):
        append_review(
            tmp_path, "rw_123456789abc", "SH603986",
            snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
            candidate_id=candidate_id, decision="unrelated", expected_revision=0,
            idempotency_key="review-unrelated-02", reviewer_session="sid-private",
        )

    state = review_state(tmp_path, "rw_123456789abc", "SH603986")
    projected = project_reviewed_newsradar_snapshot(
        snapshot, state, snapshot_asset_id="wa_1234567890abcdef",
    )
    assert len(projected["rows"]) == 1
    relation = projected["industry_candidate_relations"]["rows"][0]
    assert relation["relation_status"] == "related"
    assert relation["issuer_fact"] is False
    assert relation["report_eligible"] is True
    assert newsradar_report_projection_is_valid(projected) is True
    events = normalise_company_events(
        {}, [{"snapshot": projected}], subject_code="SH603986",
    )
    assert len(events) == 1
    assert events[0]["kind"] == "industry_signal_related_context"
    assert events[0]["subject_code"] is None
    assert events[0]["payload"]["issuer_fact"] is False


def test_latest_unrelated_and_uncertain_decisions_stay_out_of_report(tmp_path):
    snapshot = _snapshot()
    candidate_id = snapshot["industry_candidate_relations"]["rows"][0]["candidate_id"]
    first = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="decision-related-01", reviewer_session="sid",
    )
    second = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="unrelated", expected_revision=1,
        idempotency_key="decision-unrelated-01", reviewer_session="sid",
    )
    assert first["state"]["decisions"][0]["decision"] == "related"
    unrelated = project_reviewed_newsradar_snapshot(
        snapshot, second["state"], snapshot_asset_id="wa_1234567890abcdef",
    )
    assert unrelated["rows"] == []
    assert unrelated["industry_review_gate"]["candidate_counts"]["unrelated"] == 1

    third = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="uncertain", expected_revision=2,
        idempotency_key="decision-uncertain-01", reviewer_session="sid",
    )
    uncertain = project_reviewed_newsradar_snapshot(
        snapshot, third["state"], snapshot_asset_id="wa_1234567890abcdef",
    )
    assert uncertain["rows"] == []
    assert uncertain["industry_review_gate"]["status"] == "awaiting_review"
    assert uncertain["industry_review_gate"]["candidate_counts"]["uncertain"] == 1


def test_report_projection_integrity_rejects_forged_review_or_digest(tmp_path):
    snapshot = _snapshot()
    candidate_id = snapshot["industry_candidate_relations"]["rows"][0]["candidate_id"]
    reviewed = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="projection-integrity-01", reviewer_session="sid",
    )
    projected = project_reviewed_newsradar_snapshot(
        snapshot, reviewed["state"], snapshot_asset_id="wa_1234567890abcdef",
    )
    assert newsradar_report_projection_is_valid(projected) is True

    forged = copy.deepcopy(projected)
    forged["industry_candidate_relations"]["rows"][0]["review"][
        "decision"
    ] = "uncertain"
    assert newsradar_report_projection_is_valid(forged) is False

    digest_tampered = copy.deepcopy(projected)
    digest_tampered["industry_review_gate"]["candidate_counts"]["unreviewed"] = 9
    assert newsradar_report_projection_is_valid(digest_tampered) is False

    wrong_asset = project_reviewed_newsradar_snapshot(
        snapshot, reviewed["state"], snapshot_asset_id="wa_fedcba0987654321",
    )
    assert wrong_asset["rows"] == []
    assert wrong_asset["industry_review_gate"]["candidate_counts"]["unreviewed"] == 1

    wrong_company_state = copy.deepcopy(reviewed["state"])
    wrong_company_state["subject_code"] = "SZ000001"
    wrong_company = project_reviewed_newsradar_snapshot(
        snapshot, wrong_company_state,
        snapshot_asset_id="wa_1234567890abcdef",
    )
    assert wrong_company["rows"] == []


def test_workspace_tools_list_review_and_keep_scope_private(tmp_path, monkeypatch):
    store, workspace, _sid, token, asset = _world(tmp_path)
    try:
        listed = workspace_tools.workspace_industry_review_list_impl(asset["id"])
        assert listed["ok"] is True
        assert listed["review_state"]["revision"] == 0
        candidate = listed["candidates"][0]
        assert candidate["decision"] == "unreviewed"

        reviewed = workspace_tools.workspace_industry_review_impl(
            asset["id"], candidate["candidate_id"], "related", 0,
            "workspace-review-01", "与半导体行业供给相关",
        )
        assert reviewed["ok"] is True
        assert reviewed["review_state"]["revision"] == 1
        assert reviewed["review"]["issuer_fact"] is False

        listed_again = workspace_tools.workspace_industry_review_list_impl(asset["id"])
        assert listed_again["candidates"][0]["decision"] == "related"
        assert listed_again["candidates"][0]["report_eligible"] is True

        replay = workspace_tools.workspace_asset_get_impl(asset["id"])
        assert replay["ok"] is True
        assert replay["industry_review_state"]["revision"] == 1
        assert replay["snapshot"]["rows"]  # immutable raw candidate remains visible
        assert replay["snapshot"]["industry_review_state"]["decisions"][0][
            "decision"
        ] == "related"
        assert not (tmp_path / "events" / "events.db").exists()
        assert (tmp_path / "workspace_industry_reviews" / workspace["id"] /
                "ledger.json").is_file()

        monkeypatch.setattr(
            workspace_tools, "_build_company_pack",
            lambda code, asof="": ({
                "schema_version": "company-research-evidence@2",
                "code": code, "generated_at": asof,
                "sections": {}, "coverage": {}, "errors": {},
            }, {"ok": True, "path": None}),
        )
        prepared = workspace_tools.workspace_company_research_prepare_impl(
            asof=date.today().isoformat(), refresh_calendar=False,
            include_newsradar=False, include_latest_snapshots=True,
        )
        assert prepared["ok"] is True
        run = Path(workspace["root_path"], prepared["asset"]["run_rel"])
        events = json.loads((run / "events.json").read_text(encoding="utf-8"))
        related = [
            row for row in events
            if row.get("kind") == "industry_signal_related_context"
        ]
        assert len(related) == 1
        assert related[0]["payload"]["candidate_id"] == candidate["candidate_id"]
        assert related[0]["payload"]["issuer_fact"] is False
        engine = json.loads(
            (run / "engine_evidence_pack.json").read_text(encoding="utf-8")
        )
        report_snapshot = engine["sections"]["workspace_live"]["datasets"][
            "research.newsradar.live"
        ]["snapshot"]
        assert len(report_snapshot["rows"]) == 1
        assert report_snapshot["industry_candidate_relations"]["rows"][0][
            "relation_status"
        ] == "related"
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)


def test_decision_cannot_be_reused_for_modified_candidate(tmp_path):
    snapshot = _snapshot()
    candidate_id = snapshot["industry_candidate_relations"]["rows"][0]["candidate_id"]
    append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="candidate-original-01", reviewer_session="sid",
    )
    changed = _snapshot()
    changed["rows"][0]["title"] = "Changed title"
    projected = project_reviewed_newsradar_snapshot(
        changed, review_state(tmp_path, "rw_123456789abc", "SH603986"),
        snapshot_asset_id="wa_1234567890abcdef",
    )
    assert projected["rows"] == []
    assert projected["industry_review_gate"]["candidate_counts"]["invalid"] == 1


def test_report_inventory_registers_only_related_projection(tmp_path):
    snapshot = _snapshot()
    candidate_id = snapshot["industry_candidate_relations"]["rows"][0]["candidate_id"]
    source = {
        "evidence_id": "EV-NEWS",
        "source_id": "research.newsradar.live",
        "source_ref": "wa_1234567890abcdef",
        "type": "context",
    }
    base = {
        "hub": {
            "source_register": [source],
            "evidence_scope": {"issuer": [], "context": ["EV-NEWS"]},
        },
        "workspace_snapshots": [{
            "asset": {
                "id": "wa_1234567890abcdef",
                "dataset_id": "research.newsradar.live",
            },
            "snapshot": snapshot,
        }],
    }
    assert "ev-news" not in _source_inventory(base, [])["refs"]

    appended = append_review(
        tmp_path, "rw_123456789abc", "SH603986",
        snapshot_asset_id="wa_1234567890abcdef", snapshot=snapshot,
        candidate_id=candidate_id, decision="related", expected_revision=0,
        idempotency_key="report-related-0001", reviewer_session="sid",
    )
    projected = project_reviewed_newsradar_snapshot(
        snapshot, appended["state"], snapshot_asset_id="wa_1234567890abcdef",
    )
    reviewed = dict(base)
    reviewed["workspace_snapshots"] = [{
        "asset": {
            "id": "wa_1234567890abcdef",
            "dataset_id": "research.newsradar.live",
        },
        "snapshot": projected,
    }]
    record = _source_inventory(reviewed, [])["refs"]["ev-news"]
    assert record["scope"] == "context"
    assert record["payload_available"] is True
