# -*- coding: utf-8 -*-
"""分钟因子库(factorlib/minute.py)—— 合成 bar 上的确定性验证。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.factorlib.minute import (
    MINUTE_FACTORS,
    compute_daily_primitives,
    compute_factors,
)

N_BARS = 48  # 5min × 48 = 一个交易日


def _mk_day(day: str, closes, volumes=None, amounts=None) -> pd.DataFrame:
    """一天 48 根 5min bar;时间轴 9:30-11:30 + 13:00-15:00。"""
    closes = np.asarray(closes, float)
    assert len(closes) == N_BARS
    am = pd.date_range(f"{day} 09:35", periods=24, freq="5min")
    pm = pd.date_range(f"{day} 13:05", periods=24, freq="5min")
    ts = am.append(pm)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    v = np.asarray(volumes, float) if volumes is not None else np.full(N_BARS, 1000.0)
    a = np.asarray(amounts, float) if amounts is not None else closes * v
    return pd.DataFrame({
        "trade_date": ts, "open": opens, "close": closes,
        "high": np.maximum(opens, closes) * 1.001,
        "low": np.minimum(opens, closes) * 0.999,
        "volume": v, "amount": a,
    })


def _flat_days(n_days=25, price=10.0) -> pd.DataFrame:
    days = pd.bdate_range("2026-06-01", periods=n_days)
    rng = np.random.default_rng(7)
    frames = []
    p = price
    for d in days:
        closes = p * (1 + rng.normal(0, 0.001, N_BARS)).cumprod()
        vols = rng.uniform(500.0, 2000.0, N_BARS)  # 量须有变异,否则量价相关无定义
        frames.append(_mk_day(d.strftime("%Y-%m-%d"), closes, volumes=vols))
        p = closes[-1]
    return pd.concat(frames, ignore_index=True)


class TestPrimitives:
    def test_head_tail_share_exact(self):
        v = np.full(N_BARS, 1.0)
        a = np.full(N_BARS, 1.0)
        a[-6:] = 9.0  # 尾盘 6 根巨额
        bars = _mk_day("2026-06-01", np.full(N_BARS, 10.0), volumes=v, amounts=a)
        p = compute_daily_primitives(bars)
        row = p.iloc[0]
        assert row["tail_share"] == pytest.approx(54.0 / 96.0)
        assert row["head_share"] == pytest.approx(6.0 / 96.0)

    def test_open30_trunc_identity(self):
        closes = np.linspace(10.0, 11.0, N_BARS)
        bars = _mk_day("2026-06-01", closes)
        p = compute_daily_primitives(bars).iloc[0]
        assert p["open30_ret"] == pytest.approx(closes[5] / closes[0] - 1)
        assert p["trunc_ret"] == pytest.approx(closes[-1] / closes[5] - 1)
        # 隔夜×日内 = 全日(次日验证)
        bars2 = pd.concat([bars, _mk_day("2026-06-02", closes * 1.05)], ignore_index=True)
        p2 = compute_daily_primitives(bars2).iloc[1]
        full = (1 + p2["overnight_ret"]) * (1 + p2["intraday_ret"]) - 1
        assert full == pytest.approx(p2["ret_d"])

    def test_vol_time_center_late(self):
        v = np.full(N_BARS, 1e-9)
        v[-1] = 1e6
        bars = _mk_day("2026-06-01", np.full(N_BARS, 10.0) + np.arange(N_BARS) * 0.001,
                       volumes=v)
        p = compute_daily_primitives(bars).iloc[0]
        assert p["vol_tc"] == pytest.approx((N_BARS - 0.5) / N_BARS, abs=1e-6)

    def test_high_pos_and_flat_day_nan(self):
        closes = np.linspace(10.0, 12.0, N_BARS)  # 单边上行 → 最高价在末端
        p = compute_daily_primitives(_mk_day("2026-06-01", closes)).iloc[0]
        assert p["high_pos"] == pytest.approx(1.0)
        flat = _mk_day("2026-06-02", np.full(N_BARS, 10.0))
        flat["high"] = flat["low"] = flat["open"] = flat["close"] = 10.0  # 一字
        p2 = compute_daily_primitives(flat).iloc[0]
        assert np.isnan(p2["high_pos"])

    def test_short_day_dropped_and_amount_fallback(self):
        good = _mk_day("2026-06-01", np.linspace(10, 10.5, N_BARS))
        stub = good.head(2).copy()
        stub["trade_date"] = stub["trade_date"] + pd.Timedelta(days=1)
        bars = pd.concat([good, stub], ignore_index=True).drop(columns=["amount"])
        p = compute_daily_primitives(bars)
        assert len(p) == 1  # 残日剔除
        assert p.iloc[0]["amount"] == pytest.approx(
            float((good["close"] * good["volume"]).sum()))


class TestFactors:
    def test_all_factors_present_and_computable(self):
        panel = compute_factors(_flat_days())
        assert list(panel.columns) == [f.name for f in MINUTE_FACTORS]
        assert len(panel.columns) >= 20
        for c in panel.columns:
            assert "error" not in panel[c].attrs, f"{c}: {panel[c].attrs.get('error')}"
        # 20 日窗因子在第 25 日应已有值
        last = panel.iloc[-1]
        for name in ("gl_min_cpv_avg", "gl_min_tail_share", "gl_min_apb20",
                     "gl_min_smart_q", "gl_min_trunc_rev"):
            assert np.isfinite(last[name]), name

    def test_smart_q_direction(self):
        """聪明段(大|r|小量)集中在高价 → Q>1;集中在低价 → Q<1。"""
        days = pd.bdate_range("2026-06-01", periods=12)
        up_frames, dn_frames = [], []
        for d in days:
            base = np.full(N_BARS, 10.0)
            v = np.full(N_BARS, 1000.0)
            hi = base.copy()
            hi[-4:] = [10.5, 10.9, 11.4, 12.0]   # 高价 bar 且大波动
            v_hi = v.copy(); v_hi[-4:] = 10.0     # 小量 → S 极大 → 必入聪明段
            up_frames.append(_mk_day(d.strftime("%Y-%m-%d"), hi, volumes=v_hi))
            lo = base.copy()
            lo[-4:] = [9.5, 9.1, 8.6, 8.0]
            v_lo = v.copy(); v_lo[-4:] = 10.0
            dn_frames.append(_mk_day(d.strftime("%Y-%m-%d"), lo, volumes=v_lo))
        q_up = compute_factors(pd.concat(up_frames, ignore_index=True),
                               ["gl_min_smart_q"]).iloc[-1, 0]
        q_dn = compute_factors(pd.concat(dn_frames, ignore_index=True),
                               ["gl_min_smart_q"]).iloc[-1, 0]
        assert q_up > 1.0 > q_dn

    def test_ideal_amp_direction(self):
        """高收盘日配大振幅 → 理想振幅为正。"""
        days = pd.bdate_range("2026-06-01", periods=20)
        frames = []
        for i, d in enumerate(days):
            lvl = 10.0 + i * 0.1
            swing = 0.03 if i >= 15 else 0.003   # 价越高振幅越大
            closes = lvl * (1 + np.sin(np.linspace(0, 6, N_BARS)) * swing)
            frames.append(_mk_day(d.strftime("%Y-%m-%d"), closes))
        v = compute_factors(pd.concat(frames, ignore_index=True),
                            ["gl_min_ideal_amp"]).iloc[-1, 0]
        assert v > 0

    def test_factor_subset_selection(self):
        panel = compute_factors(_flat_days(15), ["gl_min_rv", "gl_min_rskew"])
        assert list(panel.columns) == ["gl_min_rv", "gl_min_rskew"]
