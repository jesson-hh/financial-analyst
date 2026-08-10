# -*- coding: utf-8 -*-
"""L3 · Task 2 — the rowless experience worker is decided at ADMISSION.

Charter §2.1 item 6 ("晚失败改成早拒绝") measured at HEAD, not assumed. The
charter's fact — a DYNAMIC (planner-sourced) plan carrying a worker that declares
the ``experience_cases`` read category with no reviewed prefetch row passes
admission and then dies LATE at the gateway's closed allowlist door
(``max_capability_invocations=0``, live run ``deep-8eb9afef6e9a5e48``) — PREDATES
campaign commit ``4ee43ca``. That commit extended ruling C3's discrimination to
the experience provider half, so the non-granted worker now freezes with an
honest EMPTY contribution and the gateway is never touched (pinned at
``test_phase10_e2e.py::TestLateFailureLoudness``, this suite's control).

So the residue this task closes is NOT the ruled licensed-EMPTY reader. It is the
one rowless shape that emptiness cannot honestly serve: a worker whose OWN
``evidence_policy.tool_calls`` is ``REQUIRED``. An empty contribution can never
satisfy a REQUIRED tool-call discipline, so such a seat is structurally dead —
and today ``check_runtime_support`` only ever notices it through the *summed*
tool-call arithmetic (``tool_calls_required_unmet``), a cause-blind number that
names neither the bridge nor the missing row and that a sibling bridge's row can
mask. This suite pins the DISCRIMINATION:

* ``tool_calls=REQUIRED`` + category-only activation + no reviewed row
  => refused AT ADMISSION by a typed, named ``rowless_read_category_unrunnable``
  issue that names the worker, the bridge, the read category and the missing row;
* ``tool_calls=FORBIDDEN`` (the sealed ``dec.research_mgr``) and
  ``tool_calls=OPTIONAL`` => the C3 licensed-EMPTY posture is PRESERVED, byte for
  byte: admitted, and provably never reaching the gateway. **Rowless reader is
  not a refusal** — regressing that would regress ruling C3.
* the allowlisted-but-rowless half (a reviewed grant that LOST its row) keeps its
  LOUD ``CatalogError`` — untouched here, pinned by its owners.

The REQUIRED stand-in is a ONE-FIELD copy of the sealed ``dec.research_mgr``
WorkerSpec (only ``evidence_policy.tool_calls`` differs — asserted below, so this
suite cannot fake its own premise) served through the REAL ``CatalogRuntime``,
the REAL production bridge view, the REAL sealed experience descriptor and the
REAL analyzer. No sealed worker declares that shape today (asserted below), which
is why this task moves no digest and reddens no existing pin.

Run: ``python -m pytest tests/orchestration/test_experience_rowless_admission.py -v``
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.catalog import CatalogError
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DependencyPolicy,
    ExecutionKind,
    PlanSource,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.pipeline.assembly import production_bridge_view
from guanlan_v2.orchestration.refs import PayloadRef
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    compute_candidate_plan_digest,
)

# ---- reviewed sibling harness (the Task-8 replay-evidence precedent, the same
# import test_phase10_e2e.py uses) so this suite cannot drift from the per-task
# suites on what "the sealed Phase-9 catalog" means.
from tests.orchestration.test_pipeline_screening import (  # noqa: F401
    NOW,
    _FixedClock,
    env,
    phase9_runtime,
)

#: the typed refusal this task adds.
ROWLESS_CODE = "rowless_read_category_unrunnable"
#: the cause-blind arithmetic refusal that already existed (kept, not replaced).
ARITHMETIC_CODE = "tool_calls_required_unmet"
EXPERIENCE_CATEGORY = "experience_cases"
ROWLESS_WORKER = "dec.research_mgr"


# =========================================================================== #
# harness                                                                      #
# =========================================================================== #
class _WorkerOverrideRuntime:
    """The REAL :class:`CatalogRuntime` with ONE worker swapped.

    The reviewed stand-in idiom (``test_phase8_e2e``'s trimmed catalog): every
    material loader, descriptor, analyzer and digest is the real sealed one; only
    the named WorkerSpec is served from the override map.
    ``test_the_required_stand_in_differs_in_exactly_one_field`` proves the swap is
    a single-field copy, so no test here can invent its own premise.
    """

    __slots__ = ("_inner", "_overrides")

    def __init__(self, inner, overrides) -> None:
        self._inner = inner
        self._overrides = dict(overrides)

    def worker(self, worker_id):
        override = self._overrides.get(worker_id)
        return override if override is not None else self._inner.worker(worker_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _tool_calls_variant(spec, requirement: ToolCallRequirement):
    """``spec`` with ONLY ``evidence_policy.tool_calls`` replaced."""
    return spec.model_copy(update={
        "evidence_policy": spec.evidence_policy.model_copy(
            update={"tool_calls": requirement})})


@pytest.fixture(scope="module")
def bridge_view(phase9_runtime):
    return production_bridge_view(phase9_runtime)


@pytest.fixture(scope="module")
def stores(env):
    resolver = SchemaRegistryResolver()
    resolver.register(env["registry"])
    return RuntimeStores(resolver=resolver, clock=_FixedClock(),
                         allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))


@pytest.fixture(scope="module")
def dynamic_plan(env):
    """A DYNAMIC (planner-shaped) ``text.sentiment -> dec.research_mgr`` draft.

    Deliberately WITHOUT ``pv.technical`` / ``text.news`` — the two honest
    data-grant support refusals L3 Tasks 3-5 grant — so the only thing this plan
    can be refused for is the experience seam under test.
    """
    request = OrchestrationRequest(
        request_id="l3t2-req", goal="观澜 · L3 Task 2 · rowless experience probe",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    context = env["context"]
    nodes = (
        PlanNode.model_validate({
            "id": "sentiment", "worker_id": "text.sentiment",
            "writes_slot": "slot.sentiment"}),
        PlanNode.model_validate({
            "id": "research-mgr", "worker_id": ROWLESS_WORKER,
            "writes_slot": "slot.research_plan",
            "dependencies": ({
                "upstream_node_id": "sentiment",
                "artifact_slot": "slot.sentiment",
                "inject_as": "sentiment",
                "policy": DependencyPolicy.BLOCK},)}),
    )
    draft = PlanDraft(
        id="plan-l3t2", run_id="run-l3t2", request_id=request.request_id,
        phase="main", source=PlanSource.DYNAMIC, goal=request.goal,
        as_of=context.data_context.as_of, mode=context.data_context.mode,
        context_snapshot_ref=env["ctx_ref"], universe=(), nodes=nodes,
        sink_node_ids=("research-mgr",), debates=(), gates=(), reducers=(),
        catalog_version=env["snapshot"].catalog_version,
        catalog_digest=env["snapshot"].catalog_digest,
        schema_registry_digest=env["registry"].registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=2_000_000, budget_request_llm_invocations=2,
        max_concurrency=2, stop_condition_refs=(), legacy_source_schema=None,
        legacy_source_config_digest=None, legacy_mapping_digest=None)
    return SimpleNamespace(request=request, draft=draft)


def _admit(env, stores, runtime, bridge_view, dynamic_plan):
    """Drive the REAL :class:`PlanAdmissionService` and return its preparation."""
    request, draft = dynamic_plan.request, dynamic_plan.draft
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request},
        drafts={draft.id: draft},
        contexts={env["context"].content_digest: env["context"]}, attestations={},
        approvals={}, catalog=runtime, bridge_view=bridge_view,
        phase1_registry=env["registry"],
        runtime_registry_digest=env["registry"].registry_digest,
        profile=STATIC_RUNTIME_PROFILE_V2, stores=stores,
        run_budget=RunBudget(
            ledger_id=f"led-{draft.run_id}",
            max_tokens=draft.budget_request_tokens,
            max_llm_invocations=draft.budget_request_llm_invocations,
            max_concurrency=draft.max_concurrency),
        clock=_FixedClock())
    return service.prepare_candidate(draft.id, request_id=request.request_id)


def _variant_runtime(env, phase9_runtime, requirement):
    sealed = {w.id: w for w in env["snapshot"].workers}[ROWLESS_WORKER]
    return _WorkerOverrideRuntime(
        phase9_runtime, {ROWLESS_WORKER: _tool_calls_variant(sealed, requirement)})


def _codes(report):
    return sorted({i.code for i in report.issues})


def _issues_with(report, code):
    """Every issue carrying ``code`` — asserted non-empty, so a missing refusal
    reads as a plain assertion failure, never a StopIteration."""
    found = [i for i in report.issues if i.code == code]
    assert found, f"no {code!r} issue; report carries {_codes(report)}"
    return found


# =========================================================================== #
# 0 — the premise, asserted (this suite may not invent its own facts)          #
# =========================================================================== #
class TestThePremise:
    def test_the_sealed_rowless_reader_is_category_only_and_forbidden(
            self, env, bridge_view):
        """``dec.research_mgr`` declares the experience read category, holds NO
        experience capability (so it can have no reviewed row), and FORBIDS tool
        calls — the exact C3 licensed-EMPTY reader."""
        spec = {w.id: w for w in env["snapshot"].workers}[ROWLESS_WORKER]
        cap_ref, _material = bs.experience_retrieve_capability()
        assert EXPERIENCE_CATEGORY in spec.read_categories
        assert all((c.id, c.version, c.content_digest)
                   != (cap_ref.id, cap_ref.version, cap_ref.content_digest)
                   for c in spec.capability_allowlist)
        assert spec.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
        rows = bs.build_experience_prefetch_bindings(cap_ref)
        assert ROWLESS_WORKER not in {r.worker_id for r in rows}
        assert {r.worker_id for r in rows} == {"market.regime", "market.rotation"}

    def test_no_sealed_worker_can_trip_the_new_refusal(self, env, bridge_view):
        """GREEN-PRESERVING, made executable: sweep EVERY sealed Phase-9 worker
        and prove the category-only-activated-with-no-grant set is exactly
        ``{dec.research_mgr}`` and that its tool-call discipline is FORBIDDEN.
        No sealed seat reaches the REQUIRED branch, so this task moves nothing.
        """
        category_only: dict[str, ToolCallRequirement] = {}
        for spec in env["snapshot"].workers:
            allow = {(c.id, c.version, c.content_digest)
                     for c in spec.capability_allowlist}
            cats = frozenset(spec.read_categories)
            for rb in bridge_view.active_bridges_for(spec):
                desc = rb.descriptor
                activation = {(c.id, c.version, c.content_digest)
                              for c in desc.activation_capability_refs}
                if (activation and not (activation & allow)
                        and (cats & frozenset(desc.activation_read_categories))):
                    category_only[spec.id] = spec.evidence_policy.tool_calls
        assert category_only == {ROWLESS_WORKER: ToolCallRequirement.FORBIDDEN}

    def test_the_required_stand_in_differs_in_exactly_one_field(self, env):
        """The stand-in may differ from the sealed spec ONLY in
        ``evidence_policy.tool_calls`` — read categories, allowlist and every
        other reviewed field are the sealed bytes."""
        sealed = {w.id: w for w in env["snapshot"].workers}[ROWLESS_WORKER]
        variant = _tool_calls_variant(sealed, ToolCallRequirement.REQUIRED)
        assert variant.evidence_policy.tool_calls is ToolCallRequirement.REQUIRED
        assert variant.read_categories == sealed.read_categories
        assert variant.capability_allowlist == sealed.capability_allowlist
        sealed_dump = sealed.model_dump(mode="json")
        variant_dump = variant.model_dump(mode="json")
        differing = {k for k in sealed_dump
                     if sealed_dump[k] != variant_dump.get(k)}
        assert differing == {"evidence_policy"}
        sealed_ep = dict(sealed_dump["evidence_policy"])
        variant_ep = dict(variant_dump["evidence_policy"])
        assert {k for k in sealed_ep if sealed_ep[k] != variant_ep.get(k)} == {
            "tool_calls"}


# =========================================================================== #
# 1 — PRESERVED: the C3 rowless reader is admitted and freezes honestly EMPTY  #
# =========================================================================== #
class TestC3RowlessReaderPreserved:
    """Ruling C3 / campaign commit ``4ee43ca``, NOT regressed.

    A rowless reader is not a refusal. Every test here is green BEFORE and AFTER
    this task; if the new admission check ever reddens one it has over-reached from
    "empty is un-runnable for THIS worker" to "empty is illegal", which is the
    silent-to-loud inversion the program refuses.
    """

    def test_the_forbidden_rowless_reader_is_still_admitted(
            self, env, stores, phase9_runtime, bridge_view, dynamic_plan):
        prep = _admit(env, stores, phase9_runtime, bridge_view, dynamic_plan)
        report = prep.support_report
        assert report.supported is True, [
            (i.code, i.node_id) for i in report.issues]
        assert report.issues == ()
        summary = next(s for s in report.bridge_support_summaries
                       if s.bridge_id == "experience.bridge"
                       and s.node_id == "research-mgr")
        assert summary.allowed_capability_refs == ()
        assert summary.max_capability_invocations == 0
        assert summary.min_finalized_tool_calls_on_success == 0

    def test_the_optional_rowless_reader_keeps_the_licensed_empty_posture(
            self, env, stores, phase9_runtime, bridge_view, dynamic_plan):
        """The other honest half of the C3 asymmetry: OPTIONAL can run on
        nothing, so its rowless grant stays licensed-EMPTY (the D-1(0) ruling's
        'OPTIONAL worker 的无行授权 = C3 已裁的 licensed-EMPTY' posture, mirrored
        here on the read-category activation path)."""
        runtime = _variant_runtime(env, phase9_runtime, ToolCallRequirement.OPTIONAL)
        report = _admit(env, stores, runtime, bridge_view, dynamic_plan).support_report
        assert report.supported is True, [
            (i.code, i.node_id) for i in report.issues]
        assert ROWLESS_CODE not in _codes(report)

    def test_the_admitted_reader_never_reaches_the_gateway(
            self, env, phase9_runtime, bridge_view, dynamic_plan):
        """The control half of item 6, driven through the REAL provider + REAL
        gateway: what is admitted is provably un-able to fail late."""
        request, draft = dynamic_plan.request, dynamic_plan.draft
        spec = {w.id: w for w in env["snapshot"].workers}[ROWLESS_WORKER]
        rb = bridge_view.resolve("experience.bridge")
        node = next(n for n in draft.nodes if n.id == "research-mgr")
        summary = rb.analyzer.analyze(
            candidate_plan_digest=compute_candidate_plan_digest(
                request=request, draft=draft,
                context_content_digest=env["context"].content_digest),
            node=node, worker=spec, descriptor=rb.descriptor,
            descriptor_ref=rb.descriptor_ref, config_bytes=rb.config_bytes)
        assert summary.max_capability_invocations == 0

        sequencer = W.ExecutionEvidenceSequencer(node_id="research-mgr", attempt=1)
        refusals: list[dict] = []
        gateway = W.CapabilityGateway(
            plan_digest=summary.candidate_plan_digest, worker=spec,
            summaries={summary.summary_digest: summary}, catalog=phase9_runtime,
            factories=TrustedFactoryRegistry(phase9_runtime),
            phase1_registry=env["registry"],
            refusal_sink=SimpleNamespace(
                record=lambda **kw: refusals.append(kw)),
            clock=_FixedClock(), sequencer=sequencer)
        gateway.mark_running()
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)
        lane0 = bs.load_lane0_catalog()
        provider = bs.make_lane0_experience_bridge_factory(
            pool=None, registry=env["registry"],
            capability_ref=lane0.capability_ref, views=None,
            scaler=SimpleNamespace(feature_schema_version="l3t2-probe-v1"),
            k=1, as_of=NOW)(bridge=rb, summary=summary)
        prepared = provider.prepare_input(SimpleNamespace(token=token))
        assert prepared.status == "prepared"

        class _ForbiddenEvidenceWriter:
            def put(self, **kwargs):
                raise AssertionError("evidence write on the empty freeze path")

            def record_existing(self, **kwargs):
                raise AssertionError("evidence write on the empty freeze path")

        session = provider.open_execution(SimpleNamespace(
            plan=None, node=node, worker=spec, bridge=rb, summary=summary,
            handle=prepared.prepared_handle,
            input_snapshot=SimpleNamespace(artifact_inputs=()),
            capability_gateway=gateway,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=sequencer))
        outcome = session.freeze_for_execution(kind=ExecutionKind.LLM)
        assert outcome.status == "completed"
        assert outcome.frozen_contribution.tool_call_records == ()
        assert outcome.frozen_contribution.untrusted_blocks == ()
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals == []


# =========================================================================== #
# 2 — REFUSED AT ADMISSION: the shape emptiness cannot honestly serve          #
# =========================================================================== #
class TestRequiredRowlessRefusedAtAdmission:
    @pytest.fixture(scope="class")
    def refused(self, env, stores, phase9_runtime, bridge_view, dynamic_plan):
        runtime = _variant_runtime(env, phase9_runtime, ToolCallRequirement.REQUIRED)
        return _admit(env, stores, runtime, bridge_view, dynamic_plan).support_report

    def test_admission_refuses_it(self, refused):
        assert refused.supported is False
        assert ROWLESS_CODE in _codes(refused)

    def test_the_refusal_names_the_worker_the_bridge_and_the_missing_row(
            self, refused):
        """A typed refusal an operator can act on: WHICH worker, WHICH bridge,
        WHICH read category, and that the reviewed prefetch row is what is
        missing — never a bare number."""
        issue = _issues_with(refused, ROWLESS_CODE)[0]
        assert issue.node_id == "research-mgr"
        assert ROWLESS_WORKER in issue.explanation
        assert "experience.bridge" in issue.explanation
        assert EXPERIENCE_CATEGORY in issue.explanation
        assert "row" in issue.explanation
        assert issue.model_path == "WorkerSpec.read_categories"

    def test_the_refusal_is_structural_not_the_arithmetic_renamed(self, refused):
        """The pre-existing cause-blind arithmetic refusal STAYS (it is a
        different fact about a different model path). The new one is derived
        per offending bridge summary, so a sibling bridge's row could never mask
        it — two independent refusals, not one renamed."""
        arithmetic = _issues_with(refused, ARITHMETIC_CODE)
        rowless = _issues_with(refused, ROWLESS_CODE)
        assert arithmetic[0].model_path == "WorkerSpec.evidence_policy.tool_calls"
        assert rowless[0].model_path != arithmetic[0].model_path
        # exactly one per offending bridge summary (the node has exactly one).
        assert len(rowless) == 1

    def test_the_zero_contribution_summary_is_still_reported_honestly(
            self, refused):
        """Refusing does not hide the measurement that caused it."""
        summary = next(s for s in refused.bridge_support_summaries
                       if s.bridge_id == "experience.bridge"
                       and s.node_id == "research-mgr")
        assert summary.allowed_capability_refs == ()
        assert summary.max_capability_invocations == 0


# =========================================================================== #
# 3 — the LOUD half is untouched                                              #
# =========================================================================== #
class TestAllowlistedButRowlessStaysLoud:
    def test_a_reviewed_grant_that_lost_its_row_still_raises(
            self, env, stores, phase9_runtime, bridge_view, dynamic_plan):
        """Task-11's kept-LOUD branch: a worker whose allowlist DOES hold the
        experience capability but has no reviewed row is a lost row, not an
        absent grant, and it must never degrade into any typed report — it
        raises out of the support check. This task adds a refusal; it removes
        no loudness."""
        sealed = {w.id: w for w in env["snapshot"].workers}[ROWLESS_WORKER]
        cap_ref, _material = bs.experience_retrieve_capability()
        granted_rowless = sealed.model_copy(update={
            "capability_allowlist": tuple(sealed.capability_allowlist) + (cap_ref,)})
        runtime = _WorkerOverrideRuntime(
            phase9_runtime, {ROWLESS_WORKER: granted_rowless})
        with pytest.raises(CatalogError, match="no reviewed experience prefetch row"):
            _admit(env, stores, runtime, bridge_view, dynamic_plan)
