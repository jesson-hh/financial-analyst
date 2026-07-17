"""The point-in-time firewall — ``PitGuard`` refuses future data.

This module is the PIT 红线 heart of the Phase-3 data layer. It judges every raw
row/vintage by its **available_at** (当时可知时间, *when the fact became knowable*)
against the *frozen* ``ctx.as_of`` cutoff — never period-end, never the wall clock,
never a global "today". Three disjoint outcomes, and there is deliberately no
fourth soft path:

* **pass** — every candidate has an aware ``available_at`` at or before ``as_of``
  and (if a policy is supplied) is fresh; the guard returns the exact ordered
  tuple plus a coherent ``PitAudit(guard_result="passed")``.
* **refuse** — *any* ``available_at > as_of`` raises :class:`FutureDataRefused`;
  a missing or naive ``available_at`` raises :class:`MissingAvailabilityRefused`;
  a request whose context digest disagrees with the guard's bound context raises
  :class:`SnapshotMismatchError`; a mis-built ``PIT_REPLAY`` context is refused at
  binding time. Every refusal is recorded *exactly once* through the supplied
  recorder (a typed :class:`DataFetchRefusalDetails` + ordered
  :class:`~guanlan_v2.orchestration.runtime_contracts.NamedEvidenceDigest`\\ s) and
  then the typed error is raised. None of these is silently filtered and none is
  fallback-eligible (they live in the *raise* bucket of the Task-1 taxonomy).
* **terminate** — data that is PIT-valid but older than the versioned
  :class:`FreshnessPolicy` allows raises :class:`StaleDataError` with an honest
  ``latest_available_at`` and a ``pit_audit`` (a typed terminal, not a refusal).

The guard is *bound to* a :class:`DataContext`'s ``as_of`` / mode / frozen clock
via :meth:`PitGuard.from_context` and copies no caller override: mode, backend,
strictness, as-of, snapshot locator/content digest, vintage digest and
source-config identity come only from the context. Freshness is evaluated against
that frozen ``as_of`` and the exact injected trading calendar — there is no global
``MAX_STALE_DAYS`` and no session-arithmetic fallback.

``RawRowCandidate`` / ``FreshnessPolicy`` / ``DataFetchRefusalDetails`` are strict,
frozen, versioned :class:`~guanlan_v2.orchestration.digest.DigestModel`\\ s. They
are intentionally **not** registered in the Phase-1 ``default_registry`` (Task 5's
data / audit-detail registries register them), so this module adds no Phase-1
payload schema; it is classified into ``INTERNAL_MODELS`` for the completeness
firewall exactly like Task 3's calendar material and limit-rule policy.

``PitGuard`` constructs no store and requires no Task-5 registry: it records a
refusal through an injected recorder protocol (proved with an in-memory spy in
Task 4) and Task 6 binds that recorder to the production
:class:`~guanlan_v2.orchestration.eventstore.EventRefusalAuditSink` /
``CapabilityGateway.reject`` adapter. Neither the guard nor any recorder caller
constructs an ``EventRefusalRecord``, and a refusal is never forged as a public
``RunEvent``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import model_validator

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import TradingCalendar
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    LiveFallbackRefused,
    MissingAvailabilityRefused,
    RoutingConfigurationError,
    SnapshotMismatchError,
    StaleDataError,
)
from guanlan_v2.orchestration.data.result import PitAudit
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import NamedEvidenceDigest

__all__ = [
    "RawRowCandidate",
    "FreshnessPolicy",
    "DataFetchRefusalDetails",
    "DataRefusalRecorder",
    "PitGuard",
    "DataRefusalStage",
    "DataRefusalReason",
]

#: Well-formed but deliberately-wrong digest used by the ``build`` classmethods
#: when field values are malformed: it forces the validating constructor to
#: surface the real ``ValidationError`` and can never seal a valid record.
_DIGEST_PLACEHOLDER = "0" * 64

#: Closed refusal-stage vocabulary — *how far execution reached* when refused.
DataRefusalStage = Literal["context_validation", "candidate_check"]
#: Closed refusal-reason vocabulary.
DataRefusalReason = Literal[
    "request_context_digest_mismatch",
    "future_data",
    "missing_availability",
    "naive_availability",
]
#: reason ↔ stage partition (each reason belongs to exactly one stage).
_CONTEXT_VALIDATION_REASONS: frozenset[str] = frozenset({"request_context_digest_mismatch"})
_CANDIDATE_CHECK_REASONS: frozenset[str] = frozenset(
    {"future_data", "missing_availability", "naive_availability"}
)


def _is_aware(v: datetime) -> bool:
    """True iff ``v`` is a tz-aware datetime (a naive value cannot prove PIT)."""
    return v.tzinfo is not None and v.tzinfo.utcoffset(v) is not None


# --------------------------------------------------------------------------- #
# RawRowCandidate — the minimal pre-validation envelope                        #
# --------------------------------------------------------------------------- #
class RawRowCandidate(DigestModel):
    """The minimal immutable pre-validation envelope for one raw row/vintage.

    Carries the canonical raw JSON ``raw_payload`` (never consumable data — Task 5
    converts a *passed* candidate into a registered ``PitRecord`` subtype), the
    ``effective_at`` period time, the *classifiable* ``available_at`` (a plain
    ``datetime | None`` so **missing** metadata — ``None`` — or a **naive**
    tz-less value stays representable and can be refused rather than rejected at
    construction), the trusted ``ingested_at`` collection time (audit-only for
    freshness), an optional ``revision_id``, a ``required`` marker and the verified
    ``raw_content_digest`` of the raw payload.

    ``available_at`` is deliberately *not* a ``UtcDateTime`` field: the guard, not
    the type system, decides that a missing/naive availability is a
    :class:`MissingAvailabilityRefused`. A *passed* candidate always carries an
    aware ``available_at``, so the whole model stays canonically digestible.
    ``raw_content_digest`` is the digest of the *referenced* ``raw_payload`` (an
    ordinary verified field, like a :class:`ContentRef`'s digest), re-checked on
    load; it is never treated as raw bytes to leak.
    """

    schema_version: Literal["1"] = "1"
    raw_payload: dict[str, Any]
    effective_at: UtcDateTime | None = None
    available_at: datetime | None = None
    ingested_at: UtcDateTime
    revision_id: NonEmptyStr | None = None
    required: bool = True
    raw_content_digest: DigestHex

    @model_validator(mode="after")
    def _verify(self) -> "RawRowCandidate":
        if self.raw_content_digest != content_digest(self.raw_payload):
            raise ValueError("declared raw_content_digest does not match the raw payload")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RawRowCandidate":
        """Seal a candidate: compute ``raw_content_digest`` from ``raw_payload``.

        A malformed payload (non-canonical value) makes the digest projection raise;
        we fall back to a placeholder so the validating constructor surfaces the
        precise ``ValidationError``. A placeholder can never seal a real record
        because the ``model_validator`` recomputes and re-verifies the digest.
        """
        try:
            digest = content_digest(fields.get("raw_payload"))
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, raw_content_digest=digest)

    # -- internal PIT classification (used by the guard) -------------------- #
    def _availability_status(self) -> str:
        if self.available_at is None:
            return "missing"
        if not _is_aware(self.available_at):
            return "naive"
        return "aware"

    def _safe_metadata(self) -> dict[str, Any]:
        """Digestable candidate *metadata* — never the raw bytes / naive datetime."""
        return {
            "raw_content_digest": self.raw_content_digest,
            "availability_status": self._availability_status(),
            "revision_id": self.revision_id,
            "required": self.required,
        }


# --------------------------------------------------------------------------- #
# FreshnessPolicy — versioned per-method/category, frozen-clock evaluated       #
# --------------------------------------------------------------------------- #
class FreshnessPolicy(DigestModel):
    """A closed, versioned per-method/category freshness policy (self-sealed).

    Exactly one threshold is set: an elapsed-duration ``max_elapsed_seconds`` *or* a
    trading-session ``max_trading_sessions``. A session-based policy carries the
    exact trading-calendar identity (``calendar_id`` + versioned
    ``calendar_material_ref``) it must be evaluated against; an elapsed-duration
    policy carries neither. Counts are strict non-negative ints (``bool`` rejected).
    There is deliberately **no** global ``MAX_STALE_DAYS`` constant and no
    unversioned module-global policy — a policy is selected per method/category and
    passed to :meth:`PitGuard.check_raw`. ``policy_digest`` seals the whole policy.
    """

    schema_version: Literal["1"] = "1"
    policy_id: NonEmptyStr
    policy_version: NonEmptyStr
    method_or_category: NonEmptyStr
    max_elapsed_seconds: NonNegativeInt | None = None
    max_trading_sessions: NonNegativeInt | None = None
    calendar_id: NonEmptyStr | None = None
    calendar_material_ref: ContentRef | None = None
    policy_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"policy_digest"})

    @property
    def is_session_based(self) -> bool:
        return self.max_trading_sessions is not None

    @model_validator(mode="after")
    def _verify(self) -> "FreshnessPolicy":
        session_based = self.max_trading_sessions is not None
        elapsed_based = self.max_elapsed_seconds is not None
        if session_based == elapsed_based:
            raise ValueError(
                "exactly one of max_elapsed_seconds / max_trading_sessions must be set"
            )
        if session_based:
            if self.calendar_id is None or self.calendar_material_ref is None:
                raise ValueError(
                    "a session-based freshness policy requires calendar_id and "
                    "calendar_material_ref"
                )
        else:
            if self.calendar_id is not None or self.calendar_material_ref is not None:
                raise ValueError(
                    "an elapsed-duration freshness policy must not carry a calendar identity"
                )
        if self.policy_digest != self.semantic_digest():
            raise ValueError("declared policy_digest does not match canonical digest")
        return self

    @property
    def rule_version(self) -> str:
        return f"{self.policy_id}@{self.policy_version}"

    @classmethod
    def build(cls, **fields: Any) -> "FreshnessPolicy":
        """Seal a policy: compute ``policy_digest`` from the reviewed field values."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, policy_digest=digest)


# --------------------------------------------------------------------------- #
# DataFetchRefusalDetails — the safe, typed audit detail (no raw bytes)         #
# --------------------------------------------------------------------------- #
class DataFetchRefusalDetails(DigestModel):
    """The safe, typed refusal-detail payload the guard hands the recorder.

    Binds the reason/stage plus the request / context / method / routing /
    snapshot-content / vintage identities, and — *according to how far execution
    reached* — the optional source / capability / candidate-metadata / PIT-audit
    identities. It contains **no** raw candidate bytes, credentials or physical
    paths (only digests and versioned refs). Its validator enforces the
    stage/optional-field matrix: a pre-invocation ``context_validation`` refusal
    forbids candidate/PIT facts, while a ``candidate_check`` refusal (future /
    missing / naive) requires both. ``detail_digest`` seals the whole detail.

    Registered by Task 5's audit-detail registry (never the Phase-1 default
    registry); Task 4 keeps it a pure value the in-memory spy observes.
    """

    schema_version: Literal["1"] = "1"
    reason_code: DataRefusalReason
    stage: DataRefusalStage
    request_digest: DigestHex
    request_context_digest: DigestHex
    method: NonEmptyStr
    routing_snapshot_digest: DigestHex
    data_snapshot_content_digest: DigestHex
    vintage_manifest_digest: DigestHex | None = None
    source_ref: ContentRef | None = None
    attempted_capability_ref: CapabilityRef | None = None
    candidate_metadata_digest: DigestHex | None = None
    pit_audit_digest: DigestHex | None = None
    detail_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"detail_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataFetchRefusalDetails":
        has_candidate = self.candidate_metadata_digest is not None
        has_pit = self.pit_audit_digest is not None
        if self.stage == "context_validation":
            if self.reason_code not in _CONTEXT_VALIDATION_REASONS:
                raise ValueError(
                    f"reason_code {self.reason_code!r} is not a context_validation reason"
                )
            if has_candidate or has_pit:
                raise ValueError(
                    "a context_validation (pre-invocation) refusal must not carry "
                    "candidate-metadata or PIT-audit facts"
                )
        else:  # candidate_check
            if self.reason_code not in _CANDIDATE_CHECK_REASONS:
                raise ValueError(
                    f"reason_code {self.reason_code!r} is not a candidate_check reason"
                )
            if not (has_candidate and has_pit):
                raise ValueError(
                    "a candidate_check refusal requires both candidate-metadata and "
                    "PIT-audit facts"
                )
        if self.detail_digest != self.semantic_digest():
            raise ValueError("declared detail_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "DataFetchRefusalDetails":
        """Seal a refusal detail: compute ``detail_digest`` from the field values."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, detail_digest=digest)


# --------------------------------------------------------------------------- #
# The refusal recorder protocol (an in-memory spy in Task 4)                    #
# --------------------------------------------------------------------------- #
@runtime_checkable
class DataRefusalRecorder(Protocol):
    """Records one refusal (typed detail + ordered evidence) before the guard raises.

    The recorder returns only *after* recording succeeds; if it fails, its exception
    propagates unmasked and the guard returns no data. It owns detail persistence
    and any ``EventRefusalRecord`` creation — the guard never constructs one. Task 4
    passes an in-memory spy; Task 6 binds this to the production
    ``EventRefusalAuditSink.record`` / ``CapabilityGateway.reject`` adapter.
    """

    def record_data_refusal(
        self,
        details: DataFetchRefusalDetails,
        evidence: tuple[NamedEvidenceDigest, ...],
        *,
        idempotency_key: str,
    ) -> None:
        ...


# --------------------------------------------------------------------------- #
# PitGuard — the context-bound point-in-time firewall                          #
# --------------------------------------------------------------------------- #
class PitGuard:
    """The context-bound point-in-time firewall.

    Built via :meth:`from_context`, which copies *only* intrinsic context facts
    (as-of, mode, backend, strictness, snapshot/vintage/routing digests) and binds
    the frozen clock reading and the exact trading calendar. It reads no wall clock
    and holds no global "today": ``as_of`` is the single frozen cutoff for both the
    PIT check and freshness.
    """

    __slots__ = ("_ctx", "_as_of", "_calendar", "_recorder", "_context_digest")

    def __init__(
        self,
        *,
        ctx: DataContext,
        as_of: datetime,
        calendar: TradingCalendar,
        recorder: DataRefusalRecorder,
        context_digest: str,
    ) -> None:
        self._ctx = ctx
        self._as_of = as_of
        self._calendar = calendar
        self._recorder = recorder
        self._context_digest = context_digest

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_context(
        cls,
        ctx: DataContext,
        *,
        clock: AuthoritativeClock,
        calendar: TradingCalendar,
        refusal_recorder: DataRefusalRecorder,
    ) -> "PitGuard":
        """Bind a guard to ``ctx``'s frozen as-of / mode / clock / calendar.

        Re-asserts the ``PIT_REPLAY`` intrinsic invariants (a defense-in-depth
        firewall that never trusts a mis-built context, even one bypassing the
        Phase-1 ``DataContext`` validator), requires the injected clock to read
        *exactly* the frozen ``ctx.as_of`` (no wall-clock drift, no global today),
        and requires the calendar to be the context's calendar. Copies no caller
        override.
        """
        cls._validate_replay_context(ctx)
        now = clock_now(clock)  # rejects a naive-returning clock
        if now != ctx.as_of:
            raise RoutingConfigurationError(
                "PitGuard must bind a frozen clock reading exactly the context as_of "
                f"({ctx.as_of.isoformat()}); got {now.isoformat()}"
            )
        if calendar.calendar_id != ctx.calendar_id:
            raise RoutingConfigurationError(
                f"PitGuard calendar {calendar.calendar_id!r} does not match the context "
                f"calendar {ctx.calendar_id!r}; there is no calendar fallback"
            )
        return cls(
            ctx=ctx,
            as_of=ctx.as_of,
            calendar=calendar,
            recorder=refusal_recorder,
            context_digest=content_digest(ctx),
        )

    @staticmethod
    def _validate_replay_context(ctx: DataContext) -> None:
        """Refuse a mis-built ``PIT_REPLAY`` context before any source call.

        ``PIT_REPLAY`` requires ``strict_pit=True``, a non-empty snapshot locator,
        a snapshot content digest, a vintage manifest digest and a non-LIVE backend.
        A LIVE backend is a structural fall-through (:class:`LiveFallbackRefused`);
        every other violation is a routing/config integrity fault. Both are
        raise-bucket errors — dispatch never catches them for fallback.
        """
        if ctx.mode is not DataMode.PIT_REPLAY:
            return
        if ctx.backend is DataBackend.LIVE:
            raise LiveFallbackRefused(
                "PIT_REPLAY may never bind a LIVE backend (no fall-through to LIVE)"
            )
        if not ctx.strict_pit:
            raise RoutingConfigurationError("PIT_REPLAY requires strict_pit=True")
        if not (ctx.data_snapshot_id or "").strip():
            raise RoutingConfigurationError("PIT_REPLAY requires a non-empty snapshot locator")
        if not (ctx.data_snapshot_content_digest or "").strip():
            raise RoutingConfigurationError("PIT_REPLAY requires a snapshot content digest")
        if ctx.vintage_manifest_digest is None:
            raise RoutingConfigurationError("PIT_REPLAY requires a vintage manifest digest")

    # -- the point-in-time check ------------------------------------------- #
    def check_raw(
        self,
        candidates: tuple[RawRowCandidate, ...],
        *,
        request_digest: DigestHex,
        request_context_digest: DigestHex,
        source_ref: ContentRef,
        freshness: FreshnessPolicy | None = None,
    ) -> tuple[tuple[RawRowCandidate, ...], PitAudit]:
        """Firewall a batch of raw candidates against the frozen ``as_of``.

        Verifies the supplied context digest, then classifies each candidate by its
        ``available_at``: a missing / naive availability raises
        :class:`MissingAvailabilityRefused`; an availability after ``as_of`` raises
        :class:`FutureDataRefused`; neither is filtered or fallback-eligible. If all
        candidates are visible and (when a policy is supplied) fresh, returns the
        exact ordered tuple plus a ``PitAudit(guard_result="passed")``. Data older
        than ``freshness`` allows raises :class:`StaleDataError`.
        """
        # 1) the request must have been built against THIS frozen context.
        if request_context_digest != self._context_digest:
            self._refuse_pre_invocation(
                reason_code="request_context_digest_mismatch",
                request_digest=request_digest,
                request_context_digest=request_context_digest,
                source_ref=source_ref,
                error=SnapshotMismatchError(
                    "request_context_digest does not match the guard's frozen "
                    "DataContext digest"
                ),
            )

        # 2) classify by available_at (missing/naive first — an unprovable
        #    availability cannot even be compared to the cutoff).
        missing = [c for c in candidates if c.available_at is None]
        naive = [c for c in candidates if c.available_at is not None and not _is_aware(c.available_at)]
        future = [
            c for c in candidates
            if c.available_at is not None and _is_aware(c.available_at)
            and c.available_at.astimezone(timezone.utc) > self._as_of
        ]

        if missing:
            self._refuse_candidates(
                reason_code="missing_availability",
                offending=tuple(missing),
                rows_seen=len(candidates),
                missing_rows=len(missing) + len(naive),
                future_rows=len(future),
                request_digest=request_digest,
                request_context_digest=request_context_digest,
                source_ref=source_ref,
                error=MissingAvailabilityRefused(
                    f"{len(missing)} required row(s) have no available_at; PIT cannot be proven"
                ),
            )
        if naive:
            self._refuse_candidates(
                reason_code="naive_availability",
                offending=tuple(naive),
                rows_seen=len(candidates),
                missing_rows=len(naive),
                future_rows=len(future),
                request_digest=request_digest,
                request_context_digest=request_context_digest,
                source_ref=source_ref,
                error=MissingAvailabilityRefused(
                    f"{len(naive)} row(s) have a naive (tz-less) available_at; PIT cannot be proven"
                ),
            )
        if future:
            self._refuse_candidates(
                reason_code="future_data",
                offending=tuple(future),
                rows_seen=len(candidates),
                missing_rows=0,
                future_rows=len(future),
                request_digest=request_digest,
                request_context_digest=request_context_digest,
                source_ref=source_ref,
                error=FutureDataRefused(
                    f"{len(future)} row(s) have available_at after the frozen as_of",
                    future_rows=len(future),
                ),
            )

        # 3) all visible — evaluate freshness against the frozen as_of/calendar.
        latest = self._latest_available(candidates)
        if freshness is not None and latest is not None:
            self._check_freshness(freshness, latest, rows_seen=len(candidates))

        # 4) passed — the exact ordered tuple + a coherent audit.
        audit = PitAudit(
            mode=self._ctx.mode,
            as_of=self._as_of,
            rows_seen=len(candidates),
            rows_returned=len(candidates),
            future_rows=0,
            missing_available_at_rows=0,
            guard_result="passed",
            latest_available_at=latest,
        )
        return tuple(candidates), audit

    # -- freshness ---------------------------------------------------------- #
    @staticmethod
    def _latest_available(candidates: tuple[RawRowCandidate, ...]) -> datetime | None:
        aware = [
            c.available_at.astimezone(timezone.utc)
            for c in candidates
            if c.available_at is not None and _is_aware(c.available_at)
        ]
        return max(aware) if aware else None

    def _check_freshness(
        self, policy: FreshnessPolicy, latest: datetime, *, rows_seen: int
    ) -> None:
        if policy.is_session_based:
            if (
                policy.calendar_id != self._calendar.calendar_id
                or policy.calendar_material_ref != self._calendar.material_ref
            ):
                raise SnapshotMismatchError(
                    "session-based freshness policy calendar does not match the guard's "
                    "trading calendar; there is no session-arithmetic fallback"
                )
            latest_date = latest.date()
            span = self._calendar.sessions_between(latest_date, self._as_of.date())
            sessions_since = span - (1 if self._calendar.is_session(latest_date) else 0)
            if sessions_since > policy.max_trading_sessions:
                raise StaleDataError(
                    f"data is {sessions_since} trading session(s) old; policy "
                    f"{policy.rule_version} allows {policy.max_trading_sessions}",
                    latest_available_at=latest,
                    pit_audit=self._stale_audit(rows_seen, latest),
                )
        else:
            elapsed = (self._as_of - latest).total_seconds()
            if elapsed > policy.max_elapsed_seconds:
                raise StaleDataError(
                    f"data is {elapsed:.0f}s old; policy {policy.rule_version} allows "
                    f"{policy.max_elapsed_seconds}s",
                    latest_available_at=latest,
                    pit_audit=self._stale_audit(rows_seen, latest),
                )

    def _stale_audit(self, rows_seen: int, latest: datetime) -> PitAudit:
        return PitAudit(
            mode=self._ctx.mode,
            as_of=self._as_of,
            rows_seen=rows_seen,
            rows_returned=0,
            future_rows=0,
            missing_available_at_rows=0,
            guard_result="refused",
            latest_available_at=latest,
        )

    # -- refusal helpers (record exactly once, then raise) ------------------ #
    def _refuse_pre_invocation(
        self,
        *,
        reason_code: str,
        request_digest: str,
        request_context_digest: str,
        source_ref: ContentRef,
        error: Exception,
    ) -> None:
        details = DataFetchRefusalDetails.build(
            reason_code=reason_code,
            stage="context_validation",
            request_digest=request_digest,
            request_context_digest=request_context_digest,
            method=source_ref.id,
            routing_snapshot_digest=self._ctx.routing_snapshot_digest,
            data_snapshot_content_digest=self._ctx.data_snapshot_content_digest,
            vintage_manifest_digest=self._ctx.vintage_manifest_digest,
            source_ref=source_ref,
        )
        evidence = self._evidence(
            request_digest=request_digest,
            source_ref=source_ref,
            candidate_metadata_digest=None,
            pit_audit_digest=None,
        )
        self._record_and_raise(details, evidence, error)

    def _refuse_candidates(
        self,
        *,
        reason_code: str,
        offending: tuple[RawRowCandidate, ...],
        rows_seen: int,
        missing_rows: int,
        future_rows: int,
        request_digest: str,
        request_context_digest: str,
        source_ref: ContentRef,
        error: Exception,
    ) -> None:
        audit = PitAudit(
            mode=self._ctx.mode,
            as_of=self._as_of,
            rows_seen=rows_seen,
            rows_returned=0,
            future_rows=future_rows,
            missing_available_at_rows=missing_rows,
            guard_result="refused",
            latest_available_at=None,
        )
        candidate_metadata_digest = content_digest([c._safe_metadata() for c in offending])
        pit_audit_digest = content_digest(audit)
        details = DataFetchRefusalDetails.build(
            reason_code=reason_code,
            stage="candidate_check",
            request_digest=request_digest,
            request_context_digest=request_context_digest,
            method=source_ref.id,
            routing_snapshot_digest=self._ctx.routing_snapshot_digest,
            data_snapshot_content_digest=self._ctx.data_snapshot_content_digest,
            vintage_manifest_digest=self._ctx.vintage_manifest_digest,
            source_ref=source_ref,
            candidate_metadata_digest=candidate_metadata_digest,
            pit_audit_digest=pit_audit_digest,
        )
        evidence = self._evidence(
            request_digest=request_digest,
            source_ref=source_ref,
            candidate_metadata_digest=candidate_metadata_digest,
            pit_audit_digest=pit_audit_digest,
        )
        self._record_and_raise(details, evidence, error)

    def _evidence(
        self,
        *,
        request_digest: str,
        source_ref: ContentRef | None,
        candidate_metadata_digest: str | None,
        pit_audit_digest: str | None,
    ) -> tuple[NamedEvidenceDigest, ...]:
        items = [
            NamedEvidenceDigest(name="data_context", digest=self._context_digest),
            NamedEvidenceDigest(name="request", digest=request_digest),
        ]
        if source_ref is not None:
            items.append(NamedEvidenceDigest(name="source_ref", digest=source_ref.content_digest))
        if candidate_metadata_digest is not None:
            items.append(
                NamedEvidenceDigest(name="candidate_metadata", digest=candidate_metadata_digest)
            )
        if pit_audit_digest is not None:
            items.append(NamedEvidenceDigest(name="pit_audit", digest=pit_audit_digest))
        items.sort(key=lambda e: e.name)  # EventRefusalRecord requires canonical order
        return tuple(items)

    def _record_and_raise(
        self,
        details: DataFetchRefusalDetails,
        evidence: tuple[NamedEvidenceDigest, ...],
        error: Exception,
    ) -> None:
        """Record the refusal exactly once (waiting for success) then raise.

        A deterministic idempotency key (the detail's own sealed digest) makes an
        identical refusal deduplicate downstream. If the recorder raises, that
        failure propagates unmasked — the guard never swallows it and never returns
        data.
        """
        idempotency_key = f"data-refusal:{details.detail_digest}"
        self._recorder.record_data_refusal(details, evidence, idempotency_key=idempotency_key)
        raise error
