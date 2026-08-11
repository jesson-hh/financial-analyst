# -*- coding: utf-8 -*-
"""L3 · the A-line screening EXECUTOR — the downstream driver the door names.

The gap this suite exists for was MEASURED on the live 9999 (2026-08-10):
``POST /orchestration/pipeline/start {source_kind:"v4", top_n:2}`` admitted two
lanes under a real screening ``ApprovalLease`` (journal row 58, 2 admissions /
16 LLM cap) and then executed neither — ``status`` stayed ``awaiting_approval``
and ``/state`` showed no runs. A consumed human authorization with zero
execution is the burned-lease shape ``live_decide``'s binding block records; the
A-line exit gate (L3 Task 7 Step 6) cannot close without a real screening run.

What is REAL here and what is scripted, stated up front:

* REAL: the sealed screening-lane preset, ``build_screening_batch``, the per-lane
  ``PlanAdmissionService``\\ s, the ``PlanApprovalCoordinator`` + a real durable
  ``ApprovalLease``, ``admit_screening_batch``, the freeze
  (``admit_after_approval``), the committed run journals, the recommendation
  assembler/renderer and the landing seam — plus the whole router path.
* SCRIPTED: the ``plan_runner_factory`` seam (:class:`_ScriptedRunner`). It does
  not fake the freeze away — it calls the REAL
  ``admission.verify_for_dispatch(digest)``, which only answers for a plan that
  was genuinely frozen and admitted, and it writes each lane's terminal history
  through the REAL unit of work (``_RunHistory``, borrowed from the screening
  suite). ``dag.run_plan`` itself is exercised over a screening lane by
  ``test_phase10_e2e``'s A-chain; duplicating it here would test the kernel, not
  this driver.

Run: ``python -m pytest tests/orchestration/test_pipeline_screening_driver.py -v``
"""
from __future__ import annotations

import dataclasses
import inspect
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.approval import PlanApprovalCoordinator
from guanlan_v2.orchestration.enums import PortfolioRating
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef
from guanlan_v2.orchestration.schemas import ResearchPlan

from guanlan_v2.orchestration.pipeline import screening as screening_mod
from guanlan_v2.orchestration.pipeline import screening_driver as driver_mod
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_plan_runner,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.pipeline.live_decide import SubjectPromptAssembler
from guanlan_v2.orchestration.pipeline.screening import (
    RECOMMENDATION_ADVISORY_BANNER,
    SCREENING_LANE_PRESET_ID,
    ScreeningError,
    screening_reserve_idempotency_key,
)
from guanlan_v2.orchestration.pipeline.screening_driver import (
    BATCH_AWAITING_APPROVAL,
    BATCH_COMPLETED,
    BATCH_FAILED,
    BATCH_PARTIAL,
    LANE_COMPLETED,
    LANE_FAILED,
    LANE_PENDING_HUMAN,
    SubjectBondRefused,
    execute_screening_batch,
    slate_projection,
)

# the router harness, verbatim (its ``env`` / ``trimmed_catalog`` fixtures cover
# the union worker set, which contains all nine screening workers).
from tests.orchestration.test_pipeline_api import (  # noqa: F401
    API,
    CODES,
    GOOD_CRED,
    NOW,
    _client,
    _make_env,
    _Verifier,
    env,
    trimmed_catalog,
)
from tests.orchestration.test_pipeline_screening import _RunHistory

_RATINGS = {0: PortfolioRating.BUY, 1: PortfolioRating.HOLD,
            2: PortfolioRating.BUY}
_RATIONALE = {
    0: "订单能见度延伸至明年一季度,产能爬坡兑现。",
    1: "估值中枢已修复,等待旺季验证。",
    2: "行业景气度触底回升,库存周期见拐点。",
}


# =========================================================================== #
# doubles                                                                      #
# =========================================================================== #
class _ScriptedRunner:
    """The injected ``plan_runner_factory``: real dispatch verification, scripted run.

    Per lane it (1) records every kwarg the driver handed the production factory
    seam, (2) calls the REAL ``verify_for_dispatch`` — which answers only for a
    genuinely frozen+admitted plan, so a driver that skipped the freeze reddens
    here rather than silently "running" — and (3) writes that lane's terminal
    ``ResearchPlan@1`` history through the real unit of work.
    """

    def __init__(self, history: _RunHistory, *, fail_codes=(), skip_codes=()) -> None:
        self.history = history
        self.fail_codes = frozenset(fail_codes)
        self.skip_codes = frozenset(skip_codes)
        self.factory_calls: list[dict] = []
        self.run_calls: list[dict] = []

    def __call__(self, *, admission, lane_bindings, run_context_factory,
                 request_id, prompt_assembler, subject_params=None):
        self.factory_calls.append({
            "admission": admission, "lane_bindings": lane_bindings,
            "run_context_factory": run_context_factory,
            "request_id": request_id, "prompt_assembler": prompt_assembler,
            "subject_params": subject_params})

        def _runner(*, lane, point, approval, data_context, memory_binding,
                    candidate_plan_digest):
            bundle = admission.verify_for_dispatch(candidate_plan_digest)
            plan = bundle.plan
            code = plan.run_id.rsplit("-", 1)[-1]
            self.run_calls.append({
                "run_id": plan.run_id, "code": code, "lane": lane,
                "point_ordinal": point.point_ordinal,
                "digest": candidate_plan_digest,
                "data_context": data_context, "approval": approval})
            if code in self.fail_codes:
                raise RuntimeError(f"scripted lane failure for {code}")
            if code in self.skip_codes:  # ran, committed nothing (honest degrade)
                return SimpleNamespace(run_id=plan.run_id, artifact=None)
            index = [c for c in CODES].index(code)
            self.history.commit_terminal_plan(plan.run_id, ResearchPlan(
                recommendation=_RATINGS[index], rationale=_RATIONALE[index],
                strategic_actions=(f"分批建仓 lane{index}",)))
            return SimpleNamespace(run_id=plan.run_id, artifact="scripted")

        return _runner


class _ArchiveSpy:
    def __init__(self, *, boom: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self._boom = boom

    def __call__(self, artifact_id, artifact_type, markdown) -> None:
        self.calls.append((artifact_id, artifact_type, markdown))
        if self._boom is not None:
            raise self._boom


# =========================================================================== #
# the environment: the REAL router + a REAL standing screening lease            #
# =========================================================================== #
def _issue_screening_lease(e, *, max_admissions=3, budget_cap=100):
    """One REAL durable ``ApprovalLease`` over the router's own journal file.

    Issued through a second coordinator on the SAME journal path (the
    live_decide-suite idiom): the lease is a durable row, so the router's own
    per-request coordinator finds it exactly as production does.
    """
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    issuing = PlanApprovalCoordinator(
        e.journal, admission=SimpleNamespace(), clock=e.clock,
        verifier=_Verifier(), console_emit=None, preset_registry=presets,
        catalog_digest=e.snapshot.catalog_digest,
        registry_digest=e.registry.registry_digest)
    return issuing.issue_lease(
        purpose="观澜 · 选股批量整体成本审批(test)",
        preset_id=SCREENING_LANE_PRESET_ID,
        preset_record_digest=presets.get(
            SCREENING_LANE_PRESET_ID).semantic_digest(),
        catalog_digest=e.snapshot.catalog_digest,
        registry_digest=e.registry.registry_digest,
        valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=1),
        max_admissions=max_admissions, budget_cap_llm_invocations=budget_cap,
        actor=GOOD_CRED, reason="reviewed whole-picture screening cost preview")


class _DriveEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _drive_env(env, trimmed_catalog, tmp_path, *, lease=True, max_admissions=3,
               fail_codes=(), skip_codes=(), archive=None):
    """The REAL router env + the two new injected seams bound to doubles."""
    e = _make_env(env, trimmed_catalog, tmp_path)
    history = _RunHistory(e.stores, e.registry.registry_digest)
    scripted = _ScriptedRunner(history, fail_codes=fail_codes,
                               skip_codes=skip_codes)
    spy = _ArchiveSpy() if archive is None else archive
    e.deps = dataclasses.replace(
        e.deps, plan_runner_factory=scripted, archive_put=spy)
    if lease:
        _issue_screening_lease(e, max_admissions=max_admissions)
    return _DriveEnv(e=e, client=_client(e.deps), runner=scripted, archive=spy,
                     history=history)


def _start(drive, *, top_n=3):
    r = drive.client.post(f"{API}/start",
                          json={"source_kind": "v4", "top_n": top_n})
    assert r.status_code == 200, r.text
    return r.json()


def _by_lane(body):
    return {row["lane_index"]: row for row in body["execution"]["lanes"]}


# =========================================================================== #
# 1. admitted lanes EXECUTE (the missing production surface)                    #
# =========================================================================== #
class TestAdmittedLanesExecute:
    def test_every_lease_admitted_lane_runs_and_the_batch_completes(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        body = _start(drive)

        assert body["outcomes"] == ["lease_admitted"] * 3
        assert body["status"] == BATCH_COMPLETED
        lanes = _by_lane(body)
        assert {i: lanes[i]["status"] for i in (0, 1, 2)} == {
            0: LANE_COMPLETED, 1: LANE_COMPLETED, 2: LANE_COMPLETED}
        assert body["execution"]["completed"] == 3
        # the runner really ran once per lane, in batch order, on the lane's
        # OWN run id (the identity build_screening_batch stamped).
        assert [c["code"] for c in drive.runner.run_calls] == list(CODES)
        assert [c["run_id"] for c in drive.runner.run_calls] == [
            ln["run_id"] for ln in body["lanes"]]

    def test_the_freeze_really_happened_on_every_executed_lane(
            self, env, trimmed_catalog, tmp_path):
        """``PLAN_FROZEN`` per lane run — the admission boundary was crossed for
        real, not simulated (and ``verify_for_dispatch`` inside the scripted
        runner would already have refused otherwise)."""
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        body = _start(drive)
        for lane in body["lanes"]:
            kinds = [ev.event_type for ev in
                     drive.e.stores.events.journal(lane["run_id"], "main")]
            assert EventType.PLAN_FROZEN in kinds, lane["code"]

    def test_the_state_projection_shows_the_executed_runs(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        body = _start(drive)
        state = drive.client.get(
            f"{API}/state", params={"request_id": body["request_id"]}).json()
        runs = {row["run_id"]: row["status"] for row in state["runs"]}
        assert set(runs) == {ln["run_id"] for ln in body["lanes"]}
        assert "not_started" not in set(runs.values())
        assert state["state"]["status"] == BATCH_COMPLETED
        assert [row["status"] for row in state["state"]["lane_outcomes"]] == (
            [LANE_COMPLETED] * 3)

    def test_the_lane_stamp_and_its_subject_reach_the_runner_seam(
            self, env, trimmed_catalog, tmp_path):
        """L1 Task 4's thread, now at a PRODUCTION call site: each lane's own
        materialization-stamped ``subject_params`` and a ``SubjectPromptAssembler``
        closed over THAT lane's committed subject ref."""
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        body = _start(drive)
        assert len(drive.runner.factory_calls) == 3
        for call, lane in zip(drive.runner.factory_calls, body["lanes"]):
            params = call["subject_params"]
            assert params is not None
            # the closed projection document: its singleton instrument set is
            # THIS lane's code (never the batch's first, never a re-projection).
            assert [s.code for s in params.code_value] == [lane["code"]]
            assert isinstance(call["prompt_assembler"], SubjectPromptAssembler)
            assert call["request_id"] == lane["request_id"]
            binding = call["lane_bindings"]["main"]
            assert binding.candidate_plan_digest == lane["candidate_plan_digest"]
            assert binding.reservation_id and binding.approval_event_id

    def test_the_factory_kwargs_are_exactly_the_production_runner_seam(
            self, env, trimmed_catalog, tmp_path):
        """ABI pin: every keyword the driver hands the factory is one the
        production ``build_production_plan_runner`` accepts, so the injected seam
        and the real one can never drift apart silently."""
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        _start(drive)
        accepted = set(
            inspect.signature(build_production_plan_runner).parameters)
        for call in drive.runner.factory_calls:
            assert set(call) <= accepted, sorted(set(call) - accepted)
        assert {"admission", "lane_bindings", "run_context_factory",
                "request_id", "prompt_assembler", "subject_params"} == set(
                    drive.runner.factory_calls[0])


# =========================================================================== #
# 2. a pending_human lane is NEVER executed                                     #
# =========================================================================== #
class TestPendingHumanIsNeverExecuted:
    def test_a_lease_that_covers_one_lane_executes_exactly_that_lane(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path, max_admissions=1)
        body = _start(drive)

        assert body["outcomes"] == ["lease_admitted", "pending_human",
                                    "pending_human"]
        lanes = _by_lane(body)
        assert lanes[0]["status"] == LANE_COMPLETED
        assert lanes[1]["status"] == LANE_PENDING_HUMAN
        assert lanes[2]["status"] == LANE_PENDING_HUMAN
        assert "human" in lanes[1]["reason"]
        assert [c["code"] for c in drive.runner.run_calls] == [CODES[0]]
        assert body["status"] == BATCH_PARTIAL

    def test_a_pending_lane_is_never_frozen_and_never_journalled(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path, max_admissions=1)
        body = _start(drive)
        for lane in body["lanes"][1:]:
            kinds = [ev.event_type for ev in
                     drive.e.stores.events.journal(lane["run_id"], "main")]
            assert EventType.PLAN_FROZEN not in kinds, lane["code"]

    def test_without_a_lease_the_door_behaves_exactly_as_before(
            self, env, trimmed_catalog, tmp_path):
        """The default-on executor must be BIT-UNCHANGED where nothing was
        admitted: no lease ⇒ three pending cards, zero runs, zero landings, and
        the door's own ``awaiting_approval`` status."""
        drive = _drive_env(env, trimmed_catalog, tmp_path, lease=False)
        body = _start(drive)
        assert body["outcomes"] == ["pending_human"] * 3
        assert body["status"] == BATCH_AWAITING_APPROVAL
        assert drive.runner.run_calls == []
        assert drive.runner.factory_calls == []
        assert drive.archive.calls == []


# =========================================================================== #
# 3. one lane's failure never takes the batch down                              #
# =========================================================================== #
class TestFailureIsolation:
    def test_a_failing_lane_is_recorded_and_its_siblings_still_run(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path,
                           fail_codes=(CODES[1],))
        body = _start(drive)
        lanes = _by_lane(body)
        assert lanes[0]["status"] == LANE_COMPLETED
        assert lanes[1]["status"] == LANE_FAILED
        assert lanes[2]["status"] == LANE_COMPLETED
        assert "scripted lane failure" in lanes[1]["reason"]
        assert body["status"] == BATCH_PARTIAL
        # the sibling AFTER the failure really ran (a batch abort would show
        # exactly two run calls and lane 2 pending).
        assert [c["code"] for c in drive.runner.run_calls] == list(CODES)

    def test_the_slate_folds_the_completed_lanes_only(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path,
                           fail_codes=(CODES[1],))
        body = _start(drive)
        slate = body["execution"]["slate"]
        assert [e["code"] for e in slate["entries"]] == [CODES[0], CODES[2]]
        assert slate["degraded"] == [{"lane_index": 1, "code": CODES[1]}]

    def test_zero_completed_lanes_lands_nothing_and_says_failed(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path, fail_codes=CODES)
        body = _start(drive)
        assert body["status"] == BATCH_FAILED
        assert body["execution"]["completed"] == 0
        assert body["execution"]["failed"] == 3
        assert body["execution"].get("slate") is None
        assert drive.archive.calls == []

    def test_lanes_that_ran_but_committed_no_verdict_land_nothing(
            self, env, trimmed_catalog, tmp_path):
        """Completed ≠ a verdict: a run that committed no ``ResearchPlan@1``
        leaves an EMPTY slate, and an empty document must never be filed under
        the day-scoped (upsert!) archive id."""
        drive = _drive_env(env, trimmed_catalog, tmp_path, skip_codes=CODES)
        body = _start(drive)
        assert body["status"] == BATCH_COMPLETED
        assert drive.archive.calls == []
        assert body["execution"].get("archive_id") is None


# =========================================================================== #
# 4. assemble → render → land                                                   #
# =========================================================================== #
class TestTheProductLands:
    def test_the_document_is_filed_once_day_scoped_and_banner_first(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        body = _start(drive)
        assert len(drive.archive.calls) == 1
        artifact_id, artifact_type, markdown = drive.archive.calls[0]
        assert artifact_id == "rs_orch_screen_20260724"
        assert artifact_type == "research"
        assert markdown.splitlines()[0] == RECOMMENDATION_ADVISORY_BANNER
        assert body["batch_id"] in markdown
        assert body["execution"]["archive_id"] == artifact_id
        assert body["execution"]["landed"] is True

    def test_re_posting_the_same_body_replays_and_never_re_lands(
            self, env, trimmed_catalog, tmp_path):
        """The idempotent door + the idempotent landing, together: a re-POST
        answers the durable receipt, so the batch is neither re-run nor
        re-filed (and the archive id is a pure function of the slate anyway)."""
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        first = _start(drive)
        again = _start(drive)
        assert again["replayed"] is True
        assert again["status"] == first["status"]
        assert len(drive.archive.calls) == 1
        assert len(drive.runner.run_calls) == 3

    def test_a_landing_failure_is_recorded_never_swallowed(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path,
                           archive=_ArchiveSpy(boom=RuntimeError("archive down")))
        body = _start(drive)
        assert body["execution"]["landed"] is False
        assert "archive down" in body["execution"]["landing_error"]
        # the runs happened and say so; the FILING failed and says so.
        assert body["execution"]["completed"] == 3
        assert body["status"] == BATCH_COMPLETED

    def test_screening_latest_serves_the_landed_slate(
            self, env, trimmed_catalog, tmp_path):
        """The A-line tail on the wire. No ``RecommendationSlate@1`` payload is
        committed in production (its registry is not one of the five the startup
        resolver holds — adding one would wedge every existing store on restart),
        so the durable REQUEST RECORD is the slate's authority and the route
        serves it, naming its source."""
        drive = _drive_env(env, trimmed_catalog, tmp_path,
                           fail_codes=(CODES[1],))
        started = _start(drive)
        got = drive.client.get(f"{API}/screening/latest")
        assert got.status_code == 200
        slate = got.json()["slate"]
        assert slate["source"] == "request_record"
        assert slate["batch_id"] == started["batch_id"]
        assert slate["archive_id"] == "rs_orch_screen_20260724"
        assert slate["advisory_banner"] == RECOMMENDATION_ADVISORY_BANNER
        assert [e["code"] for e in slate["entries"]] == [CODES[0], CODES[2]]
        assert slate["degraded"] == [{"lane_index": 1, "code": CODES[1]}]
        assert slate["landed"] is True
        for entry in slate["entries"]:
            assert re.fullmatch(r"[0-9a-f]{64}", entry["research_plan_digest"])

    def test_screening_latest_is_still_an_honest_empty_before_any_drive(
            self, env, trimmed_catalog, tmp_path):
        drive = _drive_env(env, trimmed_catalog, tmp_path)
        assert drive.client.get(f"{API}/screening/latest").json() == {
            "ok": True, "slate": None}


# =========================================================================== #
# 5. the driver's own refusals + the shared reservation key                     #
# =========================================================================== #
class TestDriverRefusals:
    def test_a_mismatched_outcome_pairing_refuses_rather_than_guesses(self):
        build = SimpleNamespace(
            lanes=(SimpleNamespace(), SimpleNamespace(), SimpleNamespace()),
            batch=SimpleNamespace(batch_id="b" * 64))
        with pytest.raises(ScreeningError) as exc:
            execute_screening_batch(
                build, outcomes=(SimpleNamespace(outcome="pending_human"),),
                dispatcher=None, stores=None, context=None,
                plan_runner_factory=None)
        assert "positional" in str(exc.value)

    def test_a_subject_that_does_not_bind_its_committed_ref_refuses(self):
        lane = SimpleNamespace(
            code="600519", lane_index=0,
            subject_ref=TypedPayloadRef(
                schema_ref=screening_mod.RUN_SUBJECT_SCHEMA_REF,
                payload_ref=PayloadRef(namespace="main", object_id="rs-x",
                                       content_digest="f" * 64)))
        with pytest.raises(SubjectBondRefused):
            driver_mod._run_subject_of(lane, as_of=NOW)

    def test_the_reservation_key_is_the_ONE_helper_both_sides_call(self):
        """A drifted key would not replay — it would mint a SECOND reservation
        for the same lane. Both call sites must be the helper, by source."""
        assert screening_reserve_idempotency_key("bid", "600519") == (
            "screening-reserve:bid:600519")
        for module in (screening_mod, driver_mod):
            src = Path(module.__file__).read_text(encoding="utf-8")
            assert "screening_reserve_idempotency_key(" in src
        assert 'f"screening-reserve:' not in Path(
            driver_mod.__file__).read_text(encoding="utf-8")

    def test_the_executor_gate_names_the_one_admitting_outcome(self):
        """The load-bearing guard, pinned at source: execution is entered only
        for ``lease_admitted`` (the mutation that drops it reddens the
        pending-human tests above)."""
        src = inspect.getsource(execute_screening_batch)
        assert "!= LEASE_ADMITTED" in src
        assert driver_mod.LEASE_ADMITTED == "lease_admitted"


# =========================================================================== #
# 6. the projection helper (pure)                                               #
# =========================================================================== #
class TestSlateProjection:
    def test_the_projection_names_the_degraded_codes_from_the_batchs_lanes(self):
        slate = SimpleNamespace(
            as_of=NOW, batch_id="b" * 64, entries=(), degraded_lanes=(0, 2),
            badges=("no_cross_sectional_summary_v1",))
        build = SimpleNamespace(lanes=(
            SimpleNamespace(lane_index=0, code="600519"),
            SimpleNamespace(lane_index=1, code="000001"),
            SimpleNamespace(lane_index=2, code="300750")))
        projection = slate_projection(
            slate, build=build, archive_id="rs_orch_screen_20260724",
            landed=True)
        assert projection["degraded"] == [
            {"lane_index": 0, "code": "600519"},
            {"lane_index": 2, "code": "300750"}]
        assert projection["entries"] == []
        assert projection["badges"] == ["no_cross_sectional_summary_v1"]
        assert projection["landed"] is True


def test_dataclasses_are_frozen_carriers_not_contract_models():
    """The Phase-1 completeness walk / classification firewall stay inert over
    this module (the api.py precedent): frozen dataclasses + functions only."""
    assert dataclasses.is_dataclass(driver_mod.LaneExecution)
    assert dataclasses.is_dataclass(driver_mod.ScreeningExecutionReport)
    from guanlan_v2.orchestration.digest import ContractModel

    for name in driver_mod.__all__:
        obj = getattr(driver_mod, name)
        assert not (isinstance(obj, type) and issubclass(obj, ContractModel)), name
