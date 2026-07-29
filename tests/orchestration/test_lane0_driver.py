# -*- coding: utf-8 -*-
"""L2-a — the Lane-0 production driver.

Drives :mod:`guanlan_v2.orchestration.lane0_driver` over the SAME production
assembly the deep lane uses (the sealed Phase-9 catalog + cumulative registry +
``build_production_catalog_runtime`` / ``production_bridge_view``), with
in-memory stores and a scripted gateway. The gateway factory is the only
test/production difference.

The acceptance core (charter §测试) is
:func:`test_committed_snapshot_is_readable_by_the_deep_lane_reader`: the
``ContextSnapshot@1`` the driver commits is exactly what
``live_decide._latest_snapshot_production`` hands back — that is what proves the
deep lane will see it on its next tick.

Run: ``python -m pytest tests/orchestration/test_lane0_driver.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.enums import Confidence, RotationStage
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.market.factors import (
    MARKET_FACTOR_REPORT_SCHEMA_REF,
    DailyValueRow,
    EvidenceAnchor,
    HeatState,
    MainlineRead,
    MarketFactorInputs,
    RegimeReport,
    RiskState,
    RotationReport,
    TrendState,
    build_market_factor_set_v1,
)
from guanlan_v2.orchestration.pipeline.live_decide import _latest_snapshot_production

from guanlan_v2.orchestration import lane0_driver as L

UTC = timezone.utc
#: 2026-07-29 09:30 +08:00 — a weekday session, mid-morning.
AS_OF = datetime(2026, 7, 29, 1, 30, tzinfo=UTC)
SESSION = "2026-07-29"


# --------------------------------------------------------------------------- #
# an advancing clock (production's SystemClock advances; a frozen clock would   #
# make "which ContextSnapshot is latest" a coin flip)                          #
# --------------------------------------------------------------------------- #
class AdvancingClock:
    def __init__(self, start: datetime = AS_OF) -> None:
        self._start = start
        self._ticks = 0

    def now(self) -> datetime:
        self._ticks += 1
        return self._start + timedelta(microseconds=self._ticks)


# --------------------------------------------------------------------------- #
# scenario inputs                                                             #
# --------------------------------------------------------------------------- #
def _daily_rows(n: int, base: float = 30.0) -> tuple[DailyValueRow, ...]:
    end = datetime(2026, 7, 28, tzinfo=UTC)
    rows = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        rows.append(DailyValueRow(
            date=d.strftime("%Y-%m-%d"), value=base + i * 0.5,
            available_at=datetime(d.year, d.month, d.day, 7, 5, tzinfo=UTC)))
    return tuple(rows)


def happy_inputs() -> MarketFactorInputs:
    """A full ``limit_up_total`` window ⇒ a computable breadth factor."""
    return MarketFactorInputs(limit_up_total=_daily_rows(60))


def empty_inputs() -> MarketFactorInputs:
    """``load_market_factor_inputs``'s honest steady state: every series absent."""
    return MarketFactorInputs()


# --------------------------------------------------------------------------- #
# the scripted Lane-0 gateway (the ONLY test/production difference)            #
# --------------------------------------------------------------------------- #
class ScriptedLane0Gateway:
    """Guardrail-conformant scripted regime/rotation reads.

    Resolves the committed ``MarketFactorReport@1`` by scanning the payload
    backend (the pool is the driver's own; a fake gateway never gets one), so the
    scripted reports bind the exact ``factor_report_digest`` the run committed.
    """

    def __init__(self, *, stores, registry, fail_nodes=()) -> None:
        self._stores = stores
        self._registry = registry
        self._fail = set(fail_nodes)
        self._seed_factor_id = build_market_factor_set_v1().definitions[0].factor_id
        self.invocations: list[str] = []

    def _factor_report(self):
        best = None
        for stored in dict(self._stores._shared.backend.payloads).values():
            if getattr(stored, "schema_key", None) != MARKET_FACTOR_REPORT_SCHEMA_REF.key:
                continue
            best = stored.model
        return best

    def _anchor(self, report):
        if report is not None and report.feature_vector:
            fid, val = next(iter(report.feature_vector.items()))
            return EvidenceAnchor(factor_id=fid, value=val, reading="reading"), True
        fid = report.values[0].factor_id if report is not None else self._seed_factor_id
        return EvidenceAnchor(factor_id=fid, value=0.0, reading="no usable factor data"), False

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._stores.payloads)
        if record.node_id in self._fail:
            raise RuntimeError(f"scripted model failure on {record.node_id}")
        self.invocations.append(record.node_id)
        report = self._factor_report()
        anchor, confident = self._anchor(report)
        digest = report.content_digest if report is not None else "0" * 64
        as_of = report.as_of if report is not None else AS_OF
        if record.worker_id == "market.regime":
            payload = self._regime(as_of, digest, anchor, confident)
        elif record.worker_id == "market.rotation":
            payload = self._rotation(as_of, digest, anchor, confident)
        else:  # pragma: no cover — only the two Lane-0 LLM workers reach a gateway
            raise AssertionError(f"unexpected worker {record.worker_id!r}")
        return W.ModelResult(
            payload=payload, rendered_text="scripted", number_anchors=(),
            input_tokens=11, output_tokens=7, provider="fake", model="fake-lane0",
            provider_response_id=f"resp-{record.node_id}")

    def _regime(self, as_of, digest, anchor, confident) -> RegimeReport:
        if confident:
            return RegimeReport.build(
                as_of=as_of, factor_report_digest=digest,
                trend=TrendState.BULL, risk_state=RiskState.RISK_ON,
                heat_state=HeatState.NORMAL,
                trend_probabilities={TrendState.BULL: 0.6, TrendState.BEAR: 0.1,
                                     TrendState.RANGE: 0.2, TrendState.UNKNOWN: 0.1},
                risk_probabilities={RiskState.RISK_ON: 0.6, RiskState.RISK_OFF: 0.2,
                                    RiskState.NEUTRAL: 0.1, RiskState.UNKNOWN: 0.1},
                heat_probabilities={HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2,
                                    HeatState.UNKNOWN: 0.1},
                confidence=Confidence.MEDIUM, evidence=(anchor,), drivers=("breadth",),
                evidence_factor_ids=(anchor.factor_id,),
                narrative="Supportive breadth; advisory only, zero trading authority.")
        return RegimeReport.build(
            as_of=as_of, factor_report_digest=digest,
            trend=TrendState.UNKNOWN, risk_state=RiskState.UNKNOWN,
            heat_state=HeatState.UNKNOWN,
            trend_probabilities={TrendState.BULL: 0.0, TrendState.BEAR: 0.0,
                                 TrendState.RANGE: 0.0, TrendState.UNKNOWN: 1.0},
            risk_probabilities={RiskState.RISK_ON: 0.0, RiskState.RISK_OFF: 0.0,
                                RiskState.NEUTRAL: 0.0, RiskState.UNKNOWN: 1.0},
            heat_probabilities={HeatState.NORMAL: 0.0, HeatState.OVERHEAT: 0.0,
                                HeatState.UNKNOWN: 1.0},
            confidence=Confidence.LOW, evidence=(anchor,), drivers=("insufficient_data",),
            evidence_factor_ids=(anchor.factor_id,),
            narrative="No usable factor data; regime unknown (honest degraded read).",
            unknown_reason="insufficient factor coverage")

    def _rotation(self, as_of, digest, anchor, confident) -> RotationReport:
        if confident:
            mainline = MainlineRead(
                name="AI compute", universe_key="ai_compute", stage=RotationStage.SPREAD,
                strength=6.0, persistence="two-session inflow", evidence=(anchor,))
            return RotationReport.build(
                as_of=as_of, factor_report_digest=digest, mainlines=(mainline,),
                confidence=Confidence.MEDIUM, evidence_factor_ids=(anchor.factor_id,),
                narrative="AI compute leads; advisory only, zero trading authority.")
        return RotationReport.build(
            as_of=as_of, factor_report_digest=digest, mainlines=(),
            confidence=Confidence.LOW, evidence_factor_ids=(),
            narrative="No mainline discernible; themeless tape (honest degraded read).",
            unknown_reason="archive-young factors; no mainline signal")


# --------------------------------------------------------------------------- #
# the RAW-JSON gateway — what a real provider actually hands back              #
# --------------------------------------------------------------------------- #
class ScriptedJsonLane0Gateway:
    """Answers with raw JSON dicts, the way the live 2026-07-29 run's model did.

    Deliberately omits ``as_of`` / ``factor_report_digest`` / ``content_digest``
    (a model cannot know a full 64-hex digest — the rendered block header shows
    12 chars — and cannot compute its own self-seal), and wraps the rotation
    answer in the ``{"rotation_report": {...}}`` envelope the live run produced.
    """

    def __init__(self, *, stores, **_kw) -> None:
        self._stores = stores
        self.invocations: list[str] = []
        #: the EXACT authorized request bytes — what a real provider would see.
        self.canonical_requests: dict[str, str] = {}
        #: answer with an EMPTY citation list + self-written excuse (裁决 3 · B).
        self.empty_evidence = False

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._stores.payloads)
        self.invocations.append(record.node_id)
        self.canonical_requests[record.node_id] = (
            request.canonical_request_bytes.decode("utf-8"))
        if record.worker_id == "market.regime":
            payload = {
                "trend": "unknown", "risk_state": "unknown", "heat_state": "unknown",
                "trend_probabilities": {"牛": 0.0, "熊": 0.0, "震荡": 0.0, "unknown": 1.0},
                "risk_probabilities": {"risk_on": 0.0, "risk_off": 0.0,
                                       "neutral": 0.0, "unknown": 1.0},
                "heat_probabilities": {"normal": 0.0, "overheat": 0.0, "unknown": 1.0},
                "confidence": "low",
                "evidence": [{"factor_id": "breadth.limit_up_ema", "value": 0.0,
                              "reading": "coverage thin this session"}],
                "conflicts": [], "drivers": ["insufficient_coverage"],
                "evidence_factor_ids": ["breadth.limit_up_ema"],
                "narrative": "Advisory only; every axis carries its unknown mass.",
                "unknown_reason": "most of the battery is UNAVAILABLE",
            }
            if self.empty_evidence:
                payload.update(evidence=[], evidence_factor_ids=[],
                               unknown_reason="I decided not to cite anything")
        else:
            payload = {"rotation_report": {
                "mainlines": [], "confidence": "low", "conflicts": [],
                "narrative": "Themeless tape; advisory only, zero trading authority.",
                "evidence_factor_ids": [],
                "unknown_reason": "the rotation family is UNAVAILABLE (archive-young)",
            }}
        return W.ModelResult(
            payload=payload, rendered_text="{}", number_anchors=(),
            input_tokens=13, output_tokens=9, provider="fake-json",
            model="fake-lane0-json", provider_response_id=f"resp-{record.node_id}")


# --------------------------------------------------------------------------- #
# the in-memory environment (production assembly, test stores + test gateway)  #
# --------------------------------------------------------------------------- #
class Env:
    def __init__(self, *, fail_nodes=(), raw_json_gateway=False):
        from guanlan_v2.orchestration.adapters.chain import (
            PHASE9_BASE_REGISTRY_DIGEST,
            build_phase9_registry,
        )

        self.clock = AdvancingClock()
        self.registry = build_phase9_registry(PHASE9_BASE_REGISTRY_DIGEST)
        resolver = SchemaRegistryResolver()
        resolver.register(self.registry)
        self.stores = RuntimeStores(
            resolver=resolver, clock=self.clock,
            allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
        gateway_class = (ScriptedJsonLane0Gateway if raw_json_gateway
                         else ScriptedLane0Gateway)
        self.gateway = gateway_class(
            stores=self.stores, registry=self.registry, fail_nodes=fail_nodes)
        self.bindings = L.build_lane0_bindings(
            stores=self.stores, clock=self.clock,
            gateway_factory=lambda **_kw: self.gateway)

    def run(self, *, inputs=None, authorization=None, **kw):
        return L.run_lane0_bootstrap(
            authorization=(authorization if authorization is not None
                           else AlwaysGrantedAuthorization()),
            as_of=AS_OF, bindings=self.bindings,
            inputs=inputs if inputs is not None else happy_inputs(), **kw)


class AlwaysGrantedAuthorization:
    """A test stand-in for a HUMAN decision already recorded elsewhere.

    Deliberately shaped like the production
    :class:`~guanlan_v2.orchestration.lane0_driver.RecordedApprovalAuthorization`:
    it is handed the digest the driver computed and must deposit a real
    ``PlanApproval`` into the admission-owned store and go through
    ``record_approval``. Tests that assert on the production authorization use the
    real class over a temp journal.
    """

    def __init__(self, actor_id: str = "human:test") -> None:
        self.actor_id = actor_id
        self.calls: list[str] = []

    def grant(self, *, admission, request, candidate_plan_digest, approvals, now):
        from guanlan_v2.orchestration.admission import ApprovalSubmission
        from guanlan_v2.orchestration.enums import ApprovalDecision
        from guanlan_v2.orchestration.events import PlanApproval

        self.calls.append(candidate_plan_digest)
        approvals[(request.request_id, candidate_plan_digest)] = PlanApproval(
            request_id=request.request_id,
            candidate_plan_digest=candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor_id=self.actor_id,
            decided_at=now)
        event = admission.record_approval(
            candidate_plan_digest,
            ApprovalSubmission(request_id=request.request_id,
                               candidate_plan_digest=candidate_plan_digest,
                               decision=ApprovalDecision.APPROVED),
            authenticated_actor=self.actor_id,
            idempotency_key=f"lane0-approve:{candidate_plan_digest}")
        return L.Lane0Grant(
            candidate_plan_digest=candidate_plan_digest, actor_id=self.actor_id,
            approval_event_id=event.event_id, channel="test", decided_at=now)


# =========================================================================== #
# 1 — the acceptance core                                                      #
# =========================================================================== #
def test_committed_snapshot_is_readable_by_the_deep_lane_reader():
    env = Env()
    result = env.run()

    assert result.outcome == L.OUTCOME_COMPLETED, result.refusal_detail
    assert result.terminal_status == "completed"
    assert result.session_date == SESSION
    assert result.run_id == f"lane0-{SESSION}"
    assert result.llm_invocations == 2          # regime + rotation; the reader zero
    assert result.snapshot_ref is not None

    # THE acceptance assertion: the deep lane's own reader hands back exactly the
    # snapshot this driver committed — same object id, same digest, same identity.
    pair = _latest_snapshot_production(env.stores)
    assert pair is not None, "the deep lane would still refuse: no ContextSnapshot"
    context, ref = pair
    assert context.snapshot_id == result.snapshot_id
    assert context.content_digest == result.snapshot_digest
    assert ref == result.snapshot_ref
    assert result.snapshot_visible_to_deep_lane is True


def test_a_shadowed_snapshot_is_badged_rather_than_claimed_a_success(monkeypatch):
    # The driver's success claim is a READ-BACK, not an assertion. If the deep
    # lane's own reader would hand back some OTHER ContextSnapshot (a later
    # built_at from anywhere), the run still stands but must say so — the one
    # thing it may never do is report a snapshot nobody will read as visible.
    import guanlan_v2.orchestration.pipeline.live_decide as LD

    env = Env()
    monkeypatch.setattr(LD, "_latest_snapshot_production", lambda _stores: None)
    result = env.run()
    assert result.snapshot_visible_to_deep_lane is False
    assert "snapshot_shadowed" in result.degradation_badges
    assert result.outcome == L.OUTCOME_DEGRADED   # never reported as a clean success
    assert any("does NOT return this snapshot" in n for n in result.notes)
    # and the snapshot itself really did commit — the badge is about visibility.
    assert result.snapshot_ref is not None


def test_manifest_pins_the_snapshot_the_deep_lane_will_read():
    # the honest-manifest half: the BootstrapContextManifest's
    # context_snapshot_digest names the SAME snapshot the deep-lane reader returns
    # (the run also persists its own input-side bootstrap ContextSnapshot; a driver
    # that let that one win would badge a snapshot nobody reads).
    env = Env()
    result = env.run()
    context, _ref = _latest_snapshot_production(env.stores)
    assert result.manifest_context_snapshot_digest == context.content_digest


# =========================================================================== #
# 2 — never self-bind the stores                                               #
# =========================================================================== #
def test_production_bindings_refuse_when_the_process_store_is_unbound(monkeypatch):
    import guanlan_v2.orch_store_status as status

    monkeypatch.setattr(status, "orchestration_store_bound", lambda: False)
    monkeypatch.setattr(status, "orchestration_store_state", lambda: "not_attempted")
    with pytest.raises(L.Lane0StoresUnbound) as exc:
        L.build_production_lane0_bindings()
    assert "never self-binds" in str(exc.value)


def test_the_library_surface_never_binds_process_stores():
    # The guarantee is about the LIBRARY: no code path a caller (the server, a
    # scheduler, a test) can reach may bind the process stores — it must refuse.
    # A standalone `python -m` process is a different thing: it has no startup of
    # its own, so the CLI layer alone may perform the reviewed startup binding,
    # and only behind the explicit --bind-stores flag.
    import inspect

    library = (
        L.build_production_lane0_bindings, L.build_lane0_bindings,
        L.run_lane0_bootstrap, L.propose_lane0_candidate, L.approve_lane0_candidate,
        L.load_lane0_inputs, L.RecordedApprovalAuthorization,
        L._admission_for, L._reserve,
    )
    for banned in ("bind_process_durable_stores_and_scan", "bind_orchestration_stores",
                   "build_durable_runtime_stores"):
        for obj in library:
            assert banned not in inspect.getsource(obj), (
                f"{getattr(obj, '__name__', obj)} must never self-bind ({banned})")


def test_binding_the_cli_process_stores_is_opt_in():
    parsed = L.build_arg_parser().parse_args(["run"])
    assert parsed.bind_stores is False
    assert L.build_arg_parser().parse_args(["run", "--bind-stores"]).bind_stores is True


# =========================================================================== #
# 3 — never self-approve                                                       #
# =========================================================================== #
def test_missing_authorization_refuses_with_zero_side_effects():
    env = Env()
    before_payloads = len(dict(env.stores._shared.backend.payloads))
    before_events = len(list(env.stores._shared.backend.events))
    with pytest.raises(L.Lane0Unauthorized):
        L.run_lane0_bootstrap(
            authorization=None, as_of=AS_OF, bindings=env.bindings,
            inputs=happy_inputs())
    assert len(dict(env.stores._shared.backend.payloads)) == before_payloads
    assert len(list(env.stores._shared.backend.events)) == before_events
    assert env.gateway.invocations == []


def test_recorded_approval_authorization_refuses_an_undecided_candidate(tmp_path):
    env = Env()
    auth = L.RecordedApprovalAuthorization(
        clock=env.clock, journal_path=tmp_path / "plan_approvals.jsonl")
    with pytest.raises(L.Lane0Unauthorized) as exc:
        env.run(authorization=auth)
    assert "no recorded" in str(exc.value).lower() or "no plan approval" in str(exc.value).lower()
    assert env.gateway.invocations == []


def test_recorded_approval_authorization_cannot_decide_anything(tmp_path):
    # the structural half of "never self-approve": the coordinator the driver's
    # authorization builds carries NO verifier, so `decide` / `issue_lease` are
    # fail-closed refusals rather than a code path someone could later reach.
    from guanlan_v2.orchestration.approval import ApprovalAuthorityError
    from guanlan_v2.orchestration.enums import ApprovalDecision

    env = Env()
    auth = L.RecordedApprovalAuthorization(
        clock=env.clock, journal_path=tmp_path / "plan_approvals.jsonl")
    coordinator = auth.coordinator_for(admission=None, approvals={})
    assert coordinator._verifier is None
    with pytest.raises(ApprovalAuthorityError):
        coordinator.decide(
            request_id="req-x", candidate_plan_digest="a" * 64,
            decision=ApprovalDecision.APPROVED, actor="human:ops", reason=None,
            idempotency_key="k")


def test_a_recorded_human_approval_admits_the_run(tmp_path):
    # the reviewed channel end to end: an operator records a decision through the
    # verifier-backed coordinator (the CLI `approve` verb), and a LATER driver run
    # merely LOADS it.
    env = Env()
    journal = tmp_path / "plan_approvals.jsonl"
    allowlist = tmp_path / "operators.json"
    allowlist.write_text(
        '{"schema_version": "1", "operators": [{"actor_id": "human:ops", '
        '"note": "test operator"}]}', encoding="utf-8")

    proposal = L.propose_lane0_candidate(
        as_of=AS_OF, bindings=env.bindings, journal_path=journal)
    assert proposal.candidate_plan_digest
    assert env.gateway.invocations == []          # proposing burns nothing

    L.approve_lane0_candidate(
        candidate_plan_digest=proposal.candidate_plan_digest, actor="human:ops",
        as_of=AS_OF, bindings=env.bindings, journal_path=journal,
        allowlist_path=allowlist, reason="L2-a first real run")

    auth = L.RecordedApprovalAuthorization(clock=env.clock, journal_path=journal)
    result = env.run(authorization=auth)
    assert result.outcome == L.OUTCOME_COMPLETED, result.refusal_detail
    assert result.approval is not None
    assert result.approval.actor_id == "human:ops"
    assert result.approval.channel == "recorded_approval"


def test_an_undeclared_operator_cannot_approve(tmp_path):
    from guanlan_v2.orchestration.adapters.identity import OperatorNotAllowed

    env = Env()
    journal = tmp_path / "plan_approvals.jsonl"
    allowlist = tmp_path / "operators.json"
    allowlist.write_text(
        '{"schema_version": "1", "operators": [{"actor_id": "human:ops", '
        '"note": "test operator"}]}', encoding="utf-8")
    proposal = L.propose_lane0_candidate(
        as_of=AS_OF, bindings=env.bindings, journal_path=journal)
    with pytest.raises(OperatorNotAllowed):
        L.approve_lane0_candidate(
            candidate_plan_digest=proposal.candidate_plan_digest,
            actor="mallory", as_of=AS_OF, bindings=env.bindings,
            journal_path=journal, allowlist_path=allowlist,
            reason="not on the list")


# =========================================================================== #
# 4 — idempotent per session date                                              #
# =========================================================================== #
def test_same_session_rerun_reuses_the_snapshot_and_burns_no_tokens():
    env = Env()
    first = env.run()
    assert first.outcome == L.OUTCOME_COMPLETED
    assert len(env.gateway.invocations) == 2

    second = env.run()
    assert second.outcome == L.OUTCOME_REUSED
    assert second.reused is True
    assert second.llm_invocations == 0
    assert len(env.gateway.invocations) == 2      # NOT re-burned
    assert second.snapshot_digest == first.snapshot_digest
    assert second.snapshot_ref == first.snapshot_ref


def test_a_partially_executed_run_identity_refuses_instead_of_crashing():
    # Artifact envelopes are process-local upstream (``pool._PoolStorage``), so a
    # run identity whose layers are committed but whose snapshot never landed (a
    # crash in that window) cannot be replayed by a fresh process: ``ArtifactPool
    # .replay()`` raises PoolError. The driver detects that state FIRST and
    # refuses by name rather than dying inside the kernel.
    env = Env()
    first = env.run()
    # simulate the crash window: the layers are committed, the snapshot is gone.
    backend = env.stores._shared.backend
    for object_id, stored in list(backend.payloads.items()):
        if getattr(stored, "schema_key", None) == "ContextSnapshot@1":
            backend.payloads.pop(object_id)
    with pytest.raises(L.Lane0Refused) as exc:
        env.run()
    assert exc.value.code == "partial_prior_run"
    assert first.run_id in str(exc.value)
    assert len(env.gateway.invocations) == 2       # nothing re-burned


def test_run_identity_is_one_per_session_date():
    env = Env()
    assert L._run_identity_already_executed(env.stores, f"lane0-{SESSION}") is False
    env.run()
    assert L._run_identity_already_executed(env.stores, f"lane0-{SESSION}") is True


# --------------------------------------------------------------------------- #
# D4 — a spent run identity is a named refusal, never an IdempotencyConflict   #
# --------------------------------------------------------------------------- #
def _seed_terminal_run_result(env, *, terminal_status="cancelled", run_id=None):
    """Persist a prior terminal RunResult exactly the way ``dag`` does.

    Reproduces the 2026-07-29 state: a `RunCancelled` + a `RunResult` payload
    under `runresult:<run_id>:payload`, with NO committed layer. A second run of
    the same identity later died inside the kernel with
    `IdempotencyConflict: payload idempotency key ... reused with different
    content` — one RunResult per run id is a kernel-level, forever constraint.
    """
    from guanlan_v2.orchestration import dag as D
    from guanlan_v2.orchestration.runtime_contracts import RunResult

    rid = run_id or f"lane0-{SESSION}"
    plan = type("P", (), {"run_id": rid, "plan_digest": "b" * 64})
    runtime = type("R", (), {
        "runtime_registry_digest": env.bindings.runtime_registry_digest})
    result = RunResult(run_id=rid, plan_digest="b" * 64,
                       terminal_status=terminal_status,
                       settled_tokens=0, settled_llm_invocations=0)
    D._persist_run_result(env.stores, runtime, plan, result, terminal_status)


def test_a_prior_terminal_run_result_refuses_by_name_before_anything_burns():
    env = _seeded_env()
    with pytest.raises(L.Lane0Refused) as exc:
        env.run()
    assert exc.value.code == "partial_prior_run"
    assert f"lane0-{SESSION}" in str(exc.value)
    assert "RunCancelled" in str(exc.value)      # names the evidence it found
    assert env.gateway.invocations == []         # nothing was burned


def _seeded_env():
    env = Env()
    _seed_terminal_run_result(env)
    return env


def test_the_refusal_names_the_documented_supersede():
    env = _seeded_env()
    with pytest.raises(L.Lane0Refused) as exc:
        env.run()
    assert "--attempt 2" in str(exc.value)


def test_attempt_two_is_a_distinct_run_identity():
    assert L._lane0_identity(SESSION) == (
        f"lane0-{SESSION}", f"plan-lane0-{SESSION}", f"req-lane0-{SESSION}")
    assert L._lane0_identity(SESSION, attempt=2) == (
        f"lane0-{SESSION}-r2", f"plan-lane0-{SESSION}-r2", f"req-lane0-{SESSION}-r2")


def test_a_superseding_attempt_runs_on_a_spent_identity():
    # the honest escape hatch: the prior identity is spent (its RunResult key can
    # never be rewritten), so a fresh judgment needs a fresh identity — and a
    # fresh identity means a different candidate digest, i.e. a NEW human
    # approval. Nothing is forced, nothing is overwritten.
    env = _seeded_env()
    result = env.run(attempt=2)
    assert result.outcome == L.OUTCOME_COMPLETED, result.refusal_detail
    assert result.run_id == f"lane0-{SESSION}-r2"
    assert result.snapshot_id == f"bootstrap-ctx-lane0-{SESSION}-r2"
    assert result.snapshot_visible_to_deep_lane is True


def test_a_snapshot_from_any_attempt_of_the_session_is_reused():
    # one judgment per +08:00 session date stays the red line: --attempt may
    # never buy a second token burn on a day that already produced a snapshot.
    env = Env()
    first = env.run()
    second = env.run(attempt=2)
    assert second.outcome == L.OUTCOME_REUSED
    assert second.reused is True
    assert second.snapshot_digest == first.snapshot_digest
    assert len(env.gateway.invocations) == 2      # NOT re-burned


def test_an_escaping_idempotency_conflict_becomes_the_named_refusal(monkeypatch):
    # belt and braces: whatever else in the kernel refuses a spent run identity,
    # the CLI must never show a raw IdempotencyConflict traceback.
    from guanlan_v2.orchestration.eventstore import IdempotencyConflict

    env = Env()

    def _boom(*_a, **_kw):
        raise IdempotencyConflict(
            "payload idempotency key 'runresult:lane0-2026-07-29:payload' "
            "reused with different content")

    monkeypatch.setattr(L, "build_dag_plan_executor", lambda **_kw: _boom)
    with pytest.raises(L.Lane0Refused) as exc:
        env.run()
    assert exc.value.code == "partial_prior_run"
    assert "--attempt 2" in str(exc.value)


def test_the_cli_takes_an_attempt_flag():
    args = L.build_arg_parser().parse_args(["run", "--attempt", "3"])
    assert args.attempt == 3
    assert L.build_arg_parser().parse_args(["run"]).attempt == 1


# =========================================================================== #
# 4b — a REAL model's raw JSON completes the run (D2/D3, 2026-07-29)            #
# =========================================================================== #
def test_a_raw_json_completion_from_a_real_model_completes_the_run():
    # The live run's two LLM seats both came back `output_schema_invalid`: the
    # strict DigestModel could not accept JSON at all, and three fields
    # (as_of / factor_report_digest / content_digest) are unproducible by any
    # model. With the runtime owning those three and decoding JSON in strict
    # JSON mode, the same raw-JSON answer — envelope and all — now seals.
    env = Env(raw_json_gateway=True)
    result = env.run()

    assert result.outcome == L.OUTCOME_COMPLETED, result.refusal_detail
    assert result.node_statuses["lane0.regime"] == "completed"
    assert result.node_statuses["lane0.rotation"] == "completed"
    assert result.llm_invocations == 2
    assert result.snapshot_visible_to_deep_lane is True


def test_the_runtime_stamps_the_digest_of_the_report_the_run_committed():
    from guanlan_v2.orchestration.market.factors import (
        MARKET_FACTOR_REPORT_SCHEMA_REF,
        REGIME_REPORT_SCHEMA_REF,
    )

    env = Env(raw_json_gateway=True)
    env.run()
    stored = dict(env.stores._shared.backend.payloads).values()
    factor = next(s.model for s in stored
                  if getattr(s, "schema_key", None) == MARKET_FACTOR_REPORT_SCHEMA_REF.key)
    regime = next(s.model for s in stored
                  if getattr(s, "schema_key", None) == REGIME_REPORT_SCHEMA_REF.key)
    # the anchor the deep lane audits: the read is bound to the exact report.
    assert regime.factor_report_digest == factor.content_digest
    assert regime.as_of == factor.as_of
    assert regime.content_digest == regime.semantic_digest()


# =========================================================================== #
# 4c — 裁决 1 + 2: the production run's prompt carries the data and the schema  #
# =========================================================================== #
def test_the_production_run_puts_the_rendered_numbers_in_the_model_request():
    """The 2026-07-29 hole, pinned at the driver: the seat that answered
    "no factor report" now receives the rendered block in its authorized bytes."""
    import json as _json

    from guanlan_v2.orchestration import bootstrap as _B
    from guanlan_v2.orchestration import worker as _W
    from guanlan_v2.orchestration.market.factors import (
        MARKET_FACTOR_REPORT_SCHEMA_REF,
        render_factor_report_for_prompt,
    )

    env = Env(raw_json_gateway=True)
    env.run()
    stored = list(dict(env.stores._shared.backend.payloads).values())
    factor = next(s.model for s in stored
                  if getattr(s, "schema_key", None) == MARKET_FACTOR_REPORT_SCHEMA_REF.key)
    records = [s.model for s in stored
               if getattr(s, "schema_key", None) == "PromptAssemblyRecord@1"]
    assert {r.node_id for r in records} == {"lane0.regime", "lane0.rotation"}
    # the driver injects the Lane-0 assembler, not the static one.
    assert {r.assembler_id for r in records} == {_B.LANE0_ASSEMBLER_ID}

    rendered = render_factor_report_for_prompt(factor)
    for node_id, blob in env.gateway.canonical_requests.items():
        channel = _json.loads(blob)
        section = channel[_B.LANE0_FACTOR_REPORT_SECTION]
        assert section["text"] == rendered, node_id
        assert section["factor_report_digest"] == factor.content_digest
        assert f"battery_digest={factor.battery_digest[:8]}" in blob
        out = channel[_W.OUTPUT_SCHEMA_SECTION]
        assert out["runtime_supplied_fields"] == sorted(_B.LANE0_RUNTIME_OWNED_FIELDS)
        assert "narrative" in set(out["required_fields"]) | set(out["optional_fields"])


# =========================================================================== #
# 4d — 裁决 3 · Option B through the PRODUCTION normalizer                      #
# =========================================================================== #
def test_an_all_unavailable_report_lets_the_seat_answer_without_an_anchor():
    """The dark-battery case, end to end: real digest, zero citations, runtime reason."""
    from guanlan_v2.orchestration.market.factors import (
        MARKET_FACTOR_REPORT_SCHEMA_REF,
        NO_CITABLE_READING_REASON,
        NO_FACTOR_REPORT_DIGEST,
        REGIME_REPORT_SCHEMA_REF,
    )

    env = Env(raw_json_gateway=True)
    env.gateway.empty_evidence = True
    result = env.run(inputs=empty_inputs())          # every factor UNAVAILABLE

    assert result.node_statuses["lane0.regime"] in ("completed", "degraded")
    stored = list(dict(env.stores._shared.backend.payloads).values())
    factor = next(s.model for s in stored
                  if getattr(s, "schema_key", None) == MARKET_FACTOR_REPORT_SCHEMA_REF.key)
    regime = next(s.model for s in stored
                  if getattr(s, "schema_key", None) == REGIME_REPORT_SCHEMA_REF.key)
    assert factor.feature_vector == {}               # nothing citable existed
    # the audit anchor the deep lane depends on is INTACT (never the no-report marker)…
    assert regime.factor_report_digest == factor.content_digest
    assert regime.factor_report_digest != NO_FACTOR_REPORT_DIGEST
    # …the read cites nothing, and the reason is the runtime's measurement, not
    # the model's "I decided not to cite anything".
    assert regime.evidence == () and regime.evidence_factor_ids == ()
    assert regime.unknown_reason == NO_CITABLE_READING_REASON


def test_a_citable_report_still_refuses_a_seat_that_cites_nothing():
    """The ④ ≥1 rule, bit-for-bit, on the production path."""
    env = Env(raw_json_gateway=True)
    env.gateway.empty_evidence = True
    result = env.run()                               # happy inputs ⇒ citable readings
    assert result.node_statuses["lane0.regime"] == "incomplete"
    assert "regime_missing" in result.degradation_badges


# =========================================================================== #
# 5 — honest degradation: never fabricate a number                             #
# =========================================================================== #
def test_all_unavailable_inputs_report_degraded_with_badges():
    env = Env()
    result = env.run(inputs=empty_inputs())
    assert result.outcome == L.OUTCOME_DEGRADED
    assert result.node_statuses["lane0.factor"] == "degraded"
    assert "factor_degraded" in result.degradation_badges
    # the snapshot still commits and is still what the deep lane will read.
    pair = _latest_snapshot_production(env.stores)
    assert pair is not None and pair[0].content_digest == result.snapshot_digest


def test_input_availability_is_reported_series_by_series():
    absent = L.describe_input_availability(empty_inputs())
    assert absent.present == ()
    assert "limit_up_total" in absent.absent
    assert absent.degraded is True

    present = L.describe_input_availability(happy_inputs())
    assert "limit_up_total" in present.present
    assert present.degraded is False


def test_the_driver_fabricates_no_input_when_the_loader_returns_nothing(monkeypatch):
    import guanlan_v2.orchestration.market.factors as F

    monkeypatch.setattr(F, "load_market_factor_inputs",
                        lambda **_kw: MarketFactorInputs())
    loaded = L.load_lane0_inputs(as_of=AS_OF, provider_uri="G:/nope", archive_dir=None)
    assert loaded == MarketFactorInputs()
    for field in type(loaded).model_fields:
        assert getattr(loaded, field) is None


# =========================================================================== #
# 6 — dry run                                                                  #
# =========================================================================== #
def test_dry_run_computes_the_candidate_and_persists_nothing():
    env = Env()
    before_payloads = len(dict(env.stores._shared.backend.payloads))
    before_events = len(list(env.stores._shared.backend.events))
    result = env.run(dry_run=True)
    assert result.outcome == L.OUTCOME_DRY_RUN
    assert len(result.candidate_plan_digest) == 64
    assert result.llm_invocations == 0
    assert env.gateway.invocations == []
    assert len(dict(env.stores._shared.backend.payloads)) == before_payloads
    assert len(list(env.stores._shared.backend.events)) == before_events


def test_dry_run_candidate_digest_equals_the_real_run_candidate():
    env = Env()
    dry = env.run(dry_run=True)
    real = env.run()
    assert real.candidate_plan_digest == dry.candidate_plan_digest


def test_the_candidate_digest_is_stable_across_wall_clock_invocations():
    # REGRESSION (found on the real box): the draft carries ``as_of`` and the
    # candidate digest hashes the draft, so a wall-clock ``as_of`` gave every
    # invocation its own digest — propose → approve → run could never agree, and
    # the second reservation died on IdempotencyConflict. The plan must stamp the
    # SESSION, not the instant.
    env = Env()
    first = L.propose_lane0_candidate(as_of=AS_OF, bindings=env.bindings, register=False)
    later = L.propose_lane0_candidate(
        as_of=AS_OF + timedelta(hours=3), bindings=env.bindings, register=False)
    assert later.session_date == first.session_date
    assert later.candidate_plan_digest == first.candidate_plan_digest
    assert later.draft.as_of == first.draft.as_of
    # …and it really is the +08:00 whole-day stamp, the deep lane's own convention.
    assert first.draft.as_of == L._session_as_of(SESSION)


def test_the_pit_window_keeps_the_real_wall_clock():
    # the other half: the plan is rounded to the session, the MEASUREMENT is not.
    seen: dict = {}
    env = Env()
    real_loader = L.load_lane0_inputs

    def _spy(*, as_of, provider_uri, archive_dir):
        seen["as_of"] = as_of
        return MarketFactorInputs()

    L.load_lane0_inputs = _spy
    try:
        L.run_lane0_bootstrap(
            authorization=AlwaysGrantedAuthorization(), as_of=AS_OF,
            bindings=env.bindings)
    finally:
        L.load_lane0_inputs = real_loader
    assert seen["as_of"] == AS_OF
    assert seen["as_of"] != L._session_as_of(SESSION)


# =========================================================================== #
# 7 — a failed LLM node still lands a badged snapshot                          #
# =========================================================================== #
def test_regime_failure_badges_missing_and_still_commits_a_snapshot():
    env = Env(fail_nodes=("lane0.regime",))
    result = env.run()
    assert result.outcome == L.OUTCOME_DEGRADED
    assert "regime_missing" in result.degradation_badges
    assert result.snapshot_ref is not None
    pair = _latest_snapshot_production(env.stores)
    assert pair is not None and pair[0].content_digest == result.snapshot_digest


# =========================================================================== #
# 8 — red lines                                                                #
# =========================================================================== #
def test_no_order_or_signal_surface_is_reachable_from_the_driver():
    from pathlib import Path

    src = Path(L.__file__).read_text(encoding="utf-8").lower()
    for banned in ("orderbus", "signalbus", "order_bus", "signal_bus",
                   "place_order", "submit_order", "broker", "workflow.executor"):
        assert banned not in src, f"lane0_driver.py references a banned symbol: {banned}"
    assert "import" in src  # sanity: the file was actually read


def test_the_driver_mints_no_plan_approval_of_its_own():
    import re
    from pathlib import Path

    src = Path(L.__file__).read_text(encoding="utf-8")
    # \b keeps `PendingPlanApproval(` (the reviewer card, which carries no
    # authority) out of the sweep while catching a real PlanApproval construction.
    assert re.search(r"\bPlanApproval\(", src) is None, (
        "the driver must never construct a PlanApproval: authority is LOADED from "
        "the reviewed channel, never minted here")


def test_the_lane0_pending_card_can_never_be_lease_admitted():
    # Lane 0 has no sealed PlanPresetRecord, so its card carries no preset
    # provenance — which is exactly what makes `_find_admissible_lease` return
    # None for it. Lane 0 is approvable by a NAMED HUMAN only.
    env = Env()
    proposal = L.propose_lane0_candidate(
        as_of=AS_OF, bindings=env.bindings, register=False)
    assert proposal.pending_card.preset_id is None
    assert proposal.pending_card.preset_record_digest is None


# =========================================================================== #
# 9 — the reviewed pieces are REUSED, not re-implemented                       #
# =========================================================================== #
def test_the_driver_reuses_the_reviewed_bootstrap_surfaces():
    from pathlib import Path

    src = Path(L.__file__).read_text(encoding="utf-8")
    for reused in ("build_bootstrap_plan", "build_bootstrap_plan_draft",
                   "build_bootstrap_run_context", "register_bootstrap_runtime_factories",
                   "build_context_snapshot_from_bootstrap", "build_dag_plan_executor",
                   "production_gateway_factory", "load_market_factor_inputs"):
        assert reused in src, f"the driver must reuse the reviewed {reused}"


def test_the_bootstrap_profile_is_the_reviewed_one():
    assert L.LANE0_RUNTIME_PROFILE.profile_id == "bootstrap-runtime"
    assert (L.LANE0_RUNTIME_PROFILE.profile_digest
            == B.BOOTSTRAP_RUNTIME_PROFILE.profile_digest)


# =========================================================================== #
# 10 — the CLI                                                                 #
# =========================================================================== #
def test_cli_renders_every_step_honestly():
    env = Env()
    result = env.run()
    text = L.render_run_report(result)
    for expected in ("session", "stores", "authorization", "inputs", "admission",
                     "lane0.factor", "lane0.regime", "lane0.rotation", "snapshot",
                     "llm_invocations"):
        assert expected in text, f"the CLI report omits {expected!r}"
    assert result.snapshot_digest[:12] in text


def test_cli_parses_the_four_verbs():
    for argv in (["propose"], ["approve", "--digest", "a" * 64],
                 ["run"], ["status"]):
        parsed = L.build_arg_parser().parse_args(argv)
        assert parsed.verb == argv[0]


def test_cli_answers_a_verifier_refusal_with_one_honest_line(monkeypatch, capsys):
    # measured on the real box: an undeclared actor made the CLI print a traceback,
    # which reads like a broken tool rather than a fail-closed answer.
    from guanlan_v2.orchestration.adapters.identity import OperatorNotAllowed

    env = Env()
    monkeypatch.setattr(L, "build_production_lane0_bindings", lambda **_kw: env.bindings)

    def _boom(**_kw):
        raise OperatorNotAllowed("actor 'mallory' is not a declared operator")

    monkeypatch.setattr(L, "approve_lane0_candidate", _boom)
    code = L.main(["approve", "--digest", "a" * 64, "--actor", "mallory"])
    assert code == 5
    err = capsys.readouterr().err
    assert "REFUSED (operator)" in err
    assert "Traceback" not in err
