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

# Alpha-Zoo signals (v1.4.2+ rolling top-N with bench metadata)
The snapshot rotates through 440 alphas weekly — quant-analyst sees the
top-20 picked by latest-bench |rank_IR| for the current universe. Each
entry in ``zoo_signals.alphas`` has:

    {
      value: float,          // raw alpha value for this stock
      rank_pct: float,       // cross-sectional percentile in [0, 1]
      universe_n: int,       // number of stocks in the rank pool
      bench_rank_ic: float,  // signed cross-universe rank-IC from bench
      bench_hit_rate: float, // bench-measured direction-accuracy [0, 1]
      bench_n_dates: int     // bench window length (>=30 trustworthy)
    }

# Direction interpretation (sign-agnostic, derived per-alpha)
Compute each alpha's direction from its own ``bench_rank_ic`` sign — do
NOT hard-code sign conventions; the alpha set is dynamic.

For each alpha row, the BULLISH side is:
- ``rank_pct > 0.7`` IF ``bench_rank_ic > 0`` (positive-class alpha,
  high rank predicts forward gain)
- ``rank_pct < 0.3`` IF ``bench_rank_ic < 0`` (reversal-class alpha,
  low rank predicts forward gain)

Symmetrically, the BEARISH side is the opposite extreme. Treat alphas
with ``bench_hit_rate`` near 50% as low-confidence; only count an alpha
toward the consensus if ``|bench_rank_ic| > 0.05 AND bench_n_dates >= 30``.

# Consensus aggregation
- 5+ bullish alphas with no bearish ones: +2 (strong_long) if model agrees
- 3+ bullish: +1 (weak_long)
- Mixed (some bullish, some bearish): 0 (neutral)
- 3+ bearish: -1 (weak_short)
- 5+ bearish: -2 (strong_short)
- ZOO CONTRADICTS MODEL (model rank_pct > 0.7 but zoo majority bearish, or
  vice versa) → conviction=low, anti_signals += "zoo_model_disagreement"

bull_points / bear_points MUST cite at least one specific alpha row,
quoting both ``rank_pct`` and ``bench_rank_ic`` so the reader can verify
the direction. Example bull_point:
"qlib_VSTD60 rank_pct=85% with bench_rank_ic=+0.054 (positive-class) →
bullish reading from this alpha."

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
        bundle: Dict[str, Any] = {
            "model_predictor": inputs.get("model-predictor", {}),
            "factor_scores": factor.get("factor_scores", {}),
            "zoo_signals": factor.get("zoo_signals", {}),  # v1.3.4+
        }
        # v1.9.7: 行业轮动信号 (新 Tier-1 agent)
        rotation = inputs.get("sector-rotation-analyzer") or {}
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        if rotation:
            bundle["sector_rotation"] = {
                "leaders": [{"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                            for s in (rotation.get("today_leaders") or [])[:3]],
                "laggards": [{"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                             for s in (rotation.get("today_laggards") or [])[:3]],
                "signal": rotation.get("rotation_signal", ""),
            }
            sys_prompt += (
                "\n\n# v1.9.7 板块轮动 context\n"
                "如果 upstream 含 sector_rotation, 在 quant 信号融合时考虑:\n"
                "- 该股所属行业在 leaders → 因子打分 + 0.3\n"
                "- 在 laggards → 因子打分 - 0.3\n"
                "- 行业不明 → 不动. 行业轮动属于 cross-sectional 信号, 是因子之外的补充."
            )
        upstream = json.dumps(bundle, default=str, ensure_ascii=False)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
