"""ETF 基金指标更新器 (Tushare fund_* → etf_*.parquet).

从 Tushare 拉取基金相关 API, 写入以下 Parquet 文件:
  - etf_basic.parquet    : 基金基本信息 (管理费/托管费/跟踪指数等)
  - etf_nav.parquet      : 历史净值 (单位净值/累计净值/复权净值)
  - etf_share.parquet    : 份额变动
  - etf_holdings.parquet : 基金持仓 (季报)
  - etf_div.parquet      : 分红记录
  - etf_index.parquet    : 跟踪指数日线 (由 etf_basic.index_code 驱动)

设计原则:
- ``_fund_query`` 是唯一对外 HTTP 函数, 测试时 monkeypatch 它即可, 不需要网络.
- ``domestic_session()`` 确保直连 Tushare, 绕开 Clash 代理.
- ``Accept-Encoding: identity`` 避免 brotli ContentDecodingError (某些 Windows 环境缺 brotli).
- Tushare 限速 200/min; ``_per_code`` 每个 code 间 sleep 0.12s (约 8/s, 留余量).
- ``_INDEX_CACHE`` 是模块级 dict, 测试前后手动清空即可; 进程内只查一次 index_basic.

公开 API:
- ``update_etf_basic(codes, parquet_root, token)``   → DataFrame
- ``update_etf_nav(codes, parquet_root, token)``     → DataFrame
- ``update_etf_share(codes, parquet_root, token)``   → DataFrame
- ``update_etf_holdings(codes, parquet_root, token)`` → DataFrame
- ``update_etf_div(codes, parquet_root, token)``     → DataFrame
- ``update_etf_index(parquet_root, token)``          → DataFrame
- ``update_all_fund(codes, parquet_root, token)``    → None (all of the above)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

import pandas as pd

from financial_analyst.data.loaders.tushare import TushareLoader
from financial_analyst.data.net import domestic_session

TUSHARE_URL = "http://api.tushare.pro"

# ──────────────────────── helpers ────────────────────────


def _token(token: Optional[str] = None) -> str:
    """Resolve token: explicit arg > FA_TUSHARE_TOKEN env > TUSHARE_TOKEN env."""
    t = token or os.getenv("FA_TUSHARE_TOKEN") or os.getenv("TUSHARE_TOKEN")
    if not t:
        raise ValueError("TUSHARE_TOKEN missing (set FA_TUSHARE_TOKEN or TUSHARE_TOKEN env)")
    return t


def _fund_query(token: str, api: str, fields: str = "", **params) -> pd.DataFrame:
    """POST to Tushare API; returns DataFrame.

    ``Accept-Encoding: identity`` avoids brotli ContentDecodingError on Windows
    environments that don't have the brotli C extension installed.
    """
    req: dict = {"api_name": api, "token": token, "params": params}
    if fields:
        req["fields"] = fields
    r = domestic_session().post(
        TUSHARE_URL,
        json=req,
        timeout=30,
        headers={"Accept-Encoding": "identity"},
    )
    d = r.json()
    if d.get("code") != 0:
        raise Exception(f"tushare {api} failed: {d.get('msg', '')}")
    return pd.DataFrame(d["data"]["items"], columns=d["data"]["fields"])


def _ts(codes: List[str]) -> List[str]:
    """Convert SH510300-style codes to 510300.SH Tushare format."""
    return [TushareLoader._to_tushare_code(c) for c in codes]


# Module-level index cache: populated once per process from index_basic.
# Tests clear this with ``etf_fund._INDEX_CACHE.clear()`` before/after.
_INDEX_CACHE: dict = {}


def _resolve_index_code(benchmark: str, token: Optional[str] = None) -> Optional[str]:
    """Fuzzy-match a free-text benchmark string to an index ts_code.

    Queries Tushare ``index_basic`` once per process (cached in ``_INDEX_CACHE``).
    Matches by checking whether the index name is a substring of the benchmark text.
    Returns the first matching ts_code, or None if no match.

    Examples:
        "沪深300指数收益率"  → "000300.SH"
        "中证500指数"        → "000905.SH"
        "纳斯达克100指数"    → None  (no A-share index matches)
    """
    if not benchmark:
        return None
    if not _INDEX_CACHE:
        try:
            ib = _fund_query(_token(token), "index_basic")
            for _, row in ib.iterrows():
                name = str(row.get("name", ""))
                code = row.get("ts_code")
                if name and code:
                    _INDEX_CACHE[name] = code
        except Exception:
            return None
    for name, code in _INDEX_CACHE.items():
        if name and name in benchmark:
            return code
    return None


# ──────────────────────── per-code batch helper ──────────────────────────


def _per_code(
    api: str,
    codes: List[str],
    token: str,
    fields: str = "",
    sleep: float = 0.12,
) -> pd.DataFrame:
    """Iterate over codes, query ``api`` once per code, concat results.

    Sleep between calls to respect Tushare 200/min limit.
    Individual failures are silently skipped (delisted / no data).
    """
    frames = []
    for tc in _ts(codes):
        try:
            d = _fund_query(token, api, fields=fields, ts_code=tc)
            if d is not None and not d.empty:
                frames.append(d)
        except Exception:
            pass
        time.sleep(sleep)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ──────────────────────── public updater functions ───────────────────────


def update_etf_basic(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉 fund_basic (market='E'), 过滤目标代码, 追加 index_code 列, 写 etf_basic.parquet.

    Args:
        codes:        SH/SZ 前缀 ETF 代码列表, 如 ``["SH510300", "SZ159915"]``.
        parquet_root: 输出目录 (Path 或 str).
        token:        Tushare token; 为空时读环境变量.

    Returns:
        写入的 DataFrame.
    """
    tok = _token(token)
    df = _fund_query(tok, "fund_basic", market="E")
    df = df[df["ts_code"].isin(_ts(codes))].copy()
    df["index_code"] = df["benchmark"].map(lambda b: _resolve_index_code(b, tok))
    df.to_parquet(Path(parquet_root) / "etf_basic.parquet", index=False)
    return df


def update_etf_nav(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉 fund_nav, 逐代码合并, 写 etf_nav.parquet.

    Columns retained: ts_code, nav_date, unit_nav, accum_nav, adj_nav, net_asset.
    """
    df = _per_code(
        "fund_nav",
        codes,
        _token(token),
        fields="ts_code,nav_date,unit_nav,accum_nav,adj_nav,net_asset",
    )
    df.to_parquet(Path(parquet_root) / "etf_nav.parquet", index=False)
    return df


def update_etf_share(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉 fund_share (份额变动), 写 etf_share.parquet.

    Columns retained: ts_code, trade_date, fd_share.
    """
    df = _per_code(
        "fund_share",
        codes,
        _token(token),
        fields="ts_code,trade_date,fd_share",
    )
    df.to_parquet(Path(parquet_root) / "etf_share.parquet", index=False)
    return df


def update_etf_holdings(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉 fund_portfolio (持仓季报), 写 etf_holdings.parquet.

    Columns retained: ts_code, end_date, symbol, mkv, amount, stk_mkv_ratio.
    """
    df = _per_code(
        "fund_portfolio",
        codes,
        _token(token),
        fields="ts_code,end_date,symbol,mkv,amount,stk_mkv_ratio",
    )
    df.to_parquet(Path(parquet_root) / "etf_holdings.parquet", index=False)
    return df


def update_etf_div(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉 fund_div (分红), 写 etf_div.parquet.

    Columns retained: ts_code, ex_date, div_cash.
    """
    df = _per_code(
        "fund_div",
        codes,
        _token(token),
        fields="ts_code,ex_date,div_cash",
    )
    df.to_parquet(Path(parquet_root) / "etf_div.parquet", index=False)
    return df


def update_etf_index(
    parquet_root,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """拉跟踪指数日线 (index_daily), 由 etf_basic.index_code 驱动, 写 etf_index.parquet.

    需要先运行 ``update_etf_basic`` 生成 etf_basic.parquet.
    如果文件不存在或 index_code 列全空则返回空 DataFrame.
    """
    tok = _token(token)
    basic_f = Path(parquet_root) / "etf_basic.parquet"
    if not basic_f.exists():
        return pd.DataFrame()
    idx_codes = [
        c for c in pd.read_parquet(basic_f)["index_code"].dropna().unique()
    ]
    frames = []
    for ic in idx_codes:
        try:
            d = _fund_query(tok, "index_daily", ts_code=ic)
            if d is not None and not d.empty:
                frames.append(d)
        except Exception:
            pass
        time.sleep(0.12)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    out.to_parquet(Path(parquet_root) / "etf_index.parquet", index=False)
    return out


def update_all_fund(
    codes: List[str],
    parquet_root,
    token: Optional[str] = None,
) -> None:
    """全量更新: basic → nav → share → holdings → div → index (按顺序).

    index 依赖 basic 的 index_code 列, 因此 basic 必须先跑.
    """
    tok = _token(token)
    update_etf_basic(codes, parquet_root, tok)
    update_etf_nav(codes, parquet_root, tok)
    update_etf_share(codes, parquet_root, tok)
    update_etf_holdings(codes, parquet_root, tok)
    update_etf_div(codes, parquet_root, tok)
    update_etf_index(parquet_root, tok)
