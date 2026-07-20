# -*- coding: utf-8 -*-
"""Phase 8 - Task 4b: the K线经典形态词典 P0 registry contract + self-authored seed
dictionary material (AMEND-6a, R10).

This is the user's personally-named P0 ("把 k线缺口这个重点记录 一定要完善"). The
现状 before this task: ``compute_pa_features`` (guanlan_v2/seats/price_action.py) gives
15 keys of bar-level geometry, but **经典形态零覆盖**. This module is the first,
**self-authored** seed dictionary (不等外部投喂 — the ingestion loop is a later
iteration channel, not the starting point).

Delivered surface (R10 boundary, frozen)
-----------------------------------------
The registry **contract** and the reviewed seed dictionary **material** (definitions +
explicit numeric params) ONLY. Each entry is a
``pattern_id@definition_version`` + a **deterministic recognizer contract** (a prose
predicate + the exact bar-geometry inputs it consumes + explicit numeric ``rule_params``)
+ a **replay-stats shape binding**. What is deliberately NOT here (a separately-chartered
small task that depends only on daily bars and can start early): the ~31 deterministic
recognizer *handlers* and the historical-replay statistics generation. Until that lands,
every seed entry's :attr:`PatternDefinition.replay_stats` is ``None`` ⇒ UNAVAILABLE 显形
(样本不足绝不硬给胜率 — AMEND-6a 交付纪律②).

Load-bearing red lines (AMEND-6a 交付纪律)
------------------------------------------
* **No LLM anywhere.** 识别器 = a pure predicate over bar geometry. An LLM may *draft* a
  rule, but evaluation / gate / human review run the identical miner/curator path; the
  recognition itself is deterministic. This module imports nothing from the engine, the
  datafeed live layers, or any network/LLM library (proven by an AST scan in the test).
* **Reproducible params.** Every judgment threshold is an explicit, versioned
  :attr:`~PatternDefinition.rule_params` finite float — no magic constants buried in a
  handler. Boolean gates (context/confirmation conditions) live in the prose
  :attr:`~PatternDefinition.predicate`; ``rule_params`` are numeric thresholds only.
* **Honest stats.** :class:`PatternReplayStats` and the gap family's
  :class:`GapFillStats` (缺口回补统计) render UNAVAILABLE whenever a field is absent, and
  a rate can never be reported without its sample count (``n_occurrences`` / ``n_gaps``).
* **No fake precision.** 多 bar 结构类 (头肩/双顶/三角形/旗形/箱体/杯柄) are marked
  :attr:`~PatternDefinition.approximate` — the recognizer emits a confidence score and is
  allowed 宽松几何近似; every other family is geometrically precise.

Recognizer-binding contract (Task 5 / #27 ``pv.curator`` / ``pv.*`` workers)
----------------------------------------------------------------------------
:attr:`~PatternDefinition.geometry_inputs` is the reserved handler-binding surface: it
names the exact bar-geometry keys (drawn from :data:`GEOMETRY_INPUTS`, a superset of the
``compute_pa_features`` vocabulary plus the raw OHLCV bar fields and the swing/trend
aggregates the multi-bar structures need) the deterministic recognizer will consume.
Because the inputs and params are fixed **now**, the later recognizer-handler task binds
a ``handler`` material to a pattern with **no @2 bump** of the definition. Dictionary
entries feed the ``pv.price_action`` / ``pv.technical`` SKILL data面; the 判读方法论
references a ``pattern_id`` and never re-narrates the geometry (AMEND-6a 交付纪律④).

Catalog material
----------------
The reviewed :class:`PatternDictionary` is persisted as a catalog ``kind="guardrail"``
text material (Task-0b/Task-3 precedent). Its physical bytes are
:func:`pattern_dictionary_material_bytes` (the ``sha256+cjson-v1`` canonical JSON) and its
content-digest-sealed :class:`~guanlan_v2.orchestration.refs.ContentRef` is
:func:`build_pattern_dictionary_material`; the physical file lives at
``config/orchestration/materials/guardrails/pattern-dictionary.json``. Registration is
Task 11.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, get_args

from pydantic import model_validator

from guanlan_v2.orchestration.catalog import ResolvedTextMaterial
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    canonical_json,
)
from guanlan_v2.orchestration.refs import ContentRef, LogicalId

__all__ = [
    "PatternFamily",
    "PATTERN_FAMILIES",
    "GeometryInput",
    "GEOMETRY_INPUTS",
    "STATS_UNAVAILABLE",
    "PatternReplayStats",
    "GapFillStats",
    "PatternDefinition",
    "PatternDictionary",
    "PATTERN_DICTIONARY_MATERIAL_ID",
    "PATTERN_DICTIONARY_MATERIAL_VERSION",
    "replay_stats_label",
    "gap_fill_stats_label",
    "build_seed_pattern_dictionary",
    "pattern_dictionary_material_bytes",
    "build_pattern_dictionary_material",
    "pattern_dictionary_from_material_bytes",
]

# --------------------------------------------------------------------------- #
# Closed vocabularies                                                         #
# --------------------------------------------------------------------------- #
#: The closed family vocabulary (AMEND-6a §6.1 six classes). ``multi_bar`` structures are
#: the only family allowed 宽松几何近似 + 置信分 (approximate=True).
PatternFamily = Literal[
    "single_bar", "double_bar", "triple_bar", "multi_bar", "gap", "astock",
]
#: canonical family order (for census / documentation).
PATTERN_FAMILIES: tuple[PatternFamily, ...] = (
    "single_bar", "double_bar", "triple_bar", "multi_bar", "gap", "astock",
)

#: The closed bar-geometry input vocabulary a deterministic recognizer may consume. A
#: superset of the ``compute_pa_features`` 15-key output plus the raw OHLCV bar fields and
#: the swing/trend aggregates the multi-bar structures require. Every
#: :attr:`PatternDefinition.geometry_inputs` element is drawn from here, so a later
#: handler binds against a known, compatible surface (no parallel vocabulary invented).
GeometryInput = Literal[
    # raw per-bar OHLCV (over the recognizer's bar window)
    "open", "high", "low", "close", "vol",
    # cross-bar raw
    "prev_close",
    # derived per-bar geometry (compute_pa_features)
    "body", "upper_wick", "lower_wick", "close_pos", "range_atr", "ema20_rel", "bar_type",
    # derived cross-bar / context (compute_pa_features)
    "gap", "limit", "vol_ratio", "breakout", "inside_streak", "follow", "recent",
    # structural aggregates for multi-bar approximate recognizers
    "swing_high", "swing_low", "prior_trend",
]
GEOMETRY_INPUTS: tuple[GeometryInput, ...] = get_args(GeometryInput)

#: Rendered wherever a replay-stats / gap-fill-stats value is absent. NEVER a zero — an
#: unavailable statistic must not masquerade as a real measured value.
STATS_UNAVAILABLE = "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# Replay-stats shapes (honest: any None => UNAVAILABLE)                        #
# --------------------------------------------------------------------------- #
class PatternReplayStats(DigestModel):
    """Historical-replay statistics for a pattern (胜率/盈亏比/样本数).

    Honesty rule (样本不足绝不硬给胜率): a rate (``win_rate`` / ``profit_loss_ratio``)
    can never be reported without its sample count ``n_occurrences``; and the stats are
    :attr:`is_available` only when all three are present. The two allowed UNAVAILABLE
    shapes are (a) not yet replayed (all ``None``) and (b) replayed but the sample is
    below the reporting floor (``n_occurrences`` known, rates withheld).
    """

    schema_version: Literal["1"] = "1"
    win_rate: FiniteFloat | None
    profit_loss_ratio: FiniteFloat | None
    n_occurrences: NonNegativeInt | None

    @model_validator(mode="after")
    def _honest(self) -> "PatternReplayStats":
        if self.win_rate is not None and not (0.0 <= self.win_rate <= 1.0):
            raise ValueError("win_rate must be a fraction in [0, 1]")
        if self.profit_loss_ratio is not None and self.profit_loss_ratio < 0.0:
            raise ValueError("profit_loss_ratio must be >= 0")
        if (self.win_rate is not None or self.profit_loss_ratio is not None) and (
            self.n_occurrences is None
        ):
            raise ValueError(
                "a win_rate / profit_loss_ratio without an n_occurrences sample count is "
                "dishonest; report UNAVAILABLE instead"
            )
        return self

    @property
    def is_available(self) -> bool:
        return (
            self.win_rate is not None
            and self.profit_loss_ratio is not None
            and self.n_occurrences is not None
        )


class GapFillStats(DigestModel):
    """缺口回补统计 — the gap family's dedicated replay-stats shape.

    A gap's fill behaviour differs sharply by type (a 衰竭缺口 fills fast; a 突破缺口
    rarely fills), so the fill statistics bind *per gap pattern* rather than globally.
    Same honesty rule as :class:`PatternReplayStats`: a ``fill_rate`` /
    ``median_bars_to_fill`` can never be reported without its ``n_gaps`` sample count, and
    the stats are :attr:`is_available` only when all three are present.
    """

    schema_version: Literal["1"] = "1"
    fill_rate: FiniteFloat | None
    median_bars_to_fill: FiniteFloat | None  # trading-day bars from gap to full fill
    n_gaps: NonNegativeInt | None

    @model_validator(mode="after")
    def _honest(self) -> "GapFillStats":
        if self.fill_rate is not None and not (0.0 <= self.fill_rate <= 1.0):
            raise ValueError("fill_rate must be a fraction in [0, 1]")
        if self.median_bars_to_fill is not None and self.median_bars_to_fill < 0.0:
            raise ValueError("median_bars_to_fill must be >= 0")
        if (self.fill_rate is not None or self.median_bars_to_fill is not None) and (
            self.n_gaps is None
        ):
            raise ValueError(
                "a fill_rate / median_bars_to_fill without an n_gaps sample count is "
                "dishonest; report UNAVAILABLE instead"
            )
        return self

    @property
    def is_available(self) -> bool:
        return (
            self.fill_rate is not None
            and self.median_bars_to_fill is not None
            and self.n_gaps is not None
        )


def replay_stats_label(stats: "PatternReplayStats | None") -> str:
    """Render replay stats honestly: ``UNAVAILABLE`` when absent/insufficient, never zeros."""
    if stats is None or not stats.is_available:
        return STATS_UNAVAILABLE
    return (
        f"win_rate={stats.win_rate:g} "
        f"pl={stats.profit_loss_ratio:g} "
        f"n={stats.n_occurrences}"
    )


def gap_fill_stats_label(stats: "GapFillStats | None") -> str:
    """Render 缺口回补统计 honestly: ``UNAVAILABLE`` when absent/insufficient, never zeros."""
    if stats is None or not stats.is_available:
        return STATS_UNAVAILABLE
    return (
        f"fill_rate={stats.fill_rate:g} "
        f"median_bars={stats.median_bars_to_fill:g} "
        f"n={stats.n_gaps}"
    )


# --------------------------------------------------------------------------- #
# Pattern definition (recognizer contract)                                    #
# --------------------------------------------------------------------------- #
class PatternDefinition(DigestModel):
    """One K线形态 definition = a deterministic recognizer contract + a stats binding.

    Fields:

    * ``pattern_id`` / ``definition_version`` — the versioned identity; a later
      recognizer-handler binds to exactly ``pattern_id@definition_version``.
    * ``family`` — the closed :data:`PatternFamily` class.
    * ``display_name`` — the human name (中文).
    * ``predicate`` — the prose deterministic判定规则 (references the geometry inputs +
      params + any context/confirmation gate). LLM-draftable, but never LLM-evaluated.
    * ``geometry_inputs`` — the exact, canonically sorted, dup-free subset of
      :data:`GEOMETRY_INPUTS` the recognizer consumes (the handler-binding surface).
    * ``rule_params`` — the explicit, versioned numeric thresholds (finite floats).
    * ``approximate`` — ``True`` only for multi-bar structures (宽松几何近似 + 置信分).
    * ``replay_stats`` — the frozen :class:`PatternReplayStats` (``None`` ⇒ UNAVAILABLE
      until the replay task lands).
    * ``gap_fill_stats`` — the 缺口回补统计 slot; permitted ONLY on the ``gap`` family
      (a non-gap pattern carrying gap-fill stats is a category error).
    """

    schema_version: Literal["1"] = "1"
    pattern_id: LogicalId
    definition_version: NonEmptyStr
    family: PatternFamily
    display_name: NonEmptyStr
    predicate: NonEmptyStr
    geometry_inputs: tuple[GeometryInput, ...]
    rule_params: dict[NonEmptyStr, FiniteFloat]
    approximate: bool
    replay_stats: PatternReplayStats | None = None
    gap_fill_stats: GapFillStats | None = None

    @model_validator(mode="after")
    def _validate(self) -> "PatternDefinition":
        gi = self.geometry_inputs
        if len(gi) == 0:
            raise ValueError(f"{self.pattern_id}: geometry_inputs must not be empty")
        if list(gi) != sorted(set(gi)):
            raise ValueError(
                f"{self.pattern_id}: geometry_inputs must be canonically sorted and "
                f"duplicate-free; got {gi!r}"
            )
        if not self.rule_params:
            raise ValueError(f"{self.pattern_id}: rule_params must not be empty")
        if self.family != "gap" and self.gap_fill_stats is not None:
            raise ValueError(
                f"{self.pattern_id}: gap_fill_stats (缺口回补统计) is permitted only on the "
                f"'gap' family; got family {self.family!r}"
            )
        return self

    @property
    def replay_stats_available(self) -> bool:
        return self.replay_stats is not None and self.replay_stats.is_available

    def render_replay_stats(self) -> str:
        return replay_stats_label(self.replay_stats)


# --------------------------------------------------------------------------- #
# Pattern dictionary (self-sealed, sorted, dup-free)                          #
# --------------------------------------------------------------------------- #
def _entry_key(e: PatternDefinition) -> str:
    return f"{e.pattern_id}@{e.definition_version}"


class PatternDictionary(DigestModel):
    """The reviewed, self-sealed seed dictionary (Task-11 registered payload).

    ``entries`` are canonically sorted by ``pattern_id@definition_version`` and
    duplicate-free. ``content_digest`` is the model's own semantic seal (excluded from its
    own projection via ``SELF_DIGEST_FIELDS``); construct via :meth:`build`, which computes
    it, and the validator re-verifies it on load.
    """

    schema_version: Literal["1"] = "1"
    entries: tuple[PatternDefinition, ...]
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _validate(self) -> "PatternDictionary":
        keys = [_entry_key(e) for e in self.entries]
        if keys != sorted(keys):
            raise ValueError(
                "PatternDictionary entries must be canonically sorted by "
                "'pattern_id@definition_version'"
            )
        if len(set(keys)) != len(keys):
            raise ValueError("PatternDictionary entries must be duplicate-free")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, *, entries: tuple[PatternDefinition, ...]) -> "PatternDictionary":
        """Seal a dictionary: sort entries canonically, then compute ``content_digest``.

        Sorting is explicit (never silent tampering): duplicate keys still raise in the
        validator. A caller passing already-sorted entries gets an identical result.
        """
        ordered = tuple(sorted(entries, key=_entry_key))
        digest = cls.digest_of_fields(projection="semantic", entries=ordered)
        return cls(entries=ordered, content_digest=digest)

    def by_id(self, pattern_id: str) -> PatternDefinition:
        for e in self.entries:
            if e.pattern_id == pattern_id:
                return e
        raise KeyError(pattern_id)


# --------------------------------------------------------------------------- #
# The reviewed seed dictionary (self-authored — AMEND-6a §6.1)                #
# --------------------------------------------------------------------------- #
def _p(
    pattern_id: str,
    family: PatternFamily,
    display_name: str,
    predicate: str,
    geometry_inputs: tuple[str, ...],
    rule_params: dict[str, float],
    *,
    approximate: bool = False,
    gap_fill: bool = False,
) -> PatternDefinition:
    """Author one seed entry (geometry inputs canonicalized here)."""
    return PatternDefinition(
        pattern_id=pattern_id,
        definition_version="1",
        family=family,
        display_name=display_name,
        predicate=predicate,
        geometry_inputs=tuple(sorted(set(geometry_inputs))),  # canonical
        rule_params=dict(rule_params),
        approximate=approximate,
        replay_stats=None,  # R10: UNAVAILABLE until the replay task lands
        # gap family carries the reserved 缺口回补统计 slot (UNAVAILABLE now).
        gap_fill_stats=(
            GapFillStats(fill_rate=None, median_bars_to_fill=None, n_gaps=None)
            if gap_fill
            else None
        ),
    )


def _seed_entries() -> tuple[PatternDefinition, ...]:
    # NOTE on the geometry fraction convention: ``body`` + ``upper_wick`` + ``lower_wick``
    # partition the bar range (each is a fraction of high-low that sums to 1), exactly as
    # compute_pa_features defines them. ``close_pos`` = (close-low)/range in [0,1].
    return (
        # ---------------- 单 bar (single_bar) — precise ---------------------------- #
        _p(
            "pv.single.hammer", "single_bar", "锤子线",
            "下影线长(下影/实体 ≥ lower_wick_to_body_min)、实体小(body ≤ max_body_frac)、"
            "上影极短(upper_wick ≤ max_upper_wick_frac)、收盘居上部(close_pos ≥ "
            "min_close_pos),且出现在一段下跌之后(prior_trend=下跌):见底做多信号。",
            ("body", "upper_wick", "lower_wick", "close_pos", "prior_trend"),
            {"lower_wick_to_body_min": 2.0, "max_body_frac": 0.33,
             "max_upper_wick_frac": 0.10, "min_close_pos": 0.60},
        ),
        _p(
            "pv.single.hanging_man", "single_bar", "上吊线",
            "几何同锤子线(长下影、小实体、极短上影、收于上部),但出现在一段上涨之后"
            "(prior_trend=上涨):见顶警示,需次日阴线确认。",
            ("body", "upper_wick", "lower_wick", "close_pos", "prior_trend"),
            {"lower_wick_to_body_min": 2.0, "max_body_frac": 0.33,
             "max_upper_wick_frac": 0.10, "min_close_pos": 0.60},
        ),
        _p(
            "pv.single.doji", "single_bar", "十字星",
            "实体极小(body ≤ max_body_frac,收开近乎相等)、上下影均不极端(单侧影 ≤ "
            "max_shadow_frac,以区别长腿十字):多空僵持、变盘警示。",
            ("body", "upper_wick", "lower_wick"),
            {"max_body_frac": 0.05, "max_shadow_frac": 0.60},
        ),
        _p(
            "pv.single.long_legged_doji", "single_bar", "长腿十字",
            "实体极小(body ≤ max_body_frac),上、下影均很长(各 ≥ min_leg_frac)、"
            "当根振幅大(range_atr ≥ min_range_atr):剧烈拉锯、分歧极大。",
            ("body", "upper_wick", "lower_wick", "range_atr"),
            {"max_body_frac": 0.05, "min_leg_frac": 0.35, "min_range_atr": 1.20},
        ),
        _p(
            "pv.single.shooting_star", "single_bar", "流星线",
            "上影线长(上影/实体 ≥ upper_wick_to_body_min)、实体小(body ≤ max_body_frac)、"
            "下影极短(lower_wick ≤ max_lower_wick_frac)、收盘居下部(close_pos ≤ "
            "max_close_pos),且出现在一段上涨之后(prior_trend=上涨):见顶信号。",
            ("body", "upper_wick", "lower_wick", "close_pos", "prior_trend"),
            {"upper_wick_to_body_min": 2.0, "max_body_frac": 0.33,
             "max_lower_wick_frac": 0.10, "max_close_pos": 0.40},
        ),
        _p(
            "pv.single.spinning_top", "single_bar", "纺锤线",
            "小实体(min_body_frac ≤ body ≤ max_body_frac,非十字)、上下影均较长"
            "(各 ≥ min_shadow_frac):动能衰减、多空均衡。",
            ("body", "upper_wick", "lower_wick"),
            {"min_body_frac": 0.05, "max_body_frac": 0.30, "min_shadow_frac": 0.20},
        ),

        # ---------------- 双 bar (double_bar) — precise ---------------------------- #
        _p(
            "pv.double.bullish_engulfing", "double_bar", "阳包阴吞没",
            "前一根为阴、当根为阳,当根实体完全吞没前根实体(当根 open ≤ 前 close 且 "
            "当根 close ≥ 前 open),当根实体较大(body ≥ curr_body_min_frac),"
            "出现在下跌之后(prior_trend=下跌):底部反转做多。",
            ("open", "close", "body", "prior_trend"),
            {"prev_body_min_frac": 0.10, "curr_body_min_frac": 0.50, "engulf_cover_min": 1.0},
        ),
        _p(
            "pv.double.bearish_engulfing", "double_bar", "阴包阳吞没",
            "前一根为阳、当根为阴,当根实体完全吞没前根实体(当根 open ≥ 前 close 且 "
            "当根 close ≤ 前 open),当根实体较大,出现在上涨之后(prior_trend=上涨):"
            "顶部反转。",
            ("open", "close", "body", "prior_trend"),
            {"prev_body_min_frac": 0.10, "curr_body_min_frac": 0.50, "engulf_cover_min": 1.0},
        ),
        _p(
            "pv.double.harami", "double_bar", "孕线",
            "前根大实体(body ≥ prev_body_min_frac),当根小实体(body ≤ curr_body_max_frac)"
            "且实体完全落在前根实体范围内(containment ≥ 1.0):趋势动能衰减、变盘。",
            ("open", "close", "body"),
            {"prev_body_min_frac": 0.50, "curr_body_max_frac": 0.30, "containment": 1.0},
        ),
        _p(
            "pv.double.dark_cloud_cover", "double_bar", "乌云盖顶",
            "上涨中,前根大阳;当根高开(open ≥ 前 close)后收阴,收盘深入前根阳线实体过半"
            "(penetration ≥ penetration_min,但未完全吞没):顶部反转。",
            ("open", "high", "close", "prev_close", "prior_trend"),
            {"prev_body_min_frac": 0.50, "penetration_min": 0.50},
        ),
        _p(
            "pv.double.piercing_line", "double_bar", "曙光初现",
            "下跌中,前根大阴;当根低开(open ≤ 前 close)后收阳,收盘上穿前根阴线实体过半"
            "(penetration ≥ penetration_min,但未完全吞没):底部反转做多。",
            ("open", "low", "close", "prev_close", "prior_trend"),
            {"prev_body_min_frac": 0.50, "penetration_min": 0.50},
        ),

        # ---------------- 三 bar (triple_bar) — precise ---------------------------- #
        _p(
            "pv.triple.morning_star", "triple_bar", "启明星",
            "下跌中三根:第一根大阴(body ≥ first_body_min_frac);第二根小实体星"
            "(body ≤ star_body_max_frac,通常跳空低开);第三根大阳(body ≥ "
            "third_body_min_frac)且收盘回补第一根实体过半(recover ≥ recover_min):底部反转。",
            ("open", "close", "body", "prior_trend"),
            {"first_body_min_frac": 0.50, "star_body_max_frac": 0.30,
             "third_body_min_frac": 0.50, "recover_min": 0.50},
        ),
        _p(
            "pv.triple.evening_star", "triple_bar", "黄昏星",
            "上涨中三根:第一根大阳;第二根小实体星;第三根大阴且收盘回撤第一根实体过半"
            "(retrace ≥ retrace_min):顶部反转。",
            ("open", "close", "body", "prior_trend"),
            {"first_body_min_frac": 0.50, "star_body_max_frac": 0.30,
             "third_body_min_frac": 0.50, "retrace_min": 0.50},
        ),
        _p(
            "pv.triple.three_white_soldiers", "triple_bar", "红三兵",
            "连续三根阳线,收盘逐根抬高(严格递增),每根实体较大(body ≥ body_min_frac)、"
            "收于高位(close_pos ≥ min_close_pos)、上影短(upper_wick ≤ max_upper_wick_frac),"
            "开盘落在前根实体内(稳健推进、非跳空虚涨):多头强势。",
            ("open", "close", "body", "close_pos"),
            {"body_min_frac": 0.50, "min_close_pos": 0.60, "max_upper_wick_frac": 0.20},
        ),
        _p(
            "pv.triple.three_black_crows", "triple_bar", "三只乌鸦",
            "连续三根阴线,收盘逐根走低(严格递减),每根实体较大、收于低位"
            "(close_pos ≤ max_close_pos)、下影短(lower_wick ≤ max_lower_wick_frac),"
            "开盘落在前根实体内:空头强势、见顶下跌。",
            ("open", "close", "body", "close_pos"),
            {"body_min_frac": 0.50, "max_close_pos": 0.40, "max_lower_wick_frac": 0.20},
        ),

        # ---------------- 多 bar 结构 (multi_bar) — approximate + 置信分 ----------- #
        _p(
            "pv.multi.head_shoulders_top", "multi_bar", "头肩顶",
            "上涨后出现三个波峰:中峰(头)显著高于两侧峰(head 高出肩 ≥ head_prominence_min),"
            "两肩高度相近(差 ≤ shoulder_symmetry_tol);跌破颈线(两谷连线)确认。"
            "宽松几何近似,输出置信分。",
            ("swing_high", "swing_low", "close", "prior_trend"),
            {"shoulder_symmetry_tol": 0.03, "head_prominence_min": 0.02,
             "min_lookback_bars": 20.0},
            approximate=True,
        ),
        _p(
            "pv.multi.head_shoulders_bottom", "multi_bar", "头肩底",
            "下跌后出现三个波谷:中谷(头)显著低于两侧谷,两肩高度相近;向上突破颈线确认:"
            "底部反转。宽松几何近似,输出置信分。",
            ("swing_high", "swing_low", "close", "prior_trend"),
            {"shoulder_symmetry_tol": 0.03, "head_prominence_min": 0.02,
             "min_lookback_bars": 20.0},
            approximate=True,
        ),
        _p(
            "pv.multi.double_top", "multi_bar", "双顶(M头)",
            "上涨后两个相近高点(高度差 ≤ peak_symmetry_tol),中间夹一回调谷"
            "(深度 ≥ valley_depth_min);跌破颈线(谷)确认。M 形顶部反转,输出置信分。",
            ("swing_high", "swing_low", "close", "prior_trend"),
            {"peak_symmetry_tol": 0.02, "valley_depth_min": 0.03, "min_lookback_bars": 20.0},
            approximate=True,
        ),
        _p(
            "pv.multi.double_bottom", "multi_bar", "双底(W底)",
            "下跌后两个相近低点(高度差 ≤ bottom_symmetry_tol),中间夹一反弹峰"
            "(高度 ≥ peak_height_min);向上突破颈线确认。W 形底部反转,输出置信分。",
            ("swing_high", "swing_low", "close", "prior_trend"),
            {"bottom_symmetry_tol": 0.02, "peak_height_min": 0.03, "min_lookback_bars": 20.0},
            approximate=True,
        ),
        _p(
            "pv.multi.triangle", "multi_bar", "三角形整理",
            "高点连线与低点连线相互收敛(后段振幅相对前段收敛 ≥ convergence_min),"
            "至少 min_touch_points 次触边、量能萎缩(vol_ratio 下行):对称/上升/下降三角整理,"
            "突破方向待定,输出置信分。",
            ("swing_high", "swing_low", "range_atr", "vol_ratio"),
            {"convergence_min": 0.50, "min_touch_points": 4.0, "min_lookback_bars": 15.0},
            approximate=True,
        ),
        _p(
            "pv.multi.flag", "multi_bar", "旗形",
            "一段急涨/急跌旗杆(单向幅度 ≥ pole_move_min)后,小幅逆向的平行通道回调(旗面,"
            "回撤 ≤ flag_retrace_max、持续 ≤ max_flag_bars 根、量缩 vol_ratio ≤ vol_contract_max):"
            "顺势中继形态,输出置信分。",
            ("close", "range_atr", "vol_ratio", "prior_trend"),
            {"pole_move_min": 0.15, "flag_retrace_max": 0.50, "max_flag_bars": 10.0,
             "vol_contract_max": 0.80},
            approximate=True,
        ),
        _p(
            "pv.multi.box_range", "multi_bar", "箱体平台",
            "价格在水平上、下沿之间反复(上沿各高点离散 ≤ boundary_tol、下沿各低点离散 ≤ "
            "boundary_tol),至少 min_touch_points 次触边、箱高 ≤ max_box_height:窄幅震荡,"
            "区间高抛低吸参照,输出置信分。",
            ("swing_high", "swing_low", "close", "range_atr"),
            {"boundary_tol": 0.03, "min_touch_points": 4.0, "max_box_height": 0.15,
             "min_lookback_bars": 15.0},
            approximate=True,
        ),
        _p(
            "pv.multi.cup_with_handle", "multi_bar", "杯柄",
            "先一个圆弧底(U 形杯,缓跌缓涨,深度 cup_depth_min ≤ 回撤 ≤ cup_depth_max),"
            "右沿附近浅幅整理成柄(回撤 ≤ handle_retrace_max、持续 ≤ handle_max_bars 根、量缩);"
            "突破杯口确认。做多突破中继,输出置信分。",
            ("swing_high", "swing_low", "close", "vol_ratio", "prior_trend"),
            {"cup_depth_min": 0.12, "cup_depth_max": 0.35, "handle_retrace_max": 0.50,
             "handle_max_bars": 15.0, "min_lookback_bars": 30.0},
            approximate=True,
        ),

        # ---------------- 缺口族 (gap) — P0 focus, precise, + 回补统计 slot -------- #
        # Rigorous 缺口 definition: a true gap requires NON-overlap of adjacent bar ranges
        # (向上跳空: 今 low > 昨 high; 向下跳空: 今 high < 昨 low) — NOT merely 高开
        # (open > prev_close). Type is disambiguated by location + volume + trend context.
        _p(
            "pv.gap.common", "gap", "普通缺口",
            "相邻两根出现跳空(今 low > 昨 high 或 今 high < 昨 low),幅度小"
            "(≥ min_gap_frac)、发生在震荡区间内(breakout=区间内)、量能不放大"
            "(vol_ratio ≤ max_vol_ratio):无趋势意义,通常很快回补。",
            ("high", "low", "prev_close", "vol_ratio", "breakout"),
            {"min_gap_frac": 0.005, "max_vol_ratio": 1.50},
            gap_fill=True,
        ),
        _p(
            "pv.gap.breakaway", "gap", "突破缺口",
            "在盘整末端向趋势方向跳空(幅度 ≥ min_gap_frac),伴随明显放量"
            "(vol_ratio ≥ min_vol_ratio),且突破前期区间边界(breakout=突破前高/跌破前低):"
            "行情启动信号,通常不易快速回补。",
            ("high", "low", "prev_close", "vol_ratio", "breakout"),
            {"min_gap_frac": 0.01, "min_vol_ratio": 2.0},
            gap_fill=True,
        ),
        _p(
            "pv.gap.runaway", "gap", "持续缺口(量度缺口)",
            "已确立的趋势途中再度同向跳空(prior_trend 同向,幅度 ≥ min_gap_frac),量能温和"
            "(vol_ratio ≤ vol_ratio_max),常位于整段行情中部,可作量度目标估算剩余空间:"
            "趋势健康的确认。",
            ("high", "low", "prev_close", "vol_ratio", "prior_trend"),
            {"min_gap_frac": 0.01, "vol_ratio_max": 3.0},
            gap_fill=True,
        ),
        _p(
            "pv.gap.exhaustion", "gap", "衰竭缺口",
            "趋势末端的最后一跳(幅度 ≥ min_gap_frac),常伴极端放量(vol_ratio ≥ "
            "min_vol_ratio)后迅速反向(次日反向确认、收盘位掉头):趋势枯竭,高回补概率。",
            ("high", "low", "prev_close", "vol_ratio", "close_pos", "prior_trend"),
            {"min_gap_frac": 0.01, "min_vol_ratio": 3.0},
            gap_fill=True,
        ),

        # ---------------- A股特色 (astock) — precise, T+1 涨跌停 reality ----------- #
        _p(
            "pv.astock.one_word_board", "astock", "一字板",
            "开=高=低=收 全等(振幅 ≤ max_range_frac)且封于涨停(limit=涨停):全天一字封死,"
            "买不进(跌停一字则卖不出),T+1 流动性极端。",
            ("open", "high", "low", "close", "limit"),
            {"max_range_frac": 0.001},
        ),
        _p(
            "pv.astock.t_word_board", "astock", "T字板",
            "收=高=开且封于涨停(收≈涨停价),盘中曾被砸开留下可见下影"
            "(lower_wick ≥ min_lower_wick_frac),形如字母 T:涨停开板又封回,资金分歧。",
            ("open", "high", "low", "close", "lower_wick", "limit"),
            {"close_eq_high_tol": 0.001, "min_lower_wick_frac": 0.05},
        ),
        _p(
            "pv.astock.limit_up_breakout", "astock", "涨停突破",
            "以涨停(limit=涨停)放量(vol_ratio ≥ min_vol_ratio)突破前期高点"
            "(breakout=突破前高),且非一字(有实体换手 body ≥ min_body_frac):强势突破启动。",
            ("close", "limit", "breakout", "vol_ratio", "body"),
            {"min_vol_ratio": 1.50, "min_body_frac": 0.02},
        ),
        _p(
            "pv.astock.failed_limit_long_upper", "astock", "炸板长上影",
            "盘中触及涨停(high 达涨停价附近)但收盘回落未封(limit≠涨停),留下长上影"
            "(upper_wick ≥ min_upper_wick_frac)、收于中下部(close_pos ≤ max_close_pos),"
            "常伴放量(vol_ratio ≥ min_vol_ratio):冲高回落、多头受阻(炸板)。",
            ("high", "close", "upper_wick", "close_pos", "vol_ratio", "limit"),
            {"min_upper_wick_frac": 0.40, "max_close_pos": 0.50, "min_vol_ratio": 1.50},
        ),
    )


def build_seed_pattern_dictionary() -> PatternDictionary:
    """Build the reviewed, self-authored seed :class:`PatternDictionary` (AMEND-6a §6.1).

    Pure (no I/O, no LLM). Deterministic: same bytes every call.
    """
    return PatternDictionary.build(entries=_seed_entries())


# --------------------------------------------------------------------------- #
# Catalog guardrail material                                                   #
# --------------------------------------------------------------------------- #
#: the reviewed guardrail material identity (a ``LogicalId``, never a file path); the
#: physical file lives at
#: ``config/orchestration/materials/guardrails/pattern-dictionary.json``.
PATTERN_DICTIONARY_MATERIAL_ID: LogicalId = "guardrail.pattern_dictionary"
PATTERN_DICTIONARY_MATERIAL_VERSION = "1"
_MATERIAL_KIND = "guardrail"


def pattern_dictionary_material_bytes(dictionary: PatternDictionary) -> bytes:
    """The physical guardrail material bytes: canonical (``sha256+cjson-v1``) JSON.

    These are exactly the bytes persisted at
    ``config/orchestration/materials/guardrails/pattern-dictionary.json``. The dictionary's
    own ``content_digest`` (a ``SELF_DIGEST_FIELDS`` member) is excluded from the semantic
    projection, so it is not written and is recomputed on load.
    """
    return canonical_json(dictionary).encode("utf-8")


def build_pattern_dictionary_material(
    dictionary: PatternDictionary,
) -> tuple[ContentRef, ResolvedTextMaterial]:
    """Seal the reviewed dictionary as a ``guardrail`` text material + its ``ContentRef``.

    The digest is computed from the canonical material bytes via the single reviewed
    :func:`~guanlan_v2.orchestration.catalog_runtime.build_text_material` helper — a caller
    never pins a digest by hand.
    """
    return build_text_material(
        id=PATTERN_DICTIONARY_MATERIAL_ID,
        version=PATTERN_DICTIONARY_MATERIAL_VERSION,
        kind=_MATERIAL_KIND,
        raw=pattern_dictionary_material_bytes(dictionary),
    )


def pattern_dictionary_from_material_bytes(raw: bytes) -> PatternDictionary:
    """Parse canonical material bytes back into the reviewed :class:`PatternDictionary`.

    The inverse of :func:`pattern_dictionary_material_bytes`; used to prove the physical
    material reproduces the reviewed payload (Task 11's registered-payload check). The
    dictionary re-seals its ``content_digest`` on :meth:`PatternDictionary.build`.
    """
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("pattern-dictionary material must decode to a JSON object")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("pattern-dictionary material must carry an 'entries' array")
    entries = tuple(_entry_from_json(e) for e in raw_entries)
    return PatternDictionary.build(entries=entries)


def _stats_from_json(d: Any, cls: type) -> Any:
    if d is None:
        return None
    return cls(**d)


def _entry_from_json(e: Mapping[str, Any]) -> PatternDefinition:
    return PatternDefinition(
        pattern_id=e["pattern_id"],
        definition_version=e["definition_version"],
        family=e["family"],
        display_name=e["display_name"],
        predicate=e["predicate"],
        geometry_inputs=tuple(e["geometry_inputs"]),
        rule_params=dict(e["rule_params"]),
        approximate=e["approximate"],
        replay_stats=_stats_from_json(e.get("replay_stats"), PatternReplayStats),
        gap_fill_stats=_stats_from_json(e.get("gap_fill_stats"), GapFillStats),
    )
