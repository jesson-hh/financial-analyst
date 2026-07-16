# -*- coding: utf-8 -*-
"""Phase 2 · Task 2 — audit-only event-refusal sink (``runtime_contracts`` + sink).

Written test-first (RED until the refusal contracts + ``EventRefusalAuditSink``
exist). A refusal is **not** a ``RunEvent``: it receives no journal/visible
sequence, is persisted only in the ``audit`` namespace, never rides a main/public
event and never stores the raw rejected bytes/secrets. The sink is the single
owner of refusal-detail validation, audit persistence and record creation.

Reviewed brief bullets under test:

* strict audit-only ``NamedEvidenceDigest`` / ``GenericRefusalDetails`` /
  ``EventRefusalRecord`` facts; raw rejected bytes/secrets are never fields;
* the sink validates a generic safe detail against its audit-detail registry,
  persists it in the ``audit`` namespace and returns exactly one record;
* a Phase-3-style typed detail schema is accepted through a *later exact registry
  digest* without changing the ``EventRefusalRecord`` wrapper ABI;
* same idempotency key + identical semantic content returns the stored record;
  a same-key semantic conflict raises ``IdempotencyConflict``;
* the refusal cannot be read through the main/public EventStore APIs.

Run: ``python -m pytest tests/orchestration/test_event_refusal.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from guanlan_v2.orchestration.digest import DigestHex, NonEmptyStr
from guanlan_v2.orchestration.refs import CapabilityRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.budget import IdempotencyConflict
from guanlan_v2.orchestration.runtime_clock import clock_now
from guanlan_v2.orchestration.runtime_contracts import (
    AuditDetailRegistry,
    EventRefusalRecord,
    GenericRefusalDetails,
    NamedEvidenceDigest,
    default_audit_detail_registry,
)
from guanlan_v2.orchestration.eventstore import (
    EventAppendRequest,
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64


class AdvancingClock:
    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


def _sink(registry: AuditDetailRegistry | None = None) -> EventRefusalAuditSink:
    return EventRefusalAuditSink(
        detail_registry=registry or default_audit_detail_registry(),
        clock=AdvancingClock(),
    )


def _record(sink: EventRefusalAuditSink, *, idem: str = "ref-1", summary: str = "unresolved schema"):
    return sink.record(
        detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload=GenericRefusalDetails(summary=summary),
        reason_code="schema_unresolved",
        attempted_schema_ref=SchemaRef(name="Nope", version="1"),
        attempted_namespace="main",
        idempotency_key=idem,
    )


# --------------------------------------------------------------------------- #
# 1. strict audit-only fact shapes                                            #
# --------------------------------------------------------------------------- #
def test_named_evidence_digest_is_strict():
    e = NamedEvidenceDigest(name="input_snapshot", digest=DA)
    assert e.name == "input_snapshot" and e.digest == DA
    with pytest.raises(ValidationError):
        NamedEvidenceDigest(name="x", digest="not-a-digest")
    with pytest.raises(ValidationError):
        NamedEvidenceDigest(name="x", digest=DA, extra="nope")


def test_generic_refusal_details_is_a_safe_generic_detail():
    d = GenericRefusalDetails(summary="a validation error occurred")
    assert d.category == "generic"
    with pytest.raises(ValidationError):
        GenericRefusalDetails(summary="x", raw_bytes=b"secret")  # no raw-bytes field


def test_event_refusal_record_rejects_non_audit_detail_namespace():
    with pytest.raises(ValidationError):
        EventRefusalRecord.build(
            record_id="rr-1",
            occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
            reason_code="x",
            detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
            detail_payload_ref=PayloadRef(namespace="main", object_id="o1", content_digest=DA),
            idempotency_key="k1",
        )


def test_event_refusal_record_is_digest_bearing_and_verifies():
    rec = EventRefusalRecord.build(
        record_id="rr-1",
        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
        reason_code="schema_unresolved",
        detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload_ref=PayloadRef(namespace="audit", object_id="o1", content_digest=DA),
        evidence_digests=(NamedEvidenceDigest(name="ctx", digest=DB),),
        idempotency_key="k1",
    )
    # the sealed record_digest round-trips through validation on reload.
    reloaded = EventRefusalRecord.model_validate(rec.model_dump())
    assert reloaded == rec
    # a tampered record_digest is rejected.
    bad = rec.model_dump()
    bad["record_digest"] = DC
    with pytest.raises(ValidationError):
        EventRefusalRecord.model_validate(bad)


def test_record_digest_excludes_audit_identity_but_tracks_semantic_content():
    def build(*, record_id, occurred_at, reason_code):
        return EventRefusalRecord.build(
            record_id=record_id, occurred_at=occurred_at, reason_code=reason_code,
            detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
            detail_payload_ref=PayloadRef(namespace="audit", object_id="o", content_digest=DA),
            idempotency_key="k",
        )
    base = build(record_id="rr-1", occurred_at=datetime(2026, 7, 16, tzinfo=UTC), reason_code="r")
    relocated = build(record_id="rr-2", occurred_at=datetime(2026, 7, 17, tzinfo=UTC), reason_code="r")
    changed = build(record_id="rr-1", occurred_at=datetime(2026, 7, 16, tzinfo=UTC), reason_code="other")
    # audit identity/time do not move the record digest; a semantic change does.
    assert base.record_digest == relocated.record_digest
    assert base.record_digest != changed.record_digest


def test_evidence_digests_must_be_canonically_ordered_and_unique():
    kwargs = dict(
        record_id="rr-1", occurred_at=datetime(2026, 7, 16, tzinfo=UTC), reason_code="r",
        detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload_ref=PayloadRef(namespace="audit", object_id="o", content_digest=DA),
        idempotency_key="k",
    )
    with pytest.raises(ValidationError):
        EventRefusalRecord.build(
            evidence_digests=(
                NamedEvidenceDigest(name="zeta", digest=DA),
                NamedEvidenceDigest(name="alpha", digest=DB),
            ),
            **kwargs,
        )
    with pytest.raises(ValidationError):
        EventRefusalRecord.build(
            evidence_digests=(
                NamedEvidenceDigest(name="alpha", digest=DA),
                NamedEvidenceDigest(name="alpha", digest=DB),
            ),
            **kwargs,
        )


# --------------------------------------------------------------------------- #
# 2. the sink records exactly one typed audit record with a safe detail        #
# --------------------------------------------------------------------------- #
def test_sink_records_one_audit_record_with_audit_detail():
    sink = _sink()
    rec = _record(sink)
    assert isinstance(rec, EventRefusalRecord)
    assert rec.reason_code == "schema_unresolved"
    # the typed detail is persisted in the audit namespace.
    assert rec.detail_schema_ref.key == "GenericRefusalDetails@1"
    assert rec.detail_payload_ref.namespace == "audit"
    # exactly one record was persisted.
    assert len(sink.records()) == 1
    assert sink.records()[0].record_digest == rec.record_digest


def test_sink_binds_attempted_capability_schema_namespace_and_evidence():
    sink = _sink()
    rec = sink.record(
        detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload=GenericRefusalDetails(summary="capability not allowed"),
        reason_code="capability_denied",
        attempted_capability_ref=CapabilityRef(id="cap.x", version="1", content_digest=DA),
        attempted_schema_ref=SchemaRef(name="SentimentReport", version="1"),
        attempted_namespace="sealed",
        evidence_digests=(NamedEvidenceDigest(name="worker", digest=DB),),
        idempotency_key="ref-cap",
    )
    assert rec.attempted_capability_ref.id == "cap.x"
    assert rec.attempted_schema_ref.key == "SentimentReport@1"
    assert rec.attempted_namespace == "sealed"
    assert rec.evidence_digests[0].name == "worker"


def test_sink_rejects_a_detail_payload_that_fails_its_schema():
    sink = _sink()
    # the detail schema resolves to GenericRefusalDetails; a mismatched payload
    # (extra field / wrong type) is rejected by the audit-detail registry.
    with pytest.raises(ValidationError):
        sink.record(
            detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
            detail_payload={"summary": "x", "leaked_secret": "abc"},
            reason_code="r",
            idempotency_key="ref-bad",
        )
    assert sink.records() == ()  # nothing persisted on a rejected detail


def test_sink_rejects_unknown_detail_schema():
    sink = _sink()
    with pytest.raises(Exception):
        sink.record(
            detail_schema_ref=SchemaRef(name="NotRegisteredDetail", version="1"),
            detail_payload=GenericRefusalDetails(summary="x"),
            reason_code="r",
            idempotency_key="ref-unknown",
        )
    assert sink.records() == ()


# --------------------------------------------------------------------------- #
# 3. Phase-3-style typed detail through a later exact registry digest           #
# --------------------------------------------------------------------------- #
class _Phase3TypedDetail(BaseModel):
    """A stand-in for a later Phase-3 typed refusal detail schema."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["1"] = "1"
    tool_name: NonEmptyStr
    bound_digest: DigestHex


def test_phase3_typed_detail_accepted_through_a_later_registry_without_abi_change():
    base = default_audit_detail_registry()
    later = default_audit_detail_registry()
    later.register(_Phase3TypedDetail)
    later.seal()
    # the later registry is a distinct exact digest — no "latest" aliasing.
    assert later.registry_digest != base.registry_digest

    sink = EventRefusalAuditSink(detail_registry=later, clock=AdvancingClock())
    rec = sink.record(
        detail_schema_ref=SchemaRef(name="_Phase3TypedDetail", version="1"),
        detail_payload=_Phase3TypedDetail(tool_name="get_quote", bound_digest=DA),
        reason_code="tool_bound_unsupported",
        idempotency_key="ref-p3",
    )
    # the wrapper ABI is unchanged: it is still an EventRefusalRecord binding a
    # typed detail SchemaRef + audit PayloadRef.
    assert isinstance(rec, EventRefusalRecord)
    assert rec.detail_schema_ref.key == "_Phase3TypedDetail@1"
    assert rec.detail_payload_ref.namespace == "audit"
    # the base registry cannot resolve the Phase-3 detail schema (no reinterpret).
    with pytest.raises(Exception):
        base.resolve(SchemaRef(name="_Phase3TypedDetail", version="1"))


# --------------------------------------------------------------------------- #
# 4. idempotency: identical retry vs semantic conflict                         #
# --------------------------------------------------------------------------- #
def test_identical_refusal_retry_returns_the_stored_record():
    sink = _sink()
    r1 = _record(sink, idem="k1", summary="same detail")
    r2 = _record(sink, idem="k1", summary="same detail")
    assert r1.record_digest == r2.record_digest
    assert r1.record_id == r2.record_id
    assert len(sink.records()) == 1  # not re-appended


def test_conflicting_refusal_idempotency_key_raises():
    sink = _sink()
    _record(sink, idem="k1", summary="first detail")
    with pytest.raises(IdempotencyConflict):
        _record(sink, idem="k1", summary="different detail")


def test_concurrent_same_key_records_persist_exactly_once():
    """The probe + append is serialized under the sink's lock, so concurrent
    identical same-key records can never double-append past the probe."""
    import threading

    sink = _sink()
    results, errors = [], []

    def worker():
        try:
            results.append(_record(sink, idem="k-conc", summary="same detail"))
        except Exception as exc:  # pragma: no cover - would fail the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(sink.records()) == 1  # exactly one persisted record
    assert len({r.record_id for r in results}) == 1  # everyone got the same record


# --------------------------------------------------------------------------- #
# 5. refusals never reach the main/public stream                               #
# --------------------------------------------------------------------------- #
def test_refused_append_leaves_the_run_journal_unchanged_and_records_one_refusal():
    reg = default_registry()
    resolver = SchemaRegistryResolver()
    digest = resolver.register(reg)
    stores = RuntimeStores(resolver=resolver, clock=AdvancingClock())
    sink = _sink()

    # an append whose payload ref does not exist is rejected by the EventStore.
    bad_request = EventAppendRequest(
        run_id="run-1", partition="main", event_type="ArtifactStaged",
        payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="ghost", content_digest=DA),
        registry_digest=digest, idempotency_key="ev-bad",
    )
    with pytest.raises(Exception):
        stores.events.append(bad_request)

    # the rejection is recorded only in the audit-only sink, never in the journal.
    sink.record(
        detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload=GenericRefusalDetails(summary="payload ref missing"),
        reason_code="payload_missing",
        attempted_namespace="main",
        idempotency_key="ref-journal",
    )
    assert stores.events.journal("run-1", "main") == ()
    assert stores.events.visible("run-1", "main") == ()
    assert len(sink.records()) == 1
    # the audit detail ref is not a main/public payload and carries no journal/visible seq.
    rec = sink.records()[0]
    assert rec.detail_payload_ref.namespace == "audit"
    assert not hasattr(rec, "journal_seq") and not hasattr(rec, "visible_seq")
