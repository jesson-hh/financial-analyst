# -*- coding: utf-8 -*-
"""Task 3 — sealable schema registry (``schema_registry.py``).

Written test-first (RED before ``schema_registry.py`` exists). Locks the
registry invariants from the brief: register/resolve/validate, the closed
``schema_version`` const, payload/schema version-mismatch rejection, extra-field
rejection, idempotent vs conflicting registration, registration-order-independent
manifest/digest, JSON-schema sensitivity of the registry digest, and a fully
sealed registry that refuses further registration while still serving reads.

Run from repo root: ``pytest tests/orchestration/test_schema_registry.py -v``
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError, create_model

from guanlan_v2.orchestration.digest import DigestModel, NonEmptyStr, NonNegativeInt
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaConflictError,
    SchemaRegistry,
    SchemaRegistryError,
    SchemaVersionMismatchError,
    UnknownSchemaError,
)


# --------------------------------------------------------------------------- #
# Sample payload schemas                                                      #
# --------------------------------------------------------------------------- #
class SampleA(DigestModel):
    schema_version: Literal["1"] = "1"
    a: NonNegativeInt


class SampleB(DigestModel):
    schema_version: Literal["1"] = "1"
    b: NonEmptyStr


class NoVersion(DigestModel):
    # deliberately has no schema_version field
    a: NonNegativeInt


def _widget(*fields: str):
    """Build a distinct model class all named 'Widget'@1 with the given int fields."""
    return create_model(
        "Widget",
        __base__=DigestModel,
        schema_version=(Literal["1"], "1"),
        **{f: (int, ...) for f in fields},
    )


# --------------------------------------------------------------------------- #
# register / resolve                                                          #
# --------------------------------------------------------------------------- #
def test_register_returns_schema_ref_and_resolve_roundtrips():
    reg = SchemaRegistry()
    ref = reg.register(SampleA)
    assert isinstance(ref, SchemaRef)
    assert ref.key == "SampleA@1"
    assert reg.resolve(SchemaRef(name="SampleA", version="1")) is SampleA


def test_unknown_schema_rejected():
    reg = SchemaRegistry()
    reg.register(SampleA)
    with pytest.raises(UnknownSchemaError):
        reg.resolve(SchemaRef(name="Nope", version="1"))


def test_registering_under_different_version_is_impossible():
    # register reads the version FROM the model; SampleA is only ever SampleA@1,
    # so a SchemaRef at a different version resolves to nothing.
    reg = SchemaRegistry()
    reg.register(SampleA)
    with pytest.raises(UnknownSchemaError):
        reg.resolve(SchemaRef(name="SampleA", version="2"))


def test_model_without_schema_version_is_rejected():
    reg = SchemaRegistry()
    with pytest.raises(SchemaRegistryError):
        reg.register(NoVersion)


def test_non_contract_model_rejected():
    class Plain(BaseModel):
        schema_version: Literal["1"] = "1"
        a: int

    reg = SchemaRegistry()
    with pytest.raises(SchemaRegistryError):
        reg.register(Plain)


# --------------------------------------------------------------------------- #
# validate_payload                                                            #
# --------------------------------------------------------------------------- #
def test_validate_payload_ok_returns_instance():
    reg = SchemaRegistry()
    reg.register(SampleA)
    inst = reg.validate_payload(SchemaRef(name="SampleA", version="1"), {"a": 5})
    assert isinstance(inst, SampleA)
    assert inst.a == 5
    # explicit version is accepted when it matches.
    inst2 = reg.validate_payload(
        SchemaRef(name="SampleA", version="1"), {"schema_version": "1", "a": 6}
    )
    assert inst2.a == 6


def test_validate_payload_extra_field_rejected():
    reg = SchemaRegistry()
    reg.register(SampleA)
    with pytest.raises(ValidationError):
        reg.validate_payload(SchemaRef(name="SampleA", version="1"), {"a": 5, "bogus": 1})


def test_validate_payload_version_mismatch_rejected():
    reg = SchemaRegistry()
    reg.register(SampleA)
    with pytest.raises(SchemaVersionMismatchError):
        reg.validate_payload(
            SchemaRef(name="SampleA", version="1"), {"schema_version": "2", "a": 5}
        )


def test_validate_payload_unknown_schema_rejected():
    reg = SchemaRegistry()
    with pytest.raises(UnknownSchemaError):
        reg.validate_payload(SchemaRef(name="SampleA", version="1"), {"a": 5})


def test_json_schema_makes_schema_version_a_const():
    schema = SampleA.model_json_schema()
    assert schema["properties"]["schema_version"]["const"] == "1"


# --------------------------------------------------------------------------- #
# idempotency / conflict                                                      #
# --------------------------------------------------------------------------- #
def test_same_model_registration_is_idempotent():
    reg = SchemaRegistry()
    reg.register(SampleA)
    reg.register(SampleA)  # no raise
    assert len(reg.manifest()) == 1


def test_conflicting_registration_rejected():
    reg = SchemaRegistry()
    reg.register(_widget("x"))
    with pytest.raises(SchemaConflictError):
        reg.register(_widget("y"))  # different class, same 'Widget@1' key


# --------------------------------------------------------------------------- #
# manifest / registry digest                                                  #
# --------------------------------------------------------------------------- #
def test_manifest_sorted_by_schema_key():
    reg = SchemaRegistry()
    reg.register(SampleB)
    reg.register(SampleA)
    keys = [e.key for e in reg.manifest()]
    assert keys == ["SampleA@1", "SampleB@1"] == sorted(keys)


def test_reverse_registration_order_same_manifest_and_digest():
    r1 = SchemaRegistry()
    r1.register(SampleA)
    r1.register(SampleB)
    r2 = SchemaRegistry()
    r2.register(SampleB)
    r2.register(SampleA)
    assert r1.manifest() == r2.manifest()
    assert r1.registry_digest == r2.registry_digest


def test_changed_model_json_schema_changes_registry_digest():
    thin = SchemaRegistry()
    thin.register(_widget("x"))
    fat = SchemaRegistry()
    fat.register(_widget("x", "y"))  # same 'Widget@1' key, different schema shape
    assert thin.registry_digest != fat.registry_digest


def test_registry_digest_is_deterministic():
    reg = SchemaRegistry()
    reg.register(SampleA)
    reg.register(SampleB)
    assert reg.registry_digest == reg.registry_digest


# --------------------------------------------------------------------------- #
# seal                                                                        #
# --------------------------------------------------------------------------- #
def test_seal_blocks_further_registration_but_serves_reads():
    reg = SchemaRegistry()
    reg.register(SampleA)
    digest_before = reg.registry_digest
    reg.seal()
    assert reg.sealed is True
    with pytest.raises(RegistrySealedError):
        reg.register(SampleB)
    # reads still work after sealing.
    assert reg.resolve(SchemaRef(name="SampleA", version="1")) is SampleA
    assert reg.validate_payload(SchemaRef(name="SampleA", version="1"), {"a": 1}).a == 1
    assert reg.registry_digest == digest_before
    assert len(reg.manifest()) == 1
