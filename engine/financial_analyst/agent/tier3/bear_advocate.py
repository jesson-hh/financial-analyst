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

If a `# 上次研报时间线` block is supplied at the end of the user message, you
MUST reconcile your bear case with it: cite the most recent prior judgement
(rating + date), and at least one bullet should reference what's changed
or confirmed since the prior analysis. Treat the timeline as the user's
accumulated research on this stock.

# REQUIRED OUTPUT CONSTRAINTS (强制)
- `thesis_bullets` 必须有**至少 2 条**, 每条以 `[F#]` 锚点开头 (如 `[F4] 游资博弈票...`, `[F8] super_distr...`).
- 即使整体看多, 也必须找出 2 条潜在风险 (估值过热 / 板块拥挤 / 信号衰减 / 政策风险). 空数组 = 输出无效.
- `f_anchors` 数组列出本次用到的 F# (如 `["F2", "F8"]`), 不能空.
- `target_price_low` 给数字 (即使弱空, 给可达低点); `downside_pct` 为负值百分比 (如 -0.15).

Output JSON only. No free text."""


class BearAdvocate(SubAgent[BearOutput]):
    NAME = "bear-advocate"
    OUTPUT_SCHEMA = BearOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        factor = inputs.get("factor-computer", {}) or {}
        upstream = json.dumps({
            "fundamental": inputs.get("fundamental-analyst", {}),
            "technical": inputs.get("technical-analyst", {}),
            "whale": inputs.get("whale-analyst", {}),
            "quant": inputs.get("quant-analyst", {}),
        }, default=str, ensure_ascii=False)
        timeline = (factor.get("stock_timeline") or "").strip()
        timeline_block = f"\n\n# 上次研报时间线 (必读)\n{timeline}" if timeline else ""

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
            {"role": "user", "content": f"Upstream:\n{upstream}{timeline_block}\n\nReturn JSON per schema."},
        ]
        # 同 bull-advocate: 若 thesis_bullets 空, 一次激进 retry; 仍空才占位 (introspector 踩坑反推)
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
                messages.append({"role": "user", "content":
                    "你刚才返回的 thesis_bullets 是空 / 只有 1 条, 不符合要求. "
                    "即便整体看多, 也必须给出 ≥2 条看空 bullet (例如 [F2] 游资博弈票, 模型失效 / "
                    "[F8] super_distr 量能特征 / [F12] 估值已透支基本面). 每条以 [F#] 开头. 重新输出完整 JSON."
                })
                continue
            raw.setdefault("thesis_bullets", ["[F0] (LLM 未能给出明确看空论点, 上游信号偏多, 建议参考 bull 视角)"])
            raw.setdefault("f_anchors", ["F0"])
        return raw
