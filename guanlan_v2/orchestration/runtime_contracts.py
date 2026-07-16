"""Phase 2 · Task 2 — audit-only event-refusal contracts + audit-detail registry.

This module holds the **initial** Phase 2 control-plane facts: the strict,
audit-only refusal-detail/record contracts and the tiny sealed registry that
validates a refusal *detail* payload before the sink persists it. Task 5 appends
the remaining runtime-control / prompt-evidence facts (support report, admission,
bridge descriptors, prompt records) to this same module.

Why these are ``_StrictModel`` (not :class:`~guanlan_v2.orchestration.digest.ContractModel`)
------------------------------------------------------------------------------
Task 1 established the idiom: a Phase 2 runtime value model mirrors the Phase 1
strict config (``extra='forbid'`` / ``strict=True`` / ``frozen=True``) *without*
inheriting the registered-contract identity of ``ContractModel`` — that keeps the
Phase 1 contract-completeness firewall (``test_contract_completeness.py``, which
walks the whole package for public ``ContractModel`` subclasses) **legitimately
blind** to Phase 2 additions, so no Phase 1 firewall/registry file is forked to
land a Phase 2 fact.

The refusal contracts *are* persisted, digest-bearing, audit-namespace facts —
but they are deliberately **not** part of the Phase 1 ``default_registry`` and are
never resolved through the main payload/event schema registry. Their detail
payloads are validated against a *separate*, self-contained
:class:`AuditDetailRegistry` (this module), and the record seals its own semantic
digest through :meth:`EventRefusalRecord.build` (a self-managed digest computed by
:func:`~guanlan_v2.orchestration.digest.content_digest` over the record's semantic
projection, excluding audit identity/time). A later reviewed audit-detail registry
adds Phase-3-style typed detail schemas under a *new exact registry digest* — the
``EventRefusalRecord`` wrapper ABI never changes.

(Task 5, which builds the cumulative Phase 2 ``phase2_runtime_registry``, is the
reviewed place to decide whether these facts should additionally be promoted into
that registry; doing so there — alongside the Phase 2 completeness review — is
cleaner than forking the Phase 1 firewall from Task 2. See the task report.)
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from guanlan_v2.orchestration.context import ContextRuntimeRequirements
from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DependencyPolicy, ExecutionKind, PlanSource
from guanlan_v2.orchestration.catalog import Cardinality, ReadCategory
from guanlan_v2.orchestration.events import LayerCommit
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    LogicalId,
    PayloadNamespace,
    PayloadRef,
    SchemaManifestEntry,
    SchemaRef,
    TypedPayloadRef,
)

if TYPE_CHECKING:  # heavy Phase-1 surfaces used only for signatures / doc
    from guanlan_v2.orchestration.catalog import WorkerSpec
    from guanlan_v2.orchestration.spec import PlanNode

__all__ = [
    # -- Task 2 audit-refusal ABI (unchanged) ------------------------------- #
    "NamedEvidenceDigest",
    "GenericRefusalDetails",
    "EventRefusalRecord",
    "AuditDetailRegistry",
    "AuditDetailRegistryError",
    "UnknownAuditDetailSchema",
    "AuditDetailSealedError",
    "default_audit_detail_registry",
    # -- Task 5 static runtime profile + support report --------------------- #
    "StaticRuntimeProfile",
    "static_runtime_profile",
    "RuntimeSupportIssue",
    "RuntimeSupportReport",
    # -- Task 5 control facts ---------------------------------------------- #
    "AdmissionCandidate",
    "AdmissionInvalidated",
    "PlanAdmitted",
    "RunResult",
    # -- Task 5 bridge descriptor / summary / analyzer ---------------------- #
    "ExecutionBridgeDescriptor",
    "BridgeStaticSupportSummary",
    "BridgeSupportAnalyzer",
    "ResolvedContextRuntimeRequirements",
    # -- Task 5 prompt-evidence facts -------------------------------------- #
    "PromptUntrustedBlockRef",
    "PromptAssemblyRecord",
    "ExecutionEvidenceOrdinalToken",
    "BridgeEvidenceRecorded",
    # -- Task 5 cumulative Phase-2 runtime registry ------------------------ #
    "Phase2RuntimeRegistry",
    "Phase2RuntimeRegistryError",
    "PHASE2_RUNTIME_MODELS",
    "PHASE2_PUBLIC_MODELS",  # noqa: F822 — provided lazily via module __getattr__
    "PHASE2_BASE_REGISTRY_DIGEST",
    "phase2_runtime_registry",
]


# --------------------------------------------------------------------------- #
# Strict runtime base (NOT a ContractModel — see module docstring)            #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    """Strict, frozen, closed audit-fact base.

    Mirrors the Phase 1 strict config (``extra='forbid'`` / ``strict=True`` /
    ``frozen=True``) without inheriting the registered-contract identity of
    :class:`~guanlan_v2.orchestration.digest.ContractModel`; the Phase 1
    completeness firewall therefore never discovers a Phase 2 audit fact.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# Audit-only facts                                                            #
# --------------------------------------------------------------------------- #
class NamedEvidenceDigest(_StrictModel):
    """One named digest of evidence bound to a refusal (a reference, not content).

    ``name`` is the semantic slot (e.g. ``"input_snapshot"``) and ``digest`` is the
    ``sha256+cjson-v1`` digest of that evidence — never the evidence bytes.
    """

    schema_version: Literal["1"] = "1"
    name: NonEmptyStr
    digest: DigestHex


class GenericRefusalDetails(_StrictModel):
    """The default safe, generic refusal detail — carries no raw rejected bytes.

    A minimal, non-sensitive human/machine summary of *why* an append was refused.
    Deliberately flat (only strings): a rejected payload's raw bytes or secrets are
    never fields, and never enter a refusal record.
    """

    schema_version: Literal["1"] = "1"
    summary: NonEmptyStr
    category: NonEmptyStr = "generic"


def _refusal_semantic_payload(
    *,
    reason_code: str,
    attempted_capability_ref: CapabilityRef | None,
    attempted_schema_ref: SchemaRef | None,
    attempted_namespace: str | None,
    detail_schema_ref: SchemaRef,
    detail_payload_ref: PayloadRef,
    evidence_digests: tuple[NamedEvidenceDigest, ...],
    idempotency_key: str,
) -> dict[str, Any]:
    """The canonical *semantic* mapping a refusal record seals its digest over.

    Audit identity/time (``record_id`` / ``occurred_at``) and the self-digest are
    excluded; nested Phase 1 refs (``SchemaRef`` / ``PayloadRef`` / ``CapabilityRef``)
    project themselves (so ``detail_payload_ref.object_id`` — audit-only — never
    moves the digest), and ``NamedEvidenceDigest`` (a ``_StrictModel``, not a
    ``DigestModel``) is flattened to ``{name, digest}`` so
    :func:`content_digest` sees only projectable values.
    """
    return {
        "schema_version": "1",
        "reason_code": reason_code,
        "attempted_capability_ref": attempted_capability_ref,
        "attempted_schema_ref": attempted_schema_ref,
        "attempted_namespace": attempted_namespace,
        "detail_schema_ref": detail_schema_ref,
        "detail_payload_ref": detail_payload_ref,
        "evidence_digests": [
            {"name": e.name, "digest": e.digest} for e in evidence_digests
        ],
        "idempotency_key": idempotency_key,
    }


class EventRefusalRecord(_StrictModel):
    """One audit-only record that an append was refused.

    Binds the reason/code, the attempted capability/schema/namespace, a *typed
    detail* (``detail_schema_ref`` + an ``audit``-namespace ``detail_payload_ref``),
    canonically ordered named evidence digests and audit identity/time. It is
    **not** a :class:`~guanlan_v2.orchestration.events.RunEvent`: it carries no
    journal/visible sequence and is never readable through the main/public stream.
    The raw rejected payload/secret is never a field.

    Use :meth:`build`, which seals ``record_digest`` from the semantic projection;
    the ``model_validator`` recomputes and re-verifies it on load.
    """

    schema_version: Literal["1"] = "1"

    # -- audit identity / time (excluded from the semantic digest) ---------- #
    record_id: NonEmptyStr
    occurred_at: UtcDateTime

    # -- semantic identity -------------------------------------------------- #
    reason_code: NonEmptyStr
    attempted_capability_ref: CapabilityRef | None = None
    attempted_schema_ref: SchemaRef | None = None
    attempted_namespace: PayloadNamespace | None = None
    detail_schema_ref: SchemaRef
    detail_payload_ref: PayloadRef
    evidence_digests: tuple[NamedEvidenceDigest, ...] = ()
    idempotency_key: NonEmptyStr

    # -- self-sealed semantic digest ---------------------------------------- #
    record_digest: DigestHex

    #: audit identity/time — never part of the semantic digest.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"record_id", "occurred_at"})

    def _recompute_digest(self) -> str:
        return content_digest(
            _refusal_semantic_payload(
                reason_code=self.reason_code,
                attempted_capability_ref=self.attempted_capability_ref,
                attempted_schema_ref=self.attempted_schema_ref,
                attempted_namespace=self.attempted_namespace,
                detail_schema_ref=self.detail_schema_ref,
                detail_payload_ref=self.detail_payload_ref,
                evidence_digests=self.evidence_digests,
                idempotency_key=self.idempotency_key,
            )
        )

    @model_validator(mode="after")
    def _verify(self) -> "EventRefusalRecord":
        if self.detail_payload_ref.namespace != "audit":
            raise ValueError(
                "EventRefusalRecord.detail_payload_ref must live in the 'audit' "
                f"namespace; got {self.detail_payload_ref.namespace!r}"
            )
        names = [e.name for e in self.evidence_digests]
        if names != sorted(names):
            raise ValueError("evidence_digests must be canonically ordered by name")
        if len(set(names)) != len(names):
            raise ValueError("evidence_digests must be unique by name")
        if self.record_digest != self._recompute_digest():
            raise ValueError(
                "declared record_digest does not match the recomputed semantic digest"
            )
        return self

    @classmethod
    def build(
        cls,
        *,
        record_id: str,
        occurred_at: Any,
        reason_code: str,
        detail_schema_ref: SchemaRef,
        detail_payload_ref: PayloadRef,
        idempotency_key: str,
        attempted_capability_ref: CapabilityRef | None = None,
        attempted_schema_ref: SchemaRef | None = None,
        attempted_namespace: str | None = None,
        evidence_digests: tuple[NamedEvidenceDigest, ...] = (),
    ) -> "EventRefusalRecord":
        """Seal an :class:`EventRefusalRecord`: compute ``record_digest`` then build."""
        evidence = tuple(evidence_digests)
        digest = content_digest(
            _refusal_semantic_payload(
                reason_code=reason_code,
                attempted_capability_ref=attempted_capability_ref,
                attempted_schema_ref=attempted_schema_ref,
                attempted_namespace=attempted_namespace,
                detail_schema_ref=detail_schema_ref,
                detail_payload_ref=detail_payload_ref,
                evidence_digests=evidence,
                idempotency_key=idempotency_key,
            )
        )
        return cls(
            record_id=record_id,
            occurred_at=occurred_at,
            reason_code=reason_code,
            attempted_capability_ref=attempted_capability_ref,
            attempted_schema_ref=attempted_schema_ref,
            attempted_namespace=attempted_namespace,
            detail_schema_ref=detail_schema_ref,
            detail_payload_ref=detail_payload_ref,
            evidence_digests=evidence,
            idempotency_key=idempotency_key,
            record_digest=digest,
        )


# --------------------------------------------------------------------------- #
# Audit-detail registry — the separate, self-contained detail-schema authority #
# --------------------------------------------------------------------------- #
class AuditDetailRegistryError(Exception):
    """Base for audit-detail registry errors."""


class UnknownAuditDetailSchema(AuditDetailRegistryError):
    """A detail :class:`SchemaRef` resolves to no registered audit-detail schema."""


class AuditDetailSealedError(AuditDetailRegistryError):
    """A registration was attempted on a sealed audit-detail registry."""


class AuditDetailRegistry:
    """A tiny sealable registry of strict, versioned *audit-detail* schemas.

    Mirrors the Phase 1 :class:`~guanlan_v2.orchestration.schema_registry.SchemaRegistry`
    shape (register / seal / resolve / validate / ``registry_digest``) but for the
    ``_StrictModel`` audit-detail facts — deliberately separate from the main
    payload schema registry so audit details never enter the public model surface.
    A later reviewed registry that adds a Phase-3 typed detail schema is a *new
    exact ``registry_digest``*, never a mutable "latest".
    """

    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}
        self._sealed = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    def register(self, model: type[BaseModel]) -> SchemaRef:
        if self._sealed:
            raise AuditDetailSealedError("cannot register into a sealed audit-detail registry")
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise AuditDetailRegistryError(f"audit-detail schema must be a model, got {model!r}")
        field = model.model_fields.get("schema_version")
        if field is None or not isinstance(field.default, str) or not field.default:
            raise AuditDetailRegistryError(
                f"{model.__name__} must declare a non-empty 'schema_version' default"
            )
        version = field.default
        key = f"{model.__name__}@{version}"
        existing = self._models.get(key)
        if existing is not None and existing is not model:
            raise AuditDetailRegistryError(f"audit-detail key {key!r} already bound to a different model")
        self._models[key] = model
        return SchemaRef(name=model.__name__, version=version)

    def seal(self) -> None:
        self._sealed = True

    def resolve(self, ref: SchemaRef) -> type[BaseModel]:
        model = self._models.get(ref.key)
        if model is None:
            raise UnknownAuditDetailSchema(f"no audit-detail schema registered for {ref.key!r}")
        return model

    def validate(self, ref: SchemaRef, payload: Any) -> BaseModel:
        """Validate ``payload`` against the audit-detail schema resolved from ``ref``."""
        model = self.resolve(ref)
        if isinstance(payload, Mapping):
            declared = payload.get("schema_version")
            if declared is not None and declared != ref.version:
                raise AuditDetailRegistryError(
                    f"detail schema_version {declared!r} does not match {ref.key!r}"
                )
            return model.model_validate(payload)
        if isinstance(payload, model):
            return payload
        return model.model_validate(payload)

    @property
    def registry_digest(self) -> DigestHex:
        """Registration-order-independent digest over the sorted schema manifest."""
        entries = sorted(
            [key, content_digest(model.model_json_schema())]
            for key, model in self._models.items()
        )
        return content_digest(entries)


def default_audit_detail_registry() -> AuditDetailRegistry:
    """A fresh (unsealed) audit-detail registry with the reviewed base detail schemas.

    Registers :class:`GenericRefusalDetails` and :class:`NamedEvidenceDigest`.
    Returned unsealed so a test/later phase may register an additional reviewed
    Phase-3 detail schema and seal a *new exact digest*; callers that want the base
    surface can seal it themselves.
    """
    reg = AuditDetailRegistry()
    reg.register(GenericRefusalDetails)
    reg.register(NamedEvidenceDigest)
    return reg


# ============================================================================ #
# Task 5 — static runtime support profile + control / prompt-evidence facts    #
# ============================================================================ #
#
# Why these stay ``_StrictModel`` (NOT ``ContractModel``)
# ------------------------------------------------------
# The Phase-1 completeness firewall (``test_contract_completeness.py``) walks the
# WHOLE package and requires every module that *defines* a public ``ContractModel``
# subclass to appear in its frozen ``PHASE1_MODULES`` tuple. Making a Task-5 fact a
# ``ContractModel`` would therefore either fail that Phase-1 firewall or force a
# Phase-1 test/registry fork — exactly what the "never mutate Phase 1" constraint
# forbids. So every registered Task-5 fact mirrors the Phase-1 strict config
# (``extra='forbid'`` / ``strict=True`` / ``frozen=True``) through ``_StrictModel``
# and self-seals its own digest with :func:`content_digest` over an explicit
# semantic projection (the Task-2 :class:`EventRefusalRecord` idiom), and the
# cumulative :func:`phase2_runtime_registry` is a *new* registry type that accepts
# these strict versioned models without demanding ``ContractModel`` identity.
#
# The refusal facts (``NamedEvidenceDigest`` / ``GenericRefusalDetails`` /
# ``EventRefusalRecord``) are the SAME Task-2 classes; the cumulative registry adds
# them by identity (no redefinition), keeping the Task-2 ABI exactly intact.

_ZERO_DIGEST = "0" * 64


def _flatten_named_digest(e: "NamedEvidenceDigest") -> dict[str, str]:
    """Project a ``NamedEvidenceDigest`` (a ``_StrictModel``) for canonical JSON."""
    return {"name": e.name, "digest": e.digest}


# --------------------------------------------------------------------------- #
# StaticRuntimeProfile — the closed v1 feature matrix                         #
# --------------------------------------------------------------------------- #
#: canonical closed feature-matrix constants (also the checker's supported set).
_PROFILE_PLAN_SOURCES: tuple[PlanSource, ...] = (
    PlanSource.PRESET,
    PlanSource.PRESET_FALLBACK,
    PlanSource.DYNAMIC,
)
_PROFILE_EXECUTION_KINDS: tuple[ExecutionKind, ...] = (
    ExecutionKind.LLM,
    ExecutionKind.DETERMINISTIC,
)
_PROFILE_DEPENDENCY_POLICIES: tuple[DependencyPolicy, ...] = (
    DependencyPolicy.BLOCK,
    DependencyPolicy.DEGRADE,
    DependencyPolicy.SKIP,
)
_PROFILE_CARDINALITIES: tuple[str, ...] = ("many", "one")
_PROFILE_PRE_INPUT_MODES: tuple[str, ...] = ("memory_refs_v1", "none")


class StaticRuntimeProfile(_StrictModel):
    """The closed v1 static-runtime feature matrix (self-sealed ``profile_digest``).

    Every capability the static runtime supports is an *explicit fixed field*: a
    ``PlanSource`` set that never includes ``BOOTSTRAP``; the two execution kinds;
    the three dependency policies; both input cardinalities; and the closed
    two-phase bridge matrix (``memory_refs_v1`` / ``none`` pre-input then
    ``static_prefetch_v1``). Everything unsupported is a ``Literal[False]`` /
    ``Literal[1]`` switch so an unknown or future feature can never be *assumed*
    supported (invariant 5). ``profile_digest`` seals the whole matrix.
    """

    schema_version: Literal["1"] = "1"
    profile_id: Literal["static-runtime"] = "static-runtime"
    profile_version: Literal["1"] = "1"

    supported_plan_sources: tuple[PlanSource, ...] = _PROFILE_PLAN_SOURCES
    supported_execution_kinds: tuple[ExecutionKind, ...] = _PROFILE_EXECUTION_KINDS
    supported_dependency_policies: tuple[DependencyPolicy, ...] = _PROFILE_DEPENDENCY_POLICIES
    supported_cardinalities: tuple[Cardinality, ...] = ("one", "many")

    supports_bootstrap: Literal[False] = False
    supports_conditions: Literal[False] = False
    supports_reducers: Literal[False] = False
    supports_multi_writer: Literal[False] = False
    supports_debates: Literal[False] = False
    supports_gates: Literal[False] = False
    supports_stop_conditions: Literal[False] = False
    supports_retries: Literal[False] = False
    max_attempts_supported: Literal[1] = 1

    bridge_pre_input_modes: tuple[str, ...] = ("none", "memory_refs_v1")
    bridge_lifecycle: Literal["static_prefetch_v1"] = "static_prefetch_v1"
    max_prompt_assemblies_per_llm_node: Literal[1] = 1
    max_model_invocations_per_llm_node: Literal[1] = 1

    profile_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "supported_plan_sources": [s.value for s in self.supported_plan_sources],
            "supported_execution_kinds": [k.value for k in self.supported_execution_kinds],
            "supported_dependency_policies": [
                p.value for p in self.supported_dependency_policies
            ],
            "supported_cardinalities": list(self.supported_cardinalities),
            "supports_bootstrap": self.supports_bootstrap,
            "supports_conditions": self.supports_conditions,
            "supports_reducers": self.supports_reducers,
            "supports_multi_writer": self.supports_multi_writer,
            "supports_debates": self.supports_debates,
            "supports_gates": self.supports_gates,
            "supports_stop_conditions": self.supports_stop_conditions,
            "supports_retries": self.supports_retries,
            "max_attempts_supported": self.max_attempts_supported,
            "bridge_pre_input_modes": list(self.bridge_pre_input_modes),
            "bridge_lifecycle": self.bridge_lifecycle,
            "max_prompt_assemblies_per_llm_node": self.max_prompt_assemblies_per_llm_node,
            "max_model_invocations_per_llm_node": self.max_model_invocations_per_llm_node,
        }

    @model_validator(mode="after")
    def _verify(self) -> "StaticRuntimeProfile":
        if tuple(sorted(s.value for s in self.supported_plan_sources)) != tuple(
            sorted(s.value for s in _PROFILE_PLAN_SOURCES)
        ):
            raise ValueError("supported_plan_sources must equal the closed static matrix")
        if PlanSource.BOOTSTRAP in self.supported_plan_sources:
            raise ValueError("BOOTSTRAP is never a supported static plan source")
        if tuple(sorted(k.value for k in self.supported_execution_kinds)) != tuple(
            sorted(k.value for k in _PROFILE_EXECUTION_KINDS)
        ):
            raise ValueError("supported_execution_kinds must equal the closed static matrix")
        if tuple(sorted(p.value for p in self.supported_dependency_policies)) != tuple(
            sorted(p.value for p in _PROFILE_DEPENDENCY_POLICIES)
        ):
            raise ValueError("supported_dependency_policies must equal the closed static matrix")
        if tuple(sorted(self.supported_cardinalities)) != _PROFILE_CARDINALITIES:
            raise ValueError("supported_cardinalities must equal {'one','many'}")
        if tuple(sorted(self.bridge_pre_input_modes)) != _PROFILE_PRE_INPUT_MODES:
            raise ValueError("bridge_pre_input_modes must equal {'none','memory_refs_v1'}")
        if self.profile_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared profile_digest does not match the canonical matrix")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "StaticRuntimeProfile":
        stub = {
            "schema_version": "1",
            "profile_id": "static-runtime",
            "profile_version": "1",
            "supported_plan_sources": _PROFILE_PLAN_SOURCES,
            "supported_execution_kinds": _PROFILE_EXECUTION_KINDS,
            "supported_dependency_policies": _PROFILE_DEPENDENCY_POLICIES,
            "supported_cardinalities": ("one", "many"),
            "supports_bootstrap": False,
            "supports_conditions": False,
            "supports_reducers": False,
            "supports_multi_writer": False,
            "supports_debates": False,
            "supports_gates": False,
            "supports_stop_conditions": False,
            "supports_retries": False,
            "max_attempts_supported": 1,
            "bridge_pre_input_modes": ("none", "memory_refs_v1"),
            "bridge_lifecycle": "static_prefetch_v1",
            "max_prompt_assemblies_per_llm_node": 1,
            "max_model_invocations_per_llm_node": 1,
        }
        stub.update(fields)
        # profile is closed: compute the digest over the canonical projection.
        provisional = cls.model_construct(**stub, profile_digest=_ZERO_DIGEST)
        stub["profile_digest"] = content_digest(provisional._semantic_payload())
        return cls(**stub)


def static_runtime_profile() -> StaticRuntimeProfile:
    """The single canonical static-runtime v1 profile (a fresh, equal instance)."""
    return StaticRuntimeProfile.build()


# --------------------------------------------------------------------------- #
# RuntimeSupportIssue                                                         #
# --------------------------------------------------------------------------- #
class RuntimeSupportIssue(_StrictModel):
    """One deterministic runtime-support finding: stable code + model path + why."""

    schema_version: Literal["1"] = "1"
    code: NonEmptyStr
    model_path: NonEmptyStr
    explanation: NonEmptyStr
    node_id: LogicalId | None = None

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.code, self.node_id or "", self.model_path, self.explanation)


# --------------------------------------------------------------------------- #
# ExecutionBridgeDescriptor                                                   #
# --------------------------------------------------------------------------- #
class ExecutionBridgeDescriptor(_StrictModel):
    """A reviewed static-prefetch execution-bridge descriptor (guardrail material).

    Its full canonical JSON is stored as a catalog ``kind="guardrail"`` material and
    indexed by :class:`~guanlan_v2.orchestration.catalog_runtime.CatalogRuntime`
    only when it carries the ``descriptor_kind="execution_bridge"`` marker. There is
    no caller override / negative predicate: a descriptor *activates* for a
    ``WorkerSpec`` when **any** listed activation capability is in its allowlist or
    **any** listed read category is in its read categories, and at least one
    activation tuple is non-empty.
    """

    schema_version: Literal["1"] = "1"
    descriptor_kind: Literal["execution_bridge"] = "execution_bridge"
    bridge_id: LogicalId
    bridge_version: NonEmptyStr
    priority: NonNegativeInt
    provider_handler_ref: ContentRef
    config_ref: ContentRef
    support_analyzer_ref: ContentRef
    config_schema_ref: SchemaRef
    activation_capability_refs: tuple[CapabilityRef, ...] = ()
    activation_read_categories: tuple[ReadCategory, ...] = ()
    supported_execution_kinds: tuple[ExecutionKind, ...]
    pre_input_kind: Literal["none", "memory_refs_v1"]
    lifecycle: Literal["static_prefetch_v1"] = "static_prefetch_v1"
    required: Literal[True] = True

    @model_validator(mode="after")
    def _verify(self) -> "ExecutionBridgeDescriptor":
        cap_keys = [(c.id, c.version) for c in self.activation_capability_refs]
        if len(set(cap_keys)) != len(cap_keys):
            raise ValueError("activation_capability_refs must be unique")
        if cap_keys != sorted(cap_keys):
            raise ValueError("activation_capability_refs must be canonically sorted")
        cats = list(self.activation_read_categories)
        if len(set(cats)) != len(cats):
            raise ValueError("activation_read_categories must be unique")
        if cats != sorted(cats):
            raise ValueError("activation_read_categories must be canonically sorted")
        if not self.activation_capability_refs and not self.activation_read_categories:
            raise ValueError("at least one activation tuple (capability/category) must be non-empty")
        kinds = [k.value for k in self.supported_execution_kinds]
        if not kinds:
            raise ValueError("supported_execution_kinds must be non-empty")
        if len(set(kinds)) != len(kinds):
            raise ValueError("supported_execution_kinds must be unique")
        if kinds != sorted(kinds):
            raise ValueError("supported_execution_kinds must be canonically sorted")
        return self

    def activates_for(
        self, *, capability_ids: frozenset[str], read_categories: frozenset[str]
    ) -> bool:
        """True iff any activation capability id or read category matches the worker."""
        if any(c.id in capability_ids for c in self.activation_capability_refs):
            return True
        if any(cat in read_categories for cat in self.activation_read_categories):
            return True
        return False


# --------------------------------------------------------------------------- #
# BridgeStaticSupportSummary + analyzer protocol                              #
# --------------------------------------------------------------------------- #
class BridgeStaticSupportSummary(_StrictModel):
    """A pure analyzer's static support summary for one (node, bridge) pair.

    Bound to the exact candidate digest, PlanDraft node id/params digest and
    WorkerSpec identity plus the descriptor/config/provider/analyzer refs. Carries
    the canonical allowed capability refs and the strict numeric bounds the
    REQUIRED/FORBIDDEN arithmetic uses; ``dynamic_or_model_selected_calls`` is
    closed ``False`` (static v1 never model-selects a call). ``summary_digest``
    seals the whole fact.
    """

    schema_version: Literal["1"] = "1"
    candidate_plan_digest: DigestHex
    node_id: LogicalId
    node_params_digest: DigestHex
    worker_id: LogicalId
    worker_digest: DigestHex
    bridge_id: LogicalId
    descriptor_ref: ContentRef
    config_ref: ContentRef
    provider_ref: ContentRef
    analyzer_ref: ContentRef
    allowed_capability_refs: tuple[CapabilityRef, ...] = ()
    min_finalized_tool_calls_on_success: NonNegativeInt
    max_capability_invocations: NonNegativeInt
    pre_input_kind: Literal["none", "memory_refs_v1"]
    lifecycle: Literal["static_prefetch_v1"] = "static_prefetch_v1"
    dynamic_or_model_selected_calls: Literal[False] = False
    summary_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_plan_digest": self.candidate_plan_digest,
            "node_id": self.node_id,
            "node_params_digest": self.node_params_digest,
            "worker_id": self.worker_id,
            "worker_digest": self.worker_digest,
            "bridge_id": self.bridge_id,
            "descriptor_ref": self.descriptor_ref,
            "config_ref": self.config_ref,
            "provider_ref": self.provider_ref,
            "analyzer_ref": self.analyzer_ref,
            "allowed_capability_refs": list(self.allowed_capability_refs),
            "min_finalized_tool_calls_on_success": self.min_finalized_tool_calls_on_success,
            "max_capability_invocations": self.max_capability_invocations,
            "pre_input_kind": self.pre_input_kind,
            "lifecycle": self.lifecycle,
            "dynamic_or_model_selected_calls": self.dynamic_or_model_selected_calls,
        }

    @model_validator(mode="after")
    def _verify(self) -> "BridgeStaticSupportSummary":
        keys = [(c.id, c.version) for c in self.allowed_capability_refs]
        if len(set(keys)) != len(keys):
            raise ValueError("allowed_capability_refs must be unique")
        if keys != sorted(keys):
            raise ValueError("allowed_capability_refs must be canonically sorted")
        if self.min_finalized_tool_calls_on_success > self.max_capability_invocations and (
            self.max_capability_invocations != 0 or self.min_finalized_tool_calls_on_success != 0
        ):
            # a guaranteed minimum can never exceed the maximum invocation ceiling.
            raise ValueError(
                "min_finalized_tool_calls_on_success cannot exceed max_capability_invocations"
            )
        if self.summary_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared summary_digest does not match the canonical projection")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "BridgeStaticSupportSummary":
        provisional = cls.model_construct(**fields, summary_digest=_ZERO_DIGEST)
        digest = content_digest(provisional._semantic_payload())
        return cls(**fields, summary_digest=digest)


@runtime_checkable
class BridgeSupportAnalyzer(Protocol):
    """A pure, deterministic, clock/store/gateway-free bridge support analyzer.

    Receives only the already-computed candidate digest, the Phase-1-validated
    PlanDraft node, the WorkerSpec and the parsed descriptor + canonical config
    bytes, and returns a :class:`BridgeStaticSupportSummary`. It performs no I/O,
    reads no clock and calls no gateway; support analysis happens *before*
    reservation/approval/freeze, so it never claims to consume a frozen Plan. A
    claimed bound unsupported by the config/worker allowlist is an analyzer failure,
    not authority (the checker re-verifies the summary against the worker allowlist).
    """

    def analyze(
        self,
        *,
        candidate_plan_digest: str,
        node: "PlanNode",
        worker: "WorkerSpec",
        descriptor: ExecutionBridgeDescriptor,
        descriptor_ref: ContentRef,
        config_bytes: bytes,
    ) -> BridgeStaticSupportSummary:
        ...


# --------------------------------------------------------------------------- #
# ResolvedContextRuntimeRequirements (internal — produced by Task 6)          #
# --------------------------------------------------------------------------- #
class ResolvedContextRuntimeRequirements(_StrictModel):
    """A ``ContextRuntimeRequirements`` already resolved from the payload store.

    Produced only by Task 6 after registry/PayloadStore resolution and validation:
    it pairs the exact main :class:`TypedPayloadRef` the ContextSnapshot carried
    with the resolved, validated :class:`ContextRuntimeRequirements` fact. It is a
    checker *input*, never a persisted registered payload.
    """

    typed_ref: TypedPayloadRef
    fact: ContextRuntimeRequirements

    @model_validator(mode="after")
    def _verify(self) -> "ResolvedContextRuntimeRequirements":
        if self.typed_ref.payload_ref.namespace != "main":
            raise ValueError("resolved requirements typed_ref must be main-namespace")
        if (
            self.typed_ref.schema_ref.name != "ContextRuntimeRequirements"
            or self.typed_ref.schema_ref.version != "1"
        ):
            raise ValueError("typed_ref must resolve ContextRuntimeRequirements@1")
        if self.typed_ref.payload_ref.content_digest != self.fact.requirements_digest:
            raise ValueError("typed_ref content digest must equal the fact's requirements_digest")
        return self


# --------------------------------------------------------------------------- #
# RuntimeSupportReport                                                        #
# --------------------------------------------------------------------------- #
class RuntimeSupportReport(_StrictModel):
    """The frozen result of :func:`~guanlan_v2.orchestration.runtime_support.check_runtime_support`.

    ``supported`` iff ``issues`` is empty. Binds the candidate/phase-1-report/
    profile/catalog/registry digests it validated, the exact
    context-runtime-requirements typed ref + digest when present, the canonically
    ordered active bridge descriptor/config/provider/analyzer ``ContentRef``s and
    the complete canonically ordered per-node :class:`BridgeStaticSupportSummary`
    values embedded directly. ``report_digest`` seals the whole report so Tasks 6/8
    bind one digest.
    """

    schema_version: Literal["1"] = "1"
    checker_version: Literal["static-runtime-checker-v1"] = "static-runtime-checker-v1"
    supported: bool
    issues: tuple[RuntimeSupportIssue, ...] = ()
    candidate_plan_digest: DigestHex
    phase1_report_digest: DigestHex
    runtime_profile_digest: DigestHex
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    context_requirements_ref: TypedPayloadRef | None = None
    context_requirements_digest: DigestHex | None = None
    active_bridge_descriptor_refs: tuple[ContentRef, ...] = ()
    active_bridge_config_refs: tuple[ContentRef, ...] = ()
    active_bridge_provider_refs: tuple[ContentRef, ...] = ()
    active_bridge_analyzer_refs: tuple[ContentRef, ...] = ()
    bridge_support_summaries: tuple[BridgeStaticSupportSummary, ...] = ()
    report_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checker_version": self.checker_version,
            "supported": self.supported,
            "issues": [
                {
                    "code": i.code,
                    "model_path": i.model_path,
                    "explanation": i.explanation,
                    "node_id": i.node_id,
                }
                for i in self.issues
            ],
            "candidate_plan_digest": self.candidate_plan_digest,
            "phase1_report_digest": self.phase1_report_digest,
            "runtime_profile_digest": self.runtime_profile_digest,
            "catalog_digest": self.catalog_digest,
            "schema_registry_digest": self.schema_registry_digest,
            "context_requirements_ref": self.context_requirements_ref,
            "context_requirements_digest": self.context_requirements_digest,
            "active_bridge_descriptor_refs": list(self.active_bridge_descriptor_refs),
            "active_bridge_config_refs": list(self.active_bridge_config_refs),
            "active_bridge_provider_refs": list(self.active_bridge_provider_refs),
            "active_bridge_analyzer_refs": list(self.active_bridge_analyzer_refs),
            "bridge_support_summaries": [s.summary_digest for s in self.bridge_support_summaries],
        }

    @staticmethod
    def _sorted_ref_keys(refs: tuple[ContentRef, ...]) -> list[tuple[str, str, str]]:
        return [(r.id, r.version, r.content_digest) for r in refs]

    @model_validator(mode="after")
    def _verify(self) -> "RuntimeSupportReport":
        if self.supported != (len(self.issues) == 0):
            raise ValueError("supported must be true iff issues is empty")
        issue_keys = [i.sort_key for i in self.issues]
        if issue_keys != sorted(issue_keys):
            raise ValueError("issues must be canonically ordered")
        for field_name, refs in (
            ("active_bridge_descriptor_refs", self.active_bridge_descriptor_refs),
            ("active_bridge_config_refs", self.active_bridge_config_refs),
            ("active_bridge_provider_refs", self.active_bridge_provider_refs),
            ("active_bridge_analyzer_refs", self.active_bridge_analyzer_refs),
        ):
            keys = self._sorted_ref_keys(refs)
            if keys != sorted(keys):
                raise ValueError(f"{field_name} must be canonically ordered")
            if len(set(keys)) != len(keys):
                raise ValueError(f"{field_name} must be duplicate-free")
        summ_keys = [(s.node_id, s.bridge_id) for s in self.bridge_support_summaries]
        if summ_keys != sorted(summ_keys):
            raise ValueError("bridge_support_summaries must be canonically ordered by (node, bridge)")
        if len(set(summ_keys)) != len(summ_keys):
            raise ValueError("bridge_support_summaries must be duplicate-free by (node, bridge)")
        if (self.context_requirements_ref is None) != (self.context_requirements_digest is None):
            raise ValueError(
                "context_requirements_ref and context_requirements_digest are both-set or both-none"
            )
        if self.context_requirements_ref is not None:
            if self.context_requirements_ref.payload_ref.content_digest != self.context_requirements_digest:
                raise ValueError("context_requirements_ref content digest must equal the bound digest")
        if self.report_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared report_digest does not match the canonical projection")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RuntimeSupportReport":
        provisional = cls.model_construct(**fields, report_digest=_ZERO_DIGEST)
        digest = content_digest(provisional._semantic_payload())
        return cls(**fields, report_digest=digest)


# --------------------------------------------------------------------------- #
# Control facts (Tasks 6/8 own transitions, not alternate definitions)        #
# --------------------------------------------------------------------------- #
class AdmissionCandidate(_StrictModel):
    """A support-validated candidate awaiting budget reservation (no dispatch grant)."""

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    support_report_digest: DigestHex
    phase1_report_digest: DigestHex
    runtime_profile_digest: DigestHex
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex
    context_requirements_digest: DigestHex | None = None


class AdmissionInvalidated(_StrictModel):
    """An admitted candidate/reservation invalidated by drift of an authoritative input.

    Binds the candidate + reservation, the exact drifted authoritative input
    names/digests (reusing the Task-2 :class:`NamedEvidenceDigest`) and a reviewed
    reason code. It grants no dispatch authority — it only records why admission was
    revoked.
    """

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    reservation_id: NonEmptyStr
    drifted_inputs: tuple[NamedEvidenceDigest, ...]
    reason_code: NonEmptyStr

    @model_validator(mode="after")
    def _verify(self) -> "AdmissionInvalidated":
        names = [d.name for d in self.drifted_inputs]
        if not names:
            raise ValueError("drifted_inputs must name at least one drifted authoritative input")
        if names != sorted(names):
            raise ValueError("drifted_inputs must be canonically ordered by name")
        if len(set(names)) != len(names):
            raise ValueError("drifted_inputs must be unique by name")
        return self


class PlanAdmitted(_StrictModel):
    """The terminal admission fact: a candidate that reserved budget and was admitted."""

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    plan_digest: DigestHex
    support_report_digest: DigestHex
    reservation_id: NonEmptyStr
    runtime_profile_digest: DigestHex
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex


class RunResult(_StrictModel):
    """The terminal result of an admitted run (Task 8 owns the transition to it)."""

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    plan_digest: DigestHex
    terminal_status: Literal["completed", "degraded", "incomplete", "failed", "cancelled"]
    settled_tokens: NonNegativeInt = 0
    settled_llm_invocations: NonNegativeInt = 0


# --------------------------------------------------------------------------- #
# Prompt-evidence facts                                                       #
# --------------------------------------------------------------------------- #
_PROMPT_MEDIA_TYPES = ("application/json", "text/markdown", "text/plain")


class PromptUntrustedBlockRef(_StrictModel):
    """One ordered untrusted-input block envelope inside a prompt assembly.

    A one-based strict positive ``ordinal`` (the first block is ``1``), an exact
    main :class:`TypedPayloadRef` (dereference/audit identity is the payload object
    id; SchemaRef + namespace/content is semantic), a reviewed media type, a bounded
    non-negative rendered length and a verified ``block_digest``. It contains a *ref*
    to the untrusted bytes, never the bytes.
    """

    schema_version: Literal["1"] = "1"
    ordinal: PositiveInt
    payload_ref: TypedPayloadRef
    media_type: Literal["application/json", "text/markdown", "text/plain"]
    rendered_length: NonNegativeInt
    block_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ordinal": self.ordinal,
            "payload_ref": self.payload_ref,  # TypedPayloadRef projects semantic (no object id)
            "media_type": self.media_type,
            "rendered_length": self.rendered_length,
        }

    @model_validator(mode="after")
    def _verify(self) -> "PromptUntrustedBlockRef":
        if self.payload_ref.payload_ref.namespace != "main":
            raise ValueError("PromptUntrustedBlockRef.payload_ref must be main-namespace")
        if self.block_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared block_digest does not match the canonical projection")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "PromptUntrustedBlockRef":
        provisional = cls.model_construct(**fields, block_digest=_ZERO_DIGEST)
        digest = content_digest(provisional._semantic_payload())
        return cls(**fields, block_digest=digest)


class PromptAssemblyRecord(_StrictModel):
    """Persisted, main-namespace execution evidence of one prompt assembly.

    Binds plan/node/worker, the exact assembler id/version, the system-prompt/
    ordered skill/guardrail identities, canonically named trusted-input digests
    (reusing :class:`NamedEvidenceDigest`), the ordered untrusted blocks, the
    canonical model-request digest and a verified ``assembly_digest``. It holds
    refs/digests, never duplicated raw untrusted bytes; changing a block's
    SchemaRef/content/order changes ``assembly_digest`` while an object-id
    relocation changes only audit/dereference identity.
    """

    schema_version: Literal["1"] = "1"
    plan_digest: DigestHex
    node_id: LogicalId
    worker_id: LogicalId
    assembler_id: LogicalId
    assembler_version: NonEmptyStr
    system_prompt_ref: ContentRef
    skill_refs: tuple[ContentRef, ...] = ()
    guardrail_refs: tuple[ContentRef, ...] = ()
    trusted_input_digests: tuple[NamedEvidenceDigest, ...] = ()
    untrusted_blocks: tuple[PromptUntrustedBlockRef, ...] = ()
    model_request_digest: DigestHex
    assembly_digest: DigestHex

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_digest": self.plan_digest,
            "node_id": self.node_id,
            "worker_id": self.worker_id,
            "assembler_id": self.assembler_id,
            "assembler_version": self.assembler_version,
            "system_prompt_ref": self.system_prompt_ref,
            "skill_refs": list(self.skill_refs),
            "guardrail_refs": list(self.guardrail_refs),
            "trusted_input_digests": [_flatten_named_digest(e) for e in self.trusted_input_digests],
            "untrusted_blocks": [b.block_digest for b in self.untrusted_blocks],
            "model_request_digest": self.model_request_digest,
        }

    @model_validator(mode="after")
    def _verify(self) -> "PromptAssemblyRecord":
        names = [e.name for e in self.trusted_input_digests]
        if names != sorted(names):
            raise ValueError("trusted_input_digests must be canonically ordered by name")
        if len(set(names)) != len(names):
            raise ValueError("trusted_input_digests must be unique by name")
        ordinals = [b.ordinal for b in self.untrusted_blocks]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError("untrusted_blocks ordinals must be 1..N contiguous ascending")
        if self.assembly_digest != content_digest(self._semantic_payload()):
            raise ValueError("declared assembly_digest does not match the canonical projection")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "PromptAssemblyRecord":
        provisional = cls.model_construct(**fields, assembly_digest=_ZERO_DIGEST)
        digest = content_digest(provisional._semantic_payload())
        return cls(**fields, assembly_digest=digest)


class ExecutionEvidenceOrdinalToken(_StrictModel):
    """A stable within-node evidence position token (attempt/call/evidence ordinals)."""

    schema_version: Literal["1"] = "1"
    attempt: PositiveInt
    call_ordinal: PositiveInt
    evidence_ordinal: PositiveInt


class BridgeEvidenceRecorded(_StrictModel):
    """A main-namespace control fact recording one bridge evidence value's position.

    Binds run/plan/node, the exact :class:`ExecutionEvidenceOrdinalToken`
    projection, the within-call role and one main :class:`TypedPayloadRef`. It is
    recovery metadata, not a replacement for the referenced evidence or NodeRun.
    """

    schema_version: Literal["1"] = "1"
    run_id: NonEmptyStr
    plan_digest: DigestHex
    node_id: LogicalId
    ordinal_token: ExecutionEvidenceOrdinalToken
    within_call_role: Literal["provider_prefetch", "tool_result", "memory_prefetch"]
    evidence_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "BridgeEvidenceRecorded":
        if self.evidence_ref.payload_ref.namespace != "main":
            raise ValueError("BridgeEvidenceRecorded.evidence_ref must be main-namespace")
        return self


# --------------------------------------------------------------------------- #
# Cumulative Phase-2 runtime registry                                         #
# --------------------------------------------------------------------------- #
#: the Task-5 (+ Task-2) strict versioned facts the cumulative registry adds on top
#: of the reviewed Phase-1 public models. The Task-2 refusal facts appear here by
#: identity — the cumulative registry adds them WITHOUT redefinition.
PHASE2_RUNTIME_MODELS: tuple[type[BaseModel], ...] = (
    # Task 2 audit-refusal facts (same classes, no redefinition)
    NamedEvidenceDigest,
    GenericRefusalDetails,
    EventRefusalRecord,
    # Task 5 profile + support report
    StaticRuntimeProfile,
    RuntimeSupportIssue,
    RuntimeSupportReport,
    # Task 5 control facts
    AdmissionCandidate,
    AdmissionInvalidated,
    PlanAdmitted,
    RunResult,
    # Task 5 bridge descriptor / summary
    ExecutionBridgeDescriptor,
    BridgeStaticSupportSummary,
    # Task 5 prompt-evidence facts
    PromptUntrustedBlockRef,
    PromptAssemblyRecord,
    BridgeEvidenceRecorded,
    # Task 4 layer-commit payload. Reviewed promotion: Phase 1 deliberately keeps
    # LayerCommit OUT of its payload registry (_R_EVENT_RECORD — event-log regime);
    # the cumulative Phase-2 registry intentionally promotes it to a resolvable
    # payload schema because the runtime pool persists it as a registry-validated
    # PayloadStore payload behind the public LayerCommitted visibility event.
    # CommittedArtifactRef rides nested inside it and is NOT registered separately.
    LayerCommit,
)


class Phase2RuntimeRegistryError(Exception):
    """Base for cumulative Phase-2 runtime-registry errors."""


def _model_schema_version(model: type[BaseModel]) -> str:
    field = model.model_fields.get("schema_version")
    if field is None or not isinstance(field.default, str) or not field.default:
        raise Phase2RuntimeRegistryError(
            f"{model.__name__} must declare a non-empty 'schema_version' default"
        )
    return field.default


class Phase2RuntimeRegistry:
    """A sealed cumulative registry over the Phase-1 public models + Phase-2 facts.

    Accepts any strict, frozen, versioned pydantic model (a Phase-1
    ``ContractModel`` *or* a Phase-2 ``_StrictModel``) — it never demands
    ``ContractModel`` identity, so the Phase-1 completeness firewall stays blind to
    Phase-2 facts. Its manifest / ``registry_digest`` are computed with the *exact*
    Phase-1 :class:`SchemaManifestEntry` + :func:`content_digest` formula, so the
    inherited Phase-1 schema subset is byte-identical to the Phase-1 golden. Sealed
    on build; there is no ``latest`` alias.
    """

    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}
        self._sealed = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    def register(self, model: type[BaseModel]) -> SchemaRef:
        if self._sealed:
            raise Phase2RuntimeRegistryError("cannot register into a sealed runtime registry")
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise Phase2RuntimeRegistryError(f"only pydantic models can be registered; got {model!r}")
        version = _model_schema_version(model)
        key = f"{model.__name__}@{version}"
        existing = self._models.get(key)
        if existing is not None:
            if existing is model:
                return SchemaRef(name=model.__name__, version=version)
            raise Phase2RuntimeRegistryError(
                f"runtime schema key {key!r} already bound to a different model"
            )
        self._models[key] = model
        return SchemaRef(name=model.__name__, version=version)

    def seal(self) -> None:
        self._sealed = True

    def resolve(self, ref: SchemaRef) -> type[BaseModel]:
        model = self._models.get(ref.key)
        if model is None:
            raise Phase2RuntimeRegistryError(f"no runtime schema registered for {ref.key!r}")
        return model

    def validate_payload(self, ref: SchemaRef, payload: Any) -> BaseModel:
        model = self.resolve(ref)
        if isinstance(payload, Mapping):
            declared = payload.get("schema_version")
            if declared is not None and declared != ref.version:
                raise Phase2RuntimeRegistryError(
                    f"payload schema_version {declared!r} does not match {ref.key!r}"
                )
            return model.model_validate(payload)
        if isinstance(payload, model):
            return payload
        return model.model_validate(payload)

    def manifest(self) -> tuple[SchemaManifestEntry, ...]:
        entries = [
            SchemaManifestEntry(
                schema_ref=SchemaRef(name=model.__name__, version=_model_schema_version(model)),
                json_schema_digest=content_digest(model.model_json_schema()),
            )
            for model in self._models.values()
        ]
        entries.sort(key=lambda e: e.key)
        return tuple(entries)

    @property
    def registry_digest(self) -> DigestHex:
        return content_digest(list(self.manifest()))


def _phase1_public_models() -> tuple[type[BaseModel], ...]:
    from guanlan_v2.orchestration.schema_registry import PHASE1_PUBLIC_MODELS

    return tuple(PHASE1_PUBLIC_MODELS)


def phase2_public_models() -> tuple[type[BaseModel], ...]:
    """The reviewed cumulative model set: Phase-1 public models + Phase-2 facts."""
    return _phase1_public_models() + PHASE2_RUNTIME_MODELS


def phase2_runtime_registry(expected_phase1_digest: str) -> Phase2RuntimeRegistry:
    """Build + seal the cumulative Phase-2 runtime registry, pinned to Phase-1.

    Requires the exact Phase-1 ``default_registry().registry_digest`` up front (a
    drifted Phase-1 base is rejected), then registers the reviewed Phase-1 public
    models followed by the Phase-2 runtime/control/prompt-evidence facts and seals.
    Never mutates or regenerates the Phase-1 default registry / golden.
    """
    from guanlan_v2.orchestration.schema_registry import default_registry

    phase1_digest = default_registry().registry_digest
    if expected_phase1_digest != phase1_digest:
        raise Phase2RuntimeRegistryError(
            "phase2_runtime_registry requires the exact Phase-1 base digest "
            f"{phase1_digest!r}; got {expected_phase1_digest!r}"
        )
    reg = Phase2RuntimeRegistry()
    for model in phase2_public_models():
        reg.register(model)
    reg.seal()
    return reg


def _phase2_base_registry_digest() -> DigestHex:
    from guanlan_v2.orchestration.schema_registry import default_registry

    return default_registry().registry_digest


def __getattr__(name: str) -> Any:
    """Lazily expose the cumulative registry constants (keeps import light)."""
    if name == "PHASE2_BASE_REGISTRY_DIGEST":
        return _phase2_base_registry_digest()
    if name == "PHASE2_PUBLIC_MODELS":
        return phase2_public_models()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
