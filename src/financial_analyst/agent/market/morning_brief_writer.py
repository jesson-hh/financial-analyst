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


SYSTEM_PROMPT = """You are the A-share morning brief writer (v1.9.7 — bilingual ZH brief).

You receive 4 upstream agent outputs:
- **market-scanner**: A 股异动数据 (top_gainers/losers/volume_anomalies + index_snapshot)
- **overseas-market-scanner** (新): 隔夜美股 + 港股 + VIX + risk_tone
- **catalyst-extractor** (新): 异动股的催化因素 + 利好/利空判读
- **sector-rotation-analyzer** (新): 今日板块 leaders/laggards + 轮动 signal

写一份 markdown brief, sections:

1. **Headline** — 一句话: 今日大盘 + 海外格局 + 2-3 个关键主题
2. **隔夜海外** — risk_tone + 美股 4 指数 + 港股 2 指数 + VIX. 一段话总结隔夜事件
3. **大盘速览** — 指数 + breadth (n_flagged / n_scanned)
4. **板块轮动** — 用 sector-rotation 输出: leaders top 3 + laggards top 3 + 一句话 rotation_signal
5. **领涨 top 10** — table: code | name | pct | mv_tier. **如果 catalyst-extractor 给了催化, 在备注列加上**
6. **领跌 top 10** — 同上
7. **量能异动 top 10** — vol_ratio anomalies
8. **今日关注 watchlist** — 3-5 codes worth deep-dive (建议 `fa report <code>`)

规则:
- catalyst-extractor 没说的事别编故事
- 海外联动判读用 overseas-market-scanner 的 risk_tone, 别自己 hallucinate Fed 决议时间
- 板块轮动用 sector-rotation-analyzer 的 leaders, 别自己分类

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
        scanner = inputs.get("market-scanner", {}) or {}
        # v1.9.7: 新加 3 个 upstream agent (overseas + catalyst + rotation).
        # 老的 morning-brief.yaml 只有 scanner, 兼容 — 缺哪个都接 {} 不挂.
        overseas = inputs.get("overseas-market-scanner", {}) or {}
        catalyst = inputs.get("catalyst-extractor", {}) or {}
        rotation = inputs.get("sector-rotation-analyzer", {}) or {}
        as_of = scanner.get("as_of") or overseas.get("as_of") or "unknown"
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        # 把 4 个 upstream 整合到一个 user prompt, 各自加 section 头
        bundle = {
            "as_of": as_of,
            "market_scanner": scanner,
            "overseas": overseas,
            "catalysts": catalyst.get("catalysts") or [],
            "sector_rotation": {
                "leaders": rotation.get("today_leaders") or [],
                "laggards": rotation.get("today_laggards") or [],
                "signal": rotation.get("rotation_signal", ""),
            },
        }
        client = LLMClient.for_agent(self.NAME)
        upstream_json = json.dumps(bundle, default=str, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"As of: {as_of}\n\n4 upstream agent outputs:\n{upstream_json[:14000]}\n\nReturn JSON."},
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
