"""Markdown chunker — splits a `.md` file into chunks at H2 boundaries.

Used by the knowledge index to break strategy/research/wisdom markdown into
semantically coherent sections that fit inside a single embedding context.

Rules:
- Split on H2 (``## ...``). Each chunk = one H2 + its body until the next H2.
- If a file has **no H2** at all, return a single chunk covering the whole
  file with ``section_h2 = "(root)"`` (preserves stand-alone notes like
  short README-style files).
- If a file is **empty** (no non-whitespace content), return ``[]`` — do not
  raise. The indexer treats this as a skip.
- ``id`` is a 16-char SHA-256 prefix of ``source_file + section_h2`` so the
  same chunk gets a stable id across rebuilds (mtime drives re-embed, not id
  churn).
- ``tokens`` is a cheap whitespace approximation (good enough for stats /
  budget tracking; not a real tokenizer).
- ``lang`` defaults to ``'zh'`` because the entire strategy corpus is
  Simplified Chinese. Override by the caller if needed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# H2 detection: line starts with "## " (exactly two #), captures the heading text.
# We intentionally don't match "###" or "#" — H1 is treated as document title,
# H3+ as body subsections (BGE handles a few hundred tokens per chunk fine).
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Chunk:
    """A single chunked section of a markdown file."""

    id: str
    """Stable 16-char SHA-256 prefix of (source_file + section_h2)."""

    source_file: str
    """Path string of the source MD file (as passed to ``chunk_markdown``).
    Stored verbatim — caller decides whether to pass absolute or relative."""

    section_h2: str
    """H2 heading text (without the leading ``##``). ``"(root)"`` for files
    without any H2."""

    text: str
    """Section body, including the H2 heading line. Stripped of trailing
    whitespace; leading/intra whitespace preserved for downstream embedding."""

    mtime: float
    """File modification time (epoch seconds). Drives mtime-based incremental
    re-indexing in ``KnowledgeIndexer``."""

    tokens: int
    """Whitespace-split token count (approximate). For stats / budgets."""

    lang: str = "zh"
    """Language hint for the embedder. Defaults to Simplified Chinese — the
    strategy corpus is entirely zh-CN."""


def _make_id(source_file: str, section_h2: str) -> str:
    h = hashlib.sha256(f"{source_file}::{section_h2}".encode("utf-8")).hexdigest()
    return h[:16]


def _count_tokens(text: str) -> int:
    """Whitespace token approximation — cheap, deterministic, good enough."""
    return len(text.split())


def chunk_markdown(
    path: Path,
    *,
    lang: str = "zh",
    source_id: Optional[str] = None,
) -> List[Chunk]:
    """Split a markdown file into chunks at H2 boundaries.

    Parameters
    ----------
    path
        Path to the ``.md`` file. Must exist and be readable as UTF-8.
    lang
        Language tag stored on each chunk (default ``'zh'``).
    source_id
        Optional override for the ``source_file`` field. Defaults to
        ``str(path)`` — pass a normalised relative path here when indexing a
        whole tree so chunk ids stay stable across machines.

    Returns
    -------
    list of Chunk
        - One chunk per H2 section.
        - One chunk covering the whole file if there are no H2 headings.
        - **Empty list** if the file is empty (no non-whitespace content).
    """
    if not path.exists():
        raise FileNotFoundError(f"chunk_markdown: {path} does not exist")

    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return []

    mtime = path.stat().st_mtime
    src = source_id if source_id is not None else str(path)

    # Find every H2 with its byte offset so we can slice cleanly.
    matches = list(_H2_RE.finditer(raw))
    if not matches:
        # No H2 → whole file as one chunk.
        text = raw.rstrip()
        return [
            Chunk(
                id=_make_id(src, "(root)"),
                source_file=src,
                section_h2="(root)",
                text=text,
                mtime=mtime,
                tokens=_count_tokens(text),
                lang=lang,
            )
        ]

    chunks: List[Chunk] = []
    for i, m in enumerate(matches):
        section = m.group(1)
        start = m.start()  # include the "## ..." heading line in the chunk text
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        text = raw[start:end].rstrip()
        if not text.strip():
            continue
        chunks.append(
            Chunk(
                id=_make_id(src, section),
                source_file=src,
                section_h2=section,
                text=text,
                mtime=mtime,
                tokens=_count_tokens(text),
                lang=lang,
            )
        )
    return chunks


__all__ = ["Chunk", "chunk_markdown"]
