# -*- coding: utf-8 -*-
"""Phase 5 · Task 1 — market-factor contract models + the frozen v1 battery.

The market-factor layer is the deterministic context surface the whole framework
boots from: ``market.factor`` computes **带参走势向量** from PIT-windowed raw
inputs, and the LLM Lane-0 workers read only the resulting
:class:`MarketFactorReport`. This module owns the *contracts half* (Task 1) —

* :class:`MarketFactorPoint`   — one dated series point (+ derived ``aux`` values);
* :class:`MarketFactorDefinition` — a frozen factor definition (params/inputs);
* :class:`MarketFactorSetSpec`  — the versioned, self-sealed factor battery
  (registered ``MarketFactorSetSpec@1``);
* :class:`MarketFactorValue`    — one computed factor series + status/coverage
  (registered ``MarketFactorValue@1``);
* :class:`MarketFactorReport`   — the report envelope over the whole battery
  (registered ``MarketFactorReport@1``).

Every model is strict / frozen / extra-forbid over the Phase-1 :class:`DigestModel`
so a factor value or param change moves the semantic digest and a payload
relocation (a ``PayloadRef.object_id`` change) does not. Honesty is structural:
an ``UNAVAILABLE`` factor can never carry a series point, a non-``None`` value or a
non-zero coverage — zero-fill is impossible by construction (①§0 红线). The
compute core, PIT loaders, shield projection and worker handler are added in
Task 3; only the contracts + the hand-frozen 19-id v1 factor set live here.

The three ``@1`` models are **not** registered by the Phase-1 default registry;
they are enumerated by the Phase-5 cumulative registry chain (Task 9). Task 1
only defines them and the reviewed ``build_market_factor_set_v1`` battery, whose
frozen digest is pinned by ``tests/orchestration/golden/market_factor_set_v1.json``
(hand-frozen, never regenerated from test code).
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.enums import Confidence, RotationStage
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef, SchemaRef

__all__ = [
    "MarketFactorPoint",
    "FactorSummary",
    "FactorProvenance",
    "CoverageSummary",
    "MarketFactorDefinition",
    "MarketFactorSetSpec",
    "MarketFactorValue",
    "MarketFactorReport",
    "assemble_market_factor_report",
    "build_market_factor_set_v1",
    "MARKET_FACTOR_SET_V1_FACTOR_IDS",
    # --- Task 2: Lane 0 LLM output contracts ---
    "TrendState",
    "RiskState",
    "HeatState",
    "AXIS_SUM_TOLERANCE",
    "UNKNOWN_ATTENTION_THRESHOLD",
    "HIGH_CONFIDENCE_UNKNOWN_MAX",
    "EvidenceAnchor",
    "RegimeReport",
    "MainlineRead",
    "RotationReport",
    "MARKET_FACTOR_REPORT_SCHEMA_REF",
    "REGIME_REPORT_SCHEMA_REF",
    "ROTATION_REPORT_SCHEMA_REF",
]

#: sealing placeholder used by the ``build`` classmethods when a raw field is
#: malformed, so the real ``ValidationError`` surfaces at strict construction.
_DIGEST_PLACEHOLDER = "0" * 64

#: closed factor-family vocabulary (① §2).
FactorFamily = Literal["breadth", "flow", "rot", "vol", "val", "temp"]

#: a value in a frozen params map — a finite float or a non-blank string.
_ParamValue = FiniteFloat | NonEmptyStr


def _require_iso_date(v: str) -> str:
    """Reject anything that is not a real ISO ``YYYY-MM-DD`` calendar date."""
    from datetime import datetime

    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date must be a valid ISO YYYY-MM-DD calendar date, got {v!r}")
    return v


#: strict trading-date string: regex-shaped AND a real calendar date.
IsoDate = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(_require_iso_date)
]
#: a coverage fraction in ``[0, 1]``.
_Coverage = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]

#: D1 cap — a rendered factor series carries at most 60 trading-day points.
_MAX_SERIES_POINTS = 60


# --------------------------------------------------------------------------- #
# Nested value objects                                                        #
# --------------------------------------------------------------------------- #
class MarketFactorPoint(DigestModel):
    """One dated series point plus its per-date derived ``aux`` values.

    ``aux`` carries the definition's derived quantities (MA/EMA/slope/z…); its
    keys are restricted to the owning definition's ``aux_keys`` — a restriction
    enforced at report assembly (:func:`assemble_market_factor_report`), since a
    bare point does not know its definition.
    """

    date: IsoDate
    value: FiniteFloat
    aux: dict[NonEmptyStr, FiniteFloat] = {}


class FactorSummary(DigestModel):
    """Compact latest/change/percentile summary (① D1: 5/20 摘要 + 250 分位).

    ``pct_250d`` is ``None`` when 250-session coverage is insufficient — never
    hard-computed (① §5 冷启动语义).
    """

    latest: FiniteFloat
    chg_5d: FiniteFloat | None = None
    chg_20d: FiniteFloat | None = None
    pct_250d: FiniteFloat | None = None


class FactorProvenance(DigestModel):
    """Upstream data surfaces + snapshot refs a factor value was computed from.

    ``snapshot_refs`` are :class:`PayloadRef`s: their ``object_id`` is an
    audit-only dereference locator (semantic identity is namespace +
    content_digest), so relocating byte-identical evidence cannot move the
    parent value's semantic digest.
    """

    sources: tuple[NonEmptyStr, ...] = ()
    snapshot_refs: tuple[PayloadRef, ...] = ()


class CoverageSummary(DigestModel):
    """Per-status tally of a report's factor values (① §1)."""

    n_ok: NonNegativeInt
    n_degraded: NonNegativeInt
    n_unavailable: NonNegativeInt


class MarketFactorDefinition(DigestModel):
    """A frozen factor definition — the identity that seals into the battery digest.

    ``required_inputs`` and ``aux_keys`` are canonically sorted + duplicate-free so
    the definition digest is representation-independent. Changing any param, input
    or window is a reviewed change that moves ``MarketFactorSetSpec.content_digest``
    (① §0: 加/改因子 = 注册表 bump).
    """

    factor_id: LogicalId
    definition_version: NonEmptyStr
    params: dict[NonEmptyStr, _ParamValue]
    required_inputs: tuple[LogicalId, ...]
    min_history_sessions: PositiveInt
    aux_keys: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _verify(self) -> "MarketFactorDefinition":
        ri = list(self.required_inputs)
        if ri != sorted(ri):
            raise ValueError("required_inputs must be canonically sorted")
        if len(set(ri)) != len(ri):
            raise ValueError("required_inputs must be duplicate-free")
        ak = list(self.aux_keys)
        if ak != sorted(ak):
            raise ValueError("aux_keys must be sorted")
        if len(set(ak)) != len(ak):
            raise ValueError("aux_keys must be duplicate-free")
        return self


# --------------------------------------------------------------------------- #
# MarketFactorSetSpec@1 — the versioned, self-sealed battery                    #
# --------------------------------------------------------------------------- #
class MarketFactorSetSpec(DigestModel):
    """The reviewed, versioned factor battery (registered ``MarketFactorSetSpec@1``).

    ``definitions`` are sorted by ``factor_id``, duplicate-free and non-empty;
    ``content_digest`` self-seals the whole battery (the ``battery_digest`` a
    :class:`MarketFactorReport` binds). Build via :meth:`build`; the frozen v1
    battery is :func:`build_market_factor_set_v1`.
    """

    schema_version: Literal["1"] = "1"
    factor_set_version: NonEmptyStr
    feature_schema_version: NonEmptyStr
    universe: NonEmptyStr
    frequency: Literal["day"] = "day"
    definitions: tuple[MarketFactorDefinition, ...]
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MarketFactorSetSpec":
        if not self.definitions:
            raise ValueError("definitions must be non-empty")
        ids = [d.factor_id for d in self.definitions]
        if ids != sorted(ids):
            raise ValueError("definitions must be sorted by factor_id")
        if len(set(ids)) != len(ids):
            raise ValueError("definitions must be duplicate-free by factor_id")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MarketFactorSetSpec":
        """Seal a factor set: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# MarketFactorValue@1 — one computed factor series                             #
# --------------------------------------------------------------------------- #
class MarketFactorValue(DigestModel):
    """One computed factor: series + honest status/coverage (registered ``@1``).

    Field shape is aligned to ① §2 ``FactorSeries`` (① is the authoritative field
    list). The ①§2 three-state status matrix is structural:

    * ``OK``          ⇒ non-empty series, ``value`` == the latest point's value,
      ``coverage == 1.0``, ``reason`` forbidden;
    * ``DEGRADED``    ⇒ non-empty series, ``value`` == latest, ``0 < coverage <= 1``
      (short window / young archive), ``reason`` **required**;
    * ``UNAVAILABLE`` ⇒ empty series, ``value is None``, ``summary is None``,
      ``coverage == 0.0``, ``n_days == 0``, ``first_date is None``,
      ``effective_at == available_at``, ``reason`` required.

    ``series`` dates are strictly increasing and capped at 60 points (D1);
    ``first_date`` (the true archive start) is honesty-displayed and never later
    than the first rendered point. ``content_digest`` self-seals; build via
    :meth:`build`.
    """

    schema_version: Literal["1"] = "1"
    factor_id: LogicalId
    definition_version: NonEmptyStr
    family: FactorFamily
    value: FiniteFloat | None
    params: dict[NonEmptyStr, _ParamValue]
    universe: NonEmptyStr
    frequency: Literal["day"] = "day"
    effective_at: UtcDateTime
    available_at: UtcDateTime
    status: Literal["OK", "DEGRADED", "UNAVAILABLE"]
    coverage: _Coverage
    missing_policy: NonEmptyStr
    series: tuple[MarketFactorPoint, ...] = ()
    summary: FactorSummary | None = None
    n_days: NonNegativeInt
    first_date: NonEmptyStr | None
    provenance: FactorProvenance = Field(default_factory=FactorProvenance)
    reason: NonEmptyStr | None = None
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MarketFactorValue":
        # series shape: strictly increasing dates, capped at 60, ≤ n_days.
        dates = [p.date for p in self.series]
        for a, b in zip(dates, dates[1:]):
            if not a < b:
                raise ValueError("series point dates must be strictly increasing")
        if len(self.series) > _MAX_SERIES_POINTS:
            raise ValueError(f"series may not exceed {_MAX_SERIES_POINTS} points (D1)")
        if len(self.series) > self.n_days:
            raise ValueError("n_days must be >= the number of displayed series points")

        if self.status == "UNAVAILABLE":
            if self.series:
                raise ValueError("UNAVAILABLE must have an empty series")
            if self.value is not None:
                raise ValueError("UNAVAILABLE must have value=None")
            if self.summary is not None:
                raise ValueError("UNAVAILABLE must have summary=None")
            if self.coverage != 0.0:
                raise ValueError("UNAVAILABLE must have coverage=0.0")
            if self.n_days != 0:
                raise ValueError("UNAVAILABLE must have n_days=0")
            if self.first_date is not None:
                raise ValueError("UNAVAILABLE must have first_date=None")
            if self.effective_at != self.available_at:
                raise ValueError("UNAVAILABLE must have effective_at == available_at")
            if self.reason is None:
                raise ValueError("UNAVAILABLE requires a reason")
        else:
            if not self.series:
                raise ValueError(f"{self.status} requires a non-empty series")
            if self.value is None:
                raise ValueError(f"{self.status} requires a non-None value")
            if self.value != self.series[-1].value:
                raise ValueError("value must equal the latest series point's value")
            if self.first_date is None:
                raise ValueError(f"{self.status} requires first_date")
            if self.first_date > self.series[0].date:
                raise ValueError("first_date must not be later than the series start date")
            if self.status == "OK":
                if self.coverage != 1.0:
                    raise ValueError("OK requires coverage == 1.0")
                if self.reason is not None:
                    raise ValueError("OK forbids a reason")
            else:  # DEGRADED
                if not 0.0 < self.coverage <= 1.0:
                    raise ValueError("DEGRADED requires 0 < coverage <= 1")
                if self.reason is None:
                    raise ValueError("DEGRADED requires a reason")

        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MarketFactorValue":
        """Seal a factor value: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# MarketFactorReport@1 — the battery report envelope                           #
# --------------------------------------------------------------------------- #
def _report_coverage(values: tuple[MarketFactorValue, ...]) -> float:
    """Report-level coverage = mean of per-factor coverage over the bound set."""
    return sum(v.coverage for v in values) / len(values)


class MarketFactorReport(DigestModel):
    """The report the whole battery emits (registered ``MarketFactorReport@1``).

    Envelope aligned to ① §1. The cross-consistency validators tie
    ``feature_vector`` / ``feature_coverage`` / ``missing_features`` /
    ``unavailable_factor_ids`` / ``coverage`` / ``coverage_summary`` to the
    ``values`` tuple bit-for-bit, so a fabricated feature (a ``feature_vector``
    key for an ``UNAVAILABLE`` factor) or a drifted aggregate is rejected.

    Spec-binding checks (ids equal the bound set, ``battery_digest`` == the set's
    ``content_digest``, aux keys ⊆ each definition's ``aux_keys``) require the
    :class:`MarketFactorSetSpec` and live in :func:`assemble_market_factor_report`,
    which is the only legal constructor from a set + values.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    clock_mode: Literal["eod", "intraday"]
    universe_registry_version: NonEmptyStr
    factor_set_version: NonEmptyStr
    battery_digest: DigestHex
    feature_schema_version: NonEmptyStr
    universe: NonEmptyStr
    values: tuple[MarketFactorValue, ...]
    data_snapshot_hash: DigestHex
    coverage: _Coverage
    coverage_summary: CoverageSummary
    feature_vector: dict[LogicalId, FiniteFloat]
    feature_coverage: dict[LogicalId, FiniteFloat]
    missing_features: tuple[LogicalId, ...]
    unavailable_factor_ids: tuple[LogicalId, ...]
    badges: tuple[NonEmptyStr, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "MarketFactorReport":
        if not self.values:
            raise ValueError("values must be non-empty")
        ids = [v.factor_id for v in self.values]
        if ids != sorted(ids):
            raise ValueError("values must be sorted by factor_id")
        if len(set(ids)) != len(ids):
            raise ValueError("values must be duplicate-free by factor_id")

        nonunavail = [v for v in self.values if v.status != "UNAVAILABLE"]
        unavail_ids = tuple(sorted(v.factor_id for v in self.values if v.status == "UNAVAILABLE"))

        exp_fv = {v.factor_id: v.value for v in nonunavail}
        if dict(self.feature_vector) != exp_fv:
            raise ValueError("feature_vector must be the latest value of every OK/DEGRADED factor")
        exp_fc = {v.factor_id: v.coverage for v in nonunavail}
        if dict(self.feature_coverage) != exp_fc:
            raise ValueError("feature_coverage must be the coverage of every OK/DEGRADED factor")
        if self.missing_features != unavail_ids:
            raise ValueError("missing_features must be exactly the sorted UNAVAILABLE ids")
        if self.unavailable_factor_ids != unavail_ids:
            raise ValueError("unavailable_factor_ids must equal missing_features")
        # invariant 4 — no fabricated feature.
        if set(self.feature_vector) & set(self.missing_features):
            raise ValueError("feature_vector must not contain a missing (UNAVAILABLE) feature")

        expected_cov = _report_coverage(self.values)
        if self.coverage != expected_cov:
            raise ValueError("coverage must equal the mean of per-factor coverage")

        cs = self.coverage_summary
        tally = (
            sum(1 for v in self.values if v.status == "OK"),
            sum(1 for v in self.values if v.status == "DEGRADED"),
            sum(1 for v in self.values if v.status == "UNAVAILABLE"),
        )
        if (cs.n_ok, cs.n_degraded, cs.n_unavailable) != tally:
            raise ValueError("coverage_summary counts must equal the per-status tally")

        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "MarketFactorReport":
        """Seal a report: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


def assemble_market_factor_report(
    *,
    spec: MarketFactorSetSpec,
    as_of: UtcDateTime,
    clock_mode: Literal["eod", "intraday"],
    universe_registry_version: NonEmptyStr,
    values: tuple[MarketFactorValue, ...],
    data_snapshot_hash: DigestHex,
    badges: tuple[NonEmptyStr, ...] = (),
) -> MarketFactorReport:
    """Assemble + validate a :class:`MarketFactorReport` from a bound set + values.

    Enforces the spec-binding invariants the report envelope cannot self-check —
    the values' ids equal the bound set's ids exactly, every value's ``family``
    matches its ``factor_id`` prefix and universe matches the set, and each series
    point's ``aux`` keys are ⊆ the owning definition's ``aux_keys`` — then derives
    the report aggregates deterministically and seals the envelope. This is the
    only legal path from a factor set to a report.
    """
    definitions = {d.factor_id: d for d in spec.definitions}
    value_ids = {v.factor_id for v in values}
    if value_ids != set(definitions):
        raise ValueError(
            "report values must match the bound factor set's ids exactly; "
            f"symmetric difference: {sorted(value_ids ^ set(definitions))}"
        )

    for v in values:
        definition = definitions[v.factor_id]
        if v.family != v.factor_id.split(".", 1)[0]:
            raise ValueError(
                f"{v.factor_id}: family {v.family!r} does not match the factor_id prefix"
            )
        if v.universe != spec.universe:
            raise ValueError(
                f"{v.factor_id}: universe {v.universe!r} does not match the set universe "
                f"{spec.universe!r}"
            )
        allowed = set(definition.aux_keys)
        for point in v.series:
            extra = set(point.aux) - allowed
            if extra:
                raise ValueError(
                    f"{v.factor_id}: aux keys {sorted(extra)} are outside the "
                    f"definition's aux_keys {sorted(allowed)}"
                )

    ordered = tuple(sorted(values, key=lambda v: v.factor_id))
    nonunavail = [v for v in ordered if v.status != "UNAVAILABLE"]
    missing = tuple(sorted(v.factor_id for v in ordered if v.status == "UNAVAILABLE"))
    coverage_summary = CoverageSummary(
        n_ok=sum(1 for v in ordered if v.status == "OK"),
        n_degraded=sum(1 for v in ordered if v.status == "DEGRADED"),
        n_unavailable=sum(1 for v in ordered if v.status == "UNAVAILABLE"),
    )
    return MarketFactorReport.build(
        as_of=as_of,
        clock_mode=clock_mode,
        universe_registry_version=universe_registry_version,
        factor_set_version=spec.factor_set_version,
        battery_digest=spec.content_digest,
        feature_schema_version=spec.feature_schema_version,
        universe=spec.universe,
        values=ordered,
        data_snapshot_hash=data_snapshot_hash,
        coverage=_report_coverage(ordered),
        coverage_summary=coverage_summary,
        feature_vector={v.factor_id: v.value for v in nonunavail},
        feature_coverage={v.factor_id: v.coverage for v in nonunavail},
        missing_features=missing,
        unavailable_factor_ids=missing,
        badges=badges,
    )


# --------------------------------------------------------------------------- #
# The reviewed v1 factor battery (17 ① rows, R3 splits ⇒ 19 ids)               #
# --------------------------------------------------------------------------- #
# Hand-frozen alongside ``tests/orchestration/golden/market_factor_set_v1.json``.
# ⏳-marked params (divergence.alert) are reviewed v1 provisionals; tuning belongs
# to Phase 4 ``run_optimize`` over matured cases (a new ``definition_version``,
# never a silent edit). ``breadth.promotion_rate`` uses the implemented
# ``limit_days>=2`` numerator (Ledger R4) — a standing DEGRADED 口径 note, not a
# redefinition. Editing any value here moves the battery digest ⇒ the golden test
# fails until the golden is re-frozen under review (① §0).
_V1_ROWS: tuple[dict[str, Any], ...] = (
    {
        "factor_id": "breadth.ad_ratio",
        "params": {"ma_short": 5.0, "ma_long": 20.0, "slope_window": 20.0},
        "aux_keys": ("ma20", "ma5", "slope"),
        "required_inputs": ("updown",),
        "min_history_sessions": 25,
    },
    {
        "factor_id": "breadth.nhnl",
        "params": {"window_short": 20.0, "window_long": 60.0},
        "aux_keys": ("nhnl20", "nhnl60"),
        "required_inputs": ("closes_panel",),
        "min_history_sessions": 61,
    },
    {
        "factor_id": "breadth.limit_up_ema",
        "params": {"ema_span": 3.0},
        "aux_keys": ("ema3",),
        "required_inputs": ("limit_up_total",),
        "min_history_sessions": 4,
    },
    {
        "factor_id": "breadth.break_rate",
        "params": {},
        "aux_keys": (),
        "required_inputs": ("break_counts",),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "breadth.ladder_height",
        "params": {},
        "aux_keys": (),
        "required_inputs": ("board_pools",),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "breadth.promotion_rate",
        "params": {"numerator": "limit_days>=2"},
        "aux_keys": (),
        "required_inputs": ("board_pools",),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "breadth.divergence",
        "params": {"ret_window": 20.0, "z_window": 250.0, "alert": 1.5},
        "aux_keys": ("z_breadth", "z_index"),
        "required_inputs": ("closes_index", "updown"),
        "min_history_sessions": 271,
    },
    {
        "factor_id": "flow.northbound",
        "params": {"cum_short": 5.0, "cum_long": 20.0, "pct_window": 250.0},
        "aux_keys": ("cum20", "cum5", "pct250", "slope"),
        "required_inputs": ("north_net",),
        "min_history_sessions": 250,
    },
    {
        "factor_id": "flow.main_pct",
        "params": {"pct_window": 250.0},
        "aux_keys": ("pct250",),
        "required_inputs": ("main_net",),
        "min_history_sessions": 250,
    },
    {
        "factor_id": "rot.hhi",
        "params": {"topk": 3.0},
        "aux_keys": ("hhi", "top3_share"),
        "required_inputs": ("sector_flows",),
        "min_history_sessions": 20,
    },
    {
        "factor_id": "rot.diffusion",
        "params": {"topk": 3.0},
        "aux_keys": (),
        "required_inputs": ("closes_panel", "concept_membership", "sector_flows"),
        "min_history_sessions": 20,
    },
    {
        "factor_id": "rot.dispersion",
        "params": {"dispersion": "cross_sectional_std"},
        "aux_keys": (),
        "required_inputs": ("industry_returns",),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "rot.ladder_theme",
        "params": {"topk": 3.0},
        "aux_keys": (),
        "required_inputs": ("board_pools", "limit_reasons"),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "rot.leader_persist",
        "params": {"win": 5.0},
        "aux_keys": (),
        "required_inputs": ("sector_leaders",),
        "min_history_sessions": 6,
    },
    {
        "factor_id": "rot.flow_streak",
        "params": {},
        "aux_keys": (),
        "required_inputs": ("sector_flows",),
        "min_history_sessions": 5,
    },
    {
        "factor_id": "rot.theme_burst",
        "params": {},
        "aux_keys": (),
        "required_inputs": ("board_pools", "universe_versions"),
        "min_history_sessions": 2,
    },
    {
        "factor_id": "vol.rv",
        "params": {"window_short": 5.0, "window_long": 20.0},
        "aux_keys": ("rv_ratio",),
        "required_inputs": ("closes_index",),
        "min_history_sessions": 21,
    },
    {
        "factor_id": "val.pct",
        "params": {"pct_years": 5.0},
        "aux_keys": ("pb_pct", "pe_pct"),
        "required_inputs": ("index_valuation",),
        "min_history_sessions": 1150,
    },
    {
        "factor_id": "temp.astock",
        "params": {},
        "aux_keys": (),
        "required_inputs": ("astock_temp",),
        "min_history_sessions": 20,
    },
)

#: the frozen v1 id list, sorted by factor_id (17 ① rows, R3 splits ⇒ 19).
MARKET_FACTOR_SET_V1_FACTOR_IDS: tuple[str, ...] = tuple(
    sorted(row["factor_id"] for row in _V1_ROWS)
)


def build_market_factor_set_v1() -> MarketFactorSetSpec:
    """Construct the reviewed, self-sealed v1 market-factor battery.

    The runtime source of truth for the ``mfs-v1`` battery; its frozen digest is
    pinned by ``tests/orchestration/golden/market_factor_set_v1.json`` (any edit
    to a row above moves the digest and fails the golden test until re-reviewed).
    """
    definitions = tuple(
        sorted(
            (
                MarketFactorDefinition(
                    factor_id=row["factor_id"],
                    definition_version="1",
                    params=row["params"],
                    required_inputs=row["required_inputs"],
                    min_history_sessions=row["min_history_sessions"],
                    aux_keys=row["aux_keys"],
                )
                for row in _V1_ROWS
            ),
            key=lambda d: d.factor_id,
        )
    )
    return MarketFactorSetSpec.build(
        factor_set_version="mfs-v1",
        feature_schema_version="mfs-v1",
        universe="all_a",
        frequency="day",
        definitions=definitions,
    )


# =========================================================================== #
# Task 2 — Lane 0 LLM output contracts (RegimeReport / RotationReport)          #
# =========================================================================== #
# The two reasoner seats read exactly one MarketFactorReport (bound by its
# content digest) and emit these draft-only reads. They are Lane 0 outputs and
# are NEVER decision-class (invariant 1: neither name is in Phase 1
# ``_DECISION_CLASS_SCHEMAS``). Honesty is structural: unknown is a first-class
# mass on every axis, an axis whose modal label is unknown forces LOW confidence,
# and an empty mainline list is a lawful "无主线=诚实" output with a named reason.


# --------------------------------------------------------------------------- #
# Phase-5-local axis enums (④§1; ruling R6 fixes the CHINESE trend values).     #
# --------------------------------------------------------------------------- #
# Phase-1 ``enums.py`` is untouched — these live here (Phase-5-local). Ruling R6
# fixes the Chinese ``TrendState`` values (牛/熊/震荡); the delayed grader's
# ``RealizedRegime`` realized-trend vocabulary (memory/experience.py, a later
# task) MUST reuse this exact same 词表 so 判读 vs realized calibration keys off
# one word list — the constraint is recorded here at the vocabulary's source.
class TrendState(str, Enum):
    BULL = "牛"; BEAR = "熊"; RANGE = "震荡"; UNKNOWN = "unknown"  # noqa: E702


class RiskState(str, Enum):
    RISK_ON = "risk_on"; RISK_OFF = "risk_off"; NEUTRAL = "neutral"; UNKNOWN = "unknown"  # noqa: E702


class HeatState(str, Enum):
    NORMAL = "normal"; OVERHEAT = "overheat"; UNKNOWN = "unknown"  # noqa: E702


#: closed-map sum must land within this tolerance of 1.0 (LLM-reported floats).
AXIS_SUM_TOLERANCE: float = 1e-8
#: an axis whose ``unknown`` mass reaches this REQUIRES a named ``unknown_reason``
#: (and forbids one below it — unknown is never decorative).
UNKNOWN_ATTENTION_THRESHOLD: float = 0.25
#: HIGH confidence requires every axis's ``unknown`` mass to be at or below this.
HIGH_CONFIDENCE_UNKNOWN_MAX: float = 0.10

#: the ordered axis → (enum, probabilities-field, modal-field) wiring the
#: RegimeReport validators iterate; ``unknown_member`` is each enum's honest mass.
_REGIME_AXES: tuple[tuple[str, type[Enum], str, str, Enum], ...] = (
    ("trend", TrendState, "trend_probabilities", "trend", TrendState.UNKNOWN),
    ("risk", RiskState, "risk_probabilities", "risk_state", RiskState.UNKNOWN),
    ("heat", HeatState, "heat_probabilities", "heat_state", HeatState.UNKNOWN),
)


def _argmax_member(members: type[Enum], probs: dict[Any, float]) -> Any:
    """The highest-probability member, ties broken by frozen declaration order.

    ``max`` returns the first maximal element encountered, so iterating the enum
    in declaration order makes the earliest-declared label win an exact tie.
    """
    return max(members, key=lambda m: probs[m])


# --------------------------------------------------------------------------- #
# EvidenceAnchor / MainlineRead — nested value objects.                         #
# --------------------------------------------------------------------------- #
# ④§1 sketches these as ``ContractModel``; here they are ``DigestModel`` (a
# ``ContractModel`` subclass). They MUST be DigestModel because canonical JSON
# refuses to project a non-DigestModel pydantic model nested in a DigestModel's
# semantic fields (``digest._project`` raises), so a plain ContractModel anchor
# would make the parent report's digest uncomputable — the same in-file idiom as
# ``MarketFactorPoint``/``FactorSummary`` (nested DigestModel value objects).
class EvidenceAnchor(DigestModel):
    """One cited factor reading anchoring a load-bearing claim (④§1).

    ``factor_id`` must exist in the bound ``market_factor_report`` — that machine
    check is a downstream (Task 10 e2e) responsibility; the contract only carries
    the fields. ``value`` is 逐字 from the Task 3b rendered block.
    """

    factor_id: NonEmptyStr
    value: FiniteFloat
    reading: NonEmptyStr


class MainlineRead(DigestModel):
    """One ranked mainline with its per-mainline rotation stage (④§1).

    Replaces the earlier ``RotationMainline``. ``stage`` binds the FROZEN
    ``RotationStage`` enum (启动/扩散/分化/退潮/unknown) — staging is per mainline
    (④/AMEND-4 §4.2), ``unknown`` lawful. ``strength`` is on ④'s [0,10] scale.
    ``chain_nodes`` stays empty without an industry-chain block (never improvised).
    """

    name: NonEmptyStr
    universe_key: NonEmptyStr
    stage: RotationStage
    strength: Annotated[FiniteFloat, Field(ge=0.0, le=10.0)]
    persistence: NonEmptyStr
    evidence: tuple[EvidenceAnchor, ...]
    chain_nodes: tuple[NonEmptyStr, ...] = ()


# --------------------------------------------------------------------------- #
# RegimeReport@1 — three-axis regime read.                                     #
# --------------------------------------------------------------------------- #
class RegimeReport(DigestModel):
    """The Lane 0 market-regime read (registered ``RegimeReport@1``; ④§1).

    Three closed probability axes (trend/risk/heat) each summing to 1 with
    ``unknown`` a first-class mass; ``confidence`` is the ONLY confidence carrier
    (④/AMEND-7-3 deleted the earlier ``confidence_score`` float). ``build`` seals
    the content digest; a ``model_validator`` re-verifies it on load.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    factor_report_digest: DigestHex
    trend: TrendState
    risk_state: RiskState
    heat_state: HeatState
    trend_probabilities: dict[TrendState, FiniteFloat]
    risk_probabilities: dict[RiskState, FiniteFloat]
    heat_probabilities: dict[HeatState, FiniteFloat]
    confidence: Confidence
    evidence: tuple[EvidenceAnchor, ...]
    conflicts: tuple[NonEmptyStr, ...] = ()
    analog_case_ids: tuple[NonEmptyStr, ...] = ()
    drivers: tuple[NonEmptyStr, ...]
    evidence_factor_ids: tuple[LogicalId, ...]
    narrative: NonEmptyStr
    unknown_reason: NonEmptyStr | None = None
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RegimeReport":
        axis_probs: dict[str, dict[Any, float]] = {}
        any_unknown_attention = False
        modal_is_unknown = False

        for _label, members, probs_field, modal_field, unknown_member in _REGIME_AXES:
            probs: dict[Any, float] = getattr(self, probs_field)
            # 1. exactly the enum's member set (axis-specific labels only).
            if set(probs) != set(members):
                raise ValueError(
                    f"{probs_field} must carry exactly the {members.__name__} member set"
                )
            # 2. every probability in [0, 1] and the axis sums to 1 ± tolerance.
            for p in probs.values():
                if not 0.0 <= p <= 1.0:
                    raise ValueError(f"{probs_field} probabilities must lie in [0, 1]")
            if abs(math.fsum(probs.values()) - 1.0) > AXIS_SUM_TOLERANCE:
                raise ValueError(
                    f"{probs_field} must sum to 1 ± {AXIS_SUM_TOLERANCE}"
                )
            # 3. the modal field equals the axis argmax (declaration-order ties).
            if getattr(self, modal_field) is not _argmax_member(members, probs):
                raise ValueError(
                    f"{modal_field} must equal the highest-probability {members.__name__} label"
                )
            axis_probs[probs_field] = probs
            if probs[unknown_member] >= UNKNOWN_ATTENTION_THRESHOLD:
                any_unknown_attention = True
            if getattr(self, modal_field) is unknown_member:
                modal_is_unknown = True

        # 4. unknown_reason required iff some axis reaches the attention threshold.
        if any_unknown_attention and self.unknown_reason is None:
            raise ValueError(
                "unknown_reason is required when any axis unknown mass "
                f">= {UNKNOWN_ATTENTION_THRESHOLD}"
            )
        if not any_unknown_attention and self.unknown_reason is not None:
            raise ValueError(
                "unknown_reason is forbidden when no axis reaches the attention "
                "threshold (unknown must be coverage/evidence-driven, not decorative)"
            )

        # 5. HIGH-confidence caps + modal-unknown forces LOW.
        if self.confidence is Confidence.HIGH:
            for _label, _members, probs_field, _modal, unknown_member in _REGIME_AXES:
                if axis_probs[probs_field][unknown_member] > HIGH_CONFIDENCE_UNKNOWN_MAX:
                    raise ValueError(
                        "HIGH confidence requires every axis unknown mass "
                        f"<= {HIGH_CONFIDENCE_UNKNOWN_MAX}"
                    )
        if modal_is_unknown and self.confidence is not Confidence.LOW:
            raise ValueError(
                "an axis with a modal 'unknown' label forces confidence == LOW"
            )

        # 6. evidence non-empty; evidence_factor_ids == sorted distinct anchor ids;
        #    drivers sorted + duplicate-free.
        if not self.evidence:
            raise ValueError("evidence must carry at least one EvidenceAnchor (④ ≥1)")
        distinct_ids = tuple(sorted({a.factor_id for a in self.evidence}))
        if self.evidence_factor_ids != distinct_ids:
            raise ValueError(
                "evidence_factor_ids must equal the sorted distinct anchor factor_ids"
            )
        drivers = list(self.drivers)
        if drivers != sorted(drivers):
            raise ValueError("drivers must be sorted")
        if len(set(drivers)) != len(drivers):
            raise ValueError("drivers must be duplicate-free")

        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RegimeReport":
        """Seal a regime read: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# RotationReport@1 — mainline rotation read.                                    #
# --------------------------------------------------------------------------- #
class RotationReport(DigestModel):
    """The Lane 0 mainline-rotation read (registered ``RotationReport@1``; ④§1).

    ``mainlines`` tuple order **is** the ranking (no separate rank field); the
    list may be empty — 无主线=诚实合法 — in which case a ``unknown_reason`` naming
    the driver (themeless tape / archive-young factors) is required, and forbidden
    otherwise. Staging lives on each ``MainlineRead`` (the earlier report-level
    ``stage`` field is deleted).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    factor_report_digest: DigestHex
    mainlines: tuple[MainlineRead, ...]
    confidence: Confidence
    conflicts: tuple[NonEmptyStr, ...] = ()
    analog_case_ids: tuple[NonEmptyStr, ...] = ()
    narrative: NonEmptyStr
    evidence_factor_ids: tuple[LogicalId, ...]
    unknown_reason: NonEmptyStr | None = None
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RotationReport":
        names = [m.name for m in self.mainlines]
        if len(set(names)) != len(names):
            raise ValueError("mainline names must be duplicate-free")
        ids = list(self.evidence_factor_ids)
        if ids != sorted(ids):
            raise ValueError("evidence_factor_ids must be sorted")
        if len(set(ids)) != len(ids):
            raise ValueError("evidence_factor_ids must be duplicate-free")
        # 无主线=诚实: an empty ranking requires a named reason; a non-empty one
        # forbids it (the reason is only for the honest empty case).
        if not self.mainlines and self.unknown_reason is None:
            raise ValueError(
                "an empty mainlines list requires unknown_reason (无主线=诚实: name "
                "the driver — themeless tape or archive-young factors)"
            )
        if self.mainlines and self.unknown_reason is not None:
            raise ValueError(
                "unknown_reason is forbidden when mainlines is non-empty"
            )
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RotationReport":
        """Seal a rotation read: compute ``content_digest`` from the fields."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Exported SchemaRef constants — one definition each, imported everywhere else. #
# --------------------------------------------------------------------------- #
#: downstream pinning surfaces (bridge config, manifest validators, worker output
#: bindings) import these — never re-instantiate the SchemaRef inline.
MARKET_FACTOR_REPORT_SCHEMA_REF: SchemaRef = SchemaRef(name="MarketFactorReport", version="1")
REGIME_REPORT_SCHEMA_REF: SchemaRef = SchemaRef(name="RegimeReport", version="1")
ROTATION_REPORT_SCHEMA_REF: SchemaRef = SchemaRef(name="RotationReport", version="1")
