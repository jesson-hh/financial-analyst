# -*- coding: utf-8 -*-
"""Capability-isolated sealed result store + one-shot sealed evaluator gateway (Task 6).

This is the security spine of the Phase 4 Evaluator-Optimizer: the detailed
holdout metrics/curves of a sealed evaluation exist **only** as ``sealed``-namespace
payloads, reachable through exactly one capability-gated read path
(:meth:`SealedResultStore.get`). No optimizer / ``improve`` / L3 / memory / public
pool path can dereference them. The public artifact of a holdout run is the opaque
:class:`~guanlan_v2.orchestration.trial.HoldoutReceipt` (status + opaque
``result_digest``), carried on the ``main`` journal by the Task 5 ledger; it has no
ref field, so it is structurally incapable of pointing at sealed content.

Structural isolation (frozen access-control matrix)
---------------------------------------------------
The security does **not** rest on :meth:`PayloadStore.get` refusing the ``sealed``
namespace — it does not. ``PayloadStore.get`` has no namespace or capability gate:
hand it the exact :class:`PayloadRef` (namespace + content digest) and it returns
the payload. Sealed metrics stay unreachable because that ref never escapes,
defended by three independent barriers:

* **(a) Unlinkability.** A sealed record is persisted with a bare
  :meth:`PayloadStore.put` (``namespace="sealed"``) that writes **no** event; the
  only reference to it lives inside :class:`SealedResultStore`'s in-process
  ``_sealed_index``. The ledger's terminal transition appends a ``HoldoutReceipt``
  (``main``) + the full terminal ``TrialRecord`` (``audit``) — neither carries the
  sealed ``PayloadRef``, so no public/audit reader can discover it.
* **(b) Content-digest-as-capability.** Even a reader who guessed the sealed object
  id could not dereference it: :meth:`PayloadStore.get` demands the exact content
  digest, which is never published. The public ``HoldoutReceipt.result_digest`` is a
  *different* digest domain (:data:`SEALED_RESULT_DOMAIN` over the metrics), not the
  payload content digest.
* **(c) Event-path NamespaceViolation.** The Phase 1/2 ``EventStore`` refuses at
  ``append`` any ``main`` event that names a ``sealed`` payload (namespace-masquerade
  rule), so a coding error cannot smuggle the ref onto the public journal.

On top of these, the one sanctioned reader (:meth:`SealedResultStore.get`) adds a
**capability gate** — a valid :class:`SealedCapability` (``final_report`` /
``human_review`` scope); an expired or forged capability is refused with
:class:`CapabilityVerificationError`. And :func:`assert_no_sealed_refs` is a
defense-in-depth guard refusing any non-public payload ref before an adapter stages
evidence into the Phase 2 ``ArtifactPool``.

One-shot lease coordination (with Task 5)
-----------------------------------------
The gateway coordinates **with** the ledger's one-shot ``(family_id,
holdout_window_id)`` lease (CAS-from-``None`` on ``trial.holdout_lease.v1``); it
never opens a second lease mechanism. :func:`issue_holdout_lease` mints the signed
:class:`HoldoutLease` value over the ledger-assigned reservation, and
:meth:`SealedEvaluatorGateway.evaluate_once` verifies it (signature + binding +
expiry) **before** the evaluation callable runs. Every terminal path — success,
evaluator exception, timeout, all-``None`` metrics — exhausts the window through the
ledger and returns a receipt; a re-call after a terminal returns the byte-identical
original receipt without re-running the callable and without reopening the lease.

Signature honesty (v1)
----------------------
:data:`LEASE_SIGNATURE_DOMAIN` / :data:`CAPABILITY_SIGNATURE_DOMAIN` v1 signatures
are domain-tagged recomputable digests over the record's semantic fields plus its
``nonce`` / ``token_id`` — **deterministic verification, not cryptographic
secrecy**. A later reviewed change may swap in keyed signing (HMAC/asymmetric)
without an ABI change, since verification stays a pure function of the value.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any, Literal

from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    PositiveInt,
    content_digest,
)
from guanlan_v2.orchestration.refs import (
    PUBLIC_PAYLOAD_NAMESPACES,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.trial import (
    HoldoutLease,
    HoldoutReceipt,
    HoldoutWindow,
    SealedCapability,
    SealedEvaluationRecord,
    StudySpec,
    TrialRecord,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial_ledger import TrialLedger

__all__ = [
    "LEASE_SIGNATURE_DOMAIN",
    "CAPABILITY_SIGNATURE_DOMAIN",
    "SEALED_NAMESPACE",
    "SEALED_RESULT_DOMAIN",
    "SealedAccessError",
    "LeaseVerificationError",
    "CapabilityVerificationError",
    "issue_holdout_lease",
    "verify_holdout_lease",
    "issue_sealed_capability",
    "verify_sealed_capability",
    "assert_no_sealed_refs",
    "SealedResultStore",
    "SealedEvaluatorGateway",
]

#: domain tag separating the holdout-lease signature space from any other digest.
LEASE_SIGNATURE_DOMAIN = "holdout-lease-v1"
#: domain tag for the sealed-capability signature space.
CAPABILITY_SIGNATURE_DOMAIN = "sealed-capability-v1"
#: domain tag for the opaque sealed-holdout ``result_digest``.
SEALED_RESULT_DOMAIN = "sealed-holdout-result-v1"
#: the single non-public namespace the sealed evaluation record lives in.
SEALED_NAMESPACE = "sealed"

#: the decision-bearing :class:`ValidationMetrics` fields — when *every* one is
#: ``None`` the holdout produced no decidable content and the trial is honestly
#: ``inconclusive`` (never scored on empty evidence). Bookkeeping-only fields
#: (``n_dates`` / ``factor`` / ``n_occurrences`` / ``source``) are excluded.
_CORE_METRIC_FIELDS: tuple[str, ...] = (
    "rank_ic",
    "sharpe",
    "ann_return",
    "max_drawdown",
    "turnover",
    "win_rate",
    "tail_ratio",
    "coverage",
    "oos_verdict",
    "profit_loss_ratio",
)

#: the exact schema the sealed evaluation record is persisted / read under.
_SEALED_RECORD_SCHEMA = SchemaRef(name="SealedEvaluationRecord", version="1")


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class SealedAccessError(Exception):
    """Base for every sealed-holdout access-control refusal."""


class LeaseVerificationError(SealedAccessError):
    """A holdout lease failed signature / binding / expiry verification."""


class CapabilityVerificationError(SealedAccessError):
    """A sealed capability failed signature / expiry verification."""


# --------------------------------------------------------------------------- #
# One-shot holdout lease issuance / verification                               #
# --------------------------------------------------------------------------- #
def _lease_signature(
    *,
    lease_id: str,
    trial_id: str,
    candidate_hash: str,
    holdout_window_id: str,
    issued_at: Any,
    expires_at: Any,
    nonce: str,
) -> DigestHex:
    """The domain-tagged recomputable signature of a holdout lease."""
    return content_digest(
        {
            "domain": LEASE_SIGNATURE_DOMAIN,
            "lease_id": lease_id,
            "trial_id": trial_id,
            "candidate_hash": candidate_hash,
            "holdout_window_id": holdout_window_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": nonce,
        }
    )


def issue_holdout_lease(
    *,
    trial: TrialRecord,
    clock: AuthoritativeClock,
    ttl_seconds: PositiveInt,
    nonce: NonEmptyStr,
) -> HoldoutLease:
    """Mint a signed one-shot :class:`HoldoutLease` over a reserved holdout trial.

    Only callable with a ``stage="sealed_holdout", status="reserved"`` record — the
    ledger reservation that already claimed the ``(family, window)`` lease cell. The
    lease reuses the ledger-assigned ``holdout_lease_id`` (this is not a second lease
    mechanism) and signs ``{lease_id, trial_id, candidate_hash, holdout_window_id,
    issued_at, expires_at, nonce}``.
    """
    if trial.stage != "sealed_holdout" or trial.status != "reserved":
        raise SealedAccessError(
            "issue_holdout_lease requires a reserved sealed_holdout TrialRecord; got "
            f"stage={trial.stage!r} status={trial.status!r}"
        )
    if trial.holdout_lease_id is None or trial.holdout_window_id is None:
        raise SealedAccessError(
            "a reserved holdout record must carry holdout_lease_id and holdout_window_id"
        )
    issued_at = clock_now(clock)
    expires_at = issued_at + timedelta(seconds=int(ttl_seconds))
    signature = _lease_signature(
        lease_id=trial.holdout_lease_id,
        trial_id=trial.trial_id,
        candidate_hash=trial.candidate_hash,
        holdout_window_id=trial.holdout_window_id,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
    )
    return HoldoutLease(
        lease_id=trial.holdout_lease_id,
        trial_id=trial.trial_id,
        candidate_hash=trial.candidate_hash,
        holdout_window_id=trial.holdout_window_id,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        signature=signature,
    )


def verify_holdout_lease(
    lease: HoldoutLease, *, trial: TrialRecord, clock: AuthoritativeClock
) -> None:
    """Verify a lease against its reserved trial and the authoritative clock.

    Recomputes the signature (catching any tampered signed field), checks the
    ``trial_id`` / ``candidate_hash`` / ``holdout_window_id`` binding to the reserved
    record, and refuses an expired lease (``now >= expires_at``). Raises
    :class:`LeaseVerificationError` on any failure — the caller must run no
    evaluation on a lease that does not verify.
    """
    expected = _lease_signature(
        lease_id=lease.lease_id,
        trial_id=lease.trial_id,
        candidate_hash=lease.candidate_hash,
        holdout_window_id=lease.holdout_window_id,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        nonce=lease.nonce,
    )
    if expected != lease.signature:
        raise LeaseVerificationError("holdout lease signature does not recompute (tampered)")
    if (
        lease.trial_id != trial.trial_id
        or lease.candidate_hash != trial.candidate_hash
        or lease.holdout_window_id != trial.holdout_window_id
    ):
        raise LeaseVerificationError("holdout lease is not bound to the reserved trial")
    if clock_now(clock) >= lease.expires_at:
        raise LeaseVerificationError("holdout lease has expired")


# --------------------------------------------------------------------------- #
# Sealed capability issuance / verification                                    #
# --------------------------------------------------------------------------- #
def _capability_signature(
    *, token_id: str, scope: str, principal_id: str, expires_at: Any
) -> DigestHex:
    """The domain-tagged recomputable signature of a sealed capability."""
    return content_digest(
        {
            "domain": CAPABILITY_SIGNATURE_DOMAIN,
            "token_id": token_id,
            "scope": scope,
            "principal_id": principal_id,
            "expires_at": expires_at,
        }
    )


def issue_sealed_capability(
    *,
    scope: Literal["final_report", "human_review"],
    principal_id: NonEmptyStr,
    clock: AuthoritativeClock,
    ttl_seconds: PositiveInt,
) -> SealedCapability:
    """Issue a scope-bound, time-limited capability to read sealed holdout content.

    The sealed store admits only ``final_report`` / ``human_review`` readers (the
    closed :class:`SealedCapability` scope literal); no optimizer / ``improve`` / L3
    scope exists, so those readers can never construct a valid capability.
    """
    issued_at = clock_now(clock)
    expires_at = issued_at + timedelta(seconds=int(ttl_seconds))
    token_id = "cap." + content_digest(
        {
            "domain": CAPABILITY_SIGNATURE_DOMAIN,
            "scope": scope,
            "principal_id": principal_id,
            "issued_at": issued_at,
        }
    )[:40]
    signature = _capability_signature(
        token_id=token_id, scope=scope, principal_id=principal_id, expires_at=expires_at
    )
    return SealedCapability(
        token_id=token_id,
        scope=scope,
        principal_id=principal_id,
        expires_at=expires_at,
        signature=signature,
    )


def verify_sealed_capability(
    capability: SealedCapability, *, clock: AuthoritativeClock
) -> None:
    """Verify a capability's signature and expiry; raise on forgery or expiry.

    The scope is a closed literal validated at construction, so verification is the
    recomputable signature (forgery) plus ``now < expires_at`` (expiry). Both scopes
    (``final_report`` / ``human_review``) are admitted readers.
    """
    expected = _capability_signature(
        token_id=capability.token_id,
        scope=capability.scope,
        principal_id=capability.principal_id,
        expires_at=capability.expires_at,
    )
    if expected != capability.signature:
        raise CapabilityVerificationError(
            "sealed capability signature does not recompute (forged)"
        )
    if clock_now(clock) >= capability.expires_at:
        raise CapabilityVerificationError("sealed capability has expired")


# --------------------------------------------------------------------------- #
# Public-pool rejection helper (defense-in-depth)                              #
# --------------------------------------------------------------------------- #
def assert_no_sealed_refs(refs: Iterable[PayloadRef | TypedPayloadRef]) -> None:
    """Refuse any non-public payload ref before it crosses into the public pool.

    Raises :class:`SealedAccessError` on the first ref whose payload namespace is not
    in :data:`PUBLIC_PAYLOAD_NAMESPACES` (``sealed`` / ``review`` / ``audit``). Used
    by adapters before staging evidence into the Phase 2 ``ArtifactPool`` — a
    redundant guard on top of the Phase 1/2 namespace validators, so a coding error
    that tried to stage a sealed ref is caught at the boundary rather than silently.
    """
    for ref in refs:
        if isinstance(ref, TypedPayloadRef):
            namespace = ref.payload_ref.namespace
        elif isinstance(ref, PayloadRef):
            namespace = ref.namespace
        else:
            raise SealedAccessError(
                "assert_no_sealed_refs expects PayloadRef / TypedPayloadRef, got "
                f"{type(ref).__name__}"
            )
        if namespace not in PUBLIC_PAYLOAD_NAMESPACES:
            raise SealedAccessError(
                f"a non-public ({namespace!r}) payload ref may not cross into the public pool"
            )


# --------------------------------------------------------------------------- #
# Sealed result store                                                          #
# --------------------------------------------------------------------------- #
class SealedResultStore:
    """The capability-isolated store of sealed holdout evaluation records.

    A record is persisted with a bare ``PayloadStore.put`` in the ``sealed``
    namespace — **no event, no public journal reference** — and indexed only in this
    store's in-process map (``trial_id -> sealed PayloadRef``). The single read path
    is :meth:`get`, gated by a valid :class:`SealedCapability`. Terminal reveal is
    delegated to the Task 5 ledger, which is idempotent, so a re-``put`` returns the
    original :class:`HoldoutReceipt` without any further write or reopen.
    """

    def __init__(self, *, payload_store: Any, ledger: TrialLedger, clock: AuthoritativeClock,
                 registry: Any) -> None:
        self._payload_store = payload_store
        self._ledger = ledger
        self._clock = clock
        self._registry_digest = (
            registry if isinstance(registry, str) else registry.registry_digest
        )
        self._sealed_index: dict[str, PayloadRef] = {}
        self._lock = threading.RLock()

    def put(self, lease: HoldoutLease, record: SealedEvaluationRecord) -> HoldoutReceipt:
        """Seal the evaluation record and reveal the opaque receipt (idempotently).

        Verifies ``lease.lease_id`` is the live reservation for ``record.trial_id``
        **and** authenticates the full lease token via :func:`verify_holdout_lease`
        (signature + trial binding + expiry) — id-equality alone would let a direct
        ``put`` caller seal a record using only the audit-visible lease id, so the
        full signature check is enforced here as well as at the gateway. Then
        persists the :class:`SealedEvaluationRecord` (and, by its own validator, any
        ``curve_ref`` already points into the ``sealed`` namespace) with
        ``namespace="sealed"``, then calls
        ``ledger.exhaust_holdout(status="revealed", result_digest=record.result_digest)``.
        Returns the resulting :class:`HoldoutReceipt`. A second ``put`` for a trial
        that is already terminal returns the original receipt **without writing**.
        """
        trial_id = record.trial_id
        trial = self._ledger.get_trial(trial_id)  # UnknownTrialError if absent
        if trial.stage != "sealed_holdout":
            raise SealedAccessError(
                f"trial {trial_id!r} is not a sealed_holdout trial"
            )
        if trial.holdout_lease_id != lease.lease_id:
            raise LeaseVerificationError(
                "put lease_id is not the live reservation for this trial"
            )
        # full-token authentication (M1): signature + trial binding + expiry, so a
        # forged / mis-bound / expired lease cannot bypass with a matching id.
        verify_holdout_lease(lease, trial=trial, clock=self._clock)
        with self._lock:
            if trial.status == "reserved":
                ref = self._payload_store.put(
                    _SEALED_RECORD_SCHEMA,
                    record,
                    registry_digest=self._registry_digest,
                    namespace=SEALED_NAMESPACE,
                    idempotency_key=f"sealed-put:{trial_id}",
                )
                self._sealed_index[trial_id] = ref
            # exhaust_holdout(revealed) is idempotent: on a second put (trial already
            # terminal) it short-circuits to the byte-identical original receipt with
            # no write and no reopen.
            return self._ledger.exhaust_holdout(
                trial_id, status="revealed", result_digest=record.result_digest
            )

    def get(
        self, trial_id: str, *, capability: SealedCapability
    ) -> SealedEvaluationRecord:
        """The only *sanctioned* read path — verify the capability, then return the record.

        The isolation does **not** come from :meth:`PayloadStore.get` self-defending:
        that primitive has no namespace or capability gate (see eventstore.py) — hand
        it the exact :class:`PayloadRef` (namespace + content digest) and it returns
        the payload. Sealed content stays unreachable because that ref never escapes:

        * **unlinkability** — the sealed ``PayloadRef`` lives only in this store's
          in-process ``_sealed_index``, written with *no* journal event, so no
          public/audit reader can discover it; and
        * **content-digest-as-capability** — a dereference needs the exact content
          digest, which is never published (the public ``HoldoutReceipt.result_digest``
          is a *different* digest domain, :data:`SEALED_RESULT_DOMAIN` over the
          metrics, not the payload content digest).

        The capability check added here is the gate for the one legitimate reader; it
        is not what keeps the optimizer / ``improve`` / L3 side out.
        """
        verify_sealed_capability(capability, clock=self._clock)
        ref = self._sealed_index.get(trial_id)
        if ref is None:
            raise SealedAccessError(f"no sealed record for trial {trial_id!r}")
        return self._payload_store.get(ref, expected_schema_ref=_SEALED_RECORD_SCHEMA)


# --------------------------------------------------------------------------- #
# Sealed evaluator gateway                                                     #
# --------------------------------------------------------------------------- #
class SealedEvaluatorGateway:
    """Process/authority-separated one-shot sealed evaluator (spec §2.2).

    Holds the deterministic ``evaluate_holdout`` callable but **no** read path into
    the sealed store — it only writes through :meth:`SealedResultStore.put`. The
    reservation (and its ``TrialRecord``) is written strictly before any window data
    is read; every terminal path exhausts the one-shot window and returns a receipt.
    """

    def __init__(
        self,
        *,
        study: StudySpec,
        ledger: TrialLedger,
        sealed_store: SealedResultStore,
        evaluate_holdout: Callable[[TypedPayloadRef, HoldoutWindow], ValidationMetrics],
        clock: AuthoritativeClock,
        lease_ttl_seconds: PositiveInt,
        timeout_seconds: PositiveInt,
        data_snapshot_hash: DigestHex,
        code_prompt_model_hash: DigestHex,
    ) -> None:
        self._study = study
        self._ledger = ledger
        self._sealed_store = sealed_store
        self._evaluate_holdout = evaluate_holdout
        self._clock = clock
        self._lease_ttl_seconds = int(lease_ttl_seconds)
        self._timeout_seconds = int(timeout_seconds)
        self._data_snapshot_hash = data_snapshot_hash
        self._code_prompt_model_hash = code_prompt_model_hash
        # the gateway dereferences the window it already reserved against through the
        # ledger's shared payload store (a window locator, never sealed content).
        self._payload_store = ledger._payload_store
        self._window_by_trial: dict[str, HoldoutWindow] = {}

    def reserve_and_lease(
        self,
        frozen_candidate_ref: TypedPayloadRef,
        *,
        window_ref: TypedPayloadRef,
        idempotency_key: str,
    ) -> tuple[TrialRecord, HoldoutLease]:
        """Reserve the one-shot window and mint the signed lease — before any read.

        The ledger CAS-claims ``(family, window)`` from ``None`` and writes the
        metrics-empty reserved ``TrialRecord`` atomically; only then is a signed
        :class:`HoldoutLease` minted over it. The window payload is resolved here
        (a metadata locator, not the holdout data itself) so :meth:`evaluate_once`
        can hand it to the deterministic callable.
        """
        reserved = self._ledger.reserve_holdout(
            self._study,
            frozen_candidate_ref,
            window_ref,
            data_snapshot_hash=self._data_snapshot_hash,
            code_prompt_model_hash=self._code_prompt_model_hash,
            idempotency_key=idempotency_key,
        )
        window = self._payload_store.get(
            window_ref.payload_ref, expected_schema_ref=window_ref.schema_ref
        )
        self._window_by_trial[reserved.trial_id] = window
        nonce = "nonce." + content_digest(
            [reserved.trial_id, reserved.holdout_lease_id]
        )[:40]
        lease = issue_holdout_lease(
            trial=reserved,
            clock=self._clock,
            ttl_seconds=self._lease_ttl_seconds,
            nonce=nonce,
        )
        return reserved, lease

    def evaluate_once(
        self,
        frozen_candidate_ref: TypedPayloadRef,
        *,
        holdout_reservation_id: str,
        lease_token: HoldoutLease,
    ) -> HoldoutReceipt:
        """Evaluate the sealed holdout exactly once; every path exhausts the window.

        Verifies the lease against the reserved record (a forged / expired /
        mismatched lease raises **before** the callable runs). If the trial is
        already terminal, returns the original receipt (idempotent recovery, no
        reopen, no re-run). Otherwise runs the deterministic callable bounded by
        ``timeout_seconds``: success → build a :class:`SealedEvaluationRecord`
        (metrics dumped into the sealed payload) → :meth:`SealedResultStore.put`;
        evaluator exception → ``failed``; timeout → ``timed_out``; all-``None`` core
        metrics → ``inconclusive``. Each returns a :class:`HoldoutReceipt`.
        """
        trial = self._ledger.get_trial(holdout_reservation_id)
        if trial.status != "reserved":
            # Terminal-first idempotent crash recovery: a receipt already exists,
            # so re-return it byte-identically **without** lease verification. A
            # terminal trial requires no *live* lease — the lease may legitimately
            # have expired between the original evaluation and this recovery call,
            # and refusing recovery on expiry would break the "re-calling
            # evaluate_once returns the original receipt" invariant. Only a
            # genuinely-pending (still-``reserved``) evaluation, below, needs a
            # verified live lease. No reopen, no callable re-run.
            return self._ledger.exhaust_holdout(
                holdout_reservation_id, status=trial.status,
                result_digest=trial.result_digest,
            )
        verify_holdout_lease(lease_token, trial=trial, clock=self._clock)

        window = self._resolve_window(trial)
        metrics, error, timed_out = self._run_bounded(frozen_candidate_ref, window)
        if timed_out:
            return self._ledger.exhaust_holdout(holdout_reservation_id, status="timed_out")
        if error is not None:
            return self._ledger.exhaust_holdout(holdout_reservation_id, status="failed")
        if self._is_inconclusive(metrics):
            return self._ledger.exhaust_holdout(
                holdout_reservation_id, status="inconclusive"
            )
        record = self._build_record(trial, metrics)
        return self._sealed_store.put(lease_token, record)

    # -- helpers ------------------------------------------------------------ #
    def _resolve_window(self, trial: TrialRecord) -> HoldoutWindow:
        window = self._window_by_trial.get(trial.trial_id)
        if window is None:
            raise SealedAccessError(
                f"gateway holds no window binding for reservation {trial.trial_id!r}; "
                "reserve_and_lease must precede evaluate_once"
            )
        return window

    def _run_bounded(
        self, cand_ref: TypedPayloadRef, window: HoldoutWindow
    ) -> tuple[ValidationMetrics | None, BaseException | None, bool]:
        """Run the callable in a bounded worker thread; return (metrics, error, timed_out)."""
        box: dict[str, Any] = {}

        def _runner() -> None:
            try:
                box["metrics"] = self._evaluate_holdout(cand_ref, window)
            except BaseException as exc:  # noqa: BLE001 — any evaluator failure = failed
                box["error"] = exc

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join(self._timeout_seconds)
        if thread.is_alive():
            return None, None, True
        if "error" in box:
            return None, box["error"], False
        return box.get("metrics"), None, False

    @staticmethod
    def _is_inconclusive(metrics: ValidationMetrics | None) -> bool:
        if metrics is None:
            return True
        return all(getattr(metrics, name) is None for name in _CORE_METRIC_FIELDS)

    def _build_record(
        self, trial: TrialRecord, metrics: ValidationMetrics
    ) -> SealedEvaluationRecord:
        metrics_payload = metrics.model_dump(mode="json")
        result_digest = content_digest(
            {
                "domain": SEALED_RESULT_DOMAIN,
                "trial_id": trial.trial_id,
                "metrics": metrics_payload,
            }
        )
        return SealedEvaluationRecord(
            trial_id=trial.trial_id,
            result_artifact_id="sealed-result." + trial.trial_id,
            result_digest=result_digest,
            metrics_payload=metrics_payload,
            curve_ref=None,
            created_at=clock_now(self._clock),
        )
