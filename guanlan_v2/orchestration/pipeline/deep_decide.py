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
   baseline while :func:`load_phase10_preset_registry` reads both generations.

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
   instrument-param data prefetch is clause E2b (the runner invocation half is
   Task 7). The always-present :data:`SUBJECT_RUN_SCOPED_BADGE` says so on every
   materialization rather than implying a binding that does not exist.

``PlanDraft`` also has no badge field, so materialization returns the composite
:class:`MaterializedDeepDecide` (draft + subject ref + context ref + badges).
Registration of ``PlanPresetRecord@2`` into the cumulative Phase-10 registry is
the Phase-10 chain task's, exactly like Task 1's contracts.
"""
from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.debate import DEBATE_MAX_ROUNDS
from guanlan_v2.orchestration.digest import NonEmptyStr
from guanlan_v2.orchestration.enums import PlanSource
from guanlan_v2.orchestration.market.factors import _session_date as session_date_of
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRecord,
    PlanPresetRegistry,
)
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import DebateCfg, OrchestrationRequest, PlanDraft, ReducerCfg

from guanlan_v2.orchestration.pipeline.assembly import PRODUCTION_PRESETS_DIR

__all__ = [
    # identity
    "DEEP_DECIDE_PRESET_ID",
    "DEEP_DECIDE_PRESET_VERSION",
    "DEEP_DECIDE_DEBATE_ID",
    "DEEP_DECIDE_TERMINAL_NODE_ID",
    "DEEP_DECIDE_WORKER_IDS",
    "PLAN_PRESET_RECORD_V2_SCHEMA_REF",
    "RUN_SUBJECT_SCHEMA_REF",
    # committed source
    "PHASE10_PRESETS_V2_DIRNAME",
    "PHASE10_PRESETS_V2_DIR",
    "DEEP_DECIDE_PRESET_FILE",
    # badges
    "CONTEXT_SNAPSHOT_STALE_BADGE",
    "CONTEXT_SNAPSHOT_DATA_DATE_BADGE_PREFIX",
    "SUBJECT_RUN_SCOPED_BADGE",
    # errors
    "DeepDecideError",
    "SubjectRefused",
    "ContextSnapshotRefused",
    # surface
    "PlanPresetRecordV2",
    "load_phase10_preset_registry",
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

#: the registered identity of the Phase-10 preset record generation. Registration
#: itself belongs to the Phase-10 chain task (the Task-1 contracts precedent).
PLAN_PRESET_RECORD_V2_SCHEMA_REF: SchemaRef = SchemaRef(
    name="PlanPresetRecord", version="2")

#: the committed subject artifact's schema identity (Task 1's ``RunSubject@1``).
RUN_SUBJECT_SCHEMA_REF: SchemaRef = SchemaRef(name="RunSubject", version="1")


# =========================================================================== #
# committed source location                                                    #
# =========================================================================== #
#: v2 preset files live in this subdirectory of the committed presets directory
#: precisely so the Phase-7 non-recursive ``root/*.json`` loader cannot see them
#: (see reality 2 in the module docstring).
PHASE10_PRESETS_V2_DIRNAME: str = "v2"
PHASE10_PRESETS_V2_DIR: Path = PRODUCTION_PRESETS_DIR / PHASE10_PRESETS_V2_DIRNAME
DEEP_DECIDE_PRESET_FILE: Path = PHASE10_PRESETS_V2_DIR / "luozi_deep_decide_v1.json"


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


@runtime_checkable
class _Clock(Protocol):
    """The authoritative clock port (``now() -> datetime``); never a module global."""

    def now(self) -> Any:  # pragma: no cover - structural protocol
        ...


# =========================================================================== #
# PlanPresetRecordV2 — the Phase-10 debate-carrying preset record               #
# =========================================================================== #
class PlanPresetRecordV2(PlanPresetRecord):
    """A reviewed preset that MAY declare one bounded debate (``PlanPresetRecord@2``).

    A strict superset of the Phase-7 v1 record: same identity/budget fields, plus
    ``debates`` / ``reducers`` in the implemented Phase-1 shapes. It subclasses the
    Phase-7 record so the Phase-7 registry's ``isinstance`` gate accepts it, and it
    OVERRIDES the v1 ``_admissible`` validator (which refuses debate identity
    outright). Everything v1 forbids for a non-debate reason stays forbidden:

    * no ``gate_ids`` and no ``condition_ref`` on any node (conditions remain locked
      under the v2 runtime profile; this preset declares no gates at all);
    * **no node params at all** — the structural half of "no plan param carries a
      code". Every worker in this generation of presets is paramsless, so Phase 1
      would refuse node params anyway (``params_not_allowed``, spec.py:951-954);
      refusing them here makes a code-carrying param impossible one layer earlier;
    * ``max_attempts <= 2`` (the v2 profile cap ``max_attempts_limit``).

    The debate rules mirror the implemented runtime checks so a malformed debate
    can never be sealed: every seat node's ``debate_id`` must name a declared
    debate and its ``round_role`` must be one of that debate's seats; every
    debate's ``judge_node_id`` must be a plan node that is NOT a seat of the same
    debate (``debate_judge_is_seat``); ``max_rounds <= DEBATE_MAX_ROUNDS``; and
    every multi-writer slot must carry exactly one ``ReducerCfg`` whose
    ``producer_node_ids`` equal that slot's writers.
    """

    schema_version: Literal["2"] = "2"  # type: ignore[assignment]
    debates: tuple[DebateCfg, ...] = ()
    reducers: tuple[ReducerCfg, ...] = ()

    @model_validator(mode="after")
    def _admissible(self) -> "PlanPresetRecordV2":  # type: ignore[override]
        # (transcribed from the v1 validator rather than delegated: a pydantic
        #  same-name override REPLACES the parent validator, and the v1 body's
        #  debate refusal is exactly what v2 exists to relax.)
        if not self.nodes:
            raise ValueError("a preset must declare at least one node")
        if not self.sink_node_ids:
            raise ValueError("a preset must declare at least one sink node id")

        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("preset node ids must be unique")
        node_by_id = {n.id: n for n in self.nodes}
        for sink in self.sink_node_ids:
            if sink not in node_by_id:
                raise ValueError(f"sink node id {sink!r} is not a preset node")

        for node in self.nodes:
            if node.gate_ids != ():
                raise ValueError(
                    f"preset node {node.id!r} must carry no gate_ids (v2 declares "
                    "no gates)")
            if node.condition_ref is not None:
                raise ValueError(
                    f"preset node {node.id!r} must carry no condition_ref "
                    "(conditions stay locked under the v2 runtime profile)")
            if node.params:
                raise ValueError(
                    f"preset node {node.id!r} must carry no params — a sealed v2 "
                    "preset is structurally code-free")
            if node.max_attempts > 2:
                raise ValueError(
                    f"preset node {node.id!r} max_attempts={node.max_attempts} "
                    "exceeds the v2 profile cap max_attempts_limit=2")

        # -- debate coherence (mirrors the implemented runtime checks) -------- #
        debate_by_id: dict[str, DebateCfg] = {}
        for debate in self.debates:
            if debate.id in debate_by_id:
                raise ValueError(f"duplicate debate id {debate.id!r}")
            debate_by_id[debate.id] = debate
            if debate.max_rounds > DEBATE_MAX_ROUNDS:
                raise ValueError(
                    f"debate {debate.id!r} max_rounds={debate.max_rounds} exceeds "
                    f"the reviewed Lane-D cap DEBATE_MAX_ROUNDS={DEBATE_MAX_ROUNDS}")
            if debate.judge_node_id not in node_by_id:
                raise ValueError(
                    f"debate {debate.id!r} judge_node_id {debate.judge_node_id!r} "
                    "is not a preset node")

        for node in self.nodes:
            if node.debate_id is None:
                continue
            debate = debate_by_id.get(node.debate_id)
            if debate is None:
                raise ValueError(
                    f"preset node {node.id!r} references undeclared debate "
                    f"{node.debate_id!r}")
            if node.round_role not in set(debate.seats):
                raise ValueError(
                    f"preset node {node.id!r} round_role {node.round_role!r} is not "
                    f"a seat of debate {debate.id!r}")

        for debate in self.debates:
            judge = node_by_id[debate.judge_node_id]
            if judge.debate_id == debate.id:
                raise ValueError(
                    f"debate {debate.id!r} judge_node_id {debate.judge_node_id!r} is "
                    "itself a seat of the same debate; the judge must be a distinct "
                    "plan node")

        # -- multi-writer slots must be deterministically reduced ------------- #
        slot_writers: dict[str, list[str]] = {}
        for node in self.nodes:
            slot_writers.setdefault(node.writes_slot, []).append(node.id)
        reducer_by_slot: dict[str, ReducerCfg] = {}
        for reducer in self.reducers:
            if reducer.slot in reducer_by_slot:
                raise ValueError(
                    f"slot {reducer.slot!r} has more than one reducer")
            reducer_by_slot[reducer.slot] = reducer
            writers = sorted(slot_writers.get(reducer.slot, ()))
            if not writers:
                raise ValueError(
                    f"reducer {reducer.id!r} slot {reducer.slot!r} is written by no "
                    "preset node")
            if sorted(reducer.producer_node_ids) != writers:
                raise ValueError(
                    f"reducer {reducer.id!r} producer_node_ids "
                    f"{sorted(reducer.producer_node_ids)} do not equal slot "
                    f"{reducer.slot!r} writers {writers}")
        for slot, writers in slot_writers.items():
            if len(writers) > 1 and slot not in reducer_by_slot:
                raise ValueError(
                    f"slot {slot!r} is written by {sorted(writers)} without a "
                    "deterministic reducer")
        return self


# =========================================================================== #
# the Phase-10 strict loader (v1 + v2 from ONE committed directory)             #
# =========================================================================== #
def _load_record(path: Path, model: type[PlanPresetRecord]) -> PlanPresetRecord:
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        raise PlanPresetError(
            f"preset file {path.name!r} must be UTF-8 with no byte-order mark")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanPresetError(
            f"preset file {path.name!r} is not valid UTF-8: {exc}") from exc
    try:
        return model.model_validate_json(text)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise PlanPresetError(
            f"preset file {path.name!r} is not a valid {model.__name__}: {exc}"
        ) from exc


def load_phase10_preset_registry(
    root: Path = PRODUCTION_PRESETS_DIR,
) -> PlanPresetRegistry:
    """Load, validate and seal BOTH preset generations under ``root``.

    ``root/*.json`` are Phase-7 :class:`PlanPresetRecord` v1 files (loaded exactly
    as ``plan_presets.load_preset_registry`` does — strict UTF-8, BOM refused,
    ``extra="forbid"``); ``root/v2/*.json`` are :class:`PlanPresetRecordV2` files.
    A ``preset_id`` duplicated *across either generation* is a
    :class:`PlanPresetError`, so the two directories are one namespace. The
    registry is sealed before it is returned and no physical path ever enters a
    record.

    The split is not cosmetic: the Phase-7 loader's glob is non-recursive, so
    keeping v2 files in the subdirectory is what lets that loader — and every
    existing caller of it — keep working byte-unchanged.
    """
    root = Path(root)
    registry = PlanPresetRegistry()
    seen: set[str] = set()

    def _ingest(paths, model: type[PlanPresetRecord]) -> None:
        for path in sorted(paths):
            record = _load_record(path, model)
            if record.preset_id in seen:
                raise PlanPresetError(
                    f"duplicate preset_id {record.preset_id!r} across preset files")
            seen.add(record.preset_id)
            registry.register(record)

    _ingest(root.glob("*.json"), PlanPresetRecord)
    _ingest((root / PHASE10_PRESETS_V2_DIRNAME).glob("*.json"), PlanPresetRecordV2)
    registry.seal()
    return registry


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
) -> MaterializedDeepDecide:
    """Materialize the sealed deep-decide preset into a ``source=PRESET`` draft.

    Refusal order is deliberate and typed. A missing / mistyped ``subject_ref`` is
    a :class:`SubjectRefused`; a missing or unbound ContextSnapshot is a
    :class:`ContextSnapshotRefused` (the live path degrades to its fast chain on
    that one). Neither is ever defaulted.

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
    directions.
    """
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
    record = preset_registry.get(DEEP_DECIDE_PRESET_ID)
    if not isinstance(record, PlanPresetRecordV2):
        raise DeepDecideError(
            f"{DEEP_DECIDE_PRESET_ID!r} must be a PlanPresetRecordV2 "
            f"({PLAN_PRESET_RECORD_V2_SCHEMA_REF.key}); the registered record is a "
            f"{type(record).__name__} and cannot carry this graph's debate")

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
    session_date = session_date_of(clock.now())
    snapshot_date = session_date_of(context.data_context.as_of)
    badges: list[str] = [SUBJECT_RUN_SCOPED_BADGE]
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
    )
