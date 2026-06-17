# guanlan_v2.screen.llm · L4/L5 真 LLM 定性点评(离线部分:prompt 构造 + 空入参短路,不触网)
#
# 真模型调用不在单测覆盖(外部网络);此处只锁 prompt 诚实约束 + 失败诚实返回的契约。
import asyncio

from guanlan_v2.screen.llm import build_prompt, screen_commentary

_FINAL = [
    {"code": "SH600519", "name": "贵州茅台", "ind": "白酒", "stars_str": "★★★★★",
     "label": "强烈看多", "v4_total": 6, "band": {"tier": "重仓", "lo": 25, "hi": 35},
     "mainline": "mainline", "mainline_golden": True, "shields": [{"name": "金信号护盾"}],
     "views": [{"v": "V3", "name": "强度", "conf": "data", "label": "主线"},
               {"v": "V5", "name": "反应", "conf": "gap", "label": "需材料"}]},
]
_MARKET = {"stage": "回踩/启动", "lu_pct60": 0.333, "as_of": "2026-04-21"}


def test_prompt_carries_holdings_and_market():
    p = build_prompt(_FINAL, _MARKET)
    assert "贵州茅台" in p and "SH600519" in p
    assert "回踩/启动" in p              # V1 节奏带入
    assert "金信号护盾" in p             # 护盾带入
    assert "V3强度" in p or "V3" in p    # 九视角读数带入


def test_prompt_has_no_fabrication_guard_in_system():
    # 诚实红线必须在 system 提示里(禁编造数字/新闻)
    from guanlan_v2.screen.llm import _SYSTEM
    assert "严禁编造" in _SYSTEM
    assert "需盘面/材料确认" in _SYSTEM
    assert "JSON" in _SYSTEM


def test_empty_final_short_circuits_without_network():
    # 无持仓 → 立即 ok:False,不触网(快、确定)
    res = asyncio.run(screen_commentary([], _MARKET))
    assert res["ok"] is False
    assert "无" in res["reason"]


# ───────── B:选因子 / 调约束(离线:prompt + 校验 + 空入参短路)─────────
from guanlan_v2.screen.llm import (  # noqa: E402
    build_phrase_prompt,
    build_pick_prompt,
    parse_phrase,
    pick_factors,
    _validate_factors,
)


def test_pick_prompt_lists_catalog():
    """选股页 2.0:pick 提示词带动态目录(~56 因子,含大盘共振族);幽灵 fa_north 已除名。"""
    p = build_pick_prompt("震荡市超跌反弹叠加资金面")
    assert "fa_reversal" in p                          # legacy 价量仍在目录
    assert "fa_north" not in p                         # 北向停披已除名(原幽灵因子)
    assert "市场Beta" in p or "共振" in p                # 大盘因子族进目录
    assert "震荡市超跌反弹" in p


def test_phrase_prompt_carries_current_cfg():
    p = build_phrase_prompt("更集中 行业均衡", {"topN": 20, "industryNeutral": True, "indCap": 0.25, "liqMin": 5})
    assert "topN=20" in p and "更集中" in p


def test_validate_factors_filters_unknown_and_clamps():
    fs = _validate_factors([{"id": "fa_reversal", "w": 5}, {"id": "bogus", "w": 1},
                            {"id": "fa_news", "w": 0.5}, {"id": "fa_distrib", "w": 0.01},
                            {"id": "fa_reversal", "w": 1}])
    ids = [f["id"] for f in fs]
    assert "bogus" not in ids and "fa_news" not in ids   # 未知/已清的 id 丢弃(fa_news 随演示链清掉)
    assert ids.count("fa_reversal") == 1                 # 去重
    assert next(f for f in fs if f["id"] == "fa_reversal")["w"] == 2.0   # 钳上限
    assert next(f for f in fs if f["id"] == "fa_distrib")["w"] == 0.1    # 钳下限


def test_pick_and_phrase_empty_short_circuit():
    assert asyncio.run(pick_factors(""))["ok"] is False
    assert asyncio.run(parse_phrase("", {"topN": 20}))["ok"] is False
