"""Read-only, explainable health audit for Weiwo memory.

The Markdown stores managed by :mod:`memory_admin` remain the only memory
content truth.  This module never edits or deletes them.  It derives a small
set of deterministic governance findings, persists only issue workflow state
(open/dismissed/resolved), and records real recall observations so absence of
use is never guessed from a memory's age.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

from guanlan_v2.console.memory_admin import MemoryAdmin, MemoryAdminError
from guanlan_v2.console.store import ConsoleStore


_SCHEMA_VERSION = 1
_INSIGHT_LOCK = threading.RLock()
_VALID_STATUSES = frozenset({"open", "dismissed", "resolved"})
_TEMPORAL_RE = re.compile(
    r"今天|今日|当前|目前|实时|最新|截至|截止|停在|现阶段|"
    r"能力缺口|尚未支持|暂不支持|现在没有|仍然没有|仍未"
)
_DURABLE_FACT_RE = re.compile(r"持仓成本|成本价|买入成本|长期偏好|风险偏好")
_POTENTIAL_STALE_DAYS = 30
_LOW_USE_MIN_DAYS = 30
_LOW_USE_MIN_QUERIES = 20


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _parse_day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normal(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(3):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def _load_sidecar(path: Path, root_key: str) -> Dict[str, Any]:
    for candidate in (path, path.with_suffix(".json.bak")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get(root_key), dict):
            return dict(payload)
    return {"schema_version": _SCHEMA_VERSION, root_key: {}}


def _save_sidecar(path: Path, payload: Mapping[str, Any], root_key: str) -> None:
    try:
        current = path.read_text(encoding="utf-8")
        parsed = json.loads(current)
        if isinstance(parsed, dict) and isinstance(parsed.get(root_key), dict):
            _atomic_write(path.with_suffix(".json.bak"), current)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        pass
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False),
    )


def _fingerprint(issue_type: str, memories: Sequence[Mapping[str, Any]],
                 extra: Any = None) -> tuple[str, str]:
    ids = sorted(str(item.get("id") or "") for item in memories)
    versions = sorted(
        (str(item.get("id") or ""), str(item.get("etag") or ""))
        for item in memories
    )
    material = json.dumps(
        {"type": issue_type, "ids": ids, "versions": versions, "extra": extra},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return "mi_" + digest[:16], digest[:24]


class MemoryInsight:
    """Context-bound audit and recall-usage telemetry.

    All scope authorization is delegated to ``MemoryAdmin.list``; callers can
    never name an unrelated workspace without a session actually bound to it.
    """

    def __init__(self, store: ConsoleStore):
        self.store = store
        self.admin = MemoryAdmin(store)

    @property
    def _state_path(self) -> Path:
        return Path(self.store.root) / "memory.insight.json"

    @property
    def _usage_path(self) -> Path:
        return Path(self.store.root) / "memory.recall.usage.json"

    @staticmethod
    def _new_issue(issue_type: str, severity: str,
                   memories: Sequence[Mapping[str, Any]], *, title: str,
                   description: str, evidence: Sequence[str], impact: str,
                   suggested_actions: Sequence[str], extra: Any = None) -> Dict[str, Any]:
        issue_id, fingerprint = _fingerprint(issue_type, memories, extra)
        memory_ids = sorted(str(item.get("id") or "") for item in memories)
        scopes = sorted({str(item.get("scope") or "") for item in memories
                         if str(item.get("scope") or "")})
        first_id = memory_ids[0] if memory_ids else ""
        actions = []
        for label in suggested_actions:
            kind = "edit" if re.search(r"编辑|重写|补充|归档|确认权威", str(label)) else "locate"
            actions.append({"kind": kind, "memory_id": first_id, "label": str(label)})
        return {
            "id": issue_id,
            "severity": severity,
            "type": issue_type,
            "title": title,
            "description": description,
            "evidence": list(evidence),
            "impact": {
                "count": len(memory_ids),
                "scopes": scopes,
                "memory_ids": memory_ids,
                "description": impact,
            },
            "memory_ids": memory_ids,
            "suggested_actions": actions,
            "fingerprint": fingerprint,
        }

    def _derive_issues(self, memories: Sequence[Mapping[str, Any]],
                       usage: Mapping[str, Any]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []

        # Exact normalized duplicates.  This is deliberately conservative: no
        # fuzzy similarity is used, because similar research notes may differ
        # in one decision-critical qualifier.
        by_text: Dict[str, List[Mapping[str, Any]]] = {}
        for memory in memories:
            normalized = _normal(memory.get("text"))
            if normalized:
                by_text.setdefault(normalized, []).append(memory)
        for normalized, group in sorted(by_text.items()):
            if len(group) < 2:
                continue
            scopes = "、".join(sorted({str(row.get("scope_label") or row.get("scope"))
                                      for row in group}))
            issues.append(self._new_issue(
                "exact_duplicate", "info", group,
                title="存在重复记忆",
                description="多条记忆的规范化正文完全一致，可人工确认是否需要合并。",
                evidence=[f"相同正文共 {len(group)} 条", f"涉及范围：{scopes}",
                          f"正文摘要：{normalized[:120]}"],
                impact="重复内容可能占用召回预算，并让同一历史事实重复出现。",
                suggested_actions=["核对来源和日期", "保留权威版本后再人工归档重复项"],
                extra={"normalized_text": normalized},
            ))

        # Same key with different content.  Same-scope conflict is more likely
        # accidental; cross-scope conflict may be an intentional override and
        # is therefore worded as a relationship needing confirmation.
        by_key: Dict[str, List[Mapping[str, Any]]] = {}
        for memory in memories:
            key = _normal(memory.get("key"))
            if key and memory.get("scope") != "global_archive":
                by_key.setdefault(key, []).append(memory)
        for key, group in sorted(by_key.items()):
            if len(group) < 2 or len({_normal(row.get("text")) for row in group}) < 2:
                continue
            same_scope = len({str(row.get("scope") or "") for row in group}) == 1
            previews = [
                f"{row.get('scope_label') or row.get('scope')}：{str(row.get('text') or '')[:100]}"
                for row in group
            ]
            issues.append(self._new_issue(
                "key_conflict", "critical" if same_scope else "warning", group,
                title="同主题键内容冲突" if same_scope else "主题键存在跨层覆盖关系",
                description=(
                    "同一范围内的同主题键对应不同正文，可能存在互相矛盾的事实。"
                    if same_scope else
                    "不同记忆层使用同一主题键但正文不同，可能是有意覆盖，也可能是旧结论未清理。"
                ),
                evidence=[f"主题键：{key}", *previews],
                impact="主动召回可能同时获得不同版本，使用前必须确认当前权威口径。",
                suggested_actions=["比较日期、范围和原始依据", "人工确认权威版本并编辑主题键或归档旧版本"],
                extra={"key": key, "same_scope": same_scope},
            ))

        today = date.today()
        for memory in memories:
            text = str(memory.get("text") or "")
            key = str(memory.get("key") or "")
            if memory.get("scope") == "global_archive":
                continue
            marker = _TEMPORAL_RE.search(f"{key} {text}")
            day = _parse_day(memory.get("date"))
            age = (today - day).days if day else -1
            if (not marker or age < _POTENTIAL_STALE_DAYS
                    or _DURABLE_FACT_RE.search(f"{key} {text}")):
                continue
            issues.append(self._new_issue(
                "potential_stale", "warning", [memory],
                title="时效性记忆可能过期",
                description="该记忆含明确时效措辞且已超过复核阈值；这只是风险提示，不代表内容已被判定错误。",
                evidence=[f"记忆日期：{memory.get('date')}（距今 {age} 天）",
                          f"时效措辞：{marker.group(0)}",
                          f"正文摘要：{text[:120]}"],
                impact="若把历史状态当作当前事实，可能导致错误判断。",
                suggested_actions=["重新核验当前事实或能力状态", "确认后编辑日期/正文，或人工归档旧记录"],
                extra={"marker": marker.group(0)},
            ))

        for memory in memories:
            if not memory.get("subject_mismatch"):
                continue
            issues.append(self._new_issue(
                "subject_mismatch", "warning", [memory],
                title="记忆与当前股票不匹配",
                description="研究空间或会话已切换标的，但该记忆仍绑定旧股票；系统已阻止其进入主动召回。",
                evidence=[f"记忆标的：{memory.get('subject_code') or '通用'}",
                          f"当前标的：{memory.get('current_context_code') or '通用'}",
                          f"来源：{memory.get('scope_label') or memory.get('scope')}"],
                impact="若人工复制或误用，可能把另一只股票的结论带入当前研究。",
                suggested_actions=["核对标的绑定", "在原标的空间保留，或人工复制并重写为当前标的结论"],
                extra={"subject": memory.get("subject_code"),
                       "current": memory.get("current_context_code")},
            ))

        telemetry = self._telemetry(usage, memories)
        usage_memories = usage.get("memories") if isinstance(usage.get("memories"), dict) else {}
        if telemetry["status"] == "ready":
            for memory in memories:
                if (memory.get("scope") == "global_archive" or memory.get("key")
                        or memory.get("pinned") or memory.get("subject_mismatch")):
                    continue
                observed = usage_memories.get(str(memory.get("id") or "")) or {}
                first = _parse_dt(observed.get("first_observed_at"))
                last = _parse_dt(observed.get("last_observed_at"))
                opportunities = int(observed.get("query_opportunities") or 0)
                hits = int(observed.get("hit_count") or 0)
                span = (last - first).days if first and last else 0
                if (span < _LOW_USE_MIN_DAYS or opportunities < _LOW_USE_MIN_QUERIES
                        or hits != 0):
                    continue
                issues.append(self._new_issue(
                    "low_use", "info", [memory],
                    title="长期观测未发生召回",
                    description="真实召回遥测已覆盖足够长的窗口，但未观测到该条被命中；这不是自动删除依据。",
                    evidence=[f"观测窗口：{span} 天", f"真实查询机会：{opportunities} 次",
                              "观测到的召回命中：0 次"],
                    impact="该记忆可能信号较低，也可能只是尚未遇到对应问题。",
                    suggested_actions=["人工判断是否仍有长期价值", "需要时补充主题键；否则人工归档"],
                ))

        return sorted(issues, key=lambda item: (
            {"critical": 0, "warning": 1, "info": 2}.get(item["severity"], 9),
            item["type"], item["id"],
        ))

    def _load_usage(self) -> Dict[str, Any]:
        payload = _load_sidecar(self._usage_path, "memories")
        payload.setdefault("schema_version", _SCHEMA_VERSION)
        payload.setdefault("memories", {})
        payload.setdefault("total_queries", 0)
        payload.setdefault("total_hits", 0)
        return payload

    @staticmethod
    def _telemetry(usage: Mapping[str, Any],
                   memories: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        first = _parse_dt(usage.get("first_observed_at"))
        last = _parse_dt(usage.get("last_observed_at"))
        observed_days = max(0, (last - first).days) if first and last else 0
        total_queries = int(usage.get("total_queries") or 0)
        total_hits = int(usage.get("total_hits") or 0)
        by_memory = usage.get("memories") if isinstance(usage.get("memories"), dict) else {}
        eligible_ids = {
            str(memory.get("id") or "") for memory in memories
            if memory.get("scope") != "global_archive" and not memory.get("subject_mismatch")
        }
        observed_ids = {mid for mid in eligible_ids if mid in by_memory}
        coverage = (len(observed_ids) / len(eligible_ids)) if eligible_ids else 1.0
        ready = observed_days >= _LOW_USE_MIN_DAYS and total_queries >= _LOW_USE_MIN_QUERIES
        return {
            "status": "ready" if ready else "warming_up",
            "first_observed_at": usage.get("first_observed_at"),
            "last_observed_at": usage.get("last_observed_at"),
            "observed_days": observed_days,
            "total_queries": total_queries,
            "total_hits": total_hits,
            "minimum_days": _LOW_USE_MIN_DAYS,
            "minimum_queries": _LOW_USE_MIN_QUERIES,
            "coverage": round(coverage, 4),
        }

    def record_recall(self, *, session_id: Any = "", workspace_id: Any = "",
                      query: Any = "", hits: Iterable[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        """Record one actual query and its stable-id hits.

        Zero-hit queries are important evidence and are recorded.  The method
        first asks MemoryAdmin for the caller's visible set, both validating
        context binding and giving each memory a real query opportunity.
        """
        if not str(query or "").strip():
            return {"ok": True, "recorded": False, "reason": "empty_query"}
        with _INSIGHT_LOCK:
            listed = self.admin.list(
                session_id=session_id, workspace_id=workspace_id,
                include_archived=True, limit=1000,
            )
            visible = list(listed.get("memories") or [])
            visible_ids = {str(memory.get("id") or "") for memory in visible}
            hit_ids = {
                str(hit.get("id") or "") for hit in hits
                if str(hit.get("id") or "") in visible_ids
            }
            now = _now()
            usage = self._load_usage()
            usage["schema_version"] = _SCHEMA_VERSION
            usage["first_observed_at"] = usage.get("first_observed_at") or now
            usage["last_observed_at"] = now
            usage["total_queries"] = int(usage.get("total_queries") or 0) + 1
            usage["total_hits"] = int(usage.get("total_hits") or 0) + len(hit_ids)
            memory_usage: MutableMapping[str, Dict[str, Any]] = usage.setdefault("memories", {})
            for memory_id in visible_ids:
                row = dict(memory_usage.get(memory_id) or {})
                row["first_observed_at"] = row.get("first_observed_at") or now
                row["last_observed_at"] = now
                row["query_opportunities"] = int(row.get("query_opportunities") or 0) + 1
                row.setdefault("hit_count", 0)
                if memory_id in hit_ids:
                    row["hit_count"] = int(row.get("hit_count") or 0) + 1
                    row["first_recalled_at"] = row.get("first_recalled_at") or now
                    row["last_recalled_at"] = now
                memory_usage[memory_id] = row
            _save_sidecar(self._usage_path, usage, "memories")
            return {"ok": True, "recorded": True, "hit_count": len(hit_ids)}

    def audit(self, *, session_id: Any = "", workspace_id: Any = "",
              include_archived: Any = True) -> Dict[str, Any]:
        """Return current explainable findings without changing memory content."""
        with _INSIGHT_LOCK:
            listed = self.admin.list(
                session_id=session_id, workspace_id=workspace_id,
                include_archived=_truthy(include_archived), limit=1000,
            )
            memories = list(listed.get("memories") or [])
            usage = self._load_usage()
            issues = self._derive_issues(memories, usage)
            state = _load_sidecar(self._state_path, "issues")
            state_items: MutableMapping[str, Dict[str, Any]] = state.setdefault("issues", {})
            now = _now()
            changed = False
            for issue in issues:
                previous = state_items.get(issue["id"])
                if not isinstance(previous, dict) \
                        or previous.get("fingerprint") != issue["fingerprint"]:
                    previous = {
                        "fingerprint": issue["fingerprint"],
                        "status": "open",
                        "created_at": now,
                        "updated_at": now,
                    }
                    state_items[issue["id"]] = previous
                    changed = True
                status = str(previous.get("status") or "open")
                if status not in _VALID_STATUSES:
                    status = "open"
                issue.update({
                    "status": status,
                    # The current UI has one closed-items drawer; resolved is a
                    # stronger closed state and must not reappear as open.
                    "dismissed": status in {"dismissed", "resolved"},
                    "resolved": status == "resolved",
                    "created_at": previous.get("created_at") or now,
                    "updated_at": previous.get("updated_at") or now,
                })
            if changed:
                state["schema_version"] = _SCHEMA_VERSION
                state["updated_at"] = now
                _save_sidecar(self._state_path, state, "issues")

            counts_status = {status: 0 for status in ("open", "dismissed", "resolved")}
            by_type: Dict[str, int] = {}
            by_severity: Dict[str, int] = {}
            for issue in issues:
                counts_status[issue["status"]] += 1
                by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1
                by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1
            open_issues = [issue for issue in issues if issue["status"] == "open"]
            open_severity = {
                severity: sum(1 for issue in open_issues if issue["severity"] == severity)
                for severity in ("critical", "warning", "info")
            }
            penalty = sum({"critical": 20, "warning": 8, "info": 2}.get(
                issue["severity"], 0) for issue in open_issues)
            score = max(0, 100 - penalty)
            status = (
                "risk" if any(issue["severity"] == "critical" for issue in open_issues)
                else "attention" if open_issues else "healthy"
            )
            return {
                "ok": True,
                "summary": {
                    "status": status,
                    "score": score,
                    "total": len(issues),
                    "counts": {
                        **counts_status,
                        # Direct open-severity counters are the compact UI
                        # contract.  Dismissed/resolved remain separate above.
                        **open_severity,
                        "by_type": by_type,
                        "by_severity": by_severity,
                    },
                    "generated_at": now,
                    "telemetry": self._telemetry(usage, memories),
                },
                # Current dismissed/resolved issues intentionally remain in the
                # payload so the UI can hide or restore them.  A content edit
                # changes etag/fingerprint/id, so an old dismissal cannot mask
                # the new finding.
                "insights": issues,
                "context": listed.get("context") or {},
                "read_only_audit": True,
            }

    def set_status(self, issue_id: str, status: Any, *, session_id: Any = "",
                   workspace_id: Any = "", include_archived: Any = True) -> Dict[str, Any]:
        value = str(status or "").strip().lower()
        if value not in _VALID_STATUSES:
            raise MemoryAdminError(400, "status 非法(允许 open/dismissed/resolved)")
        with _INSIGHT_LOCK:
            current = self.audit(
                session_id=session_id, workspace_id=workspace_id,
                include_archived=include_archived,
            )
            issue = next((row for row in current["insights"] if row["id"] == issue_id), None)
            if issue is None:
                raise MemoryAdminError(404, "记忆洞察不存在或已因内容变化失效")
            state = _load_sidecar(self._state_path, "issues")
            entries: MutableMapping[str, Dict[str, Any]] = state.setdefault("issues", {})
            entry = dict(entries.get(issue_id) or {})
            now = _now()
            entry.update({
                "fingerprint": issue["fingerprint"],
                "status": value,
                "created_at": entry.get("created_at") or issue.get("created_at") or now,
                "updated_at": now,
            })
            entries[issue_id] = entry
            state["schema_version"] = _SCHEMA_VERSION
            state["updated_at"] = now
            _save_sidecar(self._state_path, state, "issues")
            issue.update({
                "status": value,
                "dismissed": value in {"dismissed", "resolved"},
                "resolved": value == "resolved",
                "updated_at": now,
            })
            return {"ok": True, "insight": issue}

    def dismiss(self, issue_id: str, body: Optional[Mapping[str, Any]] = None, *,
                session_id: Any = "", workspace_id: Any = "",
                include_archived: Any = True) -> Dict[str, Any]:
        dismissed = _truthy((body or {}).get("dismissed", True))
        return self.set_status(
            issue_id, "dismissed" if dismissed else "open",
            session_id=session_id, workspace_id=workspace_id,
            include_archived=include_archived,
        )

    def annotate_hits(self, hits: Sequence[Dict[str, Any]], *, session_id: Any = "",
                      workspace_id: Any = "") -> Sequence[Dict[str, Any]]:
        """Add best-effort verification warnings to actual recall hits."""
        if not hits:
            return hits
        report = self.audit(
            session_id=session_id, workspace_id=workspace_id,
            include_archived=True,
        )
        warning_types = {"potential_stale", "key_conflict"}
        by_memory: Dict[str, List[Dict[str, Any]]] = {}
        for issue in report["insights"]:
            if issue["status"] != "open" or issue["type"] not in warning_types:
                continue
            label = "可能过期" if issue["type"] == "potential_stale" else "主题覆盖冲突"
            warning = {
                "issue_id": issue["id"],
                "type": issue["type"],
                "label": label,
                "reason": issue["description"],
            }
            for memory_id in issue["memory_ids"]:
                by_memory.setdefault(memory_id, []).append(warning)
        for hit in hits:
            warnings = by_memory.get(str(hit.get("id") or ""), [])
            if not warnings:
                continue
            hit["governance_warnings"] = warnings
            hit["requires_verification"] = True
        return hits
