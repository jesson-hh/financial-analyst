# -*- coding: utf-8 -*-
"""Deterministic, auditable market-clue aggregation over the events store."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from guanlan_v2.events import store
from guanlan_v2.events.relations import RELATION_POLICY
from guanlan_v2.events.research import (
    EventsSearchError,
    _strings,
    sanitize_outbound,
    search_events_page,
)


_RISK_TERMS = (
    "立案", "调查", "处罚", "警示函", "问询函", "减持", "预亏", "业绩预减",
    "退市", "诉讼", "冻结", "质押", "商誉减值", "风险提示", "跌停", "炸板",
)
_LIMIT_TERMS = ("涨停原因", "涨停理由", "涨停", "封板", "连板")
_LIMIT_TYPES = ("limit_up", "limitup", "zt_", "harden", "涨停")
_RISK_TYPES = ("negative", "risk", "warning", "penalty", "investigation")
_STATUS_PRIORITY = {"unlinked": 0, "rejected": 1, "candidate": 2,
                    "source_asserted": 3, "confirmed": 4}
_MAX_GROUP_EVIDENCE = 20


def _bounded_int(value: Any, *, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise EventsSearchError(f"invalid_{name}", f"{name} 必须在 {low}..{high} 之间")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EventsSearchError(f"invalid_{name}", f"{name} 必须在 {low}..{high} 之间") from exc
    if not low <= result <= high:
        raise EventsSearchError(f"invalid_{name}", f"{name} 必须在 {low}..{high} 之间")
    return result


def _relation_summary(item: Mapping[str, Any]) -> Dict[str, Any]:
    relations = item.get("relations")
    if isinstance(relations, (list, tuple)):
        eligible = [
            relation for relation in relations
            if isinstance(relation, Mapping)
            and relation.get("filter_eligible") is not False
        ]
        active = [relation for relation in eligible
                  if relation.get("relation_status") != "rejected"]
        candidates = active or eligible
        if candidates:
            winning = max(candidates, key=lambda relation: (
                _STATUS_PRIORITY.get(str(relation.get("relation_status") or "unlinked"), 0),
                float(relation.get("confidence") or 0.0),
                str(relation.get("target_id") or relation.get("target_name") or ""),
            ))
            status = str(winning.get("relation_status") or "unlinked")
            confidence = float(winning.get("confidence") or 0.0)
            unlinked = False
        else:
            status, confidence, unlinked = "unlinked", 0.0, True
        evidence = []
        for relation in eligible:
            for proof in relation.get("evidence") or []:
                if not isinstance(proof, Mapping):
                    continue
                evidence.append({
                    "relation_type": relation.get("relation_type"),
                    "target_id": relation.get("target_id"),
                    "target_name": relation.get("target_name"),
                    "relation_status": relation.get("relation_status"),
                    **dict(proof),
                })
    else:
        status = str(item.get("relation_status") or "unlinked")
        confidence = float(item.get("confidence") or 0.0)
        unlinked = bool(item.get("unlinked", True))
        evidence = list(item.get("evidence") or [])
    return {
        "relation_status": status,
        "confidence": confidence,
        "evidence": evidence[:_MAX_GROUP_EVIDENCE],
        "evidence_count": len(evidence),
        "evidence_truncated": len(evidence) > _MAX_GROUP_EVIDENCE,
        "unlinked": unlinked,
        "issuer_fact": False,
    }


def _event_ref(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": item.get("event_id"),
        "line_id": item.get("line_id") or item.get("event_id"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "event_type": item.get("event_type"),
        "event_time": item.get("event_time"),
        "available_at": item.get("available_at"),
        "time_classification": item.get("time_classification"),
        "provenance": item.get("provenance"),
        **_relation_summary(item),
    }


def _active_relations(item: Mapping[str, Any], relation_type: str) -> Iterable[Mapping[str, Any]]:
    for relation in item.get("relations") or []:
        if (relation.get("relation_type") == relation_type
                and relation.get("relation_status") != "rejected"
                and relation.get("filter_eligible") is not False):
            yield relation


def _hotspots(items: Sequence[Mapping[str, Any]], topn: int) -> List[Dict[str, Any]]:
    # One event line can be republished by many sources.  Score each
    # (industry,line) once, using the strongest auditable relation on the line;
    # source breadth remains a separate signal instead of multiplying score.
    per_line: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in items:
        for relation in _active_relations(item, "industry"):
            industry_id = str(relation.get("target_id") or "")
            if not industry_id:
                continue
            event_id = str(item.get("event_id") or "")
            line_id = str(item.get("line_id") or event_id)
            key = (industry_id, line_id)
            candidate = {
                "industry_id": industry_id,
                "industry": str(relation.get("target_name") or industry_id),
                "line_id": line_id,
                "event_id": event_id,
                "event_ids": {event_id},
                "sources": {str(item.get("source") or item.get("ingested_from") or "unknown")},
                "confidence": float(relation.get("confidence") or 0.0),
                "relation_status": str(relation.get("relation_status") or "candidate"),
                "relation_method": relation.get("relation_method"),
                "evidence": relation.get("evidence") or [],
            }
            current = per_line.get(key)
            rank = (_STATUS_PRIORITY.get(candidate["relation_status"], 0),
                    candidate["confidence"], candidate["event_id"])
            current_rank = ((-1, -1.0, "") if current is None else (
                _STATUS_PRIORITY.get(current["relation_status"], 0),
                current["confidence"], current["event_id"],
            ))
            if current is None:
                per_line[key] = candidate
            else:
                current["event_ids"].add(event_id)
                current["sources"].update(candidate["sources"])
                if rank > current_rank:
                    kept_event_ids = current["event_ids"]
                    kept_sources = current["sources"]
                    current.update(candidate)
                    current["event_ids"] = kept_event_ids
                    current["sources"] = kept_sources

    groups: Dict[str, Dict[str, Any]] = {}
    for candidate in per_line.values():
        industry_id = candidate["industry_id"]
        group = groups.setdefault(industry_id, {
            "industry_id": industry_id, "names": set(), "event_ids": set(),
            "line_ids": set(), "sources": set(), "score": 0.0,
            "relations": [], "evidence": [],
        })
        group["names"].add(candidate["industry"])
        group["event_ids"].update(candidate["event_ids"])
        group["line_ids"].add(candidate["line_id"])
        group["sources"].update(candidate["sources"])
        group["score"] += candidate["confidence"]
        group["relations"].append(candidate)
        group["evidence"].append({
            "event_id": candidate["event_id"], "line_id": candidate["line_id"],
            "relation_method": candidate["relation_method"],
            "relation_status": candidate["relation_status"],
            "evidence": candidate["evidence"],
        })
    result: List[Dict[str, Any]] = []
    for group in groups.values():
        winning = max(group["relations"], key=lambda r: (
            _STATUS_PRIORITY.get(r["relation_status"], 0), r["confidence"], r["event_id"]
        ))
        evidence_count = len(group["evidence"])
        result.append({
            "industry_id": group["industry_id"],
            "industry": sorted(group["names"])[0] if group["names"] else group["industry_id"],
            "score": round(group["score"], 4),
            "event_count": len(group["event_ids"]),
            "line_count": len(group["line_ids"]),
            "source_count": len(group["sources"]),
            "sources": sorted(group["sources"]),
            "relation_status": winning["relation_status"],
            "aggregate_status": "candidate_aggregate",
            "confidence": round(winning["confidence"], 4),
            "evidence": group["evidence"][:_MAX_GROUP_EVIDENCE],
            "evidence_count": evidence_count,
            "evidence_truncated": evidence_count > _MAX_GROUP_EVIDENCE,
            "unlinked": False,
            "issuer_fact": False,
        })
    return sorted(result, key=lambda x: (-x["score"], -x["line_count"],
                                         x["industry_id"]))[:topn]


def _event_lines(items: Sequence[Mapping[str, Any]], topn: int) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item.get("line_id") or item.get("event_id") or "unidentified")].append(item)
    result: List[Dict[str, Any]] = []
    for line_id, rows in groups.items():
        rows = sorted(rows, key=lambda x: (str(x.get("available_at") or ""),
                                           str(x.get("event_id") or "")), reverse=True)
        relation_summaries = [_relation_summary(row) for row in rows]
        winning = max(relation_summaries, key=lambda summary: (
            _STATUS_PRIORITY.get(str(summary.get("relation_status") or "unlinked"), 0),
            float(summary.get("confidence") or 0.0),
        ))
        status = str(winning.get("relation_status") or "unlinked")
        evidence_count = len(rows)
        result.append({
            "line_id": line_id,
            "event_count": len(rows),
            "is_reprint_line": len(rows) > 1,
            "first_available_at": min(str(r.get("available_at") or "") for r in rows),
            "last_available_at": max(str(r.get("available_at") or "") for r in rows),
            "sources": sorted({str(r.get("source") or r.get("ingested_from") or "unknown")
                               for r in rows}),
            "representative": _event_ref(rows[0]),
            "relation_status": status,
            "confidence": round(float(winning.get("confidence") or 0.0), 4),
            "evidence": [_event_ref(row) for row in rows[:_MAX_GROUP_EVIDENCE]],
            "evidence_count": evidence_count,
            "evidence_truncated": evidence_count > _MAX_GROUP_EVIDENCE,
            "unlinked": status == "unlinked",
            "issuer_fact": False,
        })
    # Stable multi-key ordering: activity desc, source breadth desc, recency
    # desc, then line id asc.  Repeated stable sorts keep the final tie-break
    # deterministic without encoding descending timestamps into a cursor.
    result.sort(key=lambda x: x["line_id"])
    result.sort(key=lambda x: x["last_available_at"], reverse=True)
    result.sort(key=lambda x: len(x["sources"]), reverse=True)
    result.sort(key=lambda x: x["event_count"], reverse=True)
    return result[:topn]


def _matched_terms(text: str, terms: Sequence[str]) -> List[str]:
    return [term for term in terms if term in text]


def _clue_items(items: Sequence[Mapping[str, Any]], *, kind: str,
                topn: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_lines: set[str] = set()
    for item in items:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}"
        event_type = str(item.get("event_type") or "").casefold()
        if kind == "limit_up_reason":
            terms = _matched_terms(text, _LIMIT_TERMS)
            typed = any(marker in event_type for marker in _LIMIT_TYPES)
        else:
            terms = _matched_terms(text, _RISK_TERMS)
            typed = any(marker in event_type for marker in _RISK_TYPES)
        if not terms and not typed:
            continue
        line_id = str(item.get("line_id") or item.get("event_id") or "")
        if line_id in seen_lines:
            continue
        seen_lines.add(line_id)
        relation = _relation_summary(item)
        out.append({
            "clue_type": kind,
            "event_id": item.get("event_id"),
            "line_id": line_id,
            "text": item.get("title") or item.get("summary"),
            "matched_terms": terms,
            "basis": "structured_event_type" if typed else "lexical_candidate",
            "event_type": item.get("event_type"),
            "available_at": item.get("available_at"),
            "event_time": item.get("event_time"),
            "provenance": item.get("provenance"),
            **relation,
        })
        if len(out) >= topn:
            break
    return out


def _collect(asof: Any, *, window_days: int, max_events: int,
             industry: Any, industries: Any, entity: Any, entities: Any,
             conn: Any, path: Any, catalog: Optional[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        canonical = store.normalize_asof(asof)
    except (TypeError, ValueError) as exc:
        raise EventsSearchError("invalid_asof", str(exc)) from exc
    asof_dt = datetime.fromisoformat(canonical)
    floor = (asof_dt - timedelta(days=window_days)).isoformat()
    rows: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    retrieval: Dict[str, Any] = {}
    own = conn is None
    if own:
        try:
            conn = store.connect_readonly(path=path)
            conn.execute("BEGIN")
        except Exception as exc:
            if conn is not None:
                conn.close()
            raise EventsSearchError(
                "events_db_unavailable", "events.db 无法以只读方式打开"
            ) from exc
    try:
        while len(rows) < max_events:
            page = search_events_page(
                "", canonical, industry=industry, industries=industries,
                entity=entity, entities=entities, limit=min(100, max_events - len(rows)),
                cursor=cursor, conn=conn, path=path, catalog=catalog,
                available_from=floor,
            )
            rows.extend(page["items"])
            retrieval = page.get("retrieval") or retrieval
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows, {"asof": canonical, "available_from": floor,
                      "retrieval": retrieval, "truncated": bool(cursor)}
    finally:
        if own and conn is not None:
            conn.rollback()
            conn.close()


def market_clues(asof: Any = None, *, window_days: int = 7,
                 industry: Any = None, industries: Any = None,
                 entity: Any = None, entities: Any = None,
                 limit: int = 100, conn: Any = None, path: Any = None,
                 catalog: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate PIT-visible hotspots, lines, limit-up reasons and risk clues.

    ``time_conflict`` legacy rows are quarantined from all four aggregates.
    Explicitly scheduled future events are returned under ``scheduled`` rather
    than being presented as events that already happened.
    """
    try:
        # Enforce resource bounds before _collect opens or queries a database.
        _strings(industry, industries, name="industry")
        _strings(entity, entities, name="entity")
        window = _bounded_int(window_days, name="window_days", low=1, high=365)
        clue_limit = _bounded_int(limit, name="limit", low=1, high=500)
        # ``limit`` is an output-per-section bound, not a claim that the latest
        # N raw rows represent the market.  Scan a wider, bounded evidence pool.
        max_events = min(5000, max(200, clue_limit * 20))
        rows, meta = _collect(
            asof, window_days=window, max_events=max_events,
            industry=industry, industries=industries, entity=entity, entities=entities,
            conn=conn, path=path, catalog=catalog,
        )
        conflicts = [row for row in rows if row.get("time_classification") == "time_conflict"]
        scheduled = [row for row in rows if row.get("time_classification") == "scheduled"]
        eligible = [row for row in rows
                    if row.get("time_classification") not in {"time_conflict", "scheduled"}]
        topn = clue_limit
        return sanitize_outbound({
            "ok": True,
            "asof": meta["asof"],
            "window_days": window,
            "available_from": meta["available_from"],
            "events_considered": len(rows),
            "eligible_events": len(eligible),
            "hotspots": _hotspots(eligible, topn),
            "event_lines": _event_lines(eligible, topn),
            "limit_up_reasons": _clue_items(eligible, kind="limit_up_reason", topn=topn),
            "risk_clues": _clue_items(eligible, kind="risk_clue", topn=topn),
            "scheduled": [_event_ref(row) for row in scheduled[:topn]],
            "quarantined": [{**_event_ref(row),
                              "quarantine_reason": row.get("classification_reason")}
                             for row in conflicts[:topn]],
            "retrieval": meta["retrieval"],
            "truncated": meta["truncated"],
            "relation_policy": RELATION_POLICY,
            "issuer_fact": False,
            "honesty_note": (
                "热点、涨停原因与风险均为事件候选线索；关键词命中不会自动升级为发行人事实。"
                "逐条保留 relation_status/confidence/evidence，time_conflict 已隔离。"
            ),
        })
    except EventsSearchError as exc:
        return {"ok": False, "hotspots": [], "event_lines": [],
                "limit_up_reasons": [], "risk_clues": [], "scheduled": [],
                "quarantined": [],
                "error": {"code": exc.code, "message": exc.message},
                "relation_policy": RELATION_POLICY, "issuer_fact": False}
    except Exception:  # noqa: BLE001 - stable tool boundary
        return {"ok": False, "hotspots": [], "event_lines": [],
                "limit_up_reasons": [], "risk_clues": [], "scheduled": [],
                "quarantined": [],
                "error": {"code": "market_clues_internal_error",
                          "message": "市场线索聚合失败"},
                "relation_policy": RELATION_POLICY, "issuer_fact": False}


__all__ = ["market_clues"]
