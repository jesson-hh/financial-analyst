"""Morning brief writer — synthesize scanner output into a markdown brief."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class MorningBriefOutput(BaseModel):
    output_md_path: str
    output_json_path: str
    headline: str = ""
    watchlist_today: List[str] = []   # codes worth a deep-dive
    hot_themes: List[str] = []        # market themes detected


SYSTEM_PROMPT = """You are the A-share morning brief writer.

You receive:
- as_of date
- n_scanned / n_flagged (scope)
- top_gainers / top_losers (20 each, with code/pct_chg/mv_tier/flagged_by)
- volume_anomalies (top 20 by vol_ratio)
- index_snapshot (SH000300 etc)

Produce a markdown brief WITHOUT fabricating fundamental data — you only have prices/volume. Write:

1. **Headline** — one sentence: today's market regime + 2-3 notable themes
2. **大盘速览** — index move + breadth (n_flagged / n_scanned ratio)
3. **领涨 top 10** — table: code | pct | mv_tier | flagged_by
4. **领跌 top 10** — same table
5. **量能异动 top 10** — vol_ratio anomalies
6. **主题猜想** — based on which mv_tiers led / which sector codes (you can infer 'AI/半导体' from SH/SZ + code patterns ONLY if pattern is clear, otherwise say "需进一步核实")
7. **今日关注 watchlist** — 3-5 codes worth a full deep-dive (suggest `financial-analyst report <code>`)

DO NOT make up news / catalysts you don't see in the data. If you don't know why something moved, say so.

Return JSON:
{
  "markdown_body": "<full markdown>",
  "headline": "<one sentence>",
  "watchlist_today": ["<codes>"],
  "hot_themes": ["<themes>"],
  "summary_json": {<key metrics>}
}
"""


class MorningBriefWriter(SubAgent[MorningBriefOutput]):
    NAME = "morning-brief-writer"
    OUTPUT_SCHEMA = MorningBriefOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        scanner = inputs.get("market-scanner", {})
        as_of = scanner.get("as_of", "unknown")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        client = LLMClient.for_agent(self.NAME)
        upstream_json = json.dumps(scanner, default=str, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"As of: {as_of}\n\nScanner output:\n{upstream_json[:12000]}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.2,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        md_path = out_dir / f"morning_brief_{as_of}.md"
        json_path = out_dir / f"morning_brief_{as_of}.json"
        md_path.write_text(parsed.get("markdown_body", f"# Morning Brief {as_of}\n(empty)"), encoding="utf-8")
        json_path.write_text(
            json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "headline": str(parsed.get("headline", "")),
            "watchlist_today": list(parsed.get("watchlist_today", [])),
            "hot_themes": list(parsed.get("hot_themes", [])),
        }
