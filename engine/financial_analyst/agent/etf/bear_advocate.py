"""EtfBearAdvocate — tier-3 ETF bear debate agent with F-anchor system."""
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfBearOutput(BaseModel):
    thesis_bullets: List[str] = []    # ≥2 bearish bullets, each prefixed [F#]
    target_price_low: float = 0.0     # bear case NAV/price target
    downside_pct: float = 0.0         # vs current price, negative e.g. -0.15


SYSTEM_PROMPT = """You are a buy-side Bear Advocate for ETF research. You receive same upstream as bull.

Build the strongest bear case in 3-5 bullets. Anchor each to the ETF F-anchors below:
- F1: 赛道拥挤已 price-in — the sector/theme is overcrowded and fully priced into the index
- F2: 跟踪误差漂移 — tracking error vs benchmark has widened, reducing index fidelity
- F3: 高费率拖累 — above-average expense ratio structurally drags long-term returns
- F4: 持仓过度集中/单票风险 — top-10 concentration risk; one stock collapse hurts NAV materially
- F5: 溢价均值回归 — trading at a premium to NAV that will mean-revert downward
- F6: AUM 萎缩/清盘风险 — persistent net outflow/AUM shrinkage raises fund closure risk
- F7: 杠杆/反向 ETF 衰减 — leveraged or inverse ETF daily rebalancing causes decay in trending markets

Each thesis bullet MUST be prefixed with [F#] (e.g. "[F1] 赛道拥挤...").

Provide:
- target_price_low: bear case 3-month NAV/price target
- downside_pct: estimated downside as a negative percentage (e.g. -0.12 for -12%)

# REQUIRED OUTPUT CONSTRAINTS (强制)
- `thesis_bullets` 必须有**至少 2 条**, 每条以 `[F#]` 锚点开头.
- 即使整体看多, 也必须找出 2 条潜在风险 (估值过热 / 赛道拥挤 / AUM 萎缩). 空数组 = 输出无效.
- `target_price_low` 给数字 (即使弱空, 给可达低点).
- `downside_pct` 为负值 (如 -0.10).

Output JSON only. No free text."""


class EtfBearAdvocate(SubAgent[EtfBearOutput]):
    NAME = "etf-bear-advocate"
    OUTPUT_SCHEMA = EtfBearOutput

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

        # Use FTS5 retrieval when an index is available; fall back to full load (mirrors stock bear)
        if self.memory.index is not None:
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
        # Same retry-then-placeholder pattern as stock bear advocate
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
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你刚才返回的 thesis_bullets 是空 / 只有 1 条, 不符合要求. "
                            "即便整体看多, 也必须给出 ≥2 条看空 bullet "
                            "(例如 [F1] 赛道拥挤估值透支 / [F4] 持仓集中单票崩塌风险 / "
                            "[F6] AUM 持续萎缩清盘风险). 每条以 [F#] 开头. 重新输出完整 JSON."
                        ),
                    }
                )
                continue
            # Still empty after retry → placeholder
            if not raw.get("thesis_bullets"):
                raw["thesis_bullets"] = ["[F0] (LLM 未能给出明确看空论点, 上游信号偏多, 建议参考 bull 视角)"]
        return raw
