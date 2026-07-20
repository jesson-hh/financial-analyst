# -*- coding: utf-8 -*-
"""Phase 8 · the migrated final-worker catalog builders (grown one lane batch at a time).

This module assembles the reviewed :class:`~guanlan_v2.orchestration.catalog.WorkerSpec`
declarations for the 20 new + 5 updated final workers of the D5 final-27 map, plus the
per-batch material manifests and (Task 11) the ``PHASE8_*`` registry/catalog chain. It
is a **consumer** of the Phase-1 catalog ABI and the Phase-3 data-capability refs — it
forks no digest, WorkerSpec model, catalog builder or capability descriptor.

Batch order is frozen (Lane C 文本 → Lane B pv → Lane A quant → 跨切 → Lane D last); this
file's Lane C section ships first. Each builder is a pure function of resolved materials:
``build_<lane>_worker_specs(*, materials)`` indexes the batch's text materials by
``ContentRef`` id and resolves data-capability refs from the implemented Phase-3
``data_capability_refs()`` (never a re-typed capability string).

Reviewed rulings folded into Lane C (FLAGGED in the task report):

* **Allowlist broadening (Task 0b review).** The plan §7.1 single-capability shorthand
  under-supplies two seats; the reviewed corrections are applied so every shipped seat's
  ``lint_skill_supply(...) == ()``: ``text.news`` gains ``cap.data.fundamentals`` (it
  promises ``ww_f10``) and ``text.macro`` gains ``cap.data.indicators`` +
  ``cap.data.verified_snapshot`` (it promises ``ww_macro_pulse`` / ``ww_market_tape`` /
  ``ww_overseas``). No tool is ever stuffed into the wrong manifest row.
* **Pilot reviewed update (clause d).** ``text.sentiment`` changes ONLY two WorkerSpec
  fields: ``skills`` rebinds to the Task-1 relocated ``skill.text.sentiment`` (whose
  bytes are the 交付物② 逐字安装件), and ``guardrail.anti_fabrication`` is added to
  ``guardrail_refs``. Its ``system_prompt_ref`` (``prompt.sentiment``), ``guard.provenance``
  guardrail, FORBIDDEN tool policy, WRITER tier, ``reasoner`` model tier,
  ``(context, market_data)`` read categories, empty inputs and ``SentimentReport@1``
  output are the frozen Phase-2 pilot baseline (unchanged). The roster's design-intent
  columns (critic tier, ``upstream_artifacts``) are NOT retro-applied — clause (d) freezes
  every field this update does not name.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from guanlan_v2.orchestration.catalog import (
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedTextMaterial,
    SkillBinding,
    WorkerSpec,
)
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.data.catalog import data_capability_refs
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef, SchemaRef

__all__ = [
    "TEXT_LANE_WORKER_IDS",
    "text_lane_material_specs",
    "load_text_lane_materials",
    "build_text_worker_specs",
]

_REPO = Path(__file__).resolve().parents[2]
_SKILLS_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MATERIALS = _REPO / "config" / "orchestration" / "materials"
_PROMPTS = _MATERIALS / "prompts"
_GUARDRAILS = _MATERIALS / "guardrails"
_PILOT = _MATERIALS / "phase2-pilot-v1"

_LANE = "text"
_MODES = (DataMode.ONLINE, DataMode.PIT_REPLAY)

TEXT_LANE_WORKER_IDS: tuple[str, ...] = (
    "text.news", "text.sentiment", "text.research_report", "text.policy", "text.macro",
)


# --------------------------------------------------------------------------- #
# Material manifest — the physical bytes this batch owns + the pilot reuse      #
# --------------------------------------------------------------------------- #
class _MaterialSpec(NamedTuple):
    material_id: str
    kind: str
    path: Path


def text_lane_material_specs() -> tuple[_MaterialSpec, ...]:
    """The (id, kind, path) rows Lane C loads — batch-owned files + reused pilot bytes.

    The four new seats' skills/prompts and the three shared guardrails are authored in
    this batch; ``skill.text.sentiment`` is the Task-1 relocated tree file (now the 交付物②
    install); ``prompt.sentiment`` / ``guard.provenance`` are the frozen Phase-2 pilot
    bytes reused verbatim by the ``text.sentiment`` reviewed update (clause d).
    """
    rows: list[_MaterialSpec] = []
    for wid in ("text.news", "text.research_report", "text.policy", "text.macro",
                "text.sentiment"):
        rows.append(_MaterialSpec(f"skill.{wid}", "skill", _SKILLS_TREE / wid / "SKILL.md"))
    for wid in ("text.news", "text.research_report", "text.policy", "text.macro"):
        rows.append(_MaterialSpec(f"prompt.{wid}", "prompt", _PROMPTS / f"{wid}.md"))
    rows.append(_MaterialSpec("prompt.sentiment", "prompt", _PILOT / "prompts" / "sentiment.md"))
    rows.append(_MaterialSpec(
        "guardrail.untrusted_input_isolation", "guardrail",
        _GUARDRAILS / "untrusted-input-isolation.md"))
    rows.append(_MaterialSpec(
        "guardrail.anti_fabrication", "guardrail", _GUARDRAILS / "anti-fabrication.md"))
    rows.append(_MaterialSpec(
        "guardrail.number_provenance", "guardrail", _GUARDRAILS / "number-provenance.md"))
    rows.append(_MaterialSpec(
        "guard.provenance", "guardrail", _PILOT / "guardrails" / "provenance.md"))
    return tuple(rows)


def load_text_lane_materials() -> tuple[ResolvedTextMaterial, ...]:
    """Resolve every Lane C text material to bytes + a content-digest-sealed ref."""
    out: list[ResolvedTextMaterial] = []
    for spec in text_lane_material_specs():
        _ref, mat = build_text_material(
            id=spec.material_id, version="1", kind=spec.kind, raw=spec.path.read_bytes())
        out.append(mat)
    return tuple(out)


# --------------------------------------------------------------------------- #
# The reviewed Lane C worker rows                                              #
# --------------------------------------------------------------------------- #
class _TextWorkerRow(NamedTuple):
    worker_id: str
    persona: str
    tier: Tier
    model_tier: str
    thinking_budget: int
    prompt_id: str
    skill_id: str
    guardrail_ids: tuple[str, ...]
    capability_methods: tuple[str, ...]   # Phase-3 method ids (never re-typed cap strings)
    read_categories: tuple[str, ...]
    output_schema: SchemaRef
    tool_calls: ToolCallRequirement
    inputs: tuple[InputBinding, ...]


_NEWS_DIGEST_REPORT = SchemaRef(name="NewsDigestReport", version="1")

#: The reviewed Lane C roster (frozen-order migration batch 1/5). ``text.sentiment`` is the
#: clause-(d) pilot update: it reuses ``prompt.sentiment`` + ``guard.provenance`` (frozen
#: pilot bytes), rebinds skills to the 交付物② install and adds ``guardrail.anti_fabrication``.
_TEXT_ROWS: tuple[_TextWorkerRow, ...] = (
    _TextWorkerRow(
        worker_id="text.news", persona="A-share news reader", tier=Tier.READER,
        model_tier="fast", thinking_budget=0, prompt_id="prompt.text.news", skill_id="skill.text.news",
        guardrail_ids=("guardrail.anti_fabrication", "guardrail.number_provenance",
                       "guardrail.untrusted_input_isolation"),
        capability_methods=("news", "fundamentals"),
        read_categories=("context", "market_data"),
        output_schema=SchemaRef(name="NewsDigestReport", version="1"),
        tool_calls=ToolCallRequirement.REQUIRED, inputs=()),
    _TextWorkerRow(
        worker_id="text.sentiment", persona="A-share sentiment analyst", tier=Tier.WRITER,
        model_tier="reasoner", thinking_budget=4096, prompt_id="prompt.sentiment", skill_id="skill.text.sentiment",
        guardrail_ids=("guard.provenance", "guardrail.anti_fabrication"),
        capability_methods=(),
        read_categories=("context", "market_data"),
        output_schema=SchemaRef(name="SentimentReport", version="1"),
        tool_calls=ToolCallRequirement.FORBIDDEN, inputs=()),
    _TextWorkerRow(
        worker_id="text.research_report", persona="Research report extractor",
        tier=Tier.READER, model_tier="reasoner", thinking_budget=0,
        prompt_id="prompt.text.research_report", skill_id="skill.text.research_report",
        guardrail_ids=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        capability_methods=("news",),
        read_categories=("context", "market_data", "upstream_artifacts"),
        output_schema=SchemaRef(name="ResearchReportExtract", version="1"),
        tool_calls=ToolCallRequirement.OPTIONAL,
        inputs=(InputBinding(name="news_digest", schema_ref=_NEWS_DIGEST_REPORT,
                             required=False, cardinality="one"),)),
    _TextWorkerRow(
        worker_id="text.policy", persona="A-share policy reader", tier=Tier.READER,
        model_tier="fast", thinking_budget=0, prompt_id="prompt.text.policy", skill_id="skill.text.policy",
        guardrail_ids=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        capability_methods=("news",),
        read_categories=("context", "market_data"),
        output_schema=SchemaRef(name="PolicyReport", version="1"),
        tool_calls=ToolCallRequirement.REQUIRED, inputs=()),
    _TextWorkerRow(
        worker_id="text.macro", persona="Macro pulse reader", tier=Tier.READER,
        model_tier="fast", thinking_budget=0, prompt_id="prompt.text.macro", skill_id="skill.text.macro",
        guardrail_ids=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        capability_methods=("signals", "indicators", "verified_snapshot"),
        read_categories=("context", "market_data"),
        output_schema=SchemaRef(name="MacroPulseReport", version="1"),
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
)


def _text_index(
    materials: tuple[ResolvedTextMaterial, ...],
) -> dict[str, ContentRef]:
    index: dict[str, ContentRef] = {}
    for m in materials:
        if not isinstance(m, ResolvedTextMaterial):
            continue
        index[m.ref.id] = m.ref
    return index


def build_text_worker_specs(
    *, materials: tuple[ResolvedTextMaterial, ...],
) -> tuple[WorkerSpec, ...]:
    """Build the reviewed Lane C final WorkerSpecs from resolved batch materials.

    Prompt/skill/guardrail refs are indexed by id from ``materials``; capability
    allowlist refs are resolved from the implemented Phase-3 ``data_capability_refs()``
    (method id → sealed ``CapabilityRef``), never a re-typed capability string. The
    result is deterministic and order-stable (sorted by worker id).
    """
    text_ix = _text_index(materials)
    cap_refs: dict[str, CapabilityRef] = data_capability_refs()

    def _content(mid: str) -> ContentRef:
        try:
            return text_ix[mid]
        except KeyError:
            raise KeyError(f"missing resolved text material {mid!r} for Lane C") from None

    specs: list[WorkerSpec] = []
    for row in _TEXT_ROWS:
        guardrails = tuple(sorted(
            (_content(g) for g in row.guardrail_ids), key=lambda r: (r.id, r.version)))
        caps = tuple(sorted(
            (cap_refs[m] for m in row.capability_methods), key=lambda c: (c.id, c.version)))
        specs.append(WorkerSpec(
            id=row.worker_id, catalog_role="final", selection_scope="dynamic_allowed",
            lane=_LANE, persona=row.persona, tier=row.tier,
            execution=ExecutionSpec(
                kind=ExecutionKind.LLM, model_tier=row.model_tier,
                thinking_budget=row.thinking_budget),
            system_prompt_ref=_content(row.prompt_id),
            skills=(SkillBinding(skill_ref=_content(row.skill_id)),),
            guardrail_refs=guardrails,
            capability_allowlist=caps,
            read_categories=row.read_categories,
            inputs=row.inputs,
            outputs=(OutputBinding(name="primary", schema_ref=row.output_schema),),
            evidence_policy=EvidencePolicy(
                tool_calls=row.tool_calls, require_input_refs=True,
                require_number_anchors=True, allow_unsourced_numbers=False,
                optional_data_may_degrade=True),
            supported_modes=_MODES,
            can_emit_decision=False, decision_authority="none"))
    return tuple(sorted(specs, key=lambda w: w.id))
