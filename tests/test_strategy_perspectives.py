# guanlan_v2.strategy.perspectives · L4 九视角读数(observation, not decision-tree)
#
# market_cycle 读 vendored market_breadth_resid.parquet(datetime idx; lu_resid_pct60 float 0..1)。
# nine_view_scan 纯计算(传入 s/metrics/market/v9)。playbook 红线:视角是观察角度,带 conf 标签。
from guanlan_v2.strategy.perspectives import (
    market_cycle,
    nine_view_scan,
    resonance_count,
)

_VALID_STAGES = {"冰点", "分化", "逼空", "发酵", "回踩/启动"}


def test_market_cycle_reads_breadth_or_none():
    mc = market_cycle()
    # vendored 产物在场 → 返回阶段;缺则 None(诚实)
    if mc is not None:
        assert mc["stage"] in _VALID_STAGES
        assert 0.0 <= mc["lu_pct60"] <= 1.0


def test_nine_view_scan_covers_v1_to_v10():
    s = {"code": "SH600519", "name": "贵州茅台", "ind": "白酒", "v4_total": 5,
         "v4_layer": "大盘", "mainline": "mainline", "mainline_golden": False,
         "vol_regime": None, "chg": 1.2}
    views = nine_view_scan(s, {"pos_pct": 0.4, "ret60": 0.1, "ret20": 0.05, "rsi": 60},
                           {"stage": "发酵", "lu_pct60": 0.5, "amt_pct60": 0.5}, {"pct": 0.6, "n": 4})
    vs = [v["v"] for v in views]
    assert vs == ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10"]
    # 每项带 conf 标签(data/proxy/gap)
    assert all(v["conf"] in ("data", "proxy", "gap") for v in views)
    # V3 主线 = 真数据,V5/V7 = 缺口
    v3 = next(v for v in views if v["v"] == "V3")
    assert v3["conf"] == "data" and "主线" in v3["label"]
    assert next(v for v in views if v["v"] == "V5")["conf"] == "gap"


def test_resonance_count_bounds_and_signal():
    # 五维≥4 + 主线在场 + 量能非派发 + 位置低 → 四重共振
    s_bull = {"v4_total": 6, "mainline": "mainline", "vol_regime": None}
    assert resonance_count(s_bull, {"pos_pct": 0.2}) == 4
    # 派发 + 高位 + 弱分 + 无主线 → 0
    s_bear = {"v4_total": 0, "mainline": "cold", "vol_regime": "distr"}
    assert resonance_count(s_bear, {"pos_pct": 0.95}) == 0


def test_v9_needs_two_peers():
    s = {"code": "X", "ind": "Y", "v4_total": 3, "mainline": "neutral"}
    # 同业只有自己 → V9 gap
    views = nine_view_scan(s, {"pos_pct": 0.5, "ret60": 0.1}, None, {"pct": 0.5, "n": 1})
    assert next(v for v in views if v["v"] == "V9")["conf"] == "gap"
