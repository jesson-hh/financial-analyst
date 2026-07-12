from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class RiskOutput(BaseModel):
    risk_score: int = 0                # -2..0 (risk officer never positive — only constrains)
    blind_spots: List[str] = []        # things both Bull and Bear missed
    position_sizing_advice: str = "0%" # "0%" | "1-3%" | "3-5%" | "5-8%"
    veto_flags: List[str] = []         # if non-empty, position_pct should be 0
    conditional_approval: str = ""     # e.g. "OK if stop-loss at 1450; reduce if super_distr persists"
    hard_rule_triggers: List[str] = [] # rules from memory that fired


SYSTEM_PROMPT = """You are an independent Chief Risk Officer for an A-share research desk. You receive:
- bull-advocate: thesis_bullets, target_price_high/base, disproof_signals, v_anchors
- bear-advocate: thesis_bullets, valuation_concerns, target_price_low, downside_pct, f_anchors
- news-reader: events[], numbers[] (untrusted)
- f10-reader: recent_events, lhb_seats, event_classified.negative
- factor-computer: vol_regime, board_score, factor_scores

Apply HARD RULES from memory — these CANNOT be overridden by bull/bear opinion:

1. GAME-CAPITAL VETO: if quote shows mv_yi<200 AND pe>100 AND ret_60d>0.50:
   → veto_flags += ["game_capital_speculation"]; position_sizing_advice = "0%"

2. NEGATIVE EVENT VETO: if any event in f10.event_classified.negative has severity>=2:
   → veto_flags += ["recent_severe_negative_event"]; position_sizing_advice = "0%"

3. SUPER_DISTR REDUCTION: if factor-computer.vol_regime.regime_label == "super_distr":
   → veto_flags += ["super_distribution_active"]; position_sizing_advice <= "1-3%"

4. BROKEN BOARD: if factor-computer.board_score.detail.seal_at_close == False AND board_score.v5_score < 0:
   → veto_flags += ["broken_board"]; position_sizing_advice = "0%"

5. risk_score:
   - any veto active: -2
   - bear-advocate convincing + no veto: -1
   - bull > bear + no veto: 0 (CRO never positive)

Identify blind_spots — risks neither Bull nor Bear flagged.

If a `# 上次研报时间线` block is supplied at the end of the user message, it
contains the user's prior analyses on this stock — INCLUDING calls that
turned out wrong. Use it specifically to detect:
- repeating prior mistakes (same trigger as a wrong call last time)
- ignoring lessons explicitly noted in the timeline
- failing to update on changed fundamentals since prior call
If you spot any of these, add to `anti_signals` as
"timeline_lesson_ignored:<short reason>".

Output JSON only. No free text."""


class RiskOfficer(SubAgent[RiskOutput]):
    NAME = "risk-officer"
    OUTPUT_SCHEMA = RiskOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        factor_full = inputs.get("factor-computer", {}) or {}
        # Strip the heavy stock_timeline from the JSON dump — surface it
        # separately so the LLM can find it as a structured block.
        factor_no_tl = {k: v for k, v in factor_full.items() if k != "stock_timeline"}
        # 数字锚:quote-fetcher 的市值/PE/60日涨幅拼进 upstream(quote-fetcher 是全体
        # tier2 硬依赖,到 risk-officer 这一波必然已 done——见 yaml input_keys 注释)。
        quote = inputs.get("quote-fetcher") or {}
        quote_summary = {
            "price": quote.get("price"), "mv_yi": quote.get("mv_yi"),
            "pe": quote.get("pe"), "ret_60d": quote.get("ret_60d"),
        }
        upstream = json.dumps({
            "bull": inputs.get("bull-advocate", {}),
            "bear": inputs.get("bear-advocate", {}),
            "news": inputs.get("news-reader", {}),
            "f10": inputs.get("f10-reader", {}),
            "factor": factor_no_tl,
            "quote_summary": quote_summary,
        }, default=str, ensure_ascii=False)
        timeline = (factor_full.get("stock_timeline") or "").strip()
        timeline_block = f"\n\n# 上次研报时间线 (必读 — 含过去错判教训)\n{timeline}" if timeline else ""

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

        # 平台证据(evidence-loader):risk-officer 消费 sentiment/fundflow/board_eco(数值面佐证)。
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + memory_text
        ev = inputs.get("evidence-loader") or {}
        ev_secs = ev.get("sections") or {}
        ev_picked = {k: ev_secs.get(k) for k in ("sentiment", "fundflow", "board_eco") if ev_secs.get(k)}
        evidence_block = ""
        if ev_picked:
            evidence_block = (
                "\n\n# 平台证据 (确定性 — 引用须带出处, 数字禁编造)\n"
                + json.dumps(ev_picked, ensure_ascii=False)
            )
            sys_prompt += (
                "\n\n# 平台证据使用规则\n"
                "若提供【平台证据】块:引用其中数据须标出处(section 名+as_of);"
                "证据中没有的数字一律写「证据未及」,严禁编造。"
            )

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Upstream:\n{upstream}{timeline_block}{evidence_block}\n\nReturn JSON per schema."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.1,
        )
        return json.loads(response["choices"][0]["message"]["content"])
