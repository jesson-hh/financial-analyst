# -*- coding: utf-8 -*-
"""Phase 6 shadow-consumer adapters — the bridge from the frozen shadow contract
layer (:mod:`guanlan_v2.orchestration.shadow`) to the untouched fa backtest engine.

This subpackage is deliberately a thin, reviewed edge: the only place in the
orchestration package that consumes the engine's ``DecisionLeg`` / ``Decision`` /
``DecisionInput`` value objects. Its members are the target-portfolio diff step and
the engine-shaped, zero-LLM ``ShadowDecisionAgent`` (:mod:`.luozi`).

The package ``__init__`` intentionally carries **no re-exports** — a reviewed empty
export surface — so importing the package never eagerly imports :mod:`.luozi` (and,
transitively, nothing forces the engine onto the import path at package load). Import
the concrete symbols from :mod:`guanlan_v2.orchestration.adapters.luozi` directly.
"""
