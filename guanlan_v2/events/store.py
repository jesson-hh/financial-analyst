# -*- coding: utf-8 -*-
"""events.store —— schema / PIT 读原语(小 phase 乙 Task 1;Task 4 追加 jieba FTS 检索面)。

设计红线:
- **PIT 谓词冻结为单点常量** `PIT_PREDICATE`,读侧(read_events)与检索侧(search_events,
  Task 4)共用同一行,绝不各写各的(前视温床)。谓词语义:
  `available_at <= :asof AND (invalidated_at IS NULL OR invalidated_at > :asof)`。
  `available_at` = 入库落盘时刻(R16,保守但真);`event_time` = 首发/披露时刻(另存,
  回测敏感性分析可显式选用,绝不默认)。
- `invalidated_at` 本 phase 仅保留**列 + 读取语义**(三态:NULL / 早于 asof / 晚于 asof);
  失效流(Graphiti 式写入)明确属第二步,不实现。
- **available_at NOT NULL**:本系统真正可知时刻绝不缺;缺则约束炸(诚实)。
- WAL 模式:高并发读 + append-only 写不互锁。
- **单一存储(R15)**:本表即新闻归档;月度语义 = `substr(available_at,1,7)` 派生查询,
  不做物理分库/jsonl 轮转(超 ~1GB 再引 events-YYYYMM.db + ATTACH,列为升级项)。

Phase 8 预留注记(§9-5):目录装配 phase 的 Lane C text worker 载荷届时入**同一张 events
表**——以 additive 列(或 `payload_json` 列)后补,**PIT 谓词与既有列零改**;本 phase
**不预建任何 text-worker 列**,仅在此落字预留语义。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ───────────────────────── 路径 ─────────────────────────

_ENV_DB_PATH = "GUANLAN_EVENTS_DB"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = _REPO_ROOT / "var" / "events" / "events.db"


def db_path(path: Optional[Union[str, Path]] = None) -> Path:
    """事件库路径解析:显式参数 > env `GUANLAN_EVENTS_DB` > 默认 `var/events/events.db`。

    测试一律注入 tmp(参数或 env),绝不触碰真 `var/`。"""
    if path is not None:
        return Path(path)
    env = os.environ.get(_ENV_DB_PATH)
    if env:
        return Path(env)
    return _DEFAULT_DB_PATH


# ───────────────────────── schema ─────────────────────────

# 冻结 PIT 谓词(读/检索单点复用;named param `:asof`)。改此常量即改全库 PIT 语义。
PIT_PREDICATE = "available_at <= :asof AND (invalidated_at IS NULL OR invalidated_at > :asof)"

# events 表列(顺序即插入列序)。text-worker 载荷届时以 additive 列后补,不在此预建。
_COLUMNS = (
    "event_id",       # 确定性哈希(source + source_ref/title + event_time);PRIMARY KEY,幂等
    "event_type",     # kuaixun / newsradar:<sector> / ...(v1 不做语义分类)
    "tickers",        # json 数组(qlib 码列表);newsradar 行为 []
    "title",
    "summary",
    "source",         # 人读源名(东财快讯 / RSS 源名)
    "source_ref",     # url / 外链(newsradar 有,kuaixun 无则空)
    "event_time",     # 首发/披露时刻(源声称,格式随源:kuaixun 16 位 / newsradar ISO)
    "available_at",   # 入库落盘时刻(R16,NOT NULL,PIT 裁决列)
    "invalidated_at", # 失效时刻(v1 只读语义;默认 NULL)
    "simhash",        # 64 位 SimHash(TEXT 存十进制串,避免 sqlite 有符号溢出)
    "line_id",        # 事件线桶 id(转载共享,不丢证据;新线默认 = 自身 event_id)
    "ingested_from",  # 入库 adapter 名(kuaixun / newsradar)
)

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,
    tickers        TEXT,
    title          TEXT,
    summary        TEXT,
    source         TEXT,
    source_ref     TEXT,
    event_time     TEXT,
    available_at   TEXT NOT NULL,
    invalidated_at TEXT,
    simhash        TEXT,
    line_id        TEXT,
    ingested_from  TEXT
)
"""

# 月度语义 + PIT 检索走 available_at;line 归并查询走 (event_type, available_at)。
_CREATE_IDX_AVAIL = "CREATE INDEX IF NOT EXISTS idx_events_available_at ON events(available_at)"
_CREATE_IDX_TYPE = "CREATE INDEX IF NOT EXISTS idx_events_type_avail ON events(event_type, available_at)"
_CREATE_IDX_LINE = "CREATE INDEX IF NOT EXISTS idx_events_line ON events(line_id)"

# FTS5 索引表(Task 4,R16):jieba 预分词后的 tokens 写入 `tokens` 列,event_id 关联主表。
# 用**手工同步的独立 FTS5 表**(非 external-content):external-content 会以 unicode61
# 重新分词原始列 → 抹掉 jieba 预分词,故我们自管索引列写入(见模块 docstring 决策)。
_CREATE_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts "
               "USING fts5(tokens, event_id UNINDEXED, tokenize='unicode61')")


def connect(path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """打开(必要时建父目录)事件库连接:WAL 模式 + Row 工厂。"""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """幂等建表 + 索引 + FTS(重复调用不炸不重建不丢行,靠 IF NOT EXISTS)。"""
    conn.execute(_CREATE_EVENTS)
    conn.execute(_CREATE_IDX_AVAIL)
    conn.execute(_CREATE_IDX_TYPE)
    conn.execute(_CREATE_IDX_LINE)
    conn.execute(_CREATE_FTS)
    conn.commit()


# ───────────────────────── 分词(jieba,可注入降级)─────────────────────────

def _jieba_tokens(text: str) -> str:
    """jieba 切词 → 空格连接(供 FTS unicode61 按空格切成子词)。

    jieba 不可得(未装/坏)→ **抛 ImportError**,由 search_events 捕获走 LIKE 降级。
    测试可 monkeypatch 本函数模拟 jieba 坏,强制走降级档(不靠卸载依赖)。"""
    import jieba  # 局部 import:缺则抛 ImportError,降级路径接住
    return " ".join(w for w in jieba.cut(text or "") if w.strip())


def _fts_index(conn: sqlite3.Connection, event_id: str,
               title: Optional[str], summary: Optional[str]) -> None:
    """入库同事务写 FTS 索引:title+summary 经 jieba 预分词写入。jieba 不可得时静默跳过
    索引(检索侧仍可 LIKE 降级兜底,证据行已在主表,不丢)。"""
    text = ((title or "") + " " + (summary or "")).strip()
    try:
        tokens = _jieba_tokens(text)
    except Exception:  # noqa: BLE001 — jieba 不可得:跳过索引,检索走 LIKE 降级
        return
    conn.execute("INSERT INTO events_fts (tokens, event_id) VALUES (?, ?)", (tokens, event_id))


# ───────────────────────── 写原语 ─────────────────────────

def _encode_tickers(tickers: Any) -> Optional[str]:
    if tickers is None:
        return None
    if isinstance(tickers, (list, tuple)):
        return json.dumps([str(t) for t in tickers], ensure_ascii=False)
    return str(tickers)


def insert_event(conn: sqlite3.Connection, row: Dict[str, Any]) -> bool:
    """插入一行事件(幂等:同 `event_id` `INSERT OR IGNORE` 不重复)。

    返回 True=真插入 / False=已存在被忽略。**不 commit**(调用方管事务,保证单事务不半写)。

    必填:`event_id`、`event_type`、`available_at`(NOT NULL,缺则 KeyError/约束炸)。
    `line_id` 缺省 = 自身 `event_id`(新事件线);转载由 ingest 层归并共享。"""
    values = {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "tickers": _encode_tickers(row.get("tickers")),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "source": row.get("source"),
        "source_ref": row.get("source_ref"),
        "event_time": row.get("event_time"),
        "available_at": row["available_at"],
        "invalidated_at": row.get("invalidated_at"),
        "simhash": None if row.get("simhash") is None else str(row["simhash"]),
        "line_id": row.get("line_id") or row["event_id"],
        "ingested_from": row.get("ingested_from"),
    }
    sql = ("INSERT OR IGNORE INTO events (" + ",".join(_COLUMNS) + ") VALUES ("
           + ",".join(":" + c for c in _COLUMNS) + ")")
    cur = conn.execute(sql, values)
    inserted = cur.rowcount == 1
    if inserted:                                 # 仅真插入才写 FTS(重入不重复索引)
        _fts_index(conn, values["event_id"], values["title"], values["summary"])
    return inserted


# ───────────────────────── PIT 读原语 ─────────────────────────

def _require_asof(asof: Any) -> None:
    if not asof:
        raise ValueError("events 检索必须显式传 asof(PIT);静默 now = 前视温床,拒绝")


def read_events(conn: sqlite3.Connection, asof: str, *,
                event_type: Optional[str] = None, limit: int = 200) -> List[sqlite3.Row]:
    """PIT 读:复用冻结谓词,取 asof 时点可见的事件(available_at 倒序)。asof 必填缺则 raise。"""
    _require_asof(asof)
    sql = f"SELECT * FROM events WHERE {PIT_PREDICATE}"
    params: Dict[str, Any] = {"asof": asof}
    if event_type:
        sql += " AND event_type = :event_type"
        params["event_type"] = event_type
    sql += " ORDER BY available_at DESC, rowid DESC LIMIT :limit"
    params["limit"] = int(limit)
    return list(conn.execute(sql, params).fetchall())


# ───────────────────────── 检索面(FTS + asof 必填 + LIKE 降级)─────────────────────────

def _decode_tickers(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["tickers"] = _decode_tickers(d.get("tickers"))
    return d


class _FtsUnavailable(Exception):
    """jieba 分词不可得,检索走 LIKE 降级的内部信号。"""


def _jieba_query(query: str) -> str:
    """查询串 jieba 切词 → FTS5 OR 连接;jieba 不可得抛 `_FtsUnavailable`。"""
    try:
        toks = [t for t in _jieba_tokens(query or "").split(" ") if t]
    except Exception as exc:  # noqa: BLE001 — jieba 未装/坏 → 降级信号
        raise _FtsUnavailable(str(exc)) from exc
    if not toks:
        return ""
    # 每个子词加引号防 FTS 语法字符;OR 连接(任一子词命中即召回)。
    return " OR ".join('"' + t.replace('"', '') + '"' for t in toks)


def _search_fts(conn: sqlite3.Connection, fts_query: str, asof: str,
                limit: int) -> List[Dict[str, Any]]:
    if not fts_query:
        return []
    sql = (f"SELECT e.* FROM events e JOIN events_fts f ON f.event_id = e.event_id "
           f"WHERE f.tokens MATCH :q AND {PIT_PREDICATE} "
           f"ORDER BY e.available_at DESC, e.rowid DESC LIMIT :limit")
    rows = conn.execute(sql, {"q": fts_query, "asof": asof, "limit": int(limit)}).fetchall()
    return [_row_to_dict(r) for r in rows]


def _search_like(conn: sqlite3.Connection, query: str, asof: str,
                 limit: int) -> List[Dict[str, Any]]:
    like = f"%{query or ''}%"
    sql = (f"SELECT * FROM events WHERE (title LIKE :like OR summary LIKE :like) "
           f"AND {PIT_PREDICATE} ORDER BY available_at DESC, rowid DESC LIMIT :limit")
    rows = conn.execute(sql, {"like": like, "asof": asof, "limit": int(limit)}).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_events(query: str, asof: str, limit: int = 50, *,
                  conn: Optional[sqlite3.Connection] = None,
                  path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """中文全文检索,PIT 强制:`asof` 必填缺则 raise(静默 now = 前视温床)。

    优先 jieba 预分词命中 FTS5(分词后子词可查);jieba 不可得 → LIKE 降级 + payload 标
    `fts:"unavailable"`(诚实显形,绝不静默装好)。复用 Task 1 冻结 PIT 谓词。

    返回 `{"items": [...], "n": N, "fts": "ok"|"unavailable", "asof": ..., "query": ...}`。
    `conn` 或 `path` 注入(测试);皆缺 → 默认库。"""
    _require_asof(asof)
    own = False
    if conn is None:
        conn = connect(path)
        own = True
    try:
        try:
            items = _search_fts(conn, _jieba_query(query), asof, limit)
            fts_status = "ok"
        except _FtsUnavailable:
            items = _search_like(conn, query, asof, limit)
            fts_status = "unavailable"
        return {"items": items, "n": len(items), "fts": fts_status,
                "asof": asof, "query": query}
    finally:
        if own:
            conn.close()


__all__ = [
    "PIT_PREDICATE", "db_path", "connect", "init_db",
    "insert_event", "read_events", "search_events",
]
