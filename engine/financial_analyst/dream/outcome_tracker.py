"""Collect outcomes (T+5d / T+20d) for historical reports in out/*.json."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    """One historical prediction + its measured outcome."""
    code: str
    asof_date: str
    rating_overall: int
    action: str
    target_price: float
    stop_loss: float
    position_pct: float
    actual_close_t5d: Optional[float] = None
    actual_close_t20d: Optional[float] = None
    high_t1_t5d: Optional[float] = None
    low_t1_t5d: Optional[float] = None
    return_t5d: Optional[float] = None
    return_t20d: Optional[float] = None
    hit_target_within_5d: Optional[bool] = None
    hit_stop_within_5d: Optional[bool] = None
    verdict: str = "pending"   # correct | wrong | partial | pending
    summary_json: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


def _action_verdict(action: str, return_5d: float, hit_target: bool, hit_stop: bool) -> str:
    """Map predicted action × actual outcome → verdict.

    Rules:
      buy:    return_5d > 2% OR hit_target → correct, hit_stop OR < -2% → wrong, else partial
      hold:   -2% ≤ return_5d ≤ 2% → correct, else partial
      sell:   return_5d < 0 → correct, else wrong
      avoid:  return_5d ≤ 0 → correct, else partial
    """
    if hit_stop:
        return "wrong"
    if action == "buy":
        if hit_target or return_5d > 0.02:
            return "correct"
        if return_5d < -0.02:
            return "wrong"
        return "partial"
    if action == "hold":
        if -0.02 <= return_5d <= 0.02:
            return "correct"
        return "partial"
    if action == "sell":
        return "correct" if return_5d < 0 else "wrong"
    if action == "avoid":
        return "correct" if return_5d <= 0 else "partial"
    return "partial"


class OutcomeTracker:
    """Collects predictions from out/*.json and measures outcomes via loader."""

    def __init__(self, loader, out_dir: Path = Path("out")):
        self.loader = loader
        self.out_dir = Path(out_dir)

    def _load_report_json(self, json_path: Path) -> Optional[Dict]:
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("skip %s: %s", json_path, exc)
            return None

    def collect(self, since_days: int = 30, today: Optional[pd.Timestamp] = None) -> List[Outcome]:
        """Scan out/*.json, fetch outcomes via loader, return Outcome list.

        Filters to reports asof_date within `since_days` days of `today`.
        Reports too recent for T+5d data → verdict='pending'.
        """
        today = today or pd.Timestamp.today().normalize()
        cutoff = today - pd.Timedelta(days=since_days)

        outcomes: List[Outcome] = []
        for json_path in sorted(self.out_dir.glob("*.json")):
            data = self._load_report_json(json_path)
            if not data:
                continue
            stem = json_path.stem
            try:
                code, asof_str = stem.rsplit("_", 1)
                asof_ts = pd.Timestamp(asof_str)
            except Exception:
                continue
            if asof_ts < cutoff:
                continue

            outcome = Outcome(
                code=code,
                asof_date=asof_str,
                rating_overall=int(data.get("rating_overall", 0)),
                action=str(data.get("action", "hold")),
                target_price=float(data.get("target_price", 0.0)),
                stop_loss=float(data.get("stop_loss", 0.0)),
                position_pct=float(data.get("position_pct", 0.0)),
                summary_json=data,
            )

            t1 = asof_ts + pd.Timedelta(days=1)
            t25 = asof_ts + pd.Timedelta(days=30)
            try:
                bars = self.loader.fetch_quote(
                    code, t1.strftime("%Y-%m-%d"), t25.strftime("%Y-%m-%d"), freq="day"
                )
            except Exception as exc:
                log.warning("loader failed for %s @ %s: %s", code, asof_str, exc)
                outcomes.append(outcome)
                continue

            if bars is None or bars.empty:
                outcomes.append(outcome)
                continue

            t1_5 = bars.head(5)
            if len(t1_5) >= 5:
                outcome.actual_close_t5d = float(t1_5["close"].iloc[-1])
                outcome.high_t1_t5d = float(t1_5["high"].max())
                outcome.low_t1_t5d = float(t1_5["low"].min())
                base = float(t1_5["close"].iloc[0])
                if base > 0:
                    outcome.return_t5d = (outcome.actual_close_t5d / base) - 1
                outcome.hit_target_within_5d = bool(
                    outcome.target_price > 0 and outcome.high_t1_t5d >= outcome.target_price
                )
                outcome.hit_stop_within_5d = bool(
                    outcome.stop_loss > 0 and outcome.low_t1_t5d <= outcome.stop_loss
                )
                outcome.verdict = _action_verdict(
                    outcome.action,
                    outcome.return_t5d or 0.0,
                    outcome.hit_target_within_5d or False,
                    outcome.hit_stop_within_5d or False,
                )

            t20 = bars.head(20)
            if len(t20) >= 20:
                base = float(t20["close"].iloc[0])
                close_t20 = float(t20["close"].iloc[-1])
                outcome.actual_close_t20d = close_t20
                if base > 0:
                    outcome.return_t20d = (close_t20 / base) - 1

            outcomes.append(outcome)
        return outcomes
