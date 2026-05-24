"""Macro impact analyzer — 融合海外 + A 股 scanner, 判读传导效率 (LLM).

最终的 macro-impact agent. 给你:
1. overseas-market-scanner: 海外价格 + risk_tone
2. global-news-aggregator: 海外格局 narrative + impacts
3. market-scanner (optional): 今日 A 股 异动 + 沪深300 涨跌

任务: 判读 "A 股今日跟随 / 背离海外的程度" + "明天传导预期" + "可执行 actionable signals".

写一段总结 + 列 3-5 个明天可操作信号.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class ActionableSignal(BaseModel):
    signal: str                                          # "明日大概率高开" / "防御板块抢筹" etc.
    confidence: Literal["high", "medium", "low"] = "medium"
    affected_codes_or_sectors: List[str] = Field(default_factory=list)


class MacroImpactOutput(BaseModel):
    as_of: str
    headline: str = ""                                   # 一句话总结
    follow_through_judgment: str = ""                    # A 股 vs 海外 follow-through 判读
    actionable_signals: List[ActionableSignal] = Field(default_factory=list)
    output_md_path: Optional[str] = None                 # 落盘 markdown


SYSTEM_PROMPT = """你是 A 股宏观影响分析助手. 给你今日海外格局 + 今日 A 股异动数据, 判读海外 → A 股的传导效率 + 给明天可执行信号.

任务:
1. **headline**: 一句话总结今日海外/A 股关系 (例如 "美股 +1% A 股仅 +0.3% → 韧性偏弱, 关注明日补跟随")
2. **follow_through_judgment**: 100 字内详细判读. 看 A 股是否同步跟随海外, 哪些板块 leading/lagging, follow-through 强度
3. **actionable_signals**: 3-5 个明天可执行信号. 每个含 signal text + confidence + 受影响 codes/sectors

判读规则:
- 美股大涨 + A 股弱 → A 股偏弱信号, 防御占优
- 美股大跌 + A 股韧性 → A 股有支撑, 可能短期机会
- VIX > 25 + 国际地缘升温 → A 股大概率低开, 减仓信号
- 商品涨 + A 股资源股弱 → 反向, 等补涨
- risk_on + A 股科技弱 → 板块切换, 关注新主线

actionable_signals 例子:
- "明日大盘大概率低开 0.3-0.8%, VIX 22 + 美股小跌"
- "半导体短期补跌, 美股纳指 -0.5% 但 A 股半导体仍 +1%"
- "防御板块 (红利/电力) 中线机会, 海外避险情绪上升"

返回 JSON:
{
  "headline": "...",
  "follow_through_judgment": "...",
  "actionable_signals": [{"signal": "...", "confidence": "high", "affected_codes_or_sectors": ["半导体", "SH600519"]}, ...]
}
"""


class MacroImpactAnalyzer(SubAgent[MacroImpactOutput]):
    """融合海外 + A 股, 写宏观影响报告. 1 个 LLM call. 落盘 markdown."""

    NAME = "macro-impact-analyzer"
    OUTPUT_SCHEMA = MacroImpactOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        as_of = inputs.get("asof_date") or datetime.today().strftime("%Y-%m-%d")
        overseas = inputs.get("overseas-market-scanner", {}) or {}
        news = inputs.get("global-news-aggregator", {}) or {}
        scanner = inputs.get("market-scanner", {}) or {}  # 可选
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        ctx = {
            "as_of": as_of,
            "overseas_risk_tone": overseas.get("risk_tone"),
            "overseas_detail": overseas.get("risk_tone_detail"),
            "vix": overseas.get("vix_level"),
            "us_indices": {
                k: {"price": v.get("price"), "chg%": v.get("changePercent")}
                for k, v in (overseas.get("us_overnight") or {}).items()
            },
            "hk_indices": {
                k: {"price": v.get("price"), "chg%": v.get("changePercent")}
                for k, v in (overseas.get("hk_market") or {}).items()
            },
            "global_narrative": news.get("overall_narrative", ""),
            "key_channels": news.get("key_channels", []),
            "global_impacts": [
                {"channel": i.get("channel"), "dir": i.get("direction_for_a_shares"),
                 "sectors": i.get("affected_sectors"), "summary": i.get("summary")}
                for i in (news.get("impacts") or [])[:5]
            ],
            "a_shares_today": {
                "csi300_pct": (scanner.get("index_snapshot") or {}).get("SH000300_pct"),
                "n_flagged": scanner.get("n_flagged"),
                "top_gainers_top5": [r.get("name") + " " + str(round(r.get("pct_chg", 0), 1)) + "%"
                                     for r in (scanner.get("top_gainers") or [])[:5]],
                "top_losers_top5": [r.get("name") + " " + str(round(r.get("pct_chg", 0), 1)) + "%"
                                    for r in (scanner.get("top_losers") or [])[:5]],
            } if scanner else None,
        }

        client = LLMClient.for_agent(self.NAME)
        messages = [
            {"role": "system",
             "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user",
             "content": f"as_of: {as_of}\n\n输入:\n{json.dumps(ctx, ensure_ascii=False, indent=2)[:6000]}\n\n返回 JSON."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
        except Exception:
            parsed = {}

        signals_raw = parsed.get("actionable_signals") or []
        signals: List[ActionableSignal] = []
        for s in signals_raw:
            try:
                signals.append(ActionableSignal(**s))
            except Exception:
                pass

        # Write markdown report
        md_path = out_dir / f"overseas_radar_{as_of}.md"
        md_lines = [
            f"# 海外格局雷达 · {as_of}",
            "",
            f"**Headline**: {parsed.get('headline', '')}",
            "",
            "## 海外快照",
            f"- 风险偏好: **{overseas.get('risk_tone', '?')}** ({overseas.get('risk_tone_detail', '')})",
            f"- VIX: {overseas.get('vix_level', '?')}",
            "",
            "## 全球格局 (global-news)",
            news.get("overall_narrative", "(无)"),
            "",
            "## A 股 vs 海外 follow-through",
            parsed.get("follow_through_judgment", "(无)"),
            "",
            "## 明日可执行信号",
        ]
        for s in signals:
            tag = {"high": "🟢", "medium": "🟡", "low": "⚪"}.get(s.confidence, "·")
            scope = ", ".join(s.affected_codes_or_sectors[:5]) if s.affected_codes_or_sectors else ""
            md_lines.append(f"- {tag} **{s.signal}** {f'_({scope})_' if scope else ''}")
        md_lines.append("")
        md_lines.append(f"<sub>generated by macro-impact-analyzer · {datetime.now().isoformat(timespec='seconds')}</sub>")
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return MacroImpactOutput(
            as_of=as_of,
            headline=str(parsed.get("headline", ""))[:200],
            follow_through_judgment=str(parsed.get("follow_through_judgment", ""))[:600],
            actionable_signals=signals,
            output_md_path=str(md_path),
        ).model_dump()
