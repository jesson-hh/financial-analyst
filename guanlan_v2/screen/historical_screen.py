# -*- coding: utf-8 -*-
"""Causal daily replay of the closed data-screen rule schema.

Unlike the latest-snapshot evaluator, historical replay requires an explicit
trading calendar. Missing symbol/day rows remain NaN observations; they cannot
silently pull older observations into a rolling window. This module does not
load data, execute trades, truncate candidates, or evaluate generated code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from guanlan_v2.screen import data_screen as ds


SCHEMA_VERSION = "guanlan.historical_screen.v1"
MAX_MATRIX_CELLS = 2_000_000
MAX_BRANCHES = 8


@dataclass
class HistoricalScreenResult:
    eligibility: Any
    scores: Any
    branch_eligibility: dict[str, Any]
    calendar_valid: Any
    diagnostics: dict[str, Any]


def _daily_index(values: Any, *, name: str):
    import pandas as pd

    try:
        index = pd.DatetimeIndex(values)
    except Exception as exc:
        raise ds.DataScreenSpecError(f"{name} 必须是明确的日频交易日历") from exc
    if index.empty or index.hasnans or index.tz is not None:
        raise ds.DataScreenSpecError(f"{name} 不能为空、含 NaT 或带时区")
    if not index.equals(index.normalize()):
        raise ds.DataScreenSpecError(f"{name} 仅支持无时刻的日频日期")
    if not index.is_unique or not index.is_monotonic_increasing:
        raise ds.DataScreenSpecError(f"{name} 必须严格递增且不重复")
    return index.rename("datetime")


def _finite_series(series):
    import numpy as np
    return series.where(np.isfinite(series))


def _compare(left, right, op):
    import numpy as np

    valid = left.notna() & right.notna()
    # Same tolerance as data_screen._compare_values, without Series.combine's
    # element-by-element Python calls for every symbol and every rule.
    tolerance = np.maximum(np.maximum(left.abs(), right.abs()), 1.0) * 1e-12
    if op == ">":
        values = left > right + tolerance
    elif op == ">=":
        values = left >= right - tolerance
    elif op == "<":
        values = left < right - tolerance
    elif op == "<=":
        values = left <= right + tolerance
    elif op == "==":
        values = (left - right).abs() <= tolerance
    elif op == "!=":
        values = (left - right).abs() > tolerance
    else:
        raise ds.DataScreenSpecError(f"不支持 compare op: {op}")
    return values & valid, valid


def _rule_mask(rule, resolve):
    import numpy as np

    typ = rule["type"]
    if typ == "compare":
        return _compare(resolve(rule["left"]), resolve(rule["right"]), rule["op"])[0]
    if typ == "strict_rise":
        series = resolve(rule["series"])
        n = int(rule["transitions"])
        # Exactly n adjacent increases, not a positive n-day endpoint slope.
        positive = series.diff().gt(1e-12)
        return positive.rolling(n, min_periods=n).sum().eq(n)
    if typ == "range":
        series = resolve(rule["value"])
        lo, hi = float(rule["min"]), float(rule["max"])
        epsilon = np.maximum(series.abs(), max(1.0, abs(lo), abs(hi))) * 1e-12
        if rule.get("inclusive", True):
            return series.ge(lo - epsilon) & series.le(hi + epsilon) & series.notna()
        return series.gt(lo + epsilon) & series.lt(hi - epsilon) & series.notna()
    if typ == "max_consecutive":
        p = rule["predicate"]
        truth, valid = _compare(resolve(p["left"]), resolve(p["right"]), p["op"])
        n, maximum = int(rule["lookback"]), int(rule["max_count"])
        complete = valid.rolling(n, min_periods=n).sum().eq(n)
        if maximum == n:
            return complete
        # A streak exceeds maximum iff an all-true run of maximum+1 occurs
        # wholly inside the trailing n observations (never across its boundary).
        too_long = truth.rolling(maximum + 1, min_periods=maximum + 1).sum().eq(maximum + 1)
        violation = too_long.rolling(n - maximum, min_periods=n - maximum).max().eq(1)
        return complete & ~violation
    raise ds.DataScreenSpecError(f"不支持 rule type: {typ}")


def _score_series(spec, rank, resolve, index):
    import pandas as pd

    if rank == "nearest_ma10_ma20":
        biases = [resolve({"type": "bias", "field": "close", "baseline": {
            "type": "rolling_mean", "field": "close", "window": window,
        }}).abs() for window in (10, 20)]
        # Both baselines must exist. skipna=False is essential during warmup.
        return -pd.concat(biases, axis=1).min(axis=1, skipna=False)
    score = spec.get("score")
    if not score:
        return pd.Series(float("nan"), index=index, dtype=float)
    values = resolve(score["value"])
    if score["type"] == "series":
        return values
    lo, hi = float(score["min_bias"]), float(score["max_bias"])
    if lo == hi:
        return (values.sub(lo).abs().le(1e-12).astype(float) * 100.0).where(values.notna())
    return (100.0 * (1.0 - (values - lo) / (hi - lo))).clip(0.0, 100.0)


def evaluate_historical_screen(
    panel: Any,
    requests: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    calendar: Any,
    start: Optional[str] = None,
    end: Optional[str] = None,
    rank: Optional[str] = None,
    universe_meta: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
    metadata_provenance: Optional[Mapping[str, Any]] = None,
    load_meta: Optional[Mapping[str, Any]] = None,
) -> HistoricalScreenResult:
    """Replay 1–8 AND selectors as a daily OR union, with no top-N truncation.

    The supplied calendar must include the warmup interval, not just the output
    interval. ``start`` trims output only. All rules use at most today's close;
    a downstream simulator must execute these signals at a later tradable bar.
    Current/static universe and company metadata are explicitly research-biased.
    ``calendar_valid`` distinguishes low data coverage from a genuine empty pool.
    """
    import numpy as np
    import pandas as pd

    raw_requests = [requests] if isinstance(requests, Mapping) else requests
    if not isinstance(raw_requests, (list, tuple)) or not 1 <= len(raw_requests) <= MAX_BRANCHES:
        raise ds.DataScreenSpecError("历史筛选需提供 1–8 个 data_screen 请求分支")
    specs = [ds.compile_data_screen_spec(request) for request in raw_requests]
    shared = specs[0]
    for spec in specs[1:]:
        for key in ("universe", "codes", "freq", "requested_as_of", "filters", "score"):
            if spec[key] != shared[key]:
                raise ds.DataScreenSpecError(f"OR 分支的 {key} 必须一致，禁止混合不同股票池或不可比评分")
    if rank not in (None, "nearest_ma10_ma20"):
        raise ds.DataScreenSpecError("rank 仅支持 nearest_ma10_ma20 或不提供")
    if rank is not None and shared.get("score") is not None:
        raise ds.DataScreenSpecError("rank 与请求 score 不得同时提供")

    dates = _daily_index(calendar, name="calendar")
    last_day = min(pd.Timestamp(end) if end else dates[-1],
                   pd.Timestamp(shared["requested_as_of"]) if shared["requested_as_of"] else dates[-1])
    first_day = pd.Timestamp(start) if start else dates[0]
    if (pd.isna(last_day) or pd.isna(first_day) or last_day.tzinfo or first_day.tzinfo
            or last_day != last_day.normalize() or first_day != first_day.normalize()):
        raise ds.DataScreenSpecError("start/end 必须是无时刻、无时区的日频日期")
    dates = dates[dates <= last_day]
    if dates.empty or first_day > dates[-1]:
        raise ds.DataScreenSpecError("日历与历史筛选输出区间没有交集")
    output_dates = dates[dates >= first_day]

    frame = getattr(panel, "df", panel)
    if (not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.MultiIndex)
            or frame.index.nlevels != 2):
        raise ds.DataScreenSpecError("panel 必须是 (datetime, code) MultiIndex DataFrame/PanelData")
    if len(frame) > MAX_MATRIX_CELLS:
        raise ds.DataScreenSpecError("历史筛选行情超过 200 万行上限")
    frame = frame.copy()
    frame.index = frame.index.set_names(["datetime", "code"])
    if frame.index.has_duplicates:
        raise ds.DataScreenSpecError("行情 (datetime, code) 不得重复")
    actual_dates = pd.DatetimeIndex(frame.index.get_level_values("datetime"))
    if actual_dates.hasnans or actual_dates.tz is not None or not actual_dates.equals(actual_dates.normalize()):
        raise ds.DataScreenSpecError("行情必须是无时刻、无时区且有效的日频日期")
    frame.index = pd.MultiIndex.from_arrays(
        [actual_dates, frame.index.get_level_values("code")], names=["datetime", "code"],
    )
    if frame.index.has_duplicates:
        raise ds.DataScreenSpecError("行情日期标准化后 (datetime, code) 不得重复")
    frame = frame.loc[actual_dates <= dates[-1]]
    if frame.empty or "close" not in frame.columns:
        raise ds.DataScreenSpecError("截止日前没有 close 行情")
    if not pd.DatetimeIndex(frame.index.get_level_values("datetime").unique()).isin(dates).all():
        raise ds.DataScreenSpecError("calendar 必须包含行情及预热区间全部交易日，禁止悄悄丢弃日期")
    if any(not isinstance(code, str) or not code for code in frame.index.get_level_values("code")):
        raise ds.DataScreenSpecError("行情股票代码必须是非空字符串")
    codes = sorted(shared["codes"] or frame.index.get_level_values("code").unique())
    if len(dates) * len(codes) > MAX_MATRIX_CELLS:
        raise ds.DataScreenSpecError("历史筛选交易日×股票数超过 200 万格上限")
    frame = frame.loc[frame.index.get_level_values("code").isin(codes)].sort_index()
    if "vol" in frame.columns:
        alias = pd.to_numeric(frame["vol"], errors="coerce")
        if "volume" in frame.columns:
            volume = pd.to_numeric(frame["volume"], errors="coerce")
            overlap = alias.notna() & volume.notna()
            if not ((alias[overlap] - volume[overlap]).abs() <= volume[overlap].abs().clip(lower=1.0) * 1e-12).all():
                raise ds.DataScreenSpecError("行情 vol/volume 数值冲突，拒绝猜测成交量口径")
            frame["volume"] = volume.combine_first(alias)
        else:
            frame["volume"] = alias
        frame = frame.drop(columns="vol")
    frame = frame.reindex(columns=["open", "high", "low", "close", "volume", "amount"])
    frame = frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)

    universe_meta, metadata = dict(universe_meta or {}), dict(metadata or {})
    metadata_provenance, load_meta = dict(metadata_provenance or {}), dict(load_meta or {})
    if load_meta.get("amount_unit") != "yuan" or not load_meta.get("verified", False):
        raise ds.DataScreenSpecError("历史筛选要求已验证 amount 单位为 yuan")
    filters = shared["filters"]
    if filters["exclude_st"] and (not metadata or not metadata_provenance.get("available", bool(metadata))):
        raise ds.DataScreenSpecError("请求排除 ST 但公司主表不可用，已失败封闭")
    if filters["require_strict_metadata_pit"] and not metadata_provenance.get("strict_pit"):
        raise ds.DataScreenSpecError("要求严格历史元数据 PIT，但当前元数据不满足")
    try:
        resolved_n = int(universe_meta.get("n_resolved", len(codes)))
    except (TypeError, ValueError) as exc:
        raise ds.DataScreenSpecError("n_resolved 必须为有效股票数量") from exc
    if resolved_n < len(codes) or resolved_n <= 0:
        raise ds.DataScreenSpecError("n_resolved 不得小于加载股票数且必须为正")
    if shared["codes"]:
        resolved_n = len(codes)

    masks = {f"branch_{i + 1}": pd.DataFrame(False, index=dates, columns=codes)
             for i in range(len(specs))}
    scores = pd.DataFrame(np.nan, index=dates, columns=codes, dtype=float)
    valid_bars = pd.Series(0, index=dates, dtype=int)
    missing_metadata = []
    missing_rows_by_code, invalid_bars_by_code = {}, {}
    groups = {str(code): group.droplevel("code") for code, group in frame.groupby(level="code", sort=False)}
    for code in codes:
        raw_group = groups.get(code, frame.iloc[:0].droplevel("code"))
        group = raw_group.reindex(dates)
        real_bar = group["close"].gt(0) & group["volume"].gt(0) & group["amount"].gt(0)
        valid_bars += real_bar.astype(int)
        missing_days = output_dates[~output_dates.isin(raw_group.index)]
        bad_days = output_dates[~real_bar.loc[output_dates]]
        if len(missing_days):
            missing_rows_by_code[code] = [d.date().isoformat() for d in missing_days]
        if len(bad_days):
            invalid_bars_by_code[code] = [d.date().isoformat() for d in bad_days]
        present = group["close"].notna() & group["close"].gt(0)
        if filters["require_asof_trade"]:
            present &= real_bar
        meta = metadata.get(code) or {}
        name = str(meta.get("name") or "").strip()
        if filters["exclude_st"] and (not name or name == code):
            missing_metadata.append(code)
            continue
        if filters["exclude_st"] and "ST" in name.upper():
            continue
        amount = group["amount"].where(group["amount"].ge(0))
        window, observations = filters["amount_window"], filters["amount_min_observations"]
        # Zero is a real observation; missing dates never reach behind the
        # fixed window to retrieve old high-turnover bars.
        liquid = amount.rolling(window, min_periods=observations).mean().ge(filters["min_avg_amount_yuan"])
        base = present & liquid
        expression_cache, rule_cache = {}, {}

        def resolve(expr):
            key = ds._canonical_json(expr)
            if key not in expression_cache:
                expression_cache[key] = _finite_series(ds._resolve_series(group, expr))
            return expression_cache[key]

        for i, spec in enumerate(specs):
            eligible = base.copy()
            for rule in spec["rules"]:
                key = ds._canonical_json({k: v for k, v in rule.items() if k not in ("id", "label")})
                if key not in rule_cache:
                    rule_cache[key] = _rule_mask(rule, resolve)
                eligible &= rule_cache[key]
            masks[f"branch_{i + 1}"][code] = eligible
        scores[code] = _score_series(shared, rank, resolve, dates)

    minimum_bars = max(1, int(np.ceil(resolved_n * 0.8)))
    calendar_valid = valid_bars.ge(minimum_bars).rename("calendar_valid")
    for key in masks:
        masks[key] = masks[key].mul(calendar_valid, axis=0).astype(bool).loc[output_dates]
    union = next(iter(masks.values())).copy()
    for mask in list(masks.values())[1:]:
        union |= mask
    scores = scores.loc[output_dates]
    for matrix in [union, scores, *masks.values()]:
        matrix.columns.name = "code"
    warnings = [
        "历史筛选未截断 top_n；日频持仓数、排名和执行必须由下游模拟器明确设置。",
        "使用固定交易日历，缺失股票/日期保留 NaN；与按已有观测压缩窗口的旧快照缺行口径不同。",
    ]
    if not universe_meta.get("strict_pit"):
        warnings.append("股票池来自当前/静态成分，不是历史逐日成分 PIT，存在成分及幸存者偏差。")
    if filters["exclude_st"] and not metadata_provenance.get("strict_pit"):
        warnings.append("ST/名称过滤来自当前公司主表，不是历史逐日 PIT，可能用当前状态排除历史股票。")
    invalid_dates = [d.date().isoformat() for d in output_dates if not calendar_valid.loc[d]]
    if invalid_dates:
        warnings.append(f"{len(invalid_dates)} 个交易日有效行情低于固定股票池 80%，该日信号失败封闭；不能当作真实空池。")
    diagnostics = {
        "schema_version": SCHEMA_VERSION, "status": "complete", "freq": "day",
        "as_of": output_dates[-1].date().isoformat(), "start": output_dates[0].date().isoformat(),
        "n_dates": len(output_dates), "n_codes": len(codes), "n_resolved": resolved_n,
        "n_branches": len(specs), "branch_combine": "any", "within_branch_combine": "all",
        "top_n_applied": False, "requested_top_n": [spec["top_n"] for spec in specs],
        "spec_digests": [spec["spec_digest"] for spec in specs],
        "required_bars": max([ds._required_bars(spec) for spec in specs] + ([20] if rank else [])),
        "calendar_policy": "fixed_explicit_calendar_no_fill",
        "warmup_start": dates[0].date().isoformat(), "signal_time": "after_daily_close",
        "downstream_execution_lag_required": True,
        "coverage_minimum": minimum_bars, "coverage_basis": "fixed_resolved_universe",
        "invalid_calendar_dates": invalid_dates,
        "missing_rows_by_code": missing_rows_by_code,
        "invalid_bars_by_code": invalid_bars_by_code,
        "matched_by_date": {d.date().isoformat(): int(n) for d, n in union.sum(axis=1).items()},
        "branch_matched_by_date": {key: {d.date().isoformat(): int(n) for d, n in mask.sum(axis=1).items()}
                                   for key, mask in masks.items()},
        "metadata_unknown_excluded": missing_metadata,
        "score": ({"type": "fixed", "name": rank, "direction": "desc",
                   "formula": "-min(abs(close/MA10-1),abs(close/MA20-1)); both MAs required"}
                  if rank else shared.get("score")),
        "universe": universe_meta, "metadata_provenance": metadata_provenance,
        "data_load": load_meta, "warnings": warnings,
        "strict_pit": bool(universe_meta.get("strict_pit")) and bool(metadata_provenance.get("strict_pit")),
    }
    return HistoricalScreenResult(union, scores, masks, calendar_valid.loc[output_dates], diagnostics)


__all__ = ["HistoricalScreenResult", "SCHEMA_VERSION", "evaluate_historical_screen"]
