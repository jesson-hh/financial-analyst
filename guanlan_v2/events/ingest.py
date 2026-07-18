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
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import sqlite3

from guanlan_v2.events import store

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
    return {
        "event_id": _event_id("kuaixun", title, event_time),
        "event_type": "kuaixun",
        "tickers": tickers,
        "title": title,
        "summary": summary,
        "source": KUAIXUN_SOURCE,
        "source_ref": None,
        "event_time": event_time,
        "available_at": available_at,
        "ingested_from": "kuaixun",
    }


def _map_newsradar_row(item: Dict[str, Any], available_at: str) -> Dict[str, Any]:
    """newsradar 缓存行 → events 行。event_type=newsradar:<sector>(无 sector 则裸
    newsradar),tickers=[](产业源无个股码),source_ref=url,event_time=ts(epoch→ISO)。"""
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    url = str(item.get("url") or "").strip()
    sector = str(item.get("sector") or "").strip()
    source = str(item.get("source") or "").strip()
    event_time = _epoch_to_iso(item.get("ts"))
    return {
        "event_id": _event_id("newsradar", url or title, event_time or title),
        "event_type": f"newsradar:{sector}" if sector else "newsradar",
        "tickers": [],
        "title": title,
        "summary": summary,
        "source": source or None,
        "source_ref": url or None,
        "event_time": event_time,
        "available_at": available_at,
        "ingested_from": "newsradar",
    }


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

def _ingest_rows(conn: sqlite3.Connection, mapped: List[Dict[str, Any]]) -> Dict[str, Any]:
    """逐行入库(单事务:任一行炸 → 全批回滚,绝不半写)。每行先算 SimHash 再归并事件线。
    返回 {fetched, inserted}。"""
    inserted = 0
    with conn:                                   # 成功 commit / 异常 rollback(不半写)
        for row in mapped:
            row["simhash"] = _simhash(_row_text(row))
            row["line_id"] = _resolve_line_id(conn, row)
            if store.insert_event(conn, row):
                inserted += 1
    return {"fetched": len(mapped), "inserted": inserted}


# ───────────────────────── 默认 feed 出口(可注入)─────────────────────────

def _default_kuaixun_fetch(limit: int = 200) -> List[Dict[str, Any]]:
    from guanlan_v2.datafeed.kuaixun import fetch_kuaixun
    return fetch_kuaixun(limit=limit)


def _default_newsradar_read(**kwargs: Any) -> Dict[str, Any]:
    from guanlan_v2.datafeed.newsradar import read_radar
    return read_radar(**kwargs)


# ───────────────────────── 入库 adapter ─────────────────────────

def ingest_kuaixun(conn: sqlite3.Connection, *,
                   fetch: Optional[Callable[..., List[Dict[str, Any]]]] = None,
                   now: Optional[datetime] = None, limit: int = 200) -> Dict[str, Any]:
    """东财快讯入库:抓规范行 → 逐字段映射 → 单事务入库。源抛错向上传播(事务外零写)。"""
    fetch = fetch or _default_kuaixun_fetch
    rows = fetch(limit=limit) or []              # 抛错上传;空返 [] → 零行零错
    available_at = _now_iso(now)
    mapped = [_map_kuaixun_row(r, available_at) for r in rows if isinstance(r, dict)]
    return _ingest_rows(conn, mapped)


def ingest_newsradar(conn: sqlite3.Connection, *,
                     read: Optional[Callable[..., Dict[str, Any]]] = None,
                     now: Optional[datetime] = None,
                     sectors: Optional[List[str]] = None,
                     days: int = 7, limit: int = 200) -> Dict[str, Any]:
    """产业资讯雷达入库:读缓存聚合 → 逐字段映射 → 单事务入库。源抛错向上传播(事务外零写)。"""
    read = read or _default_newsradar_read
    result = read(sectors=sectors, days=days, limit=limit) or {}
    items = result.get("items") or []
    available_at = _now_iso(now)
    mapped = [_map_newsradar_row(it, available_at) for it in items if isinstance(it, dict)]
    return _ingest_rows(conn, mapped)


# ───────────────────────── 调度 tick(三门)+ 宿主循环(Task 5)─────────────────────────

_ENV_INGEST_GATE = "GUANLAN_EVENTS_INGEST"    # 入库总闸(默认关)
_INGEST_CADENCE_MIN = 30                       # 30min 节奏对齐 newsradar TTL(1800s)
_EOD_HHMM = (15, 30)                           # 收盘后 EOD 兜底窗(≥15:30)
_LOOP_INTERVAL_S = 300                         # 宿主循环基础节拍(节奏门再节流到 30min)

# 模块级 tick 状态(进程内;重启后 last=None → 首拍即入库,节奏门防连打)。
_TICK_STATE: Dict[str, Any] = {"last_ingest_ts": None, "eod_date": None}


def _tick_due(now: datetime) -> tuple:
    """节奏 + EOD 兜底判定 → (due, is_eod_window)。
    due = 距上次入库 ≥30min(或从未入库) 或 进入 EOD 窗且今日未兜底。"""
    last = _TICK_STATE.get("last_ingest_ts")
    today = now.date().isoformat()
    is_eod_window = (now.hour, now.minute) >= _EOD_HHMM
    eod_pending = is_eod_window and _TICK_STATE.get("eod_date") != today
    cadence_due = last is None or (now - last).total_seconds() >= _INGEST_CADENCE_MIN * 60
    return (cadence_due or eod_pending), is_eod_window


def _default_tick_ingest(now: datetime) -> None:
    """生产入库:开真库(WAL)+ 建表 + 两 feed 各入一批。tick 缺省绑此;测试注入桩替。"""
    conn = store.connect()
    try:
        store.init_db(conn)
        ingest_kuaixun(conn, now=now)
        ingest_newsradar(conn, now=now)
    finally:
        conn.close()


def maybe_ingest_tick(now: Optional[datetime] = None, *,
                      do_ingest: Optional[Callable[[datetime], None]] = None) -> bool:
    """入库调度钩子(照 autonomy.runtime.maybe_enqueue_daily_review 模式,**自吞异常绝不
    拖垮宿主 tick 循环**)。三门全过才入库,返回 True=本拍已入库:

    ①env 总闸 `GUANLAN_EVENTS_INGEST=="1"`(默认关);
    ②30min 节奏(距上次入库 <30min 且非 EOD 兜底 → 跳过,对齐 newsradar TTL);
    ③EOD 兜底(收盘后每日至少入库一次,即便节奏未到)。

    落盘时刻 = `now`(墙钟,注入用于测试);入库失败(源抛错经单事务回滚后上传)在此吞掉,
    **不更新 last_ingest_ts** → 下拍可重试,绝不因一次失败静默停摆。"""
    try:
        import os
        if os.environ.get(_ENV_INGEST_GATE) != "1":
            return False
        now = now or datetime.now()
        due, is_eod_window = _tick_due(now)
        if not due:
            return False
        (do_ingest or _default_tick_ingest)(now)
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


__all__ = ["ingest_kuaixun", "ingest_newsradar", "maybe_ingest_tick", "run_loop"]
