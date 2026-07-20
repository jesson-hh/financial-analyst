# -*- coding: utf-8 -*-
"""handler.quant.factor — deterministic factor-IC projection (Phase 8, Task 6).

Clause (h) impure-fallback: the legacy source ``guanlan_v2.screen.factor_ic`` is NOT an
import-safe pure function — its ``compute_catalog_ic`` needs the financial-analyst loader,
the universe panel, and parquet artifact IO — so this handler is a self-contained stdlib
projection over the legacy TYPED OUTPUT (the ``{factor_id: {ic, ...}}`` rows the compute
path lands) rather than importing / re-running it.

Honesty rail (badge-adjacent): the 帷幄 ``factor_ic`` IC is a 近窗回看 (look-back) IC, so
every projected row carries ``oos=False`` — a 回看 IC must NEVER masquerade as a PIT-OOS
one. ``FEEDBACK_IS_OOS`` is exported so the Task-8 gate-metric materials can bind the
"``oos=False`` rows never satisfy an OOS-labeled gate" contract to a single source.

Pure + import-safe: no engine / datafeed / pandas / network import.
"""
from __future__ import annotations

import math

#: the 帷幄 factor_ic source is 近窗回看; its rows are NEVER PIT-OOS.
FEEDBACK_IS_OOS = False


def _finite(value) -> float | None:
    """Coerce to a finite float, or ``None`` (a non-finite / non-numeric datum is absent)."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def project_rows(records, *, window: str) -> tuple[dict, ...]:
    """Project legacy factor-IC records into sorted, duplicate-free report rows.

    ``records``: an iterable of mappings ``{"factor_id", "ic", optional "rank_ic"}`` (the
    legacy ``load_factor_ic`` output shape). Returns a tuple sorted by ``factor_id``,
    duplicate-free (a duplicate id is a caller bug — raised, never silently collapsed),
    with each row marked ``oos=FEEDBACK_IS_OOS`` (回看 honesty) and a ``None`` ``rank_ic``
    left ``None`` (never fabricated). ``ic`` is required (a rowless factor is honestly
    absent upstream, not passed here).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for rec in records:
        fid = rec["factor_id"]
        if fid in seen:
            raise ValueError(f"duplicate factor_id {fid!r} in factor-IC records")
        seen.add(fid)
        ic = _finite(rec.get("ic"))
        if ic is None:
            raise ValueError(
                f"factor {fid!r} has no finite ic — an uncomputable factor is honestly "
                "absent upstream, never projected with a fabricated IC"
            )
        out.append({
            "factor_id": fid,
            "ic": ic,
            "rank_ic": _finite(rec.get("rank_ic")),
            "window": window,
            "oos": FEEDBACK_IS_OOS,
        })
    out.sort(key=lambda r: r["factor_id"])
    return tuple(out)
