from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from financial_analyst.agent.orchestrator import Orchestrator
from financial_analyst.agent.registry import SubAgentRegistry
from financial_analyst.agent.tier1.evidence_loader import EvidenceLoader
from financial_analyst.data.trusted_receipts import (
    KEY_ENV,
    KEY_ID_ENV,
    VERIFY_KEYS_ENV,
    WORKSPACE_SNAPSHOT_DOMAIN,
    sign_payload,
    workspace_snapshot_auth_payload,
)
from financial_analyst.swarm.loader import load_preset
from guanlan_v2.console import workspace_tools
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.reports import evidence
from guanlan_v2.research.company_workspace import build_workspace_company_artifacts


def _asof() -> str:
    return (date.today() - timedelta(days=2)).isoformat()


def _f10_receipt(asof: str) -> dict:
    return {
        "source": "f10:最新提示", "status": "available",
        "requested_asof": asof, "data_asof": asof, "pulled_at": None,
        "snapshot_version": "fixture-v1", "snapshot_asof": asof,
        "snapshot_immutable": True, "content_sha256": "a" * 64,
        "cache_key": "b" * 64, "point_in_time": True,
        "historical_version_proven": True,
    }


def _build_historical(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_ID_ENV, "pit-test-key-v1")
    monkeypatch.setenv(
        KEY_ENV, "test-only-report-pit-hmac-key-material-32-bytes-minimum",
    )
    asof = _asof()
    calls = []

    class Facts:
        def to_dict(self):
            return {
                "snapshot_date": asof.replace("-", ""),
                "valuation": {"report_period": "2025-12-31", "eps": 1.0},
                "events": [], "broker": {"ratings": []}, "holders": None,
                "main_capital": None,
                "lhb": {"margin": [], "moneyflow": [], "abnormal": []},
                "provenance": [], "honest_note": "",
                "snapshot_receipts": [_f10_receipt(asof)],
                "unavailable_sources": [],
            }

    class F10:
        @staticmethod
        def load_facts(code, asof=None):
            calls.append((code, asof))
            return Facts()

    monkeypatch.setattr(evidence, "_engine_data_module", lambda name: F10)
    monkeypatch.setattr(
        evidence, "_valuation_bands",
        lambda code: (_ for _ in ()).throw(AssertionError("historical live valuation")),
    )
    live_calls = []
    for name in set(evidence._SECTION_NAMES) - {"company_facts", "valuation"}:
        monkeypatch.setattr(
            evidence, f"_sec_{name}",
            lambda code, _name=name: live_calls.append(_name),
        )
    result = evidence.build_evidence_pack("SH600000", out_dir=tmp_path, asof=asof)
    return asof, result, calls, live_calls


def test_historical_producer_without_signing_key_does_not_persist_success(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv(KEY_ID_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    out_dir = tmp_path / "unsigned-history"

    result = evidence.build_evidence_pack(
        "SH600000", out_dir=out_dir, asof=_asof(),
    )

    assert result["ok"] is False
    assert result["path"] is None
    assert result["manifest_digest"] is None
    assert "signing key unavailable" in result["errors"]["_trust"]
    assert not out_dir.exists()


def test_historical_pack_skips_live_sources_and_keeps_v2_manifest(monkeypatch, tmp_path):
    asof, result, calls, live_calls = _build_historical(monkeypatch, tmp_path)
    pack = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert live_calls == []
    assert calls == [("SH600000", asof), ("SH600000", asof)]
    assert pack["schema_version"] == "company-research-evidence@2"
    assert pack["manifest_schema_version"] == "report-evidence-manifest/v1"
    assert pack["mode"] == "historical"
    assert pack["manifest_auth"]["algorithm"] == "HMAC-SHA256"
    assert set(pack["sections"]) == set(evidence._SECTION_NAMES)
    for name in set(evidence._SECTION_NAMES) - {"company_facts", "valuation"}:
        assert pack["sections"][name] is None
        assert pack["receipts"][name]["status"] == "unavailable"
        assert pack["receipts"][name]["auth"]["algorithm"] == "HMAC-SHA256"
    unsigned = dict(pack)
    digest = unsigned.pop("manifest_digest")
    assert evidence._digest(unsigned) == digest


def test_loader_rejects_tampered_history_but_keeps_current_legacy_compatibility(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    loader = EvidenceLoader(tmp_path, pack_path=result["path"])
    output = asyncio.run(loader._execute({"code": "SH600000", "asof_date": asof}))
    assert output["mode"] == "historical"
    assert output["sections"]["valuation"] is not None

    tampered = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    tampered["sections"]["valuation"]["f10"]["eps"] = 99
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    try:
        asyncio.run(EvidenceLoader(tmp_path, pack_path=tampered_path)._execute({
            "code": "SH600000", "asof_date": asof,
        }))
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered historical manifest was accepted")

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps({
        "schema_version": "company-research-evidence@2",
        "generated_at": "now", "sections": {"quote_live": {"price": 1}},
        "errors": {},
    }), encoding="utf-8")
    current = asyncio.run(EvidenceLoader(tmp_path, pack_path=legacy_path)._execute({}))
    assert current["sections"]["quote_live"]["price"] == 1


def test_historical_manifest_requires_external_hmac_and_rejects_coordinated_forgery(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    original = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

    def load(pack: dict, name: str):
        path = tmp_path / f"manifest-{name}.json"
        path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        return asyncio.run(EvidenceLoader(tmp_path, pack_path=path)._execute({
            "code": "SH600000", "asof_date": asof,
        }))

    fabricated_section = {"as_of": asof, "f10": {"eps": 999}}
    fabricated_receipt = {
        "source": "valuation", "status": "available",
        "requested_asof": asof, "data_asof": asof, "pulled_at": None,
        "snapshot_version": "fabricated-v1", "snapshot_asof": asof,
        "snapshot_immutable": True,
        "content_sha256": evidence._digest(fabricated_section),
        "cache_key": "f" * 64, "point_in_time": True,
        "historical_version_proven": True, "proof": "fabricated",
        "reason": None,
    }
    fabricated = {
        "schema_version": "company-research-evidence@2",
        "manifest_schema_version": "report-evidence-manifest/v1",
        "code": "SH600000", "asof": asof, "mode": "historical",
        "generated_at": f"{asof}T15:00:00+08:00",
        "cache_key": evidence._manifest_cache_key("SH600000", asof, "historical"),
        "coverage": {}, "errors": {}, "sections_ok": ["valuation"],
        "sections": {"valuation": fabricated_section},
        "receipts": {"valuation": fabricated_receipt},
        "unavailable_sources": [],
    }
    fabricated["manifest_digest"] = evidence._digest(fabricated)
    with pytest.raises(ValueError, match="authentication failed"):
        load(fabricated, "from-zero")

    coordinated = json.loads(json.dumps(original))
    coordinated["sections"]["valuation"]["f10"]["eps"] = 999
    coordinated["receipts"]["valuation"]["content_sha256"] = evidence._digest(
        coordinated["sections"]["valuation"],
    )
    coordinated["receipts"]["valuation"].pop("auth", None)
    coordinated.pop("manifest_auth", None)
    coordinated.pop("manifest_digest", None)
    coordinated["manifest_digest"] = evidence._digest(coordinated)
    with pytest.raises(ValueError, match="authentication failed"):
        load(coordinated, "coordinated-digests")

    no_signature = json.loads(json.dumps(original))
    no_signature.pop("manifest_auth")
    no_signature.pop("manifest_digest")
    no_signature["manifest_digest"] = evidence._digest(no_signature)
    with pytest.raises(ValueError, match="authentication failed"):
        load(no_signature, "missing-signature")

    monkeypatch.setenv(
        KEY_ENV, "wrong-report-pit-hmac-key-material-that-is-over-32-bytes",
    )
    with pytest.raises(ValueError, match="authentication failed"):
        load(original, "wrong-key")

    monkeypatch.delenv(KEY_ENV)
    with pytest.raises(ValueError, match="trusted key unavailable"):
        load(original, "missing-key")


def test_manifest_key_rotation_verifies_old_and_reseals_enrichment_with_active_key(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    pack = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    old_key = "test-only-report-pit-hmac-key-material-32-bytes-minimum"
    monkeypatch.setenv(KEY_ID_ENV, "pit-new-key")
    monkeypatch.setenv(
        KEY_ENV, "new-active-report-pit-hmac-key-material-over-32-bytes",
    )
    monkeypatch.setenv(
        VERIFY_KEYS_ENV, json.dumps({"pit-test-key-v1": old_key}),
    )

    loaded = asyncio.run(EvidenceLoader(tmp_path, pack_path=result["path"])._execute({
        "code": "SH600000", "asof_date": asof,
    }))
    assert loaded["sections"]["valuation"] is not None

    artifacts = build_workspace_company_artifacts(
        pack,
        [],
        subject={"kind": "stock", "code": "SH600000", "name": "fixture"},
        source_plan=[],
        as_of=asof,
    )
    resealed = artifacts["engine_evidence_pack"]
    assert resealed["manifest_auth"]["key_id"] == "pit-new-key"
    assert all(
        receipt["auth"]["key_id"] == "pit-new-key"
        for receipt in resealed["receipts"].values()
    )


def test_loader_binds_each_historical_receipt_to_its_exact_section(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    original = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

    def load_case(name: str, mutate, *, resign_receipts=()) -> dict:
        pack = json.loads(json.dumps(original))
        mutate(pack)
        for source in resign_receipts:
            pack["receipts"][source] = evidence._sign_historical_receipt(
                pack["receipts"][source],
            )
        evidence._seal_evidence_manifest(pack, historical=True)
        path = tmp_path / f"receipt-{name}.json"
        path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        return asyncio.run(EvidenceLoader(tmp_path, pack_path=path)._execute({
            "code": "SH600000", "asof_date": asof,
        }))

    missing = load_case(
        "missing",
        lambda pack: pack["receipts"]["valuation"].update({"content_sha256": None}),
        resign_receipts=("valuation",),
    )
    assert missing["sections"]["valuation"] is None
    assert "content identity missing" in missing["errors"]["valuation"]

    absent_receipt = load_case(
        "absent-receipt",
        lambda pack: pack["receipts"].pop("valuation"),
    )
    assert absent_receipt["sections"]["valuation"] is None
    assert "snapshot receipt missing" in absent_receipt["errors"]["valuation"]

    forged = load_case(
        "forged",
        lambda pack: pack["receipts"]["valuation"].update({
            "content_sha256": "f" * 64,
        }),
        resign_receipts=("valuation",),
    )
    assert forged["sections"]["valuation"] is None
    assert "content digest mismatch" in forged["errors"]["valuation"]

    tampered = load_case(
        "tampered-section",
        lambda pack: pack["sections"]["valuation"]["f10"].update({"eps": 99}),
    )
    assert tampered["sections"]["valuation"] is None
    assert "content digest mismatch" in tampered["errors"]["valuation"]

    crossed = load_case(
        "cross-section",
        lambda pack: pack["receipts"].update({
            "company_facts": dict(pack["receipts"]["valuation"]),
        }),
    )
    assert crossed["sections"]["company_facts"] is None
    assert "source mismatch" in crossed["errors"]["company_facts"]

    missing_content = load_case(
        "missing-content",
        lambda pack: pack["sections"].update({"valuation": None}),
    )
    assert missing_content["receipts"]["valuation"]["status"] == "unavailable"
    assert missing_content["receipts"]["valuation"]["content_sha256"] is None
    assert "content missing" in missing_content["errors"]["valuation"]

    after_hong_kong_cutoff = load_case(
        "hong-kong-cutoff",
        lambda pack: pack["receipts"]["valuation"].update({
            "pulled_at": f"{asof}T20:00:00Z",
        }),
        resign_receipts=("valuation",),
    )
    assert after_hong_kong_cutoff["sections"]["valuation"] is None
    assert "at/before requested asof" in after_hong_kong_cutoff["errors"]["valuation"]

    # Current packs retain their long-standing compatibility boundary: receipt
    # provenance is surfaced but strict historical filtering is not activated.
    current = json.loads(json.dumps(original))
    current.update({
        "asof": date.today().isoformat(),
        "mode": "current",
        "manifest_digest": "not-required-for-current",
    })
    current["receipts"]["valuation"]["content_sha256"] = "wrong-current-digest"
    current_path = tmp_path / "current-mismatched-receipt.json"
    current_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    current_output = asyncio.run(
        EvidenceLoader(tmp_path, pack_path=current_path)._execute({
            "code": "SH600000", "asof_date": date.today().isoformat(),
        })
    )
    assert current_output["sections"]["valuation"] is not None


def test_old_yaml_evidence_loader_receives_request_identity(monkeypatch, tmp_path):
    preset = tmp_path / "legacy.yaml"
    preset.write_text(
        "agents:\n  - name: evidence-loader\n    deps: []\n    input_keys: []\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(SubAgentRegistry._registry, "evidence-loader", EvidenceLoader)
    nodes = load_preset("legacy", memory_root=tmp_path, preset_dir=tmp_path)
    assert nodes[0].input_keys == ["code", "asof_date"]
    inputs = Orchestrator(nodes)._build_inputs(
        "evidence-loader", {"code": "SH600000", "asof_date": _asof()}, {},
    )
    assert inputs == {"code": "SH600000", "asof_date": _asof()}


def _workspace_snapshot(
    asof: str, *, asset_id: str, pulled_at: str, value: str,
    acquired_at: str | None = None,
    dataset_id: str = "research.trade_disclosure_calendar.live",
    subject_code: str = "SH600000",
    workspace_id: str = "rw_pit_fixture",
) -> dict:
    snapshot = {
        "ok": True,
        "dataset_id": dataset_id,
        "status": "ok",
        "query": {"stock_code": "SH600000"},
        "rows": [{"date": asof, "value": value}],
        "sources": [{"authority": "fixture", "status": "ok"}],
        "pulled_at": pulled_at,
        "as_of": asof,
        "warnings": [],
    }
    payload = {
        "asset_id": asset_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "snapshot": snapshot,
    }
    asset = {
        "id": asset_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "payload_sha256": evidence._digest(payload),
        "acquired_at": acquired_at or pulled_at,
        "pulled_at": pulled_at,
        "as_of": asof,
        "source": "ww_workspace_fixture",
        "version": 1,
        "subject": {"kind": "stock", "code": subject_code},
    }
    auth_payload = workspace_snapshot_auth_payload(
        asset_id=asset_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        payload_sha256=asset["payload_sha256"],
        acquired_at=asset["acquired_at"],
        pulled_at=pulled_at,
        as_of=asof,
        source=asset["source"],
        subject=asset["subject"],
        asset_version=asset["version"],
    )
    asset["snapshot_auth"] = sign_payload(
        WORKSPACE_SNAPSHOT_DOMAIN, auth_payload,
    )
    return {
        "asset": asset,
        "snapshot": snapshot,
    }


def test_historical_workspace_does_not_launder_self_hashed_unsigned_snapshot(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    pack = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    fabricated = _workspace_snapshot(
        asof,
        asset_id="wa_3333333333333333",
        pulled_at=f"{asof}T15:00:00+08:00",
        value="today-fabricated-forward-secret",
    )
    fabricated["asset"].pop("snapshot_auth")

    with pytest.raises(ValueError, match="workspace snapshot.*untrusted.*authentication"):
        build_workspace_company_artifacts(
            pack,
            [fabricated],
            subject={"kind": "stock", "code": "SH600000", "name": "fixture"},
            source_plan=[],
            as_of=asof,
        )


@pytest.mark.parametrize("subject_code", ["", "SZ000001"])
def test_historical_workspace_requires_exact_signed_subject_binding(
    monkeypatch, tmp_path, subject_code,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    pack = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    snapshot = _workspace_snapshot(
        asof,
        asset_id="wa_4444444444444444",
        pulled_at=f"{asof}T15:00:00+08:00",
        value="cross-subject-secret",
        subject_code=subject_code,
    )

    with pytest.raises(ValueError, match="workspace snapshot.*another subject"):
        build_workspace_company_artifacts(
            pack,
            [snapshot],
            subject={"kind": "stock", "code": "SH600000", "name": "fixture"},
            source_plan=[],
            as_of=asof,
        )


def test_historical_workspace_enrichment_filters_future_and_reseals_manifest(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path)
    monkeypatch.setattr(evidence, "build_evidence_pack", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        workspace_tools, "_company_profile_section",
        lambda code: (_ for _ in ()).throw(AssertionError("historical live profile")),
    )
    monkeypatch.setattr(
        workspace_tools, "_financials_section",
        lambda code: (_ for _ in ()).throw(AssertionError("historical live financials")),
    )
    pack, pack_receipt = workspace_tools._build_company_pack("SH600000", asof)
    assert pack_receipt["temporary_path_discarded"] is True
    assert "company_profile" not in pack["sections"]
    assert "financials" not in pack["sections"]
    unsigned = dict(pack)
    digest = unsigned.pop("manifest_digest")
    assert evidence._digest(unsigned) == digest

    # A signed manifest can still contain a section without a valid source
    # receipt.  The workspace adapter must scrub it, not bless it by re-signing.
    pack["sections"]["announcements"] = {
        "as_of": asof, "items": [{"title": "unversioned-current-secret"}],
    }
    evidence._seal_evidence_manifest(pack, historical=True)

    visible = _workspace_snapshot(
        asof,
        asset_id="wa_1111111111111111",
        pulled_at=f"{asof}T15:00:00Z",
        value="visible-at-cutoff",
    )
    future = _workspace_snapshot(
        asof,
        asset_id="wa_2222222222222222",
        pulled_at=f"{asof}T15:00:00+08:00",
        acquired_at=f"{date.today().isoformat()}T09:30:00+08:00",
        dataset_id="research.future.only.secret",
        value="future-secret",
    )
    artifacts = build_workspace_company_artifacts(
        pack,
        [visible, future],
        subject={"kind": "stock", "code": "SH600000", "name": "fixture"},
        source_plan=[],
        as_of=asof,
    )
    engine_pack = artifacts["engine_evidence_pack"]

    assert set(evidence._SECTION_NAMES) <= set(engine_pack["sections"])
    assert engine_pack["sections"]["announcements"] is None
    live = engine_pack["sections"]["workspace_live"]["datasets"]
    assert live["research.trade_disclosure_calendar.live"]["asset_id"] \
        == "wa_1111111111111111"
    serialised_artifacts = json.dumps(artifacts, ensure_ascii=False)
    for future_marker in (
        "future-secret",
        "wa_2222222222222222",
        "research.future.only.secret",
    ):
        assert future_marker not in json.dumps(engine_pack, ensure_ascii=False)
        assert future_marker not in serialised_artifacts
    for name in (
        "workspace_live", "company_profile_contract", "company_financials_contract",
        "company_estimates_contract", "company_valuation_dag",
        "company_vibe_valuation", "company_risk_contract", "company_research_manifest",
    ):
        assert engine_pack["receipts"][name]["historical_version_proven"] is True

    unsigned = dict(engine_pack)
    digest = unsigned.pop("manifest_digest")
    assert evidence._digest(unsigned) == digest
    assert evidence._verify_evidence_manifest_auth(engine_pack) is True

    # The production mapping enrichment happens after the company adapter.  It
    # must issue its own receipt and re-sign the now-complete section list.
    mapping_receipt = {
        "schema_version": "company-source-mapping-receipt@1",
        "receipt_id": "CMR-PIT-FIXTURE",
        "subject_code": "SH600000",
        "research_as_of": asof,
        "status": "unmapped",
        "targets": {},
    }
    workspace_tools._embed_mapping_receipt(artifacts, mapping_receipt)
    engine_pack = artifacts["engine_evidence_pack"]
    assert engine_pack["receipts"]["company_source_mapping"][
        "historical_version_proven"
    ] is True
    unsigned = dict(engine_pack)
    digest = unsigned.pop("manifest_digest")
    assert evidence._digest(unsigned) == digest

    path = tmp_path / "workspace-historical.json"
    path.write_text(json.dumps(engine_pack, ensure_ascii=False), encoding="utf-8")
    output = asyncio.run(EvidenceLoader(tmp_path, pack_path=path)._execute({
        "code": "SH600000", "asof_date": asof,
    }))
    assert output["sections"]["company_research_manifest"] is not None
    assert output["sections"]["company_source_mapping"]["receipt_id"] \
        == "CMR-PIT-FIXTURE"
    for name in (
        "workspace_live", "company_profile_contract", "company_financials_contract",
        "company_estimates_contract", "company_valuation_dag",
        "company_vibe_valuation", "company_risk_contract", "company_research_manifest",
        "company_source_mapping",
    ):
        assert output["sections"][name] is not None
        assert output["receipts"][name]["content_sha256"] \
            == evidence._digest(output["sections"][name])
        assert output["receipts"][name]["auth"]["algorithm"] == "HMAC-SHA256"


def test_historical_latest_selector_ignores_future_flood_and_unsigned_order(
    monkeypatch, tmp_path,
):
    asof, result, _, _ = _build_historical(monkeypatch, tmp_path / "pack")
    pack = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    store = ConsoleStore(root=tmp_path / "console")
    workspace = store.workspace_store.create({
        "kind": "stock", "code": "SH600000", "name": "fixture",
        "display_name": "fixture", "root_mode": "managed", "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    token = workspace_tools.CTX_WORKSPACE.set(
        workspace_tools.bind_workspace_context(store, sid),
    )

    early = _workspace_snapshot(
        asof,
        asset_id="wa_a000000000000001",
        workspace_id=workspace["id"],
        pulled_at=f"{asof}T10:00:00+08:00",
        acquired_at=f"{asof}T10:00:00+08:00",
        value="eligible-early",
    )
    latest = _workspace_snapshot(
        asof,
        asset_id="wa_b000000000000002",
        workspace_id=workspace["id"],
        pulled_at=f"{asof}T18:00:00+08:00",
        acquired_at=f"{asof}T18:00:00+08:00",
        value="eligible-latest",
    )
    future_calendar = _workspace_snapshot(
        asof,
        asset_id="wa_f000000000000000",
        workspace_id=workspace["id"],
        pulled_at=f"{asof}T09:00:00+08:00",
        acquired_at=f"{date.today().isoformat()}T09:30:00+08:00",
        value="future-calendar-secret",
    )
    future_flood = [
        _workspace_snapshot(
            asof,
            asset_id=f"wa_f{index + 1:015x}",
            workspace_id=workspace["id"],
            pulled_at=f"{asof}T09:00:00+08:00",
            acquired_at=f"{date.today().isoformat()}T09:30:00+08:00",
            dataset_id="research.future.only.secret",
            value="future-flood-secret",
        )
        for index in range(501)
    ]
    records = {
        row["asset"]["id"]: row
        for row in [early, latest, future_calendar, *future_flood]
    }

    def inventory(_store, _workspace_id):
        # Raw index order is deliberately hostile: >500 future entries first,
        # then the older eligible record with a forged newer ``updated`` value.
        ordered = [*future_flood, future_calendar, early, latest]
        rows = []
        for position, record in enumerate(ordered):
            item = dict(record["asset"])
            item.update({
                "kind": "data", "scope": "workspace",
                "updated": (
                    "2099-01-01T00:00:00+08:00"
                    if record is early else f"2020-01-01T00:{position % 60:02d}:00+08:00"
                ),
            })
            rows.append(item)
        return rows

    monkeypatch.setattr(workspace_tools, "_list_workspace_assets_complete", inventory)
    monkeypatch.setattr(
        workspace_tools, "_verified_snapshot_record",
        lambda _store, _workspace_id, _code, asset_id: records[asset_id],
    )
    monkeypatch.setattr(
        workspace_tools, "_build_company_pack", lambda *args, **kwargs: (pack, result),
    )
    try:
        prepared = workspace_tools.workspace_company_research_prepare_impl(
            asof=asof,
            refresh_calendar=False,
            include_newsradar=False,
            include_latest_snapshots=True,
        )
    finally:
        workspace_tools.CTX_WORKSPACE.reset(token)

    assert prepared["ok"] is True
    assert prepared["asset"]["snapshot_asset_ids"] == [latest["asset"]["id"]]
    calendar_plan = next(
        row for row in prepared["source_plan"]
        if row.get("dataset_id") == "research.trade_disclosure_calendar.live"
    )
    assert calendar_plan["asset_id"] == latest["asset"]["id"]
    run_dir = Path(workspace["root_path"], prepared["asset"]["run_rel"])
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*") if path.is_file()
    )
    serialised = json.dumps(prepared, ensure_ascii=False) + persisted
    for forbidden in (
        future_calendar["asset"]["id"],
        "wa_f000000000000001",
        "wa_f0000000000001f5",
        "future-calendar-secret",
        "future-flood-secret",
        "research.future.only.secret",
    ):
        assert forbidden not in serialised
