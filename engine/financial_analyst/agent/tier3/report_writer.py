from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, model_validator
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient

log = logging.getLogger(__name__)

# 匹配两种时间线条目的前导日期:① 自动回写行 `- 2026-06-10 …` ② 导入的表格行 `| 2026-06-10 | …`。
# 表头 `| 日期 | …`、分隔 `|---|`、快照行 `- **基本面**…` 无日期 → 不匹配 → 保留(结构/非时点)。
_TL_LINE_DATE = re.compile(r"^[-*|]\s*(\d{4}-\d{2}-\d{2})")


def _timeline_asof(text: str, asof: str) -> str:
    """PIT 过滤时间线。asof 非 YYYY-MM-DD(如 'UNKNOWN'/live)→ 不过滤,原样返回。

    - **总是**丢弃前导日期晚于 asof 的条目行(回测不得看到未来回写)。
    - 当 asof 是**历史日期**(< 今天 = 真回测)时,还要丢掉导入时间线里无条件含「最新」
      信息的散文(intro blockquote / 「## 最新快照」段 / footer / 笔记)—— 它们没有逐行
      日期可挡,会泄露未来。只保留:日期 ≤ asof 的表格/回写行 + 表格结构行(`|`)+ 段标题
      (但「最新快照」标题也丢)。asof = 今天(live)时这些散文即当前,全部保留。"""
    if not text or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(asof or "")):
        return text
    from datetime import date as _date
    try:
        historical = asof < _date.today().isoformat()
    except Exception:
        historical = False
    out = []
    for line in text.splitlines():
        s = line.strip()
        m = _TL_LINE_DATE.match(s)
        if m:
            if m.group(1) > asof:
                continue                       # 未来日期行 → 丢(无论 live/历史)
            out.append(line)                   # 历史日期行 → 留
            continue
        if not historical:
            out.append(line)                   # live:非日期行(快照=当前)全留
            continue
        # 历史 as-of:只留表格结构 / 段标题(快照标题除外),丢一切可能含「最新」的散文
        if "最新快照" in s:
            continue
        if s.startswith("|") or re.match(r"#{1,6}\s", s):
            out.append(line)
    return "\n".join(out)


def render_broker_section(broker: dict) -> str:
    """确定性渲染券商评级与目标价段(源 F10 研究报告)。数字逐字,缺则诚实"无"。

    LLM 不碰这些数字 —— 评级机构 / 评级 / 报告日价 / 目标价全来自 f10_corpus
    的确定性正则抽取,此处仅按行拼接。`ratings` 空 → 写"无",绝不编造。
    """
    ratings = (broker or {}).get("ratings") or []
    if not ratings:
        return "券商评级与目标价:无(F10 无券商评级记录)"

    def _px(v) -> str:
        # A 股报价定 2 位小数:6.80 渲染成 "6.80"(保留末位 0,不让 float 吃掉),逐字忠实。
        return f"{float(v):.2f}"

    lines = ["券商评级与目标价(确定性,源 F10 研究报告):"]
    for r in ratings[:8]:
        seg = f"- {r['date']} {r.get('org', '')} {r.get('rating') or '-'}"
        if r.get("report_price") is not None:
            seg += f",报告日价 {_px(r['report_price'])}"
        if r.get("target_price") is not None:
            seg += f",目标价 {_px(r['target_price'])}"
        lines.append(seg)
    return "\n".join(lines)


def render_ownership_section(facts: dict) -> str:
    """确定性渲染股东与主力段(源 F10 股东研究 / 主力追踪)。

    控股股东 / 实控人 / A股户数 / top3 流通股东 + 机构持仓比例,数字逐字照搬
    f10_corpus 的确定性抽取结果,LLM 不碰。holders 与 main_capital 均空 → "股东与主力:无"。
    """
    facts = facts or {}
    holders = facts.get("holders") or {}
    main_capital = facts.get("main_capital") or {}
    if not holders and not main_capital:
        return "股东与主力:无"

    lines = ["股东与主力(确定性,源 F10 股东研究 / 主力追踪):"]

    if holders:
        rd = holders.get("report_date")
        if rd:
            lines.append(f"- 股东结构(截至 {rd}):")
        else:
            lines.append("- 股东结构:")
        if holders.get("controlling_holder"):
            lines.append(f"  控股股东 {holders['controlling_holder']}")
        if holders.get("actual_controller"):
            lines.append(f"  实际控制人 {holders['actual_controller']}")
        ah = holders.get("a_share_holders")
        if ah is not None:
            # 86.6667万 户:以"万户"口径渲染,逐字忠实(866667 → 86.6667万户)
            lines.append(f"  A股户数 {ah / 1e4:g}万户")
        top = holders.get("top_holders") or []
        for h in top[:3]:
            seg = f"  - {h.get('name', '')}"
            if h.get("pct") is not None:
                seg += f",占流通股 {h['pct']}%"
            if h.get("nature"):
                seg += f"({h['nature']})"
            if h.get("change"):
                seg += f",{h['change']}"
            lines.append(seg)

    if main_capital:
        rp = main_capital.get("report_period")
        head = f"- 机构持仓(报告期 {rp}):" if rp else "- 机构持仓:"
        bits = []
        if main_capital.get("inst_count") is not None:
            bits.append(f"机构数量 {main_capital['inst_count']}")
        if main_capital.get("inst_holding_pct") is not None:
            bits.append(f"累计持仓比例 {main_capital['inst_holding_pct']}%")
        if main_capital.get("fund_holding_pct") is not None:
            bits.append(f"基金持仓比例 {main_capital['fund_holding_pct']}%")
        lines.append(head + ("、".join(bits) if bits else "无"))
        trend = main_capital.get("holder_count_trend") or []
        if trend:
            t0 = trend[0]
            cnt = t0.get("count")
            if cnt is not None:
                seg = f"  股东户数趋势(最新 {t0.get('date', '')}):{cnt / 1e4:g}万户"
                if t0.get("change_pct") is not None:
                    seg += f"(较上期 {t0['change_pct']}%)"
                lines.append(seg)

    return "\n".join(lines)


class ReportOutput(BaseModel):
    """Writer 最终输出.

    Pydantic 约束 (hard guard, 违反 → agent ok=False):
      - rating_overall ∈ [-10, 10]
      - position_pct ∈ [0, 0.10]
      - target_price > 0, stop_loss >= 0
      - action ∈ {buy, hold, sell, avoid, accumulate}

    Cross-field 一致性约束 (model_validator, 违反 → agent ok=False):
      - action='avoid' → position_pct == 0
      - position_pct == 0 → action 不能是 'buy'

    更软性的不一致 (rating_overall ≠ sum(dims), action vs target_price 方向冲突)
    在 ``_execute`` 里 auto-fix + sanity_notes 记录, 不让整个报告挂掉.
    """
    output_md_path: str
    output_json_path: str
    rating_overall: int = Field(..., ge=-10, le=10)
    rating_dimensions: Dict[str, int] = {}
    action: str
    target_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., ge=0)
    position_pct: float = Field(..., ge=0.0, le=0.10)

    @model_validator(mode="after")
    def _check_cross_field(self):
        valid_actions = {"buy", "hold", "sell", "avoid", "accumulate"}
        if self.action not in valid_actions:
            raise ValueError(
                f"action={self.action!r} 不在合法集 {valid_actions}"
            )
        if self.action == "avoid" and self.position_pct > 0:
            raise ValueError(
                f"action='avoid' 但 position_pct={self.position_pct} > 0. "
                f"avoid 必须 0% 仓位 (CRO veto 已生效)."
            )
        if self.position_pct == 0.0 and self.action == "buy":
            raise ValueError(
                f"position_pct=0 但 action='buy'. 矛盾 — buy 必须有正仓位."
            )
        return self


SYSTEM_PROMPT = """You are the chief analyst writing the final research report for an A-share single stock.

Synthesize all upstream:
- quote-fetcher: price, valuation (含 F10 兜底 total_shares/bvps/roe/revenue), returns, MA
- fundamental-analyst: valuation_score, mv_tier
- technical-analyst: technical_score, MA/RSI/MACD states
- whale-analyst: whale_score, sentiment_label
- bull-advocate: thesis_bullets, target_price_high/base, v_anchors
- bear-advocate: thesis_bullets, target_price_low, downside_pct, f_anchors
- risk-officer: risk_score, veto_flags, position_sizing_advice
- news-sentiment: market_read(大盘消息面主线), market_tilt, stock_tilt(本票倾向), stock_read, evidence(真快讯引用), covered

新闻情绪研判(News sentiment): in markdown_body, add a dedicated section "## 新闻情绪研判 (消息面)" placed after 二、市场环境 and immediately BEFORE "## 三、基本面".
- ONLY when covered is true AND news-sentiment.evidence is non-empty: summarize market_read (大盘消息面) and the stock's stock_tilt/stock_read (本票消息面), quoting the evidence titles verbatim.
- When covered is false (or evidence is empty / market_read is null): write exactly "本票近期无相关消息面" — do NOT quote evidence (there is none to quote) and do NOT fabricate news. covered=false 与逐字引用证据是互斥的:无料即写"无",绝不引用空证据。

券商评级与目标价(Broker section): a deterministic, pre-rendered block titled
"券商评级与目标价" is supplied verbatim at the end of the user message under
`# 券商评级与目标价 (确定性 — 逐字照搬, 禁改数字)`. Surface it as a "## 券商评级与目标价"
section inside §三 基本面 (or §一 综合评级 target-price discussion). Copy the
机构 / 评级 / 报告日价 / 目标价 numbers EXACTLY as given — these are
deterministic F10 facts, NOT your estimates. If the block says "无", write 无;
do not invent any broker rating or target price.
- This section is QUALITATIVE context ONLY. DO NOT change any of the rating dimensions or rating_overall based on it. Use plain sentiment language (利好/利空/中性), no quant vocabulary (遵循全报告禁量化术语规则).

股东与主力(Ownership section): a deterministic, pre-rendered block titled
"股东与主力" is supplied verbatim at the end of the user message under
`# 股东与主力 (确定性 — 逐字照搬, 禁改数字)`. Surface it as a "## 股东与主力"
section inside §四 主力情绪 (or §三 基本面). Copy the 控股股东 / 实际控制人 /
A股户数 / top 流通股东 / 机构持仓比例 numbers EXACTLY as given — these are
deterministic F10 facts, NOT your estimates. If the block says "无", write 无;
do not invent any holder, shareholder count, or institutional holding figure.
- This section is QUALITATIVE context ONLY. DO NOT change any rating dimension or rating_overall based on it. No quant vocabulary.

Apply the multi-dimensional rating from memory (rating_system.md). The
factor-computer / model-predictor / quant-analyst nodes were retired
(2026-06-04); there is NO standalone 量化模型 dimension — do NOT invent a
"quant" score or claim a quant model ran. Score only these dimensions:
- 基本面 (fundamental): from fundamental-analyst.valuation_score
- 技术面 (technical): from technical-analyst.technical_score
- 主力情绪 (whale): from whale-analyst.whale_score
- 风险面 (risk): from risk-officer.risk_score (-2..0)

rating_overall = sum of these dims, range -10..+10.

action:
- veto active: "avoid"
- rating >= 6 + no veto: "buy"
- 2..5: "hold" (or "accumulate" if positive trend)
- < 0: "sell"

target_price = bull-advocate.target_price_base
stop_loss = bear-advocate.target_price_low (or 0.92 × current price if more conservative)
position_pct = parse risk-officer.position_sizing_advice (e.g. "3-5%" → 0.04)

Then provide a structured payload that the writer will save to BOTH .md and .json.

Output JSON only. The actual file writing happens in Python after LLM returns.

If a `# 上次研报时间线` block is supplied at the end of the user message, you
MUST surface it in the report's markdown_body — typically as a short
"上次回顾" section at the top of §一 综合评级, mentioning the prior
rating + date + the bullet that changed most. The user has years of
research on this stock; ignoring the timeline is a critical failure.

After your structured analysis, return JSON with these top-level fields:
- "markdown_body": full markdown report (use the template from memory)
- "rating_overall": int
- "rating_dimensions": {"fundamental": int, "technical": int, "whale": int, "risk": int}
- "action": str
- "target_price": float
- "stop_loss": float
- "position_pct": float
- "summary_json": dict (machine-readable structured payload)
"""


class ReportWriter(SubAgent[ReportOutput]):
    NAME = "report-writer"
    OUTPUT_SCHEMA = ReportOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
        code = inputs.get("code", "UNKNOWN")
        asof = inputs.get("asof_date", "UNKNOWN")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        upstream = {k: inputs.get(k, {}) for k in [
            "quote-fetcher",
            "fundamental-analyst", "technical-analyst", "whale-analyst",
            "bull-advocate", "bear-advocate", "risk-officer", "news-sentiment",
        ]}
        # 上次研报时间线 —— 直接读 StockTimelineLoader 的真盘(每份研报跑完由 TUI 回写到
        # ~/.financial-analyst/memories/stocks/<CODE>.md)。此前是从 inputs["factor-computer"]
        # 取,但该节点 2026-06-04 已下线 → 永远空 → 正文「上次回顾」从来写不出(死代码)。
        # 按 asof 做 PIT 过滤,回测/历史 as-of 研报不得看到未来的回写条目。
        try:
            from financial_analyst.data.loaders.stock_timeline import StockTimelineLoader
            timeline = _timeline_asof(StockTimelineLoader().load_tail(code) or "", asof).strip()
        except Exception:
            timeline = ""
        upstream_json = json.dumps(upstream, default=str, ensure_ascii=False, indent=2)
        timeline_block = f"\n\n# 上次研报时间线 (必读 — 用户多年研究, 写入 markdown_body §一 顶部)\n{timeline}" if timeline else ""

        # 券商评级与目标价(送 C 档):确定性 F10 研究报告抽取,逐字渲染喂 LLM,数字不经 LLM。
        # asof 非真实日期(live / 'UNKNOWN')→ 传 None 取最新;真实 YYYY-MM-DD → PIT 口径。
        pit_asof = asof if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(asof or "")) else None
        try:
            from financial_analyst.data import f10_corpus
            f10_facts = f10_corpus.load_facts(code, pit_asof).to_dict()
        except Exception:
            f10_facts = {}
        broker_section = render_broker_section(f10_facts.get("broker", {}))
        broker_block = (
            "\n\n# 券商评级与目标价 (确定性 — 逐字照搬, 禁改数字)\n" + broker_section
        )
        ownership_section = render_ownership_section(f10_facts)
        ownership_block = (
            "\n\n# 股东与主力 (确定性 — 逐字照搬, 禁改数字)\n" + ownership_section
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Code: {code}\nAs-of: {asof}\n\nUpstream:\n{upstream_json}{timeline_block}{broker_block}{ownership_block}\n\nReturn JSON."},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.3,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        # Sanity-check the output before writing files — enforce internal consistency.
        # 多层 auto-fix: 1) range clamp 2) cross-field 3) introspector 反馈过的 pattern
        rating_overall = int(parsed.get("rating_overall", 0))
        rating_dimensions = parsed.get("rating_dimensions", {}) or {}
        position_pct = float(parsed.get("position_pct", 0.0))
        target_price = float(parsed.get("target_price", 0.0))
        stop_loss = float(parsed.get("stop_loss", 0.0))
        veto_flags_from_risk = inputs.get("risk-officer", {}).get("veto_flags", [])
        current_price = float(inputs.get("quote-fetcher", {}).get("price", 0.0) or 0.0)

        sanity_notes = []

        # Range clamps
        if not (-10 <= rating_overall <= 10):
            old = rating_overall
            rating_overall = max(-10, min(10, rating_overall))
            sanity_notes.append(f"rating_overall {old} → {rating_overall} (clamped to [-10, 10])")

        if not (0.0 <= position_pct <= 0.10):
            old = position_pct
            position_pct = max(0.0, min(0.10, position_pct))
            sanity_notes.append(f"position_pct {old:.3f} → {position_pct:.3f} (clamped to [0, 0.10])")

        # Auto-fix: rating_overall ≠ sum(dimensions) — 用 sum 覆盖 LLM 自己的 rating
        # (introspector quality_flag '~rating_overall (-2) ≠ sum (-4)' 反馈)
        if rating_dimensions:
            dim_sum = sum(int(v) for v in rating_dimensions.values())
            if abs(rating_overall - dim_sum) > 1:
                sanity_notes.append(
                    f"rating_overall {rating_overall} ≠ sum(dimensions) {dim_sum} "
                    f"(超 1 分容差) → 修正为 {dim_sum}: {rating_dimensions}"
                )
                rating_overall = dim_sum

        # CRO veto / 低评级 → 0 仓
        if veto_flags_from_risk:
            if position_pct > 0:
                sanity_notes.append(f"veto active ({veto_flags_from_risk}) — position_pct forced 0")
                position_pct = 0.0
        elif rating_overall <= 0:
            if position_pct > 0:
                sanity_notes.append(f"rating={rating_overall} <= 0 — position_pct forced 0")
                position_pct = 0.0

        # Derive action consistently with final position
        if position_pct == 0.0:
            action = "avoid" if rating_overall <= -3 else "sell" if rating_overall <= 0 else "hold"
        else:
            action = str(parsed.get("action", "hold"))
            if action not in {"buy", "hold", "sell", "avoid", "accumulate"}:
                sanity_notes.append(f"action {action!r} 不合法 → 'hold'")
                action = "hold"

        # Auto-fix: action='sell' 但 target_price > current_price 时, 降到 current * 0.95
        # (introspector quality_flag 'action_target_price_mismatch' 反馈)
        if action == "sell" and current_price > 0 and target_price > current_price:
            old = target_price
            target_price = current_price * 0.95
            sanity_notes.append(
                f"action='sell' 但 target_price {old:.2f} > current_price {current_price:.2f} "
                f"→ 修正为 {target_price:.2f} (current × 0.95)"
            )

        # stop_loss < 0 不合法 → 0
        if stop_loss < 0:
            sanity_notes.append(f"stop_loss {stop_loss} < 0 → 0")
            stop_loss = 0.0

        # target_price = 0 是 Pydantic gt=0 不接受的 — 给个默认
        if target_price <= 0:
            target_price = current_price if current_price > 0 else 0.01
            sanity_notes.append(f"target_price <= 0 → fallback to current_price {target_price}")

        if sanity_notes:
            log.info("report_writer sanity_notes for %s: %s", code, sanity_notes)
            markdown_body = parsed.get("markdown_body", "")
            markdown_body += (
                "\n\n---\n*Post-write sanity overrides:*\n"
                + "\n".join(f"- {n}" for n in sanity_notes)
                + "\n"
            )
            parsed["markdown_body"] = markdown_body

        md_path = out_dir / f"{code}_{asof}.md"
        json_path = out_dir / f"{code}_{asof}.json"
        md_path.write_text(parsed.get("markdown_body", f"# {code} Report\n(empty)"), encoding="utf-8")
        json_path.write_text(json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "rating_overall": rating_overall,
            "rating_dimensions": rating_dimensions,
            "action": action,
            "target_price": target_price,
            "stop_loss": stop_loss,
            "position_pct": position_pct,
        }
