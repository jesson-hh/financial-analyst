# -*- coding: utf-8 -*-
"""R21 — the production approval verifier (config-declared operator allowlist).

Covers ``guanlan_v2.orchestration.adapters.identity``:

* :func:`load_operator_allowlist` — the strict UTF-8-no-BOM, ``extra="forbid"``
  loader over ``config/orchestration/operators.json`` (house convention: the same
  shape ``plan_presets.load_preset_registry`` uses for
  ``config/orchestration/presets/*.json``);
* :class:`ConfigOperatorVerifier` — the fail-closed ``verify(actor) -> principal``
  port that :class:`~guanlan_v2.orchestration.approval.PlanApprovalCoordinator`
  consumes. Every refusal is a typed :class:`OperatorIdentityError`; there is no
  default operator, no wildcard and no "allow when the list is empty";
* the R21 acceptance itself — a **real** ``PlanApprovalCoordinator`` built with
  this verifier records a **real** ``PlanApproval`` (both against the lightweight
  Phase-7 harness AND against the real Phase-2 ``PlanAdmissionService``, which
  produces a real ``PLAN_APPROVED`` ``RunEvent``);
* the lease-actor decision — ``lease:*`` is REFUSED by the verifier (it is never a
  human operator), while the verifier-free lease path
  (``register_and_try_lease`` -> ``_admit_under_lease``) keeps stamping
  ``actor_id=lease:<id>`` exactly as before.

Run: ``python -m pytest tests/orchestration/test_operator_identity.py -v``
"""
from __future__ import annotations

import codecs
import inspect
import json
from pathlib import Path

import pytest

from guanlan_v2.orchestration.approval import (
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.enums import ApprovalDecision, PlanSource
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.plan_diff import build_pending_plan_approval, build_plan_diff

from guanlan_v2.orchestration.adapters.identity import (
    DEFAULT_OPERATOR_ALLOWLIST_PATH,
    ConfigOperatorVerifier,
    OperatorAllowlistError,
    OperatorIdentityError,
    OperatorNotAllowed,
    load_operator_allowlist,
)

# the lightweight Phase-7 approval harness (house pattern: sibling-test reuse).
from tests.orchestration.test_approval_lease import _coord, _issue, _pending, _try

OPERATOR = "human:ops"
OTHER_OPERATOR = "human:reviewer"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _write_allowlist(path: Path, actor_ids, *, schema_version: str = "1") -> Path:
    doc = {
        "schema_version": schema_version,
        "operators": [{"actor_id": a, "note": "test operator"} for a in actor_ids],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _verifier(tmp_path, actor_ids=(OPERATOR,)) -> ConfigOperatorVerifier:
    return ConfigOperatorVerifier(
        allowlist_path=_write_allowlist(tmp_path / "operators.json", actor_ids))


# =========================================================================== #
# The principal shape — the trap the survey flagged                            #
# =========================================================================== #
def test_approval_py_reads_principal_dot_actor_at_source():
    """Pin the attribute ``approval.py`` actually reads (NOT ``actor_id``).

    The existing ``tests/orchestration/test_adapters_api.py::_StubVerifier`` returns
    ``SimpleNamespace(actor_id=...)`` — it is only used on construction paths, so
    nothing catches the mismatch. This test makes the real contract explicit so a
    future edit on either side breaks loudly.
    """
    for method in (PlanApprovalCoordinator.decide,
                   PlanApprovalCoordinator.issue_lease,
                   PlanApprovalCoordinator.revoke_lease):
        src = inspect.getsource(method)
        assert "self._verifier.verify(actor)" in src
        assert "principal.actor" in src
        assert "principal.actor_id" not in src


def test_verify_returns_a_principal_whose_dot_actor_is_the_declared_id(tmp_path):
    principal = _verifier(tmp_path).verify(OPERATOR)
    assert isinstance(principal, AuthenticatedAdminPrincipal)
    assert principal.actor == OPERATOR
    assert principal.verified_by and isinstance(principal.verified_by, str)
    # the returned attribute name is the one approval.py reads.
    assert getattr(principal, "actor") == OPERATOR


def test_verify_picks_the_matching_declared_id_out_of_several(tmp_path):
    v = _verifier(tmp_path, (OPERATOR, OTHER_OPERATOR))
    assert v.verify(OTHER_OPERATOR).actor == OTHER_OPERATOR
    assert v.verify(OPERATOR).actor == OPERATOR


# =========================================================================== #
# Fail-closed: the actor side                                                  #
# =========================================================================== #
def test_unknown_actor_is_refused(tmp_path):
    with pytest.raises(OperatorNotAllowed):
        _verifier(tmp_path).verify("human:intruder")


@pytest.mark.parametrize("actor", ["", "   ", "\t", "\n"])
def test_blank_actor_is_refused(tmp_path, actor):
    with pytest.raises(OperatorIdentityError):
        _verifier(tmp_path).verify(actor)


@pytest.mark.parametrize(
    "actor", [None, 123, 1.0, True, b"human:ops", ["human:ops"], {"actor": "human:ops"},
              object()])
def test_non_string_actor_is_refused(tmp_path, actor):
    with pytest.raises(OperatorIdentityError):
        _verifier(tmp_path).verify(actor)


def test_no_wildcard_actor_and_no_default_operator(tmp_path):
    v = _verifier(tmp_path, (OPERATOR,))
    for probe in ("*", "any", "default", "admin", "human:*"):
        with pytest.raises(OperatorNotAllowed):
            v.verify(probe)


def test_actor_match_is_exact_no_trim_no_casefold(tmp_path):
    v = _verifier(tmp_path, (OPERATOR,))
    for probe in (" human:ops", "human:ops ", "HUMAN:OPS", "Human:Ops"):
        with pytest.raises(OperatorIdentityError):
            v.verify(probe)


# =========================================================================== #
# Fail-closed: the declaration side                                            #
# =========================================================================== #
def test_missing_config_refuses(tmp_path):
    v = ConfigOperatorVerifier(allowlist_path=tmp_path / "nope.json")
    with pytest.raises(OperatorAllowlistError):
        v.verify(OPERATOR)
    with pytest.raises(OperatorAllowlistError):
        load_operator_allowlist(tmp_path / "nope.json")


def test_empty_allowlist_refuses(tmp_path):
    v = ConfigOperatorVerifier(allowlist_path=_write_allowlist(tmp_path / "o.json", ()))
    with pytest.raises(OperatorAllowlistError):
        v.verify(OPERATOR)


@pytest.mark.parametrize("body", [
    "not json at all",
    "[]",
    '{"operators": []}',                                   # missing schema_version
    '{"schema_version": "1"}',                             # missing operators
    '{"schema_version": "2", "operators": [{"actor_id": "human:ops"}]}',  # bad version
    '{"schema_version": "1", "operators": "human:ops"}',   # not a list
    '{"schema_version": "1", "operators": ["human:ops"]}', # not a row object
    '{"schema_version": "1", "operators": [{"actor_id": "human:ops"}], "x": 1}',  # extra
    '{"schema_version": "1", "operators": [{"id": "human:ops"}]}',  # extra/renamed key
    '{"schema_version": "1", "operators": [{"actor_id": 7}]}',      # not a string
])
def test_malformed_config_refuses(tmp_path, body):
    path = tmp_path / "o.json"
    path.write_text(body, encoding="utf-8")
    v = ConfigOperatorVerifier(allowlist_path=path)
    with pytest.raises(OperatorAllowlistError):
        v.verify(OPERATOR)


def test_config_with_a_byte_order_mark_refuses(tmp_path):
    path = tmp_path / "o.json"
    path.write_bytes(
        codecs.BOM_UTF8
        + json.dumps({"schema_version": "1",
                      "operators": [{"actor_id": OPERATOR}]}).encode("utf-8"))
    with pytest.raises(OperatorAllowlistError):
        ConfigOperatorVerifier(allowlist_path=path).verify(OPERATOR)


def test_config_that_is_a_directory_refuses(tmp_path):
    d = tmp_path / "operators.json"
    d.mkdir()
    with pytest.raises(OperatorAllowlistError):
        ConfigOperatorVerifier(allowlist_path=d).verify(OPERATOR)


def test_duplicate_declared_ids_refuse(tmp_path):
    v = ConfigOperatorVerifier(
        allowlist_path=_write_allowlist(tmp_path / "o.json", (OPERATOR, OPERATOR)))
    with pytest.raises(OperatorAllowlistError):
        v.verify(OPERATOR)


@pytest.mark.parametrize("declared", [
    "", "   ", " human:ops", "human:ops ", "human ops", "human:o\tps", "human:o\nps",
    "*", "lease:abc", "LEASE:abc",
])
def test_an_unusable_declared_id_poisons_the_whole_declaration(tmp_path, declared):
    """A bad row is never skipped — the WHOLE declaration is refused (no partial trust)."""
    v = ConfigOperatorVerifier(
        allowlist_path=_write_allowlist(tmp_path / "o.json", (OPERATOR, declared)))
    with pytest.raises(OperatorAllowlistError):
        v.verify(OPERATOR)


def test_declaration_is_reread_on_every_verify(tmp_path):
    """The declaration in force at DECISION time is authoritative (no start-up cache)."""
    path = _write_allowlist(tmp_path / "o.json", (OPERATOR, OTHER_OPERATOR))
    v = ConfigOperatorVerifier(allowlist_path=path)
    assert v.verify(OTHER_OPERATOR).actor == OTHER_OPERATOR
    _write_allowlist(path, (OPERATOR,))  # operator revoked out of band
    with pytest.raises(OperatorNotAllowed):
        v.verify(OTHER_OPERATOR)
    assert v.verify(OPERATOR).actor == OPERATOR


def test_construction_never_touches_disk(tmp_path):
    """A missing config must not break construction — it must break the DECISION."""
    ConfigOperatorVerifier(allowlist_path=tmp_path / "absent.json")  # no raise


def test_every_refusal_is_one_typed_family():
    assert issubclass(OperatorAllowlistError, OperatorIdentityError)
    assert issubclass(OperatorNotAllowed, OperatorIdentityError)
    assert issubclass(OperatorIdentityError, Exception)
    # the two refusal reasons stay distinguishable (a config fault is not a denial).
    assert not issubclass(OperatorAllowlistError, OperatorNotAllowed)
    assert not issubclass(OperatorNotAllowed, OperatorAllowlistError)


# =========================================================================== #
# The shipped repo declaration                                                 #
# =========================================================================== #
def test_the_shipped_repo_declaration_loads_and_is_non_empty():
    assert DEFAULT_OPERATOR_ALLOWLIST_PATH.name == "operators.json"
    assert DEFAULT_OPERATOR_ALLOWLIST_PATH.parent.name == "orchestration"
    assert DEFAULT_OPERATOR_ALLOWLIST_PATH.parent.parent.name == "config"
    assert DEFAULT_OPERATOR_ALLOWLIST_PATH.exists(), (
        f"the shipped operator declaration is missing: {DEFAULT_OPERATOR_ALLOWLIST_PATH}")
    declared = load_operator_allowlist(DEFAULT_OPERATOR_ALLOWLIST_PATH)
    assert isinstance(declared, tuple) and declared
    assert all(isinstance(a, str) and a and not a.startswith("lease:") for a in declared)


def test_the_default_verifier_accepts_a_shipped_operator_and_refuses_others():
    declared = load_operator_allowlist(DEFAULT_OPERATOR_ALLOWLIST_PATH)
    v = ConfigOperatorVerifier()  # default path = the shipped declaration
    assert v.verify(declared[0]).actor == declared[0]
    with pytest.raises(OperatorNotAllowed):
        v.verify("human:not-declared")


# =========================================================================== #
# The lease-actor decision — REFUSED by the verifier                            #
# =========================================================================== #
def test_lease_prefixed_actor_is_refused_by_the_verifier(tmp_path):
    v = _verifier(tmp_path)
    for probe in ("lease:" + "a" * 64, "lease:", "lease:whatever"):
        with pytest.raises(OperatorIdentityError):
            v.verify(probe)


def test_a_lease_actor_cannot_be_declared_into_the_allowlist(tmp_path):
    """Refused on the DECLARATION side too — a lease id cannot be smuggled onto
    the list even by an operator with config write access."""
    path = _write_allowlist(tmp_path / "o.json", (OPERATOR, "lease:" + "a" * 64))
    with pytest.raises(OperatorAllowlistError):
        load_operator_allowlist(path)
    # …and the whole declaration is refused, so even the good row stops verifying.
    with pytest.raises(OperatorAllowlistError):
        ConfigOperatorVerifier(allowlist_path=path).verify(OPERATOR)


def test_the_verifier_free_lease_path_still_admits_with_a_lease_actor(tmp_path):
    """The lease channel never consults the verifier — refusing ``lease:*`` at
    :meth:`verify` costs Lane 0 nothing."""
    v = _verifier(tmp_path)
    coord = _coord(tmp_path, verifier=v)
    lease = _issue(coord, actor=OPERATOR)
    out = _try(coord, _pending())
    assert out.outcome == "lease_admitted"
    assert out.approval.actor_id == f"lease:{lease.lease_id}"
    # …and that very actor id is refused if anyone tries to sign a decision with it.
    with pytest.raises(OperatorIdentityError):
        v.verify(out.approval.actor_id)


def test_decide_with_a_forged_lease_actor_refuses_and_records_nothing(tmp_path):
    """The hole this decision closes: a forged ``lease:<id>`` decision would mint a
    lease-signed approval with no lease, no ``lease_consumed`` row and no envelope."""
    v = _verifier(tmp_path)
    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coord(tmp_path, verifier=v, name=journal.name)
    pending = _pending()
    coord.register_pending(pending, idempotency_key="k1")
    before = journal.read_bytes()
    with pytest.raises(OperatorIdentityError):
        coord.decide(
            request_id=pending.request_id,
            candidate_plan_digest=pending.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor="lease:" + "a" * 64,
            reason="forged", idempotency_key="d1")
    assert journal.read_bytes() == before, "a refused decision must append no row"
    assert coord.load_decision(
        pending.request_id, pending.candidate_plan_digest) is None


# =========================================================================== #
# R21 acceptance (a) — the real coordinator records a real PlanApproval         #
# =========================================================================== #
def test_real_coordinator_decide_succeeds_for_an_allowlisted_actor(tmp_path):
    v = _verifier(tmp_path)
    coord = _coord(tmp_path, verifier=v)
    admission = coord._admission  # the harness' recording fake
    pending = _pending()
    coord.register_pending(pending, idempotency_key="k1")
    approval, event = coord.decide(
        request_id=pending.request_id,
        candidate_plan_digest=pending.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor=OPERATOR, reason="reviewed",
        idempotency_key="d1")
    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.actor_id == OPERATOR, "the VERIFIED id is what gets stamped"
    assert event is not None
    assert len(admission.calls) == 1
    assert coord.load_decision(
        pending.request_id, pending.candidate_plan_digest) == approval


def test_real_coordinator_decide_refuses_a_non_allowlisted_actor_and_records_nothing(
        tmp_path):
    v = _verifier(tmp_path)
    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coord(tmp_path, verifier=v)
    admission = coord._admission
    pending = _pending()
    coord.register_pending(pending, idempotency_key="k1")
    before = journal.read_bytes()
    with pytest.raises(OperatorNotAllowed):
        coord.decide(
            request_id=pending.request_id,
            candidate_plan_digest=pending.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor="human:intruder",
            reason="nope", idempotency_key="d1")
    assert journal.read_bytes() == before
    assert admission.calls == []
    assert coord.load_decision(
        pending.request_id, pending.candidate_plan_digest) is None


def test_real_coordinator_decide_refuses_when_the_declaration_is_missing(tmp_path):
    v = ConfigOperatorVerifier(allowlist_path=tmp_path / "absent.json")
    journal = tmp_path / "plan_approvals.jsonl"
    coord = _coord(tmp_path, verifier=v)
    pending = _pending()
    coord.register_pending(pending, idempotency_key="k1")
    before = journal.read_bytes()
    with pytest.raises(OperatorAllowlistError):
        coord.decide(
            request_id=pending.request_id,
            candidate_plan_digest=pending.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor=OPERATOR, reason="r",
            idempotency_key="d1")
    assert journal.read_bytes() == before
    assert coord.load_decision(
        pending.request_id, pending.candidate_plan_digest) is None


def test_issue_and_revoke_lease_are_gated_by_the_same_verifier(tmp_path):
    v = _verifier(tmp_path)
    coord = _coord(tmp_path, verifier=v)
    lease = _issue(coord, actor=OPERATOR)
    assert lease.issued_by == OPERATOR
    with pytest.raises(OperatorNotAllowed):
        _issue(coord, actor="human:intruder", purpose="sneaky")
    revocation = coord.revoke_lease(
        lease.lease_id, actor=OPERATOR, reason="done", idempotency_key="rv1")
    assert revocation.actor_id == OPERATOR
    with pytest.raises(OperatorIdentityError):
        coord.revoke_lease(
            lease.lease_id, actor="lease:" + lease.lease_id, reason="forged",
            idempotency_key="rv2")


# =========================================================================== #
# R21 acceptance (b) — against the REAL Phase-2 PlanAdmissionService            #
# =========================================================================== #
def test_r21_real_admission_service_records_a_real_plan_approval(tmp_path):
    """THE R21 closure assertion: a real ``PlanApprovalCoordinator`` built with the
    production verifier drives the real Phase-2 admission service to a real
    ``PLAN_APPROVED`` ``RunEvent`` and a frozen ``Plan`` — the framework's first
    decision point is no longer fail-closed-by-absence."""
    from tests.orchestration import test_dynamic_e2e as e2e

    env = e2e._build_env(request_id="req-r21",
                         fallback_preset_id=e2e.RESEARCH_BASELINE, run_id="run-r21")
    draft = e2e._materialize_fallback(env, draft_id="plan-r21")
    service = e2e._admission_service(env, draft)
    prep = service.prepare_candidate(draft.id, request_id=env.request.request_id)
    candidate_digest = prep.candidate_plan_digest
    _cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="res-r21")

    diff = build_plan_diff(draft, request=env.request,
                           candidate_plan_digest=candidate_digest,
                           baseline=None, baseline_kind="none")
    preset = e2e._preset_registry().get(e2e.RESEARCH_BASELINE)
    pending = build_pending_plan_approval(
        draft=draft, request=env.request, candidate_plan_digest=candidate_digest,
        diff=diff, plan_diff_ref=e2e._diff_ref(diff), planner_rationale=None,
        candidate_id="cand-r21", requested_at=env.clock.now(),
        preset_id=e2e.RESEARCH_BASELINE, preset_record_digest=preset.semantic_digest())
    assert pending.source is PlanSource.PRESET_FALLBACK

    def _sink(approval):
        env.approvals[(approval.request_id, approval.candidate_plan_digest)] = approval

    verifier = _verifier(tmp_path)
    coord = PlanApprovalCoordinator(
        tmp_path / "plan_approvals.jsonl", admission=service, clock=env.clock,
        verifier=verifier, console_emit=None, approvals_sink=_sink)
    coord.register_pending(pending, idempotency_key="pending-r21")

    # a non-allowlisted actor refuses HERE, on the real path, and records nothing.
    with pytest.raises(OperatorNotAllowed):
        coord.decide(request_id=env.request.request_id,
                     candidate_plan_digest=candidate_digest,
                     decision=ApprovalDecision.APPROVED, actor="human:intruder",
                     reason="nope", idempotency_key="decide-r21-bad")
    assert env.approvals == {}

    approval, event = coord.decide(
        request_id=env.request.request_id, candidate_plan_digest=candidate_digest,
        decision=ApprovalDecision.APPROVED, actor=OPERATOR, reason="reviewed by operator",
        idempotency_key="decide-r21")
    assert event.event_type is EventType.PLAN_APPROVED
    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.actor_id == OPERATOR
    assert env.approvals[(env.request.request_id, candidate_digest)] is approval

    plan, _admitted = admit_after_approval(
        admission=service, candidate_id=candidate_digest,
        reservation_id=res.reservation_id, approval_event_id=event.event_id,
        idempotency_key="freeze-r21")
    assert plan.plan_digest == candidate_digest


# =========================================================================== #
# Housekeeping — the new module must not disturb the sealed contract firewalls  #
# =========================================================================== #
def test_identity_module_defines_no_public_contract_model():
    """It is a SERVICE port, not a contract: it must define no public
    ``ContractModel`` subclass, so the Phase-1 discovery walk and the Phase-9
    classification firewall have nothing new to classify."""
    import guanlan_v2.orchestration.adapters.identity as ident
    from guanlan_v2.orchestration.digest import ContractModel, DigestModel

    for obj in vars(ident).values():
        if inspect.isclass(obj) and issubclass(obj, ContractModel):
            assert obj in (ContractModel, DigestModel) or obj.__module__ != ident.__name__ \
                or obj.__name__.startswith("_"), (
                f"{obj.__name__} would enter the completeness firewall unclassified")


def test_identity_module_imports_no_side_surface_and_reads_no_environment():
    """The verifier is an identity edge: no seats/trade/console-write surface on its
    import graph, and no environment variable may redirect or widen the authority
    list (an env-overridable allowlist path would undo the one claim it makes)."""
    import ast

    import guanlan_v2.orchestration.adapters.identity as ident

    tree = ast.parse(inspect.getsource(ident))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith(("guanlan_v2.seats", "guanlan_v2.console",
                                    "guanlan_v2.autonomy")), \
            f"identity.py must not import {name!r}"
    assert "os" not in imported and "dotenv" not in imported

    # no environment read anywhere in executable code (docstrings are exempt —
    # the module docstring must be free to SAY it reads no environment).
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("environ", "getenv"), \
                "identity.py must not read the environment"
        if isinstance(node, ast.Name):
            assert node.id != "getenv", "identity.py must not read the environment"
