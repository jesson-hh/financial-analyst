# guanlan_v2.strategy.decision · L5 评级 / 护盾 / ≤5 收敛(纯计算,无数据依赖)
#
# 规则锚定 vendored rating_system.md:总分评级表 + v4.1 金信号护盾 + v4.2 仕佳 + ≤5 收敛。
from guanlan_v2.strategy.decision import apply_shields, converge, rate_v4


def test_rate_table_maps_total_to_band():
    assert rate_v4(6)["band"]["tier"] == "重仓"       # ≥6 ★★★★★
    assert rate_v4(5)["band"]["tier"] == "标准"       # 4-5 ★★★★
    assert rate_v4(3)["band"]["tier"] == "轻仓"       # 2-3 ★★★☆
    assert rate_v4(1)["band"]["tier"] == "观望"       # 0-1 ★★★
    assert rate_v4(-1)["band"]["tier"] == "减仓"      # -2~-1
    assert rate_v4(-5)["band"]["tier"] == "清仓"      # ≤-3
    assert rate_v4(None)["band"]["tier"] == "观望"    # 缺分 → 中性


def test_rate_stars_monotonic():
    assert rate_v4(6)["stars"] >= rate_v4(4)["stars"] >= rate_v4(2)["stars"] >= rate_v4(0)["stars"]


def test_golden_shield_floors_to_4_stars():
    # v4_total=1(本应 ★★★)但金信号 → 强制下限 ★★★★
    s = {"code": "SH600000", "v4_total": 1, "mainline": "mainline", "mainline_golden": True, "ind": "银行"}
    dec = apply_shields(s, {"ret60": 0.1, "rsi": 55, "pos_pct": 0.5})
    assert dec["stars"] >= 4.0
    assert dec["base"]["stars"] < 4.0                       # 原始低于下限
    assert any(sh["id"] == "v4.1" and sh["level"] == "floor" for sh in dec["shields"])


def test_golden_shield_iron_bottom_exception():
    # 金信号 + RSI>90(铁底)→ 例外:放宽到 ★★★,不再强制 ★★★★
    s = {"code": "SH600001", "v4_total": 1, "mainline": "mainline", "mainline_golden": True, "ind": "半导体"}
    dec = apply_shields(s, {"ret60": 0.5, "rsi": 93, "pos_pct": 0.97})
    assert any(sh["id"] == "v4.1" and sh["level"] == "exception" for sh in dec["shields"])
    assert dec["stars"] < 4.0       # 例外路径不强制 ★★★★


def test_golden_shield_iron_bottom_floors_at_3_stars():
    # rating_system.md v4.1 例外:即便铁底,评级下限仍是 ★★★(不可跌到 ★★☆/★★)
    s = {"code": "SH600002", "v4_total": -5, "mainline": "mainline", "mainline_golden": True, "ind": "半导体"}
    dec = apply_shields(s, {"ret60": 0.5, "rsi": 93, "pos_pct": 0.97})  # base=★★(2.0)
    assert dec["base"]["stars"] == 2.0
    assert dec["stars"] == 3.0      # 被 ★★★ 地板抬起,与 KB「可下调至 ★★★」一致
    assert dec["band"]["tier"] == "观望"


def test_shijia_risk_warns_not_downgrades():
    # 涨幅≥30% + 板块退潮 → 仕佳风险警示,但 b/c 缺 → 不降级(评级=base)
    s = {"code": "SZ000001", "v4_total": 5, "mainline": "decay", "mainline_golden": False, "ind": "电气设备"}
    dec = apply_shields(s, {"ret60": 0.45, "rsi": 60, "pos_pct": 0.9})
    sj = [sh for sh in dec["shields"] if sh["id"] == "v4.2"]
    assert sj and sj[0]["level"] == "warn"
    assert dec["stars"] == dec["base"]["stars"]             # 不硬降级(playbook 红线)


def test_converge_caps_5_and_dedups_industry():
    # 8 只 ★★★★+,其中 3 只同业 → 收敛后 ≤5 且每业仅 1
    rows = []
    for i in range(8):
        ind = "半导体" if i < 3 else f"业{i}"
        rows.append({"s": {"code": f"C{i}", "name": f"股{i}", "ind": ind, "v4_total": 6 - (i * 0.1)}})
    out = converge(rows, {}, max_n=5)
    assert len(out["final"]) <= 5
    inds = [f["ind"] for f in out["final"]]
    assert len(inds) == len(set(inds))                      # 行业去重
    assert out["final"][0]["band"]["tier"] == "重仓"        # 最优带仓位档


def test_converge_excludes_below_4_stars():
    rows = [{"s": {"code": "L1", "name": "弱", "ind": "X", "v4_total": 1}}]  # ★★★ < ★★★★
    out = converge(rows, {}, max_n=5)
    assert out["final"] == []
    assert out["n_actionable"] == 0
