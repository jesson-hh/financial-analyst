"""Strict point-in-time helpers for the legacy company-research runtime.

Date-bounded rows are necessary but not sufficient evidence of a historical
snapshot.  Mutable stores can be read today while returning only old row
dates.  Historical callers therefore need an explicit acquisition timestamp
at/before the requested boundary, or an immutable version sidecar anchored to
that boundary.  Missing proof fails closed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd


_HONG_KONG = ZoneInfo("Asia/Hong_Kong")


class PitUnavailableError(RuntimeError):
    """The requested historical version cannot be proven."""


def hong_kong_now() -> datetime:
    """Return an aware acquisition clock independent of the host timezone."""
    return datetime.now(_HONG_KONG)


def hong_kong_today() -> date:
    """Return the product calendar date in Asia/Hong_Kong."""
    return hong_kong_now().date()


def normalize_asof(value: Any, *, today: Optional[date] = None) -> str:
    """Return a strict ``YYYY-MM-DD`` request date and reject future dates."""
    if value in (None, "", "today"):
        return (today or hong_kong_today()).isoformat()
    text = str(value).strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"asof must be YYYY-MM-DD, got {text!r}") from exc
    current = today or hong_kong_today()
    if parsed > current:
        raise ValueError(
            f"asof {parsed.isoformat()} is in the future relative to {current.isoformat()}"
        )
    return parsed.isoformat()


def is_historical_asof(value: Any, *, today: Optional[date] = None) -> bool:
    return normalize_asof(value, today=today) < (today or hong_kong_today()).isoformat()


def asof_cutoff(value: Any) -> datetime:
    """Inclusive end-of-day cutoff in the product's Asia/Hong_Kong timezone."""
    return datetime.combine(
        date.fromisoformat(normalize_asof(value)), time.max, tzinfo=_HONG_KONG,
    )


def build_cache_key(source: str, code: str, asof: Any, params: Dict[str, Any]) -> str:
    """Stable semantic cache identity with ``asof`` as a first-class dimension."""
    payload = {
        "source": str(source),
        "code": str(code).upper(),
        "asof": normalize_asof(asof),
        "params": params,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _frame_dates(frame: pd.DataFrame) -> Optional[pd.Series]:
    if frame is None or frame.empty:
        return None
    if "trade_date" in frame.columns:
        raw = frame["trade_date"]
        compact = raw.astype(str).str.replace(r"\.0$", "", regex=True)
        if compact.str.fullmatch(r"\d{8}").all():
            values = pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
        else:
            values = pd.to_datetime(raw, errors="coerce")
    elif isinstance(frame.index, pd.DatetimeIndex):
        values = pd.Series(frame.index, index=frame.index)
    else:
        return None
    return pd.Series(values, index=frame.index)


def _coerce_timestamp(value: Any) -> Optional[pd.Timestamp]:
    if value in (None, ""):
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(ts) else ts


def _after_cutoff(ts: pd.Timestamp, cutoff: datetime) -> bool:
    bound = pd.Timestamp(cutoff)
    if bound.tzinfo is None:
        bound = bound.tz_localize(_HONG_KONG)
    else:
        bound = bound.tz_convert(_HONG_KONG)
    local = ts.tz_localize(_HONG_KONG) if ts.tzinfo is None else ts.tz_convert(_HONG_KONG)
    return bool(local > bound)


def timestamp_not_after_asof(value: Any, asof: Any) -> bool:
    """Whether a timestamp is at/before the Hong Kong end-of-day boundary."""
    ts = _coerce_timestamp(value)
    return ts is not None and not _after_cutoff(ts, asof_cutoff(asof))


def _content_digest(frame: pd.DataFrame) -> str:
    """Bind a receipt to the exact returned frame, not only query parameters."""
    h = hashlib.sha256()
    h.update(repr([(str(c), str(t)) for c, t in zip(frame.columns, frame.dtypes)]).encode())
    try:
        hashed = pd.util.hash_pandas_object(frame, index=True).values
        h.update(hashed.tobytes())
    except (TypeError, ValueError):
        h.update(frame.to_json(date_format="iso", orient="split", default_handler=str).encode())
    return h.hexdigest()


def _version_proof(attrs: Dict[str, Any], cutoff: datetime) -> Tuple[bool, str, Optional[str]]:
    """Return ``(proven, proof_kind, version)`` for explicit provider metadata."""
    pulled_raw = attrs.get("pulled_at")
    pulled_at = _coerce_timestamp(pulled_raw)
    if pulled_raw not in (None, ""):
        if pulled_at is None:
            return False, "invalid_pulled_at", None
        if _after_cutoff(pulled_at, cutoff):
            return False, "pulled_after_asof", None
        return True, "provider_pulled_at", None

    version = str(attrs.get("snapshot_version") or "").strip()
    version_asof = _coerce_timestamp(attrs.get("snapshot_asof"))
    immutable = attrs.get("snapshot_immutable") is True
    if version and immutable and version_asof is not None and not _after_cutoff(version_asof, cutoff):
        return True, "immutable_snapshot_version", version
    return False, "missing_version_sidecar", version or None


def guard_frame_asof(
    frame: pd.DataFrame,
    *,
    source: str,
    code: str,
    asof: Any,
    params: Dict[str, Any],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Validate row dates and strict historical provenance, then emit a receipt."""
    requested = normalize_asof(asof)
    historical = is_historical_asof(requested)
    attrs = dict(getattr(frame, "attrs", {}) or {})
    dates = _frame_dates(frame)
    if historical and dates is None:
        raise PitUnavailableError(
            f"{source} unavailable: historical frame has no provable trade_date axis"
        )

    if historical and dates is not None and bool(dates.isna().any()):
        valid = dates.notna().to_numpy()
        guarded = frame.iloc[valid].copy()
        guarded.attrs.update(attrs)
        frame = guarded
        dates = dates.iloc[valid]
        if frame.empty:
            raise PitUnavailableError(
                f"{source} unavailable: historical frame has no parseable trade_date rows"
            )

    cutoff = asof_cutoff(requested)
    data_asof: Optional[str] = None
    if dates is not None:
        parsed_dates = dates.dropna()
        if not parsed_dates.empty:
            latest = pd.Timestamp(parsed_dates.max())
            if _after_cutoff(latest, cutoff):
                raise PitUnavailableError(
                    f"{source} unavailable: provider returned {latest.isoformat()} "
                    f"after asof {requested} Asia/Hong_Kong"
                )
            local_latest = (
                latest.tz_localize(_HONG_KONG)
                if latest.tzinfo is None else latest.tz_convert(_HONG_KONG)
            )
            data_asof = local_latest.date().isoformat()

    proven, proof, version = _version_proof(attrs, cutoff)
    if historical and not proven:
        if proof == "pulled_after_asof":
            raise PitUnavailableError(
                f"{source} unavailable: pulled_at {attrs.get('pulled_at')} after asof {requested}"
            )
        raise PitUnavailableError(
            f"{source} unavailable: bounded row dates do not prove a historical version; "
            "an immutable snapshot_version/snapshot_asof sidecar or pulled_at at/before "
            f"{requested} is required"
        )

    content_sha256 = _content_digest(frame)
    pulled_at = _coerce_timestamp(attrs.get("pulled_at"))
    snapshot_asof = _coerce_timestamp(attrs.get("snapshot_asof"))
    immutable_snapshot = bool(
        proof == "immutable_snapshot_version"
        and attrs.get("snapshot_immutable") is True
        and snapshot_asof is not None
    )
    cache_params = dict(params)
    cache_params.update({
        "content_sha256": content_sha256,
        "pulled_at": pulled_at.isoformat() if pulled_at is not None else None,
        "snapshot_version": version,
    })
    receipt = {
        "source": source,
        "status": "available",
        "requested_asof": requested,
        "data_asof": data_asof,
        "pulled_at": pulled_at.isoformat() if pulled_at is not None else None,
        "snapshot_version": version,
        "snapshot_asof": snapshot_asof.isoformat() if snapshot_asof is not None else None,
        "snapshot_immutable": immutable_snapshot,
        "content_sha256": content_sha256,
        "cache_key": build_cache_key(source, code, requested, cache_params),
        "point_in_time": bool(proven),
        "historical_version_proven": bool(proven),
        "proof": proof if proven else "current_unversioned_read",
    }
    return frame, receipt


def unavailable_receipt(
    source: str,
    *,
    code: str,
    asof: Any,
    params: Optional[Dict[str, Any]] = None,
    reason: str,
) -> Dict[str, Any]:
    requested = normalize_asof(asof)
    return {
        "source": source,
        "status": "unavailable",
        "requested_asof": requested,
        "data_asof": None,
        "pulled_at": None,
        "snapshot_version": None,
        "snapshot_asof": None,
        "snapshot_immutable": False,
        "content_sha256": None,
        "cache_key": build_cache_key(source, code, requested, params or {}),
        "point_in_time": False,
        "historical_version_proven": False,
        "proof": None,
        "reason": reason,
    }


__all__ = [
    "PitUnavailableError",
    "normalize_asof",
    "is_historical_asof",
    "asof_cutoff",
    "timestamp_not_after_asof",
    "build_cache_key",
    "guard_frame_asof",
    "unavailable_receipt",
]
