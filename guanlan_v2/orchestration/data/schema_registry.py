# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — the sealed cumulative **data-only** schema registry.

``build_phase3_registry`` constructs a fresh sealed registry from Phase 2's
exported ``PHASE2_PUBLIC_MODELS`` (which already includes the exact Phase-1
public model set) plus the reviewed :data:`PHASE3_PUBLIC_MODELS`, after first
verifying the caller-expected Phase-2 runtime digest against the actual sealed
Phase-2 registry (and its exported ``PHASE2_BASE_REGISTRY_DIGEST``). It never
mutates or unseals either upstream sealed registry, and there is no global
mutable "latest registry": old Plans keep resolving their exact Phase-1/Phase-2
digest, while a Plan using these data payloads binds the Phase-3 cumulative
digest. Task 9 may extend this immutable snapshot under a *new* digest/golden
but may not rewrite this tuple, digest or golden.

The reviewed partition:

* :data:`PHASE3_PUBLIC_MODELS` — the Task-3 ``LimitRulePolicy`` plus every public
  model introduced by Tasks 4–5 (PIT candidates/policies/refusal details, the
  method/request/fetch ABI, the seven concrete record/batch families and their
  seven **named** DataResult envelopes, the routing/snapshot/cache models, the
  rendered-block contract and the prefetch catalog-material contracts);
* :data:`PHASE3_INTERNAL_MODELS` — the intentionally-unregistered frozen dispatch
  carriers (``DataInvocationScope`` / ``ResolvedDataMethodPolicy`` /
  ``VerifiedDataCacheHit`` / ``DataReadOutcome``), each with a reviewed reason.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from guanlan_v2.orchestration.data import catalog as _dcatalog
from guanlan_v2.orchestration.data import render as _drender
from guanlan_v2.orchestration.data import snapshot as _dsnapshot
from guanlan_v2.orchestration.data import source as _dsource
from guanlan_v2.orchestration.data.pit import (
    DataFetchRefusalDetails,
    FreshnessPolicy,
    RawRowCandidate,
)
from guanlan_v2.orchestration.data.symbols import LimitRuleEntry, LimitRulePolicy
from guanlan_v2.orchestration.runtime_contracts import (
    Phase2RuntimeRegistry,
    phase2_public_models,
    phase2_runtime_registry,
)

__all__ = [
    "PHASE3_PUBLIC_MODELS",
    "PHASE3_INTERNAL_MODELS",
    "Phase3DataRegistryError",
    "build_phase3_registry",
    "PHASE3_DATA_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
]


class Phase3DataRegistryError(Exception):
    """A Phase-3 data-registry construction invariant was violated."""


#: the reviewed tuple of every registered Phase-3 data payload/fact schema.
PHASE3_PUBLIC_MODELS: tuple[type, ...] = (
    # Task 3 — versioned price-limit policy (+ its nested table slice, which is a
    # closed self-versioned value resolved alongside the policy)
    LimitRulePolicy,
    LimitRuleEntry,
    # Task 4 — PIT firewall facts
    RawRowCandidate,
    FreshnessPolicy,
    DataFetchRefusalDetails,
    # Task 5 — method/request/fetch ABI + records/batches/envelopes + registry DTOs
    *_dsource.SOURCE_PUBLIC_MODELS,
    # Task 5 — routing / snapshot / cache ABI
    *_dsnapshot.SNAPSHOT_PUBLIC_MODELS,
    # Task 5 — rendered untrusted-data block
    *_drender.RENDER_PUBLIC_MODELS,
    # Task 5 — prefetch catalog-material contracts
    *_dcatalog.CATALOG_PUBLIC_MODELS,
)

#: intentionally-unregistered Task-5 helpers, each with a reviewed reason.
PHASE3_INTERNAL_MODELS: Mapping[type, str] = MappingProxyType(
    {
        **_dsource.SOURCE_INTERNAL_MODELS,
        **_dsnapshot.SNAPSHOT_INTERNAL_MODELS,
        **_drender.RENDER_INTERNAL_MODELS,
        **_dcatalog.CATALOG_INTERNAL_MODELS,
    }
)

_overlap = set(PHASE3_PUBLIC_MODELS) & set(PHASE3_INTERNAL_MODELS)
if _overlap:  # pragma: no cover - reviewed invariant, guards a future edit
    raise Phase3DataRegistryError(
        "a model cannot be both a registered Phase-3 payload and internal: "
        + ", ".join(sorted(m.__name__ for m in _overlap))
    )


def build_phase3_registry(expected_phase2_runtime_digest: str) -> Phase2RuntimeRegistry:
    """Build + seal the cumulative Phase-3 data registry, pinned to Phase 2.

    Verifies the expected Phase-2 runtime manifest/digest up front by rebuilding
    the sealed Phase-2 registry from its own exported
    ``PHASE2_BASE_REGISTRY_DIGEST`` (a drifted Phase-2 base is rejected), then
    registers ``PHASE2_PUBLIC_MODELS`` followed by :data:`PHASE3_PUBLIC_MODELS`
    into a *fresh* registry and seals it. Neither upstream registry is mutated.
    """
    from guanlan_v2.orchestration.runtime_contracts import PHASE2_BASE_REGISTRY_DIGEST

    phase2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    if expected_phase2_runtime_digest != phase2.registry_digest:
        raise Phase3DataRegistryError(
            "build_phase3_registry requires the exact Phase-2 runtime registry digest "
            f"{phase2.registry_digest!r}; got {expected_phase2_runtime_digest!r}"
        )
    reg = Phase2RuntimeRegistry()
    for model in tuple(phase2_public_models()) + PHASE3_PUBLIC_MODELS:
        reg.register(model)
    reg.seal()
    return reg


_PHASE3_DATA_REGISTRY_DIGEST: str | None = None


def __getattr__(name: str) -> Any:
    """Lazily expose the cumulative Phase-3 registry digest (PEP 562)."""
    if name == "PHASE3_DATA_REGISTRY_DIGEST":
        global _PHASE3_DATA_REGISTRY_DIGEST
        if _PHASE3_DATA_REGISTRY_DIGEST is None:
            from guanlan_v2.orchestration.runtime_contracts import (
                PHASE2_BASE_REGISTRY_DIGEST,
            )

            phase2_digest = phase2_runtime_registry(
                PHASE2_BASE_REGISTRY_DIGEST
            ).registry_digest
            _PHASE3_DATA_REGISTRY_DIGEST = build_phase3_registry(
                phase2_digest
            ).registry_digest
        return _PHASE3_DATA_REGISTRY_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
