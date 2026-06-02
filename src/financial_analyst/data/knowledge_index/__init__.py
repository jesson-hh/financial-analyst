"""Vector knowledge index over strategy/ markdown.

See ``docs/superpowers/specs/2026-06-02-knowledge-index-fdr-shap-design.md``
section SP-1 for the full design.

Public surface::

    from financial_analyst.data.knowledge_index import (
        KnowledgeIndex,    # facade — build + search
        ChunkResult,       # one search hit
        Chunk,             # one chunked MD section
    )

Internals (use only when you need to swap the embedder or talk to the store
directly)::

    from financial_analyst.data.knowledge_index.embedder import (
        BgeEmbedder, StubEmbedder,
    )
    from financial_analyst.data.knowledge_index.store import ChromaStore
    from financial_analyst.data.knowledge_index.indexer import (
        KnowledgeIndexer, BuildStats,
    )
    from financial_analyst.data.knowledge_index.chunker import chunk_markdown
"""
from __future__ import annotations

from .chunker import Chunk, chunk_markdown
from .indexer import BuildStats, KnowledgeIndexer
from .search import ChunkResult, KnowledgeIndex
from .store import ChromaStore

__all__ = [
    "KnowledgeIndex",
    "ChunkResult",
    "Chunk",
    "chunk_markdown",
    "KnowledgeIndexer",
    "BuildStats",
    "ChromaStore",
]
