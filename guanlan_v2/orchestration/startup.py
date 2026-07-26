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
record owned by the dependency-free leaf :mod:`guanlan_v2.orch_store_status` (which
holds the canonical state vocabulary — ``not_attempted`` / ``bound`` / ``unavailable``
/ ``corrupt`` / ``failed`` — and the ``/data/health`` provider), and the
data-integrity case also gets a ``CRITICAL`` log plus a stderr line carrying the
stable :data:`CORRUPT_MARKER`. ``unavailable`` is written both here (a broken/absent
``durable`` module) and by ``server.py`` (this module itself not importable).

By default a corrupt store still lets the server boot (the 9999 process serves the
entire product; an additive subsystem's damaged journal must not take the UI down)
— but it boots *visibly* store-less, and ``GUANLAN_ORCH_STORE_STRICT=1`` turns that
into a refused boot for operators who want the harder guarantee. **Strict mode is the
only way this call can ever refuse a boot**: every import and every step of the bind
sits inside the guard, so a broken ``durable.py`` degrades to ``unavailable`` instead
of propagating ``ImportError`` out of ``create_app()``.

This module is pure additive wiring: it consumes the sealed Phase 1–9 surfaces and
modifies none of them.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from guanlan_v2 import orch_store_status as _record
from guanlan_v2.orch_store_status import (  # single definition — importable without this module
    STRICT_ENV,
    OrchestrationStoreBootRefused,
)
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
    # -- R3: the adapters-router production binding (opt-in) ----------------- #
    "LAUNCHER_ENV",
    "bind_orchestration_launcher",
    "orchestration_launcher_status",
    "reset_launcher_status_for_tests",
]

_LOG = logging.getLogger(__name__)

#: store-root override (the 9998 verification runs point this at a temp dir).
STORE_ROOT_ENV = "GUANLAN_ORCH_STORE_ROOT"
# NOTE: ``STRICT_ENV`` is imported above from :mod:`guanlan_v2.orch_store_status`, which
# owns its definition AND its rationale (it refuses on every non-``bound`` outcome,
# including when the subsystem is absent). Deliberately no ``#:`` block here — a stranded
# one would merge into the next constant's docs.

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


def orchestration_store_status() -> dict[str, Any]:
    """A JSON-safe defensive copy of this process's durable-store binding outcome.

    ``state == "bound"`` is the only healthy value. Everything else means *this
    process has no orchestration durable store* — the whole point of the record is
    that an operator can tell that apart from "everything is fine" without reading
    a log line that has long since scrolled away.

    The record itself lives in the dependency-free leaf
    :mod:`guanlan_v2.orch_store_status`, which owns the canonical state vocabulary
    (including ``unavailable``, the one state only ``server.py`` can observe — it means
    *this* module could not be imported) and the ``/data/health`` provider. This is a
    convenience re-export so callers inside the orchestration package need not know
    where the record lives.
    """
    return _record.orchestration_store_status()


def reset_status_for_tests() -> None:
    """Test-only: forget the recorded outcome (the store binding itself lives in
    ``durable._PROCESS_STORES`` and is reset there)."""
    _record.reset_for_tests()


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

    Each channel is independently guarded and this function NEVER raises. That matters
    in exactly one place: ``_degrade`` shouts *before* the strict refusal, so a closed
    or broken stderr would otherwise replace ``OrchestrationStoreBootRefused`` with an
    ``OSError``/``ValueError`` — which the call site records as ``failed`` and boots
    through, silently inverting the operator's strict assertion.
    """
    try:
        _LOG.log(level, "[%s] %s", marker, message)
    except Exception:  # noqa: BLE001 — a broken log handler must not eat the refusal
        pass
    try:
        print(f"[guanlan_v2][{marker}] {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — a closed stderr must not eat the refusal
        pass


def bind_orchestration_stores(
    *, root: Path | str | None = None, strict: bool | None = None
) -> dict[str, Any]:
    """Bind this process's orchestration durable stores — correctly, once.

    Supplies the three things the kwarg-less call omitted: the Phase-1/2/9 resolver,
    an authoritative clock, and the full 14-name state-cell union
    (:data:`PRODUCTION_CELL_NAMESPACES`). Must be the **first** bind in the process:
    the underlying entry point is idempotent-once and the namespace set is frozen at
    construction (R24).

    Returns the :func:`orchestration_store_status` record. **Never raises** — except
    in strict mode (:data:`STRICT_ENV` ``=1`` or ``strict=True``), where a store that
    could not be bound raises :class:`OrchestrationStoreBootRefused` so the boot fails
    outright. Strict mode is the *only* path by which this call can refuse a boot; the
    default server start must never depend on this optional machinery, so **every**
    import and every step below sits inside the guard. In particular the
    ``durable`` / ``runtime_clock`` imports are deliberately *inside* the try: this
    module's own top-level imports do not pull in ``durable``, so a broken ``durable.py``
    would otherwise propagate ``ImportError`` out of ``create_app()`` and take the whole
    9999 process (选股 / 落子 / 帷幄 / datafeed / MCP) down with it — the exact failure
    the old kwarg-less code caught and survived.
    """
    if strict is None:
        strict = os.environ.get(STRICT_ENV) == "1"
    # Placeholder so `_degrade` below can always stamp a root, even if resolving one is
    # what failed. `strict` is resolved FIRST and unconditionally, so every recorded
    # state carries the operator's real flag — a record claiming `strict: false` while
    # the flag is on would be a small lie, and this project does not ship those.
    resolved_root: Any = "<unresolved store root>"

    def _degrade(state: str, marker: str, level: int, what: str, exc: BaseException):
        _record.record_status(dict(
            _record.blank_status(), state=state, root=str(resolved_root),
            error_type=type(exc).__name__, error=str(exc), strict=bool(strict)))
        _shout(level, marker, what)   # guaranteed not to raise (see _shout)
        _record.refuse_if_strict(bool(strict), marker, exc, f"at {resolved_root}")
        return orchestration_store_status()

    # Step 0 — resolving the root is guarded too (round-4 Minor 1). It is the only work
    # that used to sit outside every try, so a `root=` that is not path-like raised
    # TypeError straight out of this function and into the call site's defence-in-depth
    # branch, which recorded `failed` WITHOUT the strict flag and booted through.
    try:
        resolved_root = Path(root) if root is not None else Path(
            os.environ.get(STORE_ROOT_ENV) or _DEFAULT_ROOT)
    except Exception as exc:  # noqa: BLE001 — an unusable root is a recorded failure
        return _degrade(
            "failed", "ORCH-STORE-FAILED", logging.ERROR,
            f"the orchestration durable-store root could not be resolved — this process "
            f"has NO orchestration durable store ({type(exc).__name__}: {exc}). "
            f"Set {STRICT_ENV}=1 to refuse the boot instead.", exc)

    # Step 1 — the imports, INSIDE the guard on purpose (see the docstring). This
    # module's own top-level imports do NOT pull in `durable`, so a broken durable.py
    # would otherwise escape as ImportError and kill the whole server process.
    try:
        from guanlan_v2.orchestration.adapters.durable import DurableStoreCorrupt
        from guanlan_v2.orchestration.runtime_clock import SystemClock
    except Exception as exc:  # noqa: BLE001 — absent/broken machinery, not a fatality
        return _degrade(
            "unavailable", "ORCH-STORE-UNAVAILABLE", logging.ERROR,
            f"the orchestration durable-store machinery is not importable — this "
            f"process has NO orchestration durable store, but the rest of the server "
            f"starts normally ({type(exc).__name__}: {exc}). Set {STRICT_ENV}=1 to "
            f"refuse the boot instead.", exc)

    # Step 2 — the bind itself. `DurableStoreCorrupt` is safely nameable here.
    try:
        resolver, digests = build_production_resolver()
        stores = _bind_process_stores(
            root=resolved_root,
            resolver=resolver,
            clock=SystemClock(),
            allowed_cell_namespaces=PRODUCTION_CELL_NAMESPACES,
        )
    except DurableStoreCorrupt as exc:
        return _degrade(
            "corrupt", CORRUPT_MARKER, logging.CRITICAL,
            f"the orchestration durable store at {resolved_root} is CORRUPT and was "
            f"NOT bound — this process has NO orchestration durable store "
            f"({type(exc).__name__}: {exc}). Inspect the journal by hand; do not "
            f"delete it blind. Set {STRICT_ENV}=1 to refuse the boot instead.", exc)
    except Exception as exc:  # noqa: BLE001 — recorded + shouted, never swallowed
        return _degrade(
            "failed", "ORCH-STORE-FAILED", logging.ERROR,
            f"binding the orchestration durable store at {resolved_root} failed — this "
            f"process has NO orchestration durable store ({type(exc).__name__}: {exc}). "
            f"Set {STRICT_ENV}=1 to refuse the boot instead.", exc)

    # Read the ACTUAL sealed values back off the store — never echo the intent.
    namespaces = sorted(stores.cells.allowed_namespaces)
    status = _record.record_status({
        "state": "bound",
        "bound": True,
        "root": str(getattr(stores, "root", resolved_root)),
        "cell_namespaces": namespaces,
        "cell_namespace_count": len(namespaces),
        "registry_digests": list(digests),
        "error_type": None,
        "error": None,
        "strict": bool(strict),
    })
    _LOG.info(
        "orchestration durable stores bound at %s (%d state-cell namespaces, "
        "%d schema registries)", status["root"], len(namespaces), len(digests))
    return status


# =========================================================================== #
# R3 — the adapters-router production binding (opt-in)                         #
# =========================================================================== #
#: opt-in switch. Default OFF ⇒ **the six ``/orchestration`` router routes are
#: byte-unchanged**, keeping their honest ``*_unwired`` 503s — the same idiom
#: ``GUANLAN_SEATS_WATCH`` / ``GUANLAN_REGEN_DAILY`` use. Deliberately NOT the wider
#: claim "production is byte-unchanged": ``GET /orchestration/launcher_status``
#: registers on **every** boot (it must, or an operator could not ask why the
#: subsystem is off), and ``server.py``'s ``plan_approval_actor`` line runs whenever
#: ``_console_kw`` is non-empty — inert today only because the Phase-7 coordinator is
#: ``None``, NOT because this flag is off. It is opt-in rather than always-on because binding
#: it makes a real, previously-refusing surface live in a process that also serves
#: 选股 / 落子 / 帷幄, and because the launcher's own admission side is not finished
#: (see the R3 launcher report): turning on half a subsystem must be a decision, not
#: a side effect of a restart.
LAUNCHER_ENV = "GUANLAN_ORCH_LAUNCHER"

#: the queryable record, same shape discipline as the store status.
_LAUNCHER_STATUS: dict[str, Any] = {
    "state": "not_attempted", "replay_state_store": False, "replay_bindings": False,
    "shadow_wakeup_context": False, "schedule_count": 0,
    "error_type": None, "error": None, "notes": [],
}


def orchestration_launcher_status() -> dict[str, Any]:
    """A defensive copy of this process's launcher-binding record."""
    record = dict(_LAUNCHER_STATUS)
    record["notes"] = list(record["notes"])
    return record


def reset_launcher_status_for_tests() -> None:
    """Test seam: forget the record (never called in production)."""
    global _LAUNCHER_STATUS
    _LAUNCHER_STATUS = {
        "state": "not_attempted", "replay_state_store": False, "replay_bindings": False,
        "shadow_wakeup_context": False, "schedule_count": 0,
        "error_type": None, "error": None, "notes": [],
    }


def _record_launcher(record: dict[str, Any]) -> dict[str, Any]:
    global _LAUNCHER_STATUS
    _LAUNCHER_STATUS = dict(record)
    _LAUNCHER_STATUS["notes"] = list(record.get("notes", ()))
    return orchestration_launcher_status()


def bind_orchestration_launcher(
    *, enabled: bool | None = None, stores: Any = None,
) -> dict[str, Any]:
    """R3 — bind the adapters router to this process's durable stores. Never raises.

    Before this, every ``/orchestration/*`` route answered ``*_unwired``: the router
    reads ``AdaptersRouterDeps`` from a process container that nothing in production
    ever filled (``set_adapters_router_deps`` had no production caller). This is that
    caller.

    What it binds, and what it deliberately does NOT:

    * ``replay_state_store`` — a real :class:`ReplayStateStore` over the **already
      bound** process stores (never a second store: the durable bind is
      idempotent-once and its namespace set is frozen at construction, so a second
      one over the same root is the R24 hazard). ``GET /replay/state`` and
      ``GET /replay/curves`` become real, read-only answers;
    * ``clock`` — the authoritative :class:`SystemClock`;
    * ``schedule_registry`` / ``replay_requests`` / ``weiwo_requests`` /
      ``weiwo_receipts`` — the process-level carriers ``POST /replay/start`` and the
      两 weiwo routes read. An empty schedule registry is the honest starting state:
      a route asked for an unregistered schedule refuses by name;
    * **NOT ``replay_bindings``** — a :class:`ReplayRuntimeBindings` is *run-scoped*
      (it carries a per-run coordinator, budget ledger and intent ledger), so there
      is no process-level value that is not an invention. ``POST /replay/wakeup``
      therefore keeps its honest 503, and the record says so rather than leaving a
      reader to discover it;
    * **NOT the shadow-wakeup context provider** — it must return
      ``(store, bindings, now)``, and the same run-scoped ``bindings`` are missing.
      Binding a provider that returned ``None`` for them would turn an honest 503
      into a crash inside a scheduled playbook.

    Outcomes: ``disabled`` (opt-in switch off) · ``unavailable`` (no bound process
    store — the store bind already shouted) · ``failed`` (a wiring bug; recorded, and
    the router keeps its 503s) · ``bound``.
    """
    import os

    if enabled is None:
        enabled = os.environ.get(LAUNCHER_ENV) == "1"
    if not enabled:
        return _record_launcher(dict(
            _LAUNCHER_STATUS, state="disabled", error_type=None, error=None,
            notes=[f"{LAUNCHER_ENV} is not '1': the /orchestration routes keep their "
                   "honest *_unwired 503s and production is byte-unchanged"]))

    notes: list[str] = []
    try:
        from guanlan_v2.orchestration.adapters import chain
        from guanlan_v2.orchestration.adapters.api import (
            AdaptersRouterDeps,
            set_adapters_router_deps,
        )
        from guanlan_v2.orchestration.adapters.durable import process_durable_stores
        from guanlan_v2.orchestration.adapters.luozi import ReplayStateStore
        from guanlan_v2.orchestration.runtime_clock import SystemClock
        from guanlan_v2.orchestration.shadow import DecisionScheduleRegistry

        bound = stores if stores is not None else process_durable_stores()
        if bound is None:
            return _record_launcher(dict(
                _LAUNCHER_STATUS, state="unavailable",
                notes=["no orchestration durable store is bound in this process, so "
                       "the adapters router cannot be wired; see "
                       "GET /orchestration/store_status"]))

        clock = SystemClock()
        registry = chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)
        replay_store = ReplayStateStore(
            payload_store=bound.payloads, state_cells=bound.cells, registry=registry,
            clock=clock, uow_factory=lambda: bound.unit_of_work,
            event_store=bound.events)
        schedules = DecisionScheduleRegistry()
        notes.append(
            "replay_bindings is NOT bound: a ReplayRuntimeBindings is run-scoped "
            "(per-run coordinator + budget + intent ledger), so POST /replay/wakeup "
            "keeps its honest clock/bindings 503")
        notes.append(
            "the shadow-wakeup context provider is NOT bound for the same reason "
            "(it must return (store, bindings, now))")
        notes.append(
            "the schedule registry starts EMPTY: POST /replay/start refuses an "
            "unregistered schedule by name, which is the honest starting state")
        set_adapters_router_deps(AdaptersRouterDeps(
            schedule_registry=schedules, replay_state_store=replay_store,
            replay_bindings=None, clock=clock, replay_requests={},
            weiwo_requests={}, weiwo_receipts={}))
    except Exception as exc:  # noqa: BLE001 — a wiring bug must never kill create_app()
        _shout(logging.ERROR, "ORCH-LAUNCHER-FAILED",
               f"the orchestration adapters-router binding raised "
               f"({type(exc).__name__}: {exc}); the /orchestration routes keep their "
               "honest *_unwired 503s and this process serves everything else "
               "normally")
        return _record_launcher(dict(
            _LAUNCHER_STATUS, state="failed", error_type=type(exc).__name__,
            error=str(exc), notes=notes))

    _LOG.info("orchestration adapters router bound (replay_state_store live, "
              "replay_bindings deliberately absent)")
    return _record_launcher({
        "state": "bound", "replay_state_store": True, "replay_bindings": False,
        "shadow_wakeup_context": False, "schedule_count": 0,
        "error_type": None, "error": None, "notes": notes,
    })
