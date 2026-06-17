# tests/test_price_action.py
# 落子价量几何特征纯函数(price_action.py)单测:逐特征公式 + A股 涨跌停 + PIT 降级 + 渲染。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from guanlan_v2.seats.price_action import (  # noqa: E402
    compute_pa_features, render_pa_block, PA_METHOD_DEFAULT, _board_limit, _bar_type)


def _df(rows):
    """rows: list of (open, high, low, close, vol);trade_date 自动按日填。"""
    ts = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame({
        "trade_date": ts,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "vol": [r[4] for r in rows],
    })


def test_empty_or_missing_cols():
    assert compute_pa_features(pd.DataFrame()) == {}
    assert compute_pa_features(pd.DataFrame({"open": [1]})) == {}


def test_single_bar_geometry_trend_bull():
    # o=10 h=11 l=9.8 c=10.9 → rng=1.2,body=0.9/1.2=0.75,close_pos=1.1/1.2≈0.917
    feat = compute_pa_features(_df([(10, 11, 9.8, 10.9, 1000)]))
    assert feat["bar_type"] == "趋势阳"
    assert feat["body"] == 0.75
    assert feat["close_pos"] == 0.917
    assert feat["upper_wick"] == 0.083
    assert feat["lower_wick"] == 0.167
    # 首根:无 prev → limit/gap None,breakout/vol_ratio/ema/atr None(窗口不足)
    assert feat["limit"] is None and feat["gap"] is None
    assert feat["range_atr"] is None and feat["ema20_rel"] is None


def test_doji_and_flat():
    assert compute_pa_features(_df([(10, 10.5, 9.5, 10.02, 1000)]))["bar_type"] == "十字"
    assert compute_pa_features(_df([(10, 10, 10, 10, 1000)]))["bar_type"] == "平"


def test_inside_and_outside_and_streak():
    # 第2根被第1根包住 = 内含bar;第3根再内含 → streak=2
    feat = compute_pa_features(_df([(10, 12, 8, 11, 1000), (10, 11, 9, 10, 1000), (9.5, 10.5, 9.2, 10, 1000)]))
    assert feat["bar_type"] == "内含bar"
    assert feat["inside_streak"] == 2
    # 外包:末根包住前根且收阳
    feat2 = compute_pa_features(_df([(10, 11, 9, 10, 1000), (9, 12, 8.5, 11.5, 1000)]))
    assert feat2["bar_type"] == "外包阳"


def test_breakout_up_and_down():
    base = [(10, 10.5, 9.5, 10, 1000)] * 5
    up = compute_pa_features(_df(base + [(10, 12, 9.8, 11.8, 1000)]))
    assert up["breakout"] == "突破前5高"
    down = compute_pa_features(_df(base + [(10, 10.2, 9.0, 9.1, 1000)]))
    assert down["breakout"] == "跌破前5低"


def test_vol_ratio():
    rows = [(10, 10.5, 9.5, 10, 1000)] * 5 + [(10, 10.5, 9.5, 10, 2000)]
    assert compute_pa_features(_df(rows))["vol_ratio"] == 2.0


def test_limit_by_board():
    # 主板 600:+10% → 涨停;+8% → 接近涨停
    main = _df([(10, 11, 10, 10, 1000), (10.9, 11.2, 10.9, 11.0, 1000)])
    assert compute_pa_features(main, code="SH600519")["limit"] == "涨停"
    near = _df([(10, 11, 10, 10, 1000), (10.7, 10.9, 10.7, 10.8, 1000)])
    assert compute_pa_features(near, code="SH600519")["limit"] == "接近涨停"
    # 科创 688:同样 +10% 不算涨停也不接近(板幅 20%,阈值 0.7×0.20=0.14)→ 正常(与主板对照)
    star = _df([(10, 11, 10, 10, 1000), (10.9, 11.2, 10.9, 11.0, 1000)])
    assert compute_pa_features(star, code="SH688111")["limit"] == "正常"
    # ST:+5% → 涨停
    st = _df([(10, 11, 10, 10, 1000), (10.4, 10.6, 10.4, 10.5, 1000)])
    assert compute_pa_features(st, code="SH600519", name="*ST 测试")["limit"] == "涨停"


def test_gap():
    up = _df([(10, 10.5, 9.5, 10, 1000), (10.2, 10.6, 10.1, 10.3, 1000)])
    assert compute_pa_features(up)["gap"] == "高开"
    down = _df([(10, 10.5, 9.5, 10, 1000), (9.8, 10.0, 9.6, 9.9, 1000)])
    assert compute_pa_features(down)["gap"] == "低开"


def test_follow_confirm_and_weaken():
    # 前根趋势阳(o9 c10 实体大),本根收更高且收阳 → 已确认(多)
    conf = _df([(9, 10.1, 8.9, 10, 1000), (10, 10.8, 9.9, 10.6, 1000)])
    assert compute_pa_features(conf)["follow"] == "已确认(多)"
    # 前根趋势阳,本根收阴且跌破前低 → 转弱
    weak = _df([(9, 10.1, 8.9, 10, 1000), (10, 10.1, 8.5, 8.6, 1000)])
    assert compute_pa_features(weak)["follow"] == "转弱"


def test_atr_and_ema_need_window():
    rows = [(10 + i * 0.1, 10.5 + i * 0.1, 9.5 + i * 0.1, 10 + i * 0.1, 1000) for i in range(25)]
    feat = compute_pa_features(_df(rows))
    assert feat["range_atr"] is not None   # ≥15 根
    assert feat["ema20_rel"] is not None   # ≥20 根
    short = compute_pa_features(_df(rows[:10]))
    assert short["range_atr"] is None and short["ema20_rel"] is None


def test_recent_three():
    rows = [(10, 11, 9, 10.8, 1000), (10, 10.2, 9.9, 10.0, 1000), (10, 11.5, 8.5, 8.7, 1000), (9, 9.5, 8.8, 9.4, 1000)]
    feat = compute_pa_features(_df(rows))
    assert len(feat["recent"]) == 3
    assert all(r is not None for r in feat["recent"])


def test_helpers():
    assert _board_limit("SH688111", "") == 0.20
    assert _board_limit("SZ300001", "") == 0.20
    assert _board_limit("BJ830001", "") == 0.30
    assert _board_limit("SH600519", "*ST x") == 0.05
    assert _board_limit("SH600519", "") == 0.10
    assert _bar_type(10, 11, 9.8, 10.9, None, None) == "趋势阳"


def test_render_block():
    feat = compute_pa_features(_df([(10, 11, 9.8, 10.9, 1000)]))
    s = render_pa_block(feat)
    assert "趋势阳" in s and "—" in s   # 含型态,缺窗项渲染 —
    assert render_pa_block({}) == ""
    assert "(每根=根30分钟bar)" in render_pa_block(feat, unit="根30分钟bar")
    assert isinstance(PA_METHOD_DEFAULT, str) and "T+1" in PA_METHOD_DEFAULT


def test_nan_cell_does_not_produce_nan_output():
    import math
    # NaN 单元格(新股首日/停牌/稀疏源)不得泄漏 nan;依赖该值的项诚实 None
    rows = [(10, 11, 9.5, 10.0, 1000)] * 20 + [(10.0, 11.0, 9.8, float("nan"), 1000)]
    feat = compute_pa_features(_df(rows), code="SH600519")
    for v in feat.values():
        if isinstance(v, float):
            assert not math.isnan(v)
    assert feat["ema20_rel"] is None   # close 含 nan → EMA 诚实 None
    assert feat["limit"] is None       # 今收 nan → 不冒充「正常」
