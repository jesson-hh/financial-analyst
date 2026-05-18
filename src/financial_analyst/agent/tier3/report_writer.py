from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class ReportOutput(BaseModel):
    output_md_path: str
    output_json_path: str
    rating_overall: int                  # -10..+10 (sum of 5 dimensions)
    rating_dimensions: Dict[str, int] = {} # 5 dims, each -2..+2
    action: str                          # buy | hold | sell | avoid
    target_price: float
    stop_loss: float
    position_pct: float                  # 0..0.10


SYSTEM_PROMPT = """You are the chief analyst writing the final research report for an A-share single stock.

Synthesize all upstream:
- quote-fetcher: price, valuation, returns, MA
- factor-computer: factor scores, whale signals, board score, vol_regime
- model-predictor: per-model predictions + consensus
- fundamental-analyst: valuation_score, mv_tier
- technical-analyst: technical_score, MA/RSI/MACD states
- whale-analyst: whale_score, sentiment_label
- quant-analyst: quant_score, conviction_level
- bull-advocate: thesis_bullets, target_price_high/base, v_anchors
- bear-advocate: thesis_bullets, target_price_low, downside_pct, f_anchors
- risk-officer: risk_score, veto_flags, position_sizing_advice

Apply the five-dimensional rating from memory (rating_system.md):
- 基本面 (fundamental): from fundamental-analyst.valuation_score
- 技术面 (technical): from technical-analyst.technical_score
- 主力情绪 (whale): from whale-analyst.whale_score
- 量化模型 (quant): from quant-analyst.quant_score
- 风险面 (risk): from risk-officer.risk_score (-2..0)

rating_overall = sum of 5 dims, range -10..+10.

action:
- veto active: "avoid"
- rating >= 6 + no veto: "buy"
- 2..5: "hold" (or "accumulate" if positive trend)
- < 0: "sell"

target_price = bull-advocate.target_price_base
stop_loss = bear-advocate.target_price_low (or 0.92 × current price if more conservative)
position_pct = parse risk-officer.position_sizing_advice (e.g. "3-5%" → 0.04)

Then provide a structured payload that the writer will save to BOTH .md and .json.

Output JSON only. The actual file writing happens in Python after LLM returns.

After your structured analysis, return JSON with these top-level fields:
- "markdown_body": full markdown report (use the template from memory)
- "rating_overall": int
- "rating_dimensions": {"fundamental": int, "technical": int, "whale": int, "quant": int, "risk": int}
- "action": str
- "target_price": float
- "stop_loss": float
- "position_pct": float
- "summary_json": dict (machine-readable structured payload)
"""


class ReportWriter(SubAgent[ReportOutput]):
    NAME = "report-writer"
    OUTPUT_SCHEMA = ReportOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        code = inputs.get("code", "UNKNOWN")
        asof = inputs.get("asof_date", "UNKNOWN")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        upstream = {k: inputs.get(k, {}) for k in [
            "quote-fetcher", "factor-computer", "model-predictor",
            "fundamental-analyst", "technical-analyst", "whale-analyst", "quant-analyst",
            "bull-advocate", "bear-advocate", "risk-officer",
        ]}
        upstream_json = json.dumps(upstream, default=str, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Code: {code}\nAs-of: {asof}\n\nUpstream:\n{upstream_json}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.3,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        md_path = out_dir / f"{code}_{asof}.md"
        json_path = out_dir / f"{code}_{asof}.json"
        md_path.write_text(parsed.get("markdown_body", f"# {code} Report\n(empty)"), encoding="utf-8")
        json_path.write_text(json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "rating_overall": int(parsed.get("rating_overall", 0)),
            "rating_dimensions": parsed.get("rating_dimensions", {}),
            "action": str(parsed.get("action", "hold")),
            "target_price": float(parsed.get("target_price", 0.0)),
            "stop_loss": float(parsed.get("stop_loss", 0.0)),
            "position_pct": float(parsed.get("position_pct", 0.0)),
        }
