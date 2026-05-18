from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class BearOutput(BaseModel):
    thesis_bullets: List[str] = []              # 3-5 bearish bullets
    valuation_concerns: List[str] = []
    technical_breakdown: List[str] = []
    target_price_low: float = 0.0
    downside_pct: float = 0.0                    # vs current price, e.g. -0.20
    f_anchors: List[str] = []                    # F1-F14 failure mode references


SYSTEM_PROMPT = """You are a buy-side Bear Advocate for A-share single-stock research. You receive same upstream as bull.

Build the strongest bear case in 3-5 bullets. Anchor each to F1-F14 failure modes from memory:
- F1: factor signal in systemic-uptrend regime is unreliable
- F2: game-capital tickers — quant models structurally fail
- F3: Alpha158 failure mode (overfitting on lookback)
- F4: Sub-200亿 + PE>100 + ret60>50% (game-capital)
- F5: 商誉/净资产 > 30% (impairment risk)
- F6: Below-MA200 + rev_20 positive (catching falling knife)
- F7: 增量数据覆盖事故 (data quality breakdown)
- F8: super_distr regime (-4.20pp)
- F9: broken board (seal_at_close=False)
- F10: tail_surge (-1.40pp)
- F11: hidden 大股东 减持 (insider selling)
- F12: industry tailwind already priced in (peak P/E)
- F13: 公用事业 sector weighted down (defensive trap)
- F14: lagging signal trap (limit-up breadth as confirmation)

Provide target_price_low + downside_pct.
Output JSON only. No free text."""


class BearAdvocate(SubAgent[BearOutput]):
    NAME = "bear-advocate"
    OUTPUT_SCHEMA = BearOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps({
            "fundamental": inputs.get("fundamental-analyst", {}),
            "technical": inputs.get("technical-analyst", {}),
            "whale": inputs.get("whale-analyst", {}),
            "quant": inputs.get("quant-analyst", {}),
        }, default=str, ensure_ascii=False)

        # Use FTS5 retrieval when an index is available; fall back to full load
        if self.memory.index is not None:
            # Strip JSON punctuation so FTS5 doesn't mis-parse keys as column filters
            query = " ".join(re.findall(r"[A-Za-z一-鿿]+", upstream[:1500]))
            if query:
                memory_text = self.memory.load_relevant(query, top_k=5)
            else:
                memory_text = self.memory.load_all()
        else:
            memory_text = self.memory.load_all()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + memory_text},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        return json.loads(response["choices"][0]["message"]["content"])
