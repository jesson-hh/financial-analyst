"""Read-only Seats projection for one research workspace.

The authoritative assignment and decision log stay in ``var``.  This module
only filters and redacts them for the workspace Dock; it never writes a second
copy and never returns model reasoning, workspace memory, factors, or bars.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from guanlan_v2.console.workspaces import normalize_security_code


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEATS_VAR = Path(os.environ.get("GUANLAN_SEATS_VAR_ROOT") or (_REPO_ROOT / "var"))
ASSIGNMENTS_PATH = _SEATS_VAR / "seats_watch_assignments.json"
STRATEGY_ARCHIVE_DIR = Path(os.environ.get("GUANLAN_ARCHIVE_DIR") or (_REPO_ROOT / "var" / "archive"))
DECISIONS_LOG = _SEATS_VAR / "seats_decisions.jsonl"
REALTIME_DB_PATH = _SEATS_VAR / "realtime" / "seats_bars.sqlite3"

_A_SHARE_CODE_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_TEXT_LIMITS = {
    "id": 100,
    "ts": 40,
    "kind": 20,
    "name": 80,
    "source": 48,
    "strategy_id": 100,
    "strategy_name": 100,
    "mode": 20,
    "freq": 20,
    "tf": 20,
    "requested_evidence_tf": 20,
    "effective_evidence_tf": 20,
    "decision_cadence": 20,
    "direction": 24,
    "rationale": 1200,
    "data_asof": 40,
    "evidence_source": 80,
    "data_ref": 800,
    "data_digest": 128,
    "asof": 40,
    "position_action": 32,
    "hybrid_direction": 32,
    "side": 24,
    "logic": 12,
    "note": 400,
    "validity": 40,
}
_NUMBER_FIELDS = {
    "confidence", "assignment_revision", "exit_fraction", "stop", "take",
}
_QUALITY_NUMBER_FIELDS = {"quality_flagged_n", "complete_n", "n"}
_QUALITY_TEXT_FIELDS = {"source_error", "quote_error"}


class SeatsProjectionError(RuntimeError):
    """The authoritative Seats stores could not be read safely."""


def _canonical_a_share(value: Any) -> Optional[tuple[str, str]]:
    """Return ``(canonical, six_digit_symbol)`` for a Seats-supported code."""
    try:
        canonical = normalize_security_code(value)
    except Exception:
        return None
    match = _A_SHARE_CODE_RE.fullmatch(canonical)
    if not match:
        return None
    return canonical, match.group(2)


def supports_security_code(value: Any) -> bool:
    """Whether the global Seats watcher supports this workspace subject."""
    return _canonical_a_share(value) is not None


def _text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    result = str(value).replace("\x00", "").strip()
    return result[:limit] if result else None


def _number(value: Any) -> Optional[float | int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    if isinstance(value, int):
        return value
    return result


def _safe_quality(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _QUALITY_NUMBER_FIELDS:
        number = _number(value.get(key))
        if number is not None:
            out[key] = int(number)
    for key in _QUALITY_TEXT_FIELDS:
        text = _text(value.get(key), 180)
        if text is not None:
            out[key] = text
    return out


def _safe_triggers(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for raw in value[:12]:
        if not isinstance(raw, dict):
            continue
        kind = _text(raw.get("kind"), 24)
        op = _text(raw.get("op"), 4)
        number = _number(raw.get("value"))
        if kind and op and number is not None:
            out.append({"kind": kind, "op": op, "value": number})
    return out


def _safe_decision(raw: dict, canonical_code: str) -> dict:
    """Strict allowlist for the workspace-facing decision timeline."""
    out: dict[str, Any] = {"code": canonical_code}
    for key, limit in _TEXT_LIMITS.items():
        text = _text(raw.get(key), limit)
        if text is not None:
            out[key] = text
    for key in _NUMBER_FIELDS:
        number = _number(raw.get(key))
        if number is not None:
            out[key] = int(number) if key == "assignment_revision" else number

    evidence = raw.get("key_evidence")
    if isinstance(evidence, list):
        safe_evidence = []
        for item in evidence[:12]:
            text = _text(item, 300)
            if text is not None:
                safe_evidence.append(text)
        if safe_evidence:
            out["key_evidence"] = safe_evidence

    triggers = _safe_triggers(raw.get("triggers"))
    if triggers or "triggers" in raw:
        out["triggers"] = triggers
    quality = _safe_quality(raw.get("data_quality"))
    if quality:
        out["data_quality"] = quality
    return out


def _safe_assignment(raw: Optional[dict], canonical_code: str) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {
        "code": canonical_code,
        "symbol": canonical_code[2:],
        "enabled": bool(raw.get("enabled")),
        "runnable": bool(raw.get("runnable")),
        "needs_review": bool(raw.get("needs_review")),
    }
    text_fields = {
        "strategy_id": 100,
        "strategy_name": 100,
        "evidence_tf": 20,
        "requested_evidence_tf": 20,
        "effective_evidence_tf": 20,
        "decision_cadence": 20,
        "thinking_mode": 20,
        "status": 40,
        "updated_at": 40,
    }
    for key, limit in text_fields.items():
        text = _text(raw.get(key), limit)
        if text is not None:
            out[key] = text
    revision = _number(raw.get("revision"))
    if revision is not None:
        out["revision"] = int(revision)
    return out


def load_assignment(canonical_code: str, *, until: Any = None) -> Optional[dict]:
    supported = _canonical_a_share(canonical_code)
    if supported is None:
        return None
    canonical, symbol = supported
    try:
        from guanlan_v2.seats.watch_assignments import (
            AssignmentError,
            assignment_by_code,
        )

        raw = assignment_by_code(
            symbol,
            path=ASSIGNMENTS_PATH,
            archive_dir=STRATEGY_ARCHIVE_DIR,
        )
    except AssignmentError as exc:
        raise SeatsProjectionError("落子盯盘配置暂不可读") from exc
    except OSError as exc:
        raise SeatsProjectionError("落子盯盘配置暂不可读") from exc
    assignment = _safe_assignment(raw, canonical)
    if until and assignment is not None:
        # Assignments are a current global control record rather than an
        # append-only history.  An archived workspace must not display a
        # later reassignment as though it belonged to its historical window.
        # Missing/invalid timestamps therefore fail closed for archived views.
        if not _within_subject_scope(assignment.get("updated_at"), None, until):
            return None
    return assignment


def _timestamp(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _within_subject_scope(value: Any, since: Any, until: Any) -> bool:
    if not since and not until:
        return True
    current = _timestamp(value)
    lower = _timestamp(since) if since else None
    upper = _timestamp(until) if until else None
    if current is None or (since and lower is None) or (until and upper is None):
        return False
    # Workspace/decision timestamps are normally exchange-local naive values.
    # Refuse a mixed aware/naive comparison instead of guessing a timezone.
    try:
        return not ((lower is not None and current < lower)
                    or (upper is not None and current > upper))
    except TypeError:
        return False


def load_decisions(canonical_code: str, *, limit: int = 30,
                   since: Any = None, until: Any = None) -> list[dict]:
    """Filter authoritative decisions by exact canonical code and exclude runs."""
    supported = _canonical_a_share(canonical_code)
    if supported is None:
        return []
    canonical, _symbol = supported
    cap = max(1, min(int(limit or 30), 100))
    try:
        if not DECISIONS_LOG.exists():
            return []
        lines = DECISIONS_LOG.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SeatsProjectionError("落子研判历史暂不可读") from exc

    out: list[dict] = []
    for line in reversed(lines):
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict) or raw.get("run_id"):
            # Replays/batch runs are intentionally absent from the live
            # workspace timeline (same semantics as exclude_runs=1).
            continue
        if not _within_subject_scope(raw.get("ts"), since, until):
            continue
        row_code = _canonical_a_share(raw.get("code"))
        if row_code is None or row_code[0] != canonical:
            continue
        out.append(_safe_decision(raw, canonical))
        if len(out) >= cap:
            break
    return out


def _return_pct(bars: list[dict], minutes: int, interval_minutes: int) -> Optional[float]:
    if minutes < interval_minutes or minutes % interval_minutes:
        return None
    steps = minutes // interval_minutes
    if len(bars) <= steps:
        return None
    latest = _number(bars[-1].get("close"))
    previous = _number(bars[-1 - steps].get("close"))
    if latest is None or previous is None or previous == 0:
        return None
    return round((float(latest) / float(previous) - 1.0) * 100.0, 4)


def load_latest_snapshot(canonical_code: str) -> Optional[dict]:
    """Read a compact trend summary from the durable cache, without network/sealing."""
    supported = _canonical_a_share(canonical_code)
    if supported is None:
        return None
    canonical, _symbol = supported
    try:
        if not REALTIME_DB_PATH.exists():
            return None
        from guanlan_v2.seats.realtime_store import RealtimeBarStore

        store = RealtimeBarStore(REALTIME_DB_PATH)
        candidates = []
        for freq in ("1min", "5min"):
            candidate = store.snapshot(canonical, freq, limit=480)
            if candidate and candidate.get("bars"):
                candidates.append(candidate)
        if not candidates:
            return None
        # Prefer the newest durable evidence.  A stale 1-minute cache must not
        # hide a current 5-minute snapshot merely because it has finer grain;
        # at the same timestamp the finer series wins.
        snapshot = max(
            candidates,
            key=lambda item: (
                str(item.get("last_bar_time") or ""),
                item.get("freq") == "1min",
            ),
        )
        bars = snapshot["bars"]
        latest = bars[-1]
        interval = 1 if snapshot.get("freq") == "1min" else 5
        day = str(latest.get("bar_time") or "")[:10]
        day_bars = [row for row in bars
                    if str(row.get("bar_time") or "")[:10] == day]
        highs = [_number(row.get("high")) for row in day_bars]
        lows = [_number(row.get("low")) for row in day_bars]
        volumes = [_number(row.get("volume")) for row in day_bars]
        highs = [float(value) for value in highs if value is not None]
        lows = [float(value) for value in lows if value is not None]
        volumes = [float(value) for value in volumes if value is not None]
        return {
            "freq": snapshot.get("freq"),
            "close": _number(latest.get("close")),
            # Intraday windows never cross the previous session.  Early in
            # the morning an unavailable 15/30m return is safer than an
            # overnight move mislabeled as a minute trend.
            "return_1m_pct": _return_pct(day_bars, 1, interval),
            "return_5m_pct": _return_pct(day_bars, 5, interval),
            "return_15m_pct": _return_pct(day_bars, 15, interval),
            "return_30m_pct": _return_pct(day_bars, 30, interval),
            "day_high": max(highs) if highs else None,
            "day_low": min(lows) if lows else None,
            "volume": _number(latest.get("volume")),
            "day_volume": round(sum(volumes), 4) if volumes else None,
            "data_asof": snapshot.get("last_bar_time"),
            "source": _text(latest.get("source"), 80),
            "data_ref": snapshot.get("data_ref"),
            "data_digest": snapshot.get("digest"),
            "data_quality": {
                "quality_flagged_n": int(snapshot.get("quality_flagged_n") or 0),
                "complete_n": int(snapshot.get("complete_n") or 0),
                "n": int(snapshot.get("n") or 0),
            },
            "is_final": bool(latest.get("is_final")),
            "freshness": "unknown",
        }
    except Exception:
        # The decision timeline is still useful when SQLite is absent, locked,
        # or corrupt.  Never turn this optional read model into a 503 for the
        # whole workspace projection.
        return None


def latest_snapshot_metadata(decisions: list[dict],
                             canonical_code: str = "") -> Optional[dict]:
    """Prefer stored minute trends, then fall back to decision lineage only."""
    if canonical_code:
        cached = load_latest_snapshot(canonical_code)
        if cached is not None:
            return cached
    for row in decisions:
        data_asof = row.get("data_asof") or row.get("asof")
        data_ref = row.get("data_ref")
        if not data_asof and not data_ref:
            continue
        return {
            "freq": row.get("effective_evidence_tf") or row.get("freq") or row.get("tf"),
            "data_asof": data_asof,
            "source": row.get("evidence_source"),
            "data_ref": data_ref,
            "data_digest": row.get("data_digest"),
            "data_quality": row.get("data_quality") or {},
            # True freshness requires the exchange trading clock.  Do not infer
            # it from wall-clock age here; the consumer can display data_asof.
            "freshness": "unknown",
        }
    return None


__all__ = [
    "ASSIGNMENTS_PATH",
    "DECISIONS_LOG",
    "REALTIME_DB_PATH",
    "STRATEGY_ARCHIVE_DIR",
    "SeatsProjectionError",
    "latest_snapshot_metadata",
    "load_assignment",
    "load_decisions",
    "load_latest_snapshot",
    "supports_security_code",
]
