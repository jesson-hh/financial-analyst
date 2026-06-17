"""EtfTechnicalAnalyst — tier-2 LLM analyst: ETF price / MA / RSI / breakout."""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfTechnicalOutput(BaseModel):
    technical_score: int = 0       # -2..+2
    bull_points: List[str] = []
    bear_points: List[str] = []
    ma_state: str = "neutral"      # bullish | bearish | neutral
    rsi_state: str = "neutral"     # overbought | oversold | neutral
    breakout_signal: Optional[str] = None


SYSTEM_PROMPT = """You are a technical analyst specialising in ETFs. You receive ETF quote data
(close price, short/medium/long MA, volatility, volume_ratio, returns).

Classify:
- ma_state: based on price relative to MA5/MA20/MA60
  * bullish: price > MA5 > MA20 > MA60
  * bearish: price < MA5 < MA20 < MA60
  * neutral: mixed
- rsi_state: infer from price action and volatility patterns
  * overbought: strong recent run (ret_20d > 10% + high volatility), oversold: sharp decline
  * neutral: otherwise
- breakout_signal: optional string if a notable pattern exists
  (e.g. "volume_breakout", "ma20_golden_cross", "52w_high_break"), else null
- technical_score: -2..+2 summarising the overall technical picture
- bull_points / bear_points: 2-4 specific observations citing numbers

Output strictly JSON conforming to schema. No free text."""


class EtfTechnicalAnalyst(SubAgent[EtfTechnicalOutput]):
    NAME = "etf-technical-analyst"
    OUTPUT_SCHEMA = EtfTechnicalOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        quote = inputs.get("etf-quote-fetcher", {})

        upstream = json.dumps(quote, default=str, ensure_ascii=False)
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"ETF quote data:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
