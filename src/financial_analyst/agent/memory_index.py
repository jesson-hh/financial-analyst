"""SQLite FTS5 full-text index over memories/**/*.md.

Each row = one markdown file (file-level chunk granularity).
Columns: agent (str), filename (str), content (text, FTS5 indexed).
Persisted to db_path. Rebuildable from filesystem.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _check_fts5(conn: sqlite3.Connection) -> None:
    """Raise RuntimeError if SQLite was compiled without FTS5."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "SQLite FTS5 extension is not available in this build. "
            "FTS5 requires SQLite >= 3.9 compiled with FTS5 support. "
            f"Your SQLite version: {sqlite3.sqlite_version}. "
            f"Original error: {exc}"
        ) from exc


class MemoryIndex:
    """SQLite FTS5 full-text index over memories/**/*.md.

    Each row = one markdown file (file-level chunk granularity).
    Columns: agent (str), filename (str), content (text, FTS5 indexed).
    Persisted to db_path. Rebuildable from filesystem.

    The ``_shared`` directory is treated as a regular agent name
    (``agent="_shared"``), so agent-filtered queries work on it.

    Args:
        memory_root: Root directory containing per-agent subdirectories
            (e.g. ``memories/``).
        db_path: Path for the persisted SQLite database file.
    """

    _CREATE_FTS = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
        "agent UNINDEXED, "
        "filename UNINDEXED, "
        "content, "
        'tokenize="unicode61 remove_diacritics 2"'
        ")"
    )
    _CREATE_META = (
        "CREATE TABLE IF NOT EXISTS memory_meta ("
        "agent TEXT NOT NULL, "
        "filename TEXT NOT NULL, "
        "mtime REAL NOT NULL, "
        "indexed_at REAL NOT NULL, "
        "PRIMARY KEY (agent, filename)"
        ")"
    )

    def __init__(self, memory_root: Path, db_path: Path) -> None:
        self.memory_root = Path(memory_root)
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            _check_fts5(self._conn)
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._conn
        assert conn is not None
        conn.execute(self._CREATE_FTS)
        conn.execute(self._CREATE_META)
        conn.commit()

    def _iter_md_files(self):
        """Yield (agent_name, md_path) for every *.md under memory_root."""
        for agent_dir in sorted(self.memory_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            for md_file in sorted(agent_dir.glob("*.md")):
                yield agent_name, md_file

    def _index_file(
        self,
        conn: sqlite3.Connection,
        agent: str,
        md_path: Path,
        now: float,
    ) -> None:
        """Insert or replace one file in both fts and meta tables."""
        content = md_path.read_text(encoding="utf-8")
        filename = md_path.name
        mtime = md_path.stat().st_mtime

        # Remove old FTS row (if any) then re-insert
        conn.execute(
            "DELETE FROM memory_fts WHERE agent = ? AND filename = ?",
            (agent, filename),
        )
        conn.execute(
            "INSERT INTO memory_fts(agent, filename, content) VALUES (?, ?, ?)",
            (agent, filename, content),
        )
        conn.execute(
            "INSERT OR REPLACE INTO memory_meta(agent, filename, mtime, indexed_at) "
            "VALUES (?, ?, ?, ?)",
            (agent, filename, mtime, now),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rebuild(self) -> int:
        """Drop + recreate FTS5 table from filesystem. Returns row count."""
        conn = self._get_conn()
        conn.execute("DROP TABLE IF EXISTS memory_fts")
        conn.execute("DROP TABLE IF EXISTS memory_meta")
        conn.commit()
        # Re-create schema from scratch
        conn.execute(self._CREATE_FTS)
        conn.execute(self._CREATE_META)
        conn.commit()

        now = time.time()
        count = 0
        for agent, md_path in self._iter_md_files():
            self._index_file(conn, agent, md_path, now)
            count += 1
        conn.commit()
        return count

    def update_changed(self) -> int:
        """Incremental: re-index files whose mtime > last_indexed_at.

        Also indexes brand-new files not yet present in memory_meta.
        Returns count of updated/added files.
        """
        conn = self._get_conn()
        updated = 0
        now = time.time()

        # Build a set of (agent, filename) -> indexed_at from meta table
        indexed: Dict[tuple, float] = {
            (row["agent"], row["filename"]): row["indexed_at"]
            for row in conn.execute("SELECT agent, filename, indexed_at FROM memory_meta")
        }

        for agent, md_path in self._iter_md_files():
            key = (agent, md_path.name)
            mtime = md_path.stat().st_mtime
            last_indexed = indexed.get(key)
            if last_indexed is None or mtime > last_indexed:
                self._index_file(conn, agent, md_path, now)
                updated += 1

        if updated:
            conn.commit()
        return updated

    @staticmethod
    def _to_prefix_query(query: str) -> str:
        """Convert a plain query string to FTS5 prefix-match form.

        Each whitespace-delimited term gets a trailing ``*`` so that
        partial CJK tokens (e.g. "游资" matching "游资博弈") and partial
        ASCII tokens work via FTS5 prefix search.

        Terms that already end with ``*`` or contain FTS5 operators
        (quotes, parentheses, ``AND``/``OR``/``NOT``) are passed through
        unchanged so callers can still issue raw FTS5 expressions.
        """
        # If the query contains FTS5 operators, pass through as-is
        if any(op in query for op in ('"', '(', 'AND', 'OR', 'NOT')):
            return query
        terms = query.split()
        return " ".join(t if t.endswith("*") else t + "*" for t in terms)

    def search(
        self,
        query: str,
        agent: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """FTS5 MATCH against content. Optionally filter by agent.

        Each term in *query* is automatically converted to a prefix match
        (trailing ``*``) so that partial CJK words and partial ASCII tokens
        are found correctly with the ``unicode61`` tokenizer.

        Returns list of dicts with keys: agent, filename, content, rank,
        sorted by relevance (most relevant first).
        """
        conn = self._get_conn()
        fts_query = self._to_prefix_query(query)

        if agent is not None:
            sql = (
                "SELECT agent, filename, content, rank "
                "FROM memory_fts "
                "WHERE memory_fts MATCH ? AND agent = ? "
                "ORDER BY rank "
                "LIMIT ?"
            )
            rows = conn.execute(sql, (fts_query, agent, top_k)).fetchall()
        else:
            sql = (
                "SELECT agent, filename, content, rank "
                "FROM memory_fts "
                "WHERE memory_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?"
            )
            rows = conn.execute(sql, (fts_query, top_k)).fetchall()

        return [
            {
                "agent": row["agent"],
                "filename": row["filename"],
                "content": row["content"],
                "rank": row["rank"],
            }
            for row in rows
        ]

    def stats(self) -> Dict[str, Any]:
        """Per-agent file count + total bytes."""
        conn = self._get_conn()

        per_agent: Dict[str, int] = {}
        per_agent_bytes: Dict[str, int] = {}
        total_files = 0
        total_bytes = 0

        rows = conn.execute(
            "SELECT agent, filename, content FROM memory_fts"
        ).fetchall()
        for row in rows:
            agent_name = row["agent"]
            n = len(row["content"].encode("utf-8"))
            per_agent[agent_name] = per_agent.get(agent_name, 0) + 1
            per_agent_bytes[agent_name] = per_agent_bytes.get(agent_name, 0) + n
            total_files += 1
            total_bytes += n

        return {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "per_agent": per_agent,
            "per_agent_bytes": per_agent_bytes,
        }
