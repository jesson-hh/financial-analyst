"""K-line spinner — finance-themed thinking animation for the buddy REPL.

Single-line sparkline format::

    ⠿ 整合中…  ▂▅▁▇▃   -1.34%   #167

- 5 candles wide (sparkline-style, 1 character per candle)
- Each candle's vertical char (▁▂▃▄▅▆▇█) encodes the close price within
  the window range; the colour encodes direction (green=up, red=down).
- Braille spinner dot on the left cycles every frame for a constant
  "alive" pulse even when candles haven't shifted yet.
- Live delta % + frame counter on the right.
- One blank padding row above so the spinner doesn't slam against the
  previous text.

Usage::

    spinner = KLineSpinner()
    with Live(spinner.render(), refresh_per_second=10, transient=True) as live:
        # spawn an asyncio task that calls spinner.tick() + live.update(spinner.render())
        # every ~100 ms; the spinner is transient so it disappears when Live exits.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import List

from rich.console import Group
from rich.text import Text


# 8 vertical-fill levels for the sparkline (lowest → highest)
_LEVELS = "▁▂▃▄▅▆▇█"

# Braille spinner — 8 frames give a smooth rotating-dots feel at 10 fps
_SPINNER_DOTS = "⠋⠙⠹⠸⠼⠴⠦⠧"


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
    """Compact sparkline K-line for the chat thinking indicator.

    Default render is one line of text, padded above by a blank line so
    it doesn't sit flush against the previous output. The visual budget
    is roughly:

        "  "  + braille dot + " " + status + "  "
            + 5 candle chars + "   " + ±delta% + "   " + #frame

    ≈ 30-40 columns; safe on any terminal ≥ 60 chars wide.
    """

    n_candles: int = 5
    status: str = "思考中…"
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
        """Return a Rich Group: one blank padding row + one inline ticker row.

        Inline row layout (left to right):
            "  "  + spinner dot + " " + status text + "  "
            + 5 colored sparkline chars + "   " + ±delta% + "   " + #frame
        """
        if not self.candles:
            return Group(Text(""), Text("  (initialising)", style="dim"))

        # Y-scale across the visible closes; pad ±20% so the chars never
        # all stick to the extremes when the walk is monotonic.
        closes = [c.c for c in self.candles]
        lo = min(closes)
        hi = max(closes)
        pad = (hi - lo) * 0.2 if hi > lo else max(lo * 0.01, 1.0)
        lo -= pad
        hi += pad
        span = max(hi - lo, 1e-9)

        def level_char(price: float) -> str:
            """Pick one of 8 vertical-fill chars based on price within [lo, hi]."""
            v = (price - lo) / span
            idx = min(len(_LEVELS) - 1, max(0, int(v * len(_LEVELS))))
            return _LEVELS[idx]

        # Spinner: rotate one frame per tick so the dot is alive even when
        # candles haven't materially changed yet.
        dot = _SPINNER_DOTS[self._frame % len(_SPINNER_DOTS)]

        # Last-candle stats for the right-side ticker
        last = self.candles[-1]
        delta_pct = (last.c - last.o) / last.o * 100.0 if last.o else 0.0
        delta_color = "bright_green" if delta_pct >= 0 else "bright_red"

        line = Text("  ")
        line.append(dot, style="cyan bold")
        line.append(" ")
        line.append(self.status, style="cyan")
        line.append("  ")

        # Sparkline candles — color encodes direction, char encodes level
        for c in self.candles:
            color = "bright_green" if c.is_up else "bright_red"
            line.append(level_char(c.c), style=f"{color} bold")

        line.append(f"   {delta_pct:+5.2f}%", style=f"{delta_color} bold")
        line.append(f"   #{self._frame:03d}", style="dim")

        # Blank padding row above so the spinner doesn't slam against
        # the previous output.
        return Group(Text(""), line)


# Status string presets used by the REPL --------------------------------------

STATUS_THINKING = "思考中…"
STATUS_TOOL_CALLING = "调用 {tool}…"
STATUS_TOOL_PARSING = "解析结果…"
STATUS_TOOL_FINISHED = "整合中…"
