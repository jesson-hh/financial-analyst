"""EtfRiskOfficer — tier-3 ETF Chief Risk Officer.

risk_score is ALWAYS in [-2, 0].  CRO never awards positive scores;
it only constrains.  Hard-rule vetoes:
- persistent_premium:      ETF trades at persistent premium above threshold
- low_liquidity:           ADV/AUM ratio too low to trade safely
- tracking_error_blowout:  tracking error exploded (factor bet gone wrong)
- aum_below_closure_line:  AUM dangerously small (closure risk)
- leveraged_held_long:     leveraged/inverse ETF held as long position (unsuitable)
"""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfRiskOutput(BaseModel):
    risk_score: int = 0                  # constrained to [-2, 0] after sanity fix
    veto_flags: List[str] = []           # non-empty → position_pct must be 0
    position_sizing_advice: str = "0%"   # "0%" | "1-3%" | "3-5%" | "5-8%"


SYSTEM_PROMPT = """You are an independent ETF Chief Risk Officer (CRO). You receive:
- etf-bull-advocate: thesis_bullets, target_price_high/base, disproof_signals
- etf-bear-advocate: thesis_bullets, target_price_low, downside_pct
- etf-metrics-fetcher: premium_discount_pct, aum_cny, adv_cny, tracking_error,
                       is_leveraged, expense_ratio

Apply these HARD VETO RULES (cannot be overridden by bull/bear opinion):

1. PERSISTENT PREMIUM VETO: if premium_discount_pct > +2% (ETF consistently overpriced vs NAV):
   → veto_flags += ["persistent_premium"]; position_sizing_advice = "0%"

2. LOW LIQUIDITY VETO: if daily_volume_turnover_rate < 0.1% of AUM, or spread estimate > 0.5%:
   → veto_flags += ["low_liquidity"]; position_sizing_advice = "0%"

3. TRACKING ERROR BLOWOUT: if annualised_tracking_error > 2% for equity index ETFs:
   → veto_flags += ["tracking_error_blowout"]; position_sizing_advice <= "1-3%"

4. AUM BELOW CLOSURE LINE: if aum_cny < 200_000_000 (2亿):
   → veto_flags += ["aum_below_closure_line"]; position_sizing_advice = "0%"

5. LEVERAGED/INVERSE HELD LONG: if is_leveraged == True:
   → veto_flags += ["leveraged_held_long"]; position_sizing_advice = "0%"

risk_score rules (CRO NEVER positive — only 0 = minimal risk, -1 = elevated, -2 = veto level):
- any veto active → risk_score = -2
- bear case convincing + no veto → risk_score = -1
- bull > bear + no veto → risk_score = 0

Output JSON only. No free text.
Fields: risk_score (int, -2..0), veto_flags (list[str]), position_sizing_advice (str)."""


class EtfRiskOfficer(SubAgent[EtfRiskOutput]):
    NAME = "etf-risk-officer"
    OUTPUT_SCHEMA = EtfRiskOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps({
            "bull": inputs.get("etf-bull-advocate", {}),
            "bear": inputs.get("etf-bear-advocate", {}),
            "metrics": inputs.get("etf-metrics-fetcher", {}),
        }, default=str, ensure_ascii=False)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])

        # Sanity fix: clamp risk_score to [-2, 0] — CRO never positive
        risk_score = int(raw.get("risk_score", 0))
        risk_score = max(-2, min(0, risk_score))
        raw["risk_score"] = risk_score

        return raw
