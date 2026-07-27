# Orchestration Phase 10 · 接入收口(帷幄选股流水线 + 落子买卖点换脑 + 公共件)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Compose the completed Phase 1–9 kernel into the two user-facing product pipelines ruled in `docs/superpowers/specs/2026-07-18-orchestration-integration-design.md` (rulings I1–I6): **(A) 帷幄选股流水线** — deterministic candidate workers (`cand.v4`/`cand.lane0`/`cand.model`) feeding per-stock deep-research lane runs whose whole-picture cost is approved on the Phase 7 lease card (or per-card without a lease), landing an advisory `RecommendationSlate` into the console surface and the GL research archive; **(B) 落子买卖点 live 换脑** — the seats watcher's injectable `decide_fn` wrapped so the existing fast single-LLM tick keeps running while a pure zero-LLM escalation judge (direction flip / stop-take proximity / event wordlist / pattern hit / per-strategy opt-in) promotes selected judgments to a sealed single-code deep-decide preset admitted through a Phase 7 intraday `ApprovalLease`, with every deep failure degrading honestly back to the fast result; **(C) 公共件** — the `/orchestration/pipeline` router, `ww_orchestrate_*` console tools (five-place sync), the D7 external-TA inbox, and the Phase 10 registry/catalog/preset chain node with goldens.

**Architecture:** Phase 10 is the product-composition phase: it imports — never redefines — Phases 1–9. Three mechanism rulings are fixed here: **(R-A, revised post-Task-0 — ratified D3)** screening runs are N per-code instances of a sealed single-code lane preset (subject via a committed `RunSubject@1` input artifact — ruled post-Task-0 round 2: the gate pinned that the kernel has NO structural code carrier at all, so Phase 10 supplies its own digest-bound one through the existing Phase 1 `InputArtifactBinding` machinery, never free-text goal parsing) admitted through a screening `ApprovalLease` whose issue form carries the whole-picture cost preview (count=N, budget cap), falling back to N pending human cards without a lease; the cross-sectional `dec.pm` summary is deferred with an honest badge (single-code context convention); the chat path stays the Phase 7 planner with per-plan approval. **(R-D, post-Task-0)** Task 0b supplies the production assembly (CatalogRuntime loading + worker tier ModelGateways + ExecutionRuntime/ArtifactPool composition into the Phase 9 launcher's plan-runner path) that Tasks 3/6/7 bind for real execution; the interval-shaped plan graph + per-interval `ReplayPlanCoordinator` port change (Phase 9 rulings A/C2/C3/C4) stays explicitly chartered post-Phase-10, and Task 8 uses the sanctioned injectable replay seam. **(R-B)** the deep-decide preset is a sealed single-code preset in the Phase 7 pilot idiom — `code`/`asof` flow through `OrchestrationRequest` context, never plan params — so one `PlanPresetRecord` digest serves every stock and the Phase 7 `ApprovalLease` binds it. **(R-C)** the escalation judge is a pure function over an explicitly-assembled context; all I/O lives in injected ports in the context builder, and an absent port renders its trigger inert with a badge, never a guess. New code lives in `guanlan_v2/orchestration/pipeline/` plus additive seams in `guanlan_v2/seats/watcher.py`, `guanlan_v2/console/tools.py` and `guanlan_v2/server.py`.

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio` (`to_thread` for journal/kernel I/O reached from the 9999 loop; sync HTTP self-calls from coroutines remain forbidden), `pytest` + `pytest-asyncio`, FastAPI `TestClient`. All modules `from __future__ import annotations`. Run tests from repo root `G:\guanlan-v2` with `pytest`.

## Global Constraints

These extend, and never override, the Phase 1–9 Global Constraints and Exit Gates plus the integration spec §8 red lines. Every task implicitly includes those documents.

- **Consume, do not fork.** Import contracts/builders/runtimes from their owning phases. Phase 10 must not redefine canonical JSON, digests, plan validation/freeze/admission, `ApprovalLease` semantics (Phase 7 Task 7b), durable stores (Phase 9 Task 1b), shadow types, or the memory facade.
- **Spec §8 red lines verbatim:** LLM 零买卖(`kind=trade` 永远人手);推荐/买卖点全 advisory;v4 信号不动(overlay only);DYNAMIC Plan 逐个人审;AUTO 全拒;sealed holdout 一次性;PIT 整批拒绝;UI 只填充不重建;协程内禁同步自 HTTP;绝不 `git add -A`。
- **TypedPayloadRef discipline.** Schema-bearing references in Phase 10 contracts are `TypedPayloadRef(schema_ref, payload_ref)` with validator-pinned schemas; bare `PayloadRef` only on locator-only surfaces.
- **Chain discipline.** `PHASE10_REGISTRY_DIGEST` + `build_phase10_registry(expected_phase9_digest)` and `PHASE10_CATALOG_DIGEST` + `build_phase10_catalog_snapshot(...)`; own goldens; inherited entries byte-identical; no upstream golden regenerated; no "latest" alias. Phase 10 adds **no** new `EventType` member and **no** new Phase 3 data method id.
- **Escalation thresholds are frozen constants, not knobs:** `ESCALATION_THRESHOLDS_VERSION = "escalation-v1"`, `STOP_TAKE_PROXIMITY_PCT = 0.02`, `EVENT_WORDLIST_V1` tiers `severe=("立案","留置","强制退市")` / `high=("问询函","减持计划","质押平仓")` / `watch=("关注函",)`; escalation fires on `severe|high` only. Changing any value is a reviewed contract change (golden-pinned).
- **Count-honesty for console tools.** WW tool/allowlist/MCP/doc counts drift with concurrent sessions; no task hardcodes today's numbers — each count guard is bumped from the value the test asserts at implementation time (+N for the tools this plan adds).
- **Deep chain never blocks the fast chain.** Any deep-path failure, lease miss or budget exhaustion returns the fast result with honest `deep_outcome` markers; the watcher's behavior with `GUANLAN_SEATS_DEEP` unset is bit-unchanged.
- **Budget honesty.** Deep-chain LLM invocations reconcile with the seats 24/day pool exactly once via the Phase 9 `note_external_llm_use` seam; kernel spend runs on the one Phase 2 `BudgetLedger`; the intraday lease's `budget_cap` bounds total leased spend.
- **Executable red/green checkpoints.** Every "Write failing … tests" step immediately runs the focused command shown and records the expected missing-contract/behavior failure before implementation; collection/environment errors do not count as red. The PASS step reruns the same focused tests plus listed regressions.
- **Explicit pathspec commits.** Shared branch with concurrent sessions: every commit block lists exactly the task's files; `git add -A`, `git add .` and bare `git commit -a` are forbidden.
- No placeholders, DRY, YAGNI, TDD, frequent commits.

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 10 depends on **all** of Phases 1–9 (including the 2026-07-18 amendments: Phase 7 Task 7b, Phase 9 Tasks 1b/6). Work starts only after every upstream Exit Gate passes with test evidence. Add `tests/orchestration/test_phase10_handoff.py` as an executable consumer test.

**Files:**
- Create: `tests/orchestration/test_phase10_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. the full `tests/orchestration` upstream suite is green; the linear registry chain resolves by exact digest through `PHASE9_REGISTRY_DIGEST` and the catalog chain through `PHASE9_CATALOG_DIGEST`; no "latest" alias anywhere in `guanlan_v2/orchestration/`;
2. Phase 7 exports resolve: the planner admission path, `PlanPresetRegistry`/`PlanPresetRecord`/`materialize_fallback_draft`, the approval coordinator, **and the Task 7b lease surface** — `ApprovalLease`, `issue_lease`, `list_leases`, `revoke_lease`, `register_and_try_lease`, `LeaseAdmissionOutcome`, plus `PendingPlanApproval.preset_id`/`preset_record_digest`;
3. Phase 9 exports resolve: `build_durable_runtime_stores` (Task 1b), the `/orchestration` router builder, `persist_replay_run_compat`, `run_interval_replay`, `note_external_llm_use`, `orchestrated_codes`, `maybe_enqueue_lane0_bootstrap`;
4. the Phase 8 catalog resolves the lane workers this plan schedules — `text.news`, `text.research_report`, `quant.factor`, `quant.backtest`, `pv.price_action`, `pv.technical`, `pv.microstructure`, `dec.bull`, `dec.bear`, `dec.research_mgr`, `dec.pm`, `dec.trader` — each `selection_scope="dynamic_allowed"` (or the implemented equivalent) with importable `params_schema_ref`s; `dec.trader` emits only `PortfolioTargetProposal@1`;
5. Phase 5/6 anchors resolve: `RegimeReport@1`/`RotationReport@1` registered schemas, the bootstrap `ContextSnapshot` production path, `TARGET_WEIGHT_BANDS`, and the Phase 6 compute_* schedule functions;
6. production seams hold by name: `guanlan_v2/seats/watcher.py::tick` accepts an injectable `decide_fn` and `run_loop` is the lifespan entry; `guanlan_v2/console/tools.py` exports `WW_TOOL_TABLE`, `register_console_tools`, `_wrap`, `_self_get`/`_self_post`; `scripts/gen_agent_interface_doc.py` exists with its drift-guard test; the seats decision-persistence helper and run-head shapes match the Phase 9 Task 10 grounding;
7. a screen ranking read surface exists for `cand.v4`/`cand.model` (the reviewed helper that returns the current v4 / variant ranking artifact with its as-of date) — recorded by exact module:function name in the fixture;
8. `POST /archive/put` has an in-process callable seam (the console `_archive_research` idiom) for report landing;
9. no Phase 10 source/test path overwrites any Phase 1–9 source, test or golden file.

**Correction clauses (binding on every later task):** if an implemented upstream name/signature differs, update this plan to the reviewed API before writing code; never invent an adapter with parallel semantics.

- **D1 (lease API):** the exact Phase 7 Task 7b names/fields bind Tasks 6/7/9.
- **D2 (durable stores):** `build_durable_runtime_stores(root)` and the store ABIs bind Tasks 7/9; tests default to in-memory stores.
- **D3 (P8 worker params):** each lane worker's `params_schema_ref` governs what a lane node may carry; the screening builder populates exactly the reviewed param fields (`code`, `asof` etc.) — if a worker takes code from request context instead of params, the builder emits context-consistent single-code plans per lane worker's reviewed convention.
- **D4 (rotation leaders):** `cand.lane0` binds to the implemented `RotationReport` mainline fields; if leader codes are absent from the report, it derives them from the `MarketFactorReport` ladder factor by the reviewed accessor; if neither carries codes, `cand.lane0` refuses honestly (typed error) — never invents a universe.
- **D5 (ranking read surface):** the exact screen/model-registry helper for item 7 binds Task 2's production reader.
- **D6 (lease-able screening preset, deferred):** if a later reviewed Phase 7/8 ABI extension supports runtime-varying codes under one sealed preset digest (slate-as-data lanes), the screening daily lease lands as a follow-up; v1 ships per-run approval only — this plan must not fake it.
- **D7 (seats persistence names):** the in-process decision-append helper and only-if-present key convention bind Task 7's compat writer.
- **D8 (router mount):** the Phase 9 `/orchestration` mount pattern in `guanlan_v2/server.py` is the idiom for Task 9's additive `/orchestration/pipeline` mount.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture** — exact digests + module:function names only; never local paths or mutable singleton identities.

- [ ] **Step 3: Run** `pytest tests/orchestration -v` — expected: every upstream test plus `test_phase10_handoff.py` PASS. Any failure blocks Task 1.

- [ ] **Step 4: Commit**

```bash
git add tests/orchestration/test_phase10_handoff.py
git commit -m "test(orchestration): gate phase10 on phase1-9 contracts and production seams"
```

---

## File Structure (created/modified in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/pipeline/__init__.py` | package export surface |
| `guanlan_v2/orchestration/pipeline/contracts.py` | Phase 10 public contracts: `CandidateSlate@1`, `RecommendationSlate@1`, `EscalationReport@1`, `TaSubmission@1` + internal carriers |
| `guanlan_v2/orchestration/pipeline/candidates.py` | deterministic candidate workers `cand.v4`/`cand.lane0`/`cand.model` + `RankingReader` port + production reader |
| `guanlan_v2/orchestration/pipeline/assembly.py` | Task 0b production assembly: CatalogRuntime loader + worker tier ModelGateways + ExecutionRuntime/ArtifactPool composition into the launcher plan-runner path + `build_phase10_preset_registry` mechanism |
| `guanlan_v2/orchestration/pipeline/screening.py` | sealed per-code screening lane preset + lease-governed batch builder + cost preview + `build_recommendation_slate` assembler + archive landing |
| `config/orchestration/presets/screening_lane_v1.json` | the sealed screening lane preset source |
| `guanlan_v2/orchestration/pipeline/escalation.py` | frozen thresholds/wordlist, `EscalationContext` builder ports, pure `judge_escalation` |
| `guanlan_v2/orchestration/pipeline/deep_decide.py` | sealed single-code deep-decide preset (context-bound) + Phase 10 preset registry extension |
| `guanlan_v2/orchestration/pipeline/live_decide.py` | `make_orchestrated_decide` wrapper: fast passthrough + lease-admitted deep run + honest degradation + seats compat record |
| `guanlan_v2/orchestration/pipeline/api.py` | `build_pipeline_router` (`/orchestration/pipeline/*`): start/state/runs/screening-latest/ta_ingest |
| `ui/console/console-recommendation-card.jsx` | 推荐榜 card on the existing console page (poll `/screening/latest`, advisory banner, honest empty state) |
| `guanlan_v2/orchestration/pipeline/chain.py` | `PHASE10_PUBLIC_MODELS`/`PHASE10_INTERNAL_MODELS`, chain builders + digests |
| `config/orchestration/presets/luozi_deep_decide_v1.json` | the sealed deep-decide preset source |
| `guanlan_v2/seats/watcher.py` (modify, additive) | `run_loop`/`tick` accept the injected deep `decide_fn` binding (default unchanged) |
| `guanlan_v2/server.py` (modify, additive) | mount pipeline router; `GUANLAN_SEATS_DEEP` gated wrapper wiring |
| `guanlan_v2/console/tools.py` (modify, additive) | `ww_orchestrate_start`/`ww_orchestrate_status`/`ww_orchestrate_runs`/`ww_ta_ingest` rows + impls |
| `guanlan_v2/console/api.py` (modify, `_SYSTEM_PROMPT` only) | tool mentions (five-place sync) |
| `docs/agent_data_interfaces.md` (regenerated) | via `scripts/gen_agent_interface_doc.py` |
| `tests/orchestration/golden/phase10_schema_manifest_v1.json` | registry golden |
| `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | catalog golden (cand.* workers + preset records) |
| `tests/orchestration/test_phase10_handoff.py` | executable Phases 1–9 → 10 gate |
| `tests/orchestration/test_pipeline_assembly.py` | Task 0b |
| `tests/orchestration/test_pipeline_contracts.py` | Task 1 |
| `tests/orchestration/test_pipeline_candidates.py` | Task 2 |
| `tests/orchestration/test_pipeline_screening.py` | Tasks 3–4 |
| `tests/orchestration/test_pipeline_escalation.py` | Task 5 |
| `tests/orchestration/test_pipeline_deep_preset.py` | Task 6 |
| `tests/orchestration/test_pipeline_live_decide.py` | Task 7 |
| `tests/orchestration/test_pipeline_replay_evidence.py` | Task 8 |
| `tests/orchestration/test_pipeline_api.py` | Task 9 |
| `tests/test_console_tools.py` (modify) | Task 10 count/reachability bumps |
| `tests/orchestration/test_phase10_chain.py` | Task 11 |
| `tests/orchestration/test_phase10_e2e.py` | Task 12 |

---

## Task 0b: Production assembly (CatalogRuntime loading + worker gateways + plan-runner composition)

> Chartered post-Task-0 (2026-07-27) from the Phase 9 permanent record: the kernel classes exist but nothing assembles them for lane workers in production — "no production CatalogRuntime loader / ExecutionRuntime / ArtifactPool / worker ModelGateway assembler" (Task 0 evidence, launcher docstring pins). Without this, every Phase 10 "production binding" would be a fixture stand-in — the exact overclaim the Phase 9 reviews caught. Scope is the assembler ONLY; the interval-shaped plan graph + per-interval `ReplayPlanCoordinator` port change (Phase 9 rulings A/C2/C3/C4) remains explicitly chartered post-Phase-10.

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/assembly.py` (plus the minimal `pipeline/__init__.py` if this task lands first — whichever of Task 0b/Task 1 runs first creates it)
- Test: `tests/orchestration/test_pipeline_assembly.py`

**Interfaces:**
- Consumes: the implemented kernel classes (`catalog_runtime.CatalogRuntime` + `TrustedFactoryRegistry`, the `worker.py` execution runtime + `ModelGateway` port, `pool.py` artifact pool), the Phase 9 launcher `build_admitted_plan_runner` (adapters/launcher.py) and `build_durable_runtime_stores`; the Phase 7 `planner_gateway.PlannerLLMModelGateway` idiom (engine `LLMClient.for_agent`, explicit repo `config/llm.yaml` path, single-shot, thread-isolated); the Phase 8 llm.yaml tier seats (`orchestration-fast`/`orchestration-reasoner`/`orchestration-reasoner-deep`) and ModelTier vocabulary; Phase 7 `PlanPresetRegistry`/`PlanPresetRecord`. **Correction clause E1 (binding):** the exact composition surface `build_admitted_plan_runner` requires, the deterministic-handler registration ABI, and the reviewed `ModelGateway` port signature are upstream-owned — bind to the implemented names at source before writing code; never a parallel runtime.
- Produces:
  - `def build_production_catalog_runtime(catalog_snapshot, *, registry, handler_registry) -> CatalogRuntime` — resolves catalog materials from their `config/orchestration/` physical sources per the manifests and registers deterministic handlers via the reviewed `TrustedFactoryRegistry` mechanism (Task 11 registers the `cand.*` handlers here);
  - `class WorkerSeatModelGateway` — the worker-side `ModelGateway` implementation: maps a worker's model tier to the Phase 8 llm.yaml seat via `LLMClient.for_agent(<seat>, config_path=<repo>/config/llm.yaml)` (explicit path — never `find_config`); an unconfigured tier raises loudly (never a silent vendor fallback); thread-isolated, single-shot per invocation, per the planner-gateway idiom;
  - `def build_production_plan_runner(*, stores, catalog_snapshot, registry, gateway_factory) -> <the launcher's plan-runner callable>` — composes CatalogRuntime + ExecutionRuntime + ArtifactPool + gateways into exactly what `build_admitted_plan_runner` consumes; `gateway_factory` is the ONLY seam that differs between tests (scripted gateways) and production (`WorkerSeatModelGateway`) — assembly code itself is identical in both;
  - `def build_phase10_preset_registry(phase7_registry, records: tuple[PlanPresetRecord, ...]) -> PlanPresetRegistry` — the sealed extension mechanism (refuses non-Phase-7 bases; Phase 7 preset golden untouched); Task 3 adds the screening lane record, Task 6 the deep-decide record.

**Required invariants:**

1. the end-to-end proof runs the EXISTING pilot preset (`config/orchestration/presets/main_research_baseline.json`) through admission (fixture-approved) and `build_admitted_plan_runner` composed by `build_production_plan_runner` with scripted gateways — the assembly path is the real one, zero network, zero fixture stand-ins for assembly itself;
2. tier resolution: each of the three Phase 8 seats resolves from the repo llm.yaml by explicit path; a missing/unknown tier raises a typed error naming the seat (loud, never silent);
3. no approval/admission bypass exists anywhere in assembly (the runner only accepts admitted plans — inherited from the launcher, regression-pinned);
4. no engine file is modified; no new EventType/method id; imports stay inside orchestration + engine LLM client.

- [ ] **Step 1: Write failing assembly tests** — E1 reconciliation first (record the implemented composition surface in the test docstring with file:line); matrix: pilot-preset e2e through the composed runner (scripted gateways, RunResult completed, artifacts committed to tmp durable stores); tier happy ×3 + unconfigured-tier typed raise; gateway-factory seam (same assembly, different factory → different gateway observed); non-admitted plan refused; preset-registry extension (wrong base refused; Phase 7 golden byte-identical).

Run now: `python -m pytest tests/orchestration/test_pipeline_assembly.py -v` — expected FAIL on missing module.

- [ ] **Step 2: Implement `assembly.py`.**

- [ ] **Step 3: Run and commit**

Run: `python -m pytest tests/orchestration/test_pipeline_assembly.py tests/orchestration/test_phase10_handoff.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/pipeline/assembly.py tests/orchestration/test_pipeline_assembly.py
git commit -m "feat(orchestration): production assembly - catalog runtime loading, worker tier gateways, plan-runner composition"
```

---

## Task 1: Phase 10 public contracts

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/__init__.py`, `guanlan_v2/orchestration/pipeline/contracts.py`
- Test: `tests/orchestration/test_pipeline_contracts.py`

**Interfaces — Produces (later tasks rely on these exact names):**

- `class CandidateEntry(ContractModel)` (internal): `code: NonEmptyStr` (validated via Phase 3 `normalize_symbol`), `rank: PositiveInt`, `score: FiniteFloat | None`, `source_note: NonEmptyStr | None`.
- `class CandidateSlate(DigestModel)` → **`CandidateSlate@1`**: `schema_version: Literal["1"]`, `source_kind: Literal["v4","lane0","model_variant"]`, `as_of: UtcDateTime`, `top_n: PositiveInt`, `entries: tuple[CandidateEntry, ...]` (≤ `top_n`, ranks strictly increasing, codes unique), `source_artifact_digest: DigestHex | None`, `rotation_report_ref: TypedPayloadRef | None` (schema pinned `RotationReport@1`), `variant_id: NonEmptyStr | None`, `badges: tuple[NonEmptyStr, ...]`. Validator: `source_kind=="lane0"` ⇔ `rotation_report_ref is not None`; `source_kind=="model_variant"` ⇔ `variant_id is not None`.
- `class RecommendationEntry(ContractModel)` (internal): `code: NonEmptyStr`, `lane_index: NonNegativeInt`, `rating: NonEmptyStr` (copied from the lane's `ResearchPlan`), `research_plan_ref: TypedPayloadRef` (pinned `ResearchPlan@1`).
- `class RecommendationSlate(DigestModel)` → **`RecommendationSlate@1`**: `schema_version`, `as_of: UtcDateTime`, `batch_id: NonEmptyStr`, `candidate_slate_ref: TypedPayloadRef` (pinned `CandidateSlate@1`), `entries: tuple[RecommendationEntry, ...]`, `portfolio_decision_ref: TypedPayloadRef | None` (pinned `PortfolioDecision@1`; **v1 is always `None` with mandatory badge `no_cross_sectional_summary_v1`** — the `dec.pm` 全场裁决 is deferred per ratified D3's single-code context convention; the field stays so a future summary lands without a schema bump), `degraded_lanes: tuple[NonNegativeInt, ...]`, `badges: tuple[NonEmptyStr, ...]`.
- `class EscalationTrigger(ContractModel)` (internal): `kind: Literal["direction_flip","stop_take_proximity","event_wordlist","pattern_hit","opt_in"]`, `detail: NonEmptyStr`.
- `class EscalationReport(DigestModel)` → **`EscalationReport@1`**: `schema_version`, `code: NonEmptyStr`, `as_of: UtcDateTime`, `thresholds_version: Literal["escalation-v1"]`, `triggers_hit: tuple[EscalationTrigger, ...]`, `escalate: bool` (validator: `escalate` ⇔ `triggers_hit` non-empty), `inert_ports: tuple[NonEmptyStr, ...]` (badges for absent context ports).
- `class RunSubject(DigestModel)` → **`RunSubject@1`** (Amendment 2 — the pipeline's subject carrier): `schema_version`, `code: NonEmptyStr` (validated via Phase 3 `normalize_symbol`), `as_of: UtcDateTime`. Committed at request creation and bound to plans via the Phase 1 `InputArtifactBinding` machinery; the ONLY sanctioned way a Phase 10 plan names its stock (the handoff gate pins that no upstream structural carrier exists).
- `class TaSubmission(DigestModel)` → **`TaSubmission@1`**: `schema_version`, `author: NonEmptyStr` (mandatory — D7 source attribution), `title: NonEmptyStr | None`, `submitted_at: UtcDateTime`, `text_digest: DigestHex`, `status: Literal["received"]`.

All four registered models: strict/frozen/extra-forbid `DigestModel`s with semantic/audit projections per Phase 1 conventions (wall-clock/audit-only fields in the reviewed exclude sets).

- [ ] **Step 1: Write failing contract tests** — construction happy paths; validator matrix (lane0⇔rotation ref, model⇔variant, escalate⇔triggers, duplicate codes rejected, rank monotonicity, mandatory author); digest determinism (two identical constructions → equal `content_digest`); projection exclusions; registration round-trip via a fresh registry.

Run: `pytest tests/orchestration/test_pipeline_contracts.py -v` — expected FAIL on missing module.

- [ ] **Step 2: Implement `contracts.py`** (+ package `__init__.py` exporting the four publics).

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_pipeline_contracts.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/pipeline/__init__.py guanlan_v2/orchestration/pipeline/contracts.py tests/orchestration/test_pipeline_contracts.py
git commit -m "feat(orchestration): phase10 pipeline contracts (slate/recommendation/escalation/ta)"
```

---

## Task 2: Deterministic candidate workers (`cand.v4` / `cand.lane0` / `cand.model`)

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/candidates.py`
- Test: `tests/orchestration/test_pipeline_candidates.py`

**Interfaces:**
- Consumes: Task 1 `CandidateSlate`/`CandidateEntry`; Phase 3 `normalize_symbol`; Phase 5 `RotationReport` (implemented fields per D4); the ranking read surface per D5.
- Produces:
  - `class RankingReader(Protocol): def read_ranking(self, *, variant_id: str | None, as_of_hint: UtcDateTime) -> RankingArtifact: ...` with `class RankingArtifact(ContractModel)` (internal): `as_of: UtcDateTime`, `artifact_digest: DigestHex`, `rows: tuple[RankingRow, ...]` (`RankingRow`: `code`, `rank`, `score`).
  - `def build_production_ranking_reader() -> RankingReader` — lazy-imports the ratified D5 surface: `guanlan_v2.strategy.ranking:load_v4_ranking` + `:ranking_date` (one surface; `model_id=None/"prod"` → v4, a variant id → `guanlan_v2.screen.model_registry:variant_ranking_path` artifact); missing artifact ⇒ honest `FileNotFoundError` passthrough; no module-level screen/strategy import.
  - Handler callables (registered as DETERMINISTIC catalog handlers in Task 11): `def cand_v4_handler(params, inputs, ports) -> CandidateSlate`, `def cand_lane0_handler(...) -> CandidateSlate`, `def cand_model_handler(...) -> CandidateSlate` — exact handler ABI bound to the reviewed Phase 2 deterministic-handler signature (Task 0 discipline).
  - Params: `cand.v4` `{top_n: 1..50}`; `cand.lane0` `{top_n: 1..50, mainline_limit: 1..5}`; `cand.model` `{top_n: 1..50, variant_id: str}`.

**Required invariants:**

1. zero LLM, zero network: handlers consume only injected ports + typed inputs; import sweep proves no `httpx`/`requests`/LLM-client import;
2. honest staleness: if the ranking artifact's `as_of` date ≠ the run context's as-of date, the slate carries badge `"stale_ranking:<artifact-date>"` — never silently re-dated, never refused (rankings are evening artifacts consumed next session);
3. `cand.lane0` v1 IS the honest typed refusal (`Lane0LeadersUnavailable`) — Task 0 ratified D4: neither `RotationReport.MainlineRead` nor the ladder factor carries leader codes, and the handoff gate pins that absence; the worker ships its full param/provenance surface so it flips to real extraction the day upstream carries codes (the gate reddens then, re-opening D4) — never an invented universe;
4. every slate carries full provenance (`source_artifact_digest` / `rotation_report_ref` / `variant_id`) and validates against Task 1's validators;
5. `cand.v4`/`cand.model` never write anything — the v4 ranking surface is read-only (spec §8: v4 信号不动).

- [ ] **Step 1: Write failing tests** — fixture `RankingReader` (deterministic rows); matrix: top_n truncation; rank monotonic + unique codes; stale badge; lane0 typed refusal (always, v1 — carries rotation-report provenance in the error) + an xfail-marked happy-path spec documenting the future code-bearing upstream; model variant provenance; import sweep; handler determinism (same inputs → byte-equal slate digest).

Run: `pytest tests/orchestration/test_pipeline_candidates.py -v` — expected FAIL.

- [ ] **Step 2: Implement `candidates.py`.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_pipeline_candidates.py tests/orchestration/test_pipeline_contracts.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/pipeline/candidates.py tests/orchestration/test_pipeline_candidates.py
git commit -m "feat(orchestration): deterministic candidate workers cand.v4/cand.lane0/cand.model"
```

---

## Task 3: Screening lane preset (sealed, per-code) + lease-governed batch builder + cost preview

> Revised post-Task-0 per ratified D3, sharpened by round 2: no Phase 8 lane worker carries `params_schema_ref`, **and the kernel has no structural subject-code carrier anywhere** (gate-pinned: `DataContext` is codeless, paramsless workers refuse node params, the Phase 2 compat "code → context" mapping is aspirational prose). An N-lane single plan with per-lane codes is structurally impossible; the reviewed form is N per-code instances of ONE sealed code-agnostic preset (the Task 6 deep-decide idiom) whose subject enters as a committed **`RunSubject@1` input artifact** (controller ruling, Amendment 2), with the whole-picture cost approval carried by a screening `ApprovalLease` (or N pending human cards without one).

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/screening.py`, `config/orchestration/presets/screening_lane_v1.json`
- Test: `tests/orchestration/test_pipeline_screening.py`

**Interfaces:**
- Consumes: Task 1 `CandidateSlate`; Task 0b `build_phase10_preset_registry` + the launcher plan-runner ABI names; Phase 7 `PlanPresetRecord`/materializer + the coordinator lease methods (`register_and_try_lease` etc. — D1: methods, not free functions; `ApprovalLease` is deliberately UNREGISTERED upstream, assume no registry entry); Phase 1 `validate_plan_draft`/`compute_candidate_plan_digest`; Phase 8 runtime profile v2 vocabulary (debates allowed).
- Produces:
  - Sealed preset `pipeline.screening_lane` v1 (`SCREENING_LANE_PRESET_ID`), carried by the Amendment 3 `PlanPresetRecord@2` + Phase 10 loader (**mirror Task 6's reconciled idioms exactly — Task 6 is the pathfinder**): a code-agnostic single-code graph — evidence `text.news + text.research_report + quant.factor + quant.backtest + pv.price_action` (auxiliary per the implemented P8 semantics) `→ dec.bull ↔ dec.bear` (one round) `→ dec.research_mgr` (terminal, `ResearchPlan@1`); the exact worker set is frozen at implementation against validation-green evidence (research_mgr's required inputs bind — e.g. `text.sentiment` joins if its `sentiment` input demands it, as Task 6 found for `dec.pm`). **The subject is run-scoped per Amendment 3**: the committed `RunSubject@1` artifact is the digest-bound authority and the Task 0b assembly threads it (rendered trusted subject block + instrument-param prefetch, clause E2b); the preset graph carries no code anywhere. Registered via Task 0b's `build_phase10_preset_registry`; record digest golden-frozen.
  - `def build_screening_batch(slate: CandidateSlate, *, preset_registry, clock, base_request) -> ScreeningBatch` — one `OrchestrationRequest` + materialized preset draft per slate entry, all sharing a `batch_id` (digest over slate ref + request semantics); `class ScreeningBatch(ContractModel)` (internal): `batch_id`, `candidate_slate_ref`, per-code `(code, request_id, candidate_plan_digest)` tuples, `cost_preview`.
  - `def screening_cost_preview(batch: ScreeningBatch) -> ScreeningCostPreview` — `class ScreeningCostPreview(ContractModel)` (internal): `n_codes: PositiveInt`, `per_lane_llm_nodes: NonNegativeInt`, `total_llm_nodes: NonNegativeInt`, requested budget in the reviewed RunBudget units; deterministic pure function. **Its JSON is the whole picture the human approves**: on the screening `ApprovalLease` issue form (preset digest + `max_admissions=N` + budget cap mirror the preview), or across N pending cards when no lease is active.
  - `def admit_screening_batch(batch, *, coordinator, admission, now) -> tuple[LeaseAdmissionOutcome, ...]` — per candidate: Phase 1 validate → Phase 2 reserve → coordinator `register_and_try_lease`; an active screening lease admits within its envelope, everything else stays a pending human card (`pending_human`). No third path, no self-approval, DYNAMIC never involved (the chat path stays the Phase 7 planner with per-plan approval).

**Required invariants:**

1. the preset graph is byte-frozen and structurally code-free (no params field can carry a code — schema-level impossibility); every materialized draft passes the unmodified Phase 1 `validate_plan_draft` + profile-v2 runtime support;
2. batch determinism: same slate + same base-request semantics → identical `batch_id` and identical per-code candidate digests;
3. whole-picture integrity: the cost preview's totals equal the sum over the batch's drafts (arithmetic pinned); lease-admitted and pending-human candidates are disjoint and together cover the batch exactly;
4. slate honesty: an empty slate refuses batch construction (typed error) — no zero-candidate batch;
5. per-code independence: one candidate's refusal/failure never blocks its siblings' admission or execution;
6. `admit_screening_batch` stops at admission — no execution, no approval minting outside the lease channel.

- [ ] **Step 1: Write failing tests** — preset load/normalize/digest pin + structural no-code assertion; per-code materialization happy (code via request context); 3-entry fixture slate → batch of 3 with deterministic digests + `batch_id`; cost-preview arithmetic (totals = sum of drafts); lease path (active lease admits all 3 within envelope; envelope exhaustion mid-batch → remainder `pending_human`, never an error); no-lease path (all pending); empty-slate refusal; Phase 1 validation green on every draft; Phase 7 preset golden byte-identical (registry extension regression).

Run: `pytest tests/orchestration/test_pipeline_screening.py -v` — expected FAIL.

- [ ] **Step 2: Implement the preset + batch builder + preview + admission helper.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_pipeline_screening.py -v` — expected PASS.

```bash
git add guanlan_v2/orchestration/pipeline/screening.py config/orchestration/presets/screening_lane_v1.json tests/orchestration/test_pipeline_screening.py
git commit -m "feat(orchestration): sealed per-code screening lane preset + lease-governed batch builder"
```

---

## Task 4: Recommendation assembly + landings

**Files:**
- Modify: `guanlan_v2/orchestration/pipeline/screening.py`
- Test: `tests/orchestration/test_pipeline_screening.py` (extend)

**Interfaces:**
- Consumes: Phase 2 `RunResult`/committed artifacts + stores ABI; Task 1 `RecommendationSlate`; the archive in-process seam (Task 0 item 8).
- Produces:
  - `def build_recommendation_slate(batch: ScreeningBatch, *, stores) -> RecommendationSlate` — folds each per-code run's committed `ResearchPlan` artifact into a `RecommendationEntry` (rating copied verbatim — never re-derived; `lane_index` = the candidate's slate rank order; entries sorted by rating grade then rank), records codes with no committed plan in `degraded_lanes`, sets `portfolio_decision_ref=None` + badge `no_cross_sectional_summary_v1` (v1 invariant);
  - `def render_recommendation_md(slate: RecommendationSlate, *, stores) -> str` — deterministic markdown (per-stock rating + rationale excerpts with `[UNSOURCED]` discipline inherited from payload text; advisory banner line `> 本推荐为编排研究产物,仅供参考,不构成交易指令` mandatory first line);
  - `def land_recommendation(slate, *, stores, archive_put: Callable[[str, str, str], None]) -> None` — writes the md through the injected archive seam as artifact id `rs_orch_screen_<YYYYMMDD>` type `research`; landing failure raises (caller surfaces it) — never a silent skip.

**Required invariants:**

1. assembly is read-only over the run's committed artifacts — it never mutates kernel state;
2. ratings/summaries are copied, never recomputed; a lane with no committed `ResearchPlan` appears only in `degraded_lanes`;
3. the md always opens with the advisory banner; every number in the md traces to a payload field (no assembler-invented numbers);
4. `land_recommendation` is idempotent per `(batch_id)` — a second call with the same slate overwrites the same archive id (archive semantics), never mints a second id.

- [ ] **Step 1: Write failing tests** — fake stores with 2 committed per-code runs + 1 degraded; slate assembly matrix (sort order, rank mapping); banner-first assertion; v1 `None`+`no_cross_sectional_summary_v1` badge invariant; archive landing spy (id format, idempotent re-land per batch_id, failure propagation).

Run: `pytest tests/orchestration/test_pipeline_screening.py -v` — expected FAIL on new names.

- [ ] **Step 2: Implement.**

- [ ] **Step 3: Run and commit**

```bash
git add guanlan_v2/orchestration/pipeline/screening.py tests/orchestration/test_pipeline_screening.py
git commit -m "feat(orchestration): recommendation slate assembly + advisory archive landing"
```

---

## Task 5: Escalation judge (pure) + context builder

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/escalation.py`
- Test: `tests/orchestration/test_pipeline_escalation.py`

**Interfaces:**
- Produces:
  - Frozen constants: `ESCALATION_THRESHOLDS_VERSION = "escalation-v1"`, `STOP_TAKE_PROXIMITY_PCT = 0.02`, `EVENT_WORDLIST_V1: Mapping[str, tuple[str, ...]]` (tiers per Global Constraints, values verbatim).
  - `class EscalationContext(ContractModel)` (internal): `code`, `as_of: UtcDateTime`, `fast_direction: NonEmptyStr | None`, `prev_direction: NonEmptyStr | None`, `last_price: FiniteFloat | None`, `stop_price: FiniteFloat | None`, `take_price: FiniteFloat | None`, `news_titles: tuple[NonEmptyStr, ...] | None` (`None` = port absent), `pattern_hits: tuple[NonEmptyStr, ...] | None` (`None` = pattern feed absent — AMEND-6a not yet landed), `opt_in_deep: bool`.
  - `def build_escalation_context(*, code, as_of, fast_result: Mapping, decisions_tail: Sequence[Mapping], quote: Mapping | None, strat: Mapping | None, news_titles_fn: Callable[[str], Sequence[str]] | None) -> EscalationContext` — pure assembly over injected data; `prev_direction` = the most recent prior decision row for `code`; stop/take from the strat clock config when present (best-effort, uncontracted JSON — absent fields stay `None`); `opt_in_deep = bool(strat.get("deep_research"))` when the strat mapping is present (the spec §5 per-strategy opt-in flag), else `False`.
  - `def judge_escalation(ctx: EscalationContext) -> EscalationReport` — pure, zero I/O: `direction_flip` when both directions present and differ; `stop_take_proximity` when `last_price` within `STOP_TAKE_PROXIMITY_PCT` of either band price; `event_wordlist` when any title contains a `severe|high` term (`watch` never escalates); `pattern_hit` when `pattern_hits` non-empty; `opt_in` when `opt_in_deep`; each `None` context group appends its port name to `inert_ports` and cannot fire.

**Required invariants:**

1. `judge_escalation` performs zero I/O and is deterministic — property test: same context → byte-equal report digest;
2. absent ports are inert-with-badge, never guessed (`news_titles=None` ⇒ no `event_wordlist` trigger + `"news"` in `inert_ports`);
3. `watch`-tier words never escalate; tier membership is exactly `EVENT_WORDLIST_V1` (golden-pinned constant digest);
4. thresholds/wordlist changes fail a pinned digest test (reviewed contract change discipline).

- [ ] **Step 1: Write failing tests** — full trigger matrix (each trigger alone; combinations; none); proximity boundary cases (exactly 2.0% fires; 2.01% does not); inert-port matrix; watch-tier non-escalation; determinism property; frozen-constant digest pin.

Run: `pytest tests/orchestration/test_pipeline_escalation.py -v` — expected FAIL.

- [ ] **Step 2: Implement.**

- [ ] **Step 3: Run and commit**

```bash
git add guanlan_v2/orchestration/pipeline/escalation.py tests/orchestration/test_pipeline_escalation.py
git commit -m "feat(orchestration): pure zero-LLM escalation judge with frozen thresholds"
```

---

## Task 6: Sealed deep-decide preset (context-bound single code)

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/deep_decide.py`, `config/orchestration/presets/luozi_deep_decide_v1.json`
- Test: `tests/orchestration/test_pipeline_deep_preset.py`

**Interfaces:**
- Consumes: Phase 7 `PlanPresetRecord`/`PlanPresetRegistry` + materializer (reviewed extension mechanism per Task 0); Phase 8 workers `pv.price_action`, `pv.technical`, `pv.microstructure`, `text.news`, `dec.bull`, `dec.bear`, `dec.pm`, `dec.trader`; the Phase 5 latest committed `ContextSnapshot` accessor.
- Produces:
  - **Amendment 3 (post-Task-6 E1, four executed resistances ruled):** Preset `pipeline.luozi_deep_decide` v1 (`DEEP_DECIDE_PRESET_ID = "pipeline.luozi_deep_decide"`), carried by a **Phase 10-registered `PlanPresetRecord@2`** (adds the debate vocabulary in the implemented Phase 8 draft shapes — the Phase 7 record/loader structurally refuse debate identity and stay untouched; a Phase 10-owned loader accepts v1+v2 files from the same presets directory). Graph frozen at the validation-green **ten-worker set**: `pv.price_action + pv.technical + pv.microstructure + text.news` evidence + `text.sentiment` `→ dec.bull ↔ dec.bear` (one round) `→ dec.research_mgr → dec.pm` (deep tier) `→ dec.trader` (sole terminal, `PortfolioTargetProposal@1`); evidence/debate nodes are `auxiliary=True` per the implemented Phase 8 semantics (their value reaches the judge via the debate transcript — same scoping as Phase 8's own e2e). **The subject is run-scoped, not plan-scoped** (ruled: `InputArtifactBinding` is dispatch-time intra-plan only; no worker declares `RunSubject@1`): the committed `RunSubject@1` artifact stays the digest-bound authority, and the Task 0b assembly threads it — rendered trusted subject block into prompt assembly + `subject.code` into instrument-param data prefetch (clause **E2b**, Phase 10-owned assembly extension, zero upstream edits; landing split = the assembly threading itself in this task where the materializer needs it, the runner invocation in Task 7). One `PlanPresetRecord@2` digest serves every stock and an `ApprovalLease` binds it (ruling R-B). Contingency: if profile v2 rejects debate drafts under `source=PRESET` (only the DYNAMIC path is e2e-proven), report back before inventing anything.
  - Registers the deep-decide `PlanPresetRecord` via Task 0b's `build_phase10_preset_registry` (beside Task 3's screening-lane record); record digest golden-frozen. Note (ratified D1): `ApprovalLease` is deliberately UNREGISTERED upstream and the lease verbs are `PlanApprovalCoordinator` methods — assume no `ApprovalLease@1` registry entry anywhere.
  - `def materialize_deep_decide_draft(*, request, preset_registry, context_snapshot_ref, subject_ref, clock) -> MaterializedDeepDecide` — composite return (internal carrier: `draft: PlanDraft`, `subject_ref`, `badges: tuple`) since `PlanDraft` has no badge field; `source=PRESET`; `subject_ref` is the committed `RunSubject@1` artifact (run-scoped per Amendment 3); session date derives from `clock` in +08:00 session terms (reuse the identity-pinned session-date helper — `OrchestrationRequest` carries no session date); recency badge when the snapshot's data date < the session date; missing snapshot OR missing subject refuses typed. The latest-committed-snapshot accessor does not exist upstream — the caller supplies `context_snapshot_ref` (Task 7 gains a `latest_snapshot_fn` injected port).

**Required invariants:**

1. the preset JSON normalizes deterministically and its record digest is golden-frozen; any graph edit fails the golden;
2. drafts pass Phase 1 validation + profile-v2 runtime support; `dec.trader` is the only terminal and emits only `PortfolioTargetProposal@1`;
3. no plan param carries a code — a param-injected code is structurally impossible (schema has no such field); the subject is exclusively the bound `RunSubject@1` artifact, and materialization without a `subject_ref` refuses (typed);
4. the ContextSnapshot recency badge appears whenever snapshot data-date < request session date; a missing snapshot refuses materialization honestly (typed error → live path degrades to fast chain).

- [ ] **Step 1: Write failing tests** — preset load/normalize/digest pin; materialize happy (code via request context); validation green; recency badge matrix; missing-snapshot refusal; terminal/worker-set assertions; registry extension chain (Phase 7 golden byte-identical).

Run: `pytest tests/orchestration/test_pipeline_deep_preset.py -v` — expected FAIL.

- [ ] **Step 2: Implement preset + registry extension.**

- [ ] **Step 3: Run and commit**

```bash
git add guanlan_v2/orchestration/pipeline/deep_decide.py config/orchestration/presets/luozi_deep_decide_v1.json tests/orchestration/test_pipeline_deep_preset.py
git commit -m "feat(orchestration): sealed context-bound deep-decide preset + phase10 preset registry"
```

---

## Task 7: `make_orchestrated_decide` wrapper + watcher/server binding

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/live_decide.py`
- Modify: `guanlan_v2/seats/watcher.py` (additive `decide_fn` plumbing on `run_loop`), `guanlan_v2/server.py` (env-gated wiring)
- Test: `tests/orchestration/test_pipeline_live_decide.py`

**Interfaces:**
- Consumes: Task 5 judge/context builder; Task 6 materializer; Phase 7 `register_and_try_lease`/`admit_after_approval` (D1); Phase 2 `run_plan` + Phase 9 durable stores (D2); Phase 9 `note_external_llm_use`; the seats decision-append helper (D7); the watcher injectable `tick(..., decide_fn=...)` seam.
- Produces:
  - `class DeepDecideBindings(ContractModel-free dataclass)`: `stores`, `preset_registry`, `coordinator`, `admission`, `clock`, `news_titles_fn: ... | None`, `position_fn: ... | None` (Task 5 ruling: in-process READ-ONLY seats-ledger state replay → `{code: entry_price}`; the context builder prefers it over tail rows for `hold_entry`; absent ⇒ `strat_bands` badge unchanged), `latest_snapshot_fn` (Task 6 finding: no latest-committed-ContextSnapshot accessor exists upstream — this port scans the durable stores; absent snapshot ⇒ honest deep refusal → fast fallback), `persist_decision: Callable`, `note_llm_use: Callable[[int], None]`, `plan_runner` (the Task 0b `build_production_plan_runner` product — the ONLY execution path; scripted gateways enter through the same factory seam in tests). **E2b assembly half (Amendment 3/4 ruling) lands HERE:** a per-run Phase 10 prompt assembler with the subject closed over occupies the `prompt_assembler` injection seam of `build_production_plan_runner` (the implemented trusted channel carries name+digest pairs only — no caller text — so the rendered trusted subject block enters via this per-run assembler) plus `subject.code` threading into instrument-param data prefetch; both Phase 10-owned, zero upstream edits. Known upstream pin to reconcile: `BridgeCatalogView` over the full Phase 9 runtime raises `CatalogError` for `dec.research_mgr` (no reviewed experience prefetch row — the strict-xfail pin from Task 6's fix round marks it; closing the grant gap is Task 11's material work, and Task 7 binds whatever reviewed shape exists at implementation time).
  - `def make_orchestrated_decide(*, fast_decide: Callable[[dict], dict], bindings: DeepDecideBindings) -> Callable[[dict], dict]` — the wrapper:
    1. **captures the decisions tail BEFORE running `fast_decide`** (Task 5 trap: `_decide_impl` persists its own decide row, so a tail read after the fast call makes `prev_direction == fast_direction` and the flip trigger can never fire — explicit regression test mandatory), then always runs `fast_decide(payload)`; on fast failure returns it unchanged (deep never rescues a broken fast path);
    2. builds the `EscalationContext` (pre-captured tail, quote — noting the fresh-gate accepts truthy strings like `"false"` as fresh, caller passes booleans — strat file, optional news + position ports, all kwargs passed EXPLICITLY) and runs `judge_escalation`;
    3. `escalate=False` → returns the fast result untouched;
    4. `escalate=True` → commit the `RunSubject@1` artifact + resolve the snapshot via `latest_snapshot_fn` → `materialize_deep_decide_draft` (composite `MaterializedDeepDecide` return — draft + subject_ref + badges; missing snapshot/subject refusals degrade to fast) → Phase 1 validate + Phase 2 reserve → `register_and_try_lease` → `lease_admitted` ⇒ `admit_after_approval` → execute via `bindings.plan_runner` with the per-run subject-closed prompt assembler (E2b) on the durable stores → extract the committed `PortfolioTargetProposal` → append ONE orchestrated decision row via the seats helper (existing shape + `source:"orchestrated"`, `run_id=<kernel run id>`, target band + trigger ranges in the record's rationale/fields, `escalation_digest`, `escalated_from_asof=<fast row asof>`) → when the trader proposal carries tranche trigger price ranges, additionally append ONE advisory conditional-order record through the existing seats order-record persistence path (D7; same only-if-present key convention, `source:"orchestrated"`, human executes — spec §5 落现有条件单记录) → `note_llm_use(n_llm_invocations)` once → returns the deep-derived record dict with `deep_outcome:"completed"`;
    5. `pending_human` / materialize refusal / any deep exception / lease budget exhaustion ⇒ returns the fast result plus only-if-present keys `deep_attempted: True, deep_outcome: "no_lease"|"failed"|"refused"` — degraded honest, never raises into the tick.
  - Watcher/server seam: `run_loop(*, decide_fn=None)` additive kwarg threading into `tick`; `guanlan_v2/server.py` builds the wrapper only when `GUANLAN_SEATS_DEEP == "1"` (bindings on `build_durable_runtime_stores`); env unset ⇒ byte-identical current behavior.

**Required invariants:**

1. two-row model is explicit: the fast row persists via `_decide_impl` as today; a completed deep run appends exactly one additional orchestrated row referencing the fast row — no suppression, no duplication on retry (idempotency key = kernel run id);
2. every deep failure path returns the fast result with honest markers; the wrapper never raises into `tick` (matrix-tested per failure point);
3. LLM budget: `note_llm_use` fires exactly once per completed deep run with the run's actual LLM invocation count; failed runs report their settled spend;
4. red line: the wrapper writes no ledger `kind=trade`, no order, no signal — import + call sweep;
5. `GUANLAN_SEATS_DEEP` unset ⇒ `run_loop`/`tick` behavior and the seats stores are bit-unchanged (regression: existing watcher suite green, zero new rows).

- [ ] **Step 1: Write failing tests** — fake fast_decide + scripted kernel (fake gateways): happy deep path (row shape keys, run_id, source, escalation digest, note_llm_use once); no-lease `pending_human` fallback; materialize-refusal fallback; run-failure fallback; fast-failure passthrough; idempotent re-tick (same key → one orchestrated row); env-off regression; trade-write sweep.

Run: `pytest tests/orchestration/test_pipeline_live_decide.py -v` — expected FAIL.

- [ ] **Step 2: Implement wrapper + additive watcher/server seams.**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_pipeline_live_decide.py -v` plus the existing seats watcher suite — expected PASS, watcher suite byte-identical green.

```bash
git add guanlan_v2/orchestration/pipeline/live_decide.py guanlan_v2/seats/watcher.py guanlan_v2/server.py tests/orchestration/test_pipeline_live_decide.py
git commit -m "feat(orchestration): lease-gated deep-decide wrapper on the watcher decide_fn seam"
```

---

## Task 8: Deep-chain replay evidence (回放双曲线验收闸)

**Files:**
- Create: `tests/orchestration/test_pipeline_replay_evidence.py`
- Test: (this task is executable evidence, no new source module)

**Interfaces:**
- Consumes: Phase 9 `run_interval_replay` + dual-curve lane + `DualCurveReport`; Task 6 preset.

**Produces:** the executable proof that the deep-decide preset runs under the Phase 9 interval-replay driver and yields a `DualCurveReport` attributable to the preset digest — the evidence a human cites when signing an intraday lease (spec §5: 有证据才签租约,人判断曲线).

**Required invariants / scenarios:**

1. a short fixture interval (scripted gateways, fake data context) replays the deep preset per decision point **through the Phase 9 injectable `ReplayRuntimeBindings.admission`/`ReplayPlanCoordinator` seam — the sanctioned path (real-path multi-point `dag.run_plan` remains refused upstream; its interval-shaped resolution stays chartered post-Phase-10)** — and produces both curves under one `ShadowExecutionConfig` attestation;
2. the resulting `DualCurveReport` carries the deep preset's `preset_record_digest` in its config attestation chain (add the linkage assertion; if the implemented Phase 9 report lacks a preset-linkage field, record the candidate-plan→preset digest chain through the run events instead — no new contract invented);
3. the evidence convention is pinned by test: `issue_lease(..., reason=...)` for `DEEP_DECIDE_PRESET_ID` leases must be non-empty (Phase 7 already enforces `reason: NonEmptyStr`) and the console lease card displays it — the convention that `reason` cites the `DualCurveReport` digest is documented in the test's docstring and the Exit Gates (procedural, human-judged — this plan does not pretend to enforce report quality structurally);
4. zero LLM in the deterministic lane; the LLM lane runs scripted gateways only.

- [ ] **Step 1: Write the failing evidence suite** (red = wiring gaps while Tasks 6–7 land).

Run: `pytest tests/orchestration/test_pipeline_replay_evidence.py -v`

- [ ] **Step 2: Fix wiring gaps only (no new contracts).**

- [ ] **Step 3: Run and commit**

```bash
git add tests/orchestration/test_pipeline_replay_evidence.py
git commit -m "test(orchestration): deep-decide preset dual-curve replay evidence gate"
```

---

## Task 9: Pipeline router + D7 TA inbox + console recommendation card

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/api.py`, `ui/console/console-recommendation-card.jsx`
- Modify: `guanlan_v2/server.py` (additive mount, D8 idiom); the existing console page mount file (read from the repo at implementation time, P7 Task 0 clause (d) idiom) — mount + `?v` bump via `Edit`
- Test: `tests/orchestration/test_pipeline_api.py`

**Interfaces:**
- Consumes: Tasks 2–4 builders/assembler; Phase 7 planner + approval surface; Phase 9 durable stores; Task 1 `TaSubmission`.
- Produces: `def build_pipeline_router() -> APIRouter` mounted at `/orchestration/pipeline`:
  - `POST /orchestration/pipeline/start` — body `{goal?: str, preset_id?: str, source_kind?: "v4"|"lane0"|"model_variant", top_n?: int, variant_id?: str}`; exactly one of `goal`/`preset_id`/`source_kind` modes: `goal` → Phase 7 planner path; `preset_id` → preset materialization (deep-decide etc.); `source_kind` → run the candidate handler, build the screening draft, persist candidate; ALL modes return `{ok, request_id, status: "awaiting_approval", candidate_plan_digest, cost_preview}` — **never self-approves** (leases apply only downstream via the coordinator, exactly as everywhere else);
  - `GET /orchestration/pipeline/state?request_id=` — admission/run/terminal status projection + badges; unknown → 404 typed;
  - `GET /orchestration/pipeline/runs` — recent pipeline requests (id, mode, status, as_of) from the durable stores;
  - `GET /orchestration/pipeline/screening/latest` — the latest committed `RecommendationSlate` semantic projection (+ archive id); none yet → `{ok: true, slate: null}` honest;
  - `POST /orchestration/pipeline/ta_ingest` — body `{author: str, title?: str, text: str}`; missing/blank author → 422 (D7 attribution mandatory); writes `var/ta_inbox/<utc-ts>_<digest8>.json` (`TaSubmission` dump + raw text) append-only + one index line in `var/ta_inbox/index.jsonl`; returns the `TaSubmission` projection. **No LLM, no processing** — pv.curator (#27) consumes the inbox in its own later phase; ingested text is untrusted data (FSI), stored verbatim, never executed or prompt-injected here.
- `ui/console/console-recommendation-card.jsx` — spec §4 landing ①: follows the P7 approval-card idiom (fold/poll/cleanup, copied per page, no cross-page import): collapsed header shows the latest slate date + entry count; expanded list shows per-stock code/rating/degradation badges and the mandatory advisory banner verbatim; polls `GET /orchestration/pipeline/screening/latest` every 60s while open; empty state renders 「暂无编排推荐」 honestly. Mounted beside the existing console cards; no new page.
- All handler I/O in `to_thread`; all failure bodies honest and typed.

**Required invariants:**

1. route table is additive (snapshot test); no endpoint executes an unapproved plan or writes orders/signals/trades;
2. `start` leaves zero reservations when the draft fails validation (typed 422 with issue codes);
3. ta_ingest is append-only + idempotent by content digest (same author+text → same file, one index line);
4. the router binds `build_durable_runtime_stores` in production wiring; tests inject tmp/in-memory stores;
5. the card displays only server truth (no client-side ranking, no optimistic state); browser verification after a 9999 restart is an Execution Handoff checkpoint artifact, not a pytest gate.

- [ ] **Step 1: Write failing router tests** — mode matrix (goal/preset/source_kind + ambiguous-mode 422); awaiting_approval + zero-reservation-on-invalid; state/runs/latest projections incl. empty-honest; ta_ingest happy/no-author-422/idempotent; route snapshot.

Run: `pytest tests/orchestration/test_pipeline_api.py -v` — expected FAIL.

- [ ] **Step 2: Implement router + mount.** Note: 9999 restart required to serve routes; tests run in-process.

- [ ] **Step 3: Run and commit**

```bash
git add guanlan_v2/orchestration/pipeline/api.py guanlan_v2/server.py ui/console/console-recommendation-card.jsx tests/orchestration/test_pipeline_api.py
git commit -m "feat(orchestration): pipeline router + D7 TA inbox + console recommendation card"
```

(Include the console mount-file edit in the same commit's pathspec once the exact file resolves.)

---

## Task 10: `ww_orchestrate_*` + `ww_ta_ingest` console tools(五处同步)

**Files:**
- Modify: `guanlan_v2/console/tools.py` (four `WW_TOOL_TABLE` rows + impls), `guanlan_v2/console/api.py` (`_SYSTEM_PROMPT` mentions only), `tests/test_console_tools.py` (count/reachability bumps), `tests/test_guanlan_mcp.py` (MCP count bump)
- Regenerate: `docs/agent_data_interfaces.md` via `scripts/gen_agent_interface_doc.py`
- Test: `tests/test_console_tools.py` (extended)

**Interfaces — Produces (tool rows):**

| name | confirm | reachable | behavior |
|---|---|---|---|
| `ww_orchestrate_start` | **True** | `['/orchestration/pipeline/start']` | body passthrough of the Task 9 start modes; returns request_id + `status:"awaiting_approval"` + cost preview verbatim — the tool NEVER approves; content states 「已受理,等待审批卡人审」 honestly |
| `ww_orchestrate_status` | False | `['/orchestration/pipeline/state','/orchestration/replay/state','/orchestration/weiwo/state']` | polls the matching state endpoint by id prefix; full JSON content via `_wrap` discipline |
| `ww_orchestrate_runs` | False | `['/orchestration/pipeline/runs']` | recent runs listing |
| `ww_ta_ingest` | **True** | `['/orchestration/pipeline/ta_ingest']` | author mandatory in the input schema; submits to the inbox; returns the `TaSubmission` receipt |

Impls use `_self_get`/`_self_post` in `to_thread` (repo red line) and assemble **full structured content** (the `_wrap` envelope lesson: data tools must return complete JSON content, regression-tested through the real `_wrap`).

**五处同步 (each bumped from the values asserted at implementation time — never hardcode today's numbers):** ① `WW_TOOL_TABLE` rows; ② `_SYSTEM_PROMPT` one-line mentions; ③ `tests/test_console_tools.py` count guards (+4) + `_WW_REACHABLE_ENDPOINTS` drift guard rows; ④ MCP auto-derivation count in `tests/test_guanlan_mcp.py` (+4); ⑤ regenerate `docs/agent_data_interfaces.md` and its drift-guard test passes.

**Required invariants:**

1. all four tools flow through the real `_wrap` envelope in tests (content is complete JSON, not a truncated fallback);
2. `ww_orchestrate_start`/`ww_ta_ingest` are confirm-gated; status/runs are not;
3. MCP surface gains exactly the four tools automatically (no manual glmcp edit);
4. the interface doc regenerates cleanly and its guard test passes.

- [ ] **Step 1: Write failing tool tests** — row presence/schema; confirm flags; reachability rows; `_wrap`-through content completeness (fake `_self_post`); count guards red at old values.

Run: `pytest tests/test_console_tools.py -v` — expected FAIL on missing rows.

- [ ] **Step 2: Implement rows/impls/prompt mentions; bump guards; regenerate the doc** (`python scripts/gen_agent_interface_doc.py`).

- [ ] **Step 3: Run and commit**

Run: `pytest tests/test_console_tools.py tests/test_guanlan_mcp.py tests/test_agent_interface_doc.py -v` — expected PASS.

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py tests/test_guanlan_mcp.py docs/agent_data_interfaces.md
git commit -m "feat(console): ww_orchestrate_* + ww_ta_ingest tools with five-place sync"
```

---

## Task 11: Phase 10 registry/catalog chain node + goldens

**Files:**
- Create: `guanlan_v2/orchestration/pipeline/chain.py`, `tests/orchestration/golden/phase10_schema_manifest_v1.json`, `tests/orchestration/golden/phase10_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase10_chain.py`

**Interfaces — Produces:**

- `PHASE10_PUBLIC_MODELS` — exactly: `CandidateSlate`, `RecommendationSlate`, `EscalationReport`, `TaSubmission`, `RunSubject`, `PlanPresetRecordV2` (registered as `PlanPresetRecord@2`; Amendment 3) (6 models). `PHASE10_INTERNAL_MODELS` — reviewed-reason map for `CandidateEntry`, `RankingArtifact`/`RankingRow`, `RecommendationEntry`, `EscalationTrigger`, `EscalationContext`, `ScreeningCostPreview` (value carriers, never cross-boundary payloads; `DeepDecideBindings` is a plain dataclass, invisible to the ContractModel firewall by design).
- `build_phase10_registry(expected_phase9_digest) -> SchemaRegistry` + `PHASE10_REGISTRY_DIGEST`; `build_phase10_catalog_snapshot(phase9_snapshot, ...) -> WorkerCatalogSnapshot` + `PHASE10_CATALOG_DIGEST` — adds exactly the three `cand.*` DETERMINISTIC WorkerSpecs (tier READER, `can_emit_decision=False`, `selection_scope="dynamic_allowed"`, output `CandidateSlate@1`) and the deep-decide preset record reference; inherited entries byte-identical; wrong base refused.

**Required invariants:** chain linearity; byte-identical inheritance sweep across all phases; goldens hand-frozen (never regenerated from test code); `count_final_workers` = Phase 8 count + 3; no `EventType` member added (pinned regression).

- [ ] **Step 1: Write failing chain tests** — wrong-base refusal; population exactness (6 publics, internal/public partition disjoint+exhaustive for pipeline modules); inheritance sweep; golden equality; cand.* spec assertions; EventType frozen-set regression.

Run: `pytest tests/orchestration/test_phase10_chain.py -v` — expected FAIL.

- [ ] **Step 2: Implement chain + freeze goldens by review.**

- [ ] **Step 3: Run and commit**

```bash
git add guanlan_v2/orchestration/pipeline/chain.py tests/orchestration/golden/phase10_schema_manifest_v1.json tests/orchestration/golden/phase10_catalog_manifest_v1.json tests/orchestration/test_phase10_chain.py
git commit -m "feat(orchestration): phase10 cumulative registry/catalog chain + cand.* workers"
```

---

## Task 12: Whole-pipeline e2e + red-line regression

**Files:**
- Create: `tests/orchestration/test_phase10_e2e.py`
- Test: (executable proof, no new source)

**Scenarios (all on in-memory stores + scripted gateways, zero network):**

1. **A-chain e2e:** fixture ranking → `cand.v4` slate → `build_screening_plan_draft` → Phase 1 validate → Phase 2/7 admission with a human APPROVED decision → `run_plan` (scripted lane gateways) → `build_recommendation_slate` → banner-first md → archive landing spy. Assert: one approval for the whole N-lane plan; degraded lane honesty; no v4 artifact write.
2. **B-chain e2e:** scripted fast decide (direction flip) → judge fires → deep draft via preset → `register_and_try_lease` with an active lease → run → one orchestrated seats row (existing shape, `source:"orchestrated"`) + `note_llm_use` once; then lease exhausted → next escalation returns fast result with `deep_outcome:"no_lease"`.
3. **Red lines:** AUTO rejected for every Phase 10 draft; DYNAMIC screening drafts never lease-admit; no module in `guanlan_v2/orchestration/pipeline/` imports seats-trade/order-write surfaces or HTTP clients (import sweep); `kind=trade` appears nowhere; escalation constants digest unchanged; watcher env-off bit-unchanged (re-run of the Task 7 regression); the full `tests/orchestration` tree passes.

- [ ] **Step 1: Write failing e2e** (red = wiring gaps only).

Run: `pytest tests/orchestration/test_phase10_e2e.py -v`

- [ ] **Step 2: Fix wiring gaps only (no new contracts).**

- [ ] **Step 3: Run the full tree and commit**

Run: `pytest tests/orchestration -v` and `python -m compileall -q guanlan_v2/orchestration`

```bash
git add tests/orchestration/test_phase10_e2e.py
git commit -m "test(orchestration): phase10 pipeline e2e + red-line regression"
```

---

## Phase 10 Exit Gates

### Upstream handoff and chain
- [ ] every Phase 1–9 Exit Gate remains green; `test_phase10_handoff.py` passes with reviewed frozen evidence; all eight correction clauses reconciled;
- [ ] `PHASE10_*` chain nodes exist with hand-frozen goldens; inherited entries byte-identical; no upstream golden touched; no new `EventType`/data-method id.

### Production assembly (Task 0b)
- [ ] the assembler composes the real kernel classes (CatalogRuntime / ExecutionRuntime / ArtifactPool / worker ModelGateway) into the launcher's plan-runner path, the gateway factory being the ONLY test/production difference; the pilot preset executes end-to-end through the composed runner with scripted gateways, zero network, zero assembly stand-ins;
- [ ] the three Phase 8 tier seats resolve via the explicit repo llm.yaml path; unconfigured tiers raise loudly; no admission/approval bypass exists in assembly;
- [ ] `build_phase10_preset_registry` refuses non-Phase-7 bases; the Phase 7 preset golden stays byte-identical.

### A · 帷幄选股
- [ ] `cand.*` workers are deterministic, zero-LLM, provenance-complete, staleness-badged; `cand.lane0` is the honest typed refusal in v1 (upstream leader-code absence is gate-pinned; refusal carries provenance); v4 surface read-only;
- [ ] the screening lane preset is sealed and structurally code-free (subject exclusively via the bound `RunSubject@1` artifact); batches admit per-code through the Phase 7 lease channel with the cost preview as the human's whole picture (or N pending cards without a lease); per-code independence holds; empty slate refuses;
- [ ] `RecommendationSlate` copies ratings verbatim, badges degradations, carries `no_cross_sectional_summary_v1` (pm 全场裁决 deferred, field retained), opens every md with the advisory banner, lands idempotently in the archive per batch_id.

### B · 落子买卖点
- [ ] the escalation judge is pure/deterministic with frozen golden-pinned thresholds; absent ports are inert-with-badge; watch-tier never escalates;
- [ ] the deep preset is sealed and code-agnostic (subject exclusively via the bound `RunSubject@1` artifact; missing subject refuses); leases bind its record digest; missing snapshot refuses honestly;
- [ ] the wrapper never blocks or breaks the fast chain (all failure paths return the fast result with honest `deep_outcome`); one orchestrated row per completed deep run, idempotent, budget-reconciled once; `GUANLAN_SEATS_DEEP` unset ⇒ bit-unchanged watcher;
- [ ] deep-chain dual-curve replay evidence exists and is attributable to the preset digest; the lease-reason evidence convention is documented and displayed.

### C · 公共件
- [ ] the pipeline router is additive, never self-approves, refuses invalid drafts with zero reservations; ta inbox is append-only, author-mandatory, LLM-free, FSI-isolated;
- [ ] four console tools pass through the real `_wrap` with complete content; confirm gating correct; five-place sync green (counts bumped from implementation-time values); MCP + interface doc follow automatically;
- [ ] the recommendation card rides the existing console page (no new page), shows the advisory banner + degradation badges, renders honest empty state — reviewed screenshot artifact after 9999 restart.

### e2e, red lines and scope
- [ ] both chain e2es pass on in-memory stores with zero network; AUTO/lease/trade/import sweeps green; full `tests/orchestration` tree green; `compileall` clean;
- [ ] modifications outside `guanlan_v2/orchestration/pipeline/` are exactly the reviewed additive seams (watcher/server/console tools/prompt/tests/doc); explicit-pathspec commits verified in `git log --stat`.

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — upstream evidence + all eight correction clauses resolved against implemented APIs;
2. after Tasks 0b–2 — production-assembly proof (pilot preset through the composed runner), contracts, and deterministic candidate workers (honesty matrix);
3. after Tasks 3–4 — screening builder/preview + recommendation assembly/landing;
4. after Tasks 5–6 — escalation judge (frozen constants) + sealed deep preset;
5. after Tasks 7–8 — live wrapper (never-blocks matrix + env-off regression) + replay evidence;
6. after Tasks 9–10 — router/inbox + console tools five-place sync (restart 9999 to serve routes; verify on 9998 first);
7. after Tasks 11–12 — chain goldens + full e2e/red-line suites plus all Exit Gates.

A/B are independent after Tasks 0b–1 (A = Tasks 2–4, B = Tasks 5–8) and may be executed as parallel workstreams by separate subagents provided each merges through the same review gate; C (Tasks 9–10) starts after both. No execution method requires a particular optional skill package.
