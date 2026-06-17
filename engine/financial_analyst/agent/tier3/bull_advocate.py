from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class BullOutput(BaseModel):
    thesis_bullets: List[str] = []      # 3-5 bullish bullets
    catalysts: List[str] = []            # upcoming events that could re-rate
    target_price_high: float = 0.0
    target_price_base: float = 0.0
    disproof_signals: List[str] = []     # what would invalidate the bull thesis
    v_anchors: List[str] = []            # V1-V9 references, e.g. ["V1", "V4-立讯模式"]


SYSTEM_PROMPT = """You are a buy-side Bull Advocate for A-share single-stock research. You receive:
- fundamental-analyst: valuation_score, mv_tier, bull_points, bear_points
- technical-analyst: technical_score, MA/RSI/MACD states, bull_points
- whale-analyst: whale_score, sentiment_label, alerts, bull_points
- quant-analyst: quant_score, model_consensus, conviction_level

Build the strongest bull case in 3-5 bullets, citing specific numbers from upstream.
Anchor each bullet to V1-V9 from the analyst playbook in memory:
- V1: Industry tailwind > company quality
- V2: Margin trend > absolute margin
- V3: Revenue mix shift
- V4: Four modes (立讯/华工/仕佳/信维)
- V5: Capacity utilization > capacity
- V6: Order visibility
- V7: 单价 vs 销量
- V8: Inventory cycle
- V9: Catalyst calendar

Provide target_price_high (bull case) and target_price_base (most likely).
List disproof_signals — what data would invalidate this thesis.

If a `# 上次研报时间线` block is supplied at the end of the user message, you
MUST reconcile your bull case with it: cite the most recent prior judgement
(rating + date), note whether the prior call was right or wrong if outcomes
appear, and at least one bullet should mention what's changed vs the prior
analysis. Treat the timeline as the user's accumulated research on this stock.

# REQUIRED OUTPUT CONSTRAINTS (强制)
- `thesis_bullets` 必须有**至少 2 条**, 每条以 `[V#]` 锚点开头 (如 `[V1] ...`, `[V4-立讯模式] ...`).
- 即使整体看跌, 也必须找出 2 条**逆向 / 短期反弹 / 战术机会 / 等待回调**型的 bullet —
  例如 `[V4-信维模式] 已调整 4 个月 -25%, 等待回踩低点确认后试探...`. 空数组 = 输出无效.
- `v_anchors` 数组列出本次用到的 V# (如 `["V1", "V4-信维"]`), 不能空.
- `target_price_base` 必须给数字 (即使弱多, 给区间中点); `target_price_high` 不能等于 `target_price_base` (否则=没区间).

Output JSON only. No free text."""


class BullAdvocate(SubAgent[BullOutput]):
    NAME = "bull-advocate"
    OUTPUT_SCHEMA = BullOutput

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
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Upstream analyses:\n{upstream}{timeline_block}\n\nReturn JSON per schema."},
        ]
        # 第一次尝试. 若 thesis_bullets 为空, 加一次激进 retry (introspector 历史踩坑).
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
                # 留下助手的空回复 + 加 retry 指令
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                    "你刚才返回的 thesis_bullets 是空 / 只有 1 条, 不符合要求. "
                    "即便整体看跌, 也必须给出 ≥2 条看多 bullet (例如 [V4-信维模式] 等回踩到位后建仓 / "
                    "[V6] 长期订单可见度高, 短期波动中线买点). 每条以 [V#] 开头. 重新输出完整 JSON."
                })
                continue
            # 仍空 → 占位避免下游崩 (writer 会从 raw upstream 兜底)
            raw.setdefault("thesis_bullets", ["[V0] (LLM 未能给出明确看多论点, 上游信号偏空, 建议参考 bear 视角)"])
            raw.setdefault("v_anchors", ["V0"])
        return raw
