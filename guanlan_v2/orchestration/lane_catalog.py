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
from guanlan_v2.orchestration.pattern_registry import (
    build_pattern_dictionary_material,
    build_seed_pattern_dictionary,
)
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef, SchemaRef

__all__ = [
    "TEXT_LANE_WORKER_IDS",
    "text_lane_material_specs",
    "load_text_lane_materials",
    "build_text_worker_specs",
    "PV_LANE_WORKER_IDS",
    "pv_lane_material_specs",
    "load_pv_lane_materials",
    "build_pv_worker_specs",
    "QUANT_LANE_WORKER_IDS",
    "quant_lane_material_specs",
    "load_quant_lane_materials",
    "build_quant_worker_specs",
    "XCUT_LANE_WORKER_IDS",
    "xcut_lane_material_specs",
    "load_xcut_lane_materials",
    "build_xcut_worker_specs",
    "DECISION_LANE_WORKER_IDS",
    "decision_lane_material_specs",
    "load_decision_lane_materials",
    "build_decision_worker_specs",
]

_REPO = Path(__file__).resolve().parents[2]
_SKILLS_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MATERIALS = _REPO / "config" / "orchestration" / "materials"
_PROMPTS = _MATERIALS / "prompts"
_GUARDRAILS = _MATERIALS / "guardrails"
_HANDLERS = _MATERIALS / "handlers"
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


# =========================================================================== #
# Batch 2 · Lane B 量价几何 (Task 5)                                           #
# =========================================================================== #
# Reviewed rulings folded into Lane B (FLAGGED in the task report):
#
# * Allowlist broadening (Task 0b review). ``pv.microstructure`` promises 五档/逐笔/主力
#   (ww_orderbook / ww_ticks / ww_fundflow), which live under ``cap.data.verified_snapshot``
#   (orderbook + ticks) and ``cap.data.indicators`` (fundflow). Its allowlist is broadened
#   BEYOND the plan's ``get_signal`` shorthand to include those supplying capabilities —
#   mirroring the ``text.macro`` correction — so ``lint_skill_supply(...) == ()`` while the
#   plan's ``cap.data.signals`` shorthand is retained.
# * Pattern-dictionary binding. The three pattern-consuming seats (``pv.price_action`` /
#   ``pv.technical`` / ``pv.curator``) bind the Task-4b ``guardrail.pattern_dictionary``
#   catalog material (content-digest-sealed ``ContentRef``, kind ``guardrail``);
#   ``pv.microstructure`` does not consume the K-line dictionary and does not bind it.
# * #27 ``pv.curator`` is the batch's sole LLM spec and is proposal-only / offline
#   (AMEND-6, same treatment as ``market.factor_miner``): FORBIDDEN tool policy ⇔ empty
#   allowlist (no write capability), ``can_emit_decision=False``, ``critic`` tier, and a
#   ``draft_only`` ``PatternLifecycleProposal`` output. This batch installs only its
#   WorkerSpec + guardrails; the D7 console delivery entry is later runtime wiring.

_PV_LANE = "pv"

PV_LANE_WORKER_IDS: tuple[str, ...] = (
    "pv.price_action", "pv.technical", "pv.microstructure", "pv.curator",
)

#: input schema refs the Lane B chain binds (published by this batch's payloads).
_PA_FEATURE_REPORT = SchemaRef(name="PriceActionFeatureReport", version="1")
_TECHNICAL_REPORT = SchemaRef(name="TechnicalReport", version="1")


def pv_lane_material_specs() -> tuple[_MaterialSpec, ...]:
    """The (id, kind, path) rows Lane B loads from disk — batch-owned files.

    The four seats' skills; the two LLM seats' prompts; the two deterministic seats'
    handler modules; the two batch-new guardrails (``external_ta_ingest`` /
    ``revision_throttle``) + the shared ``draft_only_advisory``; and the two reused shared
    guardrails (``number_provenance`` / ``untrusted_input_isolation``). The Task-4b pattern
    dictionary is a BUILT catalog material (added in :func:`load_pv_lane_materials`), not a
    file row here.
    """
    rows: list[_MaterialSpec] = []
    for wid in ("pv.price_action", "pv.technical", "pv.microstructure", "pv.curator"):
        rows.append(_MaterialSpec(f"skill.{wid}", "skill", _SKILLS_TREE / wid / "SKILL.md"))
    for wid in ("pv.technical", "pv.curator"):
        rows.append(_MaterialSpec(f"prompt.{wid}", "prompt", _PROMPTS / f"{wid}.md"))
    for wid in ("pv.price_action", "pv.microstructure"):
        rows.append(_MaterialSpec(f"handler.{wid}", "handler", _HANDLERS / f"{wid}.py"))
    rows.append(_MaterialSpec(
        "guardrail.external_ta_ingest", "guardrail", _GUARDRAILS / "external-ta-ingest.md"))
    rows.append(_MaterialSpec(
        "guardrail.revision_throttle", "guardrail", _GUARDRAILS / "revision-throttle.md"))
    rows.append(_MaterialSpec(
        "guardrail.draft_only_advisory", "guardrail", _GUARDRAILS / "draft-only-advisory.md"))
    rows.append(_MaterialSpec(
        "guardrail.number_provenance", "guardrail", _GUARDRAILS / "number-provenance.md"))
    rows.append(_MaterialSpec(
        "guardrail.untrusted_input_isolation", "guardrail",
        _GUARDRAILS / "untrusted-input-isolation.md"))
    return tuple(rows)


def load_pv_lane_materials() -> tuple[ResolvedTextMaterial, ...]:
    """Resolve every Lane B material to bytes + a content-digest-sealed ref.

    File-owned rows are hashed through the single reviewed ``build_text_material`` helper;
    the Task-4b pattern dictionary is resolved through its owner's
    ``build_pattern_dictionary_material`` (its sealed ``guardrail.pattern_dictionary``
    ContentRef, digest ``3f24f8a1…``) so this batch never re-hashes the dictionary by hand.
    """
    out: list[ResolvedTextMaterial] = []
    for spec in pv_lane_material_specs():
        _ref, mat = build_text_material(
            id=spec.material_id, version="1", kind=spec.kind, raw=spec.path.read_bytes())
        out.append(mat)
    _pd_ref, pd_mat = build_pattern_dictionary_material(build_seed_pattern_dictionary())
    out.append(pd_mat)
    return tuple(out)


class _PVWorkerRow(NamedTuple):
    worker_id: str
    persona: str
    tier: Tier
    exec_kind: ExecutionKind
    model_tier: str | None          # None ⇔ deterministic
    thinking_budget: int | None     # None ⇔ deterministic
    handler_id: str | None          # set ⇔ deterministic
    prompt_id: str | None           # set ⇔ LLM
    skill_id: str
    guardrail_ids: tuple[str, ...]
    capability_methods: tuple[str, ...]   # Phase-3 method ids (never re-typed cap strings)
    read_categories: tuple[str, ...]
    output_schema: SchemaRef
    tool_calls: ToolCallRequirement
    inputs: tuple[InputBinding, ...]


#: The reviewed Lane B roster (frozen-order migration batch 2/5). Deterministic seats
#: carry a ``handler_ref`` + no tier; ``pv.curator`` (#27) is the sole LLM spec.
_PV_ROWS: tuple[_PVWorkerRow, ...] = (
    _PVWorkerRow(
        worker_id="pv.price_action", persona="Price-action geometry reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.pv.price_action", prompt_id=None,
        skill_id="skill.pv.price_action",
        guardrail_ids=("guardrail.number_provenance", "guardrail.pattern_dictionary"),
        capability_methods=("ohlcv",),
        read_categories=("market_data",),
        output_schema=SchemaRef(name="PriceActionFeatureReport", version="1"),
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
    _PVWorkerRow(
        worker_id="pv.technical", persona="Technical indicator reader",
        tier=Tier.READER, exec_kind=ExecutionKind.LLM,
        model_tier="reasoner", thinking_budget=0,
        handler_id=None, prompt_id="prompt.pv.technical",
        skill_id="skill.pv.technical",
        guardrail_ids=("guardrail.number_provenance", "guardrail.untrusted_input_isolation",
                       "guardrail.pattern_dictionary"),
        capability_methods=("verified_snapshot", "indicators"),
        read_categories=("context", "market_data"),
        output_schema=SchemaRef(name="TechnicalReport", version="1"),
        tool_calls=ToolCallRequirement.REQUIRED,
        inputs=(InputBinding(name="price_action", schema_ref=_PA_FEATURE_REPORT,
                             required=False, cardinality="one"),)),
    _PVWorkerRow(
        worker_id="pv.microstructure", persona="Microstructure projection reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.pv.microstructure", prompt_id=None,
        skill_id="skill.pv.microstructure",
        guardrail_ids=("guardrail.number_provenance",),
        # Task-0b broadening: 五档/逐笔 (verified_snapshot) + 主力 (indicators) supply the
        # promised ww_orderbook/ww_ticks/ww_fundflow; the get_signal shorthand is retained.
        capability_methods=("signals", "verified_snapshot", "indicators"),
        read_categories=("market_data",),
        output_schema=SchemaRef(name="MicrostructureReport", version="1"),
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
    _PVWorkerRow(
        worker_id="pv.curator",
        persona=("Offline K-line pattern curator (#27, AMEND-6) — draft-only, zero "
                 "trading authority"),
        tier=Tier.CRITIC, exec_kind=ExecutionKind.LLM,
        model_tier="reasoner", thinking_budget=0,
        handler_id=None, prompt_id="prompt.pv.curator",
        skill_id="skill.pv.curator",
        guardrail_ids=("guardrail.untrusted_input_isolation", "guardrail.number_provenance",
                       "guardrail.draft_only_advisory", "guardrail.external_ta_ingest",
                       "guardrail.revision_throttle", "guardrail.pattern_dictionary"),
        capability_methods=(),
        read_categories=("context", "upstream_artifacts"),
        output_schema=SchemaRef(name="PatternLifecycleProposal", version="1"),
        tool_calls=ToolCallRequirement.FORBIDDEN,
        inputs=(InputBinding(name="price_action", schema_ref=_PA_FEATURE_REPORT,
                             required=False, cardinality="one"),
                InputBinding(name="technical", schema_ref=_TECHNICAL_REPORT,
                             required=False, cardinality="one"))),
)


def build_pv_worker_specs(
    *, materials: tuple[ResolvedTextMaterial, ...],
) -> tuple[WorkerSpec, ...]:
    """Build the reviewed Lane B final WorkerSpecs from resolved batch materials.

    Skill/prompt/handler/guardrail refs (including the Task-4b pattern dictionary) are
    indexed by id from ``materials``; capability allowlist refs resolve from the
    implemented Phase-3 ``data_capability_refs()`` (method id → sealed ``CapabilityRef``),
    never a re-typed capability string. Deterministic seats bind a ``handler_ref`` and no
    tier; the LLM seats bind a prompt + model tier. Order-stable (sorted by worker id).
    """
    pv_ix = _text_index(materials)
    cap_refs: dict[str, CapabilityRef] = data_capability_refs()

    def _content(mid: str) -> ContentRef:
        try:
            return pv_ix[mid]
        except KeyError:
            raise KeyError(f"missing resolved text material {mid!r} for Lane B") from None

    specs: list[WorkerSpec] = []
    for row in _PV_ROWS:
        guardrails = tuple(sorted(
            (_content(g) for g in row.guardrail_ids), key=lambda r: (r.id, r.version)))
        caps = tuple(sorted(
            (cap_refs[m] for m in row.capability_methods), key=lambda c: (c.id, c.version)))
        if row.exec_kind is ExecutionKind.DETERMINISTIC:
            execution = ExecutionSpec(
                kind=ExecutionKind.DETERMINISTIC, handler_ref=_content(row.handler_id))
            prompt_ref: ContentRef | None = None
        else:
            execution = ExecutionSpec(
                kind=ExecutionKind.LLM, model_tier=row.model_tier,
                thinking_budget=row.thinking_budget)
            prompt_ref = _content(row.prompt_id)
        specs.append(WorkerSpec(
            id=row.worker_id, catalog_role="final", selection_scope="dynamic_allowed",
            lane=_PV_LANE, persona=row.persona, tier=row.tier, execution=execution,
            system_prompt_ref=prompt_ref,
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


# =========================================================================== #
# Batch 3 · Lane A 量化 (Task 6)                                               #
# =========================================================================== #
# Reviewed rulings folded into Lane A (FLAGGED in the task report):
#
# * No allowlist broadening is needed. Each producer's ``Data Source Priority`` promises
#   only tools that its plan-shorthand capability already supplies: ``quant.factor`` /
#   ``quant.model`` promise ww_factor_analyze / ww_screen_run / ww_model_health (all under
#   ``cap.data.signals``); ``quant.fundamentals`` promises ww_f10 (``cap.data.fundamentals``).
#   The three FORBIDDEN seats (``quant.backtest`` / ``quant.factor_miner`` / ``quant.curator``)
#   name ZERO ww_ tools in their DSP — they read upstream artifacts / doctrine. Every shipped
#   seat's ``lint_skill_supply(...) == ()``.
# * The 5 producers are DETERMINISTIC (handler_ref + no tier, zero LLM reservations). Their
#   handlers DELEGATE per clause (h): the named legacy sources (``screen/factor_ic.py`` /
#   ``screen/model_registry.py`` / ``screen/factor_vintage.py`` / TA ``tier2/fundamental_
#   analyst.py`` / ``research/loop.py``) are NOT import-safe pure functions — their compute
#   paths need the engine loader / artifact-parquet IO / a full research-loop orchestration —
#   so each handler is a self-contained stdlib projection over the legacy TYPED OUTPUT (the
#   impure-fallback branch of clause (h)), with the honesty rails encoded (回看≠OOS,
#   stale_days 不清零, absent verdict stays None, passed_gate 不升级).
# * #26 ``quant.curator`` is the batch's sole LLM spec and is proposal-only / offline
#   (AMEND-5, same treatment as ``market.factor_miner`` #25 / ``pv.curator`` #27): FORBIDDEN
#   tool policy ⇔ empty allowlist (no write capability), ``can_emit_decision=False``,
#   ``critic`` tier, and a ``draft_only`` ``FactorLifecycleProposal`` output. Its 过拟合红线
#   五条 are bound via ``guardrail.draft_only_advisory`` + ``guardrail.revision_throttle``
#   (D6: 月频因子 N=3; the material defers admission to the Phase-4 governor + Phase-5
#   matured-case grader). This batch installs only its WorkerSpec + reused guardrails.

_QUANT_LANE = "quant"

QUANT_LANE_WORKER_IDS: tuple[str, ...] = (
    "quant.factor", "quant.model", "quant.backtest", "quant.fundamentals",
    "quant.factor_miner", "quant.curator",
)

#: input schema refs the Lane A chain binds (published by this batch's payloads).
_FACTOR_IC_REPORT = SchemaRef(name="FactorICReport", version="1")
_MODEL_PREDICTION_REPORT = SchemaRef(name="ModelPredictionReport", version="1")
_BACKTEST_EVIDENCE_REPORT = SchemaRef(name="BacktestEvidenceReport", version="1")


def quant_lane_material_specs() -> tuple[_MaterialSpec, ...]:
    """The (id, kind, path) rows Lane A loads from disk — batch-owned + reused files.

    The six seats' skills; the one LLM seat's prompt (``quant.curator``); the five
    deterministic seats' handler modules; and the three reused shared guardrails
    (``number_provenance`` — all six; ``draft_only_advisory`` — the two draft-only seats;
    ``revision_throttle`` — the curator). The guardrail files are shared across lanes and
    already on disk (Task 4/5); this batch references them, it does not recreate them.
    """
    rows: list[_MaterialSpec] = []
    for wid in QUANT_LANE_WORKER_IDS:
        rows.append(_MaterialSpec(f"skill.{wid}", "skill", _SKILLS_TREE / wid / "SKILL.md"))
    rows.append(_MaterialSpec("prompt.quant.curator", "prompt", _PROMPTS / "quant.curator.md"))
    for wid in ("quant.factor", "quant.model", "quant.backtest", "quant.fundamentals",
                "quant.factor_miner"):
        rows.append(_MaterialSpec(f"handler.{wid}", "handler", _HANDLERS / f"{wid}.py"))
    rows.append(_MaterialSpec(
        "guardrail.number_provenance", "guardrail", _GUARDRAILS / "number-provenance.md"))
    rows.append(_MaterialSpec(
        "guardrail.draft_only_advisory", "guardrail", _GUARDRAILS / "draft-only-advisory.md"))
    rows.append(_MaterialSpec(
        "guardrail.revision_throttle", "guardrail", _GUARDRAILS / "revision-throttle.md"))
    return tuple(rows)


def load_quant_lane_materials() -> tuple[ResolvedTextMaterial, ...]:
    """Resolve every Lane A material to bytes + a content-digest-sealed ref."""
    out: list[ResolvedTextMaterial] = []
    for spec in quant_lane_material_specs():
        _ref, mat = build_text_material(
            id=spec.material_id, version="1", kind=spec.kind, raw=spec.path.read_bytes())
        out.append(mat)
    return tuple(out)


class _QuantWorkerRow(NamedTuple):
    worker_id: str
    persona: str
    tier: Tier
    exec_kind: ExecutionKind
    model_tier: str | None          # None ⇔ deterministic
    thinking_budget: int | None     # None ⇔ deterministic
    handler_id: str | None          # set ⇔ deterministic
    prompt_id: str | None           # set ⇔ LLM
    skill_id: str
    guardrail_ids: tuple[str, ...]
    capability_methods: tuple[str, ...]   # Phase-3 method ids (never re-typed cap strings)
    read_categories: tuple[str, ...]
    output_schema: SchemaRef
    tool_calls: ToolCallRequirement
    inputs: tuple[InputBinding, ...]


#: The reviewed Lane A roster (frozen-order migration batch 3/5). The 5 producers are
#: deterministic (handler_ref + no tier); ``quant.curator`` (#26) is the sole LLM spec.
_QUANT_ROWS: tuple[_QuantWorkerRow, ...] = (
    _QuantWorkerRow(
        worker_id="quant.factor", persona="Factor IC reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.quant.factor", prompt_id=None,
        skill_id="skill.quant.factor",
        guardrail_ids=("guardrail.number_provenance",),
        capability_methods=("signals",),
        read_categories=("market_data",),
        output_schema=_FACTOR_IC_REPORT,
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
    _QuantWorkerRow(
        worker_id="quant.model", persona="Model prediction reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.quant.model", prompt_id=None,
        skill_id="skill.quant.model",
        guardrail_ids=("guardrail.number_provenance",),
        capability_methods=("signals",),
        read_categories=("market_data",),
        output_schema=_MODEL_PREDICTION_REPORT,
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
    _QuantWorkerRow(
        worker_id="quant.backtest", persona="Backtest evidence reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.quant.backtest", prompt_id=None,
        skill_id="skill.quant.backtest",
        guardrail_ids=("guardrail.number_provenance",),
        capability_methods=(),
        read_categories=("upstream_artifacts",),
        output_schema=_BACKTEST_EVIDENCE_REPORT,
        tool_calls=ToolCallRequirement.FORBIDDEN,
        inputs=(InputBinding(name="factor_ic", schema_ref=_FACTOR_IC_REPORT,
                             required=True, cardinality="one"),
                InputBinding(name="model_predictions", schema_ref=_MODEL_PREDICTION_REPORT,
                             required=False, cardinality="one"))),
    _QuantWorkerRow(
        worker_id="quant.fundamentals", persona="Fundamentals reader",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.quant.fundamentals", prompt_id=None,
        skill_id="skill.quant.fundamentals",
        guardrail_ids=("guardrail.number_provenance",),
        capability_methods=("fundamentals",),
        read_categories=("market_data",),
        output_schema=SchemaRef(name="FundamentalsReport", version="1"),
        tool_calls=ToolCallRequirement.OPTIONAL, inputs=()),
    _QuantWorkerRow(
        worker_id="quant.factor_miner", persona="Mined-factor draft producer",
        tier=Tier.READER, exec_kind=ExecutionKind.DETERMINISTIC,
        model_tier=None, thinking_budget=None,
        handler_id="handler.quant.factor_miner", prompt_id=None,
        skill_id="skill.quant.factor_miner",
        guardrail_ids=("guardrail.draft_only_advisory", "guardrail.number_provenance"),
        capability_methods=(),
        read_categories=("upstream_artifacts",),
        output_schema=SchemaRef(name="MinedFactorDraft", version="1"),
        tool_calls=ToolCallRequirement.FORBIDDEN,
        inputs=(InputBinding(name="factor_ic", schema_ref=_FACTOR_IC_REPORT,
                             required=False, cardinality="one"),)),
    _QuantWorkerRow(
        worker_id="quant.curator",
        persona=("Offline factor curator (#26, AMEND-5) — draft-only, zero trading "
                 "authority"),
        tier=Tier.CRITIC, exec_kind=ExecutionKind.LLM,
        model_tier="reasoner", thinking_budget=0,
        handler_id=None, prompt_id="prompt.quant.curator",
        skill_id="skill.quant.curator",
        guardrail_ids=("guardrail.draft_only_advisory", "guardrail.number_provenance",
                       "guardrail.revision_throttle"),
        capability_methods=(),
        read_categories=("context", "upstream_artifacts"),
        output_schema=SchemaRef(name="FactorLifecycleProposal", version="1"),
        tool_calls=ToolCallRequirement.FORBIDDEN,
        inputs=(InputBinding(name="factor_ic", schema_ref=_FACTOR_IC_REPORT,
                             required=False, cardinality="one"),
                InputBinding(name="backtest_evidence", schema_ref=_BACKTEST_EVIDENCE_REPORT,
                             required=False, cardinality="one"))),
)


def build_quant_worker_specs(
    *, materials: tuple[ResolvedTextMaterial, ...],
) -> tuple[WorkerSpec, ...]:
    """Build the reviewed Lane A final WorkerSpecs from resolved batch materials.

    Skill/prompt/handler/guardrail refs are indexed by id from ``materials``; capability
    allowlist refs resolve from the implemented Phase-3 ``data_capability_refs()`` (method
    id → sealed ``CapabilityRef``), never a re-typed capability string. Deterministic seats
    bind a ``handler_ref`` and no tier; the sole LLM seat binds a prompt + model tier.
    Order-stable (sorted by worker id).
    """
    quant_ix = _text_index(materials)
    cap_refs: dict[str, CapabilityRef] = data_capability_refs()

    def _content(mid: str) -> ContentRef:
        try:
            return quant_ix[mid]
        except KeyError:
            raise KeyError(f"missing resolved text material {mid!r} for Lane A") from None

    specs: list[WorkerSpec] = []
    for row in _QUANT_ROWS:
        guardrails = tuple(sorted(
            (_content(g) for g in row.guardrail_ids), key=lambda r: (r.id, r.version)))
        caps = tuple(sorted(
            (cap_refs[m] for m in row.capability_methods), key=lambda c: (c.id, c.version)))
        if row.exec_kind is ExecutionKind.DETERMINISTIC:
            execution = ExecutionSpec(
                kind=ExecutionKind.DETERMINISTIC, handler_ref=_content(row.handler_id))
            prompt_ref: ContentRef | None = None
        else:
            execution = ExecutionSpec(
                kind=ExecutionKind.LLM, model_tier=row.model_tier,
                thinking_budget=row.thinking_budget)
            prompt_ref = _content(row.prompt_id)
        specs.append(WorkerSpec(
            id=row.worker_id, catalog_role="final", selection_scope="dynamic_allowed",
            lane=_QUANT_LANE, persona=row.persona, tier=row.tier, execution=execution,
            system_prompt_ref=prompt_ref,
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


# =========================================================================== #
# Batch 4 · 跨切 xcut (Task 7)                                                 #
# =========================================================================== #
# Reviewed rulings folded into the xcut batch (FLAGGED in the task report):
#
# * Lane literal is ``"xcut"``. The Task-0 D5 worker map's ``cross`` string is documentation
#   ONLY and never appears in any WorkerSpec (a ``cross`` lane is not even in the Lane
#   Literal — ``Lane = {market, quant, pv, text, decision, xcut}``); the firewall test
#   asserts no shipped spec contains the ``cross`` substring.
# * Both seats are DETERMINISTIC (handler_ref + no tier, zero LLM reservations) and are the
#   two cross-cut FINAL critics. Their handlers bind per clause (h):
#   ``handler.x.number_critic`` binds the import-safe PURE honesty spine DIRECTLY (branch-1,
#   like ``pv.price_action`` binds ``compute_pa_features``); ``handler.x.quality_gate`` is a
#   self-contained stdlib projection over the wired reports' honesty channels (the
#   impure-fallback branch — ``datafeed.health.collect_data_health`` reads live freshness
#   snapshots, not an import-safe pure function).
# * Both are FORBIDDEN ⇔ empty allowlist (structural: no tool ⇒ no write capability),
#   ``can_emit_decision=False``, and name ZERO ww_ tools in their DSP — they read upstream
#   artifacts only. Every shipped seat's ``lint_skill_supply(...) == ()``.
# * Per-seat EvidencePolicy ``require_number_anchors``: ``x.quality_gate`` = ``True``;
#   ``x.number_critic`` = ``False`` — it PRODUCES the anchor verdicts, so it is not itself
#   anchored (CONTROLLER RULING (a): ``require_number_anchors`` is subsumed by
#   ``allow_unsourced_numbers`` in @1; the contradictory True+True combo is never set).
# * Output schemas: ``x.quality_gate`` → this batch's ``DataQualityGrade@1``;
#   ``x.number_critic`` → Task 2's ``HonestyReport@1`` (owned by ``honesty.py``). The input
#   SchemaRefs bind by name+version only — the not-yet-defined Lane-D decision payloads
#   (``BullCase`` / ``BearCase`` / ``ResearchPlan`` / ``PortfolioDecision``) are forward
#   references (dependency injection uses exact SchemaRef equality at wiring time, spec.py
#   999-1000); this batch introduces no premature coupling.

_XCUT_LANE = "xcut"

XCUT_LANE_WORKER_IDS: tuple[str, ...] = (
    "x.quality_gate", "x.number_critic",
)

#: input schema refs the xcut chain binds (published by other lanes / Phase 8 decision layer).
_NEWS_DIGEST = SchemaRef(name="NewsDigestReport", version="1")
_MACRO_PULSE = SchemaRef(name="MacroPulseReport", version="1")
_MICROSTRUCTURE = SchemaRef(name="MicrostructureReport", version="1")
_MODEL_PREDICTION = SchemaRef(name="ModelPredictionReport", version="1")
_BULL_CASE = SchemaRef(name="BullCase", version="1")
_BEAR_CASE = SchemaRef(name="BearCase", version="1")
_RESEARCH_PLAN = SchemaRef(name="ResearchPlan", version="1")
_PORTFOLIO_DECISION = SchemaRef(name="PortfolioDecision", version="1")
_TECHNICAL = SchemaRef(name="TechnicalReport", version="1")
_FUNDAMENTALS = SchemaRef(name="FundamentalsReport", version="1")


def xcut_lane_material_specs() -> tuple[_MaterialSpec, ...]:
    """The (id, kind, path) rows the xcut batch loads from disk — batch-owned + reused files.

    The two seats' skills; the two deterministic seats' handler modules; and the two reused
    shared guardrails (``number_provenance`` — both seats; ``untrusted_input_isolation`` — the
    number critic, binding the FSI 不可信输入隔离 doctrine). The guardrail files are shared
    across lanes and already on disk (Task 4/5); this batch references them, not recreates.
    """
    rows: list[_MaterialSpec] = []
    for wid in XCUT_LANE_WORKER_IDS:
        rows.append(_MaterialSpec(f"skill.{wid}", "skill", _SKILLS_TREE / wid / "SKILL.md"))
    for wid in XCUT_LANE_WORKER_IDS:
        rows.append(_MaterialSpec(f"handler.{wid}", "handler", _HANDLERS / f"{wid}.py"))
    rows.append(_MaterialSpec(
        "guardrail.number_provenance", "guardrail", _GUARDRAILS / "number-provenance.md"))
    rows.append(_MaterialSpec(
        "guardrail.untrusted_input_isolation", "guardrail",
        _GUARDRAILS / "untrusted-input-isolation.md"))
    return tuple(rows)


def load_xcut_lane_materials() -> tuple[ResolvedTextMaterial, ...]:
    """Resolve every xcut material to bytes + a content-digest-sealed ref."""
    out: list[ResolvedTextMaterial] = []
    for spec in xcut_lane_material_specs():
        _ref, mat = build_text_material(
            id=spec.material_id, version="1", kind=spec.kind, raw=spec.path.read_bytes())
        out.append(mat)
    return tuple(out)


class _XcutWorkerRow(NamedTuple):
    worker_id: str
    persona: str
    tier: Tier
    handler_id: str
    skill_id: str
    guardrail_ids: tuple[str, ...]
    read_categories: tuple[str, ...]
    output_schema: SchemaRef
    inputs: tuple[InputBinding, ...]
    require_number_anchors: bool   # per-seat: quality_gate True; number_critic False


#: The reviewed xcut roster (frozen-order migration batch 4/5). Both seats are deterministic
#: cross-cut FINAL critics (handler_ref + no tier); both FORBIDDEN ⇔ empty allowlist.
_XCUT_ROWS: tuple[_XcutWorkerRow, ...] = (
    _XcutWorkerRow(
        worker_id="x.quality_gate",
        persona=("Cross-cut data-quality ABCDF gate (xcut) — weakest-link, zero trading "
                 "authority"),
        tier=Tier.CRITIC, handler_id="handler.x.quality_gate",
        skill_id="skill.x.quality_gate",
        guardrail_ids=("guardrail.number_provenance",),
        read_categories=("upstream_artifacts", "market_data"),
        output_schema=SchemaRef(name="DataQualityGrade", version="1"),
        inputs=(InputBinding(name="news_digest", schema_ref=_NEWS_DIGEST,
                             required=False, cardinality="one"),
                InputBinding(name="macro_pulse", schema_ref=_MACRO_PULSE,
                             required=False, cardinality="one"),
                InputBinding(name="microstructure", schema_ref=_MICROSTRUCTURE,
                             required=False, cardinality="one"),
                InputBinding(name="model_predictions", schema_ref=_MODEL_PREDICTION,
                             required=False, cardinality="one")),
        require_number_anchors=True),
    _XcutWorkerRow(
        worker_id="x.number_critic",
        persona=("Cross-cut number-provenance and honesty critic (xcut) — zero trading "
                 "authority"),
        tier=Tier.CRITIC, handler_id="handler.x.number_critic",
        skill_id="skill.x.number_critic",
        guardrail_ids=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        read_categories=("upstream_artifacts",),
        # Task 2's HonestyReport (owned by honesty.py, registered by Task 11).
        output_schema=SchemaRef(name="HonestyReport", version="1"),
        inputs=(InputBinding(name="bull_case", schema_ref=_BULL_CASE,
                             required=False, cardinality="one"),
                InputBinding(name="bear_case", schema_ref=_BEAR_CASE,
                             required=False, cardinality="one"),
                InputBinding(name="research_plan", schema_ref=_RESEARCH_PLAN,
                             required=False, cardinality="one"),
                InputBinding(name="portfolio_decision", schema_ref=_PORTFOLIO_DECISION,
                             required=False, cardinality="one"),
                InputBinding(name="technical", schema_ref=_TECHNICAL,
                             required=False, cardinality="one"),
                InputBinding(name="fundamentals", schema_ref=_FUNDAMENTALS,
                             required=False, cardinality="one")),
        # it PRODUCES the anchor verdicts, so it is not itself anchored (ruling (a)).
        require_number_anchors=False),
)


def build_xcut_worker_specs(
    *, materials: tuple[ResolvedTextMaterial, ...],
) -> tuple[WorkerSpec, ...]:
    """Build the reviewed 跨切 xcut final WorkerSpecs from resolved batch materials.

    Skill/handler/guardrail refs are indexed by id from ``materials``. Both seats are
    DETERMINISTIC (a bound ``handler_ref``, no model tier, no prompt) and FORBIDDEN ⇔ empty
    allowlist (no tool ⇒ no write capability). ``require_number_anchors`` is per-seat
    (``x.number_critic`` = ``False`` — it produces the anchor verdicts). Order-stable
    (sorted by worker id).
    """
    xcut_ix = _text_index(materials)

    def _content(mid: str) -> ContentRef:
        try:
            return xcut_ix[mid]
        except KeyError:
            raise KeyError(f"missing resolved text material {mid!r} for xcut") from None

    specs: list[WorkerSpec] = []
    for row in _XCUT_ROWS:
        guardrails = tuple(sorted(
            (_content(g) for g in row.guardrail_ids), key=lambda r: (r.id, r.version)))
        specs.append(WorkerSpec(
            id=row.worker_id, catalog_role="final", selection_scope="dynamic_allowed",
            lane=_XCUT_LANE, persona=row.persona, tier=row.tier,
            execution=ExecutionSpec(
                kind=ExecutionKind.DETERMINISTIC, handler_ref=_content(row.handler_id)),
            system_prompt_ref=None,
            skills=(SkillBinding(skill_ref=_content(row.skill_id)),),
            guardrail_refs=guardrails,
            capability_allowlist=(),
            read_categories=row.read_categories,
            inputs=row.inputs,
            outputs=(OutputBinding(name="primary", schema_ref=row.output_schema),),
            evidence_policy=EvidencePolicy(
                tool_calls=ToolCallRequirement.FORBIDDEN, require_input_refs=True,
                require_number_anchors=row.require_number_anchors,
                allow_unsourced_numbers=False, optional_data_may_degrade=True),
            supported_modes=_MODES,
            can_emit_decision=False, decision_authority="none"))
    return tuple(sorted(specs, key=lambda w: w.id))


# =========================================================================== #
# Batch 5 · Lane D 决策/风控 (Task 10) — the FINAL migration batch             #
# =========================================================================== #
# Reviewed rulings folded into Lane D (FLAGGED in the task report):
#
# * The 4 NEW seats (``dec.bull`` / ``dec.bear`` / ``dec.risk_debate`` / ``dec.trader``) are
#   pool-fed LLM critics/writers: FORBIDDEN tool policy ⇔ empty allowlist (R2 §8: Lane D reads
#   upstream artifacts, never calls a tool), require_input_refs=True, allow_unsourced_numbers
#   =False. Every shipped seat's ``lint_skill_supply(...) == ()`` trivially (no ww_ promise, no
#   capability). Debate seats use only ``fast``/``reasoner`` tiers (invariant 5).
# * The 2 PILOT updates (``dec.research_mgr`` / ``dec.pm``) are clause-(d) surgical model_copies
#   of the FROZEN Phase-7 baseline — they change ONLY the WorkerSpec fields the reviewed update
#   names, never the pilot's evidence policy, capability allowlist, execution or persona. The
#   roster's design-intent columns (e.g. research_mgr's 2-col read_categories) are NOT
#   retro-applied. ``dec.pm`` keeps its ``cap.ashare_constraint_check`` allowlist + optional tool
#   policy (clause d freezes them); the "every Lane-D spec FORBIDDEN" wording describes the 4 NEW
#   seats — the pilots are frozen (text.sentiment precedent, Task 4). The tree SKILL bytes are the
#   交付物③ 逐字安装件 (a new material version ⇒ new digest; the Phase-7 catalog still binds the
#   pilot digest until Task 11 — the sanctioned SUPERSEDED_IN_PHASE8 transitional state).
# * ``dec.pm`` is asserted ``reasoner_deep`` (the ONLY one across the whole lane_catalog — the
#   pilot already carried it; the update asserts, does not change it → execution stays frozen).
# * ``dec.trader`` emits Phase 6's ``PortfolioTargetProposal@1`` — bound by SchemaRef NAME only;
#   no Phase-8 module defines a model by that name (invariant 2). It has no order/signal/intent
#   capability anywhere (guardrail.advisory_shadow_only + FORBIDDEN ⇔ empty allowlist).
# * ``dec.risk_debate`` binds ``params_schema_ref = RiskDebateParams@1`` — the single WorkerSpec
#   is instantiated as multiple PlanNodes whose ``params.stance_role`` seat assignment is
#   validated against the schema (per-instance seat; fixture-proven in test_lane_batch_decision).

_DEC_LANE = "decision"

DECISION_LANE_WORKER_IDS: tuple[str, ...] = (
    "dec.bull", "dec.bear", "dec.research_mgr", "dec.risk_debate", "dec.pm", "dec.trader",
)

#: schema refs the Lane D chain binds (published by this batch's payloads, Phase 1/2/6, Task 9,
#: and the Task-9b decision-input faces). Bound by name+version only — dependency injection uses
#: exact SchemaRef equality at wiring time (spec.py 999-1000).
_BULL_CASE_D = SchemaRef(name="BullCase", version="1")
_BEAR_CASE_D = SchemaRef(name="BearCase", version="1")
_RISK_STANCE = SchemaRef(name="RiskDebateStance", version="1")
_RISK_PARAMS = SchemaRef(name="RiskDebateParams", version="1")
_FUNDAMENTALS_D = SchemaRef(name="FundamentalsReport", version="1")
_TECHNICAL_D = SchemaRef(name="TechnicalReport", version="1")
_SENTIMENT_D = SchemaRef(name="SentimentReport", version="1")
_NEWS_DIGEST_D = SchemaRef(name="NewsDigestReport", version="1")
_RESEARCH_PLAN_D = SchemaRef(name="ResearchPlan", version="1")
_PORTFOLIO_DECISION_D = SchemaRef(name="PortfolioDecision", version="1")
_PORTFOLIO_TARGET_PROPOSAL = SchemaRef(name="PortfolioTargetProposal", version="1")
_DEBATE_TRANSCRIPT = SchemaRef(name="DebateTranscript", version="1")
_UPSTREAM_RATINGS = SchemaRef(name="UpstreamRatingsExtract", version="1")
_ALLOWED_ACTIONS = SchemaRef(name="AllowedActions", version="1")
_ANNOUNCEMENT_RISK = SchemaRef(name="AnnouncementRiskFlags", version="1")


def decision_lane_material_specs() -> tuple[_MaterialSpec, ...]:
    """The (id, kind, path) rows Lane D loads from disk — batch-owned + reused pilot bytes.

    The six seats' skills (the four new + the two 交付物③ pilot installs); the four new seats'
    prompts; the two pilots' frozen Phase-2 prompts (``prompt.research_mgr`` / ``prompt.pm``,
    reused verbatim); the three batch-new guardrails (``debate_rounds`` / ``advisory_shadow_only``
    / ``vf_anchor_dict``); the two shared guardrails (``number_provenance`` /
    ``untrusted_input_isolation``, already on disk); and the two pilots' frozen guardrails
    (``guard.provenance`` / ``guard.advisory_discipline``, reused so the model_copy'd pilot refs
    resolve at their frozen digests).
    """
    rows: list[_MaterialSpec] = []
    for wid in ("dec.bull", "dec.bear", "dec.risk_debate", "dec.trader",
                "dec.research_mgr", "dec.pm"):
        rows.append(_MaterialSpec(f"skill.{wid}", "skill", _SKILLS_TREE / wid / "SKILL.md"))
    for wid in ("dec.bull", "dec.bear", "dec.risk_debate", "dec.trader"):
        rows.append(_MaterialSpec(f"prompt.{wid}", "prompt", _PROMPTS / f"{wid}.md"))
    rows.append(_MaterialSpec("prompt.research_mgr", "prompt", _PILOT / "prompts" / "research_mgr.md"))
    rows.append(_MaterialSpec("prompt.pm", "prompt", _PILOT / "prompts" / "pm.md"))
    rows.append(_MaterialSpec(
        "guardrail.debate_rounds", "guardrail", _GUARDRAILS / "debate-rounds.md"))
    rows.append(_MaterialSpec(
        "guardrail.advisory_shadow_only", "guardrail", _GUARDRAILS / "advisory-shadow-only.md"))
    rows.append(_MaterialSpec(
        "guardrail.vf_anchor_dict", "guardrail", _GUARDRAILS / "vf-anchor-dict.md"))
    rows.append(_MaterialSpec(
        "guardrail.number_provenance", "guardrail", _GUARDRAILS / "number-provenance.md"))
    rows.append(_MaterialSpec(
        "guardrail.untrusted_input_isolation", "guardrail",
        _GUARDRAILS / "untrusted-input-isolation.md"))
    rows.append(_MaterialSpec("guard.provenance", "guardrail", _PILOT / "guardrails" / "provenance.md"))
    rows.append(_MaterialSpec(
        "guard.advisory_discipline", "guardrail", _PILOT / "guardrails" / "advisory_discipline.md"))
    return tuple(rows)


def load_decision_lane_materials() -> tuple[ResolvedTextMaterial, ...]:
    """Resolve every Lane D text material to bytes + a content-digest-sealed ref."""
    out: list[ResolvedTextMaterial] = []
    for spec in decision_lane_material_specs():
        _ref, mat = build_text_material(
            id=spec.material_id, version="1", kind=spec.kind, raw=spec.path.read_bytes())
        out.append(mat)
    return tuple(out)


class _DecWorkerRow(NamedTuple):
    worker_id: str
    persona: str
    tier: Tier
    model_tier: str
    thinking_budget: int
    prompt_id: str
    skill_id: str
    guardrail_ids: tuple[str, ...]
    read_categories: tuple[str, ...]
    output_schema: SchemaRef
    params_schema_ref: SchemaRef | None
    can_emit_decision: bool
    inputs: tuple[InputBinding, ...]


#: The reviewed Lane D roster for the FOUR NEW seats (frozen-order migration batch 5/5). All
#: four are FORBIDDEN ⇔ empty allowlist LLM critics/writers; the two pilots are built separately
#: as clause-(d) model_copies of the Phase-7 baseline (see :func:`build_decision_worker_specs`).
_DEC_NEW_ROWS: tuple[_DecWorkerRow, ...] = (
    _DecWorkerRow(
        worker_id="dec.bull",
        persona="Bull advocate (bounded debate seat) — zero trading authority",
        tier=Tier.CRITIC, model_tier="reasoner", thinking_budget=4096,
        prompt_id="prompt.dec.bull", skill_id="skill.dec.bull",
        guardrail_ids=("guardrail.debate_rounds", "guardrail.vf_anchor_dict",
                       "guardrail.untrusted_input_isolation", "guardrail.number_provenance"),
        read_categories=("context", "upstream_artifacts"),
        output_schema=_BULL_CASE_D, params_schema_ref=None, can_emit_decision=False,
        inputs=(InputBinding(name="fundamentals", schema_ref=_FUNDAMENTALS_D,
                             required=False, cardinality="one"),
                InputBinding(name="technical", schema_ref=_TECHNICAL_D,
                             required=False, cardinality="one"),
                InputBinding(name="sentiment", schema_ref=_SENTIMENT_D,
                             required=False, cardinality="one"),
                InputBinding(name="news_digest", schema_ref=_NEWS_DIGEST_D,
                             required=False, cardinality="one"),
                InputBinding(name="opponent_case", schema_ref=_BEAR_CASE_D,
                             required=False, cardinality="one"))),
    _DecWorkerRow(
        worker_id="dec.bear",
        persona="Bear advocate (bounded debate seat, 晚一波) — zero trading authority",
        tier=Tier.CRITIC, model_tier="reasoner", thinking_budget=4096,
        prompt_id="prompt.dec.bear", skill_id="skill.dec.bear",
        guardrail_ids=("guardrail.debate_rounds", "guardrail.vf_anchor_dict",
                       "guardrail.untrusted_input_isolation", "guardrail.number_provenance"),
        read_categories=("context", "upstream_artifacts"),
        output_schema=_BEAR_CASE_D, params_schema_ref=None, can_emit_decision=False,
        # asymmetric ammo: bear alone carries announcement_risk; bull has no such input.
        inputs=(InputBinding(name="fundamentals", schema_ref=_FUNDAMENTALS_D,
                             required=False, cardinality="one"),
                InputBinding(name="technical", schema_ref=_TECHNICAL_D,
                             required=False, cardinality="one"),
                InputBinding(name="sentiment", schema_ref=_SENTIMENT_D,
                             required=False, cardinality="one"),
                InputBinding(name="news_digest", schema_ref=_NEWS_DIGEST_D,
                             required=False, cardinality="one"),
                InputBinding(name="opponent_case", schema_ref=_BULL_CASE_D,
                             required=True, cardinality="one"),
                InputBinding(name="announcement_risk", schema_ref=_ANNOUNCEMENT_RISK,
                             required=False, cardinality="one"))),
    _DecWorkerRow(
        worker_id="dec.risk_debate",
        persona="Risk debate seat (激进/稳健/中性 by instance) — zero trading authority",
        tier=Tier.CRITIC, model_tier="fast", thinking_budget=0,
        prompt_id="prompt.dec.risk_debate", skill_id="skill.dec.risk_debate",
        guardrail_ids=("guardrail.debate_rounds", "guardrail.untrusted_input_isolation",
                       "guardrail.number_provenance"),
        read_categories=("context", "upstream_artifacts"),
        output_schema=_RISK_STANCE, params_schema_ref=_RISK_PARAMS, can_emit_decision=False,
        inputs=(InputBinding(name="research_plan", schema_ref=_RESEARCH_PLAN_D,
                             required=True, cardinality="one"),
                InputBinding(name="opponent_stances", schema_ref=_RISK_STANCE,
                             required=False, cardinality="many"),
                InputBinding(name="allowed_actions", schema_ref=_ALLOWED_ACTIONS,
                             required=False, cardinality="one"))),
    _DecWorkerRow(
        worker_id="dec.trader",
        persona="Advisory trader (proposal-only) — zero trading authority",
        tier=Tier.WRITER, model_tier="reasoner", thinking_budget=4096,
        prompt_id="prompt.dec.trader", skill_id="skill.dec.trader",
        guardrail_ids=("guardrail.debate_rounds", "guardrail.advisory_shadow_only",
                       "guardrail.untrusted_input_isolation", "guardrail.number_provenance"),
        read_categories=("context", "upstream_artifacts"),
        # Phase-6-owned schema, bound by name only; never redefined in Phase 8.
        output_schema=_PORTFOLIO_TARGET_PROPOSAL, params_schema_ref=None, can_emit_decision=True,
        inputs=(InputBinding(name="portfolio_decision", schema_ref=_PORTFOLIO_DECISION_D,
                             required=True, cardinality="one"),)),
)


def _dec_index(materials: tuple[ResolvedTextMaterial, ...]) -> dict[str, ContentRef]:
    return _text_index(materials)


def build_decision_worker_specs(
    *, materials: tuple[ResolvedTextMaterial, ...],
) -> tuple[WorkerSpec, ...]:
    """Build the reviewed Lane D final WorkerSpecs — the FINAL migration batch.

    The FOUR NEW seats are built fresh from resolved batch materials (FORBIDDEN ⇔ empty
    allowlist). The TWO PILOT updates (``dec.research_mgr`` / ``dec.pm``) are clause-(d)
    ``model_copy`` surgeries over the FROZEN Phase-7 baseline: only the reviewed fields change
    (skills rebind to the 交付物③ tree install; ``guardrail.debate_rounds`` appended; the
    reviewed optional inputs added; ``dec.pm`` also gains the ``memory`` read category and is
    asserted ``reasoner_deep``). Every other pilot field — evidence policy, capability allowlist,
    execution, persona, tier, output — is preserved verbatim. Order-stable (sorted by worker id).
    """
    dec_ix = _dec_index(materials)

    def _content(mid: str) -> ContentRef:
        try:
            return dec_ix[mid]
        except KeyError:
            raise KeyError(f"missing resolved text material {mid!r} for Lane D") from None

    specs: list[WorkerSpec] = []

    # -- the four NEW seats (built fresh) --------------------------------- #
    for row in _DEC_NEW_ROWS:
        guardrails = tuple(sorted(
            (_content(g) for g in row.guardrail_ids), key=lambda r: (r.id, r.version)))
        specs.append(WorkerSpec(
            id=row.worker_id, catalog_role="final", selection_scope="dynamic_allowed",
            lane=_DEC_LANE, persona=row.persona, tier=row.tier,
            execution=ExecutionSpec(
                kind=ExecutionKind.LLM, model_tier=row.model_tier,
                thinking_budget=row.thinking_budget),
            system_prompt_ref=_content(row.prompt_id),
            skills=(SkillBinding(skill_ref=_content(row.skill_id)),),
            guardrail_refs=guardrails,
            capability_allowlist=(),
            read_categories=row.read_categories,
            params_schema_ref=row.params_schema_ref,
            inputs=row.inputs,
            outputs=(OutputBinding(name="primary", schema_ref=row.output_schema),),
            evidence_policy=EvidencePolicy(
                tool_calls=ToolCallRequirement.FORBIDDEN, require_input_refs=True,
                require_number_anchors=True, allow_unsourced_numbers=False,
                optional_data_may_degrade=True),
            supported_modes=_MODES,
            can_emit_decision=row.can_emit_decision,
            decision_authority=("advisory_only" if row.can_emit_decision else "none")))

    # -- the two PILOT updates (clause-(d) model_copy of the Phase-7 baseline) -- #
    from guanlan_v2.orchestration.phase7_registry import phase7_catalog_snapshot
    baseline = {w.id: w for w in phase7_catalog_snapshot().workers}

    debate_rounds = _content("guardrail.debate_rounds")

    rm = baseline["dec.research_mgr"]
    specs.append(rm.model_copy(update=dict(
        skills=(SkillBinding(skill_ref=_content("skill.dec.research_mgr")),),
        guardrail_refs=tuple(rm.guardrail_refs) + (debate_rounds,),
        inputs=tuple(rm.inputs) + (
            InputBinding(name="bullbear_transcript", schema_ref=_DEBATE_TRANSCRIPT,
                         required=False, cardinality="one"),
            InputBinding(name="upstream_ratings", schema_ref=_UPSTREAM_RATINGS,
                         required=False, cardinality="one")),
    )))

    pm = baseline["dec.pm"]
    specs.append(pm.model_copy(update=dict(
        skills=(SkillBinding(skill_ref=_content("skill.dec.pm")),),
        guardrail_refs=tuple(pm.guardrail_refs) + (debate_rounds,),
        read_categories=tuple(pm.read_categories) + ("memory",),
        inputs=tuple(pm.inputs) + (
            InputBinding(name="riskdebate_transcript", schema_ref=_DEBATE_TRANSCRIPT,
                         required=False, cardinality="one"),
            InputBinding(name="allowed_actions", schema_ref=_ALLOWED_ACTIONS,
                         required=False, cardinality="one"),
            InputBinding(name="announcement_risk", schema_ref=_ANNOUNCEMENT_RISK,
                         required=False, cardinality="one")),
    )))

    return tuple(sorted(specs, key=lambda w: w.id))
