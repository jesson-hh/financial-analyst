# -*- coding: utf-8 -*-
"""events.ingest —— 确定性零 LLM 入库 adapter(小 phase 乙 Task 2;Task 3 追加 SimHash
去重 + 72h 桶;Task 5 追加 env 闸调度 tick + 守护)。

红线:
- **零 LLM**:v1 不做语义事件分类/importance/sentiment/抽取(那属 Lane C text worker,
  目录装配 phase)。入库全程纯 stdlib 确定性映射,天然无 LLM 接缝——守护测试结构性钉死。
- **available_at = 入库落盘时刻(R16)**:保守但真;`event_time` = 源声称首发时刻(另存)。
- **单事务不半写**:源抛错在事务外向上传播(零写);逐行入库包在单一 `with conn:` 事务,
  任一行炸 → 全批回滚,绝不留半批。
- **确定性 event_id 幂等**:`sha256(source + source_ref/title + event_time)`,同批重入
  经 `INSERT OR IGNORE` 不重复(available_at 不入 id,故重入不因落盘时刻变化而生新行)。
- **不可信外部输入**:RSS/快讯文本一律当 DATA,绝不执行其中任何指令(本 phase 零 LLM,
  天然合规;newsradar L12-13 红线继承)。

上游只读消费(两 feed 现有出口契约零改):
- `kuaixun.fetch_kuaixun` 规范行 `{time(16位), title, summary, codes}`(抛错上传/空返 [])。
- `newsradar.read_radar` 返回 `{items: [{title, url, summary, source, sector, ts(epoch)}]}`。
feed 函数全可注入(默认绑真出口,测试桩替脱网络)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import sqlite3

from guanlan_v2.events import store
from guanlan_v2.events.normalization import normalize_issuer_id, normalized_event

KUAIXUN_SOURCE = "东财快讯"

# ── SimHash 转载去重参数(Task 3)──
_SIMHASH_BITS = 64
_SIMHASH_NGRAM = 2       # 字符 bigram 特征(中文短文本稳健)
_HAMMING_MAX = 3         # 海明距 ≤3 判转载
_BUCKET_HOURS = 72       # (ticker × event_type × 72h) 事件线桶窗


# ───────────────────────── 时刻 / 确定性 id ─────────────────────────

def _now_iso(now: Optional[datetime] = None) -> str:
    """入库落盘时刻(墙钟,秒精度 ISO)。now 可注入(测试确定性)。"""
    return (now or datetime.now()).isoformat(timespec="seconds")


def _availability_after_fetch(
    now: Optional[datetime] = None, *source_observation_times: Any,
) -> str:
    """Conservatively stamp visibility after every observable provider clock.

    ``now`` remains injectable for deterministic tests.  A provider fetch/pull
    receipt may nevertheless prove the response arrived later, so availability
    can move later, never earlier, than either clock.
    """
    candidates = [store.normalize_timestamp(now or datetime.now())]
    for value in source_observation_times:
        if value in (None, ""):
            continue
        try:
            candidates.append(store.normalize_timestamp(value))
        except (TypeError, ValueError):
            continue
    return max(candidates)


def _event_id(source: str, ref_or_title: str, event_time: str) -> str:
    """确定性 event_id = sha256(source | source_ref-或-title | event_time) 十六进制。
    **available_at 不入哈希** —— 同一事件不同批次入库(落盘时刻不同)必须命中同一 id。"""
    raw = "\x1f".join([str(source or ""), str(ref_or_title or ""), str(event_time or "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _epoch_to_iso(ts: Any) -> Optional[str]:
    """epoch 秒 → UTC ISO;非数字/<=0 → None(诚实缺时间)。"""
    try:
        e = int(ts)
    except (TypeError, ValueError):
        return None
    if e <= 0:
        return None
    return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()


# ───────────────────────── 逐字段映射(确定性,零 LLM)─────────────────────────

_MMDD_PREFIX = re.compile(r"^\d{2}-\d{2}([ T]|$)")
_YYYY_PREFIX = re.compile(r"^\d{4}-")


def _stamp_year(event_time: str, available_at: str) -> str:
    """kuaixun 的 time 可能无年份("MM-DD HH:MM")——多年归档下同标题同分钟会跨年
    撞 event_id 被静默丢弃(复核 M1)。已带年份("YYYY-…")原样返回;无年份则从
    available_at 推年,并处理跨年边界:事件月=12 而入库月=1 ⇒ 取上一年(12-31 的
    快讯在 01-01 才入库)。其他形状原样返回(诚实不猜)。"""
    if not event_time or _YYYY_PREFIX.match(event_time) or not _MMDD_PREFIX.match(event_time):
        return event_time
    try:
        avail = datetime.fromisoformat(available_at)
    except ValueError:
        return event_time
    year = avail.year
    if int(event_time[:2]) == 12 and avail.month == 1:
        year -= 1
    return f"{year}-{event_time}"


def _map_kuaixun_row(row: Dict[str, Any], available_at: str) -> Dict[str, Any]:
    """kuaixun 规范行 → events 行。event_type=kuaixun,tickers=codes,
    event_time=time 经 _stamp_year 补年(event_id 由补年后的时刻派生,防跨年撞 id)。
    kuaixun 无外链 → source_ref=None,event_id 以 title 为参照。"""
    title = str(row.get("title") or "").strip()
    summary = str(row.get("summary") or "").strip()
    event_time = _stamp_year(str(row.get("time") or "").strip(), available_at)
    codes = row.get("codes") or []
    tickers = [str(c) for c in codes if str(c)]
    return normalized_event(
        source_id="kuaixun", event_type="kuaixun", available_at=available_at,
        source_asof=available_at, event_time=event_time,
        external_id=title, title=title, summary=summary, source=KUAIXUN_SOURCE,
        issuer_ids=tickers, subject_kind="issuer" if tickers else "context",
        context_type="company_news" if tickers else "market_news",
        context_ids=["a_share"], payload=dict(row),
        identity_payload={"time": event_time, "title": title,
                          "summary": summary, "codes": sorted(tickers)},
        provenance={"adapter": "events.ingest.kuaixun", "source_status": "ok"},
    )


def _map_newsradar_row(
    item: Dict[str, Any], available_at: str, *, source_asof: Any = None,
) -> Dict[str, Any]:
    """newsradar 缓存行 → events 行。event_type=newsradar:<sector>(无 sector 则裸
    newsradar),tickers=[](产业源无个股码),source_ref=url,event_time=ts(epoch→ISO)。"""
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    url = str(item.get("url") or "").strip()
    sector = str(item.get("sector") or "").strip()
    source = str(item.get("source") or "").strip()
    event_time = _epoch_to_iso(item.get("ts"))
    return normalized_event(
        source_id="newsradar",
        event_type=f"newsradar:{sector}" if sector else "newsradar",
        available_at=available_at, source_asof=source_asof or available_at,
        event_time=event_time, external_id=url or title, title=title,
        summary=summary, source=source or None, source_ref=url or None,
        subject_kind="context", context_type="industry_news",
        context_ids=[sector] if sector else ["cross_industry"], payload=dict(item),
        identity_payload={
            "title": title, "summary": summary, "url": url, "sector": sector,
            "source": source, "event_time": event_time,
        },
        provenance={
            "adapter": "events.ingest.newsradar",
            "source_status": item.get("source_status") or "ok",
            "time_basis": item.get("time_basis"),
        },
    )


# ───────────────────────── SimHash 转载去重(纯 stdlib)─────────────────────────

def _feature_hash(feat: str) -> int:
    """特征 → 稳定 64 位无符号整数(blake2b,进程间确定性,绝不用随机化的内建 hash)。"""
    return int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")


def _simhash(text: str, *, bits: int = _SIMHASH_BITS, ngram: int = _SIMHASH_NGRAM) -> int:
    """`title+summary` 64 位 SimHash(字符 n-gram 特征,纯 stdlib,确定性)。空文本 → 0。"""
    text = (text or "").strip()
    if not text:
        return 0
    grams = ([text[i:i + ngram] for i in range(len(text) - ngram + 1)]
             if len(text) >= ngram else [text])
    v = [0] * bits
    for gram, weight in Counter(grams).items():
        h = _feature_hash(gram)
        for i in range(bits):
            v[i] += weight if (h >> i) & 1 else -weight
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= (1 << i)
    return out


def _hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def _row_text(row: Dict[str, Any]) -> str:
    return ((row.get("title") or "") + " " + (row.get("summary") or "")).strip()


def _minus_hours(iso: str, hours: int) -> str:
    """available_at ISO 减 N 小时(桶窗下界);不可解析 → 返回极早哨兵(退化为全量候选,保守)。"""
    try:
        return (datetime.fromisoformat(str(iso)) - timedelta(hours=hours)).isoformat()
    except (TypeError, ValueError):
        return "0000-01-01T00:00:00"


def _candidate_tickers(raw: Any) -> set:
    if not raw:
        return set()
    try:
        v = json.loads(raw)
        return {str(x) for x in v} if isinstance(v, list) else set()
    except (TypeError, ValueError):
        return set()


def _resolve_line_id(conn: sqlite3.Connection, row: Dict[str, Any]) -> str:
    """事件线归并:同 event_type、桶窗内(available_at ≥ now-72h)、海明 ≤3 且 ticker 兼容
    的既有行 → 复用其 line_id(转载共享);否则新线 = 自身 event_id。

    ticker 兼容:新行有码 → 需与候选码相交(按 ticker 分桶);新行无码(newsradar/无码
    快讯)→ 需候选亦无码(按 event_type 分桶)。同批已插行经同连接同事务可见,亦纳入候选。"""
    new_hash = int(row["simhash"])
    new_tickers = set(row.get("tickers") or [])
    since = _minus_hours(row["available_at"], _BUCKET_HOURS)
    sql = ("SELECT line_id, simhash, tickers FROM events "
           "WHERE event_type = :et AND available_at >= :since "
           "ORDER BY available_at ASC, rowid ASC")
    for cand in conn.execute(sql, {"et": row["event_type"], "since": since}).fetchall():
        cand_hash = cand["simhash"]
        if cand_hash is None:
            continue
        if _hamming(new_hash, int(cand_hash)) > _HAMMING_MAX:
            continue
        cand_tickers = _candidate_tickers(cand["tickers"])
        if new_tickers:
            if not (new_tickers & cand_tickers):     # 有码需相交(同票桶)
                continue
        elif cand_tickers:                            # 无码需候选亦无码(event_type 桶)
            continue
        return cand["line_id"]                        # 命中最早候选 → 共享其线
    return row["event_id"]                            # 无命中 → 开新线


# ───────────────────────── 单事务入库 ─────────────────────────

def _ingest_rows(
    conn: sqlite3.Connection, mapped: List[Dict[str, Any]], *,
    run: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """逐行入库(单事务:任一行炸 → 全批回滚,绝不半写)。每行先算 SimHash 再归并事件线。
    返回 {fetched, inserted}。"""
    inserted = 0
    with conn:                                   # 成功 commit / 异常 rollback(不半写)
        for row in mapped:
            row["simhash"] = _simhash(_row_text(row))
            row["line_id"] = _resolve_line_id(conn, row)
            if store.insert_event(conn, row):
                inserted += 1
        if run is not None:
            audit = dict(run)
            audit.setdefault("fetched_count", len(mapped))
            audit["inserted_count"] = inserted
            store.record_ingest_run(conn, audit)
    return {"fetched": len(mapped), "inserted": inserted}


def _record_source_run(
    conn: sqlite3.Connection, *, source_id: str, observed_at: str,
    status: str, requested_asof: Any = None, fetched_count: int = 0,
    inserted_count: int = 0, error_kind: str = "", note: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Persist source health separately; an outage/empty result is never an event."""
    with conn:
        store.record_ingest_run(conn, {
            "source_id": source_id, "requested_asof": requested_asof,
            "observed_at": observed_at, "status": status,
            "fetched_count": fetched_count, "inserted_count": inserted_count,
            "error_kind": error_kind, "note": note, "metadata": dict(metadata or {}),
        })


# ───────────────────────── 默认 feed 出口(可注入)─────────────────────────

def _default_kuaixun_fetch(limit: int = 200) -> List[Dict[str, Any]]:
    from guanlan_v2.datafeed.kuaixun import fetch_kuaixun
    return fetch_kuaixun(limit=limit)


def _default_newsradar_read(**kwargs: Any) -> Dict[str, Any]:
    from guanlan_v2.datafeed.newsradar import read_radar_for_ingest
    return read_radar_for_ingest(
        sectors=kwargs.get("sectors"), days=kwargs.get("days", 7),
        per_sector_limit=kwargs.get("limit", 200),
    )


# ───────────────────────── 入库 adapter ─────────────────────────

def ingest_kuaixun(conn: sqlite3.Connection, *,
                   fetch: Optional[Callable[..., List[Dict[str, Any]]]] = None,
                   now: Optional[datetime] = None, limit: int = 200) -> Dict[str, Any]:
    """东财快讯入库:抓规范行 → 逐字段映射 → 单事务入库。源抛错向上传播(事务外零写)。"""
    fetch = fetch or _default_kuaixun_fetch
    try:
        rows = fetch(limit=limit) or []          # 抛错上传;空返 [] → 零行零错
    except Exception as exc:
        available_at = _availability_after_fetch(now)
        _record_source_run(
            conn, source_id="kuaixun", observed_at=available_at, status="failed",
            error_kind=type(exc).__name__, note=str(exc)[:500],
        )
        raise
    available_at = _availability_after_fetch(now)
    mapped = [_map_kuaixun_row(r, available_at) for r in rows if isinstance(r, dict)]
    return _ingest_rows(conn, mapped, run={
        "source_id": "kuaixun", "observed_at": available_at,
        "status": "ok" if mapped else "empty",
        "metadata": {"requested_limit": int(limit)},
    })


def ingest_newsradar(conn: sqlite3.Connection, *,
                     read: Optional[Callable[..., Dict[str, Any]]] = None,
                     now: Optional[datetime] = None,
                     sectors: Optional[List[str]] = None,
                     days: int = 7, limit: int = 500) -> Dict[str, Any]:
    """产业资讯雷达入库:逐赛道最多 `limit` 条 → 单事务入库。

    这里的 limit 是每赛道上限，不是展示流的全市场总上限；默认 500 配合每源 20 条
    边界可覆盖当前 106 源而不让 macro/tech 挤掉其余赛道。
    """
    read = read or _default_newsradar_read
    try:
        result = read(sectors=sectors, days=days, limit=limit) or {}
    except Exception as exc:
        available_at = _availability_after_fetch(now)
        _record_source_run(
            conn, source_id="newsradar", observed_at=available_at, status="failed",
            error_kind=type(exc).__name__, note=str(exc)[:500],
            metadata={"sectors": sectors or [], "days": days, "limit": limit},
        )
        raise
    available_at = _availability_after_fetch(
        now, result.get("retrieved_at"), result.get("pulled_at"),
    )
    # 双保险：新 NewsRadar 已把未来源时间放进 future_items；即使调用方注入了
    # 旧/异常信封，也绝不把明确标成 future_source_time 的行写进近期事件库。
    cutoff_ts = int((now or datetime.now()).timestamp())
    items = [item for item in (result.get("items") or [])
             if (not isinstance(item, dict) or
                 (item.get("time_bucket") != "future_source_time"
                  and int(item.get("ts") or 0) <= cutoff_ts))]
    source_asof = result.get("retrieved_at") or result.get("pulled_at") or available_at
    mapped = [_map_newsradar_row(it, available_at, source_asof=source_asof)
              for it in items if isinstance(it, dict)]
    failures = int(result.get("sources_failed") or 0)
    quality = str(result.get("source_quality") or "unknown")
    if mapped:
        run_status = "partial" if failures or quality == "degraded" else "ok"
    elif failures or (quality == "unknown" and result.get("note")):
        run_status = "failed"
    else:
        run_status = "empty"
    out = _ingest_rows(conn, mapped, run={
        "source_id": "newsradar", "observed_at": available_at,
        "requested_asof": (result.get("window") or {}).get("end"),
        "status": run_status,
        "note": str(result.get("note") or "")[:500],
        "metadata": {
            "source_quality": quality, "sources_failed": failures,
            "coverage_by_sector": result.get("coverage_by_sector") or {},
            "future_items_excluded": result.get("future_count", 0),
        },
    })
    out.update({
        "status": run_status,
        "sources": {"newsradar": {
            "status": run_status, "fetched": len(items),
            "normalized": len(mapped), "inserted": out.get("inserted", 0),
        }},
        "coverage_by_sector": result.get("coverage_by_sector") or {},
        "available_by_sector": result.get("available_by_sector") or {},
        "source_quality": result.get("source_quality") or "unknown",
        "sources_failed": result.get("sources_failed"),
        "future_items_excluded": result.get("future_count", 0),
        "selected_for_ingest": len(items),
        "total_available_recent": result.get("total_available_recent"),
        "truncated": bool(result.get("truncated")),
    })
    return out


def ingest_newsradar_snapshot(
    conn: sqlite3.Connection, snapshot: Mapping[str, Any], *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist the exact NewsRadar snapshot already fetched by a workspace.

    No second HTTP/cache query is made.  The workspace ``rows`` envelope is
    translated back to the existing ingest contract and retains its pulled-at,
    source-health and warning metadata.
    """
    payload = dict(snapshot or {})
    rows = payload.get("rows")
    if isinstance(rows, list):
        payload["items"] = rows
    payload.setdefault("items", [])
    raw_status = str(payload.get("status") or "").strip().lower()
    if "source_quality" not in payload:
        payload["source_quality"] = (
            "degraded" if raw_status in {"partial", "partial_empty", "stale", "error"}
            else "complete" if raw_status in {"ok", "empty"}
            else "unknown"
        )
    if "sources_failed" not in payload and raw_status in {"error", "failed"}:
        payload["sources_failed"] = 1
    query = payload.get("query") if isinstance(payload.get("query"), Mapping) else {}
    return ingest_newsradar(
        conn, read=lambda **_kwargs: payload, now=now,
        sectors=list(query.get("sectors") or []),
        days=int(query.get("days") or payload.get("days") or 7),
        limit=max(1, min(len(payload.get("items") or []) or 1, 500)),
    )


# ───────────────────── 统一实时文本/事件源 → 全局 events.db ─────────────────────

_LIVE_EVENT_SOURCE_IDS = frozenset({
    "cninfo_announcements", "ths_hot_reason", "em_limit_up_pool",
    "ths_limit_up_pool",
})
_MARKET_EVENT_SOURCE_IDS = (
    "ths_hot_reason", "em_limit_up_pool", "ths_limit_up_pool",
)
_LIVE_TRANSPORT_DEPENDENCY = (
    "guanlan_v2.datafeed.live_client -> external stocks probe runtime; "
    "provider requests are live HTTP, no stocks database/parquet is read"
)


def _canonical_query_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _session_event_time(date_value: Any, clock: Any = None) -> Optional[str]:
    day = _canonical_query_date(date_value)
    if not day:
        return None
    text = str(clock or "").strip()
    if re.fullmatch(r"\d{2}:\d{2}:\d{2}", text):
        return f"{day}T{text}"
    return f"{day}T00:00:00"


def _live_issuer_ids(item: Mapping[str, Any], raw: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    stock_codes = item.get("stock_codes")
    if isinstance(stock_codes, (list, tuple, set)):
        values.extend(stock_codes)
    elif stock_codes:
        values.append(stock_codes)
    if raw.get("code") or raw.get("secCode"):
        values.append(raw.get("code") or raw.get("secCode"))
    return sorted({normalize_issuer_id(value) for value in values
                   if normalize_issuer_id(value)})


def _live_provenance(
    envelope: Mapping[str, Any], item: Mapping[str, Any], *, derived_event_time: bool,
) -> Dict[str, Any]:
    return {
        "adapter": "events.ingest.live_client",
        "transport_dependency": _LIVE_TRANSPORT_DEPENDENCY,
        "source_status": envelope.get("status"),
        "provider": envelope.get("provider") or item.get("provider"),
        "actual_provider": envelope.get("actual_provider"),
        "schema_version": envelope.get("schema_version"),
        "fetched_ts": envelope.get("fetched_ts"),
        "pulled_at": envelope.get("pulled_at"),
        "requested_code": envelope.get("code"),
        "requested_date": envelope.get("date"),
        "cache_hit": envelope.get("cache_hit"),
        "network_used": envelope.get("network_used"),
        "pit_certified": item.get("pit_certified"),
        "capture_mode": item.get("capture_mode"),
        "time_source": item.get("time_source"),
        "time_precision": item.get("time_precision"),
        "event_time_derived_from_requested_session": derived_event_time,
    }


def _map_live_event_item(
    source_id: str, envelope: Mapping[str, Any], item: Mapping[str, Any],
    available_at: str,
) -> Dict[str, Any]:
    if source_id not in _LIVE_EVENT_SOURCE_IDS:
        raise ValueError(f"unsupported live event source: {source_id}")
    raw_value = item.get("raw")
    raw = dict(raw_value) if isinstance(raw_value, Mapping) else {}
    title = str(item.get("title") or raw.get("name") or "").strip()
    text = str(item.get("text") or raw.get("reason") or "").strip()
    issuers = _live_issuer_ids(item, raw)
    industries = [str(value).strip() for value in (item.get("industries") or [])
                  if str(value).strip()]
    source_asof = (
        envelope.get("fetched_ts") or envelope.get("pulled_at") or available_at
    )
    provider = envelope.get("actual_provider") or envelope.get("provider") or item.get("provider")
    derived_event_time = False

    if source_id == "cninfo_announcements":
        external_id = (
            raw.get("announcementId") or item.get("revision_id")
            or item.get("url") or title
        )
        event_time = item.get("publish_ts") or item.get("visible_ts")
        event_type = "announcement"
        context_type = "company_disclosure"
        context_ids = industries or ["a_share_disclosure"]
        stable = {
            "announcement_id": raw.get("announcementId"),
            "revision_id": item.get("revision_id"),
            "title": title,
            "url": item.get("url"),
            "announcement_type": raw.get("announcementType"),
            "publish_ts": event_time,
            "issuer_ids": issuers,
        }
    elif source_id == "ths_hot_reason":
        query_date = raw.get("date") or envelope.get("date")
        external_id = raw.get("id") or f"{query_date}:{','.join(issuers)}"
        event_time = _session_event_time(query_date) or item.get("visible_ts")
        event_type = "market_hot_reason"
        context_type = "market_theme"
        context_ids = industries or ["a_share_hot_reason"]
        stable = {
            "source_row_id": raw.get("id"), "date": query_date,
            "issuer_ids": issuers, "title": title, "reason": text,
        }
    else:
        query_date = envelope.get("date")
        first_clock = (
            raw.get("first_time") if source_id == "ths_limit_up_pool"
            else raw.get("first_seal")
        )
        event_time = _session_event_time(query_date, first_clock)
        derived_event_time = True
        external_id = f"{_canonical_query_date(query_date) or query_date}:{','.join(issuers)}"
        reason = text or str(raw.get("reason") or "").strip()
        event_type = "limit_up_reason" if reason else "limit_up"
        context_type = "limit_up_pool"
        context_ids = industries or ["a_share_limit_up"]
        stable = {
            "date": _canonical_query_date(query_date) or query_date,
            "issuer_ids": issuers, "title": title, "reason": reason,
            "industry": raw.get("industry"), "board_type": raw.get("board_type"),
            "limit_days": raw.get("limit_days"), "high_days": raw.get("high_days"),
            "first_time": first_clock, "zt_stat": raw.get("zt_stat"),
        }
        if not reason:
            text = " · ".join(str(value) for value in (
                raw.get("industry"), raw.get("zt_stat")
            ) if value not in (None, ""))

    if not issuers:
        raise ValueError(f"{source_id} event is missing issuer code")
    return normalized_event(
        source_id=source_id, event_type=event_type, available_at=available_at,
        source_asof=source_asof, event_time=event_time, external_id=external_id,
        title=title, summary=text, source=provider, source_ref=item.get("url"),
        issuer_ids=issuers, subject_kind="issuer", context_type=context_type,
        context_ids=context_ids, payload=dict(item), identity_payload=stable,
        provenance=_live_provenance(
            envelope, item, derived_event_time=derived_event_time,
        ),
    )


def _live_run_metadata(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "transport_dependency": _LIVE_TRANSPORT_DEPENDENCY,
        "provider": envelope.get("provider"),
        "actual_provider": envelope.get("actual_provider"),
        "schema_version": envelope.get("schema_version"),
        "cache_hit": envelope.get("cache_hit"),
        "network_used": envelope.get("network_used"),
        "requested_code": envelope.get("code"),
        "requested_date": envelope.get("date"),
    }


def ingest_live_envelopes(
    conn: sqlite3.Connection, envelopes: Iterable[Mapping[str, Any]], *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Normalize already-fetched live query envelopes on demand.

    This is the company/workspace integration seam: the caller can persist the
    exact result it already queried without a second provider request.  Only
    ``ok``/``stale`` envelopes with concrete items can create events.  Empty,
    planned, circuit, transport and schema failures write an audit run only.
    """
    envelope_rows = list(envelopes)
    # All envelopes have returned before normalization starts.  Never reuse a
    # scheduler's earlier decision time as proof that their contents existed.
    available_at = _availability_after_fetch(
        now,
        *(
            value
            for envelope in envelope_rows if isinstance(envelope, Mapping)
            for value in (envelope.get("fetched_ts"), envelope.get("pulled_at"))
        ),
    )
    source_results: Dict[str, Dict[str, Any]] = {}
    total_fetched = total_inserted = 0
    for raw_envelope in envelope_rows:
        if not isinstance(raw_envelope, Mapping):
            continue
        envelope = dict(raw_envelope)
        source_id = str(envelope.get("source") or "").strip().lower()
        result_key = source_id or "unknown"
        if envelope.get("code"):
            result_key = f"{result_key}:{normalize_issuer_id(envelope.get('code'))}"
        status = str(envelope.get("status") or "").strip().lower()
        items = envelope.get("items")
        requested_asof = _canonical_query_date(envelope.get("date"))
        metadata = _live_run_metadata(envelope)
        if source_id not in _LIVE_EVENT_SOURCE_IDS:
            status = "failed"
            note = f"unsupported live event source: {source_id or '<missing>'}"
            _record_source_run(
                conn, source_id=source_id or "unknown", observed_at=available_at,
                status="failed", requested_asof=requested_asof,
                error_kind="unsupported_source", note=note, metadata=metadata,
            )
            source_results[result_key] = {
                "status": "failed", "fetched": 0, "inserted": 0, "note": note,
            }
            continue
        if (
            envelope.get("ok") is not True
            or status not in {"ok", "stale"}
            or not isinstance(items, list)
        ):
            audited_status = "empty" if (
                envelope.get("ok") is True and status == "empty"
                and isinstance(items, list) and not items
            ) else "failed"
            note = str(envelope.get("note") or envelope.get("error") or status or
                       "live source returned no usable envelope")[:500]
            _record_source_run(
                conn, source_id=source_id, observed_at=available_at,
                status=audited_status, requested_asof=requested_asof,
                error_kind=str(envelope.get("error_kind") or status or "source_error"),
                note=note, metadata=metadata,
            )
            source_results[result_key] = {
                "status": audited_status, "fetched": 0, "inserted": 0, "note": note,
            }
            continue
        if not items:
            _record_source_run(
                conn, source_id=source_id, observed_at=available_at, status="empty",
                requested_asof=requested_asof, note=str(envelope.get("note") or "")[:500],
                metadata=metadata,
            )
            source_results[result_key] = {
                "status": "empty", "fetched": 0, "inserted": 0,
                "note": str(envelope.get("note") or ""),
            }
            continue

        mapped: List[Dict[str, Any]] = []
        mapping_errors: List[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                mapping_errors.append("item_not_object")
                continue
            try:
                mapped.append(_map_live_event_item(
                    source_id, envelope, item, available_at,
                ))
            except Exception as exc:  # deterministic schema quarantine
                mapping_errors.append(f"{type(exc).__name__}:{exc}"[:240])
        if not mapped:
            note = "all live rows failed normalization"
            _record_source_run(
                conn, source_id=source_id, observed_at=available_at,
                status="failed", requested_asof=requested_asof,
                fetched_count=len(items), error_kind="normalization_error",
                note=note, metadata={**metadata, "mapping_errors": mapping_errors[:20]},
            )
            source_results[result_key] = {
                "status": "failed", "fetched": len(items), "inserted": 0,
                "note": note, "mapping_errors": mapping_errors,
            }
            total_fetched += len(items)
            continue

        run_status = "partial" if mapping_errors else ("stale" if status == "stale" else "ok")
        result = _ingest_rows(conn, mapped, run={
            "source_id": source_id, "observed_at": available_at,
            "requested_asof": requested_asof, "status": run_status,
            "fetched_count": len(items),
            "note": (f"{len(mapping_errors)} rows quarantined" if mapping_errors else ""),
            "metadata": {**metadata, "mapping_errors": mapping_errors[:20]},
        })
        source_results[result_key] = {
            "status": run_status, "fetched": len(items),
            "normalized": len(mapped), "inserted": result["inserted"],
            "mapping_errors": mapping_errors,
        }
        total_fetched += len(items)
        total_inserted += result["inserted"]
    return {
        "fetched": total_fetched, "inserted": total_inserted,
        "sources": source_results,
        "transport_dependency": _LIVE_TRANSPORT_DEPENDENCY,
    }


def _default_live_probe_many(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from guanlan_v2.datafeed.live_client import probe_many
    return probe_many(requests, timeout=120)


def _latest_a_share_session(instant: datetime) -> str:
    """Use the configured authoritative calendar; never invent a weekday fallback."""
    try:
        from guanlan_v2.channels.trading_calendar import AShareTradingCalendar
        days = AShareTradingCalendar().trading_days(
            (instant - timedelta(days=40)).date(), instant.date(),
        )
    except Exception:  # calendar absence yields today's honest empty upstream query
        return instant.strftime("%Y%m%d")
    if not days:
        return instant.strftime("%Y%m%d")
    latest = max(days)
    # Before the opening auction the prior completed session remains the useful
    # market-event snapshot.  At/after 09:15 today's source can be queried.
    if latest == instant.date() and (instant.hour, instant.minute) < (9, 15):
        previous = [value for value in days if value < latest]
        if previous:
            latest = max(previous)
    return latest.strftime("%Y%m%d")


def ingest_live_market_events(
    conn: sqlite3.Connection, *,
    probe_many: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    now: Optional[datetime] = None, date: Optional[str] = None, limit: int = 300,
) -> Dict[str, Any]:
    """Pull market-wide hotspot/limit-up sources through the real live HTTP path."""
    instant = now or datetime.now()
    query_date = date or _latest_a_share_session(instant)
    requests = [
        {"source": source_id, "date": query_date, "limit": int(limit)}
        for source_id in _MARKET_EVENT_SOURCE_IDS
    ]
    try:
        envelopes = (probe_many or _default_live_probe_many)(requests)
    except Exception as exc:
        observed_at = _availability_after_fetch(now)
        for source_id in _MARKET_EVENT_SOURCE_IDS:
            _record_source_run(
                conn, source_id=source_id, observed_at=observed_at, status="failed",
                requested_asof=_canonical_query_date(query_date),
                error_kind=type(exc).__name__, note=str(exc)[:500],
                metadata={"transport_dependency": _LIVE_TRANSPORT_DEPENDENCY},
            )
        raise
    if not isinstance(envelopes, list) or len(envelopes) != len(requests):
        observed_at = _availability_after_fetch(now)
        for source_id in _MARKET_EVENT_SOURCE_IDS:
            _record_source_run(
                conn, source_id=source_id, observed_at=observed_at, status="failed",
                requested_asof=_canonical_query_date(query_date),
                error_kind="envelope_count_mismatch",
                note="live market probe returned a mismatched envelope count",
                metadata={"transport_dependency": _LIVE_TRANSPORT_DEPENDENCY},
            )
        raise RuntimeError("live market probe returned a mismatched envelope count")
    return ingest_live_envelopes(conn, envelopes, now=now)


def ingest_live_announcements(
    conn: sqlite3.Connection, codes: Sequence[str], *,
    probe_many: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    now: Optional[datetime] = None, limit: int = 30,
) -> Dict[str, Any]:
    """On-demand per-company announcement ingest; never scan the whole market."""
    issuers = sorted({normalize_issuer_id(code) for code in codes
                      if normalize_issuer_id(code)})
    requests = [
        {"source": "cninfo_announcements", "code": code, "limit": int(limit)}
        for code in issuers
    ]
    if not requests:
        return {"fetched": 0, "inserted": 0, "sources": {},
                "note": "no issuer codes requested"}
    try:
        envelopes = (probe_many or _default_live_probe_many)(requests)
    except Exception as exc:
        observed_at = _availability_after_fetch(now)
        for issuer in issuers:
            _record_source_run(
                conn, source_id="cninfo_announcements", observed_at=observed_at,
                status="failed", error_kind=type(exc).__name__, note=str(exc)[:500],
                metadata={"issuer": issuer,
                          "transport_dependency": _LIVE_TRANSPORT_DEPENDENCY},
            )
        raise
    if not isinstance(envelopes, list) or len(envelopes) != len(requests):
        observed_at = _availability_after_fetch(now)
        for issuer in issuers:
            _record_source_run(
                conn, source_id="cninfo_announcements", observed_at=observed_at,
                status="failed", error_kind="envelope_count_mismatch",
                note="announcement probe returned a mismatched envelope count",
                metadata={"issuer": issuer,
                          "transport_dependency": _LIVE_TRANSPORT_DEPENDENCY},
            )
        raise RuntimeError("announcement probe returned a mismatched envelope count")
    return ingest_live_envelopes(conn, envelopes, now=now)


# ───────────────────────── 调度 tick(三门)+ 宿主循环(Task 5)─────────────────────────

_ENV_INGEST_GATE = "GUANLAN_EVENTS_INGEST"    # 入库总闸(默认关)
_INGEST_CADENCE_MIN = 30                       # 30min 节奏对齐 newsradar TTL(1800s)
_EOD_HHMM = (15, 30)                           # 收盘后 EOD 兜底窗(≥15:30)
_LOOP_INTERVAL_S = 300                         # 宿主循环基础节拍(节奏门再节流到 30min)

# 模块级 tick 状态(进程内;重启后 last=None → 首拍即入库,节奏门防连打)。
_TICK_STATE: Dict[str, Any] = {
    "last_ingest_ts": None, "eod_date": None, "last_result": None,
}


def _tick_due(now: datetime) -> tuple:
    """节奏 + EOD 兜底判定 → (due, is_eod_window)。
    due = 距上次入库 ≥30min(或从未入库) 或 进入 EOD 窗且今日未兜底。"""
    last = _TICK_STATE.get("last_ingest_ts")
    today = now.date().isoformat()
    is_eod_window = (now.hour, now.minute) >= _EOD_HHMM
    eod_pending = is_eod_window and _TICK_STATE.get("eod_date") != today
    cadence_due = last is None or (now - last).total_seconds() >= _INGEST_CADENCE_MIN * 60
    return (cadence_due or eod_pending), is_eod_window


def _default_tick_ingest(now: datetime) -> Dict[str, Any]:
    """Run every source independently so one outage cannot starve the others."""
    conn = store.connect()
    try:
        store.init_db(conn)
        results: Dict[str, Any] = {}

        def _attempt(name: str, operation: Callable[[], Dict[str, Any]]) -> None:
            try:
                results[name] = {"status": "completed", "result": operation()}
            except Exception as exc:  # each adapter already wrote its failed run
                results[name] = {
                    "status": "failed", "error_kind": type(exc).__name__,
                    "note": str(exc)[:500],
                }

        # ``now`` is only the scheduler decision time.  Each adapter stamps its
        # own post-fetch wall clock so PIT availability cannot predate I/O.
        _attempt("kuaixun", lambda: ingest_kuaixun(conn))
        _attempt("newsradar", lambda: ingest_newsradar(conn))
        _attempt("live_market_events", lambda: ingest_live_market_events(conn))

        # Announcements are per-code and therefore strictly bounded/on-demand.
        # The scheduler only polls an explicitly configured active issuer set;
        # an empty env never degrades into a whole-market scan.
        issuer_codes = [
            value.strip() for value in
            os.environ.get("GUANLAN_EVENTS_ISSUER_CODES", "").split(",")
            if value.strip()
        ]
        if issuer_codes:
            _attempt(
                "live_announcements",
                lambda: ingest_live_announcements(conn, issuer_codes),
            )
        else:
            results["live_announcements"] = {
                "status": "not_requested", "reason": "no configured issuer codes",
            }
        return results
    finally:
        conn.close()


def maybe_ingest_tick(now: Optional[datetime] = None, *,
                      do_ingest: Optional[Callable[[datetime], None]] = None) -> bool:
    """入库调度钩子(照 autonomy.runtime.maybe_enqueue_daily_review 模式,**自吞异常绝不
    拖垮宿主 tick 循环**)。三门全过才入库,返回 True=本拍已入库:

    ①env 总闸 `GUANLAN_EVENTS_INGEST=="1"`(默认关);
    ②30min 节奏(距上次入库 <30min 且非 EOD 兜底 → 跳过,对齐 newsradar TTL);
    ③EOD 兜底(收盘后每日至少入库一次,即便节奏未到)。

    ``now`` 只决定调度节奏；各 adapter 在上游返回后另取真实墙钟，并不得早于
    provider fetch/pull receipt。入库失败在此吞掉且**不更新 last_ingest_ts**，
    下拍可重试，绝不因一次失败静默停摆。"""
    try:
        import os
        if os.environ.get(_ENV_INGEST_GATE) != "1":
            return False
        now = now or datetime.now()
        due, is_eod_window = _tick_due(now)
        if not due:
            return False
        result = (do_ingest or _default_tick_ingest)(now)
        _TICK_STATE["last_result"] = result
        _TICK_STATE["last_ingest_ts"] = now                # 仅成功才占节奏
        if is_eod_window:
            _TICK_STATE["eod_date"] = now.date().isoformat()
        return True
    except Exception:  # noqa: BLE001 — 入库失败绝不拖垮宿主 tick 循环(诚实:失败不占节奏)
        return False


async def run_loop(interval_s: int = _LOOP_INTERVAL_S) -> None:
    """常驻循环(`GUANLAN_EVENTS_INGEST=1` 时由 server lifespan 起):每拍 to_thread 调
    maybe_ingest_tick(取数/写盘全在工作线程,严禁堵事件循环);节奏门把真入库节流到 30min。
    异常只记不退出;server 停机 CancelledError 干净退出。"""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(maybe_ingest_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 单拍故障绝不杀循环
            pass
        await asyncio.sleep(max(30, int(interval_s or _LOOP_INTERVAL_S)))


__all__ = [
    "ingest_kuaixun", "ingest_live_announcements", "ingest_live_envelopes",
    "ingest_live_market_events", "ingest_newsradar", "ingest_newsradar_snapshot",
    "maybe_ingest_tick", "run_loop",
]
