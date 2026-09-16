"""Closed, path-free artifact envelope for the Codex MCP bridge.

The ordinary Console tool path carries Python ``side_effect`` objects in the
same process.  Codex goes through MCP/App Server, so that object must cross a
JSON protocol boundary.  This module deliberately does *not* serialize an
entire tool result.  It only accepts the small artifact shapes already
understood by Weiwo and rebuilds them from an allowlist of scalar fields and
opaque server-issued ids.

In particular, local paths, URLs, source blobs and arbitrary nested result
objects are never copied into the envelope.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


ARTIFACT_ENVELOPE_SCHEMA = "guanlan.weiwo-artifact.v1"
MAX_ARTIFACT_ENVELOPE_BYTES = 64 * 1024

_WORKSPACE_ID_RE = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")
_ASSET_ID_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_MEDIA_ID_RE = re.compile(r"^mi_[0-9a-f]{32}$")
_DOCUMENT_ID_RE = re.compile(r"^do_[0-9a-f]{32}$")
_CHANGE_ID_RE = re.compile(r"^wc_[0-9a-f]{16}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SECURITY_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)?[0-9]{6}$")
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_TOOL_NAME_RE = re.compile(r"^[a-z0-9_]{1,100}$")
_WORKFLOW_ID_RE = re.compile(r"^w[0-9a-f]{12}$")
_WORKFLOW_RUN_ID_RE = re.compile(r"^wr_[0-9a-f]{12,40}$")
_WORKFLOW_ARTIFACT_TOOLS = frozenset({
    "ww_workflow_generate", "ww_workflow_save", "ww_workflow_get",
    "ww_workflow_run", "ww_workflow_runs",
})
_LOCATOR_RE = re.compile(
    r"(?i)(?:https?|file)://|(?:^|[\s\"'])(?:[A-Z]:[\\/]|\\\\|"
    r"/(?:etc|home|users?|var|tmp|private|opt|root|mnt)(?:/|\b))|"
    r"(?:^|[\\/])\.\.(?:[\\/]|$)"
)

_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_DOCUMENT_SUFFIXES = {
    "application/pdf": frozenset({".pdf"}),
    "text/markdown": frozenset({".md", ".markdown"}),
    "text/plain": frozenset({".txt"}),
    "text/csv": frozenset({".csv"}),
    "text/tab-separated-values": frozenset({".tsv"}),
    "application/json": frozenset({".json"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        frozenset({".xlsx"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        frozenset({".docx"}),
}


def _text(value: Any, limit: int, *, required: bool = False) -> Optional[str]:
    if not isinstance(value, str):
        return None
    rendered = _CONTROL_RE.sub(" ", value).strip()[:limit]
    if required and not rendered:
        return None
    return rendered


def _path_free_text(value: Any, limit: int, *,
                    required: bool = False) -> Optional[str]:
    rendered = _text(value, limit, required=required)
    if rendered and _LOCATOR_RE.search(rendered):
        return None
    return rendered


def _opaque(value: Any, pattern: re.Pattern[str]) -> Optional[str]:
    rendered = str(value or "").strip()
    return rendered if pattern.fullmatch(rendered) else None


def _workspace(value: Any, expected: str) -> Optional[str]:
    rendered = _opaque(value, _WORKSPACE_ID_RE)
    return rendered if rendered == expected else None


def _positive_int(value: Any, maximum: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= maximum else None


def _nonnegative_int(value: Any, maximum: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _within_envelope_limit(value: Any) -> bool:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return False
    return len(encoded) <= MAX_ARTIFACT_ENVELOPE_BYTES


def _image_payload(value: Any, workspace_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    media_id = _opaque(value.get("media_id"), _MEDIA_ID_RE)
    owner = _workspace(value.get("workspace_id"), workspace_id)
    mime = str(value.get("mime") or "").strip().lower()
    width = _positive_int(value.get("width"), 16_384)
    height = _positive_int(value.get("height"), 16_384)
    name = _path_free_text(value.get("name"), 160, required=True)
    raw_alt = value.get("alt")
    alt = _path_free_text(raw_alt, 300)
    if isinstance(raw_alt, str) and raw_alt.strip() and alt is None:
        return None
    alt = alt or name
    if name and any(char in name for char in ("/", "\\", ":")):
        return None
    if not all((media_id, owner, name, width, height)) or mime not in _IMAGE_MIMES:
        return None
    return {
        "media_id": media_id,
        "name": name,
        "mime": mime,
        "width": width,
        "height": height,
        "alt": alt,
        "workspace_id": owner,
    }


def _document_payload(value: Any, workspace_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    document_id = _opaque(value.get("document_id"), _DOCUMENT_ID_RE)
    owner = _workspace(value.get("workspace_id"), workspace_id)
    mime = str(value.get("mime") or "").strip().lower()
    size = _positive_int(value.get("size"), 10 * 1024 * 1024)
    name = _path_free_text(value.get("name"), 160, required=True)
    if not all((document_id, owner, size, name)) \
            or any(char in name for char in ("/", "\\", ":")):
        return None
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in _DOCUMENT_SUFFIXES.get(mime, frozenset()):
        return None
    return {
        "document_id": document_id,
        "name": name,
        "mime": mime,
        "size": size,
        "title": _path_free_text(value.get("title"), 300) or name,
        "workspace_id": owner,
    }


def _analysis_bundle(value: Dict[str, Any], workspace_id: str) -> Optional[Dict[str, Any]]:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return None
    run_id = _opaque(payload.get("run_id"), _OPAQUE_ID_RE)
    title = _path_free_text(payload.get("title"), 160, required=True)
    if not run_id or not title:
        return None
    raw_images = payload.get("images")
    raw_images = raw_images if isinstance(raw_images, list) else []
    images = []
    for row in raw_images[:12]:
        clean = _image_payload(row, workspace_id)
        if clean is not None:
            images.append(clean)
    if len(images) != len(raw_images[:12]):
        return None
    raw_documents = payload.get("documents")
    raw_documents = raw_documents if isinstance(raw_documents, list) else []
    documents = []
    for row in raw_documents[:12]:
        clean = _document_payload(row, workspace_id)
        if clean is not None:
            documents.append(clean)
    if len(documents) != len(raw_documents[:12]):
        return None
    clean_payload: Dict[str, Any] = {
        "run_id": run_id,
        "title": title,
        "images": images,
        "documents": documents,
    }
    asset_id = _opaque(payload.get("asset_id"), _ASSET_ID_RE)
    if asset_id:
        clean_payload["asset_id"] = asset_id
    for key in ("code_sha256", "input_sha256"):
        digest = _opaque(payload.get(key), _HEX64_RE)
        if digest:
            clean_payload[key] = digest
    return {
        "kind": "analysis_bundle",
        "page": None,
        "channel": "conversation",
        "payload": clean_payload,
        "ref": run_id,
    }


def _workflow_artifact(kind: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sanitize the non-capability workflow cards emitted by allowed tools."""
    clean: Dict[str, Any]
    if kind in {"ic_report", "backtest_report"}:
        expr = _path_free_text(payload.get("expr"), 2_000, required=True)
        name = _path_free_text(payload.get("name"), 160, required=True)
        if not expr or not name:
            return None
        clean = {"expr": expr, "name": name}
    elif kind == "compose_report":
        raw_members = payload.get("members")
        if not isinstance(raw_members, list):
            return None
        members = [
            item for item in (_path_free_text(value, 256, required=True)
                              for value in raw_members[:32])
            if item is not None
        ]
        method = str(payload.get("method") or "")
        if len(members) < 2 or method not in {"equal", "ic", "icir"}:
            return None
        clean = {"members": members, "method": method}
    elif kind == "feature_matrix":
        raw_features = payload.get("features")
        if not isinstance(raw_features, list):
            return None
        features = [
            item for item in (_path_free_text(value, 256, required=True)
                              for value in raw_features[:64])
            if item is not None
        ]
        if not features:
            return None
        clean = {"features": features}
    elif kind == "news_sentiment":
        scope = str(payload.get("scope") or "")
        if scope not in {"market", "stock", "both"}:
            return None
        clean = {"scope": scope}
        code = str(payload.get("code") or "").strip().upper()
        if code and _SECURITY_CODE_RE.fullmatch(code):
            clean["code"] = code
        for key, limit in (("as_of", 80), ("market_read", 1_000), ("model", 80)):
            value = _path_free_text(payload.get(key), limit)
            if value:
                clean[key] = value
        raw_sentiment = payload.get("sentiment")
        sentiment: Dict[str, Any] = {}
        if isinstance(raw_sentiment, dict):
            for raw_code, raw_value in list(raw_sentiment.items())[:20]:
                item_code = str(raw_code or "").strip().upper()
                if not _SECURITY_CODE_RE.fullmatch(item_code) \
                        or not isinstance(raw_value, dict):
                    continue
                tag = str(raw_value.get("tag") or "")
                read = _path_free_text(raw_value.get("read"), 500)
                if tag in {"利好", "中性", "利空"}:
                    sentiment[item_code] = {
                        "tag": tag,
                        **({"read": read} if read else {}),
                    }
        if sentiment:
            clean["sentiment"] = sentiment
    else:
        return None
    return {
        "kind": kind,
        "page": "factor" if kind != "news_sentiment" else None,
        "channel": "workflow" if kind != "news_sentiment" else "console",
        "payload": clean,
        "ref": None,
    }


def _workflow_canvas_artifact(value: Dict[str, Any], workspace_id: str):
    """Closed graph-preview shape; not an arbitrary nested tool-result tunnel.

    The normal save/run authoring checks remain authoritative for execution.
    Here every graph/edge/node field is checked, params are restricted to the
    server catalog, all strings are path-free, and the existing 64KiB limit
    still applies. No source files, transport addresses or local paths pass.
    """
    payload = value.get("payload")
    if value.get("page") != "factor" or value.get("channel") != "guanlan.workflow.import":
        return None
    if not isinstance(payload, dict) or set(payload) - {
        "graph", "name", "workflow_id", "run_id", "workspace_id", "session_id",
    } or not _workspace(payload.get("workspace_id"), workspace_id):
        return None
    name = _path_free_text(payload.get("name"), 160)
    if name is None:
        return None
    ids = {}
    for key, pattern in (("workflow_id", _WORKFLOW_ID_RE), ("run_id", _WORKFLOW_RUN_ID_RE)):
        raw = payload.get(key)
        if raw is not None and not _opaque(raw, pattern):
            return None
        ids[key] = raw
    graph = payload.get("graph")
    if not isinstance(graph, dict) or set(graph) - {"nodes", "edges", "schema_version"}:
        return None
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 60:
        return None
    if not isinstance(edges, list) or len(edges) > 160 or not _within_envelope_limit(graph):
        return None

    def safe_json(item, depth=0):
        if depth > 5:
            return False
        if item is None or isinstance(item, (bool, int, float)):
            return True  # finite numbers checked by _within_envelope_limit
        if isinstance(item, str):
            return len(item) <= 2000 and not _CONTROL_RE.search(item) and not _LOCATOR_RE.search(item)
        if isinstance(item, list):
            return len(item) <= 160 and all(safe_json(x, depth + 1) for x in item)
        if isinstance(item, dict):
            return len(item) <= 128 and all(
                isinstance(k, str) and _OPAQUE_ID_RE.fullmatch(k) and safe_json(v, depth + 1)
                for k, v in item.items())
        return False

    try:
        from guanlan_v2.workflow.authoring import catalog
        node_catalog = catalog()["nodes"]
        node_ids = set()
        for node in nodes:
            if not isinstance(node, dict) or set(node) - {"id", "type", "x", "y", "params"}:
                return None
            nid, kind, params = node.get("id"), node.get("type"), node.get("params")
            if not isinstance(nid, str) or not _OPAQUE_ID_RE.fullmatch(nid) or nid in node_ids:
                return None
            if not isinstance(kind, str) or kind not in node_catalog or not isinstance(params, dict):
                return None
            if set(params) - (set(node_catalog[kind].get("params", [])) | {"context_source_id"}):
                return None
            if not safe_json(params):
                return None
            if any(k in node and (isinstance(node[k], bool) or not isinstance(node[k], (int, float)))
                   for k in ("x", "y")):
                return None
            node_ids.add(nid)
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "to"}:
                return None
            for field in ("from", "to"):
                endpoint = edge[field]
                if not isinstance(endpoint, list) or len(endpoint) != 2 or not all(
                    isinstance(x, str) and _OPAQUE_ID_RE.fullmatch(x) for x in endpoint
                ) or endpoint[0] not in node_ids:
                    return None
        if "schema_version" in graph and (isinstance(graph["schema_version"], bool)
                or not isinstance(graph["schema_version"], int)):
            return None
    except (ImportError, TypeError, ValueError, KeyError):
        return None
    # Copy only the verified graph and ids. session_id is intentionally not
    # transported: the receiving Console already owns the session binding.
    clean = {"graph": json.loads(json.dumps(graph, allow_nan=False)), "name": name,
             "workspace_id": workspace_id, **ids}
    return {"kind": "workflow", "page": "factor", "channel": "guanlan.workflow.import",
            "payload": clean, "ref": ids["run_id"] or ids["workflow_id"]}


def sanitize_artifact(value: Any, workspace_id: str) -> Optional[Dict[str, Any]]:
    """Return a path-free artifact or ``None`` for an unknown/invalid shape."""
    if not isinstance(value, dict) or not _WORKSPACE_ID_RE.fullmatch(workspace_id):
        return None
    kind = str(value.get("kind") or "")
    payload = value.get("payload")
    if kind == "analysis_bundle":
        return _analysis_bundle(value, workspace_id)
    if kind == "workflow":
        return _workflow_canvas_artifact(value, workspace_id)
    if not isinstance(payload, dict):
        return None
    if kind in {
        "ic_report", "backtest_report", "compose_report", "feature_matrix",
        "news_sentiment",
    }:
        return _workflow_artifact(kind, payload)

    if kind == "media_image":
        clean = _image_payload(payload, workspace_id)
        return ({"kind": kind, "page": None, "channel": "conversation",
                 "payload": clean, "ref": clean["media_id"]}
                if clean is not None else None)
    if kind == "file_document":
        clean = _document_payload(payload, workspace_id)
        return ({"kind": kind, "page": None, "channel": "conversation",
                 "payload": clean, "ref": clean["document_id"]}
                if clean is not None else None)
    if kind == "workspace_data":
        owner = _workspace(payload.get("workspace_id"), workspace_id)
        asset_id = _opaque(payload.get("asset_id"), _ASSET_ID_RE)
        dataset_id = _opaque(payload.get("dataset_id"), _DATASET_ID_RE)
        count = _nonnegative_int(payload.get("record_count"), 100_000_000)
        pulled_at = _text(payload.get("pulled_at"), 80)
        if not owner or not asset_id or not dataset_id or count is None:
            return None
        return {
            "kind": kind,
            "page": None,
            "channel": "workspace",
            "payload": {
                "workspace_id": owner,
                "asset_id": asset_id,
                "dataset_id": dataset_id,
                "record_count": count,
                "pulled_at": pulled_at or "",
            },
            "ref": asset_id,
        }
    if kind == "workspace_factor":
        owner = _workspace(payload.get("workspace_id"), workspace_id)
        asset_id = _opaque(payload.get("asset_id"), _ASSET_ID_RE)
        name = _path_free_text(payload.get("name"), 80, required=True)
        version = _positive_int(payload.get("version"), 1_000_000)
        if not owner or not asset_id or not name or version is None \
                or payload.get("status") != "draft":
            return None
        return {
            "kind": kind,
            "page": "factor",
            "channel": "workspace",
            "payload": {
                "workspace_id": owner,
                "asset_id": asset_id,
                "name": name,
                "status": "draft",
                "version": version,
            },
            "ref": asset_id,
        }
    if kind == "workspace_change":
        owner = _workspace(payload.get("workspace_id"), workspace_id)
        change_id = _opaque(payload.get("change_id"), _CHANGE_ID_RE)
        if not owner or not change_id:
            return None
        clean_payload: Dict[str, Any] = {
            "workspace_id": owner,
            "change_id": change_id,
        }
        if isinstance(payload.get("undone"), bool):
            clean_payload["undone"] = payload["undone"]
        digest = _opaque(payload.get("sha256"), _HEX64_RE)
        if digest:
            clean_payload["sha256"] = digest
        return {
            "kind": kind,
            "page": None,
            "channel": "workspace",
            "payload": clean_payload,
            "ref": change_id,
        }
    return None


def build_artifact_envelope(result: Any, *, tool_name: str,
                            workspace_id: str) -> Optional[Dict[str, Any]]:
    """Build the sole structured-content shape emitted by scoped Guanlan MCP."""
    if not isinstance(result, dict):
        return None
    if _TOOL_NAME_RE.fullmatch(str(tool_name or "")) is None:
        return None
    artifact = sanitize_artifact(result.get("artifact"), workspace_id)
    if artifact is None:
        return None
    if artifact.get("kind") == "workflow" and tool_name not in _WORKFLOW_ARTIFACT_TOOLS:
        return None
    envelope = {
        "schema": ARTIFACT_ENVELOPE_SCHEMA,
        "tool": str(tool_name),
        "workspace_id": workspace_id,
        "artifact": artifact,
    }
    return envelope if _within_envelope_limit(envelope) else None


def read_artifact_envelope(value: Any, *, tool_name: str,
                           workspace_id: str) -> Optional[Dict[str, Any]]:
    """Verify App Server structured content before it becomes a Console event."""
    if not isinstance(value, dict):
        return None
    if set(value) != {"schema", "tool", "workspace_id", "artifact"} \
            or not _within_envelope_limit(value):
        return None
    if value.get("schema") != ARTIFACT_ENVELOPE_SCHEMA \
            or value.get("tool") != tool_name \
            or value.get("workspace_id") != workspace_id:
        return None
    artifact = sanitize_artifact(value.get("artifact"), workspace_id)
    if artifact is None:
        return None
    rebuilt = build_artifact_envelope(
        {"artifact": artifact}, tool_name=tool_name,
        workspace_id=workspace_id,
    )
    return rebuilt["artifact"] if rebuilt is not None else None


__all__ = [
    "ARTIFACT_ENVELOPE_SCHEMA",
    "MAX_ARTIFACT_ENVELOPE_BYTES",
    "build_artifact_envelope",
    "read_artifact_envelope",
    "sanitize_artifact",
]
