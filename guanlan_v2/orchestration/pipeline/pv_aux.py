# -*- coding: utf-8 -*-
"""L2-b · Task 6 — the two pv aux seats' trusted deterministic handlers.

``pv.price_action`` and ``pv.microstructure`` are the deep lane's DETERMINISTIC
auxiliary readers (``lane_catalog.py``): ``inputs=()``, ``tool_calls=OPTIONAL``,
a data capability allowlist, and a ``handler_ref`` pointing at a sealed catalog
handler material.  Until this module they had NO production factory binding
(the Task-0 D-E gap), so a deep run's aux node died at
``CatalogMaterialError: no handler factory bound`` — an outcome that tells a
reader nothing about the world.

The D-E branch this module IS (verified at source, 2026-08-01)
---------------------------------------------------------------
The reviewed handler sources exist **only as sealed catalog material bytes**:
``config/orchestration/materials/handlers/pv.price_action.py`` and
``…/pv.microstructure.py``, loaded by ``lane_catalog.load_pv_lane_materials``
through ``build_text_material(raw=path.read_bytes())``.  They are NOT importable
code — ``config/`` carries no ``__init__.py`` and ``pv.price_action`` is not a
legal module name — and no ``guanlan_v2`` module exports either handler.  Sealed
material bytes are a digest-attested CONTRACT artifact, never a code loader, so
they are never ``exec``-ed.  Per the Task-0 D-E recon that leaves exactly one
lawful shape: a thin **trusted wrapper** module, bound by the reviewed
``TrustedFactoryRegistry.register_handler`` mechanism against the snapshot's own
handler ``ContentRef`` — bind at source, never execute material.

The ruling: honest refusal over plausible empty
-----------------------------------------------
In L2-b both seats reach their handler with **nothing to compute over**: they
carry no plan-fed inputs, and the reviewed Phase-3 prefetch binding grants them
no data row (grants are L3's scope), so their data bridge freezes the
catalog-licensed EMPTY contribution (Task 3/4).  A handler that answered that
with an empty ``PriceActionFeatureReport`` / ``MicrostructureReport`` would read
downstream as *"computed: no patterns found"* — a fabricated reading of a market
nobody looked at.  So instead the handler raises :class:`AuxDataUngranted`,
which NAMES the seat, the capabilities it holds and the L3 grant gap.  The
executor turns that into an honest FAILED node (``reason_code="handler_error"``,
``error_type="AuxDataUngranted"``, the typed code leading the reason string);
both seats are non-trunk, so the run degrades without blocking, and the
inter-node inliner states the absence downstream (``status="absent"`` +
``TRUSTED_UPSTREAM_ABSENT_TEXT``) rather than letting a debate seat imagine the
reading.

L3 EXIT GATE (named here, not pre-empted)
-----------------------------------------
The day ``_REVIEWED_INTEGRATION_GRANTS`` (``data/catalog.py``) grows a row for
either seat, the bridge starts feeding real ``DataResult`` refs — and THIS
handler still has no code to read them.  It says so, typed
(:class:`AuxComputeNotWired`), rather than emitting a report that silently
ignores the data it was handed.  That is the tripwire L3's exit gate trips: at
that point the wrapper binds the reviewed compute spines at source — the
material bytes name them, ``guanlan_v2.seats.price_action.compute_pa_features``
for the geometry seat and the stdlib projection for the microstructure seat —
and both refusal arms narrow to the genuinely-rowless case.  Nothing here is
half-wired toward that: L2-b's handler computes nothing and therefore imports
no compute spine at all.

This module is import-safe and pure: stdlib only, zero engine / datafeed /
network / LLM import, zero I/O.
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "AUX_DATA_UNGRANTED_CODE",
    "AUX_COMPUTE_NOT_WIRED_CODE",
    "PV_AUX_HANDLER_IDS",
    "PvAuxHandlerError",
    "AuxDataUngranted",
    "AuxComputeNotWired",
    "build_pv_aux_handler_factory",
    "pv_aux_handler_registry",
]

#: the typed refusal codes.  ``aux_data_ungranted`` leads the reason string the
#: executor seals, so the node's outcome is readable without unpacking the
#: exception type (``worker.py``'s deterministic branch stamps a fixed
#: ``reason_code="handler_error"`` for ANY handler exception and carries the
#: typed identity in ``error_type`` + ``reason`` — the two channels this uses).
AUX_DATA_UNGRANTED_CODE = "aux_data_ungranted"
AUX_COMPUTE_NOT_WIRED_CODE = "aux_compute_not_wired"

#: worker id -> its sealed catalog handler material id (``lane_catalog._PV_ROWS``).
#: Registration is keyed by the SNAPSHOT's own ``ContentRef`` for these ids —
#: never by an id string alone and never by a digest computed here.
PV_AUX_HANDLER_IDS: dict[str, str] = {
    "pv.price_action": "handler.pv.price_action",
    "pv.microstructure": "handler.pv.microstructure",
}

Factory = Callable[..., Any]


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class PvAuxHandlerError(RuntimeError):
    """Base of every typed refusal a pv aux handler may raise."""

    reason_code = "pv_aux_handler_error"

    def __init__(self, message: str, *, worker_id: str = "",
                 capability_ids: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.worker_id = worker_id
        self.capability_ids = tuple(capability_ids)


class AuxDataUngranted(PvAuxHandlerError):
    """No data reached the seat, because no data was ever granted to it.

    The L2-b end state for both pv aux nodes: the seat holds data capabilities,
    the sealed prefetch binding grants it no row, its bridge completed the
    licensed EMPTY — so there is nothing to compute over and the handler refuses
    rather than emitting a report over nothing.
    """

    reason_code = AUX_DATA_UNGRANTED_CODE


class AuxComputeNotWired(PvAuxHandlerError):
    """Data DID arrive — and this L2-b handler has no code to read it.

    The L3 tripwire (see the module docstring's exit gate).  Refusing here is
    the only honest answer: a report that ignored the granted rows would claim a
    reading that was never performed.
    """

    reason_code = AUX_COMPUTE_NOT_WIRED_CODE


# =========================================================================== #
# the trusted factories (the two-stage handler ABI)                            #
# =========================================================================== #
def _held_capability_ids(worker: Any, worker_id: str) -> tuple[str, ...]:
    """The capability ids the SEALED WorkerSpec grants this seat, sorted.

    Read off the catalog's own ``capability_allowlist`` so the refusal can never
    drift from what the seat actually holds.  A factory invoked without its
    WorkerSpec (or with another seat's) refuses at BIND time — naming an
    invented allowlist would be exactly the fabrication this task exists to
    forbid.

    A bind-time refusal is DELIBERATELY not the aux-node outcome: it fires only
    on catalog drift or a misused factory, i.e. a wiring failure, and it is
    raised where ``worker.py`` resolves the factory (outside the deterministic
    branch's ``try``), so it propagates and ``dag.run_plan`` records it as
    ``executor_exception``.  That is the right shape — a broken wire must not
    masquerade as a statement about the market.
    """
    if worker is None:
        raise PvAuxHandlerError(
            f"{worker_id} handler factory was invoked without its WorkerSpec; "
            "the refusal it exists to raise must name the capabilities the "
            "sealed seat actually holds, and this wrapper will not guess them",
            worker_id=worker_id)
    seen_id = getattr(worker, "id", None)
    if seen_id != worker_id:
        raise PvAuxHandlerError(
            f"the {PV_AUX_HANDLER_IDS[worker_id]!r} factory was invoked for "
            f"worker {seen_id!r}, not {worker_id!r}; a handler bound to one "
            "sealed seat never serves another",
            worker_id=worker_id)
    allowlist = tuple(getattr(worker, "capability_allowlist", ()) or ())
    if not allowlist:
        raise PvAuxHandlerError(
            f"{worker_id}'s WorkerSpec carries an EMPTY capability allowlist; "
            "the sealed roster gives both pv aux seats data capabilities, so "
            "this is catalog drift, not a runnable seat",
            worker_id=worker_id)
    return tuple(sorted(str(cap.id) for cap in allowlist))


def _bind_handler(*, worker_id: str, capability_ids: tuple[str, ...]):
    """The inner executor-shaped handler (stage two of the handler ABI).

    ``worker.py`` calls it ``handler(node=…, input_snapshot=…, contributions=…,
    data_result_refs=…)``; ``data_result_refs`` is the executor's own merge over
    every bridge contribution, so it is the single honest answer to "did any
    data reach this node".
    """
    held = ", ".join(capability_ids)

    def handler(*, node=None, input_snapshot=None, contributions=(),
                data_result_refs=()):
        rows = tuple(data_result_refs or ())
        if rows:
            raise AuxComputeNotWired(
                f"{AUX_COMPUTE_NOT_WIRED_CODE}: {worker_id} was handed "
                f"{len(rows)} data result(s) over {held}, but the L2-b handler "
                "binds no compute spine for them yet (that binding is L3's "
                "exit gate); refusing to emit a report that ignores the data "
                "it was given",
                worker_id=worker_id, capability_ids=capability_ids)
        raise AuxDataUngranted(
            f"{AUX_DATA_UNGRANTED_CODE}: {worker_id} holds {held} but the "
            "sealed prefetch binding grants it no row (L3); no data reached "
            "this node; refusing to emit a report computed over nothing",
            worker_id=worker_id, capability_ids=capability_ids)

    return handler


def build_pv_aux_handler_factory(worker_id: str) -> Factory:
    """The trusted factory for ONE pv aux seat, in the reviewed two-stage shape.

    ``TrustedFactoryRegistry`` calls it ``factory(worker=…, resolved=…)``
    (``worker.py``'s deterministic branch) and the returned inner callable is
    the handler.  Nothing is captured from the runtime: the refusal is built
    from the invoked seat's OWN sealed WorkerSpec.
    """
    if worker_id not in PV_AUX_HANDLER_IDS:
        raise PvAuxHandlerError(
            f"{worker_id!r} is not one of the two reviewed pv aux seats "
            f"{sorted(PV_AUX_HANDLER_IDS)}; this wrapper binds no other seat",
            worker_id=worker_id)

    def factory(*, worker=None, resolved=None):
        return _bind_handler(
            worker_id=worker_id,
            capability_ids=_held_capability_ids(worker, worker_id))

    return factory


def pv_aux_handler_registry() -> dict[str, Factory]:
    """``handler material id -> trusted factory`` for both pv aux seats.

    The mapping ``build_production_catalog_runtime`` merges into its
    ``handler_registry`` — the Task-11 ``cand.*`` idiom, one level up.
    """
    return {
        handler_id: build_pv_aux_handler_factory(worker_id)
        for worker_id, handler_id in PV_AUX_HANDLER_IDS.items()
    }
