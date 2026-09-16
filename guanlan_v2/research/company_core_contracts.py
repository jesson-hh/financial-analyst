"""Deterministic core gates for one listed-company research run.

The Evidence Hub answers *where* evidence came from.  This module answers the
stricter question: is that evidence sufficient to close the Vibe Research
profile, financials and estimates stages?  It never fetches data and never
fills a missing field from memory or from industry/context evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence


PROFILE_SCHEMA = "company-profile-contract@1"
FINANCIALS_SCHEMA = "company-financials-contract@1"
ESTIMATES_SCHEMA = "company-estimates-contract@1"

_PERIOD_RE = re.compile(r"^(\d{4})[-/]?(03[-/]?31|06[-/]?30|09[-/]?30|12[-/]?31)$")
_UNAVAILABLE = {"", "-", "--", "n/a", "na", "null", "none"}


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe(item) for item in value]
    scalar = getattr(value, "item", None)
    if callable(scalar):
        try:
            return _safe(scalar())
        except (TypeError, ValueError):
            pass
    return str(value)


def _digest(value: Any) -> str:
    body = json.dumps(
        _safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _calc_id(prefix: str, formula: str, inputs: Any) -> str:
    return f"CAL-{prefix}-{_digest({'formula': formula, 'inputs': inputs})[:16].upper()}"


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if text.lower() in _UNAVAILABLE:
        return None
    multiplier = 1.0
    for suffix, factor in (("万亿元", 1e12), ("亿元", 1e8), ("万元", 1e4), ("元", 1.0)):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            multiplier = factor
            break
    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _iso_date(value: Any) -> str:
    text = _text(value, 64)
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return ""


def _as_date(value: Any) -> date | None:
    text = _iso_date(value)
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _sections(pack: Mapping[str, Any]) -> Mapping[str, Any]:
    value = pack.get("sections")
    return value if isinstance(value, Mapping) else {}


def _snapshot(raw: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}, {}
    snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), Mapping) else raw
    metadata = raw.get("asset") if isinstance(raw.get("asset"), Mapping) else raw
    return snapshot, metadata


def _gap(code: str, message: str, *, field: str = "", critical: bool = True,
         details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": code,
        "message": message,
        "critical": critical,
    }
    if field:
        row["field"] = field
    if details:
        row["details"] = dict(details)
    return row


def _market_for(code: str) -> str:
    code = str(code or "").strip().upper()
    if code.startswith("SH"):
        return "SSE"
    if code.startswith("SZ"):
        return "SZSE"
    if code.startswith("BJ"):
        return "BSE"
    return ""


def evaluate_profile_contract(
    evidence_pack: Mapping[str, Any], workspace_snapshots: Iterable[Any] = (), *,
    subject: Mapping[str, Any], as_of: str,
) -> dict[str, Any]:
    """Validate issuer identity, listing state, quote timing and calendar evidence."""
    sections = _sections(evidence_pack)
    profile = sections.get("company_profile")
    profile = profile if isinstance(profile, Mapping) else {}
    quote = sections.get("quote_live")
    quote = quote if isinstance(quote, Mapping) else {}
    expected_code = _text(subject.get("code"), 32).upper()
    pack_code = _text(evidence_pack.get("code"), 32).upper()
    expected_name = _text(subject.get("name") or subject.get("display_name"), 120)
    profile_code = _text(profile.get("code"), 32).upper()
    profile_name = _text(profile.get("name") or profile.get("security_name"), 120)
    market = _text(profile.get("market"), 32) or _market_for(expected_code)
    listing_status = _text(profile.get("listing_status"), 32).lower()
    listing_date = _iso_date(profile.get("listing_date"))
    gaps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    identity_ok = bool(expected_code and pack_code == expected_code)
    if profile_code:
        identity_ok = identity_ok and profile_code == expected_code
    if not identity_ok:
        gaps.append(_gap(
            "issuer_identity_mismatch",
            "Evidence Pack、公司名录与工作空间代码未能严格一致。",
            field="code",
            details={"workspace": expected_code, "pack": pack_code, "profile": profile_code},
        ))
    checks.append({"check": "issuer_code_match", "passed": identity_ok})

    name_ok = bool(expected_name and profile_name and expected_name == profile_name)
    if not name_ok:
        gaps.append(_gap(
            "issuer_name_not_verified",
            "公司简称未由发行人名录与工作空间共同验证。",
            field="name",
            details={"workspace": expected_name, "profile": profile_name},
        ))
    checks.append({"check": "issuer_name_match", "passed": name_ok})

    market_ok = market in {"SSE", "SZSE", "BSE"}
    if not market_ok:
        gaps.append(_gap("market_missing", "上市市场没有可靠映射。", field="market"))
    checks.append({"check": "market_known", "passed": market_ok, "value": market or None})

    listing_ok = listing_status in {"listed", "active"} and bool(listing_date)
    if not listing_ok:
        gaps.append(_gap(
            "listing_status_unverified",
            "缺少官方在市状态或上市日期，不能仅凭股票代码假定仍在上市。",
            field="listing_status",
        ))
    checks.append({
        "check": "listing_status_verified", "passed": listing_ok,
        "value": listing_status or None, "listing_date": listing_date or None,
    })

    quote_price = _number(quote.get("price"))
    # Acquisition time is not a market observation date.  A retained Friday
    # quote fetched on Saturday must remain undated (and therefore unusable)
    # unless the source explicitly attests its exchange-session date.
    quote_date = _as_date(quote.get("market_data_as_of") or quote.get("as_of"))
    cutoff = _as_date(as_of)
    quote_age = (cutoff - quote_date).days if cutoff and quote_date else None
    quote_ok = quote_price is not None and quote_price > 0 and quote_age is not None and 0 <= quote_age <= 3
    if not quote_ok:
        gaps.append(_gap(
            "quote_missing_or_stale",
            "缺少正价格、明确时点且不超过三日的行情快照。",
            field="quote",
            details={"price": quote_price, "quote_date": quote_date.isoformat() if quote_date else None,
                     "age_days": quote_age},
        ))
    checks.append({
        "check": "quote_usable", "passed": quote_ok, "price": quote_price,
        "quote_date": quote_date.isoformat() if quote_date else None,
        "age_days": quote_age,
    })

    calendar_rows: list[Mapping[str, Any]] = []
    calendar_assets: list[str] = []
    for raw in workspace_snapshots or ():
        snapshot, metadata = _snapshot(raw)
        if snapshot.get("dataset_id") != "research.trade_disclosure_calendar.live":
            continue
        rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
        calendar_rows.extend(row for row in rows if isinstance(row, Mapping))
        if metadata.get("id"):
            calendar_assets.append(str(metadata["id"]))
    trading_days = sorted({
        _iso_date(row.get("date")) for row in calendar_rows
        if (
            row.get("record_type") == "trading_day"
            or (
                row.get("record_type") == "trading_calendar"
                and row.get("trading") is True
            )
        )
        and _iso_date(row.get("date"))
    })
    calendar_ok = bool(trading_days)
    if not calendar_ok:
        gaps.append(_gap(
            "trade_calendar_missing",
            "工作空间没有可用的官方交易日历记录，无法解释非交易日行情时点。",
            field="trade_calendar",
        ))
    checks.append({
        "check": "trade_calendar_available", "passed": calendar_ok,
        "first": trading_days[0] if trading_days else None,
        "last": trading_days[-1] if trading_days else None,
    })

    moat = profile.get("moat_label") or "待补"
    if moat == "待补":
        gaps.append(_gap(
            "moat_label_pending",
            "竞争壁垒尚未由公司证据支持，保留为“待补”。",
            field="moat_label", critical=False,
        ))
    critical = [row for row in gaps if row["critical"]]
    return {
        "schema_version": PROFILE_SCHEMA,
        "stage": "profile",
        "status": "complete" if not critical else "incomplete",
        "subject": {
            "code": expected_code or None,
            "name": profile_name or expected_name or None,
            "market": market or None,
            "listing_status": listing_status or None,
            "listing_date": listing_date or None,
            "moat_label": moat,
        },
        "quote": {
            "price": quote_price, "unit": "CNY/share",
            "as_of": quote_date.isoformat() if quote_date else None,
            "age_days": quote_age,
        },
        "calendar": {"trading_days": trading_days, "asset_ids": sorted(set(calendar_assets))},
        "checks": checks,
        "gaps": gaps,
        "source_refs": [
            "pack.company_profile", "pack.quote_live",
            *[f"workspace_asset:{item}" for item in sorted(set(calendar_assets))],
        ],
    }


_FINANCIAL_ALIASES: Mapping[str, tuple[str, ...]] = {
    "revenue_cum": ("revenue_cum", "total_revenue"),
    "net_profit_parent_cum": ("net_profit_parent_cum", "net_profit_parent"),
    "net_profit_deducted_cum": ("net_profit_deducted_cum", "np_deducted"),
}


def _period(value: Any) -> str:
    text = _text(value, 32)
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) < 8:
        return ""
    normal = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return normal if _PERIOD_RE.match(normal) else ""


def _first(row: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for key in aliases:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _quarter_number(period: str) -> int:
    return {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}.get(period[5:], 0)


def _quarter_ordinal(period: str) -> int | None:
    """Return a monotonic quarter index for a canonical statement period."""
    quarter = _quarter_number(period)
    if not quarter:
        return None
    try:
        year = int(period[:4])
    except (TypeError, ValueError):
        return None
    return year * 4 + quarter - 1


def _consecutive_quarter_tail(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the latest uninterrupted run of single-quarter observations.

    A count of eight observations is insufficient for TTM-vs-prior-TTM math:
    they must represent eight adjacent fiscal quarters.  Resetting the tail on
    every gap prevents sparse Q1-only histories from being treated as a valid
    two-year window.
    """
    tail: list[dict[str, Any]] = []
    previous_ordinal: int | None = None
    for raw in rows:
        row = dict(raw)
        ordinal = _quarter_ordinal(str(row.get("period") or ""))
        if ordinal is None:
            tail = []
            previous_ordinal = None
            continue
        if previous_ordinal is None or ordinal != previous_ordinal + 1:
            tail = []
        tail.append(row)
        previous_ordinal = ordinal
    return tail


def _previous_cumulative_period(period: str) -> str:
    year = int(period[:4])
    quarter = _quarter_number(period)
    return {
        2: f"{year}-03-31",
        3: f"{year}-06-30",
        4: f"{year}-09-30",
    }.get(quarter, "")


def _calculation(prefix: str, metric: str, formula: str, inputs: list[dict[str, Any]],
                 value: float | None, *, unit: str, period: str,
                 status: str = "complete", note: str = "") -> dict[str, Any]:
    row = {
        "calculation_id": _calc_id(prefix, formula, inputs),
        "metric": metric,
        "formula": formula,
        "inputs": inputs,
        "result": {"value": value, "unit": unit, "period": period},
        "status": status,
    }
    if note:
        row["note"] = note
    return row


def evaluate_financials_contract(evidence_pack: Mapping[str, Any]) -> dict[str, Any]:
    """Quarterise cumulative YTD statements and produce traceable TTM metrics."""
    section = _sections(evidence_pack).get("financials")
    section = section if isinstance(section, Mapping) else {}
    rows = section.get("rows") if isinstance(section.get("rows"), list) else []
    basis = _text(section.get("basis") or section.get("accounting_basis"), 64).lower()
    unit = _text(section.get("unit") or "", 32).upper()
    gaps: list[dict[str, Any]] = []
    if basis not in {"cumulative_ytd", "ytd_cumulative"}:
        gaps.append(_gap(
            "financial_basis_unverified",
            "财务行未明确标注为累计年初至今口径，拒绝自动单季化。",
            field="basis",
        ))
    if unit not in {"CNY", "RMB", "元"}:
        gaps.append(_gap(
            "financial_unit_unverified",
            "财务行缺少统一的人民币元单位。",
            field="unit", details={"observed": unit or None},
        ))

    normalised: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    ytd_ineligible_periods: set[str] = set()

    def ytd_source_usable(value: Mapping[str, Any]) -> bool:
        return (value.get("usable") is not False and value.get("ok") is not False
                and not value.get("future")
                and not value.get("stale")
                and _text(value.get("status"), 32).lower()
                in {"", "ok", "complete", "available", "partial", "incomplete"})

    ytd_cutoff = _as_date(evidence_pack.get("as_of") or evidence_pack.get("asof")
                         or evidence_pack.get("generated_at"))
    for row_index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            continue
        period = _period(raw.get("period") or raw.get("report_date") or raw.get("报告期"))
        if not period:
            continue
        if not ytd_source_usable(raw) or (ytd_cutoff and _as_date(period) > ytd_cutoff):
            ytd_ineligible_periods.add(period)
        values = {
            metric: _number(_first(raw, aliases))
            for metric, aliases in _FINANCIAL_ALIASES.items()
        }
        observed_unit = _text(raw.get("unit") or unit, 32).upper()
        candidate = {
            "period": period, "unit": observed_unit or unit,
            **values,
            "source_ref": f"pack.sections.financials.rows[{row_index}]",
            "provider_source_ref": _text(raw.get("source_ref"), 500) or None,
        }
        if period in normalised and any(
            normalised[period].get(metric) != candidate.get(metric)
            for metric in _FINANCIAL_ALIASES
        ):
            conflicts.append({
                "kind": "duplicate_period_conflict", "period": period,
                "first": normalised[period], "second": candidate,
            })
            continue
        normalised[period] = candidate

    complete_rows = [
        row for row in normalised.values()
        if all(row.get(metric) is not None for metric in _FINANCIAL_ALIASES)
        and row.get("unit") in {"CNY", "RMB", "元"}
    ]
    complete_rows.sort(key=lambda row: row["period"])
    if len(complete_rows) < 8:
        gaps.append(_gap(
            "insufficient_cumulative_periods",
            "需要至少 8 个同时包含营业收入、归母净利润、扣非净利润的累计财务期。",
            field="rows", details={"complete_periods": len(complete_rows), "observed_rows": len(rows)},
        ))
    if conflicts:
        gaps.append(_gap(
            "financial_period_conflict",
            "同一报告期存在互相冲突的累计财务值。",
            field="rows", details={"conflicts": len(conflicts)},
        ))

    by_period = {row["period"]: row for row in complete_rows}
    single_quarters: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    for row in complete_rows:
        period = row["period"]
        quarter = _quarter_number(period)
        previous_period = _previous_cumulative_period(period)
        previous = by_period.get(previous_period) if previous_period else None
        if quarter != 1 and not previous:
            continue
        values: dict[str, float] = {}
        calc_ids: dict[str, str] = {}
        for metric in _FINANCIAL_ALIASES:
            current_value = float(row[metric])
            if quarter == 1:
                formula = f"{metric}_single_q = {metric}_cum"
                inputs = [{
                    "ref": row["source_ref"], "value": current_value,
                    "unit": "CNY", "period": period,
                }]
                value = current_value
            else:
                prior_value = float(previous[metric])
                formula = f"{metric}_single_q = current_cum - previous_cum"
                inputs = [
                    {"ref": row["source_ref"], "value": current_value, "unit": "CNY", "period": period},
                    {"ref": previous["source_ref"], "value": prior_value, "unit": "CNY",
                     "period": previous_period},
                ]
                value = current_value - prior_value
            calculation = _calculation(
                "FIN", f"{metric}_single_quarter", formula, inputs, value,
                unit="CNY", period=period,
            )
            calculations.append(calculation)
            values[metric.removesuffix("_cum")] = value
            calc_ids[metric.removesuffix("_cum")] = calculation["calculation_id"]
        single_quarters.append({
            "period": period, "quarter": f"{period[:4]}Q{quarter}", "unit": "CNY",
            **values, "calculation_ids": calc_ids,
        })

    single_quarters.sort(key=lambda row: row["period"])
    consecutive_quarters = _consecutive_quarter_tail(single_quarters)
    if len(single_quarters) < 8:
        gaps.append(_gap(
            "insufficient_single_quarters",
            "累计财务无法连续单季化出当前 TTM 与上年同期 TTM 所需的 8 个季度。",
            field="quarterisation", details={"single_quarters": len(single_quarters)},
        ))
    elif len(consecutive_quarters) < 8:
        gaps.append(_gap(
            "non_contiguous_single_quarters",
            "已有至少 8 个单季值，但最新序列不是连续 8 个财季，拒绝拼接计算 TTM 与同比。",
            field="quarterisation",
            details={
                "single_quarters": len(single_quarters),
                "latest_consecutive_quarters": len(consecutive_quarters),
                "latest_consecutive_periods": [row["period"] for row in consecutive_quarters],
            },
        ))

    metrics: dict[str, Any] = {}
    # A half-year / nine-month statement is cumulative, not a single quarter
    # or TTM. Register its same-period YoY explicitly so the writer need not
    # invent a transformation and attach it to an unrelated raw evidence id.
    if (complete_rows and not conflicts
            and basis in {"cumulative_ytd", "ytd_cumulative"}
            and unit in {"CNY", "RMB", "元"}
            and ytd_source_usable(section) and ytd_source_usable(evidence_pack)):
        latest_cumulative = complete_rows[-1]
        period = latest_cumulative["period"]
        prior_period = f"{int(period[:4]) - 1}{period[4:]}"
        prior_cumulative = by_period.get(prior_period)
        if period in ytd_ineligible_periods or prior_period in ytd_ineligible_periods:
            gaps.append(_gap(
                "cumulative_yoy_source_unusable",
                "累计同比所需财期包含不可用或超出截止日的记录，拒绝登记有效计算。",
                field="cumulative_yoy", critical=False,
            ))
        elif prior_cumulative is None:
            gaps.append(_gap(
                "cumulative_yoy_prior_period_missing",
                "最新累计财务缺少上年相同报告期，不能用相邻季度或 TTM 替代累计同比。",
                field="cumulative_yoy", critical=False,
                details={"period": period, "prior_period": prior_period},
            ))
        else:
            for metric in _FINANCIAL_ALIASES:
                current_value = float(latest_cumulative[metric])
                prior_value = float(prior_cumulative[metric])
                metric_name = f"{metric.removesuffix('_cum')}_ytd_yoy"
                usable_base = prior_value > 0
                note = "" if usable_base else "上年同期累计值非正，不输出常规同比增长率。"
                if not usable_base:
                    gaps.append(_gap(
                        "cumulative_yoy_nonpositive_base", note,
                        field=metric_name, critical=False,
                    ))
                calc = _calculation(
                    "FIN", metric_name,
                    f"{metric_name} = current_ytd / prior_year_same_period_ytd - 1",
                    [
                        {"ref": latest_cumulative["source_ref"], "field": metric,
                         "value": current_value, "unit": "CNY", "period": period},
                        {"ref": prior_cumulative["source_ref"], "field": metric,
                         "value": prior_value, "unit": "CNY", "period": prior_period},
                    ],
                    current_value / prior_value - 1.0 if usable_base else None,
                    unit="ratio", period=period,
                    status="complete" if usable_base else "incomplete", note=note,
                )
                calculations.append(calc)
                metrics[metric_name] = calc["result"] | {
                    "calculation_id": calc["calculation_id"], "status": calc["status"],
                }
    if single_quarters:
        latest = single_quarters[-1]
        metrics["latest_quarter"] = latest
    if len(consecutive_quarters) >= 8:
        current_four = consecutive_quarters[-4:]
        prior_four = consecutive_quarters[-8:-4]
        ttm_period = f"TTM@{current_four[-1]['period']}"
        for metric in ("net_profit_parent", "net_profit_deducted"):
            current_inputs = [
                {"ref": row["calculation_ids"][metric], "value": row[metric],
                 "unit": "CNY", "period": row["period"]}
                for row in current_four
            ]
            current_value = sum(float(row[metric]) for row in current_four)
            ttm_calc = _calculation(
                "FIN", f"ttm_{metric}", f"ttm_{metric} = sum(latest_4_single_quarters)",
                current_inputs, current_value, unit="CNY", period=ttm_period,
            )
            calculations.append(ttm_calc)
            metrics[f"ttm_{metric}"] = ttm_calc["result"] | {
                "calculation_id": ttm_calc["calculation_id"]
            }

            prior_value = sum(float(row[metric]) for row in prior_four)
            yoy_inputs = [{
                "ref": ttm_calc["calculation_id"], "value": current_value,
                "unit": "CNY", "period": ttm_period,
            }]
            yoy_inputs.extend(
                {"ref": row["calculation_ids"][metric], "value": row[metric],
                 "unit": "CNY", "period": row["period"]}
                for row in prior_four
            )
            if prior_value == 0:
                yoy = None
                yoy_status = "incomplete"
                note = "上年同期 TTM 为 0，增长率无定义。"
                gaps.append(_gap(
                    "ttm_yoy_denominator_zero", note, field=f"ttm_{metric}_yoy",
                ))
            else:
                yoy = current_value / prior_value - 1.0
                yoy_status = "complete"
                note = ""
            yoy_calc = _calculation(
                "FIN", f"ttm_{metric}_yoy",
                f"ttm_{metric}_yoy = current_ttm / prior_ttm - 1",
                yoy_inputs, yoy, unit="ratio", period=ttm_period,
                status=yoy_status, note=note,
            )
            calculations.append(yoy_calc)
            metrics[f"ttm_{metric}_yoy"] = yoy_calc["result"] | {
                "calculation_id": yoy_calc["calculation_id"], "status": yoy_status,
            }

        latest = consecutive_quarters[-1]
        previous = consecutive_quarters[-2]
        denominator = float(previous["net_profit_deducted"])
        qoq_inputs = [
            {"ref": latest["calculation_ids"]["net_profit_deducted"],
             "value": latest["net_profit_deducted"], "unit": "CNY", "period": latest["period"]},
            {"ref": previous["calculation_ids"]["net_profit_deducted"],
             "value": previous["net_profit_deducted"], "unit": "CNY", "period": previous["period"]},
        ]
        if denominator == 0:
            qoq = None
            qoq_status = "incomplete"
            note = "上一季度扣非净利润为 0，环比无定义。"
            gaps.append(_gap("qoq_denominator_zero", note, field="deducted_profit_qoq"))
        else:
            qoq = float(latest["net_profit_deducted"]) / denominator - 1.0
            qoq_status = "complete"
            note = ""
        qoq_calc = _calculation(
            "FIN", "net_profit_deducted_qoq",
            "deducted_profit_qoq = latest_single_q / previous_single_q - 1",
            qoq_inputs, qoq, unit="ratio", period=latest["period"],
            status=qoq_status, note=note,
        )
        calculations.append(qoq_calc)
        metrics["net_profit_deducted_qoq"] = qoq_calc["result"] | {
            "calculation_id": qoq_calc["calculation_id"], "status": qoq_status,
        }

    critical = [row for row in gaps if row["critical"]]
    return {
        "schema_version": FINANCIALS_SCHEMA,
        "stage": "financials",
        "status": "complete" if not critical else "incomplete",
        "basis": basis or None,
        "unit": "CNY" if unit in {"CNY", "RMB", "元"} else unit or None,
        "cumulative_periods": complete_rows,
        "single_quarters": single_quarters,
        "latest_consecutive_quarters": consecutive_quarters,
        "metrics": metrics,
        "calculations": calculations,
        "conflicts": conflicts,
        "gaps": gaps,
    }


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not float(number).is_integer():
        return None
    return int(number)


def evaluate_estimates_contract(evidence_pack: Mapping[str, Any], *, as_of: str) -> dict[str, Any]:
    """Validate T/T+1/T+2 consensus EPS and derive CAGR/dispersion."""
    section = _sections(evidence_pack).get("expectations")
    section = section if isinstance(section, Mapping) else {}
    items = section.get("items") if isinstance(section.get("items"), list) else []
    cutoff = _as_date(as_of)
    start_year = cutoff.year if cutoff else None
    required_years = [start_year + offset for offset in range(3)] if start_year else []
    gaps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if start_year is None:
        gaps.append(_gap("estimate_cutoff_invalid", "as_of 无法解析，不能确定 T/T+1/T+2。", field="as_of"))

    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    item_index: dict[int, int] = {}
    for index, row in enumerate(items):
        if not isinstance(row, Mapping):
            continue
        item_index[id(row)] = index
        year = _integer(row.get("year") or row.get("fiscal_year"))
        if year:
            grouped[year].append(row)

    summaries: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    for year in required_years:
        rows = grouped.get(year, [])
        aggregate = next((row for row in rows if any(
            key in row for key in ("eps_mean", "eps_min", "eps_max", "n_org", "institution_count")
        )), None)
        if aggregate:
            mean = _number(aggregate.get("eps_mean") if "eps_mean" in aggregate else aggregate.get("mean"))
            minimum = _number(aggregate.get("eps_min") if "eps_min" in aggregate else aggregate.get("min"))
            maximum = _number(aggregate.get("eps_max") if "eps_max" in aggregate else aggregate.get("max"))
            count = _integer(aggregate.get("n_org") if "n_org" in aggregate else aggregate.get("institution_count"))
            aggregate_index = item_index.get(id(aggregate), items.index(aggregate))
            source_refs = [f"pack.sections.expectations.items[{aggregate_index}]"]
            provider_source_refs = [_text(aggregate.get("source_ref"), 500) or None]
        else:
            individual = [
                row for row in rows
                if _number(row.get("eps")) is not None
                and _text(row.get("institution") or row.get("institution_id"), 120)
            ]
            values = [float(_number(row.get("eps"))) for row in individual]
            mean = sum(values) / len(values) if values else None
            minimum = min(values) if values else None
            maximum = max(values) if values else None
            count = len({_text(row.get("institution") or row.get("institution_id"), 120)
                         for row in individual}) or None
            source_refs = [
                f"pack.sections.expectations.items[{item_index.get(id(row), items.index(row))}]"
                for row in individual
            ]
            provider_source_refs = [
                _text(row.get("source_ref"), 500) or None for row in individual
            ]

        complete = all(value is not None for value in (mean, minimum, maximum, count))
        if not complete:
            gaps.append(_gap(
                "consensus_year_incomplete",
                f"{year} 年一致预期缺少 EPS 均值、上下界或机构数。",
                field=str(year), details={"rows": len(rows)},
            ))
        elif minimum > mean or mean > maximum:
            gaps.append(_gap(
                "consensus_range_invalid",
                f"{year} 年 EPS 最小值、均值、最大值次序不合法。",
                field=str(year), details={"min": minimum, "mean": mean, "max": maximum},
            ))
        if count is not None and count < 3:
            warnings.append(_gap(
                "thin_consensus",
                f"{year} 年仅 {count} 家机构，按 Vibe 规则可继续但可靠性偏低。",
                field=str(year), critical=False,
            ))

        dispersion = None
        range_over_mean = None
        dispersion_id = None
        ordered = bool(complete and minimum <= mean <= maximum)
        if ordered and minimum > 0:
            inputs = [
                {"ref": source_refs[0], "value": minimum, "unit": "CNY/share", "period": str(year)},
                {"ref": source_refs[0], "value": mean, "unit": "CNY/share", "period": str(year)},
                {"ref": source_refs[0], "value": maximum, "unit": "CNY/share", "period": str(year)},
            ]
            dispersion = maximum / minimum
            range_over_mean = (maximum - minimum) / mean
            calc = _calculation(
                "EST", "consensus_dispersion",
                "consensus_dispersion = eps_max / eps_min",
                inputs, dispersion, unit="倍", period=str(year),
            )
            calc["details"] = {"range_over_mean": range_over_mean}
            calculations.append(calc)
            dispersion_id = calc["calculation_id"]
        elif ordered:
            warnings.append(_gap(
                "eps_dispersion_denominator_non_positive",
                f"{year} 年 EPS 下界 ≤ 0，max/min 离散倍数无意义。",
                field=str(year), critical=False,
            ))
        summaries.append({
            "year": year, "eps_mean": mean, "eps_min": minimum, "eps_max": maximum,
            "institution_count": count, "unit": "CNY/share",
            "source_refs": source_refs, "dispersion": dispersion,
            "provider_source_refs": provider_source_refs,
            "dispersion_unit": "倍", "range_over_mean": range_over_mean,
            "dispersion_calculation_id": dispersion_id,
        })

    cagr = None
    cagr_id = None
    if len(summaries) == 3:
        start = summaries[0]["eps_mean"]
        end = summaries[2]["eps_mean"]
        if start is not None and end is not None and start > 0 and end > 0:
            inputs = [
                {"ref": summaries[0]["source_refs"][0] if summaries[0]["source_refs"] else "",
                 "value": start, "unit": "CNY/share", "period": str(summaries[0]["year"])},
                {"ref": summaries[2]["source_refs"][0] if summaries[2]["source_refs"] else "",
                 "value": end, "unit": "CNY/share", "period": str(summaries[2]["year"])},
            ]
            cagr = (end / start) ** 0.5 - 1.0
            calc = _calculation(
                "EST", "eps_forward_cagr_t_to_t2",
                "eps_cagr_t_to_t2 = (eps_t2 / eps_t) ** (1 / 2) - 1",
                inputs, cagr, unit="ratio", period=f"{start_year}..{start_year + 2}",
            )
            calculations.append(calc)
            cagr_id = calc["calculation_id"]
        else:
            gaps.append(_gap(
                "eps_cagr_domain_invalid",
                "T 与 T+2 的 EPS 均值必须都为正，CAGR 才有定义。",
                field="forward_cagr",
            ))

    critical = [row for row in gaps if row["critical"]]
    # Vibe's risk check is explicitly the FY T+2 consensus range, not the
    # nearest-year range.  Keeping min/mean/max at the top level lets the risk
    # gate verify the observation without reverse engineering narrative text.
    dispersion_year = summaries[2] if len(summaries) >= 3 else {}
    consensus_dispersion = {
        "year": dispersion_year.get("year"),
        "min": dispersion_year.get("eps_min"),
        "mean": dispersion_year.get("eps_mean"),
        "max": dispersion_year.get("eps_max"),
        "ratio": dispersion_year.get("dispersion"),
        "unit": "CNY/share",
        "ratio_unit": "倍",
        "range_over_mean": dispersion_year.get("range_over_mean"),
        "source_refs": list(dispersion_year.get("source_refs") or []),
        "calculation_id": dispersion_year.get("dispersion_calculation_id"),
    }
    return {
        "schema_version": ESTIMATES_SCHEMA,
        "stage": "estimates",
        "status": "complete" if not critical else "incomplete",
        "as_of": _iso_date(as_of) or None,
        "required_years": required_years,
        "years": summaries,
        "forward_cagr_t_to_t2": {
            "value": cagr, "unit": "ratio", "period": (
                f"{start_year}..{start_year + 2}" if start_year else None
            ), "calculation_id": cagr_id,
        },
        # Explicit Vibe risk-check payload.  Keeping min/mean/max alongside the
        # derived ratio lets the risk gate verify dispersion without trying to
        # reconstruct a range from narrative text.
        "consensus_dispersion": consensus_dispersion,
        "calculations": calculations,
        "warnings": warnings,
        "gaps": gaps,
        "source_scope": "issuer_consensus_only",
        "excluded_sources": ["research_views", "industry_consensus", "peer_estimates"],
    }


__all__ = [
    "ESTIMATES_SCHEMA",
    "FINANCIALS_SCHEMA",
    "PROFILE_SCHEMA",
    "evaluate_estimates_contract",
    "evaluate_financials_contract",
    "evaluate_profile_contract",
]
