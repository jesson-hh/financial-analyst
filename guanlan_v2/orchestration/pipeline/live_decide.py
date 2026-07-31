# -*- coding: utf-8 -*-
"""Phase 10 · Task 7 — the lease-gated deep-decide wrapper on the watcher seam.

One callable, :func:`make_orchestrated_decide`, turns the seats watcher's fast
``decide_fn`` into a two-lane path: the fast judgment always runs exactly as
today, and — only when the pure Task-5 escalation judge fires — ONE budgeted,
lease-admitted deep kernel run executes the sealed Task-6 preset through the
Task-0b production plan runner, appending ONE additional orchestrated decision
row beside the fast row. Every deep failure returns the fast result with honest
markers; nothing here ever raises into the watcher tick.

The two-row model (invariant 1)
-------------------------------
The fast row persists inside ``seats.api._decide_impl`` exactly as today — this
module never suppresses, edits or replaces it. A COMPLETED deep run appends one
additional row through the seats helper (``kind="decide"``, ``source:
"orchestrated"``, ``run_id=<kernel run id>``, ``escalation_digest``,
``escalated_from_asof=<fast row asof>``, the trader proposal's target band and
trigger ranges) plus — when the proposal carries tranche trigger price ranges —
ONE advisory conditional-order record through the existing seats order-record
path (``kind="order"``; a human executes, 观澜 never trades). Idempotency key is
the kernel run id: a re-tick of the same escalation identity finds the prior
orchestrated row in the pre-captured tail and appends nothing.

The tail-before-fast trap (Task 5, Amendment 4 — regression-pinned)
-------------------------------------------------------------------
``_decide_impl`` persists its own decide row on the success path, so a decisions
tail read AFTER the fast call would contain this tick's own row: the previous
direction would equal the fast direction and ``direction_flip`` could never
fire. The wrapper therefore snapshots the tail BEFORE invoking ``fast_decide``.

E2b — the per-run subject-closed prompt assembler
-------------------------------------------------
The sealed deep-decide draft is structurally code-free (one record digest, one
lease, every stock) and the implemented trusted channel of the reviewed
``StaticPromptAssembler`` carries name+digest pairs only — no caller text. The
subject therefore enters at the ``prompt_assembler`` injection seam of
``assembly.build_production_plan_runner``: :class:`SubjectPromptAssembler` is
constructed PER RUN with the committed ``RunSubject@1`` artifact closed over,
owns the whole canonical channel (same shape as static-v1 plus one
``trusted_subject`` section rendered from OUR OWN reviewed subject fields), adds
one :class:`NamedEvidenceDigest` (``run_subject`` → the committed artifact's
content digest) to the trusted inputs, and stamps its own assembler identity on
the persisted ``PromptAssemblyRecord`` so the audit trail names it honestly.
The second E2b half — threading ``subject.code`` into the instrument-param data
prefetch — has NO reachable seam until Task 11 binds the data-bridge provider
factories and closes the reviewed prefetch grants (the strict-xfail pin in
``test_pipeline_deep_preset.py``); the committed subject artifact this module
produces is exactly what that provider will read. Recorded, not papered over.

ABI-forced corrections (recorded, mirroring the Task 0b/6 precedent)
--------------------------------------------------------------------
The plan sketched ``DeepDecideBindings.admission`` / ``coordinator`` /
``plan_runner`` as if they could be long-lived objects. They cannot:

* ``PlanAdmissionService`` binds ONE ``run_id`` + ``RunBudget`` and
  ``record_approval`` requires the SAME service instance that reserved the
  candidate (``admission.py:475`` — ``_candidates`` is per-instance), so
  ``bindings.admission`` is a per-run FACTORY
  ``(*, run_id, request, draft, context, approvals, run_budget) -> PlanAdmissionService``.
* ``PlanApprovalCoordinator`` is constructed over the per-run admission service
  (its ``_ensure_event`` calls ``admission.record_approval``), while the lease
  journal itself is durable and folded on every construction — so
  ``bindings.coordinator`` is a per-run FACTORY
  ``(*, admission, approvals_sink) -> PlanApprovalCoordinator`` over the ONE journal.
* ``build_production_plan_runner`` takes the admission artifacts and the
  ``prompt_assembler`` at build time, and the E2b assembler exists only per run —
  so ``bindings.plan_runner`` is a per-run FACTORY
  ``(*, admission, lane_bindings, run_context_factory, request_id, prompt_assembler) -> runner``
  whose production default IS ``build_production_plan_runner`` (the ONLY
  execution path; tests bind scripted gateways through the same
  ``gateway_factory`` seam of the same builder).

One sealed-vocabulary reconciliation: the deep draft is ``source=PRESET``
(Task 6), but the implemented :class:`PendingPlanApproval` accepts only
``DYNAMIC`` / ``PRESET_FALLBACK`` and carries preset provenance ONLY for
``PRESET_FALLBACK`` (plan_diff.py:261-293) — the card model predates the
``PRESET`` generation. The card this module registers therefore states
``source=PRESET_FALLBACK`` (the sealed vocabulary's only preset-provenance
value; the provenance pair itself — preset id + record digest — is exact and is
what the lease matches on). Widening the card vocabulary is an upstream contract
change for the Phase-10 chain task, not this module's to make.

Red lines (invariant 4)
-----------------------
No ledger trade write, no order execution, no signal: the only persistence
this module performs goes through the injected ``persist_decision`` port with
the decide/order advisory kinds (spec §5, 人工执行). The focused test sweeps the
source for any quoted trade literal, so even a future edit cannot smuggle one.
No engine import at module top level; seats surfaces are read lazily inside the
production defaults only.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from guanlan_v2.orchestration import worker as _worker
from guanlan_v2.orchestration.llm_output import DEEP_SEAT_RUNTIME_OWNED_FIELDS
from guanlan_v2.orchestration.adapters.launcher import LaneExecutionBinding
from guanlan_v2.orchestration.approval import admit_after_approval
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    build_plan_diff,
    render_plan_diff_md,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    NamedEvidenceDigest,
    PromptAssemblyRecord,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import OrchestrationRequest

from guanlan_v2.orchestration.pipeline import escalation as _escalation
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    REDUCED_DEEP_DECIDE_PRESET_ID,
    REDUCED_EVIDENCE_NOTE,
    RUN_SUBJECT_SCHEMA_REF,
    DeepDecideError,
    PresetSelectionRefused,
    materialize_deep_decide_draft,
    reduced_evidence_badges,
    render_reduced_evidence_banner,
    session_date_of,
)

__all__ = [
    "DEEP_GOAL",
    "DEEP_PRESET_ENV_VAR",
    "DEEP_PRESET_ENV_CHOICES",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED",
    "OUTCOME_NO_LEASE",
    "OUTCOME_REFUSED",
    "SUBJECT_ASSEMBLER_ID",
    "SUBJECT_ASSEMBLER_VERSION",
    "SUBJECT_TRUSTED_INPUT_NAME",
    "SubjectPromptAssembler",
    "ProductionStoresUnbound",
    "DeepDecideBindings",
    "make_orchestrated_decide",
    "resolve_deep_preset_id",
    "build_production_bindings",
    "build_production_decide_fn",
]

_LOG = logging.getLogger("guanlan.orchestration.live_decide")

#: the constant, deliberately CODE-FREE goal of every deep-decide request — the
#: subject enters exclusively through the committed ``RunSubject@1`` artifact.
DEEP_GOAL = "观澜 · 落子深度研判(run-scoped subject)"

#: the honest deep-outcome vocabulary carried on the returned dict (only-if-present).
OUTCOME_COMPLETED = "completed"
OUTCOME_NO_LEASE = "no_lease"
OUTCOME_FAILED = "failed"
OUTCOME_REFUSED = "refused"

#: the per-run assembler's registered identity (stamped on every prompt record).
SUBJECT_ASSEMBLER_ID = "pipeline.subject_prompt_assembler"
SUBJECT_ASSEMBLER_VERSION = "1"
#: the canonical trusted-input slot name citing the committed subject artifact.
SUBJECT_TRUSTED_INPUT_NAME = "run_subject"

#: the +08:00 session zone the subject's deterministic ``as_of`` anchors to.
_SESSION_TZ = timezone(timedelta(hours=8))

#: quote ``fresh`` strings that mean NOT fresh (the boolean-contract coercion —
#: a truthy string like ``"false"`` must never arm the band trigger).
_FALSY_FRESH_STRINGS = frozenset({"", "0", "false", "no", "none", "null"})

#: 路线 A — the ONE environment switch that selects the deep-decide generation.
#: UNSET (or blank / ``full``) keeps the reviewed TEN-node preset, whose honest
#: ``tool_calls_required_unmet`` refusal is the correct production behaviour and
#: is never quietly downgraded; ``reduced`` is the explicit human opt-in to the
#: eight-node reduced-evidence preset (every product badged, see deep_decide).
DEEP_PRESET_ENV_VAR = "GUANLAN_SEATS_DEEP_PRESET"
DEEP_PRESET_ENV_CHOICES: Mapping[str, str] = {
    "": DEEP_DECIDE_PRESET_ID,
    "full": DEEP_DECIDE_PRESET_ID,
    "reduced": REDUCED_DEEP_DECIDE_PRESET_ID,
}


class ProductionStoresUnbound(RuntimeError):
    """The process has no correctly bound orchestration durable store (typed refusal).

    Raised by :func:`build_production_bindings` when the R23/R24 startup binding
    is absent or unhealthy (``orch_store_status`` state != ``bound``). The deep
    lane NEVER self-binds: ``bind_process_durable_stores_and_scan`` runs exactly
    once per process from ``guanlan_v2.orchestration.startup`` and freezes the
    cell-namespace allowlist at construction (the server.py 全进程唯一绑定
    invariant), so a deep-lane rebind would either be a silent no-op against a
    broken binding or would freeze a one-namespace allowlist and starve every
    other durable-store consumer. The server catches this, logs loudly, and
    stays fast-only.
    """


# =========================================================================== #
# E2b — the per-run subject-closed prompt assembler                            #
# =========================================================================== #
class SubjectPromptAssembler:
    """The per-run Phase-10 :class:`~guanlan_v2.orchestration.worker.PromptAssembler`.

    Constructed once per deep run with the committed ``RunSubject@1`` artifact
    closed over; occupies the ``prompt_assembler`` injection seam of
    ``assembly.build_production_plan_runner``. It owns prompt construction: the
    canonical channel is the reviewed static-v1 shape plus one
    ``trusted_subject`` section rendered from OUR OWN reviewed subject fields
    (never caller/model text), and the trusted-input digests gain exactly one
    entry — ``run_subject`` → the committed artifact's content digest — so every
    persisted ``PromptAssemblyRecord`` cites the subject artifact it was
    assembled under. The private ``worker.py`` helpers are reused deliberately
    (the ``assembly.py`` ``_messages_from_canonical`` precedent): two assemblers
    must never drift on how text materials render or how request bytes digest.
    """

    assembler_id = SUBJECT_ASSEMBLER_ID
    assembler_version = SUBJECT_ASSEMBLER_VERSION

    def __init__(self, *, subject: RunSubject, subject_ref: TypedPayloadRef) -> None:
        if not isinstance(subject, RunSubject):
            raise DeepDecideError(
                f"SubjectPromptAssembler needs a RunSubject; got {type(subject).__name__}")
        if subject_ref.schema_ref != RUN_SUBJECT_SCHEMA_REF:
            raise DeepDecideError(
                f"subject_ref must name {RUN_SUBJECT_SCHEMA_REF.key}; got "
                f"{subject_ref.schema_ref.key}")
        self._subject = subject
        self._subject_ref = subject_ref

    def assemble(
        self,
        *,
        plan_digest: str,
        node_id: str,
        worker_id: str,
        system_prompt,
        skills,
        guardrails,
        trusted_input_digests: tuple,
        untrusted_blocks: tuple,
        output_binding=None,
        schema_registry=None,
        trusted_artifacts: tuple = (),
    ):
        if any(e.name == SUBJECT_TRUSTED_INPUT_NAME for e in trusted_input_digests):
            raise DeepDecideError(
                f"trusted input name {SUBJECT_TRUSTED_INPUT_NAME!r} is reserved for "
                "the committed run subject; refusing the collision rather than "
                "silently shadowing either digest")
        subject_digest = self._subject_ref.payload_ref.content_digest
        trusted = sorted(
            tuple(trusted_input_digests)
            + (NamedEvidenceDigest(name=SUBJECT_TRUSTED_INPUT_NAME,
                                   digest=subject_digest),),
            key=lambda e: e.name)

        system_text = _worker._text_of(system_prompt)
        skill_texts = [_worker._text_of(s) for s in skills]
        guardrail_texts = [_worker._text_of(g) for g in guardrails]
        blocks = tuple(untrusted_blocks)
        # the rendered trusted subject block: OUR OWN reviewed fields only,
        # citing the committed artifact digest — never caller or model text.
        session = self._subject.as_of.astimezone(_SESSION_TZ).date().isoformat()
        subject_block = {
            "artifact_schema": RUN_SUBJECT_SCHEMA_REF.key,
            "artifact_digest": subject_digest,
            "code": self._subject.code,
            "as_of": self._subject.as_of.isoformat(),
            "text": (
                f"本次深度研判的标的:{self._subject.code}(评估交易日 {session})。"
                f"标的身份由已提交的 {RUN_SUBJECT_SCHEMA_REF.key} 工件绑定"
                f"(digest {subject_digest})。"
            ),
        }
        channel = {
            "assembler_id": self.assembler_id,
            "assembler_version": self.assembler_version,
            "system": system_text,
            "skills": skill_texts,
            "guardrails": guardrail_texts,
            "trusted_subject": subject_block,
            "trusted_inputs": [{"name": e.name, "digest": e.digest} for e in trusted],
            "data_inputs": [
                {
                    "ordinal": b.ordinal,
                    "schema": b.payload_ref.schema_ref.key,
                    "namespace": b.payload_ref.payload_ref.namespace,
                    "content_digest": b.payload_ref.payload_ref.content_digest,
                    "media_type": b.media_type,
                    "length": b.rendered_length,
                }
                for b in blocks
            ],
        }
        # 裁决 (2026-07-31): the run's own committed upstream artifacts are
        # INLINED through the ONE shared renderer (run deep-73a70d27cb1651a0's
        # six-monologue defect — pm: "only digests … no actual data"). The
        # untrusted ``data_inputs`` references above are byte-for-byte
        # unchanged; absence arrives as an explicit stated block.
        upstream = _worker.trusted_upstream_channel_section(tuple(trusted_artifacts))
        if upstream is not None:
            channel[_worker.TRUSTED_UPSTREAM_SECTION] = upstream
        # 裁决 2 (2026-07-29): the derived output contract, from the SAME reviewed
        # helper the static assembler uses — the two assemblers must never drift
        # on what a model is told its answer is validated against.
        # 2026-07-31: ``as_of`` is declared runtime-supplied — the seat gateway
        # stamps it from THIS assembler's own ``trusted_subject`` echo, so
        # demanding it from the model (the live ``bull-r1`` refusal) is the
        # same contradiction 裁决 2 removed for Lane 0. Schemas without an
        # ``as_of`` field are byte-for-byte unchanged (the helper intersects).
        out_schema = _worker.output_schema_section(
            output_binding=output_binding, schema_registry=schema_registry,
            runtime_owned_fields=DEEP_SEAT_RUNTIME_OWNED_FIELDS)
        if out_schema is not None:
            channel[_worker.OUTPUT_SCHEMA_SECTION] = out_schema
        canonical_bytes = json.dumps(
            channel, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest = _worker._model_request_digest(canonical_bytes)
        record = PromptAssemblyRecord.build(
            plan_digest=plan_digest, node_id=node_id, worker_id=worker_id,
            assembler_id=self.assembler_id,
            assembler_version=self.assembler_version,
            system_prompt_ref=_worker._ref_of(system_prompt),
            skill_refs=tuple(_worker._ref_of(s) for s in skills),
            guardrail_refs=tuple(_worker._ref_of(g) for g in guardrails),
            trusted_input_digests=tuple(trusted), untrusted_blocks=blocks,
            model_request_digest=digest)
        return _worker.AssembledModelRequest(
            canonical_request_bytes=canonical_bytes, request_digest=digest,
            prompt_record=record)


# =========================================================================== #
# DeepDecideBindings                                                           #
# =========================================================================== #
@dataclass(frozen=True)
class DeepDecideBindings:
    """Everything :func:`make_orchestrated_decide` is allowed to reach.

    A plain frozen dataclass (never a ContractModel). The three ``*_factory``
    shaped fields keep the plan's names but are per-run factories — see the
    module docstring's ABI-forced corrections. The optional ports default to
    ``None`` = "this port did not answer": the escalation judge then reports the
    dependent trigger inert-with-badge (ruling R-C), never a guessed value.
    """

    #: the Phase-9 durable ``RuntimeStores`` (in-memory stores in tests — same ABI).
    stores: Any
    #: the sealed Phase-10 preset registry holding the deep-decide v2 record.
    preset_registry: Any
    #: the sealed ``WorkerCatalogSnapshot`` the deep draft binds.
    catalog: Any
    #: the sealed cumulative schema registry the deep draft binds.
    schema_registry: Any
    #: per-run coordinator factory ``(*, admission, approvals_sink)`` over the ONE
    #: durable lease journal.
    coordinator: Callable[..., Any]
    #: per-run admission factory
    #: ``(*, run_id, request, draft, context, approvals, run_budget)``.
    admission: Callable[..., Any]
    #: the authoritative clock port (aware datetimes; naive refuses → fast fallback).
    clock: Any
    #: per-run plan-runner factory ``(*, admission, lane_bindings,
    #: run_context_factory, request_id, prompt_assembler)`` — production binds
    #: ``assembly.build_production_plan_runner``, the ONLY execution path.
    plan_runner: Callable[..., Any]
    #: the seats decision-append helper (``seats.api._persist_decision`` shape).
    persist_decision: Callable[[str, dict], None]
    #: the watcher budget-pool hook (``seats.watcher.note_external_llm_use``).
    note_llm_use: Callable[[int], None]
    #: ``() -> (ContextSnapshot, PayloadRef) | None`` — the latest committed
    #: Lane-0 snapshot (no upstream accessor exists; this port scans the stores).
    #: ``None`` ⇒ honest deep refusal → fast fallback.
    latest_snapshot_fn: Callable[[], Any]
    #: optional escalation ports (absent ⇒ the matching trigger stays inert).
    news_titles_fn: Callable[[str], Any] | None = None
    #: ``() -> {code: entry_price}`` — in-process READ-ONLY seats-ledger state
    #: replay; preferred over the tail row's own ``hold_entry`` for the bands.
    position_fn: Callable[[], Mapping[str, Any]] | None = None
    #: ``(code) -> quote dict`` (the wrapper coerces ``fresh`` to a boolean).
    quote_fn: Callable[[str], Any] | None = None
    #: ``(strategy_id) -> strat dict | None`` (the 落子 strat file).
    strat_fn: Callable[[str], Any] | None = None
    #: ``(code) -> decision rows`` — the seats decisions tail, READ BEFORE fast.
    decisions_tail_fn: Callable[[str], Sequence[Mapping[str, Any]]] | None = None
    #: 路线 A — WHICH sealed deep generation this lane runs. The default is the
    #: reviewed TEN-node preset, so a binding that says nothing keeps meaning
    #: exactly what it meant before route A existed; production sets it from
    #: :func:`resolve_deep_preset_id`.
    preset_id: str = DEEP_DECIDE_PRESET_ID


# =========================================================================== #
# 路线 A — the preset selection seam (pure, env-driven, explicit opt-in)         #
# =========================================================================== #
def resolve_deep_preset_id(env: Mapping[str, str] | None = None) -> str:
    """The deep-decide preset id this process should run (default: the full one).

    ``env`` defaults to ``os.environ``. An unrecognized spelling is a typed
    :class:`~guanlan_v2.orchestration.pipeline.deep_decide.PresetSelectionRefused`
    rather than a silent fallback: choosing an evidence scope by accident is
    exactly the failure this whole route is trying not to commit. The server
    catches the refusal, logs it loudly and stays fast-only.
    """
    raw = (env if env is not None else os.environ).get(DEEP_PRESET_ENV_VAR, "")
    key = str(raw or "").strip().lower()
    try:
        return DEEP_PRESET_ENV_CHOICES[key]
    except KeyError:
        raise PresetSelectionRefused(
            f"{DEEP_PRESET_ENV_VAR}={raw!r} is not a recognized deep-decide "
            "preset selection; accepted: "
            + ", ".join(repr(k) for k in sorted(DEEP_PRESET_ENV_CHOICES) if k)
            + " (unset = the reviewed ten-node preset). Refusing rather than "
              "guessing which evidence scope you meant.") from None


# =========================================================================== #
# small pure helpers                                                           #
# =========================================================================== #
def _canonical_code(code: Any) -> str | None:
    """The Phase-3 canonical six-digit code, or ``None`` for an unnameable one."""
    try:
        return normalize_symbol(str(code or "")).code
    except (TypeError, ValueError):
        return None


def _coerce_quote(quote: Any) -> Mapping[str, Any] | None:
    """A quote whose ``fresh`` is a real boolean (Task 5 fresh-gate contract).

    The judge's gate treats any truthy value as fresh, so a string ``"false"``
    from an uncontracted collector would silently arm the band trigger — the
    wrapper therefore coerces string spellings conservatively and everything
    else through ``bool``.
    """
    if not isinstance(quote, Mapping):
        return None
    if "fresh" not in quote:
        return dict(quote)
    raw = quote.get("fresh")
    if isinstance(raw, str):
        fresh = raw.strip().lower() not in _FALSY_FRESH_STRINGS
    else:
        fresh = bool(raw)
    out = dict(quote)
    out["fresh"] = fresh
    return out


def _position_entry(bindings: DeepDecideBindings, canonical: str) -> float | None:
    """The held entry price for ``canonical`` from the position port, or ``None``.

    A raising/nonsense port is an ABSENCE (logged), never a guess; ledger keys
    are matched through the same Phase-3 grammar the escalation builder uses.
    """
    if bindings.position_fn is None:
        return None
    try:
        book = bindings.position_fn() or {}
    except Exception as exc:  # noqa: BLE001 — port failure = absence, never a guess
        _LOG.warning("position port failed (bands stay inert, no guess): %s", exc)
        return None
    if not isinstance(book, Mapping):
        return None
    for key, value in book.items():
        if _canonical_code(key) != canonical:
            continue
        try:
            entry = float(value)
        except (TypeError, ValueError):
            return None
        return entry if entry > 0 else None
    return None


def _overlay_position(
    tail: list, canonical: str, entry: float | None
) -> list:
    """Prefer the ledger entry over the tail row's own ``hold_entry`` (Task 5 ruling).

    The bands read ``hold_entry`` off the LATEST matching decide row only
    (escalation ``_band_prices``), so the overlay targets exactly the row the
    builder will pick — found through the builder's own ``_latest_decision_row``
    so the two matching rules can never drift. No matching row ⇒ a minimal
    anchor row carrying ONLY the ledger fact is appended (it has no direction,
    so ``prev_direction`` stays exactly as inert as it already was).
    """
    if entry is None:
        return tail
    latest = _escalation._latest_decision_row(tail, canonical)
    if latest is None:
        return tail + [{"kind": "decide", "code": canonical, "hold_entry": entry}]
    return [
        ({**dict(row), "hold_entry": entry} if row is latest else row)
        for row in tail
    ]


def _derive_run_id(canonical: str, session_date: str, report,
                   preset_id: str = DEEP_DECIDE_PRESET_ID) -> str:
    """The deterministic kernel run id = the escalation identity (invariant 1).

    One deep run per ``(code, +08:00 session, fired trigger-kind set, preset)``:
    a re-tick of the same identity replays instead of re-running; a genuinely
    NEW trigger kind the same day earns its own run. ``preset_id`` is part of the
    identity (route A): two generations of the graph produce genuinely different
    verdicts under different evidence, so a reduced run must never be replayed as
    the receipt of a full one (or the reverse).
    """
    kinds = sorted({t.kind for t in report.triggers_hit})
    return "deep-" + content_digest(
        {"code": canonical, "session": session_date, "triggers": kinds,
         "preset": preset_id})[:16]


_SUBJECT_REGISTRY: SchemaRegistry | None = None


def _subject_registry() -> SchemaRegistry:
    """A minimal sealed side registry that validates the ``RunSubject@1`` commit.

    The cumulative chain registry does not hold ``RunSubject@1`` yet (its
    registration is the Phase-10 chain task's); committing the subject artifact
    still goes through the registry-validated payload store — under this
    explicit side registry — rather than around it.
    """
    global _SUBJECT_REGISTRY
    if _SUBJECT_REGISTRY is None:
        registry = SchemaRegistry()
        registry.register(RunSubject)
        registry.seal()
        _SUBJECT_REGISTRY = registry
    return _SUBJECT_REGISTRY


def _commit_run_subject(stores: Any, subject: RunSubject, run_id: str) -> TypedPayloadRef:
    """Commit the run-scoped subject artifact and return its typed ref."""
    digest = stores.resolver.register(_subject_registry())
    ref = stores.payloads.put(
        RUN_SUBJECT_SCHEMA_REF, subject, registry_digest=digest,
        namespace="main", idempotency_key=f"subject:{run_id}")
    return TypedPayloadRef(schema_ref=RUN_SUBJECT_SCHEMA_REF, payload_ref=ref)


def _settled_llm(stores: Any, run_id: str, run_budget: RunBudget,
                 plan_reservation_id: str) -> int:
    """The run's settled LLM invocations, folded from the budget event journal."""
    state = BudgetLedger(
        sink=stores.budget_event_sink(run_id=run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget).replay()
    return sum(
        r.actual_llm_invocations for r in state.reservations.values()
        if state.parent_of.get(r.reservation_id) == plan_reservation_id
        and r.status == "settled")


def _build_pending_card(*, draft, request, candidate_plan_digest, diff,
                        preset_record_digest, run_id, requested_at,
                        preset_id: str = DEEP_DECIDE_PRESET_ID) -> PendingPlanApproval:
    """The reviewer/lease card for the deep candidate (preset provenance exact).

    ``source=PRESET_FALLBACK`` is the sealed card vocabulary's only
    preset-provenance-bearing value — see the module docstring's reconciliation.

    Route A: a reduced-evidence candidate's ``rendered_md`` OPENS with the
    missing-evidence banner. ``rendered_from_diff_digest`` still pins the diff
    the card was rendered from — the banner is an addition ABOVE the diff, never
    an edit of it.
    """
    diff_digest = diff.semantic_digest()
    diff_ref = TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(
            namespace="main", object_id=f"diff-{candidate_plan_digest[:12]}",
            content_digest=diff_digest))
    return PendingPlanApproval(
        request_id=request.request_id,
        candidate_plan_digest=candidate_plan_digest,
        goal=request.goal,
        source=PlanSource.PRESET_FALLBACK,
        approval_policy=draft.approval_policy,
        node_count=len(draft.nodes),
        worker_ids=tuple(sorted({n.worker_id for n in draft.nodes})),
        budget_request_tokens=draft.budget_request_tokens,
        budget_request_llm_invocations=draft.budget_request_llm_invocations,
        plan_diff_ref=diff_ref,
        rendered_md=(render_reduced_evidence_banner(preset_id)
                     + render_plan_diff_md(diff)),
        rendered_from_diff_digest=diff_digest,
        planner_rationale=None,
        candidate_id=f"cand-{run_id}",
        requested_at=requested_at,
        preset_id=preset_id,
        preset_record_digest=preset_record_digest)


def _only_present(record: dict) -> dict:
    """The seats only-if-present key convention: ``None`` / ``""`` never land."""
    return {k: v for k, v in record.items() if v not in (None, "")}


def _tranche_rows(proposal) -> list[dict]:
    """Every tranche trigger price range on the proposal, flattened (advisory)."""
    rows: list[dict] = []
    for position in proposal.positions:
        for tranche in position.entry_tranches:
            if tranche.price_low is None and tranche.price_high is None:
                continue  # no price range ⇒ nothing a conditional order could state
            rows.append(_only_present({
                "symbol": position.symbol.dotted,
                "price_low": tranche.price_low,
                "price_high": tranche.price_high,
                "fraction": tranche.fraction,
            }))
    return rows


class _DecisionPoint:
    """The single decision point of one live deep run (the runner's point seam)."""

    point_ordinal = 1


def _extract_proposal(artifact: Any, schema_registry: Any):
    """The committed ``PortfolioTargetProposal@1`` sink artifact, validated."""
    expected = SchemaRef(name="PortfolioTargetProposal", version="1")
    schema_ref = getattr(artifact, "payload_schema_ref", None)
    if schema_ref != expected:
        raise DeepDecideError(
            f"the deep run's sink artifact is {getattr(schema_ref, 'key', schema_ref)!r}, "
            f"not {expected.key}; refusing to read a proposal out of it")
    return schema_registry.validate_payload(schema_ref, artifact.payload)


# =========================================================================== #
# the wrapper                                                                  #
# =========================================================================== #
def make_orchestrated_decide(
    *, fast_decide: Callable[[dict], dict], bindings: DeepDecideBindings,
) -> Callable[[dict], dict]:
    """The watcher ``decide_fn``: fast always; deep only lease-admitted (module docstring)."""

    def _decide(payload: dict) -> dict:
        payload = payload if isinstance(payload, Mapping) else {}
        code_raw = str(payload.get("code") or "")
        # ── the tail-before-fast trap: snapshot the tail FIRST ─────────────── #
        tail_fn = bindings.decisions_tail_fn or _decisions_tail_rows_production
        try:
            tail = [dict(r) for r in (tail_fn(code_raw) or ()) if isinstance(r, Mapping)]
        except Exception as exc:  # noqa: BLE001 — an unreadable tail is an absence
            _LOG.warning("decisions tail port failed for %s: %s", code_raw, exc)
            tail = []

        fast = fast_decide(payload)
        if not isinstance(fast, dict) or fast.get("ok") is False:
            return fast  # a broken fast path is never rescued by the deep lane

        try:
            return _after_fast(payload, fast, tail, bindings)
        except Exception as exc:  # noqa: BLE001 — invariant 2: never raise into the tick
            _LOG.warning("deep decide failed for %s (fast result stands): %s",
                         code_raw, exc, exc_info=True)
            return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_FAILED}

    return _decide


def _after_fast(payload: Mapping[str, Any], fast: dict, tail: list,
                bindings: DeepDecideBindings) -> dict:
    canonical = _canonical_code(fast.get("code") or payload.get("code"))
    if canonical is None:
        # a subject the Phase-3 grammar cannot name has no deep lane at all.
        _LOG.debug("deep lane skipped: unnameable code %r", payload.get("code"))
        return fast

    now = bindings.clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        # mirror the materializer's ClockRefused BEFORE any session arithmetic:
        # a naive clock would silently shift the +08:00 session date.
        _LOG.warning("deep lane refused: naive clock %r (fast result stands)", now)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_REFUSED}

    # ── escalation context: every port answered or honestly absent ─────────── #
    quote = strat = None
    if bindings.quote_fn is not None:
        try:
            quote = _coerce_quote(bindings.quote_fn(canonical))
        except Exception as exc:  # noqa: BLE001 — absence, never a guess
            _LOG.warning("quote port failed for %s: %s", canonical, exc)
    strategy_id = str(payload.get("strategy_id") or "")
    if bindings.strat_fn is not None and strategy_id:
        try:
            raw = bindings.strat_fn(strategy_id)
            strat = raw if isinstance(raw, Mapping) else None
        except Exception as exc:  # noqa: BLE001 — absence, never a guess
            _LOG.warning("strat port failed for %s: %s", strategy_id, exc)
    entry = _position_entry(bindings, canonical)
    judged_tail = _overlay_position(tail, canonical, entry)

    ctx = _escalation.build_escalation_context(
        code=canonical,
        as_of=now,
        fast_result=fast,
        decisions_tail=judged_tail,
        quote=quote,
        strat=strat,
        news_titles_fn=bindings.news_titles_fn,
    )
    report = _escalation.judge_escalation(ctx)
    if not report.escalate:
        return fast

    session_date = session_date_of(now)
    run_id = _derive_run_id(canonical, session_date, report, bindings.preset_id)

    # ── idempotent re-tick: the prior orchestrated row IS the receipt ──────── #
    for row in tail:
        if row.get("source") == "orchestrated" and row.get("run_id") == run_id:
            return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_COMPLETED,
                    "deep_replayed": True, "run_id": run_id}

    # ── resolve snapshot + commit the subject + materialize ────────────────── #
    snapshot_pair = bindings.latest_snapshot_fn()
    if not snapshot_pair:
        _LOG.warning("deep lane refused for %s: no committed ContextSnapshot "
                     "(fast result stands)", canonical)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_REFUSED,
                "run_id": run_id}
    context, context_snapshot_ref = snapshot_pair

    session_dt = datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=_SESSION_TZ)
    subject = RunSubject(code=canonical, as_of=session_dt)
    subject_ref = _commit_run_subject(bindings.stores, subject, run_id)

    request = OrchestrationRequest(
        request_id=f"req-{run_id}", goal=DEEP_GOAL, workflow="orchestrate_only",
        fallback_preset_id=None, approval_policy=ApprovalPolicy.REQUIRED)
    try:
        materialized = materialize_deep_decide_draft(
            request=request,
            preset_registry=bindings.preset_registry,
            preset_id=bindings.preset_id,
            context_snapshot_ref=context_snapshot_ref,
            subject_ref=subject_ref,
            clock=bindings.clock,
            context=context,
            catalog=bindings.catalog,
            schema_registry=bindings.schema_registry,
            draft_id=f"plan-{run_id}",
            run_id=run_id,
        )
    except DeepDecideError as exc:
        _LOG.warning("deep materialization refused for %s: %s (fast result stands)",
                     canonical, exc)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_REFUSED,
                "run_id": run_id}
    draft = materialized.draft

    # ── Phase 1 validate + Phase 2 reserve (the real admission service) ────── #
    approvals: dict = {}
    run_budget = RunBudget(
        ledger_id=f"led-{run_id}",
        max_tokens=draft.budget_request_tokens,
        max_llm_invocations=draft.budget_request_llm_invocations,
        max_concurrency=draft.max_concurrency)
    service = bindings.admission(
        run_id=run_id, request=request, draft=draft, context=context,
        approvals=approvals, run_budget=run_budget)
    preparation = service.prepare_candidate(draft.id, request_id=request.request_id)
    if not preparation.support_report.supported:
        # grant-gap ruling B: the admission-time support refusal is the deep
        # lane's most-traveled honest exit — REFUSED with the run_id carried,
        # like every other post-run_id refusal arm, never the blanket failed
        # arm with a spurious traceback.
        _LOG.warning(
            "deep draft for %s is not runtime-supported: %s (fast result stands)",
            canonical, "; ".join(i.code for i in preparation.support_report.issues))
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_REFUSED,
                "run_id": run_id}
    candidate, reservation = service.persist_and_reserve_candidate(
        preparation, idempotency_key=f"reserve-{run_id}")
    digest = candidate.candidate_plan_digest

    # ── the lease channel: admitted under a standing human envelope, or a
    #    pending card for a human — no third path, never a self-approval ─────── #
    diff = build_plan_diff(draft, request=request, candidate_plan_digest=digest,
                           baseline=None, baseline_kind="none")
    pending = _build_pending_card(
        draft=draft, request=request, candidate_plan_digest=digest, diff=diff,
        preset_record_digest=bindings.preset_registry.get(
            bindings.preset_id).semantic_digest(),
        run_id=run_id, requested_at=now, preset_id=bindings.preset_id)

    def _approvals_sink(approval):
        approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    coordinator = bindings.coordinator(
        admission=service, approvals_sink=_approvals_sink)
    outcome = coordinator.register_and_try_lease(
        pending, idempotency_key=f"lease-{run_id}", now=now,
        candidate_catalog_digest=draft.catalog_digest,
        candidate_registry_digest=draft.schema_registry_digest)
    if outcome.outcome != "lease_admitted":
        _LOG.info("deep run for %s stays pending_human (card registered; "
                  "fast result stands)", canonical)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_NO_LEASE,
                "run_id": run_id}

    plan, _admitted = admit_after_approval(
        admission=service, candidate_id=digest,
        reservation_id=reservation.reservation_id,
        approval_event_id=outcome.event.event_id,
        idempotency_key=f"freeze-{run_id}")

    # ── execute through the ONE production runner, subject-closed (E2b) ────── #
    assembler = SubjectPromptAssembler(subject=subject, subject_ref=subject_ref)
    lane_binding = LaneExecutionBinding(
        lane="main", candidate_plan_digest=digest,
        reservation_id=reservation.reservation_id,
        approval_event_id=outcome.event.event_id)

    def _run_context_factory(*, lane, point, plan, data_context, memory_binding):
        return RunContext(
            run_id=plan.run_id, data=data_context,
            context_snapshot_id=context.snapshot_id,
            memory_snapshot_hash=context.memory_snapshot_hash,
            budget=run_budget, cancellation_token_id=f"cancel-{run_id}")

    runner = bindings.plan_runner(
        admission=service, lane_bindings={"main": lane_binding},
        run_context_factory=_run_context_factory, request_id=request.request_id,
        prompt_assembler=assembler)

    # invariant 3, closed BOTH sides (review I-1): once the runner has been
    # invoked the settled spend is real money against the watcher's 24/day pool
    # — it must reach note_llm_use whether the run failed, the run completed, or
    # the run completed and a LANDING step (extract/persist) then blew up. One
    # closure, one flag, exactly one note per run.
    note_state = {"done": False, "settled": 0}

    def _note_settled_once() -> int:
        if note_state["done"]:
            return note_state["settled"]
        note_state["done"] = True
        settled = 0
        try:
            settled = _settled_llm(bindings.stores, run_id, run_budget,
                                   reservation.reservation_id)
        except Exception:  # noqa: BLE001 — the fold itself failing loses no honesty
            _LOG.warning("settled-spend fold failed for %s", run_id, exc_info=True)
        note_state["settled"] = settled
        if settled > 0:
            bindings.note_llm_use(settled)
        return settled

    try:
        artifact = runner(
            lane="main", point=_DecisionPoint(), approval=None,
            data_context=context.data_context, memory_binding=None,
            candidate_plan_digest=digest)
    except Exception as exc:  # noqa: BLE001 — invariant 3: failed runs settle honestly
        settled = _note_settled_once()
        _LOG.warning("deep run %s failed after settling %s LLM invocation(s): %s "
                     "(fast result stands)", run_id, settled, exc)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_FAILED,
                "run_id": run_id}

    # ── the two-row model: ONE orchestrated row (+ ONE advisory order rec) ─── #
    try:
        proposal = _extract_proposal(artifact, bindings.schema_registry)
        tranches = _tranche_rows(proposal)
        # 路线 A honesty red line: a reduced-evidence verdict must be readable AS
        # reduced everywhere it lands — the ledger row, the advisory order record
        # and (above) the reviewer card all carry the same named badge + note.
        scope_badges = reduced_evidence_badges(bindings.preset_id)
        scope_note = REDUCED_EVIDENCE_NOTE if scope_badges else None
        record = _only_present({
            "code": canonical,
            "name": fast.get("name") or payload.get("name"),
            "strategy_id": payload.get("strategy_id"),
            "strategy_name": payload.get("strategy_name"),
            "mode": "deep",
            "freq": payload.get("freq"),
            "rationale": proposal.rationale,
            "confidence": proposal.confidence.value,
            "target_weights": [
                _only_present({
                    "symbol": p.symbol.dotted,
                    "weight": p.target_weight,
                    "stop_loss_pct": p.stop_loss_pct,
                    "take_profit_pct": p.take_profit_pct,
                })
                for p in proposal.positions
            ],
            "cash_weight": proposal.cash_weight,
            "trigger_ranges": tranches or None,
            "triggers": [t.kind for t in report.triggers_hit],
            "inert_ports": list(report.inert_ports) or None,
            "asof": str(session_date),
            "run_id": run_id,
            "source": "orchestrated",
            "preset_id": bindings.preset_id,
            "badges": list(materialized.badges) or None,
            "evidence_note": scope_note,
            "escalation_digest": report.semantic_digest(),
            "escalated_from_asof": fast.get("asof"),
        })
        bindings.persist_decision("decide", record)
        if tranches:
            bindings.persist_decision("order", _only_present({
                "code": canonical,
                "name": fast.get("name") or payload.get("name"),
                "strategy_id": payload.get("strategy_id"),
                "strategy_name": payload.get("strategy_name"),
                "tranches": tranches,
                "stop": proposal.positions[0].stop_loss_pct if proposal.positions else None,
                "take": proposal.positions[0].take_profit_pct if proposal.positions else None,
                "logic": "深研判 trader 提案的分批触发价区间(advisory;人工执行,观澜绝不下单)",
                "asof": str(session_date),
                "run_id": run_id,
                "source": "orchestrated",
                "preset_id": bindings.preset_id,
                "badges": list(scope_badges) or None,
                "evidence_note": scope_note,
            }))
    except Exception as exc:  # noqa: BLE001 — the run happened; the landing failed
        _LOG.warning("deep run %s completed but its outcome could not be landed: %s "
                     "(fast result stands)", run_id, exc, exc_info=True)
        return {**fast, "deep_attempted": True, "deep_outcome": OUTCOME_FAILED,
                "run_id": run_id}
    finally:
        _note_settled_once()
    return {**record, "ok": True, "deep_attempted": True,
            "deep_outcome": OUTCOME_COMPLETED}


# =========================================================================== #
# production defaults (lazy seats/engine reads — none at import time)          #
# =========================================================================== #
def _decisions_tail_rows_production(code: str, max_lines: int = 4000) -> list[dict]:
    """The seats decisions tail rows for ``code`` (read-only, honest empty)."""
    from guanlan_v2.seats import watcher as _watcher

    want = _watcher._code6(code)
    try:
        if not _watcher.DECISIONS_LOG.exists():
            return []
        lines = _watcher.DECISIONS_LOG.read_text(
            encoding="utf-8").splitlines()[-max_lines:]
    except Exception:  # noqa: BLE001 — unreadable log = empty tail (badged inert)
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — a bad line is skipped, never repaired
            continue
        if isinstance(row, dict) and _watcher._code6(str(row.get("code") or "")) == want:
            rows.append(row)
    return rows


def _quote_production(code: str) -> dict:
    """The watcher's own fresh-gated quote collector (boolean ``fresh``)."""
    from guanlan_v2.seats import watcher as _watcher

    return _watcher._quote_production(code)


def _strat_production(strategy_id: str) -> Mapping[str, Any] | None:
    """The 落子 strat file for ``strategy_id`` (id field first, stem fallback)."""
    from guanlan_v2.seats import watcher as _watcher

    try:
        paths = sorted(_watcher.ARCHIVE_DIR.glob("strat_*.json"))
    except Exception:  # noqa: BLE001
        return None
    for path in paths:
        try:
            strat = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a bad file is not this strategy
            continue
        if isinstance(strat, dict) and (
                str(strat.get("id") or "") == strategy_id or path.stem == strategy_id):
            return strat
    return None


def _position_production() -> Mapping[str, float]:
    """READ-ONLY seats-ledger state replay → ``{code: entry_price}`` (Task 5 ruling)."""
    from guanlan_v2.seats import api as _seats_api

    state = _seats_api._ledger_replay(_seats_api._ledger_events())
    if not state.get("opened"):
        return {}
    out: dict[str, float] = {}
    for code, position in (state.get("positions") or {}).items():
        try:
            qty = int(position.get("qty") or 0)
            cost = float(position.get("avg_cost") or 0)
        except (TypeError, ValueError):
            continue
        if qty > 0 and cost > 0:
            out[str(code)] = cost
    return out


def _latest_snapshot_production(stores: Any):
    """Best-effort latest committed ``ContextSnapshot@1`` scan over the stores.

    No reviewed accessor exists upstream (Task 6 finding) — until one does, this
    reads the payload backend directly and returns ``None`` on ANY doubt, which
    the wrapper turns into the honest ``refused`` → fast fallback. A reviewed
    accessor (Task 11/12) should replace this via the ``latest_snapshot_fn`` port.
    """
    try:
        backend = stores._shared.backend  # noqa: SLF001 — no public listing exists
        best = None
        best_ref = None
        for object_id, stored in dict(backend.payloads).items():
            if getattr(stored, "schema_key", None) != "ContextSnapshot@1":
                continue
            model = stored.model
            stamp = getattr(model, "built_at", None)
            if best is None or (stamp is not None and stamp > best[0]):
                best = (stamp, model)
                best_ref = PayloadRef(
                    namespace=stored.namespace, object_id=object_id,
                    content_digest=stored.content_digest)
        if best is None:
            return None
        return best[1], best_ref
    except Exception as exc:  # noqa: BLE001 — any doubt is an honest refusal
        _LOG.warning("latest-snapshot scan failed (deep lane will refuse): %s", exc)
        return None


def build_production_bindings(*, preset_id: str | None = None) -> DeepDecideBindings:
    """Assemble the production :class:`DeepDecideBindings` (durable stores, sealed chain).

    HONEST STATE (Task 11 update): the ONE remaining gate before assembly is
    the process durable-store binding, which must already be ``bound`` (the
    R23/R24 startup binding; :class:`ProductionStoresUnbound` otherwise — the
    deep lane never self-binds, review I-3). The catalog runtime now builds for
    real (``build_production_catalog_runtime`` over the promoted nine-loader
    material universe; Task-0b concern 2 closed) with the full three-analyzer
    bridge view, and ``check_runtime_support`` COMPLETES for the deep preset's
    workers (the Task-11 rowless-reader discriminations). HONEST RESIDUE: the
    sealed Phase-3 data-prefetch grants still cover ``dec.pm`` only, so a deep
    run is SUPPORT-REFUSED at admission (``tool_calls_required_unmet`` naming
    ``pv.technical`` / ``text.news`` — the permanent-honest grant-gap ruling);
    the wrapper degrades to the fast chain with an honest ``deep_outcome``.
    Nothing is faked.

    路线 A: ``preset_id`` defaults to :func:`resolve_deep_preset_id`, i.e. the
    ten-node preset above unless ``GUANLAN_SEATS_DEEP_PRESET=reduced`` says
    otherwise. The selection is resolved FIRST — before the store binding is even
    consulted — so an unrecognized spelling refuses on its own terms instead of
    hiding behind an unrelated error.
    """
    preset_id = preset_id or resolve_deep_preset_id()
    from guanlan_v2.orchestration.adapters.chain import (
        build_phase9_registry,
        phase9_catalog_snapshot,
    )
    from guanlan_v2 import orch_store_status as _orch_status
    from guanlan_v2.orchestration.adapters.durable import process_durable_stores
    from guanlan_v2.orchestration.adapters.api import build_plan_approval_coordinator
    from guanlan_v2.orchestration.admission import PlanAdmissionService
    from guanlan_v2.orchestration.runtime_clock import SystemClock
    from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
    from guanlan_v2.orchestration.adapters import chain as _chain
    from guanlan_v2.orchestration.pipeline.assembly import (
        PRODUCTION_PRESETS_DIR,
        build_production_catalog_runtime,
        build_production_plan_runner,
        load_phase10_preset_registry,
        production_bridge_analyzers,
        production_bridge_view,
        production_gateway_factory,
    )
    from guanlan_v2.seats import api as _seats_api
    from guanlan_v2.seats import watcher as _watcher

    clock = SystemClock()
    # review I-3: consult the R23/R24 status record and REFUSE — never self-bind.
    # A deep-lane bind here would violate the server's 全进程唯一绑定 invariant
    # (the allowlist freezes at construction) and would side-effect the process
    # store binding before today's catalog-runtime refusal fires.
    stores = process_durable_stores()
    if stores is None or not _orch_status.orchestration_store_bound():
        raise ProductionStoresUnbound(
            "the process orchestration durable-store binding is "
            f"{_orch_status.orchestration_store_state()!r} (only 'bound' is "
            "healthy); the deep lane never self-binds — fix the startup binding "
            "(guanlan_v2.orchestration.startup / GET /orchestration/store_status) "
            "and restart. Deep lane stays dead; fast path unaffected.")
    registry = build_phase9_registry(_chain.PHASE9_BASE_REGISTRY_DIGEST)
    stores.resolver.register(registry)
    rt_digest = registry.registry_digest
    snapshot = phase9_catalog_snapshot()
    bundle = build_production_catalog_runtime(snapshot)
    # Task 11: the full three-analyzer view (assembly.production_bridge_view).
    # The sealed catalog carries three bridge descriptors, and BridgeCatalogView
    # REFUSES a bridge without a reviewed analyzer binding — an empty analyzer
    # map (the pre-Task-11 placeholder) would crash HERE now that the material
    # universe resolves, instead of reaching the ruled honest support-refusal.
    #
    # 2026-07-31 live-store defect: the map is built ONCE and closed over by
    # BOTH halves — the admission view here and the runner's bridge view in
    # plan_runner_factory. The runner half previously received NO analyzers and
    # refused at construction AFTER register_and_try_lease had consumed the
    # ApprovalLease (a burned human authorization with zero execution). One
    # recipe (assembly.production_bridge_analyzers), one map, zero forks; and
    # because THIS line already builds a view over it, the whole class of
    # analyzer-binding refusal now fires at binding construction (server
    # startup, deep lane stays down honestly) — never after a lease draw.
    bridge_analyzers = production_bridge_analyzers()
    view = production_bridge_view(bundle.runtime, bridge_analyzers)
    # 2026-07-31 controller ruling (the deep decision trunk): the sealed
    # catalog's experience.bridge activates for dec.research_mgr (the
    # `experience_cases` read category), and ExecutionBridgeResolver requires a
    # trusted provider factory for EVERY active bridge — unbound, every deep
    # run died at bridge prepare AFTER its lease draw (bridge_preparation_failed,
    # run deep-8eb9afef6e9a5e48). Bound HERE, once per bundle, through the SAME
    # reviewed recipe the Lane-0 driver uses
    # (bootstrap.register_lane0_experience_factories — one recipe, no fork).
    # The values are the deep lane's honest experience facts: no committed
    # Lane-0 pool, no reviewed view source, no fitted scaler. All of them are
    # GRANTED-branch-only, and no worker this lane can admit holds the
    # experience capability (the sealed v2 presets' workers only — the two
    # granted Lane-0 readers are not among them), so under the ruled provider
    # discrimination dec.research_mgr freezes with an honest EMPTY experience
    # contribution and the granted branch is structurally unreachable;
    # reaching it anyway fails LOUDLY (bridge_execution_error), never
    # fabricates a retrieval.
    from guanlan_v2.orchestration import bootstrap as _bootstrap

    _bootstrap.register_lane0_experience_factories(
        factories=bundle.factories, catalog=_bootstrap.load_lane0_catalog(),
        pool=None, registry=registry, experience_views=(),
        experience_scaler=None, as_of=clock.now())
    # 2026-07-31 controller ruling (pm's two bridges — run deep-a06fd33840c0b3ee:
    # research-mgr completed, then dec.pm died at bridge prepare naming
    # data.runtime, trader blocked behind it). dec.pm activates data.runtime
    # (cap.data.verified_snapshot, priority 100) AND memory.runtime (the
    # 'memory' read category, priority 200); both providers are bound HERE, on
    # the SAME bundle.factories the per-run plan_runner_factory →
    # build_production_plan_runner._plan_executor → ExecutionRuntime executes
    # over. Memory: the one reviewed recipe over the sealed catalog materials —
    # the production prefetch binding has ZERO rows, so dec.pm takes the C3
    # rowless branch (honest empty prefetch, stores untouched). Data: the
    # worldless StructurallyDeadRowDataProvider — honest-empty ONLY for the
    # structurally-dead dec.pm verified_snapshot row (defect H: node_param
    # bindings on a params_schema_ref=None worker — unrunnable in ANY legal
    # plan); every other shape stays LOUD, so the chartered L2-b gap (no
    # production DataRuntimeWorld) keeps firing for the two pv aux nodes and
    # for any future resolvable row. Pinned in test_pm_two_bridges.py; the
    # tombstone (L1 ruling D-0 + L2-b supersede this at the re-freeze phase)
    # lives on the provider class.
    from guanlan_v2.orchestration.data.runtime import (
        register_structurally_dead_row_data_provider,
    )
    from guanlan_v2.orchestration.memory.runtime import (
        register_phase3_memory_provider_factory,
    )

    register_phase3_memory_provider_factory(
        factories=bundle.factories, stores=stores)
    register_structurally_dead_row_data_provider(factories=bundle.factories)
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)

    def admission_factory(*, run_id, request, draft, context, approvals, run_budget):
        return PlanAdmissionService(
            run_id=run_id, requests={request.request_id: request},
            drafts={draft.id: draft}, contexts={context.content_digest: context},
            attestations={}, approvals=approvals, catalog=bundle.runtime,
            bridge_view=view, phase1_registry=registry,
            runtime_registry_digest=rt_digest, profile=STATIC_RUNTIME_PROFILE_V2,
            stores=stores, run_budget=run_budget, clock=clock)

    def coordinator_factory(*, admission, approvals_sink):
        # the reviewed durable builder (replay path) over the ONE production
        # journal; verifier-free — this wrapper never records a HUMAN decision,
        # and register_and_try_lease/register_pending need no verifier.
        return build_plan_approval_coordinator(
            admission=admission, clock=clock, verifier=None,
            approvals=_ApprovalsBridge(approvals_sink),
            preset_registry=presets,
            catalog_digest=snapshot.catalog_digest,
            registry_digest=rt_digest)

    def plan_runner_factory(*, admission, lane_bindings, run_context_factory,
                            request_id, prompt_assembler):
        # bridge_analyzers: the SAME map instance the admission view above was
        # built over — never a second recipe, never an empty placeholder (the
        # 2026-07-31 burned-lease defect; see the binding-time comment).
        return build_production_plan_runner(
            stores=stores, catalog_snapshot=snapshot, registry=registry,
            gateway_factory=production_gateway_factory, admission=admission,
            lane_bindings=lane_bindings, run_context_factory=run_context_factory,
            request_id=request_id, clock=clock, runtime_registry_digest=rt_digest,
            runtime_limit=4, catalog=bundle, bridge_analyzers=bridge_analyzers,
            prompt_assembler=prompt_assembler)

    if preset_id != DEEP_DECIDE_PRESET_ID:
        # LOUD on the server's own stderr, not just a logger nobody configured:
        # an operator must never discover after the fact that today's deep
        # verdicts were produced without technical or news evidence.
        print(f"[guanlan_v2][DEEP-PRESET] {DEEP_PRESET_ENV_VAR}=reduced → the "
              f"deep lane runs {preset_id!r}. {REDUCED_EVIDENCE_NOTE}",
              file=sys.stderr)
    _LOG.info("deep lane preset selection: %s (%s=%r)", preset_id,
              DEEP_PRESET_ENV_VAR, os.environ.get(DEEP_PRESET_ENV_VAR, ""))
    return DeepDecideBindings(
        preset_id=preset_id,
        stores=stores, preset_registry=presets, catalog=snapshot,
        schema_registry=registry, coordinator=coordinator_factory,
        admission=admission_factory, clock=clock, plan_runner=plan_runner_factory,
        persist_decision=_seats_api._persist_decision,
        note_llm_use=_watcher.note_external_llm_use,
        latest_snapshot_fn=lambda: _latest_snapshot_production(stores),
        news_titles_fn=None,
        position_fn=_position_production,
        quote_fn=_quote_production,
        strat_fn=_strat_production,
        decisions_tail_fn=_decisions_tail_rows_production)


class _ApprovalsBridge(dict):
    """A dict that mirrors every deposited approval into the caller's sink.

    ``build_plan_approval_coordinator`` requires the admission service's mutable
    approvals mapping; the wrapper additionally deposits into its own per-run
    ``approvals`` store via the sink, so ``record_approval`` loads the exact
    durable decision either way.
    """

    def __init__(self, sink):
        super().__init__()
        self._sink = sink

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._sink(value)


def build_production_decide_fn() -> Callable[[dict], dict]:
    """The env-gated server product: the wrapper over the REAL fast decide path.

    Raises loudly when the production assembly cannot stand up (see
    :func:`build_production_bindings`) — the server catches, logs and stays
    fast-only rather than pretending a deep lane exists.
    """
    bindings = build_production_bindings()
    from guanlan_v2.seats.watcher import _decide_production

    return make_orchestrated_decide(fast_decide=_decide_production, bindings=bindings)
