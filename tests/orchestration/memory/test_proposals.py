# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — proposal-only mutation boundary tests.

Covers: the four-registered-payload sequence (Request → Preparation → Proposal
→ Receipt), grant denials creating NO preparation/pending side effect,
idempotent submission + same-key drift conflict, logical pending locators, one
repository-enforced terminal Decision, the full agent loop (write → pending
capture → exact marker + semantic receipt → NEW approved capture revision →
later-run visibility), legacy/manual writes staying pending, and the HONEST
console deferral (delegated write without the exact marker remains pending).

Run: ``pytest tests/orchestration/memory/test_proposals.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.memory import proposals as P
from guanlan_v2.orchestration.memory.catalog import _Phase3MemorySurface
from guanlan_v2.orchestration.memory.store import MemoryContextPreparationService
from tests.orchestration.memory._env import build_memory_world, make_data_context

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)


class StaticVerifier:
    """A fail-closed test verifier: only the exact reviewed credential passes."""

    def verify(self, credential):
        if credential != "admin-token-1":
            raise M.MemoryAuthorityError("admin credential rejected")
        return M.AuthenticatedAdminPrincipal(actor="ops-admin", verified_by="static-verifier")


@pytest.fixture()
def world(tmp_path):
    return build_memory_world(tmp_path)


def _granted_worker(cap_ref):
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    base = phase3_data_catalog_snapshot().workers[0]
    fields = {name: getattr(base, name) for name in type(base).model_fields}
    fields["capability_allowlist"] = tuple(sorted(
        tuple(base.capability_allowlist) + (cap_ref,), key=lambda c: (c.id, c.version)))
    return type(base)(**fields)


@pytest.fixture()
def env(world):
    """A derived reviewed test surface: one granted proposer worker."""
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    base_worker = phase3_data_catalog_snapshot().workers[0]
    surface = _Phase3MemorySurface(
        proposal_grants=(M.MemoryProposalGrant(
            worker_id=base_worker.id,
            allowed_agent_owners=("dec.pm",),
            allowed_console_scopes=("global", "session")),))
    worker = _granted_worker(surface.proposal_capability_ref)
    service = P.MemoryProposalService(
        stores=world.stores, registry_digest=world.registry_digest,
        facade=surface.facade_descriptor, clock=world.clock,
        agent_root=world.agent_root, lease_factory=world.lease_factory,
        console_root=world.console_root, admin_verifier=StaticVerifier())
    plan = SimpleNamespace(plan_digest="a" * 64)
    context = SimpleNamespace(memory_session_id="cs.demo")
    return SimpleNamespace(world=world, surface=surface, worker=worker,
                           service=service, plan=plan, context=context)


def _agent_request(lesson="respect the drawdown limit"):
    return M.MemoryProposalRequest(target=M.AgentMemoryProposalTarget(
        target_agent="dec.pm", topic_slug="drawdown-rule", title="Drawdown rule",
        confidence=0.8, reasoning="five losing trades", lesson_md=lesson))


def _submit(env, request=None, key="idem-1"):
    return env.service.submit_proposal(
        request or _agent_request(), worker=env.worker, plan=env.plan,
        run_id="run-1", context=env.context, idempotency_key=key)


# --------------------------------------------------------------------------- #
# submission                                                                   #
# --------------------------------------------------------------------------- #
def test_submission_produces_the_four_registered_payload_sequence(env):
    proposal, proposal_ref, receipt, receipt_ref = _submit(env)
    assert proposal_ref.schema_ref.name == "MemoryProposal"
    assert receipt_ref.schema_ref.name == "MemoryProposalReceipt"
    assert receipt.status == "pending_external_review"
    assert proposal.request_ref.schema_ref.name == "MemoryProposalRequest"
    assert proposal.preparation_ref.schema_ref.name == "MemoryProposalPreparation"
    assert proposal.marker_id.startswith("apply.")
    # the pending file exists under staging, at the LOGICAL relative locator.
    loc = receipt.pending_locator
    assert loc.locator_kind == "agent_pending"
    pending = env.world.agent_root / loc.relative_locator
    assert pending.exists()
    import hashlib

    assert hashlib.sha256(pending.read_bytes()).hexdigest() == (
        receipt.pending_store_content_digest)


def test_submission_is_idempotent_and_same_key_drift_conflicts(env):
    p1, ref1, r1, rref1 = _submit(env, key="idem-1")
    p2, ref2, r2, rref2 = _submit(env, key="idem-1")
    assert ref1 == ref2 and rref1 == rref2 and p1.proposal_id == p2.proposal_id
    with pytest.raises(M.MemoryConflictError):
        _submit(env, request=_agent_request("a DIFFERENT lesson"), key="idem-1")


def test_ungranted_targets_fail_before_any_pending_side_effect(env, world):
    # cross-agent: grant is dec.pm only.
    foreign = M.MemoryProposalRequest(target=M.AgentMemoryProposalTarget(
        target_agent="mkt.macro", topic_slug="x", title="t", confidence=0.5,
        reasoning="r", lesson_md="l"))
    with pytest.raises(M.MemoryAuthorityError):
        _submit(env, request=foreign, key="idem-x")
    # no preparation cell, no pending file was created.
    repo = P.MemoryProposalSubmissionRepository(
        world.stores, registry_digest=world.registry_digest)
    assert repo.load_result("idem-x") is None
    assert not (world.agent_root / "_proposed" / "mkt.macro").exists()


def test_pending_content_is_invisible_to_capture(env, world):
    _submit(env)
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, M.MemoryContextAuthority(
            memory_session_id="cs.demo",
            granted_scopes=("console_global", "console_session"),
            authenticated_by="t"),
        query_text="drawdown", top_k=3, operation_key="cap-pending")
    texts = [r.text for r, _ in binding.records]
    assert not any("respect the drawdown" in t for t in texts)


def test_console_submission_journals_a_pending_event_locator(env, world):
    from guanlan_v2.console.store import ConsoleStore

    store = ConsoleStore(root=world.console_root)
    meta = store.create_session("review")
    context = SimpleNamespace(memory_session_id=meta["id"])
    request = M.MemoryProposalRequest(target=M.ConsoleMemoryProposalTarget(
        scope="global", key="", text="prefer limit orders in thin books"))
    proposal, _pref, receipt, _rref = env.service.submit_proposal(
        request, worker=env.worker, plan=env.plan, run_id="run-1",
        context=context, idempotency_key="idem-console")
    loc = receipt.pending_locator
    assert loc.locator_kind == "console_pending"
    assert loc.journal_session_id == meta["id"]
    events = store.read_events(meta["id"])
    assert events[-1]["id"] == loc.event_id
    assert events[-1]["status"] == "pending_external_review"
    # console memory.md was NOT touched by submission.
    assert "limit orders" not in (world.console_root / "memory.md").read_text(
        encoding="utf-8")


# --------------------------------------------------------------------------- #
# decision                                                                     #
# --------------------------------------------------------------------------- #
def test_decision_requires_the_injected_verifier_and_fails_closed(env, world):
    _p, pref, _r, rref = _submit(env)
    no_verifier = P.MemoryProposalService(
        stores=world.stores, registry_digest=world.registry_digest,
        facade=env.surface.facade_descriptor, clock=world.clock,
        agent_root=world.agent_root, admin_verifier=None)
    with pytest.raises(M.MemoryAuthorityError, match="disabled"):
        no_verifier.decide(pref, rref, decision="approved",
                           credential="admin-token-1", reason="ok")
    with pytest.raises(M.MemoryAuthorityError):
        env.service.decide(pref, rref, decision="approved",
                           credential="WRONG", reason="ok")


def test_one_terminal_decision_per_proposal(env):
    _p, pref, _r, rref = _submit(env)
    d1 = env.service.decide(pref, rref, decision="approved",
                            credential="admin-token-1", reason="ok")
    # identical decision (different call) recovers the SAME ref.
    d2 = env.service.decide(pref, rref, decision="approved",
                            credential="admin-token-1", reason="ok")
    assert d1 == d2
    # an approve-vs-reject race conflicts.
    with pytest.raises(M.MemoryConflictError):
        env.service.decide(pref, rref, decision="rejected",
                           credential="admin-token-1", reason="no")


def test_rejected_proposal_creates_no_memory_record(env, world):
    _p, pref, _r, rref = _submit(env)
    env.service.decide(pref, rref, decision="rejected",
                       credential="admin-token-1", reason="not convincing")
    dc = make_data_context(as_of=AS_OF)
    binding = world.service.prepare_online(
        dc, M.MemoryContextAuthority(
            memory_session_id=None, granted_scopes=("console_global",),
            authenticated_by="t"),
        query_text="drawdown", top_k=3, operation_key="cap-rejected")
    assert not any("respect the drawdown" in r.text for r, _ in binding.records)


# --------------------------------------------------------------------------- #
# the full agent loop: pending → decision → exact apply → approved capture     #
# --------------------------------------------------------------------------- #
def _approve_and_apply(env):
    _p, pref, _r, rref = _submit(env)
    decision_ref = env.service.decide(pref, rref, decision="approved",
                                      credential="admin-token-1", reason="ok")
    owner_receipt_ref = env.service.apply_agent_decision(decision_ref)
    return decision_ref, owner_receipt_ref


def _capture_service(env, world):
    return MemoryContextPreparationService(
        stores=world.stores, registry_digest=world.registry_digest,
        policy=world.policy, agent_adapter=world.agent_adapter,
        console_adapter_factory=world.console_factory, clock=world.clock,
        cutover=world.cutover,
        required_schema_registry_digest=world.registry_digest,
        required_catalog_digest="c" * 64,
        applied_resolver=env.service.applied_evidence_resolver())


def test_exact_apply_writes_marker_and_projects_the_semantic_receipt(env, world):
    decision_ref, owner_receipt_ref = _approve_and_apply(env)
    target = world.agent_root / "dec.pm" / "drawdown-rule.md"
    first = target.read_text(encoding="utf-8").split("\n", 1)[0]
    assert first.startswith("<!-- guanlan-memory-apply-v1 marker_id=apply.")
    receipt = world.stores.payloads.get(
        owner_receipt_ref.payload_ref, expected_schema_ref=owner_receipt_ref.schema_ref)
    assert receipt.receipt_kind == "agent" and receipt.operation == "create"
    assert receipt.actual_after_target_store_digest == (
        receipt.intended_after_target_store_digest)
    # idempotent retry (crash between owner write and projection): same refs.
    assert env.service.apply_agent_decision(decision_ref) == owner_receipt_ref


def test_applied_content_becomes_an_approved_capture_revision_later_run(env, world):
    _approve_and_apply(env)
    capture = _capture_service(env, world)
    dc = make_data_context(as_of=AS_OF)
    auth = M.MemoryContextAuthority(
        memory_session_id=None, granted_scopes=("console_global",),
        authenticated_by="t")
    b1 = capture.prepare_online(dc, auth, query_text="drawdown", top_k=3,
                                operation_key="cap-applied")
    applied = [r for r, _ in b1.records if "respect the drawdown" in r.text]
    assert len(applied) == 1
    rec = applied[0]
    assert rec.review_state == "approved"
    assert rec.review_basis == "memory_ops_approval"
    assert rec.available_at > dc.as_of  # a NEW capture-timed revision: not THIS run
    # a LATER run (as_of after the apply) selects it via a worker query.
    later = make_data_context(as_of=rec.available_at + timedelta(hours=1))
    b2 = capture.prepare_online(later, auth, query_text="drawdown", top_k=3,
                                operation_key="cap-later")
    later_query = M.MemoryQuery(
        query_text="drawdown", role="worker", reader_id="dec.pm",
        allowed_kinds=("episodic", "procedural", "semantic"),
        allowed_scopes=("agent_own", "agent_shared"), memory_session_id=None,
        top_k=3, policy_digest=world.policy.policy.policy_digest)
    from guanlan_v2.orchestration.memory.store import select_memory

    entries = select_memory(later_query, b2.records, as_of=later.as_of,
                            policy=world.policy)
    assert any(e.record_ref.record_id == rec.record_id for e in entries)


def test_direct_edit_after_apply_returns_to_pending(env, world):
    _approve_and_apply(env)
    target = world.agent_root / "dec.pm" / "drawdown-rule.md"
    # a direct edit KEEPS the marker line but changes the payload.
    marker = target.read_text(encoding="utf-8").split("\n", 1)[0]
    target.write_text(marker + "\ntampered payload\n", encoding="utf-8")
    capture = _capture_service(env, world)
    dc = make_data_context(as_of=AS_OF)
    b = capture.prepare_online(
        dc, M.MemoryContextAuthority(memory_session_id=None,
                                     granted_scopes=("console_global",),
                                     authenticated_by="t"),
        query_text="drawdown", top_k=3, operation_key="cap-tampered")
    tampered = [r for r, _ in b.records if "tampered payload" in r.text]
    assert len(tampered) == 1
    assert tampered[0].review_state == "pending"  # old evidence cannot re-approve


def test_legacy_accept_and_manual_writes_remain_pending(env, world):
    from financial_analyst import memory_ops

    # (a) legacy accept path (no marker, no Decision) — pending.
    proposed = world.agent_root / "_proposed" / "dec.pm"
    proposed.mkdir(parents=True, exist_ok=True)
    (proposed / "legacy-note.md").write_text("legacy accepted note", encoding="utf-8")
    coordinator = memory_ops.AgentMemoryFileCoordinator(
        world.agent_root, lease_factory=world.lease_factory)
    out = coordinator.accept_legacy("dec.pm/legacy-note", source="mcp")
    assert out.get("action") == "accept"
    # (b) a manual write with the SAME visible text as an applied lesson, no marker.
    (world.agent_root / "dec.pm" / "manual-copy.md").write_text(
        "respect the drawdown limit\n", encoding="utf-8")
    capture = _capture_service(env, world)
    dc = make_data_context(as_of=AS_OF)
    b = capture.prepare_online(
        dc, M.MemoryContextAuthority(memory_session_id=None,
                                     granted_scopes=("console_global",),
                                     authenticated_by="t"),
        query_text="legacy", top_k=3, operation_key="cap-legacy")
    by_text = {r.text: r for r, _ in b.records}
    assert by_text["legacy accepted note"].review_state == "pending"
    assert by_text["respect the drawdown limit\n"].review_state == "pending"


def test_worker_path_never_reaches_owner_or_admin_writes(env, monkeypatch):
    from financial_analyst import memory_ops

    calls = []
    monkeypatch.setattr(
        memory_ops.AgentMemoryFileCoordinator, "apply_exact",
        lambda self, cmd: calls.append(cmd) or (_ for _ in ()).throw(AssertionError))
    decide_calls = []
    monkeypatch.setattr(
        P.MemoryDecisionRepository, "decide_once",
        lambda self, **kw: decide_calls.append(kw) or (_ for _ in ()).throw(AssertionError))
    _submit(env, key="idem-spy")  # a submission may not touch either
    assert calls == [] and decide_calls == []


# --------------------------------------------------------------------------- #
# console deferral (honest, fail-safe)                                          #
# --------------------------------------------------------------------------- #
def test_console_apply_delegates_to_the_existing_writer_and_stays_pending(env, world):
    from guanlan_v2.console.store import ConsoleStore

    store = ConsoleStore(root=world.console_root)
    meta = store.create_session("review")
    context = SimpleNamespace(memory_session_id=meta["id"])
    request = M.MemoryProposalRequest(target=M.ConsoleMemoryProposalTarget(
        scope="global", key="", text="prefer limit orders in thin books"))
    written = []

    def fake_writer(*, text, scope, key):
        # stands in for guanlan_v2.console.tools.memory_write_impl on a tmp root
        # (the real impl writes the PRODUCTION var/console path — same behavior:
        # a dated line WITHOUT any exact marker).
        line = f"- [2026-07-17] {text}"
        with (world.console_root / "memory.md").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        written.append((scope, key, text))
        return {"ok": True, "scope": scope, "key": key}

    service = P.MemoryProposalService(
        stores=world.stores, registry_digest=world.registry_digest,
        facade=env.surface.facade_descriptor, clock=world.clock,
        agent_root=world.agent_root, lease_factory=world.lease_factory,
        console_root=world.console_root, admin_verifier=StaticVerifier(),
        console_writer=fake_writer)
    _p, pref, _r, rref = service.submit_proposal(
        request, worker=env.worker, plan=env.plan, run_id="run-1",
        context=context, idempotency_key="idem-console-apply")
    decision_ref = service.decide(pref, rref, decision="approved",
                                  credential="admin-token-1", reason="ok")
    out = service.apply_console_decision(decision_ref)
    assert out["ok"] is True and written
    # DEFERRED(console-lifecycle): without the exact in-container marker the
    # applied line is captured but conservatively stays PENDING.
    capture = _capture_service(env, world)
    dc = make_data_context(as_of=AS_OF)
    b = capture.prepare_online(
        dc, M.MemoryContextAuthority(memory_session_id=None,
                                     granted_scopes=("console_global",),
                                     authenticated_by="t"),
        query_text="limit orders", top_k=3, operation_key="cap-console")
    applied = [r for r, _ in b.records if "limit orders" in r.text]
    assert len(applied) == 1
    assert applied[0].review_state == "pending"
