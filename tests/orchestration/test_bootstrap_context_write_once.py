# -*- coding: utf-8 -*-
"""The synthesized bootstrap ``ContextSnapshot@1`` vs. content-addressed write-once.

Measured on the real production store (``var/orchestration/``, 2026-07-29): live
``run --attempt 4`` died in :func:`guanlan_v2.orchestration.dag._synthesize_bootstrap_context_ref`
with ``PayloadWriteConflict`` on digest ``a1a866af…``. The payload store is
content-addressed by the **semantic** digest (``ContextSnapshot.SEMANTIC_EXCLUDE``
drops ``snapshot_id`` / ``memory_snapshot_id`` / ``built_at``) while persisting the
**full** bytes — so a second synthesis of the logically identical bootstrap context
lands on the same file path carrying different audit bytes, and write-once (rightly)
refuses.

Two families, both anchored on ONE durable store (the defect is invisible to a
fresh in-memory store with a fixed clock, which is why the suite never caught it):

1. **convergence** — repeat synthesis of the same session's bootstrap context, at a
   different wall clock and under a later attempt's run identity, must succeed and
   bind the SAME payload; and it must still succeed when the store already holds a
   pre-fix payload whose audit bytes cannot be reproduced (the live store's state).
2. **the write-once check must keep firing** — nothing here relaxes it: genuinely
   divergent bytes at one digest still raise, and a semantically tampered content
   file still hard-fails the fold. (Family 2 is a guard, not a bug repro: it is
   GREEN before and after. It was proved able to fail by temporarily neutering the
   byte comparison in ``_DurableLog._write_payload_content`` and the digest
   re-verification in ``_DurableLog._fold_payloads`` — both tests went RED.)

Run: ``python -m pytest tests/orchestration/test_bootstrap_context_write_once.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration.adapters.chain import (
    PHASE9_BASE_REGISTRY_DIGEST,
    build_phase9_registry,
)
from guanlan_v2.orchestration.adapters.durable import (
    DurableStoreCorrupt,
    PayloadWriteConflict,
    build_durable_runtime_stores,
)
from guanlan_v2.orchestration.bootstrap import build_bootstrap_run_context
from guanlan_v2.orchestration.context import (
    ContextSnapshot,
    RunBudget,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.eventstore import (
    ContentDigestMismatch,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.presets import pilot_data_context
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schema_registry import default_registry

UTC = timezone.utc
_CONTEXT_SR = SchemaRef(name="ContextSnapshot", version="1")
_EMPTY_SNAP_SR = SchemaRef(name="EmptyMemorySnapshot", version="1")
_EMPTY_SEL_SR = SchemaRef(name="EmptyMemorySelection", version="1")

#: the live incident's session date and the two wall clocks it was synthesized at.
SESSION_AS_OF = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)  # 2026-07-29 CST midnight
NEXT_SESSION_AS_OF = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
CLOCK_A = datetime(2026, 7, 29, 12, 6, 8, 438507, tzinfo=UTC)
CLOCK_B = datetime(2026, 7, 29, 15, 58, 2, 101991, tzinfo=UTC)


class _SettableClock:
    """The store's authoritative clock — retunable so ONE durable store can host two
    synthesis cycles that genuinely happen at different wall-clock instants."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _open_stores(root):
    """A durable store rooted at ``root`` with the Lane-0 (Phase 9) registry."""
    # the registry must be resolvable BEFORE the fold — the production startup
    # (``orchestration.startup``) registers phase 1/2/9 up front for exactly this
    # reason: a fold that cannot resolve a stored payload's registry is corruption.
    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    rt = resolver.register(build_phase9_registry(PHASE9_BASE_REGISTRY_DIGEST))
    clock = _SettableClock(CLOCK_A)
    stores = build_durable_runtime_stores(root, resolver=resolver, clock=clock)
    return stores, rt, clock


def _synthesize(store_handle, *, run_id: str, at: datetime,
                as_of: datetime = SESSION_AS_OF):
    """Exactly what ``dag.run_plan`` does for a ``context_snapshot_id=None`` plan,
    with the run's authoritative wall clock reading ``at``."""
    stores, rt, clock = store_handle
    if clock is not None:
        clock.at = at
    ctx = build_bootstrap_run_context(
        run_id=run_id, data=pilot_data_context(as_of=as_of),
        budget=RunBudget(ledger_id=f"led-{run_id}", max_tokens=200_000,
                         max_llm_invocations=2, max_concurrency=2),
        cancellation_token_id=f"cancel-{run_id}")
    return D._synthesize_bootstrap_context_ref(  # noqa: SLF001 — the unit under test
        ctx, run_id=run_id, stores=stores, runtime_registry_digest=rt)


def _put_pre_fix_snapshot(stores, rt, *, run_id: str, built_at: datetime,
                          as_of: datetime = SESSION_AS_OF):
    """Persist the payload the PRE-FIX builder produced — run-scoped locators and a
    wall-clock ``built_at``, exactly the shape sitting in ``var/orchestration/``."""
    binding = build_empty_memory_binding()
    stores.payloads.put(_EMPTY_SNAP_SR, binding.snapshot, registry_digest=rt,
                        namespace="main", idempotency_key="bootstrap-run:empty-memory-snapshot")
    stores.payloads.put(_EMPTY_SEL_SR, binding.selection, registry_digest=rt,
                        namespace="main", idempotency_key="bootstrap-run:empty-memory-selection")
    snapshot = ContextSnapshot.build(
        snapshot_id=f"bootstrap-run-ctx-{run_id}",
        data_context=pilot_data_context(as_of=as_of),
        memory_snapshot_id=f"bootstrap-run-ms-{run_id}",
        memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, memory_session_id=None, built_at=built_at)
    return stores.payloads.put(
        _CONTEXT_SR, snapshot, registry_digest=rt, namespace="main",
        idempotency_key=f"bootstrap-run-ctx:{run_id}")


# =========================================================================== #
# Family 1 — convergence: one session's bootstrap context is ONE payload        #
# =========================================================================== #
def test_second_attempt_at_a_different_wall_clock_binds_the_same_payload(tmp_path):
    """The load-bearing case: two synthesize+put cycles at DIFFERENT wall clocks in
    ONE durable store for the SAME session. Attempt 4's run identity is
    ``-r4``-suffixed (``lane0_driver._lane0_identity``), so it also carries a
    different idempotency key — the exact live ``--attempt 4`` shape."""
    handle = _open_stores(tmp_path / "store")

    first = _synthesize(handle, run_id="lane0-2026-07-29", at=CLOCK_A)
    assert handle[2].at == CLOCK_A
    second = _synthesize(handle, run_id="lane0-2026-07-29-r4", at=CLOCK_B)
    assert handle[2].at == CLOCK_B  # the two cycles really did read different clocks

    assert second.payload_ref.content_digest == first.payload_ref.content_digest
    assert second.payload_ref.object_id == first.payload_ref.object_id
    assert second.schema_ref == first.schema_ref


def test_repeat_synthesis_survives_a_store_reopen(tmp_path):
    """The live shape: attempt 1 wrote in a previous PROCESS. The second attempt
    reads the payload back through the fold, not through in-memory put state."""
    root = tmp_path / "store"
    handle = _open_stores(root)
    first = _synthesize(handle, run_id="lane0-2026-07-29", at=CLOCK_A)

    reopened = _open_stores(root)
    second = _synthesize(reopened, run_id="lane0-2026-07-29-r5", at=CLOCK_B)

    assert second.payload_ref.object_id == first.payload_ref.object_id
    assert second.payload_ref.content_digest == first.payload_ref.content_digest


def test_a_pre_fix_payload_already_on_disk_is_bound_not_rewritten(tmp_path):
    """The production store's actual state: a ``ContextSnapshot@1`` whose audit bytes
    (``built_at`` = the 12:06:08 wall clock, ``snapshot_id`` naming attempt 1's run)
    NO deterministic builder can reproduce. Convergence must still hold — otherwise
    the fix does not unblock a re-run of session 2026-07-29."""
    handle = _open_stores(tmp_path / "store")
    stores, rt, _clock = handle
    legacy = _put_pre_fix_snapshot(
        stores, rt, run_id="lane0-2026-07-29", built_at=CLOCK_A)

    bound = _synthesize(handle, run_id="lane0-2026-07-29-r5", at=CLOCK_B)

    assert bound.payload_ref.object_id == legacy.object_id
    stored = stores.payloads.get(bound.payload_ref, expected_schema_ref=_CONTEXT_SR)
    assert stored.built_at == CLOCK_A  # the first freeze stands; nothing was rewritten
    assert stored.snapshot_id == "bootstrap-run-ctx-lane0-2026-07-29"


def test_a_different_session_is_a_different_payload(tmp_path):
    """Convergence must not over-reach: a genuinely different DataContext (another
    session date) is a different semantic identity and gets its own payload."""
    handle = _open_stores(tmp_path / "store")
    day1 = _synthesize(handle, run_id="lane0-2026-07-29", at=CLOCK_A)
    day2 = _synthesize(handle, run_id="lane0-2026-07-30", at=CLOCK_B,
                       as_of=NEXT_SESSION_AS_OF)

    assert day2.payload_ref.content_digest != day1.payload_ref.content_digest
    assert day2.payload_ref.object_id != day1.payload_ref.object_id


def test_synthesized_bytes_are_a_pure_function_of_the_semantic_content(tmp_path):
    """Prevention: two INDEPENDENT stores, different run identities, different wall
    clocks ⇒ byte-identical payload files. Write-once is then satisfied by
    construction, not by the convergence lookup."""
    a = _synthesize(_open_stores(tmp_path / "a"), run_id="lane0-2026-07-29", at=CLOCK_A)
    b = _synthesize(_open_stores(tmp_path / "b"), run_id="lane0-2026-07-29-r7",
                    at=CLOCK_B)

    assert a.payload_ref.content_digest == b.payload_ref.content_digest
    a_file = tmp_path / "a" / "payloads" / "main" / f"{a.payload_ref.content_digest}.json"
    b_file = tmp_path / "b" / "payloads" / "main" / f"{b.payload_ref.content_digest}.json"
    assert a_file.read_bytes() == b_file.read_bytes()


# =========================================================================== #
# Family 1b — the SECOND site: the Lane-0 driver's own commit of the assembled  #
#             ContextSnapshot (lane0_driver.run_lane0_bootstrap)                #
# =========================================================================== #
# The driver assembles a ContextSnapshot from the finished run and puts it under
# a RUN-scoped key (`lane0-driver-context:{run_id}`). That snapshot is built by
# `presets.build_empty_memory_context` over the SAME DataContext and the SAME
# canonical empty-memory pair as the run's input-side snapshot, so it is the same
# semantic identity — the same content-addressed file — wearing different audit
# clothing (`bootstrap-ctx-{run_id}` vs `bootstrap-run-ctx-…`, a wall clock vs the
# session stamp). On a durable store the second write is byte-divergent and dies.
# Live `run --attempt 4` died here on 2026-07-30 after the dag-site fix let it
# through, with the SAME digest a1a866af….
def _durable_lane0_env(root):
    """A real Lane-0 driver environment over a DURABLE store (the suite's own
    ``Env`` is in-memory, which is exactly why neither site was ever caught)."""
    from guanlan_v2.orchestration import lane0_driver as L
    import tests.orchestration.test_lane0_driver as LT

    clock = LT.AdvancingClock()
    registry = build_phase9_registry(PHASE9_BASE_REGISTRY_DIGEST)
    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(registry)
    from guanlan_v2.orchestration import worker as W

    stores = build_durable_runtime_stores(
        root, resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    gateway = LT.ScriptedLane0Gateway(stores=stores, registry=registry)
    bindings = L.build_lane0_bindings(
        stores=stores, clock=clock, gateway_factory=lambda **_kw: gateway)
    return L, LT, bindings, gateway


def test_one_durable_lane0_run_does_not_collide_with_its_own_input_snapshot(tmp_path):
    """A single, first, clean Lane-0 run on a DURABLE store. The driver's assembled
    snapshot has the same semantic digest as the input-side one dag persisted at run
    start, so the driver must bind that payload, not write a second byte-variant."""
    L, LT, bindings, gateway = _durable_lane0_env(tmp_path / "store")

    result = L.run_lane0_bootstrap(
        authorization=LT.AlwaysGrantedAuthorization(), as_of=LT.AS_OF,
        bindings=bindings, inputs=LT.happy_inputs())

    assert result.outcome == L.OUTCOME_COMPLETED, result
    # exactly ONE ContextSnapshot@1 payload for the session — they are one fact.
    snaps = [m for _r, m in L._scan_snapshots(bindings.stores)]  # noqa: SLF001
    assert len(snaps) == 1, [s.snapshot_id for s in snaps]
    assert result.snapshot_digest == snaps[0].content_digest
    assert result.snapshot_ref.content_digest == snaps[0].content_digest
    # the receipt names the fact that is actually stored, never an unwritten twin.
    assert result.snapshot_id == snaps[0].snapshot_id


def test_the_session_reuse_guard_survives_the_convergence(tmp_path):
    """The red line — one judgment per +08:00 session date — must keep holding once
    the two snapshots are one payload. It can no longer key off the assembled
    snapshot's audit ``snapshot_id`` (there is no separate assembled payload); the
    driver's own acceptance record, the ``BootstrapContextManifest@1`` committed with
    ``CONTEXT_SNAPSHOT_FROZEN``, is what says the session produced its judgment."""
    L, LT, bindings, gateway = _durable_lane0_env(tmp_path / "store")

    first = L.run_lane0_bootstrap(
        authorization=LT.AlwaysGrantedAuthorization(), as_of=LT.AS_OF,
        bindings=bindings, inputs=LT.happy_inputs())
    assert first.outcome == L.OUTCOME_COMPLETED
    burned = len(gateway.invocations)

    second = L.run_lane0_bootstrap(
        authorization=LT.AlwaysGrantedAuthorization(), as_of=LT.AS_OF,
        bindings=bindings, inputs=LT.happy_inputs(), attempt=2)

    assert second.outcome == L.OUTCOME_REUSED
    assert second.reused is True
    assert second.snapshot_digest == first.snapshot_digest
    assert len(gateway.invocations) == burned  # not re-burned


def test_a_started_but_unassembled_session_is_still_re_runnable(tmp_path):
    """The guard must not fire on the INPUT-side snapshot alone. dag persists it at
    run start, so if its mere presence counted as "the day produced a judgment",
    every crashed first attempt would lock the session out for good — which is
    exactly the state ``var/orchestration/`` was in for 2026-07-29."""
    L, _LT, bindings, _gw = _durable_lane0_env(tmp_path / "store")
    handle = (bindings.stores, bindings.runtime_registry_digest, None)
    _synthesize(handle, run_id="lane0-2026-07-29", at=CLOCK_A)  # dag's run-start write

    assert L._committed_snapshot_for_session(  # noqa: SLF001
        bindings.stores, "2026-07-29",
        registry_digest=bindings.runtime_registry_digest) is None


# =========================================================================== #
# Family 2 — the write-once byte check must keep firing (guard, not a repro)    #
# =========================================================================== #
def test_write_once_still_refuses_genuinely_divergent_bytes(tmp_path):
    """Nothing above relaxes the content-addressed write-once check: a file whose
    bytes disagree with a same-digest write is still a hard refusal."""
    handle = _open_stores(tmp_path / "store")
    stores, rt, _clock = handle
    ref = _synthesize(handle, run_id="lane0-2026-07-29", at=CLOCK_A)
    model = stores.payloads.get(ref.payload_ref, expected_schema_ref=_CONTEXT_SR)
    path = (tmp_path / "store" / "payloads" / "main"
            / f"{ref.payload_ref.content_digest}.json")
    path.write_text('{"not": "the same bytes"}', encoding="utf-8")

    with pytest.raises(PayloadWriteConflict):
        stores.payloads.put(
            _CONTEXT_SR, model, registry_digest=rt, namespace="main",
            idempotency_key="a-genuinely-new-writer")


def test_a_semantically_tampered_content_file_still_hard_fails_the_fold(tmp_path):
    """Convergence reads the folded backend — so a tampered content file must never
    become a silently-bound payload. The fold refuses to open the store at all."""
    root = tmp_path / "store"
    ref = _synthesize(_open_stores(root), run_id="lane0-2026-07-29", at=CLOCK_A)

    other = _synthesize(_open_stores(tmp_path / "other"), run_id="lane0-2026-07-30",
                        at=CLOCK_B, as_of=NEXT_SESSION_AS_OF)
    other_file = (tmp_path / "other" / "payloads" / "main"
                  / f"{other.payload_ref.content_digest}.json")
    (root / "payloads" / "main" / f"{ref.payload_ref.content_digest}.json").write_bytes(
        other_file.read_bytes())  # another session's snapshot under this digest

    with pytest.raises((DurableStoreCorrupt, ContentDigestMismatch)):
        _open_stores(root)
