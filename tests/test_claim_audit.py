"""断言质检(0612演习修复#2)单元测试。

坏样例=演习真实误读文本(20日+20% 实为跌21.7%);好样例=修复#1后真实冒烟 rationale。
质检是 advisory:返回 flags 列表,空=干净;绝不抛异常。
"""
from guanlan_v2.factorlib.claim_audit import audit_claims, unsourced_percents

# 0612 演习中微公司真实因子值
FAC = {"rev_20": 0.2170881, "mom_60": -0.0313489, "rsi_14": 22.79383,
       "ma_diff_20": -0.1907521, "turnover_20": 8.8468891}
# 喂入源(修复#1 后 fac_line 的真实渲染,含全部合法数字)
SRC = ("反转20=0.217(过去20日下跌21.7%,超跌状态); 动量60=-0.031(过去60日累计下跌3.1%); "
       "RSI14=22.8(超卖区,<30); 均线乖离20=-0.191(收盘低于20日均线19.1%); "
       "20日量比=8.85倍(当日量为20日均量的8.85倍,明显放量)")

BAD = "动量最强(20日+20%)、业绩爆发式增长、量比2.2放量上攻"
GOOD = ("超跌反转因子20日跌幅21.7%,RSI14=22.8处于超卖区,均线乖离-19.1%严重偏离,"
        "且20日量比8.85倍显著放量,可能预示反弹。")


def test_drill_bad_text_flagged():
    flags = audit_claims(BAD, FAC, SRC)
    assert any("方向矛盾" in f for f in flags)      # 20日+X% vs 实际下跌
    assert any("无出处" in f for f in flags)        # 20% 不在喂入证据里


def test_drill_good_text_clean():
    assert audit_claims(GOOD, FAC, SRC) == []


def test_direction_rev20_rose_but_text_says_fell():
    flags = audit_claims("近20日下跌明显,弱势", {"rev_20": -0.15}, "")
    assert any("方向矛盾" in f for f in flags)


def test_direction_rsi_contradiction():
    assert any("超买" in f or "方向矛盾" in f
               for f in audit_claims("RSI显示超买,回调风险大", {"rsi_14": 22.8}, ""))
    assert any("超卖" in f or "方向矛盾" in f
               for f in audit_claims("RSI已超卖,可博反弹", {"rsi_14": 78.7}, ""))


def test_direction_ma_diff():
    flags = audit_claims("已站上20日均线,趋势转强", {"ma_diff_20": -0.19}, "")
    assert any("方向矛盾" in f for f in flags)


def test_direction_turnover20():
    flags = audit_claims("20日量比显示放量", {"turnover_20": 0.6}, "")
    assert any("方向矛盾" in f for f in flags)


def test_provenance_creed_numbers_are_legit():
    # 止损/止盈数字来自 creed(在 source 里)→ 不许误报
    assert audit_claims("触发后止损5%、止盈10%", {}, "信条:止损5%止盈10%持有10日") == []


def test_provenance_rounding_tolerated():
    # 21.7% 被复述成约22% 属合理改写,不报;凭空的 35% 要报
    assert audit_claims("近20日跌约22%", FAC, SRC) == []
    flags = audit_claims("该股近期上涨35%", FAC, SRC)
    assert any("35" in f and "无出处" in f for f in flags)


def test_dead_zone_no_false_positive():
    # |rev|<2% 的微小波动不触发方向断言(±0.02 死区)
    assert audit_claims("20日小幅上涨", {"rev_20": 0.01}, "") == []


def test_nan_and_missing_fields_safe():
    assert audit_claims("随便说点什么", {}, "") == []
    assert audit_claims("20日上涨", {"rev_20": float("nan")}, "") == []


def test_unsourced_percents_helper():
    assert unsourced_percents("RankIC 4.8%,年化48%", "ic: RankIC 4.80% · 回测年化48%") == []
    rogue = unsourced_percents("动量20日+20%", "RankIC 4.80%")
    assert rogue and abs(rogue[0] - 20.0) < 1e-9


def test_no_false_positive_on_20d_high_breakout():
    # 「突破20日高点+5%」是突破叙事不是20日涨跌断言,rev>0 时不许误报
    assert audit_claims("放量突破20日高点+5%", {"rev_20": 0.217}, "+5%") == []
    assert audit_claims("跌破20日低点", {"rev_20": -0.15}, "") == []
    assert audit_claims("站上20日均线后上涨", {"rev_20": 0.217, "ma_diff_20": 0.05}, "") == []
    # 「20日线」是均线高频缩写,不是20日涨跌断言(复审残留观察,一字修锁定)
    assert audit_claims("突破20日线后上涨加速", {"rev_20": 0.217, "ma_diff_20": 0.05}, "") == []


def test_direction_high_magnitude_locks_dead_zone():
    # 高幅度方向矛盾必须触发——锁死 _DIR_DEAD 不被悄悄调大
    assert any("方向矛盾" in f for f in
               audit_claims("收盘已站上20日均线", {"ma_diff_20": -0.60}, ""))
    assert any("方向矛盾" in f for f in
               audit_claims("近20日涨幅可观", {"rev_20": 0.40}, ""))
