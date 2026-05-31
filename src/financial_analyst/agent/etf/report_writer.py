"""EtfReportWriter — tier-3 ETF final report writer.

THE sole file-writer in the ETF pipeline.  Gathers all upstream agents,
asks the LLM for a structured markdown + ratings payload, applies
Python sanity-fixes (same pattern as stock report_writer), then writes:
  <out_dir>/<code>_<asof_date>.md
  <out_dir>/<code>_<asof_date>.json

Five rating dimensions (each -2..+2, except risk which is -2..0):
  holdings   → from etf-holdings-analyst.holdings_score
  technical  → from etf-technical-analyst.technical_score
  flow       → from etf-flow-analyst.flow_score
  valuation  → from etf-valuation-analyst.valuation_score
  risk       → from etf-risk-officer.risk_score  (-2..0 only)

rating_overall = sum of 5 dims, range -10..+10
position_pct ∈ [0, 0.10]; forced to 0 when any veto_flag or rating_overall <= 0
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, model_validator
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient

log = logging.getLogger(__name__)


class EtfReportOutput(BaseModel):
    """Schema for EtfReportWriter output.

    Pydantic hard guards:
      rating_overall  ∈ [-10, 10]
      position_pct    ∈ [0, 0.10]
      target_price    > 0
      stop_loss       >= 0
      action          ∈ {buy, hold, sell, avoid, accumulate}

    Cross-field (model_validator):
      avoid → position_pct == 0
      position_pct == 0 → action != 'buy'
    """
    output_md_path: str
    output_json_path: str
    rating_overall: int = Field(..., ge=-10, le=10)
    rating_dimensions: Dict[str, int] = {}
    action: str
    target_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., ge=0)
    position_pct: float = Field(..., ge=0.0, le=0.10)
    markdown_body: str = ""
    summary_json: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_cross_field(self):
        valid_actions = {"buy", "hold", "sell", "avoid", "accumulate"}
        if self.action not in valid_actions:
            raise ValueError(f"action={self.action!r} not in {valid_actions}")
        if self.action == "avoid" and self.position_pct > 0:
            raise ValueError(
                f"action='avoid' but position_pct={self.position_pct} > 0; must be 0."
            )
        if self.position_pct == 0.0 and self.action == "buy":
            raise ValueError(
                f"position_pct=0 but action='buy'. Contradiction."
            )
        return self


SYSTEM_PROMPT = """You are the chief analyst writing the final ETF research report.

Synthesize all upstream:
- etf-quote-fetcher:      close, premium_discount_pct, returns
- etf-metrics-fetcher:    aum_cny, adv_cny, tracking_error, expense_ratio, holdings
- etf-holdings-analyst:   holdings_score, top_holding_weight, sector_concentration_hhi
- etf-technical-analyst:  technical_score, ma_state, rsi, trend_signals
- etf-flow-analyst:       flow_score, flow_regime, aum_trend, liquidity_note
- etf-valuation-analyst:  valuation_score, premium_discount_pct, pe_ratio, relative_value
- etf-bull-advocate:      thesis_bullets ([V#] anchored), target_price_base/high, disproof_signals
- etf-bear-advocate:      thesis_bullets ([F#] anchored), target_price_low, downside_pct
- etf-risk-officer:       risk_score (-2..0), veto_flags, position_sizing_advice

Apply the five-dimensional ETF rating (each -2..+2, risk capped at 0):
  holdings  (持仓构成)  : etf-holdings-analyst.holdings_score
  technical (技术面)    : etf-technical-analyst.technical_score
  flow      (资金流)    : etf-flow-analyst.flow_score
  valuation (估值/跟踪) : etf-valuation-analyst.valuation_score
  risk      (风控)      : etf-risk-officer.risk_score  ← always ≤ 0

rating_overall = sum of all 5 dimensions, range -10..+10

action guidance:
  - veto_flags non-empty → "avoid"
  - rating >= 6, no veto → "buy"
  - rating 2..5 → "hold" or "accumulate" (positive trend)
  - rating <= 0 → "sell" or "avoid"

target_price = etf-bull-advocate.target_price_base
stop_loss    = etf-bear-advocate.target_price_low  (or 0.95 × current price if more conservative)
position_pct = parse etf-risk-officer.position_sizing_advice (e.g. "3-5%" → 0.04)

Structure the markdown_body with these 8 sections (in Chinese):
  一、综合评级   — overall rating table (5 dims), action, target/stop
  二、跟踪误差   — tracking error analysis vs benchmark
  三、持仓构成   — top holdings, sector weights, concentration risk
  四、流动性与资金流 — ADV/AUM, net creation/redemption, premium/discount trend
  五、估值与跟踪 — PE/PB vs index history, premium/discount vs NAV
  六、多空辩论   — bull V-anchors vs bear F-anchors summary
  七、风控审查   — veto flags (if any), risk_score rationale, hard rules that fired
  八、操作建议   — entry price range, position sizing, stop-loss, holding period

Output JSON only. Fields:
  "markdown_body": str (full markdown, 8 sections above)
  "rating_overall": int
  "rating_dimensions": {"holdings": int, "technical": int, "flow": int, "valuation": int, "risk": int}
  "action": str
  "target_price": float
  "stop_loss": float
  "position_pct": float  (decimal, e.g. 0.05 for 5%)
  "summary_json": dict (machine-readable key metrics)
"""


class EtfReportWriter(SubAgent[EtfReportOutput]):
    NAME = "etf-report-writer"
    OUTPUT_SCHEMA = EtfReportOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)

        # Read routing keys from inputs (mirror stock report_writer)
        code = inputs.get("code", "UNKNOWN")
        asof = inputs.get("asof_date", "UNKNOWN")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        # Gather all upstream agent outputs
        upstream = {k: inputs.get(k, {}) for k in [
            "etf-quote-fetcher",
            "etf-metrics-fetcher",
            "etf-holdings-analyst",
            "etf-technical-analyst",
            "etf-flow-analyst",
            "etf-valuation-analyst",
            "etf-bull-advocate",
            "etf-bear-advocate",
            "etf-risk-officer",
        ]}
        upstream_json = json.dumps(upstream, default=str, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": (
                f"ETF Code: {code}\nAs-of: {asof}\n\n"
                f"Upstream:\n{upstream_json}\n\nReturn JSON."
            )},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        # ------------------------------------------------------------------
        # Python sanity-fix layer (mirrors stock report_writer exactly)
        # ------------------------------------------------------------------
        rating_overall = int(parsed.get("rating_overall", 0))
        rating_dimensions: Dict[str, int] = parsed.get("rating_dimensions", {}) or {}
        position_pct = float(parsed.get("position_pct", 0.0))

        # I-2: Override each rating dimension with the authoritative analyst value
        # so the LLM cannot invent scores inconsistent with analyst outputs.
        for dim, src_key, field in [
            ("holdings", "etf-holdings-analyst", "holdings_score"),
            ("technical", "etf-technical-analyst", "technical_score"),
            ("flow", "etf-flow-analyst", "flow_score"),
            ("valuation", "etf-valuation-analyst", "valuation_score"),
            ("risk", "etf-risk-officer", "risk_score"),
        ]:
            v = (inputs.get(src_key) or {}).get(field)
            if v is not None:
                rating_dimensions[dim] = int(v)
        target_price = float(parsed.get("target_price", 0.0))
        stop_loss = float(parsed.get("stop_loss", 0.0))

        # Pull veto_flags from the risk-officer upstream (not just LLM output)
        veto_flags_from_risk = inputs.get("etf-risk-officer", {}).get("veto_flags", []) or []
        current_price = float(
            (inputs.get("etf-quote-fetcher") or {}).get("close", 0.0) or 0.0
        )

        sanity_notes = []

        # 1. Range clamp: rating_overall
        if not (-10 <= rating_overall <= 10):
            old = rating_overall
            rating_overall = max(-10, min(10, rating_overall))
            sanity_notes.append(f"rating_overall {old} → {rating_overall} (clamped [-10,10])")

        # 2. Range clamp: position_pct
        if not (0.0 <= position_pct <= 0.10):
            old = position_pct
            position_pct = max(0.0, min(0.10, position_pct))
            sanity_notes.append(f"position_pct {old:.3f} → {position_pct:.3f} (clamped [0,0.10])")

        # 3. rating_overall must equal sum(dimensions) — override LLM's own rating
        if rating_dimensions:
            dim_sum = sum(int(v) for v in rating_dimensions.values())
            if rating_overall != dim_sum:
                sanity_notes.append(
                    f"rating_overall {rating_overall} ≠ sum(dims) {dim_sum} → corrected to {dim_sum}"
                )
                rating_overall = dim_sum
            # Re-clamp after correction
            if not (-10 <= rating_overall <= 10):
                old = rating_overall
                rating_overall = max(-10, min(10, rating_overall))
                sanity_notes.append(f"rating_overall post-sum {old} clamped → {rating_overall}")

        # 4. Veto active → force position to 0
        if veto_flags_from_risk:
            if position_pct > 0:
                sanity_notes.append(f"veto active ({veto_flags_from_risk}) → position_pct forced 0")
                position_pct = 0.0
        elif rating_overall <= 0:
            # Low rating → also no position
            if position_pct > 0:
                sanity_notes.append(f"rating={rating_overall} ≤ 0 → position_pct forced 0")
                position_pct = 0.0

        # 5. Derive action consistently with final position/rating
        if position_pct == 0.0:
            if veto_flags_from_risk or rating_overall <= -3:
                action = "avoid"
            elif rating_overall <= 0:
                action = "sell"
            else:
                action = "hold"
        else:
            action = str(parsed.get("action", "hold"))
            if action not in {"buy", "hold", "sell", "avoid", "accumulate"}:
                sanity_notes.append(f"action {action!r} invalid → 'hold'")
                action = "hold"

        # 6. action='sell' but target_price > current price: fix target
        if action == "sell" and current_price > 0 and target_price > current_price:
            old = target_price
            target_price = current_price * 0.95
            sanity_notes.append(
                f"action='sell' but target {old:.4f} > current {current_price:.4f} "
                f"→ set to current×0.95={target_price:.4f}"
            )

        # 7. stop_loss < 0 → 0
        if stop_loss < 0:
            sanity_notes.append(f"stop_loss {stop_loss} < 0 → 0")
            stop_loss = 0.0

        # 8. target_price <= 0 → fallback
        if target_price <= 0:
            target_price = current_price if current_price > 0 else 0.01
            sanity_notes.append(f"target_price ≤ 0 → fallback {target_price}")

        # Append sanity notes to markdown body
        markdown_body = parsed.get("markdown_body", f"# {code} ETF 研报\n(空报告)")
        if sanity_notes:
            log.info("etf_report_writer sanity_notes for %s: %s", code, sanity_notes)
            markdown_body += (
                "\n\n---\n*Post-write sanity overrides:*\n"
                + "\n".join(f"- {n}" for n in sanity_notes)
                + "\n"
            )

        # ------------------------------------------------------------------
        # Write files
        # ------------------------------------------------------------------
        md_path = out_dir / f"{code}_{asof}.md"
        json_path = out_dir / f"{code}_{asof}.json"
        md_path.write_text(markdown_body, encoding="utf-8")
        json_path.write_text(
            json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "rating_overall": rating_overall,
            "rating_dimensions": rating_dimensions,
            "action": action,
            "target_price": target_price,
            "stop_loss": stop_loss,
            "position_pct": position_pct,
            "markdown_body": markdown_body,
            "summary_json": parsed.get("summary_json", {}),
        }
