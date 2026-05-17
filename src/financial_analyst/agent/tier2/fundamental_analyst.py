from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class FundamentalOutput(BaseModel):
    valuation_score: int  # -2..+2
    mv_tier: str  # "large" | "mid" | "small"
    dimension_detail: Dict[str, str]  # e.g. {"pe": "in line", "pb": "premium"}
    red_flags: List[str]
    bull_points: List[str]
    bear_points: List[str]


SYSTEM_PROMPT = """You are a fundamental equity analyst for A-shares. You receive structured quote data
(price, PE, PB, PS, market cap, returns) and produce a structured analysis.

Apply the market-cap-tiered rating from memory:
- mv_yi > 1000 (large): valuation_score forced to 0 (factor scores unreliable for mega-caps)
- mv_yi 300-1000 (mid): capped at ±1
- mv_yi 100-300 (small-mid): full -2..+2 allowed
- mv_yi < 100 (small): full -2..+2 allowed, but flag if pe>100 AND ret60>50% AND mv<200 (game-capital ticker — neutralize signal)

Set mv_tier to one of: "large", "mid", "small".
Identify red_flags (e.g. game-capital pattern, valuation extreme, financial weakness).
List bull/bear points (2-4 each, cite specific numbers).
Output strictly JSON conforming to schema. No free text."""


class FundamentalAnalyst(SubAgent[FundamentalOutput]):
    NAME = "fundamental-analyst"
    OUTPUT_SCHEMA = FundamentalOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps(inputs.get("quote-fetcher", {}), default=str, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream quote data:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
