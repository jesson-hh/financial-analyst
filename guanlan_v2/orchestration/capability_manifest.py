# -*- coding: utf-8 -*-
"""Phase 8 · Task 0b (D11) — the capability-manifest generator + drift guard.

The 27-seat capability↔real-interface mapping material. It answers, for each
Phase-3 data capability a worker may allowlist, *which real ww_ tools* actually
supply it — the map every batch task (Tasks 4-7/10) consumes to author its item-4
capability manifest and the map the 承诺-供给 lint checks a SKILL's ``Data Source
Priority`` promises against.

Single-source doctrine (mirrors ``scripts/gen_agent_interface_doc.py`` /
``tests/test_agent_interface_doc.py``): the rows are **derived**, never hand-typed.
The reviewed ``_METHOD_TOOL_INTENT`` below is the "template" (the intent of which
tools serve which capability); :func:`generate_capability_manifest` intersects it
with the LIVE ``WW_TOOL_TABLE`` so the physical ``real_tools`` are always a subset
of tools that actually exist. The drift guard (``tests/orchestration/
test_capability_manifest.py``) regenerates from the live table and byte-compares
the committed material, so any hand edit or upstream tool-table change fails
HONESTLY with a "re-run generator" message — the R2 §7.1 vaccine against the
glmcp 58≠64 手抄计数腐烂 disease.

Capability ids are bound to the *implemented* Phase-3 refs
(``data.catalog.data_capability_refs``, clause (c)); the generator invents no
capability and grants nothing — it produces mapping material + a lint only.

FROZEN TABLE STATE (HAZARD note): ``WW_TOOL_TABLE`` is dirty on this branch — the
worktree carries ``ww_newsradar`` / ``ww_overseas`` (live in production,
uncommitted here). The material freezes against that worktree state (64 rows =
the 62 committed tools plus that uncommitted pair), and the manifest records the
exact frozen table shape it derives from (``source_tool_count`` +
``source_tool_digest`` over the sorted tool-name projection) so a foreign commit
that changes the table trips the drift guard with concrete numbers, never
silently.

Re-freeze history: sealed at 60 rows, re-generated to 64 after ``b969c93`` added
the four orchestration-router tools (``ww_orchestrate_start`` / ``_status`` /
``_runs``, ``ww_ta_ingest``). None of the four joins ``_METHOD_TOOL_INTENT``:
they are control-plane and ingest surfaces (two of them ``confirm``-gated writes
that open a paid approval door / append to the D7 inbox), not suppliers of any
Phase-3 market-data capability. Mapping one under a data capability would let a
worker's DATA allowlist reach a money-spending door — exactly the 承诺-供给
confusion :func:`lint_skill_supply` exists to catch. The re-freeze therefore moves
``source_tool_count`` / ``source_tool_digest`` only; every row is byte-identical.

Regenerate: ``python -m guanlan_v2.orchestration.capability_manifest``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.catalog import _CRITICAL_HEADING_LINE
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    content_digest,
)
from guanlan_v2.orchestration.refs import CapabilityRef, LogicalId

__all__ = [
    "CapabilityInterfaceRow",
    "CapabilityManifest",
    "CAPABILITY_MANIFEST_MATERIAL_ID",
    "MATERIAL_PATH",
    "generate_capability_manifest",
    "serialize_capability_manifest",
    "parse_capability_manifest",
    "lint_skill_supply",
    "manifest_tool_index",
    "known_capability_ids",
]

#: the material id this manifest publishes as (kind ``"guardrail"``, per the Task 3
#: config-material precedent). Batch tasks bind the guardrail material under this id.
CAPABILITY_MANIFEST_MATERIAL_ID = "guardrail.capability_manifest"

_REPO = Path(__file__).resolve().parents[2]
#: the physical guardrail material: bytes = canonical JSON of the generated manifest.
MATERIAL_PATH = (
    _REPO / "config" / "orchestration" / "materials" / "guardrails" / "capability-manifest.json"
)

#: a real ww_ tool token (lowercase, ``ww_`` prefix) inside a SKILL prose line.
_WW_TOKEN = re.compile(r"\bww_[a-z0-9_]+\b")


# --------------------------------------------------------------------------- #
# The reviewed single-source template: method_id -> candidate real ww tools    #
# --------------------------------------------------------------------------- #
#: The reviewed capability→tool intent, keyed by the implemented Phase-3 data
#: method_id (the capability *id* is derived from ``data_capability_refs``, never
#: re-typed here). The GENERATED ``real_tools`` = this intent ∩ live
#: ``WW_TOOL_TABLE`` names, so a renamed/removed upstream tool changes the material
#: and trips the drift guard. Grounded in the R2 §7.1 逐席映射 + each ww tool's
#: primary data category. ``instrument_names`` (reference method) has no worker
#: data intent and is intentionally absent — it is not a manifest row.
_METHOD_TOOL_INTENT: dict[str, tuple[str, ...]] = {
    "news": (
        "ww_live_text", "ww_news_live", "ww_news_search", "ww_newsradar", "ww_sentiment",
    ),
    "ohlcv": (
        "ww_live_text", "ww_update_data",
    ),
    "indicators": (
        "ww_fundflow", "ww_macro_pulse", "ww_market_tape",
    ),
    "verified_snapshot": (
        "ww_live_text", "ww_market_tape", "ww_orderbook", "ww_overseas", "ww_ticks",
    ),
    "fundamentals": (
        "ww_f10",
    ),
    "signals": (
        "ww_factor_analyze", "ww_model_health", "ww_rescore", "ww_screen_run",
    ),
}

#: one reviewed human note per capability row (the mapping rationale in prose).
_METHOD_NOTES: dict[str, str] = {
    "news": (
        "Realtime + RSS news feeds and the shared sentiment store; ww_live_text "
        "carries per-source news / report / announcement probes."
    ),
    "ohlcv": (
        "OHLCV price bars via the unified live probe (tdx_kline) and incremental "
        "market-data refresh."
    ),
    "indicators": (
        "Derived market indicators (limit-up breadth, sector fund flow, macro "
        "sentiment temperature) — display-only, never trade signals."
    ),
    "verified_snapshot": (
        "Live quote / order-book / tick / overseas snapshots (ww_live_text "
        "realtime_quote probe)."
    ),
    "fundamentals": (
        "F10 structured corporate facts (valuation, share capital, events, LHB, "
        "broker ratings)."
    ),
    "signals": (
        "Model / factor signal reads (v4 screening, rescore overlay, model health, "
        "factor IC) — research / display, never order signals."
    ),
}


# --------------------------------------------------------------------------- #
# Contracts                                                                   #
# --------------------------------------------------------------------------- #
class CapabilityInterfaceRow(DigestModel):
    """One capability → real-interface (真实工具名) mapping row.

    ``capability_id`` is an implemented Phase-3 data capability id (e.g.
    ``cap.data.news``); ``real_tools`` is the sorted, duplicate-free tuple of real
    ww_ tool names (e.g. ``ww_news_live``) that supply it, derived by intersecting
    the reviewed intent with the live tool table (never hand-typed).
    """

    schema_version: Literal["1"] = "1"
    capability_id: LogicalId
    real_tools: tuple[NonEmptyStr, ...]
    notes: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _verify(self) -> "CapabilityInterfaceRow":
        tools = list(self.real_tools)
        if tools != sorted(tools):
            raise ValueError("real_tools must be canonically sorted")
        if len(set(tools)) != len(tools):
            raise ValueError("real_tools must be duplicate-free")
        return self


class CapabilityManifest(DigestModel):
    """The 27-seat allowlist ↔ real-interface mapping material.

    ``rows`` is sorted by ``capability_id`` and duplicate-free. ``source_tool_count``
    + ``source_tool_digest`` (a content digest over the sorted tool-name projection
    of the ``WW_TOOL_TABLE`` the rows were derived from) freeze — and document — the
    exact table state the material was sealed against, so a foreign tool-table change
    trips the drift guard with concrete numbers.
    """

    schema_version: Literal["1"] = "1"
    source_tool_count: NonNegativeInt
    source_tool_digest: DigestHex
    rows: tuple[CapabilityInterfaceRow, ...]

    @model_validator(mode="after")
    def _verify(self) -> "CapabilityManifest":
        ids = [r.capability_id for r in self.rows]
        if ids != sorted(ids):
            raise ValueError("rows must be canonically ordered by capability_id")
        if len(set(ids)) != len(ids):
            raise ValueError("rows must be duplicate-free by capability_id")
        return self

    #: excluded from nothing — every field is authorization/provenance identity.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset()


# --------------------------------------------------------------------------- #
# Derivation                                                                  #
# --------------------------------------------------------------------------- #
def _tool_names(tool_table: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(row["name"]) for row in tool_table))


def _source_digest(tool_table: Sequence[Mapping[str, Any]]) -> DigestHex:
    """Content digest over the sorted tool-name projection the derivation depends on."""
    return content_digest(list(_tool_names(tool_table)))


def generate_capability_manifest(
    tool_table: Sequence[Mapping[str, Any]],
) -> CapabilityManifest:
    """Derive the capability manifest purely from ``tool_table`` (``WW_TOOL_TABLE``).

    For each reviewed data method, the row's ``real_tools`` = the reviewed intent ∩
    the tool names present in ``tool_table`` (sorted, duplicate-free). Capability ids
    are bound to the implemented Phase-3 refs via ``data_capability_refs`` — no
    capability string is re-typed and no new capability is invented. The frozen
    ``source_tool_count`` / ``source_tool_digest`` record the exact table shape.
    """
    # lazy import: keeps module import light (walk-safe) and avoids an import cycle
    # with the data catalog surface.
    from guanlan_v2.orchestration.data.catalog import data_capability_refs

    refs = data_capability_refs()
    present = {str(row["name"]) for row in tool_table}
    rows: list[CapabilityInterfaceRow] = []
    for method_id, intent_tools in _METHOD_TOOL_INTENT.items():
        cap_ref = refs[method_id]  # KeyError here = a reviewed method drifted upstream
        real = tuple(sorted(t for t in intent_tools if t in present))
        rows.append(
            CapabilityInterfaceRow(
                capability_id=cap_ref.id,
                real_tools=real,
                notes=_METHOD_NOTES[method_id],
            )
        )
    rows.sort(key=lambda r: r.capability_id)
    return CapabilityManifest(
        source_tool_count=len(tool_table),
        source_tool_digest=_source_digest(tool_table),
        rows=tuple(rows),
    )


def serialize_capability_manifest(manifest: CapabilityManifest) -> bytes:
    """Serialize to deterministic canonical guardrail-material bytes (Task 3 precedent)."""
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_capability_manifest(raw: bytes) -> CapabilityManifest:
    """Parse committed guardrail bytes into a verified manifest (strict marker material)."""
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("capability manifest material must be a JSON object")
    # reviewed catalog material: JSON arrays become tuples (strict=False), but extra
    # fields, closed literals and the row invariants still re-verify.
    return CapabilityManifest.model_validate(obj, strict=False)


# --------------------------------------------------------------------------- #
# Consumption surface for the batch tasks                                     #
# --------------------------------------------------------------------------- #
def manifest_tool_index(manifest: CapabilityManifest) -> dict[str, tuple[str, ...]]:
    """capability_id -> its ``real_tools`` (the map a batch task reads)."""
    return {row.capability_id: row.real_tools for row in manifest.rows}


def known_capability_ids(manifest: CapabilityManifest) -> frozenset[str]:
    """The set of capability ids the manifest covers (for the invariant-4 sweep)."""
    return frozenset(row.capability_id for row in manifest.rows)


def _promised_tools(skill_text: str) -> tuple[str, ...]:
    """The real ww_ tool names a SKILL promises in its ``Data Source Priority`` section.

    Reads ONLY the canonical ``## ⚠️ CRITICAL: Data Source Priority`` section (up to
    the next ``## `` heading), then scans for ``ww_`` tokens — the AMEND-7 层 2 rule
    that Data Source Priority bullets name 真实工具名. Prose / external pipelines (e.g.
    "Kimi") carry no ``ww_`` token and are ignored.
    """
    lines = skill_text.splitlines()
    body: list[str] = []
    in_section = False
    for line in lines:
        if line.strip() == _CRITICAL_HEADING_LINE:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            body.append(line)
    text = "\n".join(body)
    return tuple(sorted(set(_WW_TOKEN.findall(text))))


def lint_skill_supply(
    *,
    manifest: CapabilityManifest,
    skill_text: str,
    capability_allowlist: tuple[CapabilityRef, ...],
) -> tuple[NonEmptyStr, ...]:
    """承诺-供给 lint (R2 §7.2 第 1 条 machine-enforced; TA #557).

    Every real tool name a SKILL's ``Data Source Priority`` section promises must be
    a subset of the manifest tools reachable through that worker's allowlisted
    capabilities. Each promised tool that no allowlisted capability supplies is
    returned as one issue string (empty tuple = clean). The generator/lint owns no
    grant authority; this is a pure structural check over the reviewed material.
    """
    index = manifest_tool_index(manifest)
    supplied: set[str] = set()
    for cap in capability_allowlist:
        supplied.update(index.get(cap.id, ()))
    allow_ids = sorted({c.id for c in capability_allowlist})
    issues: list[str] = []
    for tool in _promised_tools(skill_text):
        if tool not in supplied:
            issues.append(
                f"skill promises tool {tool!r} but no allowlisted capability supplies "
                f"it (allowlist capabilities: {allow_ids})"
            )
    return tuple(sorted(issues))


# --------------------------------------------------------------------------- #
# Material generation entrypoint                                              #
# --------------------------------------------------------------------------- #
def _live_tool_table() -> Sequence[Mapping[str, Any]]:
    import guanlan_v2.console.tools as ct

    return ct.WW_TOOL_TABLE


def render_material_bytes() -> bytes:
    """The committed material bytes, freshly derived from the live ``WW_TOOL_TABLE``."""
    return serialize_capability_manifest(generate_capability_manifest(_live_tool_table()))


def main() -> None:
    body = render_material_bytes()
    MATERIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MATERIAL_PATH.write_bytes(body)
    print(f"written: {MATERIAL_PATH} ({len(body)} bytes)")


if __name__ == "__main__":
    main()
