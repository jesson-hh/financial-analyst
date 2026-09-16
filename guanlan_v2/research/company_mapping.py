"""Auditable entity-to-dataset mappings for listed-company research.

The live Taiwan revenue, mainland commodity and control-list adapters accept
explicit query keys.  This module provides the deliberately narrower bridge
from an A-share issuer to those keys.  A mapping is never inferred here: the
immutable JSON baseline contains exact-code, source-pinned reviewed rows, while
SQLite writers can propose an evidence-backed version and a reviewer records a
decision.  Readers only see reviewed-table rows inside their validity window
or SQLite versions whose latest point-in-time decision is ``approve``.

Two safety properties are intentional:

* :func:`resolve_approved_mappings` opens an existing registry read-only.  A
  normal research preparation call therefore cannot create a global database.
* The reviewed JSON table is validated in full and exact-matched.  A malformed
  row or ambiguous alias fails the table closed; there is no LLM/fuzzy fallback.
* request-local ``explicit_targets`` are returned to the caller but are never
  persisted.  A one-off manual query cannot silently become global knowledge.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
RESOLUTION_SCHEMA = "company-mapping-resolution@1"
REVIEWED_TABLE_SCHEMA = "company-mapping-reviewed-table@1"
ENV_DB_PATH = "GUANLAN_COMPANY_MAPPING_DB"
ENV_REVIEWED_TABLE_PATH = "GUANLAN_COMPANY_MAPPING_REVIEWED_TABLE"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = _REPO_ROOT / "var" / "research" / "company_mappings.db"
_DEFAULT_REVIEWED_TABLE_PATH = Path(__file__).with_name(
    "company_mapping_reviewed.json"
)

TARGET_DATASETS = frozenset({
    "tw_monthly_revenue",
    "cn_commodity_futures",
    "control_lists",
})

_TW_COMPANY_ID = re.compile(r"^[0-9A-Z]{4,8}$")
_CN_FUTURES_SYMBOL = re.compile(r"^[A-Z]{1,3}(?:0|\d{3,4})$")
_EDGE_STATUSES = frozenset({"proposed", "approved", "rejected", "invalidated"})
_DECISIONS = frozenset({"approve", "reject", "invalidate"})
_REVIEWED_STATUSES = frozenset({"approved", "rejected", "invalidated"})


class MappingRegistryError(RuntimeError):
    """Base class for registry contract failures."""


class MappingConflictError(MappingRegistryError):
    """An id or natural key already represents a different object."""


class MappingNotFoundError(MappingRegistryError):
    """A referenced entity, edge or version does not exist."""


class AmbiguousEntityError(MappingRegistryError):
    """A subject alias points at more than one entity and cannot be guessed."""


def db_path(path: str | Path | None = None) -> Path:
    """Resolve explicit path, then env override, then the repository default."""
    if path is not None:
        return Path(path)
    configured = os.environ.get(ENV_DB_PATH)
    return Path(configured) if configured else _DEFAULT_DB_PATH


def reviewed_table_path(path: str | Path | None = None) -> Path:
    """Resolve the immutable reviewed-table input without creating it."""
    if path is not None:
        return Path(path)
    configured = os.environ.get(ENV_REVIEWED_TABLE_PATH)
    return Path(configured) if configured else _DEFAULT_REVIEWED_TABLE_PATH


def _normal_text(value: Any, *, field: str, limit: int = 500) -> str:
    text_value = " ".join(str(value or "").replace("\x00", " ").split())
    if not text_value:
        raise ValueError(f"{field} 不能为空")
    if len(text_value) > limit:
        raise ValueError(f"{field} 不能超过 {limit} 个字符")
    return text_value


def _normal_key(value: Any) -> str:
    text_value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text_value.split()).casefold()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 值不能包含 NaN/Infinity")
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    scalar = getattr(value, "item", None)
    if callable(scalar):
        return _json_safe(scalar())
    raise TypeError(f"不支持写入 JSON 的值类型: {type(value).__name__}")


def _json_body(value: Any) -> str:
    return json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_body(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{_digest(value)[:24]}"


def _utc_timestamp(
    value: str | date | datetime | None = None, *, date_is_end: bool = False,
) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(
            value, time.max if date_is_end else time.min, tzinfo=timezone.utc,
        )
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed_date = date.fromisoformat(raw)
            parsed = datetime.combine(
                parsed_date, time.max if date_is_end else time.min,
                tzinfo=timezone.utc,
            )
        else:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("时间必须是 ISO-8601 日期或时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_json(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return fallback


def _target_key(dataset: str, value: Any) -> tuple[str, str]:
    dataset = str(dataset or "").strip()
    if dataset not in TARGET_DATASETS:
        raise ValueError(
            f"target_dataset 必须是 {', '.join(sorted(TARGET_DATASETS))} 之一"
        )
    key = _normal_text(value, field="target_key", limit=200)
    if dataset == "tw_monthly_revenue":
        key = key.upper()
        if not _TW_COMPANY_ID.fullmatch(key):
            raise ValueError("台系月营收 target_key 必须是 4-8 位数字/大写字母公司代码")
    elif dataset == "cn_commodity_futures":
        key = key.upper().removeprefix("NF_")
        if not _CN_FUTURES_SYMBOL.fullmatch(key):
            raise ValueError("商品期货 target_key 必须是实际查询接口支持的合约代码")
    return key, _normal_key(key)


def _normal_evidence(evidence: Iterable[str | Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in evidence or ():
        if isinstance(raw, str):
            row = {"source_ref": _normal_text(raw, field="evidence.source_ref", limit=2000)}
        elif isinstance(raw, Mapping):
            row = dict(_json_safe(raw))
            source_ref = row.get("source_ref") or row.get("url") or row.get("asset_id")
            if not source_ref:
                raise ValueError("每条 evidence 必须包含 source_ref、url 或 asset_id")
            row["source_ref"] = _normal_text(
                source_ref, field="evidence.source_ref", limit=2000,
            )
        else:
            raise TypeError("evidence 每项必须是字符串或对象")
        rows.append(row)
    if not rows:
        raise ValueError("全局映射必须至少有一条可审计 evidence")
    # Evidence order must not create a spurious new version.
    unique = {_json_body(row): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _normal_mapping(value: Mapping[str, Any] | None, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} 必须是对象")
    return dict(_json_safe(value))


def _normal_a_share_code(value: Any) -> str:
    """Return one exact exchange-qualified A-share code; never fuzzy-match."""
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    raw = re.sub(r"[.\-_: ]", "", raw)
    match = re.fullmatch(r"(?:(SH|SZ|BJ))?(\d{6})", raw)
    if match is None:
        raise ValueError("security_code 必须是六位 A 股代码或 SH/SZ/BJ 前缀代码")
    exchange, digits = match.groups()
    inferred = (
        "SH" if digits.startswith("6")
        else "SZ" if digits.startswith(("0", "3"))
        else "BJ" if digits.startswith(("4", "8", "9"))
        else ""
    )
    if not inferred or (exchange and exchange != inferred):
        raise ValueError("security_code 的交易所前缀与代码段不一致")
    return f"{exchange or inferred}{digits}"


def _require_keys(
    value: Mapping[str, Any], *, allowed: set[str], required: set[str], field: str,
) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise MappingRegistryError(
            f"{field} 缺少字段: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise MappingRegistryError(
            f"{field} 含未审核字段: {', '.join(sorted(unknown))}"
        )


def _reviewed_target_entity(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MappingRegistryError(f"{field} 必须是对象")
    allowed = {
        "entity_id", "entity_type", "canonical_name", "jurisdiction",
        "identifiers", "metadata",
    }
    _require_keys(
        value, allowed=allowed,
        required={"entity_id", "entity_type", "canonical_name", "jurisdiction"},
        field=field,
    )
    return {
        "entity_id": _normal_text(value["entity_id"], field=f"{field}.entity_id", limit=200),
        "entity_type": _normal_text(
            value["entity_type"], field=f"{field}.entity_type", limit=100,
        ),
        "canonical_name": _normal_text(
            value["canonical_name"], field=f"{field}.canonical_name", limit=500,
        ),
        "jurisdiction": _normal_text(
            value["jurisdiction"], field=f"{field}.jurisdiction", limit=100,
        ),
        "identifiers": _normal_mapping(
            value.get("identifiers"), f"{field}.identifiers",
        ),
        "metadata": _normal_mapping(value.get("metadata"), f"{field}.metadata"),
    }


def _reviewed_mapping_row(
    raw: Any, *, subject_entity_id: str, table_version: str,
    table_digest: str, table_reviewer: str, table_reviewed_at: str,
    field: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise MappingRegistryError(f"{field} 必须是对象")
    allowed = {
        "status", "version", "target_dataset", "target_key", "target_entity",
        "relationship", "context", "confidence", "evidence", "reviewer",
        "reviewed_at", "valid_from", "valid_to", "reason", "metadata",
    }
    required = {
        "status", "version", "target_dataset", "target_key", "target_entity",
        "relationship", "context", "evidence", "reviewer", "reviewed_at",
        "valid_from", "valid_to",
    }
    _require_keys(raw, allowed=allowed, required=required, field=field)
    status = str(raw["status"] or "").strip().lower()
    if status not in _REVIEWED_STATUSES:
        raise MappingRegistryError(
            f"{field}.status 必须是 {', '.join(sorted(_REVIEWED_STATUSES))} 之一"
        )
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise MappingRegistryError(f"{field}.version 必须是正整数")
    dataset = _normal_text(
        raw["target_dataset"], field=f"{field}.target_dataset", limit=100,
    )
    key, key_norm = _target_key(dataset, raw["target_key"])
    relationship = _normal_text(
        raw["relationship"], field=f"{field}.relationship", limit=200,
    )
    context = _normal_mapping(raw.get("context"), f"{field}.context")
    if context.get("scope") != "context":
        raise MappingRegistryError(
            f"{field}.context.scope 必须明确为 context，不能冒充公司自身事实"
        )
    evidence = _normal_evidence(raw.get("evidence") or [])
    reviewer = _normal_text(raw["reviewer"], field=f"{field}.reviewer", limit=200)
    reviewed_at = _utc_timestamp(raw["reviewed_at"])
    valid_from = _utc_timestamp(raw["valid_from"])
    valid_to_raw = raw.get("valid_to")
    valid_to = (
        _utc_timestamp(valid_to_raw, date_is_end=True)
        if valid_to_raw not in (None, "") else None
    )
    if valid_to is not None and valid_to < valid_from:
        raise MappingRegistryError(f"{field}.valid_to 不能早于 valid_from")
    reason = " ".join(str(raw.get("reason") or "").split())[:2000]
    if status != "approved" and not reason:
        raise MappingRegistryError(f"{field} 非 approved 行必须提供 reason")
    confidence = raw.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MappingRegistryError(f"{field}.confidence 必须是 0-1 数字")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise MappingRegistryError(f"{field}.confidence 必须在 0-1 之间")
    target_entity = _reviewed_target_entity(
        raw["target_entity"], field=f"{field}.target_entity",
    )
    metadata = _normal_mapping(raw.get("metadata"), f"{field}.metadata")
    payload = {
        "source_entity_id": subject_entity_id,
        "target_entity_id": target_entity["entity_id"],
        "target_dataset": dataset,
        "target_key": key,
        "relationship": relationship,
        "context": context,
        "confidence": confidence,
        "evidence": evidence,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "status": status,
        "version": version,
        "reason": reason,
        "metadata": metadata,
        "reviewed_table_version": table_version,
        "reviewed_table_sha256": table_digest,
    }
    edge_identity = {
        "source_entity_id": subject_entity_id,
        "target_entity_id": target_entity["entity_id"],
        "target_dataset": dataset,
        "target_key_norm": key_norm,
        "relationship": _normal_key(relationship),
    }
    edge_id = _stable_id("reviewed_edge", edge_identity)
    version_digest = _digest(payload)
    return {
        "edge_id": edge_id,
        "version_id": _stable_id(
            "reviewed_mapv", {
                "edge_id": edge_id, "version": version,
                "digest": version_digest,
            },
        ),
        "version": version,
        "version_digest": version_digest,
        "supersedes_version_id": None,
        "target_dataset": dataset,
        "target_key": key,
        "relationship": relationship,
        "context": context,
        "confidence": confidence,
        "evidence": evidence,
        "metadata": {
            **metadata,
            "reviewed_table_version": table_version,
            "reviewed_table_sha256": table_digest,
            "table_reviewer": table_reviewer,
            "table_reviewed_at": table_reviewed_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "review_status": status,
            "review_reason": reason,
        },
        "target_entity": target_entity,
        "approved_by": reviewer,
        "approved_at": reviewed_at,
        "approval_decision_id": _stable_id(
            "reviewed_decision", {
                "edge_id": edge_id, "version_digest": version_digest,
                "status": status, "reviewer": reviewer,
            },
        ),
        "origin": "reviewed_table",
        "review_status": status,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def _load_reviewed_table(
    path: Path, *, subject_code: str, as_of: str,
) -> dict[str, Any]:
    """Load and validate the whole immutable table before resolving one code."""
    if not path.is_file():
        return {
            "status": "not_configured", "subject": None, "edges": [],
            "warnings": ["reviewed_mapping_table_not_configured"],
            "metadata": {"path": str(path), "schema_version": None},
        }
    try:
        body = path.read_bytes()
        document = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MappingRegistryError(
            f"审核映射表不可读: {type(exc).__name__}"
        ) from exc
    if not isinstance(document, Mapping):
        raise MappingRegistryError("审核映射表顶层必须是对象")
    _require_keys(
        document,
        allowed={
            "schema_version", "table_version", "reviewer", "reviewed_at",
            "source_policy", "subjects",
        },
        required={
            "schema_version", "table_version", "reviewer", "reviewed_at",
            "source_policy", "subjects",
        },
        field="reviewed_table",
    )
    if document["schema_version"] != REVIEWED_TABLE_SCHEMA:
        raise MappingRegistryError("不支持的 reviewed mapping table schema")
    table_version = _normal_text(
        document["table_version"], field="reviewed_table.table_version", limit=100,
    )
    table_reviewer = _normal_text(
        document["reviewer"], field="reviewed_table.reviewer", limit=200,
    )
    table_reviewed_at = _utc_timestamp(document["reviewed_at"])
    source_policy = _normal_text(
        document["source_policy"], field="reviewed_table.source_policy", limit=1000,
    )
    subjects = document["subjects"]
    if not isinstance(subjects, list):
        raise MappingRegistryError("reviewed_table.subjects 必须是数组")
    table_digest = hashlib.sha256(body).hexdigest()
    alias_index: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    subject_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    row_count = 0
    active_count = 0
    horizon = as_of
    for index, raw_subject in enumerate(subjects):
        field = f"reviewed_table.subjects[{index}]"
        if not isinstance(raw_subject, Mapping):
            raise MappingRegistryError(f"{field} 必须是对象")
        _require_keys(
            raw_subject,
            allowed={
                "entity_id", "security_code", "canonical_name", "jurisdiction",
                "aliases", "identifiers", "metadata", "mappings",
            },
            required={
                "entity_id", "security_code", "canonical_name", "jurisdiction",
                "aliases", "identifiers", "metadata", "mappings",
            },
            field=field,
        )
        code = _normal_a_share_code(raw_subject["security_code"])
        entity = {
            "entity_id": _normal_text(
                raw_subject["entity_id"], field=f"{field}.entity_id", limit=200,
            ),
            "entity_type": "listed_issuer",
            "canonical_name": _normal_text(
                raw_subject["canonical_name"],
                field=f"{field}.canonical_name", limit=500,
            ),
            "jurisdiction": _normal_text(
                raw_subject["jurisdiction"],
                field=f"{field}.jurisdiction", limit=100,
            ),
            "identifiers": _normal_mapping(
                raw_subject.get("identifiers"), f"{field}.identifiers",
            ),
            "metadata": _normal_mapping(
                raw_subject.get("metadata"), f"{field}.metadata",
            ),
        }
        if entity["jurisdiction"] != "CN":
            raise MappingRegistryError(f"{field}.jurisdiction 必须是 CN")
        aliases = raw_subject["aliases"]
        if not isinstance(aliases, list):
            raise MappingRegistryError(f"{field}.aliases 必须是数组")
        lookup_values = [code, code[2:], entity["canonical_name"], *aliases]
        mappings = raw_subject["mappings"]
        if not isinstance(mappings, list):
            raise MappingRegistryError(f"{field}.mappings 必须是数组")
        parsed_rows = [
            _reviewed_mapping_row(
                row, subject_entity_id=entity["entity_id"],
                table_version=table_version, table_digest=table_digest,
                table_reviewer=table_reviewer,
                table_reviewed_at=table_reviewed_at,
                field=f"{field}.mappings[{row_index}]",
            )
            for row_index, row in enumerate(mappings)
        ]
        natural_keys: set[tuple[str, str]] = set()
        for row in parsed_rows:
            natural = (
                row["target_dataset"], _normal_key(row["target_key"]),
            )
            if natural in natural_keys:
                raise MappingRegistryError(f"{field}.mappings 含重复审核边")
            natural_keys.add(natural)
        subject_rows.append((entity, parsed_rows))
        for raw_alias in lookup_values:
            alias = _normal_key(_normal_text(raw_alias, field=f"{field}.alias", limit=500))
            existing = alias_index.get(alias)
            if existing is not None and existing[0]["entity_id"] != entity["entity_id"]:
                raise AmbiguousEntityError(
                    f"审核映射表 alias {raw_alias!r} 同时属于多个主体"
                )
            alias_index[alias] = (entity, parsed_rows)
        row_count += len(parsed_rows)
        active_count += sum(
            1 for row in parsed_rows
            if row["review_status"] == "approved"
            and row["approved_at"] <= horizon
            and row["valid_from"] <= horizon
            and (row["valid_to"] is None or horizon <= row["valid_to"])
        )

    query_key = _normal_key(subject_code)
    resolved = alias_index.get(query_key)
    selected: list[dict[str, Any]] = []
    selected_subject = None
    inactive_rows = 0
    if resolved is not None:
        selected_subject, candidate_rows = resolved
        for row in candidate_rows:
            active = (
                row["review_status"] == "approved"
                and row["approved_at"] <= horizon
                and row["valid_from"] <= horizon
                and (row["valid_to"] is None or horizon <= row["valid_to"])
            )
            if active:
                selected.append(row)
            else:
                inactive_rows += 1
    status = (
        "ok" if selected else "inactive" if resolved is not None else "unmapped"
    )
    return {
        "status": status,
        "subject": selected_subject,
        "edges": selected,
        "warnings": (
            ["reviewed_mapping_rows_inactive"] if inactive_rows else []
        ),
        "metadata": {
            "path": str(path),
            "schema_version": REVIEWED_TABLE_SCHEMA,
            "table_version": table_version,
            "sha256": table_digest,
            "reviewer": table_reviewer,
            "reviewed_at": table_reviewed_at,
            "source_policy": source_policy,
            "subject_count": len(subject_rows),
            "mapping_count": row_count,
            "active_mapping_count_at_as_of": active_count,
            "matched_subject": selected_subject is not None,
            "matched_mapping_count": len(selected),
            "inactive_matched_mapping_count": inactive_rows,
        },
    }


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS mapping_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
        entity_id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        canonical_name_norm TEXT NOT NULL,
        jurisdiction TEXT NOT NULL DEFAULT '',
        identifiers_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(entity_type, jurisdiction, canonical_name_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aliases (
        alias_id TEXT PRIMARY KEY,
        entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
        alias_text TEXT NOT NULL,
        alias_norm TEXT NOT NULL,
        alias_type TEXT NOT NULL,
        source_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        invalidated_at TEXT,
        UNIQUE(entity_id, alias_norm, alias_type, source_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mapping_edges (
        edge_id TEXT PRIMARY KEY,
        source_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
        target_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE RESTRICT,
        target_dataset TEXT NOT NULL,
        target_key TEXT NOT NULL,
        target_key_norm TEXT NOT NULL,
        relationship TEXT NOT NULL,
        current_version_id TEXT,
        status TEXT NOT NULL DEFAULT 'proposed',
        active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        invalidated_at TEXT,
        CHECK(target_dataset IN ('tw_monthly_revenue','cn_commodity_futures','control_lists')),
        CHECK(status IN ('proposed','approved','rejected','invalidated')),
        UNIQUE(source_entity_id, target_entity_id, target_dataset,
               target_key_norm, relationship)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mapping_versions (
        version_id TEXT PRIMARY KEY,
        edge_id TEXT NOT NULL REFERENCES mapping_edges(edge_id) ON DELETE RESTRICT,
        version INTEGER NOT NULL CHECK(version >= 1),
        digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        supersedes_version_id TEXT REFERENCES mapping_versions(version_id) ON DELETE RESTRICT,
        invalidated_at TEXT,
        UNIQUE(edge_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mapping_decisions (
        decision_id TEXT PRIMARY KEY,
        edge_id TEXT NOT NULL REFERENCES mapping_edges(edge_id) ON DELETE RESTRICT,
        version_id TEXT NOT NULL REFERENCES mapping_versions(version_id) ON DELETE RESTRICT,
        decision TEXT NOT NULL,
        decided_by TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        decided_at TEXT NOT NULL,
        CHECK(decision IN ('approve','reject','invalidate'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alias_lookup ON aliases(alias_norm, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_mapping_source ON mapping_edges(source_entity_id, target_dataset)",
    "CREATE INDEX IF NOT EXISTS idx_mapping_versions_edge ON mapping_versions(edge_id, version)",
    "CREATE INDEX IF NOT EXISTS idx_mapping_decisions_version ON mapping_decisions(version_id, decided_at)",
)


@dataclass(frozen=True)
class _ResolvedSubject:
    query: str
    entity: dict[str, Any] | None


class CompanyMappingRegistry:
    """SQLite-backed mapping writer and resolver.

    Constructing this class is an explicit write-side action: it initializes
    the injected database.  Normal Agent reads should use the module-level
    :func:`resolve_approved_mappings`, which never creates a database.
    """

    def __init__(self, path: str | Path | None = None, *, initialize: bool = True):
        self.path = db_path(path)
        if initialize:
            self.init_db()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        return conn

    def init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                conn.execute(statement)
            current = conn.execute(
                "SELECT value FROM mapping_meta WHERE key='schema_version'"
            ).fetchone()
            if current is None:
                conn.execute(
                    "INSERT INTO mapping_meta(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
            elif current["value"] != str(SCHEMA_VERSION):
                raise MappingRegistryError(
                    f"不支持的 company mapping schema: {current['value']}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _entity_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row)
        return {
            "entity_id": raw["entity_id"],
            "entity_type": raw["entity_type"],
            "canonical_name": raw["canonical_name"],
            "jurisdiction": raw["jurisdiction"],
            "identifiers": _parse_json(raw.get("identifiers_json"), {}),
            "metadata": _parse_json(raw.get("metadata_json"), {}),
            "created_at": raw["created_at"],
            "updated_at": raw["updated_at"],
        }

    @staticmethod
    def _require_entity(conn: sqlite3.Connection, entity_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM entities WHERE entity_id=?", (entity_id,),
        ).fetchone()
        if row is None:
            raise MappingNotFoundError(f"entity 不存在: {entity_id}")
        return row

    @staticmethod
    def _insert_alias(
        conn: sqlite3.Connection, *, entity_id: str, alias: str,
        alias_type: str, source_ref: str, at: str,
    ) -> sqlite3.Row:
        alias_text = _normal_text(alias, field="alias", limit=500)
        alias_kind = _normal_text(alias_type, field="alias_type", limit=100)
        source = " ".join(str(source_ref or "").split())[:2000]
        identity = {
            "entity_id": entity_id,
            "alias_norm": _normal_key(alias_text),
            "alias_type": alias_kind,
            "source_ref": source,
        }
        alias_id = _stable_id("alias", identity)
        conn.execute(
            """INSERT OR IGNORE INTO aliases(
                   alias_id,entity_id,alias_text,alias_norm,alias_type,source_ref,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (alias_id, entity_id, alias_text, identity["alias_norm"], alias_kind, source, at),
        )
        return conn.execute("SELECT * FROM aliases WHERE alias_id=?", (alias_id,)).fetchone()

    def upsert_entity(
        self, *, entity_type: str, canonical_name: str, jurisdiction: str = "",
        identifiers: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        entity_id: str | None = None,
        at: str | date | datetime | None = None,
    ) -> dict[str, Any]:
        """Create or enrich an entity without guessing identity merges."""
        kind = _normal_text(entity_type, field="entity_type", limit=100)
        name = _normal_text(canonical_name, field="canonical_name", limit=500)
        place = " ".join(str(jurisdiction or "").split())[:100]
        ids = _normal_mapping(identifiers, "identifiers")
        meta = _normal_mapping(metadata, "metadata")
        natural = {"entity_type": kind, "jurisdiction": place, "name": _normal_key(name)}
        chosen_id = entity_id or _stable_id("ent", natural)
        chosen_id = _normal_text(chosen_id, field="entity_id", limit=200)
        timestamp = _utc_timestamp(at)
        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM entities WHERE entity_id=?", (chosen_id,),
            ).fetchone()
            natural_existing = conn.execute(
                """SELECT * FROM entities
                   WHERE entity_type=? AND jurisdiction=? AND canonical_name_norm=?""",
                (kind, place, natural["name"]),
            ).fetchone()
            if natural_existing is not None and natural_existing["entity_id"] != chosen_id:
                raise MappingConflictError(
                    "相同类型、辖区和法定名称已经属于另一个 entity_id"
                )
            if existing is None:
                conn.execute(
                    """INSERT INTO entities(
                           entity_id,entity_type,canonical_name,canonical_name_norm,jurisdiction,
                           identifiers_json,metadata_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (chosen_id, kind, name, natural["name"], place,
                     _json_body(ids), _json_body(meta), timestamp, timestamp),
                )
            else:
                if existing["entity_type"] != kind or existing["jurisdiction"] != place:
                    raise MappingConflictError(
                        "entity_id 已存在，但 entity_type/jurisdiction 不一致"
                    )
                merged_ids = {**_parse_json(existing["identifiers_json"], {}), **ids}
                merged_meta = {**_parse_json(existing["metadata_json"], {}), **meta}
                changed = (
                    existing["canonical_name"] != name
                    or merged_ids != _parse_json(existing["identifiers_json"], {})
                    or merged_meta != _parse_json(existing["metadata_json"], {})
                )
                if changed:
                    if existing["canonical_name"] != name:
                        self._insert_alias(
                            conn, entity_id=chosen_id, alias=existing["canonical_name"],
                            alias_type="former_canonical_name", source_ref="entity.upsert", at=timestamp,
                        )
                    conn.execute(
                        """UPDATE entities SET canonical_name=?,canonical_name_norm=?,
                           identifiers_json=?,metadata_json=?,updated_at=? WHERE entity_id=?""",
                        (name, natural["name"], _json_body(merged_ids), _json_body(merged_meta),
                         timestamp, chosen_id),
                    )
                    ids = merged_ids
            for identifier_type, raw_value in ids.items():
                values = (
                    list(raw_value)
                    if isinstance(raw_value, Sequence)
                    and not isinstance(raw_value, (str, bytes, bytearray))
                    else [raw_value]
                )
                for value in values:
                    if value is None or not str(value).strip():
                        continue
                    self._insert_alias(
                        conn, entity_id=chosen_id, alias=str(value),
                        alias_type=f"identifier:{identifier_type}",
                        source_ref=f"entity.identifiers:{identifier_type}", at=timestamp,
                    )
            row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (chosen_id,)).fetchone()
            return self._entity_dict(row)

    def add_alias(
        self, entity_id: str, alias: str, *, alias_type: str = "name",
        source_ref: str = "", at: str | date | datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _utc_timestamp(at)
        with self._write() as conn:
            self._require_entity(conn, entity_id)
            row = self._insert_alias(
                conn, entity_id=entity_id, alias=alias, alias_type=alias_type,
                source_ref=source_ref, at=timestamp,
            )
            return dict(row)

    def propose_mapping(
        self, *, source_entity_id: str, target_entity_id: str,
        target_dataset: str, target_key: str, relationship: str,
        evidence: Iterable[str | Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
        confidence: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        at: str | date | datetime | None = None,
    ) -> dict[str, Any]:
        """Append an immutable proposal version; approval never carries forward."""
        key, key_norm = _target_key(target_dataset, target_key)
        relation = _normal_text(relationship, field="relationship", limit=200)
        evidence_rows = _normal_evidence(evidence)
        context_row = _normal_mapping(context, "context")
        metadata_row = _normal_mapping(metadata, "metadata")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise TypeError("confidence 必须是 0-1 数字")
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence 必须在 0-1 之间")
        timestamp = _utc_timestamp(at)
        edge_identity = {
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "target_dataset": target_dataset,
            "target_key_norm": key_norm,
            "relationship": _normal_key(relation),
        }
        edge_id = _stable_id("edge", edge_identity)
        payload = {
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "target_dataset": target_dataset,
            "target_key": key,
            "relationship": relation,
            "evidence": evidence_rows,
            "context": context_row,
            "confidence": confidence,
            "metadata": metadata_row,
        }
        version_digest = _digest(payload)
        with self._write() as conn:
            self._require_entity(conn, source_entity_id)
            self._require_entity(conn, target_entity_id)
            edge = conn.execute(
                "SELECT * FROM mapping_edges WHERE edge_id=?", (edge_id,),
            ).fetchone()
            if edge is None:
                conn.execute(
                    """INSERT INTO mapping_edges(
                           edge_id,source_entity_id,target_entity_id,target_dataset,target_key,
                           target_key_norm,relationship,status,active,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,'proposed',0,?,?)""",
                    (edge_id, source_entity_id, target_entity_id, target_dataset, key,
                     key_norm, relation, timestamp, timestamp),
                )
                version_number = 1
                supersedes = None
            else:
                current = conn.execute(
                    "SELECT * FROM mapping_versions WHERE version_id=?",
                    (edge["current_version_id"],),
                ).fetchone()
                if current is not None and current["digest"] == version_digest:
                    return self._proposal_result(edge, current)
                version_number = int(conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM mapping_versions WHERE edge_id=?",
                    (edge_id,),
                ).fetchone()[0])
                supersedes = current["version_id"] if current is not None else None
                if current is not None and current["invalidated_at"] is None:
                    conn.execute(
                        "UPDATE mapping_versions SET invalidated_at=? WHERE version_id=?",
                        (timestamp, current["version_id"]),
                    )
            version_id = _stable_id(
                "mapv", {
                    "edge_id": edge_id, "version": version_number,
                    "digest": version_digest,
                },
            )
            conn.execute(
                """INSERT INTO mapping_versions(
                       version_id,edge_id,version,digest,payload_json,evidence_json,
                       context_json,metadata_json,created_at,supersedes_version_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (version_id, edge_id, version_number, version_digest, _json_body(payload),
                 _json_body(evidence_rows), _json_body(context_row), _json_body(metadata_row),
                 timestamp, supersedes),
            )
            conn.execute(
                """UPDATE mapping_edges SET target_key=?,relationship=?,current_version_id=?,
                   status='proposed',active=0,updated_at=?,invalidated_at=NULL WHERE edge_id=?""",
                (key, relation, version_id, timestamp, edge_id),
            )
            edge = conn.execute(
                "SELECT * FROM mapping_edges WHERE edge_id=?", (edge_id,),
            ).fetchone()
            version = conn.execute(
                "SELECT * FROM mapping_versions WHERE version_id=?", (version_id,),
            ).fetchone()
            return self._proposal_result(edge, version)

    @staticmethod
    def _proposal_result(edge: sqlite3.Row, version: sqlite3.Row) -> dict[str, Any]:
        return {
            "edge_id": edge["edge_id"],
            "status": edge["status"],
            "active": bool(edge["active"]),
            "invalidated_at": edge["invalidated_at"],
            "version_id": version["version_id"],
            "version": version["version"],
            "version_digest": version["digest"],
            "supersedes_version_id": version["supersedes_version_id"],
            "version_invalidated_at": version["invalidated_at"],
        }

    def decide_mapping(
        self, edge_id: str, *, decision: str, decided_by: str,
        reason: str = "", version_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        at: str | date | datetime | None = None,
    ) -> dict[str, Any]:
        """Record an append-only review decision for the current version."""
        action = str(decision or "").strip().lower()
        if action not in _DECISIONS:
            raise ValueError(f"decision 必须是 {', '.join(sorted(_DECISIONS))} 之一")
        reviewer = _normal_text(decided_by, field="decided_by", limit=200)
        explanation = " ".join(str(reason or "").split())[:2000]
        if action in {"reject", "invalidate"} and not explanation:
            raise ValueError("reject/invalidate 必须提供 reason")
        meta = _normal_mapping(metadata, "metadata")
        timestamp = _utc_timestamp(at)
        with self._write() as conn:
            edge = conn.execute(
                "SELECT * FROM mapping_edges WHERE edge_id=?", (edge_id,),
            ).fetchone()
            if edge is None:
                raise MappingNotFoundError(f"mapping edge 不存在: {edge_id}")
            chosen_version = version_id or edge["current_version_id"]
            if chosen_version != edge["current_version_id"]:
                raise MappingConflictError("只能裁决当前 mapping version")
            version = conn.execute(
                "SELECT * FROM mapping_versions WHERE version_id=? AND edge_id=?",
                (chosen_version, edge_id),
            ).fetchone()
            if version is None:
                raise MappingNotFoundError(f"mapping version 不存在: {chosen_version}")
            identity = {
                "edge_id": edge_id,
                "version_id": chosen_version,
                "decision": action,
                "decided_by": reviewer,
                "reason": explanation,
                "metadata": meta,
            }
            decision_id = _stable_id("decision", identity)
            existing = conn.execute(
                "SELECT * FROM mapping_decisions WHERE decision_id=?", (decision_id,),
            ).fetchone()
            if existing is not None:
                return self._decision_result(existing, edge)
            if version["invalidated_at"] is not None:
                raise MappingConflictError("已失效的 mapping version 不能再次裁决")
            conn.execute(
                """INSERT INTO mapping_decisions(
                       decision_id,edge_id,version_id,decision,decided_by,reason,
                       metadata_json,decided_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (decision_id, edge_id, chosen_version, action, reviewer, explanation,
                 _json_body(meta), timestamp),
            )
            if action == "approve":
                status, active, invalidated = "approved", 1, None
            elif action == "reject":
                status, active, invalidated = "rejected", 0, None
            else:
                status, active, invalidated = "invalidated", 0, timestamp
                conn.execute(
                    "UPDATE mapping_versions SET invalidated_at=? WHERE version_id=?",
                    (timestamp, chosen_version),
                )
            conn.execute(
                """UPDATE mapping_edges SET status=?,active=?,updated_at=?,invalidated_at=?
                   WHERE edge_id=?""",
                (status, active, timestamp, invalidated, edge_id),
            )
            row = conn.execute(
                "SELECT * FROM mapping_decisions WHERE decision_id=?", (decision_id,),
            ).fetchone()
            edge = conn.execute(
                "SELECT * FROM mapping_edges WHERE edge_id=?", (edge_id,),
            ).fetchone()
            return self._decision_result(row, edge)

    @staticmethod
    def _decision_result(decision: sqlite3.Row, edge: sqlite3.Row) -> dict[str, Any]:
        return {
            "decision_id": decision["decision_id"],
            "edge_id": decision["edge_id"],
            "version_id": decision["version_id"],
            "decision": decision["decision"],
            "decided_by": decision["decided_by"],
            "reason": decision["reason"],
            "decided_at": decision["decided_at"],
            "status": edge["status"],
            "active": bool(edge["active"]),
            "invalidated_at": edge["invalidated_at"],
        }

    def _resolve_subject(
        self, conn: sqlite3.Connection, subject_code: str, as_of: str,
    ) -> _ResolvedSubject:
        query = _normal_text(subject_code, field="subject_code", limit=500)
        exact = conn.execute(
            "SELECT * FROM entities WHERE entity_id=? AND created_at<=?",
            (query, as_of),
        ).fetchone()
        if exact is not None:
            return _ResolvedSubject(query, self._entity_dict(exact))
        norm = _normal_key(query)
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               LEFT JOIN aliases a ON a.entity_id=e.entity_id
               WHERE e.created_at<=? AND (
                   e.canonical_name_norm=? OR (
                       a.alias_norm=? AND a.created_at<=?
                       AND (a.invalidated_at IS NULL OR a.invalidated_at>?)
                   )
               ) ORDER BY e.entity_id""",
            (as_of, norm, norm, as_of, as_of),
        ).fetchall()
        if len(rows) > 1:
            raise AmbiguousEntityError(
                f"subject_code/alias {query!r} 命中多个实体: "
                + ", ".join(row["entity_id"] for row in rows)
            )
        return _ResolvedSubject(query, self._entity_dict(rows[0]) if rows else None)

    def resolve_approved(
        self, subject_code: str, *, as_of: str | date | datetime | None = None,
        explicit_targets: Mapping[str, Sequence[str] | str] | None = None,
        read_only: bool = True,
    ) -> dict[str, Any]:
        """Resolve versions that were approved and active at ``as_of``."""
        horizon = _utc_timestamp(as_of, date_is_end=True)
        conn = self._connect(read_only=read_only)
        try:
            subject = self._resolve_subject(conn, subject_code, horizon)
            edges: list[dict[str, Any]] = []
            if subject.entity is not None:
                rows = conn.execute(
                    """SELECT e.*,v.version_id,v.version,v.digest AS version_digest,
                              v.payload_json,v.evidence_json,v.context_json,v.metadata_json,
                              v.created_at AS version_created_at,
                              v.supersedes_version_id,v.invalidated_at AS version_invalidated_at,
                              t.entity_type AS target_entity_type,
                              t.canonical_name AS target_canonical_name,
                              t.jurisdiction AS target_jurisdiction,
                              t.identifiers_json AS target_identifiers_json,
                              t.metadata_json AS target_metadata_json,
                              t.created_at AS target_created_at,t.updated_at AS target_updated_at
                       FROM mapping_edges e
                       JOIN mapping_versions v ON v.edge_id=e.edge_id
                       JOIN entities t ON t.entity_id=e.target_entity_id
                       WHERE e.source_entity_id=? AND e.created_at<=?
                         AND v.created_at<=?
                         AND (v.invalidated_at IS NULL OR v.invalidated_at>?)
                       ORDER BY e.edge_id,v.version DESC""",
                    (subject.entity["entity_id"], horizon, horizon, horizon),
                ).fetchall()
                latest_versions: dict[str, sqlite3.Row] = {}
                for row in rows:
                    latest_versions.setdefault(row["edge_id"], row)
                version_ids = [row["version_id"] for row in latest_versions.values()]
                decisions: dict[str, sqlite3.Row] = {}
                if version_ids:
                    placeholders = ",".join("?" for _ in version_ids)
                    decision_rows = conn.execute(
                        f"""SELECT rowid AS decision_rowid,* FROM mapping_decisions
                            WHERE version_id IN ({placeholders}) AND decided_at<=?
                            ORDER BY decided_at,decision_rowid""",
                        (*version_ids, horizon),
                    ).fetchall()
                    for decision in decision_rows:
                        decisions[decision["version_id"]] = decision
                for row in latest_versions.values():
                    decision = decisions.get(row["version_id"])
                    if decision is None or decision["decision"] != "approve":
                        continue
                    payload = _parse_json(row["payload_json"], {})
                    target_entity = {
                        "entity_id": row["target_entity_id"],
                        "entity_type": row["target_entity_type"],
                        "canonical_name": row["target_canonical_name"],
                        "jurisdiction": row["target_jurisdiction"],
                        "identifiers": _parse_json(row["target_identifiers_json"], {}),
                        "metadata": _parse_json(row["target_metadata_json"], {}),
                    }
                    edges.append({
                        "edge_id": row["edge_id"],
                        "version_id": row["version_id"],
                        "version": row["version"],
                        "version_digest": row["version_digest"],
                        "supersedes_version_id": row["supersedes_version_id"],
                        "target_dataset": row["target_dataset"],
                        "target_key": payload.get("target_key") or row["target_key"],
                        "relationship": payload.get("relationship") or row["relationship"],
                        "context": _parse_json(row["context_json"], {}),
                        "confidence": payload.get("confidence"),
                        "evidence": _parse_json(row["evidence_json"], []),
                        "metadata": _parse_json(row["metadata_json"], {}),
                        "target_entity": target_entity,
                        "approved_by": decision["decided_by"],
                        "approved_at": decision["decided_at"],
                        "approval_decision_id": decision["decision_id"],
                        "origin": "registry",
                    })
            edges.sort(key=lambda row: (
                row["target_dataset"], row["target_key"], row["relationship"], row["edge_id"],
            ))
            status = "ok" if edges else "empty"
            return _build_resolution(
                subject_code=subject.query, subject=subject.entity, as_of=horizon,
                registry_status=status, edges=edges,
                explicit_targets=explicit_targets,
            )
        finally:
            conn.close()


def _explicit_rows(
    explicit_targets: Mapping[str, Sequence[str] | str] | None,
) -> dict[str, list[dict[str, Any]]]:
    result = {dataset: [] for dataset in sorted(TARGET_DATASETS)}
    if explicit_targets is None:
        return result
    if not isinstance(explicit_targets, Mapping):
        raise TypeError("explicit_targets 必须是 target_dataset -> keys 对象")
    unknown = set(explicit_targets) - TARGET_DATASETS
    if unknown:
        raise ValueError(f"不支持的 explicit target_dataset: {', '.join(sorted(unknown))}")
    for dataset, raw_values in explicit_targets.items():
        values = [raw_values] if isinstance(raw_values, str) else list(raw_values or ())
        seen: set[str] = set()
        for raw in values:
            key, key_norm = _target_key(dataset, raw)
            if key_norm in seen:
                continue
            seen.add(key_norm)
            result[dataset].append({
                "key": key,
                "edge_id": None,
                "version_id": None,
                "version": None,
                "version_digest": None,
                "relationship": "manual_explicit",
                "context": {"scope": "request_only", "persisted": False},
                "target_entity": None,
                "origin": "explicit",
            })
        result[dataset].sort(key=lambda row: row["key"])
    return result


def _build_resolution(
    *, subject_code: str, subject: dict[str, Any] | None, as_of: str,
    registry_status: str, edges: Sequence[Mapping[str, Any]],
    explicit_targets: Mapping[str, Sequence[str] | str] | None,
    warnings: Sequence[str] = (),
    registry_db_status: str | None = None,
    reviewed_table_status: str | None = None,
    reviewed_table: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    explicit = _explicit_rows(explicit_targets)
    registered: dict[str, list[dict[str, Any]]] = {
        dataset: [] for dataset in sorted(TARGET_DATASETS)
    }
    for edge in edges:
        dataset = str(edge["target_dataset"])
        target_row = {
            "key": edge["target_key"],
            "edge_id": edge["edge_id"],
            "version_id": edge["version_id"],
            "version": edge["version"],
            "version_digest": edge["version_digest"],
            "relationship": edge["relationship"],
            "context": edge.get("context") or {},
            "target_entity": edge.get("target_entity"),
            "origin": str(edge.get("origin") or "registry"),
        }
        if target_row["origin"] == "reviewed_table":
            metadata = edge.get("metadata") if isinstance(edge.get("metadata"), Mapping) else {}
            target_row["review"] = {
                "reviewer": edge.get("approved_by"),
                "reviewed_at": edge.get("approved_at"),
                "decision_id": edge.get("approval_decision_id"),
                "status": edge.get("review_status"),
                "valid_from": edge.get("valid_from"),
                "valid_to": edge.get("valid_to"),
                "table_version": metadata.get("reviewed_table_version"),
                "table_sha256": metadata.get("reviewed_table_sha256"),
                "evidence": list(edge.get("evidence") or []),
            }
        registered[dataset].append(target_row)
    combined: dict[str, list[dict[str, Any]]] = {}
    target_keys: dict[str, list[str]] = {}
    for dataset in sorted(TARGET_DATASETS):
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        # Reviewed global knowledge takes precedence over a duplicate manual key.
        for row in [*registered[dataset], *explicit[dataset]]:
            key_norm = _normal_key(row["key"])
            if key_norm in seen:
                continue
            seen.add(key_norm)
            rows.append(row)
        combined[dataset] = rows
        target_keys[dataset] = [row["key"] for row in rows]
    version_material = [
        {
            "edge_id": edge["edge_id"], "version_id": edge["version_id"],
            "version": edge["version"], "version_digest": edge["version_digest"],
        }
        for edge in edges
    ]
    mapping_version = _digest(version_material) if version_material else None
    digest_material = {
        "subject": subject,
        "edges": list(edges),
        "targets": combined,
        "registry_status": registry_status,
    }
    return {
        "schema_version": RESOLUTION_SCHEMA,
        "registry_status": registry_status,
        "registry_db_status": registry_db_status or registry_status,
        "reviewed_table_status": reviewed_table_status or "not_checked",
        "reviewed_table": dict(reviewed_table or {}),
        "subject": subject or {"query": subject_code, "entity_id": None},
        "subject_query": subject_code,
        "as_of": as_of,
        "mapping_version": mapping_version,
        "digest": _digest(digest_material),
        "targets": combined,
        "target_keys": target_keys,
        "registered_targets": registered,
        "explicit_targets": explicit,
        "edges": list(edges),
        "warnings": list(warnings),
    }


def resolve_approved_mappings(
    subject_code: str, *, path: str | Path | None = None,
    reviewed_path: str | Path | None = None,
    as_of: str | date | datetime | None = None,
    explicit_targets: Mapping[str, Sequence[str] | str] | None = None,
) -> dict[str, Any]:
    """Resolve immutable reviewed rows plus approved SQLite overrides read-only.

    ``explicit_targets`` is only a request-local overlay for low-level callers.
    The company preparation workflow applies its stronger three-state policy
    before calling live adapters: ``None`` uses the approved sources, a
    non-empty value replaces them, and an explicit empty value disables that
    dataset.  Neither the JSON nor SQLite read path creates or modifies a file.
    """
    resolved_path = db_path(path)
    resolved_reviewed_path = reviewed_table_path(reviewed_path)
    horizon = _utc_timestamp(as_of, date_is_end=True)
    query = _normal_text(subject_code, field="subject_code", limit=500)

    try:
        reviewed = _load_reviewed_table(
            resolved_reviewed_path, subject_code=query, as_of=horizon,
        )
    except (MappingRegistryError, ValueError, TypeError) as exc:
        reviewed = {
            "status": "unavailable", "subject": None, "edges": [],
            "warnings": [
                f"reviewed_mapping_table_unavailable:{type(exc).__name__}"
            ],
            "metadata": {
                "path": str(resolved_reviewed_path), "schema_version": None,
                "error_type": type(exc).__name__,
            },
        }

    db_status = "not_configured"
    db_subject: dict[str, Any] | None = None
    db_edges: list[dict[str, Any]] = []
    db_warnings: list[str] = []
    if not resolved_path.is_file():
        db_warnings.append("mapping_registry_not_configured")
    else:
        registry = CompanyMappingRegistry(resolved_path, initialize=False)
        try:
            conn = registry._connect(read_only=True)
            try:
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                schema_row = (
                    conn.execute(
                        "SELECT value FROM mapping_meta WHERE key='schema_version'"
                    ).fetchone()
                    if "mapping_meta" in tables else None
                )
            finally:
                conn.close()
            required = {
                "mapping_meta", "entities", "aliases", "mapping_edges",
                "mapping_versions", "mapping_decisions",
            }
            if not required <= tables:
                db_status = "not_configured"
                db_warnings.append("mapping_registry_schema_missing")
            elif schema_row is None or schema_row["value"] != str(SCHEMA_VERSION):
                db_status = "unavailable"
                db_warnings.append("mapping_registry_schema_unsupported")
            else:
                db_result = registry.resolve_approved(
                    query, as_of=horizon, explicit_targets=None, read_only=True,
                )
                db_status = str(db_result.get("registry_status") or "unavailable")
                subject_row = db_result.get("subject")
                if isinstance(subject_row, Mapping) and subject_row.get("entity_id"):
                    db_subject = dict(subject_row)
                db_edges = [
                    dict(row) for row in (db_result.get("edges") or [])
                    if isinstance(row, Mapping)
                ]
                db_warnings.extend(
                    str(row) for row in (db_result.get("warnings") or [])
                )
        except (sqlite3.DatabaseError, MappingRegistryError) as exc:
            db_status = "unavailable"
            db_warnings.append(
                f"mapping_registry_unavailable:{type(exc).__name__}"
            )

    # The immutable table is the baseline.  A separately approved SQLite row
    # with the same downstream key is the administrative override.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in reviewed.get("edges") or []:
        if isinstance(edge, Mapping):
            merged[(
                str(edge.get("target_dataset") or ""),
                _normal_key(edge.get("target_key")),
            )] = dict(edge)
    for edge in db_edges:
        merged[(
            str(edge.get("target_dataset") or ""),
            _normal_key(edge.get("target_key")),
        )] = edge
    edges = sorted(
        merged.values(),
        key=lambda row: (
            row["target_dataset"], row["target_key"], row["relationship"],
            row["edge_id"],
        ),
    )
    reviewed_status = str(reviewed.get("status") or "unavailable")
    if edges:
        combined_status = "ok"
    elif "unavailable" in {db_status, reviewed_status}:
        combined_status = "unavailable"
    elif db_status == "not_configured" and reviewed_status in {
        "not_configured", "unmapped",
    }:
        combined_status = "not_configured"
    else:
        combined_status = "empty"
    subject = db_subject
    if subject is None and isinstance(reviewed.get("subject"), Mapping):
        subject = dict(reviewed["subject"])
    warnings = list(dict.fromkeys([
        *(str(item) for item in (reviewed.get("warnings") or [])),
        *db_warnings,
    ]))
    return _build_resolution(
        subject_code=query, subject=subject, as_of=horizon,
        registry_status=combined_status, edges=edges,
        explicit_targets=explicit_targets, warnings=warnings,
        registry_db_status=db_status, reviewed_table_status=reviewed_status,
        reviewed_table=(
            reviewed.get("metadata")
            if isinstance(reviewed.get("metadata"), Mapping) else {}
        ),
    )


__all__ = [
    "AmbiguousEntityError",
    "CompanyMappingRegistry",
    "ENV_DB_PATH",
    "ENV_REVIEWED_TABLE_PATH",
    "MappingConflictError",
    "MappingNotFoundError",
    "MappingRegistryError",
    "RESOLUTION_SCHEMA",
    "REVIEWED_TABLE_SCHEMA",
    "SCHEMA_VERSION",
    "TARGET_DATASETS",
    "db_path",
    "reviewed_table_path",
    "resolve_approved_mappings",
]
