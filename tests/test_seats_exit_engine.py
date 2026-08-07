# -*- coding: utf-8 -*-
"""出场引擎(seats/exit_engine.py)—— 合成路径上的确定性触发验证。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.seats.exit_engine import compute_exit_plan, render_lines


def _bars(closes, lows=None, highs=None, start="2026-01-01") -> pd.DataFrame:
    closes = np.asarray(closes, float)
    n = len(closes)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=n),
        "open": opens,
        "high": highs if highs is not None else np.maximum(opens, closes) * 1.01,
        "low": lows if lows is not None else np.minimum(opens, closes) * 0.99,
        "close": closes,
    })


def test_uptrend_then_chandelier_break():
    """主升后深回撤:破吊灯线 → 清仓。"""
    up = np.linspace(10, 16, 50)
    down = np.linspace(16, 12.8, 10)          # 回撤20%,远超3×ATR
    df = _bars(np.concatenate([up, down]))
    plan = compute_exit_plan(df, entry_price=10.5, entry_date="2026-01-05")
    assert plan["triggers"]["chandelier_hit"] or plan["triggers"]["giveback_hit"]
    assert plan["action"] in ("清仓", "减仓")
    assert plan["close"] < plan["chandelier"] or plan["giveback"] >= 1 / 3


def test_stop_hit_full_exit():
    """跌破初始止损 → 清仓,且优先级最高。"""
    flat = np.full(40, 10.0) + np.random.default_rng(1).normal(0, 0.02, 40)
    crash = np.linspace(10, 8.8, 8)           # -12%,破7%线
    df = _bars(np.concatenate([flat, crash]))
    plan = compute_exit_plan(df, entry_price=10.0, entry_date="2026-01-20")
    assert plan["triggers"]["stop_hit"]
    assert plan["action"] == "清仓"
    assert plan["close"] < plan["initial_stop"] < plan["entry_price"]


def test_takeprofit_zone_partial():
    """浮盈≥2R 且趋势未破 → 减仓,建议防线提保本。"""
    up = np.linspace(10, 13.5, 60)            # 平稳爬升,ATR小 → R小,浮盈多R
    df = _bars(up)
    plan = compute_exit_plan(df, entry_price=10.2, entry_date="2026-01-08")
    assert plan["r_multiple"] >= 2.0
    assert plan["action"] == "减仓"
    assert plan["suggested_stop"] >= plan["entry_price"]  # free roll:至少保本


def test_time_barrier():
    """横盘 N bar 未达预期 → 时间障碍清仓。"""
    rng = np.random.default_rng(3)
    flat = 10.0 + rng.normal(0, 0.03, 45)
    df = _bars(flat)
    plan = compute_exit_plan(df, entry_price=10.0, entry_date="2026-01-15",
                             params={"time_bars": 10})
    assert plan["held_bars"] >= 10
    assert plan["triggers"]["time_hit"]
    assert plan["action"] == "清仓"


def test_healthy_hold():
    """刚入场、趋势完好 → 继续持有。"""
    up = np.linspace(10, 10.6, 40)
    df = _bars(up)
    plan = compute_exit_plan(df, entry_price=10.45, entry_date="2026-02-20")
    assert plan["action"] == "继续持有"
    assert not any(plan["triggers"].values())


def test_entry_date_inferred_and_render():
    """不给入场日:按成本价回溯推定,假设如实标注;渲染行包含关键数字。"""
    up = np.linspace(10, 12, 50)
    df = _bars(up)
    plan = compute_exit_plan(df, entry_price=11.0)
    assert plan["assumptions"]
    txt = render_lines(plan)
    assert "出场引擎" in txt and "建议:" in txt
    assert f"{plan['initial_stop']:.2f}" in txt


def test_insufficient_data_raises():
    with pytest.raises(ValueError):
        compute_exit_plan(_bars(np.linspace(10, 11, 8)), entry_price=10.0)
