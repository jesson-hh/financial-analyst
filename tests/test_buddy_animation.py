"""Tests for KLineSpinner (v1.5.1 — finance-themed thinking animation)."""
from __future__ import annotations

import pytest

from financial_analyst.buddy.animation import (
    KLineSpinner, STATUS_THINKING, STATUS_TOOL_CALLING,
    STATUS_TOOL_PARSING, STATUS_TOOL_FINISHED, _Candle,
)


def test_spinner_initialises_with_default_window():
    """v1.5.3+: default is 5 candles (compact sparkline), not 18."""
    spinner = KLineSpinner()
    assert len(spinner.candles) == 5
    assert all(isinstance(c, _Candle) for c in spinner.candles)


def test_spinner_render_returns_two_rows_blank_plus_inline():
    """v1.5.3+: compact format is one blank padding row + one inline row."""
    spinner = KLineSpinner(n_candles=5)
    group = spinner.render()
    renderables = list(group.renderables)
    assert len(renderables) == 2, f"expected 2 rows, got {len(renderables)}"
    # First row is blank padding
    assert renderables[0].plain == ""
    # Second row contains both status and a sparkline + delta
    inline = renderables[1].plain
    assert "思考中" in inline or "整合" in inline or "调用" in inline or "…" in inline
    # 5 sparkline chars somewhere in there
    spark_chars = "▁▂▃▄▅▆▇█"
    assert any(c in inline for c in spark_chars)


def test_spinner_render_with_no_candles_returns_safe_placeholder():
    """An empty spinner (somehow) should not crash."""
    spinner = KLineSpinner()
    spinner.candles.clear()
    group = spinner.render()
    assert group is not None


def test_tick_advances_frame_and_shifts_candles():
    spinner = KLineSpinner(n_candles=5)
    spinner.tick()
    # Window size constant
    assert len(spinner.candles) == 5
    assert spinner._frame == 1
    # Continuity: new open == prior close
    assert spinner.candles[-1].o == pytest.approx(spinner.candles[-2].c)


def test_set_status_persists_through_render():
    spinner = KLineSpinner()
    spinner.set_status("调用 chain_for...")
    group = spinner.render()
    # Inline row (the 2nd) carries the status text
    inline = list(group.renderables)[-1]
    assert "chain_for" in inline.plain


def test_status_constants_are_strings():
    """Sanity check that the imported constants are non-empty."""
    for s in (STATUS_THINKING, STATUS_TOOL_CALLING,
              STATUS_TOOL_PARSING, STATUS_TOOL_FINISHED):
        assert isinstance(s, str)
        assert s.strip()


def test_render_styles_up_candles_green_down_candles_red():
    """Sparkline candles use bright_green/bright_red per direction.
    Verify by inspecting Text spans on the inline row."""
    spinner = KLineSpinner(n_candles=2)
    spinner.candles = [
        _Candle(o=100.0, h=104.0, l=99.0, c=103.0),  # up
        _Candle(o=103.0, h=104.0, l=98.0, c=99.0),   # down
    ]
    group = spinner.render()
    inline = list(group.renderables)[-1]
    # Walk styled segments; collect (char, style) per chunk
    cells = []
    for span in inline.spans:
        char = inline.plain[span.start:span.end]
        style = str(span.style) if span.style else ""
        if char.strip():
            cells.append((char, style))
    has_green = any("bright_green" in c[1] for c in cells)
    has_red = any("bright_red" in c[1] for c in cells)
    assert has_green, f"no green candle in cells: {cells}"
    assert has_red, f"no red candle in cells: {cells}"
