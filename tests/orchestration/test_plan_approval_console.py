# -*- coding: utf-8 -*-
"""Phase 7 - Task 8: console carrier for the plan-approval surface.

Covers the additive console REST endpoints + reserved-session event mirroring that
``guanlan_v2.console.api.build_console_router`` grows when a
:class:`~guanlan_v2.orchestration.approval.PlanApprovalCoordinator` is injected:

* the honest ``503`` fail-closed shape on every plan-approval endpoint when the
  coordinator is ``None`` (never a fake-empty success);
* ``GET /plan/approvals`` (public pending-card JSON), ``GET /plan/approvals/status``
  (decided / pending / unknown triple), ``POST /plan/approvals/decide`` (verified
  decision -> ``record_approval`` via the approvals sink -> ``admit_after_approval``
  for APPROVED, with digest-bound console events emitted in order; 404/409/403);
* the lease surface (``GET /plan/approvals/leases``, ``POST /plan/approvals/lease``,
  ``POST /plan/approvals/lease/revoke``) + a leased auto-admission surfacing
  ``plan_lease_admitted`` in the reserved session feed;
* regression: the existing ``/confirm`` gate + session CRUD stay byte-identical.

The coordinator is REAL; the admission + verifier are fakes mirroring the Task 7/7b
store-test doubles. The fake admission models the Phase-2 contract that
``record_approval`` LOADS the authoritative ``PlanApproval`` from its service-owned
store (populated by the coordinator's ``approvals_sink``) rather than trusting a
caller-carried record. A temp ``ConsoleStore`` root is used throughout.

Run: ``python -m pytest tests/orchestration/test_plan_approval_console.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.orchestration.approval import (
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    PlanDiff,
    PlanDiffEntry,
)
from guanlan_v2.orchestration.plan_presets import PlanPresetError
from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

UTC = timezone.utc
NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)

CAND_A = "a" * 64
CAND_D = "d" * 64
REQ_DIGEST = "c" * 64
PRESET_ID = "daily-brief"
PRESET_REC_DIG = "b" * 64
CAT_DIG = "1" * 64
REG_DIG = "2" * 64
GOOD = "good-cred"
BAD = "bad-cred"
RESERVED_SID = "plan-approvals"


# --------------------------------------------------------------------------- #
# Doubles (mirror the Task 7 / 7b store-test fakes)                            #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _VerifyDenied(Exception):
    """The fake verifier's refusal for an unverifiable credential."""


class _FakeVerifier:
    def __init__(self, actor: str = "admin-1", *, good: str = GOOD) -> None:
        self._actor = actor
        self._good = good
        self.calls: list[object] = []

    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        self.calls.append(credential)
        if credential != self._good:
            raise _VerifyDenied(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor=self._actor, verified_by="test-verifier")


class _FakeRunEvent:
    def __init__(self, *, event_id, candidate_id, decision, actor, idempotency_key):
        self.event_id = event_id
        self.candidate_id = candidate_id
        self.decision = decision
        self.actor = actor
        self.idempotency_key = idempotency_key


class _FakeAdmission:
    """Records ``record_approval`` + ``freeze_and_admit_candidate`` effects.

    Models the REAL Phase-2 contract: ``record_approval`` LOADS the authoritative
    :class:`PlanApproval` from ``self.approvals`` (the service-owned store the
    coordinator's ``approvals_sink`` deposits into) and cross-checks the
    authenticated actor. A missing approval raises (as the real service refuses with
    ``missing_approval``), so a passing decide-approve test proves the sink bridge.
    """

    def __init__(self) -> None:
        self.approvals: dict[tuple, object] = {}      # populated by the sink
        self.record_calls: list[tuple] = []
        self.freeze_calls: list[tuple] = []
        self._events: dict[str, _FakeRunEvent] = {}

    def record_approval(self, candidate_id, approval_input, *, authenticated_actor,
                        idempotency_key):
        if idempotency_key in self._events:              # durable idempotency
            return self._events[idempotency_key]
        loaded = self.approvals.get((approval_input.request_id, candidate_id))
        if loaded is None:
            raise AssertionError(
                "record_approval found no PlanApproval on file: the approvals sink "
                "did not deposit the authoritative record before record_approval")
        assert loaded.actor_id == authenticated_actor
        assert loaded.decision == approval_input.decision
        self.record_calls.append(
            (candidate_id, approval_input, authenticated_actor, idempotency_key))
        ev = _FakeRunEvent(
            event_id=f"ev-{len(self._events) + 1}", candidate_id=candidate_id,
            decision=approval_input.decision, actor=authenticated_actor,
            idempotency_key=idempotency_key)
        self._events[idempotency_key] = ev
        return ev

    def freeze_and_admit_candidate(self, candidate_id, *, reservation_id,
                                   approval_event_id, idempotency_key):
        self.freeze_calls.append(
            (candidate_id, reservation_id, approval_event_id, idempotency_key))
        return (f"plan-{candidate_id[:6]}", f"admitted-{candidate_id[:6]}")


class _FakePresetRecord:
    def __init__(self, digest: str) -> None:
        self._digest = digest

    def semantic_digest(self) -> str:
        return self._digest


class _FakePresetRegistry:
    def __init__(self, presets: dict[str, str]) -> None:
        self._presets = dict(presets)

    def get(self, preset_id: str) -> _FakePresetRecord:
        if preset_id not in self._presets:
            raise PlanPresetError(f"no plan preset registered for {preset_id!r}")
        return _FakePresetRecord(self._presets[preset_id])


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _pending(request_id="r-1", candidate=CAND_A, *, source=PlanSource.DYNAMIC,
             goal="g", preset_id=None, preset_record_digest=None,
             budget_llm=0, worker_ids=("text.sentiment",)) -> PendingPlanApproval:
    diff = PlanDiff(
        baseline_kind="none", request_digest=REQ_DIGEST,
        candidate_plan_digest=candidate,
        entries=(PlanDiffEntry(pointer="goal", change="added",
                               baseline_json=None, candidate_json='"v"'),))
    dd = diff.semantic_digest()
    ref = TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="o-" + candidate[:6],
                               content_digest=dd))
    return PendingPlanApproval(
        request_id=request_id, candidate_plan_digest=candidate, goal=goal,
        source=source, approval_policy=ApprovalPolicy.REQUIRED, node_count=1,
        worker_ids=worker_ids, budget_request_llm_invocations=budget_llm,
        plan_diff_ref=ref, rendered_md="# Plan Diff body", rendered_from_diff_digest=dd,
        candidate_id="cand-" + candidate[:6], requested_at=NOW,
        preset_id=preset_id, preset_record_digest=preset_record_digest)


def _preset_pending(request_id="r-9", candidate=CAND_D, budget_llm=0):
    return _pending(request_id=request_id, candidate=candidate,
                    source=PlanSource.PRESET_FALLBACK, preset_id=PRESET_ID,
                    preset_record_digest=PRESET_REC_DIG, budget_llm=budget_llm)


def _make_coord(tmp_path, *, admission=None, verifier=None, lease=False):
    admission = admission if admission is not None else _FakeAdmission()

    def _sink(approval):
        admission.approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    kw = {}
    if lease:
        kw = dict(preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
                  catalog_digest=CAT_DIG, registry_digest=REG_DIG)
    coord = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=admission, clock=_FixedClock(),
        verifier=verifier if verifier is not None else _FakeVerifier(),
        approvals_sink=_sink, **kw)
    return coord, admission


def _plan_admit_bridge(admission):
    def _admit(approval, event):
        return admit_after_approval(
            admission=admission, candidate_id=approval.candidate_plan_digest,
            reservation_id="resv-1", approval_event_id=event.event_id,
            idempotency_key="adm:" + approval.candidate_plan_digest[:10])
    return _admit


def _lease_context(preset_id):
    return {"preset_record_digest": PRESET_REC_DIG,
            "catalog_digest": CAT_DIG, "registry_digest": REG_DIG}


def _client(tmp_path, *, coord=None, admission=None, actor=GOOD, plan_admit=None,
            lease_context=None):
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    router = build_console_router(
        store=store, plan_approval_coordinator=coord, plan_approval_actor=actor,
        plan_admit=plan_admit, plan_lease_context=lease_context)
    app.include_router(router)
    return TestClient(app), store


# =========================================================================== #
# unwired -> honest 503 on every plan-approval endpoint                        #
# =========================================================================== #
def test_unwired_returns_honest_503_never_fake_success(tmp_path):
    c, _ = _client(tmp_path, coord=None)
    probes = [
        ("GET", "/console/plan/approvals", None),
        ("GET", "/console/plan/approvals/status?request_id=r-1&candidate_plan_digest=" + CAND_A, None),
        ("POST", "/console/plan/approvals/decide",
         {"request_id": "r-1", "candidate_plan_digest": CAND_A, "decision": "approved"}),
        ("GET", "/console/plan/approvals/leases", None),
        ("POST", "/console/plan/approvals/lease",
         {"preset_id": PRESET_ID, "valid_until": VALID_UNTIL.isoformat(),
          "max_admissions": 3, "budget_cap": 10, "reason": "x"}),
        ("POST", "/console/plan/approvals/lease/revoke", {"lease_id": "z" * 64, "reason": "x"}),
    ]
    for method, url, body in probes:
        r = c.request(method, url, json=body)
        assert r.status_code == 503, (method, url, r.status_code)
        j = r.json()
        assert j["ok"] is False and "reason" in j, (method, url, j)


# =========================================================================== #
# GET /plan/approvals — public pending-card JSON                               #
# =========================================================================== #
def test_list_pending_returns_public_card_json(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, _ = _client(tmp_path, coord=coord, admission=admission)
    coord.register_pending(_pending(), idempotency_key="k1")

    r = c.get("/console/plan/approvals")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and len(j["items"]) == 1
    item = j["items"][0]
    assert item["request_id"] == "r-1"
    assert item["candidate_plan_digest"] == CAND_A
    assert item["goal"] == "g" and item["source"] == "dynamic"
    assert item["node_count"] == 1 and item["worker_ids"] == ["text.sentiment"]
    assert item["rendered_md"] == "# Plan Diff body"
    # the typed PlanDiff@1 reference survives the public dump.
    assert item["plan_diff_ref"]["schema_ref"]["name"] == "PlanDiff"


# =========================================================================== #
# POST /plan/approvals/decide — approve admits + emits ordered events          #
# =========================================================================== #
def test_decide_approve_records_admits_and_mirrors_events(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, store = _client(tmp_path, coord=coord, admission=admission,
                       plan_admit=_plan_admit_bridge(admission))
    coord.register_pending(_pending(), idempotency_key="k1")

    r = c.post("/console/plan/approvals/decide",
               json={"request_id": "r-1", "candidate_plan_digest": CAND_A,
                     "decision": "approved", "reason": "looks good"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["decision"] == "approved"
    assert j["candidate_plan_digest"] == CAND_A and j["admitted"] is True

    # the sink deposited the authoritative approval -> record_approval loaded it.
    assert len(admission.record_calls) == 1
    cand_id, submission, actor, _ = admission.record_calls[0]
    assert cand_id == CAND_A and actor == "admin-1"
    # admit_after_approval -> freeze_and_admit_candidate happened before admitted:true.
    assert len(admission.freeze_calls) == 1
    assert admission.freeze_calls[0][0] == CAND_A

    # reserved-session mirror: request then resolved, in id order, digest-bound.
    evs = store.read_events(RESERVED_SID)
    kinds = [e["type"] for e in evs]
    assert kinds == ["plan_approval_request", "plan_approval_resolved"]
    req = evs[0]
    assert req["candidate_plan_digest"] == CAND_A and req["node_count"] == 1
    assert req["goal"] == "g" and req["source"] == "dynamic"
    assert req["rendered_md"] == "# Plan Diff body"
    res = evs[1]
    assert res["decision"] == "approved" and res["actor_id"] == "admin-1"
    assert res["reason"] == "looks good" and res["candidate_plan_digest"] == CAND_A
    assert evs[0]["id"] < evs[1]["id"]

    # durable decision re-readable via status.
    st = c.get("/console/plan/approvals/status",
               params={"request_id": "r-1", "candidate_plan_digest": CAND_A}).json()
    assert st["ok"] is True and st["decision"] == "approved"
    assert st["actor_id"] == "admin-1"


def test_decide_reject_records_no_admit(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, store = _client(tmp_path, coord=coord, admission=admission,
                       plan_admit=_plan_admit_bridge(admission))
    coord.register_pending(_pending(), idempotency_key="k1")

    r = c.post("/console/plan/approvals/decide",
               json={"request_id": "r-1", "candidate_plan_digest": CAND_A,
                     "decision": "rejected", "reason": "no"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["decision"] == "rejected" and j["admitted"] is False
    assert len(admission.record_calls) == 1     # rejection still records
    assert admission.freeze_calls == []          # never freeze a rejected plan
    kinds = [e["type"] for e in store.read_events(RESERVED_SID)]
    assert kinds[-1] == "plan_approval_resolved"


def test_decide_unknown_candidate_is_404(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, _ = _client(tmp_path, coord=coord, admission=admission)
    r = c.post("/console/plan/approvals/decide",
               json={"request_id": "r-1", "candidate_plan_digest": CAND_A,
                     "decision": "approved"})
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_decide_double_decide_conflict_is_409(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, _ = _client(tmp_path, coord=coord, admission=admission,
                   plan_admit=_plan_admit_bridge(admission))
    coord.register_pending(_pending(), idempotency_key="k1")
    c.post("/console/plan/approvals/decide",
           json={"request_id": "r-1", "candidate_plan_digest": CAND_A, "decision": "approved"})
    r = c.post("/console/plan/approvals/decide",
               json={"request_id": "r-1", "candidate_plan_digest": CAND_A, "decision": "rejected"})
    assert r.status_code == 409
    j = r.json()
    assert j["ok"] is False
    assert j.get("decision") == "approved"       # the stored terminal decision


def test_decide_unverified_actor_is_403_and_persists_nothing(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, store = _client(tmp_path, coord=coord, admission=admission, actor=BAD,
                       plan_admit=_plan_admit_bridge(admission))
    coord.register_pending(_pending(), idempotency_key="k1")
    r = c.post("/console/plan/approvals/decide",
               json={"request_id": "r-1", "candidate_plan_digest": CAND_A, "decision": "approved"})
    assert r.status_code == 403
    assert r.json()["ok"] is False
    # nothing recorded / admitted; the card stays pending.
    assert admission.record_calls == [] and admission.freeze_calls == []
    assert coord.load_decision("r-1", CAND_A) is None
    assert len(coord.list_pending()) == 1


# =========================================================================== #
# GET /plan/approvals/status — decided / pending / unknown triple              #
# =========================================================================== #
def test_status_triple(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, _ = _client(tmp_path, coord=coord, admission=admission,
                   plan_admit=_plan_admit_bridge(admission))
    # pending (registered, undecided)
    coord.register_pending(_pending(request_id="r-1", candidate=CAND_A), idempotency_key="k1")
    st = c.get("/console/plan/approvals/status",
               params={"request_id": "r-1", "candidate_plan_digest": CAND_A}).json()
    assert st["ok"] is True and st["decision"] is None and st["pending"] is True

    # decided
    c.post("/console/plan/approvals/decide",
           json={"request_id": "r-1", "candidate_plan_digest": CAND_A, "decision": "approved"})
    st = c.get("/console/plan/approvals/status",
               params={"request_id": "r-1", "candidate_plan_digest": CAND_A}).json()
    assert st["decision"] == "approved"

    # unknown pair -> 404
    r = c.get("/console/plan/approvals/status",
              params={"request_id": "r-x", "candidate_plan_digest": CAND_D})
    assert r.status_code == 404


# =========================================================================== #
# regression: existing console routes untouched                                #
# =========================================================================== #
def test_existing_confirm_and_sessions_untouched(tmp_path):
    coord, admission = _make_coord(tmp_path)
    c, _ = _client(tmp_path, coord=coord, admission=admission)
    # /confirm with no pending turn keeps its honest byte-identical shape.
    r = c.post("/console/confirm", json={"turn_id": "nope", "choice": "y"}).json()
    assert r == {"ok": False, "reason": "no pending confirm"}
    # session CRUD still works with the coordinator wired.
    meta = c.post("/console/sessions", json={"title": "t"}).json()["meta"]
    assert meta["title"] == "t"
    assert any(m["id"] == meta["id"] for m in c.get("/console/sessions").json()["sessions"])


# =========================================================================== #
# lease surface                                                                #
# =========================================================================== #
def test_lease_issue_list_revoke_happy(tmp_path):
    coord, admission = _make_coord(tmp_path, lease=True)
    c, _ = _client(tmp_path, coord=coord, admission=admission, lease_context=_lease_context)

    r = c.post("/console/plan/approvals/lease",
               json={"preset_id": PRESET_ID, "valid_from": VALID_FROM.isoformat(),
                     "valid_until": VALID_UNTIL.isoformat(), "max_admissions": 3,
                     "budget_cap": 10, "reason": "daily standing approval"})
    assert r.status_code == 200, r.text
    lease_id = r.json()["lease"]["lease_id"]
    assert lease_id

    lst = c.get("/console/plan/approvals/leases").json()
    assert lst["ok"] is True and len(lst["leases"]) == 1
    view = lst["leases"][0]
    assert view["status"] == "active"
    assert view["admissions_used"] == 0 and view["admissions_remaining"] == 3
    assert view["budget_remaining"] == 10

    rv = c.post("/console/plan/approvals/lease/revoke",
                json={"lease_id": lease_id, "reason": "done"})
    assert rv.status_code == 200, rv.text
    lst = c.get("/console/plan/approvals/leases").json()
    assert lst["leases"][0]["status"] == "revoked"


def test_leases_endpoint_folds_at_coordinator_clock_not_wall_clock(tmp_path):
    # Regression (authoritative-clock doctrine): GET /plan/approvals/leases must
    # fold lease balances at the coordinator's injected clock, never datetime.now().
    # The window below (2026-07-20 -> 2026-07-21) is already in the past relative to
    # any real wall clock, so a wall-clock read would fold the lease "expired"; the
    # fixed coordinator clock sits INSIDE the window, so the honest status is "active".
    class _WindowClock:
        def now(self) -> datetime:
            return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    admission = _FakeAdmission()

    def _sink(approval):
        admission.approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    coord = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=admission, clock=_WindowClock(),
        verifier=_FakeVerifier(), approvals_sink=_sink,
        preset_registry=_FakePresetRegistry({PRESET_ID: PRESET_REC_DIG}),
        catalog_digest=CAT_DIG, registry_digest=REG_DIG)
    c, _ = _client(tmp_path, coord=coord, admission=admission, lease_context=_lease_context)

    r = c.post("/console/plan/approvals/lease",
               json={"preset_id": PRESET_ID, "valid_from": VALID_FROM.isoformat(),
                     "valid_until": VALID_UNTIL.isoformat(), "max_admissions": 3,
                     "budget_cap": 10, "reason": "clock doctrine"})
    assert r.status_code == 200, r.text

    lst = c.get("/console/plan/approvals/leases").json()
    assert lst["ok"] is True and len(lst["leases"]) == 1
    # coordinator now (2026-07-20 12:00) is inside the window -> active; a wall-clock
    # read (today) would be past valid_until -> "expired". Active proves the fix.
    assert lst["leases"][0]["status"] == "active"


def test_lease_issue_unverified_is_403(tmp_path):
    coord, admission = _make_coord(tmp_path, lease=True)
    c, _ = _client(tmp_path, coord=coord, admission=admission, actor=BAD,
                   lease_context=_lease_context)
    r = c.post("/console/plan/approvals/lease",
               json={"preset_id": PRESET_ID, "valid_until": VALID_UNTIL.isoformat(),
                     "max_admissions": 3, "budget_cap": 10, "reason": "x"})
    assert r.status_code == 403
    assert r.json()["ok"] is False


def test_lease_issue_drifted_digest_is_409(tmp_path):
    coord, admission = _make_coord(tmp_path, lease=True)

    def _drifted(preset_id):
        return {"preset_record_digest": "9" * 64, "catalog_digest": CAT_DIG,
                "registry_digest": REG_DIG}

    c, _ = _client(tmp_path, coord=coord, admission=admission, lease_context=_drifted)
    r = c.post("/console/plan/approvals/lease",
               json={"preset_id": PRESET_ID, "valid_until": VALID_UNTIL.isoformat(),
                     "max_admissions": 3, "budget_cap": 10, "reason": "x"})
    assert r.status_code == 409
    assert r.json()["ok"] is False


def test_lease_admitted_surfaces_in_reserved_feed(tmp_path):
    coord, admission = _make_coord(tmp_path, lease=True)
    c, store = _client(tmp_path, coord=coord, admission=admission,
                       lease_context=_lease_context)
    # issue an active lease for the preset.
    c.post("/console/plan/approvals/lease",
           json={"preset_id": PRESET_ID, "valid_from": VALID_FROM.isoformat(),
                 "valid_until": VALID_UNTIL.isoformat(), "max_admissions": 3,
                 "budget_cap": 10, "reason": "standing"})
    # an external preset candidate is registered + auto-admitted under the lease.
    outcome = coord.register_and_try_lease(
        _preset_pending(), idempotency_key="k9", now=NOW,
        candidate_catalog_digest=CAT_DIG, candidate_registry_digest=REG_DIG)
    assert outcome.outcome == "lease_admitted"

    kinds = [e["type"] for e in store.read_events(RESERVED_SID)]
    assert "plan_lease_issued" in kinds
    assert "plan_lease_admitted" in kinds
    admitted = [e for e in store.read_events(RESERVED_SID)
                if e["type"] == "plan_lease_admitted"][0]
    assert admitted["candidate_plan_digest"] == CAND_D
