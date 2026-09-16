"""Private, append-only review ledger for company industry-news candidates.

The raw NewsRadar asset is immutable.  This ledger records a user's relevance
decision beside it without editing provider data or promoting an external
headline to an issuer fact.  Every entry is hash-chained and revisions use an
explicit compare-and-swap value so two tabs cannot silently overwrite each
other.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from guanlan_v2.research.company_industry_news import snapshot_candidate


SCHEMA_VERSION = "workspace-industry-candidate-review-ledger@1"
VIEW_SCHEMA_VERSION = "workspace-industry-candidate-review-state@1"
_HK = ZoneInfo("Asia/Hong_Kong")
_WORKSPACE_RE = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")
_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)[0-9]{6}$")
_ASSET_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_CANDIDATE_RE = re.compile(r"^INC-[0-9A-F]{16}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_DECISIONS = frozenset({"related", "unrelated", "uncertain"})
_ZERO_HEAD = "0" * 64
_MAX_ENTRIES = 10_000
_MAX_LEDGER_BYTES = 8 * 1024 * 1024
_MAX_BATCH_REVIEWS = 100


class IndustryReviewError(ValueError):
    """A fail-closed ledger error safe to show to the caller."""


class IndustryReviewConflict(IndustryReviewError):
    """The caller's expected revision is stale."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IndustryReviewError("行业候选审核数据不是合法 JSON") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _now() -> str:
    return datetime.now(_HK).isoformat(timespec="seconds")


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if any(ord(ch) < 32 for ch in text):
        raise IndustryReviewError("审核文字包含控制字符")
    return text[:limit]


def _ledger_path(root: str | Path, workspace_id: str) -> Path:
    if not _WORKSPACE_RE.fullmatch(str(workspace_id or "")):
        raise IndustryReviewError("workspace_id 格式非法")
    base = Path(root) / "workspace_industry_reviews"
    directory = base / workspace_id
    path = directory / "ledger.json"
    for probe in (base, directory, path):
        if (probe.exists() or probe.is_symlink()) and probe.is_symlink():
            raise IndustryReviewError("行业审核账本目录被链接重定向")
        if os.name == "nt" and (probe.exists() or probe.is_symlink()):
            try:
                if int(probe.lstat().st_file_attributes) & 0x400:  # reparse point
                    raise IndustryReviewError("行业审核账本目录被链接重定向")
            except AttributeError:
                pass
    return path


def _empty_ledger(workspace_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "revision": 0,
        "head_sha256": _ZERO_HEAD,
        "entries": [],
    }


def _validate_entry(
    raw: Any, *, workspace_id: str, expected_revision: int,
    previous_head: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise IndustryReviewError("行业审核账本 entry 不是对象")
    entry = dict(raw)
    if entry.get("schema_version") != "industry-candidate-review-entry@1" \
            or entry.get("workspace_id") != workspace_id \
            or entry.get("revision") != expected_revision \
            or entry.get("base_revision") != expected_revision - 1 \
            or entry.get("previous_entry_sha256") != previous_head \
            or entry.get("decision") not in _DECISIONS \
            or entry.get("research_scope") != "industry_context" \
            or entry.get("issuer_fact") is not False \
            or not _CODE_RE.fullmatch(str(entry.get("subject_code") or "")) \
            or not _ASSET_RE.fullmatch(str(entry.get("snapshot_asset_id") or "")) \
            or not _CANDIDATE_RE.fullmatch(str(entry.get("candidate_id") or "")) \
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("candidate_sha256") or "")) \
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("idempotency_sha256") or "")) \
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("request_sha256") or "")) \
            or not re.fullmatch(r"IRV-[0-9A-F]{16}", str(entry.get("entry_id") or "")):
        raise IndustryReviewError("行业审核账本 entry 结构或边界非法")
    note = entry.get("note")
    reviewer = entry.get("reviewer")
    if not isinstance(note, str) or len(note) > 500 \
            or _clean_text(note, limit=500) != note \
            or not isinstance(reviewer, Mapping) \
            or reviewer.get("kind") != "console_user_confirmed" \
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(reviewer.get("session_sha256") or ""),
            ):
        raise IndustryReviewError("行业审核账本审核人或备注字段非法")
    try:
        reviewed_at = datetime.fromisoformat(str(entry.get("reviewed_at") or ""))
    except ValueError as exc:
        raise IndustryReviewError("行业审核账本 reviewed_at 非法") from exc
    if reviewed_at.tzinfo is None:
        raise IndustryReviewError("行业审核账本 reviewed_at 缺少时区")
    request_identity = {
        "workspace_id": workspace_id,
        "subject_code": entry["subject_code"],
        "snapshot_asset_id": entry["snapshot_asset_id"],
        "candidate_id": entry["candidate_id"],
        "candidate_sha256": entry["candidate_sha256"],
        "decision": entry["decision"],
        "note": note,
        "idempotency_sha256": entry["idempotency_sha256"],
    }
    request_sha = _sha(request_identity)
    if entry["request_sha256"] != request_sha \
            or entry["entry_id"] != "IRV-" + request_sha[:16].upper():
        raise IndustryReviewError("行业审核账本请求身份校验失败")
    claimed = str(entry.pop("entry_sha256", ""))
    actual = _sha(entry)
    entry["entry_sha256"] = claimed
    if not re.fullmatch(r"[0-9a-f]{64}", claimed) or claimed != actual:
        raise IndustryReviewError("行业审核账本 hash chain 校验失败")
    return entry


def load_ledger(root: str | Path, workspace_id: str) -> dict[str, Any]:
    """Read and fully validate the workspace ledger without creating state."""
    path = _ledger_path(root, workspace_id)
    if not path.exists():
        return _empty_ledger(workspace_id)
    if not path.is_file():
        raise IndustryReviewError("行业审核账本不是普通文件")
    try:
        size = path.stat().st_size
        if size <= 0 or size > _MAX_LEDGER_BYTES:
            raise IndustryReviewError("行业审核账本为空或异常过大")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except IndustryReviewError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IndustryReviewError("行业审核账本损坏或不可读") from exc
    if not isinstance(payload, Mapping) \
            or payload.get("schema_version") != SCHEMA_VERSION \
            or payload.get("workspace_id") != workspace_id \
            or isinstance(payload.get("revision"), bool) \
            or not isinstance(payload.get("revision"), int) \
            or payload.get("revision", -1) < 0 \
            or not isinstance(payload.get("entries"), list):
        raise IndustryReviewError("行业审核账本头部非法")
    entries = payload["entries"]
    if len(entries) > _MAX_ENTRIES or payload["revision"] != len(entries):
        raise IndustryReviewError("行业审核账本 revision 与 entries 不一致")
    head = _ZERO_HEAD
    checked: list[dict[str, Any]] = []
    for revision, raw in enumerate(entries, 1):
        entry = _validate_entry(
            raw, workspace_id=workspace_id,
            expected_revision=revision, previous_head=head,
        )
        head = entry["entry_sha256"]
        checked.append(entry)
    if payload.get("head_sha256") != head:
        raise IndustryReviewError("行业审核账本 head_sha256 不一致")
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "revision": len(checked),
        "head_sha256": head,
        "entries": checked,
    }


def review_state(
    root: str | Path, workspace_id: str, subject_code: str, *, as_of: str = "",
) -> dict[str, Any]:
    """Return the latest decision for each candidate identity of one company."""
    code = str(subject_code or "").strip().upper()
    if not _CODE_RE.fullmatch(code):
        raise IndustryReviewError("subject_code 格式非法")
    ledger = load_ledger(root, workspace_id)
    cutoff = str(as_of or "").strip()
    if cutoff:
        try:
            datetime.strptime(cutoff, "%Y-%m-%d")
        except ValueError as exc:
            raise IndustryReviewError("as_of 必须是 YYYY-MM-DD") from exc
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in ledger["entries"]:
        reviewed_day = str(entry.get("reviewed_at") or "")[:10]
        if entry["subject_code"] == code \
                and (not cutoff or (reviewed_day and reviewed_day <= cutoff)):
            latest[(entry["candidate_id"], entry["candidate_sha256"])] = entry
    decisions = [
        {
            key: entry.get(key) for key in (
                "entry_id", "revision", "snapshot_asset_id", "candidate_id",
                "candidate_sha256", "decision", "note", "reviewed_at",
                "research_scope", "issuer_fact",
            )
        }
        for _key, entry in sorted(latest.items())
    ]
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "subject_code": code,
        "as_of": cutoff or None,
        "revision": ledger["revision"],
        "head_sha256": ledger["head_sha256"],
        "decisions": decisions,
    }


def append_review(
    root: str | Path, workspace_id: str, subject_code: str, *,
    snapshot_asset_id: str, snapshot: Mapping[str, Any], candidate_id: str,
    decision: str, expected_revision: int, idempotency_key: str,
    reviewer_session: str, note: str = "",
) -> dict[str, Any]:
    """Append one CAS/idempotent human decision.

    The caller must hold the workspace's ordinary single-writer lock.  This
    function still re-reads and validates the full hash chain immediately
    before the atomic replacement.
    """
    code = str(subject_code or "").strip().upper()
    asset_id = str(snapshot_asset_id or "").strip()
    candidate = str(candidate_id or "").strip().upper()
    choice = str(decision or "").strip().lower()
    idem = str(idempotency_key or "").strip()
    if not _CODE_RE.fullmatch(code):
        raise IndustryReviewError("subject_code 格式非法")
    if not _ASSET_RE.fullmatch(asset_id):
        raise IndustryReviewError("snapshot_asset_id 格式非法")
    if not _CANDIDATE_RE.fullmatch(candidate):
        raise IndustryReviewError("candidate_id 格式非法")
    if choice not in _DECISIONS:
        raise IndustryReviewError("decision 只允许 related/unrelated/uncertain")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
            or expected_revision < 0:
        raise IndustryReviewError("expected_revision 必须是非负整数")
    if not _IDEMPOTENCY_RE.fullmatch(idem):
        raise IndustryReviewError("idempotency_key 必须是 8..128 位安全字符")
    if not str(reviewer_session or "").strip():
        raise IndustryReviewError("行业候选审核缺少当前确认会话")
    resolved = snapshot_candidate(snapshot, candidate)
    relation = resolved["relation"]
    if relation.get("collection") != "rows":
        raise IndustryReviewError("未来时间隔离区候选不能进入当前公司研报审核")
    note_text = _clean_text(note, limit=500)
    idem_sha = hashlib.sha256(idem.encode("utf-8")).hexdigest()
    request_identity = {
        "workspace_id": workspace_id,
        "subject_code": code,
        "snapshot_asset_id": asset_id,
        "candidate_id": candidate,
        "candidate_sha256": resolved["candidate_sha256"],
        "decision": choice,
        "note": note_text,
        "idempotency_sha256": idem_sha,
    }
    request_sha = _sha(request_identity)
    ledger = load_ledger(root, workspace_id)

    for entry in ledger["entries"]:
        if entry["idempotency_sha256"] != idem_sha:
            continue
        if entry["request_sha256"] != request_sha:
            raise IndustryReviewError("idempotency_key 已被另一笔审核请求使用")
        return {
            "created": False,
            "idempotent_replay": True,
            "entry": entry,
            "state": review_state(root, workspace_id, code),
        }
    if ledger["revision"] != expected_revision:
        raise IndustryReviewConflict(
            f"审核账本版本已变化：expected={expected_revision}，"
            f"current={ledger['revision']}；请重新读取候选后再确认"
        )
    if ledger["revision"] >= _MAX_ENTRIES:
        raise IndustryReviewError("行业审核账本已达到容量上限")

    revision = ledger["revision"] + 1
    reviewed_at = _now()
    body = {
        "schema_version": "industry-candidate-review-entry@1",
        "entry_id": "IRV-" + request_sha[:16].upper(),
        "workspace_id": workspace_id,
        "subject_code": code,
        "snapshot_asset_id": asset_id,
        "candidate_id": candidate,
        "candidate_sha256": resolved["candidate_sha256"],
        "decision": choice,
        "note": note_text,
        "reviewed_at": reviewed_at,
        "reviewer": {
            "kind": "console_user_confirmed",
            "session_sha256": hashlib.sha256(
                str(reviewer_session or "").encode("utf-8")
            ).hexdigest(),
        },
        "research_scope": "industry_context",
        "issuer_fact": False,
        "base_revision": ledger["revision"],
        "revision": revision,
        "previous_entry_sha256": ledger["head_sha256"],
        "idempotency_sha256": idem_sha,
        "request_sha256": request_sha,
    }
    body["entry_sha256"] = _sha(body)
    updated = {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "revision": revision,
        "head_sha256": body["entry_sha256"],
        "entries": [*ledger["entries"], body],
    }
    encoded = json.dumps(
        updated, ensure_ascii=False, indent=1, allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_LEDGER_BYTES:
        raise IndustryReviewError("行业审核账本将超过容量上限")
    path = _ledger_path(root, workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after mkdir: an attacker must not win the missing-directory gap.
    path = _ledger_path(root, workspace_id)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_bytes(encoded)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise IndustryReviewError("行业审核账本原子写入失败") from exc
    return {
        "created": True,
        "idempotent_replay": False,
        "entry": body,
        "state": review_state(root, workspace_id, code),
    }


def append_reviews(
    root: str | Path,
    workspace_id: str,
    subject_code: str,
    *,
    snapshot_asset_id: str,
    snapshot: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    expected_revision: int,
    idempotency_key: str,
    reviewer_session: str,
) -> dict[str, Any]:
    """Atomically append a bounded batch of relevance decisions.

    The batch is all-or-nothing: every candidate is resolved and every request
    identity is validated before the ledger file is replaced once.  A retry of
    the same batch key returns the original entries; a partial replay is treated
    as ledger corruption/conflicting use and fails closed.
    """
    code = str(subject_code or "").strip().upper()
    asset_id = str(snapshot_asset_id or "").strip()
    idem = str(idempotency_key or "").strip()
    if not _CODE_RE.fullmatch(code):
        raise IndustryReviewError("subject_code 格式非法")
    if not _ASSET_RE.fullmatch(asset_id):
        raise IndustryReviewError("snapshot_asset_id 格式非法")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
            or expected_revision < 0:
        raise IndustryReviewError("expected_revision 必须是非负整数")
    if not _IDEMPOTENCY_RE.fullmatch(idem):
        raise IndustryReviewError("idempotency_key 必须是 8..128 位安全字符")
    if not str(reviewer_session or "").strip():
        raise IndustryReviewError("行业候选审核缺少当前确认会话")
    if isinstance(reviews, (str, bytes)) or not isinstance(reviews, Sequence) \
            or not 1 <= len(reviews) <= _MAX_BATCH_REVIEWS:
        raise IndustryReviewError(
            f"reviews 必须包含 1..{_MAX_BATCH_REVIEWS} 条审核"
        )

    prepared: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for index, raw in enumerate(reviews):
        if not isinstance(raw, Mapping):
            raise IndustryReviewError("reviews 每项必须是对象")
        candidate = str(raw.get("candidate_id") or "").strip().upper()
        choice = str(raw.get("decision") or "").strip().lower()
        note_text = _clean_text(raw.get("note"), limit=500)
        if not _CANDIDATE_RE.fullmatch(candidate):
            raise IndustryReviewError("candidate_id 格式非法")
        if candidate in seen_candidates:
            raise IndustryReviewError("同一批次不能重复审核同一 candidate_id")
        seen_candidates.add(candidate)
        if choice not in _DECISIONS:
            raise IndustryReviewError("decision 只允许 related/unrelated/uncertain")
        resolved = snapshot_candidate(snapshot, candidate)
        if resolved["relation"].get("collection") != "rows":
            raise IndustryReviewError("未来时间隔离区候选不能进入当前公司研报审核")
        idem_sha = hashlib.sha256(f"{idem}:{index}".encode("utf-8")).hexdigest()
        request_identity = {
            "workspace_id": workspace_id,
            "subject_code": code,
            "snapshot_asset_id": asset_id,
            "candidate_id": candidate,
            "candidate_sha256": resolved["candidate_sha256"],
            "decision": choice,
            "note": note_text,
            "idempotency_sha256": idem_sha,
        }
        prepared.append({
            **request_identity,
            "request_sha256": _sha(request_identity),
        })

    ledger = load_ledger(root, workspace_id)
    existing_by_idem = {
        str(entry.get("idempotency_sha256")): entry for entry in ledger["entries"]
    }
    replayed: list[dict[str, Any]] = []
    for item in prepared:
        existing = existing_by_idem.get(item["idempotency_sha256"])
        if existing is None:
            continue
        if existing.get("request_sha256") != item["request_sha256"]:
            raise IndustryReviewError("idempotency_key 已被另一批审核请求使用")
        replayed.append(existing)
    if replayed:
        if len(replayed) != len(prepared):
            raise IndustryReviewError("批量审核幂等状态不完整，拒绝部分重放")
        return {
            "created": False,
            "idempotent_replay": True,
            "entries": replayed,
            "state": review_state(root, workspace_id, code),
        }
    if ledger["revision"] != expected_revision:
        raise IndustryReviewConflict(
            f"审核账本版本已变化：expected={expected_revision}，"
            f"current={ledger['revision']}；请重新读取候选后再确认"
        )
    if ledger["revision"] + len(prepared) > _MAX_ENTRIES:
        raise IndustryReviewError("行业审核账本将超过容量上限")

    reviewed_at = _now()
    session_sha = hashlib.sha256(str(reviewer_session).encode("utf-8")).hexdigest()
    head = ledger["head_sha256"]
    appended: list[dict[str, Any]] = []
    for offset, item in enumerate(prepared, 1):
        revision = ledger["revision"] + offset
        request_sha = item["request_sha256"]
        body = {
            "schema_version": "industry-candidate-review-entry@1",
            "entry_id": "IRV-" + request_sha[:16].upper(),
            "workspace_id": workspace_id,
            "subject_code": code,
            "snapshot_asset_id": asset_id,
            "candidate_id": item["candidate_id"],
            "candidate_sha256": item["candidate_sha256"],
            "decision": item["decision"],
            "note": item["note"],
            "reviewed_at": reviewed_at,
            "reviewer": {
                "kind": "console_user_confirmed", "session_sha256": session_sha,
            },
            "research_scope": "industry_context",
            "issuer_fact": False,
            "base_revision": revision - 1,
            "revision": revision,
            "previous_entry_sha256": head,
            "idempotency_sha256": item["idempotency_sha256"],
            "request_sha256": request_sha,
        }
        body["entry_sha256"] = _sha(body)
        head = body["entry_sha256"]
        appended.append(body)

    updated = {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "revision": ledger["revision"] + len(appended),
        "head_sha256": head,
        "entries": [*ledger["entries"], *appended],
    }
    encoded = json.dumps(
        updated, ensure_ascii=False, indent=1, allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_LEDGER_BYTES:
        raise IndustryReviewError("行业审核账本将超过容量上限")
    path = _ledger_path(root, workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _ledger_path(root, workspace_id)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_bytes(encoded)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise IndustryReviewError("行业审核账本原子写入失败") from exc
    return {
        "created": True,
        "idempotent_replay": False,
        "entries": appended,
        "state": review_state(root, workspace_id, code),
    }


__all__ = [
    "IndustryReviewConflict",
    "IndustryReviewError",
    "SCHEMA_VERSION",
    "VIEW_SCHEMA_VERSION",
    "append_review",
    "append_reviews",
    "load_ledger",
    "review_state",
]
