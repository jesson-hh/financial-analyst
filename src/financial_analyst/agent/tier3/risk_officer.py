from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class RiskOutput(BaseModel):
    risk_score: int                    # -2..0 (risk officer never positive — only constrains)
    blind_spots: List[str]             # things both Bull and Bear missed
    position_sizing_advice: str        # "0%" | "1-3%" | "3-5%" | "5-8%"
    veto_flags: List[str]              # if non-empty, position_pct should be 0
    conditional_approval: str          # e.g. "OK if stop-loss at 1450; reduce if super_distr persists"
    hard_rule_triggers: List[str]      # rules from memory that fired


SYSTEM_PROMPT = """You are an independent Chief Risk Officer for an A-share research desk. You receive:
- bull-advocate: thesis_bullets, target_price_high/base, disproof_signals, v_anchors
- bear-advocate: thesis_bullets, valuation_concerns, target_price_low, downside_pct, f_anchors
- news-reader: events[], numbers[] (untrusted)
- f10-reader: recent_events, lhb_seats, event_classified.negative
- factor-computer: vol_regime, board_score, factor_scores

Apply HARD RULES from memory — these CANNOT be overridden by bull/bear opinion:

1. GAME-CAPITAL VETO: if quote shows mv_yi<200 AND pe>100 AND ret_60d>0.50:
   → veto_flags += ["game_capital_speculation"]; position_sizing_advice = "0%"

2. NEGATIVE EVENT VETO: if any event in f10.event_classified.negative has severity>=2:
   → veto_flags += ["recent_severe_negative_event"]; position_sizing_advice = "0%"

3. SUPER_DISTR REDUCTION: if factor-computer.vol_regime.regime_label == "super_distr":
   → veto_flags += ["super_distribution_active"]; position_sizing_advice <= "1-3%"

4. BROKEN BOARD: if factor-computer.board_score.detail.seal_at_close == False AND board_score.v5_score < 0:
   → veto_flags += ["broken_board"]; position_sizing_advice = "0%"

5. risk_score:
   - any veto active: -2
   - bear-advocate convincing + no veto: -1
   - bull > bear + no veto: 0 (CRO never positive)

Identify blind_spots — risks neither Bull nor Bear flagged.

Output JSON only. No free text."""


class RiskOfficer(SubAgent[RiskOutput]):
    NAME = "risk-officer"
    OUTPUT_SCHEMA = RiskOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps({
            "bull": inputs.get("bull-advocate", {}),
            "bear": inputs.get("bear-advocate", {}),
            "news": inputs.get("news-reader", {}),
            "f10": inputs.get("f10-reader", {}),
            "factor": inputs.get("factor-computer", {}),
        }, default=str, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.1,
        )
        return json.loads(response["choices"][0]["message"]["content"])
