# -*- coding: utf-8 -*-
"""Phase 8 - Task 3: the ModelTier -> llm.yaml bridge (catalog-owned config material).

Written test-first (RED until ``guanlan_v2/orchestration/model_tiers.py`` exists).

Red-line proven here (plan verbatim): the tier map is built from **explicitly
supplied ``config/llm.yaml`` bytes** — no orchestration code calls engine
``find_config`` / ``LLMClient`` config discovery — and a tier without a
configured provider/model raises :class:`ModelTierUnconfigured`, NEVER silently
falling back to ``default_provider`` / ``default_model``.

Invariant matrix:
  * map matrix          -> the repo llm.yaml binds every tier to deepseek per clause (g);
  * totality            -> the map is total over the closed ``ModelTier`` vocabulary
    (exactly three bindings, one per tier, canonical order), or construction fails;
  * per-tier unconfigured -> removing any one alias key raises ``ModelTierUnconfigured``
    naming that tier (proven for each tier);
  * no silent default   -> a yaml with default_provider/default_model but no alias
    keys still raises (never binds the default vendor);
  * resolver            -> ``resolve_model_binding`` returns the tier binding for an
    LLM ExecutionSpec and rejects a DETERMINISTIC one;
  * strict-YAML         -> duplicate keys / anchors are rejected (Phase-1 loader discipline);
  * material digest     -> the physical guardrail material bytes reproduce the reviewed
    ``ModelTierMap`` via ``catalog_material_digest`` (stable, pinned);
  * no-find_config      -> an AST/source scan of ``model_tiers.py`` proves it never
    imports or references engine config discovery;
  * P7 regression       -> the additive alias edit leaves the planner seat override intact.

Run from repo root: ``pytest tests/orchestration/test_model_tiers.py -v``
"""
from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

from guanlan_v2.orchestration.catalog import (
    ExecutionSpec,
    ModelTier,
    ResolvedTextMaterial,
    catalog_material_digest,
)
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.migration import normalize_legacy_graph_config

from guanlan_v2.orchestration.model_tiers import (
    MODEL_TIER_MAP_MATERIAL_ID,
    ORCHESTRATION_TIER_ALIASES,
    ModelTierBinding,
    ModelTierMap,
    ModelTierUnconfigured,
    build_model_tier_map_material,
    model_tier_map_from_material_bytes,
    model_tier_map_material_bytes,
    resolve_model_binding,
    tier_map_from_llm_yaml,
)

# --------------------------------------------------------------------------- #
# Paths + explicit byte supply (never find_config)                            #
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_YAML = REPO_ROOT / "config" / "llm.yaml"
MATERIAL_FILE = (
    REPO_ROOT / "config" / "orchestration" / "materials" / "guardrails"
    / "model-tier-map.json"
)
MODEL_TIERS_SRC = REPO_ROOT / "guanlan_v2" / "orchestration" / "model_tiers.py"

#: pinned reviewed material digest (drift guard); sealed from the physical bytes.
EXPECTED_MATERIAL_DIGEST = (
    "bcbc4aeb8e63698a32571c7a110c81a82d49bd42eca32997ffa521f2ca4b4994"
)

TIER_ORDER = ("fast", "reasoner", "reasoner_deep")


def _llm_yaml_text() -> str:
    """Explicitly supply llm.yaml BYTES (decoded) — never discovered via find_config."""
    return LLM_YAML.read_text(encoding="utf-8")


def _yaml_with_overrides(overrides: dict) -> str:
    """Emit a minimal llm.yaml doc with the given agent_overrides flow mappings."""
    lines = ["default_provider: deepseek", "default_model: deepseek-chat", "agent_overrides:"]
    for key, entry in overrides.items():
        if entry is None:
            continue
        inner = ", ".join(f"{k}: {v}" for k, v in entry.items())
        lines.append(f"  {key}: {{{inner}}}")
    return "\n".join(lines) + "\n"


def _yaml_with_providers(provider_names: list, overrides: dict) -> str:
    """Emit an llm.yaml doc that DOES declare a ``providers:`` section.

    Mirrors the real repo yaml's shape (a ``providers:`` mapping + ``agent_overrides``)
    so the provider cross-check surface can be exercised: an alias may name a provider
    that is present in — or absent from — ``providers``.
    """
    lines = ["default_provider: deepseek", "default_model: deepseek-chat", "providers:"]
    for name in provider_names:
        lines.append(f"  {name}: {{api_key_env: {name.upper()}_API_KEY}}")
    lines.append("agent_overrides:")
    for key, entry in overrides.items():
        if entry is None:
            continue
        inner = ", ".join(f"{k}: {v}" for k, v in entry.items())
        lines.append(f"  {key}: {{{inner}}}")
    return "\n".join(lines) + "\n"


FULL_ALIASES: dict = {
    "orchestration-fast": {"provider": "deepseek", "model": "deepseek-chat"},
    "orchestration-reasoner": {"provider": "deepseek", "model": "deepseek-reasoner"},
    "orchestration-reasoner-deep": {
        "provider": "deepseek", "model": "deepseek-reasoner",
        "max_tokens": 8192, "timeout": 300,
    },
}


def _llm_exec(tier: str) -> ExecutionSpec:
    return ExecutionSpec(kind=ExecutionKind.LLM, model_tier=tier)


# =========================================================================== #
# Map matrix — repo llm.yaml binds every tier to deepseek (clause g).          #
# =========================================================================== #
def test_repo_llm_yaml_binds_every_tier_to_deepseek():
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    by_tier = {b.tier: b for b in m.bindings}
    assert set(by_tier) == set(TIER_ORDER)

    fast = by_tier["fast"]
    assert (fast.provider, fast.model) == ("deepseek", "deepseek-chat")
    assert fast.max_tokens is None and fast.timeout_sec is None

    reasoner = by_tier["reasoner"]
    assert (reasoner.provider, reasoner.model) == ("deepseek", "deepseek-reasoner")
    assert reasoner.max_tokens is None and reasoner.timeout_sec is None

    deep = by_tier["reasoner_deep"]
    assert (deep.provider, deep.model) == ("deepseek", "deepseek-reasoner")
    assert deep.max_tokens == 8192 and deep.timeout_sec == 300


def test_synthetic_full_yaml_matches_the_reviewed_values():
    m = tier_map_from_llm_yaml(_yaml_with_overrides(FULL_ALIASES))
    assert tuple(b.tier for b in m.bindings) == TIER_ORDER
    assert m == tier_map_from_llm_yaml(_llm_yaml_text())


# =========================================================================== #
# Totality over the closed ModelTier vocabulary.                               #
# =========================================================================== #
def test_map_is_total_over_the_model_tier_literal():
    vocab = set(typing.get_args(ModelTier))
    assert vocab == set(TIER_ORDER)
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    assert {b.tier for b in m.bindings} == vocab
    # canonical order fast < reasoner < reasoner_deep.
    assert tuple(b.tier for b in m.bindings) == TIER_ORDER


def test_map_validator_rejects_missing_incomplete_or_misordered_bindings():
    fast = ModelTierBinding(tier="fast", provider="deepseek", model="deepseek-chat")
    reasoner = ModelTierBinding(tier="reasoner", provider="deepseek", model="deepseek-reasoner")
    deep = ModelTierBinding(tier="reasoner_deep", provider="deepseek", model="deepseek-reasoner")
    # a good, canonically ordered triple validates.
    assert ModelTierMap(bindings=(fast, reasoner, deep))
    # fewer than three tiers -> reject.
    with pytest.raises(ValueError):
        ModelTierMap(bindings=(fast, reasoner))
    # duplicate tier -> reject.
    with pytest.raises(ValueError):
        ModelTierMap(bindings=(fast, reasoner, reasoner))
    # wrong order -> reject.
    with pytest.raises(ValueError):
        ModelTierMap(bindings=(reasoner, fast, deep))


# =========================================================================== #
# Per-tier unconfigured raise + the silent-default red line.                    #
# =========================================================================== #
@pytest.mark.parametrize("tier,alias", list(ORCHESTRATION_TIER_ALIASES.items()))
def test_removing_any_alias_key_raises_unconfigured_naming_the_tier(tier, alias):
    overrides = {k: v for k, v in FULL_ALIASES.items() if k != alias}
    with pytest.raises(ModelTierUnconfigured) as exc:
        tier_map_from_llm_yaml(_yaml_with_overrides(overrides))
    assert tier in str(exc.value)


@pytest.mark.parametrize("drop", ["provider", "model"])
def test_missing_provider_or_model_raises_unconfigured(drop):
    overrides = {k: dict(v) for k, v in FULL_ALIASES.items()}
    overrides["orchestration-reasoner"].pop(drop)
    with pytest.raises(ModelTierUnconfigured):
        tier_map_from_llm_yaml(_yaml_with_overrides(overrides))


def test_never_falls_back_to_default_provider_or_model():
    # a yaml with the defaults present but NO alias keys must still raise —
    # the silent-vendor red line (绝不静默回落到未配置 vendor).
    yaml_text = "default_provider: deepseek\ndefault_model: deepseek-chat\nagent_overrides: {}\n"
    with pytest.raises(ModelTierUnconfigured):
        tier_map_from_llm_yaml(yaml_text)
    # and a yaml with no agent_overrides mapping at all.
    with pytest.raises(ModelTierUnconfigured):
        tier_map_from_llm_yaml("default_provider: deepseek\ndefault_model: deepseek-chat\n")


# =========================================================================== #
# Unconfigured-vendor red line: an alias may not name a provider that is        #
# absent from the yaml's own ``providers:`` section (the ghost-vendor surface). #
# =========================================================================== #
def test_alias_naming_a_provider_absent_from_providers_raises():
    # providers: declares ONLY deepseek; reasoner_deep's alias names openai, which is
    # NOT in providers -> the unconfigured vendor the red line guards. Must raise
    # ModelTierUnconfigured naming BOTH the tier AND the missing provider (never a
    # silent build against a vendor with no provider config).
    overrides = {k: dict(v) for k, v in FULL_ALIASES.items()}
    overrides["orchestration-reasoner-deep"] = {
        "provider": "openai", "model": "gpt-4o", "max_tokens": 8192, "timeout": 300,
    }
    yaml_text = _yaml_with_providers(["deepseek"], overrides)
    with pytest.raises(ModelTierUnconfigured) as exc:
        tier_map_from_llm_yaml(yaml_text)
    msg = str(exc.value)
    assert "reasoner_deep" in msg
    assert "openai" in msg


def test_alias_provider_present_in_providers_builds():
    # same shape, but every alias names deepseek, which IS declared in providers -> builds.
    yaml_text = _yaml_with_providers(["deepseek"], FULL_ALIASES)
    m = tier_map_from_llm_yaml(yaml_text)
    assert tuple(b.tier for b in m.bindings) == TIER_ORDER
    assert all(b.provider == "deepseek" for b in m.bindings)


def test_repo_llm_yaml_providers_cover_every_alias_provider():
    # the real repo llm.yaml declares providers: for every vendor its orchestration
    # aliases name, so the cross-check leaves the reviewed build (and its digest) intact.
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    assert {b.provider for b in m.bindings} == {"deepseek"}


# =========================================================================== #
# Resolver.                                                                     #
# =========================================================================== #
def test_resolve_model_binding_returns_the_tier_binding():
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    for tier in TIER_ORDER:
        binding = resolve_model_binding(_llm_exec(tier), tier_map=m)
        assert binding.tier == tier
        assert binding.provider == "deepseek"


def test_resolve_model_binding_rejects_deterministic_execution():
    from guanlan_v2.orchestration.refs import ContentRef

    m = tier_map_from_llm_yaml(_llm_yaml_text())
    handler = ContentRef(id="handler.x", version="1", content_digest="0" * 64)
    det = ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler)
    with pytest.raises(ValueError):
        resolve_model_binding(det, tier_map=m)


# =========================================================================== #
# Strict YAML discipline (Phase-1 loader).                                      #
# =========================================================================== #
def test_strict_yaml_rejects_duplicate_alias_key():
    dup = (
        "default_provider: deepseek\n"
        "agent_overrides:\n"
        "  orchestration-fast: {provider: deepseek, model: deepseek-chat}\n"
        "  orchestration-fast: {provider: deepseek, model: deepseek-reasoner}\n"
    )
    with pytest.raises(ValueError):
        tier_map_from_llm_yaml(dup)


def test_strict_yaml_rejects_anchors_and_aliases():
    anchored = (
        "default_provider: deepseek\n"
        "agent_overrides:\n"
        "  orchestration-fast: &a {provider: deepseek, model: deepseek-chat}\n"
        "  orchestration-reasoner: *a\n"
        "  orchestration-reasoner-deep: {provider: deepseek, model: deepseek-reasoner}\n"
    )
    with pytest.raises(ValueError):
        tier_map_from_llm_yaml(anchored)


# =========================================================================== #
# ORCHESTRATION_TIER_ALIASES — the only join key.                              #
# =========================================================================== #
def test_orchestration_tier_aliases_are_the_only_join_key():
    assert ORCHESTRATION_TIER_ALIASES == {
        "fast": "orchestration-fast",
        "reasoner": "orchestration-reasoner",
        "reasoner_deep": "orchestration-reasoner-deep",
    }
    # the map is built ONLY through these keys: renaming a key in the yaml breaks it.
    renamed = {("x-" + k): v for k, v in FULL_ALIASES.items()}
    with pytest.raises(ModelTierUnconfigured):
        tier_map_from_llm_yaml(_yaml_with_overrides(renamed))


# =========================================================================== #
# Catalog guardrail material — digest reproduction + stability + round-trip.    #
# =========================================================================== #
def test_material_id_is_the_reviewed_guardrail_logical_id():
    assert MODEL_TIER_MAP_MATERIAL_ID == "guardrail.model_tier_map"


def test_physical_material_bytes_reproduce_the_reviewed_map_digest():
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    ref, mat = build_model_tier_map_material(m)
    assert ref.id == MODEL_TIER_MAP_MATERIAL_ID
    assert ref.version == "1"
    assert mat.kind == "guardrail"

    # the physical file bytes reproduce the same material digest.
    assert MATERIAL_FILE.is_file()
    physical = MATERIAL_FILE.read_bytes()
    physical_mat = ResolvedTextMaterial(ref=ref, kind="guardrail", raw_utf8=physical)
    assert catalog_material_digest(physical_mat) == ref.content_digest
    # and the physical bytes ARE the canonical material bytes of the reviewed map.
    assert physical == model_tier_map_material_bytes(m)


def test_material_digest_is_stable_and_pinned():
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    a, _ = build_model_tier_map_material(m)
    b, _ = build_model_tier_map_material(m)
    assert a.content_digest == b.content_digest  # deterministic
    assert a.content_digest == EXPECTED_MATERIAL_DIGEST  # drift guard


def test_material_bytes_round_trip_back_to_the_reviewed_map():
    m = tier_map_from_llm_yaml(_llm_yaml_text())
    raw = model_tier_map_material_bytes(m)
    assert model_tier_map_from_material_bytes(raw) == m
    # and the physical file round-trips too.
    assert model_tier_map_from_material_bytes(MATERIAL_FILE.read_bytes()) == m


# =========================================================================== #
# No-find_config proof (AST + source scan of model_tiers.py).                   #
# =========================================================================== #
def test_model_tiers_never_imports_engine_config_discovery():
    # AST scan (not a raw substring scan — the red-line prose in the docstring is
    # allowed to NAME find_config/LLMClient; what is forbidden is REFERENCING them
    # as code: an import, a called name, or an attribute access).
    tree = ast.parse(MODEL_TIERS_SRC.read_text(encoding="utf-8"))
    imported: list[str] = []
    referenced_names: set[str] = set()
    referenced_attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_attrs.add(node.attr)

    banned = {"find_config", "LLMClient"}
    assert not (banned & referenced_names), f"engine config discovery referenced: {banned & referenced_names}"
    assert not (banned & referenced_attrs), f"engine config discovery referenced: {banned & referenced_attrs}"

    # every imported module is stdlib or an orchestration sibling; never the engine
    # or the datafeed/live layers (where implicit config discovery would live).
    for mod in imported:
        assert not mod.startswith("financial_analyst"), f"engine import leaked: {mod}"
        assert not mod.startswith("guanlan_v2.datafeed"), f"datafeed import leaked: {mod}"


# =========================================================================== #
# P7 planner-gateway regression — the additive edit left the seat intact.       #
# =========================================================================== #
def test_planner_agent_override_is_untouched_by_the_additive_edit():
    data = normalize_legacy_graph_config(_llm_yaml_text(), source_format="yaml")
    overrides = data["agent_overrides"]
    # the three new alias keys landed additively.
    for alias in ORCHESTRATION_TIER_ALIASES.values():
        assert alias in overrides, f"missing additive alias key {alias!r}"
    # the P7 planner seat override is unchanged (deepseek reasoner).
    planner = overrides["planner"]
    assert planner["provider"] == "deepseek"
    assert planner["model"] == "deepseek-reasoner"
    # Task-0 gate line preserved (string check).
    assert "default_provider: deepseek" in _llm_yaml_text()
