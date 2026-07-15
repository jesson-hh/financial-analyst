# -*- coding: utf-8 -*-
"""Task 10 — pure, catalog-free ``validate_plan_structure`` graph invariants.

Written test-first (RED before ``spec.py`` exists). Locks the structural
rejections from ``task-10-brief.md`` that require only the ``PlanDraft`` itself
(no catalog / registry / context resolution):

* duplicate node ids; dependency refs to missing nodes; duplicate / missing
  sinks; cycles; non-auxiliary nodes that cannot reach a sink;
* bootstrap-with-context / main-without-context;
* debate all-or-none identity, undefined-debate reference, out-of-range round,
  seats/turn-order mismatch;
* incoherent reducer producers/slots and same-slot multi-write without a reducer;
* ``artifact_slot`` must equal the upstream node's ``writes_slot``;
* ``Dependency(policy=BLOCK)`` accepts exactly ``{COMPLETED}`` and success-only
  statuses; negative budget / zero concurrency; frozen mutation rejection.

Run from repo root: ``pytest tests/orchestration/test_plan_structure.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    DependencyPolicy,
    NodeStatus,
    PlanSource,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.spec import (
    DebateCfg,
    Dependency,
    GateCfg,
    PlanDraft,
    PlanNode,
    PlanStructureError,
    ReducerCfg,
    validate_plan_structure,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64


def _dt() -> datetime:
    return datetime(2026, 7, 15, 1, 30, tzinfo=UTC)


def _cref(id: str = "cond.a", *, version: str = "1") -> ContentRef:
    return ContentRef(id=id, version=version, content_digest=DA)


def _main_ref(content_digest: str = DC) -> PayloadRef:
    return PayloadRef(namespace="main", object_id="ctx-obj", content_digest=content_digest)


def _node(
    id: str,
    *,
    worker_id: str = "w.a",
    writes_slot: str | None = None,
    deps=(),
    gate_ids=(),
    condition_ref: ContentRef | None = None,
    auxiliary: bool = False,
    debate=None,
    params=None,
) -> PlanNode:
    kw: dict = {}
    if debate is not None:
        kw.update(
            debate_id=debate[0],
            round_role=debate[1],
            debate_round=debate[2],
            debate_turn=debate[3],
        )
    if params is not None:
        kw["params"] = params
    return PlanNode(
        id=id,
        worker_id=worker_id,
        writes_slot=writes_slot if writes_slot is not None else f"{id}.slot",
        dependencies=tuple(deps),
        gate_ids=tuple(gate_ids),
        condition_ref=condition_ref,
        auxiliary=auxiliary,
        **kw,
    )


def _dep(
    upstream: str,
    slot: str,
    *,
    inject_as: str = "feed",
    policy: DependencyPolicy = DependencyPolicy.BLOCK,
    accept=None,
) -> Dependency:
    kw: dict = {}
    if accept is not None:
        kw["accept_statuses"] = accept
    return Dependency(
        upstream_node_id=upstream,
        artifact_slot=slot,
        inject_as=inject_as,
        policy=policy,
        **kw,
    )


def _draft(
    nodes,
    sinks,
    *,
    phase: str = "bootstrap",
    context_ref: PayloadRef | None = None,
    source: PlanSource = PlanSource.BOOTSTRAP,
    debates=(),
    gates=(),
    reducers=(),
    **over,
) -> PlanDraft:
    base = dict(
        id="plan.x",
        run_id="run-1",
        request_id="req-1",
        phase=phase,
        source=source,
        goal="g",
        as_of=_dt(),
        mode=DataMode.ONLINE,
        context_snapshot_ref=context_ref,
        universe=(),
        nodes=tuple(nodes),
        sink_node_ids=tuple(sinks),
        debates=tuple(debates),
        gates=tuple(gates),
        reducers=tuple(reducers),
        catalog_version="v",
        catalog_digest=DA,
        schema_registry_digest=DB,
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    base.update(over)
    return PlanDraft(**base)


# --------------------------------------------------------------------------- #
# happy paths                                                                 #
# --------------------------------------------------------------------------- #
def test_valid_bootstrap_draft_builds():
    d = _draft((_node("na", writes_slot="sa"),), ("na",))
    assert d.phase == "bootstrap" and d.context_snapshot_ref is None
    validate_plan_structure(d)  # no raise


def test_valid_main_draft_builds():
    d = _draft(
        (_node("na", writes_slot="sa"),),
        ("na",),
        phase="main",
        source=PlanSource.DYNAMIC,
        context_ref=_main_ref(),
    )
    assert d.phase == "main"
    validate_plan_structure(d)


def test_valid_two_node_chain_reaches_sink():
    up = _node("up", writes_slot="su")
    sink = _node("sink", writes_slot="ss", deps=(_dep("up", "su"),))
    d = _draft((up, sink), ("sink",))
    validate_plan_structure(d)


# --------------------------------------------------------------------------- #
# node / sink / dependency graph                                              #
# --------------------------------------------------------------------------- #
def test_duplicate_node_id_rejected():
    n1 = _node("na", writes_slot="s1")
    n2 = _node("na", writes_slot="s2")
    with pytest.raises(ValidationError):
        _draft((n1, n2), ("na",))


def test_dependency_ref_to_missing_node_rejected():
    sink = _node("sink", writes_slot="ss", deps=(_dep("ghost", "sg"),))
    with pytest.raises(ValidationError):
        _draft((sink,), ("sink",))


def test_duplicate_sink_rejected():
    with pytest.raises(ValidationError):
        _draft((_node("na", writes_slot="sa"),), ("na", "na"))


def test_missing_sink_reference_rejected():
    with pytest.raises(ValidationError):
        _draft((_node("na", writes_slot="sa"),), ("nope",))


def test_empty_sinks_rejected():
    with pytest.raises(ValidationError):
        _draft((_node("na", writes_slot="sa"),), ())


def test_cycle_rejected():
    na = _node("na", writes_slot="sa", deps=(_dep("nb", "sb"),))
    nb = _node("nb", writes_slot="sb", deps=(_dep("na", "sa"),))
    with pytest.raises(ValidationError):
        _draft((na, nb), ("na",))


def test_self_dependency_is_a_cycle():
    na = _node("na", writes_slot="sa", deps=(_dep("na", "sa"),))
    with pytest.raises(ValidationError):
        _draft((na,), ("na",))


def test_unreachable_non_auxiliary_node_rejected():
    up = _node("up", writes_slot="su")
    sink = _node("sink", writes_slot="ss", deps=(_dep("up", "su"),))
    orphan = _node("orphan", writes_slot="so")  # feeds nobody, not a sink
    with pytest.raises(ValidationError):
        _draft((up, sink, orphan), ("sink",))


def test_auxiliary_node_exempt_from_reachability():
    up = _node("up", writes_slot="su")
    sink = _node("sink", writes_slot="ss", deps=(_dep("up", "su"),))
    aux = _node("aux", writes_slot="sx", auxiliary=True)
    d = _draft((up, sink, aux), ("sink",))
    validate_plan_structure(d)


def test_artifact_slot_must_equal_upstream_writes_slot():
    up = _node("up", writes_slot="su")
    sink = _node("sink", writes_slot="ss", deps=(_dep("up", "wrongslot"),))
    with pytest.raises(ValidationError):
        _draft((up, sink), ("sink",))


# --------------------------------------------------------------------------- #
# dependency accept-status matrix                                             #
# --------------------------------------------------------------------------- #
def test_block_dependency_accepts_exactly_completed():
    ok = _dep("up", "su", policy=DependencyPolicy.BLOCK)
    assert ok.accept_statuses == frozenset({NodeStatus.COMPLETED})


def test_block_dependency_cannot_widen_accept_statuses():
    with pytest.raises(ValidationError):
        _dep(
            "up",
            "su",
            policy=DependencyPolicy.BLOCK,
            accept=frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}),
        )


def test_accept_statuses_cannot_include_failure_status():
    with pytest.raises(ValidationError):
        _dep(
            "up",
            "su",
            policy=DependencyPolicy.DEGRADE,
            accept=frozenset({NodeStatus.COMPLETED, NodeStatus.FAILED}),
        )


def test_degrade_dependency_may_accept_degraded():
    dep = _dep(
        "up",
        "su",
        policy=DependencyPolicy.DEGRADE,
        accept=frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}),
    )
    assert NodeStatus.DEGRADED in dep.accept_statuses


def test_accept_statuses_must_be_non_empty():
    with pytest.raises(ValidationError):
        _dep("up", "su", policy=DependencyPolicy.SKIP, accept=frozenset())


# --------------------------------------------------------------------------- #
# debate invariants                                                           #
# --------------------------------------------------------------------------- #
def test_debate_identity_all_or_none():
    with pytest.raises(ValidationError):
        PlanNode(id="na", worker_id="w.a", writes_slot="sa", debate_id="deb.a")


def test_debate_cfg_seats_turn_order_must_match():
    with pytest.raises(ValidationError):
        DebateCfg(
            id="deb.a",
            seats=("bull", "bear"),
            turn_order=("bull", "referee"),
            max_rounds=2,
            judge_node_id="judge",
        )


def test_debate_cfg_seats_must_be_unique():
    with pytest.raises(ValidationError):
        DebateCfg(
            id="deb.a",
            seats=("bull", "bull"),
            turn_order=("bull", "bull"),
            max_rounds=2,
            judge_node_id="judge",
        )


def test_debate_node_references_undefined_debate_rejected():
    judge = _node("judge", writes_slot="sj", deps=(_dep("seat", "ss"),))
    seat = _node(
        "seat",
        writes_slot="ss",
        debate=("deb.missing", "bull", 1, 1),
    )
    with pytest.raises(ValidationError):
        _draft((judge, seat), ("judge",))


def test_debate_node_round_out_of_range_rejected():
    debate = DebateCfg(
        id="deb.a",
        seats=("bull", "bear"),
        turn_order=("bull", "bear"),
        max_rounds=2,
        judge_node_id="judge",
    )
    judge = _node("judge", writes_slot="sj", deps=(_dep("seat", "ss"),))
    seat = _node("seat", writes_slot="ss", debate=("deb.a", "bull", 3, 1))
    with pytest.raises(ValidationError):
        _draft((judge, seat), ("judge",), debates=(debate,))


def test_debate_node_role_not_a_seat_rejected():
    debate = DebateCfg(
        id="deb.a",
        seats=("bull", "bear"),
        turn_order=("bull", "bear"),
        max_rounds=2,
        judge_node_id="judge",
    )
    judge = _node("judge", writes_slot="sj", deps=(_dep("seat", "ss"),))
    seat = _node("seat", writes_slot="ss", debate=("deb.a", "referee", 1, 1))
    with pytest.raises(ValidationError):
        _draft((judge, seat), ("judge",), debates=(debate,))


# --------------------------------------------------------------------------- #
# reducer / multi-write invariants                                            #
# --------------------------------------------------------------------------- #
def test_reducer_producers_must_be_non_empty():
    with pytest.raises(ValidationError):
        ReducerCfg(
            id="red.a",
            slot="shared",
            reducer_ref=_cref("reducer.a"),
            producer_node_ids=(),
            output_schema_ref=SchemaRef(name="OutModel", version="1"),
        )


def test_same_slot_multi_write_without_reducer_rejected():
    a = _node("wa", writes_slot="shared")
    b = _node("wb", writes_slot="shared")
    sink = _node(
        "sink",
        writes_slot="ss",
        deps=(_dep("wa", "shared", inject_as="fa"), _dep("wb", "shared", inject_as="fb")),
    )
    with pytest.raises(ValidationError):
        _draft((a, b, sink), ("sink",))


def test_same_slot_multi_write_with_reducer_ok():
    a = _node("wa", writes_slot="shared")
    b = _node("wb", writes_slot="shared")
    sink = _node(
        "sink",
        writes_slot="ss",
        deps=(_dep("wa", "shared", inject_as="fa"), _dep("wb", "shared", inject_as="fb")),
    )
    red = ReducerCfg(
        id="red.a",
        slot="shared",
        reducer_ref=_cref("reducer.a"),
        producer_node_ids=("wa", "wb"),
        output_schema_ref=SchemaRef(name="OutModel", version="1"),
    )
    d = _draft((a, b, sink), ("sink",), reducers=(red,))
    validate_plan_structure(d)


def test_reducer_producers_must_match_slot_writers():
    a = _node("wa", writes_slot="shared")
    b = _node("wb", writes_slot="shared")
    sink = _node(
        "sink",
        writes_slot="ss",
        deps=(_dep("wa", "shared", inject_as="fa"), _dep("wb", "shared", inject_as="fb")),
    )
    # reducer omits wb -> incoherent producer set for a two-writer slot
    red = ReducerCfg(
        id="red.a",
        slot="shared",
        reducer_ref=_cref("reducer.a"),
        producer_node_ids=("wa",),
        output_schema_ref=SchemaRef(name="OutModel", version="1"),
    )
    with pytest.raises(ValidationError):
        _draft((a, b, sink), ("sink",), reducers=(red,))


def test_gate_id_must_reference_declared_gate():
    n = _node("na", writes_slot="sa", gate_ids=("gate.missing",))
    with pytest.raises(ValidationError):
        _draft((n,), ("na",))


def test_declared_gate_id_reference_ok():
    gate = GateCfg(
        id="gate.a",
        metric=_cref("gate.metric"),
        operator=">=",
        threshold=0.5,
        scope="csi300",
    )
    n = _node("na", writes_slot="sa", gate_ids=("gate.a",))
    d = _draft((n,), ("na",), gates=(gate,))
    validate_plan_structure(d)


# --------------------------------------------------------------------------- #
# phase / context / legacy tuple / bounds                                     #
# --------------------------------------------------------------------------- #
def test_bootstrap_with_context_ref_rejected():
    with pytest.raises(ValidationError):
        _draft(
            (_node("na", writes_slot="sa"),),
            ("na",),
            phase="bootstrap",
            context_ref=_main_ref(),
        )


def test_main_without_context_ref_rejected():
    with pytest.raises(ValidationError):
        _draft(
            (_node("na", writes_slot="sa"),),
            ("na",),
            phase="main",
            source=PlanSource.DYNAMIC,
            context_ref=None,
        )


def test_main_draft_context_ref_must_use_main_namespace():
    # a sealed/review payload ref is a valid PayloadRef, but a main plan must
    # bind its context in the public "main" namespace.
    sealed_ref = PayloadRef(namespace="sealed", object_id="o", content_digest=DC)
    with pytest.raises(ValidationError):
        _draft(
            (_node("na", writes_slot="sa"),),
            ("na",),
            phase="main",
            source=PlanSource.DYNAMIC,
            context_ref=sealed_ref,
        )


def test_legacy_tuple_all_or_none_rejected():
    with pytest.raises(ValidationError):
        _draft(
            (_node("na", writes_slot="sa"),),
            ("na",),
            legacy_source_schema=SchemaRef(name="LegacyCfg", version="1"),
            # config + mapping digests missing -> partial tuple
        )


def test_negative_budget_rejected():
    with pytest.raises(ValidationError):
        _draft((_node("na", writes_slot="sa"),), ("na",), budget_request_tokens=-1)


def test_zero_concurrency_rejected():
    with pytest.raises(ValidationError):
        _draft((_node("na", writes_slot="sa"),), ("na",), max_concurrency=0)


def test_zero_timeout_rejected():
    with pytest.raises(ValidationError):
        PlanNode(id="na", worker_id="w.a", writes_slot="sa", timeout_sec=0)


# --------------------------------------------------------------------------- #
# immutability + pure-function surface                                        #
# --------------------------------------------------------------------------- #
def test_plandraft_is_frozen():
    d = _draft((_node("na", writes_slot="sa"),), ("na",))
    with pytest.raises(ValidationError):
        d.goal = "changed"


def test_plannode_is_frozen():
    n = _node("na", writes_slot="sa")
    with pytest.raises(ValidationError):
        n.worker_id = "w.b"


def test_validate_plan_structure_raises_on_model_copy_corruption():
    # model_copy bypasses validation; the pure function still catches the cycle.
    good = _draft((_node("na", writes_slot="sa"),), ("na",))
    na = _node("na", writes_slot="sa", deps=(_dep("nb", "sb"),))
    nb = _node("nb", writes_slot="sb", deps=(_dep("na", "sa"),))
    corrupt = good.model_copy(update={"nodes": (na, nb), "sink_node_ids": ("na",)})
    with pytest.raises(PlanStructureError):
        validate_plan_structure(corrupt)
