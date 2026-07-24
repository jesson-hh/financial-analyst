# -*- coding: utf-8 -*-
"""Phase 9 · Task 1b — durable jsonl/file backend for the Phase 2 store ABIs.

Phase 7 states outright that the Phase 2 in-memory stores make **no** durability
claim, yet Task 6 parks ``WAITING_FOR_MATURITY`` heads that must survive to the
next day's wakeup. This module supplies file-backed implementations of the exact
Phase 2 store ABIs (:mod:`guanlan_v2.orchestration.eventstore`) with
byte-equivalent semantics, following the Phase 7 journal discipline
(:mod:`guanlan_v2.orchestration.approval`): append-only, fsync per append,
torn-tail drop with a logged badge, mid-file corruption ⇒ a typed hard failure,
fold-on-open.

Design — write-through over the reviewed in-memory kernel
--------------------------------------------------------
The durable stores are **not** a parallel reimplementation of the store
semantics: they subclass the reviewed in-memory stores and reuse their exact
apply logic (``_apply_payload_put`` / ``_append_event`` / ``RuntimeUnitOfWork.
_apply_batch``) so every behavioural assertion that holds in memory holds here.
The one thing they add is a durable log: on construction the log is *folded* back
into the shared in-memory backend (rebuilding the payloads / event journal /
dual cursors / state-cell heads byte-identically), and every mutating operation
appends its produced facts to disk **before** publishing the in-memory
transition (persist-then-publish).

Crash atomicity across the three file families (the brief's
``payloads/<ns>/<digest>`` content files, ``events/<partition>.jsonl`` journals
and ``state_cells.jsonl``) is provided by a single per-commit **barrier**:
each mutating operation is assigned a globally-monotonic ``commit_seq``, writes
all of its data rows (each tagged with that seq), then fsyncs one barrier row to
``commits.jsonl`` **last**. Fold trusts a data row only if its ``commit_seq`` was
barrier-committed, so a crash before the barrier fsync drops the whole commit
(all-or-none) while content-addressed payload files remain harmless idempotent
orphans. ``commit_seq`` values are never reused, so an un-barriered orphan can
never be resurrected by a later commit.

Durability scope (honest, matching the interrupt model)
-------------------------------------------------------
Restart survival is guaranteed for **run events, payloads and state-cell heads**
(invariant 3). Budget events and whole-batch idempotency live only in the shared
in-memory backend: they keep full in-process semantics but are intentionally
*not* restart-durable, because the startup scan never resumes an in-flight
attempt — it marks it ``interrupted`` (a terminal ``RunResult``), and a fresh run
uses a fresh budget. This keeps the durable surface minimal and the honesty red
line intact: nothing is displayed as running that is not.

Startup interrupt scan
----------------------
:func:`scan_and_mark_interrupted` folds the journals and, for every run with
node-execution events but no terminal ``RunCompleted``/``RunFailed``/
``RunCancelled`` event and no experiment-lifecycle event, appends a terminal
``RunResult(terminal_status="cancelled")`` through the **exact** reviewed Phase 2
record path (``RuntimeUnitOfWork.commit`` with the ``runresult:{run_id}``
idempotency-key family the Phase 2 DAG runner uses). It never re-executes,
re-admits or fabricates progress; a re-scan is idempotent (the run now carries
its terminal event); parked ``WAITING_FOR_MATURITY`` heads (state cells) are never
touched.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from guanlan_v2.orchestration.events import EventType, RunEvent
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    EventStore,
    IdempotencyConflict,
    PayloadPutCommand,
    PayloadStore,
    RuntimeBatch,
    RuntimeStateCellStore,
    RuntimeStores,
    RuntimeUnitOfWork,
    SchemaRegistryResolver,
    StagedPayloadKey,
    _apply_payload_put,
    _append_event,
    _Backend,
    _payload_content_digest,
    _Shared,
    _StoredBatch,
    _StoredPayload,
)
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, SystemClock
from guanlan_v2.orchestration.runtime_contracts import RunResult, phase2_runtime_registry
from guanlan_v2.orchestration.schema_registry import default_registry

__all__ = [
    "DurableStoreError",
    "DurableStoreCorrupt",
    "PayloadWriteConflict",
    "FilePayloadStore",
    "JsonlEventStore",
    "JsonlStateCellStore",
    "DurableRuntimeStores",
    "build_durable_runtime_stores",
    "scan_and_mark_interrupted",
    "bind_process_durable_stores_and_scan",
    "process_durable_stores",
]

_LOG = logging.getLogger(__name__)

_RUN_RESULT_SCHEMA_REF = SchemaRef(name="RunResult", version="1")

#: run-terminal event types — their presence means a run finished (never interrupt).
_TERMINAL_RUN_EVENTS = frozenset(
    {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
)


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class DurableStoreError(Exception):
    """Base for every durable-store failure."""


class DurableStoreCorrupt(DurableStoreError):
    """The durable log is unreadable beyond a tolerated torn final line.

    An earlier malformed row, a payload file whose bytes no longer recompute to
    their declared content digest, or an un-reconstructable event/cell — none are
    silently skipped (mirrors :class:`~guanlan_v2.orchestration.approval.ApprovalStoreCorrupt`).
    """


class PayloadWriteConflict(DurableStoreError):
    """A content-addressed payload file on disk differs from a write of the same
    digest — the write-once integrity guarantee is violated (never overwritten)."""


# --------------------------------------------------------------------------- #
# _DurableLog — file layout + append + fold                                   #
# --------------------------------------------------------------------------- #
def _oid_num(object_id: str) -> int:
    """The numeric suffix of a ``payload-<n>`` object id (0 if unparseable)."""
    _, _, tail = object_id.rpartition("-")
    try:
        return int(tail)
    except ValueError:  # pragma: no cover - defensive
        return 0


class _DurableLog:
    """Owns the on-disk layout and the append/fold discipline for one store root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.events_dir = self.root / "events"
        self.payloads_dir = self.root / "payloads"
        self.payload_index = self.payloads_dir / "_index.jsonl"
        self.state_cells_path = self.root / "state_cells.jsonl"
        self.commits_path = self.root / "commits.jsonl"
        self._commit_seq = 0  # globally-monotonic; set at fold, bumped per commit

    # -- commit-seq allocation ------------------------------------------- #
    def next_commit_seq(self) -> int:
        """Allocate the next never-reused commit sequence (called under the store lock)."""
        self._commit_seq += 1
        return self._commit_seq

    # -- low-level io ----------------------------------------------------- #
    @staticmethod
    def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with open(path, "ab") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """Parse an append-only jsonl file, tolerating exactly a torn final line.

        A malformed *final* line (an interrupted append) is dropped with a warning
        badge; any *earlier* malformed line is a :class:`DurableStoreCorrupt` hard
        failure (no silent skip). Mirrors the Phase 7 approval journal fold.
        """
        if not path.exists():
            return []
        raw = path.read_bytes().decode("utf-8")
        if not raw:
            return []
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]  # a clean trailing newline, not a torn tail
        rows: list[dict[str, Any]] = []
        n = len(lines)
        for idx, line in enumerate(lines):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if idx == n - 1:
                    _LOG.warning(
                        "dropping torn final line in %s (interrupted append)", path)
                    break
                raise DurableStoreCorrupt(
                    f"malformed (non-JSON) line at position {idx} in {path}")
        return rows

    # -- write-once content-addressed payloads --------------------------- #
    def _write_payload_content(self, stored: _StoredPayload) -> None:
        path = self.payloads_dir / stored.namespace / f"{stored.content_digest}.json"
        canonical = json.dumps(
            stored.model.model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if path.exists():
            if path.read_bytes() != canonical:
                raise PayloadWriteConflict(
                    f"content-addressed payload {stored.namespace}/{stored.content_digest} "
                    "on disk differs from the write (write-once integrity)")
            return  # idempotent identical write-once
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(canonical)
            fh.flush()
            os.fsync(fh.fileno())

    # -- commit persistence (payloads → events → cells → barrier) -------- #
    def persist_commit(
        self,
        seq: int,
        *,
        payloads: list[tuple[str, _StoredPayload, str, SchemaRef]] = (),
        events: list[tuple[RunEvent, int]] = (),
        cells: list[tuple[str, str, TypedPayloadRef]] = (),
    ) -> None:
        """Persist one commit's facts then its barrier row (the durability point)."""
        for object_id, stored, idem, schema_ref in payloads:
            self._write_payload_content(stored)  # write-once (verify byte-identity)
            self._append_jsonl(self.payload_index, {
                "seq": seq,
                "object_id": object_id,
                "namespace": stored.namespace,
                "digest": stored.content_digest,
                "schema": {"name": schema_ref.name, "version": schema_ref.version},
                "registry_digest": stored.registry_digest,
                "idempotency_key": idem,
            })
        for event, ordv in events:
            self._append_jsonl(self.events_dir / f"{event.partition}.jsonl", {
                "seq": seq, "ord": ordv,
                "event": event.model_dump(mode="json"),
            })
        for namespace, key_digest, tref in cells:
            self._append_jsonl(self.state_cells_path, {
                "seq": seq, "namespace": namespace, "key_digest": key_digest,
                "typed_ref": tref.model_dump(mode="json"),
            })
        # barrier LAST — its fsync is what commits the whole set.
        self._append_jsonl(self.commits_path, {"seq": seq})

    # -- fold-on-open ---------------------------------------------------- #
    def fold_into(self, shared: _Shared, resolver: SchemaRegistryResolver) -> None:
        """Rebuild the shared in-memory backend from the durable log (fail-closed)."""
        committed, max_seq = self._read_commit_barriers()
        backend = shared.backend  # the fresh empty _Backend from RuntimeStores.__init__
        max_seq = max(max_seq, self._fold_payloads(backend, resolver, committed))
        max_seq = max(max_seq, self._fold_events(backend, committed))
        max_seq = max(max_seq, self._fold_cells(backend, committed))
        self._commit_seq = max_seq

    def _read_commit_barriers(self) -> tuple[frozenset[int], int]:
        committed: set[int] = set()
        max_seq = 0
        for row in self._read_jsonl(self.commits_path):
            seq = int(row["seq"])
            committed.add(seq)
            max_seq = max(max_seq, seq)
        return frozenset(committed), max_seq

    def _fold_payloads(
        self, backend: _Backend, resolver: SchemaRegistryResolver, committed: frozenset[int]
    ) -> int:
        max_seq = 0
        payload_seq = 0
        for row in self._read_jsonl(self.payload_index):
            seq = int(row["seq"])
            max_seq = max(max_seq, seq)
            if seq not in committed:
                continue  # orphan of an un-barriered (torn) last commit
            object_id = row["object_id"]
            namespace = row["namespace"]
            digest = row["digest"]
            schema_ref = SchemaRef(name=row["schema"]["name"], version=row["schema"]["version"])
            registry_digest = row["registry_digest"]
            content_path = self.payloads_dir / namespace / f"{digest}.json"
            try:
                raw = content_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise DurableStoreCorrupt(
                    f"payload content file for {namespace}/{digest} is unreadable: {exc}"
                ) from exc
            try:
                # reconstruct through the JSON boundary (strict models coerce
                # JSON arrays→tuples and strings→enums via model_validate_json).
                model_cls = resolver.resolve(registry_digest).resolve(schema_ref)
                model = model_cls.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001 - any reconstruction failure is corruption
                raise DurableStoreCorrupt(
                    f"payload {namespace}/{digest} does not reconstruct under its "
                    f"declared schema/registry: {exc}") from exc
            if _payload_content_digest(model) != digest:
                raise DurableStoreCorrupt(
                    f"payload {namespace}/{digest} content does not recompute to its "
                    "declared digest (tamper)")
            backend.payloads[object_id] = _StoredPayload(
                namespace=namespace, schema_key=schema_ref.key,
                registry_digest=registry_digest, content_digest=digest, model=model)
            backend.payload_idem[row["idempotency_key"]] = object_id
            payload_seq = max(payload_seq, _oid_num(object_id))
        backend.payload_seq = payload_seq
        return max_seq

    def _fold_events(self, backend: _Backend, committed: frozenset[int]) -> int:
        max_seq = 0
        collected: list[tuple[int, int, RunEvent]] = []
        if self.events_dir.is_dir():
            for part_file in sorted(self.events_dir.glob("*.jsonl")):
                for row in self._read_jsonl(part_file):
                    seq = int(row["seq"])
                    max_seq = max(max_seq, seq)
                    if seq not in committed:
                        continue
                    try:
                        event = RunEvent.model_validate_json(json.dumps(row["event"]))
                    except ValidationError as exc:
                        raise DurableStoreCorrupt(
                            f"event row seq={seq} in {part_file} is not a valid RunEvent: {exc}"
                        ) from exc
                    collected.append((seq, int(row.get("ord", 0)), event))
        collected.sort(key=lambda t: (t[0], t[1]))  # global append order
        for _seq, _ord, event in collected:
            key = (event.run_id, event.partition)
            backend.events = backend.events + (event,)
            backend.event_by_key[event.idempotency_key] = event
            backend.journal_counters[key] = max(
                backend.journal_counters.get(key, 0), event.journal_seq)
            if event.visible_seq is not None:
                backend.visible_counters[key] = max(
                    backend.visible_counters.get(key, 0), event.visible_seq)
        return max_seq

    def _fold_cells(self, backend: _Backend, committed: frozenset[int]) -> int:
        max_seq = 0
        for row in self._read_jsonl(self.state_cells_path):
            seq = int(row["seq"])
            max_seq = max(max_seq, seq)
            if seq not in committed:
                continue
            try:
                tref = TypedPayloadRef.model_validate_json(json.dumps(row["typed_ref"]))
            except ValidationError as exc:
                raise DurableStoreCorrupt(
                    f"state-cell row seq={seq} is not a valid TypedPayloadRef: {exc}"
                ) from exc
            backend.cells[(row["namespace"], row["key_digest"])] = tref  # last-write-wins
        return max_seq


# --------------------------------------------------------------------------- #
# Durable store views (subclass the reviewed in-memory stores)                 #
# --------------------------------------------------------------------------- #
class FilePayloadStore(PayloadStore):
    """Content-addressed, write-once durable payload store.

    Payloads persist to ``payloads/<namespace>/<digest>.json`` (namespaces
    partition physically — ``sealed`` never shares a directory with ``main``); a
    second put of an existing digest verifies byte-identity (mismatch ⇒
    :class:`PayloadWriteConflict`); ``get`` re-verifies the folded model's digest
    before returning (inherited), and the fold re-validates every content file so
    on-disk corruption never flows onward silently.
    """

    def __init__(self, shared: _Shared, resolver: SchemaRegistryResolver, log: _DurableLog) -> None:
        super().__init__(shared, resolver)
        self._log = log

    def put(self, schema_ref, payload, *, registry_digest, namespace, idempotency_key):
        with self._shared.lock:
            old = self._shared.backend
            wb = old.clone()
            ref = _apply_payload_put(
                wb, self._resolver, schema_ref=schema_ref, payload=payload,
                registry_digest=registry_digest, namespace=namespace,
                idempotency_key=idempotency_key)
            if ref.object_id not in old.payloads:  # a genuinely new payload
                seq = self._log.next_commit_seq()
                self._log.persist_commit(
                    seq, payloads=[(ref.object_id, wb.payloads[ref.object_id],
                                    idempotency_key, schema_ref)])
            self._shared.backend = wb
            return ref


class JsonlEventStore(EventStore):
    """Per-partition append-only durable event journal with fold-on-open.

    Appends persist one row to ``events/<partition>.jsonl`` (fsync per append);
    the dual ``journal_seq``/``visible_seq`` cursors are rebuilt byte-equivalently
    on open from the folded events, exactly as :meth:`EventStore.replay` recomputes
    them in memory. Read methods (``journal``/``visible``/``cursor``/``replay``)
    are inherited unchanged.
    """

    def __init__(self, shared, resolver, clock, log: _DurableLog) -> None:
        super().__init__(shared, resolver, clock)
        self._log = log

    def append(self, request):
        with self._shared.lock:
            old = self._shared.backend
            wb = old.clone()
            event = _append_event(wb, self._resolver, self._clock, request)
            if request.idempotency_key not in old.event_by_key:  # a genuinely new event
                seq = self._log.next_commit_seq()
                self._log.persist_commit(seq, events=[(event, 0)])
            self._shared.backend = wb
            return event

    def run_ids(self, partition: str = "main") -> tuple[str, ...]:
        """Distinct run ids present on ``partition`` (fold order) — the scan's index."""
        seen: list[str] = []
        seen_set: set[str] = set()
        for event in self._shared.backend.events:
            if event.partition == partition and event.run_id not in seen_set:
                seen_set.add(event.run_id)
                seen.append(event.run_id)
        return tuple(seen)


class JsonlStateCellStore(RuntimeStateCellStore):
    """Durable state-cell store: heads fold from ``state_cells.jsonl`` on open.

    ``load`` is inherited (reads the folded head); CAS transitions run only inside a
    :class:`RuntimeUnitOfWork`, whose durable variant appends the head rows. CAS
    conflict semantics are identical to the in-memory store.
    """


class _DurableBudgetSink:  # pragma: no cover - not instantiated (budget stays in-memory)
    """Marker: budget events keep in-memory semantics and are intentionally not
    restart-durable (see the module docstring's durability-scope note)."""


class _DurableUnitOfWork(RuntimeUnitOfWork):
    """Atomic durable unit of work: reuses ``_apply_batch`` verbatim, then persists
    the produced payloads / events / state-cell heads under one commit barrier
    before publishing the in-memory transition (persist-then-publish)."""

    def __init__(self, shared, resolver, clock, *, allowed_cell_namespaces, run_budgets, log: _DurableLog) -> None:
        super().__init__(
            shared, resolver, clock,
            allowed_cell_namespaces=allowed_cell_namespaces, run_budgets=run_budgets)
        self._log = log

    def commit(self, batch: RuntimeBatch):
        with self._shared.lock:
            prev = self._shared.backend.batch_idem.get(batch.idempotency_key)
            if prev is not None:
                if prev.batch != batch:
                    raise IdempotencyConflict(
                        f"batch idempotency key {batch.idempotency_key!r} reused with "
                        "different content")
                return prev.result
            old = self._shared.backend
            wb = old.clone()
            result = self._apply_batch(wb, batch)  # reuse the reviewed in-memory logic
            wb.batch_idem[batch.idempotency_key] = _StoredBatch(batch=batch, result=result)

            new_payloads: list[tuple[str, _StoredPayload, str, SchemaRef]] = []
            for put in batch.payload_puts:
                object_id = result.payload_ref(put.staged_key.key).object_id
                if object_id not in old.payloads:  # genuinely new (not an idempotent hit)
                    new_payloads.append(
                        (object_id, wb.payloads[object_id], put.idempotency_key, put.schema_ref))
            new_payloads.sort(key=lambda t: _oid_num(t[0]))
            new_events = [(ev, i) for i, ev in enumerate(wb.events[len(old.events):])]
            new_cells = [(ns, key, tref) for (ns, key), tref in result.cells.items()]

            if new_payloads or new_events or new_cells:
                seq = self._log.next_commit_seq()
                self._log.persist_commit(
                    seq, payloads=new_payloads, events=new_events, cells=new_cells)
            self._shared.backend = wb
            return result


# --------------------------------------------------------------------------- #
# DurableRuntimeStores — the shared-backend wiring over one durable log         #
# --------------------------------------------------------------------------- #
class DurableRuntimeStores(RuntimeStores):
    """A :class:`RuntimeStores` whose payload/event/cell/UoW views are durable.

    Construction builds the standard in-memory shared backend, folds the durable
    log into it (rebuilding all persisted state byte-identically), then swaps the
    four views for durable ones that share that backend + one :class:`_DurableLog`.
    Budget event sinks and whole-batch idempotency remain the inherited in-memory
    implementations (durability scope, see module docstring).
    """

    def __init__(self, *, resolver, clock, allowed_cell_namespaces=(), root: Path) -> None:
        super().__init__(
            resolver=resolver, clock=clock, allowed_cell_namespaces=allowed_cell_namespaces)
        self._log = _DurableLog(Path(root))
        self._log.fold_into(self._shared, resolver)
        self.payloads = FilePayloadStore(self._shared, resolver, self._log)
        self.events = JsonlEventStore(self._shared, resolver, clock, self._log)
        self.cells = JsonlStateCellStore(
            self._shared, allowed_namespaces=self._allowed_cell_namespaces)
        self.unit_of_work = _DurableUnitOfWork(
            self._shared, resolver, clock,
            allowed_cell_namespaces=self._allowed_cell_namespaces,
            run_budgets=self._run_budgets, log=self._log)

    @property
    def root(self) -> Path:
        return self._log.root


def build_durable_runtime_stores(
    root: Path,
    *,
    resolver: SchemaRegistryResolver | None = None,
    clock: AuthoritativeClock | None = None,
    allowed_cell_namespaces: tuple[str, ...] = (),
) -> RuntimeStores:
    """The production binding consumed by Task 6's playbooks and Task 10's router.

    ``root`` is the store root (default ``var/orchestration/`` at the call site);
    ``resolver``/``clock`` default to a fresh cumulative Phase-2 runtime resolver and
    the production :class:`SystemClock` so the minimal ``build_durable_runtime_stores(root)``
    call is usable, while production/tests inject their own registered resolver and
    deterministic clock. In-memory stores remain the test default everywhere.
    """
    if resolver is None:
        resolver = SchemaRegistryResolver()
        resolver.register(default_registry())
        resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    if clock is None:
        clock = SystemClock()
    return DurableRuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=allowed_cell_namespaces, root=Path(root))


# --------------------------------------------------------------------------- #
# Startup interrupt scan — honest + conservative                              #
# --------------------------------------------------------------------------- #
def scan_and_mark_interrupted(
    stores: RuntimeStores, *, registry_digest: str, clock: AuthoritativeClock
) -> tuple[str, ...]:
    """Mark every admitted-but-unfinished node-execution attempt ``interrupted``.

    For each run on the ``main`` journal that has events but **no** terminal
    ``RunCompleted``/``RunFailed``/``RunCancelled`` event and **no** experiment-
    lifecycle (``ExperimentStateChanged``) event, append a terminal
    ``RunResult(terminal_status="cancelled")`` through the exact reviewed Phase 2
    record path (``RuntimeUnitOfWork.commit`` with the ``runresult:{run_id}``
    idempotency-key family the DAG runner uses). Never re-executes, re-admits or
    fabricates progress; a re-scan is idempotent; parked ``WAITING_FOR_MATURITY``
    heads (state cells) and any experiment-lifecycle run are left untouched — the
    maturity/wakeup layer (Task 6) owns those. Returns the run ids marked this call.
    """
    events = stores.events
    if not hasattr(events, "run_ids"):  # only the durable event store is scannable
        return ()
    marked: list[str] = []
    for run_id in events.run_ids("main"):
        journal = events.journal(run_id, "main")
        if any(e.event_type in _TERMINAL_RUN_EVENTS for e in journal):
            continue  # already finished (includes a prior scan's RunCancelled)
        if any(e.event_type is EventType.EXPERIMENT_STATE_CHANGED for e in journal):
            continue  # experiment-lifecycle run — owned by the Task 6 maturity layer
        plan_digest = next(
            (e.plan_digest for e in reversed(journal) if e.plan_digest is not None), None)
        if plan_digest is None:
            continue  # cannot honestly record a terminal without a recoverable plan digest
        result = RunResult(
            run_id=run_id, plan_digest=plan_digest, terminal_status="cancelled")
        stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key=f"runresult:{run_id}",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="runresult"),
                schema_ref=_RUN_RESULT_SCHEMA_REF, namespace="main",
                payload_template=dict(result), registry_digest=registry_digest,
                idempotency_key=f"runresult:{run_id}:payload"),),
            event_appends=(EventAppendCommand(
                run_id=run_id, partition="main", event_type="RunCancelled",
                payload_schema_ref=_RUN_RESULT_SCHEMA_REF,
                payload_target=StagedPayloadKey(key="runresult"),
                registry_digest=registry_digest,
                idempotency_key=f"runresult-ev:{run_id}", plan_digest=plan_digest),),
        ))
        marked.append(run_id)
    return tuple(marked)


# --------------------------------------------------------------------------- #
# Process binding (consumed by the server lifespan block)                      #
# --------------------------------------------------------------------------- #
_PROCESS_STORES: RuntimeStores | None = None


def process_durable_stores() -> RuntimeStores | None:
    """The durable stores bound once for this process (or ``None`` if unbound)."""
    return _PROCESS_STORES


def bind_process_durable_stores_and_scan(
    *,
    root: Path | str | None = None,
    resolver: SchemaRegistryResolver | None = None,
    clock: AuthoritativeClock | None = None,
    allowed_cell_namespaces: tuple[str, ...] = (),
) -> RuntimeStores:
    """Bind the durable stores once per process and run the honest interrupt scan.

    Root default ``var/orchestration/`` with the ``GUANLAN_ORCH_STORE_ROOT``
    override (for 9998 verification runs). Idempotent per process: the second call
    returns the already-bound stores without re-scanning.
    """
    global _PROCESS_STORES
    if _PROCESS_STORES is not None:
        return _PROCESS_STORES
    resolved_root = Path(root or os.environ.get("GUANLAN_ORCH_STORE_ROOT", "var/orchestration"))
    if resolver is None:
        resolver = SchemaRegistryResolver()
        resolver.register(default_registry())
        resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    rt_digest = phase2_runtime_registry(default_registry().registry_digest).registry_digest
    if clock is None:
        clock = SystemClock()
    stores = build_durable_runtime_stores(
        resolved_root, resolver=resolver, clock=clock,
        allowed_cell_namespaces=allowed_cell_namespaces)
    scan_and_mark_interrupted(stores, registry_digest=rt_digest, clock=clock)
    _PROCESS_STORES = stores
    return stores
