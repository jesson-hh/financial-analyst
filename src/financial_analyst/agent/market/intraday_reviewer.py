"""Intraday Reviewer — read past reports + pull current quotes → per-stock verdict.

Designed for the 11:35-13:00 lunch break window. Reads the user's `out/*.json`
(from previous deep-dive reports) for predicted target_price / stop_loss / action,
then pulls today's quote to judge whether the thesis holds.

Three verdicts per stock:
  OK     — direction matches, hold position
  警惕   — slight divergence, monitor afternoon
  撤离   — stop_loss hit OR thesis broken, exit now
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class StockVerdict(BaseModel):
    model_config = {"extra": "allow"}
    code: str
    prev_asof: Optional[str] = None      # date of the past report
    prev_action: str = "hold"
    prev_target: Optional[float] = None
    prev_stop: Optional[float] = None
    prev_position_pct: Optional[float] = None

    current_close: Optional[float] = None
    current_high: Optional[float] = None
    current_low: Optional[float] = None
    pct_change_since_asof: Optional[float] = None

    verdict: str = "OK"     # OK | 警惕 | 撤离
    reason: str = ""        # short explanation
    afternoon_action: str = ""   # what to do in PM session


class IntradayReviewOutput(BaseModel):
    output_md_path: str
    output_json_path: str
    as_of: str
    n_stocks: int
    verdicts: List[StockVerdict] = []
    summary: str = ""


SYSTEM_PROMPT = """You are the lunch-break intraday reviewer for an A-share research desk.

You receive:
- as_of date (today)
- list of stocks with:
  * prev_asof: when the past report was written
  * prev_action / prev_target / prev_stop / prev_position_pct: what the past report said
  * current_close / current_high / current_low: today's quote so far
  * pct_change_since_asof: realized return since the past report

Your job: per stock, emit a verdict ∈ {OK, 警惕, 撤离}, plus one-sentence reason and one-sentence afternoon_action.

Rules:
1. **撤离** if:
   - current_low <= prev_stop (stop_loss already triggered)
   - OR pct_change_since_asof < -8% (extreme adverse move regardless of stop)
   - OR prev_action="buy" but current pct_change_since_asof < -5%
2. **警惕** if:
   - prev_action="buy" but flat/slight neg (pct < -2% but > -5%)
   - OR prev_action="sell/avoid" but stock rallied > +3%
   - OR approaching stop_loss (current_low within 3% of prev_stop)
3. **OK** if direction matches expectation.

afternoon_action examples:
- "继续持有, 关注 14:30 收盘价是否守住 X.XX"
- "如下午继续下行破 X.XX 立即清仓"
- "止损已触发, 开盘后剩余仓位市价清"

DO NOT make up data you don't have. If a stock has no past report data (prev_action="?"), skip it.

Also produce a top-level `summary` (1-2 sentences): how many OK / 警惕 / 撤离, today's market regime.

Return JSON matching IntradayReviewOutput schema:
{
  "verdicts": [{"code": "...", "verdict": "OK|警惕|撤离", "reason": "...", "afternoon_action": "...", ...}],
  "summary": "...",
  "markdown_body": "<full markdown brief>",
  "summary_json": {<key counts>}
}
"""


def _load_past_report(code: str, out_dir: Path) -> Optional[Dict[str, Any]]:
    """Find the most recent JSON report for this code under out_dir."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return None
    candidates = sorted(out_dir.glob(f"{code}_*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    try:
        data = json.loads(candidates[0].read_text(encoding="utf-8"))
        stem = candidates[0].stem
        # filename format: SH600519_2026-05-15.json
        try:
            _code, asof = stem.rsplit("_", 1)
            data["_asof"] = asof
        except ValueError:
            pass
        return data
    except Exception:
        return None


def _build_stock_context(
    code: str, asof: str, loader, out_dir: Path,
) -> Dict[str, Any]:
    """Build the per-stock input row for the LLM."""
    past = _load_past_report(code, out_dir) or {}
    prev_asof = past.get("_asof")
    ctx: Dict[str, Any] = {
        "code": code,
        "prev_asof": prev_asof,
        "prev_action": past.get("action", "?"),
        "prev_target": past.get("target_price"),
        "prev_stop": past.get("stop_loss"),
        "prev_position_pct": past.get("position_pct"),
    }
    # Today's quote
    try:
        start = (pd.Timestamp(asof) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        df = loader.fetch_quote(code, start, asof)
        if df is not None and not df.empty:
            last = df.iloc[-1]
            ctx["current_close"] = float(last["close"])
            ctx["current_high"] = float(last["high"])
            ctx["current_low"] = float(last["low"])
            if prev_asof:
                try:
                    prev_row = df[df["trade_date"] == pd.Timestamp(prev_asof)]
                    if not prev_row.empty:
                        base = float(prev_row.iloc[0]["close"])
                    else:
                        base = float(df["close"].iloc[0])
                    if base > 0:
                        ctx["pct_change_since_asof"] = (float(last["close"]) / base - 1) * 100
                except Exception:
                    pass
    except Exception:
        pass
    return ctx


class IntradayReviewer(SubAgent[IntradayReviewOutput]):
    NAME = "intraday-reviewer"
    OUTPUT_SCHEMA = IntradayReviewOutput

    def __init__(self, memory_root, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        from financial_analyst.data.loader_factory import get_default_loader
        return self._loader or get_default_loader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        codes = inputs.get("codes") or []
        if isinstance(codes, str):
            codes = [c.strip().upper() for c in codes.split(",") if c.strip()]
        asof = inputs.get("asof_date") or pd.Timestamp.today().strftime("%Y-%m-%d")
        out_dir = Path(inputs.get("out_dir", "./out"))
        out_dir.mkdir(parents=True, exist_ok=True)

        loader = self._get_loader()

        # If codes not given, auto-detect from recent reports
        if not codes:
            seen = set()
            for f in sorted(out_dir.glob("*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                stem = f.stem
                try:
                    code, _ = stem.rsplit("_", 1)
                except ValueError:
                    continue
                if code not in seen:
                    seen.add(code)
                    codes.append(code)
                if len(codes) >= 10:
                    break

        if not codes:
            raise ValueError("No codes given and no past reports found in out/")

        contexts = [_build_stock_context(c, asof, loader, out_dir) for c in codes]

        client = LLMClient.for_agent(self.NAME)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": (
                f"As of: {asof}\n"
                f"Stocks: {len(contexts)}\n\n"
                f"```json\n{json.dumps(contexts, default=str, ensure_ascii=False, indent=2)[:10000]}\n```\n\n"
                "Return JSON per schema."
            )},
        ]
        response = await client.chat(
            messages=messages, response_format={"type": "json_object"}, temperature=0.2,
        )
        parsed = json.loads(response["choices"][0]["message"]["content"])

        md_path = out_dir / f"intraday_review_{asof}.md"
        json_path = out_dir / f"intraday_review_{asof}.json"
        md_path.write_text(parsed.get("markdown_body", f"# Intraday Review {asof}\n(empty)"), encoding="utf-8")
        json_path.write_text(
            json.dumps(parsed.get("summary_json", parsed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "output_md_path": str(md_path),
            "output_json_path": str(json_path),
            "as_of": asof,
            "n_stocks": len(contexts),
            "verdicts": parsed.get("verdicts", []),
            "summary": str(parsed.get("summary", "")),
        }
