# -*- coding: utf-8 -*-
"""Task 2 (Phase 8) — honesty spine ``honesty.py``.

Written test-first: with ``guanlan_v2/orchestration/honesty.py`` absent the
module fails to import (RED). It then locks, GREEN, the red-line-sensitive
classifier / number-provenance scan / attribution hook:

* ``classify_worker`` closed rule matrix — 编数 (fabrication) / EvidencePolicy
  violation / 空产出 (empty output) ⇒ incomplete; REQUIRED-tools-zero-calls ⇒
  incomplete; a LEGAL no-tool worker (FORBIDDEN policy, zero calls, clean
  output) is **never** killed (classifies ``ok``); unsourced numbers ⇒
  ``[UNSOURCED]`` badge and (when ``allow_unsourced_numbers=False``) incomplete;
  degradation is always badged.
* ``scan_unsourced_numbers`` — anchor↔leaf reconciliation over the payload's
  semantic canonical JSON (anchor_path_unresolved / fabricated_number /
  unsourced_number), order-independent and pure, with the documented
  load-bearing-number definition and its schema_version / ``*_digest`` exemptions.
* ``attribution_candidates`` — deterministic, node_id-ordered, verdict≠ok subset
  (the frozen attribution hook surface, spec §6.3, closed issue-code vocabulary).

Run from repo root: ``pytest tests/orchestration/test_honesty.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    WorkerSpec,
)
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
)
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    NodeStatus,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schemas import (
    Artifact,
    ArtifactRef,
    NodeRun,
    NumberAnchor,
    Provenance,
    ToolCallRecord,
)

# --- the module under test (absent ⇒ RED) ---------------------------------- #
from guanlan_v2.orchestration.honesty import (
    DEGRADED_BADGE,
    UNSOURCED_BADGE,
    HonestyIssue,
    HonestyReport,
    attribution_candidates,
    classify_worker,
    scan_unsourced_numbers,
)

UTC = timezone.utc

DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
DE = "e" * 64
DF = "f" * 64


def _dt(hour: int = 9, minute: int = 30) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Concrete typed payloads                                                     #
# --------------------------------------------------------------------------- #
class _Metrics(DigestModel):
    schema_version: Literal["1"] = "1"
    excess: FiniteFloat


class _NestedPayload(DigestModel):
    """One numeric leaf at ``metrics.excess`` (plus two non-numeric leaves)."""

    schema_version: Literal["1"] = "1"
    name: NonEmptyStr
    metrics: _Metrics


class _ScorePayload(DigestModel):
    """A single numeric leaf at ``score``."""

    schema_version: Literal["1"] = "1"
    ticker: NonEmptyStr
    score: FiniteFloat


class _Row(DigestModel):
    schema_version: Literal["1"] = "1"
    ic: FiniteFloat


class _RowsPayload(DigestModel):
    """A tuple leaf so a list-index path ``rows.0.ic`` is exercised."""

    schema_version: Literal["1"] = "1"
    rows: tuple[_Row, ...]


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
_PROMPT = ContentRef(id="text.sentiment.prompt", version="1", content_digest=DB)
_HANDLER = ContentRef(id="handler.x.number-critic", version="1", content_digest=DC)
_CAP = CapabilityRef(id="news.search", version="1", content_digest=DC)


def _worker(
    *,
    worker_id: str = "text.sentiment",
    tool_calls: ToolCallRequirement = ToolCallRequirement.OPTIONAL,
    require_input_refs: bool = True,
    require_number_anchors: bool = True,
    allow_unsourced_numbers: bool = False,
    optional_data_may_degrade: bool = True,
    inputs: tuple[InputBinding, ...] = (),
    deterministic: bool = False,
) -> WorkerSpec:
    policy = EvidencePolicy(
        tool_calls=tool_calls,
        require_input_refs=require_input_refs,
        require_number_anchors=require_number_anchors,
        allow_unsourced_numbers=allow_unsourced_numbers,
        optional_data_may_degrade=optional_data_may_degrade,
    )
    allowlist = () if tool_calls is ToolCallRequirement.FORBIDDEN else (_CAP,)
    if deterministic:
        execution = ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=_HANDLER)
        prompt_ref = None
    else:
        execution = ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner", thinking_budget=0)
        prompt_ref = _PROMPT
    return WorkerSpec(
        id=worker_id,
        catalog_role="final",
        selection_scope="dynamic_allowed",
        lane="text",
        persona="Sentiment reader",
        tier=Tier.WRITER,
        execution=execution,
        system_prompt_ref=prompt_ref,
        capability_allowlist=tuple(allowlist),
        inputs=tuple(inputs),
        outputs=(OutputBinding(name="primary", schema_ref=SchemaRef(name="ScorePayload", version="1")),),
        evidence_policy=policy,
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False,
        decision_authority="none",
    )


def _dep_input(*, required: bool = True) -> InputBinding:
    return InputBinding(
        name="upstream",
        schema_ref=SchemaRef(name="DemoPayload", version="1"),
        required=required,
        cardinality="one",
    )


def _typed_ref(*, name: str = "ToolResult", object_id: str = "obj-1", content: str = DE) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version="1"),
        payload_ref=PayloadRef(namespace="main", object_id=object_id, content_digest=content),
    )


def _tool_call(*, call_ordinal: int = 1) -> ToolCallRecord:
    return ToolCallRecord(
        call_ordinal=call_ordinal,
        tool_ref=_CAP,
        request_ref=_typed_ref(name="ToolRequest", object_id="req", content=DD),
        result_ref=_typed_ref(name="ToolResult", object_id="res", content=DE),
        call_id=f"call-{call_ordinal}",
        started_at=_dt(9, 0),
        finished_at=_dt(9, 1),
    )


def _node_run(
    *,
    status: NodeStatus = NodeStatus.COMPLETED,
    node_id: str = "sentiment",
    worker_id: str = "text.sentiment",
    tool_calls: tuple[ToolCallRecord, ...] = (),
) -> NodeRun:
    kwargs: dict = dict(
        node_run_id="nr-1",
        run_id="run-1",
        plan_id="plan-1",
        plan_digest=DA,
        node_id=node_id,
        worker_id=worker_id,
        status=status,
        attempt_id="att-1",
        input_snapshot_digest="1" * 64,
        tool_call_records=tuple(tool_calls),
    )
    if status is NodeStatus.COMPLETED:
        kwargs["output_keys"] = ("primary",)
        kwargs["output_artifact_ids"] = ("art-1",)
    return NodeRun(**kwargs)


def _provenance() -> Provenance:
    return Provenance(
        plan_digest=DA,
        code_version="git:abc123",
        as_of=_dt(15, 0),
        pit_mode=DataMode.ONLINE,
    )


def _input_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="in-1",
        schema_ref=SchemaRef(name="DemoPayload", version="1"),
        producer_node_id="node.market",
        slot="market",
        output_key="factor",
        content_digest="3" * 64,
        relation="input",
    )


def _artifact(
    *,
    payload=None,
    numbers: tuple[NumberAnchor, ...] = (),
    input_refs: tuple[ArtifactRef, ...] = (),
    kind: str = "sentiment_report",
) -> Artifact:
    return Artifact.build(
        artifact_id="art-1",
        run_id="run-1",
        created_at=_dt(16, 0),
        producer_node_id="node.sentiment",
        slot="sentiment",
        output_key="primary",
        kind=kind,
        payload_schema_ref=SchemaRef(name="ScorePayload", version="1"),
        payload=payload if payload is not None else _ScorePayload(ticker="600519.SH", score=0.74),
        rendered_md="# Report",
        input_refs=tuple(input_refs),
        provenance=_provenance(),
        numbers=tuple(numbers),
        badges=(),
    )


def _sourced_anchor(*, label="score", value=0.74, path="score") -> NumberAnchor:
    return NumberAnchor(
        label=label, value=value, payload_path=path, source_artifact_id="in-1", is_unsourced=False
    )


def _unsourced_anchor(*, label="score", value=0.74, path="score") -> NumberAnchor:
    return NumberAnchor(label=label, value=value, payload_path=path, is_unsourced=True)


# =========================================================================== #
# scan_unsourced_numbers                                                       #
# =========================================================================== #
def test_scan_clean_when_every_numeric_leaf_is_sourced_and_matching():
    art = _artifact(numbers=(_sourced_anchor(),))
    assert scan_unsourced_numbers(art) == ()


def test_scan_flags_unsourced_anchor():
    art = _artifact(numbers=(_unsourced_anchor(),))
    issues = scan_unsourced_numbers(art)
    assert [i.code for i in issues] == ["unsourced_number"]
    assert issues[0].pointer == "score"
    assert issues[0].number_label == "score"


def test_scan_flags_uncovered_load_bearing_number():
    # A numeric leaf with NO anchor at all ⇒ load-bearing but unsourced.
    art = _artifact(numbers=())
    issues = scan_unsourced_numbers(art)
    assert [i.code for i in issues] == ["unsourced_number"]
    assert issues[0].pointer == "score"


def test_scan_flags_fabricated_number_on_value_mismatch():
    # anchor claims 9.99 but the payload leaf is 0.74 ⇒ 编数.
    art = _artifact(numbers=(_sourced_anchor(value=9.99),))
    issues = scan_unsourced_numbers(art)
    assert [i.code for i in issues] == ["fabricated_number"]
    assert issues[0].pointer == "score"


def test_scan_flags_anchor_path_unresolved():
    # cover the real ``score`` leaf so only the ghost-path anchor is flagged.
    art = _artifact(
        numbers=(
            _sourced_anchor(),
            NumberAnchor(label="ghost", value=1.0, payload_path="does.not.exist",
                        source_artifact_id="in-1", is_unsourced=False),
        )
    )
    issues = scan_unsourced_numbers(art)
    assert [i.code for i in issues] == ["anchor_path_unresolved"]
    assert issues[0].number_label == "ghost"


def test_scan_resolves_nested_and_list_index_paths():
    nested = _NestedPayload(name="x", metrics=_Metrics(excess=1.5))
    art = _artifact(
        payload=nested,
        numbers=(
            NumberAnchor(
                label="excess", value=1.5, payload_path="metrics.excess",
                source_artifact_id="in-1", is_unsourced=False,
            ),
        ),
    )
    assert scan_unsourced_numbers(art) == ()

    rows = _RowsPayload(rows=(_Row(ic=0.2),))
    art2 = _artifact(
        payload=rows,
        numbers=(
            NumberAnchor(
                label="ic", value=0.2, payload_path="rows.0.ic",
                source_artifact_id="in-1", is_unsourced=False,
            ),
        ),
    )
    assert scan_unsourced_numbers(art2) == ()


def test_scan_exempts_schema_version_and_digest_named_leaves():
    # A dict payload with two exempt numeric leaves (schema_version, *_digest)
    # and one real load-bearing number.
    payload = {"schema_version": 3, "row_digest": 7, "real": 5}
    art = _artifact(payload=payload, numbers=())
    issues = scan_unsourced_numbers(art)
    assert [i.code for i in issues] == ["unsourced_number"]
    assert issues[0].pointer == "real"


def test_scan_does_not_flag_booleans_as_numbers():
    payload = {"flag": True, "score": 0.5}
    art = _artifact(
        payload=payload,
        numbers=(NumberAnchor(label="score", value=0.5, payload_path="score",
                              source_artifact_id="in-1", is_unsourced=False),),
    )
    # only ``score`` is a number; the bool ``flag`` must not be flagged.
    assert scan_unsourced_numbers(art) == ()


def test_scan_is_order_independent_and_pure():
    a1 = _unsourced_anchor(label="a", value=0.74, path="score")
    a2 = NumberAnchor(label="b", value=1.0, payload_path="ghost", is_unsourced=True)
    art_fwd = _artifact(numbers=(a1, a2))
    art_rev = _artifact(numbers=(a2, a1))
    out_fwd = scan_unsourced_numbers(art_fwd)
    out_rev = scan_unsourced_numbers(art_rev)
    assert out_fwd == out_rev            # order-independent
    assert scan_unsourced_numbers(art_fwd) == out_fwd   # pure / repeatable


# =========================================================================== #
# classify_worker — rule matrix                                               #
# =========================================================================== #
def test_rule1_empty_output_none_artifact_is_incomplete():
    rep = classify_worker(
        worker=_worker(),
        node_run=_node_run(status=NodeStatus.COMPLETED),
        artifact=None,
    )
    assert rep.verdict == "incomplete"
    assert "empty_output" in {i.code for i in rep.issues}
    assert rep.subject_content_digest is None


def test_rule1_empty_primary_payload_is_incomplete():
    art = _artifact(payload={}, numbers=())
    rep = classify_worker(worker=_worker(), node_run=_node_run(), artifact=art)
    assert rep.verdict == "incomplete"
    assert "empty_output" in {i.code for i in rep.issues}


def test_rule2_required_tools_zero_calls_is_incomplete():
    worker = _worker(tool_calls=ToolCallRequirement.REQUIRED)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(tool_calls=()),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "incomplete"
    assert "required_tools_zero_calls" in {i.code for i in rep.issues}


def test_rule2_forbidden_tools_called_is_incomplete():
    worker = _worker(tool_calls=ToolCallRequirement.FORBIDDEN)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(tool_calls=(_tool_call(),)),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "incomplete"
    assert "forbidden_tools_called" in {i.code for i in rep.issues}


def test_rule2_optional_zero_calls_is_never_an_issue():
    rep = classify_worker(
        worker=_worker(tool_calls=ToolCallRequirement.OPTIONAL),
        node_run=_node_run(tool_calls=()),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "ok"
    assert rep.issues == ()


def test_rule2_forbidden_zero_calls_is_never_an_issue():
    rep = classify_worker(
        worker=_worker(tool_calls=ToolCallRequirement.FORBIDDEN),
        node_run=_node_run(tool_calls=()),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "ok"


def test_legal_no_tool_worker_is_never_killed():
    """FORBIDDEN policy + zero calls + clean output ⇒ COMPLETE (ok)."""
    worker = _worker(tool_calls=ToolCallRequirement.FORBIDDEN, inputs=())
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(status=NodeStatus.COMPLETED, tool_calls=()),
        artifact=_artifact(numbers=(_sourced_anchor(),), input_refs=()),
    )
    assert rep.verdict == "ok"
    assert rep.issues == ()
    assert rep.badges == ()


def test_rule3_missing_input_refs_when_worker_has_required_dependency():
    worker = _worker(require_input_refs=True, inputs=(_dep_input(required=True),))
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_sourced_anchor(),), input_refs=()),
    )
    assert rep.verdict == "incomplete"
    assert "missing_input_refs" in {i.code for i in rep.issues}


def test_rule3_present_input_refs_satisfies_dependency():
    worker = _worker(require_input_refs=True, inputs=(_dep_input(required=True),))
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_sourced_anchor(),), input_refs=(_input_ref(),)),
    )
    assert rep.verdict == "ok"


def test_rule3_optional_only_dependency_does_not_fire_missing_input_refs():
    """A worker with only OPTIONAL inputs is not a required dependency ⇒ no kill."""
    worker = _worker(require_input_refs=True, inputs=(_dep_input(required=False),))
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_sourced_anchor(),), input_refs=()),
    )
    assert rep.verdict == "ok"
    assert "missing_input_refs" not in {i.code for i in rep.issues}


def test_rule4_unsourced_number_disallowed_is_incomplete():
    worker = _worker(allow_unsourced_numbers=False)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_unsourced_anchor(),)),
    )
    assert rep.verdict == "incomplete"
    assert "unsourced_number" in {i.code for i in rep.issues}
    assert UNSOURCED_BADGE in rep.badges


def test_rule4_unsourced_number_allowed_is_badged_not_incomplete():
    worker = _worker(allow_unsourced_numbers=True)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_unsourced_anchor(),)),
    )
    assert rep.verdict == "ok"
    assert "unsourced_number" in {i.code for i in rep.issues}
    assert UNSOURCED_BADGE in rep.badges


def test_rule4_fabricated_number_is_incomplete_even_when_unsourced_allowed():
    """编数 (fabrication) is a hard integrity failure regardless of the flag."""
    worker = _worker(allow_unsourced_numbers=True)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(),
        artifact=_artifact(numbers=(_sourced_anchor(value=9.99),)),
    )
    assert rep.verdict == "incomplete"
    assert "fabricated_number" in {i.code for i in rep.issues}


def test_rule5_degraded_status_is_degraded_and_badged():
    rep = classify_worker(
        worker=_worker(optional_data_may_degrade=True),
        node_run=_node_run(status=NodeStatus.DEGRADED),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "degraded"
    assert "degraded_input" in {i.code for i in rep.issues}
    assert DEGRADED_BADGE in rep.badges


def test_rule6_otherwise_ok():
    rep = classify_worker(
        worker=_worker(),
        node_run=_node_run(),
        artifact=_artifact(numbers=(_sourced_anchor(),)),
    )
    assert rep.verdict == "ok"
    assert rep.issues == ()
    assert rep.badges == ()


def test_incomplete_dominates_degraded():
    rep = classify_worker(
        worker=_worker(),
        node_run=_node_run(status=NodeStatus.DEGRADED),
        artifact=None,
    )
    assert rep.verdict == "incomplete"          # empty_output beats degraded
    assert DEGRADED_BADGE in rep.badges         # degradation still badged


def test_classify_subject_digest_and_identity():
    art = _artifact(numbers=(_sourced_anchor(),))
    rep = classify_worker(worker=_worker(worker_id="text.sentiment"),
                          node_run=_node_run(node_id="sentiment"), artifact=art)
    assert rep.worker_id == "text.sentiment"
    assert rep.node_id == "sentiment"
    assert rep.subject_content_digest == art.content_digest


def test_classify_is_deterministic_double_run_digest_equal():
    art = _artifact(numbers=(_unsourced_anchor(), _sourced_anchor(value=9.99, path="score")))
    kw = dict(worker=_worker(), node_run=_node_run(), artifact=art)
    r1 = classify_worker(**kw)
    r2 = classify_worker(**kw)
    assert r1 == r2
    assert r1.semantic_digest() == r2.semantic_digest()


def test_classify_badges_are_deduped_and_stable():
    # unsourced + degraded together ⇒ both badges, deduped, stable order.
    worker = _worker(allow_unsourced_numbers=True, optional_data_may_degrade=True)
    rep = classify_worker(
        worker=worker,
        node_run=_node_run(status=NodeStatus.DEGRADED),
        artifact=_artifact(numbers=(_unsourced_anchor(),)),
    )
    assert sorted(set(rep.badges)) == list(rep.badges)   # deduped + sorted/stable
    assert UNSOURCED_BADGE in rep.badges
    assert DEGRADED_BADGE in rep.badges


# =========================================================================== #
# HonestyIssue / HonestyReport model invariants                               #
# =========================================================================== #
def test_report_ok_rejects_incomplete_class_issue():
    with pytest.raises(ValidationError):
        HonestyReport(
            worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
            verdict="ok",
            issues=(HonestyIssue(code="empty_output", message="nothing produced"),),
        )


def test_report_incomplete_requires_a_justifying_issue():
    with pytest.raises(ValidationError):
        HonestyReport(
            worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
            verdict="incomplete", issues=(),
        )


def test_report_unsourced_number_forces_badge():
    with pytest.raises(ValidationError):
        HonestyReport(
            worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
            verdict="incomplete",
            issues=(HonestyIssue(code="unsourced_number", message="no source", pointer="score"),),
            badges=(),   # missing [UNSOURCED]
        )


def test_report_unsourced_number_with_badge_is_valid():
    rep = HonestyReport(
        worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
        verdict="ok",
        issues=(HonestyIssue(code="unsourced_number", message="no source", pointer="score"),),
        badges=(UNSOURCED_BADGE,),
    )
    assert rep.verdict == "ok"


def test_report_degraded_requires_degraded_input_issue():
    with pytest.raises(ValidationError):
        HonestyReport(
            worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
            verdict="degraded", issues=(), badges=(DEGRADED_BADGE,),
        )


def test_report_degraded_rejects_hard_issue():
    with pytest.raises(ValidationError):
        HonestyReport(
            worker_id="text.sentiment", node_id="sentiment", subject_content_digest=DA,
            verdict="degraded",
            issues=(
                HonestyIssue(code="degraded_input", message="stale"),
                HonestyIssue(code="empty_output", message="nothing"),
            ),
            badges=(DEGRADED_BADGE,),
        )


def test_report_clean_ok_is_valid():
    rep = HonestyReport(
        worker_id="text.sentiment", node_id="sentiment", subject_content_digest=None,
        verdict="ok",
    )
    assert rep.issues == ()
    assert rep.badges == ()
    assert rep.schema_version == "1"


def test_report_is_frozen_digest_model():
    assert issubclass(HonestyReport, DigestModel)
    assert issubclass(HonestyIssue, DigestModel)
    rep = HonestyReport(worker_id="a.b", node_id="c", subject_content_digest=None, verdict="ok")
    with pytest.raises(ValidationError):
        rep.verdict = "incomplete"  # type: ignore[misc]


# =========================================================================== #
# attribution_candidates — spec §6.3 hook surface                             #
# =========================================================================== #
def _report(node_id: str, verdict: str) -> HonestyReport:
    if verdict == "ok":
        issues = ()
        badges = ()
    elif verdict == "degraded":
        issues = (HonestyIssue(code="degraded_input", message="stale"),)
        badges = (DEGRADED_BADGE,)
    else:
        issues = (HonestyIssue(code="empty_output", message="nothing"),)
        badges = ()
    return HonestyReport(
        worker_id="w.x", node_id=node_id, subject_content_digest=None,
        verdict=verdict, issues=issues, badges=badges,
    )


def test_attribution_candidates_selects_non_ok_stable_ordered():
    reports = (
        _report("zeta", "incomplete"),
        _report("alpha", "ok"),
        _report("mike", "degraded"),
        _report("bravo", "ok"),
    )
    cands = attribution_candidates(reports)
    assert [r.node_id for r in cands] == ["mike", "zeta"]   # non-ok, sorted by node_id


def test_attribution_candidates_is_order_independent():
    reports = (
        _report("mike", "degraded"),
        _report("zeta", "incomplete"),
    )
    assert attribution_candidates(reports) == attribution_candidates(tuple(reversed(reports)))


def test_attribution_candidates_empty_when_all_ok():
    reports = (_report("a", "ok"), _report("b", "ok"))
    assert attribution_candidates(reports) == ()
