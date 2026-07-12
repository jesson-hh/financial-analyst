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


def _fmt_num(v: Any, suffix: str = "") -> str:
    """数字锚渲染:真实值→'123.45亿' 一类字符串;缺失(None/非数字)→诚实'证据未及'
    (绝不留 None/编造)。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:.2f}{suffix}"
    return "证据未及"


def f4_clause(quote: Dict[str, Any]) -> str:
    """F4(game-capital)判定文案——根治幻觉市值:旧版硬编码"Sub-200亿"逼模型自己
    编造市值数字来凑条件;现在从 quote-fetcher 真实 mv_yi/pe/ret_60d 判定三条件是否
    同时成立,不满足则明确禁止援引 F4,满足才允许,数字全部真实可溯源。"""
    quote = quote or {}
    mv = quote.get("mv_yi")
    pe = quote.get("pe")
    ret60 = quote.get("ret_60d")
    mv_txt = _fmt_num(mv, "亿")
    pe_txt = _fmt_num(pe)
    ret60_txt = f"{ret60 * 100:.1f}%" if isinstance(ret60, (int, float)) and not isinstance(ret60, bool) else "证据未及"

    condition_met = (
        isinstance(mv, (int, float)) and not isinstance(mv, bool) and mv < 200
        and isinstance(pe, (int, float)) and not isinstance(pe, bool) and pe > 100
        and isinstance(ret60, (int, float)) and not isinstance(ret60, bool) and ret60 > 0.50
    )
    verdict = (
        "三条件(mv<200亿 且 PE>100 且 ret60>50%)同时成立,可援引 F4(game-capital)"
        if condition_met else
        "不同时满足 mv<200亿 且 PE>100 且 ret60>50% 三条件,不得援引 F4 模式"
    )
    return f"game-capital 模式——当前市值 {mv_txt}, PE {pe_txt}, ret60 {ret60_txt}:{verdict}。"


SYSTEM_PROMPT = """You are a buy-side Bear Advocate for A-share single-stock research. You receive same upstream as bull.

Build the strongest bear case in 3-5 bullets. Anchor each to F1-F14 failure modes from memory:
- F1: factor signal in systemic-uptrend regime is unreliable
- F2: game-capital tickers — quant models structurally fail
- F3: Alpha158 failure mode (overfitting on lookback)
- F4: {F4_CLAUSE}
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
        # 数字锚:quote-fetcher 的市值/PE/60日涨幅拼进 upstream(quote-fetcher 是全体
        # tier2 硬依赖,到 bear-advocate 这一波必然已 done——见 yaml input_keys 注释)。
        quote = inputs.get("quote-fetcher") or {}
        quote_summary = {
            "price": quote.get("price"), "mv_yi": quote.get("mv_yi"),
            "pe": quote.get("pe"), "ret_60d": quote.get("ret_60d"),
        }
        upstream = json.dumps({
            "fundamental": inputs.get("fundamental-analyst", {}),
            "technical": inputs.get("technical-analyst", {}),
            "whale": inputs.get("whale-analyst", {}),
            "quant": inputs.get("quant-analyst", {}),
            "quote_summary": quote_summary,
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

        # F4 幻觉市值断根:静态模板改成占位符 {F4_CLAUSE},这里用 quote-fetcher 真实
        # mv_yi/pe/ret_60d 判定后原样替换——不满足三条件时明确禁止援引 F4。
        sys_prompt = SYSTEM_PROMPT.replace("{F4_CLAUSE}", f4_clause(quote)) + "\n\n# Memory\n" + memory_text

        # 平台证据(evidence-loader):bear 消费 sentiment/fundflow/board_eco(数值面佐证)。
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
