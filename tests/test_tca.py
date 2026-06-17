# -*- coding: utf-8 -*-
"""落子事后 TCA(执行质量)模块 TDD —— guanlan_v2.seats.tca。

验证:当日 VWAP、滑点成本 bps 的方向符号约定(买高/卖低=正成本)、逐笔 TCA 缺基准诚实 None、
按笔/按日/按策略的成交额加权汇总与 coverage。纯函数,只算不取数(基准由端点喂入),不碰引擎/IO。"""
from __future__ import annotations

import math

from guanlan_v2.seats import tca


# ── 当日 VWAP ────────────────────────────────────────────────────────────────
def test_day_vwap_typical_price_weighted():
    bars = [
        {"high": 10.2, "low": 9.8, "close": 10.0, "vol": 100},   # tp=10.0
        {"high": 11.0, "low": 10.0, "close": 10.5, "vol": 300},  # tp=10.5
    ]
    # Σ tp·vol / Σ vol = (10.0*100 + 10.5*300) / 400 = (1000+3150)/400 = 10.375
    v = tca.day_vwap(bars)
    assert v is not None and abs(v - 10.375) < 1e-9


def test_day_vwap_empty_or_zero_vol_none():
    assert tca.day_vwap([]) is None
    assert tca.day_vwap([{"high": 1, "low": 1, "close": 1, "vol": 0}]) is None
    assert tca.day_vwap(None) is None


# ── 滑点成本 bps 符号约定 ────────────────────────────────────────────────────
def test_cost_bps_buy_above_ref_is_positive_cost():
    # 买入价 10.1 高于基准 10.0 → 多付 → 正成本 ≈ +100 bps
    c = tca.cost_bps(10.1, 10.0, "buy")
    assert c is not None and abs(c - 100.0) < 1e-6


def test_cost_bps_sell_below_ref_is_positive_cost():
    # 卖出价 9.9 低于基准 10.0 → 少收 → 正成本 ≈ +100 bps
    c = tca.cost_bps(9.9, 10.0, "sell")
    assert c is not None and abs(c - 100.0) < 1e-6


def test_cost_bps_favorable_is_negative():
    assert tca.cost_bps(9.9, 10.0, "buy") < 0     # 买得便宜 = 负成本(好)
    assert tca.cost_bps(10.1, 10.0, "sell") < 0   # 卖得贵 = 负成本(好)


def test_cost_bps_guards_none_and_nonpositive():
    assert tca.cost_bps(10.0, None, "buy") is None
    assert tca.cost_bps(10.0, 0.0, "buy") is None
    assert tca.cost_bps(None, 10.0, "buy") is None
    assert tca.cost_bps(-1.0, 10.0, "buy") is None


# ── 逐笔 TCA ─────────────────────────────────────────────────────────────────
def test_compute_trade_tca_all_refs():
    trade = {"code": "SH600000", "name": "浦发", "date": "2026-06-10", "side": "buy",
             "price": 10.10, "qty": 1000, "source": "decide",
             "decision_id": "decide_1", "strategy_id": "s1", "strategy_name": "动量·默认"}
    refs = {"vwap": 10.00, "arrival": 10.05, "open": 9.95, "close": 10.20}
    r = tca.compute_trade_tca(trade, refs)
    assert r["notional"] == 10.10 * 1000
    assert abs(r["cost_vwap_bps"] - 100.0) < 1e-6        # 买 10.10 vs VWAP 10.00
    assert r["cost_arrival_bps"] is not None
    assert r["cost_open_bps"] is not None and r["cost_close_bps"] is not None
    assert r["side"] == "buy" and r["strategy_name"] == "动量·默认"


def test_compute_trade_tca_missing_refs_honest_none():
    trade = {"code": "SZ000001", "date": "2026-06-10", "side": "sell", "price": 12.0, "qty": 500,
             "source": "manual"}
    refs = {"vwap": None, "arrival": None, "open": 12.1, "close": 11.9}
    r = tca.compute_trade_tca(trade, refs)
    assert r["cost_vwap_bps"] is None          # 无 VWAP 基准 → 诚实 None
    assert r["cost_arrival_bps"] is None        # manual 无决策链接 → 无到达价
    assert r["cost_open_bps"] is not None
    assert r["notional"] == 12.0 * 500


# ── 汇总(成交额加权 + 按日/按策略 + coverage)──────────────────────────────
def _rows():
    return [
        tca.compute_trade_tca({"code": "A", "date": "2026-06-10", "side": "buy", "price": 10.1, "qty": 1000,
                               "source": "decide", "strategy_name": "动量"}, {"vwap": 10.0, "open": 10.0, "close": 10.2}),
        tca.compute_trade_tca({"code": "B", "date": "2026-06-10", "side": "buy", "price": 20.0, "qty": 1000,
                               "source": "decide", "strategy_name": "动量"}, {"vwap": 20.0, "open": 20.0, "close": 20.0}),
        tca.compute_trade_tca({"code": "A", "date": "2026-06-11", "side": "sell", "price": 9.9, "qty": 1000,
                               "source": "manual", "strategy_name": "反转"}, {"vwap": 10.0, "open": 10.0, "close": 9.8}),
    ]


def test_summarize_notional_weighted_headline():
    s = tca.summarize_tca(_rows())
    # vs VWAP:笔1 +100bps(notional≈10100)、笔2 0bps(20000)、笔3 +100bps(9900)
    # 加权 = (100*10100 + 0*20000 + 100*9900)/(10100+20000+9900) = (1010000+0+990000)/40000 = 2000000/40000 = 50
    assert abs(s["cost_vwap_bps"] - 50.0) < 1e-6
    assert s["n_trades"] == 3
    assert s["total_notional"] == 10.1 * 1000 + 20.0 * 1000 + 9.9 * 1000


def test_summarize_by_day_and_by_strategy():
    s = tca.summarize_tca(_rows())
    days = {d["date"]: d for d in s["by_day"]}
    assert "2026-06-10" in days and "2026-06-11" in days
    assert days["2026-06-11"]["n_trades"] == 1
    strat = {x["strategy"]: x for x in s["by_strategy"]}
    assert "动量" in strat and strat["动量"]["n_trades"] == 2
    assert "反转" in strat


def test_summarize_coverage_and_empty():
    s = tca.summarize_tca(_rows())
    assert s["coverage"]["vwap"] == 3            # 3 笔都有 VWAP
    assert s["coverage"]["arrival"] == 0          # 无一笔有到达价
    empty = tca.summarize_tca([])
    assert empty["n_trades"] == 0
    assert empty["cost_vwap_bps"] is None         # 无样本 → 诚实 None


def test_summarize_all_refs_missing_metric_none_not_zero():
    rows = [tca.compute_trade_tca({"code": "X", "date": "2026-06-10", "side": "buy", "price": 5.0, "qty": 100,
                                   "source": "manual"}, {"vwap": None, "open": None, "close": None})]
    s = tca.summarize_tca(rows)
    assert s["n_trades"] == 1
    assert s["cost_vwap_bps"] is None             # 全缺 → None 而非 0
    assert s["total_notional"] == 500.0
