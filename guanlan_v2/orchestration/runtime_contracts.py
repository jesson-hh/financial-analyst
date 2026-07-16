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
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    PayloadNamespace,
    PayloadRef,
    SchemaRef,
)

__all__ = [
    "NamedEvidenceDigest",
    "GenericRefusalDetails",
    "EventRefusalRecord",
    "AuditDetailRegistry",
    "AuditDetailRegistryError",
    "UnknownAuditDetailSchema",
    "AuditDetailSealedError",
    "default_audit_detail_registry",
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
