# -*- coding: utf-8 -*-
"""Phase 8 · lane worker output payloads (the registered ``@1`` DigestModel set).

This module is the **single home** for the typed output payloads the migrated lane
workers publish. It is grown one reviewed batch at a time (Lane C text first, then
Lane B pv, Lane A quant, 跨切, Lane D); every model is a strict, frozen
:class:`~guanlan_v2.orchestration.digest.DigestModel` following the Phase-1 house
rules (``schema_version: Literal["1"] = "1"``, closed ``Literal`` enums, ``tuple``
collections, tz-aware :data:`~guanlan_v2.orchestration.digest.UtcDateTime`, finite
floats, non-blank strings).

Registration into the cumulative schema registry is deliberately **deferred to the
Task-11 registry seal** (the ``PHASE8_*`` chain); defining a payload here does not
register it. The Phase-8 discovery firewall
(``tests/orchestration/test_contract_completeness.py``) already lists this module
under ``PHASE8_MODULES`` so a new public contract here is inert until Task 11
reviews it into the Phase-8 public/internal partition.

Honesty is structural: every report carries an explicit shortfall channel — a
``coverage_note`` / ``degradation`` tuple, an ``unknown`` stance, a nullable
``board_temp`` — so a coverage gap is rendered as UNAVAILABLE, never back-filled
with a zero or a fabricated directional read.

Lane C · 文本 (Task 4)
---------------------
* ``NewsDigestItem`` / ``NewsDigestReport`` — ``text.news`` realtime/RSS digest.
* ``ExtractedClaim`` / ``ResearchReportExtract`` — ``text.research_report`` Kimi
  extraction + 旧报降权 (staleness downweight).
* ``PolicyEntry`` / ``PolicyReport`` — ``text.policy`` official-wording read.
* ``PredictionMarketRead`` / ``MacroPulseReport`` — ``text.macro`` macro pulse.

Lane B · 量价几何 (Task 5)
-------------------------
* ``PriceActionFeatureReport`` — ``pv.price_action`` deterministic per-bar geometry;
  its ``features`` are the numeric (``FiniteFloat``) projection named by the extensible
  ``pa-15key-v1`` feature-set registry (:data:`PA_FEATURE_SET_KEYS`). The categorical /
  structural ``compute_pa_features`` keys (bar_type/breakout/limit/gap/follow/recent/date)
  are NOT ``FiniteFloat`` and are never coerced into the numeric ``features`` dict; the
  handler carries the full 15-key geometry verbatim (前后端镜像逐位一致 red line).
* ``IndicatorReading`` / ``TechnicalReport`` — ``pv.technical`` ≤8 complementary
  indicators read against a ``verified_anchor_digest`` truth anchor + an honest bias.
* ``MicrostructureReport`` — ``pv.microstructure`` L1-book / tick / tape projection with
  a non-fabricated ``degradation`` channel for every absent optional feed.
* ``PatternLifecycleProposal`` — the #27 ``pv.curator`` draft-only output (``draft_only``
  is ``Literal[True]`` — a proposal can NEVER be constructed adopted; ``trigger_evidence``
  is non-empty — 不许"顺手优化"; ``source_label`` records an external feed's 来源作者).

Lane A · 量化 (Task 6)
---------------------
* ``FactorICRow`` / ``FactorICReport`` — ``quant.factor`` measured factor rank-IC. ``oos``
  is an explicit bool: the 帷幄 ``factor_ic`` source is a 近窗回看 IC, so a look-back number
  MUST NOT masquerade as a PIT-OOS one (badge-adjacent honesty). Rows are sorted by
  ``factor_id`` and duplicate-free.
* ``ModelScoreRow`` / ``ModelPredictionReport`` — ``quant.model`` v4+DL ensemble ranking;
  ranks are unique + strictly ascending, and ``stale_days`` (DL 断供显形) is carried
  verbatim so a stale/absent DL feed is never hidden behind a zero.
* ``BacktestEvidenceReport`` — ``quant.backtest`` vintage-IC / OOS-verdict / PBO card; every
  metric is independently nullable so a not-yet-matured realized-date OOS window renders
  ``oos_verdict=None`` (UNAVAILABLE), never a fabricated "pass". ``pbo`` ∈ [0, 1].
* ``FundamentalsReport`` — ``quant.fundamentals`` valuation + market-value tier + profit
  forecast provenance; ``inputs_complete`` reads the honest presence of the core inputs
  (an absent field is ``None``, never fabricated).
* ``MinedFactorDraft`` — the ``quant.factor_miner`` deterministic mined-factor DRAFT
  (``draft_only`` is ``Literal[True]`` — factorlib promotion stays human; ``passed_gate``
  records the research-loop Sharpe/robust 门 verdict verbatim, never upgraded).
* ``FactorLifecycleProposal`` — the #26 ``quant.curator`` draft-only lifecycle proposal
  (``draft_only`` is ``Literal[True]``; ``trigger_evidence`` non-empty — 不许"顺手优化";
  ``proposed_expr`` is revision_draft-only — 表达式只进 candidate、不进 study 身份).
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.pattern_registry import PatternDefinition
from guanlan_v2.orchestration.refs import ContentRef, LogicalId

__all__ = [
    # Lane C · text
    "NewsDigestItem",
    "NewsDigestReport",
    "ExtractedClaim",
    "ResearchReportExtract",
    "PolicyEntry",
    "PolicyReport",
    "PredictionMarketRead",
    "MacroPulseReport",
    # Lane B · price-volume geometry
    "PA_FEATURE_SET_KEYS",
    "PriceActionFeatureReport",
    "IndicatorReading",
    "TechnicalReport",
    "MicrostructureReport",
    "PatternLifecycleProposal",
    # Lane A · quant
    "FactorICRow",
    "FactorICReport",
    "ModelScoreRow",
    "ModelPredictionReport",
    "BacktestEvidenceReport",
    "FundamentalsReport",
    "MinedFactorDraft",
    "FactorLifecycleProposal",
    # partition helpers (for the Task-11 Phase-8 firewall)
    "LANE_C_PUBLIC_MODELS",
    "LANE_B_PUBLIC_MODELS",
    "LANE_A_PUBLIC_MODELS",
]

#: a finite float constrained to the closed unit interval ``[0, 1]`` (bool/NaN/Inf
#: already rejected by :data:`FiniteFloat`).
UnitFloat = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]


# --------------------------------------------------------------------------- #
# text.news — NewsDigestReport                                                 #
# --------------------------------------------------------------------------- #
class NewsDigestItem(DigestModel):
    """One de-duplicated news item in a :class:`NewsDigestReport`.

    ``published_at`` / ``available_at`` are independently nullable: an item may be
    known to exist (available at a given as_of) without a trustworthy publish
    timestamp, or vice versa — neither is fabricated to fill the other.
    """

    schema_version: Literal["1"] = "1"
    headline: NonEmptyStr
    summary: NonEmptyStr
    source_label: NonEmptyStr
    published_at: UtcDateTime | None = None
    available_at: UtcDateTime | None = None
    codes: tuple[NonEmptyStr, ...] = ()


class NewsDigestReport(DigestModel):
    """A calibrated news digest for a name, the market, or both.

    ``coverage_note`` is the honest shortfall channel: an empty ``items`` tuple with
    a stated ``coverage_note`` means the feeds were quiet or unavailable — never a
    fabricated headline to appear complete.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    scope: Literal["stock", "market", "both"]
    items: tuple[NewsDigestItem, ...]
    coverage_note: NonEmptyStr | None = None


# --------------------------------------------------------------------------- #
# text.research_report — ResearchReportExtract                                 #
# --------------------------------------------------------------------------- #
class ExtractedClaim(DigestModel):
    """One extracted claim from a research report, tagged by evidence class.

    ``anchored`` records whether the claim is tied to a concrete figure/quote in the
    source excerpt; an unanchored opinion may corroborate direction but never
    establishes it.
    """

    schema_version: Literal["1"] = "1"
    text: NonEmptyStr
    kind: Literal["forecast", "fact", "opinion"]
    anchored: bool


class ResearchReportExtract(DigestModel):
    """A machine extraction of one research report, down-weighted by staleness.

    ``report_age_days`` is the report's age in trading/calendar days; older reports
    carry a smaller ``staleness_downweight`` (旧报降权) in ``[0, 1]`` so a stale
    call cannot dominate a fresh read. ``claims`` may be empty (a report with no
    extractable, attributable claim is an honest empty extraction, not a guess).
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    source_report_label: NonEmptyStr
    report_age_days: NonNegativeInt
    staleness_downweight: UnitFloat
    claims: tuple[ExtractedClaim, ...]


# --------------------------------------------------------------------------- #
# text.policy — PolicyReport                                                    #
# --------------------------------------------------------------------------- #
class PolicyEntry(DigestModel):
    """One policy / window-guidance item.

    ``effective_hint`` is the nullable free-text timing hint (e.g. "自 2026 年
    起施行") — nullable because many announcements state no effective date, and one
    is never invented.
    """

    schema_version: Literal["1"] = "1"
    title: NonEmptyStr
    summary: NonEmptyStr
    effective_hint: NonEmptyStr | None = None
    source_label: NonEmptyStr


class PolicyReport(DigestModel):
    """A read of the current policy stance from official wording.

    ``stance`` includes an explicit ``"unknown"`` so a coverage shortfall is honest
    (UNAVAILABLE 不补零): a thin or absent policy signal is ``unknown``, never
    silently coerced to ``neutral``.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    stance: Literal["supportive", "neutral", "restrictive", "unknown"]
    entries: tuple[PolicyEntry, ...]


# --------------------------------------------------------------------------- #
# text.macro — MacroPulseReport                                                 #
# --------------------------------------------------------------------------- #
class PredictionMarketRead(DigestModel):
    """One prediction-market probability read (PM / Kalshi), display-only.

    ``probability`` is a finite value in ``[0, 1]``; ``direction_hint`` names the
    market's anchored direction so the probability is never read against an
    ambiguous sign.
    """

    schema_version: Literal["1"] = "1"
    market_label: NonEmptyStr
    probability: UnitFloat
    direction_hint: NonEmptyStr


class MacroPulseReport(DigestModel):
    """A macro sentiment pulse: prediction-market reads + A-share board temperature.

    ``board_temp`` is nullable and ``degradation`` is a non-fabricated shortfall
    channel: when the 打板温度 block or an overseas source is unavailable, the read
    says so (``board_temp=None`` and/or a ``degradation`` entry) rather than
    imputing a temperature. ``narrative`` ties the composite read to its blocks.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    prediction_markets: tuple[PredictionMarketRead, ...]
    board_temp: FiniteFloat | None
    degradation: tuple[NonEmptyStr, ...] = ()
    narrative: NonEmptyStr


#: The Lane C public payload models (the Task-11 Phase-8 firewall reviews this set
#: into the cumulative registry; later batches extend it in place).
LANE_C_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    NewsDigestItem,
    NewsDigestReport,
    ExtractedClaim,
    ResearchReportExtract,
    PolicyEntry,
    PolicyReport,
    PredictionMarketRead,
    MacroPulseReport,
)


# =========================================================================== #
# Lane B · 量价几何 (Task 5)                                                   #
# =========================================================================== #
# --------------------------------------------------------------------------- #
# pv.price_action — PriceActionFeatureReport                                    #
# --------------------------------------------------------------------------- #
#: The extensible price-action feature-set registry (AMEND-1-style 15键→N键): a
#: ``feature_set_version`` → the exact ``FiniteFloat`` feature-key vocabulary a
#: :class:`PriceActionFeatureReport` of that version carries. ``"pa-15key-v1"`` is the
#: numeric projection of ``compute_pa_features`` (guanlan_v2/seats/price_action.py):
#: the seven float bar-geometry keys plus ``inside_streak`` (a count rendered as a
#: float). The five categorical keys (bar_type/breakout/limit/gap/follow) and the two
#: structural keys (date/recent) are NOT finite floats and are therefore NOT part of the
#: numeric ``features`` dict — the deterministic handler carries the full 15-key geometry
#: verbatim (前后端镜像逐位一致 red line), and a categorical fact is never fabricated into a
#: number here. The version LABEL keeps the historical "15key" name (the source geometry
#: is 15 keys); registering a superseding "N-key" version is an in-place map extension.
PA_FEATURE_SET_KEYS: dict[str, tuple[str, ...]] = {
    "pa-15key-v1": (
        "body",
        "close_pos",
        "ema20_rel",
        "inside_streak",
        "lower_wick",
        "range_atr",
        "upper_wick",
        "vol_ratio",
    ),
}


class PriceActionFeatureReport(DigestModel):
    """The deterministic per-bar price-volume geometry for a name's decision bar.

    ``features`` are keyed EXACTLY by the registry vocabulary of ``feature_set_version``
    (:data:`PA_FEATURE_SET_KEYS`) — a missing/extra/foreign key is a validation error, so
    a partial-history bar (some numeric key still ``None``) yields no report rather than a
    silently truncated one. ``patterns`` are ``pattern_id@definition_version`` hits from
    the Task-4b dictionary; they stay ``()`` until the separately-chartered recognizer
    task lands, and are NEVER fabricated. ``methodology_ref`` (可编辑方法论) is opt-in.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    feature_set_version: NonEmptyStr
    features: dict[NonEmptyStr, FiniteFloat]
    patterns: tuple[NonEmptyStr, ...] = ()
    methodology_ref: ContentRef | None = None

    @model_validator(mode="after")
    def _validate(self) -> "PriceActionFeatureReport":
        keys = PA_FEATURE_SET_KEYS.get(self.feature_set_version)
        if keys is None:
            raise ValueError(
                f"unknown feature_set_version {self.feature_set_version!r}; "
                f"known versions: {sorted(PA_FEATURE_SET_KEYS)}"
            )
        if set(self.features) != set(keys):
            raise ValueError(
                f"features keys must be exactly the {self.feature_set_version!r} registry "
                f"keys {tuple(keys)!r}; got {tuple(sorted(self.features))!r}"
            )
        for hit in self.patterns:
            pid, sep, ver = hit.partition("@")
            if not sep or not pid.strip() or not ver.strip():
                raise ValueError(
                    f"pattern hit {hit!r} must be 'pattern_id@definition_version' "
                    "(a bare id is never a dictionary hit)"
                )
        return self


# --------------------------------------------------------------------------- #
# pv.technical — TechnicalReport                                                #
# --------------------------------------------------------------------------- #
class IndicatorReading(DigestModel):
    """One named technical-indicator reading (a finite value + an optional note)."""

    schema_version: Literal["1"] = "1"
    name: NonEmptyStr
    value: FiniteFloat
    note: NonEmptyStr | None = None


class TechnicalReport(DigestModel):
    """A multi-indicator technical read anchored to a verified-snapshot truth anchor.

    ``indicators`` is 1–8 complementary readings with unique names (≤8 互补指标 — a wall
    of redundant oscillators is not a read). ``verified_anchor_digest`` is the content
    digest of the ``VerifiedSnapshotDataResult`` the numbers were read against (``None``
    when no verified anchor was available — recorded honestly, never spoofed). ``bias``
    carries an explicit ``"unknown"`` so a thin read is not coerced to ``"neutral"``.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    indicators: tuple[IndicatorReading, ...]
    verified_anchor_digest: DigestHex | None
    bias: Literal["bullish", "bearish", "neutral", "unknown"]
    summary: NonEmptyStr

    @model_validator(mode="after")
    def _validate(self) -> "TechnicalReport":
        n = len(self.indicators)
        if not (1 <= n <= 8):
            raise ValueError(
                f"a TechnicalReport carries 1–8 complementary indicators; got {n}"
            )
        names = [i.name for i in self.indicators]
        if len(set(names)) != len(names):
            raise ValueError("indicator names must be unique")
        return self


# --------------------------------------------------------------------------- #
# pv.microstructure — MicrostructureReport                                      #
# --------------------------------------------------------------------------- #
class MicrostructureReport(DigestModel):
    """An L1-book / tick / tape microstructure projection for a name.

    Every metric is independently nullable and ``degradation`` is the non-fabricated
    shortfall channel (orderbook 空档降级 precedent): when the L1 book / tick / tape /
    fund-flow feed is absent, the corresponding metric is ``None`` AND the absence is
    named in ``degradation`` — a down feed is never back-filled with a zero or an imputed
    imbalance. ``narrative`` ties the composite read to the feeds that were present.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    l1_spread_bp: FiniteFloat | None
    bid_ask_imbalance: FiniteFloat | None
    break_ratio: FiniteFloat | None
    whale_net_inflow: FiniteFloat | None
    degradation: tuple[NonEmptyStr, ...] = ()
    narrative: NonEmptyStr


# --------------------------------------------------------------------------- #
# pv.curator (#27) — PatternLifecycleProposal                                   #
# --------------------------------------------------------------------------- #
class PatternLifecycleProposal(DigestModel):
    """The #27 ``pv.curator`` draft-only lifecycle proposal (AMEND-6/6a).

    Two-route discipline (两路分流): a ``"pattern_definition"`` proposal carries a
    computable :class:`~guanlan_v2.orchestration.pattern_registry.PatternDefinition`
    ((a) 路 — evaluated by historical daily-bar replay); a ``"skill_diff"`` proposal
    carries a ``skill_diff_summary`` for the non-computable 判读方法论 ((b) 路 — routed
    through the A/B shadow + matured 门). A ``"retirement"`` proposal names the
    ``pattern_id`` to retire.

    Load-bearing red lines: ``draft_only`` is ``Literal[True]`` — a proposal can NEVER be
    constructed as adopted (adoption is always 人审, downstream of this schema);
    ``trigger_evidence`` is non-empty (不许"顺手优化"); ``source_label`` records the 来源
    作者 whenever the proposal originates from an external technical-analysis feed (外部
    投喂 = 不可信数据 — its text is never elevated to a system instruction).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    kind: Literal["pattern_definition", "skill_diff", "retirement"]
    pattern_id: LogicalId | None = None
    proposed_definition: PatternDefinition | None = None
    skill_diff_summary: NonEmptyStr | None = None
    source_label: NonEmptyStr | None = None
    trigger_evidence: tuple[NonEmptyStr, ...]
    draft_only: Literal[True] = True

    @model_validator(mode="after")
    def _validate(self) -> "PatternLifecycleProposal":
        if not self.trigger_evidence:
            raise ValueError(
                "trigger_evidence must be non-empty — a proposal records why it fires "
                "(不许\"顺手优化\")"
            )
        if self.kind == "pattern_definition":
            if self.proposed_definition is None:
                raise ValueError(
                    "a 'pattern_definition' proposal requires a proposed_definition ((a) 路)"
                )
        elif self.kind == "skill_diff":
            if self.skill_diff_summary is None:
                raise ValueError(
                    "a 'skill_diff' proposal requires a skill_diff_summary ((b) 路)"
                )
        else:  # retirement
            if self.pattern_id is None:
                raise ValueError("a 'retirement' proposal requires the pattern_id to retire")
        return self


#: The Lane B public payload models (the Task-11 Phase-8 firewall reviews this set into
#: the cumulative registry alongside :data:`LANE_C_PUBLIC_MODELS`).
LANE_B_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    PriceActionFeatureReport,
    IndicatorReading,
    TechnicalReport,
    MicrostructureReport,
    PatternLifecycleProposal,
)


# =========================================================================== #
# Lane A · 量化 (Task 6)                                                       #
# =========================================================================== #
# --------------------------------------------------------------------------- #
# quant.factor — FactorICReport                                                 #
# --------------------------------------------------------------------------- #
class FactorICRow(DigestModel):
    """One factor's measured information coefficient for a window.

    ``ic`` is the measured IC and ``rank_ic`` the rank-IC (nullable — some sources
    report only one). ``oos`` is the load-bearing honesty flag: the 帷幄 ``factor_ic``
    source is a 近窗回看 IC, so a look-back number MUST carry ``oos=False`` — a 回看 IC
    must never masquerade as a PIT-OOS one (badge-adjacent honesty; documented for the
    Task-8 gate-metric materials so an ``oos=False`` row never satisfies an OOS-labeled
    downstream gate).
    """

    schema_version: Literal["1"] = "1"
    factor_id: LogicalId
    ic: FiniteFloat
    rank_ic: FiniteFloat | None = None
    window: NonEmptyStr
    oos: bool


class FactorICReport(DigestModel):
    """A measured-IC report over the factor catalog for a decision as_of.

    ``rows`` are sorted by ``factor_id`` and duplicate-free — a factor that could not be
    computed is an honest absent row (前端 显示「—」), never a fabricated decorative IC.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    rows: tuple[FactorICRow, ...]

    @model_validator(mode="after")
    def _validate(self) -> "FactorICReport":
        ids = [r.factor_id for r in self.rows]
        if ids != sorted(ids):
            raise ValueError("FactorICReport rows must be sorted by factor_id")
        if len(set(ids)) != len(ids):
            raise ValueError("FactorICReport rows must be duplicate-free by factor_id")
        return self


# --------------------------------------------------------------------------- #
# quant.model — ModelPredictionReport                                           #
# --------------------------------------------------------------------------- #
class ModelScoreRow(DigestModel):
    """One name's model score + its (1-based) rank within the prediction."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    score: FiniteFloat
    rank: PositiveInt


class ModelPredictionReport(DigestModel):
    """A v4+DL ensemble ranking as of a decision date.

    ``model_asof`` records the model's own vintage and ``stale_days`` (DL 断供显形) the
    freshness gap in days — carried verbatim so a stale or absent DL feed is never hidden
    behind a zero. ``rows`` ranks are unique and strictly ascending (a rank collision is a
    ranking bug, not a tie); an empty ``rows`` is an honest empty prediction.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    model_id: NonEmptyStr
    model_asof: UtcDateTime
    rows: tuple[ModelScoreRow, ...]
    stale_days: NonNegativeInt

    @model_validator(mode="after")
    def _validate(self) -> "ModelPredictionReport":
        ranks = [r.rank for r in self.rows]
        for a, b in zip(ranks, ranks[1:]):
            if b <= a:
                raise ValueError("ModelPredictionReport ranks must be strictly ascending")
        if len(set(ranks)) != len(ranks):
            raise ValueError("ModelPredictionReport ranks must be unique")
        return self


# --------------------------------------------------------------------------- #
# quant.backtest — BacktestEvidenceReport                                       #
# --------------------------------------------------------------------------- #
class BacktestEvidenceReport(DigestModel):
    """A vintage-IC / OOS-verdict / PBO evidence card for a subject.

    Every metric is independently nullable and honest: a not-yet-matured realized-date
    OOS window renders ``oos_verdict=None`` (UNAVAILABLE) with the gap named in
    ``caveats`` — never a fabricated "pass". ``pbo`` (probability of backtest overfitting)
    is a finite value in ``[0, 1]``.
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    subject: NonEmptyStr
    vintage_ic: FiniteFloat | None
    oos_verdict: NonEmptyStr | None
    pbo: UnitFloat | None
    caveats: tuple[NonEmptyStr, ...] = ()


# --------------------------------------------------------------------------- #
# quant.fundamentals — FundamentalsReport                                       #
# --------------------------------------------------------------------------- #
class FundamentalsReport(DigestModel):
    """A fundamentals read: valuation percentile, market-value tier, profit forecast.

    Every derived field is nullable and ``inputs_complete`` reflects the honest presence
    of the core inputs (valuation + market-value tier): a missing field is ``None`` and
    ``inputs_complete=False``, never a fabricated score. ``profit_forecast_note`` records
    the astock ``get_profit_forecast`` provenance when present.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    valuation_score: FiniteFloat | None
    mv_tier: NonEmptyStr | None
    profit_forecast_note: NonEmptyStr | None
    inputs_complete: bool


# --------------------------------------------------------------------------- #
# quant.factor_miner — MinedFactorDraft                                         #
# --------------------------------------------------------------------------- #
class MinedFactorDraft(DigestModel):
    """A deterministic mined-factor DRAFT from the research-loop Sharpe/robust 门.

    Load-bearing red line: ``draft_only`` is ``Literal[True]`` — a mined factor can NEVER
    be constructed as promoted (factorlib promotion is always 人审, downstream of this
    schema). ``passed_gate`` records the gate verdict verbatim (a failed gate is a failed
    gate — never upgraded); ``sharpe`` / ``robust`` are nullable (a gate may fire on
    ``rank_ic`` alone).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    factor_expr: NonEmptyStr
    rank_ic: FiniteFloat
    sharpe: FiniteFloat | None = None
    robust: FiniteFloat | None = None
    passed_gate: bool
    draft_only: Literal[True] = True


# --------------------------------------------------------------------------- #
# quant.curator (#26) — FactorLifecycleProposal                                 #
# --------------------------------------------------------------------------- #
class FactorLifecycleProposal(DigestModel):
    """The #26 ``quant.curator`` draft-only factor-lifecycle proposal (AMEND-5).

    Four proposal-only 职能 (R2 §5): ``decay_alert`` (衰减警报), ``revision_draft`` (修订
    draft — ``factor_id`` 不变, a new ``proposed_expr`` that runs through the miner's same
    deterministic evaluation pipeline → a new ``definition_version`` downstream),
    ``retirement`` (退役提案), ``portfolio_trigger`` (组合触发 — v4 变体重训 / 族权重调整).

    Load-bearing red lines: ``draft_only`` is ``Literal[True]`` — a proposal can NEVER be
    constructed as adopted (adoption is always 人审, downstream); ``trigger_evidence`` is
    non-empty (不许"顺手优化" — every revision cites the specific evidence artifact that
    fired it); ``proposed_expr`` is revision_draft-only (表达式只进 candidate、不进 study
    身份 — 修订族恒等约定, Phase-4 契约).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    kind: Literal["decay_alert", "revision_draft", "retirement", "portfolio_trigger"]
    factor_id: LogicalId
    definition_version: NonEmptyStr
    proposed_expr: NonEmptyStr | None = None
    trigger_evidence: tuple[NonEmptyStr, ...]
    draft_only: Literal[True] = True

    @model_validator(mode="after")
    def _validate(self) -> "FactorLifecycleProposal":
        if not self.trigger_evidence:
            raise ValueError(
                "trigger_evidence must be non-empty — a proposal cites the evidence "
                "artifact that fired it (不许\"顺手优化\")"
            )
        if self.kind == "revision_draft":
            if self.proposed_expr is None:
                raise ValueError(
                    "a 'revision_draft' proposal requires a proposed_expr (走 miner 同一"
                    "确定性求值管道)"
                )
        elif self.proposed_expr is not None:
            raise ValueError(
                "proposed_expr is revision_draft-only; a "
                f"{self.kind!r} proposal must not carry one"
            )
        return self


#: The Lane A public payload models (the Task-11 Phase-8 firewall reviews this set into
#: the cumulative registry alongside :data:`LANE_C_PUBLIC_MODELS` / :data:`LANE_B_PUBLIC_MODELS`).
LANE_A_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    FactorICRow,
    FactorICReport,
    ModelScoreRow,
    ModelPredictionReport,
    BacktestEvidenceReport,
    FundamentalsReport,
    MinedFactorDraft,
    FactorLifecycleProposal,
)
