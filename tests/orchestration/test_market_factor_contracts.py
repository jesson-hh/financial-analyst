# -*- coding: utf-8 -*-
"""Phase 5 · Task 1 — market-factor contract models + frozen v1 battery.

Written test-first (RED until ``guanlan_v2.orchestration.market.factors`` exists).
Covers the five contract models (``MarketFactorPoint`` / ``MarketFactorDefinition`` /
``MarketFactorSetSpec`` / ``MarketFactorValue`` / ``MarketFactorReport``), the
①§2 three-state status matrix, the spec field-presence invariant, the
report cross-consistency validators, the hand-frozen 19-id v1 factor set golden
(``market_factor_set_v1.json``, never regenerated from test code), and the
semantic-digest differential (param change moves, payload relocation does not).

Run from repo root: ``pytest tests/orchestration/test_market_factor_contracts.py -v``
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.refs import PayloadRef
from guanlan_v2.orchestration.market.factors import (
    CoverageSummary,
    FactorProvenance,
    FactorSummary,
    MarketFactorDefinition,
    MarketFactorPoint,
    MarketFactorReport,
    MarketFactorSetSpec,
    MarketFactorValue,
    assemble_market_factor_report,
    build_market_factor_set_v1,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)

GOLDEN = Path(__file__).resolve().parent / "golden" / "market_factor_set_v1.json"

#: the R3-mandated 19-id v1 battery, in canonical (sorted-by-factor_id) order.
EXPECTED_FACTOR_IDS: tuple[str, ...] = (
    "breadth.ad_ratio",
    "breadth.break_rate",
    "breadth.divergence",
    "breadth.ladder_height",
    "breadth.limit_up_ema",
    "breadth.nhnl",
    "breadth.promotion_rate",
    "flow.main_pct",
    "flow.northbound",
    "rot.diffusion",
    "rot.dispersion",
    "rot.flow_streak",
    "rot.hhi",
    "rot.ladder_theme",
    "rot.leader_persist",
    "rot.theme_burst",
    "temp.astock",
    "val.pct",
    "vol.rv",
)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _prov() -> FactorProvenance:
    return FactorProvenance(
        sources=("engine.daily_panel",),
        snapshot_refs=(
            PayloadRef(namespace="main", object_id="obj-1", content_digest="ab" * 32),
        ),
    )


def _ok_value(
    factor_id: str = "breadth.ad_ratio",
    family: str = "breadth",
    *,
    aux: dict[str, float] | None = None,
) -> MarketFactorValue:
    pts = (
        MarketFactorPoint(date="2026-07-15", value=0.5, aux=aux or {}),
        MarketFactorPoint(date="2026-07-16", value=0.7, aux=aux or {}),
    )
    return MarketFactorValue.build(
        factor_id=factor_id,
        definition_version="1",
        family=family,
        value=0.7,
        params={},
        universe="all_a",
        effective_at=AS_OF,
        available_at=AS_OF,
        status="OK",
        coverage=1.0,
        missing_policy="computable from the engine daily panel",
        series=pts,
        summary=FactorSummary(latest=0.7, chg_5d=0.2, chg_20d=None, pct_250d=None),
        n_days=2,
        first_date="2026-07-15",
        provenance=_prov(),
    )


def _degraded_value(factor_id: str = "breadth.limit_up_ema", family: str = "breadth") -> MarketFactorValue:
    pts = (MarketFactorPoint(date="2026-07-16", value=3.0),)
    return MarketFactorValue.build(
        factor_id=factor_id,
        definition_version="1",
        family=family,
        value=3.0,
        params={},
        universe="all_a",
        effective_at=AS_OF,
        available_at=AS_OF,
        status="DEGRADED",
        coverage=0.4,
        missing_policy="young archive; coverage < 1",
        series=pts,
        summary=None,
        n_days=1,
        first_date="2026-07-16",
        provenance=_prov(),
        reason="archive-young series (observed 2 of 5 sessions)",
    )


def _unavailable_value(factor_id: str = "flow.northbound", family: str = "flow") -> MarketFactorValue:
    return MarketFactorValue.build(
        factor_id=factor_id,
        definition_version="1",
        family=family,
        value=None,
        params={},
        universe="all_a",
        effective_at=AS_OF,
        available_at=AS_OF,
        status="UNAVAILABLE",
        coverage=0.0,
        missing_policy="no daily northbound store; 北向 2024-08 停披",
        series=(),
        summary=None,
        n_days=0,
        first_date=None,
        provenance=_prov(),
        reason="required input 'north_net' is missing (no daily store)",
    )


def _values_for_full_battery() -> tuple[MarketFactorValue, ...]:
    """One value per v1 definition: two OK, one DEGRADED, the rest UNAVAILABLE."""
    spec = build_market_factor_set_v1()
    out: list[MarketFactorValue] = []
    for d in spec.definitions:
        fam = d.factor_id.split(".")[0]
        if d.factor_id == "breadth.ad_ratio":
            out.append(_ok_value("breadth.ad_ratio", "breadth"))
        elif d.factor_id == "vol.rv":
            out.append(_ok_value("vol.rv", "vol"))
        elif d.factor_id == "breadth.limit_up_ema":
            out.append(_degraded_value("breadth.limit_up_ema", "breadth"))
        else:
            out.append(_unavailable_value(d.factor_id, fam))
    return tuple(out)


# --------------------------------------------------------------------------- #
# construction / frozen / extra-forbid / strict types                          #
# --------------------------------------------------------------------------- #
def test_point_constructs_and_is_frozen():
    p = MarketFactorPoint(date="2026-07-16", value=1.5, aux={"ma5": 1.2})
    assert p.value == 1.5 and p.aux["ma5"] == 1.2
    with pytest.raises(ValidationError):
        p.value = 2.0  # frozen


def test_all_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        MarketFactorPoint(date="2026-07-16", value=1.0, junk=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        MarketFactorDefinition(
            factor_id="breadth.nhnl",
            definition_version="1",
            params={},
            required_inputs=("closes_panel",),
            min_history_sessions=61,
            aux_keys=(),
            junk=1,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        FactorSummary(latest=1.0, chg_5d=None, chg_20d=None, pct_250d=None, junk=1)  # type: ignore[call-arg]


def test_strict_types_reject_bool_as_float():
    with pytest.raises(ValidationError):
        MarketFactorPoint(date="2026-07-16", value=True)  # bool is not a float


def test_strict_types_reject_naive_datetime():
    # via build path the naive datetime is rejected at construction
    with pytest.raises(ValidationError):
        MarketFactorValue.build(
            factor_id="breadth.ad_ratio",
            definition_version="1",
            family="breadth",
            value=0.7,
            params={},
            universe="all_a",
            effective_at=datetime(2026, 7, 16),  # naive # noqa: DTZ001
            available_at=AS_OF,
            status="OK",
            coverage=1.0,
            missing_policy="x",
            series=(MarketFactorPoint(date="2026-07-16", value=0.7),),
            summary=None,
            n_days=1,
            first_date="2026-07-16",
            provenance=_prov(),
        )


# --------------------------------------------------------------------------- #
# MarketFactorPoint date regex + strictly-increasing series dates              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["2026/07/16", "20260716", "2026-7-16", "not-a-date", "2026-13-40"])
def test_point_date_regex_rejects_malformed(bad):
    with pytest.raises(ValidationError):
        MarketFactorPoint(date=bad, value=1.0)


def test_series_non_increasing_dates_rejected():
    pts = (
        MarketFactorPoint(date="2026-07-16", value=1.0),
        MarketFactorPoint(date="2026-07-15", value=2.0),  # out of order
    )
    with pytest.raises(ValidationError):
        MarketFactorValue.build(
            factor_id="breadth.ad_ratio",
            definition_version="1",
            family="breadth",
            value=2.0,
            params={},
            universe="all_a",
            effective_at=AS_OF,
            available_at=AS_OF,
            status="OK",
            coverage=1.0,
            missing_policy="x",
            series=pts,
            summary=None,
            n_days=2,
            first_date="2026-07-15",
            provenance=_prov(),
        )


def test_series_duplicate_dates_rejected():
    pts = (
        MarketFactorPoint(date="2026-07-16", value=1.0),
        MarketFactorPoint(date="2026-07-16", value=2.0),
    )
    with pytest.raises(ValidationError):
        MarketFactorValue.build(
            factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
            value=2.0, params={}, universe="all_a", effective_at=AS_OF, available_at=AS_OF,
            status="OK", coverage=1.0, missing_policy="x", series=pts, summary=None,
            n_days=2, first_date="2026-07-16", provenance=_prov(),
        )


def test_series_cannot_exceed_60_points():
    from datetime import timedelta

    days = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(61)]
    pts = tuple(
        MarketFactorPoint(date=d.strftime("%Y-%m-%d"), value=float(i)) for i, d in enumerate(days)
    )
    with pytest.raises(ValidationError):
        MarketFactorValue.build(
            factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
            value=60.0, params={}, universe="all_a", effective_at=AS_OF, available_at=AS_OF,
            status="OK", coverage=1.0, missing_policy="x", series=pts, summary=None,
            n_days=61, first_date="2026-01-01", provenance=_prov(),
        )


# --------------------------------------------------------------------------- #
# aux keys restricted to the definition's aux_keys — enforced at assembly       #
# --------------------------------------------------------------------------- #
def test_aux_keys_outside_definition_rejected_at_assembly():
    # breadth.nhnl allows only nhnl20/nhnl60; inject a stray key.
    bad = _ok_value("breadth.nhnl", "breadth", aux={"bogus": 1.0})
    values = tuple(
        bad if v.factor_id == "breadth.nhnl" else v for v in _values_for_full_battery()
    )
    spec = build_market_factor_set_v1()
    with pytest.raises(ValueError):
        assemble_market_factor_report(
            spec=spec, as_of=AS_OF, clock_mode="eod",
            universe_registry_version="ureg-1", values=values,
            data_snapshot_hash="cd" * 32,
        )


def test_aux_keys_within_definition_accepted_at_assembly():
    ok = _ok_value("breadth.nhnl", "breadth", aux={"nhnl20": 0.1, "nhnl60": 0.2})
    values = tuple(
        ok if v.factor_id == "breadth.nhnl" else v for v in _values_for_full_battery()
    )
    spec = build_market_factor_set_v1()
    report = assemble_market_factor_report(
        spec=spec, as_of=AS_OF, clock_mode="eod",
        universe_registry_version="ureg-1", values=values,
        data_snapshot_hash="cd" * 32,
    )
    assert report.content_digest


# --------------------------------------------------------------------------- #
# status matrix (①§2 three states)                                             #
# --------------------------------------------------------------------------- #
def test_legal_ok_degraded_unavailable_accepted():
    assert _ok_value().status == "OK"
    assert _degraded_value().status == "DEGRADED"
    assert _unavailable_value().status == "UNAVAILABLE"


def test_summary_pct250d_none_accepted():
    v = _ok_value()
    assert v.summary is not None and v.summary.pct_250d is None


def _mk(**over):
    base = dict(
        factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
        value=0.7, params={}, universe="all_a", effective_at=AS_OF, available_at=AS_OF,
        status="OK", coverage=1.0, missing_policy="x",
        series=(MarketFactorPoint(date="2026-07-16", value=0.7),),
        summary=None, n_days=1, first_date="2026-07-16", provenance=_prov(), reason=None,
    )
    base.update(over)
    return lambda: MarketFactorValue.build(**base)


@pytest.mark.parametrize(
    "maker",
    [
        _mk(status="OK", series=()),                          # OK + empty series
        _mk(status="OK", value=None),                         # OK + value None
        _mk(status="OK", coverage=0.9),                       # OK + coverage < 1
        _mk(status="OK", reason="nope"),                      # OK with reason
        _mk(status="DEGRADED", series=(), value=None, coverage=0.0, reason="r", n_days=0, first_date=None),  # DEGRADED empty
        _mk(status="DEGRADED", coverage=0.5, reason=None),    # DEGRADED without reason
        _mk(status="UNAVAILABLE", value=None, series=(MarketFactorPoint(date="2026-07-16", value=0.7),), coverage=0.0, reason="r", n_days=1, first_date=None),  # UNAVAILABLE with points
        _mk(status="UNAVAILABLE", series=(), coverage=0.0, reason="r", n_days=0, first_date=None),  # UNAVAILABLE non-None value
        _mk(status="UNAVAILABLE", value=None, series=(), coverage=0.3, reason="r", n_days=0, first_date=None),  # UNAVAILABLE coverage>0
        _mk(status="UNAVAILABLE", value=None, series=(), coverage=0.0, reason=None, n_days=0, first_date=None),  # UNAVAILABLE without reason
        _mk(status="UNAVAILABLE", value=None, series=(), coverage=0.0, reason="r", summary=FactorSummary(latest=1.0, chg_5d=None, chg_20d=None, pct_250d=None), n_days=0, first_date=None),  # UNAVAILABLE with summary
    ],
)
def test_illegal_status_combinations_rejected(maker):
    with pytest.raises(ValidationError):
        maker()


def test_status_outside_literal_rejected():
    with pytest.raises(ValidationError):
        _mk(status="PARTIAL")()


def test_ok_value_must_equal_latest_point():
    with pytest.raises(ValidationError):
        _mk(status="OK", value=0.1)()  # value 0.1 != last point 0.7


# --------------------------------------------------------------------------- #
# spec field-presence invariant (line-378 + §8 names)                          #
# --------------------------------------------------------------------------- #
def test_market_factor_value_spec_fields_present_under_exact_names():
    required = {
        "factor_id", "definition_version", "value", "params", "universe",
        "frequency", "effective_at", "available_at", "coverage", "status",
        "missing_policy", "content_digest",
    }
    assert required <= set(MarketFactorValue.model_fields)
    # ① renames / additions also present under their names.
    for name in ("series", "summary", "n_days", "first_date", "reason", "family", "provenance"):
        assert name in MarketFactorValue.model_fields


# --------------------------------------------------------------------------- #
# MarketFactorSetSpec sorted / dup-free / non-empty                            #
# --------------------------------------------------------------------------- #
def _defn(fid: str) -> MarketFactorDefinition:
    return MarketFactorDefinition(
        factor_id=fid, definition_version="1", params={},
        required_inputs=("updown",), min_history_sessions=5, aux_keys=(),
    )


def test_setspec_rejects_empty_definitions():
    with pytest.raises(ValidationError):
        MarketFactorSetSpec.build(
            factor_set_version="x", feature_schema_version="x", universe="all_a",
            definitions=(),
        )


def test_setspec_rejects_unsorted_definitions():
    with pytest.raises(ValidationError):
        MarketFactorSetSpec.build(
            factor_set_version="x", feature_schema_version="x", universe="all_a",
            definitions=(_defn("breadth.z"), _defn("breadth.a")),
        )


def test_setspec_rejects_duplicate_factor_ids():
    with pytest.raises(ValidationError):
        MarketFactorSetSpec.build(
            factor_set_version="x", feature_schema_version="x", universe="all_a",
            definitions=(_defn("breadth.a"), _defn("breadth.a")),
        )


def test_definition_rejects_unsorted_required_inputs():
    with pytest.raises(ValidationError):
        MarketFactorDefinition(
            factor_id="rot.diffusion", definition_version="1", params={},
            required_inputs=("sector_flows", "closes_panel"), min_history_sessions=5, aux_keys=(),
        )


def test_definition_rejects_duplicate_aux_keys():
    with pytest.raises(ValidationError):
        MarketFactorDefinition(
            factor_id="breadth.ad_ratio", definition_version="1", params={},
            required_inputs=("updown",), min_history_sessions=25, aux_keys=("ma5", "ma5"),
        )


# --------------------------------------------------------------------------- #
# The frozen v1 golden — reproduced, never regenerated                          #
# --------------------------------------------------------------------------- #
def test_v1_has_exactly_19_ids_verbatim():
    spec = build_market_factor_set_v1()
    ids = tuple(d.factor_id for d in spec.definitions)
    assert ids == EXPECTED_FACTOR_IDS
    assert len(ids) == 19


def test_golden_reproduces_content_digest():
    assert GOLDEN.exists(), f"missing golden: {GOLDEN}"
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    spec = build_market_factor_set_v1()
    # the golden's stored digest is REAL and reproduced by the constructor
    assert spec.content_digest == doc["content_digest"]
    assert spec.content_digest != "0" * 64
    assert tuple(doc["factor_ids"]) == EXPECTED_FACTOR_IDS
    # canonical json byte-identical, and the digest is genuinely sha256(canonical)
    assert doc["canonical_json"] == canonical_json(spec, projection="semantic")
    assert hashlib.sha256(doc["canonical_json"].encode("utf-8")).hexdigest() == doc["content_digest"]


def test_golden_reloads_and_self_verifies():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    reloaded = MarketFactorSetSpec.model_validate_json(json.dumps(doc["spec"]))
    assert reloaded.content_digest == doc["content_digest"]
    # an independent rebuild via build() reproduces the frozen seal
    rebuilt = MarketFactorSetSpec.build(
        factor_set_version=reloaded.factor_set_version,
        feature_schema_version=reloaded.feature_schema_version,
        universe=reloaded.universe,
        frequency=reloaded.frequency,
        definitions=reloaded.definitions,
    )
    assert rebuilt.content_digest == doc["content_digest"]


def test_golden_meta_matches_the_reviewed_battery():
    spec = build_market_factor_set_v1()
    assert spec.factor_set_version == "mfs-v1"
    assert spec.feature_schema_version == "mfs-v1"
    assert spec.universe == "all_a"
    assert spec.frequency == "day"


def test_v1_key_battery_rows_frozen():
    by_id = {d.factor_id: d for d in build_market_factor_set_v1().definitions}
    assert by_id["breadth.ad_ratio"].params["slope_window"] == 20.0
    assert by_id["breadth.ad_ratio"].min_history_sessions == 25
    assert by_id["breadth.divergence"].params["z_window"] == 250.0
    assert by_id["breadth.divergence"].min_history_sessions == 271
    assert by_id["breadth.promotion_rate"].params["numerator"] == "limit_days>=2"
    assert by_id["rot.dispersion"].params["dispersion"] == "cross_sectional_std"
    assert by_id["val.pct"].params["pct_years"] == 5.0
    assert by_id["val.pct"].min_history_sessions == 1150
    assert set(by_id["flow.northbound"].aux_keys) == {"cum5", "cum20", "slope", "pct250"}
    assert by_id["breadth.break_rate"].params == {}
    assert by_id["breadth.break_rate"].aux_keys == ()


# --------------------------------------------------------------------------- #
# MarketFactorReport cross-consistency (both directions)                        #
# --------------------------------------------------------------------------- #
def test_assembled_report_is_self_consistent():
    spec = build_market_factor_set_v1()
    values = _values_for_full_battery()
    report = assemble_market_factor_report(
        spec=spec, as_of=AS_OF, clock_mode="eod",
        universe_registry_version="ureg-1", values=values, data_snapshot_hash="cd" * 32,
    )
    assert report.battery_digest == spec.content_digest
    assert report.coverage_summary.n_ok == 2
    assert report.coverage_summary.n_degraded == 1
    assert report.coverage_summary.n_unavailable == 16
    # feature_vector = latest per OK/DEGRADED; missing_features = UNAVAILABLE ids
    assert set(report.feature_vector) == {"breadth.ad_ratio", "vol.rv", "breadth.limit_up_ema"}
    assert set(report.missing_features).isdisjoint(report.feature_vector)
    assert report.unavailable_factor_ids == report.missing_features
    # report coverage = mean of per-factor coverage
    expected = sum(v.coverage for v in report.values) / len(report.values)
    assert report.coverage == expected


def _legal_report_fields():
    spec = build_market_factor_set_v1()
    values = tuple(sorted(_values_for_full_battery(), key=lambda v: v.factor_id))
    nonun = [v for v in values if v.status != "UNAVAILABLE"]
    missing = tuple(sorted(v.factor_id for v in values if v.status == "UNAVAILABLE"))
    return dict(
        as_of=AS_OF, clock_mode="eod", universe_registry_version="ureg-1",
        factor_set_version=spec.factor_set_version, battery_digest=spec.content_digest,
        feature_schema_version=spec.feature_schema_version, universe=spec.universe,
        values=values, data_snapshot_hash="cd" * 32,
        coverage=sum(v.coverage for v in values) / len(values),
        coverage_summary=CoverageSummary(n_ok=2, n_degraded=1, n_unavailable=16),
        feature_vector={v.factor_id: v.value for v in nonun},
        feature_coverage={v.factor_id: v.coverage for v in nonun},
        missing_features=missing, unavailable_factor_ids=missing,
    )


def test_report_feature_vector_key_for_unavailable_rejected():
    fields = _legal_report_fields()
    fv = dict(fields["feature_vector"])
    fv["flow.northbound"] = 1.0  # an UNAVAILABLE id
    with pytest.raises(ValidationError):
        MarketFactorReport.build(**{**fields, "feature_vector": fv})


def test_report_ok_factor_absent_from_feature_vector_rejected():
    fields = _legal_report_fields()
    fv = dict(fields["feature_vector"])
    del fv["breadth.ad_ratio"]  # drop an OK factor
    with pytest.raises(ValidationError):
        MarketFactorReport.build(**{**fields, "feature_vector": fv})


def test_report_missing_features_mismatch_rejected():
    fields = _legal_report_fields()
    with pytest.raises(ValidationError):
        MarketFactorReport.build(**{**fields, "missing_features": fields["missing_features"][:-1]})


def test_report_coverage_deviation_rejected():
    fields = _legal_report_fields()
    with pytest.raises(ValidationError):
        MarketFactorReport.build(**{**fields, "coverage": fields["coverage"] + 0.1})


def test_report_coverage_summary_deviation_rejected():
    fields = _legal_report_fields()
    with pytest.raises(ValidationError):
        MarketFactorReport.build(
            **{**fields, "coverage_summary": CoverageSummary(n_ok=3, n_degraded=0, n_unavailable=16)}
        )


def test_report_ids_not_matching_bound_set_rejected():
    spec = build_market_factor_set_v1()
    values = _values_for_full_battery()[:-1]  # drop one id → mismatch
    with pytest.raises(ValueError):
        assemble_market_factor_report(
            spec=spec, as_of=AS_OF, clock_mode="eod",
            universe_registry_version="ureg-1", values=values, data_snapshot_hash="cd" * 32,
        )


def test_report_declared_digest_mismatch_rejected():
    fields = _legal_report_fields()
    with pytest.raises(ValidationError):
        MarketFactorReport(**fields, content_digest="0" * 64)


# --------------------------------------------------------------------------- #
# semantic-digest differentials — param change moves, relocation does not        #
# --------------------------------------------------------------------------- #
def test_param_change_moves_semantic_digest():
    a = MarketFactorValue.build(
        factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
        value=0.7, params={"ma_short": 5.0}, universe="all_a", effective_at=AS_OF,
        available_at=AS_OF, status="OK", coverage=1.0, missing_policy="x",
        series=(MarketFactorPoint(date="2026-07-16", value=0.7),), summary=None,
        n_days=1, first_date="2026-07-16", provenance=_prov(),
    )
    b = MarketFactorValue.build(
        factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
        value=0.7, params={"ma_short": 6.0}, universe="all_a", effective_at=AS_OF,
        available_at=AS_OF, status="OK", coverage=1.0, missing_policy="x",
        series=(MarketFactorPoint(date="2026-07-16", value=0.7),), summary=None,
        n_days=1, first_date="2026-07-16", provenance=_prov(),
    )
    assert a.content_digest != b.content_digest


def test_payload_relocation_does_not_move_semantic_digest():
    def _mkval(object_id: str) -> MarketFactorValue:
        prov = FactorProvenance(
            sources=("engine.daily_panel",),
            snapshot_refs=(
                PayloadRef(namespace="main", object_id=object_id, content_digest="ab" * 32),
            ),
        )
        return MarketFactorValue.build(
            factor_id="breadth.ad_ratio", definition_version="1", family="breadth",
            value=0.7, params={}, universe="all_a", effective_at=AS_OF, available_at=AS_OF,
            status="OK", coverage=1.0, missing_policy="x",
            series=(MarketFactorPoint(date="2026-07-16", value=0.7),), summary=None,
            n_days=1, first_date="2026-07-16", provenance=prov,
        )

    a = _mkval("obj-A")
    b = _mkval("obj-B")  # only the payload dereference locator differs
    assert a.content_digest == b.content_digest
