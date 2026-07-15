"""Strict contract base + canonical semantic/audit digests (``sha256+cjson-v1``).

This module is the identity foundation of ``guanlan_v2.orchestration``. Every
public semantic model in later tasks inherits :class:`ContractModel` /
:class:`DigestModel` and every persisted digest is produced by
:func:`content_digest` / :func:`audit_digest`, so the canonicalization rules
below are frozen and versioned.

Canonicalization version ``sha256+cjson-v1`` — the 10 normative rules:

1. project from Python-mode model fields (``getattr``), never
   ``model_dump(mode="json")`` — nested model type information must survive;
2. a nested :class:`DigestModel` applies its *own* semantic/audit projection,
   so a nested wall-clock field can never leak into a parent semantic digest;
3. dict keys are sorted and must be strings;
4. ``set`` / ``frozenset`` elements are sorted by their canonical element JSON;
   ``list`` / ``tuple`` order is preserved;
5. aware datetimes normalize to UTC and serialize with one reviewed
   representation (``YYYY-MM-DDTHH:MM:SS.ffffffZ``);
6. naive datetime, ``NaN`` and infinities are rejected;
7. ``-0.0`` is normalized to ``0.0``;
8. an ``Enum`` serializes by its ``.value``;
9. unsupported semantic types are rejected;
10. an object's own declared digest field is excluded to prevent
    self-reference.

The final byte layout is produced only by
``json.dumps(..., allow_nan=False, sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`` after normalization.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

CJSON_VERSION = "sha256+cjson-v1"
Projection = Literal["semantic", "audit"]

__all__ = [
    "CJSON_VERSION",
    "ContractModel",
    "DigestModel",
    "DigestHex",
    "UtcDateTime",
    "FiniteFloat",
    "NonNegativeInt",
    "PositiveInt",
    "NonEmptyStr",
    "canonical_json",
    "content_digest",
    "audit_digest",
    "verify_digest",
]


# --------------------------------------------------------------------------- #
# Shared strict types                                                         #
# --------------------------------------------------------------------------- #
def _reject_bool(v: Any) -> Any:
    """Reject ``bool`` where a number is expected (``bool`` is an ``int``)."""
    if isinstance(v, bool):
        raise ValueError("bool is not a valid numeric value")
    return v


def _require_finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("float must be finite (NaN/Inf are rejected)")
    return v


def _require_non_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("string must not be empty or blank")
    return v


def _require_aware_utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("naive datetime is not allowed; a tz-aware value is required")
    return v.astimezone(timezone.utc)


#: strict 64-char lowercase SHA-256 hex string.
DigestHex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
#: tz-aware datetime, normalized to UTC; naive input is a validation error.
UtcDateTime = Annotated[datetime, AfterValidator(_require_aware_utc)]
#: finite float; ``NaN`` / ``±Inf`` / ``bool`` are rejected.
FiniteFloat = Annotated[float, BeforeValidator(_reject_bool), AfterValidator(_require_finite)]
#: strict non-negative int; ``bool`` is rejected.
NonNegativeInt = Annotated[int, BeforeValidator(_reject_bool), Field(ge=0)]
#: strict positive int; ``bool`` is rejected.
PositiveInt = Annotated[int, BeforeValidator(_reject_bool), Field(gt=0)]
#: non-blank string.
NonEmptyStr = Annotated[str, AfterValidator(_require_non_blank)]


# --------------------------------------------------------------------------- #
# Canonicalization                                                            #
# --------------------------------------------------------------------------- #
def _dumps(obj: Any) -> str:
    """Final canonical byte layout — only ever called on JSON-native values."""
    return json.dumps(
        obj,
        allow_nan=False,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _canonical_datetime(v: datetime) -> str:
    """One reviewed UTC representation; fixed 6-digit microseconds, ``Z`` suffix."""
    u = _require_aware_utc(v)
    return (
        f"{u.year:04d}-{u.month:02d}-{u.day:02d}T"
        f"{u.hour:02d}:{u.minute:02d}:{u.second:02d}.{u.microsecond:06d}Z"
    )


def _project(value: Any, projection: Projection) -> Any:
    """Recursively normalize ``value`` into a JSON-native structure.

    Rule ordering matters: a nested :class:`DigestModel` first (so it applies
    its own projection), then ``Enum`` before ``bool``/``int``/``str`` (our
    enums are ``str, Enum``), then ``bool`` before ``int``.
    """
    if isinstance(value, DigestModel):
        return value._projected_mapping(projection)
    if isinstance(value, BaseModel):
        # A non-DigestModel semantic model has no declared projection, so
        # canonicalizing it could silently leak audit fields. Reject it.
        raise TypeError(
            "non-DigestModel pydantic model is unsupported in canonical JSON: "
            f"{type(value).__name__}"
        )
    if value is None:
        return None
    if isinstance(value, Enum):
        return _project(value.value, projection)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float (NaN/Inf) is not allowed in canonical JSON")
        return 0.0 if value == 0.0 else value  # normalize -0.0 -> 0.0
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return _canonical_datetime(value)
    if isinstance(value, (set, frozenset)):
        projected = [_project(e, projection) for e in value]
        projected.sort(key=_dumps)  # sort by canonical element JSON
        return projected
    if isinstance(value, (list, tuple)):
        return [_project(e, projection) for e in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"dict keys must be strings, got {type(k).__name__}")
            out[k] = _project(v, projection)
        return out
    raise TypeError(f"unsupported type for canonical JSON: {type(value).__name__}")


def canonical_json(data: Any, *, projection: Projection = "semantic") -> str:
    """Return the ``sha256+cjson-v1`` canonical JSON string for ``data``."""
    if projection not in ("semantic", "audit"):
        raise ValueError(f"projection must be 'semantic' or 'audit', got {projection!r}")
    return _dumps(_project(data, projection))


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_digest(data: Any) -> DigestHex:
    """SHA-256 over the *semantic* canonical JSON of ``data``."""
    return _sha256_hex(canonical_json(data, projection="semantic"))


def audit_digest(data: Any) -> DigestHex:
    """SHA-256 over the *audit* canonical JSON of ``data``."""
    return _sha256_hex(canonical_json(data, projection="audit"))


def verify_digest(data: Any, expected: str, *, projection: Projection = "semantic") -> None:
    """Raise ``ValueError`` if the recomputed digest does not match ``expected``."""
    actual = content_digest(data) if projection == "semantic" else audit_digest(data)
    if actual != expected:
        raise ValueError(
            f"{projection} digest mismatch: expected {expected!r}, computed {actual!r}"
        )


# --------------------------------------------------------------------------- #
# Strict contract bases                                                       #
# --------------------------------------------------------------------------- #
class ContractModel(BaseModel):
    """Strict, closed base for every public orchestration contract.

    ``extra="forbid"`` rejects unknown fields; ``strict=True`` disables lax
    coercion (e.g. ``"5"`` is not an int, ``True`` is not a number). Each public
    subclass declares its own closed ``schema_version: Literal["N"]`` so a
    payload cannot self-report a different or arbitrary version.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class DigestModel(ContractModel):
    """Immutable contract with deterministic semantic and audit digests.

    Each subclass declares, via class variables:

    * ``SEMANTIC_EXCLUDE`` — audit identity (random ids, wall-clock, provider
      response ids…) that must not enter the *semantic* digest;
    * ``AUDIT_EXCLUDE`` — fields excluded from the *audit* digest (usually
      empty);
    * ``SELF_DIGEST_FIELDS`` — fields that store *this* object's own digest;
      excluded from both projections to prevent self-reference (rule 10).

    Exclusion is declared per model, never inferred from a global name
    blacklist. A nested ``DigestModel`` keeps its own projection because
    :func:`_project` recurses through ``_projected_mapping``.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset()
    AUDIT_EXCLUDE: ClassVar[frozenset[str]] = frozenset()
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset()

    def _projected_mapping(self, projection: Projection) -> dict[str, Any]:
        """Project this model's *actual* field values under ``projection``.

        Iterates declared fields via ``getattr`` so nested models arrive at
        :func:`_project` as models (not pre-flattened dicts) and apply their
        own projection.
        """
        if projection == "semantic":
            exclude = self.SEMANTIC_EXCLUDE | self.SELF_DIGEST_FIELDS
        else:
            exclude = self.AUDIT_EXCLUDE | self.SELF_DIGEST_FIELDS
        result: dict[str, Any] = {}
        for name in type(self).model_fields:
            if name in exclude:
                continue
            result[name] = _project(getattr(self, name), projection)
        return result

    def semantic_digest(self) -> DigestHex:
        """This object's content digest (schema_version participates)."""
        return content_digest(self)

    def audit_digest_value(self) -> DigestHex:
        """This object's audit digest (includes volatile audit fields)."""
        return audit_digest(self)

    @classmethod
    def digest_of_fields(
        cls, *, projection: Projection = "semantic", **field_values: Any
    ) -> DigestHex:
        """Compute this model's own digest from raw field values, excluding the
        self-declared digest field(s).

        Model-specific builders use this to *seal* a record before construction,
        breaking the digest self-reference cycle: build the digest here, then
        construct the real (validating) instance with the digest attached, and a
        model-specific ``model_validator`` re-verifies it on load.
        """
        stub = cls.model_construct(**field_values)
        return (
            stub.semantic_digest()
            if projection == "semantic"
            else stub.audit_digest_value()
        )
