from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, model_validator
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient

log = logging.getLogger(__name__)


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
- quote-fetcher: price, valuation, returns, MA
- factor-computer: factor scores, whale signals, board score, vol_regime
- model-predictor: per-model predictions + consensus
- fundamental-analyst: valuation_score, mv_tier
- technical-analyst: technical_score, MA/RSI/MACD states
- whale-analyst: whale_score, sentiment_label
- quant-analyst: quant_score, conviction_level
- bull-advocate: thesis_bullets, target_price_high/base, v_anchors
- bear-advocate: thesis_bullets, target_price_low, downside_pct, f_anchors
- risk-officer: risk_score, veto_flags, position_sizing_advice

Apply the five-dimensional rating from memory (rating_system.md):
- 基本面 (fundamental): from fundamental-analyst.valuation_score
- 技术面 (technical): from technical-analyst.technical_score
- 主力情绪 (whale): from whale-analyst.whale_score
- 量化模型 (quant): from quant-analyst.quant_score
- 风险面 (risk): from risk-officer.risk_score (-2..0)

rating_overall = sum of 5 dims, range -10..+10.

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
- "rating_dimensions": {"fundamental": int, "technical": int, "whale": int, "quant": int, "risk": int}
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
            "quote-fetcher", "factor-computer", "model-predictor",
            "fundamental-analyst", "technical-analyst", "whale-analyst", "quant-analyst",
            "bull-advocate", "bear-advocate", "risk-officer",
        ]}
        # Surface the stock_timeline as its own block so the markdown body
        # can cite the prior call directly (rather than inheriting it from
        # a buried JSON field).
        factor_full = inputs.get("factor-computer", {}) or {}
        timeline = (factor_full.get("stock_timeline") or "").strip()
        if timeline and "factor-computer" in upstream:
            upstream["factor-computer"] = {k: v for k, v in upstream["factor-computer"].items() if k != "stock_timeline"}
        upstream_json = json.dumps(upstream, default=str, ensure_ascii=False, indent=2)
        timeline_block = f"\n\n# 上次研报时间线 (必读 — 用户多年研究, 写入 markdown_body §一 顶部)\n{timeline}" if timeline else ""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": f"Code: {code}\nAs-of: {asof}\n\nUpstream:\n{upstream_json}{timeline_block}\n\nReturn JSON."},
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
