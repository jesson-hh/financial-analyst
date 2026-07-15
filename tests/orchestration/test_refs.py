# -*- coding: utf-8 -*-
"""Task 3 — versioned logical refs (``refs.py``).

Written test-first: with ``guanlan_v2/orchestration/refs.py`` absent the module
fails to import (RED). It then locks, GREEN, the reviewed ID grammar, the strict
digest shape, the closed payload namespace matrix, and — critically — that each
ref is a :class:`DigestModel` that participates in a parent's semantic digest,
while ``PayloadRef.object_id`` is audit-only (storage locator excluded from the
parent's semantic projection).

Run from repo root: ``pytest tests/orchestration/test_refs.py -v``
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel, NonEmptyStr
from guanlan_v2.orchestration.refs import (
    NON_PUBLIC_PAYLOAD_NAMESPACES,
    PAYLOAD_NAMESPACES,
    PUBLIC_PAYLOAD_NAMESPACES,
    CapabilityRef,
    ContentRef,
    LogicalId,  # noqa: F401 — imported to assert the type alias exists
    PayloadNamespace,  # noqa: F401
    PayloadRef,
    SchemaManifestEntry,
    SchemaName,  # noqa: F401
    SchemaRef,
    SchemaVersion,  # noqa: F401
)

# Three distinct, well-formed sha256 hex digests.
D1 = "1" * 64
D2 = "2" * 64
D3 = "3" * 64


# --------------------------------------------------------------------------- #
# LogicalId grammar (via ContentRef.id)                                       #
# --------------------------------------------------------------------------- #
VALID_LOGICAL_IDS = [
    "reader",
    "news_sentiment",
    "reader.v1",
    "a-b-c1",
    "worker_1",
    "gat.model.head",
]

# Physical paths and other junk the reviewed grammar must reject.
INVALID_LOGICAL_IDS = [
    "prompts/reader",   # forward-slash path
    "prompts/reader.md",
    "/etc/passwd",      # absolute posix path
    "..\\win",          # backslash path / traversal
    "C:/models/x",      # windows drive path (uppercase + colon + slash)
    "Reader",           # uppercase start
    "1reader",          # leading digit
    "a b",              # space
    "",                 # empty
    "a__b",             # double separator
    "reader-",          # trailing separator
    ".hidden",          # leading dot
    "a@b",              # at-sign
]


@pytest.mark.parametrize("good", VALID_LOGICAL_IDS)
def test_logical_id_grammar_accepts_valid(good):
    ref = ContentRef(id=good, version="1", content_digest=D1)
    assert ref.id == good


@pytest.mark.parametrize("bad", INVALID_LOGICAL_IDS)
def test_logical_id_rejects_path_like_and_junk(bad):
    with pytest.raises(ValidationError):
        ContentRef(id=bad, version="1", content_digest=D1)


def test_content_ref_path_like_id_specifically_rejected():
    # The headline invariant: a physical file path can never be a logical id.
    with pytest.raises(ValidationError):
        ContentRef(id="prompts/reader_v1.md", version="1", content_digest=D1)


# --------------------------------------------------------------------------- #
# Digest-format rejection                                                     #
# --------------------------------------------------------------------------- #
BAD_DIGESTS = [
    "xyz",              # too short / non-hex
    "g" * 64,           # 64 chars but non-hex
    "a" * 63,           # 63 hex chars
    "a" * 65,           # 65 hex chars
    "A" * 64,           # uppercase hex
    "",                 # empty
]


@pytest.mark.parametrize("bad", BAD_DIGESTS)
def test_content_ref_digest_format_rejected(bad):
    with pytest.raises(ValidationError):
        ContentRef(id="reader", version="1", content_digest=bad)


@pytest.mark.parametrize("bad", BAD_DIGESTS)
def test_payload_ref_digest_format_rejected(bad):
    with pytest.raises(ValidationError):
        PayloadRef(namespace="main", object_id="obj-1", content_digest=bad)


# --------------------------------------------------------------------------- #
# SchemaRef — canonical key + name/version grammar                            #
# --------------------------------------------------------------------------- #
def test_schema_ref_canonical_key():
    ref = SchemaRef(name="ReportOutput", version="1")
    assert ref.key == "ReportOutput@1"


@pytest.mark.parametrize("bad_name", ["Foo@Bar", "foo bar", "prompts/Foo", "1Foo", ""])
def test_schema_ref_rejects_bad_name(bad_name):
    with pytest.raises(ValidationError):
        SchemaRef(name=bad_name, version="1")


@pytest.mark.parametrize("bad_version", ["1.0", "v1", "1@2", "", "one"])
def test_schema_ref_rejects_bad_version(bad_version):
    with pytest.raises(ValidationError):
        SchemaRef(name="Foo", version=bad_version)


# --------------------------------------------------------------------------- #
# CapabilityRef — no transport double-write surface                           #
# --------------------------------------------------------------------------- #
def test_capability_ref_has_no_transport_field():
    # transport lives only on the (Task 9A) manifest entry, never on the ref.
    assert "transport" not in CapabilityRef.model_fields
    cap = CapabilityRef(id="fetch_news", version="1", content_digest=D1)
    assert cap.id == "fetch_news"


# --------------------------------------------------------------------------- #
# PayloadRef — closed namespace matrix + publicness classification            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ns", ["main", "sealed", "review", "audit"])
def test_payload_namespace_accepts_closed_values(ns):
    ref = PayloadRef(namespace=ns, object_id="obj-1", content_digest=D1)
    assert ref.namespace == ns


@pytest.mark.parametrize("ns", ["public", "private", "Main", "", "draft"])
def test_payload_namespace_rejects_unknown_values(ns):
    with pytest.raises(ValidationError):
        PayloadRef(namespace=ns, object_id="obj-1", content_digest=D1)


def test_payload_namespace_publicness_classification():
    assert PAYLOAD_NAMESPACES == {"main", "sealed", "review", "audit"}
    assert PUBLIC_PAYLOAD_NAMESPACES == {"main"}
    # audit is excluded from public visibility, just like sealed/review.
    assert NON_PUBLIC_PAYLOAD_NAMESPACES == {"sealed", "review", "audit"}
    assert PUBLIC_PAYLOAD_NAMESPACES.isdisjoint(NON_PUBLIC_PAYLOAD_NAMESPACES)
    assert PUBLIC_PAYLOAD_NAMESPACES | NON_PUBLIC_PAYLOAD_NAMESPACES == PAYLOAD_NAMESPACES
    assert PayloadRef(namespace="main", object_id="o", content_digest=D1).is_public
    for ns in ("sealed", "review", "audit"):
        assert not PayloadRef(namespace=ns, object_id="o", content_digest=D1).is_public


def test_payload_ref_object_id_must_be_non_blank():
    with pytest.raises(ValidationError):
        PayloadRef(namespace="main", object_id="   ", content_digest=D1)


# --------------------------------------------------------------------------- #
# Refs are immutable DigestModels                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cls", [SchemaRef, ContentRef, CapabilityRef, PayloadRef, SchemaManifestEntry]
)
def test_refs_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


def test_content_ref_is_frozen():
    ref = ContentRef(id="reader", version="1", content_digest=D1)
    with pytest.raises(ValidationError):
        ref.id = "writer"


# --------------------------------------------------------------------------- #
# Semantic participation — refs embedded in a parent DigestModel              #
# --------------------------------------------------------------------------- #
class _PayloadHolder(DigestModel):
    schema_version: Literal["1"] = "1"
    label: NonEmptyStr
    payload: PayloadRef


class _ContentHolder(DigestModel):
    schema_version: Literal["1"] = "1"
    ref: ContentRef


def _payload_holder(namespace: str, object_id: str, content_digest: str) -> _PayloadHolder:
    return _PayloadHolder(
        label="anchor",
        payload=PayloadRef(
            namespace=namespace, object_id=object_id, content_digest=content_digest
        ),
    )


def test_payload_ref_object_id_is_audit_only_in_parent_semantic():
    base = _payload_holder("main", "obj-A", D1)
    other_object_id = _payload_holder("main", "obj-B", D1)
    # object_id is a storage locator: it must NOT change the parent semantic digest,
    # but it MUST change the parent audit digest.
    assert base.semantic_digest() == other_object_id.semantic_digest()
    assert base.audit_digest_value() != other_object_id.audit_digest_value()


def test_payload_ref_content_digest_and_namespace_are_semantic():
    base = _payload_holder("main", "obj-A", D1)
    changed_content = _payload_holder("main", "obj-A", D2)
    changed_namespace = _payload_holder("sealed", "obj-A", D1)
    # referenced content digest and namespace ARE authorization identity.
    assert base.semantic_digest() != changed_content.semantic_digest()
    assert base.semantic_digest() != changed_namespace.semantic_digest()


def test_content_ref_all_fields_participate_in_parent_semantic():
    base = _ContentHolder(ref=ContentRef(id="reader", version="1", content_digest=D1))
    diff_id = _ContentHolder(ref=ContentRef(id="writer", version="1", content_digest=D1))
    diff_ver = _ContentHolder(ref=ContentRef(id="reader", version="2", content_digest=D1))
    diff_dig = _ContentHolder(ref=ContentRef(id="reader", version="1", content_digest=D2))
    base_d = base.semantic_digest()
    assert base_d != diff_id.semantic_digest()
    assert base_d != diff_ver.semantic_digest()
    assert base_d != diff_dig.semantic_digest()


# --------------------------------------------------------------------------- #
# SchemaManifestEntry                                                         #
# --------------------------------------------------------------------------- #
def test_schema_manifest_entry_key_and_digest_sensitivity():
    e1 = SchemaManifestEntry(
        schema_ref=SchemaRef(name="Foo", version="1"), json_schema_digest=D1
    )
    assert e1.key == "Foo@1"
    e_same = SchemaManifestEntry(
        schema_ref=SchemaRef(name="Foo", version="1"), json_schema_digest=D1
    )
    e_diff = SchemaManifestEntry(
        schema_ref=SchemaRef(name="Foo", version="1"), json_schema_digest=D2
    )
    assert e1.semantic_digest() == e_same.semantic_digest()
    assert e1.semantic_digest() != e_diff.semantic_digest()
