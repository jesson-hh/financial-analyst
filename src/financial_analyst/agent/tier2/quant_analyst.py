from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class QuantOutput(BaseModel):
    quant_score: int = 0  # -2..+2
    model_consensus: str = "neutral"  # strong_long | weak_long | neutral | weak_short | strong_short
    conviction_level: str = "low"  # low | medium | high
    anti_signals: List[str] = []
    bull_points: List[str] = []
    bear_points: List[str] = []


SYSTEM_PROMPT = """You are a quantitative analyst for A-shares. You receive predictions from registered models
(LGB momentum, possibly FM cluster, B3 TSFM in v0.2+). You also see factor scores
and (when available) a curated Alpha-Zoo snapshot.

Apply rules from memory:
- For game-capital tickers (mv<200亿 + pe>100 + ret60>50%): quant signals UNRELIABLE — neutralize, set anti_signals="game_capital_speculation"
- Models agreeing (all rank_pct > 0.7 or all < 0.3) = high conviction
- Models diverging > 0.3 spread = low conviction
- Single-model with rank_pct in [0.4, 0.6] = neutral

# Alpha-Zoo signals (v1.3.4+)
When ``zoo_signals.alphas`` is present, each entry is `{value, rank_pct, universe_n}`
where ``rank_pct`` is the stock's cross-sectional percentile within the
snapshot universe (typically 868-stock CSI300). Treat them as
INDEPENDENT confirmations of the model consensus:
- 3+ zoo alphas agreeing (all rank_pct > 0.7 or all < 0.3) bumps conviction one level
- zoo + model agreeing: bull_points must cite at least one specific alpha by name+pct
- zoo CONTRADICTING the model (e.g. model long but zoo top alphas rank_pct < 0.3) → conviction=low, list under anti_signals as "zoo_model_disagreement"

Sign conventions (from CSI300 2024-H2 bench, docs/csi300_bench_2024h2.md):
- `qlib_VSTD60` POSITIVE (high rank_pct = bullish)
- `qlib_ROC60` POSITIVE (rank_pct > 0.7 = oversold recovery candidate)
- `gtja095`, `qlib_STD10`, `qlib_KLEN`, `gtja052`, `qlib_VSUMP20`,
  `qlib_BETA20`, `qlib_IMAX20` NEGATIVE (high rank_pct = bearish; low
  rank_pct = bullish)
- `gtja042` POSITIVE

model_consensus mapping:
- consensus_rank_pct > 0.8: strong_long
- > 0.6: weak_long
- 0.4-0.6: neutral
- < 0.4: weak_short
- < 0.2: strong_short

quant_score:
- strong_long + high conviction: +2
- weak_long + medium: +1
- neutral or low conviction: 0
- weak_short: -1
- strong_short: -2

List anti_signals (rules from memory or zoo disagreements that fired against the model output).
Output JSON only. No free text."""


class QuantAnalyst(SubAgent[QuantOutput]):
    NAME = "quant-analyst"
    OUTPUT_SCHEMA = QuantOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        factor = inputs.get("factor-computer", {}) or {}
        upstream = json.dumps({
            "model_predictor": inputs.get("model-predictor", {}),
            "factor_scores": factor.get("factor_scores", {}),
            "zoo_signals": factor.get("zoo_signals", {}),  # v1.3.4+
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
