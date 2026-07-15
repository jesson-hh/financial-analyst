# -*- coding: utf-8 -*-
"""Task 12 — source-versioned, reversible legacy-schema migration adapters.

Every adapter here is **source-schema-specific**: there is deliberately no
generic ``migrate_action(raw)`` and no generic ``MigratedAction``. A raw legacy
``"hold"`` under a *report* source (``ResearchAction`` domain) and under a
*position* source (``PositionAction`` domain) migrate through separate functions
into different result classes, because the two carry different semantics.

Load-bearing rules (frozen from ``task-12-brief.md`` + the Task-0 evidence in
``tests/orchestration/fixtures/legacy_contract_samples.json`` and the reviewed
contract map ``docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md``):

* Every scalar result is an immutable :class:`~guanlan_v2.orchestration.digest.DigestModel`
  preserving ``source_schema`` (a module-qualified :class:`SchemaRef`),
  ``adapter_version``, the exact ``raw`` (+ explicit ``raw_kind``), the
  ``mapping_status`` / ``mapping_basis`` / optional ``mapping_policy_id``, the
  typed normalized value or ``None`` and a ``reason``. The MAPPED / UNMAPPABLE
  matrix is *model validation*, not caller convention.
* ``LegacyScalar = StrictStr | StrictInt | StrictFloat``; the exact Python/JSON
  scalar type and value are preserved (``50`` ≠ ``50.0`` ≠ ``" HOLD "``), and
  ``bool`` is always rejected even though it is an ``int`` subclass.
* Unknown source / out-of-domain / wrong-type / out-of-range / non-finite raises
  :class:`MigrationError`. A *known-valid* legacy value with **no** evidence-backed
  equivalent returns an explicit ``UNMAPPABLE`` result (never a guess).
* Every adapter has an exact reverse returning the original ``raw`` (including
  for an ``UNMAPPABLE`` result).

The legacy static-graph ABI (§12.7) is a *data conversion only*: a
:class:`LegacyGraphMapping` is migration evidence and can never construct or
masquerade as a runnable ``Plan`` while any worker/edge/input is incomplete.
"""
from __future__ import annotations

import json
import math
from typing import Any, ClassVar, Literal, Union

import yaml
from pydantic import (
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from guanlan_v2.orchestration.catalog import (
    CompatibilityBinding,
    WorkerCatalogSnapshot,
)
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    content_digest,
)
from guanlan_v2.orchestration.enums import (
    Confidence,
    DependencyPolicy,
    LegacyMarketCycleStage,
    MappingStatus,
    NodeStatus,
    PlanSource,
    PortfolioRating,  # noqa: F401 (imported to document the un-fabricated target)
    PositionAction,
    ResearchAction,
    RotationStage,  # noqa: F401 (imported to document the un-collapsed target)
    SentimentBand,  # noqa: F401 (imported to document the un-fabricated target)
)
from guanlan_v2.orchestration.refs import LogicalId, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    StaticLegacyPlanAttestation,
    compute_candidate_plan_digest,
)

__all__ = [
    "MigrationError",
    "MappingBasis",
    "LegacyScalar",
    "JsonScalar",
    "JsonValue",
    "JsonObject",
    "ADAPTER_VERSION",
    "POLICY_ACTION_SURFACE_ALIAS_V1",
    # reviewed module-qualified source keys
    "SRC_REPORT_OUTPUT",
    "SRC_ETF_REPORT_OUTPUT",
    "SRC_DECISION_LEG",
    "SRC_WATCH_REC",
    "SRC_INTROSPECTION_PROPOSAL",
    "SRC_MARKET_CYCLE",
    "SRC_TAG_SCORE",
    "SRC_ASTOCK_TEMP",
    "SRC_PULSE_TEMP",
    "SRC_NEWS_SENTIMENT",
    "SRC_MARKET_TEMP_GATE",
    "SRC_STOCK_DEEP_DIVE",
    # scalar result models
    "MigratedRating",
    "MigratedResearchAction",
    "MigratedPositionAction",
    "MigratedConfidence",
    "MigratedSentiment",
    "MigratedRotationStage",
    # scalar adapters + reverses
    "migrate_rating",
    "rating_to_legacy",
    "rating_bucket",
    "migrate_research_action",
    "research_action_to_legacy",
    "migrate_position_action",
    "position_action_to_legacy",
    "migrate_confidence",
    "confidence_to_legacy",
    "migrate_sentiment",
    "sentiment_to_legacy",
    "migrate_rotation_stage",
    "rotation_stage_to_legacy",
    # legacy graph ABI
    "LegacyWorkerMapping",
    "LegacyDependencyMapping",
    "LegacyInputMapping",
    "LegacyGraphMapping",
    "normalize_legacy_graph_config",
    "migrate_legacy_graph",
    "legacy_graph_to_normalized_config",
    "compatibility_binding_for",
    "attest_static_legacy_plan",
]

#: adapter/policy version stamped on every reviewed row (Task 0 = v1).
ADAPTER_VERSION = "v1"

#: the opt-in, reviewed surface-alias policy for the two *action* adapters. It
#: tolerates leading/trailing whitespace and letter case on an otherwise
#: canonical action verb (``strip().lower()`` is used **only** as the lookup key;
#: the reverse still returns the exact raw). It never invents a semantic
#: equivalence — every accepted value still maps to its own canonical verb.
POLICY_ACTION_SURFACE_ALIAS_V1 = "policy.action_surface_alias.v1"

MappingBasis = Literal["authoritative_code", "approved_policy", "none"]
LegacyScalar = Union[StrictStr, StrictInt, StrictFloat]

#: recursive JSON value space used by the legacy graph config ABI. Scalar-adapter
#: ``bool`` rejection does **not** apply here — booleans are legal graph config.
#: These aliases document the ABI; graph-config model *fields* are annotated
#: ``dict[str, Any]`` (pydantic cannot build a schema for a self-referential
#: alias) and are deep-validated to this shape by :func:`_ensure_json_object`.
JsonScalar = Union[None, bool, StrictInt, FiniteFloat, StrictStr]
JsonValue = Union[JsonScalar, list, dict]
JsonObject = dict


class MigrationError(ValueError):
    """A source-versioned legacy migration adapter rejected its input.

    Subclasses :class:`ValueError` so callers may catch either.
    """


# --------------------------------------------------------------------------- #
# Reviewed module-qualified source keys                                        #
# --------------------------------------------------------------------------- #
# ``SchemaRef.name`` is a Python identifier (no ``.`` / ``-``), so the legacy
# module path ``financial_analyst.agent.tier3.report_writer.ReportOutput`` is
# encoded with underscores. The name carries the FULL module path, so the stock
# and ETF report writers can never collide on a bare ``ReportOutput`` short name.
def _src(module_qualified: str) -> SchemaRef:
    name = module_qualified.replace(".", "_").replace("-", "_")
    return SchemaRef(name=name, version="1")


SRC_REPORT_OUTPUT = _src("financial_analyst.agent.tier3.report_writer.ReportOutput")
SRC_ETF_REPORT_OUTPUT = _src("financial_analyst.agent.etf.report_writer.EtfReportOutput")
SRC_DECISION_LEG = _src("financial_analyst.backtest.decision.DecisionLeg")
SRC_WATCH_REC = _src("financial_analyst.watch.models.WatchRec")
SRC_INTROSPECTION_PROPOSAL = _src(
    "financial_analyst.agent.tier3.introspector.IntrospectionProposal")
SRC_MARKET_CYCLE = _src("guanlan_v2.strategy.perspectives.market_cycle")
SRC_TAG_SCORE = _src("guanlan_v2.datafeed.sentiment._TAG_SCORE")
SRC_ASTOCK_TEMP = _src("guanlan_v2.macro.astock.build_astock")
SRC_PULSE_TEMP = _src("guanlan_v2.macro.pulse._theme_temp")
SRC_NEWS_SENTIMENT = _src("financial_analyst.agent.tier1.news_sentiment.NewsSentimentOutput")
SRC_MARKET_TEMP_GATE = _src("guanlan_v2.screen.market_temp._gate")
SRC_STOCK_DEEP_DIVE = _src("financial_analyst.swarm.stock-deep-dive")


# --------------------------------------------------------------------------- #
# Strict raw-scalar validation (function boundary)                             #
# --------------------------------------------------------------------------- #
def _raw_kind_strict(raw: Any) -> str:
    """Return ``'str' | 'int' | 'float'`` or raise; ``bool`` is always rejected."""
    if isinstance(raw, bool):
        raise MigrationError("bool is not a valid legacy scalar")
    if isinstance(raw, str):
        return "str"
    if isinstance(raw, int):
        return "int"
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise MigrationError("non-finite float is not a valid legacy scalar")
        return "float"
    raise MigrationError(f"unsupported legacy scalar type {type(raw).__name__}")


def _require_source(source_schema: Any, allowed: tuple[SchemaRef, ...], adapter: str) -> SchemaRef:
    if not isinstance(source_schema, SchemaRef):
        raise MigrationError(f"{adapter}: source_schema must be a SchemaRef")
    for ref in allowed:
        if source_schema == ref:
            return source_schema
    raise MigrationError(
        f"{adapter}: unknown source schema {source_schema.key!r} "
        f"(accepted: {[r.key for r in allowed]})")


# --------------------------------------------------------------------------- #
# Scalar result envelope + MAPPED/UNMAPPABLE matrix                            #
# --------------------------------------------------------------------------- #
def _kind_of(v: Any) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        return "str"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return None


class _ScalarResult(DigestModel):
    """Common immutable envelope for every scalar migration result."""

    schema_version: Literal["1"] = "1"
    adapter_id: LogicalId
    adapter_version: NonEmptyStr = ADAPTER_VERSION
    source_schema: SchemaRef
    raw: LegacyScalar
    raw_kind: Literal["str", "int", "float"]
    mapping_status: MappingStatus
    mapping_basis: MappingBasis
    mapping_policy_id: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None

    @field_validator("raw")
    @classmethod
    def _raw_no_bool_finite(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("bool is not a valid legacy scalar")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("non-finite float is not a valid legacy scalar")
        return v

    @model_validator(mode="after")
    def _matrix(self) -> "_ScalarResult":
        normalized = getattr(self, "normalized", None)
        if _kind_of(self.raw) != self.raw_kind:
            raise ValueError(f"raw_kind {self.raw_kind!r} does not match raw type")
        if self.mapping_status is MappingStatus.MAPPED:
            if normalized is None:
                raise ValueError("MAPPED requires a non-None normalized value")
            if self.mapping_basis == "none":
                raise ValueError("MAPPED requires a mapping_basis other than 'none'")
            if self.reason is not None:
                raise ValueError("MAPPED must carry no reason")
            if self.mapping_basis == "approved_policy":
                if not self.mapping_policy_id:
                    raise ValueError("approved_policy basis requires a mapping_policy_id")
            elif self.mapping_policy_id is not None:
                raise ValueError("mapping_policy_id only with approved_policy basis")
        else:  # UNMAPPABLE
            if normalized is not None:
                raise ValueError("UNMAPPABLE requires a None normalized value")
            if not self.reason:
                raise ValueError("UNMAPPABLE requires a non-empty reason")
            if self.mapping_basis != "none":
                raise ValueError("UNMAPPABLE requires mapping_basis='none'")
            if self.mapping_policy_id is not None:
                raise ValueError("UNMAPPABLE must carry no mapping_policy_id")
        return self


class MigratedRating(_ScalarResult):
    normalized: PortfolioRating | None = None


class MigratedResearchAction(_ScalarResult):
    normalized: ResearchAction | None = None


class MigratedPositionAction(_ScalarResult):
    normalized: PositionAction | None = None


class MigratedConfidence(_ScalarResult):
    normalized: Confidence | None = None
    source_scale_id: LogicalId | None = None


class MigratedSentiment(_ScalarResult):
    normalized: SentimentBand | None = None
    source_scale: Literal["pm1", "zero_ten"] | None = None


class MigratedRotationStage(_ScalarResult):
    normalized: RotationStage | None = None


# =========================================================================== #
# 12.1 Rating adapter                                                          #
# =========================================================================== #
_RATING_SOURCES = (SRC_REPORT_OUTPUT, SRC_ETF_REPORT_OUTPUT)
_RATING_UNMAPPED_REASON = (
    "int score [-10,10] has no authoritative binning to the 5-band PortfolioRating; "
    "preserve raw int (Task 0: mapping_basis=none)")


def migrate_rating(
    raw: StrictInt, *, source_schema: SchemaRef, mapping_policy_id: str | None = None
) -> MigratedRating:
    """Migrate a legacy ``rating_overall`` int. UNMAPPABLE by Task-0 evidence."""
    _require_source(source_schema, _RATING_SOURCES, "migrate_rating")
    if _raw_kind_strict(raw) != "int":
        raise MigrationError("rating raw must be a strict int")
    if not -10 <= raw <= 10:
        raise MigrationError(f"rating {raw} outside the inventoried source domain [-10,10]")
    if mapping_policy_id is not None:
        # Task 0 records no authoritative code and no approved five-band policy.
        raise MigrationError(
            f"no approved five-band rating policy {mapping_policy_id!r}; "
            "rating binning is UNMAPPABLE in Phase 1")
    return MigratedRating(
        adapter_id="migration.rating", source_schema=source_schema, raw=raw, raw_kind="int",
        mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
        normalized=None, reason=_RATING_UNMAPPED_REASON)


def rating_to_legacy(migrated: MigratedRating) -> int:
    """Exact reverse — returns the original raw int."""
    return migrated.raw


def rating_bucket(rating: int, mapping_policy_id: str) -> PortfolioRating:
    """Optional reviewed-band exposer. No approved band policy exists in Phase 1,
    so this always raises; it never claims exact reversal."""
    raise MigrationError(
        f"no approved five-band policy {mapping_policy_id!r} for rating {rating}; "
        "Phase-1 rating binning is UNMAPPABLE")


# =========================================================================== #
# 12.2 / 12.3 Action adapters (separate result classes)                        #
# =========================================================================== #
_RESEARCH_SOURCES = (SRC_REPORT_OUTPUT, SRC_ETF_REPORT_OUTPUT)
_POSITION_SOURCES = (SRC_DECISION_LEG, SRC_WATCH_REC)
_RESEARCH_DOMAIN = tuple(e.value for e in ResearchAction)   # buy/accumulate/hold/avoid/sell
_POSITION_DOMAIN = tuple(e.value for e in PositionAction)   # buy/add/hold/reduce/sell


def _migrate_action(raw, source_schema, mapping_policy_id, *, sources, domain, enum, result_cls, adapter_id):
    _require_source(source_schema, sources, adapter_id)
    if _raw_kind_strict(raw) != "str":
        raise MigrationError(f"{adapter_id}: action raw must be a strict str")
    if mapping_policy_id is None:
        # exact lowercase identity is authoritative
        if raw in domain:
            return result_cls(
                adapter_id=adapter_id, source_schema=source_schema, raw=raw, raw_kind="str",
                mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
                normalized=enum(raw), reason=None)
        raise MigrationError(
            f"{adapter_id}: raw {raw!r} is outside the source action domain {domain} "
            "(case/whitespace variants require an approved alias policy)")
    # an approved surface-alias policy was explicitly selected
    if mapping_policy_id != POLICY_ACTION_SURFACE_ALIAS_V1:
        raise MigrationError(f"{adapter_id}: unknown alias policy {mapping_policy_id!r}")
    key = raw.strip().lower()
    if key in domain:
        return result_cls(
            adapter_id=adapter_id, source_schema=source_schema, raw=raw, raw_kind="str",
            mapping_status=MappingStatus.MAPPED, mapping_basis="approved_policy",
            mapping_policy_id=POLICY_ACTION_SURFACE_ALIAS_V1, normalized=enum(key), reason=None)
    raise MigrationError(
        f"{adapter_id}: raw {raw!r} is not a legal action even after surface normalization")


def migrate_research_action(
    raw: StrictStr, *, source_schema: SchemaRef, mapping_policy_id: str | None = None
) -> MigratedResearchAction:
    """Migrate a report ``action`` (``buy/accumulate/hold/avoid/sell``)."""
    return _migrate_action(
        raw, source_schema, mapping_policy_id, sources=_RESEARCH_SOURCES,
        domain=_RESEARCH_DOMAIN, enum=ResearchAction, result_cls=MigratedResearchAction,
        adapter_id="migration.research_action")


def research_action_to_legacy(migrated: MigratedResearchAction) -> str:
    return migrated.raw


def migrate_position_action(
    raw: StrictStr, *, source_schema: SchemaRef, mapping_policy_id: str | None = None
) -> MigratedPositionAction:
    """Migrate a backtest/watch ``action`` (``buy/add/hold/reduce/sell``)."""
    return _migrate_action(
        raw, source_schema, mapping_policy_id, sources=_POSITION_SOURCES,
        domain=_POSITION_DOMAIN, enum=PositionAction, result_cls=MigratedPositionAction,
        adapter_id="migration.position_action")


def position_action_to_legacy(migrated: MigratedPositionAction) -> str:
    return migrated.raw


# =========================================================================== #
# 12.4 Confidence adapter                                                      #
# =========================================================================== #
_CONFIDENCE_SOURCES = (SRC_INTROSPECTION_PROPOSAL,)
_CONFIDENCE_DOMAIN = ("low", "med", "high")
_CONFIDENCE_IDENTITY = {"low": Confidence.LOW, "high": Confidence.HIGH}  # 'med' -> UNMAPPABLE
_CONFIDENCE_MED_REASON = (
    "raw 'med' != design Confidence 'medium'; med->medium rename has no authoritative "
    "code or approved policy in Task 0; preserve raw")


def migrate_confidence(
    raw: LegacyScalar, *, source_schema: SchemaRef, mapping_policy_id: str | None = None
) -> MigratedConfidence:
    """Migrate the categorical introspector ``confidence`` (``low/med/high``).

    No *numeric* confidence scale is inventoried in Task 0, so a numeric raw is
    rejected. ``low``/``high`` are authoritative identity; ``med`` is UNMAPPABLE.
    """
    _require_source(source_schema, _CONFIDENCE_SOURCES, "migrate_confidence")
    if _raw_kind_strict(raw) != "str":
        raise MigrationError(
            "introspection confidence raw must be a str; no numeric confidence "
            "scale is inventoried in Task 0")
    if raw not in _CONFIDENCE_DOMAIN:
        raise MigrationError(f"raw {raw!r} outside confidence domain {_CONFIDENCE_DOMAIN}")
    if raw in _CONFIDENCE_IDENTITY:
        if mapping_policy_id is not None:
            raise MigrationError("authoritative identity confidence takes no policy id")
        return MigratedConfidence(
            adapter_id="migration.confidence", source_schema=source_schema, raw=raw,
            raw_kind="str", mapping_status=MappingStatus.MAPPED,
            mapping_basis="authoritative_code", normalized=_CONFIDENCE_IDENTITY[raw],
            source_scale_id=None, reason=None)
    # raw == 'med'
    if mapping_policy_id is not None:
        raise MigrationError(
            f"no approved 'med'->medium alias policy {mapping_policy_id!r} in Task 0")
    return MigratedConfidence(
        adapter_id="migration.confidence", source_schema=source_schema, raw=raw,
        raw_kind="str", mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
        normalized=None, source_scale_id=None, reason=_CONFIDENCE_MED_REASON)


def confidence_to_legacy(migrated: MigratedConfidence) -> LegacyScalar:
    return migrated.raw


# =========================================================================== #
# 12.5 Sentiment adapter                                                       #
# =========================================================================== #
# reviewed per-source policy. Every Task-0 sentiment source is UNMAPPABLE (no
# authoritative target band/scale), but its input validation differs by source.
_SENTIMENT_SCALED = {  # source.key -> (fixed source scale, lo, hi)
    SRC_TAG_SCORE.key: ("pm1", -1.0, 1.0),
}
_SENTIMENT_TEMP = {  # numeric [0,100] temperature sources with NO reviewed scale
    SRC_ASTOCK_TEMP.key: (0.0, 100.0),
    SRC_PULSE_TEMP.key: (0.0, 100.0),
}
_SENTIMENT_CATEGORICAL = {  # source.key -> allowed str values
    SRC_NEWS_SENTIMENT.key: frozenset({"利好", "利空", "中性"}),
    SRC_MARKET_TEMP_GATE.key: frozenset({"risk_off", "overheat", "neutral"}),
}
_SENTIMENT_SCALED_REASON = (
    "ternary/continuous store score has no authoritative rescale to the design "
    "SentimentReport score[0,10]; preserve raw")
_SENTIMENT_TEMP_REASON = (
    "deterministic temperature [0,100] has no authoritative mapping to the design "
    "SentimentReport score[0,10]; preserve raw")
_SENTIMENT_CATEGORICAL_REASON = (
    "3-level tilt / shield-gate level has no authoritative mapping to the 6-level "
    "SentimentBand; preserve raw")


def migrate_sentiment(
    raw: LegacyScalar, *, source_schema: SchemaRef,
    scale: Literal["pm1", "zero_ten"] | None = None,
) -> MigratedSentiment:
    """Migrate an inventoried sentiment scalar. All Task-0 sources are UNMAPPABLE.

    A numeric source with a reviewed scale (``pm1``) requires the caller to pass
    exactly that scale; a caller cannot reinterpret one producer by choosing a
    different scale. Temperature sources have no reviewed ``pm1``/``zero_ten``
    scale and reject a spuriously-supplied one.
    """
    if not isinstance(source_schema, SchemaRef):
        raise MigrationError("migrate_sentiment: source_schema must be a SchemaRef")
    key = source_schema.key
    kind = _raw_kind_strict(raw)

    if key in _SENTIMENT_SCALED:
        want_scale, lo, hi = _SENTIMENT_SCALED[key]
        if kind == "str":
            raise MigrationError("this numeric sentiment source requires a numeric raw")
        if scale is None:
            raise MigrationError(f"numeric source requires the explicit scale {want_scale!r}")
        if scale != want_scale:
            raise MigrationError(
                f"scale {scale!r} != the fixed source scale {want_scale!r}; "
                "a caller cannot reinterpret this producer")
        if not lo <= raw <= hi:
            raise MigrationError(f"value {raw} out of {want_scale} range [{lo},{hi}]")
        return MigratedSentiment(
            adapter_id="migration.sentiment", source_schema=source_schema, raw=raw,
            raw_kind=kind, mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
            normalized=None, source_scale=want_scale, reason=_SENTIMENT_SCALED_REASON)

    if key in _SENTIMENT_TEMP:
        lo, hi = _SENTIMENT_TEMP[key]
        if kind == "str":
            raise MigrationError("this temperature sentiment source requires a numeric raw")
        if scale is not None:
            raise MigrationError(
                "temperature source has no reviewed pm1/zero_ten scale; do not supply one")
        if not lo <= raw <= hi:
            raise MigrationError(f"temperature {raw} out of inventoried range [{lo},{hi}]")
        return MigratedSentiment(
            adapter_id="migration.sentiment", source_schema=source_schema, raw=raw,
            raw_kind=kind, mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
            normalized=None, source_scale=None, reason=_SENTIMENT_TEMP_REASON)

    if key in _SENTIMENT_CATEGORICAL:
        allowed = _SENTIMENT_CATEGORICAL[key]
        if kind != "str":
            raise MigrationError("this categorical sentiment source requires a str raw")
        if scale is not None:
            raise MigrationError("a categorical sentiment source takes no scale")
        if raw not in allowed:
            raise MigrationError(f"raw {raw!r} outside categorical domain {sorted(allowed)}")
        return MigratedSentiment(
            adapter_id="migration.sentiment", source_schema=source_schema, raw=raw,
            raw_kind=kind, mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
            normalized=None, source_scale=None, reason=_SENTIMENT_CATEGORICAL_REASON)

    raise MigrationError(f"migrate_sentiment: unknown source schema {key!r}")


def sentiment_to_legacy(migrated: MigratedSentiment) -> LegacyScalar:
    return migrated.raw


# =========================================================================== #
# 12.6 Rotation / legacy market-cycle stage                                    #
# =========================================================================== #
_ROTATION_SOURCES = (SRC_MARKET_CYCLE,)
_LEGACY_CYCLE_VALUES = tuple(e.value for e in LegacyMarketCycleStage)
_ROTATION_UNMAPPED_REASON = (
    "LegacyMarketCycleStage != RotationStage; no evidence-reviewed semantic equivalent "
    "in Task 0. 冰点/逼空/发酵/回踩·启动 must not be collapsed into 启动/扩散/分化/退潮 "
    "by string intuition; UNMAPPABLE until Phase 5")


def migrate_rotation_stage(raw: StrictStr, *, source_schema: SchemaRef) -> MigratedRotationStage:
    """Migrate a legacy ``market_cycle`` stage. UNMAPPABLE until Phase 5.

    ``LegacyMarketCycleStage`` and ``RotationStage`` are different enums; even the
    identical literal ``分化`` is not collapsed without evidence-reviewed meaning.
    """
    _require_source(source_schema, _ROTATION_SOURCES, "migrate_rotation_stage")
    if _raw_kind_strict(raw) != "str":
        raise MigrationError("rotation stage raw must be a strict str")
    if raw not in _LEGACY_CYCLE_VALUES:
        raise MigrationError(
            f"raw {raw!r} is not an inventoried LegacyMarketCycleStage value "
            f"{_LEGACY_CYCLE_VALUES}")
    return MigratedRotationStage(
        adapter_id="migration.rotation_stage", source_schema=source_schema, raw=raw,
        raw_kind="str", mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
        normalized=None, reason=_ROTATION_UNMAPPED_REASON)


def rotation_stage_to_legacy(migrated: MigratedRotationStage) -> str:
    return migrated.raw


# =========================================================================== #
# 12.7a  Closed, deterministic legacy-YAML/JSON normalizer                     #
# =========================================================================== #
def _ensure_json_value(v: Any) -> Any:
    """Deep-validate a JSON value: string keys, finite floats, JSON-native only.

    ``bool`` is allowed (graph config), preserved distinct from ``int``.
    """
    if v is None or isinstance(v, bool) or isinstance(v, str):
        return v
    if isinstance(v, int):  # bool already handled
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("non-finite float is forbidden in legacy graph config")
        return v
    if isinstance(v, list):
        return [_ensure_json_value(x) for x in v]
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, val in v.items():
            if not isinstance(k, str):
                raise ValueError("legacy graph config mapping keys must be strings")
            out[k] = _ensure_json_value(val)
        return out
    raise ValueError(f"unsupported legacy graph config type {type(v).__name__}")


def _ensure_json_object(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object (mapping)")
    return _ensure_json_value(v)


class _StrictGraphYamlLoader(yaml.SafeLoader):
    """A safe loader that rejects everything outside the closed graph-config ABI."""


def _reject_tag(desc: str):
    def _c(loader, node):  # noqa: ANN001
        raise ValueError(f"legacy graph YAML: {desc} is forbidden")
    return _c


_StrictGraphYamlLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", _reject_tag("timestamps/dates"))
_StrictGraphYamlLoader.add_constructor(
    "tag:yaml.org,2002:binary", _reject_tag("binary values"))
_StrictGraphYamlLoader.add_constructor(
    "tag:yaml.org,2002:merge", _reject_tag("merge key '<<'"))


def _construct_float(loader, node):  # noqa: ANN001
    val = yaml.SafeLoader.construct_yaml_float(loader, node)
    if not math.isfinite(val):
        raise ValueError("legacy graph YAML: non-finite float is forbidden")
    return val


_StrictGraphYamlLoader.add_constructor("tag:yaml.org,2002:float", _construct_float)


def _construct_mapping(loader, node, deep=False):  # noqa: ANN001
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("legacy graph YAML: expected a mapping node")
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ValueError("legacy graph YAML: merge key '<<' is forbidden")
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("legacy graph YAML: non-string mapping key")
        if key in mapping:
            raise ValueError(f"legacy graph YAML: duplicate key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictGraphYamlLoader.construct_mapping = _construct_mapping  # type: ignore[assignment]


def _compose_node(self, parent, index):  # noqa: ANN001
    if self.check_event(yaml.events.AliasEvent):
        raise ValueError("legacy graph YAML: aliases are forbidden")
    event = self.peek_event()
    if event is not None and event.anchor is not None:
        raise ValueError("legacy graph YAML: anchors are forbidden")
    return yaml.composer.Composer.compose_node(self, parent, index)


_StrictGraphYamlLoader.compose_node = _compose_node  # type: ignore[assignment]


def _yaml_load_strict(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_StrictGraphYamlLoader)
    except ValueError:
        raise
    except yaml.YAMLError as exc:
        raise ValueError(f"legacy graph YAML parse error: {exc}") from exc


def _json_load_strict(text: str) -> Any:
    def _no_dup(pairs):
        d: dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                raise ValueError(f"legacy graph JSON: duplicate key {k!r}")
            d[k] = v
        return d

    return json.loads(text, object_pairs_hook=_no_dup)  # JSONDecodeError <: ValueError


def normalize_legacy_graph_config(
    raw: "StrictStr | JsonObject", *, source_format: Literal["yaml", "json"]
) -> JsonObject:
    """Closed, deterministic normalization of a legacy graph config to a JsonObject.

    Rejects (before any information is lost): duplicate keys, non-string mapping
    keys, custom tags, timestamps/dates, binary, merge ``<<``, anchors/aliases and
    non-finite floats. JSON scalar types and list order are preserved; mapping
    order is irrelevant (canonicalized by Task 1). ``source_config_digest`` is
    ``content_digest(normalized)``.
    """
    if source_format not in ("yaml", "json"):
        raise MigrationError("source_format must be 'yaml' or 'json'")
    if isinstance(raw, str):
        data = _yaml_load_strict(raw) if source_format == "yaml" else _json_load_strict(raw)
    elif isinstance(raw, dict):
        data = raw
    else:
        raise MigrationError("raw must be a YAML/JSON string or a JsonObject dict")
    if not isinstance(data, dict):
        raise MigrationError("legacy graph config must normalize to a JSON object (mapping)")
    return _ensure_json_object(data)


# =========================================================================== #
# 12.7b  Legacy graph mapping models                                           #
# =========================================================================== #
_SERVICE_BINDING_ALLOWLIST: frozenset[str] = frozenset({"output_locator"})


def _check_item_matrix(m: Any, *, target: Any) -> None:
    """MAPPED/UNMAPPABLE matrix shared by worker + dependency mappings."""
    if m.mapping_status is MappingStatus.MAPPED:
        if target is None:
            raise ValueError("a mapped item requires a target")
        if m.mapping_basis == "none":
            raise ValueError("a mapped item requires basis != 'none'")
        if m.reason is not None:
            raise ValueError("a mapped item must carry no reason")
        if m.mapping_basis == "approved_policy":
            if not m.mapping_policy_id:
                raise ValueError("approved_policy basis requires a mapping_policy_id")
        elif m.mapping_policy_id is not None:
            raise ValueError("mapping_policy_id only with approved_policy basis")
    else:
        if target is not None:
            raise ValueError("an unmappable item requires no target")
        if not m.reason:
            raise ValueError("an unmappable item requires a reason")
        if m.mapping_basis != "none":
            raise ValueError("an unmappable item requires basis 'none'")
        if m.mapping_policy_id is not None:
            raise ValueError("an unmappable item takes no mapping_policy_id")


class LegacyWorkerMapping(DigestModel):
    """One legacy graph node -> reviewed target worker (or UNMAPPABLE)."""

    schema_version: Literal["1"] = "1"
    source_node_id: NonEmptyStr
    raw_node: JsonObject
    target_worker_id: LogicalId | None = None
    mapping_status: MappingStatus
    mapping_basis: MappingBasis
    mapping_policy_id: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None

    @field_validator("raw_node")
    @classmethod
    def _json(cls, v: Any) -> Any:
        return _ensure_json_object(v)

    @model_validator(mode="after")
    def _matrix(self) -> "LegacyWorkerMapping":
        _check_item_matrix(self, target=self.target_worker_id)
        return self


class LegacyDependencyMapping(DigestModel):
    """One legacy hard/soft edge -> reviewed accepted-statuses / behavior / policy."""

    schema_version: Literal["1"] = "1"
    source_upstream_node_id: NonEmptyStr
    source_downstream_node_id: NonEmptyStr
    raw_edge: JsonObject
    source_strength: Literal["hard", "soft"]
    accepted_statuses: tuple[NodeStatus, ...] = ()
    missing_output_behavior: Literal["block", "degrade", "skip", "unknown"]
    target_policy: DependencyPolicy | None = None
    mapping_status: MappingStatus
    mapping_basis: MappingBasis
    mapping_policy_id: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None

    @field_validator("raw_edge")
    @classmethod
    def _json(cls, v: Any) -> Any:
        return _ensure_json_object(v)

    @model_validator(mode="after")
    def _matrix(self) -> "LegacyDependencyMapping":
        _check_item_matrix(self, target=self.target_policy)
        if self.mapping_status is MappingStatus.MAPPED:
            if not self.accepted_statuses:
                raise ValueError("a mapped dependency requires non-empty accepted_statuses")
            if len(set(self.accepted_statuses)) != len(self.accepted_statuses):
                raise ValueError("accepted_statuses must be unique")
            if self.missing_output_behavior == "unknown":
                raise ValueError("a mapped dependency behavior may not be 'unknown'")
        return self


class LegacyInputMapping(DigestModel):
    """One legacy consumer input key -> reviewed target (or UNMAPPABLE).

    ``source_kind`` records the reviewed *origin* (base request field vs upstream
    artifact); a target is never guessed from a matching name.
    """

    schema_version: Literal["1"] = "1"
    source_consumer_node_id: NonEmptyStr
    source_key: NonEmptyStr
    source_kind: Literal["base", "upstream"]
    source_upstream_node_id: NonEmptyStr | None = None
    target_kind: Literal["input_binding", "param", "context", "service_binding"] | None = None
    target_input_binding: LogicalId | None = None
    target_param_key: NonEmptyStr | None = None
    target_context_field: NonEmptyStr | None = None
    target_service_binding: NonEmptyStr | None = None
    upstream_output_schema_ref: SchemaRef | None = None
    projection: Literal["raw", "single_field_unwrap", "model_dump"] = "raw"
    projection_field: NonEmptyStr | None = None
    missing_behavior: Literal["error", "omit", "inject_none", "skip_consumer", "unknown"]
    mapping_status: MappingStatus
    mapping_basis: MappingBasis
    mapping_policy_id: NonEmptyStr | None = None
    mapping_evidence: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _matrix(self) -> "LegacyInputMapping":
        _validate_input_matrix(self)
        return self


def _validate_service_binding(name: str | None) -> None:
    if not name:
        raise ValueError("a service_binding target requires a name")
    if "/" in name or "\\" in name or ":" in name:
        raise ValueError("a caller path/callable is never a service binding")
    if name not in _SERVICE_BINDING_ALLOWLIST:
        raise ValueError(
            f"service binding {name!r} is not in the reviewed allowlist "
            f"{sorted(_SERVICE_BINDING_ALLOWLIST)}")


def _validate_input_matrix(m: LegacyInputMapping) -> None:
    targets = {
        "input_binding": m.target_input_binding,
        "param": m.target_param_key,
        "context": m.target_context_field,
        "service_binding": m.target_service_binding,
    }
    set_targets = [k for k, v in targets.items() if v is not None]

    # -- source_kind field rules ------------------------------------------ #
    if m.source_kind == "base":
        if m.source_upstream_node_id is not None:
            raise ValueError("a base input forbids source_upstream_node_id")
        if m.upstream_output_schema_ref is not None:
            raise ValueError("a base input forbids upstream_output_schema_ref")
        if m.projection != "raw":
            raise ValueError("a base input requires projection='raw'")
    else:  # upstream
        if m.source_upstream_node_id is None:
            raise ValueError("an upstream input requires source_upstream_node_id")

    # -- projection rules ------------------------------------------------- #
    if m.projection == "single_field_unwrap":
        if not m.projection_field:
            raise ValueError("single_field_unwrap requires projection_field")
    elif m.projection_field is not None:
        raise ValueError("projection_field is only for single_field_unwrap")
    if m.projection != "raw" and m.source_kind != "upstream":
        raise ValueError("non-raw projection is only allowed for an upstream input")

    if m.mapping_status is MappingStatus.MAPPED:
        if m.mapping_basis == "none":
            raise ValueError("a mapped input requires basis != 'none'")
        if m.reason is not None:
            raise ValueError("a mapped input must carry no reason")
        if m.mapping_basis == "approved_policy":
            if not m.mapping_policy_id:
                raise ValueError("approved_policy basis requires a mapping_policy_id")
        elif m.mapping_policy_id is not None:
            raise ValueError("mapping_policy_id only with approved_policy basis")
        if not m.mapping_evidence:
            raise ValueError("a mapped input requires non-empty mapping_evidence")
        if m.missing_behavior == "unknown":
            raise ValueError("a mapped input missing_behavior may not be 'unknown'")
        if m.target_kind is None:
            raise ValueError("a mapped input requires a target_kind")
        if len(set_targets) != 1 or set_targets[0] != m.target_kind:
            raise ValueError(
                f"a mapped input must set exactly the {m.target_kind!r} target and no other")
        if m.source_kind == "upstream":
            if m.target_kind != "input_binding":
                raise ValueError("an upstream input must target an input_binding")
            if m.upstream_output_schema_ref is None:
                raise ValueError("a mapped upstream input requires upstream_output_schema_ref")
        elif m.target_kind == "input_binding":
            raise ValueError("a base input cannot target an input_binding")
        if m.target_kind == "service_binding":
            _validate_service_binding(m.target_service_binding)
    else:  # UNMAPPABLE
        if m.target_kind is not None:
            raise ValueError("an unmappable input requires target_kind=None")
        if set_targets:
            raise ValueError("an unmappable input must clear every target field")
        if m.mapping_basis != "none":
            raise ValueError("an unmappable input requires basis 'none'")
        if m.mapping_policy_id is not None:
            raise ValueError("an unmappable input takes no mapping_policy_id")
        if not m.reason:
            raise ValueError("an unmappable input requires a reason")


class LegacyGraphMapping(DigestModel):
    """The full legacy static-graph mapping — migration evidence, never a Plan.

    ``input_mappings`` participate in the semantic digest, so changing any input
    field invalidates a bound compatibility binding/attestation. MAPPED iff every
    nested worker/dependency/input item is mapped; otherwise UNMAPPABLE.
    """

    schema_version: Literal["1"] = "1"
    adapter_version: NonEmptyStr = ADAPTER_VERSION
    source_schema: SchemaRef
    source_format: Literal["yaml", "json"]
    normalized_raw_config: JsonObject
    source_config_digest: DigestHex
    worker_mappings: tuple[LegacyWorkerMapping, ...] = ()
    dependency_mappings: tuple[LegacyDependencyMapping, ...] = ()
    input_mappings: tuple[LegacyInputMapping, ...] = ()
    mapping_status: MappingStatus
    reason: NonEmptyStr | None = None

    @field_validator("normalized_raw_config")
    @classmethod
    def _json(cls, v: Any) -> Any:
        return _ensure_json_object(v)

    @model_validator(mode="after")
    def _verify(self) -> "LegacyGraphMapping":
        _validate_graph_mapping(self)
        return self


def _validate_graph_mapping(g: LegacyGraphMapping) -> None:
    if g.source_config_digest != content_digest(g.normalized_raw_config):
        raise ValueError("source_config_digest does not match normalized_raw_config")

    wids = [w.source_node_id for w in g.worker_mappings]
    if len(set(wids)) != len(wids):
        raise ValueError("worker mappings must have a unique source_node_id")
    dids = [(d.source_upstream_node_id, d.source_downstream_node_id)
            for d in g.dependency_mappings]
    if len(set(dids)) != len(dids):
        raise ValueError("dependency mappings must have a unique (upstream, downstream) key")
    iids = [(i.source_consumer_node_id, i.source_key) for i in g.input_mappings]
    if len(set(iids)) != len(iids):
        raise ValueError("every legacy input key must appear exactly once per consumer")

    all_mapped = (
        all(w.mapping_status is MappingStatus.MAPPED for w in g.worker_mappings)
        and all(d.mapping_status is MappingStatus.MAPPED for d in g.dependency_mappings)
        and all(i.mapping_status is MappingStatus.MAPPED for i in g.input_mappings)
    )
    if g.mapping_status is MappingStatus.MAPPED:
        if not all_mapped:
            raise ValueError(
                "a MAPPED graph requires every worker/dependency/input item mapped")
        if g.reason is not None:
            raise ValueError("a MAPPED graph must carry no summary reason")
        _validate_input_dependency_consistency(g)
    else:
        if all_mapped:
            raise ValueError("an UNMAPPABLE graph must carry at least one unmapped item")
        if not g.reason:
            raise ValueError("an UNMAPPABLE graph requires a summary reason")


def _validate_input_dependency_consistency(g: LegacyGraphMapping) -> None:
    edge_policy: dict[tuple[str, str], DependencyPolicy] = {}
    succ: dict[str, set[str]] = {}
    for d in g.dependency_mappings:
        edge_policy[(d.source_upstream_node_id, d.source_downstream_node_id)] = d.target_policy
        succ.setdefault(d.source_upstream_node_id, set()).add(d.source_downstream_node_id)

    def _is_ancestor(up: str, down: str) -> bool:
        seen: set[str] = set()
        stack = [up]
        while stack:
            cur = stack.pop()
            for nxt in succ.get(cur, ()):
                if nxt == down:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    behavior_by_policy = {
        DependencyPolicy.BLOCK: {"error"},
        DependencyPolicy.DEGRADE: {"omit", "inject_none"},
        DependencyPolicy.SKIP: {"skip_consumer"},
    }
    for i in g.input_mappings:
        if i.source_kind != "upstream" or i.mapping_status is not MappingStatus.MAPPED:
            continue
        up, down = i.source_upstream_node_id, i.source_consumer_node_id
        pol = edge_policy.get((up, down))
        if pol is not None:  # direct dependency row
            if i.missing_behavior not in behavior_by_policy[pol]:
                raise ValueError(
                    f"input {i.source_key!r} of {down!r} missing_behavior "
                    f"{i.missing_behavior!r} is inconsistent with a {pol.value} dependency")
        elif not _is_ancestor(up, down):  # non-direct upstream must be a proven ancestor
            raise ValueError(
                f"upstream input {i.source_key!r} of {down!r} references {up!r}, "
                "which is not a proven transitive ancestor in the legacy graph")


# =========================================================================== #
# 12.7c  migrate_legacy_graph + reverse                                        #
# =========================================================================== #
# Task-0 reviewed node ids for the one inventoried static graph. All 18 map to
# *planned/unresolved* workers in Phase 1 (fixture: runnable=false), so every
# node -> UNMAPPABLE and the whole graph is UNMAPPABLE.
_STOCK_DEEP_DIVE_NODE_IDS: frozenset[str] = frozenset({
    "quote-fetcher", "news-reader", "f10-reader", "market-scanner", "mainline-classifier",
    "morning-brief-writer", "overseas-market-scanner", "sector-rotation-analyzer",
    "news-sentiment", "evidence-loader", "fundamental-analyst", "technical-analyst",
    "whale-analyst", "bull-advocate", "bear-advocate", "risk-officer", "report-writer",
    "introspector",
})

#: reviewed soft-dependency accepted statuses ("any terminal done regardless of ok").
_SOFT_ACCEPTED_STATUSES: tuple[NodeStatus, ...] = (
    NodeStatus.COMPLETED, NodeStatus.DEGRADED, NodeStatus.INCOMPLETE,
    NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.SKIPPED, NodeStatus.CANCELLED,
)


def _hard_dep(up: str, down: str) -> LegacyDependencyMapping:
    return LegacyDependencyMapping(
        source_upstream_node_id=up, source_downstream_node_id=down,
        raw_edge={"strength": "hard"}, source_strength="hard",
        accepted_statuses=(NodeStatus.COMPLETED,), missing_output_behavior="block",
        target_policy=DependencyPolicy.BLOCK, mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None, reason=None)


def _soft_dep(up: str, down: str) -> LegacyDependencyMapping:
    return LegacyDependencyMapping(
        source_upstream_node_id=up, source_downstream_node_id=down,
        raw_edge={"strength": "soft"}, source_strength="soft",
        accepted_statuses=_SOFT_ACCEPTED_STATUSES, missing_output_behavior="degrade",
        target_policy=DependencyPolicy.DEGRADE, mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None, reason=None)


def migrate_legacy_graph(
    raw_config: "StrictStr | JsonObject", *, source_schema: SchemaRef,
    source_format: Literal["yaml", "json"],
) -> LegacyGraphMapping:
    """Convert a reviewed legacy static graph into a :class:`LegacyGraphMapping`.

    Consumes the Task-0 reviewed worker/edge table. For the one inventoried graph
    (``stock-deep-dive``) every node maps to a planned/unresolved worker, so the
    worker and input mappings are UNMAPPABLE and the whole graph is UNMAPPABLE —
    while the hard/soft dependency semantics are still faithfully retained.
    """
    if not isinstance(source_schema, SchemaRef):
        raise MigrationError("source_schema must be a SchemaRef")
    if source_schema != SRC_STOCK_DEEP_DIVE:
        raise MigrationError(f"unknown legacy graph source {source_schema.key!r}")

    normalized = normalize_legacy_graph_config(raw_config, source_format=source_format)
    nodes = normalized.get("nodes")
    if not isinstance(nodes, dict):
        raise MigrationError("legacy graph config requires a 'nodes' mapping")
    node_names = set(nodes.keys())

    worker_maps: list[LegacyWorkerMapping] = []
    dep_maps: list[LegacyDependencyMapping] = []
    input_maps: list[LegacyInputMapping] = []

    for node, spec in nodes.items():
        spec_obj = spec if isinstance(spec, dict) else {}
        deps = spec_obj.get("deps", []) or []
        soft = spec_obj.get("soft_deps", []) or []
        input_keys = spec_obj.get("input_keys", []) or []
        known = node in _STOCK_DEEP_DIVE_NODE_IDS

        worker_maps.append(LegacyWorkerMapping(
            source_node_id=node, raw_node=_ensure_json_object(spec_obj),
            target_worker_id=None, mapping_status=MappingStatus.UNMAPPABLE,
            mapping_basis="none", mapping_policy_id=None,
            reason=("phase-1: node maps to a planned/unresolved worker; no runnable "
                    "WorkerSpec exists yet" if known
                    else f"unknown legacy node {node!r} with no reviewed worker")))

        soft_set = set(soft)
        for up in deps:
            if up in soft_set:
                dep_maps.append(_soft_dep(up, node))
            else:
                dep_maps.append(_hard_dep(up, node))
        # a soft dep listed only in soft_deps (should be a subset, but be robust)
        for up in soft:
            if up not in deps:
                dep_maps.append(_soft_dep(up, node))

        for key in input_keys:
            if key in node_names:  # reviewed upstream-artifact origin
                input_maps.append(LegacyInputMapping(
                    source_consumer_node_id=node, source_key=key, source_kind="upstream",
                    source_upstream_node_id=key, target_kind=None, projection="raw",
                    missing_behavior="unknown", mapping_status=MappingStatus.UNMAPPABLE,
                    mapping_basis="none", mapping_evidence=None,
                    reason="phase-1: upstream artifact target (InputBinding) unresolved"))
            else:  # reviewed base (request) origin
                input_maps.append(LegacyInputMapping(
                    source_consumer_node_id=node, source_key=key, source_kind="base",
                    target_kind=None, projection="raw", missing_behavior="unknown",
                    mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
                    mapping_evidence=None,
                    reason="phase-1: base input target (param/context/service) unresolved"))

    return LegacyGraphMapping(
        adapter_version=ADAPTER_VERSION, source_schema=source_schema,
        source_format=source_format, normalized_raw_config=normalized,
        source_config_digest=content_digest(normalized),
        worker_mappings=tuple(worker_maps), dependency_mappings=tuple(dep_maps),
        input_mappings=tuple(input_maps), mapping_status=MappingStatus.UNMAPPABLE,
        reason=("phase-1: legacy graph maps to planned/unresolved workers; migration "
                "evidence only, not runnable"))


def legacy_graph_to_normalized_config(mapping: LegacyGraphMapping) -> JsonObject:
    """Return the deep-equal canonical JSON-normalized config (incl. UNMAPPABLE)."""
    return _ensure_json_value(mapping.normalized_raw_config)


# =========================================================================== #
# 12.7d  Pure builders — require a FULLY-MAPPED graph                          #
# =========================================================================== #
def compatibility_binding_for(mapping: LegacyGraphMapping) -> CompatibilityBinding:
    """Build the ``compat.*`` :class:`CompatibilityBinding` for a MAPPED graph.

    Binds the graph's semantic digest + ``source_config_digest``. An incomplete /
    unmappable graph cannot produce a binding.
    """
    if not isinstance(mapping, LegacyGraphMapping):
        raise MigrationError("expected a LegacyGraphMapping")
    if mapping.mapping_status is not MappingStatus.MAPPED:
        raise MigrationError(
            "a compatibility binding requires a fully MAPPED legacy graph "
            "(every worker/dependency/input mapped)")
    return CompatibilityBinding(
        legacy_source_schema=mapping.source_schema,
        source_config_digest=mapping.source_config_digest,
        legacy_mapping_digest=mapping.semantic_digest())


def attest_static_legacy_plan(
    mapping: LegacyGraphMapping,
    draft: PlanDraft,
    request: OrchestrationRequest,
    *,
    context: ContextSnapshot | None,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
) -> StaticLegacyPlanAttestation:
    """Pure builder of a :class:`StaticLegacyPlanAttestation` for a MAPPED graph.

    Calls the one :func:`compute_candidate_plan_digest` with the same
    request/draft/context/catalog/registry inputs used by validation (the exact
    budget request already comes from the draft) and binds the graph's semantic
    digest + ``source_config_digest``. No attestation is emitted for a
    partial/unmappable graph.
    """
    if not isinstance(mapping, LegacyGraphMapping):
        raise MigrationError("expected a LegacyGraphMapping")
    if mapping.mapping_status is not MappingStatus.MAPPED:
        raise MigrationError("no attestation for a partial/unmappable legacy graph")
    if draft.source not in (PlanSource.PRESET, PlanSource.PRESET_FALLBACK):
        raise MigrationError(
            "a static legacy attestation is only for preset/preset_fallback plans")

    mapping_digest = mapping.semantic_digest()
    # the draft must reference this exact legacy graph + the supplied snapshots
    if draft.catalog_digest != catalog.catalog_digest:
        raise MigrationError("draft.catalog_digest != supplied catalog digest")
    if draft.schema_registry_digest != schema_registry.registry_digest:
        raise MigrationError("draft.schema_registry_digest != supplied registry digest")
    if draft.legacy_source_schema != mapping.source_schema:
        raise MigrationError("draft.legacy_source_schema != mapping.source_schema")
    if draft.legacy_source_config_digest != mapping.source_config_digest:
        raise MigrationError("draft.legacy_source_config_digest != mapping.source_config_digest")
    if draft.legacy_mapping_digest != mapping_digest:
        raise MigrationError("draft.legacy_mapping_digest != mapping.semantic_digest()")

    ctx_content_digest = context.content_digest if context is not None else None
    candidate = compute_candidate_plan_digest(
        request=request, draft=draft, context_content_digest=ctx_content_digest)
    return StaticLegacyPlanAttestation(
        plan_source=draft.source.value,
        request_digest=request.semantic_digest(),
        candidate_plan_digest=candidate,
        catalog_digest=catalog.catalog_digest,
        legacy_source_schema=mapping.source_schema,
        source_config_digest=mapping.source_config_digest,
        legacy_mapping_digest=mapping_digest,
        builder_id="migration.static_legacy_attestor")
