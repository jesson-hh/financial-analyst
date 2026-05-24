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

If a `# 产业链上下文 (必读)` block is supplied at the end of the user message, you MUST:
- Frame at least one bull or bear point around the stock's role in the chain
  (anchor / data_supported / llm_inferred) and weight.
- Cite at least one peer code by name + the catalyst from the product's
  "催化逻辑" section.
- For anchor-role stocks, treat the chain catalyst as structural support.
- For llm_inferred role stocks with weight < 0.5, flag that the chain link
  is hypothesised (not data-confirmed) — add to red_flags as
  "chain_link_inferred_only".

Output strictly JSON conforming to schema. No free text."""


class FundamentalAnalyst(SubAgent[FundamentalOutput]):
    NAME = "fundamental-analyst"
    OUTPUT_SCHEMA = FundamentalOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps(inputs.get("quote-fetcher", {}), default=str, ensure_ascii=False)

        # v1.4.5: industry-chain context injection (best-effort, silent skip when absent)
        factor = inputs.get("factor-computer", {}) or {}
        chain_ctx = factor.get("chain_context") or {}
        chain_block = ""
        if chain_ctx and chain_ctx.get("primary_product"):
            chain_block = (
                "\n\n# 产业链上下文 (必读)\n"
                + json.dumps(chain_ctx, ensure_ascii=False, indent=2)
            )

        # v1.9.7: optional overseas context (新 Tier-1 agent)
        overseas = inputs.get("overseas-market-scanner") or {}
        overseas_block = ""
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        if overseas:
            overseas_block = (
                "\n\n# v1.9.7 海外宏观快照 (optional context)\n"
                + json.dumps({
                    "risk_tone": overseas.get("risk_tone"),
                    "risk_tone_detail": overseas.get("risk_tone_detail"),
                    "vix": overseas.get("vix_level"),
                }, ensure_ascii=False, indent=2)
            )
            sys_prompt += (
                "\n\n# v1.9.7 海外 context 用法\n"
                "如果 user 段含 `# v1.9.7 海外宏观快照` 块, 在估值判读时考虑全球流动性:\n"
                "- risk_off + 高 VIX → 大盘估值锚 (PE/PB) 承压, 估值评分扣 0.5\n"
                "- risk_on + 低 VIX → 估值锚抬升, 加 0.5 (但 mega-cap 仍归 0)\n"
                "- 该股出口 / 海外业务占比高 + 美元强 → bear point 加 1 条"
            )

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Upstream quote data:\n{upstream}{chain_block}{overseas_block}\n\nReturn JSON per schema."},
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
