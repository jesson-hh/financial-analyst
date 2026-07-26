# -*- coding: utf-8 -*-
"""Production process-wide binding of the orchestration durable stores (R23 + R24).

Why this module exists
----------------------
``guanlan_v2/server.py`` bound the process durable stores with **no kwargs**, so
:func:`~guanlan_v2.orchestration.adapters.durable.bind_process_durable_stores_and_scan`
fell back to its minimal defaults:

* a resolver holding only the Phase-1 ``default_registry()`` and the Phase-2 runtime
  registry — no Phase-3…9 registry. A Phase-9 payload write therefore raised
  ``UnknownRegistryDigest``; worse, once such a row existed on disk the *next*
  startup fold re-read it, failed reconstruction, and ``_DurableLog.fold_into``
  wrapped that as ``DurableStoreCorrupt`` — which the server swallowed into one
  stderr line, leaving the whole process silently store-less.
* ``allowed_cell_namespaces=()`` — zero namespaces. ``ReplayStateStore.__init__``
  fails fast with ``ShadowContractError``; ``RuntimeUnitOfWork.commit`` raises
  ``StateCellError`` on every replay-head / index / operation / prompt CAS.

**R24 — why this has to happen at the single startup call site.**
``bind_process_durable_stores_and_scan`` is idempotent per process (``if
_PROCESS_STORES is not None: return _PROCESS_STORES``), so a later call *with* kwargs
is a silent no-op; and ``RuntimeStores`` freezes ``frozenset(allowed_cell_namespaces)``
at construction behind a read-only property with no setter. The registry half is
post-hoc fixable (``SchemaRegistryResolver.register`` is idempotent and unsealed) but
the namespace half is not, and building a *second* ``DurableRuntimeStores`` over the
same root is hazardous (each owns an independent ``_commit_seq`` and its own folded
backend — two instances over one root diverge). So: one binding, done right, first.

**R23 — the union.** :data:`PRODUCTION_CELL_NAMESPACES` is *derived* from the owning
modules' own constants, never hand-listed. It deliberately includes
``worker.PROMPT_CELL_NAMESPACE`` (``runtime.prompt.v1``), which
``_persist_prompt_record`` CAS-writes on **every LLM node attempt** and which belongs
to no Phase-3/Phase-4 union — omit it and the first LLM node dies on commit.
``tests/orchestration/test_startup_binding.py`` carries the mechanical drift guard
that re-derives the set from the package source.

Honest failure (see :func:`bind_orchestration_stores`)
------------------------------------------------------
A bind failure is never a silent skip. Every outcome lands in a typed, queryable
:func:`orchestration_store_status` record and, for the data-integrity case, a
``CRITICAL`` log plus a stderr line carrying the stable :data:`CORRUPT_MARKER`.
By default a corrupt store still lets the server boot (the 9999 process serves the
entire product; an additive subsystem's damaged journal must not take the UI down)
— but it boots *visibly* store-less, and ``GUANLAN_ORCH_STORE_STRICT=1`` turns that
into a refused boot for operators who want the harder guarantee.

This module is pure additive wiring: it consumes the sealed Phase 1–9 surfaces and
modifies none of them.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from guanlan_v2.orchestration.adapters.luozi import REPLAY_STATE_CELL_NAMESPACES
from guanlan_v2.orchestration.trial_ledger import PHASE4_STATE_CELL_NAMESPACES
from guanlan_v2.orchestration.worker import PROMPT_CELL_NAMESPACE

__all__ = [
    "PRODUCTION_CELL_NAMESPACES",
    "STORE_ROOT_ENV",
    "STRICT_ENV",
    "CORRUPT_MARKER",
    "OrchestrationStoreBootRefused",
    "build_production_resolver",
    "bind_orchestration_stores",
    "orchestration_store_status",
    "reset_status_for_tests",
]

_LOG = logging.getLogger(__name__)

#: store-root override (the 9998 verification runs point this at a temp dir).
STORE_ROOT_ENV = "GUANLAN_ORCH_STORE_ROOT"
#: opt-in: refuse the boot instead of booting visibly store-less.
STRICT_ENV = "GUANLAN_ORCH_STORE_STRICT"
#: stable, greppable operator signal for a data-integrity hard failure.
CORRUPT_MARKER = "ORCH-STORE-CORRUPT"

_DEFAULT_ROOT = "var/orchestration"

#: The production state-cell namespace union, derived from the three owning modules.
#:
#: * ``PHASE4_STATE_CELL_NAMESPACES`` (11) — itself the sealed union of the seven
#:   Phase-3 ``memory.*`` names and the four Phase-4 ``trial.*`` names (the latter
#:   covers ``optimize.EXPERIMENT_HEAD_NAMESPACE``).
#: * ``PROMPT_CELL_NAMESPACE`` (1) — ``runtime.prompt.v1``, in no phase union (R23).
#: * ``REPLAY_STATE_CELL_NAMESPACES`` (2) — the Phase-9 adapters replay head/operation.
#:
#: 14 names, canonically sorted (the store freezes it into a ``frozenset``).
PRODUCTION_CELL_NAMESPACES: tuple[str, ...] = tuple(
    sorted(
        set(PHASE4_STATE_CELL_NAMESPACES)
        | {PROMPT_CELL_NAMESPACE}
        | set(REPLAY_STATE_CELL_NAMESPACES)
    )
)


class OrchestrationStoreBootRefused(RuntimeError):
    """Strict mode (:data:`STRICT_ENV`) refused to boot over a broken durable store."""


def _blank_status() -> dict[str, Any]:
    return {
        "state": "not_attempted",   # not_attempted | bound | corrupt | failed
        "bound": False,
        "root": None,
        "cell_namespaces": [],
        "cell_namespace_count": 0,
        "registry_digests": [],
        "error_type": None,
        "error": None,
        "strict": False,
    }


_STATUS: dict[str, Any] = _blank_status()


def orchestration_store_status() -> dict[str, Any]:
    """A JSON-safe defensive copy of this process's durable-store binding outcome.

    ``state == "bound"`` is the only healthy value. Everything else means *this
    process has no orchestration durable store* — the whole point of the record is
    that an operator can tell that apart from "everything is fine" without reading
    a log line that has long since scrolled away.
    """
    return dict(_STATUS)


def reset_status_for_tests() -> None:
    """Test-only: forget the recorded outcome (the store binding itself lives in
    ``durable._PROCESS_STORES`` and is reset there)."""
    global _STATUS
    _STATUS = _blank_status()


def build_production_resolver():
    """The production schema-registry resolver: Phase-1 + Phase-2 + Phase-9 cumulative.

    Returns ``(resolver, (phase1_digest, phase2_digest, phase9_digest))``. The Phase-9
    registry is the *cumulative* chain node (Phase-1 public + Phase-2 runtime facts +
    Phase-3 data/memory + Phase-4 + Phase-5 + Phase-6 + Phase-7 + Phase-8 + Phase-9),
    so registering it covers every intermediate phase's payload contracts in one go.
    Its Phase-8 base comes from the chain's own ``PHASE9_BASE_REGISTRY_DIGEST`` — never
    a hardcoded digest, so a chain reseal can never leave a stale pin here.
    """
    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.eventstore import SchemaRegistryResolver
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    resolver = SchemaRegistryResolver()
    phase1 = default_registry()
    d1 = resolver.register(phase1)
    d2 = resolver.register(phase2_runtime_registry(phase1.registry_digest))
    d9 = resolver.register(chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST))
    return resolver, (d1, d2, d9)


def _bind_process_stores(**kwargs):
    """Seam over the sealed Task-1b entry point (kept patchable for the failure tests)."""
    from guanlan_v2.orchestration.adapters.durable import bind_process_durable_stores_and_scan

    return bind_process_durable_stores_and_scan(**kwargs)


def _shout(level: int, marker: str, message: str) -> None:
    """Log loudly AND print to stderr.

    Both, deliberately: the logger is the machine-readable channel an operator's log
    pipeline greps for, and the stderr line survives a process whose logging root was
    never configured (which is exactly the state a boot-time failure can be in).
    """
    _LOG.log(level, "[%s] %s", marker, message)
    print(f"[guanlan_v2][{marker}] {message}", file=sys.stderr, flush=True)


def bind_orchestration_stores(
    *, root: Path | str | None = None, strict: bool | None = None
) -> dict[str, Any]:
    """Bind this process's orchestration durable stores — correctly, once.

    Supplies the three things the kwarg-less call omitted: the Phase-1/2/9 resolver,
    an authoritative clock, and the full 14-name state-cell union
    (:data:`PRODUCTION_CELL_NAMESPACES`). Must be the **first** bind in the process:
    the underlying entry point is idempotent-once and the namespace set is frozen at
    construction (R24).

    Returns the :func:`orchestration_store_status` record. Never raises, except in
    strict mode (:data:`STRICT_ENV` ``=1`` or ``strict=True``), where a broken store
    raises :class:`OrchestrationStoreBootRefused` so the boot fails outright.
    """
    global _STATUS
    from guanlan_v2.orchestration.adapters.durable import DurableStoreCorrupt
    from guanlan_v2.orchestration.runtime_clock import SystemClock

    if strict is None:
        strict = os.environ.get(STRICT_ENV) == "1"
    resolved_root = Path(root) if root is not None else Path(
        os.environ.get(STORE_ROOT_ENV) or _DEFAULT_ROOT)

    try:
        resolver, digests = build_production_resolver()
        stores = _bind_process_stores(
            root=resolved_root,
            resolver=resolver,
            clock=SystemClock(),
            allowed_cell_namespaces=PRODUCTION_CELL_NAMESPACES,
        )
    except DurableStoreCorrupt as exc:
        _STATUS = dict(
            _blank_status(), state="corrupt", root=str(resolved_root),
            error_type=type(exc).__name__, error=str(exc), strict=bool(strict))
        _shout(
            logging.CRITICAL, CORRUPT_MARKER,
            f"the orchestration durable store at {resolved_root} is CORRUPT and was "
            f"NOT bound — this process has NO orchestration durable store "
            f"({type(exc).__name__}: {exc}). Inspect the journal by hand; do not "
            f"delete it blind. Set {STRICT_ENV}=1 to refuse the boot instead.")
        if strict:
            raise OrchestrationStoreBootRefused(
                f"{CORRUPT_MARKER}: refusing to boot over a corrupt orchestration "
                f"durable store at {resolved_root} ({exc})") from exc
        return orchestration_store_status()
    except Exception as exc:  # noqa: BLE001 — recorded + shouted, never swallowed
        _STATUS = dict(
            _blank_status(), state="failed", root=str(resolved_root),
            error_type=type(exc).__name__, error=str(exc), strict=bool(strict))
        _shout(
            logging.ERROR, "ORCH-STORE-FAILED",
            f"binding the orchestration durable store at {resolved_root} failed — this "
            f"process has NO orchestration durable store ({type(exc).__name__}: {exc})")
        if strict:
            raise OrchestrationStoreBootRefused(
                f"ORCH-STORE-FAILED: refusing to boot without an orchestration durable "
                f"store ({type(exc).__name__}: {exc})") from exc
        return orchestration_store_status()

    # Read the ACTUAL sealed values back off the store — never echo the intent.
    namespaces = sorted(stores.cells.allowed_namespaces)
    _STATUS = {
        "state": "bound",
        "bound": True,
        "root": str(getattr(stores, "root", resolved_root)),
        "cell_namespaces": namespaces,
        "cell_namespace_count": len(namespaces),
        "registry_digests": list(digests),
        "error_type": None,
        "error": None,
        "strict": bool(strict),
    }
    _LOG.info(
        "orchestration durable stores bound at %s (%d state-cell namespaces, "
        "%d schema registries)", _STATUS["root"], len(namespaces), len(digests))
    return orchestration_store_status()
