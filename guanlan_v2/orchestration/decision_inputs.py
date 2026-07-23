# -*- coding: utf-8 -*-
"""Phase 8 · Task 9b — Lane D deterministic injection faces (AMEND-8 + 交付物③装配说明).

The six runtime injection faces the Lane D decision/risk seats read are all
**deterministic, typed, when-supplied optional** pre-inputs: the pilot chain
(sentiment→mgr→pm, no debate) runs unchanged when every face is absent. This module
owns the ones with machinery to build — the deterministic **pre-input adapters** (R11
Phase-3 memory-bridge 同构): typed payloads in, typed injection faces out, pure
functions, **zero LLM reservations**. It joins no worker roster, changes no 27-count,
and registers no capability.

The three structural rulings this module embodies (R2 §8.1):

1. **The hard-constraint layer is not the LLM** (ai-hedge-fund ``risk_manager`` zero-prompt
   precedent). CRO hard rules (游资票否决 / 恶性事件否决) + A股 制度 constraints
   (T+1 / 涨跌停一字 / 手数 / 停牌 / ST 减仓) are computed DETERMINISTICALLY, *before*
   ``dec.pm``, into :class:`AllowedActions`: per symbol 可否买卖 / 手数 / 最大目标仓位带.
   The block is marked ``already_validated`` — the LLM **only SELECTS from the allowed set,
   never computes it**. The maximum-target-weight vocabulary is Phase 6's exported
   :data:`~guanlan_v2.orchestration.shadow.TARGET_WEIGHT_BANDS`, imported by object
   identity (R7: 仓位带词表由 Phase 6 冻结 — never redefined here); an allowance can never
   carry a weight outside it (validator-enforced).
2. **Upstream ratings consistency is deterministic** — :func:`extract_upstream_ratings`
   normalizes raw analyst labels into a closed 5-band scale and computes the *majority
   tilt* deterministically, so ``dec.research_mgr``'s SKILL consistency-check compares its
   verdict against a typed extract (背离多数倾向须点名分歧, 禁静默漂移), not free prose.
3. **Announcement risk is a deterministic pre-input** — :func:`scan_announcement_risk`
   applies a closed 词表 + 排除词 + 三层烈度 (立案调查 > 问询函 > 关注函) + hard veto
   flags; the tier-1 veto binds ``dec.pm`` (veto 在场 rating 封顶 Hold).

Face ⑤ (教训块 PIT 配方) is *configuration of the existing Phase-3 memory bridge, no new
machinery*: :data:`PAST_LESSONS_RECIPE` names the reviewed parameters (同票近 5 全文 +
跨票近 3 只反思, matured-only, pending 不注入, payload 必列 ``lesson_id``) and
:func:`select_past_lessons` is a pure post-filter over already-surfaced records — it opens
no store and performs no retrieval.

Faces ① (辩论历史块 = ``DebateTranscript@1``) and ⑥ (风险三席输出 = the riskdebate
transcript + ``opponent_stances`` many-binding) have nothing to build here (免建); Task 10
wires all six as optional inputs.

Registration is deliberately **deferred to the Task-11 registry seal**: this module is
already listed under ``PHASE8_MODULES`` in
``tests/orchestration/test_contract_completeness.py``, so the three public faces
(:data:`DECISION_INPUT_PUBLIC_MODELS`) and their three nested value objects
(:data:`DECISION_INPUT_INTERNAL_MODELS`) are inert contracts until Task 11 reviews them
into the Phase-8 public/internal partition. The adapter INPUTS and the face-⑤
recipe/block are plain frozen dataclasses — pre-input configuration, not registered
contracts — so they never enter the contract firewall at all.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    PositiveInt,
    UtcDateTime,
)

# R7: the target-weight band vocabulary is Phase 6's frozen export — imported by object
# identity and NEVER redefined here. ``AllowedActions`` reads this exact tuple so a copy
# can never drift from the proposal/intent band vocabulary the shadow consumer enforces.
from guanlan_v2.orchestration.shadow import TARGET_WEIGHT_BANDS

__all__ = [
    # Face ② — upstream ratings
    "RATING_BANDS",
    "RATING_LABEL_LEXICON",
    "RatingBand",
    "UpstreamRatingRow",
    "UpstreamRatingsExtract",
    "normalize_rating_label",
    "extract_upstream_ratings",
    # Face ③ — allowed actions (the deterministic hard-constraint guardrail)
    "TARGET_WEIGHT_BANDS",
    "LimitStatus",
    "SymbolConstraintFacts",
    "SymbolAllowance",
    "AllowedActions",
    "game_capital_veto",
    "compute_allowance",
    "build_allowed_actions",
    # Face ④ — announcement risk
    "ANNOUNCEMENT_TIER1",
    "ANNOUNCEMENT_TIER2",
    "ANNOUNCEMENT_TIER3",
    "ANNOUNCEMENT_VETO_TERMS",
    "ANNOUNCEMENT_EXCLUSIONS",
    "AnnouncementTier",
    "AnnouncementFlag",
    "AnnouncementRiskFlags",
    "scan_announcement_risk",
    # Face ⑤ — past-lessons PIT recipe (parameterizes the Phase 3 memory bridge)
    "PastLessonsRecipe",
    "PAST_LESSONS_RECIPE",
    "LessonCandidate",
    "PastLessonsBlock",
    "select_past_lessons",
    # partition helpers (for the Task-11 Phase-8 firewall)
    "DECISION_INPUT_PUBLIC_MODELS",
    "DECISION_INPUT_INTERNAL_MODELS",
]


# =========================================================================== #
# Face ② · UpstreamRatingsExtract — deterministic analyst-rating extraction      #
# =========================================================================== #
#: the closed five-band rating scale, ordered most-bullish → most-bearish. It mirrors the
#: ``dec.research_mgr`` five-band rubric (Buy/Overweight/Hold/Underweight/Sell) so the
#: SKILL's consistency check compares its verdict against the same vocabulary. The order is
#: load-bearing: :func:`extract_upstream_ratings` reports tie bands in this canonical order.
RATING_BANDS: tuple[str, ...] = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
RatingBand = Literal["Buy", "Overweight", "Hold", "Underweight", "Sell"]

#: the closed CN/EN raw-label → band normalization lexicon. Keys are matched case-folded
#: and stripped; a label NOT in this table normalizes to ``None`` (honestly UNMAPPED — a
#: rating we cannot machine-read is never guessed into a band and never votes in the tilt).
RATING_LABEL_LEXICON: dict[str, RatingBand] = {
    # Buy
    "买入": "Buy", "强烈推荐": "Buy", "推荐": "Buy", "强推": "Buy",
    "buy": "Buy", "strong buy": "Buy",
    # Overweight
    "增持": "Overweight", "跑赢行业": "Overweight", "优于大市": "Overweight",
    "谨慎推荐": "Overweight", "accumulate": "Overweight", "add": "Overweight",
    "overweight": "Overweight", "outperform": "Overweight",
    # Hold
    "中性": "Hold", "持有": "Hold", "同步大市": "Hold",
    "hold": "Hold", "neutral": "Hold", "market perform": "Hold",
    # Underweight
    "减持": "Underweight", "跑输行业": "Underweight", "弱于大市": "Underweight",
    "underweight": "Underweight", "reduce": "Underweight", "underperform": "Underweight",
    # Sell
    "卖出": "Sell", "sell": "Sell", "strong sell": "Sell",
}


def normalize_rating_label(raw: str) -> RatingBand | None:
    """Normalize a raw analyst label into a :data:`RatingBand`, or ``None`` if unmapped.

    Case-folded, whitespace-stripped exact lookup against :data:`RATING_LABEL_LEXICON`.
    An unmapped label returns ``None`` — a machine cannot read it, so it is honestly
    absent rather than guessed into a band.
    """
    if not isinstance(raw, str):
        return None
    return RATING_LABEL_LEXICON.get(raw.strip().casefold())


class UpstreamRatingRow(DigestModel):
    """One upstream analyst reading: source, verbatim label, normalized band, optionals.

    ``band`` is the deterministically normalized :data:`RatingBand` or ``None`` when the
    raw label could not be machine-read (never guessed). ``raw_label`` preserves the
    verbatim reported text for audit; ``score`` / ``target_price`` are honestly nullable.
    """

    schema_version: Literal["1"] = "1"
    source: NonEmptyStr
    raw_label: NonEmptyStr
    band: RatingBand | None = None
    score: FiniteFloat | None = None
    target_price: FiniteFloat | None = Field(default=None, gt=0)


class UpstreamRatingsExtract(DigestModel):
    """The typed upstream-ratings block (face ②) with a deterministic majority tilt.

    ``rows`` are sorted by ``source`` and duplicate-free (one reading per source per
    extract — a source graded twice is a caller bug). ``majority_tilt`` is the modal
    mapped band; a tie between two or more bands yields ``majority_tilt=None`` with the
    tied bands named in ``tie_bands`` (canonical :data:`RATING_BANDS` order) — an honest
    "no majority", never an arbitrary pick. Unmapped rows (``band=None``) never vote.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    rows: tuple[UpstreamRatingRow, ...] = ()
    majority_tilt: RatingBand | None = None
    tie_bands: tuple[RatingBand, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "UpstreamRatingsExtract":
        sources = [r.source for r in self.rows]
        if sources != sorted(sources):
            raise ValueError("UpstreamRatingsExtract rows must be sorted by source")
        if len(set(sources)) != len(sources):
            raise ValueError(
                "UpstreamRatingsExtract rows must be duplicate-free by source "
                "(one reading per source per extract)"
            )
        # tilt/tie coherence: a resolved majority carries no tie_bands and vice versa
        if self.majority_tilt is not None and self.tie_bands:
            raise ValueError("a resolved majority_tilt cannot also carry tie_bands")
        return self


def extract_upstream_ratings(
    *,
    symbol: Symbol,
    as_of: datetime,
    rows: Sequence[Mapping[str, Any]],
) -> UpstreamRatingsExtract:
    """Deterministically extract upstream analyst readings into a typed block (face ②).

    Each raw row is a mapping with ``source`` + ``raw_label`` (required) and optional
    ``score`` / ``target_price``. Labels are normalized via :func:`normalize_rating_label`
    (unmapped → ``band=None``, excluded from the tally). Rows are sorted by ``source``;
    the majority tilt is the modal mapped band, ties resolved to ``None`` + named
    ``tie_bands`` in canonical order. Same inputs ⇒ identical extract (order-independent).
    """
    built = tuple(
        sorted(
            (
                UpstreamRatingRow(
                    source=r["source"],
                    raw_label=r["raw_label"],
                    band=normalize_rating_label(r["raw_label"]),
                    score=r.get("score"),
                    target_price=r.get("target_price"),
                )
                for r in rows
            ),
            key=lambda row: row.source,
        )
    )
    # tally mapped bands; the modal band is the tilt, a tie is honest None + named bands.
    counts: dict[str, int] = {}
    for row in built:
        if row.band is not None:
            counts[row.band] = counts.get(row.band, 0) + 1
    majority_tilt: RatingBand | None = None
    tie_bands: tuple[RatingBand, ...] = ()
    if counts:
        top = max(counts.values())
        at_top = tuple(b for b in RATING_BANDS if counts.get(b, 0) == top)  # canonical order
        if len(at_top) == 1:
            majority_tilt = at_top[0]  # type: ignore[assignment]
        else:
            tie_bands = at_top  # type: ignore[assignment]
    return UpstreamRatingsExtract(
        symbol=symbol,
        as_of=as_of,
        rows=built,
        majority_tilt=majority_tilt,
        tie_bands=tie_bands,
    )


# =========================================================================== #
# Face ③ · AllowedActions — the deterministic hard-constraint guardrail          #
# =========================================================================== #
#: closed intraday price-limit status. ``*_oneword`` is a sealed 一字板 (queue never
#: fills on the blocked side); a non-sealed touch (``up`` / ``down``) is still tradable.
LimitStatus = Literal["none", "up", "down", "up_oneword", "down_oneword"]

#: CRO 游资票否决 thresholds (AMEND-8 §8.0 ④): a small-cap, extreme-multiple, hot name.
_GAME_CAPITAL_MV_YI = 200.0     # 市值 < 200 亿
_GAME_CAPITAL_PE = 100.0        # PE > 100
_GAME_CAPITAL_RET60 = 50.0      # 60 日涨幅 > 50%
#: ST / delisting-risk names demand reduced exposure — capped to this band member.
_ST_MAX_BAND = 0.25


@dataclasses.dataclass(frozen=True)
class SymbolConstraintFacts:
    """The raw, deterministic per-symbol tradability facts an allowance is computed from.

    A pre-input adapter INPUT (a plain frozen dataclass, not a registered contract). Its
    fields bind to REAL data surfaces at wiring time (Task 10): ``suspended`` / ``limit_status``
    from the market-tape 打板/停牌 snapshot, ``is_st`` from the instrument metadata,
    ``holding_acquired_today`` from the position ledger (T+1), ``market_cap_yi`` / ``pe`` /
    ``ret_60d_pct`` from daily_basic (the 游资票 veto inputs), and ``severe_negative_event``
    from :attr:`AnnouncementRiskFlags.hard_veto`. Every numeric fact is honestly nullable —
    a missing fact never fabricates a veto.
    """

    symbol: Symbol
    suspended: bool = False
    limit_status: LimitStatus = "none"
    is_st: bool = False
    holding_acquired_today: bool = False
    market_cap_yi: float | None = None
    pe: float | None = None
    ret_60d_pct: float | None = None
    severe_negative_event: bool = False


class SymbolAllowance(DigestModel):
    """One symbol's pre-computed, already-validated allowed action set (face ③ row).

    ``can_buy`` / ``can_sell`` are the deterministic tradability verdicts; ``max_target_weight``
    is the ceiling the LLM may pick UP TO — always a member of the imported
    :data:`TARGET_WEIGHT_BANDS` (validator-enforced, never an arbitrary weight); ``lot_size``
    is the board lot (100, STAR 200); ``reasons`` names every constraint that fired.

    Honesty red line: a name you cannot buy cannot carry a positive target ceiling today,
    so ``can_buy=False`` forces ``max_target_weight == 0.0`` — the impossible action is
    provably excluded, not merely discouraged.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    can_buy: bool
    can_sell: bool
    lot_size: PositiveInt
    max_target_weight: FiniteFloat
    reasons: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "SymbolAllowance":
        if self.max_target_weight not in TARGET_WEIGHT_BANDS:
            raise ValueError(
                f"max_target_weight {self.max_target_weight} is not a member of the "
                f"imported closed band vocabulary {list(TARGET_WEIGHT_BANDS)}"
            )
        if not self.can_buy and self.max_target_weight != 0.0:
            raise ValueError(
                "a name that cannot be bought (can_buy=False) must have a zero target "
                "ceiling (max_target_weight=0.0) — the impossible action is excluded"
            )
        return self


class AllowedActions(DigestModel):
    """The pre-computed, already-validated allowed-action block for ``dec.pm`` (face ③).

    ``allowances`` are sorted by ``symbol.dotted`` and duplicate-free. ``already_validated``
    is ``Literal[True]``: the block is computed DETERMINISTICALLY before the LLM, which
    selects within it and never recomputes it (ai-hedge-fund risk_manager precedent).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    allowances: tuple[SymbolAllowance, ...] = ()
    already_validated: Literal[True] = True

    @model_validator(mode="after")
    def _verify(self) -> "AllowedActions":
        dotted = [a.symbol.dotted for a in self.allowances]
        if dotted != sorted(dotted):
            raise ValueError("AllowedActions allowances must be sorted by symbol.dotted")
        if len(set(dotted)) != len(dotted):
            raise ValueError("AllowedActions allowances must be duplicate-free by symbol")
        return self


def game_capital_veto(facts: SymbolConstraintFacts) -> bool:
    """The deterministic CRO 游资票否决 verdict (mv<200亿 ∧ pe>100 ∧ 60日涨幅>50%).

    Requires ALL THREE inputs present — a missing fact never fabricates a veto (honest:
    an unknown cannot be treated as a trigger).
    """
    if facts.market_cap_yi is None or facts.pe is None or facts.ret_60d_pct is None:
        return False
    return (
        facts.market_cap_yi < _GAME_CAPITAL_MV_YI
        and facts.pe > _GAME_CAPITAL_PE
        and facts.ret_60d_pct > _GAME_CAPITAL_RET60
    )


def compute_allowance(facts: SymbolConstraintFacts) -> SymbolAllowance:
    """Compute one symbol's :class:`SymbolAllowance` from its raw facts (deterministic).

    Fixed-order rule application (reasons appended in this order):
    停牌 → 一字涨停(禁买) → 一字跌停(禁卖) → T+1(禁卖) → 游资否决(禁买) →
    恶性事件否决(禁买) → ST(减仓封顶). Every produced ``max_target_weight`` is a
    member of :data:`TARGET_WEIGHT_BANDS`.
    """
    can_buy = True
    can_sell = True
    max_band = 1.0
    reasons: list[str] = []
    lot_size = 200 if facts.symbol.board == "star" else 100

    if facts.suspended:  # 停牌 — neither side trades
        can_buy = False
        can_sell = False
        max_band = 0.0
        reasons.append("suspended_halt_停牌")
    if facts.limit_status == "up_oneword":  # 一字涨停 — cannot buy (queue never fills)
        can_buy = False
        max_band = 0.0
        reasons.append("limit_up_sealed_no_buy_一字涨停")
    if facts.limit_status == "down_oneword":  # 一字跌停 — cannot sell
        can_sell = False
        reasons.append("limit_down_sealed_no_sell_一字跌停")
    if facts.holding_acquired_today:  # T+1 — today-bought shares are locked
        can_sell = False
        reasons.append("t_plus_1_lock_T+1")
    if game_capital_veto(facts):  # 游资票否决 — do not buy
        can_buy = False
        max_band = 0.0
        reasons.append("game_capital_veto_游资否决")
    if facts.severe_negative_event:  # 恶性事件否决
        can_buy = False
        max_band = 0.0
        reasons.append("severe_event_veto_恶性事件否决")
    if facts.is_st:  # ST / 退市风险 — reduced exposure
        max_band = min(max_band, _ST_MAX_BAND)
        reasons.append("st_reduced_exposure_ST减仓")

    return SymbolAllowance(
        symbol=facts.symbol,
        can_buy=can_buy,
        can_sell=can_sell,
        lot_size=lot_size,
        max_target_weight=max_band,
        reasons=tuple(reasons),
    )


def build_allowed_actions(
    *, as_of: datetime, facts: Sequence[SymbolConstraintFacts]
) -> AllowedActions:
    """Build the :class:`AllowedActions` block from per-symbol facts (deterministic).

    Order-independent: allowances are sorted by ``symbol.dotted``. A duplicate symbol
    across the facts is a caller bug (surfaced by the model validator).
    """
    allowances = tuple(
        sorted((compute_allowance(f) for f in facts), key=lambda a: a.symbol.dotted)
    )
    return AllowedActions(as_of=as_of, allowances=allowances)


# =========================================================================== #
# Face ④ · AnnouncementRiskFlags — deterministic 词表 + 排除词 + 三层烈度        #
# =========================================================================== #
#: Tier 1 (highest severity, hard-veto candidates): must be addressed in the thesis even
#: when bullish (立案调查/退市风险/质押强平/重大爆雷/大额解禁).
ANNOUNCEMENT_TIER1: tuple[str, ...] = (
    "立案调查", "退市风险", "质押强平", "重大爆雷", "大额解禁",
)
#: Tier 2 (tempers conviction): 问询函/商誉减值/减持计划/定增.
ANNOUNCEMENT_TIER2: tuple[str, ...] = (
    "问询函", "商誉减值", "减持计划", "定增",
)
#: Tier 3 (context only): 关注函.
ANNOUNCEMENT_TIER3: tuple[str, ...] = ("关注函",)
#: the hard-veto terms — the tier-1 subset that caps ``dec.pm`` at Hold and (when wired)
#: sets :attr:`SymbolConstraintFacts.severe_negative_event`.
ANNOUNCEMENT_VETO_TERMS: frozenset[str] = frozenset(ANNOUNCEMENT_TIER1)
#: 排除词 — a clarification / denial / non-involvement announcement carrying a risk term
#: is NOT a risk event. Any exclusion token present suppresses that announcement's flags
#: (a conservative false-positive guard).
ANNOUNCEMENT_EXCLUSIONS: tuple[str, ...] = (
    "澄清", "不属实", "不存在", "不涉及", "传闻",
)

AnnouncementTier = Literal["tier1", "tier2", "tier3"]

_TIER_TABLE: tuple[tuple[AnnouncementTier, tuple[str, ...]], ...] = (
    ("tier1", ANNOUNCEMENT_TIER1),
    ("tier2", ANNOUNCEMENT_TIER2),
    ("tier3", ANNOUNCEMENT_TIER3),
)
_TIER_RANK: dict[str, int] = {"tier1": 0, "tier2": 1, "tier3": 2}


class AnnouncementFlag(DigestModel):
    """One matched announcement-risk flag: its severity tier, keyword, and veto status."""

    schema_version: Literal["1"] = "1"
    tier: AnnouncementTier
    keyword: NonEmptyStr
    is_veto: bool


class AnnouncementRiskFlags(DigestModel):
    """The deterministic announcement-risk block (face ④).

    ``flags`` are sorted by (tier rank, keyword) and duplicate-free by (tier, keyword).
    ``max_tier`` is the highest severity present (``None`` when empty); ``hard_veto`` is
    True iff any tier-1 veto flag is present. Coherence is validator-enforced so the
    aggregate fields can never disagree with ``flags``.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    as_of: UtcDateTime
    flags: tuple[AnnouncementFlag, ...] = ()
    max_tier: AnnouncementTier | None = None
    hard_veto: bool = False

    @model_validator(mode="after")
    def _verify(self) -> "AnnouncementRiskFlags":
        keys = [(f.tier, f.keyword) for f in self.flags]
        ordered = sorted(keys, key=lambda k: (_TIER_RANK[k[0]], k[1]))
        if keys != ordered:
            raise ValueError("AnnouncementRiskFlags flags must be sorted by (tier, keyword)")
        if len(set(keys)) != len(keys):
            raise ValueError("AnnouncementRiskFlags flags must be duplicate-free by (tier, keyword)")
        expected_max = (
            min((f.tier for f in self.flags), key=lambda t: _TIER_RANK[t])
            if self.flags
            else None
        )
        if self.max_tier != expected_max:
            raise ValueError(
                f"max_tier {self.max_tier!r} does not match the highest-severity flag "
                f"{expected_max!r}"
            )
        expected_veto = any(f.tier == "tier1" for f in self.flags)
        if self.hard_veto != expected_veto:
            raise ValueError(
                f"hard_veto {self.hard_veto} does not match the presence of a tier-1 flag "
                f"({expected_veto})"
            )
        return self


def scan_announcement_risk(
    *,
    symbol: Symbol,
    as_of: datetime,
    announcements: Sequence[str],
) -> AnnouncementRiskFlags:
    """Deterministically scan announcement texts for risk flags (face ④).

    Per announcement: if any :data:`ANNOUNCEMENT_EXCLUSIONS` token is present the whole
    announcement is treated as a clarification/denial and contributes no flags; otherwise
    every lexicon term it contains yields a (tier, keyword) flag. Flags are deduped by
    (tier, keyword) and sorted by (tier rank, keyword). Same inputs ⇒ identical block.
    """
    matched: dict[tuple[str, str], AnnouncementFlag] = {}
    for text in announcements:
        if any(excl in text for excl in ANNOUNCEMENT_EXCLUSIONS):
            continue
        for tier, terms in _TIER_TABLE:
            for kw in terms:
                if kw in text:
                    matched[(tier, kw)] = AnnouncementFlag(
                        tier=tier, keyword=kw, is_veto=kw in ANNOUNCEMENT_VETO_TERMS
                    )
    flags = tuple(
        sorted(matched.values(), key=lambda f: (_TIER_RANK[f.tier], f.keyword))
    )
    max_tier: AnnouncementTier | None = (
        min((f.tier for f in flags), key=lambda t: _TIER_RANK[t]) if flags else None
    )
    hard_veto = any(f.tier == "tier1" for f in flags)
    return AnnouncementRiskFlags(
        symbol=symbol,
        as_of=as_of,
        flags=flags,
        max_tier=max_tier,
        hard_veto=hard_veto,
    )


# =========================================================================== #
# Face ⑤ · past-lessons PIT recipe — parameterizes the Phase 3 memory bridge     #
# =========================================================================== #
# This is CONFIGURATION of the existing mechanism, NOT new machinery: the recipe names the
# reviewed parameters and the selector is a pure post-filter over records the Phase-3
# bridge already surfaces (it opens no store and performs no retrieval).


@dataclasses.dataclass(frozen=True)
class PastLessonsRecipe:
    """The deterministic PIT recipe for the ``dec.pm`` past-lessons block (face ⑤).

    Names the reviewed parameters only (TA 主线单点注入配方): 同票近 ``same_name_full_recent``
    全文 + 跨票近 ``cross_name_reflection_recent`` 只反思. The three honesty red lines are
    STRUCTURAL — a recipe that would inject immature or pending lessons, or omit lesson-id
    citation, cannot be constructed.
    """

    same_name_full_recent: int = 5
    cross_name_reflection_recent: int = 3
    matured_only: bool = True
    exclude_pending: bool = True
    require_cited_lesson_ids: bool = True

    def __post_init__(self) -> None:
        if self.same_name_full_recent < 1 or self.cross_name_reflection_recent < 1:
            raise ValueError("recipe recency caps must be strict positive integers")
        if not self.matured_only:
            raise ValueError("matured_only is a red line — an immature lesson is never injected")
        if not self.exclude_pending:
            raise ValueError("exclude_pending is a red line — a pending lesson is never injected")
        if not self.require_cited_lesson_ids:
            raise ValueError("require_cited_lesson_ids is a red line — the block must cite lesson ids")


#: the single reviewed recipe (同票近 5 全文 + 跨票近 3 只反思; matured-only; pending 不注入).
PAST_LESSONS_RECIPE = PastLessonsRecipe()


@dataclasses.dataclass(frozen=True)
class LessonCandidate:
    """One already-surfaced lesson record the selector filters (a pre-input INPUT).

    ``same_name`` marks a 同票 record (vs a 跨票 one); ``is_reflection`` marks a reflection
    (跨票 only injects reflections); ``matured`` / ``pending`` carry the maturity + review
    state the recipe red lines filter on.
    """

    lesson_id: str
    as_of: datetime
    same_name: bool
    is_reflection: bool
    matured: bool
    pending: bool


@dataclasses.dataclass(frozen=True)
class PastLessonsBlock:
    """The rendered past-lessons block (face ⑤ output): the cited lesson ids only.

    ``same_name_lesson_ids`` are the recent full-record ids (most-recent first);
    ``cross_name_reflection_ids`` are the recent reflection ids. Listing the ids satisfies
    the recipe's ``require_cited_lesson_ids`` discipline (payload 必列 ``lesson_id``).
    """

    subject: Symbol
    as_of: datetime
    same_name_lesson_ids: tuple[str, ...]
    cross_name_reflection_ids: tuple[str, ...]


def select_past_lessons(
    *,
    subject: Symbol,
    as_of: datetime,
    candidates: Sequence[LessonCandidate],
    recipe: PastLessonsRecipe = PAST_LESSONS_RECIPE,
) -> PastLessonsBlock:
    """Apply the recipe to already-surfaced records (pure, deterministic; no store access).

    Filters to matured, non-pending candidates; keeps the ``same_name_full_recent`` most
    recent 同票 full records and the ``cross_name_reflection_recent`` most recent 跨票
    reflections; drops 跨票 non-reflections. Recency order is ``as_of`` descending, ties
    broken by ``lesson_id`` so the selection is deterministic regardless of input order.
    """
    eligible = [
        c
        for c in candidates
        if (c.matured or not recipe.matured_only)
        and (not c.pending or not recipe.exclude_pending)
    ]

    def _recency(c: LessonCandidate) -> tuple[datetime, str]:
        return (c.as_of, c.lesson_id)

    same = sorted(
        (c for c in eligible if c.same_name), key=_recency, reverse=True
    )[: recipe.same_name_full_recent]
    cross = sorted(
        (c for c in eligible if not c.same_name and c.is_reflection),
        key=_recency,
        reverse=True,
    )[: recipe.cross_name_reflection_recent]
    return PastLessonsBlock(
        subject=subject,
        as_of=as_of,
        same_name_lesson_ids=tuple(c.lesson_id for c in same),
        cross_name_reflection_ids=tuple(c.lesson_id for c in cross),
    )


# =========================================================================== #
# Task-11 Phase-8 firewall partition helpers                                    #
# =========================================================================== #
#: the three registered public injection faces (Task-11 reviews these into the Phase-8
#: cumulative registry; the plan's PHASE8_PUBLIC_MODELS names exactly these three for 9b).
DECISION_INPUT_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    UpstreamRatingsExtract,
    AllowedActions,
    AnnouncementRiskFlags,
)
#: the three nested value objects (Task-11 classifies these into PHASE8_INTERNAL_MODELS).
DECISION_INPUT_INTERNAL_MODELS: tuple[type[DigestModel], ...] = (
    UpstreamRatingRow,
    SymbolAllowance,
    AnnouncementFlag,
)

# a static self-check that no public/internal contract is double-counted (load-time).
assert not set(DECISION_INPUT_PUBLIC_MODELS) & set(DECISION_INPUT_INTERNAL_MODELS)
