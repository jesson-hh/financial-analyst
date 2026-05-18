"""Local SQLite news DB. Stores news/lhb/holders + FTS5 full-text index.

Schema:
    news       — eastmoney_kuaixun / sinafinance_news / etc
    lhb        — 龙虎榜 daily entries
    holders    — 十大流通股东 quarterly snapshots
    news_fts   — FTS5 virtual table over news.title || news.content
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_DB_PATH = Path.home() / ".financial-analyst" / "data" / "news.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    ts TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    url TEXT,
    related_codes TEXT,
    sentiment TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS news_ts_idx ON news(ts);
CREATE INDEX IF NOT EXISTS news_source_idx ON news(source);

CREATE TABLE IF NOT EXISTS lhb (
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    close_price REAL,
    change_rate REAL,
    board_amt REAL,
    buy_amt REAL,
    sell_amt REAL,
    net_amt REAL,
    turnover REAL,
    deal_ratio REAL,
    market TEXT,
    reason TEXT,
    PRIMARY KEY (trade_date, code, reason)
);
CREATE INDEX IF NOT EXISTS lhb_code_idx ON lhb(code);

CREATE TABLE IF NOT EXISTS holders (
    code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    holder_name TEXT NOT NULL,
    hold_num INTEGER,
    float_ratio REAL,
    change TEXT,
    PRIMARY KEY (code, report_date, rank)
);
CREATE INDEX IF NOT EXISTS holders_code_idx ON holders(code);

CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(
    title, content, related_codes,
    tokenize="unicode61 remove_diacritics 2"
);
"""


class NewsDB:
    """SQLite-backed news / 龙虎榜 / 股东 store with FTS5 over news."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- news ----

    def upsert_news(self, items: Iterable[Dict[str, Any]], source: str) -> int:
        """Insert news rows. Uses (source, ts, title) hash as id to dedupe.

        Each item should have at minimum: time, title (or content). Optionally summary, stocks, url.
        """
        n = 0
        for it in items:
            ts = str(it.get("time") or it.get("ts") or "")
            title = str(it.get("title") or "")
            content = str(it.get("summary") or it.get("content") or "")
            url = str(it.get("url") or "")
            related = str(it.get("stocks") or it.get("related_codes") or "")
            row_id = f"{source}::{ts}::{title[:120]}"
            self.conn.execute(
                "INSERT OR REPLACE INTO news (id, source, ts, title, content, url, related_codes, raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, source, ts, title, content, url, related,
                 json.dumps(it, ensure_ascii=False, default=str)),
            )
            # FTS5: delete existing row for this rowid, then re-insert
            self.conn.execute("DELETE FROM news_fts WHERE rowid IN (SELECT rowid FROM news WHERE id=?)", (row_id,))
            self.conn.execute(
                "INSERT INTO news_fts (rowid, title, content, related_codes) "
                "SELECT rowid, title, content, related_codes FROM news WHERE id=?",
                (row_id,),
            )
            n += 1
        self.conn.commit()
        return n

    def query_news(
        self, code: Optional[str] = None, since_days: int = 7,
        source: Optional[str] = None, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query recent news for a stock (or all stocks if code=None)."""
        from datetime import timedelta
        cutoff_dt = datetime.now() - timedelta(days=since_days)
        cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

        conditions = ["ts >= ?"]
        params: list = [cutoff]
        if code:
            short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
            conditions.append("(related_codes LIKE ? OR content LIKE ?)")
            params.extend([f"%{short}%", f"%{short}%"])
        if source:
            conditions.append("source = ?")
            params.append(source)
        where = " AND ".join(conditions)
        sql = f"SELECT * FROM news WHERE {where} ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def search_news(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """FTS5 search over news content."""
        # auto prefix tokens for CJK
        tokens = [t + "*" if t else "" for t in query.split()]
        fts_query = " ".join(t for t in tokens if t)
        if not fts_query:
            return []
        sql = """
        SELECT news.* FROM news_fts
        JOIN news ON news.rowid = news_fts.rowid
        WHERE news_fts MATCH ? ORDER BY news.ts DESC LIMIT ?
        """
        try:
            cur = self.conn.execute(sql, (fts_query, limit))
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in cur.fetchall()]

    # ---- lhb ----

    def upsert_lhb(self, items: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO lhb (trade_date, code, name, close_price, change_rate, "
                "board_amt, buy_amt, sell_amt, net_amt, turnover, deal_ratio, market, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    it.get("tradeDate"),
                    str(it.get("code", "")),
                    it.get("name"),
                    it.get("closePrice"),
                    it.get("changeRate"),
                    it.get("boardAmt"),
                    it.get("buyAmt"),
                    it.get("sellAmt"),
                    it.get("netAmt"),
                    it.get("turnover"),
                    it.get("dealRatio"),
                    it.get("market"),
                    it.get("reason"),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_lhb(self, code: str = "", since_days: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        params: list = [cutoff]
        sql = "SELECT * FROM lhb WHERE trade_date >= ?"
        if code:
            short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
            sql += " AND code = ?"
            params.append(short)
        sql += " ORDER BY trade_date DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ---- holders ----

    def upsert_holders(self, code: str, items: Iterable[Dict[str, Any]]) -> int:
        n = 0
        short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO holders (code, report_date, rank, holder_name, hold_num, "
                "float_ratio, change) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    short,
                    it.get("reportDate"),
                    it.get("rank"),
                    it.get("name"),
                    it.get("holdNum"),
                    it.get("floatRatio"),
                    it.get("change"),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_holders(self, code: str, latest_only: bool = True) -> List[Dict[str, Any]]:
        short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        if latest_only:
            cur = self.conn.execute(
                "SELECT * FROM holders WHERE code = ? AND report_date = "
                "(SELECT MAX(report_date) FROM holders WHERE code = ?) ORDER BY rank",
                (short, short),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM holders WHERE code = ? ORDER BY report_date DESC, rank",
                (short,),
            )
        return [dict(row) for row in cur.fetchall()]

    def stats(self) -> Dict[str, int]:
        return {
            "news": self.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "lhb": self.conn.execute("SELECT COUNT(*) FROM lhb").fetchone()[0],
            "holders": self.conn.execute("SELECT COUNT(*) FROM holders").fetchone()[0],
        }
