# -*- coding: utf-8 -*-
"""macro 双源客户端:归一 schema/单源失败降级/Kalshi 无价跳过/themes.yaml 解析。全 mock 不打真 API。"""
import json

import pytest

from guanlan_v2.macro import sources as ms


# ── 夹具:真实报文缩样(合成值) ──────────────────────────────────────────────

PM_EVENT = {
    "title": "Fed Decision in July?",
    "slug": "fed-decision-in-july",
    "volume24hr": 1234567.8,
    "markets": [
        {"id": "517311", "question": "Will the Fed cut rates in July?",
         "outcomes": json.dumps(["Yes", "No"]),
         "outcomePrices": json.dumps(["0.63", "0.37"]),
         "volume24hr": 234567.8, "endDate": "2026-07-29T17:00:00Z"},
        {"id": "517312", "question": "How many cuts in 2026?",
         "outcomes": json.dumps(["0", "1", "2+"]),
         "outcomePrices": json.dumps(["0.2", "0.5", "0.3"]),
         "volume24hr": 1000.0, "endDate": "2026-12-31T17:00:00Z"},
        {"id": "517313", "question": "broken outcomes",
         "outcomes": "not-json{", "outcomePrices": "[]",
         "volume24hr": 5.0, "endDate": ""},
    ],
}

KALSHI_BODY = {
    "markets": [
        {"ticker": "KXFED-27APR-T4.25",
         "title": "Fed funds above 4.25% after Apr 2027 meeting?",
         "last_price_dollars": "0.0800", "liquidity_dollars": "150.0000",
         "close_time": "2027-04-28T17:55:00Z"},
        {"ticker": "KXFED-27APR-T9.99",
         "title": "no trades yet, bid/ask only",
         "last_price_dollars": "0.0000",
         "yes_bid_dollars": "0.1000", "yes_ask_dollars": "0.3000",
         "liquidity_dollars": "0.0000", "close_time": "2027-04-28T17:55:00Z"},
        {"ticker": "KXFED-27APR-DEAD", "title": "priceless market",
         "last_price_dollars": "0.0000", "close_time": "2027-04-28T17:55:00Z"},
    ]
}


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class FakeHttp:
    """get 按 url+tag 派发夹具;fail_tags 中的 tag 抛异常模拟单源故障。"""

    def __init__(self, fail_tags=()):
        self.fail_tags = set(fail_tags)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        tag = params.get("tag_slug") or params.get("series_ticker") or ""
        if tag in self.fail_tags:
            raise ConnectionError(f"boom {tag}")
        if "gamma-api" in url:
            return _Resp([PM_EVENT])
        return _Resp(KALSHI_BODY)


# ── Polymarket ────────────────────────────────────────────────────────────────

def test_polymarket_normalizes_binary_and_multi():
    rows, notes = ms.fetch_polymarket(["fed-rates"], http=FakeHttp())
    assert notes == []
    assert len(rows) == 2  # 脏 outcomes 行被跳过
    binary = rows[0]
    assert binary == {
        "source": "polymarket", "id": "pm_517311",
        "question": "Will the Fed cut rates in July?", "prob": 0.63,
        "volume": 234567.8, "close_time": "2026-07-29",
        "url": "https://polymarket.com/event/fed-decision-in-july"}
    multi = rows[1]
    assert multi["prob"] == 0.2 and multi["question"].endswith("→ 0")


def test_polymarket_single_tag_failure_degrades_with_note():
    rows, notes = ms.fetch_polymarket(["dead-tag", "fed-rates"],
                                      http=FakeHttp(fail_tags={"dead-tag"}))
    assert len(rows) == 2
    assert len(notes) == 1 and "dead-tag" in notes[0]


# ── Kalshi ────────────────────────────────────────────────────────────────────

def test_kalshi_last_price_and_mid_fallback_and_skip():
    rows, notes = ms.fetch_kalshi(["KXFED"], http=FakeHttp())
    assert [r["id"] for r in rows] == ["k_KXFED-27APR-T4.25", "k_KXFED-27APR-T9.99"]
    assert rows[0]["prob"] == 0.08 and rows[0]["source"] == "kalshi"
    assert rows[1]["prob"] == 0.2  # (0.10+0.30)/2
    assert any("跳过 1" in n for n in notes)  # 无价市场诚实计数


def test_kalshi_series_failure_note():
    rows, notes = ms.fetch_kalshi(["KXDEAD"], http=FakeHttp(fail_tags={"KXDEAD"}))
    assert rows == [] and len(notes) == 1 and "KXDEAD" in notes[0]


# ── themes.yaml ───────────────────────────────────────────────────────────────

def test_load_themes_has_five_themes_and_astock_constants():
    cfg = ms.load_themes()
    ids = [t["id"] for t in cfg["themes"]]
    assert ids == ["fed", "inflation_recession", "geopolitics", "china", "crypto_risk"]
    for t in cfg["themes"]:
        assert t["label"] and isinstance(t["polymarket_tags"], list)
        for a in t.get("anchors") or []:
            assert a["direction"] in (1, -1) and a["match"]
    ast = cfg["astock"]
    assert set(ast) >= {"base", "k_zt", "k_streak", "k_break"}
    assert cfg["display_top_n"] >= 1
