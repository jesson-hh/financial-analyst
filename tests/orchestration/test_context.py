# -*- coding: utf-8 -*-
"""Task 8 — context / clock / immutable snapshot contracts (``context.py``).

Written test-first: with ``guanlan_v2/orchestration/context.py`` absent the
module fails to import (RED). It then locks, GREEN, the reviewed invariants:

* :class:`ClockSpec` / :class:`DataContext` are frozen, strict, versioned
  ``DigestModel``s; top-level ``as_of`` and ``clock.as_of`` are the *same
  instant* and ``calendar_id`` matches ``clock.calendar_id``; every datetime is
  tz-aware and canonicalized to UTC (naive rejected);
* the ``ONLINE`` / ``PIT_REPLAY`` mode matrix: both require a real snapshot
  locator + content digest; ``PIT_REPLAY`` additionally requires
  ``strict_pit=True``, a non-LIVE backend and a vintage manifest digest, while
  ``ONLINE`` may use a frozen capture-root digest with no vintage;
* the storage-only ``data_snapshot_id`` and the builder wall-clock are audit —
  relocating identical content under a new id leaves the semantic/context digest
  stable — while source/registry/routing/chain/snapshot-content/vintage are
  semantic and move it;
* :class:`MemoryRecordRef` strict field/time/digest validation, canonical
  :class:`InputSnapshot` ordering, duplicate + same-record/revision conflict
  rejection, and full-ref (incl. ``available_at``) semantic sensitivity;
* the canonical empty-memory builder / digests, the persisted locator/ref
  binding, the ``memory_selection_ref`` namespace + content-digest matrix,
  pre-facade ``memory_session_id=None`` and rejection of caller/worker session
  widening;
* every snapshot ``content_digest`` excludes itself and is computed/verified by a
  pure builder (a declared mismatch is rejected on load); mutation is rejected.

Run from repo root: ``pytest tests/orchestration/test_context.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schemas import ArtifactRef
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextRuntimeRequirements,
    ContextSnapshot,
    DataContext,
    EmptyMemorySelection,
    EmptyMemorySnapshot,
    InputArtifactBinding,
    InputSnapshot,
    MemoryRecordRef,
    RunBudget,
    RunContext,
    build_empty_memory_binding,
    check_memory_session_scope,
    compute_context_subject_digest,
    verify_context_runtime_requirements,
    verify_memory_record_ref,
)

UTC = timezone.utc
CST = timezone(timedelta(hours=8))  # Asia/Shanghai fixed offset

# Well-formed but distinct sha256 hex digests used as opaque referenced digests.
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
DE = "e" * 64
DF = "f" * 64
D1 = "1" * 64
D2 = "2" * 64
WRONG = "0" * 64


def _dt(hour: int = 1, minute: int = 30, *, tz: timezone = UTC) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=tz)


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def _clock(
    *,
    as_of: datetime | None = None,
    calendar_id: str = "cn_a_share",
    tz: str = "Asia/Shanghai",
) -> ClockSpec:
    return ClockSpec(
        as_of=as_of if as_of is not None else _dt(1, 30),
        timezone=tz,
        calendar_id=calendar_id,
        clock_version="1",
    )


def _data_context(
    *,
    mode: DataMode = DataMode.ONLINE,
    backend: DataBackend = DataBackend.CACHE,
    strict_pit: bool = False,
    as_of: datetime | None = None,
    clock_as_of: datetime | None = None,
    calendar_id: str = "cn_a_share",
    clock_calendar_id: str | None = None,
    vintage_manifest_digest: str | None = None,
    resolved_vendor_chains=None,
    source_config_digest: str = DA,
    source_registry_digest: str = DB,
    routing_snapshot_digest: str = DC,
    data_snapshot_id: str = "snap-1",
    data_snapshot_content_digest: str = DD,
    built_at: datetime | None = None,
    **over,
) -> DataContext:
    top_as_of = as_of if as_of is not None else _dt(1, 30)
    clk = _clock(
        as_of=clock_as_of if clock_as_of is not None else top_as_of,
        calendar_id=clock_calendar_id if clock_calendar_id is not None else calendar_id,
    )
    if resolved_vendor_chains is None:
        resolved_vendor_chains = {"prices": ("tushare", "eastmoney")}
    base = dict(
        as_of=top_as_of,
        clock=clk,
        mode=mode,
        backend=backend,
        strict_pit=strict_pit,
        calendar_id=calendar_id,
        resolved_vendor_chains=resolved_vendor_chains,
        source_config_digest=source_config_digest,
        source_registry_digest=source_registry_digest,
        routing_snapshot_digest=routing_snapshot_digest,
        data_snapshot_id=data_snapshot_id,
        data_snapshot_content_digest=data_snapshot_content_digest,
        vintage_manifest_digest=vintage_manifest_digest,
        built_at=built_at if built_at is not None else _dt(3, 0),
    )
    base.update(over)
    return DataContext(**base)


def _online_ctx(**over) -> DataContext:
    kw = dict(mode=DataMode.ONLINE, backend=DataBackend.LIVE, strict_pit=False)
    kw.update(over)
    return _data_context(**kw)


def _pit_ctx(**over) -> DataContext:
    kw = dict(
        mode=DataMode.PIT_REPLAY,
        backend=DataBackend.PIT_STORE,
        strict_pit=True,
        vintage_manifest_digest=DE,
    )
    kw.update(over)
    return _data_context(**kw)


def _mem_ref(
    *,
    record_id: str = "mem.alpha",
    revision_id: str = "r1",
    available_at: datetime | None = None,
    content_digest: str = DA,
) -> MemoryRecordRef:
    return MemoryRecordRef(
        record_id=record_id,
        revision_id=revision_id,
        available_at=available_at if available_at is not None else _dt(0, 0),
        content_digest=content_digest,
    )


def _typed_ref(
    schema_name: str,
    content_digest_hex: str,
    object_id: str,
    *,
    version: str = "1",
    namespace: str = "main",
) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=schema_name, version=version),
        payload_ref=PayloadRef(
            namespace=namespace, object_id=object_id, content_digest=content_digest_hex
        ),
    )


def _runtime_requirements_ref(
    content_digest_hex: str = DE, *, object_id: str = "rr-obj-1", namespace: str = "main"
) -> TypedPayloadRef:
    return _typed_ref(
        "ContextRuntimeRequirements", content_digest_hex, object_id, namespace=namespace
    )


def _context_snapshot(
    *,
    data_context: DataContext | None = None,
    snapshot_id: str = "ctx-1",
    memory_snapshot_id: str = "memsnap-1",
    memory_snapshot_hash: str | None = None,
    past_context_hash: str | None = None,
    snapshot_object_id: str = "snap-obj-1",
    snapshot_namespace: str = "main",
    snapshot_content_digest: str | None = None,
    selection_object_id: str = "sel-obj-1",
    selection_namespace: str = "main",
    selection_content_digest: str | None = None,
    runtime_requirements_ref: TypedPayloadRef | None = None,
    memory_session_id: str | None = None,
    built_at: datetime | None = None,
) -> ContextSnapshot:
    binding = build_empty_memory_binding()
    dc = data_context if data_context is not None else _online_ctx()
    msh = memory_snapshot_hash if memory_snapshot_hash is not None else binding.snapshot_hash
    pch = past_context_hash if past_context_hash is not None else binding.past_context_hash
    snap_cd = snapshot_content_digest if snapshot_content_digest is not None else msh
    sel_cd = selection_content_digest if selection_content_digest is not None else pch
    snap_ref = _typed_ref(
        "EmptyMemorySnapshot", snap_cd, snapshot_object_id, namespace=snapshot_namespace
    )
    sel_ref = _typed_ref(
        "EmptyMemorySelection", sel_cd, selection_object_id, namespace=selection_namespace
    )
    return ContextSnapshot.build(
        snapshot_id=snapshot_id,
        data_context=dc,
        memory_snapshot_id=memory_snapshot_id,
        memory_snapshot_hash=msh,
        past_context_hash=pch,
        memory_snapshot_ref=snap_ref,
        memory_selection_ref=sel_ref,
        runtime_requirements_ref=runtime_requirements_ref,
        memory_session_id=memory_session_id,
        built_at=built_at if built_at is not None else _dt(3, 5),
    )


def _non_empty_context_snapshot(
    *,
    memory_snapshot_hash: str = DA,
    past_context_hash: str = DB,
    snapshot_schema: str = "EmptyMemorySnapshot",
    selection_schema: str = "EmptyMemorySelection",
    snapshot_object_id: str = "snap-obj-1",
    selection_object_id: str = "sel-obj-1",
    requirements_object_id: str = "rr-obj-1",
    requirements_content_digest: str = DE,
    memory_session_id: str | None = None,
) -> ContextSnapshot:
    """A ContextSnapshot bound to a *non*-canonical-empty memory pair.

    A non-empty (or different-schema) memory binding is no longer the canonical
    empty pair, so it must carry a ``runtime_requirements_ref``.
    """
    snap_ref = _typed_ref(snapshot_schema, memory_snapshot_hash, snapshot_object_id)
    sel_ref = _typed_ref(selection_schema, past_context_hash, selection_object_id)
    rr_ref = _runtime_requirements_ref(
        requirements_content_digest, object_id=requirements_object_id
    )
    return ContextSnapshot.build(
        snapshot_id="ctx-1",
        data_context=_online_ctx(),
        memory_snapshot_id="memsnap-1",
        memory_snapshot_hash=memory_snapshot_hash,
        past_context_hash=past_context_hash,
        memory_snapshot_ref=snap_ref,
        memory_selection_ref=sel_ref,
        runtime_requirements_ref=rr_ref,
        memory_session_id=memory_session_id,
        built_at=_dt(3, 5),
    )


# --------------------------------------------------------------------------- #
# base contract                                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cls",
    [ClockSpec, DataContext, MemoryRecordRef, EmptyMemorySnapshot, EmptyMemorySelection,
     ContextSnapshot, InputSnapshot, RunContext],
)
def test_all_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


def test_happy_online_and_pit_contexts_build():
    assert _online_ctx().mode is DataMode.ONLINE
    assert _pit_ctx().mode is DataMode.PIT_REPLAY


# --------------------------------------------------------------------------- #
# 1. online + PIT mode matrices                                               #
# --------------------------------------------------------------------------- #
def test_online_allows_live_backend_and_no_vintage():
    ctx = _online_ctx(vintage_manifest_digest=None)
    assert ctx.backend is DataBackend.LIVE
    assert ctx.vintage_manifest_digest is None


def test_online_may_carry_capture_root_without_vintage():
    # ONLINE freezes a real snapshot locator + content digest (capture root) but
    # does not have to claim a vintage manifest / live source isolation.
    ctx = _online_ctx(strict_pit=False, backend=DataBackend.CACHE)
    assert ctx.data_snapshot_id
    assert ctx.data_snapshot_content_digest == DD


def test_pit_replay_requires_strict_pit():
    with pytest.raises(ValidationError):
        _data_context(
            mode=DataMode.PIT_REPLAY,
            backend=DataBackend.PIT_STORE,
            strict_pit=False,
            vintage_manifest_digest=DE,
        )


def test_pit_replay_rejects_live_backend():
    with pytest.raises(ValidationError):
        _data_context(
            mode=DataMode.PIT_REPLAY,
            backend=DataBackend.LIVE,
            strict_pit=True,
            vintage_manifest_digest=DE,
        )


def test_pit_replay_requires_vintage_manifest_digest():
    with pytest.raises(ValidationError):
        _data_context(
            mode=DataMode.PIT_REPLAY,
            backend=DataBackend.PIT_STORE,
            strict_pit=True,
            vintage_manifest_digest=None,
        )


@pytest.mark.parametrize("backend", [DataBackend.PIT_STORE, DataBackend.CACHE])
def test_pit_replay_accepts_non_live_backends(backend):
    ctx = _pit_ctx(backend=backend)
    assert ctx.backend is backend
    assert ctx.strict_pit is True
    assert ctx.vintage_manifest_digest == DE


# --------------------------------------------------------------------------- #
# 2. equal-instant clock/context handling + naive rejection                   #
# --------------------------------------------------------------------------- #
def test_equal_instant_different_offset_is_accepted():
    # top-level as_of in UTC and clock.as_of in +08:00 name the SAME instant.
    top = _dt(1, 30, tz=UTC)          # 01:30Z
    same = _dt(9, 30, tz=CST)         # 09:30+08 == 01:30Z
    assert top == same
    ctx = _data_context(as_of=top, clock_as_of=same)
    assert ctx.as_of == ctx.clock.as_of


def test_different_instant_clock_and_context_rejected():
    with pytest.raises(ValidationError):
        _data_context(as_of=_dt(1, 30), clock_as_of=_dt(2, 30))


def test_calendar_id_must_match_clock():
    with pytest.raises(ValidationError):
        _data_context(calendar_id="cn_a_share", clock_calendar_id="us_equity")


def test_naive_context_as_of_rejected():
    with pytest.raises(ValidationError):
        _data_context(as_of=datetime(2026, 7, 15, 1, 30), clock_as_of=_dt(1, 30))


def test_naive_clock_as_of_rejected():
    with pytest.raises(ValidationError):
        ClockSpec(
            as_of=datetime(2026, 7, 15, 1, 30),
            timezone="Asia/Shanghai",
            calendar_id="cn_a_share",
            clock_version="1",
        )


def test_context_datetimes_normalized_to_utc():
    ctx = _data_context(as_of=_dt(9, 30, tz=CST), clock_as_of=_dt(9, 30, tz=CST))
    assert ctx.as_of.tzinfo == UTC
    assert ctx.as_of.hour == 1  # 09:30 +08 -> 01:30Z


# --------------------------------------------------------------------------- #
# 4. complete field/mode/backend matrix (empty reviewed routing)              #
# --------------------------------------------------------------------------- #
def test_empty_reviewed_routing_uses_real_digests_not_blanks():
    # an empty data setup still uses reviewed (non-blank) registry/config/routing
    # digests — blank placeholders are rejected by the strict digest types.
    ctx = _online_ctx(resolved_vendor_chains={})
    assert ctx.resolved_vendor_chains == {}
    assert ctx.source_registry_digest == DB
    with pytest.raises(ValidationError):
        _online_ctx(source_registry_digest="")


def test_context_forbids_extra_field():
    with pytest.raises(ValidationError):
        _online_ctx(pit_guard={"root": "x"})


def test_context_is_frozen():
    ctx = _online_ctx()
    with pytest.raises(ValidationError):
        ctx.strict_pit = True


# --------------------------------------------------------------------------- #
# 5. relocation stability vs semantic sensitivity                             #
# --------------------------------------------------------------------------- #
def test_data_snapshot_id_relocation_leaves_data_context_semantic_stable():
    a = _online_ctx(data_snapshot_id="snap-1")
    b = _online_ctx(data_snapshot_id="snap-99")
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_builder_wall_clock_is_audit_only_on_data_context():
    a = _online_ctx(built_at=_dt(3, 0))
    b = _online_ctx(built_at=_dt(4, 0))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


@pytest.mark.parametrize(
    "over",
    [
        {"source_config_digest": DF},
        {"source_registry_digest": DF},
        {"routing_snapshot_digest": DF},
        {"data_snapshot_content_digest": DF},
        {"vintage_manifest_digest": DF},
        {"resolved_vendor_chains": {"prices": ("eastmoney",)}},
    ],
)
def test_semantic_fields_move_data_context_digest(over):
    base = _online_ctx()
    changed = _online_ctx(**over)
    assert base.semantic_digest() != changed.semantic_digest()


def test_context_snapshot_stable_under_data_snapshot_relocation():
    a = _context_snapshot(data_context=_online_ctx(data_snapshot_id="snap-1"))
    b = _context_snapshot(data_context=_online_ctx(data_snapshot_id="snap-99"))
    assert a.content_digest == b.content_digest


def test_context_snapshot_stable_under_memory_snapshot_id_relocation():
    a = _context_snapshot(memory_snapshot_id="memsnap-1")
    b = _context_snapshot(memory_snapshot_id="memsnap-99")
    assert a.content_digest == b.content_digest


def test_context_snapshot_stable_under_selection_object_id_relocation():
    a = _context_snapshot(selection_object_id="sel-obj-1")
    b = _context_snapshot(selection_object_id="sel-obj-99")
    assert a.content_digest == b.content_digest


def test_context_snapshot_stable_under_snapshot_ref_object_id_relocation():
    # relocating the byte-identical memory snapshot payload under a new object id
    # touches only the audit-only nested object_id, never the content digest.
    a = _context_snapshot(snapshot_object_id="snap-obj-1")
    b = _context_snapshot(snapshot_object_id="snap-obj-99")
    assert a.content_digest == b.content_digest


def test_context_snapshot_stable_under_requirements_ref_object_id_relocation():
    # a non-empty memory binding carries a runtime_requirements_ref; relocating its
    # payload (object_id churn only) cannot move the ContextSnapshot content digest.
    a = _non_empty_context_snapshot(requirements_object_id="rr-obj-1")
    b = _non_empty_context_snapshot(requirements_object_id="rr-obj-99")
    assert a.content_digest == b.content_digest


def test_context_snapshot_moves_on_source_registry_change():
    a = _context_snapshot(data_context=_online_ctx(source_registry_digest=DB))
    b = _context_snapshot(data_context=_online_ctx(source_registry_digest=DF))
    assert a.content_digest != b.content_digest


def test_context_snapshot_moves_on_memory_snapshot_hash_change():
    a = _context_snapshot()
    # a changed memory snapshot hash is no longer the canonical empty pair, so it
    # must carry a runtime_requirements_ref; the digest still moves on the hash.
    b = _context_snapshot(memory_snapshot_hash=DF, runtime_requirements_ref=_runtime_requirements_ref())
    assert a.content_digest != b.content_digest


def test_context_snapshot_moves_on_past_context_hash_change():
    a = _context_snapshot()
    # past_context_hash is bound by the selection ref content digest too.
    b = _context_snapshot(
        past_context_hash=DF,
        selection_content_digest=DF,
        runtime_requirements_ref=_runtime_requirements_ref(),
    )
    assert a.content_digest != b.content_digest


def test_context_snapshot_moves_on_runtime_requirements_ref_content_change():
    # the requirements ref's referenced content is semantic: two otherwise-equal
    # non-empty snapshots that bind different requirements payloads differ.
    a = _non_empty_context_snapshot(requirements_content_digest=DE)
    b = _non_empty_context_snapshot(requirements_content_digest=DF)
    assert a.content_digest != b.content_digest


def test_context_snapshot_moves_on_memory_snapshot_ref_schema_change():
    # exact SchemaRef of the memory snapshot ref is semantic (a Phase 3 non-empty
    # schema replays differently from the canonical empty schema).
    a = _non_empty_context_snapshot(snapshot_schema="EmptyMemorySnapshot")
    b = _non_empty_context_snapshot(snapshot_schema="MemorySnapshot")
    assert a.content_digest != b.content_digest


def test_context_snapshot_moves_on_memory_session_scope_change():
    a = _context_snapshot(memory_session_id=None)
    b = _context_snapshot(memory_session_id="sess.desk_a")
    assert a.content_digest != b.content_digest


# --------------------------------------------------------------------------- #
# 3. snapshot mutation + declared digest mismatch rejection                   #
# --------------------------------------------------------------------------- #
def test_context_snapshot_roundtrips_and_verifies_on_load():
    a = _context_snapshot()
    reloaded = ContextSnapshot(**a.model_dump())
    assert reloaded.content_digest == a.content_digest


def test_context_snapshot_declared_digest_mismatch_rejected():
    a = _context_snapshot()
    payload = a.model_dump()
    payload["content_digest"] = WRONG
    with pytest.raises(ValidationError):
        ContextSnapshot(**payload)


def test_context_snapshot_is_frozen():
    a = _context_snapshot()
    with pytest.raises(ValidationError):
        a.memory_snapshot_hash = DF


def test_context_snapshot_selection_ref_namespace_must_be_main():
    with pytest.raises(ValidationError):
        _context_snapshot(selection_namespace="review")


def test_context_snapshot_selection_ref_content_must_equal_past_context_hash():
    with pytest.raises(ValidationError):
        _context_snapshot(selection_content_digest=DF)  # != past_context_hash


def test_context_snapshot_snapshot_ref_namespace_must_be_main():
    with pytest.raises(ValidationError):
        _context_snapshot(snapshot_namespace="sealed")


def test_context_snapshot_snapshot_ref_content_must_equal_memory_snapshot_hash():
    with pytest.raises(ValidationError):
        _context_snapshot(snapshot_content_digest=DF)  # != memory_snapshot_hash


def test_context_snapshot_empty_pair_forbids_runtime_requirements_ref():
    # the canonical empty pair must have runtime_requirements_ref=None.
    with pytest.raises(ValidationError):
        _context_snapshot(runtime_requirements_ref=_runtime_requirements_ref())


def test_context_snapshot_non_empty_pair_requires_runtime_requirements_ref():
    # a non-empty memory binding without a requirements ref is rejected.
    binding = build_empty_memory_binding()
    snap_ref = _typed_ref("MemorySnapshot", DA, "snap-obj-1")
    sel_ref = _typed_ref("MemorySelection", DB, "sel-obj-1")
    with pytest.raises(ValidationError):
        ContextSnapshot.build(
            snapshot_id="ctx-1", data_context=_online_ctx(), memory_snapshot_id="ms-1",
            memory_snapshot_hash=DA, past_context_hash=DB,
            memory_snapshot_ref=snap_ref, memory_selection_ref=sel_ref,
            runtime_requirements_ref=None, built_at=_dt(3, 5),
        )
    assert binding.snapshot_hash != DA  # sanity: DA is not the canonical empty hash


def test_context_snapshot_non_empty_pair_builds_with_requirements_ref():
    snap = _non_empty_context_snapshot()
    assert snap.runtime_requirements_ref is not None
    assert snap.content_digest == snap.semantic_digest()


def test_context_snapshot_runtime_requirements_ref_must_pin_requirements_schema():
    # a present requirements ref must resolve ContextRuntimeRequirements@1.
    bad = _typed_ref("NotRequirements", DE, "rr-obj-1")
    with pytest.raises(ValidationError):
        _context_snapshot(  # empty pair would forbid it anyway; make it non-empty
            memory_snapshot_hash=DA,
            snapshot_content_digest=DA,
            past_context_hash=DB,
            selection_content_digest=DB,
            runtime_requirements_ref=bad,
        )


def test_context_snapshot_runtime_requirements_ref_namespace_must_be_main():
    bad = _runtime_requirements_ref(namespace="review")
    with pytest.raises(ValidationError):
        _context_snapshot(
            memory_snapshot_hash=DA,
            snapshot_content_digest=DA,
            past_context_hash=DB,
            selection_content_digest=DB,
            runtime_requirements_ref=bad,
        )


# --------------------------------------------------------------------------- #
# 6. MemoryRecordRef strict validation                                        #
# --------------------------------------------------------------------------- #
def test_memory_record_ref_happy():
    ref = _mem_ref()
    assert ref.record_id == "mem.alpha"
    assert ref.available_at.tzinfo == UTC


def test_memory_record_ref_rejects_pathlike_record_id():
    with pytest.raises(ValidationError):
        _mem_ref(record_id="C:/memory/alpha.json")


def test_memory_record_ref_rejects_naive_available_at():
    with pytest.raises(ValidationError):
        _mem_ref(available_at=datetime(2026, 7, 15, 0, 0))


def test_memory_record_ref_rejects_bad_content_digest():
    with pytest.raises(ValidationError):
        _mem_ref(content_digest="not-a-digest")


def test_memory_record_ref_rejects_extra_field():
    with pytest.raises(ValidationError):
        MemoryRecordRef(
            record_id="mem.alpha", revision_id="r1", available_at=_dt(0, 0),
            content_digest=DA, score=0.9,
        )


def test_memory_record_ref_is_frozen():
    ref = _mem_ref()
    with pytest.raises(ValidationError):
        ref.revision_id = "r2"


# --------------------------------------------------------------------------- #
# 6. InputSnapshot canonical ordering + duplicate/conflict                    #
# --------------------------------------------------------------------------- #
def _ctx_ref(
    *, content_digest_hex: str = D1, object_id: str = "ctx-obj-1",
    namespace: str = "main", schema_name: str = "ContextSnapshot",
) -> TypedPayloadRef:
    return _typed_ref(schema_name, content_digest_hex, object_id, namespace=namespace)


def _data_ref(
    schema_name: str, content_digest_hex: str, object_id: str = "dr-obj-1",
    *, namespace: str = "main",
) -> TypedPayloadRef:
    return _typed_ref(schema_name, content_digest_hex, object_id, namespace=namespace)


def _artifact_ref(
    *, output_key: str = "primary", content_digest_hex: str = DA, artifact_id: str = "art-1",
    schema_name: str = "ResearchPlan", producer: str = "up.node", slot: str = "slot-1",
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        schema_ref=SchemaRef(name=schema_name, version="1"),
        producer_node_id=producer,
        slot=slot,
        output_key=output_key,
        content_digest=content_digest_hex,
    )


def _binding(
    input_name: str, cardinality: str, refs: tuple[ArtifactRef, ...] = ()
) -> InputArtifactBinding:
    return InputArtifactBinding(
        input_name=input_name, cardinality=cardinality, artifact_refs=refs
    )


def _input_snapshot(refs=(), **over) -> InputSnapshot:
    base = dict(
        snapshot_id="in-1",
        run_id="run-1",
        plan_id="plan-1",
        plan_digest=D2,
        node_id="node-1",
        layer_index=0,
        attempt=1,
        context_snapshot_ref=_ctx_ref(),
        artifact_inputs=(),
        data_result_refs=(),
        memory_record_refs=tuple(refs),
        readiness="ready",
        missing_input_names=(),
        built_at=_dt(3, 10),
    )
    base.update(over)
    return InputSnapshot.build(**base)


def test_input_snapshot_accepts_canonical_order():
    refs = (
        _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA),
        _mem_ref(record_id="mem.alpha", revision_id="r2", content_digest=DB),
        _mem_ref(record_id="mem.beta", revision_id="r1", content_digest=DC),
    )
    snap = _input_snapshot(refs)
    assert len(snap.memory_record_refs) == 3


def test_input_snapshot_rejects_non_canonical_order():
    refs = (
        _mem_ref(record_id="mem.beta", revision_id="r1", content_digest=DC),
        _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA),
    )
    with pytest.raises(ValidationError):
        _input_snapshot(refs)


def test_input_snapshot_rejects_exact_duplicate():
    r = _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA)
    with pytest.raises(ValidationError):
        _input_snapshot((r, r))


def test_input_snapshot_rejects_same_record_revision_conflict():
    # same (record_id, revision_id) but different content/availability is a
    # conflict, not two records.
    refs = (
        _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA,
                 available_at=_dt(0, 0)),
        _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DB,
                 available_at=_dt(0, 5)),
    )
    with pytest.raises(ValidationError):
        _input_snapshot(refs)


def test_input_snapshot_full_ref_available_at_is_semantic():
    # available_at is not part of the ordering key but IS part of the semantic
    # projection (full refs) — changing it moves the snapshot content digest.
    a = _input_snapshot((_mem_ref(available_at=_dt(0, 0)),))
    b = _input_snapshot((_mem_ref(available_at=_dt(0, 5)),))
    assert a.content_digest != b.content_digest


def test_input_snapshot_declared_digest_mismatch_rejected():
    snap = _input_snapshot((_mem_ref(),))
    payload = snap.model_dump()
    payload["content_digest"] = WRONG
    with pytest.raises(ValidationError):
        InputSnapshot(**payload)


def test_input_snapshot_snapshot_id_is_audit_only():
    a = _input_snapshot((_mem_ref(),), snapshot_id="in-1")
    b = _input_snapshot((_mem_ref(),), snapshot_id="in-99")
    assert a.content_digest == b.content_digest


# --------------------------------------------------------------------------- #
# 6. InputSnapshot redesigned ABI — context ref / data refs / artifacts        #
# --------------------------------------------------------------------------- #
def test_input_snapshot_happy_ready_builds():
    snap = _input_snapshot()
    assert snap.readiness == "ready"
    assert snap.layer_index == 0
    assert snap.attempt == 1
    assert snap.content_digest == snap.semantic_digest()


def test_input_snapshot_context_ref_namespace_must_be_main():
    with pytest.raises(ValidationError):
        _input_snapshot(context_snapshot_ref=_ctx_ref(namespace="sealed"))


def test_input_snapshot_context_ref_must_pin_context_snapshot_schema():
    with pytest.raises(ValidationError):
        _input_snapshot(context_snapshot_ref=_ctx_ref(schema_name="NotContextSnapshot"))


def test_input_snapshot_context_ref_object_relocation_is_audit_only():
    a = _input_snapshot(context_snapshot_ref=_ctx_ref(object_id="ctx-obj-1"))
    b = _input_snapshot(context_snapshot_ref=_ctx_ref(object_id="ctx-obj-99"))
    assert a.content_digest == b.content_digest


def test_input_snapshot_context_ref_content_is_semantic():
    a = _input_snapshot(context_snapshot_ref=_ctx_ref(content_digest_hex=DA))
    b = _input_snapshot(context_snapshot_ref=_ctx_ref(content_digest_hex=DB))
    assert a.content_digest != b.content_digest


def test_input_snapshot_data_result_refs_canonical_order_required():
    hi = _data_ref("Zeta", DB)
    lo = _data_ref("Alpha", DA)
    with pytest.raises(ValidationError):
        _input_snapshot(data_result_refs=(hi, lo))  # out of order
    ok = _input_snapshot(data_result_refs=(lo, hi))
    assert len(ok.data_result_refs) == 2


def test_input_snapshot_data_result_refs_reject_duplicate():
    r = _data_ref("Alpha", DA)
    r2 = _data_ref("Alpha", DA, object_id="dr-obj-2")  # same semantic identity
    with pytest.raises(ValidationError):
        _input_snapshot(data_result_refs=(r, r2))


def test_input_snapshot_data_result_refs_reject_non_main():
    r = _data_ref("Alpha", DA, namespace="sealed")
    with pytest.raises(ValidationError):
        _input_snapshot(data_result_refs=(r,))


def test_input_snapshot_data_result_ref_object_relocation_is_audit_only():
    a = _input_snapshot(data_result_refs=(_data_ref("Alpha", DA, object_id="dr-1"),))
    b = _input_snapshot(data_result_refs=(_data_ref("Alpha", DA, object_id="dr-9"),))
    assert a.content_digest == b.content_digest


def test_input_snapshot_data_result_ref_content_is_semantic():
    a = _input_snapshot(data_result_refs=(_data_ref("Alpha", DA),))
    b = _input_snapshot(data_result_refs=(_data_ref("Alpha", DB),))
    assert a.content_digest != b.content_digest


# --------------------------------------------------------------------------- #
# 6. InputSnapshot readiness matrix + artifact binding cardinality             #
# --------------------------------------------------------------------------- #
def test_input_snapshot_ready_with_missing_names_rejected():
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="ready", missing_input_names=("feed",))


def test_input_snapshot_ready_requires_one_binding_exactly_one_ref():
    empty_one = _binding("feed", "one", ())  # zero refs is legal at model level
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="ready", artifact_inputs=(empty_one,))


def test_input_snapshot_ready_accepts_one_binding_with_one_ref():
    one = _binding("feed", "one", (_artifact_ref(),))
    snap = _input_snapshot(readiness="ready", artifact_inputs=(one,))
    assert snap.artifact_inputs[0].cardinality == "one"


def test_input_snapshot_ready_accepts_many_binding_with_multiple_refs():
    many = _binding("feeds", "many", (_artifact_ref(content_digest_hex=DA),
                                       _artifact_ref(content_digest_hex=DB, artifact_id="art-2")))
    snap = _input_snapshot(readiness="ready", artifact_inputs=(many,))
    assert len(snap.artifact_inputs[0].artifact_refs) == 2


def test_input_snapshot_terminal_partial_requires_missing_names():
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="terminal_partial", missing_input_names=())


def test_input_snapshot_terminal_partial_records_exact_missing_names():
    snap = _input_snapshot(readiness="terminal_partial", missing_input_names=("feed", "peer"))
    assert snap.readiness == "terminal_partial"
    assert snap.missing_input_names == ("feed", "peer")


def test_input_snapshot_missing_names_must_be_sorted_and_dupfree():
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="terminal_partial", missing_input_names=("peer", "feed"))
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="terminal_partial", missing_input_names=("feed", "feed"))


def test_input_snapshot_artifact_inputs_reject_duplicate_input_name():
    a = _binding("feed", "one", (_artifact_ref(),))
    b = _binding("feed", "many", ())
    with pytest.raises(ValidationError):
        _input_snapshot(readiness="terminal_partial", missing_input_names=("x",),
                        artifact_inputs=(a, b))


def test_input_snapshot_artifact_inputs_order_is_semantic():
    a = _binding("alpha", "one", (_artifact_ref(),))
    b = _binding("beta", "one", (_artifact_ref(artifact_id="art-2"),))
    x = _input_snapshot(readiness="ready", artifact_inputs=(a, b))
    y = _input_snapshot(readiness="ready", artifact_inputs=(b, a))
    assert x.content_digest != y.content_digest


# --------------------------------------------------------------------------- #
# 6. InputSnapshot audit-vs-semantic field classification                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field, val_a, val_b", [
    ("run_id", "run-1", "run-2"),
    ("plan_id", "plan-1", "plan-2"),
    ("attempt", 1, 2),
])
def test_input_snapshot_audit_fields_do_not_move_digest(field, val_a, val_b):
    a = _input_snapshot(**{field: val_a})
    b = _input_snapshot(**{field: val_b})
    assert a.content_digest == b.content_digest
    assert a.audit_digest_value() != b.audit_digest_value()


@pytest.mark.parametrize("field, val_a, val_b", [
    ("plan_digest", DA, DB),
    ("node_id", "node-1", "node-2"),
    ("layer_index", 0, 3),
    ("readiness", "ready", "terminal_partial"),
])
def test_input_snapshot_semantic_fields_move_digest(field, val_a, val_b):
    over_a = {field: val_a}
    over_b = {field: val_b}
    if val_b == "terminal_partial":
        over_b["missing_input_names"] = ("feed",)
    a = _input_snapshot(**over_a)
    b = _input_snapshot(**over_b)
    assert a.content_digest != b.content_digest


def test_input_snapshot_semantic_exclude_is_the_new_audit_set():
    assert InputSnapshot.SEMANTIC_EXCLUDE == frozenset(
        {"snapshot_id", "run_id", "plan_id", "attempt", "built_at"}
    )


# --------------------------------------------------------------------------- #
# InputArtifactBinding model                                                   #
# --------------------------------------------------------------------------- #
def test_input_artifact_binding_one_rejects_two_refs():
    with pytest.raises(ValidationError):
        InputArtifactBinding(
            input_name="feed", cardinality="one",
            artifact_refs=(_artifact_ref(), _artifact_ref(artifact_id="art-2")),
        )


def test_input_artifact_binding_one_allows_single_ref():
    b = InputArtifactBinding(input_name="feed", cardinality="one", artifact_refs=(_artifact_ref(),))
    assert b.cardinality == "one"


def test_input_artifact_binding_is_frozen_and_forbids_extra():
    b = _binding("feed", "many", ())
    with pytest.raises(ValidationError):
        b.input_name = "other"
    with pytest.raises(ValidationError):
        InputArtifactBinding(input_name="feed", cardinality="one", bogus=1)


def test_input_artifact_binding_artifact_id_is_audit_only():
    a = _binding("feed", "one", (_artifact_ref(artifact_id="art-1"),))
    b = _binding("feed", "one", (_artifact_ref(artifact_id="art-99"),))
    assert a.semantic_digest() == b.semantic_digest()


# --------------------------------------------------------------------------- #
# ContextRuntimeRequirements model + helpers                                   #
# --------------------------------------------------------------------------- #
def _content_ref(id: str = "prompt.macro", cd: str = DA) -> ContentRef:
    return ContentRef(id=id, version="1", content_digest=cd)


def _capability_ref(id: str = "cap.markets", cd: str = DB) -> CapabilityRef:
    return CapabilityRef(id=id, version="1", content_digest=cd)


def _requirements(**over) -> ContextRuntimeRequirements:
    base = dict(
        context_subject_digest=DA,
        required_schema_registry_digest=DB,
        required_catalog_digest=DC,
        required_runtime_material_refs=(),
        required_capability_refs=(),
        required_bridge_ids=(),
    )
    base.update(over)
    return ContextRuntimeRequirements.build(**base)


def test_requirements_happy_self_seals():
    req = _requirements()
    assert req.requirements_digest == req.semantic_digest()


def test_requirements_declared_digest_mismatch_rejected():
    req = _requirements()
    payload = req.model_dump()
    payload["requirements_digest"] = WRONG
    with pytest.raises(ValidationError):
        ContextRuntimeRequirements(**payload)


def test_requirements_is_frozen_and_forbids_extra():
    req = _requirements()
    with pytest.raises(ValidationError):
        req.required_catalog_digest = DF
    with pytest.raises(ValidationError):
        _requirements(bogus=1)


def test_requirements_material_refs_canonical_and_dupfree():
    hi = _content_ref("z.prompt", DB)
    lo = _content_ref("a.prompt", DA)
    with pytest.raises(ValidationError):
        _requirements(required_runtime_material_refs=(hi, lo))  # out of order
    with pytest.raises(ValidationError):
        _requirements(required_runtime_material_refs=(lo, lo))  # duplicate
    ok = _requirements(required_runtime_material_refs=(lo, hi))
    assert len(ok.required_runtime_material_refs) == 2


def test_requirements_capability_refs_canonical_and_dupfree():
    hi = _capability_ref("z.cap", DB)
    lo = _capability_ref("a.cap", DA)
    with pytest.raises(ValidationError):
        _requirements(required_capability_refs=(hi, lo))
    with pytest.raises(ValidationError):
        _requirements(required_capability_refs=(lo, lo))
    ok = _requirements(required_capability_refs=(lo, hi))
    assert len(ok.required_capability_refs) == 2


def test_requirements_bridge_ids_sorted_and_dupfree():
    with pytest.raises(ValidationError):
        _requirements(required_bridge_ids=("bridge.b", "bridge.a"))
    with pytest.raises(ValidationError):
        _requirements(required_bridge_ids=("bridge.a", "bridge.a"))
    ok = _requirements(required_bridge_ids=("bridge.a", "bridge.b"))
    assert ok.required_bridge_ids == ("bridge.a", "bridge.b")


def test_requirements_semantic_fields_move_digest():
    a = _requirements(required_catalog_digest=DC)
    b = _requirements(required_catalog_digest=DF)
    assert a.requirements_digest != b.requirements_digest


def test_compute_context_subject_digest_is_deterministic_and_sensitive():
    snap = _non_empty_context_snapshot()
    d1 = compute_context_subject_digest(
        data_context=snap.data_context,
        memory_snapshot_ref=snap.memory_snapshot_ref,
        memory_selection_ref=snap.memory_selection_ref,
        memory_session_id=snap.memory_session_id,
    )
    d2 = compute_context_subject_digest(
        data_context=snap.data_context,
        memory_snapshot_ref=snap.memory_snapshot_ref,
        memory_selection_ref=snap.memory_selection_ref,
        memory_session_id=snap.memory_session_id,
    )
    assert d1 == d2
    # session scope participates in the subject digest.
    d3 = compute_context_subject_digest(
        data_context=snap.data_context,
        memory_snapshot_ref=snap.memory_snapshot_ref,
        memory_selection_ref=snap.memory_selection_ref,
        memory_session_id="sess.desk_a",
    )
    assert d1 != d3


def test_verify_context_runtime_requirements_cross_match_both_directions():
    snap = _non_empty_context_snapshot()
    subject = compute_context_subject_digest(
        data_context=snap.data_context,
        memory_snapshot_ref=snap.memory_snapshot_ref,
        memory_selection_ref=snap.memory_selection_ref,
        memory_session_id=snap.memory_session_id,
    )
    req = _requirements(context_subject_digest=subject)
    # bind the snapshot's runtime_requirements_ref to the requirements' self digest.
    bound = _non_empty_context_snapshot(requirements_content_digest=req.requirements_digest)
    assert verify_context_runtime_requirements(bound, req) is None
    # a requirements whose subject digest does not match is rejected.
    wrong = _requirements(context_subject_digest=DF)
    bound_wrong = _non_empty_context_snapshot(requirements_content_digest=wrong.requirements_digest)
    with pytest.raises(ValueError):
        verify_context_runtime_requirements(bound_wrong, wrong)


def test_verify_context_runtime_requirements_rejects_content_digest_mismatch():
    snap = _non_empty_context_snapshot()
    subject = compute_context_subject_digest(
        data_context=snap.data_context,
        memory_snapshot_ref=snap.memory_snapshot_ref,
        memory_selection_ref=snap.memory_selection_ref,
        memory_session_id=snap.memory_session_id,
    )
    req = _requirements(context_subject_digest=subject)
    # snap's requirements_ref content digest (DE) != req.requirements_digest.
    with pytest.raises(ValueError):
        verify_context_runtime_requirements(snap, req)


def test_verify_context_runtime_requirements_rejects_empty_pair():
    empty = _context_snapshot()  # canonical empty pair, no requirements ref
    req = _requirements()
    with pytest.raises(ValueError):
        verify_context_runtime_requirements(empty, req)


# --------------------------------------------------------------------------- #
# 7. empty-memory builder + persisted locator/ref binding                     #
# --------------------------------------------------------------------------- #
def test_empty_memory_binding_is_deterministic_and_empty():
    a = build_empty_memory_binding()
    b = build_empty_memory_binding()
    assert a.snapshot.records == ()
    assert a.selection.records == ()
    assert a.snapshot_hash == b.snapshot_hash
    assert a.past_context_hash == b.past_context_hash


def test_empty_memory_binding_hashes_are_canonical_content_digests():
    b = build_empty_memory_binding()
    assert b.snapshot_hash == b.snapshot.content_digest
    assert b.past_context_hash == b.selection.content_digest
    # the selection binds the snapshot digest.
    assert b.selection.snapshot_digest == b.snapshot.content_digest
    assert b.snapshot_hash != WRONG and b.past_context_hash != WRONG


def test_empty_memory_snapshot_rejects_nonempty_records():
    b = build_empty_memory_binding()
    payload = b.snapshot.model_dump()
    payload["records"] = (_mem_ref().model_dump(),)
    with pytest.raises(ValidationError):
        EmptyMemorySnapshot(**payload)


def test_empty_memory_models_verify_declared_digest_on_load():
    b = build_empty_memory_binding()
    payload = b.snapshot.model_dump()
    payload["content_digest"] = WRONG
    with pytest.raises(ValidationError):
        EmptyMemorySnapshot(**payload)


def test_no_memory_context_snapshot_binds_empty_selection():
    binding = build_empty_memory_binding()
    snap = _context_snapshot(memory_session_id=None)
    assert snap.memory_snapshot_hash == binding.snapshot_hash
    assert snap.past_context_hash == binding.past_context_hash
    assert snap.memory_snapshot_ref.payload_ref.namespace == "main"
    assert snap.memory_snapshot_ref.payload_ref.content_digest == binding.snapshot_hash
    assert snap.memory_selection_ref.payload_ref.namespace == "main"
    assert snap.memory_selection_ref.payload_ref.content_digest == binding.past_context_hash
    # a no-memory (canonical empty pair) runtime carries no requirements ref.
    assert snap.runtime_requirements_ref is None
    assert snap.memory_session_id is None


def test_build_empty_memory_binding_exposes_canonical_typed_refs():
    binding = build_empty_memory_binding()
    assert binding.memory_snapshot_ref.schema_ref.name == "EmptyMemorySnapshot"
    assert binding.memory_snapshot_ref.schema_ref.version == "1"
    assert binding.memory_snapshot_ref.payload_ref.namespace == "main"
    assert binding.memory_snapshot_ref.payload_ref.content_digest == binding.snapshot_hash
    assert binding.memory_selection_ref.schema_ref.name == "EmptyMemorySelection"
    assert binding.memory_selection_ref.payload_ref.content_digest == binding.past_context_hash
    # a ContextSnapshot built straight from the binding refs is the empty pair.
    snap = ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=_online_ctx(), memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, built_at=_dt(3, 5),
    )
    assert snap.runtime_requirements_ref is None


# --------------------------------------------------------------------------- #
# 7. memory session scope: no caller/worker widening                          #
# --------------------------------------------------------------------------- #
def test_session_scope_none_authority_allows_none():
    check_memory_session_scope(authority_session_id=None, requested_session_id=None)


def test_session_scope_none_authority_rejects_widening():
    with pytest.raises(ValueError):
        check_memory_session_scope(authority_session_id=None, requested_session_id="sess.desk_a")


def test_session_scope_exact_match_allowed():
    check_memory_session_scope(
        authority_session_id="sess.desk_a", requested_session_id="sess.desk_a"
    )


def test_session_scope_stricter_none_subset_allowed():
    check_memory_session_scope(authority_session_id="sess.desk_a", requested_session_id=None)


def test_session_scope_different_session_is_widening_rejected():
    with pytest.raises(ValueError):
        check_memory_session_scope(
            authority_session_id="sess.desk_a", requested_session_id="sess.desk_b"
        )


# --------------------------------------------------------------------------- #
# 7. verify_memory_record_ref — pure identity checker                         #
# --------------------------------------------------------------------------- #
def test_verify_memory_record_ref_happy_returns_none():
    ref = _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA,
                   available_at=_dt(0, 0))
    assert verify_memory_record_ref(
        ref, record_id="mem.alpha", revision_id="r1",
        available_at=_dt(0, 0), content_digest=DA,
    ) is None


def test_verify_memory_record_ref_equal_instant_offset_ok():
    ref = _mem_ref(available_at=_dt(0, 0, tz=UTC))
    verify_memory_record_ref(
        ref, record_id="mem.alpha", revision_id="r1",
        available_at=_dt(8, 0, tz=CST), content_digest=DA,  # same instant
    )


@pytest.mark.parametrize(
    "over",
    [
        {"record_id": "mem.other"},
        {"revision_id": "r2"},
        {"content_digest": DB},
        {"available_at": _dt(0, 5)},
    ],
)
def test_verify_memory_record_ref_mismatch_rejected(over):
    ref = _mem_ref(record_id="mem.alpha", revision_id="r1", content_digest=DA,
                   available_at=_dt(0, 0))
    kw = dict(record_id="mem.alpha", revision_id="r1",
              available_at=_dt(0, 0), content_digest=DA)
    kw.update(over)
    with pytest.raises(ValueError):
        verify_memory_record_ref(ref, **kw)


def test_verify_memory_record_ref_naive_availability_rejected():
    ref = _mem_ref(available_at=_dt(0, 0))
    with pytest.raises(ValueError):
        verify_memory_record_ref(
            ref, record_id="mem.alpha", revision_id="r1",
            available_at=datetime(2026, 7, 15, 0, 0), content_digest=DA,
        )


# --------------------------------------------------------------------------- #
# RunContext — frozen run-level binding                                       #
# --------------------------------------------------------------------------- #
def _run_context(**over) -> RunContext:
    binding = build_empty_memory_binding()
    base = dict(
        run_id="run-1",
        data=_online_ctx(),
        context_snapshot_id="ctx-1",
        memory_snapshot_hash=binding.snapshot_hash,
        budget=RunBudget(ledger_id="ledger-1", max_tokens=1000, max_llm_invocations=10,
                         max_concurrency=4),
        cancellation_token_id="cancel-1",
    )
    base.update(over)
    return RunContext(**base)


def test_run_context_happy():
    rc = _run_context()
    assert rc.run_id == "run-1"
    assert rc.data.mode is DataMode.ONLINE


def test_run_context_locator_and_token_are_audit_only():
    a = _run_context(context_snapshot_id="ctx-1", cancellation_token_id="cancel-1")
    b = _run_context(context_snapshot_id="ctx-9", cancellation_token_id="cancel-9")
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_run_context_data_and_memory_are_semantic():
    a = _run_context()
    b = _run_context(memory_snapshot_hash=DF)
    assert a.semantic_digest() != b.semantic_digest()


def test_run_context_bootstrap_allows_absent_context_snapshot_id():
    rc = _run_context(context_snapshot_id=None)
    assert rc.context_snapshot_id is None


def test_run_context_is_frozen_and_forbids_extra():
    rc = _run_context()
    with pytest.raises(ValidationError):
        rc.run_id = "run-2"
    with pytest.raises(ValidationError):
        _run_context(bogus=1)
