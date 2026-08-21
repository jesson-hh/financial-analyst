# -*- coding: utf-8 -*-
"""Persistent, single-owner assignments for the Seats live watcher.

Historically ``strategy.bind`` did two unrelated jobs: it described where a
strategy applied *and* switched unattended monitoring on.  A symbol bound to
more than one strategy was consequently judged by whichever ``strat_*.json``
file happened to sort first.  This module gives the watcher its own small,
versioned control plane while retaining a deterministic one-time migration
from those legacy bindings.

The store is deliberately independent from the global watcher switch in
``seats_watch.json``.  An assignment says *what would run*; the global switch
still decides whether the scheduler may run at all.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


VAR = Path(__file__).resolve().parents[2] / "var"
ASSIGNMENTS_PATH = VAR / "seats_watch_assignments.json"
ARCHIVE_DIR = VAR / "archive"

SCHEMA_VERSION = 1
EVIDENCE_TFS = frozenset({"day", "30min", "5min", "1min"})
# Every accepted value has a real, fail-closed evidence adapter.  Intraday
# values use the durable realtime store and never fall back to day bars.
EFFECTIVE_EVIDENCE_TFS = EVIDENCE_TFS
DECISION_CADENCES = frozenset({"daily", "60min", "30min", "10min"})
THINKING_MODES = frozenset({"fast", "deep"})

_CODE_RE = re.compile(r"^(?:(?:SH|SZ|BJ))?(\d{6})(?:\.(?:SH|SZ|BJ))?$", re.I)
_LOCK = threading.RLock()


class AssignmentError(ValueError):
    """Invalid assignment input or an unreadable assignment store."""


class RevisionConflict(AssignmentError):
    """Optimistic revision supplied by a caller no longer matches."""


def normalize_code(code: Any) -> str:
    raw = str(code or "").strip().upper()
    m = _CODE_RE.fullmatch(raw)
    if not m:
        raise AssignmentError("code 必须是 A 股六位代码(可带 SH/SZ/BJ 前后缀)")
    return m.group(1)


def normalize_evidence_tf(value: Any) -> str:
    aliases = {"30": "30min", "30m": "30min", "5": "5min", "5m": "5min",
               "1": "1min", "1m": "1min", "d": "day"}
    raw = str(value or "").strip().lower()
    raw = aliases.get(raw, raw)
    if raw not in EVIDENCE_TFS:
        raise AssignmentError(
            "evidence_tf 仅支持 day/30min/5min/1min")
    return raw


def normalize_decision_cadence(value: Any) -> str:
    aliases = {"hourly": "60min", "hour": "60min", "60m": "60min",
               "30m": "30min", "10m": "10min"}
    raw = str(value or "").strip().lower()
    raw = aliases.get(raw, raw)
    if raw not in DECISION_CADENCES:
        raise AssignmentError(
            "decision_cadence 仅支持 daily/60min/30min/10min")
    return raw


def normalize_thinking_mode(value: Any) -> str:
    raw = str(value or "fast").strip().lower()
    if raw not in THINKING_MODES:
        raise AssignmentError("thinking_mode 仅支持 fast/deep")
    return raw


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _strategy_rows(archive_dir: Path) -> list[dict]:
    """Load valid strategy archives in deterministic filename order."""
    rows: list[dict] = []
    try:
        paths = sorted(Path(archive_dir).glob("strat_*.json"))
    except Exception:  # pragma: no cover - exotic filesystem failure
        return rows
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # one bad legacy row must not hide the healthy rows
            continue
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "").strip()
        if not sid or row.get("type") not in (None, "strategy"):
            continue
        row = dict(row)
        row["id"] = sid
        row["_archive_name"] = path.name
        rows.append(row)
    return rows


def _strategy_index(archive_dir: Path) -> tuple[dict[str, dict], set[str]]:
    """Return unique strategies and duplicate ids (duplicates fail closed)."""
    out: dict[str, dict] = {}
    duplicates: set[str] = set()
    for row in _strategy_rows(archive_dir):
        sid = row["id"]
        if sid in out:
            duplicates.add(sid)
            continue
        out[sid] = row
    for sid in duplicates:
        out.pop(sid, None)
    return out, duplicates


def _empty_document() -> dict:
    return {"schema_version": SCHEMA_VERSION, "migrated_at": _now_iso(),
            "assignments": {}}


def _write_document(path: Path, doc: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _legacy_evidence_tf(strategy: dict) -> str:
    raw = str((strategy.get("clock") or {}).get("execTF") or "day").strip().lower()
    # The old UI only emitted day/5min.  Unknown hand-edited values keep the
    # old effective behaviour (day) rather than being guessed into a new mode.
    return raw if raw in ("day", "5min") else "day"


def _legacy_cadence(strategy: dict) -> str:
    raw = (strategy.get("clock") or {}).get("decisionFreq") or "hourly"
    try:
        return normalize_decision_cadence(raw)
    except AssignmentError:
        return "60min"


def _migrate_legacy(archive_dir: Path) -> dict:
    """Build one assignment per code; first sorted archive wins, visibly."""
    doc = _empty_document()
    candidates: dict[str, list[dict]] = {}
    for strategy in _strategy_rows(archive_dir):
        binds = strategy.get("bind")
        if not isinstance(binds, list):
            continue
        for raw_code in binds:
            try:
                code = normalize_code(raw_code)
            except AssignmentError:
                continue
            candidates.setdefault(code, []).append(strategy)

    for code in sorted(candidates):
        choices = candidates[code]
        chosen = choices[0]
        requested = _legacy_evidence_tf(chosen)
        ids = list(dict.fromkeys(str(s.get("id")) for s in choices))
        doc["assignments"][code] = {
            "code": code,
            "enabled": True,
            "strategy_id": chosen["id"],
            "strategy_name": str(chosen.get("name") or chosen["id"]),
            "evidence_tf": requested,
            "decision_cadence": _legacy_cadence(chosen),
            "thinking_mode": "fast",
            "revision": 1,
            "source": "legacy_bind",
            "updated_at": doc["migrated_at"],
            "migration_candidates": ids,
            "needs_review": len(ids) > 1,
        }
    return doc


def _read_or_migrate(path: Path, archive_dir: Path) -> dict:
    path = Path(path)
    if not path.exists():
        doc = _migrate_legacy(Path(archive_dir))
        # Never persist from an ordinary GET/status/watcher read.  Browser
        # archive hydration is asynchronous, so only the explicit migration
        # handshake may freeze legacy bind candidates.  Explicit upsert also
        # persists its complete result below.
        return doc
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # Never reinterpret a corrupt authoritative file as legacy bindings;
        # doing so could silently re-enable symbols an operator disabled.
        raise AssignmentError(f"assignment store 不可读:{type(exc).__name__}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise AssignmentError("assignment store schema_version 不支持")
    if not isinstance(doc.get("assignments"), dict):
        raise AssignmentError("assignment store assignments 非对象")
    return doc


def _decorate(raw: dict, strategies: dict[str, dict], duplicates: set[str]) -> dict:
    item = dict(raw)
    code = str(item.get("code") or "")
    sid = str(item.get("strategy_id") or "")
    requested = str(item.get("evidence_tf") or "")
    item["code"] = code
    item["requested_evidence_tf"] = requested
    item["effective_evidence_tf"] = (
        requested if requested in EFFECTIVE_EVIDENCE_TFS else None)
    strategy = strategies.get(sid)
    if strategy is not None:
        item["strategy_name"] = str(strategy.get("name") or sid)

    if not item.get("enabled"):
        status = "disabled"
    elif sid in duplicates:
        status = "strategy_ambiguous"
    elif strategy is None:
        status = "strategy_missing"
    elif requested not in EVIDENCE_TFS:
        status = "invalid_evidence_tf"
    elif item["effective_evidence_tf"] is None:
        status = "unsupported_evidence_tf"
    else:
        status = "active"
    item["status"] = status
    item["runnable"] = status == "active"
    return item


def list_assignments(*, path: Path = ASSIGNMENTS_PATH,
                     archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    with _LOCK:
        doc = _read_or_migrate(Path(path), Path(archive_dir))
        strategies, duplicates = _strategy_index(Path(archive_dir))
        out = []
        for code in sorted(doc["assignments"]):
            raw = doc["assignments"][code]
            if isinstance(raw, dict):
                out.append(_decorate(raw, strategies, duplicates))
        return out


def migrate_legacy_assignments(*, path: Path = ASSIGNMENTS_PATH,
                               archive_dir: Path = ARCHIVE_DIR,
                               persist_empty: bool = False) -> list[dict]:
    """Freeze legacy ``strategy.bind`` rows after archive hydration completes.

    Startup may call this with ``persist_empty=False`` to migrate an already
    durable server archive without blocking a later browser upload.  The UI
    calls it after its archiveReady barrier with ``persist_empty=True``.
    """
    with _LOCK:
        target = Path(path)
        if target.exists():
            doc = _read_or_migrate(target, Path(archive_dir))
        else:
            doc = _migrate_legacy(Path(archive_dir))
            if doc.get("assignments") or persist_empty:
                _write_document(target, doc)
        strategies, duplicates = _strategy_index(Path(archive_dir))
        return [_decorate(raw, strategies, duplicates)
                for _, raw in sorted(doc.get("assignments", {}).items())
                if isinstance(raw, dict)]


def runtime_assignments(*, path: Path = ASSIGNMENTS_PATH,
                        archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    """Return only runnable assignments joined to their exact strategy id."""
    with _LOCK:
        target = Path(path)
        if not target.exists():
            return []
        doc = _read_or_migrate(target, Path(archive_dir))
        strategies, duplicates = _strategy_index(Path(archive_dir))
        out: list[dict] = []
        for code in sorted(doc["assignments"]):
            raw = doc["assignments"][code]
            if not isinstance(raw, dict):
                continue
            item = _decorate(raw, strategies, duplicates)
            if not item["runnable"]:
                continue
            strategy = strategies.get(item["strategy_id"])
            if strategy is None:  # defensive; _decorate already closed this
                continue
            out.append({"assignment": item, "strategy": dict(strategy)})
        return out


def upsert_assignment(code: Any, payload: dict, *,
                      path: Path = ASSIGNMENTS_PATH,
                      archive_dir: Path = ARCHIVE_DIR) -> dict:
    """Create/update one code assignment and increment its local revision."""
    if not isinstance(payload, dict):
        raise AssignmentError("payload 必须是对象")
    key = normalize_code(code)
    with _LOCK:
        doc = _read_or_migrate(Path(path), Path(archive_dir))
        current = doc["assignments"].get(key)
        current = current if isinstance(current, dict) else {}

        expected = payload.get("expected_revision")
        if expected is None and "revision" in payload:
            expected = payload.get("revision")
        if expected is not None:
            try:
                expected_i = int(expected)
            except (TypeError, ValueError) as exc:
                raise AssignmentError("revision 必须是整数") from exc
            if expected_i != int(current.get("revision") or 0):
                raise RevisionConflict(
                    f"revision 冲突:期望 {expected_i},当前 {int(current.get('revision') or 0)}")

        strategies, duplicates = _strategy_index(Path(archive_dir))
        sid = str(payload.get("strategy_id", current.get("strategy_id") or "")).strip()
        enabled = bool(payload.get("enabled", current.get("enabled", True)))
        if not sid:
            raise AssignmentError("缺 strategy_id")
        if sid in duplicates and (enabled or not current):
            raise AssignmentError("strategy_id 在归档中重复,拒绝歧义策略")
        strategy = strategies.get(sid)
        if strategy is None and (enabled or not current):
            raise AssignmentError("strategy_id 不存在或策略归档不可读")

        requested = normalize_evidence_tf(
            payload.get("evidence_tf", current.get("evidence_tf") or "day"))
        cadence = normalize_decision_cadence(
            payload.get("decision_cadence",
                        current.get("decision_cadence") or _legacy_cadence(strategy or {})))
        thinking_mode = normalize_thinking_mode(
            payload.get("thinking_mode", current.get("thinking_mode") or "fast"))
        revision = int(current.get("revision") or 0) + 1
        row = {
            "code": key,
            "enabled": enabled,
            "strategy_id": sid,
            "strategy_name": str((strategy or {}).get("name")
                                 or current.get("strategy_name") or sid),
            "evidence_tf": requested,
            "decision_cadence": cadence,
            "thinking_mode": thinking_mode,
            "revision": revision,
            "source": "explicit",
            "updated_at": _now_iso(),
            "migration_candidates": current.get("migration_candidates") or [sid],
            "needs_review": False,
        }
        doc["assignments"][key] = row
        _write_document(Path(path), doc)
        return _decorate(row, strategies, duplicates)


def assignment_by_code(code: Any, *, path: Path = ASSIGNMENTS_PATH,
                       archive_dir: Path = ARCHIVE_DIR) -> Optional[dict]:
    key = normalize_code(code)
    for item in list_assignments(path=path, archive_dir=archive_dir):
        if item["code"] == key:
            return item
    return None


def referenced_codes_for_strategy(strategy_id: str, *,
                                  path: Path = ASSIGNMENTS_PATH,
                                  archive_dir: Path = ARCHIVE_DIR) -> list[str]:
    """Return every assignment reference, including disabled configurations."""
    sid = str(strategy_id or "").strip()
    if not sid:
        return []
    return [item["code"] for item in list_assignments(
        path=path, archive_dir=archive_dir)
        if item.get("strategy_id") == sid]


def enabled_codes_for_strategy(strategy_id: str, *,
                               path: Path = ASSIGNMENTS_PATH,
                               archive_dir: Path = ARCHIVE_DIR) -> list[str]:
    """Compatibility alias; deletion protection now covers disabled rows too."""
    return referenced_codes_for_strategy(
        strategy_id, path=path, archive_dir=archive_dir)


def remove_archive_if_unreferenced(artifact_id: str, archive_path: Path, *,
                                   path: Path = ASSIGNMENTS_PATH,
                                   archive_dir: Path = ARCHIVE_DIR) -> dict:
    """Atomically guard assignment references and remove one archive file.

    ``upsert_assignment`` uses the same process lock, closing the check/delete
    race in the desktop single-worker runtime.  New assignments cannot target
    the removed strategy because strategy lookup then fails closed.
    """
    sid = str(artifact_id or "").strip()
    target = Path(archive_path)
    with _LOCK:
        doc = _read_or_migrate(Path(path), Path(archive_dir))
        codes = sorted({str(raw.get("code") or code)
                        for code, raw in doc.get("assignments", {}).items()
                        if isinstance(raw, dict)
                        and str(raw.get("strategy_id") or "") == sid})
        if codes:
            return {"ok": False, "reason": "strategy_assigned", "codes": codes}
        if target.exists():
            target.unlink()
        return {"ok": True, "id": sid}
