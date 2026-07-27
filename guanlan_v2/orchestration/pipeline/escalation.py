# -*- coding: utf-8 -*-
"""Phase 10 · Task 5 — the pure, zero-LLM 落子 escalation judge.

This module answers exactly one question — *does this stock's intraday fast
judgment deserve a deep (kernel) re-judgment right now?* — and it answers it
with arithmetic and string containment only. No LLM, no network, no disk, no
clock: :func:`judge_escalation` is a total, deterministic function of the
context it is handed, so the same context always yields a byte-equal
:class:`~guanlan_v2.orchestration.pipeline.contracts.EscalationReport` digest
and the verdict can be replayed and audited years later.

Ruling R-C, in three parts
--------------------------
1. **Frozen thresholds, not knobs.** :data:`ESCALATION_THRESHOLDS_VERSION`,
   :data:`STOP_TAKE_PROXIMITY_PCT` and :data:`EVENT_WORDLIST_V1` are the reviewed
   Phase-10 Global-Constraints values, verbatim. Editing any of them moves
   :data:`ESCALATION_CONSTANTS_DIGEST` and therefore fails the golden pin in
   ``tests/orchestration/test_pipeline_escalation.py`` — a threshold change is a
   deliberate contract change, never a quiet tuning.
2. **All I/O lives in the injected ports of the context builder.**
   :func:`build_escalation_context` assembles an :class:`EscalationContext` out
   of data the caller already holds (the fast result, the decisions tail, the
   quote, the strategy file) plus ONE optional callable port (news titles). The
   judge itself receives only the assembled context.
3. **An absent port is a badge, never a guess.** Every optional context group is
   tri-state: a value, an explicit *empty* answer, or ``None`` = "this port did
   not answer". ``None`` makes the dependent trigger structurally unable to fire
   and names the port in ``EscalationReport.inert_ports``. A present-but-empty
   feed (no news, no pattern) is a real answer: it simply does not fire, and it
   earns no badge.

The five triggers (evaluated, and reported, in this fixed order)
---------------------------------------------------------------
* ``direction_flip`` — both directions present and DIFFER.
  Inert ports: ``fast_result`` (no fast direction) / ``decisions`` (no prior row).
* ``stop_take_proximity`` — ``|last - band| / |band| <= 2%`` for either band.
  Inert ports: ``quote`` (no last price) / ``strat_bands`` (neither band known).
* ``event_wordlist`` — a news title contains a ``severe`` or ``high`` term;
  ``watch`` NEVER escalates. Inert port: ``news``.
* ``pattern_hit`` — the pattern feed is non-empty. Inert port: ``patterns``.
* ``opt_in`` — the strategy instance set ``deep_research``. No inert port: a
  ``False`` flag is a real answer, and a missing strategy file already shows up
  as the ``strat_bands`` badge.

Two deliberate safety choices, recorded
---------------------------------------
* **A trigger detail never echoes untrusted external text.** News titles are
  外部不可信数据; the ``event_wordlist`` detail names only OUR frozen term, its
  tier and a match count, so nothing a stranger wrote can ride into a contract
  field that a human — or a later model — reads. Pattern names are echoed
  because they come from our own 形态词典, not from the outside world.
* **Escalation only ever buys more thinking.** A false positive costs one deep
  run (budgeted, lease-admitted); it can never move money. That asymmetry is why
  the band derivation below is allowed to use the last judged position cost even
  though it may be a few minutes stale — but it is NOT why an absent port may be
  guessed: an absence is always reported as an absence.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any

from pydantic import field_validator

from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    FiniteFloat,
    NonEmptyStr,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.pipeline.contracts import (
    EscalationReport,
    EscalationTrigger,
)

__all__ = [
    "ESCALATION_THRESHOLDS_VERSION",
    "STOP_TAKE_PROXIMITY_PCT",
    "EVENT_WORDLIST_V1",
    "ESCALATING_TIERS",
    "ESCALATION_CONSTANTS_DIGEST",
    "INERT_PORT_NAMES",
    "PORT_FAST_RESULT",
    "PORT_DECISIONS",
    "PORT_QUOTE",
    "PORT_STRAT_BANDS",
    "PORT_NEWS",
    "PORT_PATTERNS",
    "EscalationContext",
    "build_escalation_context",
    "judge_escalation",
]

_LOG = logging.getLogger(__name__)


# =========================================================================== #
# frozen constants (Global Constraints, verbatim — golden-pinned)              #
# =========================================================================== #
#: the reviewed threshold vocabulary this judge implements; every report it
#: produces states it, so a verdict always names the thresholds behind it.
ESCALATION_THRESHOLDS_VERSION = "escalation-v1"

#: relative proximity band: a last price within 2% of a stop/take price (either
#: side) escalates. Exactly 2.0% fires; 2.01% does not.
STOP_TAKE_PROXIMITY_PCT = 0.02

#: the deterministic 个股重大事件 wordlist, tiered by 烈度. Read-only on purpose —
#: an importer must not be able to widen the vocabulary at runtime.
EVENT_WORDLIST_V1: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "severe": ("立案", "留置", "强制退市"),
        "high": ("问询函", "减持计划", "质押平仓"),
        "watch": ("关注函",),
    }
)

#: the tiers that escalate — 立案-class and 问询-class only. ``watch`` exists so a
#: 关注函 can be RECOGNIZED (and, later, surfaced) without buying a deep run.
ESCALATING_TIERS: tuple[str, ...] = ("severe", "high")

# ── inert-port badge vocabulary ────────────────────────────────────────────── #
# These strings travel into ``EscalationReport.inert_ports`` and from there onto
# the 落子 record, so they are named constants rather than inline literals.
PORT_FAST_RESULT = "fast_result"
PORT_DECISIONS = "decisions"
PORT_QUOTE = "quote"
PORT_STRAT_BANDS = "strat_bands"
PORT_NEWS = "news"
PORT_PATTERNS = "patterns"

#: every badge the judge can emit, in emission order.
INERT_PORT_NAMES: tuple[str, ...] = (
    PORT_FAST_RESULT,
    PORT_DECISIONS,
    PORT_QUOTE,
    PORT_STRAT_BANDS,
    PORT_NEWS,
    PORT_PATTERNS,
)

#: canonical digest over the four frozen values above. The literal hex is pinned
#: in the Task-5 test (which also carries its own independent copy of the
#: values), so a threshold/wordlist/tier-policy edit cannot land silently.
ESCALATION_CONSTANTS_DIGEST: DigestHex = content_digest(
    {
        "thresholds_version": ESCALATION_THRESHOLDS_VERSION,
        "stop_take_proximity_pct": STOP_TAKE_PROXIMITY_PCT,
        "event_wordlist": EVENT_WORDLIST_V1,
        "escalating_tiers": ESCALATING_TIERS,
    }
)


# =========================================================================== #
# EscalationContext (internal carrier)                                         #
# =========================================================================== #
class EscalationContext(ContractModel):
    """Everything :func:`judge_escalation` is allowed to know.

    Internal to Phase 10 (never registered, never persisted): it is the seam
    that makes the judge pure. Each optional field is tri-state — a value, an
    explicit empty answer (``()``), or ``None`` meaning "this port did not
    answer" — and only ``None`` produces an ``inert_ports`` badge.

    ``pattern_hits`` is ``None`` for every context :func:`build_escalation_context`
    currently produces: the 形态词典 feed (AMEND-6a) has not landed, so the honest
    value is "absent", not an invented empty feed. The field exists now so the
    judge is complete the day that feed arrives — and so a test (or that feed)
    can already exercise the trigger.
    """

    code: NonEmptyStr
    as_of: UtcDateTime
    fast_direction: NonEmptyStr | None = None
    prev_direction: NonEmptyStr | None = None
    last_price: FiniteFloat | None = None
    stop_price: FiniteFloat | None = None
    take_price: FiniteFloat | None = None
    news_titles: tuple[NonEmptyStr, ...] | None = None
    pattern_hits: tuple[NonEmptyStr, ...] | None = None
    opt_in_deep: bool = False

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        # same Phase-3 syntactic grammar as every Phase-10 contract code: an
        # industry/concept term or a partial code is refused, never repaired.
        return normalize_symbol(v).code


# =========================================================================== #
# best-effort readers over uncontracted JSON                                   #
# =========================================================================== #
# The four data inputs of the builder are all frontend-written or log-written
# JSON with NO schema behind them. Every reader below therefore returns ``None``
# on anything it does not positively recognize — a missing key, a wrong type, a
# blank string, a non-finite number. "I could not read it" is a first-class
# answer here; a default value would be a fabricated one.

#: the effective direction keys of a decision, most-authoritative first.
#: ``hybrid_direction`` IS the decision's final direction (with ``w=0`` it is the
#: LLM direction passed through, seats/api.py ``_hybrid_direction``), so it wins
#: — and because the same reader is used for the fast result and for the prior
#: row, the flip comparison is always apples-to-apples.
_DIRECTION_KEYS = ("hybrid_direction", "direction")
_STOP_PRICE_KEYS = ("stopPrice", "stop_price")
_TAKE_PRICE_KEYS = ("takePrice", "take_price")
_STOP_FRACTION_KEYS = ("stopLoss", "stop_loss")
_TAKE_FRACTION_KEYS = ("takeProfit", "take_profit")
_QUOTE_PRICE_KEYS = ("price", "last_price")


def _finite_float(value: Any) -> float | None:
    """A finite float, or ``None``. ``bool`` is never a number here."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return out if math.isfinite(out) else None


def _positive_float(value: Any) -> float | None:
    """A finite, strictly positive float, or ``None`` (a price/fraction gate)."""
    out = _finite_float(value)
    return out if (out is not None and out > 0.0) else None


def _first_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """The first non-``None`` value among ``keys`` (camelCase alias first).

    A key that is present but ``None`` does NOT shadow its snake_case alias —
    the frontend writes one spelling and the JSON is uncontracted, so an
    explicit null is "not written here", not "stop looking".
    """
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _direction_of(row: Any) -> str | None:
    """The effective direction of a decision row / fast result, or ``None``."""
    if not isinstance(row, Mapping):
        return None
    for key in _DIRECTION_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _latest_decision_row(
    decisions_tail: Sequence[Mapping[str, Any]] | None, code: str
) -> Mapping[str, Any] | None:
    """The most recent prior ``kind='decide'`` row for ``code``, or ``None``.

    "Most recent" = LAST in sequence order: ``var/seats_decisions.jsonl`` is an
    append-only chronological log and the caller hands us its tail. Codes are
    compared through the Phase-3 grammar because the log stores ``SZ300750``
    while a strategy binds ``300750``; an unparseable row code simply does not
    match (it is never repaired into one).

    **Caller contract:** the tail must be the one observed BEFORE the current
    fast judgment was persisted — a tail that already contains this tick's own
    row would compare the fast direction against itself, and no flip could ever
    be seen.
    """
    latest: Mapping[str, Any] | None = None
    for row in decisions_tail or ():
        if not isinstance(row, Mapping):
            continue
        kind = row.get("kind")
        if kind is not None and kind != "decide":
            continue
        raw_code = row.get("code")
        if not isinstance(raw_code, str):
            continue
        try:
            if normalize_symbol(raw_code).code != code:
                continue
        except (TypeError, ValueError):
            continue  # junk code in an uncontracted log: not this stock
        latest = row
    return latest


def _quote_price(quote: Mapping[str, Any] | None) -> float | None:
    """The last traded price from the quote port, or ``None``.

    An EXPLICITLY stale quote (``fresh`` present and falsy) yields ``None``: a
    known-stale price must not arm a band trigger. A quote that carries no
    ``fresh`` key at all is taken at face value — the freshness gate belongs to
    the caller that owns the collector.
    """
    if not isinstance(quote, Mapping):
        return None
    if "fresh" in quote and not quote.get("fresh"):
        return None
    return _positive_float(_first_value(quote, _QUOTE_PRICE_KEYS))


def _band_prices(
    strat: Mapping[str, Any] | None, latest_row: Mapping[str, Any] | None
) -> tuple[float | None, float | None]:
    """``(stop_price, take_price)`` from the strategy clock, best-effort.

    Two shapes are accepted, in this order:

    1. **explicit band prices** — ``clock.stopPrice`` / ``clock.takePrice``, used
       verbatim;
    2. **fractions + an entry reference** — the shape the real
       ``var/archive/strat_*.json`` actually stores (``clock.stopLoss`` 0.08 /
       ``clock.takeProfit`` 0.18 are FRACTIONS, not prices), converted against
       the position cost the latest decision row was judged against
       (``hold_entry``, written by seats ``_persist_decision`` only when a
       position is held).

    Only the LATEST matching row supplies the entry: an older row's cost would
    describe a position that may since have been closed. Without an entry the
    bands stay ``None`` (inert-with-badge) — the fractions alone are not prices
    and are never treated as if they were. A fraction ``>= 1`` (or any input
    that lands on a non-positive band) yields ``None`` for that band.
    """
    if not isinstance(strat, Mapping):
        return None, None
    clock = strat.get("clock")
    if not isinstance(clock, Mapping):
        return None, None

    stop = _positive_float(_first_value(clock, _STOP_PRICE_KEYS))
    take = _positive_float(_first_value(clock, _TAKE_PRICE_KEYS))
    if stop is not None and take is not None:
        return stop, take

    entry = _positive_float(
        latest_row.get("hold_entry") if isinstance(latest_row, Mapping) else None
    )
    if entry is None:
        return stop, take
    if stop is None:
        fraction = _positive_float(_first_value(clock, _STOP_FRACTION_KEYS))
        if fraction is not None:
            stop = _positive_float(entry * (1.0 - fraction))
    if take is None:
        fraction = _positive_float(_first_value(clock, _TAKE_FRACTION_KEYS))
        if fraction is not None:
            take = _positive_float(entry * (1.0 + fraction))
    return stop, take


def _news_titles(
    news_titles_fn: Callable[[str], Sequence[str]] | None, code: str
) -> tuple[str, ...] | None:
    """Titles from the injected news port, or ``None`` = the port did not answer.

    The port is the ONE piece of I/O in this module's reach, so its failure mode
    matters: a raising or nonsense-returning port yields ``None`` (inert, with a
    badge on the report) and a WARNING — never an empty tuple, which would claim
    "there is no news" on the strength of a network error.
    """
    if news_titles_fn is None:
        return None
    try:
        raw = news_titles_fn(code)
    except Exception as exc:  # noqa: BLE001 — any port failure is an ABSENCE
        _LOG.warning(
            "escalation news port failed for %s (trigger left inert, no guess): %s",
            code, exc,
        )
        return None
    if raw is None:
        return None
    if isinstance(raw, (str, bytes, Mapping)):
        _LOG.warning(
            "escalation news port returned %s for %s; treated as absent "
            "(a sequence of titles is required)", type(raw).__name__, code,
        )
        return None
    try:
        items = list(raw)
    except TypeError:
        _LOG.warning(
            "escalation news port returned a non-iterable %s for %s; "
            "treated as absent", type(raw).__name__, code,
        )
        return None
    return tuple(
        item.strip() for item in items if isinstance(item, str) and item.strip()
    )


# =========================================================================== #
# context assembly                                                             #
# =========================================================================== #
def build_escalation_context(
    *,
    code: str,
    as_of: datetime,
    fast_result: Mapping[str, Any],
    decisions_tail: Sequence[Mapping[str, Any]],
    quote: Mapping[str, Any] | None = None,
    strat: Mapping[str, Any] | None = None,
    news_titles_fn: Callable[[str], Sequence[str]] | None = None,
) -> EscalationContext:
    """Assemble the judge's context from INJECTED data only.

    Every argument is data the caller already holds (or, for ``news_titles_fn``,
    a port the caller injects) — this function opens no file, makes no request
    and consults no clock of its own. The three port arguments default to
    "absent" so a caller can never be surprised into a guessed value.

    ``code`` is normalized through the Phase-3 grammar and the canonical
    six-digit form is what reaches the news port and the decision-row matching.
    An unparseable code raises :class:`ValueError` — a subject we cannot name is
    refused, never judged.
    """
    canonical = normalize_symbol(code).code
    latest_row = _latest_decision_row(decisions_tail, canonical)
    stop_price, take_price = _band_prices(strat, latest_row)
    return EscalationContext(
        code=canonical,
        as_of=as_of,
        fast_direction=_direction_of(fast_result),
        prev_direction=_direction_of(latest_row),
        last_price=_quote_price(quote),
        stop_price=stop_price,
        take_price=take_price,
        news_titles=_news_titles(news_titles_fn, canonical),
        # AMEND-6a (形态词典 feed) has not landed: there is no pattern port to
        # inject yet, so the honest value is "absent" → inert-with-badge.
        pattern_hits=None,
        opt_in_deep=bool(strat.get("deep_research"))
        if isinstance(strat, Mapping)
        else False,
    )


# =========================================================================== #
# the judge                                                                    #
# =========================================================================== #
def _wordlist_match(titles: Sequence[str]) -> tuple[str, str, int] | None:
    """``(tier, term, matched_title_count)`` for the highest tier hit, or ``None``.

    Deterministic by construction: tiers are scanned in :data:`ESCALATING_TIERS`
    order (severity first), titles in the order given, terms in wordlist order.
    ``watch`` is not scanned at all — it can never escalate.
    """
    for tier in ESCALATING_TIERS:
        terms = EVENT_WORDLIST_V1[tier]
        matched = tuple(
            title for title in titles if any(term in title for term in terms)
        )
        if not matched:
            continue
        term = next(t for t in terms if t in matched[0])
        return tier, term, len(matched)
    return None


def judge_escalation(ctx: EscalationContext) -> EscalationReport:
    """The verdict: pure, total, zero-I/O, deterministic.

    Triggers are evaluated — and reported — in the fixed order
    ``direction_flip → stop_take_proximity → event_wordlist → pattern_hit →
    opt_in``; badges in the fixed order of :data:`INERT_PORT_NAMES`. Nothing
    here reads a clock, a file or a network socket, so two runs over the same
    context produce byte-identical reports.

    The ``escalate`` ⇔ ``triggers_hit`` biconditional is NOT re-checked here —
    :class:`~guanlan_v2.orchestration.pipeline.contracts.EscalationReport`'s own
    validator owns it, and duplicating it would let the two drift.
    """
    triggers: list[EscalationTrigger] = []
    inert: list[str] = []

    # ── 1. direction flip ─────────────────────────────────────────────────── #
    if ctx.fast_direction is None:
        inert.append(PORT_FAST_RESULT)
    if ctx.prev_direction is None:
        inert.append(PORT_DECISIONS)
    if (
        ctx.fast_direction is not None
        and ctx.prev_direction is not None
        and ctx.fast_direction != ctx.prev_direction
    ):
        triggers.append(
            EscalationTrigger(
                kind="direction_flip",
                detail=(
                    f"fast direction {ctx.fast_direction!r} differs from the "
                    f"previous decision {ctx.prev_direction!r}"
                ),
            )
        )

    # ── 2. stop/take proximity ────────────────────────────────────────────── #
    if ctx.last_price is None:
        inert.append(PORT_QUOTE)
    if ctx.stop_price is None and ctx.take_price is None:
        inert.append(PORT_STRAT_BANDS)
    if ctx.last_price is not None:
        for label, band in (("stop", ctx.stop_price), ("take", ctx.take_price)):
            # a 0.0 band has no relative neighbourhood — skip it rather than
            # divide by zero (the judge stays total for any context, including
            # one constructed directly rather than through the builder).
            if band is None or band == 0.0:
                continue
            distance = abs(ctx.last_price - band) / abs(band)
            if distance <= STOP_TAKE_PROXIMITY_PCT:
                triggers.append(
                    EscalationTrigger(
                        kind="stop_take_proximity",
                        detail=(
                            f"last price {ctx.last_price!r} is {distance:.4%} "
                            f"from the {label} band {band!r} "
                            f"(<= {STOP_TAKE_PROXIMITY_PCT:.2%})"
                        ),
                    )
                )
                break  # one trigger per kind; the nearer band is not ranked

    # ── 3. event wordlist ─────────────────────────────────────────────────── #
    if ctx.news_titles is None:
        inert.append(PORT_NEWS)
    else:
        hit = _wordlist_match(ctx.news_titles)
        if hit is not None:
            tier, term, matched_n = hit
            # NOTE: the title text itself is untrusted external data and never
            # enters the detail — only our own frozen term, its tier and a count.
            triggers.append(
                EscalationTrigger(
                    kind="event_wordlist",
                    detail=(
                        f"{tier}-tier term {term!r} matched in {matched_n} of "
                        f"{len(ctx.news_titles)} news title(s)"
                    ),
                )
            )

    # ── 4. pattern hit ────────────────────────────────────────────────────── #
    if ctx.pattern_hits is None:
        inert.append(PORT_PATTERNS)
    elif ctx.pattern_hits:
        # pattern names come from our own 形态词典, not from outside — safe to echo.
        triggers.append(
            EscalationTrigger(
                kind="pattern_hit",
                detail=(
                    f"{len(ctx.pattern_hits)} pattern(s) matched: "
                    + "、".join(ctx.pattern_hits)
                ),
            )
        )

    # ── 5. per-strategy opt-in ────────────────────────────────────────────── #
    # No badge here: a strategy that did not set the flag (or a stock with no
    # strategy file at all) is a real "not opted in", not an unanswered port —
    # and the missing strategy file already shows up as the strat_bands badge.
    if ctx.opt_in_deep:
        triggers.append(
            EscalationTrigger(
                kind="opt_in",
                detail="strategy instance opted in (deep_research=true)",
            )
        )

    return EscalationReport(
        code=ctx.code,
        as_of=ctx.as_of,
        triggers_hit=tuple(triggers),
        escalate=bool(triggers),
        inert_ports=tuple(inert),
    )
