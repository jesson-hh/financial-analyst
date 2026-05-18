from __future__ import annotations
import json
from typing import Any, Dict, List, Literal
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient

_MV_TIER_NORM: Dict[str, str] = {
    "大盘": "large",
    "大盘股": "large",
    "large_cap": "large",
    "中盘": "mid",
    "mid_cap": "mid",
    "中小盘": "small",
    "小盘": "small",
    "small_cap": "small",
}


class FundamentalOutput(BaseModel):
    valuation_score: int = 0  # -2..+2
    mv_tier: Literal["large", "mid", "small"] = "mid"
    dimension_detail: Dict[str, str] = {}
    red_flags: List[str] = []
    bull_points: List[str] = []
    bear_points: List[str] = []


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
        parsed = json.loads(response["choices"][0]["message"]["content"])
        # Normalize Chinese or variant mv_tier strings before pydantic validation
        if parsed.get("mv_tier") in _MV_TIER_NORM:
            parsed["mv_tier"] = _MV_TIER_NORM[parsed["mv_tier"]]
        return parsed
