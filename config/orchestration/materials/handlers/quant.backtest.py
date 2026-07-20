# -*- coding: utf-8 -*-
"""handler.quant.backtest — deterministic backtest-evidence projection (Phase 8, Task 6).

Clause (h) impure-fallback: the legacy source ``guanlan_v2.screen.factor_vintage`` is NOT
an import-safe pure function — its vintage-IC / realized-date OOS lookup reads parquet
artifacts — so this handler is a self-contained stdlib projection over the legacy TYPED
OUTPUT (the vintage card fields) rather than importing / re-computing it.

Honesty rail: a not-yet-matured realized-date OOS window renders ``oos_verdict=None``
(UNAVAILABLE) and this handler appends an honest caveat naming the gap — a verdict is
NEVER fabricated as "pass". Any non-finite ``vintage_ic`` / ``pbo`` is dropped to ``None``
(absent), never coerced into an in-range number.

Pure + import-safe: no engine / datafeed / pandas / network import (only ``math`` for a
finiteness guard).
"""
from __future__ import annotations

import math

_NO_OOS_CAVEAT = "no matured realized-date OOS window; verdict UNAVAILABLE"


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def project(*, subject: str, vintage_ic=None, oos_verdict=None, pbo=None,
            caveats=()) -> dict:
    """Project vintage card fields into a ``BacktestEvidenceReport`` field set.

    ``vintage_ic`` / ``pbo`` are finite-guarded (a non-finite datum is absent → ``None``);
    an absent ``oos_verdict`` (``None``) triggers an appended honest caveat so the shortfall
    is stated rather than back-filled with a fabricated verdict.
    """
    verdict = oos_verdict if (oos_verdict is None or str(oos_verdict).strip()) else None
    out_caveats = list(caveats)
    if verdict is None and _NO_OOS_CAVEAT not in out_caveats:
        out_caveats.append(_NO_OOS_CAVEAT)
    return {
        "subject": subject,
        "vintage_ic": _finite(vintage_ic),
        "oos_verdict": verdict,
        "pbo": _finite(pbo),
        "caveats": tuple(out_caveats),
    }
