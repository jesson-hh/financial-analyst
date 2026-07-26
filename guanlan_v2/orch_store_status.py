# -*- coding: utf-8 -*-
"""The process's orchestration durable-store binding outcome — a dependency-free leaf.

Why this lives OUTSIDE ``guanlan_v2.orchestration``
---------------------------------------------------
It is the *provider* half of the operator surface for the R23/R24 startup binding, and
its whole job is to be safe to import from anywhere — including from a consumer that
must keep working when the orchestration package is absent, broken, or simply never
bound. So it imports nothing (not even from this repo), has no side effects, and is
never the thing that fails.

``guanlan_v2.orchestration.startup`` writes the ``bound`` / ``corrupt`` / ``failed``
outcomes here; ``guanlan_v2.server`` writes ``unavailable`` (the one state only the
server can observe, because it means ``startup`` itself could not be imported). Readers
call :func:`orchestration_store_state` or :func:`orchestration_store_health_item`.

The state vocabulary (the single canonical list — do not restate it elsewhere)
------------------------------------------------------------------------------
=================  ===========================================================
``not_attempted``  no bind has run in this process yet
``bound``          **the only healthy value** — the store is bound and correct
``unavailable``    ``guanlan_v2.orchestration.startup`` could not be imported
                   (written by ``server.py``, the only place that can see this)
``corrupt``        ``DurableStoreCorrupt`` — a data-integrity hard failure
``failed``         any other bind failure (a wiring bug)
=================  ===========================================================

Everything other than ``bound`` means **this process has no orchestration durable
store**. That is the distinction the whole record exists to make legible.

CARRY — the `/data/health` consumer (deferred, controller ruling 2026-07-26)
-----------------------------------------------------------------------------
A boot-time ``CRITICAL`` scrolls away and ``GET /orchestration/store_status`` is a route
nobody polls, so a corrupt store can sit unnoticed for days. The fix is to surface it in
the ``/data/health`` operator gate — but ``guanlan_v2/datafeed/health.py`` was
foreign-dirty when this landed, so only the provider ships here.
:func:`orchestration_store_health_item` already returns health.py's exact item shape.
The complete remaining wiring, when that file is clean, is:

1. ``from guanlan_v2.orch_store_status import orchestration_store_health_item``
2. add ``"orchestration_store": orchestration_store_health_item`` to ``_ITEMS``
3. add ``"orchestration_store"`` to ``_OPS_ITEMS`` — it is an ops item, not a data
   freshness item, exactly like ``regen_scheduler``; it must not move ``overall``.
"""
from __future__ import annotations

__all__ = [
    "STATES",
    "HEALTHY_STATE",
    "STRICT_ENV",
    "OrchestrationStoreBootRefused",
    "refuse_if_strict",
    "blank_status",
    "record_status",
    "orchestration_store_status",
    "orchestration_store_state",
    "orchestration_store_bound",
    "orchestration_store_health_item",
    "reset_for_tests",
]

#: the closed state vocabulary (see the module docstring's table).
STATES: tuple[str, ...] = ("not_attempted", "bound", "unavailable", "corrupt", "failed")
#: the one value that means "everything is fine".
HEALTHY_STATE = "bound"

#: Opt-in: refuse the boot instead of booting visibly store-less. The flag asserts "I
#: require a working orchestration durable store in this process", so it refuses on
#: **every** non-``bound`` outcome — a corrupt journal, a wiring bug, **and including
#: when the subsystem is absent entirely** (a broken ``durable.py``, or
#: ``guanlan_v2.orchestration.startup`` itself not importable). A missing package is the
#: most complete failure of that assertion, not an exemption from it.
#:
#: This constant and :class:`OrchestrationStoreBootRefused` live in this leaf precisely
#: so that ``server.py`` can honour the flag in the branch where the orchestration
#: package could not be imported at all — the branch that previously fell through and
#: booted normally under strict.
STRICT_ENV = "GUANLAN_ORCH_STORE_STRICT"


class OrchestrationStoreBootRefused(RuntimeError):
    """Strict mode (:data:`STRICT_ENV`) refused to boot without a bound durable store.

    The **only** exception any part of the startup binding is allowed to let escape into
    ``create_app()``. Defined here rather than in
    ``guanlan_v2.orchestration.startup`` so it stays importable when that module is not.
    """


def refuse_if_strict(strict: bool, marker: str, exc: BaseException | None = None,
                     detail: str = "") -> None:
    """Raise :class:`OrchestrationStoreBootRefused` when ``strict``; else do nothing.

    The single definition of the refusal, shared by both callers — ``startup``'s
    ``_degrade`` and ``server.py``'s package-not-importable branch. Having one function
    is the point: the two paths previously produced the same recorded ``unavailable``
    state with *opposite* boot behaviour under the same flag.
    """
    if not strict:
        return None
    cause = f" ({type(exc).__name__}: {exc})" if exc is not None else ""
    raise OrchestrationStoreBootRefused(
        f"{marker}: refusing to boot without a correctly bound orchestration durable "
        f"store{(' ' + detail) if detail else ''}{cause}") from exc


def blank_status() -> dict:
    """A fresh, JSON-safe, never-bound record. The single shape definition."""
    return {
        "state": "not_attempted",
        "bound": False,
        "root": None,
        "cell_namespaces": [],
        "cell_namespace_count": 0,
        "registry_digests": [],
        "error_type": None,
        "error": None,
        "strict": False,
    }


_STATUS: dict = blank_status()


def record_status(status: dict) -> dict:
    """Record this process's binding outcome (writers only). Returns a read copy."""
    global _STATUS
    state = status.get("state")
    if state not in STATES:  # pragma: no cover - defensive; writers are in-repo
        raise ValueError(f"unknown orchestration store state {state!r}")
    _STATUS = dict(status)
    return orchestration_store_status()


def orchestration_store_status() -> dict:
    """A JSON-safe defensive copy of the full record."""
    return dict(_STATUS)


def orchestration_store_state() -> str:
    """Just the state — the one-liner an external operator gate wants."""
    return str(_STATUS.get("state", "not_attempted"))


def orchestration_store_bound() -> bool:
    """``True`` only when this process really has a correctly bound durable store."""
    return orchestration_store_state() == HEALTHY_STATE


def orchestration_store_health_item() -> dict:
    """The record in ``guanlan_v2/datafeed/health.py``'s item shape (see the CARRY note).

    An ops item, not a data-freshness item: ``fresh`` when bound, ``missing`` when the
    store is corrupt or the bind failed (a real fault an operator must act on), and
    ``unknown`` when nothing was ever attempted or the package is simply absent (not a
    fault — the orchestration framework is opt-in machinery).
    """
    state = orchestration_store_state()
    status = {
        "bound": "fresh",
        "corrupt": "missing",
        "failed": "missing",
        "not_attempted": "unknown",
        "unavailable": "unknown",
    }.get(state, "unknown")
    note = ""
    if state == "corrupt":
        note = ("orchestration durable store CORRUPT — this process has NO store "
                "(grep ORCH-STORE-CORRUPT); inspect the journal by hand")
    elif state == "failed":
        note = "orchestration durable store bind FAILED — this process has NO store"
    elif state == "unavailable":
        note = "orchestration startup wiring not importable (subsystem absent)"
    elif state == "not_attempted":
        note = "no orchestration store bind ran in this process"
    return {
        "status": status,
        "state": state,
        "root": _STATUS.get("root"),
        "cell_namespace_count": _STATUS.get("cell_namespace_count", 0),
        "error_type": _STATUS.get("error_type"),
        "note": note,
    }


def reset_for_tests() -> None:
    """Test-only: forget the recorded outcome."""
    global _STATUS
    _STATUS = blank_status()
