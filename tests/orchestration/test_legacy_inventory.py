# -*- coding: utf-8 -*-
"""Task 0 completeness test — legacy contract / worker-ownership inventory.

Validates the *structure and honesty* of the Phase-1 evidence pack:
  - tests/orchestration/fixtures/legacy_contract_samples.json
  - docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md
  - docs/superpowers/migrations/2026-07-15-orchestration-worker-map.md

It deliberately does NOT import any legacy class (so no engine PYTHONPATH is
needed); it only reads the human-reviewed tables + the real-sample fixture and
cross-checks them against each other and against the real source files on disk.

Run from repo root: ``pytest tests/orchestration/test_legacy_inventory.py -v``
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

# repo root = .../guanlan-v2 (this file: tests/orchestration/test_legacy_inventory.py)
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "orchestration" / "fixtures" / "legacy_contract_samples.json"
CONTRACT_MAP = REPO_ROOT / "docs" / "superpowers" / "migrations" / "2026-07-15-orchestration-legacy-contract-map.md"
WORKER_MAP = REPO_ROOT / "docs" / "superpowers" / "migrations" / "2026-07-15-orchestration-worker-map.md"

# The 24 stable design worker IDs (verbatim from the Phase-1 plan / brief).
REQUIRED_WORKER_IDS = [
    "market.factor", "market.regime", "market.rotation",
    "quant.factor", "quant.model", "quant.backtest", "quant.fundamentals", "quant.factor_miner",
    "pv.price_action", "pv.technical", "pv.microstructure",
    "text.news", "text.sentiment", "text.research_report", "text.policy", "text.macro",
    "dec.bull", "dec.bear", "dec.research_mgr", "dec.risk_debate", "dec.pm", "dec.trader",
    "x.quality_gate", "x.number_critic",
]

# module.qualified.Name@<int> ; segments allow word chars and hyphens (e.g. stock-deep-dive)
_SOURCE_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)+@\d+$")

UNMAPPABLE = "UNMAPPABLE"


def _canonical_digest(obj) -> str:
    """sha256 over canonical JSON — must match the fixture's recorded graph digest."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def fx() -> dict:
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract_pairs() -> set:
    """(source_schema, semantic_domain) pairs parsed from the human-reviewed contract-map table."""
    assert CONTRACT_MAP.exists(), f"missing contract map {CONTRACT_MAP}"
    pairs = set()
    for line in CONTRACT_MAP.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5:
            continue
        source_schema, semantic_domain = cells[0], cells[4]
        # skip header + separator rows
        if source_schema in ("source_schema", "") or set(source_schema) <= set("-: "):
            continue
        pairs.add((source_schema, semantic_domain))
    return pairs


# 1 -----------------------------------------------------------------------
def test_every_action_sample_has_source_schema_and_semantic_domain(fx):
    actions = [s for s in fx["scalars"] if s["kind"] == "action"]
    assert actions, "no action scalars inventoried"
    for s in actions:
        assert s.get("source_schema"), f"action scalar missing source_schema: {s}"
        assert s.get("semantic_domain"), f"action scalar missing semantic_domain: {s}"


# 2 -----------------------------------------------------------------------
def test_raw_hold_is_a_different_domain_under_report_vs_position(fx):
    hold_domains = {
        s["semantic_domain"]
        for s in fx["scalars"]
        if s["kind"] == "action" and "hold" in s["raw_domain"]
    }
    assert "research_recommendation" in hold_domains
    assert "position_adjustment" in hold_domains
    assert (
        len(hold_domains) >= 2
    ), f"'hold' must not collapse report and position domains: {hold_domains}"


# 3 -----------------------------------------------------------------------
def test_all_24_worker_ids_occur_exactly_once(fx):
    ids = [w["worker_id"] for w in fx["workers"]]
    assert len(ids) == len(REQUIRED_WORKER_IDS)
    assert sorted(ids) == sorted(REQUIRED_WORKER_IDS), (
        set(REQUIRED_WORKER_IDS).symmetric_difference(ids)
    )
    assert len(set(ids)) == len(ids), "duplicate worker_id present"


# 4 -----------------------------------------------------------------------
def test_every_documented_source_path_exists(fx):
    seen = 0
    for section in ("scalars", "workers", "graphs"):
        for row in fx[section]:
            p = row.get("source_path")
            if not p:
                continue
            seen += 1
            assert (REPO_ROOT / p).exists(), f"documented source_path missing on disk: {p}"
    assert seen >= 13, f"expected the real legacy sources to be documented, saw {seen}"


# 5 -----------------------------------------------------------------------
def test_no_lossy_or_uncertain_mapping_labelled_exact(fx):
    for s in fx["scalars"]:
        rp = s.get("roundtrip_policy")
        if rp == "exact":
            assert s["mapping_basis"] == "authoritative_code", (
                f"'exact' requires authoritative_code basis: {s['key']}"
            )
            assert s["target_schema"] and s["target_schema"] != UNMAPPABLE, (
                f"'exact' cannot target UNMAPPABLE: {s['key']}"
            )
            assert set(s["raw_domain"]) == set(s["target_domain"]), (
                f"'exact' requires identical value sets: {s['key']}"
            )
        # inverse guard: anything unmapped/uncertain must never claim exact
        if s.get("target_schema") in (None, UNMAPPABLE) or s.get("unmapped_reason"):
            assert rp != "exact", f"unmapped/uncertain row labelled exact: {s['key']}"


# 6 -----------------------------------------------------------------------
def test_fixture_domains_match_human_reviewed_table(fx, contract_pairs):
    for s in fx["scalars"]:
        pair = (s["source_schema"], s["semantic_domain"])
        assert pair in contract_pairs, (
            f"fixture scalar {pair} not found in reviewed contract-map table"
        )


# 7 -----------------------------------------------------------------------
def test_every_source_schema_key_is_module_qualified_and_unique(fx):
    keys = []
    for s in fx["scalars"]:
        assert _SOURCE_SCHEMA_RE.match(s["source_schema"]), (
            f"non-module-qualified source_schema: {s['source_schema']}"
        )
        keys.append(s["key"])
    assert len(keys) == len(set(keys)), "duplicate scalar composite keys"
    for g in fx["graphs"]:
        assert _SOURCE_SCHEMA_RE.match(g["source_schema"]), (
            f"non-module-qualified graph source_schema: {g['source_schema']}"
        )
    g_keys = [g["source_schema"] for g in fx["graphs"]]
    assert len(g_keys) == len(set(g_keys)), "duplicate graph source_schema"


# 8 -----------------------------------------------------------------------
def test_every_graph_records_normalized_config_digest_and_dep_semantics(fx):
    assert fx["graphs"], "no legacy graphs inventoried"
    for g in fx["graphs"]:
        assert g.get("source_format"), f"graph missing source_format: {g['source_schema']}"
        norm = g.get("normalized_config")
        assert norm, f"graph missing normalized_config: {g['source_schema']}"
        digest = g.get("config_digest", "")
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"bad config_digest: {g['source_schema']}"
        assert digest == _canonical_digest(norm), (
            f"config_digest does not match normalized_config: {g['source_schema']}"
        )
        dep = g.get("dependency_semantics") or {}
        for kind in ("hard", "soft"):
            assert kind in dep, f"graph {g['source_schema']} missing {kind} dependency semantics"
            assert dep[kind].get("accepted_statuses"), f"{kind} missing accepted_statuses"
            assert dep[kind].get("missing_output_behavior"), f"{kind} missing missing_output_behavior"
        assert g.get("nodes"), f"graph {g['source_schema']} records no nodes"
        for n in g["nodes"]:
            assert "node" in n and "slot_output" in n, f"node missing identity/slot: {n}"


# 9 -----------------------------------------------------------------------
def test_sections_present_and_nothing_incomplete_is_runnable(fx):
    for section in ("scalars", "workers", "graphs"):
        assert section in fx and fx[section], f"fixture missing/empty section: {section}"
    # A planned worker is never a runnable WorkerSpec; Task 0 builds zero runnable workers.
    for w in fx["workers"]:
        assert w["status"] in ("planned", "contract_ready", "runnable")
        assert w["status"] != "runnable", (
            f"Task 0 must not mint runnable WorkerSpecs: {w['worker_id']}"
        )
    # A graph with any unresolved worker/edge meaning must not be labelled runnable.
    for g in fx["graphs"]:
        unresolved = bool(g.get("unresolved_workers") or g.get("unresolved_edges"))
        if unresolved:
            assert g["runnable"] is False, (
                f"graph {g['source_schema']} labelled runnable with unresolved meaning"
            )
        # every graph node target worker must be one of the 24 planned IDs or an explicit compat.*
        for n in g["nodes"]:
            tw = n.get("target_worker")
            if tw and tw != UNMAPPABLE and not tw.startswith("compat."):
                assert tw in REQUIRED_WORKER_IDS, f"node maps to unknown worker id: {tw}"
