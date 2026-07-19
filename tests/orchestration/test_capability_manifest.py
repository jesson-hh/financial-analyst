# -*- coding: utf-8 -*-
"""Phase 8 · Task 0b — capability-manifest generator + drift guard + 承诺-供给 lint.

The D11 vaccine (R2 §7.1: glmcp 58≠64 病的同款防疫 / 手抄 allowlist 腐烂疫苗). The
27-seat capability↔real-interface mapping material is *derived* from the single
source ``WW_TOOL_TABLE`` (``guanlan_v2.console.tools``) — never a hand-typed row —
exactly like the sibling ``scripts/gen_agent_interface_doc.py`` /
``tests/test_agent_interface_doc.py`` pair derives the interface doc. The drift
guard regenerates from the LIVE table and byte-compares the committed material, so
any hand edit or upstream tool-table change fails HONESTLY with a "re-run
generator" message, never silently.

FROZEN TABLE STATE: the committed material freezes against the WORKTREE
``WW_TOOL_TABLE`` (60 rows; ``ww_newsradar`` / ``ww_overseas`` live in production
but are uncommitted on this branch). The generator keys on the live table, so a
foreign commit that changes the row set trips both the drift guard and the
dangling-intent test below.

Run from repo root: ``pytest tests/orchestration/test_capability_manifest.py -v``
"""
from __future__ import annotations

import json

import pytest

import guanlan_v2.console.tools as ct
from guanlan_v2.orchestration.capability_manifest import (
    CapabilityInterfaceRow,
    CapabilityManifest,
    MATERIAL_PATH,
    _METHOD_TOOL_INTENT,
    generate_capability_manifest,
    lint_skill_supply,
    parse_capability_manifest,
    serialize_capability_manifest,
)
from guanlan_v2.orchestration.data.catalog import data_capability_refs
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.refs import CapabilityRef

# The six plan-ABI data capability method ids (clause (c) of the Phase 8 handoff
# gate); ``instrument_names`` is a reference method with no worker data intent and
# is intentionally NOT a manifest row.
_PLAN_ABI_METHODS = (
    "news", "ohlcv", "indicators", "verified_snapshot", "fundamentals", "signals",
)

#: The worktree freeze this material was sealed against (documented, not derived).
FROZEN_TOOL_COUNT = 60


def _live_table():
    return ct.WW_TOOL_TABLE


def _live_names() -> set[str]:
    return {str(r["name"]) for r in ct.WW_TOOL_TABLE}


def _cap_refs() -> dict[str, CapabilityRef]:
    return data_capability_refs()


def _skill(section_body: str, *, tail: str = "") -> str:
    """A minimal skill-v1 doc whose Data Source Priority section = ``section_body``."""
    return (
        "---\n"
        "name: demo\n"
        "description: |\n"
        "  A demo skill summary line.\n"
        '  Perfect for: ["x"]\n'
        '  Not ideal for: ["y"]\n'
        "---\n"
        "## ⚠️ CRITICAL: Data Source Priority\n"
        f"{section_body}\n"
        f"## Playbook\n{tail}\n"
    )


# --------------------------------------------------------------------------- #
# Derivation purity                                                           #
# --------------------------------------------------------------------------- #
def test_generate_is_pure_and_deterministic():
    a = generate_capability_manifest(_live_table())
    b = generate_capability_manifest(_live_table())
    assert a == b
    assert a.semantic_digest() == b.semantic_digest()


def test_rows_sorted_and_dup_free_by_capability_id():
    m = generate_capability_manifest(_live_table())
    ids = [r.capability_id for r in m.rows]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_real_tools_sorted_and_dup_free_within_each_row():
    m = generate_capability_manifest(_live_table())
    for row in m.rows:
        assert list(row.real_tools) == sorted(row.real_tools)
        assert len(set(row.real_tools)) == len(row.real_tools)


def test_capability_ids_are_exactly_the_six_plan_abi_data_caps():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    expected = {refs[method].id for method in _PLAN_ABI_METHODS}
    got = {r.capability_id for r in m.rows}
    assert got == expected


def test_generator_registers_no_new_capability():
    # invariant 3: every manifest capability id is one of the implemented Phase-3
    # data capability refs — the generator invents no capability and grants nothing.
    m = generate_capability_manifest(_live_table())
    known = {ref.id for ref in _cap_refs().values()}
    for row in m.rows:
        assert row.capability_id in known


def test_every_real_tool_exists_in_the_live_tool_table():
    # derivation purity: no phantom tool name can appear — real_tools ⊆ live names.
    m = generate_capability_manifest(_live_table())
    live = _live_names()
    for row in m.rows:
        for tool in row.real_tools:
            assert tool in live, f"{row.capability_id} names phantom tool {tool!r}"


def test_reviewed_intent_has_no_dangling_tool():
    # the reviewed capability→tool intent keys on the LIVE table: every named tool
    # must exist, so an upstream rename/removal fails here (never a silent drop).
    live = _live_names()
    for method, tools in _METHOD_TOOL_INTENT.items():
        for tool in tools:
            assert tool in live, f"reviewed intent for {method!r} names missing tool {tool!r}"


def test_manifest_covers_the_section_7_1_seat_tools():
    # fit-for-purpose: the §7.1 逐席映射 real tool names are all suppliable.
    m = generate_capability_manifest(_live_table())
    supplied = {t for row in m.rows for t in row.real_tools}
    for tool in (
        "ww_news_live", "ww_newsradar", "ww_live_text", "ww_f10",
        "ww_macro_pulse", "ww_sentiment", "ww_market_tape", "ww_overseas",
    ):
        assert tool in supplied, f"§7.1 seat tool {tool!r} is not supplied by any capability"


# --------------------------------------------------------------------------- #
# Frozen table state (documented in the material)                            #
# --------------------------------------------------------------------------- #
def test_material_documents_the_frozen_table_state():
    raw = MATERIAL_PATH.read_bytes()
    m = parse_capability_manifest(raw)
    # self-consistent with the live table it was regenerated from ...
    assert m.source_tool_count == len(ct.WW_TOOL_TABLE)
    assert m.source_tool_digest == content_digest(sorted(_live_names()))
    # ... and pinned to the documented worktree freeze (60 rows).
    assert m.source_tool_count == FROZEN_TOOL_COUNT


# --------------------------------------------------------------------------- #
# The drift guard (regenerate-and-compare)                                    #
# --------------------------------------------------------------------------- #
def test_material_file_is_in_sync_with_the_generator():
    assert MATERIAL_PATH.is_file(), (
        "capability manifest material missing: run "
        "python -m guanlan_v2.orchestration.capability_manifest"
    )
    committed = MATERIAL_PATH.read_bytes()
    regenerated = serialize_capability_manifest(generate_capability_manifest(ct.WW_TOOL_TABLE))
    assert committed == regenerated, (
        "capability manifest material drifted from the generator (most likely "
        "WW_TOOL_TABLE changed): re-run "
        "python -m guanlan_v2.orchestration.capability_manifest"
    )


def test_material_bytes_are_canonical_json():
    committed = MATERIAL_PATH.read_bytes()
    obj = json.loads(committed.decode("utf-8"))
    # canonical: sorted keys, compact separators, no trailing newline.
    assert committed == json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def test_drift_guard_is_red_on_a_byte_tamper():
    m = generate_capability_manifest(ct.WW_TOOL_TABLE)
    good = serialize_capability_manifest(m)
    # tamper: drop a real tool from the first non-empty row.
    rows = list(m.rows)
    for i, row in enumerate(rows):
        if row.real_tools:
            rows[i] = CapabilityInterfaceRow(
                capability_id=row.capability_id,
                real_tools=row.real_tools[1:],
                notes=row.notes,
            )
            break
    tampered = serialize_capability_manifest(
        CapabilityManifest(
            source_tool_count=m.source_tool_count,
            source_tool_digest=m.source_tool_digest,
            rows=tuple(rows),
        )
    )
    assert tampered != good


def test_parse_round_trips_the_material():
    raw = MATERIAL_PATH.read_bytes()
    m = parse_capability_manifest(raw)
    assert serialize_capability_manifest(m) == raw


# --------------------------------------------------------------------------- #
# 承诺-供给 lint (R2 §7.2 第 1 条; TA #557)                                     #
# --------------------------------------------------------------------------- #
def test_lint_passes_when_promise_is_subset_of_supply():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    allowlist = (refs["news"],)
    skill = _skill("- Prefer ww_news_live, then ww_newsradar.")
    assert lint_skill_supply(
        manifest=m, skill_text=skill, capability_allowlist=allowlist
    ) == ()


def test_lint_flags_a_promise_that_exceeds_supply():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    allowlist = (refs["news"],)  # supplies news tools, NOT ww_orderbook
    skill = _skill("- Prefer ww_news_live, then ww_orderbook for depth.")
    issues = lint_skill_supply(
        manifest=m, skill_text=skill, capability_allowlist=allowlist
    )
    assert issues, "a promised tool outside the allowlisted supply must be flagged"
    assert any("ww_orderbook" in i for i in issues)
    assert all(isinstance(i, str) and i.strip() for i in issues)


def test_lint_union_across_allowlisted_capabilities():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    # text.news-style: news + fundamentals covers ww_news_live + ww_f10.
    allowlist = (refs["news"], refs["fundamentals"])
    skill = _skill("- ww_news_live for flow, ww_f10 for corporate facts, ww_newsradar for RSS.")
    assert lint_skill_supply(
        manifest=m, skill_text=skill, capability_allowlist=allowlist
    ) == ()


def test_lint_ignores_non_ww_tokens_and_prose():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    allowlist = (refs["news"],)
    skill = _skill("- ww_news_live, then the Kimi extraction pipeline and human review.")
    assert lint_skill_supply(
        manifest=m, skill_text=skill, capability_allowlist=allowlist
    ) == ()


def test_lint_reads_only_the_data_source_priority_section():
    m = generate_capability_manifest(_live_table())
    refs = _cap_refs()
    allowlist = (refs["news"],)
    # ww_orderbook mentioned only in a LATER (playbook) section — not a promise.
    skill = _skill("- ww_news_live only.", tail="Historically we also tried ww_orderbook.")
    assert lint_skill_supply(
        manifest=m, skill_text=skill, capability_allowlist=allowlist
    ) == ()


def test_lint_empty_allowlist_flags_every_promise():
    m = generate_capability_manifest(_live_table())
    skill = _skill("- ww_news_live is the source.")
    issues = lint_skill_supply(manifest=m, skill_text=skill, capability_allowlist=())
    assert any("ww_news_live" in i for i in issues)
