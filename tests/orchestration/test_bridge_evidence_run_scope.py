# -*- coding: utf-8 -*-
"""The experience-bridge evidence idempotency keys vs. a shared durable store.

Measured on the real production store (``var/orchestration/``, 2026-07-29): live
``run --attempt 4`` produced a COMPLETED ``lane0.factor`` but BOTH LLM seats
failed with ``reason_code=bridge_execution_error``::

    payload idempotency key 'experience.bridge:lane0.regime:a1:c1:query:evidence'
    reused with different content
    payload idempotency key 'experience.bridge:lane0.rotation:a1:c1:query:evidence'
    reused with different content

Attempt 1 (run identity ``lane0-2026-07-29``) had already written evidence under
those exact keys; attempt 4 is a DIFFERENT run identity (``lane0-2026-07-29-r4``)
whose evidence content differed — but the key, minted at
``bootstrap.py`` ``_Lane0ExperienceSession.freeze_for_execution`` as
``{bridge_id}:{node_id}:a{attempt}:c{call_ordinal}``, carries only per-run-LOCAL
coordinates: the sealed preset pins the node ids and every fresh run restarts at
``a1:c1``. Both seats died inside ``freeze_for_execution`` — BEFORE prompt
assembly, zero tokens burned (the store holds only attempt 1's two
PromptAssemblyRecords). The sixth member of the Task 8b defect class
(run-identity-less keys against a durable cross-run store).

The fix is at the one choke point every provider write crosses:
:meth:`~guanlan_v2.orchestration.worker.BridgeEvidenceWriter.put` /
``record_existing`` now fold the writer's own ``run_id`` into the caller's
semantic key, healing the experience bridge, the memory runtime bridge
(``…:query`` / ``…:selection`` / ``…:rendered-block``) and the data adapter
(``{node}:a{n}:{semantic}``) at once — every one of them minted run-less keys
while the ``:control`` fact it commits ALWAYS embeds ``run_id`` (so even
byte-identical evidence conflicted cross-run).

Three families:

1. **the production shape** — one DURABLE store on disk, run identity A executes,
   the process "restarts" (a fresh fold: ``batch_idem`` is not refolded,
   ``payload_idem`` is — exactly why production surfaced the payload-level
   conflict), then run identity B executes the same sealed node ids with
   different factor inputs. Post-fix B completes and both identities' evidence
   coexists under run-scoped keys.
2. **the general shape** — two run identities over ONE shared in-memory store
   instance (no restart): both complete, for BOTH seats.
3. **the writer-level pin** — the key shape itself, plus the two invariants the
   fix must NOT break: within-run crash-replay stays idempotent, and the
   write-once conflict still fires for genuinely divergent content within ONE
   run identity (nothing here relaxes the machinery).

Run: ``python -m pytest tests/orchestration/test_bridge_evidence_run_scope.py -v``
"""
from __future__ import annotations

import pytest

from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.adapters.durable import build_durable_runtime_stores
from guanlan_v2.orchestration.budget import IdempotencyConflict
from guanlan_v2.orchestration.enums import NodeStatus
from guanlan_v2.orchestration.memory.experience import (
    EXPERIENCE_QUERY_SCHEMA_REF,
    ExperienceQuery,
)

from tests.orchestration.test_bootstrap_e2e import (
    DT,
    FSV,
    _daily_rows,
    _status,
    build_bootstrap_env,
)
from guanlan_v2.orchestration.market.factors import MarketFactorInputs

_SEATS = ("lane0.regime", "lane0.rotation")


def _inputs_variant_a() -> MarketFactorInputs:
    return MarketFactorInputs(limit_up_total=_daily_rows(60, base=30.0))


def _inputs_variant_b() -> MarketFactorInputs:
    # a different tape ⇒ a different feature_vector ⇒ the second identity's
    # ExperienceQuery payload genuinely differs (the production condition that
    # surfaced the conflict at the ``:query:evidence`` payload put).
    return MarketFactorInputs(limit_up_total=_daily_rows(60, base=55.0))


def _payload_idem(stores) -> dict:
    return dict(stores._shared.backend.payload_idem)  # noqa: SLF001


def _assert_run_scoped_seat_evidence(stores, run_id: str) -> None:
    """The pin: the durable payload keys for this run's two seats are RUN-scoped.

    The shape is recomputed here on purpose — change it in
    ``BridgeEvidenceWriter._run_scoped`` and this test reddens consciously.
    """
    idem = _payload_idem(stores)
    for seat in _SEATS:
        for role in ("query", "selection"):
            key = f"{run_id}:experience.bridge:{seat}:a1:c1:{role}:evidence"
            assert key in idem, (key, sorted(k for k in idem if "experience.bridge" in k))


# =========================================================================== #
# 1 — the production shape: durable store + restart + a second run identity    #
# =========================================================================== #
def test_second_run_identity_on_one_durable_store_completes(tmp_path):
    """Attempt A wrote evidence; attempt B (a NEW run identity, after a process
    restart) re-runs the same sealed node ids in the same durable store — and
    must now succeed. Pre-fix this died in ``freeze_for_execution`` for BOTH
    seats with the exact production error (``payload idempotency key
    'experience.bridge:lane0.regime:a1:c1:query:evidence' reused with different
    content``) before any model invoke."""
    root = tmp_path / "orchestration"

    def durable_factory(resolver, clock):
        return build_durable_runtime_stores(
            root, resolver=resolver, clock=clock,
            allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    env1 = build_bootstrap_env(
        suffix="-att1", inputs=_inputs_variant_a(), stores_factory=durable_factory)
    result1, rec1 = env1.run()
    assert result1.terminal_status == "completed"
    for seat in _SEATS:
        assert _status(rec1, seat) is NodeStatus.COMPLETED

    # the "restart": a FRESH durable stores over the same root. ``batch_idem``
    # is deliberately not refolded (adapters/durable.py fold_into), so any
    # cross-run collision surfaces at the payload level — production's shape.
    env2 = build_bootstrap_env(
        suffix="-att4", inputs=_inputs_variant_b(), stores_factory=durable_factory)
    result2, rec2 = env2.run()
    assert result2.terminal_status == "completed"
    for seat in _SEATS:
        assert _status(rec2, seat) is NodeStatus.COMPLETED
    # both LLM seats actually invoked the model this time (attempt 4 burned zero).
    assert result2.settled_llm_invocations == 2

    # both identities' evidence coexists under run-scoped durable keys.
    _assert_run_scoped_seat_evidence(env2.stores, "run-boot-att1")
    _assert_run_scoped_seat_evidence(env2.stores, "run-boot-att4")


# =========================================================================== #
# 2 — the general shape: two run identities, ONE store instance, both seats    #
# =========================================================================== #
def test_two_run_identities_one_shared_store_both_seats_complete():
    env1 = build_bootstrap_env(suffix="-runa", inputs=_inputs_variant_a())
    result1, rec1 = env1.run()
    assert result1.terminal_status == "completed"

    env2 = build_bootstrap_env(
        suffix="-runb", inputs=_inputs_variant_b(),
        stores_factory=lambda resolver, clock: env1.stores)
    assert env2.stores is env1.stores
    result2, rec2 = env2.run()
    assert result2.terminal_status == "completed"
    for rec in (rec1, rec2):
        for seat in _SEATS:
            assert _status(rec, seat) is NodeStatus.COMPLETED

    _assert_run_scoped_seat_evidence(env1.stores, "run-boot-runa")
    _assert_run_scoped_seat_evidence(env1.stores, "run-boot-runb")


# =========================================================================== #
# 3 — the writer-level pin + the two invariants the fix must not break         #
# =========================================================================== #
def _writer_for(env, *, run_id: str) -> tuple:
    """A fresh ``BridgeEvidenceWriter`` + issued token over ``env``'s store,
    exactly as the executor builds one (worker.py builds it with ``ctx.run_id``)."""
    sequencer = W.ExecutionEvidenceSequencer(node_id="lane0.regime", attempt=1)
    token = sequencer.issue_call_token(
        bridge_priority=0, bridge_id="experience.bridge", summary_digest="a" * 64)
    writer = W.BridgeEvidenceWriter(
        stores=env.stores, sequencer=sequencer, run_id=run_id,
        plan_digest="b" * 64, node_id="lane0.regime",
        data_registry_digest=env.registry.registry_digest,
        runtime_registry_digest=env.rt_digest)
    return writer, token


def _query(features: dict) -> ExperienceQuery:
    return ExperienceQuery.build(
        as_of=DT, feature_schema_version=FSV, features=features, k=3)


_SEMANTIC_KEY = "experience.bridge:lane0.regime:a1:c1:query"


def test_two_run_identities_may_write_different_evidence_under_one_semantic_key():
    """The direct pre-model repro: run A's writer and run B's writer put
    DIFFERENT content under the SAME provider-minted semantic key against one
    store. Pre-fix: ``IdempotencyConflict`` (the exact production death, since
    the durable key was the semantic key verbatim). Post-fix: two distinct
    run-scoped payloads."""
    env = build_bootstrap_env(approve=False)
    writer_a, token_a = _writer_for(env, run_id="lane0-2026-07-29")
    writer_b, token_b = _writer_for(env, run_id="lane0-2026-07-29-r4")

    ref_a = writer_a.put(
        token=token_a, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload=_query({"f1": 1.0}), idempotency_key=_SEMANTIC_KEY)
    ref_b = writer_b.put(
        token=token_b, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload=_query({"f1": 2.0}), idempotency_key=_SEMANTIC_KEY)

    assert ref_a.payload_ref.content_digest != ref_b.payload_ref.content_digest
    idem = _payload_idem(env.stores)
    assert f"lane0-2026-07-29:{_SEMANTIC_KEY}:evidence" in idem
    assert f"lane0-2026-07-29-r4:{_SEMANTIC_KEY}:evidence" in idem
    # the pre-fix run-less key shape is never minted again.
    assert f"{_SEMANTIC_KEY}:evidence" not in idem


def test_within_one_run_identity_the_replay_stays_idempotent():
    """Run-scoping must NOT break the within-run crash-replay: a re-execution of
    the same (run, node, attempt) — a fresh writer, the same run identity —
    replays the identical batch and binds the SAME payload, no conflict."""
    env = build_bootstrap_env(approve=False)
    writer1, token1 = _writer_for(env, run_id="lane0-2026-07-29")
    ref1 = writer1.put(
        token=token1, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload=_query({"f1": 1.0}), idempotency_key=_SEMANTIC_KEY)

    writer2, token2 = _writer_for(env, run_id="lane0-2026-07-29")
    ref2 = writer2.put(
        token=token2, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload=_query({"f1": 1.0}), idempotency_key=_SEMANTIC_KEY)
    assert ref2.payload_ref.content_digest == ref1.payload_ref.content_digest


def test_write_once_still_fires_within_one_run_identity():
    """Nothing here relaxes the machinery: the SAME run identity reusing the
    same semantic key with genuinely different content still refuses."""
    env = build_bootstrap_env(approve=False)
    writer1, token1 = _writer_for(env, run_id="lane0-2026-07-29")
    writer1.put(
        token=token1, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
        payload=_query({"f1": 1.0}), idempotency_key=_SEMANTIC_KEY)

    writer2, token2 = _writer_for(env, run_id="lane0-2026-07-29")
    with pytest.raises(IdempotencyConflict):
        writer2.put(
            token=token2, role="provider_prefetch", schema_ref=EXPERIENCE_QUERY_SCHEMA_REF,
            payload=_query({"f1": 999.0}), idempotency_key=_SEMANTIC_KEY)
