# -*- coding: utf-8 -*-
"""Phase 8 - Task 3: the ModelTier -> ``config/llm.yaml`` bridge, as catalog-owned
config material.

The design's 24-worker map declares each LLM worker's ``ExecutionSpec.model_tier``
(``fast`` / ``reasoner`` / ``reasoner_deep``) but Phase 1 deliberately never mapped
a tier to a concrete provider/model. This module is that bridge, and it is the ONE
place the closed :data:`~guanlan_v2.orchestration.catalog.ModelTier` vocabulary joins
the engine's ``agent_overrides`` convention.

Load-bearing red line (plan verbatim)
-------------------------------------
No orchestration code calls engine ``find_config`` / ``LLMClient`` config discovery
implicitly: the tier map is built from **explicitly supplied ``config/llm.yaml``
bytes** (:func:`tier_map_from_llm_yaml`). A tier without a configured provider/model
raises :class:`ModelTierUnconfigured` — it NEVER silently falls back to
``default_provider`` / ``default_model`` (红线: 绝不静默回落到未配置 vendor). The
three ``orchestration-fast`` / ``orchestration-reasoner`` / ``orchestration-reasoner-deep``
alias keys under ``agent_overrides`` are the ONLY join key
(:data:`ORCHESTRATION_TIER_ALIASES`); the engine ``LLMClient.for_agent(agent_name)``
convention (reads those overrides) is consumed as documented convention only — this
module never imports the engine config layer.

Strict parsing reuses the Phase-1
:func:`~guanlan_v2.orchestration.migration.normalize_legacy_graph_config` loader
discipline (duplicate keys / anchors / aliases / merge keys / timestamps rejected
before any information is lost).

Catalog material + the ``Provenance.model_config_digest`` wiring contract
-------------------------------------------------------------------------
The reviewed :class:`ModelTierMap` is persisted as a catalog ``kind="guardrail"``
text material (Phase-2 precedent: descriptors / config are guardrail materials).
Its physical bytes are :func:`model_tier_map_material_bytes` — the ``sha256+cjson-v1``
canonical JSON of the map — and its content-digest-sealed
:class:`~guanlan_v2.orchestration.refs.ContentRef` is
:func:`build_model_tier_map_material`. That ``ContentRef.content_digest`` is the value
Tasks 10/12 record as ``Provenance.model_config_digest`` on every Phase-8 LLM node run
(see the module note at the bottom). This module makes the digest *available* and
total over the vocabulary; it does NOT wire the Phase-2 runtime (worker.py stays
untouched here).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.catalog import (
    ExecutionSpec,
    ModelTier,
    ResolvedTextMaterial,
)
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    PositiveInt,
    canonical_json,
)
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.migration import normalize_legacy_graph_config
from guanlan_v2.orchestration.refs import ContentRef, LogicalId

__all__ = [
    "ModelTierUnconfigured",
    "ModelTierBinding",
    "ModelTierMap",
    "ORCHESTRATION_TIER_ALIASES",
    "TIER_ORDER",
    "MODEL_TIER_MAP_MATERIAL_ID",
    "MODEL_TIER_MAP_MATERIAL_VERSION",
    "tier_map_from_llm_yaml",
    "resolve_model_binding",
    "model_tier_map_material_bytes",
    "build_model_tier_map_material",
    "model_tier_map_from_material_bytes",
]

#: the closed ``ModelTier`` vocabulary in canonical order (``fast`` < ``reasoner``
#: < ``reasoner_deep``); the bridge is total over exactly this set.
TIER_ORDER: tuple[ModelTier, ...] = ("fast", "reasoner", "reasoner_deep")

#: the ONLY join key between orchestration tiers and llm.yaml ``agent_overrides``.
ORCHESTRATION_TIER_ALIASES: Mapping[ModelTier, str] = {
    "fast": "orchestration-fast",
    "reasoner": "orchestration-reasoner",
    "reasoner_deep": "orchestration-reasoner-deep",
}

#: the reviewed guardrail material identity (a ``LogicalId``, never a file path);
#: the physical file lives at
#: ``config/orchestration/materials/guardrails/model-tier-map.json``.
MODEL_TIER_MAP_MATERIAL_ID: LogicalId = "guardrail.model_tier_map"
MODEL_TIER_MAP_MATERIAL_VERSION = "1"
_MATERIAL_KIND = "guardrail"


class ModelTierUnconfigured(ValueError):
    """A model tier has no explicitly configured provider/model in llm.yaml.

    Raised instead of ever silently binding ``default_provider`` /
    ``default_model`` (the silent-vendor red line).
    """


class ModelTierBinding(DigestModel):
    """The reviewed provider/model (and optional limits) a single tier resolves to."""

    schema_version: Literal["1"] = "1"
    tier: ModelTier
    provider: NonEmptyStr
    model: NonEmptyStr
    max_tokens: PositiveInt | None = None
    timeout_sec: PositiveInt | None = None


class ModelTierMap(DigestModel):
    """The total, reviewed ModelTier -> provider/model map (Task-11 registered payload).

    Exactly three bindings, one per :data:`TIER_ORDER` tier, in canonical order —
    the bridge is total over the closed ``ModelTier`` vocabulary or construction
    fails.
    """

    schema_version: Literal["1"] = "1"
    bindings: tuple[ModelTierBinding, ...]

    @model_validator(mode="after")
    def _total_and_ordered(self) -> "ModelTierMap":
        tiers = tuple(b.tier for b in self.bindings)
        if len(tiers) != len(TIER_ORDER):
            raise ValueError(
                f"ModelTierMap requires exactly {len(TIER_ORDER)} bindings "
                f"(one per tier), got {len(tiers)}"
            )
        if len(set(tiers)) != len(tiers):
            raise ValueError("ModelTierMap bindings must be one-per-tier (no duplicates)")
        if set(tiers) != set(TIER_ORDER):
            raise ValueError(
                f"ModelTierMap must cover every tier {TIER_ORDER!r}; got {tiers!r}"
            )
        if tiers != TIER_ORDER:
            raise ValueError(
                f"ModelTierMap bindings must be canonically ordered {TIER_ORDER!r}; "
                f"got {tiers!r}"
            )
        return self

    def binding_for(self, tier: ModelTier) -> ModelTierBinding:
        """Return the binding for ``tier`` (the map is total, so this always finds)."""
        for b in self.bindings:
            if b.tier == tier:
                return b
        raise ModelTierUnconfigured(f"tier {tier!r} has no binding in the model-tier map")


def _require_str(value: Any, *, tier: str, alias: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelTierUnconfigured(
            f"tier {tier!r}: alias {alias!r} is missing an explicit non-empty "
            f"{field!r} (never falls back to default_provider/default_model)"
        )
    return value


def tier_map_from_llm_yaml(yaml_text: str) -> ModelTierMap:
    """Build the total :class:`ModelTierMap` from explicitly supplied llm.yaml bytes.

    Parsing reuses the Phase-1 strict loader discipline (duplicate keys / anchors /
    aliases rejected). Every tier's alias key must be present under
    ``agent_overrides`` with an explicit ``provider`` + ``model`` (the engine yaml
    key ``timeout`` maps to :attr:`ModelTierBinding.timeout_sec`); any absence raises
    :class:`ModelTierUnconfigured` naming the tier — NEVER a fallback to
    ``default_provider`` / ``default_model``.
    """
    data = normalize_legacy_graph_config(yaml_text, source_format="yaml")
    overrides = data.get("agent_overrides")
    if not isinstance(overrides, Mapping):
        raise ModelTierUnconfigured(
            "config/llm.yaml has no 'agent_overrides' mapping; the orchestration "
            "tier aliases are unconfigured (no silent default fallback)"
        )

    bindings: list[ModelTierBinding] = []
    for tier in TIER_ORDER:
        alias = ORCHESTRATION_TIER_ALIASES[tier]
        entry = overrides.get(alias)
        if entry is None:
            raise ModelTierUnconfigured(
                f"tier {tier!r}: alias {alias!r} is absent from agent_overrides; "
                "the tier is unconfigured (never falls back to "
                "default_provider/default_model)"
            )
        if not isinstance(entry, Mapping):
            raise ModelTierUnconfigured(
                f"tier {tier!r}: alias {alias!r} must be a provider/model mapping"
            )
        provider = _require_str(entry.get("provider"), tier=tier, alias=alias, field="provider")
        model = _require_str(entry.get("model"), tier=tier, alias=alias, field="model")
        bindings.append(
            ModelTierBinding(
                tier=tier,
                provider=provider,
                model=model,
                max_tokens=entry.get("max_tokens"),
                timeout_sec=entry.get("timeout"),  # engine yaml key -> timeout_sec
            )
        )
    return ModelTierMap(bindings=tuple(bindings))


def resolve_model_binding(
    execution: ExecutionSpec, *, tier_map: ModelTierMap
) -> ModelTierBinding:
    """Return the tier binding for an LLM ``ExecutionSpec``.

    A ``DETERMINISTIC`` execution has no model tier and raises ``ValueError``.
    """
    if execution.kind is ExecutionKind.DETERMINISTIC:
        raise ValueError("DETERMINISTIC execution has no model tier to resolve")
    tier = execution.model_tier
    if tier is None:  # defensive: ExecutionSpec's own validator forbids this for LLM
        raise ValueError("LLM execution is missing a model_tier")
    return tier_map.binding_for(tier)


# --------------------------------------------------------------------------- #
# Catalog guardrail material                                                  #
# --------------------------------------------------------------------------- #
def model_tier_map_material_bytes(tier_map: ModelTierMap) -> bytes:
    """The physical guardrail material bytes: canonical (``sha256+cjson-v1``) JSON.

    These are exactly the bytes persisted at
    ``config/orchestration/materials/guardrails/model-tier-map.json``.
    """
    return canonical_json(tier_map).encode("utf-8")


def build_model_tier_map_material(
    tier_map: ModelTierMap,
) -> tuple[ContentRef, ResolvedTextMaterial]:
    """Seal the reviewed map as a ``guardrail`` text material + its ``ContentRef``.

    The digest is computed from the canonical material bytes via the single reviewed
    :func:`~guanlan_v2.orchestration.catalog_runtime.build_text_material` helper — a
    caller never pins a digest by hand. The returned ``ContentRef.content_digest`` is
    the ``Provenance.model_config_digest`` value for Phase-8 LLM node runs (Tasks 10/12).
    """
    return build_text_material(
        id=MODEL_TIER_MAP_MATERIAL_ID,
        version=MODEL_TIER_MAP_MATERIAL_VERSION,
        kind=_MATERIAL_KIND,
        raw=model_tier_map_material_bytes(tier_map),
    )


def model_tier_map_from_material_bytes(raw: bytes) -> ModelTierMap:
    """Parse canonical material bytes back into the reviewed :class:`ModelTierMap`.

    The inverse of :func:`model_tier_map_material_bytes`; used to prove the physical
    material reproduces the reviewed payload (Task 11's registered-payload check).
    """
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("model-tier-map material must decode to a JSON object")
    raw_bindings = data.get("bindings")
    if not isinstance(raw_bindings, list):
        raise ValueError("model-tier-map material must carry a 'bindings' array")
    bindings = tuple(
        ModelTierBinding(
            tier=b["tier"],
            provider=b["provider"],
            model=b["model"],
            max_tokens=b.get("max_tokens"),
            timeout_sec=b.get("timeout_sec"),
        )
        for b in raw_bindings
    )
    return ModelTierMap(bindings=bindings)
