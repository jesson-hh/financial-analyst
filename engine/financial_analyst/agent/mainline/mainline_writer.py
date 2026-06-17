"""Mainline writer — synthesize MainlineOutput into a markdown brief.

LLM-driven. Writes to ./out/mainline_<date>.md.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class MainlineReportOutput(BaseModel):
    output_md_path: str
    output_json_path: str
    headline: str = ""              # one-line summary
    actionable_signals: list[str] = []   # what to do today


SYSTEM_PROMPT = """You are the market structure analyst. Synthesize a monthly mainline radar brief.

You receive:
- as_of date
- status_groups: dict of {mainline, revival, initiation, decay, cold, neutral} -> top 20 industries each
- just_become_mainline: industries that JUST switched initiation -> mainline (★ golden signal, fwd_60d +5.54pp 胜率 87%)
- just_become_decay: industries that switched mainline -> decay (NOT bearish — v1 decay is misnamed, actually a pullback candidate)
- alpha_summary: empirical fwd-return numbers per status

Produce a markdown report with these sections:
1. **Headline** — one sentence verdict
2. **Mainline 行业** (top 10) — leading sectors, cite ex_60d
3. **Initiation 行业** (top 10) — 启动期候选
4. **★ 金信号: init->mainline 切换** — if any industries appeared today, highlight + cite +5.54pp alpha
5. **Revival 候选** — 主线短期回调点 (V4 立讯模式)
6. **大龙反指标** — flag mainline industries with lu_max_mv_60d_mean >= 500亿 (don't chase)
7. **Cold 行业** — 真冷门, 回避

Return JSON:
{
  "markdown_body": "<full markdown report>",
  "headline": "<one sentence>",
  "actionable_signals": ["<list of 3-5 today's actions>"],
  "summary_json": {<key metrics>}
}
"""


class MainlineWriter(SubAgent[MainlineReportOutput]):
    NAME = "mainline-writer"
    OUTPUT_SCHEMA = MainlineReportOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        upstream = inputs.get("mainline-classifier", {})
        as_of = upstream.get("as_of", "unknown")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        client = LLMClient.for_agent(self.NAME)
        upstream_json = json.dumps(upstream, default=str, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"As of: {as_of}\n\nUpstream:\n{upstream_json[:12000]}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.2,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        md_path = out_dir / f"mainline_{as_of}.md"
        json_path = out_dir / f"mainline_{as_of}.json"
        md_path.write_text(parsed.get("markdown_body", f"# Mainline {as_of}\n(empty)"), encoding="utf-8")
        json_path.write_text(
            json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "headline": str(parsed.get("headline", "")),
            "actionable_signals": list(parsed.get("actionable_signals", [])),
        }
