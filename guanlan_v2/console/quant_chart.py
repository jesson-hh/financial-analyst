# -*- coding: utf-8 -*-
"""Deterministic single-security quant chart for a research workspace.

The module deliberately has no free-form HTTP client and no console/API
dependency.  It reads the configured formal daily loader and canonical local
CSI 300/XDXR stores.  Bounded calls through the existing trusted
``PytdxClient`` may verify/extend the latest stock day, CSI 300 day, and XDXR
history; every resulting ``current_ready`` badge is gated by overlap identity,
date alignment, and fresh corporate-action verification.

Public entry point: :func:`build_workspace_quant_chart`.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd


SCHEMA_VERSION = "guanlan.workspace-quant-chart.v1"
_BENCHMARKS = {"csi300": ("399300.SZ", "000300", 1, "沪深300")}


class QuantChartDataError(RuntimeError):
    """The formal data contract cannot support an honest result."""


def _iso_date(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _canonical_code(value: str) -> str:
    raw = str(value or "").strip().upper()
    m = re.fullmatch(r"(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?", raw)
    if not m:
        raise ValueError(f"unsupported A-share code: {value!r}")
    prefix, digits, suffix = m.groups()
    market = prefix or suffix
    if market is None:
        if digits.startswith(("0", "3")):
            market = "SZ"
        elif digits.startswith(("4", "8")) or digits.startswith("920"):
            market = "BJ"
        else:
            market = "SH"
    return f"{market}{digits}"


def _windows(values: Iterable[int]) -> tuple[int, ...]:
    out: list[int] = []
    for raw in values:
        value = int(raw)
        if value < 2 or value > 2000:
            raise ValueError("windows must be integers between 2 and 2000")
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError("at least one window is required")
    return tuple(out)


def _expected_latest_weekday(ref: date) -> date:
    """Weekend-safe expectation; unknown weekday holidays stay fail-closed."""
    while ref.weekday() >= 5:
        ref -= timedelta(days=1)
    return ref


def _normalise_ohlc(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    required = ("trade_date", "open", "high", "low", "close")
    if raw is None or raw.empty or not set(required).issubset(raw.columns):
        return pd.DataFrame(columns=required), 0 if raw is None else len(raw)
    frame = raw.loc[:, required].copy()
    before = len(frame)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    finite = np.isfinite(frame[["open", "high", "low", "close"]]).all(axis=1)
    positive = (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
    coherent = (
        (frame["high"] >= frame["low"])
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
    )
    frame = frame[frame["trade_date"].notna() & finite & positive & coherent]
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return frame.reset_index(drop=True), before - len(frame)


def _normalise_close(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    required = ("trade_date", "close")
    if raw is None or raw.empty or not set(required).issubset(raw.columns):
        return pd.DataFrame(columns=required), 0 if raw is None else len(raw)
    frame = raw.loc[:, required].copy()
    before = len(frame)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame[
        frame["trade_date"].notna()
        & np.isfinite(frame["close"])
        & (frame["close"] > 0)
    ]
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return frame.reset_index(drop=True), before - len(frame)


def _load_stock_daily(code: str, start: str, end: str) -> pd.DataFrame:
    from financial_analyst.data.loader_factory import get_default_loader

    return get_default_loader().fetch_quote(code, start, end, "day")


def _load_tdx_stock(code: str, count: int) -> pd.DataFrame:
    """Bounded raw-daily tail used only to extend a proven-identical formal feed."""
    from financial_analyst.data.updaters.pytdx_pool import PytdxClient, qlib_code_to_pytdx
    from pytdx.params import TDXParams

    market, number = qlib_code_to_pytdx(code)
    count = max(30, min(int(count), 800))
    with PytdxClient(max_retries=1, connect_timeout=1.5) as client:
        rows = client.call(
            "get_security_bars", TDXParams.KLINE_TYPE_DAILY,
            market, number, 0, count,
        )
    if not rows:
        raise QuantChartDataError("TDX stock tail returned no rows")
    cooked: list[dict[str, Any]] = []
    for row in rows:
        td = row.get("datetime")
        if not td and all(row.get(k) is not None for k in ("year", "month", "day")):
            td = f"{int(row['year']):04d}-{int(row['month']):02d}-{int(row['day']):02d}"
        cooked.append({"trade_date": td, **{
            key: row.get(key) for key in ("open", "high", "low", "close")
        }})
    return pd.DataFrame(cooked)


def _extend_raw_stock_from_tdx(formal: pd.DataFrame, tdx_raw: pd.DataFrame,
                               *, min_overlap: int = 5,
                               max_ratio_error: float = 0.001) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Append a TDX tail only after proving it is the same raw-price series.

    The formal Qlib stock feed on this installation stores raw prices.  A TDX
    tail is therefore accepted only when at least ``min_overlap`` recent closes
    match raw units (median ratio and every overlap within ``max_ratio_error``
    of 1).  Stable-but-differently-adjusted series are rejected: silently
    scaling them would make cash-dividend XDXR units ambiguous.
    """
    tdx, invalid = _normalise_ohlc(tdx_raw)
    meta: dict[str, Any] = {
        "attempted": True,
        "status": "rejected",
        "rows_added": 0,
        "invalid_rows_dropped": invalid,
        "min_overlap": min_overlap,
        "max_ratio_error_allowed": max_ratio_error,
    }
    if tdx.empty:
        raise QuantChartDataError("TDX stock tail empty after strict OHLC validation")
    overlap = formal[["trade_date", "close"]].merge(
        tdx[["trade_date", "close"]], on="trade_date", suffixes=("_formal", "_tdx"),
    ).tail(20)
    meta["overlap_n"] = len(overlap)
    meta["tdx_asof"] = _iso_date(tdx.iloc[-1]["trade_date"])
    if len(overlap) < min_overlap:
        raise QuantChartDataError(f"TDX stock tail overlap insufficient:{len(overlap)}/{min_overlap}")
    ratios = overlap["close_formal"].to_numpy(float) / overlap["close_tdx"].to_numpy(float)
    median = float(np.median(ratios))
    maximum_error = float(np.max(np.abs(ratios - 1.0)))
    meta.update({"close_ratio_median": median, "max_ratio_error": maximum_error})
    if (not math.isfinite(median) or abs(median - 1.0) > max_ratio_error
            or maximum_error > max_ratio_error):
        raise QuantChartDataError(
            f"TDX/formal stock raw-price identity check failed: median={median:.8f}, "
            f"max_error={maximum_error:.8f}"
        )
    formal_asof = pd.Timestamp(formal.iloc[-1]["trade_date"])
    missing = tdx[tdx["trade_date"] > formal_asof]
    if missing.empty:
        meta["status"] = "verified_no_new_rows"
        return formal, meta
    combined = pd.concat([formal, missing], ignore_index=True)
    combined = combined.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    meta.update({"status": "extended", "rows_added": len(missing)})
    return combined.reset_index(drop=True), meta


def _load_local_benchmark(benchmark: str) -> pd.DataFrame:
    if benchmark not in _BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark!r}")
    from financial_analyst.data.paths import get_data_paths

    ts_code = _BENCHMARKS[benchmark][0]
    path = get_data_paths().parquet_root / "etf_index.parquet"
    raw = pd.read_parquet(path, columns=["ts_code", "trade_date", "close"])
    return raw.loc[raw["ts_code"] == ts_code, ["trade_date", "close"]]


def _load_tdx_benchmark(benchmark: str, count: int) -> pd.DataFrame:
    """Trusted, bounded fallback through the engine's TDX connection pool."""
    if benchmark not in _BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark!r}")
    from financial_analyst.data.updaters.pytdx_pool import PytdxClient
    from pytdx.params import TDXParams

    _ts_code, number, market, _name = _BENCHMARKS[benchmark]
    count = max(200, min(int(count), 800))
    # Bound the optional network seam.  ``PytdxClient`` still walks its fixed
    # known-good host list, but performs no second request retry.
    with PytdxClient(max_retries=1, connect_timeout=1.5) as client:
        rows = client.call(
            "get_index_bars", TDXParams.KLINE_TYPE_DAILY,
            market, number, 0, count,
        )
    if not rows:
        raise QuantChartDataError("TDX index fallback returned no rows")
    cooked: list[dict[str, Any]] = []
    for row in rows:
        td = row.get("datetime")
        if not td and all(row.get(k) is not None for k in ("year", "month", "day")):
            td = f"{int(row['year']):04d}-{int(row['month']):02d}-{int(row['day']):02d}"
        cooked.append({"trade_date": td, **{k: row.get(k) for k in ("open", "high", "low", "close")}})
    frame, invalid = _normalise_ohlc(pd.DataFrame(cooked))
    if frame.empty or invalid:
        raise QuantChartDataError(f"TDX index fallback failed strict OHLC validation ({invalid} bad rows)")
    return frame[["trade_date", "close"]]


def _verify_benchmark_identity(local: pd.DataFrame, tdx: pd.DataFrame,
                               *, min_overlap: int = 5,
                               max_ratio_error: float = 0.001) -> dict[str, Any]:
    """Prove the TDX endpoint is the same canonical benchmark price series."""
    overlap = local[["trade_date", "close"]].merge(
        tdx[["trade_date", "close"]], on="trade_date", suffixes=("_local", "_tdx"),
    ).tail(30)
    meta: dict[str, Any] = {
        "verified": False,
        "overlap_n": len(overlap),
        "min_overlap": min_overlap,
        "max_ratio_error_allowed": max_ratio_error,
    }
    if len(overlap) < min_overlap:
        raise QuantChartDataError(
            f"TDX/local benchmark overlap insufficient:{len(overlap)}/{min_overlap}"
        )
    ratios = overlap["close_local"].to_numpy(float) / overlap["close_tdx"].to_numpy(float)
    median = float(np.median(ratios))
    maximum_error = float(np.max(np.abs(ratios - 1.0)))
    meta.update({"close_ratio_median": median, "max_ratio_error": maximum_error})
    if (not math.isfinite(median) or abs(median - 1.0) > max_ratio_error
            or maximum_error > max_ratio_error):
        raise QuantChartDataError(
            f"TDX/local benchmark identity check failed: median={median:.8f}, "
            f"max_error={maximum_error:.8f}"
        )
    meta["verified"] = True
    return meta


def _load_xdxr(code: str) -> pd.DataFrame:
    from financial_analyst.data.paths import get_data_paths

    path = get_data_paths().parquet_root / "xdxr.parquet"
    cols = ["code", "event_date", "category", "fenhong", "peigujia", "songzhuangu", "peigu"]
    raw = pd.read_parquet(path, columns=cols)
    return raw.loc[raw["code"].astype(str).str.upper() == code, cols]


def _load_tdx_xdxr(code: str) -> pd.DataFrame:
    """Fetch one security's complete XDXR history through trusted Pytdx."""
    from financial_analyst.data.updaters.pytdx_pool import (
        PytdxClient,
        qlib_code_to_pytdx,
    )

    market, number = qlib_code_to_pytdx(code)
    with PytdxClient(max_retries=1, connect_timeout=1.5) as client:
        rows = client.call("get_xdxr_info", market, number)
    if not rows:
        raise QuantChartDataError("TDX XDXR verification returned no rows")
    cooked: list[dict[str, Any]] = []
    for raw in rows:
        try:
            year, month, day = (int(raw.get(k) or 0) for k in ("year", "month", "day"))
            event_date = pd.Timestamp(year=year, month=month, day=day).normalize()
        except (TypeError, ValueError):
            continue
        item: dict[str, Any] = {
            "code": code,
            "event_date": event_date,
            "category": pd.to_numeric(raw.get("category"), errors="coerce"),
        }
        for field in ("fenhong", "peigujia", "songzhuangu", "peigu"):
            value = pd.to_numeric(raw.get(field), errors="coerce")
            item[field] = float(value) if pd.notna(value) else 0.0
        cooked.append(item)
    frame = pd.DataFrame(cooked)
    if frame.empty or frame["category"].isna().all():
        raise QuantChartDataError("TDX XDXR verification contained no valid events")
    return frame


def _merge_xdxr(local: pd.DataFrame, verified: pd.DataFrame, code: str) -> pd.DataFrame:
    """Merge full-history XDXR rows on the updater's documented primary key."""
    cols = ["code", "event_date", "category", "fenhong", "peigujia", "songzhuangu", "peigu"]
    pieces = []
    for raw in (local, verified):
        if raw is None or raw.empty:
            continue
        part = raw.copy()
        for col in cols:
            if col not in part:
                part[col] = code if col == "code" else 0.0
        part = part.loc[:, cols]
        part["code"] = code
        part["event_date"] = pd.to_datetime(part["event_date"], errors="coerce").dt.normalize()
        part["category"] = pd.to_numeric(part["category"], errors="coerce")
        part = part[part["event_date"].notna() & part["category"].notna()]
        for field in ("fenhong", "peigujia", "songzhuangu", "peigu"):
            part[field] = pd.to_numeric(part[field], errors="coerce").fillna(0.0)
        pieces.append(part)
    if not pieces:
        return pd.DataFrame(columns=cols)
    # ``verified`` is concatenated last and therefore wins corrected values for
    # an existing (code,event_date,category) event.
    return (pd.concat(pieces, ignore_index=True)
            .drop_duplicates(["code", "event_date", "category"], keep="last")
            .sort_values(["event_date", "category"]).reset_index(drop=True))


def _qfq_from_xdxr(raw: pd.DataFrame, xdxr: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build a latest-price-anchored, total-return-continuous QFQ OHLC series.

    Pytdx XDXR fields are expressed per ten shares.  On an ex-date the return
    denominator is the exchange-style ex-right reference price::

      (previous_close - cash/10 + rights_price*(rights/10))
      / (1 + bonus/10 + rights/10)

    This keeps dividends/splits out of the economic daily return while the last
    adjusted close remains exactly the latest formal raw close.
    """
    if raw.empty:
        raise QuantChartDataError("formal stock daily series is empty")
    if xdxr is None or xdxr.empty:
        raise QuantChartDataError("XDXR coverage for stock is absent; refusing to label raw prices as adjusted")

    events = xdxr.copy()
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce").dt.normalize()
    events["category"] = pd.to_numeric(events["category"], errors="coerce")
    events = events[events["event_date"].notna() & (events["category"] == 1)]
    for col in ("fenhong", "peigujia", "songzhuangu", "peigu"):
        events[col] = pd.to_numeric(events[col], errors="coerce").fillna(0.0)

    grouped: dict[pd.Timestamp, dict[str, float]] = {}
    for td, part in events.groupby("event_date"):
        rights = float(part["peigu"].sum()) / 10.0
        weighted_price = 0.0
        if rights > 0:
            weights = part["peigu"].clip(lower=0)
            weighted_price = float(np.average(part["peigujia"], weights=weights)) if weights.sum() else 0.0
        grouped[pd.Timestamp(td)] = {
            "cash": float(part["fenhong"].sum()) / 10.0,
            "bonus": float(part["songzhuangu"].sum()) / 10.0,
            "rights": rights,
            "rights_price": weighted_price,
        }

    frame = raw.copy().reset_index(drop=True)
    closes = frame["close"].to_numpy(dtype=float)
    returns = np.full(len(frame), np.nan, dtype=float)
    applied = 0
    invalid_events: list[str] = []
    for i in range(1, len(frame)):
        prev = closes[i - 1]
        denominator = prev
        event = grouped.get(pd.Timestamp(frame.loc[i, "trade_date"]))
        if event:
            denominator = (
                prev - event["cash"] + event["rights_price"] * event["rights"]
            ) / (1.0 + event["bonus"] + event["rights"])
            if not math.isfinite(denominator) or denominator <= 0:
                invalid_events.append(_iso_date(frame.loc[i, "trade_date"]))
                continue
            applied += 1
        returns[i] = closes[i] / denominator - 1.0
    if invalid_events:
        raise QuantChartDataError(f"invalid XDXR adjustment events: {', '.join(invalid_events)}")

    wealth = np.ones(len(frame), dtype=float)
    for i in range(1, len(frame)):
        r = returns[i]
        if not math.isfinite(r) or r <= -1.0:
            raise QuantChartDataError(f"invalid adjusted return on {_iso_date(frame.loc[i, 'trade_date'])}")
        wealth[i] = wealth[i - 1] * (1.0 + r)
    qfq_close = wealth / wealth[-1] * closes[-1]
    factors = qfq_close / closes
    for col in ("open", "high", "low", "close"):
        frame[f"qfq_{col}"] = frame[col].to_numpy(dtype=float) * factors
    loaded_start = pd.Timestamp(frame.iloc[0]["trade_date"])
    loaded_end = pd.Timestamp(frame.iloc[-1]["trade_date"])
    in_loaded_window = sum(loaded_start <= td <= loaded_end for td in grouped)
    return frame, {
        "method": "qfq_total_return_xdxr_v1",
        "price_anchor": "latest_trusted_raw_close",
        "xdxr_unit": "per_10_shares",
        "coverage_ready": True,
        "code_event_dates_total": len(grouped),
        "events_in_loaded_window": in_loaded_window,
        "events_applied": applied,
    }


def _metric_row(aligned: pd.DataFrame, window: int) -> dict[str, Any]:
    returns = aligned[["trade_date", "stock", "benchmark"]].copy()
    returns[["stock", "benchmark"]] = returns[["stock", "benchmark"]].pct_change()
    returns = returns.dropna()
    actual = min(window, len(returns))
    base = {
        "window": window,
        "available": False,
        "n": actual,
        "required_n": window,
        "asof": _iso_date(returns.iloc[-1]["trade_date"]) if len(returns) else None,
        "corr": None,
        "alpha_daily": None,
        "beta": None,
        "r2": None,
    }
    if len(returns) < window:
        base["reason"] = f"insufficient_aligned_returns:{len(returns)}/{window}"
        return base
    tail = returns.tail(window)
    y = tail["stock"].to_numpy(dtype=float)
    x = tail["benchmark"].to_numpy(dtype=float)
    x_centered = x - x.mean()
    denom = float(np.dot(x_centered, x_centered))
    if denom <= np.finfo(float).eps:
        base["reason"] = "zero_benchmark_variance"
        return base
    beta = float(np.dot(x_centered, y - y.mean()) / denom)
    alpha = float(y.mean() - beta * x.mean())
    fitted = alpha + beta * x
    sst = float(np.dot(y - y.mean(), y - y.mean()))
    corr = float(np.corrcoef(x, y)[0, 1]) if sst > 0 else None
    r2 = float(1.0 - np.dot(y - fitted, y - fitted) / sst) if sst > 0 else None
    base.update({
        "available": True,
        "n": window,
        "asof": _iso_date(tail.iloc[-1]["trade_date"]),
        "corr": corr,
        "alpha_daily": alpha,
        "beta": beta,
        "r2": r2,
    })
    return base


def _level_row(stock: pd.DataFrame, window: int) -> dict[str, Any]:
    actual = min(window, len(stock))
    base = {
        "window": window,
        "available": False,
        "n": actual,
        "required_n": window,
        "asof": _iso_date(stock.iloc[-1]["trade_date"]) if len(stock) else None,
        "label": f"unavailable ({actual}/{window} formal sessions)",
        "support": None,
        "resistance": None,
    }
    if len(stock) < window:
        base["reason"] = f"insufficient_formal_price_bars:{len(stock)}/{window}"
        return base
    tail = stock.tail(window)
    base.update({
        "available": True,
        "n": window,
        "label": f"{window}-session adjusted range",
        "support": float(tail["qfq_low"].min()),
        "resistance": float(tail["qfq_high"].max()),
        "last_close": float(tail.iloc[-1]["qfq_close"]),
    })
    return base


def _cluster_levels(candidates: list[tuple[float, float, str]], *,
                    relative_gap: float = 0.012) -> list[dict[str, Any]]:
    """Merge nearby technical levels without pretending to know precision.

    A single daily high/low is noisy.  Nearby pivots, moving averages and
    recent bar edges are therefore grouped into zones; the returned price is a
    weighted centre and ``sources`` preserves why the zone exists.
    """
    clean = sorted(
        (float(price), float(weight), str(source))
        for price, weight, source in candidates
        if math.isfinite(float(price)) and float(price) > 0 and float(weight) > 0
    )
    groups: list[list[tuple[float, float, str]]] = []
    for item in clean:
        if not groups:
            groups.append([item])
            continue
        centre = sum(p * w for p, w, _ in groups[-1]) / sum(w for _, w, _ in groups[-1])
        if abs(item[0] / centre - 1.0) <= relative_gap:
            groups[-1].append(item)
        else:
            groups.append([item])
    out: list[dict[str, Any]] = []
    for group in groups:
        total = sum(weight for _, weight, _ in group)
        out.append({
            "price": sum(price * weight for price, weight, _ in group) / total,
            "strength": total,
            "sources": sorted({source for _, _, source in group}),
            "zone_low": min(price for price, _, _ in group),
            "zone_high": max(price for price, _, _ in group),
        })
    return out


def _position_levels(stock: pd.DataFrame, lookback: int = 120) -> dict[str, Any]:
    """Nearest evidence-backed support/resistance zones around latest close.

    Window extrema remain available in ``levels`` for auditability, but they
    are not automatically useful *current* levels after a sharp rally.  This
    view combines local pivots, short/medium moving averages and recent bar
    edges, clusters nearby values, then selects the nearest zones on each side
    of the latest formal close.
    """
    tail = stock.tail(max(5, int(lookback))).reset_index(drop=True)
    if tail.empty:
        return {"available": False, "reason": "no_adjusted_price_bars"}
    current = float(tail.iloc[-1]["qfq_close"])
    candidates: list[tuple[float, float, str]] = []
    for window in (5, 10, 20, 60):
        if len(tail) >= window:
            value = float(tail["qfq_close"].tail(window).mean())
            candidates.append((value, 2.0, f"MA{window}"))
    # Confirmed local pivots get the highest weight.  Do not use the final two
    # bars as confirmed pivots because they have no right-hand confirmation.
    span = 2
    for idx in range(span, len(tail) - span):
        neighbourhood = tail.iloc[idx - span:idx + span + 1]
        low = float(tail.iloc[idx]["qfq_low"])
        high = float(tail.iloc[idx]["qfq_high"])
        if low <= float(neighbourhood["qfq_low"].min()):
            candidates.append((low, 3.0, "pivot_low"))
        if high >= float(neighbourhood["qfq_high"].max()):
            candidates.append((high, 3.0, "pivot_high"))
    # Recent edges catch breakout/retest levels that are not old enough to be
    # confirmed pivots.  Their low weight prevents one bar from dominating.
    for _, row in tail.tail(min(5, len(tail))).iterrows():
        candidates.append((float(row["qfq_low"]), 1.0, "recent_low"))
        candidates.append((float(row["qfq_high"]), 1.0, "recent_high"))

    clusters = _cluster_levels(candidates)
    epsilon = max(current * 0.001, 1e-8)
    supports = sorted(
        (item for item in clusters if item["price"] < current - epsilon),
        key=lambda item: item["price"], reverse=True,
    )[:2]
    resistances = sorted(
        (item for item in clusters if item["price"] > current + epsilon),
        key=lambda item: item["price"],
    )[:2]
    return {
        "available": bool(supports or resistances),
        "asof": _iso_date(tail.iloc[-1]["trade_date"]),
        "last_close": current,
        "lookback_sessions": len(tail),
        "supports": supports,
        "resistances": resistances,
        "range_low": float(tail["qfq_low"].min()),
        "range_high": float(tail["qfq_high"].max()),
        "method": "clustered pivots + MA5/10/20/60 + recent 5-bar edges",
    }


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def _render_png(result: dict[str, Any], stock: pd.DataFrame, path: Path) -> None:
    from PIL import Image, ImageDraw

    width, height = 1600, 980
    image = Image.new("RGB", (width, height), "#f7f9fc")
    draw = ImageDraw.Draw(image)
    title_font, h_font, body_font, small_font = _font(31, True), _font(20, True), _font(17), _font(14)
    dark, muted, grid = "#172033", "#667085", "#d9e1ec"

    subject = result["subject"]
    draw.text((48, 30), f"{subject['workspace_subject']} · {subject['code']} 量化位置", fill=dark, font=title_font)
    fresh = result["freshness"]
    badge = "CURRENT READY" if fresh["current_ready"] else "HISTORICAL / NOT CURRENT"
    badge_color = "#16794a" if fresh["current_ready"] else "#b42318"
    draw.rounded_rectangle((1190, 28, 1550, 72), radius=12, fill=badge_color)
    draw.text((1210, 39), badge, fill="white", font=body_font)

    # Fixed chart and fixed legend panel: labels never compete with price lines.
    x0, y0, x1, y1 = 70, 115, 1110, 610
    draw.rounded_rectangle((42, 92, 1140, 650), radius=14, fill="white", outline=grid)
    show = stock.tail(max(result["requested_windows"]))
    if len(show):
        lo = float(show["qfq_low"].min())
        hi = float(show["qfq_high"].max())
        pad = max((hi - lo) * 0.08, hi * 0.005)
        lo, hi = lo - pad, hi + pad
        for i in range(6):
            yy = y0 + (y1 - y0) * i / 5
            draw.line((x0, yy, x1, yy), fill=grid, width=1)
            draw.text((x0 + 5, yy + 3), f"{hi - (hi-lo)*i/5:.2f}", fill=muted, font=small_font)
        pts = []
        for i, value in enumerate(show["qfq_close"]):
            xx = x0 + (x1 - x0) * i / max(1, len(show) - 1)
            yy = y1 - (float(value) - lo) / (hi - lo) * (y1 - y0)
            pts.append((xx, yy))
        if len(pts) > 1:
            draw.line(pts, fill="#175cd3", width=4)
        position_levels = result.get("position_levels") or {}
        for side, color in (("supports", "#12b76a"), ("resistances", "#f04438")):
            for zone in position_levels.get(side) or []:
                value = float(zone["price"])
                if lo <= value <= hi:
                    yy = y1 - (value - lo) / (hi - lo) * (y1 - y0)
                    draw.line((x0, yy, x1, yy), fill=color, width=3)
        series_label = (
            "formal + verified TDX tail, adjusted"
            if result.get("position_source") == "formal_daily_plus_verified_tdx_tail_adjusted_close"
            else "formal adjusted prices"
        )
        draw.text((x0, 620), f"{series_label} · as of {result['analysis_asof']}",
                  fill=muted, font=small_font)
    else:
        draw.text((x0 + 300, y0 + 220), "No adjusted price series", fill="#b42318", font=h_font)

    # Level legend in its own non-overlapping panel.
    draw.rounded_rectangle((1165, 92, 1558, 650), radius=14, fill="white", outline=grid)
    draw.text((1190, 116), "当前关键位（复权）", fill=dark, font=h_font)
    position_levels = result.get("position_levels") or {}
    yy = 164
    for label, key, color in (("支撑", "supports", "#16794a"),
                              ("压力", "resistances", "#b42318")):
        zones = position_levels.get(key) or []
        draw.text((1190, yy), label, fill=color, font=body_font)
        yy += 32
        if not zones:
            draw.text((1206, yy), "未形成可确认区域", fill=muted, font=small_font)
            yy += 48
            continue
        for zone in zones:
            sources = "+".join(zone.get("sources") or [])
            draw.text((1206, yy), f"{_fmt(zone['price'], 2)}  {sources}"[:42],
                      fill=dark, font=small_font)
            yy += 32
        yy += 16
    draw.text((1190, 450), "区间边界（审计，不等同当前关键位）", fill=muted, font=small_font)
    yy = 478
    for level in result["levels"]:
        if level["available"]:
            text = f"{level['window']}日  {_fmt(level['support'], 2)} – {_fmt(level['resistance'], 2)}"
        else:
            text = f"{level['window']}日  unavailable {level['n']}/{level['required_n']}"
        draw.text((1206, yy), text, fill=muted, font=small_font)
        yy += 26
    draw.text((1190, 540), f"价格截止  {result['analysis_asof']}", fill=muted, font=small_font)
    draw.text((1190, 570), f"共同截止  {result['common_cutoff'] or '—'}", fill=muted, font=small_font)
    draw.text((1190, 600), f"基准截止  {result['benchmark_asof']}", fill=muted, font=small_font)

    # Regression table.
    draw.rounded_rectangle((42, 680, 1558, 934), radius=14, fill="white", outline=grid)
    draw.text((70, 704), "相对沪深300：日简单收益 OLS（无风险利率固定为 0）", fill=dark, font=h_font)
    cols = [(80, "窗口"), (260, "状态 / n"), (520, "相关性"), (735, "Alpha（日）"), (980, "Beta"), (1200, "R²"), (1390, "截至")]
    for xx, label in cols:
        draw.text((xx, 750), label, fill=muted, font=small_font)
    yy = 786
    for metric in result["regressions"]:
        values = [
            f"{metric['window']}日",
            f"available · {metric['n']}" if metric["available"] else metric.get("reason", "unavailable"),
            _fmt(metric["corr"]), _fmt(metric["alpha_daily"], 6), _fmt(metric["beta"]),
            _fmt(metric["r2"]), metric["asof"] or "—",
        ]
        for (xx, _), value in zip(cols, values):
            draw.text((xx, yy), value, fill=dark if metric["available"] else "#b42318", font=body_font)
        yy += 42
    if fresh["warnings"]:
        warning = "；".join(fresh["warnings"])
        draw.text((70, 910), warning[:150], fill="#b42318", font=small_font)
    image.save(path, format="PNG", optimize=True)


def build_workspace_quant_chart(
    workspace_subject: str,
    code: str,
    *,
    output_dir: str | Path,
    benchmark: str = "csi300",
    windows: Sequence[int] = (20, 60, 120),
    reference_date: str | date | datetime | None = None,
    allow_tdx_stock_extension: bool = True,
    allow_tdx_index_fallback: bool = True,
    allow_tdx_xdxr_refresh: bool = True,
    freshness_tolerance_calendar_days: int = 4,
    stock_loader: Callable[[str, str, str], pd.DataFrame] | None = None,
    tdx_stock_loader: Callable[[str, int], pd.DataFrame] | None = None,
    local_benchmark_loader: Callable[[str], pd.DataFrame] | None = None,
    tdx_benchmark_loader: Callable[[str, int], pd.DataFrame] | None = None,
    xdxr_loader: Callable[[str], pd.DataFrame] | None = None,
    tdx_xdxr_loader: Callable[[str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Create one PNG and one JSON and return their relative artifact paths.

    ``current_ready`` is deliberately strict: the adjusted stock series and
    benchmark must end on the same latest market date, the benchmark may not be
    clearly behind the expected weekday, and XDXR adjustment must be available.
    Historical metrics are still emitted when this flag is false, always with a
    visible common cutoff.
    """
    canonical = _canonical_code(code)
    requested = _windows(windows)
    if benchmark not in _BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark!r}")
    if freshness_tolerance_calendar_days < 0:
        raise ValueError("freshness_tolerance_calendar_days must be non-negative")
    ref = pd.Timestamp(reference_date or date.today()).date()
    expected = _expected_latest_weekday(ref)
    start = ref - timedelta(days=max(730, max(requested) * 4))

    stock_injected = stock_loader is not None
    tdx_stock_injected = tdx_stock_loader is not None
    local_benchmark_injected = local_benchmark_loader is not None
    tdx_benchmark_injected = tdx_benchmark_loader is not None
    xdxr_injected = xdxr_loader is not None
    tdx_xdxr_injected = tdx_xdxr_loader is not None
    stock_loader = stock_loader or _load_stock_daily
    tdx_stock_loader = tdx_stock_loader or _load_tdx_stock
    local_benchmark_loader = local_benchmark_loader or _load_local_benchmark
    tdx_benchmark_loader = tdx_benchmark_loader or _load_tdx_benchmark
    xdxr_loader = xdxr_loader or _load_xdxr
    tdx_xdxr_loader = tdx_xdxr_loader or _load_tdx_xdxr
    raw_stock, stock_invalid = _normalise_ohlc(
        stock_loader(canonical, start.isoformat(), ref.isoformat())
    )
    if raw_stock.empty:
        raise QuantChartDataError(f"no formal daily bars for {canonical}")
    raw_stock = raw_stock[raw_stock["trade_date"].dt.date <= ref].reset_index(drop=True)
    if raw_stock.empty:
        raise QuantChartDataError(f"no formal daily bars for {canonical} at or before {ref}")
    formal_stock_rows = len(raw_stock)
    formal_stock_asof = _iso_date(raw_stock.iloc[-1]["trade_date"])
    stock_extension: dict[str, Any] | None = None
    stock_extension_error: str | None = None
    stock_latest_verified_by_tdx = False
    if (allow_tdx_stock_extension and (not stock_injected or tdx_stock_injected)
            and raw_stock.iloc[-1]["trade_date"].date() < expected):
        try:
            raw_stock, stock_extension = _extend_raw_stock_from_tdx(
                raw_stock, tdx_stock_loader(canonical, max(requested) + 40),
            )
            raw_stock = raw_stock[raw_stock["trade_date"].dt.date <= ref].reset_index(drop=True)
            stock_latest_verified_by_tdx = bool(stock_extension)
        except Exception as exc:  # noqa: BLE001 - formal series stays authoritative
            stock_extension_error = f"tdx_stock_extension_error:{type(exc).__name__}:{exc}"
    xdxr_refresh_error: str | None = None
    xdxr_verified_by_tdx = bool(xdxr_injected and not tdx_xdxr_injected)
    try:
        local_xdxr = xdxr_loader(canonical)
    except Exception as exc:  # noqa: BLE001 - trusted TDX may still supply full history
        local_xdxr = pd.DataFrame()
        xdxr_refresh_error = f"local_xdxr_error:{type(exc).__name__}:{exc}"
    xdxr = local_xdxr
    if (allow_tdx_xdxr_refresh and (not xdxr_injected or tdx_xdxr_injected)):
        try:
            verified_xdxr = tdx_xdxr_loader(canonical)
            xdxr = _merge_xdxr(local_xdxr, verified_xdxr, canonical)
            xdxr_verified_by_tdx = True
            xdxr_refresh_error = None
        except Exception as exc:  # noqa: BLE001 - retain local history, badge historical
            xdxr_refresh_error = f"tdx_xdxr_error:{type(exc).__name__}:{exc}"
    stock, adjustment = _qfq_from_xdxr(raw_stock, xdxr)

    fallback_error: str | None = None
    local_invalid = 0
    try:
        local, local_invalid = _normalise_close(local_benchmark_loader(benchmark))
        if len(local):
            local = local[local["trade_date"].dt.date <= ref].reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001 - fallback is explicit and badged
        local = pd.DataFrame(columns=["trade_date", "close"])
        fallback_error = f"local_benchmark_error:{type(exc).__name__}:{exc}"
    local_asof = _iso_date(local.iloc[-1]["trade_date"]) if len(local) else None
    needs_fallback = not len(local) or pd.Timestamp(local.iloc[-1]["trade_date"]).date() < expected
    benchmark_frame = local
    benchmark_source = (
        "injected_local_benchmark_loader" if local_benchmark_injected
        else "etf_index.parquet:399300.SZ"
    )
    benchmark_latest_verified_by_tdx = False
    benchmark_identity: dict[str, Any] = {
        "verified": bool(len(local)),
        "source": "canonical_local" if len(local) else "unverified_no_local_overlap",
    }
    if needs_fallback and allow_tdx_index_fallback:
        try:
            tdx, _ = _normalise_close(tdx_benchmark_loader(benchmark, max(requested) + 80))
            if not len(tdx):
                raise QuantChartDataError("TDX index fallback returned no valid rows")
            tdx = tdx[tdx["trade_date"].dt.date <= ref].reset_index(drop=True)
            if not len(tdx):
                raise QuantChartDataError("TDX index fallback empty after reference-date clipping")
            if len(local) and tdx.iloc[-1]["trade_date"] < local.iloc[-1]["trade_date"]:
                raise QuantChartDataError("TDX index fallback is older than local benchmark")
            if len(local):
                benchmark_identity = _verify_benchmark_identity(local, tdx)
                benchmark_identity["source"] = "local_tdx_overlap"
            # A live endpoint returning the same old date as local storage does
            # not prove that the missing weekday was a holiday; it may simply
            # be stale as well.  Without an authoritative exchange calendar we
            # only call the latest date verified when TDX reaches ``expected``.
            benchmark_latest_verified_by_tdx = (
                pd.Timestamp(tdx.iloc[-1]["trade_date"]).date() == expected
                and bool(benchmark_identity.get("verified"))
            )
            if not len(local) or tdx.iloc[-1]["trade_date"] > local.iloc[-1]["trade_date"]:
                benchmark_frame = tdx
                benchmark_source = (
                    "injected_tdx_benchmark_loader" if tdx_benchmark_injected
                    else "tdx_index_daily:get_index_bars"
                )
            fallback_error = None
        except Exception as exc:  # noqa: BLE001 - retain stale local, surface reason
            fallback_error = f"tdx_index_fallback_error:{type(exc).__name__}:{exc}"
    if benchmark_frame.empty:
        raise QuantChartDataError(f"benchmark unavailable ({fallback_error or 'no rows'})")

    stock_asof = pd.Timestamp(stock.iloc[-1]["trade_date"])
    benchmark_asof_ts = pd.Timestamp(benchmark_frame.iloc[-1]["trade_date"])
    aligned = stock[["trade_date", "qfq_close"]].merge(
        benchmark_frame[["trade_date", "close"]], on="trade_date", how="inner"
    ).rename(columns={"qfq_close": "stock", "close": "benchmark"})
    aligned = aligned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    common_cutoff = pd.Timestamp(aligned.iloc[-1]["trade_date"]) if len(aligned) else None

    lag_days = max(0, (expected - benchmark_asof_ts.date()).days)
    warnings: list[str] = []
    if stock_extension_error:
        warnings.append(stock_extension_error)
    if xdxr_refresh_error:
        warnings.append(xdxr_refresh_error)
    if fallback_error:
        warnings.append(fallback_error)
    if not benchmark_identity.get("verified"):
        warnings.append("benchmark identity is unverified because canonical overlap is unavailable")
    if stock_asof != benchmark_asof_ts:
        warnings.append(
            f"formal stock as-of {_iso_date(stock_asof)} != benchmark as-of {_iso_date(benchmark_asof_ts)}"
        )
    if lag_days > freshness_tolerance_calendar_days:
        warnings.append(f"benchmark behind expected weekday by {lag_days} calendar days")
    elif benchmark_asof_ts.date() != expected:
        warnings.append(
            f"benchmark as-of {_iso_date(benchmark_asof_ts)} is not expected latest weekday {expected.isoformat()}"
        )
    if not len(aligned):
        warnings.append("stock and benchmark have no common formal date")
    current_ready = bool(
        len(aligned)
        and stock_asof == benchmark_asof_ts == common_cutoff
        and benchmark_asof_ts.date() == expected
        and bool(benchmark_identity.get("verified"))
        and adjustment["coverage_ready"]
        and xdxr_verified_by_tdx
    )

    levels = [_level_row(stock, w) for w in requested]
    position_levels = _position_levels(stock, lookback=max(requested))
    regressions = [_metric_row(aligned, w) for w in requested]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "subject": {"workspace_subject": str(workspace_subject), "code": canonical},
        "benchmark": {"id": benchmark, "name": _BENCHMARKS[benchmark][3]},
        "requested_windows": list(requested),
        "position_price": position_levels.get("last_close"),
        "position_asof": position_levels.get("asof"),
        "position_source": (
            "formal_daily_plus_verified_tdx_tail_adjusted_close"
            if stock_extension and stock_extension.get("status") == "extended"
            else "formal_adjusted_daily_close"
        ),
        "analysis_asof": _iso_date(stock_asof),
        "benchmark_asof": _iso_date(benchmark_asof_ts),
        "common_cutoff": _iso_date(common_cutoff) if common_cutoff is not None else None,
        "current_ready": current_ready,
        "freshness": {
            "reference_date": ref.isoformat(),
            "expected_latest_weekday": expected.isoformat(),
            "stock_asof": _iso_date(stock_asof),
            "local_benchmark_asof": local_asof,
            "benchmark_asof": _iso_date(benchmark_asof_ts),
            "common_cutoff": _iso_date(common_cutoff) if common_cutoff is not None else None,
            "benchmark_lag_calendar_days": lag_days,
            "tolerance_calendar_days": freshness_tolerance_calendar_days,
            "sources_synchronized": stock_asof == benchmark_asof_ts,
            "stock_latest_verified_by_tdx": stock_latest_verified_by_tdx,
            "benchmark_latest_verified_by_tdx": benchmark_latest_verified_by_tdx,
            "xdxr_latest_verified_by_tdx": xdxr_verified_by_tdx,
            "current_ready": current_ready,
            "warnings": warnings,
        },
        "provenance": {
            "stock": "injected_stock_loader" if stock_injected else "default_loader:day",
            "stock_formal_asof": formal_stock_asof,
            "stock_extension": stock_extension,
            "stock_extension_source": (
                "injected_tdx_stock_loader" if tdx_stock_injected
                else "tdx_stock_daily:get_security_bars"
            ) if stock_extension else None,
            "stock_adjustment": adjustment,
            "xdxr": (
                "injected_xdxr_loader" if xdxr_injected
                else "parquet_root/xdxr.parquet + tdx_xdxr:get_xdxr_info"
            ),
            "xdxr_refresh_source": (
                "injected_tdx_xdxr_loader" if tdx_xdxr_injected
                else "tdx_xdxr:get_xdxr_info"
            ) if xdxr_verified_by_tdx else None,
            "xdxr_refresh_error": xdxr_refresh_error,
            "benchmark": benchmark_source,
            "benchmark_identity": benchmark_identity,
            "network_policy": (
                "no_free_http; optional trusted PytdxClient "
                "get_security_bars/get_index_bars/get_xdxr_info only"
            ),
            "tdx_timeout_policy": "max_retries=1; connect_timeout=1.5s; fail closed to local benchmark",
        },
        "data_quality": {
            "stock_formal_rows": formal_stock_rows,
            "stock_tdx_rows_added": int((stock_extension or {}).get("rows_added") or 0),
            "stock_total_rows": len(stock),
            "stock_invalid_rows_dropped": stock_invalid,
            "benchmark_rows": len(benchmark_frame),
            "local_benchmark_invalid_rows_dropped": local_invalid,
            "aligned_price_rows": len(aligned),
        },
        "methodology": {
            "returns": "simple daily returns on strictly inner-joined price dates",
            "regression": "stock_return = alpha_daily + beta * benchmark_return + epsilon",
            "risk_free_rate": 0.0,
            "alpha_annualized": False,
            "levels": "min adjusted low / max adjusted high over complete formal-session windows",
            "position_levels": (
                "clustered pivots + MA5/10/20/60 + recent 5-session edges; "
                "derived from the trusted adjusted daily series"
            ),
        },
        "levels": levels,
        "position_levels": position_levels,
        "regressions": regressions,
        "artifacts": {},
    }

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{canonical}_quant"
    png_path = out / f"{stem}.png"
    json_path = out / f"{stem}.json"
    result["artifacts"] = {
        "png": png_path.name,
        "json": json_path.name,
    }
    _render_png(result, stock, png_path)
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return result


__all__ = ["QuantChartDataError", "build_workspace_quant_chart"]
