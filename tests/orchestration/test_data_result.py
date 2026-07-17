# -*- coding: utf-8 -*-
"""Task 5 — typed ``DataResult[T]`` status envelope + PIT record contracts.

Written test-first: with ``guanlan_v2/orchestration/data/result.py`` absent the
module fails to import (RED). It then locks, GREEN, the reviewed invariants:

* ``SourceAttempt`` / ``PitAudit`` / ``PitRecord`` / ``DataResult[T]`` are all
  frozen, strict :class:`DigestModel` subclasses;
* the status/data matrix — only ``OK``/``DEGRADED`` carry typed data *and* a
  verified data content digest; every other status carries neither;
* ``DEGRADED`` requires ``coverage`` in ``[0, 1]`` and a non-empty reason;
* a nested attempt's wall-clock changes the *audit* digest but never the
  *semantic* one, while attempt outcome/order does change the semantic digest;
* PIT row counts are strict non-negative and internally coherent, and a replay
  with future / missing-availability rows can never claim ``guard_result=passed``;
* a concrete multi-row payload uses a *registered* ``PitRecord`` subtype — a
  plain ``dict`` is not accepted as PIT-compliant data;
* the declared content/audit digests are recomputed and verified on load.

Run from repo root: ``pytest tests/orchestration/test_data_result.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonNegativeInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataMode, DataStatus
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.data.result import (
    DataResult,
    PitAudit,
    PitRecord,
    SourceAttempt,
    row_time_metadata_digest_of,
)

UTC = timezone.utc


def _dt(hour: int = 9, minute: int = 30, *, day: int = 15) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


# Computed (not placeholder) digests for the external request / source config.
REQ_DIGEST = content_digest(
    {"method": "get_ohlcv", "symbol": "600519.SH", "start": "2026-07-01", "end": "2026-07-15"}
)
CFG_DIGEST = content_digest({"chain": ["mootdx", "sina"], "vendor_config_version": "1"})
WRONG_DIGEST = "0" * 64  # valid hex shape, wrong value


# --------------------------------------------------------------------------- #
# A concrete, registrable multi-row PIT payload schema                        #
# --------------------------------------------------------------------------- #
class OhlcvRow(PitRecord):
    """A real ``PitRecord`` subtype — the only proof of PIT compliance."""

    schema_version: Literal["1"] = "1"
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat
    volume: NonNegativeInt


def _row(close: float = 10.0, *, available_at: datetime | None = None,
         ingested_at: datetime | None = None) -> OhlcvRow:
    return OhlcvRow.build(
        available_at=available_at or _dt(9, 30),
        ingested_at=ingested_at or _dt(9, 31),
        open=9.5, high=10.5, low=9.0, close=close, volume=1000,
    )


def _attempt(outcome: str = "success", vendor: str = "mootdx",
             started: datetime | None = None, finished: datetime | None = None) -> SourceAttempt:
    return SourceAttempt(
        vendor=vendor, configured=True, outcome=outcome,
        started_at=started or _dt(9, 0), finished_at=finished or _dt(9, 1),
    )


def _audit(*, guard: str = "passed", future: int = 0, missing: int = 0,
           seen: int = 1, returned: int = 1, mode: DataMode = DataMode.ONLINE) -> PitAudit:
    return PitAudit(
        mode=mode, as_of=_dt(15, 0), rows_seen=seen, rows_returned=returned,
        future_rows=future, missing_available_at_rows=missing, guard_result=guard,
        latest_available_at=_dt(14, 0),
    )


def _result(*, status: DataStatus = DataStatus.OK, data=..., coverage=None,
            reason=None, attempts=None, **over):
    if data is ...:
        data = _row() if status in (DataStatus.OK, DataStatus.DEGRADED) else None
    base = dict(
        id="dr-1", method="get_ohlcv", request_digest=REQ_DIGEST,
        status=status, data=data, coverage=coverage, degradation_reason=reason,
        vendor="mootdx", subsource=None,
        resolved_vendor_chain=("mootdx", "sina"),
        source_config_digest=CFG_DIGEST,
        effective_at=None, available_at=_dt(9, 30), ingested_at=_dt(9, 31),
        fetched_at=_dt(9, 32), revision_id=None,
        attempts=attempts if attempts is not None else (_attempt(),),
        pit_audit=_audit(),
    )
    base.update(over)
    return DataResult[OhlcvRow].build(**base)


# --------------------------------------------------------------------------- #
# 0. all four contracts are frozen, strict DigestModels                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", [SourceAttempt, PitAudit, PitRecord, DataResult])
def test_all_four_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


# --------------------------------------------------------------------------- #
# 1. complete status/data matrix                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", list(DataStatus))
def test_status_data_matrix(status):
    row = _row()
    carries_data = status in (DataStatus.OK, DataStatus.DEGRADED)
    extra = dict(coverage=0.8, reason="partial coverage") if status is DataStatus.DEGRADED else {}

    if carries_data:
        r = _result(status=status, data=row, **extra)
        assert r.data == row
        # the data content digest is present and verified against the payload.
        assert r.data_content_digest == content_digest(row)
        # a data-bearing status must reject a None payload.
        with pytest.raises(ValidationError):
            _result(status=status, data=None, **extra)
    else:
        r = _result(status=status, data=None)
        assert r.data is None
        # no consumable data => no data content digest.
        assert r.data_content_digest is None
        # a no-data status must reject a consumable payload...
        with pytest.raises(ValidationError):
            _result(status=status, data=row)


def test_ok_requires_typed_data():
    with pytest.raises(ValidationError):
        _result(status=DataStatus.OK, data=None)


def test_no_data_status_rejects_forged_data_content_digest():
    # A no-data status may not smuggle a data content digest either.
    good = _result(status=DataStatus.NO_DATA, data=None)
    with pytest.raises(ValidationError):
        DataResult[OhlcvRow](
            **good.model_dump(exclude={"data_content_digest", "content_digest", "audit_digest"}),
            data_content_digest=content_digest(_row()),
            content_digest=good.content_digest,
            audit_digest=good.audit_digest,
        )


# --------------------------------------------------------------------------- #
# 2. degraded coverage / reason matrix                                        #
# --------------------------------------------------------------------------- #
def test_degraded_requires_coverage_and_reason():
    ok = _result(status=DataStatus.DEGRADED, coverage=0.5, reason="partial")
    assert ok.coverage == 0.5 and ok.degradation_reason == "partial"


@pytest.mark.parametrize(
    "coverage,reason",
    [
        (None, "partial"),   # missing coverage
        (0.5, None),         # missing reason
        (0.5, "   "),        # blank reason
        (0.5, ""),           # empty reason
    ],
)
def test_degraded_rejects_missing_or_blank(coverage, reason):
    with pytest.raises(ValidationError):
        _result(status=DataStatus.DEGRADED, coverage=coverage, reason=reason)


@pytest.mark.parametrize("coverage", [-0.1, 1.1, 2.0])
def test_degraded_coverage_out_of_range_rejected(coverage):
    with pytest.raises(ValidationError):
        _result(status=DataStatus.DEGRADED, coverage=coverage, reason="partial")


def test_reason_only_valid_for_degraded():
    # A non-DEGRADED status must not carry a degradation reason.
    with pytest.raises(ValidationError):
        _result(status=DataStatus.OK, reason="should not be here")


# --------------------------------------------------------------------------- #
# 3. declared content / audit digest mismatch rejection                       #
# --------------------------------------------------------------------------- #
def test_build_roundtrips_and_verifies_on_load():
    r = _result(status=DataStatus.OK)
    reloaded = DataResult[OhlcvRow](**r.model_dump())
    assert reloaded.content_digest == r.content_digest
    assert reloaded.audit_digest == r.audit_digest
    assert r.content_digest == r.semantic_digest()
    assert r.audit_digest == r.audit_digest_value()
    assert r.content_digest != r.audit_digest  # they differ by wall-clock


def test_declared_content_digest_mismatch_rejected():
    r = _result(status=DataStatus.OK)
    payload = r.model_dump()
    payload["content_digest"] = WRONG_DIGEST
    with pytest.raises(ValidationError):
        DataResult[OhlcvRow](**payload)


def test_declared_audit_digest_mismatch_rejected():
    r = _result(status=DataStatus.OK)
    payload = r.model_dump()
    payload["audit_digest"] = WRONG_DIGEST
    with pytest.raises(ValidationError):
        DataResult[OhlcvRow](**payload)


def test_pit_record_declared_digest_mismatch_rejected():
    row = _row()
    payload = row.model_dump()
    payload["content_digest"] = WRONG_DIGEST
    with pytest.raises(ValidationError):
        OhlcvRow(**payload)


def test_data_content_digest_must_match_payload():
    r = _result(status=DataStatus.OK)
    payload = r.model_dump()
    payload["data_content_digest"] = content_digest(_row(close=999.0))  # digest of other data
    # recompute the envelope digests so ONLY the data-content link is wrong.
    with pytest.raises(ValidationError):
        DataResult[OhlcvRow].build(
            **{k: v for k, v in payload.items()
               if k not in ("content_digest", "audit_digest", "data_content_digest")},
            data_content_digest=payload["data_content_digest"],
        )


# --------------------------------------------------------------------------- #
# 4. nested attempt wall-clock changes audit but NOT semantic digest          #
# --------------------------------------------------------------------------- #
def test_nested_attempt_wallclock_is_audit_only():
    a1 = _attempt(started=_dt(9, 0), finished=_dt(9, 1))
    a2 = _attempt(started=_dt(10, 15), finished=_dt(10, 16))  # same outcome/vendor, later clock
    r1 = _result(status=DataStatus.OK, attempts=(a1,))
    r2 = _result(status=DataStatus.OK, attempts=(a2,))
    assert r1.content_digest == r2.content_digest      # semantic unchanged
    assert r1.audit_digest != r2.audit_digest          # audit recurses into wall-clock


def test_result_fetched_at_is_audit_only():
    r1 = _result(status=DataStatus.OK, fetched_at=_dt(9, 32))
    r2 = _result(status=DataStatus.OK, fetched_at=_dt(11, 45))
    assert r1.content_digest == r2.content_digest
    assert r1.audit_digest != r2.audit_digest


def test_attempt_wallclock_excluded_from_its_own_semantic():
    a1 = _attempt(started=_dt(9, 0), finished=_dt(9, 1))
    a2 = _attempt(started=_dt(12, 0), finished=_dt(12, 30))
    assert a1.semantic_digest() == a2.semantic_digest()
    assert a1.audit_digest_value() != a2.audit_digest_value()


# --------------------------------------------------------------------------- #
# 5. attempt outcome / order changes semantic digest                          #
# --------------------------------------------------------------------------- #
def test_attempt_order_changes_semantic_digest():
    a = _attempt(outcome="rate_limited", vendor="mootdx")
    b = _attempt(outcome="success", vendor="sina")
    r_ab = _result(status=DataStatus.OK, attempts=(a, b))
    r_ba = _result(status=DataStatus.OK, attempts=(b, a))
    assert r_ab.content_digest != r_ba.content_digest


def test_attempt_outcome_changes_semantic_digest():
    r_success = _result(status=DataStatus.OK, attempts=(_attempt(outcome="success"),))
    r_error = _result(status=DataStatus.OK, attempts=(_attempt(outcome="error"),))
    assert r_success.content_digest != r_error.content_digest


# --------------------------------------------------------------------------- #
# 6. missing PitRecord availability rejection + guard 'passed' honesty         #
# --------------------------------------------------------------------------- #
def test_pit_record_requires_available_at():
    with pytest.raises(ValidationError):
        OhlcvRow.build(
            ingested_at=_dt(9, 31),
            open=9.5, high=10.5, low=9.0, close=10.0, volume=1000,
        )


def test_pit_record_requires_ingested_at():
    with pytest.raises(ValidationError):
        OhlcvRow.build(
            available_at=_dt(9, 30),
            open=9.5, high=10.5, low=9.0, close=10.0, volume=1000,
        )


def test_guard_passed_forbids_future_rows():
    with pytest.raises(ValidationError):
        _audit(guard="passed", future=1, seen=3, returned=2)


def test_guard_passed_forbids_missing_availability_rows():
    with pytest.raises(ValidationError):
        _audit(guard="passed", missing=1, seen=3, returned=2)


def test_replay_with_future_rows_can_be_refused_or_filtered():
    # The rows exist, but the guard must NOT be 'passed'.
    refused = _audit(guard="refused", future=2, seen=3, returned=0, mode=DataMode.PIT_REPLAY)
    filtered = _audit(guard="filtered", missing=1, seen=3, returned=2, mode=DataMode.PIT_REPLAY)
    assert refused.guard_result == "refused"
    assert filtered.guard_result == "filtered"


# --------------------------------------------------------------------------- #
# 7. negative / incoherent PIT count rejection                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["rows_seen", "rows_returned", "future_rows", "missing_available_at_rows"])
def test_negative_pit_counts_rejected(field):
    kw = dict(mode=DataMode.ONLINE, as_of=_dt(15, 0), rows_seen=2, rows_returned=1,
              future_rows=0, missing_available_at_rows=0, guard_result="passed")
    kw[field] = -1
    with pytest.raises(ValidationError):
        PitAudit(**kw)


@pytest.mark.parametrize(
    "seen,returned,future,missing",
    [
        (2, 3, 0, 0),   # returned > seen
        (2, 1, 3, 0),   # future > seen
        (2, 1, 0, 3),   # missing > seen
    ],
)
def test_incoherent_pit_counts_rejected(seen, returned, future, missing):
    guard = "filtered" if (future or missing) else "passed"
    with pytest.raises(ValidationError):
        PitAudit(
            mode=DataMode.ONLINE, as_of=_dt(15, 0), rows_seen=seen, rows_returned=returned,
            future_rows=future, missing_available_at_rows=missing, guard_result=guard,
        )


# --------------------------------------------------------------------------- #
# 8. naive datetime / non-finite coverage / bool-as-number / extra field      #
# --------------------------------------------------------------------------- #
def test_naive_datetime_rejected_on_attempt():
    with pytest.raises(ValidationError):
        SourceAttempt(vendor="mootdx", configured=True, outcome="success",
                      started_at=datetime(2026, 7, 15, 9, 0), finished_at=_dt(9, 1))


def test_naive_datetime_rejected_on_pit_audit():
    with pytest.raises(ValidationError):
        _audit()  # sanity: aware ok
        PitAudit(mode=DataMode.ONLINE, as_of=datetime(2026, 7, 15, 15, 0),
                 rows_seen=1, rows_returned=1, future_rows=0, missing_available_at_rows=0,
                 guard_result="passed")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_coverage_rejected(bad):
    with pytest.raises(ValidationError):
        _result(status=DataStatus.DEGRADED, coverage=bad, reason="partial")


def test_bool_as_coverage_rejected():
    with pytest.raises(ValidationError):
        _result(status=DataStatus.DEGRADED, coverage=True, reason="partial")


def test_bool_as_pit_count_rejected():
    with pytest.raises(ValidationError):
        PitAudit(mode=DataMode.ONLINE, as_of=_dt(15, 0), rows_seen=True, rows_returned=1,
                 future_rows=0, missing_available_at_rows=0, guard_result="passed")


def test_extra_field_rejected_on_attempt():
    with pytest.raises(ValidationError):
        SourceAttempt(vendor="mootdx", configured=True, outcome="success",
                      started_at=_dt(9, 0), finished_at=_dt(9, 1), bogus=1)


def test_extra_field_rejected_on_data_result():
    r = _result(status=DataStatus.OK)
    with pytest.raises(ValidationError):
        DataResult[OhlcvRow](**r.model_dump(), bogus=1)


# --------------------------------------------------------------------------- #
# 9. immutable records                                                        #
# --------------------------------------------------------------------------- #
def test_source_attempt_is_frozen():
    a = _attempt()
    with pytest.raises(ValidationError):
        a.vendor = "sina"


def test_pit_audit_is_frozen():
    au = _audit()
    with pytest.raises(ValidationError):
        au.rows_seen = 5


def test_pit_record_is_frozen():
    row = _row()
    with pytest.raises(ValidationError):
        row.close = 1.0


def test_data_result_is_frozen():
    r = _result(status=DataStatus.OK)
    with pytest.raises(ValidationError):
        r.status = DataStatus.NO_DATA


# --------------------------------------------------------------------------- #
# Registered PitRecord subtype — a plain dict is not PIT compliance           #
# --------------------------------------------------------------------------- #
def test_registered_pit_subtype_resolves_and_validates():
    reg = SchemaRegistry()
    ref = reg.register(OhlcvRow)
    assert ref.key == "OhlcvRow@1"
    resolved = reg.resolve(ref)
    assert issubclass(resolved, PitRecord)
    row = _row()
    validated = reg.validate_payload(ref, row.model_dump())
    assert isinstance(validated, OhlcvRow)


def test_plain_dict_is_not_accepted_as_typed_data():
    # A generic dict is NOT proof of PIT compliance: strict typed data rejects it.
    raw = {"open": 9.5, "high": 10.5, "low": 9.0, "close": 10.0, "volume": 1000}
    with pytest.raises(ValidationError):
        _result(status=DataStatus.OK, data=raw)


def test_multi_row_payload_of_pit_records():
    rows = (_row(close=10.0), _row(close=11.0))
    result = DataResult[tuple[OhlcvRow, ...]].build(
        id="dr-multi", method="get_ohlcv", request_digest=REQ_DIGEST,
        status=DataStatus.OK, data=rows, coverage=None, degradation_reason=None,
        vendor="mootdx", subsource=None, resolved_vendor_chain=("mootdx",),
        source_config_digest=CFG_DIGEST, effective_at=None,
        available_at=_dt(9, 30), ingested_at=_dt(9, 31), fetched_at=_dt(9, 32),
        revision_id=None,
        attempts=(_attempt(),), pit_audit=_audit(seen=2, returned=2),
    )
    assert len(result.data) == 2
    assert result.data_content_digest == content_digest(rows)
    # the row-time digest is derived from the rows, never caller-asserted.
    assert result.row_time_metadata_digest == row_time_metadata_digest_of(rows)


# --------------------------------------------------------------------------- #
# 10. coverage is DEGRADED-only (symmetric matrix)                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [DataStatus.OK, DataStatus.NO_DATA, DataStatus.STALE, DataStatus.UNAVAILABLE],
)
def test_coverage_rejected_on_every_non_degraded_status(status):
    """A NO_DATA envelope must never seal a misleading coverage=0.9 fraction."""
    with pytest.raises(ValidationError, match="coverage is only valid for DEGRADED"):
        _result(status=status, coverage=0.9)


def test_degraded_with_coverage_and_reason_still_accepted():
    r = _result(status=DataStatus.DEGRADED, coverage=0.5, reason="partial")
    assert r.coverage == 0.5 and r.degradation_reason == "partial"


# --------------------------------------------------------------------------- #
# 11. row_time_metadata_digest is computed + verified, never caller-asserted    #
# --------------------------------------------------------------------------- #
def test_build_computes_a_real_row_time_digest():
    r = _result(status=DataStatus.OK)
    expected = row_time_metadata_digest_of(r.data)
    assert r.row_time_metadata_digest == expected
    assert expected is not None and expected != "0" * 64  # real, not a placeholder


def test_row_time_digest_round_trips_and_reverifies_on_load():
    r = _result(status=DataStatus.OK)
    reloaded = DataResult[OhlcvRow](**r.model_dump())
    assert reloaded.row_time_metadata_digest == r.row_time_metadata_digest


def test_caller_supplied_wrong_row_time_digest_is_rejected():
    # build() seals the envelope digests over the wrong value, so ONLY the
    # row-time verification can (and must) catch it.
    with pytest.raises(ValidationError, match="row time metadata"):
        _result(status=DataStatus.OK, row_time_metadata_digest="0" * 64)


def test_tampered_row_time_metadata_fails_on_load():
    original = _result(status=DataStatus.OK)
    tampered_row = _row(available_at=_dt(11, 45))  # a validly-sealed later row
    fields = {
        n: getattr(original, n)
        for n in type(original).model_fields
        if n not in ("content_digest", "audit_digest", "data_content_digest")
    }
    fields["data"] = tampered_row  # rows' time metadata moved...
    fields["data_content_digest"] = content_digest(tampered_row)
    # ...while the stale row-time digest is retained and the envelope digests
    # are re-sealed coherently — the row-time verification must still fail.
    assert fields["row_time_metadata_digest"] == original.row_time_metadata_digest
    with pytest.raises(ValidationError, match="row time metadata"):
        DataResult[OhlcvRow].build(**fields)


@pytest.mark.parametrize(
    "status", [DataStatus.NO_DATA, DataStatus.STALE, DataStatus.UNAVAILABLE]
)
def test_absent_data_statuses_require_none_row_time_digest(status):
    ok = _result(status=status, data=None)
    assert ok.row_time_metadata_digest is None
    with pytest.raises(ValidationError, match="must be None"):
        _result(status=status, data=None,
                row_time_metadata_digest=content_digest([_dt(9, 30)]))


class _OpaquePayload(DigestModel):
    """A registered-payload stand-in with no derivable PIT rows."""

    schema_version: Literal["1"] = "1"
    note: str


class _MiniBatch(DigestModel):
    """A batch-shaped payload exposing ``rows`` of PitRecords (Phase-3 style)."""

    schema_version: Literal["1"] = "1"
    rows: tuple[OhlcvRow, ...] = ()


def _typed_result(payload_cls, data, **over):
    base = dict(
        id="dr-typed", method="get_ohlcv", request_digest=REQ_DIGEST,
        status=DataStatus.OK, data=data, coverage=None, degradation_reason=None,
        vendor="mootdx", subsource=None, resolved_vendor_chain=("mootdx",),
        source_config_digest=CFG_DIGEST, effective_at=None,
        available_at=_dt(9, 30), ingested_at=_dt(9, 31), fetched_at=_dt(9, 32),
        revision_id=None, attempts=(_attempt(),), pit_audit=_audit(),
    )
    base.update(over)
    return DataResult[payload_cls].build(**base)


def test_opaque_payload_forbids_an_unverifiable_row_time_digest():
    """A payload with no derivable PIT rows can never carry a row-time digest."""
    opaque = _OpaquePayload(note="no rows here")
    ok = _typed_result(_OpaquePayload, opaque)
    assert ok.row_time_metadata_digest is None  # honest: not derivable
    with pytest.raises(ValidationError, match="must be None"):
        _typed_result(_OpaquePayload, opaque,
                      row_time_metadata_digest=content_digest([_dt(9, 30)]))


def test_batch_rows_payloads_derive_the_row_time_digest():
    """The Phase-3 concrete batches (a model exposing rows) are derivable too."""
    batch = _MiniBatch(rows=(_row(close=10.0),))
    r = _typed_result(_MiniBatch, batch)
    assert r.row_time_metadata_digest == row_time_metadata_digest_of(batch)
    assert r.row_time_metadata_digest is not None
    # retiming a row changes the derived digest.
    later = _MiniBatch(rows=(_row(close=10.0, available_at=_dt(12, 0)),))
    assert row_time_metadata_digest_of(later) != row_time_metadata_digest_of(batch)
