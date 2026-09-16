# -*- coding: utf-8 -*-
"""Evidence-bound normalization of pre-v3 event rows.

This module is intentionally an *offline candidate* transform.  It never opens
the production database itself and it never invents issuer or industry facts.
Only fields that can be derived from stored structured columns are populated;
every derivation is recorded under ``provenance_json.legacy_normalization``.

``available_at`` is used as a last-resort ``source_asof`` only with the visible
``source_asof_from_local_available_at`` quality issue.  This preserves the old
compatibility behaviour without pretending that local availability is a
provider-authored cutoff.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from guanlan_v2.events import normalization, store


BACKFILL_VERSION = "events-legacy-evidence-v1"
QUALITY_LEVELS = frozenset({"derived", "partial", "unrecoverable"})
_FIELDS = (
    "source_id", "source_asof", "subject_kind", "issuer_ids",
    "context_type", "context_ids",
)
_SOURCE_TIME_KEYS = ("source_asof", "retrieved_at", "pulled_at", "fetched_at")


@dataclass(frozen=True)
class LegacyNormalization:
    """One deterministic patch and its traceable quality assessment."""

    event_id: str
    values: Mapping[str, Any]
    quality: str
    issues: tuple[str, ...]
    evidence: tuple[Mapping[str, Any], ...]

    @property
    def complete(self) -> bool:
        return all(self.values.get(field) not in (None, "") for field in _FIELDS)


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError, MemoryError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        decoded: Iterable[Any] = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError, MemoryError):
            return []
        decoded = parsed if isinstance(parsed, list) else ()
    else:
        decoded = ()
    return sorted({str(item).strip() for item in decoded if str(item).strip()})


def _timestamp(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return store.normalize_timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _source_time(row: Mapping[str, Any]) -> tuple[Optional[str], Dict[str, Any], bool]:
    """Return time, evidence, and whether it is a local-availability fallback."""
    for container_name in ("provenance_json", "payload_json"):
        container = _json_object(row.get(container_name))
        for key in _SOURCE_TIME_KEYS:
            normalized = _timestamp(container.get(key))
            if normalized:
                return normalized, {
                    "field": f"{container_name}.{key}", "value": normalized,
                    "derivation": "stored_source_timestamp",
                }, False
    available = _timestamp(row.get("available_at"))
    if available:
        return available, {
            "field": "available_at", "value": available,
            "derivation": "local_visibility_fallback_not_provider_cutoff",
        }, True
    return None, {"field": "source_asof", "derivation": "not_recoverable"}, False


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def plan_legacy_normalization(row: Mapping[str, Any]) -> Optional[LegacyNormalization]:
    """Derive a patch from one stored row, or ``None`` when nothing is missing.

    The publisher ``source`` column is deliberately never promoted to
    ``source_id``: it names content provenance, not necessarily the adapter that
    observed it.  An exact ``ingested_from`` value is safe to preserve.
    """
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("legacy event row requires event_id")
    if all(not _missing(row.get(field)) for field in _FIELDS):
        return None

    values: Dict[str, Any] = {field: row.get(field) for field in _FIELDS}
    # Work with decoded lists even when a partially migrated row already has
    # one of these physical JSON columns.  This prevents double-encoding.
    if not _missing(values["issuer_ids"]):
        values["issuer_ids"] = _string_list(values["issuer_ids"])
    if not _missing(values["context_ids"]):
        values["context_ids"] = _string_list(values["context_ids"])
    issues: list[str] = []
    evidence: list[Dict[str, Any]] = []

    ingested_from = str(row.get("ingested_from") or "").strip().lower()
    if _missing(values["source_id"]):
        if ingested_from:
            values["source_id"] = ingested_from
            evidence.append({
                "field": "source_id", "from": "ingested_from",
                "value": ingested_from, "derivation": "exact_stored_value",
            })
        else:
            values["source_id"] = None
            issues.append("source_id_not_recoverable")

    if _missing(values["source_asof"]):
        source_asof, time_evidence, used_fallback = _source_time(row)
        values["source_asof"] = source_asof
        evidence.append({"field": "source_asof", **time_evidence})
        if used_fallback:
            issues.append("source_asof_from_local_available_at")
        elif source_asof is None:
            issues.append("source_asof_not_recoverable")

    tickers = _string_list(row.get("tickers"))
    issuers = sorted({normalization.normalize_issuer_id(item) for item in tickers
                      if normalization.normalize_issuer_id(item)})
    event_type = str(row.get("event_type") or "").strip()

    if ingested_from == "newsradar" or event_type.lower().startswith("newsradar:"):
        sector = event_type.split(":", 1)[1].strip() if ":" in event_type else ""
        if _missing(values["subject_kind"]):
            values["subject_kind"] = "context"
        if _missing(values["issuer_ids"]):
            values["issuer_ids"] = []
        if _missing(values["context_type"]):
            values["context_type"] = "industry_news"
        if _missing(values["context_ids"]):
            values["context_ids"] = [sector] if sector else []
        evidence.append({
            "field": "subject/context", "from": "event_type",
            "value": event_type, "derivation": "newsradar_source_category",
        })
        if not sector:
            issues.append("newsradar_sector_not_recoverable")
    elif ingested_from == "kuaixun":
        if _missing(values["issuer_ids"]):
            values["issuer_ids"] = issuers
        if _missing(values["subject_kind"]):
            values["subject_kind"] = "issuer" if issuers else "context"
        if _missing(values["context_type"]):
            values["context_type"] = "company_news" if issuers else "market_news"
        if _missing(values["context_ids"]):
            values["context_ids"] = ["a_share"]
        evidence.append({
            "field": "subject/context", "from": "tickers+ingested_from",
            "value": issuers, "derivation": "stored_structured_kuaixun_fields",
        })
    elif issuers:
        if _missing(values["issuer_ids"]):
            values["issuer_ids"] = issuers
        if _missing(values["subject_kind"]):
            values["subject_kind"] = "issuer"
        # The structured ticker proves an event-to-entity link.  It does not
        # prove which context taxonomy the unknown legacy adapter intended.
        if _missing(values["context_ids"]):
            values["context_ids"] = []
        evidence.append({
            "field": "issuer_ids", "from": "tickers", "value": issuers,
            "derivation": "stored_structured_ticker_field",
        })
        if _missing(values["context_type"]):
            values["context_type"] = None
            issues.append("context_type_not_recoverable")
    else:
        if _missing(values["issuer_ids"]):
            values["issuer_ids"] = []
        if _missing(values["context_ids"]):
            values["context_ids"] = []
        for field in ("subject_kind", "context_type"):
            if _missing(values[field]):
                values[field] = None
                issues.append(f"{field}_not_recoverable")

    complete = all(values.get(field) not in (None, "") for field in _FIELDS)
    if complete and not issues:
        quality = "derived"
    elif evidence:
        quality = "partial"
    else:
        quality = "unrecoverable"
    return LegacyNormalization(
        event_id=event_id,
        values=values,
        quality=quality,
        issues=tuple(sorted(set(issues))),
        evidence=tuple(evidence),
    )


def _json_text(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _relation_id(event_id: str, relation_type: str, target_id: str) -> str:
    digest = hashlib.sha256(
        f"{BACKFILL_VERSION}\0{event_id}\0{relation_type}\0{target_id}".encode("utf-8")
    ).hexdigest()
    return f"legacy-normalization:{digest}"


def _existing_provenance(row: Mapping[str, Any]) -> Dict[str, Any]:
    return store.sanitize_provenance(_json_object(row.get("provenance_json")))


def _apply_one(conn: sqlite3.Connection, row: Mapping[str, Any],
               plan: LegacyNormalization) -> Dict[str, int]:
    provenance = _existing_provenance(row)
    provenance["legacy_normalization"] = {
        "schema": BACKFILL_VERSION,
        "quality": plan.quality,
        "issues": list(plan.issues),
        "evidence": [dict(item) for item in plan.evidence],
    }
    values = dict(plan.values)
    conn.execute(
        "UPDATE events SET source_id=?,source_asof=?,subject_kind=?,issuer_ids=?,"
        "context_type=?,context_ids=?,normalization_version=?,provenance_json=? "
        "WHERE event_id=?",
        (
            values.get("source_id"), values.get("source_asof"),
            values.get("subject_kind"), _json_text(values.get("issuer_ids") or []),
            values.get("context_type"), _json_text(values.get("context_ids") or []),
            str(row.get("normalization_version") or BACKFILL_VERSION),
            _json_text(provenance), plan.event_id,
        ),
    )

    inserted_relations = 0
    created_at = str(row.get("available_at") or "")
    for issuer_id in values.get("issuer_ids") or []:
        inserted_relations += int(store.insert_event_relation(conn, {
            "relation_id": _relation_id(plan.event_id, "entity", str(issuer_id)),
            "event_id": plan.event_id,
            "relation_type": "entity",
            "target_id": str(issuer_id),
            "target_name": str(issuer_id),
            "relation_status": "source_asserted",
            "confidence": 0.9,
            "evidence": [{
                "kind": "source_field", "field": "tickers", "value": str(issuer_id),
                "claim_scope": "event_link_only", "backfill": BACKFILL_VERSION,
            }],
            "relation_method": "legacy_structured_ticker_field",
            "created_at": created_at,
        }))
    if (values.get("context_type") == "industry_news"
            and values.get("context_ids")):
        for context_id in values["context_ids"]:
            inserted_relations += int(store.insert_event_relation(conn, {
                "relation_id": _relation_id(plan.event_id, "industry", str(context_id)),
                "event_id": plan.event_id,
                "relation_type": "industry",
                "target_id": str(context_id),
                "target_name": str(context_id),
                "relation_status": "source_asserted",
                "confidence": 0.85,
                "evidence": [{
                    "kind": "source_field", "field": "event_type",
                    "value": str(row.get("event_type") or ""),
                    "claim_scope": "source_category_only", "backfill": BACKFILL_VERSION,
                }],
                "relation_method": "legacy_source_sector_field",
                "created_at": created_at,
            }))
    return {"updated": 1, "relations_inserted": inserted_relations}


def backfill_legacy_events(conn: sqlite3.Connection, *, apply: bool = False,
                           limit: Optional[int] = None) -> Dict[str, Any]:
    """Audit or apply evidence-bound patches on an explicitly supplied DB.

    ``apply=False`` performs no writes.  Applying is idempotent because patched
    events no longer satisfy the missing-field predicate and relation IDs are
    content-stable.  Callers should run this only on a disposable candidate.
    """
    sql = (
        "SELECT * FROM events WHERE source_id IS NULL OR trim(source_id)='' "
        "OR source_asof IS NULL OR trim(source_asof)='' "
        "OR subject_kind IS NULL OR trim(subject_kind)='' "
        "OR issuer_ids IS NULL OR trim(issuer_ids)='' "
        "OR context_type IS NULL OR trim(context_type)='' "
        "OR context_ids IS NULL OR trim(context_ids)='' ORDER BY event_id"
    )
    params: Sequence[Any] = ()
    if limit is not None:
        bounded = int(limit)
        if bounded < 1:
            raise ValueError("limit must be positive")
        sql += " LIMIT ?"
        params = (bounded,)

    summary: Dict[str, Any] = {
        "schema": BACKFILL_VERSION,
        "mode": "apply" if apply else "dry_run",
        "candidates": 0,
        "quality": {quality: 0 for quality in sorted(QUALITY_LEVELS)},
        "issues": {},
        "complete_after": 0,
        "incomplete_after": 0,
        "relations_planned": {"entity": 0, "industry": 0},
        "updated": 0,
        "relations_inserted": 0,
    }
    rows = conn.execute(sql, params).fetchall()
    plans: list[tuple[Mapping[str, Any], LegacyNormalization]] = []
    for raw in rows:
        row = dict(raw)
        plan = plan_legacy_normalization(row)
        if plan is None:
            continue
        plans.append((row, plan))
        summary["candidates"] += 1
        summary["quality"][plan.quality] += 1
        if plan.complete:
            summary["complete_after"] += 1
        else:
            summary["incomplete_after"] += 1
        summary["relations_planned"]["entity"] += len(
            plan.values.get("issuer_ids") or []
        )
        if (plan.values.get("context_type") == "industry_news"
                and plan.values.get("context_ids")):
            summary["relations_planned"]["industry"] += len(
                plan.values.get("context_ids") or []
            )
        for issue in plan.issues:
            summary["issues"][issue] = int(summary["issues"].get(issue, 0)) + 1

    if not apply or not plans:
        return summary

    nested = conn.in_transaction
    savepoint = "events_legacy_normalization"
    if nested:
        conn.execute(f"SAVEPOINT {savepoint}")
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for row, plan in plans:
            result = _apply_one(conn, row, plan)
            summary["updated"] += result["updated"]
            summary["relations_inserted"] += result["relations_inserted"]
    except BaseException:
        if nested:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.rollback()
        raise
    else:
        if nested:
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.commit()
    return summary


__all__ = [
    "BACKFILL_VERSION", "LegacyNormalization", "QUALITY_LEVELS",
    "backfill_legacy_events", "plan_legacy_normalization",
]
