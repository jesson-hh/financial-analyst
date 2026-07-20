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
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    UtcDateTime,
)

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
    # partition helpers (for the Task-11 Phase-8 firewall)
    "LANE_C_PUBLIC_MODELS",
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
