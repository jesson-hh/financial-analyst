"""Whole-market ETF board snapshot.

Data source: sina ``fund_etf_category_sina(symbol='ETF基金')`` — the eastmoney
``fund_etf_spot_em`` (which carries 规模/折价率/IOPV) is unreachable behind the
local Clash fake-ip setup (RemoteDisconnected), so the board uses sina, which
returns ~1500 ETFs with name/price/change%/amount/volume. The richer ETF-only
fields live in the per-ETF deep report instead.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def etf_market_board() -> List[Dict[str, Any]]:
    """Return whole-market ETFs as a list of
    ``{code, name, price, change_pct, amount, volume}`` sorted by amount desc.

    Suspended ETFs (price null/<=0) are dropped. ``code`` is uppercased to qlib
    form (``sz159995`` -> ``SZ159995``).
    """
    # akshare uses `requests` (system proxy). Clash fake-ip hijacks domestic
    # endpoints, so bypass the proxy for THIS call only, then restore — setting
    # NO_PROXY globally would pollute the overseas LLM/HF path.
    saved = {k: os.environ.get(k) for k in ("NO_PROXY", "no_proxy")}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        import akshare as ak
        df = ak.fund_etf_category_sina(symbol="ETF基金")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _f(row, col):
        try:
            return float(row[col])
        except (TypeError, ValueError, KeyError):
            return None

    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        price = _f(r, "最新价")
        if price is None or price <= 0:
            continue  # suspended / no quote
        rows.append({
            "code": str(r["代码"]).upper(),
            "name": str(r["名称"]),
            "price": price,
            "change_pct": _f(r, "涨跌幅"),
            "amount": _f(r, "成交额"),
            "volume": _f(r, "成交量"),
        })
    rows.sort(key=lambda x: x["amount"] if x["amount"] is not None else -1.0, reverse=True)
    return rows
