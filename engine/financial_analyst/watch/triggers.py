"""watch/triggers.py — 无组合触发适配.

The 盯盘 loop has no portfolio/broker; it only knows a ``WatchItem`` (code +
optional ``avg_cost``/``stop_loss``). This module bridges that to the *pure*
``backtest.intraday.IntradayTrigger`` (zero IO, zero LLM) so the same validated
key-point logic (``breakout_high`` / ``volume_surge`` / ``stop_break``) drives
realtime watching:

* **``breakout_high`` / ``volume_surge``** — decision-class; need only the 5min
  bars, no position.
* **``stop_break``** — risk-class; ``IntradayTrigger.check`` only fires it when a
  ``position`` with ``stop_loss>0`` is supplied AND ``sellable_qty>0``. We
  synthesize a lightweight ``Position`` from ``WatchItem.stop_loss/avg_cost``
  (1 sellable lot) so a user-set stop can fire without a real book. Items with
  **no** ``stop_loss`` pass ``position=None`` → the risk channel is silently
  skipped (no doomed signal), matching design §6.

Dedup / cooldown / per-session caps are the *loop's* responsibility — the wrapped
``IntradayTrigger`` keeps its own per-day decision-class caps + ``reset_day()``,
re-exposed here verbatim.

``news_trigger`` is independent of the bar engine: a keyword hit on a realtime
headline synthesizes a ``kind="news"`` ``TriggerEvent`` (``bar_index=-1`` — not
tied to any 5min bar). Headline-level dedup is left to the loop.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

import pandas as pd

from financial_analyst.backtest.intraday import (
    IntradayTrigger,
    IntradayTriggerConfig,
    TriggerEvent,
)
from financial_analyst.backtest.portfolio import Position
from financial_analyst.watch.models import WatchItem

# Default 命中关键词 for the realtime news channel. Conservative, high-signal A 股
# event words (利好/利空 mixed); the loop may pass its own list. Kept here so the
# default behavior is testable and self-documenting.
DEFAULT_NEWS_KEYWORDS: tuple[str, ...] = (
    "重大合同", "中标", "签订", "收购", "重组", "增持", "回购", "业绩预增",
    "涨停", "立案", "处罚", "减持", "业绩预减", "亏损", "退市", "停牌",
    "诉讼", "违规", "问询", "解禁", "定增", "股权激励",
)


class WatchTrigger:
    """Portfolio-free wrapper around :class:`IntradayTrigger`.

    Holds a single stateful :class:`IntradayTrigger` (per-day dedup + caps).
    Construct once per 盯盘 session; call :meth:`reset_day` at each trading-day
    head (the loop does this).
    """

    def __init__(self, cfg: Optional[IntradayTriggerConfig] = None) -> None:
        # ``enabled`` is the engine's master switch and is NOT consulted inside
        # ``IntradayTrigger.check`` — but we set it True so the config reads
        # truthfully as "an active watcher" and stays correct if that ever
        # changes. The per-channel ``*_enabled`` flags keep their defaults.
        if cfg is None:
            cfg = IntradayTriggerConfig(enabled=True)
        self.cfg = cfg
        self._trigger = IntradayTrigger(cfg)

    def reset_day(self) -> None:
        """Clear the wrapped trigger's per-day dedup + counters."""
        self._trigger.reset_day()

    def check_item(self, item: WatchItem, bars_5min: pd.DataFrame,
                   i: Optional[int] = None) -> Optional[TriggerEvent]:
        """Evaluate ``item`` against the 5min prefix ``bars_5min.iloc[:i+1]``.

        ``i`` is the index of the bar that just closed (末行). Defaults to the
        last row so callers feeding a ready prefix needn't pass it.

        A ``WatchItem`` with a positive ``stop_loss`` synthesizes a lightweight
        held :class:`Position` (1 sellable lot) so the risk channel can fire;
        without a stop, ``position=None`` and ``stop_break`` is skipped.
        """
        if bars_5min is None or len(bars_5min) == 0:
            return None
        if i is None:
            i = len(bars_5min) - 1
        bars_upto_t = bars_5min.iloc[: i + 1]

        position: Optional[Position] = None
        sellable_qty = 0
        if item.stop_loss is not None and item.stop_loss > 0:
            # one synthetic 100-share lot, already sellable (no T+1 lock here:
            # 盯盘 is advisory, not a real fill ledger), carrying the user stop.
            position = Position(
                code=item.code,
                qty=100,
                avg_cost=float(item.avg_cost) if item.avg_cost is not None else 0.0,
                stop_loss=float(item.stop_loss),
            )
            sellable_qty = 100

        return self._trigger.check(item.code, bars_upto_t, position, sellable_qty, i)


def news_trigger(code: str, headlines: Sequence[str],
                 keywords: Optional[Iterable[str]] = None) -> Optional[TriggerEvent]:
    """Synthesize a ``kind="news"`` :class:`TriggerEvent` on the first headline
    that contains any of ``keywords`` (defaults to :data:`DEFAULT_NEWS_KEYWORDS`).

    Returns ``None`` if nothing matches. ``bar_index=-1`` marks it as not tied to
    any 5min bar; ``bar_time=""`` and ``metric=0.0`` keep the dataclass total.
    Headline-level dedup across ticks is the loop's job.
    """
    if not headlines:
        return None
    kws: List[str] = list(keywords) if keywords is not None else list(DEFAULT_NEWS_KEYWORDS)
    for hl in headlines:
        if not hl:
            continue
        hit = next((kw for kw in kws if kw and kw in hl), None)
        if hit is not None:
            return TriggerEvent(
                code=code,
                kind="news",
                bar_time="",
                bar_index=-1,
                detail=f"新闻命中[{hit}]: {hl}",
                metric=0.0,
                is_risk=False,
            )
    return None


def negative_event_trigger(code: str, warnings: Optional[dict],
                           min_severity: int = 2) -> Optional[TriggerEvent]:
    """Synthesize a ``kind="negative_event"`` **risk** event when ``code`` carries
    a tdx_f10 warning with ``severity >= min_severity`` (B1).

    ``warnings`` is the dict from :func:`watch.signals.load_negative_warnings`
    (``{code: {severity, title, event_date}}``). severity≥2 = 立案 / 处罚 / 减持 /
    业绩预减 / 退市风险 等 — the loop turns this into a *hard* sell (held) / 禁建仓
    (not held) **without** an LLM call. ``bar_index=-1`` (not tied to a 5min bar).
    Returns ``None`` if no qualifying warning.
    """
    if not warnings:
        return None
    w = warnings.get(code)
    if not isinstance(w, dict):
        return None
    try:
        sev = int(w.get("severity", 0) or 0)
    except (TypeError, ValueError):
        return None
    if sev < min_severity:
        return None
    title = str(w.get("title", ""))[:40]
    date = str(w.get("event_date", ""))
    return TriggerEvent(
        code=code,
        kind="negative_event",
        bar_time="",
        bar_index=-1,
        detail=f"负向事件 sev{sev} ({date}): {title}",
        metric=float(sev),
        is_risk=True,
    )


# Risk-side 量能 regime labels that warrant an advisor look (B2). 'bounce' is
# the +ve label and is intentionally excluded (no risk alert on a bounce).
_VOL_REGIME_RISK = ("super_distr", "distr", "tail_surge")


def vol_regime_trigger(code: str, regime: Optional[dict],
                       risk_labels: Iterable[str] = _VOL_REGIME_RISK) -> Optional[TriggerEvent]:
    """Synthesize a ``kind="vol_regime"`` **risk** event when ``code`` is in a
    distribution regime (B2).

    ``regime`` is the dict from :func:`watch.signals.compute_vol_regime`. Fires on
    ``regime_label`` in ``risk_labels`` (super_distr / distr / tail_surge — all with
    negative forward spread). Routed to the *advisor* (which carries the super_distr
    knowledge from package A), not a hard rule, since regime is probabilistic. The
    ``metric`` carries the expected forward spread (pp). ``None`` if not a risk regime.
    """
    if not regime:
        return None
    label = regime.get("regime_label")
    if label not in tuple(risk_labels):
        return None
    spread = regime.get("expected_spread_pp", 0.0)
    return TriggerEvent(
        code=code,
        kind="vol_regime",
        bar_time="",
        bar_index=-1,
        detail=f"量能regime[{label}]: {regime.get('detail', '')}",
        metric=float(spread) if spread is not None else 0.0,
        is_risk=True,
    )
