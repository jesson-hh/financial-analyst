from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class WhaleOutput(BaseModel):
    whale_score: int = 0  # -2..+2
    sentiment_label: str = "neutral"  # super_distr | distr | tail_surge | bounce | neutral
    vol_regime_label: str = "neutral"
    board_total_score: Optional[int] = None  # -7..+8 if limit-up day exists
    alerts: List[str] = []
    bull_points: List[str] = []
    bear_points: List[str] = []


SYSTEM_PROMPT = """You are a whale-behavior + sentiment analyst for A-shares. You interpret:
- whale signals (OBV trend, VR judgment, MFI, shadow ratio, chip concentration, whale_judge)
- board score (v4+v5) — limit-up board quality (-7..+8)
- vol_regime (R7-R20) — super_distr / distr / tail_surge / bounce / neutral

Apply the 14 S/SS sentiment signals from memory:
- super_distr (SS, monthly 11/12 hit): fwd_5d -4.20pp — alert "super distribution"
- distr (S, monthly 13/13): -1.42pp — alert "distribution"
- tail_surge: -1.40pp — alert "tail-surge"
- bounce (S6/S7): +0.85-0.94pp — alert "bounce setup"
- seal_at_close=False on limit-up: extreme negative signal — alert "broken board"

whale_score aggregation:
- accumulating + bounce: +2
- neutral whale + neutral regime: 0
- distributing + distr/tail_surge/super_distr: -2

Output JSON only. No free text."""


class WhaleAnalyst(SubAgent[WhaleOutput]):
    NAME = "whale-analyst"
    OUTPUT_SCHEMA = WhaleOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        quote = inputs.get("quote-fetcher", {})
        factors = inputs.get("factor-computer", {})
        upstream = json.dumps({
            "quote": quote,
            "whale_signals": factors.get("whale_signals", {}) if isinstance(factors, dict) else {},
            "board_score": factors.get("board_score", {}) if isinstance(factors, dict) else {},
            "vol_regime": factors.get("vol_regime", {}) if isinstance(factors, dict) else {},
        }, default=str, ensure_ascii=False)
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
