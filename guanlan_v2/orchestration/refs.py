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

Note on ``ContentRef.content_digest`` / ``CapabilityRef.content_digest`` /
``PayloadRef.content_digest``: unlike a *self-sealed* record (whose own digest
field goes in ``SELF_DIGEST_FIELDS``), these ``content_digest`` fields hold the
digest of the *referenced* object, so they are ordinary semantic fields and are
intentionally **not** excluded from the semantic projection.
"""
from __future__ import annotations

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
    "SchemaManifestEntry",
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


class SchemaManifestEntry(DigestModel):
    """One entry of a schema registry manifest: schema ref + its JSON-schema digest."""

    schema_version: Literal["1"] = "1"
    schema_ref: SchemaRef
    json_schema_digest: DigestHex

    @property
    def key(self) -> str:
        return self.schema_ref.key
