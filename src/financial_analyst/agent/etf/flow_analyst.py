"""EtfFlowAnalyst — tier-2 LLM analyst: ETF fund flows / AUM trend / liquidity."""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfFlowOutput(BaseModel):
    flow_score: int = 0            # -2..+2
    bull_points: List[str] = []
    bear_points: List[str] = []
    flow_regime: str = "neutral"   # net_inflow | net_outflow | neutral
    aum_trend: str = "stable"      # rising | falling | stable
    liquidity_note: str = ""


SYSTEM_PROMPT = """You are an ETF fund-flow analyst. You receive ETF flow data:
- latest_share_change: net share creation/redemption (positive = net inflow)
- aum_latest: latest AUM (万元)
- unit_nav: latest NAV per unit

Evaluate:
1. flow_regime: classify based on recent share-change direction
   - net_inflow: positive latest_share_change → bullish sign (institutional demand)
   - net_outflow: negative latest_share_change → bearish (redemption pressure)
   - neutral: near-zero or ambiguous
2. aum_trend: comment on AUM trajectory (rising / stable / falling)
   - Sustained AUM growth: bull point
   - Persistent AUM shrinkage: bear point (liquidity risk, fund closure risk)
3. liquidity_note: brief note on bid-ask spread / daily turnover / creation-redemption
   activity as a proxy for liquidity quality.
4. flow_score: -2..+2
   - +2: strong sustained net inflow, AUM at highs, liquid
   - -2: persistent net outflow, AUM falling, thin market
5. bull_points / bear_points: 2-4 each, citing specific numbers.

Output strictly JSON conforming to schema. No free text."""


class EtfFlowAnalyst(SubAgent[EtfFlowOutput]):
    NAME = "etf-flow-analyst"
    OUTPUT_SCHEMA = EtfFlowOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        metrics = inputs.get("etf-metrics-fetcher", {})
        flow_data = metrics.get("flow", {})
        nav_data = metrics.get("nav", {})

        bundle: Dict[str, Any] = {"flow": flow_data, "nav": nav_data}

        upstream = json.dumps(bundle, default=str, ensure_ascii=False)
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"ETF flow data:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
