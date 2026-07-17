# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — visibility-BEFORE-ranking selection tests.

The closed retrieval order: frozen snapshot → visible set (availability /
validity / review / role / scope / session) → mandatory-then-rank-then-top_k →
zero-hit fallback over the SAME visible set. A future/pending/expired highly
relevant record can never consume a rank slot; overflow fails closed.

Run: ``pytest tests/orchestration/memory/test_visibility.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.memory.store import select_memory
from guanlan_v2.orchestration.refs import ContentRef

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
EARLY = AS_OF - timedelta(days=2)
HEX_A = "a" * 64


def make_policy(**overrides) -> M.ResolvedMemoryPolicy:
    fields = dict(
        policy_id="memory.policy.test", policy_version="1",
        source_mappings=(
            M.MemorySourcePolicyEntry(source_id="agent.own", kind="semantic",
                                      scope="agent_own", importance=0.5),
        ),
        archive_tail_chars=0,
        context_scopes=("console_global",),
        worker_scopes=("agent_own", "agent_shared"),
        borrow_grants=(),
        allowed_kinds=("episodic", "procedural", "semantic"),
        top_k_non_mandatory=3, max_mandatory=2,
        max_record_bytes=4096, max_total_rendered_bytes=65536,
    )
    fields.update(overrides)
    pol = M.MemoryCapturePolicy.build(**fields)
    return M.ResolvedMemoryPolicy(
        policy=pol,
        material_ref=ContentRef(id="memory.facade.policy", version="1",
                                content_digest=pol.policy_digest))


def rec(text, *, locator, review="approved", available=EARLY, valid_from=None,
        valid_to=None, scope="agent_own", owner="dec.pm", session=None,
        kind="semantic", importance=0.5, mandatory=False, epoch=0):
    entry = M.MemorySourceStateEntry(
        source_id="agent.own", locator=locator, presence="present",
        store_content_digest=HEX_A, continuity_epoch=epoch)
    if review == "approved":
        evidence = M.MemoryReviewEvidence(evidence=M.LegacyCutoverEvidence(
            attestation_ref=_typed("MemoryCutoverAttestation"),
            source_payload_ref=_typed("MemoryCutoverSourcePayload"),
            genesis_continuity_epoch=epoch))
        ev_ref = _typed("MemoryReviewEvidence", evidence.semantic_digest())
        basis = "legacy_cutover"
    else:
        evidence, ev_ref, basis = None, None, None
    return M.build_memory_record(
        source_entry=entry, owner_id=owner, scope=scope, session_id=session,
        kind=kind, text=text, created_at=available,
        valid_from=valid_from or available, valid_to=valid_to,
        available_at=available, review_state=review, review_basis=basis,
        review_evidence=evidence, review_evidence_ref=ev_ref,
        importance=importance, mandatory=mandatory,
        identity={"store": "agent", "owner": owner, "locator": locator})


def _typed(schema, digest="b" * 64):
    from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef

    return TypedPayloadRef(
        schema_ref=SchemaRef(name=schema, version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=digest))


def worker_query(policy, *, top_k=3, session=None, borrowed=(), text="momentum stop"):
    return M.MemoryQuery(
        query_text=text, role="worker", reader_id="dec.pm",
        allowed_kinds=("episodic", "procedural", "semantic"),
        allowed_scopes=("agent_own", "agent_shared"),
        borrowed_owners=tuple(sorted(borrowed)),
        memory_session_id=session, top_k=top_k,
        policy_digest=policy.policy.policy_digest)


# --------------------------------------------------------------------------- #
# filter BEFORE rank                                                           #
# --------------------------------------------------------------------------- #
def test_future_pending_expired_records_cannot_consume_a_rank_slot():
    policy = make_policy()
    query = worker_query(policy, top_k=2, text="momentum")
    perfect_future = rec("momentum momentum momentum", locator="a.md",
                         available=AS_OF + timedelta(seconds=1))
    perfect_pending = rec("momentum momentum", locator="b.md", review="pending")
    perfect_expired = rec("momentum stop", locator="c.md",
                          valid_to=AS_OF - timedelta(days=1),
                          available=AS_OF - timedelta(days=2))
    weak_visible = rec("a barely related note", locator="d.md")
    entries = select_memory(
        query, tuple(perfect(r) for r in
                     (perfect_future, perfect_pending, perfect_expired, weak_visible)),
        as_of=AS_OF, policy=policy)
    ids = {e.record_ref.record_id for e in entries}
    assert ids == {weak_visible[1].record_id}


def perfect(pair):
    return pair


def test_not_yet_valid_records_are_excluded():
    policy = make_policy()
    query = worker_query(policy)
    later = rec("momentum stop", locator="a.md",
                valid_from=AS_OF + timedelta(days=1), available=EARLY)
    entries = select_memory(query, (later,), as_of=AS_OF, policy=policy)
    assert entries == ()


def test_wrong_role_scope_and_foreign_owner_are_excluded():
    policy = make_policy()
    query = worker_query(policy)
    foreign_own = rec("momentum stop", locator="a.md", owner="mkt.macro")
    console = rec("momentum stop", locator="b.md", scope="console_global",
                  owner="console", kind="episodic")
    mine = rec("momentum stop", locator="c.md")
    entries = select_memory(query, (foreign_own, console, mine),
                            as_of=AS_OF, policy=policy)
    assert {e.record_ref.record_id for e in entries} == {mine[1].record_id}


def test_borrowed_owner_is_visible_only_through_the_granted_set():
    policy = make_policy()
    borrowed = rec("momentum stop", locator="a.md", owner="mkt.macro")
    no_grant = select_memory(worker_query(policy), (borrowed,), as_of=AS_OF, policy=policy)
    assert no_grant == ()
    granted = select_memory(worker_query(policy, borrowed=("mkt.macro",)),
                            (borrowed,), as_of=AS_OF, policy=policy)
    assert len(granted) == 1


def test_session_records_require_the_exact_session_scope():
    policy = make_policy(worker_scopes=("agent_own", "console_session"))
    session_rec = rec("momentum stop", locator="s.md", scope="console_session",
                      owner="console", session="cs.demo", kind="episodic")
    q_none = M.MemoryQuery(
        query_text="momentum", role="worker", reader_id="dec.pm",
        allowed_kinds=("episodic", "semantic"),
        allowed_scopes=("agent_own", "console_session"),
        memory_session_id=None, top_k=3,
        policy_digest=policy.policy.policy_digest)
    assert select_memory(q_none, (session_rec,), as_of=AS_OF, policy=policy) == ()
    q_foreign = q_none.model_copy(update={"memory_session_id": "cs.other"})
    assert select_memory(q_foreign, (session_rec,), as_of=AS_OF, policy=policy) == ()
    q_match = q_none.model_copy(update={"memory_session_id": "cs.demo"})
    assert len(select_memory(q_match, (session_rec,), as_of=AS_OF, policy=policy)) == 1


# --------------------------------------------------------------------------- #
# mandatory / top_k / byte budgets                                             #
# --------------------------------------------------------------------------- #
def test_mandatory_records_do_not_consume_top_k_and_lead_in_id_order():
    policy = make_policy()
    query = worker_query(policy, top_k=2, text="momentum")
    m1 = rec("mandatory alpha", locator="m1.md", mandatory=True, importance=0.9)
    m2 = rec("mandatory beta", locator="m2.md", mandatory=True, importance=0.1)
    r1 = rec("momentum one", locator="r1.md")
    r2 = rec("momentum two", locator="r2.md")
    r3 = rec("momentum three", locator="r3.md")
    entries = select_memory(query, (m1, m2, r1, r2, r3), as_of=AS_OF, policy=policy)
    assert len(entries) == 4  # 2 mandatory + top_k(2)
    assert [e.mandatory for e in entries] == [True, True, False, False]
    lead = [e.record_ref.record_id for e in entries[:2]]
    assert lead == sorted(lead)  # deterministic ID order, not importance
    assert [e.rank for e in entries] == [0, 1, 2, 3]


def test_mandatory_overflow_fails_closed_never_drops():
    policy = make_policy(max_mandatory=1)
    query = worker_query(policy)
    m1 = rec("mandatory alpha", locator="m1.md", mandatory=True)
    m2 = rec("mandatory beta", locator="m2.md", mandatory=True)
    with pytest.raises(M.MemoryContractError, match="mandatory"):
        select_memory(query, (m1, m2), as_of=AS_OF, policy=policy)


def test_per_record_byte_overflow_fails_closed():
    policy = make_policy(max_record_bytes=16)
    query = worker_query(policy)
    big = rec("x" * 64, locator="big.md")
    with pytest.raises(M.MemoryContractError, match="byte"):
        select_memory(query, (big,), as_of=AS_OF, policy=policy)


# --------------------------------------------------------------------------- #
# ranking determinism + fallback                                               #
# --------------------------------------------------------------------------- #
def test_rank_is_relevance_then_importance_then_recency_with_id_ties():
    policy = make_policy()
    query = worker_query(policy, top_k=3, text="momentum stop")
    hit2 = rec("momentum stop discipline", locator="a.md", importance=0.1)
    hit1_hi = rec("momentum only, high importance", locator="b.md", importance=0.9)
    hit1_lo = rec("momentum only, low importance", locator="c.md", importance=0.2)
    entries = select_memory(query, (hit1_lo, hit2, hit1_hi), as_of=AS_OF, policy=policy)
    assert [e.record_ref.record_id for e in entries] == [
        hit2[1].record_id, hit1_hi[1].record_id, hit1_lo[1].record_id]


def test_input_order_and_filesystem_enumeration_cannot_affect_selection():
    policy = make_policy()
    query = worker_query(policy, top_k=3, text="momentum")
    records = (
        rec("momentum a", locator="a.md"),
        rec("momentum b", locator="b.md"),
        rec("unrelated", locator="c.md"),
    )
    forward = select_memory(query, records, as_of=AS_OF, policy=policy)
    reverse = select_memory(query, tuple(reversed(records)), as_of=AS_OF, policy=policy)
    assert forward == reverse


def test_zero_hit_fallback_inspects_only_the_same_visible_set():
    policy = make_policy()
    query = worker_query(policy, top_k=2, text="zzz-no-hit-token")
    older = rec("old note", locator="a.md", available=AS_OF - timedelta(days=5))
    newer = rec("new note", locator="b.md", available=AS_OF - timedelta(days=1))
    future = rec("future note", locator="c.md", available=AS_OF + timedelta(days=1))
    pending = rec("pending note", locator="d.md", review="pending")
    entries = select_memory(query, (older, newer, future, pending),
                            as_of=AS_OF, policy=policy)
    # recency order over the visible set only — never the full store.
    assert [e.record_ref.record_id for e in entries] == [
        newer[1].record_id, older[1].record_id]


def test_a_caller_policy_digest_has_no_authority():
    policy = make_policy()
    foreign = make_policy(top_k_non_mandatory=99)
    query = worker_query(policy).model_copy(
        update={"policy_digest": foreign.policy.policy_digest})
    with pytest.raises(M.MemoryAuthorityError):
        select_memory(query, (), as_of=AS_OF, policy=policy)


def test_tampered_record_ref_fails_loud():
    policy = make_policy()
    query = worker_query(policy)
    record, ref = rec("momentum stop", locator="a.md")
    forged = ref.model_copy(update={"content_digest": "f" * 64})
    with pytest.raises(M.MemoryPitError):
        select_memory(query, ((record, forged),), as_of=AS_OF, policy=policy)
