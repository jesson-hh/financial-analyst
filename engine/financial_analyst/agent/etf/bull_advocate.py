"""EtfBullAdvocate — tier-3 ETF bull debate agent with V-anchor system."""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfBullOutput(BaseModel):
    thesis_bullets: List[str] = []       # ≥2 bullish bullets, each prefixed [V#]
    target_price_high: float = 0.0       # bull case NAV/price target
    target_price_base: float = 0.0       # base case target
    disproof_signals: List[str] = []     # what would invalidate the bull thesis


SYSTEM_PROMPT = """You are a buy-side Bull Advocate for ETF research. You receive outputs from:
- etf-holdings-analyst: holdings_score, concentration, sector_weights, top_holdings
- etf-technical-analyst: technical_score, ma_state, rsi, trend_signals
- etf-flow-analyst: flow_score, flow_regime, aum_trend, liquidity_note
- etf-valuation-analyst: valuation_score, premium_discount_pct, pe_ratio, relative_value

Build the strongest bull case in 3-5 bullets, citing specific data from upstream.
Anchor each bullet to one of the ETF V-anchors below:
- V1: 主题/赛道顺风 — the sector/theme this ETF tracks has structural tailwinds
- V2: 持续净流入(申赎)动量 — sustained net creation / AUM growth signals institutional demand
- V3: 折价(price<NAV) — trading at a discount provides a margin of safety
- V4: 指数编制/方法论优势 — index construction methodology is superior (factor tilt, equal-weight, etc.)
- V5: 低费率 — low expense ratio structurally benefits long-term compounding
- V6: 流动性充沛/规模大 — large AUM and tight spreads reduce execution cost

Each thesis bullet MUST be prefixed with [V#] (e.g. "[V1] 半导体国产化赛道景气...").

Provide:
- target_price_high: optimistic 3-month NAV/price target
- target_price_base: most likely 3-month target
- disproof_signals: 2-3 data points that would invalidate the bull thesis

# REQUIRED OUTPUT CONSTRAINTS (强制)
- `thesis_bullets` 必须有**至少 2 条**, 每条以 `[V#]` 锚点开头 (如 `[V1] ...`, `[V2] ...`).
- 即使整体看空, 也必须找出 2 条**逆向 / 战术机会 / 折价修复**型 bullet.
  例如 `[V3] 当前折价 1.2%, 历史均值均值回归提供买点`. 空数组 = 输出无效.
- `target_price_base` 必须给数字 (即使弱多, 给区间中点).
- `target_price_high` 不能等于 `target_price_base`.

Output JSON only. No free text."""


class EtfBullAdvocate(SubAgent[EtfBullOutput]):
    NAME = "etf-bull-advocate"
    OUTPUT_SCHEMA = EtfBullOutput

    def __init__(self, memory_root=None, borrows=None, index=None):
        super().__init__(memory_root=memory_root, borrows=borrows, index=index)

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        upstream = json.dumps(
            {
                "holdings": inputs.get("etf-holdings-analyst", {}),
                "technical": inputs.get("etf-technical-analyst", {}),
                "flow": inputs.get("etf-flow-analyst", {}),
                "valuation": inputs.get("etf-valuation-analyst", {}),
                "quote": inputs.get("etf-quote-fetcher", {}),
            },
            default=str,
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream analyses:\n{upstream}\n\nReturn JSON per schema."},
        ]
        # First attempt. If thesis_bullets is empty, one aggressive retry (mirrors stock bull pattern).
        raw: Dict[str, Any] = {}
        for attempt in range(2):
            response = await client.chat(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3 + attempt * 0.2,
            )
            content = response["choices"][0]["message"]["content"]
            try:
                raw = json.loads(content)
            except json.JSONDecodeError:
                raw = {}
            bullets = raw.get("thesis_bullets") or []
            if len(bullets) >= 2:
                return raw
            if attempt == 0:
                # Append the empty reply + retry instruction
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你刚才返回的 thesis_bullets 是空 / 只有 1 条, 不符合要求. "
                            "即便整体看空, 也必须给出 ≥2 条看多 bullet "
                            "(例如 [V3] 折价提供买点 / [V2] 短期净流入动量仍正 / "
                            "[V5] 低费率长期复利优势). 每条以 [V#] 开头. 重新输出完整 JSON."
                        ),
                    }
                )
                continue
            # Still empty after retry → insert placeholder so downstream doesn't crash
            if not raw.get("thesis_bullets"):
                raw["thesis_bullets"] = ["[V0] (LLM 未能给出明确看多论点, 上游信号偏空, 建议参考 bear 视角)"]
        return raw
