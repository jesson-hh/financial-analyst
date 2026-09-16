"""Stable, bounded citation identities for report-derived evidence.

The LLM is allowed to emit only the opaque ``evidence_id``.  Exact source
references stay in a durable ledger and are rendered by the application, so a
conversation summary never has to reproduce a URL or document id from memory.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List


EVIDENCE_ID_RE = re.compile(r"^R-[0-9A-F]{12}$")


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def report_evidence_id(item: Dict[str, Any]) -> str:
    """Return a stable id derived from immutable report identity fields."""
    identity = "\x1f".join((
        _one_line(item.get("source_ref"), 600),
        _one_line(item.get("publish_ts"), 80),
        _one_line(item.get("institution"), 160),
        _one_line(item.get("title"), 360),
    ))
    return "R-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12].upper()


def normalise_report_evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a reader/writer row to the private, bounded ledger contract."""
    evidence_id = _one_line(item.get("evidence_id"), 32)
    if not EVIDENCE_ID_RE.fullmatch(evidence_id):
        evidence_id = report_evidence_id(item)
    alias = _one_line(item.get("citation_id"), 24)
    return {
        "evidence_id": evidence_id,
        "kind": "research_report",
        "title": _one_line(item.get("title"), 240),
        "institution": _one_line(item.get("institution"), 120),
        "publish_ts": _one_line(item.get("publish_ts"), 48),
        "source_ref": _one_line(item.get("source_ref"), 600),
        "source_kind": _one_line(item.get("source_kind"), 80),
        "view": _one_line(item.get("view"), 700),
        "trust": "untrusted_data",
        "aliases": [alias] if alias else [],
    }

def normalise_report_evidence_many(items: Iterable[Any], *, limit: int = 24) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = normalise_report_evidence(raw)
        evidence_id = item["evidence_id"]
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def public_evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    """Browser-safe citation metadata.  The report excerpt is intentionally private."""
    return {
        key: item.get(key)
        for key in (
            "evidence_id", "kind", "title", "institution", "publish_ts",
            "source_ref", "source_kind", "aliases", "first_event_id", "last_event_id",
        )
    }
