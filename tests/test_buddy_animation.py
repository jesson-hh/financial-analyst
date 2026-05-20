"""Tests for KLineSpinner (v1.5.1 — finance-themed thinking animation)."""
from __future__ import annotations

import pytest

from financial_analyst.buddy.animation import (
    KLineSpinner, STATUS_THINKING, STATUS_TOOL_CALLING,
    STATUS_TOOL_PARSING, STATUS_TOOL_FINISHED, _Candle,
)


def test_spinner_initialises_with_full_window():
    spinner = KLineSpinner(n_candles=18)
    assert len(spinner.candles) == 18
    # First and last candles should be real Candle dataclasses
    assert all(isinstance(c, _Candle) for c in spinner.candles)


def test_spinner_render_returns_group_with_chart_plus_meta_rows():
    """Render = 5 chart rows + 1 delta row + 1 status row = 7 lines."""
    spinner = KLineSpinner(n_candles=10, height=5)
    group = spinner.render()
    # Rich Group renderables — count chart + delta + status
    renderables = list(group.renderables)
    assert len(renderables) == 5 + 2, f"expected 7 rows, got {len(renderables)}"


def test_spinner_render_with_no_candles_returns_safe_placeholder():
    """An empty spinner (somehow) should not crash."""
    spinner = KLineSpinner(n_candles=18)
    spinner.candles.clear()
    group = spinner.render()
    # Just verify no exception and we get back a Group
    assert group is not None


def test_tick_advances_frame_and_shifts_candles():
    spinner = KLineSpinner(n_candles=14)
    original_first = spinner.candles[0]
    spinner.tick()
    # First candle should now be the OLD second candle (shifted left)
    assert len(spinner.candles) == 14  # window stays constant
    assert spinner._frame == 1
    # The shifted-in candle's open should match the prior close (continuity)
    assert spinner.candles[-1].o == pytest.approx(spinner.candles[-2].c)


def test_set_status_persists_through_render():
    spinner = KLineSpinner()
    spinner.set_status("调用 chain_for...")
    group = spinner.render()
    # Last renderable is the status line; concat plain text to verify
    last_row = list(group.renderables)[-1]
    assert "chain_for" in last_row.plain


def test_status_constants_are_strings():
    """Sanity check that the imported constants are non-empty."""
    for s in (STATUS_THINKING, STATUS_TOOL_CALLING,
              STATUS_TOOL_PARSING, STATUS_TOOL_FINISHED):
        assert isinstance(s, str)
        assert s.strip()


def test_render_styles_up_candles_green_down_candles_red():
    """Build a spinner with one deterministic up and one deterministic down
    candle, then check the body styles via Text.spans inspection."""
    spinner = KLineSpinner(n_candles=2)
    spinner.candles = [
        _Candle(o=100.0, h=104.0, l=99.0, c=103.0),  # up
        _Candle(o=103.0, h=104.0, l=98.0, c=99.0),   # down
    ]
    group = spinner.render()
    chart_rows = list(group.renderables)[:5]  # first 5 = chart
    # Build a flat list of (char, style) across all chart cells
    cells = []
    for row in chart_rows:
        # Iterate spans; each span has (start, end, style)
        for span in row.spans:
            char = row.plain[span.start:span.end]
            style = str(span.style) if span.style else ""
            if char.strip() and not char.isspace():
                cells.append((char, style))
    # At least one green and one red cell present
    has_green = any("bright_green" in c[1] for c in cells)
    has_red = any("bright_red" in c[1] for c in cells)
    assert has_green, f"no green candle in cells: {cells[:10]}"
    assert has_red, f"no red candle in cells: {cells[:10]}"
