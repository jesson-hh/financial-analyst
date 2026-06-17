"""EtfIntrospector — Tier-4 post-mortem meta-analyst for the ETF research swarm.

Runs AFTER ``etf-report-writer``: reads the full report payload (all upstream
agent outputs + the writer's final ratings/action), does a self-review for
immediate quality issues, and proposes new rules for human review.

**Does NOT auto-patch memory files** — proposals are written to
``memories/_pending_introspections/<date>_<code>.json`` for human review.

Role anchors for ETF research:
  V-anchors (bull): V1 theme tailwinds, V2 net-inflow momentum, V3 discount,
                    V4 index methodology, V5 low fee, V6 liquidity/scale
  F-anchors (bear): F1 crowded/priced-in, F2 tracking-error drift, F3 high fee,
                    F4 concentration, F5 premium mean-revert, F6 AUM shrink,
                    F7 leveraged-ETF decay
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class EtfIntrospectionProposal(BaseModel):
    target_agent: str           # "etf-risk-officer" / "etf-bear-advocate" / "_shared"
    pattern: str                # observed feature intersection / failure mode
    proposed_rule: str          # one-sentence rule; human reviews before patching memory
    confidence: str             # "low" | "med" | "high"
    rationale: str              # why this rule is worth adding


class EtfIntrospectionOutput(BaseModel):
    quality_flags: List[str] = []
    proposals: List[EtfIntrospectionProposal] = []
    summary: str = ""
    written_to: Optional[str] = None   # pending JSON file path (None = no proposals)


SYSTEM_PROMPT = """You are the ETF Introspector — a post-mortem meta-analyst for the
ETF research swarm. The other agents just finished a single-ETF report.
Your job: self-review for immediate quality issues + propose memory rules for human review.
Output strict JSON (EtfIntrospectionOutput schema).

# Self-review rules
- Wrong > Partial > Correct: anomalies carry more signal than agreement.
- Confidence: single ETF report = 1 case → proposals are mostly low/med unless the
  pattern strongly matches an existing memory rule.
- Look for FEATURE INTERSECTIONS: flow_regime, premium_discount, aum_trend,
  concentration_risk, technical_score, valuation_score, vol_regime signs.
- Anti-patterns (DON'T propose):
  · "Need more data" (useless; empty proposals list = honest)
  · "Bear was too bearish" without specifying when / why / which feature
  · Contradicting existing memory without citing the existing rule
- Prefer attaching rules to etf-risk-officer (CRO has veto; safer than weakening analysts).

# Quality flags — IMMEDIATE issues in THIS report (quality_flags array):
- bull bullets missing V# anchors (etf-bull-advocate thesis_bullets without "[V#]" prefix)
- bear bullets missing F# anchors (etf-bear-advocate thesis_bullets without "[F#]" prefix)
- rating_overall ≠ sum of 5 rating dimensions (writer internal inconsistency)
- bull and bear strongly disagree but rating shows no uncertainty
- etf-risk-officer has veto_flag but action != "avoid"
- target_price far from both bull target_price_high and bear target_price_low
- position_pct > 0 when rating_overall <= 0 (Pydantic guard should catch, flag if seen)

# Proposals — RULES for memory (proposals array):
- Each proposal: target_agent + pattern + proposed_rule + confidence + rationale
- Prefer etf-risk-officer (defensive) then etf-bear-advocate (failure modes)
- Do NOT add rules to etf-bull-advocate (risk of over-suppressing upside)
- Empty proposals = honest (no rule-worthy pattern found this case)

# Output
Strict EtfIntrospectionOutput JSON. proposals is an array (may be empty). No free text."""


class EtfIntrospector(SubAgent[EtfIntrospectionOutput]):
    NAME = "etf-introspector"
    OUTPUT_SCHEMA = EtfIntrospectionOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)

        ctx = {
            "holdings":   inputs.get("etf-holdings-analyst", {}),
            "technical":  inputs.get("etf-technical-analyst", {}),
            "flow":       inputs.get("etf-flow-analyst", {}),
            "valuation":  inputs.get("etf-valuation-analyst", {}),
            "bull":       inputs.get("etf-bull-advocate", {}),
            "bear":       inputs.get("etf-bear-advocate", {}),
            "risk_officer": inputs.get("etf-risk-officer", {}),
            "writer":     inputs.get("etf-report-writer", {}),
        }
        ctx_json = json.dumps(ctx, ensure_ascii=False, default=str)[:8000]

        memory_text = self.memory.load_all()

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + "\n\n# My memory rules\n" + memory_text,
            },
            {
                "role": "user",
                "content": (
                    f"Full ETF report payload (JSON):\n{ctx_json}\n\n"
                    "Return EtfIntrospectionOutput JSON per schema."
                ),
            },
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])

        # Non-empty proposals → persist for human review (never auto-patch memory)
        if raw.get("proposals"):
            try:
                from financial_analyst.memory_paths import default_memory_root
                pending_dir = default_memory_root() / "_pending_introspections"
                pending_dir.mkdir(parents=True, exist_ok=True)

                writer_out = inputs.get("etf-report-writer") or {}
                md_path = (writer_out.get("output_md_path") or "").strip()
                code = ""
                if md_path:
                    stem = Path(md_path).stem
                    code = stem.split("_")[0] if "_" in stem else stem
                today = date.today().isoformat()
                out_file = pending_dir / f"{today}_{code or 'unknown'}_etf.json"
                out_file.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                raw["written_to"] = str(out_file)
            except Exception as e:
                raw.setdefault("quality_flags", []).append(
                    f"etf-introspector: failed to persist proposals: {type(e).__name__}: {e}"
                )

        return raw
