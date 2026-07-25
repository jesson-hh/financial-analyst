# -*- coding: utf-8 -*-
"""Phase 9 · Task 10 — adapters thin router + seats-compatible run/decision persistence.

The HTTP承载面 of the Phase-9 adapters (six additive ``/orchestration/*`` routes, all
JSON, shadow/draft only) plus ``persist_replay_run_compat`` — the seats-shaped
append that lets the EXISTING RunPicker / 复盘 UI list and replay an orchestrated run
with **zero frontend changes** (UI 只填充不重建).

Plus the three chartered carries:

* **C-A** — the PRODUCTION :class:`ReplayPlanCoordinator` adapter (Task 4 shipped the
  driver behind that port with fakes): REQUIRED approval on every admitted plan, the
  per-point Bootstrap node reservation as a CHILD of the ONE interval plan reservation,
  and the SAME ``feed_floors`` tuple flowing into BOTH ``build_replay_manifest`` and
  ``derive_replay_feasible_window`` (macro never via ``read_archive``).
* **C-B** — the seats live-session settlement path (Task 4 left ``is_live_session``
  hardcoded ``False`` and the ``seats_state`` load dead).
* **C-C** — the server-side half of the Phase-7 production wiring (durable
  ``PlanApprovalCoordinator.replay()`` + the ``approvals_sink`` bridge).

CORRECTION-CLAUSE NOTE (brief wording → reviewed source; source wins — full prose in
``.superpowers/sdd/p9-task-10-report.md``): the Task-6 maturity surface is TOKEN-FREE
(``mature_shadow_replay`` / ``ReplayStateStore`` / ``derive_replay_maturity_key`` /
``ReplayMaturityKeyUnknown``), never the brief's ``wakeup_shadow_replay``.

Run: ``python -m pytest tests/orchestration/test_adapters_api.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.orchestration.adapters import api as adapters_api
from guanlan_v2.orchestration.adapters.api import (
    AdaptersRouterDeps,
    ORCHESTRATION_ROUTE_PATHS,
    ProductionReplayPlanCoordinator,
    ReplayCoordinatorApprovalRefused,
    build_adapters_router,
    build_plan_approval_coordinator,
    build_replay_feed_floors,
    derive_replay_experiment_id,
    derive_replay_plan_candidate_digest,
    derive_replay_run_id,
    persist_replay_run_compat,
    plan_approval_console_kwargs,
)
from guanlan_v2.orchestration.adapters.contracts import ShadowReplayRunState
from guanlan_v2.orchestration.adapters.luozi import (
    ReplayIntentLedger,
    ReplayRuntimeBindings,
    run_interval_replay,
)
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    ExperimentStatus,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.seats import api as seats_api

# real, reviewed harnesses reused from the Task-4/5/6 suites (the established
# cross-suite fixture-reuse precedent in tests/orchestration).
from tests.orchestration.test_luozi_replay import (
    _FakeCoordinator,
    _FixedClock,
    _bindings,
    _calendar,
    _exec_config,
    _registry,
    _request,
    _schedule,
    _schedule_ref,
    _three_point_env,
    _utc,
)
from tests.orchestration.test_luozi_wakeup import (
    _env as _wakeup_env,
    _report as _dual_curve_report,
    _seed_parked,
)

UTC = timezone.utc
TZ = "Asia/Shanghai"


# =========================================================================== #
# helpers                                                                      #
# =========================================================================== #
def _client(deps: AdaptersRouterDeps | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_adapters_router(deps))
    return TestClient(app)


def _seats_client() -> TestClient:
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _tmp_seats(monkeypatch, tmp_path) -> None:
    """Point the two append-only seats logs at tmp (never the real ``var/``)."""
    monkeypatch.setattr(seats_api, "_RUNS_LOG", tmp_path / "seats_runs.jsonl")
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "seats_decisions.jsonl")


class _SpyStore:
    """A read-recording wrapper over a real :class:`ReplayStateStore`.

    Every mutating method of the real store is shadowed by a recorder, so a GET
    endpoint that writes anything is caught (invariant 4 / ``test_no_mutating_reads``).
    """

    def __init__(self, inner):
        self._inner = inner
        self.reads: list[str] = []
        self.writes: list[str] = []

    # -- reads (delegated, recorded) ---------------------------------------- #
    def load_head(self, experiment_id):
        self.reads.append("load_head")
        return self._inner.load_head(experiment_id)

    def load_head_by_maturity_key(self, key):
        self.reads.append("load_head_by_maturity_key")
        return self._inner.load_head_by_maturity_key(key)

    def load_operation_receipt(self, key):
        self.reads.append("load_operation_receipt")
        return self._inner.load_operation_receipt(key)

    def resolve_report(self, ref):
        self.reads.append("resolve_report")
        return self._inner.resolve_report(ref)

    def waiting_states(self):
        self.reads.append("waiting_states")
        return self._inner.waiting_states()

    @property
    def payload_store(self):
        return self._inner.payload_store

    # -- writes (recorded, then delegated) ---------------------------------- #
    def persist(self, *a, **kw):
        self.writes.append("persist")
        return self._inner.persist(*a, **kw)

    def commit_maturity(self, *a, **kw):
        self.writes.append("commit_maturity")
        return self._inner.commit_maturity(*a, **kw)

    def _head_ref(self, experiment_id):
        return self._inner._head_ref(experiment_id)


def _replay_deps(store=None, bindings=None, schedule=None, clock=None,
                 **kw) -> AdaptersRouterDeps:
    sch = schedule if schedule is not None else _schedule()
    return AdaptersRouterDeps(
        schedule_registry=_registry(sch),
        replay_state_store=store,
        replay_bindings=bindings,
        clock=clock if clock is not None else SystemClock(),
        replay_requests={},
        weiwo_requests={},
        weiwo_receipts={},
        **kw,
    )


def _start_body(**over) -> dict:
    body = {
        "code": "SH600519",
        "schedule_id": "sched-replay",
        "schedule_version": "1",
        "start_date": "2026-07-06",
        "end_date": "2026-07-08",
        "strategy_id": "seat-a",
    }
    body.update(over)
    return body


# =========================================================================== #
# Row 1 — router registration is purely additive                                #
# =========================================================================== #
def test_route_table_additive():
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    before = [(r.path, tuple(sorted(r.methods))) for r in app.routes]

    app.include_router(build_adapters_router(_replay_deps()))
    after = [(r.path, tuple(sorted(r.methods))) for r in app.routes]

    assert after[: len(before)] == before, "an existing route-table entry changed"
    added = after[len(before):]
    assert [p for p, _ in added] == list(ORCHESTRATION_ROUTE_PATHS)
    assert len(added) == 6
    assert set(ORCHESTRATION_ROUTE_PATHS) == {
        "/orchestration/replay/start",
        "/orchestration/replay/state",
        "/orchestration/replay/curves",
        "/orchestration/replay/wakeup",
        "/orchestration/weiwo/start",
        "/orchestration/weiwo/state",
    }


# =========================================================================== #
# Row 2 — start awaits approval; zero reservations, zero runs                   #
# =========================================================================== #
def test_start_awaits_approval():
    sch, cal, _, _ = _three_point_env()
    coord = _FakeCoordinator()
    binds, sink = _bindings(sch, cal, coordinator=coord)
    deps = _replay_deps(bindings=binds, schedule=sch)
    r = _client(deps).post("/orchestration/replay/start", json=_start_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "awaiting_approval"
    assert body["approval_policy"] == ApprovalPolicy.REQUIRED.value
    assert len(body["candidate_plan_digest"]) == 64
    assert body["experiment_id"] == derive_replay_experiment_id(
        request_id=body["request_id"], schedule_id="sched-replay")

    # admission spy: the per-point coordinator was never touched, and the REAL
    # BudgetLedger recorded ZERO reservations (invariant 5).
    assert coord.bootstrap_calls == [] and coord.llm_calls == [] and coord.det_calls == []
    assert binds.budget.replay().reservations == {}
    assert sink.budget_events() == ()
    assert len(binds.intent_ledger) == 0


def test_start_never_self_approves_auto_is_impossible():
    """The route mints its own REQUIRED request; an AUTO policy can never be asked for."""
    deps = _replay_deps()
    r = _client(deps).post(
        "/orchestration/replay/start",
        json=_start_body(approval_policy="auto"),  # a hostile body field is ignored
    )
    assert r.status_code == 200, r.text
    assert r.json()["approval_policy"] == "required"
    recorded = deps.replay_requests[r.json()["request_id"]]
    assert recorded["request"].approval_policy is ApprovalPolicy.REQUIRED


# =========================================================================== #
# Row 3 — unknown schedule ⇒ 422                                                #
# =========================================================================== #
@pytest.mark.parametrize("over", [
    {"schedule_id": "no-such-schedule"},
    {"schedule_version": "9"},
])
def test_start_unknown_schedule_422(over):
    r = _client(_replay_deps()).post("/orchestration/replay/start", json=_start_body(**over))
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "unknown_schedule"


@pytest.mark.parametrize("over,reason", [
    ({"code": ""}, "missing_code"),
    ({"start_date": "20260706"}, "bad_date"),
    ({"start_date": "2026-07-09"}, "bad_interval"),
])
def test_start_body_validation_422(over, reason):
    r = _client(_replay_deps()).post("/orchestration/replay/start", json=_start_body(**over))
    assert r.status_code == 422
    assert r.json() == {"ok": False, "reason": reason}


# =========================================================================== #
# Row 4 — unknown experiment ⇒ 404 typed reason                                 #
# =========================================================================== #
def test_state_unknown_404():
    env = _wakeup_env()
    deps = _replay_deps(store=env.store)
    r = _client(deps).get("/orchestration/replay/state", params={"experiment_id": "nope"})
    assert r.status_code == 404
    assert r.json() == {"ok": False, "reason": "unknown_experiment"}


def test_state_projects_the_parked_head():
    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    deps = _replay_deps(store=env.store)
    r = _client(deps).get(
        "/orchestration/replay/state", params={"experiment_id": parked.experiment_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    st = body["state"]
    assert st["experiment_id"] == parked.experiment_id
    assert st["run_id"] == parked.run_id
    assert st["status"] == ExperimentStatus.WAITING_FOR_MATURITY.value
    assert st["completed_points"] == parked.completed_points
    assert st["total_points"] == parked.total_points
    assert st["wakeup_key"] == parked.wakeup_key
    assert st["resume_after"] == parked.resume_after.isoformat()
    assert "source:orchestrated" in body["badges"]
    assert "waiting_for_maturity" in body["badges"]


# =========================================================================== #
# Row 5 — pre-maturity curves are honest, never fabricated                       #
# =========================================================================== #
def test_curves_premature_honest():
    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    deps = _replay_deps(store=env.store)
    r = _client(deps).get(
        "/orchestration/replay/curves", params={"experiment_id": parked.experiment_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["report"] is None                       # never a fabricated curve
    assert body["status"] == "waiting_for_maturity"
    assert body["resume_after"] == parked.resume_after.isoformat()
    # no curve payload leaked anywhere in the honest-degradation response
    assert "points" not in json.dumps(body)


def test_curves_completed_projects_the_real_report():
    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    completed = ShadowReplayRunState(
        experiment_id=parked.experiment_id, run_id=parked.run_id,
        request_id=parked.request_id, schedule_digest=parked.schedule_digest,
        execution_config_digest=parked.execution_config_digest,
        status=ExperimentStatus.COMPLETED,
        completed_points=parked.total_points, total_points=parked.total_points,
        curve_report_ref=parked.curve_report_ref,
        updated_at=parked.updated_at + timedelta(days=1),
    )
    env.store.persist(completed, idempotency_key="completed-1")

    r = _client(_replay_deps(store=env.store)).get(
        "/orchestration/replay/curves", params={"experiment_id": parked.experiment_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["status"] == "completed"
    rep = body["report"]
    assert rep["not_causal_attribution"] is True
    assert rep["execution_config_digest"] == report.execution_config.semantic_digest()
    assert len(rep["llm_shadow"]["points"]) == len(report.llm_shadow.points)
    assert len(rep["deterministic"]["points"]) == len(report.deterministic.points)
    assert rep["deterministic"]["applied_intent_digests"] == []   # envelope-free lane


# =========================================================================== #
# Row 6 — wakeup is idempotent through HTTP                                     #
# =========================================================================== #
def test_wakeup_idempotent_via_http():
    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    from tests.orchestration.test_dual_curves import _FakePool
    binds = ReplayRuntimeBindings(
        admission=_FakeCoordinator(), budget=SimpleNamespace(), run_budget=SimpleNamespace(),
        schedule_registry=None, calendar=None, clock_factory=lambda p: SystemClock(),
        pool=_FakePool(), trial_ledger=_SpyLedger(), replay_state_store=env.store,
    )
    mature = report.interval_end + timedelta(days=1)
    deps = _replay_deps(store=env.store, bindings=binds, clock=_FixedClock(mature))
    cli = _client(deps)
    a = cli.post("/orchestration/replay/wakeup", json={"wakeup_key": parked.wakeup_key})
    b = cli.post("/orchestration/replay/wakeup", json={"wakeup_key": parked.wakeup_key})
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.json() == b.json()                            # byte-identical receipts
    assert a.json()["receipt"]["outcome"] == "completed"


def test_wakeup_unknown_key_404():
    env = _wakeup_env()
    binds = ReplayRuntimeBindings(
        admission=_FakeCoordinator(), budget=SimpleNamespace(), run_budget=SimpleNamespace(),
        schedule_registry=None, calendar=None, clock_factory=lambda p: SystemClock(),
        replay_state_store=env.store,
    )
    r = _client(_replay_deps(store=env.store, bindings=binds)).post(
        "/orchestration/replay/wakeup", json={"wakeup_key": "d" * 64})
    assert r.status_code == 404
    assert r.json() == {"ok": False, "reason": "unknown_wakeup_key"}


class _SpyLedger:
    def __init__(self):
        self.touched: list = []

    def register_holdout_window(self, *a, **kw):
        self.touched.append((a, kw))
        return None


# =========================================================================== #
# Row 11 — GET endpoints write NOTHING                                           #
# =========================================================================== #
def test_no_mutating_reads():
    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    spy = _SpyStore(env.store)
    cli = _client(_replay_deps(store=spy))
    spy.writes.clear()
    for path in ("/orchestration/replay/state", "/orchestration/replay/curves"):
        assert cli.get(path, params={"experiment_id": parked.experiment_id}).status_code == 200
        assert cli.get(path, params={"experiment_id": "nope"}).status_code == 404
    assert cli.get("/orchestration/weiwo/state", params={"run_id": "nope"}).status_code == 404
    assert spy.writes == [], f"a GET endpoint wrote: {spy.writes}"
    assert spy.reads, "the GET endpoints did read the store"


# =========================================================================== #
# weiwo — symmetric, draft-only                                                 #
# =========================================================================== #
def test_weiwo_start_awaits_approval_and_never_runs(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        adapters_api, "_run_weiwo_research",
        lambda *a, **kw: calls.append(1))
    deps = _replay_deps()
    r = _client(deps).post("/orchestration/weiwo/start", json={"goal": "找一个新因子"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["status"] == "awaiting_approval"
    assert body["run_id"].endswith(body["request_id"])
    assert calls == []                                   # start NEVER executes


def test_weiwo_start_requires_a_goal():
    r = _client(_replay_deps()).post("/orchestration/weiwo/start", json={"goal": "  "})
    assert r.status_code == 422
    assert r.json()["reason"] == "missing_goal"


def test_weiwo_state_projects_the_receipt():
    from guanlan_v2.orchestration.adapters.weiwo import WeiwoRunReceipt
    receipt = WeiwoRunReceipt(
        run_id="weiwo-run.abc", context_snapshot_digest="a" * 64,
        draft_ids=("factor_1", "factor_2"), stop_reason="completed")
    deps = _replay_deps()
    deps.weiwo_receipts["weiwo-run.abc"] = receipt
    r = _client(deps).get("/orchestration/weiwo/state", params={"run_id": "weiwo-run.abc"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ok": True, "run_id": "weiwo-run.abc", "status": "completed",
        "draft_ids": ["factor_1", "factor_2"], "stop_reason": "completed",
        "source": "orchestrated",
    }


def test_weiwo_state_awaiting_before_the_run():
    deps = _replay_deps()
    started = _client(deps).post(
        "/orchestration/weiwo/start", json={"goal": "g"}).json()
    r = _client(deps).get("/orchestration/weiwo/state", params={"run_id": started["run_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "awaiting_approval"
    assert r.json()["draft_ids"] == [] and r.json()["stop_reason"] is None


# =========================================================================== #
# unwired deps are an honest 503, never a fake empty success                     #
# =========================================================================== #
@pytest.mark.parametrize("method,path,payload", [
    ("get", "/orchestration/replay/state", {"experiment_id": "e"}),
    ("get", "/orchestration/replay/curves", {"experiment_id": "e"}),
])
def test_unwired_store_is_an_honest_503(method, path, payload):
    cli = _client(AdaptersRouterDeps())
    r = cli.get(path, params=payload)
    assert r.status_code == 503
    assert r.json()["ok"] is False and r.json()["reason"] == "replay_store_unwired"


def test_unwired_schedule_registry_is_an_honest_503():
    r = _client(AdaptersRouterDeps()).post(
        "/orchestration/replay/start", json=_start_body())
    assert r.status_code == 503
    assert r.json()["reason"] == "schedule_registry_unwired"


def test_server_mount_resolves_process_deps_lazily(monkeypatch):
    """The server mounts with no deps; Task 12 binds them with ONE provider call."""
    from guanlan_v2.orchestration.adapters.api import (
        process_adapters_router_deps, set_adapters_router_deps,
    )
    monkeypatch.setattr(adapters_api, "_PROCESS_ROUTER_DEPS", AdaptersRouterDeps())
    cli = _client(None)                                # exactly the server-side mount
    assert cli.get("/orchestration/replay/state",
                   params={"experiment_id": "e"}).json()["reason"] == "replay_store_unwired"

    env = _wakeup_env()
    report = _dual_curve_report()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    set_adapters_router_deps(_replay_deps(store=env.store))
    assert process_adapters_router_deps().replay_state_store is env.store
    r = cli.get("/orchestration/replay/state",
                params={"experiment_id": parked.experiment_id})
    assert r.status_code == 200 and r.json()["ok"] is True
    with pytest.raises(TypeError):
        set_adapters_router_deps(object())


# =========================================================================== #
# Row 7/8/9/10 — persist_replay_run_compat round-trips through the seats API      #
# =========================================================================== #
def _compat_state(run_id="replay-run.req-1", exp="replay.req-1.sched-replay"):
    return ShadowReplayRunState(
        experiment_id=exp, run_id=run_id, request_id="req-1",
        schedule_digest="a" * 64, execution_config_digest="b" * 64,
        status=ExperimentStatus.RUNNING, completed_points=3, total_points=3,
        updated_at=datetime(2026, 7, 8, 6, 0, tzinfo=UTC),
    )


def _compat_decisions():
    return (
        {"decision_as_of": _utc("2026-07-06", 14, 0), "direction": "买入",
         "confidence": 72, "rationale": "回放决策一", "key_evidence": ["ev-1"]},
        {"decision_as_of": _utc("2026-07-07", 14, 0), "direction": "观望",
         "confidence": None, "rationale": "回放决策二"},
        {"decision_as_of": _utc("2026-07-08", 14, 0), "direction": "卖出",
         "confidence": 55, "rationale": "回放决策三"},
    )


def _compat_head(run_id="replay-run.req-1"):
    return {
        "run_id": run_id, "code": "SH600519", "name": "贵州茅台",
        "strategy_id": "seat-a", "strategy_name": "回放席位", "tf": "D",
        "start_date": "2026-07-06", "end_date": "2026-07-08",
        "n_buy": 1, "n_sell": 1, "n_watch": 1, "n_err": 0,
        "model": "orchestration/dec.trader",
    }


def test_compat_run_head_roundtrip(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    persist_replay_run_compat(
        _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())

    body = _seats_client().get("/seats/runs", params={"code": "600519"}).json()
    assert body["ok"] is True and body["total"] == 1
    head = body["runs"][0]
    assert head["run_id"] == "replay-run.req-1"
    assert head["code"] == "SH600519"
    assert head["strategy_id"] == "seat-a"          # RunPicker filters on this
    assert head["tf"] == "D"
    assert (head["n_buy"], head["n_sell"], head["n_watch"]) == (1, 1, 1)
    assert head["model"] == "orchestration/dec.trader"
    assert head["source"] == "orchestrated"
    assert head["ts"]                                # the route's own convention


def test_compat_decisions_roundtrip(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    persist_replay_run_compat(
        _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())

    cli = _seats_client()
    body = cli.get("/seats/decisions", params={"run_id": "replay-run.req-1"}).json()
    assert body["ok"] is True and body["total"] == 3
    rows = list(reversed(body["decisions"]))          # endpoint is newest-first
    assert [r["asof"] for r in rows] == ["2026-07-06", "2026-07-07", "2026-07-08"]
    assert [r["direction"] for r in rows] == ["买入", "观望", "卖出"]
    for r in rows:
        assert r["source"] == "orchestrated"
        assert r["run_id"] == "replay-run.req-1"
        assert r["kind"] == "decide"                  # the existing history drawer kind
        assert "idx" not in r                         # NEVER a frontend-idx key
        assert r["model_name"] == "orchestration/dec.trader"

    # the run-scoped filter really partitions: exclude_runs hides every orchestrated row
    other = cli.get("/seats/decisions", params={"exclude_runs": 1}).json()
    assert other["total"] == 0


def test_compat_asof_is_the_store_timestamp_convention(monkeypatch, tmp_path):
    """30min runs key on ``YYYY-MM-DD HH:MM`` in the SESSION timezone, never UTC."""
    _tmp_seats(monkeypatch, tmp_path)
    head = _compat_head(run_id="replay-run.min") | {"tf": "30min"}
    decs = ({"decision_as_of": _utc("2026-07-06", 14, 0), "direction": "买入",
             "confidence": 60, "rationale": "日内", "timezone": TZ},)
    persist_replay_run_compat(
        _compat_state(run_id="replay-run.min"), decisions=decs, run_head=head)
    rows = _seats_client().get(
        "/seats/decisions", params={"run_id": "replay-run.min"}).json()["decisions"]
    assert rows[0]["asof"] == "2026-07-06 14:00"      # CST session time, not 06:00 UTC


def test_compat_append_only_idempotent(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    for _ in range(3):
        persist_replay_run_compat(
            _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())

    runs_lines = seats_api._RUNS_LOG.read_text(encoding="utf-8").strip().splitlines()
    dec_lines = seats_api._DEC_LOG.read_text(encoding="utf-8").strip().splitlines()
    assert len(runs_lines) == 1
    assert len(dec_lines) == 3
    body = _seats_client().get("/seats/runs", params={"code": "600519"}).json()
    assert body["total"] == 1


def test_compat_is_append_only_never_rewrites(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    seats_api._RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"run_id": "legacy-1", "code": "600519", "strategy_id": "seat-a",
              "tf": "D", "ts": "2026-07-01T09:00:00"}
    seats_api._RUNS_LOG.write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    persist_replay_run_compat(
        _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())
    lines = seats_api._RUNS_LOG.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0]) == legacy             # the pre-existing row is untouched
    assert len(lines) == 2


#: the exact key set a pre-Phase-9 ``/seats/decide`` row carries (grounded at
#: ``guanlan_v2/seats/api.py:931-953``; ``run_id``/``source``/``hold_*`` are the
#: optional keys "written only when non-empty").
_LEGACY_DECISION_KEYS = frozenset({
    "id", "ts", "kind", "code", "name", "strategy_id", "strategy_name", "mode", "freq",
    "direction", "confidence", "rationale", "key_evidence", "reasoning", "model_name",
    "asof", "factors_std", "recipe_factors", "recipe_factors_vintage", "card_names",
    "research", "research_excerpt_n", "narratives_surfaced", "regime_asof_used",
    "regime_asof", "audit_flags", "w", "llm_score", "factor_score", "hybrid_bias",
    "hybrid_direction", "creed", "pa", "pa_features", "run_id", "source",
    "hold_entry", "hold_bars",
})
#: the exact key set the existing frontend run-head registration writes
#: (``ui/seats/luozi-app.jsx:374``) + the route's own ``ts``.
_LEGACY_RUN_HEAD_KEYS = frozenset({
    "run_id", "code", "name", "strategy_id", "strategy_name", "tf", "start_date",
    "end_date", "n_buy", "n_sell", "n_watch", "n_err", "model", "ts", "source",
})


def test_legacy_shape_unchanged(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    persist_replay_run_compat(
        _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())

    for line in seats_api._DEC_LOG.read_text(encoding="utf-8").strip().splitlines():
        row = json.loads(line)
        extra = set(row) - _LEGACY_DECISION_KEYS
        assert not extra, f"orchestrated row invented keys: {sorted(extra)}"
        for key, value in row.items():
            if key in ("confidence",):
                continue                              # legacy always writes it (may be null)
            assert value not in ("", [], {}), f"{key} written empty (optional-key rule)"

    for line in seats_api._RUNS_LOG.read_text(encoding="utf-8").strip().splitlines():
        head = json.loads(line)
        assert not set(head) - _LEGACY_RUN_HEAD_KEYS


def test_compat_refuses_a_fabricated_row(monkeypatch, tmp_path):
    _tmp_seats(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        persist_replay_run_compat(
            _compat_state(),
            decisions=({"decision_as_of": _utc("2026-07-06", 14, 0), "direction": ""},),
            run_head=_compat_head())
    assert not seats_api._DEC_LOG.exists() or not seats_api._DEC_LOG.read_text(
        encoding="utf-8").strip()


def test_compat_never_self_calls_http(monkeypatch, tmp_path):
    """The compat append goes through the seats module IN-PROCESS (house red line)."""
    import ast

    import guanlan_v2.orchestration.adapters.api as mod
    banned = {"httpx", "requests", "aiohttp", "urllib", "socket", "starlette.testclient"}
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned, node.module

    _tmp_seats(monkeypatch, tmp_path)
    seen: list = []
    real = seats_api._persist_decision
    monkeypatch.setattr(
        seats_api, "_persist_decision",
        lambda kind, rec: (seen.append(kind), real(kind, rec))[1])
    persist_replay_run_compat(
        _compat_state(), decisions=_compat_decisions(), run_head=_compat_head())
    assert seen == ["decide", "decide", "decide"]      # the reviewed in-process helper


# =========================================================================== #
# C-A — the PRODUCTION ReplayPlanCoordinator                                     #
# =========================================================================== #
class _RecordingApproval:
    """The Phase-7 decision surface (``load_decision``) with a recorded verdict."""

    def __init__(self, decision=ApprovalDecision.APPROVED):
        self._decision = decision
        self.calls: list[tuple[str, str]] = []

    def load_decision(self, request_id, candidate_plan_digest):
        self.calls.append((request_id, candidate_plan_digest))
        if self._decision is None:
            return None
        return PlanApproval(
            request_id=request_id, candidate_plan_digest=candidate_plan_digest,
            decision=self._decision, actor_id="human:ops",
            decided_at=datetime(2026, 7, 5, 1, 0, tzinfo=UTC), reason="ok")


class _RecordingMemory:
    def __init__(self):
        self.calls: list = []

    def prepare_pit_replay(self, data_context, authority, *, prior_context_ref):
        self.calls.append((data_context.as_of, prior_context_ref))
        return SimpleNamespace(as_of=data_context.as_of, context_snapshot_digest="c" * 64)


class _RecordingRunner:
    def __init__(self, proposal_factory):
        self.lanes: list[str] = []
        self._proposal_factory = proposal_factory

    def __call__(self, *, lane, point, approval, **kw):
        self.lanes.append(f"{lane}#{point.point_ordinal}")
        if lane == "llm":
            return self._proposal_factory(f"art-{point.point_ordinal}")
        return SimpleNamespace(lane=lane)


def _coordinator_world():
    """A REAL Task-2 frozen PIT world (source registry + routing + config)."""
    from tests.orchestration.test_adapters_replay_data import (
        _STORE_META, _FakeReader, _ReplayWorld,
    )
    from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
    from guanlan_v2.orchestration.runtime_contracts import (
        PHASE2_BASE_REGISTRY_DIGEST,
        phase2_runtime_registry,
    )
    reg = build_phase3_registry(
        phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest)
    from tests.orchestration.test_adapters_replay_data import _NEWS_PARAMS
    world = _ReplayWorld(reg, method_id="news", params=_NEWS_PARAMS,
                         fake_reader=_FakeReader())
    return world, _STORE_META, reg


def _production_coordinator(*, world, store_meta, schema_registry, schedule,
                            execution_config, request, budget, feed_floors,
                            approval=None, memory=None, runner=None, universe=None):
    from tests.orchestration.test_luozi_replay import _proposal_artifact
    from guanlan_v2.orchestration.data.symbols import normalize_symbol
    return ProductionReplayPlanCoordinator(
        request=request, schedule=schedule, execution_config=execution_config,
        budget=budget,
        source_config=world.config, source_registry=world.snapshot, routing=world.routing,
        store_meta=store_meta, data_snapshot_id="pit-root-A",
        calendar_id="cn_a_share", timezone=TZ,
        routing_snapshot_digest=world.routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest,
        feed_floors=feed_floors,
        memory_service=memory or _RecordingMemory(),
        memory_authority=SimpleNamespace(memory_session_id="sess-1"),
        prior_context_ref=SimpleNamespace(schema_ref=None, payload_ref=None),
        approval=approval or _RecordingApproval(),
        plan_runner=runner or _RecordingRunner(_proposal_artifact),
        factor_scores=lambda point: {"600519": 0.4},
        universe=universe or (normalize_symbol("600519"),),
        provenance_digest="f" * 64,
    )


def _floors(macro_floor="2026-01-01", archive_floor="20260101"):
    from guanlan_v2.orchestration.adapters.replay_data import (
        archive_feed_floor_from_read, ArchiveFeedFloor,
    )
    floors = [
        archive_feed_floor_from_read(k, {"first_date": archive_floor}, archive_root="var/archive")
        for k in ("market_tape", "fundflow_concept", "fundflow_industry")
    ]
    floors.append(ArchiveFeedFloor(feed_id="macro", floor_date=macro_floor,
                                   archive_root="var/macro_pulse/snapshots.jsonl"))
    return tuple(floors)


def test_carry_a_every_admitted_plan_goes_through_required_approval():
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    cfg = _exec_config(sch)
    req = _request(sch)
    binds, _ = _bindings(sch, cal)
    approval = _RecordingApproval()
    from tests.orchestration.test_luozi_replay import _proposal_artifact
    runner = _RecordingRunner(_proposal_artifact)
    coord = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=cfg, request=req, budget=binds.budget, feed_floors=_floors(),
        approval=approval, runner=runner)
    binds = ReplayRuntimeBindings(
        admission=coord, budget=binds.budget, run_budget=binds.run_budget,
        schedule_registry=binds.schedule_registry, calendar=cal,
        clock_factory=binds.clock_factory, seats_budget_seam=binds.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    state = run_interval_replay(
        request=req, schedule=sch, execution_config=cfg,
        interval_start=start, interval_end=end, bindings=binds)
    assert state.completed_points == 3
    # every lane execution was preceded by an APPROVED decision lookup
    assert len(approval.calls) == 6                  # bootstrap + llm, per point
    assert runner.lanes == [
        "bootstrap#1", "llm#1", "bootstrap#2", "llm#2", "bootstrap#3", "llm#3"]


def test_carry_a_unapproved_plan_never_executes():
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    binds, _ = _bindings(sch, cal)
    from tests.orchestration.test_luozi_replay import _proposal_artifact
    runner = _RecordingRunner(_proposal_artifact)
    for verdict in (None, ApprovalDecision.REJECTED):
        coord = _production_coordinator(
            world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
            execution_config=_exec_config(sch), request=_request(sch),
            budget=binds.budget, feed_floors=_floors(),
            approval=_RecordingApproval(verdict), runner=runner)
        with pytest.raises(ReplayCoordinatorApprovalRefused):
            coord.bootstrap_context(_first_point(sch, cal, start, end))
        assert runner.lanes == []


def test_carry_a_auto_policy_is_refused_at_the_door():
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    binds, _ = _bindings(sch, cal)
    coord = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=_exec_config(sch),
        request=_request(sch, approval=ApprovalPolicy.AUTO),
        budget=binds.budget, feed_floors=_floors())
    with pytest.raises(ReplayCoordinatorApprovalRefused):
        coord.bootstrap_context(_first_point(sch, cal, start, end))


def _first_point(sch, cal, start, end):
    from guanlan_v2.orchestration.adapters.luozi import resolve_decision_points
    return resolve_decision_points(
        sch, calendar=cal, interval_start=start, interval_end=end)[0]


def _reserve_interval_plan(binds, req, sch, cfg):
    """Mint the interval plan reservation exactly as ``run_interval_replay`` does."""
    from guanlan_v2.orchestration.budget import BudgetRequest
    rb = binds.run_budget
    return binds.budget.reserve_plan(
        request_id=req.request_id,
        candidate_plan_digest=derive_replay_plan_candidate_digest(
            request_id=req.request_id, schedule_digest=sch.content_digest,
            execution_config_digest=cfg.semantic_digest()),
        budget_request=BudgetRequest(
            tokens=int(rb.max_tokens), llm_invocations=int(rb.max_llm_invocations),
            concurrency=int(rb.max_concurrency)),
        idempotency_key=f"replay-plan:replay-run.{req.request_id}",
    )


def test_carry_a_bootstrap_nodes_are_children_of_the_one_plan_reservation():
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    cfg, req = _exec_config(sch), _request(sch)
    base, _ = _bindings(sch, cal)
    coord = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=cfg, request=req, budget=base.budget, feed_floors=_floors())
    binds = ReplayRuntimeBindings(
        admission=coord, budget=base.budget, run_budget=base.run_budget,
        schedule_registry=base.schedule_registry, calendar=cal,
        clock_factory=base.clock_factory, seats_budget_seam=base.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    run_interval_replay(request=req, schedule=sch, execution_config=cfg,
                        interval_start=start, interval_end=end, bindings=binds)

    state = base.budget.replay()
    plans = [r for r in state.reservations.values() if r.scope_type == "plan"]
    assert len(plans) == 1, "the interval must hold exactly ONE plan reservation"
    plan_id = plans[0].reservation_id
    boots = [r for r in state.reservations.values()
             if r.scope_type == "node" and r.scope_id.startswith("bootstrap#")]
    assert len(boots) == 3
    for r in boots:
        assert state.parent_of[r.reservation_id] == plan_id
        assert r.reserved_llm_invocations == 0        # bootstrap lane is zero-LLM

    # the coordinator resolved the SAME plan reservation the driver minted (the digest
    # derivation is shared, not re-invented)
    derived = derive_replay_plan_candidate_digest(
        request_id=req.request_id, schedule_digest=sch.content_digest,
        execution_config_digest=cfg.semantic_digest())
    assert base.budget.get_active_plan(req.request_id, derived).reservation_id == plan_id


def test_carry_a_same_feed_floors_tuple_feeds_manifest_and_window(monkeypatch):
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    base, _ = _bindings(sch, cal)
    floors = _floors()
    seen: dict[str, list] = {"manifest": [], "window": []}

    import guanlan_v2.orchestration.adapters.api as mod
    real_manifest = mod.build_replay_manifest
    real_window = mod.derive_replay_feasible_window

    def _spy_manifest(**kw):
        seen["manifest"].append(kw["feed_floors"])
        return real_manifest(**kw)

    def _spy_window(**kw):
        seen["window"].append(kw["feed_floors"])
        return real_window(**kw)

    monkeypatch.setattr(mod, "build_replay_manifest", _spy_manifest)
    monkeypatch.setattr(mod, "derive_replay_feasible_window", _spy_window)

    cfg, req = _exec_config(sch), _request(sch)
    _reserve_interval_plan(base, req, sch, cfg)
    coord = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=cfg, request=req,
        budget=base.budget, feed_floors=floors)
    point = _first_point(sch, cal, start, end)
    snap = coord.bootstrap_context(point)

    assert len(seen["manifest"]) == 1 and len(seen["window"]) == 1
    assert seen["manifest"][0] is floors               # the SAME tuple object
    assert seen["window"][0] is floors
    assert snap.data_context.as_of == point.decision_as_of


def test_carry_a_pre_floor_point_is_honestly_degraded():
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    base, _ = _bindings(sch, cal)
    cfg, req = _exec_config(sch), _request(sch)
    _reserve_interval_plan(base, req, sch, cfg)
    coord = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=cfg, request=req,
        budget=base.budget, feed_floors=_floors(macro_floor="2026-12-01"))
    snap = coord.bootstrap_context(_first_point(sch, cal, start, end))
    assert any("macro" in b for b in snap.degradation_badges)
    assert snap.data_context is not None               # degraded, never a run failure


def test_carry_a_feed_floors_builder_never_reads_macro_from_the_archive(tmp_path):
    calls: list[str] = []

    def _spy_read(kind, *a, **kw):
        calls.append(kind)
        return {"kind": kind, "rows": [], "first_date": "20260105"}

    snaps = tmp_path / "snapshots.jsonl"
    snaps.write_text(
        json.dumps({"ts": "2026-02-03T15:00:00"}) + "\n"
        + json.dumps({"ts": "2026-01-20T15:00:00"}) + "\n", encoding="utf-8")

    floors = build_replay_feed_floors(
        read_archive=_spy_read, archive_root=str(tmp_path),
        macro_snapshots_path=snaps)

    from guanlan_v2.datafeed import snapshot_archive as sa
    assert calls == list(sa.KINDS)                     # the three KINDS, nothing else
    assert "macro" not in calls
    by_id = {f.feed_id: f for f in floors}
    assert set(by_id) == set(sa.KINDS) | {"macro"}
    assert by_id["macro"].floor_date == "2026-01-20"   # earliest snapshot, append-only
    assert by_id["market_tape"].floor_date == "2026-01-05"


# =========================================================================== #
# C-B — the seats live-session settlement path                                   #
# =========================================================================== #
class _SeatsSeamSpy:
    def __init__(self, counts=None, daily_budget=24):
        self.state = {"counts": dict(counts or {}), "daily_budget": daily_budget}
        self.noted: list[int] = []
        self.loads = 0

    def load_state(self):
        self.loads += 1
        return {"counts": dict(self.state["counts"]),
                "daily_budget": self.state["daily_budget"]}

    def note_external_llm_use(self, n, now=None):
        n = int(n)
        if n <= 0:
            return
        self.noted.append(n)
        day = (now or datetime.now()).date().isoformat()
        self.state["counts"][day] = int(self.state["counts"].get(day) or 0) + n


def test_carry_b_pit_replay_never_touches_the_seats_pool():
    sch, cal, start, end = _three_point_env()
    seam = _SeatsSeamSpy()
    binds, _ = _bindings(sch, cal, seats_seam=seam)
    run_interval_replay(request=_request(sch), schedule=sch,
                        execution_config=_exec_config(sch), interval_start=start,
                        interval_end=end, bindings=binds)
    assert seam.noted == []                            # the 24/day pool untouched


def test_carry_b_live_session_settles_once_per_reservation():
    sch, cal, start, end = _three_point_env()
    seam = _SeatsSeamSpy()
    binds, _ = _bindings(sch, cal, seats_seam=seam)
    run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
        is_live_session=lambda point: True)
    assert seam.noted == [1, 1, 1]                     # exactly once per settled point
    # the settled reservations are visible in the REAL ledger
    settled = [r for r in binds.budget.replay().reservations.values()
               if r.scope_id.startswith("llm.dec.trader#")]
    assert len(settled) == 3
    assert all(r.status == "settled" for r in settled)


def test_carry_b_live_session_respects_the_exhausted_daily_pool():
    sch, cal, start, end = _three_point_env()
    today = _utc("2026-07-06", 14, 0).astimezone(ZoneInfo(TZ)).date().isoformat()
    seam = _SeatsSeamSpy(counts={today: 24})           # the 24/day pool is exhausted
    binds, _ = _bindings(sch, cal, seats_seam=seam)
    run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
        is_live_session=lambda point: point.point_ordinal == 1)
    assert seam.noted == []                            # nothing admissible ⇒ nothing noted
    llm = [r for r in binds.budget.replay().reservations.values()
           if r.scope_id == "llm.dec.trader#1"]
    assert llm and llm[0].reserved_llm_invocations == 0


def test_carry_b_default_is_bit_unchanged():
    """No resolver ⇒ every point is PIT_REPLAY: reservations byte-identical to Task 4."""
    sch, cal, start, end = _three_point_env()
    a, _ = _bindings(sch, cal, seats_seam=_SeatsSeamSpy())
    run_interval_replay(request=_request(sch), schedule=sch,
                        execution_config=_exec_config(sch), interval_start=start,
                        interval_end=end, bindings=a)
    b, _ = _bindings(sch, cal, seats_seam=_SeatsSeamSpy())
    run_interval_replay(request=_request(sch), schedule=sch,
                        execution_config=_exec_config(sch), interval_start=start,
                        interval_end=end, bindings=b, is_live_session=None)
    def _shape(binds):
        return sorted(
            (r.scope_type, r.scope_id, r.reserved_llm_invocations, r.status)
            for r in binds.budget.replay().reservations.values())
    assert _shape(a) == _shape(b)
    assert all(s[3] == "reserved" for s in _shape(a))   # nothing settled by default


# =========================================================================== #
# C-C — the server-side half of the Phase-7 production wiring                    #
# =========================================================================== #
class _StubAdmission:
    """The admission store surface the approvals_sink deposits into."""

    def __init__(self):
        self.approvals: dict[tuple[str, str], PlanApproval] = {}
        self.recorded: list[tuple[str, str]] = []

    def record_approval(self, *, request_id, candidate_id, idempotency_key, **kw):
        self.recorded.append((request_id, candidate_id))
        return SimpleNamespace(event_id=f"ev-{len(self.recorded)}")


class _StubVerifier:
    def verify(self, material):
        return SimpleNamespace(actor_id="human:ops")


def test_carry_c_coordinator_is_built_through_the_durable_replay_path(tmp_path, monkeypatch):
    import guanlan_v2.orchestration.approval as approval_mod
    seen: list = []
    real_replay = approval_mod.PlanApprovalCoordinator.replay

    def _spy_replay(cls, journal_path, **kw):
        seen.append(kw)
        return real_replay.__func__(cls, journal_path, **kw)

    monkeypatch.setattr(
        approval_mod.PlanApprovalCoordinator, "replay", classmethod(_spy_replay))
    # the bare constructor must NOT be the path taken
    admission = _StubAdmission()
    coord = build_plan_approval_coordinator(
        journal_path=tmp_path / "plan_approvals.jsonl",
        admission=admission, approvals=admission.approvals,
        clock=SystemClock(), verifier=_StubVerifier())
    assert len(seen) == 1, "the coordinator must be rebuilt through replay()"
    assert seen[0]["approvals_sink"] is not None, "the approvals_sink must be bridged"
    assert coord is not None


def test_carry_c_approvals_sink_deposits_into_the_admission_store(tmp_path):
    admission = _StubAdmission()
    coord = build_plan_approval_coordinator(
        journal_path=tmp_path / "j.jsonl", admission=admission,
        approvals=admission.approvals, clock=SystemClock(), verifier=_StubVerifier())
    approval = PlanApproval(
        request_id="req-1", candidate_plan_digest="a" * 64,
        decision=ApprovalDecision.APPROVED, actor_id="human:ops",
        decided_at=datetime(2026, 7, 5, 1, 0, tzinfo=UTC))
    coord._approvals_sink(approval)
    assert admission.approvals[("req-1", "a" * 64)] is approval


def test_carry_c_console_kwargs_default_to_the_honest_unwired_shape():
    kwargs = plan_approval_console_kwargs(coordinator=None)
    assert kwargs == {}, "an unbound coordinator must leave console behaviour unchanged"
    sentinel = object()
    assert plan_approval_console_kwargs(coordinator=sentinel) == {
        "plan_approval_coordinator": sentinel}


def test_carry_c_server_mounts_the_adapters_router_and_binds_p7():
    """The server-side block exists, is additive and never touches the console module."""
    from pathlib import Path
    src = Path(adapters_api.__file__).resolve().parents[3] / "guanlan_v2" / "server.py"
    text = src.read_text(encoding="utf-8")
    assert "build_adapters_router" in text
    assert "plan_approval_console_kwargs" in text
    assert "bind_process_plan_approval_coordinator" in text
