"""Pure adapter from company evidence to workspace research artifacts.

The Console layer owns fetching, workspace binding and persistence.  This
module performs the deterministic middle of the path:

    evidence-pack + verified live snapshots
      -> Evidence Hub
      -> six-stage gate
      -> bounded report/manifest/events/calculations/conflicts documents

It intentionally produces a *research readiness bundle*.  The final narrative
report is still written by the existing financial-analyst swarm; callers must
not present this deterministic bundle as an analyst opinion.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, time
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from guanlan_v2.reports import evidence as report_evidence
from guanlan_v2.research.company_hub import STAGES, build_company_research_hub
from guanlan_v2.research.company_core_contracts import (
    evaluate_estimates_contract,
    evaluate_financials_contract,
    evaluate_profile_contract,
)
from guanlan_v2.research.company_pipeline import CompanyResearchPipeline
from guanlan_v2.research.company_risk_gate import (
    build_company_risk_contract,
    risk_stage_evidence_from_contract,
)
from guanlan_v2.research.company_valuation import build_valuation_dag_from_evidence
from guanlan_v2.research.company_vibe_valuation import build_vibe_valuation_contract


SCHEMA_VERSION = "company-research-workspace@1"
_PACK_SCHEMA_VERSION = "company-research-evidence@2"
_MANIFEST_SCHEMA_VERSION = "report-evidence-manifest/v1"
_HONG_KONG = ZoneInfo("Asia/Hong_Kong")
_SOURCE_ORDER = (
    "research.trade_disclosure_calendar.live",
    "research.tw_monthly_revenue.live",
    "research.cn_commodity_futures.live",
    "research.control_lists.live",
    "research.sec_filings_xbrl.live",
    "research.finra_short_volume.live",
    "research.us_treasury.live",
    "research.cftc_cot.live",
    "research.overseas_earnings_calendar.live",
    "research.newsradar.live",
)


def _hk_today() -> date:
    return datetime.now(_HONG_KONG).date()


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value:
            return None
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _digest(value: Any) -> str:
    body = json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Bound prompt-facing live evidence without changing the durable ledger."""
    if depth > 5:
        return "[depth truncated]"
    if isinstance(value, str):
        return value[:1500]
    if value is None or isinstance(value, (int, float, bool)):
        return _json_safe(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                out["_truncated_fields"] = len(value) - 80
                break
            out[str(key)] = _bounded(item, depth=depth + 1)
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows = list(value)
        out = [_bounded(item, depth=depth + 1) for item in rows[:20]]
        if len(rows) > 20:
            out.append({"_truncated_rows": len(rows) - 20})
        return out
    return _text(value, 1500)


def _snapshot_record(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}, {}
    snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), Mapping) else raw
    metadata = (
        raw.get("asset") if isinstance(raw.get("asset"), Mapping)
        else raw.get("metadata") if isinstance(raw.get("metadata"), Mapping)
        else raw
    )
    return dict(snapshot), dict(metadata)


def _normalise_as_of(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("as_of must be a valid YYYY-MM-DD date") from exc
    if parsed > _hk_today():
        raise ValueError(f"as_of {parsed.isoformat()} is in the future")
    return parsed.isoformat()


def timestamp_not_after_asof(value: Any, requested_asof: str) -> bool:
    if value in (None, ""):
        return False
    try:
        cutoff = datetime.combine(
            date.fromisoformat(requested_asof), time.max, tzinfo=_HONG_KONG,
        )
        text = str(value).strip()
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        local = parsed.replace(tzinfo=_HONG_KONG) if parsed.tzinfo is None \
            else parsed.astimezone(_HONG_KONG)
        return local <= cutoff
    except (TypeError, ValueError):
        return False


def _time_contract_date(value: Any, requested_asof: str) -> str | None:
    """Return a bounded ISO date without treating retrieval time as market time."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return parsed.isoformat() if parsed <= date.fromisoformat(requested_asof) else None


def _time_contract_timestamp(value: Any, requested_asof: str) -> str | None:
    """Return a timezone-qualified timestamp at/before the research day cutoff."""
    text = str(value or "").strip()
    if not text or len(text) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    local = parsed.astimezone(_HONG_KONG)
    cutoff = datetime.combine(
        date.fromisoformat(requested_asof), time.max, tzinfo=_HONG_KONG,
    )
    return parsed.isoformat() if local <= cutoff else None


def build_company_time_contract(
    evidence_pack: Mapping[str, Any], *, research_asof: str,
) -> dict[str, Any]:
    """Build the fail-closed dual-time contract for a company research run.

    ``research_asof`` is a user/query boundary, not evidence that a retained
    quote belongs to that exchange session.  A market date is therefore
    admitted only from the provider-attested ``quote_live.market_data_as_of``
    field or from a quote receipt whose identity matches the quote payload.
    Likewise, an information cutoff is never inferred from ``research_asof``
    or ``cutoff_as_of``; only an explicit, timezone-qualified cutoff is used.
    """
    requested_asof = _normalise_as_of(research_asof)
    pack = evidence_pack if isinstance(evidence_pack, Mapping) else {}
    sections = pack.get("sections") if isinstance(pack.get("sections"), Mapping) else {}
    quote = sections.get("quote_live") if isinstance(sections.get("quote_live"), Mapping) else {}
    receipts = pack.get("receipts") if isinstance(pack.get("receipts"), Mapping) else {}
    receipt = receipts.get("quote_live") if isinstance(receipts.get("quote_live"), Mapping) else {}

    direct_date: str | None = None
    attestation = (
        quote.get("market_data_attestation")
        if isinstance(quote.get("market_data_attestation"), Mapping) else {}
    )
    raw_direct_date = quote.get("market_data_as_of")
    if (
        raw_direct_date not in (None, "")
        and str(attestation.get("market_data_as_of") or "") == str(raw_direct_date)
        and str(attestation.get("source") or "").strip()
        and str(attestation.get("validation") or "").strip()
    ):
        direct_date = _time_contract_date(raw_direct_date, requested_asof)

    receipt_date: str | None = None
    receipt_identity_ok = bool(
        quote
        and receipt.get("source") == "quote_live"
        and receipt.get("status") == "available"
        and receipt.get("requested_asof") == requested_asof
        and str(receipt.get("content_sha256") or "") == _digest(quote)
    )
    if receipt_identity_ok:
        receipt_date = _time_contract_date(receipt.get("data_asof"), requested_asof)

    verified_dates = {value for value in (direct_date, receipt_date) if value}
    if len(verified_dates) == 1:
        market_data_asof = next(iter(verified_dates))
        market_data_source_ref = (
            "pack.sections.quote_live.market_data_as_of"
            if direct_date else "pack.receipts.quote_live.data_asof"
        )
    else:
        # No evidence, or two independently admitted fields disagree: neither
        # case may silently become the requested research date.
        market_data_asof = None
        market_data_source_ref = None

    retrieved_at = _time_contract_timestamp(
        quote.get("retrieved_at"), requested_asof,
    )
    if retrieved_at is None and receipt_identity_ok:
        retrieved_at = _time_contract_timestamp(
            receipt.get("pulled_at"), requested_asof,
        )

    information_cutoff = _time_contract_timestamp(
        pack.get("information_cutoff"), requested_asof,
    )
    status = (
        "incomplete" if market_data_asof is None
        else "partial" if information_cutoff is None or retrieved_at is None
        else "complete"
    )
    return {
        "research_asof": requested_asof,
        "market_data_asof": market_data_asof,
        "market_data_source_ref": market_data_source_ref,
        "retrieved_at": retrieved_at,
        "information_cutoff": information_cutoff,
        "status": status,
    }


def _date_not_after(value: Any, requested_asof: str) -> bool:
    return timestamp_not_after_asof(value, requested_asof)


def _manifest_cache_key(code: str, asof: str, mode: str) -> str:
    return _digest({
        "source": "platform-evidence",
        "schema_version": _PACK_SCHEMA_VERSION,
        "code": code,
        "asof": asof,
        "mode": mode,
    })


def _require_historical_manifest(
    pack: Mapping[str, Any], *, requested_asof: str, expected_code: str = "",
) -> None:
    """Reject an unsigned/stale base before trusted enrichment can bless it."""
    pack_code = str(pack.get("code") or "").strip().upper()
    pack_asof = str(pack.get("asof") or pack.get("cutoff_as_of") or "").strip()
    required = {
        "schema_version": pack.get("schema_version"),
        "manifest_schema_version": pack.get("manifest_schema_version"),
        "code": pack_code,
        "asof": pack_asof,
        "mode": pack.get("mode"),
        "cache_key": pack.get("cache_key"),
        "manifest_digest": pack.get("manifest_digest"),
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError(
            "historical evidence manifest identity missing: " + ", ".join(missing)
        )
    if pack.get("schema_version") != _PACK_SCHEMA_VERSION:
        raise ValueError("historical evidence pack schema mismatch")
    if pack.get("manifest_schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise ValueError("historical evidence manifest schema mismatch")
    if pack.get("mode") != "historical":
        raise ValueError("historical evidence manifest mode mismatch")
    if pack_asof != requested_asof:
        raise ValueError(
            f"historical evidence asof mismatch: requested={requested_asof}, pack={pack_asof}"
        )
    if expected_code and pack_code != str(expected_code).strip().upper():
        raise ValueError(
            f"historical evidence code mismatch: requested={expected_code}, pack={pack_code}"
        )
    if pack.get("cache_key") != _manifest_cache_key(pack_code, pack_asof, "historical"):
        raise ValueError("historical evidence manifest cache_key mismatch")
    unsigned = dict(pack)
    expected_digest = unsigned.pop("manifest_digest")
    if _digest(unsigned) != expected_digest:
        raise ValueError("historical evidence manifest digest mismatch")
    if not report_evidence._verify_evidence_manifest_auth(pack):
        raise ValueError(
            "historical evidence manifest authentication failed or trusted key unavailable"
        )


def _historical_receipt_reason(
    source: str, section: Any, receipt: Any, requested_asof: str,
) -> str:
    if not isinstance(receipt, Mapping):
        return "snapshot receipt missing"
    if receipt.get("source") != source:
        return "snapshot receipt source mismatch"
    if not report_evidence._verify_historical_receipt_auth(dict(receipt)):
        return "snapshot receipt authentication failed or trusted key unavailable"
    if receipt.get("status") != "available":
        return str(receipt.get("reason") or "source marked unavailable")
    if receipt.get("requested_asof") != requested_asof:
        return "snapshot receipt requested_asof mismatch"
    if receipt.get("point_in_time") is not True \
            or receipt.get("historical_version_proven") is not True:
        return "historical acquisition version is not proven"
    if not _date_not_after(receipt.get("data_asof"), requested_asof):
        return "snapshot receipt data_asof missing, invalid, or after requested asof"
    pulled_at = receipt.get("pulled_at")
    pulled_present = pulled_at not in (None, "")
    pulled_ok = pulled_present and _date_not_after(pulled_at, requested_asof)
    version_ok = bool(
        str(receipt.get("snapshot_version") or "").strip()
        and receipt.get("snapshot_immutable") is True
        and _date_not_after(receipt.get("snapshot_asof"), requested_asof)
    )
    if not (pulled_ok if pulled_present else version_ok):
        return "receipt lacks pulled_at or immutable snapshot version at/before requested asof"
    content_sha = str(receipt.get("content_sha256") or "")
    if not content_sha or content_sha != _digest(section):
        return "snapshot receipt content identity mismatch"
    if not receipt.get("cache_key"):
        return "snapshot receipt cache identity missing"
    return ""


def _historical_unavailable_receipt_reason(
    source: str, receipt: Any, requested_asof: str,
) -> str:
    if not isinstance(receipt, Mapping):
        return "snapshot receipt missing for unavailable section"
    if receipt.get("source") != source:
        return "snapshot receipt source mismatch"
    if not report_evidence._verify_historical_receipt_auth(dict(receipt)):
        return "snapshot receipt authentication failed or trusted key unavailable"
    if receipt.get("status") != "unavailable":
        return "historical section content missing despite available receipt"
    if receipt.get("requested_asof") != requested_asof:
        return "snapshot receipt requested_asof mismatch"
    return ""


def _unavailable_receipt(
    source: str, code: str, requested_asof: str, *, reason: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "status": "unavailable",
        "requested_asof": requested_asof,
        "data_asof": None,
        "pulled_at": None,
        "snapshot_version": None,
        "snapshot_asof": None,
        "snapshot_immutable": False,
        "content_sha256": None,
        "cache_key": _digest({
            "source": source, "code": code, "asof": requested_asof,
            "status": "unavailable", "reason": reason,
        }),
        "point_in_time": False,
        "historical_version_proven": False,
        "proof": None,
        "reason": reason,
    }


def _refresh_historical_manifest(pack: dict[str, Any]) -> None:
    sections = pack.get("sections") if isinstance(pack.get("sections"), dict) else {}
    coverage = dict(pack.get("coverage") or {})
    core = dict(coverage.get("company_core") or {})
    required = [str(name) for name in core.get("required") or []]
    present = [name for name in sections if sections.get(name) is not None]
    missing = [name for name in sections if sections.get(name) is None]
    core_missing = [name for name in required if sections.get(name) is None]
    coverage.update({
        "present": present,
        "missing": missing,
        "company_core": {
            **core,
            "required": required,
            "missing": core_missing,
            "status": "complete" if not core_missing else "incomplete",
        },
    })
    pack["coverage"] = coverage
    pack["sections_ok"] = list(dict.fromkeys([
        *[str(name) for name in pack.get("sections_ok") or []],
        *sections.keys(),
    ]))
    receipts = dict(pack.get("receipts") or {})
    for source, raw_receipt in receipts.items():
        if not isinstance(raw_receipt, Mapping):
            raise ValueError(f"historical evidence receipt {source} is not a mapping")
        signed = report_evidence._sign_historical_receipt(dict(raw_receipt))
        if not isinstance(signed.get("auth"), dict):
            raise ValueError("trusted historical signing key unavailable")
        receipts[source] = signed
    pack["receipts"] = receipts
    report_evidence._seal_evidence_manifest(pack, historical=True)
    if not isinstance(pack.get("manifest_auth"), dict):
        raise ValueError("trusted historical signing key unavailable")


def _scrub_historical_pack(
    pack: dict[str, Any], *, requested_asof: str, expected_code: str,
) -> dict[str, Any]:
    _require_historical_manifest(
        pack, requested_asof=requested_asof, expected_code=expected_code,
    )
    sections = pack.get("sections") if isinstance(pack.get("sections"), dict) else {}
    receipts = dict(pack.get("receipts") or {})
    errors = dict(pack.get("errors") or {})
    unavailable = [
        dict(item) for item in pack.get("unavailable_sources") or []
        if isinstance(item, Mapping)
    ]
    changed = False
    for source in sorted(set(sections) | set(receipts), key=str):
        section = sections.get(source)
        if section is None:
            reason = _historical_unavailable_receipt_reason(
                str(source), receipts.get(source), requested_asof,
            )
        else:
            reason = _historical_receipt_reason(
                str(source), section, receipts.get(source), requested_asof,
            )
        if not reason:
            continue
        changed = True
        sections[source] = None
        receipts[source] = _unavailable_receipt(
            str(source), expected_code, requested_asof, reason=reason,
        )
        errors.setdefault(str(source), reason)
        unavailable = [
            item for item in unavailable if str(item.get("source") or "") != str(source)
        ]
        unavailable.append({"source": str(source), "reason": reason})
    pack["sections"] = sections
    pack["receipts"] = receipts
    pack["errors"] = errors
    pack["unavailable_sources"] = unavailable
    if changed:
        _refresh_historical_manifest(pack)
    return pack


def _historical_snapshot_reason(
    raw: Any, *, requested_asof: str, expected_code: str,
) -> str:
    snapshot, metadata = _snapshot_record(raw)
    asset_id = str(metadata.get("id") or "").strip()
    workspace_id = str(metadata.get("workspace_id") or "").strip()
    dataset_id = str(snapshot.get("dataset_id") or metadata.get("dataset_id") or "").strip()
    payload_sha = str(metadata.get("payload_sha256") or "").strip()
    if not asset_id or not workspace_id or not dataset_id or not payload_sha:
        return "workspace snapshot lacks immutable asset/content sidecar"
    payload = {
        "asset_id": asset_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "snapshot": snapshot,
    }
    if _digest(payload) != payload_sha:
        return "workspace snapshot payload digest mismatch"
    trust = report_evidence._trusted_receipts_module()
    auth_payload = trust.workspace_snapshot_auth_payload(
        asset_id=asset_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        payload_sha256=payload_sha,
        acquired_at=metadata.get("acquired_at"),
        pulled_at=metadata.get("pulled_at") or snapshot.get("pulled_at"),
        as_of=metadata.get("as_of") or snapshot.get("as_of"),
        source=metadata.get("source"),
        subject=metadata.get("subject"),
        asset_version=metadata.get("version"),
    )
    if not trust.verify_payload(
        trust.WORKSPACE_SNAPSHOT_DOMAIN,
        auth_payload,
        metadata.get("snapshot_auth"),
    ):
        return "workspace snapshot authentication failed or trusted key unavailable"
    snapshot_code = str((metadata.get("subject") or {}).get("code") or "").strip().upper() \
        if isinstance(metadata.get("subject"), Mapping) else ""
    expected = str(expected_code).strip().upper()
    if not snapshot_code or not expected or snapshot_code != expected:
        return "workspace snapshot belongs to another subject"
    acquired_at = metadata.get("acquired_at")
    if not _date_not_after(acquired_at, requested_asof):
        return "workspace snapshot was not acquired at/before requested asof"
    return ""


def _filter_historical_snapshots(
    snapshots: Sequence[Any], *, requested_asof: str, expected_code: str,
) -> tuple[list[Any], list[dict[str, str]]]:
    visible: list[Any] = []
    unavailable: list[dict[str, str]] = []
    for raw in snapshots:
        snapshot, metadata = _snapshot_record(raw)
        dataset_id = str(snapshot.get("dataset_id") or metadata.get("dataset_id") or "unknown")
        asset_id = str(metadata.get("id") or "unversioned")
        reason = _historical_snapshot_reason(
            raw, requested_asof=requested_asof, expected_code=expected_code,
        )
        if reason:
            # A signed snapshot acquired after the requested date is safely
            # invisible.  Missing/forged authentication or identity is a hard
            # trust-boundary failure: do not mint authenticated enrichments
            # from a batch containing that input.
            if reason != "workspace snapshot was not acquired at/before requested asof":
                raise ValueError(
                    f"historical workspace snapshot {dataset_id}:{asset_id} "
                    f"is untrusted: {reason}"
                )
            # A post-cutoff asset is indistinguishable from an asset that does
            # not exist at the requested point in time.  Do not emit its
            # dataset, identifier, count, or any other inventory metadata into
            # a historical pack.
            continue
        else:
            visible.append(raw)
    return visible, unavailable


def enrich_historical_evidence_pack(
    evidence_pack: Mapping[str, Any], enrichments: Mapping[str, Any], *,
    proof: str = "deterministic_derivation_from_verified_pit_inputs",
    proof_context: Any = None,
) -> dict[str, Any]:
    """Add trusted deterministic sections and re-sign the complete manifest.

    The input manifest must still verify before enrichment.  This prevents a
    stale digest, an unsigned live section, or a future snapshot from being
    laundered into a valid historical pack by this adapter.
    """
    pack = _json_safe(copy.deepcopy(dict(evidence_pack or {})))
    requested_asof = _normalise_as_of(pack.get("asof") or pack.get("cutoff_as_of"))
    code = str(pack.get("code") or "").strip().upper()
    _require_historical_manifest(
        pack, requested_asof=requested_asof, expected_code=code,
    )
    sections = pack.get("sections") if isinstance(pack.get("sections"), dict) else {}
    receipts = dict(pack.get("receipts") or {})
    for source in sorted(set(sections) | set(receipts), key=str):
        section = sections.get(source)
        if section is None:
            reason = _historical_unavailable_receipt_reason(
                str(source), receipts.get(source), requested_asof,
            )
        else:
            reason = _historical_receipt_reason(
                str(source), section, receipts.get(source), requested_asof,
            )
        if reason:
            raise ValueError(f"historical evidence section {source} is unverified: {reason}")

    parent_digest = str(pack.get("manifest_digest") or "")
    context_digest = _digest(proof_context or {})
    errors = dict(pack.get("errors") or {})
    unavailable = [
        dict(item) for item in pack.get("unavailable_sources") or []
        if isinstance(item, Mapping)
    ]
    for raw_source, raw_section in enrichments.items():
        source = str(raw_source)
        section = _json_safe(copy.deepcopy(raw_section))
        sections[source] = section
        unavailable = [
            item for item in unavailable if str(item.get("source") or "") != source
        ]
        if section is None:
            reason = "deterministic historical enrichment unavailable"
            receipts[source] = _unavailable_receipt(
                source, code, requested_asof, reason=reason,
            )
            errors.setdefault(source, reason)
            unavailable.append({"source": source, "reason": reason})
            continue
        content_sha = _digest(section)
        version = _digest({
            "derivation_schema": SCHEMA_VERSION,
            "parent_manifest_digest": parent_digest,
            "source": source,
            "content_sha256": content_sha,
            "proof": proof,
            "proof_context_sha256": context_digest,
        })
        receipts[source] = {
            "source": source,
            "status": "available",
            "requested_asof": requested_asof,
            "data_asof": requested_asof,
            "pulled_at": None,
            "snapshot_version": version,
            "snapshot_asof": requested_asof,
            "snapshot_immutable": True,
            "content_sha256": content_sha,
            "cache_key": _digest({
                "source": source,
                "code": code,
                "asof": requested_asof,
                "version": version,
                "content_sha256": content_sha,
            }),
            "point_in_time": True,
            "historical_version_proven": True,
            "proof": proof,
            "reason": None,
            "lineage": {
                "parent_manifest_digest": parent_digest,
                "proof_context_sha256": context_digest,
            },
        }
        errors.pop(source, None)
    pack["sections"] = sections
    pack["receipts"] = receipts
    pack["errors"] = errors
    pack["unavailable_sources"] = unavailable
    _refresh_historical_manifest(pack)
    return pack


def _source_map(hub: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("source_id")): dict(row)
        for row in hub.get("source_register") or []
        if isinstance(row, Mapping) and row.get("source_id")
    }


def _matching_ids(
    hub: Mapping[str, Any], stage: str, *, issuer_only: bool = False,
    preferred: Sequence[str] = (),
) -> list[str]:
    allowed = set(hub.get("evidence_ids", {}).get(stage) or [])
    issuer = set(hub.get("evidence_scope", {}).get("issuer") or [])
    rows = [
        row for row in hub.get("source_register") or []
        if isinstance(row, Mapping) and row.get("evidence_id") in allowed
        and (not issuer_only or row.get("evidence_id") in issuer)
    ]
    rank = {name: index for index, name in enumerate(preferred)}
    rows.sort(key=lambda row: (
        rank.get(str(row.get("source_id")), len(rank)),
        str(row.get("source_id") or ""),
    ))
    return [str(row["evidence_id"]) for row in rows]


def pipeline_input_from_hub(hub: Mapping[str, Any]) -> dict[str, Any]:
    """Translate semantic Hub coverage into the fixed six-stage gate slots."""
    stage_status = hub.get("stage_status") if isinstance(hub.get("stage_status"), Mapping) else {}
    source_by_id = _source_map(hub)

    profile_ids = _matching_ids(
        hub, "profile", issuer_only=True,
        preferred=("pack.company_profile", "pack.company_facts", "cninfo_announcements"),
    )
    quote_ids = [
        str(row.get("evidence_id"))
        for row in source_by_id.values()
        if row.get("evidence_id") and str(row.get("source_id")) == "pack.quote_live"
    ]
    calendar_ids = [
        str(row.get("evidence_id"))
        for row in source_by_id.values()
        if row.get("evidence_id")
        and str(row.get("source_id")).startswith("research.trade_disclosure_calendar.live")
        and int(row.get("record_count") or 0) > 0
    ]
    financial_ids = _matching_ids(
        hub, "financials", issuer_only=True,
        preferred=("financials_pit_loader", "research.sec_filings_xbrl.live"),
    )
    estimate_ids = _matching_ids(
        hub, "estimates", issuer_only=True,
        preferred=("ths_eps_forecast", "pack.expectations"),
    )
    valuation_ids = _matching_ids(
        hub, "valuation", issuer_only=True,
        preferred=("pack.valuation", "pack.quote_live", "financials_pit_loader"),
    )
    risk_ids = _matching_ids(hub, "risk", issuer_only=True)

    slots: dict[str, list[tuple[str, str]]] = {
        "profile": (
            [("profile", item) for item in profile_ids]
            + [("quote", item) for item in quote_ids]
            + [("trade_calendar", item) for item in calendar_ids]
        ),
        "financials": [("financials", item) for item in financial_ids],
        "estimates": [("estimates", item) for item in estimate_ids],
        # The pipeline's ``quote`` slot means a usable current valuation anchor.
        "valuation": [("quote", item) for item in valuation_ids],
        "risk": [("risk", item) for item in risk_ids],
        "report": [
            ("evidence", str(item))
            for item in hub.get("evidence_ids", {}).get("report") or []
        ],
    }

    out: dict[str, Any] = {}
    for stage in STAGES:
        evidence = [
            {"input_key": input_key, "evidence_id": evidence_id,
             "stale": stage_status.get(stage) == "stale"}
            for input_key, evidence_id in slots.get(stage, [])
        ]
        gaps: list[dict[str, Any]] = []
        status = str(stage_status.get(stage) or "incomplete")
        if status in {"incomplete", "failed"}:
            gaps.append({
                "code": f"evidence_hub_stage_{status}",
                "message": f"Evidence Hub marked {stage} as {status}.",
                "stage": stage,
                "input_key": stage,
                "critical": True,
            })
        for gap in hub.get("gaps") or []:
            if not isinstance(gap, Mapping):
                continue
            if gap.get("stage") not in {None, stage}:
                continue
            if gap.get("severity") == "required":
                continue  # represented once by the stage-level critical gap
            gaps.append({
                "code": str(gap.get("kind") or "evidence_gap"),
                "message": str(gap.get("detail") or "optional evidence gap"),
                "stage": stage,
                "input_key": str(gap.get("source_id") or ""),
                "critical": False,
                "details": {"gap_id": gap.get("gap_id")},
            })
        out[stage] = {
            "evidence": evidence,
            "gaps": gaps,
            "stale": status == "stale",
        }
    return out


def _event(
    kind: str, *, source_id: str, when: Any = None, title: Any = None,
    subject_code: str = "", payload: Any = None, trust: str = "external_untrusted",
) -> dict[str, Any]:
    body = {
        "kind": kind,
        "source_id": source_id,
        "when": _text(when, 80) or None,
        "title": _text(title, 500) or None,
        "subject_code": subject_code or None,
        "trust": trust,
        "payload": _bounded(payload),
    }
    return {"event_id": "CRE-" + _digest(body)[:16].upper(), **body}


def normalise_company_events(
    pack: Mapping[str, Any], snapshots: Iterable[Any], *, subject_code: str,
) -> list[dict[str, Any]]:
    """Normalise event-like evidence into the private run, not global events.db."""
    events: list[dict[str, Any]] = []
    sections = pack.get("sections") if isinstance(pack.get("sections"), Mapping) else {}
    for name, kind in (
        ("announcements", "announcement"),
        ("kuaixun", "company_news"),
        ("newsradar", "industry_signal"),
    ):
        section = sections.get(name)
        if not isinstance(section, Mapping):
            continue
        if name == "newsradar":
            # Legacy pack rows have no candidate id or user-review receipt.
            # The report path consumes only the separately projected workspace
            # NewsRadar snapshot below.
            continue
        rows = section.get("items") if isinstance(section.get("items"), list) else []
        for row in rows[:100]:
            if not isinstance(row, Mapping):
                continue
            if name == "newsradar" and row.get("time_bucket") == "future_source_time":
                continue
            events.append(_event(
                kind, source_id=f"pack.{name}",
                when=(row.get("source_time") or row.get("date") or row.get("time")
                      or row.get("publish_ts")),
                title=row.get("title"),
                # 产业 RSS 只是当前公司研究的背景上下文。没有确定性实体映射时
                # 绝不把它伪绑成该公司的 issuer event（与 Vibe-Research 分工一致）。
                subject_code="" if name == "newsradar" else subject_code,
                payload=row,
                trust="official_metadata" if name == "announcements" else "external_untrusted",
            ))

    snapshot_kinds = {
        "research.trade_disclosure_calendar.live": "calendar",
        "research.control_lists.live": "regulatory_candidate",
        "research.overseas_earnings_calendar.live": "earnings_calendar_candidate",
    }
    for raw in snapshots:
        snapshot, _metadata = _snapshot_record(raw)
        dataset_id = str(snapshot.get("dataset_id") or "")
        if dataset_id == "research.newsradar.live":
            rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
            industry_context = (
                snapshot.get("industry_context")
                if isinstance(snapshot.get("industry_context"), Mapping) else {}
            )
            extension = (
                snapshot.get("industry_candidate_relations")
                if isinstance(snapshot.get("industry_candidate_relations"), Mapping) else {}
            )
            relation_rows = (
                extension.get("rows") if isinstance(extension.get("rows"), list) else []
            )
            relations_by_index = {
                relation.get("row_index"): relation
                for relation in relation_rows if isinstance(relation, Mapping)
            }
            for index, row in enumerate(rows[:200]):
                if not isinstance(row, Mapping):
                    continue
                if row.get("time_bucket") == "future_source_time":
                    continue
                relation = relations_by_index.get(index)
                # Defense in depth: an unreviewed/uncertain/unrelated RSS row
                # can never reach the report event stream, even if a caller
                # bypasses the workspace preparation helper.
                if not isinstance(relation, Mapping) \
                        or relation.get("relation_status") != "related" \
                        or relation.get("report_eligible") is not True \
                        or relation.get("issuer_fact") is not False:
                    continue
                payload = dict(row)
                payload.setdefault("industry_context", dict(industry_context))
                payload["candidate_id"] = relation.get("candidate_id")
                payload["relation_status"] = "related"
                payload["user_judgement_required"] = False
                payload["review"] = dict(relation.get("review") or {})
                payload["issuer_fact"] = False
                events.append(_event(
                    "industry_signal_related_context",
                    source_id=dataset_id,
                    when=(row.get("source_time") or row.get("published_at")
                          or row.get("date") or row.get("time") or row.get("ts")),
                    title=row.get("title"),
                    # A same-sector RSS row is deliberately not bound to the issuer.
                    subject_code="",
                    payload=payload,
                    trust="external_untrusted",
                ))
            continue
        kind = snapshot_kinds.get(dataset_id)
        if not kind:
            continue
        rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
        for row in rows[:200]:
            if not isinstance(row, Mapping):
                continue
            issuer_bound = dataset_id == "research.trade_disclosure_calendar.live"
            payload = dict(row)
            if not issuer_bound:
                payload["relation_status"] = "unreviewed"
                payload["user_judgement_required"] = True
                payload["issuer_fact"] = False
            events.append(_event(
                kind, source_id=dataset_id,
                when=(row.get("date") or row.get("actual_date")
                      or row.get("expected_date") or row.get("filed")),
                title=(row.get("title") or row.get("name") or row.get("event")
                       or row.get("symbol") or row.get("entity")),
                subject_code=subject_code if issuer_bound else "", payload=payload,
                trust="official_or_provider_metadata",
            ))
    return sorted(
        {row["event_id"]: row for row in events}.values(),
        key=lambda row: (str(row.get("when") or ""), row["event_id"]),
    )


def _review_project_company_inputs(
    pack: dict[str, Any], snapshots: list[Any],
    review_state: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[Any], dict[str, Any]]:
    """Remove unreviewed industry text from every report-facing surface."""
    from guanlan_v2.research.company_industry_news import (
        REVIEW_PROJECTION_SCHEMA,
        project_reviewed_newsradar_snapshot,
    )

    safe_pack = copy.deepcopy(pack)
    sections = safe_pack.get("sections")
    suppressed_pack_rows = 0
    if isinstance(sections, dict) and isinstance(sections.get("newsradar"), Mapping):
        old = sections.get("newsradar") or {}
        old_rows = old.get("items") if isinstance(old.get("items"), list) else []
        suppressed_pack_rows = len(old_rows)
    projected: list[Any] = []
    snapshot_gates: list[dict[str, Any]] = []
    for raw in snapshots:
        snapshot, metadata = _snapshot_record(raw)
        dataset_id = str(snapshot.get("dataset_id") or metadata.get("dataset_id") or "")
        if dataset_id != "research.newsradar.live":
            projected.append(raw)
            continue
        reviewed_snapshot = project_reviewed_newsradar_snapshot(
            snapshot, review_state,
            snapshot_asset_id=str(metadata.get("id") or ""),
        )
        gate = reviewed_snapshot.get("industry_review_gate")
        if isinstance(gate, Mapping):
            snapshot_gates.append({
                "asset_id": metadata.get("id"),
                **dict(gate),
            })
        if isinstance(raw, Mapping) and isinstance(raw.get("snapshot"), Mapping):
            record = copy.deepcopy(dict(raw))
            record["snapshot"] = reviewed_snapshot
            projected.append(record)
        else:
            projected.append(reviewed_snapshot)

    aggregate_counts = {
        "total": 0, "related": 0, "unrelated": 0,
        "uncertain": 0, "unreviewed": 0, "invalid": 0,
        "future_excluded": 0,
    }
    eligible_ids: list[str] = []
    for gate in snapshot_gates:
        counts = gate.get("candidate_counts")
        if isinstance(counts, Mapping):
            for key in aggregate_counts:
                aggregate_counts[key] += int(counts.get(key) or 0)
        eligible_ids.extend(
            str(item) for item in (gate.get("report_eligible_candidate_ids") or [])
            if str(item)
        )
    state = dict(review_state or {})
    aggregate = {
        "schema_version": REVIEW_PROJECTION_SCHEMA,
        "status": (
            "ready" if eligible_ids else
            "awaiting_review" if (
                aggregate_counts["unreviewed"] or aggregate_counts["uncertain"]
            ) else "no_related_candidates"
        ),
        "only_related_report_eligible": True,
        "issuer_fact": False,
        "ledger_revision": int(state.get("revision") or 0),
        "ledger_head_sha256": state.get("head_sha256"),
        "candidate_counts": aggregate_counts,
        "report_eligible_candidate_ids": list(dict.fromkeys(eligible_ids)),
        "snapshot_gates": snapshot_gates,
        "suppressed_unreviewable_pack_rows": suppressed_pack_rows,
    }
    aggregate["projection_sha256"] = _digest(aggregate)
    return safe_pack, projected, aggregate


def _source_plan_rows(source_plan: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rank = {name: index for index, name in enumerate(_SOURCE_ORDER)}
    rows = [dict(item) for item in source_plan if isinstance(item, Mapping)]
    rows.sort(key=lambda row: (
        rank.get(str(row.get("dataset_id") or ""), len(rank)),
        str(row.get("dataset_id") or ""),
    ))
    return rows


def render_readiness_report(
    *, subject: Mapping[str, Any], hub: Mapping[str, Any], pipeline: Mapping[str, Any],
    source_plan: Iterable[Mapping[str, Any]], as_of: str,
) -> str:
    name = _text(subject.get("name") or subject.get("code"), 120)
    code = _text(subject.get("code"), 40)
    stages = pipeline.get("stages") if isinstance(pipeline.get("stages"), list) else []
    plan = _source_plan_rows(source_plan)
    lines = [
        f"# {name}（{code}）公司研究准备包",
        "",
        "> 这是确定性的证据与通路检查，不是最终投资观点。完整叙事仍需由研究 Agent 基于本包生成。",
        "",
        f"- 截止时间：{as_of or '未提供'}",
        f"- 完成状态：{pipeline.get('status') or 'unknown'}",
        f"- Evidence Hub：{hub.get('status') or 'unknown'}",
        f"- 公司直接证据：{hub.get('summary', {}).get('issuer_evidence', 0)}",
        f"- 行业/宏观背景证据：{hub.get('summary', {}).get('context_evidence', 0)}",
        "",
        "## 六阶段闸门",
        "",
        "| 阶段 | 状态 | 已观察输入 | 缺口数 |",
        "|---|---|---:|---:|",
    ]
    for row in stages:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            f"| {row.get('stage')} | {row.get('status')} | "
            f"{len(row.get('observed_inputs') or [])} | {len(row.get('gaps') or [])} |"
        )

    lines += ["", "## 数据连接状态", "", "| 数据源 | 状态 | 方式 | 资产 | 说明 |",
              "|---|---|---|---|---|"]
    for row in plan:
        lines.append(
            "| {dataset} | {status} | {mode} | {asset} | {note} |".format(
                dataset=_text(row.get("dataset_id"), 100),
                status=_text(row.get("status"), 40),
                mode=_text(row.get("mode"), 40),
                asset=_text(row.get("asset_id"), 40) or "—",
                note=_text(row.get("note"), 180).replace("|", "/") or "—",
            )
        )

    lines += ["", "## 证据登记", "", "| 来源 | 范围 | 截止 | 新鲜度 | 可靠性 |",
              "|---|---|---|---|---|"]
    for row in hub.get("source_register") or []:
        if not isinstance(row, Mapping):
            continue
        source_status = str(row.get("status") or "").strip().lower()
        if row.get("future") is True:
            availability = "未来/不可用"
        elif row.get("usable") is False or source_status in {
            "failed", "error", "unavailable", "missing", "empty", "future",
            "blocked", "not_configured",
        }:
            availability = "不可用"
        elif row.get("stale") is None:
            availability = "未知"
        elif row.get("stale"):
            availability = "过期"
        else:
            availability = "可用"
        lines.append(
            f"| {_text(row.get('source_id'), 100)} | {row.get('type')} | "
            f"{row.get('as_of') or '—'} | {availability} | {row.get('reliability') or 'unknown'} |"
        )

    gaps = list(hub.get("gaps") or [])
    for stage in stages:
        if isinstance(stage, Mapping):
            gaps.extend(stage.get("gaps") or [])
    lines += ["", "## 未接通 / 数据缺口", ""]
    if not gaps:
        lines.append("- 当前证据闸门没有登记缺口；这不等于投资结论无风险。")
    else:
        seen: set[str] = set()
        for gap in gaps:
            if not isinstance(gap, Mapping):
                continue
            key = str(gap.get("gap_id") or gap.get("code") or "") + str(gap.get("detail") or gap.get("message") or "")
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- [{gap.get('severity') or ('关键' if gap.get('critical') else '可选')}] "
                f"{gap.get('stage') or '全局'} · {gap.get('source_id') or gap.get('input_key') or '未指定源'}："
                f"{_text(gap.get('detail') or gap.get('message'), 400)}"
            )
    lines += [
        "",
        "## 当前边界",
        "",
        "- 紫色实时接口的原始快照只保存到当前工作空间，没有写入 stocks 或全局 events.db。",
        "- 本包已生成私有 events 文件；全局事件归一化和自动线索回灌仍未接入。",
        "- calculations 已包含累计财务单季化、TTM、一致预期、Vibe 七项估值与扩展估值 DAG；失败节点保留原因。",
        "- 风险 Gate 与最终报告 Gate 已接线；准备阶段若没有结构化反证/裁决点或尚无最终正文，会如实保持 incomplete。",
        "- 台系公司、商品合约、管制实体、SEC/FINRA ticker、CFTC 合约等必须显式映射；未提供时不猜。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _apply_contract_to_stage(
    stage_input: dict[str, Any], stage: str, contract: Mapping[str, Any], *,
    evidence_id: str,
) -> None:
    """Make a deterministic contract authoritative over a Hub presence check."""
    target = stage_input.setdefault(stage, {"evidence": [], "gaps": [], "stale": False})
    evidence = target.setdefault("evidence", [])
    if not any(item.get("evidence_id") == evidence_id for item in evidence if isinstance(item, Mapping)):
        evidence.append({
            "input_key": stage if stage != "valuation" else "quote",
            "evidence_id": evidence_id,
            "available": contract.get("status") in {"complete", "stale"},
            "stale": contract.get("status") == "stale",
        })
    gaps = target.setdefault("gaps", [])
    for item in contract.get("gaps") or []:
        if not isinstance(item, Mapping):
            continue
        gaps.append({
            "code": str(item.get("code") or f"{stage}_contract_gap"),
            "message": str(item.get("message") or item.get("detail") or "Contract gap."),
            "stage": stage,
            "input_key": str(item.get("field") or item.get("operation") or stage),
            "critical": bool(item.get("critical", True)),
            "details": {key: value for key, value in item.items()
                        if key not in {"code", "message", "detail", "field", "operation", "critical"}},
        })
    if contract.get("status") not in {"complete", "stale"} and not any(
        bool(item.get("critical")) for item in gaps if isinstance(item, Mapping)
    ):
        gaps.append({
            "code": f"{stage}_contract_incomplete",
            "message": f"The deterministic {stage} contract is incomplete.",
            "stage": stage,
            "input_key": stage,
            "critical": True,
        })
    target["stale"] = bool(target.get("stale")) or contract.get("status") == "stale"


def _normalise_dag_calculations(dag: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in dag.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        row = dict(node)
        row.setdefault("calculation_id", row.get("calc_id"))
        row.setdefault("function", row.get("calc_id"))
        row.setdefault("result", {
            "value": row.get("result"), "unit": row.get("unit"), "period": row.get("period"),
        })
        rows.append(row)
    return rows


def build_workspace_company_artifacts(
    evidence_pack: Mapping[str, Any], workspace_snapshots: Iterable[Any], *,
    subject: Mapping[str, Any], source_plan: Iterable[Mapping[str, Any]], as_of: str,
    industry_review_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return JSON-safe documents ready for trusted workspace persistence."""
    requested_asof = _normalise_as_of(as_of)
    snapshots = list(workspace_snapshots or [])
    safe_pack = _json_safe(copy.deepcopy(dict(evidence_pack or {})))
    safe_snapshots = _json_safe(copy.deepcopy(snapshots))
    subject_code = str(subject.get("code") or "").strip().upper()
    source_plan_input = [
        dict(row) for row in source_plan if isinstance(row, Mapping)
    ]
    historical = bool(
        requested_asof < _hk_today().isoformat()
        or safe_pack.get("mode") == "historical"
    )
    historical_base_pack: dict[str, Any] | None = None
    if historical:
        safe_pack = _scrub_historical_pack(
            safe_pack, requested_asof=requested_asof, expected_code=subject_code,
        )
        safe_snapshots, snapshot_unavailable = _filter_historical_snapshots(
            safe_snapshots, requested_asof=requested_asof, expected_code=subject_code,
        )
        if snapshot_unavailable:
            unavailable = [
                dict(item) for item in safe_pack.get("unavailable_sources") or []
                if isinstance(item, Mapping)
            ]
            unavailable.extend(snapshot_unavailable)
            safe_pack["unavailable_sources"] = unavailable
            errors = dict(safe_pack.get("errors") or {})
            for item in snapshot_unavailable:
                errors.setdefault(str(item["source"]), str(item["reason"]))
            safe_pack["errors"] = errors
            _refresh_historical_manifest(safe_pack)
        historical_base_pack = copy.deepcopy(safe_pack)
    # Authenticate/cut off the immutable raw snapshot first.  The report-only
    # review projection necessarily changes its bytes and must not be confused
    # with the signed acquisition payload during historical verification.
    safe_pack, safe_snapshots, industry_review_gate = _review_project_company_inputs(
        safe_pack, safe_snapshots, industry_review_state,
    )
    time_contract = build_company_time_contract(
        safe_pack, research_asof=requested_asof,
    )
    for row in source_plan_input:
        if row.get("dataset_id") != "research.newsradar.live":
            continue
        counts = industry_review_gate["candidate_counts"]
        row["industry_review_gate"] = industry_review_gate
        row["report_eligible_count"] = len(
            industry_review_gate["report_eligible_candidate_ids"]
        )
        row["note"] = (
            f"行业候选 total={counts['total']} / related={counts['related']} / "
            f"unrelated={counts['unrelated']} / uncertain={counts['uncertain']} / "
            f"unreviewed={counts['unreviewed']}；仅 related 进入报告 context，"
            "始终 issuer_fact=false"
        )
    hub = build_company_research_hub(safe_pack, safe_snapshots, as_of=as_of or None)
    events = normalise_company_events(
        safe_pack, safe_snapshots, subject_code=str(subject.get("code") or ""),
    )

    profile_contract = evaluate_profile_contract(
        safe_pack, safe_snapshots, subject=subject, as_of=as_of,
    )
    financials_contract = evaluate_financials_contract(safe_pack)
    estimates_contract = evaluate_estimates_contract(safe_pack, as_of=as_of)

    # Calculated contracts are issuer-derived evidence.  Add them to a copy used
    # by downstream deterministic adapters; the raw evidence ledger below keeps
    # the original source pack unchanged.
    contract_pack = copy.deepcopy(safe_pack)
    contract_sections = contract_pack.setdefault("sections", {})
    if not isinstance(contract_sections, dict):
        contract_sections = {}
        contract_pack["sections"] = contract_sections
    contract_sections["company_profile_contract"] = profile_contract
    contract_sections["company_financials_contract"] = financials_contract
    contract_sections["company_estimates_contract"] = estimates_contract

    valuation_dag = build_valuation_dag_from_evidence(
        contract_pack, as_of=as_of, include_price_scenarios=False,
    )
    vibe_valuation = build_vibe_valuation_contract(
        contract_pack, financials_contract, estimates_contract, as_of=as_of,
    )
    contract_sections["company_valuation_dag"] = valuation_dag
    contract_sections["company_vibe_valuation"] = vibe_valuation
    conflicts = list(hub.get("conflicts") or [])
    conflicts.extend(financials_contract.get("conflicts") or [])
    derived_issuer_evidence: list[dict[str, Any]] = []
    for source_id, stage, contract in (
        ("contract.company_estimates", "estimates", estimates_contract),
        ("contract.company_vibe_valuation", "valuation", vibe_valuation),
    ):
        supporting_ids = _matching_ids(hub, stage, issuer_only=True)
        if not supporting_ids:
            continue
        derived_issuer_evidence.append({
            "source_id": source_id,
            "evidence_id": supporting_ids[0],
            "source_refs": supporting_ids,
            "scope": "issuer",
            "status": contract.get("status") or "unknown",
            "stale": (hub.get("stage_status") or {}).get(stage) == "stale",
            "contract": contract,
        })
    risk_contract = build_company_risk_contract(
        contract_pack, hub, issuer_evidence=derived_issuer_evidence,
        events=events, conflicts=conflicts, as_of=as_of,
    )
    contract_sections["company_risk_contract"] = risk_contract

    stage_input = pipeline_input_from_hub(hub)
    _apply_contract_to_stage(
        stage_input, "profile", profile_contract,
        evidence_id="PROFILE-" + _digest(profile_contract)[:16].upper(),
    )
    _apply_contract_to_stage(
        stage_input, "financials", financials_contract,
        evidence_id="FINANCIALS-" + _digest(financials_contract)[:16].upper(),
    )
    _apply_contract_to_stage(
        stage_input, "estimates", estimates_contract,
        evidence_id="ESTIMATES-" + _digest(estimates_contract)[:16].upper(),
    )
    _apply_contract_to_stage(
        stage_input, "valuation", vibe_valuation,
        evidence_id="VALUATION-" + _digest(vibe_valuation)[:16].upper(),
    )
    risk_stage = risk_stage_evidence_from_contract(risk_contract)
    # Preserve optional Hub observations while making the risk contract's gate
    # authoritative for completion.
    risk_stage.setdefault("gaps", []).extend(
        gap for gap in stage_input.get("risk", {}).get("gaps", [])
        if isinstance(gap, Mapping) and not gap.get("critical")
    )
    stage_input["risk"] = risk_stage
    stage_input["report"].setdefault("gaps", []).append({
        "code": "final_report_pending",
        "message": "The seven-section final report has not been generated and gated yet.",
        "stage": "report",
        "input_key": "report_gate",
        "critical": True,
    })

    handlers = {
        "profile": lambda _context: None,
        "financials": lambda _context: {
            "calculations": financials_contract.get("calculations") or [],
            "conflicts": financials_contract.get("conflicts") or [],
        },
        "estimates": lambda _context: {
            "calculations": estimates_contract.get("calculations") or [],
        },
        "valuation": lambda _context: {
            "calculations": vibe_valuation.get("calculations") or [],
        },
        "risk": lambda _context: {
            "events": risk_contract.get("decision_points") or [],
            "conflicts": risk_contract.get("conflicts") or [],
        },
    }
    pipeline_result = CompanyResearchPipeline(handlers).run(stage_input)
    pipeline = pipeline_result.as_dict()

    stage_states = [str(row.get("status") or "") for row in pipeline.get("stages") or []]
    # ``risk`` is completed from the report writer's structured sidecar and
    # ``report`` is gated only after Markdown exists.  Their preparation-time
    # pending state is lifecycle state, not evidence-source readiness.  Keep a
    # separate fail-closed source ceiling so the writer does not mistake an
    # expected pending stage for a permanent final ``incomplete`` status.
    pre_report_source_rows = [
        row for row in pipeline.get("stages") or []
        if isinstance(row, Mapping)
        and row.get("stage") in {"profile", "financials", "estimates", "valuation"}
    ]
    if any(str(row.get("status") or "") == "failed" for row in pre_report_source_rows):
        pre_report_source_status = "failed"
    elif any(bool(row.get("stale")) for row in pre_report_source_rows):
        pre_report_source_status = "stale"
    elif any(str(row.get("status") or "") != "complete" for row in pre_report_source_rows):
        pre_report_source_status = "incomplete"
    else:
        pre_report_source_status = "complete"
    completion_status = (
        "failed" if "failed" in stage_states else
        "incomplete" if any(state in {"incomplete", "skipped"} for state in stage_states) else
        "complete"
    )
    freshness_status = (
        "stale" if any(bool(row.get("stale")) for row in pipeline.get("stages") or []) else
        "unknown" if (hub.get("summary") or {}).get("unknown_freshness_stages") else
        "fresh"
    )
    status = (
        "failed" if completion_status == "failed" else
        "stale" if freshness_status == "stale" else
        "incomplete" if completion_status != "complete" else
        "complete"
    )
    pipeline["status"] = status
    report_md = render_readiness_report(
        subject=subject, hub=hub, pipeline=pipeline,
        source_plan=source_plan_input, as_of=as_of,
    )
    pipeline_events = (
        pipeline.get("artifacts", {}).get("events", [])
        if isinstance(pipeline.get("artifacts"), Mapping) else []
    )
    events.extend(
        {"event_id": "CRE-" + _digest(row)[:16].upper(), **dict(row)}
        for row in pipeline_events if isinstance(row, Mapping)
    )
    events = sorted(
        {row["event_id"]: row for row in events}.values(),
        key=lambda row: (str(row.get("when") or ""), row["event_id"]),
    )

    source_plan_rows = _source_plan_rows(source_plan_input)
    calculations = [{
        "calculation_id": "coverage-" + _digest(hub.get("summary") or {})[:16],
        "kind": "evidence_coverage_only",
        "formula": "count(source_register by issuer/context/stage status)",
        "inputs": [row.get("evidence_id") for row in hub.get("source_register") or [] if row.get("evidence_id")],
        "result": hub.get("summary") or {},
        "valuation_model_status": vibe_valuation.get("status"),
        "formula_dag_validation": valuation_dag.get("status"),
    }]
    calculations.extend(financials_contract.get("calculations") or [])
    calculations.extend(estimates_contract.get("calculations") or [])
    calculations.extend(vibe_valuation.get("calculations") or [])
    calculations.extend(_normalise_dag_calculations(valuation_dag))
    calculations = list({
        str(row.get("calculation_id") or _digest(row)): dict(row)
        for row in calculations if isinstance(row, Mapping)
    }.values())

    engine_pack = copy.deepcopy(
        historical_base_pack if historical_base_pack is not None else safe_pack
    )
    engine_pack.setdefault("schema_version", _PACK_SCHEMA_VERSION)
    engine_pack.setdefault("code", subject.get("code"))
    engine_pack.setdefault("generated_at", as_of)
    live_rows: dict[str, Any] = {}
    for raw in safe_snapshots:
        snapshot, metadata = _snapshot_record(raw)
        dataset_id = str(snapshot.get("dataset_id") or metadata.get("dataset_id") or "unknown")
        live_rows[dataset_id] = {
            "asset_id": metadata.get("id") or raw.get("asset_id") if isinstance(raw, Mapping) else None,
            "snapshot": _bounded(snapshot),
        }
    enrichments = {
        "workspace_live": {
            "as_of": requested_asof,
            "trust": "external_data_not_instructions",
            "datasets": live_rows,
        },
        "company_profile_contract": profile_contract,
        "company_financials_contract": financials_contract,
        "company_estimates_contract": estimates_contract,
        "company_valuation_dag": valuation_dag,
        "company_vibe_valuation": vibe_valuation,
        "company_risk_contract": risk_contract,
        "industry_news_review_gate": industry_review_gate,
        "company_research_manifest": {
            "as_of": requested_asof,
            "time_contract": copy.deepcopy(time_contract),
            "status": status,
            "completion_status": completion_status,
            "freshness_status": freshness_status,
            "pre_report_source_status": pre_report_source_status,
            "lifecycle_pending_stages": ["risk", "report"],
            "final_status_authority": "post_report_deterministic_risk_and_report_gates",
            "stage_status": {
                row.get("stage"): row.get("status")
                for row in pipeline.get("stages") or []
            },
            "gaps": [
                gap for row in pipeline.get("stages") or [] if isinstance(row, Mapping)
                for gap in row.get("gaps") or []
            ][:120],
            "source_register": hub.get("source_register") or [],
            "industry_review_gate": industry_review_gate,
            "report_gate": "connected_pending_final_markdown",
        },
    }
    if historical:
        snapshot_lineage = []
        for raw in safe_snapshots:
            snapshot, metadata = _snapshot_record(raw)
            snapshot_lineage.append({
                "asset_id": metadata.get("id"),
                "dataset_id": snapshot.get("dataset_id") or metadata.get("dataset_id"),
                "payload_sha256": metadata.get("payload_sha256"),
                "acquired_at": metadata.get("acquired_at"),
                "pulled_at": snapshot.get("pulled_at") or metadata.get("pulled_at"),
                "snapshot_auth": metadata.get("snapshot_auth"),
            })
        historical_enrichments = dict(enrichments)
        historical_sections = (
            historical_base_pack.get("sections")
            if isinstance((historical_base_pack or {}).get("sections"), dict) else {}
        )
        if "newsradar" in historical_sections:
            # Replace the signed base section through the authenticated
            # enrichment seam.  The raw unreviewed text remains preserved in
            # its parent pack lineage but is absent from the report-facing
            # child pack.
            historical_enrichments["newsradar"] = None
        engine_pack = enrich_historical_evidence_pack(
            engine_pack,
            historical_enrichments,
            proof="deterministic_workspace_enrichment_from_verified_pit_inputs",
            proof_context={"workspace_snapshots": snapshot_lineage},
        )
    else:
        engine_sections = engine_pack.setdefault("sections", {})
        if not isinstance(engine_sections, dict):
            engine_sections = {}
            engine_pack["sections"] = engine_sections
        # Raw pack NewsRadar rows have no human review receipt.  The reviewed
        # related-only workspace projection is available under workspace_live.
        engine_sections.pop("newsradar", None)
        engine_sections.update(enrichments)
    # Keep the same immutable contract visible at the engine-pack top level;
    # downstream finalization propagates the prepared manifest copy verbatim.
    engine_pack["time_contract"] = copy.deepcopy(time_contract)
    if historical:
        # ``enrich_historical_evidence_pack`` seals the derived child pack, so
        # the top-level time contract added above must be included in a final
        # seal as well.  Otherwise this last deterministic write immediately
        # invalidates ``manifest_digest``/``manifest_auth``.
        _refresh_historical_manifest(engine_pack)

    evidence = {
        "schema_version": "company-research-evidence-ledger@1",
        "subject": dict(subject),
        "as_of": as_of,
        "time_contract": copy.deepcopy(time_contract),
        "hub": hub,
        "evidence_pack": safe_pack,
        "contracts": {
            "profile": profile_contract,
            "financials": financials_contract,
            "estimates": estimates_contract,
            "valuation": vibe_valuation,
            "valuation_extended_dag": valuation_dag,
            "risk": risk_contract,
        },
        "workspace_snapshots": safe_snapshots,
        "source_plan": source_plan_rows,
        "industry_review_gate": industry_review_gate,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "company_research_readiness_bundle",
        "subject": dict(subject),
        "as_of": as_of,
        "time_contract": copy.deepcopy(time_contract),
        "status": status,
        "completion_status": completion_status,
        "freshness_status": freshness_status,
        "pre_report_source_status": pre_report_source_status,
        "lifecycle_pending_stages": ["risk", "report"],
        "pipeline": pipeline,
        "hub_summary": hub.get("summary") or {},
        "source_plan": source_plan_rows,
        "industry_review_gate": industry_review_gate,
        "known_unconnected_nodes": [
            "events.global_events_db_normalisation",
        ],
        "connected_nodes": [
            "profile.identity_listing_quote_calendar_gate",
            "financials.cumulative_quarter_ttm_gate",
            "estimates.consensus_t_t1_t2_gate",
            "valuation.vibe_seven_function_contract",
            "valuation.extended_formula_dag",
            "risk.counter_evidence_three_decision_gate",
            "report.seven_section_citation_compliance_gate",
            "industry_news.human_related_context_gate",
        ],
        "limitations": [
            "readiness_bundle_not_final_investment_report",
            "workspace_events_not_global_events_db",
            "risk_gate_requires_structured_claims_counterevidence_and_decision_points",
            "final_report_gate_runs_only_after_markdown_generation",
            "conditional_source_mappings_are_never_guessed",
            "industry_news_requires_user_related_review_and_never_becomes_issuer_fact",
        ],
    }
    return {
        "status": status,
        "completion_status": completion_status,
        "freshness_status": freshness_status,
        "report_md": report_md,
        "evidence": evidence,
        "calculations": calculations,
        "conflicts": conflicts,
        "events": events,
        "manifest": manifest,
        "engine_evidence_pack": engine_pack,
    }


__all__ = [
    "SCHEMA_VERSION",
    "build_company_time_contract",
    "build_workspace_company_artifacts",
    "enrich_historical_evidence_pack",
    "normalise_company_events",
    "pipeline_input_from_hub",
    "render_readiness_report",
    "timestamp_not_after_asof",
]
