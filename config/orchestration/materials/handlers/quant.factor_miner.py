# -*- coding: utf-8 -*-
"""handler.quant.factor_miner — deterministic mined-factor-draft projection (Phase 8, Task 6).

Clause (h) impure-fallback: the legacy source ``guanlan_v2.research.loop`` is NOT an
import-safe pure function — it orchestrates a full propose→evaluate→gate→critique research
loop (graph execution + LLM + network + artifact IO) — so this handler is a self-contained
stdlib projection over the legacy TYPED OUTPUT (one round's ``factor_expr`` / ``rank_ic`` /
``sharpe`` / ``robust`` / ``passed_gate`` result) rather than importing / re-running it.

Honesty rail: ``DRAFT_ONLY`` is structural — a mined factor is always a DRAFT (factorlib
promotion stays 人审, downstream). ``passed_gate`` is carried VERBATIM (a failed
Sharpe/robust 门 is a failed gate — never upgraded); the handler never re-decides admission.

Pure + import-safe: no engine / datafeed / pandas / network import (only ``math`` for a
finiteness guard).
"""
from __future__ import annotations

import math

#: a mined factor is always a DRAFT — factorlib promotion stays human, downstream.
DRAFT_ONLY = True


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def project(*, factor_expr: str, rank_ic, sharpe=None, robust=None,
            passed_gate: bool) -> dict:
    """Project one research-loop round result into a ``MinedFactorDraft`` field set.

    ``rank_ic`` is required finite (a round with no finite rank-IC is an honest non-result
    upstream, not projected here); ``sharpe`` / ``robust`` are finite-guarded nullables;
    ``passed_gate`` is echoed verbatim. ``draft_only`` is NOT returned — the schema pins it
    to ``True`` structurally.
    """
    ric = _finite(rank_ic)
    if ric is None:
        raise ValueError(
            "a mined-factor round requires a finite rank_ic — a non-result is honestly "
            "absent upstream, never projected"
        )
    return {
        "factor_expr": factor_expr,
        "rank_ic": ric,
        "sharpe": _finite(sharpe),
        "robust": _finite(robust),
        "passed_gate": bool(passed_gate),
    }
