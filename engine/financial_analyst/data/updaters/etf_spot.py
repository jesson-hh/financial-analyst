from __future__ import annotations
from pathlib import Path
from typing import List
import pandas as pd


def _akshare_spot() -> pd.DataFrame:
    import akshare as ak
    return ak.fund_etf_spot_em()


def _to_qlib(c) -> str:
    c = str(c).zfill(6)
    return ("SH" if c[0] in "56" else "SZ") + c


def update_etf_spot(codes: List[str], parquet_root, asof: str) -> pd.DataFrame:
    s = _akshare_spot().copy()
    s["ts_code"] = s["代码"].map(_to_qlib)
    s = s[s["ts_code"].isin(set(codes))]
    out = pd.DataFrame({
        "ts_code": s["ts_code"],
        "asof": asof,
        "iopv": s.get("IOPV实时估值"),
        "premium_discount_pct": s.get("基金折价率"),
        "shares": s.get("最新份额"),
        "aum": s.get("总市值"),
        "turnover": s.get("换手率"),
    })
    out.to_parquet(Path(parquet_root) / "etf_spot.parquet", index=False)   # overwrite each run
    return out
