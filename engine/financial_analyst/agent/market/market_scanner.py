"""Market scanner — detect 异动 stocks across a universe.

Reads instruments list (from loader's provider_uri), iterates each code, pulls
recent quote, computes today's pct_change + volume_ratio, classifies by mv tier,
flags 异动 if either threshold breached.

No LLM call. Returns structured payload.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent


# Market-cap tier -> daily pct_change threshold (% absolute)
def _mv_tier_threshold(mv_yi: float) -> float:
    if mv_yi >= 1000:
        return 3.0
    if mv_yi >= 300:
        return 4.0
    if mv_yi >= 100:
        return 5.0
    return 7.0


VOL_RATIO_THRESHOLD = 3.0


class MoveRecord(BaseModel):
    model_config = {"extra": "allow"}
    code: str
    name: Optional[str] = None
    close: float
    prev_close: float
    pct_chg: float
    volume_ratio: float = 0.0
    mv_yi: Optional[float] = None
    mv_tier: str = "?"
    flagged_by: List[str] = []   # ['pct_chg', 'volume_ratio', or both]


class MarketScannerOutput(BaseModel):
    as_of: str
    universe: str
    n_scanned: int
    n_flagged: int
    top_gainers: List[MoveRecord] = []
    top_losers: List[MoveRecord] = []
    volume_anomalies: List[MoveRecord] = []
    index_snapshot: Dict[str, float] = {}   # SH000300 / SH000016 ret / close


class MarketScanner(SubAgent[MarketScannerOutput]):
    NAME = "market-scanner"
    OUTPUT_SCHEMA = MarketScannerOutput

    def __init__(self, memory_root, loader=None, universe_file: Optional[str] = None,
                 max_scan: int = 5000):
        super().__init__(memory_root=memory_root)
        self._loader = loader
        self._universe_file = universe_file
        self._max_scan = max_scan

    def _get_loader(self):
        from financial_analyst.data.loader_factory import get_default_loader
        return self._loader or get_default_loader()

    def _list_codes(self) -> List[str]:
        """Read instruments list. Default: provider_uri/instruments/all.txt."""
        if self._universe_file:
            path = Path(self._universe_file)
        else:
            # Try to use QlibBinaryLoader's day provider_uri
            loader = self._get_loader()
            roots = getattr(loader, "_roots", None)
            if roots and "day" in roots:
                path = Path(roots["day"]) / "instruments" / "all.txt"
            else:
                env_val = os.environ.get("FA_UNIVERSE_FILE", "").strip()
                path = Path(env_val) if env_val else None
        if not path or not path.exists():
            return []
        codes = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if parts and parts[0]:
                codes.append(parts[0].upper())
        return codes[: self._max_scan]

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        asof = inputs.get("asof_date") or pd.Timestamp.today().strftime("%Y-%m-%d")
        universe = inputs.get("universe", "all")
        codes = self._list_codes()
        if not codes:
            raise FileNotFoundError(
                "No universe instruments found. Set FA_UNIVERSE_FILE or configure qlib_binary loader."
            )

        loader = self._get_loader()
        start = (pd.Timestamp(asof) - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
        flagged: List[Dict] = []
        n_scanned = 0
        # Index snapshot — try SH000300 if available
        index_snapshot: Dict[str, float] = {}
        try:
            idx = loader.fetch_quote("SH000300", start, asof)
            if idx is not None and not idx.empty:
                last_close = float(idx["close"].iloc[-1])
                prev = float(idx["close"].iloc[-2]) if len(idx) >= 2 else last_close
                index_snapshot["SH000300_close"] = last_close
                index_snapshot["SH000300_pct"] = (last_close / prev - 1) * 100 if prev else 0.0
        except Exception:
            pass

        for code in codes:
            try:
                df = loader.fetch_quote(code, start, asof)
            except Exception:
                continue
            if df is None or df.empty or len(df) < 2:
                continue
            n_scanned += 1
            close = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2])
            if prev <= 0:
                continue
            pct = (close / prev - 1) * 100
            vol_now = float(df["vol"].iloc[-1])
            vol_mean20 = float(df["vol"].iloc[-20:].mean()) if len(df) >= 20 else vol_now
            vol_ratio = vol_now / vol_mean20 if vol_mean20 > 0 else 0.0

            # mv_yi from daily_basic if available
            mv_yi = None
            try:
                db = loader.fetch_daily_basic(code, start, asof)
                if db is not None and not db.empty and "total_mv" in db.columns:
                    raw = db["total_mv"].iloc[-1]
                    if raw is not None and raw == raw:
                        mv_yi = float(raw) / 10000.0
            except Exception:
                pass

            mv_yi = mv_yi or 200.0  # default mid-tier when unknown
            tier_threshold = _mv_tier_threshold(mv_yi)
            mv_tier = (
                "large" if mv_yi >= 1000
                else "mid" if mv_yi >= 300
                else "small-mid" if mv_yi >= 100
                else "small"
            )

            reasons = []
            if abs(pct) >= tier_threshold:
                reasons.append("pct_chg")
            if vol_ratio >= VOL_RATIO_THRESHOLD:
                reasons.append("volume_ratio")

            if reasons:
                flagged.append(MoveRecord(
                    code=code, close=close, prev_close=prev,
                    pct_chg=pct, volume_ratio=vol_ratio,
                    mv_yi=mv_yi, mv_tier=mv_tier, flagged_by=reasons,
                ).model_dump())

        # Sort + group
        flagged_sorted = sorted(flagged, key=lambda r: r["pct_chg"], reverse=True)
        top_gainers = flagged_sorted[:20]
        top_losers = list(reversed(flagged_sorted[-20:])) if len(flagged_sorted) >= 20 else \
                     [r for r in flagged_sorted if r["pct_chg"] < 0][:20]
        vol_anomalies = sorted(
            [r for r in flagged if "volume_ratio" in r["flagged_by"]],
            key=lambda r: r["volume_ratio"], reverse=True,
        )[:20]

        return {
            "as_of": asof,
            "universe": universe,
            "n_scanned": n_scanned,
            "n_flagged": len(flagged),
            "top_gainers": top_gainers,
            "top_losers": top_losers,
            "volume_anomalies": vol_anomalies,
            "index_snapshot": index_snapshot,
        }
