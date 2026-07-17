# -*- coding: utf-8 -*-
"""Phase 3 · Task 4 — ``PitGuard`` (the point-in-time firewall).

Written test-first (RED until ``guanlan_v2.orchestration.data.pit`` exists). This
locks the PIT 红线: every raw row/vintage is judged by its *available_at* (当时可知
时间) against the *frozen* ``ctx.as_of`` cutoff — never period-end, never the wall
clock. A future or missing/naive availability is **refused** (raised), never
silently filtered and never fallback-eligible. ``PIT_REPLAY`` is additionally held
to strict + frozen-snapshot + vintage-manifest + non-LIVE invariants and may never
fall through to LIVE. Freshness is a versioned per-method/category policy evaluated
against the frozen clock/calendar — there is no global ``MAX_STALE_DAYS``.

The guard makes *exactly one* typed refusal-record call (with ordered evidence)
before raising; Task 4 proves this with an in-memory spy (Task 5 registers the
detail schema, Task 6 binds the production gateway/sink adapter).

Run: ``pytest tests/orchestration/data/test_pit.py -v``
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused,
    LiveFallbackRefused,
    MissingAvailabilityRefused,
    RoutingConfigurationError,
    SnapshotMismatchError,
    StaleDataError,
)
from guanlan_v2.orchestration.data.pit import (
    DataFetchRefusalDetails,
    FreshnessPolicy,
    PitGuard,
    RawRowCandidate,
)
from guanlan_v2.orchestration.data.result import PitAudit
from guanlan_v2.orchestration.context import ClockSpec, DataContext
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import ContentRef

UTC = timezone.utc

# ── frozen fixtures ─────────────────────────────────────────────────────────
AS_OF = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)  # a Wednesday
CAL_ID = "cn_a_share"


def _weekdays_2026() -> tuple[date, ...]:
    out: list[date] = []
    d = date(2026, 1, 1)
    while d <= date(2026, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


CALENDAR = build_trading_calendar(
    calendar_id=CAL_ID,
    sessions=_weekdays_2026(),
    material_id="cal.cn.2026",
    material_version="1",
)

DA, DB, DC, DD, DE = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)
SOURCE_REF = ContentRef(id="prices.tushare", version="1", content_digest=DE)


class FixedClock:
    """A frozen authoritative clock reading exactly one instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class SpyRecorder:
    """In-memory refusal recorder spy (the Phase-3 registry does not exist yet)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self._fail = fail

    def record_data_refusal(self, details, evidence, *, idempotency_key: str) -> None:
        self.calls.append((details, evidence, idempotency_key))
        if self._fail:
            raise RuntimeError("audit sink is down")


def _clock_spec(*, as_of: datetime = AS_OF, calendar_id: str = CAL_ID) -> ClockSpec:
    return ClockSpec(as_of=as_of, timezone="Asia/Shanghai", calendar_id=calendar_id)


def _ctx(
    *,
    mode: DataMode = DataMode.ONLINE,
    backend: DataBackend = DataBackend.CACHE,
    strict_pit: bool = False,
    calendar_id: str = CAL_ID,
    vintage_manifest_digest: str | None = None,
) -> DataContext:
    return DataContext(
        as_of=AS_OF,
        clock=_clock_spec(calendar_id=calendar_id),
        mode=mode,
        backend=backend,
        strict_pit=strict_pit,
        calendar_id=calendar_id,
        resolved_vendor_chains={"prices": ("tushare", "eastmoney")},
        source_config_digest=DA,
        source_registry_digest=DB,
        routing_snapshot_digest=DC,
        data_snapshot_id="snap-1",
        data_snapshot_content_digest=DD,
        vintage_manifest_digest=vintage_manifest_digest,
        built_at=AS_OF,
    )


def _replay_ctx() -> DataContext:
    return _ctx(
        mode=DataMode.PIT_REPLAY,
        backend=DataBackend.PIT_STORE,
        strict_pit=True,
        vintage_manifest_digest="f" * 64,
    )


def _cand(
    available_at: datetime | None,
    *,
    required: bool = True,
    payload: dict | None = None,
    ingested_at: datetime | None = None,
) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload=payload if payload is not None else {"close": 1720.5},
        available_at=available_at,
        ingested_at=ingested_at if ingested_at is not None else AS_OF,
        revision_id="r1",
        required=required,
    )


def _guard(ctx: DataContext | None = None, recorder: SpyRecorder | None = None) -> PitGuard:
    ctx = ctx if ctx is not None else _ctx()
    return PitGuard.from_context(
        ctx,
        clock=FixedClock(AS_OF),
        calendar=CALENDAR,
        refusal_recorder=recorder if recorder is not None else SpyRecorder(),
    )


def _req_kwargs(ctx: DataContext, source_ref: ContentRef = SOURCE_REF, **over) -> dict:
    kw = dict(
        request_digest="1" * 64,
        request_context_digest=content_digest(ctx),
        source_ref=source_ref,
    )
    kw.update(over)
    return kw


# ── freshness policies ──────────────────────────────────────────────────────
def _elapsed_policy(seconds: int) -> FreshnessPolicy:
    return FreshnessPolicy.build(
        policy_id="fresh.prices",
        policy_version="1",
        method_or_category="prices.daily",
        max_elapsed_seconds=seconds,
    )


def _session_policy(sessions: int, *, calendar=CALENDAR) -> FreshnessPolicy:
    return FreshnessPolicy.build(
        policy_id="fresh.prices",
        policy_version="1",
        method_or_category="prices.daily",
        max_trading_sessions=sessions,
        calendar_id=calendar.calendar_id,
        calendar_material_ref=calendar.material_ref,
    )


# =========================================================================== #
# 1. visible ordered candidates → immutable tuple + coherent PitAudit(passed)  #
# =========================================================================== #
def test_visible_candidates_pass_ordered_and_coherent():
    ctx = _ctx()
    guard = _guard(ctx)
    c1 = _cand(AS_OF - timedelta(days=2))
    c2 = _cand(AS_OF - timedelta(hours=1))
    c3 = _cand(AS_OF)  # boundary: available_at == as_of is visible, never future
    rows, audit = guard.check_raw((c1, c2, c3), **_req_kwargs(ctx))
    assert rows == (c1, c2, c3)  # order preserved
    assert isinstance(rows, tuple)
    assert isinstance(audit, PitAudit)
    assert audit.guard_result == "passed"
    assert audit.rows_seen == 3 and audit.rows_returned == 3
    assert audit.future_rows == 0 and audit.missing_available_at_rows == 0
    assert audit.latest_available_at == AS_OF
    assert audit.mode is DataMode.ONLINE


def test_boundary_available_at_equal_as_of_is_visible():
    ctx = _ctx()
    guard = _guard(ctx)
    rows, audit = guard.check_raw((_cand(AS_OF),), **_req_kwargs(ctx))
    assert audit.guard_result == "passed" and audit.rows_returned == 1


# =========================================================================== #
# 2. both ONLINE and PIT_REPLAY refuse a future candidate (never filter)       #
# =========================================================================== #
@pytest.mark.parametrize("ctx_factory", [_ctx, _replay_ctx])
def test_future_candidate_is_refused_not_filtered(ctx_factory):
    ctx = ctx_factory()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    future = _cand(AS_OF + timedelta(seconds=1))
    with pytest.raises(FutureDataRefused) as ei:
        guard.check_raw((_cand(AS_OF - timedelta(days=1)), future), **_req_kwargs(ctx))
    assert ei.value.future_rows == 1
    assert len(rec.calls) == 1  # recorded exactly once, then raised


def test_mixed_batch_some_future_refuses_the_whole_read():
    ctx = _ctx()
    guard = _guard(ctx)
    visible = _cand(AS_OF - timedelta(days=1))
    future = _cand(AS_OF + timedelta(hours=1))
    with pytest.raises(FutureDataRefused):
        guard.check_raw((visible, future), **_req_kwargs(ctx))


# =========================================================================== #
# 3. missing and naive availability → distinct refusal reasons                 #
# =========================================================================== #
def test_missing_availability_is_refused():
    ctx = _ctx()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    with pytest.raises(MissingAvailabilityRefused):
        guard.check_raw((_cand(None),), **_req_kwargs(ctx))
    assert rec.calls[0][0].reason_code == "missing_availability"


def test_naive_availability_is_refused_with_distinct_reason():
    ctx = _ctx()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    naive = _cand(datetime(2026, 7, 14, 6, 0))  # tz-naive: PIT cannot be proven
    with pytest.raises(MissingAvailabilityRefused):
        guard.check_raw((naive,), **_req_kwargs(ctx))
    assert rec.calls[0][0].reason_code == "naive_availability"


def test_missing_and_naive_carry_distinct_reason_codes():
    ctx = _ctx()
    r1, r2 = SpyRecorder(), SpyRecorder()
    with pytest.raises(MissingAvailabilityRefused):
        _guard(ctx, r1).check_raw((_cand(None),), **_req_kwargs(ctx))
    with pytest.raises(MissingAvailabilityRefused):
        _guard(ctx, r2).check_raw((_cand(datetime(2026, 7, 14)),), **_req_kwargs(ctx))
    assert r1.calls[0][0].reason_code != r2.calls[0][0].reason_code


def test_missing_on_optional_gives_no_filtered_escape():
    # PIT firewall applies uniformly: an explicitly-optional row missing availability
    # is NOT silently filtered into a success — it still refuses (no soft filtered path).
    ctx = _ctx()
    guard = _guard(ctx)
    with pytest.raises(MissingAvailabilityRefused):
        guard.check_raw(
            (_cand(AS_OF - timedelta(days=1)), _cand(None, required=False)),
            **_req_kwargs(ctx),
        )


# =========================================================================== #
# 4. PIT_REPLAY structural rejection BEFORE a source call (no fallback to LIVE) #
# =========================================================================== #
def _replay_construct(**over) -> DataContext:
    base = dict(
        as_of=AS_OF,
        clock=_clock_spec(),
        mode=DataMode.PIT_REPLAY,
        backend=DataBackend.PIT_STORE,
        strict_pit=True,
        calendar_id=CAL_ID,
        resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest=DA,
        source_registry_digest=DB,
        routing_snapshot_digest=DC,
        data_snapshot_id="snap-1",
        data_snapshot_content_digest=DD,
        vintage_manifest_digest="f" * 64,
        built_at=AS_OF,
    )
    base.update(over)
    return DataContext.model_construct(**base)


def test_replay_live_backend_is_refused():
    with pytest.raises(LiveFallbackRefused):
        _guard(_replay_construct(backend=DataBackend.LIVE))


def test_replay_non_strict_is_refused():
    with pytest.raises(RoutingConfigurationError):
        _guard(_replay_construct(strict_pit=False))


def test_replay_missing_vintage_is_refused():
    with pytest.raises(RoutingConfigurationError):
        _guard(_replay_construct(vintage_manifest_digest=None))


def test_replay_missing_snapshot_locator_is_refused():
    with pytest.raises(RoutingConfigurationError):
        _guard(_replay_construct(data_snapshot_id=""))


def test_replay_missing_snapshot_content_is_refused():
    with pytest.raises(RoutingConfigurationError):
        _guard(_replay_construct(data_snapshot_content_digest=None))


def test_well_formed_replay_context_binds_and_passes():
    ctx = _replay_ctx()
    guard = _guard(ctx)
    rows, audit = guard.check_raw((_cand(AS_OF - timedelta(days=1)),), **_req_kwargs(ctx))
    assert audit.guard_result == "passed" and audit.mode is DataMode.PIT_REPLAY


# =========================================================================== #
# 5. request-context digest mismatch is rejected (pre-invocation)              #
# =========================================================================== #
def test_request_context_digest_mismatch_is_rejected():
    ctx = _ctx()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    with pytest.raises(SnapshotMismatchError):
        guard.check_raw(
            (_cand(AS_OF - timedelta(days=1)),),
            **_req_kwargs(ctx, request_context_digest="0" * 64),
        )
    detail = rec.calls[0][0]
    assert detail.reason_code == "request_context_digest_mismatch"
    assert detail.stage == "context_validation"
    # a pre-invocation refusal forbids candidate/PIT facts.
    assert detail.candidate_metadata_digest is None
    assert detail.pit_audit_digest is None


# =========================================================================== #
# 6. exactly one typed detail before raising; deterministic idempotency key     #
# =========================================================================== #
def test_recorder_sees_exactly_one_typed_detail_before_exception():
    ctx = _ctx()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    with pytest.raises(FutureDataRefused):
        guard.check_raw((_cand(AS_OF + timedelta(days=1)),), **_req_kwargs(ctx))
    assert len(rec.calls) == 1
    details, evidence, idem = rec.calls[0]
    assert isinstance(details, DataFetchRefusalDetails)
    assert details.stage == "candidate_check"
    # candidate_check refusal requires BOTH candidate + PIT-audit facts.
    assert details.candidate_metadata_digest is not None
    assert details.pit_audit_digest is not None
    # evidence is canonically ordered by name.
    names = [e.name for e in evidence]
    assert names == sorted(names)


def test_guard_idempotency_key_is_deterministic():
    ctx = _ctx()
    r1, r2 = SpyRecorder(), SpyRecorder()
    with pytest.raises(FutureDataRefused):
        _guard(ctx, r1).check_raw((_cand(AS_OF + timedelta(days=1)),), **_req_kwargs(ctx))
    with pytest.raises(FutureDataRefused):
        _guard(ctx, r2).check_raw((_cand(AS_OF + timedelta(days=1)),), **_req_kwargs(ctx))
    assert r1.calls[0][2] == r2.calls[0][2]  # same idempotency key
    assert r1.calls[0][0].detail_digest == r2.calls[0][0].detail_digest


# =========================================================================== #
# 7. recorder failure stays loud and cannot return data                        #
# =========================================================================== #
def test_recorder_failure_is_loud_and_returns_no_data():
    ctx = _ctx()
    rec = SpyRecorder(fail=True)
    guard = _guard(ctx, rec)
    with pytest.raises(RuntimeError):  # the sink's failure propagates, unmasked
        guard.check_raw((_cand(AS_OF + timedelta(days=1)),), **_req_kwargs(ctx))
    assert len(rec.calls) == 1  # it was called (and failed) — never swallowed


# =========================================================================== #
# 8. refused candidates cause zero store writes (only a safe audit detail)      #
# =========================================================================== #
def test_refusal_writes_only_a_safe_audit_detail_no_raw_bytes():
    ctx = _ctx()
    rec = SpyRecorder()
    guard = _guard(ctx, rec)
    secret_payload = {"close": 999.0, "secret": "leak-me"}
    with pytest.raises(FutureDataRefused):
        guard.check_raw(
            (_cand(AS_OF + timedelta(days=1), payload=secret_payload),),
            **_req_kwargs(ctx),
        )
    details, evidence, _ = rec.calls[0]
    # the detail carries only digests/refs — never the raw candidate bytes.
    assert not hasattr(details, "raw_payload")
    dumped = details.model_dump(mode="json")
    assert "leak-me" not in repr(dumped)
    # every evidence value is a digest reference, not content.
    for e in evidence:
        assert len(e.digest) == 64


# =========================================================================== #
# 9. negative/bool thresholds, calendar mismatch, mutable/extra fields rejected #
# =========================================================================== #
def test_freshness_requires_exactly_one_threshold():
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_elapsed_seconds=10, max_trading_sessions=1,
            calendar_id=CAL_ID, calendar_material_ref=CALENDAR.material_ref,
        )
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(policy_id="p", policy_version="1", method_or_category="m")


def test_freshness_negative_and_bool_thresholds_are_rejected():
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_elapsed_seconds=-1,
        )
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_trading_sessions=True,  # bool is not a valid count
            calendar_id=CAL_ID, calendar_material_ref=CALENDAR.material_ref,
        )


def test_session_policy_requires_calendar_identity():
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_trading_sessions=1,  # session-based but no calendar
        )
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_elapsed_seconds=10, calendar_id=CAL_ID,  # elapsed must not carry calendar
            calendar_material_ref=CALENDAR.material_ref,
        )


def test_freshness_policy_is_frozen_and_closed():
    p = _elapsed_policy(60)
    with pytest.raises(ValidationError):
        p.max_elapsed_seconds = 120  # frozen
    with pytest.raises(ValidationError):
        FreshnessPolicy.build(
            policy_id="p", policy_version="1", method_or_category="m",
            max_elapsed_seconds=10, extra_field="x",  # extra forbidden
        )


def test_session_freshness_calendar_mismatch_fails_no_arithmetic_fallback():
    ctx = _ctx()
    guard = _guard(ctx)
    drifted = ContentRef(id="cal.cn.2026", version="1", content_digest="0" * 64)
    bad_policy = FreshnessPolicy.build(
        policy_id="p", policy_version="1", method_or_category="m",
        max_trading_sessions=1, calendar_id=CAL_ID, calendar_material_ref=drifted,
    )
    with pytest.raises(SnapshotMismatchError):
        guard.check_raw(
            (_cand(AS_OF - timedelta(days=1)),),
            freshness=bad_policy, **_req_kwargs(ctx),
        )


def test_guard_calendar_must_match_context():
    ctx = _ctx(calendar_id="cn_a_share")
    other = build_trading_calendar(
        calendar_id="XSHE", sessions=_weekdays_2026(),
        material_id="cal.xshe.2026", material_version="1",
    )
    with pytest.raises(RoutingConfigurationError):
        PitGuard.from_context(
            ctx, clock=FixedClock(AS_OF), calendar=other, refusal_recorder=SpyRecorder()
        )


def test_guard_clock_must_read_the_frozen_as_of():
    ctx = _ctx()
    with pytest.raises(RoutingConfigurationError):
        PitGuard.from_context(
            ctx, clock=FixedClock(AS_OF + timedelta(hours=1)),
            calendar=CALENDAR, refusal_recorder=SpyRecorder(),
        )


# =========================================================================== #
# 10. elapsed + session freshness boundaries use the frozen clock/calendar      #
# =========================================================================== #
def test_elapsed_freshness_boundary():
    ctx = _ctx()
    guard = _guard(ctx)
    # exactly at the boundary is fresh (elapsed == max, not > max)
    rows, audit = guard.check_raw(
        (_cand(AS_OF - timedelta(seconds=3600)),),
        freshness=_elapsed_policy(3600), **_req_kwargs(ctx),
    )
    assert audit.guard_result == "passed"


def test_elapsed_freshness_stale_raises_with_pit_audit():
    ctx = _ctx()
    guard = _guard(ctx)
    latest = AS_OF - timedelta(seconds=7200)
    with pytest.raises(StaleDataError) as ei:
        guard.check_raw(
            (_cand(latest),), freshness=_elapsed_policy(3600), **_req_kwargs(ctx),
        )
    assert ei.value.latest_available_at == latest
    assert isinstance(ei.value.pit_audit, PitAudit)


def test_session_freshness_fresh_at_boundary():
    ctx = _ctx()
    guard = _guard(ctx)
    # as_of Wed 07-15; latest Tue 07-14 is exactly 1 session behind → fresh at max=1.
    latest = datetime(2026, 7, 14, 6, 0, tzinfo=UTC)
    rows, audit = guard.check_raw(
        (_cand(latest),), freshness=_session_policy(1), **_req_kwargs(ctx),
    )
    assert audit.guard_result == "passed"


def test_session_freshness_stale_beyond_boundary():
    ctx = _ctx()
    guard = _guard(ctx)
    # latest Mon 07-13 is 2 sessions behind Wed 07-15 → stale at max=1.
    latest = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    with pytest.raises(StaleDataError):
        guard.check_raw(
            (_cand(latest),), freshness=_session_policy(1), **_req_kwargs(ctx),
        )


def test_no_freshness_policy_skips_the_freshness_gate():
    ctx = _ctx()
    guard = _guard(ctx)
    latest = AS_OF - timedelta(days=30)  # ancient, but no policy supplied
    rows, audit = guard.check_raw((_cand(latest),), **_req_kwargs(ctx))
    assert audit.guard_result == "passed"


# =========================================================================== #
# RawRowCandidate / DataFetchRefusalDetails intrinsic invariants               #
# =========================================================================== #
def test_raw_candidate_verifies_its_raw_content_digest():
    with pytest.raises(ValidationError):
        RawRowCandidate(
            raw_payload={"x": 1}, available_at=AS_OF, ingested_at=AS_OF,
            raw_content_digest="0" * 64,  # wrong
        )


def test_refusal_detail_stage_matrix_is_enforced():
    common = dict(
        request_digest="1" * 64, request_context_digest="2" * 64,
        method="prices.tushare", routing_snapshot_digest=DC,
        data_snapshot_content_digest=DD,
    )
    # context_validation must NOT carry candidate/PIT facts.
    with pytest.raises(ValidationError):
        DataFetchRefusalDetails.build(
            reason_code="request_context_digest_mismatch", stage="context_validation",
            candidate_metadata_digest=DA, pit_audit_digest=DB, **common,
        )
    # candidate_check MUST carry both.
    with pytest.raises(ValidationError):
        DataFetchRefusalDetails.build(
            reason_code="future_data", stage="candidate_check", **common,
        )
    # a reason/stage mismatch is rejected.
    with pytest.raises(ValidationError):
        DataFetchRefusalDetails.build(
            reason_code="future_data", stage="context_validation", **common,
        )
