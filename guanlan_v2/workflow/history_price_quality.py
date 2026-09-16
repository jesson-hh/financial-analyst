"""Fail-closed price-quality gate scoped to historical strategy replay.

Never edits the source, fills missing quotes, removes securities, or fixes
corporate actions by guessing from return outliers. A local loader's explicit,
content-bound adjustment certificate is required for performance reporting.
Old/raw artifacts remain inspectable as diagnostics, never valid performance.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "guanlan.history_price_quality.v1"
ADJUSTMENT_SCHEMA = "guanlan.history_price_adjustment.v1"
PRICE_FIELDS = ("open", "high", "low", "close")


class HistoryPriceQualityError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        reasons = "; ".join(item["message"] + (f"（{item['count']}行）" if "count" in item else "")
                            for item in diagnostics["hard_blockers"])
        super().__init__("历史策略价格质量未通过，结果仅供诊断、不可当作策略业绩: " + reasons)


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()


def history_price_fingerprint(panel, calendar):
    """Bind numeric OHLC values, their instrument/date keys and source calendar.

    Intended for a verified server-side adjustment adapter. Producing hashes is
    not certification; only a trusted loader may issue the provenance contract.
    """
    import pandas as pd
    prices = panel.loc[:, list(PRICE_FIELDS)].apply(pd.to_numeric, errors="coerce").copy()
    prices.index = pd.MultiIndex.from_arrays(
        [pd.DatetimeIndex(prices.index.get_level_values(0)), prices.index.get_level_values(1).astype(str)],
        names=["datetime", "instrument"],
    )
    prices = prices.sort_index().astype("float64")
    return {
        "adjusted_price_sha256": hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).values.tobytes()).hexdigest(),
        "calendar_sha256": _sha([str(d.date()) for d in pd.DatetimeIndex(calendar)]),
    }


def assess_history_price_quality(panel: Any, calendar: Any, *,
                                 adjustment_provenance: Mapping[str, Any] | None = None,
                                 max_examples: int = 12) -> dict[str, Any]:
    """Return bounded JSON-safe diagnostics; structural defects fail closed.

    Missing rows/NaN quotes are reported for the ledger's missing-price policy.
    Actual nonpositive/Inf/non-numeric prices and impossible OHLC envelopes are
    hard blockers. Large overnight moves are warnings, not automatic errors.
    ``calendar`` must be the full independent source calendar including warmup.
    Certification is never accepted from strategy parameters or user context.
    """
    import numpy as np
    import pandas as pd

    if isinstance(max_examples, bool) or not isinstance(max_examples, int) or not 1 <= max_examples <= 50:
        raise ValueError("max_examples must be an integer from1 to50")
    report = {"schema": SCHEMA, "performance_ready": False, "diagnostics_only": True,
              "hard_blockers": [], "warnings": [], "counts": {}, "examples": {},
              "policies": {"no_row_or_stock_exclusion": True, "no_quote_fill": True,
                           "overnight_move_warning_band": [.75, 1.25],
                           "OHLC_relative_tolerance": 1e-6,
                           "missing_quotes": "reported; downstream must retain blocked orders/stale marks",
                           "adjustment": "trusted server loader certificate bound to exact prices/calendar; never infer from price jumps"}}
    def block(code, message, count=None):
        item = {"code": code, "message": message}
        if count is not None:
            item["count"] = int(count)
        report["hard_blockers"].append(item)
    def warn(code, message, count):
        report["warnings"].append({"code": code, "message": message, "count": int(count)})

    frame = getattr(panel, "df", panel)
    try:
        dates = pd.DatetimeIndex(calendar)
        if (dates.empty or dates.hasnans or dates.tz is not None or dates.has_duplicates
                or not dates.is_monotonic_increasing or not dates.equals(dates.normalize())):
            raise ValueError("calendar must be nonempty, unique, increasing midnight dates")
        if (not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.MultiIndex)
                or frame.index.nlevels != 2 or frame.columns.has_duplicates):
            raise ValueError("panel must have unique columns and a two-level daily instrument index")
        if not set(PRICE_FIELDS).issubset(frame.columns):
            raise ValueError("panel requires open/high/low/close")
        row_dates = pd.DatetimeIndex(frame.index.get_level_values(0))
        if row_dates.hasnans or row_dates.tz is not None or not row_dates.equals(row_dates.normalize()):
            raise ValueError("panel dates must be valid midnight dates")
        codes = frame.index.get_level_values(1)
        if any(not isinstance(c, str) or not re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", c) for c in codes):
            raise ValueError("panel instrument code is malformed")
        n_codes = int(codes.nunique())
        if len(frame) > 2_000_000 or len(dates) * n_codes > 2_000_000 or n_codes > 1200:
            raise ValueError("panel exceeds bounded historical replay size")
        normalized = pd.MultiIndex.from_arrays([row_dates, codes], names=["datetime", "instrument"])
        duplicates = int(normalized.duplicated().sum())
        if duplicates:
            block("duplicate_daily_prices", "同一交易日/股票价格重复，不能选择任意一条", duplicates)
        outside = int((~row_dates.isin(dates)).sum())
        if outside:
            block("prices_outside_source_calendar", "行情日期不在完整source交易日历中", outside)
        if frame.empty:
            block("empty_price_panel", "没有历史价格")
        if report["hard_blockers"]:
            return report
    except (TypeError, ValueError, AttributeError) as exc:
        block("invalid_price_structure", str(exc))
        return report

    raw = frame.loc[:, list(PRICE_FIELDS)]
    prices = raw.apply(pd.to_numeric, errors="coerce").astype("float64")
    prices.index = normalized
    nonnumeric = raw.notna().to_numpy() & prices.isna().to_numpy()
    infinite = np.isinf(prices.to_numpy())
    nonpositive = (prices <= 0).to_numpy()
    missing = prices.isna()
    complete = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
    maximum = prices.abs().max(axis=1).clip(lower=1.0)
    tolerance = maximum * 1e-6
    bad_high_low = complete & (prices["high"] + tolerance < prices["low"])
    bad_open = complete & ((prices["open"] > prices["high"] + tolerance) | (prices["open"] < prices["low"] - tolerance))
    bad_close = complete & ((prices["close"] > prices["high"] + tolerance) | (prices["close"] < prices["low"] - tolerance))
    bad_envelope = bad_high_low | bad_open | bad_close
    required_factors = int((np.isfinite(prices) & prices.gt(0)).any(axis=1).sum())
    if not required_factors:
        block("no_observed_valid_prices", "整个源区间没有有效价格，不能把缺数据当成现金策略表现")
    report["counts"] = {
        "source_rows": len(frame), "source_calendar_days": len(dates), "source_codes": n_codes,
        "source_start": str(dates[0].date()), "source_end": str(dates[-1].date()),
        "missing_calendar_stock_rows": len(dates) * n_codes - len(frame),
        "missing_price_values": {field: int(missing[field].sum()) for field in PRICE_FIELDS},
        "all_OHLC_missing_rows": int(missing.all(axis=1).sum()),
        "partial_OHLC_missing_rows": int((missing.any(axis=1) & ~missing.all(axis=1)).sum()),
        "nonnumeric_price_values": int(nonnumeric.sum()), "infinite_price_values": int(infinite.sum()),
        "nonpositive_price_values": int(nonpositive.sum()), "OHLC_envelope_bad_rows": int(bad_envelope.sum()),
        "high_below_low_rows": int(bad_high_low.sum()), "open_outside_range_rows": int(bad_open.sum()),
        "close_outside_range_rows": int(bad_close.sum()), "required_factor_rows": required_factors,
    }
    def examples(mask):
        output = []
        for (day, code), row in prices.loc[mask].head(max_examples).iterrows():
            output.append({"date": str(day.date()), "code": code,
                           **{field: float(row[field]) if np.isfinite(row[field]) else None for field in PRICE_FIELDS}})
        return output
    for key, mask, description in (
        ("nonnumeric_prices", nonnumeric.any(axis=1), "存在非数值价格"),
        ("infinite_prices", infinite.any(axis=1), "存在Inf/-Inf价格"),
        ("nonpositive_prices", nonpositive.any(axis=1), "存在已填入的非正价格，不能当有效成交价"),
        ("impossible_OHLC", bad_envelope, "存在同日OHLC矛盾；统一复权不能修复单字段错位"),
    ):
        if int(np.asarray(mask).sum()):
            block(key, description, int(np.asarray(mask).sum()))
            report["examples"][key] = examples(mask)
    missing_n = report["counts"]["missing_calendar_stock_rows"] + int(missing.any(axis=1).sum())
    if missing_n:
        warn("missing_prices", "缺行/NaN保持缺失；不得自动删除日期或以可得未来收益补位", missing_n)

    # Fixed-calendar lag, with no missing-day compression or forward fills.
    opened = prices["open"].unstack(level=1).reindex(dates)
    closed = prices["close"].unstack(level=1).reindex(dates)
    ratio = opened / closed.shift(1)
    unusual = np.isfinite(ratio) & ((ratio < .75) | (ratio > 1.25))
    count = int(unusual.sum().sum())
    report["counts"]["overnight_move_warning_rows"] = count
    if count:
        warn("large_overnight_moves", "大幅隔夜波动可能是市场行情、除权或错价；仅提示，不按阈值删除股票/日期", count)
        report["examples"]["large_overnight_moves"] = [
            {"date": str(day.date()), "code": code, "open_over_prior_calendar_close": float(ratio.at[day, code])}
            for day, code in unusual.stack().loc[lambda x: x].index[:max_examples]]

    fingerprint = history_price_fingerprint(prices, dates)
    report["price_fingerprint"] = fingerprint
    cert = dict(adjustment_provenance or {}) if isinstance(adjustment_provenance, Mapping) else {}
    certificate_errors = []
    for key, expected in {"schema": ADJUSTMENT_SCHEMA, "status": "verified",
                          "method": "causal_cumulative_factor", "future_normalization": False,
                          "price_fields": list(PRICE_FIELDS), "volume_policy": "raw_share",
                          "amount_policy": "raw_yuan"}.items():
        actual = cert.get(key)
        if actual != expected or (isinstance(expected, bool) and not isinstance(actual, bool)):
            certificate_errors.append(key)
    if not isinstance(cert.get("source"), str) or not cert["source"].strip():
        certificate_errors.append("source")
    for key in ("raw_price_sha256", "factor_source_sha256"):
        if not isinstance(cert.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", cert[key]):
            certificate_errors.append(key)
    for key, expected in fingerprint.items():
        if cert.get(key) != expected:
            certificate_errors.append(key)
    coverage = cert.get("factor_coverage") or {}
    if not isinstance(coverage, Mapping):
        coverage = {}
    for key, expected in {"n_required": required_factors, "n_verified": required_factors, "n_missing": 0}.items():
        if isinstance(coverage.get(key), bool) or not isinstance(coverage.get(key), int) or coverage.get(key) != expected:
            certificate_errors.append("factor_coverage." + key)
    report["adjustment_certificate"] = {"valid": not certificate_errors,
                                        "source": cert["source"][:512] if isinstance(cert.get("source"), str) else None,
                                        "method": cert["method"][:64] if isinstance(cert.get("method"), str) else None,
                                        "failed_fields": certificate_errors,
                                        "trust_boundary": "certificate must originate in a verified server loader, not user-supplied params/context"}
    if certificate_errors:
        block("uncertified_price_adjustment", "未认证统一因果复权或因子覆盖不完整；原始价格断点不能作为策略损益")
    report["performance_ready"] = not report["hard_blockers"]
    report["diagnostics_only"] = not report["performance_ready"]
    return report


def require_history_price_quality(panel, calendar, *, adjustment_provenance=None, max_examples=12):
    report = assess_history_price_quality(panel, calendar, adjustment_provenance=adjustment_provenance,
                                          max_examples=max_examples)
    if not report["performance_ready"]:
        raise HistoryPriceQualityError(report)
    return report


__all__ = ["HistoryPriceQualityError", "assess_history_price_quality", "require_history_price_quality",
           "history_price_fingerprint", "ADJUSTMENT_SCHEMA"]
