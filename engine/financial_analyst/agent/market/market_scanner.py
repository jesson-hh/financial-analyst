"""Market scanner — detect 异动 stocks across a universe.

Reads instruments list (from loader's provider_uri), iterates each code, pulls
recent quote, computes today's pct_change + volume_ratio, classifies by mv tier,
flags 异动 if either threshold breached.

No LLM call. Returns structured payload.

证据包捷径(2026-07-12):`_execute` 开头读 FA_EVIDENCE_PACK(ctor 参数 pack_path >
env > 无默认 —— 与 evidence_loader.py / mainline_classifier.py 三层同款),pack 存在且
``sections.board_eco`` 非空 → 直接用其聚合字段(涨停/炸板家数→n_flagged、北向净额/晋级率→
index_snapshot)拼一份 Output 返回,零逐票扫描(个股级 top_gainers/top_losers/
volume_anomalies 该聚合面没有对应源,诚实留空,不编造)。否则退回现状逐票扫描,universe
上限从 5000 收敛到 1500(全市场扫描本就是兜底路径,证据包命中时优先级更高更省时)。
"""
from __future__ import annotations
import json
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
                 max_scan: int = 1500, pack_path: Optional[str] = None):
        super().__init__(memory_root=memory_root)
        self._loader = loader
        self._universe_file = universe_file
        self._max_scan = max_scan
        # 证据包路径:ctor 显式传入 > env FA_EVIDENCE_PACK > 无(None,诚实退回扫描)。
        self._pack_path = pack_path or os.environ.get("FA_EVIDENCE_PACK")

    def _get_loader(self):
        from financial_analyst.data.loader_factory import get_default_loader
        return self._loader or get_default_loader()

    def _read_evidence_pack(self) -> Optional[Dict[str, Any]]:
        """读 FA_EVIDENCE_PACK 落盘 JSON;文件缺失/不可解析/board_eco 段为空 → None
        (诚实退回现状扫描路径,绝不编造)。仅在此处触碰该文件——找不到就当没有。"""
        if not self._pack_path:
            return None
        path = Path(self._pack_path)
        if not path.exists():
            return None
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (pack.get("sections") or {}).get("board_eco"):
            return None
        return pack

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
        if len(codes) > self._max_scan:
            print(f"[market-scanner] universe {len(codes)} 支收敛到 max_scan={self._max_scan}"
                  f"(证据包未命中,退回逐票扫描路径)")
        return codes[: self._max_scan]

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        asof = inputs.get("asof_date") or pd.Timestamp.today().strftime("%Y-%m-%d")
        universe = inputs.get("universe", "all")

        pack = self._read_evidence_pack()
        if pack is not None:
            board = (pack.get("sections") or {}).get("board_eco") or {}
            index_snapshot: Dict[str, float] = {}
            for src_key, out_key in (("north_net", "north_net"), ("promotion_rate", "promotion_rate"),
                                      ("break_rate", "break_rate")):
                v = board.get(src_key)
                if v is not None:
                    try:
                        index_snapshot[out_key] = float(v)
                    except (TypeError, ValueError):
                        pass
            try:
                n_flagged = int(board.get("zt_count") or 0) + int(board.get("zb_count") or 0)
            except (TypeError, ValueError):
                n_flagged = 0
            return {
                "as_of": str(board.get("as_of") or asof),
                "universe": universe,
                "n_scanned": 0,   # 平台证据路径零逐票扫描,诚实 0(非"扫了 0 支")
                "n_flagged": n_flagged,
                "top_gainers": [],       # 聚合面无个股级涨跌幅明细,诚实留空(非编造)
                "top_losers": [],
                "volume_anomalies": [],  # 聚合面无个股级量比明细,诚实留空
                "index_snapshot": index_snapshot,
                "note": "平台证据路径(evidence pack):涨停/炸板家数→n_flagged,晋级率/炸板率/"
                        "北向净额→index_snapshot,取自打板生态聚合(board_eco),未逐票扫描;"
                        "个股级涨跌幅/量比该聚合面无对应源,诚实留空。",
            }

        # ---- 退回现状:证据包未命中 → 逐票扫描(原逻辑不变,仅 max_scan 上限收敛) ----
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
