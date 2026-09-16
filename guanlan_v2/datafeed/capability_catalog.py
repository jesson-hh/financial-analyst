# -*- coding: utf-8 -*-
"""Runtime discovery for the external ``stocks`` data capability catalog.

The stocks repository already exposes two read-only catalogs through
``probe_live_sources.py``: a source catalog and a dataset-contract catalog.  This
module turns those volatile, fairly large payloads into one small, stable
manifest suitable for agent discovery and orchestration preflight.

Two identities are deliberately separate:

* ``catalog_digest`` covers only capability semantics (dataset/source ids,
  query contract, routing and published schema identity).  Health churn never
  changes it.
* ``operational_digest`` covers configured/reachable/availability state.  It is
  observable, but never grants a capability.

The manifest is observation only.  In particular it contains no Worker id,
``CapabilityRef``, handler ref or permission and therefore cannot widen an
orchestration allowlist by itself.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

_SCHEMA_VERSION = "guanlan.data-capability-manifest.v1"
_DEFAULT_TTL_S = float(os.environ.get("GUANLAN_DATA_CATALOG_TTL_S", "60"))
# Catalog identities are canonical lowercase machine ids.  Keeping this equal
# to ConsoleStore's acknowledgement grammar prevents a manifest that can be
# rendered but can never be durably acknowledged.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_READER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")
_SAFE_REFRESH_ERROR_CODES = frozenset({
    "catalog_timeout",
    "catalog_io_failed",
    "catalog_validation_failed",
    "catalog_refresh_failed",
})
_BASE_CONSOLE_FIELDS = frozenset({"code", "start", "end", "as_of", "limit"})
_CACHE_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)
_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "revision_hint": "",
    "snapshot": None,
}


class DataCapabilityCatalogError(ValueError):
    """The observed external catalog is malformed or incomplete."""


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_id(value: Any, *, field: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not _ID_RE.fullmatch(text):
        raise DataCapabilityCatalogError(f"invalid {field}: {text!r}")
    return text


def _safe_id_list(value: Any, *, field: str, ordered: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise DataCapabilityCatalogError(f"{field} must be an array")
    items = tuple(_safe_id(item, field=field) for item in value)
    if len(set(items)) != len(items):
        raise DataCapabilityCatalogError(f"{field} must be duplicate-free")
    return items if ordered else tuple(sorted(items))


def _revision_paths() -> tuple[Path, ...]:
    """Files whose generation controls the two stocks runtime registries.

    A cheap stat fingerprint lets a long-lived Guanlan process notice a local
    catalog edit immediately instead of waiting for the ordinary TTL.  The
    actual catalog payload remains authoritative; this hint grants nothing.
    """
    from guanlan_v2.datafeed import live_client

    probe = Path(live_client._STOCKS_PROBE)  # the facade's configured authority
    root = probe.parents[1] if len(probe.parents) > 1 else probe.parent
    return (
        probe,
        root / "src" / "data" / "source_contracts.py",
        root / "src" / "data" / "live_sources.py",
    )


def catalog_revision_hint() -> str:
    rows: list[dict[str, Any]] = []
    for path in _revision_paths():
        try:
            stat = path.stat()
            rows.append({"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        except OSError:
            rows.append({"name": path.name, "missing": True})
    return _canonical_digest(rows)


def _schema_identity(row: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return a published result-schema status and digest, if the provider has one.

    The current stocks dataset catalog does not yet publish per-dataset result
    schemas.  Its source-level ``schema_version`` identifies the common probe
    envelope, not row-field semantics, so it must not be promoted into a false
    compatibility proof.
    """
    raw_digest = str(row.get("result_schema_digest") or "").strip().lower()
    raw_version = str(row.get("result_schema_version") or "").strip()
    if not raw_digest and not raw_version:
        return "unpublished", None
    if not _DIGEST_RE.fullmatch(raw_digest) or not raw_version:
        return "invalid", None
    return "published", raw_digest


def _console_query_support(dataset_id: str, query_fields: tuple[str, ...]) -> tuple[bool, dict[str, str], tuple[str, ...]]:
    fields = set(query_fields)
    aliases: dict[str, str] = {}
    if fields <= _BASE_CONSOLE_FIELDS:
        return True, aliases, ()
    if dataset_id == "market.bar" and fields <= (_BASE_CONSOLE_FIELDS | {"freq"}):
        aliases["freq"] = "date"
        return True, aliases, ()
    if dataset_id in {"search.iwencai", "search.iwencai_structured"}:
        aliases["query"] = "query"
        unsupported = tuple(sorted(fields - _BASE_CONSOLE_FIELDS - {"query"}))
        return not unsupported, aliases, unsupported
    unsupported = tuple(sorted(fields - _BASE_CONSOLE_FIELDS - set(aliases)))
    return False, aliases, unsupported


def _dataset_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    dataset_id = _safe_id(row.get("dataset_id"), field="dataset_id")
    category = _safe_id(row.get("category") or "uncategorized", field="category")
    source_ids = _safe_id_list(row.get("live_source_ids") or (), field="live_source_ids")
    source_priority = _safe_id_list(row.get("source_priority") or (), field="source_priority")
    # ``stocks`` publishes provider preference names here (for example
    # ``eastmoney``/``tdx_f10``), not source ids.  Preserve the reviewed order
    # as route semantics, but do not pretend the two namespaces are identical.
    query_fields = _safe_id_list(row.get("query_fields") or (), field="query_fields")
    natural_keys = _safe_id_list(row.get("natural_keys") or (), field="natural_keys")
    bridge_status = _safe_id(
        row.get("bridge_status") or "unknown", field="bridge_status"
    )
    date_field = _safe_id(row.get("date_field"), field="date_field", allow_empty=True)
    visible_field = _safe_id(
        row.get("visible_field") or "visible_ts", field="visible_field"
    )
    visible_policy = _safe_id(
        row.get("visible_ts_policy") or "unknown", field="visible_ts_policy"
    )
    schema_status, result_schema_digest = _schema_identity(row)
    query_supported, aliases, unsupported_fields = _console_query_support(
        dataset_id, query_fields
    )
    write_enabled = row.get("write_enabled")
    canonical_write = row.get("canonical_write")
    if not isinstance(write_enabled, bool) or not isinstance(canonical_write, bool):
        raise DataCapabilityCatalogError(
            "dataset write_enabled and canonical_write must both be booleans"
        )
    canonical_reader = str(row.get("canonical_reader") or "").strip()
    if canonical_reader and not _READER_RE.fullmatch(canonical_reader):
        raise DataCapabilityCatalogError(
            f"invalid canonical_reader identity for {dataset_id!r}"
        )
    canonical_reader_digest = (
        _canonical_digest({"canonical_reader": canonical_reader})
        if canonical_reader else None
    )
    modes: list[str] = []
    if source_ids:
        modes.append("live")
    if canonical_reader:
        modes.append("canonical")
    if len(modes) == 2:
        modes.append("auto")

    contract_identity = {
        "dataset_id": dataset_id,
        "category": category,
        "query_fields": list(query_fields),
        "natural_keys": list(natural_keys),
        "date_field": date_field,
        "visible_field": visible_field,
        "visible_ts_policy": visible_policy,
        "bridge_status": bridge_status,
        "modes": sorted(modes),
        "read_only": not (write_enabled or canonical_write),
        "canonical_reader_digest": canonical_reader_digest,
        "result_schema_status": schema_status,
        "result_schema_digest": result_schema_digest,
    }
    route_identity = {
        "dataset_id": dataset_id,
        "live_source_ids": list(source_ids),
        "source_priority": list(source_priority),
    }
    projection = {
        **contract_identity,
        **route_identity,
        "console_query_supported": query_supported,
        "console_param_aliases": aliases,
        "console_unsupported_fields": list(unsupported_fields),
        "contract_digest": _canonical_digest(contract_identity),
        "route_digest": _canonical_digest(route_identity),
    }
    projection["fingerprint"] = _canonical_digest(projection)
    return projection


def _source_projection(
    source_id: str,
    *,
    dataset_ids: tuple[str, ...],
    nested: Mapping[str, Any] | None,
    catalog_row: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = nested or {}
    catalog_row = catalog_row or {}
    alias_raw = catalog_row.get("alias") or source_id
    provider_raw = catalog_row.get("provider") or nested.get("actual_provider") or "unknown"
    try:
        alias = _safe_id(alias_raw, field="source alias")
    except DataCapabilityCatalogError:
        alias = source_id
    try:
        provider = _safe_id(provider_raw, field="provider")
    except DataCapabilityCatalogError:
        provider = "unknown"
    implementation_status = _safe_id(
        catalog_row.get("implementation_status")
        or ("implemented" if nested.get("implemented", True) else "planned"),
        field="implementation_status",
    )
    stable = {
        "source_id": source_id,
        "dataset_ids": list(dataset_ids),
        "alias": alias,
        "provider": provider,
        "implementation_status": implementation_status,
    }
    stable["fingerprint"] = _canonical_digest(stable)
    operational = {
        "source_id": source_id,
        "configured": bool(nested.get("configured", catalog_row.get("configured", False))),
        "reachable": nested.get("reachable", catalog_row.get("reachable")),
        "availability": str(
            nested.get("availability") or catalog_row.get("availability") or "unknown"
        )[:40],
        "circuit_state": str(
            nested.get("circuit_state") or catalog_row.get("circuit_state") or "unknown"
        )[:40],
    }
    return stable, operational


def build_data_capability_manifest(
    dataset_catalog: Mapping[str, Any],
    source_catalog: Mapping[str, Any] | None = None,
    *,
    revision_hint: str = "",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Validate and canonically project the two external catalog payloads."""
    if not isinstance(dataset_catalog, Mapping) or not dataset_catalog.get("ok", False):
        raise DataCapabilityCatalogError("dataset catalog is unavailable")
    raw_datasets = dataset_catalog.get("items")
    if not isinstance(raw_datasets, list) or not raw_datasets:
        raise DataCapabilityCatalogError("dataset catalog has no rows")

    datasets: list[dict[str, Any]] = []
    raw_by_dataset: dict[str, Mapping[str, Any]] = {}
    source_datasets: dict[str, set[str]] = defaultdict(set)
    nested_sources: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for raw in raw_datasets:
        if not isinstance(raw, Mapping):
            raise DataCapabilityCatalogError("dataset catalog contains a non-object row")
        projected = _dataset_projection(raw)
        dataset_id = projected["dataset_id"]
        if dataset_id in raw_by_dataset:
            raise DataCapabilityCatalogError(f"duplicate dataset_id: {dataset_id}")
        raw_by_dataset[dataset_id] = raw
        datasets.append(projected)
        nested_rows = raw.get("sources") or ()
        if not isinstance(nested_rows, (list, tuple)):
            raise DataCapabilityCatalogError(f"dataset {dataset_id!r} sources must be an array")
        for source_id in projected["live_source_ids"]:
            source_datasets[source_id].add(dataset_id)
        for nested in nested_rows:
            if not isinstance(nested, Mapping):
                raise DataCapabilityCatalogError(
                    f"dataset {dataset_id!r} contains a non-object source row"
                )
            source_id = _safe_id(nested.get("source_id"), field="source_id")
            if source_id not in projected["live_source_ids"]:
                raise DataCapabilityCatalogError(
                    f"dataset {dataset_id!r} has undeclared nested source {source_id!r}"
                )
            previous = nested_sources.get(source_id)
            if previous is not None:
                previous_provider = str(previous[1].get("actual_provider") or "").strip()
                current_provider = str(nested.get("actual_provider") or "").strip()
                if previous_provider and current_provider \
                        and previous_provider != current_provider:
                    raise DataCapabilityCatalogError(
                        f"source {source_id!r} has conflicting provider identities"
                    )
            # A source may legitimately implement multiple datasets.  Pick the
            # lexicographically first nested observation so health projection is
            # independent of input row order.
            if previous is None or dataset_id < previous[0]:
                nested_sources[source_id] = (dataset_id, nested)

    source_rows: dict[str, Mapping[str, Any]] = {}
    source_origin = "absent"
    if isinstance(source_catalog, Mapping):
        source_origin = str(source_catalog.get("origin") or "probe")[:24]
        raw_sources = source_catalog.get("sources")
        if isinstance(raw_sources, list):
            for raw in raw_sources:
                if not isinstance(raw, Mapping):
                    raise DataCapabilityCatalogError("source catalog contains a non-object row")
                source_id = _safe_id(raw.get("source_id"), field="source_id")
                if source_id in source_rows:
                    raise DataCapabilityCatalogError(f"duplicate source_id: {source_id}")
                source_rows[source_id] = raw

    all_source_ids = sorted(set(source_datasets) | set(source_rows))
    sources: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for source_id in all_source_ids:
        stable, operational = _source_projection(
            source_id,
            dataset_ids=tuple(sorted(source_datasets.get(source_id) or ())),
            nested=(nested_sources[source_id][1] if source_id in nested_sources else None),
            catalog_row=source_rows.get(source_id),
        )
        sources.append(stable)
        operations.append(operational)

    datasets.sort(key=lambda row: row["dataset_id"])
    sources.sort(key=lambda row: row["source_id"])
    operations.sort(key=lambda row: row["source_id"])
    categories: dict[str, list[str]] = defaultdict(list)
    for row in datasets:
        categories[row["category"]].append(row["dataset_id"])
    semantic = {
        "schema_version": _SCHEMA_VERSION,
        "datasets": datasets,
        "sources": sources,
    }
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise DataCapabilityCatalogError("generated_at must be timezone-aware")
    return {
        "ok": True,
        "authoritative": True,
        "stale": False,
        "schema_version": _SCHEMA_VERSION,
        "catalog_digest": _canonical_digest(semantic),
        "operational_digest": _canonical_digest(operations),
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "revision_hint": revision_hint,
        "source_origin": source_origin,
        "dataset_count": len(datasets),
        "source_count": len(sources),
        "categories": {key: categories[key] for key in sorted(categories)},
        "datasets": datasets,
        "sources": sources,
        "operations": operations,
        "result_schema_complete": all(
            row["result_schema_status"] == "published" for row in datasets
        ),
        "refresh_error": "",
    }


def invalidate_data_capability_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update(ts=0.0, revision_hint="", snapshot=None)


def _refresh_error_code(error: Exception) -> str:
    """Return an allowlisted agent-safe code; raw external text stays in logs."""
    if isinstance(error, TimeoutError):
        return "catalog_timeout"
    if isinstance(error, OSError):
        return "catalog_io_failed"
    if isinstance(error, DataCapabilityCatalogError):
        return "catalog_validation_failed"
    return "catalog_refresh_failed"


def _safe_snapshot_error_code(snapshot: Mapping[str, Any]) -> str:
    code = str(snapshot.get("refresh_error") or "")
    return code if code in _SAFE_REFRESH_ERROR_CODES else "catalog_unavailable"


def _degraded(last_good: Mapping[str, Any] | None, error: Exception) -> dict[str, Any]:
    note = _refresh_error_code(error)
    if last_good:
        out = copy.deepcopy(dict(last_good))
        out.update(authoritative=False, stale=True, refresh_error=note)
        return out
    return {
        "ok": False,
        "authoritative": False,
        "stale": True,
        "schema_version": _SCHEMA_VERSION,
        "catalog_digest": "",
        "dataset_count": 0,
        "source_count": 0,
        "categories": {},
        "datasets": [],
        "sources": [],
        "operations": [],
        "result_schema_complete": False,
        "refresh_error": note,
    }


def runtime_data_catalog_snapshot(
    *,
    force: bool = False,
    max_age_s: float = _DEFAULT_TTL_S,
    dataset_loader: Callable[[], Mapping[str, Any]] | None = None,
    source_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the current last-good stable manifest with single-flight refresh.

    A registry-file revision asks the live facade to recycle its persistent
    worker before accepting the new manifest.  If a request is in flight the
    facade records a pending reload; the next request restarts safely rather
    than killing an active pipe.
    """
    from guanlan_v2.datafeed import live_client

    hint = catalog_revision_hint()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get("snapshot")
        unchanged_hint = bool(_CACHE.get("revision_hint") == hint)
        if (
            not force
            and cached is not None
            and unchanged_hint
            and now - float(_CACHE.get("ts") or 0.0) < max(0.0, float(max_age_s))
        ):
            return copy.deepcopy(cached)

        source_catalog_generation = live_client.source_catalog_generation()
        if not unchanged_hint:
            live_client.request_worker_catalog_reload(
                "stocks data capability registry revision changed"
            )
        try:
            discovered_source_rows: list[dict[str, Any]] | None = None
            if dataset_loader is None and source_loader is None:
                # One fresh process observes both registries from the same code
                # generation.  Besides being cheaper, this avoids accepting a
                # source table from one edit and a dataset table from the next.
                batch = live_client._run_probe_batch(  # noqa: SLF001 - same facade boundary
                    [
                        {"dataset": "catalog", "limit": 300},
                        {"source": "catalog", "limit": 300},
                    ],
                    timeout=30,
                )
                payloads = batch.get("payloads") if isinstance(batch, Mapping) else None
                if not isinstance(batch, Mapping) or not batch.get("ok") \
                        or not isinstance(payloads, list) or len(payloads) != 2:
                    raise DataCapabilityCatalogError(
                        str(
                            batch.get("note")
                            if isinstance(batch, Mapping)
                            else "paired catalog probe failed"
                        )
                    )
                raw_dataset, raw_source = payloads
                if not isinstance(raw_dataset, Mapping) \
                        or str(raw_dataset.get("dataset_id") or "") != "catalog" \
                        or raw_dataset.get("status") != "ok" \
                        or raw_dataset.get("write_enabled") is not False \
                        or raw_dataset.get("canonical_write") is not False:
                    raise DataCapabilityCatalogError("paired dataset catalog is misaligned")
                if not isinstance(raw_source, Mapping) \
                        or str(raw_source.get("source") or "") != "catalog" \
                        or raw_source.get("write_enabled") is not False \
                        or raw_source.get("default_write") is not False:
                    raise DataCapabilityCatalogError("paired source catalog is misaligned")
                dataset_payload = {"ok": True, "items": raw_dataset.get("items")}
                source_payload = {
                    "ok": True,
                    "sources": raw_source.get("rows"),
                    "origin": "probe",
                }
                discovered_source_rows = list(raw_source.get("rows") or ())
            else:
                load_datasets = dataset_loader or (
                    lambda: live_client.query_dataset("catalog", limit=300, timeout=30)
                )
                load_sources = source_loader or (
                    lambda: live_client.catalog(max_age_s=0.0 if force else max_age_s)
                )
                dataset_payload = load_datasets()
                source_payload = load_sources()
            snapshot = build_data_capability_manifest(
                dataset_payload,
                source_payload,
                revision_hint=hint,
            )
            if discovered_source_rows is not None \
                    and not live_client.accept_source_catalog(
                        discovered_source_rows,
                        expected_generation=source_catalog_generation,
                    ):
                raise DataCapabilityCatalogError(
                    "paired source catalog cannot be installed as the current "
                    "unambiguous runtime generation"
                )
        except Exception as exc:  # noqa: BLE001 - last-good fail closed boundary
            _LOG.warning(
                "runtime data capability catalog refresh failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            live_client.revoke_source_catalog(
                "data capability catalog refresh failed",
                expected_generation=source_catalog_generation,
            )
            return _degraded(cached, exc)

        previous_digest = str((cached or {}).get("catalog_digest") or "")
        if previous_digest and previous_digest != snapshot["catalog_digest"]:
            live_client.request_worker_catalog_reload(
                "stocks data capability manifest digest changed"
            )
        _CACHE.update(ts=now, revision_hint=hint, snapshot=snapshot)
        return copy.deepcopy(snapshot)


def catalog_ack_state(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Small per-session acknowledgement state; returns ``None`` for stale data."""
    digest = str(snapshot.get("catalog_digest") or "")
    if (
        snapshot.get("ok") is not True
        or snapshot.get("authoritative") is not True
        or snapshot.get("stale") is not False
        or not _DIGEST_RE.fullmatch(digest)
    ):
        return None
    return {
        "schema_version": "1",
        "catalog_digest": digest,
        "datasets": {
            str(row["dataset_id"]): str(row["fingerprint"])
            for row in snapshot.get("datasets") or ()
        },
        "sources": {
            str(row["source_id"]): str(row["fingerprint"])
            for row in snapshot.get("sources") or ()
        },
    }


def diff_catalog_ack(
    snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> dict[str, Any]:
    current = catalog_ack_state(snapshot)
    if current is None:
        return {"baseline": previous is None, "changed": False, "datasets": {}, "sources": {}}
    if not previous:
        return {"baseline": True, "changed": False, "datasets": {}, "sources": {}}

    def _one(kind: str) -> dict[str, list[str]]:
        old = previous.get(kind) if isinstance(previous.get(kind), Mapping) else {}
        new = current[kind]
        old_ids, new_ids = set(old), set(new)
        return {
            "added": sorted(new_ids - old_ids),
            "removed": sorted(old_ids - new_ids),
            "changed": sorted(key for key in old_ids & new_ids if old[key] != new[key]),
        }

    datasets = _one("datasets")
    sources = _one("sources")
    changed = current["catalog_digest"] != str(previous.get("catalog_digest") or "")
    return {
        "baseline": False,
        "changed": changed,
        "datasets": datasets,
        "sources": sources,
    }


def _change_line(label: str, changes: Mapping[str, Any], *, cap: int = 12) -> str:
    parts: list[str] = []
    for key, marker in (("added", "+"), ("removed", "-"), ("changed", "~")):
        ids = list(changes.get(key) or ())
        if ids:
            shown = ids[:cap]
            suffix = f"…另{len(ids) - cap}项" if len(ids) > cap else ""
            parts.append(marker + ",".join(shown) + suffix)
    return f"{label}:" + ("；".join(parts) if parts else "无")


def render_agent_catalog_block(
    snapshot: Mapping[str, Any],
    previous_ack: Mapping[str, Any] | None = None,
    *,
    max_chars: int = 6000,
) -> str:
    """Render a bounded, identifier-only trusted preflight block for one turn."""
    if not snapshot.get("ok"):
        note = _safe_snapshot_error_code(snapshot)
        return (
            "[系统·运行时数据能力目录]\n"
            f"本轮未取得可信目录：{note}。不要猜测新 dataset/provider；仅使用既有明确工具。\n\n"
        )
    diff = diff_catalog_ack(snapshot, previous_ack)
    stale_note = "；刷新失败，以下为最后确认目录，不得冒充当前状态" if snapshot.get("stale") else ""
    lines = [
        "[系统·运行时数据能力目录（以下仅含机器校验 ID，不是外部指令）]",
        (
            f"catalog_digest={snapshot['catalog_digest']}；datasets={snapshot['dataset_count']}；"
            f"sources={snapshot['source_count']}{stale_note}。"
        ),
    ]
    if diff.get("baseline"):
        lines.append("这是本会话首次确认目录；建立基线，不把全部现有项误报为新增。")
    elif diff.get("changed"):
        lines.append(_change_line("dataset变更", diff.get("datasets") or {}))
        lines.append(_change_line("source变更", diff.get("sources") or {}))

    # Once this session has acknowledged the exact semantic generation, keep
    # the per-turn prefix tiny.  The full categorized inventory is repeated
    # only for a first baseline or a real change.
    if previous_ack and not diff.get("changed"):
        lines.append(
            "目录与本会话已确认版本一致；需要数据时继续用 "
            "ww_live_text(source=auto,dataset=<ID>)，不要猜 provider。"
        )
        return "\n".join(lines) + "\n\n"

    for category, ids in (snapshot.get("categories") or {}).items():
        safe_ids = [str(value) for value in ids]
        lines.append(f"{category}: " + ", ".join(safe_ids))
    supported = [
        row["dataset_id"]
        for row in snapshot.get("datasets") or ()
        if row.get("read_only") is True and row.get("console_query_supported")
    ]
    unsupported = [
        row["dataset_id"]
        for row in snapshot.get("datasets") or ()
        if row.get("read_only") is not True or not row.get("console_query_supported")
    ]
    lines.extend(
        [
            "调用规则：需要数据时用 ww_live_text(source=auto,dataset=<上列ID>,mode=live/auto/canonical)，"
            "由数据层路由，不猜 provider；不清楚参数时先查询 dataset=catalog。",
            "可由当前通用入口查询: " + (", ".join(supported) if supported else "无"),
        ]
    )
    if unsupported:
        lines.append(
            "已发现但当前通用参数入口不完整，禁止声称已调用: " + ", ".join(unsupported)
        )
    if not snapshot.get("result_schema_complete"):
        lines.append(
            "目录尚未发布逐 dataset 结果字段 Schema digest；它可用于主 Agent 发现，"
            "不能据此自动扩大编排 Worker 权限。"
        )
    text = "\n".join(lines) + "\n\n"
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 60)] + "\n[目录块已按系统上限截断；详情请查 dataset=catalog]\n\n"


def agent_catalog_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compact safe rows for ``ww_live_text(source=auto,dataset=catalog)``."""
    rows: list[dict[str, Any]] = []
    for row in snapshot.get("datasets") or ():
        rows.append(
            {
                key: copy.deepcopy(row.get(key))
                for key in (
                    "dataset_id",
                    "category",
                    "live_source_ids",
                    "source_priority",
                    "query_fields",
                    "modes",
                    "read_only",
                    "bridge_status",
                    "console_query_supported",
                    "console_param_aliases",
                    "console_unsupported_fields",
                    "result_schema_status",
                    "contract_digest",
                    "route_digest",
                )
            }
        )
    return rows


def render_agent_catalog_listing(
    snapshot: Mapping[str, Any], *, max_chars: int = 12_000
) -> str:
    """Compact tool content for the full dataset catalog.

    Exact digests remain in ``agent_catalog_rows`` for programmatic callers;
    repeating two 64-byte digests on every LLM-visible row adds no discovery
    value and used to inflate the 44-row catalog beyond 20KB.
    """
    if not snapshot.get("ok"):
        note = _safe_snapshot_error_code(snapshot)
        return f"运行时数据目录不可用：{note}"
    lines = [
        (
            "运行时数据集目录 · "
            f"catalog={snapshot['catalog_digest']} · datasets={snapshot['dataset_count']} · "
            f"sources={snapshot['source_count']}"
        ),
        "格式: dataset | modes | params | sources | console | result-schema",
    ]
    for row in snapshot.get("datasets") or ():
        aliases = row.get("console_param_aliases") or {}
        params = [
            (
                f"{field}<-{aliases[field]}"
                if aliases.get(field) and aliases[field] != field
                else field
            )
            for field in row["query_fields"]
        ]
        lines.append(
            f"{row['dataset_id']} | {','.join(row['modes']) or '-'} | "
            f"{','.join(params) or '-'} | "
            f"{','.join(row['live_source_ids']) or '-'} | "
            f"{('ready' if row['console_query_supported'] else 'unsupported-params') if row['read_only'] else 'write-capable-refused'} | "
            f"{row['result_schema_status']}"
        )
    if not snapshot.get("result_schema_complete"):
        lines.append(
            "边界:逐 dataset 结果字段 Schema digest 尚未完整发布；可供主 Agent 查询，"
            "不能据此扩大编排 Worker 权限。"
        )
    out: list[str] = []
    used = 0
    for line in lines:
        extra = len(line) + (1 if out else 0)
        if used + extra > max_chars:
            out.append("[目录内容已按系统上限截断]")
            break
        out.append(line)
        used += extra
    return "\n".join(out)


def render_source_catalog_listing(
    snapshot: Mapping[str, Any], *, max_chars: int = 12_000
) -> str:
    """Identifier-only tool content for the direct source catalog."""
    if not snapshot.get("ok"):
        return render_agent_catalog_listing(snapshot, max_chars=max_chars)
    lines = [
        (
            "运行时数据源目录 · "
            f"catalog={snapshot['catalog_digest']} · sources={snapshot['source_count']}"
        ),
        "格式: source | alias | provider | dataset | implementation",
    ]
    for row in snapshot.get("sources") or ():
        lines.append(
            f"{row['source_id']} | {row['alias']} | {row['provider']} | "
            f"{','.join(row['dataset_ids']) or '-'} | {row['implementation_status']}"
        )
    out: list[str] = []
    used = 0
    for line in lines:
        extra = len(line) + (1 if out else 0)
        if used + extra > max_chars:
            out.append("[目录内容已按系统上限截断]")
            break
        out.append(line)
        used += extra
    return "\n".join(out)


__all__ = [
    "DataCapabilityCatalogError",
    "agent_catalog_rows",
    "build_data_capability_manifest",
    "catalog_ack_state",
    "catalog_revision_hint",
    "diff_catalog_ack",
    "invalidate_data_capability_cache",
    "render_agent_catalog_block",
    "render_agent_catalog_listing",
    "render_source_catalog_listing",
    "runtime_data_catalog_snapshot",
]
