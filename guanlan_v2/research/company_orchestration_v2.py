"""Typed orchestration-v2 contracts for the five-stage company research DAG.

This module is intentionally independent from the Console transport.  It owns
the strict payload schemas, a material-verified worker catalog, the dedicated
``PlanDraft`` materializer and a catalog-confined worker runtime.  A caller may
execute only a *frozen* :class:`~guanlan_v2.orchestration.spec.Plan`; creating a
draft here never approves or dispatches it.

The report stage is represented as a deterministic *controller* handler.  The
controller may launch the existing approved/durable multi-agent report service,
but the handler material does not mislabel that downstream LLM work as a pure
calculation.  Its requested LLM/token budget remains explicit on the draft.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from pydantic import field_validator

from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.digest import DigestHex, DigestModel
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    ExecutionKind,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    Plan,
    PlanDraft,
    PlanNode,
    PlanValidationReport,
    validate_plan_draft,
)
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.research.company_recipe import TrustedCompanyResearchBinding
from guanlan_v2.glmcp.company_research import company_research_binding_sha256


CATALOG_VERSION = "company-research-v2.1"
WORKER_IDS: tuple[str, ...] = (
    "company.prepare",
    "company.research-report",
    "company.risk-gate",
    "company.report-gate",
    "company.finalize",
)
NODE_IDS: tuple[str, ...] = (
    "prepare",
    "research-report",
    "risk-gate",
    "report-gate",
    "finalize",
)

_JOB_RE = re.compile(r"^bg_[0-9a-f]{10}$")
_WORKSPACE_RE = re.compile(r"^rw_[0-9a-f]{12}$")
_ASSET_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)[0-9]{6}$")


class CompanyResearchV2Error(RuntimeError):
    """A typed company plan or stage result failed closed."""


class _CompanyResearchStageV2(DigestModel):
    """Stable server-owned identity copied through every stage output."""

    schema_version: Literal["2"] = "2"
    job_id: str
    workspace_id: str
    code: str
    as_of: str
    binding_sha256: DigestHex

    @field_validator("job_id")
    @classmethod
    def _job(cls, value: str) -> str:
        if _JOB_RE.fullmatch(value) is None:
            raise ValueError("job_id must be a service-issued bg_* id")
        return value

    @field_validator("workspace_id")
    @classmethod
    def _workspace(cls, value: str) -> str:
        if _WORKSPACE_RE.fullmatch(value) is None:
            raise ValueError("workspace_id must be a managed rw_* id")
        return value

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        if _CODE_RE.fullmatch(value) is None:
            raise ValueError("code must be an SH/SZ/BJ A-share code")
        return value

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("as_of must use YYYY-MM-DD")
        return value


class CompanyResearchPrepareParamsV2(_CompanyResearchStageV2):
    """The only configurable node payload; every field is service-minted."""


class CompanyResearchPreparedV2(_CompanyResearchStageV2):
    prepared_asset_id: str
    prepare_asset_sha256: DigestHex
    evidence_pack_sha256: DigestHex

    @field_validator("prepared_asset_id")
    @classmethod
    def _asset(cls, value: str) -> str:
        if _ASSET_RE.fullmatch(value) is None:
            raise ValueError("prepared_asset_id must be a wa_* workspace asset")
        return value


class CompanyResearchReportV2(_CompanyResearchStageV2):
    prepared_asset_id: str
    report_sha256: DigestHex
    durable_result_sha256: DigestHex

    _asset = field_validator("prepared_asset_id")(
        CompanyResearchPreparedV2._asset.__func__
    )


class CompanyResearchRiskGateV2(_CompanyResearchStageV2):
    prepared_asset_id: str
    report_sha256: DigestHex
    risk_contract_sha256: DigestHex
    gate_status: Literal["complete", "incomplete", "stale", "failed"]
    passed: bool
    blocking_reasons: tuple[str, ...] = ()

    _asset = field_validator("prepared_asset_id")(
        CompanyResearchPreparedV2._asset.__func__
    )


class CompanyResearchReportGateV2(_CompanyResearchStageV2):
    prepared_asset_id: str
    report_sha256: DigestHex
    risk_contract_sha256: DigestHex
    report_gate_sha256: DigestHex
    gate_status: Literal["complete", "incomplete", "stale", "failed"]
    passed: bool
    blocking_reasons: tuple[str, ...] = ()

    _asset = field_validator("prepared_asset_id")(
        CompanyResearchPreparedV2._asset.__func__
    )


class CompanyResearchFinalizedV2(_CompanyResearchStageV2):
    prepared_asset_id: str
    report_sha256: DigestHex
    risk_contract_sha256: DigestHex
    report_gate_sha256: DigestHex
    asset_id: str
    asset_status: Literal["complete", "incomplete", "stale", "failed"]
    adopted_existing_asset: bool
    finalizer_replayed: Literal[False] = False

    @field_validator("prepared_asset_id", "asset_id")
    @classmethod
    def _asset(cls, value: str) -> str:
        if _ASSET_RE.fullmatch(value) is None:
            raise ValueError("asset ids must be wa_* workspace assets")
        return value


SCHEMA_MODELS: tuple[type[DigestModel], ...] = (
    CompanyResearchPrepareParamsV2,
    CompanyResearchPreparedV2,
    CompanyResearchReportV2,
    CompanyResearchRiskGateV2,
    CompanyResearchReportGateV2,
    CompanyResearchFinalizedV2,
)


@lru_cache(maxsize=1)
def company_research_v2_registry() -> SchemaRegistry:
    """Return the immutable registry used by the company-v2 catalog."""
    registry = SchemaRegistry()
    for model in SCHEMA_MODELS:
        registry.register(model)
    registry.seal()
    return registry


def _schema(model: type[DigestModel]) -> SchemaRef:
    return SchemaRef(name=model.__name__, version="2")


_HANDLER_DESCRIPTIONS: Mapping[str, Mapping[str, str]] = {
    "company.prepare": {
        "effect": "private_workspace_write",
        "contract": "service-bound idempotent prepare by job id",
    },
    "company.research-report": {
        "effect": "durable_subprocess",
        "contract": "controller for the explicitly approved multi-agent report service",
    },
    "company.risk-gate": {
        "effect": "none",
        "contract": "deterministic issuer provenance and citation risk gate",
    },
    "company.report-gate": {
        "effect": "none",
        "contract": "strict evidence calculation conflict and report gate",
    },
    "company.finalize": {
        "effect": "private_workspace_write",
        "contract": "exactly-once asset adopt-or-create finalizer",
    },
}


@lru_cache(maxsize=1)
def company_research_v2_catalog() -> WorkerCatalogSnapshot:
    """Build the material-verified five-worker catalog (ONLINE only)."""
    materials = []
    entries = []
    handler_refs = {}
    for worker_id in WORKER_IDS:
        raw = json.dumps(
            {
                "schema": "company-research-handler-material@1",
                "worker_id": worker_id,
                **_HANDLER_DESCRIPTIONS[worker_id],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ref, material = build_text_material(
            id=f"handler.{worker_id}", version="1", kind="handler", raw=raw,
        )
        handler_refs[worker_id] = ref
        materials.append(material)
        entries.append(ContentManifestEntry(
            ref=ref,
            kind="handler",
            name=f"{worker_id} reviewed controller",
            description=_HANDLER_DESCRIPTIONS[worker_id]["contract"],
            source_identity="company.research.v2",
        ))

    prepared = _schema(CompanyResearchPreparedV2)
    report = _schema(CompanyResearchReportV2)
    risk = _schema(CompanyResearchRiskGateV2)
    report_gate = _schema(CompanyResearchReportGateV2)
    outputs = (
        prepared,
        report,
        risk,
        report_gate,
        _schema(CompanyResearchFinalizedV2),
    )
    inputs = (
        (),
        (InputBinding(name="prepared", schema_ref=prepared),),
        (InputBinding(name="report", schema_ref=report),),
        (InputBinding(name="risk", schema_ref=risk),),
        (InputBinding(name="report-gate", schema_ref=report_gate),),
    )
    tiers = (Tier.READER, Tier.WRITER, Tier.CRITIC, Tier.CRITIC, Tier.WRITER)
    workers = []
    for index, worker_id in enumerate(WORKER_IDS):
        workers.append(WorkerSpec(
            id=worker_id,
            catalog_role="final",
            selection_scope="dynamic_allowed",
            lane="xcut",
            persona=_HANDLER_DESCRIPTIONS[worker_id]["contract"],
            tier=tiers[index],
            execution=ExecutionSpec(
                kind=ExecutionKind.DETERMINISTIC,
                handler_ref=handler_refs[worker_id],
            ),
            capability_allowlist=(),
            read_categories=(
                ("context",) if index == 0
                else ("context", "upstream_artifacts")
            ),
            params_schema_ref=(
                _schema(CompanyResearchPrepareParamsV2)
                if index == 0 else None
            ),
            inputs=inputs[index],
            outputs=(OutputBinding(name="primary", schema_ref=outputs[index]),),
            evidence_policy=EvidencePolicy(
                tool_calls=ToolCallRequirement.FORBIDDEN,
                require_input_refs=(index != 0),
                require_number_anchors=False,
                allow_unsourced_numbers=False,
                optional_data_may_degrade=False,
            ),
            supported_modes=(DataMode.ONLINE,),
            can_emit_decision=False,
            decision_authority="none",
        ))
    return build_catalog_snapshot(
        catalog_version=CATALOG_VERSION,
        content_manifest=entries,
        skill_manifest=(),
        capability_manifest=(),
        workers=workers,
        resolved_material=materials,
    )


@dataclass(frozen=True)
class MaterializedCompanyResearchV2:
    """A validated candidate and its digest-bound validation report."""

    draft: PlanDraft
    validation_report: PlanValidationReport


def materialize_company_research_v2(
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    binding: TrustedCompanyResearchBinding,
    job_id: str,
    as_of: str,
    binding_sha256: str,
    workspace_subject_sha256: str,
    workspace_root_sha256: str,
    budget_tokens: int,
    budget_llm_invocations: int,
) -> MaterializedCompanyResearchV2:
    """Materialize and offline-validate the one reviewed five-stage DAG.

    This function never approves, reserves, dispatches, writes or calls a model.
    The binding and job id must already have been minted by the service.  A
    positive report budget is mandatory because the controller's downstream
    report service performs LLM work even though the controller itself is a
    deterministic catalog handler.
    """
    if request.approval_policy is not ApprovalPolicy.REQUIRED:
        raise CompanyResearchV2Error("company research v2 requires human approval")
    if context.data_context.mode is not DataMode.ONLINE:
        raise CompanyResearchV2Error(
            "company research v2 currently supports ONLINE only; PIT is not claimed"
        )
    expected_binding = company_research_binding_sha256(
        sid=binding.session_id,
        workspace_id=binding.workspace_id,
        code=binding.subject.code,
        subject_sha256=workspace_subject_sha256,
        root_sha256=workspace_root_sha256,
    )
    if binding_sha256 != expected_binding:
        raise CompanyResearchV2Error("trusted binding digest mismatch")
    if budget_tokens <= 0 or budget_llm_invocations <= 0:
        raise CompanyResearchV2Error(
            "the multi-agent report stage requires explicit positive token and LLM budgets"
        )
    params = CompanyResearchPrepareParamsV2(
        job_id=job_id,
        workspace_id=binding.workspace_id,
        code=binding.subject.code,
        as_of=as_of,
        binding_sha256=binding_sha256,
    )
    catalog = company_research_v2_catalog()
    registry = company_research_v2_registry()

    nodes: list[PlanNode] = []
    slots = (
        "company.prepared",
        "company.report",
        "company.risk",
        "company.report-gate",
        "company.finalized",
    )
    inject_names = (None, "prepared", "report", "risk", "report-gate")
    for index, (node_id, worker_id) in enumerate(zip(NODE_IDS, WORKER_IDS)):
        dependencies = ()
        if index:
            dependencies = (Dependency(
                upstream_node_id=NODE_IDS[index - 1],
                artifact_slot=slots[index - 1],
                inject_as=inject_names[index],
            ),)
        nodes.append(PlanNode(
            id=node_id,
            worker_id=worker_id,
            params=(params.model_dump(mode="json") if index == 0 else {}),
            dependencies=dependencies,
            writes_slot=slots[index],
            timeout_sec=(960 if index == 1 else 300),
            max_attempts=1,
            token_reservation=(budget_tokens if index == 1 else 0),
        ))

    draft = PlanDraft(
        id=f"company.plan.{job_id}",
        run_id=f"company-research-v2:{job_id}",
        request_id=request.request_id,
        phase="main",
        source=PlanSource.DYNAMIC,
        goal=request.goal,
        as_of=context.data_context.as_of,
        mode=DataMode.ONLINE,
        context_snapshot_ref=PayloadRef(
            namespace="main",
            object_id=context.snapshot_id,
            content_digest=context.content_digest,
        ),
        universe=(normalize_symbol(binding.subject.code),),
        nodes=tuple(nodes),
        sink_node_ids=("finalize",),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=budget_tokens,
        budget_request_llm_invocations=budget_llm_invocations,
        max_concurrency=1,
    )
    report = validate_plan_draft(
        draft,
        request=request,
        context=context,
        catalog=catalog,
        schema_registry=registry,
    )
    if not report.valid:
        detail = ", ".join(issue.code for issue in report.issues)
        raise CompanyResearchV2Error(f"materialized company DAG is invalid: {detail}")
    return MaterializedCompanyResearchV2(draft=draft, validation_report=report)


StageHandler = Callable[
    [DigestModel | None, Mapping[str, DigestModel]],
    DigestModel | Mapping[str, Any],
]


@dataclass(frozen=True)
class CompanyResearchV2WorkerBindings:
    """Service-owned implementations for every reviewed catalog worker."""

    prepare: StageHandler
    research_report: StageHandler
    risk_gate: StageHandler
    report_gate: StageHandler
    finalize: StageHandler

    def by_worker_id(self) -> Mapping[str, StageHandler]:
        return MappingProxyType({
            "company.prepare": self.prepare,
            "company.research-report": self.research_report,
            "company.risk-gate": self.risk_gate,
            "company.report-gate": self.report_gate,
            "company.finalize": self.finalize,
        })


@dataclass(frozen=True)
class CompanyResearchV2Execution:
    plan_digest: str
    outputs: Mapping[str, DigestModel]
    final: CompanyResearchFinalizedV2


def _verify_plan_shape(plan: Plan) -> None:
    draft = plan.draft
    if (
        draft.source.value != "dynamic"
        or draft.mode is not DataMode.ONLINE
        or draft.approval_policy is not ApprovalPolicy.REQUIRED
        or tuple(node.id for node in draft.nodes) != NODE_IDS
        or tuple(node.worker_id for node in draft.nodes) != WORKER_IDS
        or draft.sink_node_ids != ("finalize",)
        or draft.max_concurrency != 1
    ):
        raise CompanyResearchV2Error("frozen Plan is not the reviewed company-v2 DAG")
    for index, node in enumerate(draft.nodes):
        if node.max_attempts != 1:
            raise CompanyResearchV2Error("company-v2 stages forbid implicit retry")
        if index == 0:
            if node.dependencies:
                raise CompanyResearchV2Error("prepare must have no dependency")
            continue
        dep = node.dependencies
        if len(dep) != 1 or dep[0].upstream_node_id != NODE_IDS[index - 1]:
            raise CompanyResearchV2Error("company-v2 stage dependency drift")


def execute_company_research_v2(
    *,
    plan: Plan,
    bindings: CompanyResearchV2WorkerBindings,
) -> CompanyResearchV2Execution:
    """Execute a human-approved frozen company plan through typed workers.

    The runtime has no fallback, no dynamic callable/path override and no
    implicit retry.  Every stage output is validated by the exact catalog
    schema before it is passed to the next worker.  A failed risk/report gate
    stops before the finalizer binding.
    """
    catalog = company_research_v2_catalog()
    registry = company_research_v2_registry()
    if (
        plan.catalog_version != catalog.catalog_version
        or plan.catalog_digest != catalog.catalog_digest
        or plan.schema_registry_digest != registry.registry_digest
    ):
        raise CompanyResearchV2Error("frozen Plan catalog/registry binding drift")
    _verify_plan_shape(plan)
    worker_by_id = {worker.id: worker for worker in catalog.workers}
    handler_by_id = bindings.by_worker_id()
    outputs: dict[str, DigestModel] = {}
    stable: dict[str, Any] | None = None
    prepared_asset_id = ""
    report_sha256 = ""
    risk_sha256 = ""
    report_gate_sha256 = ""

    for node in plan.nodes:
        worker = worker_by_id[node.worker_id]
        params: DigestModel | None = None
        if worker.params_schema_ref is not None:
            params = registry.validate_payload(worker.params_schema_ref, node.params)
        elif node.params:
            raise CompanyResearchV2Error(f"unexpected params for {node.worker_id}")
        inputs: dict[str, DigestModel] = {}
        for dep in node.dependencies:
            try:
                inputs[dep.inject_as] = outputs[dep.upstream_node_id]
            except KeyError as exc:
                raise CompanyResearchV2Error(
                    f"missing typed upstream output for {node.id}"
                ) from exc
        raw = handler_by_id[node.worker_id](params, MappingProxyType(inputs))
        if inspect.isawaitable(raw):
            raise CompanyResearchV2Error("async stage handlers are not accepted by sync runtime")
        primary = next(out for out in worker.outputs if out.name == "primary")
        try:
            record = registry.validate_payload(primary.schema_ref, raw)
        except Exception as exc:  # pydantic + registry failures are one closed boundary
            raise CompanyResearchV2Error(
                f"{node.worker_id} returned an invalid {primary.schema_ref.key}: {exc}"
            ) from exc

        current = {
            "job_id": record.job_id,
            "workspace_id": record.workspace_id,
            "code": record.code,
            "as_of": record.as_of,
            "binding_sha256": record.binding_sha256,
        }
        if stable is None:
            if params is None:
                raise CompanyResearchV2Error("prepare params are missing")
            stable = {
                key: getattr(params, key) for key in current
            }
        if current != stable:
            raise CompanyResearchV2Error(f"{node.worker_id} changed the server binding")

        if isinstance(record, CompanyResearchPreparedV2):
            prepared_asset_id = record.prepared_asset_id
        elif getattr(record, "prepared_asset_id", "") != prepared_asset_id:
            raise CompanyResearchV2Error(f"{node.worker_id} changed prepared_asset_id")
        if isinstance(record, CompanyResearchReportV2):
            report_sha256 = record.report_sha256
        elif hasattr(record, "report_sha256") and record.report_sha256 != report_sha256:
            raise CompanyResearchV2Error(f"{node.worker_id} changed report_sha256")
        if isinstance(record, CompanyResearchRiskGateV2):
            risk_sha256 = record.risk_contract_sha256
            if not record.passed:
                raise CompanyResearchV2Error("risk gate blocked finalization")
        elif hasattr(record, "risk_contract_sha256") and (
            record.risk_contract_sha256 != risk_sha256
        ):
            raise CompanyResearchV2Error(f"{node.worker_id} changed risk contract")
        if isinstance(record, CompanyResearchReportGateV2):
            report_gate_sha256 = record.report_gate_sha256
            if not record.passed:
                raise CompanyResearchV2Error("report gate blocked finalization")
        elif hasattr(record, "report_gate_sha256") and (
            record.report_gate_sha256 != report_gate_sha256
        ):
            raise CompanyResearchV2Error(f"{node.worker_id} changed report gate")
        outputs[node.id] = record

    final = outputs.get("finalize")
    if not isinstance(final, CompanyResearchFinalizedV2):
        raise CompanyResearchV2Error("company-v2 did not produce a final asset")
    return CompanyResearchV2Execution(
        plan_digest=plan.plan_digest,
        outputs=MappingProxyType(dict(outputs)),
        final=final,
    )


__all__ = [
    "CATALOG_VERSION",
    "CompanyResearchFinalizedV2",
    "CompanyResearchPrepareParamsV2",
    "CompanyResearchPreparedV2",
    "CompanyResearchReportGateV2",
    "CompanyResearchReportV2",
    "CompanyResearchRiskGateV2",
    "CompanyResearchV2Error",
    "CompanyResearchV2Execution",
    "CompanyResearchV2WorkerBindings",
    "MaterializedCompanyResearchV2",
    "NODE_IDS",
    "SCHEMA_MODELS",
    "WORKER_IDS",
    "company_research_v2_catalog",
    "company_research_v2_registry",
    "execute_company_research_v2",
    "materialize_company_research_v2",
]
