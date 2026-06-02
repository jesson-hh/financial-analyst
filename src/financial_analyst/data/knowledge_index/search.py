"""High-level ``KnowledgeIndex`` — one-stop API for callers.

Composes ``ChromaStore`` + ``BgeEmbedder`` (default) + ``KnowledgeIndexer`` so
callers don't have to wire them together. Defaults resolve from ``DataPaths``
so the same code works on the dev box (``G:/stocks/strategy``) and a fresh
machine (env vars override).

Typical use:

    >>> from financial_analyst.data.knowledge_index import KnowledgeIndex
    >>> idx = KnowledgeIndex()
    >>> idx.build()                            # incremental
    >>> for r in idx.search("反转因子 失效场景"):
    ...     print(r.score, r.source, r.section)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from .indexer import BuildStats, KnowledgeIndexer
from .store import ChromaStore


@dataclass
class ChunkResult:
    """One search hit. ``score`` is L2 distance from chroma — lower = closer.
    Use ``sorted(..., key=lambda r: r.score)`` for ascending order (chroma
    already returns sorted, this is just documentation)."""

    text: str
    source: str
    section: str
    score: float
    chunk_id: str = ""


class KnowledgeIndex:
    """Facade over chunker + embedder + store + indexer.

    Construction is **lazy** — the chroma store is only opened when needed
    (first build / search / stats call). This lets test code construct an
    instance and only pay the cost when the test actually exercises it.

    Parameters
    ----------
    strategy_root
        Directory containing the strategy MD tree. Defaults to
        ``DataPaths.strategy_root`` (typically ``G:/stocks/strategy/``).
    index_root
        Where the chroma persistent store lives. Defaults to
        ``DataPaths.knowledge_index_root / "chroma"``.
    embedder
        Any object with ``encode(texts) -> np.ndarray``. Defaults to
        ``BgeEmbedder()`` (BAAI/bge-large-zh-v1.5, lazy-loaded on first
        encode). Tests pass ``StubEmbedder()`` to avoid the model download.
    collection_name
        Chroma collection name. Defaults to ``strategy_knowledge``.
    """

    def __init__(
        self,
        strategy_root: Optional[Path] = None,
        index_root: Optional[Path] = None,
        embedder: Optional[Any] = None,
        collection_name: str = ChromaStore.DEFAULT_COLLECTION,
    ) -> None:
        self.collection_name = collection_name
        self._strategy_root = Path(strategy_root) if strategy_root is not None else None
        self._index_root = Path(index_root) if index_root is not None else None
        self._embedder_override = embedder

        # Resolved lazily on first use.
        self._store: Optional[ChromaStore] = None
        self._embedder: Optional[Any] = None
        self._indexer: Optional[KnowledgeIndexer] = None

    # ──────────────── lazy resolvers ────────────────

    def _resolve_paths(self) -> None:
        if self._strategy_root is not None and self._index_root is not None:
            return
        # Import locally so tests that mock DataPaths still work.
        from financial_analyst.data.paths import get_data_paths

        paths = get_data_paths()
        if self._strategy_root is None:
            self._strategy_root = paths.strategy_root
        if self._index_root is None:
            self._index_root = paths.knowledge_index_root / "chroma"

    @property
    def strategy_root(self) -> Path:
        self._resolve_paths()
        assert self._strategy_root is not None
        return self._strategy_root

    @property
    def index_root(self) -> Path:
        self._resolve_paths()
        assert self._index_root is not None
        return self._index_root

    @property
    def embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        if self._embedder_override is not None:
            self._embedder = self._embedder_override
            return self._embedder
        # Default: BgeEmbedder (lazy-loads the actual model on first encode).
        from .embedder import BgeEmbedder

        self._embedder = BgeEmbedder()
        return self._embedder

    @property
    def store(self) -> ChromaStore:
        if self._store is None:
            self._store = ChromaStore(
                root=self.index_root,
                collection_name=self.collection_name,
            )
        return self._store

    @property
    def indexer(self) -> KnowledgeIndexer:
        if self._indexer is None:
            self._indexer = KnowledgeIndexer(
                strategy_root=self.strategy_root,
                store=self.store,
                embedder=self.embedder,
            )
        return self._indexer

    # ──────────────── public API ────────────────

    def build(self, force: bool = False) -> BuildStats:
        """Refresh the index. See ``KnowledgeIndexer.build`` for semantics."""
        return self.indexer.build(force=force)

    def search(self, query: str, k: int = 5) -> List[ChunkResult]:
        """Top-K semantic search by natural-language query.

        Returns ``[]`` for an empty query or empty store."""
        if not query or not query.strip():
            return []
        if self.store.count() == 0:
            return []
        vec = self.embedder.encode([query])
        if vec.shape[0] == 0:
            return []
        raw = self.store.query(vec[0], n_results=k)
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        dists = (raw.get("distances") or [[]])[0]
        out: List[ChunkResult] = []
        for i, cid in enumerate(ids):
            meta = metas[i] if i < len(metas) and metas[i] else {}
            out.append(
                ChunkResult(
                    text=docs[i] if i < len(docs) else "",
                    source=str(meta.get("source_file", "")),
                    section=str(meta.get("section_h2", "")),
                    score=float(dists[i]) if i < len(dists) and dists[i] is not None else float("nan"),
                    chunk_id=str(cid),
                )
            )
        return out

    def stats(self) -> dict:
        """Lightweight stats: collection count + paths."""
        return {
            "strategy_root": str(self.strategy_root),
            "index_root": str(self.index_root),
            "collection_name": self.collection_name,
            "n_chunks": int(self.store.count()),
        }


__all__ = ["KnowledgeIndex", "ChunkResult"]
