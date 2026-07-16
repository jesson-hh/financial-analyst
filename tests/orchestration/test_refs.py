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
    TypedPayloadRef,
    typed_ref_sort_key,
    validate_typed_ref_tuple,
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
    "cls",
    [SchemaRef, ContentRef, CapabilityRef, PayloadRef, TypedPayloadRef, SchemaManifestEntry],
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
# TypedPayloadRef — composite typed evidence reference (Amendment 1, Task A)  #
# --------------------------------------------------------------------------- #
def _typed_ref(
    name: str = "ReportOutput",
    version: str = "1",
    namespace: str = "main",
    object_id: str = "obj-1",
    digest: str = D1,
) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version=version),
        payload_ref=PayloadRef(
            namespace=namespace, object_id=object_id, content_digest=digest
        ),
    )


class _TypedHolder(DigestModel):
    """Embedding parent used to prove the composite's nested projection."""

    schema_version: Literal["1"] = "1"
    evidence: TypedPayloadRef


def test_typed_payload_ref_construction():
    ref = _typed_ref()
    assert ref.schema_version == "1"
    assert ref.schema_ref.key == "ReportOutput@1"
    assert ref.payload_ref.namespace == "main"
    assert ref.payload_ref.content_digest == D1


def test_typed_payload_ref_is_frozen():
    ref = _typed_ref()
    with pytest.raises(ValidationError):
        ref.schema_ref = SchemaRef(name="Other", version="1")


def test_typed_payload_ref_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TypedPayloadRef(
            schema_ref=SchemaRef(name="Foo", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=D1),
            transport="http",
        )


@pytest.mark.parametrize("bad", BAD_DIGESTS)
def test_typed_payload_ref_rejects_garbage_digest_through_nested_payload_ref(bad):
    # a garbage content_digest cannot sneak in through the composite: the nested
    # PayloadRef enforces the strict digest shape (constructed via dict form so
    # validation demonstrably runs through the nested model, not a pre-built ref).
    with pytest.raises(ValidationError):
        TypedPayloadRef(
            schema_ref=SchemaRef(name="Foo", version="1"),
            payload_ref={"namespace": "main", "object_id": "o", "content_digest": bad},
        )


def test_typed_payload_ref_has_no_namespace_constraint_on_the_type_itself():
    # owners enforce main-ness (see validate_typed_ref_tuple); the type accepts
    # every closed namespace so sealed/review/audit evidence can be referenced.
    for ns in ("main", "sealed", "review", "audit"):
        assert _typed_ref(namespace=ns).payload_ref.namespace == ns


def test_typed_payload_ref_declares_no_new_semantic_excludes():
    # object_id stays audit-only purely via the nested PayloadRef projection —
    # the composite itself must not need any exclusion of its own.
    assert TypedPayloadRef.SEMANTIC_EXCLUDE == frozenset()
    assert TypedPayloadRef.SELF_DIGEST_FIELDS == frozenset()


def test_typed_ref_object_id_relocation_is_audit_only_in_parent_semantic():
    base = _TypedHolder(evidence=_typed_ref(object_id="obj-A"))
    relocated = _TypedHolder(evidence=_typed_ref(object_id="obj-B"))
    # re-storing identical content under a new object_id must not move the
    # parent's semantic digest, but must move its audit digest.
    assert base.semantic_digest() == relocated.semantic_digest()
    assert base.audit_digest_value() != relocated.audit_digest_value()


def test_typed_ref_semantic_identity_moves_parent_semantic_digest():
    base_d = _TypedHolder(evidence=_typed_ref()).semantic_digest()
    diff_name = _TypedHolder(evidence=_typed_ref(name="Other"))
    diff_ver = _TypedHolder(evidence=_typed_ref(version="2"))
    diff_ns = _TypedHolder(evidence=_typed_ref(namespace="sealed"))
    diff_dig = _TypedHolder(evidence=_typed_ref(digest=D2))
    assert base_d != diff_name.semantic_digest()
    assert base_d != diff_ver.semantic_digest()
    assert base_d != diff_ns.semantic_digest()
    assert base_d != diff_dig.semantic_digest()


# --------------------------------------------------------------------------- #
# typed_ref_sort_key — canonical typed semantic projection key                #
# --------------------------------------------------------------------------- #
def test_typed_ref_sort_key_shape():
    ref = _typed_ref(name="Foo", version="2", namespace="sealed", digest=D3)
    assert typed_ref_sort_key(ref) == ("Foo", "2", "sealed", D3)


def test_typed_ref_sort_key_orders_by_name_version_namespace_digest():
    # constructed so each successive tie-break level decides exactly once.
    a = _typed_ref(name="Alpha", version="1", namespace="main", digest=D1)
    b = _typed_ref(name="Alpha", version="1", namespace="main", digest=D2)
    c = _typed_ref(name="Alpha", version="1", namespace="sealed", digest=D1)
    d = _typed_ref(name="Alpha", version="2", namespace="main", digest=D1)
    e = _typed_ref(name="Beta", version="1", namespace="main", digest=D1)
    assert sorted([e, d, c, b, a], key=typed_ref_sort_key) == [a, b, c, d, e]


def test_typed_ref_sort_key_ignores_object_id():
    assert typed_ref_sort_key(_typed_ref(object_id="obj-A")) == typed_ref_sort_key(
        _typed_ref(object_id="obj-B")
    )


# --------------------------------------------------------------------------- #
# validate_typed_ref_tuple — shared owner-side tuple invariant                #
# --------------------------------------------------------------------------- #
def test_validate_typed_ref_tuple_accepts_canonical_main_tuple():
    a = _typed_ref(name="Alpha", digest=D1)
    b = _typed_ref(name="Alpha", digest=D2)
    c = _typed_ref(name="Beta", digest=D1)
    # canonical order, duplicate-free, all main — no raise; empty is also valid.
    validate_typed_ref_tuple((a, b, c), require_main=True, field_name="data_result_refs")
    validate_typed_ref_tuple((), require_main=True, field_name="data_result_refs")


def test_validate_typed_ref_tuple_rejects_out_of_order():
    a = _typed_ref(name="Alpha", digest=D1)
    c = _typed_ref(name="Beta", digest=D1)
    with pytest.raises(ValueError, match="data_result_refs"):
        validate_typed_ref_tuple(
            (c, a), require_main=True, field_name="data_result_refs"
        )


def test_validate_typed_ref_tuple_rejects_exact_duplicate():
    a = _typed_ref()
    with pytest.raises(ValueError, match="execution_evidence_refs"):
        validate_typed_ref_tuple(
            (a, a), require_main=True, field_name="execution_evidence_refs"
        )


def test_validate_typed_ref_tuple_rejects_semantic_duplicate_differing_object_id():
    # two refs differing only in the audit object_id are the SAME evidence.
    a = _typed_ref(object_id="obj-A")
    a2 = _typed_ref(object_id="obj-B")
    with pytest.raises(ValueError, match="execution_evidence_refs"):
        validate_typed_ref_tuple(
            (a, a2), require_main=True, field_name="execution_evidence_refs"
        )


def test_validate_typed_ref_tuple_rejects_non_main_when_required():
    sealed = _typed_ref(namespace="sealed")
    with pytest.raises(ValueError, match="my_evidence"):
        validate_typed_ref_tuple(
            (sealed,), require_main=True, field_name="my_evidence"
        )


def test_validate_typed_ref_tuple_allows_non_main_when_not_required():
    # still canonical + duplicate-free, but namespace is the owner's choice.
    sealed = _typed_ref(namespace="main")
    review = _typed_ref(namespace="review")
    validate_typed_ref_tuple(
        (sealed, review), require_main=False, field_name="my_evidence"
    )


def test_validate_typed_ref_tuple_error_names_the_owning_field():
    a = _typed_ref()
    with pytest.raises(ValueError) as exc_info:
        validate_typed_ref_tuple(
            (a, a), require_main=True, field_name="some_owner_field"
        )
    assert "some_owner_field" in str(exc_info.value)


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
