# -*- coding: utf-8 -*-
"""Task 1 — strict contract base + canonical ``sha256+cjson-v1`` digests.

Written test-first: with ``guanlan_v2/orchestration/digest.py`` absent the whole
module fails to import (RED). It then locks, GREEN, every canonicalization rule
in the brief plus a cross-process hash-seed proof and a frozen golden-vector set.

Run from repo root: ``pytest tests/orchestration/test_digest.py -v``
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import ClassVar, Literal

import pytest
from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration.digest import (
    CJSON_VERSION,
    ContractModel,  # noqa: F401  (imported to assert the strict base exists)
    DigestHex,  # noqa: F401
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    UtcDateTime,
    audit_digest,
    canonical_json,
    content_digest,
    verify_digest,
)

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]
GOLDEN_PATH = THIS_FILE.parent / "golden" / "digest_vectors_v1.json"

UTC = timezone.utc
CST8 = timezone(timedelta(hours=8))  # Asia/Shanghai fixed offset


# --------------------------------------------------------------------------- #
# Sample models (also the single source of truth for golden vectors)          #
# --------------------------------------------------------------------------- #
class Color(str, Enum):
    RED = "red"
    GREEN = "green"


class Leaf(DigestModel):
    """A child with explicit audit fields excluded from its semantic digest."""

    schema_version: Literal["1"] = "1"
    business_value: NonNegativeInt
    weight: FiniteFloat
    created_at: UtcDateTime  # wall-clock — audit only
    record_id: NonEmptyStr  # random id — audit only

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"created_at", "record_id"})


class Parent(DigestModel):
    """A parent that nests :class:`Leaf` and relies on recursive projection."""

    schema_version: Literal["1"] = "1"
    label: NonEmptyStr
    leaf: Leaf


class SealedRecord(DigestModel):
    """Persisted record carrying its own content digest (self-reference guard)."""

    schema_version: Literal["1"] = "1"
    payload: NonEmptyStr
    tags: frozenset[str]
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify_declared_digest(self) -> "SealedRecord":
        expected = self.semantic_digest()  # excludes the content_digest field
        if self.content_digest != expected:
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, *, payload: str, tags: frozenset[str]) -> "SealedRecord":
        digest = cls.digest_of_fields(projection="semantic", payload=payload, tags=tags)
        return cls(payload=payload, tags=tags, content_digest=digest)


class SetFragment(DigestModel):
    """A Plan-like fragment containing sets — used by the hash-seed proof."""

    schema_version: Literal["1"] = "1"
    plan_id: NonEmptyStr
    worker_tags: frozenset[str]
    layers: tuple[str, ...]  # ordered, preserved


# 12 short strings whose frozenset iteration order varies with PYTHONHASHSEED.
_SET_ELEMENTS = (
    "gamma", "alpha", "delta", "beta", "epsilon", "zeta",
    "eta", "theta", "iota", "kappa", "lambda", "mu",
)


def build_set_fragment() -> SetFragment:
    """Single source of truth for the in-process and subprocess digests."""
    return SetFragment(
        plan_id="plan-2026-07-15",
        worker_tags=frozenset(_SET_ELEMENTS),
        layers=("L0", "L1", "L2"),
    )


def _leaf(business_value: int, created_at: datetime, record_id: str, weight: float) -> Leaf:
    return Leaf(
        business_value=business_value,
        weight=weight,
        created_at=created_at,
        record_id=record_id,
    )


# --------------------------------------------------------------------------- #
# Rule 3 — nested dict key-order independence                                 #
# --------------------------------------------------------------------------- #
def test_nested_key_order_independence():
    a = {"z": 1, "a": {"n": 2, "m": [3, 2, 1]}, "k": [{"y": 1, "x": 0}]}
    b = {"k": [{"x": 0, "y": 1}], "a": {"m": [3, 2, 1], "n": 2}, "z": 1}
    assert canonical_json(a) == canonical_json(b)
    assert content_digest(a) == content_digest(b)


def test_list_order_is_significant():
    assert content_digest([1, 2, 3]) != content_digest([3, 2, 1])


# --------------------------------------------------------------------------- #
# Rule 4 — set element ordering by canonical JSON                             #
# --------------------------------------------------------------------------- #
def test_set_order_independent_but_content_sensitive():
    assert content_digest({"s": {3, 1, 2}}) == content_digest({"s": {2, 3, 1}})
    assert content_digest({"s": {1, 2, 3}}) != content_digest({"s": {1, 2, 4}})


# --------------------------------------------------------------------------- #
# Rule 5/6 — datetime UTC normalization + naive rejection                     #
# --------------------------------------------------------------------------- #
def test_equal_instant_utc_and_offset_hash_equally():
    utc = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
    offset = datetime(2026, 7, 15, 16, 30, tzinfo=CST8)  # same instant
    assert canonical_json({"t": utc}) == canonical_json({"t": offset})
    assert content_digest({"t": utc}) == content_digest({"t": offset})


def test_naive_datetime_rejected_by_canonical_json():
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2026, 7, 15, 8, 30)})


def test_naive_datetime_rejected_by_field():
    with pytest.raises(ValidationError):
        _leaf(1, datetime(2026, 7, 15, 8, 30), "r1", 0.5)


# --------------------------------------------------------------------------- #
# Rule 6/7 — non-finite rejection + reviewed -0.0 behavior                    #
# --------------------------------------------------------------------------- #
def test_nan_and_infinity_rejected_by_canonical_json():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_json({"x": bad})


def test_nan_and_infinity_rejected_by_field():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            _leaf(1, datetime(2026, 7, 15, tzinfo=UTC), "r1", bad)


def test_negative_zero_normalized_to_zero():
    assert canonical_json({"x": -0.0}) == canonical_json({"x": 0.0})
    assert content_digest({"x": -0.0}) == content_digest({"x": 0.0})
    # nested inside lists/sets too
    assert content_digest([-0.0, 1.0]) == content_digest([0.0, 1.0])


# --------------------------------------------------------------------------- #
# Rule 8 — enum by value                                                      #
# --------------------------------------------------------------------------- #
def test_enum_serializes_by_value():
    assert canonical_json({"c": Color.RED}) == canonical_json({"c": "red"})
    assert content_digest({"c": Color.GREEN}) == content_digest({"c": "green"})


# --------------------------------------------------------------------------- #
# Rule 9 — unsupported types rejected                                         #
# --------------------------------------------------------------------------- #
def test_unsupported_type_rejected():
    with pytest.raises(TypeError):
        canonical_json({"bad": object()})


def test_non_string_dict_key_rejected():
    with pytest.raises(TypeError):
        canonical_json({1: "a"})


# --------------------------------------------------------------------------- #
# Strict base — extra field / schema_version / immutability                   #
# --------------------------------------------------------------------------- #
def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        Parent(
            label="p",
            leaf=_leaf(1, datetime(2026, 7, 15, tzinfo=UTC), "r1", 0.5),
            bogus=1,
        )


def test_wrong_and_arbitrary_schema_version_rejected():
    good = _leaf(1, datetime(2026, 7, 15, tzinfo=UTC), "r1", 0.5)
    for bad in ("2", "abc", "1.0", ""):
        with pytest.raises(ValidationError):
            Parent(schema_version=bad, label="p", leaf=good)


def test_digest_model_is_immutable():
    p = Parent(label="p", leaf=_leaf(1, datetime(2026, 7, 15, tzinfo=UTC), "r1", 0.5))
    with pytest.raises(ValidationError):
        p.label = "q"


def test_schema_version_participates_in_semantic_digest():
    class V1(DigestModel):
        schema_version: Literal["1"] = "1"
        x: NonNegativeInt

    class V2(DigestModel):
        schema_version: Literal["2"] = "2"
        x: NonNegativeInt

    assert V1(x=5).semantic_digest() != V2(x=5).semantic_digest()


# --------------------------------------------------------------------------- #
# Rule 1/2 — nested projection stays model-aware                              #
# --------------------------------------------------------------------------- #
def test_nested_audit_timestamp_changes_audit_not_semantic():
    t1 = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
    t2 = datetime(2026, 7, 15, 9, 45, tzinfo=UTC)
    p1 = Parent(label="p", leaf=_leaf(10, t1, "rid-1", 0.5))
    p2 = Parent(label="p", leaf=_leaf(10, t2, "rid-2", 0.5))
    # nested wall-clock + random id are audit-only in Leaf -> semantic unchanged
    assert p1.semantic_digest() == p2.semantic_digest()
    # but the audit digest recurses into Leaf's audit projection -> changes
    assert p1.audit_digest_value() != p2.audit_digest_value()


def test_nested_business_field_changes_semantic_digest():
    t = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
    p1 = Parent(label="p", leaf=_leaf(10, t, "rid", 0.5))
    p2 = Parent(label="p", leaf=_leaf(11, t, "rid", 0.5))
    assert p1.semantic_digest() != p2.semantic_digest()


def test_parent_label_change_changes_both_digests():
    t = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
    p1 = Parent(label="p", leaf=_leaf(10, t, "rid", 0.5))
    p2 = Parent(label="q", leaf=_leaf(10, t, "rid", 0.5))
    assert p1.semantic_digest() != p2.semantic_digest()
    assert p1.audit_digest_value() != p2.audit_digest_value()


# --------------------------------------------------------------------------- #
# Rule 10 — self-declared digest field + load-time verification               #
# --------------------------------------------------------------------------- #
def test_sealed_record_build_roundtrips_and_excludes_self():
    r = SealedRecord.build(payload="hello", tags=frozenset({"a", "b"}))
    # rebuilding from persisted fields must pass verification
    reloaded = SealedRecord(
        payload=r.payload, tags=r.tags, content_digest=r.content_digest
    )
    assert reloaded.content_digest == r.content_digest
    # the stored digest is excluded from its own computation
    assert r.content_digest == r.semantic_digest()


def test_declared_digest_mismatch_rejected():
    with pytest.raises(ValidationError):
        SealedRecord(
            payload="hello",
            tags=frozenset({"a", "b"}),
            content_digest="0" * 64,  # valid hex shape, wrong value
        )


def test_bad_digest_hex_shape_rejected():
    with pytest.raises(ValidationError):
        SealedRecord(payload="x", tags=frozenset(), content_digest="not-a-hash")


def test_verify_digest_helper():
    r = SealedRecord.build(payload="p", tags=frozenset({"x"}))
    verify_digest(r, r.content_digest, projection="semantic")  # no raise
    with pytest.raises(ValueError):
        verify_digest(r, "1" * 64, projection="semantic")


# --------------------------------------------------------------------------- #
# Cross-process PYTHONHASHSEED proof                                          #
# --------------------------------------------------------------------------- #
_CHILD = """
import sys, json, importlib.util
spec = importlib.util.spec_from_file_location("dtmod_child", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
# register before exec so Pydantic can resolve `from __future__ import annotations`
# forward refs against the module globals during class definition.
sys.modules["dtmod_child"] = mod
spec.loader.exec_module(mod)
frag = mod.build_set_fragment()
print(json.dumps({"order": list(frag.worker_tags), "digest": mod.content_digest(frag)}))
"""


def _run_child(seed: str) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(THIS_FILE)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"child failed (seed={seed}): {proc.stderr}"
    return json.loads(proc.stdout.strip())


def test_set_containing_fragment_hashes_identically_across_hash_seeds():
    seeds = ["0", "1", "2", "7", "13", "99"]
    results = [_run_child(s) for s in seeds]
    digests = {r["digest"] for r in results}
    orders = {tuple(r["order"]) for r in results}
    # the whole point: set iteration order actually varies across seeds ...
    assert len(orders) >= 2, f"set order did not vary across seeds: {orders}"
    # ... yet the canonical digest is identical for all of them.
    assert len(digests) == 1, f"digest not stable across hash seeds: {digests}"
    # and it matches this in-process computation.
    assert digests == {content_digest(build_set_fragment())}


# --------------------------------------------------------------------------- #
# Golden vectors — frozen regression + representation lock                    #
# --------------------------------------------------------------------------- #
def golden_cases():
    """(name, data, projection) — single source of truth for golden_vectors_v1."""
    t_utc = datetime(2026, 7, 15, 8, 30, 15, 123456, tzinfo=UTC)
    t_offset = datetime(2026, 7, 15, 16, 30, 15, 123456, tzinfo=CST8)  # same instant
    leaf = _leaf(10, t_utc, "rid-fixed", 0.5)
    parent = Parent(label="anchor", leaf=leaf)
    sealed = SealedRecord.build(payload="frozen", tags=frozenset({"m", "a", "z"}))
    return [
        ("primitives_neg_zero", {"a": -0.0, "b": [0.0, 1.5, -2.5], "n": 7}, "semantic"),
        ("primitives_pos_zero", {"a": 0.0, "b": [0.0, 1.5, -2.5], "n": 7}, "semantic"),
        ("nested_dict", {"z": 1, "a": {"n": 2, "m": [3, 2, 1]}}, "semantic"),
        ("set_of_ints", {"s": {5, 3, 1, 4, 2}}, "semantic"),
        ("enum_by_value", {"c": Color.RED, "d": Color.GREEN}, "semantic"),
        ("datetime_utc", {"t": t_utc}, "semantic"),
        ("datetime_offset_same_instant", {"t": t_offset}, "semantic"),
        ("leaf_semantic", leaf, "semantic"),
        ("leaf_audit", leaf, "audit"),
        ("parent_semantic", parent, "semantic"),
        ("parent_audit", parent, "audit"),
        ("sealed_semantic", sealed, "semantic"),
        ("set_fragment_semantic", build_set_fragment(), "semantic"),
    ]


def test_golden_vectors_match():
    assert GOLDEN_PATH.exists(), f"missing golden vectors: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    vectors = {v["name"]: v for v in doc["vectors"]}
    cases = golden_cases()
    assert set(vectors) == {name for name, _, _ in cases}, "golden vector name drift"
    for name, data, projection in cases:
        cj = canonical_json(data, projection=projection)
        dg = content_digest(data) if projection == "semantic" else audit_digest(data)
        assert cj == vectors[name]["canonical_json"], f"canonical drift: {name}"
        assert dg == vectors[name]["digest"], f"digest drift: {name}"


def test_golden_datetime_vectors_are_instant_equal():
    """The two same-instant datetime vectors must be byte-identical."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    vectors = {v["name"]: v for v in doc["vectors"]}
    assert (
        vectors["datetime_utc"]["digest"]
        == vectors["datetime_offset_same_instant"]["digest"]
    )
