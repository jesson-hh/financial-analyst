"""Sealable schema registry — ``"Name@version"`` → strict payload model.

The registry is the single authority that maps a :class:`SchemaRef` to the
concrete strict model that validates a payload of that type/version. It is
built empty, populated by registering models, and (in Task 13) ``seal()``-ed so
its content — and therefore its :attr:`SchemaRegistry.registry_digest` — is
frozen for the lifetime of a run.

Design invariants (all enforced here and locked by ``test_schema_registry.py``):

* :meth:`register` reads the model's ``__name__`` and its declared
  ``schema_version`` default; there is no version parameter, so a model can only
  ever be registered under its own version.
* Each public model declares ``schema_version: Literal["N"]``, which Pydantic
  renders as a JSON-Schema ``const``; :meth:`validate_payload` additionally
  rejects a payload whose self-declared ``schema_version`` differs from the
  resolved :class:`SchemaRef`.
* Registering the exact same class under an existing key is idempotent; a
  *different* class under an existing key is a :class:`SchemaConflictError`.
* Unknown refs and extra payload fields fail.
* :meth:`manifest` sorts by schema key and carries each model's canonical
  JSON-schema digest, so :attr:`registry_digest` is independent of registration
  order and changes when any registered model's JSON schema changes.
* After :meth:`seal`, every :meth:`register` raises; reads still work.

This module does **not** populate a global registry at import time — Task 13
owns the reviewed, sealed ``default_registry()``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from guanlan_v2.orchestration.digest import ContractModel, DigestHex, content_digest
from guanlan_v2.orchestration.refs import SchemaManifestEntry, SchemaRef

__all__ = [
    "SchemaRegistry",
    "SchemaRegistryError",
    "UnknownSchemaError",
    "SchemaConflictError",
    "RegistrySealedError",
    "SchemaVersionMismatchError",
    "PHASE1_PUBLIC_MODELS",  # noqa: F822 — provided lazily via module __getattr__ (PEP 562)
    "INTERNAL_MODELS",  # noqa: F822 — provided lazily via module __getattr__ (PEP 562)
    "default_registry",
]


class SchemaRegistryError(Exception):
    """Base class for schema-registry errors."""


class UnknownSchemaError(SchemaRegistryError):
    """A :class:`SchemaRef` resolves to no registered model."""


class SchemaConflictError(SchemaRegistryError):
    """A different model was registered under an already-used schema key."""


class RegistrySealedError(SchemaRegistryError):
    """A registration was attempted on a sealed registry."""


class SchemaVersionMismatchError(SchemaRegistryError):
    """A payload self-declares a ``schema_version`` other than the ref's version."""


def _declared_schema_version(model: type[ContractModel]) -> str:
    """Return the model's declared ``schema_version`` default (e.g. ``"1"``)."""
    field = model.model_fields.get("schema_version")
    if field is None:
        raise SchemaRegistryError(
            f"{model.__name__} must declare a 'schema_version' field to be registered"
        )
    default = field.default
    if not isinstance(default, str) or not default:
        raise SchemaRegistryError(
            f"{model.__name__}.schema_version must have a non-empty string default"
        )
    return default


def _json_schema_digest(model: type[ContractModel]) -> DigestHex:
    """Canonical ``sha256+cjson-v1`` digest of the model's generated JSON schema."""
    return content_digest(model.model_json_schema())


class SchemaRegistry:
    """Mutable-until-sealed registry of strict, versioned payload models."""

    def __init__(self) -> None:
        self._models: dict[str, type[ContractModel]] = {}
        self._sealed: bool = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    # -- mutation ---------------------------------------------------------- #
    def register(self, model: type[ContractModel]) -> SchemaRef:
        """Register ``model`` under ``"{model.__name__}@{schema_version}"``.

        Idempotent for the exact same class; raises :class:`SchemaConflictError`
        for a different class under an existing key, and
        :class:`RegistrySealedError` once the registry is sealed.
        """
        if self._sealed:
            raise RegistrySealedError("cannot register into a sealed registry")
        if not (isinstance(model, type) and issubclass(model, ContractModel)):
            raise SchemaRegistryError(
                "only ContractModel subclasses can be registered as payload schemas; "
                f"got {model!r}"
            )
        name = model.__name__
        version = _declared_schema_version(model)
        key = f"{name}@{version}"
        existing = self._models.get(key)
        if existing is not None:
            if existing is model:
                return SchemaRef(name=name, version=version)  # idempotent
            raise SchemaConflictError(
                f"schema key {key!r} is already registered to a different model "
                f"({existing.__module__}.{existing.__qualname__})"
            )
        self._models[key] = model
        return SchemaRef(name=name, version=version)

    def seal(self) -> None:
        """Freeze the registry: subsequent :meth:`register` calls fail."""
        self._sealed = True

    # -- reads ------------------------------------------------------------- #
    def resolve(self, ref: SchemaRef) -> type[ContractModel]:
        """Return the model registered under ``ref`` or raise :class:`UnknownSchemaError`."""
        model = self._models.get(ref.key)
        if model is None:
            raise UnknownSchemaError(f"no schema registered for {ref.key!r}")
        return model

    def validate_payload(self, ref: SchemaRef, payload: Any) -> ContractModel:
        """Validate ``payload`` against the model resolved from ``ref``.

        Rejects an unknown ref, a payload whose self-declared ``schema_version``
        differs from ``ref.version``, and (via the strict model) extra fields or
        the wrong ``schema_version`` const.
        """
        model = self.resolve(ref)
        if isinstance(payload, Mapping):
            declared = payload.get("schema_version")
            if declared is not None and declared != ref.version:
                raise SchemaVersionMismatchError(
                    f"payload schema_version {declared!r} does not match resolved "
                    f"schema {ref.key!r}"
                )
        return model.model_validate(payload)

    def manifest(self) -> tuple[SchemaManifestEntry, ...]:
        """Return manifest entries sorted by schema key, each with its schema digest."""
        entries = [
            SchemaManifestEntry(
                schema_ref=SchemaRef(name=model.__name__, version=_declared_schema_version(model)),
                json_schema_digest=_json_schema_digest(model),
            )
            for model in self._models.values()
        ]
        entries.sort(key=lambda entry: entry.key)
        return tuple(entries)

    @property
    def registry_digest(self) -> DigestHex:
        """Registration-order-independent digest over the sorted manifest."""
        return content_digest(list(self.manifest()))


# --------------------------------------------------------------------------- #
# Phase-1 reviewed registry population (Task 13)                              #
# --------------------------------------------------------------------------- #
# The reviewed partition of *every* public ``ContractModel`` subclass in the
# Phase-1 modules into exactly one of two buckets. ``PHASE1_PUBLIC_MODELS`` is the
# set of standalone versioned payload / fact schemas the sealed
# :func:`default_registry` resolves a :class:`SchemaRef` to; ``INTERNAL_MODELS``
# is the explicit ``model -> reason`` map of models that are *intentionally not*
# registered, each with a reviewed governance reason. The two are exhaustive and
# disjoint, and ``tests/orchestration/test_contract_completeness.py`` enforces
# that partition so no future public contract silently escapes registration
# review.
#
# The reviewed inclusion rule for ``PHASE1_PUBLIC_MODELS``: a model is a
# *registered payload schema* iff it is a concrete, self-versioned **node-output
# value payload** (a worker product) or a **committed context / memory fact**
# that a runtime persists in the payload store and re-validates from an untrusted
# form via a :class:`SchemaRef`. Everything else public is intentionally internal
# for one of the categorized reasons below.
#
# Imports are performed lazily inside :func:`_load_population` so importing this
# module stays pure (no model graph loaded, no registry built, no import cycle
# with ``schemas`` / ``spec`` / ``data.result`` which import ``SchemaRegistry``).

#: reviewed governance reasons (categories) for intentionally-unregistered models.
_R_ABSTRACT_BASE = (
    "abstract strict contract base; not a concrete payload schema and declares no "
    "schema_version to register under"
)
_R_ADDRESSING_REF = (
    "addressing/authorization ref primitive the registry itself is built from; "
    "registering it in the registry it keys would be circular"
)
_R_VALUE_OBJECT = (
    "embedded instrument value object with no schema_version; only ever validated "
    "transitively inside a parent contract, never resolved by a SchemaRef"
)
_R_GENERIC_ENVELOPE = (
    "generic container parameterized over a registered payload T; the registry "
    "validates the inner payload, not the envelope"
)
_R_NESTED_COMPONENT = (
    "structural sub-record only ever embedded in a parent aggregate; never "
    "independently resolved by a SchemaRef payload ref"
)
_R_ABSTRACT_PIT_BASE = (
    "abstract PIT-row base; concrete data-row payloads subclass it, the base "
    "itself ships no payload"
)
_R_EVENT_RECORD = (
    "event-log / execution record governed by the event self-seal + Phase-2 "
    "EventStore regime, not a node-output payload resolved via the schema registry"
)
_R_RUN_CONTEXT_VALUE = (
    "run-level context / budget accounting value consumed directly as a typed "
    "field of a larger context, not a SchemaRef-addressed payload"
)
_R_CATALOG_GOVERNED = (
    "catalog ABI model validated by build_catalog_snapshot / "
    "validate_catalog_snapshot; a WorkerCatalogSnapshot is catalog-verified, "
    "never treated as a schema-registry payload"
)
_R_PLAN_SURFACE = (
    "plan authorization-surface model validated by the plan structural / catalog / "
    "freeze validators, not resolved as a payload via the schema registry"
)
_R_MIGRATION_ADAPTER = (
    "legacy-migration adapter validated by the migration regime; a Phase-1 "
    "compatibility output, not a shipped node-output payload"
)

#: cache for the lazily-built population (see :func:`_load_population`).
_POPULATION: dict[str, Any] | None = None


def _load_population() -> dict[str, Any]:
    """Build (once) the reviewed ``(public tuple, internal reason map)`` partition.

    Lazily imports the model classes to keep import-time pure and avoid the import
    cycle with modules that import :class:`SchemaRegistry`.
    """
    global _POPULATION
    if _POPULATION is not None:
        return _POPULATION

    from guanlan_v2.orchestration import digest as _digest
    from guanlan_v2.orchestration import refs as _refs
    from guanlan_v2.orchestration import schemas as _schemas
    from guanlan_v2.orchestration import context as _context
    from guanlan_v2.orchestration import events as _events
    from guanlan_v2.orchestration import catalog as _catalog
    from guanlan_v2.orchestration import spec as _spec
    from guanlan_v2.orchestration import migration as _migration
    from guanlan_v2.orchestration.data import symbols as _symbols
    from guanlan_v2.orchestration.data import result as _result

    # -- registered: node-output value payloads + committed context/memory facts.
    public: tuple[type[ContractModel], ...] = (
        # node-output value payloads (worker products)
        _schemas.ResearchPlan,
        _schemas.PortfolioDecision,
        _schemas.SentimentReport,
        # committed memory facts
        _context.MemoryRecordRef,
        _context.EmptyMemorySnapshot,
        _context.EmptyMemorySelection,
        # committed frozen context / per-node input snapshots
        _context.ContextSnapshot,
        _context.InputSnapshot,
    )

    # -- intentionally-unregistered public models, each with a reviewed reason.
    internal: dict[type[ContractModel], str] = {
        # digest.py — abstract strict bases
        _digest.ContractModel: _R_ABSTRACT_BASE,
        _digest.DigestModel: _R_ABSTRACT_BASE,
        # refs.py — addressing / authorization primitives + registry manifest infra
        _refs.SchemaRef: _R_ADDRESSING_REF,
        _refs.ContentRef: _R_ADDRESSING_REF,
        _refs.CapabilityRef: _R_ADDRESSING_REF,
        _refs.PayloadRef: _R_ADDRESSING_REF,
        _refs.SchemaManifestEntry: _R_ADDRESSING_REF,
        # data/symbols.py — embedded instrument value objects (no schema_version)
        _symbols.Symbol: _R_VALUE_OBJECT,
        _symbols.InstrumentMeta: _R_VALUE_OBJECT,
        _symbols.LimitRule: _R_VALUE_OBJECT,
        # data/result.py — envelope + nested components + abstract PIT base
        _result.DataResult: _R_GENERIC_ENVELOPE,
        _result.SourceAttempt: _R_NESTED_COMPONENT,
        _result.PitAudit: _R_NESTED_COMPONENT,
        _result.PitRecord: _R_ABSTRACT_PIT_BASE,
        # schemas.py — artifact envelope + nested provenance/linkage components
        _schemas.Artifact: _R_GENERIC_ENVELOPE,
        _schemas.ArtifactRef: _R_NESTED_COMPONENT,
        _schemas.ToolCallRecord: _R_NESTED_COMPONENT,
        _schemas.Provenance: _R_NESTED_COMPONENT,
        _schemas.NumberAnchor: _R_NESTED_COMPONENT,
        _schemas.ArtifactRelation: _R_EVENT_RECORD,
        _schemas.NodeRun: _R_EVENT_RECORD,
        # context.py — embedded context components + run-level accounting values
        _context.ClockSpec: _R_NESTED_COMPONENT,
        _context.DataContext: _R_NESTED_COMPONENT,
        _context.RunBudget: _R_RUN_CONTEXT_VALUE,
        _context.BudgetReservation: _R_RUN_CONTEXT_VALUE,
        _context.RunContext: _R_RUN_CONTEXT_VALUE,
        # events.py — the append-log / barrier / approval regime (Phase-2 EventStore)
        _events.RunEvent: _R_EVENT_RECORD,
        _events.EventCursor: _R_EVENT_RECORD,
        _events.CommittedArtifactRef: _R_EVENT_RECORD,
        _events.LayerCommit: _R_EVENT_RECORD,
        _events.PlanApproval: _R_EVENT_RECORD,
        # catalog.py — catalog ABI (build/validate governed)
        _catalog.ContentManifestEntry: _R_CATALOG_GOVERNED,
        _catalog.SkillManifest: _R_CATALOG_GOVERNED,
        _catalog.CapabilityDescriptor: _R_CATALOG_GOVERNED,
        _catalog.CapabilityManifestEntry: _R_CATALOG_GOVERNED,
        _catalog.ResolvedTextMaterial: _R_CATALOG_GOVERNED,
        _catalog.ResolvedCapabilityMaterial: _R_CATALOG_GOVERNED,
        _catalog.SkillBinding: _R_CATALOG_GOVERNED,
        _catalog.InputBinding: _R_CATALOG_GOVERNED,
        _catalog.OutputBinding: _R_CATALOG_GOVERNED,
        _catalog.ExecutionSpec: _R_CATALOG_GOVERNED,
        _catalog.EvidencePolicy: _R_CATALOG_GOVERNED,
        _catalog.CompatibilityBinding: _R_CATALOG_GOVERNED,
        _catalog.WorkerSpec: _R_CATALOG_GOVERNED,
        _catalog.WorkerCatalogSnapshot: _R_CATALOG_GOVERNED,
        # spec.py — plan authorization surface (validator / freeze governed)
        _spec.OrchestrationRequest: _R_PLAN_SURFACE,
        _spec.Dependency: _R_PLAN_SURFACE,
        _spec.PlanNode: _R_PLAN_SURFACE,
        _spec.GateCfg: _R_PLAN_SURFACE,
        _spec.GateResult: _R_PLAN_SURFACE,
        _spec.DebateCfg: _R_PLAN_SURFACE,
        _spec.ReducerCfg: _R_PLAN_SURFACE,
        _spec.PlanValidationIssue: _R_PLAN_SURFACE,
        _spec.PlanDraft: _R_PLAN_SURFACE,
        _spec.StaticLegacyPlanAttestation: _R_PLAN_SURFACE,
        _spec.PlanValidationReport: _R_PLAN_SURFACE,
        _spec.Plan: _R_PLAN_SURFACE,
        # migration.py — legacy source-schema migration adapters
        _migration.MigratedRating: _R_MIGRATION_ADAPTER,
        _migration.MigratedResearchAction: _R_MIGRATION_ADAPTER,
        _migration.MigratedPositionAction: _R_MIGRATION_ADAPTER,
        _migration.MigratedConfidence: _R_MIGRATION_ADAPTER,
        _migration.MigratedSentiment: _R_MIGRATION_ADAPTER,
        _migration.MigratedRotationStage: _R_MIGRATION_ADAPTER,
        _migration.LegacyWorkerMapping: _R_MIGRATION_ADAPTER,
        _migration.LegacyDependencyMapping: _R_MIGRATION_ADAPTER,
        _migration.LegacyInputMapping: _R_MIGRATION_ADAPTER,
        _migration.LegacyGraphMapping: _R_MIGRATION_ADAPTER,
    }

    overlap = set(public) & set(internal)
    if overlap:  # pragma: no cover - reviewed invariant, guards a future edit
        raise SchemaRegistryError(
            "a model cannot be both a registered payload and internal: "
            + ", ".join(sorted(m.__name__ for m in overlap))
        )

    _POPULATION = {"public": public, "internal": internal}
    return _POPULATION


def default_registry() -> SchemaRegistry:
    """Construct and **seal** the reviewed Phase-1 schema registry.

    Registers exactly :data:`PHASE1_PUBLIC_MODELS` (each under its own declared
    ``Name@schema_version`` key), seals the registry so no further registration is
    possible, and returns it. A fresh, sealed instance is returned on every call —
    there is no global registry built at import time.
    """
    reg = SchemaRegistry()
    for model in _load_population()["public"]:
        reg.register(model)
    reg.seal()
    return reg


def __getattr__(name: str) -> Any:
    """Expose the reviewed population tuples lazily (PEP 562).

    Keeps import-time pure: the model graph is imported only on first access to
    ``PHASE1_PUBLIC_MODELS`` / ``INTERNAL_MODELS`` (or on the first
    :func:`default_registry` call), never at module import.
    """
    if name == "PHASE1_PUBLIC_MODELS":
        return _load_population()["public"]
    if name == "INTERNAL_MODELS":
        from types import MappingProxyType

        return MappingProxyType(_load_population()["internal"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
