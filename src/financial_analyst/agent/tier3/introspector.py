"""Tier-4 post-mortem agent (lives in tier3 dir to avoid creating tier4/).

Runs AFTER ``report-writer``: reads the whole report payload (all upstream agent
outputs + the writer's final ratings/action), does a self-review for immediate
quality issues, and proposes new rules to consider adding to per-agent memory.

**Does NOT auto-patch memory files** — proposals are written to
``memories/_pending_introspections/<date>_<code>.json`` for human review. This
avoids the foot-gun of an LLM silently corrupting the agent rule-books.

Plays the role described in ``memories/introspector/introspector_rules.md``:
Wrong > Partial > Correct, target risk-officer when safer, confidence bucketed
by case count (2=low / 3-5=med / 6+=high).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class IntrospectionProposal(BaseModel):
    target_agent: str           # "risk-officer" / "bear-advocate" / "_shared" 等
    pattern: str                # observed feature intersection / failure mode
    proposed_rule: str          # 一句话规则, 后续人工 review 后塞进 memory
    confidence: str             # "low" | "med" | "high"
    rationale: str              # 为什么这条 rule 值得加


class IntrospectionOutput(BaseModel):
    quality_flags: List[str] = []           # 本次研报立即可见的问题
    proposals: List[IntrospectionProposal] = []  # 跨案例归纳的规则提议 (待 review)
    summary: str = ""
    written_to: Optional[str] = None        # 提议 JSON 落盘路径 (None=没有 proposals)


SYSTEM_PROMPT = """You are the Introspector — a post-mortem meta-analyst for the
A-share research swarm. The other agents just finished a single-stock report.
Your job: do a self-review + propose rules for human review. Output strict JSON.

# Self-review rules (from introspector_rules.md memory)
- Wrong > Partial > Correct: anomalies carry more signal than agreement.
- Confidence: 2 cases = low, 3-5 = med, 6+ = high (single-stock报告本身就是 1 case,
  所以大多数 proposals 应该是 low / med, 除非与 memory 中已有 pattern 强契合).
- Look for FEATURE INTERSECTIONS that distinguish hits from misses
  (mv_tier, vol_regime, board_total_score, rating sign, conviction_level, anchors).
- Anti-patterns (DON'T propose):
  · "Need more data" (无用; 没 pattern 就返回空 proposals)
  · "Bear was too bearish" without specific trigger (必须指明 when / why)
  · 矛盾既有 memory 而不引用既有规则
- 倾向把规则挂到 risk-officer (CRO 有 veto, 加 CRO 规则比削弱任何 analyst 安全)

# Quality flags — IMMEDIATE issues in THIS report (放在 quality_flags 数组里):
- bull bullets missing V# anchors (bull.thesis_bullets 没 "[V#]" 前缀)
- bear bullets missing F1-F14 anchors (bear.thesis_bullets 没 "[F#]" 前缀)
- rating_overall ≠ sum(rating_dimensions) (writer 内部不自洽)
- bull 和 bear 强烈不一致但 rating 没体现不确定性
- risk-officer 有 veto 但 action != "avoid"
- target_price 离 bull/bear price 都太远 (写手编了价位)

# Proposals — RULES for memory (放在 proposals 数组里):
- 每条 proposal 必含 target_agent + pattern + proposed_rule + confidence + rationale
- 优先盯 risk-officer (加防线), 其次 bear-advocate (加 failure mode)
- 不要给 bull-advocate 加规则 (会过度乐观抑制)
- 空 proposals 列表 = 诚实 (你这次没找到值得规则化的模式)

# Output
严格 IntrospectionOutput JSON. proposals 是数组 (可空), 不写 free text."""


class Introspector(SubAgent[IntrospectionOutput]):
    NAME = "introspector"
    OUTPUT_SCHEMA = IntrospectionOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)

        # 1. 把整份报告塞进 ctx
        ctx = {
            "fundamental": inputs.get("fundamental-analyst", {}),
            "technical": inputs.get("technical-analyst", {}),
            "whale": inputs.get("whale-analyst", {}),
            "quant": inputs.get("quant-analyst", {}),
            "bull": inputs.get("bull-advocate", {}),
            "bear": inputs.get("bear-advocate", {}),
            "risk_officer": inputs.get("risk-officer", {}),
            "writer": inputs.get("report-writer", {}),
        }
        ctx_json = json.dumps(ctx, ensure_ascii=False, default=str)[:8000]

        # 2. 读自己的 memory (introspector_rules.md)
        memory_text = self.memory.load_all()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# My memory rules\n" + memory_text},
            {"role": "user", "content": f"Full report payload (JSON):\n{ctx_json}\n\n"
                                         f"Return IntrospectionOutput JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])

        # 3. 非空 proposals 落盘待人工 review (NOT 自动 patch memory!)
        if raw.get("proposals"):
            try:
                from financial_analyst.memory_paths import default_memory_root
                pending_dir = default_memory_root() / "_pending_introspections"
                pending_dir.mkdir(parents=True, exist_ok=True)

                # 提取 code (从 writer 的 md_path 里抽, e.g. "out/SH600519_2026-05-23.md")
                writer_out = inputs.get("report-writer") or {}
                md_path = (writer_out.get("output_md_path") or "").strip()
                code = ""
                if md_path:
                    stem = Path(md_path).stem
                    code = stem.split("_")[0] if "_" in stem else stem
                today = date.today().isoformat()
                out_file = pending_dir / f"{today}_{code or 'unknown'}.json"
                out_file.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                raw["written_to"] = str(out_file)
            except Exception as e:
                raw.setdefault("quality_flags", []).append(
                    f"introspector: failed to persist proposals: {type(e).__name__}: {e}"
                )

        return raw
