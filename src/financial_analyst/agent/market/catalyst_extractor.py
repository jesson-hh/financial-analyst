"""Catalyst extractor — extract catalysts for A-share anomaly stocks (LLM, single call).

For each stock in market-scanner's top_gainers / top_losers / volume_anomalies,
pull past-48h news (news_db) and use one LLM call to extract every stock's
catalyst type + bullish/bearish judgement at once.

input: market-scanner output (top movers + volume anomalies)
output: List[StockCatalyst] (one per anomaly stock)
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


CatalystType = Literal[
    "policy",       # policy catalyst (industrial policy, regulation, subsidy)
    "earnings",     # earnings / financial report (positive preview, negative preview, blowup)
    "product",      # new product / order / tech breakthrough
    "M&A",          # M&A / restructuring
    "macro",        # macro linkage (Fed, commodities, international)
    "rumor",        # rumour / unconfirmed
    "technical",    # pure technical (breakout / oversold rebound)
    "none",         # no explicit catalyst
]


class StockCatalyst(BaseModel):
    code: str
    name: Optional[str] = None
    pct_chg: Optional[float] = None
    catalyst_type: CatalystType = "none"
    summary: str = ""                            # 1-2 sentences describing what happened
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "low"] = "low"
    cited_news_titles: List[str] = Field(default_factory=list)


class CatalystOutput(BaseModel):
    as_of: str
    catalysts: List[StockCatalyst] = Field(default_factory=list)
    n_with_catalyst: int = 0          # count of stocks with an explicit catalyst
    n_no_news: int = 0                # count of stocks with no news in news_db


SYSTEM_PROMPT = """你是 A 股催化因素提取助手. 给你今日异动股 + 每只股过去 48h 的新闻摘要, 提取每只股的催化因素.

对每只股, 判断:
1. **catalyst_type**: policy / earnings / product / M&A / macro / rumor / technical / none
2. **summary**: 1-2 句话讲发生了啥 (具体 actor + event + 影响)
3. **direction**: bullish / bearish / neutral (对该股而言)
4. **confidence**: high (新闻明确具体) / medium (有相关消息但模糊) / low (无新闻或无关)

规则:
- 没新闻 = none + neutral + low confidence
- 涨幅大但无新闻 = technical (超跌反弹/突破) + neutral
- 跌幅大但无新闻 + 有传闻 = rumor + bearish + low
- 业绩预增/中标公告/重组 = earnings/product/M&A + 具体判读
- 优先用 cited_news_titles 引用 1-3 条最关键新闻

返回 JSON:
{
  "catalysts": [
    {"code": "SH600519", "name": "茅台", "pct_chg": -1.5,
     "catalyst_type": "earnings", "summary": "Q3 利润同比 -8%, 低于预期",
     "direction": "bearish", "confidence": "high",
     "cited_news_titles": ["茅台Q3财报: 净利同比 -8%"]},
    ...
  ]
}
"""


def _fetch_recent_news_for_code(code: str, news_db, since_hours: int = 48,
                                  limit: int = 5) -> List[Dict[str, Any]]:
    """Pull a stock's recent news from NewsDB (title + ts only, capped at `limit` rows)."""
    try:
        rows = news_db.query_news(code=code, since_days=max(1, since_hours // 24), limit=limit)
    except Exception:
        return []
    return [
        {"title": (r.get("title") or "")[:120], "ts": (r.get("ts") or "")[:16]}
        for r in (rows or [])
    ]


class CatalystExtractor(SubAgent[CatalystOutput]):
    """Extract catalysts for anomaly stocks. One LLM call handles N stocks (saves tokens)."""

    NAME = "catalyst-extractor"
    OUTPUT_SCHEMA = CatalystOutput

    def __init__(self, memory_root, news_db=None):
        super().__init__(memory_root=memory_root)
        self._news_db = news_db

    def _get_news_db(self):
        if self._news_db is not None:
            return self._news_db
        from financial_analyst.data.news_db import NewsDB
        return NewsDB()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        as_of = inputs.get("asof_date") or datetime.today().strftime("%Y-%m-%d")
        scanner = inputs.get("market-scanner", {}) or {}

        # Collect candidate stocks (top 5 gainers + top 5 losers + top 5 vol anomalies)
        movers: List[Dict[str, Any]] = []
        for grp in ("top_gainers", "top_losers", "volume_anomalies"):
            for r in (scanner.get(grp) or [])[:5]:
                if r.get("code"):
                    movers.append({
                        "code": r["code"],
                        "name": r.get("name") or "",
                        "pct_chg": r.get("pct_chg"),
                        "group": grp,
                    })

        if not movers:
            return CatalystOutput(as_of=as_of).model_dump()

        # Pull recent news per code
        db = self._get_news_db()
        try:
            stock_briefs: List[Dict[str, Any]] = []
            n_no_news = 0
            for m in movers:
                news = _fetch_recent_news_for_code(m["code"], db)
                if not news:
                    n_no_news += 1
                stock_briefs.append({**m, "news": news})
        finally:
            try:
                db.close()
            except Exception:
                pass

        # LLM call
        client = LLMClient.for_agent(self.NAME)
        user_msg = (
            f"as_of: {as_of}\n\n"
            f"今日异动股 + 各股近 48h 新闻:\n{json.dumps(stock_briefs, ensure_ascii=False, indent=2)[:8000]}\n\n"
            f"对每只股提取催化, 返回 JSON."
        )
        messages = [
            {"role": "system",
             "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": user_msg},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
            cats_raw = parsed.get("catalysts") or []
        except Exception:
            cats_raw = []

        catalysts: List[StockCatalyst] = []
        for c in cats_raw:
            try:
                catalysts.append(StockCatalyst(**c))
            except Exception:
                pass

        return CatalystOutput(
            as_of=as_of,
            catalysts=catalysts,
            n_with_catalyst=sum(1 for c in catalysts if c.catalyst_type != "none"),
            n_no_news=n_no_news,
        ).model_dump()
