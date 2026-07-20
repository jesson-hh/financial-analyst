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
