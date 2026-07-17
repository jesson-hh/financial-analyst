# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — cumulative data-only schema registry tests.

Locks the reviewed Phase-3 registry extension: the sealed cumulative data
registry is built from Phase 2's exported public models plus the reviewed
``PHASE3_PUBLIC_MODELS``; building it never mutates or unseals either upstream
sealed registry; the Phase-2 control-model subset stays byte/schema-identical;
every public ContractModel under ``orchestration.data`` introduced by Tasks 3–5
is classified; and the manifest/digest are registration-order independent and
match the frozen golden (NEVER auto-regenerated).

Run: ``pytest tests/orchestration/data/test_data_schema_registry.py -v``
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from guanlan_v2.orchestration.digest import CJSON_VERSION, ContractModel, content_digest
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schema_registry import (
    INTERNAL_MODELS as PHASE1_INTERNAL_MODELS,
    PHASE1_PUBLIC_MODELS,
    default_registry,
)
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    PHASE2_PUBLIC_MODELS,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.data.schema_registry import (
    PHASE3_INTERNAL_MODELS,
    PHASE3_PUBLIC_MODELS,
    Phase3DataRegistryError,
    build_phase3_registry,
)

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "golden" / "data_schema_manifest_v1.json"
)

#: the Task 3–5 data-layer modules whose public contracts this gate governs.
DATA_MODULES: tuple[str, ...] = (
    "guanlan_v2.orchestration.data.symbols",
    "guanlan_v2.orchestration.data.calendar",
    "guanlan_v2.orchestration.data.pit",
    "guanlan_v2.orchestration.data.source",
    "guanlan_v2.orchestration.data.snapshot",
    "guanlan_v2.orchestration.data.render",
    "guanlan_v2.orchestration.data.catalog",
)


def _phase2_digest() -> str:
    return phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest


@pytest.fixture(scope="module")
def phase3_registry():
    return build_phase3_registry(_phase2_digest())


# --------------------------------------------------------------------------- #
# construction + upstream isolation (brief item 11)                            #
# --------------------------------------------------------------------------- #
def test_build_requires_the_exact_phase2_runtime_digest():
    with pytest.raises(Phase3DataRegistryError):
        build_phase3_registry("0" * 64)


def test_registry_is_sealed_and_fresh(phase3_registry):
    assert phase3_registry.sealed is True
    other = build_phase3_registry(_phase2_digest())
    assert other is not phase3_registry
    assert other.registry_digest == phase3_registry.registry_digest


def test_phase1_and_phase2_registries_unchanged_after_phase3_build():
    ph1_before = default_registry().registry_digest
    ph2_before = _phase2_digest()
    reg = build_phase3_registry(ph2_before)
    assert reg.registry_digest  # built
    assert default_registry().registry_digest == ph1_before
    assert _phase2_digest() == ph2_before
    # both upstream registries remain sealed.
    assert default_registry().sealed is True
    assert phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).sealed is True


def test_phase2_control_subset_is_schema_identical(phase3_registry):
    """Every inherited Phase-1/Phase-2 manifest entry is byte-identical in Phase 3."""
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    ph3_manifest = {e.key: e.json_schema_digest for e in phase3_registry.manifest()}
    for entry in ph2.manifest():
        assert ph3_manifest[entry.key] == entry.json_schema_digest, (
            f"inherited schema {entry.key} drifted in the Phase-3 registry"
        )


def test_phase3_never_registers_into_a_sealed_registry(phase3_registry):
    from guanlan_v2.orchestration.data.source import DataMethodSpec

    with pytest.raises(Exception):
        phase3_registry.register(DataMethodSpec)


# --------------------------------------------------------------------------- #
# the reviewed data payloads resolve (and RowSet does not exist)               #
# --------------------------------------------------------------------------- #
def test_all_seven_named_result_envelopes_resolve(phase3_registry):
    for name in (
        "InstrumentNameDataResult", "OHLCVDataResult", "IndicatorDataResult",
        "VerifiedSnapshotDataResult", "FundamentalDataResult", "NewsDataResult",
        "SignalDataResult",
    ):
        model = phase3_registry.resolve(SchemaRef(name=name, version="1"))
        assert model.__name__ == name


def test_task3_and_task4_payloads_resolve(phase3_registry):
    for name in (
        "LimitRulePolicy", "RawRowCandidate", "FreshnessPolicy",
        "DataFetchRefusalDetails",
    ):
        assert phase3_registry.resolve(SchemaRef(name=name, version="1")).__name__ == name


def test_prefetch_contracts_resolve(phase3_registry):
    for name in ("DataPrefetchOperation", "DataBridgePrefetchBinding", "RenderedDataBlock"):
        assert phase3_registry.resolve(SchemaRef(name=name, version="1")).__name__ == name


def test_rowset_is_absent_everywhere(phase3_registry):
    import guanlan_v2.orchestration.data.source as source_mod

    assert not hasattr(source_mod, "RowSet")
    with pytest.raises(Exception):
        phase3_registry.resolve(SchemaRef(name="RowSet", version="1"))
    assert "RowSet@1" not in {e.key for e in phase3_registry.manifest()}


# --------------------------------------------------------------------------- #
# the data-only completeness gate (brief item 12)                              #
# --------------------------------------------------------------------------- #
def _discover_data_public_models() -> set[type[ContractModel]]:
    found: set[type[ContractModel]] = set()
    for module_name in DATA_MODULES:
        module = importlib.import_module(module_name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, ContractModel)
                and obj.__module__ == module_name
                and not obj.__name__.startswith("_")
            ):
                found.add(obj)
    return found


def test_every_data_public_contract_is_classified():
    """Tasks 3–5 data contracts are in PHASE3_PUBLIC_MODELS or the reviewed internal
    map (pre-Phase-3 data value objects keep their Phase-1 classification). Task 9
    memory modules are deliberately checked by their separate full-registry test."""
    discovered = _discover_data_public_models()
    phase3 = set(PHASE3_PUBLIC_MODELS) | set(PHASE3_INTERNAL_MODELS)
    phase1 = set(PHASE1_PUBLIC_MODELS) | set(PHASE1_INTERNAL_MODELS)
    unclassified = discovered - phase3 - phase1
    assert not unclassified, (
        "unreviewed public data contracts: "
        + ", ".join(sorted(m.__name__ for m in unclassified))
    )


def test_phase3_public_and_internal_are_disjoint_with_reasons():
    overlap = set(PHASE3_PUBLIC_MODELS) & set(PHASE3_INTERNAL_MODELS)
    assert not overlap
    assert len(set(PHASE3_PUBLIC_MODELS)) == len(PHASE3_PUBLIC_MODELS)
    for model, reason in PHASE3_INTERNAL_MODELS.items():
        assert isinstance(reason, str) and reason.strip(), model.__name__


def test_phase3_internal_map_names_the_frozen_carriers():
    from guanlan_v2.orchestration.data.source import (
        DataInvocationScope,
        DataReadOutcome,
        ResolvedDataMethodPolicy,
        VerifiedDataCacheHit,
    )

    for model in (
        DataInvocationScope, ResolvedDataMethodPolicy, VerifiedDataCacheHit,
        DataReadOutcome,
    ):
        assert model in PHASE3_INTERNAL_MODELS
        assert model not in PHASE3_PUBLIC_MODELS


def test_phase3_public_tuple_contains_the_briefed_models():
    from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails
    from guanlan_v2.orchestration.data.symbols import LimitRulePolicy
    from guanlan_v2.orchestration.data.catalog import (
        DataBridgePrefetchBinding,
        DataPrefetchOperation,
    )

    names = {m.__name__ for m in PHASE3_PUBLIC_MODELS}
    assert LimitRulePolicy in PHASE3_PUBLIC_MODELS
    assert DataFetchRefusalDetails in PHASE3_PUBLIC_MODELS
    assert DataPrefetchOperation in PHASE3_PUBLIC_MODELS
    assert DataBridgePrefetchBinding in PHASE3_PUBLIC_MODELS
    for envelope in (
        "InstrumentNameDataResult", "OHLCVDataResult", "IndicatorDataResult",
        "VerifiedSnapshotDataResult", "FundamentalDataResult", "NewsDataResult",
        "SignalDataResult",
    ):
        assert envelope in names


# --------------------------------------------------------------------------- #
# order independence + frozen golden (brief item 13)                           #
# --------------------------------------------------------------------------- #
def test_reversed_phase3_registration_order_same_manifest_and_digest(phase3_registry):
    from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

    reversed_reg = Phase2RuntimeRegistry()
    for model in reversed(tuple(PHASE2_PUBLIC_MODELS) + tuple(PHASE3_PUBLIC_MODELS)):
        reversed_reg.register(model)
    reversed_reg.seal()
    assert reversed_reg.manifest() == phase3_registry.manifest()
    assert reversed_reg.registry_digest == phase3_registry.registry_digest


def test_golden_manifest_exists_and_is_frozen_format():
    assert GOLDEN_PATH.exists(), f"missing golden manifest: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    assert isinstance(doc["entries"], list) and doc["entries"]
    assert doc["base_registry_digest"] == PHASE2_BASE_REGISTRY_DIGEST
    assert doc["phase2_runtime_registry_digest"] == _phase2_digest()


def test_registry_matches_golden_manifest(phase3_registry):
    """This test NEVER regenerates the golden — a changed manifest is a reviewed
    contract change regenerated by hand and re-reviewed."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    manifest = {e.key: e.json_schema_digest for e in phase3_registry.manifest()}
    assert set(manifest) == set(golden), (
        "registered schema key set drifted from data_schema_manifest_v1.json; "
        "regenerate + re-review by hand if intended"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert phase3_registry.registry_digest == doc["registry_digest"]


def test_golden_digests_are_recomputable_from_the_models():
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    for model in PHASE3_PUBLIC_MODELS:
        key = f"{model.__name__}@{model.model_fields['schema_version'].default}"
        assert golden[key] == content_digest(model.model_json_schema())


def test_phase3_digest_constant_matches_the_built_registry(phase3_registry):
    from guanlan_v2.orchestration.data import schema_registry as reg_mod

    assert reg_mod.PHASE3_DATA_REGISTRY_DIGEST == phase3_registry.registry_digest
