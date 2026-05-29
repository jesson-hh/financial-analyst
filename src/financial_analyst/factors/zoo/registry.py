"""Alpha registry — central dict of name → AlphaSpec.

Each alpha is a function ``(PanelData) -> pd.Series`` indexed by the
same ``(datetime, code)`` MultiIndex as the panel. The ``register``
decorator stores both the function and its metadata so ``alpha show``
and ``alpha bench`` can introspect without re-importing.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from financial_analyst.factors.zoo.panel import PanelData


AlphaFn = Callable[[PanelData], pd.Series]


@dataclass(frozen=True)
class AlphaSpec:
    """One alpha definition.

    Attributes
    ----------
    name : short id, e.g. ``"alpha001"`` (unique across all families)
    family : family slug, one of ``alpha101 / gtja191 / qlib158 / academic``
    description : 1-line natural-language description
    formula_text : the formula as it appears in the source paper / handbook
    compute : the implementation; takes a PanelData, returns a pd.Series
              aligned to the panel's index. NaNs allowed.
    paper : optional citation (paper title / firm / year)
    """
    name: str
    family: str
    description: str
    formula_text: str
    compute: AlphaFn
    paper: str = ""
    tags: tuple = field(default_factory=tuple)


_REGISTRY: Dict[str, AlphaSpec] = {}


def register(spec: AlphaSpec) -> AlphaSpec:
    """Register an AlphaSpec. Idempotent re-registration with identical
    `compute` is allowed (useful for hot-reload in tests); collisions
    with a different compute fn raise.
    """
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing.compute is not spec.compute:
        raise ValueError(
            f"Alpha {spec.name!r} already registered by family "
            f"{existing.family!r} with a different compute fn; "
            f"new registration from family {spec.family!r}"
        )
    _REGISTRY[spec.name] = spec
    return spec


def unregister(name: str) -> bool:
    """Remove an alpha from the registry. Returns True if it was present.

    Needed for replace semantics (e.g. reloading a user factor whose recompiled
    compute is a new fn object — register() would otherwise raise on collision)."""
    return _REGISTRY.pop(name, None) is not None


def get(name: str) -> AlphaSpec:
    """Look up an alpha by name. Raises KeyError if absent."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown alpha {name!r}. Known families: {sorted(families())}. "
            f"Call list_alphas() for available names."
        )
    return _REGISTRY[name]


def list_alphas(family: Optional[str] = None) -> List[AlphaSpec]:
    """Return all alphas, optionally filtered by family. Sorted by name."""
    out = [s for s in _REGISTRY.values() if family is None or s.family == family]
    return sorted(out, key=lambda s: s.name)


def families() -> List[str]:
    """Return all registered family slugs."""
    return sorted({s.family for s in _REGISTRY.values()})


def _clear_registry_for_tests() -> None:
    """Test-only helper to wipe the registry between unit tests."""
    _REGISTRY.clear()
