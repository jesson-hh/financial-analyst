"""Deterministic risk/catalyst gate for the company-research workflow.

The gate is deliberately a pure transformation: it does not fetch data, read a
clock, call a model, or write workspace state.  It accepts the existing company
Evidence Hub/evidence-pack shapes plus explicitly structured issuer/context
records and returns a replayable risk contract.

Only structured fields are interpreted.  Legacy narrative text is retained as
an availability gap, never promoted into a risk fact, counter-example, catalyst,
or decision point.  In particular, industry/macro/context evidence may enrich
the output but cannot close an issuer-evidence gate.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "company-risk-contract@1"

_GOOD_SOURCE_STATUSES = frozenset({
    "ok", "partial", "saved", "complete", "fresh", "live", "resolved",
})
_BAD_SOURCE_STATUSES = frozenset({
    "empty", "error", "failed", "invalid", "unavailable", "timeout", "corrupt",
})
_COUNTER_SIGNALS = frozenset({
    "counter", "contradicting", "contradiction", "disconfirming",
    "invalidating", "opposes", "opposing", "refuting", "weakening",
})
_SAME_DIRECTION_SIGNALS = frozenset({
    "confirming", "supports", "supporting", "same_direction", "reinforcing",
})
_DECISION_STATUSES = frozenset({
    "open", "scheduled", "pending", "monitoring", "triggered", "resolved",
    "closed", "overdue",
})
_OPEN_DECISION_STATUSES = frozenset({"open", "scheduled", "pending", "monitoring"})
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
_PRIORITY_ALIASES = {
    "critical": "critical", "required": "high", "material": "high",
    "high": "high", "medium": "medium", "moderate": "medium",
    "low": "low", "optional": "low",
}
_CATALYST_TYPES = {
    "realization": "realization", "delivery": "realization", "兑现型": "realization",
    "expectation": "expectation", "预期型": "expectation",
    "cycle": "cycle", "cyclical": "cycle", "周期型": "cycle",
    "flow_sentiment": "flow_sentiment", "flow": "flow_sentiment",
    "sentiment": "flow_sentiment", "资金/情绪型": "flow_sentiment",
    "资金 / 情绪型": "flow_sentiment",
}
_ISSUER_SECTION_NAMES = frozenset({
    "quote_live", "company_facts", "financials", "announcements",
    "expectations", "valuation", "research_views", "kuaixun",
})
_STRUCTURED_MARKERS = frozenset({
    "kind", "role", "record_type", "claim_id", "strong_claim", "strength",
    "relative_direction", "relation", "signal", "risk_type", "catalyst_type",
    "decision_point", "question", "metric", "event", "threshold", "resolution_condition",
    "deadline", "source_refs", "evidence_refs", "function", "check_name",
})
_CONTAINER_KINDS = {
    "strong_claims": "strong_claim",
    "claims": "claim",
    "counter_evidence": "counter_evidence",
    "counterexamples": "counter_evidence",
    "risks": "risk",
    "risk_items": "risk",
    "decision_points": "decision_point",
    "verdict_points": "decision_point",
    "catalysts": "catalyst",
    "checks": "check",
}
_CHECK_ALIASES = {
    "forward_vs_ttm": "forward_vs_ttm_judgement",
    "forward_vs_ttm_judgement": "forward_vs_ttm_judgement",
    "consensus_dispersion": "consensus_dispersion",
}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=_canonical_json)
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _text(value, 100).lower()).strip("_")


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value, 80)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    if parsed.hour == parsed.minute == parsed.second == parsed.microsecond == 0:
        return parsed.date().isoformat()
    return parsed.isoformat().replace("+00:00", "Z")


def _normalise_scope(value: Any, fallback: str = "unknown") -> str:
    raw = _text(value, 40).lower()
    if raw in {"主体", "公司", "证券", "标的"}:
        return "issuer"
    if raw in {"背景", "行业", "宏观", "市场", "外围"}:
        return "context"
    scope = _slug(value)
    if scope in {"issuer", "company", "security"}:
        return "issuer"
    if scope in {"context", "industry", "macro", "market", "peripheral"}:
        return "context"
    return fallback


def _normalise_stale(value: Mapping[str, Any]) -> bool | None:
    if isinstance(value.get("stale"), bool):
        return bool(value["stale"])
    freshness = value.get("freshness")
    if isinstance(freshness, Mapping):
        if isinstance(freshness.get("stale"), bool):
            return bool(freshness["stale"])
        state = _slug(freshness.get("status") or freshness.get("state"))
        if state in {"stale", "expired"}:
            return True
        if state in {"fresh", "ok", "live"}:
            return False
    state = _slug(value.get("status"))
    if state in {"stale", "expired"}:
        return True
    return None


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (bytes, bytearray)):
        return bool(value)
    return True


def _row_id(row: Mapping[str, Any]) -> str:
    for key in ("evidence_id", "event_id", "source_id", "source_ref", "id"):
        value = _text(row.get(key), 180)
        if value:
            return value
    return ""


def _refs(value: Mapping[str, Any], *, include_self: bool = True) -> list[str]:
    rows: list[Any] = []
    for key in ("source_refs", "evidence_refs", "refs", "inputs_refs"):
        raw = value.get(key)
        if isinstance(raw, (str, bytes, Mapping)):
            rows.append(raw)
        elif isinstance(raw, Sequence):
            rows.extend(raw)
    out: list[str] = []
    for item in rows:
        if isinstance(item, Mapping):
            ref = _text(
                item.get("ref_id") or item.get("evidence_id") or item.get("source_id")
                or item.get("event_id") or item.get("id"), 180,
            )
        else:
            ref = _text(item, 180)
        if ref:
            out.append(ref)
    if include_self:
        # ``id`` on a structured row is a claim/point identity, not provenance.
        # Trusted standalone source inputs still get their id through the
        # collector's _default_ref; model sidecar ids must not mint sources.
        own = next((
            _text(value.get(key), 180)
            for key in ("evidence_id", "event_id", "source_id", "source_ref")
            if _text(value.get(key), 180)
        ), "")
        if own:
            out.append(own)
        default = _text(value.get("_default_ref"), 180)
        if default:
            out.append(default)
    return sorted(set(out))


def _gap(
    code: str, detail: Any, *, severity: str = "critical",
    field: str = "", source_refs: Iterable[str] = (), availability: bool = False,
) -> dict[str, Any]:
    body = {
        "code": _slug(code) or "risk_contract_gap",
        "severity": "critical" if severity == "critical" else "optional",
        "field": _text(field, 120) or None,
        "detail": _text(detail, 600),
        "source_refs": sorted({_text(item, 180) for item in source_refs if _text(item, 180)}),
        "availability": bool(availability),
    }
    return {"gap_id": "RG-" + _digest(body)[:14].upper(), **body}


def _dedupe_by_key(rows: Iterable[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    """Choose a stable canonical representative for each explicit identity."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        row = dict(raw)
        identity = _text(row.get(key), 180) or _digest(row)
        grouped.setdefault(identity, []).append(row)
    return [
        min(grouped[identity], key=_canonical_json)
        for identity in sorted(grouped)
    ]


def _as_rows(value: Any, *, label: str, gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if isinstance(item, Mapping):
                rows.append(dict(item))
            else:
                gaps.append(_gap(
                    "invalid_structured_row",
                    f"{label}[{index}] is not an object and was ignored.",
                    field=label, availability=True,
                ))
        return rows
    gaps.append(_gap(
        "invalid_structured_input", f"{label} must be an object or list of objects.",
        field=label, availability=True,
    ))
    return []


def _is_structured_record(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in _STRUCTURED_MARKERS)


def _collect_payload_records(
    value: Any, *, default_kind: str = "", default_scope: str = "unknown",
    default_ref: str = "", default_source_id: str = "", default_stale: bool | None = None,
    depth: int = 0,
) -> list[dict[str, Any]]:
    """Collect only explicitly structured contract rows from known containers."""
    if depth > 5:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: list[dict[str, Any]] = []
        for item in value:
            out.extend(_collect_payload_records(
                item, default_kind=default_kind, default_scope=default_scope,
                default_ref=default_ref, default_source_id=default_source_id,
                default_stale=default_stale, depth=depth + 1,
            ))
        return out
    if not isinstance(value, Mapping):
        return []

    out: list[dict[str, Any]] = []
    if _is_structured_record(value):
        row = dict(value)
        if default_kind and not (row.get("kind") or row.get("role") or row.get("record_type")):
            row["kind"] = default_kind
        row.setdefault("_scope", default_scope)
        if default_ref:
            row.setdefault("_default_ref", default_ref)
        if default_source_id:
            row.setdefault("_source_id", default_source_id)
        if default_stale is not None and not isinstance(row.get("stale"), bool):
            row["_stale"] = default_stale
        out.append(row)

    risk_contract = value.get("risk_contract")
    if isinstance(risk_contract, (Mapping, list, tuple)):
        out.extend(_collect_payload_records(
            risk_contract, default_scope=default_scope, default_ref=default_ref,
            default_source_id=default_source_id, default_stale=default_stale,
            depth=depth + 1,
        ))
    for key, kind in _CONTAINER_KINDS.items():
        child = value.get(key)
        if isinstance(child, (Mapping, list, tuple)):
            out.extend(_collect_payload_records(
                child, default_kind=kind, default_scope=default_scope,
                default_ref=default_ref, default_source_id=default_source_id,
                default_stale=default_stale, depth=depth + 1,
            ))
    # Generic rows/items are accepted only when each child identifies its own
    # structured role.  Narrative summaries are intentionally not traversed.
    for key in ("rows", "items", "evidence"):
        child = value.get(key)
        if isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
            for item in child:
                if isinstance(item, Mapping) and _is_structured_record(item):
                    out.extend(_collect_payload_records(
                        item, default_scope=default_scope, default_ref=default_ref,
                        default_source_id=default_source_id, default_stale=default_stale,
                        depth=depth + 1,
                    ))
    return out


def _source_metadata(
    row: Mapping[str, Any], *, fallback_scope: str = "unknown",
) -> dict[str, Any]:
    status = _slug(row.get("status")) or "unknown"
    identity = _row_id(row)
    record_count = row.get("record_count")
    explicitly_empty = isinstance(record_count, int) and not isinstance(record_count, bool) \
        and record_count == 0
    return {
        "identity": identity,
        "evidence_id": _text(row.get("evidence_id"), 180) or None,
        "source_id": _text(row.get("source_id") or row.get("_source_id"), 180) or None,
        "source_ref": _text(row.get("source_ref") or row.get("_default_ref"), 180) or None,
        "scope": _normalise_scope(row.get("scope") or row.get("type") or row.get("_scope"), fallback_scope),
        "stale": _normalise_stale(row) if "_stale" not in row else bool(row.get("_stale")),
        "status": status,
        "usable": bool(
            identity
            and status in _GOOD_SOURCE_STATUSES
            and row.get("usable") is not False
            and not explicitly_empty
        ),
    }


def _alias_index(sources: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for source in sources:
        body = dict(source)
        token = _canonical_json(body)
        if token in seen:
            continue
        seen.add(token)
        aliases = {
            _text(body.get("identity"), 180), _text(body.get("evidence_id"), 180),
            _text(body.get("source_id"), 180), _text(body.get("source_ref"), 180),
        }
        source_ref = _text(body.get("source_ref"), 180)
        if source_ref.startswith("section:"):
            aliases.add("pack." + source_ref.split(":", 1)[1])
        source_id = _text(body.get("source_id"), 180)
        if "#" in source_id:
            aliases.add(source_id.split("#", 1)[0])
        for alias in aliases:
            if alias:
                out.setdefault(alias, []).append(body)
    for rows in out.values():
        rows.sort(key=lambda row: (_text(row.get("scope")), _text(row.get("identity"))))
    return out


def _resolve_refs(refs: Iterable[str], aliases: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        for item in aliases.get(ref, ()):
            key = _canonical_json(item)
            if key not in seen:
                seen.add(key)
                rows.append(dict(item))
    return rows


def _reference_diagnostics(
    refs: Iterable[str], aliases: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Expose routing failures without repairing, guessing or promoting refs."""
    out: list[dict[str, Any]] = []
    for ref in sorted(set(refs)):
        resolved = aliases.get(ref, ())
        eligible = [row for row in resolved if row.get("usable")]
        if any(row.get("scope") == "issuer" for row in eligible):
            state = "issuer"
        elif any(row.get("scope") == "context" for row in eligible):
            state = "context_only"
        elif resolved:
            state = "unavailable"
        else:
            state = "unknown_reference"
        out.append({
            "source_ref": ref,
            "resolution": state,
            "issuer_eligible": state == "issuer",
            "source_ids": sorted({
                str(row["source_id"]) for row in resolved if row.get("source_id")
            }),
            "source_statuses": sorted({
                str(row["status"]) for row in resolved if row.get("status")
            }),
        })
    return out


def _freshness(resolved: Iterable[Mapping[str, Any]], *, issuer_only: bool = False) -> str:
    rows = [row for row in resolved if not issuer_only or row.get("scope") == "issuer"]
    if not rows:
        return "unknown"
    fresh = [row for row in rows if row.get("usable") and row.get("stale") is False]
    if fresh:
        return "fresh"
    stale = [row for row in rows if row.get("usable") and row.get("stale") is True]
    if stale:
        return "stale"
    return "unknown"


def _record_kind(row: Mapping[str, Any]) -> str:
    raw = _text(row.get("kind") or row.get("role") or row.get("record_type"), 80).lower()
    aliases = {
        "强结论": "strong_claim", "结论": "claim", "反证": "counter_evidence",
        "风险": "risk", "裁决点": "decision_point", "催化剂": "catalyst",
    }
    return aliases.get(raw) or _slug(raw)


def _claim_id(row: Mapping[str, Any]) -> str:
    explicit = _text(row.get("claim_id") or row.get("id"), 160)
    if explicit:
        return explicit
    body = {
        "claim": _text(row.get("claim") or row.get("statement") or row.get("conclusion")),
        "refs": _refs(row),
    }
    return "RC-" + _digest(body)[:14].upper()


def _is_strong_claim(row: Mapping[str, Any]) -> bool:
    kind = _record_kind(row)
    strength_raw = _text(row.get("strength") or row.get("confidence"), 40).lower()
    strength = "strong" if strength_raw in {"强", "高", "绝对"} else _slug(strength_raw)
    return bool(
        row.get("strong_claim") is True
        or kind == "strong_claim"
        or (kind in {"claim", "conclusion"} and strength in {"strong", "high", "absolute"})
    )


def _counter_relation(row: Mapping[str, Any]) -> str:
    raw = _text(
        row.get("relative_direction") or row.get("relation")
        or row.get("signal") or row.get("stance"), 80,
    ).lower()
    aliases = {
        "反证": "disconfirming", "削弱": "weakening", "推翻": "invalidating",
        "反驳": "refuting", "相反": "opposing", "同向": "same_direction",
        "支持": "confirming", "强化": "reinforcing",
    }
    return aliases.get(raw) or _slug(raw)


def _counter_claim_ids(row: Mapping[str, Any]) -> set[str]:
    raw = row.get("opposes_claim_ids") or row.get("claim_ids")
    values: list[Any]
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = list(raw)
    elif raw not in (None, ""):
        values = [raw]
    else:
        values = [row.get("opposes_claim_id") or row.get("claim_id")]
    return {_text(value, 160) for value in values if _text(value, 160)}


def _counter_statement(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("counter_evidence") or row.get("fact") or row.get("statement")
        or row.get("evidence") or row.get("title"),
    )


def _normalise_priority(row: Mapping[str, Any], fallback: str = "unknown") -> str:
    raw_text = _text(
        row.get("priority") or row.get("severity") or row.get("impact_severity"), 40,
    ).lower()
    cn = {"关键": "critical", "高": "high", "中": "medium", "低": "low"}
    raw = cn.get(raw_text) or _slug(raw_text)
    return _PRIORITY_ALIASES.get(raw, fallback)


def _normalise_conflict(row: Mapping[str, Any]) -> dict[str, Any]:
    refs = row.get("source_ids") or row.get("source_refs") or row.get("evidence_refs") or []
    if isinstance(refs, (str, bytes)):
        refs = [refs]
    if not isinstance(refs, Sequence):
        refs = []
    source_refs = sorted({_text(item, 180) for item in refs if _text(item, 180)})
    body = {
        "kind": _slug(row.get("kind")) or "data_source_conflict",
        "severity": _text(row.get("severity"), 40).lower() or "required",
        "status": _slug(row.get("status")) or ("resolved" if row.get("resolved") else "open"),
        "detail": _text(row.get("detail") or row.get("reason") or row.get("statement") or row),
        "source_refs": source_refs,
        "resolution": _text(row.get("resolution"), 400) or None,
    }
    return {
        "conflict_id": _text(row.get("conflict_id"), 180)
        or "RCF-" + _digest(body)[:14].upper(),
        **body,
    }


def _check_payload(name: str, value: Any) -> tuple[bool, Any]:
    if name == "consensus_dispersion":
        if not isinstance(value, Mapping):
            return False, _json_safe(value)
        payload = value.get("output") if isinstance(value.get("output"), Mapping) else value
        valid = isinstance(payload, Mapping) and _value_present(payload.get("min")) \
            and _value_present(payload.get("max"))
        return bool(valid), _json_safe(payload)
    if isinstance(value, Mapping):
        payload = (
            value.get("output") if _value_present(value.get("output"))
            else value.get("result") if _value_present(value.get("result"))
            else value.get("judgement") if _value_present(value.get("judgement"))
            else value.get("value")
        )
    else:
        payload = value
    return _value_present(payload), _json_safe(payload)


def _scan_checks(
    value: Any, *, default_ref: str = "", default_scope: str = "unknown",
    default_stale: bool | None = None, depth: int = 0,
) -> list[dict[str, Any]]:
    if depth > 6:
        return []
    out: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        named = _slug(value.get("check_name") or value.get("function") or value.get("calculation"))
        canonical = _CHECK_ALIASES.get(named)
        if canonical:
            valid, payload = _check_payload(canonical, value)
            out.append({
                "name": canonical, "valid_payload": valid, "payload": payload,
                "source_refs": _refs({**dict(value), "_default_ref": default_ref}),
                "_scope": default_scope,
                "_stale": default_stale,
            })
        for key, child in value.items():
            canonical = _CHECK_ALIASES.get(_slug(key))
            if canonical:
                valid, payload = _check_payload(canonical, child)
                refs = _refs(value)
                if default_ref:
                    refs.append(default_ref)
                out.append({
                    "name": canonical, "valid_payload": valid, "payload": payload,
                    "source_refs": sorted(set(refs)), "_scope": default_scope,
                    "_stale": default_stale,
                })
            if isinstance(child, (Mapping, list, tuple)):
                out.extend(_scan_checks(
                    child, default_ref=default_ref, default_scope=default_scope,
                    default_stale=default_stale, depth=depth + 1,
                ))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            out.extend(_scan_checks(
                child, default_ref=default_ref, default_scope=default_scope,
                default_stale=default_stale, depth=depth + 1,
            ))
    return out


def _normalise_decision_point(
    row: Mapping[str, Any], aliases: Mapping[str, Sequence[Mapping[str, Any]]],
    reference_at: datetime | None,
) -> dict[str, Any]:
    refs = _refs(row)
    resolved = _resolve_refs(refs, aliases)
    issuer_refs = sorted({
        ref for ref in refs
        if any(item.get("scope") == "issuer" and item.get("usable") for item in aliases.get(ref, ()))
    })
    question = _text(row.get("question"), 500)
    metric = _json_safe(row.get("metric")) if _value_present(row.get("metric")) else None
    event = _json_safe(row.get("event")) if _value_present(row.get("event")) else None
    threshold = _json_safe(row.get("threshold")) if _value_present(row.get("threshold")) else None
    resolution = _text(
        row.get("resolution_condition") or row.get("resolution") or row.get("trigger"), 500,
    ) or None
    deadline_raw = row.get("deadline") or row.get("next_publication") or row.get("next_data_point")
    deadline = _iso(deadline_raw)
    status_raw = _text(row.get("status"), 60).lower()
    status = {
        "待验证": "pending", "待定": "pending", "已排期": "scheduled",
        "监测中": "monitoring", "已触发": "triggered", "已解决": "resolved",
        "已关闭": "closed", "逾期": "overdue",
    }.get(status_raw) or _slug(status_raw)
    impact = _text(
        row.get("impact") or row.get("judgement_impact") or row.get("impact_if_triggered"), 500,
    )
    reasons: list[str] = []
    if not question:
        reasons.append("question_missing")
    if metric is None and event is None:
        reasons.append("metric_or_event_missing")
    if threshold is None and not resolution:
        reasons.append("threshold_or_resolution_missing")
    if not deadline:
        reasons.append("deadline_missing_or_not_iso_date")
    if status not in _DECISION_STATUSES:
        reasons.append("status_missing_or_invalid")
    if not refs:
        reasons.append("source_refs_missing")
    if not issuer_refs:
        reasons.append("issuer_source_ref_missing")
    if not impact:
        reasons.append("impact_missing")

    overdue = False
    parsed_deadline = _parse_datetime(deadline)
    if parsed_deadline and reference_at and status in _OPEN_DECISION_STATUSES:
        overdue = parsed_deadline.date() < reference_at.date()
        if overdue:
            status = "overdue"
    freshness = _freshness(resolved, issuer_only=True)
    if freshness == "unknown" and issuer_refs:
        reasons.append("issuer_freshness_unknown")
    eligible = not reasons
    body = {
        "question": question,
        "metric": metric,
        "event": event,
        "threshold": threshold,
        "resolution_condition": resolution,
        "deadline": deadline,
        "status": status or "unknown",
        "source_refs": refs,
        "issuer_source_refs": issuer_refs,
        "reference_resolution": _reference_diagnostics(refs, aliases),
        "impact": impact or None,
        "scope": "issuer" if issuer_refs else "context",
        "freshness": freshness,
        "overdue": overdue,
        "eligible": eligible,
        "ineligibility_reasons": reasons,
    }
    return {
        "point_id": _text(row.get("point_id") or row.get("id"), 160)
        or "RDP-" + _digest(body)[:14].upper(),
        **body,
    }


def _normalise_catalyst(
    row: Mapping[str, Any], aliases: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    refs = _refs(row)
    resolved = _resolve_refs(refs, aliases)
    issuer_refs = sorted({
        ref for ref in refs
        if any(item.get("scope") == "issuer" and item.get("usable") for item in aliases.get(ref, ()))
    })
    raw_type = _text(row.get("catalyst_type") or row.get("type"), 80)
    catalyst_type = _CATALYST_TYPES.get(raw_type.lower()) or _CATALYST_TYPES.get(raw_type)
    event = _json_safe(row.get("event") or row.get("statement") or row.get("title"))
    observation = _json_safe(
        row.get("verification_data") or row.get("observation_data") or row.get("metric")
    )
    endpoint = _text(row.get("endpoint") or row.get("data_endpoint"), 180) or None
    deadline = _iso(
        row.get("deadline") or row.get("next_publication") or row.get("next_data_point")
    )
    realized = _text(row.get("impact_if_realized") or row.get("upside_case"), 500) or None
    missed = _text(row.get("impact_if_missed") or row.get("downside_case"), 500) or None
    reasons: list[str] = []
    if not catalyst_type:
        reasons.append("catalyst_type_missing_or_invalid")
    if not _value_present(event):
        reasons.append("event_missing")
    if not _value_present(observation):
        reasons.append("verification_data_missing")
    if not endpoint and not refs:
        reasons.append("endpoint_or_source_ref_missing")
    if not deadline:
        reasons.append("deadline_missing_or_not_iso_date")
    if not realized:
        reasons.append("impact_if_realized_missing")
    if not missed:
        reasons.append("impact_if_missed_missing")
    body = {
        "type": catalyst_type or "unknown",
        "event": event,
        "verification_data": observation,
        "endpoint": endpoint,
        "deadline": deadline,
        "impact_if_realized": realized,
        "impact_if_missed": missed,
        "source_refs": refs,
        "scope": "issuer" if issuer_refs else "context",
        "context_only": not bool(issuer_refs),
        "freshness": _freshness(resolved, issuer_only=bool(issuer_refs)),
        "status": "complete" if not reasons else "incomplete",
        "gaps": reasons,
    }
    return {
        "catalyst_id": _text(row.get("catalyst_id") or row.get("id"), 160)
        or "RCA-" + _digest(body)[:14].upper(),
        **body,
    }


def _risk_row(
    row: Mapping[str, Any], aliases: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    statement = _text(
        row.get("risk") or row.get("statement") or row.get("fact")
        or row.get("counter_evidence") or row.get("title"),
    )
    if not statement:
        return None
    refs = _refs(row)
    resolved = _resolve_refs(refs, aliases)
    issuer = any(item.get("scope") == "issuer" and item.get("usable") for item in resolved)
    category = _slug(row.get("risk_type") or row.get("category")) or (
        "counter_evidence" if _record_kind(row) == "counter_evidence" else "other"
    )
    priority = _normalise_priority(row)
    body = {
        "category": category,
        "statement": statement,
        "priority": priority,
        "status": _slug(row.get("status")) or "open",
        "scope": "issuer" if issuer else "context",
        "context_only": not issuer,
        "source_refs": refs,
        "freshness": _freshness(resolved, issuer_only=issuer),
        "counter_data": _text(row.get("counter_data") or row.get("what_to_check"), 500) or None,
        "resolution_condition": _text(
            row.get("resolution_condition") or row.get("trigger"), 500,
        ) or None,
        "deadline": _iso(row.get("deadline") or row.get("next_publication")),
        "impact": _text(row.get("impact") or row.get("judgement_impact"), 500) or None,
    }
    return {
        "risk_id": _text(row.get("risk_id") or row.get("id"), 160)
        or "RR-" + _digest(body)[:14].upper(),
        **body,
    }


def build_company_risk_contract(
    evidence_pack: Mapping[str, Any] | None = None,
    hub: Mapping[str, Any] | None = None,
    *,
    issuer_evidence: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    context_evidence: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    events: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    conflicts: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    model_risk_input: Mapping[str, Any] | None = None,
    as_of: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Build a structured, deterministic company risk/catalyst contract.

    Recognised records are intentionally explicit:

    * strong claims: ``kind=strong_claim`` or ``strong_claim=true``;
    * counter-evidence: matching ``claim_id`` plus a relative signal in
      ``disconfirming/weakening/invalidating/opposes/...``;
    * verdict points: ``kind=decision_point`` (or a ``decision_points`` /
      ``verdict_points`` container) with question, metric/event,
      threshold/resolution condition, ISO deadline, status, source refs, impact;
    * catalysts: ``kind=catalyst`` or a ``catalysts`` container with the Vibe
      catalyst type and verification/timing/impact fields;
    * Vibe risk checks: exact ``forward_vs_ttm_judgement`` and
      ``consensus_dispersion`` structured calculation keys/functions.

    Plain text is never parsed for sentiment or direction.  A context source can
    appear in risks/catalysts but only an issuer source reference can close the
    counter-evidence, decision-point, or required-check gates.

    ``model_risk_input`` is an untrusted narrative-structure handoff, separate
    from the trusted evidence pack.  It can propose claims, counter-evidence,
    decisions and catalysts, but never register a source or supply required
    calculations, even when it contains source-like fields or nested check
    keys.  Existing trusted ``pack.risk_contract`` adapters remain supported.
    """
    gaps: list[dict[str, Any]] = []
    input_invalid = False
    pack: Mapping[str, Any]
    if evidence_pack is None:
        pack = {}
    elif isinstance(evidence_pack, Mapping):
        pack = evidence_pack
    else:
        pack = {}
        input_invalid = True
        gaps.append(_gap(
            "invalid_evidence_pack", "evidence_pack is not an object.",
            field="evidence_pack", availability=True,
        ))
    hub_body: Mapping[str, Any]
    if hub is None:
        hub_body = {}
    elif isinstance(hub, Mapping):
        hub_body = hub
    else:
        hub_body = {}
        input_invalid = True
        gaps.append(_gap(
            "invalid_evidence_hub", "hub is not an object.",
            field="hub", availability=True,
        ))

    explicit_issuer = _as_rows(issuer_evidence, label="issuer_evidence", gaps=gaps)
    explicit_context = _as_rows(context_evidence, label="context_evidence", gaps=gaps)
    event_rows = _as_rows(events, label="events", gaps=gaps)
    explicit_conflicts = _as_rows(conflicts, label="conflicts", gaps=gaps)

    reference_raw = as_of or hub_body.get("as_of") or pack.get("generated_at") or pack.get("as_of")
    reference_at = _parse_datetime(reference_raw)
    reference_iso = _iso(reference_raw)
    if reference_at is None:
        gaps.append(_gap(
            "as_of_unavailable", "No deterministic as_of timestamp was supplied.",
            field="as_of", availability=True,
        ))

    source_rows: list[dict[str, Any]] = []
    register = hub_body.get("source_register")
    if isinstance(register, Sequence) and not isinstance(register, (str, bytes, bytearray)):
        for row in register:
            if isinstance(row, Mapping):
                source_rows.append(_source_metadata(row))
    elif register not in (None, []):
        gaps.append(_gap(
            "invalid_source_register", "hub.source_register is not a list.",
            field="hub.source_register", availability=True,
        ))

    structured_records: list[dict[str, Any]] = []
    check_rows: list[dict[str, Any]] = []
    for scope, rows in (("issuer", explicit_issuer), ("context", explicit_context)):
        for raw in rows:
            row = dict(raw)
            row.setdefault("_scope", scope)
            source_rows.append(_source_metadata(row, fallback_scope=scope))
            structured_records.extend(_collect_payload_records(
                row, default_scope=scope, default_ref=_row_id(row),
                default_source_id=_text(row.get("source_id"), 180),
                default_stale=_normalise_stale(row),
            ))
            check_rows.extend(_scan_checks(
                row, default_ref=_row_id(row), default_scope=scope,
                default_stale=_normalise_stale(row),
            ))

    # Match evidence-pack sections back to Hub rows through source_ref.  When no
    # Hub is supplied, only the same conservative issuer/context section policy
    # used by company_hub is applied; an evidence/source id is still required.
    sections = pack.get("sections") if isinstance(pack.get("sections"), Mapping) else {}
    register_by_section: dict[str, list[dict[str, Any]]] = {}
    for source in source_rows:
        ref = _text(source.get("source_ref"), 180)
        if ref.startswith("section:"):
            register_by_section.setdefault(ref.split(":", 1)[1], []).append(source)
    legacy_unstructured = False
    for name, value in sorted(sections.items(), key=lambda pair: str(pair[0])):
        section_name = str(name)
        matched = register_by_section.get(section_name, [])
        meta = matched[0] if matched else {
            "identity": "", "evidence_id": None,
            "source_id": f"pack.{section_name}", "source_ref": f"section:{section_name}",
            "scope": "issuer" if section_name in _ISSUER_SECTION_NAMES else "context",
            "stale": _normalise_stale(value) if isinstance(value, Mapping) else None,
            "status": _slug(value.get("status")) if isinstance(value, Mapping) else "unknown",
            "usable": False,
        }
        if not matched:
            source_rows.append(dict(meta))
        default_ref = _text(meta.get("evidence_id") or meta.get("source_id"), 180)
        found = _collect_payload_records(
            value, default_scope=_text(meta.get("scope"), 20) or "unknown",
            default_ref=default_ref,
            default_source_id=_text(meta.get("source_id"), 180),
            default_stale=meta.get("stale") if isinstance(meta.get("stale"), bool) else None,
        )
        structured_records.extend(found)
        check_rows.extend(_scan_checks(
            value, default_ref=default_ref,
            default_scope=_text(meta.get("scope"), 20) or "unknown",
            default_stale=meta.get("stale") if isinstance(meta.get("stale"), bool) else None,
        ))
        if section_name in {"risk", "risks", "risk_contract"} and value and not found:
            legacy_unstructured = True

    # A top-level risk_contract is also accepted for adapters that already
    # materialised the risk-stage output outside ``sections``.
    if isinstance(pack.get("risk_contract"), (Mapping, list, tuple)):
        structured_records.extend(_collect_payload_records(pack["risk_contract"]))
        check_rows.extend(_scan_checks(pack["risk_contract"]))

    for row in event_rows:
        scope = _normalise_scope(row.get("scope"), "unknown")
        row.setdefault("_scope", scope)
        source_rows.append(_source_metadata(row, fallback_scope=scope))
        structured_records.extend(_collect_payload_records(
            row, default_scope=scope, default_ref=_row_id(row),
            default_source_id=_text(row.get("source_id"), 180),
            default_stale=_normalise_stale(row),
        ))
        check_rows.extend(_scan_checks(
            row, default_ref=_row_id(row), default_scope=scope,
            default_stale=_normalise_stale(row),
        ))

    # Add aliases for every structured row identity after extraction.
    for row in structured_records:
        # Semantic sidecar ids are not independently acquired sources.  An
        # unreferenced claim/point remains ineligible below, but should not
        # create a fictitious failed data feed in the availability register.
        if not any(row.get(key) for key in (
            "evidence_id", "event_id", "source_id", "source_ref",
            "_default_ref", "_source_id",
        )):
            continue
        source_rows.append(_source_metadata(
            row, fallback_scope=_normalise_scope(row.get("_scope"), "unknown"),
        ))
    aliases = _alias_index(source_rows)

    # Deliberately collect model-authored structures *after* closing the
    # trusted source registry.  Do not feed this surface to _scan_checks or
    # _source_metadata: recursively filtering suspicious key spellings is not
    # an adequate trust boundary for fabricated calculation outputs/provenance.
    if isinstance(model_risk_input, Mapping):
        structured_records.extend(_collect_payload_records(model_risk_input))
    elif model_risk_input is not None:
        input_invalid = True
        gaps.append(_gap(
            "invalid_model_risk_input", "model_risk_input is not an object.",
            field="model_risk_input", availability=True,
        ))

    if legacy_unstructured:
        gaps.append(_gap(
            "legacy_risk_text_unstructured",
            "Legacy risk narrative was not promoted into structured evidence.",
            field="risk_contract", availability=True,
        ))

    # Preserve source failures as explicit availability gaps.  They never mean
    # that the associated risk is absent.
    for source in sorted(source_rows, key=lambda row: (
        _text(row.get("scope")), _text(row.get("source_id")), _text(row.get("identity")),
    )):
        if source.get("status") in _BAD_SOURCE_STATUSES or not source.get("usable"):
            refs = [
                item for item in (
                    source.get("identity"), source.get("source_id"), source.get("source_ref"),
                ) if item
            ]
            # Structured child rows inherit their parent Evidence Hub reference.
            # Do not report a synthetic ``unknown`` child metadata row as a
            # failed source when that alias resolves to a usable registered
            # source.  A genuinely failed registered source has no such peer.
            if any(
                candidate.get("usable")
                for ref in refs for candidate in aliases.get(_text(ref, 180), ())
            ):
                continue
            gaps.append(_gap(
                "issuer_source_unavailable" if source.get("scope") == "issuer"
                else "context_source_unavailable",
                f"Source status is {source.get('status') or 'unknown'}; absence is not evidence of no risk.",
                # Whether a missing source is contract-critical is decided by
                # the Hub gap register and the concrete counter/check/point
                # slots.  A failed optional issuer feed (for example K-line or
                # announcements) must be visible without automatically
                # overriding Vibe's optional-source rule.
                severity="optional",
                field="source", source_refs=refs, availability=True,
            ))

    hub_gaps = hub_body.get("gaps")
    if isinstance(hub_gaps, Sequence) and not isinstance(hub_gaps, (str, bytes, bytearray)):
        for item in hub_gaps:
            if not isinstance(item, Mapping):
                continue
            kind = _slug(item.get("kind")) or "evidence_hub_gap"
            is_availability = kind in {
                "source_failed", "source_missing", "source_empty", "required_stage_missing",
                "required_stage_stale", "freshness_unknown", "stale_source",
            }
            gaps.append(_gap(
                kind, item.get("detail") or item,
                severity="critical" if item.get("severity") == "required" else "optional",
                field=_text(item.get("stage") or "hub", 120),
                source_refs=[_text(item.get("source_id"), 180)] if item.get("source_id") else [],
                availability=is_availability,
            ))

    # Conflicts can come from all three contract surfaces.  An unresolved
    # required conflict blocks the deterministic risk gate but remains visible
    # as a ranked risk rather than being silently reconciled.
    conflict_register_supplied = bool(
        conflicts is not None
        or isinstance(hub_body.get("conflicts"), Sequence)
        and not isinstance(hub_body.get("conflicts"), (str, bytes, bytearray))
        or isinstance(pack.get("conflicts"), Sequence)
        and not isinstance(pack.get("conflicts"), (str, bytes, bytearray))
    )
    if not conflict_register_supplied:
        gaps.append(_gap(
            "conflict_register_unavailable",
            "No explicit conflict register was supplied; omission is not proof that sources agree.",
            field="conflicts", availability=True,
        ))
    conflict_rows = list(explicit_conflicts)
    if isinstance(hub_body.get("conflicts"), Sequence):
        conflict_rows.extend(
            item for item in hub_body.get("conflicts") or [] if isinstance(item, Mapping)
        )
    if isinstance(pack.get("conflicts"), Sequence):
        conflict_rows.extend(
            item for item in pack.get("conflicts") or [] if isinstance(item, Mapping)
        )
    normalised_conflicts = _dedupe_by_key(
        (_normalise_conflict(row) for row in conflict_rows), "conflict_id",
    )
    for conflict in normalised_conflicts:
        if conflict["severity"] == "required" and conflict["status"] != "resolved":
            gaps.append(_gap(
                "required_conflict_unresolved", conflict["detail"],
                field="conflicts", source_refs=conflict["source_refs"],
            ))

    # Strong claims and true relative counter-evidence.
    claims = [row for row in structured_records if _is_strong_claim(row)]
    claim_rows = _dedupe_by_key(
        ({**dict(row), "_claim_id": _claim_id(row)} for row in claims), "_claim_id",
    )
    claims = [(str(row.pop("_claim_id")), row) for row in claim_rows]
    counter_candidates = [
        row for row in structured_records
        if _record_kind(row) in {"counter_evidence", "counterexample", "risk"}
        and _counter_claim_ids(row)
    ]
    counter_groups: list[dict[str, Any]] = []
    rejected_counters: list[dict[str, Any]] = []
    for claim_id, claim in claims:
        claim_refs = _refs(claim)
        claim_resolved = _resolve_refs(claim_refs, aliases)
        claim_issuer_refs = sorted({
            ref for ref in claim_refs
            if any(item.get("scope") == "issuer" and item.get("usable") for item in aliases.get(ref, ()))
        })
        counters: list[dict[str, Any]] = []
        for candidate in counter_candidates:
            if claim_id not in _counter_claim_ids(candidate):
                continue
            relation = _counter_relation(candidate)
            refs = _refs(candidate)
            resolved = _resolve_refs(refs, aliases)
            issuer_refs = sorted({
                ref for ref in refs
                if any(item.get("scope") == "issuer" and item.get("usable") for item in aliases.get(ref, ()))
            })
            reasons: list[str] = []
            if relation in _SAME_DIRECTION_SIGNALS or relation not in _COUNTER_SIGNALS:
                reasons.append("not_explicitly_disconfirming")
            statement = _counter_statement(candidate)
            if not statement:
                reasons.append("counter_fact_missing")
            if not issuer_refs:
                reasons.append("issuer_source_ref_missing")
            # A Hub evidence id identifies provenance, not an individual fact.
            # One issuer filing/section may legitimately contain both the fact
            # supporting a claim and a different fact that disconfirms it.  Only
            # reject a literal dual-role structured record; equal source-ref
            # sets alone do not make two separately authored records identical.
            if _canonical_json(candidate) == _canonical_json(claim):
                reasons.append("counter_not_independent_record")
            if reasons:
                rejected_counters.append({
                    "claim_id": claim_id,
                    "candidate_ref": _row_id(candidate) or None,
                    "relation": relation or "unspecified",
                    "reasons": reasons,
                    "scope": "issuer" if issuer_refs else "context",
                    "reference_resolution": _reference_diagnostics(refs, aliases),
                })
                continue
            counter = {
                "counter_id": _text(candidate.get("counter_id") or candidate.get("id"), 160)
                or "RCE-" + _digest({"claim": claim_id, "statement": statement, "refs": refs})[:14].upper(),
                "statement": statement,
                "relative_direction": relation,
                "source_refs": refs,
                "issuer_source_refs": issuer_refs,
                "reference_resolution": _reference_diagnostics(refs, aliases),
                "freshness": _freshness(resolved, issuer_only=True),
                "impact": _text(candidate.get("impact") or candidate.get("judgement_impact"), 500) or None,
            }
            counters.append(counter)
        counters = _dedupe_by_key(counters, "counter_id")
        group_freshness = (
            "fresh" if claim_issuer_refs and _freshness(claim_resolved, issuer_only=True) == "fresh"
            and any(row["freshness"] == "fresh" for row in counters)
            else "stale" if counters and (
                _freshness(claim_resolved, issuer_only=True) == "stale"
                or all(row["freshness"] == "stale" for row in counters)
            ) else "unknown"
        )
        group = {
            "group_id": "RCG-" + _digest({
                "claim_id": claim_id, "counter_ids": [row["counter_id"] for row in counters],
            })[:14].upper(),
            "claim_id": claim_id,
            "claim": _text(claim.get("claim") or claim.get("statement") or claim.get("conclusion")),
            "claim_direction": _slug(claim.get("direction")) or "unspecified",
            "claim_source_refs": claim_refs,
            "issuer_claim_source_refs": claim_issuer_refs,
            "claim_reference_resolution": _reference_diagnostics(claim_refs, aliases),
            "counter_evidence": counters,
            "freshness": group_freshness,
            "status": "complete" if claim_issuer_refs and counters else "incomplete",
            "downgraded_to_scenario": bool(counters),
        }
        counter_groups.append(group)
        if not claim_issuer_refs:
            gaps.append(_gap(
                "strong_claim_lacks_issuer_source", f"Strong claim {claim_id} has no issuer source.",
                field="counter_evidence", source_refs=claim_refs,
            ))
        if not counters:
            gaps.append(_gap(
                "strong_claim_lacks_true_counter",
                f"Strong claim {claim_id} has no explicitly disconfirming issuer evidence.",
                field="counter_evidence", source_refs=claim_refs,
            ))
    if not claims:
        gaps.append(_gap(
            "strong_claim_inventory_missing",
            "No structured strong claim was supplied, so the counter-evidence test cannot run.",
            field="counter_evidence", availability=True,
        ))
    if not any(group["status"] == "complete" for group in counter_groups):
        gaps.append(_gap(
            "true_counter_group_missing",
            "At least one issuer-backed, explicitly disconfirming counter-evidence group is required.",
            field="counter_evidence", availability=True,
        ))

    # Required Vibe risk checks.  A matching context calculation is retained but
    # does not satisfy the issuer gate.
    checks: dict[str, dict[str, Any]] = {}
    for name in ("forward_vs_ttm_judgement", "consensus_dispersion"):
        candidates = [row for row in check_rows if row.get("name") == name]
        candidate_rows: list[dict[str, Any]] = []
        for row in candidates:
            refs = sorted(set(row.get("source_refs") or []))
            resolved = _resolve_refs(refs, aliases)
            issuer_refs = sorted({
                ref for ref in refs
                if any(item.get("scope") == "issuer" and item.get("usable") for item in aliases.get(ref, ()))
            })
            candidate_rows.append({
                "payload": row.get("payload"),
                "valid_payload": bool(row.get("valid_payload")),
                "source_refs": refs,
                "issuer_source_refs": issuer_refs,
                "freshness": _freshness(resolved, issuer_only=True),
                "eligible": bool(row.get("valid_payload") and issuer_refs),
            })
        candidate_rows.sort(key=lambda row: _canonical_json(row))
        eligible = [row for row in candidate_rows if row["eligible"]]
        check_status = (
            "complete" if any(row["freshness"] == "fresh" for row in eligible)
            else "stale" if eligible and any(row["freshness"] == "stale" for row in eligible)
            else "incomplete"
        )
        checks[name] = {"status": check_status, "candidates": candidate_rows}
        if not eligible:
            gaps.append(_gap(
                f"{name}_missing",
                f"Structured issuer-backed {name} is required by the risk contract.",
                field=name, availability=True,
            ))

    decision_candidates = [
        row for row in structured_records
        if _record_kind(row) in {"decision_point", "verdict_point"}
        or row.get("decision_point") is True
    ]
    decision_points = _dedupe_by_key(
        (_normalise_decision_point(row, aliases, reference_at) for row in decision_candidates),
        "point_id",
    )
    eligible_points = [row for row in decision_points if row["eligible"]]
    if len(eligible_points) < 3:
        gaps.append(_gap(
            "issuer_decision_points_insufficient",
            f"Three issuer-backed decision points are required; {len(eligible_points)} are eligible.",
            field="decision_points", availability=True,
        ))
    for point in decision_points:
        if not point["eligible"]:
            gaps.append(_gap(
                "decision_point_incomplete",
                f"Decision point {point['point_id']} is ineligible: {', '.join(point['ineligibility_reasons'])}.",
                severity="optional", field="decision_points",
                source_refs=point["source_refs"], availability=True,
            ))

    catalyst_candidates = [
        row for row in structured_records
        if _record_kind(row) == "catalyst" or _value_present(row.get("catalyst_type"))
    ]
    catalysts = _dedupe_by_key(
        (_normalise_catalyst(row, aliases) for row in catalyst_candidates),
        "catalyst_id",
    )
    for catalyst in catalysts:
        if catalyst["status"] != "complete":
            gaps.append(_gap(
                "catalyst_incomplete",
                f"Catalyst {catalyst['catalyst_id']} lacks: {', '.join(catalyst['gaps'])}.",
                severity="optional", field="catalysts",
                source_refs=catalyst["source_refs"], availability=True,
            ))

    # Risks are ranked by explicit qualitative priority only.  No score or
    # probability is manufactured.  Counter facts, conflicts and availability
    # failures remain visible even when no explicit risk row was supplied.
    risk_candidates = [
        row for row in structured_records
        if _record_kind(row) in {"risk", "counter_evidence"} or row.get("risk_type")
    ]
    risks = [item for item in (_risk_row(row, aliases) for row in risk_candidates) if item]
    for conflict in normalised_conflicts:
        resolved = _resolve_refs(conflict["source_refs"], aliases)
        issuer = any(item.get("scope") == "issuer" for item in resolved)
        body = {
            "category": "data_source_conflict",
            "statement": conflict["detail"],
            "priority": "high" if conflict["severity"] == "required" else "medium",
            "status": conflict["status"],
            "scope": "issuer" if issuer else "context",
            "context_only": not issuer,
            "source_refs": conflict["source_refs"],
            "freshness": _freshness(resolved, issuer_only=issuer),
            "counter_data": None,
            "resolution_condition": conflict.get("resolution"),
            "deadline": None,
            "impact": "Unresolved source conflict limits confidence in the affected conclusion.",
        }
        risks.append({"risk_id": "RR-" + _digest(body)[:14].upper(), **body})

    # Deduplicate gaps before converting availability gaps into evidence-quality
    # risks; this keeps repeated Hub/pack failure signals from inflating ranking.
    gaps = sorted(
        {row["gap_id"]: row for row in gaps}.values(), key=lambda row: row["gap_id"],
    )
    for item in gaps:
        if not item["availability"]:
            continue
        resolved = _resolve_refs(item["source_refs"], aliases)
        issuer_gap = any(row.get("scope") == "issuer" for row in resolved)
        body = {
            "category": "data_availability",
            "statement": item["detail"],
            "priority": "high" if item["severity"] == "critical" else "medium",
            "status": "open",
            "scope": "issuer" if issuer_gap else "context",
            "context_only": not issuer_gap,
            "source_refs": item["source_refs"],
            "freshness": "unknown",
            "counter_data": None,
            "resolution_condition": "Provide a structured, source-backed replacement and rerun the gate.",
            "deadline": None,
            "impact": "Missing evidence prevents a no-risk inference.",
        }
        risks.append({"risk_id": "RR-" + _digest(body)[:14].upper(), **body})
    if not risk_candidates and not normalised_conflicts:
        body = {
            "category": "risk_inventory_unavailable",
            "statement": "No structured risk inventory was supplied; this is not evidence that risk is absent.",
            "priority": "high", "status": "open", "scope": "issuer",
            "context_only": False, "source_refs": [], "freshness": "unknown",
            "counter_data": None,
            "resolution_condition": "Supply issuer-backed risk records and rerun the gate.",
            "deadline": None, "impact": "Risk conclusions are not decision-grade.",
        }
        risks.append({"risk_id": "RR-" + _digest(body)[:14].upper(), **body})

    risks = _dedupe_by_key(risks, "risk_id")
    risks.sort(key=lambda row: (
        _PRIORITY_ORDER.get(row["priority"], 4),
        0 if row["scope"] == "issuer" else 1,
        row["category"], row["risk_id"],
    ))
    for index, row in enumerate(risks, start=1):
        row["rank"] = index
        row["rank_basis"] = "explicit qualitative priority; no synthetic score"

    usable_issuer_sources = [
        source for source in source_rows
        if source.get("scope") == "issuer" and source.get("usable")
    ]
    any_input = bool(source_rows or structured_records or event_rows or normalised_conflicts)
    hub_failed = _slug(hub_body.get("status")) == "failed"
    hard_failure = input_invalid or not any_input or (hub_failed and not usable_issuer_sources)

    structural_complete = bool(
        claims
        and counter_groups
        and all(group["status"] == "complete" for group in counter_groups)
        and len(eligible_points) >= 3
        and all(checks[name]["status"] in {"complete", "stale"} for name in checks)
        and not any(gap["severity"] == "critical" for gap in gaps)
    )
    closing_freshness = [
        *(group["freshness"] for group in counter_groups if group["status"] == "complete"),
        *(point["freshness"] for point in eligible_points[:3]),
        *(checks[name]["status"] for name in checks),
    ]
    overdue = any(point["overdue"] for point in eligible_points)
    stale_closure = structural_complete and (
        overdue or "stale" in closing_freshness or "unknown" in closing_freshness
        or reference_at is None
    )
    if hard_failure:
        status = "failed"
    elif stale_closure:
        status = "stale"
    elif not structural_complete or any(gap["severity"] == "critical" for gap in gaps):
        status = "incomplete"
    else:
        status = "complete"

    # Passing this pure gate must not be presented as an independent semantic
    # endorsement.  Keep the boundary in the hashed contract, not only in UI
    # prose, so a changed validation promise changes the replay identity too.
    validation_scope = {
        "mode": "deterministic",
        "checks": [
            "structured_fields", "registered_reference_resolution",
            "issuer_context_boundary", "supplied_freshness",
        ],
        "source_content_refetched": False,
    }
    semantic_review = {
        "status": "not_independently_verified",
        "counter_relation_basis": "declared_by_structured_input",
        "counter_direction_verified": False,
        "citation_entailment_verified": False,
        "affects_gate_status": False,
        "detail": (
            "确定性 Gate 仅核验结构、引用登记、issuer/context 边界和已提供的新鲜度；"
            "反证是否真正反向、引用内容是否蕴含结论，尚未经过独立语义核验。"
        ),
    }
    contract_identity = {
        "schema_version": SCHEMA_VERSION,
        "validation_scope": validation_scope,
        "semantic_review": semantic_review,
        "as_of": reference_iso,
        "counter_groups": counter_groups,
        "decision_points": decision_points,
        "checks": checks,
        "conflicts": normalised_conflicts,
        "gaps": gaps,
    }
    contract_id = "RISK-" + _digest(contract_identity)[:16].upper()
    unavailable_gaps = [row for row in gaps if row["availability"]]
    result = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": contract_id,
        "status": status,
        "as_of": reference_iso,
        "validation_scope": validation_scope,
        "semantic_review": semantic_review,
        "gate": {
            "passed": status == "complete",
            "context_can_close_issuer_gate": False,
            "counter_evidence": {
                "required_groups": max(1, len(claims)),
                "strong_claims": len(claims),
                "complete_groups": sum(row["status"] == "complete" for row in counter_groups),
                "passed": bool(claims) and all(
                    row["status"] == "complete" for row in counter_groups
                ),
            },
            "decision_points": {
                "required": 3,
                "observed": len(decision_points),
                "eligible_issuer": len(eligible_points),
                "passed": len(eligible_points) >= 3,
            },
            "required_checks": {
                name: checks[name]["status"] for name in sorted(checks)
            },
        },
        "counter_evidence_groups": counter_groups,
        "rejected_counter_evidence": sorted(
            rejected_counters,
            key=_canonical_json,
        ),
        "decision_points": decision_points,
        "risk_ranking": risks,
        "catalysts": catalysts,
        "checks": checks,
        "conflicts": normalised_conflicts,
        "gaps": gaps,
        "unavailable_gaps": unavailable_gaps,
        "summary": {
            "issuer_sources": len({
                _canonical_json(row) for row in source_rows if row.get("scope") == "issuer"
            }),
            "usable_issuer_sources": len({
                _canonical_json(row) for row in usable_issuer_sources
            }),
            "context_sources": len({
                _canonical_json(row) for row in source_rows if row.get("scope") == "context"
            }),
            "strong_claims": len(claims),
            "counter_groups": len(counter_groups),
            "eligible_decision_points": len(eligible_points),
            "risks": len(risks),
            "catalysts": len(catalysts),
            "critical_gaps": sum(row["severity"] == "critical" for row in gaps),
            "optional_gaps": sum(row["severity"] == "optional" for row in gaps),
        },
    }
    return _json_safe(result)


def risk_stage_evidence_from_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a risk contract into ``CompanyResearchPipeline`` stage input."""
    if not isinstance(contract, Mapping):
        return {
            "evidence": [],
            "gaps": [{
                "code": "risk_contract_invalid",
                "message": "Risk contract is not an object.",
                "stage": "risk", "input_key": "risk", "critical": True,
            }],
            "failure": "Risk contract is not an object.",
        }
    status = _slug(contract.get("status")) or "failed"
    contract_id = _text(contract.get("contract_id"), 180)
    evidence = []
    if contract_id:
        evidence.append({
            "input_key": "risk", "evidence_id": contract_id,
            "available": status in {"complete", "stale"},
            "stale": status == "stale",
        })
    gaps = []
    for item in contract.get("gaps") or []:
        if not isinstance(item, Mapping) or item.get("severity") != "critical":
            continue
        gaps.append({
            "code": _text(item.get("code"), 120) or "risk_contract_gap",
            "message": _text(item.get("detail"), 500) or "Risk contract gap.",
            "stage": "risk", "input_key": _text(item.get("field"), 120) or "risk",
            "critical": True,
            "details": {"gap_id": item.get("gap_id")},
        })
    if status == "incomplete" and not gaps:
        gaps.append({
            "code": "risk_contract_incomplete",
            "message": "Risk contract did not pass its deterministic gate.",
            "stage": "risk", "input_key": "risk", "critical": True,
        })
    result: dict[str, Any] = {
        "evidence": evidence,
        "gaps": gaps,
        "stale": status == "stale",
    }
    if status == "failed":
        result["failure"] = "Risk contract failed; no risk conclusion was fabricated."
    return result


__all__ = [
    "SCHEMA_VERSION",
    "build_company_risk_contract",
    "risk_stage_evidence_from_contract",
]
