"""Deterministic A-share industry context for workspace NewsRadar reads.

The contract is intentionally one-way::

    bound stock -> current Tushare industry -> reviewed NewsRadar sector

It never infers that an RSS item is about the issuer.  Every returned article
remains an unreviewed industry candidate until the user decides relevance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "company-industry-news-context@1"
RELATION_EXTENSION_SCHEMA = "newsradar-industry-candidate-relations@1"
REVIEW_PROJECTION_SCHEMA = "newsradar-industry-review-projection@1"
_CROSSWALK_PATH = Path(__file__).with_name("industry_newsradar_map.json")
_VALID_SECTORS = frozenset({
    "ai", "semi", "robot", "auto", "energy", "bio", "space",
    "security", "tech", "consumer", "macro", "science",
})
_CODE_RE = re.compile(r"^(SH|SZ|BJ)([0-9]{6})$")


def _clean(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _iso_date(value: Any) -> str:
    text = _clean(value, 80)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}.*", text) else ""
    return parsed.date().isoformat()


def _file_observed_at(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@lru_cache(maxsize=1)
def _crosswalk() -> tuple[str, dict[str, dict[str, Any]]]:
    payload = json.loads(_CROSSWALK_PATH.read_text(encoding="utf-8"))
    version = _clean(payload.get("schema_version"), 80)
    if not version:
        raise ValueError("NewsRadar 行业映射缺少 schema_version")
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("mappings") or []:
        if not isinstance(row, Mapping):
            continue
        industry = _clean(row.get("industry"), 80)
        sectors = tuple(dict.fromkeys(
            _clean(item, 20).lower() for item in (row.get("sectors") or [])
            if _clean(item, 20)
        ))
        quality = _clean(row.get("mapping_quality") or "exact", 40).lower()
        note = _clean(row.get("note"), 300)
        if not industry or not sectors or any(item not in _VALID_SECTORS for item in sectors):
            raise ValueError("NewsRadar 行业映射存在无效条目")
        if quality not in {"exact", "broad_context"}:
            raise ValueError("NewsRadar 行业映射 quality 非法")
        if quality == "broad_context" and not note:
            raise ValueError("NewsRadar 宽口径行业映射必须说明降级原因")
        if industry in out:
            raise ValueError(f"NewsRadar 行业映射重复：{industry}")
        out[industry] = {
            "sectors": sectors, "quality": quality, "note": note or None,
        }
    return version, out


def _stock_basic_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    try:
        from financial_analyst.data.paths import get_data_paths
        paths.append(get_data_paths().parquet_root / "tushare_stock_basic.parquet")
    except Exception:
        pass
    try:
        from guanlan_v2.strategy.paths import STOCK_BASIC_PARQUET
        paths.append(Path(STOCK_BASIC_PARQUET))
    except Exception:
        pass
    return tuple(dict.fromkeys(paths))


def _ts_code(code: str) -> str:
    match = _CODE_RE.fullmatch(_clean(code, 20).upper())
    if not match:
        return ""
    exchange, digits = match.groups()
    return f"{digits}.{exchange}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _industry_record(code: str) -> dict[str, Any]:
    wanted = _ts_code(code)
    if not wanted:
        return {"status": "invalid_subject", "reason": "仅支持带交易所前缀的 A 股六位代码"}
    for path in _stock_basic_paths():
        if not path.is_file():
            continue
        try:
            import pandas as pd
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if "ts_code" not in frame.columns or "industry" not in frame.columns:
            continue
        hit = frame[frame["ts_code"].astype(str).str.upper() == wanted]
        if hit.empty:
            continue
        row = hit.iloc[0]
        industry = _clean(row.get("industry"), 80)
        if not industry or industry in {"未知", "None", "nan"}:
            continue
        fetched = _clean(row.get("fetched_at"), 80) if "fetched_at" in frame.columns else ""
        observed_at = fetched or _file_observed_at(path)
        return {
            "status": "available",
            "name": _clean(row.get("name"), 120),
            "industry": industry,
            "source_id": "tushare.stock_basic.industry",
            "taxonomy": "Tushare 基础行业（非申万/证监会分类）",
            "observed_at": observed_at,
            "source_sha256": _sha256(path),
            "point_in_time": False,
        }
    return {
        "status": "unavailable",
        "reason": "当前 stock_basic 快照未找到该代码或行业字段",
    }


def _normalise_sectors(values: Sequence[str] | None) -> list[str]:
    sectors = list(dict.fromkeys(
        _clean(item, 20).lower() for item in (values or []) if _clean(item, 20)
    ))
    unknown = [item for item in sectors if item not in _VALID_SECTORS]
    if unknown:
        raise ValueError("未知 NewsRadar 赛道：" + ",".join(unknown))
    return sectors


def resolve_industry_news_context(
    code: str,
    *,
    selected_sectors: Sequence[str] | None = None,
    as_of: str = "",
    today: str | None = None,
) -> dict[str, Any]:
    """Resolve an auditable industry-news selection for one bound stock."""
    canonical = _clean(code, 20).upper()
    explicit = _normalise_sectors(selected_sectors)
    record = _industry_record(canonical)
    version, mapping = _crosswalk()
    industry = _clean(record.get("industry"), 80)
    mapped = mapping.get(industry) or {}
    automatic = list(mapped.get("sectors") or ())
    sectors = explicit or automatic
    mapping_quality = str(mapped.get("quality") or "") or None
    if explicit:
        selection_mode = "user_confirmed_override"
        mapping_method = "user_confirmed_override"
        effective_quality = "user_confirmed"
        mapping_note = "用户为本次查询明确选择 NewsRadar 赛道"
    elif mapping_quality == "exact":
        selection_mode = "automatic_exact_crosswalk"
        mapping_method = "exact_reviewed_label"
        effective_quality = "exact"
        mapping_note = mapped.get("note")
    elif mapping_quality == "broad_context":
        selection_mode = "automatic_reviewed_broad_context"
        mapping_method = "reviewed_broad_context"
        effective_quality = "broad_context"
        mapping_note = mapped.get("note")
    else:
        selection_mode = "unmapped"
        mapping_method = "none"
        effective_quality = None
        mapping_note = None
    requested_as_of = _iso_date(as_of)
    current_day = _iso_date(today) or date.today().isoformat()

    status = "ready"
    reason = ""
    if requested_as_of and requested_as_of != current_day:
        status = "historical_feed_unavailable"
        reason = "NewsRadar 是当前实时缓存，不能用于非当日历史研究截止日"
    elif record.get("status") != "available" and not explicit:
        status = "industry_unavailable"
        reason = _clean(record.get("reason"), 300)
    elif not sectors:
        status = "industry_unmapped"
        reason = "该基础行业尚无审校后的 NewsRadar 赛道映射；需由用户明确选择赛道"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason or None,
        "subject_code": canonical or None,
        "subject_name": _clean(record.get("name"), 120) or None,
        "industry": industry or None,
        "industry_source": {
            "source_id": record.get("source_id"),
            "taxonomy": record.get("taxonomy"),
            "observed_at": record.get("observed_at"),
            "source_sha256": record.get("source_sha256"),
            "point_in_time": bool(record.get("point_in_time", False)),
        },
        "mapping_version": version,
        "mapping_method": mapping_method,
        "mapping_quality": effective_quality,
        "mapping_note": mapping_note,
        "selection_mode": selection_mode,
        "radar_sectors": sectors,
        "research_scope": "industry_context",
        "relation_status": "unreviewed",
        "user_judgement_required": True,
        "may_close_issuer_gate": False,
        "requested_as_of": requested_as_of or current_day,
    }


def annotate_newsradar_snapshot(
    snapshot: Mapping[str, Any], context: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep source rows stable and publish company-relevance claims in an extension.

    ``rows`` is a long-standing workspace/live adapter surface, so additive
    candidate metadata must not mutate each provider row.  The separately
    versioned relation extension points back by collection/index and makes the
    non-issuer, user-reviewed semantics explicit.
    """
    out = copy.deepcopy(dict(snapshot))
    safe_context = copy.deepcopy(dict(context))
    out["industry_context"] = safe_context

    def candidate_relation(row: Mapping[str, Any], *, collection: str,
                           row_index: int) -> dict[str, Any]:
        item = dict(row)
        identity = {
            "source": item.get("source"), "url": item.get("url"),
            "title": item.get("title"), "ts": item.get("ts"),
            "industry": safe_context.get("industry"), "sector": item.get("sector"),
        }
        raw = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return {
            "candidate_id": "INC-" + hashlib.sha256(raw).hexdigest()[:16].upper(),
            "collection": collection,
            "row_index": row_index,
            "source": item.get("source"),
            "url": item.get("url"),
            "title": item.get("title"),
            "ts": item.get("ts"),
            "sector": item.get("sector"),
            "research_scope": "industry_context",
            "industry_anchor": safe_context.get("industry"),
            "relation_status": "unreviewed",
            "relation_basis": "same_newsradar_sector",
            "user_judgement_required": True,
            "issuer_fact": False,
        }

    def stable_rows(raw_rows: Any, *, collection: str) -> tuple[list[Any], list[dict[str, Any]]]:
        rows: list[Any] = []
        relations: list[dict[str, Any]] = []
        for index, row in enumerate(raw_rows if isinstance(raw_rows, list) else []):
            if not isinstance(row, Mapping):
                rows.append(row)
                continue
            item = dict(row)
            # RSS 外部输入无权声明发行人主体；这是安全清洗，不是候选注解字段。
            item.pop("subject_code", None)
            rows.append(item)
            relations.append(candidate_relation(item, collection=collection, row_index=index))
        return rows, relations

    rows, row_relations = stable_rows(out.get("rows"), collection="rows")
    out["rows"] = rows
    future_relations: list[dict[str, Any]] = []
    if isinstance(out.get("future_items"), list):
        future_items, future_relations = stable_rows(
            out["future_items"], collection="future_items")
        out["future_items"] = future_items
    out["industry_candidate_relations"] = {
        "schema_version": RELATION_EXTENSION_SCHEMA,
        "research_scope": "industry_context",
        "industry_anchor": safe_context.get("industry"),
        "relation_status": "unreviewed",
        "user_judgement_required": True,
        "issuer_fact": False,
        "rows": row_relations,
        "future_items": future_relations,
    }

    warnings = list(out.get("warnings") or [])
    warnings.append(
        "这些条目只因与当前公司的基础行业落在同一 NewsRadar 赛道而列出；"
        "系统未判断与公司相关，需由用户逐条判断，且不能作为发行人事实。"
    )
    out["warnings"] = list(dict.fromkeys(str(item) for item in warnings if str(item)))
    return out


def _candidate_identity(
    row: Mapping[str, Any], *, industry: Any,
) -> dict[str, Any]:
    """Return the exact identity used by the long-standing ``INC-*`` id.

    Keep this deliberately narrower than the whole provider row.  A feed may
    add presentation fields without invalidating an earlier human decision,
    while a changed source/title/url/time/sector necessarily becomes another
    candidate.
    """
    return {
        "source": row.get("source"),
        "url": row.get("url"),
        "title": row.get("title"),
        "ts": row.get("ts"),
        "industry": industry,
        "sector": row.get("sector"),
    }


def _candidate_identity_sha256(identity: Mapping[str, Any]) -> str:
    # Byte-for-byte compatible with the original candidate-id generator in
    # ``annotate_newsradar_snapshot`` (default JSON separators are intentional).
    raw = json.dumps(
        dict(identity), ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def snapshot_candidate(
    snapshot: Mapping[str, Any], candidate_id: str,
) -> dict[str, Any]:
    """Resolve and verify one candidate against its immutable provider row.

    The relation extension is untrusted data until its collection/index and
    truncated id are checked against the row.  The returned full digest is the
    durable identity bound by the private review ledger.
    """
    wanted = _clean(candidate_id, 40).upper()
    if not re.fullmatch(r"INC-[0-9A-F]{16}", wanted):
        raise ValueError("candidate_id 格式非法")
    extension = snapshot.get("industry_candidate_relations")
    if not isinstance(extension, Mapping) \
            or extension.get("schema_version") != RELATION_EXTENSION_SCHEMA:
        raise ValueError("NewsRadar 快照缺少可审核的行业候选扩展")
    context = snapshot.get("industry_context")
    context = context if isinstance(context, Mapping) else {}
    industry = context.get("industry")

    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for collection in ("rows", "future_items"):
        source_rows = snapshot.get(collection)
        relations = extension.get(collection)
        if not isinstance(source_rows, list) or not isinstance(relations, list):
            continue
        for relation in relations:
            if not isinstance(relation, Mapping) \
                    or _clean(relation.get("candidate_id"), 40).upper() != wanted:
                continue
            if relation.get("collection") != collection:
                raise ValueError("行业候选 collection 绑定不一致")
            index = relation.get("row_index")
            if isinstance(index, bool) or not isinstance(index, int) \
                    or index < 0 or index >= len(source_rows):
                raise ValueError("行业候选 row_index 绑定无效")
            raw_row = source_rows[index]
            if not isinstance(raw_row, Mapping):
                raise ValueError("行业候选对应的资讯行不是对象")
            row = dict(raw_row)
            identity = _candidate_identity(row, industry=industry)
            digest = _candidate_identity_sha256(identity)
            if wanted != "INC-" + digest[:16].upper():
                raise ValueError("行业候选编号与资讯内容不一致")
            for field in ("source", "url", "title", "ts", "sector"):
                if relation.get(field) != row.get(field):
                    raise ValueError(f"行业候选 {field} 与资讯行不一致")
            if relation.get("industry_anchor") != industry \
                    or relation.get("research_scope") != "industry_context" \
                    or relation.get("issuer_fact") is not False:
                raise ValueError("行业候选越过了 context/issuer_fact 边界")
            matches.append((dict(relation), row))
    if len(matches) != 1:
        raise ValueError("candidate_id 在该快照中不存在或不唯一")
    relation, row = matches[0]
    return {
        "candidate_id": wanted,
        "candidate_sha256": _candidate_identity_sha256(
            _candidate_identity(row, industry=industry)
        ),
        "relation": relation,
        "row": row,
    }


def project_reviewed_newsradar_snapshot(
    snapshot: Mapping[str, Any], review_state: Mapping[str, Any] | None, *,
    snapshot_asset_id: str = "",
) -> dict[str, Any]:
    """Build the report-visible NewsRadar projection.

    Raw saved snapshots remain immutable and retain every candidate for the
    review UI.  This derived projection contains *only* human-confirmed
    ``related`` rows.  ``related`` means relevant industry context, never that
    the external claim is true and never an issuer fact.
    """
    out = copy.deepcopy(dict(snapshot))
    state = dict(review_state or {})
    bound_asset_id = _clean(snapshot_asset_id, 40)
    context = out.get("industry_context")
    context = context if isinstance(context, Mapping) else {}
    snapshot_subject = _clean(context.get("subject_code"), 32).upper()
    state_subject = _clean(state.get("subject_code"), 32).upper()
    state_matches_snapshot = bool(
        re.fullmatch(r"wa_[0-9a-f]{16}", bound_asset_id)
        and re.fullmatch(r"(?:SH|SZ|BJ)[0-9]{6}", snapshot_subject)
        and state_subject == snapshot_subject
    )
    raw_decisions = state.get("decisions")
    decisions: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in raw_decisions if isinstance(raw_decisions, list) else []:
        if not state_matches_snapshot or not isinstance(raw, Mapping):
            continue
        asset_id = _clean(raw.get("snapshot_asset_id"), 40)
        candidate_id = _clean(raw.get("candidate_id"), 40).upper()
        candidate_sha = _clean(raw.get("candidate_sha256"), 80).lower()
        decision = _clean(raw.get("decision"), 20).lower()
        if asset_id == bound_asset_id \
                and re.fullmatch(r"INC-[0-9A-F]{16}", candidate_id) \
                and re.fullmatch(r"[0-9a-f]{64}", candidate_sha) \
                and decision in {"related", "unrelated", "uncertain"}:
            decisions[(asset_id, candidate_id, candidate_sha)] = dict(raw)

    extension = out.get("industry_candidate_relations")
    relation_lists = []
    if isinstance(extension, Mapping) \
            and extension.get("schema_version") == RELATION_EXTENSION_SCHEMA:
        for collection in ("rows", "future_items"):
            raw = extension.get(collection)
            if isinstance(raw, list):
                relation_lists.extend(
                    dict(item) for item in raw if isinstance(item, Mapping)
                )

    counts = {
        "total": 0, "related": 0, "unrelated": 0,
        "uncertain": 0, "unreviewed": 0, "invalid": 0,
        "future_excluded": 0,
    }
    eligible_rows: list[dict[str, Any]] = []
    eligible_relations: list[dict[str, Any]] = []
    eligible_ids: list[str] = []
    seen_ids: set[str] = set()
    for relation in relation_lists:
        candidate_id = _clean(relation.get("candidate_id"), 40).upper()
        if candidate_id in seen_ids:
            counts["invalid"] += 1
            continue
        seen_ids.add(candidate_id)
        counts["total"] += 1
        try:
            resolved = snapshot_candidate(out, candidate_id)
        except (TypeError, ValueError):
            counts["invalid"] += 1
            continue
        digest = resolved["candidate_sha256"]
        review = decisions.get((bound_asset_id, candidate_id, digest))
        decision = _clean((review or {}).get("decision"), 20).lower() or "unreviewed"
        if decision not in counts:
            decision = "unreviewed"
        counts[decision] += 1
        if decision != "related":
            continue
        if relation.get("collection") != "rows":
            counts["future_excluded"] += 1
            continue
        row = dict(resolved["row"])
        row.pop("subject_code", None)
        projected_relation = dict(resolved["relation"])
        projected_relation.update({
            "row_index": len(eligible_rows),
            "original_row_index": resolved["relation"].get("row_index"),
            "relation_status": "related",
            "user_judgement_required": False,
            "report_eligible": True,
            "issuer_fact": False,
            "review": {
                "entry_id": (review or {}).get("entry_id"),
                "decision": "related",
                "reviewed_at": (review or {}).get("reviewed_at"),
                "candidate_sha256": digest,
            },
        })
        eligible_rows.append(row)
        eligible_relations.append(projected_relation)
        eligible_ids.append(candidate_id)

    out["rows"] = eligible_rows
    if "future_items" in out:
        out["future_items"] = []
    out["industry_candidate_relations"] = {
        "schema_version": RELATION_EXTENSION_SCHEMA,
        "projection_schema_version": REVIEW_PROJECTION_SCHEMA,
        "research_scope": "industry_context",
        "industry_anchor": (
            (out.get("industry_context") or {}).get("industry")
            if isinstance(out.get("industry_context"), Mapping) else None
        ),
        "relation_status": "related" if eligible_relations else "none_related",
        "report_eligible_only": True,
        "issuer_fact": False,
        "rows": eligible_relations,
        "future_items": [],
    }
    projection_identity = {
        "snapshot_asset_id": bound_asset_id,
        "ledger_revision": int(state.get("revision") or 0),
        "ledger_head_sha256": state.get("head_sha256"),
        "eligible_candidate_ids": eligible_ids,
        "counts": counts,
    }
    out["industry_review_gate"] = {
        "schema_version": REVIEW_PROJECTION_SCHEMA,
        "status": (
            "ready" if eligible_ids else
            "awaiting_review" if counts["unreviewed"] or counts["uncertain"] else
            "no_related_candidates"
        ),
        "only_related_report_eligible": True,
        "issuer_fact": False,
        "snapshot_asset_id": bound_asset_id,
        "ledger_revision": projection_identity["ledger_revision"],
        "ledger_head_sha256": projection_identity["ledger_head_sha256"],
        "candidate_counts": counts,
        "report_eligible_candidate_ids": eligible_ids,
        "projection_sha256": _candidate_identity_sha256(projection_identity),
    }
    warnings = [str(item) for item in (out.get("warnings") or []) if str(item)]
    warnings.append(
        f"行业候选共 {counts['total']} 条；仅 {len(eligible_ids)} 条经用户确认 related "
        "后进入研报上下文。unreviewed/uncertain/unrelated 均已隔离；related 仍非发行人事实。"
    )
    out["warnings"] = list(dict.fromkeys(warnings))
    return out


def newsradar_report_projection_is_valid(snapshot: Mapping[str, Any]) -> bool:
    """Fail-closed integrity check used by the final report citation gate.

    A projection is derived data, but it still has to prove that every visible
    row matches its stable candidate identity and a concrete human ``related``
    decision.  This stops a caller from merely adding a ``report_eligible``
    boolean to an unreviewed provider row.
    """
    gate = snapshot.get("industry_review_gate")
    extension = snapshot.get("industry_candidate_relations")
    rows = snapshot.get("rows")
    if not isinstance(gate, Mapping) \
            or gate.get("schema_version") != REVIEW_PROJECTION_SCHEMA \
            or gate.get("only_related_report_eligible") is not True \
            or gate.get("issuer_fact") is not False \
            or not isinstance(extension, Mapping) \
            or extension.get("projection_schema_version") != REVIEW_PROJECTION_SCHEMA \
            or extension.get("report_eligible_only") is not True \
            or extension.get("issuer_fact") is not False \
            or gate.get("status") != "ready" \
            or not isinstance(rows, list) \
            or not isinstance(extension.get("rows"), list) \
            or extension.get("future_items") != [] \
            or ("future_items" in snapshot and snapshot.get("future_items") != []):
        return False
    relations = extension["rows"]
    ids = gate.get("report_eligible_candidate_ids")
    if not isinstance(ids, list) or not (len(rows) == len(relations) == len(ids)):
        return False
    revision = gate.get("ledger_revision")
    head = gate.get("ledger_head_sha256")
    asset_id = gate.get("snapshot_asset_id")
    counts = gate.get("candidate_counts")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1 \
            or not isinstance(head, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", head) \
            or not isinstance(asset_id, str) \
            or not re.fullmatch(r"wa_[0-9a-f]{16}", asset_id) \
            or not isinstance(counts, Mapping):
        return False
    count_keys = {
        "total", "related", "unrelated", "uncertain", "unreviewed",
        "invalid", "future_excluded",
    }
    if set(counts) != count_keys or any(
        isinstance(counts[key], bool) or not isinstance(counts[key], int)
        or counts[key] < 0 for key in count_keys
    ) or counts["related"] < len(rows):
        return False
    expected_projection_sha = _candidate_identity_sha256({
        "snapshot_asset_id": asset_id,
        "ledger_revision": revision,
        "ledger_head_sha256": head,
        "eligible_candidate_ids": ids,
        "counts": dict(counts),
    })
    if gate.get("projection_sha256") != expected_projection_sha:
        return False
    for index, relation in enumerate(relations):
        if not isinstance(relation, Mapping) \
                or relation.get("row_index") != index \
                or relation.get("relation_status") != "related" \
                or relation.get("report_eligible") is not True \
                or relation.get("issuer_fact") is not False \
                or relation.get("candidate_id") != ids[index]:
            return False
        review = relation.get("review")
        if not isinstance(review, Mapping) \
                or review.get("decision") != "related" \
                or not re.fullmatch(
                    r"IRV-[0-9A-F]{16}", str(review.get("entry_id") or ""),
                ):
            return False
        try:
            resolved = snapshot_candidate(snapshot, str(ids[index]))
        except (TypeError, ValueError):
            return False
        if review.get("candidate_sha256") != resolved["candidate_sha256"] \
                or "subject_code" in resolved["row"]:
            return False
    # An empty, valid projection contains no item a report may cite.
    return bool(rows)


__all__ = [
    "SCHEMA_VERSION",
    "RELATION_EXTENSION_SCHEMA",
    "REVIEW_PROJECTION_SCHEMA",
    "annotate_newsradar_snapshot",
    "newsradar_report_projection_is_valid",
    "project_reviewed_newsradar_snapshot",
    "resolve_industry_news_context",
    "snapshot_candidate",
]
