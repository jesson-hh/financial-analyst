# Orchestration Phase 2 · 静态 runtime 兼容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, event-sourced runtime kernel that executes a **frozen static `Plan`** (Phase 1 contracts) — budget ledger, dual-cursor event store, staged→barrier `ArtifactPool`, strict Plan freeze validator, worker executor, and a bounded/stable-ordered DAG runner — then prove it reproduces the engine `Orchestrator`'s soft/hard dependency semantics. **No dynamic Planner in this phase.**

**Architecture:** The new kernel is engine-neutral and reuses the *behavior baseline* of `financial_analyst.agent.orchestrator.Orchestrator/DAGNode` (wave execution, `soft_deps` = done-not-ok) and `agent.base.SubAgent/SubAgentResult` (typed Pydantic output), but adds the four things those lack: **strict validation, bounded concurrency + stable ordering, persistent event/state + recovery, and per-scope budget reservation**. `run_graph` is untouched. Everything is in-memory (an `EventStore` list) with a replay/recovery test standing in for durable storage; wiring to a real store is a later phase.

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio` (`Semaphore`, `gather`, `run`), `pytest` + `pytest-asyncio` (or `asyncio.run` in sync tests). All modules `from __future__ import annotations`. Depends on Phase 1 contracts in `guanlan_v2/orchestration/`.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-07-15-orchestration-framework-design.md`). Every task implicitly includes these.

- **Reuse, don't fork semantics.** Match engine `Orchestrator._ready`: every dep (soft or hard) must be *done*; a hard dep must additionally be *ok*; a soft (DEGRADE) dep that failed does not block, it just contributes no input. Do not invent a third, divergent semantics.
- **Stable ordering.** Completion/thread order must never change `artifact_seq`, reducer input order, or sink input order. Order layers by Kahn depth, nodes within a layer by `PlanNode.id`, committed artifacts by `(node_id, output_key)`.
- **Bounded concurrency.** Never launch unbounded `asyncio.gather` per wave; cap with `min(plan.max_concurrency, budget.max_concurrency)` via a semaphore.
- **Staged→barrier.** Worker output is `stage`d (journal-only, invisible) and becomes readable only when its whole layer atomically `commit_layer`s. A crash before the barrier must leave no visible downstream input.
- **Persist-then-publish + idempotency.** Every state change appends a `RunEvent` (journal_seq assigned to *all* events; visible_seq only to public ones) keyed by an idempotency key; re-append returns the existing event.
- **Budget is one ledger for the whole run.** Bootstrap/planner/plan/node/repair/retry all draw from one `RunBudget`; reserve before work, settle actuals after, release the unused. Over-budget reservation raises.
- **Freeze validator is a hard gate.** `BLOCK.accept_statuses` must be exactly `{COMPLETED}`; `INCOMPLETE/FAILED/BLOCKED/CANCELLED` may never be declared success; same-slot multi-write requires a registered reducer; a decision-class sink requires `can_emit_decision=True`; `phase=main` requires a `context_snapshot_id`, `phase=bootstrap` forbids one.
- `plan_digest` is computed over the canonical `PlanDraft` executable fields + `catalog_digest`, excluding approval/wall-clock; any field change yields a new digest.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/budget.py` | `BudgetLedger` (reserve/settle/release + events) |
| `guanlan_v2/orchestration/eventstore.py` | `EventStore` (append, dual seq, idempotency, journal/visible, replay) |
| `guanlan_v2/orchestration/catalog.py` | `WorkerCatalog` (register/get/version/digest) |
| `guanlan_v2/orchestration/pool.py` | `ArtifactPool` staged→barrier + `freeze_input_snapshot` |
| `guanlan_v2/orchestration/validate.py` | `validate_plan_draft` + `freeze_plan` (hard gate + plan_digest) |
| `guanlan_v2/orchestration/worker.py` | `WorkerHandler` protocol + `run_node` (typed Artifact + NodeRun + honesty classify) |
| `guanlan_v2/orchestration/dag.py` | `run_plan` (layers, bounded parallel, gating, barrier, budget) |
| `guanlan_v2/orchestration/presets.py` | `plandraft_from_dagnodes` (engine DAGNode → PlanDraft) |
| `tests/orchestration/` | one test module per source module |

---

## Task 1: Budget ledger

**Files:**
- Create: `guanlan_v2/orchestration/budget.py`
- Test: `tests/orchestration/test_budget.py`

**Interfaces:**
- Consumes: `context.RunBudget`, `context.BudgetReservation` (Phase 1).
- Produces:
  - `class BudgetExceeded(Exception)`.
  - `class BudgetLedger` — `__init__(self, budget: RunBudget, *, run_id: str, now: Callable[[], datetime])`; `reserve(*, scope_type, scope_id, tokens, invocations, parent_reservation_id=None) -> BudgetReservation` (raises `BudgetExceeded` if it would exceed `max_tokens`/`max_llm_invocations`); `settle(reservation_id, *, actual_tokens, actual_invocations) -> BudgetReservation`; `release(reservation_id, *, reason) -> BudgetReservation`; `available() -> tuple[int, int]` returning `(tokens_left, invocations_left)` against outstanding reservations+settled actuals.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_budget.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.budget import BudgetLedger, BudgetExceeded

UTC = timezone.utc
def _now(): return datetime(2026, 7, 15, tzinfo=UTC)


def _ledger(**kw):
    b = RunBudget(ledger_id="L", max_tokens=1000, max_llm_invocations=24, max_concurrency=4, **kw)
    return BudgetLedger(b, run_id="r", now=_now)


def test_reserve_reduces_available():
    led = _ledger()
    led.reserve(scope_type="node", scope_id="n1", tokens=300, invocations=2)
    assert led.available() == (700, 22)


def test_over_budget_reservation_raises():
    led = _ledger()
    led.reserve(scope_type="node", scope_id="n1", tokens=900, invocations=1)
    with pytest.raises(BudgetExceeded):
        led.reserve(scope_type="node", scope_id="n2", tokens=200, invocations=1)
    with pytest.raises(BudgetExceeded):
        led.reserve(scope_type="node", scope_id="n3", tokens=10, invocations=24)


def test_settle_uses_actuals_and_release_frees():
    led = _ledger()
    r = led.reserve(scope_type="node", scope_id="n1", tokens=300, invocations=2)
    led.settle(r.reservation_id, actual_tokens=120, actual_invocations=1)
    assert led.available() == (880, 23)
    r2 = led.reserve(scope_type="node", scope_id="n2", tokens=300, invocations=2)
    led.release(r2.reservation_id, reason="blocked")
    assert led.available() == (880, 23)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_budget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guanlan_v2.orchestration.budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/budget.py
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Callable, Literal
from guanlan_v2.orchestration.context import BudgetReservation, RunBudget


class BudgetExceeded(Exception):
    pass


class BudgetLedger:
    def __init__(self, budget: RunBudget, *, run_id: str, now: Callable[[], datetime]):
        self._budget = budget
        self._run_id = run_id
        self._now = now
        self._reservations: dict[str, BudgetReservation] = {}

    def _committed(self) -> tuple[int, int]:
        tokens = inv = 0
        for r in self._reservations.values():
            if r.status == "released":
                continue
            if r.status == "settled":
                tokens += r.actual_tokens
                inv += r.actual_llm_invocations
            else:  # reserved
                tokens += r.reserved_tokens
                inv += r.reserved_llm_invocations
        return tokens, inv

    def available(self) -> tuple[int, int]:
        t, i = self._committed()
        return self._budget.max_tokens - t, self._budget.max_llm_invocations - i

    def reserve(self, *, scope_type: Literal["bootstrap", "planner", "plan", "node", "schema_repair", "retry"],
                scope_id: str, tokens: int, invocations: int,
                parent_reservation_id: str | None = None) -> BudgetReservation:
        t_left, i_left = self.available()
        if tokens > t_left or invocations > i_left:
            raise BudgetExceeded(
                f"reserve({scope_id}) tokens={tokens}/{t_left} inv={invocations}/{i_left}")
        r = BudgetReservation(
            reservation_id=uuid.uuid4().hex, ledger_id=self._budget.ledger_id, run_id=self._run_id,
            scope_type=scope_type, scope_id=scope_id, parent_reservation_id=parent_reservation_id,
            reserved_tokens=tokens, reserved_llm_invocations=invocations,
            status="reserved", reserved_at=self._now())
        self._reservations[r.reservation_id] = r
        return r

    def settle(self, reservation_id: str, *, actual_tokens: int, actual_invocations: int) -> BudgetReservation:
        r = self._reservations[reservation_id]
        updated = r.model_copy(update={"actual_tokens": actual_tokens,
                                       "actual_llm_invocations": actual_invocations,
                                       "status": "settled", "settled_at": self._now()})
        self._reservations[reservation_id] = updated
        return updated

    def release(self, reservation_id: str, *, reason: str) -> BudgetReservation:
        r = self._reservations[reservation_id]
        updated = r.model_copy(update={"status": "released", "settled_at": self._now()})
        self._reservations[reservation_id] = updated
        return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_budget.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/budget.py tests/orchestration/test_budget.py
git commit -m "feat(orchestration): budget ledger reserve/settle/release (phase2)"
```

---

## Task 2: Event store (dual cursor + idempotency + replay)

**Files:**
- Create: `guanlan_v2/orchestration/eventstore.py`
- Test: `tests/orchestration/test_eventstore.py`

**Interfaces:**
- Consumes: `events.RunEvent`, `events.EventCursor`.
- Produces: `class EventStore` (in-memory) with:
  - `append(*, run_id, partition, event_type, payload_type, payload_version, payload_ref, idempotency_key, plan_digest=None, causation_id=None, visible=False, occurred_at) -> RunEvent` — assigns monotonic `journal_seq` per `(run_id, partition)`; assigns `visible_seq` per `(run_id, partition)` **only if `visible`**; idempotent by `(run_id, partition, idempotency_key)` (re-append returns the stored event unchanged).
  - `journal(run_id, partition) -> list[RunEvent]` (all, `journal_seq` order).
  - `visible(run_id, partition, *, after: int = 0) -> list[RunEvent]` (visible only, `visible_seq` order, `visible_seq > after`).
  - `make_visible(event_ids: list[str]) -> list[RunEvent]` — assign `visible_seq` (in the given order) to events currently lacking one; idempotent (already-visible events keep their seq).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_eventstore.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.eventstore import EventStore

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


def _append(store, **kw):
    d = dict(run_id="r", partition="main", event_type="NodeStateChanged",
             payload_type="NodeRun", payload_version="1", payload_ref="ref",
             idempotency_key="k", occurred_at=_t())
    d.update(kw)
    return store.append(**d)


def test_journal_seq_is_monotonic_per_partition():
    s = EventStore()
    a = _append(s, idempotency_key="k1")
    b = _append(s, idempotency_key="k2")
    assert (a.journal_seq, b.journal_seq) == (1, 2)


def test_idempotent_append_returns_same_event():
    s = EventStore()
    a = _append(s, idempotency_key="dup")
    b = _append(s, idempotency_key="dup", payload_ref="different")
    assert a.event_id == b.event_id and a.payload_ref == b.payload_ref == "ref"


def test_staged_events_have_no_visible_seq_until_committed():
    s = EventStore()
    staged = _append(s, event_type="ArtifactStaged", idempotency_key="s1", visible=False)
    assert staged.visible_seq is None
    assert s.visible("r", "main") == []
    [made] = s.make_visible([staged.event_id])
    assert made.visible_seq == 1
    assert [e.event_id for e in s.visible("r", "main")] == [staged.event_id]


def test_visible_after_cursor():
    s = EventStore()
    _append(s, idempotency_key="v1", visible=True)
    _append(s, idempotency_key="v2", visible=True)
    assert [e.visible_seq for e in s.visible("r", "main", after=1)] == [2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_eventstore.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/eventstore.py
from __future__ import annotations
import uuid
from datetime import datetime
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.events import RunEvent


class EventStore:
    def __init__(self) -> None:
        self._events: list[RunEvent] = []
        self._by_idem: dict[tuple[str, str, str], str] = {}
        self._by_id: dict[str, int] = {}          # event_id → index in self._events
        self._journal_seq: dict[tuple[str, str], int] = {}
        self._visible_seq: dict[tuple[str, str], int] = {}

    def append(self, *, run_id: str, partition: str, event_type, payload_type: str,
               payload_version: str, payload_ref: str, idempotency_key: str,
               plan_digest: str | None = None, causation_id: str | None = None,
               visible: bool = False, occurred_at: datetime) -> RunEvent:
        idem = (run_id, partition, idempotency_key)
        if idem in self._by_idem:
            return self._events[self._by_id[self._by_idem[idem]]]
        part = (run_id, partition)
        jseq = self._journal_seq.get(part, 0) + 1
        self._journal_seq[part] = jseq
        vseq = None
        if visible:
            vseq = self._visible_seq.get(part, 0) + 1
            self._visible_seq[part] = vseq
        digest_src = {"run_id": run_id, "partition": partition, "event_type": event_type,
                      "payload_type": payload_type, "payload_version": payload_version,
                      "payload_ref": payload_ref, "plan_digest": plan_digest}
        evt = RunEvent(
            event_id=uuid.uuid4().hex, run_id=run_id, partition=partition, plan_digest=plan_digest,
            event_type=event_type, causation_id=causation_id, journal_seq=jseq, visible_seq=vseq,
            idempotency_key=idempotency_key, payload_type=payload_type, payload_version=payload_version,
            payload_ref=payload_ref, occurred_at=occurred_at, content_digest=content_digest(digest_src))
        self._by_id[evt.event_id] = len(self._events)
        self._events.append(evt)
        self._by_idem[idem] = evt.event_id
        return evt

    def journal(self, run_id: str, partition: str) -> list[RunEvent]:
        out = [e for e in self._events if e.run_id == run_id and e.partition == partition]
        return sorted(out, key=lambda e: e.journal_seq)

    def visible(self, run_id: str, partition: str, *, after: int = 0) -> list[RunEvent]:
        out = [e for e in self._events if e.run_id == run_id and e.partition == partition
               and e.visible_seq is not None and e.visible_seq > after]
        return sorted(out, key=lambda e: e.visible_seq)

    def make_visible(self, event_ids: list[str]) -> list[RunEvent]:
        made: list[RunEvent] = []
        for eid in event_ids:
            idx = self._by_id[eid]
            evt = self._events[idx]
            if evt.visible_seq is not None:
                made.append(evt)
                continue
            part = (evt.run_id, evt.partition)
            vseq = self._visible_seq.get(part, 0) + 1
            self._visible_seq[part] = vseq
            updated = evt.model_copy(update={"visible_seq": vseq})
            self._events[idx] = updated
            made.append(updated)
        return made
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_eventstore.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/eventstore.py tests/orchestration/test_eventstore.py
git commit -m "feat(orchestration): dual-cursor idempotent event store (phase2)"
```

---

## Task 3: Worker catalog

**Files:**
- Create: `guanlan_v2/orchestration/catalog.py`
- Test: `tests/orchestration/test_catalog.py`

**Interfaces:**
- Consumes: `spec.WorkerSpec`, `digest.content_digest`.
- Produces: `class WorkerCatalog` — `__init__(self, workers: Iterable[WorkerSpec] = (), *, version: str = "1")`; `register(spec) -> None` (duplicate id with different digest raises `ValueError`); `get(worker_id) -> WorkerSpec` (missing raises `KeyError`); `has(worker_id) -> bool`; property `version -> str`; property `digest -> str` (sha256 over sorted `worker_id -> semantic_digest`, so identical rosters give identical digests regardless of registration order).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_catalog.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.enums import DataMode, Tier, ExecutionKind
from guanlan_v2.orchestration.spec import WorkerSpec, ExecutionSpec
from guanlan_v2.orchestration.catalog import WorkerCatalog


def _w(wid, **kw):
    d = dict(id=wid, lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.READER,
             execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
             input_model="In", outputs={"primary": "X@1"}, supported_modes={DataMode.ONLINE})
    d.update(kw)
    return WorkerSpec(**d)


def test_get_and_has():
    c = WorkerCatalog([_w("a"), _w("b")])
    assert c.has("a") and c.get("b").id == "b"
    with pytest.raises(KeyError):
        c.get("missing")


def test_digest_is_order_independent():
    c1 = WorkerCatalog([_w("a"), _w("b")])
    c2 = WorkerCatalog([_w("b"), _w("a")])
    assert c1.digest == c2.digest


def test_conflicting_registration_raises():
    c = WorkerCatalog([_w("a", persona="p1")])
    with pytest.raises(ValueError):
        c.register(_w("a", persona="p2"))
    c.register(_w("a", persona="p1"))  # identical is idempotent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/catalog.py
from __future__ import annotations
from collections.abc import Iterable
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.spec import WorkerSpec


class WorkerCatalog:
    def __init__(self, workers: Iterable[WorkerSpec] = (), *, version: str = "1"):
        self._version = version
        self._workers: dict[str, WorkerSpec] = {}
        for w in workers:
            self.register(w)

    def register(self, spec: WorkerSpec) -> None:
        existing = self._workers.get(spec.id)
        if existing is not None and existing.semantic_digest() != spec.semantic_digest():
            raise ValueError(f"worker {spec.id} already registered with a different definition")
        self._workers[spec.id] = spec

    def get(self, worker_id: str) -> WorkerSpec:
        return self._workers[worker_id]

    def has(self, worker_id: str) -> bool:
        return worker_id in self._workers

    @property
    def version(self) -> str:
        return self._version

    @property
    def digest(self) -> str:
        table = {wid: w.semantic_digest() for wid, w in self._workers.items()}
        return content_digest(table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_catalog.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/catalog.py tests/orchestration/test_catalog.py
git commit -m "feat(orchestration): worker catalog with order-independent digest (phase2)"
```

---

## Task 4: ArtifactPool staged→barrier commit

**Files:**
- Create: `guanlan_v2/orchestration/pool.py`
- Test: `tests/orchestration/test_pool.py`

**Interfaces:**
- Consumes: `schemas.Artifact`, `events.LayerCommit`, `events.CommittedArtifactRef`, `context.InputSnapshot`, `eventstore.EventStore`, `schema_registry.SchemaRegistry`.
- Produces: `class ArtifactPool` — `__init__(self, run_id, event_store, registry, *, partition="main", now)`:
  - `stage(art, *, layer_index, idempotency_key) -> Artifact` — validates `art.payload` via `registry.validate_payload(art.payload_type, art.payload_version, art.payload)`; stores in the open staging set for `layer_index`; appends `ArtifactStaged` (invisible); raises `RuntimeError` if that layer is already committed.
  - `commit_layer(layer_index, *, node_run_ids, expected_output_keys: set[tuple[str,str]]) -> LayerCommit` — verifies every `(node_id, output_key)` in `expected_output_keys` is staged; assigns `artifact_seq` in canonical `(node_id, output_key)` order; moves them to committed; appends `LayerCommitted` and makes staged events visible atomically; closes the layer. Missing expected key → `RuntimeError`. Re-commit → `RuntimeError`.
  - `committed_artifact(node_id, output_key) -> Artifact | None`.
  - `get_typed(slot, model) -> BaseModel | None` (latest committed by `artifact_seq` for that slot, validated to `model`).
  - `history(slot) -> list[Artifact]` (committed only).
  - `freeze_input_snapshot(layer_index, *, context_snapshot_id=None) -> InputSnapshot` (over currently committed artifacts).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_pool.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from pydantic import BaseModel
from guanlan_v2.orchestration.eventstore import EventStore
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import Artifact, Provenance
from guanlan_v2.orchestration.enums import DataMode
from guanlan_v2.orchestration.pool import ArtifactPool

UTC = timezone.utc
def _now(): return datetime(2026, 7, 15, tzinfo=UTC)


class Doc(BaseModel):
    text: str


def _registry():
    r = SchemaRegistry(); r.register("Doc", "1", Doc); return r


def _prov(node):
    return Provenance(run_id="r", plan_id="p", plan_digest="pd", node_id=node,
                      as_of=_now(), pit_mode=DataMode.ONLINE, code_version="v1")


def _art(node, slot, key="primary", text="hi"):
    return Artifact(id=f"{node}:{key}", kind="doc", slot=slot, output_key=key, producer_node_id=node,
                    run_id="r", payload_type="Doc", payload_version="1", payload={"text": text},
                    rendered_md=text, provenance=_prov(node), created_at=_now(),
                    content_digest="c", rendered_from_payload_digest="rp")


def _pool():
    return ArtifactPool("r", EventStore(), _registry(), now=_now)


def test_staged_is_not_visible_until_commit():
    p = _pool()
    p.stage(_art("n1", "s1"), layer_index=0, idempotency_key="i1")
    assert p.get_typed("s1", Doc) is None            # staged, not committed
    p.commit_layer(0, node_run_ids=["nr1"], expected_output_keys={("n1", "primary")})
    assert p.get_typed("s1", Doc).text == "hi"


def test_commit_missing_expected_key_raises():
    p = _pool()
    p.stage(_art("n1", "s1"), layer_index=0, idempotency_key="i1")
    with pytest.raises(RuntimeError):
        p.commit_layer(0, node_run_ids=["nr1"], expected_output_keys={("n1", "primary"), ("n2", "primary")})


def test_late_stage_after_commit_rejected():
    p = _pool()
    p.stage(_art("n1", "s1"), layer_index=0, idempotency_key="i1")
    p.commit_layer(0, node_run_ids=["nr1"], expected_output_keys={("n1", "primary")})
    with pytest.raises(RuntimeError):
        p.stage(_art("n9", "s1"), layer_index=0, idempotency_key="i9")


def test_artifact_seq_is_canonical_not_stage_order():
    p = _pool()
    p.stage(_art("nB", "s1", text="b"), layer_index=0, idempotency_key="iB")
    p.stage(_art("nA", "s2", text="a"), layer_index=0, idempotency_key="iA")
    commit = p.commit_layer(0, node_run_ids=["x"],
                            expected_output_keys={("nB", "primary"), ("nA", "primary")})
    # canonical order by (node_id, output_key): nA before nB regardless of stage order
    assert [r.artifact_id for r in commit.artifacts] == ["nA:primary", "nB:primary"]


def test_invalid_payload_rejected_at_stage():
    p = _pool()
    bad = _art("n1", "s1"); bad = bad.model_copy(update={"payload": {"wrong": 1}})
    with pytest.raises(Exception):
        p.stage(bad, layer_index=0, idempotency_key="ibad")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_pool.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/pool.py
from __future__ import annotations
from datetime import datetime
from typing import Callable
from pydantic import BaseModel
from guanlan_v2.orchestration.context import InputSnapshot
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.eventstore import EventStore
from guanlan_v2.orchestration.events import CommittedArtifactRef, LayerCommit
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import Artifact, ArtifactRef


class ArtifactPool:
    def __init__(self, run_id: str, event_store: EventStore, registry: SchemaRegistry,
                 *, partition: str = "main", now: Callable[[], datetime]):
        self.run_id = run_id
        self._store = event_store
        self._registry = registry
        self._partition = partition
        self._now = now
        self._staged: dict[int, dict[tuple[str, str], tuple[Artifact, str]]] = {}  # layer → {(node,key): (art, event_id)}
        self._committed: dict[tuple[str, str], Artifact] = {}                        # (node,key) → art
        self._by_slot: dict[str, list[tuple[int, Artifact]]] = {}                    # slot → [(artifact_seq, art)]
        self._closed_layers: set[int] = set()
        self._artifact_seq = 0

    def stage(self, art: Artifact, *, layer_index: int, idempotency_key: str) -> Artifact:
        if layer_index in self._closed_layers:
            raise RuntimeError(f"layer {layer_index} already committed; cannot stage {art.id}")
        self._registry.validate_payload(art.payload_type, art.payload_version, art.payload)
        evt = self._store.append(
            run_id=self.run_id, partition=self._partition, event_type="ArtifactStaged",
            payload_type="Artifact", payload_version=art.schema_version, payload_ref=art.id,
            idempotency_key=idempotency_key, visible=False, occurred_at=self._now())
        self._staged.setdefault(layer_index, {})[(art.producer_node_id, art.output_key)] = (art, evt.event_id)
        return art

    def commit_layer(self, layer_index: int, *, node_run_ids: list[str],
                     expected_output_keys: set[tuple[str, str]]) -> LayerCommit:
        if layer_index in self._closed_layers:
            raise RuntimeError(f"layer {layer_index} already committed")
        staged = self._staged.get(layer_index, {})
        missing = expected_output_keys - set(staged.keys())
        if missing:
            raise RuntimeError(f"layer {layer_index} missing expected outputs: {sorted(missing)}")
        refs: list[CommittedArtifactRef] = []
        event_ids: list[str] = []
        for key in sorted(expected_output_keys):            # canonical (node_id, output_key)
            art, event_id = staged[key]
            self._artifact_seq += 1
            self._committed[key] = art
            self._by_slot.setdefault(art.slot, []).append((self._artifact_seq, art))
            refs.append(CommittedArtifactRef(artifact_id=art.id, artifact_seq=self._artifact_seq))
            event_ids.append(event_id)
        self._store.make_visible(event_ids)
        self._store.append(
            run_id=self.run_id, partition=self._partition, event_type="LayerCommitted",
            payload_type="LayerCommit", payload_version="1", payload_ref=f"layer:{layer_index}",
            idempotency_key=f"commit:{layer_index}", visible=True, occurred_at=self._now())
        self._closed_layers.add(layer_index)
        return LayerCommit(plan_digest="", layer_index=layer_index, node_run_ids=node_run_ids,
                           artifacts=refs, committed_at=self._now())

    def committed_artifact(self, node_id: str, output_key: str = "primary") -> Artifact | None:
        return self._committed.get((node_id, output_key))

    def get_typed(self, slot: str, model: type[BaseModel]) -> BaseModel | None:
        entries = self._by_slot.get(slot)
        if not entries:
            return None
        _, art = max(entries, key=lambda t: t[0])
        return model.model_validate(art.payload)

    def history(self, slot: str) -> list[Artifact]:
        return [a for _, a in sorted(self._by_slot.get(slot, []), key=lambda t: t[0])]

    def freeze_input_snapshot(self, layer_index: int, *, context_snapshot_id: str | None = None) -> InputSnapshot:
        refs = [ArtifactRef(artifact_id=a.id, producer_node_id=a.producer_node_id, slot=a.slot,
                            output_key=a.output_key, kind=a.kind, content_digest=a.content_digest,
                            relation="input")
                for (_, _), a in sorted(self._committed.items())]
        digest = content_digest({"refs": [r.model_dump(mode="json") for r in refs],
                                 "layer": layer_index, "ctx": context_snapshot_id})
        return InputSnapshot(id=f"{self.run_id}:L{layer_index}", run_id=self.run_id, plan_digest="",
                             layer_index=layer_index, context_snapshot_id=context_snapshot_id,
                             artifact_refs=refs, data_result_ids=[], memory_record_refs=[],
                             frozen_at=self._now(), content_digest=digest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_pool.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/pool.py tests/orchestration/test_pool.py
git commit -m "feat(orchestration): staged->barrier ArtifactPool (phase2)"
```

---

## Task 5: Plan freeze validator

**Files:**
- Create: `guanlan_v2/orchestration/validate.py`
- Test: `tests/orchestration/test_validate.py`

**Interfaces:**
- Consumes: `spec.PlanDraft/PlanNode/Dependency/Plan`, `catalog.WorkerCatalog`, `enums.NodeStatus/DependencyPolicy/ExecutionKind/DataMode`.
- Produces:
  - `class PlanValidationError(Exception)`.
  - `validate_plan_draft(draft: PlanDraft, *, catalog: WorkerCatalog) -> None` — raises `PlanValidationError` on any hard-gate violation (rules below).
  - `_topo_layers(draft) -> list[list[str]]` (helper; Kahn by node deps; cycle → `PlanValidationError`).

Hard-gate rules implemented:
1. node ids unique; every `node.worker_id` exists in `catalog`.
2. every `Dependency.upstream_node_id` refers to a node in the draft; DAG acyclic; every `sink_node_id` is a real node and reachable (has a path from some source).
3. `Dependency.artifact_slot`/`upstream_output_key` must exist in the upstream node's worker `outputs`.
4. `policy==BLOCK` ⇒ `accept_statuses == {COMPLETED}`; `INCOMPLETE/FAILED/BLOCKED/CANCELLED` never in `accept_statuses`.
5. same `writes_slot` by ≥2 nodes ⇒ a `ReducerCfg` for that slot must exist.
6. deterministic worker (`execution.kind==DETERMINISTIC`) ⇒ `execution.handler_ref` set; LLM worker ⇒ `execution.model_tier` set.
7. `node.worker_id`'s `supported_modes` must contain `draft.mode`.
8. every sink whose worker primary output is a decision payload (`can_emit_decision`) — enforce: a sink node whose worker has `can_emit_decision=False` may still be a research sink, but a node writing a decision-class slot must have `can_emit_decision=True`. (v1: enforce `can_emit_decision` required only when the worker declares it; test covers the reject path.)
9. `phase=="main"` ⇒ `context_snapshot_id` set; `phase=="bootstrap"` ⇒ `context_snapshot_id is None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_validate.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.enums import (DataMode, Tier, ExecutionKind, NodeStatus,
                                            DependencyPolicy, PlanSource)
from guanlan_v2.orchestration.spec import (WorkerSpec, ExecutionSpec, PlanNode, Dependency, PlanDraft)
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.validate import validate_plan_draft, PlanValidationError

UTC = timezone.utc


def _w(wid, **kw):
    d = dict(id=wid, lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.READER,
             execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
             input_model="In", outputs={"primary": "Doc@1"}, supported_modes={DataMode.ONLINE})
    d.update(kw)
    return WorkerSpec(**d)


def _draft(nodes, sinks, **kw):
    d = dict(id="pl", run_id="r", request_id="q", phase="main", source=PlanSource.PRESET,
             goal="g", as_of=datetime(2026, 7, 15, tzinfo=UTC), mode=DataMode.ONLINE,
             context_snapshot_id="cs", universe=[], nodes=nodes, sink_node_ids=sinks,
             catalog_version="1", catalog_digest="d")
    d.update(kw)
    return PlanDraft(**d)


def _cat():
    return WorkerCatalog([_w("a"), _w("b")])


def _node(nid, worker, deps=(), slot=None):
    return PlanNode(id=nid, worker_id=worker, writes_slot=slot or nid, dependencies=list(deps))


def test_valid_two_node_plan_passes():
    n1 = _node("n1", "a")
    n2 = _node("n2", "b", deps=[Dependency(upstream_node_id="n1", artifact_slot="n1",
                                            upstream_output_key="primary", inject_as="ctx")])
    validate_plan_draft(_draft([n1, n2], ["n2"]), catalog=_cat())


def test_unknown_worker_rejected():
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([_node("n1", "ghost")], ["n1"]), catalog=_cat())


def test_cycle_rejected():
    n1 = _node("n1", "a", deps=[Dependency(upstream_node_id="n2", artifact_slot="n2", inject_as="x")])
    n2 = _node("n2", "b", deps=[Dependency(upstream_node_id="n1", artifact_slot="n1", inject_as="y")])
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([n1, n2], ["n2"]), catalog=_cat())


def test_block_dep_must_accept_only_completed():
    bad = Dependency(upstream_node_id="n1", artifact_slot="n1", inject_as="ctx",
                     policy=DependencyPolicy.BLOCK, accept_statuses={NodeStatus.DEGRADED})
    n2 = _node("n2", "b", deps=[bad])
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([_node("n1", "a"), n2], ["n2"]), catalog=_cat())


def test_dependency_output_key_must_exist_on_upstream():
    dep = Dependency(upstream_node_id="n1", artifact_slot="n1", upstream_output_key="ghost", inject_as="c")
    n2 = _node("n2", "b", deps=[dep])
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([_node("n1", "a"), n2], ["n2"]), catalog=_cat())


def test_same_slot_multiwrite_needs_reducer():
    n1 = _node("n1", "a", slot="shared")
    n2 = _node("n2", "b", slot="shared")
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([n1, n2], ["n1", "n2"]), catalog=_cat())


def test_main_phase_requires_context_snapshot():
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([_node("n1", "a")], ["n1"], context_snapshot_id=None), catalog=_cat())


def test_deterministic_worker_needs_handler():
    det = _w("d", execution=ExecutionSpec(kind=ExecutionKind.DETERMINISTIC))  # no handler_ref
    cat = WorkerCatalog([det])
    with pytest.raises(PlanValidationError):
        validate_plan_draft(_draft([_node("n1", "d")], ["n1"]), catalog=cat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/validate.py
from __future__ import annotations
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.enums import DependencyPolicy, ExecutionKind, NodeStatus
from guanlan_v2.orchestration.spec import PlanDraft

_SUCCESS_ONLY = {NodeStatus.COMPLETED}
_NEVER_SUCCESS = {NodeStatus.INCOMPLETE, NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.CANCELLED}


class PlanValidationError(Exception):
    pass


def _topo_layers(draft: PlanDraft) -> list[list[str]]:
    ids = [n.id for n in draft.nodes]
    deps = {n.id: [d.upstream_node_id for d in n.dependencies] for n in draft.nodes}
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for nid, ups in deps.items():
        for up in ups:
            indeg[nid] += 1
            adj[up].append(nid)
    layers: list[list[str]] = []
    frontier = sorted([i for i in ids if indeg[i] == 0])
    seen = 0
    while frontier:
        layers.append(frontier)
        seen += len(frontier)
        nxt: list[str] = []
        for nid in frontier:
            for child in adj[nid]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    nxt.append(child)
        frontier = sorted(nxt)
    if seen != len(ids):
        raise PlanValidationError("plan DAG has a cycle")
    return layers


def validate_plan_draft(draft: PlanDraft, *, catalog: WorkerCatalog) -> None:
    ids = [n.id for n in draft.nodes]
    if len(ids) != len(set(ids)):
        raise PlanValidationError("duplicate PlanNode.id")
    by_id = {n.id: n for n in draft.nodes}

    # phase / context snapshot
    if draft.phase == "main" and not draft.context_snapshot_id:
        raise PlanValidationError("main phase requires context_snapshot_id")
    if draft.phase == "bootstrap" and draft.context_snapshot_id:
        raise PlanValidationError("bootstrap phase forbids context_snapshot_id")

    # workers exist + mode + execution
    for n in draft.nodes:
        if not catalog.has(n.worker_id):
            raise PlanValidationError(f"unknown worker {n.worker_id} for node {n.id}")
        spec = catalog.get(n.worker_id)
        if draft.mode not in spec.supported_modes:
            raise PlanValidationError(f"worker {n.worker_id} does not support mode {draft.mode}")
        if spec.execution.kind == ExecutionKind.DETERMINISTIC and not spec.execution.handler_ref:
            raise PlanValidationError(f"deterministic worker {n.worker_id} needs handler_ref")
        if spec.execution.kind == ExecutionKind.LLM and not spec.execution.model_tier:
            raise PlanValidationError(f"LLM worker {n.worker_id} needs model_tier")

    # dependencies
    for n in draft.nodes:
        for dep in n.dependencies:
            if dep.upstream_node_id not in by_id:
                raise PlanValidationError(f"node {n.id} depends on unknown node {dep.upstream_node_id}")
            up_spec = catalog.get(by_id[dep.upstream_node_id].worker_id)
            if dep.upstream_output_key not in up_spec.outputs:
                raise PlanValidationError(
                    f"node {n.id} dep output_key {dep.upstream_output_key} not in {dep.upstream_node_id} outputs")
            if _NEVER_SUCCESS & dep.accept_statuses:
                raise PlanValidationError(f"node {n.id} dep accept_statuses includes a non-success status")
            if dep.policy == DependencyPolicy.BLOCK and dep.accept_statuses != _SUCCESS_ONLY:
                raise PlanValidationError(f"node {n.id} BLOCK dep must accept exactly {{COMPLETED}}")

    # acyclic (raises on cycle)
    _topo_layers(draft)

    # sinks real + reachable
    reachable = {n.id for n in draft.nodes if not n.dependencies}
    changed = True
    while changed:
        changed = False
        for n in draft.nodes:
            if n.id not in reachable and any(d.upstream_node_id in reachable for d in n.dependencies):
                reachable.add(n.id); changed = True
    for sid in draft.sink_node_ids:
        if sid not in by_id:
            raise PlanValidationError(f"sink {sid} is not a node")
        if sid not in reachable:
            raise PlanValidationError(f"sink {sid} not reachable")

    # same-slot multi-write needs reducer
    slot_writers: dict[str, list[str]] = {}
    for n in draft.nodes:
        slot_writers.setdefault(n.writes_slot, []).append(n.id)
    reducer_slots = {r.slot for r in draft.reducers}
    for slot, writers in slot_writers.items():
        if len(writers) > 1 and slot not in reducer_slots:
            raise PlanValidationError(f"slot {slot} has multiple writers but no reducer")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_validate.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/validate.py tests/orchestration/test_validate.py
git commit -m "feat(orchestration): strict Plan freeze validator (phase2)"
```

---

## Task 6: freeze_plan (digest + budget binding)

**Files:**
- Modify: `guanlan_v2/orchestration/validate.py` (add `freeze_plan`)
- Test: `tests/orchestration/test_freeze.py`

**Interfaces:**
- Consumes: `validate_plan_draft`, `spec.PlanDraft/Plan`, `catalog.WorkerCatalog`, `digest.content_digest`.
- Produces: `freeze_plan(draft: PlanDraft, *, catalog, budget_reservation_id: str, frozen_at: datetime) -> Plan` — validates, then computes `plan_digest = content_digest(draft.model_dump minus {id, run_id, request_id} + catalog_digest)`, returns a `Plan` with `budget_reservation_id`, `frozen_at`, `plan_digest`. Two drafts identical in executable content get the same `plan_digest`; changing any node/dep/gate changes it; `id`/`run_id`/`request_id` do NOT change it.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_freeze.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.enums import DataMode, Tier, ExecutionKind, PlanSource
from guanlan_v2.orchestration.spec import WorkerSpec, ExecutionSpec, PlanNode, PlanDraft
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.validate import freeze_plan

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


def _cat():
    return WorkerCatalog([WorkerSpec(id="a", lane="text", persona="p", system_prompt_ref="s.md",
                                     tier=Tier.READER, execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
                                     input_model="In", outputs={"primary": "Doc@1"}, supported_modes={DataMode.ONLINE})])


def _draft(**kw):
    d = dict(id="pl", run_id="r", request_id="q", phase="main", source=PlanSource.PRESET, goal="g",
             as_of=_t(), mode=DataMode.ONLINE, context_snapshot_id="cs", universe=[],
             nodes=[PlanNode(id="n1", worker_id="a", writes_slot="s")], sink_node_ids=["n1"],
             catalog_version="1", catalog_digest=_cat().digest)
    d.update(kw)
    return PlanDraft(**d)


def test_freeze_produces_plan_with_digest_and_reservation():
    p = freeze_plan(_draft(), catalog=_cat(), budget_reservation_id="res1", frozen_at=_t())
    assert p.budget_reservation_id == "res1" and p.plan_digest


def test_plan_digest_ignores_run_and_request_id():
    a = freeze_plan(_draft(run_id="r1", request_id="q1"), catalog=_cat(), budget_reservation_id="x", frozen_at=_t())
    b = freeze_plan(_draft(run_id="r2", request_id="q2"), catalog=_cat(), budget_reservation_id="y", frozen_at=_t())
    assert a.plan_digest == b.plan_digest


def test_plan_digest_changes_with_node_change():
    a = freeze_plan(_draft(), catalog=_cat(), budget_reservation_id="x", frozen_at=_t())
    b = freeze_plan(_draft(nodes=[PlanNode(id="n1", worker_id="a", writes_slot="OTHER")]),
                    catalog=_cat(), budget_reservation_id="x", frozen_at=_t())
    assert a.plan_digest != b.plan_digest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_freeze.py -v`
Expected: FAIL with `ImportError: cannot import name 'freeze_plan'`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/validate.py`)

```python
# ── append to guanlan_v2/orchestration/validate.py ──
from datetime import datetime
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.spec import Plan

_DIGEST_EXCLUDE = {"id", "run_id", "request_id", "approval_policy"}


def freeze_plan(draft: PlanDraft, *, catalog: WorkerCatalog, budget_reservation_id: str,
                frozen_at: datetime) -> Plan:
    validate_plan_draft(draft, catalog=catalog)
    body = {k: v for k, v in draft.model_dump(mode="json").items() if k not in _DIGEST_EXCLUDE}
    body["catalog_digest"] = catalog.digest
    plan_digest = content_digest(body)
    return Plan(**draft.model_dump(), budget_reservation_id=budget_reservation_id,
                frozen_at=frozen_at, plan_digest=plan_digest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_freeze.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/validate.py tests/orchestration/test_freeze.py
git commit -m "feat(orchestration): freeze_plan digest + budget binding (phase2)"
```

---

## Task 7: Worker executor (typed Artifact + NodeRun + honesty classify)

**Files:**
- Create: `guanlan_v2/orchestration/worker.py`
- Test: `tests/orchestration/test_worker.py`

**Interfaces:**
- Consumes: `spec.PlanNode/WorkerSpec`, `context.RunContext`, `schemas.Artifact/Provenance/NumberAnchor/NodeRun`, `enums.NodeStatus/ToolCallRequirement`, `digest.content_digest`.
- Produces:
  - `class WorkerResult(BaseModel)`: `payload: dict`, `numbers: list[NumberAnchor] = []`, `tool_call_count: int = 0`, `degraded: bool = False`, `input_tokens: int = 0`, `output_tokens: int = 0`.
  - `class WorkerHandler(Protocol)`: `async def execute(self, node: PlanNode, spec: WorkerSpec, inputs: dict, ctx: RunContext) -> WorkerResult`.
  - `async def run_node(node, spec, handler, inputs, ctx, *, degraded_inputs: bool, now, attempt=1) -> tuple[NodeRun, Artifact | None]` — runs handler; on exception → NodeRun `FAILED`, no artifact; else classify: `INCOMPLETE` if (`evidence_policy.tool_calls==REQUIRED and tool_call_count==0`) or (`evidence_policy.require_number_anchors and not allow_unsourced_numbers and any number is_unsourced`); else `DEGRADED` if `result.degraded or degraded_inputs`; else `COMPLETED`. On non-failed, build one `Artifact` for `outputs["primary"]` with runtime `Provenance` (payload validated to the primary output model name string is caller's registry concern; here we only carry `payload_type` = primary model name from `spec.outputs["primary"]` split on "@").

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_worker.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from guanlan_v2.orchestration.enums import (DataMode, Tier, ExecutionKind, NodeStatus,
                                            ToolCallRequirement)
from guanlan_v2.orchestration.spec import WorkerSpec, ExecutionSpec, PlanNode, EvidencePolicy
from guanlan_v2.orchestration.schemas import NumberAnchor
from guanlan_v2.orchestration.context import (RunContext, DataContext, ClockSpec, RunBudget)
from guanlan_v2.orchestration.enums import DataBackend
from guanlan_v2.orchestration.worker import run_node, WorkerResult

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


def _ctx():
    clock = ClockSpec(as_of=_t(), timezone="UTC", calendar_id="XSHG", clock_version="1")
    dc = DataContext(as_of=_t(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
                     strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
                     source_config_digest="c", data_snapshot_id="s")
    return RunContext(run_id="r", data=dc, context_snapshot_id="cs", memory_snapshot_hash="m",
                      budget=RunBudget(ledger_id="L", max_tokens=1, max_llm_invocations=1, max_concurrency=1),
                      cancellation_token_id="ct")


def _spec(**kw):
    d = dict(id="w", lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.WRITER,
             execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"), can_emit_decision=True,
             input_model="In", outputs={"primary": "Doc@1"}, supported_modes={DataMode.ONLINE})
    d.update(kw)
    return WorkerSpec(**d)


def _node(): return PlanNode(id="n1", worker_id="w", writes_slot="s")


class OkHandler:
    async def execute(self, node, spec, inputs, ctx):
        return WorkerResult(payload={"text": "hi"}, tool_call_count=1)


class BoomHandler:
    async def execute(self, node, spec, inputs, ctx):
        raise RuntimeError("kaboom")


class NoToolHandler:
    async def execute(self, node, spec, inputs, ctx):
        return WorkerResult(payload={"text": "hi"}, tool_call_count=0)


def test_ok_handler_completes_with_artifact():
    nr, art = asyncio.run(run_node(_node(), _spec(), OkHandler(), {}, _ctx(),
                                   degraded_inputs=False, now=_t))
    assert nr.status == NodeStatus.COMPLETED and art is not None and art.payload == {"text": "hi"}
    assert art.payload_type == "Doc" and art.payload_version == "1"


def test_exception_is_failed_no_artifact():
    nr, art = asyncio.run(run_node(_node(), _spec(), BoomHandler(), {}, _ctx(),
                                   degraded_inputs=False, now=_t))
    assert nr.status == NodeStatus.FAILED and art is None and "kaboom" in (nr.reason or "")


def test_required_tool_zero_calls_is_incomplete():
    spec = _spec(evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.REQUIRED))
    nr, art = asyncio.run(run_node(_node(), spec, NoToolHandler(), {}, _ctx(),
                                   degraded_inputs=False, now=_t))
    assert nr.status == NodeStatus.INCOMPLETE and art is None


def test_degraded_inputs_yield_degraded_status():
    nr, art = asyncio.run(run_node(_node(), _spec(), OkHandler(), {}, _ctx(),
                                   degraded_inputs=True, now=_t))
    assert nr.status == NodeStatus.DEGRADED and art is not None


def test_unsourced_number_is_incomplete():
    class NumHandler:
        async def execute(self, node, spec, inputs, ctx):
            return WorkerResult(payload={"text": "hi"}, tool_call_count=1,
                                numbers=[NumberAnchor(label="mv", value=1.0, payload_path="$.mv")])
    nr, art = asyncio.run(run_node(_node(), _spec(), NumHandler(), {}, _ctx(),
                                   degraded_inputs=False, now=_t))
    assert nr.status == NodeStatus.INCOMPLETE and art is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/worker.py
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any, Callable, Protocol
from pydantic import BaseModel, Field
from guanlan_v2.orchestration.context import RunContext
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import NodeStatus, ToolCallRequirement
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, NumberAnchor, Provenance
from guanlan_v2.orchestration.spec import PlanNode, WorkerSpec


class WorkerResult(BaseModel):
    payload: dict[str, Any]
    numbers: list[NumberAnchor] = Field(default_factory=list)
    tool_call_count: int = 0
    degraded: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    rendered_md: str = ""


class WorkerHandler(Protocol):
    async def execute(self, node: PlanNode, spec: WorkerSpec, inputs: dict[str, Any],
                      ctx: RunContext) -> WorkerResult: ...


def _classify(spec: WorkerSpec, result: WorkerResult, degraded_inputs: bool) -> NodeStatus:
    pol = spec.evidence_policy
    if pol.tool_calls == ToolCallRequirement.REQUIRED and result.tool_call_count == 0:
        return NodeStatus.INCOMPLETE
    if pol.require_number_anchors and not pol.allow_unsourced_numbers:
        if any(n.is_unsourced for n in result.numbers):
            return NodeStatus.INCOMPLETE
    if result.degraded or degraded_inputs:
        return NodeStatus.DEGRADED
    return NodeStatus.COMPLETED


async def run_node(node: PlanNode, spec: WorkerSpec, handler: WorkerHandler, inputs: dict[str, Any],
                   ctx: RunContext, *, degraded_inputs: bool, now: Callable[[], datetime],
                   attempt: int = 1) -> tuple[NodeRun, Artifact | None]:
    started = now()
    base = dict(node_run_id=uuid.uuid4().hex, run_id=ctx.run_id, plan_id=node.id, plan_digest="",
                node_id=node.id, worker_id=spec.id, attempt_id=uuid.uuid4().hex, attempt=attempt,
                input_snapshot_hash="", started_at=started)
    try:
        result = await handler.execute(node, spec, inputs, ctx)
    except Exception as exc:  # noqa: BLE001 — honest failure
        nr = NodeRun(**base, status=NodeStatus.FAILED, finished_at=now(),
                     reason_code="handler_error", reason=f"{type(exc).__name__}: {exc}",
                     error_type=type(exc).__name__)
        return nr, None

    status = _classify(spec, result, degraded_inputs)
    if status in (NodeStatus.INCOMPLETE,):
        nr = NodeRun(**base, status=status, finished_at=now(),
                     reason_code="evidence_policy", reason="incomplete per evidence policy",
                     tool_call_count=result.tool_call_count,
                     input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        return nr, None

    ptype, pver = spec.outputs["primary"].split("@")
    art = Artifact(
        id=f"{node.id}:primary", kind=node.writes_slot, slot=node.writes_slot, output_key="primary",
        producer_node_id=node.id, run_id=ctx.run_id, payload_type=ptype, payload_version=pver,
        payload=result.payload, rendered_md=result.rendered_md or "",
        provenance=Provenance(run_id=ctx.run_id, plan_id=node.id, plan_digest="", node_id=node.id,
                              as_of=ctx.data.as_of, pit_mode=ctx.data.mode, code_version="phase2"),
        numbers=result.numbers, created_at=now(),
        content_digest=content_digest(result.payload),
        rendered_from_payload_digest=content_digest({"md": result.rendered_md or ""}))
    nr = NodeRun(**base, status=status, finished_at=now(),
                 output_keys=["primary"], output_artifact_ids=[art.id],
                 tool_call_count=result.tool_call_count,
                 input_tokens=result.input_tokens, output_tokens=result.output_tokens)
    return nr, art
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_worker.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/worker.py tests/orchestration/test_worker.py
git commit -m "feat(orchestration): worker executor with honesty classify (phase2)"
```

---

## Task 8: DAG runner (layers, gating, bounded parallel, barrier, budget)

**Files:**
- Create: `guanlan_v2/orchestration/dag.py`
- Test: `tests/orchestration/test_dag.py`

**Interfaces:**
- Consumes: `spec.Plan/PlanNode/Dependency`, `catalog.WorkerCatalog`, `worker.WorkerHandler/run_node`, `pool.ArtifactPool`, `budget.BudgetLedger`, `validate._topo_layers`, `enums.DependencyPolicy/NodeStatus`, `context.RunContext`.
- Produces:
  - `class RunResult(BaseModel)`: `run_id: str`, `node_runs: dict[str, NodeRun]`, `status: Literal["completed","partial","failed"]`.
  - `async def run_plan(plan, ctx, *, pool, catalog, handlers: dict[str, WorkerHandler], budget, now, max_concurrency=None) -> RunResult` — for each Kahn layer (from `_topo_layers`, nodes sorted by id): freeze input snapshot; for each node (bounded by `asyncio.Semaphore(min(plan.max_concurrency, budget max, max_concurrency or ∞))`): apply gating (a `BLOCK` dep whose upstream NodeRun status ∉ `accept_statuses` ⇒ node `BLOCKED`, not executed; a `DEGRADE`/`SKIP` dep missing/failed ⇒ omit its input + `degraded_inputs=True`); build `inputs[dep.inject_as]` from `pool.committed_artifact(up, key).payload` for satisfied deps; reserve budget for the node, run via `run_node`, settle; stage successful artifact. After the layer, `commit_layer` with the `(node_id, "primary")` set for `COMPLETED`/`DEGRADED` nodes. `status` = `completed` if all nodes COMPLETED, `failed` if any sink FAILED/BLOCKED, else `partial`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_dag.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pydantic import BaseModel
from guanlan_v2.orchestration.enums import (DataMode, DataBackend, Tier, ExecutionKind, NodeStatus,
                                            DependencyPolicy, PlanSource)
from guanlan_v2.orchestration.spec import (WorkerSpec, ExecutionSpec, PlanNode, Dependency, PlanDraft)
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.validate import freeze_plan
from guanlan_v2.orchestration.context import RunContext, DataContext, ClockSpec, RunBudget
from guanlan_v2.orchestration.eventstore import EventStore
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.worker import WorkerResult
from guanlan_v2.orchestration.dag import run_plan

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


class Doc(BaseModel):
    text: str


def _registry():
    r = SchemaRegistry(); r.register("Doc", "1", Doc); return r


def _w(wid):
    return WorkerSpec(id=wid, lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.WRITER,
                      execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
                      can_emit_decision=True, input_model="In", outputs={"primary": "Doc@1"},
                      supported_modes={DataMode.ONLINE})


class EchoHandler:
    def __init__(self, tag): self.tag = tag
    async def execute(self, node, spec, inputs, ctx):
        up = ",".join(f"{k}={v}" for k, v in sorted(inputs.items()))
        return WorkerResult(payload={"text": f"{self.tag}[{up}]"}, tool_call_count=1)


class FailHandler:
    async def execute(self, node, spec, inputs, ctx):
        raise RuntimeError("boom")


def _ctx():
    clock = ClockSpec(as_of=_t(), timezone="UTC", calendar_id="XSHG", clock_version="1")
    dc = DataContext(as_of=_t(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
                     strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
                     source_config_digest="c", data_snapshot_id="s")
    return RunContext(run_id="r", data=dc, context_snapshot_id="cs", memory_snapshot_hash="m",
                      budget=RunBudget(ledger_id="L", max_tokens=100000, max_llm_invocations=100,
                                       max_concurrency=4), cancellation_token_id="ct")


def _plan(nodes, sinks, cat):
    draft = PlanDraft(id="pl", run_id="r", request_id="q", phase="main", source=PlanSource.PRESET,
                      goal="g", as_of=_t(), mode=DataMode.ONLINE, context_snapshot_id="cs", universe=[],
                      nodes=nodes, sink_node_ids=sinks, catalog_version="1", catalog_digest=cat.digest)
    return freeze_plan(draft, catalog=cat, budget_reservation_id="res", frozen_at=_t())


def _harness(nodes, sinks, handlers):
    cat = WorkerCatalog([_w(n.worker_id) for n in nodes])
    plan = _plan(nodes, sinks, cat)
    ctx = _ctx()
    pool = ArtifactPool("r", EventStore(), _registry(), now=_t)
    budget = BudgetLedger(ctx.budget, run_id="r", now=_t)
    return asyncio.run(run_plan(plan, ctx, pool=pool, catalog=cat, handlers=handlers,
                                budget=budget, now=_t)), pool


def test_two_node_chain_passes_upstream_payload():
    n1 = PlanNode(id="n1", worker_id="n1", writes_slot="n1")
    n2 = PlanNode(id="n2", worker_id="n2", writes_slot="n2",
                  dependencies=[Dependency(upstream_node_id="n1", artifact_slot="n1", inject_as="ctx")])
    res, pool = _harness([n1, n2], ["n2"], {"n1": EchoHandler("A"), "n2": EchoHandler("B")})
    assert res.status == "completed"
    assert res.node_runs["n2"].status == NodeStatus.COMPLETED
    assert "ctx=A[]" in pool.get_typed("n2", Doc).text        # upstream payload injected


def test_block_dep_blocks_downstream_when_upstream_fails():
    n1 = PlanNode(id="n1", worker_id="n1", writes_slot="n1")
    n2 = PlanNode(id="n2", worker_id="n2", writes_slot="n2",
                  dependencies=[Dependency(upstream_node_id="n1", artifact_slot="n1", inject_as="ctx")])
    res, _ = _harness([n1, n2], ["n2"], {"n1": FailHandler(), "n2": EchoHandler("B")})
    assert res.node_runs["n1"].status == NodeStatus.FAILED
    assert res.node_runs["n2"].status == NodeStatus.BLOCKED
    assert res.status == "failed"


def test_soft_dep_failure_degrades_downstream_but_runs():
    n1 = PlanNode(id="n1", worker_id="n1", writes_slot="n1")
    n2 = PlanNode(id="n2", worker_id="n2", writes_slot="n2",
                  dependencies=[Dependency(upstream_node_id="n1", artifact_slot="n1", inject_as="ctx",
                                           policy=DependencyPolicy.DEGRADE,
                                           accept_statuses={NodeStatus.COMPLETED})])
    res, pool = _harness([n1, n2], ["n2"], {"n1": FailHandler(), "n2": EchoHandler("B")})
    assert res.node_runs["n1"].status == NodeStatus.FAILED
    assert res.node_runs["n2"].status == NodeStatus.DEGRADED
    assert pool.get_typed("n2", Doc).text == "B[]"            # ran without the failed input
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_dag.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/dag.py
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Callable, Literal
from pydantic import BaseModel
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.context import RunContext
from guanlan_v2.orchestration.enums import DependencyPolicy, NodeStatus
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.schemas import NodeRun
from guanlan_v2.orchestration.spec import Plan, PlanNode
from guanlan_v2.orchestration.validate import _topo_layers
from guanlan_v2.orchestration.worker import WorkerHandler, run_node

_OK_ARTIFACT = {NodeStatus.COMPLETED, NodeStatus.DEGRADED}


class RunResult(BaseModel):
    run_id: str
    node_runs: dict[str, NodeRun]
    status: Literal["completed", "partial", "failed"]


def _gate(node: PlanNode, node_runs: dict[str, NodeRun]) -> tuple[bool, bool, list[tuple[str, str, str]]]:
    """Return (blocked, degraded_inputs, satisfied[(inject_as, upstream_id, output_key)])."""
    blocked = False
    degraded = False
    satisfied: list[tuple[str, str, str]] = []
    for dep in node.dependencies:
        up = node_runs.get(dep.upstream_node_id)
        ok = up is not None and up.status in dep.accept_statuses
        if ok:
            satisfied.append((dep.inject_as, dep.upstream_node_id, dep.upstream_output_key))
        elif dep.policy == DependencyPolicy.BLOCK:
            blocked = True
        else:  # DEGRADE / SKIP — omit input, continue degraded
            degraded = True
    return blocked, degraded, satisfied


async def run_plan(plan: Plan, ctx: RunContext, *, pool: ArtifactPool, catalog: WorkerCatalog,
                   handlers: dict[str, WorkerHandler], budget: BudgetLedger,
                   now: Callable[[], datetime], max_concurrency: int | None = None) -> RunResult:
    cap = min(x for x in [plan.max_concurrency, ctx.budget.max_concurrency, max_concurrency]
              if x is not None)
    sem = asyncio.Semaphore(cap)
    node_runs: dict[str, NodeRun] = {}
    by_id = {n.id: n for n in plan.nodes}
    layers = _topo_layers(plan)

    for layer_index, layer in enumerate(layers):
        pool.freeze_input_snapshot(layer_index, context_snapshot_id=ctx.context_snapshot_id)
        staged_keys: set[tuple[str, str]] = set()

        async def _one(node: PlanNode) -> None:
            blocked, degraded_inputs, satisfied = _gate(node, node_runs)
            if blocked:
                node_runs[node.id] = NodeRun(
                    node_run_id=f"nr:{node.id}", run_id=ctx.run_id, plan_id=plan.id,
                    plan_digest=plan.plan_digest, node_id=node.id, worker_id=node.worker_id,
                    status=NodeStatus.BLOCKED, attempt_id=f"a:{node.id}", input_snapshot_hash="",
                    reason_code="dependency_failed", reason="a BLOCK dependency did not succeed",
                    started_at=now(), finished_at=now())
                return
            inputs = {inject: pool.committed_artifact(up, key).payload
                      for inject, up, key in satisfied
                      if pool.committed_artifact(up, key) is not None}
            spec = catalog.get(node.worker_id)
            reservation = budget.reserve(scope_type="node", scope_id=node.id,
                                         tokens=node.token_reservation, invocations=1)
            async with sem:
                nr, art = await run_node(node, spec, handlers[node.worker_id], inputs, ctx,
                                         degraded_inputs=degraded_inputs, now=now)
            budget.settle(reservation.reservation_id, actual_tokens=nr.input_tokens + nr.output_tokens,
                          actual_invocations=1)
            node_runs[node.id] = nr
            if art is not None and nr.status in _OK_ARTIFACT:
                pool.stage(art, layer_index=layer_index, idempotency_key=f"{node.id}:primary")
                staged_keys.add((node.id, "primary"))

        await asyncio.gather(*(_one(by_id[nid]) for nid in layer))  # layer sorted by id in _topo_layers
        pool.commit_layer(layer_index, node_run_ids=[f"nr:{nid}" for nid in layer],
                          expected_output_keys=staged_keys)

    sink_bad = any(node_runs[s].status in (NodeStatus.FAILED, NodeStatus.BLOCKED)
                   for s in plan.sink_node_ids)
    all_ok = all(nr.status == NodeStatus.COMPLETED for nr in node_runs.values())
    status: Literal["completed", "partial", "failed"] = (
        "failed" if sink_bad else ("completed" if all_ok else "partial"))
    return RunResult(run_id=ctx.run_id, node_runs=node_runs, status=status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_dag.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/dag.py tests/orchestration/test_dag.py
git commit -m "feat(orchestration): DAG runner with gating/barrier/budget (phase2)"
```

---

## Task 9: Preset adapter (engine DAGNode → PlanDraft) + 3-worker e2e

**Files:**
- Create: `guanlan_v2/orchestration/presets.py`
- Test: `tests/orchestration/test_presets.py`

**Interfaces:**
- Consumes: `financial_analyst.agent.orchestrator.DAGNode`, `spec.PlanDraft/PlanNode/Dependency`, `catalog.WorkerCatalog`, `enums.DependencyPolicy/PlanSource/DataMode`.
- Produces: `plandraft_from_dagnodes(nodes: list[DAGNode], *, run_id, request_id, as_of, mode, sink_node_ids, catalog, universe=(), phase="main", context_snapshot_id=None, source=PlanSource.PRESET) -> PlanDraft` — maps each `DAGNode`: `node_id = worker_id = agent.NAME`; for every `dep` in `agent`'s node, add a `Dependency(upstream_node_id=dep, artifact_slot=dep, upstream_output_key="primary", inject_as=dep, policy=DEGRADE if dep in soft_deps else BLOCK, accept_statuses={COMPLETED})`; `writes_slot = agent.NAME`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_presets.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.orchestrator import DAGNode
from guanlan_v2.orchestration.enums import (DataMode, DataBackend, Tier, ExecutionKind,
                                            NodeStatus, DependencyPolicy)
from guanlan_v2.orchestration.spec import WorkerSpec, ExecutionSpec
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.validate import freeze_plan
from guanlan_v2.orchestration.context import RunContext, DataContext, ClockSpec, RunBudget
from guanlan_v2.orchestration.eventstore import EventStore
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.worker import WorkerResult
from guanlan_v2.orchestration.dag import run_plan
from guanlan_v2.orchestration.presets import plandraft_from_dagnodes

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


class Doc(BaseModel):
    text: str


def _sub(name, deps=(), soft=(), input_keys=()):
    cls = type(f"Sub_{name}", (SubAgent,), {"NAME": name, "OUTPUT_SCHEMA": Doc,
                                            "_execute": lambda self, inputs: {"text": name}})
    return DAGNode(agent=cls(memory_root=Path(".")), deps=list(deps),
                   soft_deps=list(soft), input_keys=list(input_keys))


def test_mapping_marks_soft_dep_as_degrade():
    nodes = [_sub("a"), _sub("b", deps=["a"], soft=["a"], input_keys=["a"])]
    cat = WorkerCatalog([_w("a"), _w("b")])
    draft = plandraft_from_dagnodes(nodes, run_id="r", request_id="q", as_of=_t(),
                                    mode=DataMode.ONLINE, sink_node_ids=["b"], catalog=cat,
                                    context_snapshot_id="cs")
    b = next(n for n in draft.nodes if n.id == "b")
    assert b.dependencies[0].policy == DependencyPolicy.DEGRADE


def _w(wid):
    return WorkerSpec(id=wid, lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.WRITER,
                      execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
                      can_emit_decision=True, input_model="In", outputs={"primary": "Doc@1"},
                      supported_modes={DataMode.ONLINE})


def test_three_worker_static_plan_runs_end_to_end():
    nodes = [_sub("mkt"), _sub("news"), _sub("pm", deps=["mkt", "news"], input_keys=["mkt", "news"])]
    cat = WorkerCatalog([_w("mkt"), _w("news"), _w("pm")])
    draft = plandraft_from_dagnodes(nodes, run_id="r", request_id="q", as_of=_t(),
                                    mode=DataMode.ONLINE, sink_node_ids=["pm"], catalog=cat,
                                    context_snapshot_id="cs")
    plan = freeze_plan(draft, catalog=cat, budget_reservation_id="res", frozen_at=_t())

    class H:
        def __init__(self, tag): self.tag = tag
        async def execute(self, node, spec, inputs, ctx):
            return WorkerResult(payload={"text": f"{self.tag}<{sorted(inputs)}>"}, tool_call_count=1)

    clock = ClockSpec(as_of=_t(), timezone="UTC", calendar_id="XSHG", clock_version="1")
    dc = DataContext(as_of=_t(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
                     strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
                     source_config_digest="c", data_snapshot_id="s")
    ctx = RunContext(run_id="r", data=dc, context_snapshot_id="cs", memory_snapshot_hash="m",
                     budget=RunBudget(ledger_id="L", max_tokens=100000, max_llm_invocations=100,
                                      max_concurrency=4), cancellation_token_id="ct")
    pool = ArtifactPool("r", (lambda: (lambda r: (r.register("Doc", "1", Doc), r)[1])(SchemaRegistry()))(),
                        now=_t)  # registry with Doc
    budget = BudgetLedger(ctx.budget, run_id="r", now=_t)
    handlers = {"mkt": H("MKT"), "news": H("NEWS"), "pm": H("PM")}
    res = asyncio.run(run_plan(plan, ctx, pool=pool, catalog=cat, handlers=handlers, budget=budget, now=_t))
    assert res.status == "completed"
    assert res.node_runs["pm"].status == NodeStatus.COMPLETED
    assert "mkt" in pool.get_typed("pm", Doc).text and "news" in pool.get_typed("pm", Doc).text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_presets.py -v`
Expected: FAIL with `ModuleNotFoundError: ... presets`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/presets.py
from __future__ import annotations
from collections.abc import Iterable
from datetime import datetime
from financial_analyst.agent.orchestrator import DAGNode
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import DataMode, DependencyPolicy, NodeStatus, PlanSource
from guanlan_v2.orchestration.spec import Dependency, PlanDraft, PlanNode


def plandraft_from_dagnodes(nodes: list[DAGNode], *, run_id: str, request_id: str, as_of: datetime,
                            mode: DataMode, sink_node_ids: list[str], catalog: WorkerCatalog,
                            universe: Iterable[Symbol] = (), phase: str = "main",
                            context_snapshot_id: str | None = None,
                            source: PlanSource = PlanSource.PRESET) -> PlanDraft:
    plan_nodes: list[PlanNode] = []
    for dn in nodes:
        name = dn.agent.NAME
        soft = set(dn.soft_deps)
        deps = [Dependency(upstream_node_id=dep, artifact_slot=dep, upstream_output_key="primary",
                           inject_as=dep,
                           policy=DependencyPolicy.DEGRADE if dep in soft else DependencyPolicy.BLOCK,
                           accept_statuses={NodeStatus.COMPLETED})
                for dep in dn.deps]
        plan_nodes.append(PlanNode(id=name, worker_id=name, writes_slot=name, dependencies=deps))
    return PlanDraft(id=f"draft:{run_id}", run_id=run_id, request_id=request_id, phase=phase,
                     source=source, goal="static-preset", as_of=as_of, mode=mode,
                     context_snapshot_id=context_snapshot_id, universe=list(universe),
                     nodes=plan_nodes, sink_node_ids=sink_node_ids, catalog_version=catalog.version,
                     catalog_digest=catalog.digest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_presets.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/presets.py tests/orchestration/test_presets.py
git commit -m "feat(orchestration): engine DAGNode -> PlanDraft adapter + 3-worker e2e (phase2)"
```

---

## Task 10: Engine dependency-semantics equivalence test

**Files:**
- Test: `tests/orchestration/test_engine_equivalence.py`

**Interfaces:**
- Consumes: engine `Orchestrator`/`DAGNode`/`SubAgent`, plus the Phase 2 kernel (`plandraft_from_dagnodes`, `freeze_plan`, `run_plan`).
- Produces: no new source — a behavioral parity test proving the new runner reproduces the engine's soft/hard dependency outcomes on the same graph (the acceptance criterion for Phase 2). Two properties:
  1. **Hard-dep failure blocks downstream** in both: engine marks the downstream `SubAgentResult.ok is False` ("upstream dependency failed"); new runner marks it `NodeStatus.BLOCKED`.
  2. **Soft-dep failure degrades but runs** in both: engine still runs the downstream (soft dep only needs `done`); new runner marks it `NodeStatus.DEGRADED` and still produces its artifact.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_engine_equivalence.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.orchestrator import DAGNode, Orchestrator
from guanlan_v2.orchestration.enums import (DataMode, DataBackend, Tier, ExecutionKind, NodeStatus)
from guanlan_v2.orchestration.spec import WorkerSpec, ExecutionSpec
from guanlan_v2.orchestration.catalog import WorkerCatalog
from guanlan_v2.orchestration.validate import freeze_plan
from guanlan_v2.orchestration.context import RunContext, DataContext, ClockSpec, RunBudget
from guanlan_v2.orchestration.eventstore import EventStore
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.worker import WorkerResult
from guanlan_v2.orchestration.dag import run_plan
from guanlan_v2.orchestration.presets import plandraft_from_dagnodes

UTC = timezone.utc
def _t(): return datetime(2026, 7, 15, tzinfo=UTC)


class Doc(BaseModel):
    text: str


def _engine_node(name, deps=(), soft=(), fail=False):
    def _exec(self, inputs):
        if fail:
            raise RuntimeError("boom")
        return {"text": name}
    cls = type(f"E_{name}", (SubAgent,), {"NAME": name, "OUTPUT_SCHEMA": Doc, "_execute": _exec})
    return DAGNode(agent=cls(memory_root=Path(".")), deps=list(deps), soft_deps=list(soft),
                   input_keys=list(deps))


def _w(wid):
    return WorkerSpec(id=wid, lane="text", persona="p", system_prompt_ref="s.md", tier=Tier.WRITER,
                      execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="fast"),
                      can_emit_decision=True, input_model="In", outputs={"primary": "Doc@1"},
                      supported_modes={DataMode.ONLINE})


def _new_run(nodes, sinks, fail_map):
    cat = WorkerCatalog([_w(dn.agent.NAME) for dn in nodes])
    draft = plandraft_from_dagnodes(nodes, run_id="r", request_id="q", as_of=_t(),
                                    mode=DataMode.ONLINE, sink_node_ids=sinks, catalog=cat,
                                    context_snapshot_id="cs")
    plan = freeze_plan(draft, catalog=cat, budget_reservation_id="res", frozen_at=_t())

    class H:
        def __init__(self, name): self.name = name
        async def execute(self, node, spec, inputs, ctx):
            if fail_map.get(self.name):
                raise RuntimeError("boom")
            return WorkerResult(payload={"text": self.name}, tool_call_count=1)

    clock = ClockSpec(as_of=_t(), timezone="UTC", calendar_id="XSHG", clock_version="1")
    dc = DataContext(as_of=_t(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
                     strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
                     source_config_digest="c", data_snapshot_id="s")
    ctx = RunContext(run_id="r", data=dc, context_snapshot_id="cs", memory_snapshot_hash="m",
                     budget=RunBudget(ledger_id="L", max_tokens=100000, max_llm_invocations=100,
                                      max_concurrency=4), cancellation_token_id="ct")
    reg = SchemaRegistry(); reg.register("Doc", "1", Doc)
    pool = ArtifactPool("r", EventStore(), reg, now=_t)
    budget = BudgetLedger(ctx.budget, run_id="r", now=_t)
    handlers = {dn.agent.NAME: H(dn.agent.NAME) for dn in nodes}
    return asyncio.run(run_plan(plan, ctx, pool=pool, catalog=cat, handlers=handlers, budget=budget, now=_t)), pool


def test_hard_dep_failure_blocks_downstream_in_both():
    # engine
    eng = [_engine_node("a", fail=True), _engine_node("b", deps=["a"])]
    edone = asyncio.run(Orchestrator(eng).run({}))
    assert edone["a"].ok is False and edone["b"].ok is False
    # new kernel
    new = [_engine_node("a", fail=True), _engine_node("b", deps=["a"])]
    res, _ = _new_run(new, ["b"], {"a": True})
    assert res.node_runs["a"].status == NodeStatus.FAILED
    assert res.node_runs["b"].status == NodeStatus.BLOCKED


def test_soft_dep_failure_runs_downstream_in_both():
    # engine: b soft-depends on a; a fails but b still runs (soft dep only needs done)
    eng = [_engine_node("a", fail=True), _engine_node("b", deps=["a"], soft=["a"])]
    edone = asyncio.run(Orchestrator(eng).run({}))
    assert edone["a"].ok is False and edone["b"].ok is True
    # new kernel: b DEGRADED but produced
    new = [_engine_node("a", fail=True), _engine_node("b", deps=["a"], soft=["a"])]
    res, pool = _new_run(new, ["b"], {"a": True})
    assert res.node_runs["a"].status == NodeStatus.FAILED
    assert res.node_runs["b"].status == NodeStatus.DEGRADED
    assert pool.get_typed("b", Doc).text == "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_engine_equivalence.py -v`
Expected: FAIL — either import error (if a kernel piece is missing) or assertion mismatch. If the engine import fails because `financial_analyst` is not importable from repo root, prepend the engine to `PYTHONPATH`: run `pytest` with `PYTHONPATH=engine`. Document this in the test module docstring.

- [ ] **Step 3: No new implementation** — this task only asserts parity of Tasks 1–9. If a property fails, fix the offending kernel module (most likely `dag._gate` mapping of `DEGRADE`), re-run.

- [ ] **Step 4: Run the whole phase-2 suite**

Run: `pytest tests/orchestration/ -v` (with `PYTHONPATH=engine` if needed for the engine imports)
Expected: PASS (all phase-1 and phase-2 tests green)

- [ ] **Step 5: Commit**

```bash
git add tests/orchestration/test_engine_equivalence.py
git commit -m "test(orchestration): engine dependency-semantics equivalence (phase2)"
```

---

## Self-Review (completed by plan author)

**Spec coverage (§12 phase 2 + §0 decision 10 + §6.1 + §8 freeze validator):** budget ledger ✓(T1) · dual-cursor event store persist-then-publish + idempotency ✓(T2) · worker catalog ✓(T3) · staged→barrier ArtifactPool with canonical `artifact_seq` + late-stage/double-commit rejection ✓(T4) · strict freeze validator hard-gates ✓(T5) · `plan_digest` + budget binding ✓(T6) · worker executor typed Artifact + evidence-policy honesty classify ✓(T7) · DAG runner stable layers + bounded concurrency + BLOCK/DEGRADE gating + barrier + budget reservation ✓(T8) · engine `DAGNode → PlanDraft` adapter + 3-worker static Plan e2e ✓(T9) · old/new dependency-semantics equivalence ✓(T10). **Deferred to later phases (correctly out of scope):** durable (non-in-memory) event store + crash recovery replay (Phase persistence), retry/attempt re-run + late-stage rejection under real interruption (property tests, later), dynamic Planner (Phase 7), `optimize_existing`/holdout/sealed (Phase 4), Bootstrap Lane 0 (Phase 5), reducers execution + multi-write fold (folded into a later runner iteration; Phase 2 validator only *rejects* unreduced multi-write), condition-DSL, cancellation-token propagation via `RunCancelled` events, real `load_preset` YAML integration for `stock-deep-dive` (kept as a documented manual e2e; the hermetic equivalence test in T10 covers the semantics).

**Placeholder scan:** none — every code step is complete and runnable.

**Type consistency:** `WorkerResult`/`WorkerHandler`/`run_node` signatures identical across T7/T8/T9/T10; `NodeStatus` values (`COMPLETED/DEGRADED/BLOCKED/FAILED/INCOMPLETE`) used consistently; `ArtifactPool.__init__(run_id, event_store, registry, *, now)` matches every construction; `Dependency.accept_statuses={COMPLETED}` default matches `_gate`; `freeze_plan(...)` keyword args identical in T6/T8/T9/T10; engine `DAGNode(agent, deps, input_keys, soft_deps)` + `SubAgent(NAME, OUTPUT_SCHEMA, _execute)` used exactly as defined in `engine/financial_analyst/agent/{orchestrator,base}.py`.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
