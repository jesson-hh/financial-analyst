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
