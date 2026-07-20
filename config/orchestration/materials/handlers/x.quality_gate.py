# -*- coding: utf-8 -*-
"""handler.x.quality_gate — deterministic ABCDF data-quality projection (Phase 8, Task 7).

Clause (h) impure-fallback: the legacy source ``guanlan_v2.datafeed.health`` is NOT an
import-safe pure function — its ``collect_data_health`` reads live on-disk freshness
snapshots / provenance files across the whole repo — so this handler is a self-contained
stdlib projection over the WIRED REPORTS' HONESTY CHANNELS (each report's ``degradation``
tuple / ``stale_days`` / ``coverage_note``) into an ABCDF grade per source, rather than
importing / re-running the health collector.

Honest weakest-link: the overall grade is the WORST component grade — an ABCDF gate is
only as good as its weakest wired source, NEVER an average that hides a failing feed. A
source with no wired report is graded ``F`` (absent), never silently dropped.

CONTROLLER RULING (b) — carried: rendered-markdown / string-embedded numbers are OUTSIDE
the number-provenance scan boundary (a known accepted limit); this grader reads ONLY the
typed honesty channels above, never prose.

Pure + import-safe: no engine / datafeed / pandas / network import (only ``math`` for a
finiteness guard).
"""
from __future__ import annotations

import math

#: staleness window in the reports' own unit (trading days), aligned with the
#: ``datafeed.health`` DL 断供 window (``_DL_STALE_DAYS = 3``): a ``stale_days`` beyond it
#: is a material freshness gap that downgrades the source.
STALE_DAYS_THRESHOLD = 3

#: ABCDF severity rank (A best … F worst).
_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
_RANK_GRADE = {v: k for k, v in _GRADE_RANK.items()}


def _finite_int(value):
    """Coerce to a non-negative int, or ``None`` (a non-finite / negative / bad datum)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out < 0:
        return None
    return int(out)


def grade_source(*, source_id, missing=False, degradation=(), stale_days=None,
                 coverage_note=None) -> dict:
    """Grade one wired source's HONESTY channels into a ``QualityComponent`` field set.

    ABCDF ladder (honest, deterministic):

    * ``F`` — the source is ``missing`` (no report wired at all);
    * ``D`` — stale AND degraded (a compounded shortfall);
    * ``C`` — stale, OR ≥2 degradation rows;
    * ``B`` — exactly one degradation row, OR a coverage note (a single, stated gap);
    * ``A`` — fresh, complete, no degradation, no coverage note.

    ``stale`` = a finite ``stale_days`` strictly beyond :data:`STALE_DAYS_THRESHOLD`.
    Returns ``{"source_id", "grade", "reason"}`` — a band is never returned without a
    stated ``reason``.
    """
    degradation = tuple(degradation)
    sd = _finite_int(stale_days)
    stale = sd is not None and sd > STALE_DAYS_THRESHOLD
    n_deg = len(degradation)

    if missing:
        grade = "F"
        reason = f"source {source_id!r} absent — no report wired"
    elif stale and n_deg > 0:
        grade = "D"
        reason = (f"stale ({sd}d > {STALE_DAYS_THRESHOLD}d) and degraded: "
                  + "; ".join(degradation))
    elif stale or n_deg >= 2:
        grade = "C"
        reason = (f"stale ({sd}d > {STALE_DAYS_THRESHOLD}d)" if stale
                  else "multiple degradations: " + "; ".join(degradation))
    elif n_deg == 1 or coverage_note is not None:
        grade = "B"
        reason = (f"minor degradation: {degradation[0]}" if n_deg == 1
                  else f"coverage note: {coverage_note}")
    else:
        grade = "A"
        reason = "fresh, complete, no degradation"
    return {"source_id": source_id, "grade": grade, "reason": reason}


def overall_grade(components) -> str:
    """The overall grade = the WORST component grade (honest weakest-link)."""
    ranks = [_GRADE_RANK[c["grade"]] for c in components]
    if not ranks:
        raise ValueError("a DataQualityGrade must grade at least one source component")
    return _RANK_GRADE[max(ranks)]


def project(components) -> dict:
    """Project graded sources into a ``DataQualityGrade`` field set (ready to splat).

    ``components``: an iterable of :func:`grade_source` outputs. Sorted by ``source_id``,
    duplicate-free (a source graded twice is a caller bug — raised, never silently
    collapsed), with the overall ``grade`` = the worst component (weakest-link honesty).
    Returns ``{"grade": ..., "components": (...)}`` for the caller to wrap with ``as_of``.
    """
    seen: set = set()
    ordered: list = []
    for comp in components:
        sid = comp["source_id"]
        if sid in seen:
            raise ValueError(f"duplicate source_id {sid!r} in quality components")
        seen.add(sid)
        ordered.append(comp)
    ordered.sort(key=lambda c: c["source_id"])
    return {"grade": overall_grade(ordered), "components": tuple(ordered)}
