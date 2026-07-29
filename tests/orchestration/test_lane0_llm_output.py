# -*- coding: utf-8 -*-
"""D2/D3 — the Lane-0 LLM output contract against REAL model JSON.

The first live Lane-0 run (2026-07-29, deepseek over ``config/llm.yaml``) came
back with both LLM seats ``incomplete / output_schema_invalid``. Every test
gateway in the suite returns already-constructed ``RegimeReport`` /
``RotationReport`` INSTANCES, so the prompt↔schema contract had never met a
model's raw JSON. Three separate things were wrong at once:

1. ``DigestModel`` is ``strict=True``, and python-mode strict validation can
   never accept JSON: a ``str`` is not a ``TrendState``, a ``list`` is not a
   ``tuple``, a ``str`` is not a ``datetime``. Pinned by
   :func:`test_strict_python_mode_alone_can_never_accept_model_json`.
2. Three fields are unproducible BY CONSTRUCTION: ``content_digest`` (a
   self-seal over the canonical projection), ``factor_report_digest`` (a full
   64-hex digest, while the rendered block header shows only a 12-char prefix)
   and ``as_of`` (the report's own stamp). The runtime owns them.
3. The rotation seat wrapped its answer in ``{"rotation_report": {...}}``.

The malformed shapes below are transcribed from the persisted evidence
(``var/orchestration/payloads/main`` NodeRun ``reason`` fields for
``nr-lane0-2026-07-29-lane0.regime-1`` / ``-lane0.rotation-1``).

Run: ``python -m pytest tests/orchestration/test_lane0_llm_output.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration.market.factors import (
    NO_CITABLE_READING_REASON,
    NO_FACTOR_REPORT_DIGEST,
    NO_FACTOR_REPORT_REASON,
    RegimeReport,
    RotationReport,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, 1, 30, tzinfo=UTC)
DIGEST = "a" * 64


# --------------------------------------------------------------------------- #
# the shapes the live run actually produced                                    #
# --------------------------------------------------------------------------- #
def recorded_regime_json() -> dict:
    """``lane0.regime`` attempt 1 — 19 validation errors, transcribed."""
    return {
        "regime_type": "RegimeReport@1",
        "as_of": None,
        "factor_report_digest": None,
        "trend": {"bull": 0.0, "bear": 0.0, "range": 0.0, "unknown": 1.0},
        "risk": {"risk_on": 0.0, "risk_off": 0.0, "neutral": 0.0, "unknown": 1.0},
        "heat": {"normal": 0.0, "overheat": 0.0, "unknown": 1.0},
        "modal": {"trend": "unknown", "risk": "unknown", "heat": "unknown"},
        "confidence": "low",
        "evidence": [{"factor_id": "missing_report", "value": 0.0,
                      "reading": "no factor report; all axes set to unknown."}],
        "conflicts": [],
    }


def recorded_rotation_json() -> dict:
    """``lane0.rotation`` attempt 1 — the single-key envelope, transcribed."""
    return {
        "rotation_report": {
            "mainlines": [],
            "confidence": "low",
            "narrative": "No factor report was supplied; no mainline is discernible.",
            "evidence_factor_ids": [],
            "unknown_reason": "the market factor report block is absent",
            "chain_nodes": [],
        }
    }


# --------------------------------------------------------------------------- #
# conformant model JSON (right field names, JSON-native types, no runtime      #
# fields) — what a model that KNOWS the schema can honestly produce            #
# --------------------------------------------------------------------------- #
def conformant_regime_json() -> dict:
    return {
        "trend": "unknown",
        "risk_state": "unknown",
        "heat_state": "unknown",
        "trend_probabilities": {"牛": 0.0, "熊": 0.0, "震荡": 0.0, "unknown": 1.0},
        "risk_probabilities": {"risk_on": 0.0, "risk_off": 0.0,
                               "neutral": 0.0, "unknown": 1.0},
        "heat_probabilities": {"normal": 0.0, "overheat": 0.0, "unknown": 1.0},
        "confidence": "low",
        "evidence": [{"factor_id": "breadth.ad_ratio", "value": 0.94,
                      "reading": "advance/decline 0.94"}],
        "conflicts": [],
        "drivers": ["insufficient_coverage"],
        "evidence_factor_ids": ["breadth.ad_ratio"],
        "narrative": "Coverage is thin; every axis carries its unknown mass.",
        "unknown_reason": "most of the battery is UNAVAILABLE this session",
    }


def conformant_rotation_json() -> dict:
    return {
        "mainlines": [],
        "confidence": "low",
        "conflicts": [],
        "narrative": "Themeless tape; no mainline the evidence supports.",
        "evidence_factor_ids": [],
        "unknown_reason": "the rotation family is UNAVAILABLE (archive-young)",
    }


def _normalize(payload, *, model, as_of=AS_OF, digest=DIGEST):
    return B.normalize_lane0_llm_output(
        payload, model=model, as_of=as_of, factor_report_digest=digest)


# =========================================================================== #
# 1 — WHY it could never work: strict python-mode validation vs model JSON     #
# =========================================================================== #
def test_strict_python_mode_alone_can_never_accept_model_json():
    # DigestModel is ConfigDict(extra="forbid", strict=True). In python mode
    # strict refuses every JSON-expressible conversion, so the executor's
    # `registry.validate_payload` could not have accepted ANY model's JSON —
    # not even a perfectly-shaped one. This is the deepest of the three causes.
    payload = dict(conformant_regime_json(),
                   as_of=AS_OF.isoformat(), factor_report_digest=DIGEST,
                   content_digest="0" * 64)
    with pytest.raises(Exception) as exc:
        RegimeReport.model_validate(payload)
    text = str(exc.value)
    assert "is_instance_of" in text or "Input should be an instance of" in text


# =========================================================================== #
# 2 — conformant JSON decodes, and the RUNTIME owns three fields               #
# =========================================================================== #
def test_a_conformant_regime_json_decodes_and_is_sealed_by_the_runtime():
    out = _normalize(conformant_regime_json(), model=RegimeReport)
    assert isinstance(out, RegimeReport)
    assert out.as_of == AS_OF                      # runtime's, never the model's
    assert out.factor_report_digest == DIGEST
    assert out.content_digest == out.semantic_digest()   # sealed, self-consistent
    assert out.confidence.value == "low"
    assert out.evidence[0].factor_id == "breadth.ad_ratio"


def test_a_conformant_rotation_json_decodes_and_is_sealed_by_the_runtime():
    out = _normalize(conformant_rotation_json(), model=RotationReport)
    assert isinstance(out, RotationReport)
    assert out.mainlines == ()
    assert out.as_of == AS_OF and out.factor_report_digest == DIGEST
    assert out.content_digest == out.semantic_digest()


def test_the_model_never_gets_to_set_a_runtime_owned_field():
    # a model that invents as_of / factor_report_digest / content_digest is
    # overridden, not trusted: those three are the runtime's by construction.
    payload = dict(conformant_regime_json(),
                   as_of="1999-01-01T00:00:00+00:00",
                   factor_report_digest="f" * 64, content_digest="0" * 64)
    out = _normalize(payload, model=RegimeReport)
    assert isinstance(out, RegimeReport)
    assert out.as_of == AS_OF
    assert out.factor_report_digest == DIGEST
    assert out.content_digest == out.semantic_digest()


# =========================================================================== #
# 3 — the envelope rule is NARROW and NAMED                                    #
# =========================================================================== #
def test_a_single_key_envelope_named_for_the_schema_is_unwrapped():
    out = _normalize({"rotation_report": conformant_rotation_json()},
                     model=RotationReport)
    assert isinstance(out, RotationReport)


def test_an_envelope_whose_key_is_not_the_schema_name_is_never_unwrapped():
    payload = {"result": conformant_rotation_json()}
    assert _normalize(payload, model=RotationReport) is payload


def test_a_two_key_object_is_never_unwrapped():
    payload = {"rotation_report": conformant_rotation_json(), "note": "hi"}
    assert _normalize(payload, model=RotationReport) is payload


def test_the_regime_envelope_key_is_the_regime_schema_name():
    out = _normalize({"regime_report": conformant_regime_json()}, model=RegimeReport)
    assert isinstance(out, RegimeReport)


# =========================================================================== #
# 4 — honesty red line: a real refusal STAYS a refusal                         #
# =========================================================================== #
def test_the_recorded_regime_json_is_still_refused_untouched():
    # wrong field names throughout (regime_type / risk / heat / modal, no
    # risk_state, no *_probabilities, no drivers/narrative). Nothing here is
    # repairable without inventing a judgement, so the payload comes back
    # IDENTICAL and the executor's own output_schema_invalid refusal stands.
    payload = recorded_regime_json()
    assert _normalize(payload, model=RegimeReport) is payload


def test_the_recorded_rotation_envelope_is_still_refused_for_its_extra_field():
    # the envelope unwraps, but the inner object carries a report-level
    # `chain_nodes` that RotationReport does not declare. Dropping an unknown
    # field would be exactly the permissive "dig around until something fits"
    # the contract forbids — so this stays a refusal.
    payload = recorded_rotation_json()
    assert _normalize(payload, model=RotationReport) is payload


def test_an_axis_that_does_not_sum_to_one_is_never_repaired():
    payload = dict(conformant_regime_json())
    payload["trend_probabilities"] = {"牛": 0.9, "熊": 0.0, "震荡": 0.0, "unknown": 1.0}
    assert _normalize(payload, model=RegimeReport) is payload


def test_a_non_mapping_payload_is_returned_untouched():
    assert _normalize(None, model=RegimeReport) is None
    assert _normalize("not json", model=RegimeReport) == "not json"


def test_an_already_typed_report_is_returned_untouched():
    typed = _normalize(conformant_regime_json(), model=RegimeReport)
    assert _normalize(typed, model=RegimeReport) is typed


def test_an_unknown_field_is_never_silently_dropped():
    payload = dict(conformant_regime_json(), extra_thought="I also think X")
    assert _normalize(payload, model=RegimeReport) is payload


def test_a_wrongly_typed_field_is_never_coerced_out_of_json_discipline():
    # strict JSON mode allows exactly what JSON can express (str→enum, list→
    # tuple, str→datetime) and nothing more: a probability written as a STRING
    # is a malformed answer, not a float to be salvaged.
    payload = dict(conformant_regime_json())
    payload["trend_probabilities"] = {"牛": "0.0", "熊": 0.0, "震荡": 0.0, "unknown": 1.0}
    assert _normalize(payload, model=RegimeReport) is payload

    nested = dict(conformant_regime_json())
    nested["evidence"] = [{"factor_id": "breadth.ad_ratio", "value": "0.94",
                           "reading": "advance/decline 0.94"}]
    assert _normalize(nested, model=RegimeReport) is nested


# =========================================================================== #
# 5 — the schema lookup is a closed two-entry allowlist                        #
# =========================================================================== #
def test_only_the_two_lane0_llm_reports_are_normalizable():
    assert B.lane0_llm_output_model("RegimeReport@1") is RegimeReport
    assert B.lane0_llm_output_model("RotationReport@1") is RotationReport
    assert B.lane0_llm_output_model("MarketFactorReport@1") is None
    assert B.lane0_llm_output_model("PlanDraft@1") is None


# =========================================================================== #
# 6 — the gateway wrapper                                                      #
# =========================================================================== #
class _FakeInner:
    """A gateway that answers with whatever it was handed (a real LLM's JSON)."""

    def __init__(self, payload):
        self._payload = payload
        self.closed = False
        self.calls = 0

    def invoke(self, request, *, prompt_assembly_ref):
        from guanlan_v2.orchestration.worker import ModelResult

        self.calls += 1
        return ModelResult(payload=self._payload, rendered_text="raw",
                           input_tokens=3, output_tokens=5)

    def close(self):
        self.closed = True


class _FakeCatalog:
    """Just enough catalog to answer "what is this worker's primary output schema"."""

    def __init__(self, schema_key):
        self._schema_key = schema_key

    def worker(self, worker_id):
        out = SimpleNamespace(
            name="primary", schema_ref=SimpleNamespace(key=self._schema_key))
        return SimpleNamespace(outputs=(out,))


class _FakeReport:
    as_of = AS_OF
    content_digest = DIGEST
    #: at least one OK/DEGRADED factor ⇒ an anchor IS available ⇒ ④ ≥1 stands.
    feature_vector = {"breadth.ad_ratio": 0.94}


class _NoCitableReport:
    """A COMMITTED report whose every factor is UNAVAILABLE (real digest, no value)."""

    as_of = AS_OF
    content_digest = DIGEST
    feature_vector: dict = {}


class _FakePool:
    def __init__(self, report):
        self._report = report

    def committed_output(self, node_id, key):
        if self._report is None:
            return None
        return SimpleNamespace(payload=self._report)


class _FakeRequest:
    def __init__(self, worker_id="market.regime"):
        self.prompt_record = SimpleNamespace(
            worker_id=worker_id, node_id="lane0.regime")


def _gateway(payload, *, report=_FakeReport(), schema_key="RegimeReport@1",
             as_of=None):
    inner = _FakeInner(payload)
    gw = B.Lane0OutputNormalizingGateway(
        inner=inner, catalog_runtime=_FakeCatalog(schema_key),
        pool=_FakePool(report), registry=None, as_of=as_of)
    return gw, inner


def test_the_gateway_normalizes_a_raw_json_completion():
    gw, inner = _gateway(conformant_regime_json())
    result = gw.invoke(_FakeRequest(), prompt_assembly_ref=None)
    assert isinstance(result.payload, RegimeReport)
    assert result.payload.as_of == AS_OF
    assert result.payload.factor_report_digest == DIGEST
    assert result.input_tokens == 3 and result.output_tokens == 5   # never lost
    assert inner.calls == 1


def test_the_gateway_passes_an_instance_payload_through_untouched():
    typed = _normalize(conformant_regime_json(), model=RegimeReport)
    gw, _inner = _gateway(typed)
    assert gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload is typed


def test_the_gateway_leaves_the_payload_alone_with_no_committed_factor_report():
    # no committed report AND no run as_of ⇒ nothing honest to stamp at all;
    # the executor refuses by schema, which is the truthful outcome.
    raw = conformant_regime_json()
    gw, _inner = _gateway(raw, report=None)
    assert gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload is raw


# --- 裁决 3 — "no report was bound" is a stamp, not an invented digest ------- #
def test_with_no_committed_report_the_runtime_stamps_the_no_report_marker():
    raw = dict(conformant_regime_json(), evidence=[], evidence_factor_ids=[],
               unknown_reason="no market_factor_report was bound to this read")
    gw, _inner = _gateway(raw, report=None, as_of=AS_OF)
    payload = gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload
    assert isinstance(payload, RegimeReport)
    assert payload.factor_report_digest == NO_FACTOR_REPORT_DIGEST
    assert payload.as_of == AS_OF
    assert payload.evidence == ()


def test_with_no_committed_report_an_invented_anchor_is_still_refused():
    # the exact live-run shape: the model cites `missing_report`. Under the
    # no-report stamp the contract forbids it, so the payload comes back
    # untouched and the executor's own output_schema_invalid stands.
    raw = conformant_regime_json()          # carries a breadth.ad_ratio anchor
    gw, _inner = _gateway(raw, report=None, as_of=AS_OF)
    assert gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload is raw


def test_a_committed_report_is_never_overwritten_by_the_no_report_marker():
    gw, _inner = _gateway(conformant_regime_json(), as_of=AS_OF)
    payload = gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload
    assert payload.factor_report_digest == DIGEST != NO_FACTOR_REPORT_DIGEST


# --- 裁决 3 · Option B — the reason for an empty citation list is MEASURED ---- #
def _empty_evidence_json(reason="coverage is thin, I chose not to cite"):
    return dict(conformant_regime_json(), evidence=[], evidence_factor_ids=[],
                unknown_reason=reason)


def test_a_bound_report_with_no_citable_reading_licenses_the_empty_citation_list():
    """The case the next live run walks into if the battery goes fully dark."""
    gw, _inner = _gateway(_empty_evidence_json(), report=_NoCitableReport(), as_of=AS_OF)
    payload = gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload
    assert isinstance(payload, RegimeReport)
    assert payload.evidence == () and payload.evidence_factor_ids == ()
    # the audit anchor is the REAL report digest — never the no-report marker.
    assert payload.factor_report_digest == DIGEST
    assert payload.unknown_reason == NO_CITABLE_READING_REASON


def test_the_model_never_authors_the_reason_that_licenses_an_empty_list():
    """The runtime OVERWRITES the model's prose with what it measured."""
    for prose in ("coverage is thin", NO_FACTOR_REPORT_REASON, "whatever I like"):
        gw, _inner = _gateway(_empty_evidence_json(prose), report=_NoCitableReport(),
                              as_of=AS_OF)
        payload = gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload
        assert isinstance(payload, RegimeReport), prose
        assert payload.unknown_reason == NO_CITABLE_READING_REASON, prose


def test_with_a_citable_reading_available_an_empty_citation_list_is_refused():
    """The ④ ≥1 rule, bit-for-bit: a report WITH a reading still demands an anchor.

    The runtime measured `feature_vector` non-empty, so it authors NO licence and
    the payload comes back untouched — the executor's own output_schema_invalid
    stands. "no evidence" can never become a phrase that escapes citing evidence
    the model DOES have.
    """
    raw = _empty_evidence_json(NO_CITABLE_READING_REASON)   # even the exact string
    gw, _inner = _gateway(raw, report=_FakeReport(), as_of=AS_OF)
    assert gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload is raw


def test_a_caller_cannot_mint_its_own_licence_for_an_empty_citation_list():
    # a wiring guard: only the two reviewed MEASUREMENTS license empty evidence.
    with pytest.raises(B.BootstrapRuntimeError):
        B.normalize_lane0_llm_output(
            _empty_evidence_json(), model=RegimeReport, as_of=AS_OF,
            factor_report_digest=DIGEST,
            empty_evidence_reason="runtime: because I said so")


def test_a_non_empty_citation_list_keeps_its_own_reason():
    # the ordinary degraded-coverage read is untouched by the reason vocabulary.
    gw, _inner = _gateway(conformant_regime_json(), report=_NoCitableReport(),
                          as_of=AS_OF)
    payload = gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload
    assert isinstance(payload, RegimeReport)
    assert payload.unknown_reason == "most of the battery is UNAVAILABLE this session"
    assert payload.evidence_factor_ids == ("breadth.ad_ratio",)


def test_the_gateway_ignores_a_worker_outside_the_two_lane0_reports():
    raw = conformant_regime_json()
    gw, _inner = _gateway(raw, schema_key="MarketFactorReport@1")
    assert gw.invoke(_FakeRequest(), prompt_assembly_ref=None).payload is raw


def test_the_gateway_close_delegates_to_the_inner_gateway():
    gw, inner = _gateway(conformant_regime_json())
    gw.close()
    assert inner.closed is True
