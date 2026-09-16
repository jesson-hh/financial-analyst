# -*- coding: utf-8 -*-
"""Canonical, deterministic event envelopes for the global ``events.db``.

The event store deliberately keeps three clocks separate:

``event_time``
    The instant asserted by the upstream source (and therefore possibly wrong).
``source_asof``
    The cutoff/observation time of the source response used by the adapter.
``available_at``
    The first local write time that controls PIT visibility.  Only this clock is
    used by :data:`guanlan_v2.events.store.PIT_PREDICATE`.

This module is pure deterministic normalization.  It never calls a provider,
LLM, workspace mapper, or orchestration layer.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional

from guanlan_v2.events import store


NORMALIZATION_VERSION = "events-normalized-v1"
SUBJECT_KINDS = frozenset({"issuer", "context"})


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strings(values: Optional[Iterable[Any]]) -> list[str]:
    return sorted({str(value).strip() for value in (values or ()) if str(value).strip()})


def normalize_issuer_id(value: Any) -> str:
    """Return one deterministic A-share issuer id where it is unambiguous.

    Existing prefixed codes remain unchanged.  Bare six-digit A-share codes are
    prefixed from the exchange's stable leading-digit convention.  Unknown
    identifiers are preserved rather than guessed or discarded.
    """
    text = str(value or "").strip().upper().replace(".", "")
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text)
    if match:
        return match.group(1) + match.group(2)
    if re.fullmatch(r"\d{6}", text):
        if text.startswith(("6", "9")):
            return "SH" + text
        if text.startswith(("0", "3")):
            return "SZ" + text
        if text.startswith(("4", "8")):
            return "BJ" + text
    return text


def _issuer_ids(values: Optional[Iterable[Any]]) -> list[str]:
    return sorted({normalize_issuer_id(value) for value in (values or ())
                   if normalize_issuer_id(value)})


def _normalise_source_asof(value: Any, *, available_at: str) -> str:
    candidate = available_at if value in (None, "") else value
    # A date-only source cutoff means that source's full local day.  It is
    # metadata, not the PIT visibility clock, so it may be later than a morning
    # retrieval without leaking into reads.
    date_only = isinstance(candidate, str) and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.strip())
    )
    return store.normalize_timestamp(candidate, date_end=date_only)


def normalized_event(
    *,
    source_id: str,
    event_type: str,
    available_at: Any,
    external_id: Any = None,
    source_asof: Any = None,
    event_time: Any = None,
    title: Any = None,
    summary: Any = None,
    source: Any = None,
    source_ref: Any = None,
    issuer_ids: Optional[Iterable[Any]] = None,
    subject_kind: Optional[str] = None,
    context_type: Optional[str] = None,
    context_ids: Optional[Iterable[Any]] = None,
    payload: Any = None,
    identity_payload: Any = None,
    provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build one canonical row with a content-stable idempotency key.

    Retrieval time and operational envelope fields are intentionally excluded
    from the key.  Re-reading the same fact is idempotent; a material title,
    summary, event-time, issuer, context, or payload revision becomes a new row
    on the same logical ``line_id`` and remains PIT-auditable.
    """
    canonical_source = str(source_id or "").strip().lower()
    canonical_type = str(event_type or "").strip()
    if not canonical_source:
        raise ValueError("source_id is required")
    if not canonical_type:
        raise ValueError("event_type is required")

    available = store.normalize_timestamp(available_at)
    source_cutoff = _normalise_source_asof(source_asof, available_at=available)
    headline = str(title or "").strip()
    abstract = str(summary or "").strip()
    reference = str(source_ref or "").strip() or None
    locator = str(external_id or reference or headline or abstract).strip()
    if not locator:
        raise ValueError("event requires external_id, source_ref, title, or summary")

    issuers = _issuer_ids(issuer_ids)
    contexts = _strings(context_ids)
    primary = str(subject_kind or ("issuer" if issuers else "context")).strip().lower()
    if primary not in SUBJECT_KINDS:
        raise ValueError("subject_kind must be issuer or context")
    if primary == "issuer" and not issuers:
        raise ValueError("issuer event requires issuer_ids")
    canonical_context = str(context_type or "").strip().lower() or None
    if primary == "context" and canonical_context is None:
        raise ValueError("context event requires context_type")

    event_time_text = None if event_time in (None, "") else str(event_time).strip()
    safe_payload = store.sanitize_provenance(payload)
    stable_payload = (
        safe_payload if identity_payload is None
        else store.sanitize_provenance(identity_payload)
    )
    identity = {
        "normalization_version": NORMALIZATION_VERSION,
        "source_id": canonical_source,
        "event_type": canonical_type,
        "external_id": locator,
        "event_time": event_time_text,
        "title": headline,
        "summary": abstract,
        "issuer_ids": issuers,
        "subject_kind": primary,
        "context_type": canonical_context,
        "context_ids": contexts,
        "payload": stable_payload,
    }
    identity_digest = _digest(identity)
    logical_digest = _digest({
        "normalization_version": NORMALIZATION_VERSION,
        "source_id": canonical_source,
        "event_type": canonical_type,
        "external_id": locator,
    })
    idempotency_key = f"{NORMALIZATION_VERSION}:{identity_digest}"
    proof = {
        "normalization_version": NORMALIZATION_VERSION,
        "idempotency_key": idempotency_key,
        "external_id": locator,
        **dict(provenance or {}),
    }
    return {
        "event_id": identity_digest,
        "idempotency_key": idempotency_key,
        "normalization_version": NORMALIZATION_VERSION,
        "event_type": canonical_type,
        "tickers": issuers,
        "issuer_ids": issuers,
        "subject_kind": primary,
        "context_type": canonical_context,
        "context_ids": contexts,
        "title": headline or None,
        "summary": abstract or None,
        "source": str(source or "").strip() or None,
        "source_id": canonical_source,
        "source_ref": reference,
        "source_asof": source_cutoff,
        "event_time": event_time_text,
        "available_at": available,
        "line_id": logical_digest,
        "ingested_from": canonical_source,
        "payload": safe_payload,
        "provenance": store.sanitize_provenance(proof),
    }


__all__ = [
    "NORMALIZATION_VERSION", "SUBJECT_KINDS", "normalize_issuer_id",
    "normalized_event",
]
