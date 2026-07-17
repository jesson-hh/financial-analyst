"""Typed ``DataResult[T]`` status envelope + PIT record contracts.

This module freezes the boundary between a data *fetch* and everything
downstream that reads it. A :class:`DataResult` never lets a caller mistake an
absence for a value: only ``OK``/``DEGRADED`` carry typed, digest-verified data;
every other status carries *no* consumable payload at all. The design decisions
locked here (and by ``tests/orchestration/test_data_result.py``):

Semantic vs audit identity
--------------------------
Every contract is a :class:`~guanlan_v2.orchestration.digest.DigestModel`, so it
participates in a parent's canonical semantic/audit digest. The reviewed split:

* a :class:`SourceAttempt`'s ``vendor`` / ``subsource`` / ``configured`` /
  ``outcome`` / ``fallback_reason`` are *semantic* (the chain of what was tried
  and how it turned out is content), but its ``started_at`` / ``finished_at``
  wall-clock is *audit-only* (``SEMANTIC_EXCLUDE``). Because a nested
  ``DigestModel`` applies its own projection, an attempt's timestamps can never
  leak into the parent :class:`DataResult`'s semantic digest — re-running the
  same fetch a minute later yields the same semantic digest but a different audit
  digest, while reordering the attempts or changing an outcome does change the
  semantic digest.
* a :class:`DataResult`'s ``fetched_at`` is likewise audit-only; its PIT data
  times (``effective_at`` / ``available_at`` / ``ingested_at``) are *semantic*
  content, not scheduling noise.

Self-sealed digests
-------------------
A :class:`PitRecord` seals its own payload (``content_digest`` in
``SELF_DIGEST_FIELDS``); a :class:`DataResult` seals *both* a semantic
``content_digest`` and a full ``audit_digest``. Use the pure builders
:meth:`PitRecord.build` / :meth:`DataResult.build`, which compute the declared
digests from the field values; a persisted record with a mismatched declared
digest is rejected on load by the ``model_validator``. ``data_content_digest`` is
the digest *of the data payload* — present and verified for ``OK``/``DEGRADED``,
``None`` for every no-data status — so "carries no consumable data" is a
structural property, not a convention.
"""
from __future__ import annotations

from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataMode, DataStatus

__all__ = [
    "AttemptOutcome",
    "GuardResult",
    "SourceAttempt",
    "PitAudit",
    "PitRecord",
    "DataResult",
    "row_time_metadata_digest_of",
]

T = TypeVar("T")

#: Closed outcome vocabulary for a single vendor/subsource attempt.
AttemptOutcome = Literal[
    "success",
    "no_data",
    "stale",
    "rate_limited",
    "not_configured",
    "future_refused",
    "missing_availability",
    "error",
]

#: Closed PIT-guard verdict vocabulary.
GuardResult = Literal["passed", "filtered", "refused"]

#: Statuses that carry typed, consumable data.
_DATA_BEARING: frozenset[DataStatus] = frozenset({DataStatus.OK, DataStatus.DEGRADED})

#: Well-formed but deliberately-wrong digest used by ``build`` when the field
#: values are malformed: it forces the validating constructor to surface the real
#: ``ValidationError`` and can never seal a valid record (the digest is re-verified).
_DIGEST_PLACEHOLDER = "0" * 64


class SourceAttempt(DigestModel):
    """One vendor/subsource fetch attempt in a resolved fallback chain.

    ``vendor`` / ``subsource`` / ``configured`` / ``outcome`` / ``fallback_reason``
    are semantic (the attempt trail is content and its order matters);
    ``started_at`` / ``finished_at`` are audit-only wall-clock, excluded from the
    semantic projection so they never leak into a parent's semantic digest.
    """

    schema_version: Literal["1"] = "1"
    vendor: NonEmptyStr
    subsource: NonEmptyStr | None = None
    configured: bool
    outcome: AttemptOutcome
    fallback_reason: NonEmptyStr | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"started_at", "finished_at"})

    @model_validator(mode="after")
    def _clock_coherent(self) -> "SourceAttempt":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class PitAudit(DigestModel):
    """Point-in-time guard accounting for one fetch.

    Row counts are strict non-negative and internally coherent: ``rows_returned``,
    ``future_rows`` and ``missing_available_at_rows`` can never exceed
    ``rows_seen``. The honesty invariant: a replay whose window saw *future* or
    *missing-availability* rows can never claim ``guard_result="passed"`` — those
    rows must have been ``filtered`` or the whole read ``refused``.
    """

    schema_version: Literal["1"] = "1"
    mode: DataMode
    as_of: UtcDateTime
    rows_seen: NonNegativeInt
    rows_returned: NonNegativeInt
    future_rows: NonNegativeInt
    missing_available_at_rows: NonNegativeInt
    guard_result: GuardResult
    latest_available_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _counts_coherent(self) -> "PitAudit":
        if self.rows_returned > self.rows_seen:
            raise ValueError("rows_returned cannot exceed rows_seen")
        if self.future_rows > self.rows_seen:
            raise ValueError("future_rows cannot exceed rows_seen")
        if self.missing_available_at_rows > self.rows_seen:
            raise ValueError("missing_available_at_rows cannot exceed rows_seen")
        if self.guard_result == "passed" and (self.future_rows or self.missing_available_at_rows):
            raise ValueError(
                "guard_result='passed' is impossible with future or "
                "missing-availability rows; they must be filtered or refused"
            )
        return self


class PitRecord(DigestModel):
    """Immutable base for every multi-row / multi-vintage data item.

    A concrete payload schema subclasses this and adds its own payload fields;
    the inherited ``content_digest`` self-seals the row (subtype fields + PIT
    times) via :meth:`build`. A plain ``dict`` therefore can never stand in for a
    PIT-compliant row — only a real registered subtype produces a verifiable
    ``content_digest``.
    """

    schema_version: Literal["1"] = "1"
    effective_at: UtcDateTime | None = None
    available_at: UtcDateTime
    ingested_at: UtcDateTime
    revision_id: NonEmptyStr | None = None
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify_content_digest(self) -> "PitRecord":
        expected = self.semantic_digest()
        if self.content_digest != expected:
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "PitRecord":
        """Seal a row: compute its ``content_digest`` from the field values.

        If the field values are malformed (missing a required PIT time, a
        non-finite payload number, …) digest projection cannot run; we fall back
        to a placeholder and let the validating constructor raise the precise
        ``ValidationError``. A placeholder can never seal a real record because
        the ``model_validator`` recomputes and re-verifies the digest on load.
        """
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


def row_time_metadata_digest_of(data: Any) -> DigestHex | None:
    """Pure derivation of a payload's row-time-metadata digest (or ``None``).

    Derivable payload shapes — the three PIT-row-bearing families used across the
    codebase: a single :class:`PitRecord`, a ``list``/``tuple`` of
    :class:`PitRecord`\\ s, or a batch model exposing ``rows`` as a tuple of
    :class:`PitRecord`\\ s (the Phase-3 concrete ``*Rows`` batches). The digest
    covers each row's ordered PIT time metadata (``effective_at`` /
    ``available_at`` / ``ingested_at`` / ``revision_id``), so tampering with any
    row's time metadata — or reordering rows — changes it. Any other payload
    (or no payload) yields ``None``, and the :class:`DataResult` validator then
    *forbids* a declared digest, so an unverifiable caller-supplied digest can
    never survive.
    """
    if data is None:
        return None
    rows: tuple[PitRecord, ...] | None = None
    if isinstance(data, PitRecord):
        rows = (data,)
    elif isinstance(data, (list, tuple)) and all(isinstance(r, PitRecord) for r in data):
        rows = tuple(data)
    else:
        maybe = getattr(data, "rows", None)
        if isinstance(maybe, (list, tuple)) and all(isinstance(r, PitRecord) for r in maybe):
            rows = tuple(maybe)
    if rows is None:
        return None
    return content_digest(
        [
            {
                "effective_at": r.effective_at,
                "available_at": r.available_at,
                "ingested_at": r.ingested_at,
                "revision_id": r.revision_id,
            }
            for r in rows
        ]
    )


class DataResult(DigestModel, Generic[T]):
    """Strict typed status envelope for one resolved data fetch.

    Only ``OK``/``DEGRADED`` carry a typed ``data`` payload and a
    ``data_content_digest`` (verified against that payload); every other status
    carries neither. ``DEGRADED`` additionally requires ``coverage`` in ``[0, 1]``
    and a non-empty ``degradation_reason``; ``coverage`` and
    ``degradation_reason`` are *only* valid for ``DEGRADED`` (a ``NO_DATA``
    envelope can never seal a misleading coverage fraction).
    ``row_time_metadata_digest`` is never caller-asserted: it is derived from the
    payload's PIT rows by :func:`row_time_metadata_digest_of` (computed by
    :meth:`build`, re-verified on load) and must be ``None`` when the payload
    carries no derivable rows — including every no-data status. The semantic
    ``content_digest`` covers the data, PIT metadata, resolved chain/config and
    attempt outcome order but excludes volatile wall-clock (``fetched_at`` and
    each attempt's timestamps); the full ``audit_digest`` includes them. Both are
    self-sealed and re-verified on load — use :meth:`build`.
    """

    schema_version: Literal["1"] = "1"
    id: NonEmptyStr
    method: NonEmptyStr
    request_digest: DigestHex

    status: DataStatus
    data: T | None = None
    data_content_digest: DigestHex | None = None
    coverage: FiniteFloat | None = Field(default=None, ge=0, le=1)
    degradation_reason: NonEmptyStr | None = None

    vendor: NonEmptyStr | None = None
    subsource: NonEmptyStr | None = None
    resolved_vendor_chain: tuple[NonEmptyStr, ...]
    source_config_digest: DigestHex

    effective_at: UtcDateTime | None = None
    available_at: UtcDateTime | None = None
    ingested_at: UtcDateTime | None = None
    fetched_at: UtcDateTime
    revision_id: NonEmptyStr | None = None

    row_time_metadata_digest: DigestHex | None = None
    content_digest: DigestHex
    audit_digest: DigestHex

    attempts: tuple[SourceAttempt, ...]
    pit_audit: PitAudit
    warnings: tuple[NonEmptyStr, ...] = ()
    badges: tuple[NonEmptyStr, ...] = ()

    #: ``fetched_at`` is the only DataResult-level wall-clock; audit-only.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"fetched_at"})
    #: both self-declared digests are excluded from *both* projections.
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest", "audit_digest"})

    @model_validator(mode="after")
    def _verify_and_check_invariants(self) -> "DataResult[T]":
        # 1) declared digests must equal the recomputed canonical digests.
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        if self.audit_digest != self.audit_digest_value():
            raise ValueError("declared audit_digest does not match canonical audit digest")

        # 2) status / data matrix.
        if self.status in _DATA_BEARING:
            if self.data is None:
                raise ValueError(f"status={self.status.value} requires typed data")
            if self.data_content_digest != content_digest(self.data):
                raise ValueError("data_content_digest does not match the data payload")
        else:
            if self.data is not None:
                raise ValueError(f"status={self.status.value} must not carry consumable data")
            if self.data_content_digest is not None:
                raise ValueError(f"status={self.status.value} must not carry a data content digest")

        # 3) degraded coverage / reason matrix (symmetric: DEGRADED-only).
        if self.status is DataStatus.DEGRADED:
            if self.coverage is None:
                raise ValueError("DEGRADED requires coverage in [0, 1]")
            if self.degradation_reason is None:
                raise ValueError("DEGRADED requires a non-empty degradation_reason")
        else:
            if self.degradation_reason is not None:
                raise ValueError("degradation_reason is only valid for DEGRADED status")
            if self.coverage is not None:
                raise ValueError("coverage is only valid for DEGRADED status")

        # 4) row-time metadata digest: derived from the payload's PIT rows,
        # never caller-asserted; None whenever no rows are derivable.
        expected_row_time = row_time_metadata_digest_of(self.data)
        if self.row_time_metadata_digest != expected_row_time:
            if expected_row_time is None:
                raise ValueError(
                    "row_time_metadata_digest must be None when the payload carries "
                    "no derivable PIT row time metadata"
                )
            raise ValueError(
                "declared row_time_metadata_digest does not match the digest derived "
                "from the payload's row time metadata"
            )
        return self

    @classmethod
    def build(cls, **fields: Any) -> "DataResult[T]":
        """Seal a result: compute ``data_content_digest``,
        ``row_time_metadata_digest`` and the semantic / audit digests from the
        field values, then construct (and re-verify).

        ``data_content_digest`` / ``row_time_metadata_digest`` are computed from
        ``data`` unless explicitly provided (a persisted record round-trips them
        through direct construction; an explicitly-supplied wrong value is
        rejected by the validator, never trusted).
        Malformed field values make digest projection raise; we then fall back to
        placeholder digests so the validating constructor raises the precise
        ``ValidationError``. Placeholders can never seal a real record because the
        ``model_validator`` recomputes and re-verifies both digests on load.
        """
        sealed = dict(fields)
        if "data_content_digest" not in sealed:
            data = sealed.get("data")
            sealed["data_content_digest"] = content_digest(data) if data is not None else None
        if "row_time_metadata_digest" not in sealed:
            sealed["row_time_metadata_digest"] = row_time_metadata_digest_of(sealed.get("data"))
        try:
            content = cls.digest_of_fields(projection="semantic", **sealed)
            audit = cls.digest_of_fields(projection="audit", **sealed)
        except (ValueError, TypeError, AttributeError, KeyError):
            content = audit = _DIGEST_PLACEHOLDER
        return cls(**sealed, content_digest=content, audit_digest=audit)
