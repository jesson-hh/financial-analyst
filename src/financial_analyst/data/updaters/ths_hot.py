"""Tonghuashun (同花顺) hot-stock theme attribution updater — zero token.

Pulls 同花顺's editorial-tagged daily strong-stock list, one row per stock,
with the prized ``reason`` column (人工编辑部 tags like "AI算力+储能+一季报增长").

Appends one snapshot per ``date`` to ``ths_hot_daily.parquet`` so the agent
layer can both look at "today's hot list" and "this concept appeared in N out
of last M days" — a much stronger theme signal than LLM-inferred attribution.

Schema (one row per (code, date)):

    code             str    qlib upper-case
    date             date   snapshot trading day
    name             str    短简称
    reason           str    人工题材归因 tags (the value)
    market           str    "沪" / "深" / "北"
    zhangfu          float  涨幅 %
    close            float  收盘价 (元)
    zhangdie         float  涨跌额 (元)
    huanshou         float  换手率 %
    chengjiaoe       float  成交额 (元)
    chengjiaoliang   float  成交量 (股)
    ddejingliang     float  大单净量

API URL: ``zx.10jqka.com.cn/event/api/getharden/date/{YYYY-MM-DD}/...``.
Zero auth; needs a browser-like User-Agent header.

Inspired by ``simonlin1212/a-stock-data`` v2.0 endpoint discovery.
"""
from __future__ import annotations

import time
from datetime import date as _date
from pathlib import Path
from typing import Optional, Union

import pandas as pd
import requests


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_DEDUP_COLS = ["code", "date"]

# Source → our schema
_FIELD_MAP = {
    "code": "code_raw",   # 6-digit, we'll derive qlib code via market
    "name": "name",
    "reason": "reason",
    "market": "market",
    "zhangfu": "zhangfu",
    "close": "close",
    "zhangdie": "zhangdie",
    "huanshou": "huanshou",
    "chengjiaoe": "chengjiaoe",
    "chengjiaoliang": "chengjiaoliang",
    "ddejingliang": "ddejingliang",
}


def _safe_float(v) -> float:
    if v is None or v == "" or v == "-":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _market_to_qlib_prefix(market: str, code6: str) -> str:
    """Convert 同花顺 market label + 6-digit code to qlib ``SHxxxxxx`` etc."""
    if market == "沪":
        return f"SH{code6}"
    if market == "深":
        return f"SZ{code6}"
    if market == "北":
        return f"BJ{code6}"
    # Fallback by leading digit
    if code6[:1] in ("6", "9"):
        return f"SH{code6}"
    if code6[:1] in ("4", "8"):
        return f"BJ{code6}"
    return f"SZ{code6}"


def update_ths_hot(
    parquet_root: Union[str, Path],
    date: Optional[str] = None,
    timeout: float = 10.0,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull 同花顺 hot-stock list for ``date`` (YYYY-MM-DD), append to parquet.

    Re-runnable for the same date — dedups on ``(code, date)``. Re-runnable
    for a historical date as well (the endpoint accepts past dates), so this
    can also backfill missing days.
    """
    date = date or _date.today().strftime("%Y-%m-%d")
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "ths_hot_daily.parquet"

    if dry_run:
        n = 0
        if parquet_path.exists():
            try:
                n = len(pd.read_parquet(parquet_path, columns=["code"]))
            except Exception:
                pass
        return {
            "dry_run": True, "ok": True,
            "date": date, "rows_new": 0, "rows_total": n,
            "plan": f"Would pull 同花顺 hot list for {date} → {parquet_path} "
                    f"(existing {n} rows).",
            "parquet_path": str(parquet_path),
        }

    url = (f"http://zx.10jqka.com.cn/event/api/getharden/"
           f"date/{date}/orderby/date/orderway/desc/charset/GBK/")
    if progress:
        print(f"[ths_hot] pulling {date} ...", flush=True)
    t0 = time.time()
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        d = r.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"ok": False, "date": date, "rows_new": 0, "rows_total": 0,
                "error": f"{type(e).__name__}: {e}",
                "parquet_path": str(parquet_path)}

    errocode = d.get("errocode", 0)
    if errocode and errocode != 0:
        return {"ok": False, "date": date, "rows_new": 0, "rows_total": 0,
                "error": f"upstream errocode={errocode}: {d.get('errormsg', '')}",
                "parquet_path": str(parquet_path)}

    rows = d.get("data") or []
    if not rows:
        if progress:
            print(f"[ths_hot] empty for {date} (rest day / not yet available)",
                  flush=True)
        # Still return ok=True — empty for weekends / future dates is normal
        return {"ok": True, "date": date, "rows_new": 0,
                "rows_total": _row_count(parquet_path),
                "msg": "empty (rest day or not yet available)",
                "parquet_path": str(parquet_path)}

    out: list[dict] = []
    for r in rows:
        code6 = (r.get("code") or "").strip()
        if not code6:
            continue
        market = r.get("market") or ""
        rec = {
            "code": _market_to_qlib_prefix(market, code6),
            "date": pd.to_datetime(date).date(),
            "name": r.get("name") or "",
            "reason": r.get("reason") or "",
            "market": market,
            "zhangfu": _safe_float(r.get("zhangfu")),
            "close": _safe_float(r.get("close")),
            "zhangdie": _safe_float(r.get("zhangdie")),
            "huanshou": _safe_float(r.get("huanshou")),
            "chengjiaoe": _safe_float(r.get("chengjiaoe")),
            "chengjiaoliang": _safe_float(r.get("chengjiaoliang")),
            "ddejingliang": _safe_float(r.get("ddejingliang")),
        }
        out.append(rec)

    new_df = pd.DataFrame(out)
    if len(new_df) == 0:
        return {"ok": True, "date": date, "rows_new": 0,
                "rows_total": _row_count(parquet_path),
                "msg": "all rows lacked codes",
                "parquet_path": str(parquet_path)}

    parquet_root.mkdir(parents=True, exist_ok=True)
    old_df = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
    combined = (pd.concat([old_df, new_df], ignore_index=True)
                if len(old_df) > 0 else new_df)
    combined = (combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
                        .sort_values(["date", "code"]).reset_index(drop=True))
    tmp = parquet_path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if parquet_path.exists():
        parquet_path.unlink()
    tmp.rename(parquet_path)

    if progress:
        print(f"[ths_hot ✓] {date}: {len(new_df)} hot stocks (parquet total: "
              f"{len(combined)}) 耗时 {time.time()-t0:.1f}s", flush=True)

    return {
        "ok": True,
        "date": date,
        "rows_new": int(len(new_df)),
        "rows_total": int(len(combined)),
        "parquet_path": str(parquet_path),
    }


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_parquet(path, columns=["code"]))
    except Exception:
        return 0
