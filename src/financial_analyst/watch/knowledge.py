"""watch/knowledge.py — load the watch-agent 盘中决策守则 (fa memory location).

The advisor injects curated, **validated** strategy knowledge into its prompt so
盘中 decisions align with the project's edge (反转核心 / 量能 regime / 游资票失效 /
市值分层 / 风控铁律 / V1-V10). The knowledge lives as markdown in the fa agent
memory location ``<memory_root>/watch-agent/*.md`` (shipped seed under
``_resources/memories_seed/watch-agent/``) — editable markdown that ships in the
wheel and can be customised per deploy.

**Own files only (not ``_shared/``)**: the shared ``playbook_V1_V10.md`` is ~32KB;
injecting it every tick would blow the 盘中 prompt budget + latency. The curated
``intraday_playbook.md`` already carries a condensed V1-V10 anchor. Resolution
prefers the writable ``default_memory_root`` (user edits win), then falls back to
the bundled seed (always present in the wheel). Fully defensive — any failure →
``""`` so the advisor still runs on the generic prompt, never crashing a tick.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

log = logging.getLogger(__name__)

_AGENT = "watch-agent"
_cache: Optional[str] = None   # process cache for the no-arg (default-root) load


def _read_agent_dir(root: Union[str, Path]) -> str:
    """Concatenate ``<root>/watch-agent/*.md`` (own files only). ``''`` if none."""
    own = Path(root) / _AGENT
    if not own.is_dir():
        return ""
    chunks: List[str] = []
    for md in sorted(own.glob("*.md")):
        try:
            txt = md.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.debug("watch.knowledge: read %s failed: %s", md, exc)
            continue
        if txt:
            chunks.append(txt)
    return "\n\n".join(chunks)


def load_watch_knowledge(memory_root: Optional[Union[str, Path]] = None) -> str:
    """Curated watch-agent 盘中守则 text. ``""`` on any failure (never raises).

    Args:
        memory_root: explicit root (tests pass a tmp dir; bypasses the cache).
            When ``None``, resolves the writable memory root, then the bundled
            seed, and process-caches the result.
    """
    global _cache

    if memory_root is not None:
        try:
            return _read_agent_dir(memory_root)
        except Exception as exc:  # noqa: BLE001 — advisor must not crash on knowledge
            log.debug("watch.knowledge: read %s failed: %s", memory_root, exc)
            return ""

    if _cache is not None:
        return _cache

    roots: List[Path] = []
    try:
        from financial_analyst.memory_paths import (
            bundled_seed_dir,
            default_memory_root,
        )
        try:
            roots.append(default_memory_root())   # writable root (user edits win)
        except Exception as exc:  # noqa: BLE001
            log.debug("watch.knowledge: default_memory_root failed: %s", exc)
        try:
            roots.append(bundled_seed_dir())      # always in the wheel
        except Exception as exc:  # noqa: BLE001
            log.debug("watch.knowledge: bundled_seed_dir failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.debug("watch.knowledge: memory_paths import failed: %s", exc)

    text = ""
    for r in roots:
        try:
            text = _read_agent_dir(r)
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            break

    _cache = text
    return text


def _reset_cache_for_tests() -> None:
    """Clear the process cache (tests only)."""
    global _cache
    _cache = None


__all__ = ["load_watch_knowledge"]
