# -*- coding: utf-8 -*-
"""Read-only data-quality audits for normalized events and candidate links."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from . import store
from .cleanup import CleanupPlanError, connect_immutable
from .relations import RELATION_POLICY, link_event_candidates


MAX_LINK_AUDIT_SAMPLE = 1_000


def _decode_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(value) for value in raw if str(value).strip()]
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def audit_candidate_links(
    database: Path,
    *,
    asof: Any,
    source_id: str = "newsradar",
    sample_limit: int = 500,
) -> Dict[str, Any]:
    """Preview deterministic event relations without persisting any row.

    NewsRadar's explicit ``newsradar:<sector>`` category becomes a
    ``source_asserted`` *industry link*.  It remains context only and
    ``issuer_fact`` is always false.  The empty catalog below intentionally
    disables current-master keyword inference so the audit measures only
    source-supplied fields and code-in-text candidates.
    """
    normalized_asof = store.normalize_asof(asof)
    source = str(source_id or "").strip().lower()
    if not source or len(source) > 100:
        raise CleanupPlanError("source_id must be 1..100 characters")
    if type(sample_limit) is not int or not 1 <= sample_limit <= MAX_LINK_AUDIT_SAMPLE:
        raise CleanupPlanError(
            f"sample_limit must be within 1..{MAX_LINK_AUDIT_SAMPLE}"
        )

    db = Path(database)
    if not db.is_absolute():
        raise CleanupPlanError("database path must be absolute")
    try:
        resolved_db = db.resolve(strict=True)
        before = resolved_db.stat()
    except OSError as exc:
        raise CleanupPlanError("database must be an existing file") from exc
    conn = connect_immutable(resolved_db)
    try:
        if not all(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ).fetchone()
            for table in ("events", "event_relations")
        ):
            raise CleanupPlanError("events schema is incomplete")
        relation_count_before = int(
            conn.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0]
        )
        source_expression = (
            "lower(coalesce(nullif(source_id,''),nullif(ingested_from,''),''))"
        )
        where = (
            f"{source_expression}=? AND available_at<=? "
            "AND (invalidated_at IS NULL OR invalidated_at>?)"
        )
        params = (source, normalized_asof, normalized_asof)
        aggregate = conn.execute(
            "SELECT COUNT(*) AS visible_rows,"
            "SUM(CASE WHEN lower(coalesce(event_type,'')) LIKE 'newsradar:%' "
            "         AND length(trim(substr(event_type,instr(event_type,':')+1)))>0 "
            "    THEN 1 ELSE 0 END) AS structured_industry_rows,"
            "SUM(CASE WHEN coalesce(tickers,'') NOT IN ('','[]') "
            "    THEN 1 ELSE 0 END) AS structured_ticker_rows "
            f"FROM events WHERE {where}",
            params,
        ).fetchone()
        sample_rows = conn.execute(
            "SELECT event_id,event_type,tickers,title,summary,available_at "
            f"FROM events WHERE {where} ORDER BY rowid DESC LIMIT ?",
            (*params, sample_limit),
        ).fetchall()

        relation_statuses: Dict[str, int] = {}
        relation_types: Dict[str, int] = {}
        industry_targets: Dict[str, int] = {}
        unlinked = 0
        issuer_fact_true = 0
        for row in sample_rows:
            event = dict(row)
            event["tickers"] = _decode_list(event.get("tickers"))
            linked = link_event_candidates(
                event, conn=conn, catalog={"entities": [], "industries": []},
                asof=normalized_asof,
            )
            if linked.get("issuer_fact") is not False:
                issuer_fact_true += 1
            if linked.get("unlinked"):
                unlinked += 1
            for relation in linked.get("relations") or []:
                status = str(relation.get("relation_status") or "unlinked")
                kind = str(relation.get("relation_type") or "unknown")
                relation_statuses[status] = relation_statuses.get(status, 0) + 1
                relation_types[kind] = relation_types.get(kind, 0) + 1
                if kind == "industry":
                    target = str(relation.get("target_id") or
                                 relation.get("target_name") or "unknown")
                    industry_targets[target] = industry_targets.get(target, 0) + 1

        relation_count_after = int(
            conn.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0]
        )
    finally:
        conn.close()
    after = resolved_db.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CleanupPlanError("database changed during candidate-link audit")
    if relation_count_before != relation_count_after:
        raise CleanupPlanError("event_relations changed during dry-run audit")
    if issuer_fact_true:
        raise CleanupPlanError("candidate-link invariant failed: issuer_fact became true")

    visible = int(aggregate["visible_rows"] or 0)
    structured_industry = int(aggregate["structured_industry_rows"] or 0)
    return {
        "ok": True,
        "mode": "immutable_dry_run",
        "asof": normalized_asof,
        "source_id": source,
        "visible_rows": visible,
        "structured_industry_linkable_rows": structured_industry,
        "structured_industry_coverage": (
            round(structured_industry / visible, 6) if visible else 0.0
        ),
        "structured_ticker_rows": int(aggregate["structured_ticker_rows"] or 0),
        "sampled_rows": len(sample_rows),
        "sample_unlinked_rows": unlinked,
        "sample_relation_statuses": dict(sorted(relation_statuses.items())),
        "sample_relation_types": dict(sorted(relation_types.items())),
        "sample_industry_targets": dict(sorted(industry_targets.items())),
        "persisted_relation_rows_before": relation_count_before,
        "persisted_relation_rows_after": relation_count_after,
        "persisted_rows_written": 0,
        "relation_policy": RELATION_POLICY,
        "issuer_fact": False,
        "interpretation": (
            "source category is an industry-context link only; user review is "
            "required for company relevance and event truth"
        ),
        "production_mutated": False,
    }


__all__ = ["MAX_LINK_AUDIT_SAMPLE", "audit_candidate_links"]
