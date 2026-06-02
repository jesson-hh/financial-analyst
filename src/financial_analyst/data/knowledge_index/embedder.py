"""Embedding backends for the knowledge index.

Two implementations:

- ``BgeEmbedder``: production. Wraps ``sentence-transformers`` with
  ``BAAI/bge-large-zh-v1.5`` (1024-dim, Chinese SOTA at the time of design).
  The model is **lazy-loaded** on first ``encode()`` call so that import is
  cheap (avoids 340MB download at module import time).
- ``StubEmbedder``: tests. Deterministic random vectors driven by a fixed
  ``random_state``. No network, no model download — keeps the test suite
  hermetic and fast.

Both expose the same surface:

    embedder.encode(texts: List[str]) -> np.ndarray  # shape (n, dim)
    embedder.dim                                     # int

The shape contract lets ``ChromaStore.upsert`` accept the result directly.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np


class StubEmbedder:
    """Deterministic random-vector embedder for tests.

    Reproducible (fixed ``random_state``), no network, no model download.
    Same text always maps to the same vector via a hash-derived seed so
    queries can hit cached vectors.
    """

    def __init__(self, dim: int = 8, random_state: int = 0) -> None:
        self.dim = int(dim)
        self.random_state = int(random_state)

    def encode(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        # Hash each text → deterministic per-text seed → reproducible vector.
        # We mix in the global random_state so two StubEmbedder instances with
        # different seeds give different (but each one repeatable) embeddings.
        out = np.empty((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = (abs(hash(t)) + self.random_state) & 0xFFFFFFFF
            rng = np.random.RandomState(seed)
            v = rng.rand(self.dim).astype(np.float32)
            # L2-normalise — matches what real BGE produces and lets us use
            # cosine via dot-product if we ever want it.
            n = np.linalg.norm(v)
            if n > 0:
                v = v / n
            out[i] = v
        return out


class BgeEmbedder:
    """Production embedder — wraps sentence-transformers BGE-zh-v1.5.

    Lazy-loaded on first ``encode()`` call (model download ~340MB happens then,
    not at import). Subsequent calls reuse the in-process model.

    Parameters
    ----------
    model_name
        Hugging Face model id. Default ``BAAI/bge-large-zh-v1.5`` (Chinese
        SOTA). Override for tests / experiments only.
    device
        ``'cpu'`` (default) or ``'cuda'`` if you have a GPU. None lets
        sentence-transformers pick.
    normalize_embeddings
        Whether to L2-normalise output. Default True so cosine similarity
        equals dot-product (Chroma's default metric is L2; cosine is opt-in).
    """

    DEFAULT_MODEL = "BAAI/bge-large-zh-v1.5"
    DEFAULT_DIM = 1024  # BGE-large-zh-v1.5 produces 1024-dim vectors

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: Optional[str] = None,
        normalize_embeddings: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.normalize_embeddings = normalize_embeddings
        self._model = None  # lazy
        # We expose ``dim`` even before the model loads — for BGE-large-zh
        # we know the answer up front and there's no benefit to a 340MB
        # download just to read it. After load() the value is replaced
        # with the actual model dim in case the user passed a different
        # ``model_name``.
        self.dim = self.DEFAULT_DIM

    def _load(self) -> None:
        if self._model is not None:
            return
        # Import inside _load so module import stays cheap and the test suite
        # can run without sentence-transformers installed (StubEmbedder path).
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name, device=self.device)
        # Update dim from the actual model (in case a non-default model was passed).
        try:
            self.dim = int(self._model.get_sentence_embedding_dimension())
        except Exception:
            pass  # fall back to DEFAULT_DIM

    def encode(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        self._load()
        arr = self._model.encode(
            texts,
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(arr, dtype=np.float32)


__all__ = ["BgeEmbedder", "StubEmbedder"]
