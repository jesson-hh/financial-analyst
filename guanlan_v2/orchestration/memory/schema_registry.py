# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — the sealed cumulative **full (data + memory)** schema registry.

``build_phase3_full_registry`` constructs a fresh sealed registry from the exact
immutable Task-5 data snapshot (Phase-1 + Phase-2 + Phase-3 data models) plus the
reviewed :data:`~guanlan_v2.orchestration.memory.models.MEMORY_PUBLIC_MODELS`,
after first verifying the caller-expected Phase-3 **data** registry digest against
the actual sealed data registry. It never mutates or unseals any upstream sealed
registry, and there is no global mutable "latest registry": old Plans keep
resolving their exact Phase-1/Phase-2/data digest, while a memory-enabled Plan
binds the full Phase-3 cumulative digest. The data-only builder/tuple/digest/
golden remain byte-for-byte rebuildable.
"""
from __future__ import annotations

from typing import Any

from guanlan_v2.orchestration.data.schema_registry import (
    PHASE3_PUBLIC_MODELS as PHASE3_DATA_PUBLIC_MODELS,
    build_phase3_registry,
)
from guanlan_v2.orchestration.memory.models import (
    MEMORY_PUBLIC_MODELS,
    PHASE3_MEMORY_INTERNAL_MODELS,
)
from guanlan_v2.orchestration.runtime_contracts import (
    Phase2RuntimeRegistry,
    phase2_public_models,
)

__all__ = [
    "PHASE3_MEMORY_PUBLIC_MODELS",
    "PHASE3_MEMORY_INTERNAL_MODELS",
    "Phase3FullRegistryError",
    "build_phase3_full_registry",
    "PHASE3_FULL_BASE_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE3_FULL_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
]


class Phase3FullRegistryError(Exception):
    """A Phase-3 full-registry construction invariant was violated."""


#: the reviewed tuple of every registered Task-9 memory payload/fact schema.
PHASE3_MEMORY_PUBLIC_MODELS: tuple[type, ...] = MEMORY_PUBLIC_MODELS

_overlap = set(PHASE3_MEMORY_PUBLIC_MODELS) & set(PHASE3_MEMORY_INTERNAL_MODELS)
if _overlap:  # pragma: no cover - reviewed invariant, guards a future edit
    raise Phase3FullRegistryError(
        "a model cannot be both a registered memory payload and internal: "
        + ", ".join(sorted(m.__name__ for m in _overlap))
    )


def _phase3_data_digest() -> str:
    from guanlan_v2.orchestration.data import schema_registry as _data_reg

    return _data_reg.PHASE3_DATA_REGISTRY_DIGEST


def build_phase3_full_registry(expected_phase3_data_digest: str) -> Phase2RuntimeRegistry:
    """Build + seal the cumulative full (data + memory) registry, pinned to data.

    Verifies the expected Phase-3 data registry digest up front by rebuilding the
    sealed data registry from its own exported base (a drifted data base is
    rejected), then registers the complete inherited model set followed by
    :data:`PHASE3_MEMORY_PUBLIC_MODELS` into a *fresh* registry and seals it.
    No upstream registry is mutated.
    """
    actual = _phase3_data_digest()
    if expected_phase3_data_digest != actual:
        raise Phase3FullRegistryError(
            "build_phase3_full_registry requires the exact Phase-3 data registry "
            f"digest {actual!r}; got {expected_phase3_data_digest!r}"
        )
    reg = Phase2RuntimeRegistry()
    for model in (
        tuple(phase2_public_models())
        + tuple(PHASE3_DATA_PUBLIC_MODELS)
        + PHASE3_MEMORY_PUBLIC_MODELS
    ):
        reg.register(model)
    reg.seal()
    return reg


_PHASE3_FULL_REGISTRY_DIGEST: str | None = None


def __getattr__(name: str) -> Any:
    """Lazily expose the base/full registry digests (PEP 562)."""
    if name == "PHASE3_FULL_BASE_REGISTRY_DIGEST":
        return _phase3_data_digest()
    if name == "PHASE3_FULL_REGISTRY_DIGEST":
        global _PHASE3_FULL_REGISTRY_DIGEST
        if _PHASE3_FULL_REGISTRY_DIGEST is None:
            _PHASE3_FULL_REGISTRY_DIGEST = build_phase3_full_registry(
                _phase3_data_digest()
            ).registry_digest
        return _PHASE3_FULL_REGISTRY_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# re-exported so callers can rebuild the data-only snapshot through one door.
__doc_data_builder__ = build_phase3_registry
