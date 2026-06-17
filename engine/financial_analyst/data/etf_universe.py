from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import pandas as pd


def _akshare_spot() -> pd.DataFrame:
    import akshare as ak
    return ak.fund_etf_spot_em()   # cols include 代码 / 总市值 / 换手率


def _to_qlib(code6: str) -> str:
    c = str(code6).zfill(6)
    return ("SH" if c[0] in "56" else "SZ") + c   # 5xx->SH, 1xx->SZ


def build_etf_universe(top_n: int = 200, seed: Optional[List[str]] = None,
                       out_path: Optional[Path] = None) -> List[str]:
    spot = _akshare_spot().dropna(subset=["总市值"]).sort_values("总市值", ascending=False)
    top = [_to_qlib(c) for c in spot["代码"].head(top_n)]
    merged = sorted(set((seed or []) + top))
    if out_path:
        Path(out_path).write_text("\n".join(merged) + "\n", encoding="utf-8")
    return merged
