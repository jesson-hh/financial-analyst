# -*- coding: utf-8 -*-
"""Task 13 — sealed ``default_registry()`` population + frozen golden manifest.

Written test-first (RED until ``default_registry`` / ``PHASE1_PUBLIC_MODELS`` and
the golden manifest exist). Locks the reviewed, sealed Phase-1 schema registry:

* it is sealed before it is handed out (no post-hoc registration);
* every registered model declares a **non-empty** ``schema_version`` (never
  inferred), its JSON Schema closes ``schema_version`` with the matching
  ``const``, it forbids extra fields and is a frozen immutable fact;
* the registry key equals ``Name@version`` and resolves back to the model;
* the Task-8 memory / snapshot facts (``MemoryRecordRef``, both empty-memory
  facts, ``ContextSnapshot``, ``InputSnapshot``) are registered with their
  locator / hash / ref / session-scope projections intact;
* representative payloads round-trip through the registry, and payload validation
  rejects both a self-declared version different from the resolved ``SchemaRef``
  and any extra field;
* the manifest's per-model JSON-schema digests and the registry digest match the
  frozen ``golden/schema_manifest_v1.json`` — which this test NEVER regenerates —
  and are registration-order independent.

Run from repo root: ``pytest tests/orchestration/test_registry_population.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.context import (
    ContextSnapshot,
    EmptyMemorySelection,
    EmptyMemorySnapshot,
    InputSnapshot,
    MemoryRecordRef,
)
from guanlan_v2.orchestration.digest import (
    CJSON_VERSION,
    ContractModel,
    DigestModel,
    content_digest,
)
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schema_registry import (
    PHASE1_PUBLIC_MODELS,
    SchemaRegistry,
    SchemaVersionMismatchError,
    UnknownSchemaError,
    default_registry,
)
from guanlan_v2.orchestration.schemas import (
    PortfolioDecision,
    ResearchPlan,
    SentimentReport,
)

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "schema_manifest_v1.json"

UTC = timezone.utc
_D64 = "a" * 64  # a well-formed 64-hex digest for building simple facts


# --------------------------------------------------------------------------- #
# registry is sealed and pure                                                 #
# --------------------------------------------------------------------------- #
def test_default_registry_is_sealed():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry)
    assert reg.sealed is True


def test_default_registry_refuses_further_registration():
    from guanlan_v2.orchestration.schema_registry import RegistrySealedError

    reg = default_registry()
    with pytest.raises(RegistrySealedError):
        reg.register(ResearchPlan)


def test_default_registry_returns_fresh_but_equal_instances():
    """No global registry at import: each call constructs + seals a fresh object,
    yet the reviewed content (and therefore the digest) is identical."""
    a = default_registry()
    b = default_registry()
    assert a is not b
    assert a.registry_digest == b.registry_digest


# --------------------------------------------------------------------------- #
# every public model: version / const / extra / frozen / key                   #
# --------------------------------------------------------------------------- #
def test_every_public_model_has_nonempty_schema_version():
    for model in PHASE1_PUBLIC_MODELS:
        field = model.model_fields.get("schema_version")
        assert field is not None, f"{model.__name__} must declare schema_version"
        version = field.default
        assert isinstance(version, str) and version, (
            f"{model.__name__}.schema_version must be a non-empty string default"
        )


def test_every_public_model_json_schema_closes_version_const():
    for model in PHASE1_PUBLIC_MODELS:
        version = model.model_fields["schema_version"].default
        schema = model.model_json_schema()
        assert schema["properties"]["schema_version"]["const"] == version, (
            f"{model.__name__} JSON schema must close schema_version to const={version!r}"
        )


def test_every_public_model_forbids_extra_fields():
    for model in PHASE1_PUBLIC_MODELS:
        assert model.model_config.get("extra") == "forbid", (
            f"{model.__name__} must forbid extra fields"
        )


def test_every_public_model_is_a_frozen_immutable_fact():
    for model in PHASE1_PUBLIC_MODELS:
        assert issubclass(model, DigestModel)
        assert model.model_config.get("frozen") is True, (
            f"{model.__name__} must be a frozen immutable fact"
        )


def test_registry_key_matches_model_name_and_version():
    reg = default_registry()
    for model in PHASE1_PUBLIC_MODELS:
        version = model.model_fields["schema_version"].default
        ref = SchemaRef(name=model.__name__, version=version)
        assert ref.key == f"{model.__name__}@{version}"
        assert reg.resolve(ref) is model


# --------------------------------------------------------------------------- #
# Task-8 memory / snapshot facts registered with projections intact            #
# --------------------------------------------------------------------------- #
def test_memory_and_snapshot_facts_are_registered():
    reg = default_registry()
    for model in (
        MemoryRecordRef,
        EmptyMemorySnapshot,
        EmptyMemorySelection,
        ContextSnapshot,
        InputSnapshot,
    ):
        assert model in PHASE1_PUBLIC_MODELS
        assert reg.resolve(SchemaRef(name=model.__name__, version="1")) is model


def test_snapshot_locator_and_hash_projections_intact():
    # ContextSnapshot: audit locators + freeze wall-clock excluded from semantic,
    # while the memory hashes / session id stay semantic.
    assert ContextSnapshot.SEMANTIC_EXCLUDE == frozenset(
        {"snapshot_id", "memory_snapshot_id", "built_at"}
    )
    assert ContextSnapshot.SELF_DIGEST_FIELDS == frozenset({"content_digest"})
    for semantic_field in ("memory_snapshot_hash", "past_context_hash", "memory_session_id"):
        assert semantic_field not in ContextSnapshot.SEMANTIC_EXCLUDE

    # InputSnapshot: storage/correlation locators + attempt + wall-clock are audit;
    # the frozen memory record refs (and plan_digest/node/layer/refs/readiness) stay
    # semantic.
    assert InputSnapshot.SEMANTIC_EXCLUDE == frozenset(
        {"snapshot_id", "run_id", "plan_id", "attempt", "built_at"}
    )
    assert InputSnapshot.SELF_DIGEST_FIELDS == frozenset({"content_digest"})
    assert "memory_record_refs" not in InputSnapshot.SEMANTIC_EXCLUDE
    for semantic_field in ("plan_digest", "node_id", "layer_index", "readiness"):
        assert semantic_field not in InputSnapshot.SEMANTIC_EXCLUDE

    # empty-memory facts self-seal their canonical identity.
    assert EmptyMemorySnapshot.SELF_DIGEST_FIELDS == frozenset({"content_digest"})
    assert EmptyMemorySelection.SELF_DIGEST_FIELDS == frozenset({"content_digest"})

    # a MemoryRecordRef is an all-semantic exact-revision fact (no exclusions).
    assert MemoryRecordRef.SEMANTIC_EXCLUDE == frozenset()
    assert MemoryRecordRef.SELF_DIGEST_FIELDS == frozenset()


# --------------------------------------------------------------------------- #
# representative payload round-trip + validation rejections                     #
# --------------------------------------------------------------------------- #
def _research_plan() -> ResearchPlan:
    from guanlan_v2.orchestration.enums import PortfolioRating

    return ResearchPlan(
        recommendation=PortfolioRating.BUY,
        rationale="Margins expanding; accumulate on strength.",
        strategic_actions=("Initiate starter", "Add on pullback"),
    )


def _portfolio_decision() -> PortfolioDecision:
    from guanlan_v2.orchestration.enums import PortfolioRating

    return PortfolioDecision(
        rating=PortfolioRating.OVERWEIGHT,
        executive_summary="Constructive into the print.",
        investment_thesis="Demand plus operating leverage.",
        price_target=182.5,
        time_horizon="6-12 months",
    )


def _sentiment_report() -> SentimentReport:
    from guanlan_v2.orchestration.enums import Confidence, SentimentBand

    return SentimentReport(
        overall_band=SentimentBand.MILDLY_BULLISH,
        overall_score=6.5,
        confidence=Confidence.HIGH,
        narrative="Breadth improving; flows positive.",
    )


def _memory_record_ref() -> MemoryRecordRef:
    return MemoryRecordRef(
        record_id="case.abc",
        revision_id="rev-1",
        available_at=datetime(2026, 7, 15, 8, 30, tzinfo=UTC),
        content_digest=_D64,
    )


@pytest.mark.parametrize(
    "obj",
    [
        _research_plan(),
        _portfolio_decision(),
        _sentiment_report(),
        _memory_record_ref(),
    ],
)
def test_representative_payloads_round_trip_through_registry(obj):
    reg = default_registry()
    ref = SchemaRef(name=type(obj).__name__, version="1")
    payload = obj.model_dump()
    restored = reg.validate_payload(ref, payload)
    assert isinstance(restored, type(obj))
    assert restored == obj
    # the content identity survives the round-trip.
    assert content_digest(restored) == content_digest(obj)


def test_validate_payload_rejects_self_declared_version_mismatch():
    reg = default_registry()
    ref = SchemaRef(name="PortfolioDecision", version="1")
    payload = _portfolio_decision().model_dump()
    payload["schema_version"] = "2"  # disagrees with the resolved ref
    with pytest.raises(SchemaVersionMismatchError):
        reg.validate_payload(ref, payload)


def test_validate_payload_rejects_extra_field():
    reg = default_registry()
    ref = SchemaRef(name="PortfolioDecision", version="1")
    payload = _portfolio_decision().model_dump()
    payload["bogus"] = 1
    with pytest.raises(ValidationError):
        reg.validate_payload(ref, payload)


def test_validate_payload_rejects_unknown_ref():
    reg = default_registry()
    with pytest.raises(UnknownSchemaError):
        reg.validate_payload(SchemaRef(name="NotARegisteredThing", version="1"), {})


# --------------------------------------------------------------------------- #
# manifest / registry digest are registration-order independent                #
# --------------------------------------------------------------------------- #
def test_reverse_registration_order_same_registry_digest():
    reg = default_registry()
    reversed_reg = SchemaRegistry()
    for model in reversed(PHASE1_PUBLIC_MODELS):
        reversed_reg.register(model)
    reversed_reg.seal()
    assert reversed_reg.manifest() == reg.manifest()
    assert reversed_reg.registry_digest == reg.registry_digest


def test_manifest_is_sorted_by_key():
    reg = default_registry()
    keys = [e.key for e in reg.manifest()]
    assert keys == sorted(keys)


# --------------------------------------------------------------------------- #
# frozen golden manifest — NEVER auto-regenerated                              #
# --------------------------------------------------------------------------- #
def test_golden_manifest_exists_and_is_frozen_format():
    assert GOLDEN_PATH.exists(), f"missing golden manifest: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    assert isinstance(doc["entries"], list) and doc["entries"]


def test_registry_matches_golden_manifest():
    """Per-model JSON-schema digests + registry digest must match the frozen
    golden. This test NEVER writes the golden: a changed manifest is a reviewed
    change that must be regenerated by hand and re-reviewed."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}

    reg = default_registry()
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}

    assert set(manifest) == set(golden), (
        "registered schema key set drifted from the golden manifest; regenerate "
        "and re-review golden/schema_manifest_v1.json if this change is intended"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], (
            f"JSON-schema digest drift for {key}: registry {manifest[key]!r} != "
            f"golden {golden[key]!r}"
        )
    assert reg.registry_digest == doc["registry_digest"], (
        "registry digest drifted from the golden manifest"
    )


def test_golden_manifest_keys_are_exactly_the_public_models():
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden_keys = {e["key"] for e in doc["entries"]}
    expected = {
        f"{m.__name__}@{m.model_fields['schema_version'].default}"
        for m in PHASE1_PUBLIC_MODELS
    }
    assert golden_keys == expected


def test_golden_digests_are_recomputable_from_the_models():
    """Each golden digest equals the canonical content digest of the model's
    JSON schema — the same rule the registry manifest uses."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    for model in PHASE1_PUBLIC_MODELS:
        key = f"{model.__name__}@{model.model_fields['schema_version'].default}"
        assert golden[key] == content_digest(model.model_json_schema())


def test_public_models_are_contract_models():
    for model in PHASE1_PUBLIC_MODELS:
        assert issubclass(model, ContractModel)
