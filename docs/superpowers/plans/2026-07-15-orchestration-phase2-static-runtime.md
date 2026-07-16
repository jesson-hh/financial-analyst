# Orchestration Phase 2 · 静态 runtime 兼容 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, admission service, three-worker pilot, and full legacy-equivalence tasks. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Consume the exact Phase 1 contracts to build a deterministic, event-sourced runtime kernel for an already validated **static `Plan`**: typed payload/event storage, an event-sourced budget ledger, read-only catalog material resolution, service-owned admission, staged→barrier artifacts, a capability-confined worker executor and a bounded/stable DAG runner. First prove a reviewed three-final-worker pilot, then prove full attested `stock-deep-dive` compatibility. **No dynamic Planner and no second contract/digest implementation in this phase.**

**Architecture:** Phase 1 remains the sole owner of `WorkerCatalogSnapshot`, `PlanDraft`, `PlanValidationReport`, `Plan`, canonical digests and `freeze_plan`. Phase 2 adds authoritative stores and state transitions around those immutable values. The initial backend is in-memory, but every accepted transition is append-only and replayable. Legacy behavior is admitted only through a reviewed `LegacyGraphMapping`, `compat.*` WorkerSpecs and a matching `StaticLegacyPlanAttestation`; arbitrary `DAGNode` names never become runnable workers. Existing `workflow/executor.run_graph` and the legacy engine remain untouched.

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio` (`Semaphore`, `gather`, `run`), `pytest` + `pytest-asyncio` (or `asyncio.run` in sync tests). All modules `from __future__ import annotations`. Depends on Phase 1 contracts in `guanlan_v2/orchestration/`.

## Global Constraints

These extend, and never override, the Phase 1 Global Constraints and Exit Gates. Every task implicitly includes both documents.

- **Consume, do not fork.** Import Phase 1 models/builders from their owning modules. Phase 2 must not redefine canonical JSON, candidate/plan digest, WorkerSpec, catalog snapshot, schema registry, Artifact, RunEvent, Plan validation or freeze semantics.
- **Admission order is fixed.** Phase 1 validation → Phase 2 runtime-support report → atomic same-digest plan reservation → same-digest REQUIRED approval → Phase 1 freeze → append `PlanAdmitted` → dispatch. `AUTO` remains rejected for every PlanSource.
- **Stable ordering.** Completion/thread order must never change dependency injection, `artifact_seq` or sink input order. Order layers by Kahn depth, nodes within a layer by `PlanNode.id`, `many` inputs by Plan dependency declaration order, and committed artifacts by `(node_id, output_key)`.
- **Bounded concurrency.** Use the minimum of the frozen Plan budget request, active reservation, runtime limit and RunBudget limit. Never launch an unbounded layer.
- **Staged→barrier.** Worker output is `stage`d (journal-only, invisible) and becomes readable only when its whole layer atomically `commit_layer`s. A crash before the barrier must leave no visible downstream input.
- **Persist-then-publish + strict idempotency.** Every accepted state change appends a typed `RunEvent` using `SchemaRef + PayloadRef`. Repeating the same idempotency key with identical semantic content returns the stored event; different semantic content raises `IdempotencyConflict`. Rejected/unauthorized append attempts never enter the run journal and may be recorded only in a separate audit-only refusal sink.
- **Immutable visibility.** `ArtifactStaged.visible_seq` is always `None`. No event is updated in place and there is no `make_visible`; the public `LayerCommitted` event is the visibility boundary.
- **Budget is one event-sourced ledger.** Plan and node reservations bind request, candidate plan digest and exact token/invocation/concurrency amounts. Node reservations are children of the active plan reservation. Settlement cannot exceed reservation.
- **Catalog authority is read-only.** A runtime view is built from one verified `WorkerCatalogSnapshot`; it cannot register or mutate workers. Physical material resolution must match every referenced id/version/digest.
- **Runtime support is narrower than schema validity.** Static v1 supports LLM/deterministic workers, BLOCK/DEGRADE/SKIP, one/many named inputs, timeout, cancellation and barrier commit. It rejects conditions, reducers, debates, gates, stop conditions, `max_attempts > 1` and multi-writer slots before any budget reservation.
- **Legacy semantics come from evidence.** Hard/soft accepted statuses, missing-output behavior, base/upstream input projection and output slots come from the Phase 1 migration table/mapping, not from a new heuristic.
- **Digest discipline.** Runtime records call Phase 1 builders and verify persisted digests. Empty/short placeholders, JSON-mode ad-hoc hashing and blank `plan_digest`/snapshot hashes are forbidden.
- **Executable red/green checkpoints.** Every step named “Write failing … tests” immediately runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation. The later PASS step reruns the same focused tests plus the listed upstream regressions; merely writing a test without observing the red state is not completion evidence.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task 0: Phase 1 handoff gate (mandatory before Task 1)

Phase 2 work starts only after the Phase 1 Exit Gates pass. Add `tests/orchestration/test_phase2_handoff.py` as an executable consumer test rather than copying Phase 1 assertions.

**Files:**
- Create: `tests/orchestration/test_phase2_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. `schema_manifest_v1.json` and digest golden vectors pass, and `default_registry()` is sealed;
2. Phase 1 exports one canonical `compute_candidate_plan_digest`, `validate_plan_draft` and `freeze_plan`; Phase 2 contains no alternative projection;
3. `WorkerSpec`, `ExecutionSpec` and `EvidencePolicy` resolve from `catalog.py`, not `spec.py`;
4. `WorkerCatalogSnapshot` material/content/capability digests validate with the Phase 1 builder;
5. `RunEvent` accepts `SchemaRef + PayloadRef`; Phase 1 `PayloadRef` preserves exact schema/content with audit-only object relocation; `Artifact`, `InputSnapshot`, `NodeRun`, `BudgetReservation`, `PlanApproval`, `PlanValidationReport` and `StaticLegacyPlanAttestation` reject arbitrary digest strings;
6. the real Task 0 legacy fixture contains `scalars`, `workers` and `graphs`, including one frozen `stock-deep-dive` config digest plus complete `LegacyWorkerMapping`, `LegacyDependencyMapping` and `LegacyInputMapping` tuples;
7. canonical empty-memory facts persist in `main` and produce an exact ContextSnapshot binding with `memory_session_id=None`; session-scope drift is semantic and cannot be supplied by a Worker/model;
8. no Phase 2 source/test path overwrites the Phase 1-owned `catalog.py`, `spec.py`, `schema_registry.py`, `test_catalog.py`, `test_budget.py` or contract golden files.

If an exact field or builder name differs in the implemented Phase 1 public API, update this plan to that reviewed API before writing runtime code; do not invent an adapter with parallel semantics.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture**

Record only the exact Phase 1 schema-registry/catalog/migration/config digests and exported symbol signatures; never record local paths or mutable singleton identities.

- [ ] **Step 3: Run the complete Phase 1 suite and the frozen handoff gate**

Run from the repository state in which no Phase 2 runtime module exists yet: `pytest tests/orchestration -v`.

Expected: every Phase 1 test plus `test_phase2_handoff.py` PASS **after** the reviewed evidence has been recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase2_handoff.py
git commit -m "test(orchestration): gate phase2 on phase1 contracts"
```

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/budget.py` | event-sourced `BudgetLedger` over Phase 1 `RunBudget/BudgetReservation` |
| `guanlan_v2/orchestration/runtime_clock.py` | `AuthoritativeClock` service port and aware-UTC clock validation |
| `guanlan_v2/orchestration/eventstore.py` | registry-validated `PayloadStore`, append-only `EventStore`, dual cursors, replay and idempotency conflict |
| `guanlan_v2/orchestration/catalog_runtime.py` | read-only runtime index/material resolver over `WorkerCatalogSnapshot` |
| `guanlan_v2/orchestration/runtime_contracts.py` | Phase 2 control-plane facts plus registered ExecutionBridgeDescriptor and prompt evidence |
| `guanlan_v2/orchestration/runtime_support.py` | `StaticRuntimeProfile@1` and pure `RuntimeSupportReport` |
| `guanlan_v2/orchestration/admission.py` | service-owned validation→support→reservation→approval→freeze→`PlanAdmitted` coordinator |
| `guanlan_v2/orchestration/pool.py` | `ArtifactPool` staged→barrier + `freeze_input_snapshot` |
| `guanlan_v2/orchestration/worker.py` | trusted handler/model resolution, `CapabilityGateway`, typed Artifact/NodeRun builders |
| `guanlan_v2/orchestration/dag.py` | `run_plan` (layers, bounded parallel, gating, barrier, budget) |
| `guanlan_v2/orchestration/presets.py` | reviewed `LegacyGraphMapping` → attested static PlanDraft adapter |
| `config/orchestration/catalogs/` + `config/orchestration/materials/` | service-owned physical sources for only the three pilot and reviewed compatibility materials; paths never enter Plan |
| `tests/orchestration/golden/runtime_schema_manifest_v1.json` | reviewed Phase 2 control-payload registry manifest; does not replace Phase 1 golden |
| `tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json` | deterministic full-graph old/new equivalence cases keyed to Phase 1 mapping/config digests |
| `tests/orchestration/test_phase2_handoff.py` | executable Phase 1→2 ABI/golden gate |
| `tests/orchestration/test_budget_ledger.py` | Phase 2 ledger tests; does not replace Phase 1 `test_budget.py` |
| `tests/orchestration/test_catalog_runtime.py` | runtime resolver/pilot catalog tests; does not replace Phase 1 `test_catalog.py` |
| `tests/orchestration/` | remaining source-focused runtime and end-to-end tests |

---

## Task 1: Event-sourced budget ledger

**Files:**
- Create: `guanlan_v2/orchestration/budget.py`
- Create: `guanlan_v2/orchestration/runtime_clock.py`
- Test: `tests/orchestration/test_budget_ledger.py`
- Test: `tests/orchestration/test_runtime_clock.py`

**Consumes:** Phase 1 `RunBudget`, `BudgetReservation`, candidate-plan digest contract, plus an injected append-only `BudgetEventSink` protocol and `AuthoritativeClock`. Task 2 supplies the production in-memory PayloadStore/EventStore.

**Produces:**

- `BudgetExceeded`, `InvalidBudgetTransition`.
- `AuthoritativeClock(Protocol)`: `now() -> UtcDateTime`. Runtime services accept this port, reject naive datetimes and do not call `datetime.now()` directly; tests use a deterministic fixed/advancing implementation.
- strict internal `BudgetTransitionCommand(operation, semantic_args, idempotency_key)`: a closed `reserve_plan | reserve_node | settle | release` command with no caller-selected reservation ID, cursor or timestamp. `BudgetLedger` can validate it purely against a supplied folded state; the injected sink assigns service IDs/time and commits the corresponding typed budget event. Task 2 may include this declared command in a wider RuntimeUnitOfWork without accepting an arbitrary callback.
- `BudgetLedger`, rebuilt from budget events rather than a mutable reservation dict:
  - `reserve_plan(*, request_id, candidate_plan_digest, budget_request, idempotency_key) -> BudgetReservation`;
  - `reserve_node(*, plan_reservation_id, node_id, attempt, tokens, llm_invocations, concurrency, idempotency_key) -> BudgetReservation`;
  - `settle(reservation_id, *, actual_tokens, actual_llm_invocations, idempotency_key) -> BudgetReservation`;
  - `release(reservation_id, *, reason, idempotency_key) -> BudgetReservation`;
  - `get(reservation_id)`, `get_active_plan(request_id, candidate_plan_digest)`, `available()`, and `replay(...)`.

The implementation must construct each new Phase 1 `BudgetReservation` through its validated builder. It must not mutate a frozen reservation with unvalidated `model_copy(update=...)`.

**Required invariants:**

1. the plan reservation binds ledger ID, request ID, candidate plan digest and the exact token/invocation/concurrency budget request;
2. every node reservation is a child of that active plan reservation and carries the same request/candidate/ledger identity;
3. total outstanding + settled actual usage never exceeds the run or plan reservation;
4. actual values cannot exceed the reservation; bool/negative values fail through Phase 1 strict types;
5. deterministic nodes reserve zero LLM invocations; LLM nodes reserve per allowed attempt;
6. reserve/settle/release is atomic with its typed budget event; the public convenience methods use the sink's single-command transaction, while Task 2's production sink may validate/commit the exact same `BudgetTransitionCommand` inside a larger RuntimeUnitOfWork;
7. identical idempotent calls return the same record, while conflicting calls fail;
8. invalid state transitions (double settle, settle after release, child of inactive plan) fail without appending an accepted event;
9. approval rejection, admission failure or cancellation releases unused plan/node reservations;
10. replay produces the same availability and active-reservation index.

- [ ] **Step 1: Write failing ledger tests**

Cover aware authoritative-clock injection/naive rejection, request/candidate/concurrency binding, parent-child scope, concurrent over-reservation, transition matrix, actual-over-reserved rejection, idempotency conflict, rejection release and event replay. Prove callers cannot select reservation ID/cursor/time, the closed BudgetTransitionCommand validator is deterministic over the same folded state, and same-head concurrent commands cannot over-reserve. Task 2 later reuses these exact command vectors in its production transaction tests.

Run now: `pytest tests/orchestration/test_runtime_clock.py tests/orchestration/test_budget_ledger.py -v`.

Expected: FAIL on the missing clock/ledger contract or intended transition assertions before implementation; unrelated collection/environment failures do not count as the red checkpoint.

- [ ] **Step 2: Implement event-sourced transitions**

Reservation authority is the ledger fold, not a caller-carried `BudgetReservation`. Implement transitions against an injected strict append-only sink and use a fake sink in this focused test; Task 2 adds the real store/replay integration. Expose a lookup by reservation ID for admission/dispatch.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_runtime_clock.py tests/orchestration/test_budget_ledger.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/runtime_clock.py guanlan_v2/orchestration/budget.py tests/orchestration/test_runtime_clock.py tests/orchestration/test_budget_ledger.py
git commit -m "feat(orchestration): add candidate-bound event-sourced budget ledger"
```

---

## Task 2: Typed payload store + append-only event store

**Files:**
- Create: `guanlan_v2/orchestration/eventstore.py`
- Create: `guanlan_v2/orchestration/runtime_contracts.py` (initial refusal-audit contracts; Task 5 appends the remaining runtime-control facts)
- Test: `tests/orchestration/test_eventstore.py`
- Test: `tests/orchestration/test_event_refusal.py`

**Consumes:** Phase 1 `SchemaRegistry`, `SchemaRef`, `PayloadRef`, `RunEvent`, `EventCursor`, `LayerCommit` and their pure builders, plus Task 1 `BudgetLedger`, `BudgetEventSink` and closed `BudgetTransitionCommand`.

**Produces:**

- `SchemaRegistryResolver`: a service-owned read-only map from reviewed registry digest to one sealed registry snapshot. Registration verifies the declared manifest/digest, rejects conflicts, and never treats a mutable global "latest" registry as authority. A run resolves the exact registry digest bound by its admitted Plan.
- `PayloadStore`: registry-validated in-memory payload persistence. `put(schema_ref, payload, *, namespace, idempotency_key) -> PayloadRef` validates against the sealed registry, verifies/computes content digest and assigns only the audit locator `object_id`; `get(ref, *, expected_schema_ref) -> validated payload` re-verifies namespace/content/schema before returning.
- `IdempotencyConflict`: raised when one idempotency key is reused with different semantic content.
- `NamedEvidenceDigest@1`, `GenericRefusalDetails@1` and `EventRefusalRecord@1`: strict audit-only facts. `EventRefusalRecord` binds reason/code, attempted capability/schema/namespace, a typed detail `SchemaRef + PayloadRef` in the audit namespace, canonically ordered named evidence digests and audit identity/time; raw rejected bytes/secrets are never fields.
- `EventRefusalAuditSink.record(*, detail_schema_ref, detail_payload, reason_code, attempted_capability_ref=None, attempted_schema_ref=None, attempted_namespace=None, evidence_digests=(), idempotency_key) -> EventRefusalRecord`: the sink is the **single owner** of refusal-detail validation, audit-namespace persistence and record creation. A same-key semantic conflict fails. Refusals are not `RunEvent`, receive no journal/visible sequence and cannot expose rejected payloads through the public stream.
- exported stable runtime-port DTO `EventAppendRequest`: the fields known before persistence (`run_id`, partition/type, `SchemaRef`, `PayloadRef`, idempotency/causation/plan identity), but no caller-selected sequence or occurred-at. It is strict but not itself a persisted public event schema.
- `EventStore`: constructed with `SchemaRegistryResolver` and `AuthoritativeClock`; `append(request: EventAppendRequest) -> RunEvent`, `journal(run_id, partition)`, `visible(run_id, partition, after=0)`, cursor helpers and deterministic replay. Under the store lock it stamps aware time, allocates sequences and invokes the Phase 1 RunEvent builder.
- internal service-only `RuntimeUnitOfWork` over the production PayloadStore/EventStore/BudgetEventSink's same copy-on-write backend/lock atomically commits a declared tuple of registry-validated PayloadStore puts, EventStore appends and closed BudgetTransitionCommands, or none. Under that lock it folds the current ledger head, calls BudgetLedger's pure command validator, then assigns all object IDs/cursors/reservation IDs/timestamps only at commit. It exposes no arbitrary callback. Task 4 layer commit, Task 6 candidate+reservation and Plan+PlanAdmitted, and Task 7 evidence-payload+`BridgeEvidenceRecorded` use this one boundary. Semantic idempotency keys cover the whole batch; same-key drift conflicts, crash before commit exposes none and replay after commit exposes all.

**Required invariants:**

1. the store accepts only a strict append request whose `SchemaRef` resolves and whose `PayloadRef` exists with the same content digest; the resulting record must be a Phase 1-valid immutable `RunEvent`;
2. registry resolution uses the exact admitted digest; adding a later sealed Phase 3 registry cannot reinterpret payloads or replay from a Phase 1/2-bound Plan;
3. the store, never the caller, assigns monotonic `journal_seq` per run/partition; `visible_seq` is assigned only while initially building a Phase 1-valid public event;
4. `ArtifactStaged.visible_seq` is always `None`; no `make_visible` API exists;
5. `LayerCommitted` is the public barrier and refers to canonically ordered committed artifact refs;
6. main/public namespace restrictions from Phase 1 are rechecked before persistence; `sealed`, `review` and `audit` refs can never back a main/public event;
7. an identical retry returns the existing event; a same-key/different-event retry raises `IdempotencyConflict`;
8. events are never changed with `model_copy(update=...)`; facts change only through a new event;
9. replay from the persisted payloads/events reproduces cursors and visibility indexes without trusting cached maps;
10. a rejected append leaves both payload visibility and the run journal unchanged; its typed audit detail is recorded exactly once by `EventRefusalAuditSink` and cannot be read through main/public APIs.
11. a RuntimeUnitOfWork validates every payload/event/budget transition before allocating any cursor/reservation/object visibility; a failure cannot leave an orphan payload, reservation, bridge journal row or half-published admission pair.

- [ ] **Step 1: Write failing store tests**

Required tests:

- typed payload put/get and wrong SchemaRef/content digest/namespace/registry-digest rejection;
- simultaneous resolution/replay of old Phase 1, current Phase 2 and a fake later sealed registry without "latest" aliasing;
- journal/visible dual-cursor monotonicity;
- staged event permanently invisible and LayerCommitted visible;
- identical idempotent retry versus conflicting retry;
- sealed/review/audit payload cannot enter a main-public event;
- invalid append produces only one typed refusal-audit record with a generic safe detail; Phase 3-style typed detail schemas are accepted through a later exact registry digest without changing the wrapper ABI;
- callers cannot set event cursors or occurred-at, and a naive/invalid authoritative clock is rejected;
- reverse/replayed construction yields the same journal, cursors and visible view;
- crash before the LayerCommitted atomic batch exposes no artifact; replay after the batch exposes all of them.
- crash-before/after matrices for payload+event, candidate+reservation and Plan+PlanAdmitted batches prove all-or-none replay; an injected mid-batch failure leaves no orphan payload/reservation/event.

Run now: `pytest tests/orchestration/test_eventstore.py tests/orchestration/test_event_refusal.py -v`.

Expected: FAIL on the missing store/refusal behavior before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement append-only stores**

Use a lock plus copy-on-write state for the in-memory transaction boundary. This is a behavioral stand-in for later durable storage, not permission to mutate accepted records.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase2_handoff.py tests/orchestration/test_eventstore.py tests/orchestration/test_event_refusal.py tests/orchestration/test_budget_ledger.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/eventstore.py guanlan_v2/orchestration/runtime_contracts.py tests/orchestration/test_eventstore.py tests/orchestration/test_event_refusal.py
git commit -m "feat(orchestration): add typed append-only event store"
```

---

## Task 3: Read-only catalog runtime + material resolver

**Files:**
- Create: `guanlan_v2/orchestration/catalog_runtime.py`
- Create: `config/orchestration/catalogs/phase2-pilot-v1.yaml`
- Create: only the prompt/SKILL/guardrail materials referenced by the reviewed three-worker pilot under `config/orchestration/materials/phase2-pilot-v1/`
- Test: `tests/orchestration/test_catalog_runtime.py`

**Consumes:** Phase 1 `WorkerCatalogSnapshot`, `WorkerSpec`, content/skill/capability manifests, `ResolvedMaterial`, `build_catalog_snapshot` and `validate_catalog_snapshot`.

**Produces:**

- `MaterialSource(Protocol)`: service configuration maps a logical ref to bytes/descriptor; this physical locator is never exposed in WorkerSpec or Plan.
- `CatalogMaterialError`.
- `CatalogRuntime`: immutable runtime index constructed from one snapshot and exact resolved materials. It exposes lookup/resolution only: `worker(worker_id)`, `text(ContentRef)`, `capability(CapabilityRef)`, `resolve_worker(worker_id) -> ResolvedWorkerRuntime`, and the pinned snapshot/catalog digest.
- `ResolvedWorkerRuntime`: service-owned prompt/skill/guardrail/handler/capability materials already checked against the selected WorkerSpec. It cannot add or replace a binding.
- a trusted handler/model-factory registry keyed by the full catalog ref identity, never by caller-provided callable or path.

There is deliberately no `register(spec)`, mutable version, or second catalog digest implementation in Phase 2.

**Pilot catalog scope:**

Freeze exactly three final workers before the Task 9 pilot. The recommended chain, because it consumes the three Phase 1 compatibility payloads, is:

`text.sentiment -> dec.research_mgr -> dec.pm`

with primary output schemas `SentimentReport`, `ResearchPlan`, and `PortfolioDecision`. If repository evidence selects another triad, update the Task 0 worker map and this plan together before implementation. Each pilot worker must be `catalog_role="final"`, `selection_scope="dynamic_allowed"`, complete, and use actual Phase 1-valid prompt/SKILL/guardrail/capability material. `dec.pm` remains advisory-only and gains no trading authority. This task does not finalize the other 21 redesigned workers.

**Required invariants:**

1. rebuild the snapshot using the Phase 1 material-aware builder and require exact catalog digest equality;
2. strict UTF-8/NFC/LF and skill-v1 envelope rules are inherited, not reimplemented differently;
3. missing, extra, duplicate or content-drifted material fails startup;
4. handler/model/tool resolution cannot escape the selected WorkerSpec refs;
5. a snapshot is immutable for a run; publishing another catalog creates a new version/digest and never changes an old Plan;
6. final workers remain ordinary candidates; `compat.*` workers remain `static_legacy_only` and gain no authority merely by resolving;
7. a compatibility worker is not counted toward the final 24 or the three-worker pilot;
8. no physical path occurs in the snapshot, WorkerSpec, PlanDraft or Plan.

- [ ] **Step 1: Write failing runtime/material tests**

Test exact material resolution, drift/missing/extra material rejection, immutable snapshot lookup, handler-ref confinement, pilot worker role/scope/output schemas, compat exclusion and absence of physical paths.

Run now: `pytest tests/orchestration/test_catalog_runtime.py -v`.

Expected: FAIL on missing resolver/material confinement before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Add only the reviewed pilot materials and resolver**

Do not write all 24 playbooks. The three pilot SKILL files must satisfy Phase 1's machine-readable trigger/data-source-priority envelope and contain enough reviewed behavior to be runnable through a fake model gateway in Task 9.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_catalog.py tests/orchestration/test_catalog_runtime.py -v`

Expected: PASS; the first file is the unchanged Phase 1 contract suite.

```bash
git add guanlan_v2/orchestration/catalog_runtime.py config/orchestration/catalogs/phase2-pilot-v1.yaml config/orchestration/materials/phase2-pilot-v1 tests/orchestration/test_catalog_runtime.py
git commit -m "feat(orchestration): add read-only catalog material runtime"
```

---

## Task 4: ArtifactPool staged→barrier commit

**Files:**
- Create: `guanlan_v2/orchestration/pool.py`
- Test: `tests/orchestration/test_pool.py`

**Consumes:** Phase 1 `Artifact`, `ArtifactRef`, `InputSnapshot`, `NodeRun`, `LayerCommit`, their builders/validators, sealed SchemaRegistry, plus the Task 2 stores.

**Produces:**

- `ArtifactPool`, scoped to one run and frozen Plan:
  - `stage(artifact, *, layer_index, node_run, idempotency_key) -> ArtifactRef`;
  - `commit_layer(layer_index, *, node_runs, expected_outputs, idempotency_key) -> LayerCommit`;
  - `committed(ref) -> Artifact`;
  - `committed_output(node_id, output_key) -> Artifact | None`;
  - `freeze_input_snapshot(node, *, context_snapshot_ref, bound_dependencies, data_result_ids=(), memory_record_refs=()) -> InputSnapshot`;
  - `replay(...) -> ArtifactPool`.

`expected_outputs` is derived from the frozen Worker's `OutputBinding` for actual COMPLETED/DEGRADED NodeRuns; non-success NodeRuns require no Artifact. It is never a caller-written slot set.

**Required invariants:**

1. stage verifies run/plan/node/output binding, payload SchemaRef, content/reproducibility/audit digests, provenance and rendered-payload binding through Phase 1 functions;
2. the Artifact and ArtifactStaged payload are persisted before returning, but the artifact is not readable as committed;
3. downstream/runtime dataflow reads artifacts only through the pool's committed index; possession of a low-level staged PayloadRef does not authorize an input read;
4. one layer commit atomically validates all expected outputs, assigns canonical `artifact_seq`, stores the typed LayerCommit payload, appends one public LayerCommitted event and advances the committed index;
5. ArtifactStaged events remain journal-only forever;
6. a crash before the atomic commit exposes none of the layer; replay after commit exposes all;
7. identical stage/commit retries are idempotent; conflicting retries raise `IdempotencyConflict`;
8. late stage after commit and duplicate `(node_id, output_key)` with different content fail;
9. every LayerCommit and InputSnapshot binds the real plan digest; no blank digest or fabricated NodeRun ID is allowed;
10. InputSnapshot contains exactly the selected node's named inputs available **before node start**. `one` has one ArtifactRef; `many` preserves Plan dependency declaration order. Pre-existing Context, DataResult and memory refs enter through their Phase 1 fields/builders;
11. DataResult/PayloadRefs obtained during node execution do not mutate or replace the pre-node InputSnapshot; they bind ToolCallRecord, NodeRun evidence and final Artifact provenance instead;
12. retrieval resolves the exact committed ArtifactRef/content digest, not “latest value by slot”;
13. replay derives committed visibility only from LayerCommitted events and their typed refs.

- [ ] **Step 1: Write failing barrier/snapshot tests**

Required tests:

- invalid payload/provenance/digest rejected at stage;
- staged artifact unavailable before barrier;
- canonical artifact sequence independent of worker completion order;
- missing/extra/conflicting output prevents the whole commit;
- crash-before/crash-after replay matrix;
- ArtifactStaged never gains visible sequence;
- same commit retry succeeds, conflicting retry fails;
- late stage fails;
- one/many InputSnapshot ordering and digest binding;
- context/data/memory/artifact change alters snapshot content digest;
- no empty plan/snapshot/provenance digest can be persisted.

Run now: `pytest tests/orchestration/test_pool.py -v`.

Expected: FAIL on missing staged/barrier/snapshot behavior before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement pool over the Task 2 transaction boundary**

Do not manually hash `model_dump(mode="json")`. Use the Phase 1 Artifact/InputSnapshot/LayerCommit builders and validate every persisted record on replay.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_pool.py tests/orchestration/test_eventstore.py tests/orchestration/test_artifact.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/pool.py tests/orchestration/test_pool.py
git commit -m "feat(orchestration): add atomic replayable artifact barriers"
```

---

## Task 5: Static runtime profile + support report

**Files:**
- Modify: `guanlan_v2/orchestration/runtime_contracts.py`
- Modify: `guanlan_v2/orchestration/catalog_runtime.py`
- Create: `guanlan_v2/orchestration/runtime_support.py`
- Create: `tests/orchestration/golden/runtime_schema_manifest_v1.json`
- Test: `tests/orchestration/test_runtime_contracts.py`
- Test: `tests/orchestration/test_runtime_support.py`
- Test: `tests/orchestration/test_catalog_runtime.py`

**Consumes:** the exact Phase 1 `PlanDraft`, `PlanValidationReport`, sealed catalog/registry snapshots and shared enums. It does not replace `validate_plan_draft`.

**Produces:**

- `StaticRuntimeProfile@1`: immutable `DigestModel` with closed `schema_version="1"`, `profile_id="static-runtime"`, `profile_version="1"` and an explicit fixed feature matrix.
- `RuntimeSupportIssue@1`: stable issue code, model path and explanation.
- `RuntimeSupportReport@1`: immutable, canonically ordered issues plus:
  - `supported`;
  - `candidate_plan_digest`;
  - Phase 1 validation-report digest;
  - runtime-profile digest;
  - catalog and schema-registry digests;
  - canonically ordered active execution-bridge descriptor/config/handler `ContentRef`s;
  - checker version.
- the remaining Phase 2 immutable control facts: `AdmissionCandidate@1`, `PlanAdmitted@1`, and `RunResult@1`. Task 2 already owns `NamedEvidenceDigest/GenericRefusalDetails/EventRefusalRecord`; Tasks 6 and 8 own state-transition behavior, not alternate model definitions.
- `ExecutionBridgeDescriptor@1`: strict registered generic descriptor with `descriptor_kind=Literal["execution_bridge"]`, closed version, stable bridge ID/version, strict priority, exact provider-handler/config `ContentRef`s, config `SchemaRef`, canonical activation tuples of exact capability refs and read categories, supported execution kinds, `pre_input_kind: Literal["none","memory_refs_v1"]`, `lifecycle: Literal["static_prefetch_v1"]` and `required: Literal[True]`. At least one activation tuple is non-empty; a descriptor activates for a WorkerSpec when **any** listed capability is in its allowlist or any listed category is in its read categories. There is no caller override/negative predicate. Its full canonical JSON is a catalog `kind="guardrail"` material; handler is `kind="handler"`, config is a schema-validated `kind="guardrail"` material. `CatalogRuntime` indexes only marker-bearing strict descriptors and ignores ordinary guardrail prose; malformed/unknown marker versions fail catalog resolution.
- `PromptUntrustedBlockRef@1`: immutable ordered block envelope with a one-based strict positive ordinal (the first block is `1`), exact Phase 1 `PayloadRef`, reviewed media type, bounded non-negative rendered length and verified block digest. The typed ref must use `main`; its payload object ID is dereference/audit identity while SchemaRef + namespace/content are semantic.
- `PromptAssemblyRecord@1`: immutable persisted execution evidence binding plan/node/worker, exact assembler ID/version, system-prompt/ordered skill/guardrail identities, canonically named trusted-input digests, ordered `PromptUntrustedBlockRef`s, canonical model-request digest and verified assembly digest. It contains refs/digests, not duplicated raw untrusted bytes, and is persisted in `main` before model invocation.
- `BridgeEvidenceRecorded@1`: strict main-namespace control fact binding run/plan/node, exact `ExecutionEvidenceOrdinalToken` projection, within-call role and the already persisted main `PayloadRef`. The service-owned bridge journal appends it idempotently immediately after each evidence put; it is recovery metadata, not a replacement for the referenced evidence or NodeRun tuple.
- `check_runtime_support(draft, *, phase1_report, catalog, bridge_view, schema_registry, profile) -> RuntimeSupportReport`: pure and I/O-free. `bridge_view` is the immutable, already material-verified view produced by CatalogRuntime from that exact catalog digest; it is not a mutable provider registry.
- `PHASE2_PUBLIC_MODELS`, `PHASE2_BASE_REGISTRY_DIGEST` and `phase2_runtime_registry(expected_phase1_digest)`: a deterministic sealed registry containing the reviewed Phase 1 public models plus all Phase 2 runtime-control and prompt-evidence payloads above. The builder first requires the exact Phase 1 digest, and its tests prove the inherited Phase 1 schema subset is byte-identical. It has its own reviewed golden manifest and never mutates or regenerates the Phase 1 default registry/golden.
- every new Phase 2 PlanDraft/Plan uses the cumulative Phase 2 registry digest. Previously persisted Phase 1 registry-bound Plans remain resolvable and replayable through `SchemaRegistryResolver`; they are never silently rebound.

**StaticRuntimeProfile v1 support matrix:**

Supported:

- already Phase 1-validated **main** static DAGs from `PRESET`, `PRESET_FALLBACK`, or `DYNAMIC`; Phase 2 itself never invokes a Planner, and `PlanSource` never grants authority or bypasses REQUIRED approval;
- `ExecutionKind.LLM` and `DETERMINISTIC`;
- `DependencyPolicy.BLOCK`, `DEGRADE`, and `SKIP`;
- `InputBinding.cardinality` one and many;
- typed params and exact v1 SchemaRef input/output equality;
- bounded layers, staged→barrier commit;
- per-node timeout and run cancellation.
- reviewed `ExecutionBridgeDescriptor@1` with the exact two-phase `memory_refs_v1` pre-input or no-pre-input path followed by `static_prefetch_v1`; all provider-selected calls/parameters come from the admitted Plan's validated node params/InputSnapshot through the schema-validated config, never model-generated dynamic expansion. Static v1 performs at most one prompt assembly and one ModelGateway invocation per LLM node.

Rejected before budget reservation:

- `BOOTSTRAP` / no-ContextSnapshot Lane 0 execution, which remains a Phase 5 runtime profile;
- condition refs;
- reducers and any slot with multiple writers;
- debates;
- gates/gate metrics;
- stop conditions;
- `max_attempts > 1` or any repair/retry request;
- missing/drifted descriptor/config/handler material, unknown bridge version/lifecycle, an unregistered provider, model-driven/dynamic late bridge calls, a multi-round tool-result→prompt/model loop, or any provider feature outside the two-phase static-prefetch matrix;
- an LLM node with `tool_calls=REQUIRED` unless its exact activated static-prefetch config guarantees at least one WorkerSpec-allowed finalized capability call; conversely `FORBIDDEN` rejects any activated prefetch call. Phase 1 schema validity alone does not promise that runtime behavior;
- any runtime construct not explicitly listed as supported.

Dependency runtime meanings are closed:

- unsatisfied `BLOCK` → downstream `BLOCKED`;
- unsatisfied `DEGRADE` → omit only that input, execute, and terminal success is at least `DEGRADED`;
- unsatisfied `SKIP` → downstream `SKIPPED`, no handler/model call and no output;
- every dependency waits for a terminal upstream state;
- for an attested legacy Plan, the selected policy/missing behavior must equal its reviewed `LegacyDependencyMapping`.

**Required invariants:**

1. a support report is emitted only for a valid, exact-input Phase 1 report; it cannot turn an invalid Plan valid;
2. candidate/catalog/registry/report/profile digest mismatches fail;
3. issue order is deterministic and report construction has no ledger/event side effect;
4. changing the profile or executable draft changes the bound report;
5. unknown future fields/features are rejected rather than assumed supported;
6. compatibility workers remain subject to the matching legacy attestation already checked by Phase 1;
7. `EventRefusalRecord` and its generic detail/evidence contracts remain exactly the Task 2 ABI; the cumulative registry adds them without redefinition, and rejected raw content is never a field;
8. AdmissionCandidate/PlanAdmitted/RunResult field matrices match Tasks 6/8 and reject extra fields/mutation;
9. the runtime registry manifest is complete, reviewed and never regenerated automatically; its declared base digest equals Phase 1 and every inherited Phase 1 JSON Schema is byte-identical.
10. the runtime checker/store accept a later reviewed cumulative registry snapshot by exact digest without changing Phase 1/2 schema identities or hard-coding the Phase 2 digest as "latest".
11. PromptAssemblyRecord/PromptUntrustedBlockRef are registered, main-only, strict and order-sensitive; changing a block SchemaRef/content/order changes assembly digest, while object-ID relocation changes audit/dereference identity only.
12. active bridge descriptors are derived from their exact activation predicates against each admitted WorkerSpec, not caller choice; every active descriptor/config/handler ref is bound into the support report, and unsupported/missing bridge semantics produce deterministic issue paths before reservation.
13. one catalog may contain at most one descriptor per `bridge_id` and one exact `(priority, bridge_id)` key; duplicate ID/version, competing versions, or the same identity bound to different descriptor/config/handler refs fails CatalogRuntime indexing and RuntimeSupport before reservation.

- [ ] **Step 1: Write the failing support-matrix tests**

Include public-model completeness/golden tests (including `ExecutionBridgeDescriptor`, `BridgeEvidenceRecorded`, `PromptUntrustedBlockRef` and `PromptAssemblyRecord`), then one accepted fixture for every supported execution/dependency/cardinality/bridge branch and one rejection for every deferred feature. Prove active descriptors/material refs enter RuntimeSupportReport; missing/drifted provider material, duplicate/competing bridge identity, unknown lifecycle and dynamic/tool-loop config are refused before reservation. Assert the EventStore and BudgetLedger remain unchanged after both supported and rejected checks.

Run now: `pytest tests/orchestration/test_runtime_contracts.py tests/orchestration/test_runtime_support.py -v`.

Expected: FAIL on the missing support models/checker before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement the pure checker and runtime registry manifest**

Reuse Phase 1 topology/catalog/schema validation results. Do not copy their algorithms.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_runtime_contracts.py tests/orchestration/test_runtime_support.py tests/orchestration/test_catalog_runtime.py tests/orchestration/test_plan_catalog_validation.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/runtime_contracts.py guanlan_v2/orchestration/runtime_support.py guanlan_v2/orchestration/catalog_runtime.py tests/orchestration/test_runtime_contracts.py tests/orchestration/test_runtime_support.py tests/orchestration/test_catalog_runtime.py tests/orchestration/golden/runtime_schema_manifest_v1.json
git commit -m "feat(orchestration): freeze static runtime support profile"
```

---

## Task 6: Service-owned Plan admission and freeze

**Files:**
- Create: `guanlan_v2/orchestration/admission.py`
- Test: `tests/orchestration/test_admission.py`

**Consumes:** authoritative stores for request/draft/context/catalog/registry/legacy mapping and attestation, exact CatalogRuntime material resolver/immutable bridge view, Phase 1 `validate_plan_draft` / `freeze_plan`, Task 5 frozen runtime contracts/support checker, BudgetLedger, PayloadStore/EventStore, Task 2's service-only `RuntimeUnitOfWork` and Phase 1 `PlanApproval`.

**Produces:**

- `AdmissionRejected`.
- service transitions that construct the Task 5-owned immutable `AdmissionCandidate@1`, which binds exact request/draft/context/catalog/registry/legacy-attestation inputs, Phase 1 report, RuntimeSupportReport and one candidate plan digest but contains no approval or reservation authority;
- service transitions that construct the Task 5-owned immutable `PlanAdmitted@1`, which binds:
  - request/candidate/final plan digest;
  - Phase 1 validation-report digest;
  - RuntimeSupportReport and StaticRuntimeProfile digests;
  - context/catalog/schema-registry digests;
  - active plan BudgetReservation ID and semantic digest;
  - approved PlanApproval event ID/digest;
  - matching legacy-attestation digest when present;
  - persisted frozen Plan PayloadRef.
- strict internal `PreparedAdmissionCandidate`, which carries the fully validated candidate contents and report refs but is neither persisted authority nor approval/reservation evidence;
- `PlanAdmissionService` with separate state transitions:
  - `prepare_candidate(draft_id, *, authoritative_refs...) -> PreparedAdmissionCandidate`;
  - `persist_and_reserve_candidate(preparation, *, idempotency_key) -> (AdmissionCandidate, BudgetReservation)`;
  - `record_approval(candidate_id, approval_input, *, authenticated_actor, idempotency_key) -> RunEvent`;
  - `freeze_and_admit_candidate(candidate_id, *, reservation_id, approval_event_id, idempotency_key) -> (Plan, PlanAdmitted)`;
  - `load_admitted(plan_digest) -> (Plan, PlanAdmitted)`;
  - `verify_for_dispatch(plan_digest) -> verified authoritative bundle`.

The public/service API receives identifiers and authenticated approval input. It loads Reservation, PlanApproval and StaticLegacyPlanAttestation from authoritative stores; callers cannot submit constructed records as authority.

**Normative state sequence:**

1. load immutable request/draft/context/catalog/registry and optional service-owned legacy attestation; resolve/verify the catalog's immutable bridge descriptor/config/handler view with no provider execution;
2. call Phase 1 `validate_plan_draft`;
3. call `check_runtime_support` with that exact bridge view;
4. persist both reports as typed payloads — no budget event may exist before this succeeds; if `RuntimeSupportReport.supported=False`, return the diagnostic and stop with no AdmissionCandidate/reservation;
5. only for `supported=True`, use one Task 2 `RuntimeUnitOfWork` to persist AdmissionCandidate and reserve the exact candidate/budget request as one all-or-none transition; `PreparedAdmissionCandidate` alone is not loadable authority;
6. append an APPROVED or REJECTED `PlanApproval` event for the same request/candidate digest;
7. on rejection, release the reservation and stop;
8. on approval, reload all authoritative inputs, rerun Phase 1 validation and runtime support, require byte/semantic equality with the stored reports, and require the active reservation/approval/attestation to bind the same digest;
9. call the Phase 1 `freeze_plan`; never recompute its projection locally;
10. use one Task 2 `RuntimeUnitOfWork` to persist the frozen Plan payload and append public `PlanAdmitted` as one all-or-none transition;
11. dispatch only through `verify_for_dispatch`, which recomputes/verifies the persisted Plan digest and checks the same catalog/registry/context plus active reservation.

**Required invariants:**

- `ApprovalPolicy.AUTO` fails for every source;
- PRESET provenance alone grants no approval;
- REJECTED, missing, wrong-request, wrong-candidate or unauthenticated approval fails;
- a caller-carried reservation/approval/attestation object is ignored/rejected;
- RuntimeSupportReport is created before reservation and its digest is included in PlanAdmitted;
- any changed draft/request/context/catalog/registry/profile/attestation after preparation invalidates the candidate and releases unused reservation;
- all selected `compat.*` bindings match one service-owned attestation; Phase 1 rejects compatibility workers under DYNAMIC/BOOTSTRAP, and this Phase 2 profile independently rejects every BOOTSTRAP Plan;
- freeze/reservation/approval IDs and wall-clock do not cause a second plan digest;
- candidate persistence and plan-budget reservation are one semantic idempotency boundary: before commit neither is visible, after commit both are replayable, and a conflicting retry exposes neither a second candidate nor a second reservation;
- frozen Plan persistence and `PlanAdmitted` append are one semantic idempotency boundary: dispatch can observe both or neither, never an orphan Plan or a dangling admitted event;
- identical admission retries return the existing PlanAdmitted; conflicting retries fail;
- there is no Phase 2 `compute_plan_digest` or alternative `freeze_plan`.

- [ ] **Step 1: Write failing admission tests**

Cover the full happy-path ordering and every missing/mismatched/forged record; assert no reservation before both reports; assert PlanAdmitted binds both report digests; assert rejection releases reservation; assert dispatch fails after catalog/reservation drift; assert AUTO and spoofed PRESET fail. Add crash-before/crash-after and injected mid-commit matrices for both RuntimeUnitOfWork boundaries: candidate+reservation and Plan+PlanAdmitted must replay all-or-none, retries must reuse the same semantic batch, and no orphan reservation, Plan payload or admission event may remain.

Run now: `pytest tests/orchestration/test_admission.py -v`.

Expected: FAIL on missing service-owned admission/order enforcement before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement stores/coordinator over Phase 1 builders**

The in-memory repositories are service-owned test doubles for later persistence. Keep each transition append-only and replayable.

- [ ] **Step 3: Run focused and Phase 1 freeze tests**

Run: `pytest tests/orchestration/test_admission.py tests/orchestration/test_runtime_support.py tests/orchestration/test_plan_catalog_validation.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add guanlan_v2/orchestration/admission.py tests/orchestration/test_admission.py
git commit -m "feat(orchestration): add authoritative static Plan admission"
```

---

## Task 7: Worker executor (typed Artifact + NodeRun + evidence)

**Files:**
- Create: `guanlan_v2/orchestration/worker.py`
- Test: `tests/orchestration/test_worker.py`

**Consumes:** admitted frozen Plan bundle, Task 5's exact `CatalogRuntime` bridge view/`ExecutionBridgeDescriptor`, `ResolvedWorkerRuntime`, exact Phase 1 `InputSnapshot`, `RunContext`, Artifact/Provenance/NumberAnchor/ToolCallRecord/NodeRun/PayloadRef builders, registered `PromptUntrustedBlockRef/PromptAssemblyRecord`, schema registry, PayloadStore/EventStore, Task 2's service-only `RuntimeUnitOfWork`, `AuthoritativeClock` and an active child budget reservation.

**Produces:**

- strict internal `WorkerExecutionResult`: typed primary payload, rendered text, NumberAnchors, Phase 1 ToolCallRecords, DataResult/PayloadRefs, bridge-supplied ordered `execution_evidence_refs: tuple[PayloadRef, ...]`, `prompt_assembly_ref: PayloadRef | None`, token usage and explicit degradation reasons. Complete untrusted prompt-block refs live in the referenced public PromptAssemblyRecord, while non-prompt runtime evidence such as memory query/selection refs remains directly in `execution_evidence_refs`. The closed execution-kind matrix is: `LLM` requires one persisted main-namespace prompt ref; `DETERMINISTIC` requires `None`, has no PromptAssembler/ModelGateway call and retains only the direct typed evidence it actually consumed. It is not a replacement public Artifact schema.
- internal immutable `AssembledModelRequest(canonical_request_bytes, request_digest, prompt_record)` is produced only by PromptAssembler. `PromptAssemblyRecord.canonical_model_request_digest` must equal the domain-separated digest of those exact bytes and binds assembler version/order. After persisting the record, the executor calls `ModelGateway.invoke(request: AssembledModelRequest, *, prompt_assembly_ref: PayloadRef)`: the gateway resolves the ref, rehashes the exact bytes and refuses any record/request/assembler/order mismatch **before** sending provider bytes. It cannot accept a detached raw prompt/string.
- `ModelGateway(Protocol)` is that service-owned single-shot invocation selected from the catalog's model tier/prompt/skills/guardrails. Static v1 exposes no model-controlled provider callback or tool-result→second-model loop; tests use a fake gateway behind the same interface.
- internal immutable `ExecutionEvidenceOrdinalToken(node_id, call_ordinal, bridge_priority, bridge_id, issuance_digest)` and `PreparedBridgeSet`: only the node-owned sequencer constructs tokens, starting at one and continuing across pre-input/execution phases; providers must return the exact token and cannot change its provider identity. These are reviewed runtime DTOs, not persisted public schemas.
- internal closed `BridgeStageOutcome(status=prepared|completed|failed|timed_out|cancelled, input_contribution, prepared_handle, frozen_contribution, reason, journal_cursor)` and service-owned `BridgeEvidenceJournal`. Evidence persistence goes through one helper that atomically performs the PayloadStore put and idempotent `BridgeEvidenceRecorded` append before returning the ref to a provider. Provider exceptions are caught at the resolver boundary and always yield an outcome; recovery drains the journal by node/token even when no provider return value exists. A service crash replays the same idempotency keys/events. Providers never own the only copy of partial evidence state.
- generic internal `ExecutionBridgeProvider/ExecutionBridgeSession` ports plus immutable `BridgeInputContribution`, `PreparedBridgeHandle` and `BridgeContribution` close a two-stage protocol. `CatalogRuntime` resolves providers only from reviewed `ExecutionBridgeDescriptor`/config/handler material and verifies provider ID/version/content digests; no worker/model/caller can inject a provider, choose material or mint an ordinal.
  - `prepare_input(..., sequencer, evidence_journal) -> BridgeStageOutcome` runs after terminal dependencies but before final InputSnapshot freeze. It receives admitted Plan/node/WorkerSpec, ContextSnapshot, unresolved dependency bindings and the same node-global sequencer later used during execution—not an InputSnapshot. The resolver obtains issuance tokens before any pre-input evidence work; successful handles freeze those tokens with every created ref/digest, while failure outcomes retain the journal cursor/partial refs. Preparation may read/write only exact registry-validated Phase 2 PayloadStore facts and may add only descriptor-authorized canonical `memory_refs_v1`; CapabilityGateway, external/live stores, model/handler, wall clock and data-result backfill are forbidden.
  - after the runner freezes the exact InputSnapshot and reserves the child budget, `open_execution(handle, *, input_snapshot, sequencer, evidence_journal, ...) -> ExecutionBridgeSession` re-verifies every pre-input ref/token and continues the same sequencer rather than restarting ordinals. Only after RUNNING/timeout/cancellation are active may the session perform catalog-configured static prefetch through CapabilityGateway. `freeze_for_execution(kind) -> BridgeStageOutcome` seals the session and returns direct evidence plus LLM-only untrusted-block DTOs; any late call/mutation is rejected, and every exception/timeout/cancellation still returns/drains a terminal outcome.
- `ExecutionBridgeResolver` derives the required provider set from the support-report-bound descriptors and composes it in canonical `(bridge_priority, bridge_id)` order. One executor-owned `ExecutionEvidenceSequencer` issues globally unique ordinals before bridge work; providers receive issuance tokens and only echo/validate them. The single canonical merge key everywhere is `(call_ordinal, bridge_priority, bridge_id, within_call_role)`. Duplicate/conflicting tokens/roles/refs fail. Reversed completion cannot change the merge. Required provider absence, extra/forged providers or prepared-handle drift fail before handler/model execution.
- providers never receive or call PromptAssembler. The executor merges all providers' untrusted-block DTOs, assigns one-based prompt block ordinals in canonical merge order, builds/persists exactly one PromptAssemblyRecord and invokes ModelGateway at most once. Phase 3/static v1 bridge calls and parameters are derived solely from the descriptor's validated config plus admitted node params/InputSnapshot; model-driven late fetch or tool-result→second-prompt/model loops are unsupported. The frozen merged contribution set is the only bridge input/evidence source for WorkerExecutionResult.
- strict internal `PendingCapabilityInvocation` and `UnpublishedCapabilityResult`, plus one closed `CapabilityGateway` state machine:
  - `begin(*, plan_digest, node_id, worker_id, capability_ref, request_schema_ref, idempotency_key) -> PendingCapabilityInvocation` verifies the admitted Plan, exact WorkerSpec allowlist and CapabilityDescriptor request schema;
  - `invoke(pending, validated_request) -> UnpublishedCapabilityResult` calls only the trusted resolved backend. The raw result has no PayloadRef/public visibility and cannot count as tool evidence;
  - the owning adapter validates PIT/output schema and persists the validated request/result through `PayloadStore` exactly once;
  - `finalize_success(pending, *, request_ref, result_ref, request_digest, result_digest) -> ToolCallRecord` re-resolves/verifies those existing refs and digests, then creates the single success record. It performs **no second payload write**;
  - `reject(pending, *, detail_schema_ref, detail_payload, reason_code, evidence_digests, idempotency_key) -> EventRefusalRecord` delegates the one audit-only persistence to `EventRefusalAuditSink`. A pending invocation has exactly one terminal transition; repeated identical terminal calls are idempotent and success/reject conflicts fail.
- `PromptAssembler(Protocol)`: combines resolved system prompt/ordered skills/guardrails with typed trusted inputs and generic untrusted payload blocks (`PayloadRef`, media type and bounded length), validates/builds the registered Task 5 `PromptAssemblyRecord`, and returns only `AssembledModelRequest(canonical_request_bytes, request_digest, prompt_record)`. It never returns a detached prompt/string. Untrusted blocks are placed only in the model gateway's data/tool-input channel, never interpolated into system/skill/guardrail text. It is generic and does not import Phase 3 `RenderedDataBlock`.
- on the `LLM` branch, the executor persists the single merged `PromptAssemblyRecord` exactly once in `main` **before** invoking ModelGateway with its digest-matching `AssembledModelRequest`, obtains one exact `PayloadRef`, and forms the final evidence tuple as direct non-prompt bridge refs in canonical merge order followed by the prompt ref exactly once. Successfully assembled untrusted block refs remain transitive only. If termination occurs after a block was persisted/journaled but before a valid prompt record exists, NodeRun directly retains those otherwise-orphaned block PayloadRefs; no Artifact/model call exists. On `DETERMINISTIC`, prompt ref is `None`, PromptAssembler/ModelGateway are absent and only direct evidence is used. Successful Artifact Provenance equals NodeRun's tuple. Failure/timeout/cancellation drains all journaled evidence even when a provider never returned.
- `execute_node(plan, node, *, runtime, prepared_bridges: PreparedBridgeSet, input_snapshot, ctx, node_reservation, bridge_resolver: ExecutionBridgeResolver, model_gateway, capability_gateway, registry, stores, clock: AuthoritativeClock) -> (NodeRun, Artifact | None)`.

There is no public `handlers: dict[worker_id, callable]` injection point.

**Execution and honesty rules:**

1. reload/verify the admitted Plan, catalog/support-report bridge refs, active parent/child reservations, prepared handles and exact WorkerSpec using pure preflight checks; preflight failure performs no provider/capability/PayloadStore side effect;
2. build runtime inputs from the final InputSnapshot's named bindings. The resolver recomputes `expected_memory_record_refs = canonical_union(base_authorized_memory_refs, every completed PreparedBridgeSet memory addition)` with the Phase 1 canonicalizer and requires exact tuple equality with InputSnapshot—missing, extra, foreign, duplicate-ambiguous or late/future refs fail before execution. Each provider also verifies its own prepared subset unchanged; one/many artifact shape and order must already match WorkerSpec;
3. append typed RUNNING state, then activate the node timeout/cancellation scope **before** opening any execution-stage bridge session or allowing provider/capability I/O;
4. open the exact prepared sessions, perform only catalog-configured static prefetch and freeze their reviewed contributions. For `LLM`, all prompt-contributing bridge work is sealed before the executor's single PromptAssembler→prompt persistence→ModelGateway sequence; for `DETERMINISTIC`, only direct typed contributions are supplied to the handler and untrusted-block output is forbidden. A terminal interruption drains already frozen/recorded evidence into NodeRun;
5. every capability call goes through CapabilityGateway; RUNNING always precedes `begin/invoke`, timeout/cancellation covers bridge work, a worker cannot self-report a numeric tool count, and pending/rejected invocations do not count as evidence;
6. `tool_calls=REQUIRED` requires at least one allowed, finalized-success ToolCallRecord; FORBIDDEN requires no invocation;
7. required input refs, number anchors and unsourced-number policy are evaluated from typed evidence, including numeric payload paths, not merely a claimed list length;
8. every persisted untrusted block accepted by PromptAssembler is retained transitively in the persisted PromptAssemblyRecord rather than duplicated as a direct execution-evidence ref. For `LLM`, direct non-prompt bridge refs plus the exact prompt-record typed ref form the execution-evidence tuple; for `DETERMINISTIC`, only actually consumed direct typed refs are permitted and no render-only block or prompt record is created. No required ref is dropped after prompt construction or on a no-Artifact terminal path;
9. validate the primary payload through its exact OutputBinding SchemaRef before declaring success;
10. schema/evidence failure with static v1 `max_attempts=1` yields INCOMPLETE and no Artifact; handler/model exception yields FAILED; timeout yields TIMED_OUT; cancellation yields CANCELLED; every required reason code is present;
11. successful degraded input/result yields DEGRADED, otherwise COMPLETED;
12. COMPLETED/DEGRADED creates one primary Artifact exclusively through the Phase 1 builder.

**Artifact/NodeRun binding:**

- NodeRun uses the real run ID, Plan ID/digest, node/worker ID, attempt number, exact InputSnapshot content digest, actual tool-call records/usage and a real output ArtifactRef only on an output-producing terminal status. Phase 1 NodeRun has no reservation field: the child reservation is correlated externally by the BudgetLedger and lifecycle RunEvents using the same run/plan/node/attempt identity;
- Artifact content digest covers payload + SchemaRef;
- reproducibility provenance includes Plan/code/model config, prompt/ordered skills/guardrails/capabilities, InputSnapshot artifact/data/memory/context digests, deterministic ToolCallRecord request/result digests and the complete exact main execution-evidence tuple (including PromptAssemblyRecord on `LLM`, absent on `DETERMINISTIC`);
- data obtained after node start remains in ToolCallRecord/NodeRun/Artifact provenance and never backfills the already frozen InputSnapshot;
- audit provenance includes runtime IDs, provider response ID and wall-clock;
- `rendered_from_payload_digest` is set/verified by the Phase 1 builder against the source payload, never by hashing markdown as a substitute;
- no blank plan/snapshot digest, fabricated Plan ID, path ref or arbitrary short digest is accepted.

- [ ] **Step 1: Write failing worker tests**

Required matrix:

- LLM and deterministic success through trusted resolution;
- arbitrary handler/model/capability injection rejected;
- missing required, extra or forged bridge provider/material rejected before execution; a worker/model cannot inject a provider or ordinal;
- exact input snapshot/SchemaRef enforcement;
- required/optional/forbidden tool behavior based on ToolCallRecords;
- capability outside allowlist and request/result schema mismatch rejected;
- pending→finalize/reject state matrix, including PIT future/missing refusal with no public raw payload or success ToolCallRecord;
- adapter-owned single request/result persistence followed by gateway ref verification, proving `finalize_success` performs no duplicate write;
- generic prompt assembly keeps malicious untrusted payload text out of system/skill/guardrail channels, enforces the registered bounded typed block envelope, persists PromptAssemblyRecord once before an `LLM` model call, retains direct non-prompt bridge evidence plus its exact typed ref in WorkerExecutionResult/NodeRun/Artifact, and replays every direct/ordered block ref with zero live renderer/source call;
- PromptAssembler/request binding is exact: changing assembler ID/version, trusted-input order, block order or canonical request bytes changes the recorded request digest. A gateway spy proves that a record for request A cannot authorize request B and that any ref/schema/content/assembler/order/request-digest mismatch is rejected before provider bytes are sent;
- the execution-kind matrix rejects `LLM` without a prompt ref and `DETERMINISTIC` with one; spies prove deterministic execution never invokes PromptAssembler/ModelGateway or creates a render-only block, but retains its actually consumed direct evidence;
- two reviewed providers complete in reversed order yet merge by `(call_ordinal, bridge_priority, bridge_id, within_call_role)`; provider-minted/duplicate/conflicting issuance tokens or refs fail, and failure after contribution freeze retains the merged branch-complete tuple;
- a provider exception after its first evidence put, timeout/cancellation between puts, and service crash before provider return all recover the already committed refs from `BridgeEvidenceJournal` in canonical token/role order. Injected failure between evidence payload and `BridgeEvidenceRecorded` leaves neither visible; replay after their RuntimeUnitOfWork commit exposes both exactly once;
- preflight failure has zero side effects; event/capability spies prove RUNNING precedes bridge `begin/invoke`, timeout/cancellation covers bridge work, and a late call after freeze is rejected. Failed/timed-out/no-Artifact execution retains every already frozen contribution in NodeRun, while wrong namespace/SchemaRef/content, duplicate write or Artifact/NodeRun ref drift fails;
- termination after an untrusted block is journaled but before PromptAssemblyRecord persistence retains that otherwise-orphaned block ref directly in the no-Artifact NodeRun; a successful assembled branch keeps it only transitively and never duplicates it in direct evidence;
- sourced/unsourced/missing NumberAnchor cases;
- output schema failure → INCOMPLETE/no Artifact;
- exception/timeout/cancellation reason/status;
- degraded input propagation;
- provenance content/reproducibility/audit digests vary in the Phase 1-defined layers;
- NodeRun carries real plan/snapshot identity; lifecycle events and BudgetLedger records correlate every attempt handed to Task 7—including a pure-preflight failure—to its real child reservation. Runner-only BLOCKED/SKIPPED, bridge-preparation-failed, child-allocation-denied and not-started-cancelled attempts have none.

Run now: `pytest tests/orchestration/test_worker.py -v`.

Expected: FAIL on missing gateway/executor/evidence behavior before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement gateways and executor**

Do not add retry/schema repair in static v1. Tests may inject fake model/tool backends only when those fakes are registered behind the same trusted resolver/gateway boundaries.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_worker.py tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/worker.py tests/orchestration/test_worker.py
git commit -m "feat(orchestration): add capability-confined typed worker executor"
```

---

## Task 8: DAG runner (layers, gating, bounded parallel, barrier, budget)

**Files:**
- Create: `guanlan_v2/orchestration/dag.py`
- Test: `tests/orchestration/test_dag.py`

**Consumes:** a Plan digest resolved through `PlanAdmissionService.verify_for_dispatch`, the bound RuntimeSupportReport/Profile, CatalogRuntime, ArtifactPool, BudgetLedger, worker gateways/executor and RunContext.

**Produces:**

- the Task 5-owned immutable `RunResult@1`: run/plan digest, canonically keyed NodeRuns, committed LayerCommit refs and terminal `completed | partial | failed | cancelled` status.
- `run_plan(plan_digest, ctx, *, admission, pool, budget, catalog_runtime, bridge_resolver: ExecutionBridgeResolver, model_gateway, capability_gateway, stores, runtime_limit, clock: AuthoritativeClock) -> RunResult`.

The caller does not pass an arbitrary Plan object, catalog, registry, handler map or raw provider map. The service-owned bridge resolver is bound to the same exact admitted catalog digest and rejects any provider whose reviewed material identity is absent or drifted.

**Normative algorithm:**

1. verify Plan/PlanAdmitted, Phase 1 and RuntimeSupport report digests, catalog/registry/context and active plan reservation;
2. derive stable Kahn layers from the already validated DAG; sort nodes by PlanNode ID;
3. before each layer, recheck cancellation and active reservation;
4. wait for every dependency to reach a terminal state;
5. apply the closed policy to determine, but not yet persist, the node outcome:
   - unsatisfied BLOCK → pending terminal outcome `BLOCKED`;
   - unsatisfied SKIP → pending terminal outcome `SKIPPED`;
   - unsatisfied DEGRADE → omit only that dependency's value, retain the other inputs, mark `degraded_inputs=True` and remain executable;
6. resolve the deterministic base dependency bindings in Plan declaration order for every node. Then follow exactly one branch:
   - `BLOCKED | SKIPPED`: do not resolve/open a bridge or issue an evidence token. First freeze the exact base InputSnapshot containing every satisfied binding available at the terminal decision boundary and no prepared memory additions; only then persist the terminal NodeRun with an empty bridge-evidence tuple, no child reservation/RUNNING/executor/output;
   - executable: purely resolve the exact support-report-bound provider set, create the node-global evidence sequencer, then call each provider's `prepare_input(..., sequencer)` in canonical bridge order. Merge only descriptor-authorized memory refs and freeze returned handles plus sequencer state; data/capability/live-I/O contributions are forbidden at this stage;
7. for the executable branch, compute `expected_memory_record_refs` as the Phase 1-canonical union of base authorized memory refs and every completed provider addition, freeze the exact per-node InputSnapshot, then re-resolve it and require exact tuple equality—no caller/provider may insert an extra, foreign, duplicate-ambiguous or late/future ref. If preparation fails, compute the same equality from the deterministic base plus additions from providers whose preparation already completed successfully; exclude every half-built contribution from the failing provider, while retaining its journal-recovered partial evidence only in NodeRun. Create one `INCOMPLETE` NodeRun with the reviewed bridge-preparation reason and allocate no child reservation/RUNNING/provider execution/Artifact;
8. otherwise attempt to reserve a candidate-bound child node budget (zero LLM invocation for deterministic, one for v1 LLM). Allocation denial or cancellation before allocation persists a reviewed non-success NodeRun against the already frozen snapshot, retains preparation evidence and has no child reservation/RUNNING/Task 7 call. On success, call Task 7 with the prepared handles under a semaphore capped by every bound concurrency limit;
9. settle actuals or release unused reservation on non-execution/cancellation;
10. persist terminal NodeRun, stage COMPLETED/DEGRADED Artifact only;
11. after every node in the layer is terminal, atomically commit the layer's successful outputs in canonical order;
12. only LayerCommitted artifacts become inputs to the next layer;
13. append/persist the final RunResult; replay derives progress from PlanAdmitted, bridge preparation evidence, node, budget, ArtifactStaged and LayerCommitted records.

**Failure/recovery rules:**

- BudgetExceeded becomes an explicit non-success NodeRun/reason against the already frozen InputSnapshot and cannot leave a hidden reservation;
- timeout and cancellation propagate through the Task 7 statuses/events;
- cancellation during a layer does not publish its staged artifacts. A not-started node first freezes its deterministic base InputSnapshot, skips bridge/reservation/RUNNING and then records CANCELLED; a prepared-but-unreserved node uses its already frozen snapshot/evidence; a reserved/running node releases/settles its externally correlated reservation;
- a crash before LayerCommitted restarts that uncommitted layer with the same semantic idempotency keys; a crash after commit resumes at the next layer;
- actual NodeRun IDs, never fabricated `nr:{node_id}` strings, enter LayerCommit;
- sink FAILED/BLOCKED/TIMED_OUT/INCOMPLETE is failed, cancellation is cancelled, usable degraded/skipped outcomes are partial, and only fully successful required sinks are completed;
- unsupported condition/reducer/debate/gate/stop/retry/multi-writer constructs cannot reach this function because Task 5 rejects them; the runner still defense-in-depth rejects a mismatched profile/report.

- [ ] **Step 1: Write failing runner tests**

Cover:

- two-node one binding and fan-in many ordering;
- BLOCK, DEGRADE and SKIP independently;
- LLM versus deterministic reservation counts;
- bounded concurrency under different completion orders;
- stable artifact sequence and sink results;
- timeout/cancellation/budget exhaustion;
- no next-layer read before LayerCommitted;
- crash-before/crash-after replay and idempotent resume;
- actual NodeRun IDs in LayerCommit;
- dispatch rejection for changed Plan/catalog/registry/context/profile/reservation/report;
- node memory selection is prepared and the InputSnapshot memory tuple equals the Phase 1-canonical union of base-authorized refs plus every completed PreparedBridgeSet addition before reservation/RUNNING; missing/extra/foreign/duplicate-ambiguous/late refs fail, while a data call or late bridge mutation cannot backfill the current snapshot;
- bridge-input preparation failure yields deterministic INCOMPLETE/no-reservation/no-execution with retained preparation evidence; provider I/O never occurs before RUNNING;
- BLOCKED/SKIPPED freeze a real base InputSnapshot before their NodeRun, issue no bridge token, create no bridge/prompt evidence and have no child reservation/RUNNING/output; DEGRADE remains executable and follows normal preparation/reservation;
- allocation-denied and cancellation-before-allocation NodeRuns use a real already frozen snapshot, retain any completed preparation evidence and have no child reservation/RUNNING/Task 7 call; every attempt handed to Task 7, including preflight failure, is externally correlated to one real child reservation;
- defense-in-depth rejection of every unsupported static-v1 feature.

Run now: `pytest tests/orchestration/test_dag.py -v`.

Expected: FAIL on missing runner/barrier/cancellation behavior before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 2: Implement runner without another validator**

Runtime Kahn scheduling is allowed; do not recalculate a competing Plan validity or digest. Every input/output follows the already validated Phase 1 bindings.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_dag.py tests/orchestration/test_worker.py tests/orchestration/test_pool.py tests/orchestration/test_admission.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/dag.py tests/orchestration/test_dag.py
git commit -m "feat(orchestration): add admitted static DAG runner"
```

---

## Task 9: Reviewed three-worker pilot + attested legacy preset adapter

**Files:**
- Create: `guanlan_v2/orchestration/presets.py`
- Modify: `guanlan_v2/orchestration/catalog_runtime.py`
- Create: `config/orchestration/catalogs/stock-deep-dive-compat-v1.yaml`
- Create: only the complete compatibility materials required by the reviewed graph under `config/orchestration/materials/stock-deep-dive-compat-v1/`
- Test: `tests/orchestration/test_pilot_runtime.py`
- Test: `tests/orchestration/test_presets.py`

This task has two ordered checkpoints. The compatibility adapter cannot begin until the three-final-worker path passes.

### 9.1 Three-final-worker pilot

Run the Task 3 triad (recommended `text.sentiment -> dec.research_mgr -> dec.pm`) end-to-end with the actual Phase 1 `SentimentReport -> ResearchPlan -> PortfolioDecision` schemas and a trusted fake model gateway.

The selected pilot must also pass the Task 5 tool/bridge support matrix. If repository evidence shows an LLM worker requires a model-driven tool loop, choose another reviewed final-worker triad or add an exact catalog-bound static-prefetch descriptor/config and new catalog digest; do not smuggle a second model invocation into static v1.

The test must exercise the real path:

1. build/verify the material-aware final-worker catalog snapshot;
2. create a strict PRESET OrchestrationRequest, ContextSnapshot and PlanDraft with exact registry/catalog/budget bindings;
   the pre-Phase-3 runtime builds and persists Phase 1's canonical `EmptyMemorySnapshot/EmptyMemorySelection`, then puts their exact locator/hash/selection ref into ContextSnapshot with `memory_session_id=None`—never blank/random placeholders or a caller-selected session scope;
3. Phase 1 validation;
4. RuntimeSupportReport;
5. plan reservation;
6. REQUIRED authenticated approval;
7. Phase 1 freeze and PlanAdmitted;
8. bounded execution with typed InputSnapshots, NodeRuns, Artifacts, LayerCommits and provenance;
9. replay and final PortfolioDecision validation.

All three workers are `final/dynamic_allowed`; none uses `compat.*`. The pilot must fail if any prompt/SKILL/capability byte drifts, if a skill/tool override is attempted, or if approval/reservation/report identity changes.

### 9.2 Legacy mapping adapter and compatibility catalog

**Consumes:** the Phase 1 real `LegacyGraphMapping` (including `worker_mappings`, `dependency_mappings` and `input_mappings: tuple[LegacyInputMapping,...]`), `compatibility_binding_for`, `attest_static_legacy_plan`, Task 0 normalized fixture/mapping tables, catalog builder and admission service.

**Produces:**

- `legacy_draft_from_mapping(mapping, *, request, context, catalog, schema_registry, budget_request, sink_mapping) -> PlanDraft`;
- a service-only attestation builder/store path using the Phase 1 `StaticLegacyPlanAttestation`;
- the complete `compat.*` WorkerSpec/material subset needed by the frozen graph.
- `PHASE2_STATIC_CATALOG_DIGEST` plus `phase2_static_catalog_snapshot()`: the reviewed cumulative end-of-Phase-2 catalog containing the unchanged three final pilot workers and the complete Task 9 compatibility subset. This is the sole canonical base for a new Phase 3 catalog extension; earlier pilot/catalog digests remain resolvable for old Plans but are not alternative Phase 3 build bases.

There is no public `plandraft_from_dagnodes(list[DAGNode])` heuristic.

**Legacy adapter invariants:**

1. mapping must be fully MAPPED and its source schema/config/mapping digests must match the frozen Task 0 fixture;
2. node → worker comes from `LegacyWorkerMapping`; edge policy/accepted statuses/missing-output behavior comes from `LegacyDependencyMapping`; source kind/key, target kind plus its exact input-binding/param/context/service target, upstream SchemaRef, projection/projection field, missing behavior and evidence come from `LegacyInputMapping`;
3. legacy agent names never become final worker IDs by coincidence. Non-honest one-to-one mappings use `compat.*`, `catalog_role=compatibility`, `selection_scope=static_legacy_only` and a matching CompatibilityBinding;
4. every compatibility WorkerSpec is otherwise complete: params/input/output SchemaRefs, execution ref, prompt/SKILL/guardrails/capabilities and evidence policy resolve and hash;
5. every legacy `input_keys` entry resolves through exactly one mapped `LegacyInputMapping`, and its semantics are preserved:
   - base `code/asof_date` require reviewed `target_kind="param"|"context"` and validation against the strict target schema;
   - `out_dir` requires a reviewed `target_kind="service_binding"`; it is supplied by the service and is never an arbitrary Plan/caller path;
   - every upstream value uses `target_kind="input_binding"`. A value named in `input_keys` but not a direct legacy dep must be a proven transitive ancestor, so the existing graph already orders it; the adapter may not invent a dependency or weaken an intervening BLOCK;
   - direct edge/input missing semantics obey the Phase 1 BLOCK/DEGRADE/SKIP consistency matrix and nullable/optional target rules;
   - the Plan/InputSnapshot binds the full upstream output SchemaRef exactly; legacy single-field unwrap versus multi-field `model_dump` occurs only afterwards inside an attested catalog-owned compatibility handler and is recorded in provenance;
6. `memory_mode` and `borrows_memory` map through reviewed WorkerSpec/read-category/provenance entries;
7. unknown node/input/output/edge semantics, `missing_behavior="unknown"`, incomplete evidence or an unresolved required target InputBinding return UNMAPPABLE and cannot yield a draft, binding or attestation;
8. the adapter writes the all-set legacy source/config/mapping tuple into PlanDraft and calls the single Phase 1 candidate-digest function; `legacy_mapping_digest` is the LegacyGraphMapping semantic digest and therefore includes every `input_mappings` target-kind/target/projection/behavior/evidence field;
9. the service calls `attest_static_legacy_plan(mapping, draft, request, context=context, catalog=catalog, schema_registry=schema_registry)` from Phase 1. The stored attestation therefore binds the same request/candidate/context/catalog/registry/config/mapping inputs and grants neither AUTO approval nor dynamic selection;
10. PRESET/PRESET_FALLBACK still follows support→reserve→REQUIRED approval→freeze;
11. compatibility entries do not count toward the final 24 or the three-worker pilot.
12. the exported static catalog digest is rebuilt/verified by the Phase 1 material-aware builder, is pinned by a reviewed manifest, and cannot vary with physical material location or load order.

The current authoritative fixture is derived from `config/swarm/stock-deep-dive.yaml` (currently 18 agents) and must prove its normalized content is identical to `engine/financial_analyst/_resources/config/swarm/stock-deep-dive.yaml` before attestation.

- [ ] **Step 1: Write the failing three-worker pilot**

Run: `pytest tests/orchestration/test_pilot_runtime.py -v`

Expected: FAIL on the not-yet-integrated pilot path; unrelated collection/environment failures do not count.

- [ ] **Step 2: Complete and pass the three-worker pilot**

Run: `pytest tests/orchestration/test_pilot_runtime.py -v`

Expected: PASS before compatibility work starts.

- [ ] **Step 3: Write failing mapping/attestation/catalog tests**

Cover fully mapped versus partial graph, unknown node/input/edge, final versus compat ID/role/scope, the complete `LegacyInputMapping` source-kind/target-kind/exact-target/upstream-schema/projection/projection-field/missing-behavior/evidence matrix, dependency/input behavior consistency, nullable/optional checks, non-direct transitive ancestry, memory metadata, YAML-copy drift, context/registry-sensitive attestation and prohibition of direct DAGNode-name conversion.

Run now: `pytest tests/orchestration/test_presets.py tests/orchestration/test_catalog_runtime.py -v`.

Expected: FAIL on the missing compatibility mapping/catalog behavior before implementation; unrelated collection/environment failures do not count.

- [ ] **Step 4: Add only the reviewed compatibility materials and adapter**

Do not redesign the compatibility workers into the final 24. Their purpose is a faithful, attested bridge for Task 10.

- [ ] **Step 5: Run pilot + compatibility regressions and commit**

Run: `pytest tests/orchestration/test_pilot_runtime.py tests/orchestration/test_presets.py tests/orchestration/test_migration.py tests/orchestration/test_catalog_runtime.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/presets.py guanlan_v2/orchestration/catalog_runtime.py config/orchestration/catalogs/stock-deep-dive-compat-v1.yaml config/orchestration/materials/stock-deep-dive-compat-v1 tests/orchestration/test_pilot_runtime.py tests/orchestration/test_presets.py
git commit -m "feat(orchestration): add pilot and attested legacy preset bridge"
```

---

## Task 10: Full attested stock-deep-dive execution equivalence

**Files:**
- Create: `tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json`
- Test: `tests/orchestration/test_engine_equivalence.py`

**Consumes:** both authoritative YAML copies, the frozen Task 0 LegacyGraphMapping/attestation fixture, the complete Task 9 compatibility catalog, legacy `Orchestrator/DAGNode/SubAgentResult`, and the admitted Phase 2 kernel.

This is not a two-node semantic smoke test. It is the Phase 2 acceptance test for the complete current `stock-deep-dive` graph.

**Hermetic harness:**

- load and normalize the real repository YAML and assert the bundled copy normalizes identically and matches the attested source-config digest;
- preserve the full current node/deps/soft_deps/input_keys/memory metadata (currently 18 agents);
- instantiate deterministic legacy SubAgent test doubles with the real node names and reviewed output schemas;
- resolve the corresponding final/compat WorkerSpecs and deterministic/fake model handlers only through CatalogRuntime;
- feed both runtimes the same typed base inputs and per-node fixture outputs/failures;
- do not call live data, LLM, memory or filesystem output. A service-owned temporary output locator may stand in for legacy `out_dir`;
- configure the test environment to import `engine/financial_analyst` automatically; do not leave a manual `PYTHONPATH` workaround as an acceptance condition.

**Equivalence assertions:**

For each scenario compare the legacy result with the reviewed canonical mapping of the new result:

1. complete node set, dependency partial order and executed/not-executed set;
2. each node's canonical terminal outcome. A missing failed soft input maps to DEGRADED only where the reviewed table says so; hard failure maps downstream blocking; SKIP follows its explicit mapping;
3. every `LegacyInputMapping` source kind/key, target kind and exact input-binding/param/context/service target, upstream output SchemaRef, projection/projection field, missing behavior and injected payload shape, including proven non-direct transitive input and single-field unwrap/multi-field dump projection;
4. every `LegacyDependencyMapping` hard/soft accepted-status, target-policy and missing-output behavior;
5. artifact slot, output key and output SchemaRef per mapped node;
6. canonical normalized typed payload and content digest for deterministic outputs;
7. sink outcomes and public LayerCommit boundaries;
8. no uncommitted staged artifact is visible;
9. Plan/Artifact/NodeRun provenance binds the same attested config/mapping/catalog and input/tool/data evidence.

Required scenarios:

- all-success full graph;
- one injected failure at each legacy node, parameterized across the full graph, so every hard/soft downstream consequence is exercised;
- optional/soft output absent while downstream still runs;
- required/hard output absent and downstream blocks;
- base input missing/invalid according to the reviewed policy;
- completion-order permutations proving stable new artifact/input ordering;
- crash before and after a representative LayerCommitted boundary followed by replay.

Live LLM prose is deliberately not byte-compared. This exception does not permit replacing output schemas or canonical deterministic payload fields with a generic `Doc`.

**Negative-control assertions:**

- no/mismatched request, candidate, catalog, registry, source-config or mapping attestation;
- one YAML copy drifted;
- unknown/partial mapping;
- `compat.*` selected under DYNAMIC/BOOTSTRAP;
- caller-supplied handler/path/approval/reservation;
- changed prompt/SKILL/capability material after snapshot;
- runtime profile/report changed after reservation.

Every negative control must fail before execution, and pre-admission failures must consume no budget.

- [ ] **Step 1: Write the full equivalence harness/test**

Create `tests/orchestration/test_engine_equivalence.py` with the hermetic harness, every assertion/scenario/negative control above and an explicit requirement for the reviewed fixture.

Run: `pytest tests/orchestration/test_engine_equivalence.py -v`

Expected: FAIL because the reviewed equivalence fixture is absent; unrelated import/environment failure or a skipped/empty matrix does not count.

- [ ] **Step 2: Build the reviewed deterministic equivalence fixture**

The fixture contains deterministic node outputs/failure injections and expected canonical outcome mappings. It references, rather than duplicates or silently edits, the Phase 1 graph/config/mapping digests.

- [ ] **Step 3: Run the complete old/new equivalence matrix**

Run: `pytest tests/orchestration/test_engine_equivalence.py -v`

Expected: PASS for the actual full graph and all parameterized failure cases.

- [ ] **Step 4: Run the whole Phase 1 + Phase 2 suite**

Run:

`pytest tests/orchestration/ -v`

Also run:

`python -m compileall -q guanlan_v2/orchestration`

If Ruff is available:

`ruff check guanlan_v2/orchestration tests/orchestration`

- [ ] **Step 5: Commit**

```bash
git add tests/orchestration/test_engine_equivalence.py tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json
git commit -m "test(orchestration): prove full attested legacy execution equivalence"
```

---

## Phase 2 Exit Gates

The previous “Self-Review completed” assertion is not evidence. Phase 2 is complete only when every gate below is checked by tests and reviewed artifacts.

### Phase 1 handoff

- [ ] every Phase 1 Exit Gate remains green;
- [ ] Phase 2 imports, rather than redefines, WorkerSpec/catalog/registry/Plan validation/freeze/digest contracts;
- [ ] no Phase 1 source, test or golden file was overwritten;
- [ ] Phase 2 runtime registry/golden is explicit, sealed and separate from the unchanged Phase 1 golden.
- [ ] new Phase 2 Plans bind the cumulative Phase 2 registry digest, while registry resolution/replay preserves exact older digests and permits later reviewed cumulative snapshots without reinterpretation.
- [ ] every pre-Phase-3 ContextSnapshot uses the persisted canonical empty-memory snapshot/query-selection binding with `memory_session_id=None`; no caller/worker session field is invented.

### Store, visibility and replay

- [ ] every RunEvent uses SchemaRef + existing digest-matching PayloadRef;
- [ ] ArtifactStaged remains permanently journal-only;
- [ ] LayerCommitted is the only artifact visibility boundary and commit is atomic;
- [ ] same-key/same-content retry is idempotent; same-key/different-content fails;
- [ ] invalid or main/public-to-sealed/review/audit append is absent from the run journal and can only reach the audit refusal sink;
- [ ] replay reproduces cursors, ledger, admitted Plan, node states and artifact visibility;
- [ ] every declared RuntimeUnitOfWork batch is all-or-none under injected failure and crash/replay; it cannot expose a payload-only/event-only bridge record, orphan reservation, orphan Plan or half-published admission pair;
- [ ] crash-before/crash-after barrier tests pass.

### Catalog and execution authority

- [ ] runtime catalog is an immutable view over one verified WorkerCatalogSnapshot;
- [ ] material drift/missing/extra bytes fail;
- [ ] the three pilot final workers have complete reviewed prompt/SKILL/guardrail/capability material;
- [ ] Task 9 exports one canonical `PHASE2_STATIC_CATALOG_DIGEST` containing the unchanged pilot plus reviewed compatibility subset; earlier catalog digests remain replayable but cannot serve as alternate Phase 3 bases;
- [ ] arbitrary handler/model/tool/MCP/path injection is impossible;
- [ ] CapabilityGateway enforces the exact allowlist and produces typed ToolCallRecords/evidence;
- [ ] gateway begin/invoke/finalize/reject signatures and exactly-once terminal transitions are frozen; adapters own validated success persistence, while EventRefusalAuditSink alone owns rejection persistence;
- [ ] the service-owned ExecutionBridgeResolver admits only support-report-bound `ExecutionBridgeDescriptor`/config/handler providers and supplies the only bridge path to `run_plan/execute_node`; its two stages prepare authorized memory refs before InputSnapshot, continue one ordinal sequencer after RUNNING, merge providers by the one canonical key and reject missing/extra/forged/late/dynamic behavior. CatalogRuntime/RuntimeSupport also reject duplicate or competing `bridge_id` identities before reservation;
- [ ] bridge evidence put + `BridgeEvidenceRecorded` is atomic; provider exception/timeout/cancellation or service crash without a provider return drains the replayed journal into the terminal NodeRun in canonical token/role order, with no payload-only/event-only orphan;
- [ ] before execution, InputSnapshot memory refs equal the Phase 1-canonical union of base-authorized refs and every completed PreparedBridgeSet addition; the resolver rejects missing/extra/foreign/duplicate-ambiguous/late refs rather than accepting provider-local subset checks;
- [ ] PromptAssembler keeps typed untrusted blocks out of system/skill/guardrail channels and returns no detached prompt. On `LLM`, the registered PromptAssemblyRecord persists once in `main`; before provider send, ModelGateway resolves that exact ref and rehashes the canonical request bytes against its assembler/version/order/request digest. Direct non-prompt bridge evidence + prompt-record PayloadRefs survive in NodeRun plus successful Artifact Provenance, while a pre-assembly orphan block survives directly only on the no-Artifact failure path. On `DETERMINISTIC`, the prompt ref is `None`, PromptAssembler/ModelGateway are never called and actually consumed direct evidence still survives for replay without imports or live I/O;
- [ ] pending/rejected capability results never enter main/public PayloadStore and never satisfy REQUIRED tool evidence; only validated `finalize_success` does;
- [ ] compatibility workers remain static_legacy_only and are excluded from final-24/pilot counts.

### Admission and budget

- [ ] Phase 1 validation and RuntimeSupportReport both exist before the first plan reservation;
- [ ] RuntimeSupportReport binds candidate/report/profile/catalog/registry digests plus every activated bridge descriptor/config/handler ref, rejects unsupported bridge semantics and has no budget/provider side effect;
- [ ] AdmissionCandidate persistence + exact plan reservation is one RuntimeUnitOfWork, and frozen Plan persistence + `PlanAdmitted` append is a second; crash-before/after, injected mid-batch failure and retry prove both pairs all-or-none with no orphan authority;
- [ ] plan reservation binds request/candidate and exact token/invocation/concurrency request;
- [ ] node reservations are children of the active plan reservation and replay correctly;
- [ ] REQUIRED authenticated same-digest approval is mandatory for every PlanSource; AUTO always fails;
- [ ] reservation/approval/attestation authority is loaded from service-owned stores;
- [ ] Phase 1 freeze is rerun from exact immutable inputs and no second plan digest exists;
- [ ] PlanAdmitted binds Phase 1 report, RuntimeSupportReport, reservation, approval, optional attestation and frozen Plan PayloadRef;
- [ ] dispatch re-verifies Plan, PlanAdmitted, context/catalog/registry/profile/report and active reservation.

### Static runtime behavior

- [ ] PRESET/PRESET_FALLBACK and already validated DYNAMIC **main** DAGs share the same support matrix and REQUIRED approval; Phase 2 invokes no Planner, while BOOTSTRAP is rejected before reservation until the Phase 5 profile;
- [ ] accepted matrix covers LLM/deterministic, BLOCK/DEGRADE/SKIP, one/many, timeout/cancel and barrier;
- [ ] conditions/reducers/debates/gates/stop conditions/retries/multi-writer are rejected before reservation;
- [ ] BLOCK/DEGRADE/SKIP terminal behavior is distinct and tested;
- [ ] bounded concurrency and dependency-many ordering are deterministic;
- [ ] every NodeRun has real Plan and frozen InputSnapshot identity plus the required terminal reason. Every attempt handed to Task 7—including preflight failure—correlates to a real child reservation through BudgetLedger/lifecycle events. BLOCKED, SKIPPED, bridge-preparation-failed, allocation-denied and not-started-cancelled attempts have no child reservation/RUNNING/output; only COMPLETED/DEGRADED NodeRuns carry an output ArtifactRef;
- [ ] every Artifact passes Phase 1 payload/provenance/digest validation;
- [ ] cancellation/budget/timeout cannot publish a partial layer or leak a reservation.

### Pilot and legacy compatibility

- [ ] the reviewed three-final-worker pilot passes the full validate→support→reserve→approve→freeze→dispatch→replay path;
- [ ] the authoritative and bundled stock-deep-dive YAML normalize identically and match the frozen config digest;
- [ ] only a fully MAPPED graph produces compatibility bindings/draft/attestation;
- [ ] legacy input_keys/base/unwrap, hard/soft/missing-output, output slot/SchemaRef and memory metadata are evidence-mapped;
- [ ] PRESET/PRESET_FALLBACK compat execution requires one exact service-owned attestation and still requires approval;
- [ ] full current stock-deep-dive all-success and parameterized per-node failure equivalence pass;
- [ ] canonical terminal outcomes, inputs, artifact slots, output SchemaRefs and deterministic normalized payloads match;
- [ ] no “manual e2e later” substitute remains.

### Scope protection

- [ ] no dynamic Planner, Bootstrap Lane 0, trial/holdout, optimizer, reducer engine, retry/repair or real trading authority was added;
- [ ] existing legacy engine and workflow/executor.run_graph are unchanged;
- [ ] unrelated worktree changes are not staged.

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after the Phase 1 Handoff Gate — exact imported ABI/goldens;
2. after Tasks 1–2 — append-only replay and candidate-bound ledger;
3. after Tasks 3 and 5 — material authority and closed runtime feature profile;
4. after Task 6 — support-before-budget plus approval/freeze admission order;
5. after Tasks 7–8 — evidence/provenance and crash-safe static execution;
6. after Task 9.1 — three-final-worker pilot;
7. after Tasks 9.2–10 — attested full legacy-graph equivalence and all Exit Gates.

Do not begin Phase 3 until the Phase 2 Exit Gates are checked with test evidence. No execution method requires a particular optional skill package.
