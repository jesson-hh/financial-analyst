# Orchestration Phase 1 · 契约冻结、目录 ABI 与迁移表 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the digest, catalog/skill ABI, Plan freeze, and migration tasks. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.
>
> **Normative order:** Global Constraints → each task's Interfaces/Invariants → rejection tests → implementation sketch. A legacy code sketch never overrides an invariant or a failing test.

**Goal:** Freeze the versioned, typed, immutable and digest-stable cross-phase contracts for `guanlan_v2/orchestration/`, define the worker/prompt/skill/capability catalog ABI, and establish evidence-backed reversible legacy-schema adapters — pure offline models with zero runtime behavior.

**Architecture:** Pydantic v2 models organized by responsibility (`digest`/`enums`/`refs`/`schema_registry`/`data`/`schemas`/`context`/`events`/`catalog`/`spec`/`migration`). Every public semantic model inherits a strict base, carries `schema_version`, rejects unknown fields, is immutable when it represents a fact, and exposes deterministic semantic/audit digests. Catalog-owned content is referenced by stable `id + version + digest`; Plan never carries physical prompt/skill/tool paths. Legacy scalars and graph configs convert through source-schema-specific, versioned adapters that preserve exact raw values or canonical normalized config. No scheduler, no I/O, no LLM, no change to `workflow/executor.run_graph`.

**Tech Stack:** Python ≥3.11, Pydantic v2 (`pydantic.BaseModel`, `field_validator`, `model_validator`, `ConfigDict`), `hashlib.sha256`, `pytest`. All modules use `from __future__ import annotations`.

## Global Constraints

Derived from the spec (`docs/superpowers/specs/2026-07-15-orchestration-framework-design.md`) and tightened by the Phase 1 review. Every task's requirements normatively include this section.

- Every public semantic model inherits `ContractModel`/`DigestModel`, uses `ConfigDict(extra="forbid", strict=True)`, and declares a closed version such as `schema_version: Literal["1"] = "1"` (`PlanDraft`/`Artifact` use `Literal["2"]`). A payload cannot self-report another or arbitrary version.
- Immutable facts (`Plan`, snapshots, events, artifacts, migration results) are frozen; changes create a new object/event rather than mutating an approved record.
- Digests use versioned canonical JSON (`sha256+cjson-v1`) from `model_dump(mode="python")`: keys sorted, set/frozenset elements canonically sorted, times normalized to UTC, floats finite and `-0.0` normalized. An object's **own audit identity** (random id, wall-clock, provider response id, event/journal seq) does not enter its semantic digest. Catalog authorization identity is different: `ContentRef`/`CapabilityRef` logical `id + version + content_digest` all enter semantic/catalog digests. Only storage-assigned locators such as `PayloadRef.object_id` are projected out in favor of referenced content digest.
- Each schema explicitly declares semantic vs audit fields. Nested `DigestModel` values apply their own projection, so nested wall-clock fields cannot leak back into a parent semantic digest.
- Persisted `content_digest`/`audit_digest`/`plan_digest` values exclude themselves and are computed by a pure builder or verified on load; arbitrary digest strings are rejected.
- All datetimes are timezone-aware. A naive datetime is a validation error; canonical output is UTC.
- Reversible migration is source-schema-specific. `ResearchAction` (`buy/accumulate/hold/avoid/sell`) and `PositionAction` (`buy/add/hold/reduce/sell`) are different semantic domains; there is no generic `migrate_action(raw)`.
- Every migration result preserves `source_schema`, `adapter_version`, exact `raw`, and `mapping_status`. Known but non-equivalent values are explicitly `UNMAPPABLE`; unknown/invalid values raise; **no silent coercion**.
- Worker prompt/skill/guardrail/handler/capability refs use stable id/version/digest. MCP transport is not an authorization boundary. V1 WorkerSpec binds an ordered fixed skill set; Plan selects only `worker_id`.
- `DataResult` `OK`/`DEGRADED` must carry typed `data`; all other statuses must not carry consumable data; `DEGRADED` requires `coverage` + `degradation_reason`.
- Deferred Phase 5 redline: `RegimeReport` probability axes normalize independently to `1 ± 1e-8`, use only their own labels, and each probability lies in `[0,1]`.
- Deferred Phase 6 redline: runtime-only `TargetPortfolioIntent` remains `origin=LLM` / `authority=ADVISORY_ONLY` / `execution_scope=SHADOW_ONLY`; v1 A-share long-only weights sum to one without silent normalization.
- PIT: multi-row / multi-vintage data items subclass `PitRecord` and each carry `available_at`.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests with `pytest` from repo root `G:\guanlan-v2`.

## Phase 1 Boundary

Phase 1 is the orchestration system's **shared language, identity layer and compatibility border**. It builds:

- strict/versioned model bases and canonical digest rules;
- typed Request / Plan / Worker / Budget / Event / Artifact / Context / DataResult envelopes;
- schema registry and worker/content/capability catalog ABI;
- the 24-worker stable-ID/legacy ownership map (not 24 runnable workers);
- source-versioned legacy scalar/worker/YAML/static-graph adapters and real-sample round-trip fixtures.

Phase 1 does **not** build agent behavior. It does not finalize all prompt/SKILL.md prose, schedule a DAG, call an LLM/tool, fetch data, mutate memory, run an optimizer, expose sealed holdout, or consume a shadow portfolio. In particular:

- full Trial/Holdout contracts move to Phase 4, where they can be frozen with the complete lease state machine and sealed namespace;
- Lane 0 market/regime payload refinements remain gated by the Phase 5 Bootstrap consumer;
- final Proposal/Intent/DecisionSchedule details remain gated by the Phase 6 shadow consumer;
- Phase 1 does not populate all 24 final WorkerSpecs/SKILL.md playbooks. Phase 2 first completes the reviewed three-worker pilot, then adds the explicitly `compat.*` WorkerSpecs/content needed to mirror the full legacy `stock-deep-dive` graph for old/new execution-equivalence. Those compatibility entries preserve legacy behavior and are not falsely counted as the final redesigned 24. Later phases freeze each new worker before first use, with the final 24-worker catalog completed by Phase 8.

The catalog **data contract** is in Phase 1 because Plan binds `catalog_digest`; catalog loading/runtime and full worker content remain later.

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/__init__.py` | package marker, then reviewed public exports in Task 13 |
| `guanlan_v2/orchestration/digest.py` | canonical JSON + `content_digest` / `audit_digest` helpers |
| `guanlan_v2/orchestration/enums.py` | all shared enums |
| `guanlan_v2/orchestration/refs.py` | versioned schema/content/capability/payload refs + generic typed evidence ref |
| `guanlan_v2/orchestration/schema_registry.py` | `SchemaRegistry`: `"Type@version"` → model; validate payloads |
| `guanlan_v2/orchestration/data/__init__.py` | package marker (empty) |
| `guanlan_v2/orchestration/data/symbols.py` | `Symbol` / `InstrumentMeta` / `LimitRule` value objects |
| `guanlan_v2/orchestration/data/result.py` | `SourceAttempt` / `PitAudit` / `PitRecord` / `DataResult[T]` |
| `guanlan_v2/orchestration/schemas.py` | artifact/provenance + minimal static compatibility payloads |
| `guanlan_v2/orchestration/context.py` | `ClockSpec` / `DataContext` / `RunBudget` / `BudgetReservation` / `RunContext` / snapshots |
| `guanlan_v2/orchestration/events.py` | `RunEvent` / `EventCursor` / `LayerCommit` / `PlanApproval` |
| `guanlan_v2/orchestration/catalog.py` | `WorkerSpec` + prompt/skill/capability manifests + runnable catalog snapshot |
| `guanlan_v2/orchestration/spec.py` | `OrchestrationRequest` / `PlanNode` / `Dependency` / `PlanDraft` / `Plan` |
| `guanlan_v2/orchestration/migration.py` | reversible legacy scalar + worker/YAML/static-graph mapping adapters |
| `docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md` | real legacy field/source/semantic mapping evidence |
| `docs/superpowers/migrations/2026-07-15-orchestration-worker-map.md` | all 24 stable worker IDs and legacy ownership/status map |
| `tests/orchestration/fixtures/legacy_contract_samples.json` | real legacy samples for exact round-trip tests |
| `tests/orchestration/` | one test module per source module |

---

## Task 0: Inventory real legacy schemas and the 24-worker ownership map

**Files:**
- Create: `docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md`
- Create: `docs/superpowers/migrations/2026-07-15-orchestration-worker-map.md`
- Create: `tests/orchestration/fixtures/legacy_contract_samples.json`
- Test: `tests/orchestration/test_legacy_inventory.py`

**Purpose:** Task 12 must consume repository evidence rather than invent a universal action vocabulary or anonymous thresholds.

The legacy contract map has one row per source schema/field or legacy graph construct:

`source_schema | source_path | field/node | raw_type/domain | semantic_domain | target_schema | adapter/policy_version | mapping_basis | evidence | roundtrip_policy | unmapped_policy`

`source_schema` is module-qualified and versioned. Short names such as `report_output@1` are forbidden because stock and ETF already define different `ReportOutput` classes. `mapping_basis` is one of `authoritative_code | approved_policy | none`; an `approved_policy` row names its reviewed policy ID and approval evidence.

Inventory at minimum:

- `financial_analyst.agent.tier3.report_writer.ReportOutput@1.rating_overall`: strict integer `[-10,10]`.
- `financial_analyst.agent.tier3.report_writer.ReportOutput@1.action`: `buy/hold/sell/avoid/accumulate`; semantic domain `research_recommendation`.
- `financial_analyst.agent.etf.report_writer.EtfReportOutput@1.rating_overall/action`: inventory separately; never alias it to the stock class merely because field names overlap.
- `financial_analyst.backtest.decision.DecisionLeg@1.action`: `buy/add/hold/reduce/sell`; semantic domain `position_adjustment`.
- `financial_analyst.watch.models.WatchRec@1.action`: `buy/add/hold/reduce/sell`; semantic domain `position_adjustment`.
- `financial_analyst.agent.tier3.introspector.IntrospectionProposal@1.confidence`: `low/med/high`.
- every numeric/categorical sentiment producer and its authoritative scale/domain.
- `guanlan_v2.strategy.perspectives.market_cycle@1.stage`: `"冰点"`, `"分化"`, `"逼空"`, `"发酵"`, `"回踩/启动"`.
- current agent classes, preset/YAML nodes and static DAG edges that map to the 24 stable WorkerSpec IDs.

For every legacy YAML/static graph, record the source schema/format, normalized config object/digest, node identity, old hard/soft dependency meaning (accepted statuses + missing-output behavior), output/slot meaning and reviewed target mapping. For **every** declared `input_keys` entry, also inventory source kind (`base|upstream`), source upstream node when applicable, exact target kind/field, upstream output SchemaRef, projection/projection field, missing behavior, mapping basis/policy/evidence and unmappable reason. This is source evidence consumed by Task 12, not a late adapter guess. Task 0 evidence must not label a graph runnable when any worker, edge or input-key meaning is unresolved.

The worker map lists every design ID exactly once with:

`worker_id | lane | execution_kind | legacy_owner/source | legacy_config_schema/digest | proposed params/input/output ABI | can_emit_decision | status`

Status is `planned | contract_ready | runnable`. A planned row is not a runnable WorkerSpec.

Phase 2 legacy adapters, when one-to-one preservation cannot honestly use a final design ID, use a separate `compat.*` logical ID recorded in the legacy graph table. A `compat.*` row never satisfies or replaces one of the required 24 final-ID rows.

The required 24 IDs are:

- market: `market.factor`, `market.regime`, `market.rotation`;
- quant: `quant.factor`, `quant.model`, `quant.backtest`, `quant.fundamentals`, `quant.factor_miner`;
- price/volume: `pv.price_action`, `pv.technical`, `pv.microstructure`;
- text: `text.news`, `text.sentiment`, `text.research_report`, `text.policy`, `text.macro`;
- decision: `dec.bull`, `dec.bear`, `dec.research_mgr`, `dec.risk_debate`, `dec.pm`, `dec.trader`;
- cross-cutting: `x.quality_gate`, `x.number_critic`.

- [ ] **Step 1: Write the inventory completeness test**

The test must assert:

1. every action sample has explicit `source_schema` and `semantic_domain`;
2. raw `"hold"` under report and position sources belongs to different domains;
3. all 24 design worker IDs occur exactly once;
4. every documented source path exists;
5. no lossy or semantically uncertain mapping is labelled exact;
6. fixture domains match the human-reviewed table;
7. every source-schema key is module-qualified and unique;
8. every inventoried graph fixture records its normalized source config/digest and reviewed hard/soft dependency semantics;
9. every legacy `input_keys` entry has exactly one reviewed base/upstream inventory row with exact target, projection, missing behavior, upstream SchemaRef when applicable and mapping evidence/reason;
10. fixtures have explicit `scalars`, `workers` and `graphs` sections, and no incomplete worker/edge/input mapping is presented as a runnable Plan.

- [ ] **Step 2: Observe the missing-inventory failure**

Run: `pytest tests/orchestration/test_legacy_inventory.py -v`

Expected: FAIL because the reviewed evidence tables/fixture are absent or incomplete; a collection/import error is not sufficient evidence.

- [ ] **Step 3: Build the two evidence tables and real-sample fixture**

Do not add inferred mappings. If no authoritative mapping exists, record `UNMAPPABLE` with a reason.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_legacy_inventory.py -v`

Expected: PASS.

```bash
git add docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md docs/superpowers/migrations/2026-07-15-orchestration-worker-map.md tests/orchestration/fixtures/legacy_contract_samples.json tests/orchestration/test_legacy_inventory.py
git commit -m "docs(orchestration): inventory legacy contracts and worker ownership"
```

---

## Task 1: Strict contract base + canonical semantic/audit digests

**Files:**
- Create: `guanlan_v2/orchestration/__init__.py`
- Create: `guanlan_v2/orchestration/digest.py`
- Test: `tests/orchestration/__init__.py`, `tests/orchestration/test_digest.py`
- Create: `tests/orchestration/golden/digest_vectors_v1.json`

**Interfaces:**
- `ContractModel`: `ConfigDict(extra="forbid", strict=True)`; each public subclass declares its own closed `schema_version: Literal["N"]`.
- `DigestModel(ContractModel)`: immutable; explicit `SEMANTIC_EXCLUDE` and `AUDIT_EXCLUDE`; nested projections remain model-aware; exposes `semantic_digest()` and `audit_digest_value()`.
- `DigestHex`: strict 64-char lowercase SHA-256 hex.
- `canonical_json(data: Any, *, projection: Literal["semantic","audit"]="semantic") -> str`.
- `content_digest(data: Any) -> DigestHex`.
- `audit_digest(data: Any) -> DigestHex`.
- `verify_digest(data, expected, *, projection) -> None`.
- `UtcDateTime`, `FiniteFloat`, `NonNegativeInt`, `PositiveInt`, `NonEmptyStr`: shared strict types that reject naive/non-finite/bool/blank inputs as applicable.

Canonicalization version is `sha256+cjson-v1`:

1. project from Python-mode model fields, never `model_dump(mode="json")`;
2. nested DigestModel invokes its own semantic/audit projection;
3. dict keys are sorted and must be strings;
4. set/frozenset elements sort by their canonical element JSON; list/tuple order is preserved;
5. aware datetimes normalize to UTC and serialize with one reviewed representation;
6. reject naive datetime, NaN and infinities;
7. normalize `-0.0` to `0.0`;
8. serialize Enum by value;
9. reject unsupported semantic types;
10. exclude the object's own declared digest field to prevent self-reference.

- [ ] **Step 1: Write the failing tests**

Required tests:

- nested key-order independence;
- equal instants in UTC and `+08:00` hash equally;
- naive datetime rejection;
- NaN/`±Infinity` rejection;
- reviewed `-0.0` behavior;
- extra field rejection;
- wrong/arbitrary schema_version rejection;
- immutable DigestModel assignment rejection;
- nested audit timestamp changes audit digest but not parent semantic digest;
- nested business-field change changes semantic digest;
- declared digest mismatch rejection;
- subprocess test with two `PYTHONHASHSEED` values proves set-containing Plan fragments hash identically;
- every vector in `digest_vectors_v1.json` matches.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_digest.py -v`

Expected: FAIL because the package does not exist.

- [ ] **Step 3: Implement the strict base and recursive projections**

Implementation requirements:

- iterate actual model fields/getattr so nested model type information is retained;
- do not flatten nested models before semantic/audit projection;
- use `json.dumps(..., allow_nan=False, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` only after normalization;
- schema version participates in semantic digest;
- random IDs/wall-clock are excluded per model, not by a fragile global name blacklist;
- direct loading of persisted records verifies declared digests through a model-specific builder/validator.

- [ ] **Step 4: Run focused and cross-process tests**

Run: `pytest tests/orchestration/test_digest.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/__init__.py guanlan_v2/orchestration/digest.py tests/orchestration/__init__.py tests/orchestration/test_digest.py tests/orchestration/golden/digest_vectors_v1.json
git commit -m "feat(orchestration): strict contract base and canonical digest v1"
```
---

## Task 2: Shared enums

**Files:**
- Create: `guanlan_v2/orchestration/enums.py`
- Test: `tests/orchestration/test_enums.py`

**Interfaces:**
- Produces (all `str, Enum`): `PortfolioRating`, `ResearchAction`, `PositionAction`, `SentimentBand`, `RotationStage`, `LegacyMarketCycleStage`, `MappingStatus`, `Tier`, `Confidence`, `ExecutionKind`, `ToolCallRequirement`, `NodeStatus`, `ExperimentStatus`, `DependencyPolicy`, `PlanSource`, `ApprovalPolicy`, `ApprovalDecision`, `DataStatus`, `DataMode`, `DataBackend`.
- There is deliberately no generic `Action` enum. Research recommendation and position adjustment are different types.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_enums.py
from __future__ import annotations
from guanlan_v2.orchestration import enums as e


def test_portfolio_rating_five_values():
    assert [r.value for r in e.PortfolioRating] == ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def test_node_status_has_all_lifecycle_states():
    got = {s.value for s in e.NodeStatus}
    assert got == {"pending", "ready", "running", "completed", "degraded",
                   "incomplete", "failed", "timed_out", "blocked", "skipped", "cancelled"}


def test_data_mode_values():
    assert {m.value for m in e.DataMode} == {"online", "pit_replay"}


def test_enums_are_str_backed():
    assert e.Confidence.LOW == "low"
    assert e.DataStatus.OK == "ok"


def test_action_domains_are_distinct_and_complete():
    assert [x.value for x in e.ResearchAction] == ["buy", "accumulate", "hold", "avoid", "sell"]
    assert [x.value for x in e.PositionAction] == ["buy", "add", "hold", "reduce", "sell"]
    assert not hasattr(e, "Action")


def test_rotation_and_legacy_cycle_are_distinct():
    assert "分化" in {x.value for x in e.RotationStage}
    assert "分化" in {x.value for x in e.LegacyMarketCycleStage}
    assert e.RotationStage is not e.LegacyMarketCycleStage


def test_approval_decision_is_not_policy():
    assert {x.value for x in e.ApprovalDecision} == {"approved", "rejected"}
    assert e.ApprovalDecision is not e.ApprovalPolicy
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_enums.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/enums.py
from __future__ import annotations
from enum import Enum


class PortfolioRating(str, Enum):
    BUY = "Buy"; OVERWEIGHT = "Overweight"; HOLD = "Hold"
    UNDERWEIGHT = "Underweight"; SELL = "Sell"


class ResearchAction(str, Enum):
    BUY = "buy"; ACCUMULATE = "accumulate"; HOLD = "hold"; AVOID = "avoid"; SELL = "sell"


class PositionAction(str, Enum):
    BUY = "buy"; ADD = "add"; HOLD = "hold"; REDUCE = "reduce"; SELL = "sell"


class SentimentBand(str, Enum):
    BULLISH = "Bullish"; MILDLY_BULLISH = "Mildly Bullish"; NEUTRAL = "Neutral"
    MIXED = "Mixed"; MILDLY_BEARISH = "Mildly Bearish"; BEARISH = "Bearish"


class Tier(str, Enum):
    READER = "reader"; CRITIC = "critic"; WRITER = "writer"


class Confidence(str, Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"


class RotationStage(str, Enum):
    START = "启动"; SPREAD = "扩散"; DIVERGENCE = "分化"; EBB = "退潮"; UNKNOWN = "unknown"


class LegacyMarketCycleStage(str, Enum):
    FREEZE = "冰点"; DIVERGENCE = "分化"; SQUEEZE = "逼空"
    FERMENT = "发酵"; PULLBACK_START = "回踩/启动"


class MappingStatus(str, Enum):
    MAPPED = "mapped"; UNMAPPABLE = "unmappable"


class ExecutionKind(str, Enum):
    LLM = "llm"; DETERMINISTIC = "deterministic"


class ToolCallRequirement(str, Enum):
    FORBIDDEN = "forbidden"; OPTIONAL = "optional"; REQUIRED = "required"


class NodeStatus(str, Enum):
    PENDING = "pending"; READY = "ready"; RUNNING = "running"; COMPLETED = "completed"
    DEGRADED = "degraded"; INCOMPLETE = "incomplete"; FAILED = "failed"
    TIMED_OUT = "timed_out"; BLOCKED = "blocked"; SKIPPED = "skipped"; CANCELLED = "cancelled"


class ExperimentStatus(str, Enum):
    RUNNING = "running"; WAITING_FOR_MATURITY = "waiting_for_maturity"
    PASSED_VALIDATION = "passed_validation"; SEALED_EVALUATING = "sealed_evaluating"
    COMPLETED = "completed"; FAILED = "failed"


class DependencyPolicy(str, Enum):
    BLOCK = "block"; DEGRADE = "degrade"; SKIP = "skip"


class PlanSource(str, Enum):
    BOOTSTRAP = "bootstrap"; DYNAMIC = "dynamic"; PRESET = "preset"; PRESET_FALLBACK = "preset_fallback"


class ApprovalPolicy(str, Enum):
    REQUIRED = "required"; AUTO = "auto"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"; REJECTED = "rejected"


class DataStatus(str, Enum):
    OK = "ok"; NO_DATA = "no_data"; STALE = "stale"; UNAVAILABLE = "unavailable"; DEGRADED = "degraded"


class DataMode(str, Enum):
    ONLINE = "online"; PIT_REPLAY = "pit_replay"


class DataBackend(str, Enum):
    LIVE = "live"; PIT_STORE = "pit_store"; CACHE = "cache"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_enums.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/enums.py tests/orchestration/test_enums.py
git commit -m "feat(orchestration): shared enums (phase1)"
```

---

## Task 3: Versioned refs + sealable schema registry

**Files:**
- Create: `guanlan_v2/orchestration/refs.py`
- Create: `guanlan_v2/orchestration/schema_registry.py`
- Test: `tests/orchestration/test_refs.py`
- Test: `tests/orchestration/test_schema_registry.py`

**Interfaces:**
- `LogicalId`: strict lowercase logical key matching `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`.
- `SchemaRef(name, version)`, canonical key `name@version`.
- `ContentRef(id, version, content_digest)`.
- `CapabilityRef(id, version, content_digest)`; transport exists only on the corresponding manifest entry, so it cannot be double-written inconsistently.
- `PayloadRef(namespace, object_id, content_digest)`, namespace `main | sealed | review | audit`. `audit` is non-public, never a valid ordinary Artifact/InputSnapshot/public-event source, and exists so later phases can persist typed refusal details without mislabeling them as main data.
- `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)`: generic immutable typed evidence reference. Its semantic projection includes the exact SchemaRef plus payload namespace/content digest and excludes only `payload_ref.object_id`; owners that expose public/runtime evidence additionally require `namespace="main"`.
- `SchemaManifestEntry(schema_ref, json_schema_digest)`.
- `SchemaRegistry.register(model)`, `resolve(ref)`, `validate_payload(ref, payload)`, `manifest()`, `seal()`, `registry_digest`.

Logical refs never contain a physical file path. Content/capability logical IDs, versions and expected digests are semantic authorization identity; a later catalog resolver owns physical storage.

`PayloadRef.object_id` is an audit/dereference locator. A parent semantic digest projects a payload ref as `namespace + content_digest` and excludes the random `object_id`; changing referenced payload content therefore changes the parent semantic digest without making it depend on a storage-assigned ID. `TypedPayloadRef` is the only generic public wrapper when both the schema identity and payload locator are needed for deterministic replay.

Registry invariants:

- registration reads model name and declared `schema_version`;
- registering under a different version is impossible;
- generated JSON Schema makes the closed `schema_version` a `const`, and registry payload validation rejects a payload whose self-declared version differs from the resolved `SchemaRef`;
- same key/model is idempotent; conflicting model fails;
- unknown refs and extra payload fields fail;
- namespace is a closed value; `audit` refs are excluded from all Phase 1 main/public visibility paths just like `sealed`/`review`, while retaining distinct policy semantics;
- manifest entries sort by schema key and include canonical JSON-schema digest;
- registry digest is independent of registration order;
- after `seal()`, every registration attempt fails.

- [ ] **Step 1: Write failing ref/registry tests**

Required tests:

- ref syntax and digest format rejection;
- physical path-like ContentRef IDs rejected by the reviewed ID grammar;
- register/resolve/validate;
- unknown schema rejection;
- model/version mismatch rejection;
- payload/schema-version mismatch rejection and JSON-Schema `const` assertion;
- conflicting registration rejection;
- payload extra field rejection;
- closed payload-namespace matrix, including `audit` rejection from main/public visibility paths;
- `TypedPayloadRef` SchemaRef sensitivity, object-ID relocation invariance and main-only checks at public evidence consumers;
- reverse registration order produces the same manifest/digest;
- changed model JSON schema changes registry digest;
- mutation after seal rejected.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_refs.py tests/orchestration/test_schema_registry.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement refs and registry**

Do not populate a global registry at import time. Task 13 creates the reviewed, sealed `default_registry()`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/orchestration/test_refs.py tests/orchestration/test_schema_registry.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/refs.py guanlan_v2/orchestration/schema_registry.py tests/orchestration/test_refs.py tests/orchestration/test_schema_registry.py
git commit -m "feat(orchestration): add versioned refs and sealed schema registry"
```
---

## Task 4: Symbol value objects

**Files:**
- Create: `guanlan_v2/orchestration/data/__init__.py` (empty), `guanlan_v2/orchestration/data/symbols.py`
- Test: `tests/orchestration/test_symbols.py`

**Interfaces:**
- Consumes: Task 1 `DigestModel`, `FiniteFloat`, `UtcDateTime` and strict shared validators.
- Produces:
  - `Symbol(DigestModel)`: immutable; `code`, `exchange: Literal["SH","SZ","BJ"]`, `board: Literal["main","star","chinext","bj"]`; props `dotted` and `engine_code`; validates code shape and exchange/board compatibility.
  - `InstrumentMeta(DigestModel)`: `symbol`, explicit unknown `is_st`, and timezone-aware listing/metadata availability.
  - `LimitRule(DigestModel)`: finite `pct` in `(0,1]` or explicit `None`, reason and rule version.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_symbols.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.data.symbols import Symbol, InstrumentMeta, LimitRule


def test_symbol_dotted_and_engine_code():
    s = Symbol(code="600519", exchange="SH", board="main")
    assert s.dotted == "600519.SH"
    assert s.engine_code == "SH600519"


def test_symbol_rejects_non_six_digit_code():
    with pytest.raises(ValueError):
        Symbol(code="60051", exchange="SH", board="main")
    with pytest.raises(ValueError):
        Symbol(code="60051X", exchange="SH", board="main")


def test_symbol_is_frozen():
    s = Symbol(code="600519", exchange="SH", board="main")
    with pytest.raises(Exception):
        s.code = "000001"  # frozen


def test_instrument_meta_is_st_unknown_defaults_none():
    m = InstrumentMeta(symbol=Symbol(code="600519", exchange="SH", board="main"))
    assert m.is_st is None  # cannot infer from code; unknown must be explicit


def test_limit_rule_allows_none_pct():
    r = LimitRule(pct=None, reason="rule unknown", rule_version="v0")
    assert r.pct is None


def test_symbol_rejects_exchange_board_mismatch():
    with pytest.raises(ValueError):
        Symbol(code="688001", exchange="SZ", board="star")


def test_limit_rule_rejects_invalid_pct():
    with pytest.raises(ValueError):
        LimitRule(pct=-0.1, reason="bad", rule_version="v1")
    with pytest.raises(ValueError):
        LimitRule(pct=float("inf"), reason="bad", rule_version="v1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_symbols.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/symbols.py
from __future__ import annotations
import re
from typing import Literal
from pydantic import Field, field_validator, model_validator
from guanlan_v2.orchestration.digest import DigestModel, FiniteFloat, UtcDateTime

_CODE_RE = re.compile(r"^[0-9]{6}$")


class Symbol(DigestModel):
    code: str
    exchange: Literal["SH", "SZ", "BJ"]
    board: Literal["main", "star", "chinext", "bj"]

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError(f"code must be 6 digits, got {v!r}")
        return v

    @model_validator(mode="after")
    def _exchange_board(self) -> "Symbol":
        allowed = {"SH": {"main", "star"}, "SZ": {"main", "chinext"}, "BJ": {"bj"}}
        if self.board not in allowed[self.exchange]:
            raise ValueError(f"board={self.board} incompatible with exchange={self.exchange}")
        return self

    @property
    def dotted(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def engine_code(self) -> str:
        return f"{self.exchange}{self.code}"


class InstrumentMeta(DigestModel):
    symbol: Symbol
    is_st: bool | None = None                  # 不能从代码纯语法推断;unknown 显形
    listed_at: UtcDateTime | None = None
    metadata_available_at: UtcDateTime | None = None


class LimitRule(DigestModel):
    pct: FiniteFloat | None = Field(default=None, gt=0, le=1)
    reason: str
    rule_version: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_symbols.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/__init__.py guanlan_v2/orchestration/data/symbols.py tests/orchestration/test_symbols.py
git commit -m "feat(orchestration): Symbol/InstrumentMeta/LimitRule value objects (phase1)"
```

---

## Task 5: Typed DataResult + PIT records

**Files:**
- Create: `guanlan_v2/orchestration/data/result.py`
- Test: `tests/orchestration/test_data_result.py`

**Interfaces:**
- `SourceAttempt`: source/configuration/outcome/fallback plus audit-only start/finish wall-clock.
- `PitAudit`: mode/as-of/row counts/guard result/latest availability.
- `PitRecord`: immutable base requiring `available_at`, `ingested_at`, payload content digest; optional effective time/revision.
- `DataResult[T]`: strict typed status envelope.

**Required invariants:**

- OK/DEGRADED require typed data and verified data content digest.
- all other statuses carry no consumable data and no data content digest.
- DEGRADED requires coverage in `[0,1]` and non-empty reason.
- attempt order/outcome is semantic; attempt wall-clock is audit-only.
- PIT row counts are strict non-negative integers and internally coherent.
- PIT replay with future/missing-availability rows cannot claim `passed`.
- all datetimes use the shared timezone-aware type.
- a concrete multi-row payload schema uses a registered PitRecord subtype; a generic dict is not proof of PIT compliance.

Provide a pure `DataResult.build(...)` (or equivalent) that computes declared content/audit digests. Direct loading of persisted data rejects mismatched digests.

- [ ] **Step 1: Write failing tests**

Required tests:

1. complete status/data matrix;
2. degraded coverage/reason matrix;
3. declared content/audit digest mismatch rejection;
4. nested attempt wall-clock changes audit but not semantic digest;
5. attempt outcome/order changes semantic digest;
6. missing PitRecord availability rejection;
7. negative/incoherent PIT count rejection;
8. naive datetime, non-finite coverage, bool-as-number and extra-field rejection;
9. immutable records.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_data_result.py -v`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement models, projections and pure builder**

Use valid computed digests in tests; do not use short placeholders such as `"c"` or `"a"`.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_data_result.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/data/result.py tests/orchestration/test_data_result.py
git commit -m "feat(orchestration): freeze typed DataResult and PIT contracts"
```
---

## Task 6: Artifact / provenance / number-anchor / NodeRun

**Files:**
- Create: `guanlan_v2/orchestration/schemas.py`
- Test: `tests/orchestration/test_artifact.py`
- Test: `tests/orchestration/test_node_run.py`

**Interfaces:**
- Produces `ArtifactRef`, `ToolCallRecord`, `Provenance`, `NumberAnchor`, `Artifact`, `ArtifactRelation`, `NodeRun`.
- Uses `SchemaRef`, `PayloadRef` and Task 3 `TypedPayloadRef` for payload type/version, dereference identity and reproducibility.

Artifact has three distinct digest meanings:

1. `content_digest`: typed payload plus payload schema ref;
2. `reproducibility_digest`: stable provenance—plan/code/model config/prompt/skills/capabilities/input artifact+data digests and deterministic tool request/result digests;
3. `audit_digest`: IDs, wall-clock, provider response ID and other volatile facts.

Do not exclude the whole Provenance object from reproducibility. Changing only provider response ID changes audit digest; changing prompt/skill/model config/data input changes reproducibility digest; changing payload changes content digest.

**Validation:**

- `validate_artifact_payload(artifact, registry)` resolves SchemaRef and validates payload.
- persisted content/reproducibility/audit digests are computed by a pure builder or verified on load.
- `rendered_from_payload_digest` must bind the rendered source payload.
- `Provenance.execution_evidence_refs: tuple[TypedPayloadRef, ...]` is immutable, deterministically ordered and duplicate-free. It records persisted runtime evidence that is not already represented by an Artifact/InputSnapshot/ToolCallRecord field (for example a later phase's prompt-assembly record); every ref must be `main`, registry-resolvable and content-matching. Full SchemaRef + namespace/content enter reproducibility, while object IDs enter audit/dereference identity only.
- `NodeRun.execution_evidence_refs` records the same pre-output evidence refs even for INCOMPLETE/FAILED/TIMED_OUT/CANCELLED attempts. On COMPLETED/DEGRADED, the produced Artifact Provenance must contain the exact same tuple; evidence cannot disappear because execution produced no Artifact.
- ArtifactRef random artifact ID is audit identity; schema/producer/slot/output/content digest form its semantic projection.
- NumberAnchor value is finite; `is_unsourced` is explicit and never silently treated as sourced.
- NodeRun counters are non-negative; attempt starts at one.
- COMPLETED requires declared outputs; FAILED/TIMED_OUT/CANCELLED requires a reason code.

- [ ] **Step 1: Write failing tests**

Required tests:

1. content/reproducibility/audit digest layers vary independently;
2. random artifact/run IDs do not alter content equality;
3. prompt/skill digest changes reproducibility digest;
4. provider response ID/wall-clock changes audit digest only;
5. payload schema/version and declared digest mismatch rejection;
6. rendered digest mismatch rejection;
7. execution-evidence SchemaRef/namespace/content mismatch and duplicate/order rejection; object-ID-only relocation changes audit but not reproducibility;
8. successful Artifact/NodeRun evidence-ref equality and failed/no-Artifact NodeRun evidence retention;
9. non-finite NumberAnchor rejection;
10. NodeRun status/counter/attempt matrix;
11. all models reject extra fields and mutation.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py -v`

Expected: FAIL with missing models/module.

- [ ] **Step 3: Implement models, builders and pure registry validator**

No ArtifactPool, persistence, model/tool call or event emission is implemented here.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/schemas.py tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py
git commit -m "feat(orchestration): freeze artifact provenance and node-run contracts"
```
---

## Task 7: Minimal Phase 2 compatibility payloads + downstream contract gates

**Files:**
- Modify: `guanlan_v2/orchestration/schemas.py`
- Test: `tests/orchestration/test_payloads.py`

**Phase 1 payloads:**
- `ResearchPlan`: five-level `PortfolioRating`, rationale and strategic actions.
- `PortfolioDecision`: rating, executive summary, thesis, finite optional price target and time horizon.
- `SentimentReport`: `SentimentBand`, finite score in `[0,10]`, `Confidence`, narrative.

These are the minimal typed outputs needed to exercise the Phase 2 static compatibility path and legacy rating/sentiment adapters.

Every payload:

- inherits strict immutable DigestModel;
- carries schema version;
- rejects unknown fields, naive datetime and non-finite numbers;
- contains no runtime-generated authority/identity fields.

**Explicit downstream gates — do not create placeholder Phase 1 models:**

- Phase 5 freezes `MarketFactorValue/MarketFactorReport/RegimeReport/RealizedRegime/RotationReport` with the Bootstrap consumer, PIT coverage and experience-case tests.
- Phase 6 freezes `TargetPosition/PortfolioTargetProposal/TargetPortfolioIntent/DecisionSchedule` with the shadow consumer, weights, trading clock, idempotency and broker tests.
- Phase 8 freezes `DebateMessage` with bounded Lane D orchestration and immutable round/turn reconstruction.

- [ ] **Step 1: Write failing minimal payload tests**

Required tests:

1. exact enum serialization;
2. price target rejects bool, NaN, infinity and non-positive values;
3. sentiment score bounds and finite checks;
4. extra fields rejected;
5. models immutable;

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_payloads.py -v`

Expected: FAIL with missing models/module.

- [ ] **Step 3: Implement only the three Phase 1 payloads**

Do not append deferred classes “for completeness”; their consumer phases own the final invariants.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_payloads.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/schemas.py tests/orchestration/test_payloads.py
git commit -m "feat(orchestration): add minimal static compatibility payloads"
```
---

## Task 8: Context, budget, clock and immutable snapshots

**Files:**
- Create: `guanlan_v2/orchestration/context.py`
- Test: `tests/orchestration/test_context.py`
- Test: `tests/orchestration/test_budget.py`

**Interfaces:**
- `ClockSpec`, `DataContext`.
- `RunBudget`, `BudgetReservation`.
- `MemoryRecordRef`, `EmptyMemorySnapshot`, `EmptyMemorySelection`, `ContextSnapshot`, `InputSnapshot`, `RunContext`.

**Closed `DataContext@1` field matrix:**

- `schema_version: Literal["1"]`, timezone-aware `as_of`, `clock: ClockSpec`, `mode: DataMode`, `backend: DataBackend`, strict `strict_pit: bool`, and `calendar_id`;
- `resolved_vendor_chains: dict[LogicalId, tuple[LogicalId, ...]]`, `source_config_digest`, `source_registry_digest` and `routing_snapshot_digest` are semantic and immutable. An empty data setup uses reviewed empty registry/config/routing digests, never blank placeholders;
- `data_snapshot_id: NonEmptyStr` is a storage/audit locator, while `data_snapshot_content_digest: DigestHex` is the semantic snapshot identity. `vintage_manifest_digest: DigestHex | None` is semantic;
- `DataContext` contains no `PitGuard`, source handler, credential, cache path or mutable registry object. Later phases construct guards/services from this frozen value;
- `ONLINE` and `PIT_REPLAY` both require a real snapshot locator and content digest. `PIT_REPLAY` additionally requires `strict_pit=True`, a non-LIVE backend and `vintage_manifest_digest`; `ONLINE` may use a frozen capture-root digest without claiming live source isolation;
- its semantic/content projection includes clock/as-of/mode/backend/strictness/calendar, resolved chains and every config/registry/routing/snapshot/vintage digest, but excludes the storage-only `data_snapshot_id` and builder wall-clock.

**Closed memory-reference/snapshot ABI:**

- `MemoryRecordRef(schema_version: Literal["1"], record_id: LogicalId, revision_id: NonEmptyStr, available_at: UtcDateTime, content_digest: DigestHex)` is a semantic reference to one exact accepted memory revision. It contains no filesystem path, mutable score, `PayloadRef`, review writer or storage handle; later phases map it to stored evidence without extending this ABI;
- `EmptyMemorySnapshot@1` and `EmptyMemorySelection@1` are strict immutable Phase 1 compatibility facts with provably empty record tuples and verified canonical content digests; the selection binds the empty snapshot digest. `build_empty_memory_binding()` is the sole pure builder and returns both models/digests for a runtime to persist—never random placeholder hashes;
- `ContextSnapshot.memory_snapshot_id: NonEmptyStr` is an audit/dereference locator. `memory_snapshot_hash: DigestHex` and `past_context_hash: DigestHex` are required semantic identities; the former binds the complete frozen visible-memory universe and the latter binds the reviewed/query-specific selection used to construct context. Required `memory_selection_ref: PayloadRef` uses `namespace="main"` and `content_digest == past_context_hash`;
- `ContextSnapshot.memory_session_id: LogicalId | None` is the service-bound semantic session scope for memory. It is populated only from an authenticated request/session authority before Plan validation, never from Planner, Worker or model output. A Phase 3 context selection and every node-specific selection must use this exact scope or a stricter non-session subset;
- before the Phase 3 memory facade exists, a no-memory runtime persists the two canonical empty models in `main`, uses the empty snapshot `PayloadRef.object_id/content_digest` as `memory_snapshot_id/hash`, uses the exact empty selection ref/hash and sets `memory_session_id=None`. Task 9 substitutes its non-empty schemas through the same generic fields. Blank/random locator/hash pairs are invalid;
- relocating byte-identical memory snapshot/selection evidence changes only `memory_snapshot_id` or `memory_selection_ref.object_id`; it cannot change `ContextSnapshot.content_digest`. Changing `memory_snapshot_hash`, selection namespace/content, `past_context_hash` or `memory_session_id` must change that digest and therefore the candidate Plan digest;
- `InputSnapshot.memory_record_refs` is an immutable tuple canonically ordered by `(record_id, revision_id, content_digest)`, duplicate-free, and included in the snapshot semantic projection as full refs (including `available_at`). Two refs with the same `(record_id, revision_id)` but different availability/content are a conflict, not two records. A node cannot receive a later/live record by mutating the already-frozen snapshot;
- `verify_memory_record_ref(ref, *, record_id, revision_id, available_at, content_digest) -> None` is the pure Phase 1 identity checker. A later memory facade must call it for each selected payload; missing, naive or mismatched availability is not repaired here.

**DataContext/snapshot invariants:**

- context and clock as-of represent the same instant and calendar ID matches;
- PIT_REPLAY requires strict PIT, data snapshot ID and vintage manifest digest;
- every datetime is timezone-aware and canonicalized to UTC;
- contexts/snapshots are immutable;
- random IDs/freeze wall-clock are audit; content refs, hashes, modes and PIT identity are semantic;
- persisted snapshot content digest excludes itself and is computed/verified by a pure builder.
- `ContextSnapshot` embeds/references the exact `DataContext` semantic digest; changing source registry, route, chain, config, snapshot content or vintage changes ContextSnapshot content and therefore the candidate Plan digest. Relocating the same snapshot under another `data_snapshot_id` does not.
- `ContextSnapshot` and `InputSnapshot` apply the memory locator/hash/ref rules above; storage IDs remain audit-only while memory hashes and exact record revisions remain semantic.

**Budget invariants:**

- maxima/reserved/actual values are strict non-negative integers; bool rejected;
- `max_concurrency >= 1`;
- `BudgetReservation` binds `request_id`, `candidate_plan_digest`, ledger identity and the exact requested token/invocation/concurrency amounts; its random reservation ID and wall-clock remain audit fields;
- reserved totals do not exceed maxima;
- actual use does not exceed its reservation in Phase 1;
- `status="reserved"` has no settled time;
- `settled/released` requires settled time;
- all scopes carry the same ledger identity contract; no ledger mutation behavior is implemented.

- [ ] **Step 1: Write failing context/budget tests**

Required tests:

1. online and PIT mode matrices;
2. equal-instant clock/context handling and naive rejection;
3. snapshot mutation and declared digest mismatch rejection;
4. complete DataContext field/mode/backend matrix, including empty reviewed routing, ONLINE capture-root and strict PIT replay;
5. data- or memory-snapshot-ID-only relocation leaves semantic/context digest stable, while source/routing/snapshot-content/vintage/memory-snapshot/past-context hash or memory-session scope changes alter it;
6. strict `MemoryRecordRef` field/time/digest validation, canonical InputSnapshot ordering, duplicate rejection, same-record/revision conflict rejection and full-ref semantic sensitivity;
7. canonical empty-memory builder/digests, exact persisted locator/ref binding, `memory_selection_ref` namespace/content-digest matrix, pre-facade `memory_session_id=None`, rejection of caller/worker session widening and record payload versus `MemoryRecordRef` identity verification through the exported helper;
8. negative/bool budget rejection;
9. reserved>max and actual>reserved rejection;
10. zero concurrency rejection;
11. reservation status/time matrix;
12. reservation/request/candidate-plan-digest binding and declared-digest rejection;
13. extra-field rejection.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_context.py tests/orchestration/test_budget.py -v`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement immutable models and pure snapshot builders**

No BudgetLedger, reservation mutation or snapshot persistence is implemented.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_context.py tests/orchestration/test_budget.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/context.py tests/orchestration/test_context.py tests/orchestration/test_budget.py
git commit -m "feat(orchestration): freeze context budget and snapshot contracts"
```
---

## Task 9: Run event and visibility contracts

**Files:**
- Create: `guanlan_v2/orchestration/events.py`
- Test: `tests/orchestration/test_events.py`

**Interfaces:**
- `RunEvent`, `EventCursor`, `CommittedArtifactRef`, `LayerCommit`, `PlanApproval`.
- payload uses `SchemaRef + PayloadRef`, not untyped type/version/ref strings.

**Required invariants:**

- journal sequence is positive;
- visible sequence is positive when present;
- ArtifactStaged always has `visible_seq=None`;
- LayerCommitted is a public visibility boundary and requires visible sequence;
- main/public event cannot reference `sealed`, `review` or `audit` payload namespace;
- `sealed`/`review`/`audit` payload or event cannot masquerade as main-public; audit-only refusal details never receive a public RunEvent;
- committed artifact sequences are positive, unique and sorted;
- `PlanApproval` binds one request, `ApprovalDecision`, actor/reason and the exact pre-freeze `candidate_plan_digest`; `REJECTED` can never satisfy Plan freeze;
- event semantic digest excludes event ID, sequences and wall-clock, but includes event type, partition, plan digest, payload schema/ref and idempotency identity;
- Trial/Holdout event types remain deferred with Task 11.

- [ ] **Step 1: Write failing event tests**

Required rejection tests explicitly construct:

1. ArtifactStaged with visible sequence;
2. LayerCommitted without visible sequence;
3. main event pointing to sealed/review/audit payload;
4. zero/negative sequence;
5. duplicate/unsorted committed artifact sequence;
6. mismatched PlanApproval request/candidate digest and rejected approval presented as freeze authority.

Also test wall-clock/sequence and `PayloadRef.object_id` changes affect audit only, while payload content digest/type/version/namespace changes semantic digest.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_events.py -v`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement immutable event models and validators**

No journal, subscription or barrier runtime is implemented.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_events.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/events.py tests/orchestration/test_events.py
git commit -m "feat(orchestration): freeze staged and public event contracts"
```
---

## Task 9A: Prompt / skill / capability refs and runnable Worker catalog ABI

**Files:**
- Create: `guanlan_v2/orchestration/catalog.py`
- Test: `tests/orchestration/test_catalog.py`

**Interfaces:**
- Consumes: `refs.ContentRef/CapabilityRef/SchemaRef`, shared enums and `digest.DigestModel`.
- Produces: `ContentManifestEntry`, `SkillManifest`, `CapabilityDescriptor`, `CapabilityManifestEntry`, `ResolvedTextMaterial`, `ResolvedCapabilityMaterial`, `SkillBinding`, `InputBinding`, `OutputBinding`, `ExecutionSpec`, `EvidencePolicy`, `CompatibilityBinding`, `WorkerSpec`, `WorkerCatalogSnapshot`, plus pure material-aware builder and snapshot validator functions.

The field-level v1 ABI is fixed here so implementation does not have to invent it:

- `LogicalId` comes from `refs.py`; names/descriptions are trimmed `NonEmptyStr`s. `source_identity` and every `borrowed_from` item are `LogicalId`s, never paths;
- `ContentManifestEntry(ref, kind, name, description, source_identity)`, where `kind: Literal["prompt","guardrail","handler","condition","reducer","stop_condition","gate_metric"]`;
- `SkillManifest(ref, name, summary, format_version, perfect_for, not_ideal_for, critical_data_source_heading, source_identity)`, where `format_version: Literal["skill-v1"]`, `summary: NonEmptyStr` is one logical line, both trigger tuples are non-empty/ordered/duplicate-free `tuple[NonEmptyStr,...]`, and `critical_data_source_heading: Literal["⚠️ CRITICAL: Data Source Priority"]`;
- skill-v1 frontmatter has exactly the keys `name` and `description`; duplicate/unknown keys fail. Parsed `description` has exactly three LF-separated lines and no terminal LF: line 1 is `summary`; line 2 is `Perfect for: ` plus Task 1 canonical JSON of the ordered `perfect_for` string array; line 3 is `Not ideal for: ` plus canonical JSON of `not_ideal_for`. Canonical JSON defines escaping and tuple order is semantic. The first body heading after frontmatter must be `## ⚠️ CRITICAL: Data Source Priority` followed by a non-empty ordered source-priority list. Phase 1 validates this machine-readable envelope but does not prescribe a universal six-section body or finalize any worker's playbook prose;
- `CapabilityManifestEntry(ref, capability_kind, transport)`, where `capability_kind: Literal["tool","mcp_method","data_adapter"]` and `transport: Literal["in_process","mcp","http"]`; transport is routing metadata and never grants authority;
- `CapabilityDescriptor(schema_version: Literal["1"], id: LogicalId, version: NonEmptyStr, capability_kind, transport, operation: LogicalId, input_schema_ref: SchemaRef, output_schema_ref: SchemaRef)`; it is the sole hashed source for kind/transport and the manifest copies those values;
- `ResolvedTextMaterial(ref: ContentRef, kind: Literal["prompt","skill","guardrail","handler","condition","reducer","stop_condition","gate_metric"], raw_utf8: bytes)` and `ResolvedCapabilityMaterial(ref: CapabilityRef, descriptor: CapabilityDescriptor)` form the closed `ResolvedMaterial` union supplied to the builder;
- `SkillBinding(skill_ref, required: Literal[True] = True)`; every v1 binding is mandatory and ordered, with optional/adaptive skill selection deferred to a future schema version;
- `InputBinding(name: LogicalId, schema_ref: SchemaRef, required: bool = True, cardinality: Literal["one","many"] = "one")` describes dependency-injected runtime artifacts;
- `OutputBinding(name: LogicalId, schema_ref: SchemaRef)`, with exactly one output named `primary`;
- `ExecutionSpec(kind, handler_ref, model_tier, thinking_budget)`, where `model_tier: Literal["fast","reasoner","reasoner_deep"] | None` and `thinking_budget: NonNegativeInt | None`;
- `EvidencePolicy(tool_calls=OPTIONAL, require_input_refs=True, require_number_anchors=True, allow_unsourced_numbers=False, optional_data_may_degrade=True)`, with the last four fields strict booleans;
- `CompatibilityBinding(legacy_source_schema: SchemaRef, source_config_digest: DigestHex, legacy_mapping_digest: DigestHex)`;
- `WorkerSpec(schema_version, id, catalog_role, selection_scope, compatibility, lane, persona, tier, execution, system_prompt_ref, skills, guardrail_refs, capability_allowlist, read_categories, params_schema_ref, inputs, outputs, evidence_policy, supported_modes, can_emit_decision, decision_authority, borrowed_from)`: `schema_version: Literal["1"]`; `id: LogicalId`; `catalog_role: Literal["final","compatibility"]`; `selection_scope: Literal["dynamic_allowed","static_legacy_only"]`; `persona: NonEmptyStr`; prompt/skill/guardrail refs are `ContentRef`; capabilities are `CapabilityRef`; `supported_modes: tuple[DataMode,...]` is non-empty, sorted and unique; `lane` uses the six design lanes; `read_categories` is an ordered unique tuple drawn from `context | upstream_artifacts | market_data | memory | experience_cases`; `decision_authority: Literal["none","advisory_only"]`; `borrowed_from: tuple[LogicalId,...]` is sorted/unique;
- `WorkerCatalogSnapshot(schema_version: Literal["1"], catalog_version: NonEmptyStr, content_manifest, skill_manifest, capability_manifest, workers, catalog_digest)`.

All plural fields use immutable tuples; fixed-order bindings preserve their declared order, while set-like manifests/allowlists are canonical-key sorted and unique. A snapshot contains declarations, not prompt/skill bytes. `validate_catalog_snapshot(snapshot)` only verifies declared digest equality and cross-references.

`catalog_material_digest(material)` hashes Task 1 canonical JSON with domain tag `catalog-material-v1`, material kind and canonical content. Text `raw_utf8` must decode as strict UTF-8 without BOM, is Unicode-NFC normalized, and converts CRLF/CR to LF; every other code point is preserved, so trailing newline/space presence changes the digest. Capability content is the descriptor's semantic projection (`id`, `version`, kind, transport, operation, input/output SchemaRefs), excluding only the ref's own declared digest. `build_catalog_snapshot(..., resolved_material: tuple[ResolvedMaterial,...])` is pure: it computes these digests, requires exact one-to-one ref/material coverage, builds manifests, sorts set-like collections and computes/verifies `catalog_digest`; it never loads a path. Manifest kind/transport must equal the descriptor/material and cannot be supplied independently. The runtime resolver that supplies bytes/descriptors is outside Phase 1.

`WorkerSpec` freezes: stable identity, lane/persona/tier, execution contract, optional prompt ref, ordered skill/guardrail bindings, exact capability allowlist, read categories, a separate params schema, named runtime input/output bindings, EvidencePolicy, supported modes, decision authority and provenance labels.

`WorkerCatalogSnapshot` freezes: catalog version, sorted content/capability manifests, complete workers and verified catalog digest. Physical storage/resolution is server-owned and outside the public snapshot.

**Layer meanings:**
- prompt = role, authority and non-negotiable system boundary;
- skill = reusable playbook and data-source priority;
- guardrail = mandatory cross-cutting rule;
- typed context = current-run input and never a skill;
- capability = exact callable authorization; MCP transport alone grants no permission.

**WorkerSpec v1 invariants:**

- params/input/output types are `SchemaRef`, not `"Model@1"` strings;
- prompt/skill/guardrail/handler/capability refs are catalog-owned id/version/digest values, never file paths;
- skill bindings are fixed and ordered; Plan may select `worker_id` but cannot add, remove or replace skill refs;
- `DETERMINISTIC` requires a handler ref and forbids model tier/thinking budget; prompt/skills may be empty;
- `LLM` requires prompt ref and model tier, defaults thinking budget to `0`, uses a non-negative thinking budget, and forbids arbitrary handler ref;
- supported modes is non-empty and primary output is required;
- `tool_calls=FORBIDDEN` requires an empty capability allowlist;
- `tool_calls=REQUIRED` requires a non-empty capability allowlist;
- a valid no-tool worker may complete with zero tool calls;
- `can_emit_decision=False` requires `decision_authority="none"`; `True` requires `"advisory_only"`. V1 has no execution/trading authority value;
- final workers require `catalog_role="final"`, `selection_scope="dynamic_allowed"`, no compatibility binding and a non-`compat.` ID. Compatibility workers require `catalog_role="compatibility"`, `selection_scope="static_legacy_only"`, a `compat.*` ID and a complete `CompatibilityBinding`; they are excluded from the final-24 completeness count;
- each required `InputBinding` must be satisfied by Plan dependency validation; `params_schema_ref` never doubles as the runtime artifact-input schema;
- refs are unique and resolve in the same snapshot;
- only a complete entry may appear in a runnable catalog.

**WorkerCatalogSnapshot invariants:**

- contains reviewed content/capability manifests and complete WorkerSpecs;
- rejects duplicate/unknown refs and digest mismatches;
- catalog digest is registration-order independent;
- changing any bound prompt/skill/guardrail/handler/capability/WorkerSpec changes catalog digest;
- the Task 0 24-worker map is planning/migration evidence, not a runnable catalog.

- [ ] **Step 1: Write failing catalog tests**

Required tests:

1. LLM/deterministic conditional matrix;
2. deterministic no-prompt/no-skill success;
3. no-tool success, forbidden-tool mismatch rejection, and required-tool/empty-allowlist rejection;
4. duplicate/unknown ref rejection;
5. declared digest mismatch, missing/duplicate material and descriptor/manifest mismatch rejection;
6. manifest order independence;
7. content drift changes catalog digest; LF/CRLF and canonically equivalent Unicode match, while trailing-newline and descriptor changes do not;
8. physical path-like refs cannot be supplied through WorkerSpec;
9. an incomplete planned worker cannot enter a runnable snapshot.
10. exact skill-v1 three-line description grammar, duplicate/extra frontmatter key, trigger escaping/order/mismatch and critical-opening rejection;
11. separate params/input/output binding construction and duplicate binding-name rejection.
12. final/compatibility role, ID, scope and compatibility-binding matrix rejection; final-24 counting ignores `compat.*`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_catalog.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement pure catalog models and validators**

Do not load files, write the 24 playbooks, sync skills, instantiate agents, call tools or populate all 24 final workers in this task. Phase 1 freezes the shape and validation rules of a skill binding. Phase 2 populates the three-worker pilot and then the `compat.*` subset required for full legacy `stock-deep-dive` equivalence; later phases freeze the redesigned workers' actual content before first use, and Phase 8 closes the final 24-worker catalog.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/orchestration/test_catalog.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/catalog.py tests/orchestration/test_catalog.py
git commit -m "feat(orchestration): freeze worker skill and capability catalog ABI"
```

---

## Task 10: OrchestrationRequest / PlanDraft / frozen Plan contracts

**Files:**
- Create: `guanlan_v2/orchestration/spec.py`
- Test: `tests/orchestration/test_spec.py`
- Test: `tests/orchestration/test_plan_structure.py`
- Test: `tests/orchestration/test_plan_catalog_validation.py`

**Interfaces:**
- Consumes: `WorkerCatalogSnapshot`, `SchemaRegistry`, `BudgetReservation`, `ContextSnapshot`, `PlanApproval`, shared enums, `SchemaRef`, `PayloadRef`, `Symbol`, `DigestModel`.
- Produces: `OrchestrationRequest`, `Dependency`, `PlanNode`, `GateCfg`, `GateResult`, `DebateCfg`, `ReducerCfg`, `PlanDraft`, `Plan`, `PlanValidationIssue`, `PlanValidationReport`, `StaticLegacyPlanAttestation`.

WorkerSpec/ExecutionSpec/EvidencePolicy live in `catalog.py` (Task 9A), not in `spec.py`.

### Request invariants

- `workflow=="optimize_existing"` requires existing candidate artifact ID/hash/context snapshot ID together; other workflows forbid them.
- fallback preset belongs to the persisted request and is explicit.
- approval policy defaults to required.
- a `PlanDraft` with `source=DYNAMIC` copies approval policy from the trusted request and it must be `REQUIRED`; Planner output cannot choose `AUTO`.
- Phase 1 validation/freeze rejects `ApprovalPolicy.AUTO` for every source, including a caller-labelled PRESET/BOOTSTRAP. `PlanSource` is server-recorded provenance, not authority. Enabling AUTO requires a future versioned trusted-preset material/attestation contract in Phase 2; it cannot be inferred from a string enum.
- decision schedule details remain a Phase 6 consumer contract; Phase 1 request carries at most `decision_schedule_ref: ContentRef | None`, never three untyped id/version/digest strings.

### PlanNode and dependency invariants

- `PlanDraft` carries the exact `catalog_digest`, `schema_registry_digest`, `context_snapshot_ref: PayloadRef | None` and budget request selected by the trusted runtime; the ref must use namespace `main` and binds ContextSnapshot `content_digest`. It also carries the all-set/all-none compatibility tuple `legacy_source_schema`, `legacy_source_config_digest`, `legacy_mapping_digest`. Validation compares each with supplied immutable evidence rather than accepting Planner authority.
- PlanNode carries `worker_id`, typed params and graph metadata only.
- PlanNode has no prompt/skill/tool/MCP/callable/path override field.
- `Dependency(upstream_node_id, artifact_slot, upstream_output_key="primary", inject_as, policy, accept_statuses)` is the complete v1 edge ABI. `artifact_slot` must equal the upstream node's `writes_slot`; `inject_as` must name one downstream `InputBinding`.
- `Dependency(policy=BLOCK)` accepts exactly `{COMPLETED}`; callers cannot weaken this with DEGRADED/FAILED.
- debate identity fields are all-set or all-none.
- timeout, attempts and reservations use strict non-negative/positive integers as appropriate.
- conditions, reducers, stop conditions and gate metrics are catalog-owned `ContentRef`s of the corresponding manifest kind; Plan never embeds Python/callable/expression text.
- `params` is strict JSON-shaped configuration and is validated only against the selected Worker's `params_schema_ref`; hidden authority keys such as `handler`, `system_prompt`, `skills`, `tools`, `mcp` and `path` cannot bypass that schema.
- dependency-injected artifacts are separate from params. Each upstream `OutputBinding.schema_ref` must equal the target `InputBinding.schema_ref` in v1 (no implicit subtyping/coercion). Every required input is covered; cardinality `one` accepts exactly one edge, while `many` preserves Plan dependency declaration order. Unknown `inject_as`, duplicate single-input injection and unsatisfied required input are invalid.

### Pure offline validation in Phase 1

The model or pure `validate_plan_structure()` must reject:

1. duplicate node IDs;
2. dependency refs to missing nodes;
3. duplicate/missing sinks;
4. cycles;
5. non-auxiliary nodes that cannot reach a sink;
6. bootstrap with ContextSnapshot or main without one;
7. duplicate/mismatched debate seats/turn order and invalid round/turn tuples;
8. incoherent reducer producers/slots;
9. negative budget requests or zero concurrency.

Phase 1 also implements the I/O-free validator:

`validate_plan_draft(draft, *, request, context: ContextSnapshot | None, catalog, schema_registry, legacy_attestation: StaticLegacyPlanAttestation | None = None) -> PlanValidationReport`

Before validation, compute one `candidate_plan_digest` with domain tag `candidate-plan-v1` over the request identity/semantic digest, every executable draft field (including legacy source/config/mapping digests when present), ContextSnapshot **content** digest (not storage ID), catalog digest, schema-registry digest and exact budget request. It excludes its own declared value, approval/legacy-attestation records, reservation ID and freeze wall-clock. This is the single digest used before and after freeze; there is no second post-approval digest.

Expose that algorithm once as `compute_candidate_plan_digest(...) -> DigestHex`; validator, freeze and `attest_static_legacy_plan()` must call this same pure function rather than copy the projection.

`PlanValidationReport` freezes `valid`, canonically ordered `issues`, `candidate_plan_digest`, `request_digest`, `context_content_digest: DigestHex | None`, `catalog_digest`, `schema_registry_digest`, `legacy_attestation_digest: DigestHex | None` and `validator_version`. Bootstrap requires `context=None`; main requires the supplied immutable ContextSnapshot to match the draft ref/digest. The report binds the exact inputs it validated and rejects use with a different request/draft/context/catalog/registry/attestation.

`StaticLegacyPlanAttestation(schema_version: Literal["1"], attestation_version: Literal["static-legacy-v1"], plan_source: Literal["preset","preset_fallback"], request_digest: DigestHex, candidate_plan_digest: DigestHex, catalog_digest: DigestHex, legacy_source_schema: SchemaRef, source_config_digest: DigestHex, legacy_mapping_digest: DigestHex, builder_id: LogicalId)` is service-owned evidence created from one reviewed `LegacyGraphMapping`. It is not a Planner field and does not grant AUTO approval.

The validator must additionally reject:

1. unknown/incomplete `worker_id`, or draft catalog/schema-registry/context digests different from the supplied sealed snapshots;
2. `params` that fail the Worker's strict `params_schema_ref`, including missing, extra or override fields;
3. missing/incompatible `InputBinding` coverage, upstream output key/schema, injection cardinality or artifact-slot binding;
4. unsupported `DataMode`, unauthorized decision-class sink or a dependency policy that weakens `EvidencePolicy`;
5. unknown or wrong-kind condition/reducer/stop-condition/gate refs;
6. unresolved prompt/skill/guardrail/handler/capability refs or an invalid execution/tool-call matrix;
7. request/draft mismatch, any Phase 1 AUTO policy, or source/provenance fields chosen to bypass approval.
8. any `catalog_role="compatibility"` worker under DYNAMIC/BOOTSTRAP, or under PRESET/PRESET_FALLBACK without a same-request/candidate/catalog/source-config/mapping `StaticLegacyPlanAttestation`; all selected compatibility bindings must match that one attested legacy graph. Final workers remain usable normally.

This is contract validation, not runtime work: it reads only supplied immutable snapshots and performs no file/catalog loading, ledger mutation, scheduling, model/tool call or approval.

### Freeze semantics

- PlanDraft is immutable/copy-on-write.
- Plan is immutable.
- persisted `Plan` explicitly carries `request_id`, `request_digest`, `context_snapshot_ref: PayloadRef | None` (`None` for bootstrap), `catalog_digest`, `schema_registry_digest`, compatibility tuple/attestation digest when present, exact budget request, `budget_reservation_id`, freeze wall-clock and the complete executable draft fields needed to recompute its digest.
- final `Plan.plan_digest` must equal the already validated `candidate_plan_digest`; it therefore binds request, all executable draft fields, ContextSnapshot content, catalog, schema registry and budget request.
- `plan_digest` excludes approval event, legacy-attestation record/digest, freeze wall-clock and reservation ID, so those records can all bind the same precomputed digest without a cycle.
- persisted Plan rejects a mismatched declared digest.
- changing any executable field yields a new digest and requires new approval.
- `freeze_plan(draft, *, request, context, catalog, schema_registry, legacy_attestation, report, reservation, approval) -> Plan` reruns `validate_plan_draft` from those immutable inputs, then requires the supplied report to equal the recomputed result. A caller-constructed `valid=True` report is never authority.
- report, reservation and `ApprovalDecision.APPROVED` must bind the same request/candidate digest; missing/rejected/mismatched approval fails. Phase 1 `AUTO` always fails, regardless of `PlanSource`.
- the pure Phase 1 builder proves shape/digest consistency, not store authenticity. In Phase 2 the freeze endpoint is service-owned and accepts reservation/approval/legacy attestation only from the authoritative budget ledger, append-only event journal and reviewed legacy-mapping builder/store, never from Planner/user payload fields.
- the Phase 1 builder verifies supplied immutable records but never allocates budget or emits approval. Phase 2 runtime order for this v1 contract is: validate candidate → atomically reserve against that digest → obtain same-digest approval → freeze → dispatch. Dispatch recomputes/rechecks the frozen Plan digest from persisted request/context/catalog/registry/budget bindings and checks the active reservation.

- [ ] **Step 1: Write failing request/structure/freeze tests**

Required tests:

- request workflow/existing-ref matrix;
- valid bootstrap and main drafts;
- phase/context rejection;
- duplicate/missing node/sink rejection;
- cycle and unreachable-node rejection;
- BLOCK accept-status override rejection;
- debate/reducer local invariant rejection;
- negative/zero operational bounds rejection;
- unknown worker and catalog-digest mismatch rejection;
- strict Worker params-schema validation, including rejection of nested `handler/system_prompt/skills/tools/mcp/path` params when not declared by that schema;
- required/unknown/duplicate input binding, cardinality, slot and upstream-output/downstream-input schema mismatch rejection;
- unsupported mode, wrong-kind condition/reducer ref and weakened EvidencePolicy rejection;
- DYNAMIC/BOOTSTRAP compatibility-worker rejection; PRESET/PRESET_FALLBACK requires one matching service-owned legacy attestation and mapping/config digests;
- dynamic Planner approval-policy override rejection;
- validation report cannot be reused with a changed request/draft/context/catalog/registry;
- manually forged `valid=True` report cannot freeze an invalid or changed draft;
- report, reservation, approval and final Plan all bind one candidate digest; missing/rejected/mismatched approval blocks REQUIRED freeze;
- AUTO is rejected for dynamic, preset and bootstrap sources in Phase 1; a spoofed `source=PRESET` grants nothing;
- persisted Plan exposes every request/context/catalog/registry/budget binding needed for digest recomputation;
- PlanDraft and Plan mutation rejection;
- freeze time/reservation ID/approval-event ID do not change plan digest;
- params/node/dependency/context-content/catalog/schema-registry/budget-request changes do change candidate/final plan digest;
- declared plan-digest mismatch rejection;
- PlanNode JSON schema contains no prompt/skill/tool/MCP/path override field.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_spec.py tests/orchestration/test_plan_structure.py tests/orchestration/test_plan_catalog_validation.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement contracts and pure offline validators**

Resolve WorkerSpecs and schemas only from the supplied sealed in-memory snapshots. Do not load catalog files, reserve budget, schedule nodes, call models/tools, emit approval or execute a Plan in this task.

- [ ] **Step 4: Run tests**

Run: `pytest tests/orchestration/test_spec.py tests/orchestration/test_plan_structure.py tests/orchestration/test_plan_catalog_validation.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/spec.py tests/orchestration/test_spec.py tests/orchestration/test_plan_structure.py tests/orchestration/test_plan_catalog_validation.py
git commit -m "feat(orchestration): freeze request and offline-validated plan contracts"
```
---

## Task 11: Deferred gate — Study / trial / holdout contracts move to Phase 4

**Phase 1 action:** do not create `trials.py` and do not register provisional Study/Trial/Holdout models.

Reason: these records are security- and state-machine-bearing contracts. Freezing DTO fields without the following behavior would create false safety:

- governor-derived family identity and attestation;
- append-only TrialLedger and idempotency;
- atomic holdout reservation before reading data;
- exact lease transitions `reserved -> consumed/exhausted`;
- failure/timeout/inconclusive all exhausting the window;
- sealed/review namespace and capability checks at write and read;
- public HoldoutReceipt that cannot dereference metrics;
- recovery returning the same receipt and never reopening a lease;
- non-overlapping later-matured OOT windows.

**Phase 4 entry gate:** reuse the strict bases, refs, registry, events and digest rules from Phase 1, then design the complete state matrix and namespace tests before adding public models.

- [ ] Record this deferral in the Phase 1 registry completeness test: no Trial/Holdout type may appear in `default_registry()`.

No Phase 1 commit is required for this deferred task.
---

## Task 12: Source-versioned reversible legacy-schema migration adapters

**Files:**
- Create: `guanlan_v2/orchestration/migration.py`
- Test: `tests/orchestration/test_migration.py`
- Consumes: Task 0 migration table/`legacy_contract_samples.json`, Task 9A `CompatibilityBinding`, and Task 10 `StaticLegacyPlanAttestation`.

### Common migration envelope

`MappingBasis = Literal["authoritative_code", "approved_policy", "none"]`. Every scalar result is an immutable DigestModel with:

- `schema_version: Literal["1"] = "1"`, `adapter_id: LogicalId`, `adapter_version: NonEmptyStr`;
- `source_schema: SchemaRef`, exact `raw: LegacyScalar` and explicit `raw_kind: Literal["str","int","float"]`;
- `mapping_status: mapped | unmappable`;
- `mapping_basis`, plus `mapping_policy_id` when basis is `approved_policy`;
- typed normalized value or `None`;
- `reason: NonEmptyStr | None`.

Each concrete result freezes its normalized field and evidence fields:

- `MigratedRating.normalized: PortfolioRating | None`, `mapping_policy_id`;
- `MigratedResearchAction.normalized: ResearchAction | None`;
- `MigratedPositionAction.normalized: PositionAction | None`;
- `MigratedConfidence.normalized: Confidence | None`, `source_scale_id: LogicalId | None`, `mapping_policy_id`;
- `MigratedSentiment.normalized: SentimentBand | None`, `source_scale: Literal["pm1","zero_ten"] | None`, `mapping_policy_id`;
- `MigratedRotationStage.normalized: RotationStage | None`, `mapping_policy_id`.

For every result, `MAPPED` requires a non-`None` normalized value, basis other than `none`, no reason, and a policy ID iff basis is `approved_policy`. `UNMAPPABLE` requires normalized `None`, a non-empty reason and preserved raw/evidence. These matrices are model validation, not caller convention.

Scalar adapters use `LegacyScalar = StrictStr | StrictInt | StrictFloat`. They preserve the exact Python/JSON scalar type and value: `50`, `50.0`, `" HOLD "` and `"hold"` are distinct raw values whenever a reviewed adapter accepts them; `bool` is always rejected even though Python treats it as an integer subtype.

Unknown source schema, a value outside the inventoried source domain, wrong type, out-of-range value or non-finite number raises `ValueError`. A known, valid legacy value that has no evidence-backed equivalent in the new semantic domain returns explicit `UNMAPPABLE`.

Every adapter has an exact reverse function that returns `raw`, including an `UNMAPPABLE` result. A helper that returns a bucket/range is not the reverse API.

Accepted sources are `SchemaRef`s with module-qualified names. The implementation defines reviewed constants for these keys; it never dispatches on anonymous names such as `report_output@1`.

### 12.1 Rating adapter

- `migrate_rating(raw: StrictInt, *, source_schema: SchemaRef, mapping_policy_id: str | None = None) -> MigratedRating`.
- accepted source names are `financial_analyst.agent.tier3.report_writer.ReportOutput@1` and, only after its separate Task 0 row is reviewed, `financial_analyst.agent.etf.report_writer.EtfReportOutput@1`.
- strict integer `[-10,10]`; bool rejected.
- a five-band `PortfolioRating` table may be used only when Task 0 finds authoritative code or an explicitly approved new migration policy. The table records exact boundaries, policy ID, mapping basis and approval evidence; until then a valid raw rating returns `UNMAPPABLE` rather than pretending historical equivalence.
- `rating_to_legacy(migrated: MigratedRating) -> int` returns exact raw.
- optional `rating_bucket(rating, mapping_policy_id)` may expose the reviewed band, but does not claim exact reversal.

### 12.2 Research recommendation action

- `migrate_research_action(raw: StrictStr, *, source_schema: SchemaRef, mapping_policy_id: str | None = None) -> MigratedResearchAction`.
- accepted sources are the separately inventoried `financial_analyst.agent.tier3.report_writer.ReportOutput@1` and `financial_analyst.agent.etf.report_writer.EtfReportOutput@1` keys.
- normalized type is `ResearchAction`.
- legal domain: `buy/accumulate/hold/avoid/sell`.
- the inventoried stock/ETF sources accept exact lowercase values. Exact lowercase identity mapping is `mapping_basis=authoritative_code`; case/whitespace variants are outside their source domain and raise unless Task 0 records a separate approved alias policy. When such a policy is explicitly selected, `strip().lower()` may be used only as its lookup key and reverse still returns exact raw.
- `research_action_to_legacy(migrated) -> str` returns exact raw.
- research action is not silently converted into `PortfolioRating`; legacy rating and action migrate independently and any cross-field conflict remains explicit compatibility evidence.

### 12.3 Position adjustment action

- `migrate_position_action(raw: StrictStr, *, source_schema: SchemaRef, mapping_policy_id: str | None = None) -> MigratedPositionAction`.
- accepted sources are `financial_analyst.backtest.decision.DecisionLeg@1` and `financial_analyst.watch.models.WatchRec@1`.
- normalized type is `PositionAction`.
- legal domain: `buy/add/hold/reduce/sell`.
- exact lowercase identity is authoritative. Backtest evidence is `_VALID_ACTIONS`; Watch evidence is `_ACTIONS`. Case/whitespace aliases require a separate approved policy exactly as above; without one they raise rather than silently normalizing an invalid legacy value.
- `position_action_to_legacy(migrated) -> str` returns exact raw.
- this adapter never returns target weight, quantity, order or TargetPortfolioIntent; those require current holdings/cash/schedule and belong to the Phase 6/9 consumer adapter.

There is deliberately no `migrate_action(raw)` and no generic `MigratedAction`.

### 12.4 Confidence adapter

- `migrate_confidence(raw: LegacyScalar, *, source_schema: SchemaRef, mapping_policy_id: str | None = None) -> MigratedConfidence`.
- `confidence_to_legacy(migrated: MigratedConfidence) -> LegacyScalar` returns exact type and value even when unmappable.
- categorical adapter accepts only aliases documented for its source schema. `med -> Confidence.MEDIUM` is mapped only if the Task 0 row records authoritative alias evidence or an approved policy ID; otherwise valid `med` is returned as `UNMAPPABLE`.
- `financial_analyst.agent.tier3.introspector.IntrospectionProposal@1` is the module-qualified categorical source for `low/med/high`.
- numeric adapter requires an explicit source and reviewed `source_scale_id`/policy version, both persisted in `MigratedConfidence`.
- thresholds come from Task 0 evidence or an explicitly approved adapter table; do not invent anonymous `34/67` boundaries.
- bool and non-finite numbers are rejected.
- reverse returns exact raw string/number.

### 12.5 Sentiment adapter

- `migrate_sentiment(raw: LegacyScalar, *, source_schema: SchemaRef, scale: Literal["pm1", "zero_ten"] | None = None) -> MigratedSentiment`.
- `sentiment_to_legacy(migrated: MigratedSentiment) -> LegacyScalar` returns exact type/value and retains source/scale evidence.
- numeric inputs require explicit `pm1` or `zero_ten` source scale.
- Task 0 fixes the allowed scale per numeric source schema; caller-supplied `scale` must equal that row or validation raises. A caller cannot reinterpret one producer by choosing a different scale.
- categorical inputs require source schema and cover only inventoried producer domains such as `pos/neg/neu` or `利好/利空/中性`.
- a missing authoritative threshold/mapping is UNMAPPABLE, not guessed.
- reverse returns exact raw and retains scale/source.

### 12.6 Rotation / legacy market-cycle stage

- `migrate_rotation_stage(raw: StrictStr, *, source_schema: SchemaRef) -> MigratedRotationStage`.
- `rotation_stage_to_legacy(migrated: MigratedRotationStage) -> str` returns exact raw even when unmappable.
- the inventoried source is `guanlan_v2.strategy.perspectives.market_cycle@1`.
- `LegacyMarketCycleStage` and `RotationStage` are different enums.
- only evidence-reviewed semantic equivalents may map.
- literal `分化` may map only if Task 0 confirms equivalent meaning.
- legacy `"冰点"`, `"逼空"`, `"发酵"`, `"回踩/启动"` must not be collapsed into new `"启动"`, `"扩散"`, `"分化"`, `"退潮"` by string intuition; record UNMAPPABLE until Phase 5 defines an evidence-backed translation.
- reverse always returns exact raw.

### 12.7 Legacy agent / YAML / static-graph mapping ABI

Define recursive JSON types: `JsonScalar = None | bool | StrictInt | FiniteFloat | StrictStr`, `JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]`, `JsonObject = dict[str, JsonValue]`. Scalar-adapter bool rejection does not prohibit booleans in graph config.

YAML normalization v1 is closed and deterministic: use a safe parser; reject duplicate keys, non-string mapping keys, custom tags, timestamps/dates, binary values, merge key `<<`, anchors/aliases and non-finite floats. Preserve JSON scalar types and list order; mapping order is irrelevant and canonical JSON uses Task 1. Comments, quoting style and whitespace are intentionally outside the semantic ABI. `source_config_digest = content_digest(normalized_json_object)`.

Phase 1 freezes these exact immutable models:

- `LegacyWorkerMapping(schema_version, source_node_id, raw_node: JsonObject, target_worker_id: LogicalId | None, mapping_status, mapping_basis, mapping_policy_id, reason)`;
- `LegacyDependencyMapping(schema_version, source_upstream_node_id, source_downstream_node_id, raw_edge: JsonObject, source_strength: Literal["hard","soft"], accepted_statuses: tuple[NodeStatus,...], missing_output_behavior: Literal["block","degrade","skip","unknown"], target_policy: DependencyPolicy | None, mapping_status, mapping_basis, mapping_policy_id, reason)`;
- `LegacyInputMapping(schema_version, source_consumer_node_id, source_key: NonEmptyStr, source_kind: Literal["base","upstream"], source_upstream_node_id: NonEmptyStr | None, target_kind: Literal["input_binding","param","context","service_binding"] | None, target_input_binding: LogicalId | None, target_param_key: NonEmptyStr | None, target_context_field: NonEmptyStr | None, target_service_binding: NonEmptyStr | None, upstream_output_schema_ref: SchemaRef | None, projection: Literal["raw","single_field_unwrap","model_dump"], projection_field: NonEmptyStr | None, missing_behavior: Literal["error","omit","inject_none","skip_consumer","unknown"], mapping_status, mapping_basis, mapping_policy_id, mapping_evidence: NonEmptyStr | None, reason)`;
- `LegacyGraphMapping(schema_version, adapter_version, source_schema, source_format: Literal["yaml","json"], normalized_raw_config: JsonObject, source_config_digest, worker_mappings, dependency_mappings, input_mappings, mapping_status, reason)`.

Nested mapping matrices are strict: each mapped worker/dependency/input requires its target plus basis/policy evidence and no reason; an unmappable item requires no target and a reason. For `LegacyInputMapping`, `source_kind="base"` forbids both upstream fields; `source_kind="upstream"` requires `source_upstream_node_id`, `target_kind="input_binding"` and, when mapped, `upstream_output_schema_ref`. A mapped row selects exactly one target field matching `target_kind`: an input binding resolving on the mapped consumer WorkerSpec, a key accepted by its strict params schema, a reviewed `DataContext`/request context field, or a named service-owned binding such as the output locator. Caller paths/callables are never service bindings. `single_field_unwrap` alone requires `projection_field`; the other projections forbid it, and non-raw projection is allowed only for an upstream input handled inside the attested compatibility handler.

For an upstream row, Plan/InputSnapshot injection still binds the **unprojected** upstream Artifact `SchemaRef` to an exact compatibility `InputBinding`; scalar unwrap/`model_dump` occurs only after snapshot verification inside the catalog-owned compatibility handler and is recorded in provenance. `missing_behavior="inject_none"` requires a nullable target, `omit` requires an optional/defaulted target, and `skip_consumer` maps only to reviewed SKIP semantics. A direct dependency row must agree with its `LegacyDependencyMapping`: BLOCK→`error`, DEGRADE→`omit|inject_none`, SKIP→`skip_consumer`. A non-direct upstream source must be a proven transitive ancestor in the normalized legacy graph and its behavior may not bypass an intervening BLOCK; otherwise it is UNMAPPABLE. A mapped input requires non-empty `mapping_evidence` and non-`unknown` behavior; an unmappable input clears every target field and explains why. Every normalized legacy input key appears exactly once per consumer, and a fully mapped compatibility graph covers every required target InputBinding/param/context/service requirement according to its own cardinality. No adapter may infer field unwrapping, `model_dump`, missing-value behavior, scheduling or base-versus-upstream origin from matching names.

The graph is `MAPPED` iff every nested worker, dependency and input item is mapped; otherwise it is `UNMAPPABLE` with a summary reason. Worker/dependency/input tuples follow normalized source declaration order and their respective identity keys are unique. `input_mappings` are part of the `LegacyGraphMapping` semantic digest: changing source kind/key, target kind or target field, upstream schema, projection, projection field, missing behavior or evidence changes `legacy_mapping_digest` and invalidates any compatibility binding/attestation.

- `normalize_legacy_graph_config(raw: StrictStr | JsonObject, *, source_format: Literal["yaml","json"]) -> JsonObject` performs the closed normalization/rejection rules before information can be lost;
- `migrate_legacy_graph(raw_config: StrictStr | JsonObject, *, source_schema: SchemaRef, source_format: Literal["yaml","json"]) -> LegacyGraphMapping` consumes the Task 0 reviewed worker/edge table;
- `legacy_graph_to_normalized_config(mapping: LegacyGraphMapping) -> JsonObject` returns a deep-equal canonical JSON-normalized config, including an `UNMAPPABLE` graph;
- `compatibility_binding_for(mapping) -> CompatibilityBinding` and `attest_static_legacy_plan(mapping, draft, request, *, context: ContextSnapshot | None, catalog: WorkerCatalogSnapshot, schema_registry: SchemaRegistry) -> StaticLegacyPlanAttestation` are pure builders. They require a fully `MAPPED` graph, including complete input mappings, and bind its semantic digest plus `source_config_digest`. The attestation builder calls the one `compute_candidate_plan_digest(...)` with the same request/draft/context/catalog/schema-registry inputs used by validation; the exact budget request already comes from the draft. No attestation is emitted for a partial/unmappable graph;
- old hard/soft behavior maps only through a reviewed row that fixes `accepted_statuses`, `missing_output_behavior` and target `DependencyPolicy`;
- an unknown worker, YAML node kind, output slot, input key/origin/projection or dependency meaning yields `UNMAPPABLE`, never a guessed edge or input binding;
- a `LegacyGraphMapping` is migration evidence and **cannot construct or masquerade as a runnable `Plan`** while any worker/edge/input is incomplete;
- Phase 2 first proves the three-worker pilot, then uses attested `compat.*` entries to run full legacy `stock-deep-dive` equivalence on a frozen fixture: node terminal statuses, dependency/block/degrade behavior, artifact slots, output `SchemaRef`s, base-versus-upstream input origin, target InputBinding names, projections, missing-input behavior, injected payload shapes/schema refs and canonical normalized payloads must match. Live LLM prose is not required to be byte-identical. Phase 1 only freezes and round-trips the mapping/attestation ABI and fixtures.

- [ ] **Step 1: Write failing migration tests from real fixtures**

Required tests:

1. report `avoid` and `accumulate` are preserved;
2. position `add` and `reduce` are preserved;
3. raw `"hold"` under report and position source schemas returns different typed result classes;
4. canonical lowercase actions map exactly; case/whitespace variants raise without an approved alias policy and round-trip exact raw only when such a policy is selected;
5. wrong semantic-domain/source passed to an adapter fails;
6. no generic action adapter/model is exported;
7. rating bounds, bool and finite checks;
8. confidence `med` follows the Task 0 evidence row: mapped only with authoritative/approved alias evidence, otherwise exact-raw UNMAPPABLE;
9. numeric sentiment requires the explicit scale fixed for that source and rejects a caller-selected mismatch;
10. known non-equivalent rotation stages return UNMAPPABLE;
11. unknown source/raw raises;
12. every scalar fixture marked exact round-trips with identical raw type and value, including `50` versus `50.0` and UNMAPPABLE values;
13. stock/ETF short-name collisions are impossible because source keys are module-qualified;
14. graph config round-trips to the exact normalized object and digest;
15. duplicate/non-string YAML keys, tags/timestamps, merge/anchor/alias and non-finite values are rejected;
16. reviewed hard/soft accepted statuses/missing-output/target-policy semantics are retained, while unknown YAML nodes/edges are UNMAPPABLE;
17. nested/overall mapping-status matrices cover workers, dependencies and inputs, and any incomplete graph mapping cannot produce a compatibility binding, attestation or runnable Plan;
18. input source-kind matrix rejects upstream fields for `base`, requires the upstream node/output SchemaRef for `upstream`, and permits a missing upstream schema only on an explicitly UNMAPPABLE input;
19. target-kind matrix requires exactly one matching input-binding/param/context/service target, validates it against the WorkerSpec/request/context/service allowlist, and rejects caller paths;
20. projection matrix requires `projection_field` only for upstream `single_field_unwrap`; mapped inputs require evidence and a non-`unknown` missing behavior, while Plan/InputSnapshot retain the unprojected exact SchemaRef;
21. BLOCK/DEGRADE/SKIP versus error/omit/inject-none/skip-consumer consistency, nullable/optional targets and non-direct transitive-ancestor ordering are enforced;
22. duplicate `(source_consumer_node_id, source_key)`, unknown input keys, uncovered required targets, cardinality violations and unreviewed base/upstream origins make the graph invalid or UNMAPPABLE rather than guessing by name;
23. compatibility binding/attestation digests match the graph/config and change when either normalized config or any worker/dependency/input mapping field changes;
24. attestation candidate digest exactly equals validator/freeze output for the same context/schema registry and changes on either input;
25. the full `stock-deep-dive` fixture records expected source kind/target kind, exact target, projection, missing behavior, injected payload shape and input/output SchemaRefs for Phase 2 old/new equivalence.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/test_migration.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement versioned adapters**

Use separate result models/functions for the two action domains. Mapping tables include adapter/policy version and are covered by tests. Implement the graph mapping as data conversion only. Do not derive a trade target from a scalar legacy action and do not instantiate a Plan from legacy YAML in Phase 1.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/orchestration/test_migration.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/migration.py tests/orchestration/test_migration.py
git commit -m "feat(orchestration): add source-versioned reversible legacy adapters"
```
---

## Task 13: Populate sealed registries + phase-1 completeness/golden tests

**Files:**
- Modify: `guanlan_v2/orchestration/schema_registry.py`
- Modify: `guanlan_v2/orchestration/__init__.py`
- Test: `tests/orchestration/test_registry_population.py`
- Test: `tests/orchestration/test_contract_completeness.py`
- Create: `tests/orchestration/golden/schema_manifest_v1.json`

**Interfaces:**
- `PHASE1_PUBLIC_MODELS`: one reviewed tuple of public schema models.
- `INTERNAL_MODELS`: explicit mapping of intentionally unregistered helper model to reason.
- `default_registry() -> sealed SchemaRegistry`.
- default registry exposes a stable manifest and registry digest.

Do not infer a version with `or "1"`; every registered model must explicitly inherit/declare the version. Do not register only the classes convenient for one test.

### Completeness rules

Every public ContractModel subclass in Phase 1 modules must either:

1. appear in `PHASE1_PUBLIC_MODELS`, or
2. appear in `INTERNAL_MODELS` with a reviewed reason.

The test must assert:

- public model has non-empty schema version;
- public model JSON Schema closes `schema_version` with the expected `const`;
- unknown fields are forbidden;
- immutable fact models are frozen;
- registry key matches model name/version;
- model JSON-schema digest matches `schema_manifest_v1.json`;
- reverse registration order produces the same registry digest;
- representative payloads round-trip through registry;
- `TypedPayloadRef`, `MemoryRecordRef`, both canonical empty-memory facts, `ContextSnapshot` and `InputSnapshot` are public registered models with the Task 3/8 locator/schema/hash/ref/session-scope projections intact;
- registry payload validation rejects a self-declared version different from the resolved `SchemaRef`;
- registry validation still rejects extra fields;
- no Task 11 Trial/Holdout type appears;
- no deferred Market/Regime/Rotation/TargetPortfolio/DecisionSchedule/DebateMessage type appears;
- every catalog snapshot is validated by catalog rules rather than being silently treated as a schema registry;
- changed golden manifest requires explicit review; tests never regenerate it automatically.

- [ ] **Step 1: Write failing population/completeness tests**

Run: `pytest tests/orchestration/test_registry_population.py tests/orchestration/test_contract_completeness.py -v`

Expected: FAIL until `default_registry`, public/internal lists and golden manifest exist.

- [ ] **Step 2: Implement reviewed population and seal**

Keep import-time behavior pure: construct on explicit `default_registry()` call and seal before return.

- [ ] **Step 3: Run focused tests**

Run: `pytest tests/orchestration/test_registry_population.py tests/orchestration/test_contract_completeness.py -v`

Expected: PASS.

- [ ] **Step 4: Run the whole Phase 1 suite**

Run: `pytest tests/orchestration/ -v`

Also run:

`python -m compileall -q guanlan_v2/orchestration`

If Ruff is available:

`ruff check guanlan_v2/orchestration tests/orchestration`

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/schema_registry.py guanlan_v2/orchestration/__init__.py tests/orchestration/test_registry_population.py tests/orchestration/test_contract_completeness.py tests/orchestration/golden/schema_manifest_v1.json
git commit -m "test(orchestration): freeze phase1 registry and contract manifest"
```
---

## Phase 1 Exit Gates

The old “self-review complete” assertion is removed. Phase 1 is complete only when implementation evidence satisfies every gate below.

### Contract foundation

- [ ] digest golden vectors pass under at least two subprocess hash seeds;
- [ ] every public model rejects extra fields;
- [ ] every public datetime rejects naive input;
- [ ] every immutable fact rejects assignment;
- [ ] every persisted declared digest is computed/verified and non-self-referential;
- [ ] `PayloadRef` accepts only main/sealed/review/audit, and sealed/review/audit refs cannot enter main/public events or snapshots;
- [ ] `TypedPayloadRef` binds exact schema + payload content for replay while projecting only object locator to audit; Artifact/NodeRun evidence consumers reject non-main refs and retain evidence on failed/no-Artifact attempts;
- [ ] data/memory snapshot locators are audit-only while snapshot-content/memory/past-context hashes, service-bound memory-session scope and exact canonical MemoryRecordRefs are semantic; the empty-memory builder with `memory_session_id=None` produces the only valid pre-facade empty binding.

### Catalog / Plan security

- [ ] Plan cannot carry arbitrary callable, physical path, tool, MCP or skill override;
- [ ] all runnable WorkerSpec refs resolve to the approved catalog snapshot;
- [ ] `compat.*` workers are `static_legacy_only`, require one matching service-owned graph attestation, are rejected for DYNAMIC/BOOTSTRAP and never count toward the final 24;
- [ ] bound content drift changes catalog digest;
- [ ] deterministic/LLM/no-tool conditional matrices pass;
- [ ] `tool_calls=REQUIRED` cannot bind an empty capability allowlist;
- [ ] Plan structural, catalog/schema-aware offline validation and freeze-digest tests pass;
- [ ] a validation report is bound to the exact request/draft/context/catalog/registry digests and cannot be replayed against changed inputs;
- [ ] params schema and named dependency input bindings are validated independently, with exact v1 I/O SchemaRef matching;
- [ ] report, supplied reservation, supplied approval and frozen Plan all bind one `candidate_plan_digest`/`plan_digest`; the pure freeze builder recomputes validation, rejects missing/mismatched records and never trusts a report flag;
- [ ] Phase 1 rejects AUTO from every source and exports the required consumer order `candidate validation → reservation → same-digest approval → freeze → dispatch`; AUTO remains disabled until a separate trusted-preset attestation contract is reviewed.

Authoritative ledger/journal provenance, support-before-reservation and enforcement of that runtime order are Phase 2 Exit Gates, not circular prerequisites for completing the Phase 1 contract package.

### Migration correctness

- [ ] all 24 stable worker IDs exist once in the migration map;
- [ ] incomplete workers are absent from runnable catalog snapshots;
- [ ] research and position actions remain different types;
- [ ] all legacy source schema keys are module-qualified and collision-free;
- [ ] every exact legacy scalar fixture round-trips with identical raw type and value, including UNMAPPABLE results;
- [ ] legacy worker/YAML/static-graph fixtures preserve normalized config digest and reviewed hard/soft dependency semantics;
- [ ] every legacy graph input key has one reviewed base/upstream `LegacyInputMapping`; target-kind/field, required bindings/params/context/service inputs, projection, missing behavior and upstream SchemaRefs are complete and cross-consistent, and any input-mapping change invalidates the bound attestation;
- [ ] an incomplete legacy graph mapping cannot become a runnable Plan;
- [ ] uncertain stage/sentiment semantics are UNMAPPABLE rather than guessed;
- [ ] five-band rating conversion cites authoritative code or an approved mapping-policy ID; otherwise it is UNMAPPABLE;
- [ ] no generic source-less action migration API exists.

### Scope protection

- [ ] no scheduler, LLM/tool/data call or runtime integration was added;
- [ ] no Trial/Holdout provisional schema is registered;
- [ ] no deferred Phase 5/6/8 payload is registered as a Phase 1 contract;
- [ ] existing `financial_analyst` runtime and `workflow/executor.run_graph` are unchanged;
- [ ] unrelated worktree changes are not staged.

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 1 — canonical digest and strict base;
2. after Task 3 — schema/content identity;
3. after Task 9A — skill/capability authorization;
4. after Task 10 — Plan structure, catalog/schema validation and freeze;
5. after Task 12 — source-schema migration semantics;
6. after Task 13 — full registry/golden gate.

Do not begin Phase 2 until every Phase 1 exit gate is checked with test evidence.
