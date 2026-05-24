"""Global news aggregator — overseas-news aggregation + transmission-channel judgement (LLM).

v1: mainly based on price moves from overseas-market-scanner + the LLM's
built-in knowledge + transmission rules in memory; writes "today's overseas
landscape + key channels + impact on A-shares". Optionally also pulls
macro-tagged news from news_db (if any).

v2 (future): hook up X / Reuters / WSJ feed, fetch real news titles.

input:
- overseas-market-scanner output (us + hk price moves + VIX + risk_tone)
- optional news_db macro tag

output: NewsImpactBundle (one narrative paragraph + impact channel categorisation)
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


Channel = Literal[
    "us_equity",       # US equity direct impact (tech/AI/China ADRs)
    "fed_policy",      # Fed monetary policy (rates/QT/PSL)
    "geopolitical",    # geopolitics (US-China / Russia-Ukraine / Middle East / Taiwan Strait)
    "commodity",       # commodities (oil / gold / copper)
    "china_specific",  # overseas news targeting China (tariffs / sanctions / MSCI / foreign capital)
    "fx_rates",        # FX / rates (DXY / UST10Y / USDCNY)
]


class NewsImpactItem(BaseModel):
    channel: Channel
    summary: str                                 # 1-2 sentences
    direction_for_a_shares: Literal["bullish", "bearish", "neutral"] = "neutral"
    affected_sectors: List[str] = Field(default_factory=list)  # affected A-share sectors
    importance: Literal["high", "medium", "low"] = "medium"


class GlobalNewsOutput(BaseModel):
    as_of: str
    overall_narrative: str = ""                  # one overview paragraph (~150 chars)
    impacts: List[NewsImpactItem] = Field(default_factory=list)
    key_channels: List[Channel] = Field(default_factory=list)


SYSTEM_PROMPT = """你是 A 股海外消息面分析助手. 给你今日国际指数的 OHLC + risk_tone 判读, 用你的知识 + memory 提取的传导规则, 写一段"海外格局 → A 股可能影响" 的报告.

任务:
1. **overall_narrative**: 一段 100-150 字的总览, 描述当下海外宏观/市场格局 (美股节奏 / Fed 政策预期 / 港股流动性 / VIX 风险偏好 / 关键事件等)
2. **impacts**: 拆 3-5 个具体影响 channel, 每个独立 JSON 对象 — channel 类型 + summary + 对 A 股方向 + 受影响板块 + importance
3. **key_channels**: 列出今日最关键的 1-3 个 channel

判读时:
- 美股大涨 + 港股强 → A 股 risk_on, 成长/科技受益
- VIX > 25 → 系统性避险, A 股大概率低开, 防御/红利占优
- 美元强 + UST 高 → 外资可能流出, AH 股大盘价值股承压
- 商品涨 (原油/铜/金) → 上游资源股 + 通胀链
- 仅有价格变动数据, 没新闻 title 时, 也可基于宏观 channel 写 narrative (用 medium / low confidence)

返回 JSON:
{
  "overall_narrative": "...",
  "impacts": [{"channel": "us_equity", "summary": "...", "direction_for_a_shares": "bullish", "affected_sectors": ["半导体", "CPO"], "importance": "high"}, ...],
  "key_channels": ["us_equity", "fed_policy"]
}
"""


class GlobalNewsAggregator(SubAgent[GlobalNewsOutput]):
    """Write a transmission report from overseas-index moves. 1 LLM call."""

    NAME = "global-news-aggregator"
    OUTPUT_SCHEMA = GlobalNewsOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        as_of = inputs.get("asof_date") or datetime.today().strftime("%Y-%m-%d")
        overseas = inputs.get("overseas-market-scanner", {}) or {}

        # Build a compact context for the LLM (strip out verbose fields)
        ctx = {
            "as_of": as_of,
            "risk_tone": overseas.get("risk_tone"),
            "risk_tone_detail": overseas.get("risk_tone_detail"),
            "vix_level": overseas.get("vix_level"),
            "us_overnight": {
                code: {
                    "name": s.get("name"),
                    "price": s.get("price"),
                    "changePercent": s.get("changePercent"),
                } for code, s in (overseas.get("us_overnight") or {}).items()
            },
            "hk_market": {
                code: {
                    "name": s.get("name"),
                    "price": s.get("price"),
                    "changePercent": s.get("changePercent"),
                } for code, s in (overseas.get("hk_market") or {}).items()
            },
        }

        client = LLMClient.for_agent(self.NAME)
        messages = [
            {"role": "system",
             "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user",
             "content": f"今日海外快照:\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n返回 JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
        except Exception:
            parsed = {}

        impacts_raw = parsed.get("impacts") or []
        impacts: List[NewsImpactItem] = []
        for i in impacts_raw:
            try:
                impacts.append(NewsImpactItem(**i))
            except Exception:
                pass

        return GlobalNewsOutput(
            as_of=as_of,
            overall_narrative=str(parsed.get("overall_narrative", ""))[:600],
            impacts=impacts,
            key_channels=parsed.get("key_channels") or [],
        ).model_dump()
