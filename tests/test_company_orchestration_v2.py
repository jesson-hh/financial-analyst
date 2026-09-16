from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration.context import (
    BudgetReservation,
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataBackend,
    DataMode,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    freeze_plan,
)
from guanlan_v2.research.company_orchestration_v2 import (
    NODE_IDS,
    WORKER_IDS,
    CompanyResearchV2Error,
    CompanyResearchV2WorkerBindings,
    company_research_v2_catalog,
    company_research_v2_registry,
    execute_company_research_v2,
    materialize_company_research_v2,
)
from guanlan_v2.research.company_recipe import TrustedCompanyResearchBinding
from guanlan_v2.glmcp.company_research import company_research_binding_sha256


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 2, 30, tzinfo=UTC)


def _context(*, mode=DataMode.ONLINE):
    memory = build_empty_memory_binding()
    clock = ClockSpec(
        as_of=NOW, timezone="Asia/Shanghai", calendar_id="cn_a_share",
    )
    data = DataContext(
        as_of=NOW,
        clock=clock,
        mode=mode,
        backend=(DataBackend.LIVE if mode is DataMode.ONLINE else DataBackend.PIT_STORE),
        strict_pit=(mode is DataMode.PIT_REPLAY),
        calendar_id="cn_a_share",
        resolved_vendor_chains={"company_research": ("workspace",)},
        source_config_digest="a" * 64,
        source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64,
        data_snapshot_id="company-snapshot-1",
        data_snapshot_content_digest="d" * 64,
        vintage_manifest_digest=("e" * 64 if mode is DataMode.PIT_REPLAY else None),
        built_at=NOW,
    )
    return ContextSnapshot.build(
        snapshot_id="company-context-1",
        data_context=data,
        memory_snapshot_id="company-memory-1",
        memory_snapshot_hash=memory.snapshot_hash,
        past_context_hash=memory.past_context_hash,
        memory_snapshot_ref=memory.memory_snapshot_ref,
        memory_selection_ref=memory.memory_selection_ref,
        runtime_requirements_ref=None,
        built_at=NOW,
    )


def _binding():
    return TrustedCompanyResearchBinding(
        binding_source="server_current_session",
        session_id="cs_aaaaaaaaaaaa",
        workspace_id="rw_bbbbbbbbbbbb",
        workspace_kind="stock",
        active=True,
        subject={"kind": "stock", "code": "SH603986", "name": "兆易创新"},
    )


def _binding_sha(binding):
    return company_research_binding_sha256(
        sid=binding.session_id,
        workspace_id=binding.workspace_id,
        code=binding.subject.code,
        subject_sha256="f" * 64,
        root_sha256="9" * 64,
    )


def _request():
    return OrchestrationRequest(
        request_id="company-request-1",
        goal="研究当前工作空间公司",
        workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED,
    )


def _bundle(*, context=None):
    binding = _binding()
    return materialize_company_research_v2(
        request=_request(),
        context=context or _context(),
        binding=binding,
        job_id="bg_aaaaaaaaaa",
        as_of="2026-09-04",
        binding_sha256=_binding_sha(binding),
        workspace_subject_sha256="f" * 64,
        workspace_root_sha256="9" * 64,
        budget_tokens=120000,
        budget_llm_invocations=25,
    )


def _frozen_plan():
    context = _context()
    request = _request()
    binding = _binding()
    bundle = materialize_company_research_v2(
        request=request,
        context=context,
        binding=binding,
        job_id="bg_aaaaaaaaaa",
        as_of="2026-09-04",
        binding_sha256=_binding_sha(binding),
        workspace_subject_sha256="f" * 64,
        workspace_root_sha256="9" * 64,
        budget_tokens=120000,
        budget_llm_invocations=25,
    )
    candidate = bundle.validation_report.candidate_plan_digest
    reservation = BudgetReservation(
        reservation_id="company-reservation-1",
        ledger_id="company-ledger-1",
        run_id=bundle.draft.run_id,
        request_id=request.request_id,
        candidate_plan_digest=candidate,
        scope_type="plan",
        scope_id=bundle.draft.id,
        reserved_tokens=120000,
        reserved_llm_invocations=25,
        reserved_concurrency=1,
        status="reserved",
        reserved_at=NOW,
    )
    approval = PlanApproval(
        request_id=request.request_id,
        candidate_plan_digest=candidate,
        decision=ApprovalDecision.APPROVED,
        actor_id="reviewer-1",
        decided_at=NOW,
    )
    return freeze_plan(
        bundle.draft,
        request=request,
        context=context,
        catalog=company_research_v2_catalog(),
        schema_registry=company_research_v2_registry(),
        legacy_attestation=None,
        report=bundle.validation_report,
        reservation=reservation,
        approval=approval,
        frozen_at=NOW,
    )


def _stable():
    binding = _binding()
    return {
        "schema_version": "2",
        "job_id": "bg_aaaaaaaaaa",
        "workspace_id": binding.workspace_id,
        "code": binding.subject.code,
        "as_of": "2026-09-04",
        "binding_sha256": _binding_sha(binding),
    }


def test_company_v2_catalog_registry_and_materializer_are_exact():
    catalog = company_research_v2_catalog()
    registry = company_research_v2_registry()
    bundle = _bundle()
    assert registry.sealed is True
    assert len(registry.manifest()) == 6
    # WorkerCatalogSnapshot canonicalizes set-like workers by logical id; the
    # executable business order is carried only by the materialized Plan.
    assert tuple(worker.id for worker in catalog.workers) == tuple(sorted(WORKER_IDS))
    assert bundle.validation_report.valid is True
    assert tuple(node.id for node in bundle.draft.nodes) == NODE_IDS
    assert bundle.draft.sink_node_ids == ("finalize",)
    assert bundle.draft.max_concurrency == 1
    assert bundle.draft.approval_policy is ApprovalPolicy.REQUIRED
    assert bundle.draft.budget_request_tokens == 120000
    assert bundle.draft.budget_request_llm_invocations == 25
    assert all(node.max_attempts == 1 for node in bundle.draft.nodes)


def test_company_v2_materializer_refuses_pit_and_hidden_zero_budget():
    with pytest.raises(CompanyResearchV2Error, match="ONLINE only"):
        _bundle(context=_context(mode=DataMode.PIT_REPLAY))
    binding = _binding()
    with pytest.raises(CompanyResearchV2Error, match="explicit positive"):
        materialize_company_research_v2(
            request=_request(),
            context=_context(),
            binding=binding,
            job_id="bg_aaaaaaaaaa",
            as_of="2026-09-04",
            binding_sha256=_binding_sha(binding),
            workspace_subject_sha256="f" * 64,
            workspace_root_sha256="9" * 64,
            budget_tokens=0,
            budget_llm_invocations=0,
        )


def test_company_v2_frozen_plan_executes_typed_chain_once():
    plan = _frozen_plan()
    base = _stable()
    order = []

    def prepare(params, inputs):
        order.append("prepare")
        assert params.job_id == base["job_id"] and not inputs
        return {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "prepare_asset_sha256": "1" * 64,
            "evidence_pack_sha256": "2" * 64,
        }

    def report(_params, inputs):
        order.append("research-report")
        assert inputs["prepared"].prepared_asset_id == "wa_1111111111111111"
        return {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "durable_result_sha256": "4" * 64,
        }

    def risk(_params, inputs):
        order.append("risk-gate")
        assert inputs["report"].report_sha256 == "3" * 64
        return {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "risk_contract_sha256": "5" * 64,
            "gate_status": "complete",
            "passed": True,
            "blocking_reasons": (),
        }

    def gate(_params, inputs):
        order.append("report-gate")
        assert inputs["risk"].risk_contract_sha256 == "5" * 64
        return {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "risk_contract_sha256": "5" * 64,
            "report_gate_sha256": "6" * 64,
            "gate_status": "complete",
            "passed": True,
            "blocking_reasons": (),
        }

    def finalize(_params, inputs):
        order.append("finalize")
        assert inputs["report-gate"].report_gate_sha256 == "6" * 64
        return {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "risk_contract_sha256": "5" * 64,
            "report_gate_sha256": "6" * 64,
            "asset_id": "wa_2222222222222222",
            "asset_status": "complete",
            "adopted_existing_asset": False,
            "finalizer_replayed": False,
        }

    result = execute_company_research_v2(
        plan=plan,
        bindings=CompanyResearchV2WorkerBindings(
            prepare=prepare,
            research_report=report,
            risk_gate=risk,
            report_gate=gate,
            finalize=finalize,
        ),
    )
    assert order == list(NODE_IDS)
    assert result.plan_digest == plan.plan_digest
    assert result.final.asset_id == "wa_2222222222222222"
    assert result.final.finalizer_replayed is False


def test_company_v2_gate_failure_never_calls_finalizer():
    plan = _frozen_plan()
    base = _stable()
    finalizer_calls = []

    bindings = CompanyResearchV2WorkerBindings(
        prepare=lambda _p, _i: {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "prepare_asset_sha256": "1" * 64,
            "evidence_pack_sha256": "2" * 64,
        },
        research_report=lambda _p, _i: {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "durable_result_sha256": "4" * 64,
        },
        risk_gate=lambda _p, _i: {
            **base,
            "prepared_asset_id": "wa_1111111111111111",
            "report_sha256": "3" * 64,
            "risk_contract_sha256": "5" * 64,
            "gate_status": "incomplete",
            "passed": False,
            "blocking_reasons": ("issuer provenance missing",),
        },
        report_gate=lambda _p, _i: pytest.fail("report gate must not run"),
        finalize=lambda _p, _i: finalizer_calls.append(True),
    )
    with pytest.raises(CompanyResearchV2Error, match="risk gate blocked"):
        execute_company_research_v2(plan=plan, bindings=bindings)
    assert finalizer_calls == []


def test_company_v2_worker_cannot_change_server_binding():
    plan = _frozen_plan()
    base = _stable()
    bindings = CompanyResearchV2WorkerBindings(
        prepare=lambda _p, _i: {
            **base,
            "code": "SH600000",
            "prepared_asset_id": "wa_1111111111111111",
            "prepare_asset_sha256": "1" * 64,
            "evidence_pack_sha256": "2" * 64,
        },
        research_report=lambda _p, _i: pytest.fail("must not advance"),
        risk_gate=lambda _p, _i: pytest.fail("must not advance"),
        report_gate=lambda _p, _i: pytest.fail("must not advance"),
        finalize=lambda _p, _i: pytest.fail("must not advance"),
    )
    with pytest.raises(CompanyResearchV2Error, match="changed the server binding"):
        execute_company_research_v2(plan=plan, bindings=bindings)
