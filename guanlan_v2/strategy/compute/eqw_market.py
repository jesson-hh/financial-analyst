# -*- coding: utf-8 -*-
"""全A等权基准产物(P1 §1):当日全市场 close/prev_close−1 的截面均值日线。

给 basket_perf(选股篮子收益跟踪)一把公平尺子。regen 顺算(breadth 后非阻断步),
产物 = ARTIFACTS_DIR/eqw_market_ret.parquet(date/ret/n 三列)。
口径:逐股 close.pct_change(fill_method=None)——停牌日 close=NaN 自然剔除
(**不 ffill**,否则停牌日 ret=0 污染均值);复牌首日 prev=NaN 亦剔除(保守);
当日未结算 bar 在二进制里本就无 close,天然无前视。全量重算幂等覆盖(原子写)。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from guanlan_v2.strategy.paths import EQW_MARKET_RET_PARQUET

DEFAULT_START = "2019-11-01"   # 对齐 breadth FETCH_START,足够覆盖任何 picks 跟踪窗


def compute_eqw_market(provider_uri: str, end: Optional[str] = None,
                       codes: Optional[List[str]] = None, start: str = DEFAULT_START,
                       loader=None) -> int:
    """全量重算等权日收益产物 → 行数。loader 可注入(测试);None=QlibBinaryLoader。"""
    import pandas as pd
    if loader is None:
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        loader = QlibBinaryLoader(provider_uri)
    if codes is None:
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        codes = list_all_instruments(provider_uri)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else None
    rets: List["pd.Series"] = []
    for code in codes:
        c = loader._read_bin(code, "close")
        if c is None or len(c) == 0:
            continue
        c = c.loc[c.index >= start_ts]
        if end_ts is not None:
            c = c.loc[c.index <= end_ts]
        if len(c) < 2:
            continue
        rets.append(c.pct_change(fill_method=None))   # 停牌 NaN 剔除;复牌首日保守剔除
    if not rets:
        raise RuntimeError("compute_eqw_market: 无任何可读股票(检查 provider_uri/窗口)")
    wide = pd.concat(rets, axis=1)
    mean = wide.mean(axis=1, skipna=True)
    n = wide.notna().sum(axis=1)
    out = pd.DataFrame({
        "date": [pd.Timestamp(d).date().isoformat() for d in mean.index],
        "ret": mean.values.astype(float),
        "n": n.values.astype(int),
    })
    out = out[out["n"] > 0].reset_index(drop=True)
    EQW_MARKET_RET_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(EQW_MARKET_RET_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(EQW_MARKET_RET_PARQUET))
    return len(out)


_eqw_cache: Dict[str, Any] = {"mtime": None, "df": None}


def load_eqw_ret():
    """读产物 → DataFrame(date/ret/n)|None(缺失=消费方显形)。mtime 缓存(同 factor_vintage 模式)。"""
    import pandas as pd
    p = EQW_MARKET_RET_PARQUET
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _eqw_cache["mtime"] != mt:
        try:
            _eqw_cache["df"] = pd.read_parquet(p)
            _eqw_cache["mtime"] = mt
        except Exception:  # noqa: BLE001 — 读失败=None,诚实缺席
            return None
    return _eqw_cache["df"]


def eqw_cum_ret(df, entry_date: str, exit_date: str) -> Optional[float]:
    """(entry_date, exit_date] 窗口等权累计收益 ∏(1+ret)−1。
    产物缺席/窗口头尾任一不被产物覆盖/空窗 → None(诚实,绝不编造基准)。"""
    if df is None or len(df) == 0 or not entry_date or not exit_date \
            or str(exit_date) <= str(entry_date):
        return None
    if str(df["date"].min()) > str(entry_date) or str(df["date"].max()) < str(exit_date):
        return None                                   # 头/尾不覆盖 → 诚实 None
    sub = df[(df["date"] > str(entry_date)) & (df["date"] <= str(exit_date))]
    if len(sub) == 0:
        return None
    total = 1.0
    for r in sub["ret"]:
        total *= (1.0 + float(r))
    return total - 1.0
