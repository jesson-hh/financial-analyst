"""EtfHoldingsAnalyst — tier-2 LLM analyst: holdings concentration / index methodology."""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfHoldingsOutput(BaseModel):
    holdings_score: int = 0        # -2..+2
    bull_points: List[str] = []
    bear_points: List[str] = []
    top_holding_weight: float = 0.0
    sector_concentration_hhi: float = 0.0
    index_methodology_note: str = ""


SYSTEM_PROMPT = """You are an ETF holdings analyst. You receive ETF holdings data (top constituents, weights,
sector breakdown) and optional sector-rotation context.

Evaluate:
1. top_holding_weight: weight of the single largest constituent (%).
   - >10%: notable concentration risk → bear point
   - <5%: well-diversified → bull point
2. sector_concentration_hhi: Herfindahl-Hirschman Index computed across sector weights
   (sum of squared sector-weight fractions, range 0–1).
   - HHI > 0.25: high sector concentration → deduct 1 from score
   - HHI < 0.10: well-diversified across sectors → add 1 to score
3. index_methodology_note: brief comment on the index construction method
   (e.g. "市值加权宽基", "等权行业ETF", "Smart Beta低波", "主题ETF").
4. holdings_score: integer -2..+2 summarising the holdings quality.
   - +2: diversified, transparent, liquid constituents
   - -2: highly concentrated, illiquid or opaque holdings
5. bull_points / bear_points: 2-4 specific points each, citing numbers.

If sector-rotation context is supplied, note whether the ETF's sector tilt aligns with
today's rotation leaders (add to bull_points) or laggards (add to bear_points).

Output strictly JSON conforming to schema. No free text."""


class EtfHoldingsAnalyst(SubAgent[EtfHoldingsOutput]):
    NAME = "etf-holdings-analyst"
    OUTPUT_SCHEMA = EtfHoldingsOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        metrics = inputs.get("etf-metrics-fetcher", {})
        holdings_data = metrics.get("holdings", {})

        bundle: Dict[str, Any] = {"holdings": holdings_data}

        rotation = inputs.get("sector-rotation-analyzer") or {}
        if rotation:
            bundle["sector_rotation"] = {
                "today_leaders_top3": [
                    {"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                    for s in (rotation.get("today_leaders") or [])[:3]
                ],
                "today_laggards_top3": [
                    {"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                    for s in (rotation.get("today_laggards") or [])[:3]
                ],
                "signal": rotation.get("rotation_signal", ""),
            }

        upstream = json.dumps(bundle, default=str, ensure_ascii=False)
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"ETF holdings data:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
