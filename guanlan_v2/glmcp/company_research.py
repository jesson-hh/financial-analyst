"""Codex-only declaration and receipts for the workspace company workflow.

This tool is intentionally absent from the public ``/gl-mcp`` inventory.  The
scoped server may advertise it only when a short-lived Codex scope carries a
server-owned launch callback.  Its public arguments contain no workspace id,
security code, prepared asset id, report path, or background job id.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Mapping


CODEX_COMPANY_RESEARCH_TOOL = "ww_workspace_company_research_run"
CODEX_COMPANY_RESEARCH_RECIPE = "company_research_v1"
CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA = "codex-company-research-receipt@1"
_JOB_ID_RE = re.compile(r"^bg_[0-9a-f]{10}$")
_ASSET_ID_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def codex_company_research_declaration() -> dict[str, Any]:
    """Return a fresh, closed declaration for ``/gl-mcp-codex`` only."""
    return {
        "name": CODEX_COMPANY_RESEARCH_TOOL,
        "description": (
            "对当前服务端绑定的 A 股研究空间发起 company_research_v1。"
            "本工具不接受 workspace_id 或股票代码；每次调用都先等待帷幄人工确认，"
            "确认后由 Console 后台跑道依次准备证据、生成真实研报、执行风险/报告严格门，"
            "并归档为当前空间的原生 wa_* 资产。返回的是后台受理回执，不是完成报告。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asof": {
                    "type": "string",
                    "format": "date",
                    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
                    "description": "研究截止日；省略时由服务端取香港当天日期",
                },
            },
            "additionalProperties": False,
        },
        "read_only": False,
        "destructive": True,
        "gated": True,
        "engine": False,
        "builtin": "codex_company_research_launch",
        "codex_only": True,
    }


def validate_codex_company_research_arguments(
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the tiny public request again at the execution boundary."""
    raw = dict(arguments or {})
    unexpected = sorted(str(key) for key in raw if key != "asof")
    if unexpected:
        raise ValueError(
            "company_research_v1 不接受这些参数: " + ", ".join(unexpected)
        )
    asof = raw.get("asof")
    if asof is None or asof == "":
        return {"asof": None}
    if not isinstance(asof, str) or len(asof) != 10:
        raise ValueError("asof 必须是 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(asof)
    except ValueError as exc:
        raise ValueError("asof 必须是有效 YYYY-MM-DD") from exc
    if parsed.isoformat() != asof:
        raise ValueError("asof 必须使用 YYYY-MM-DD")
    if parsed > date.today():
        raise ValueError("asof 不能晚于今天")
    return {"asof": asof}


def company_research_binding_sha256(
    *, sid: str, workspace_id: str, code: str, subject_sha256: str,
    root_sha256: str,
) -> str:
    """Bind a durable receipt to the exact server-side research subject."""
    body = json.dumps(
        {
            "sid": str(sid),
            "workspace_id": str(workspace_id),
            "code": str(code),
            "subject_sha256": str(subject_sha256),
            "root_sha256": str(root_sha256),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def validate_company_research_launch_result(
    result: Any, *, sid: str, workspace_id: str, code: str,
    subject_sha256: str, root_sha256: str,
) -> dict[str, Any]:
    """Close the callback result before returning it to an MCP client."""
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        raise RuntimeError("Console 未返回可核验的 company_research_v1 受理回执")
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise RuntimeError("Console company_research_v1 回执缺失")
    expected_binding = company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace_id,
        code=code,
        subject_sha256=subject_sha256,
        root_sha256=root_sha256,
    )
    expected = {
        "schema_version": CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA,
        "recipe_id": CODEX_COMPANY_RESEARCH_RECIPE,
        "sid": sid,
        "workspace_id": workspace_id,
        "code": code,
        "binding_sha256": expected_binding,
        "status": "queued",
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"Console company_research_v1 回执字段不匹配: {key}")
    job_id = receipt.get("job_id")
    accepted_at = receipt.get("accepted_at")
    asof = receipt.get("asof")
    if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None:
        raise RuntimeError("Console company_research_v1 回执 job_id 非法")
    if not isinstance(accepted_at, str) or not accepted_at or len(accepted_at) > 64:
        raise RuntimeError("Console company_research_v1 回执时间非法")
    try:
        accepted = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("Console company_research_v1 回执时间非法") from exc
    if accepted.tzinfo is None:
        raise RuntimeError("Console company_research_v1 回执时间缺少时区")
    if asof is not None:
        validate_codex_company_research_arguments({"asof": asof})
    allowed_receipt = {
        "schema_version", "recipe_id", "job_id", "status", "sid",
        "workspace_id", "code", "binding_sha256", "asof", "accepted_at",
    }
    if set(receipt) != allowed_receipt:
        raise RuntimeError("Console company_research_v1 回执字段集合非法")
    content = result.get("content")
    if not isinstance(content, str) or not content or len(content) > 2000:
        raise RuntimeError("Console company_research_v1 回执说明非法")
    return {"ok": True, "content": content, "receipt": dict(receipt)}


def find_finalized_company_asset(
    store: Any, *, workspace_id: str, prepared_asset_id: str,
    report_sha256: str, finalizer_effect_key: str = "",
) -> dict[str, Any] | None:
    """Find the native asset proving an already-completed finalizer call.

    The finalizer writes the asset metadata last.  A process that dies after
    that atomic write but before updating the background receipt can therefore
    adopt the exact asset instead of invoking the finalizer a second time.
    """
    if not prepared_asset_id or not report_sha256:
        return None
    if (
        _ASSET_ID_RE.fullmatch(prepared_asset_id) is None
        or _SHA256_RE.fullmatch(report_sha256) is None
    ):
        return None
    from guanlan_v2.console.workspace_tools import (
        _list_workspace_assets_complete,
    )

    matches = [
        row for row in _list_workspace_assets_complete(store, workspace_id)
        if row.get("source") == "ww_workspace_company_research_run"
        and row.get("prepared_asset_id") == prepared_asset_id
        and row.get("report_sha256") == report_sha256
        and (
            not finalizer_effect_key
            or row.get("finalizer_effect_key") == finalizer_effect_key
        )
        and isinstance(row.get("id"), str)
        and _ASSET_ID_RE.fullmatch(str(row.get("id"))) is not None
    ]
    if len(matches) > 1:
        raise RuntimeError(
            "同一准备包与报告哈希出现多个最终资产；拒绝选择并停止再次 finalizer"
        )
    if not matches:
        return None
    return validate_finalized_company_asset(
        matches[0],
        workspace_id=workspace_id,
        prepared_asset_id=prepared_asset_id,
        report_sha256=report_sha256,
        finalizer_effect_key=finalizer_effect_key,
    )


def validate_finalized_company_asset(
    asset: Any, *, workspace_id: str, prepared_asset_id: str,
    report_sha256: str, finalizer_effect_key: str = "",
) -> dict[str, Any]:
    """Prove that a finalizer result is the expected native workspace asset."""
    if not isinstance(asset, Mapping):
        raise RuntimeError("company_research_v1 finalizer 未返回资产")
    expected = {
        "workspace_id": workspace_id,
        "source": "ww_workspace_company_research_run",
        "prepared_asset_id": prepared_asset_id,
        "report_sha256": report_sha256,
    }
    for key, value in expected.items():
        if asset.get(key) != value:
            raise RuntimeError(
                f"company_research_v1 最终资产字段不匹配: {key}"
            )
    if finalizer_effect_key and asset.get("finalizer_effect_key") != finalizer_effect_key:
        raise RuntimeError(
            "company_research_v1 最终资产 finalizer_effect_key 不匹配"
        )
    asset_id = asset.get("id")
    if not isinstance(asset_id, str) or _ASSET_ID_RE.fullmatch(asset_id) is None:
        raise RuntimeError("company_research_v1 最终资产 id 非法")
    run_id = asset.get("run_id")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 160:
        raise RuntimeError("company_research_v1 最终资产 run_id 非法")
    if not isinstance(asset.get("attachments"), Mapping):
        raise RuntimeError("company_research_v1 最终资产附件索引缺失")
    return dict(asset)


def artifact_for_finalized_company_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the same closed Console artifact shape for an adopted asset."""
    attachments = asset.get("attachments")
    documents = (
        list(attachments.get("documents") or [])
        if isinstance(attachments, Mapping) else []
    )
    return {
        "kind": "analysis_bundle",
        "page": None,
        "channel": "conversation",
        "payload": {
            "run_id": asset.get("run_id"),
            "asset_id": asset.get("id"),
            "title": asset.get("name"),
            "images": [],
            "documents": documents,
        },
        "ref": asset.get("run_id"),
    }


__all__ = [
    "CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA",
    "CODEX_COMPANY_RESEARCH_RECIPE",
    "CODEX_COMPANY_RESEARCH_TOOL",
    "artifact_for_finalized_company_asset",
    "codex_company_research_declaration",
    "company_research_binding_sha256",
    "find_finalized_company_asset",
    "validate_finalized_company_asset",
    "validate_company_research_launch_result",
    "validate_codex_company_research_arguments",
]
