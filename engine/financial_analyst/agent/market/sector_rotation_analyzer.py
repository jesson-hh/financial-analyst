"""Sector rotation analyzer — 异动股聚合到行业, 算今日板块涨幅 + 轮动方向.

无 LLM, 纯 Python pandas 聚合. 跟 catalyst-extractor 并行跑 (都依赖
market-scanner 输出).

数据源:
- market-scanner.top_gainers / top_losers (异动股 list)
- parquet/tushare_stock_basic.parquet (code → industry 映射)

输出 leaders / laggards + 一句话 rotation_signal:
- "顺周期占优, 防御性回调"
- "TMT 内部分化: 半导体强 / 软件弱"
- "全市场普跌, 红利防御占优"
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent


class SectorRanking(BaseModel):
    sector: str
    n_gainers: int = 0
    n_losers: int = 0
    n_total_in_movers: int = 0
    avg_pct_chg: float = 0.0
    top_stock_code: Optional[str] = None       # 这个 sector 里涨幅榜首
    top_stock_name: Optional[str] = None
    top_stock_pct: Optional[float] = None


class SectorRotationOutput(BaseModel):
    as_of: str
    today_leaders: List[SectorRanking] = Field(default_factory=list)
    today_laggards: List[SectorRanking] = Field(default_factory=list)
    rotation_signal: str = ""
    n_sectors_covered: int = 0


def _load_industry_map(parquet_root: Path) -> Dict[str, str]:
    """返 {code (UPPER): industry}. 用 tushare_stock_basic.parquet 或行业表."""
    import pandas as pd
    p = parquet_root / "tushare_stock_basic.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p)
    # tushare_stock_basic schema: ts_code, name, area, industry, market, list_date
    out: Dict[str, str] = {}
    code_col = "ts_code" if "ts_code" in df.columns else None
    ind_col = "industry" if "industry" in df.columns else None
    if not code_col or not ind_col:
        return {}
    for _, r in df.iterrows():
        ts_code = r[code_col]
        ind = r[ind_col]
        if not isinstance(ts_code, str) or not isinstance(ind, str):
            continue
        # ts_code = "600519.SH" → SH600519
        if "." in ts_code:
            num, _, suf = ts_code.partition(".")
            code = (suf + num).upper()
            out[code] = ind
    return out


def _judge_rotation(leaders: List[SectorRanking],
                     laggards: List[SectorRanking],
                     scanner: Dict[str, Any]) -> str:
    """一句话总结轮动方向."""
    if not leaders and not laggards:
        return "数据不足, 无法判读轮动"

    # SH000300 当日涨幅作大盘锚
    idx_pct = (scanner.get("index_snapshot") or {}).get("SH000300_pct")
    big_move = idx_pct is not None and abs(idx_pct) > 1.0

    lead_names = [s.sector for s in leaders[:3] if s.sector]
    lag_names = [s.sector for s in laggards[:3] if s.sector]

    parts = []
    if lead_names:
        parts.append(f"领涨: {' · '.join(lead_names)}")
    if lag_names:
        parts.append(f"领跌: {' · '.join(lag_names)}")
    if big_move:
        parts.append(f"沪深300 {idx_pct:+.2f}% — 全市场" +
                     ("普涨" if idx_pct > 0 else "普跌"))
    elif idx_pct is not None:
        parts.append(f"沪深300 {idx_pct:+.2f}% — 结构性行情, 看板块轮动")

    return " · ".join(parts) if parts else "今日板块分化不显著"


class SectorRotationAnalyzer(SubAgent[SectorRotationOutput]):
    """聚合 market-scanner 的 movers 到行业, 算今日板块 perf 排名."""

    NAME = "sector-rotation-analyzer"
    OUTPUT_SCHEMA = SectorRotationOutput

    def __init__(self, memory_root, parquet_root: Optional[Path] = None,
                 min_movers_per_sector: int = 2):
        super().__init__(memory_root=memory_root)
        if parquet_root is None:
            from financial_analyst.data.paths import get_data_paths
            parquet_root = get_data_paths().parquet_root
        self._parquet_root = Path(parquet_root)
        self._min_n = min_movers_per_sector

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        as_of = inputs.get("asof_date") or datetime.today().strftime("%Y-%m-%d")
        scanner = inputs.get("market-scanner", {}) or {}
        ind_map = _load_industry_map(self._parquet_root)

        # Aggregate all movers (gainers + losers + vol_anomalies, deduped by code)
        all_movers: Dict[str, Dict[str, Any]] = {}
        for grp in ("top_gainers", "top_losers", "volume_anomalies"):
            for r in (scanner.get(grp) or []):
                code = (r.get("code") or "").upper()
                if code and code not in all_movers:
                    all_movers[code] = r

        # Group by industry
        by_sector: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for code, r in all_movers.items():
            ind = ind_map.get(code, "未分类")
            by_sector[ind].append({**r, "code": code})

        rankings: List[SectorRanking] = []
        for sector, rows in by_sector.items():
            if len(rows) < self._min_n:
                continue
            pct_list = [r.get("pct_chg") for r in rows if r.get("pct_chg") is not None]
            if not pct_list:
                continue
            avg_pct = sum(pct_list) / len(pct_list)
            n_up = sum(1 for p in pct_list if p > 0)
            n_dn = sum(1 for p in pct_list if p < 0)
            # Top stock by pct_chg in this sector
            top_row = max(rows, key=lambda r: r.get("pct_chg") or -999)
            rankings.append(SectorRanking(
                sector=sector,
                n_gainers=n_up,
                n_losers=n_dn,
                n_total_in_movers=len(rows),
                avg_pct_chg=round(avg_pct, 2),
                top_stock_code=top_row.get("code"),
                top_stock_name=top_row.get("name"),
                top_stock_pct=top_row.get("pct_chg"),
            ))

        rankings.sort(key=lambda x: x.avg_pct_chg, reverse=True)
        leaders = rankings[:5]
        laggards = list(reversed(rankings[-5:])) if len(rankings) > 5 else \
                   [r for r in rankings if r.avg_pct_chg < 0]

        signal = _judge_rotation(leaders, laggards, scanner)

        return SectorRotationOutput(
            as_of=as_of,
            today_leaders=leaders,
            today_laggards=laggards,
            rotation_signal=signal,
            n_sectors_covered=len(rankings),
        ).model_dump()
