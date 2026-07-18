# -*- coding: utf-8 -*-
"""Event-sourced cross-run :class:`TrialLedger` over the Phase 2 stores (Task 5).

Phase 1 froze the Trial / Holdout *values* (Task 2) and the additive Trial *event
vocabulary* (Task 3); Phase 2 froze the append-only stores and the atomic
:class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork`; Phase 4 Task 4
froze the governor's family derivation. This module composes them into the
authority that decides, cross-run and cross-experiment, whether an evaluation is a
*new* raw trial or a cached reuse, and enforces the one-shot sealed-holdout lease.

Design (load-bearing)
---------------------
* **Event streams key on ``run_id = family_id``.** Every family's trials share one
  journal, so the ledger is cross-run and cross-experiment by construction. A trial
  id embeds its family (``"<family_id>::<token>"``) so :meth:`reveal_validation` /
  :meth:`exhaust_holdout` locate the journal from the id alone — statelessly, so a
  fresh ledger instance reproduces every read model by folding.
* **The ledger holds no mutable accounting state.** Like the Phase 1
  :class:`~guanlan_v2.orchestration.budget.BudgetLedger`, every read is a pure fold
  over the journal (plus the head cells), so a second instance over the same stores
  (:meth:`replay`) reproduces ``effective_trial_stats`` and every lease state
  exactly. With the Phase 2 in-memory backend this proves *same-process* semantics
  only; cross-process durability arrives with a later durable backend — the same
  honest caveat as Phase 3.
* **Every accepted transition is one :class:`RuntimeUnitOfWork`** (payload put +
  :class:`~guanlan_v2.orchestration.events.RunEvent` append + any state-cell CAS),
  all-or-none. Reservation failures raise loudly — the research store's
  swallow-and-return-bool contract is deliberately not reused here.
* **Idempotency is by content in the journal.** Each :class:`TrialRecord` carries
  its ``idempotency_key``; a replayed key with matching content returns the stored
  record (never re-reading the clock into a fresh batch), and a replayed key with
  different content raises :class:`IdempotencyConflict`. Window registration and
  the holdout lease derive their idempotency from the head cell / lease occupancy.

Frozen event ↔ payload mapping (Task 3, consumed here)
------------------------------------------------------
* validation reserve → ``main`` ``TrialReserved`` (``TrialRecord``);
* validation reveal / reuse → ``main`` ``TrialRevealed`` (``TrialRecord``);
* holdout reserve → ``main`` ``TrialReserved`` (metrics-empty ``TrialRecord``) + a
  ``trial.holdout_lease.v1`` CAS from ``None``;
* holdout terminal → ``main`` ``TrialRevealed`` / ``TrialExhausted`` carrying
  **only** a ``HoldoutReceipt`` (the public-partition metrics restriction), while
  the full terminal holdout ``TrialRecord@1`` is appended to the ``audit``
  partition (Task 3's partition-conditional rule admits it) for fold / audit.

Only the combinations the plan's frozen table names are ever emitted here
(``main`` → ``HoldoutReceipt``-only for terminals; ``audit`` → full ``TrialRecord``);
the Task-3 validator additionally admits ``TrialRecord`` in every non-public
partition, but this ledger never exploits that wider surface.

AMEND-8 future-landing note
---------------------------
Lane D seat-weight accounting (spec:261 — seats start equal-weight, matured later
weighted by seat historical accuracy) will land in this ledger through the
catalog-assembly phase, as an additive chained-registry extension plus a
Task-3-style guard flip. This phase implements none of it and pre-reserves **no**
state-cell keys and **no** event names for it: R2 has not named the seat events —
only the mechanism is declared here. The :meth:`effective_trial_stats` closed key
set is untouched by this note; no seat-weight key is pre-reserved.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from guanlan_v2.orchestration.digest import DigestHex, content_digest
from guanlan_v2.orchestration.eventstore import (
    CasPreconditionFailed,
    EventAppendCommand,
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    StagedPayloadKey,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.governor import (
    FamilyAttestationError,
    derive_study_family,
    verify_family_attestation,
)
from guanlan_v2.orchestration.memory.models import PHASE3_MEMORY_STATE_CELL_NAMESPACES
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.trial import (
    HoldoutReceipt,
    HoldoutWindow,
    StudyFamily,
    StudySpec,
    TrialRecord,
)

__all__ = [
    "PHASE4_TRIAL_STATE_CELL_NAMESPACES",
    "PHASE4_STATE_CELL_NAMESPACES",
    "TRIAL_TRIPLE_DOMAIN",
    "HOLDOUT_WINDOW_ATTESTATION_DOMAIN",
    "L1_METRIC_NAMES",
    "TrialLedgerError",
    "FamilyAttestationError",  # re-exported from governor (single type)
    "HoldoutWindowOverlapError",
    "HoldoutLeaseExhaustedError",
    "TrialReuseViolation",
    "UnknownTrialError",
    "trial_triple_key",
    "compute_holdout_window_attestation",
    "TrialLedger",
]

# --------------------------------------------------------------------------- #
# State-cell namespaces (the sealed Phase-4 union)                             #
# --------------------------------------------------------------------------- #
#: The four Phase-4 recovery-cell namespaces the ledger + optimize state machine
#: own. ``trial.experiment_head.v1`` is reserved for Task 8's optimize run state;
#: Task 5 writes only ``family_head`` / ``holdout_lease`` / ``window_head``.
PHASE4_TRIAL_STATE_CELL_NAMESPACES: tuple[str, ...] = (
    "trial.experiment_head.v1",
    "trial.family_head.v1",
    "trial.holdout_lease.v1",
    "trial.window_head.v1",
)
#: The canonical lexicographic union of the Phase-3 memory namespaces and the four
#: Phase-4 trial namespaces — the sealed startup namespace set a Phase-4-enabled
#: store is sealed with (a new sealed union, not a reseal of a live store).
PHASE4_STATE_CELL_NAMESPACES: tuple[str, ...] = tuple(
    sorted(set(PHASE3_MEMORY_STATE_CELL_NAMESPACES) | set(PHASE4_TRIAL_STATE_CELL_NAMESPACES))
)

#: domain tag for the idempotent trial-reuse key.
TRIAL_TRIPLE_DOMAIN = "trial-triple-v1"
#: domain tag for a holdout window's recomputable non-overlap attestation.
HOLDOUT_WINDOW_ATTESTATION_DOMAIN = "holdout-window-v1"
#: the closed L1 validation metric-name tuple a validation reveal records — a
#: reveal never records a metric name outside this frozen set (canonically sorted).
L1_METRIC_NAMES: tuple[str, ...] = (
    "ann_return",
    "max_drawdown",
    "oos_verdict",
    "rank_ic",
    "sharpe",
    "turnover",
)


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class TrialLedgerError(Exception):
    """Base for every trial-ledger failure."""


class HoldoutWindowOverlapError(TrialLedgerError):
    """A holdout window is not a valid non-overlapping, already-matured registration.

    Raised for an overlapping ``start_at``, a ``prior_window_ids`` set that does not
    acknowledge exactly the currently-registered windows, a ``matured_at`` in the
    future (not-yet-matured OOT data), or a ``non_overlap_attestation`` that does
    not recompute.
    """


class HoldoutLeaseExhaustedError(TrialLedgerError):
    """A second candidate tried to reserve an already-leased ``(family, window)``.

    The one-shot lease is spent for its lifetime and never reopens; a distinct
    candidate is refused before any window data is read.
    """


class TrialReuseViolation(TrialLedgerError):
    """An attempt to reveal (run) a reused trial record.

    A caller may reuse cached results but never reveal more metrics through a new
    run — revealing a record that carries ``reused_from_trial_id`` is refused.
    """


class UnknownTrialError(TrialLedgerError):
    """A trial id does not resolve to any recorded reservation in its family."""


# --------------------------------------------------------------------------- #
# Pure keys                                                                     #
# --------------------------------------------------------------------------- #
def trial_triple_key(
    *,
    family_id: str,
    candidate_hash: str,
    data_snapshot_hash: str,
    split_spec_hash: str,
    code_prompt_model_hash: str,
) -> DigestHex:
    """The domain-tagged idempotent-reuse key of a validation triple.

    Two evaluations of the same ``(family, candidate, data snapshot, split, code /
    prompt / model)`` tuple share this key; a revealed trial under it is reused
    instead of re-revealed, so re-proposing an identical candidate cannot inflate
    the raw trial count the governor deflates against.
    """
    return content_digest(
        {
            "domain": TRIAL_TRIPLE_DOMAIN,
            "family_id": family_id,
            "candidate_hash": candidate_hash,
            "data_snapshot_hash": data_snapshot_hash,
            "split_spec_hash": split_spec_hash,
            "code_prompt_model_hash": code_prompt_model_hash,
        }
    )


def compute_holdout_window_attestation(
    *,
    family_identity_digest: str,
    start_at: datetime,
    end_at: datetime,
    prior_window_ids: tuple[str, ...],
) -> DigestHex:
    """The recomputable non-overlap attestation of a holdout window.

    A domain-tagged digest over the family identity, the window bounds and the
    canonically sorted prior window ids — recomputed on registration so a window
    that lied about its non-overlap evidence is rejected. Deterministic
    verification, not a cryptographic secret.
    """
    return content_digest(
        {
            "domain": HOLDOUT_WINDOW_ATTESTATION_DOMAIN,
            "family_identity_digest": family_identity_digest,
            "start_at": start_at,
            "end_at": end_at,
            "prior_window_ids": sorted(prior_window_ids),
        }
    )


def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


def _template(model: Any) -> dict[str, Any]:
    """A one-level payload template: field name -> field value (models stay models)."""
    return {name: getattr(model, name) for name in type(model).model_fields}


def _family_head_key(family_id: str) -> str:
    return content_digest(["trial.family_head", family_id])


def _window_head_key(family_id: str) -> str:
    return content_digest(["trial.window_head", family_id])


def _holdout_lease_key(family_id: str, window_id: str) -> str:
    return content_digest(["trial.holdout_lease", family_id, window_id])


# --------------------------------------------------------------------------- #
# Fold projection                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class _Fold:
    """The read model folded from one family's ``main`` + ``audit`` journals."""

    trials: dict[str, TrialRecord] = field(default_factory=dict)
    by_idempotency: dict[str, TrialRecord] = field(default_factory=dict)
    revealed_triples: dict[str, str] = field(default_factory=dict)
    reserved_holdout_by_window: dict[str, TrialRecord] = field(default_factory=dict)
    receipts_by_window: dict[str, HoldoutReceipt] = field(default_factory=dict)
    receipts_by_trial: dict[str, HoldoutReceipt] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# The ledger                                                                   #
# --------------------------------------------------------------------------- #
class TrialLedger:
    """Event-sourced cross-run / cross-experiment trial ledger over Phase 2 stores.

    Holds no mutable accounting state: every read folds the journal fresh and every
    write is one atomic :class:`RuntimeUnitOfWork`. Constructed over the injected
    Phase-2 store views; ``registry`` is the sealed cumulative registry (or its
    digest) the payloads validate against, ``uow_factory`` yields the unit of work
    to commit each transition, and ``id_factory`` mints audit-only trial tokens
    (default a uuid-hex factory).
    """

    def __init__(
        self,
        *,
        event_store: Any,
        payload_store: Any,
        state_cells: Any,
        registry: Any,
        clock: AuthoritativeClock,
        uow_factory: Callable[[], Any],
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._event_store = event_store
        self._payload_store = payload_store
        self._state_cells = state_cells
        self._registry = registry
        self._registry_digest = registry if isinstance(registry, str) else registry.registry_digest
        self._clock = clock
        self._uow_factory = uow_factory
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        missing = set(PHASE4_TRIAL_STATE_CELL_NAMESPACES) - set(state_cells.allowed_namespaces)
        if missing:
            raise TrialLedgerError(
                "trial state-cell namespaces missing from the sealed store: "
                f"{sorted(missing)}"
            )

    # -- low-level helpers -------------------------------------------------- #
    def _resolve(self, ref: TypedPayloadRef, schema: str) -> Any:
        if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
            raise TrialLedgerError(f"typed ref does not resolve {schema}@1")
        return self._payload_store.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def _mint_trial_id(self, family_id: str) -> str:
        # embed the family so reveal/exhaust locate the journal statelessly.
        return f"{family_id}::{self._id_factory()}"

    @staticmethod
    def _family_of_trial(trial_id: str) -> str:
        if "::" not in trial_id:
            raise UnknownTrialError(f"trial id {trial_id!r} does not encode a family")
        return trial_id.split("::", 1)[0]

    def _commit(self, batch: RuntimeBatch) -> Any:
        return self._uow_factory().commit(batch)

    def _put_command(
        self, staged: str, schema: str, namespace: str, model: Any, put_key: str
    ) -> PayloadPutCommand:
        return PayloadPutCommand(
            staged_key=StagedPayloadKey(key=staged),
            schema_ref=_SR(schema),
            namespace=namespace,
            payload_template=_template(model),
            registry_digest=self._registry_digest,
            idempotency_key=put_key,
        )

    def _event_command(
        self, run_id: str, partition: str, event_type: EventType,
        schema: str, staged: str, evt_key: str,
    ) -> EventAppendCommand:
        return EventAppendCommand(
            run_id=run_id,
            partition=partition,
            event_type=event_type.value,
            payload_schema_ref=_SR(schema),
            payload_target=StagedPayloadKey(key=staged),
            registry_digest=self._registry_digest,
            idempotency_key=evt_key,
        )

    def _family_setup_commands(
        self, family: StudyFamily
    ) -> tuple[list[PayloadPutCommand], list[StateCellCompareAndSwapCommand]]:
        """On first use, the commands that persist the StudyFamily + head CAS."""
        head_ref = self._state_cells.load(
            "trial.family_head.v1", _family_head_key(family.family_id))
        if head_ref is not None:
            return [], []
        put = self._put_command(
            "fam", "StudyFamily", "main", family, f"fam-put:{family.family_id}")
        cas = StateCellCompareAndSwapCommand(
            cell_namespace="trial.family_head.v1",
            cell_key_digest=_family_head_key(family.family_id),
            expected_value=None,
            new_target=StagedTypedPayloadRef(
                staged_key=StagedPayloadKey(key="fam"),
                schema_ref=_SR("StudyFamily"), namespace="main"),
        )
        return [put], [cas]

    # -- fold --------------------------------------------------------------- #
    def _fold(self, family_id: str) -> _Fold:
        fold = _Fold()
        main = self._event_store.journal(family_id, "main")
        audit = self._event_store.journal(family_id, "audit")
        for ev in tuple(main) + tuple(audit):
            name = ev.payload_schema_ref.name
            model = self._payload_store.get(
                ev.payload_ref, expected_schema_ref=ev.payload_schema_ref)
            if name == "TrialRecord":
                rec: TrialRecord = model
                fold.trials[rec.trial_id] = rec  # later state (revealed/terminal) wins
                fold.by_idempotency[rec.idempotency_key] = rec
                if (
                    rec.stage == "validation"
                    and rec.status == "revealed"
                    and rec.reused_from_trial_id is None
                ):
                    tk = trial_triple_key(
                        family_id=rec.family_id,
                        candidate_hash=rec.candidate_hash,
                        data_snapshot_hash=rec.data_snapshot_hash,
                        split_spec_hash=rec.split_spec_hash,
                        code_prompt_model_hash=rec.code_prompt_model_hash,
                    )
                    fold.revealed_triples.setdefault(tk, rec.trial_id)
                if rec.stage == "sealed_holdout" and rec.status == "reserved":
                    fold.reserved_holdout_by_window[rec.holdout_window_id] = rec
            elif name == "HoldoutReceipt":
                receipt: HoldoutReceipt = model
                fold.receipts_by_window[receipt.holdout_window_id] = receipt
                fold.receipts_by_trial[receipt.trial_id] = receipt
        return fold

    # -- family ------------------------------------------------------------- #
    def resolve_family(self, study: StudySpec) -> StudyFamily:
        """Derive the study's family and read-through the family head cell.

        Pure derivation (never mints an id itself); when a family head is already
        persisted it is resolved and its attestation verified, so a tampered head
        cell payload is rejected on read.
        """
        family = derive_study_family(study)
        head_ref = self._state_cells.load(
            "trial.family_head.v1", _family_head_key(family.family_id))
        if head_ref is not None:
            stored: StudyFamily = self._resolve(head_ref, "StudyFamily")
            verify_family_attestation(stored)
            if stored.identity_digest != family.identity_digest:
                raise FamilyAttestationError(
                    "stored family head does not bind the derived study identity"
                )
        return family

    # -- holdout windows ---------------------------------------------------- #
    def register_holdout_window(
        self, study: StudySpec, window: HoldoutWindow, *, idempotency_key: str
    ) -> HoldoutWindow:
        """Register a non-overlapping, already-matured OOT holdout window.

        Validates the window's family binding and recomputed non-overlap
        attestation, requires ``prior_window_ids`` to acknowledge exactly the
        currently-registered windows, forbids overlap with the latest window
        (boundary-touching ``start_at == prior end_at`` is allowed) and a
        ``matured_at`` in the future, then CAS-advances ``trial.window_head.v1``.
        """
        family = derive_study_family(study)
        if window.family_identity_digest != family.identity_digest:
            raise TrialLedgerError(
                "window.family_identity_digest does not match the derived family"
            )
        expected_att = compute_holdout_window_attestation(
            family_identity_digest=window.family_identity_digest,
            start_at=window.start_at, end_at=window.end_at,
            prior_window_ids=tuple(window.prior_window_ids))
        if window.non_overlap_attestation != expected_att:
            raise HoldoutWindowOverlapError(
                "window non_overlap_attestation does not recompute"
            )
        if window.matured_at > clock_now(self._clock):
            raise HoldoutWindowOverlapError(
                "window.matured_at is in the future (not-yet-matured OOT data)"
            )

        head_ref = self._state_cells.load(
            "trial.window_head.v1", _window_head_key(family.family_id))
        head_window: HoldoutWindow | None = None
        registered_ids: tuple[str, ...] = ()
        if head_ref is not None:
            head_window = self._resolve(head_ref, "HoldoutWindow")
            registered_ids = tuple(head_window.prior_window_ids) + (
                head_window.holdout_window_id,)

        # already-registered idempotent replay (by window id).
        if window.holdout_window_id in registered_ids:
            if head_window is not None and window.holdout_window_id == head_window.holdout_window_id:
                if content_digest(window) != content_digest(head_window):
                    raise IdempotencyConflict(
                        "holdout window id re-registered with different content"
                    )
                return head_window
            return window  # an older prior window — idempotent no-op

        if set(window.prior_window_ids) != set(registered_ids):
            raise HoldoutWindowOverlapError(
                "prior_window_ids must acknowledge exactly all currently-registered windows"
            )
        if head_window is not None and window.start_at < head_window.end_at:
            raise HoldoutWindowOverlapError(
                "window.start_at overlaps the latest registered window "
                "(must be >= its end_at)"
            )

        batch = RuntimeBatch(
            idempotency_key=f"register-window:{idempotency_key}",
            payload_puts=(
                self._put_command(
                    "window", "HoldoutWindow", "main", window,
                    f"register-window-put:{idempotency_key}"),
            ),
            cell_cas=(
                StateCellCompareAndSwapCommand(
                    cell_namespace="trial.window_head.v1",
                    cell_key_digest=_window_head_key(family.family_id),
                    expected_value=head_ref,
                    new_target=StagedTypedPayloadRef(
                        staged_key=StagedPayloadKey(key="window"),
                        schema_ref=_SR("HoldoutWindow"), namespace="main")),
            ),
        )
        self._commit(batch)
        return window

    # -- validation reserve / reveal ---------------------------------------- #
    def reserve_validation(
        self,
        study: StudySpec,
        candidate_ref: TypedPayloadRef,
        split_ref: TypedPayloadRef,
        *,
        data_snapshot_hash: DigestHex,
        code_prompt_model_hash: DigestHex,
        idempotency_key: str,
    ) -> TrialRecord:
        """Reserve a validation trial, or return a cached-reuse record.

        Resolves the family (persisting the head + ``StudyFamily`` on first use),
        dereferences the candidate / split payloads to the reuse triple key, and —
        if a revealed trial with the same triple already exists — returns a new
        ``revealed`` record with ``reused_from_trial_id`` set and the original
        metrics / result digest copied (idempotent reuse, no re-reveal, no new raw
        trial). Otherwise persists a ``reserved`` ``TrialRecord`` + a ``main``
        ``TrialReserved`` event. Failures raise loudly.
        """
        family = self.resolve_family(study)
        candidate = self._resolve(candidate_ref, "OptimizeCandidate")
        split = self._resolve(split_ref, "SplitSpec")
        candidate_hash = candidate.candidate_hash
        split_spec_hash = content_digest(split)
        tk = trial_triple_key(
            family_id=family.family_id, candidate_hash=candidate_hash,
            data_snapshot_hash=data_snapshot_hash, split_spec_hash=split_spec_hash,
            code_prompt_model_hash=code_prompt_model_hash)

        fold = self._fold(family.family_id)
        existing = fold.by_idempotency.get(idempotency_key)
        if existing is not None:
            self._assert_same_triple(existing, tk, family.family_id)
            return existing

        if tk in fold.revealed_triples:
            original = fold.trials[fold.revealed_triples[tk]]
            return self._persist_reuse(
                family, original, candidate_hash, data_snapshot_hash,
                split_spec_hash, code_prompt_model_hash, idempotency_key)

        return self._persist_reserved_validation(
            family, candidate_hash, data_snapshot_hash, split_spec_hash,
            code_prompt_model_hash, idempotency_key)

    def _assert_same_triple(self, existing: TrialRecord, tk: str, family_id: str) -> None:
        existing_tk = trial_triple_key(
            family_id=existing.family_id, candidate_hash=existing.candidate_hash,
            data_snapshot_hash=existing.data_snapshot_hash,
            split_spec_hash=existing.split_spec_hash,
            code_prompt_model_hash=existing.code_prompt_model_hash)
        if existing_tk != tk or existing.family_id != family_id:
            raise IdempotencyConflict(
                "idempotency key reused with different semantic trial content"
            )

    def _persist_reserved_validation(
        self, family: StudyFamily, candidate_hash: str, data_snapshot_hash: str,
        split_spec_hash: str, code_prompt_model_hash: str, idempotency_key: str,
    ) -> TrialRecord:
        record = TrialRecord(
            trial_id=self._mint_trial_id(family.family_id),
            family_id=family.family_id, candidate_hash=candidate_hash,
            data_snapshot_hash=data_snapshot_hash, split_spec_hash=split_spec_hash,
            code_prompt_model_hash=code_prompt_model_hash,
            stage="validation", status="reserved", lease_state="none",
            idempotency_key=idempotency_key, created_at=clock_now(self._clock))
        fam_puts, fam_cas = self._family_setup_commands(family)
        batch = RuntimeBatch(
            idempotency_key=f"reserve-val:{idempotency_key}",
            payload_puts=tuple(fam_puts) + (
                self._put_command("trial", "TrialRecord", "main", record,
                                  f"reserve-val-put:{idempotency_key}"),),
            event_appends=(
                self._event_command(
                    family.family_id, "main", EventType.TRIAL_RESERVED, "TrialRecord",
                    "trial", f"reserve-val-evt:{idempotency_key}"),),
            cell_cas=tuple(fam_cas),
        )
        self._commit(batch)
        return record

    def _persist_reuse(
        self, family: StudyFamily, original: TrialRecord, candidate_hash: str,
        data_snapshot_hash: str, split_spec_hash: str, code_prompt_model_hash: str,
        idempotency_key: str,
    ) -> TrialRecord:
        now = clock_now(self._clock)
        record = TrialRecord(
            trial_id=self._mint_trial_id(family.family_id),
            family_id=family.family_id, candidate_hash=candidate_hash,
            data_snapshot_hash=data_snapshot_hash, split_spec_hash=split_spec_hash,
            code_prompt_model_hash=code_prompt_model_hash,
            metrics_revealed=original.metrics_revealed,
            stage="validation", status="revealed",
            validation_result_artifact_id=original.validation_result_artifact_id,
            result_digest=original.result_digest, revealed_at=now,
            idempotency_key=idempotency_key, reused_from_trial_id=original.trial_id,
            created_at=now)
        batch = RuntimeBatch(
            idempotency_key=f"reuse-val:{idempotency_key}",
            payload_puts=(
                self._put_command("trial", "TrialRecord", "main", record,
                                  f"reuse-val-put:{idempotency_key}"),),
            event_appends=(
                self._event_command(
                    family.family_id, "main", EventType.TRIAL_REVEALED, "TrialRecord",
                    "trial", f"reuse-val-evt:{idempotency_key}"),),
        )
        self._commit(batch)
        return record

    def reveal_validation(
        self, trial_id: str, result_artifact_id: str, result_digest: DigestHex
    ) -> TrialRecord:
        """Transition a ``reserved`` validation trial to ``revealed``.

        Records exactly the closed :data:`L1_METRIC_NAMES`. Revealing a reused
        record raises :class:`TrialReuseViolation` ("may reuse cached results but
        never reveal more metrics through a new run"); an idempotent re-reveal with
        the same result returns the stored record; a different result on an already
        revealed trial is refused.
        """
        family_id = self._family_of_trial(trial_id)
        fold = self._fold(family_id)
        rec = fold.trials.get(trial_id)
        if rec is None:
            raise UnknownTrialError(f"no trial {trial_id!r} in family {family_id!r}")
        if rec.stage != "validation":
            raise TrialLedgerError("reveal_validation only applies to a validation trial")
        if rec.reused_from_trial_id is not None:
            raise TrialReuseViolation(
                "may reuse cached results but never reveal more metrics through a new run"
            )
        if rec.status == "revealed":
            if (
                rec.validation_result_artifact_id == result_artifact_id
                and rec.result_digest == result_digest
            ):
                return rec  # idempotent re-reveal
            raise TrialReuseViolation(
                "a revealed validation trial cannot be re-revealed with a different result"
            )
        if rec.status != "reserved":
            raise TrialLedgerError(
                f"cannot reveal a validation trial in status {rec.status!r}")

        revealed = TrialRecord(
            trial_id=rec.trial_id, family_id=rec.family_id,
            candidate_hash=rec.candidate_hash, data_snapshot_hash=rec.data_snapshot_hash,
            split_spec_hash=rec.split_spec_hash,
            code_prompt_model_hash=rec.code_prompt_model_hash,
            metrics_revealed=L1_METRIC_NAMES, stage="validation", status="revealed",
            validation_result_artifact_id=result_artifact_id, result_digest=result_digest,
            revealed_at=clock_now(self._clock), idempotency_key=rec.idempotency_key,
            created_at=rec.created_at)
        batch = RuntimeBatch(
            idempotency_key=f"reveal-val:{trial_id}",
            payload_puts=(
                self._put_command("trial", "TrialRecord", "main", revealed,
                                  f"reveal-val-put:{trial_id}"),),
            event_appends=(
                self._event_command(
                    family_id, "main", EventType.TRIAL_REVEALED, "TrialRecord",
                    "trial", f"reveal-val-evt:{trial_id}"),),
        )
        self._commit(batch)
        return revealed

    # -- holdout reserve / exhaust ------------------------------------------ #
    def reserve_holdout(
        self,
        study: StudySpec,
        candidate_ref: TypedPayloadRef,
        window_ref: TypedPayloadRef,
        *,
        data_snapshot_hash: DigestHex,
        code_prompt_model_hash: DigestHex,
        idempotency_key: str,
    ) -> TrialRecord:
        """One-shot holdout lease reservation, atomic before any data read.

        CAS-claims ``trial.holdout_lease.v1`` for ``(family_id, holdout_window_id)``
        from ``None``; a second candidate on the same window raises
        :class:`HoldoutLeaseExhaustedError` before any evaluation, and the same
        candidate replays the original reservation. The metrics-empty holdout
        ``TrialRecord`` + a ``main`` ``TrialReserved`` event + the lease CAS commit
        atomically.
        """
        family = self.resolve_family(study)
        candidate = self._resolve(candidate_ref, "OptimizeCandidate")
        window: HoldoutWindow = self._resolve(window_ref, "HoldoutWindow")
        candidate_hash = candidate.candidate_hash
        window_id = window.holdout_window_id
        split_spec_hash = content_digest(window)
        lease_key = _holdout_lease_key(family.family_id, window_id)

        fold = self._fold(family.family_id)
        existing = fold.by_idempotency.get(idempotency_key)
        if existing is not None:
            if existing.holdout_window_id != window_id or existing.candidate_hash != candidate_hash:
                raise IdempotencyConflict(
                    "idempotency key reused with different holdout content"
                )
            return existing

        lease_ref = self._state_cells.load("trial.holdout_lease.v1", lease_key)
        if lease_ref is not None:
            return self._decide_occupied_lease(lease_ref, candidate_hash, window_id)

        record = TrialRecord(
            trial_id=self._mint_trial_id(family.family_id),
            family_id=family.family_id, candidate_hash=candidate_hash,
            data_snapshot_hash=data_snapshot_hash, split_spec_hash=split_spec_hash,
            code_prompt_model_hash=code_prompt_model_hash,
            stage="sealed_holdout", status="reserved",
            holdout_window_id=window_id,
            holdout_lease_id="lease." + content_digest([family.family_id, window_id])[:40],
            lease_state="reserved", idempotency_key=idempotency_key,
            created_at=clock_now(self._clock))
        fam_puts, fam_cas = self._family_setup_commands(family)
        lease_cas = StateCellCompareAndSwapCommand(
            cell_namespace="trial.holdout_lease.v1",
            cell_key_digest=lease_key, expected_value=None,
            new_target=StagedTypedPayloadRef(
                staged_key=StagedPayloadKey(key="trial"),
                schema_ref=_SR("TrialRecord"), namespace="main"))
        batch = RuntimeBatch(
            idempotency_key=f"reserve-holdout:{idempotency_key}",
            payload_puts=tuple(fam_puts) + (
                self._put_command("trial", "TrialRecord", "main", record,
                                  f"reserve-holdout-put:{idempotency_key}"),),
            event_appends=(
                self._event_command(
                    family.family_id, "main", EventType.TRIAL_RESERVED, "TrialRecord",
                    "trial", f"reserve-holdout-evt:{idempotency_key}"),),
            cell_cas=tuple(fam_cas) + (lease_cas,),
        )
        try:
            self._commit(batch)
        except CasPreconditionFailed:
            lease_ref = self._state_cells.load("trial.holdout_lease.v1", lease_key)
            if lease_ref is not None:
                return self._decide_occupied_lease(lease_ref, candidate_hash, window_id)
            raise
        return record

    def _decide_occupied_lease(
        self, lease_ref: TypedPayloadRef, candidate_hash: str, window_id: str
    ) -> TrialRecord:
        reserved: TrialRecord = self._resolve(lease_ref, "TrialRecord")
        if reserved.candidate_hash == candidate_hash:
            return reserved  # same candidate replays the original reservation
        raise HoldoutLeaseExhaustedError(
            f"holdout window {window_id!r} is already leased to a different candidate"
        )

    def exhaust_holdout(
        self,
        trial_id: str,
        *,
        status: Literal["revealed", "failed", "timed_out", "inconclusive"],
        result_digest: DigestHex | None = None,
    ) -> HoldoutReceipt:
        """Atomically terminate a reserved holdout trial.

        ``revealed`` consumes the lease; every other status exhausts it. The
        ``main`` partition receives **only** a :class:`HoldoutReceipt`
        (``TrialRevealed`` for ``revealed``, ``TrialExhausted`` otherwise); the full
        terminal holdout ``TrialRecord`` is appended to the ``audit`` partition for
        fold / audit. Repeated calls return the original receipt idempotently — no
        reopen transition exists.
        """
        family_id = self._family_of_trial(trial_id)
        fold = self._fold(family_id)
        rec = fold.trials.get(trial_id)
        if rec is None or rec.stage != "sealed_holdout":
            raise UnknownTrialError(f"no holdout trial {trial_id!r} in family {family_id!r}")

        existing_receipt = fold.receipts_by_trial.get(trial_id)
        if existing_receipt is not None:
            return existing_receipt  # idempotent terminal, byte-identical, no reopen
        if rec.status != "reserved":
            raise TrialLedgerError(
                f"holdout trial {trial_id!r} is not in a reserved state")

        if status == "revealed":
            if result_digest is None:
                raise TrialLedgerError(
                    "exhaust_holdout(status='revealed') requires a result_digest")
            lease_state = "consumed"
            main_event = EventType.TRIAL_REVEALED
            revealed_at = clock_now(self._clock)
            receipt_digest: DigestHex | None = result_digest
        else:
            lease_state = "exhausted"
            main_event = EventType.TRIAL_EXHAUSTED
            revealed_at = None
            receipt_digest = None

        receipt = HoldoutReceipt(
            trial_id=trial_id, family_id=rec.family_id,
            holdout_window_id=rec.holdout_window_id, status=status,
            result_digest=receipt_digest, revealed_at=revealed_at)
        terminal = TrialRecord(
            trial_id=trial_id, family_id=rec.family_id, candidate_hash=rec.candidate_hash,
            data_snapshot_hash=rec.data_snapshot_hash, split_spec_hash=rec.split_spec_hash,
            code_prompt_model_hash=rec.code_prompt_model_hash,
            stage="sealed_holdout", status=status,
            holdout_window_id=rec.holdout_window_id, holdout_lease_id=rec.holdout_lease_id,
            lease_state=lease_state, result_digest=receipt_digest, revealed_at=revealed_at,
            idempotency_key=rec.idempotency_key, created_at=rec.created_at)

        batch = RuntimeBatch(
            idempotency_key=f"exhaust-holdout:{trial_id}",
            payload_puts=(
                self._put_command("receipt", "HoldoutReceipt", "main", receipt,
                                  f"exhaust-receipt-put:{trial_id}"),
                self._put_command("terminal", "TrialRecord", "audit", terminal,
                                  f"exhaust-terminal-put:{trial_id}"),),
            event_appends=(
                self._event_command(
                    family_id, "main", main_event, "HoldoutReceipt", "receipt",
                    f"exhaust-main-evt:{trial_id}"),
                self._event_command(
                    family_id, "audit", main_event, "TrialRecord", "terminal",
                    f"exhaust-audit-evt:{trial_id}"),),
        )
        self._commit(batch)
        return receipt

    # -- reads -------------------------------------------------------------- #
    def get_trial(self, trial_id: str) -> TrialRecord:
        """The latest-state :class:`TrialRecord` for ``trial_id`` (folded)."""
        family_id = self._family_of_trial(trial_id)
        rec = self._fold(family_id).trials.get(trial_id)
        if rec is None:
            raise UnknownTrialError(f"no trial {trial_id!r} in family {family_id!r}")
        return rec

    def effective_trial_stats(self, study: StudySpec) -> dict:
        """The closed audit statistics the governor consumes.

        Closed key set: ``family_id`` / ``raw_trial_count`` (distinct revealed
        validation triples — the audit upper bound) / ``distinct_candidate_count`` /
        ``reveal_count`` (holdout reveals consumed) / ``holdout_windows_registered``
        / ``holdout_windows_exhausted`` / ``last_revealed_at``.
        """
        family = self.resolve_family(study)
        fold = self._fold(family.family_id)

        revealed_records = [fold.trials[tid] for tid in fold.revealed_triples.values()]
        distinct_candidates = {r.candidate_hash for r in revealed_records}
        reveal_count = sum(
            1 for r in fold.receipts_by_window.values() if r.status == "revealed")

        head_ref = self._state_cells.load(
            "trial.window_head.v1", _window_head_key(family.family_id))
        if head_ref is not None:
            head_window: HoldoutWindow = self._resolve(head_ref, "HoldoutWindow")
            windows_registered = len(head_window.prior_window_ids) + 1
        else:
            windows_registered = 0

        revealed_times = [r.revealed_at for r in revealed_records if r.revealed_at is not None]
        revealed_times += [
            r.revealed_at for r in fold.receipts_by_window.values()
            if r.revealed_at is not None]
        last_revealed_at = max(revealed_times).isoformat() if revealed_times else None

        return {
            "family_id": family.family_id,
            "raw_trial_count": len(fold.revealed_triples),
            "distinct_candidate_count": len(distinct_candidates),
            "reveal_count": reveal_count,
            "holdout_windows_registered": windows_registered,
            "holdout_windows_exhausted": len(fold.receipts_by_window),
            "last_revealed_at": last_revealed_at,
        }

    def replay(self, family_id: str) -> "TrialLedger":
        """A fresh ledger over the same stores — the fold reconstruction.

        The ledger caches no accounting state, so a new instance reproduces
        ``effective_trial_stats`` and every lease state purely by folding the
        journal (same-process semantics with the Phase 2 in-memory backend).
        """
        return TrialLedger(
            event_store=self._event_store, payload_store=self._payload_store,
            state_cells=self._state_cells, registry=self._registry, clock=self._clock,
            uow_factory=self._uow_factory, id_factory=self._id_factory)
