# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — DataSource ABI tests: method specs, requests, raw fetches,
concrete PIT record/batch/result families and the frozen dispatch carriers.

Covers brief items 1–6: strict params validation, request-digest stability /
sensitivity, RawFetch semantic vs audit identity, missing availability confined
to the raw candidate, per-method batch typing/ordering/duplicates, and the
registry-mediated named DataResult envelopes (no generic ``RowSet`` anywhere).

Run: ``pytest tests/orchestration/data/test_source.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.context import ClockSpec, DataContext
from guanlan_v2.orchestration.data.pit import RawRowCandidate
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.source import (
    DATA_METHOD_SCHEMAS,
    DataInvocationScope,
    DataMethodSpec,
    DataRequest,
    InstrumentNameRecord,
    InstrumentUniverseParams,
    NewsRecord,
    OHLCVDataResult,
    OHLCVRecord,
    OHLCVRows,
    ResolvedMethodRoute,
    RouteEntry,
    build_data_request,
    build_data_result,
    build_raw_fetch,
)
from guanlan_v2.orchestration.data.result import PitAudit, SourceAttempt
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, DataStatus
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    ExecutionEvidenceOrdinalToken,
    phase2_runtime_registry,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


def _ctx(**over) -> DataContext:
    fields = dict(
        as_of=AS_OF,
        clock=ClockSpec(as_of=AS_OF, timezone="Asia/Shanghai", calendar_id="cn_a_share"),
        mode=DataMode.ONLINE,
        backend=DataBackend.LIVE,
        strict_pit=False,
        calendar_id="cn_a_share",
        resolved_vendor_chains={"ohlcv": ("tushare", "akshare")},
        source_config_digest="a" * 64,
        source_registry_digest="b" * 64,
        routing_snapshot_digest="c" * 64,
        data_snapshot_id="snap-1",
        data_snapshot_content_digest="d" * 64,
        built_at=AS_OF,
    )
    fields.update(over)
    return DataContext(**fields)


def _method_spec(**over) -> DataMethodSpec:
    binding = next(b for b in DATA_METHOD_SCHEMAS if b.method_id == "ohlcv")
    fields = dict(
        method_id="ohlcv",
        method_version="1",
        category="prices",
        params_schema_ref=binding.params_schema_ref,
        batch_schema_ref=binding.batch_schema_ref,
        result_schema_ref=binding.result_schema_ref,
        optional=False,
        supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
        supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
        read_only=True,
        capability_ref=CapabilityRef(id="cap.data.ohlcv", version="1", content_digest="e" * 64),
        freshness_policy_ref=ContentRef(id="policy.freshness.default-elapsed", version="1",
                                        content_digest="f" * 64),
        renderer_ref=ContentRef(id="data.render.deterministic", version="1",
                                content_digest="a" * 64),
    )
    fields.update(over)
    return DataMethodSpec.build(**fields)


_SERIES_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-01T00:00:00+00:00",
    "end": "2026-07-15T00:00:00+00:00",
}


def _request(registry, *, ctx=None, params=None, spec=None, request_id="req-1"):
    return build_data_request(
        ctx if ctx is not None else _ctx(),
        method_spec=spec if spec is not None else _method_spec(),
        params=dict(_SERIES_PARAMS if params is None else params),
        registry=registry,
        request_id=request_id,
    )


def _cand(*, close=1.5, av=None, eff=None, ing=None, required=True) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload={
            "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
            "open": 1.0, "high": 2.0, "low": 0.5, "close": close, "volume": 100,
        },
        effective_at=eff if eff is not None else datetime(2026, 7, 1, tzinfo=UTC),
        available_at=av,
        ingested_at=ing if ing is not None else datetime(2026, 7, 2, tzinfo=UTC),
        required=required,
    )


def _record(*, close=1.5, eff=None) -> OHLCVRecord:
    return OHLCVRecord.from_candidate(
        _cand(close=close, av=datetime(2026, 7, 2, tzinfo=UTC), eff=eff)
    )


# --------------------------------------------------------------------------- #
# item 1 — params validated by the method's exact params_schema_ref            #
# --------------------------------------------------------------------------- #
def test_valid_params_build_a_request(registry):
    req = _request(registry)
    assert isinstance(req, DataRequest)
    assert req.method_spec_ref.id == "ohlcv"
    assert req.params_schema_ref == SchemaRef(name="InstrumentSeriesParams", version="1")


def test_params_extra_field_fails(registry):
    with pytest.raises(ValidationError):
        _request(registry, params={**_SERIES_PARAMS, "bogus": 1})


def test_params_bool_as_number_fails(registry):
    spec = _method_spec(
        method_id="verified_snapshot",
        params_schema_ref=SchemaRef(name="InstrumentUniverseParams", version="1"),
        batch_schema_ref=SchemaRef(name="VerifiedSnapshotRows", version="1"),
        result_schema_ref=SchemaRef(name="VerifiedSnapshotDataResult", version="1"),
    )
    with pytest.raises(ValidationError):
        _request(registry, spec=spec,
                 params={"as_of": "2026-07-16T07:00:00+00:00", "limit": True})


def test_params_naive_datetime_fails(registry):
    with pytest.raises(ValidationError):
        _request(registry, params={**_SERIES_PARAMS, "start": "2026-07-01T00:00:00"})


def test_params_non_finite_fails(registry):
    with pytest.raises(ValidationError):
        _request(registry, params={**_SERIES_PARAMS, "min_value": float("nan")})


def test_universe_params_reject_naive_and_accept_aware():
    InstrumentUniverseParams(as_of="2026-07-16T15:00:00+08:00")
    with pytest.raises(ValidationError):
        InstrumentUniverseParams(as_of="2026-07-16T15:00:00")


# --------------------------------------------------------------------------- #
# item 2 (brief step text) — request digest stability + sensitivity            #
# --------------------------------------------------------------------------- #
def test_reversed_params_key_order_is_digest_stable(registry):
    forward = dict(_SERIES_PARAMS)
    backward = dict(reversed(list(_SERIES_PARAMS.items())))
    assert list(forward) != list(backward)  # insertion order really differs
    assert _request(registry, params=forward).request_digest == \
        _request(registry, params=backward).request_digest


def test_request_id_and_snapshot_locator_are_audit_only(registry):
    a = _request(registry, request_id="req-a")
    b = _request(registry, request_id="req-b",
                 ctx=_ctx(data_snapshot_id="snap-relocated"))
    assert a.request_digest == b.request_digest
    assert "strict_pit" not in DataRequest.model_fields  # no caller-owned override


@pytest.mark.parametrize(
    "over",
    [
        {"mode": DataMode.PIT_REPLAY, "backend": DataBackend.PIT_STORE,
         "strict_pit": True, "vintage_manifest_digest": "9" * 64},
        {"data_snapshot_content_digest": "1" * 64},
        {"routing_snapshot_digest": "2" * 64},
        {"source_config_digest": "3" * 64},
        {"resolved_vendor_chains": {"ohlcv": ("akshare",)}},
    ],
)
def test_context_change_alters_request_digest(registry, over):
    assert _request(registry).request_digest != \
        _request(registry, ctx=_ctx(**over)).request_digest


def test_params_change_alters_request_digest(registry):
    other = {**_SERIES_PARAMS, "end": "2026-07-16T00:00:00+00:00"}
    assert _request(registry).request_digest != \
        _request(registry, params=other).request_digest


def test_schema_registry_change_alters_request_digest(registry):
    class _FakeRegistry:
        registry_digest = "5" * 64

        def validate_payload(self, ref, payload):
            return registry.validate_payload(ref, payload)

    assert _request(registry).request_digest != \
        _request(_FakeRegistry()).request_digest


def test_request_digest_declared_mismatch_rejected(registry):
    req = _request(registry)
    fields = {n: getattr(req, n) for n in type(req).model_fields if n != "request_digest"}
    with pytest.raises(ValidationError):
        DataRequest(**fields, request_digest="0" * 64)


def test_request_params_must_be_json_shaped(registry):
    req = _request(registry)
    fields = {n: getattr(req, n) for n in type(req).model_fields}
    fields["params"] = {"when": datetime(2026, 7, 1, tzinfo=UTC)}  # non-JSON value
    with pytest.raises(ValidationError):
        DataRequest(**fields)


# --------------------------------------------------------------------------- #
# item 2 — RawFetch semantic digest ignores only audit timing/provider ids      #
# --------------------------------------------------------------------------- #
def _fetch(**over):
    fields = dict(
        request_digest="a" * 64,
        source_ref=ContentRef(id="guanlan.datafeed", version="1", content_digest="b" * 64),
        capability_ref=CapabilityRef(id="cap.data.ohlcv", version="1", content_digest="c" * 64),
        candidates=(_cand(av=datetime(2026, 7, 2, tzinfo=UTC)),
                    _cand(close=1.6, av=datetime(2026, 7, 3, tzinfo=UTC))),
        subsource="main",
        fetched_at=datetime(2026, 7, 16, 7, 0, tzinfo=UTC),
        provider_request_id="prov-1",
    )
    fields.update(over)
    return build_raw_fetch(**fields)


def test_raw_fetch_audit_timing_is_semantically_invisible():
    a = _fetch()
    b = _fetch(fetched_at=datetime(2026, 7, 16, 9, 30, tzinfo=UTC),
               provider_request_id="prov-2")
    assert a.fetch_content_digest == b.fetch_content_digest
    assert a.fetch_audit_digest != b.fetch_audit_digest


def test_raw_fetch_semantic_dimensions_are_sensitive():
    base = _fetch()
    assert _fetch(request_digest="9" * 64).fetch_content_digest != base.fetch_content_digest
    assert _fetch(source_ref=ContentRef(id="other.vendor", version="1",
                                        content_digest="b" * 64)
                  ).fetch_content_digest != base.fetch_content_digest
    assert _fetch(capability_ref=CapabilityRef(id="cap.data.news", version="1",
                                               content_digest="c" * 64)
                  ).fetch_content_digest != base.fetch_content_digest
    assert _fetch(subsource="backup").fetch_content_digest != base.fetch_content_digest
    # candidate content and ORDER are semantic.
    reordered = _fetch(candidates=tuple(reversed(base.candidates)))
    assert reordered.fetch_content_digest != base.fetch_content_digest
    changed = _fetch(candidates=(base.candidates[0],
                                 _cand(close=9.9, av=datetime(2026, 7, 3, tzinfo=UTC))))
    assert changed.fetch_content_digest != base.fetch_content_digest


# --------------------------------------------------------------------------- #
# item 3 — missing availability lives ONLY in the raw candidate                #
# --------------------------------------------------------------------------- #
def test_missing_availability_survives_only_as_a_candidate():
    candidate = _cand(av=None)  # representable: the guard refuses it (Task 4)
    assert candidate.available_at is None
    with pytest.raises(ValidationError):
        OHLCVRecord.from_candidate(candidate)  # a registered record NEVER lacks it


def test_naive_availability_survives_only_as_a_candidate():
    candidate = _cand(av=datetime(2026, 7, 2))  # naive: classifiable, refusable
    assert candidate.available_at is not None
    with pytest.raises(ValidationError):
        OHLCVRecord.from_candidate(candidate)


# --------------------------------------------------------------------------- #
# item 4 — concrete batches: matching subtype, immutability, canonical order    #
# --------------------------------------------------------------------------- #
def test_batch_accepts_only_its_matching_record_subtype():
    news = NewsRecord.build(headline="hello", available_at=datetime(2026, 7, 1, tzinfo=UTC),
                            ingested_at=datetime(2026, 7, 1, tzinfo=UTC))
    with pytest.raises(ValidationError):
        OHLCVRows(rows=(news,), batch_content_digest="0" * 64)


def test_batch_is_immutable_and_canonically_ordered():
    r1 = _record(eff=datetime(2026, 6, 30, tzinfo=UTC))
    r2 = _record(close=1.7, eff=datetime(2026, 7, 1, tzinfo=UTC))
    shuffled = OHLCVRows.build([r2, r1])
    ordered = OHLCVRows.build([r1, r2])
    assert shuffled.batch_content_digest == ordered.batch_content_digest
    assert [r.effective_at for r in shuffled.rows] == sorted(r.effective_at for r in shuffled.rows)
    with pytest.raises(ValidationError):
        shuffled.rows = ()  # frozen


def test_batch_rejects_unsorted_and_duplicate_rows():
    r1 = _record(eff=datetime(2026, 6, 30, tzinfo=UTC))
    r2 = _record(close=1.7, eff=datetime(2026, 7, 1, tzinfo=UTC))
    good = OHLCVRows.build([r1, r2])
    with pytest.raises(ValidationError):
        OHLCVRows(rows=(r2, r1), batch_content_digest=good.batch_content_digest)
    # duplicate method identity (same symbol + period) is rejected even with
    # different payload content.
    dup = _record(close=9.9, eff=datetime(2026, 6, 30, tzinfo=UTC))
    with pytest.raises(ValidationError):
        OHLCVRows.build([r1, dup])


def test_record_pit_metadata_is_never_a_single_top_level_timestamp():
    for binding in DATA_METHOD_SCHEMAS:
        fields = binding.record_cls.model_fields
        for required in ("available_at", "ingested_at", "revision_id", "content_digest"):
            assert required in fields, (binding.record_cls.__name__, required)


# --------------------------------------------------------------------------- #
# item 5 — named envelopes through DataResult.build; generics rejected          #
# --------------------------------------------------------------------------- #
def _audit(mode=DataMode.ONLINE) -> PitAudit:
    return PitAudit(mode=mode, as_of=AS_OF, rows_seen=1, rows_returned=1,
                    future_rows=0, missing_available_at_rows=0, guard_result="passed")


def _attempt() -> SourceAttempt:
    return SourceAttempt(vendor="tushare", configured=True, outcome="success",
                         started_at=AS_OF, finished_at=AS_OF)


def _ok_result(registry, spec=None, data=None):
    return build_data_result(
        spec if spec is not None else _method_spec(),
        registry=registry,
        id="res-1",
        request_digest="a" * 64,
        status=DataStatus.OK,
        data=data if data is not None else OHLCVRows.build([_record()]),
        resolved_vendor_chain=("tushare",),
        source_config_digest="a" * 64,
        fetched_at=AS_OF,
        attempts=(_attempt(),),
        pit_audit=_audit(),
    )


def test_every_method_builds_its_named_envelope(registry):
    for binding in DATA_METHOD_SCHEMAS:
        spec = _method_spec(
            method_id=binding.method_id, category=binding.category,
            params_schema_ref=binding.params_schema_ref,
            batch_schema_ref=binding.batch_schema_ref,
            result_schema_ref=binding.result_schema_ref,
        )
        result = build_data_result(
            spec, registry=registry, id="r", request_digest="a" * 64,
            status=DataStatus.NO_DATA, data=None, resolved_vendor_chain=("tushare",),
            source_config_digest="a" * 64, fetched_at=AS_OF,
            attempts=(SourceAttempt(vendor="tushare", configured=True, outcome="no_data",
                                    started_at=AS_OF, finished_at=AS_OF),),
            pit_audit=PitAudit(mode=DataMode.ONLINE, as_of=AS_OF, rows_seen=0,
                               rows_returned=0, future_rows=0,
                               missing_available_at_rows=0, guard_result="passed"),
        )
        assert type(result).__name__ == binding.result_cls.__name__
        assert result.data is None  # NO_DATA carries no consumable payload


def test_ok_result_round_trips_through_the_registry(registry):
    result = _ok_result(registry)
    assert isinstance(result, OHLCVDataResult)
    ref = SchemaRef(name="OHLCVDataResult", version="1")
    restored = registry.validate_payload(ref, result.model_dump())
    assert restored == result


def test_wrong_batch_type_for_the_envelope_is_rejected(registry):
    name_row = InstrumentNameRecord.build(
        symbol=Symbol(code="600519", exchange="SH", board="main"), name="贵州茅台",
        available_at=datetime(2026, 7, 1, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 1, tzinfo=UTC))
    from guanlan_v2.orchestration.data.source import InstrumentNameRows

    wrong_batch = InstrumentNameRows.build([name_row])
    with pytest.raises(ValueError, match="requires a OHLCVRows batch"):
        _ok_result(registry, data=wrong_batch)


def test_generic_mapping_payload_is_rejected(registry):
    with pytest.raises(ValueError, match="requires a OHLCVRows batch"):
        _ok_result(registry, data={"rows": [{"close": 1.5}]})


def test_unregistered_result_schema_is_rejected(registry):
    spec = _method_spec(result_schema_ref=SchemaRef(name="DataResult", version="1"))
    with pytest.raises(Exception):
        _ok_result(registry, spec=spec)


def test_non_envelope_registered_schema_is_rejected(registry):
    spec = _method_spec(result_schema_ref=SchemaRef(name="RenderedDataBlock", version="1"))
    with pytest.raises(ValueError, match="named DataResult envelope"):
        _ok_result(registry, spec=spec)


def test_declared_digest_mismatch_rejected_on_load(registry):
    result = _ok_result(registry)
    payload = result.model_dump()
    payload["content_digest"] = "0" * 64
    with pytest.raises(ValidationError):
        registry.validate_payload(SchemaRef(name="OHLCVDataResult", version="1"), payload)


# --------------------------------------------------------------------------- #
# frozen dispatch carriers — token discipline                                  #
# --------------------------------------------------------------------------- #
def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=evidence)


def _route(entries: int) -> ResolvedMethodRoute:
    return ResolvedMethodRoute(
        method_ref=ContentRef(id="ohlcv", version="1", content_digest="a" * 64),
        entries=tuple(
            RouteEntry(
                source_ref=ContentRef(id=f"vendor{i}", version="1", content_digest="b" * 64),
                capability_ref=CapabilityRef(id="cap.data.ohlcv", version="1",
                                             content_digest="c" * 64),
            )
            for i in range(entries)
        ),
        route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                    content_digest="d" * 64),
    )


def test_invocation_scope_requires_one_distinct_token_per_route_entry():
    scope = DataInvocationScope(
        plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
        operation_token=_token(1), attempt_tokens=(_token(2), _token(3)),
        frozen_route=_route(2), invocation_mode="cache_or_invoke",
        catalog_digest="b" * 64, schema_registry_digest="c" * 64)
    assert len(scope.attempt_tokens) == 2
    with pytest.raises(ValidationError):  # too few tokens for the route
        DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1), attempt_tokens=(_token(2),),
            frozen_route=_route(2), invocation_mode="cache_or_invoke",
            catalog_digest="b" * 64, schema_registry_digest="c" * 64)
    with pytest.raises(ValidationError):  # duplicate / reused attempt tokens
        DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1), attempt_tokens=(_token(2), _token(2)),
            frozen_route=_route(2), invocation_mode="cache_or_invoke",
            catalog_digest="b" * 64, schema_registry_digest="c" * 64)


def test_route_must_be_non_empty():
    with pytest.raises(ValidationError):
        _route(0)


# --------------------------------------------------------------------------- #
# RenderedDataBlock — deterministic, spec-owned renderer, no detached pair      #
# --------------------------------------------------------------------------- #
def _typed_ref(result) -> "TypedPayloadRef":
    from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

    return TypedPayloadRef(
        schema_ref=SchemaRef(name=type(result).__name__, version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                               content_digest=result.content_digest))


def test_rendered_block_is_deterministic_and_untrusted(registry):
    from guanlan_v2.orchestration.data.render import build_rendered_data_block

    spec = _method_spec()
    result = _ok_result(registry, spec=spec)
    ref = _typed_ref(result)
    a = build_rendered_data_block(method_spec=spec, result=result, result_ref=ref,
                                  registry=registry)
    b = build_rendered_data_block(method_spec=spec, result=result, result_ref=ref,
                                  registry=registry)
    assert a.block_digest == b.block_digest  # pure function of the result
    assert a.trust == "untrusted_data"
    assert a.renderer_ref == spec.renderer_ref  # renderer comes ONLY from the spec
    assert a.rendered_from_payload_digest == result.content_digest
    assert "build_rendered_data_block" in dir(__import__(
        "guanlan_v2.orchestration.data.render", fromlist=["x"]))


def test_rendered_block_rejects_a_detached_schema_ref_pair(registry):
    from guanlan_v2.orchestration.data.render import build_rendered_data_block
    from guanlan_v2.orchestration.refs import PayloadRef, TypedPayloadRef

    spec = _method_spec()
    result = _ok_result(registry, spec=spec)
    detached = TypedPayloadRef(
        schema_ref=SchemaRef(name="NewsDataResult", version="1"),  # wrong schema
        payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                               content_digest=result.content_digest))
    with pytest.raises(ValueError, match="schema"):
        build_rendered_data_block(method_spec=spec, result=result,
                                  result_ref=detached, registry=registry)
    drifted = TypedPayloadRef(
        schema_ref=SchemaRef(name="OHLCVDataResult", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="obj-1",
                               content_digest="0" * 64))  # wrong content digest
    with pytest.raises(ValueError, match="digest"):
        build_rendered_data_block(method_spec=spec, result=result,
                                  result_ref=drifted, registry=registry)


def test_rendered_block_has_no_caller_renderer_parameter():
    from guanlan_v2.orchestration.data.render import build_rendered_data_block
    import inspect

    params = inspect.signature(build_rendered_data_block).parameters
    assert "renderer_ref" not in params  # caller/model input cannot select a renderer
