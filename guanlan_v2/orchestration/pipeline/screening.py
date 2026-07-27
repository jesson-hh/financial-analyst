# -*- coding: utf-8 -*-
"""Phase 10 · Task 3 — the sealed per-code screening lane + lease-governed batch.

选股 in this phase is **N instances of ONE sealed, code-agnostic lane preset**,
never an N-lane single plan: the reviewed kernel has no structural subject-code
carrier anywhere (``DataContext`` is codeless, every lane worker is paramsless and
Phase 1 refuses node params outright), so a per-lane code is structurally
impossible. The subject enters run-scoped instead, as a committed ``RunSubject@1``
artifact per code (Amendment 3), and the whole batch's cost is approved in ONE
human moment — the screening :class:`~guanlan_v2.orchestration.approval.ApprovalLease`
issue form, whose ``max_admissions`` / ``budget_cap_llm_invocations`` mirror
:func:`screening_cost_preview`. Without a lease, every candidate simply stays a
pending human card. There is no third path.

Task 6 is the pathfinder; the idioms below are ITS idioms, mirrored:

1. **The v2 preset generation lives in** :mod:`~guanlan_v2.orchestration.pipeline.
   assembly` — ``PlanPresetRecordV2`` / ``load_phase10_preset_registry`` /
   ``PHASE10_PRESETS_V2_DIR``. The committed file MUST sit in
   ``config/orchestration/presets/v2/``: the Phase-7 loader globs ``root/*.json``
   non-recursively (plan_presets.py:277) and a ``schema_version="2"`` file at the
   root is a hard ``PlanPresetError`` for every existing caller of it.
2. **The debate is expressed exactly as the reviewed Phase-8 e2e expresses it** —
   seat identity on the nodes, ``opponent_case`` BLOCK chaining, both seats writing
   ONE transcript slot folded by ``debate.transcript_reducer@1``, seats
   ``auxiliary=True``, and the judge bound through ``DebateCfg.judge_node_id``
   rather than a data edge. That last point is forced, not stylistic: Phase 1's
   dependency check compares the UPSTREAM WORKER'S output schema against the input
   (spec.py:995-999), so ``dec.research_mgr``'s optional
   ``bullbear_transcript: DebateTranscript@1`` input can never be fed from a seat
   (whose output is ``BullCase@1``/``BearCase@1``). Wiring the folded transcript
   into the judge's model request stays Phase-9 data-method work.
3. **The worker set is frozen against executed validation-green evidence.** The
   brief's five evidence workers + bull/bear + ``dec.research_mgr`` — and, exactly
   as Task 6 found for ``dec.pm``, ``dec.research_mgr`` REQUIRES
   ``sentiment: SentimentReport@1`` whose only producer is ``text.sentiment``, so
   that worker joins: :data:`SCREENING_LANE_WORKER_IDS` is NINE workers.
4. **The subject is run-scoped.** One committed ``RunSubject@1`` per code, carried
   beside its draft on :class:`MaterializedScreeningLane`; no
   ``InputArtifactBinding`` is forged (they are built at dispatch only, from
   upstream plan-node outputs — dag.py:380-430) and no subject worker is invented.
   :data:`SUBJECT_RUN_SCOPED_BADGE` says so on every batch. Threading the subject
   into prompt assembly is clause E2b, which lands wholly in Task 7 (the seam is
   the ``prompt_assembler`` parameter of ``assembly.build_production_plan_runner``).

**The one place this deviates from Task 6, and why (recorded).** Task 6 stamps
``source=PlanSource.PRESET``; a screening draft is stamped
``source=PlanSource.PRESET_FALLBACK``. That is forced by the lease channel, twice
over: :class:`~guanlan_v2.orchestration.plan_diff.PendingPlanApproval` structurally
covers only ``DYNAMIC`` / ``PRESET_FALLBACK`` (plan_diff.py:261-265), and
``PlanApprovalCoordinator._find_admissible_lease`` matches only a card carrying
preset provenance, which the card may carry iff its source is ``PRESET_FALLBACK``
(plan_diff.py:283-293). A ``PRESET`` screening draft could not be carded at all,
so it could never be lease-admitted **or** shown to a human.

The reviewed refusal of *relabelling* a preset as a fallback
(adapters/replay_cards.py:284-292 — "relabelling a preset as a 'fallback' to make
it fit would put a false provenance in front of the reviewer") is respected rather
than dodged: this builder does not relabel anything. Each per-code request
genuinely names this preset in its ``fallback_preset_id`` and
:func:`build_screening_batch` enforces the Phase-7 selection rule (exact
``fallback_preset_id == preset_id`` equality) before materializing, so the
provenance the reviewer sees — ``preset_fallback`` + this ``preset_id`` + this
record digest — is true in every part. A base request that does not name this
preset is a typed :class:`BaseRequestRefused`.

**Where the code appears, and where it must never appear.** The code lives in the
run *identity* (``request_id`` / ``draft_id`` / ``run_id``) so a human reading N
cards, and an operator reading the journal, can tell the lanes apart. It appears
in NO executable plan field: not in ``params`` (structurally impossible), not in
``universe``, and deliberately not in ``goal`` — the goal is copied verbatim from
the base request, so the N executable projections are byte-identical and the
committed ``RunSubject@1`` stays the only sanctioned subject authority. Free-text
goal parsing remains forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Protocol,
    Sequence,
    runtime_checkable,
)

from pydantic import model_validator

from guanlan_v2.orchestration.admission import AdmissionRejected
from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, ExecutionKind, PlanSource
from guanlan_v2.orchestration.market.factors import _session_date as session_date_of
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.plan_presets import PlanPresetRegistry
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    compute_candidate_plan_digest,
)

from guanlan_v2.orchestration.pipeline.assembly import (
    PHASE10_PRESETS_V2_DIR,
    PLAN_PRESET_RECORD_V2_SCHEMA_REF,
    PlanPresetRecordV2,
)
from guanlan_v2.orchestration.pipeline.contracts import CandidateSlate, RunSubject

if TYPE_CHECKING:  # the coordinator is duck-typed at the call seam; this is the
    # returned value's real type, imported for readers/type-checkers only so the
    # module keeps no runtime dependency on the approval carrier.
    from guanlan_v2.orchestration.approval import LeaseAdmissionOutcome

__all__ = [
    # identity
    "SCREENING_LANE_PRESET_ID",
    "SCREENING_LANE_PRESET_VERSION",
    "SCREENING_LANE_DEBATE_ID",
    "SCREENING_LANE_TERMINAL_NODE_ID",
    "SCREENING_LANE_WORKER_IDS",
    "RUN_SUBJECT_SCHEMA_REF",
    "CANDIDATE_SLATE_SCHEMA_REF",
    # committed source
    "SCREENING_LANE_PRESET_FILE",
    # badges
    "SUBJECT_RUN_SCOPED_BADGE",
    "CONTEXT_SNAPSHOT_STALE_BADGE",
    "CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX",
    "AUXILIARY_EVIDENCE_UNWIRED_BADGE",
    # errors
    "ScreeningError",
    "EmptySlateRefused",
    "SlateRefRefused",
    "BaseRequestRefused",
    "SubjectRefused",
    "ContextSnapshotRefused",
    "ClockRefused",
    "BatchAdmissionRefused",
    # ports
    "SubjectCommitter",
    # contracts
    "ScreeningLaneEntry",
    "ScreeningCostPreview",
    "ScreeningBatch",
    # composites
    "MaterializedScreeningLane",
    "MaterializedScreeningBatch",
    # surface
    "build_screening_batch",
    "screening_cost_preview",
    "admit_screening_batch",
    "session_date_of",
]


# =========================================================================== #
# identity                                                                     #
# =========================================================================== #
#: the sealed screening-lane preset id (ONE digest serves every candidate, so one
#: lease binds the whole batch).
SCREENING_LANE_PRESET_ID: LogicalId = "pipeline.screening_lane"
SCREENING_LANE_PRESET_VERSION: str = "1"

#: the one bounded bull/bear debate this preset declares.
SCREENING_LANE_DEBATE_ID: LogicalId = "bullbear"

#: the SOLE terminal node id (``dec.research_mgr`` → ``ResearchPlan@1``). It is
#: also the debate's judge — a judge is never a seat of its own debate.
SCREENING_LANE_TERMINAL_NODE_ID: LogicalId = "research-mgr"

#: the frozen validation-green worker set. ``text.sentiment`` is NOT decoration:
#: ``dec.research_mgr``'s ``sentiment`` input is REQUIRED and it is its only
#: producer (the same forcing Task 6 recorded for ``dec.pm``).
SCREENING_LANE_WORKER_IDS: tuple[str, ...] = (
    "text.news",
    "text.research_report",
    "quant.factor",
    "quant.backtest",
    "pv.price_action",
    "text.sentiment",
    "dec.bull",
    "dec.bear",
    "dec.research_mgr",
)

#: the committed subject artifact's schema identity (Task 1's ``RunSubject@1``).
RUN_SUBJECT_SCHEMA_REF: SchemaRef = SchemaRef(name="RunSubject", version="1")

#: the slate artifact's schema identity (Task 1's ``CandidateSlate@1``).
CANDIDATE_SLATE_SCHEMA_REF: SchemaRef = SchemaRef(name="CandidateSlate", version="1")


# =========================================================================== #
# committed source location                                                    #
# =========================================================================== #
#: the committed source of this preset. The ``v2/`` subdirectory is load-bearing
#: (see the module docstring, reality 1); the directory constant and the v2 record
#: model / loader are shared Phase-10 preset infrastructure owned by ``assembly``.
SCREENING_LANE_PRESET_FILE: Path = PHASE10_PRESETS_V2_DIR / "screening_lane_v1.json"


# =========================================================================== #
# badges                                                                       #
# =========================================================================== #
#: ALWAYS emitted: the subject is run-scoped. It names the deferred assembly
#: threading (clause E2b, Task 7) instead of implying a plan-level binding.
SUBJECT_RUN_SCOPED_BADGE: str = "subject_run_scoped_v1"

#: emitted when the bound ContextSnapshot's data date is BEHIND the clock's +08:00
#: session date — honest staleness, never a silent intraday re-run of Lane 0.
CONTEXT_SNAPSHOT_STALE_BADGE: str = "context_snapshot_stale"

#: accompanies the stale badge with the snapshot's own session date.
CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX: str = "context_snapshot_data_date:"

#: MANDATORY on every :class:`ScreeningCostPreview` (the ``subject_run_scoped_v1``
#: precedent, and ``RecommendationSlate``'s mandatory-badge shape). The human
#: approving N × 6 LLM nodes must be told what those nodes currently buy: SEVEN of
#: the nine workers are ``auxiliary=True``, and their reports reach the terminal
#: ``dec.research_mgr`` only through the debate seats' shared transcript — whose
#: reduction is not (and cannot yet be) a plan dependency. Wiring those evidence
#: reports into the judge's model request is Phase-9 data-method work. The badge is
#: fixed, not computed: it states a property of the sealed graph, and it must be
#: removed only when that graph changes (which moves the record golden too).
AUXILIARY_EVIDENCE_UNWIRED_BADGE: str = "auxiliary_evidence_unwired_v1"


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class ScreeningError(ValueError):
    """The screening lane cannot be built or admitted as asked (typed, never silent)."""


class EmptySlateRefused(ScreeningError):
    """The slate names no candidate, so there is no batch to build.

    An empty ``CandidateSlate`` is a legal, honest product (无候选), but a
    zero-candidate *batch* is not a thing: it would mint a batch id, a cost
    preview reading "0 codes" and a lease-shaped approval form for nothing.
    """


class SlateRefRefused(ScreeningError):
    """The supplied slate ref does not pin ``CandidateSlate@1`` or does not bind
    the supplied slate's content."""


class BaseRequestRefused(ScreeningError):
    """The base request cannot authorize a screening batch.

    Either it does not name this preset in ``fallback_preset_id`` (the Phase-7
    selection rule, which is what makes the card's ``preset_fallback`` provenance
    TRUE rather than relabelled), or it carries ``approval_policy=AUTO`` — a
    Phase-10 red line that is refused at the door rather than carded.
    """


class SubjectRefused(ScreeningError):
    """The subject-commit port did not return a ref binding the subject it was given."""


class ContextSnapshotRefused(ScreeningError):
    """The latest committed Lane-0 ``ContextSnapshot`` is missing or unbound.

    Refused rather than fabricated: a screening batch never re-runs Lane 0 and
    never invents a context.
    """


class ClockRefused(ScreeningError):
    """The supplied clock returned a naive (tz-unaware) datetime.

    Refused rather than coerced: :func:`session_date_of` calls ``astimezone``,
    which reads a naive datetime as **machine-local** time — on a non-UTC host
    that silently shifts the +08:00 session date and flips the recency badge.
    """


class BatchAdmissionRefused(ScreeningError):
    """A batch entry does not bind the candidate the admission service prepared.

    Raised by :func:`admit_screening_batch`'s pre-flight, BEFORE any reservation
    or journal row — the reviewed replay-cards atomicity idiom
    (adapters/replay_cards.py:419-426): every lane is checked before any lane is
    registered, so a refusal leaves the journal untouched.
    """


@runtime_checkable
class _Clock(Protocol):
    """The authoritative clock port (``now() -> datetime``); never a module global."""

    def now(self) -> Any:  # pragma: no cover - structural protocol
        ...


#: the injected subject-commit port: it persists one ``RunSubject@1`` and returns
#: the typed ref of the committed payload. A port, not a store, because this
#: module performs no I/O — and because ``RunSubject@1`` is not registered in any
#: sealed registry yet (Task 11 owns that), so the real ``payloads.put`` lands
#: with its registration.
SubjectCommitter = Callable[[RunSubject], TypedPayloadRef]


# =========================================================================== #
# contracts (internal carriers — the Phase-10 firewall is Task 11's)           #
# =========================================================================== #
class ScreeningLaneEntry(DigestModel):
    """One candidate's lane inside a batch: its identity, its plan, its cost.

    ``lane_index`` is the 0-based slate position (the same index vocabulary
    ``RecommendationSlate.degraded_lanes`` uses, so Task 4 can join the two
    without inventing a mapping). ``subject_ref`` is the committed ``RunSubject@1``
    that names this lane's stock — the graph itself carries no code.
    ``llm_node_count`` is the lane's LLM-execution node count, read from the bound
    catalog at build time so the preview never has to guess it.
    """

    schema_version: Literal["1"] = "1"
    code: NonEmptyStr
    lane_index: NonNegativeInt
    request_id: NonEmptyStr
    draft_id: LogicalId
    candidate_plan_digest: DigestHex
    subject_ref: TypedPayloadRef
    budget_request_tokens: NonNegativeInt
    budget_request_llm_invocations: NonNegativeInt
    llm_node_count: NonNegativeInt
    max_concurrency: PositiveInt

    @model_validator(mode="after")
    def _verify(self) -> "ScreeningLaneEntry":
        if self.subject_ref.schema_ref != RUN_SUBJECT_SCHEMA_REF:
            raise ValueError(
                f"subject_ref must name {RUN_SUBJECT_SCHEMA_REF.key}; got "
                f"{self.subject_ref.schema_ref.key}")
        return self


class ScreeningCostPreview(DigestModel):
    """The whole picture a human approves before N lanes ever run.

    Deterministic and pure. Its JSON is what the screening ``ApprovalLease``'s
    issue form mirrors (``max_admissions=n_codes``,
    ``budget_cap_llm_invocations=total_budget_llm_invocations``) and what the N
    pending cards add up to when no lease is active.

    The arithmetic is STRUCTURAL: every total must equal ``n_codes`` × its
    per-lane value, so a preview whose totals quietly disagree with the batch it
    claims to describe cannot be constructed at all. ``max_concurrency`` has no
    total on purpose — it is a per-run slot cap, not an additive cost, and
    summing it would read as a promise about parallelism this module does not make.

    ``badges`` has NO default and must contain
    :data:`AUXILIARY_EVIDENCE_UNWIRED_BADGE`: the numbers alone would let a human
    approve N × 6 LLM nodes believing all nine workers' reports reach the verdict,
    and today seven of them are auxiliary evidence whose only path to the terminal
    is the debate transcript. An honest price tag names what it does not yet buy.
    """

    schema_version: Literal["1"] = "1"
    n_codes: PositiveInt
    per_lane_llm_nodes: NonNegativeInt
    total_llm_nodes: NonNegativeInt
    per_lane_budget_tokens: NonNegativeInt
    total_budget_tokens: NonNegativeInt
    per_lane_budget_llm_invocations: NonNegativeInt
    total_budget_llm_invocations: NonNegativeInt
    per_lane_max_concurrency: PositiveInt
    badges: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _arithmetic(self) -> "ScreeningCostPreview":
        if AUXILIARY_EVIDENCE_UNWIRED_BADGE not in self.badges:
            raise ValueError(
                f"badges must contain {AUXILIARY_EVIDENCE_UNWIRED_BADGE!r} — a "
                "screening cost preview always declares that seven of the nine "
                "lane workers are auxiliary evidence whose reports reach the "
                "terminal only through the debate transcript")
        for total_name, per_lane_name in (
            ("total_llm_nodes", "per_lane_llm_nodes"),
            ("total_budget_tokens", "per_lane_budget_tokens"),
            ("total_budget_llm_invocations", "per_lane_budget_llm_invocations"),
        ):
            expected = self.n_codes * getattr(self, per_lane_name)
            if getattr(self, total_name) != expected:
                raise ValueError(
                    f"{total_name} must equal n_codes × {per_lane_name} "
                    f"({self.n_codes} × {getattr(self, per_lane_name)} = {expected}); "
                    f"got {getattr(self, total_name)}")
        return self


class ScreeningBatch(DigestModel):
    """One screening batch: the slate it came from, its N lanes, its whole cost.

    Internal to Phase 10 (registration is the chain task's). It is deliberately
    NOT a carrier of drafts: the executable plans are service-owned (the Phase-2
    admission service loads a draft by ``draft_id``, never from a caller), so the
    batch carries identity + cost and :func:`admit_screening_batch` reads the
    plans back from the service — the "authority is never caller-carried" rule.

    ``batch_id`` binds the slate ref, the base request's semantics, **the bound
    ContextSnapshot's content digest**, this preset's identity+digest and the chain
    digests the lanes were built against, so the same slate + the same request
    semantics against the same context on the same chain always yield the same
    batch id — and a refreshed Lane-0 context, a moved chain or a re-reviewed
    preset never reuses one. The context axis is not optional: the per-lane
    candidate digests bind the context content (``compute_candidate_plan_digest``),
    so a batch id that ignored it would key different plans under one identity and
    the derived idempotency keys would collide on the second run.
    """

    schema_version: Literal["1"] = "1"
    batch_id: DigestHex
    candidate_slate_ref: TypedPayloadRef
    base_request_digest: DigestHex
    context_content_digest: DigestHex
    as_of: UtcDateTime
    preset_id: LogicalId
    preset_record_digest: DigestHex
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    entries: tuple[ScreeningLaneEntry, ...]
    cost_preview: ScreeningCostPreview
    badges: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "ScreeningBatch":
        if self.candidate_slate_ref.schema_ref != CANDIDATE_SLATE_SCHEMA_REF:
            raise ValueError(
                f"candidate_slate_ref must name {CANDIDATE_SLATE_SCHEMA_REF.key}; "
                f"got {self.candidate_slate_ref.schema_ref.key}")
        if not self.entries:
            raise ValueError(
                "a screening batch must carry at least one lane (an empty slate "
                "refuses batch construction outright)")
        for field, values in (
            ("code", [e.code for e in self.entries]),
            ("request_id", [e.request_id for e in self.entries]),
            ("draft_id", [e.draft_id for e in self.entries]),
            ("candidate_plan_digest", [e.candidate_plan_digest for e in self.entries]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"batch entries must carry a unique {field}")
        if [e.lane_index for e in self.entries] != list(range(len(self.entries))):
            raise ValueError(
                "batch entries must carry contiguous 0-based lane_index values in "
                "slate order")
        recomputed = _preview_of(self.entries)
        if recomputed != self.cost_preview:
            raise ValueError(
                "cost_preview does not equal the preview computed from this "
                "batch's own lanes; the human's whole picture must be the sum of "
                "the plans it describes")
        return self


# =========================================================================== #
# composites (invisible to the ContractModel firewall by design)               #
# =========================================================================== #
@dataclass(frozen=True)
class MaterializedScreeningLane:
    """One code's materialized lane: its request, its draft, its run-scoped subject.

    A plain frozen dataclass, the Task-6 ``MaterializedDeepDecide`` precedent:
    ``PlanDraft`` has no badge field and no external-input field, so the honest
    badges and the committed ``subject_ref`` travel beside the plan rather than
    being forged into it.
    """

    code: str
    lane_index: int
    request: OrchestrationRequest
    draft: PlanDraft
    subject_ref: TypedPayloadRef
    candidate_plan_digest: str
    badges: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializedScreeningBatch:
    """The builder's full product: the digest-bound batch + its materialized lanes.

    ``batch`` alone is what :func:`admit_screening_batch` and Task 4 consume; the
    ``lanes`` are what the caller registers into the Phase-2 admission service
    (``requests=`` / ``drafts=``) and what Task 7 hands the runner.
    """

    batch: ScreeningBatch
    lanes: tuple[MaterializedScreeningLane, ...]

    @property
    def batch_id(self) -> str:
        return self.batch.batch_id

    @property
    def cost_preview(self) -> ScreeningCostPreview:
        return self.batch.cost_preview


# =========================================================================== #
# pure helpers                                                                 #
# =========================================================================== #
def _uniform(entries: Sequence[ScreeningLaneEntry], field: str) -> int:
    values = {getattr(e, field) for e in entries}
    if len(values) != 1:
        raise ScreeningError(
            f"every lane of a batch runs the SAME sealed preset, so {field} must "
            f"be uniform across lanes; got {sorted(values)}")
    return values.pop()


def _preview_of(entries: Sequence[ScreeningLaneEntry]) -> ScreeningCostPreview:
    """The whole-picture preview of these lanes (totals are real sums)."""
    if not entries:
        raise ScreeningError("a cost preview needs at least one lane")
    return ScreeningCostPreview(
        n_codes=len(entries),
        per_lane_llm_nodes=_uniform(entries, "llm_node_count"),
        total_llm_nodes=sum(e.llm_node_count for e in entries),
        per_lane_budget_tokens=_uniform(entries, "budget_request_tokens"),
        total_budget_tokens=sum(e.budget_request_tokens for e in entries),
        per_lane_budget_llm_invocations=_uniform(
            entries, "budget_request_llm_invocations"),
        total_budget_llm_invocations=sum(
            e.budget_request_llm_invocations for e in entries),
        per_lane_max_concurrency=_uniform(entries, "max_concurrency"),
        badges=(AUXILIARY_EVIDENCE_UNWIRED_BADGE,),
    )


def _lane_request(base: OrchestrationRequest, code: str) -> OrchestrationRequest:
    """The per-code request: the base record with ONLY its identity re-stamped.

    The goal is copied verbatim — the code lives in the identity, never in a
    free-text field a later prompt assembler might carry into a model.
    """
    return base.model_copy(update={"request_id": f"{base.request_id}:{code}"})


def _batch_id(
    *,
    slate_ref: TypedPayloadRef,
    base_request: OrchestrationRequest,
    context_content_digest: str,
    preset_id: str,
    preset_record_digest: str,
    catalog_digest: str,
    schema_registry_digest: str,
) -> str:
    """Everything the lanes' candidate digests bind, folded into one identity.

    The ContextSnapshot content digest is in here for a concrete reason: it is an
    input to every lane's ``compute_candidate_plan_digest``, so two batches built
    from the same slate + request against DIFFERENT contexts hold different plans.
    A batch id blind to that would hand both the same derived idempotency keys.
    """
    return content_digest([
        "screening-batch-v1",
        slate_ref.payload_ref.content_digest,
        base_request.semantic_digest(),
        context_content_digest,
        preset_id,
        preset_record_digest,
        catalog_digest,
        schema_registry_digest,
    ])


# =========================================================================== #
# build_screening_batch                                                        #
# =========================================================================== #
def build_screening_batch(
    slate: CandidateSlate,
    *,
    preset_registry: PlanPresetRegistry,
    clock: _Clock,
    base_request: OrchestrationRequest,
    # -- ABI-forced additions (recorded, never a parallel runtime) ------------ #
    # the four kwargs above are the reviewed surface. Materializing a Phase-1
    # ``PlanDraft`` additionally needs the committed slate's ref (the batch pins
    # what it was built from), the subject-commit port (Amendment 3's per-code
    # ``RunSubject@1``), the ContextSnapshot + its ref (``as_of``/``mode`` and the
    # recency comparison — there is no accessor anywhere in the kernel that could
    # fetch the latest committed snapshot for us, only a writer) and the sealed
    # catalog/registry the draft binds. ``materialize_fallback_draft`` takes the
    # same last four (plan_presets.py:305-315).
    slate_ref: TypedPayloadRef,
    subject_committer: SubjectCommitter,
    context: ContextSnapshot | None,
    context_snapshot_ref: PayloadRef | None,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
) -> MaterializedScreeningBatch:
    """Materialize ONE sealed lane per slate entry, under ONE ``batch_id``.

    Refusal order is deliberate and typed: an empty slate
    (:class:`EmptySlateRefused`), an unbound / mis-schemed slate ref
    (:class:`SlateRefRefused`), a base request that does not name this preset or
    carries ``AUTO`` (:class:`BaseRequestRefused`), a missing / unbound context
    (:class:`ContextSnapshotRefused`), a naive clock (:class:`ClockRefused`), a
    lying subject port (:class:`SubjectRefused`). Nothing is defaulted or coerced.

    Every returned draft is a plain Phase-1 ``PlanDraft`` whose identity and
    authority fields are runtime-stamped and whose reviewed graph is copied
    verbatim from the sealed record, so the N executable projections are
    byte-identical: one record digest, one lease, N run-scoped subjects.
    """
    # -- 1. the slate, and its committed ref ---------------------------------- #
    if not slate.entries:
        raise EmptySlateRefused(
            "the candidate slate names no code; refusing to build a zero-candidate "
            "screening batch (an empty slate is an honest product, an empty batch "
            "is not)")
    if slate_ref is None or not isinstance(slate_ref, TypedPayloadRef):
        raise SlateRefRefused(
            "slate_ref must be the TypedPayloadRef of the committed CandidateSlate; "
            f"got {type(slate_ref).__name__}")
    if slate_ref.schema_ref != CANDIDATE_SLATE_SCHEMA_REF:
        raise SlateRefRefused(
            f"slate_ref must name {CANDIDATE_SLATE_SCHEMA_REF.key}; got "
            f"{slate_ref.schema_ref.key}")
    if slate_ref.payload_ref.content_digest != slate.semantic_digest():
        raise SlateRefRefused(
            "slate_ref.payload_ref.content_digest does not bind the supplied "
            "CandidateSlate content")

    # -- 2. the base request: the Phase-7 selection rule + the AUTO red line -- #
    if base_request.fallback_preset_id != SCREENING_LANE_PRESET_ID:
        raise BaseRequestRefused(
            "base_request.fallback_preset_id "
            f"{base_request.fallback_preset_id!r} does not name "
            f"{SCREENING_LANE_PRESET_ID!r}; a preset is only ever selected by exact "
            "fallback_preset_id equality, and that equality is what makes the "
            "reviewer card's preset_fallback provenance true rather than relabelled")
    if base_request.approval_policy is not ApprovalPolicy.REQUIRED:
        raise BaseRequestRefused(
            "base_request.approval_policy is AUTO; a Phase-10 screening batch is "
            "refused at the door rather than carded (a pending human card is "
            "unconstructible for AUTO, and Phase-1 validation refuses it too)")

    # -- 3. the latest committed Lane-0 ContextSnapshot ---------------------- #
    if context is None or context_snapshot_ref is None:
        raise ContextSnapshotRefused(
            "the latest committed Lane-0 ContextSnapshot (and its ref) are "
            "required; refusing rather than fabricating a context — a screening "
            "batch never re-runs Lane 0")
    if context_snapshot_ref.namespace != "main":
        raise ContextSnapshotRefused(
            "context_snapshot_ref must use namespace='main'; got "
            f"{context_snapshot_ref.namespace!r}")
    if context_snapshot_ref.content_digest != context.content_digest:
        raise ContextSnapshotRefused(
            "context_snapshot_ref.content_digest does not bind the supplied "
            "ContextSnapshot content")

    # -- 4. the sealed record (must be the v2 generation) -------------------- #
    record = preset_registry.get(SCREENING_LANE_PRESET_ID)
    if not isinstance(record, PlanPresetRecordV2):
        raise ScreeningError(
            f"{SCREENING_LANE_PRESET_ID!r} must be a PlanPresetRecordV2 "
            f"({PLAN_PRESET_RECORD_V2_SCHEMA_REF.key}); the registered record is a "
            f"{type(record).__name__} and cannot carry this graph's debate")

    # -- 5. final-workers-only catalog-role gate (before any construction) ---- #
    worker_by_id = {w.id: w for w in catalog.workers}
    non_final = sorted({
        node.worker_id for node in record.nodes
        if worker_by_id.get(node.worker_id) is None
        or worker_by_id[node.worker_id].catalog_role != "final"
    })
    if non_final:
        raise ScreeningError(
            "preset worker(s) not catalog_role='final' in the bound catalog: "
            + ", ".join(non_final))

    # -- 6. honest recency, in +08:00 session terms -------------------------- #
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ClockRefused(
            "clock.now() must return a timezone-aware datetime; got "
            f"{now!r} — a naive datetime would be read as machine-local time and "
            "silently shift the +08:00 session date")
    session_date = session_date_of(now)
    snapshot_date = session_date_of(context.data_context.as_of)
    badges: list[str] = [SUBJECT_RUN_SCOPED_BADGE]
    if snapshot_date < session_date:
        badges.append(CONTEXT_SNAPSHOT_STALE_BADGE)
        badges.append(f"{CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX}{snapshot_date}")
    badge_tuple = tuple(badges)

    # -- 7. the batch identity (computed BEFORE materialization) ------------- #
    record_digest = record.semantic_digest()
    batch_id = _batch_id(
        slate_ref=slate_ref, base_request=base_request,
        context_content_digest=context.content_digest,
        preset_id=SCREENING_LANE_PRESET_ID, preset_record_digest=record_digest,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest)

    llm_node_count = sum(
        1 for node in record.nodes
        if worker_by_id[node.worker_id].execution.kind is ExecutionKind.LLM)

    # -- 8. one lane per candidate ------------------------------------------- #
    lanes: list[MaterializedScreeningLane] = []
    entries: list[ScreeningLaneEntry] = []
    for lane_index, candidate in enumerate(slate.entries):
        code = candidate.code
        request = _lane_request(base_request, code)
        subject = RunSubject(code=code, as_of=slate.as_of)
        subject_ref = subject_committer(subject)
        if not isinstance(subject_ref, TypedPayloadRef):
            raise SubjectRefused(
                "the subject committer must return a TypedPayloadRef naming the "
                f"committed subject; got {type(subject_ref).__name__}")
        if subject_ref.schema_ref != RUN_SUBJECT_SCHEMA_REF:
            raise SubjectRefused(
                f"the committed subject ref must name {RUN_SUBJECT_SCHEMA_REF.key}; "
                f"got {subject_ref.schema_ref.key}")
        if subject_ref.payload_ref.content_digest != subject.semantic_digest():
            raise SubjectRefused(
                f"the committed subject ref for {code!r} does not bind the subject "
                "content it was handed; a subject that is not digest-bound is not "
                "an authority")

        draft = PlanDraft(
            id=f"screening.{batch_id[:16]}.{code}",
            run_id=f"screening-{batch_id[:16]}-{code}",
            request_id=request.request_id,
            phase="main",
            # PRESET_FALLBACK, not PRESET: the lease channel is structurally
            # card-only and a card covers only DYNAMIC/PRESET_FALLBACK. The
            # provenance is true — the request names this preset (checked above).
            source=PlanSource.PRESET_FALLBACK,
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
        candidate_plan_digest = compute_candidate_plan_digest(
            request=request, draft=draft,
            context_content_digest=context.content_digest)
        lanes.append(MaterializedScreeningLane(
            code=code, lane_index=lane_index, request=request, draft=draft,
            subject_ref=subject_ref, candidate_plan_digest=candidate_plan_digest,
            badges=badge_tuple))
        entries.append(ScreeningLaneEntry(
            code=code, lane_index=lane_index, request_id=request.request_id,
            draft_id=draft.id, candidate_plan_digest=candidate_plan_digest,
            subject_ref=subject_ref,
            budget_request_tokens=draft.budget_request_tokens,
            budget_request_llm_invocations=draft.budget_request_llm_invocations,
            llm_node_count=llm_node_count,
            max_concurrency=draft.max_concurrency))

    entry_tuple = tuple(entries)
    batch = ScreeningBatch(
        batch_id=batch_id,
        candidate_slate_ref=slate_ref,
        base_request_digest=base_request.semantic_digest(),
        context_content_digest=context.content_digest,
        as_of=slate.as_of,
        preset_id=SCREENING_LANE_PRESET_ID,
        preset_record_digest=record_digest,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        entries=entry_tuple,
        cost_preview=_preview_of(entry_tuple),
        badges=badge_tuple,
    )
    return MaterializedScreeningBatch(batch=batch, lanes=tuple(lanes))


# =========================================================================== #
# screening_cost_preview                                                       #
# =========================================================================== #
def screening_cost_preview(batch: ScreeningBatch) -> ScreeningCostPreview:
    """The batch's whole-picture cost — deterministic, pure, recomputed.

    Recomputed from the batch's own lanes rather than read off the stored field,
    so a caller can verify the preview a human was shown against the plans it
    claims to describe (the batch's own validator pins them equal, which makes a
    tampered preview unconstructible; this function is how a consumer checks).
    """
    return _preview_of(batch.entries)


# =========================================================================== #
# admit_screening_batch                                                        #
# =========================================================================== #
def admit_screening_batch(
    batch: ScreeningBatch,
    *,
    coordinator: Any,
    admission: Any,
    now: UtcDateTime,
    # -- ABI-forced additions (recorded) -------------------------------------- #
    # a pending card references its ``PlanDiff@1`` payload by a ref that must
    # RESOLVE, so the diff is really committed before the card is built — exactly
    # ``build_replay_lane_card``'s parameters (adapters/replay_cards.py:316-322).
    payloads: Any,
    registry_digest: str,
    # the run's budget ledger (``BudgetLedger`` over the run's sink + RunBudget —
    # the reviewed `_shared_ledger` idiom). The Phase-2 service keeps its own
    # ledger private, and the whole-picture guard below has to read remaining
    # capacity BEFORE the first reservation is written.
    budget: Any,
) -> tuple[LeaseAdmissionOutcome, ...]:
    """Admit every lane of ``batch``: validate → reserve → try the lease. Stop there.

    Per candidate, in batch order:

    1. the Phase-2 service prepares the candidate (which runs the unmodified
       Phase-1 :func:`~guanlan_v2.orchestration.spec.validate_plan_draft` and the
       runtime-support check, and re-derives the candidate digest);
    2. the same service persists both reports and reserves the plan's budget;
    3. the diff payload is committed and the reviewer card built, then
       ``coordinator.register_and_try_lease`` registers it and admits it **iff** an
       active screening lease matches its preset/chain identity and has room in
       its envelope.

    An active lease admits within its envelope; **everything else stays a pending
    human card** (``pending_human``). There is no third outcome, no self-approval,
    and no execution: this function stops at admission — it never freezes a plan,
    never dispatches, and never mints an approval outside the lease channel.

    **Envelope exhaustion mid-batch is not an error.** When the lease runs out of
    admissions or budget, ``_find_admissible_lease`` simply stops matching and the
    remaining lanes come back ``pending_human`` — admitted and pending are
    disjoint and together cover the batch exactly.

    **Pre-flight, then act** (the reviewed replay-cards atomicity idiom): every
    lane is prepared, digest-checked, Phase-1-checked and **runtime-support-checked**
    BEFORE any lane is reserved or journalled, and the batch's whole-picture cost is
    checked against the run's remaining budget, so a wiring fault — a drafted lane
    the service does not hold, a batch entry whose digest no longer recomputes, a
    lane that fails Phase-1 validation, a lane the runtime cannot support (today:
    every lane, under the sealed production catalog's data-bridge grant gap), or a
    run budget that cannot cover the previewed cost — raises
    :class:`BatchAdmissionRefused` with **nothing written**: no report payload, no
    reservation, no journal row. Those faults are batch-wide by construction (every
    lane runs the same sealed graph and draws on one run budget), never a per-code
    accident. Re-calling is idempotent: an already-consumed candidate completes
    without a second consume.

    The support check is not redundant with the service's own: without it, an
    unsupported lane surfaces as a raw ``AdmissionRejected`` from
    ``persist_and_reserve_candidate`` — *after* it has persisted both report
    payloads and possibly after earlier lanes were already reserved and carded.

    **A ``pending_human`` lane holds its reservation until a human decides, and
    that is by design** (controller ruling): the budget must be there if the human
    approves, and Phase 7's ``decide(REJECTED)`` is what releases it. This function
    therefore never releases a reservation, and a batch that stays pending keeps its
    previewed cost held — which is exactly what the human was shown.
    """
    # -- pre-flight: prepare + bind EVERY lane before touching anything ------- #
    prepared = []
    for entry in batch.entries:
        try:
            prep = admission.prepare_candidate(
                entry.draft_id, request_id=entry.request_id)
        except AdmissionRejected as exc:
            # ONLY the service's own typed refusal is re-raised as ours (named by
            # lane). Anything else — an AttributeError, a store failure — is a bug
            # or an outage and propagates raw rather than being dressed as a
            # refusal a human could mistake for a considered verdict.
            raise BatchAdmissionRefused(
                f"lane {entry.lane_index} ({entry.code}) could not be prepared for "
                f"admission [{exc.code}]: {exc}") from exc
        if prep.candidate_plan_digest != entry.candidate_plan_digest:
            raise BatchAdmissionRefused(
                f"lane {entry.lane_index} ({entry.code}) candidate_plan_digest does "
                f"not recompute from the prepared plan (batch says "
                f"{entry.candidate_plan_digest!r}, the service computed "
                f"{prep.candidate_plan_digest!r}); an approval that does not bind "
                "the plan the reviewer saw is worthless")
        if not prep.phase1_report.valid:
            raise BatchAdmissionRefused(
                f"lane {entry.lane_index} ({entry.code}) fails Phase-1 validation: "
                + ", ".join(sorted({i.code for i in prep.phase1_report.issues})))
        if not prep.support_report.supported:
            raise BatchAdmissionRefused(
                f"lane {entry.lane_index} ({entry.code}) is not runtime-supported: "
                + ", ".join(sorted({i.code for i in prep.support_report.issues}))
                + " — refused write-free, before any report payload or reservation")
        prepared.append((entry, prep))

    # -- pre-flight: the whole picture must fit the run's remaining budget ---- #
    # The preview exists to be the human's whole picture; this is where it also
    # becomes the machine's. Checked before the FIRST reservation so a batch that
    # cannot fit refuses cleanly instead of half-reserving and aborting mid-loop.
    available = budget.available()
    preview = batch.cost_preview
    if (preview.total_budget_llm_invocations > available.llm_invocations
            or preview.total_budget_tokens > available.tokens):
        raise BatchAdmissionRefused(
            f"the batch's previewed cost ({preview.n_codes} lanes = "
            f"{preview.total_budget_llm_invocations} llm invocations / "
            f"{preview.total_budget_tokens} tokens) exceeds the run's remaining "
            f"budget ({available.llm_invocations} llm invocations / "
            f"{available.tokens} tokens); refused write-free rather than reserving "
            "part of a batch a human approved as a whole")

    # -- act: reserve, card, lease — lane by lane, in batch order ------------- #
    outcomes: list[LeaseAdmissionOutcome] = []
    for entry, prep in prepared:
        admission.persist_and_reserve_candidate(
            prep, idempotency_key=f"screening-reserve:{batch.batch_id}:{entry.code}")
        diff = build_plan_diff(
            prep.draft, request=prep.request,
            candidate_plan_digest=entry.candidate_plan_digest,
            baseline=None, baseline_kind="none")
        payload_ref = payloads.put(
            PLAN_DIFF_SCHEMA_REF, diff, registry_digest=registry_digest,
            namespace="main",
            idempotency_key=f"screening-plan-diff:{batch.batch_id}:{entry.code}")
        pending = build_pending_plan_approval(
            draft=prep.draft, request=prep.request,
            candidate_plan_digest=entry.candidate_plan_digest, diff=diff,
            plan_diff_ref=TypedPayloadRef(
                schema_ref=PLAN_DIFF_SCHEMA_REF, payload_ref=payload_ref),
            planner_rationale=None,
            candidate_id=(
                f"screening-card.{entry.code}."
                f"{entry.candidate_plan_digest[:16]}"),
            requested_at=now,
            preset_id=batch.preset_id,
            preset_record_digest=batch.preset_record_digest)
        outcomes.append(coordinator.register_and_try_lease(
            pending,
            idempotency_key=f"screening-card:{batch.batch_id}:{entry.code}",
            now=now,
            candidate_catalog_digest=prep.draft.catalog_digest,
            candidate_registry_digest=prep.draft.schema_registry_digest))
    return tuple(outcomes)
