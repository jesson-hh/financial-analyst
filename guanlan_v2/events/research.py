# -*- coding: utf-8 -*-
"""Read-only research search over :mod:`guanlan_v2.events.store`.

The public surface adds strict ``asof`` parsing, composable filters, a frozen
row watermark plus keyset cursor, auditable provenance and candidate relations.
It never creates/migrates a database; write-side migration remains an explicit
operator/ingest concern.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, time
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from guanlan_v2.events import store
from guanlan_v2.events.relations import (
    RELATION_POLICY,
    catalog_snapshot,
    link_event_candidates,
    relation_matches,
)


_HK = ZoneInfo("Asia/Hong_Kong")
_CURSOR_VERSION = 2
_MAX_LIMIT = 100
_SCAN_CHUNK = 200
_MAX_QUERY_CHARS = 512
_MAX_CURSOR_CHARS = 4096
_MAX_FILTER_VALUES = 50
_MAX_FILTER_ITEM_CHARS = 200
_MAX_TIME_CHARS = 64
_MAX_URL_DECODE_LAYERS = 3
_MAX_JSON_UNESCAPE_LAYERS = 2
_MAX_JSON_STRING_CHARS = 65_536
_MAX_JSON_NESTING = 64
_REDACTED = "[REDACTED]"
_REDACTED_PATH = "[REDACTED_PATH]"
_SECRET_KEYS = {
    "access_token", "accesstoken", "api_key", "apikey", "auth", "authorization", "bearer",
    "aws_access_key_id", "awsaccesskeyid",
    "client_secret", "clientsecret", "cookie", "credential", "credentials",
    "encryption_key", "password", "password_hash", "passwd", "private_key",
    "privatekey", "proxy_authorization", "refresh_token", "refreshtoken", "secret",
    "secret_key", "secretkey", "set_cookie", "sig", "signature", "signing_key",
    "google_access_id", "googleaccessid", "key", "session_id", "sessionid", "token",
}
_PATH_KEYS = {"absolute_path", "db_path", "filesystem_path", "local_path"}
_PAYLOAD_JSON_KEYS = {"payload_json", "payloadjson"}
_SENSITIVE_QUERY_KEYS = {
    "auth", "authorization", "aws_access_key_id", "awsaccesskeyid",
    "google_access_id", "googleaccessid", "key", "session_id", "session_key",
    "session_token", "sessionid",
    "sig", "signature", "x_amz_credential", "x_amz_security_token", "x_amz_signature",
}
_WEB_ROUTE_QUERY_KEYS = {
    "callback", "callback_url", "continue", "dest", "destination", "next", "redirect",
    "redirect_uri", "redirect_url", "return", "return_to", "url",
}
# This is intentionally much narrower than the application's API route surface:
# it applies to untrusted redirects in source evidence, not first-party routing.
_WEB_ROUTE_ROOT_ALLOWLIST = {"reports"}
_URL_RE = re.compile(
    r"(?i)\b(?!file:)[a-z][a-z0-9+.-]*://[^\s<>{}\"'，。；！？]+"
)
_NETWORK_USERINFO_URL_RE = re.compile(
    r"(?i)(?<![:/])//[^\s<>{}\"'，。；！？@]+@[^\s<>{}\"'，。；！？]+"
)
_FILE_URI_RE = re.compile(r"(?i)\bfile:(?://)?[^\s<>{}\"'，。；！？]+")
_QUOTED_LOCAL_PATH_RE = re.compile(
    r"(?P<quote>[\"'])(?P<path>(?:[A-Za-z]:[\\/]|\\\\|/[A-Za-z0-9._-]+/)"
    r"[^\"'\r\n]+)(?P=quote)",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)[^\s<>{}\"'|?*,;，。；！？)\]]+"
)
_WINDOWS_DEVICE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:\\\\[?.]\\|\\\?\?\\)"
    r"(?:UNC\\)?[^\s<>{}\"'|*,;，。；！？)\]]+"
)
_POSIX_PATH_RE = re.compile(
    r"(?i)(?<![:/\w])/(?P<root>[A-Za-z0-9._-]+)"
    r"(?:/[^\s<>{}\"'*,;，。；！？)\]]+)+"
)
_ASSIGNMENT_NAME = (
    r"(?:proxy[_-]?authorization|x[_-]?amz[_-]?(?:credential|security[_-]?token|signature)|"
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?key|client[_-]?secret|"
    r"aws[_-]?access[_-]?key[_-]?id|google[_-]?access[_-]?id|session[_-]?id|"
    r"private[_-]?key|secret[_-]?key|signing[_-]?key|password(?:[_-]?hash)?|"
    r"authorization|credentials?|set[_-]?cookie|cookie|passwd|signature|secret|token|auth|sig|key)"
)
_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>(?<![\w-])(?P<key_quote>\\?[\"']|){_ASSIGNMENT_NAME}"
    rf"(?P=key_quote)(?![\w-])\s*[:=]\s*)(?P<value_quote>\\?[\"'])"
    rf".*?(?P=value_quote)",
    re.IGNORECASE,
)
_BARE_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>(?<![\w-])(?P<key_quote>\\?[\"']|){_ASSIGNMENT_NAME}"
    rf"(?P=key_quote)(?![\w-])\s*[:=]\s*)"
    r"(?!\\?[\"'])(?:\[(?:REDACTED|REDACTED_PATH)\]|"
    r"(?:(?:Bearer|Basic)\s+)?[^\s,;&}\]，；。！？]+)",
    re.IGNORECASE,
)
_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(?P<scheme>Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"
)
_HIGH_SIGNAL_SECRET_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,})\b"
)
_PAYLOAD_JSON_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:\\?[\"']\s*)?"
    r"(?:[A-Za-z0-9]+[_-])*payload[_-]?json"
    r"(?:\s*\\?[\"'])?\s*[:=]"
)
_JSON_PARSE_REFUSED = object()


class EventsSearchError(ValueError):
    """A stable, user-visible search contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _bounded_text(value: Any, *, name: str, max_chars: int,
                  code: Optional[str] = None) -> str:
    text = str(value or "")
    if len(text) > max_chars:
        raise EventsSearchError(
            code or f"{name}_too_long",
            f"{name} 最长 {max_chars} 字符",
        )
    return text.strip()


def _strings(*values: Any, name: str = "filter") -> Tuple[str, ...]:
    out: List[str] = []
    seen = 0
    for value in values:
        if value is None:
            continue
        seq: Iterable[Any]
        if isinstance(value, (list, tuple, set)):
            seq = value
            if seen + len(value) > _MAX_FILTER_VALUES:
                raise EventsSearchError(
                    "too_many_filter_values",
                    f"{name} 最多 {_MAX_FILTER_VALUES} 项",
                )
        else:
            if value == "":
                continue
            seq = (value,)
        for one in seq:
            seen += 1
            if seen > _MAX_FILTER_VALUES:
                raise EventsSearchError(
                    "too_many_filter_values",
                    f"{name} 最多 {_MAX_FILTER_VALUES} 项",
                )
            text = _bounded_text(
                one, name=name, max_chars=_MAX_FILTER_ITEM_CHARS,
                code="filter_value_too_long",
            )
            if text and text not in out:
                out.append(text)
    return tuple(out)


def _normalise_limit(limit: Any) -> int:
    if isinstance(limit, bool):
        raise EventsSearchError("invalid_limit", "limit 必须是 1..100 的整数")
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise EventsSearchError("invalid_limit", "limit 必须是 1..100 的整数") from exc
    if value < 1 or value > _MAX_LIMIT:
        raise EventsSearchError("invalid_limit", "limit 必须在 1..100 之间")
    return value


def _parse_local(value: Any, *, end_of_date: bool = False) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise EventsSearchError("invalid_time", "时间过滤值不能为空")
    is_date = len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"
    try:
        if is_date:
            day = datetime.fromisoformat(text).date()
            dt = datetime.combine(day, time.max if end_of_date else time.min)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventsSearchError("invalid_time", "时间过滤值必须是有效 ISO 日期或时间") from exc
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_HK)
    return dt.astimezone(_HK)


def _canonical_asof(asof: Any) -> str:
    asof = _bounded_text(
        asof, name="asof", max_chars=_MAX_TIME_CHARS,
        code="filter_value_too_long",
    )
    try:
        # Store owns the canonical PIT representation after the P0 migration.
        return store.normalize_asof(asof)
    except AttributeError:
        dt = _parse_local(asof, end_of_date=True)
        return dt.replace(tzinfo=None).isoformat(timespec="microseconds")
    except (TypeError, ValueError) as exc:
        raise EventsSearchError("invalid_asof", "asof 必须是有效 ISO 日期或时间") from exc


def _event_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=_HK) if dt.tzinfo is None else dt.astimezone(_HK)


def _fingerprint(request: Mapping[str, Any]) -> str:
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _encode_cursor(*, fingerprint: str, watermark: int,
                   available_at: str, event_id: str,
                   snapshot: Mapping[str, str]) -> str:
    body = {
        "v": _CURSOR_VERSION,
        "fp": fingerprint,
        "wm": int(watermark),
        "a": available_at,
        "e": event_id,
        "s": dict(snapshot),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, fingerprint: str) -> Dict[str, Any]:
    cursor = _bounded_text(
        cursor, name="cursor", max_chars=_MAX_CURSOR_CHARS,
        code="cursor_too_long",
    )
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - one stable boundary for opaque cursors
        raise EventsSearchError("invalid_cursor", "cursor 不是有效的事件检索游标") from exc
    if not isinstance(data, dict) or data.get("v") != _CURSOR_VERSION:
        raise EventsSearchError("invalid_cursor", "cursor 版本不受支持")
    if data.get("fp") != fingerprint:
        raise EventsSearchError("cursor_filter_mismatch", "cursor 与本次 asof/query/过滤条件不匹配")
    if not isinstance(data.get("wm"), int) or data["wm"] < 0:
        raise EventsSearchError("invalid_cursor", "cursor watermark 非法")
    if not str(data.get("a") or "") or not str(data.get("e") or ""):
        raise EventsSearchError("invalid_cursor", "cursor key 非法")
    snapshot = data.get("s")
    required = {
        "relation_watermark", "catalog_digest", "retrieval_mode",
        "retrieval_watermark", "fts_watermark",
    }
    if (not isinstance(snapshot, dict) or set(snapshot) != required
            or any(not isinstance(snapshot.get(key), str) or not snapshot[key]
                   for key in required)):
        raise EventsSearchError("invalid_cursor", "cursor snapshot 非法")
    return data


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','view')", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _state_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _fts_health(conn: sqlite3.Connection, event_watermark: int) -> Dict[str, Any]:
    if not _table_exists(conn, "events_fts"):
        return {"status": "unavailable", "indexed": 0, "total": None,
                "reason": "events_fts_missing",
                "watermark": _state_digest({"status": "missing"})}
    try:
        total = int(conn.execute(
            "SELECT COUNT(*) FROM events WHERE rowid<=?", (event_watermark,),
        ).fetchone()[0])
        fts_row = conn.execute(
            "SELECT COUNT(*) AS n,COUNT(DISTINCT f.event_id) AS indexed,"
            "COALESCE(MAX(f.rowid),0) AS max_rowid,"
            "COALESCE(SUM(LENGTH(COALESCE(f.tokens,''))),0) AS token_units "
            "FROM events_fts f JOIN events e ON e.event_id=f.event_id "
            "WHERE e.rowid<=?",
            (event_watermark,),
        ).fetchone()
        rows = int(fts_row[0])
        indexed = int(fts_row[1])
        max_rowid = int(fts_row[2])
        token_units = int(fts_row[3])
    except sqlite3.Error as exc:
        return {"status": "unavailable", "indexed": 0, "total": None,
                "reason": f"fts_health_error:{type(exc).__name__}",
                "watermark": _state_digest({"status": "unavailable",
                                             "error": type(exc).__name__})}
    status = "ok" if indexed >= total else "partial"
    watermark = _state_digest({
        "rows": rows, "indexed": indexed, "max_rowid": max_rowid,
        "token_units": token_units,
    })
    return {"status": status, "indexed": indexed, "total": total,
            "rows": rows, "watermark": watermark,
            "reason": None if status == "ok" else "fts_rows_missing_like_supplement_enabled"}


def _like_pattern(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _query_tokens(query: str) -> Tuple[str, ...]:
    tokens = [token for token in store._jieba_tokens(query or "").split(" ") if token]
    return tuple(dict.fromkeys(tokens))


def _token_like_plan(tokens: Sequence[str]) -> Tuple[str, Dict[str, Any]]:
    clauses: List[str] = []
    params: Dict[str, Any] = {}
    for index, token in enumerate(tokens):
        key = f"like_{index}"
        clauses.append(
            f"(e.title LIKE :{key} ESCAPE '\\' OR e.summary LIKE :{key} ESCAPE '\\')"
        )
        params[key] = _like_pattern(token)
    return ("(" + " OR ".join(clauses) + ")" if clauses else "0=1", params)


def _query_plan(conn: sqlite3.Connection, query: str,
                event_watermark: int) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Return SQL predicate/params/retrieval metadata.

    A partial FTS index is supplemented with token-wise OR LIKE for unindexed
    rows, matching the FTS boolean semantics.  If FTS itself is unavailable,
    the whole-query LIKE fallback is labelled honestly.
    """
    if not query:
        return "1=1", {}, {"mode": "scan", "fts": _fts_health(conn, event_watermark)}
    health = _fts_health(conn, event_watermark)
    if health["status"] == "unavailable":
        predicate, params = _token_like_plan((query,))
        return predicate, params, {
            "mode": "like", "fts": health, "query_semantics": "whole_query_like",
        }
    try:
        tokens = _query_tokens(query)  # one tokenizer contract with write-side indexing
    except Exception as exc:  # noqa: BLE001 - jieba is an optional honest degradation
        health = {**health, "status": "unavailable",
                  "reason": f"tokenizer_unavailable:{type(exc).__name__}"}
        predicate, params = _token_like_plan((query,))
        return predicate, params, {"mode": "like", "fts": health,
                                   "query_semantics": "whole_query_like"}
    like_predicate, like_params = _token_like_plan(tokens)
    if not tokens:
        return "0=1", {}, {"mode": "fts", "fts": health,
                            "query_semantics": "token_or"}
    fts_query = " OR ".join('"' + token.replace('"', "") + '"' for token in tokens)
    # Health reads can succeed while MATCH itself is unavailable/corrupt.  Probe
    # now so the effective retrieval mode, rather than an optimistic plan, is
    # what gets sealed into the cursor.
    try:
        conn.execute(
            "SELECT 1 FROM events_fts WHERE events_fts MATCH ? LIMIT 1", (fts_query,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        health = {**health, "status": "unavailable",
                  "reason": f"fts_query_error:{type(exc).__name__}"}
        return like_predicate, like_params, {
            "mode": "like", "fts": health, "query_semantics": "token_or",
        }
    predicate = (
        "(e.event_id IN (SELECT event_id FROM events_fts WHERE events_fts MATCH :fts_q) "
        "OR (NOT EXISTS (SELECT 1 FROM events_fts fx WHERE fx.event_id=e.event_id) "
        f"AND {like_predicate}))"
    )
    return predicate, {"fts_q": fts_query, **like_params}, {
        "mode": "fts+like_missing" if health["status"] == "partial" else "fts",
        "fts": health,
        "query_semantics": "token_or",
    }


def _relation_watermark(conn: sqlite3.Connection, event_watermark: int) -> Dict[str, Any]:
    """Digest every relation attached to the frozen event set.

    Relation reviews are mutable operational state even though event rows are
    append-only.  Hashing the complete logical rows detects inserts, deletes,
    reviews and evidence edits while exposing none of their private contents.
    """
    if not _table_exists(conn, "event_relations"):
        return {"status": "missing", "rows": 0, "max_rowid": 0,
                "digest": _state_digest({"status": "missing"})}
    digest = hashlib.sha256()
    rows = 0
    max_rowid = 0
    try:
        cursor = conn.execute(
            "SELECT r.rowid AS _snapshot_rowid,r.* "
            "FROM event_relations r JOIN events e ON e.event_id=r.event_id "
            "WHERE e.rowid<=? ORDER BY r.rowid",
            (event_watermark,),
        )
        for row in cursor:
            material = dict(row)
            rowid = int(material.get("_snapshot_rowid") or 0)
            max_rowid = max(max_rowid, rowid)
            digest.update(json.dumps(
                material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=str,
            ).encode("utf-8"))
            digest.update(b"\n")
            rows += 1
    except sqlite3.Error as exc:
        marker = {"status": "unavailable", "error": type(exc).__name__}
        return {"status": "unavailable", "rows": 0, "max_rowid": 0,
                "digest": _state_digest(marker)}
    return {"status": "ok", "rows": rows, "max_rowid": max_rowid,
            "digest": digest.hexdigest()}


def _snapshot_binding(conn: sqlite3.Connection, *, event_watermark: int,
                      retrieval: Mapping[str, Any],
                      query_params: Mapping[str, Any],
                      catalog: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, str], Dict[str, Any]]:
    relation = _relation_watermark(conn, event_watermark)
    catalog_state = catalog_snapshot(catalog)
    fts = dict(retrieval.get("fts") or {})
    binding = {
        "relation_watermark": str(relation["digest"]),
        "catalog_digest": str(catalog_state["digest"]),
        "retrieval_mode": str(retrieval.get("mode") or "unknown"),
        "retrieval_watermark": _state_digest({
            "mode": retrieval.get("mode"),
            "query_semantics": retrieval.get("query_semantics"),
            "query_params": dict(query_params),
        }),
        "fts_watermark": str(fts.get("watermark") or _state_digest({"status": "unknown"})),
    }
    public = {
        **binding,
        "relation_rows": relation["rows"],
        "relation_status": relation["status"],
        "catalog_asofs": list(catalog_state.get("catalog_asofs") or []),
        "catalog_entity_count": int(catalog_state.get("entity_count") or 0),
        "catalog_industry_count": int(catalog_state.get("industry_count") or 0),
    }
    return binding, public


def _assert_cursor_snapshot(decoded: Mapping[str, Any], current: Mapping[str, str]) -> None:
    if decoded.get("s") != dict(current):
        raise EventsSearchError(
            "cursor_snapshot_changed",
            "cursor 对应的关系、目录或检索快照已变化，请重新检索",
        )


def _json_obj(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        value = dict(raw)
        return {} if _json_nesting_exceeds_cap(value) else value
    if not raw:
        return {}
    text = str(raw)
    if len(text) > _MAX_JSON_STRING_CHARS:
        return {}
    try:
        value = json.loads(text)
        if not isinstance(value, Mapping):
            return {}
        out = dict(value)
        return {} if _json_nesting_exceeds_cap(out) else out
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError, MemoryError):
        return {}


def _normalise_sensitive_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().casefold()).strip("_")


def _is_secret_key(key: Any) -> bool:
    low = _normalise_sensitive_key(key)
    return low in _SECRET_KEYS or low.endswith((
        "_api_key", "_credential", "_password", "_secret", "_signature", "_token",
    ))


def _is_sensitive_query_key(key: Any) -> bool:
    low = _normalise_sensitive_key(unquote_plus(str(key or "")))
    return _is_secret_key(low) or low in _SENSITIVE_QUERY_KEYS


def _is_absolute_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.casefold().startswith("file:"):
        return True
    return PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()


def _contains_local_path(value: str) -> bool:
    return bool(
        _FILE_URI_RE.search(value)
        or _WINDOWS_DEVICE_PATH_RE.search(value)
        or _WINDOWS_PATH_RE.search(value)
        or _POSIX_PATH_RE.search(value)
    )


def _decoded_url_layers(value: str) -> Iterable[str]:
    """Yield raw plus at most three percent-decoded forms for detection only."""
    current = value
    yield current
    for _ in range(_MAX_URL_DECODE_LAYERS):
        decoded = unquote_plus(current)
        if decoded == current:
            break
        current = decoded
        yield current


def _url_decode_exceeds_cap(value: str) -> bool:
    """Fail closed when detection would require more than the bounded decode budget."""
    current = value
    for _ in range(_MAX_URL_DECODE_LAYERS):
        decoded = unquote_plus(current)
        if decoded == current:
            return False
        current = decoded
    return unquote_plus(current) != current


def _url_has_sensitive_material(url: str, *, depth: int) -> bool:
    if depth < 0:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return "@" in url
    if "@" in parts.netloc:
        return True
    return (
        _url_component_has_sensitive_material(parts.query, depth=depth)
        or _url_component_has_sensitive_material(parts.fragment, depth=depth)
    )


def _is_benign_web_route(value: str, *, parameter_key: Optional[str]) -> bool:
    key = _normalise_sensitive_key(unquote_plus(str(parameter_key or "")))
    text = value.strip()
    if key not in _WEB_ROUTE_QUERY_KEYS or not text.startswith("/") or text.startswith("//"):
        return False
    route_path = re.split(r"[?#]", text, maxsplit=1)[0]
    if "\\" in route_path or "\x00" in route_path:
        return False
    segments = [segment for segment in route_path.split("/") if segment]
    if not segments or any(segment in {".", ".."} for segment in segments):
        return False
    if segments[0].casefold() not in _WEB_ROUTE_ROOT_ALLOWLIST:
        return False
    if _WINDOWS_PATH_RE.search(route_path) or _FILE_URI_RE.search(route_path):
        return False
    suffix = text[len(route_path):]
    return not _contains_local_path(suffix)


def _url_value_has_sensitive_material(
    value: str, *, depth: int, parameter_key: Optional[str] = None,
) -> bool:
    if _url_decode_exceeds_cap(value):
        return True
    for decoded in _decoded_url_layers(value):
        if (
            _HIGH_SIGNAL_SECRET_RE.search(decoded)
            or _AUTH_SCHEME_RE.search(decoded)
            or _QUOTED_SECRET_ASSIGNMENT_RE.search(decoded)
            or _BARE_SECRET_ASSIGNMENT_RE.search(decoded)
        ):
            return True
        if decoded.startswith("//") and _url_has_sensitive_material(
            f"https:{decoded}", depth=depth - 1,
        ):
            return True
        if _contains_local_path(decoded) and not _is_benign_web_route(
            decoded, parameter_key=parameter_key,
        ):
            return True
        if depth > 0:
            for match in _URL_RE.finditer(decoded):
                if _url_has_sensitive_material(match.group(0), depth=depth - 1):
                    return True
    return False


def _url_component_has_sensitive_material(component: str, *, depth: int) -> bool:
    if not component:
        return False
    pieces = re.split(r"[&;]", component)
    for pair in pieces:
        if not pair:
            continue
        key, separator, raw_value = pair.partition("=")
        if _is_sensitive_query_key(key):
            return True
        candidate = raw_value if separator else pair
        if _url_value_has_sensitive_material(
            candidate, depth=depth, parameter_key=key if separator else None,
        ):
            return True
    return False


def _sanitize_url_component(component: str, *, depth: int = _MAX_URL_DECODE_LAYERS) -> str:
    """Redact query/fragment credentials without re-encoding benign URL text."""
    if not component:
        return component
    pieces = re.split(r"([&;])", component)
    for index in range(0, len(pieces), 2):
        pair = pieces[index]
        if not pair:
            continue
        key, separator, raw_value = pair.partition("=")
        if _is_sensitive_query_key(key):
            pieces[index] = f"{key}={_REDACTED}"
            continue
        if separator:
            if _url_value_has_sensitive_material(
                raw_value, depth=depth, parameter_key=key,
            ):
                pieces[index] = f"{key}={_REDACTED}"
        elif _url_value_has_sensitive_material(pair, depth=depth):
            pieces[index] = _REDACTED
    return "".join(pieces)


def _sanitize_url(url: str, *, depth: int = _MAX_URL_DECODE_LAYERS) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _REDACTED if "@" in url else url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return urlunsplit((
        parts.scheme,
        netloc,
        parts.path,
        _sanitize_url_component(parts.query, depth=depth),
        _sanitize_url_component(parts.fragment, depth=depth),
    ))


def _looks_like_json_container(value: str) -> bool:
    text = value.lstrip()
    return text.startswith(("{", "[", '"{', '"['))


def _json_nesting_exceeds_cap(value: Union[Dict[str, Any], List[Any]]) -> bool:
    stack: List[Tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_JSON_NESTING:
            return True
        children: Iterable[Any]
        if isinstance(current, dict):
            children = current.values()
        elif isinstance(current, list):
            children = current
        else:
            continue
        stack.extend(
            (child, depth + 1)
            for child in children
            if isinstance(child, (dict, list))
        )
    return False


def _try_parse_json_container(value: str) -> Any:
    """Parse JSON through bounded size, nesting and escape budgets.

    Returns ``(container, encodings)``, ``None`` for ordinary non-JSON text, or
    a private refusal sentinel when JSON-like input cannot be handled safely.
    """
    json_like = _looks_like_json_container(value)
    if len(value) > _MAX_JSON_STRING_CHARS:
        return _JSON_PARSE_REFUSED if json_like else None
    candidate = value.strip()
    if not candidate:
        return None
    unescapes = 0
    encodings: List[str] = []
    while True:
        try:
            parsed = json.loads(candidate)
        except (RecursionError, MemoryError):
            return _JSON_PARSE_REFUSED if json_like else None
        except (TypeError, ValueError, json.JSONDecodeError):
            if unescapes >= _MAX_JSON_UNESCAPE_LAYERS or r'\"' not in candidate:
                return None
            try:
                unwrapped = json.loads(f'"{candidate}"')
            except (RecursionError, MemoryError):
                return _JSON_PARSE_REFUSED if json_like else None
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if not isinstance(unwrapped, str) or unwrapped == candidate:
                return None
            candidate = unwrapped.strip()
            unescapes += 1
            encodings.append("escaped")
            continue
        if isinstance(parsed, (dict, list)):
            if _json_nesting_exceeds_cap(parsed):
                return _JSON_PARSE_REFUSED
            return parsed, tuple(encodings)
        if (
            isinstance(parsed, str)
            and unescapes < _MAX_JSON_UNESCAPE_LAYERS
            and parsed.strip() != candidate
            and parsed.strip().startswith(("{", "["))
        ):
            candidate = parsed.strip()
            unescapes += 1
            encodings.append("quoted")
            continue
        return None


def _sanitize_outbound_string(value: str) -> str:
    """Redact embedded credentials/paths while preserving surrounding evidence."""
    if not value:
        return value

    parsed_json = _try_parse_json_container(value)
    if parsed_json is _JSON_PARSE_REFUSED:
        return _REDACTED
    if parsed_json is not None:
        container, encodings = parsed_json
        try:
            serialized = json.dumps(
                sanitize_outbound(container),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (RecursionError, MemoryError):
            return _REDACTED
        for encoding in reversed(encodings):
            wrapped = json.dumps(serialized, ensure_ascii=False)
            serialized = wrapped if encoding == "quoted" else wrapped[1:-1]
        return serialized
    if _PAYLOAD_JSON_ASSIGNMENT_RE.search(value):
        return _REDACTED

    urls: List[str] = []

    def stash_url(match: re.Match[str]) -> str:
        urls.append(_sanitize_url(match.group(0)))
        return f"\x00GL_EVENT_URL_{len(urls) - 1}\x00"

    def stash_network_url(match: re.Match[str]) -> str:
        sanitized = _sanitize_url(f"https:{match.group(0)}")
        urls.append(sanitized.removeprefix("https:"))
        return f"\x00GL_EVENT_URL_{len(urls) - 1}\x00"

    text = _NETWORK_USERINFO_URL_RE.sub(stash_network_url, value)
    text = _URL_RE.sub(stash_url, text)
    text = _QUOTED_LOCAL_PATH_RE.sub(
        lambda match: (
            f"{match.group('quote')}{_REDACTED_PATH}{match.group('quote')}"
        ),
        text,
    )
    text = _FILE_URI_RE.sub(_REDACTED_PATH, text)
    text = _WINDOWS_DEVICE_PATH_RE.sub(_REDACTED_PATH, text)
    text = _WINDOWS_PATH_RE.sub(_REDACTED_PATH, text)
    text = _POSIX_PATH_RE.sub(_REDACTED_PATH, text)
    text = _QUOTED_SECRET_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('value_quote')}"
            f"{_REDACTED}{match.group('value_quote')}"
        ),
        text,
    )
    text = _BARE_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED}",
        text,
    )
    text = _AUTH_SCHEME_RE.sub(
        lambda match: f"{match.group('scheme')} {_REDACTED}",
        text,
    )
    text = _HIGH_SIGNAL_SECRET_RE.sub(_REDACTED, text)
    for index, url in enumerate(urls):
        text = text.replace(f"\x00GL_EVENT_URL_{index}\x00", url)
    return text


def sanitize_outbound(value: Any) -> Any:
    """Apply one recursive sanitizer to every Events value crossing a read boundary."""
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, child in value.items():
            low = _normalise_sensitive_key(key)
            if (
                low in _PATH_KEYS
                or low in _PAYLOAD_JSON_KEYS
                or low.endswith(("_payload_json", "_payloadjson"))
            ):
                continue
            safe_key = _sanitize_outbound_string(str(key))
            if _is_secret_key(key):
                out[safe_key] = _REDACTED
            else:
                out[safe_key] = sanitize_outbound(child)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_outbound(x) for x in value]
    if isinstance(value, str):
        return _sanitize_outbound_string(value)
    return value


def _redact_provenance(value: Any) -> Any:
    """Backward-compatible private name for the unified outbound sanitizer."""
    return sanitize_outbound(value)


def _decode_tickers(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError, MemoryError):
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def _decorate_row(row: Mapping[str, Any], *, asof: str,
                  retrieval: Mapping[str, Any], conn: sqlite3.Connection,
                  catalog: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = dict(row)
    raw.pop("_rowid", None)
    raw["tickers"] = _decode_tickers(raw.get("tickers"))
    raw["issuer_ids"] = _decode_tickers(raw.get("issuer_ids"))
    raw["context_ids"] = _decode_tickers(raw.get("context_ids"))
    persisted_provenance = _redact_provenance(_json_obj(raw.pop("provenance_json", None)))
    # payload_json remains stored/auditable but is not sent through the unauthenticated
    # MCP surface; its digest/logical locator belongs in provenance.
    raw.pop("payload_json", None)
    raw.setdefault("time_classification", "observed")
    raw.setdefault("classification_reason", "legacy_default")
    raw["provenance"] = {
        **persisted_provenance,
        "store": "events.db",
        "event_id": raw.get("event_id"),
        "source": raw.get("source"),
        "source_id": raw.get("source_id") or raw.get("ingested_from"),
        "source_ref": raw.get("source_ref"),
        "source_asof": raw.get("source_asof") or raw.get("available_at"),
        "ingested_from": raw.get("ingested_from"),
        "available_at": raw.get("available_at"),
        "pit_available_at": raw.get("available_at"),
        "event_time": raw.get("event_time"),
        "subject_kind": raw.get("subject_kind"),
        "context_type": raw.get("context_type"),
        "line_id": raw.get("line_id"),
        "retrieval_asof": asof,
        "pit_predicate": store.PIT_PREDICATE,
        "retrieval_mode": retrieval.get("mode"),
    }
    linked = link_event_candidates(raw, conn=conn, catalog=catalog, asof=asof)
    # Relations may carry persisted reviewer evidence as well as generated
    # evidence; apply the same outbound redaction policy to the whole item.
    return _redact_provenance(linked)


def _time_matches(item: Mapping[str, Any], start: Optional[datetime],
                  end: Optional[datetime]) -> bool:
    if start is None and end is None:
        return True
    value = _event_dt(item.get("event_time"))
    if value is None:
        return False
    return (start is None or value >= start) and (end is None or value <= end)


def search_events_page(query: str, asof: Any, *,
                       source: Any = None, sources: Any = None,
                       time_from: Any = None, time_to: Any = None,
                       industry: Any = None, industries: Any = None,
                       entity: Any = None, entities: Any = None,
                       limit: int = 20, cursor: Optional[str] = None,
                       conn: Optional[sqlite3.Connection] = None,
                       path: Optional[Union[str, "os.PathLike[str]"]] = None,
                       catalog: Optional[Mapping[str, Any]] = None,
                       available_from: Optional[str] = None) -> Dict[str, Any]:
    """Strict read-only keyset search.  Raises :class:`EventsSearchError`."""
    query = _bounded_text(query, name="query", max_chars=_MAX_QUERY_CHARS)
    cursor_text = _bounded_text(
        cursor, name="cursor", max_chars=_MAX_CURSOR_CHARS,
        code="cursor_too_long",
    )
    asof_text = _bounded_text(
        asof, name="asof", max_chars=_MAX_TIME_CHARS,
        code="filter_value_too_long",
    )
    canonical_asof = _canonical_asof(asof_text)
    page_limit = _normalise_limit(limit)
    source_filters = _strings(source, sources, name="source")
    industry_filters = _strings(industry, industries, name="industry")
    entity_filters = _strings(entity, entities, name="entity")
    time_from_text = _bounded_text(
        time_from, name="time_from", max_chars=_MAX_TIME_CHARS,
        code="filter_value_too_long",
    )
    time_to_text = _bounded_text(
        time_to, name="time_to", max_chars=_MAX_TIME_CHARS,
        code="filter_value_too_long",
    )
    available_from_text = _bounded_text(
        available_from, name="available_from", max_chars=_MAX_TIME_CHARS,
        code="filter_value_too_long",
    )
    start_dt = _parse_local(time_from_text, end_of_date=False) if time_from else None
    end_dt = _parse_local(time_to_text, end_of_date=True) if time_to else None
    if start_dt and end_dt and start_dt > end_dt:
        raise EventsSearchError("invalid_time_range", "time_from 不得晚于 time_to")
    available_floor = None
    if available_from:
        available_floor = _parse_local(
            available_from_text, end_of_date=False,
        ).replace(tzinfo=None).isoformat()

    request = {
        "asof": canonical_asof,
        "query": query,
        "sources": sorted(source_filters),
        "time_from": start_dt.isoformat() if start_dt else None,
        "time_to": end_dt.isoformat() if end_dt else None,
        "industries": sorted(industry_filters),
        "entities": sorted(entity_filters),
        "available_from": available_floor,
    }
    fp = _fingerprint(request)
    decoded = _decode_cursor(cursor_text, fingerprint=fp) if cursor_text else None
    own = False
    if conn is None:
        try:
            conn = store.connect_readonly(path=path)
        except (FileNotFoundError, sqlite3.Error, OSError) as exc:
            raise EventsSearchError("events_db_unavailable", "events.db 只读打开失败") from exc
        own = True
    try:
        if not _table_exists(conn, "events"):
            raise EventsSearchError("events_schema_incompatible", "events.db 缺少 events 表")
        try:
            current_watermark = int(
                conn.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0]
            )
        except sqlite3.Error as exc:
            raise EventsSearchError("events_schema_incompatible", "events 表不可读") from exc
        watermark = current_watermark
        after_available: Optional[str] = None
        after_event_id: Optional[str] = None
        if decoded is not None:
            watermark = decoded["wm"]
            if watermark > current_watermark:
                raise EventsSearchError(
                    "cursor_snapshot_changed",
                    "cursor 对应的关系、目录或检索快照已变化，请重新检索",
                )
            after_available = str(decoded["a"])
            after_event_id = str(decoded["e"])

        predicate, query_params, retrieval = _query_plan(conn, query, watermark)
        snapshot_binding, snapshot_public = _snapshot_binding(
            conn, event_watermark=watermark, retrieval=retrieval,
            query_params=query_params, catalog=catalog,
        )
        if decoded is not None:
            _assert_cursor_snapshot(decoded, snapshot_binding)
        base_where = [
            f"({store.PIT_PREDICATE})",
            "e.rowid <= :watermark",
            f"({predicate})",
        ]
        base_params: Dict[str, Any] = {
            "asof": canonical_asof,
            "watermark": watermark,
            **query_params,
        }
        if source_filters:
            marks = []
            for i, value in enumerate(source_filters):
                key = f"source_{i}"
                marks.append(f":{key}")
                base_params[key] = value
            joined = ",".join(marks)
            base_where.append(f"(e.source IN ({joined}) OR e.ingested_from IN ({joined}))")
        if available_floor:
            base_where.append("e.available_at >= :available_from")
            base_params["available_from"] = available_floor

        accepted: List[Dict[str, Any]] = []
        exhausted = False
        scan_available, scan_event_id = after_available, after_event_id
        while len(accepted) < page_limit + 1 and not exhausted:
            where = list(base_where)
            params = dict(base_params)
            if scan_available is not None and scan_event_id is not None:
                where.append("(e.available_at < :last_available OR "
                             "(e.available_at = :last_available AND e.event_id < :last_event_id))")
                params.update(last_available=scan_available, last_event_id=scan_event_id)
            params["scan_limit"] = _SCAN_CHUNK
            sql = (
                "SELECT e.rowid AS _rowid,e.* FROM events e WHERE "
                + " AND ".join(where)
                + " ORDER BY e.available_at DESC,e.event_id DESC LIMIT :scan_limit"
            )
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                # SQLite may have an FTS table but no working FTS5 module/corrupt
                # index. Restart the page deterministically in LIKE mode.
                if retrieval.get("mode", "").startswith("fts"):
                    if decoded is not None:
                        raise EventsSearchError(
                            "cursor_snapshot_changed",
                            "cursor 对应的关系、目录或检索快照已变化，请重新检索",
                        ) from exc
                    retrieval = {"mode": "like", "fts": {
                        **(retrieval.get("fts") or {}), "status": "unavailable",
                        "reason": f"fts_query_error:{type(exc).__name__}",
                    }, "query_semantics": "token_or"}
                    try:
                        fallback_tokens = _query_tokens(query)
                    except Exception:  # noqa: BLE001 - fixed whole-query degradation
                        fallback_tokens = (query,)
                        retrieval["query_semantics"] = "whole_query_like"
                    predicate, query_params = _token_like_plan(fallback_tokens)
                    base_where[2] = f"({predicate})"
                    base_params = {k: v for k, v in base_params.items()
                                   if k != "fts_q" and not k.startswith("like_")}
                    base_params.update(query_params)
                    snapshot_binding, snapshot_public = _snapshot_binding(
                        conn, event_watermark=watermark, retrieval=retrieval,
                        query_params=query_params, catalog=catalog,
                    )
                    accepted = []
                    scan_available, scan_event_id = after_available, after_event_id
                    continue
                raise EventsSearchError("events_query_failed", "events 查询失败") from exc
            exhausted = len(rows) < _SCAN_CHUNK
            if not rows:
                break
            for row in rows:
                scan_available = str(row["available_at"])
                scan_event_id = str(row["event_id"])
                item = _decorate_row(row, asof=canonical_asof, retrieval=retrieval,
                                     conn=conn, catalog=catalog)
                if not _time_matches(item, start_dt, end_dt):
                    continue
                if not relation_matches(item, industries=industry_filters, entities=entity_filters):
                    continue
                accepted.append(item)
                if len(accepted) >= page_limit + 1:
                    break

        has_more = len(accepted) > page_limit
        items = accepted[:page_limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(
                fingerprint=fp, watermark=watermark,
                available_at=str(last.get("available_at") or ""),
                event_id=str(last.get("event_id") or ""),
                snapshot=snapshot_binding,
            )
        result = {
            "ok": True,
            "items": items,
            "n": len(items),
            "asof": canonical_asof,
            "query": query,
            "filters": {
                "sources": list(source_filters), "time_from": time_from_text,
                "time_to": time_to_text, "industries": list(industry_filters),
                "entities": list(entity_filters),
            },
            "ordering": ["available_at:desc", "event_id:desc"],
            "snapshot_watermark": watermark,
            "snapshot": snapshot_public,
            "next_cursor": next_cursor,
            "retrieval": retrieval,
            "provenance": {
                "store": "events.db", "retrieval_asof": canonical_asof,
                "pit_predicate": store.PIT_PREDICATE,
                "snapshot_watermark": watermark,
            },
            "relation_policy": RELATION_POLICY,
            "issuer_fact": False,
        }
        return _redact_provenance(result)
    finally:
        if own:
            conn.close()


def events_search(query: str = "", asof: Any = None, **kwargs: Any) -> Dict[str, Any]:
    """Honest service boundary used by Console and ``/gl-mcp``.

    Unlike the lower-level store primitive, errors are returned as stable JSON
    so both tool transports expose the same boundary.
    """
    try:
        return search_events_page(query, asof, **kwargs)
    except EventsSearchError as exc:
        return {"ok": False, "items": [], "n": 0,
                "error": {"code": exc.code, "message": exc.message},
                "relation_policy": RELATION_POLICY, "issuer_fact": False}
    except Exception:  # noqa: BLE001 - one fixed, non-disclosing public boundary
        return {"ok": False, "items": [], "n": 0,
                "error": {"code": "events_internal_error",
                          "message": "事件检索内部错误"},
                "relation_policy": RELATION_POLICY, "issuer_fact": False}


__all__ = [
    "EventsSearchError", "events_search", "sanitize_outbound", "search_events_page",
]
