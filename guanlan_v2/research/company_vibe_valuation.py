"""Vibe Research's exact seven-function valuation contract.

This complements the broader enterprise-value DAG with the company-research
cells actually required by Vibe: deducted-profit annualised PE, forward PE,
TTM PE from parts, history percentile, PEG, four-anchor PE digestion years and
forward-versus-TTM judgement.  The four anchors are valuation assumptions, not
price targets; this module never emits an implied or target price.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "company-vibe-valuation@1"
CALC_VERSION = "0.3.2"
CALC_COMPATIBILITY = (
    "Equivalent pure-Python implementation of Vibe calc/SPEC.md@0.3.2; "
    "the upstream calc CLI is not imported at runtime."
)
FUNCTIONS = (
    "pe_deducted_annualized",
    "forward_pe",
    "pe_ttm_from_parts",
    "percentile_rank",
    "peg",
    "pe_digestion_scenarios",
    "forward_vs_ttm_judgement",
)
ANCHORS: Mapping[str, float] = {
    "景气延续": 30.0,
    "中性减速": 25.0,
    "周期重定级_上沿": 22.0,
    "周期重定级_下沿": 18.0,
}


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe(item) for item in value]
    return str(value)


def _digest(value: Any) -> str:
    body = json.dumps(
        _safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _period_end(value: Any) -> date | None:
    parsed = _date(value)
    if parsed is not None:
        return parsed
    matched = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return _date(matched.group(0)) if matched else None


def _unit(value: Any) -> str:
    return str(value or "").strip()


def _money_in_yuan(value: Any, *, fallback_unit: Any = None) -> tuple[float | None, str]:
    """Normalize an explicitly-unitised CNY amount to yuan."""
    raw = value
    unit = _unit(fallback_unit)
    if isinstance(value, Mapping):
        raw = value.get("value")
        unit = _unit(value.get("unit") or fallback_unit)
    number = _number(raw)
    factors = {
        "CNY": 1.0, "RMB": 1.0, "元": 1.0,
        "万元": 10_000.0, "亿元": 100_000_000.0,
    }
    factor = factors.get(unit.upper()) if unit.upper() in {"CNY", "RMB"} else factors.get(unit)
    if number is None or factor is None:
        return None, unit
    return number * factor, unit


def _price_in_yuan_per_share(value: Any, *, fallback_unit: Any = None) -> tuple[float | None, str]:
    raw = value
    unit = _unit(fallback_unit)
    if isinstance(value, Mapping):
        raw = value.get("value")
        unit = _unit(value.get("unit") or fallback_unit)
    number = _number(raw)
    if unit.upper() not in {"CNY/SHARE", "RMB/SHARE"} and unit not in {"元/股", "元/每股"}:
        return None, unit
    return number, unit


def _input(value: float | None, *, unit: str, period: str = "", as_of: str = "") -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "period": period or None,
        "as_of": as_of or None,
    }


def _display(value: Any, unit: str, status: str) -> str | None:
    if status != "ok" or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if unit == "小数":
        return f"{value * 100:.2f}%"
    if unit == "百分点":
        return f"{value:.2f} 个百分点"
    if unit == "%":
        return f"{value:.2f}%"
    if unit in {"倍", "年"}:
        return f"{value:.2f} {unit}"
    return f"{value:.2f} {unit}".strip()


def _calc(function: str, inputs: Mapping[str, Any], refs: Sequence[str], *,
          status: str, value: Any, unit: str = "", reason: str = "",
          details: Mapping[str, Any] | None = None,
          warnings: Sequence[Mapping[str, Any] | str] = ()) -> dict[str, Any]:
    input_refs = sorted({str(ref) for ref in refs if ref})
    calculation_id = "calc-" + _digest({
        "function": function,
        "calc_version": CALC_VERSION,
        "args": inputs,
        "input_refs": input_refs,
    })[:20]
    output = {
        "status": status,
        "value": value,
        "unit": unit,
        "reason": reason,
        "details": dict(details or {}),
        "display": _display(value, unit, status),
    }
    return {
        "calc_id": calculation_id,
        "calculation_id": calculation_id,
        "calc_version": CALC_VERSION,
        "function": function,
        "formula": {
            "market_cap": "quote.total_market_cap OR current_price * shares_outstanding",
            "pe_deducted_annualized": "market_cap / (latest_quarter_deducted_profit * 4)",
            "forward_pe": "current_price / consensus_eps_t",
            "pe_ttm_from_parts": "market_cap / ttm_parent_profit",
            "percentile_rank": "count(history <= current) / valid_history * 100",
            "peg": "pe_deducted_annualized / (forward_cagr * 100)",
            "pe_digestion_scenarios": "ln(pe / anchor) / ln(1 + forward_cagr)",
            "forward_vs_ttm_judgement": "(forward_cagr - parent_profit_ttm_yoy) * 100",
        }.get(function, function),
        "inputs": dict(inputs),
        "input_refs": input_refs,
        "output": output,
        # Generic report-gate compatibility.
        "result": {"value": value, "unit": unit},
        "unit": unit,
        "status": {
            "ok": "complete",
            "not_meaningful": "not_meaningful",
            "error": "incomplete",
        }.get(status, status),
        "warnings": [dict(item) if isinstance(item, Mapping) else {"message": str(item)}
                     for item in warnings],
        "gaps": ([{
            "reason_code": "input_or_domain_unavailable" if status == "error" else "not_meaningful",
            "detail": reason,
            "critical": status == "error",
        }] if reason else []),
    }


def _missing(function: str, missing: Sequence[str], *, refs: Sequence[str] = (),
             inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _calc(
        function, dict(inputs or {}), refs, status="error", value=None,
        reason="缺少可验证输入: " + ", ".join(missing),
        details={"missing_inputs": list(missing)},
    )


def _pe(function: str, market_cap: float | None, profit: float | None,
        refs: Sequence[str], *, annualise: bool, profit_period: str = "",
        as_of: str = "") -> dict[str, Any]:
    inputs = {
        "total_market_cap": _input(market_cap, unit="元", period=f"spot@{as_of}", as_of=as_of),
        "profit": _input(profit, unit="元", period=profit_period, as_of=as_of),
        "annualisation_factor": 4 if annualise else 1,
    }
    if market_cap is None or profit is None:
        return _missing(function, [
            name for name, value in (("total_market_cap", market_cap), ("profit", profit))
            if value is None
        ], refs=refs, inputs=inputs)
    denominator = profit * (4 if annualise else 1)
    if market_cap <= 0 or denominator <= 0:
        return _calc(
            function, inputs, refs, status="not_meaningful", value=None, unit="倍",
            reason="总市值或利润分母 ≤ 0，PE 无意义。",
            details={"denominator_yuan": denominator},
        )
    return _calc(
        function, inputs, refs, status="ok", value=market_cap / denominator, unit="倍",
        details={"denominator_yuan": denominator},
    )


def _find_metric(contract: Mapping[str, Any], key: str) -> tuple[float | None, str, str, str]:
    metrics = contract.get("metrics") if isinstance(contract.get("metrics"), Mapping) else {}
    row = metrics.get(key) if isinstance(metrics.get(key), Mapping) else {}
    return (
        _number(row.get("value")),
        str(row.get("calculation_id") or row.get("calc_id") or ""),
        str(row.get("period") or ""),
        str(row.get("unit") or ""),
    )


_F10_TOTAL_SHARE_REFS = frozenset({
    "pack.sections.company_facts.valuation.total_shares",
    "pack.sections.valuation.f10.total_shares",
})


def _typed_f10_total_shares(
    evidence_pack: Mapping[str, Any], *, as_of: str,
) -> dict[str, Any]:
    """Return only the canonical typed F10 total-share fallback.

    ``extract_valuation_inputs`` already resolves the duplicated
    ``company_facts``/``valuation.f10`` fields, rejects conflicting values and
    attaches an exact evidence-pack reference.  Keep this fallback deliberately
    narrow: no provider multiple is reversed and no unrecognised share-like
    field is guessed.
    """
    from guanlan_v2.research.company_valuation import extract_valuation_inputs

    extracted = extract_valuation_inputs(evidence_pack, as_of=as_of)
    inputs = extracted.get("inputs") if isinstance(extracted.get("inputs"), Mapping) else {}
    facts = inputs.get("facts") if isinstance(inputs.get("facts"), Mapping) else {}
    row = facts.get("shares_outstanding")
    if not isinstance(row, Mapping):
        return {}
    ref = str(row.get("ref") or "")
    if ref not in _F10_TOTAL_SHARE_REFS:
        return {}
    value = _number(row.get("value"))
    unit = _unit(row.get("unit"))
    period = str(row.get("period") or "")
    observed_as_of = str(row.get("as_of") or period)
    cutoff = _date(as_of)
    period_end = _period_end(period)
    observed_date = _date(observed_as_of)
    if (
        value is None or value <= 0
        or unit.lower() not in {"shares", "share"}
        or cutoff is None or period_end is None or period_end > cutoff
        or observed_date is None or observed_date > cutoff
    ):
        return {}
    return {
        "value": value,
        "unit": "shares",
        "period": period,
        "as_of": observed_as_of,
        "ref": ref,
    }


def _valuation_history(pack: Mapping[str, Any], *, as_of: str) -> tuple[
    list[float], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
]:
    sections = pack.get("sections") if isinstance(pack.get("sections"), Mapping) else {}
    valuation = sections.get("valuation") if isinstance(sections.get("valuation"), Mapping) else {}
    history_key = "history_series" if isinstance(valuation.get("history_series"), Mapping) \
        else "history_5y_series"
    history = valuation.get(history_key)
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(history, Mapping):
        issues.append({
            "reason_code": "history_missing",
            "detail": "缺少原始 TTM PE 历史序列。",
        })
        return [], [], issues, warnings, []
    for warning in history.get("warnings") or []:
        if isinstance(warning, Mapping):
            warnings.append({
                "reason_code": str(warning.get("code") or warning.get("reason_code") or "history_source_limitation"),
                "detail": str(warning.get("message") or warning.get("detail") or ""),
            })
    metric = str(history.get("metric") or "").strip().lower()
    if metric != "ttm_pe":
        issues.append({
            "reason_code": "history_metric_mismatch",
            "detail": f"历史序列 metric 必须为 ttm_pe，实际为 {metric or 'missing'}。",
        })
        return [], [], issues, warnings, []
    points = history.get("points")
    cutoff = _date(as_of)
    values: list[float] = []
    refs: list[str] = []
    observations: list[dict[str, Any]] = []
    dated: list[tuple[date, float, str, str]] = []
    seen_dates: set[date] = set()
    for index, raw in enumerate(points if isinstance(points, list) else []):
        if not isinstance(raw, Mapping):
            warnings.append({
                "reason_code": "history_point_shape_invalid",
                "detail": f"历史点 {index} 不是带日期的对象，已跳过。",
            })
            continue
        value = _number(raw.get("value") if raw.get("value") is not None else raw.get("pe_ttm"))
        observed = _date(raw.get("date") or raw.get("as_of") or raw.get("period"))
        ref = f"pack.sections.valuation.{history_key}.points[{index}]"
        provider_ref = str(raw.get("ref") or raw.get("source_ref") or "")
        if value is None or value <= 0:
            warnings.append({
                "reason_code": "history_value_not_positive",
                "detail": f"历史点 {index} 的 TTM PE 不是正数，已跳过。",
            })
            continue
        if observed is None:
            warnings.append({
                "reason_code": "history_date_missing",
                "detail": f"历史点 {index} 缺少可解析日期，已跳过。",
            })
            continue
        if cutoff is not None and observed > cutoff:
            warnings.append({
                "reason_code": "history_point_after_as_of",
                "detail": f"历史点 {observed.isoformat()} 晚于 as_of，已跳过。",
            })
            continue
        if observed in seen_dates:
            warnings.append({
                "reason_code": "history_date_duplicate",
                "detail": f"历史日期 {observed.isoformat()} 重复，后续点已跳过。",
            })
            continue
        seen_dates.add(observed)
        dated.append((observed, value, ref, provider_ref))
    dated.sort(key=lambda item: item[0])
    for observed, value, ref, provider_ref in dated:
        values.append(value)
        refs.append(ref)
        observations.append({
            "date": observed.isoformat(), "value": value, "unit": "倍", "ref": ref,
            "provider_ref": provider_ref or None,
        })
    if len(values) < 20:
        issues.append({
            "reason_code": "history_sample_insufficient",
            "detail": f"有效、去重且不晚于 as_of 的 TTM PE 历史样本 {len(values)} < 20。",
            "valid_samples": len(values),
            "required_samples": 20,
        })
    return values, refs, issues, warnings, observations


def build_vibe_valuation_contract(
    evidence_pack: Mapping[str, Any], financials_contract: Mapping[str, Any],
    estimates_contract: Mapping[str, Any], *, as_of: str,
) -> dict[str, Any]:
    """Run the seven Vibe valuation functions with explicit missing semantics."""
    sections = evidence_pack.get("sections") if isinstance(evidence_pack.get("sections"), Mapping) else {}
    quote = sections.get("quote_live") if isinstance(sections.get("quote_live"), Mapping) else {}
    financials = sections.get("financials") if isinstance(sections.get("financials"), Mapping) else {}
    rows = financials.get("rows") if isinstance(financials.get("rows"), list) else []
    cutoff = _date(as_of)
    statement_candidates = [
        row for row in rows
        if isinstance(row, Mapping)
        and _date(row.get("period") or row.get("report_date")) is not None
        and (cutoff is None or _date(row.get("period") or row.get("report_date")) <= cutoff)
    ]
    latest_statement = max(
        statement_candidates,
        key=lambda row: _date(row.get("period") or row.get("report_date")),
        default={},
    )
    latest_statement_index = next(
        (index for index, row in enumerate(rows) if row is latest_statement), None,
    )
    latest_quarter = (
        financials_contract.get("metrics", {}).get("latest_quarter", {})
        if isinstance(financials_contract.get("metrics"), Mapping) else {}
    )

    quote_as_of = str(quote.get("as_of") or "")
    quote_date = _date(quote_as_of)
    quote_temporally_valid = bool(
        cutoff is not None and quote_date is not None and quote_date <= cutoff
    )
    price, price_unit = _price_in_yuan_per_share(
        quote.get("price"), fallback_unit=quote.get("unit"),
    )
    if not quote_temporally_valid:
        price = None
    price_ref = "pack.sections.quote_live.price"

    has_quote_market_cap = "total_market_cap" in quote or "market_cap" in quote
    market_cap: float | None = None
    market_cap_inputs: dict[str, Any]
    market_cap_refs: tuple[str, ...]
    market_cap_reason = ""
    market_cap_warnings: list[dict[str, Any]] = []
    if has_quote_market_cap:
        market_cap_key = "total_market_cap" if "total_market_cap" in quote else "market_cap"
        market_cap_raw = quote.get(market_cap_key)
        market_cap_raw_value = _number(market_cap_raw)
        market_cap, market_cap_observed_unit = _money_in_yuan(
            market_cap_raw,
            fallback_unit=(
                quote.get(f"{market_cap_key}_unit")
                or quote.get("total_market_cap_unit")
                or quote.get("market_cap_unit")
            ),
        )
        if not quote_temporally_valid:
            market_cap = None
            market_cap_reason = "quote_live.as_of 缺失、无效或晚于研究截止日。"
        elif market_cap is None:
            market_cap_reason = (
                "quote_live.total_market_cap 缺少明确的人民币金额单位"
                f"（实际单位: {market_cap_observed_unit or 'missing'}）。"
            )
        elif market_cap <= 0:
            market_cap = None
            market_cap_reason = "quote_live.total_market_cap 必须为正数。"
        market_cap_inputs = {
            "quote_total_market_cap": _input(
                market_cap_raw_value, unit=market_cap_observed_unit,
                period=f"spot@{quote_as_of or as_of}", as_of=quote_as_of,
            ),
        }
        market_cap_refs = (f"pack.sections.quote_live.{market_cap_key}",)
        market_cap_source = "quote_total_market_cap"
        market_cap_method = "quote_total_market_cap"
    else:
        shares_raw = latest_statement.get("shares_outstanding")
        shares = _number(shares_raw)
        shares_unit = _unit(
            shares_raw.get("unit") if isinstance(shares_raw, Mapping)
            else latest_statement.get("shares_outstanding_unit") or "股"
        )
        shares_ref = (
            f"pack.sections.financials.rows[{latest_statement_index}].shares_outstanding"
            if latest_statement_index is not None and shares_raw is not None else ""
        )
        provider_shares_ref = str(latest_statement.get("balance_source_ref") or "")
        statement_period = str(
            latest_statement.get("period") or latest_statement.get("report_date") or ""
        )
        shares_as_of = statement_period
        if shares_raw is None:
            typed_f10_shares = _typed_f10_total_shares(evidence_pack, as_of=as_of)
            if typed_f10_shares:
                shares = _number(typed_f10_shares.get("value"))
                shares_unit = _unit(typed_f10_shares.get("unit"))
                shares_ref = str(typed_f10_shares.get("ref") or "")
                provider_shares_ref = shares_ref
                statement_period = str(typed_f10_shares.get("period") or "")
                shares_as_of = str(
                    typed_f10_shares.get("as_of") or statement_period
                )
        if shares_unit.lower() not in {"shares", "share"} and shares_unit != "股":
            shares = None
        if price is None:
            market_cap_reason = (
                "缺少带 CNY/share 单位且 as_of 不晚于截止日的现价。"
            )
        elif shares is None or shares <= 0:
            market_cap_reason = "缺少最新、带股数语义且为正数的总股本。"
        elif not shares_ref:
            market_cap_reason = "总股本无法定位到 evidence-pack 行，拒绝生成无血缘的市值。"
        else:
            market_cap = price * shares
            market_cap_warnings.append({
                "reason_code": "quote_market_cap_missing_derived_fallback",
                "detail": "quote_live 未提供 total_market_cap，使用现价 × 最新总股本推导。",
            })
        market_cap_inputs = {
            "current_price": _input(
                price, unit="元/股", period=f"spot@{quote_as_of or as_of}", as_of=quote_as_of,
            ),
            "shares_outstanding": _input(
                shares, unit="股", period=statement_period, as_of=shares_as_of,
            ),
        }
        market_cap_refs = (
            price_ref,
            shares_ref,
        )
        market_cap_source = "derived"
        market_cap_method = "current_price_times_shares_outstanding"
    market_cap_calc = _calc(
        "market_cap", market_cap_inputs, market_cap_refs,
        status="ok" if market_cap is not None and market_cap > 0 else "error",
        value=market_cap if market_cap is not None and market_cap > 0 else None,
        unit="元", reason=market_cap_reason,
        details={
            "source": market_cap_source,
            "method": market_cap_method,
            "provider_shares_ref": (
                provider_shares_ref if not has_quote_market_cap else None
            ),
        },
        warnings=market_cap_warnings,
    )
    market_cap_ref = market_cap_calc["calculation_id"]

    latest_deducted, _ = _money_in_yuan(
        latest_quarter.get("net_profit_deducted"),
        fallback_unit=latest_quarter.get("unit") or financials_contract.get("unit"),
    )
    latest_period = str(latest_quarter.get("period") or "")
    latest_period_end = _period_end(latest_period)
    if (
        cutoff is None or latest_period_end is None
        or latest_period_end > cutoff
    ):
        latest_deducted = None
    latest_deducted_ref = str(
        (latest_quarter.get("calculation_ids") or {}).get("net_profit_deducted")
        if isinstance(latest_quarter.get("calculation_ids"), Mapping) else ""
    )
    if not latest_deducted_ref:
        latest_deducted = None
    ttm_parent_raw, ttm_parent_ref, ttm_parent_period, ttm_parent_unit = _find_metric(
        financials_contract, "ttm_net_profit_parent"
    )
    ttm_parent, _ = _money_in_yuan(ttm_parent_raw, fallback_unit=ttm_parent_unit)
    ttm_parent_period_end = _period_end(ttm_parent_period)
    if cutoff is None or ttm_parent_period_end is None or ttm_parent_period_end > cutoff:
        ttm_parent = None
    if not ttm_parent_ref:
        ttm_parent = None
    ttm_parent_yoy, ttm_parent_yoy_ref, ttm_parent_yoy_period, _ = _find_metric(
        financials_contract, "ttm_net_profit_parent_yoy"
    )
    ttm_parent_yoy_period_end = _period_end(ttm_parent_yoy_period)
    if (
        cutoff is None or ttm_parent_yoy_period_end is None
        or ttm_parent_yoy_period_end > cutoff
    ):
        ttm_parent_yoy = None
    if not ttm_parent_yoy_ref:
        ttm_parent_yoy = None
    years = estimates_contract.get("years") if isinstance(estimates_contract.get("years"), list) else []
    year_t_number = cutoff.year if cutoff else None
    year_t = next((
        row for row in years
        if isinstance(row, Mapping) and _number(row.get("year")) == year_t_number
    ), {})
    eps_t, eps_unit = _price_in_yuan_per_share(
        year_t.get("eps_mean"), fallback_unit=year_t.get("unit"),
    )
    eps_ref = str((year_t.get("source_refs") or [""])[0]) if year_t else ""
    if not eps_ref:
        eps_t = None
    cagr_row = estimates_contract.get("forward_cagr_t_to_t2")
    cagr_row = cagr_row if isinstance(cagr_row, Mapping) else {}
    cagr = _number(cagr_row.get("value"))
    cagr_ref = str(cagr_row.get("calculation_id") or "")
    if not cagr_ref:
        cagr = None

    pe_deducted = _pe(
        "pe_deducted_annualized", market_cap, latest_deducted,
        (market_cap_ref, latest_deducted_ref), annualise=True,
        profit_period=latest_period, as_of=as_of,
    )
    pe_ttm = _pe(
        "pe_ttm_from_parts", market_cap, ttm_parent,
        (market_cap_ref, ttm_parent_ref), annualise=False,
        profit_period=ttm_parent_period, as_of=as_of,
    )
    forward_inputs = {
        "current_price": _input(
            price, unit="元/股", period=f"spot@{quote_as_of or as_of}", as_of=quote_as_of,
        ),
        "consensus_eps_t": _input(
            eps_t, unit="元/股", period=str(year_t.get("year") or ""), as_of=as_of,
        ),
    }
    if price is None or eps_t is None:
        forward = _missing("forward_pe", [
            name for name, value in (("current_price", price), ("consensus_eps_t", eps_t))
            if value is None
        ], refs=(price_ref, eps_ref), inputs=forward_inputs)
    elif price <= 0 or eps_t <= 0:
        forward = _calc(
            "forward_pe", forward_inputs, (price_ref, eps_ref),
            status="not_meaningful", value=None, unit="倍",
            reason="价格或一致预期 EPS ≤ 0，前瞻 PE 无意义。",
        )
    else:
        forward = _calc(
            "forward_pe", forward_inputs, (price_ref, eps_ref), status="ok",
            value=price / eps_t, unit="倍",
        )

    current_ttm_pe = _number(pe_ttm["output"].get("value"))
    history, history_refs, history_issues, history_warnings, history_register = _valuation_history(
        evidence_pack, as_of=as_of,
    )
    percentile_inputs = {
        "history": history_register,
        "current_ttm_pe": _input(current_ttm_pe, unit="倍", period=ttm_parent_period, as_of=as_of),
        "metric": "ttm_pe",
    }
    if current_ttm_pe is None:
        percentile = _missing(
            "percentile_rank", ["current_ttm_pe"],
            refs=(*history_refs, pe_ttm["calculation_id"]), inputs=percentile_inputs,
        )
    elif history_issues:
        percentile = _calc(
            "percentile_rank", percentile_inputs,
            (*history_refs, pe_ttm["calculation_id"]), status="not_meaningful",
            value=None, unit="%",
            reason="；".join(str(item.get("detail") or item.get("reason_code"))
                            for item in history_issues),
            details={"n": len(history), "required": 20, "issues": history_issues},
            warnings=history_warnings,
        )
    else:
        percentile = _calc(
            "percentile_rank", percentile_inputs,
            (*history_refs, pe_ttm["calculation_id"]), status="ok",
            value=100 * sum(value <= current_ttm_pe for value in history) / len(history),
            unit="%", details={"n": len(history), "metric": "ttm_pe"},
            warnings=history_warnings,
        )

    pe_value = _number(pe_deducted["output"].get("value"))
    peg_inputs = {
        "pe_deducted_annualized": _input(pe_value, unit="倍", period=latest_period, as_of=as_of),
        "forward_cagr": _input(
            cagr, unit="小数", period=str(cagr_row.get("period") or ""), as_of=as_of,
        ),
    }
    if pe_value is None or cagr is None:
        peg = _missing("peg", [
            name for name, value in (("pe_deducted_annualized", pe_value), ("forward_cagr", cagr))
            if value is None
        ], refs=(pe_deducted["calculation_id"], cagr_ref), inputs=peg_inputs)
    elif pe_value <= 0 or cagr <= 0:
        peg = _calc(
            "peg", peg_inputs,
            (pe_deducted["calculation_id"], cagr_ref), status="not_meaningful",
            value=None, unit="倍", reason="PE 或 CAGR ≤ 0，PEG 无意义。",
        )
    else:
        peg = _calc(
            "peg", peg_inputs,
            (pe_deducted["calculation_id"], cagr_ref), status="ok",
            value=pe_value / (cagr * 100), unit="倍",
        )

    scenario_details: dict[str, Any] = {}
    digestion_inputs = {
        **peg_inputs,
        "anchors": {name: {"value": value, "unit": "倍"} for name, value in ANCHORS.items()},
    }
    if pe_value is None or cagr is None:
        digestion = _missing("pe_digestion_scenarios", [
            name for name, value in (("pe_deducted_annualized", pe_value), ("forward_cagr", cagr))
            if value is None
        ], refs=(pe_deducted["calculation_id"], cagr_ref), inputs=digestion_inputs)
    else:
        any_ok = False
        for name, anchor in ANCHORS.items():
            if pe_value <= 0:
                status, value, reason, below = "not_meaningful", None, "PE ≤ 0。", False
            elif pe_value <= anchor:
                status, value, reason, below = "ok", 0.0, "", True
                any_ok = True
            elif cagr <= 0:
                status, value, reason, below = "not_meaningful", None, "CAGR ≤ 0，无法消化到锚。", False
            else:
                status = "ok"
                value = math.log(pe_value / anchor) / math.log(1 + cagr)
                reason, below, any_ok = "", False, True
            scenario_details[name] = {
                "status": status, "value": value, "unit": "年", "reason": reason,
                "display": _display(value, "年", status),
                "details": {"anchor": anchor, "below_anchor": below},
            }
        digestion = _calc(
            "pe_digestion_scenarios", digestion_inputs,
            (pe_deducted["calculation_id"], cagr_ref),
            status="ok" if any_ok else "not_meaningful",
            value=scenario_details if any_ok else None, unit="年",
            reason="" if any_ok else "四个情景均无意义。",
            details={"anchors": dict(ANCHORS), "scenarios": scenario_details,
                     "is_price_target": False},
        )

    judgement_inputs = {
        "forward_cagr": _input(
            cagr, unit="小数", period=str(cagr_row.get("period") or ""), as_of=as_of,
        ),
        "ttm_parent_profit_yoy": _input(
            ttm_parent_yoy, unit="小数", period=ttm_parent_period, as_of=as_of,
        ),
        "tolerance": {"value": 10, "unit": "百分点"},
    }
    if cagr is None or ttm_parent_yoy is None:
        judgement = _missing("forward_vs_ttm_judgement", [
            name for name, value in (("forward_cagr", cagr), ("ttm_parent_profit_yoy", ttm_parent_yoy))
            if value is None
        ], refs=(cagr_ref, ttm_parent_yoy_ref), inputs=judgement_inputs)
    else:
        difference = (cagr - ttm_parent_yoy) * 100
        category = "approx" if abs(difference) <= 10 else (
            "forward_below" if difference < 0 else "forward_above"
        )
        labels = {
            "approx": "前瞻 ≈ TTM：增长已兑现，可信度相对较高",
            "forward_below": "前瞻远低于 TTM：隐含明显减速，需要解释依据",
            "forward_above": "前瞻远高于 TTM：预期偏高，需要订单/产能/预告等额外证据",
        }
        judgement = _calc(
            "forward_vs_ttm_judgement",
            judgement_inputs,
            (cagr_ref, ttm_parent_yoy_ref), status="ok", value=difference,
            unit="百分点", details={"category": category, "label": labels[category]},
        )

    calculations = [
        market_cap_calc, pe_deducted, forward, pe_ttm, percentile, peg, digestion, judgement,
    ]
    gaps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for row in calculations:
        warnings.extend(row.get("warnings") or [])
        output = row["output"]
        if output["status"] == "error":
            gaps.append({
                "code": f"{row['function']}_unavailable",
                "reason_code": f"{row['function']}_unavailable",
                "message": output["reason"],
                "detail": output["reason"],
                "operation": row["function"],
                "critical": row["function"] != "percentile_rank",
            })
        elif output["status"] == "not_meaningful":
            gaps.append({
                "code": f"{row['function']}_not_meaningful",
                "reason_code": f"{row['function']}_not_meaningful",
                "message": output["reason"],
                "detail": output["reason"],
                "operation": row["function"],
                "critical": False,
            })
    critical = [gap for gap in gaps if gap["critical"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "calc_version": CALC_VERSION,
        "calc_compatibility": CALC_COMPATIBILITY,
        "stage": "valuation",
        "status": "complete" if not critical else "incomplete",
        "as_of": as_of,
        "functions_required": list(FUNCTIONS),
        "calculations": calculations,
        "gaps": gaps,
        "warnings": warnings,
        "guardrails": {
            "emits_price_target": False,
            "scenario_anchors_are_pe_multiples_not_prices": True,
            "provider_multiples_never_reverse_engineered": True,
        },
    }


__all__ = [
    "ANCHORS", "CALC_COMPATIBILITY", "CALC_VERSION", "FUNCTIONS", "SCHEMA_VERSION",
    "build_vibe_valuation_contract",
]
