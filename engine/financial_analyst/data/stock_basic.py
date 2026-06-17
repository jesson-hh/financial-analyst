"""本票身份(name/area/industry/market/list_date)只读查询。

读 tushare_stock_basic.parquet(`ts_code` 形如 `000630.SZ`);
`get_basic(code)` 把 `SZ000630` / `000630` / `000630.SZ` 归一成 `000630.SZ` 查;
命中返 dict,无 / 读不到 → None(诚实降级,绝不抛)。
"""
from __future__ import annotations
import os
import re
from typing import Dict, Optional

import pandas as pd

BASIC_PATH = os.environ.get(
    "GL_STOCK_BASIC",
    r"G:\stocks\stock_data\parquet\tushare_stock_basic.parquet",
)

_cache: Optional[pd.DataFrame] = None


def _normalize_ts_code(code: str) -> Optional[str]:
    """把 SZ000630 / 000630 / 000630.SZ 归一成 `000630.SZ`。

    无法解析(取不到 6 位数字码 + 交易所)→ None。
    """
    if code is None:
        return None
    c = str(code).strip().upper()
    if not c:
        return None
    # 已是 ts_code 形式:000630.SZ
    m = re.match(r"^(\d{6})\.([A-Z]{2})$", c)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    # 前缀形式:SZ000630 / SH600519
    m = re.match(r"^([A-Z]{2})(\d{6})$", c)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    # 裸 6 位:从首位推交易所(6→SH,其余→SZ)
    m = re.match(r"^(\d{6})$", c)
    if m:
        digits = m.group(1)
        exch = "SH" if digits[0] == "6" else "SZ"
        return f"{digits}.{exch}"
    return None


def _load() -> Optional[pd.DataFrame]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = pd.read_parquet(BASIC_PATH)
    except Exception:
        _cache = None
    return _cache


def get_basic(code: str) -> Optional[Dict[str, Optional[str]]]:
    """返回 {name, area, industry, market, list_date(str)};无→None。"""
    ts = _normalize_ts_code(code)
    if ts is None:
        return None
    df = _load()
    if df is None or df.empty:
        return None
    hit = df[df["ts_code"] == ts]
    if hit.empty:
        return None
    row = hit.iloc[0]

    def _s(val) -> Optional[str]:
        if val is None:
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        return str(val)

    return {
        "name": _s(row.get("name")),
        "area": _s(row.get("area")),
        "industry": _s(row.get("industry")),
        "market": _s(row.get("market")),
        "list_date": _s(row.get("list_date")),
    }
