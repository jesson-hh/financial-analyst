# -*- coding: utf-8 -*-
"""Explicit, auditable import adapters for legacy company-event files.

Nothing in this module runs at server start or during a read.  Call
``import_legacy_events`` (normally through ``scripts/migrate_events_p0.py``) to
write.  Supported inputs are a single ``events.json`` and a PIT store shaped as
``pit_store/<YYYY-MM-DD>/events.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from guanlan_v2.events import store

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^(?:(SH|SZ|BJ))?(\d{6})$", re.IGNORECASE)
_CATEGORY_KEYS = frozenset({
    "positive", "negative", "calendar", "neutral", "risk", "risks",
    "announcement", "announcements", "company_event", "company_events",
})
_EVENT_MARKERS = frozenset({
    "title", "summary", "body", "type", "event_type", "date", "time", "ts",
    "event_time", "event_date", "ann_date", "available_at", "visible_ts",
})
_SNAPSHOT_KEYS = (
    "snapshot_at", "snapshot_ts", "snapshot_date", "snapshot", "fetched_at",
    "fetched_ts", "fetched", "fetch_time", "scanned_at",
)
_WRAPPER_KEYS = ("events", "company_events")
_INHERITED_KEYS = (
    "code", "ticker", "symbol", "category", "source", "visible_ts",
    "available_at", *_SNAPSHOT_KEYS,
)
_INHERITED_FIELD_KEYS = ("visible_ts", *_SNAPSHOT_KEYS)


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(source_kind: str, payload: Mapping[str, Any]) -> str:
    material = {"source_kind": source_kind, "payload": payload}
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _logical_locator(path: Path, partition_date: Optional[str]) -> str:
    if partition_date:
        return f"pit_store/{partition_date}/{path.name}"
    return path.name


def _source_kind(path: Path, partition_date: Optional[str]) -> str:
    if path.suffix.lower() == ".jsonl" or partition_date:
        return "pit_store_jsonl"
    return "legacy_company_events_json"


def _partition_date(path: Path) -> Optional[str]:
    return path.parent.name if _DATE_RE.fullmatch(path.parent.name) else None


def discover_legacy_event_files(path: str | Path) -> tuple[Path, ...]:
    """Discover only the two reviewed legacy layouts, in deterministic order."""
    root = Path(path)
    if root.is_file():
        if root.name not in {"events.json", "events.jsonl"}:
            raise ValueError(f"unsupported legacy event file: {root.name}")
        return (root,)
    if not root.exists():
        raise FileNotFoundError(root)
    found = []
    direct = root / "events.json"
    if direct.is_file():
        found.append(direct)
    direct_jsonl = root / "events.jsonl"
    if direct_jsonl.is_file():
        found.append(direct_jsonl)
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir() and _DATE_RE.fullmatch(child.name):
            candidate = child / "events.jsonl"
            if candidate.is_file():
                found.append(candidate)
    if not found:
        raise FileNotFoundError(f"no supported legacy events beneath {root}")
    return tuple(found)


def _looks_like_event(node: Mapping[str, Any]) -> bool:
    return bool(_EVENT_MARKERS.intersection(node)) and not any(
        key in node for key in _WRAPPER_KEYS
    )


def _flatten_node(
    node: Any,
    inherited: Optional[Mapping[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    inherited_dict = dict(inherited or {})
    if isinstance(node, str):
        text = node.strip()
        if text:
            yield {**inherited_dict, "title": text}
        return
    if isinstance(node, list):
        for item in node:
            yield from _flatten_node(item, inherited_dict)
        return
    if not isinstance(node, Mapping):
        return

    local = dict(inherited_dict)
    for key in _INHERITED_KEYS:
        if node.get(key) not in (None, ""):
            local[key] = node[key]

    fields = node.get("fields")
    if isinstance(fields, Mapping):
        inherited_fields = local.get("fields")
        safe_fields = dict(inherited_fields) if isinstance(inherited_fields, Mapping) else {}
        for key in _INHERITED_FIELD_KEYS:
            if fields.get(key) not in (None, ""):
                safe_fields[key] = fields[key]
        if safe_fields:
            local["fields"] = safe_fields

    # A document-level availability marker is metadata for its wrapped rows,
    # not a standalone event.  Expand wrappers before classifying event nodes.
    handled = False
    for wrapper in _WRAPPER_KEYS:
        if wrapper in node:
            handled = True
            yield from _flatten_node(node[wrapper], local)
    if handled:
        return

    if _looks_like_event(node):
        event = dict(node)
        for key, value in local.items():
            event.setdefault(key, value)
        yield event
        return

    for key, value in node.items():
        if key in {*_WRAPPER_KEYS, *_INHERITED_KEYS, "fields"}:
            continue
        match = _CODE_RE.fullmatch(str(key))
        if match and isinstance(value, (Mapping, list)):
            handled = True
            child = dict(local)
            child["code"] = str(key)
            yield from _flatten_node(value, child)
        elif str(key).lower() in _CATEGORY_KEYS and isinstance(value, (Mapping, list)):
            handled = True
            child = dict(local)
            child["category"] = str(key)
            yield from _flatten_node(value, child)
    if not handled and node:
        # Unknown wrappers are not guessed into events.  This prevents metadata
        # dictionaries (especially ``fields``) from becoming fabricated rows.
        return


def load_legacy_file(path: str | Path) -> tuple[Dict[str, Any], ...]:
    """Parse and flatten one reviewed legacy file without writing anything."""
    source = Path(path)
    if source.name == "events.json":
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        return tuple(_flatten_node(document))
    if source.name != "events.jsonl":
        raise ValueError(f"unsupported legacy event file: {source.name}")
    rows = []
    with source.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid {source.name} line {line_number}: {exc.msg}"
                ) from exc
            rows.extend(_flatten_node(document))
    return tuple(rows)


def iter_legacy_records(path: str | Path) -> Iterator[Dict[str, Any]]:
    """Yield flattened records plus path-free logical source metadata."""
    for source in discover_legacy_event_files(path):
        partition = _partition_date(source)
        kind = _source_kind(source, partition)
        locator = _logical_locator(source, partition)
        mtime = source.stat().st_mtime
        for item_index, payload in enumerate(load_legacy_file(source), 1):
            yield {
                "payload": payload,
                "source_kind": kind,
                "source_locator": locator,
                "partition_date": partition,
                "file_mtime": mtime,
                "item_index": item_index,
            }


def _first(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_possible_epoch(value: Any, *, date_end: bool = False) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return store.normalize_timestamp(dt)
    raw = str(value).strip()
    if re.fullmatch(r"\d{10}(?:\.\d+)?", raw):
        dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        return store.normalize_timestamp(dt)
    return store.normalize_timestamp(value, date_end=date_end)


def derive_available_at(
    payload: Mapping[str, Any],
    *,
    partition_date: Optional[str],
    file_mtime: float,
) -> tuple[str, str]:
    """Derive conservative availability without ever consulting event_time.

    Priority is ``fields.visible_ts`` → explicit ``available_at`` → snapshot or
    fetch clock → PIT partition date → file mtime.  Date-only evidence maps to
    Hong Kong local end-of-day.
    """
    fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
    candidates = [
        ("fields.visible_ts", fields.get("visible_ts")),
        ("visible_ts", payload.get("visible_ts")),
        ("available_at", payload.get("available_at")),
    ]
    for key in _SNAPSHOT_KEYS:
        candidates.append((key, payload.get(key)))
        candidates.append((f"fields.{key}", fields.get(key)))
    for basis, value in candidates:
        if value in (None, ""):
            continue
        if isinstance(value, Mapping):
            value = _first(value, ("visible_ts", "available_at", "at", "ts", "time", "date"))
            if value in (None, ""):
                continue
        raw = str(value).strip()
        return _normalize_possible_epoch(
            value, date_end=bool(_DATE_RE.fullmatch(raw))
        ), basis
    if partition_date:
        return store.normalize_timestamp(partition_date, date_end=True), "partition_date"
    mtime = datetime.fromtimestamp(file_mtime, tz=store.HONG_KONG).replace(tzinfo=None)
    return store.normalize_timestamp(mtime), "file_mtime"


def _normalize_code(value: Any) -> Optional[str]:
    raw = str(value or "").strip().upper()
    match = _CODE_RE.fullmatch(raw)
    if not match:
        return raw or None
    market, digits = match.groups()
    if market:
        return market.upper() + digits
    if digits.startswith(("43", "83", "87", "92")):
        return "BJ" + digits
    if digits[0] in "569":
        return "SH" + digits
    return "SZ" + digits


def _extract_codes(payload: Mapping[str, Any]) -> list[str]:
    raw = _first(payload, ("tickers", "codes", "code", "ticker", "symbol"))
    if raw is None:
        return []
    if isinstance(raw, str):
        values = re.split(r"[,;\s]+", raw.strip())
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [raw]
    out = []
    for value in values:
        code = _normalize_code(value)
        if code and code not in out:
            out.append(code)
    return out


def _event_time(payload: Mapping[str, Any]) -> Any:
    fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
    value = _first(payload, ("event_time", "time", "ts", "date", "event_date", "trade_date"))
    if value is None:
        value = _first(fields, ("event_time", "event_date", "trade_date"))
    if value is None:
        value = payload.get("ann_date")
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    raw = str(value).strip()
    if re.fullmatch(r"\d{10}(?:\.\d+)?", raw):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    return value


def _safe_source_ref(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    raw = str(value)
    if Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        return PureWindowsPath(raw).name
    return raw


def _event_row(record: Mapping[str, Any]) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
    payload = dict(record["payload"])
    digest = _digest(str(record["source_kind"]), payload)
    available_at, availability_basis = derive_available_at(
        payload, partition_date=record.get("partition_date"),
        file_mtime=float(record["file_mtime"]),
    )
    event_time = _event_time(payload)
    category = str(payload.get("category") or "").strip()
    event_type = str(
        payload.get("event_type") or payload.get("type")
        or (f"company:{category}" if category else "legacy_company_event")
    )
    title = str(payload.get("title") or "").strip() or None
    summary = str(payload.get("summary") or payload.get("body") or "").strip() or None
    provenance = {
        "adapter": "events.compat.p0",
        "source_kind": record["source_kind"],
        "source_locator": record["source_locator"],
        "partition_date": record.get("partition_date"),
        "item_index": record["item_index"],
        "record_digest": digest,
        "availability_basis": availability_basis,
    }
    classification, reason = store.classify_event_time(
        event_time, available_at, event_type=event_type, title=title, payload=payload
    )
    row = {
        "event_id": "legacy:" + digest,
        "event_type": event_type,
        "tickers": _extract_codes(payload),
        "title": title,
        "summary": summary,
        "source": payload.get("source") or record["source_kind"],
        "source_ref": _safe_source_ref(
            payload.get("source_ref") or payload.get("url")
        ),
        "event_time": event_time,
        "available_at": available_at,
        "ingested_from": "legacy_compat",
        "time_classification": classification,
        "classification_reason": reason,
        "payload_json": payload,
        "provenance_json": provenance,
    }
    return row, digest, provenance


def import_legacy_events(conn: sqlite3.Connection, path: str | Path) -> Dict[str, Any]:
    """Explicitly import one legacy source with per-record digest idempotency."""
    store.init_db(conn)
    report: Dict[str, Any] = {
        "ok": True, "files": 0, "scanned": 0, "inserted": 0, "skipped": 0,
        "relations": 0,
        "time_classifications": {
            "observed": 0, "scheduled": 0, "time_conflict": 0,
        },
    }
    files = discover_legacy_event_files(path)
    records = list(iter_legacy_records(path))
    report["files"] = len(files)
    imported_at = store.normalize_timestamp(
        datetime.now(store.HONG_KONG).replace(tzinfo=None)
    )
    classified_digests: set[str] = set()
    with conn:
        for record in records:
            report["scanned"] += 1
            row, digest, provenance = _event_row(record)
            if digest not in classified_digests:
                report["time_classifications"][row["time_classification"]] += 1
                classified_digests.add(digest)
            exists = conn.execute(
                "SELECT event_id FROM legacy_imports WHERE record_digest=?", (digest,)
            ).fetchone()
            if exists is not None:
                report["skipped"] += 1
                continue
            if store.insert_event(conn, row):
                report["inserted"] += 1

            for code in row["tickers"]:
                relation_key = f"{row['event_id']}\x1fentity\x1f{code}\x1fsource_asserted"
                relation_id = hashlib.sha256(relation_key.encode("utf-8")).hexdigest()
                if store.insert_event_relation(conn, {
                    "relation_id": relation_id,
                    "event_id": row["event_id"],
                    "relation_type": "entity",
                    "target_id": code,
                    "target_name": None,
                    "relation_status": "source_asserted",
                    "confidence": 1.0,
                    "evidence": {
                        "record_digest": digest,
                        "basis": "explicit_code_field_or_container",
                    },
                    "relation_method": "legacy_explicit_code",
                    "created_at": row["available_at"],
                }):
                    report["relations"] += 1

            import_id = hashlib.sha256(
                ("events-legacy-import-v1\x1f" + digest).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """INSERT INTO legacy_imports(
                       import_id,record_digest,event_id,source_kind,source_locator,
                       partition_date,imported_at,provenance_json
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    import_id, digest, row["event_id"], record["source_kind"],
                    record["source_locator"], record.get("partition_date"), imported_at,
                    _canonical(store.sanitize_provenance(provenance)),
                ),
            )
    return report


__all__ = [
    "derive_available_at", "discover_legacy_event_files", "import_legacy_events",
    "iter_legacy_records", "load_legacy_file",
]
