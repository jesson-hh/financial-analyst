from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "ui" / "seats" / "luozi-app.jsx").read_text(encoding="utf-8")
DATA = (ROOT / "ui" / "seats" / "luozi-data.jsx").read_text(encoding="utf-8")
FLEET = (ROOT / "ui" / "seats" / "luozi-fleet.jsx").read_text(encoding="utf-8")
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "seats" / "观澜 · 落子.html").read_text(encoding="utf-8")


def test_expensive_market_pollers_pause_outside_open_session():
    # Quote, orderbook/ticks, live 5m and live 1m all retain one snapshot read but
    # create their recurring interval only while the server says the market is open.
    assert APP.count("if (liveMarketOpen !== true) return") >= 4
    assert "[page, code, liveMarketOpen]" in APP
    assert "[page, tf, code, liveMarketOpen]" in APP

    assert "function TicketList({ code, onSelect, poolTick, assignments, assignmentMode, onConfigureWatch, marketOpen })" in FLEET
    assert "if (marketOpen !== true) return" in FLEET
    assert "[poolTick, marketOpen]" in FLEET

    order_signature = PANELS.split("function OrderWatchPanel(", 1)[1].split(")", 1)[0]
    for prop in ("code", "name", "onTrigger", "mode", "strategies", "asOf", "seatId", "symbol", "marketOpen"):
        assert prop in order_signature
    assert "if (marketOpen !== true) return" in PANELS
    assert "[mode, order, liveFired, code, marketOpen]" in PANELS
    assert "休市暂停 · 下次开盘自动恢复" in PANELS

    # The 30-second watcher heartbeat remains, so 09:30/13:00 can wake pollers.
    assert "const iv = setInterval(pull, 30000);" in APP


def test_quote_labels_use_server_session_state_not_daily_bar_lag():
    assert "function lzQuoteSessionLabel(quote)" in PANELS
    for label in ("实时盘中", "午间休市 · 最新快照", "已收盘 · 最新快照", "休市 · 最新快照"):
        assert label in PANELS
    assert "quote.fresh ? '实时盘中' : '最新收盘'" not in PANELS
    assert "marketPhase: j.market_phase" in DATA
    assert "newerThanSettledBar: j.newer_than_settled_bar" in DATA
    assert "source: j.source || ''" in DATA
    assert "capturedAt: j.captured_at || ''" in DATA


def test_changed_seats_assets_have_one_cache_generation():
    assert "luozi-fleet.jsx?v=20260815session1" in HTML
    for asset in ("luozi-data.jsx", "luozi-panels.jsx", "luozi-app.jsx"):
        assert f'{asset}?v=20260831evidence2' in HTML
