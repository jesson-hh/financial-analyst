from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class WhaleOutput(BaseModel):
    whale_score: int = 0  # -2..+2
    sentiment_label: str = "neutral"  # super_distr | distr | tail_surge | bounce | neutral
    vol_regime_label: str = "neutral"
    board_total_score: Optional[int] = None  # -7..+8 if limit-up day exists
    alerts: List[str] = []
    bull_points: List[str] = []
    bear_points: List[str] = []


SYSTEM_PROMPT = """You are a whale-behavior + sentiment analyst for A-shares. You interpret:
- whale signals (OBV trend, VR judgment, MFI, shadow ratio, chip concentration, whale_judge)
- board score (v4+v5) — limit-up board quality (-7..+8)
- vol_regime (R7-R20) — super_distr / distr / tail_surge / bounce / neutral
- 雪球散户讨论 (when supplied below) — retail sentiment proxy

Apply the 14 S/SS sentiment signals from memory:
- super_distr (SS, monthly 11/12 hit): fwd_5d -4.20pp — alert "super distribution"
- distr (S, monthly 13/13): -1.42pp — alert "distribution"
- tail_surge: -1.40pp — alert "tail-surge"
- bounce (S6/S7): +0.85-0.94pp — alert "bounce setup"
- seal_at_close=False on limit-up: extreme negative signal — alert "broken board"

whale_score aggregation:
- accumulating + bounce: +2
- neutral whale + neutral regime: 0
- distributing + distr/tail_surge/super_distr: -2

# Required JSON output schema (return ONLY these keys, no others, no commentary):
{
  "whale_score": int,                  // -2..+2 from the aggregation rule above
  "sentiment_label": str,              // one of: super_distr | distr | tail_surge | bounce | neutral
  "vol_regime_label": str,             // same enum as sentiment_label, mirrors upstream vol_regime
  "board_total_score": int | null,     // -7..+8 if a limit-up board is present, else null
  "alerts": [str, ...],                // short flags: "super distribution", "tail-surge", "broken board", "bounce setup", "OBV-VR divergence", "retail FOMO", "retail capitulation", etc.
  "bull_points": [str, ...],           // 1-4 specific bullish observations. If 雪球 retail sentiment is supplied below, AT LEAST ONE point must reference it concretely (e.g. likes/comments counts, top-post stance).
  "bear_points": [str, ...]            // 1-4 specific bearish observations. Same rule: cite retail divergence if present.
}

Hard rules:
- Use the EXACT keys above. Do not invent fields like ticker / analyst_note / whale_signals_detail / playbook_v_anchors.
- bull_points / bear_points are Chinese full sentences, each grounded in a concrete number or upstream signal.
- If 雪球 social posts are supplied, you MUST surface their signal in bull/bear or alerts. Empty social block is fine — just say so once in alerts ("retail sentiment unavailable").
- No free text outside the JSON object."""


class WhaleAnalyst(SubAgent[WhaleOutput]):
    NAME = "whale-analyst"
    OUTPUT_SCHEMA = WhaleOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        quote = inputs.get("quote-fetcher", {})
        factors = inputs.get("factor-computer", {})
        code = quote.get("code", "") if isinstance(quote, dict) else ""

        upstream = json.dumps({
            "quote": quote,
            "whale_signals": factors.get("whale_signals", {}) if isinstance(factors, dict) else {},
            "board_score": factors.get("board_score", {}) if isinstance(factors, dict) else {},
            "vol_regime": factors.get("vol_regime", {}) if isinstance(factors, dict) else {},
        }, default=str, ensure_ascii=False)

        # v1.2: augment with social posts from NewsDB (retail sentiment)
        # since_days=30 because xueqiu activity for any single stock is often
        # bursty — 7 days frequently has zero posts even for liquid names like
        # 600519. 30 captures the latest discussion wave without bleeding into
        # ancient sentiment.
        social_block = ""
        if code:
            try:
                from financial_analyst.data.news_db import NewsDB
                db = NewsDB()
                posts = db.query_social_posts(code=code, since_days=30, limit=20)
                db.close()
                if posts:
                    lines = [f"## 雪球散户讨论 (近 30 日, {len(posts)} 条):\n"]
                    # Aggregate stats
                    total_likes = sum(p.get("likes", 0) or 0 for p in posts)
                    total_comments = sum(p.get("comments_count", 0) or 0 for p in posts)
                    lines.append(f"- 总点赞: {total_likes}, 总评论: {total_comments}\n")
                    # Sample posts (top by engagement)
                    top_posts = sorted(
                        posts,
                        key=lambda p: (p.get("likes", 0) or 0) + (p.get("comments_count", 0) or 0),
                        reverse=True,
                    )[:5]
                    for p in top_posts:
                        likes = p.get("likes", 0)
                        content = (p.get("content", "") or "")[:200]
                        lines.append(f"- [{likes}赞] {content}")
                    social_block = "\n\n# 散户情绪 (NewsDB.social_posts)\n" + "\n".join(lines)
            except Exception:
                pass

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all() + social_block},
            {"role": "user", "content": f"Upstream:\n{upstream}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
