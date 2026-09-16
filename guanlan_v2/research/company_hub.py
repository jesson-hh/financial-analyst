"""Deterministic evidence routing for one company-research workspace.

The hub deliberately does not fetch data and does not write workspace state.  It
normalises the existing ``company-research-evidence@2`` pack together with saved
workspace live snapshots into a small, auditable routing contract.  Downstream
research stages consume evidence ids; they must not infer issuer facts from an
industry, macro, positioning, or other peripheral-market source.

The public builder is total for JSON-like input: malformed or failed inputs are
reported as structured gaps instead of being silently dropped or raising during
an Agent run.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "company-research-hub@1"
SUPPORTED_PACK_SCHEMA = "company-research-evidence@2"
STAGES: tuple[str, ...] = (
    "profile", "financials", "estimates", "valuation", "risk", "report",
)
REQUIRED_INPUT_STAGES: tuple[str, ...] = STAGES[:-1]


# scope, stages, reliability, maximum content age in calendar days
_SECTION_POLICY: dict[str, tuple[str, tuple[str, ...], str, int]] = {
    "company_profile": ("issuer", ("profile", "report"), "official", 30),
    "quote_live": ("issuer", ("profile", "valuation", "risk"), "secondary", 3),
    "company_facts": (
        "issuer", ("profile", "valuation", "risk"), "secondary", 200,
    ),
    # A company profile/F10 card is not a substitute for income statement,
    # balance-sheet and cash-flow rows.  The workspace adapter adds this
    # explicit PIT financial section when the existing loader can provide it.
    "financials": ("issuer", ("financials", "valuation", "risk"), "official", 550),
    "announcements": ("issuer", ("profile", "risk"), "official", 180),
    "expectations": ("issuer", ("estimates", "valuation"), "secondary", 120),
    "valuation": ("issuer", ("valuation",), "derived", 120),
    "research_views": ("issuer", ("estimates", "risk"), "untrusted", 120),
    "kuaixun": ("issuer", ("risk",), "untrusted", 30),
    # The following may inform a stage but never close an issuer-evidence gate.
    "fundflow": ("context", ("risk",), "secondary", 7),
    "board_eco": ("context", ("risk",), "secondary", 7),
    "sentiment": ("context", ("risk",), "derived", 7),
    "chain": ("context", ("profile", "risk"), "derived", 120),
    "quant": ("context", ("valuation", "risk"), "derived", 30),
    "mainline": ("context", ("risk",), "derived", 14),
    "macro": ("context", ("risk",), "derived", 14),
    "holding": ("context", ("risk",), "internal", 7),
    # Vibe-Research's RSS radar is useful industry context, never issuer proof.
    "newsradar": ("context", ("profile", "risk"), "untrusted", 30),
}


# Workspace adapters are intentionally conservative.  SEC facts can be issuer
# evidence only when the saved query names the bound workspace subject; every
# industry/macro/peripheral feed remains context even if its query contains a
# company symbol.
_WORKSPACE_POLICY: dict[str, tuple[str, tuple[str, ...], str, int]] = {
    "research.trade_disclosure_calendar.live": (
        "context", ("profile", "risk"), "official", 370,
    ),
    "research.tw_monthly_revenue.live": (
        "context", ("profile", "financials", "risk"), "official", 75,
    ),
    "research.cn_commodity_futures.live": (
        "context", ("valuation", "risk"), "secondary", 7,
    ),
    "research.control_lists.live": ("context", ("risk",), "official", 180),
    "research.sec_filings_xbrl.live": (
        "issuer", ("profile", "financials", "risk"), "official", 550,
    ),
    "research.finra_short_volume.live": (
        "context", ("valuation", "risk"), "official", 7,
    ),
    "research.us_treasury.live": (
        "context", ("valuation", "risk"), "official", 7,
    ),
    "research.cftc_cot.live": ("context", ("valuation", "risk"), "official", 14),
    "research.overseas_earnings_calendar.live": (
        "context", ("estimates", "risk"), "secondary", 45,
    ),
    "research.newsradar.live": (
        "context", ("profile", "risk"), "untrusted", 30,
    ),
}

_REQUIRED_SECTIONS = frozenset({
    "company_facts", "financials", "announcements", "expectations", "valuation",
})
_GOOD_STATUSES = frozenset({"ok", "partial", "empty", "saved", "complete"})
_BAD_STATUSES = frozenset({
    "error", "failed", "invalid", "unavailable", "timeout", "corrupt",
})
_DATE_KEYS = (
    "as_of", "content_date", "report_period", "filing_date", "filed",
    "period_end", "date", "publish_ts", "published_at", "actual_date",
    "source_updated_at",
)
_UNIT_KEYS = ("unit", "uom", "currency")


def _json_safe(value: Any) -> Any:
    """Return a stable JSON-compatible representation without NaN/Infinity."""
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
        safe = [_json_safe(item) for item in value]
        return sorted(safe, key=_canonical_json)
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_value(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    if parsed.hour == parsed.minute == parsed.second == parsed.microsecond == 0:
        return parsed.date().isoformat()
    return parsed.isoformat().replace("+00:00", "Z")


def _walk_mappings(value: Any, *, depth: int = 0) -> Iterable[Mapping[str, Any]]:
    if depth > 3:
        return
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk_mappings(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value[:500]:
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk_mappings(child, depth=depth + 1)


def _content_as_of(value: Any) -> str | None:
    # A normalised envelope/section owns its explicit content anchor.  Nested
    # event dates may be newer than a financial report period, but must not
    # silently rewrite the source's declared ``as_of``.
    if isinstance(value, Mapping):
        explicit = _iso_value(value.get("as_of"))
        if explicit is not None:
            return explicit
    dates: list[datetime] = []
    for item in _walk_mappings(value):
        for key in _DATE_KEYS:
            parsed = _parse_datetime(item.get(key))
            if parsed is not None:
                dates.append(parsed)
    if not dates:
        return None
    latest = max(dates)
    if latest.hour == latest.minute == latest.second == latest.microsecond == 0:
        return latest.date().isoformat()
    return latest.isoformat().replace("+00:00", "Z")


def _period(value: Mapping[str, Any]) -> str | None:
    raw = value.get("content_period") or value.get("period")
    if isinstance(raw, Mapping):
        first = raw.get("first_year") or raw.get("start") or raw.get("period_start")
        last = raw.get("last_year") or raw.get("end") or raw.get("period_end")
        if first or last:
            return f"{first or '?'}..{last or '?'}"[:120]
        return _canonical_json(raw)[:120]
    if raw not in (None, ""):
        return _bounded_text(raw, 120)
    rows = value.get("rows") if isinstance(value.get("rows"), list) else []
    starts: list[str] = []
    ends: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        start = _iso_value(row.get("period_start"))
        end = _iso_value(row.get("period_end"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
    if starts or ends:
        return f"{min(starts) if starts else '?'}..{max(ends) if ends else '?'}"
    frequency = value.get("frequency") or value.get("latency_note")
    return _bounded_text(frequency, 120) or None


def _unit(value: Any) -> str | None:
    units: set[str] = set()
    for item in _walk_mappings(value):
        for key in _UNIT_KEYS:
            unit = _bounded_text(item.get(key), 40)
            if unit:
                units.add(unit)
    if not units:
        return None
    rendered = ",".join(sorted(units))
    return rendered[:160]


def _explicit_stale(value: Mapping[str, Any]) -> bool | None:
    if isinstance(value.get("stale"), bool):
        return bool(value["stale"])
    freshness = value.get("freshness")
    if isinstance(freshness, Mapping):
        if isinstance(freshness.get("stale"), bool):
            return bool(freshness["stale"])
        state = str(freshness.get("status") or freshness.get("state") or "").lower()
        if state in {"stale", "expired"}:
            return True
        if state in {"fresh", "ok", "live"}:
            return False
    state = str(value.get("status") or "").lower()
    if state in {"stale", "expired"}:
        return True
    return None


def _stale_at(
    value: Mapping[str, Any], content_as_of: str | None,
    reference_at: datetime | None, max_age_days: int,
) -> bool | None:
    explicit = _explicit_stale(value)
    if explicit is not None:
        return explicit
    observed = _parse_datetime(content_as_of)
    if observed is None or reference_at is None:
        return None
    return (reference_at.date() - observed.date()).days > max_age_days


def _future_at(
    content_as_of: str | None, reference_at: datetime | None,
) -> bool | None:
    """Return whether content falls after the Hub's daily PIT reference.

    Future evidence is a separate temporal state, not the inverse of stale.
    It must be checked independently of an upstream ``stale=false`` assertion:
    a provider cannot make post-cutoff content point-in-time safe by labelling it
    fresh.  Company research currently uses a calendar-day PIT contract, so a
    later timestamp on the same date is allowed; only a later date is future.
    """
    observed = _parse_datetime(content_as_of)
    if observed is None or reference_at is None:
        return None
    return observed.date() > reference_at.date()


def _authority_reliability(snapshot: Mapping[str, Any], fallback: str) -> str:
    sources = snapshot.get("sources")
    if not isinstance(sources, list) or not sources:
        return fallback
    authorities = " ".join(
        str(item.get("authority") or item.get("name") or "")
        for item in sources if isinstance(item, Mapping)
    ).lower()
    official_markers = (
        "sec", "edgar", "finra", "treasury", "cftc", "nasdaq",
        "证券交易所", "巨潮", "政府", "商务部", "ofac", "bis", "twse", "tpex",
    )
    return "official" if any(marker in authorities for marker in official_markers) else fallback


def _source_name(value: Mapping[str, Any], fallback: str) -> str:
    source = value.get("source")
    if isinstance(source, str) and source.strip():
        return _bounded_text(source, 160)
    return fallback


def _candidate(
    *, source_id: str, source_type: str, stages: Sequence[str],
    reliability: str, max_age_days: int, value: Mapping[str, Any],
    origin: str, source_ref: str, status: str = "ok", usable: bool = True,
    supports_required: bool = True, authorities: Sequence[str] = (),
) -> dict[str, Any]:
    content_as_of = _content_as_of(value)
    return {
        "base_source_id": _bounded_text(source_id, 180) or "unknown",
        "type": source_type,
        "stages": tuple(stage for stage in stages if stage in STAGES),
        "reliability": reliability,
        "max_age_days": max_age_days,
        "as_of": content_as_of,
        "period": _period(value),
        "unit": _unit(value),
        "status": _bounded_text(status, 32).lower() or "unknown",
        "origin": origin,
        "source_ref": _bounded_text(source_ref, 240),
        "record_count": (
            len(value.get("rows")) if isinstance(value.get("rows"), list)
            else len(value.get("items")) if isinstance(value.get("items"), list)
            else None
        ),
        "warnings": [
            _bounded_text(item, 300)
            for item in (value.get("warnings") or [])[:12]
        ] if isinstance(value.get("warnings"), list) else [],
        "authorities": sorted({_bounded_text(item, 160) for item in authorities if item}),
        "content_digest": _digest(value),
        "value": value,
        "usable": bool(usable),
        "supports_required": bool(supports_required),
    }


def _gap(
    *, kind: str, stage: str | None, source_id: str | None,
    detail: Any, severity: str,
) -> dict[str, Any]:
    body = {
        "kind": kind,
        "stage": stage,
        "source_id": source_id,
        "severity": severity,
        "detail": _bounded_text(detail, 500),
    }
    return {"gap_id": "G-" + _digest(body)[:12].upper(), **body}


def _conflict(
    *, kind: str, stage: str | None, source_ids: Sequence[str],
    detail: Any, severity: str,
) -> dict[str, Any]:
    body = {
        "kind": kind,
        "stage": stage,
        "source_ids": sorted({_bounded_text(item, 180) for item in source_ids if item}),
        "severity": severity,
        "detail": _bounded_text(detail, 500),
    }
    return {"conflict_id": "C-" + _digest(body)[:12].upper(), **body}


def _snapshot_record(raw: Any) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    """Return ``(snapshot, metadata, reference)`` for accepted workspace shapes."""
    if not isinstance(raw, Mapping):
        return {}, {}, ""
    metadata: Mapping[str, Any] = {}
    snapshot: Mapping[str, Any] = raw
    if isinstance(raw.get("snapshot"), Mapping):
        snapshot = raw["snapshot"]
        if isinstance(raw.get("asset"), Mapping):
            metadata = raw["asset"]
        elif isinstance(raw.get("metadata"), Mapping):
            metadata = raw["metadata"]
        else:
            metadata = raw
    reference = str(
        metadata.get("id") or raw.get("asset_id") or metadata.get("ref") or ""
    )
    return snapshot, metadata, reference


def _subject_code(metadata: Mapping[str, Any]) -> str:
    subject = metadata.get("subject")
    if not isinstance(subject, Mapping):
        return ""
    return _bounded_text(subject.get("code"), 40).upper()


def _identity_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _sec_is_bound_issuer(snapshot: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    subject = _identity_token(_subject_code(metadata))
    query = snapshot.get("query") if isinstance(snapshot.get("query"), Mapping) else {}
    queried = _identity_token(query.get("ticker") or query.get("cik"))
    if not subject or not queried:
        return False
    # Strip common A-share venue prefixes only; do not guess cross-market identity.
    subject_bare = re.sub(r"^(SH|SZ|BJ)", "", subject)
    return queried in {subject, subject_bare}


def _provider_authorities(snapshot: Mapping[str, Any]) -> list[str]:
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        return []
    return [
        _bounded_text(item.get("authority") or item.get("name"), 160)
        for item in sources if isinstance(item, Mapping)
        and (item.get("authority") or item.get("name"))
    ]


def _reference_time(
    explicit: Any, pack: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
) -> datetime | None:
    parsed = _parse_datetime(explicit)
    if parsed is not None:
        return parsed
    parsed = _parse_datetime(pack.get("generated_at"))
    if parsed is not None:
        return parsed
    # Pull times are availability anchors, not content dates.  They are used
    # only as the comparison clock when the caller supplied no report clock.
    pulled: list[datetime] = []
    for candidate in candidates:
        value = candidate.get("value")
        if not isinstance(value, Mapping):
            continue
        for key in ("pulled_at", "retrieved_at", "generated_at"):
            item = _parse_datetime(value.get(key))
            if item is not None:
                pulled.append(item)
    return max(pulled) if pulled else None


def _normalise_declared_conflicts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            stage = str(item.get("stage") or "") or None
            source_ids = item.get("source_ids") if isinstance(item.get("source_ids"), list) else []
            out.append(_conflict(
                kind=str(item.get("kind") or "declared"),
                stage=stage if stage in STAGES else None,
                source_ids=[str(value) for value in source_ids],
                detail=item.get("detail") or item.get("reason") or item,
                severity=str(item.get("severity") or "required"),
            ))
        else:
            out.append(_conflict(
                kind="declared", stage=None, source_ids=[], detail=item,
                severity="required",
            ))
    return out


def build_company_research_hub(
    evidence_pack: Mapping[str, Any] | None = None,
    workspace_snapshots: Iterable[Any] = (),
    *,
    sections: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    as_of: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Build one ``company-research-hub@1`` JSON-compatible document.

    ``evidence_pack`` accepts the persisted body of an existing
    ``company-research-evidence@2`` pack.  Callers that already unpacked it may
    pass ``sections`` and ``coverage`` directly.  ``workspace_snapshots`` accepts
    a raw live envelope, the durable ``{asset_id, dataset_id, snapshot}`` payload,
    or ``{asset|metadata, snapshot}`` returned by the workspace tools.

    No clock is read implicitly.  Freshness uses ``as_of``, then the pack's
    ``generated_at``, then the latest input pull time.  If none is available,
    ``stale`` is ``null`` and the affected required stage remains incomplete.
    """
    pack: Mapping[str, Any] = evidence_pack if isinstance(evidence_pack, Mapping) else {}
    supplied_pack_invalid = evidence_pack is not None and not isinstance(evidence_pack, Mapping)
    pack_schema = str(pack.get("schema_version") or "")
    has_pack_body = isinstance(pack.get("sections"), Mapping)
    unsupported_schema = bool(
        pack_schema and pack_schema != SUPPORTED_PACK_SCHEMA and has_pack_body
    )

    raw_sections: Mapping[str, Any]
    if sections is not None and isinstance(sections, Mapping):
        raw_sections = sections
    elif has_pack_body:
        raw_sections = pack["sections"]
    else:
        raw_sections = {}
    raw_coverage = (
        coverage if isinstance(coverage, Mapping)
        else pack.get("coverage") if isinstance(pack.get("coverage"), Mapping)
        else {}
    )
    pack_errors = pack.get("errors") if isinstance(pack.get("errors"), Mapping) else {}

    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    conflicts = _normalise_declared_conflicts(pack.get("conflicts"))
    subject_codes: set[str] = set()
    pack_code = _bounded_text(pack.get("code"), 40).upper()
    if pack_code:
        subject_codes.add(pack_code)

    if supplied_pack_invalid:
        gaps.append(_gap(
            kind="invalid_input", stage=None, source_id="evidence_pack",
            detail="evidence_pack is not an object", severity="required",
        ))
    if unsupported_schema:
        gaps.append(_gap(
            kind="unsupported_schema", stage=None, source_id="evidence_pack",
            detail=f"expected {SUPPORTED_PACK_SCHEMA}, got {pack_schema}",
            severity="required",
        ))

    section_error_names = {
        str(name) for name in pack_errors if str(name) in _SECTION_POLICY
    }
    for name in sorted(set(raw_sections) | section_error_names):
        policy = _SECTION_POLICY.get(name)
        value = raw_sections.get(name)
        if value is None:
            error = pack_errors.get(name)
            severity = "required" if name in _REQUIRED_SECTIONS else "optional"
            gaps.append(_gap(
                kind="source_failed" if error else "source_missing",
                stage=None, source_id=f"pack.{name}",
                detail=error or f"section {name} has no data", severity=severity,
            ))
            continue
        if not isinstance(value, Mapping):
            gaps.append(_gap(
                kind="invalid_section", stage=None, source_id=f"pack.{name}",
                detail=f"section {name} is not an object",
                severity="required" if name in _REQUIRED_SECTIONS else "optional",
            ))
            continue
        if policy is None:
            # Unknown sections are retained as context but never close a gate.
            policy = ("context", ("risk",), "unknown", 30)
            gaps.append(_gap(
                kind="unclassified_source", stage="risk", source_id=f"pack.{name}",
                detail="unknown pack section retained as context", severity="optional",
            ))
        if name == "newsradar":
            # The legacy pack section has no stable per-item candidate id or
            # human-review receipt.  Preserve it in the immutable acquisition
            # pack, but do not register it as report evidence.  Reviewed rows
            # enter through the workspace NewsRadar projection below.
            gaps.append(_gap(
                kind="industry_review_required", stage=None,
                source_id="pack.newsradar",
                detail=(
                    "legacy NewsRadar rows are excluded until represented by "
                    "workspace candidate ids and confirmed related"
                ),
                severity="optional",
            ))
            continue
        source_type, stage_names, reliability, max_age = policy
        declared_status = str(value.get("status") or "ok").lower()
        no_data = declared_status in _BAD_STATUSES or declared_status in {
            "empty", "not_applicable", "not_configured",
        }
        if no_data and declared_status != "not_applicable":
            gaps.append(_gap(
                kind="source_not_configured" if declared_status == "not_configured" else
                     "source_empty" if declared_status == "empty" else "source_failed",
                stage=None, source_id=f"pack.{name}",
                detail=value.get("note") or f"section {name} status={declared_status}",
                severity="required" if name in _REQUIRED_SECTIONS else "optional",
            ))
        candidates.append(_candidate(
            source_id=_source_name(value, f"pack.{name}"),
            source_type=source_type, stages=stage_names,
            reliability=reliability, max_age_days=max_age, value=value,
            origin=f"pack:{name}", source_ref=f"section:{name}",
            status=declared_status, usable=not no_data,
        ))

    # Coverage can report missing data that was omitted from ``sections``.
    missing = raw_coverage.get("missing") if isinstance(raw_coverage, Mapping) else []
    if isinstance(missing, list):
        for name in missing:
            section_name = str(name)
            if section_name in raw_sections or section_name in pack_errors:
                continue
            gaps.append(_gap(
                kind="source_missing", stage=None, source_id=f"pack.{section_name}",
                detail=f"coverage declares {section_name} missing",
                severity="required" if section_name in _REQUIRED_SECTIONS else "optional",
            ))
    core = raw_coverage.get("company_core") if isinstance(raw_coverage, Mapping) else None
    if isinstance(core, Mapping) and isinstance(core.get("missing"), list):
        for name in core["missing"]:
            section_name = str(name)
            if any(gap.get("source_id") == f"pack.{section_name}" for gap in gaps):
                continue
            gaps.append(_gap(
                kind="source_missing", stage=None, source_id=f"pack.{section_name}",
                detail=f"company_core declares {section_name} missing", severity="required",
            ))
    for name, error in pack_errors.items():
        if name in raw_sections or name in _SECTION_POLICY:
            continue
        gaps.append(_gap(
            kind="source_failed", stage=None, source_id=f"pack.{name}",
            detail=error, severity="required" if str(name).startswith("_") else "optional",
        ))

    snapshot_records: Iterable[Any]
    if isinstance(workspace_snapshots, Mapping):
        snapshot_records = (workspace_snapshots,)
    else:
        snapshot_records = workspace_snapshots or ()
    for index, raw in enumerate(snapshot_records):
        snapshot, metadata, ref = _snapshot_record(raw)
        if not snapshot:
            gaps.append(_gap(
                kind="invalid_snapshot", stage=None,
                source_id=f"workspace.snapshot.{index}",
                detail="workspace snapshot is not an object", severity="optional",
            ))
            continue
        dataset_id = _bounded_text(
            snapshot.get("dataset_id") or metadata.get("dataset_id"), 160,
        ) or f"workspace.unknown.{index}"
        subject_code = _subject_code(metadata)
        if subject_code:
            subject_codes.add(subject_code)
        source_type, stage_names, reliability, max_age = _WORKSPACE_POLICY.get(
            dataset_id, ("context", ("risk",), "unknown", 30),
        )
        if dataset_id == "research.sec_filings_xbrl.live" \
                and not _sec_is_bound_issuer(snapshot, metadata):
            source_type = "context"
        reliability = _authority_reliability(snapshot, reliability)
        status = str(
            snapshot.get("status") or metadata.get("upstream_status") or "unknown"
        ).lower()
        rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else None
        ok_flag = snapshot.get("ok")
        bad = status in _BAD_STATUSES or ok_flag is False or rows is None
        empty = rows == []
        provider_failures = [
            item for item in (snapshot.get("sources") or [])
            if isinstance(item, Mapping)
            and str(item.get("status") or "ok").lower() not in _GOOD_STATUSES
        ] if isinstance(snapshot.get("sources"), list) else []
        candidate = _candidate(
            source_id=dataset_id, source_type=source_type, stages=stage_names,
            reliability=reliability, max_age_days=max_age, value=snapshot,
            origin="workspace", source_ref=ref or f"dataset:{dataset_id}",
            status=status, usable=not bad,
            supports_required=(not bad and not empty),
            authorities=_provider_authorities(snapshot),
        )
        candidates.append(candidate)
        if bad:
            detail = "; ".join(candidate["warnings"]) or f"upstream status {status}"
            gaps.append(_gap(
                kind="source_failed", stage=None, source_id=dataset_id,
                detail=detail, severity="required" if source_type == "issuer" else "optional",
            ))
        elif empty:
            review_gate = (
                snapshot.get("industry_review_gate")
                if dataset_id == "research.newsradar.live"
                and isinstance(snapshot.get("industry_review_gate"), Mapping)
                else None
            )
            candidate_counts = (
                review_gate.get("candidate_counts")
                if isinstance(review_gate, Mapping)
                and isinstance(review_gate.get("candidate_counts"), Mapping)
                else {}
            )
            def review_count(key: str) -> int:
                try:
                    return max(0, int(candidate_counts.get(key) or 0))
                except (TypeError, ValueError, OverflowError):
                    return 0

            raw_candidate_count = review_count("total")
            if raw_candidate_count:
                pending_count = sum(
                    review_count(key)
                    for key in ("unreviewed", "uncertain")
                )
                unrelated_count = review_count("unrelated")
                gaps.append(_gap(
                    kind="industry_news_review_pending",
                    stage=None,
                    source_id=dataset_id,
                    detail=(
                        f"{raw_candidate_count} industry candidates were fetched; "
                        f"{pending_count} remain unreviewed/uncertain, "
                        f"{unrelated_count} are unrelated, and 0 are "
                        "user-confirmed related/report-eligible. Candidates remain "
                        "industry context and never become issuer facts."
                    ),
                    severity="optional",
                ))
            else:
                gaps.append(_gap(
                    kind="source_empty", stage=None, source_id=dataset_id,
                    detail="query completed but returned no rows",
                    severity="required" if source_type == "issuer" else "optional",
                ))
        if provider_failures:
            names = [
                item.get("authority") or item.get("name") or "unknown provider"
                for item in provider_failures
            ]
            gaps.append(_gap(
                kind="upstream_partial", stage=None, source_id=dataset_id,
                detail="failed providers: " + ", ".join(map(str, names)),
                severity="required" if source_type == "issuer" else "optional",
            ))
        if dataset_id not in _WORKSPACE_POLICY:
            gaps.append(_gap(
                kind="unclassified_source", stage="risk", source_id=dataset_id,
                detail="unknown workspace dataset retained as context", severity="optional",
            ))

    reference_at = _reference_time(as_of, pack, candidates)

    # Deduplicate exact snapshots, version repeated source ids, and expose
    # contradictory same-period bodies as explicit conflicts.
    unique_candidates: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_source[candidate["base_source_id"]].append(candidate)
    for base_source_id in sorted(by_source):
        by_digest: dict[str, dict[str, Any]] = {}
        for candidate in by_source[base_source_id]:
            existing = by_digest.get(candidate["content_digest"])
            if existing is None:
                by_digest[candidate["content_digest"]] = candidate
            else:
                existing["stages"] = tuple(sorted(set(existing["stages"]) | set(candidate["stages"])))
                existing["authorities"] = sorted(set(existing["authorities"]) | set(candidate["authorities"]))
        group = sorted(
            by_digest.values(),
            key=lambda item: (str(item.get("as_of") or ""), item["content_digest"]),
        )
        if len(group) > 1:
            observation_groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
            for candidate in group:
                observation_groups[(
                    candidate.get("as_of"), candidate.get("period"), candidate.get("unit"),
                )].append(candidate)
            for observation_key, observed in observation_groups.items():
                if len(observed) > 1:
                    conflicts.append(_conflict(
                        kind="conflicting_snapshots", stage=None,
                        source_ids=[base_source_id],
                        detail=(
                            f"{len(observed)} different bodies share observation "
                            f"{observation_key}"
                        ),
                        severity="required" if any(
                            item["type"] == "issuer" for item in observed
                        ) else "optional",
                    ))
        for candidate in group:
            candidate["source_id"] = (
                base_source_id if len(group) == 1
                else f"{base_source_id}#{candidate['content_digest'][:8]}"
            )
            candidate["upstream_status"] = candidate["status"]
            candidate["future"] = _future_at(candidate.get("as_of"), reference_at)
            if candidate["future"] is True:
                # Future evidence is unavailable at this run's point-in-time
                # boundary even when the provider asserted ``stale=false``.
                candidate["status"] = "future"
                candidate["usable"] = False
                candidate["supports_required"] = False
                candidate["stale"] = None
                reference_iso = reference_at.isoformat().replace("+00:00", "Z") \
                    if reference_at else "unknown"
                gaps.append(_gap(
                    kind="future_source", stage=None,
                    source_id=candidate["source_id"],
                    detail=(
                        f"content {candidate.get('as_of') or 'unknown'} is later than "
                        f"hub reference {reference_iso}"
                    ),
                    severity="required" if candidate["type"] == "issuer" else "optional",
                ))
            else:
                candidate["stale"] = _stale_at(
                    candidate["value"], candidate.get("as_of"), reference_at,
                    int(candidate["max_age_days"]),
                )
            identity = {
                "source_id": candidate["source_id"],
                "type": candidate["type"],
                "as_of": candidate.get("as_of"),
                "period": candidate.get("period"),
                "unit": candidate.get("unit"),
                "content_digest": candidate["content_digest"],
            }
            candidate["evidence_id"] = (
                "CE-" + _digest(identity)[:16].upper() if candidate["usable"] else None
            )
            unique_candidates.append(candidate)

    unique_candidates.sort(key=lambda item: item["source_id"])

    if len(subject_codes) > 1:
        conflicts.append(_conflict(
            kind="subject_mismatch", stage=None, source_ids=sorted(subject_codes),
            detail="inputs are bound to different workspace subjects", severity="required",
        ))

    evidence_ids: dict[str, list[str]] = {stage: [] for stage in STAGES}
    issuer_by_stage: dict[str, list[dict[str, Any]]] = {stage: [] for stage in REQUIRED_INPUT_STAGES}
    context_ids: list[str] = []
    issuer_ids: list[str] = []
    for candidate in unique_candidates:
        evidence_id = candidate.get("evidence_id")
        if not evidence_id:
            continue
        if candidate["type"] == "issuer":
            issuer_ids.append(evidence_id)
        else:
            context_ids.append(evidence_id)
        for stage in candidate["stages"]:
            evidence_ids[stage].append(evidence_id)
            if stage in issuer_by_stage and candidate["type"] == "issuer" \
                    and candidate["supports_required"]:
                issuer_by_stage[stage].append(candidate)
    all_ids = sorted(set(issuer_ids) | set(context_ids))
    evidence_ids["report"] = all_ids
    for stage in STAGES:
        evidence_ids[stage] = sorted(set(evidence_ids[stage]))

    missing_stages: list[str] = []
    stale_stages: list[str] = []
    unknown_freshness_stages: list[str] = []
    for stage in REQUIRED_INPUT_STAGES:
        sources = issuer_by_stage[stage]
        if not sources:
            missing_stages.append(stage)
            gaps.append(_gap(
                kind="required_stage_missing", stage=stage, source_id=None,
                detail=f"{stage} has no issuer evidence", severity="required",
            ))
            continue
        if any(source.get("stale") is False for source in sources):
            continue
        if any(source.get("stale") is None for source in sources):
            unknown_freshness_stages.append(stage)
            gaps.append(_gap(
                kind="freshness_unknown", stage=stage, source_id=None,
                detail=f"{stage} issuer evidence has no comparable content date",
                severity="required",
            ))
        else:
            stale_stages.append(stage)
            gaps.append(_gap(
                kind="required_stage_stale", stage=stage, source_id=None,
                detail=f"{stage} is covered only by stale issuer evidence",
                severity="required",
            ))

    # Per-source freshness gaps are useful for diagnostics, even when another
    # fresh source safely closes the same stage.
    for candidate in unique_candidates:
        if candidate.get("stale") is True:
            gaps.append(_gap(
                kind="stale_source", stage=None, source_id=candidate["source_id"],
                detail=(
                    f"content {candidate.get('as_of') or 'unknown'} exceeds "
                    f"{candidate['max_age_days']} day policy"
                ),
                severity="required" if candidate["type"] == "issuer" else "optional",
            ))
        elif candidate.get("stale") is None and candidate["usable"]:
            gaps.append(_gap(
                kind="freshness_unknown", stage=None, source_id=candidate["source_id"],
                detail="source has no comparable as_of/reference time",
                severity="required" if candidate["type"] == "issuer" else "optional",
            ))

    required_conflicts = [
        item for item in conflicts if item.get("severity") == "required"
    ]
    if not any(candidate["usable"] for candidate in unique_candidates):
        status = "failed"
    elif missing_stages or unknown_freshness_stages or required_conflicts \
            or supplied_pack_invalid or unsupported_schema:
        status = "incomplete"
    elif stale_stages:
        status = "stale"
    else:
        status = "complete"

    stage_status: dict[str, str] = {}
    for stage in REQUIRED_INPUT_STAGES:
        if stage in missing_stages or stage in unknown_freshness_stages:
            stage_status[stage] = "incomplete"
        elif stage in stale_stages:
            stage_status[stage] = "stale"
        else:
            stage_status[stage] = "complete"
    stage_status["report"] = (
        "complete" if status == "complete" else
        "stale" if status == "stale" else
        "failed" if status == "failed" else "incomplete"
    )

    source_register = [
        {
            "source_id": candidate["source_id"],
            "type": candidate["type"],
            "as_of": candidate.get("as_of"),
            "period": candidate.get("period"),
            "unit": candidate.get("unit"),
            "reliability": candidate["reliability"],
            "stale": candidate.get("stale"),
            "future": candidate.get("future"),
            "status": candidate["status"],
            "upstream_status": candidate.get("upstream_status"),
            "usable": bool(candidate["usable"]),
            "evidence_id": candidate.get("evidence_id"),
            "stages": list(candidate["stages"]),
            "source_ref": candidate["source_ref"],
            "record_count": candidate.get("record_count"),
            "authorities": candidate["authorities"],
            "content_sha256": candidate["content_digest"],
            "warnings": candidate["warnings"],
        }
        for candidate in unique_candidates
    ]

    # Stable ordering and id-based deduplication keep the whole output replayable.
    gaps = sorted(
        {item["gap_id"]: item for item in gaps}.values(),
        key=lambda item: item["gap_id"],
    )
    conflicts = sorted(
        {item["conflict_id"]: item for item in conflicts}.values(),
        key=lambda item: item["conflict_id"],
    )
    reference_iso = (
        reference_at.isoformat().replace("+00:00", "Z") if reference_at else None
    )
    observed_codes = sorted(subject_codes)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "as_of": reference_iso,
        "subject": {
            "code": observed_codes[0] if len(observed_codes) == 1 else None,
            "observed_codes": observed_codes,
        },
        "source_register": source_register,
        "evidence_ids": evidence_ids,
        "evidence_scope": {
            "issuer": sorted(set(issuer_ids)),
            "context": sorted(set(context_ids)),
        },
        "stage_status": stage_status,
        "gaps": gaps,
        "conflicts": conflicts,
        "summary": {
            "sources": len(source_register),
            "usable_sources": sum(1 for item in unique_candidates if item["usable"]),
            "issuer_evidence": len(set(issuer_ids)),
            "context_evidence": len(set(context_ids)),
            "missing_stages": missing_stages,
            "stale_stages": stale_stages,
            "unknown_freshness_stages": unknown_freshness_stages,
            "future_sources": [
                item["source_id"] for item in unique_candidates
                if item.get("future") is True
            ],
            "future_issuer_sources": [
                item["source_id"] for item in unique_candidates
                if item.get("future") is True and item["type"] == "issuer"
            ],
        },
    }


__all__ = [
    "SCHEMA_VERSION", "STAGES", "SUPPORTED_PACK_SCHEMA",
    "build_company_research_hub",
]
