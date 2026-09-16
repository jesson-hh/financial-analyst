# -*- coding: utf-8 -*-
"""Durable, assignment-scoped authorization for unattended watch deep runs.

This is deliberately a Seats-domain control plane, separate from the generic
orchestration approval journal.  A grant is useful only for one exact stock,
assignment revision, trading session and sealed preset record.  Mutations are
append-only and every mutation replays the journal while holding the lock for
its resolved path; separate store instances in different request/worker
threads therefore cannot oversubscribe a grant or the process-wide daily cap.
This is intentionally a **single-process** guarantee.  There is no OS file lock;
two independent Python processes must not mutate the same journal.

An unresolved reservation remains charged after restart.  This is intentional:
after a crash the store cannot prove that no model call happened, so silently
returning capacity could run the same deterministic deep ``run_id`` twice.
Call ``release`` only for a known pre-execution refusal, and ``settle`` after a
runner was entered (including failed runs).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from guanlan_v2.seats.watch_assignments import normalize_code


__all__ = [
    "WATCH_DEEP_AUTHORIZATIONS_PATH",
    "DEFAULT_GLOBAL_DAILY_MAX_RUNS",
    "WatchDeepAuthorizationError",
    "WatchDeepAuthorizationStoreCorrupt",
    "WatchDeepAuthorizationConflict",
    "WatchDeepReservationConflict",
    "WatchDeepAuthorization",
    "WatchDeepAuthorizationView",
    "WatchDeepReservation",
    "WatchDeepRunView",
    "WatchDeepGlobalBudgetView",
    "WatchDeepGlobalConcurrencyView",
    "WatchDeepAuthorizationStore",
]


VAR = Path(os.environ.get("GUANLAN_SEATS_VAR_ROOT") or (
    Path(__file__).resolve().parents[2] / "var"
))
WATCH_DEEP_AUTHORIZATIONS_PATH = VAR / "watch_deep_authorizations.jsonl"
DEFAULT_GLOBAL_DAILY_MAX_RUNS = 2
HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ROW_KINDS = frozenset({
    "authorization_issued",
    "authorization_revoked",
    "run_reserved",
    "run_released",
    "run_settled",
})

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


class WatchDeepAuthorizationError(Exception):
    """Base class for a refused watch-deep authorization operation."""


class WatchDeepAuthorizationStoreCorrupt(WatchDeepAuthorizationError):
    """The append-only journal cannot be trusted and is refused fail-closed."""


class WatchDeepAuthorizationConflict(WatchDeepAuthorizationError):
    """A conflicting active authorization already owns the same assignment scope."""


class WatchDeepReservationConflict(WatchDeepAuthorizationError):
    """A run reservation was reused with different binding or terminal data."""


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _as_hkt(value: datetime | str | None, *, field: str,
            default: Callable[[], datetime] | None = None) -> datetime:
    if value is None:
        if default is None:
            raise ValueError(f"{field} is required")
        value = default()
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO datetime") from exc
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=HKT)
    return value.astimezone(HKT)


def _as_date(value: date | str, *, field: str = "session_date") -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"{field} must be YYYY-MM-DD") from exc
    if not isinstance(value, date):
        raise ValueError(f"{field} must be a date")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if out < 1 or out != value:
        raise ValueError(f"{field} must be a positive integer")
    return out


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if out < 0 or out != value:
        raise ValueError(f"{field} must be a non-negative integer")
    return out


def _text(value: Any, *, field: str, max_len: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    out = value.strip()
    if len(out) > max_len:
        raise ValueError(f"{field} is too long")
    return out


def _preset_digest(value: Any) -> str:
    out = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(out):
        raise ValueError("preset_record_digest must be a 64-character hex digest")
    return out


def _iso(value: datetime) -> str:
    return value.isoformat()


@dataclass(frozen=True)
class WatchDeepAuthorization:
    authorization_id: str
    code: str
    assignment_revision: int
    session_date: str
    preset_id: str
    preset_record_digest: str
    valid_from: datetime
    valid_until: datetime
    max_runs: int
    max_llm_invocations: int
    issued_by: str
    issued_at: datetime
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "code": self.code,
            "assignment_revision": self.assignment_revision,
            "session_date": self.session_date,
            "preset_id": self.preset_id,
            "preset_record_digest": self.preset_record_digest,
            "valid_from": _iso(self.valid_from),
            "valid_until": _iso(self.valid_until),
            "max_runs": self.max_runs,
            "max_llm_invocations": self.max_llm_invocations,
            "issued_by": self.issued_by,
            "issued_at": _iso(self.issued_at),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WatchDeepAuthorizationView:
    authorization: WatchDeepAuthorization
    status: Literal["not_yet_valid", "active", "expired", "exhausted", "revoked"]
    terminal_reason: Optional[str]
    runs_used: int
    runs_remaining: int
    llm_invocations_used: int
    llm_invocations_remaining: int
    revoked_by: Optional[str] = None
    revoked_at: Optional[datetime] = None
    revocation_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out = self.authorization.to_dict()
        out.update({
            "status": self.status,
            "terminal_reason": self.terminal_reason,
            "runs_used": self.runs_used,
            "runs_remaining": self.runs_remaining,
            "llm_invocations_used": self.llm_invocations_used,
            "llm_invocations_remaining": self.llm_invocations_remaining,
            "revoked_by": self.revoked_by,
            "revoked_at": _iso(self.revoked_at) if self.revoked_at else None,
            "revocation_reason": self.revocation_reason,
        })
        return out


@dataclass(frozen=True)
class WatchDeepReservation:
    reservation_id: str
    authorization_id: str
    run_id: str
    code: str
    assignment_revision: int
    session_date: str
    preset_id: str
    preset_record_digest: str
    requested_llm_invocations: int
    reserved_at: datetime
    # False means this deterministic run id was already journaled.  Callers
    # must not enter the model runner again on such an idempotent replay.
    newly_reserved: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "authorization_id": self.authorization_id,
            "run_id": self.run_id,
            "code": self.code,
            "assignment_revision": self.assignment_revision,
            "session_date": self.session_date,
            "preset_id": self.preset_id,
            "preset_record_digest": self.preset_record_digest,
            "requested_llm_invocations": self.requested_llm_invocations,
            "reserved_at": _iso(self.reserved_at),
            "newly_reserved": self.newly_reserved,
        }


@dataclass(frozen=True)
class WatchDeepRunView:
    reservation: WatchDeepReservation
    status: Literal["reserved", "released", "settled"]
    actual_llm_invocations: Optional[int] = None
    outcome: Optional[str] = None
    detail: Optional[str] = None
    terminal_at: Optional[datetime] = None
    llm_overrun: bool = False
    llm_overrun_by: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = self.reservation.to_dict()
        out.pop("newly_reserved", None)
        out.update({
            "status": self.status,
            "actual_llm_invocations": self.actual_llm_invocations,
            "outcome": self.outcome,
            "detail": self.detail,
            "terminal_at": _iso(self.terminal_at) if self.terminal_at else None,
            "llm_overrun": self.llm_overrun,
            "llm_overrun_by": self.llm_overrun_by,
        })
        return out


@dataclass(frozen=True)
class WatchDeepGlobalBudgetView:
    session_date: str
    max_runs: int
    runs_used: int
    runs_remaining: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "max_runs": self.max_runs,
            "runs_used": self.runs_used,
            "runs_remaining": self.runs_remaining,
        }


@dataclass(frozen=True)
class WatchDeepGlobalConcurrencyView:
    max_runs: int
    runs_in_flight: int
    runs_available: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_runs": self.max_runs,
            "runs_in_flight": self.runs_in_flight,
            "runs_available": self.runs_available,
        }


@dataclass
class _Folded:
    seq: int
    last_digest: str
    authorizations: dict[str, WatchDeepAuthorization]
    authorization_order: list[str]
    revocations: dict[str, dict[str, Any]]
    reservations: dict[str, WatchDeepReservation]
    run_ids: dict[str, str]
    run_order: list[str]
    releases: dict[str, dict[str, Any]]
    settlements: dict[str, dict[str, Any]]


def _empty_fold() -> _Folded:
    return _Folded(
        seq=0, last_digest="", authorizations={}, authorization_order=[],
        revocations={}, reservations={}, run_ids={}, run_order=[], releases={},
        settlements={},
    )


class WatchDeepAuthorizationStore:
    """Append-only watch authorization store with path-wide thread serialization."""

    def __init__(self, path: Path | str | None = None, *,
                 global_daily_max_runs: int = DEFAULT_GLOBAL_DAILY_MAX_RUNS,
                 global_max_concurrent_runs: int = 1,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path) if path is not None else WATCH_DEEP_AUTHORIZATIONS_PATH
        self.global_daily_max_runs = _positive_int(
            global_daily_max_runs, field="global_daily_max_runs")
        self.global_max_concurrent_runs = _positive_int(
            global_max_concurrent_runs, field="global_max_concurrent_runs")
        self._clock = clock or (lambda: datetime.now(HKT))
        self._lock = _path_lock(self.path)

    def now(self) -> datetime:
        return _as_hkt(self._clock(), field="clock")

    def get(self, *, code: str, assignment_revision: int,
            session_date: date | str | None = None,
            now: datetime | None = None) -> WatchDeepAuthorizationView | None:
        canonical = normalize_code(code)
        revision = _positive_int(assignment_revision, field="assignment_revision")
        instant = _as_hkt(now, field="now", default=self._clock)
        session = _as_date(session_date or instant.date()).isoformat()
        with self._lock:
            folded = self._fold()
            for authorization_id in reversed(folded.authorization_order):
                auth = folded.authorizations[authorization_id]
                if (auth.code == canonical and auth.assignment_revision == revision
                        and auth.session_date == session):
                    return self._authorization_view(folded, auth, now=instant)
        return None

    def get_by_id(self, authorization_id: str, *,
                  now: datetime | None = None) -> WatchDeepAuthorizationView | None:
        auth_id = _text(authorization_id, field="authorization_id", max_len=64)
        instant = _as_hkt(now, field="now", default=self._clock)
        with self._lock:
            folded = self._fold()
            auth = folded.authorizations.get(auth_id)
            return self._authorization_view(folded, auth, now=instant) if auth else None

    def issue(self, *, code: str, assignment_revision: int,
              session_date: date | str, preset_id: str,
              preset_record_digest: str, valid_from: datetime,
              valid_until: datetime, max_runs: int = 1,
              max_llm_invocations: int = 8, issued_by: str,
              reason: str) -> WatchDeepAuthorizationView:
        canonical = normalize_code(code)
        revision = _positive_int(assignment_revision, field="assignment_revision")
        session = _as_date(session_date).isoformat()
        preset = _text(preset_id, field="preset_id", max_len=200)
        record_digest = _preset_digest(preset_record_digest)
        start = _as_hkt(valid_from, field="valid_from")
        end = _as_hkt(valid_until, field="valid_until")
        run_cap = _positive_int(max_runs, field="max_runs")
        llm_cap = _positive_int(max_llm_invocations, field="max_llm_invocations")
        actor = _text(issued_by, field="issued_by", max_len=200)
        why = _text(reason, field="reason")
        issued_at = self.now()
        session_day = date.fromisoformat(session)
        if start.date() != session_day or end.date() != session_day:
            raise ValueError("validity window must stay inside session_date")
        if end <= start:
            raise ValueError("valid_until must be after valid_from")
        if end <= issued_at:
            raise ValueError("cannot issue an already-expired authorization")

        with self._lock:
            folded = self._fold()
            # The one-run product promise belongs to the assignment/session
            # scope, not to an individual authorization id.  Once any grant for
            # this exact scope reached admission, exhaustion/revocation must not
            # be bypassable by minting another grant and choosing a fresh run id.
            # A known pre-execution release is the sole exception because it
            # proves the reserved runner was never entered.
            if self._scope_consumed(
                    folded, code=canonical, assignment_revision=revision,
                    session_date=session):
                raise WatchDeepAuthorizationConflict(
                    "this code/assignment revision/session already has a "
                    "non-released deep admission; it is permanently closed "
                    "to re-issuance")

            for old_id in reversed(folded.authorization_order):
                old = folded.authorizations[old_id]
                if (old.code == canonical and old.assignment_revision == revision
                        and old.session_date == session):
                    old_view = self._authorization_view(folded, old, now=issued_at)
                    if old_view.status in ("active", "not_yet_valid"):
                        same_envelope = (
                            old.preset_id == preset
                            and old.preset_record_digest == record_digest
                            and old.valid_until == end
                            and old.max_runs == run_cap
                            and old.max_llm_invocations == llm_cap
                            and old.issued_by == actor
                        )
                        if same_envelope:
                            return old_view
                        raise WatchDeepAuthorizationConflict(
                            "an active authorization already exists for this "
                            "code/assignment revision/session; revoke it before replacing it")

            # A revoked-before-consume grant may be safely replaced.  A frozen
            # test clock (or two same-microsecond calls) could otherwise derive
            # the revoked authorization's exact id again, so advance only the
            # audit timestamp until the new append has a distinct content id.
            while True:
                if end <= issued_at:
                    raise ValueError("cannot issue an already-expired authorization")
                fields = {
                    "code": canonical,
                    "assignment_revision": revision,
                    "session_date": session,
                    "preset_id": preset,
                    "preset_record_digest": record_digest,
                    "valid_from": _iso(start),
                    "valid_until": _iso(end),
                    "max_runs": run_cap,
                    "max_llm_invocations": llm_cap,
                    "issued_by": actor,
                    "issued_at": _iso(issued_at),
                    "reason": why,
                }
                authorization_id = _digest(
                    {"kind": "watch_deep_authorization", **fields})
                if authorization_id not in folded.authorizations:
                    break
                issued_at += timedelta(microseconds=1)
            auth = WatchDeepAuthorization(authorization_id=authorization_id, **{
                **fields, "valid_from": start, "valid_until": end,
                "issued_at": issued_at,
            })
            self._append(folded, "authorization_issued", auth.to_dict())
            folded.authorizations[authorization_id] = auth
            folded.authorization_order.append(authorization_id)
            return self._authorization_view(folded, auth, now=issued_at)

    def revoke(self, authorization_id: str, *, revoked_by: str, reason: str,
               revoked_at: datetime | None = None) -> WatchDeepAuthorizationView:
        auth_id = _text(authorization_id, field="authorization_id", max_len=64)
        actor = _text(revoked_by, field="revoked_by", max_len=200)
        why = _text(reason, field="reason")
        instant = _as_hkt(revoked_at, field="revoked_at", default=self._clock)
        with self._lock:
            folded = self._fold()
            auth = folded.authorizations.get(auth_id)
            if auth is None:
                raise WatchDeepAuthorizationConflict("unknown authorization_id")
            if instant < auth.issued_at:
                raise ValueError("revoked_at cannot precede issued_at")
            if auth_id not in folded.revocations:
                payload = {
                    "authorization_id": auth_id,
                    "revoked_by": actor,
                    "revoked_at": _iso(instant),
                    "reason": why,
                }
                self._append(folded, "authorization_revoked", payload)
                folded.revocations[auth_id] = payload
            return self._authorization_view(folded, auth, now=instant)

    def consume(self, *, run_id: str, code: str, assignment_revision: int,
                session_date: date | str, preset_id: str,
                preset_record_digest: str,
                requested_llm_invocations: int = 8,
                now: datetime | None = None) -> WatchDeepReservation | None:
        run = _text(run_id, field="run_id", max_len=240)
        canonical = normalize_code(code)
        revision = _positive_int(assignment_revision, field="assignment_revision")
        session = _as_date(session_date).isoformat()
        preset = _text(preset_id, field="preset_id", max_len=200)
        record_digest = _preset_digest(preset_record_digest)
        requested = _positive_int(
            requested_llm_invocations, field="requested_llm_invocations")
        instant = _as_hkt(now, field="now", default=self._clock)

        with self._lock:
            folded = self._fold()
            existing_id = folded.run_ids.get(run)
            if existing_id is not None:
                existing = folded.reservations[existing_id]
                if not (
                    existing.code == canonical
                    and existing.assignment_revision == revision
                    and existing.session_date == session
                    and existing.preset_id == preset
                    and existing.preset_record_digest == record_digest
                    and existing.requested_llm_invocations == requested
                ):
                    raise WatchDeepReservationConflict(
                        "run_id already exists with a different authorization binding")
                if existing_id not in folded.releases:
                    # An unresolved or settled deterministic run is never
                    # re-entered. Concurrent callers therefore observe exactly
                    # one newly_reserved=True owner.
                    return replace(existing, newly_reserved=False)
                # A release proves the runner was never entered. The SAME
                # deterministic identity may safely retry after a transient
                # pre-capture/pre-admission refusal; keep audit time monotonic
                # even under a frozen test clock.
                released_at = _as_hkt(
                    folded.releases[existing_id]["released_at"],
                    field="released_at")
                if instant <= released_at:
                    instant = released_at + timedelta(microseconds=1)

            global_view = self._global_budget_view(folded, session)
            if global_view.runs_remaining < 1:
                return None
            if self._global_concurrency_view(folded).runs_available < 1:
                return None

            chosen: WatchDeepAuthorization | None = None
            for auth_id in reversed(folded.authorization_order):
                auth = folded.authorizations[auth_id]
                if not (
                    auth.code == canonical
                    and auth.assignment_revision == revision
                    and auth.session_date == session
                    and auth.preset_id == preset
                    and auth.preset_record_digest == record_digest
                ):
                    continue
                view = self._authorization_view(folded, auth, now=instant)
                if (view.status == "active" and view.runs_remaining >= 1
                        and view.llm_invocations_remaining >= requested):
                    chosen = auth
                    break
            if chosen is None:
                return None

            reservation_fields = {
                "authorization_id": chosen.authorization_id,
                "run_id": run,
                "code": canonical,
                "assignment_revision": revision,
                "session_date": session,
                "preset_id": preset,
                "preset_record_digest": record_digest,
                "requested_llm_invocations": requested,
                "reserved_at": _iso(instant),
            }
            reservation_id = _digest({"kind": "watch_deep_reservation",
                                      **reservation_fields})
            reservation = WatchDeepReservation(
                reservation_id=reservation_id,
                authorization_id=chosen.authorization_id,
                run_id=run,
                code=canonical,
                assignment_revision=revision,
                session_date=session,
                preset_id=preset,
                preset_record_digest=record_digest,
                requested_llm_invocations=requested,
                reserved_at=instant,
                newly_reserved=True,
            )
            self._append(folded, "run_reserved", reservation.to_dict())
            folded.reservations[reservation_id] = reservation
            folded.run_ids[run] = reservation_id
            folded.run_order.append(reservation_id)
            return reservation

    def release(self, reservation_id: str, *, reason: str,
                released_at: datetime | None = None) -> WatchDeepRunView:
        rid = _text(reservation_id, field="reservation_id", max_len=64)
        why = _text(reason, field="reason")
        instant = _as_hkt(released_at, field="released_at", default=self._clock)
        with self._lock:
            folded = self._fold()
            reservation = folded.reservations.get(rid)
            if reservation is None:
                raise WatchDeepReservationConflict("unknown reservation_id")
            if instant < reservation.reserved_at:
                raise ValueError("released_at cannot precede reserved_at")
            if rid in folded.settlements:
                raise WatchDeepReservationConflict(
                    "a settled reservation cannot be released")
            stored = folded.releases.get(rid)
            if stored is not None:
                if str(stored.get("reason")) != why:
                    raise WatchDeepReservationConflict(
                        "reservation was already released with a different reason")
            else:
                payload = {
                    "reservation_id": rid,
                    "released_at": _iso(instant),
                    "reason": why,
                }
                self._append(folded, "run_released", payload)
                folded.releases[rid] = payload
            return self._run_view(folded, reservation)

    def settle(self, reservation_id: str, *, actual_llm_invocations: int,
               outcome: str, detail: str | None = None,
               settled_at: datetime | None = None) -> WatchDeepRunView:
        rid = _text(reservation_id, field="reservation_id", max_len=64)
        actual = _nonnegative_int(
            actual_llm_invocations, field="actual_llm_invocations")
        result = _text(outcome, field="outcome", max_len=80)
        detail_text = str(detail).strip()[:1000] if detail is not None else None
        instant = _as_hkt(settled_at, field="settled_at", default=self._clock)
        with self._lock:
            folded = self._fold()
            reservation = folded.reservations.get(rid)
            if reservation is None:
                raise WatchDeepReservationConflict("unknown reservation_id")
            if instant < reservation.reserved_at:
                raise ValueError("settled_at cannot precede reserved_at")
            if rid in folded.releases:
                raise WatchDeepReservationConflict(
                    "a released reservation cannot be settled")
            payload = {
                "reservation_id": rid,
                "actual_llm_invocations": actual,
                "outcome": result,
                "detail": detail_text,
                "settled_at": _iso(instant),
            }
            stored = folded.settlements.get(rid)
            if stored is not None:
                comparable = dict(stored)
                comparable.pop("settled_at", None)
                wanted = dict(payload)
                wanted.pop("settled_at", None)
                if comparable != wanted:
                    raise WatchDeepReservationConflict(
                        "reservation already has a different terminal settlement")
                return self._run_view(folded, reservation)
            self._append(folded, "run_settled", payload)
            folded.settlements[rid] = payload
            return self._run_view(folded, reservation)

    def list_runs(self, *, code: str, assignment_revision: int | None = None,
                  limit: int = 50) -> list[WatchDeepRunView]:
        canonical = normalize_code(code)
        revision = (_positive_int(assignment_revision, field="assignment_revision")
                    if assignment_revision is not None else None)
        max_items = max(1, min(int(limit), 500))
        with self._lock:
            folded = self._fold()
            out: list[WatchDeepRunView] = []
            for rid in reversed(folded.run_order):
                reservation = folded.reservations[rid]
                if reservation.code != canonical:
                    continue
                if revision is not None and reservation.assignment_revision != revision:
                    continue
                out.append(self._run_view(folded, reservation))
                if len(out) >= max_items:
                    break
            return out

    def global_usage(self, session_date: date | str) -> WatchDeepGlobalBudgetView:
        session = _as_date(session_date).isoformat()
        with self._lock:
            return self._global_budget_view(self._fold(), session)

    def scope_consumed(self, *, code: str, assignment_revision: int,
                       session_date: date | str) -> bool:
        """Whether this exact assignment/session has a non-released admission."""
        canonical = normalize_code(code)
        revision = _positive_int(assignment_revision, field="assignment_revision")
        session = _as_date(session_date).isoformat()
        with self._lock:
            return self._scope_consumed(
                self._fold(), code=canonical, assignment_revision=revision,
                session_date=session)

    def global_concurrency(self) -> WatchDeepGlobalConcurrencyView:
        with self._lock:
            return self._global_concurrency_view(self._fold())

    def _authorization_view(self, folded: _Folded,
                            auth: WatchDeepAuthorization,
                            *, now: datetime) -> WatchDeepAuthorizationView:
        runs_used = 0
        llm_used = 0
        for rid in folded.run_order:
            reservation = folded.reservations[rid]
            if reservation.authorization_id != auth.authorization_id:
                continue
            if rid in folded.releases:
                continue
            runs_used += 1
            settlement = folded.settlements.get(rid)
            llm_used += (int(settlement["actual_llm_invocations"])
                         if settlement is not None
                         else reservation.requested_llm_invocations)
        runs_remaining = max(0, auth.max_runs - runs_used)
        llm_remaining = max(0, auth.max_llm_invocations - llm_used)
        revoked = folded.revocations.get(auth.authorization_id)
        if revoked is not None:
            status = "revoked"
            terminal_reason = "revoked"
        elif now < auth.valid_from:
            status = "not_yet_valid"
            terminal_reason = None
        elif now >= auth.valid_until:
            status = "expired"
            terminal_reason = "expired"
        elif runs_remaining < 1 or llm_remaining < 1:
            status = "exhausted"
            terminal_reason = "runs_exhausted" if runs_remaining < 1 else "llm_exhausted"
        else:
            status = "active"
            terminal_reason = None
        return WatchDeepAuthorizationView(
            authorization=auth,
            status=status,  # type: ignore[arg-type]
            terminal_reason=terminal_reason,
            runs_used=runs_used,
            runs_remaining=runs_remaining,
            llm_invocations_used=llm_used,
            llm_invocations_remaining=llm_remaining,
            revoked_by=(str(revoked["revoked_by"]) if revoked else None),
            revoked_at=(_as_hkt(revoked["revoked_at"], field="revoked_at")
                        if revoked else None),
            revocation_reason=(str(revoked["reason"]) if revoked else None),
        )

    def _global_budget_view(self, folded: _Folded,
                            session: str) -> WatchDeepGlobalBudgetView:
        used = sum(
            1 for rid, reservation in folded.reservations.items()
            if reservation.session_date == session and rid not in folded.releases
        )
        return WatchDeepGlobalBudgetView(
            session_date=session,
            max_runs=self.global_daily_max_runs,
            runs_used=used,
            runs_remaining=max(0, self.global_daily_max_runs - used),
        )

    def _global_concurrency_view(self, folded: _Folded) -> WatchDeepGlobalConcurrencyView:
        in_flight = sum(
            1 for rid in folded.reservations
            if rid not in folded.releases and rid not in folded.settlements
        )
        return WatchDeepGlobalConcurrencyView(
            max_runs=self.global_max_concurrent_runs,
            runs_in_flight=in_flight,
            runs_available=max(0, self.global_max_concurrent_runs - in_flight),
        )

    @staticmethod
    def _scope_consumed(folded: _Folded, *, code: str,
                        assignment_revision: int, session_date: str) -> bool:
        return any(
            reservation.code == code
            and reservation.assignment_revision == assignment_revision
            and reservation.session_date == session_date
            and rid not in folded.releases
            for rid, reservation in folded.reservations.items()
        )

    def _run_view(self, folded: _Folded,
                  reservation: WatchDeepReservation) -> WatchDeepRunView:
        rid = reservation.reservation_id
        if rid in folded.settlements:
            row = folded.settlements[rid]
            return WatchDeepRunView(
                reservation=replace(reservation, newly_reserved=False),
                status="settled",
                actual_llm_invocations=int(row["actual_llm_invocations"]),
                outcome=str(row["outcome"]),
                detail=row.get("detail"),
                terminal_at=_as_hkt(row["settled_at"], field="settled_at"),
                llm_overrun=(int(row["actual_llm_invocations"])
                             > reservation.requested_llm_invocations),
                llm_overrun_by=max(
                    0, int(row["actual_llm_invocations"])
                    - reservation.requested_llm_invocations),
            )
        if rid in folded.releases:
            row = folded.releases[rid]
            return WatchDeepRunView(
                reservation=replace(reservation, newly_reserved=False),
                status="released",
                actual_llm_invocations=0,
                outcome="released_before_execution",
                detail=str(row["reason"]),
                terminal_at=_as_hkt(row["released_at"], field="released_at"),
            )
        return WatchDeepRunView(
            reservation=replace(reservation, newly_reserved=False), status="reserved")

    def _append(self, folded: _Folded, kind: str, payload: dict[str, Any]) -> None:
        if kind not in _ROW_KINDS:
            raise ValueError(f"unsupported row kind: {kind}")
        seq = folded.seq + 1
        base = {
            "schema_version": "1",
            "seq": seq,
            "kind": kind,
            "prev_digest": folded.last_digest,
            "payload": payload,
        }
        row = {**base, "row_digest": _digest(base)}
        line = _canonical_bytes(row) + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        folded.seq = seq
        folded.last_digest = row["row_digest"]

    def _fold(self) -> _Folded:
        folded = _empty_fold()
        if not self.path.exists():
            return folded
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WatchDeepAuthorizationStoreCorrupt(
                f"authorization journal cannot be read: {exc}") from exc
        lines = raw.splitlines()
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"blank journal row at line {index}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"invalid JSON at journal line {index}") from exc
            if not isinstance(row, dict) or set(row) != {
                "schema_version", "seq", "kind", "prev_digest", "payload", "row_digest",
            }:
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"invalid journal row shape at line {index}")
            base = {k: row[k] for k in (
                "schema_version", "seq", "kind", "prev_digest", "payload")}
            if row["schema_version"] != "1" or row["seq"] != index:
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"broken journal sequence at line {index}")
            if row["kind"] not in _ROW_KINDS or not isinstance(row["payload"], dict):
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"unsupported journal row at line {index}")
            if row["prev_digest"] != folded.last_digest or row["row_digest"] != _digest(base):
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"broken journal digest chain at line {index}")
            try:
                self._fold_row(folded, str(row["kind"]), row["payload"])
            except WatchDeepAuthorizationStoreCorrupt:
                raise
            except Exception as exc:  # validation errors become a typed store refusal
                raise WatchDeepAuthorizationStoreCorrupt(
                    f"invalid {row['kind']} payload at line {index}: {exc}") from exc
            folded.seq = index
            folded.last_digest = str(row["row_digest"])
        return folded

    def _fold_row(self, folded: _Folded, kind: str, payload: dict[str, Any]) -> None:
        if kind == "authorization_issued":
            auth = self._authorization_from_payload(payload)
            if auth.authorization_id in folded.authorizations:
                raise WatchDeepAuthorizationStoreCorrupt("duplicate authorization_id")
            folded.authorizations[auth.authorization_id] = auth
            folded.authorization_order.append(auth.authorization_id)
            return
        if kind == "authorization_revoked":
            auth_id = _text(payload.get("authorization_id"), field="authorization_id", max_len=64)
            if auth_id not in folded.authorizations or auth_id in folded.revocations:
                raise WatchDeepAuthorizationStoreCorrupt("invalid authorization revocation")
            revoked_at = _as_hkt(payload.get("revoked_at"), field="revoked_at")
            if revoked_at < folded.authorizations[auth_id].issued_at:
                raise WatchDeepAuthorizationStoreCorrupt("revocation predates issuance")
            folded.revocations[auth_id] = {
                "authorization_id": auth_id,
                "revoked_by": _text(payload.get("revoked_by"), field="revoked_by", max_len=200),
                "revoked_at": _iso(revoked_at),
                "reason": _text(payload.get("reason"), field="reason"),
            }
            return
        if kind == "run_reserved":
            reservation = self._reservation_from_payload(payload)
            if reservation.reservation_id in folded.reservations:
                raise WatchDeepAuthorizationStoreCorrupt("duplicate reservation_id")
            prior_id = folded.run_ids.get(reservation.run_id)
            if prior_id is not None:
                prior = folded.reservations[prior_id]
                if not (
                    prior_id in folded.releases
                    and prior.code == reservation.code
                    and prior.assignment_revision == reservation.assignment_revision
                    and prior.session_date == reservation.session_date
                    and prior.preset_id == reservation.preset_id
                    and prior.preset_record_digest == reservation.preset_record_digest
                    and prior.requested_llm_invocations
                        == reservation.requested_llm_invocations
                ):
                    raise WatchDeepAuthorizationStoreCorrupt(
                        "duplicate/unreleased run_id")
            auth = folded.authorizations.get(reservation.authorization_id)
            if auth is None or not (
                auth.code == reservation.code
                and auth.assignment_revision == reservation.assignment_revision
                and auth.session_date == reservation.session_date
                and auth.preset_id == reservation.preset_id
                and auth.preset_record_digest == reservation.preset_record_digest
            ):
                raise WatchDeepAuthorizationStoreCorrupt(
                    "reservation does not match its authorization")
            instant = reservation.reserved_at
            view = self._authorization_view(folded, auth, now=instant)
            global_view = self._global_budget_view(folded, reservation.session_date)
            if (view.status != "active" or view.runs_remaining < 1
                    or view.llm_invocations_remaining
                    < reservation.requested_llm_invocations
                    or global_view.runs_remaining < 1):
                raise WatchDeepAuthorizationStoreCorrupt(
                    "reservation exceeded authorization/global envelope")
            if self._global_concurrency_view(folded).runs_available < 1:
                raise WatchDeepAuthorizationStoreCorrupt(
                    "reservation exceeded global concurrency envelope")
            folded.reservations[reservation.reservation_id] = reservation
            folded.run_ids[reservation.run_id] = reservation.reservation_id
            folded.run_order.append(reservation.reservation_id)
            return
        rid = _text(payload.get("reservation_id"), field="reservation_id", max_len=64)
        if rid not in folded.reservations:
            raise WatchDeepAuthorizationStoreCorrupt("terminal row names unknown reservation")
        if kind == "run_released":
            if rid in folded.releases or rid in folded.settlements:
                raise WatchDeepAuthorizationStoreCorrupt("duplicate/conflicting release")
            released_at = _as_hkt(payload.get("released_at"), field="released_at")
            if released_at < folded.reservations[rid].reserved_at:
                raise WatchDeepAuthorizationStoreCorrupt("release predates reservation")
            folded.releases[rid] = {
                "reservation_id": rid,
                "released_at": _iso(released_at),
                "reason": _text(payload.get("reason"), field="reason"),
            }
            return
        if kind == "run_settled":
            if rid in folded.releases or rid in folded.settlements:
                raise WatchDeepAuthorizationStoreCorrupt("duplicate/conflicting settlement")
            settled_at = _as_hkt(payload.get("settled_at"), field="settled_at")
            if settled_at < folded.reservations[rid].reserved_at:
                raise WatchDeepAuthorizationStoreCorrupt("settlement predates reservation")
            folded.settlements[rid] = {
                "reservation_id": rid,
                "actual_llm_invocations": _nonnegative_int(
                    payload.get("actual_llm_invocations"),
                    field="actual_llm_invocations"),
                "outcome": _text(payload.get("outcome"), field="outcome", max_len=80),
                "detail": (str(payload.get("detail")).strip()[:1000]
                           if payload.get("detail") is not None else None),
                "settled_at": _iso(settled_at),
            }

    def _authorization_from_payload(self, payload: dict[str, Any]) -> WatchDeepAuthorization:
        code = normalize_code(payload.get("code"))
        revision = _positive_int(payload.get("assignment_revision"),
                                 field="assignment_revision")
        session = _as_date(payload.get("session_date")).isoformat()
        preset = _text(payload.get("preset_id"), field="preset_id", max_len=200)
        record_digest = _preset_digest(payload.get("preset_record_digest"))
        start = _as_hkt(payload.get("valid_from"), field="valid_from")
        end = _as_hkt(payload.get("valid_until"), field="valid_until")
        issued_at = _as_hkt(payload.get("issued_at"), field="issued_at")
        fields = {
            "code": code,
            "assignment_revision": revision,
            "session_date": session,
            "preset_id": preset,
            "preset_record_digest": record_digest,
            "valid_from": _iso(start),
            "valid_until": _iso(end),
            "max_runs": _positive_int(payload.get("max_runs"), field="max_runs"),
            "max_llm_invocations": _positive_int(
                payload.get("max_llm_invocations"), field="max_llm_invocations"),
            "issued_by": _text(payload.get("issued_by"), field="issued_by", max_len=200),
            "issued_at": _iso(issued_at),
            "reason": _text(payload.get("reason"), field="reason"),
        }
        auth_id = _text(payload.get("authorization_id"),
                        field="authorization_id", max_len=64)
        if auth_id != _digest({"kind": "watch_deep_authorization", **fields}):
            raise WatchDeepAuthorizationStoreCorrupt("authorization_id digest mismatch")
        if start.date().isoformat() != session or end.date().isoformat() != session or end <= start:
            raise WatchDeepAuthorizationStoreCorrupt("authorization validity/session mismatch")
        return WatchDeepAuthorization(
            authorization_id=auth_id,
            **{**fields, "valid_from": start, "valid_until": end, "issued_at": issued_at},
        )

    def _reservation_from_payload(self, payload: dict[str, Any]) -> WatchDeepReservation:
        fields = {
            "authorization_id": _text(payload.get("authorization_id"),
                                      field="authorization_id", max_len=64),
            "run_id": _text(payload.get("run_id"), field="run_id", max_len=240),
            "code": normalize_code(payload.get("code")),
            "assignment_revision": _positive_int(
                payload.get("assignment_revision"), field="assignment_revision"),
            "session_date": _as_date(payload.get("session_date")).isoformat(),
            "preset_id": _text(payload.get("preset_id"), field="preset_id", max_len=200),
            "preset_record_digest": _preset_digest(payload.get("preset_record_digest")),
            "requested_llm_invocations": _positive_int(
                payload.get("requested_llm_invocations"),
                field="requested_llm_invocations"),
            "reserved_at": _iso(_as_hkt(payload.get("reserved_at"), field="reserved_at")),
        }
        rid = _text(payload.get("reservation_id"), field="reservation_id", max_len=64)
        if rid != _digest({"kind": "watch_deep_reservation", **fields}):
            raise WatchDeepAuthorizationStoreCorrupt("reservation_id digest mismatch")
        return WatchDeepReservation(
            reservation_id=rid,
            **{**fields, "reserved_at": _as_hkt(fields["reserved_at"], field="reserved_at")},
            newly_reserved=False,
        )
