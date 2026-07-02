# -*- coding: utf-8 -*-
"""环节聚合:量化侧(Task 7 追加文本侧与 board 组装)。

一切产物缺失 → 字段 None + reason,绝不静默补零(诚实红线)。
"""
from __future__ import annotations

from typing import Optional


def _fetch_quotes(codes: list, days: int = 45) -> dict:
    """真数据路径:引擎 loader 逐票取(照 seats/api.py:784-812 先例)。单票失败跳过。"""
    out: dict = {}
    try:
        import pandas as pd
        from financial_analyst.data import loader_factory as _lf
        loader = _lf.get_default_loader()
        end = str(pd.Timestamp.now().date())
        start = str((pd.Timestamp.now() - pd.Timedelta(days=days + 30)).date())
        for c in codes:
            try:
                df = loader.fetch_quote(c, start, end, "day")
                if df is not None and len(df) and "close" in df.columns:
                    out[c] = df
            except Exception:  # noqa: BLE001 — 单票失败=该票缺
                continue
    except Exception:  # noqa: BLE001 — loader 整体失败=全缺
        return {}
    return out


def _v4_pct_map() -> Optional[dict]:
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
        df = pd.read_parquet(V4_RANKING_PARQUET)
        codecol = "code" if "code" in df.columns else ("ts_code" if "ts_code" in df.columns else None)
        pctcol = "pct" if "pct" in df.columns else None
        if not codecol or not pctcol:
            return None
        return dict(zip(df[codecol].astype(str), df[pctcol]))
    except Exception:  # noqa: BLE001
        return None


def _fundflow_map() -> Optional[dict]:
    """近5日主力净流入 {code: 合计};文件缺/列不识 → None(诚实降级)。列名以实测 rename。"""
    try:
        import os
        import pandas as pd
        from pathlib import Path
        root = Path(os.environ.get("GL_PARQUET_ROOT") or r"G:/stocks/stock_data/parquet")
        p = root / "stock_fund_flow_daily.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        # 实现时实测列名后 rename 成 code/date/main_net;不识则返回 None
        cols = {c.lower(): c for c in df.columns}
        codec = cols.get("code") or cols.get("ts_code") or cols.get("stock_code")
        datec = cols.get("date") or cols.get("trade_date")
        netc = cols.get("main_net") or cols.get("main_net_inflow") or cols.get("主力净流入")
        if not (codec and datec and netc):
            return None
        df = df.rename(columns={codec: "code", datec: "date", netc: "main_net"})
        df["date"] = df["date"].astype(str).str[:10]
        last5 = sorted(df["date"].unique())[-5:]
        sub = df[df["date"].isin(last5)]
        return sub.groupby("code")["main_net"].sum().to_dict()
    except Exception:  # noqa: BLE001
        return None


def _eqw_ret20() -> Optional[float]:
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import EQW_MARKET_RET_PARQUET
        df = pd.read_parquet(EQW_MARKET_RET_PARQUET)
        retcol = "ret" if "ret" in df.columns else df.columns[-1]
        r = df[retcol].astype(float).tail(20)
        if len(r) < 20:
            return None
        return float((1 + r).prod() - 1)
    except Exception:  # noqa: BLE001
        return None


def quant_signals(fw: dict, quotes: Optional[dict] = None) -> dict:
    import numpy as np

    all_codes = sorted({x["code"] for s in fw["segments"] if not s.get("adjacent") for x in s.get("stocks", [])})
    if quotes is None:
        quotes = _fetch_quotes(all_codes)
    v4map = _v4_pct_map()
    eqw20 = _eqw_ret20()
    ffmap = _fundflow_map()

    out: dict = {}
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        codes = [x["code"] for x in s.get("stocks", [])]
        moms, amts5, amts20, v4s = [], [], [], []
        qdate = None
        for c in codes:
            df = quotes.get(c)
            if df is None or len(df) < 21:
                continue
            close = df["close"].astype(float).to_numpy()
            moms.append(close[-1] / close[-21] - 1.0)
            if "amount" in df.columns:
                amt = df["amount"].astype(float).to_numpy()
                if len(amt) >= 20:
                    amts5.append(float(amt[-5:].mean()))
                    amts20.append(float(amt[-20:].mean()))
            if "trade_date" in df.columns:
                qdate = max(qdate or "", str(df["trade_date"].iloc[-1])[:10])
            if v4map:
                hit = v4map.get(c) or v4map.get(c[2:]) or v4map.get(f"{c[2:]}.{c[:2]}")
                if hit is not None:
                    v4s.append(float(hit))
        if not moms:
            out[s["id"]] = {"momentum20": None, "excess20": None, "amount_share_delta20": None,
                            "fundflow5": None, "v4_pct_mean": None, "breadth": None, "quote_date": None,
                            "reason": "票池行情不可得"}
            continue
        mom = float(np.mean(moms))
        ff = None
        if ffmap:
            hits = [ffmap.get(c) or ffmap.get(c[2:]) or ffmap.get(f"{c[2:]}.{c[:2]}") for c in codes]
            hits = [h for h in hits if h is not None]
            ff = float(np.sum(hits)) if hits else None
        out[s["id"]] = {
            "momentum20": mom,
            "excess20": (mom - eqw20) if eqw20 is not None else None,
            "amount_share_delta20": (float(np.sum(amts5) / np.sum(amts20)) - 1.0) if amts20 and np.sum(amts20) > 0 else None,
            "fundflow5": ff,
            "v4_pct_mean": (float(np.mean(v4s)) if v4s else None),
            "breadth": float(np.mean([1.0 if m > 0 else 0.0 for m in moms])),
            "quote_date": qdate,
            "reason": None if (eqw20 is not None and v4map and ffmap) else "部分产物缺失(eqw/v4/资金流)→对应字段null",
        }
    return out
