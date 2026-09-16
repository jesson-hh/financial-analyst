from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from guanlan_v2.research.company_mapping import (
    AmbiguousEntityError,
    CompanyMappingRegistry,
    TARGET_DATASETS,
    db_path,
    reviewed_table_path,
    resolve_approved_mappings,
)


T0 = "2026-08-30T01:00:00Z"
T1 = "2026-08-30T02:00:00Z"
T2 = "2026-08-30T03:00:00Z"
T3 = "2026-08-30T04:00:00Z"
T4 = "2026-08-30T05:00:00Z"


def _seed_entities(registry: CompanyMappingRegistry):
    source = registry.upsert_entity(
        entity_id="issuer-sh603986",
        entity_type="listed_issuer",
        canonical_name="兆易创新",
        jurisdiction="CN",
        identifiers={"security_code": "SH603986", "short_code": "603986"},
        at=T0,
    )
    tw = registry.upsert_entity(
        entity_id="tw-6488",
        entity_type="listed_issuer",
        canonical_name="环球晶圆",
        jurisdiction="TW",
        identifiers={"company_id": "6488"},
        at=T0,
    )
    commodity = registry.upsert_entity(
        entity_id="commodity-cu",
        entity_type="commodity_contract",
        canonical_name="沪铜连续合约",
        jurisdiction="CN",
        identifiers={"symbol": "CU0"},
        at=T0,
    )
    legal = registry.upsert_entity(
        entity_id="legal-giga",
        entity_type="legal_entity",
        canonical_name="GigaDevice Semiconductor Inc.",
        jurisdiction="CN",
        at=T0,
    )
    return source, tw, commodity, legal


def _proposal(registry: CompanyMappingRegistry, **overrides):
    values = {
        "source_entity_id": "issuer-sh603986",
        "target_entity_id": "tw-6488",
        "target_dataset": "tw_monthly_revenue",
        "target_key": "6488",
        "relationship": "industry_peer",
        "evidence": [{"source_ref": "annual-report:2025", "page": 62}],
        "context": {"scope": "context", "reason": "silicon-wafer demand"},
        "confidence": 0.9,
        "at": T1,
    }
    values.update(overrides)
    return registry.propose_mapping(**values)


def test_path_injection_and_resolver_does_not_create_default_or_explicit_db(
    tmp_path, monkeypatch,
):
    configured = tmp_path / "configured" / "company_mappings.db"
    monkeypatch.setenv("GUANLAN_COMPANY_MAPPING_DB", str(configured))
    assert db_path() == configured

    result = resolve_approved_mappings("SH603986")

    assert result["registry_status"] == "not_configured"
    assert result["target_keys"] == {dataset: [] for dataset in sorted(TARGET_DATASETS)}
    assert not configured.exists()
    assert not configured.parent.exists()


def test_init_is_idempotent_wal_and_has_auditable_schema(tmp_path):
    path = tmp_path / "mapping" / "registry.db"
    CompanyMappingRegistry(path)
    CompanyMappingRegistry(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "entities", "aliases", "mapping_edges", "mapping_decisions", "mapping_versions",
        } <= tables
        assert conn.execute(
            "SELECT value FROM mapping_meta WHERE key='schema_version'"
        ).fetchone()[0] == "1"


def test_entity_and_identifier_aliases_are_idempotent(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    first, *_ = _seed_entities(registry)
    second, *_ = _seed_entities(registry)

    assert first == second
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_id='issuer-sh603986'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM aliases WHERE entity_id='issuer-sh603986'"
        ).fetchone()[0] == 2


def test_only_approved_mapping_resolves_with_receipt_context(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    _seed_entities(registry)
    proposed = _proposal(registry)

    before = resolve_approved_mappings("SH603986", path=registry.path, as_of=T1)
    assert before["registry_status"] == "empty"
    assert before["edges"] == []

    approval = registry.decide_mapping(
        proposed["edge_id"], decision="approve", decided_by="research-admin",
        reason="issuer disclosure checked", at=T2,
    )
    resolved = resolve_approved_mappings("603986", path=registry.path, as_of=T2)

    assert approval["active"] is True
    assert resolved["registry_status"] == "ok"
    assert resolved["target_keys"]["tw_monthly_revenue"] == ["6488"]
    target = resolved["targets"]["tw_monthly_revenue"][0]
    assert target == {
        "key": "6488",
        "edge_id": proposed["edge_id"],
        "version_id": proposed["version_id"],
        "version": 1,
        "version_digest": proposed["version_digest"],
        "relationship": "industry_peer",
        "context": {"reason": "silicon-wafer demand", "scope": "context"},
        "target_entity": {
            "entity_id": "tw-6488",
            "entity_type": "listed_issuer",
            "canonical_name": "环球晶圆",
            "jurisdiction": "TW",
            "identifiers": {"company_id": "6488"},
            "metadata": {},
        },
        "origin": "registry",
    }
    assert resolved["edges"][0]["approval_decision_id"] == approval["decision_id"]
    assert len(resolved["mapping_version"]) == 64
    assert len(resolved["digest"]) == 64


def test_new_version_supersedes_and_requires_fresh_approval_with_pit_history(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    _seed_entities(registry)
    v1 = _proposal(registry)
    registry.decide_mapping(
        v1["edge_id"], decision="approve", decided_by="reviewer", at=T2,
    )

    v2 = _proposal(
        registry,
        evidence=[
            {"source_ref": "annual-report:2025", "page": 62},
            {"source_ref": "company-ir:2026", "page": 8},
        ],
        confidence=0.95,
        at=T3,
    )

    assert v2["version"] == 2
    assert v2["supersedes_version_id"] == v1["version_id"]
    assert resolve_approved_mappings(
        "SH603986", path=registry.path, as_of="2026-08-30T03:30:00Z",
    )["edges"][0]["version_id"] == v1["version_id"]
    assert resolve_approved_mappings(
        "SH603986", path=registry.path, as_of=T3,
    )["registry_status"] == "empty"

    registry.decide_mapping(
        v2["edge_id"], decision="approve", decided_by="reviewer", at=T4,
    )
    current = resolve_approved_mappings("SH603986", path=registry.path, as_of=T4)
    assert current["edges"][0]["version_id"] == v2["version_id"]
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute(
            "SELECT invalidated_at FROM mapping_versions WHERE version_id=?", (v1["version_id"],)
        ).fetchone()[0].startswith("2026-08-30T04:00:00")


@pytest.mark.parametrize("decision", ["reject", "invalidate"])
def test_rejected_or_invalidated_mapping_never_resolves(tmp_path, decision):
    registry = CompanyMappingRegistry(tmp_path / f"{decision}.db")
    _seed_entities(registry)
    proposed = _proposal(registry)
    registry.decide_mapping(
        proposed["edge_id"], decision="approve", decided_by="reviewer", at=T2,
    )
    registry.decide_mapping(
        proposed["edge_id"], decision=decision, decided_by="reviewer",
        reason="evidence withdrawn", at=T3,
    )

    assert resolve_approved_mappings(
        "SH603986", path=registry.path, as_of=T3,
    )["registry_status"] == "empty"


def test_all_supported_datasets_validate_downstream_keys(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    _seed_entities(registry)
    for values in (
        {
            "target_entity_id": "commodity-cu", "target_dataset": "cn_commodity_futures",
            "target_key": "nf_cu0", "relationship": "input_cost",
        },
        {
            "target_entity_id": "legal-giga", "target_dataset": "control_lists",
            "target_key": "GigaDevice Semiconductor Inc.", "relationship": "legal_name",
        },
    ):
        proposal = _proposal(registry, at=T1, **values)
        registry.decide_mapping(
            proposal["edge_id"], decision="approve", decided_by="reviewer", at=T2,
        )

    result = resolve_approved_mappings("SH603986", path=registry.path, as_of=T2)
    assert result["target_keys"]["cn_commodity_futures"] == ["CU0"]
    assert result["target_keys"]["control_lists"] == ["GigaDevice Semiconductor Inc."]
    with pytest.raises(ValueError, match="target_dataset"):
        _proposal(registry, target_dataset="unknown_dataset")
    with pytest.raises(ValueError, match="合约代码"):
        _proposal(
            registry, target_entity_id="commodity-cu",
            target_dataset="cn_commodity_futures", target_key="CU-CONT",
        )


def test_explicit_targets_are_request_local_and_never_persisted(tmp_path):
    path = tmp_path / "does-not-exist" / "registry.db"

    first = resolve_approved_mappings(
        "SH603986", path=path,
        explicit_targets={
            "tw_monthly_revenue": ["6488", "6488"],
            "cn_commodity_futures": "nf_CU0",
            "control_lists": ["GigaDevice Semiconductor Inc."],
        },
    )
    second = resolve_approved_mappings("SH603986", path=path)

    assert first["registry_status"] == "not_configured"
    assert first["target_keys"] == {
        "cn_commodity_futures": ["CU0"],
        "control_lists": ["GigaDevice Semiconductor Inc."],
        "tw_monthly_revenue": ["6488"],
    }
    assert first["targets"]["tw_monthly_revenue"][0]["origin"] == "explicit"
    assert first["targets"]["tw_monthly_revenue"][0]["context"]["persisted"] is False
    assert second["target_keys"]["tw_monthly_revenue"] == []
    assert not path.exists()


def test_proposal_and_decision_are_idempotent_under_concurrent_writers(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    _seed_entities(registry)

    with ThreadPoolExecutor(max_workers=8) as pool:
        proposals = list(pool.map(lambda _: _proposal(registry), range(16)))
    assert len({row["version_id"] for row in proposals}) == 1
    edge_id = proposals[0]["edge_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(
            lambda _: registry.decide_mapping(
                edge_id, decision="approve", decided_by="reviewer",
                reason="same reviewed decision", at=T2,
            ),
            range(16),
        ))
    assert len({row["decision_id"] for row in decisions}) == 1
    with sqlite3.connect(registry.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mapping_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM mapping_decisions").fetchone()[0] == 1


def test_ambiguous_alias_is_refused_not_guessed(tmp_path):
    registry = CompanyMappingRegistry(tmp_path / "registry.db")
    registry.upsert_entity(
        entity_id="one", entity_type="legal_entity", canonical_name="One", at=T0,
    )
    registry.upsert_entity(
        entity_id="two", entity_type="legal_entity", canonical_name="Two", at=T0,
    )
    registry.add_alias("one", "shared", source_ref="source:1", at=T0)
    registry.add_alias("two", "shared", source_ref="source:2", at=T0)

    with registry._connect(read_only=True) as conn:
        with pytest.raises(AmbiguousEntityError):
            registry._resolve_subject(conn, "shared", T1)
    result = resolve_approved_mappings("shared", path=registry.path, as_of=T1)
    assert result["registry_status"] == "unavailable"
    assert result["warnings"] == ["mapping_registry_unavailable:AmbiguousEntityError"]


def test_default_reviewed_table_exactly_maps_pinned_starter_with_audit(tmp_path):
    missing_db = tmp_path / "read-only" / "missing.db"
    result = resolve_approved_mappings(
        "SZ300308", path=missing_db, as_of="2026-08-30T14:00:00Z",
    )

    assert result["registry_status"] == "ok"
    assert result["registry_db_status"] == "not_configured"
    assert result["reviewed_table_status"] == "ok"
    assert result["reviewed_table"]["table_version"] == "2026-08-30.1"
    assert result["reviewed_table"]["subject_count"] == 4
    assert result["reviewed_table"]["mapping_count"] == 13
    assert result["target_keys"] == {
        "cn_commodity_futures": ["AL0", "CU0", "NI0", "SI0", "SN0"],
        "control_lists": ["Zhongji Innolight Co., Ltd."],
        "tw_monthly_revenue": ["2368", "2383", "3081", "6274"],
    }
    assert len(result["edges"]) == 10
    assert all(row["origin"] == "reviewed_table" for row in result["edges"])
    target = result["targets"]["control_lists"][0]
    assert target["review"]["status"] == "approved"
    assert target["review"]["valid_from"].startswith("2026-08-30T00:00:00")
    assert target["review"]["valid_to"] is None
    assert target["review"]["evidence"][0]["field"] == "F001V"
    assert len(target["review"]["table_sha256"]) == 64
    assert not missing_db.exists()
    assert reviewed_table_path().is_file()

    # Exact aliases are accepted, substrings and nearby codes are not.
    assert resolve_approved_mappings(
        "中际旭创", path=missing_db, as_of="2026-08-30T14:00:00Z",
    )["target_keys"] == result["target_keys"]
    for query in ("中际", "SZ300309"):
        unmapped = resolve_approved_mappings(
            query, path=missing_db, as_of="2026-08-30T14:00:00Z",
        )
        assert unmapped["reviewed_table_status"] == "unmapped"
        assert all(not values for values in unmapped["target_keys"].values())


def test_reviewed_vibe_evidence_pins_raw_github_bytes_not_windows_checkout():
    document = json.loads(reviewed_table_path().read_text(encoding="utf-8"))
    observed: dict[str, set[str]] = {}
    for subject in document["subjects"]:
        for mapping in subject["mappings"]:
            for evidence in mapping["evidence"]:
                source_ref = str(evidence.get("source_ref") or "")
                if "github.com/simonlin1212/Vibe-Research" not in source_ref:
                    continue
                observed.setdefault(source_ref.rsplit("/", 1)[-1], set()).add(
                    evidence["source_sha256"]
                )

    # These are SHA-256 digests of raw.githubusercontent.com bytes at the
    # pinned commit.  A Windows checkout can rewrite LF to CRLF and therefore
    # must not be used as the evidence digest for a GitHub blob URL.
    assert observed == {
        "industry_tags.json": {
            "a2521f792ca8ed7182e44c1713723d831dd865effb9dba6b9467926cfb33f4fd"
        },
        "industry.py": {
            "98668841b50101f6c8469c4a499ceabd255b4c2f033bc30926d064a232abd283"
        },
        "commodity.py": {
            "cd5363524f8ee5c91c6d9047d41d525cb06f3ae86982da78b086ac3a4b756690"
        },
    }


def test_malformed_reviewed_table_fails_closed_and_never_creates_db(tmp_path):
    document = json.loads(reviewed_table_path().read_text(encoding="utf-8"))
    # One evidence-free row invalidates the whole reviewed table.  Resolution
    # must not quietly keep the other apparently-good rows.
    document["subjects"][-1]["mappings"][0]["evidence"] = []
    bad_table = tmp_path / "reviewed-bad.json"
    bad_table.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    missing_db = tmp_path / "registry" / "missing.db"

    result = resolve_approved_mappings(
        "SZ300308", path=missing_db, reviewed_path=bad_table,
        as_of="2026-08-30T14:00:00Z",
    )

    assert result["registry_status"] == "unavailable"
    assert result["reviewed_table_status"] == "unavailable"
    assert all(not values for values in result["target_keys"].values())
    assert result["warnings"] == [
        "reviewed_mapping_table_unavailable:ValueError",
        "mapping_registry_not_configured",
    ]
    assert not missing_db.exists()


def test_reviewed_rows_are_point_in_time_and_inactive_rows_do_not_leak(tmp_path):
    document = json.loads(reviewed_table_path().read_text(encoding="utf-8"))
    subject = document["subjects"][0]
    subject["mappings"][0]["valid_to"] = "2026-08-30"
    document["subjects"] = [subject]
    table = tmp_path / "expired.json"
    table.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    before = resolve_approved_mappings(
        "SZ000636", path=tmp_path / "missing.db", reviewed_path=table,
        as_of="2026-08-30T14:00:00Z",
    )
    after = resolve_approved_mappings(
        "SZ000636", path=tmp_path / "missing.db", reviewed_path=table,
        as_of="2026-08-31T00:00:00Z",
    )

    assert before["target_keys"]["control_lists"] == [
        "Guangdong Fenghua Advanced Technology (Holding) Co., Ltd."
    ]
    assert after["reviewed_table_status"] == "inactive"
    assert after["registry_status"] == "empty"
    assert after["target_keys"]["control_lists"] == []
    assert "reviewed_mapping_rows_inactive" in after["warnings"]
