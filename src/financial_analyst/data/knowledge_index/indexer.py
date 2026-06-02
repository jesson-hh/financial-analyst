"""Indexer — scans a strategy/ directory, chunks each MD, embeds, and stores.

The default scan covers the four canonical strategy locations:

- top-level ``strategy/*.md`` (pitfalls / factor_insights / rating_system / ...)
- ``strategy/research/*.md`` (dated research notes)
- ``strategy/wisdom/*.md`` (sentinel / playbook / analyst wisdom)
- ``strategy/stocks/*.md`` (per-stock timelines)

Incremental rebuild:

- ``build(force=True)`` re-embeds **everything** and overwrites the store.
- ``build(force=False)`` (default) consults each chunk's existing metadata
  ``mtime`` in the store; if the source file's current mtime matches the
  stored mtime, the chunk is skipped. Only changed / new chunks get
  re-embedded. Old chunks whose source file or section disappeared are
  deleted so the store stays in sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .chunker import Chunk, chunk_markdown
from .store import ChromaStore


# Glob patterns we scan. Relative to ``strategy_root``. Order is informational
# only — chunk ids are content-derived.
DEFAULT_GLOBS = (
    "*.md",
    "research/*.md",
    "wisdom/*.md",
    "stocks/*.md",
)


@dataclass
class BuildStats:
    """Summary of the work done by one ``build()`` call."""

    files_scanned: int = 0
    chunks_seen: int = 0
    chunks_embedded: int = 0
    chunks_skipped_unchanged: int = 0
    chunks_deleted_stale: int = 0
    files_skipped_empty: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, int | List[str]]:
        return {
            "files_scanned": self.files_scanned,
            "chunks_seen": self.chunks_seen,
            "chunks_embedded": self.chunks_embedded,
            "chunks_skipped_unchanged": self.chunks_skipped_unchanged,
            "chunks_deleted_stale": self.chunks_deleted_stale,
            "files_skipped_empty": self.files_skipped_empty,
            "errors": list(self.errors),
        }


class KnowledgeIndexer:
    """Build / refresh a ``ChromaStore`` from a strategy/ markdown tree."""

    def __init__(
        self,
        strategy_root: Path,
        store: ChromaStore,
        embedder,
        globs: Sequence[str] = DEFAULT_GLOBS,
    ) -> None:
        self.strategy_root = Path(strategy_root)
        self.store = store
        self.embedder = embedder
        self.globs = tuple(globs)

    # ──────────────── public API ────────────────

    def discover(self) -> List[Path]:
        """List MD files under ``strategy_root`` matching configured globs.

        De-duplicated and sorted for deterministic ordering. Missing root
        returns ``[]`` — the indexer is best-effort on a fresh box."""
        if not self.strategy_root.exists():
            return []
        seen: Set[Path] = set()
        for pattern in self.globs:
            for p in self.strategy_root.glob(pattern):
                if p.is_file():
                    seen.add(p.resolve())
        return sorted(seen)

    def _chunk_all(self) -> List[Chunk]:
        """Chunk every discovered file. Empty files are silently dropped."""
        chunks: List[Chunk] = []
        for path in self.discover():
            # Use the path **relative to strategy_root** as the canonical
            # source id so the same chunk gets the same id regardless of
            # whether the indexer was started with an absolute or relative
            # strategy_root.
            try:
                rel = path.relative_to(self.strategy_root)
                source_id = rel.as_posix()
            except ValueError:
                source_id = str(path)
            chunks.extend(chunk_markdown(path, source_id=source_id))
        return chunks

    def build(self, force: bool = False) -> BuildStats:
        """Build / refresh the index.

        Parameters
        ----------
        force
            If True, re-embed every chunk and overwrite the store (still
            deletes stale chunks too). If False (default), skip chunks whose
            source file's mtime matches the stored mtime.
        """
        stats = BuildStats()

        # 1. Discover + chunk everything.
        files = self.discover()
        stats.files_scanned = len(files)

        all_chunks = self._chunk_all()
        stats.chunks_seen = len(all_chunks)

        # Files that produced 0 chunks → empty
        chunked_files = {c.source_file for c in all_chunks}
        for p in files:
            try:
                rel = p.relative_to(self.strategy_root).as_posix()
            except ValueError:
                rel = str(p)
            if rel not in chunked_files:
                stats.files_skipped_empty += 1

        if not all_chunks:
            # Nothing to embed — still need to clean stale entries from a
            # prior build (e.g. all source files deleted).
            existing_ids = set(self.store.all_ids())
            stale = list(existing_ids)
            self.store.delete(stale)
            stats.chunks_deleted_stale = len(stale)
            return stats

        # 2. Decide which chunks need embedding.
        chunks_by_id = {c.id: c for c in all_chunks}
        to_embed: List[Chunk] = []

        if force:
            to_embed = list(all_chunks)
        else:
            existing = self.store.get_by_ids(list(chunks_by_id.keys()))
            stored_mtime: Dict[str, float] = {}
            for cid, meta in zip(existing.get("ids", []), existing.get("metadatas", [])):
                if meta and "mtime" in meta:
                    try:
                        stored_mtime[cid] = float(meta["mtime"])
                    except (TypeError, ValueError):
                        pass
            for c in all_chunks:
                prior = stored_mtime.get(c.id)
                if prior is None or prior < c.mtime:
                    to_embed.append(c)
                else:
                    stats.chunks_skipped_unchanged += 1

        # 3. Embed (batch) and upsert.
        if to_embed:
            texts = [c.text for c in to_embed]
            try:
                embeddings = self.embedder.encode(texts)
            except Exception as e:  # pragma: no cover - depends on backend
                stats.errors.append(f"embedder failed: {e!r}")
                return stats
            ids = [c.id for c in to_embed]
            metadatas = [
                {
                    "source_file": c.source_file,
                    "section_h2": c.section_h2,
                    "mtime": float(c.mtime),
                    "tokens": int(c.tokens),
                    "lang": c.lang,
                }
                for c in to_embed
            ]
            documents = [c.text for c in to_embed]
            self.store.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
            stats.chunks_embedded = len(to_embed)

        # 4. Delete stale chunks (in store but no longer in the source tree).
        existing_ids = set(self.store.all_ids())
        current_ids = set(chunks_by_id.keys())
        stale = sorted(existing_ids - current_ids)
        if stale:
            self.store.delete(stale)
            stats.chunks_deleted_stale = len(stale)

        return stats


__all__ = ["KnowledgeIndexer", "BuildStats", "DEFAULT_GLOBS"]
