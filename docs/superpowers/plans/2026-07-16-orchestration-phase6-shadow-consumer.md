# Orchestration Phase 6 · 影子消费端 (shadow consumer) Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the schedule/envelope contract tasks, the shadow runner, the compatibility mirror and the registry-chain/red-line tasks. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Pin the shadow consumer end **before** Lane D migrates onto it (spec §12.6): a minimal, fully typed `PortfolioTargetProposal → runtime TargetPortfolioIntent → ShadowDecisionAgent / ShadowBacktestRunner → engine Broker` chain plus the stage-① compatibility mirror against the frontend `runBacktest` profile, and the deterministic dual-curve runner lane (`ShadowBacktestRunner.run_targets` over envelope-free target sets, Task 6) that Phase 9's dual-curve replay consumes. The phase freezes the portfolio, time and idempotency invariants (apply-once targets, order/fill key families, schedule-computed execution times) and closes the gaps the engine baseline lacks — take-profit, max-hold and a corporate-action ledger — **in the shadow runner, never inside the engine**. LLM zero-trading is structural: `origin="LLM"`, `authority="ADVISORY_ONLY"`, `execution_scope="SHADOW_ONLY"` are closed `Literal`s, no live order/signal capability exists anywhere in the catalog for a worker to be granted, and nothing in this phase can promote a shadow artifact into a live instruction.

**Architecture:** Phase 1 stays the sole owner of digests, `Artifact`, `RunEvent`, Plan validation/freeze and the schema registry; Phase 2 owns the runtime stores/admission; Phase 3 owns data/PIT and the `TradingCalendar` protocol; Phase 4 owns Trial/Holdout and the Evaluator; Phase 5 owns Lane 0. Phase 6 adds one new contract module `guanlan_v2/orchestration/shadow.py` (the four deferred decision-class payloads, the `DecisionSchedule` registry, idempotency key families, shadow run records and the Phase 6 registry/catalog chain) and one new adapter module `guanlan_v2/orchestration/adapters/luozi.py` (target-portfolio diff, `ShadowDecisionAgent`, `ShadowBacktestRunner`, corporate-action application, compatibility mirror). The engine fill baseline `engine/financial_analyst/backtest/` (`Broker`, `VirtualPortfolio`, `CostModel`, limit helpers, `prepare_bar`, `compute_metrics`, `TradeLog`, `PitReader`) is consumed **read-only and unmodified**; the daily `agent.decide()` loop of `BacktestRunner` is deliberately NOT reused as the execution entry. The frontend `ui/seats/luozi-data.jsx` is also read-only: its `runBacktest` semantics become a documented compatibility profile plus fixed fixtures, never a Python call contract. The only Phase 1 source/test files touched are `guanlan_v2/orchestration/events.py` (pure-additive `EventType` members + per-type rules) and the two absence guards (`tests/orchestration/test_events.py` frozen set, `tests/orchestration/test_contract_completeness.py` deferred-payload guard), flipped exactly per the CRIB Phase 4 mechanism: absence → presence-in-phase6-registry. Phase 1 golden manifests are never regenerated.

**Tech Stack:** Python ≥3.11, Pydantic v2 (strict/frozen/extra-forbid `DigestModel`), `zoneinfo`, `pytest` + `pytest-asyncio` (or `asyncio.run` in sync tests). All new modules `from __future__ import annotations`. Depends on implemented Phase 1 contracts and the implemented Phase 2 runtime, Phase 3 data/PIT + calendar, Phase 4 chain node and Phase 5 chain node in `guanlan_v2/orchestration/`.

## Global Constraints

These extend, and never override, the Phase 1–5 Global Constraints and Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import `DigestModel`/`ContractModel`, `DigestHex`/`UtcDateTime`/`FiniteFloat`/`NonNegativeInt`/`PositiveInt`/`NonEmptyStr`, `content_digest`, `SchemaRef`/`ContentRef`/`PayloadRef`/`TypedPayloadRef`, `Symbol`, `Confidence`, `Artifact`, `RunEvent`, `OrchestrationRequest`, `SchemaRegistry`, `WorkerCatalogSnapshot`, `AuthoritativeClock`, `EventStore`/`PayloadStore`/`IdempotencyConflict` and `TradingCalendar` from their owning modules. Phase 6 must not redefine canonical JSON, candidate/plan digests, WorkerSpec, catalog snapshot semantics, Plan validation/freeze, event visibility semantics or PIT guard behavior.
- **Typed refs per Phase 1 Amendment 1.** Typed evidence pairs — any schema-bearing evidence reference this phase introduces (e.g. refs to registered `ShadowRunResult`/dual-curve payloads, provenance evidence refs) — are the Phase 1 composite `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)`; bare storage locators (`PayloadStore` put/get) stay plain `PayloadRef`. Never alias or conflate the two.
- **Exact frozen names.** `TargetPosition`, `PortfolioTargetProposal`, `TargetPortfolioIntent`, `DecisionSchedule`, `ShadowDecisionAgent`, `ShadowBacktestRunner` are spec-frozen names; the first three class names must byte-match Phase 1 `spec.py` `_DECISION_CLASS_SCHEMAS` entries (`spec.py:149-151`) so the existing `unauthorized_decision_sink` validation keys onto them without modification.
- **Engine and frontend are read-only.** No file under `engine/financial_analyst/backtest/` and no file under `ui/` is modified. Every gap (take-profit, max-hold, corporate actions, target-diff) is closed in `adapters/luozi.py` above the Broker. `Broker.match` mutates the portfolio on success (broker.py:168-176), therefore every idempotency boundary lives **above** Broker.
- **Runtime-only envelope.** Every `TargetPortfolioIntent` envelope field (`intent_id`, `target_version`, `proposal_artifact_id`, `proposal_digest`, `source_decision_artifact_id`, `decision_schedule_id/version/digest`, `scheduled_for`, `decision_as_of`, `eligible_execution_at`, `valid_until`, `origin`, `authority`, `execution_scope`, `created_at`) is produced by the runtime builder from runtime arguments. The LLM-writable payload is `PortfolioTargetProposal` only; it is `extra="forbid"` so a model physically cannot self-report or override envelope fields.
- **Structural zero-trading.** `origin: Literal["LLM"]`, `authority: Literal["ADVISORY_ONLY"]`, `execution_scope: Literal["SHADOW_ONLY"]` — closed single-value Literals. No task in this phase registers any catalog capability; the Phase 6 catalog chain node is an identity extension, so no live order/signal write tool exists for any worker to be granted. `adapters/luozi.py` must not import `guanlan_v2.seats`, any HTTP client or any live quote/ledger writer.
- **No silent normalization.** v1 A-share long-only: duplicate symbols, NaN/Inf, negative weights, short positions and leverage are rejected **before staging**; `abs(sum(target_weight) + cash_weight - 1) <= 1e-8` is a validation error when violated, never a renormalization.
- **Schedule-computed time.** `scheduled_for` and `eligible_execution_at` are uniquely computed from registered `DecisionSchedule` fields (`kind`, `calendar_id`, `timezone`, `decision_local_time`, `cutoff_local_time`, `bar_frequency`, `execution_policy`); no caller may pass either timestamp directly into the envelope. `next_open↔open` and `next_bar_close↔close` pairing is a model validator. Time model ruled 2026-07-16: **`cutoff_at <= decision_as_of < eligible_execution_at`** — the cutoff is the upstream data/entry freeze at `cutoff_local_time`, `decision_as_of` is the `decision_local_time` instant (matching the schedule validator ordering `cutoff_local_time <= decision_local_time`); Phase 9 replay consumes this model.
- **Idempotency key families are frozen** (spec §2.3/§8): target apply `(intent_id, scheduled_for, target_version)` applies a target portfolio exactly once; every order id derives from `(target_apply_key, symbol, order_kind, trigger_bar, ordinal)`; every fill id from `(order_id, fill_seq)`. Orders, fills and rejects all retain their causation keys. Seeing realized results never mutates an intent in place — intents are `frozen=True` and run results reference intents only by content digest.
- **Compatibility profile honesty.** The stage-① mirror replicates what the frontend actually does (zero cost, single symbol, same-bar-close fill, fractional shares, no suspension/limit/T+1/lot/reject ledger, stop-first intrabar priority) as a **separate compatibility fill path**, never as a Broker parameterization, and never sets a false gate on behavior the frontend does not have (spec §9 line 958). Numeric tolerances are explicit module constants defined in this plan (Task 8). Full fill/reject/cost/corporate-action verification (stage ②) and the frontend switch-over date belong to Phase 9.
- **Registry/catalog chain naming** exactly per CRIB 4.5: `PHASE6_REGISTRY_DIGEST` + `build_phase6_registry(expected_phase5_digest)`, `PHASE6_CATALOG_DIGEST` + `build_phase6_catalog_snapshot(...)`, golden `tests/orchestration/golden/phase6_schema_manifest_v1.json`. Base = the exact Phase 5 digests; inherited JSON Schemas byte-identical; no "latest" alias; upstream goldens never regenerated.
- **Guard flips are the only Phase 1 test touches.** `tests/orchestration/test_contract_completeness.py` deferred-payload guard (Phase 6 names at lines 74-78) and `tests/orchestration/test_events.py` frozen `EventType` set (lines 124-153, as reshaped by Phase 4) flip from "absent" to "present and owned by the Phase 6 registry". `guanlan_v2/orchestration/events.py` receives only pure-additive enum members + per-type rules (the mechanism the CRIB fixed for Phase 4).
- **Degradation is badged.** Unknown ST flags, synthetic corporate-action inputs and any coverage shortfall surface as `badges`/`warnings` tuple entries on `ShadowRunResult`, never as silently-different fills.
- **Executable red/green checkpoints.** Every step named "Write failing … tests" immediately runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation; collection/environment errors do not count as the red checkpoint. The PASS step reruns the same focused tests plus listed upstream regressions.
- No placeholders, DRY, YAGNI, TDD, frequent commits with **explicit pathspec** (`git add <exact files>`; never `git add -A` — the branch is shared with concurrent sessions). Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 6 work starts only after the Phase 1 Amendment 1 exit gates and the Phase 2 exit gates pass and **every Phase 3, Phase 4 and Phase 5 Exit Gate has test evidence — chain-node existence alone is insufficient** (CRIB dependency: 6 depends on 2 for the runtime and 4 for the dual-curve→Evaluator handoff; the registry/catalog chain is linear through 5). Add `tests/orchestration/test_phase6_handoff.py` as an executable consumer test rather than copying upstream assertions.

**Files:**
- Create: `tests/orchestration/test_phase6_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. Phase 1 goldens (`schema_manifest_v1.json` **as re-frozen by Phase 1 Amendment 1** — `docs/superpowers/plans/2026-07-16-orchestration-phase1-amendment.md`, whose exit gates are a precondition of this gate — plus digest vectors) still pass and `default_registry()` is sealed with exactly the amended **11** registered schemas (the reviewed 8 plus `TypedPayloadRef@1`, `InputArtifactBinding@1`, `ContextRuntimeRequirements@1`) under the amendment's re-frozen `registry_digest`;
2. the Amendment 1 surface holds: `TypedPayloadRef` resolves as the Phase 1 **composite** `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)`; plain `PayloadRef` is unchanged as the bare locator; `ContextSnapshot.memory_snapshot_ref` and `ContextSnapshot.memory_selection_ref` are `TypedPayloadRef`;
3. the implemented upstream chain nodes resolve by their exact names: `PHASE2_BASE_REGISTRY_DIGEST`/`phase2_runtime_registry`, `PHASE3_DATA_REGISTRY_DIGEST`, `PHASE3_FULL_REGISTRY_DIGEST`/`build_phase3_full_registry`, `PHASE4_REGISTRY_DIGEST`/`build_phase4_registry`, `PHASE5_REGISTRY_DIGEST`/`build_phase5_registry`, and the catalog chain `PHASE2_STATIC_CATALOG_DIGEST → PHASE3_DATA_CATALOG_DIGEST → PHASE3_FULL_CATALOG_DIGEST → PHASE4_CATALOG_DIGEST → PHASE5_CATALOG_DIGEST`; each builder verifies its predecessor digest;
4. Phase 1 `_DECISION_CLASS_SCHEMAS` still equals `{"PortfolioDecision", "PortfolioTargetProposal", "TargetPortfolioIntent"}` and `OrchestrationRequest.decision_schedule_ref: ContentRef | None` still exists — the Phase 6 class names and the schedule binding surface key onto these without any `spec.py` change;
5. the Phase 1 deferred-payload guard still lists exactly `TargetPosition`, `PortfolioTargetProposal`, `TargetPortfolioIntent`, `DecisionSchedule` as Phase-6 deferred (`tests/orchestration/test_contract_completeness.py:74-78`) and no such model exists anywhere under `guanlan_v2/orchestration/` yet;
6. Phase 2 `EventStore.append`/`PayloadStore.put`/`IdempotencyConflict`/`RuntimeUnitOfWork` and `AuthoritativeClock` resolve with their implemented signatures; Phase 3 `TradingCalendar` protocol (`calendar_id`, `is_session(date)`, `sessions_between(start, end)`) resolves;
7. the engine baseline API is unchanged at the pinned signatures: `Broker.match(order, bar, prev_close, portfolio, next_bar_open=None, next_bar_date=None, is_st=False)` (broker.py:67-76), `Order` fields (broker.py:36-44), `CostModel` defaults (costs.py:38-43), `VirtualPortfolio.buy/sell/mark_to_market/check_stop/snapshot/seed_initial_nav/record_nav` (portfolio.py:94-226), `Position.sellable(today)` (portfolio.py:55-59), `limit_pct_for`/`compute_ref_prev_close`/`is_one_word` (limits.py:29/47/61), `prepare_bar(code, T, reader, loader, cfg)` (engine.py:98), `legs_to_orders` (engine.py:165), `compute_metrics` (metrics.py:32-39), `TradeLog.add_fill/trade_stats` (records.py:93/100), `DecisionLeg`/`Decision`/`DecisionInput` (decision.py:60-88) and `DecisionAgent.n_calls` being read by `BacktestRunner` (engine.py:278/355);
8. the engine has **no** take-profit, **no** max-hold and **no** corporate-action ledger (grep-style assertions over `engine/financial_analyst/backtest/`, per grounding evidence engine.py:15-16 and the corporate-action NOT-FOUND sweep) — the gaps this phase closes above Broker;
9. no Phase 6 source/test path overwrites Phase 1-owned `schemas.py`, `spec.py`, `schema_registry.py`, `catalog.py` or any upstream golden file.

**Task 0 correction clauses** (facts unknowable until upstream phases are implemented — resolve them here, never invent parallel semantics):

- If an exact Phase 4/5 registry/catalog export name, builder signature or golden filename differs in the implemented upstream public API, update this plan to that reviewed API before writing any Phase 6 code; do not invent an adapter with parallel semantics.
- The completeness firewall (`test_contract_completeness.py:149`, `test_phase1_modules_lists_every_module_defining_a_public_contract_model`) walks the whole package; Phases 2–5 will already have established the reviewed mechanism by which new-phase modules defining public `ContractModel` subclasses are enumerated and classified. Adopt that exact implemented mechanism for `shadow.py` and `adapters/luozi.py`; if — contrary to expectation — none exists, extend the firewall with a `PHASE6_MODULES` + public/internal classification mirroring the Phase 1 pattern, as a reviewed test change in the same commit that creates the module.
- `tests/orchestration/test_events.py:124-153` will have been reshaped by Phase 4 (Trial/Holdout flip). Extend the **current reviewed** frozen set with the two Phase 6 event values; do not restore or assume the original Phase 1 shape.
- If the implemented Phase 4 Evaluator intake requires a specific metrics payload shape for shadow curves, update the `ShadowRunResult` field list (Task 4) to that reviewed API before implementing it; Phase 6 only guarantees the payload is registered and digest-stable — wiring the dual curve into the Evaluator is Phase 9.
- If any pinned engine signature in item 7 has drifted, update this plan's **Consumes** lines to the real code before implementation; the engine itself is still not modified.

- [ ] **Step 2: Freeze the reviewed upstream evidence**

Record only exact digests and exported symbol signatures (Phase 1–5 registry/catalog digests, engine signature tuple); never record local paths or mutable singleton identities.

- [ ] **Step 3: Run the full suite and the gate**

Run from a state in which no Phase 6 module exists yet: `pytest tests/orchestration -v`.

Expected: every upstream test plus `test_phase6_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase6_handoff.py
git commit -m "test(orchestration): gate phase6 shadow consumer on phase2-5 contracts + engine baseline"
```

---

## File Structure (created/modified in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/shadow.py` | Phase 6 contract layer: `TargetPosition`/`PortfolioTargetProposal`/`TargetPortfolioIntent`/`DecisionSchedule`, schedule time computation, `DecisionScheduleRegistry`, idempotency key families, shadow run records, `CorporateActionEvent`, Phase 6 registry/catalog chain exports |
| `guanlan_v2/orchestration/adapters/__init__.py` | new subpackage marker (empty reviewed export surface) |
| `guanlan_v2/orchestration/adapters/luozi.py` | shadow half: `diff_target_portfolio`, `ShadowDecisionAgent`, `ShadowBacktestRunner`, corporate-action application, compatibility mirror + tolerance constants |
| `guanlan_v2/orchestration/events.py` | **modify (pure addition)**: `SHADOW_INTENT_ISSUED`/`SHADOW_TARGET_APPLIED` EventType members + per-type partition rule |
| `tests/orchestration/test_phase6_handoff.py` | executable upstream ABI/golden gate |
| `tests/orchestration/test_shadow_contracts.py` | proposal/position validation matrix |
| `tests/orchestration/test_decision_schedule.py` | schedule model, registry, time computation |
| `tests/orchestration/test_shadow_envelope.py` | runtime-only intent envelope + key families |
| `tests/orchestration/test_shadow_records.py` | apply/order/fill/reject/run-result/corporate-action contracts |
| `tests/orchestration/test_shadow_diff.py` | target-portfolio → order-plan diff step |
| `tests/orchestration/test_shadow_agent.py` | `ShadowDecisionAgent` engine-shape conformance |
| `tests/orchestration/test_shadow_runner.py` | `ShadowBacktestRunner` loop, apply-once, engine semantics |
| `tests/orchestration/test_shadow_gaps.py` | take-profit / max-hold / intrabar priority / corporate-action ledger |
| `tests/orchestration/test_shadow_mirror.py` | stage-① compatibility mirror vs fixtures under declared tolerances |
| `tests/orchestration/test_shadow_events.py` | shadow event types, persist-then-publish, idempotency |
| `tests/orchestration/test_phase6_registry.py` | cumulative registry/catalog chain + goldens + sink authorization |
| `tests/orchestration/test_shadow_redlines.py` | never-live-bus, no-promotion, structural Literals, no-mutation regression |
| `tests/orchestration/golden/phase6_schema_manifest_v1.json` | reviewed Phase 6 registry golden (never regenerated from test code) |
| `tests/orchestration/golden/phase6_catalog_manifest_v1.json` | reviewed identity catalog chain node evidence |
| `tests/orchestration/fixtures/shadow_mirror_v1.json` | fixed bars/signals/clock → expected trades/eqSeg/firstSig/metrics vectors |
| `tests/orchestration/test_contract_completeness.py` | **modify**: flip the four Phase-6 deferred-payload entries (absence → presence-in-phase6-registry) |
| `tests/orchestration/test_events.py` | **modify**: extend the reviewed frozen EventType set with the two shadow values |

---

## Task 1: Proposal contracts — `TargetPosition` + `PortfolioTargetProposal`

**Files:**
- Create: `guanlan_v2/orchestration/shadow.py`
- Test: `tests/orchestration/test_shadow_contracts.py`
- Modify (per the Task 0 firewall correction clause, same commit): the reviewed completeness-surface enumeration for the new module

**Consumes:** Phase 1 `DigestModel`, `FiniteFloat`, `PositiveInt`, `NonEmptyStr`, `DigestHex`, `Symbol` (data/symbols.py:39), `Confidence` (enums.py:27), `content_digest`.

**Produces (exact signatures/fields; spec §8 lines 802-809 field names preserved, types upgraded to Phase 1 strict house types):**

- `class ShadowContractError(ValueError)` — base; `class ProposalRejected(ShadowContractError)` — stage-前 rejection carrying a closed `reason_code: str` from `{"duplicate_symbol", "non_finite_weight", "negative_weight", "weight_sum_violation", "leverage_or_short"}`. (Pydantic `ValidationError` from strict field types also counts as rejection; the model validator raises `PydanticCustomError` with these same reason codes so tests can assert the code.)
- `WEIGHT_SUM_TOLERANCE: float = 1e-8` — module constant; the **only** tolerance in the weight-sum invariant.
- `class TargetPosition(DigestModel)` — `schema_version: Literal["1"] = "1"`; `symbol: Symbol`; `target_weight: FiniteFloat = Field(ge=0, le=1)`; `stop_loss_pct: FiniteFloat | None = Field(default=None, gt=0, le=1)`; `take_profit_pct: FiniteFloat | None = Field(default=None, gt=0)`; `max_hold_bars: PositiveInt | None = None`. All fields semantic; no excludes.
- `class PortfolioTargetProposal(DigestModel)` — `schema_version: Literal["1"] = "1"`; `positions: tuple[TargetPosition, ...]`; `cash_weight: FiniteFloat = Field(ge=0, le=1)`; `rationale: NonEmptyStr`; `confidence: Confidence`. Model validator (order fixed): ① duplicate `symbol` (by `Symbol.code + exchange`) → `duplicate_symbol`; ② `abs(sum(target_weight) + cash_weight - 1) > WEIGHT_SUM_TOLERANCE` → `weight_sum_violation` — **never renormalized**; ③ `sum(target_weight) > 1 + WEIGHT_SUM_TOLERANCE` → `leverage_or_short` (long-only v1; shorts are already impossible via `ge=0`, negatives via strict `FiniteFloat` bounds; NaN/Inf via `FiniteFloat`).

**Required invariants:**

1. the proposal is the **only** LLM-writable payload in this phase; it contains zero envelope fields, and `extra="forbid"` rejects any attempt to smuggle `intent_id`/`authority`/`scheduled_for`/etc. into it;
2. weight-sum acceptance is exact at the boundary: `sum + cash == 1 ± 1e-8` passes; `1e-7` off fails; a passing proposal's weights re-read byte-identical (no normalization side channel);
3. duplicate symbol, NaN/Inf (rejected by `FiniteFloat`), negative weight, weight above 1, and aggregate leverage all fail construction — i.e. **before** any staging or envelope wrapping;
4. semantic digest is stable across construction order and excludes nothing (all proposal fields are semantic);
5. the completeness firewall discovers `shadow.py` and both new classes are classified per the implemented upstream mechanism (Task 0 clause) — no unreviewed public contract escapes.

- [ ] **Step 1: Write failing contract tests**

Cover the validation matrix above (each closed reason code observable), envelope-field smuggling rejection, boundary tolerance vectors (`1 - 1e-9` accepted / `1 - 1e-7` rejected), digest stability, and firewall classification.

Run now: `pytest tests/orchestration/test_shadow_contracts.py -v`

Expected: FAIL on missing `guanlan_v2.orchestration.shadow` module/classes.

- [ ] **Step 2: Implement `shadow.py` proposal half + firewall classification**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_contracts.py tests/orchestration/test_contract_completeness.py -v` — Expected: PASS (the deferred-payload guard still passes at this point: `shadow.py` is not in `PHASE1_MODULES`, and the four names are not yet registered anywhere).

```bash
git add guanlan_v2/orchestration/shadow.py tests/orchestration/test_shadow_contracts.py tests/orchestration/test_contract_completeness.py
git commit -m "feat(orchestration): phase6 shadow proposal contracts with strict long-only validation"
```

---

## Task 2: `DecisionSchedule` + registry + unique time computation

**Files:**
- Modify: `guanlan_v2/orchestration/shadow.py`
- Test: `tests/orchestration/test_decision_schedule.py`

**Consumes:** Phase 1 `DigestModel`, `LogicalId`, `NonEmptyStr`, `DigestHex`, `ContentRef` (refs.py:107), `content_digest`; Phase 3 `TradingCalendar` protocol; `zoneinfo.ZoneInfo`.

**Produces (spec §8 lines 823-834 field names preserved; `time`/`date` fields upgraded to canonicalizable strict string types because `sha256+cjson-v1` rejects `datetime.time`/`datetime.date`):**

- `LocalTimeStr = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]`; `IsoDateStr = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]` — module-level strict aliases.
- `SHADOW_MATCHING_ENGINE_VERSION: str = "shadow-match-v1"` — the only matching-engine version this phase implements; it supports `bar_frequency="1d"` only and pins the v1 A-share session anchors `ASHARE_SESSION_OPEN: str = "09:30"`, `ASHARE_SESSION_CLOSE: str = "15:00"`.
- `class DecisionSchedule(DigestModel)` — `schema_version: Literal["1"] = "1"`; `id: LogicalId`; `version: NonEmptyStr`; `calendar_id: NonEmptyStr`; `timezone: NonEmptyStr` (must resolve via `ZoneInfo`, validator-enforced); `kind: Literal["daily","weekly","rebalance_dates","manual"]`; `decision_local_time: LocalTimeStr`; `cutoff_local_time: LocalTimeStr`; `bar_frequency: Literal["1d","60m","30m","15m","5m","1m"]`; `execution_policy: Literal["next_open","next_bar_close"]`; `execution_price_field: Literal["open","close"]`; `matching_engine_version: NonEmptyStr`; `weekdays: tuple[int, ...] = ()` (each `Field(ge=1, le=7)` ISO weekday, unique, sorted); `rebalance_dates: tuple[IsoDateStr, ...] = ()` (unique, sorted); `intrabar_exit_priority: Literal["worst_case","stop_first","take_profit_first"] = "worst_case"`; `content_digest: DigestHex` with `SELF_DIGEST_FIELDS = frozenset({"content_digest"})` and a `build(cls, **fields)` classmethod sealing it. Every non-self-digest field is semantic (spec §8 line 936: version/timezone/cutoff/calendar/bar frequency/execution policy/price field/matching engine version/intrabar priority all enter the digest). Model validators: `cutoff_local_time <= decision_local_time`; pairing `execution_policy=="next_open" ⇔ execution_price_field=="open"` and `"next_bar_close" ⇔ "close"`; kind matrix — `weekly` requires non-empty `weekdays` and empty `rebalance_dates`, `rebalance_dates` requires the converse, `daily`/`manual` require both empty.
- `class ScheduleComputationError(ShadowContractError)`; `class UnsupportedBarFrequencyError(ScheduleComputationError)`.
- `def is_decision_point(schedule: DecisionSchedule, *, session_date: IsoDateStr, calendar: TradingCalendar) -> bool` — pure: `calendar.calendar_id` must equal `schedule.calendar_id` (mismatch raises `ScheduleComputationError`); non-session dates are never decision points; `daily`→every session, `weekly`→ISO weekday ∈ `weekdays`, `rebalance_dates`→membership, `manual`→every session (the caller supplies the point explicitly).
- `def compute_scheduled_for(schedule: DecisionSchedule, *, session_date: IsoDateStr, calendar: TradingCalendar) -> UtcDateTime` — pure/deterministic: requires `is_decision_point(...)` true; returns the UTC instant of `decision_local_time` in `schedule.timezone` on `session_date`.
- `def compute_cutoff_at(schedule: DecisionSchedule, *, session_date: IsoDateStr) -> UtcDateTime` — the UTC instant of `cutoff_local_time` on `session_date`: the upstream data/entry freeze that precedes (or equals) the decision instant, per the ruled time model `cutoff_at <= decision_as_of < eligible_execution_at`.
- `def compute_eligible_execution_at(schedule: DecisionSchedule, *, scheduled_for: UtcDateTime, calendar: TradingCalendar) -> UtcDateTime` — pure/deterministic: raises `UnsupportedBarFrequencyError` unless `bar_frequency == "1d"` under `SHADOW_MATCHING_ENGINE_VERSION`; the execution session is the first calendar session **strictly after** the `scheduled_for` local date; `next_open` → that session's `ASHARE_SESSION_OPEN` local instant → UTC; `next_bar_close` → that session's `ASHARE_SESSION_CLOSE` → UTC.
- `class ScheduleConflictError(ShadowContractError)`; `class UnknownScheduleError(ShadowContractError)`; `class ScheduleRegistrySealedError(ShadowContractError)`.
- `class DecisionScheduleRegistry` (service class, not a model, mirroring the Phase 1 `SchemaRegistry` shape): `def register(self, schedule: DecisionSchedule) -> ContentRef` (key `(id, version)`; idempotent for identical content digest; `ScheduleConflictError` for same key different digest; raises when sealed); `def seal(self) -> None`; `@property sealed -> bool`; `def resolve(self, ref: ContentRef) -> DecisionSchedule` (verifies the **triple**: `ref.id == schedule.id`, `ref.version == schedule.version`, `ref.content_digest == schedule.content_digest`; any mismatch → `UnknownScheduleError` — the id/version/digest triple is same-present-or-same-absent **structurally**, because `ContentRef` requires all three fields); `def manifest(self) -> tuple[ContentRef, ...]` (sorted by `(id, version)`); `@property registry_digest -> DigestHex` (`content_digest(list(manifest()))`, registration-order independent).

**Required invariants:**

1. digest moves when and only when a digest-bearing field changes: two schedules differing only in `intrabar_exit_priority`, `timezone`, `cutoff_local_time`, `matching_engine_version` or `execution_policy` have different `content_digest`;
2. `scheduled_for`/`eligible_execution_at` are injective per schedule point and reproducible: same `(schedule, session_date, calendar)` → identical instants across calls and processes; a non-session date, a non-decision-point date, or a calendar-id mismatch raises;
3. `eligible_execution_at > scheduled_for` always (execution session strictly after the decision session);
4. pairing violations (`next_open` + `close`, `next_bar_close` + `open`) fail at construction;
5. registry resolve verifies the full triple — a `ContentRef` with the right `(id, version)` but stale digest is refused, so no schedule can be silently swapped under an intent;
6. `bar_frequency != "1d"` schedules construct fine (schema-valid for later phases) but `compute_eligible_execution_at` refuses them honestly under `shadow-match-v1`.

- [ ] **Step 1: Write failing schedule tests** — validation matrix, kind matrix, pairing, digest sensitivity vectors, deterministic time computation against a fake in-memory `TradingCalendar` (fixed session list, `Asia/Shanghai`), registry conflict/idempotency/seal/triple-verification, unsupported-frequency refusal.

Run now: `pytest tests/orchestration/test_decision_schedule.py -v` — Expected: FAIL on missing contracts.

- [ ] **Step 2: Implement**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_decision_schedule.py tests/orchestration/test_shadow_contracts.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/shadow.py tests/orchestration/test_decision_schedule.py
git commit -m "feat(orchestration): DecisionSchedule registry + unique schedule-time computation"
```

---

## Task 3: Runtime-only `TargetPortfolioIntent` envelope + idempotency key families

**Files:**
- Modify: `guanlan_v2/orchestration/shadow.py`
- Test: `tests/orchestration/test_shadow_envelope.py`

**Consumes:** Task 1/2 contracts; Phase 1 `Artifact` (schemas.py:239), `OrchestrationRequest` (spec.py:218), `UtcDateTime`, `PositiveInt`; Phase 2 `AuthoritativeClock`; Phase 3 `TradingCalendar`.

**Produces (spec §8 lines 810-821 field names preserved):**

- `class TargetPortfolioIntent(DigestModel)` — `schema_version: Literal["1"] = "1"`; `intent_id: NonEmptyStr`; `target_version: PositiveInt`; `proposal_artifact_id: NonEmptyStr`; `proposal_digest: DigestHex`; `source_decision_artifact_id: NonEmptyStr`; `decision_schedule_id: LogicalId`; `decision_schedule_version: NonEmptyStr`; `decision_schedule_digest: DigestHex`; `scheduled_for: UtcDateTime`; `decision_as_of: UtcDateTime`; `eligible_execution_at: UtcDateTime`; `valid_until: UtcDateTime | None = None`; `positions: tuple[TargetPosition, ...]`; `cash_weight: FiniteFloat = Field(ge=0, le=1)`; `origin: Literal["LLM"] = "LLM"`; `authority: Literal["ADVISORY_ONLY"] = "ADVISORY_ONLY"`; `execution_scope: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"`; `rationale: NonEmptyStr`; `confidence: Confidence`; `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"intent_id", "created_at"})` (runtime-random id and wall clock are audit facts per spec §8 line 931; every business field including the schedule triple and all times is semantic). Model validators: the same portfolio matrix as `PortfolioTargetProposal` (duplicate/weight-sum/leverage — an intent can never be laxer than a proposal); `decision_as_of < eligible_execution_at`; `scheduled_for <= eligible_execution_at`; `valid_until is None or eligible_execution_at <= valid_until`.
- `class ShadowEnvelopeError(ShadowContractError)`.
- `def wrap_proposal_as_intent(*, proposal_artifact: Artifact, source_decision_artifact_id: NonEmptyStr, request: OrchestrationRequest, schedule_registry: DecisionScheduleRegistry, calendar: TradingCalendar, session_date: IsoDateStr, decision_as_of: UtcDateTime, target_version: PositiveInt, intent_id: NonEmptyStr, clock: AuthoritativeClock, valid_until: UtcDateTime | None = None) -> TargetPortfolioIntent` — the **sole** intent constructor path. Behavior (closed order): ① `proposal_artifact.payload_schema_ref` must be `PortfolioTargetProposal@1` and the payload re-validates as `PortfolioTargetProposal` (`ShadowEnvelopeError` otherwise); ② `request.decision_schedule_ref` must be present (`ShadowEnvelopeError` — "any request producing a shadow intent must bind a registered schedule", spec §8 line 932) and `schedule_registry.resolve(request.decision_schedule_ref)` must succeed (triple-verified); ③ `scheduled_for = compute_scheduled_for(schedule, session_date=session_date, calendar=calendar)`; `eligible_execution_at = compute_eligible_execution_at(schedule, scheduled_for=scheduled_for, calendar=calendar)`; ④ `compute_cutoff_at(schedule, session_date=session_date) <= decision_as_of` enforced (`ShadowEnvelopeError`) — time model ruled 2026-07-16: `cutoff ≤ decision_as_of < eligible_execution_at` (cutoff = upstream data/entry freeze at `cutoff_local_time`; `decision_as_of` = the `decision_local_time` instant, matching the Task 2 schedule validator ordering); Phase 9 replay consumes this; ⑤ envelope assembled entirely from these runtime arguments: `proposal_artifact_id = proposal_artifact.artifact_id`, `proposal_digest = proposal_artifact.content_digest`, schedule triple from the resolved schedule, `created_at = clock.now()`; positions/cash_weight/rationale/confidence copied **verbatim** from the proposal payload.
- Key-family builders (pure; all return `DigestHex` via Phase 1 `content_digest` over a domain-tagged mapping with canonical UTC strings):
  - `SHADOW_APPLY_KEY_DOMAIN: str = "shadow-apply-key-v1"`; `def target_apply_key(intent: TargetPortfolioIntent) -> DigestHex` — over exactly `{"domain", "intent_id", "scheduled_for", "target_version"}`;
  - `SHADOW_ORDER_ID_DOMAIN: str = "shadow-order-id-v1"`; `def shadow_order_id(*, apply_key: DigestHex, symbol: Symbol, order_kind: ShadowOrderKind, trigger_bar: NonEmptyStr, ordinal: NonNegativeInt) -> DigestHex` — over exactly `{"domain", "target_apply_key", "symbol" (Symbol.dotted), "order_kind", "trigger_bar", "ordinal"}`;
  - `SHADOW_FILL_ID_DOMAIN: str = "shadow-fill-id-v1"`; `def shadow_fill_id(*, order_id: DigestHex, fill_seq: PositiveInt) -> DigestHex` — over exactly `{"domain", "order_id", "fill_seq"}`.
  - `ShadowOrderKind = Literal["target_buy", "target_sell", "stop_loss", "take_profit", "max_hold_exit"]` — the closed order-kind vocabulary of the key family.

**Required invariants:**

1. **the model can never self-report the envelope**: a proposal payload carrying any envelope key fails at Task 1's `extra="forbid"`; the intent's `origin`/`authority`/`execution_scope` reject every non-default value at construction (structural Literals); there is no public constructor path that accepts caller-supplied `scheduled_for`/`eligible_execution_at` — both are computed inside `wrap_proposal_as_intent`;
2. a request without `decision_schedule_ref`, an unregistered schedule, a digest-stale schedule ref, a non-decision-point `session_date`, or `decision_as_of` earlier than `compute_cutoff_at(...)` (the ruled ordering is `cutoff <= decision_as_of`) each raise `ShadowEnvelopeError` with distinct reason text — no intent object is ever produced on these paths;
3. intents are frozen: attribute assignment after construction raises (pydantic frozen), so "seeing realized results" cannot mutate an intent in place;
4. key families are deterministic and discriminating: same inputs → identical keys; changing any single component (`scheduled_for` by one second, `target_version` by one, `ordinal`, `trigger_bar`, `fill_seq`) changes the key; keys survive round-trips through JSON;
5. semantic digest of an intent excludes exactly `intent_id`/`created_at`: two intents identical except those two have equal `semantic_digest()` but distinct apply keys (apply identity is operational — keyed on `intent_id` — while content identity is semantic; both facts are asserted).

- [ ] **Step 1: Write failing envelope tests** — full matrix above, using a real Phase 1 `Artifact.build` around a valid proposal, a fake calendar, a fixed `AuthoritativeClock`, and an `OrchestrationRequest` with/without `decision_schedule_ref`.

Run now: `pytest tests/orchestration/test_shadow_envelope.py -v` — Expected: FAIL on missing intent/builder/keys.

- [ ] **Step 2: Implement**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_envelope.py tests/orchestration/test_shadow_contracts.py tests/orchestration/test_decision_schedule.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/shadow.py tests/orchestration/test_shadow_envelope.py
git commit -m "feat(orchestration): runtime-only TargetPortfolioIntent envelope + shadow idempotency key families"
```

---

## Task 4: Shadow run records + `CorporateActionEvent` contracts

**Files:**
- Modify: `guanlan_v2/orchestration/shadow.py`
- Test: `tests/orchestration/test_shadow_records.py`

**Consumes:** Tasks 1–3; Phase 1 strict types, `ContentRef`, `Symbol`.

**Produces (all `DigestModel`, `schema_version: Literal["1"] = "1"` unless noted):**

- `class ShadowTargetApplyRecord(DigestModel)` — `target_apply_key: DigestHex`; `intent_content_digest: DigestHex` (semantic identity of the applied intent); `intent_id: NonEmptyStr` (causation, `SEMANTIC_EXCLUDE`); `scheduled_for: UtcDateTime`; `target_version: PositiveInt`; `trigger_bar: NonEmptyStr` ('YYYY-MM-DD' execution bar); `order_ids: tuple[DigestHex, ...]`; `applied: bool` (False = eligible bar never tradable inside the run window; honest non-application, not an error).
- `class ShadowOrderRecord(DigestModel)` — `order_id: DigestHex`; `target_apply_key: DigestHex`; `symbol: Symbol`; `order_kind: ShadowOrderKind`; `trigger_bar: NonEmptyStr`; `ordinal: NonNegativeInt`; `side: Literal["buy","sell"]`; `otype: Literal["limit","market","stop"]`; `limit_price: FiniteFloat | None`; `qty: PositiveInt | None` (None → cash-budget sized buy, mirroring engine `Order.qty` semantics); `cash_budget: FiniteFloat | None`. Model validator: `order_id == shadow_order_id(...)` recomputed from its own components (self-consistent key; forged ids fail).
- `class ShadowFillRecord(DigestModel)` — `fill_id: DigestHex`; `order_id: DigestHex`; `fill_seq: PositiveInt`; `symbol: Symbol`; `side: Literal["buy","sell"]`; `qty: PositiveInt`; `price: FiniteFloat`; `trade_date: NonEmptyStr`; `gross: FiniteFloat`; `cost: FiniteFloat`; `reason: NonEmptyStr` (engine fill reason, e.g. `"target_buy"`/`"stop_loss"`). Validator: `fill_id == shadow_fill_id(order_id=order_id, fill_seq=fill_seq)`.
- `class ShadowRejectRecord(DigestModel)` — `order_id: DigestHex`; `symbol: Symbol`; `trade_date: NonEmptyStr`; `reason: NonEmptyStr` (verbatim `Broker.last_reason`: `"suspended"`, `"one_word_limit_up"`, `"below_one_lot"`, `"t1_locked_or_empty"`, …).
- `class CorporateActionEvent(DigestModel)` — `symbol: Symbol`; `kind: Literal["cash_dividend","stock_bonus","split"]`; `ex_date: IsoDateStr`; `cash_per_share: FiniteFloat = Field(ge=0)` (must be > 0 iff kind == "cash_dividend", else exactly 0); `shares_ratio: FiniteFloat = Field(ge=0)` (stock_bonus: additional shares per held share, > 0; split: new shares per old share, > 0 and ≠ 1; cash_dividend: exactly 0 — matrix validator-enforced); `available_at: UtcDateTime` (PIT fact — structurally required, no silent missing-availability).
- `class ShadowRunResult(DigestModel)` — `matching_engine_version: NonEmptyStr`; `schedule_ref: ContentRef`; `start: IsoDateStr`; `end: IsoDateStr`; `init_cash: FiniteFloat = Field(gt=0)`; `cost_model_digest: DigestHex` (content digest of the CostModel parameter mapping); `intent_content_digests: tuple[DigestHex, ...]`; `applies: tuple[ShadowTargetApplyRecord, ...]`; `orders: tuple[ShadowOrderRecord, ...]`; `fills: tuple[ShadowFillRecord, ...]`; `rejects: tuple[ShadowRejectRecord, ...]`; `nav_history: tuple[tuple[NonEmptyStr, FiniteFloat], ...]`; `metrics: dict[NonEmptyStr, FiniteFloat] = {}` (keys ⊆ `{"ann_return","sharpe","max_drawdown","volatility","turnover","win_rate","calmar","trade_win_rate","profit_factor"}`; non-finite metrics are **omitted**, never smuggled as sentinels); `n_trades: NonNegativeInt = 0` (dedicated strict-int field — counts are never floats and never enter the float metrics dict); `warnings: tuple[NonEmptyStr, ...] = ()`; `badges: tuple[NonEmptyStr, ...] = ()`; `content_digest: DigestHex` self-sealed (`SELF_DIGEST_FIELDS`), `build(cls, **fields)` classmethod. Cross-record validator: every fill's `order_id` and every apply's `order_ids` entries appear in `orders`; every order's `target_apply_key` appears in `applies` — causation keys are closed over the record set.

**Required invariants:**

1. results reference intents **only** by `intent_content_digests` — a `ShadowRunResult` cannot embed or mutate an intent;
2. key self-consistency validators make forged order/fill ids unconstructible;
3. the corporate-action kind/field matrix is closed (wrong combination fails);
4. dangling causation (a fill whose order is absent, an order whose apply is absent) fails construction;
5. all records are frozen and canonically digestible.

- [ ] **Step 1: Write failing record tests** — field/kind matrices, key self-consistency (tamper one component → fail), causation closure, metrics key vocabulary, omission of non-finite metrics, strict-int `n_trades` (a float value is rejected).

Run now: `pytest tests/orchestration/test_shadow_records.py -v` — Expected: FAIL on missing contracts.

- [ ] **Step 2: Implement**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_records.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/shadow.py tests/orchestration/test_shadow_records.py
git commit -m "feat(orchestration): shadow apply/order/fill/reject/run-result + corporate-action contracts"
```

---

## Task 5: Target-portfolio diff step + `ShadowDecisionAgent`

**Files:**
- Create: `guanlan_v2/orchestration/adapters/__init__.py`
- Create: `guanlan_v2/orchestration/adapters/luozi.py`
- Test: `tests/orchestration/test_shadow_diff.py`
- Test: `tests/orchestration/test_shadow_agent.py`

**Consumes:** Tasks 1–4; engine `DecisionLeg`/`Decision`/`DecisionInput` (decision.py:60-88) read-only. The diff step **does not exist anywhere today** (grounding gotcha 13: `legs_to_orders` normalizes buy weights only over the same batch and ignores held weights) — Phase 6 builds it.

**Produces (in `adapters/luozi.py`):**

- Internal frozen carriers (classified internal per the firewall mechanism, not registered): `class ShadowOrderPlanEntry(ContractModel, frozen)` — `symbol: Symbol`; `order_kind: ShadowOrderKind`; `side: Literal["buy","sell"]`; `qty: PositiveInt | None`; `cash_budget: FiniteFloat | None`; `ordinal: NonNegativeInt`; `class ShadowOrderSkip(ContractModel, frozen)` — `symbol: Symbol`; `reason: NonEmptyStr` (closed set `{"no_reference_price", "below_lot_resolution", "already_at_target"}`); `class ShadowOrderPlan(ContractModel, frozen)` — `entries: tuple[ShadowOrderPlanEntry, ...]`; `skipped: tuple[ShadowOrderSkip, ...]`.
- `def diff_target_portfolio(intent: TargetPortfolioIntent, *, holdings: Mapping[str, int], reference_prices: Mapping[str, float], nav: float, lot_size: int = 100) -> ShadowOrderPlan` — pure/deterministic. Rules (closed): target qty per position = `floor((target_weight * nav / price) / lot_size) * lot_size` (with `lot_size=1` this degenerates to whole-share fractional-free sizing for the compat path); held symbols absent from the target → full `target_sell`; `delta < 0` → `target_sell` of `-delta`; `delta > 0` → `target_buy` of `delta` with `cash_budget = delta * price`; `delta == 0` → `already_at_target` skip; missing reference price → `no_reference_price` skip (honest — never a fabricated price); lot-floored target of 0 while `target_weight > 0` → `below_lot_resolution` skip. Ordering is canonical and completion-order-free: all sells sorted by `Symbol.code`, then all buys sorted by `Symbol.code`; `ordinal` increments 0.. across the whole plan in that order. **No weight renormalization anywhere.**
- `class ShadowDecisionAgent` — the zero-LLM, engine-shaped consumer (grounding gotcha 5: anything plugged where `BacktestRunner` expects an agent must expose `n_calls` and async `decide`): `NAME: str = "shadow-intent-agent"`; `def __init__(self, *, intents: tuple[TargetPortfolioIntent, ...], calendar: TradingCalendar, schedule: DecisionSchedule, lot_size: int = 100) -> None` (rejects two intents sharing a `target_apply_key` with different semantic digests — `ShadowApplyConflict`; identical duplicates are collapsed); `@property def n_calls(self) -> int` — always `0`; `async def decide(self, inp: DecisionInput) -> Decision` — looks up the unique frozen intent whose `eligible_execution_at` falls on `inp.date`'s session; none → all-`hold` `Decision`; otherwise runs `diff_target_portfolio` against `inp.holdings`/`inp.nav` and maps plan entries to `DecisionLeg`s by the closed table: full sell → `action="sell"`; partial sell → `action="reduce"`; buy → `action="buy"` with `weight_pct = delta_weight * 100`, `stop_loss` from the matching `TargetPosition.stop_loss_pct` (as absolute price = reference price × (1 − pct)), `reason` = `"shadow-intent:" + intent.semantic_digest()[:16]` (causation visible). The agent never mutates its intents and never fabricates an intent for a date without one.
- `class ShadowApplyConflict(ShadowContractError)`.

**Required invariants:**

1. diff determinism: same `(intent, holdings, prices, nav)` → identical plan including ordinals, independent of mapping iteration order;
2. sells always precede buys (cash is freed before spent);
3. a diff over an already-conforming portfolio yields zero entries and only `already_at_target` skips (idempotent target application at the plan level);
4. the agent conforms to the engine agent shape (`NAME`, `n_calls == 0` before and after `decide`, `decide` is a coroutine returning engine `Decision`) — proven by duck-type assertions against the real engine classes, without instantiating `BacktestRunner`;
5. the agent is total: any `DecisionInput` date yields a valid `Decision` (all-hold default), never an exception for missing intents;
6. **runner-consumed only** (red line): the agent is consumed exclusively by `ShadowBacktestRunner` and must never be executed under the engine `BacktestRunner`'s `legs_to_orders` path — engine.py:165 re-normalizes buy `weight_pct` over the same batch and ignores held weights, which would silently renormalize shadow targets (the exact silent-normalization red line). Asserted by an explicit invariant test: `guanlan_v2.orchestration.adapters.luozi` neither imports nor references `BacktestRunner`/`legs_to_orders` (source/import-graph assertion), and the hazard is documented on the agent (docstring citing engine.py:165).

- [ ] **Step 1: Write failing diff/agent tests** — determinism/ordering vectors, full/partial sell mapping, lot flooring, skips, conflict on same-key-different-content intents, engine-shape conformance, all-hold default, runner-consumed-only guard (no `BacktestRunner`/`legs_to_orders` reference in `adapters/luozi.py`).

Run now: `pytest tests/orchestration/test_shadow_diff.py tests/orchestration/test_shadow_agent.py -v` — Expected: FAIL on missing `guanlan_v2.orchestration.adapters.luozi`.

- [ ] **Step 2: Implement `adapters/__init__.py` + the diff/agent half of `adapters/luozi.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_diff.py tests/orchestration/test_shadow_agent.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/adapters/__init__.py guanlan_v2/orchestration/adapters/luozi.py tests/orchestration/test_shadow_diff.py tests/orchestration/test_shadow_agent.py
git commit -m "feat(orchestration): target-portfolio diff step + engine-shaped ShadowDecisionAgent"
```

---

## Task 6: `ShadowBacktestRunner` — apply-once loop over the engine Broker baseline

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py`
- Test: `tests/orchestration/test_shadow_runner.py`

**Consumes:** engine `Broker` (+ `Order`), `VirtualPortfolio`, `CostModel`, `prepare_bar`, `compute_metrics`, `TradeLog`, `PitReader.trading_days` — all read-only; Tasks 3–5. Explicitly **not** consumed: `BacktestRunner.run`'s daily `agent.decide()` entry (spec §1 reuse boundary line 73), `legs_to_orders` (target semantics differ), seats ledger (grounding gotcha 15).

**Produces:**

- `class ShadowBacktestRunner` — `def __init__(self, *, reader, loader, schedule: DecisionSchedule, schedule_ref: ContentRef, calendar: TradingCalendar, cost_model: CostModel, init_cash: float = 1_000_000.0, corporate_actions: tuple[CorporateActionEvent, ...] = (), is_st: Mapping[str, bool] | None = None, lot_size: int = 100) -> None` — validates `schedule_ref` against `schedule` (triple), `schedule.matching_engine_version == SHADOW_MATCHING_ENGINE_VERSION`, `bar_frequency == "1d"` (else `UnsupportedBarFrequencyError`); `def run(self, intents: tuple[TargetPortfolioIntent, ...], *, start: IsoDateStr, end: IsoDateStr) -> ShadowRunResult` — synchronous (zero LLM ⇒ no coroutine).
- **Deterministic dual-curve lane (reviewed additive runner entry, ruled 2026-07-16 — this plan owns the runner; Phase 9's dual-curve replay is the consumer):**
  - `class DeterministicTargetSet(ContractModel, frozen)` (internal carrier, classified per the firewall mechanism): `rule_id: NonEmptyStr`; `point_ordinal: NonNegativeInt`; `target_version: PositiveInt`; `session_date: IsoDateStr` (the decision point); `positions: tuple[TargetPosition, ...]`; `cash_weight: FiniteFloat = Field(ge=0, le=1)` — validated by the exact Task 1 duplicate/weight-sum/leverage matrix. Produced **without** any LLM or intent envelope: it carries no `origin`/`authority`/`execution_scope` and can never become a `TargetPortfolioIntent`.
  - `SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN: str = "shadow-deterministic-apply-key-v1"`; `def deterministic_apply_key(target_set: DeterministicTargetSet) -> DigestHex` — over exactly `{"domain", "rule_id", "point_ordinal", "target_version"}`: its **own apply-key family**; the distinct domain tag makes collision with the intent family (`target_apply_key`) structurally impossible.
  - `def run_targets(self, target_sets: tuple[DeterministicTargetSet, ...], *, run_config, calendar, clock) -> ShadowRunResult` — the deterministic dual-curve entry. `run_config`/`calendar`/`clock` must be identical to the lane config `run(intents)` executes under (`calendar.calendar_id` match; `run_config` = the same `(start, end, init_cash, cost_model, corporate_actions, is_st, lot_size)` bundle, pinned by the **same config digest** — `cost_model_digest` included — as `run(intents)`); any mismatch raises `ShadowContractError`, so the two lanes can never run under different matching engines, cost models, calendars or clocks. Each target set applies at its `session_date`'s eligible execution bar through the identical per-day loop below, deduped on `deterministic_apply_key`. Apply records reuse `ShadowTargetApplyRecord` with `target_apply_key = deterministic_apply_key(target_set)`, `intent_content_digest` = the target set's semantic digest (the deterministic causation digest) and `intent_id = "{rule_id}#{point_ordinal}"` (audit-only per Task 4). Provenance is **origin-free** — no record in a `run_targets` result ever carries `origin="LLM"` — and the method is barred from constructing any `TargetPortfolioIntent`; the result's `intent_content_digests` stays `()`.

Closed per-day loop (documented as the normative order; every sub-step keyed to the engine invariant it reuses):

1. **calendar**: days = `reader.trading_days(start, end)`; `portfolio.seed_initial_nav(start)` before the first bar (portfolio.py:198 — required for correct max-drawdown);
2. **corporate actions**: apply all events with `ex_date == T` via Task 7's `apply_corporate_actions` **before** any matching that day, exactly once per event digest;
3. **target application**: if some intent's `eligible_execution_at` falls in T's session and its `target_apply_key` is not yet in the runner's applied-key set → `diff_target_portfolio` against the live `VirtualPortfolio` snapshot with T's reference prices → for each plan entry, build the engine `Order` (`target_sell` → limit sell at the ex-div-corrected 跌停价 floor `dn`, the engine's own sell convention engine.py:180; `target_buy` → limit buy with the engine ceiling `prev_close * (1 + pct/2)` engine.py:202 and `cash_budget` sizing) → `prepare_bar(code, T, reader, loader, cfg)` supplies `(bar, ref_prev_close)` → `broker.match(order, bar, ref_prev_close, portfolio, is_st=is_st.get(code, False))`; a `Fill` becomes a `ShadowFillRecord` (`fill_seq=1` — engine fills are all-or-none; the key family still carries `fill_seq` so multi-fill engines dedup identically later); `None` becomes a `ShadowRejectRecord` with verbatim `broker.last_reason`. Record one `ShadowTargetApplyRecord` (with `applied=True`); duplicate intents (same apply key, same digest) are no-ops returning the original record; same key different digest → `ShadowApplyConflict`. If the eligible bar lies outside `[start, end]` the intent is recorded `applied=False` — never silently re-timed;
4. **exit management** (Task 7 supplies stop/take/max-hold order generation for held positions);
5. **EOD**: `portfolio.record_nav(T, prices=eod_closes)` (mark+record atomic, portfolio.py:210); suspended names keep previous `mkt_value` (engine semantics, portfolio.py:154).

Result assembly: `nav_history` from the portfolio; `metrics` from engine `compute_metrics` + `TradeLog.trade_stats`, non-finite values omitted (`n_trades` populates the dedicated strict-int field on `ShadowRunResult` — counts are never floats); `badges` include `"st_flags_unavailable"` when `is_st` is empty/None and at least one order matched, and `"corporate_actions_synthetic"` when the events tuple was caller-supplied rather than data-layer-sourced (always true in Phase 6); `cost_model_digest = content_digest` of the CostModel field mapping.

**Required invariants:**

1. **apply-once above Broker**: replaying `run` with a duplicated intent tuple produces byte-identical `ShadowRunResult` content digests; the duplicate contributes no second order (grounding gotcha 4 — `Broker.match` mutates, so idempotency lives in the runner);
2. engine semantics pass through unfiltered: a suspended bar rejects `"suspended"`; a one-word limit-up bar rejects buys; a buy sized below 100 shares rejects `"below_one_lot"` (`lot_size=100` path); a same-day re-sell of a T+1-locked buy rejects `"t1_locked_or_empty"`; costs/slippage match `CostModel` arithmetic exactly on a hand-computed vector;
3. every fill/reject retains its causation chain: `fill → order → apply → intent digest` all resolvable inside the result (Task 4 closure validator);
4. `trade_date` strings are `'YYYY-MM-DD'` end-to-end (gotcha 7 — a Timestamp leaking into `Position.locked` breaks T+1);
5. the runner never touches `guanlan_v2.seats`, never performs network I/O, and never mutates the input intents (frozen + identity assertions);
6. determinism: two runs over the same fixture reader/loader produce identical result digests;
7. **dual-lane same-bar equivalence**: a `DeterministicTargetSet` whose `positions`/`cash_weight` are identical to an intent's at the same decision point yields identical fills/costs/rejects through `run_targets` as `run` produces for that intent — only the apply-key family and provenance fields differ;
8. **key-family disjointness**: `deterministic_apply_key(...)` never equals any `target_apply_key(...)` (distinct domain tags, asserted over adversarial inputs), and the two lanes' applied-key sets never intersect;
9. **no intent minted**: `run_targets` constructs no `TargetPortfolioIntent` and records no `origin="LLM"` provenance anywhere in its result (`intent_content_digests == ()` asserted structurally) — the deterministic lane is envelope-free end-to-end; Phase 9's dual-curve replay is its named consumer.

- [ ] **Step 1: Write failing runner tests** — build a small in-memory fixture reader/loader (deterministic OHLCV frames incl. a suspension day, a one-word board day, a T+1 scenario), then cover the invariant list, including the deterministic lane (same-bar equivalence, key-family disjointness, no-intent-minted, config-digest mismatch refusal). The red checkpoint is the missing `ShadowBacktestRunner` contract plus the intended apply-once behavior.

Run now: `pytest tests/orchestration/test_shadow_runner.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement the loop (steps 1/3/5; leave 2/4 as reviewed seams that Task 7 fills — the seam methods exist but raise `NotImplementedError` only until Task 7, and the Task 6 tests do not exercise them)**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_runner.py tests/orchestration/test_shadow_diff.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py tests/orchestration/test_shadow_runner.py
git commit -m "feat(orchestration): ShadowBacktestRunner with apply-once targets over engine Broker baseline"
```

---

## Task 7: Gap-filling — take-profit, max-hold, intrabar priority, corporate-action ledger

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py`
- Test: `tests/orchestration/test_shadow_gaps.py`

**Consumes:** engine Broker order types (`stop` protective sell broker.py:121-127; `limit` sell touch `limit_price <= high` broker.py:129-139) — the engine itself has **no take-profit and no max-hold** (engine.py:15-16; grounding §1.5) and **no corporate-action ledger** (grounding §1.5 NOT-FOUND sweep): all three are built here, above Broker, never inside it.

**Produces:**

- Runner-held per-position exit state (internal): entry trigger bar index, entry price, the governing `TargetPosition` (stop/take/max-hold parameters travel with the position that opened it; a later target that re-buys re-parameterizes).
- **Stop-loss** (engine-native, reused not rebuilt): submitted as `Order(otype="stop", limit_price=stop_px)` — triggers when `bar.low <= stop_px`, fills at `slip_sell(min(stop_px, open))` clipped to `[low, high]` with real costs.
- **Take-profit** (new): when `bar.high >= entry_price * (1 + take_profit_pct)` → `Order(otype="limit", side="sell", limit_price=tp_px)` on the trigger bar — the engine's own limit-sell touch rule fills it intrabar, respecting suspension/one-word/T+1/costs/slippage. No exchange-alien fill path is invented.
- **Max-hold** (new): when bars held since entry ≥ `max_hold_bars` → limit sell at the ex-div-corrected 跌停价 floor `dn` on the expiry bar (the engine's own forced-sell convention, engine.py:180), `order_kind="max_hold_exit"`.
- **Intrabar priority** (schedule-governed, closed): when stop and take both touch the same bar — `"worst_case"` → execute the stop only (for long-only, worst case ≡ stop; documented equivalence); `"stop_first"` → stop only; `"take_profit_first"` → take only. Exactly one exit order is submitted for the double-touch bar; priority comes solely from `schedule.intrabar_exit_priority` (digest-bearing, spec §8 line 936). Max-hold is evaluated only when neither stop nor take triggered that bar.
- `def apply_corporate_actions(portfolio: VirtualPortfolio, events: tuple[CorporateActionEvent, ...], *, on_date: IsoDateStr, applied_digests: set[str]) -> tuple[CorporateActionApplication, ...]` — new ledger mechanism. `class CorporateActionApplication(ContractModel, frozen)` (internal): `event_digest: DigestHex`; `symbol: Symbol`; `cash_credited: FiniteFloat`; `qty_before: NonNegativeInt`; `qty_after: NonNegativeInt`; `avg_cost_before: FiniteFloat`; `avg_cost_after: FiniteFloat`. Closed semantics, applied in `(ex_date, Symbol.code, kind)` order, each event at most once (digest-keyed): `cash_dividend` → `portfolio.cash += qty * cash_per_share`, avg_cost/qty unchanged; `stock_bonus` → `qty_after = floor(qty * (1 + shares_ratio))`; `split` → `qty_after = floor(qty * shares_ratio)`; for both share events `avg_cost_after = avg_cost * qty / qty_after` (cost basis preserved), per-position `stop_loss` price rescaled by `qty / qty_after`, and every `Position.locked` T+1 bucket rescaled with the same floor rule (T+1 never unlocks early via a corporate action). Events for symbols not held are no-ops recorded as zero-delta applications. Applying corporate actions never creates orders and never touches intents.

**Required invariants:**

1. take-profit and max-hold exits demonstrably do **not** exist in the engine (Task 0 item 8) and demonstrably do exist in shadow runs: fixtures where the frontend clock would exit (tp touch, expiry) produce the corresponding `ShadowFillRecord` with `order_kind` `take_profit`/`max_hold_exit`;
2. gap exits still obey engine reality: a take-profit on a suspended bar rejects `"suspended"` and the position persists to the next tradable bar (re-submitted deterministically); a stop through a one-word limit-down bar rejects and re-arms;
3. double-touch bars produce exactly one exit order and its kind follows `intrabar_exit_priority`; flipping the schedule field flips the outcome and the schedule digest;
4. corporate actions: a cash dividend credits exactly `qty * cash_per_share`; bonus/split preserve `qty * avg_cost` within one floor step; locked buckets and stop prices rescale; the same event tuple replayed yields zero additional applications (digest idempotency); NAV is continuous across an ex-date fixture (no phantom drawdown from a 10-送-3);
5. every gap exit order id uses the frozen key family with the **originating** apply key (causation survives exits).

- [ ] **Step 1: Write failing gap tests** — the five invariant groups over deterministic fixtures (tp-touch day, suspension-on-tp day, double-touch bar × three priorities, dividend/bonus/split vectors incl. locked-bucket rescale, replay idempotency).

Run now: `pytest tests/orchestration/test_shadow_gaps.py -v` — Expected: FAIL on the Task 6 seams (`NotImplementedError`) / missing functions.

- [ ] **Step 2: Implement exit management + corporate-action application; remove the Task 6 seams**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_gaps.py tests/orchestration/test_shadow_runner.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py tests/orchestration/test_shadow_gaps.py
git commit -m "feat(orchestration): shadow-side take-profit/max-hold/intrabar-priority + corporate-action ledger"
```

---

## Task 8: Compatibility mirror stage ① — frontend `runBacktest` profile

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py`
- Create: `tests/orchestration/fixtures/shadow_mirror_v1.json`
- Test: `tests/orchestration/test_shadow_mirror.py`

**Consumes:** the documented frontend semantics of `ui/seats/luozi-data.jsx::runBacktest` (luozi-data.jsx:1508-1563) and `metricsOf` (luozi-data.jsx:461-482) — as a **profile**, not as a callable (spec decision 15). The Broker is structurally unable to run this profile (grounding gotcha 2: T+1/limit/lot checks are not config-gated), so the mirror is a separate compatibility fill path.

**Produces:**

- `COMPATIBILITY_PROFILE_ID: str = "luozi-runbacktest-compat-v1"` and the profile facts as module documentation constants: zero cost (no commission/stamp/transfer/slippage), single symbol, same-bar-close fills (`+b.c` of the signal bar), fractional shares (`shares = cash / px`), full-in/full-out (`pos ∈ {0,1}`), no suspension/涨跌停/T+1/手数/reject-ledger, clock exits intrabar at the **exact trigger price** (stop: `entry*(1-stop)`; take: `entry*(1+take)`), same-bar double touch resolves **stop-first** (frontend hard-coding, luozi-data.jsx:1535), max-hold exits at bar close with reason `'到期'`, exits only when `i > entryIdx`, pre-entry equity flat `1.0`, all-watch runs return `None`, an open position at the end yields a synthetic `openEnd` trade.
- **Declared numeric tolerances (the spec leaves these to this plan — §13 line 1013); module constants, used by every mirror assertion:**
  - `COMPAT_TRADE_STRUCTURE_TOL: int = 0` — trade count, entry/exit bar indices, `firstSig` index, exit `reason` strings and `openEnd` flags match **exactly**;
  - `COMPAT_PRICE_REL_TOL: float = 1e-9` — entry/exit prices (both sides compute IEEE-754 doubles with identical operation order);
  - `COMPAT_RETURN_ABS_TOL: float = 1e-9` — per-trade returns;
  - `COMPAT_EQUITY_REL_TOL: float = 1e-9` — every `eqSeg` curve point;
  - `COMPAT_METRIC_REL_TOL: float = 1e-6` — metrics (annualization stacks more floating ops);
  - Metrics are recomputed **one way** (grounding gotcha 10): the mirror's own `compat_metrics` reimplements the frontend `metricsOf` conventions (`perDay = 48 if freq=='5min' else 1`, sharpe annualized by `sqrt(252*perDay)`, `years = (n/perDay)*1.4/365`) and both fixture expectation and mirror output flow through it; engine `compute_metrics` is never compared against `metricsOf`.
  - The frontend switch-over date is **explicitly deferred to Phase 9's decommission gates**; stage ① only freezes profile + tolerances.
- `class CompatSignal(ContractModel, frozen)` (internal) — `idx: NonNegativeInt` (bar index); `side: Literal["buy","sell","watch"]`.
- `class CompatClock(ContractModel, frozen)` (internal) — `stop_loss: FiniteFloat | None`; `take_profit: FiniteFloat | None`; `max_hold: PositiveInt | None` (fractions/bars, frontend clock fields luozi-data.jsx:1512-1514).
- `class CompatTrade(ContractModel, frozen)` (internal) — `entry: FiniteFloat`; `exit: FiniteFloat`; `ret: FiniteFloat`; `in_idx: NonNegativeInt`; `out_idx: NonNegativeInt`; `reason: Literal["止损","止盈","到期","信号"]`; `open_end: bool = False`.
- `class CompatibilityRunResult(ContractModel, frozen)` (internal) — `eq_seg: tuple[FiniteFloat, ...]`; `trades: tuple[CompatTrade, ...]`; `first_sig: NonNegativeInt`; `metrics: dict[str, float]`.
- `def run_compatibility_mirror(signals: tuple[CompatSignal, ...], bars: tuple[Mapping[str, float], ...], clock: CompatClock | None = None) -> CompatibilityRunResult | None` — the byte-faithful Python replication of `runBacktest(runDecs, bars, useHybrid, clock)` fill semantics (the `useHybrid` branch only changes which frontend field supplies the side; the mirror consumes the already-derived side). Returns `None` for all-watch (honest no-curve).
- `class CompatibilityProfileError(ShadowContractError)`; `def map_intents_to_compat_signals(intents: tuple[TargetPortfolioIntent, ...], *, bars: tuple[Mapping[str, float], ...], calendar: TradingCalendar) -> tuple[tuple[CompatSignal, ...], CompatClock | None]` — an intent maps to the profile only when it has exactly one position with `target_weight == 1.0` (→ buy at its eligible bar) or zero positions with `cash_weight == 1.0` (→ sell); clock fields come from the single `TargetPosition`'s `stop_loss_pct/take_profit_pct/max_hold_bars`; the mirror only accepts `intrabar_exit_priority == "stop_first"` schedules (the frontend hard-codes stop-first; a `worst_case`/`take_profit_first` schedule raises `CompatibilityProfileError` rather than silently reinterpreting — reconciling grounding gotcha 1 explicitly). Any other portfolio shape raises `CompatibilityProfileError` — the profile never coerces (no false gate on behavior the frontend lacks, and no silent widening either).
- Fixture `tests/orchestration/fixtures/shadow_mirror_v1.json`: fixed bars/signals/clock cases with expected `trades/eqSeg/firstSig/metrics` vectors, each hand-derived from the cited luozi-data.jsx line semantics with the derivation documented in the fixture's `"derivation"` field. Mandatory cases: ① buy-and-hold to end (openEnd trade); ② buy→sell signal round trip; ③ stop hit intrabar; ④ take hit intrabar; ⑤ same-bar stop+take double touch (stop wins); ⑥ max-hold expiry at close (`'到期'`); ⑦ all-watch → `null`; ⑧ pre-entry flat equity then late entry; ⑨ signal on the entry bar itself never exits (`i > entryIdx` guard); ⑩ no-clock run (signal-only exits).

**Required invariants:**

1. every fixture case passes under the declared tolerance constants — and a deliberately perturbed expectation (one ULP-scale nudge beyond tolerance) fails, proving the tolerances actually bind;
2. mirror comparisons align on `eqSeg`/`firstSig` (post-entry segment), never raw `eq` (gotcha 9), and the `None` case is asserted as `None`, not empty;
3. the mirror shares the Task 5 diff/lot logic only through `lot_size=1`-degenerate sizing — it never routes through `Broker` and never applies costs;
4. profile violations (two positions, fractional target weight 0.5, non-stop-first schedule) raise `CompatibilityProfileError` — the mirror cannot silently accept out-of-profile intents;
5. the same intent tuple run through **both** the full `ShadowBacktestRunner` (zeroed `CostModel`, `lot_size=1`) and the mirror on a fixture with no suspension/limit/T+1 interference produces trades whose entry/exit bars agree — a documented sanity link between the two paths, compared only on bar indices and sides (prices legitimately differ: same-bar close vs next-bar conventions), asserted exactly.

- [ ] **Step 1: Write failing mirror tests + fixture** — the ten cases, tolerance-binding proof, profile-violation matrix, sanity link.

Run now: `pytest tests/orchestration/test_shadow_mirror.py -v` — Expected: FAIL on missing mirror contract.

- [ ] **Step 2: Implement the mirror + intent mapping**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_mirror.py tests/orchestration/test_shadow_runner.py tests/orchestration/test_shadow_gaps.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py tests/orchestration/fixtures/shadow_mirror_v1.json tests/orchestration/test_shadow_mirror.py
git commit -m "feat(orchestration): stage-1 compatibility mirror of frontend runBacktest with declared tolerances"
```

---

## Task 9: Shadow events — pure-additive `EventType` + persist-then-publish

**Files:**
- Modify: `guanlan_v2/orchestration/events.py` (pure addition only)
- Modify: `tests/orchestration/test_events.py` (frozen-set extension — reviewed guard flip)
- Test: `tests/orchestration/test_shadow_events.py`

**Consumes:** Phase 1 `RunEvent.build`, `SchemaRef`, `PayloadRef`, partition rules; Phase 2 `EventStore`/`PayloadStore`/`IdempotencyConflict`; Task 3/4 payloads. Mechanism per CRIB 4.5/Phase-4 ruling: EventType extension is the only allowed Phase 1 source touch, done as pure addition + guard flip.

**Produces:**

- Two new `EventType` members appended to `guanlan_v2/orchestration/events.py`: `SHADOW_INTENT_ISSUED = "ShadowIntentIssued"`, `SHADOW_TARGET_APPLIED = "ShadowTargetApplied"`. Per-type rule added alongside the existing `ArtifactStaged`/`LayerCommitted` validators: both shadow types require `partition == "main"` (shadow facts are public advisory evidence, never sealed/review; the existing namespace-masquerade validator already blocks non-public payload refs on main events). No existing member, value or validator is modified.
- Payload bindings (convention, asserted in tests): `ShadowIntentIssued` carries `payload_schema_ref = TargetPortfolioIntent@1` with idempotency key `content_digest({"domain": "shadow-intent-issued-v1", "intent": <semantic digest>})`; `ShadowTargetApplied` carries `ShadowTargetApplyRecord@1` with idempotency key = the record's `target_apply_key`. Any typed evidence pair persisted around these events (schema + locator together) is a Phase 1 `TypedPayloadRef` composite; bare `PayloadStore` locators remain plain `PayloadRef` (Amendment 1 global constraint).
- `tests/orchestration/test_events.py` frozen-set extension: add the two values to the reviewed exact-equality set (as reshaped by Phase 4 — Task 0 correction clause) and drop nothing.

**Required invariants:**

1. the `EventType` change is provably additive: the pre-existing reviewed set is a strict subset of the new set and every old value string is unchanged;
2. persist-then-publish via the real Phase 2 `EventStore`: appending `ShadowIntentIssued` twice with the same key and identical payload returns the stored event; same key + different intent content raises `IdempotencyConflict`; `ShadowTargetApplied` keyed on `target_apply_key` makes the apply-once invariant event-visible (one visible apply event per `(intent_id, scheduled_for, target_version)` no matter how many times a recovering runner re-reports);
3. a shadow event on `partition="sealed"`/`"review"` fails construction; a main shadow event referencing a non-public payload namespace fails (inherited rule, re-asserted for the new types);
4. replaying the journal reproduces the shadow events in order with stable visible cursors (Phase 2 semantics, consumed not reimplemented).

- [ ] **Step 1: Write failing event tests** (and the guard-flip edit to `test_events.py` in the same change, so the red run shows the enum members missing)

Run now: `pytest tests/orchestration/test_shadow_events.py tests/orchestration/test_events.py -v` — Expected: FAIL on missing enum members (the extended frozen set is red until implementation).

- [ ] **Step 2: Add the two members + per-type rule to `events.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_events.py tests/orchestration/test_events.py tests/orchestration/test_contract_completeness.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/events.py tests/orchestration/test_events.py tests/orchestration/test_shadow_events.py
git commit -m "feat(orchestration): additive ShadowIntentIssued/ShadowTargetApplied events + guard flip"
```

---

## Task 10: Phase 6 registry/catalog chain + goldens + deferred-guard flip

**Files:**
- Modify: `guanlan_v2/orchestration/shadow.py`
- Modify: `tests/orchestration/test_contract_completeness.py` (deferred-payload guard flip — reviewed)
- Create: `tests/orchestration/golden/phase6_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase6_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase6_registry.py`

**Consumes:** Phase 5 chain nodes (`PHASE5_REGISTRY_DIGEST`/`build_phase5_registry`, `PHASE5_CATALOG_DIGEST` — exact names per Task 0 correction clause); Phase 1 `SchemaRegistry`, `validate_plan_draft` sink-authorization path.

**Produces (CRIB 4.5 chain naming, verbatim):**

- `PHASE6_PUBLIC_MODELS: tuple[type, ...]` — exactly the ten registered Phase 6 payloads: `TargetPosition`, `PortfolioTargetProposal`, `TargetPortfolioIntent`, `DecisionSchedule`, `ShadowTargetApplyRecord`, `ShadowOrderRecord`, `ShadowFillRecord`, `ShadowRejectRecord`, `ShadowRunResult`, `CorporateActionEvent`.
- `PHASE6_INTERNAL_MODELS` — reviewed reason map for the internal carriers (`ShadowOrderPlanEntry`, `ShadowOrderSkip`, `ShadowOrderPlan`, `DeterministicTargetSet`, `CorporateActionApplication`, `CompatSignal`, `CompatClock`, `CompatTrade`, `CompatibilityRunResult`), following the upstream classification mechanism.
- `PHASE6_BASE_REGISTRY_DIGEST: DigestHex` — declared equal to the exact Phase 5 digest; `def build_phase6_registry(expected_phase5_digest: DigestHex) -> SchemaRegistry` — verifies the Phase 5 manifest digest first, registers Phase 5's cumulative public set plus `PHASE6_PUBLIC_MODELS`, seals, returns a fresh instance per call; `PHASE6_REGISTRY_DIGEST: DigestHex` — the reviewed sealed result digest. Inherited JSON Schemas byte-identical to upstream; no upstream golden regenerated; no "latest" alias.
- `def build_phase6_catalog_snapshot(phase5_snapshot: WorkerCatalogSnapshot, *, expected_phase5_digest: DigestHex) -> WorkerCatalogSnapshot` — the **explicit identity chain node** (CRIB 4.5: a phase adding no worker/capability still exports the chain node): verifies `phase5_snapshot.catalog_digest == expected_phase5_digest` and returns the snapshot unchanged; `PHASE6_CATALOG_DIGEST: DigestHex` — reviewed equal to `PHASE5_CATALOG_DIGEST` (recorded, not aliased). Phase 6 adds **zero** workers and **zero** capabilities — that emptiness is itself a red-line fact (no live order/signal write tool can enter through this phase).
- Golden `phase6_schema_manifest_v1.json` — full manifest of the sealed Phase 6 registry (base digest + every `SchemaRef` + JSON-schema digest), hand-frozen, never regenerated from test code. Golden `phase6_catalog_manifest_v1.json` — records `{base: PHASE5_CATALOG_DIGEST, result: PHASE6_CATALOG_DIGEST, workers_added: 0, capabilities_added: 0}`.
- **Deferred-guard flip** in `test_contract_completeness.py`: remove the four Phase-6 names from `DEFERRED_PHASE_PAYLOADS` and add `test_phase6_payloads_present_in_phase6_registry` asserting each of `TargetPosition/PortfolioTargetProposal/TargetPortfolioIntent/DecisionSchedule` ① is defined in `guanlan_v2.orchestration.shadow`, ② is registered in `build_phase6_registry(...)` at `@1`, and ③ remains **absent** from Phase 1 `default_registry()` and both Phase 1 classification buckets (absence→presence-in-phase6-registry, exactly the CRIB mechanism). The Phase-5 and Phase-8 deferred names stay guarded untouched (Phase 5 will have flipped its own).

**Required invariants:**

1. chain integrity: `build_phase6_registry` fails loudly on a wrong base digest; the sealed digest equals `PHASE6_REGISTRY_DIGEST` and the golden byte-matches; every inherited entry's JSON-schema digest equals its upstream golden value;
2. sink authorization now bites for real: a `PlanDraft` whose sink's primary output schema is `PortfolioTargetProposal@1` (resolvable in the Phase 6 registry) validates only when the emitting worker has `can_emit_decision=True` — otherwise Phase 1 `validate_plan_draft` reports `unauthorized_decision_sink` with **zero** `spec.py` changes (the class names were frozen for exactly this);
3. the identity catalog node verifies its base and adds nothing: worker and capability manifests of the Phase 6 snapshot are byte-equal to Phase 5's;
4. old Plans bound to upstream digests remain resolvable (registry resolution by exact digest, no reinterpretation — consumed Phase 2 semantics);
5. the flipped guard keeps protecting: `DebateMessage` (Phase 8) still absent everywhere; the four Phase 6 names still absent from Phase 1 surfaces.

- [ ] **Step 1: Write failing registry/chain tests + the guard flip**

Run now: `pytest tests/orchestration/test_phase6_registry.py tests/orchestration/test_contract_completeness.py -v` — Expected: FAIL on missing chain exports (guard-flip presence assertions red).

- [ ] **Step 2: Implement chain exports; freeze both goldens by review**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase6_registry.py tests/orchestration/test_contract_completeness.py tests/orchestration/test_registry_population.py -v` — Expected: PASS, Phase 1 golden untouched.

```bash
git add guanlan_v2/orchestration/shadow.py tests/orchestration/test_phase6_registry.py tests/orchestration/test_contract_completeness.py tests/orchestration/golden/phase6_schema_manifest_v1.json tests/orchestration/golden/phase6_catalog_manifest_v1.json
git commit -m "feat(orchestration): phase6 cumulative registry/catalog chain + deferred-guard flip"
```

---

## Task 11: Red-line regression suite

**Files:**
- Test: `tests/orchestration/test_shadow_redlines.py`

**Consumes:** everything above. Test-only task; no production change.

**Required invariants (each a test):**

1. **structural zero-trading**: constructing a `TargetPortfolioIntent` with `origin="HUMAN"`, `authority="LIVE"`, `execution_scope="LIVE"` or any non-default Literal value fails validation; the three fields have exactly one allowed value each (introspected from the model fields, not just tried values);
2. **never on a live bus**: `guanlan_v2.orchestration.adapters.luozi` and `guanlan_v2.orchestration.shadow` import neither `guanlan_v2.seats` nor any of `{requests, httpx, aiohttp, urllib.request}` (asserted over each module's `sys.modules`-visible import graph); the Phase 6 catalog capability manifest is byte-equal to Phase 5's, so **no order/signal write capability exists for any worker grant** — there is no such tool to grant;
3. **no promotion surface**: the reviewed `__all__` of `shadow.py` and `adapters/luozi.py` is asserted by exact equality, and no exported callable accepts a shadow artifact/result and returns a seats-ledger/live-order/live-signal object (closed export surface is the enforcement);
4. **no in-place mutation after realized**: mutate-attempts on a `TargetPortfolioIntent` and on a `ShadowRunResult` raise; `ShadowBacktestRunner.run` returns intents untouched (pre/post digest equality) and a rerun after "seeing" the first result cannot change any already-produced apply record (digest equality of replayed results);
5. **no silent normalization anywhere**: a weight-sum-violating proposal cannot reach the envelope, the diff, the runner or the mirror — each entry point re-refuses it (defense in depth asserted at all four seams);
6. **honest degradation badges**: an ST-unknown match and a synthetic corporate-action run both surface their badges on `ShadowRunResult`;
7. **scope protection**: no file under `engine/financial_analyst/backtest/` or `ui/` was modified (git-diff-based assertion is not portable in tests — instead assert the Task 0 pinned engine signatures still hold and rely on the Exit Gate review for the file-level check; this signature/digest-pin mechanism is **the** cross-phase test-hygiene rule for scope protection, and Phase 9 will follow it); `WAITING_FOR_MATURITY` resume/wakeup, multi-decision-point PIT replay, dual-curve **Evaluator wiring** (the deterministic lane `run_targets` itself is Phase 6, Task 6; its Evaluator/replay consumption is Phase 9) and frontend switch-over remain unimplemented here (assert the names are absent from Phase 6 modules) — they are Phase 9 scope.

- [ ] **Step 1: Write the failing/at-red subset** — items 1–6 red only where behavior is missing; where Tasks 1–10 already enforce an invariant the test documents it green (regression pinning, allowed here because the *suite* is the deliverable).

Run now: `pytest tests/orchestration/test_shadow_redlines.py -v`

- [ ] **Step 2: Fix any gap the suite exposes (production edits go to the owning module with its own focused rerun)**

- [ ] **Step 3: Full sweep and commit**

Run: `pytest tests/orchestration -v`

Also run: `python -m compileall -q guanlan_v2/orchestration`

If Ruff is available: `ruff check guanlan_v2/orchestration/shadow.py guanlan_v2/orchestration/adapters tests/orchestration`

```bash
git add tests/orchestration/test_shadow_redlines.py
git commit -m "test(orchestration): phase6 shadow red-line regression suite"
```

---

## Phase 6 Exit Gates

Phase 6 is complete only when every gate below is checked by tests or a reviewed artifact.

### Upstream handoff and chain

- [ ] every Phase 1–5 exit gate remains green; no upstream source, test or golden file modified except the two reviewed guard flips and the pure-additive `events.py` change;
- [ ] `test_phase6_handoff.py` pins the exact upstream digests, engine signatures and the engine's verified take-profit/max-hold/corporate-action absence;
- [ ] `PHASE6_REGISTRY_DIGEST`/`build_phase6_registry(expected_phase5_digest)` and `PHASE6_CATALOG_DIGEST`/`build_phase6_catalog_snapshot(...)` exist with exact CRIB names; wrong base digests fail; goldens `phase6_schema_manifest_v1.json`/`phase6_catalog_manifest_v1.json` are frozen and byte-verified; inherited schemas byte-identical; no "latest" alias;
- [ ] the deferred-payload guard is flipped exactly for the four Phase 6 names (present in the Phase 6 registry, still absent from every Phase 1 surface); Phase 8's `DebateMessage` remains guarded.

### Contracts, schedule and envelope

- [ ] `PortfolioTargetProposal` rejects duplicate symbol / NaN / negative / short / leverage before staging, and `abs(sum+cash-1) <= 1e-8` is enforced with zero normalization (boundary vectors both sides);
- [ ] the proposal payload cannot carry any envelope field (`extra="forbid"` proven adversarially);
- [ ] `DecisionSchedule` digest covers version/timezone/cutoff/calendar/bar-frequency/execution-policy/price-field/matching-engine-version/intrabar-priority (sensitivity vectors); `next_open↔open`/`next_bar_close↔close` pairing enforced;
- [ ] the schedule id/version/digest triple is structurally same-present-or-same-absent (`ContentRef`), and registry resolve verifies all three;
- [ ] `scheduled_for`/`eligible_execution_at` are uniquely computed from schedule fields via `compute_scheduled_for`/`compute_eligible_execution_at`; no caller-supplied execution times exist on any public path; unsupported bar frequencies are refused honestly;
- [ ] `wrap_proposal_as_intent` is the sole intent path: unregistered/absent/stale schedule ref, non-decision-point date, and pre-cutoff `decision_as_of` all refuse; the ruled time model `cutoff_at <= decision_as_of < eligible_execution_at` (with `eligible_execution_at <= valid_until` when set) holds on every constructed intent;
- [ ] a request without `decision_schedule_ref` can never yield a shadow intent.

### Runner, gaps and idempotency

- [ ] the runner consumes frozen intents over `Broker/VirtualPortfolio/CostModel/limit helpers` and never enters the engine's daily `agent.decide()` loop; `ShadowDecisionAgent` conforms to the engine agent shape with `n_calls == 0` and is runner-consumed only — never executed under `BacktestRunner`'s `legs_to_orders` path (engine.py:165 batch renormalization hazard);
- [ ] the diff step converts absolute target weights + cash into canonical sells-first order plans deterministically; already-conforming portfolios produce zero orders;
- [ ] `(intent_id, scheduled_for, target_version)` applies a target exactly once above Broker; replays are digest-identical; same-key/different-content conflicts raise; order ids `(target_apply_key, symbol, order_kind, trigger_bar, ordinal)` and fill ids `(order_id, fill_seq)` dedup exactly per spec with causation keys resolvable end-to-end;
- [ ] engine T+1/suspension/one-word-limit/lot/cost/slippage semantics pass through with verbatim reject reasons;
- [ ] take-profit, max-hold and the three intrabar priorities execute above Broker per the schedule's digest-bearing priority field; gap exits still obey suspension/limit reality;
- [ ] the corporate-action ledger applies dividend/bonus/split events exactly once each, preserves cost basis, rescales T+1 locks and stop prices, and keeps NAV continuous across ex-dates; the ledger mechanism is new shadow-side code and the engine remains untouched;
- [ ] realized results never mutate an intent; results reference intents only by content digest;
- [ ] the deterministic dual-curve lane `run_targets(target_sets, *, run_config, calendar, clock)` shares the identical matching engine/cost model/calendar/clock config as `run(intents)` (same config digest; mismatch refused), uses the disjoint `(rule_id, point_ordinal, target_version)` apply-key family (domain-tagged — never collides with intent apply keys), mints no `TargetPortfolioIntent`, records no `origin="LLM"` provenance, and a target set identical to an intent's positions yields identical fills/costs (same-bar equivalence) — Phase 9's dual-curve replay consumes this lane.

### Compatibility mirror (stage ①)

- [ ] the mirror replicates the frontend profile (zero cost/single symbol/same-bar-close/fractional/full-in-full-out/stop-first/exact trigger-price exits/maxHold-at-close/pre-entry-flat/all-watch-None/openEnd) as a separate fill path — never a Broker parameterization;
- [ ] all ten fixture cases pass under the declared tolerance constants (`COMPAT_TRADE_STRUCTURE_TOL=0`, `COMPAT_PRICE_REL_TOL=1e-9`, `COMPAT_RETURN_ABS_TOL=1e-9`, `COMPAT_EQUITY_REL_TOL=1e-9`, `COMPAT_METRIC_REL_TOL=1e-6`), and the tolerance-binding proof fails a perturbed expectation;
- [ ] comparisons align on `eqSeg`/`firstSig`; metrics are recomputed one way via the mirrored `metricsOf` conventions; out-of-profile intents and non-stop-first schedules raise `CompatibilityProfileError`;
- [ ] the frontend switch-over date is explicitly deferred to Phase 9 and no frontend file changed.

### Events

- [ ] `ShadowIntentIssued`/`ShadowTargetApplied` are pure-additive `EventType` members; the reviewed frozen set in `test_events.py` is extended, nothing removed or revalued;
- [ ] both types are main-partition only; persist-then-publish with `IdempotencyConflict` on same-key/different-content proven against the real Phase 2 `EventStore`; the apply event is keyed on `target_apply_key` so recovery re-reports collapse to one visible event.

### Red lines and scope protection

- [ ] `origin/authority/execution_scope` are single-value structural Literals; no live order/signal capability exists in the Phase 6 catalog (byte-equal manifests) and no such tool can be granted; shadow modules import no seats/HTTP surface; the closed `__all__` exposes no promotion path from shadow artifact to live instruction;
- [ ] no silent vendor fallback, no silent normalization, degradation always badged;
- [ ] `engine/financial_analyst/backtest/` and `ui/` byte-unmodified (reviewer file-level check + pinned-signature tests);
- [ ] no `WAITING_FOR_MATURITY` resume machinery, multi-decision-point replay, dual-curve Evaluator wiring (the `run_targets` lane itself is Phase 6 scope; only its Evaluator/replay consumption is Phase 9), Lane D workers or frontend decommission was added — all Phase 8/9 scope;
- [ ] unrelated worktree changes are not staged (every commit used explicit pathspec).

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — exact upstream ABI/digests, engine-gap evidence, and every correction clause resolved against implemented code;
2. after Tasks 1–3 — proposal/schedule/envelope contracts, unique time computation, key families (the frozen vocabulary Lane D will later target);
3. after Tasks 4–6 — record contracts, diff step, apply-once runner over the unmodified Broker baseline, and the deterministic dual-curve lane (`run_targets` + disjoint key family) Phase 9 consumes;
4. after Task 7 — gap exits + corporate-action ledger semantics;
5. after Task 8 — compatibility profile, fixtures and the declared tolerances (this is the reviewed answer to spec §13's open tolerance item);
6. after Tasks 9–11 — additive events, registry/catalog chain + goldens, guard flips, red-line suite and all Exit Gates.

Do not begin Phase 7 (or any Lane D schema migration in Phase 8) until every Phase 6 Exit Gate is checked with test evidence — that ordering is the entire point of pinning the shadow consumer first (spec §12.6). No execution method requires a particular optional skill package.
