"""K-line spinner — finance-themed thinking animation for the buddy REPL.

Renders an animated candlestick chart that updates ~8 fps while the LLM is
thinking or a tool is running. The candles do a bounded random walk; each
new tick shifts the chart left by one and appends a fresh candle on the
right. Up-days render green, down-days red, doji as a horizontal bar.

The status line below the chart shows what the agent is doing
("思考中...", "调用 chain_for...", "等待 LLM..."), and a delta line shows
the close-vs-open % of the most recent candle so the animation feels
like a tiny live ticker.

Usage::

    spinner = KLineSpinner()
    with Live(spinner.render(), refresh_per_second=8, transient=True) as live:
        # spawn an asyncio task that calls spinner.tick() + live.update(spinner.render())
        # every ~120ms; the spinner is transient so it disappears when Live exits.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import List

from rich.console import Group
from rich.text import Text


@dataclass
class _Candle:
    o: float
    h: float
    l: float
    c: float

    @property
    def is_up(self) -> bool:
        return self.c >= self.o


@dataclass
class KLineSpinner:
    """Animated K-line chart for the chat thinking indicator."""

    n_candles: int = 18
    height: int = 5
    status: str = "思考中..."
    candles: List[_Candle] = field(default_factory=list)
    _last_close: float = 100.0
    _frame: int = 0

    def __post_init__(self) -> None:
        random.seed()  # use system entropy so every chat run looks different
        for _ in range(self.n_candles):
            self._append_random_candle()

    # ----- mutation ----------------------------------------------------------

    def _append_random_candle(self) -> None:
        """Append one random-walk candle on the right edge."""
        # Bounded random walk: drift ~0, volatility ~2.5%, occasional shock
        drift = 0.0
        sigma = 2.5
        if random.random() < 0.08:
            sigma = 6.0  # occasional larger move
        open_ = self._last_close
        close_ = open_ + random.gauss(drift, sigma)
        wick_up = abs(random.gauss(0, 1.0))
        wick_dn = abs(random.gauss(0, 1.0))
        high_ = max(open_, close_) + wick_up
        low_ = min(open_, close_) - wick_dn
        self.candles.append(_Candle(open_, high_, low_, close_))
        self._last_close = close_

    def tick(self) -> None:
        """Advance one frame — shift candles left, append a new one."""
        if self.candles:
            self.candles.pop(0)
        self._append_random_candle()
        self._frame += 1

    def set_status(self, status: str) -> None:
        self.status = status

    # ----- render ------------------------------------------------------------

    def render(self) -> Group:
        """Return a Rich Group: 5 chart rows + delta row + status row."""
        if not self.candles:
            return Group(Text("(initialising)", style="dim"))

        # Y-scale: use 10th/90th percentile of opens+closes so a rare outlier
        # candle doesn't compress every other candle into doji-height.
        bodies = sorted(p for c in self.candles for p in (c.o, c.c))
        n = len(bodies)
        lo_body = bodies[max(0, n // 10)]
        hi_body = bodies[min(n - 1, n - 1 - n // 10)]
        # Add some headroom for wicks
        wick_pad = (hi_body - lo_body) * 0.15 if hi_body > lo_body else 1.0
        lo = lo_body - wick_pad
        hi = hi_body + wick_pad
        span = max(hi - lo, 1e-9)

        def y_of(price: float) -> int:
            """0 = top row, height-1 = bottom row.
            Clamps prices outside the trimmed range to the edges."""
            v = (price - lo) / span
            row = int(round((self.height - 1) * (1.0 - v)))
            return max(0, min(self.height - 1, row))

        chart_rows: List[Text] = [Text() for _ in range(self.height)]

        for candle in self.candles:
            color = "bright_green" if candle.is_up else "bright_red"
            y_h = y_of(candle.h)
            y_l = y_of(candle.l)
            y_o = y_of(candle.o)
            y_c = y_of(candle.c)
            body_top, body_bot = min(y_o, y_c), max(y_o, y_c)

            for row_idx in range(self.height):
                if body_top == body_bot and row_idx == body_top:
                    ch, style = "━", color  # doji
                elif body_top <= row_idx <= body_bot:
                    ch, style = "█", color  # body
                elif y_h <= row_idx < body_top:
                    ch, style = "│", color  # upper wick
                elif body_bot < row_idx <= y_l:
                    ch, style = "│", color  # lower wick
                else:
                    ch, style = " ", None

                chart_rows[row_idx].append(ch, style=style)
                chart_rows[row_idx].append(" ")  # 1-space gutter

        # Bottom line: delta % of most recent candle (single +/- sign)
        last = self.candles[-1]
        delta_pct = (last.c - last.o) / last.o * 100.0 if last.o else 0.0
        delta_color = "bright_green" if delta_pct >= 0 else "bright_red"
        delta_line = Text()
        delta_line.append("  ", style="")
        delta_line.append(
            f"{delta_pct:+5.2f}%", style=f"{delta_color} bold"
        )
        delta_line.append(f"   bar #{self._frame:03d}", style="dim")

        # Status line
        status_line = Text()
        status_line.append("  ▸ ", style="cyan bold")
        status_line.append(self.status, style="cyan")

        return Group(*chart_rows, delta_line, status_line)


# Status string presets used by the REPL --------------------------------------

STATUS_THINKING = "思考中…"
STATUS_TOOL_CALLING = "调用 {tool}…"
STATUS_TOOL_PARSING = "解析结果…"
STATUS_TOOL_FINISHED = "整合中…"
