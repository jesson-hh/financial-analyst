from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class TechnicalOutput(BaseModel):
    technical_score: int = 0  # -2..+2
    ma_state: str = "neutral"           # bullish | bearish | neutral
    rsi_state: str = "neutral"          # overbought | oversold | neutral
    macd_state: str = "neutral"         # bullish_cross | bearish_cross | neutral
    factor_consensus: str = "neutral"   # strong_long | weak_long | neutral | weak_short | strong_short
    breakout_signal: Optional[str] = None
    bull_points: List[str] = []
    bear_points: List[str] = []


SYSTEM_PROMPT = """You are a technical analyst for A-shares. You receive quote data (returns, MA, vol, volume_ratio) and factor scores (rev_20, mom_20, rsi_14, macd_bar, bb_pct_20, obv_slope_20, etc.).

Apply factor insights from memory:
- rev_20 = NEGATIVE of the past-20d return. positive rev_20 => the stock FELL over the past 20 days (oversold; the alpha expects an upward bounce). negative rev_20 => the stock ROSE. NEVER read rev_20 as a return/momentum number; to recover the actual 20d return, flip its sign.
- Factor IC/ICIR degrades with market cap; for large-caps the factor signal is weaker
- MA50/MA200 cross interpretation is in memory

Classify:
- ma_state: based on price relative to MA5/MA20/MA60
  * bullish: price above MA5, MA5 above MA20, MA20 above MA60
  * bearish: price below MA5, MA5 below MA20, MA20 below MA60
  * neutral: mixed signals
- rsi_state: <30 oversold, >70 overbought, otherwise neutral
- macd_state: macd_bar transitioning sign
  * bullish_cross: macd_bar recently turned positive (from negative)
  * bearish_cross: macd_bar recently turned negative (from positive)
  * neutral: no recent crossover
- factor_consensus: aggregate vote from rev_20, mom_20, ma_diff_20
  * strong_long: majority of factors signal long with high confidence
  * weak_long: slight edge toward long
  * neutral: no clear direction
  * weak_short: slight edge toward short
  * strong_short: majority of factors signal short with high confidence
- technical_score: integer from -2 to +2 summarizing overall technical outlook
- breakout_signal: optional string if a notable breakout pattern is detected (e.g. "volume_breakout", "ma_golden_cross"), else null
- bull_points: 2-4 specific bullish observations with numbers
- bear_points: 2-4 specific bearish observations with numbers

Output JSON only. No free text."""


class TechnicalAnalyst(SubAgent[TechnicalOutput]):
    NAME = "technical-analyst"
    OUTPUT_SCHEMA = TechnicalOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        quote = inputs.get("quote-fetcher", {})
        factors = inputs.get("factor-computer", {})
        # v1.9.7: 可选辅助 context — overseas + sector rotation (新 Tier-1 agent)
        overseas = inputs.get("overseas-market-scanner") or {}
        rotation = inputs.get("sector-rotation-analyzer") or {}
        bundle: Dict[str, Any] = {"quote": quote, "factors": factors}
        if overseas:
            bundle["overseas_context"] = {
                "risk_tone": overseas.get("risk_tone"),
                "risk_tone_detail": overseas.get("risk_tone_detail"),
                "vix": overseas.get("vix_level"),
            }
        if rotation:
            bundle["sector_rotation"] = {
                "today_leaders_top3": [{"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                                       for s in (rotation.get("today_leaders") or [])[:3]],
                "today_laggards_top3": [{"sector": s.get("sector"), "avg_pct": s.get("avg_pct_chg")}
                                        for s in (rotation.get("today_laggards") or [])[:3]],
                "signal": rotation.get("rotation_signal", ""),
            }
        upstream = json.dumps(bundle, default=str, ensure_ascii=False)
        sys_prompt = SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()
        if overseas or rotation:
            sys_prompt += (
                "\n\n# v1.9.7 辅助 context (optional)\n"
                "- overseas_context: 隔夜美股 + 港股 risk_tone (risk_on/off/mixed) + VIX. "
                "判读 momentum 时考虑 — risk_off 状态 + 该股贝塔高的话减分.\n"
                "- sector_rotation: 今日板块 leaders / laggards / rotation_signal. "
                "如果该股所属行业在 leaders → 顺势加分, 在 laggards → 警惕."
            )
        # 平台证据(evidence-loader):技术面消费 quote_live/fundflow/board_eco(量价/资金/打板生态)。
        ev = inputs.get("evidence-loader") or {}
        ev_secs = ev.get("sections") or {}
        ev_picked = {k: ev_secs.get(k) for k in ("quote_live", "fundflow", "board_eco") if ev_secs.get(k)}
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
            {"role": "user", "content": f"Upstream:\n{upstream}{evidence_block}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return json.loads(response["choices"][0]["message"]["content"])
