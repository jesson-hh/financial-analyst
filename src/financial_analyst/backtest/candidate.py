"""Candidate pool pre-filter (§246 degraded form).

The universe for a decision day is ``holdings ∪ watchlist ∪ rev_20 top-N``,
where rev_20 is the 20-trading-day reversal computed off **≤T-1** close only.
This deliberately does NOT run the full 5000+ market (that is the stocks
universe; fa has no csiall API) and does NOT touch any T-day data — the single
market-data source is ``reader.fetch_quote_leq_prev(end=prev)`` plus the static
``watchlist.parquet``.

rev_20 picks the *lowest* reversal by default (``rev20_pick='low'`` = nsmallest):
A 股 reversal is the strongest factor and the project rule is "rev_20 越低越值得
关注, 不追涨". Flip to ``'high'`` only deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class CandidateConfig:
    topn: int = 20
    pool: Optional[str] = None           # P2 新增 — None=旧 watchlist 路径, 非空=池子模式
    # codes 模式 (2026-06-03): 用户指定代码 (单股/watchlist), 非空时优先级最高.
    # 优先级: codes > pool > 旧 watchlist 路径. 见 select_candidates 三分支.
    codes: Optional[List[str]] = None
    rev20_lookback_tradedays: int = 30   # trading-day rollback (≥21 points)
    rev20_pick: str = "low"              # "low"=nsmallest (reversal); "high"=nlargest
    include_holdings: bool = True
    include_watchlist: bool = True       # pool 非空时此字段被忽略
    watchlist_path: Optional[Path] = None
    sentinel_codes: tuple = ("SH999999",)


@dataclass
class CandidateResult:
    codes: List[str]                     # holdings ∪ watchlist ∪ rev20_top, deduped, holdings first
    rev20_rank: Dict[str, float]         # code -> cross-sectional pct rank (0..1)
    universe_source: Dict[str, str]      # code -> 'holding'|'watchlist'|'rev20_top'
    asof_prev: str                       # the ≤T-1 cut date (= prev_trade_date)
    filter_stats: Dict[str, int] = field(default_factory=dict)
    # P1.3 数字化: {n_pool, n_holdings, n_base, n_rev20_computable, n_final}
    # n_pool: 池子成分股数 (pool mode) or len(watchlist) (watchlist mode)
    # n_holdings: 当前持仓数
    # n_base: 合并去重 (holdings ∪ pool/watchlist)
    # n_rev20_computable: 有 ≥21 个 close 点能算 rev_20 的数
    # n_final: 实际入选 (rev20_top + holdings, deduped)


def _load_watchlist_codes(cfg: CandidateConfig) -> List[str]:
    if not cfg.include_watchlist:
        return []
    path = cfg.watchlist_path
    if path is None:
        try:
            from financial_analyst.data.paths import get_data_paths
            path = Path(get_data_paths().parquet_root) / "watchlist.parquet"
        except Exception:
            return []
    path = Path(path)
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
    except Exception:
        return []
    if "code" not in df.columns:
        return []
    return [str(c) for c in df["code"].tolist()]


def select_candidates(date: str, holdings: List[str], reader,
                      cfg: CandidateConfig = CandidateConfig()) -> CandidateResult:
    """Build the candidate pool for ``date`` using only ≤T-1 data.

    Three modes (优先级 codes > pool > watchlist):
    * cfg.codes 非空 → codes 模式 (2026-06-03 新增): base = holdings ∪ user codes,
        不解析 pool/watchlist. 单股回测 (1 只) 或自定义 watchlist 回测 (N 只).
    * cfg.pool 非空  → 池子模式: base = holdings ∪ resolve_universe_codes(pool),
        watchlist 不参与 (BacktestRunner 回测场景, 在固定池子内 rev_20 选股).
    * 否则           → 旧 watchlist 路径: base = holdings ∪ watchlist
        (WatchLoop 实盘盯盘场景).

    filter_stats.n_pool 语义随模式变:
        codes 模式 → len(cfg.codes); pool 模式 → len(pool_codes); 老路径 → len(watch).
    """
    date = str(date)
    prev = reader.prev_trade_date(date)

    holdings = list(dict.fromkeys(holdings)) if cfg.include_holdings else []
    sentinels = set(cfg.sentinel_codes)

    if cfg.codes:
        # codes 模式 (优先级最高): 用户指定代码, 跳过 pool/watchlist 解析
        user_codes = [c for c in cfg.codes if c not in sentinels]
        base: List[str] = list(dict.fromkeys([*holdings, *user_codes]))
        watch = []   # codes 模式下 watchlist 不参与
        n_pool = len(user_codes)  # "候选输入规模" — 此处含义为 codes 长度
    elif cfg.pool:
        # 池子模式
        from financial_analyst.data.universe import resolve_universe_codes
        pool_codes = [c for c in resolve_universe_codes(cfg.pool) if c not in sentinels]
        if not pool_codes:
            raise ValueError(
                f"pool '{cfg.pool}' resolved to 0 codes "
                f"(缺 index_constituents.parquet? 跑 `fa data bootstrap`)")
        base = list(dict.fromkeys([*holdings, *pool_codes]))
        watch = []   # 池子模式下 watchlist 不参与
        n_pool = len(pool_codes)
    else:
        # 旧 watchlist 路径
        watch = [c for c in _load_watchlist_codes(cfg) if c not in sentinels]
        base = list(dict.fromkeys([*holdings, *watch]))
        n_pool = len(watch)

    # P1.3 数字化 — 前端 PoolFilterPopover 显示真数字
    filter_stats: Dict[str, int] = {
        "n_pool": n_pool,
        "n_holdings": len(holdings),
        "n_base": len(base),
        "n_rev20_computable": 0,  # filled after loop
        "n_final": 0,             # filled after ordered built
    }

    raw_rev20: Dict[str, float] = {}
    for code in base:
        df = reader.fetch_quote_leq_prev(
            code, n_days_back=cfg.rev20_lookback_tradedays,
            freq="day", as_of_date=date)
        if df is None or len(df) == 0 or "close" not in df.columns:
            continue
        df = df.sort_values("trade_date")
        close = df["close"].dropna()
        if len(close) >= 21:
            raw_rev20[code] = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    filter_stats["n_rev20_computable"] = len(raw_rev20)

    rev20_rank: Dict[str, float] = {}
    rev20_top: List[str] = []
    if raw_rev20:
        s = pd.Series(raw_rev20)
        rev20_rank = {k: float(v) for k, v in s.rank(pct=True).to_dict().items()}
        picked = (s.nsmallest(cfg.topn) if cfg.rev20_pick == "low"
                  else s.nlargest(cfg.topn))
        rev20_top = list(picked.index)

    # union, holdings first, then rev20_top, then remaining watchlist (老路径才填)
    # codes 模式: 用户指定代码强制入选 (在 rev20_top 之后补齐), 不被 topn 截断 —
    # 用户既然指定了就要看到, rev20 排名仅作信息字段供前端展示.
    ordered: List[str] = []
    source: Dict[str, str] = {}
    for c in holdings:
        if c not in source:
            ordered.append(c)
            source[c] = "holding"
    for c in rev20_top:
        if c not in source:
            ordered.append(c)
            source[c] = "rev20_top"
    if cfg.codes:
        for c in cfg.codes:
            if c not in source and c not in sentinels:
                ordered.append(c)
                source[c] = "user_codes"
    if not cfg.pool and not cfg.codes:
        for c in watch:
            if c not in source:
                ordered.append(c)
                source[c] = "watchlist"

    filter_stats["n_final"] = len(ordered)

    return CandidateResult(
        codes=ordered, rev20_rank=rev20_rank, universe_source=source,
        asof_prev=prev if prev is not None else "",
        filter_stats=filter_stats,
    )
