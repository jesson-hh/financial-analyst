"""因子语义契约层(0612演习修复#1)单元测试。

铁律:rev_20 正值=过去20日下跌(字段=负的涨跌幅);turnover_20 是量比口径(倍数)非换手率。
回归样例取自 0612 演习中微公司真实因子值(var/seats_decisions.jsonl)。
"""
from pathlib import Path

from guanlan_v2.factorlib.semantics import FACTOR_SEMANTICS, render_factor, render_factors

# —— 0612 演习真实值(中微公司 SH688012 asof 2026-06-11)——
DRILL_FAC = {"rev_20": 0.2170881, "mom_60": -0.0313489, "rsi_14": 22.79383,
             "ma_diff_20": -0.1907521, "turnover_20": 8.8468891}
DECIDE_FIELDS = ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20")


def test_rev20_positive_means_fell():
    # rev = -pct_change → 0.217 必须渲染成「下跌21.7%」,绝不允许出现「上涨」
    s = render_factor("rev_20", 0.2170881)
    assert "下跌21.7%" in s and "超跌" in s
    assert "上涨" not in s


def test_rev20_negative_means_rose():
    s = render_factor("rev_20", -0.150)
    assert "上涨15.0%" in s and "下跌" not in s


def test_mom60_sign():
    assert "下跌3.1%" in render_factor("mom_60", -0.0313489)
    assert "上涨9.6%" in render_factor("mom_60", 0.096)


def test_rsi_zones():
    assert "超卖" in render_factor("rsi_14", 22.8)
    assert "超买" in render_factor("rsi_14", 78.7)
    s = render_factor("rsi_14", 50.0)
    assert "超卖" not in s and "超买" not in s


def test_ma_diff_direction():
    assert "低于20日均线19.1%" in render_factor("ma_diff_20", -0.1907521)
    assert "高于20日均线10.9%" in render_factor("ma_diff_20", 0.109)


def test_turnover20_is_volume_ratio_not_turnover_rate():
    # 字段名叫 turnover 但口径是量比:8.85 = 当日量为20日均量的8.85倍
    s = render_factor("turnover_20", 8.8468891)
    assert "20日量比" in s and "8.85倍" in s and "放量" in s
    assert "换手" not in s          # 绝不允许再被当成换手率
    assert "缩量" in render_factor("turnover_20", 0.79)


def test_vol_ratio_distinct_from_turnover20():
    # 第二个量比(腾讯实时,10日窗)必须可区分
    s = render_factor("vol_ratio", 1.58)
    assert "实时量比" in s and "10日窗" in s


def test_turnover_rate_labeled():
    s = render_factor("turnover_rate", 2.94)
    assert "换手率" in s and "流通股本" in s


def test_unknown_field_fallback_and_none():
    assert render_factor("mystery_x", 1.5) == "mystery_x=1.5"
    assert render_factor("rev_20", None) == "反转20=—"
    assert render_factor("rev_20", float("nan")) == "反转20=—"


def test_render_factors_drill_regression():
    # 演习整行回归:五字段全渲染、方向全对、无误导词
    line = render_factors(DRILL_FAC, DECIDE_FIELDS)
    for cn in ("反转20", "动量60", "RSI14", "均线乖离20", "20日量比"):
        assert cn in line
    assert "下跌21.7%" in line and "超卖" in line and "8.85倍" in line
    assert "上涨21.7%" not in line


def test_render_factors_skips_missing_gracefully():
    line = render_factors({"rev_20": 0.1}, ("rev_20", "rsi_14"))
    assert "反转20" in line and "RSI14=—" in line


def test_threshold_boundaries_inclusive():
    assert "超跌" in render_factor("rev_20", 0.10)
    assert "强势上行" in render_factor("rev_20", -0.10)
    assert "明显放量" in render_factor("turnover_20", 1.5)
    assert "缩量" in render_factor("turnover_20", 0.8)
    assert "mystery_x=1234567" == render_factor("mystery_x", 1234567.0)
    assert "mystery_x=0.000001" == render_factor("mystery_x", 0.000001)


def test_render_factors_unit_minute():
    # Task 2:分钟级渲染须把回看窗口「日」换成传入单位,缺省=日向后兼容
    from guanlan_v2.factorlib.semantics import render_factors
    fac = {"rev_20": 0.105, "rsi_14": 37.1}
    day = render_factors(fac, ("rev_20",))               # 缺省=日,向后兼容
    assert "20日" in day
    mn = render_factors(fac, ("rev_20",), unit="根30分钟bar")
    assert "30分钟bar" in mn
    assert "20日" not in mn


def test_engine_prompt_pins():
    # 契约钉:引擎侧两处字面量修补不被回退(Task 3/4 落地后通过)
    root = Path(__file__).resolve().parents[1] / "engine" / "financial_analyst"
    brief = (root / "buddy" / "tools.py").read_text(encoding="utf-8")
    assert "(10日窗,>1放量·<1缩量)" in brief
    ta = (root / "agent" / "tier2" / "technical_analyst.py").read_text(encoding="utf-8")
    assert "flip its sign" in ta
    assert "mean-reversion DOWN" not in ta
