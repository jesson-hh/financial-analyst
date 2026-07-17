# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — the unified PIT-safe memory facade.

Submodules (import them directly; this package root stays import-cheap):

* ``models``          — every registered memory contract + pure builders;
* ``schema_registry`` — the sealed cumulative full (data + memory) registry;
* ``catalog``         — the reviewed memory facade/bridge catalog extension;
* ``adapters``        — read-only AgentMemory / console store adapters;
* ``store``           — exactly-once repositories + capture/visibility/selection;
* ``proposals``       — the proposal-only mutation boundary (delegation);
* ``runtime``         — the Phase-2 ``memory_refs_v1`` execution bridge.
"""
