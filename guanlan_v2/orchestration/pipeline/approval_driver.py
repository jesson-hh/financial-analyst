# -*- coding: utf-8 -*-
"""Process-local carrier from a durable human approval to the owning plan runner.

The approval journal is global, but ``PlanAdmissionService`` is deliberately
run-scoped: the instance which reserved a candidate is the only instance that can
record its approval, freeze it and verify it for dispatch.  A console-wide
``PlanApprovalCoordinator`` therefore cannot safely approve arbitrary pipeline
candidates.  This module keeps a small routing table from the approval identity
``(request_id, candidate_plan_digest)`` to that owning coordinator and to the
reviewed execution closure assembled by the pipeline.

It creates no authority and contains no alternate admission implementation.  A
human decision still goes through ``PlanApprovalCoordinator.decide``; execution
still goes through ``build_production_plan_runner``.  The carrier merely prevents
the run-scoped objects from being discarded when ``POST /pipeline/start`` returns.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.runtime_clock import clock_now

__all__ = [
    "PipelineApprovalSurface",
    "PipelineApprovalSurfaceUnavailable",
    "bind_process_pipeline_approval_surface",
    "process_pipeline_approval_surface",
]


class PipelineApprovalSurfaceUnavailable(RuntimeError):
    """The production pipeline bindings have not supplied an approval surface."""


@dataclass
class _CandidateRuntime:
    coordinator: Any
    execute: Callable[[Any], Any]
    lock: threading.Lock = field(default_factory=threading.Lock)
    completed: bool = False
    result: Any = None


class PipelineApprovalSurface:
    """Route console decisions to the run-scoped owner and execute once approved."""

    def __init__(self, *, clock: Any) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._candidates: dict[tuple[str, str], _CandidateRuntime] = {}
        self._coordinators: list[Any] = []
        self._coordinator_ids: set[int] = set()
        self._console_emit_fn: Callable[[str, dict], None] | None = None
        self._resume_started = False

    @property
    def available(self) -> bool:
        return True

    @property
    def _console_emit(self):  # noqa: ANN202 - compatibility with the console carrier
        return self._console_emit_fn

    @_console_emit.setter
    def _console_emit(self, emit) -> None:  # noqa: ANN001 - injected console callback
        with self._lock:
            self._console_emit_fn = emit
            for coordinator in self._coordinators:
                coordinator._console_emit = emit

    def attach_coordinator(self, coordinator: Any) -> None:
        """Attach one run coordinator before it registers its reviewer card."""
        if coordinator is None:
            raise ValueError("a pipeline approval candidate needs its coordinator")
        with self._lock:
            identity = id(coordinator)
            if identity not in self._coordinator_ids:
                self._coordinator_ids.add(identity)
                self._coordinators.append(coordinator)
            coordinator._console_emit = self._console_emit_fn

    def bind_candidate(
        self,
        *,
        request_id: str,
        candidate_plan_digest: str,
        coordinator: Any,
        execute: Callable[[Any], Any],
    ) -> None:
        """Bind the exact candidate to its owner and post-approval runner.

        An identical route rebind is harmless while the candidate has not run.
        A completed candidate keeps its original execution result and closure so
        an HTTP retry can never cause a second dispatch.
        """
        key = (str(request_id), str(candidate_plan_digest))
        if not all(key) or not callable(execute):
            raise ValueError("a pipeline approval binding needs identity and executor")
        self.attach_coordinator(coordinator)
        with self._lock:
            current = self._candidates.get(key)
            if current is not None:
                if current.coordinator is not coordinator:
                    raise ValueError(
                        "the same pipeline approval identity was bound to two "
                        "different run coordinators"
                    )
                if not current.completed:
                    current.execute = execute
                return
            self._candidates[key] = _CandidateRuntime(
                coordinator=coordinator, execute=execute)

    def now(self):  # noqa: ANN201 - mirrors PlanApprovalCoordinator
        return clock_now(self._clock)

    def list_pending(self) -> tuple[Any, ...]:
        """Union the per-run journal folds without returning decided cards."""
        with self._lock:
            coordinators = tuple(self._coordinators)
            candidates = dict(self._candidates)
        cards: dict[tuple[str, str], Any] = {}
        for coordinator in coordinators:
            for card in coordinator.list_pending():
                key = (card.request_id, card.candidate_plan_digest)
                # A journal may contain cards from an earlier process whose
                # run-scoped admission owner is no longer reconstructible.  Do
                # not advertise those as approvable through this live surface.
                if key in candidates:
                    cards[key] = card
        for key, runtime in candidates.items():
            if runtime.coordinator.load_decision(*key) is not None:
                cards.pop(key, None)
        return tuple(cards.values())

    def load_decision(self, request_id: str, candidate_plan_digest: str):  # noqa: ANN201
        runtime = self._runtime_for(request_id, candidate_plan_digest, required=False)
        if runtime is not None:
            return runtime.coordinator.load_decision(
                request_id, candidate_plan_digest)
        # A card may be visible in a later coordinator's global journal fold even
        # before its execution route is installed.  This is read-only discovery.
        with self._lock:
            coordinators = tuple(reversed(self._coordinators))
        for coordinator in coordinators:
            decision = coordinator.load_decision(request_id, candidate_plan_digest)
            if decision is not None:
                return decision
        return None

    def decide(self, **kwargs):  # noqa: ANN201 - exact coordinator ABI passthrough
        runtime = self._runtime_for(
            kwargs.get("request_id"), kwargs.get("candidate_plan_digest"))
        return runtime.coordinator.decide(**kwargs)

    def admit_and_run(self, approval: Any, approval_event: Any) -> dict[str, Any] | None:
        """Execute one APPROVED candidate through its already-bound real runner.

        The console invokes this only after ``decide`` has durably recorded the
        decision and admission event.  The decision is rechecked here so this
        callable cannot be used as an approval shortcut.  The per-candidate lock
        makes identical console retries return the first result without a second
        dispatch; a failed attempt is deliberately retryable because the kernel
        runner is freeze-idempotent and DAG-resumable.
        """
        if getattr(approval, "decision", None) is not ApprovalDecision.APPROVED:
            return None
        request_id = str(getattr(approval, "request_id", ""))
        digest = str(getattr(approval, "candidate_plan_digest", ""))
        runtime = self._runtime_for(request_id, digest)
        with runtime.lock:
            if runtime.completed:
                return {
                    "request_id": request_id,
                    "candidate_plan_digest": digest,
                    "status": "completed",
                    "replayed": True,
                    "result": runtime.result,
                }
            result = runtime.execute(approval_event)
            runtime.result = result
            runtime.completed = True
            return {
                "request_id": request_id,
                "candidate_plan_digest": digest,
                "status": "completed",
                "replayed": False,
                "result": result,
            }

    def resume_approved_in_background(self) -> int:
        """Resume journal-approved, non-dispatched bindings on one daemon thread.

        The coordinator replay has already recreated each authoritative approval
        event.  The production runner is freeze-idempotent and DAG-resumable, so
        invoking the same candidate after a process restart resumes committed
        layers instead of minting a second plan or rerunning them.
        """
        with self._lock:
            if self._resume_started:
                return 0
            jobs = []
            for key, runtime in self._candidates.items():
                approval = runtime.coordinator.load_decision(*key)
                event = runtime.coordinator.load_approval_event(*key)
                if (getattr(approval, "decision", None) is ApprovalDecision.APPROVED
                        and event is not None):
                    jobs.append((approval, event))
            self._resume_started = True
        if not jobs:
            return 0

        def _resume() -> None:
            for approval, event in jobs:
                try:
                    self.admit_and_run(approval, event)
                except Exception:  # noqa: BLE001 - recovery failure stays loud and retryable
                    logging.getLogger(__name__).exception(
                        "approved pipeline candidate recovery failed: %s / %s",
                        getattr(approval, "request_id", None),
                        getattr(approval, "candidate_plan_digest", None))

        threading.Thread(
            target=_resume, daemon=True, name="pipeline-approval-resume").start()
        return len(jobs)

    # Lease management remains one journal-wide authority.  Any attached
    # coordinator folds the same durable lease journal; use the most recently
    # constructed one so it has the freshest pre-call fold.
    def list_leases(self, **kwargs):  # noqa: ANN201
        return self._control().list_leases(**kwargs)

    def issue_lease(self, **kwargs):  # noqa: ANN201
        return self._control().issue_lease(**kwargs)

    def revoke_lease(self, *args, **kwargs):  # noqa: ANN201
        return self._control().revoke_lease(*args, **kwargs)

    def _control(self) -> Any:
        with self._lock:
            if not self._coordinators:
                raise PipelineApprovalSurfaceUnavailable(
                    "no pipeline run coordinator is attached yet")
            return self._coordinators[-1]

    def _runtime_for(
        self, request_id: Any, candidate_plan_digest: Any, *, required: bool = True
    ) -> _CandidateRuntime | None:
        key = (str(request_id or ""), str(candidate_plan_digest or ""))
        with self._lock:
            runtime = self._candidates.get(key)
        if runtime is None and required:
            # Keep the console's reviewed 404 mapping by raising the coordinator's
            # public exception, not a parallel pipeline-only error vocabulary.
            from guanlan_v2.orchestration.approval import UnknownPendingCandidate

            raise UnknownPendingCandidate(
                f"no executable pipeline candidate for request {key[0]!r} / "
                f"candidate {key[1]!r}")
        return runtime


class _ProcessPipelineApprovalProxy:
    """Stable object mounted before deferred production bindings complete."""

    def __init__(self) -> None:
        self._target: PipelineApprovalSurface | None = None
        self._emit = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        with self._lock:
            return self._target is not None

    @property
    def _console_emit(self):  # noqa: ANN202
        return self._emit

    @_console_emit.setter
    def _console_emit(self, emit) -> None:  # noqa: ANN001
        with self._lock:
            self._emit = emit
            if self._target is not None:
                self._target._console_emit = emit

    def bind(self, target: PipelineApprovalSurface | None) -> None:
        with self._lock:
            self._target = target
            if target is not None:
                target._console_emit = self._emit

    def admit_and_run(self, approval: Any, approval_event: Any):  # noqa: ANN201
        """Stable callback identity for the console's early router mount."""
        return self._require().admit_and_run(approval, approval_event)

    def _require(self) -> PipelineApprovalSurface:
        with self._lock:
            target = self._target
        if target is None:
            raise PipelineApprovalSurfaceUnavailable(
                "the production pipeline approval surface is not bound")
        return target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require(), name)


_PROCESS_SURFACE = _ProcessPipelineApprovalProxy()


def process_pipeline_approval_surface() -> _ProcessPipelineApprovalProxy:
    """Return the stable process proxy used by the already-mounted console."""
    return _PROCESS_SURFACE


def bind_process_pipeline_approval_surface(
    surface: PipelineApprovalSurface | None,
) -> None:
    """Install or clear the process target (the proxy identity never changes)."""
    _PROCESS_SURFACE.bind(surface)
