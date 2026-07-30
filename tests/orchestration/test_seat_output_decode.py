# -*- coding: utf-8 -*-
"""The deep lane's seats under the reviewed D2/D3 decode treatment (2026-07-31).

Today's live deep run ``deep-f1f0031d521bea3a`` (reduced eight-node preset,
subject 300750) settled two real LLM invocations and terminated ``failed``
because the two answers were validated in strict PYTHON mode — the exact
disease D2 (2026-07-29) diagnosed and fixed for the Lane-0 bootstrap gateway,
which ``assembly.WorkerSeatModelGateway`` never received:

* ``sentiment``  → ``'Neutral' is not an instance of SentimentBand``,
  ``'low' is not an instance of Confidence`` — a CORRECT answer, refused
  purely by decode mode;
* ``bull-r1``    → six errors: ``as_of`` (the model was asked to invent a
  runtime-owned field) plus five ``list``-is-not-``tuple`` artifacts.

The malformed payloads below are transcribed from the persisted evidence
(``var/orchestration/payloads/main`` NodeRun ``reason`` fields for
``nr-deep-f1f0031d521bea3a-sentiment-1`` / ``-bull-r1-1``) — that is what makes
these tests load-bearing.

The treatment is ONE shared recipe (``orchestration.llm_output``), consumed by
BOTH ``bootstrap.normalize_lane0_llm_output`` and the deep lane's
``assembly.SeatOutputNormalizingGateway`` so the two can never drift:

1. strict JSON-mode decoding (exactly the conversions JSON can express);
2. runtime-stamping of unproducible fields (the deep seats' ``as_of`` comes
   from the run's OWN committed subject, echoed by the assembler in the
   canonical channel's ``trusted_subject`` section — never from the model);
3. a NARROW named envelope unwrap (single key == the schema's snake_case name).

Honesty red line, unchanged: the layer never repairs a judgement — anything it
cannot decode comes back byte-identical, and the executor's own
``output_schema_invalid`` refusal now carries the TRUE errors (strict JSON-mode
at step (9) for raw-JSON payloads; see ``test_lane0_driver.py`` for the
masking regression this fixes).

Run: ``python -m pytest tests/orchestration/test_seat_output_decode.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration import llm_output as LO
from guanlan_v2.orchestration.pipeline import assembly as A
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.enums import Confidence, SentimentBand
from guanlan_v2.orchestration.lane_payloads import BearCase, BullCase
from guanlan_v2.orchestration.schemas import SentimentReport
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.refs import SchemaRef

# the run's committed RunSubject (payload c1d144aa…): the runtime-owned as_of.
SUBJECT_AS_OF = datetime(2026, 7, 30, 16, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# the recorded malformed outputs (verbatim where the record carries the value) #
# --------------------------------------------------------------------------- #
def recorded_sentiment_json() -> dict:
    """``nr-deep-f1f0031d521bea3a-sentiment-1``: refused with exactly
    ``overall_band='Neutral'`` / ``confidence='low'`` — both valid enum
    LITERALS, refused only because python-mode strict wants enum INSTANCES."""
    return {
        "schema_version": "1",
        "overall_band": "Neutral",
        "overall_score": 5.0,
        "confidence": "low",
        "narrative": "Neutral tape; flows mixed and no dominant narrative.",
    }


def recorded_bull_json() -> dict:
    """``nr-deep-f1f0031d521bea3a-bull-r1-1``: six python-mode errors — the
    recorded ``as_of`` string IS the committed subject's as_of (the model
    copied the field it should never have been asked for), and every tuple
    field arrived as the JSON list it can only ever be."""
    return {
        "schema_version": "1",
        "symbol": {"code": "300750", "exchange": "SZ", "board": "chinext"},
        "as_of": "2026-07-30T16:00:00+00:00",
        "thesis_bullets": ["Thesis: 300750作为全球动力电池龙头,若盘面证伪则需重新评估。"],
        "catalysts": [],
        "disproof_signals": ["季度出货量环比下滑;主力资金连续5日净流出"],
        "v_anchors": ["V9"],
        "rebuttal_of": [],
    }


# --------------------------------------------------------------------------- #
# test seams (the lane0 llm-output idiom: just enough catalog / request)       #
# --------------------------------------------------------------------------- #
def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(SentimentReport)
    reg.register(BullCase)
    reg.register(BearCase)
    reg.seal()
    return reg


class _FakeCatalog:
    def __init__(self, schema_key):
        self._schema_key = schema_key

    def worker(self, worker_id):
        name, version = self._schema_key.split("@")
        out = SimpleNamespace(
            name="primary", schema_ref=SchemaRef(name=name, version=version))
        return SimpleNamespace(outputs=(out,))


class _FakeInner:
    def __init__(self, payload):
        self._payload = payload
        self.closed = False
        self.calls = 0

    def invoke(self, request, *, prompt_assembly_ref):
        self.calls += 1
        return W.ModelResult(payload=self._payload, rendered_text="raw",
                             input_tokens=3, output_tokens=5)

    def close(self):
        self.closed = True


def _subject_channel_bytes(as_of_iso: str = "2026-07-30T16:00:00+00:00") -> bytes:
    """The SubjectPromptAssembler channel shape the gateway is authorized with."""
    return json.dumps({
        "assembler_id": "pipeline.subject_prompt_assembler",
        "assembler_version": "1",
        "trusted_subject": {
            "artifact_schema": "RunSubject@1",
            "artifact_digest": "c1" * 32,
            "code": "300750",
            "as_of": as_of_iso,
            "text": "本次深度研判的标的:300750。",
        },
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _FakeRequest:
    def __init__(self, worker_id, canonical: bytes | None = None):
        self.prompt_record = SimpleNamespace(worker_id=worker_id, node_id="n1")
        self.canonical_request_bytes = (
            canonical if canonical is not None else _subject_channel_bytes())


def _gateway(payload, *, schema_key, registry=None):
    inner = _FakeInner(payload)
    gw = A.SeatOutputNormalizingGateway(
        inner=inner, catalog_runtime=_FakeCatalog(schema_key),
        registry=registry if registry is not None else _registry())
    return gw, inner


# =========================================================================== #
# 1. the recorded live payloads now decode into their typed reports            #
# =========================================================================== #
def test_the_recorded_sentiment_answer_becomes_a_typed_report():
    gw, inner = _gateway(recorded_sentiment_json(), schema_key="SentimentReport@1")
    result = gw.invoke(_FakeRequest("text.sentiment"), prompt_assembly_ref=None)
    assert isinstance(result.payload, SentimentReport)
    assert result.payload.overall_band is SentimentBand.NEUTRAL
    assert result.payload.confidence is Confidence.LOW
    assert result.input_tokens == 3 and result.output_tokens == 5  # never lost
    assert inner.calls == 1


def test_the_recorded_bull_answer_becomes_a_typed_case_with_the_runtime_as_of():
    gw, _inner = _gateway(recorded_bull_json(), schema_key="BullCase@1")
    payload = gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload
    assert isinstance(payload, BullCase)
    assert payload.as_of == SUBJECT_AS_OF
    assert payload.thesis_bullets == (
        "Thesis: 300750作为全球动力电池龙头,若盘面证伪则需重新评估。",)
    assert payload.v_anchors == ("V9",)
    assert payload.symbol.dotted == "300750.SZ"


def test_a_model_written_as_of_is_discarded_not_trusted():
    """The runtime owns ``as_of``: a model claiming a DIFFERENT timestamp is
    overwritten with the subject's own — never believed."""
    raw = dict(recorded_bull_json(), as_of="2020-01-01T00:00:00+00:00")
    gw, _inner = _gateway(raw, schema_key="BullCase@1")
    payload = gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload
    assert isinstance(payload, BullCase)
    assert payload.as_of == SUBJECT_AS_OF


def test_without_a_trusted_subject_the_models_own_as_of_decodes():
    """A channel with no ``trusted_subject`` (the static assembler) has nothing
    honest to stamp; the model's own field then decodes under strict JSON."""
    canonical = json.dumps({"assembler_id": "static-v1"}).encode("utf-8")
    gw, _inner = _gateway(recorded_bull_json(), schema_key="BullCase@1")
    payload = gw.invoke(
        _FakeRequest("dec.bull", canonical=canonical),
        prompt_assembly_ref=None).payload
    assert isinstance(payload, BullCase)
    assert payload.as_of == SUBJECT_AS_OF  # the recorded string decodes to the same instant


# =========================================================================== #
# 2. the narrow named envelope                                                 #
# =========================================================================== #
def test_a_named_envelope_is_unwrapped():
    gw, _inner = _gateway({"bull_case": recorded_bull_json()}, schema_key="BullCase@1")
    payload = gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload
    assert isinstance(payload, BullCase)


def test_an_envelope_whose_key_is_not_the_schema_name_is_never_unwrapped():
    raw = {"answer": recorded_bull_json()}
    gw, _inner = _gateway(raw, schema_key="BullCase@1")
    assert gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload is raw


def test_a_two_key_envelope_is_never_unwrapped():
    raw = {"bull_case": recorded_bull_json(), "extra": 1}
    gw, _inner = _gateway(raw, schema_key="BullCase@1")
    assert gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload is raw


# =========================================================================== #
# 3. refusals stay refusals (byte-identical passthrough)                       #
# =========================================================================== #
def test_an_undeclared_field_is_never_silently_dropped():
    raw = dict(recorded_sentiment_json(), invented_field="x")
    gw, _inner = _gateway(raw, schema_key="SentimentReport@1")
    assert gw.invoke(_FakeRequest("text.sentiment"),
                     prompt_assembly_ref=None).payload is raw


def test_a_wrongly_typed_field_is_never_coerced_out_of_json_discipline():
    raw = dict(recorded_sentiment_json(), overall_score="5.0")  # str, not number
    gw, _inner = _gateway(raw, schema_key="SentimentReport@1")
    assert gw.invoke(_FakeRequest("text.sentiment"),
                     prompt_assembly_ref=None).payload is raw


def test_a_rule_the_answer_breaks_is_still_a_refusal():
    raw = dict(recorded_bull_json(), thesis_bullets=[])  # a case with no thesis
    gw, _inner = _gateway(raw, schema_key="BullCase@1")
    assert gw.invoke(_FakeRequest("dec.bull"), prompt_assembly_ref=None).payload is raw


def test_a_typed_instance_passes_through_untouched():
    typed = SentimentReport(
        overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
        confidence=Confidence.LOW, narrative="typed")
    gw, _inner = _gateway(typed, schema_key="SentimentReport@1")
    assert gw.invoke(_FakeRequest("text.sentiment"),
                     prompt_assembly_ref=None).payload is typed


def test_an_unresolvable_schema_passes_through_for_the_executor_to_refuse():
    raw = recorded_sentiment_json()
    gw, _inner = _gateway(raw, schema_key="NotRegistered@1")
    assert gw.invoke(_FakeRequest("text.sentiment"),
                     prompt_assembly_ref=None).payload is raw


def test_close_delegates_to_the_inner_gateway():
    gw, inner = _gateway({}, schema_key="SentimentReport@1")
    gw.close()
    assert inner.closed is True


# =========================================================================== #
# 4. ONE shared recipe — bootstrap and the deep lane cannot drift              #
# =========================================================================== #
def test_both_gateways_route_through_the_one_shared_recipe(monkeypatch):
    """The drift guard: lane-0's normalizer and the deep seat gateway both call
    ``llm_output.decode_llm_output_json`` — one recipe, not two implementations."""
    from guanlan_v2.orchestration import bootstrap as B

    calls: list[str] = []
    real = LO.decode_llm_output_json

    def spy(payload, *, model, **kw):
        calls.append(model.__name__)
        return real(payload, model=model, **kw)

    monkeypatch.setattr(LO, "decode_llm_output_json", spy)

    gw, _inner = _gateway(recorded_sentiment_json(), schema_key="SentimentReport@1")
    gw.invoke(_FakeRequest("text.sentiment"), prompt_assembly_ref=None)

    from guanlan_v2.orchestration.market.factors import RegimeReport
    B.normalize_lane0_llm_output(
        {"not": "decodable"}, model=RegimeReport,
        as_of=SUBJECT_AS_OF, factor_report_digest="0" * 64)

    assert calls == ["SentimentReport", "RegimeReport"]


def test_the_subject_assembler_declares_as_of_as_runtime_supplied():
    """裁决 2's other half for the deep lane: the derived output-schema section
    must stop DEMANDING the runtime-owned field the gateway will discard."""
    binding = SimpleNamespace(schema_ref=SchemaRef(name="BullCase", version="1"))
    section = W.output_schema_section(
        output_binding=binding, schema_registry=_registry(),
        runtime_owned_fields=LO.DEEP_SEAT_RUNTIME_OWNED_FIELDS)
    assert section["runtime_supplied_fields"] == ["as_of"]
    assert "as_of" not in section["required_fields"]

    # schemas WITHOUT as_of are byte-identical to the undeclared form.
    s_binding = SimpleNamespace(schema_ref=SchemaRef(name="SentimentReport", version="1"))
    with_decl = W.output_schema_section(
        output_binding=s_binding, schema_registry=_registry(),
        runtime_owned_fields=LO.DEEP_SEAT_RUNTIME_OWNED_FIELDS)
    without = W.output_schema_section(
        output_binding=s_binding, schema_registry=_registry())
    assert with_decl == without


# =========================================================================== #
# 5. step (9) — the executor's decode mode matches the payload's nature        #
# =========================================================================== #
def test_step9_validates_a_raw_json_mapping_in_strict_json_mode():
    reg = _registry()
    ref = SchemaRef(name="SentimentReport", version="1")
    validated = W._validate_primary_output(reg, ref, recorded_sentiment_json())
    assert isinstance(validated, SentimentReport)
    assert validated.overall_band is SentimentBand.NEUTRAL


def test_step9_refusal_of_a_raw_json_mapping_carries_the_true_errors():
    reg = _registry()
    ref = SchemaRef(name="BullCase", version="1")
    raw = dict(recorded_bull_json(), thesis_bullets=[])
    with pytest.raises(Exception) as excinfo:
        W._validate_primary_output(reg, ref, raw)
    msg = str(excinfo.value)
    assert "at least one thesis bullet" in msg
    assert "is_instance_of" not in msg      # never the python-mode artifacts


def test_step9_still_refuses_wrong_json_types_strictly():
    reg = _registry()
    ref = SchemaRef(name="SentimentReport", version="1")
    raw = dict(recorded_sentiment_json(), overall_score="5.0")
    with pytest.raises(Exception) as excinfo:
        W._validate_primary_output(reg, ref, raw)
    assert "overall_score" in str(excinfo.value)


def test_step9_keeps_the_python_mode_path_for_non_json_mappings():
    """A Mapping carrying values JSON cannot express is a scripted double, not
    a model completion — the reviewed python-mode path stands for it."""
    reg = _registry()
    ref = SchemaRef(name="BullCase", version="1")
    from guanlan_v2.orchestration.data.symbols import Symbol
    mapping = {
        "schema_version": "1",
        "symbol": Symbol(code="300750", exchange="SZ", board="chinext"),
        "as_of": SUBJECT_AS_OF,                     # a python datetime
        "thesis_bullets": ("typed tuple",),
    }
    validated = W._validate_primary_output(reg, ref, mapping)
    assert isinstance(validated, BullCase)
    assert validated.as_of == SUBJECT_AS_OF


def test_step9_names_a_schema_version_mismatch():
    reg = _registry()
    ref = SchemaRef(name="SentimentReport", version="1")
    raw = dict(recorded_sentiment_json(), schema_version="9")
    with pytest.raises(Exception) as excinfo:
        W._validate_primary_output(reg, ref, raw)
    assert "schema_version" in str(excinfo.value)


def test_step9_typed_instances_keep_the_python_mode_path_bit_for_bit():
    reg = _registry()
    ref = SchemaRef(name="SentimentReport", version="1")
    typed = SentimentReport(
        overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
        confidence=Confidence.LOW, narrative="typed")
    validated = W._validate_primary_output(reg, ref, typed)
    assert isinstance(validated, SentimentReport)
