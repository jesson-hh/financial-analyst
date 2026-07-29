# -*- coding: utf-8 -*-
"""裁决 1 + 裁决 2 — the Lane-0 prompt actually carries the data and the schema.

The first live Lane-0 run answered ``"no factor report; all axes set to
unknown."`` and invented its own field names. Both were honest answers to a
broken question:

* **裁决 1 (data).** ``StaticPromptAssembler`` places every block as a
  schema/namespace/digest REFERENCE, and ``render_factor_report_for_prompt`` —
  documented as the ONLY numeric outlet feeding ``market.regime`` /
  ``market.rotation`` — was registered but never invoked on the execution path.
  The model literally received no numbers. The deep lane already had the answer:
  ``SubjectPromptAssembler`` inlines its OWN trusted subject block while
  untrusted external content stays a reference. :class:`Lane0PromptAssembler`
  applies the same line — the rendered factor report (content this system
  computed and can attest) is inlined and cited by artifact digest; the
  experience-selection block (untrusted retrieval content) stays a reference.
* **裁决 2 (schema).** The sealed prompt materials name ``RegimeReport@1`` and
  its rules but never its field names. The assembler now DERIVES an
  ``output_schema`` section from the worker's own primary ``OutputBinding``
  through the run's schema registry — the same binding
  ``worker._primary_output_binding`` validates the answer against, so the
  declared contract cannot drift from the enforced one.

Run from repo root: ``pytest tests/orchestration/test_lane0_prompt_assembly.py -v``
"""
from __future__ import annotations

import json
from datetime import date as _date
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.catalog import OutputBinding
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.market.factors import (
    DEFAULT_UNIVERSE_REGISTRY_VERSION,
    MARKET_FACTOR_REPORT_SCHEMA_REF,
    REGIME_REPORT_SCHEMA_REF,
    ROTATION_REPORT_SCHEMA_REF,
    BoardPoolRow,
    DailyValueRow,
    MarketFactorInputs,
    MarketFactorReport,
    RegimeReport,
    RotationReport,
    UpDownRow,
    build_market_factor_set_v1,
    compute_market_factors,
    render_factor_report_for_prompt,
)
from guanlan_v2.orchestration.memory.experience import EXPERIENCE_SELECTION_SCHEMA_REF
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    NamedEvidenceDigest,
    PromptUntrustedBlockRef,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry

UTC = timezone.utc
FACTOR_INPUT_NAME = "market_factor_report"


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _stamp(d: str) -> datetime:
    y, m, dd = (int(x) for x in d.split("-"))
    return datetime(y, m, dd, 7, 5, tzinfo=UTC)


def _dates(n: int, start: _date = _date(2025, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _report(*, empty: bool = False) -> MarketFactorReport:
    """A real report: OK + DEGRADED + UNAVAILABLE factors, or an all-UNAVAILABLE one."""
    if empty:
        inp = MarketFactorInputs()
        as_of = _stamp("2025-04-01")
    else:
        d90, d80 = _dates(90), _dates(80)
        inp = MarketFactorInputs(
            updown=tuple(
                UpDownRow(date=d, up=60, down=40, total=100, available_at=_stamp(d))
                for d in d90),
            board_pools=tuple(
                BoardPoolRow(date=d, max_streak=4, promotion_rate=0.3,
                             available_at=_stamp(d))
                for d in d80),
            astock_temp=tuple(
                DailyValueRow(date=d, value=50.0, available_at=_stamp(d))
                for d in _dates(40)),
        )
        as_of = _stamp(d90[-1])
    return compute_market_factors(
        inp, spec=build_market_factor_set_v1(), as_of=as_of, clock_mode="eod",
        universe_registry_version=DEFAULT_UNIVERSE_REGISTRY_VERSION)


def _artifact_digest(report: MarketFactorReport) -> str:
    """Stand in for the committed Artifact envelope's own ``content_digest``.

    The node's ``market_factor_report`` trusted-input digest is the ARTIFACT's
    digest (that is what ``ArtifactRef.content_digest`` carries), not the report
    payload's own seal — the two are deliberately different values.
    """
    return content_digest({"artifact-of": report.content_digest})


class _Pool:
    """The ArtifactPool surface the assembler is allowed to touch."""

    class _Art:
        def __init__(self, payload):
            self.payload = payload
            self.content_digest = _artifact_digest(payload)

    def __init__(self, report=None):
        self._report = report
        self.asked: list[tuple[str, str]] = []

    def committed_output(self, node_id, key):
        self.asked.append((node_id, key))
        if self._report is None or node_id != "lane0.factor":
            return None
        return self._Art(self._report)


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    for model in (MarketFactorReport, RegimeReport, RotationReport):
        reg.register(model)
    reg.seal()
    return reg


def _material(id_: str, kind: str, text: str):
    _ref, mat = build_text_material(
        id=id_, version="1", kind=kind, raw=text.encode("utf-8"))
    return mat


def _experience_block(ordinal: int = 1) -> PromptUntrustedBlockRef:
    """One untrusted retrieval block — a REFERENCE, never bytes."""
    digest = content_digest({"experience": "untrusted retrieval payload"})
    return PromptUntrustedBlockRef.build(
        ordinal=ordinal,
        payload_ref=TypedPayloadRef(
            schema_ref=EXPERIENCE_SELECTION_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="payload-9",
                                   content_digest=digest)),
        media_type="application/json", rendered_length=1234)


def _assemble(assembler, *, report=None, trusted=None, blocks=(),
              output_binding=None, schema_registry=None, worker_id="market.regime"):
    if trusted is None:
        trusted = ()
        if report is not None:
            trusted = (NamedEvidenceDigest(name=FACTOR_INPUT_NAME,
                                           digest=_artifact_digest(report)),)
    trusted = tuple(trusted) + (
        NamedEvidenceDigest(name="context_snapshot", digest="cd" * 32),)
    return assembler.assemble(
        plan_digest="ab" * 32, node_id="lane0.regime", worker_id=worker_id,
        system_prompt=_material("lane0.regime.prompt", "prompt", "SYSTEM TEXT"),
        skills=(_material("lane0.regime.skill", "skill", "SKILL TEXT"),),
        guardrails=(_material("lane0.honesty.guardrail", "guardrail", "GUARDRAIL TEXT"),),
        trusted_input_digests=trusted, untrusted_blocks=tuple(blocks),
        output_binding=output_binding, schema_registry=schema_registry)


def _channel(assembled) -> dict:
    return json.loads(assembled.canonical_request_bytes.decode("utf-8"))


# =========================================================================== #
# 裁决 1 — the rendered numbers actually reach the model                       #
# =========================================================================== #
def test_the_assembled_prompt_carries_the_rendered_factor_numbers():
    """THE load-bearing assertion: real rendered content, never a flag."""
    report = _report()
    rendered = render_factor_report_for_prompt(report)
    pool = _Pool(report)
    assembled = _assemble(B.Lane0PromptAssembler(pool=pool, registry=_registry()),
                          report=report)
    blob = assembled.canonical_request_bytes.decode("utf-8")

    # the whole rendered block, verbatim.
    assert json.dumps(rendered, ensure_ascii=False)[1:-1] in blob or rendered in blob
    channel = _channel(assembled)
    assert channel[B.LANE0_FACTOR_REPORT_SECTION]["text"] == rendered

    # …and the real numbers inside it, not merely a "block present" flag.
    assert f"battery_digest={report.battery_digest[:8]}" in blob
    assert "## breadth" in blob
    ok_ids = [v.factor_id for v in report.values if v.status == "OK"]
    assert ok_ids, "fixture must contain at least one OK factor"
    for fid in ok_ids:
        assert fid in blob
    assert pool.asked == [("lane0.factor", "primary")]


def test_the_inlined_block_is_bound_to_the_artifact_it_came_from():
    report = _report()
    assembled = _assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()), report=report)
    section = _channel(assembled)[B.LANE0_FACTOR_REPORT_SECTION]
    # the FULL 64-hex payload seal (the rendered header shows 12 chars only),
    # so the record proves exactly which report the model saw.
    assert section["factor_report_digest"] == report.content_digest
    assert section["artifact_schema"] == MARKET_FACTOR_REPORT_SCHEMA_REF.key
    assert section["status"] == "present"
    # and the committed ARTIFACT digest is exactly the node's bound input.
    named = {e.name: e.digest for e in assembled.prompt_record.trusted_input_digests}
    assert section["artifact_digest"] == named[FACTOR_INPUT_NAME]
    assert section["artifact_digest"] == _artifact_digest(report)


def test_an_untrusted_block_stays_a_reference():
    """The trusted/untrusted line is the whole point — it must not move."""
    report = _report()
    block = _experience_block()
    assembled = _assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()),
        report=report, blocks=(block,))
    channel = _channel(assembled)
    blob = assembled.canonical_request_bytes.decode("utf-8")

    assert channel["data_inputs"] == [{
        "ordinal": 1,
        "schema": EXPERIENCE_SELECTION_SCHEMA_REF.key,
        "namespace": "main",
        "content_digest": block.payload_ref.payload_ref.content_digest,
        "media_type": "application/json",
        "length": 1234,
    }]
    # the untrusted payload's own bytes never appear.
    assert "untrusted retrieval payload" not in blob


def test_an_absent_factor_report_says_so_in_the_prompt():
    """An empty question must never masquerade as a complete one."""
    assembled = _assemble(
        B.Lane0PromptAssembler(pool=_Pool(None), registry=_registry()), report=None)
    section = _channel(assembled)[B.LANE0_FACTOR_REPORT_SECTION]
    assert section["status"] == "absent"
    assert section["artifact_digest"] is None
    assert section["factor_report_digest"] is None
    assert section["text"] == B.LANE0_NO_FACTOR_REPORT_TEXT
    assert "no factor report" in B.LANE0_NO_FACTOR_REPORT_TEXT.lower()
    # the section is always present: absence is stated, never silently omitted.
    assert B.LANE0_FACTOR_REPORT_SECTION in _channel(assembled)


def test_a_report_with_no_citable_reading_is_declared_degraded():
    report = _report(empty=True)
    assert report.feature_vector == {}
    assembled = _assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()), report=report)
    section = _channel(assembled)[B.LANE0_FACTOR_REPORT_SECTION]
    assert section["status"] == "no_citable_reading"
    assert section["n_ok"] == 0
    assert section["factor_report_digest"] == report.content_digest
    assert section["text"] == render_factor_report_for_prompt(report)
    # 裁决 3 · Option B: the model is told the anchor is impossible AND that the
    # reason is the runtime's to write — never its own.
    assert section["note"] == B.LANE0_NO_CITABLE_READING_NOTE
    assert "do not write one" in section["note"].lower()


def test_a_citable_report_carries_no_no_anchor_note():
    """The note is a measurement, not decoration: with a reading available the
    ④ ≥1 rule stands and nothing licenses an empty citation list."""
    report = _report()
    assert report.feature_vector
    section = _channel(_assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()),
        report=report))[B.LANE0_FACTOR_REPORT_SECTION]
    assert "note" not in section


def test_a_present_report_declares_its_own_coverage_numbers():
    report = _report()
    section = _channel(_assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()),
        report=report))[B.LANE0_FACTOR_REPORT_SECTION]
    assert section["n_ok"] == report.coverage_summary.n_ok
    assert section["n_degraded"] == report.coverage_summary.n_degraded
    assert section["n_unavailable"] == report.coverage_summary.n_unavailable
    assert section["badges"] == list(report.badges)


def test_a_bound_report_the_pool_cannot_produce_refuses_rather_than_inlining():
    report = _report()
    with pytest.raises(B.BootstrapRuntimeError):
        _assemble(B.Lane0PromptAssembler(pool=_Pool(None), registry=_registry()),
                  report=report)


def test_a_digest_mismatch_between_the_bound_input_and_the_pool_refuses():
    """Inlining the WRONG report would be worse than inlining none."""
    report = _report()
    other = _report(empty=True)
    assert other.content_digest != report.content_digest
    with pytest.raises(B.BootstrapRuntimeError):
        _assemble(
            B.Lane0PromptAssembler(pool=_Pool(other), registry=_registry()),
            report=report)


def test_the_assembler_declares_its_own_identity_on_the_record():
    report = _report()
    assembled = _assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()), report=report)
    assert assembled.prompt_record.assembler_id == B.LANE0_ASSEMBLER_ID
    assert assembled.prompt_record.assembler_version == B.LANE0_ASSEMBLER_VERSION
    assert assembled.prompt_record.assembler_id != W.ASSEMBLER_ID


def test_the_trusted_instruction_text_is_unchanged_by_the_inlining():
    report = _report()
    channel = _channel(_assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()), report=report))
    assert channel["system"] == "SYSTEM TEXT"
    assert channel["skills"] == ["SKILL TEXT"]
    assert channel["guardrails"] == ["GUARDRAIL TEXT"]


# =========================================================================== #
# 裁决 2 — the output schema is derived from the worker's own OutputBinding     #
# =========================================================================== #
def _binding(schema_ref: SchemaRef = REGIME_REPORT_SCHEMA_REF) -> OutputBinding:
    return OutputBinding(name="primary", schema_ref=schema_ref)


def test_the_output_schema_section_names_the_schemas_real_fields():
    reg = _registry()
    section = W.output_schema_section(
        output_binding=_binding(), schema_registry=reg)
    assert section["schema"] == REGIME_REPORT_SCHEMA_REF.key
    declared = set(section["required_fields"]) | set(section["optional_fields"])
    # the three names the live model invented are NOT in the contract…
    assert {"regime_type", "risk", "heat", "modal"} & declared == set()
    # …and the ones it needed ARE.
    for name in ("risk_state", "heat_state", "trend_probabilities",
                 "risk_probabilities", "heat_probabilities", "drivers",
                 "evidence_factor_ids", "narrative"):
        assert name in declared, name


def test_the_output_schema_cannot_drift_from_the_registry_it_validates_against():
    """Derived, not prose: change the registered model, the section follows."""
    reg = _registry()
    section = W.output_schema_section(output_binding=_binding(), schema_registry=reg)
    assert section["json_schema"] == reg.resolve(REGIME_REPORT_SCHEMA_REF).model_json_schema()
    other = W.output_schema_section(
        output_binding=_binding(ROTATION_REPORT_SCHEMA_REF), schema_registry=reg)
    assert other["schema"] == ROTATION_REPORT_SCHEMA_REF.key
    assert "mainlines" in set(other["required_fields"]) | set(other["optional_fields"])
    assert other["json_schema"] != section["json_schema"]


def test_the_runtime_owned_fields_are_declared_as_the_runtimes_never_the_models():
    section = W.output_schema_section(
        output_binding=_binding(), schema_registry=_registry(),
        runtime_owned_fields=B.LANE0_RUNTIME_OWNED_FIELDS)
    assert section["runtime_supplied_fields"] == sorted(B.LANE0_RUNTIME_OWNED_FIELDS)
    declared = set(section["required_fields"]) | set(section["optional_fields"])
    for name in B.LANE0_RUNTIME_OWNED_FIELDS:
        assert name not in declared


def test_the_lane0_assembler_marks_the_three_runtime_owned_fields():
    report = _report()
    channel = _channel(_assemble(
        B.Lane0PromptAssembler(pool=_Pool(report), registry=_registry()),
        report=report, output_binding=_binding(), schema_registry=_registry()))
    section = channel[W.OUTPUT_SCHEMA_SECTION]
    assert section["runtime_supplied_fields"] == sorted(B.LANE0_RUNTIME_OWNED_FIELDS)


def test_the_static_assembler_carries_the_section_too():
    channel = _channel(_assemble(
        W.StaticPromptAssembler(), output_binding=_binding(),
        schema_registry=_registry()))
    assert channel[W.OUTPUT_SCHEMA_SECTION]["schema"] == REGIME_REPORT_SCHEMA_REF.key
    # the static assembler makes no Lane-0 runtime-ownership claim.
    assert "runtime_supplied_fields" not in channel[W.OUTPUT_SCHEMA_SECTION]


def test_no_binding_no_section():
    """A caller that declares no output binding (the planner) is unchanged."""
    channel = _channel(_assemble(W.StaticPromptAssembler()))
    assert W.OUTPUT_SCHEMA_SECTION not in channel


def test_an_unresolvable_binding_says_so_instead_of_guessing():
    section = W.output_schema_section(
        output_binding=_binding(SchemaRef(name="NotRegistered", version="1")),
        schema_registry=_registry())
    assert section["schema"] == "NotRegistered@1"
    assert "unresolved" in section
    assert "required_fields" not in section
