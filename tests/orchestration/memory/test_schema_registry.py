# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — cumulative FULL (data + memory) schema registry tests.

Locks the reviewed full-registry extension: the sealed registry is built from
the exact immutable Task-5 data snapshot plus the reviewed
``PHASE3_MEMORY_PUBLIC_MODELS``; building it never mutates any upstream sealed
registry; every inherited Phase-1/2/data entry stays byte-identical; every
public ContractModel under ``orchestration.memory`` is classified; the data-only
digest/golden remain byte-for-byte rebuildable; and the manifest/digest match
the frozen golden (NEVER auto-regenerated).

Run: ``pytest tests/orchestration/memory/test_schema_registry.py -v``
"""
from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

import guanlan_v2.orchestration.memory as _memory_pkg
from guanlan_v2.orchestration.digest import CJSON_VERSION, ContractModel, content_digest
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.data.schema_registry import (
    PHASE3_PUBLIC_MODELS as PHASE3_DATA_PUBLIC_MODELS,
    build_phase3_registry,
)
from guanlan_v2.orchestration.memory.schema_registry import (
    PHASE3_MEMORY_INTERNAL_MODELS,
    PHASE3_MEMORY_PUBLIC_MODELS,
    Phase3FullRegistryError,
    build_phase3_full_registry,
)
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    phase2_runtime_registry,
)

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "golden" / "phase3_full_schema_manifest_v1.json"
)
DATA_GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "golden" / "data_schema_manifest_v1.json"
)


def _data_digest() -> str:
    from guanlan_v2.orchestration.data import schema_registry as data_reg

    return data_reg.PHASE3_DATA_REGISTRY_DIGEST


@pytest.fixture(scope="module")
def full_registry():
    return build_phase3_full_registry(_data_digest())


# --------------------------------------------------------------------------- #
# construction + upstream isolation                                            #
# --------------------------------------------------------------------------- #
def test_build_requires_the_exact_phase3_data_digest():
    with pytest.raises(Phase3FullRegistryError):
        build_phase3_full_registry("0" * 64)


def test_registry_is_sealed_fresh_and_deterministic(full_registry):
    assert full_registry.sealed is True
    other = build_phase3_full_registry(_data_digest())
    assert other is not full_registry
    assert other.registry_digest == full_registry.registry_digest


def test_upstream_registries_unchanged_and_data_only_rebuildable(full_registry):
    """The data-only builder/tuple/digest/golden remain byte-for-byte rebuildable."""
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    data = build_phase3_registry(ph2.registry_digest)
    doc = json.loads(DATA_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert data.registry_digest == doc["registry_digest"]
    assert _data_digest() == doc["registry_digest"]
    assert ph2.sealed is True and data.sealed is True


def test_inherited_schemas_are_byte_identical_and_simultaneously_resolvable(full_registry):
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    data = build_phase3_registry(ph2.registry_digest)
    full_manifest = {e.key: e.json_schema_digest for e in full_registry.manifest()}
    for upstream in (ph2, data):
        for entry in upstream.manifest():
            assert full_manifest[entry.key] == entry.json_schema_digest, (
                f"inherited schema {entry.key} drifted in the full registry"
            )
    # both digests resolve side by side — no 'latest' alias replaced the data one.
    assert data.registry_digest != full_registry.registry_digest


def test_full_registry_never_registers_after_seal(full_registry):
    from guanlan_v2.orchestration.memory.models import MemoryRecord

    with pytest.raises(Exception):
        full_registry.register(MemoryRecord)


# --------------------------------------------------------------------------- #
# the reviewed memory payloads resolve                                          #
# --------------------------------------------------------------------------- #
def test_every_reviewed_memory_model_resolves(full_registry):
    for model in PHASE3_MEMORY_PUBLIC_MODELS:
        ref = SchemaRef(name=model.__name__,
                        version=model.model_fields["schema_version"].default)
        assert full_registry.resolve(ref) is model


def test_recovery_preparations_are_registered_controls(full_registry):
    for name in ("MemoryCutoverPreparation", "MemorySnapshotCapturePreparation",
                 "MemoryProposalPreparation"):
        assert full_registry.resolve(SchemaRef(name=name, version="1")).__name__ == name


def test_briefed_payload_spot_list_resolves(full_registry):
    for name in (
        "MemoryCapturePolicy", "MemoryCutoverSourcePayload", "MemoryCutoverManifest",
        "MemoryCutoverAttestation", "MemorySourceStateEntry", "MemorySourceStateSnapshot",
        "MemoryOwnerApplySemanticReceipt", "MemoryReviewEvidence", "MemoryRecord",
        "MemorySnapshot", "MemoryQuery", "MemorySelection", "MemoryProposalGrant",
        "MemoryProposalRequest", "MemoryProposal", "MemoryProposalReceipt",
        "MemoryProposalDecision", "MemoryBridgePrefetchBinding",
        "MemoryFacadeDescriptor", "RenderedMemoryBlock",
        "AgentPendingLocator", "ConsolePendingEventLocator",
        "LegacyCutoverEvidence", "AgentAppliedEvidence", "ConsoleAppliedEvidence",
    ):
        assert full_registry.resolve(SchemaRef(name=name, version="1")).__name__ == name


def test_internal_carriers_are_never_registered(full_registry):
    registered = {e.schema_ref.name for e in full_registry.manifest()}
    for model in PHASE3_MEMORY_INTERNAL_MODELS:
        assert model.__name__ not in registered, model.__name__


def test_data_only_registry_contains_no_memory_schema():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    data = build_phase3_registry(ph2.registry_digest)
    data_keys = {e.schema_ref.name for e in data.manifest()}
    for model in PHASE3_MEMORY_PUBLIC_MODELS:
        assert model.__name__ not in data_keys


# --------------------------------------------------------------------------- #
# completeness over the memory package                                         #
# --------------------------------------------------------------------------- #
#: Phase-5 (Bootstrap Lane 0) modules that physically live under the memory package
#: but are governed by the Phase-5 registry/completeness firewall (Task 9), NOT the
#: Phase-3 memory registry — excluded from this Phase-3 memory completeness walk the
#: same way the Phase-1 ``test_contract_completeness`` firewall excludes the Phase-4/5
#: modules. ``memory.experience`` (Task 4) defines the Phase-5 experience-library
#: contracts; Task 9 stands up their Phase-5 registry classification.
PHASE5_MEMORY_MODULES: frozenset[str] = frozenset(
    {"guanlan_v2.orchestration.memory.experience"}
)


def _discover_memory_contract_models() -> set[type[ContractModel]]:
    found: set[type[ContractModel]] = set()
    for mod_info in pkgutil.walk_packages(
        _memory_pkg.__path__, prefix=_memory_pkg.__name__ + "."
    ):
        if mod_info.name in PHASE5_MEMORY_MODULES:
            continue  # Phase-5 module — governed by the Phase-5 firewall (Task 9)
        module = importlib.import_module(mod_info.name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, ContractModel)
                and obj.__module__ == mod_info.name
                and not obj.__name__.startswith("_")
            ):
                found.add(obj)
    return found


def test_every_memory_contract_model_is_a_reviewed_registered_payload():
    """No memory module may define an unclassified public ContractModel: the
    registered surface is exactly PHASE3_MEMORY_PUBLIC_MODELS (internal carriers
    are deliberately non-ContractModel types)."""
    discovered = _discover_memory_contract_models()
    assert discovered == set(PHASE3_MEMORY_PUBLIC_MODELS), (
        "unreviewed memory contracts: "
        + ", ".join(sorted(m.__name__ for m in
                           discovered.symmetric_difference(PHASE3_MEMORY_PUBLIC_MODELS)))
    )
    for model, reason in PHASE3_MEMORY_INTERNAL_MODELS.items():
        assert not issubclass(model, ContractModel)
        assert isinstance(reason, str) and reason.strip()


def test_public_tuple_has_no_duplicates_and_no_data_overlap():
    assert len(set(PHASE3_MEMORY_PUBLIC_MODELS)) == len(PHASE3_MEMORY_PUBLIC_MODELS)
    assert not set(PHASE3_MEMORY_PUBLIC_MODELS) & set(PHASE3_DATA_PUBLIC_MODELS)


# --------------------------------------------------------------------------- #
# order independence + frozen golden                                            #
# --------------------------------------------------------------------------- #
def test_reversed_registration_order_same_manifest_and_digest(full_registry):
    from guanlan_v2.orchestration.runtime_contracts import (
        Phase2RuntimeRegistry,
        phase2_public_models,
    )

    reversed_reg = Phase2RuntimeRegistry()
    for model in reversed(
        tuple(phase2_public_models())
        + tuple(PHASE3_DATA_PUBLIC_MODELS)
        + tuple(PHASE3_MEMORY_PUBLIC_MODELS)
    ):
        reversed_reg.register(model)
    reversed_reg.seal()
    assert reversed_reg.manifest() == full_registry.manifest()
    assert reversed_reg.registry_digest == full_registry.registry_digest


def test_golden_manifest_exists_and_is_frozen_format():
    assert GOLDEN_PATH.exists(), f"missing golden manifest: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    assert isinstance(doc["entries"], list) and doc["entries"]
    assert doc["base_registry_digest"] == _data_digest()


def test_registry_matches_golden_manifest(full_registry):
    """This test NEVER regenerates the golden — a changed manifest is a reviewed
    contract change regenerated by hand and re-reviewed."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    manifest = {e.key: e.json_schema_digest for e in full_registry.manifest()}
    assert set(manifest) == set(golden), (
        "registered schema key set drifted from phase3_full_schema_manifest_v1.json; "
        "regenerate + re-review by hand if intended"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert full_registry.registry_digest == doc["registry_digest"]


def test_golden_digests_are_recomputable_from_the_models():
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    for model in PHASE3_MEMORY_PUBLIC_MODELS:
        key = f"{model.__name__}@{model.model_fields['schema_version'].default}"
        assert golden[key] == content_digest(model.model_json_schema())


def test_full_digest_constants_match_the_built_registry(full_registry):
    from guanlan_v2.orchestration.memory import schema_registry as reg_mod

    assert reg_mod.PHASE3_FULL_REGISTRY_DIGEST == full_registry.registry_digest
    assert reg_mod.PHASE3_FULL_BASE_REGISTRY_DIGEST == _data_digest()
    assert reg_mod.PHASE3_FULL_REGISTRY_DIGEST != reg_mod.PHASE3_FULL_BASE_REGISTRY_DIGEST
