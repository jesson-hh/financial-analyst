# Orchestration Phase 9 · 完整落子/帷幄 adapters + DecisionSchedule replay + 双曲线 + 旧入口下线门槛 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the PIT replay data adapter, the interval-replay driver, the dual-curve/evaluator handoff, the weiwo ONLINE adapter, the mirror golden harness and the final e2e/red-line suite. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Complete the consumer edge of the orchestration framework: (a) the 落子 replay adapter that drives a `DecisionSchedule` over an interval — at every decision point it reruns Bootstrap plus the needed MainPlan against that point's PIT `ContextSnapshot` (strict raw `PitReader` adapter, `DataMode.PIT_REPLAY`), never generating historical intents retroactively — and produces the spec §2.3 dual curves (deterministic strategy vs LLM shadow intents) under one shared universe/capital/data-snapshot/calendar/cost-model/clock configuration, handing results to the Phase 4 Evaluator; (b) `WAITING_FOR_MATURITY` persistence with `resume_after`/`wakeup_key` idempotent wakeup wired to the autonomy scheduler precedent; (c) the 帷幄 live adapter: `DataMode.ONLINE` with `as_of` frozen at run start, live_client data binding, Bootstrap → ContextSnapshot → open research, `evaluate_validation=run_graph` binding, products draft-only into factorlib; (d) mirror stage ② — a backend golden harness proving rich fill/reject/cost/corporate-action execution independent of the frontend; (e) explicit, measurable retirement gates for the three legacy entry points (console report subprocess, swarm `load_preset` CLI, research loop direct route) — **no removal happens inside this phase**; (f) the whole-framework e2e + red-line regression suite of spec §11; (g) the Phase 9 cumulative registry/catalog chain node with its own goldens; (h) the durable jsonl store backend + honest lifespan resume/interrupt marking that lets admitted plans, run events and parked maturity heads survive process restarts (Task 1b — integration amendment 2026-07-18); (i) the daily Lane 0 bootstrap gate on the existing autonomy scheduling chain, admitted through the Phase 7 `ApprovalLease` channel (Task 6 — same amendment).

**Architecture:** Phase 9 is a consumer phase. It imports — never redefines — Phase 1 contracts (`guanlan_v2/orchestration/`), the Phase 2 runtime kernel (admission/eventstore/pool/worker/dag/budget), the Phase 3 data/PIT + memory facade, Phase 4 `run_optimize`/`TrialLedger`/sealed evaluator, Phase 5 `BootstrapPlan`/BOOTSTRAP profile, Phase 6 shadow consumer (`PortfolioTargetProposal`/`TargetPortfolioIntent`/`DecisionSchedule`/`ShadowDecisionAgent`/`ShadowBacktestRunner` + idempotency key families + mirror stage ①), Phase 7 dynamic planner/approval surface and the Phase 8 lane catalog. New code lives in `guanlan_v2/orchestration/adapters/` plus thin additive seams in `guanlan_v2/autonomy/`, `guanlan_v2/seats/watcher.py` and `guanlan_v2/server.py`. `engine/financial_analyst/` and `workflow/executor.run_graph`/`_DISPATCH`/`_OUT_PORT` are frozen consumption surfaces: every execution-semantics gap (take-profit, max-hold, corporate actions) is closed in the Phase 6-owned shadow-runner layer, never inside the engine. The browser keeps its existing pages and consumes backend results through the existing runs/decisions append-only stores and new read-only endpoints (UI 只填充不重建).

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio` (`to_thread` for sync engine/`run_graph` calls; HTTP self-calls from coroutines remain forbidden repo-wide), `pytest` + `pytest-asyncio`. All modules `from __future__ import annotations`. Run tests from repo root `G:\guanlan-v2` with `pytest`.

## Global Constraints

These extend, and never override, the Phase 1–8 Global Constraints and Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import Phase 1–8 models/builders from their owning modules. Phase 9 must not redefine canonical JSON, digests, `WorkerSpec`, catalog snapshots, schema registries, `Artifact`, `RunEvent`, Plan validation/freeze, event semantics, `TargetPosition`/`PortfolioTargetProposal`/`TargetPortfolioIntent`/`DecisionSchedule`, `ShadowDecisionAgent`/`ShadowBacktestRunner`, `TrialLedger`/sealed stores, `BootstrapPlan` or the memory facade. Where an upstream name is cited below and the implemented upstream API differs, Task 0's correction clause applies.
- **Strict contracts.** Every new public Phase 9 contract is a Pydantic v2 strict/frozen/extra-forbid `DigestModel` with a closed `schema_version`, semantic/audit projections (wall-clock, random ids, object locators in `SEMANTIC_EXCLUDE`/audit only) and `sha256+cjson-v1` canonicalization via the Phase 1 base classes.
- **TypedPayloadRef discipline (Phase 1 Amendment 1).** Wherever a Phase 9 contract carries a schema-bearing evidence reference, the field is the Phase 1 composite `TypedPayloadRef(schema_ref, payload_ref)` with the schema pinned to the registered payload — `ShadowReplayRunState.curve_report_ref` pins `DualCurveReport@1` with payload namespace `main`. Plain `PayloadRef` remains the bare storage locator and stays on locator-only surfaces (`persist_replay_state`'s return, `RunEvent.payload_ref`).
- **Registry/catalog chain naming is frozen (CRIB 4.5).** Phase 9 exports exactly `PHASE9_REGISTRY_DIGEST` + `build_phase9_registry(expected_phase8_digest)` and `PHASE9_CATALOG_DIGEST` + `build_phase9_catalog_snapshot(...)` with its own golden manifests; inherited schemas stay byte-identical; no upstream golden is regenerated; no "latest" alias. **Decision (explicit, not identity):** Phase 9 registers new payloads (replay/curve/maturity/harness/retirement contracts) and new data-adapter capabilities, so both chain nodes are real extensions, not identity nodes. Phase 9 adds **no** new `EventType` member: maturity transitions reuse `EXPERIMENT_STATE_CHANGED`; replay lifecycle reuses the Phase 1/2 run events; shadow apply events are Phase 6's.
- **PIT discipline.** Every replay read path pushes `available_at <= as_of` down through the Phase 3 `PitGuard`; violations raise `FutureDataRefused` and never fall through to a weaker path; missing `available_at` raises `MissingAvailabilityRefused`; memory/case/lesson reads obey the same cutoff via the Phase 3 memory facade. No silent vendor fallback: only `RateLimitError`/`NotConfiguredError` advance a frozen chain.
- **Replay feasible window starts at each feed's archive floor (R2 AMEND-1; ruling R13).** The Lane 0 half of interval replay depends on the per-feed snapshot-archive coverage (market_tape / fundflow / macro); a decision point earlier than a feed's archived floor resolves that feed's factors as UNAVAILABLE with badges (Task 2b) — never zero-filled, never a current snapshot impersonating history. This plan does not imply that arbitrary historical intervals are runnable. The snapshot-archive small phase is chartered separately and runs in parallel; it is **not** promoted to a hard precondition of Phase 9.
- **No retroactive intents.** A `TargetPortfolioIntent` for decision point *k* may be produced only from a `ContextSnapshot` whose `as_of` equals that point's `decision_as_of`, and only while no later point of the same run has begun execution. Producing any intent after the interval's later bars have entered a decision context is structurally rejected and tested.
- **Red lines (spec §0/§10).** Draft-only products with human review for adoption; LLM zero trading — `origin="LLM"`/`authority="ADVISORY_ONLY"`/`execution_scope="SHADOW_ONLY"` remain structural literals; the live adapter registers no order/signal write capability; workers propose-never-write memory/skill/code; every degradation is badged; numbers without provenance are `[UNSOURCED]`; `AUTO` approval stays rejected for every PlanSource.
- **Engine and executor are frozen.** No modification to `engine/financial_analyst/**` or to `workflow/executor.run_graph`/`_DISPATCH`/`_OUT_PORT`/their signatures. `run_graph` keeps exactly its two production callers plus tests; Phase 9 wraps it in-process from worker threads only.
- **UI 只填充不重建.** No new frontend pages, no rewrite of `ui/seats/*`. The adapter writes run heads/decision rows into the existing append-only stores (`var/seats_runs.jsonl`, `var/seats_decisions.jsonl`) in their existing shapes so the current RunPicker/replay UI consumes orchestrated results unchanged, and exposes new read-only JSON endpoints for richer curves.
- **Old entries stay alive.** The console report subprocess, swarm `load_preset` CLI and research-loop direct route are not removed, rerouted or degraded in this phase. Phase 9 only delivers their measurable retirement gates and parity evidence; removal is a post-gate follow-up commit outside this plan.
- **Budget honesty.** Each replay/weiwo run reserves from one Phase 2 `BudgetLedger` under one `RunBudget` covering Bootstrap + Planner + MainPlan + every decision point; the seats watcher's 24/day live budget and orchestration `RunBudget` reconcile through one explicit rule (Task 4) — never double-accounted, never double-spent.
- **Durability honesty (integration amendment 2026-07-18).** Production bindings (Task 10 router, Task 6 playbooks) run on the Task 1b durable jsonl stores; the in-memory Phase 2 stores remain the test default and continue to make no durability claim. On process restart, in-flight attempts are marked `interrupted` honestly — nothing resumes mid-attempt and nothing displays as running that is not — while parked `WAITING_FOR_MATURITY` heads survive byte-identically. Store files follow the repo journal discipline: append-only, fsync per append, torn-tail tolerance with a logged badge, mid-file corruption ⇒ typed hard failure.
- **Executable red/green checkpoints.** Every "Write failing … tests" step immediately runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation; collection/environment errors do not count as the red checkpoint. The PASS step reruns the same focused tests plus listed upstream regressions.
- **Explicit pathspec commits.** The branch is shared with concurrent sessions: every commit block lists exactly the task's files; `git add -A`, `git add .` and bare `git commit -a` are forbidden.
- No placeholders, DRY, YAGNI, TDD, frequent commits.

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 9 depends on **all** of Phases 1–8. Work starts only after every upstream phase's Exit Gates pass with test evidence. Add `tests/orchestration/test_phase9_handoff.py` as an executable consumer test rather than copying upstream assertions.

**Files:**
- Create: `tests/orchestration/test_phase9_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. Phase 1 goldens (`schema_manifest_v1.json` **as re-frozen by Phase 1 Amendment 1** — the amended **11**-model golden including `TypedPayloadRef@1`/`InputArtifactBinding@1`/`ContextRuntimeRequirements@1` — plus digest vectors) still pass and `default_registry()` seals; `TypedPayloadRef(schema_ref, payload_ref)` resolves as the Phase 1 composite while plain `PayloadRef` stays the bare locator; the full `tests/orchestration` upstream suite is green.
2. The linear chain resolves end-to-end by exact digest: `PHASE2_BASE_REGISTRY_DIGEST` → `PHASE3_DATA_REGISTRY_DIGEST` → `PHASE3_FULL_REGISTRY_DIGEST` → `PHASE4_REGISTRY_DIGEST` → `PHASE5_REGISTRY_DIGEST` → `PHASE6_REGISTRY_DIGEST` → `PHASE7_REGISTRY_DIGEST` → `PHASE8_REGISTRY_DIGEST`, and the catalog chain `PHASE2_STATIC_CATALOG_DIGEST` → … → `PHASE8_CATALOG_DIGEST`, each builder consuming its predecessor's exact digest; no "latest" alias exists anywhere in `guanlan_v2/orchestration/`.
3. Phase 6 shipped and registered `TargetPosition@1`, `PortfolioTargetProposal@1`, `TargetPortfolioIntent@1`, `DecisionSchedule@1` (the Phase 1 `DEFERRED_PHASE_PAYLOADS` guard was flipped for exactly these names), plus `ShadowDecisionAgent`/`ShadowBacktestRunner` with **both** entries — `run(intents, *, start, end)` and the deterministic dual-curve entry `run_targets(target_sets, *, run_config, calendar, clock)` with `DeterministicTargetSet` and `deterministic_apply_key` (domain `shadow-deterministic-apply-key-v1`) — the schedule-time computations `compute_scheduled_for`/`compute_cutoff_at`/`compute_eligible_execution_at` (+ `UnsupportedBarFrequencyError` under `shadow-match-v1`), the DecisionSchedule registry, the three idempotency key families `(intent_id, scheduled_for, target_version)` / `(target_apply_key, symbol, order_kind, trigger_bar, ordinal)` / `(order_id, fill_seq)`, and the mirror stage-① compatibility profile with its explicit tolerance constants.
4. Phase 4 exports `run_optimize`, `finalize_candidate`, `TrialLedger`, `OptimizeRunState`, `HoldoutReceipt` and honors `ExperimentStatus.WAITING_FOR_MATURITY`; Phase 5 exports the versioned `BootstrapPlan` preset and the BOOTSTRAP-enabled `StaticRuntimeProfile` version; Phase 7 exports the dynamic-planner admission path with digest-bound human approval plus the `ApprovalLease` standing-approval surface (`issue_lease`/`list_leases`/`revoke_lease`/`register_and_try_lease` — consumed by the Task 6 lane0 playbook and any unattended preset admission); Phase 8 exports the lane catalog whose `dec.trader` emits only `PortfolioTargetProposal`. The gate accepts the R2-revised Phase 8 catalog as handed off — including curator workers #26/#27 and the D11 capability-manifest generator's products where present — and asserts no seat count; the `dec.trader`-emits-only-`PortfolioTargetProposal` assertion stays unchanged.
5. Phase 5's delivered `BootstrapPlan`/`market_factor_report` surface exposes the coverage/UNAVAILABLE semantics and the `factor_report_digest` of R2 deliverables ①/④ in consumable form — the anchor for Task 2b's manifest coverage floors and Task 12's digest-binding assertions (recorded in correction-clause style as a refinement of C3; no parallel semantics invented).
6. The Phase 2 `RuntimeStateCellStore` startup-namespace union mechanism accepts a reviewed extension (P2:180) and the current sealed union (Phase 3's seven `memory.*` plus any Phase 4–8 additions) is enumerable from code, so Task 6 can extend it without resealing semantics.
7. Engine baseline symbols resolve unchanged: `financial_analyst.backtest.broker.Broker.match`, `portfolio.VirtualPortfolio` (`seed_initial_nav`/`record_nav`), `costs.CostModel`, `limits.limit_pct_for`/`compute_ref_prev_close`/`is_one_word`, `engine.BacktestRunner`/`RunConfig`/`BacktestResult`, `pit_reader.PitReader.get_visible_info`/`trading_days`/`fetch_bars_intraday`; and `guanlan_v2/datafeed/live_client.py` exports `probe`/`catalog`/`resolve_source`/`known_sources` (no source-count constant is asserted).
8. The three legacy entry points still exist at their grounded seams (console `_spawn_bg`/`_run_report_bg`/`_call_buddy_report`; engine `swarm/loader.load_preset` with its `cli.py`/`tui.py` consumers; `POST /research/loop/start` → `run_research_loop`) — Phase 9 gates them, it does not find them already removed.
9. No Phase 9 source/test path overwrites any Phase 1–8 source, test or golden file.

**Line-reference convention:** every grounded line reference in this plan — the legacy seams (`console/api.py:302/508/520`, `swarm/loader.py:32-37`, `research/api.py:63-95` etc.) and the Phase 1 grounding refs (`enums.py:58`, `context.py:117-175` etc., which are pre-Amendment-1) — is name-authoritative and line-approximate; resolve by symbol name at implementation time.

**Correction clauses (binding for every later task):** if an exact field, builder, module path or signature differs in the implemented upstream public API, update this plan to that reviewed API before writing adapter code; do not invent an adapter with parallel semantics. Specifically:

- **C1 (Phase 6 shadow API):** the exact constructor/execute signatures of `ShadowDecisionAgent`/`ShadowBacktestRunner`, the DecisionSchedule registry accessor, the intent-envelope builder and the stage-① tolerance constants are Phase 6-owned; Tasks 3–5 and 8 bind to the reviewed names.
- **C2 (Phase 4 evaluator API):** the exact `evaluate_validation` callable signature accepted by `run_optimize`, the `TrialLedger` reservation methods and the maturity/`resume_after` semantics are Phase 4-owned; Tasks 5–7 bind to the reviewed names.
- **C3 (Phase 5 bootstrap API):** the exact `BootstrapPlan` preset id/builder and BOOTSTRAP profile version are Phase 5-owned; Task 3 binds to the reviewed names. Refinement (R2): the coverage/UNAVAILABLE field names and the `factor_report_digest` accessor on the Phase 5 `market_factor_report` are likewise Phase 5-owned; Tasks 2b and 12 bind to the reviewed names rather than inventing parallel semantics.
- **C4 (Phase 3 data builders):** the exact `DataSnapshotManifest`/`DataSourceConfigSnapshot`/`DataRoutingSnapshot`/`DataSourceRegistry` builder signatures, the `DataSource` protocol method names and the existing Phase 3 data method ids (`get_ohlcv`/`get_news`/`get_verified_snapshot` families) are Phase 3-owned; Tasks 2–3 and 9 bind to the reviewed names. Task 4's quoted memory-facade signature `prepare_pit_replay(data_context, authority, *, prior_context_ref: PayloadRef, ...)` is an upstream verbatim quote; if the implemented facade lands typed (Amendment 1-consistent, `prior_context_ref: TypedPayloadRef`), the driver binds the typed form.
- **C5 (state-cell union):** the reviewed startup namespace union current at implementation time is the base Task 6 extends; the two new namespaces are appended to that union, not to a hardcoded list.
- **C6 (seats/autonomy seams):** exact helper names inside `guanlan_v2/seats/api.py` (decision persistence), `guanlan_v2/seats/watcher.py` (state file readers) and `guanlan_v2/autonomy/runtime.py`/`playbooks.py` are verified against code at implementation time; the additive seam functions of Tasks 4/6/10 are renamed to match reviewed conventions if needed.
- **C7 (Phase 7 lease API):** the exact `ApprovalLease` field set, coordinator method names and `LeaseAdmissionOutcome` vocabulary are Phase 7-owned (Task 7b); Tasks 1b/6/10 bind to the reviewed names.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture**

Record only exact digests (registry/catalog chain values, Phase 6 schedule/profile digests, engine symbol signatures **plus byte digests of the engine baseline modules** — the Task 5/8 in-test engine-untouched comparison baseline) — never local paths or mutable singleton identities.

- [ ] **Step 3: Run the upstream suites and the frozen gate**

Run: `pytest tests/orchestration -v`

Expected: every Phase 1–8 test plus `test_phase9_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase9_handoff.py
git commit -m "test(orchestration): gate phase9 on phase1-8 contracts"
```

---

## File Structure (created/modified in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/adapters/__init__.py` | package export surface (created by Phase 6; extended here) |
| `guanlan_v2/orchestration/adapters/contracts.py` | Phase 9 public contracts: decision points, execution config, curves, replay state, wakeup receipt, harness report, retirement gates |
| `guanlan_v2/orchestration/adapters/replay_data.py` | strict raw `PitReader` `DataSource` adapter + `PIT_REPLAY` DataContext/manifest builders + `ReplayPointClock` |
| `guanlan_v2/orchestration/adapters/live_data.py` | live_client `DataSource` adapter + `ONLINE` DataContext builder with start-frozen `as_of` |
| `guanlan_v2/orchestration/adapters/luozi.py` | (Phase 6 file, extended) `resolve_decision_points`, `run_interval_replay`, deterministic target rule, dual curves, evaluator handoff, maturity/wakeup, budget reconcile |
| `guanlan_v2/orchestration/adapters/weiwo.py` | ONLINE binding: Bootstrap → ContextSnapshot → open research; `run_graph` validation evaluator; draft-only factorlib sink |
| `guanlan_v2/orchestration/adapters/retirement.py` | retirement-gate contracts instances + pure readiness evaluator |
| `guanlan_v2/orchestration/adapters/chain.py` | `PHASE9_PUBLIC_MODELS`/`PHASE9_INTERNAL_MODELS`, `build_phase9_registry`, `build_phase9_catalog_snapshot`, chain digests |
| `guanlan_v2/orchestration/adapters/api.py` | thin read/start FastAPI router (shadow/draft only) + seats-compatibility persistence |
| `guanlan_v2/orchestration/adapters/durable.py` | durable jsonl/file implementations of the Phase 2 store ABIs (`JsonlEventStore`/`FilePayloadStore`/`JsonlStateCellStore`) + `build_durable_runtime_stores` + startup interrupt-marking scan |
| `guanlan_v2/autonomy/runtime.py` (modify, additive) | `maybe_enqueue_shadow_wakeup` + `maybe_enqueue_lane0_bootstrap` scheduler gates |
| `guanlan_v2/autonomy/playbooks.py` (modify, additive) | register `shadow_replay_wakeup` + `lane0_bootstrap` playbooks |
| `guanlan_v2/screen/rescore.py` (modify, additive lines) | daily-scheduler seam calls the wakeup + lane0 gates next to `maybe_enqueue_daily_review` |
| `guanlan_v2/seats/watcher.py` (modify, additive) | `note_external_llm_use` daily-budget seam + orchestrated-run skip guard |
| `guanlan_v2/server.py` (modify, additive) | mount adapters router + bind durable stores + startup interrupt-marking scan |
| `tests/orchestration/golden/phase9_schema_manifest_v1.json` | Phase 9 registry golden |
| `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | Phase 9 catalog golden |
| `tests/orchestration/golden/shadow_execution_golden_v1.json` | hand-authored mirror stage-② execution fixtures |
| `tests/orchestration/golden/phase9_retirement_gates_v1.json` | reviewed retirement-gate instances (digest-frozen) |
| `tests/orchestration/test_phase9_handoff.py` | executable Phases 1–8 → 9 ABI gate |
| `tests/orchestration/test_adapters_contracts.py` | contract validation/projection/digest tests |
| `tests/orchestration/test_durable_stores.py` | durable-store conformance + crash-recovery + restart-survival tests |
| `tests/orchestration/test_adapters_replay_data.py` | PIT adapter + replay DataContext tests |
| `tests/orchestration/test_adapters_live_data.py` | live_client adapter + ONLINE freeze tests |
| `tests/orchestration/test_luozi_replay.py` | schedule resolution + interval replay driver tests |
| `tests/orchestration/test_dual_curves.py` | dual-curve construction/attestation/evaluator handoff tests |
| `tests/orchestration/test_luozi_wakeup.py` | maturity persistence + idempotent wakeup + scheduler gate tests |
| `tests/orchestration/test_weiwo_adapter.py` | weiwo ONLINE/run_graph-binding/draft-only tests |
| `tests/orchestration/test_shadow_golden_harness.py` | mirror stage-② golden harness |
| `tests/orchestration/test_phase9_registry_chain.py` | chain node + golden tests |
| `tests/orchestration/test_adapters_api.py` | router + seats-compat persistence + budget reconcile tests |
| `tests/orchestration/test_retirement_gates.py` | gate evaluator + golden instance tests |
| `tests/orchestration/test_phase9_e2e.py` | whole-framework e2e |
| `tests/orchestration/test_redline_regression.py` | spec §11 red-line regression suite |

---

## Task 1: Phase 9 public contracts

**Files:**
- Create: `guanlan_v2/orchestration/adapters/contracts.py`
- Test: `tests/orchestration/test_adapters_contracts.py`

**Consumes:** Phase 1 `DigestModel`/`ContractModel`, strict types (`DigestHex`, `UtcDateTime`, `FiniteFloat`, `NonNegativeInt`, `PositiveInt`, `NonEmptyStr`, `LogicalId`), `Symbol`, `ClockSpec`, `PayloadRef`, `TypedPayloadRef` (the Amendment 1 composite), `ContentRef`, `ExperimentStatus` (`enums.py:58`, includes `WAITING_FOR_MATURITY`). Phase 6 `DecisionSchedule@1` (its `content_digest` covers version/timezone/cutoff/calendar/bar frequency/execution policy/price field/matching engine version/intrabar priority — spec `.md:936`).

**Produces (all strict/frozen/extra-forbid `DigestModel`, `schema_version: Literal["1"] = "1"` unless marked nested):**

- `ReplayDecisionPoint`: `schedule_ref: ContentRef`, `schedule_digest: DigestHex`, `point_ordinal: PositiveInt`, `scheduled_for: UtcDateTime`, `cutoff_at: UtcDateTime`, `decision_as_of: UtcDateTime`, `eligible_execution_at: UtcDateTime`, `execution_price_field: Literal["open","close"]`, `bar_frequency: Literal["1d","60m","30m","15m","5m","1m"]`. Validators: `cutoff_at <= decision_as_of`, `decision_as_of < eligible_execution_at` (the Phase 6-ruled unified time model `cutoff_at <= decision_as_of < eligible_execution_at`), `next_open↔open`/`next_bar_close↔close` pairing is upstream (Phase 6 schedule) but `execution_price_field`/`bar_frequency` must equal the referenced schedule's fields when checked by the Task 4 resolver (which itself refuses non-`1d` frequencies under `shadow-match-v1`).
- `ShadowExecutionConfig`: `universe: tuple[Symbol, ...]` (non-empty, unique, canonically sorted by `code`), `init_cash: FiniteFloat` (`gt=0`), `data_snapshot_content_digest: DigestHex`, `vintage_manifest_digest: DigestHex`, `calendar_id: NonEmptyStr`, `cost_model_digest: DigestHex`, `matching_engine_version: NonEmptyStr`, `clock: ClockSpec`, `schedule_digest: DigestHex`, `intrabar_exit_priority: Literal["worst_case","stop_first","take_profit_first"]`. This is the single "同一 universe/初始资金/数据快照/交易日历/成交费用模型/clock" attestation of spec `.md:130`; all fields semantic.
- `ShadowCurvePoint` (nested, no schema_version): `at: UtcDateTime`, `nav: FiniteFloat` (`gt=0`).
- `ShadowCurveSeries`: `curve_kind: Literal["deterministic_strategy","llm_shadow"]`, `execution_config_digest: DigestHex`, `points: tuple[ShadowCurvePoint, ...]` (non-empty, `at` strictly increasing), `trade_count: NonNegativeInt`, `applied_intent_digests: tuple[DigestHex, ...] = ()` (required non-empty iff `curve_kind == "llm_shadow"`), `rule_id: NonEmptyStr | None` (required iff `curve_kind == "deterministic_strategy"`), `badges: tuple[NonEmptyStr, ...] = ()`.
- `DualCurveReport`: `execution_config: ShadowExecutionConfig`, `deterministic: ShadowCurveSeries`, `llm_shadow: ShadowCurveSeries`, `interval_start: UtcDateTime`, `interval_end: UtcDateTime`, `decision_point_count: PositiveInt`, `delta_total_return: FiniteFloat | None`, `not_causal_attribution: Literal[True] = True`, `badges: tuple[NonEmptyStr, ...] = ()`. Validators: both series bind `execution_config_digest == execution_config.semantic_digest()`; `deterministic.curve_kind == "deterministic_strategy"`; `llm_shadow.curve_kind == "llm_shadow"`; `interval_start < interval_end`.
- `ShadowReplayRunState`: `experiment_id: NonEmptyStr`, `run_id: NonEmptyStr`, `request_id: NonEmptyStr`, `schedule_digest: DigestHex`, `execution_config_digest: DigestHex`, `status: ExperimentStatus`, `completed_points: NonNegativeInt`, `total_points: PositiveInt`, `last_scheduled_for: UtcDateTime | None`, `resume_after: UtcDateTime | None`, `wakeup_key: NonEmptyStr | None`, `curve_report_ref: TypedPayloadRef | None` (schema-pinned: `schema_ref` must name the registered `DualCurveReport@1`; `payload_ref` namespace `main`), `updated_at: UtcDateTime`. `SEMANTIC_EXCLUDE = {"updated_at"}`. Matrix validators: `WAITING_FOR_MATURITY` ⇔ both `resume_after` and `wakeup_key` set; `COMPLETED` ⇒ `completed_points == total_points` and `curve_report_ref` set; `RUNNING`/`FAILED` forbid `wakeup_key`; `completed_points <= total_points`; `status` never takes the optimizer-only values `PASSED_VALIDATION`/`SEALED_EVALUATING` — both are forbidden for replay runs (explicit validator; controller ruling).
- `ReplayWakeupReceipt`: `wakeup_key: NonEmptyStr`, `experiment_id: NonEmptyStr`, `outcome: Literal["resumed","not_mature","already_processed","completed"]`, `matured_points: NonNegativeInt`, `state_digest_after: DigestHex`, `processed_at: UtcDateTime`. `SEMANTIC_EXCLUDE = {"processed_at"}`.
- `MirrorHarnessCaseResult` (nested): `case_id: LogicalId`, `passed: bool`, `reason: NonEmptyStr | None` (required iff not passed).
- `MirrorHarnessReport`: `fixture_digest: DigestHex`, `matching_engine_version: NonEmptyStr`, `results: tuple[MirrorHarnessCaseResult, ...]` (non-empty, unique `case_id`, sorted by `case_id`), `all_passed: bool` (⇔ every result passed).
- `RetirementCriterion` (nested): `criterion_id: LogicalId`, `description: NonEmptyStr`, `evidence_kind: Literal["pytest_suite","parity_fixture","reviewed_artifact","operational_run_log"]`, `evidence_selector: NonEmptyStr`.
- `EntryPointRetirementGate`: `entry_point: Literal["console.report_subprocess","swarm.load_preset_cli","research.loop_direct"]`, `replacement: NonEmptyStr`, `criteria: tuple[RetirementCriterion, ...]` (non-empty, unique ids), `removal_allowed_without_gate: Literal[False] = False`.
- `RetirementCriterionResult` (nested): `criterion_id: LogicalId`, `status: Literal["green","red","unavailable"]`, `evidence_digest: DigestHex | None` (required iff green), `reason: NonEmptyStr | None` (required iff not green).
- `RetirementReadinessReport`: `gate_digest: DigestHex`, `entry_point: Literal["console.report_subprocess","swarm.load_preset_cli","research.loop_direct"]`, `results: tuple[RetirementCriterionResult, ...]` (must cover the gate's criterion ids exactly, sorted), `ready: bool` (⇔ every result green; any `unavailable` ⇒ `ready=False`, fail closed), `evaluated_at: UtcDateTime`. `SEMANTIC_EXCLUDE = {"evaluated_at"}`.

**Required invariants:**

1. Direct construction with mismatched digests, naive datetimes, NaN/Inf, empty tuples where non-empty is required, or unsorted canonical tuples fails Pydantic validation.
2. Semantic digests are stable under audit-field changes (`updated_at`/`processed_at`/`evaluated_at` moves never move semantic digests) and unstable under any semantic field change.
3. `DualCurveReport` cannot be built from two series with different `execution_config_digest` values — the shared-口径 red line is structural.
4. `not_causal_attribution` and `removal_allowed_without_gate` are literal-typed and cannot be flipped by any caller.
5. No contract duplicates a CRIB-frozen name; `DecisionSchedule`/`TargetPortfolioIntent` etc. are imported, not redefined.

- [ ] **Step 1: Write failing contract tests** covering the full validator matrix above, projection exclusions, digest stability vectors and frozen-name imports.

Test matrix (each row one focused test):

| Test | Fixture | Expected |
|---|---|---|
| `test_decision_point_time_ordering` | `cutoff_at > decision_as_of`; `decision_as_of >= eligible_execution_at` | both rejected with field-named errors |
| `test_decision_point_naive_datetime_rejected` | naive `scheduled_for` | `ValidationError` |
| `test_execution_config_universe_canonical` | duplicate/unsorted `Symbol` tuple | rejected; sorted unique tuple accepted |
| `test_execution_config_all_semantic` | flip each field once | semantic digest moves for every field |
| `test_curve_series_monotone_points` | equal/decreasing `at` pair | rejected |
| `test_curve_series_kind_matrix` | `llm_shadow` without `applied_intent_digests`; `deterministic_strategy` without `rule_id` | both rejected |
| `test_dual_curve_config_binding` | series digest ≠ `execution_config.semantic_digest()` | rejected (the shared-口径 red line) |
| `test_dual_curve_kind_positions` | deterministic series passed as `llm_shadow` | rejected |
| `test_not_causal_attribution_literal` | `not_causal_attribution=False` | `ValidationError` (literal) |
| `test_run_state_status_matrix` | `WAITING_FOR_MATURITY` missing `wakeup_key`; `COMPLETED` missing `curve_report_ref`; `RUNNING` carrying `wakeup_key`; `completed_points > total_points` | all rejected |
| `test_run_state_optimizer_statuses_forbidden` | `status=PASSED_VALIDATION`; `status=SEALED_EVALUATING` | both rejected (replay runs never take optimizer-only statuses) |
| `test_curve_report_ref_typed_and_pinned` | `curve_report_ref` whose `schema_ref` names another schema; whose `payload_ref` namespace ≠ `main` | both rejected (TypedPayloadRef schema pin) |
| `test_run_state_updated_at_audit_only` | two states differing only in `updated_at` | equal semantic digests |
| `test_wakeup_receipt_processed_at_audit_only` | same pattern | equal semantic digests |
| `test_harness_report_all_passed_coherence` | `all_passed=True` with one failed result; failed result without `reason` | both rejected |
| `test_harness_results_sorted_unique` | duplicate/unsorted `case_id`s | rejected |
| `test_retirement_gate_literal_entry_points` | unknown `entry_point`; `removal_allowed_without_gate=True` | both rejected |
| `test_readiness_fail_closed` | one `unavailable` result | `ready=True` construction rejected; `ready=False` accepted |
| `test_readiness_coverage_exact` | missing/extra `criterion_id` vs gate | rejected |
| `test_green_requires_evidence_digest` | green result with `evidence_digest=None` | rejected |
| `test_frozen_names_are_imports` | module namespace scan | no local class named `DecisionSchedule`/`TargetPortfolioIntent`/`TargetPosition`/`PortfolioTargetProposal` defined in `contracts.py` |

Run now: `pytest tests/orchestration/test_adapters_contracts.py -v`

Expected: FAIL on missing `guanlan_v2.orchestration.adapters.contracts` module/classes.

- [ ] **Step 2: Implement `contracts.py`** exactly as specified; no builder helpers beyond validated `model_validate`/direct construction (these records carry no self-sealed digest fields, so no `build` classmethods are needed).

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_adapters_contracts.py -v` — expected PASS. Also run `pytest tests/orchestration/test_contract_completeness.py -v` — expected PASS (Phase 9 models live outside `PHASE1_MODULES`; the Phase 1 partition is untouched).

```bash
git add guanlan_v2/orchestration/adapters/contracts.py tests/orchestration/test_adapters_contracts.py
git commit -m "feat(orchestration): phase9 adapter contracts (replay/curves/maturity/harness/retirement)"
```

---

## Task 1b: Durable store backend + lifespan resume/interrupt marking

> Integration amendment (2026-07-18, `docs/superpowers/specs/2026-07-18-orchestration-integration-design.md` §3; applied via `2026-07-18-integration-reconcile-checklist.md`). Phase 7 states outright that the Phase 2 in-memory stores make no durability claim, yet Task 6 parks `WAITING_FOR_MATURITY` heads that must survive to the next day's wakeup — this task supplies the durable backend Phase 9's own exit gates depend on.

**Files:**
- Create: `guanlan_v2/orchestration/adapters/durable.py`
- Modify: `guanlan_v2/server.py` (additive lifespan block; shares the Task 10 commit surface)
- Test: `tests/orchestration/test_durable_stores.py`

**Consumes:** the implemented Phase 2 store ABIs from `guanlan_v2/orchestration/eventstore.py` (`PayloadStore`, `EventStore` with dual journal/visible cursors, `RuntimeStateCellStore` + closed CAS command, `RuntimeUnitOfWork`, `RuntimeStores`) — bind to the reviewed protocol/ABC names verbatim (Task 0 correction discipline); the Phase 7 journal discipline (append-only, fsync per append, torn-tail drop with warning badge, mid-file corruption ⇒ typed hard failure) as the file-format idiom; the Phase 2 persisted `NodeRun@1` terminal records (the resume/interrupt source of truth).

**Produces:**

- `class FilePayloadStore:` — content-addressed write-once files `var/orchestration/payloads/<namespace>/<digest>`; a second put of an existing digest verifies byte-identity (mismatch ⇒ typed conflict); `get` re-verifies the digest before returning (corruption never flows onward silently); namespaces partition physically (`sealed` never shares a directory with `main`).
- `class JsonlEventStore:` — per-partition append-only journals `var/orchestration/events/<partition>.jsonl`, fsync per append; the dual `journal_seq`/`visible_seq` cursor semantics byte-equivalent to the in-memory implementation; fold-on-open rebuilds cursors.
- `class JsonlStateCellStore:` — CAS transitions appended to `var/orchestration/state_cells.jsonl`, folded to heads on open; CAS conflict semantics identical to the in-memory store.
- `def build_durable_runtime_stores(root: Path) -> RuntimeStores:` — the production binding consumed by Task 6's playbooks and Task 10's router; in-memory stores remain the test default everywhere.
- Server lifespan (additive block in `guanlan_v2/server.py`): bind the durable stores once per process (root default `var/orchestration/`, env `GUANLAN_ORCH_STORE_ROOT` override for 9998 verification runs); a startup scan folds the journals and marks any admitted-but-unfinished attempt `interrupted` through the reviewed Phase 2 record path — nothing resumes mid-attempt, nothing is displayed as running that is not; parked `WAITING_FOR_MATURITY` heads are left untouched by the scan (surviving them is the point).
- Conformance discipline: the durable implementations run the SAME behavioral matrices as the Phase 2 in-memory store suites — parametrize/reuse the reviewed Phase 2 test matrices where importable rather than mirroring them by hand — plus the durability-specific tests below.

**Required invariants:**

1. store ABI conformance: every behavioral assertion that holds for the in-memory stores holds for the durable ones (UoW atomicity, staged sentinel replacement, whole-batch idempotency, CAS conflicts, dual-cursor visibility);
2. crash windows are safe: kill between append and any downstream step recovers by fold to a consistent state; no partial row is ever trusted (torn tail dropped with a logged badge; earlier corruption ⇒ typed hard failure, no silent skip);
3. restart survival: admitted plans, run events, payloads and `WAITING_FOR_MATURITY` heads fold back byte-identically after process death;
4. the startup scan is honest and conservative: it only marks `interrupted`; it never re-executes, re-admits or fabricates progress;
5. payloads are write-once and digest-verified on read.

- [ ] **Step 1: Write failing durable-store tests** — conformance parametrization; crash-recovery matrix (kill points around append/fold/UoW); torn-tail vs mid-file corruption; write-once conflict; digest-verify-on-get; restart survival of a parked maturity head; startup-scan marking (in-flight attempt fixture ⇒ `interrupted`; parked head ⇒ untouched).

Run now: `pytest tests/orchestration/test_durable_stores.py -v` — expected FAIL on missing module.

- [ ] **Step 2: Implement `durable.py` + the lifespan block.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_durable_stores.py -v` plus the Phase 2 store suites — expected PASS with the Phase 2 suites byte-identical green.

```bash
git add guanlan_v2/orchestration/adapters/durable.py guanlan_v2/server.py tests/orchestration/test_durable_stores.py
git commit -m "feat(orchestration): durable jsonl store backend + honest lifespan interrupt marking"
```

---

## Task 2: Strict raw PitReader adapter + PIT_REPLAY DataContext

**Files:**
- Create: `guanlan_v2/orchestration/adapters/replay_data.py`
- Test: `tests/orchestration/test_adapters_replay_data.py`

**Consumes:** Phase 3 `DataSource` protocol, `RawRowCandidate`, `PitGuard.from_context`/`check_raw`, `FutureDataRefused`/`MissingAvailabilityRefused`, `DataSnapshotManifest` (`manifest_kind="pit_frozen"`), `build_data_context`, `DataSourceDescriptor`/`DataMethodSpec`; Phase 2 `AuthoritativeClock`; engine `financial_analyst.backtest.pit_reader.PitReader` (lazy import precedent: `guanlan_v2/seats/news_marks.py:34-39` `_get_reader`; PIT red line `news_marks.py:2-5`); Task 1 `ReplayDecisionPoint`. Correction clause C4 applies to every Phase 3 builder name.

**Produces:**

- `class ReplayPointClock:` — `def __init__(self, *, as_of: UtcDateTime) -> None`, `def now(self) -> UtcDateTime` returning the frozen aware-UTC `as_of`; rejects naive input. Implements the Phase 2 `AuthoritativeClock` protocol for replay: the point's `decision_as_of` **is** the run clock; no wall-clock read exists on the replay path.
- `class PitReaderRawSource:` — the strict raw adapter implementing the Phase 3 `DataSource` protocol. `def __init__(self, *, reader_factory: Callable[[], Any] | None = None) -> None` (default lazily imports `PitReader` exactly like `news_marks._get_reader`; tests inject a fake). Supported operations are bound **behind the existing Phase 3 method ids** (`get_ohlcv`/`get_news` families; exact mapping reviewed per clause C4 — **no new `DataMethodSpec` id is minted**): daily bars (day bars ≤ as_of via `get_visible_info`/day loader), intraday bars (via `fetch_bars_intraday`), visible news/events/policy (via `get_visible_info(..., include=("news","events","policy"))`). The adapter registers as a `PIT_REPLAY` `DataSourceDescriptor` in the Phase 3 `DataSourceRegistry` (supported modes `PIT_REPLAY` only, backend `PIT_STORE`, every bound method `read_only=True`); `DataMode` routing selects it — workers reach it through the data capabilities they already hold. Every returned row is wrapped as a `RawRowCandidate` with explicit `available_at` (news `ts`; events `ann_date` end-of-availability convention identical to `news_marks.py`; bars: the bar close time in exchange tz converted to UTC) and passed through `PitGuard.check_raw` before leaving the adapter — the adapter itself never filters future rows silently.
- `def build_replay_manifest(*, store_meta: Mapping[str, Any], entries: tuple[DataSnapshotEntry, ...]) -> DataSnapshotManifest:` — `manifest_kind="pit_frozen"`; binds the pit_store `_meta.json` facts (`news_coverage_floor`, `cal_start`/`cal_end`, data end) into digest-bearing entries; the physical store root is audit-only, never semantic.
- `def build_replay_data_context(*, decision_point: ReplayDecisionPoint, source_config: DataSourceConfigSnapshot, source_registry: DataSourceRegistrySnapshot, routing: DataRoutingSnapshot, manifest: DataSnapshotManifest) -> DataContext:` — constructs `ReplayPointClock(as_of=decision_point.decision_as_of)` and delegates to the one Phase 3 `build_data_context` with `mode=DataMode.PIT_REPLAY`, `backend=DataBackend.PIT_STORE`; the Phase 1 `DataContext._coherent` validator (`context.py:117-175`) then enforces `strict_pit=True` + non-LIVE backend + `vintage_manifest_digest` presence.

**Required invariants:**

1. A candidate row with `available_at > decision_as_of` raises `FutureDataRefused` with the offending row count; it never degrades, never falls back, never returns a truncated OK result.
2. A row missing/naive `available_at` raises `MissingAvailabilityRefused`.
3. PIT hash stability: adding strictly-future rows to the fake store leaves every old-date `DataResult.content_digest` byte-identical (spec §11 PIT invariant ①).
4. `build_replay_data_context` output has `as_of == clock.as_of == decision_point.decision_as_of` and `calendar_id` equal on both context and clock; PIT_REPLAY without a manifest digest is impossible by construction.
5. No method of `PitReaderRawSource` accepts a caller `as_of`/`strict` override — time authority comes only from the `DataContext`/`PitGuard`.
6. The adapter is registered behind the Phase 2 `CapabilityGateway` route (Phase 3 dispatch); direct calls from worker code are not exported.

- [ ] **Step 1: Write failing adapter tests** with an injected fake reader (fixed rows with controlled `available_at`), covering invariants 1–5, the source descriptor's method-binding request/response schema validation, and the manifest builder's semantic/audit split.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_replay_clock_frozen_and_aware` | naive ctor input; two `now()` calls | naive rejected; both calls return the exact frozen instant |
| `test_future_news_row_refused` | one news row `available_at = as_of + 1min` | `FutureDataRefused` with `future_rows == 1`; no partial result object escapes |
| `test_future_bar_refused_not_filtered` | intraday bar closing after `as_of` returned by fake reader | `FutureDataRefused` (the adapter must not silently drop it) |
| `test_missing_available_at_refused` | row with `available_at=None`; row with naive timestamp | `MissingAvailabilityRefused` both |
| `test_refusal_never_falls_back` | route with a second configured vendor behind the refusing one | second vendor never invoked (spy) |
| `test_old_date_digest_stable_under_future_rows` | run once, add strictly-future rows to fake store, run again | `DataResult.content_digest` byte-identical |
| `test_event_ann_date_availability_convention` | event row with `ann_date == as_of.date()` | visible; `ann_date == as_of.date() + 1d` refused — matches `news_marks.py` convention |
| `test_context_builder_coherence` | build for a fixture point | `as_of == clock.as_of == decision_as_of`; `mode=PIT_REPLAY`, `backend=PIT_STORE`, `strict_pit=True`, manifest digest present |
| `test_no_caller_time_override` | introspect method signatures | no `as_of`/`strict`/`now` parameter on any `PitReaderRawSource` method |
| `test_manifest_semantic_audit_split` | two manifests differing only in physical root locator | equal semantic digests; audit digests differ |
| `test_source_descriptor_pit_only` | the registered `DataSourceDescriptor` + its bound method refs | supported modes `== (PIT_REPLAY,)`, backend `PIT_STORE`, every bound method `read_only=True`; method refs ⊆ the existing Phase 3 method-id set (no new id) |

Run now: `pytest tests/orchestration/test_adapters_replay_data.py -v`

Expected: FAIL on missing `replay_data` module.

- [ ] **Step 2: Implement** `ReplayPointClock`, `PitReaderRawSource`, both builders. Reuse `news_marks.py` conventions (coverage floor, `ann_date` availability) rather than re-deriving them.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_adapters_replay_data.py tests/orchestration/test_adapters_contracts.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/replay_data.py tests/orchestration/test_adapters_replay_data.py
git commit -m "feat(orchestration): strict raw PitReader adapter + PIT_REPLAY data context"
```

---

## Task 2b: Per-feed snapshot-archive coverage floors in the replay manifest + feasible-window honesty

R2 reconcile addition (AMEND-1 + deliverable ① §5 archive dependency; ruling R13). Task 2's `build_replay_manifest` binds only the pit_store `_meta.json` facts — yet every decision point reruns Bootstrap (Task 4 step ③), whose `market.factor` historical series can only come from the market_tape/fundflow/macro snapshot archives. This task makes that dependency explicit and honest. Task 4 step ③ and Task 12 red line 9 consume it.

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/replay_data.py` (additive)
- Test: `tests/orchestration/test_adapters_replay_data.py` (extend)

**Consumes:** Task 2 `build_replay_manifest`/`PitReaderRawSource` descriptor/`DataSnapshotManifest`; Phase 3 `DataSnapshotEntry`/`DataRoutingSnapshot` (clause C4); R2 deliverable ① (`docs/superpowers/specs/2026-07-17-market-factor-report-schema.md`) §5 and the AMEND-1 red line (缺历史覆盖→UNAVAILABLE,绝不拿当前快照冒充历史); the per-feed snapshot archives (market_tape / fundflow concept+industry / macro) delivered by the snapshot-archive small phase — **already chartered as its own parallel small phase; consumed by feed id and floor date only, never promoted to a hard Phase 9 precondition (ruling R13: the replay feasible window simply starts at each feed's archive floor)**. Exact feed ids and read-surface names are reviewed at implementation time against that phase's delivered API (correction-clause style).

**Produces:**

- `build_replay_manifest` extension: per-feed **archive coverage-floor entries** (one per Lane 0 feed the factor battery reads historically), digest-bearing exactly like the pit_store `_meta.json` facts; physical archive roots stay audit-only, never semantic.
- The derived **replay feasible-window fact**: a decision point earlier than a feed's coverage floor ⇒ that feed's factors resolve UNAVAILABLE + badge + reason under that point's PIT context; Bootstrap degrades honestly and continues; the Task 4 driver records a degraded point, never a run failure.
- The structural guarantee — now explicitly tested — that `PIT_REPLAY` mode routing can never select the `ONLINE` live source (reuse of the Task 2 descriptor's supported-modes `(PIT_REPLAY,)` mechanism): a current snapshot can never impersonate history.

**Required invariants:**

1. A point before a feed's archived floor yields UNAVAILABLE for that feed's factors — no zero-fill, no synthetic backfill, no fallback to a current snapshot; the floor boundary is exact.
2. Old-point result digests stay byte-identical as the archive grows (the Task 2 invariant-3 PIT stability property extended to archive feeds).
3. An interval straddling a floor completes with per-point honesty — UNAVAILABLE points and normal points coexist in one run without failure.
4. A routing snapshot carrying both the Task 2 and Task 3 descriptors never resolves the ONLINE descriptor for a PIT_REPLAY request.

- [ ] **Step 1: Write failing tests**

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_pre_floor_factor_unavailable` | decision point before the fixture archive floor | UNAVAILABLE + badge + reason; no zero/NaN fill; no partial fabricated series |
| `test_floor_boundary_exact` | points one session before / on the floor | before ⇒ UNAVAILABLE; on ⇒ resolves normally |
| `test_pre_floor_digest_stable` | evaluate a pre-floor point, append newer archive rows, evaluate again | result digests byte-identical |
| `test_mixed_interval_honest` | interval straddling the floor | run completes; both behaviors coexist per point |
| `test_manifest_floor_entries_semantic` | two manifests differing only in physical archive root | equal semantic digests; a floor-date change moves the semantic digest |
| `test_pit_replay_never_routes_online` | routing snapshot with both the PIT_REPLAY and ONLINE descriptors | a PIT_REPLAY request never resolves the ONLINE descriptor |

Run now: `pytest tests/orchestration/test_adapters_replay_data.py -v` — expected FAIL on the new tests.

- [ ] **Step 2: Implement** the manifest extension and the feasibility semantics (additive; Task 2's existing surface and tests unchanged).

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_adapters_replay_data.py tests/orchestration/test_adapters_contracts.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/replay_data.py tests/orchestration/test_adapters_replay_data.py
git commit -m "feat(orchestration): per-feed archive coverage floors in replay manifest + feasible-window honesty"
```

---

## Task 3: live_client ONLINE adapter + start-frozen as_of

**Files:**
- Create: `guanlan_v2/orchestration/adapters/live_data.py`
- Test: `tests/orchestration/test_adapters_live_data.py`

**Consumes:** Phase 3 `DataSource` protocol/`RawRowCandidate`/`PitGuard`/`build_data_context`/`DataSnapshotManifest` (`manifest_kind="online_capture_root"`); Phase 2 `AuthoritativeClock`; `guanlan_v2/datafeed/live_client.py` facade (`probe(source, code="", date="", limit=20, timeout=...)`, `catalog(max_age_s=...)`, `resolve_source(source)`, `known_sources()` — `live_client.py:148-278`; honesty contract: caller/mechanical error → `ok:False`, upstream planned/error → `ok:True` + visible status, `live_client.py:6,276-278`). Task 1 contracts. Clause C4/C6.

**Produces:**

- `class LiveClientSource:` — `def __init__(self, *, probe_fn: Callable[..., Mapping[str, Any]] | None = None, catalog_fn: Callable[..., Mapping[str, Any]] | None = None) -> None` (defaults bind the live_client facade functions; tests inject fakes; **no hardcoded source count anywhere** — gotcha: 枚举钉旧31). Operations are bound **behind the existing Phase 3 method ids** (`get_verified_snapshot`/`get_news`/`get_ohlcv` families; exact mapping reviewed per clause C4 — **no new `DataMethodSpec` id is minted**): per-code realtime quote, announcement/news-style text feeds, market-wide tape snapshot. The adapter registers as an `ONLINE` `DataSourceDescriptor` in the Phase 3 `DataSourceRegistry` (supported modes `ONLINE` only, backend `LIVE`); `DataMode` routing selects it. Each probe envelope row becomes a `RawRowCandidate` with `available_at = pulled_at` (from the probe envelope; missing `pulled_at` ⇒ `MissingAvailabilityRefused` — ONLINE mode still records availability honestly), then flows through `PitGuard` (ONLINE guard passes rows with `available_at <= as_of` where `as_of` is the start-frozen run timestamp; rows fetched after run start that carry later `pulled_at` are refused, proving the freeze is real).
- `def build_online_data_context(*, clock: AuthoritativeClock, source_config: DataSourceConfigSnapshot, source_registry: DataSourceRegistrySnapshot, routing: DataRoutingSnapshot, manifest: DataSnapshotManifest) -> DataContext:` — reads `clock.now()` **exactly once**, builds `ClockSpec` and delegates to Phase 3 `build_data_context` with `mode=DataMode.ONLINE`, `backend=DataBackend.LIVE`. The returned `DataContext` is the frozen `as_of` for the whole run (Phase 1 already enforces coherence, `context.py:117-175`; ONLINE may omit `vintage_manifest_digest` while carrying the capture-root digest).
- Upstream `status: planned|error` envelopes map to `DataResult` non-data statuses with `degradation_reason`/badges — never fabricated rows, never a silent vendor swap (only `RateLimitError`/`NotConfiguredError` advance the frozen chain, per Phase 3).

**Required invariants:**

1. Two `clock.now()` advances after `build_online_data_context` do not change the context's `as_of` (freeze proven with an advancing fake clock).
2. A probe envelope with `ok:False` maps to a typed error; `ok:True, status:"planned"` maps to `DataStatus.UNAVAILABLE`-family results with visible reason — no data fabrication.
3. No order/signal write method exists on `LiveClientSource`; every method its descriptor binds is `read_only=True` (red line §10: live adapter 不注册订单/信号写工具).
4. Source ids are resolved through `resolve_source`; unknown source ids fail loudly.

- [ ] **Step 1: Write failing tests** for invariants 1–4 with fake probe/catalog functions and an advancing fake clock.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_as_of_frozen_at_build` | advancing fake clock; build then advance twice and fetch | context `as_of` unchanged; later-`pulled_at` rows refused by the guard, proving the freeze is enforced not decorative |
| `test_probe_ok_false_is_typed_error` | `{ok: False, error: "bad code"}` envelope | typed caller error; no `DataResult` fabricated |
| `test_probe_planned_maps_unavailable` | `{ok: True, status: "planned", items: []}` | non-data `DataStatus` with visible reason; zero rows |
| `test_upstream_error_visible_not_swapped` | `{ok: True, status: "error"}` on primary vendor | result surfaces the error; frozen chain advances only on `RateLimitError`/`NotConfiguredError` |
| `test_missing_pulled_at_refused` | envelope without `pulled_at` | `MissingAvailabilityRefused` |
| `test_no_write_methods` | introspect `LiveClientSource` + its descriptor's bound method refs | all `read_only=True`; no order/signal operation; method refs ⊆ the existing Phase 3 method-id set (no new id) |
| `test_unknown_source_fails_loud` | fake `resolve_source` raising | error propagates; no silent substitute |
| `test_no_hardcoded_source_count` | source scan of `live_data.py` | no integer literal asserted against `known_sources()` length |

Run now: `pytest tests/orchestration/test_adapters_live_data.py -v` — expected FAIL on missing module.

- [ ] **Step 2: Implement `live_data.py`.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_adapters_live_data.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/live_data.py tests/orchestration/test_adapters_live_data.py
git commit -m "feat(orchestration): live_client ONLINE data adapter with start-frozen as_of"
```

---

## Task 4: DecisionSchedule interval-replay driver (adapters/luozi.py full half)

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py` (Phase 6 file — extend, never rewrite Phase 6 sections)
- Modify: `guanlan_v2/seats/watcher.py` (additive seam only)
- Test: `tests/orchestration/test_luozi_replay.py`

**Consumes:** Phase 6 `DecisionSchedule`/intent envelope/idempotency keys/`ShadowBacktestRunner` and the schedule-time computations `compute_scheduled_for`/`compute_cutoff_at`/`compute_eligible_execution_at` + `UnsupportedBarFrequencyError` (consumed interfaces, clause C1); Phase 5 `BootstrapPlan` + BOOTSTRAP profile (clause C3); Phase 2 admission (`PlanAdmissionService`), `run_plan`, `BudgetLedger`, `RuntimeUnitOfWork`; Phase 3 memory facade `prepare_pit_replay(data_context, authority, *, prior_context_ref, ...)`; Phase 3 `TradingCalendar`/`TradingCalendarResolver`; Task 1/2 outputs. Seats watcher facts: `DEFAULT_BUDGET = 24` (`watcher.py:39`), state file `var/seats_watch.json` (`watcher.py:37`), tick pipeline `watcher.py:358-361` (clause C6).

**Produces:**

- `def resolve_decision_points(schedule: DecisionSchedule, *, calendar: TradingCalendar, interval_start: UtcDateTime, interval_end: UtcDateTime) -> tuple[ReplayDecisionPoint, ...]:` — pure. Expands `kind` (`daily` on calendar sessions filtered by `weekdays`; `weekly` first session of week; `rebalance_dates` exact listed dates; `manual` ⇒ empty tuple, manual points enter only via explicit request) into ordered points. For each point the three instants come **only** from the Phase 6-owned computations — never re-derived inline: `scheduled_for = compute_scheduled_for(schedule, session_date=..., calendar=calendar)`; `cutoff_at = compute_cutoff_at(schedule, session_date=...)`; `decision_as_of = scheduled_for` (the `decision_local_time` instant, per the ruled time model `cutoff_at <= decision_as_of < eligible_execution_at`); `eligible_execution_at = compute_eligible_execution_at(schedule, scheduled_for=scheduled_for, calendar=calendar)` — spec §11: "bar frequency/policy 唯一算出 eligible time". `shadow-match-v1` supports `bar_frequency="1d"` only: a non-`1d` schedule is refused via the Phase 6 `UnsupportedBarFrequencyError`; intraday replay requires a reviewed Phase 6 matching-engine version bump and is out of scope for this phase. Deterministic: same inputs ⇒ byte-identical tuple; `point_ordinal` dense from 1.
- `class ReplayRuntimeBindings:` (internal frozen carrier, not registered) — bundles `admission`, `pool`, `budget: BudgetLedger`, `stores`, `catalog_runtime`, `bridge_resolver`, `model_gateway`, `capability_gateway`, `clock_factory: Callable[[ReplayDecisionPoint], AuthoritativeClock]`, `shadow_runner`, `memory_preparer`, `schedule_registry`, `seats_budget_seam`. No callable-injected handlers beyond these service ports.
- `def run_interval_replay(*, request: OrchestrationRequest, schedule: DecisionSchedule, execution_config: ShadowExecutionConfig, interval_start: UtcDateTime, interval_end: UtcDateTime, bindings: ReplayRuntimeBindings) -> ShadowReplayRunState:` — the driver. Per decision point, strictly in `point_ordinal` order: ① `build_replay_data_context` for that point (Task 2); ② memory facade `prepare_pit_replay` (memory PIT'd to `decision_as_of`); ③ admit + run the versioned `BootstrapPlan` (Phase 5) → frozen per-point `ContextSnapshot` (per-point Bootstrap's `market.factor` historical reads resolve through the Task 2b archive-backed coverage floors under PIT; a factor whose feed floor postdates the point resolves UNAVAILABLE ⇒ an honestly degraded snapshot whose badge propagates to the replay state's badge surface — a degraded point, never a run failure); ④ admit + run the needed MainPlan (deterministic lane: zero-LLM plan; LLM shadow lane: the Phase 8 decision chain ending in `dec.trader`'s `PortfolioTargetProposal`) bound to that point's ContextSnapshot; ⑤ (LLM shadow lane only) runtime wraps the proposal into `TargetPortfolioIntent` via the Phase 6 envelope (all envelope fields runtime-generated) — the deterministic lane's per-point targets are carried as Phase 6 `DeterministicTargetSet` records (Task 5), envelope-free, never wrapped as intents; ⑥ append the intent to the run's intent ledger keyed by `(intent_id, scheduled_for, target_version)`. Execution (curve building) is Task 5's `build_dual_curves` over the frozen intent ledger. **Not every bar is a decision point, and no single "today" judgment covers the interval** — the only decision times are the resolved points.
- **No-retroactive-intent enforcement:** the driver records a monotone high-water mark `last_scheduled_for` in `ShadowReplayRunState`; any attempt to admit a decision plan for a point with `scheduled_for <= last_scheduled_for` (replay duplicates aside — same idempotency key returns the stored intent) or to inject an intent whose `decision_as_of` differs from its point's ContextSnapshot `as_of` raises a typed `RetroactiveIntentRefused` error and appends nothing.
- `def reconcile_daily_llm_budget(*, seats_watch_state: Mapping[str, Any], run_budget: RunBudget, requested_llm_invocations: int, session_date: str, is_live_session: bool) -> int:` — pure rule, single place: (i) historical `PIT_REPLAY` points never consume the seats daily budget (research compute, governed solely by `RunBudget`); (ii) points executing during a live trading session (`is_live_session=True`) may reserve at most `daily_budget - counts[session_date]` from the shared 24/day pool; the returned admissible count feeds `BudgetLedger.reserve_node`, and settlement is reported back through the watcher seam — one pool, counted once.
- Watcher seam (additive in `guanlan_v2/seats/watcher.py`): `def note_external_llm_use(n: int, now: datetime | None = None) -> None` — increments the same daily counters `tick` reads so watcher gating sees orchestrated consumption; and `def orchestrated_codes() -> set[str]` — codes owned by an active orchestrated run, which `tick` skips (`skipped[code] = "orchestrated"`) to prevent double judging. Existing watcher behavior for non-orchestrated codes is bit-unchanged.

**Required invariants:**

1. `resolve_decision_points` is deterministic, calendar-driven, and produces zero points outside `[interval_start, interval_end]`; `manual` yields no implicit points.
2. Each point's decision plans bind a ContextSnapshot whose `data_context.as_of == decision_as_of` — asserted structurally before admission (mismatch ⇒ refuse, no reservation).
3. Intents are produced in `point_ordinal` order; `RetroactiveIntentRefused` on any backfill attempt; idempotent re-application of the same point is a no-op returning the stored intent.
4. One `RunBudget` covers the whole interval run (Bootstrap + MainPlan × points); per-point node reservations are children of the plan reservation; deterministic-lane points reserve zero LLM invocations.
5. `reconcile_daily_llm_budget` never returns a value exceeding either pool; live-session settlement calls `note_external_llm_use` exactly once per settled reservation.
6. Watcher `tick` with an orchestrated code present skips it and judges others exactly as before (regression: existing watcher tests stay green).
7. `AUTO` approval remains rejected; every admitted plan in the loop went through REQUIRED approval (test uses a pre-approved fixture actor path from Phase 2/7).

- [ ] **Step 1: Write failing driver tests** — fake calendar + 3-point daily schedule; fixture bindings with recorded fake admission/run services; cover invariants 1–7, including the retroactive-refusal and idempotent-replay branches, and a `weekly`/`rebalance_dates`/`manual` expansion matrix.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_daily_expansion_deterministic` | 5-session fake calendar, daily schedule, weekday filter | exact expected point tuple twice (byte-identical); ordinals dense from 1 |
| `test_weekly_expansion` | two-week calendar | first session of each week only |
| `test_rebalance_dates_exact` | 3 listed dates, one falling on a non-session | non-session date refused loudly (not silently skipped) |
| `test_manual_yields_empty` | `kind="manual"` | empty tuple |
| `test_interval_bounds_respected` | points would fall outside `[interval_start, interval_end]` | excluded |
| `test_eligible_time_next_open` | `execution_policy="next_open"`, 1d bars | eligible equals `compute_eligible_execution_at(...)` — the next session's `ASHARE_SESSION_OPEN` instant; paired `execution_price_field="open"` |
| `test_eligible_time_next_bar_close_1d` | `"next_bar_close"`, 1d bars | eligible equals `compute_eligible_execution_at(...)` — the next session's `ASHARE_SESSION_CLOSE` instant; paired `execution_price_field="close"` |
| `test_non_1d_frequency_refused` | 30m-bar schedule | `UnsupportedBarFrequencyError` propagates from the consumed Phase 6 `compute_eligible_execution_at`; zero points produced |
| `test_point_snapshot_binding_enforced` | tamper: hand the driver a ContextSnapshot with wrong `as_of` | structural refusal before admission; zero reservations recorded |
| `test_points_run_in_order` | recorded fake services | admissions observed strictly in `point_ordinal` order |
| `test_retroactive_intent_refused` | attempt point 2 after point 3 executed | `RetroactiveIntentRefused`; intent ledger unchanged |
| `test_point_replay_idempotent` | rerun point 2 with same idempotency key | stored intent returned; no second envelope |
| `test_one_runbudget_whole_interval` | 3 points, per-point node reservations | all children of one plan reservation; deterministic-lane nodes reserve 0 LLM invocations |
| `test_budget_reconcile_replay_exempt` | `is_live_session=False` | admissible == requested (bounded by RunBudget only); seats counters untouched |
| `test_budget_reconcile_live_capped` | watcher state `counts[today]=20`, `daily_budget=24`, request 10 | returns 4; settlement calls `note_external_llm_use(4)` exactly once |
| `test_watcher_skips_orchestrated_codes` | `orchestrated_codes()` contains one watched code | tick returns `skipped[code]=="orchestrated"`; other codes judged exactly as before |
| `test_auto_approval_still_rejected` | request with `approval_policy=AUTO` | validation failure propagates; nothing runs |

Run now: `pytest tests/orchestration/test_luozi_replay.py -v` — expected FAIL on missing `resolve_decision_points`/`run_interval_replay`.

- [ ] **Step 2: Implement** the resolver, driver, budget rule and watcher seam. Watcher changes are additive-only; do not alter `tick`'s existing gate order for non-orchestrated codes.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_luozi_replay.py tests/guanlan/test_seats_watcher.py -v` (use the existing watcher test module path found at implementation time) — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py guanlan_v2/seats/watcher.py tests/orchestration/test_luozi_replay.py
git commit -m "feat(orchestration): DecisionSchedule-driven interval replay with per-point PIT snapshots"
```

---

## Task 5: Dual curves + Phase 4 Evaluator handoff

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py`
- Test: `tests/orchestration/test_dual_curves.py`

**Consumes:** Phase 6 `ShadowBacktestRunner` — both entries: `run(intents, *, start, end)` (deterministic execution over frozen intents) and the deterministic dual-curve entry `run_targets(target_sets, *, run_config, calendar, clock)` with `DeterministicTargetSet` and `deterministic_apply_key` (domain `shadow-deterministic-apply-key-v1` over `{"domain","rule_id","point_ordinal","target_version"}`) — take-profit/max-hold/corporate-action ledger live in this runner layer; engine `Broker` has neither, `engine.py:15-16` (clause C1); engine `Broker/VirtualPortfolio/CostModel/limit helpers` as the fill baseline (unchanged); Phase 4 evaluator/TrialLedger surface (clause C2); Task 1 curve contracts; Task 4 driver output. Grounded seats fact: the deterministic direction rule precedent is the backend w-blend pure-factor path (`_hybrid_direction` with `w=1` ⇒ factor-score sign, `guanlan_v2/seats/api.py:510-524`).

**Produces:**

- `def derive_deterministic_targets(point: ReplayDecisionPoint, *, factor_scores: Mapping[str, FiniteFloat], universe: tuple[Symbol, ...], rule_id: Literal["deterministic-target-rule-v1"] = "deterministic-target-rule-v1", target_version: PositiveInt = 1) -> DeterministicTargetSet:` — pure, versioned, zero-LLM: sign of the PIT factor score at `decision_as_of` maps to full-weight/flat single-name targets (the frozen v1 rule mirrors the seats `w=1` pure-factor direction with dead-zone τ=0.15 exactly as `api.py:479`); scores must come from a `DataResult` produced under the point's PIT context (provenance digest required — `[UNSOURCED]` scores are refused). The result is the Phase 6 `DeterministicTargetSet` (`rule_id`, `point_ordinal = point.point_ordinal`, `target_version`, `session_date` from the point, positions + `cash_weight`) — never a `TargetPortfolioIntent`. Rule changes require a new `rule_id`.
- `def build_dual_curves(*, execution_config: ShadowExecutionConfig, points: tuple[ReplayDecisionPoint, ...], deterministic_target_sets: tuple[DeterministicTargetSet, ...], intents: tuple[TargetPortfolioIntent, ...], shadow_runner: ShadowBacktestRunner) -> DualCurveReport:` — executes both lanes through the **same** runner instance/configuration: same universe, `init_cash`, data snapshot (`data_snapshot_content_digest`/`vintage_manifest_digest`), `calendar_id`, cost model (`cost_model_digest`), `matching_engine_version`, clock and `intrabar_exit_priority` — all bound by `execution_config` and re-verified against the runner's own configuration digest before the first bar. Intent lane: `shadow_runner.run(intents, ...)` — apply-once per `(intent_id, scheduled_for, target_version)`; order/fill dedup by the Phase 6 key families; fills at `eligible_execution_at`'s bar per `execution_policy`/`execution_price_field`. Deterministic lane: the target sets are dispatched through the Phase 6 `ShadowBacktestRunner.run_targets(target_sets, *, run_config, calendar, clock)` entry — deduped on `deterministic_apply_key`, identical per-day loop and config digest as `run` (`run_config`/`calendar`/`clock` mismatch ⇒ `ShadowContractError`). Deterministic targets are **never** wrapped as `TargetPortfolioIntent`, never pass through the intents-only `run()`, and carry no `origin="LLM"` provenance — the lane's result keeps `intent_content_digests == ()` structurally (Phase 6 invariant, re-asserted here at the consumer). NAV series → `ShadowCurveSeries` per lane → `DualCurveReport` (with `not_causal_attribution=True` structural).
- `def submit_dual_curves_to_evaluator(report: DualCurveReport, *, run_id: NonEmptyStr, pool: ArtifactPool, ledger: TrialLedger, maturity_now: UtcDateTime) -> ShadowReplayRunState:` — stages/commits the `DualCurveReport` Artifact through the normal staged→barrier path and registers the interval result with the Phase 4 evaluator surface. If the interval's realized window is not yet mature (interval_end's realized data `available_at > maturity_now`), the run transitions to `WAITING_FOR_MATURITY` (Task 6 persists it) instead of feeding feedback — spec `.md:132`: shadow 结果成熟后才能进反馈.

**Required invariants:**

1. Two lanes with any differing config dimension (universe/cash/snapshot/calendar/cost/clock/priority) cannot produce a `DualCurveReport` — the Task 1 validator plus a runner-side pre-check both fire.
2. Curve points are produced only from runner NAV history; no synthetic smoothing; an all-watch deterministic lane yields a flat NAV series from `init_cash` (honest, never `null`-crash).
3. Duplicate intent re-application changes nothing (apply-once), while distinct `order_kind`/`trigger_bar`/multi-fill events within one target application are all preserved (not swallowed) — spec §11.
4. The evaluator handoff never bypasses maturity: immature interval ⇒ `WAITING_FOR_MATURITY` state, no TrialLedger reveal; mature ⇒ registered exactly once (idempotent by run identity).
5. No LLM call exists anywhere in this task's call graph (`ShadowDecisionAgent`-style zero-LLM execution; the runner exposes `n_calls == 0`).
6. `engine/financial_analyst/**` untouched: the Task 0-recorded engine signature/byte-digest pins still match, compared in-test (no git subprocess).

- [ ] **Step 1: Write failing tests** — fixture bars + 3 points; assert config-attestation refusals, apply-once/no-swallow semantics, flat-lane honesty, maturity gating and the deterministic rule's exact v1 mapping vectors (including the τ dead zone).

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_deterministic_rule_v1_vectors` | factor scores `{+0.5, +0.14, 0.0, -0.14, -0.5}` with τ=0.15 | full-weight / flat / flat / flat / flat-or-exit mapping exactly as the frozen v1 table; vectors pinned in-test |
| `test_unsourced_scores_refused` | score mapping without a provenance digest | typed refusal; no target produced |
| `test_config_mismatch_refused_at_runner` | runner configured with different `cost_model_digest` than `execution_config` | pre-bar refusal (`ShadowContractError` from `run_targets` / the runner pre-check); no fills |
| `test_deterministic_lane_envelope_free` | deterministic lane built via `run_targets` | no `TargetPortfolioIntent` constructed; lane result `intent_content_digests == ()`; no `origin="LLM"` provenance anywhere |
| `test_same_bars_same_calendar_both_lanes` | instrumented runner | both lanes consume the identical bar stream and calendar id |
| `test_apply_once_duplicate_intent` | same `(intent_id, scheduled_for, target_version)` delivered twice | one application; NAV identical to single delivery |
| `test_multi_fill_not_swallowed` | one target producing two orders (distinct `order_kind`) and a partial fill sequence | all orders/fills present with distinct dedup keys |
| `test_flat_lane_honest` | all-watch deterministic lane | flat NAV series from `init_cash`; `trade_count == 0`; no `null`/crash |
| `test_curve_points_from_nav_only` | instrumented runner NAV history | series points equal NAV history 1:1; no interpolation |
| `test_report_construction` | both lanes green | `DualCurveReport` validates; `delta_total_return` matches hand computation; `not_causal_attribution=True` |
| `test_immature_interval_parks` | `maturity_now < interval_end` realized availability | `WAITING_FOR_MATURITY` state returned; TrialLedger untouched (spy) |
| `test_mature_registered_once` | run handoff twice for same run identity | evaluator registration exactly once (idempotent) |
| `test_zero_llm_calls` | full dual-curve build | runner-reported `n_calls == 0`; no model gateway interaction recorded |
| `test_engine_untouched` | recompute the Task 0-recorded engine signature/byte-digest pins in-test | identical to the pinned values (no git subprocess) |

Run now: `pytest tests/orchestration/test_dual_curves.py -v` — expected FAIL on missing functions.

- [ ] **Step 2: Implement** the three functions in `luozi.py`, consuming the Phase 6 runner API per clause C1.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_dual_curves.py tests/orchestration/test_luozi_replay.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py tests/orchestration/test_dual_curves.py
git commit -m "feat(orchestration): dual curves under one execution attestation + evaluator handoff"
```

---

## Task 6: WAITING_FOR_MATURITY persistence + idempotent wakeup + daily Lane 0 gate

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/luozi.py`
- Modify: `guanlan_v2/autonomy/runtime.py`, `guanlan_v2/autonomy/playbooks.py`, `guanlan_v2/screen/rescore.py` (all additive)
- Test: `tests/orchestration/test_luozi_wakeup.py`

**Consumes:** Phase 2 `RuntimeStateCellStore` + closed CAS command + `RuntimeUnitOfWork` (P2:180 permits extending the reviewed startup namespace union); Phase 1 `EXPERIMENT_STATE_CHANGED` event type (`events.py`, existing member — no new EventType); `ExperimentStatus`; autonomy precedent `maybe_enqueue_daily_review` (three gates: env flag / `note=="daily-scheduler"` / not-already-today; self-swallows — `guanlan_v2/autonomy/runtime.py:118-140`), `PLAYBOOKS` registry (`playbooks.py:5-10`), scheduler seam `guanlan_v2/screen/rescore.py:379-380`. Clauses C2, C5, C6.

**Produces:**

- Two new state-cell namespaces appended to the reviewed startup union (clause C5): `adapters.replay_head.v1` (current `ShadowReplayRunState` head per experiment) and `adapters.replay_operation.v1` (wakeup operation results keyed by `wakeup_key` digest). One UoW per transition: typed state payload put + head CAS + operation-result CAS + `ExperimentStateChanged` RunEvent — all-or-none.
- `def persist_replay_state(state: ShadowReplayRunState, *, stores, idempotency_key: NonEmptyStr) -> PayloadRef:` — persist-then-publish; same-key/same-content replay returns the stored ref; conflict raises `IdempotencyConflict`.
- `def wakeup_shadow_replay(wakeup_key: str, *, bindings: ReplayRuntimeBindings, now: UtcDateTime) -> ReplayWakeupReceipt:` — idempotent: ① resolve the head state by `wakeup_key`; unknown key ⇒ typed error; ② `now < resume_after` ⇒ `outcome="not_mature"`, state untouched; ③ already processed (operation cell hit) ⇒ `outcome="already_processed"` returning the original receipt; ④ mature ⇒ process **only matured batches** (points/intervals whose realized data is now available under PIT), re-run `submit_dual_curves_to_evaluator`, advance state (`COMPLETED` or a new `WAITING_FOR_MATURITY` with strictly later `resume_after` and a **new** `wakeup_key`), `outcome="resumed"|"completed"`. Wakeup never re-executes decision points and never regenerates intents — it only consumes maturity.
- `wakeup_key` derivation: `content_digest({"domain": "adapters-replay-wakeup-v1", "experiment_id": ..., "resume_after": ..., "state_digest": ...})` — service-derived, never caller-chosen.
- Autonomy wiring: `def maybe_enqueue_shadow_wakeup(note: str) -> bool` in `autonomy/runtime.py` — gates: env `GUANLAN_SHADOW_WAKEUP == "1"`; `note == "daily-scheduler"`; not already run today (scan `read_jobs` for today's `shadow_replay_wakeup`); self-swallows exceptions (same shape as `maybe_enqueue_daily_review`). New playbook `PLAYBOOKS["shadow_replay_wakeup"]` iterates pending `WAITING_FOR_MATURITY` heads and calls `wakeup_shadow_replay` per key; job report lists per-key outcomes; write scope = orchestration stores only (no picks/signal/seats writes — review-officer red-line shape). One additive call site in `rescore.py` next to `maybe_enqueue_daily_review(note)`.
- Daily Lane 0 gate (integration amendment 2026-07-18): `def maybe_enqueue_lane0_bootstrap(note: str) -> bool` in `autonomy/runtime.py` — same three-gate shape (env `GUANLAN_LANE0_DAILY == "1"`; `note == "daily-scheduler"`; not already run today) and self-swallowing. New playbook `PLAYBOOKS["lane0_bootstrap"]`: materialize the Phase 5 bootstrap preset draft → Phase 1 validation + Phase 2 reservation → Phase 7 `register_and_try_lease` (clause C7); with an active lane0 `ApprovalLease` the candidate admits (real `PlanApproval`, actor `lease:<id>`) → `admit_after_approval` → `run_plan` under the BOOTSTRAP profile on the durable stores (Task 1b), committing the `ContextSnapshot` and appending the day's case seeds per the Phase 5 lifecycle; no active lease ⇒ job report `"skipped: no active lease"` — an honest skip, not an error, nothing admitted. Snapshot `as_of` = the data date of the evening chain; intraday consumers reference the latest committed snapshot with its recency badge. Same additive `rescore.py` call-site block as the wakeup gate.

**Required invariants:**

1. Duplicate wakeup delivery (same key, concurrent or sequential) applies effects exactly once; the second caller gets the stored receipt byte-identically.
2. Crash between state put and event append is impossible to observe (single UoW, all-or-none under injected failure).
3. A `WAITING_FOR_MATURITY` state always has a resolvable `resume_after`/`wakeup_key`; wakeup with a stale (superseded) key returns `already_processed`, never double-advances.
4. The scheduler gate is off by default (env unset ⇒ `False`, no job) and never raises into the rescore seam.
5. Every state transition appends `ExperimentStateChanged` with the state's `PayloadRef`; replaying the journal folds to the current head.
6. `maybe_enqueue_lane0_bootstrap` honors the same off-by-default/self-swallow contract; the lane0 playbook admits nothing without an active lease and reports the skip honestly.

- [ ] **Step 1: Write failing tests** — fake stores with injectable crash points; duplicate-delivery matrix; not-mature/mature/partial-maturity paths; scheduler gate matrix (env off / wrong note / already-today / happy path); journal fold equality.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_persist_state_single_uow` | crash injected between payload put and head CAS | neither visible; retry with same key lands both |
| `test_persist_idempotent_conflict` | same key, different state content | `IdempotencyConflict` |
| `test_wakeup_unknown_key` | never-issued key | typed error; no state read side effects |
| `test_wakeup_not_mature` | `now < resume_after` | `outcome="not_mature"`; state digest unchanged |
| `test_wakeup_duplicate_delivery` | sequential + concurrent double delivery of one key | second/racing caller gets the byte-identical stored receipt; effects once |
| `test_wakeup_stale_key_superseded` | wake, re-park with new key, wake old key again | `outcome="already_processed"`; head not double-advanced |
| `test_wakeup_partial_maturity_reparks` | half the interval matured | matured batch processed; new state `WAITING_FOR_MATURITY` with strictly later `resume_after` and a **different** `wakeup_key` |
| `test_wakeup_completion` | full maturity | `outcome="completed"`; state `COMPLETED` with `curve_report_ref` |
| `test_wakeup_never_reexecutes_points` | spy on admission/driver | zero decision-plan admissions during wakeup |
| `test_wakeup_key_service_derived` | attempt caller-chosen key on state construction path | impossible by API shape (no parameter); derivation matches the pinned domain-tagged digest |
| `test_event_per_transition_and_fold` | three transitions | three `ExperimentStateChanged` events; journal fold equals head |
| `test_scheduler_gate_matrix` | env unset / wrong note / already-today / happy | `False`/`False`/`False`/`True`+job enqueued |
| `test_gate_self_swallows` | playbook raising | `maybe_enqueue_shadow_wakeup` returns without raising into the rescore seam |
| `test_playbook_write_scope` | run playbook against fakes | writes only orchestration stores/job events; no picks/signal/seats/memory writes |
| `test_lane0_gate_matrix` | env unset / wrong note / already-today / happy | `False`/`False`/`False`/`True`+job enqueued |
| `test_lane0_no_lease_honest_skip` | no active lease (admission spy) | job report `skipped: no active lease`; zero admissions |
| `test_lane0_leased_run_commits_snapshot` | active lease + fake stores | BootstrapPlan admitted with actor `lease:<id>`; snapshot committed; case seeds appended |

Run now: `pytest tests/orchestration/test_luozi_wakeup.py -v` — expected FAIL on missing functions/namespaces.

- [ ] **Step 2: Implement** persistence, wakeup, key derivation, playbook and gates.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_luozi_wakeup.py tests/orchestration/test_dual_curves.py -v` plus the existing autonomy suite module — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/luozi.py guanlan_v2/autonomy/runtime.py guanlan_v2/autonomy/playbooks.py guanlan_v2/screen/rescore.py tests/orchestration/test_luozi_wakeup.py
git commit -m "feat(orchestration): WAITING_FOR_MATURITY persistence + idempotent shadow wakeup"
```

---

## Task 7: 帷幄 ONLINE adapter (adapters/weiwo.py)

**Files:**
- Create: `guanlan_v2/orchestration/adapters/weiwo.py`
- Test: `tests/orchestration/test_weiwo_adapter.py`

**Consumes:** Task 3 `build_online_data_context`/`LiveClientSource`; Phase 5 `BootstrapPlan`; Phase 7 dynamic planner + REQUIRED approval; Phase 4 `run_optimize` (`evaluate_validation` pluggable — spec `.md:123`: 帷幄绑定 `run_graph`); `workflow/executor.run_graph` (frozen signature `run_graph(graph, overrides=None, on_node=None, prefer_model_terminal=False)`, `executor.py:568-570`; research-loop precedent `_run_graph_eval` calls it in-process, `research/loop.py:81-91`); factorlib draft precedent `_save_draft` → `SaveIn(..., status="draft")` (`research/loop.py:94-104`) and `_VALID_SAVE_STATUS = {"", "draft"}` (`guanlan_v2/factorlib/api.py:60` — **no "rejected" status exists**). Clauses C2, C3, C6.

**Produces:**

- `def evaluate_validation_via_run_graph(candidate_graph: Mapping[str, Any], *, overrides: Mapping[str, Any] | None = None, on_node: Callable[[str, str, str], None] | None = None) -> Mapping[str, Any]:` — thin wrapper over `run_graph(graph, overrides=..., on_node=..., prefer_model_terminal=True)` executed in a worker thread (`asyncio.to_thread` from async contexts; never from the event loop synchronously, never via HTTP self-call from a coroutine). It adds nothing to and removes nothing from `run_graph` semantics; the Phase 4 `evaluate_validation` binding adapts this callable to the reviewed optimizer signature (clause C2). Bit-for-bit口径 test: for a fixture graph, the wrapper's metrics equal a direct `wex.run_graph(..., prefer_model_terminal=True)` call.
- `def run_weiwo_research(request: OrchestrationRequest, *, bindings: WeiwoRuntimeBindings) -> ShadowReplayRunState | WeiwoRunReceipt:` — flow: ① `build_online_data_context` (as_of frozen at start); ② Bootstrap → `ContextSnapshot` (memory facade `prepare_online`); ③ open research MainPlan via Phase 7 (DYNAMIC with REQUIRED human approval, or PRESET/`fallback_preset_id` from the request); ④ optimizer loop via Phase 4 `run_optimize` with `evaluate_validation=evaluate_validation_via_run_graph`; ⑤ products land draft-only (below). `WeiwoRunReceipt` is an internal (unregistered) result carrier: `run_id`, `context_snapshot_digest`, `draft_ids: tuple[NonEmptyStr, ...]`, `stop_reason`. `WeiwoRuntimeBindings` is the internal frozen service-port carrier symmetric to `ReplayRuntimeBindings`.
- `def save_draft_to_factorlib(candidate: Mapping[str, Any], *, source: NonEmptyStr = "orchestration_weiwo", provenance_digest: DigestHex) -> Mapping[str, Any]:` — reuses the factorlib save path with `status="draft"` exactly like `_save_draft`; never any other status; promotion remains the existing human `/factorlib/promote` gate. **Honest rejection handling:** candidates failing gates are recorded only in run events/TrialLedger (append-only); nothing is written to factorlib for them and no "rejected" status is invented.
- Capability discipline: the weiwo catalog binding grants read-only data capabilities (the Task 3 `ONLINE` source behind the existing Phase 3 method ids) plus `memory.propose` where Phase 3/8 granted it; **no order/signal write capability, no memory-accept, no code/skill write** — asserted by scanning the resolved catalog in tests.

**Required invariants:**

1. The run's `as_of` is the start-frozen timestamp everywhere: ContextSnapshot, every `DataRequest`, every provenance record; an advancing clock during the run changes nothing semantic.
2. Every product write is `status="draft"`; a save attempt with any other status is rejected by factorlib itself (regression against `_VALID_SAVE_STATUS`) and by the adapter's own guard first.
3. `evaluate_validation_via_run_graph` leaves `run_graph`'s module surface untouched (no monkeypatching, no `_DISPATCH` edits) and produces口径-identical metrics to a direct call.
4. Unapproved DYNAMIC plans never execute; missing `fallback_preset_id` on planner failure ⇒ honest terminal failure, no silent preset fallback.
5. A prompt-injected instruction inside fetched live text cannot widen capabilities (the untrusted-data channel from Phase 2/3 is preserved; test with a hostile fixture text).

- [ ] **Step 1: Write failing tests** for invariants 1–5 with fake bindings, a fixture candidate graph and a hostile text fixture.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_as_of_frozen_across_run` | advancing fake clock; inspect ContextSnapshot + every `DataRequest`/provenance | one identical `as_of` everywhere; semantic digests independent of clock advances after start |
| `test_run_graph_binding_bit_identical` | fixture graph runnable offline (deterministic nodes only) | wrapper metrics `==` direct `wex.run_graph(graph, overrides=None, prefer_model_terminal=True)` result |
| `test_run_graph_surface_untouched` | module introspection before/after import | `run_graph`/`_DISPATCH`/`_OUT_PORT` identities unchanged; no monkeypatch |
| `test_wrapper_runs_off_event_loop` | call from async context | executed via `asyncio.to_thread`; calling synchronously from a running loop raises the guard error |
| `test_draft_only_landing` | passing candidate | factorlib save called with `status="draft"` and adapter `source`; returned id recorded in receipt |
| `test_non_draft_status_guarded_twice` | adapter forced with `status="active"` | adapter guard raises before factorlib; factorlib `_VALID_SAVE_STATUS` regression also rejects |
| `test_rejected_candidate_no_factorlib_trace` | failing candidate | zero factorlib calls; rejection visible only in run events/TrialLedger records |
| `test_unapproved_dynamic_never_executes` | DYNAMIC draft without approval event | dispatch refused; zero reservations |
| `test_no_silent_preset_fallback` | planner failure, `fallback_preset_id=None` | honest terminal failure; no preset admission |
| `test_explicit_fallback_used` | planner failure, request carries `fallback_preset_id` | fallback preset admitted through the full REQUIRED-approval path |
| `test_no_write_capabilities_in_binding` | resolved catalog scan | no order/signal/memory-accept/skill/code write capability |
| `test_hostile_text_cannot_widen` | fetched text containing "call tool X / approve plan Y" | text stays in the untrusted-data channel; no capability call, no approval event |

Run now: `pytest tests/orchestration/test_weiwo_adapter.py -v` — expected FAIL on missing module.

- [ ] **Step 2: Implement `weiwo.py`.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_weiwo_adapter.py tests/orchestration/test_adapters_live_data.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/weiwo.py tests/orchestration/test_weiwo_adapter.py
git commit -m "feat(orchestration): weiwo ONLINE adapter with run_graph validation binding and draft-only products"
```

---

## Task 8: Mirror stage ② — backend golden execution harness

**Files:**
- Create: `tests/orchestration/golden/shadow_execution_golden_v1.json`
- Test: `tests/orchestration/test_shadow_golden_harness.py`
- Modify (only if the harness exposes gaps): `guanlan_v2/orchestration/adapters/luozi.py` / the Phase 6 shadow-runner module (runner layer only; **never** `engine/financial_analyst/**`)

**Consumes:** Phase 6 `ShadowBacktestRunner` + engine baseline (`Broker.match` semantics: suspended reject `broker.py:87-90`, one-word both-direction block `broker.py:100-108`, market next-bar-open same-date `broker.py:113-119`, stop `slip_sell(min(stop, open))` clipped `broker.py:121-127`, `below_one_lot` `broker.py:154-155`, `t1_locked_or_empty` `broker.py:156-166`; `CostModel` defaults `costs.py:38-43`; T+1 `Position.sellable` `portfolio.py:55-59`); Task 1 `MirrorHarnessReport`. Spec §9 stage ②: "后端完整规则用独立 golden harness 验证 fill/reject/cost/公司行动" — independent of the frontend; frontend-absent behaviors are legitimate here (the false-gate prohibition cuts the other way: stage ① must not demand them of the frontend).

**Produces:**

- `shadow_execution_golden_v1.json` — hand-authored, hand-computed, never regenerated from code. Each case record: `{case_id, description, bars, prev_close, is_st, targets_or_intents, config, expected: {fills, rejects, costs, nav_series, badges}}`. The reviewed case set:

| case_id | Input essence | Hand-computed expectation |
|---|---|---|
| `suspended_reject` | bar with NaN OHLC / `vol<=0` | reject reason `suspended` (`broker.py:87-90`); position/cash unchanged |
| `one_word_up_blocks_buy` | high==low at up-limit vs ref prev close, vol>0 | reject `one_word_limit_up*`; both directions blocked (`broker.py:100-108`) |
| `one_word_down_blocks_sell` | one-word down board | reject `one_word_limit_down*` |
| `t1_same_day_sell_locked` | buy at T, sell attempt same T | reject `t1_locked_or_empty` via `Position.sellable` (`portfolio.py:55-59`); sell at T+1 fills |
| `below_one_lot_reject` | cash affords <100 shares | reject `below_one_lot` (`broker.py:154-155`) |
| `market_next_bar_open_same_date_only` | market order, next bar next day | no cross-day fill (`broker.py:113-119`) |
| `stop_fill_slipped_clipped` | stop trigger with gap-down open | fill `slip_sell(min(stop, open))` clipped to `[low, high]` (`broker.py:121-127`) with sell costs |
| `limit_touch_buy` / `limit_touch_sell` | limit vs bar high/low touch | touch rules `broker.py:129-139`; no-touch ⇒ no fill |
| `costs_commission_min5_stamp_sell_transfer_sh` | small SH round trip | commission floor 5元, sell-only stamp, SH transfer both sides (`costs.py:38-56`); hand-summed cash after |
| `take_profit_runner_level` | `take_profit_pct` touched intrabar | runner-level exit (engine has none, `engine.py:15-16`); fill per schedule price field |
| `max_hold_runner_level` | `max_hold_bars` reached | runner-level close-out at bar close |
| `intrabar_priority_worst_case` | same-bar stop+take double touch | worst-case resolution (schedule default, spec `.md:833`) |
| `intrabar_priority_stop_first` / `_take_profit_first` | same double touch, explicit priority | matching resolution; the three cases differ only in the priority field |
| `dividend_cash_credit` | ex-div date inside hold | cash credited, position qty unchanged, NAV continuous per the Phase 6 ledger |
| `split_qty_adjustment` | 10-for-1 split inside hold | qty×10, avg_cost/10, NAV continuous |
| `unrepresentable_corporate_action_refused` | attempted raw event payload with `kind="rights_issue"` | unconstructible at the contract boundary: `CorporateActionEvent.kind` is the closed Literal `{cash_dividend, stock_bonus, split}`, so construction fails Pydantic validation; the harness asserts the typed refusal (never a silent ledger effect, never a ledger behavior) |
| `apply_once_duplicate_intent` | duplicate `(intent_id, scheduled_for, target_version)` | single application |
| `multi_fill_not_swallowed` | one apply → two order kinds, partial fills | every `(order_id, fill_seq)` present |
- `test_shadow_golden_harness.py` — loads the fixture, runs every case through the shadow runner, emits one `MirrorHarnessReport` (fixture digest bound), asserts `all_passed` and pins `matching_engine_version` to the `ShadowExecutionConfig`/`DecisionSchedule` field so a runner behavior change forces a version bump.
- Gap closure: any red case caused by a missing runner behavior is fixed in the runner layer (adapter/Phase 6 modules) with its own red/green loop inside this task; any red case caused by a wrong hand expectation is a reviewed fixture correction commit with rationale in the commit message. Engine diffs remain empty.

**Required invariants:**

1. Harness runs fully offline (fixture bars only), deterministic across ordering/threads.
2. `MirrorHarnessReport.fixture_digest` equals the canonical digest of the loaded fixture; digest drift fails the suite before any case runs.
3. Changing `matching_engine_version` without a reviewed fixture change fails; changing behavior without bumping the version fails.
4. Engine untouched: the Task 0-recorded engine signature/byte-digest pins still match, compared in-test (no git subprocess).

- [ ] **Step 1: Author the fixture + write the failing harness** (red = cases exist, runner path or expectations not yet wired).

Run now: `pytest tests/orchestration/test_shadow_golden_harness.py -v` — expected FAIL (missing fixture wiring or red cases).

- [ ] **Step 2: Wire the harness and close exposed runner-layer gaps.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_shadow_golden_harness.py tests/orchestration/test_dual_curves.py -v` — expected PASS.

```bash
git add tests/orchestration/golden/shadow_execution_golden_v1.json tests/orchestration/test_shadow_golden_harness.py guanlan_v2/orchestration/adapters/luozi.py
git commit -m "test(orchestration): mirror stage-2 backend golden execution harness"
```

---

## Task 9: Phase 9 registry/catalog chain node + goldens

**Files:**
- Create: `guanlan_v2/orchestration/adapters/chain.py`
- Create: `tests/orchestration/golden/phase9_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase9_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase9_registry_chain.py`

**Consumes:** Phase 8 `PHASE8_REGISTRY_DIGEST`/`PHASE8_CATALOG_DIGEST` and their builders (sole bases); Phase 1 `SchemaRegistry`/`build_catalog_snapshot`/`validate_catalog_snapshot`; Phase 3 `DataSourceDescriptor`/`DataSourceRegistry`/`DataMethodSpec` (existing method ids only, clause C4) + the `DataRuntimeBridge` descriptor pattern; Tasks 1–3 models/adapters.

**Produces:**

- `PHASE9_PUBLIC_MODELS: tuple[type[ContractModel], ...]` — exactly: `ReplayDecisionPoint`, `ShadowExecutionConfig`, `ShadowCurveSeries`, `DualCurveReport`, `ShadowReplayRunState`, `ReplayWakeupReceipt`, `MirrorHarnessReport`, `EntryPointRetirementGate`, `RetirementReadinessReport`.
- `PHASE9_INTERNAL_MODELS` — reviewed-reason map for the nested/carrier types: `ShadowCurvePoint`, `MirrorHarnessCaseResult`, `RetirementCriterion`, `RetirementCriterionResult`, `ReplayRuntimeBindings`, `WeiwoRuntimeBindings`, `WeiwoRunReceipt`, `ReplayPointClock`, `PitReaderRawSource`, `LiveClientSource` (reason categories follow the Phase 1 taxonomy: value object / run-context carrier / service port).
- `PHASE9_BASE_REGISTRY_DIGEST` (== the exact `PHASE8_REGISTRY_DIGEST`) + `PHASE9_REGISTRY_DIGEST` + `def build_phase9_registry(expected_phase8_digest: DigestHex) -> SchemaRegistry:` — verifies the Phase 8 manifest/digest first, registers the cumulative model set, seals; rejects any other base digest; inherited JSON Schemas byte-identical; golden `phase9_schema_manifest_v1.json` hand-frozen, never auto-regenerated.
- `PHASE9_BASE_CATALOG_DIGEST` (== `PHASE8_CATALOG_DIGEST`) + `PHASE9_CATALOG_DIGEST` + `def build_phase9_catalog_snapshot(phase8_snapshot: WorkerCatalogSnapshot, *, replay_source_descriptor: DataSourceDescriptor, live_source_descriptor: DataSourceDescriptor, resolved_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot:` — registers the Task 2/3 adapters as two new `DataSourceDescriptor`s (the `PIT_REPLAY` PitReader source and the `ONLINE` live_client source) **behind the existing Phase 3 method ids** (`get_ohlcv`/`get_news`/`get_verified_snapshot` families, clause C4), `DataMode`-routed via the `DataSourceRegistry` under the existing `DataRuntimeBridge` pattern. **No new method id, no new capability id, no worker-allowlist edit:** a worker that already holds the Phase 3 data capabilities reaches the new sources purely through mode routing; the catalog carries only the new source/config materials (handler + source-config manifest entries) and their digests. **No new worker id is added** — the final-worker table is inherited from the reviewed Phase 8 catalog and pinned byte-identical; Phase 9 adds no worker id and writes **no integer assertion on the worker count** (same vaccine as the Task 3 no-hardcoded-source-count rule); the deterministic target rule is adapter-internal, not a worker. Golden `phase9_catalog_manifest_v1.json`.
- **EventType decision (explicit):** Phase 9 adds no EventType member and flips no absence guard; a test pins the Phase 8 EventType value set unchanged.

**Required invariants:**

1. `build_phase9_registry(PHASE8_REGISTRY_DIGEST)` succeeds; any other digest raises; the sealed manifest equals the golden byte-for-byte.
2. Every inherited schema entry is byte-identical to its upstream golden entry; only the nine new `@1` entries are additions.
3. Catalog: the two new source descriptors and their materials resolve through `validate_catalog_snapshot`; the data method-id set and every worker allowlist are byte-identical to Phase 8's (pinned); `compat.*` stays `static_legacy_only`; no order/signal write capability exists in the snapshot (scan by operation/schema).
4. No "latest" alias, no second digest implementation, no upstream golden touched (`git diff` on upstream goldens empty).

- [ ] **Step 1: Write failing chain tests** (missing module/goldens), including the EventType-unchanged pin, the method-id/worker-allowlist-unchanged pins and the no-write-capability scan.

Run now: `pytest tests/orchestration/test_phase9_registry_chain.py -v` — expected FAIL.

- [ ] **Step 2: Implement `chain.py`, freeze the goldens by review** (record digests from a one-off construction, review, commit — never regenerate in test code).

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase9_registry_chain.py tests/orchestration/test_contract_completeness.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/chain.py tests/orchestration/golden/phase9_schema_manifest_v1.json tests/orchestration/golden/phase9_catalog_manifest_v1.json tests/orchestration/test_phase9_registry_chain.py
git commit -m "feat(orchestration): phase9 cumulative registry/catalog chain node + goldens"
```

---

## Task 10: Adapter router + seats-compatibility persistence (UI 只填充不重建)

**Files:**
- Create: `guanlan_v2/orchestration/adapters/api.py`
- Modify: `guanlan_v2/server.py` (additive mount)
- Test: `tests/orchestration/test_adapters_api.py`

**Consumes:** server thin-shell pattern (`guanlan_v2/server.py:113-331`: per-module `build_*_router` appended; backend changes need a 9999 restart, verification runs use port 9998); seats append-only stores and shapes (`var/seats_runs.jsonl` head shape `seats/api.py:1024-1043`; `var/seats_decisions.jsonl` row shape via `_persist_decision` `api.py:931-953` — optional keys written only when non-empty; run listing filters `api.py:979-1007`); frontend coordinate rule: decisions must be keyed by `asof` timestamp, never frontend `idx` (`ui/seats/luozi-data.jsx:797-845` `mapDecsToFrame`). Clause C6 for exact seats helper names.

**Produces:**

- `def build_adapters_router() -> APIRouter` mounted at `/orchestration` in `server.py` (one additive block, same shape as the existing sixteen mounts); production wiring binds `build_durable_runtime_stores` (Task 1b; root default `var/orchestration/`, env-overridable for 9998 verification) while tests keep tmp/in-memory stores. Endpoints (all JSON; shadow/draft only; **no endpoint executes an unapproved plan or writes orders/signals**):
  - `POST /orchestration/replay/start` — body `{code: str, schedule_id: str, schedule_version: str, start_date: "YYYY-MM-DD", end_date: "YYYY-MM-DD", strategy_id?: str}`; validates the schedule against the Phase 6 registry (unknown id/version ⇒ 422 `{ok: False, reason}`), creates the `OrchestrationRequest` + candidate and returns `{ok: True, request_id, experiment_id, status: "awaiting_approval", candidate_plan_digest}` — approval flows through the Phase 7 console surface; the route never self-approves (AUTO forbidden).
  - `GET /orchestration/replay/state?experiment_id=` — `{ok, state: <ShadowReplayRunState semantic projection>, badges: [...]}`; unknown id ⇒ `{ok: False, reason: "unknown_experiment"}` with 404.
  - `GET /orchestration/replay/curves?experiment_id=` — `{ok, report: <DualCurveReport projection>}` (curve points, config attestation, `not_causal_attribution` flag) when `COMPLETED`; pre-maturity ⇒ `{ok: True, report: null, status: "waiting_for_maturity", resume_after}` — explicit, never a fabricated curve.
  - `POST /orchestration/replay/wakeup` — body `{wakeup_key: str}`; returns `{ok, receipt: <ReplayWakeupReceipt projection>}` (idempotent by construction; unknown key ⇒ 404 typed reason).
  - `POST /orchestration/weiwo/start` — body `{goal: str, fallback_preset_id?: str}` → `{ok, request_id, run_id, status: "awaiting_approval"}`; `GET /orchestration/weiwo/state?run_id=` → `{ok, status, draft_ids, stop_reason}` — symmetric, draft-only.
- `def persist_replay_run_compat(state: ShadowReplayRunState, *, decisions: tuple[Mapping[str, Any], ...], run_head: Mapping[str, Any]) -> None:` — after an interval completes, appends one run head to `var/seats_runs.jsonl` (existing shape: `run_id`/`code`/`strategy_id`/`tf`/`start_date`/`end_date`/counts/`model`) and one row per decision point to `var/seats_decisions.jsonl` (existing shape with `run_id`, `source: "orchestrated"`, `asof` = the point's `decision_as_of` in the store's timestamp convention, direction/confidence/rationale from the committed decision Artifact) via the seats module's in-process helpers — never HTTP self-call from a coroutine, never a new store, never a mutated legacy record shape. The existing RunPicker/replay UI then lists and replays orchestrated runs with zero frontend changes.
- Honesty badges: rows and API responses carry `source:"orchestrated"` and the run head carries `model` so existing badge surfaces display provenance; degraded lanes (e.g. curve unavailable pre-maturity) return explicit `status` fields, never fabricated curves.

**Required invariants:**

1. Router registration adds routes without altering any existing route table entry (snapshot test of route paths before/after).
2. `persist_replay_run_compat` writes shapes that the existing `GET /seats/runs` and `GET /seats/decisions?run_id=` handlers list/filter correctly (round-trip test through those handlers in-process).
3. Appends are append-only; a replay of the same completed state is idempotent (no duplicate run head — guarded by `run_id` presence check before append).
4. No endpoint mutates orchestration state except `wakeup` (idempotent) and `start` (request creation); everything else is read-only.
5. `start` on a request whose plan was never approved leaves zero reservations and zero runs (admission untouched).

- [ ] **Step 1: Write failing router tests** with FastAPI test client + tmp jsonl stores, covering invariants 1–5.

Test matrix:

| Test | Fixture | Expected |
|---|---|---|
| `test_route_table_additive` | route-path snapshot before/after mount | existing paths unchanged; exactly the six new paths added |
| `test_start_awaits_approval` | valid start body | `status="awaiting_approval"`; zero reservations, zero runs (admission spy) |
| `test_start_unknown_schedule_422` | bad `schedule_id` | 422 `{ok: False, reason}` |
| `test_state_unknown_404` | unknown `experiment_id` | 404 typed reason |
| `test_curves_premature_honest` | `WAITING_FOR_MATURITY` state | `report: null` + `status`/`resume_after`; no fabricated points |
| `test_wakeup_idempotent_via_http` | double POST same key | identical receipt bodies |
| `test_compat_run_head_roundtrip` | `persist_replay_run_compat` then in-process `GET /seats/runs` | orchestrated run listed with correct `run_id`/`code`/counts/`model` |
| `test_compat_decisions_roundtrip` | then `GET /seats/decisions?run_id=` | rows filtered correctly; `source=="orchestrated"`; `asof` equals point `decision_as_of` convention; no frontend-`idx` field invented |
| `test_compat_append_only_idempotent` | persist same completed state twice | one run head; decision rows not duplicated |
| `test_legacy_shape_unchanged` | row key-set diff vs a pre-Phase-9 fixture row | only optional keys added, written only when non-empty (grounded convention `seats/api.py:949`) |
| `test_no_mutating_reads` | GET endpoints against spied stores | zero writes |

Run now: `pytest tests/orchestration/test_adapters_api.py -v` — expected FAIL on missing router.

- [ ] **Step 2: Implement `api.py` + the server mount.** Note in the task log that 9999 needs a restart to serve the new routes; tests run in-process and do not touch the live server.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_adapters_api.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/api.py guanlan_v2/server.py tests/orchestration/test_adapters_api.py
git commit -m "feat(orchestration): adapters thin router + seats-compatible run/decision persistence"
```

---

## Task 11: Old-entry retirement gates (no removal in this phase)

**Files:**
- Create: `guanlan_v2/orchestration/adapters/retirement.py`
- Create: `tests/orchestration/golden/phase9_retirement_gates_v1.json`
- Test: `tests/orchestration/test_retirement_gates.py`

**Consumes:** Task 1 retirement contracts; the grounded legacy seams (console: `_spawn_bg` `console/api.py:508` → `_run_report_bg` `:520` → `_call_buddy_report` `:302`, ETF `:614`; swarm: `load_preset` `engine/financial_analyst/swarm/loader.py:32-37`, consumers `cli.py:713/717/799/803`, `tui.py:683`, frozen legacy semantics `docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md:27-59`; research: `POST /research/loop/start` `research/api.py:63-95,102-122` → `run_research_loop` `research/loop.py`). Spec §12.9: 红线/并发/恢复/e2e 全绿后才逐步下线旧入口; spec §12.8: 预算/模型档位与旧入口隔离.

**Produces:**

- `def default_retirement_gates() -> tuple[EntryPointRetirementGate, ...]` — exactly three reviewed instances, frozen into `phase9_retirement_gates_v1.json` (digest-pinned; changes are reviewed contract changes):
  1. `console.report_subprocess` → replacement "orchestrated report lane (Phase 8 lane workers via kernel)". Criteria: `redline-suite-green` (`pytest_suite`, selector `tests/orchestration/test_redline_regression.py`); `concurrency-recovery-green` (`pytest_suite`, selector `tests/orchestration/test_phase9_e2e.py::test_recovery` + the Phase 2 crash/replay suite ids); `report-parity` (`parity_fixture`: one fixed code/as_of orchestrated report Artifact covers the legacy report's reviewed section list with all numbers anchored/sourced; fixture digest recorded); `production-streak` (`operational_run_log`: ≥10 consecutive orchestrated production report runs with zero fallback to the subprocess lane, evidenced by run-event digests); `console-consumes-kernel` (`reviewed_artifact`: console card renders orchestrated results — existing UI, no rebuild).
  2. `swarm.load_preset_cli` → replacement "attested PRESET Plans (Phase 2 presets + Phase 8 catalog)". Criteria: `deep-dive-equivalence-green` (`pytest_suite`, the Phase 2 Task 10 equivalence suite); `radar-presets-attested` (`parity_fixture`: `mainline-radar` and `overseas-radar` mapped through `migrate_legacy_graph`-family evidence to attested Plans with equivalent dependency terminal states); `cli-kernel-path` (`reviewed_artifact`: CLI invocations documented to route via kernel or a reviewed sunset notice); `redline-suite-green`; `concurrency-recovery-green`.
  3. `research.loop_direct` → replacement "Phase 4 `run_optimize` via the weiwo adapter". Criteria: `factor-adapter-regression-green` (`pytest_suite`, the Phase 4 factor-research adapter regression); `draft-parity` (`parity_fixture`: same candidate inputs produce factorlib drafts with equal recipe digests through old and new paths); `stagnation-honesty-parity` (`pytest_suite`: stagnation guard/honest termination behave identically); `budget-isolation` (`pytest_suite`: old loop and kernel runs never share/duplicate reservations); `redline-suite-green`.
- `def evaluate_retirement_gate(gate: EntryPointRetirementGate, *, results: tuple[RetirementCriterionResult, ...], evaluated_at: UtcDateTime) -> RetirementReadinessReport:` — pure; `ready` ⇔ all green; any red/unavailable ⇒ not ready with reasons surfaced; result set must cover criteria exactly (missing/extra ⇒ `ValueError`).
- **Structural no-removal guard:** a test asserts all three legacy seams still import/resolve (the gate exists to *permit a future* removal commit, which must cite the green `RetirementReadinessReport` digest in its message; that commit is outside this plan).

**Required invariants:**

1. The golden gate instances' digests are pinned; editing a criterion silently fails the golden test.
2. `evaluate_retirement_gate` is fail-closed (unavailable ⇒ not ready) and pure (no I/O).
3. All three legacy entry points remain importable and未改动 at the end of this phase (`git diff` empty on their files except the additive watcher/rescore seams of Tasks 4/6).

- [ ] **Step 1: Write failing tests** — evaluator matrix (all-green / one-red / one-unavailable / coverage mismatch), golden digest pin, no-removal guard.

Run now: `pytest tests/orchestration/test_retirement_gates.py -v` — expected FAIL on missing module/golden.

- [ ] **Step 2: Implement `retirement.py` + freeze the golden by review.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_retirement_gates.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/adapters/retirement.py tests/orchestration/golden/phase9_retirement_gates_v1.json tests/orchestration/test_retirement_gates.py
git commit -m "feat(orchestration): measurable retirement gates for the three legacy entry points"
```

---

## Task 12: Whole-framework e2e + red-line regression suite

**Files:**
- Test: `tests/orchestration/test_phase9_e2e.py`
- Test: `tests/orchestration/test_redline_regression.py`

**Consumes:** everything above; spec §11 影子镜像/e2e + 红线回归 bullets verbatim.

**Produces:**

- `test_phase9_e2e.py` — offline, fixture-driven, deterministic:
  - `test_luozi_interval_e2e`: 3-point daily schedule over fixture bars → per-point Bootstrap+MainPlan with per-point PIT snapshots (assert each decision plan's ContextSnapshot `as_of` equals its point's `decision_as_of` — "滚动曲线确在每个 schedule 点使用当时 PIT 快照") → intents each referencing a real committed Proposal Artifact and the schedule digest → dual curves under one attestation → evaluator handoff → seats-compat rows round-trip through the existing seats handlers. Two R2 additions: each point's Bootstrap regime/rotation report binds the `factor_report_digest` of **that point's** `market_factor_report` (any cross-point digest reference is rejected), and every `EvidenceAnchor` in those reports resolves through the Phase 5 machine re-verifier (consumed, not reimplemented — the structural Lane 0 properties stay Phase 5-owned, ruling R14).
  - `test_eligible_time_uniqueness`: for each supported `(bar_frequency="1d", execution_policy)` pair, `eligible_execution_at` is uniquely determined via the Phase 6 `compute_eligible_execution_at` and the policy/price-field pairing holds; non-`1d` frequencies are refused (`UnsupportedBarFrequencyError`).
  - `test_recovery`: kill/replay between每一对 UoW boundaries (state put / event append / wakeup) — duplicate recovery applies each target once while distinct order kinds/trigger bars/multi-fills survive intact.
  - `test_weiwo_e2e`: stub live vendor → frozen as_of run → optimizer via `run_graph` binding → draft-only factorlib landing; rejected candidate leaves no factorlib trace.
  - `test_maturity_chain`: immature interval → `WAITING_FOR_MATURITY` → scheduler wakeup → matured batch → evaluator feedback exactly once (经验库/feedback append-only成熟链 at this adapter's boundary).
- `test_redline_regression.py` — structural, catalog/registry-scanning plus behavioral probes (spec §11 红线回归 verbatim, one test per line, plus one R2/AMEND-1 boundary probe):
  1. `test_llm_has_no_real_order_or_signal_capability` — scan `build_phase9_catalog_snapshot` output: no capability whose operation/schema writes orders/signals; live adapter registers none.
  2. `test_shadow_intent_cannot_be_promoted_in_place` — no public API accepts a shadow Artifact and yields a live instruction; `execution_scope` literal `"SHADOW_ONLY"` cannot be constructed otherwise; human approval APIs refuse the promotion shape.
  3. `test_unapproved_dynamic_plan_never_executes` — DYNAMIC candidate without a same-digest `PlanApproved` event: dispatch refuses; zero reservations leak.
  4. `test_no_silent_fallback_without_explicit_preset` — planner failure with `fallback_preset_id=None` ⇒ honest terminal failure, no preset run.
  5. `test_drafts_never_auto_promote` — factorlib status remains `draft`; no code path calls promote; `_VALID_SAVE_STATUS` regression.
  6. `test_workers_never_write_memory_skill_code` — catalog scan: no memory-accept/path-write/skill-write/code-write capability; memory changes only via `memory.propose` → human decision.
  7. `test_degradation_always_badged` — every DEGRADED `DataResult`/state surface in the e2e traces carries `degradation_reason`/badges.
  8. `test_auto_approval_still_rejected_everywhere` — `AUTO` fails validation for every PlanSource including through the new router.
  9. `test_replay_boundary_unavailable_honest` — (R2/AMEND-1 addition beyond the eight verbatim spec §11 lines) decision points before a feed's archive coverage floor (Task 2b) surface UNAVAILABLE + badge through the replay trace and the state/curves endpoints; never zero-filled, never a current snapshot impersonating history. Boundary probe only — the structural Lane 0 UNAVAILABLE tests are Phase 5-owned (ruling R14).

**Required invariants:**

1. Both suites run offline from repo root with `pytest`, no live server, no network, no `G:\stocks` dependency (fakes/fixtures only).
2. e2e asserts cross-suite aggregation: the exit-gate command `pytest tests/orchestration -v` is the single green signal spec §12.9's retirement precondition ("红线/并发/恢复/e2e 全绿") refers to.

- [ ] **Step 1: Write failing suites** (red = missing e2e wiring helpers or unproven red-line scans).

Run now: `pytest tests/orchestration/test_phase9_e2e.py tests/orchestration/test_redline_regression.py -v` — expected FAIL.

- [ ] **Step 2: Wire fixtures/helpers until green.** Behavior changes, if any, land in the owning task's modules with their own red/green loops.

- [ ] **Step 3: Run everything and commit**

Run: `pytest tests/orchestration -v` and `python -m compileall -q guanlan_v2/orchestration`. If Ruff is available: `ruff check guanlan_v2/orchestration tests/orchestration`.

Expected: PASS.

```bash
git add tests/orchestration/test_phase9_e2e.py tests/orchestration/test_redline_regression.py
git commit -m "test(orchestration): whole-framework e2e + red-line regression suite"
```

---

## Phase 9 Exit Gates

Phase 9 is complete only when every gate below is checked by tests and reviewed artifacts.

### Upstream handoff and chain

- [ ] every Phase 1–8 Exit Gate remains green; `test_phase9_handoff.py` passes with reviewed frozen digests;
- [ ] Phase 9 imports, never redefines, upstream contracts (shadow types, schedule, runner, evaluator, bootstrap, planner, catalog);
- [ ] `PHASE9_REGISTRY_DIGEST`/`build_phase9_registry(expected_phase8_digest)` and `PHASE9_CATALOG_DIGEST`/`build_phase9_catalog_snapshot(...)` exist, consume exactly the Phase 8 digests, and their goldens are hand-frozen; inherited schemas byte-identical; no upstream golden touched; no "latest" alias;
- [ ] Phase 9 adds no `EventType` member (pinned test), no new final worker id (the Phase 8 worker table is inherited byte-identical; no integer worker-count assertion), no new data method id and no worker-allowlist edit (method-id set and allowlists pinned byte-identical to Phase 8).

### PIT replay data honesty

- [ ] the strict raw PitReader adapter refuses future rows (`FutureDataRefused`) and missing availability (`MissingAvailabilityRefused`) with no fallback;
- [ ] adding future rows leaves old-date result digests byte-identical;
- [ ] `PIT_REPLAY` DataContexts always carry `strict_pit=True`, non-LIVE backend and a vintage manifest digest; the run clock is the decision point's `decision_as_of`, wall-clock-free;
- [ ] no adapter method accepts caller `as_of`/strict overrides.

### Schedule replay and no-retroactive-intent

- [ ] `resolve_decision_points` is deterministic and calendar-driven; `scheduled_for`/`cutoff_at`/`eligible_execution_at` come only from the Phase 6 `compute_scheduled_for`/`compute_cutoff_at`/`compute_eligible_execution_at` (no inline re-derivation) under the ruled time model `cutoff_at <= decision_as_of < eligible_execution_at`; non-`1d` bar frequencies are refused under `shadow-match-v1` (`UnsupportedBarFrequencyError`); policy/price-field pairing holds;
- [ ] every decision plan binds a ContextSnapshot whose `as_of` equals its point's `decision_as_of` (asserted pre-admission and in e2e);
- [ ] intents are produced strictly in point order; backfill attempts raise `RetroactiveIntentRefused`; duplicate points replay idempotently;
- [ ] one `RunBudget` covers the whole interval; deterministic points reserve zero LLM invocations; live-session points reconcile with the seats 24/day pool exactly once via `note_external_llm_use`; watcher skips orchestrated codes and is otherwise bit-unchanged.

### Dual curves and evaluator handoff

- [ ] both curves execute under one `ShadowExecutionConfig` attestation (universe/capital/snapshot/calendar/cost/clock/priority); mismatch is structurally impossible;
- [ ] apply-once per `(intent_id, scheduled_for, target_version)` while distinct order kinds/trigger bars/multi-fills are preserved; the deterministic lane runs envelope-free through Phase 6 `run_targets` (own `deterministic_apply_key` family, no intent minted, no `origin="LLM"`, `intent_content_digests == ()`);
- [ ] `DualCurveReport` carries `not_causal_attribution=True` structurally and reaches the Phase 4 evaluator only when mature; immature runs park in `WAITING_FOR_MATURITY`;
- [ ] the shadow path contains zero LLM calls (`n_calls == 0`).

### Maturity and wakeup

- [ ] `ShadowReplayRunState` transitions are single-UoW all-or-none with `ExperimentStateChanged` events; journal folds to head;
- [ ] `wakeup_shadow_replay` is idempotent (duplicate delivery returns the stored receipt), processes only matured batches, never re-executes decision points, and issues a fresh `wakeup_key` on re-park;
- [ ] the autonomy scheduler gate is env-gated off by default, fires at most once per day and self-swallows.

### Durable stores, resume and daily Lane 0

- [ ] the durable jsonl/file stores pass the same behavioral matrices as the in-memory Phase 2 implementations plus crash-recovery (kill between append and fold), torn-tail tolerance, mid-file corruption hard-fail, payload write-once + digest-verify-on-get;
- [ ] admitted plans, run events, payloads and parked `WAITING_FOR_MATURITY` heads survive process restart byte-identically (journal fold equality pre/post restart);
- [ ] the lifespan startup scan marks in-flight attempts `interrupted` honestly, never resumes mid-attempt, never displays stale runs as running; production router/playbooks bind the durable stores while tests default to in-memory;
- [ ] `maybe_enqueue_lane0_bootstrap` is env-gated off by default, fires at most once per day and self-swallows; without an active lease the playbook reports an honest skip and admits nothing; with a lease the BootstrapPlan admits through the Phase 7 lease channel (real `PlanApproval`, actor `lease:<id>`) and the day's snapshot/case seeds commit.

### Weiwo ONLINE

- [ ] `as_of` is frozen at run start everywhere (advancing clock changes nothing semantic);
- [ ] `evaluate_validation_via_run_graph` produces口径-identical metrics to direct `run_graph(..., prefer_model_terminal=True)` and touches no executor internals;
- [ ] products land in factorlib only as `status="draft"`; rejected candidates leave no factorlib trace and no invented status; promotion stays the existing human gate;
- [ ] the live catalog binding has no order/signal write capability; hostile fetched text cannot widen capabilities.

### Mirror stage ② golden harness

- [ ] the hand-authored `shadow_execution_golden_v1.json` covers fill/reject/cost/T+1/limit/lot/suspension/corporate-action/take-profit/max-hold/intrabar-priority/apply-once/multi-fill cases and passes end-to-end;
- [ ] `MirrorHarnessReport` binds the fixture digest and `matching_engine_version`; behavior drift without a version bump fails;
- [ ] `engine/financial_analyst/**` is byte-unmodified for the whole phase (reviewer file-level check + the Task 0 signature/byte-digest pins re-checked in-test).

### Router and UI-fill-only

- [ ] the adapters router adds routes without altering existing ones; all endpoints are shadow/draft only; `start` cannot self-approve; wakeup is the only mutating read-side endpoint and is idempotent;
- [ ] orchestrated runs round-trip through the existing seats runs/decisions handlers with legacy record shapes intact (`source:"orchestrated"` badged); no frontend file changed in this phase.

### Retirement gates

- [ ] the three reviewed `EntryPointRetirementGate` instances are digest-frozen with measurable criteria (red-line/concurrency-recovery/e2e suites + parity/operational evidence);
- [ ] `evaluate_retirement_gate` is pure and fail-closed;
- [ ] all three legacy entry points remain alive and untouched (no removal, reroute or degradation in this phase).

### e2e and red lines

- [ ] `test_phase9_e2e.py` proves the spec §11 影子镜像/e2e bullet end-to-end offline (real Proposal Artifact + schedule digest on every intent; unique eligible time; recovery apply-once without swallowing; per-point PIT snapshots; 落子双曲线; 帷幄 draft; maturity chain);
- [ ] `test_redline_regression.py` proves all eight spec §11 red lines plus the R2 replay-boundary honesty probe structurally;
- [ ] `pytest tests/orchestration -v` is fully green and is the single retirement-precondition signal.

### Scope protection

- [ ] no modification to `workflow/executor.run_graph`/`_DISPATCH`/`_OUT_PORT`, no engine change, no new frontend page, no real vendor credential/network in tests;
- [ ] seats/autonomy/rescore/server diffs are the reviewed additive seams only;
- [ ] unrelated worktree changes are not staged (explicit-pathspec commits throughout).

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — Phases 1–8 handoff evidence and the six correction clauses reconciled against implemented upstream APIs;
2. after Tasks 1–2b — contract surface, the durable store backend (Task 1b conformance + crash-recovery evidence) and the strict PIT adapter (FutureDataRefused honesty + archive coverage floors);
3. after Tasks 3–4 — ONLINE freeze and the interval-replay driver (no-retroactive-intent + budget reconcile);
4. after Tasks 5–6 — dual-curve attestation/evaluator handoff, idempotent maturity/wakeup and the daily Lane 0 gate (lease-admitted run + honest no-lease skip);
5. after Task 7 — weiwo ONLINE binding and draft-only landing;
6. after Tasks 8–9 — mirror stage-② golden harness and the frozen Phase 9 chain node;
7. after Tasks 10–12 — router/UI-fill-only compat, retirement gates and the full e2e/red-line suites plus all Exit Gates.

Old entry points are retired only by later reviewed commits that cite a green `RetirementReadinessReport` digest per entry point; this plan ships the gates, not the removal. Restart the 9999 server after merging to serve the new routes (verify on 9998 first). No execution method requires a particular optional skill package.
