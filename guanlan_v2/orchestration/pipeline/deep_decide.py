# -*- coding: utf-8 -*-
"""Phase 10 · Task 6 — the sealed context-bound deep-decide preset (Amendment 3).

The 落子 deep-research chain as ONE reviewed, byte-frozen preset: four parallel
evidence readers + the sentiment reader feed a one-round ``dec.bull ↔ dec.bear``
debate judged by ``dec.pm`` (the sole ``reasoner_deep`` seat), whose
``PortfolioDecision@1`` reaches the single terminal ``dec.trader``
(``PortfolioTargetProposal@1``). One record digest serves every stock, so a single
Phase-7 ``ApprovalLease`` can bind it (ruling R-B).

Four implemented realities shape this module. Each was found by executing the
sealed Phase 1-9 kernel, ruled by the controller (plan Amendment 3), and is
recorded here rather than papered over:

1. **A debate cannot be a Phase-7 ``PlanPresetRecord``.** That record type has no
   ``debates`` / ``reducers`` field and its validator refuses debate identity
   outright (plan_presets.py:147-155 — "a preset can never smuggle gate/debate/
   condition/retry authority that the static profile forbids"). Phase 7 stays
   untouched; :class:`PlanPresetRecordV2` (registered name ``PlanPresetRecord@2``,
   Phase-10-owned) carries the debate vocabulary in the *implemented Phase 8 draft
   shapes* — ``DebateCfg`` + the seat nodes' debate identity + the
   ``debate.transcript_reducer`` ``ReducerCfg`` over the shared transcript slot.
   It SUBCLASSES the Phase-7 record so the Phase-7 ``PlanPresetRegistry`` accepts
   it unchanged (plan_presets.py:216-218 is an ``isinstance`` gate).

2. **The committed v2 file lives in ``config/orchestration/presets/v2/``.** The
   Phase-7 loader globs ``root/*.json`` non-recursively (plan_presets.py:277) and
   a ``schema_version="2"`` file is a hard ``PlanPresetError`` for it — dropping
   the v2 preset beside the v1 baseline would break every existing caller of
   ``load_preset_registry(PRODUCTION_PRESETS_DIR)``. The ``v2/`` subdirectory is
   invisible to that glob, so the Phase-7 loader keeps returning exactly the
   baseline while ``assembly.load_phase10_preset_registry`` reads both
   generations. That record model, the loader and the ``v2/`` directory constant
   live in :mod:`~guanlan_v2.orchestration.pipeline.assembly` beside
   ``PRODUCTION_PRESETS_DIR`` — they are preset-generation infrastructure every
   Phase-10 preset shares (Task 3's screening lane binds the same loader), and
   keeping them there is what removes the call-time import edge from ``assembly``
   back into this module.

3. **The graph is the ten-worker validation-green set.** ``dec.pm`` REQUIRES
   ``research_plan: ResearchPlan@1`` and ``sentiment: SentimentReport@1``, whose
   only producers are ``dec.research_mgr`` and ``text.sentiment``; ``dec.trader``
   REQUIRES ``portfolio_decision``. The evidence and debate nodes are
   ``auxiliary=True`` because no catalog input on the pm/trader chain accepts
   ``BullCase@1``/``BearCase@1`` and a reducer's ``DebateTranscript@1`` is
   invisible to Phase 1's dependency schema check (spec.py:999-1000) — the exact
   scoping the reviewed Phase-8 e2e states for itself (test_phase8_e2e.py:551-565).
   The debate binds to its judge through ``DebateCfg.judge_node_id``, not a data
   edge; wiring the folded transcript into the judge's model request is Phase-9
   data-method work.

4. **The subject is RUN-SCOPED, not plan-scoped.** ``InputArtifactBinding`` is a
   dispatch-time, intra-plan structure built only from upstream plan-node outputs
   (dag.py:380-430); a ``PlanDraft`` has no external-input field and no worker in
   the sealed catalog declares a ``RunSubject@1`` input. So this materializer
   REQUIRES and schema-pins ``subject_ref`` and carries it beside the draft on
   :class:`MaterializedDeepDecide` — it never forges an ``InputArtifactBinding``
   and never invents a subject worker. The committed ``RunSubject@1`` artifact
   stays the digest-bound authority; threading it into prompt assembly and the
   instrument-param data prefetch is clause E2b, which lands WHOLLY in Task 7.
   **The seam is real and located** (Task 7, read this): the reviewed
   ``StaticPromptAssembler.assemble`` accepts ``trusted_input_digests`` as
   name+digest pairs only — it takes no caller-supplied text — so rendering a
   trusted subject block requires a per-run Phase-10 assembler that closes over
   the subject and occupies the ``prompt_assembler`` injection seam of
   ``assembly.build_production_plan_runner`` (assembly.py:~584 parameter,
   :~630 default — locate by name, line refs drift). That belongs with the runner invocation, not with
   materialization. The always-present :data:`SUBJECT_RUN_SCOPED_BADGE` says so
   on every materialization rather than implying a binding that does not exist.

``PlanDraft`` also has no badge field, so materialization returns the composite
:class:`MaterializedDeepDecide` (draft + subject ref + context ref + badges).
Registration of ``PlanPresetRecord@2`` into the cumulative Phase-10 registry is
the Phase-10 chain task's, exactly like Task 1's contracts.

**路线 A (2026-07-29) — a SECOND sealed generation, never a weakening.** The
ten-node preset above is honestly unsupported on the production chain forever
(the ``pv.technical`` / ``text.news`` grant gap); the eight-node
:data:`REDUCED_DEEP_DECIDE_PRESET_ID` drops exactly those two auxiliary readers
and passes the same ``check_runtime_support``. Both live in this module and in
the same committed ``v2/`` directory, selected per call through the
``preset_id`` argument (default: the ten-node one — the deep lane never
downgrades itself). The reduction is a fact about what the debate SAW, so it is
badged on every product (:data:`REDUCED_EVIDENCE_BADGE`) and the record's worker
set must prove the claim in both directions
(:class:`EvidenceScopeMismatch`). The reduced preset is deliberately NOT
referenced by the Phase-10 catalog (the screening-preset precedent): entering
the catalog would move the sealed chain digests, which belongs to the re-freeze
phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.data.runtime import SubjectParams
from guanlan_v2.orchestration.digest import NonEmptyStr
from guanlan_v2.orchestration.enums import PlanSource
from guanlan_v2.orchestration.market.factors import _session_date as session_date_of
from guanlan_v2.orchestration.plan_presets import PlanPresetRegistry
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import OrchestrationRequest, PlanDraft

from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PLAN_PRESET_RECORD_V2_SCHEMA_REF,
    PlanPresetRecordV2,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject

__all__ = [
    # identity
    "DEEP_DECIDE_PRESET_ID",
    "DEEP_DECIDE_PRESET_VERSION",
    "DEEP_DECIDE_PRESET_IDS",
    "DEEP_DECIDE_DEBATE_ID",
    "DEEP_DECIDE_TERMINAL_NODE_ID",
    "DEEP_DECIDE_WORKER_IDS",
    "REDUCED_DEEP_DECIDE_PRESET_ID",
    "REDUCED_DEEP_DECIDE_PRESET_VERSION",
    "REDUCED_DEEP_DECIDE_WORKER_IDS",
    "REDUCED_EVIDENCE_DROPPED_WORKER_IDS",
    "RUN_SUBJECT_SCHEMA_REF",
    # committed source
    "DEEP_DECIDE_PRESET_FILE",
    "REDUCED_DEEP_DECIDE_PRESET_FILE",
    # badges
    "CONTEXT_SNAPSHOT_STALE_BADGE",
    "CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX",
    "SUBJECT_RUN_SCOPED_BADGE",
    "REDUCED_EVIDENCE_BADGE",
    "REDUCED_EVIDENCE_MISSING_BADGE",
    "REDUCED_EVIDENCE_MISSING_BADGE_PREFIX",
    "REDUCED_EVIDENCE_BADGES",
    "REDUCED_EVIDENCE_NOTE",
    "reduced_evidence_badges",
    "render_reduced_evidence_banner",
    # errors
    "DeepDecideError",
    "SubjectRefused",
    "ContextSnapshotRefused",
    "ClockRefused",
    "PresetSelectionRefused",
    "EvidenceScopeMismatch",
    # surface
    "MaterializedDeepDecide",
    "materialize_deep_decide_draft",
    "session_date_of",
]


# =========================================================================== #
# identity                                                                     #
# =========================================================================== #
#: the sealed deep-decide preset id (one digest serves every stock).
DEEP_DECIDE_PRESET_ID: LogicalId = "pipeline.luozi_deep_decide"
DEEP_DECIDE_PRESET_VERSION: str = "1"

#: the one bounded bull/bear debate this preset declares.
DEEP_DECIDE_DEBATE_ID: LogicalId = "bullbear"

#: the SOLE terminal node id (``dec.trader`` → ``PortfolioTargetProposal@1``).
DEEP_DECIDE_TERMINAL_NODE_ID: LogicalId = "trader"

#: the ruled ten-worker validation-green set (Amendment 3 / R2). ``text.sentiment``
#: and ``dec.research_mgr`` are NOT decoration: ``dec.pm``'s ``sentiment`` and
#: ``research_plan`` inputs are REQUIRED and they are their only producers.
DEEP_DECIDE_WORKER_IDS: tuple[str, ...] = (
    "pv.price_action",
    "pv.technical",
    "pv.microstructure",
    "text.news",
    "text.sentiment",
    "dec.bull",
    "dec.bear",
    "dec.research_mgr",
    "dec.pm",
    "dec.trader",
)

#: the committed subject artifact's schema identity (Task 1's ``RunSubject@1``).
RUN_SUBJECT_SCHEMA_REF: SchemaRef = SchemaRef(name="RunSubject", version="1")


# --------------------------------------------------------------------------- #
# 路线 A — the REDUCED-EVIDENCE generation of the same graph                    #
# --------------------------------------------------------------------------- #
# The ten-node preset above can never pass ``check_runtime_support`` on the
# production chain: ``pv.technical`` / ``text.news`` declare
# ``evidence_policy.tool_calls = REQUIRED`` while the reviewed Phase-3 prefetch
# table grants a data row to ``dec.pm`` only, so both raise
# ``tool_calls_required_unmet`` before one LLM call happens. Closing that for
# real needs the subject→data-bridge path (L1), a production data runtime (L2 —
# which does not exist) and the grants + sealed-chain re-freeze (L3); see
# ``docs/superpowers/specs/2026-07-29-post-p10-refreeze-design.md`` §1.5.
#
# Dropping exactly those two AUXILIARY evidence readers — they reach the debate
# only through ``degrade`` dependencies, and the decision spine
# ``sentiment → research-mgr → pm → trader`` never touches them — makes the same
# support check green. That is route A: a SECOND sealed preset, never a
# weakening of the first, and never a silent substitution for it.
#
# The whole point is a run that a human can honestly read, so the reduction is
# NAMED on every product (:data:`REDUCED_EVIDENCE_BADGE`) and the materializer
# refuses any record whose worker set contradicts its own badge.

#: the reduced-evidence preset id (deliberately distinct — a duplicate id is a
#: hard ``PlanPresetError`` in ``load_phase10_preset_registry``).
REDUCED_DEEP_DECIDE_PRESET_ID: LogicalId = "pipeline.luozi_deep_decide_reduced"
REDUCED_DEEP_DECIDE_PRESET_VERSION: str = "1"

#: the two evidence readers the reduced generation does NOT run.
REDUCED_EVIDENCE_DROPPED_WORKER_IDS: tuple[str, ...] = ("pv.technical", "text.news")

#: the reduced generation's eight-worker set (the ruled ten minus those two).
REDUCED_DEEP_DECIDE_WORKER_IDS: tuple[str, ...] = tuple(
    w for w in DEEP_DECIDE_WORKER_IDS
    if w not in REDUCED_EVIDENCE_DROPPED_WORKER_IDS)

#: every preset id this module is willing to materialize (a closed vocabulary —
#: an arbitrary registry entry can never be materialized through this door).
DEEP_DECIDE_PRESET_IDS: tuple[LogicalId, ...] = (
    DEEP_DECIDE_PRESET_ID, REDUCED_DEEP_DECIDE_PRESET_ID)


# =========================================================================== #
# committed source location                                                    #
# =========================================================================== #
#: the committed source of this preset. The directory constant and the v2 record
#: model / loader live in :mod:`~guanlan_v2.orchestration.pipeline.assembly`
#: beside ``PRODUCTION_PRESETS_DIR`` (they are preset-generation infrastructure,
#: not deep-decide specifics — Task 3 binds the same loader).
DEEP_DECIDE_PRESET_FILE: Path = PHASE10_PRESETS_V2_DIR / "luozi_deep_decide_v1.json"

#: the committed source of the reduced-evidence generation (same directory, same
#: loader, its own preset id and its own hand-frozen record digest).
REDUCED_DEEP_DECIDE_PRESET_FILE: Path = (
    PHASE10_PRESETS_V2_DIR / "luozi_deep_decide_reduced_v1.json")


# =========================================================================== #
# badges                                                                       #
# =========================================================================== #
#: emitted when the bound ContextSnapshot's data date is BEHIND the request's
#: +08:00 session date — honest staleness, never an intraday re-run.
CONTEXT_SNAPSHOT_STALE_BADGE: str = "context_snapshot_stale"

#: accompanies the stale badge with the snapshot's own session date.
CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX: str = "context_snapshot_data_date:"

#: ALWAYS emitted: the subject is run-scoped (reality 4). It names the deferred
#: assembly-threading seam instead of implying a plan-level binding.
SUBJECT_RUN_SCOPED_BADGE: str = "subject_run_scoped_v1"

#: THE HONESTY RED LINE of route A — emitted on EVERY product of the reduced
#: preset (materialization, seats ledger row, advisory order record, reviewer
#: card). Whoever reads a reduced-evidence deep verdict must be able to see that
#: the debate never saw technical indicators and never saw any news.
REDUCED_EVIDENCE_BADGE: str = "reduced_evidence_preset_v1"

#: accompanies it, naming the absent evidence readers by worker id.
REDUCED_EVIDENCE_MISSING_BADGE_PREFIX: str = "reduced_evidence_missing:"
REDUCED_EVIDENCE_MISSING_BADGE: str = (
    REDUCED_EVIDENCE_MISSING_BADGE_PREFIX
    + ",".join(REDUCED_EVIDENCE_DROPPED_WORKER_IDS))

#: the pair, in the order every product carries them.
REDUCED_EVIDENCE_BADGES: tuple[str, ...] = (
    REDUCED_EVIDENCE_BADGE, REDUCED_EVIDENCE_MISSING_BADGE)

#: the human-readable half of the same statement (rows / cards / responses).
REDUCED_EVIDENCE_NOTE: str = (
    "减证据版深链(八节点):本次辩论与 PM 判断从未见过技术指标证据"
    "(缺 pv.technical),也从未见过任何新闻证据(缺 text.news)。"
    "这不是运行时降级失败,是这份预案本身就没有这两个节点——请据此打折看待结论。"
)


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class DeepDecideError(ValueError):
    """The deep-decide preset is unusable for this request (typed, never silent)."""


class SubjectRefused(DeepDecideError):
    """No committed ``RunSubject@1`` artifact was supplied, or the ref is not one.

    Materialization without a subject is refused rather than defaulted: the
    committed subject artifact is the ONLY sanctioned way a Phase-10 plan names
    its stock, and the sealed preset graph deliberately carries no code.
    """


class ContextSnapshotRefused(DeepDecideError):
    """The latest committed Lane-0 ``ContextSnapshot`` is missing or unbound.

    The live path degrades to its fast chain on this refusal — it never
    fabricates a context, and it never re-runs Lane 0 intraday.
    """


class ClockRefused(DeepDecideError):
    """The supplied clock returned a naive (tz-unaware) datetime.

    Refused rather than coerced: :func:`session_date_of` calls ``astimezone``,
    which silently interprets a naive datetime as **machine-local** time — on a
    non-UTC host that shifts the +08:00 session date and would flip the recency
    badge without any error. The clock port must return an aware datetime.
    """


class PresetSelectionRefused(DeepDecideError):
    """The caller named a preset this module will not materialize.

    Either an id outside :data:`DEEP_DECIDE_PRESET_IDS`, or (in the live path's
    env-driven selection) an unrecognized spelling. Refused rather than
    defaulted: a typo must never silently pick an evidence scope, in either
    direction — the reduced lane is opt-in, and the full lane's honest refusal
    is a real product, not a fallback.
    """


class EvidenceScopeMismatch(DeepDecideError):
    """The registered record's worker set contradicts the preset id's badge.

    :data:`REDUCED_EVIDENCE_BADGE` is a CLAIM about what the debate saw, so the
    graph must prove it, in both directions: a record registered under the
    reduced id may not carry the dropped evidence readers (the badge would be a
    lie), and a record registered under the full id may not have quietly lost
    them (an un-badged verdict would silently be a reduced one).
    """


@runtime_checkable
class _Clock(Protocol):
    """The authoritative clock port (``now() -> datetime``); never a module global."""

    def now(self) -> Any:  # pragma: no cover - structural protocol
        ...



# =========================================================================== #
# reduced-evidence badge helpers (one authority, three consumers)               #
# =========================================================================== #
def reduced_evidence_badges(preset_id: LogicalId) -> tuple[str, ...]:
    """The reduced-evidence badge pair for ``preset_id`` (empty for the full one).

    The ONE authority every surface reads — materialization, the seats rows, the
    reviewer card and the router response — so the badge can never be present on
    one product and missing from another.
    """
    if preset_id == REDUCED_DEEP_DECIDE_PRESET_ID:
        return REDUCED_EVIDENCE_BADGES
    return ()


def render_reduced_evidence_banner(preset_id: LogicalId) -> str:
    """The reviewer-card markdown banner for ``preset_id`` (``""`` for the full one).

    Prepended to the rendered plan diff so a human approving a deep candidate
    reads the missing-evidence statement BEFORE the graph, not after it.
    """
    if preset_id != REDUCED_DEEP_DECIDE_PRESET_ID:
        return ""
    return (
        f"> ⚠️ **{REDUCED_EVIDENCE_BADGE}** — {REDUCED_EVIDENCE_NOTE}\n"
        f">\n"
        f"> 缺席节点:`"
        + "` / `".join(REDUCED_EVIDENCE_DROPPED_WORKER_IDS)
        + "`(它们的 `tool_calls=REQUIRED` 在当前审阅过的数据授权下无法满足,"
        "十节点版因此在生产上恒被诚实拒绝)。\n\n"
    )


# =========================================================================== #
# MaterializedDeepDecide — the composite the runner consumes                    #
# =========================================================================== #
@dataclass(frozen=True)
class MaterializedDeepDecide:
    """One materialized deep-decide run: the draft plus its run-scoped bindings.

    A plain frozen dataclass (invisible to the ContractModel firewall by design,
    the ``DeepDecideBindings`` precedent): ``PlanDraft`` has no badge field and no
    external-input field, so the honest badges and the run-scoped ``subject_ref``
    travel here rather than being forged into the plan.
    """

    draft: PlanDraft
    subject_ref: TypedPayloadRef
    context_snapshot_ref: PayloadRef
    badges: tuple[str, ...] = ()
    #: WHICH sealed generation produced this draft (route A's evidence scope).
    #: Defaulted to the full one so every pre-route-A construction keeps meaning
    #: exactly what it meant.
    preset_id: LogicalId = DEEP_DECIDE_PRESET_ID
    #: L1 / D-0 — the closed subject-params document, materialization-stamped by
    #: the ONE reviewed recipe (``SubjectParams.project``) from THE committed
    #: ``RunSubject@1`` the ``subject_ref`` digest-binds. Out-of-band beside the
    #: draft, never inside it: ``PlanNode.params`` stays empty, the sealed record
    #: stays code-free. Defaulted to None (the ``preset_id`` convention) so every
    #: pre-L1 construction keeps meaning exactly what it meant — the data bridge
    #: refuses loudly downstream rather than defaulting a subject.
    subject_params: SubjectParams | None = None


# =========================================================================== #
# materialize_deep_decide_draft                                                 #
# =========================================================================== #
def materialize_deep_decide_draft(
    *,
    request: OrchestrationRequest,
    preset_registry: PlanPresetRegistry,
    context_snapshot_ref: PayloadRef | None,
    subject_ref: TypedPayloadRef | None,
    clock: _Clock,
    # -- ABI-forced additions (recorded, never a parallel runtime) ------------ #
    # the ruled five kwargs above are the reviewed surface; the Phase-1 stamping
    # ABI additionally needs the snapshot itself (``as_of``/``mode`` + the recency
    # comparison), the sealed catalog/registry it binds, and the caller's plan and
    # run identity. ``materialize_fallback_draft`` (plan_presets.py:305-315) takes
    # exactly the same extras — there is no accessor anywhere in the kernel that
    # could fetch the latest committed ContextSnapshot for us (only a writer
    # exists), so the caller supplies it.
    context: ContextSnapshot | None,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    draft_id: LogicalId,
    run_id: NonEmptyStr,
    # -- route A: WHICH sealed generation (default = the reviewed ten-node one) - #
    preset_id: LogicalId = DEEP_DECIDE_PRESET_ID,
    # -- L1 / D-0: the committed subject OBJECT for the data-param stamp -------- #
    # optional so every pre-L1 caller keeps meaning exactly what it meant; when
    # provided it must BE the artifact ``subject_ref`` names (digest bond below).
    subject: RunSubject | None = None,
) -> MaterializedDeepDecide:
    """Materialize the sealed deep-decide preset into a ``source=PRESET`` draft.

    Refusal order is deliberate and typed. A missing / mistyped ``subject_ref`` —
    or, when ``subject`` is supplied, a subject object the ref does not
    digest-bind — is a :class:`SubjectRefused`; a missing or unbound
    ContextSnapshot is a :class:`ContextSnapshotRefused` (the live path degrades
    to its fast chain on that one); a naive ``clock.now()`` is a
    :class:`ClockRefused`. None is ever defaulted or coerced.

    L1 / D-0 (materialization-time stamping): when ``subject`` is provided, the
    ONE reviewed recipe ``SubjectParams.project`` projects it into the closed
    two-key document stamped on ``MaterializedDeepDecide.subject_params`` —
    carried BESIDE the draft, never inside it (``PlanNode.params`` stays empty;
    the sealed record and its digest do not move). When ``subject`` is None the
    stamp is honestly None and the data bridge refuses loudly downstream.

    The returned draft is a plain Phase-1 ``PlanDraft``: identity and authority
    fields are runtime-stamped (``id``/``run_id``/``request_id``/``phase='main'``/
    ``source=PRESET``/``goal`` from the caller + trusted request; ``as_of``/``mode``
    from the snapshot's ``DataContext``; catalog/registry digests from the sealed
    snapshots; ``approval_policy`` copied verbatim — ``AUTO`` still dies in Phase-1
    validation). The reviewed ``nodes``/``sink_node_ids``/``debates``/``reducers``/
    budget/concurrency are copied verbatim from the sealed record and ``universe``
    stays empty, so the draft is byte-identical for every stock: one record digest,
    one lease.

    Badges: :data:`SUBJECT_RUN_SCOPED_BADGE` always (reality 4), and
    :data:`CONTEXT_SNAPSHOT_STALE_BADGE` (+ the dated detail) whenever the
    snapshot's +08:00 session date is BEHIND the clock's session date. A snapshot
    dated at or after the session is never badged stale — honest in both
    directions. Under ``preset_id=REDUCED_DEEP_DECIDE_PRESET_ID`` the
    :data:`REDUCED_EVIDENCE_BADGES` pair is added — and the record's worker set
    must agree with that claim (:class:`EvidenceScopeMismatch` otherwise).
    """
    # -- 0. the closed preset vocabulary (route A) --------------------------- #
    if preset_id not in DEEP_DECIDE_PRESET_IDS:
        raise PresetSelectionRefused(
            f"{preset_id!r} is not a deep-decide preset; this materializer only "
            f"serves {', '.join(repr(p) for p in DEEP_DECIDE_PRESET_IDS)}")

    # -- 1. the run-scoped subject (required, schema-pinned) ------------------ #
    if subject_ref is None:
        raise SubjectRefused(
            "a committed RunSubject@1 artifact ref is required — the sealed "
            f"{DEEP_DECIDE_PRESET_ID!r} graph carries no code, so a run without a "
            "subject has no subject at all")
    if not isinstance(subject_ref, TypedPayloadRef):
        raise SubjectRefused(
            "subject_ref must be a TypedPayloadRef naming the committed subject; "
            f"got {type(subject_ref).__name__}")
    if subject_ref.schema_ref != RUN_SUBJECT_SCHEMA_REF:
        raise SubjectRefused(
            f"subject_ref must name {RUN_SUBJECT_SCHEMA_REF.key}; got "
            f"{subject_ref.schema_ref.key}")

    # -- 1b. the committed subject object (L1/D-0 materialization stamping) --- #
    # the stamp must provably come from THE committed artifact the ref names, so
    # the digest bond (``subject.semantic_digest() ==
    # subject_ref.payload_ref.content_digest`` — the same equality the screening
    # lane enforces on every per-code committed subject) is verified, never
    # assumed. These checks live in the SUBJECT block deliberately: refusal
    # order stays subject-before-context.
    subject_params: SubjectParams | None = None
    if subject is not None:
        if not isinstance(subject, RunSubject):
            raise SubjectRefused(
                "subject must be the committed RunSubject the subject_ref "
                f"names; got {type(subject).__name__}")
        if subject.semantic_digest() != subject_ref.payload_ref.content_digest:
            raise SubjectRefused(
                "subject_ref.payload_ref.content_digest does not bind the "
                "supplied RunSubject content; a params stamp from an unbound "
                "subject would not be an authority")
        subject_params = SubjectParams.project(
            code=subject.code, as_of=subject.as_of)

    # -- 2. the latest committed Lane-0 ContextSnapshot ---------------------- #
    if context is None or context_snapshot_ref is None:
        raise ContextSnapshotRefused(
            "the latest committed Lane-0 ContextSnapshot (and its ref) are "
            "required; refusing rather than fabricating a context — the live path "
            "degrades to its fast chain here")
    if context_snapshot_ref.namespace != "main":
        raise ContextSnapshotRefused(
            "context_snapshot_ref must use namespace='main'; got "
            f"{context_snapshot_ref.namespace!r}")
    if context_snapshot_ref.content_digest != context.content_digest:
        raise ContextSnapshotRefused(
            "context_snapshot_ref.content_digest does not bind the supplied "
            "ContextSnapshot content")

    # -- 3. the sealed record (must be the v2 generation) -------------------- #
    record = preset_registry.get(preset_id)
    if not isinstance(record, PlanPresetRecordV2):
        raise DeepDecideError(
            f"{preset_id!r} must be a PlanPresetRecordV2 "
            f"({PLAN_PRESET_RECORD_V2_SCHEMA_REF.key}); the registered record is a "
            f"{type(record).__name__} and cannot carry this graph's debate")

    # -- 3b. the evidence-scope claim, proved by the graph (route A) --------- #
    # the badge says what the debate did NOT see; the record must agree, or the
    # badge (present or absent) is a lie. Refused in BOTH directions.
    present = {node.worker_id for node in record.nodes}
    dropped = set(REDUCED_EVIDENCE_DROPPED_WORKER_IDS)
    if preset_id == REDUCED_DEEP_DECIDE_PRESET_ID and (present & dropped):
        raise EvidenceScopeMismatch(
            f"the record registered as {preset_id!r} still runs "
            f"{', '.join(sorted(present & dropped))} — a reduced-evidence badge "
            "over a graph that DOES read that evidence would be a lie")
    if preset_id == DEEP_DECIDE_PRESET_ID and not dropped <= present:
        raise EvidenceScopeMismatch(
            f"the record registered as {preset_id!r} no longer runs "
            f"{', '.join(sorted(dropped - present))}; a full-evidence (un-badged) "
            f"verdict must never come out of a reduced graph — register it as "
            f"{REDUCED_DEEP_DECIDE_PRESET_ID!r} instead")

    # -- 4. final-workers-only catalog-role gate (before any construction) ---- #
    worker_by_id = {w.id: w for w in catalog.workers}
    non_final = sorted({
        node.worker_id for node in record.nodes
        if worker_by_id.get(node.worker_id) is None
        or worker_by_id[node.worker_id].catalog_role != "final"
    })
    if non_final:
        raise DeepDecideError(
            "preset worker(s) not catalog_role='final' in the bound catalog: "
            + ", ".join(non_final))

    # -- 5. honest recency, in +08:00 session terms -------------------------- #
    # a naive clock is refused, never coerced: ``session_date_of``'s astimezone
    # would read it as machine-local time and silently move the session date.
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or (
            now.utcoffset() is None):
        raise ClockRefused(
            "clock.now() must return a timezone-aware datetime; got "
            f"{now!r} — a naive datetime would be read as machine-local time and "
            "silently shift the +08:00 session date")
    session_date = session_date_of(now)
    snapshot_date = session_date_of(context.data_context.as_of)
    badges: list[str] = [SUBJECT_RUN_SCOPED_BADGE]
    badges.extend(reduced_evidence_badges(preset_id))
    if snapshot_date < session_date:
        badges.append(CONTEXT_SNAPSHOT_STALE_BADGE)
        badges.append(f"{CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX}{snapshot_date}")

    # -- 6. the runtime-stamped PRESET draft (graph copied verbatim) --------- #
    draft = PlanDraft(
        id=draft_id,
        run_id=run_id,
        request_id=request.request_id,
        phase="main",
        source=PlanSource.PRESET,
        goal=request.goal,
        as_of=context.data_context.as_of,
        mode=context.data_context.mode,
        context_snapshot_ref=context_snapshot_ref,
        universe=(),
        nodes=record.nodes,
        sink_node_ids=record.sink_node_ids,
        debates=record.debates,
        gates=(),
        reducers=record.reducers,
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=request.approval_policy,
        budget_request_tokens=record.budget_request_tokens,
        budget_request_llm_invocations=record.budget_request_llm_invocations,
        max_concurrency=record.max_concurrency,
        stop_condition_refs=(),
        legacy_source_schema=None,
        legacy_source_config_digest=None,
        legacy_mapping_digest=None,
    )
    return MaterializedDeepDecide(
        draft=draft,
        subject_ref=subject_ref,
        context_snapshot_ref=context_snapshot_ref,
        badges=tuple(badges),
        preset_id=preset_id,
        subject_params=subject_params,
    )
