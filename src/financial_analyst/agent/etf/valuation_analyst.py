"""EtfValuationAnalyst — tier-2 LLM analyst: ETF premium/discount / tracking error / fee drag."""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfValuationOutput(BaseModel):
    valuation_score: int = 0              # -2..+2
    bull_points: List[str] = []
    bear_points: List[str] = []
    premium_discount_state: str = "at_par"  # premium | discount | at_par
    tracking_error_level: str = "low"       # low | medium | high
    fee_drag_note: str = ""


SYSTEM_PROMPT = """You are an ETF valuation analyst focusing on ETF-specific pricing quality.
You receive:
- premium_discount: realtime_premium_discount_pct (positive = premium, negative = discount)
- tracking_error: tracking_error_annualized (fraction, e.g. 0.003 = 0.3% annualised)
- fee info: total_fee, m_fee (management), c_fee (custody) in % per year

Evaluate:
1. premium_discount_state:
   - premium: realtime_premium_discount_pct > 0.5% → buyer pays above NAV, bear point
   - discount: < -0.5% → opportunity (near-term mean reversion likely), bull point
   - at_par: within ±0.5%, neutral
2. tracking_error_level:
   - low: annualised TE < 0.5% (0.005) → bull
   - medium: 0.5–1.5% → neutral
   - high: > 1.5% → bear, suggests replication quality issues
3. fee_drag_note: brief note on total annual fee and its impact on long-term returns.
   - total_fee > 1.0%: notable drag → bear point
   - total_fee < 0.2%: very competitive → bull point
4. valuation_score: -2..+2 summarising the above three dimensions
5. bull_points / bear_points: 2-4 each citing specific numbers.

Output strictly JSON conforming to schema. No free text."""


class EtfValuationAnalyst(SubAgent[EtfValuationOutput]):
    NAME = "etf-valuation-analyst"
    OUTPUT_SCHEMA = EtfValuationOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        metrics = inputs.get("etf-metrics-fetcher", {})
        premium_discount = metrics.get("premium_discount", {})
        tracking_error = metrics.get("tracking_error", {})

        quote = inputs.get("etf-quote-fetcher", {})
        fee_info: Dict[str, Any] = {}
        for key in ("total_fee", "m_fee", "c_fee"):
            if quote.get(key) is not None:
                fee_info[key] = quote[key]

        bundle: Dict[str, Any] = {
            "premium_discount": premium_discount,
            "tracking_error": tracking_error,
            "fee": fee_info,
        }

        upstream = json.dumps(bundle, default=str, ensure_ascii=False)
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"ETF valuation data:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
