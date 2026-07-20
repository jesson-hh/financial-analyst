# -*- coding: utf-8 -*-
"""handler.pv.price_action — deterministic price-action geometry handler (Phase 8, Task 5).

Red line (前后端镜像逐位一致): this handler DELEGATES to the existing pure
``guanlan_v2.seats.price_action.compute_pa_features`` — it never reimplements the 15-key
bar geometry. ``compute_geometry`` returns that function's output verbatim (all 15 mixed
keys), so it stays bitwise-identical to both the front-end ``paFeatures()`` mirror and
the reference function. ``numeric_features`` is the finite-float projection the
``PriceActionFeatureReport.features`` dict carries — exactly the ``pa-15key-v1``
registry keys, drawn from the same verbatim geometry; a categorical or absent key is
NEVER fabricated into a number.

Pure + import-safe: no engine / datafeed / network / LLM import; ``compute_pa_features``
imports only ``math`` (handoff-gate clause (h)).
"""
from __future__ import annotations

from guanlan_v2.orchestration.lane_payloads import PA_FEATURE_SET_KEYS
from guanlan_v2.seats.price_action import compute_pa_features

#: the feature-set version this handler projects into (the numeric key registry key).
FEATURE_SET_VERSION = "pa-15key-v1"

#: the exact finite-float feature keys the report carries — the single source is the
#: lane payload registry, so the projection can never drift from the schema validator.
NUMERIC_FEATURE_KEYS: tuple[str, ...] = PA_FEATURE_SET_KEYS[FEATURE_SET_VERSION]


def compute_geometry(df, code: str = "", name: str = "") -> dict:
    """The full 15-key bar geometry — ``compute_pa_features`` verbatim (no reimplement)."""
    return compute_pa_features(df, code=code, name=name)


def numeric_features(geometry: dict) -> dict:
    """Project the finite-float ``pa-15key-v1`` feature subset from a geometry dict.

    Raises when a numeric key is missing / ``None`` (insufficient history) — the report
    is emitted only with the complete, exactly-keyed numeric feature set, never a
    truncated one (honest UNAVAILABLE ⇒ no report, downstream degrades).
    """
    out: dict[str, float] = {}
    for key in NUMERIC_FEATURE_KEYS:
        value = geometry.get(key)
        if value is None:
            raise ValueError(
                f"price-action feature {key!r} is unavailable on this bar "
                "(insufficient history); emit no feature report rather than truncating"
            )
        out[key] = float(value)
    return out
