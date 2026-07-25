# -*- coding: utf-8 -*-
"""Phase 9 · Task 12 — the whole-framework RED-LINE regression suite.

One test per spec §11 红线回归 line (eight verbatim lines), plus the R2/AMEND-1
replay-boundary honesty probe. Every test is **structural** (a catalog / registry /
source scan, or a behavioural probe over the real contracts) and runs OFFLINE: no live
server, no network, no vendor credential, no ``G:\\stocks`` dependency.

Scope discipline (ruling R14): the structural Lane-0 UNAVAILABLE properties stay
Phase-5-owned and the Phase-6 shadow红线 stay in ``test_shadow_redlines.py``. This suite
proves the FRAMEWORK-level red lines over the Phase-9 surface — the cumulative Phase-9
catalog, the Phase-9 adapters (落子 replay / 帷幄 ONLINE / the thin router) and the
seams they bind.

Run: ``python -m pytest tests/orchestration/test_redline_regression.py -v``
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration.adapters import chain as ch
from guanlan_v2.orchestration.adapters import luozi as lz
from guanlan_v2.orchestration.adapters import weiwo as ww
from guanlan_v2.orchestration.adapters.api import (
    ReplayCoordinatorApprovalRefused,
    fold_degraded_points_into_report_badges,
)
from guanlan_v2.orchestration.adapters.luozi import (
    ReplayApprovalRefused,
    run_interval_replay,
)
from guanlan_v2.orchestration.adapters.replay_data import (
    ARCHIVE_FEED_IDS,
    ArchiveFeedFloor,
    derive_replay_feasible_window,
)
from guanlan_v2.orchestration.adapters.weiwo import (
    WeiwoDraftStatusError,
    resolve_weiwo_capability_binding,
    run_weiwo_research,
    save_draft_to_factorlib,
)
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    PlanSource,
)
from guanlan_v2.orchestration.memory.catalog import memory_propose_capability
from guanlan_v2.orchestration.shadow import TargetPortfolioIntent
from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft

# real, reviewed harnesses reused from the sibling Phase-9 suites (the established
# cross-suite fixture-reuse precedent in tests/orchestration).
from tests.orchestration.test_adapters_api import (
    _RecordingApproval,
    _client,
    _coordinator_world,
    _floors,
    _production_coordinator,
    _replay_deps,
    _start_body,
)
from tests.orchestration.test_luozi_replay import (
    _FakeCoordinator,
    _bindings,
    _exec_config,
    _request,
    _three_point_env,
    _utc,
)
from tests.orchestration.test_plan_structure import _draft, _node
from tests.orchestration.test_weiwo_adapter import (
    _FakeApproval,
    _FakeFallback,
    _FakeOptimizer,
    _FakePlanner,
    _FrozenClock,
    _ProbeStub,
    _SaverSpy,
    _World,
    _bindings as _weiwo_bindings,
    _candidate,
    _ok_env,
    AS_OF as WEIWO_AS_OF,
)
from tests.orchestration.test_weiwo_adapter import (  # noqa: F401 — the module fixture
    schema_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: tokens that name an order / signal / execution WRITE capability. If any of these
#: appears in a capability id / kind / operation, an LLM worker could be granted a real
#: trading actuator — the single hardest red line in the whole framework.
_ORDER_WRITE_TOKENS = (
    "order", "trade", "execut", "actuat", "broker", "signal_write", "place",
    "submit", "buy", "sell", "cancel",
)

#: tokens that name a memory / skill / code WRITE capability (a worker that could edit
#: the system's own memory, skills or source).
_SELF_WRITE_TOKENS = ("skill", "code", "path_write", "file_write", "write_file")


def _catalog():
    return ch.phase9_catalog_snapshot()


# =========================================================================== #
# 1. LLM zero trading — no order/signal write capability exists to grant       #
# =========================================================================== #
def test_llm_has_no_real_order_or_signal_capability():
    """spec §11: LLM 零交易 — 全框架不存在下单/写信号能力。

    Scans the CUMULATIVE Phase-9 catalog (the snapshot every Phase-9 plan is bound to)
    plus the two Phase-9 data-source descriptors and the 帷幄 live capability binding.
    """
    snap = _catalog()
    assert snap.workers, "the Phase-9 catalog must carry the inherited worker table"

    # ① no capability in the whole manifest is an order/signal actuator.
    for cap in snap.capability_manifest:
        kind = str(getattr(cap, "capability_kind", "")).lower()
        cid = str(cap.ref.id).lower()
        assert kind in ("data_adapter", "tool"), (cid, kind)
        for token in _ORDER_WRITE_TOKENS:
            assert token not in cid, f"capability id {cid!r} looks order/signal-write"
            assert token not in kind, f"capability kind {kind!r} looks order/signal-write"

    # ② no worker carries a live/executable decision authority, and no worker's
    #    allowlist references a capability outside the reviewed manifest.
    manifest_ids = {c.ref.id for c in snap.capability_manifest}
    for w in snap.workers:
        authority = str(getattr(w, "decision_authority", "none")).lower()
        assert authority in ("none", "advisory_only"), (w.id, authority)
        for ref in w.capability_allowlist:
            assert ref.id in manifest_ids, (w.id, ref.id)

    # ③ every data method the two Phase-9 sources bind is read_only (a Literal[True] on
    #    a frozen strict model — a writable method spec is structurally unconstructible).
    replay_desc, live_desc, _materials = ch.build_phase9_source_materials()
    specs = {s.method_ref.id: s for s in phase3_data_surface().method_specs}
    bound: set[str] = set()
    for desc in (replay_desc, live_desc):
        for ref in desc.method_refs:
            spec = specs[ref.id]
            assert spec.read_only is True, (desc.source_id, ref.id)
            bound.add(ref.id)
        # the descriptor grants only capabilities that already exist in the catalog.
        for cref in desc.method_capability_refs:
            assert cref.id in manifest_ids, (desc.source_id, cref.id)
    assert bound, "the Phase-9 descriptors must bind at least one data method"

    # ④ the LIVE (ONLINE) adapter binding registers NO write capability at all.
    binding = resolve_weiwo_capability_binding(
        method_specs=phase3_data_surface().method_specs)
    assert binding.write_capability_names() == ()


# =========================================================================== #
# 2. A shadow intent can never be promoted in place                            #
# =========================================================================== #
def test_shadow_intent_cannot_be_promoted_in_place():
    """spec §11: 影子 intent 不可原地升格为真实指令。"""
    fields = TargetPortfolioIntent.model_fields
    # ① the three envelope fields are single-value Literals defaulted to the shadow
    #    values — the ONLY constructible combination.
    assert fields["origin"].default == "LLM"
    assert fields["authority"].default == "ADVISORY_ONLY"
    assert fields["execution_scope"].default == "SHADOW_ONLY"

    # ② no other value is constructible (frozen + strict + Literal).
    sch, cal, start, end = _three_point_env()
    binds, _ = _bindings(sch, cal)
    run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds)
    intent = binds.intent_ledger.intents[0]
    for field, bad in (("execution_scope", "LIVE"), ("authority", "EXECUTE"),
                       ("origin", "HUMAN")):
        with pytest.raises(Exception):
            intent.model_copy(update={field: bad}).model_validate(
                intent.model_dump() | {field: bad})
        with pytest.raises(Exception):
            type(intent)(**(intent.model_dump() | {field: bad}))
    # frozen: no in-place mutation either.
    with pytest.raises(Exception):
        intent.execution_scope = "LIVE"

    # ③ no public API of the Phase-9 / Phase-6 shadow surface is named as a promotion
    #    path, and no exported callable accepts an intent and yields a live instruction.
    promotion_tokens = (
        "to_live", "to_order", "to_signal", "to_seats", "to_ledger", "promote",
        "place_order", "send_order", "execute_trade", "go_live", "activate_live",
        "to_trade", "submit_order",
    )
    from guanlan_v2.orchestration.adapters import api as adapters_api
    from guanlan_v2.orchestration.adapters import retirement as rt
    from guanlan_v2.orchestration import shadow as shadow_mod

    for module in (shadow_mod, lz, adapters_api, ww, ch, rt):
        for name in getattr(module, "__all__", ()):  # the reviewed closed surfaces
            low = name.lower()
            for token in promotion_tokens:
                assert token not in low, (module.__name__, name)

    # ④ the human-approval surface refuses the promotion SHAPE: an approval binds a
    #    (request_id, candidate_plan_digest) pair — it has no field able to carry a
    #    shadow artifact, so an intent can never be "approved into" a live instruction.
    from guanlan_v2.orchestration.events import PlanApproval
    approval_fields = set(PlanApproval.model_fields)
    assert not (approval_fields & {"intent", "intent_id", "intents", "positions",
                                   "execution_scope", "order", "orders"})


# =========================================================================== #
# 3. An unapproved DYNAMIC plan never executes                                 #
# =========================================================================== #
def test_unapproved_dynamic_plan_never_executes():
    """spec §11: 未批准的 DYNAMIC 计划绝不执行(零预留泄漏)。"""
    # ① the replay coordinator: a candidate whose durable decision is missing (or
    #    REJECTED) refuses BEFORE any reservation — the ledger stays empty.
    world, store_meta, reg = _coordinator_world()
    sch, cal, start, end = _three_point_env()
    from tests.orchestration.test_adapters_api import _first_point

    for verdict in (None, ApprovalDecision.REJECTED):
        base, _ = _bindings(sch, cal)
        coord = _production_coordinator(
            world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
            execution_config=_exec_config(sch), request=_request(sch),
            budget=base.budget, feed_floors=_floors(),
            approval=_RecordingApproval(verdict))
        with pytest.raises(ReplayCoordinatorApprovalRefused):
            coord.bootstrap_context(_first_point(sch, cal, start, end))
        assert base.budget.replay().reservations == {}, "an unapproved plan leaked a reservation"

    # ② the 帷幄 ONLINE driver: a DYNAMIC research plan whose human decision is not
    #    APPROVED halts before the optimizer — nothing runs, nothing is written.
    world_w = _World(pytest.importorskip("guanlan_v2.orchestration.data.schema_registry")
                     and _weiwo_schema_registry(), clock=_FrozenClock(WEIWO_AS_OF),
                     probe_stub=_ProbeStub(_ok_env(items=[])))
    optimizer = _FakeOptimizer(passing=(_candidate(),))
    saver = _SaverSpy()
    receipt = run_weiwo_research(
        OrchestrationRequest(request_id="req-redline-dyn", goal="研究",
                             workflow="orchestrate_and_optimize",
                             approval_policy=ApprovalPolicy.REQUIRED),
        bindings=_weiwo_bindings(
            world_w, planner=_FakePlanner(_candidate()),
            approval=_FakeApproval(ApprovalDecision.REJECTED), optimizer=optimizer,
            factor_saver=saver))
    assert receipt.stop_reason == "unapproved"
    assert receipt.draft_ids == ()
    assert optimizer.calls == [] and saver.calls == []

    # ③ BEHAVIOURAL: a DYNAMIC pending card can never carry preset provenance — which
    #    is exactly why the Phase-7 lease channel (which only admits a preset-backed
    #    card) can never auto-admit a DYNAMIC plan. Constructed for real, both ways.
    from guanlan_v2.orchestration.plan_diff import PendingPlanApproval

    with pytest.raises(Exception):                       # DYNAMIC + preset provenance
        _pending_card(source=PlanSource.DYNAMIC, preset_id="preset.x",
                      preset_record_digest="c" * 64)
    with pytest.raises(Exception):                       # PRESET_FALLBACK without it
        _pending_card(source=PlanSource.PRESET_FALLBACK)
    dynamic = _pending_card(source=PlanSource.DYNAMIC)   # the only DYNAMIC shape there is
    assert dynamic.preset_id is None and dynamic.preset_record_digest is None
    # SUPPLEMENT (corroborating, NOT load-bearing — the constructions above are the proof)
    assert "preset_id" in PendingPlanApproval.model_fields


def _pending_card(*, source, approval_policy=None, preset_id=None,
                  preset_record_digest=None):
    """A REAL :class:`PendingPlanApproval` (the Phase-7 human-approval card)."""
    from guanlan_v2.orchestration.plan_diff import PLAN_DIFF_SCHEMA_REF, PendingPlanApproval
    from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

    diff_digest = "d" * 64
    ref = TypedPayloadRef(
        schema_ref=PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="diff-obj",
                               content_digest=diff_digest))
    return PendingPlanApproval(
        request_id="req-redline", candidate_plan_digest="a" * 64, goal="g",
        source=source,
        approval_policy=approval_policy or ApprovalPolicy.REQUIRED,
        node_count=1, worker_ids=("w.a",), plan_diff_ref=ref, rendered_md="body",
        rendered_from_diff_digest=diff_digest, candidate_id="cand-1",
        requested_at=datetime(2026, 7, 5, 1, 0, tzinfo=timezone.utc),
        preset_id=preset_id, preset_record_digest=preset_record_digest)


def _weiwo_schema_registry():
    from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
    from guanlan_v2.orchestration.runtime_contracts import (
        PHASE2_BASE_REGISTRY_DIGEST,
        phase2_runtime_registry,
    )

    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


# =========================================================================== #
# 4. No silent fallback without an explicit preset                             #
# =========================================================================== #
def test_no_silent_fallback_without_explicit_preset():
    """spec §11: planner 失败 + ``fallback_preset_id=None`` ⇒ 诚实终止,绝不悄悄套模板。"""
    world = _World(_weiwo_schema_registry(), clock=_FrozenClock(WEIWO_AS_OF),
                   probe_stub=_ProbeStub(_ok_env(items=[])))
    optimizer = _FakeOptimizer(passing=(_candidate(),))
    fallback = _FakeFallback(_candidate())
    saver = _SaverSpy()
    receipt = run_weiwo_research(
        OrchestrationRequest(request_id="req-redline-nofb", goal="研究",
                             workflow="orchestrate_and_optimize",
                             approval_policy=ApprovalPolicy.REQUIRED),
        bindings=_weiwo_bindings(
            world, planner=_FakePlanner(None),          # the planner FAILED
            approval=_FakeApproval(ApprovalDecision.APPROVED), optimizer=optimizer,
            fallback_materializer=fallback, factor_saver=saver))
    assert receipt.stop_reason == "halted_no_fallback"
    assert receipt.draft_ids == ()
    assert fallback.calls == [], "a preset was materialized without an explicit id"
    assert optimizer.calls == [] and saver.calls == []

    # SUPPLEMENT (corroborating, NOT load-bearing — the behavioural halt above is the
    # proof): the orchestrator's own fallback resolver terminates with NO preset.
    from guanlan_v2.orchestration.orchestrator import PlannerRunRecord

    text = Path(PlannerRunRecord.__module__.replace(".", "/") + ".py")
    source = (REPO_ROOT / text).read_text(encoding="utf-8")
    assert 'return "halted_no_fallback", None, None, None, None' in source, (
        "the planner fallback resolver must terminate honestly with NO preset")


# =========================================================================== #
# 5. Drafts never auto-promote                                                 #
# =========================================================================== #
def test_drafts_never_auto_promote():
    """spec §11: 产物只落 draft,绝不自动升格(``_VALID_SAVE_STATUS`` 回归)。"""
    from guanlan_v2.factorlib.api import _VALID_SAVE_STATUS

    # ① the factorlib gate itself: only "" / "draft" are valid save statuses.
    assert _VALID_SAVE_STATUS == {"", "draft"}

    # ② the adapter's own guard refuses a non-draft candidate BEFORE factorlib.
    with pytest.raises(WeiwoDraftStatusError):
        save_draft_to_factorlib(
            {"name": "lib_redline", "expr": "1", "status": "active"},
            provenance_digest="f" * 64)

    # ③ NO code path in the orchestration package calls a promote function.
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "guanlan_v2" / "orchestration").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None) or ""
                if "promote" in str(name).lower():
                    offenders.append(f"{path.name}:{node.lineno}:{name}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if "promote" in alias.name.lower():
                        offenders.append(f"{path.name}:{node.lineno}:{alias.name}")
    assert offenders == [], f"an orchestration module reaches a promote path: {offenders}"

    # ④ the adapter is pinned to the draft literal. SUPPLEMENT (corroborating, NOT
    #    load-bearing — ② and ③ above are the proof, and test_phase9_e2e's
    #    test_weiwo_e2e reads the landed product's status off disk).
    assert ww._DRAFT_STATUS == "draft"
    src = Path(ww.__file__).read_text(encoding="utf-8")
    assert "status=_DRAFT_STATUS" in src


# =========================================================================== #
# 6. Workers never write memory / skill / code                                 #
# =========================================================================== #
def test_workers_never_write_memory_skill_code():
    """spec §11: worker 绝不写记忆/技能/代码;记忆变更只经 ``memory.propose`` → 人审。"""
    snap = _catalog()
    propose_ref, propose_material = memory_propose_capability()
    descriptor = propose_material.descriptor
    assert propose_ref.id == "cap.memory.propose"
    assert descriptor.operation == "memory.propose"

    # ① NO inherited worker holds the memory-propose capability (least privilege).
    for w in snap.workers:
        held = {ref.id for ref in w.capability_allowlist}
        assert propose_ref.id not in held, f"worker {w.id} already holds memory.propose"
        for cid in held:
            for token in _SELF_WRITE_TOKENS:
                assert token not in cid.lower(), (w.id, cid)

    # ② no capability in the whole manifest is a skill / code / path writer.
    for cap in snap.capability_manifest:
        cid = str(cap.ref.id).lower()
        for token in _SELF_WRITE_TOKENS:
            assert token not in cid, f"capability {cid!r} looks like a self-write"

    # ③ memory changes are PROPOSALS — the capability's output is a receipt, and the
    #    proposal path is grant-gated. BEHAVIOURAL: the real grant gate is EXECUTED
    #    against a real sealed worker that holds no memory.propose capability, and it
    #    refuses. (`_verify_grant` reads only `self._facade`, and its capability branch
    #    fires before the request is touched, so the unbound call is the whole gate.)
    from guanlan_v2.orchestration.memory.catalog import phase3_memory_surface
    from guanlan_v2.orchestration.memory.models import MemoryAuthorityError
    from guanlan_v2.orchestration.memory.proposals import MemoryProposalService

    assert descriptor.output_schema_ref.name == "MemoryProposalReceipt"
    assert descriptor.input_schema_ref.name == "MemoryProposalRequest"
    facade = phase3_memory_surface().facade_descriptor
    ungranted = snap.workers[0]
    assert propose_ref.id not in {c.id for c in ungranted.capability_allowlist}
    with pytest.raises(MemoryAuthorityError):
        MemoryProposalService._verify_grant(
            SimpleNamespace(_facade=facade), None, worker=ungranted,
            context=SimpleNamespace(memory_session_id="cs.redline"))

    # ④ the 帷幄 ONLINE binding grants NO write capability whatsoever.
    binding = resolve_weiwo_capability_binding(
        method_specs=phase3_data_surface().method_specs)
    assert binding.write_capability_names() == ()


# =========================================================================== #
# 7. Degradation is ALWAYS badged                                              #
# =========================================================================== #
def test_degradation_always_badged():
    """spec §11: 任何降级都必须带徽章显形(绝不静默降级)。"""
    sch, cal, start, end = _three_point_env()
    floors = tuple(
        ArchiveFeedFloor(feed_id=fid, floor_date="2027-01-01",
                         archive_root=f"var/archive/{fid}")
        for fid in ARCHIVE_FEED_IDS
    )
    binds, _ = _bindings(sch, cal, feed_floors=floors)
    state = run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds)

    # ① EVERY degraded decision point carries at least one non-empty badge naming a
    #    reason — a degraded point is never a silent gap and never a run failure.
    degraded = binds.intent_ledger.degraded_points
    assert set(degraded) == {1, 2, 3}
    assert state.completed_points == 3
    for ordinal, badges in degraded.items():
        assert badges, f"point {ordinal} degraded with NO badge"
        for badge in badges:
            assert badge.strip() and ":" in badge, (ordinal, badge)

    # ② the feasibility verdicts themselves: every UNAVAILABLE verdict carries a badge
    #    AND a reason, and never a numeric payload to zero-fill.
    window = derive_replay_feasible_window(
        as_of=_utc("2026-07-06", 14, 0), feed_floors=floors)
    assert window.unavailable_feed_ids
    for verdict in window.verdicts:
        if verdict.status == "UNAVAILABLE":
            assert verdict.badge and verdict.reason
            assert not hasattr(verdict, "value")

    # ③ the degradation reaches the REPORT surface: the Task-12 fold carries every
    #    per-point badge onto the DualCurveReport a consumer projects.
    from tests.orchestration.test_dual_curves import (
        _alpha_frames, _points, _report_over, _runner,
    )

    runner = _runner(_alpha_frames())
    report = _report_over(runner, _points(runner))
    folded = fold_degraded_points_into_report_badges(report, degraded)
    for badges in degraded.values():
        for badge in badges:
            assert badge in folded.badges, badge
    assert set(report.badges) <= set(folded.badges)   # additive, never a rewrite


# =========================================================================== #
# 8. AUTO approval is still rejected EVERYWHERE                                #
# =========================================================================== #
@pytest.mark.parametrize("source", list(PlanSource))
def test_auto_approval_still_rejected_everywhere(source):
    """spec §11: ``ApprovalPolicy.AUTO`` 在每个 PlanSource 上都被拒(含新 router)。"""
    from guanlan_v2.orchestration.schema_registry import default_registry

    snap = _catalog()
    registry = default_registry()
    request = OrchestrationRequest(
        request_id="req-1", goal="g", workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.AUTO)
    draft = _draft(
        (_node("na", writes_slot="sa"),), ("na",),
        source=source,
        catalog_digest=snap.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.AUTO,
    )
    report = validate_plan_draft(
        draft, request=request, context=None, catalog=snap, schema_registry=registry)
    assert report.valid is False
    assert "auto_approval_rejected" in {i.code for i in report.issues}


def test_auto_approval_is_unreachable_through_the_phase9_surfaces():
    # ① BEHAVIOURAL: the pending human-approval card is genuinely unconstructible for
    #    AUTO — a real construction is attempted for both admissible sources.
    for source in (PlanSource.DYNAMIC, PlanSource.PRESET_FALLBACK):
        provenance = ({} if source is PlanSource.DYNAMIC
                      else {"preset_id": "preset.x", "preset_record_digest": "c" * 64})
        with pytest.raises(Exception):
            _pending_card(source=source, approval_policy=ApprovalPolicy.AUTO,
                          **provenance)
        assert _pending_card(source=source, **provenance).approval_policy is (
            ApprovalPolicy.REQUIRED)

    # ② the interval-replay driver refuses AUTO before ANYTHING runs.
    sch, cal, start, end = _three_point_env()
    coord = _FakeCoordinator()
    binds, sink = _bindings(sch, cal, coordinator=coord)
    with pytest.raises(ReplayApprovalRefused):
        run_interval_replay(
            request=_request(sch, approval=ApprovalPolicy.AUTO), schedule=sch,
            execution_config=_exec_config(sch), interval_start=start,
            interval_end=end, bindings=binds)
    assert coord.bootstrap_calls == [] and sink.budget_events() == ()

    # ③ the production coordinator refuses AUTO at the door.
    world, store_meta, reg = _coordinator_world()
    from tests.orchestration.test_adapters_api import _first_point

    base, _ = _bindings(sch, cal)
    production = _production_coordinator(
        world=world, store_meta=store_meta, schema_registry=reg, schedule=sch,
        execution_config=_exec_config(sch),
        request=_request(sch, approval=ApprovalPolicy.AUTO),
        budget=base.budget, feed_floors=_floors())
    with pytest.raises(ReplayCoordinatorApprovalRefused):
        production.bootstrap_context(_first_point(sch, cal, start, end))

    # ④ through the NEW router: a hostile body cannot ask for AUTO — the door mints its
    #    own REQUIRED request and ignores the field outright.
    deps = _replay_deps()
    response = _client(deps).post(
        "/orchestration/replay/start", json=_start_body(approval_policy="auto"))
    assert response.status_code == 200, response.text
    assert response.json()["approval_policy"] == ApprovalPolicy.REQUIRED.value
    recorded = deps.replay_requests[response.json()["request_id"]]
    assert recorded["request"].approval_policy is ApprovalPolicy.REQUIRED


# =========================================================================== #
# 9. (R2/AMEND-1) the replay boundary is honestly UNAVAILABLE                   #
# =========================================================================== #
def test_replay_boundary_unavailable_honest(tmp_path):
    """R2/AMEND-1: 决策点早于归档覆盖下限 ⇒ UNAVAILABLE + 徽章;绝不补零,绝不拿当前快照冒充历史。

    Boundary probe only — the structural Lane-0 UNAVAILABLE tests stay Phase-5-owned
    (ruling R14).
    """
    floor = "2026-07-08"
    floors = tuple(
        ArchiveFeedFloor(feed_id=fid, floor_date=floor, archive_root=f"var/archive/{fid}")
        for fid in ARCHIVE_FEED_IDS
    )
    before = derive_replay_feasible_window(
        as_of=_utc("2026-07-07", 14, 0), feed_floors=floors)
    on_floor = derive_replay_feasible_window(
        as_of=_utc("2026-07-08", 14, 0), feed_floors=floors)

    # ① one session before the floor ⇒ UNAVAILABLE for every feed, with a badge; ON the
    #    floor everything resolves (the boundary is exact).
    assert set(before.unavailable_feed_ids) == set(ARCHIVE_FEED_IDS)
    assert on_floor.unavailable_feed_ids == ()
    for verdict in before.verdicts:
        assert verdict.status == "UNAVAILABLE"
        assert verdict.badge == f"archive_pre_floor:{verdict.feed_id}"
        # ② never zero-filled: the verdict carries NO numeric payload at all.
        assert not hasattr(verdict, "value") and not hasattr(verdict, "series")

    # ③ the pre-floor verdict digest is STABLE as the archive genuinely ACCRETES newer
    #    rows: the archive feeds gain rows (same earliest `first_date`) and the macro
    #    snapshots file gains a NEWER line on disk. An old point's verdict must not move.
    from guanlan_v2.orchestration.adapters.replay_data import (
        archive_feed_floor_from_read,
        macro_feed_floor_from_snapshots,
    )

    snaps = tmp_path / "snapshots.jsonl"
    snaps.write_text(
        json.dumps({"ts": "2026-07-08T15:00:00"}) + "\n", encoding="utf-8")

    def _floors_now(rows):
        archive = tuple(
            archive_feed_floor_from_read(
                fid, {"kind": fid, "rows": rows, "first_date": "20260708"},
                archive_root="var/archive")
            for fid in ARCHIVE_FEED_IDS if fid != "macro")
        return archive + (macro_feed_floor_from_snapshots(snapshots_path=snaps),)

    thin_rows = [{"date": "20260708"}]
    grown_before = _floors_now(thin_rows)
    thin = derive_replay_feasible_window(
        as_of=_utc("2026-07-07", 14, 0), feed_floors=grown_before)
    lines_before = len(snaps.read_text(encoding="utf-8").strip().splitlines())

    with snaps.open("a", encoding="utf-8") as fh:          # the archive really grows
        fh.write(json.dumps({"ts": "2026-07-20T15:00:00"}) + "\n")
    fat_rows = [{"date": "20260708"}, {"date": "20260720"}]
    grown_after = _floors_now(fat_rows)
    # the underlying archive GENUINELY accreted: more rows in the read result and one
    # more line on disk in the macro snapshots store…
    assert len(fat_rows) > len(thin_rows)
    assert len(snaps.read_text(encoding="utf-8").strip().splitlines()) == lines_before + 1

    # …yet the derived floors — and therefore the old point's verdict digest — do NOT
    # move. That is the property: an old decision point's honesty verdict is invariant
    # under forward growth of the archive.
    assert grown_after == grown_before
    fat = derive_replay_feasible_window(
        as_of=_utc("2026-07-07", 14, 0), feed_floors=grown_after)
    assert fat.digest == thin.digest
    assert set(fat.unavailable_feed_ids) == set(ARCHIVE_FEED_IDS)

    # ④ a current snapshot can never impersonate history: a PIT_REPLAY method
    #    resolution structurally excludes the ONLINE-only descriptor.
    replay_desc, live_desc, _m = ch.build_phase9_source_materials()
    assert tuple(replay_desc.supported_modes) == ("pit_replay",)
    assert tuple(live_desc.supported_modes) == ("online",)

    # ⑤ end to end: a pre-floor interval degrades honestly through the replay trace AND
    #    surfaces on the /replay/state + /replay/curves projections (never a silent gap).
    sch, cal, start, end = _three_point_env()
    pre = tuple(
        ArchiveFeedFloor(feed_id=fid, floor_date="2027-01-01",
                         archive_root=f"var/archive/{fid}")
        for fid in ARCHIVE_FEED_IDS
    )
    binds, _ = _bindings(sch, cal, feed_floors=pre)
    state = run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds)
    assert state.completed_points == 3          # degraded, never a run failure
    for badges in binds.intent_ledger.degraded_points.values():
        assert any(b.startswith("archive_pre_floor:") for b in badges)

    from tests.orchestration.test_dual_curves import (
        _alpha_frames, _points, _report_over, _runner,
    )
    from tests.orchestration.test_luozi_wakeup import _env as _wakeup_env, _seed_parked

    runner = _runner(_alpha_frames())
    report = fold_degraded_points_into_report_badges(
        _report_over(runner, _points(runner)), binds.intent_ledger.degraded_points)
    env = _wakeup_env()
    parked = _seed_parked(env, report, resume_after=report.interval_end)
    client = _client(_replay_deps(store=env.store))
    body = client.get("/orchestration/replay/state",
                      params={"experiment_id": parked.experiment_id}).json()
    assert any(b.startswith("archive_pre_floor:") for b in body["badges"]), body["badges"]
    curves = client.get("/orchestration/replay/curves",
                        params={"experiment_id": parked.experiment_id}).json()
    assert curves["report"] is None                       # pre-maturity: never fabricated
    assert any(b.startswith("archive_pre_floor:") for b in curves["badges"])


# =========================================================================== #
# Task-11 carry — the bespoke old-loop-vs-kernel budget isolation criterion      #
# =========================================================================== #
def test_budget_isolation_old_loop_holds_no_kernel_reservation():
    """spec §12.8 预算隔离 — the ``budget-isolation`` retirement criterion's OWN first clause.

    The reviewed criterion reads: "the legacy loop holds no kernel reservation at all;
    a kernel run's reservations settle exactly once against the ledger; an unapproved
    kernel plan leaks none". Nothing in the previously-named suites touched
    ``guanlan_v2/research/loop.py``, so the first clause was clearable without ever
    being checked. This is that check (Task-11 review carry).
    """
    loop_path = REPO_ROOT / "guanlan_v2" / "research" / "loop.py"
    source = loop_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ① the legacy loop imports NOTHING from the orchestration kernel — it cannot hold
    #    a kernel reservation because it cannot reach the ledger at all.
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(m.startswith("guanlan_v2.orchestration") for m in imported), imported
    for token in ("BudgetLedger", "reserve_plan", "reserve_node", "RunBudget",
                  "BudgetRequest", "settle"):
        assert token not in source, f"the legacy research loop references {token!r}"

    # ② a KERNEL run's reservations are held on the kernel ledger only, and each is
    #    settled exactly once (never twice, never against a second pool).
    from tests.orchestration.test_adapters_api import _SeatsSeamSpy

    sch, cal, start, end = _three_point_env()
    binds, sink = _bindings(sch, cal, seats_seam=_SeatsSeamSpy())
    run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
        is_live_session=lambda point: True)
    reservations = binds.budget.replay().reservations
    plans = [r for r in reservations.values() if r.scope_type == "plan"]
    assert len(plans) == 1, "a kernel interval holds exactly ONE plan reservation"
    settled = [r for r in reservations.values() if r.scope_id.startswith("llm.dec.trader#")]
    assert len(settled) == 3 and all(r.status == "settled" for r in settled)
    settle_events = [e for e in sink.budget_events() if e.command.operation == "settle"]
    assert len(settle_events) == len({e.command.idempotency_key for e in settle_events})

    # ③ an unapproved kernel plan leaks NO reservation at all.
    coord = _FakeCoordinator()
    unapproved, unapproved_sink = _bindings(sch, cal, coordinator=coord)
    with pytest.raises(ReplayApprovalRefused):
        run_interval_replay(
            request=_request(sch, approval=ApprovalPolicy.AUTO), schedule=sch,
            execution_config=_exec_config(sch), interval_start=start,
            interval_end=end, bindings=unapproved)
    assert unapproved.budget.replay().reservations == {}
    assert unapproved_sink.budget_events() == ()


# =========================================================================== #
# Offline discipline — this suite never reaches a server / vendor / G:\stocks    #
# =========================================================================== #
def test_this_suite_is_offline_by_construction():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned = {"httpx", "requests", "aiohttp", "urllib", "socket", "akshare", "tushare"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned, node.module
    # no network endpoint, no live-server host and no G: drive path is reachable from
    # this module's own literals (the FastAPI TestClient drives the router in-process).
    # This checker's OWN needle literals are excluded — it is scanning the rest.
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef)
                and node.name == "test_this_suite_is_offline_by_construction"):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                low = child.value.lower()
                assert "://" not in low, child.value
                assert "localhost" not in low, child.value
                assert not low.startswith("g:"), child.value
