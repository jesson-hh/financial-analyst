# -*- coding: utf-8 -*-
"""Task 6 — Artifact / provenance / number-anchor contracts (``schemas.py``).

Written test-first: with ``guanlan_v2/orchestration/schemas.py`` absent the
module fails to import (RED). It then locks, GREEN, the reviewed invariants —
critically the **three distinct digest meanings** of :class:`Artifact`:

* ``content_digest`` — the typed payload plus its payload :class:`SchemaRef`
  (type/version). Only a payload / schema-ref change moves it.
* ``reproducibility_digest`` — the *stable* provenance: plan/code/model-config/
  prompt/skills/capabilities, input-artifact + data digests and deterministic
  tool request/result digests. A prompt/skill/model-config/data-input change
  moves it; the whole ``Provenance`` object is **not** excluded from it.
* ``audit_digest`` — the complete tamper-evidence digest: random IDs, wall-clock,
  provider response ID and every other volatile fact. Changing *only* the
  provider response ID (or wall-clock, or a random id) moves **only** this one.

Plus: ``rendered_from_payload_digest`` binds the rendered source payload;
``ArtifactRef``'s random artifact id is audit-only while schema/producer/slot/
output/content-digest are its semantic projection; ``NumberAnchor`` values are
finite and ``is_unsourced`` is explicit; every model is frozen and forbids extra
fields; all declared digests are recomputed and verified on load.

Run from repo root: ``pytest tests/orchestration/test_artifact.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataMode, NodeStatus
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    SchemaRegistry,
    SchemaRegistryError,
    UnknownSchemaError,
)
from guanlan_v2.orchestration.schemas import (
    Artifact,
    ArtifactRef,
    ArtifactRelation,
    NodeRun,
    NumberAnchor,
    Provenance,
    ToolCallRecord,
    validate_artifact_payload,
)

UTC = timezone.utc

# Well-formed but distinct sha256 hex digests used as opaque referenced digests.
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
DE = "e" * 64
DF = "f" * 64
WRONG = "0" * 64


def _dt(hour: int = 9, minute: int = 30, *, day: int = 15) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Concrete, registrable payload schemas                                       #
# --------------------------------------------------------------------------- #
class DemoPayload(DigestModel):
    """A real typed payload — the content the artifact carries."""

    schema_version: Literal["1"] = "1"
    ticker: NonEmptyStr
    score: FiniteFloat


class OtherPayload(DigestModel):
    """A different registered schema with an incompatible field set."""

    schema_version: Literal["1"] = "1"
    headline: NonEmptyStr


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #
def _typed_ref(
    *,
    name: str = "DemoPayload",
    version: str = "1",
    namespace: str = "main",
    object_id: str = "obj-1",
    content: str = DE,
) -> TypedPayloadRef:
    """A composite typed evidence ref: exact schema identity + payload locator."""
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version=version),
        payload_ref=PayloadRef(namespace=namespace, object_id=object_id, content_digest=content),
    )


def _tool_call(
    *,
    call_ordinal: int = 1,
    request: str = DD,
    result: str = DE,
    call_id: str = "call-1",
    provider_call_id: str | None = None,
    started: datetime | None = None,
    finished: datetime | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(
        call_ordinal=call_ordinal,
        tool_ref=CapabilityRef(id="news.search", version="1", content_digest=DC),
        request_ref=_typed_ref(name="ToolRequest", object_id="req-obj", content=request),
        result_ref=_typed_ref(name="ToolResult", object_id="res-obj", content=result),
        call_id=call_id,
        provider_call_id=provider_call_id,
        started_at=started or _dt(9, 0),
        finished_at=finished or _dt(9, 1),
    )


def _provenance(
    *,
    prompt_digest: str = DB,
    skill_ref: ContentRef | None = None,
    model_config_digest: str | None = DF,
    model_response_id: str | None = "resp-1",
    tool_calls=None,
    input_snapshot_digest: str | None = "1" * 64,
    data_result_refs=None,
    execution_evidence_refs=(),
) -> Provenance:
    if data_result_refs is None:
        data_result_refs = (_typed_ref(name="DataResult", object_id="dr-obj", content="2" * 64),)
    return Provenance(
        plan_digest=DA,
        code_version="git:abc123",
        as_of=_dt(15, 0),
        pit_mode=DataMode.ONLINE,
        model_config_digest=model_config_digest,
        sampling_seed=7,
        prompt_ref=ContentRef(id="reader.prompt", version="1", content_digest=prompt_digest),
        skill_refs=(skill_ref,) if skill_ref is not None else (),
        capability_refs=(CapabilityRef(id="news.search", version="1", content_digest=DC),),
        input_snapshot_digest=input_snapshot_digest,
        data_result_refs=tuple(data_result_refs),
        execution_evidence_refs=tuple(execution_evidence_refs),
        tool_call_records=tuple(tool_calls) if tool_calls is not None else (_tool_call(),),
        provider="deepseek",
        model="v4-pro",
        model_response_id=model_response_id,
    )


def _input_ref(*, artifact_id: str = "in-1", content_digest_hex: str = "3" * 64) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        schema_ref=SchemaRef(name="DemoPayload", version="1"),
        producer_node_id="node.market",
        slot="market",
        output_key="factor",
        content_digest=content_digest_hex,
        relation="input",
    )


def _anchor(
    *,
    value: float = 0.74,
    is_unsourced: bool = False,
    source_artifact_id: str | None = "in-1",
    source_data_result_id: str | None = None,
) -> NumberAnchor:
    return NumberAnchor(
        label="excess",
        value=value,
        unit="pct",
        as_of=_dt(15, 0),
        payload_path="metrics.excess",
        source_artifact_id=source_artifact_id,
        source_data_result_id=source_data_result_id,
        is_unsourced=is_unsourced,
    )


def _artifact(
    *,
    payload: DemoPayload | None = None,
    provenance: Provenance | None = None,
    artifact_id: str = "art-1",
    run_id: str = "run-1",
    created_at: datetime | None = None,
    rendered_md: str = "# Report",
    numbers=None,
    input_refs=None,
    schema_ref: SchemaRef | None = None,
    **over,
) -> "Artifact[DemoPayload]":
    base = dict(
        artifact_id=artifact_id,
        run_id=run_id,
        producer_node_id="node.research",
        slot="research",
        output_key="report",
        kind="research_plan",
        payload_schema_ref=schema_ref or SchemaRef(name="DemoPayload", version="1"),
        payload=payload if payload is not None else DemoPayload(ticker="600519.SH", score=0.74),
        rendered_md=rendered_md,
        input_refs=input_refs if input_refs is not None else (_input_ref(),),
        provenance=provenance or _provenance(),
        numbers=numbers if numbers is not None else (_anchor(),),
        badges=("board_backfilled",),
        created_at=created_at or _dt(16, 0),
    )
    base.update(over)
    return Artifact[DemoPayload].build(**base)


# --------------------------------------------------------------------------- #
# 0. all produced models are DigestModels                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cls", [ArtifactRef, ToolCallRecord, Provenance, NumberAnchor, Artifact, ArtifactRelation]
)
def test_all_models_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


# --------------------------------------------------------------------------- #
# 1. the three digest layers vary independently                               #
# --------------------------------------------------------------------------- #
def test_three_digest_layers_are_distinct():
    a = _artifact()
    assert len({a.content_digest, a.reproducibility_digest, a.audit_digest}) == 3


def test_content_layer_moves_only_with_payload():
    a = _artifact()
    b = _artifact(payload=DemoPayload(ticker="600519.SH", score=0.99))
    assert b.content_digest != a.content_digest         # payload changed content
    assert b.reproducibility_digest == a.reproducibility_digest  # not reproducibility


def test_reproducibility_layer_moves_without_touching_content():
    a = _artifact()
    b = _artifact(provenance=_provenance(prompt_digest=DC))
    assert b.reproducibility_digest != a.reproducibility_digest
    assert b.content_digest == a.content_digest


def test_audit_layer_moves_without_touching_content_or_reproducibility():
    a = _artifact()
    b = _artifact(created_at=_dt(23, 0))
    assert b.audit_digest != a.audit_digest
    assert b.content_digest == a.content_digest
    assert b.reproducibility_digest == a.reproducibility_digest


# --------------------------------------------------------------------------- #
# 2. random artifact / run ids don't alter content (or reproducibility)       #
# --------------------------------------------------------------------------- #
def test_random_ids_preserve_content_and_reproducibility():
    a = _artifact(artifact_id="art-1", run_id="run-1")
    b = _artifact(artifact_id="art-2", run_id="run-2")
    assert a.content_digest == b.content_digest
    assert a.reproducibility_digest == b.reproducibility_digest
    assert a.audit_digest != b.audit_digest  # the random ids live only in audit


def test_input_ref_random_id_does_not_change_reproducibility():
    a = _artifact(input_refs=(_input_ref(artifact_id="in-1"),))
    b = _artifact(input_refs=(_input_ref(artifact_id="in-99"),))
    assert a.reproducibility_digest == b.reproducibility_digest


# --------------------------------------------------------------------------- #
# 3. prompt / skill / model-config / data-input digests move reproducibility  #
# --------------------------------------------------------------------------- #
def test_prompt_digest_change_moves_reproducibility_only():
    a = _artifact(provenance=_provenance(prompt_digest=DB))
    b = _artifact(provenance=_provenance(prompt_digest=DC))
    assert a.reproducibility_digest != b.reproducibility_digest
    assert a.content_digest == b.content_digest


def test_skill_ref_change_moves_reproducibility():
    skill = ContentRef(id="skill.price_action", version="1", content_digest="7" * 64)
    a = _artifact(provenance=_provenance())
    b = _artifact(provenance=_provenance(skill_ref=skill))
    assert a.reproducibility_digest != b.reproducibility_digest
    assert a.content_digest == b.content_digest


def test_model_config_digest_change_moves_reproducibility():
    a = _artifact(provenance=_provenance(model_config_digest=DF))
    b = _artifact(provenance=_provenance(model_config_digest="8" * 64))
    assert a.reproducibility_digest != b.reproducibility_digest


def test_input_artifact_content_digest_change_moves_reproducibility():
    a = _artifact(input_refs=(_input_ref(content_digest_hex="3" * 64),))
    b = _artifact(input_refs=(_input_ref(content_digest_hex="4" * 64),))
    assert a.reproducibility_digest != b.reproducibility_digest
    assert a.content_digest == b.content_digest


def test_data_result_ref_change_moves_reproducibility():
    a = _artifact(provenance=_provenance(
        data_result_refs=(_typed_ref(name="DataResult", object_id="dr", content="2" * 64),)))
    b = _artifact(provenance=_provenance(
        data_result_refs=(_typed_ref(name="DataResult", object_id="dr", content="9" * 64),)))
    assert a.reproducibility_digest != b.reproducibility_digest


def test_tool_request_ref_change_moves_reproducibility():
    a = _artifact(provenance=_provenance(tool_calls=[_tool_call(request=DD)]))
    b = _artifact(provenance=_provenance(tool_calls=[_tool_call(request="0" * 64)]))
    assert a.reproducibility_digest != b.reproducibility_digest


# --------------------------------------------------------------------------- #
# 4. provider response id / wall-clock move ONLY the audit digest             #
# --------------------------------------------------------------------------- #
def test_provider_response_id_moves_audit_only():
    a = _artifact(provenance=_provenance(model_response_id="resp-1"))
    b = _artifact(provenance=_provenance(model_response_id="resp-2"))
    assert a.audit_digest != b.audit_digest
    assert a.content_digest == b.content_digest
    assert a.reproducibility_digest == b.reproducibility_digest


def test_created_at_moves_audit_only():
    a = _artifact(created_at=_dt(16, 0))
    b = _artifact(created_at=_dt(17, 0))
    assert a.audit_digest != b.audit_digest
    assert a.content_digest == b.content_digest
    assert a.reproducibility_digest == b.reproducibility_digest


def test_tool_call_wallclock_moves_audit_only():
    a = _artifact(provenance=_provenance(tool_calls=[_tool_call(started=_dt(9, 0), finished=_dt(9, 1))]))
    b = _artifact(provenance=_provenance(tool_calls=[_tool_call(started=_dt(10, 0), finished=_dt(10, 1))]))
    assert a.audit_digest != b.audit_digest
    assert a.content_digest == b.content_digest
    assert a.reproducibility_digest == b.reproducibility_digest


def test_provenance_response_id_is_semantic_excluded_on_its_own():
    p1 = _provenance(model_response_id="resp-1")
    p2 = _provenance(model_response_id="resp-2")
    assert p1.semantic_digest() == p2.semantic_digest()      # reproducibility contribution equal
    assert p1.audit_digest_value() != p2.audit_digest_value()  # full audit differs


# --------------------------------------------------------------------------- #
# 5. declared digest mismatch + payload schema validation                     #
# --------------------------------------------------------------------------- #
def test_build_roundtrips_and_verifies_on_load():
    a = _artifact()
    reloaded = Artifact[DemoPayload](**a.model_dump())
    assert reloaded.content_digest == a.content_digest
    assert reloaded.reproducibility_digest == a.reproducibility_digest
    assert reloaded.audit_digest == a.audit_digest


@pytest.mark.parametrize("field", ["content_digest", "reproducibility_digest", "audit_digest"])
def test_declared_digest_mismatch_rejected(field):
    a = _artifact()
    payload = a.model_dump()
    payload[field] = WRONG
    with pytest.raises(ValidationError):
        Artifact[DemoPayload](**payload)


def test_validate_artifact_payload_happy_path():
    reg = SchemaRegistry()
    reg.register(DemoPayload)
    a = _artifact()
    validated = validate_artifact_payload(a, reg)
    assert isinstance(validated, DemoPayload)
    assert validated.ticker == "600519.SH"


def test_validate_artifact_payload_unknown_schema_rejected():
    reg = SchemaRegistry()  # empty registry
    a = _artifact()
    with pytest.raises(UnknownSchemaError):
        validate_artifact_payload(a, reg)


def test_validate_artifact_payload_wrong_schema_ref_rejected():
    reg = SchemaRegistry()
    reg.register(DemoPayload)
    reg.register(OtherPayload)
    # payload is a DemoPayload but the ref points at OtherPayload (different fields).
    a = _artifact(schema_ref=SchemaRef(name="OtherPayload", version="1"))
    with pytest.raises((ValidationError, SchemaRegistryError)):
        validate_artifact_payload(a, reg)


# --------------------------------------------------------------------------- #
# 6. rendered-from-payload digest binds the source payload                    #
# --------------------------------------------------------------------------- #
def test_rendered_from_payload_digest_binds_payload():
    a = _artifact()
    assert a.rendered_from_payload_digest == content_digest(a.payload)


def test_rendered_from_payload_digest_mismatch_rejected():
    # a rendered binding that does not match the current payload is rejected,
    # even when the three self-sealed digests are internally consistent.
    with pytest.raises(ValidationError):
        _artifact(rendered_from_payload_digest=WRONG)


def test_rendered_binding_of_a_different_payload_rejected():
    other = content_digest(DemoPayload(ticker="000001.SZ", score=1.0))
    with pytest.raises(ValidationError):
        _artifact(rendered_from_payload_digest=other)


# --------------------------------------------------------------------------- #
# 7. NumberAnchor — finite value + explicit is_unsourced                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_number_anchor_non_finite_value_rejected(bad):
    with pytest.raises(ValidationError):
        NumberAnchor(label="x", value=bad, payload_path="p", is_unsourced=True)


def test_number_anchor_bool_value_rejected():
    with pytest.raises(ValidationError):
        NumberAnchor(label="x", value=True, payload_path="p", is_unsourced=True)


def test_number_anchor_sourced_requires_a_source():
    # is_unsourced=False must name a source: never silently treated as sourced.
    with pytest.raises(ValidationError):
        NumberAnchor(label="x", value=1.0, payload_path="p", is_unsourced=False)


def test_number_anchor_unsourced_forbids_a_source():
    with pytest.raises(ValidationError):
        NumberAnchor(
            label="x", value=1.0, payload_path="p", is_unsourced=True, source_artifact_id="a-1"
        )


def test_number_anchor_valid_sourced_and_unsourced():
    sourced = NumberAnchor(
        label="x", value=1.0, payload_path="p", is_unsourced=False, source_artifact_id="a-1"
    )
    assert sourced.is_unsourced is False
    unsourced = NumberAnchor(label="x", value=1.0, payload_path="p", is_unsourced=True)
    assert unsourced.is_unsourced is True


def test_number_anchor_is_unsourced_is_required():
    with pytest.raises(ValidationError):
        NumberAnchor(label="x", value=1.0, payload_path="p")


# --------------------------------------------------------------------------- #
# 8. ArtifactRef semantic projection + ArtifactRelation                       #
# --------------------------------------------------------------------------- #
def test_artifact_ref_artifact_id_is_audit_only():
    r1 = _input_ref(artifact_id="in-1")
    r2 = _input_ref(artifact_id="in-2")
    assert r1.semantic_digest() == r2.semantic_digest()
    assert r1.audit_digest_value() != r2.audit_digest_value()


@pytest.mark.parametrize(
    "kw", [{"content_digest_hex": "4" * 64}]
)
def test_artifact_ref_content_digest_is_semantic(kw):
    assert _input_ref().semantic_digest() != _input_ref(**kw).semantic_digest()


def test_artifact_relation_from_and_to_must_differ():
    with pytest.raises(ValidationError):
        ArtifactRelation(
            event_id="ev-1", relation="supersedes",
            from_artifact_id="a1", to_artifact_id="a1", created_at=_dt(),
        )


def test_artifact_relation_created_at_is_audit_only():
    kw = dict(event_id="ev-1", relation="refutes", from_artifact_id="a1", to_artifact_id="a2")
    r1 = ArtifactRelation(**kw, created_at=_dt(9, 0))
    r2 = ArtifactRelation(**kw, created_at=_dt(10, 0))
    assert r1.semantic_digest() == r2.semantic_digest()
    assert r1.audit_digest_value() != r2.audit_digest_value()


# --------------------------------------------------------------------------- #
# 9. every model forbids extra fields and is immutable                        #
# --------------------------------------------------------------------------- #
def test_artifact_rejects_extra_field():
    a = _artifact()
    with pytest.raises(ValidationError):
        Artifact[DemoPayload](**a.model_dump(), bogus=1)


def test_artifact_is_frozen():
    a = _artifact()
    with pytest.raises(ValidationError):
        a.run_id = "run-2"


def test_provenance_rejects_extra_field():
    with pytest.raises(ValidationError):
        Provenance(
            plan_digest=DA, code_version="git:abc", as_of=_dt(15, 0), pit_mode=DataMode.ONLINE,
            bogus=1,
        )


def test_provenance_is_frozen():
    p = _provenance()
    with pytest.raises(ValidationError):
        p.code_version = "git:def"


def test_tool_call_record_rejects_extra_field():
    with pytest.raises(ValidationError):
        ToolCallRecord(
            call_ordinal=1,
            tool_ref=CapabilityRef(id="news.search", version="1", content_digest=DC),
            request_ref=_typed_ref(name="ToolRequest", content=DD),
            result_ref=_typed_ref(name="ToolResult", content=DE),
            call_id="c-1", started_at=_dt(9, 0), finished_at=_dt(9, 1), bogus=1,
        )


def test_tool_call_record_is_frozen():
    t = _tool_call()
    with pytest.raises(ValidationError):
        t.call_ordinal = 2


def test_number_anchor_rejects_extra_field():
    with pytest.raises(ValidationError):
        NumberAnchor(label="x", value=1.0, payload_path="p", is_unsourced=True, bogus=1)


def test_number_anchor_is_frozen():
    n = _anchor()
    with pytest.raises(ValidationError):
        n.value = 2.0


def test_artifact_ref_rejects_extra_field():
    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="in-1", schema_ref=SchemaRef(name="DemoPayload", version="1"),
            producer_node_id="node.a", slot="s", output_key="o", content_digest="3" * 64,
            relation="input", bogus=1,
        )


def test_artifact_relation_is_frozen():
    r = ArtifactRelation(
        event_id="ev-1", relation="approves", from_artifact_id="a1", to_artifact_id="a2",
        created_at=_dt(),
    )
    with pytest.raises(ValidationError):
        r.relation = "rejects"


# --------------------------------------------------------------------------- #
# 10. typed evidence refs — amendment 1 overhaul                              #
#   ToolCallRecord is success-only with a service call_ordinal + typed         #
#   request/result refs; Provenance/NodeRun freeze canonical, main-only,       #
#   duplicate-free data-result / execution-evidence tuples with a tool-result  #
#   cross-match. A cache hit lives only in data_result_refs.                    #
# --------------------------------------------------------------------------- #
def test_tool_call_record_success_only_has_no_status_field():
    # the record represents a *successful finalized* call: it cannot carry a
    # pending/rejected/cache-only marker, so a status kwarg is an extra field.
    with pytest.raises(ValidationError):
        ToolCallRecord(
            call_ordinal=1,
            tool_ref=CapabilityRef(id="news.search", version="1", content_digest=DC),
            request_ref=_typed_ref(name="ToolRequest", content=DD),
            result_ref=_typed_ref(name="ToolResult", content=DE),
            call_id="c-1", started_at=_dt(9, 0), finished_at=_dt(9, 1),
            status="refused",
        )


def test_tool_call_record_requires_a_result_ref():
    # a finalized successful call always produced a result; result_ref is required.
    with pytest.raises(ValidationError):
        ToolCallRecord(
            call_ordinal=1,
            tool_ref=CapabilityRef(id="news.search", version="1", content_digest=DC),
            request_ref=_typed_ref(name="ToolRequest", content=DD),
            call_id="c-1", started_at=_dt(9, 0), finished_at=_dt(9, 1),
        )


@pytest.mark.parametrize("bad", [0, -1])
def test_tool_call_record_call_ordinal_must_be_positive(bad):
    with pytest.raises(ValidationError):
        _tool_call(call_ordinal=bad)


@pytest.mark.parametrize("which", ["request_ref", "result_ref"])
def test_tool_call_record_refs_must_be_main(which):
    base = dict(
        call_ordinal=1,
        tool_ref=CapabilityRef(id="news.search", version="1", content_digest=DC),
        request_ref=_typed_ref(name="ToolRequest", content=DD),
        result_ref=_typed_ref(name="ToolResult", content=DE),
        call_id="c-1", started_at=_dt(9, 0), finished_at=_dt(9, 1),
    )
    base[which] = _typed_ref(namespace="sealed", content=DD)
    with pytest.raises(ValidationError):
        ToolCallRecord(**base)


def test_tool_call_record_clock_coherence_kept():
    with pytest.raises(ValidationError):
        _tool_call(started=_dt(10, 0), finished=_dt(9, 0))


def test_tool_call_provider_call_id_is_audit_only():
    a = _tool_call(provider_call_id="prov-1")
    b = _tool_call(provider_call_id="prov-2")
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


# -- Provenance evidence-tuple invariants -----------------------------------
def test_provenance_tool_call_records_order_by_call_ordinal():
    p = _provenance(tool_calls=[_tool_call(call_ordinal=1), _tool_call(call_ordinal=2)])
    assert [r.call_ordinal for r in p.tool_call_records] == [1, 2]


def test_provenance_tool_call_records_reject_disordered_ordinal():
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[_tool_call(call_ordinal=2), _tool_call(call_ordinal=1)])


def test_provenance_tool_call_records_reject_duplicate_ordinal():
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[_tool_call(call_ordinal=1), _tool_call(call_ordinal=1)])


def test_provenance_data_result_refs_reject_out_of_order():
    r1 = _typed_ref(name="AData", object_id="o1", content="1" * 64)
    r2 = _typed_ref(name="BData", object_id="o2", content="2" * 64)
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[], data_result_refs=(r2, r1))


def test_provenance_data_result_refs_reject_duplicate_semantic_identity():
    # two refs differing only in the audit object_id are the same evidence.
    r = _typed_ref(name="AData", object_id="o1", content="1" * 64)
    dup = _typed_ref(name="AData", object_id="o2", content="1" * 64)
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[], data_result_refs=(r, dup))


def test_provenance_data_result_refs_reject_non_main():
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[], data_result_refs=(_typed_ref(namespace="sealed", content="1" * 64),))


def test_provenance_execution_evidence_refs_accept_canonical():
    r1 = _typed_ref(name="AEvidence", object_id="o1", content="1" * 64)
    r2 = _typed_ref(name="BEvidence", object_id="o2", content="2" * 64)
    p = _provenance(execution_evidence_refs=(r1, r2))
    assert len(p.execution_evidence_refs) == 2


def test_provenance_execution_evidence_refs_reject_out_of_order():
    r1 = _typed_ref(name="AEvidence", object_id="o1", content="1" * 64)
    r2 = _typed_ref(name="BEvidence", object_id="o2", content="2" * 64)
    with pytest.raises(ValidationError):
        _provenance(execution_evidence_refs=(r2, r1))


def test_provenance_execution_evidence_refs_reject_non_main():
    with pytest.raises(ValidationError):
        _provenance(execution_evidence_refs=(_typed_ref(namespace="review", content="1" * 64),))


# -- tool-result / data-result cross-match ----------------------------------
def test_data_result_that_is_a_tool_result_cross_matches():
    # the consumed data result IS the tool's finalized result (same typed identity).
    shared = _typed_ref(name="ToolResult", object_id="res-obj", content=DE)
    p = _provenance(tool_calls=[_tool_call(result=DE)], data_result_refs=(shared,))
    assert p.data_result_refs[0].payload_ref.content_digest == DE


def test_data_result_sharing_tool_result_content_but_wrong_schema_rejected():
    # same content digest as the tool result, different SchemaRef → cannot pose
    # as that finalized tool result.
    forged = _typed_ref(name="ForgedResult", object_id="x", content=DE)
    with pytest.raises(ValidationError):
        _provenance(tool_calls=[_tool_call(result=DE)], data_result_refs=(forged,))


def test_cache_hit_data_result_without_tool_call_is_retained_honestly():
    # a verified cache hit appears only in data_result_refs, fabricates no
    # ToolCallRecord, and its content matches no tool result.
    cache = _typed_ref(name="CachedData", object_id="c", content="7" * 64)
    p = _provenance(tool_calls=[], data_result_refs=(cache,))
    assert p.tool_call_records == ()
    assert p.data_result_refs == (cache,)


# -- differential digests through an embedding Artifact ---------------------
def test_execution_evidence_ref_change_moves_reproducibility():
    ev = _typed_ref(name="PromptAssembly", object_id="e1", content="5" * 64)
    a = _artifact(provenance=_provenance())
    b = _artifact(provenance=_provenance(execution_evidence_refs=(ev,)))
    assert a.reproducibility_digest != b.reproducibility_digest
    assert a.content_digest == b.content_digest


def test_tool_result_ref_change_moves_reproducibility():
    a = _artifact(provenance=_provenance(tool_calls=[_tool_call(result=DE)]))
    b = _artifact(provenance=_provenance(tool_calls=[_tool_call(result="9" * 64)]))
    assert a.reproducibility_digest != b.reproducibility_digest
    assert a.content_digest == b.content_digest


def test_provider_call_id_moves_audit_only():
    a = _artifact(provenance=_provenance(tool_calls=[_tool_call(provider_call_id="p-1")]))
    b = _artifact(provenance=_provenance(tool_calls=[_tool_call(provider_call_id="p-2")]))
    assert a.audit_digest != b.audit_digest
    assert a.content_digest == b.content_digest
    assert a.reproducibility_digest == b.reproducibility_digest


def test_evidence_object_id_relocation_moves_audit_only():
    # re-storing identical evidence under a new object_id is audit-only:
    # reproducibility and content are unchanged, audit moves.
    a = _artifact(provenance=_provenance(
        data_result_refs=(_typed_ref(name="DataResult", object_id="dr-1", content="2" * 64),)))
    b = _artifact(provenance=_provenance(
        data_result_refs=(_typed_ref(name="DataResult", object_id="dr-2", content="2" * 64),)))
    assert a.reproducibility_digest == b.reproducibility_digest
    assert a.content_digest == b.content_digest
    assert a.audit_digest != b.audit_digest


# -- cross-object equal-construction pattern (Phase 2 executor enforces) -----
def test_artifact_provenance_and_node_run_carry_equal_evidence_tuples():
    tool_calls = (_tool_call(call_ordinal=1),)
    data_refs = (_typed_ref(name="DataResult", object_id="dr", content="2" * 64),)
    evidence = (_typed_ref(name="PromptAssembly", object_id="e", content="5" * 64),)
    prov = _provenance(
        tool_calls=list(tool_calls),
        data_result_refs=data_refs,
        execution_evidence_refs=evidence,
    )
    art = _artifact(provenance=prov)
    nr = NodeRun(
        node_run_id="nr-1", run_id="run-1", plan_id="plan-1", plan_digest="a" * 64,
        node_id="node.research", worker_id="worker.reader", status=NodeStatus.COMPLETED,
        attempt_id="att-1", input_snapshot_digest="1" * 64,
        started_at=_dt(9, 0), finished_at=_dt(9, 5),
        output_keys=("report",), output_artifact_ids=("art-1",),
        tool_call_records=tool_calls, data_result_refs=data_refs,
        execution_evidence_refs=evidence,
    )
    assert nr.tool_call_records == art.provenance.tool_call_records
    assert nr.data_result_refs == art.provenance.data_result_refs
    assert nr.execution_evidence_refs == art.provenance.execution_evidence_refs
