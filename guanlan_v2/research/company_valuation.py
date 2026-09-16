"""Pure, deterministic and auditable company-valuation calculations.

The module deliberately owns no data fetching, clock reads, model calls or
workspace writes.  Callers supply normalized facts whose value, unit, period,
``as_of`` and evidence reference are all explicit.  A calculation which cannot
prove those contracts is still emitted as a DAG node, with ``result=None`` and
structured gaps; inputs are never inferred from a provider multiple.

Normalized input shape (``company-valuation-input@1``)::

    {
      "valuation_as_of": "2026-08-30",
      "facts": {
        "current_price": {
          "value": 10.0, "unit": "CNY/share", "period": "spot",
          "as_of": "2026-08-30", "ref": "pack.sections.quote_live.price"
        },
        "shares_outstanding": {
          "value": 100, "unit": "shares", "period": "2026-06-30",
          "as_of": "2026-08-30", "ref": "..."
        }
      },
      "valuation_history": {
        "metric": "ttm_pe", "points": [<metric>, ...]
      },
      "scenarios": {
        "bull": {"method": "forward_pe", "target_multiple": <metric>},
        "base": {"method": "forward_pe", "target_multiple": <metric>},
        "bear": {"method": "forward_pe", "target_multiple": <metric>}
      }
    }

Money uses an ISO-4217 base unit (``CNY``/``USD``), per-share values use
``CNY/share``/``USD/share``, share counts use ``shares`` and multiples use
``x``.  Scaled units such as ``CNY mn`` are rejected instead of being guessed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "company-valuation-dag@1"
INPUT_SCHEMA_VERSION = "company-valuation-input@1"
EXTRACTION_SCHEMA_VERSION = "company-valuation-input-extraction@1"

CORE_CALC_IDS = (
    "valuation.market_cap",
    "valuation.enterprise_value",
    "valuation.ttm_pe",
    "valuation.forward_pe",
)
SCENARIO_CASES = ("bull", "base", "bear")

# JSON-like documentation for adapters.  ``required`` means required for the
# four-node core gate; optional fields become binding only when supplied.
NORMALIZED_INPUT_CONTRACT: Mapping[str, Any] = {
    "metric_required_fields": ("value", "unit", "period", "as_of", "ref"),
    "facts": {
        "current_price": {"kind": "money_per_share", "required": True},
        "shares_outstanding": {"kind": "shares", "required": True},
        "total_debt": {"kind": "money", "required": True},
        "cash_and_equivalents": {"kind": "money", "required": True},
        "eps_ttm": {"kind": "money_per_share", "required_one_of": "ttm_earnings"},
        "net_income_ttm": {"kind": "money", "required_one_of": "ttm_earnings"},
        "forward_eps": {"kind": "money_per_share", "required": True},
        "book_value_per_share": {"kind": "money_per_share", "optional": True},
        "book_value": {"kind": "money", "optional": True},
        "preferred_equity": {"kind": "money", "optional_ev_adjustment": True},
        "minority_interest": {"kind": "money", "optional_ev_adjustment": True},
        "non_operating_investments": {"kind": "money", "optional_ev_adjustment": True},
    },
    "valuation_history": {"metric": ("ttm_pe", "pb"), "points": "metric[]"},
    "valuation_history_pb": {"metric": "pb", "points": "metric[]", "optional": True},
    "scenarios": {
        "cases": SCENARIO_CASES,
        "methods": ("forward_pe", "ttm_pe", "pb", "direct_price"),
    },
}

_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PER_SHARE_UNIT = re.compile(r"^([A-Z]{3})/share$", re.IGNORECASE)
_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TTM_PERIOD = re.compile(r"\b(?:TTM|LTM)\b", re.IGNORECASE)
_FORWARD_PERIOD = re.compile(
    r"(?:\bNTM\b|\bFWD\b|\bFORWARD\b|\bFY\d{4}E\b|\bFY[1-9]\b)",
    re.IGNORECASE,
)
_ALT_METHOD_REL_TOLERANCE = Decimal("0.005")


@dataclass(frozen=True)
class _Metric:
    key: str
    value: Decimal
    unit: str
    currency: str | None
    period: str
    as_of: str
    ref: str

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _number(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "period": self.period,
            "as_of": self.as_of,
            "ref": self.ref,
        }


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value, 80)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _gap(code: str, message: str, *, input_keys: Sequence[str] = (),
         refs: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "code": code,
        "message": _text(message, 500),
        "input_keys": list(dict.fromkeys(_text(item, 160) for item in input_keys if item)),
        "refs": list(dict.fromkeys(_text(item, 240) for item in refs if item)),
    }


def _warning(code: str, message: str, *, refs: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "code": code,
        "message": _text(message, 500),
        "refs": list(dict.fromkeys(_text(item, 240) for item in refs if item)),
    }


def _canonical_unit(raw_unit: Any, raw_currency: Any, kind: str) -> tuple[str | None, str | None]:
    unit = _text(raw_unit, 40).replace(" ", "")
    currency = _text(raw_currency, 8).upper()
    if currency and not _ISO_CURRENCY.fullmatch(currency):
        return None, None
    if kind == "shares":
        return ("shares", None) if unit.lower() in {"share", "shares"} else (None, None)
    if kind == "multiple":
        return ("x", None) if unit.lower() in {"x", "multiple", "times"} else (None, None)
    if kind == "money":
        embedded = unit.upper()
        if _ISO_CURRENCY.fullmatch(embedded):
            if currency and currency != embedded:
                return None, None
            return embedded, embedded
        if unit.lower() in {"money", "currency"} and currency:
            return currency, currency
        return None, None
    if kind == "money_per_share":
        matched = _PER_SHARE_UNIT.fullmatch(unit)
        if matched:
            embedded = matched.group(1).upper()
            if currency and currency != embedded:
                return None, None
            return f"{embedded}/share", embedded
        if unit.lower() in {"money/share", "currency/share"} and currency:
            return f"{currency}/share", currency
        return None, None
    raise ValueError(f"unknown metric kind: {kind}")


def _parse_metric(raw: Any, *, key: str, kind: str,
                  valuation_as_of: date | None) -> tuple[_Metric | None, list[dict[str, Any]]]:
    if not isinstance(raw, Mapping):
        return None, [_gap(
            "invalid_input_shape", f"{key} must be an object with value/unit/period/as_of/ref.",
            input_keys=(key,),
        )]
    gaps: list[dict[str, Any]] = []
    value = _decimal(raw.get("value"))
    if value is None:
        gaps.append(_gap("invalid_numeric_value", f"{key}.value must be finite numeric data.",
                         input_keys=(key,)))
    unit, currency = _canonical_unit(raw.get("unit"), raw.get("currency"), kind)
    if unit is None:
        gaps.append(_gap(
            "invalid_or_ambiguous_unit",
            f"{key}.unit is missing, scaled, unsupported, or conflicts with currency.",
            input_keys=(key,), refs=(_text(raw.get("ref"), 240),),
        ))
    period = _text(raw.get("period"), 120)
    if not period:
        gaps.append(_gap("missing_period", f"{key}.period is required.", input_keys=(key,)))
    observed = _as_date(raw.get("as_of"))
    if observed is None:
        gaps.append(_gap("missing_or_invalid_as_of", f"{key}.as_of must be an ISO date/time.",
                         input_keys=(key,)))
    elif valuation_as_of is not None and observed > valuation_as_of:
        gaps.append(_gap(
            "future_dated_input",
            f"{key}.as_of {observed.isoformat()} is after valuation_as_of {valuation_as_of.isoformat()}.",
            input_keys=(key,), refs=(_text(raw.get("ref"), 240),),
        ))
    ref = _text(raw.get("ref") or raw.get("source_ref"), 240)
    if not ref:
        gaps.append(_gap("missing_input_ref", f"{key}.ref is required for auditability.",
                         input_keys=(key,)))
    if gaps:
        return None, gaps
    assert value is not None and unit is not None and observed is not None
    return _Metric(
        key=key, value=value, unit=unit, currency=currency, period=period,
        as_of=observed.isoformat(), ref=ref,
    ), []


def _node(calc_id: str, formula: str, *, status: str, result: Decimal | None,
          unit: str | None, inputs: Iterable[_Metric] = (),
          depends_on: Sequence[str] = (), period: str | None = None,
          warnings: Sequence[Mapping[str, Any]] = (),
          gaps: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    metrics = list(inputs)
    return {
        "calc_id": calc_id,
        "formula": formula,
        "input_refs": list(dict.fromkeys(metric.ref for metric in metrics)),
        "depends_on": list(depends_on),
        "status": status,
        "result": _number(result) if result is not None else None,
        "unit": unit,
        "period": period,
        "warnings": [dict(item) for item in warnings],
        "gaps": [dict(item) for item in gaps],
    }


def _missing(key: str) -> dict[str, Any]:
    return _gap("missing_input", f"Required normalized input {key!r} is unavailable.",
                input_keys=(key,))


def _period_anchor(period: str) -> str | None:
    matches = _ISO_DATE.findall(period)
    return matches[-1] if matches else None


def _period_definition(period: str) -> str:
    """Return the economic period convention without its changing date anchor."""
    if _TTM_PERIOD.search(period):
        return "ttm"
    if _FORWARD_PERIOD.search(period):
        return "forward"
    if _period_anchor(period):
        return "point_in_time"
    return period.strip().lower()


def _same_balance_period(metrics: Sequence[_Metric]) -> bool:
    periods = {metric.period for metric in metrics}
    if len(periods) <= 1:
        return True
    anchors = {_period_anchor(metric.period) for metric in metrics}
    return None not in anchors and len(anchors) == 1


def _same_currency(metrics: Sequence[_Metric]) -> bool:
    currencies = {metric.currency for metric in metrics}
    return None not in currencies and len(currencies) == 1


def _relative_difference(left: Decimal, right: Decimal) -> Decimal:
    denominator = max(abs(left), abs(right), Decimal("1e-18"))
    return abs(left - right) / denominator


def _metric_or_gaps(metrics: Mapping[str, _Metric], invalid: Mapping[str, list[dict[str, Any]]],
                    key: str) -> tuple[_Metric | None, list[dict[str, Any]]]:
    if key in metrics:
        return metrics[key], []
    if key in invalid:
        return None, list(invalid[key])
    return None, [_missing(key)]


def _positive(metric: _Metric, label: str) -> list[dict[str, Any]]:
    if metric.value > 0:
        return []
    return [_gap(
        "non_positive_denominator",
        f"{label} must be greater than zero; the multiple was not calculated.",
        input_keys=(metric.key,), refs=(metric.ref,),
    )]


def _market_cap_node(metrics: Mapping[str, _Metric], invalid: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    price, price_gaps = _metric_or_gaps(metrics, invalid, "current_price")
    shares, shares_gaps = _metric_or_gaps(metrics, invalid, "shares_outstanding")
    gaps = [*price_gaps, *shares_gaps]
    inputs = [item for item in (price, shares) if item is not None]
    if price is not None and price.value <= 0:
        gaps.append(_gap("non_positive_price", "current_price must be greater than zero.",
                         input_keys=(price.key,), refs=(price.ref,)))
    if shares is not None and shares.value <= 0:
        gaps.append(_gap("non_positive_share_count", "shares_outstanding must be greater than zero.",
                         input_keys=(shares.key,), refs=(shares.ref,)))
    if gaps:
        return _node("valuation.market_cap", "current_price * shares_outstanding",
                     status="invalid" if inputs else "incomplete", result=None,
                     unit=price.currency if price else None, inputs=inputs, gaps=gaps)
    assert price is not None and shares is not None
    warnings = []
    anchor = _period_anchor(shares.period)
    if anchor and anchor != price.as_of:
        warnings.append(_warning(
            "share_count_period_differs_from_spot",
            "Spot price is combined with the explicitly dated share count; no share-count refresh was inferred.",
            refs=(price.ref, shares.ref),
        ))
    return _node(
        "valuation.market_cap", "current_price * shares_outstanding",
        status="complete", result=price.value * shares.value, unit=price.currency,
        inputs=(price, shares), period=f"spot@{price.as_of}", warnings=warnings,
    )


def _enterprise_value_node(market_cap: Mapping[str, Any], metrics: Mapping[str, _Metric],
                           invalid: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    debt, debt_gaps = _metric_or_gaps(metrics, invalid, "total_debt")
    cash, cash_gaps = _metric_or_gaps(metrics, invalid, "cash_and_equivalents")
    gaps = [*debt_gaps, *cash_gaps]
    bridge = [item for item in (debt, cash) if item is not None]
    if market_cap["status"] != "complete":
        gaps.append(_gap("dependency_unavailable", "market_cap is not complete.",
                         input_keys=("market_cap",)))
    for item in bridge:
        if item.value < 0:
            gaps.append(_gap("negative_balance_not_allowed", f"{item.key} cannot be negative.",
                             input_keys=(item.key,), refs=(item.ref,)))
    optional_keys = (
        "preferred_equity", "minority_interest", "non_operating_investments",
    )
    optional: list[_Metric] = []
    for key in optional_keys:
        if key in metrics:
            optional.append(metrics[key])
        elif key in invalid:
            gaps.extend(invalid[key])
    balances = [*bridge, *optional]
    if balances and not _same_currency(balances):
        gaps.append(_gap("mixed_currency", "EV bridge inputs must use one currency.",
                         input_keys=tuple(item.key for item in balances),
                         refs=tuple(item.ref for item in balances)))
    market_currency = market_cap.get("unit")
    if balances and market_currency and any(item.currency != market_currency for item in balances):
        gaps.append(_gap("mixed_currency", "Market cap and EV bridge currencies differ.",
                         input_keys=("market_cap", *(item.key for item in balances)),
                         refs=tuple(item.ref for item in balances)))
    if balances and not _same_balance_period(balances):
        gaps.append(_gap("mixed_balance_period", "Debt, cash and supplied EV adjustments must share a period.",
                         input_keys=tuple(item.key for item in balances),
                         refs=tuple(item.ref for item in balances)))
    if gaps:
        return _node(
            "valuation.enterprise_value",
            "market_cap + total_debt + preferred_equity + minority_interest - cash_and_equivalents - non_operating_investments",
            status="invalid" if bridge or optional else "incomplete", result=None,
            unit=market_currency, inputs=balances, depends_on=("valuation.market_cap",),
            gaps=gaps,
        )
    assert debt is not None and cash is not None
    value = Decimal(str(market_cap["result"])) + debt.value - cash.value
    formula = "market_cap + total_debt - cash_and_equivalents"
    by_key = {item.key: item for item in optional}
    if "preferred_equity" in by_key:
        value += by_key["preferred_equity"].value
        formula += " + preferred_equity"
    if "minority_interest" in by_key:
        value += by_key["minority_interest"].value
        formula += " + minority_interest"
    if "non_operating_investments" in by_key:
        value -= by_key["non_operating_investments"].value
        formula += " - non_operating_investments"
    warnings = []
    absent = [key for key in optional_keys if key not in metrics and key not in invalid]
    if absent:
        warnings.append(_warning(
            "optional_ev_adjustments_not_supplied",
            "Absent optional EV adjustments were excluded from the displayed formula, not assumed to be zero.",
        ))
    if value < 0:
        warnings.append(_warning("negative_enterprise_value", "The explicit net-cash bridge produced negative EV."))
    return _node(
        "valuation.enterprise_value", formula, status="complete", result=value,
        unit=market_currency, inputs=balances, depends_on=("valuation.market_cap",),
        period=debt.period, warnings=warnings,
    )


def _multiple_node(*, calc_id: str, label: str, price_key: str, per_share_key: str,
                   total_key: str | None, market_cap: Mapping[str, Any],
                   metrics: Mapping[str, _Metric], invalid: Mapping[str, list[dict[str, Any]]],
                   required_period: str) -> dict[str, Any]:
    price, price_gaps = _metric_or_gaps(metrics, invalid, price_key)
    per_share, per_share_gaps = _metric_or_gaps(metrics, invalid, per_share_key)
    per_share_ready = price is not None and per_share is not None
    per_share_issues = [*price_gaps, *per_share_gaps]
    if per_share_ready:
        assert price is not None and per_share is not None
        if not _same_currency((price, per_share)):
            per_share_issues.append(_gap("mixed_currency", f"{label} per-share inputs use different currencies.",
                                                input_keys=(price.key, per_share.key), refs=(price.ref, per_share.ref)))
        per_share_issues.extend(_positive(per_share, per_share.key))
        if required_period == "ttm" and not _TTM_PERIOD.search(per_share.period):
            per_share_issues.append(_gap("period_not_ttm", f"{per_share.key}.period must explicitly be TTM/LTM.",
                                                input_keys=(per_share.key,), refs=(per_share.ref,)))
        if required_period == "forward" and not _FORWARD_PERIOD.search(per_share.period):
            per_share_issues.append(_gap("period_not_forward", f"{per_share.key}.period must explicitly be a forward period.",
                                                input_keys=(per_share.key,), refs=(per_share.ref,)))

    total: _Metric | None = None
    total_issues: list[dict[str, Any]] = []
    total_ready = False
    if total_key is not None:
        total, total_gaps = _metric_or_gaps(metrics, invalid, total_key)
        total_issues.extend(total_gaps)
        total_ready = total is not None and market_cap.get("status") == "complete"
        if total is not None:
            if total.currency != market_cap.get("unit"):
                total_issues.append(_gap("mixed_currency", f"market_cap and {total.key} currencies differ.",
                                                 input_keys=("market_cap", total.key), refs=(total.ref,)))
            total_issues.extend(_positive(total, total.key))
            if required_period == "ttm" and not _TTM_PERIOD.search(total.period):
                total_issues.append(_gap("period_not_ttm", f"{total.key}.period must explicitly be TTM/LTM.",
                                                 input_keys=(total.key,), refs=(total.ref,)))
            shares = metrics.get("shares_outstanding")
            if shares is not None:
                ttm_anchor = _period_anchor(total.period)
                shares_anchor = _period_anchor(shares.period)
                if ttm_anchor and shares_anchor and ttm_anchor != shares_anchor:
                    total_issues.append(_gap(
                        "mixed_period", f"{total.key} and shares_outstanding end on different dates.",
                        input_keys=(total.key, shares.key), refs=(total.ref, shares.ref),
                    ))
        if market_cap.get("status") != "complete":
            total_issues.append(_gap("dependency_unavailable", "market_cap is not complete.",
                                             input_keys=("market_cap",)))

    candidates: list[tuple[str, Decimal, list[_Metric], str]] = []
    if per_share_ready and not per_share_issues:
        assert price is not None and per_share is not None
        candidates.append((f"{price_key} / {per_share_key}", price.value / per_share.value,
                           [price, per_share], per_share.period))
    if total_key is not None and total_ready and not total_issues:
        assert total is not None
        candidates.append((f"market_cap / {total_key}",
                           Decimal(str(market_cap["result"])) / total.value,
                           [total], total.period))

    if len(candidates) == 2 and _relative_difference(candidates[0][1], candidates[1][1]) > _ALT_METHOD_REL_TOLERANCE:
        refs = tuple(metric.ref for candidate in candidates for metric in candidate[2])
        return _node(
            calc_id, f"{candidates[0][0]} OR {candidates[1][0]}", status="invalid",
            result=None, unit="x", inputs=[metric for candidate in candidates for metric in candidate[2]],
            depends_on=("valuation.market_cap",),
            gaps=[_gap(
                "conflicting_alternative_calculations",
                f"{label} methods differ by more than 0.5%; no branch was selected.",
                input_keys=(per_share_key, total_key or ""), refs=refs,
            )],
        )
    if candidates:
        chosen = candidates[0]
        warnings = []
        if len(candidates) == 2:
            warnings.append(_warning(
                "alternative_method_tieout",
                f"Both {label} methods agree within the explicit 0.5% rounding tolerance.",
                refs=tuple(metric.ref for candidate in candidates for metric in candidate[2]),
            ))
        return _node(
            calc_id, chosen[0], status="complete", result=chosen[1], unit="x",
            inputs=chosen[2], depends_on=(("valuation.market_cap",) if chosen[0].startswith("market_cap") else ()),
            period=chosen[3], warnings=warnings,
        )

    # Do not make a valid branch fail merely because an absent alternative was
    # optional.  At this point neither branch is usable, so retain every reason.
    gaps = []
    if per_share_key in metrics or per_share_key in invalid:
        gaps.extend(per_share_issues)
    if total_key and (total_key in metrics or total_key in invalid):
        gaps.extend(total_issues)
    if not gaps:
        gaps.extend(per_share_issues)
        if total_key:
            gaps.extend(total_issues)
    inputs = [item for item in (price, per_share, total) if item is not None]
    return _node(
        calc_id,
        f"{price_key} / {per_share_key}" + (f" OR market_cap / {total_key}" if total_key else ""),
        status="invalid" if inputs else "incomplete", result=None, unit="x", inputs=inputs,
        depends_on=(("valuation.market_cap",) if total_key else ()), gaps=gaps,
    )


def _pb_node(market_cap: Mapping[str, Any], metrics: Mapping[str, _Metric],
             invalid: Mapping[str, list[dict[str, Any]]], *, supplied: bool) -> dict[str, Any]:
    if not supplied:
        return _node(
            "valuation.pb", "current_price / book_value_per_share OR market_cap / book_value",
            status="not_available", result=None, unit="x",
            gaps=[_gap("optional_input_missing", "PB inputs were not supplied.",
                       input_keys=("book_value_per_share", "book_value"))],
        )
    node = _multiple_node(
        calc_id="valuation.pb", label="PB", price_key="current_price",
        per_share_key="book_value_per_share", total_key="book_value",
        market_cap=market_cap, metrics=metrics, invalid=invalid, required_period="balance",
    )
    # PB total method must tie the book-value and share-count periods.
    if node["status"] == "complete" and node["formula"].startswith("market_cap"):
        book = metrics.get("book_value")
        shares = metrics.get("shares_outstanding")
        if book is not None and shares is not None and not _same_balance_period((book, shares)):
            node = dict(node)
            node["status"] = "invalid"
            node["result"] = None
            node["gaps"] = [*node["gaps"], _gap(
                "mixed_period", "book_value and shares_outstanding must share a period for PB.",
                input_keys=(book.key, shares.key), refs=(book.ref, shares.ref),
            )]
    return node


def _history_node(raw: Any, nodes: Mapping[str, Mapping[str, Any]], *,
                  valuation_as_of: date | None,
                  calc_id: str = "valuation.historical_percentile") -> dict[str, Any]:
    formula = "100 * count(history_value <= current_multiple) / count(history_values)"
    if raw is None:
        return _node(calc_id, formula, status="not_available", result=None, unit="%",
                     gaps=[_gap("optional_input_missing", "Raw valuation history was not supplied.",
                                input_keys=("valuation_history",))])
    if not isinstance(raw, Mapping):
        return _node(calc_id, formula, status="invalid", result=None, unit="%",
                     gaps=[_gap("invalid_history_shape", "valuation_history must be an object.",
                                input_keys=("valuation_history",))])
    metric_name = _text(raw.get("metric"), 40).lower()
    if metric_name not in {"ttm_pe", "pb"} or (
        calc_id == "valuation.pb_historical_percentile" and metric_name != "pb"
    ):
        return _node(calc_id, formula, status="invalid", result=None, unit="%",
                     gaps=[_gap("unsupported_history_metric", "History metric must be ttm_pe or pb.",
                                input_keys=("valuation_history.metric",))])
    dependency_id = "valuation.ttm_pe" if metric_name == "ttm_pe" else "valuation.pb"
    dependency = nodes[dependency_id]
    gaps: list[dict[str, Any]] = []
    if dependency["status"] != "complete":
        gaps.append(_gap("dependency_unavailable", f"{dependency_id} is not complete.",
                         input_keys=(dependency_id,)))
    raw_points = raw.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        gaps.append(_gap("empty_history_series", "valuation_history.points must contain raw observations.",
                         input_keys=("valuation_history.points",)))
        return _node(calc_id, formula, status="incomplete", result=None, unit="%",
                     depends_on=(dependency_id,), gaps=gaps)
    points: list[_Metric] = []
    for index, item in enumerate(raw_points[:5000]):
        point, point_gaps = _parse_metric(
            item, key=f"valuation_history.points[{index}]", kind="multiple",
            valuation_as_of=valuation_as_of,
        )
        if point is None:
            gaps.extend(point_gaps)
        else:
            points.append(point)
    unique_dates = {item.as_of for item in points}
    if len(points) < 20:
        gaps.append(_gap(
            "insufficient_history",
            "At least 20 valid raw history observations are required.",
            input_keys=("valuation_history.points",), refs=tuple(item.ref for item in points),
        ))
    if len(unique_dates) != len(points):
        gaps.append(_gap(
            "duplicate_history_dates",
            "Valuation history must contain at most one observation per as_of date.",
            input_keys=("valuation_history.points",), refs=tuple(item.ref for item in points),
        ))
    if metric_name == "ttm_pe" and any(not _TTM_PERIOD.search(item.period) for item in points):
        gaps.append(_gap("period_not_ttm", "Every ttm_pe history point must explicitly use TTM/LTM.",
                         input_keys=("valuation_history.points",), refs=tuple(item.ref for item in points)))
    if len({_period_definition(item.period) for item in points}) > 1:
        gaps.append(_gap("mixed_history_period_definition", "History points use inconsistent period definitions.",
                         input_keys=("valuation_history.points",), refs=tuple(item.ref for item in points)))
    if gaps:
        return _node(calc_id, formula, status="invalid", result=None, unit="%", inputs=points,
                     depends_on=(dependency_id,), gaps=gaps)
    current = Decimal(str(dependency["result"]))
    percentile = Decimal(100) * Decimal(sum(item.value <= current for item in points)) / Decimal(len(points))
    source_warnings = [
        _warning(str(item.get("code") or "history_source_limitation"),
                 str(item.get("message") or item.get("detail") or ""))
        for item in (raw.get("warnings") or []) if isinstance(item, Mapping)
    ]
    return _node(calc_id, formula, status="complete", result=percentile, unit="%", inputs=points,
                 depends_on=(dependency_id,), period=f"history_through@{max(item.as_of for item in points)}",
                 warnings=source_warnings)


def _scenario_nodes(raw_scenarios: Any, case: str, *, metrics: Mapping[str, _Metric],
                    invalid: Mapping[str, list[dict[str, Any]]],
                    valuation_as_of: date | None) -> tuple[dict[str, Any], dict[str, Any]]:
    price_id = f"valuation.scenario.{case}.implied_price"
    return_id = f"valuation.scenario.{case}.implied_return"
    price_formula = "target_multiple * scenario_denominator_per_share OR direct implied_price"
    return_formula = "100 * (implied_price / current_price - 1)"
    if not isinstance(raw_scenarios, Mapping) or case not in raw_scenarios:
        gap = _gap("optional_input_missing", f"{case} scenario assumptions were not supplied.",
                   input_keys=(f"scenarios.{case}",))
        return (
            _node(price_id, price_formula, status="not_available", result=None, unit=None, gaps=(gap,)),
            _node(return_id, return_formula, status="not_available", result=None, unit="%",
                  depends_on=(price_id,), gaps=(gap,)),
        )
    raw_case = raw_scenarios.get(case)
    if not isinstance(raw_case, Mapping):
        gap = _gap("invalid_scenario_shape", f"scenarios.{case} must be an object.",
                   input_keys=(f"scenarios.{case}",))
        return (
            _node(price_id, price_formula, status="invalid", result=None, unit=None, gaps=(gap,)),
            _node(return_id, return_formula, status="invalid", result=None, unit="%",
                  depends_on=(price_id,), gaps=(gap,)),
        )
    method = _text(raw_case.get("method"), 40).lower()
    inputs: list[_Metric] = []
    gaps: list[dict[str, Any]] = []
    implied: Decimal | None = None
    currency: str | None = None
    period: str | None = None
    formula = price_formula
    if method == "direct_price":
        direct, direct_gaps = _parse_metric(
            raw_case.get("implied_price"), key=f"scenarios.{case}.implied_price",
            kind="money_per_share", valuation_as_of=valuation_as_of,
        )
        gaps.extend(direct_gaps)
        if direct is not None:
            inputs.append(direct)
            if direct.value <= 0:
                gaps.append(_gap("non_positive_scenario_price", "Scenario price must be greater than zero.",
                                 input_keys=(direct.key,), refs=(direct.ref,)))
            else:
                implied, currency, period = direct.value, direct.currency, direct.period
                formula = "direct implied_price assumption"
    elif method in {"forward_pe", "ttm_pe", "pb"}:
        denominator_key = {
            "forward_pe": "forward_eps", "ttm_pe": "eps_ttm", "pb": "book_value_per_share",
        }[method]
        target, target_gaps = _parse_metric(
            raw_case.get("target_multiple"), key=f"scenarios.{case}.target_multiple",
            kind="multiple", valuation_as_of=valuation_as_of,
        )
        raw_denominator = raw_case.get(denominator_key)
        if raw_denominator is not None:
            denominator, denominator_gaps = _parse_metric(
                raw_denominator, key=f"scenarios.{case}.{denominator_key}",
                kind="money_per_share", valuation_as_of=valuation_as_of,
            )
        else:
            denominator, denominator_gaps = _metric_or_gaps(metrics, invalid, denominator_key)
        gaps.extend(target_gaps)
        gaps.extend(denominator_gaps)
        if target is not None:
            inputs.append(target)
            gaps.extend(_positive(target, target.key))
        if denominator is not None:
            inputs.append(denominator)
            gaps.extend(_positive(denominator, denominator.key))
        if target is not None and denominator is not None:
            if target.period != denominator.period:
                gaps.append(_gap(
                    "mixed_scenario_period", "Scenario target multiple and denominator must share a period.",
                    input_keys=(target.key, denominator.key), refs=(target.ref, denominator.ref),
                ))
            if not gaps:
                implied = target.value * denominator.value
                currency, period = denominator.currency, denominator.period
                formula = f"target_multiple * {denominator_key}"
    else:
        gaps.append(_gap(
            "unsupported_scenario_method",
            "Scenario method must be direct_price, forward_pe, ttm_pe or pb.",
            input_keys=(f"scenarios.{case}.method",),
        ))
    if gaps or implied is None:
        price_node = _node(price_id, formula, status="invalid", result=None, unit=currency,
                           inputs=inputs, period=period, gaps=gaps or [_gap(
                               "scenario_calculation_unavailable", "Scenario implied price could not be calculated.",
                               input_keys=(f"scenarios.{case}",),
                           )])
    else:
        price_node = _node(price_id, formula, status="complete", result=implied,
                           unit=f"{currency}/share", inputs=inputs, period=period)

    current_price, current_gaps = _metric_or_gaps(metrics, invalid, "current_price")
    return_gaps = list(current_gaps)
    if price_node["status"] != "complete":
        return_gaps.append(_gap("dependency_unavailable", f"{price_id} is not complete.",
                                input_keys=(price_id,)))
    if current_price is not None:
        return_gaps.extend(_positive(current_price, current_price.key))
        if currency is not None and current_price.currency != currency:
            return_gaps.append(_gap(
                "mixed_currency", "Scenario implied price and current price currencies differ.",
                input_keys=(price_id, current_price.key), refs=(current_price.ref,),
            ))
    if return_gaps:
        return_node = _node(return_id, return_formula, status="invalid", result=None, unit="%",
                            inputs=([current_price] if current_price else ()), depends_on=(price_id,),
                            period=period, gaps=return_gaps)
    else:
        assert current_price is not None and implied is not None
        result = Decimal(100) * (implied / current_price.value - Decimal(1))
        return_node = _node(return_id, return_formula, status="complete", result=result, unit="%",
                            inputs=(current_price,), depends_on=(price_id,), period=period)
    return price_node, return_node


def _edges(nodes: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        target = str(node["calc_id"])
        for ref in node.get("input_refs") or []:
            edge = (f"input:{ref}", target)
            if edge not in seen:
                seen.add(edge)
                edges.append({"from": edge[0], "to": edge[1]})
        for dependency in node.get("depends_on") or []:
            edge = (str(dependency), target)
            if edge not in seen:
                seen.add(edge)
                edges.append({"from": edge[0], "to": edge[1]})
    return edges


def build_valuation_dag(normalized_inputs: Mapping[str, Any] | None, *,
                        as_of: str | date | datetime | None = None,
                        include_price_scenarios: bool = True) -> dict[str, Any]:
    """Build a total, JSON-compatible calculation DAG without side effects.

    Invalid/missing input never raises and never disappears: the affected node
    carries ``result=None`` plus gaps.  The function does not mutate its input.
    """
    raw: Mapping[str, Any] = normalized_inputs if isinstance(normalized_inputs, Mapping) else {}
    global_gaps: list[dict[str, Any]] = []
    supplied_as_of = _as_date(as_of) if as_of is not None else None
    declared_as_of = _as_date(raw.get("valuation_as_of"))
    if as_of is not None and supplied_as_of is None:
        global_gaps.append(_gap("invalid_valuation_as_of", "as_of must be an ISO date/time.",
                                input_keys=("valuation_as_of",)))
    if raw.get("valuation_as_of") not in (None, "") and declared_as_of is None:
        global_gaps.append(_gap("invalid_valuation_as_of", "valuation_as_of must be an ISO date/time.",
                                input_keys=("valuation_as_of",)))
    if supplied_as_of and declared_as_of and supplied_as_of != declared_as_of:
        global_gaps.append(_gap(
            "valuation_as_of_conflict", "Function as_of and normalized valuation_as_of differ.",
            input_keys=("valuation_as_of",),
        ))
    valuation_as_of = supplied_as_of or declared_as_of
    if valuation_as_of is None:
        global_gaps.append(_gap("missing_valuation_as_of", "A valuation_as_of date is required.",
                                input_keys=("valuation_as_of",)))

    raw_facts = raw.get("facts")
    if not isinstance(raw_facts, Mapping):
        raw_facts = {}
        global_gaps.append(_gap("invalid_facts_shape", "facts must be an object.", input_keys=("facts",)))
    specs = {
        "current_price": "money_per_share",
        "shares_outstanding": "shares",
        "total_debt": "money",
        "cash_and_equivalents": "money",
        "preferred_equity": "money",
        "minority_interest": "money",
        "non_operating_investments": "money",
        "net_income_ttm": "money",
        "eps_ttm": "money_per_share",
        "forward_eps": "money_per_share",
        "book_value": "money",
        "book_value_per_share": "money_per_share",
    }
    metrics: dict[str, _Metric] = {}
    invalid: dict[str, list[dict[str, Any]]] = {}
    for key, kind in specs.items():
        if key not in raw_facts:
            continue
        metric, gaps = _parse_metric(raw_facts.get(key), key=key, kind=kind,
                                     valuation_as_of=valuation_as_of)
        if metric is not None:
            metrics[key] = metric
        else:
            invalid[key] = gaps

    nodes: list[dict[str, Any]] = []
    market_cap = _market_cap_node(metrics, invalid)
    nodes.append(market_cap)
    nodes.append(_enterprise_value_node(market_cap, metrics, invalid))
    nodes.append(_multiple_node(
        calc_id="valuation.ttm_pe", label="TTM PE", price_key="current_price",
        per_share_key="eps_ttm", total_key="net_income_ttm", market_cap=market_cap,
        metrics=metrics, invalid=invalid, required_period="ttm",
    ))
    nodes.append(_multiple_node(
        calc_id="valuation.forward_pe", label="Forward PE", price_key="current_price",
        per_share_key="forward_eps", total_key=None, market_cap=market_cap,
        metrics=metrics, invalid=invalid, required_period="forward",
    ))
    pb_supplied = any(key in raw_facts for key in ("book_value", "book_value_per_share"))
    nodes.append(_pb_node(market_cap, metrics, invalid, supplied=pb_supplied))
    by_id: dict[str, Mapping[str, Any]] = {str(node["calc_id"]): node for node in nodes}
    history = _history_node(raw.get("valuation_history"), by_id,
                            valuation_as_of=valuation_as_of)
    nodes.append(history)
    by_id[history["calc_id"]] = history
    if raw.get("valuation_history_pb") is not None:
        nodes.append(_history_node(
            raw["valuation_history_pb"], by_id, valuation_as_of=valuation_as_of,
            calc_id="valuation.pb_historical_percentile",
        ))

    raw_scenarios = raw.get("scenarios")
    if include_price_scenarios:
        for case in SCENARIO_CASES:
            price_node, return_node = _scenario_nodes(
                raw_scenarios, case, metrics=metrics, invalid=invalid,
                valuation_as_of=valuation_as_of,
            )
            nodes.extend((price_node, return_node))

        complete_scenario_prices = {
            case: next(
                node for node in nodes
                if node["calc_id"] == f"valuation.scenario.{case}.implied_price"
            )
            for case in SCENARIO_CASES
        }
        if all(node["status"] == "complete" for node in complete_scenario_prices.values()):
            bull = Decimal(str(complete_scenario_prices["bull"]["result"]))
            base = Decimal(str(complete_scenario_prices["base"]["result"]))
            bear = Decimal(str(complete_scenario_prices["bear"]["result"]))
            if not (bull >= base >= bear):
                warning = _warning(
                    "scenario_order_inconsistent",
                    "Scenario labels are not ordered bull >= base >= bear; math is retained but assumptions need review.",
                )
                for node in nodes:
                    if node["calc_id"].startswith("valuation.scenario."):
                        node["warnings"].append(warning)

    node_by_id = {node["calc_id"]: node for node in nodes}
    required_statuses = [node_by_id[item]["status"] for item in CORE_CALC_IDS]
    supplied_optional_ids: list[str] = []
    if pb_supplied:
        supplied_optional_ids.append("valuation.pb")
    if raw.get("valuation_history") is not None:
        supplied_optional_ids.append("valuation.historical_percentile")
    if raw.get("valuation_history_pb") is not None:
        supplied_optional_ids.append("valuation.pb_historical_percentile")
    if include_price_scenarios and isinstance(raw_scenarios, Mapping):
        for case in SCENARIO_CASES:
            if case in raw_scenarios:
                supplied_optional_ids.extend((
                    f"valuation.scenario.{case}.implied_price",
                    f"valuation.scenario.{case}.implied_return",
                ))
    considered = [*required_statuses, *(node_by_id[item]["status"] for item in supplied_optional_ids)]
    if global_gaps or "invalid" in considered:
        status = "invalid"
    elif any(item != "complete" for item in required_statuses):
        status = "incomplete"
    elif any(item != "complete" for item in considered):
        status = "incomplete"
    else:
        status = "complete"
    all_gaps = [*global_gaps]
    warnings: list[dict[str, Any]] = []
    for node in nodes:
        all_gaps.extend(node["gaps"])
        warnings.extend(node["warnings"])
    return {
        "schema_version": SCHEMA_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "status": status,
        "valuation_as_of": valuation_as_of.isoformat() if valuation_as_of else None,
        "input_register": [metrics[key].public() for key in sorted(metrics)],
        "nodes": nodes,
        "edges": _edges(nodes),
        "gaps": all_gaps,
        "warnings": warnings,
        "guardrails": {
            "price_scenario_nodes_included": bool(include_price_scenarios),
            "vibe_company_main_chain_safe": not include_price_scenarios,
        },
        "summary": {
            "core_complete": sum(node_by_id[item]["status"] == "complete" for item in CORE_CALC_IDS),
            "core_required": len(CORE_CALC_IDS),
            "nodes_complete": sum(node["status"] == "complete" for node in nodes),
            "nodes_total": len(nodes),
        },
    }


def _metric_payload(value: Any, *, unit: str | None, currency: str | None,
                    period: Any, as_of: Any, ref: str) -> dict[str, Any] | None:
    if value is None or not unit or not period or not as_of:
        return None
    row: dict[str, Any] = {
        "value": value, "unit": unit, "period": _text(period, 120),
        "as_of": _text(as_of, 80), "ref": ref,
    }
    if currency:
        row["currency"] = currency
    return row


def _declared_currency(*values: Any) -> str | None:
    for value in values:
        if not isinstance(value, Mapping):
            continue
        currency = _text(value.get("currency"), 8).upper()
        if _ISO_CURRENCY.fullmatch(currency):
            return currency
        unit = _text(value.get("unit"), 40).replace(" ", "")
        per_share = _PER_SHARE_UNIT.fullmatch(unit)
        if per_share:
            return per_share.group(1).upper()
        if _ISO_CURRENCY.fullmatch(unit.upper()):
            return unit.upper()
    return None


def _money_unit(value: Mapping[str, Any], *, per_share: bool,
                parents: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    currency = _declared_currency(value, *parents)
    raw_unit = _text(value.get("unit"), 40)
    if per_share:
        matched = _PER_SHARE_UNIT.fullmatch(raw_unit.replace(" ", ""))
        if matched:
            return f"{matched.group(1).upper()}/share", matched.group(1).upper()
        return (f"{currency}/share", currency) if currency else (None, None)
    if _ISO_CURRENCY.fullmatch(raw_unit.replace(" ", "").upper()):
        embedded = raw_unit.replace(" ", "").upper()
        return embedded, embedded
    return (currency, currency) if currency else (None, None)


def _latest_financial_row(rows: Any, valuation_as_of: date | None) -> Mapping[str, Any] | None:
    if not isinstance(rows, list):
        return None
    candidates: list[tuple[date, Mapping[str, Any]]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        period = _as_date(row.get("report_date") or row.get("period_end") or row.get("period"))
        if period is not None and (valuation_as_of is None or period <= valuation_as_of):
            candidates.append((period, row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _f10_bvps_payload(value: Any, *, parent: Mapping[str, Any],
                      section: Mapping[str, Any], pack: Mapping[str, Any],
                      valuation_as_of: date | None, ref: str) -> dict[str, Any] | None:
    """Restore the known A-share F10 ``每股净资产(元)`` field contract.

    The local F10 parser emits ``bvps`` without unit metadata.  Only its dated,
    same-company 最新提示 receipt permits this narrow CNY/share adapter.  This
    attaches units; it does not turn a current snapshot into a historical one.
    Explicit conflicting or unrecognised metadata must never be overwritten.
    """
    for container in (parent, section, pack):
        if "usable" in container and container["usable"] is not True:
            return None
        status = _text(container.get("status"), 40).lower()
        if status and status not in {"ok", "available", "complete", "partial", "degraded"}:
            return None
    unit, currency = _money_unit(parent, per_share=True, parents=(section, pack))
    if unit:
        return _metric_payload(
            value, unit=unit, currency=currency,
            period=parent.get("report_period") or section.get("as_of"),
            as_of=section.get("as_of") or parent.get("report_period"), ref=ref,
        )
    if any(container.get(key) for container in (parent, section, pack)
           for key in ("unit", "currency", "bvps_unit")):
        return None
    code = _text(pack.get("code"), 20).upper()
    if not re.fullmatch(r"(?:SH6\d{5}|SZ[03]\d{5}|BJ[489]\d{5})", code):
        return None
    period = _as_date(parent.get("report_period"))
    if period is None or valuation_as_of is None or period > valuation_as_of:
        return None
    receipts = section.get("snapshot_receipts")
    if not isinstance(receipts, list):
        return None
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        locator = receipt.get("logical_locator")
        if not isinstance(locator, Mapping):
            continue
        snapshot_date = _as_date(receipt.get("data_asof") or receipt.get("snapshot_asof"))
        if (
            receipt.get("source") != "f10:最新提示"
            or receipt.get("status") != "available"
            or ("usable" in receipt and receipt["usable"] is not True)
            or _text(locator.get("code"), 20).upper() != code
            or locator.get("source") != "f10:最新提示"
            or not re.fullmatch(r"[0-9a-fA-F]{64}", str(receipt.get("content_sha256") or ""))
            or snapshot_date is None or not period <= snapshot_date <= valuation_as_of
        ):
            continue
        payload = _metric_payload(
            value, unit="CNY/share", currency="CNY", period=period.isoformat(),
            as_of=snapshot_date.isoformat(), ref=ref,
        )
        if payload:
            payload["unit_source"] = "tdx_f10_a_share.latest_indicators.每股净资产(元)"
        return payload
    return None


def _select_forward_eps(items: Any, *, section: Mapping[str, Any],
                        valuation_as_of: date | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(items, list):
        return None, []
    candidates: list[tuple[str, Mapping[str, Any], str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value_field = "eps" if item.get("eps") is not None else "eps_mean"
        if item.get(value_field) is None:
            continue
        period = _text(item.get("period"), 40)
        if not period and str(item.get("year") or "").isdigit():
            period = f"FY{item['year']}E"
        if not period or not _FORWARD_PERIOD.search(period):
            continue
        match = re.search(r"(20\d{2})", period)
        if valuation_as_of and match and int(match.group(1)) < valuation_as_of.year:
            continue
        candidates.append((period, item, value_field))
    if not candidates:
        return None, []
    first_period = min(period for period, _item, _field in candidates)
    selected = [
        (item, field) for period, item, field in candidates if period == first_period
    ]
    values = {_decimal(item.get(field)) for item, field in selected}
    if len(values) != 1 or None in values:
        return None, [_gap(
            "conflicting_forward_eps", f"Multiple {first_period} EPS values conflict; no average was inferred.",
            input_keys=("forward_eps",),
        )]
    item, value_field = selected[0]
    unit, currency = _money_unit(item, per_share=True, parents=(section,))
    payload = _metric_payload(
        item.get(value_field), unit=unit, currency=currency, period=first_period,
        as_of=item.get("as_of") or section.get("as_of") or section.get("retrieved_at"),
        ref=f"pack.sections.expectations.items[{items.index(item)}].{value_field}",
    )
    return payload, []


def extract_valuation_inputs(evidence_pack: Mapping[str, Any] | None, *,
                             as_of: str | date | datetime | None = None) -> dict[str, Any]:
    """Conservatively adapt known evidence-pack fields to normalized inputs.

    Only semantically named raw fields are copied.  ``pe_ttm``/``pb`` provider
    outputs are never reversed into shares, earnings, book value, debt or cash.
    A summarized five-year percentile is not treated as a raw history series.
    """
    pack: Mapping[str, Any] = evidence_pack if isinstance(evidence_pack, Mapping) else {}
    sections = pack.get("sections") if isinstance(pack.get("sections"), Mapping) else {}
    valuation_as_of = _as_date(as_of) if as_of is not None else _as_date(pack.get("generated_at"))
    gaps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    if not sections:
        gaps.append(_gap("missing_sections", "Evidence pack has no sections object.",
                         input_keys=("sections",)))

    quote = sections.get("quote_live") if isinstance(sections.get("quote_live"), Mapping) else {}
    if quote.get("price") is not None:
        unit, currency = _money_unit(quote, per_share=True, parents=(pack,))
        payload = _metric_payload(
            quote.get("price"), unit=unit, currency=currency, period="spot",
            as_of=quote.get("as_of") or quote.get("pulled_at"),
            ref="pack.sections.quote_live.price",
        )
        if payload is not None:
            facts["current_price"] = payload
        else:
            gaps.append(_gap(
                "quote_price_metadata_missing",
                "quote_live.price lacks explicit currency/unit or as_of metadata.",
                input_keys=("current_price",), refs=("pack.sections.quote_live.price",),
            ))

    company = sections.get("company_facts") if isinstance(sections.get("company_facts"), Mapping) else {}
    company_val = company.get("valuation") if isinstance(company.get("valuation"), Mapping) else {}
    valuation = sections.get("valuation") if isinstance(sections.get("valuation"), Mapping) else {}
    f10 = valuation.get("f10") if isinstance(valuation.get("f10"), Mapping) else {}

    # Prefer the latest explicitly dated balance-sheet share count.  F10 can
    # legitimately lag the current financial statement by one reporting
    # period; choosing it first creates a false mixed-period failure for TTM PE
    # and PB even when the newer statement supplies the exact same semantic
    # field.  Older F10 shares remain a conservative fallback only.
    financials = sections.get("financials") if isinstance(sections.get("financials"), Mapping) else {}
    financial_row = _latest_financial_row(financials.get("rows"), valuation_as_of)
    if financial_row is not None and financial_row.get("shares_outstanding") is not None:
        period = financial_row.get("report_date") or financial_row.get("period_end") or financial_row.get("period")
        observed = financial_row.get("as_of") or financials.get("as_of") or period
        payload = _metric_payload(
            financial_row.get("shares_outstanding"), unit="shares", currency=None,
            period=period, as_of=observed,
            ref=(
                f"pack.sections.financials.rows["
                f"{financials.get('rows', []).index(financial_row)}].shares_outstanding"
            ),
        )
        if payload:
            facts["shares_outstanding"] = payload

    share_candidates: list[tuple[Any, Mapping[str, Any], str]] = []
    for value, parent, ref in (
        (company_val.get("total_shares"), company_val, "pack.sections.company_facts.valuation.total_shares"),
        (f10.get("total_shares"), f10, "pack.sections.valuation.f10.total_shares"),
    ):
        if value is not None:
            share_candidates.append((value, parent, ref))
    if share_candidates and "shares_outstanding" not in facts:
        identities = {(
            _decimal(value), _text(parent.get("report_period") or company.get("as_of") or valuation.get("as_of"), 80),
        ) for value, parent, _ref in share_candidates}
        if len(identities) == 1:
            value, parent, ref = share_candidates[0]
            period = parent.get("report_period") or company.get("as_of") or valuation.get("as_of")
            payload = _metric_payload(
                value, unit="shares", currency=None, period=period,
                as_of=company.get("as_of") or valuation.get("as_of") or period, ref=ref,
            )
            if payload:
                facts["shares_outstanding"] = payload
        else:
            gaps.append(_gap(
                "conflicting_share_count", "Known share-count sources disagree; no source was selected.",
                input_keys=("shares_outstanding",), refs=tuple(item[2] for item in share_candidates),
            ))

    if financial_row is not None:
        period = financial_row.get("report_date") or financial_row.get("period_end") or financial_row.get("period")
        observed = financial_row.get("as_of") or financials.get("as_of") or period
        if "shares_outstanding" not in facts and financial_row.get("shares_outstanding") is not None:
            payload = _metric_payload(
                financial_row.get("shares_outstanding"), unit="shares", currency=None,
                period=period, as_of=observed,
                ref=(
                    f"pack.sections.financials.rows["
                    f"{financials.get('rows', []).index(financial_row)}].shares_outstanding"
                ),
            )
            if payload:
                facts["shares_outstanding"] = payload
        for key, per_share, period_override in (
            ("total_debt", False, period),
            ("cash_and_equivalents", False, period),
            ("book_value", False, period),
            ("book_value_per_share", True, period),
            ("net_income_ttm", False, f"TTM@{period}" if period else None),
            ("eps_ttm", True, f"TTM@{period}" if period else None),
        ):
            if financial_row.get(key) is None:
                continue
            unit, currency = _money_unit(financial_row, per_share=per_share, parents=(financials, pack))
            payload = _metric_payload(
                financial_row.get(key), unit=unit, currency=currency,
                period=period_override, as_of=observed,
                ref=f"pack.sections.financials.rows[{financials.get('rows', []).index(financial_row)}].{key}",
            )
            if payload:
                facts[key] = payload
            else:
                gaps.append(_gap(
                    "financial_metric_metadata_missing",
                    f"{key} exists but lacks explicit currency/unit/period/as_of metadata.",
                    input_keys=(key,),
                ))

    financial_contract = (
        sections.get("company_financials_contract")
        if isinstance(sections.get("company_financials_contract"), Mapping) else {}
    )
    contract_metrics = (
        financial_contract.get("metrics")
        if isinstance(financial_contract.get("metrics"), Mapping) else {}
    )
    ttm_parent = (
        contract_metrics.get("ttm_net_profit_parent")
        if isinstance(contract_metrics.get("ttm_net_profit_parent"), Mapping) else {}
    )
    if "net_income_ttm" not in facts and ttm_parent.get("value") is not None:
        payload = _metric_payload(
            ttm_parent.get("value"), unit="CNY", currency="CNY",
            period=ttm_parent.get("period"), as_of=financials.get("as_of") or ttm_parent.get("period"),
            ref=str(ttm_parent.get("calculation_id") or "company_financials_contract.ttm_net_profit_parent"),
        )
        if payload:
            facts["net_income_ttm"] = payload

    # Resolve duplicate F10 BVPS values before selecting a source.  Their
    # typed field contract can supply a missing unit, but never a missing value.
    bvps_candidates: list[dict[str, Any]] = []
    for source, parent, section, ref in (
        (company_val.get("bvps"), company_val, company, "pack.sections.company_facts.valuation.bvps"),
        (f10.get("bvps"), f10, valuation, "pack.sections.valuation.f10.bvps"),
    ):
        if source is None or "book_value_per_share" in facts:
            continue
        payload = _f10_bvps_payload(
            source, parent=parent, section=section, pack=pack,
            valuation_as_of=valuation_as_of, ref=ref,
        )
        if payload:
            bvps_candidates.append(payload)
    if bvps_candidates:
        newest_period = max(str(item["period"]) for item in bvps_candidates)
        latest_bvps = [item for item in bvps_candidates if item["period"] == newest_period]
        identities = {(_decimal(item["value"]), item["unit"]) for item in latest_bvps}
        if len(identities) == 1 and all(identity[0] is not None for identity in identities):
            facts["book_value_per_share"] = latest_bvps[0]
            if latest_bvps[0].get("unit_source"):
                warnings.append(_warning(
                    "typed_f10_bvps_unit_restored",
                    "The dated A-share F10 每股净资产(元) contract supplies CNY/share; historical availability remains subject to its snapshot receipt.",
                    refs=(latest_bvps[0]["ref"],),
                ))
        else:
            gaps.append(_gap(
                "conflicting_book_value_per_share",
                "F10 BVPS sources disagree for the same report period; no source was selected.",
                input_keys=("book_value_per_share",),
                refs=tuple(item["ref"] for item in latest_bvps),
            ))

    expectations = sections.get("expectations") if isinstance(sections.get("expectations"), Mapping) else {}
    forward, forward_gaps = _select_forward_eps(
        expectations.get("items"), section=expectations, valuation_as_of=valuation_as_of,
    )
    gaps.extend(forward_gaps)
    if forward:
        facts["forward_eps"] = forward

    history = None
    history_key = "history_series" if isinstance(valuation.get("history_series"), Mapping) \
        else "history_5y_series"
    raw_history = valuation.get(history_key)
    if isinstance(raw_history, Mapping) and isinstance(raw_history.get("points"), list):
        history = copy.deepcopy(dict(raw_history))
        for warning in raw_history.get("warnings") or []:
            if isinstance(warning, Mapping):
                warnings.append(_warning(
                    str(warning.get("code") or "history_source_limitation"),
                    str(warning.get("message") or warning.get("detail") or ""),
                    refs=(f"pack.sections.valuation.{history_key}",),
                ))
        for index, point in enumerate(history["points"]):
            if not isinstance(point, dict):
                continue
            point["provider_ref"] = point.get("ref") or point.get("source_ref")
            point["ref"] = f"pack.sections.valuation.{history_key}.points[{index}]"
    elif isinstance(valuation.get("history_5y"), Mapping):
        warnings.append(_warning(
            "summary_percentile_not_raw_series",
            "valuation.history_5y is a provider summary, not raw observations; it was not used as a deterministic percentile input.",
            refs=("pack.sections.valuation.history_5y",),
        ))

    scenarios = valuation.get("scenarios") if isinstance(valuation.get("scenarios"), Mapping) else None
    for source in (company_val, f10, valuation):
        if source.get("pe_ttm") is not None or source.get("pb") is not None:
            warnings.append(_warning(
                "provider_multiple_not_reversed",
                "Existing provider PE/PB values are cross-check evidence only; no missing raw input was reverse-engineered.",
            ))
            break

    normalized: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "valuation_as_of": valuation_as_of.isoformat() if valuation_as_of else None,
        "facts": facts,
    }
    if history is not None:
        normalized["valuation_history"] = history
    raw_pb_history = valuation.get("history_pb_series")
    if isinstance(raw_pb_history, Mapping) and isinstance(raw_pb_history.get("points"), list):
        pb_history = copy.deepcopy(dict(raw_pb_history))
        for index, point in enumerate(pb_history["points"]):
            if isinstance(point, dict):
                point["provider_ref"] = point.get("ref") or point.get("source_ref")
                point["ref"] = f"pack.sections.valuation.history_pb_series.points[{index}]"
        normalized["valuation_history_pb"] = pb_history
    if scenarios is not None:
        normalized["scenarios"] = copy.deepcopy(dict(scenarios))
    missing_core = [
        key for key in (
            "current_price", "shares_outstanding", "total_debt",
            "cash_and_equivalents", "forward_eps",
        ) if key not in facts
    ]
    if "eps_ttm" not in facts and "net_income_ttm" not in facts:
        missing_core.append("eps_ttm_or_net_income_ttm")
    for key in missing_core:
        gaps.append(_gap("normalized_input_unavailable", f"Extractor could not prove {key}.",
                         input_keys=(key,)))
    status = "complete" if not gaps else ("partial" if facts else "incomplete")
    return {
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "status": status,
        "inputs": normalized,
        "gaps": gaps,
        "warnings": warnings,
    }


def build_valuation_dag_from_evidence(evidence_pack: Mapping[str, Any] | None, *,
                                      as_of: str | date | datetime | None = None,
                                      include_price_scenarios: bool = True) -> dict[str, Any]:
    """Extract conservative inputs and build the corresponding calculation DAG."""
    extraction = extract_valuation_inputs(evidence_pack, as_of=as_of)
    dag = build_valuation_dag(
        extraction["inputs"], as_of=as_of,
        include_price_scenarios=include_price_scenarios,
    )
    return {
        **dag,
        "extraction": {
            "schema_version": extraction["schema_version"],
            "status": extraction["status"],
            "gaps": extraction["gaps"],
            "warnings": extraction["warnings"],
        },
    }


__all__ = [
    "CORE_CALC_IDS",
    "EXTRACTION_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "NORMALIZED_INPUT_CONTRACT",
    "SCENARIO_CASES",
    "SCHEMA_VERSION",
    "build_valuation_dag",
    "build_valuation_dag_from_evidence",
    "extract_valuation_inputs",
]
