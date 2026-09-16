"""Bounded, read-only workspace history for Luozi decisions.

The research workspace is the ownership boundary.  This module deliberately
reads only the already-curated ``workspace_memory/<workspace>/memory.md`` file;
it never scans conversation logs, workspace files, reports, or the shared
archive.  The returned ``ref`` is safe to persist because it contains only
provenance and a digest, never the private summary text.

Workspace memory lines are written by ``console.tools.memory_write_impl`` as::

    - [YYYY-MM-DD] (optional-key) condensed text

Malformed/continuation lines are ignored.  Historical replay is conservative:
for both daily and intraday decisions a line must predate the decision day.
This avoids treating a same-day note with no time-of-day as information that
was already available at the replay instant.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.console.workspaces import normalize_security_code


MAX_ITEMS = 3
MAX_TEXT_CHARS = 700

_MEMORY_LINE_RE = re.compile(
    r"^- \[(?P<date>\d{4}-\d{2}-\d{2})\]"
    r"(?: \((?P<key>[^()\r\n]+)\))? (?P<text>\S.*)$"
)


def _empty_ref(*, code: str, asof: Optional[str], reason: str,
               workspace_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "kind": "workspace_history",
        "workspace_id": workspace_id,
        "code": code,
        "asof": asof,
        "item_count": 0,
        "latest_date": None,
        "digest": None,
        "reason": reason,
    }


def _empty(*, code: str, asof: Optional[str], reason: str,
           workspace_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "items": [],
        "text": "",
        "ref": _empty_ref(
            code=code, asof=asof, reason=reason, workspace_id=workspace_id,
        ),
    }


def _asof_day(value: Any) -> Optional[str]:
    raw = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError):
        return None


def _render(item: Dict[str, Any]) -> str:
    key = f" ({item['key']})" if item.get("key") else ""
    return f"- [{item['date']}]{key} {item['text']}"


def _parse_memory(text: str, *, asof: str, replay: bool) -> List[Dict[str, Any]]:
    """Parse complete physical lines and keep the newest row for each key."""
    parsed: List[Dict[str, Any]] = []
    for index, raw in enumerate(text.splitlines()):
        match = _MEMORY_LINE_RE.fullmatch(raw.strip())
        if match is None:
            continue
        day = _asof_day(match.group("date"))
        body = match.group("text").strip()
        key = (match.group("key") or "").strip() or None
        if day is None or not body:
            continue
        # Live/current decisions may use notes available on the as-of day, but
        # never a future-dated row.  Replay has no time-of-day provenance, so a
        # same-day row is conservatively excluded.
        if day > asof or (replay and day >= asof):
            continue
        parsed.append({"date": day, "key": key, "text": body, "_index": index})

    latest_by_key: Dict[str, Dict[str, Any]] = {}
    unkeyed: List[Dict[str, Any]] = []
    for item in parsed:
        key = item.get("key")
        if key is None:
            unkeyed.append(item)
            continue
        previous = latest_by_key.get(key)
        if previous is None or (item["date"], item["_index"]) > (
                previous["date"], previous["_index"]):
            latest_by_key[key] = item

    candidates = [*latest_by_key.values(), *unkeyed]
    candidates.sort(key=lambda item: (item["date"], item["_index"]), reverse=True)

    selected: List[Dict[str, Any]] = []
    used = 0
    for item in candidates:
        public = {key: item.get(key) for key in ("date", "key", "text")}
        line = _render(public)
        added = len(line) + (1 if selected else 0)
        if len(selected) >= MAX_ITEMS:
            break
        if used + added > MAX_TEXT_CHARS:
            # Preserve whole memory rows.  An oversized recent row must not
            # prevent a smaller, still-relevant older row from being selected.
            continue
        selected.append(public)
        used += added
    return selected


def build_workspace_history_brief(
    console_store: Any,
    code: Any,
    asof: Any,
    replay: bool = False,
    freq: str = "day",
) -> Dict[str, Any]:
    """Return a bounded summary from the one active workspace for ``code``.

    The function is intentionally fail-closed and side-effect free.  Exactly
    one active ``stock``/``etf`` workspace must match the canonical code.  A
    missing, archived, general, or ambiguous workspace yields an empty brief.
    ``ref`` remains shape-stable on every path for decision-log auditing.
    """
    try:
        canonical = normalize_security_code(code)
    except Exception:
        canonical = ""
    day = _asof_day(asof)
    if not canonical:
        return _empty(code=str(code or "").strip().upper(), asof=day,
                      reason="invalid_code")
    if day is None:
        return _empty(code=canonical, asof=None, reason="invalid_asof")

    try:
        workspace_store = console_store.workspace_store
        active = workspace_store.list(include_archived=False)
    except Exception:
        return _empty(code=canonical, asof=day, reason="workspace_store_unavailable")

    matches = [
        item for item in active
        if item.get("kind") in {"stock", "etf"}
        and str(item.get("code") or "").upper() == canonical
        and not item.get("archived")
    ]
    if len(matches) > 1:
        return _empty(code=canonical, asof=day, reason="workspace_ambiguous")
    if not matches:
        # Distinguish an archived exact-code workspace for honest diagnostics,
        # while still refusing to read its retained private files.
        try:
            archived = [
                item for item in workspace_store.list(include_archived=True)
                if item.get("kind") in {"stock", "etf"}
                and str(item.get("code") or "").upper() == canonical
                and item.get("archived")
            ]
        except Exception:
            archived = []
        return _empty(code=canonical, asof=day,
                      reason="workspace_archived" if archived else "workspace_not_found")

    workspace_id = str(matches[0].get("id") or "")
    memory_path = Path(console_store.root) / "workspace_memory" / workspace_id / "memory.md"
    try:
        if not memory_path.is_file():
            return _empty(code=canonical, asof=day, reason="memory_empty",
                          workspace_id=workspace_id)
        raw = memory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _empty(code=canonical, asof=day, reason="memory_unreadable",
                      workspace_id=workspace_id)

    items = _parse_memory(raw, asof=day, replay=bool(replay))
    if not items:
        return _empty(code=canonical, asof=day, reason="memory_no_eligible_items",
                      workspace_id=workspace_id)

    rendered = "\n".join(_render(item) for item in items)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return {
        "items": items,
        "text": rendered,
        "ref": {
            "kind": "workspace_history",
            "workspace_id": workspace_id,
            "code": canonical,
            "asof": day,
            "item_count": len(items),
            "latest_date": items[0]["date"],
            "digest": digest,
            "reason": None,
        },
    }


__all__ = ["build_workspace_history_brief", "MAX_ITEMS", "MAX_TEXT_CHARS"]
