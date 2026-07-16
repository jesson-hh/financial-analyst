"""Phase 2 · Task 4 — ArtifactPool staged→barrier commit + input-snapshot freeze.

The runtime dataflow store scoped to **one run and one frozen Plan**. It sits on
top of the Task 2 :class:`~guanlan_v2.orchestration.eventstore.RuntimeStores`
transaction boundary and turns individual worker outputs into an atomically,
replayably visible DAG-layer barrier.

Reviewed shape
--------------
* :meth:`ArtifactPool.stage` verifies a produced :class:`Artifact` against the
  run / Plan / node / output binding, the payload :class:`SchemaRef` and the three
  Artifact digests (all through the Phase 1 builder + registry validator), persists
  the typed payload plus a **journal-only** ``ArtifactStaged`` event in one unit of
  work, and returns an :class:`ArtifactRef` that is *not yet* readable as
  committed. Possessing that ref — or the low-level staged ``PayloadRef`` — does
  not authorize a committed read.
* :meth:`ArtifactPool.commit_layer` is the atomic visibility boundary for one
  layer. It derives the expected outputs from the frozen Worker ``OutputBinding``
  of the actual COMPLETED / DEGRADED :class:`NodeRun`\\s (never a caller-written
  slot set), validates that the staged outputs are *exactly* those, assigns a
  canonical ``artifact_seq`` ordered by ``(node_id, output_key)`` — independent of
  stage / completion order — and stores one typed :class:`LayerCommit` payload plus
  one public ``LayerCommitted`` event in a **single** Task 2 unit of work. Only
  after that atomic commit does it advance the committed index (persist-then-
  publish). A crash before the barrier exposes none of the layer.
* :meth:`ArtifactPool.committed` / :meth:`committed_output` resolve the exact
  committed ref / content digest, never a "latest value by slot".
* :meth:`ArtifactPool.replay` rebuilds the committed visibility **only** from the
  persisted ``LayerCommitted`` events and their typed refs.
* :meth:`ArtifactPool.freeze_input_snapshot` seals a per-node read-set to the real
  run / Plan / node / layer / attempt identity, with exact one/many binding order
  (outer bindings follow the WorkerSpec declaration order) and the ready /
  terminal_partial matrix. A ``terminal_partial`` snapshot can never be executed
  (:meth:`assert_executable`).

The pool never redefines a Phase 1 model, digest or registry: it *calls* the
Phase 1 :class:`Artifact` / :class:`InputSnapshot` / :class:`LayerCommit` builders
and validators and the Task 2 stores. ``IdempotencyConflict`` is the single shared
persist-then-publish type re-exported from
:mod:`guanlan_v2.orchestration.eventstore`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot, WorkerSpec
from guanlan_v2.orchestration.context import (
    InputArtifactBinding,
    InputSnapshot,
    MemoryRecordRef,
)
from guanlan_v2.orchestration.digest import DigestHex, NonEmptyStr, content_digest
from guanlan_v2.orchestration.enums import NodeStatus
from guanlan_v2.orchestration.events import CommittedArtifactRef, EventType, LayerCommit
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.schemas import (
    Artifact,
    ArtifactRef,
    NodeRun,
    validate_artifact_payload,
)
from guanlan_v2.orchestration.spec import Plan, PlanNode

__all__ = [
    # errors
    "PoolError",
    "ArtifactStageError",
    "LateStageError",
    "DuplicateOutputError",
    "LayerCommitError",
    "ArtifactNotCommitted",
    "InputSnapshotError",
    "IdempotencyConflict",  # re-exported (single persist-then-publish type)
    # DTO
    "ExpectedOutput",
    # pool
    "ArtifactPool",
]


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class PoolError(Exception):
    """Base for every ArtifactPool failure."""


class ArtifactStageError(PoolError):
    """A staged artifact failed run/plan/node/output/digest/provenance binding."""


class LateStageError(ArtifactStageError):
    """A stage arrived after its layer (or its output) already committed."""


class DuplicateOutputError(ArtifactStageError):
    """A second, different artifact was staged for one ``(node_id, output_key)``."""


class LayerCommitError(PoolError):
    """A layer commit failed expected-output validation (missing/extra/mismatch)."""


class ArtifactNotCommitted(PoolError):
    """A committed read was attempted for a staged-only or mis-identified ref."""


class InputSnapshotError(PoolError):
    """A frozen input snapshot failed binding/order/readiness validation, or a
    terminal_partial snapshot was handed to execution."""


# --------------------------------------------------------------------------- #
# Strict runtime base (NOT a ContractModel — mirrors the Task 1/2 idiom)        #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ExpectedOutput(_StrictModel):
    """One output a COMPLETED / DEGRADED node is expected to have produced.

    Derived *only* from the frozen Worker's :class:`OutputBinding` for the actual
    node runs — never a caller-written slot set. ``schema_ref`` is the declared
    output schema the staged artifact's ``payload_schema_ref`` must equal.
    """

    node_id: NonEmptyStr
    output_key: NonEmptyStr
    schema_ref: SchemaRef


#: the two node statuses that require a produced Artifact per declared output.
_SUCCESS_STATUSES: frozenset[NodeStatus] = frozenset(
    {NodeStatus.COMPLETED, NodeStatus.DEGRADED}
)

_LAYER_COMMIT_SCHEMA_REF = SchemaRef(name="LayerCommit", version="1")
_INPUT_SNAPSHOT_SCHEMA_REF = SchemaRef(name="InputSnapshot", version="1")


# --------------------------------------------------------------------------- #
# Internal records                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class _StoredEnvelope:
    """A durable staged artifact record: the envelope plus its persisted locator."""

    artifact: Artifact
    ref: ArtifactRef
    payload_ref: PayloadRef
    node_id: str
    output_key: str
    layer_index: int


@dataclass
class _CommittedArtifact:
    artifact: Artifact
    ref: ArtifactRef
    artifact_seq: int
    layer_index: int


@dataclass
class _PoolStorage:
    """The durable pool-owned storage shared across a pool and its replays.

    Holds every staged artifact envelope by ``artifact_id`` (the value store the
    committed index dereferences), plus the stage/commit idempotency indexes. The
    *committed visibility* itself is **not** stored here — it is derived only from
    the persisted ``LayerCommitted`` events (see :meth:`ArtifactPool._rebuild`).
    """

    lock: threading.RLock
    envelopes: dict[str, _StoredEnvelope]
    stage_idem: dict[str, str]  # stage idempotency key -> artifact_id
    commit_idem: dict[str, tuple[int, str]]  # commit key -> (layer_index, signature)

    @classmethod
    def empty(cls) -> "_PoolStorage":
        return cls(lock=threading.RLock(), envelopes={}, stage_idem={}, commit_idem={})


# --------------------------------------------------------------------------- #
# Phase 1 verification helpers                                                #
# --------------------------------------------------------------------------- #
def _verify_artifact_through_phase1(artifact: Artifact, registry: Any) -> None:
    """Verify the artifact's payload SchemaRef + three digests + rendered binding
    through the Phase 1 registry validator and the Phase 1 :meth:`Artifact.build`.

    Raises :class:`ArtifactStageError` if any declared digest does not recompute or
    the typed payload does not conform to its registered schema. This catches a
    ``model_construct``-tampered record whose validator was bypassed.
    """
    # (1) the typed payload must conform to its registered schema (Phase 1 fn).
    validate_artifact_payload(artifact, registry)

    # (2) recompute all four declared digests via the Phase 1 builder and compare.
    fields = {
        name: getattr(artifact, name)
        for name in Artifact.model_fields
        if name not in Artifact.SELF_DIGEST_FIELDS and name != "rendered_from_payload_digest"
    }
    rebuilt = Artifact.build(**fields)
    if (
        rebuilt.content_digest != artifact.content_digest
        or rebuilt.reproducibility_digest != artifact.reproducibility_digest
        or rebuilt.audit_digest != artifact.audit_digest
        or rebuilt.rendered_from_payload_digest != artifact.rendered_from_payload_digest
    ):
        raise ArtifactStageError(
            "artifact digests do not recompute through the Phase 1 Artifact builder"
        )


def _is_subsequence(sub: list[str], full: list[str]) -> bool:
    """True iff ``sub`` appears in ``full`` in order (not necessarily contiguous)."""
    it = iter(full)
    return all(name in it for name in sub)


# --------------------------------------------------------------------------- #
# ArtifactPool                                                                #
# --------------------------------------------------------------------------- #
class ArtifactPool:
    """Staged→barrier artifact store for one run + one frozen :class:`Plan`."""

    def __init__(
        self,
        *,
        stores: RuntimeStores,
        registry_digest: str,
        plan: Plan,
        catalog: WorkerCatalogSnapshot,
        clock: AuthoritativeClock,
        _storage: _PoolStorage | None = None,
    ) -> None:
        if plan.catalog_digest != catalog.catalog_digest:
            raise PoolError(
                "the frozen Plan is not bound to the supplied catalog "
                "(catalog_digest mismatch)"
            )
        self._stores = stores
        self._registry_digest = registry_digest
        self._plan = plan
        self._catalog = catalog
        self._clock = clock

        # resolve the runtime registry and require it to carry the control schemas
        # the pool persists (a Phase-1-only registry would silently break commit).
        self._registry = stores.resolver.resolve(registry_digest)
        self._registry.resolve(_LAYER_COMMIT_SCHEMA_REF)
        self._registry.resolve(_INPUT_SNAPSHOT_SCHEMA_REF)

        self._nodes_by_id: dict[str, PlanNode] = {n.id: n for n in plan.nodes}
        worker_by_id: dict[str, WorkerSpec] = {w.id: w for w in catalog.workers}
        self._worker_by_node: dict[str, WorkerSpec] = {}
        for node in plan.nodes:
            worker = worker_by_id.get(node.worker_id)
            if worker is None:
                raise PoolError(
                    f"plan node {node.id!r} references unknown worker {node.worker_id!r}"
                )
            self._worker_by_node[node.id] = worker

        self._storage = _storage if _storage is not None else _PoolStorage.empty()
        self._lock = threading.RLock()
        # live derived indexes (rebuilt from events on replay).
        self._staged: dict[tuple[str, str], _StoredEnvelope] = {}
        self._committed_by_id: dict[str, _CommittedArtifact] = {}
        self._committed_by_output: dict[tuple[str, str], str] = {}
        self._committed_layers: dict[int, tuple[str, LayerCommit]] = {}

    # -- identity ---------------------------------------------------------- #
    @property
    def run_id(self) -> str:
        return self._plan.run_id

    @property
    def plan(self) -> Plan:
        return self._plan

    # -- stage ------------------------------------------------------------- #
    def stage(
        self,
        artifact: Artifact,
        *,
        layer_index: int,
        node_run: NodeRun,
        idempotency_key: str,
    ) -> ArtifactRef:
        """Persist one produced artifact as *staged* (journal-only) evidence.

        Verifies the run/plan/node/output binding, payload SchemaRef, three Artifact
        digests and provenance through the Phase 1 functions, then persists the
        typed payload + one journal-only ``ArtifactStaged`` event in a single unit
        of work. The returned :class:`ArtifactRef` is *not* readable as committed
        until its whole layer commits. Idempotent on an identical retry; a different
        artifact under the same key raises :class:`IdempotencyConflict`.
        """
        with self._lock, self._storage.lock:
            prior_id = self._storage.stage_idem.get(idempotency_key)
            if prior_id is not None:
                prior = self._storage.envelopes[prior_id]
                if prior.artifact == artifact:
                    return prior.ref
                raise IdempotencyConflict(
                    f"stage idempotency key {idempotency_key!r} reused with a different artifact"
                )

            self._require_run(artifact.run_id, "artifact")
            self._require_node_run_binds_plan(node_run)
            if node_run.node_id != artifact.producer_node_id:
                raise ArtifactStageError(
                    "node_run.node_id does not match artifact.producer_node_id"
                )

            key = (artifact.producer_node_id, artifact.output_key)
            if layer_index in self._committed_layers:
                raise LateStageError(
                    f"layer {layer_index} already committed; cannot stage {key}"
                )
            if key in self._committed_by_output:
                raise LateStageError(f"output {key} already committed; cannot re-stage")
            if key in self._staged:
                raise DuplicateOutputError(
                    f"output {key} is already staged in this layer with different content"
                )

            self._require_output_binding(artifact)
            self._require_provenance_binds_plan(artifact)
            _verify_artifact_through_phase1(artifact, self._registry)

            payload_ref = self._persist_staged(artifact, idempotency_key, layer_index)
            ref = self._make_ref(artifact)
            env = _StoredEnvelope(
                artifact=artifact, ref=ref, payload_ref=payload_ref,
                node_id=artifact.producer_node_id, output_key=artifact.output_key,
                layer_index=layer_index,
            )
            self._storage.envelopes[artifact.artifact_id] = env
            self._storage.stage_idem[idempotency_key] = artifact.artifact_id
            self._staged[key] = env
            return ref

    def _persist_staged(
        self, artifact: Artifact, idempotency_key: str, layer_index: int
    ) -> PayloadRef:
        batch = RuntimeBatch(
            idempotency_key=f"stage:{idempotency_key}",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="payload"),
                    schema_ref=artifact.payload_schema_ref,
                    namespace="main",
                    payload_template=dict(artifact.payload),
                    registry_digest=self._registry_digest,
                    idempotency_key=f"{idempotency_key}:payload",
                ),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self.run_id, partition="main", event_type="ArtifactStaged",
                    payload_schema_ref=artifact.payload_schema_ref,
                    payload_target=StagedPayloadKey(key="payload"),
                    registry_digest=self._registry_digest,
                    idempotency_key=f"{idempotency_key}:staged-event",
                    plan_digest=self._plan.plan_digest,
                    correlation_id=artifact.artifact_id,
                ),
            ),
        )
        result = self._stores.unit_of_work.commit(batch)
        return result.payload_ref("payload")

    # -- commit ------------------------------------------------------------ #
    def commit_layer(
        self,
        layer_index: int,
        *,
        node_runs: Any,
        expected_outputs: Any,
        idempotency_key: str,
    ) -> LayerCommit:
        """Atomically make one DAG layer's outputs visible.

        Validates that the staged outputs are *exactly* the ones derived from the
        frozen Worker ``OutputBinding`` of the COMPLETED / DEGRADED node runs (the
        ``expected_outputs`` argument must equal that derivation; a caller-written
        slot set is rejected), assigns a canonical ``artifact_seq`` ordered by
        ``(node_id, output_key)``, and stores one typed :class:`LayerCommit` payload
        plus one public ``LayerCommitted`` event in a single unit of work. Only then
        does it advance the committed index. Idempotent on an identical retry; a
        conflicting retry raises :class:`IdempotencyConflict`.
        """
        node_runs = tuple(node_runs)
        expected_outputs = tuple(expected_outputs)
        with self._lock, self._storage.lock:
            for node_run in node_runs:
                self._require_node_run_binds_plan(node_run)

            derived = self.derive_expected_outputs(node_runs)
            if set(expected_outputs) != set(derived):
                raise LayerCommitError(
                    "expected_outputs is not the frozen Worker OutputBinding "
                    "derivation for these node runs; a caller-written slot set is refused"
                )
            signature = _commit_signature(layer_index, derived, node_runs)

            # -- idempotency (persist-then-publish) -------------------------- #
            prior = self._storage.commit_idem.get(idempotency_key)
            if prior is not None:
                prior_layer, prior_sig = prior
                if prior_layer == layer_index and prior_sig == signature:
                    return self._committed_layers[layer_index][1]
                raise IdempotencyConflict(
                    f"commit idempotency key {idempotency_key!r} reused with different content"
                )
            if layer_index in self._committed_layers:
                raise IdempotencyConflict(
                    f"layer {layer_index} already committed under a different idempotency key"
                )

            expected_by_key = {(eo.node_id, eo.output_key): eo for eo in derived}
            staged_here = {
                key: env for key, env in self._staged.items() if env.layer_index == layer_index
            }
            if set(staged_here) != set(expected_by_key):
                missing = sorted(set(expected_by_key) - set(staged_here))
                extra = sorted(set(staged_here) - set(expected_by_key))
                raise LayerCommitError(
                    f"layer {layer_index} staged outputs do not match the expected outputs "
                    f"(missing={missing}, extra={extra})"
                )
            for key, eo in expected_by_key.items():
                if staged_here[key].artifact.payload_schema_ref != eo.schema_ref:
                    raise LayerCommitError(
                        f"staged output {key} payload schema does not match the "
                        "declared Worker OutputBinding schema"
                    )

            # -- canonical artifact_seq by (node_id, output_key) ------------- #
            ordered = sorted(expected_by_key)  # (node_id, output_key) tuples
            committed = [(seq, staged_here[key]) for seq, key in enumerate(ordered, start=1)]
            layer_commit = LayerCommit(
                plan_digest=self._plan.plan_digest,
                layer_index=layer_index,
                node_run_ids=tuple(sorted({nr.node_run_id for nr in node_runs})),
                artifacts=tuple(
                    CommittedArtifactRef(artifact_id=env.artifact.artifact_id, artifact_seq=seq)
                    for seq, env in committed
                ),
                committed_at=clock_now(self._clock),
            )

            # -- persist the barrier atomically, THEN advance visibility ----- #
            self._persist_commit(layer_commit, idempotency_key)
            for seq, env in committed:
                self._committed_by_id[env.artifact.artifact_id] = _CommittedArtifact(
                    artifact=env.artifact, ref=env.ref, artifact_seq=seq, layer_index=layer_index
                )
                self._committed_by_output[(env.node_id, env.output_key)] = env.artifact.artifact_id
                self._staged.pop((env.node_id, env.output_key), None)
            self._committed_layers[layer_index] = (idempotency_key, layer_commit)
            self._storage.commit_idem[idempotency_key] = (layer_index, signature)
            return layer_commit

    def _persist_commit(self, layer_commit: LayerCommit, idempotency_key: str) -> None:
        batch = RuntimeBatch(
            idempotency_key=f"commit:{idempotency_key}",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="layercommit"),
                    schema_ref=_LAYER_COMMIT_SCHEMA_REF,
                    namespace="main",
                    payload_template=dict(layer_commit),
                    registry_digest=self._registry_digest,
                    idempotency_key=f"{idempotency_key}:layercommit",
                ),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=self.run_id, partition="main", event_type="LayerCommitted",
                    payload_schema_ref=_LAYER_COMMIT_SCHEMA_REF,
                    payload_target=StagedPayloadKey(key="layercommit"),
                    registry_digest=self._registry_digest,
                    idempotency_key=idempotency_key,
                    plan_digest=self._plan.plan_digest,
                ),
            ),
        )
        self._stores.unit_of_work.commit(batch)

    def derive_expected_outputs(self, node_runs: Any) -> tuple[ExpectedOutput, ...]:
        """The expected outputs for a set of node runs, derived **only** from the
        frozen Worker ``OutputBinding``\\s of the COMPLETED / DEGRADED runs.

        A non-success node run requires no artifact and contributes nothing. This is
        the sole source of a commit's expected-output set; the caller passes the
        result straight back to :meth:`commit_layer`, which re-derives and rejects
        anything else.
        """
        out: list[ExpectedOutput] = []
        for node_run in node_runs:
            if node_run.status not in _SUCCESS_STATUSES:
                continue
            worker = self._worker_by_node.get(node_run.node_id)
            if worker is None:
                raise LayerCommitError(
                    f"node_run node_id {node_run.node_id!r} is not a node of the pool Plan"
                )
            for binding in worker.outputs:
                out.append(
                    ExpectedOutput(
                        node_id=node_run.node_id,
                        output_key=binding.name,
                        schema_ref=binding.schema_ref,
                    )
                )
        return tuple(out)

    # -- retrieval --------------------------------------------------------- #
    def committed(self, ref: ArtifactRef) -> Artifact:
        """Resolve the exact committed artifact for ``ref`` (never latest-by-slot).

        Raises :class:`ArtifactNotCommitted` if the artifact is not committed
        (staged-only artifacts are unreadable) or the ref's content digest / identity
        does not match the committed record.
        """
        with self._lock:
            entry = self._committed_by_id.get(ref.artifact_id)
            if entry is None:
                raise ArtifactNotCommitted(
                    f"artifact {ref.artifact_id!r} is not committed; a staged artifact "
                    "is not readable through the committed index"
                )
            art = entry.artifact
            if ref.content_digest != art.content_digest:
                raise ArtifactNotCommitted(
                    "ref content_digest does not match the committed artifact "
                    "(exact committed digest required, not latest-by-slot)"
                )
            if ref.producer_node_id != art.producer_node_id or ref.output_key != art.output_key:
                raise ArtifactNotCommitted("ref identity does not match the committed artifact")
            return art

    def committed_output(self, node_id: str, output_key: str) -> Artifact | None:
        """The committed artifact for ``(node_id, output_key)``, or ``None``."""
        with self._lock:
            artifact_id = self._committed_by_output.get((node_id, output_key))
            if artifact_id is None:
                return None
            return self._committed_by_id[artifact_id].artifact

    # -- input snapshot ---------------------------------------------------- #
    def freeze_input_snapshot(
        self,
        node: PlanNode,
        *,
        run_id: str,
        plan: Plan,
        layer_index: int,
        attempt: int,
        context_snapshot_ref: TypedPayloadRef,
        bound_artifact_inputs: tuple[InputArtifactBinding, ...],
        data_result_refs: tuple[TypedPayloadRef, ...] = (),
        memory_record_refs: tuple[MemoryRecordRef, ...] = (),
        readiness: str,
        missing_input_names: tuple[str, ...] = (),
    ) -> InputSnapshot:
        """Seal one node's pre-execution read-set as an immutable :class:`InputSnapshot`.

        Binds the real run / Plan / node / layer / attempt identity, validates that
        every bound input is a declared Worker input with matching cardinality, that
        the outer binding order follows the WorkerSpec declaration order, and that a
        ``ready`` snapshot carries every required input. Persists the registered
        InputSnapshot so the frozen read-set is durable, and returns it. DataResult
        refs obtained *during* execution never enter this pre-node snapshot.
        """
        self._require_run(run_id, "freeze_input_snapshot")
        if plan.plan_id != self._plan.plan_id or plan.plan_digest != self._plan.plan_digest:
            raise InputSnapshotError(
                "freeze_input_snapshot plan does not match the pool's frozen Plan"
            )
        resolved = self._nodes_by_id.get(node.id)
        if resolved is None or resolved != node:
            raise InputSnapshotError(
                f"node {getattr(node, 'id', None)!r} is not a node of the pool's frozen Plan"
            )
        worker = self._worker_by_node[node.id]
        worker_input_names = [b.name for b in worker.inputs]
        worker_input_by_name = {b.name: b for b in worker.inputs}

        names = [b.input_name for b in bound_artifact_inputs]
        if len(set(names)) != len(names):
            raise InputSnapshotError("bound_artifact_inputs repeats an input name")
        for binding in bound_artifact_inputs:
            declared = worker_input_by_name.get(binding.input_name)
            if declared is None:
                raise InputSnapshotError(
                    f"input {binding.input_name!r} is not declared by worker {worker.id!r}"
                )
            if binding.cardinality != declared.cardinality:
                raise InputSnapshotError(
                    f"input {binding.input_name!r} cardinality {binding.cardinality!r} does not "
                    f"match the WorkerSpec declaration {declared.cardinality!r}"
                )
        if not _is_subsequence(names, worker_input_names):
            raise InputSnapshotError(
                "bound_artifact_inputs order must follow the WorkerSpec input declaration order"
            )
        if readiness == "ready":
            present = set(names)
            for declared in worker.inputs:
                if declared.required and declared.name not in present:
                    raise InputSnapshotError(
                        f"readiness='ready' requires the required input {declared.name!r}"
                    )

        snapshot = InputSnapshot.build(
            snapshot_id=f"insnap-{run_id}-{node.id}-L{layer_index}-a{attempt}",
            run_id=run_id, plan_id=self._plan.plan_id, plan_digest=self._plan.plan_digest,
            node_id=node.id, layer_index=layer_index, attempt=attempt,
            context_snapshot_ref=context_snapshot_ref,
            artifact_inputs=tuple(bound_artifact_inputs),
            data_result_refs=tuple(data_result_refs),
            memory_record_refs=tuple(memory_record_refs),
            readiness=readiness, missing_input_names=tuple(missing_input_names),
            built_at=clock_now(self._clock),
        )
        self._stores.payloads.put(
            _INPUT_SNAPSHOT_SCHEMA_REF, snapshot, registry_digest=self._registry_digest,
            namespace="main",
            idempotency_key=(
                f"input-snapshot:{run_id}:{node.id}:{layer_index}:{attempt}:{snapshot.content_digest}"
            ),
        )
        return snapshot

    @staticmethod
    def assert_executable(snapshot: InputSnapshot) -> InputSnapshot:
        """Return ``snapshot`` if it is executable; raise for a ``terminal_partial``.

        A ``terminal_partial`` snapshot exists solely to bind a BLOCKED / SKIPPED /
        early-terminal NodeRun to real evidence and must never reach a worker.
        """
        if snapshot.readiness != "ready":
            raise InputSnapshotError(
                "a terminal_partial InputSnapshot is non-executable and cannot be "
                "handed to a worker"
            )
        return snapshot

    # -- replay ------------------------------------------------------------ #
    def replay(self) -> "ArtifactPool":
        """Rebuild a fresh pool whose committed visibility is derived **only** from
        the persisted ``LayerCommitted`` events + their typed refs."""
        fresh = ArtifactPool(
            stores=self._stores, registry_digest=self._registry_digest, plan=self._plan,
            catalog=self._catalog, clock=self._clock, _storage=self._storage,
        )
        fresh._rebuild()
        return fresh

    def _rebuild(self) -> None:
        with self._lock, self._storage.lock:
            self._staged.clear()
            self._committed_by_id.clear()
            self._committed_by_output.clear()
            self._committed_layers.clear()
            committed_ids: set[str] = set()
            for event in self._stores.events.journal(self.run_id, "main"):
                if event.event_type is not EventType.LAYER_COMMITTED:
                    continue
                layer_commit = self._stores.payloads.get(
                    event.payload_ref, expected_schema_ref=_LAYER_COMMIT_SCHEMA_REF
                )
                for car in layer_commit.artifacts:
                    env = self._storage.envelopes.get(car.artifact_id)
                    if env is None:
                        raise PoolError(
                            f"replay: committed artifact {car.artifact_id!r} has no persisted envelope"
                        )
                    # re-validate the persisted typed payload against the registry.
                    self._stores.payloads.get(
                        env.payload_ref, expected_schema_ref=env.artifact.payload_schema_ref
                    )
                    self._committed_by_id[car.artifact_id] = _CommittedArtifact(
                        artifact=env.artifact, ref=env.ref, artifact_seq=car.artifact_seq,
                        layer_index=layer_commit.layer_index,
                    )
                    self._committed_by_output[(env.node_id, env.output_key)] = car.artifact_id
                    committed_ids.add(car.artifact_id)
                self._committed_layers[layer_commit.layer_index] = (
                    event.idempotency_key, layer_commit,
                )
            # staged = durable envelopes not yet made visible by any barrier.
            for artifact_id, env in self._storage.envelopes.items():
                if artifact_id not in committed_ids:
                    self._staged[(env.node_id, env.output_key)] = env

    # -- binding verification helpers -------------------------------------- #
    def _require_run(self, run_id: str, what: str) -> None:
        if run_id != self.run_id:
            raise PoolError(
                f"{what} run_id {run_id!r} does not match the pool run {self.run_id!r}"
            )

    def _require_node_run_binds_plan(self, node_run: NodeRun) -> None:
        self._require_run(node_run.run_id, "node_run")
        if node_run.plan_id != self._plan.plan_id:
            raise PoolError("node_run plan_id does not bind the pool's frozen Plan")
        if node_run.plan_digest != self._plan.plan_digest:
            raise PoolError("node_run plan_digest does not bind the pool's frozen Plan")
        if node_run.node_id not in self._nodes_by_id:
            raise PoolError(
                f"node_run node_id {node_run.node_id!r} is not a node of the pool's Plan "
                "(fabricated NodeRun)"
            )

    def _require_output_binding(self, artifact: Artifact) -> None:
        node = self._nodes_by_id.get(artifact.producer_node_id)
        if node is None:
            raise ArtifactStageError(
                f"artifact producer_node_id {artifact.producer_node_id!r} is not a plan node"
            )
        if artifact.slot != node.writes_slot:
            raise ArtifactStageError(
                f"artifact slot {artifact.slot!r} does not match node writes_slot "
                f"{node.writes_slot!r}"
            )
        worker = self._worker_by_node[node.id]
        binding = next((o for o in worker.outputs if o.name == artifact.output_key), None)
        if binding is None:
            raise ArtifactStageError(
                f"worker {worker.id!r} declares no output {artifact.output_key!r}"
            )
        if binding.schema_ref != artifact.payload_schema_ref:
            raise ArtifactStageError(
                f"artifact payload schema {artifact.payload_schema_ref.key} does not match the "
                f"declared output binding schema {binding.schema_ref.key}"
            )

    def _require_provenance_binds_plan(self, artifact: Artifact) -> None:
        if artifact.provenance.plan_digest != self._plan.plan_digest:
            raise ArtifactStageError(
                "artifact provenance plan_digest does not bind the pool's frozen Plan"
            )

    def _make_ref(self, artifact: Artifact) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=artifact.artifact_id, schema_ref=artifact.payload_schema_ref,
            producer_node_id=artifact.producer_node_id, slot=artifact.slot,
            output_key=artifact.output_key, content_digest=artifact.content_digest,
            relation="input",
        )


def _commit_signature(
    layer_index: int, derived: tuple[ExpectedOutput, ...], node_runs: tuple[NodeRun, ...]
) -> str:
    """A deterministic idempotency signature for one layer commit request."""
    return repr(
        (
            layer_index,
            sorted((eo.node_id, eo.output_key, eo.schema_ref.key) for eo in derived),
            sorted((nr.node_id, nr.status.value, nr.node_run_id) for nr in node_runs),
        )
    )
