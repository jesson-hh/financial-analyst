"""Local SQLite news DB. Stores news/lhb/holders/social/hot/earnings + FTS5 full-text index.

Schema:
    news          — eastmoney_kuaixun / sinafinance_news / etc
    lhb           — Dragon-Tiger list (龙虎榜) daily entries
    holders       — top 10 tradable shareholders (十大流通股东) quarterly snapshots
    news_fts      — FTS5 virtual table over news.title || news.content
    social_posts  — xueqiu comments / weibo (retail sentiment)
    hot_stocks    — xueqiu/sinafinance heat rankings
    earnings_dates — upcoming earnings release calendar
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

CREATE TABLE IF NOT EXISTS social_posts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    code TEXT NOT NULL,
    ts TEXT NOT NULL,
    author TEXT,
    content TEXT,
    likes INTEGER,
    comments_count INTEGER,
    retweet_count INTEGER,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS social_code_idx ON social_posts(code);
CREATE INDEX IF NOT EXISTS social_ts_idx ON social_posts(ts);

CREATE TABLE IF NOT EXISTS hot_stocks (
    snapshot_date TEXT NOT NULL,
    source TEXT NOT NULL,
    rank INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    price REAL,
    change_percent REAL,
    heat INTEGER,
    PRIMARY KEY (snapshot_date, source, rank)
);
CREATE INDEX IF NOT EXISTS hot_code_idx ON hot_stocks(code);

CREATE TABLE IF NOT EXISTS earnings_dates (
    code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    quarter TEXT,
    source TEXT,
    captured_at TEXT,
    PRIMARY KEY (code, report_date)
);

-- v1.7.1: xueqiu personal account snapshots
CREATE TABLE IF NOT EXISTS user_watchlists (
    snapshot_date TEXT NOT NULL,
    group_pid TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    price REAL,
    change_percent REAL,
    url TEXT,
    raw TEXT,
    PRIMARY KEY (snapshot_date, group_pid, symbol)
);
CREATE INDEX IF NOT EXISTS watchlist_symbol_idx ON user_watchlists(symbol);

CREATE TABLE IF NOT EXISTS user_groups (
    snapshot_date TEXT NOT NULL,
    pid TEXT NOT NULL,
    name TEXT,
    count INTEGER,
    PRIMARY KEY (snapshot_date, pid)
);

CREATE TABLE IF NOT EXISTS fund_snapshots (
    snapshot_date TEXT NOT NULL,
    account_id TEXT NOT NULL,
    account_name TEXT,
    total_assets REAL,
    available_cash REAL,
    daily_gain REAL,
    hold_gain REAL,
    raw TEXT,
    PRIMARY KEY (snapshot_date, account_id)
);

CREATE TABLE IF NOT EXISTS fund_holdings (
    snapshot_date TEXT NOT NULL,
    account_name TEXT NOT NULL,
    fd_code TEXT NOT NULL,
    fd_name TEXT,
    market_value REAL,
    volume REAL,
    daily_gain REAL,
    hold_gain REAL,
    hold_gain_rate REAL,
    market_percent REAL,
    raw TEXT,
    PRIMARY KEY (snapshot_date, account_name, fd_code)
);

-- v1.7.2: Tonghuashun (ths-extra opencli plugin) tables
CREATE TABLE IF NOT EXISTS iwencai_results (
    snapshot_ts TEXT NOT NULL,
    question TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    columns TEXT,
    cells TEXT,
    PRIMARY KEY (snapshot_ts, question, row_index)
);

CREATE TABLE IF NOT EXISTS ths_fund_flow (
    snapshot_ts TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'gegu',
    code TEXT NOT NULL,
    name TEXT,
    price TEXT,
    change_pct TEXT,
    turnover_pct TEXT,
    inflow TEXT,
    outflow TEXT,
    main_net TEXT,
    total_amount TEXT,
    num_stocks TEXT,
    leader TEXT,
    trade_time TEXT,
    direction TEXT,
    volume TEXT,
    url TEXT,
    raw_cells TEXT,
    PRIMARY KEY (snapshot_ts, target, code, name)
);
CREATE INDEX IF NOT EXISTS ths_fund_flow_code_idx ON ths_fund_flow(code);
-- ths_fund_flow_target_idx is created inside _migrate after ensuring
-- the target column exists (legacy DBs predate v1.7.3 schema).

CREATE TABLE IF NOT EXISTS ths_concept_boards (
    snapshot_ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    board_code TEXT NOT NULL,
    board_name TEXT,
    release_date TEXT,
    num_stocks TEXT,
    change_pct TEXT,
    board_url TEXT,
    raw_cells TEXT,
    PRIMARY KEY (snapshot_ts, mode, board_code, board_name)
);
"""


class NewsDB:
    """SQLite-backed news / dragon-tiger list (龙虎榜) / shareholders store with FTS5 over news."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns to existing tables for forward-compat. SQLite's
        ``CREATE TABLE IF NOT EXISTS`` doesn't alter on schema change,
        so we ALTER TABLE in place for any new columns we expect."""
        # v1.7.3: ths_fund_flow gained target + num_stocks + leader +
        # trade_time + direction + volume + url + raw_cells. Add any
        # missing columns to the live table.
        wanted = [
            ("target", "TEXT NOT NULL DEFAULT 'gegu'"),
            ("num_stocks", "TEXT"),
            ("leader", "TEXT"),
            ("trade_time", "TEXT"),
            ("direction", "TEXT"),
            ("volume", "TEXT"),
            ("url", "TEXT"),
            ("raw_cells", "TEXT"),
        ]
        cur = self.conn.execute("PRAGMA table_info(ths_fund_flow)")
        existing = {row[1] for row in cur.fetchall()}
        for col, typ in wanted:
            if col not in existing:
                try:
                    self.conn.execute(f"ALTER TABLE ths_fund_flow ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass
        # Now safe to create the target index (column guaranteed present)
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS ths_fund_flow_target_idx ON ths_fund_flow(target)"
            )
        except sqlite3.OperationalError:
            pass

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

    # ---- social_posts ----

    def upsert_social_posts(self, code: str, items: Iterable[Dict[str, Any]], source: str) -> int:
        """Insert/update social media posts for a stock.

        The opencli xueqiu/comments adapter returns items shaped like
        ``{author, text, likes, replies, retweets, created_at, url}`` — no
        explicit ``id`` field. ``url`` is the only stable per-post identifier
        (the trailing number is the post ID on xueqiu). Without it every row
        in a batch collapses to the same ``source::code::`` key under
        ``INSERT OR REPLACE`` and only the last item survives.
        """
        n = 0
        for it in items:
            post_id = str(
                it.get("id")
                or it.get("post_id")
                or it.get("url")          # xueqiu: unique per-post URL
                or it.get("created_at")   # xueqiu: ISO timestamp
                or it.get("ts")
                or it.get("time")
                or ""
            )
            row_id = f"{source}::{code}::{post_id}"
            self.conn.execute(
                "INSERT OR REPLACE INTO social_posts (id, source, code, ts, author, content, "
                "likes, comments_count, retweet_count, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id, source, code.upper(),
                    str(it.get("ts") or it.get("time") or it.get("created_at") or ""),
                    str(it.get("author") or it.get("user") or ""),
                    str(it.get("content") or it.get("text") or ""),
                    it.get("likes") or it.get("like_count") or 0,
                    it.get("comments_count") or it.get("comments")
                    or it.get("reply_count") or it.get("replies") or 0,
                    it.get("retweet_count") or it.get("retweets") or 0,
                    json.dumps(it, ensure_ascii=False, default=str),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_social_posts(self, code: str, since_days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent social posts for a stock."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        cur = self.conn.execute(
            "SELECT * FROM social_posts WHERE code = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (code.upper(), cutoff, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    # ---- hot_stocks ----

    def upsert_hot_stocks(self, items: Iterable[Dict[str, Any]], source: str,
                           snapshot_date: Optional[str] = None) -> int:
        snapshot = snapshot_date or datetime.now().strftime("%Y-%m-%d")
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO hot_stocks (snapshot_date, source, rank, code, name, "
                "price, change_percent, heat) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot, source, it.get("rank"),
                    str(it.get("symbol") or it.get("code") or ""),
                    it.get("name"),
                    it.get("price"),
                    it.get("changePercent") or it.get("change_percent"),
                    it.get("heat") or it.get("hotness") or 0,
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_hot_stocks(self, source: Optional[str] = None,
                          latest_only: bool = True, limit: int = 50) -> List[Dict[str, Any]]:
        if latest_only:
            sql = """SELECT * FROM hot_stocks
                     WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM hot_stocks)"""
            params: list = []
            if source:
                sql += " AND source = ?"
                params.append(source)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
        else:
            sql = "SELECT * FROM hot_stocks"
            params = []
            if source:
                sql += " WHERE source = ?"
                params.append(source)
            sql += " ORDER BY snapshot_date DESC, rank LIMIT ?"
            params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ---- earnings_dates ----

    def upsert_earnings_dates(self, items: Iterable[Dict[str, Any]], source: str) -> int:
        captured = datetime.now().isoformat(timespec="seconds")
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO earnings_dates (code, report_date, quarter, source, captured_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(it.get("code") or it.get("symbol") or "").upper(),
                    str(it.get("report_date") or it.get("date") or ""),
                    str(it.get("quarter") or ""),
                    source,
                    captured,
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_earnings_dates(self, code: Optional[str] = None,
                              upcoming_days: int = 90) -> List[Dict[str, Any]]:
        from datetime import timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        cutoff = (datetime.now() + timedelta(days=upcoming_days)).strftime("%Y-%m-%d")
        params: list = [today, cutoff]
        sql = "SELECT * FROM earnings_dates WHERE report_date >= ? AND report_date <= ?"
        if code:
            sql += " AND code = ?"
            params.append(code.upper())
        sql += " ORDER BY report_date ASC"
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ---- user_watchlists / user_groups (v1.7.1 — xueqiu personal) ----

    def upsert_watchlist(self, items: Iterable[Dict[str, Any]], group_pid: str = "-1",
                          snapshot_date: Optional[str] = None) -> int:
        snapshot = snapshot_date or datetime.now().strftime("%Y-%m-%d")
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO user_watchlists (snapshot_date, group_pid, symbol, "
                "name, price, change_percent, url, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot, group_pid,
                    str(it.get("symbol") or it.get("code") or "").upper(),
                    it.get("name"),
                    it.get("price"),
                    it.get("changePercent") or it.get("change_percent"),
                    it.get("url"),
                    json.dumps(it, ensure_ascii=False, default=str),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def upsert_groups(self, items: Iterable[Dict[str, Any]],
                       snapshot_date: Optional[str] = None) -> int:
        snapshot = snapshot_date or datetime.now().strftime("%Y-%m-%d")
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO user_groups (snapshot_date, pid, name, count) "
                "VALUES (?, ?, ?, ?)",
                (snapshot, str(it.get("pid")), it.get("name"), it.get("count") or 0),
            )
            n += 1
        self.conn.commit()
        return n

    def query_watchlist(self, group_pid: str = "-1", snapshot_date: Optional[str] = None
                         ) -> List[Dict[str, Any]]:
        """Latest watchlist for a group. Defaults to today's snapshot."""
        if snapshot_date:
            cur = self.conn.execute(
                "SELECT * FROM user_watchlists WHERE snapshot_date=? AND group_pid=? ORDER BY symbol",
                (snapshot_date, group_pid),
            )
        else:
            # Latest available snapshot
            cur = self.conn.execute(
                "SELECT * FROM user_watchlists WHERE group_pid=? AND snapshot_date="
                "(SELECT MAX(snapshot_date) FROM user_watchlists WHERE group_pid=?) ORDER BY symbol",
                (group_pid, group_pid),
            )
        return [dict(row) for row in cur.fetchall()]

    def query_groups(self, snapshot_date: Optional[str] = None) -> List[Dict[str, Any]]:
        if snapshot_date:
            cur = self.conn.execute(
                "SELECT * FROM user_groups WHERE snapshot_date=? ORDER BY pid",
                (snapshot_date,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM user_groups WHERE snapshot_date="
                "(SELECT MAX(snapshot_date) FROM user_groups) ORDER BY pid",
            )
        return [dict(row) for row in cur.fetchall()]

    # ---- fund_snapshots / fund_holdings (v1.7.1 — danjuanfunds personal) ----

    def upsert_fund_snapshot(self, accounts: Iterable[Dict[str, Any]],
                              snapshot_date: Optional[str] = None) -> int:
        snapshot = snapshot_date or datetime.now().strftime("%Y-%m-%d")
        n = 0
        for it in accounts:
            self.conn.execute(
                "INSERT OR REPLACE INTO fund_snapshots (snapshot_date, account_id, account_name, "
                "total_assets, available_cash, daily_gain, hold_gain, raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot,
                    str(it.get("accountId") or it.get("account_id") or it.get("accountName") or "default"),
                    it.get("accountName") or it.get("account_name"),
                    it.get("totalAssets") or it.get("total_assets"),
                    it.get("availableCash") or it.get("available_cash"),
                    it.get("dailyGain") or it.get("daily_gain"),
                    it.get("holdGain") or it.get("hold_gain"),
                    json.dumps(it, ensure_ascii=False, default=str),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def upsert_fund_holdings(self, items: Iterable[Dict[str, Any]],
                              snapshot_date: Optional[str] = None) -> int:
        snapshot = snapshot_date or datetime.now().strftime("%Y-%m-%d")
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO fund_holdings (snapshot_date, account_name, fd_code, fd_name, "
                "market_value, volume, daily_gain, hold_gain, hold_gain_rate, market_percent, raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot,
                    it.get("accountName") or it.get("account_name") or "default",
                    str(it.get("fdCode") or it.get("fd_code") or ""),
                    it.get("fdName") or it.get("fd_name"),
                    it.get("marketValue") or it.get("market_value"),
                    it.get("volume"),
                    it.get("dailyGain") or it.get("daily_gain"),
                    it.get("holdGain") or it.get("hold_gain"),
                    it.get("holdGainRate") or it.get("hold_gain_rate"),
                    it.get("marketPercent") or it.get("market_percent"),
                    json.dumps(it, ensure_ascii=False, default=str),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_fund_snapshot(self, snapshot_date: Optional[str] = None) -> List[Dict[str, Any]]:
        if snapshot_date:
            cur = self.conn.execute(
                "SELECT * FROM fund_snapshots WHERE snapshot_date=?", (snapshot_date,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM fund_snapshots WHERE snapshot_date="
                "(SELECT MAX(snapshot_date) FROM fund_snapshots)",
            )
        return [dict(row) for row in cur.fetchall()]

    def query_fund_holdings(self, account_name: str = "",
                             snapshot_date: Optional[str] = None) -> List[Dict[str, Any]]:
        if snapshot_date is None:
            row = self.conn.execute(
                "SELECT MAX(snapshot_date) FROM fund_holdings"
            ).fetchone()
            snapshot_date = row[0] if row and row[0] else None
        if snapshot_date is None:
            return []
        if account_name:
            cur = self.conn.execute(
                "SELECT * FROM fund_holdings WHERE snapshot_date=? AND account_name=? ORDER BY market_value DESC",
                (snapshot_date, account_name),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM fund_holdings WHERE snapshot_date=? ORDER BY market_value DESC",
                (snapshot_date,),
            )
        return [dict(row) for row in cur.fetchall()]

    # ---- ths-extra: iwencai_results / ths_fund_flow / ths_concept_boards (v1.7.2) ----

    def upsert_iwencai(self, items: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO iwencai_results "
                "(snapshot_ts, question, row_index, columns, cells) VALUES (?, ?, ?, ?, ?)",
                (
                    it.get("snapshot_ts") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    it.get("question"),
                    it.get("row_index", 0),
                    it.get("columns") or "",
                    it.get("cells") or "",
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_iwencai(self, question: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the latest snapshot for a question (most recent timestamp)."""
        cur = self.conn.execute(
            "SELECT * FROM iwencai_results WHERE question=? AND snapshot_ts="
            "(SELECT MAX(snapshot_ts) FROM iwencai_results WHERE question=?) "
            "ORDER BY row_index LIMIT ?",
            (question, question, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def upsert_ths_fund_flow(self, items: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO ths_fund_flow "
                "(snapshot_ts, target, code, name, price, change_pct, turnover_pct, "
                "inflow, outflow, main_net, total_amount, num_stocks, leader, "
                "trade_time, direction, volume, url, raw_cells) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    it.get("snapshot_ts") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    it.get("target") or "gegu",
                    it.get("code") or "",
                    it.get("name") or "",
                    it.get("price"), it.get("change_pct"), it.get("turnover_pct"),
                    it.get("inflow"), it.get("outflow"),
                    it.get("main_net"), it.get("total_amount"),
                    it.get("num_stocks"), it.get("leader"),
                    it.get("trade_time"), it.get("direction"),
                    it.get("volume"), it.get("url"), it.get("raw_cells"),
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_ths_fund_flow(self, target: str = "gegu",
                             limit: int = 30) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM ths_fund_flow WHERE target=? AND snapshot_ts="
            "(SELECT MAX(snapshot_ts) FROM ths_fund_flow WHERE target=?) "
            "ORDER BY rowid LIMIT ?",
            (target, target, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def query_ths_fund_flow_history(self, code: str, target: str = "gegu",
                                     limit: int = 10) -> List[Dict[str, Any]]:
        """All snapshots of one code on a leaderboard, newest first.
        Used by the cross-day fund-flow diff tool."""
        cur = self.conn.execute(
            "SELECT * FROM ths_fund_flow WHERE code=? AND target=? "
            "ORDER BY snapshot_ts DESC LIMIT ?",
            (code, target, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def upsert_ths_concept_boards(self, items: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for it in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO ths_concept_boards "
                "(snapshot_ts, mode, board_code, board_name, release_date, "
                "num_stocks, change_pct, board_url, raw_cells) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    it.get("snapshot_ts") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    it.get("mode", "new"),
                    it.get("board_code") or "",
                    it.get("board_name") or "",
                    it.get("release_date") or "",
                    it.get("num_stocks") or "",
                    it.get("change_pct") or "",
                    it.get("board_url") or "",
                    it.get("raw_cells") or "",
                ),
            )
            n += 1
        self.conn.commit()
        return n

    def query_ths_concept_boards(self, mode: str = "new",
                                  limit: int = 30) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM ths_concept_boards WHERE mode=? AND snapshot_ts="
            "(SELECT MAX(snapshot_ts) FROM ths_concept_boards WHERE mode=?) "
            "ORDER BY rowid LIMIT ?",
            (mode, mode, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def stats(self) -> Dict[str, int]:
        return {
            "news": self.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "lhb": self.conn.execute("SELECT COUNT(*) FROM lhb").fetchone()[0],
            "holders": self.conn.execute("SELECT COUNT(*) FROM holders").fetchone()[0],
            "social_posts": self.conn.execute("SELECT COUNT(*) FROM social_posts").fetchone()[0],
            "hot_stocks": self.conn.execute("SELECT COUNT(*) FROM hot_stocks").fetchone()[0],
            "earnings_dates": self.conn.execute("SELECT COUNT(*) FROM earnings_dates").fetchone()[0],
            "user_watchlists": self.conn.execute("SELECT COUNT(*) FROM user_watchlists").fetchone()[0],
            "user_groups": self.conn.execute("SELECT COUNT(*) FROM user_groups").fetchone()[0],
            "fund_snapshots": self.conn.execute("SELECT COUNT(*) FROM fund_snapshots").fetchone()[0],
            "fund_holdings": self.conn.execute("SELECT COUNT(*) FROM fund_holdings").fetchone()[0],
            "iwencai_results": self.conn.execute("SELECT COUNT(*) FROM iwencai_results").fetchone()[0],
            "ths_fund_flow": self.conn.execute("SELECT COUNT(*) FROM ths_fund_flow").fetchone()[0],
            "ths_concept_boards": self.conn.execute("SELECT COUNT(*) FROM ths_concept_boards").fetchone()[0],
        }
