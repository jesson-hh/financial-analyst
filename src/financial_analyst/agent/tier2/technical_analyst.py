from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class TechnicalOutput(BaseModel):
    technical_score: int  # -2..+2
    ma_state: str           # bullish | bearish | neutral
    rsi_state: str          # overbought | oversold | neutral
    macd_state: str         # bullish_cross | bearish_cross | neutral
    factor_consensus: str   # strong_long | weak_long | neutral | weak_short | strong_short
    breakout_signal: Optional[str] = None
    bull_points: List[str]
    bear_points: List[str]


SYSTEM_PROMPT = """You are a technical analyst for A-shares. You receive quote data (returns, MA, vol, volume_ratio) and factor scores (rev_20, mom_20, rsi_14, macd_bar, bb_pct_20, obv_slope_20, etc.).

Apply factor insights from memory:
- rev_20 is reversal alpha (positive value = expect mean-reversion DOWN, negative = mean-reversion UP)
- Factor IC/ICIR degrades with market cap; for large-caps the factor signal is weaker
- MA50/MA200 cross interpretation is in memory

Classify:
- ma_state: based on price relative to MA5/MA20/MA60
  * bullish: price above MA5, MA5 above MA20, MA20 above MA60
  * bearish: price below MA5, MA5 below MA20, MA20 below MA60
  * neutral: mixed signals
- rsi_state: <30 oversold, >70 overbought, otherwise neutral
- macd_state: macd_bar transitioning sign
  * bullish_cross: macd_bar recently turned positive (from negative)
  * bearish_cross: macd_bar recently turned negative (from positive)
  * neutral: no recent crossover
- factor_consensus: aggregate vote from rev_20, mom_20, ma_diff_20
  * strong_long: majority of factors signal long with high confidence
  * weak_long: slight edge toward long
  * neutral: no clear direction
  * weak_short: slight edge toward short
  * strong_short: majority of factors signal short with high confidence
- technical_score: integer from -2 to +2 summarizing overall technical outlook
- breakout_signal: optional string if a notable breakout pattern is detected (e.g. "volume_breakout", "ma_golden_cross"), else null
- bull_points: 2-4 specific bullish observations with numbers
- bear_points: 2-4 specific bearish observations with numbers

Output JSON only. No free text."""


class TechnicalAnalyst(SubAgent[TechnicalOutput]):
    NAME = "technical-analyst"
    OUTPUT_SCHEMA = TechnicalOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        quote = inputs.get("quote-fetcher", {})
        factors = inputs.get("factor-computer", {})
        upstream = json.dumps({"quote": quote, "factors": factors}, default=str, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
