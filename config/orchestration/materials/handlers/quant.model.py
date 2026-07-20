# -*- coding: utf-8 -*-
"""handler.quant.model — deterministic model-prediction projection (Phase 8, Task 6).

Clause (h) impure-fallback: the legacy source ``guanlan_v2.screen.model_registry`` is NOT
an import-safe pure function — it reads ``MODELS_DIR/<id>/{v4_ranking.parquet, meta.json}``
from disk — so this handler is a self-contained stdlib projection over the legacy TYPED
OUTPUT (the ranking rows + ``meta`` vintage) rather than importing / re-reading it.

Honesty rail: ``stale_days`` (DL 断供显形) is carried VERBATIM — a stale or absent DL feed
is never hidden behind a zero. Ranks are validated strictly ascending + unique (a rank
collision is a ranking bug, not a tie).

Pure + import-safe: no engine / datafeed / pandas / network import.
"""
from __future__ import annotations


def project(rows, *, model_id: str, model_asof, stale_days: int) -> dict:
    """Project a legacy variant ranking into a ``ModelPredictionReport`` field set.

    ``rows``: an ordered sequence of mappings ``{"symbol", "score", "rank"}`` (rank-order
    preserved from the legacy ranking). Ranks must be strictly ascending + unique — a
    violation is raised here (not silently re-sorted). ``stale_days`` is coerced to a
    non-negative int and carried verbatim.
    """
    projected = tuple(dict(r) for r in rows)
    ranks = [int(r["rank"]) for r in projected]
    for a, b in zip(ranks, ranks[1:]):
        if b <= a:
            raise ValueError(
                "model prediction ranks must be strictly ascending; a rank collision is "
                "a ranking bug, not a tie"
            )
    sd = int(stale_days)
    if sd < 0:
        raise ValueError("stale_days must be non-negative (DL 断供显形, never negative)")
    return {
        "model_id": model_id,
        "model_asof": model_asof,
        "rows": projected,
        "stale_days": sd,
    }
