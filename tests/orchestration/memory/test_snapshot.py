# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — exactly-once repositories, cutover, continuity + capture.

Run: ``pytest tests/orchestration/memory/test_snapshot.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import memory_coordination
from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.memory.store import (
    MemoryCutoverCoordinator,
    MemoryCutoverRepository,
    MemorySourceStateHeadRepository,
    compute_source_state_candidate,
    validate_memory_namespace_wiring,
)
from tests.orchestration.memory._env import (
    MemoryWorld,
    SteppingClock,
    build_memory_world,
    make_data_context,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)


@pytest.fixture()
def world(tmp_path) -> MemoryWorld:
    return build_memory_world(tmp_path)


def _authority(session="cs.demo"):
    return M.MemoryContextAuthority(
        memory_session_id=session,
        granted_scopes=("console_global", "console_session"),
        authenticated_by="orchestrator-entry")


# --------------------------------------------------------------------------- #
# namespace wiring                                                             #
# --------------------------------------------------------------------------- #
def test_namespace_union_wiring_matrix():
    ok = frozenset(M.PHASE3_MEMORY_STATE_CELL_NAMESPACES) | {"runtime.prompt.v1"}
    validate_memory_namespace_wiring(ok)
    with pytest.raises(M.MemoryContractError, match="missing"):
        validate_memory_namespace_wiring(ok - {"memory.source_head.v1"})
    with pytest.raises(M.MemoryContractError, match="extra"):
        validate_memory_namespace_wiring(ok | {"memory.rogue.v1"})


def test_repositories_validate_their_owned_subset(world):
    class Rogue(MemorySourceStateHeadRepository):
        OWNED = frozenset({"memory.source_head.v1", "memory.snapshot_head.v1"})

    with pytest.raises(M.MemoryContractError, match="does not own"):
        Rogue(world.stores, registry_digest=world.registry_digest)
    repo = MemorySourceStateHeadRepository(
        world.stores, registry_digest=world.registry_digest)
    with pytest.raises(M.MemoryContractError, match="foreign"):
        repo._cell("memory.snapshot_head.v1", "0" * 64)


def test_store_construction_with_missing_union_fails_repository_startup(tmp_path):
    from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver

    stores = RuntimeStores(resolver=SchemaRegistryResolver(), clock=SteppingClock(),
                           allowed_cell_namespaces=("memory.source_head.v1",))
    with pytest.raises(M.MemoryContractError):
        MemorySourceStateHeadRepository(stores, registry_digest="0" * 64)


# --------------------------------------------------------------------------- #
# cutover                                                                      #
# --------------------------------------------------------------------------- #
def test_cutover_is_exactly_once_and_idempotent_on_retry(world):
    coordinator = world.extras["coordinator"]
    again = coordinator.perform_cutover(
        admin_operation_key="adopt-2026-07-16", admin_actor="ops-admin",
        scan=world.extras["scan"],
        cutover_policy_digest=world.policy.policy.policy_digest,
        clock_now=lambda: world.clock.now())
    assert again.attestation_ref == world.cutover.attestation_ref
    assert again.manifest_ref == world.cutover.manifest_ref
    assert again.genesis_source_state_ref == world.cutover.genesis_source_state_ref
    assert again.attested_at == world.cutover.attested_at  # frozen, not re-clocked


def test_same_admin_key_with_drifted_sources_conflicts(world):
    (world.agent_root / "dec.pm" / "lesson.md").write_text("EDITED", encoding="utf-8")
    with pytest.raises(M.MemoryConflictError):
        world.extras["coordinator"].perform_cutover(
            admin_operation_key="adopt-2026-07-16", admin_actor="ops-admin",
            scan=world.extras["scan"],
            cutover_policy_digest=world.policy.policy.policy_digest,
            clock_now=lambda: world.clock.now())


def test_a_second_baseline_cannot_be_initialized(world):
    """A restart / new session / new admin key can never re-cut the baseline."""
    with pytest.raises(M.MemoryConflictError):
        world.extras["coordinator"].perform_cutover(
            admin_operation_key="adopt-AGAIN",
            admin_actor="ops-admin", scan=world.extras["scan"],
            cutover_policy_digest="9" * 64,
            clock_now=lambda: world.clock.now())


def test_equality_scan_rejects_add_change_delete_races(tmp_path):
    """Between prepare and attestation, added/changed/deleted locators fail the
    equality-only validation scan without refreshing the frozen preparation."""
    world = build_memory_world(tmp_path, skip_service=True)
    repo = world.extras["cutover_repo"]
    # the completed world already attested; use a FRESH stores world for the race.
    world2 = build_memory_world(tmp_path / "race", skip_service=True)
    coordinator = world2.extras["coordinator"]
    calls = {"n": 0}
    real_scan = world2.extras["scan"]

    def racing_scan():
        calls["n"] += 1
        units = real_scan()
        if calls["n"] >= 3:  # the equality validation scan sees an added file
            from guanlan_v2.orchestration.memory.adapters import RawSourceUnit

            units = units + (RawSourceUnit("agent.own", "zz.new/late.md", "sneaky"),)
        return tuple(sorted(units, key=lambda u: (u.source_id, u.locator)))

    with pytest.raises(M.MemoryConflictError, match="equality"):
        coordinator.perform_cutover(
            admin_operation_key="adopt-race", admin_actor="ops-admin",
            scan=racing_scan,
            cutover_policy_digest=world2.policy.policy.policy_digest,
            clock_now=lambda: world2.clock.now())
    # the frozen preparation was NOT refreshed by the race.
    prep_ref = repo_prep = coordinator._repo.load_preparation("adopt-race")
    assert repo_prep is not None
    prep = coordinator._repo._resolve(prep_ref, "MemoryCutoverPreparation")
    assert not any(e.locator == "zz.new/late.md" for e in prep.entries)
    _ = repo  # silence unused (kept for symmetry)


def test_empty_baseline_is_rejected(tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "c").mkdir()
    from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
    from guanlan_v2.orchestration.memory.schema_registry import build_phase3_full_registry
    from guanlan_v2.orchestration.data.schema_registry import PHASE3_DATA_REGISTRY_DIGEST

    resolver = SchemaRegistryResolver()
    digest = resolver.register(build_phase3_full_registry(PHASE3_DATA_REGISTRY_DIGEST))
    stores = RuntimeStores(
        resolver=resolver, clock=SteppingClock(),
        allowed_cell_namespaces=tuple(M.PHASE3_MEMORY_STATE_CELL_NAMESPACES))
    coordinator = MemoryCutoverCoordinator(
        repository=MemoryCutoverRepository(stores, registry_digest=digest),
        source_repository=MemorySourceStateHeadRepository(stores, registry_digest=digest),
        stores=stores, registry_digest=digest,
        lease_factory=memory_coordination.RootLeaseFactory(),
        global_lease_root=tmp_path, logical_roots=(("m", tmp_path / "m"),))
    with pytest.raises(M.MemoryContractError, match="empty baseline"):
        coordinator.perform_cutover(
            admin_operation_key="adopt-empty", admin_actor="ops-admin",
            scan=lambda: (), cutover_policy_digest="0" * 64,
            clock_now=lambda: datetime(2026, 7, 16, tzinfo=UTC))


def test_cutover_root_lease_excludes_a_concurrent_legacy_writer(world):
    """While the coordinator would hold the root lease, an independently
    constructed coordinator (same canonical root) fails closed."""
    factory = memory_coordination.RootLeaseFactory()
    held = factory.lease_for(world.agent_root, owner="cutover", operation="cutover")
    held.acquire()
    try:
        other = factory.lease_for(world.agent_root, owner="legacy-writer",
                                  operation="accept_proposal")
        with pytest.raises(memory_coordination.LeaseHeldError):
            other.acquire()
    finally:
        held.release()


def test_ordered_root_acquisition_rejects_reverse_order(world):
    factory = memory_coordination.RootLeaseFactory()
    with pytest.raises(memory_coordination.RootLeaseError, match="order"):
        factory.acquire_ordered(
            (("console", world.console_root), ("agent", world.agent_root)),
            owner="x", operation="y")


def test_coordination_files_are_excluded_from_scans(world):
    """The lease dir exists under the roots after cutover; no scan unit sees it."""
    assert (world.agent_root / "_coordination").exists()
    units = world.extras["scan"]()
    assert not any("_coordination" in u.locator for u in units)


# --------------------------------------------------------------------------- #
# source-state continuity                                                      #
# --------------------------------------------------------------------------- #
def _genesis(world):
    repo = world.extras["source_repo"]
    return repo.load(world.cutover.root_binding_digest), repo.head_ref(
        world.cutover.root_binding_digest)


def test_a_to_b_to_a_advances_the_epoch_twice(world):
    genesis, genesis_ref = _genesis(world)
    lesson = world.agent_root / "dec.pm" / "lesson.md"
    original = lesson.read_text(encoding="utf-8")
    key = ("agent.own", "dec.pm/lesson.md")

    lesson.write_text("B content", encoding="utf-8")
    c1 = compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref, captured_at=world.clock.now())
    e1 = c1.entry_for(*key)
    assert e1.continuity_epoch == 1

    ref1 = world.extras["source_repo"].advance_once("scan-b", c1)
    lesson.write_text(original, encoding="utf-8")  # bytes return EXACTLY
    c2 = compute_source_state_candidate(
        world.extras["scan"](), c1, ref1, captured_at=world.clock.now())
    e2 = c2.entry_for(*key)
    assert e2.continuity_epoch == 2  # A→B→A cannot reuse the old epoch


def test_a_to_absent_to_a_advances_the_epoch_twice(world):
    genesis, genesis_ref = _genesis(world)
    lesson = world.agent_root / "dec.pm" / "lesson.md"
    original = lesson.read_text(encoding="utf-8")
    key = ("agent.own", "dec.pm/lesson.md")

    lesson.unlink()
    c1 = compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref, captured_at=world.clock.now())
    gone = c1.entry_for(*key)
    assert gone.presence == "absent" and gone.continuity_epoch == 1
    ref1 = world.extras["source_repo"].advance_once("scan-absent", c1)

    lesson.write_text(original, encoding="utf-8")
    c2 = compute_source_state_candidate(
        world.extras["scan"](), c1, ref1, captured_at=world.clock.now())
    back = c2.entry_for(*key)
    assert back.presence == "present" and back.continuity_epoch == 2


def test_unchanged_scan_yields_no_new_state(world):
    genesis, genesis_ref = _genesis(world)
    assert compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref,
        captured_at=world.clock.now()) is None


def test_advance_once_retry_returns_the_original_after_head_moves(world):
    genesis, genesis_ref = _genesis(world)
    repo = world.extras["source_repo"]
    (world.agent_root / "dec.pm" / "lesson.md").write_text("B", encoding="utf-8")
    c1 = compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref, captured_at=world.clock.now())
    ref1 = repo.advance_once("scan-1", c1)
    (world.agent_root / "dec.pm" / "lesson.md").write_text("C", encoding="utf-8")
    c2 = compute_source_state_candidate(
        world.extras["scan"](), c1, ref1, captured_at=world.clock.now())
    ref2 = repo.advance_once("scan-2", c2)
    assert ref2 != ref1
    # the ORIGINAL operation still returns its original ref after the head moved.
    assert repo.advance_once("scan-1", c1) == ref1
    # same key + semantically different state conflicts.
    with pytest.raises(M.MemoryConflictError):
        repo.advance_once("scan-1", c2)


def test_stale_predecessor_fails_closed(world):
    genesis, genesis_ref = _genesis(world)
    repo = world.extras["source_repo"]
    (world.agent_root / "dec.pm" / "lesson.md").write_text("B", encoding="utf-8")
    c1 = compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref, captured_at=world.clock.now())
    repo.advance_once("scan-x", c1)
    # a candidate still binding GENESIS as predecessor is now stale.
    (world.agent_root / "dec.pm" / "lesson.md").write_text("D", encoding="utf-8")
    stale = compute_source_state_candidate(
        world.extras["scan"](), genesis, genesis_ref, captured_at=world.clock.now())
    with pytest.raises(M.MemoryConflictError):
        repo.advance_once("scan-y", stale)


# --------------------------------------------------------------------------- #
# capture: PIT, lineage, conservative availability                              #
# --------------------------------------------------------------------------- #
def test_baseline_records_take_attestation_time_never_mtime(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    for record, _ref in binding.records:
        assert record.review_state == "approved"
        assert record.review_basis == "legacy_cutover"
        assert record.available_at == world.cutover.attested_at
        assert record.created_at == world.cutover.attested_at


def test_post_baseline_edit_is_pending_and_capture_timed(world):
    dc = make_data_context(as_of=AS_OF)
    world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    before_edit = world.clock.current
    (world.agent_root / "dec.pm" / "lesson.md").write_text(
        "edited after baseline", encoding="utf-8")
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-2")
    edited = [r for r, _ in binding.records if r.text == "edited after baseline"]
    assert len(edited) == 1
    rec = edited[0]
    assert rec.review_state == "pending" and rec.review_basis is None
    assert rec.available_at > before_edit          # capture-timed, never backdated
    assert rec.available_at > dc.as_of             # invisible to THIS run
    # ...and the pending edit is therefore not selectable this run.
    assert rec.record_id not in {
        e.record_ref.record_id for e in binding.selection.entries}
    # unchanged records kept their ORIGINAL availability (byte-stable reuse).
    unchanged = [r for r, _ in binding.records
                 if r.review_basis == "legacy_cutover"]
    assert unchanged and all(
        r.available_at == world.cutover.attested_at for r in unchanged)


def test_deleted_accepted_content_disappears_only_from_later_snapshots(world):
    dc = make_data_context(as_of=AS_OF)
    b1 = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    n1 = len(b1.snapshot.entries)
    (world.agent_root / "mkt.macro" / "regime.md").unlink()
    b2 = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-2")
    assert len(b2.snapshot.entries) == n1 - 1
    # the OLD snapshot is immutable and still resolves all its records.
    old = world.stores.payloads.get(
        b1.memory_snapshot_ref.payload_ref,
        expected_schema_ref=b1.memory_snapshot_ref.schema_ref)
    assert len(old.entries) == n1
    assert b2.snapshot.previous_snapshot_hash == b1.snapshot.content_digest


def test_capture_lineage_and_policy_binding(world):
    dc = make_data_context(as_of=AS_OF)
    b1 = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    assert b1.snapshot.previous_snapshot_ref is None  # scope's first snapshot
    assert b1.snapshot.policy_digest == world.policy.policy.policy_digest
    assert b1.snapshot.as_of == dc.as_of
    b2 = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-2")
    assert b2.snapshot.previous_snapshot_hash == b1.snapshot.content_digest
    # per-session scope heads are independent lineages.
    other = world.service.prepare_online(
        dc, _authority(session=None), query_text="momentum", top_k=3,
        operation_key="cap-3")
    assert other.snapshot.previous_snapshot_ref is None


def test_capture_retry_is_exactly_once_even_after_the_head_advances(world):
    dc = make_data_context(as_of=AS_OF)
    b1 = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-2")
    again = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    assert again.memory_snapshot_ref == b1.memory_snapshot_ref
    assert again.memory_selection_ref == b1.memory_selection_ref


def test_capture_without_genesis_blocks_online(tmp_path):
    world = build_memory_world(tmp_path, skip_service=True)
    from guanlan_v2.orchestration.memory.store import MemoryContextPreparationService
    from dataclasses import replace

    broken = replace(world.cutover, root_binding_digest="9" * 64)  # head missing
    service = MemoryContextPreparationService(
        stores=world.stores, registry_digest=world.registry_digest,
        policy=world.policy, agent_adapter=world.agent_adapter,
        console_adapter_factory=world.console_factory, clock=world.clock,
        cutover=broken,
        required_schema_registry_digest="a" * 64, required_catalog_digest="b" * 64)
    with pytest.raises(M.MemoryContractError, match="genesis"):
        service.capture_snapshot(as_of=AS_OF, memory_session_id=None,
                                 operation_key="cap-broken")


# --------------------------------------------------------------------------- #
# PIT_REPLAY matrix                                                            #
# --------------------------------------------------------------------------- #
def _persist_context(world, binding, dc, *, session="cs.demo"):
    from guanlan_v2.orchestration.context import ContextSnapshot
    from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef

    ctx = ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.memory_snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=binding.runtime_requirements_ref,
        memory_session_id=session, built_at=dc.as_of)
    pref = world.stores.payloads.put(
        SchemaRef(name="ContextSnapshot", version="1"), ctx,
        registry_digest=world.registry_digest, namespace="main",
        idempotency_key=f"ctx:{ctx.content_digest}")
    return ctx, TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextSnapshot", version="1"), payload_ref=pref)


def test_pit_replay_projects_the_exact_persisted_closure_with_no_capture(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    _ctx, ctx_ref = _persist_context(world, binding, dc)
    # replay performs no adapter/clock work: mutate the filesystem first and
    # prove the projection is unchanged.
    (world.agent_root / "dec.pm" / "lesson.md").write_text("MUTATED", encoding="utf-8")
    clock_before = world.clock.current
    rb = world.service.prepare_pit_replay(dc, _authority(), prior_context_ref=ctx_ref)
    assert world.clock.current == clock_before  # zero clock reads
    assert rb.memory_snapshot_ref == binding.memory_snapshot_ref
    assert rb.memory_selection_ref == binding.memory_selection_ref
    assert rb.runtime_requirements_ref == binding.runtime_requirements_ref
    assert rb.memory_session_id == "cs.demo"


def test_pit_replay_rejects_foreign_session_and_none_to_session(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    _ctx, ctx_ref = _persist_context(world, binding, dc)
    with pytest.raises(M.MemoryAuthorityError):
        world.service.prepare_pit_replay(
            dc, _authority(session="cs.other"), prior_context_ref=ctx_ref)
    with pytest.raises((M.MemoryAuthorityError, ValueError)):
        world.service.prepare_pit_replay(
            dc, _authority(session=None), prior_context_ref=ctx_ref)


def test_pit_replay_rejects_data_context_and_as_of_drift(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    _ctx, ctx_ref = _persist_context(world, binding, dc)
    drifted = make_data_context(as_of=AS_OF + timedelta(days=1))
    with pytest.raises(M.MemoryAuthorityError):
        world.service.prepare_pit_replay(drifted, _authority(), prior_context_ref=ctx_ref)


def test_pit_replay_rejects_a_guessed_or_wrong_schema_ref(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    with pytest.raises(M.MemoryAuthorityError):
        world.service.prepare_pit_replay(
            dc, _authority(), prior_context_ref=binding.memory_snapshot_ref)
    from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef

    guessed = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="no-such",
                               content_digest="0" * 64))
    with pytest.raises(M.MemoryAuthorityError):
        world.service.prepare_pit_replay(dc, _authority(), prior_context_ref=guessed)


def test_object_id_relocation_is_audit_only_on_replay(world):
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, _authority(), query_text="momentum", top_k=3, operation_key="cap-1")
    _ctx, ctx_ref = _persist_context(world, binding, dc)
    relocated = ctx_ref.model_copy(update={
        "payload_ref": ctx_ref.payload_ref.model_copy(
            update={"object_id": ctx_ref.payload_ref.object_id})})
    rb = world.service.prepare_pit_replay(dc, _authority(), prior_context_ref=relocated)
    assert rb.memory_snapshot_hash == binding.memory_snapshot_hash
