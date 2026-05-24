"""Alpha Zoo — registered families of alpha formulas with cross-sectional
benchmarking.

A registry of named alpha formulas plus an ``alpha bench`` CLI that emits
IC / IR / hit rate per alpha against a chosen universe and period.

Two families ship in v1.3.0:

* ``alpha101`` — WorldQuant 101 Formulaic Alphas (Kakushadze, 2015).
* ``gtja191`` — Guotai Junan Securities 191 Alphas, optimised for the
  Chinese A-share short-horizon prediction task.

``qlib158`` and an ``academic`` family are stubs reserved for later
releases.

Public API::

    from financial_analyst.factors.zoo import (
        register, get, list_alphas, run_bench,
    )

The registry is populated at import time via ``register`` decorators
inside each family's ``alphas.py``. Importing ``financial_analyst.factors.zoo``
auto-loads every family — set ``FA_ZOO_LAZY=1`` in the environment to
defer loading (test mode).
"""
from __future__ import annotations
import os

from financial_analyst.factors.zoo.registry import (  # noqa: F401
    AlphaSpec,
    register,
    get,
    list_alphas,
    families,
)
from financial_analyst.factors.zoo.panel import PanelData  # noqa: F401

if os.environ.get("FA_ZOO_LAZY", "") != "1":
    # Import families so their @register decorators fire.
    from financial_analyst.factors.zoo import alpha101  # noqa: F401
    from financial_analyst.factors.zoo import gtja191   # noqa: F401
    from financial_analyst.factors.zoo import qlib158   # noqa: F401

__all__ = [
    "AlphaSpec", "register", "get", "list_alphas", "families", "PanelData",
]
