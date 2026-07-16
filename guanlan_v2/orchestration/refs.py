"""Versioned logical refs — schema / content / capability / payload references.

These are the *authorization identities* of the orchestration kernel. A Plan
never carries physical prompt/skill/tool file paths; it carries stable logical
refs (``id + version + expected content digest``) and a later catalog resolver
owns the mapping to physical storage. Rejecting path-like ids at the type level
is what makes "no physical paths in a Plan" a structural guarantee rather than a
convention.

Every ref subclasses :class:`~guanlan_v2.orchestration.digest.DigestModel` so it
can be nested inside downstream semantic models (Artifact / Event / WorkerSpec /
Plan) and correctly participate in — or be projected out of — their canonical
semantic/audit digests:

* ``SchemaRef`` / ``ContentRef`` / ``CapabilityRef`` / ``SchemaManifestEntry`` —
  every field is authorization identity, so nothing is excluded; all fields enter
  the parent's semantic digest.
* ``PayloadRef.object_id`` is a storage-assigned dereference locator (audit
  identity). It is listed in ``SEMANTIC_EXCLUDE`` so a parent digests a payload
  ref as ``namespace + content_digest`` and never depends on the random storage
  id, while the *audit* projection still records it. Changing the referenced
  payload's ``content_digest`` (or its ``namespace``) therefore changes the
  parent semantic digest; re-storing identical content under a new ``object_id``
  does not.
* ``TypedPayloadRef`` composes an exact ``SchemaRef`` with a ``PayloadRef`` —
  the only generic public wrapper when both the schema identity and the payload
  locator are needed for deterministic replay. Its semantic projection is the
  schema identity plus the payload's ``namespace + content_digest``;
  ``payload_ref.object_id`` stays audit-only purely through the nested
  ``PayloadRef`` projection, so the composite declares no exclusions of its own.

Note on ``ContentRef.content_digest`` / ``CapabilityRef.content_digest`` /
``PayloadRef.content_digest``: unlike a *self-sealed* record (whose own digest
field goes in ``SELF_DIGEST_FIELDS``), these ``content_digest`` fields hold the
digest of the *referenced* object, so they are ordinary semantic fields and are
intentionally **not** excluded from the semantic projection.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, ClassVar, Literal

from pydantic import StringConstraints

from guanlan_v2.orchestration.digest import DigestHex, DigestModel, NonEmptyStr

__all__ = [
    "LOGICAL_ID_PATTERN",
    "LogicalId",
    "SchemaName",
    "SchemaVersion",
    "PayloadNamespace",
    "PAYLOAD_NAMESPACES",
    "PUBLIC_PAYLOAD_NAMESPACES",
    "NON_PUBLIC_PAYLOAD_NAMESPACES",
    "SchemaRef",
    "ContentRef",
    "CapabilityRef",
    "PayloadRef",
    "TypedPayloadRef",
    "SchemaManifestEntry",
    "typed_ref_sort_key",
    "validate_typed_ref_tuple",
]

# --------------------------------------------------------------------------- #
# Strict logical-key types                                                    #
# --------------------------------------------------------------------------- #
#: Reviewed logical-id grammar: a lowercase token with ``. _ -`` separators, each
#: of which must be followed by at least one ``[a-z0-9]``. It cannot contain a
#: path separator (``/`` or ``\``), a drive/colon, uppercase, ``@`` or spaces, so
#: a physical file path can never be accepted as a logical id.
LOGICAL_ID_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
LogicalId = Annotated[str, StringConstraints(pattern=LOGICAL_ID_PATTERN)]

#: A registered schema/model name — a Python identifier. Forbidding ``@`` keeps
#: the canonical ``name@version`` registry key unambiguous.
SCHEMA_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
SchemaName = Annotated[str, StringConstraints(pattern=SCHEMA_NAME_PATTERN)]

#: A schema version token. Models declare ``schema_version: Literal["N"]`` with a
#: numeric value, so a ``SchemaRef`` version is numeric too; forbidding non-digits
#: (in particular ``@``) also keeps the ``name@version`` key unambiguous.
SCHEMA_VERSION_PATTERN = r"^[0-9]+$"
SchemaVersion = Annotated[str, StringConstraints(pattern=SCHEMA_VERSION_PATTERN)]

# --------------------------------------------------------------------------- #
# Closed payload namespace                                                    #
# --------------------------------------------------------------------------- #
#: Closed set of payload-store namespaces. ``main`` is the only publicly visible
#: namespace; ``sealed`` / ``review`` / ``audit`` are non-public. ``audit`` is
#: never a valid ordinary Artifact/InputSnapshot/public-event source and exists so
#: later phases can persist typed refusal details without mislabeling them as main
#: data — it retains distinct policy semantics but the same "not public" visibility
#: as ``sealed`` / ``review``.
PayloadNamespace = Literal["main", "sealed", "review", "audit"]
PAYLOAD_NAMESPACES: frozenset[str] = frozenset({"main", "sealed", "review", "audit"})
PUBLIC_PAYLOAD_NAMESPACES: frozenset[str] = frozenset({"main"})
NON_PUBLIC_PAYLOAD_NAMESPACES: frozenset[str] = PAYLOAD_NAMESPACES - PUBLIC_PAYLOAD_NAMESPACES


# --------------------------------------------------------------------------- #
# Refs                                                                        #
# --------------------------------------------------------------------------- #
class SchemaRef(DigestModel):
    """Reference to a versioned payload schema, canonical key ``name@version``."""

    schema_version: Literal["1"] = "1"
    name: SchemaName
    version: SchemaVersion

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


class ContentRef(DigestModel):
    """Catalog-owned text material ref (prompt/skill/guardrail/handler/…).

    ``id`` + ``version`` + expected ``content_digest`` are the full semantic
    authorization identity; the physical location is owned by a later resolver.
    """

    schema_version: Literal["1"] = "1"
    id: LogicalId
    version: NonEmptyStr
    content_digest: DigestHex


class CapabilityRef(DigestModel):
    """Catalog-owned capability (tool) ref.

    Deliberately has no ``transport`` field: MCP transport is not an
    authorization boundary and lives only on the corresponding capability
    manifest entry, so it cannot be double-written inconsistently against a ref.
    """

    schema_version: Literal["1"] = "1"
    id: LogicalId
    version: NonEmptyStr
    content_digest: DigestHex


class PayloadRef(DigestModel):
    """Reference to a stored payload object.

    ``object_id`` is a storage-assigned dereference locator (audit identity) and
    is excluded from the semantic projection; the semantic identity of a payload
    ref is ``namespace + content_digest``.
    """

    schema_version: Literal["1"] = "1"
    namespace: PayloadNamespace
    object_id: NonEmptyStr
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"object_id"})

    @property
    def is_public(self) -> bool:
        return self.namespace in PUBLIC_PAYLOAD_NAMESPACES


class TypedPayloadRef(DigestModel):
    """Composite typed evidence reference: exact schema identity + payload locator.

    The generic immutable typed evidence reference for deterministic replay: a
    consumer re-validates the referenced payload against the exact registered
    ``schema_ref`` instead of guessing a schema from an object id or bare hash.
    The semantic projection is ``schema_version`` + the full :class:`SchemaRef`
    + the payload's ``namespace + content_digest``; ``payload_ref.object_id``
    stays audit-only purely through the nested :class:`PayloadRef` projection —
    the composite declares no exclusions of its own.

    The type itself carries no namespace constraint: owners that expose
    public/runtime evidence additionally require ``payload_ref.namespace ==
    "main"`` (the shared owner-side rule lives in
    :func:`validate_typed_ref_tuple`).
    """

    schema_version: Literal["1"] = "1"
    schema_ref: SchemaRef
    payload_ref: PayloadRef


class SchemaManifestEntry(DigestModel):
    """One entry of a schema registry manifest: schema ref + its JSON-schema digest."""

    schema_version: Literal["1"] = "1"
    schema_ref: SchemaRef
    json_schema_digest: DigestHex

    @property
    def key(self) -> str:
        return self.schema_ref.key


# --------------------------------------------------------------------------- #
# Shared typed-ref tuple validation                                           #
# --------------------------------------------------------------------------- #
def typed_ref_sort_key(ref: TypedPayloadRef) -> tuple[str, str, str, str]:
    """The canonical typed semantic projection key of a :class:`TypedPayloadRef`.

    ``(schema_ref.name, schema_ref.version, payload_ref.namespace,
    payload_ref.content_digest)`` — exactly the semantic identity of the ref,
    so ordering (and duplicate detection) can never depend on the audit-only
    ``payload_ref.object_id``.
    """
    return (
        ref.schema_ref.name,
        ref.schema_ref.version,
        ref.payload_ref.namespace,
        ref.payload_ref.content_digest,
    )


def validate_typed_ref_tuple(
    refs: Sequence[TypedPayloadRef],
    *,
    require_main: bool,
    field_name: str,
) -> None:
    """Shared owner-side invariant for typed evidence-ref tuples.

    Enforces, in order: canonical ordering by :func:`typed_ref_sort_key`;
    duplicate-freedom by the same semantic key (two refs differing only in the
    audit ``object_id`` are the *same* evidence, hence duplicates); and — when
    ``require_main`` — ``payload_ref.namespace == "main"`` on every element.
    Raises :class:`ValueError` naming ``field_name`` so an owning model's
    validator reports the offending field. The single implementation reused by
    every evidence-tuple owner (Provenance / NodeRun / InputSnapshot).
    """
    keys = [typed_ref_sort_key(r) for r in refs]
    if keys != sorted(keys):
        raise ValueError(
            f"{field_name} must be canonically ordered by (schema_ref.name, "
            "schema_ref.version, payload_ref.namespace, payload_ref.content_digest)"
        )
    seen: set[tuple[str, str, str, str]] = set()
    for key in keys:
        if key in seen:
            raise ValueError(
                f"{field_name} contains a duplicate typed ref for "
                f"{key[0]}@{key[1]} (namespace {key[2]!r}, content digest {key[3]})"
            )
        seen.add(key)
    if require_main:
        for ref in refs:
            if ref.payload_ref.namespace != "main":
                raise ValueError(
                    f"{field_name} requires main-namespace payload refs; got "
                    f"namespace {ref.payload_ref.namespace!r} for "
                    f"{ref.schema_ref.key}"
                )
