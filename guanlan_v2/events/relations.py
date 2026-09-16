# -*- coding: utf-8 -*-
"""Auditable event-to-entity/industry *candidate* relations.

This module deliberately separates retrieval from factual verification.  A text
or alias hit is always ``candidate``; it can never become an issuer fact merely
because the words occur in a headline.  ``confirmed`` is accepted only from the
review table created by the events-store migration.  Even a confirmed relation
confirms the *link*, not the truth of the event's underlying claim.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


RELATION_POLICY = "keyword_hits_are_candidates_not_issuer_facts"
_HK = ZoneInfo("Asia/Hong_Kong")
_CODE_RE = re.compile(r"\b(?:SH|SZ|BJ)\d{6}\b", re.IGNORECASE)
_STATUS_PRIORITY = {
    "unlinked": 0,
    "rejected": 1,
    "candidate": 2,
    "source_asserted": 3,
    "confirmed": 4,
}
_TERMINAL_REPLAY_PRIORITY = {"confirmed": 0, "rejected": 1}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_clean(x) for x in value if _clean(x)]
    value = _clean(value)
    return [value] if value else []


def _target_keys(relation: Mapping[str, Any]) -> set[Tuple[str, str, str]]:
    """Return every stable identity carried by one relation target.

    P0 intentionally permits audited relations that only have ``target_name``.
    Generated catalog candidates usually carry both an id and a name, so keep
    both keys: a name-only human review can still suppress the matching derived
    candidate without inventing an issuer id.
    """
    relation_type = _clean(relation.get("relation_type"))
    if not relation_type:
        return set()
    keys: set[Tuple[str, str, str]] = set()
    target_id = _clean(relation.get("target_id"))
    target_name = _clean(relation.get("target_name"))
    if target_id:
        keys.add((relation_type, "id", target_id.casefold()))
    if target_name:
        keys.add((relation_type, "name", target_name.casefold()))
    return keys


def _target_group_key(relation: Mapping[str, Any]) -> Optional[Tuple[str, str, str]]:
    """Choose an id-first grouping key while retaining name-only targets."""
    keys = _target_keys(relation)
    return next((key for key in keys if key[1] == "id"),
                next(iter(sorted(keys)), None))


def _normalise_catalog(raw: Any) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Normalise the optional JSON catalog into entity/industry dictionaries.

    Accepted forms are intentionally small but forgiving::

        {"entities": [{"id": "SH600000", "name": "浦发银行", "aliases": [...] }],
         "industries": [{"id": "bank", "name": "银行", "aliases": [...] }]}

    A mapping ``{"SH600000": {"name": ..., "industry": ...}}`` is also
    accepted for local/private compatibility.
    """
    entities: Dict[str, Dict[str, Any]] = {}
    industries: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, Mapping):
        return entities, industries

    catalog_asof = _clean(raw.get("catalog_asof"))

    ent_raw = raw.get("entities")
    ind_raw = raw.get("industries")
    if ent_raw is None and ind_raw is None:
        ent_raw = {key: value for key, value in raw.items()
                   if key not in {"catalog_asof", "catalog_version", "metadata"}}

    def add_entity(obj: Any, fallback_id: str = "") -> None:
        if isinstance(obj, str):
            obj = {"name": obj}
        if not isinstance(obj, Mapping):
            return
        target_id = _clean(obj.get("id") or obj.get("code") or fallback_id).upper()
        if not target_id:
            return
        name = _clean(obj.get("name") or target_id)
        aliases = list(dict.fromkeys([name, target_id] + _as_list(obj.get("aliases"))))
        entities[target_id] = {
            "id": target_id,
            "name": name,
            "aliases": aliases,
            "industry": _clean(obj.get("industry")),
            "catalog_asof": _clean(obj.get("catalog_asof")) or catalog_asof,
        }

    if isinstance(ent_raw, Mapping):
        for key, value in ent_raw.items():
            add_entity(value, _clean(key))
    elif isinstance(ent_raw, Sequence) and not isinstance(ent_raw, (str, bytes)):
        for value in ent_raw:
            add_entity(value)

    def add_industry(obj: Any, fallback_id: str = "") -> None:
        if isinstance(obj, str):
            obj = {"name": obj}
        if not isinstance(obj, Mapping):
            return
        name = _clean(obj.get("name") or fallback_id)
        target_id = _clean(obj.get("id") or fallback_id or name)
        if not target_id or not name:
            return
        industries[target_id] = {
            "id": target_id,
            "name": name,
            "aliases": list(dict.fromkeys([name] + _as_list(obj.get("aliases")))),
            "catalog_asof": _clean(obj.get("catalog_asof")) or catalog_asof,
        }

    if isinstance(ind_raw, Mapping):
        for key, value in ind_raw.items():
            add_industry(value, _clean(key))
    elif isinstance(ind_raw, Sequence) and not isinstance(ind_raw, (str, bytes)):
        for value in ind_raw:
            add_industry(value)

    for entity in entities.values():
        ind = _clean(entity.get("industry"))
        if ind and ind not in industries:
            add_industry({"id": ind, "name": ind})
    return entities, industries


@functools.lru_cache(maxsize=1)
def _default_catalog() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load a local catalog without network access.

    An explicit ``GUANLAN_EVENT_RELATION_CATALOG`` JSON file wins.  Otherwise
    the existing local security master is used.  That master is a current
    reference rather than a historical PIT snapshot, so every bridge derived
    from it remains a low-confidence candidate and says so in its evidence.
    """
    configured = os.environ.get("GUANLAN_EVENT_RELATION_CATALOG")
    if configured:
        try:
            catalog_path = Path(configured)
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and not raw.get("catalog_asof"):
                raw = {**raw, "catalog_asof": datetime.fromtimestamp(
                    catalog_path.stat().st_mtime, tz=_HK
                ).replace(tzinfo=None).isoformat(timespec="seconds")}
            return _normalise_catalog(raw)
        except Exception:
            return {}, {}
    try:
        from guanlan_v2.strategy.ranking import name_industry_map

        raw = {
            # The current master has day-level, not intraday historical
            # availability.  Reads before today cannot use it for strict
            # filters; same-day use is explicitly labelled day-granular.
            "catalog_asof": datetime.now(_HK).date().isoformat() + "T00:00:00",
            "entities": {
                code: {"name": pair[0], "industry": pair[1]}
                for code, pair in name_industry_map().items()
            },
        }
        return _normalise_catalog(raw)
    except Exception:
        return {}, {}


def _catalogs(catalog: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    return _normalise_catalog(catalog) if catalog is not None else _default_catalog()


def catalog_snapshot(catalog: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return a path-free digest binding pagination to one catalog state."""
    entities, industries = _catalogs(catalog)
    canonical = {
        "entities": [entities[key] for key in sorted(entities)],
        "industries": [industries[key] for key in sorted(industries)],
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    asofs = sorted({
        _clean(item.get("catalog_asof"))
        for group in (entities, industries) for item in group.values()
        if _clean(item.get("catalog_asof"))
    })
    return {
        "digest": hashlib.sha256(raw).hexdigest(),
        "catalog_asofs": asofs,
        "entity_count": len(entities),
        "industry_count": len(industries),
    }


def _decode_evidence(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, Mapping)]
    if isinstance(raw, Mapping):
        return [dict(raw)]
    try:
        decoded = json.loads(str(raw))
    except (RecursionError, MemoryError):
        return [{"kind": "review_note", "value": "[REDACTED]"}]
    except (TypeError, ValueError, json.JSONDecodeError):
        return [{"kind": "review_note", "value": _clean(raw)[:300]}]
    return _decode_evidence(decoded)


def _effective_not_after(value: Any, asof: Optional[str]) -> bool:
    """Compare canonical-ish ISO timestamps without leaking future reviews."""
    if not asof:
        return True
    raw = _clean(value)
    if not raw:
        return False
    try:
        left = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        right = datetime.fromisoformat(_clean(asof).replace("Z", "+00:00"))
        left = left.replace(tzinfo=_HK) if left.tzinfo is None else left.astimezone(_HK)
        right = right.replace(tzinfo=_HK) if right.tzinfo is None else right.astimezone(_HK)
        return left <= right
    except ValueError:
        return False


def _catalog_pit(meta: Mapping[str, Any], asof: Optional[str]) -> Tuple[bool, str]:
    catalog_asof = _clean(meta.get("catalog_asof"))
    if not asof:
        # The low-level linker can be used outside a PIT read for previews and
        # tests.  Strict historical eligibility is enforced when asof is set.
        return True, catalog_asof
    return bool(catalog_asof and _effective_not_after(catalog_asof, asof)), catalog_asof


def _reviewed_relations(conn: Optional[sqlite3.Connection], event_id: str,
                        asof: Optional[str]) -> List[Dict[str, Any]]:
    if conn is None:
        return []
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(event_relations)")
        }
        optional = [name for name in ("relation_id", "created_at") if name in columns]
        rows = conn.execute(
            "SELECT relation_type,target_id,target_name,relation_status,confidence,"
            "evidence_json,relation_method,reviewed_by,reviewed_at"
            + ("," + ",".join(optional) if optional else "") + " "
            "FROM event_relations WHERE event_id=? "
            "ORDER BY relation_type,target_id,reviewed_at,relation_status",
            (event_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    visible: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        status = _clean(d.get("relation_status")) or "candidate"
        if status not in _STATUS_PRIORITY:
            status = "candidate"
        effective_at = (_clean(d.get("reviewed_at")) if status in {"confirmed", "rejected"}
                        else "") or _clean(d.get("created_at"))
        # P0 relation rows are append-only.  A historical read sees only rows
        # whose creation/review became available by the requested PIT.
        if asof and not _effective_not_after(effective_at, asof):
            continue
        evidence = _decode_evidence(d.get("evidence_json"))
        if not evidence:
            evidence = [{"kind": "review_record",
                         "reviewed_at": _clean(d.get("reviewed_at"))}]
        review = None
        if status in {"confirmed", "rejected"}:
            review = {
                "reviewed_by": _clean(d.get("reviewed_by")),
                "reviewed_at": _clean(d.get("reviewed_at")),
            }
            evidence = [
                {**item, "reviewed_by": review["reviewed_by"],
                 "reviewed_at": review["reviewed_at"]}
                for item in evidence
            ]
        relation = {
            "relation_type": _clean(d.get("relation_type")),
            "target_id": _clean(d.get("target_id")),
            "target_name": _clean(d.get("target_name")) or _clean(d.get("target_id")),
            "relation_status": status,
            "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0.0))),
            "relation_method": _clean(d.get("relation_method")) or "review_table",
            "evidence": evidence,
            "effective_at": effective_at,
            "filter_eligible": True,
            "issuer_fact": False,
        }
        if d.get("relation_id"):
            relation["relation_id"] = _clean(d.get("relation_id"))
        if review is not None:
            relation["review"] = review
        visible.append(relation)

    # Reviews are state transitions.  Select the latest terminal row visible
    # at the PIT for each exact target; retain non-terminal source evidence.
    latest_terminal: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for relation in visible:
        if relation["relation_status"] not in {"confirmed", "rejected"}:
            out.append(relation)
            continue
        key = _target_group_key(relation)
        if key is None:
            # Keep malformed legacy evidence visible instead of letting it
            # collide with every other missing-id relation.
            out.append(relation)
            continue
        current = latest_terminal.get(key)
        if current is None or (
            _clean(relation.get("effective_at")),
            _TERMINAL_REPLAY_PRIORITY.get(str(relation.get("relation_status")), -1),
            _clean(relation.get("relation_id")),
        ) >= (
            _clean(current.get("effective_at")),
            _TERMINAL_REPLAY_PRIORITY.get(str(current.get("relation_status")), -1),
            _clean(current.get("relation_id")),
        ):
            latest_terminal[key] = relation
    out.extend(latest_terminal.values())
    return out


def _relation(relation_type: str, target_id: str, target_name: str, status: str,
              confidence: float, method: str, evidence: Mapping[str, Any], *,
              filter_eligible: bool = True) -> Dict[str, Any]:
    return {
        "relation_type": relation_type,
        "target_id": target_id,
        "target_name": target_name or target_id,
        "relation_status": status,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "relation_method": method,
        "evidence": [dict(evidence)],
        "filter_eligible": bool(filter_eligible),
        "issuer_fact": False,
    }


def _merge_relations(relations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for relation in relations:
        target_key = _target_group_key(relation)
        if target_key is None:
            continue
        key = (*target_key, _clean(relation.get("relation_status")))
        current = merged.get(key)
        if current is None:
            merged[key] = relation
            continue
        current["confidence"] = max(float(current.get("confidence") or 0.0),
                                    float(relation.get("confidence") or 0.0))
        current["evidence"] = (current.get("evidence") or []) + (relation.get("evidence") or [])
    return sorted(merged.values(), key=lambda r: (
        r.get("relation_type", ""),
        -_STATUS_PRIORITY.get(str(r.get("relation_status")), 0),
        -float(r.get("confidence") or 0.0),
        r.get("target_id", "") or r.get("target_name", ""),
    ))


def _suppress_terminal_targets(relations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep terminal audit rows and suppress derived duplicates for each target."""
    terminal_reviews: set[Tuple[str, str, str]] = set()
    for relation in relations:
        if ("review" in relation
                and relation.get("relation_status") in {"confirmed", "rejected"}):
            terminal_reviews.update(_target_keys(relation))
    if not terminal_reviews:
        return relations
    return [
        relation for relation in relations
        if (not (_target_keys(relation) & terminal_reviews))
        or ("review" in relation
            and relation.get("relation_status") in {"confirmed", "rejected"})
    ]


def link_event_candidates(event: Mapping[str, Any], *,
                          conn: Optional[sqlite3.Connection] = None,
                          catalog: Optional[Mapping[str, Any]] = None,
                          asof: Optional[str] = None) -> Dict[str, Any]:
    """Return one event decorated with auditable candidate relations.

    The input mapping is copied.  Every returned item has the top-level fields
    ``relation_status``, ``confidence``, ``evidence`` and ``unlinked`` even when
    no relation can be found.
    """
    item = dict(event)
    event_id = _clean(item.get("event_id"))
    entities, industries = _catalogs(catalog)
    relations: List[Dict[str, Any]] = _reviewed_relations(conn, event_id, asof)

    tickers = [x.upper() for x in _as_list(item.get("tickers"))]
    for code in tickers:
        meta = entities.get(code, {})
        relations.append(_relation(
            "entity", code, _clean(meta.get("name")) or code,
            "source_asserted", 0.9, "structured_ticker_field",
            {"kind": "source_field", "field": "tickers", "value": code,
             "claim_scope": "event_link_only"},
        ))

    text_fields = {
        "title": _clean(item.get("title")),
        "summary": _clean(item.get("summary")),
    }
    combined = "\n".join(text_fields.values())
    upper_combined = combined.upper()

    # A code in prose is still a lexical candidate unless the source supplied
    # it in the structured ticker field.
    for match in _CODE_RE.finditer(upper_combined):
        code = match.group(0).upper()
        if code in tickers:
            continue
        meta = entities.get(code, {})
        relations.append(_relation(
            "entity", code, _clean(meta.get("name")) or code,
            "candidate", 0.65, "ticker_text_match",
            {"kind": "keyword_match", "field": "title_or_summary",
             "matched": code, "rule": RELATION_POLICY},
        ))

    for code, meta in entities.items():
        catalog_safe, catalog_asof = _catalog_pit(meta, asof)
        for alias in meta.get("aliases") or []:
            alias = _clean(alias)
            if len(alias) < 2 or alias.upper() == code or alias not in combined:
                continue
            field = "title" if alias in text_fields["title"] else "summary"
            relations.append(_relation(
                "entity", code, _clean(meta.get("name")) or code,
                "candidate", 0.55, "entity_alias_match",
                {"kind": "keyword_match", "field": field, "matched": alias,
                 "catalog_asof": catalog_asof, "pit_safe": catalog_safe,
                 "rule": RELATION_POLICY}, filter_eligible=catalog_safe,
            ))
            break

    event_type = _clean(item.get("event_type"))
    if event_type.lower().startswith("newsradar:"):
        sector = event_type.split(":", 1)[1].strip()
        if sector:
            relations.append(_relation(
                "industry", sector, sector, "source_asserted", 0.85,
                "source_sector_field",
                {"kind": "source_field", "field": "event_type", "value": event_type,
                 "claim_scope": "source_category_only"},
            ))

    for target_id, meta in industries.items():
        catalog_safe, catalog_asof = _catalog_pit(meta, asof)
        for alias in meta.get("aliases") or []:
            alias = _clean(alias)
            if len(alias) < 2 or alias not in combined:
                continue
            field = "title" if alias in text_fields["title"] else "summary"
            relations.append(_relation(
                "industry", target_id, _clean(meta.get("name")) or target_id,
                "candidate", 0.5, "industry_alias_match",
                {"kind": "keyword_match", "field": field, "matched": alias,
                 "catalog_asof": catalog_asof, "pit_safe": catalog_safe,
                 "rule": RELATION_POLICY}, filter_eligible=catalog_safe,
            ))
            break

    # Suppress reviewed entity duplicates before deriving industry bridges.  A
    # rejected entity must not spawn an industry candidate through a lexical
    # duplicate that the same review already overruled.
    relations = _suppress_terminal_targets(relations)

    # Entity -> current industry master is useful for discovery, but is not
    # historical PIT evidence.  Keep it low-confidence and visibly non-PIT.
    entity_relations = [r for r in relations
                        if r.get("relation_type") == "entity"
                        and r.get("relation_status") != "rejected"]
    for relation in entity_relations:
        meta = entities.get(_clean(relation.get("target_id")).upper(), {})
        industry = _clean(meta.get("industry"))
        if not industry:
            continue
        ind_meta = industries.get(industry, {"id": industry, "name": industry})
        catalog_safe, catalog_asof = _catalog_pit(ind_meta, asof)
        filter_eligible = bool(relation.get("filter_eligible", True)) and catalog_safe
        relations.append(_relation(
            "industry", _clean(ind_meta.get("id")) or industry,
            _clean(ind_meta.get("name")) or industry,
            "candidate", min(0.6, float(relation.get("confidence") or 0.0) * 0.65),
            "entity_current_industry_bridge",
            {"kind": "catalog_bridge", "from_entity": relation.get("target_id"),
             "catalog": "security_master", "catalog_asof": catalog_asof,
             "pit_safe": filter_eligible, "rule": RELATION_POLICY},
            filter_eligible=filter_eligible,
        ))

    # Apply terminal reviews after *all* derived relations exist, otherwise a
    # rejected industry could be resurrected by the entity->industry bridge.
    relations = _suppress_terminal_targets(relations)

    relations = _merge_relations(relations)
    active = [r for r in relations if r.get("relation_status") != "rejected"]
    if active:
        best = max(active, key=lambda r: (
            _STATUS_PRIORITY.get(str(r.get("relation_status")), 0),
            float(r.get("confidence") or 0.0),
        ))
        status = str(best.get("relation_status"))
        confidence = float(best.get("confidence") or 0.0)
        unlinked = False
    elif relations:
        best = max(relations, key=lambda r: float(r.get("confidence") or 0.0))
        status, confidence, unlinked = "rejected", float(best.get("confidence") or 0.0), False
    else:
        status, confidence, unlinked = "unlinked", 0.0, True

    evidence: List[Dict[str, Any]] = []
    for relation in relations:
        for ev in relation.get("evidence") or []:
            evidence.append({
                "relation_type": relation.get("relation_type"),
                "target_id": relation.get("target_id"),
                "relation_status": relation.get("relation_status"),
                **dict(ev),
            })
    item.update({
        "relations": relations,
        "relation_status": status,
        "confidence": round(confidence, 4),
        "evidence": evidence,
        "unlinked": unlinked,
        "relation_policy": RELATION_POLICY,
        "issuer_fact": False,
    })
    return item


def relation_matches(item: Mapping[str, Any], *,
                     industries: Sequence[str] = (), entities: Sequence[str] = ()) -> bool:
    """Apply relation filters without treating rejected links as matches."""
    wanted_industries = {str(x).strip().casefold() for x in industries if str(x).strip()}
    wanted_entities = {str(x).strip().casefold() for x in entities if str(x).strip()}
    if not wanted_industries and not wanted_entities:
        return True
    got_industries: set[str] = set()
    got_entities: set[str] = set()
    for relation in item.get("relations") or []:
        if (relation.get("relation_status") == "rejected"
                or relation.get("filter_eligible") is False):
            continue
        values = {
            _clean(relation.get("target_id")).casefold(),
            _clean(relation.get("target_name")).casefold(),
        }
        if relation.get("relation_type") == "industry":
            got_industries.update(v for v in values if v)
        elif relation.get("relation_type") == "entity":
            got_entities.update(v for v in values if v)
    return ((not wanted_industries or bool(wanted_industries & got_industries))
            and (not wanted_entities or bool(wanted_entities & got_entities)))


__all__ = [
    "RELATION_POLICY", "catalog_snapshot", "link_event_candidates", "relation_matches",
]
