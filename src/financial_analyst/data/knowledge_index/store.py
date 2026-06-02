"""Persistent vector store — wraps chromadb's PersistentClient.

We *always* pass pre-computed embeddings on upsert. Chroma will happily fetch
a default embedding model from its remote service if you omit them; we
disable that path entirely (offline-first, no surprise network calls, no
mismatched dim from a model swap).

Persistence is on-disk under ``root``. Re-opening a fresh ``ChromaStore``
pointed at the same root sees the prior data automatically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


class ChromaStore:
    """Thin wrapper over ``chromadb.PersistentClient`` for one collection."""

    DEFAULT_COLLECTION = "strategy_knowledge"

    def __init__(
        self,
        root: Path,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        self.root = Path(root)
        self.collection_name = collection_name
        self.root.mkdir(parents=True, exist_ok=True)

        # Lazy import so test setups that mock the import path still work
        # and so module import doesn't trigger chromadb's startup cost.
        import chromadb
        from chromadb.config import Settings

        # anonymized_telemetry=False — we don't ping their CDN on each open.
        self._client = chromadb.PersistentClient(
            path=str(self.root),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        # We deliberately pass embedding_function=None and feed our own
        # embeddings on every upsert. Chroma will otherwise try to download
        # an embedding model on first use.
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=None,
        )

    # ──────────────── basic ops ────────────────

    def upsert(
        self,
        ids: List[str],
        embeddings: np.ndarray,
        metadatas: List[Dict[str, Any]],
        documents: List[str],
    ) -> None:
        """Insert-or-update a batch of vectors + metadata + documents.

        ``embeddings`` must be a 2-D numpy array with shape ``(n, dim)``.
        We convert to a list-of-lists (chroma's expected input format) so the
        caller can hand us a numpy array straight from the embedder.
        """
        if len(ids) == 0:
            return
        if not isinstance(embeddings, np.ndarray):
            raise TypeError(
                f"ChromaStore.upsert: embeddings must be a numpy array, got {type(embeddings)}"
            )
        if embeddings.ndim != 2:
            raise ValueError(
                f"ChromaStore.upsert: embeddings must be 2-D (n, dim); got shape {embeddings.shape}"
            )
        if embeddings.shape[0] != len(ids):
            raise ValueError(
                f"ChromaStore.upsert: embeddings.shape[0]={embeddings.shape[0]} != "
                f"len(ids)={len(ids)}"
            )
        if len(metadatas) != len(ids) or len(documents) != len(ids):
            raise ValueError(
                "ChromaStore.upsert: ids/metadatas/documents must be same length"
            )
        # Chroma accepts list-of-lists for embeddings; convert once.
        emb_list = embeddings.tolist()
        self._collection.upsert(
            ids=ids,
            embeddings=emb_list,
            metadatas=metadatas,
            documents=documents,
        )

    def query(
        self,
        query_embedding: np.ndarray,
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """Top-N nearest-neighbour search by query vector.

        Returns chroma's raw response dict: keys ``ids``, ``documents``,
        ``metadatas``, ``distances`` (each is a list-of-lists, one inner list
        per query — we only pass one query so caller should index ``[0]``).
        """
        if not isinstance(query_embedding, np.ndarray):
            raise TypeError(
                f"ChromaStore.query: query_embedding must be a numpy array, got {type(query_embedding)}"
            )
        # Accept (dim,) or (1, dim) — normalise to a list-of-lists with one row.
        if query_embedding.ndim == 1:
            q = [query_embedding.tolist()]
        elif query_embedding.ndim == 2 and query_embedding.shape[0] == 1:
            q = query_embedding.tolist()
        else:
            raise ValueError(
                f"ChromaStore.query: query_embedding must be (dim,) or (1, dim); "
                f"got shape {query_embedding.shape}"
            )
        return self._collection.query(
            query_embeddings=q,
            n_results=n_results,
        )

    def get_by_ids(self, ids: List[str]) -> Dict[str, Any]:
        """Fetch existing entries by id — used by the indexer to compare mtime."""
        if not ids:
            return {"ids": [], "metadatas": [], "documents": []}
        return self._collection.get(
            ids=ids,
            include=["metadatas", "documents"],
        )

    def count(self) -> int:
        return int(self._collection.count())

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        self._collection.delete(ids=ids)

    def all_ids(self) -> List[str]:
        """Return every id currently in the collection. Cheap for our scale
        (target ~500 chunks)."""
        # chromadb returns all ids when no filter is given (limit is opt-in).
        got = self._collection.get(include=[])
        return list(got.get("ids", []))


__all__ = ["ChromaStore"]
