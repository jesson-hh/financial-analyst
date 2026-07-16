# Orchestration Phase 7 · 动态 Orchestrator (Planner) + Plan 人审承载面 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the bounded planner loop, the durable approval carrier, and the end-to-end dynamic admission task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Build the dynamic Orchestrator: a catalog-confined Planner LLM that consumes one frozen `ContextSnapshot` + the pre-persisted `OrchestrationRequest` + one verified `WorkerCatalogSnapshot` and emits a **MainPlanDraft** candidate — where "MainPlanDraft" is the spec's vocabulary for a Phase 1 `PlanDraft` with `phase="main"`, `source=PlanSource.DYNAMIC`; Phase 7 defines **no new draft class and no second validation/freeze/admission path**. The candidate flows verbatim through Phase 1 `validate_plan_draft`/`compute_candidate_plan_digest`/`freeze_plan` and the Phase 2 `PlanAdmissionService`. Phase 7 adds exactly three missing pieces: (1) **generation** — a budget-reserved, bounded-attempt Planner loop with a request-level `fallback_preset_id` path and honest termination; (2) **the human-approval carrier** — a durable digest-bound `PlanApproval` recording surface riding the existing console (events.jsonl + SSE + a REST decide endpoint following the `POST /confirm` precedent at `guanlan_v2/console/api.py:827-838` — api.py:741 is the confirm-callback closure, not the route; file:line cites in this plan are advisory and every symbol/route binds by name), plus a plan-diff typed payload with deterministic `rendered_md` for the reviewer; (3) the **Phase 7 registry/catalog chain** with its own goldens. Dynamic v1 default is `approval_policy=required`; `AUTO` remains rejected for every `PlanSource`; approval events remain the sole source of truth (no `approved_by/at` is ever written back into a Plan). Phase 2 already executes validated DYNAMIC static main DAGs — Phase 7 adds no new runner.

**Architecture:** The Planner is an LLM invocation executed inside the runtime discipline: catalog-owned prompt/SKILL/guardrail materials (never inline strings), one `PromptAssemblyRecord` per attempt, one `ModelGateway` invocation per attempt, and a per-attempt `BudgetReservation` with `scope_type="planner"` drawn from the ONE run ledger that bootstrap/planner/main share (spec §10). The Planner authors only a closed low-authority field set (nodes/dependencies/slots/sinks/params/universe/budget request); every authority-bearing field (`approval_policy`, `catalog_digest`, `schema_registry_digest`, `as_of`, `mode`, `context_snapshot_ref`, `source`, `phase`) is runtime-stamped. The Planner can only SHRINK — structurally: Phase 1 `PlanNode` carries no tool/server/path/prompt/skill override field, `_HIDDEN_AUTHORITY_KEYS` are rejected in params, and the Planner is deliberately **not** registered as a catalog `WorkerSpec`, so it can never select itself into a plan. Fallback is exclusively the pre-persisted `OrchestrationRequest.fallback_preset_id` resolved against a sealed, golden-frozen `PlanPresetRegistry`; a failed generation with no explicit fallback terminates honestly. The approval carrier persists pending/decision rows in an append-only fsync journal that survives process restarts (the Phase 2 in-memory stores make no durability claim), mirrors them into the console event stream for the human, and feeds decisions into Phase 2 `PlanAdmissionService.record_approval` — the console card is a new card on the EXISTING console surface (user red line: UI 只填充不重建; no new page).

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio` (`to_thread` for journal/LLM I/O reached from the 9999 loop), `pytest` + `pytest-asyncio`, FastAPI `TestClient` for the console endpoints, React (existing console card idiom) for the UI card. All modules `from __future__ import annotations`. Depends on Phase 1 contracts (`guanlan_v2/orchestration/`), Phase 2 runtime (`budget.py`, `eventstore.py`, `catalog_runtime.py`, `admission.py`, `worker.py` ports), Phase 3 `normalize_symbol`, and the Phase 4→5→6 registry/catalog chain digests.

## Global Constraints

These extend, and never override, the Phase 1–6 Global Constraints, the frozen CRIB, and each upstream plan's Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import `PlanDraft`, `PlanNode`, `Dependency`, `OrchestrationRequest`, `PlanValidationReport`, `PlanApproval`, `validate_plan_draft`, `compute_candidate_plan_digest`, `freeze_plan` from `guanlan_v2/orchestration/spec.py`/`events.py`; `WorkerSpec`/`WorkerCatalogSnapshot`/`build_catalog_snapshot`/skill-v1 from `catalog.py`; `BudgetLedger`/`AuthoritativeClock` from `budget.py`/`runtime_clock.py`; `PayloadStore`/`EventStore`/`EventRefusalAuditSink`/`RuntimeUnitOfWork` from `eventstore.py`; `CatalogRuntime` from `catalog_runtime.py`; `PromptAssembler`/`ModelGateway`/`AssembledModelRequest` ports from `worker.py`; `PlanAdmissionService` from `admission.py`. Phase 7 must not redefine canonical JSON, the candidate/plan digest, plan validation, freeze semantics, event semantics, or admission order.
- **`TypedPayloadRef` vs `PayloadRef` (Phase 1 Amendment 1).** `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)` is the implemented Phase 1 composite for schema-typed references; bare `PayloadRef` is a plain storage locator. Phase 7's typed reference fields (`prompt_assembly_ref`, `record_ref`, `plan_diff_ref`) are `TypedPayloadRef`s with validator-pinned `schema_ref`s, and digest/namespace checks go through `.payload_ref.content_digest`/`.payload_ref.namespace`; pure storage locators stay plain `PayloadRef`, and Phase 1-owned fields keep whatever type the implemented (post-Amendment) Phase 1 API defines.
- **One admission path.** Dynamic and fallback candidates go through the exact Phase 2 sequence: Phase 1 validation → runtime-support report → atomic same-digest plan reservation → same-digest REQUIRED approval → Phase 1 freeze → `PlanAdmitted` → dispatch. There is no Planner-side shortcut, no second `freeze_plan`, and no post-approval digest.
- **`AUTO` stays rejected for every `PlanSource`.** Phase 7 does not relax `auto_approval_rejected` (spec.py:869-870), not even for presets. Relaxing it for versioned static presets is a future reviewed change to `validate_plan_draft` itself and is explicitly out of scope.
- **Planner authority is subtractive only.** The Planner chooses catalog worker IDs with `selection_scope="dynamic_allowed"` and params bound by each worker's `params_schema_ref`. It cannot name a Python callable, file path, tool, MCP server, or skill path; it cannot widen any `capability_allowlist`; it cannot pick `approval_policy`; it cannot emit debates/gates/reducers/conditions/`max_attempts>1` in v1 (static profile rejects them; Phase 8 lifts). `_HIDDEN_AUTHORITY_KEYS = {"handler","system_prompt","skills","tools","mcp","path"}` are rejected wherever they appear in authored params.
- **Fallback is request-level and explicit.** Only a pre-persisted `OrchestrationRequest.fallback_preset_id` resolved in the sealed `PlanPresetRegistry` may materialize a `PRESET_FALLBACK` draft after generation exhaustion. Neither the model nor the runtime ever picks a preset. Failure without an explicit fallback is an honest terminal outcome, never a silent default.
- **One budget ledger.** Every Planner attempt takes a `BudgetReservation` (`scope_type="planner"`) from the same event-sourced ledger the bootstrap/main plans use, settles actual usage, and releases the remainder. A failed or invalid attempt consumes budget honestly. Attempts are bounded by the reviewed `PlannerSpec.max_generation_attempts` (hard cap 3); no unbounded regeneration loop exists.
- **Approval events are the sole source of truth.** A `Plan` never carries approver identity or decision time (structural: Phase 1 `Plan` has no such fields; tests must keep it that way). Human decisions are recorded as durable journal rows + Phase 1 `PlanApproval` values fed to `PlanAdmissionService.record_approval`, reusing the existing `EventType.PLAN_APPROVED`/`PLAN_REJECTED`. Phase 7 adds **no new `EventType` member** and flips no Phase 1 absence guard.
- **Digest-bound, restart-durable approval.** Every pending card and decision binds the exact `candidate_plan_digest`; changing any executable field produces a new digest requiring a new approval. The approval journal is append-only, fsynced, and replayable after process death — the console's in-memory `pending` confirm futures are NOT reused for plan approval.
- **UI 只填充不重建.** The reviewer surface is one new card in the existing console page plus additive REST endpoints on `build_console_router`. No new page, no new frontend app, no console event schema mutation of existing event types.
- **Untrusted-data isolation.** Catalog roster/params schemas, the pre-persisted request goal, and runtime budget figures enter the Planner prompt as trusted material; ContextSnapshot-derived narrative (regime `rendered_md`, artifact text) enters only as ordered untrusted blocks via the Phase 2 `PromptAssembler` channel. Planner `rationale` text shown to the reviewer is labeled untrusted display text and carries no authority.
- **Chain discipline.** Registry base is exactly `PHASE6_REGISTRY_DIGEST` via `build_phase7_registry(expected_phase6_digest)`; catalog base is exactly `PHASE6_CATALOG_DIGEST` via `build_phase7_catalog_snapshot(...)`. Separate goldens; upstream goldens are never regenerated; no "latest" alias.
- **Executable red/green checkpoints.** Every "Write failing … tests" step runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation; collection/environment errors do not count as red. The PASS step reruns the same focused tests plus listed regressions.
- **Explicit pathspec commits only** (shared branch with concurrent sessions; never `git add -A`). No placeholders, DRY, YAGNI, TDD. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 7 work starts only after the Phase 2 Exit Gates (admission/runtime), the Phase 5 Exit Gates (Bootstrap Lane 0 → production `ContextSnapshot`), and the Phase 6 chain nodes pass. Add `tests/orchestration/test_phase7_handoff.py` as an executable consumer test rather than copying upstream assertions.

**Files:**
- Create: `tests/orchestration/test_phase7_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. Phase 1 goldens (`schema_manifest_v1.json`, digest vectors) still pass; `default_registry()` seals; exactly one `compute_candidate_plan_digest`, one `validate_plan_draft`, one `freeze_plan` export from `guanlan_v2/orchestration/spec.py`; `Plan.plan_digest` equals the candidate digest and `Plan` exposes no approver/decision fields;
2. `OrchestrationRequest` carries `fallback_preset_id: LogicalId | None` and `approval_policy` defaulting to `REQUIRED` (spec.py:234-235); `PlanApproval` exposes `is_approved`, `authorizes_freeze(*, request_id, candidate_plan_digest)` and `require_freeze_authority(...)` (events.py:320-354); `EventType` already contains `PLAN_DRAFTED/PLAN_APPROVED/PLAN_REJECTED/PLAN_FROZEN` — Phase 7 asserts it will add none;
3. a DYNAMIC draft that fails to copy the request's approval policy, or whose policy is not REQUIRED, is rejected by `validate_plan_draft` with the two distinct issue codes (spec.py:871-875); `ApprovalPolicy.AUTO` fails for every source; compatibility workers are rejected under DYNAMIC source (spec.py:932-945);
4. Phase 2 `PlanAdmissionService` exposes `prepare_candidate`, `persist_and_reserve_candidate`, `record_approval(candidate_id, approval_input, *, authenticated_actor, idempotency_key) -> RunEvent`, `freeze_and_admit_candidate`, `load_admitted`, `verify_for_dispatch`; `BudgetLedger` exposes `reserve_plan/reserve_node/settle/release/get/get_active_plan/available/replay` and its closed `BudgetTransitionCommand` operation set is importable (Phase 7 will extend it additively with `reserve_planner`);
5. Phase 2 `StaticRuntimeProfile` v1 admits already-validated DYNAMIC **main** static DAGs (P2:1036) — the gate constructs a minimal DYNAMIC pilot-triad draft and shows Phase 1 validation + runtime support accept it without any Phase 7 code;
6. the chain nodes exist and verify: `PHASE4_REGISTRY_DIGEST`/`PHASE5_REGISTRY_DIGEST`/`PHASE6_REGISTRY_DIGEST` + builders, `PHASE4_CATALOG_DIGEST`/`PHASE5_CATALOG_DIGEST`/`PHASE6_CATALOG_DIGEST` + builders, each golden manifest loads, and inherited schema entries are byte-identical up the chain;
7. Phase 5 produces a real frozen `ContextSnapshot` for `phase="main"` planning (Bootstrap Lane 0 output) whose `memory_selection_ref` — a `TypedPayloadRef` after Phase 1 Amendment 1 — satisfies `memory_selection_ref.payload_ref.namespace == "main"`; the gate consumes one fixture snapshot built through the reviewed Phase 5 path (or, if Phase 5 fixtures expose a builder, through that builder — never a hand-rolled parallel constructor);
8. Phase 3 `normalize_symbol` is importable and rejects non-symbol strings (used by the Planner universe parser);
9. no Phase 7 source/test path overwrites Phase 1–6 owned modules, tests, or golden files;
10. Phase 1 Amendment 1 is in force: the amended registry golden carries the 11-model set (8→11, incl. `ContextRuntimeRequirements@1`/`InputArtifactBinding@1`) and the `TypedPayloadRef(schema_ref, payload_ref)` composite imports and resolves (a schema-pinned ref round-trips; bare `PayloadRef` remains the plain locator).

**Task 0 correction clauses** (binding on every later task): if the implemented upstream public API differs from the names above, update this plan to that reviewed API before writing runtime code; do not invent an adapter with parallel semantics. Specifically:

- (a) if Phase 2's implemented `BudgetTransitionCommand`/`BudgetLedger` expose a different closed-operation vocabulary or a dedicated non-plan scope reservation entry point, adopt it verbatim instead of adding `reserve_planner`;
- (b) if Phase 1/2 define how a `scope_type="planner"` `BudgetReservation` binds `candidate_plan_digest` before a candidate exists, adopt that binding; otherwise this plan's reviewed binding (the `OrchestrationRequest` semantic digest) stands;
- (c) if Phase 3 Task 9's implemented console changes altered `build_console_router`'s signature (e.g. `memory_review_verifier` keyword) or the `AdminReviewVerifier`/`AuthenticatedAdminPrincipal` port names/locations, mirror the implemented names for the plan-approval verifier wiring;
- (d) the exact console UI mount point for the new card (the file that mounts `ui/console/console-report-card.jsx`) is read from the repo at implementation time; the card must be mounted beside that card in the same existing page, and its `?v` cache-bust bumped via `Edit`;
- (e) if Phase 5/6 exported chain node names differ (e.g. catalog builder parameter spelling), consume the implemented exports and update Task 9's expected-base wiring;
- (f) if Phase 2's admission emits `PLAN_DRAFTED` (vs an `AdmissionCandidatePrepared` control fact) at candidate persistence, consume whichever the implemented service emits; Phase 7 never appends plan-lifecycle events itself;
- (g) file:line references into Phase 1 modules are pre-Amendment-1 snapshots — e.g. Task 4 cites `BudgetScopeType`/`scope_type="planner"` at context.py:76-78, but Amendment 1 moved that vocabulary (~context.py:90); locate every cited symbol, and any frozen-set/guard flip, by name, never by line.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture**

Record only exact upstream registry/catalog digests and exported symbol signatures; never local paths or mutable singleton identities.

- [ ] **Step 3: Run the upstream suites and the frozen gate**

Run: `pytest tests/orchestration -v`

Expected: every Phase 1–6 test plus `test_phase7_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase7_handoff.py
git commit -m "test(orchestration): gate phase7 on phase2/5/6 contracts"
```

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/orchestrator.py` | Planner contracts (`PlannerSpec@1`, `PlannerAttemptRecord@1`, `PlannerRunRecord@1`), strict output parser, dynamic draft assembly, bounded `run_planner` loop, fallback/halt terminal logic |
| `guanlan_v2/orchestration/plan_presets.py` | `PlanPresetRecord@1`, sealed `PlanPresetRegistry`, strict loader, `materialize_fallback_draft` |
| `guanlan_v2/orchestration/plan_diff.py` | `PlanDiffEntry@1`/`PlanDiff@1`, `build_plan_diff` over `executable_projection()`, deterministic `render_plan_diff_md`, `build_pending_plan_approval` |
| `guanlan_v2/orchestration/approval.py` | `PendingPlanApproval@1` consumption, durable append-only approval journal, `PlanApprovalCoordinator` (register/list/decide/replay) feeding Phase 2 `record_approval` |
| `guanlan_v2/orchestration/planner_gateway.py` | production `ModelGateway` adapter for the `planner` LLM seat (engine `LLMClient`, explicit repo `config/llm.yaml` path, single-shot, thread-isolated) |
| `guanlan_v2/orchestration/phase7_registry.py` | `PHASE7_PUBLIC_MODELS`/`PHASE7_INTERNAL_MODELS`, `build_phase7_registry`, `PHASE7_REGISTRY_DIGEST`, `build_phase7_catalog_snapshot`, `PHASE7_CATALOG_DIGEST`, `build_phase7_planner_spec` |
| `guanlan_v2/console/api.py` (modified, additive) | `GET /plan/approvals`, `GET /plan/approvals/status`, `POST /plan/approvals/decide`, console event emission `plan_approval_request`/`plan_approval_resolved`, optional `plan_approval_coordinator`/verifier wiring |
| `ui/console/console-plan-approval-card.jsx` | pending-approval card on the existing console surface (fold/poll/diff markdown/approve-reject), source + degradation badges |
| `config/orchestration/materials/planner/` | reviewed planner system prompt, `SKILL.md` (skill-v1 grammar), guardrail text — catalog-owned physical sources; paths never enter any contract |
| `config/orchestration/presets/main_research_baseline.json` | the one reviewed v1 fallback preset (pilot triad `text.sentiment → dec.research_mgr → dec.pm`) |
| `config/llm.yaml` (modified, additive) | `planner` seat under `agent_overrides` (deep tier) |
| `tests/orchestration/golden/phase7_schema_manifest_v1.json` | Phase 7 cumulative registry golden (base = Phase 6) |
| `tests/orchestration/golden/phase7_catalog_manifest_v1.json` | Phase 7 cumulative catalog golden incl. frozen `planner_spec_digest` |
| `tests/orchestration/golden/plan_preset_manifest_v1.json` | sealed preset registry golden |
| `tests/orchestration/test_phase7_handoff.py` | executable upstream ABI/golden gate |
| `tests/orchestration/` (`test_orchestrator_contracts.py`, `test_orchestrator_assembly.py`, `test_plan_presets.py`, `test_planner_loop.py`, `test_planner_gateway.py`, `test_plan_diff.py`, `test_approval_store.py`, `test_plan_approval_console.py`, `test_phase7_registry.py`, `test_dynamic_e2e.py`) | task-focused suites |

---

## Task 1: Planner contracts + strict output parser

**Files:**
- Create: `guanlan_v2/orchestration/orchestrator.py`
- Test: `tests/orchestration/test_orchestrator_contracts.py`

**Consumes:** Phase 1 `DigestModel`, strict types (`LogicalId`, `NonEmptyStr`, `DigestHex`, `PositiveInt`, `NonNegativeInt`, `UtcDateTime`), `ContentRef`, `PayloadRef`, `TypedPayloadRef` (the Amendment 1 schema-typed composite), `SkillBinding`, catalog `ModelTier` literal, `_HIDDEN_AUTHORITY_KEYS` (documented set; re-declared as a frozen import, not a copy).

**Produces:**

- Vocabularies:
  - `PlannerAttemptOutcome = Literal["draft_admissible","model_error","timed_out","parse_rejected","shape_rejected","budget_rejected","validation_rejected"]`
  - `PlannerTerminalOutcome = Literal["candidate_ready","fallback_materialized","halted_no_fallback"]`
  - `PLANNER_OUTPUT_CONTRACT = "planner-output-v1"` (domain constant for the authored-JSON grammar).
- `class PlannerSpec(DigestModel):` — `schema_version: Literal["1"] = "1"`, `planner_id: LogicalId`, `version: NonEmptyStr`, `system_prompt_ref: ContentRef`, `skills: tuple[SkillBinding, ...]` (non-empty), `guardrail_refs: tuple[ContentRef, ...]`, `model_tier: ModelTier`, `thinking_budget: NonNegativeInt = 0`, `max_generation_attempts: PositiveInt` (validator: `<= 3`), `attempt_token_reservation: PositiveInt`, `attempt_timeout_sec: PositiveInt = 300`, `output_contract: Literal["planner-output-v1"] = "planner-output-v1"`. All semantic.
- `class PlannerAttemptRecord(DigestModel):` — `schema_version: Literal["1"] = "1"`, `attempt: PositiveInt`, `outcome: PlannerAttemptOutcome`, `issue_codes: tuple[NonEmptyStr, ...] = ()`, `candidate_plan_digest: DigestHex | None = None`, `validation_report_digest: DigestHex | None = None`, `prompt_assembly_ref: TypedPayloadRef | None = None` (validator: when present, `schema_ref` is pinned to `PromptAssemblyRecord@1` and `payload_ref.namespace == "main"`), `reservation_id: NonEmptyStr`, `started_at: UtcDateTime`, `finished_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"reservation_id","started_at","finished_at"})`. Matrix validators: `draft_admissible` ⇒ both digests present and `issue_codes == ()`; `validation_rejected` ⇒ report digest present, non-empty issue codes; `parse_rejected`/`shape_rejected`/`budget_rejected` ⇒ no candidate digest, non-empty issue codes; `model_error`/`timed_out` ⇒ no digests; `finished_at >= started_at`.
- `class PlannerRunRecord(DigestModel):` — `schema_version: Literal["1"] = "1"`, `run_id: NonEmptyStr`, `request_id: NonEmptyStr`, `request_digest: DigestHex`, `context_content_digest: DigestHex`, `planner_spec_digest: DigestHex`, `catalog_digest: DigestHex`, `schema_registry_digest: DigestHex`, `attempts: tuple[PlannerAttemptRecord, ...]`, `terminal_outcome: PlannerTerminalOutcome`, `fallback_preset_id: LogicalId | None = None`, `final_candidate_plan_digest: DigestHex | None = None`, `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"run_id","created_at"})`. Matrix: `candidate_ready` ⇒ final digest set, last attempt `draft_admissible`, `fallback_preset_id is None`; `fallback_materialized` ⇒ final digest set, `fallback_preset_id` set, no attempt `draft_admissible`; `halted_no_fallback` ⇒ both `None`, no attempt `draft_admissible`; attempts strictly ordered `1..n` with `n <= 3`.
- Internal (unregistered, listed in `PHASE7_INTERNAL_MODELS` with reviewed reasons): `class PlannerDraftEnvelope(ContractModel)` — the closed model-authored field set: `nodes` (tuple of node envelopes), `sink_node_ids: tuple[LogicalId, ...]`, `universe: tuple[NonEmptyStr, ...] = ()` (raw symbol strings, normalized later), `budget_request_tokens: NonNegativeInt`, `budget_request_llm_invocations: NonNegativeInt`, `max_concurrency: PositiveInt = 4`, `rationale: str = ""`. Node envelope closed keys: `id, worker_id, params, dependencies, writes_slot, timeout_sec, token_reservation`; dependency envelope closed keys: `upstream_node_id, artifact_slot, upstream_output_key, inject_as, policy` (`policy ∈ {"block","degrade","skip"}`; `accept_statuses` is never model-authored — Phase 1 defaults apply). `extra="forbid"` everywhere.
- `class PlannerOutputRejected(ValueError):` carrying `issue_codes: tuple[str, ...]`.
- `def parse_planner_output(raw_text: str) -> PlannerDraftEnvelope:` — accepts exactly one JSON object (optionally inside a single ```json fence, surrounding whitespace tolerated); rejects: multiple objects, non-object roots, NaN/Inf, non-string keys, any unknown key at any level (`planner_output_unknown_key`), any `_HIDDEN_AUTHORITY_KEYS` member appearing as a params key at any nesting depth (`planner_hidden_authority_key`), any of `gate_ids/debate_id/round_role/debate_round/debate_turn/condition_ref/max_attempts/auxiliary/approval_policy/source/phase/catalog_digest/schema_registry_digest/as_of/mode/context_snapshot_ref/debates/gates/reducers/stop_condition_refs/legacy_source_schema` anywhere (`planner_authored_reserved_field`). Output params must already be plain JSON shapes compatible with Phase 1 `_ensure_json_shaped` (spec.py:195-212).

**Required invariants:**

1. all three registered contracts are strict/frozen/extra-forbid `DigestModel`s with closed `schema_version` and reviewed semantic/audit projections; wall-clock and random-id fields never move semantic digests;
2. the parser is pure and deterministic: same text ⇒ same envelope or same `PlannerOutputRejected.issue_codes` (canonically sorted);
3. no parser path constructs `PlanDraft`/`PlanNode` directly — the envelope is authority-free;
4. `max_generation_attempts > 3` is unconstructible;
5. `rationale` is bounded (validator: ≤ 4000 chars) and never parsed for instructions.

- [ ] **Step 1: Write failing contract/parser tests**

Cover: field/projection matrices for all three contracts (valid + each matrix violation), attempt ordering, terminal-outcome coherence; parser acceptance of a well-formed planner JSON (fenced + bare); every rejection class above with exact issue codes; hidden-authority key at depth 3 of params rejected; determinism (double-parse equality); NaN rejection.

Run: `pytest tests/orchestration/test_orchestrator_contracts.py -v`

Expected: FAIL on missing `guanlan_v2/orchestration/orchestrator.py` contracts (import-time `AttributeError`/`ValidationError` asymmetry counts; collection errors from other suites do not).

- [ ] **Step 2: Implement contracts + parser**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_orchestrator_contracts.py tests/orchestration/test_phase7_handoff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/orchestrator.py tests/orchestration/test_orchestrator_contracts.py
git commit -m "feat(orchestration): planner contracts + strict authored-output parser"
```

---

## Task 2: Dynamic draft assembly (runtime-stamped, shrink-only)

**Files:**
- Modify: `guanlan_v2/orchestration/orchestrator.py`
- Test: `tests/orchestration/test_orchestrator_assembly.py`

**Consumes:** Phase 1 `PlanDraft`/`PlanNode`/`Dependency`/`OrchestrationRequest`/`ContextSnapshot`/`WorkerCatalogSnapshot`/`SchemaRegistry`/`validate_plan_draft`; Phase 3 `normalize_symbol`; Task 1 envelope.

**Produces:**

- `class PlannerShapeRejected(ValueError):` carrying canonical `issue_codes`.
- `def assemble_dynamic_draft(envelope: PlannerDraftEnvelope, *, request: OrchestrationRequest, context: ContextSnapshot, context_snapshot_ref: PayloadRef, catalog: WorkerCatalogSnapshot, schema_registry: SchemaRegistry, draft_id: LogicalId, run_id: NonEmptyStr, remaining_tokens: NonNegativeInt | None = None, remaining_llm_invocations: NonNegativeInt | None = None) -> PlanDraft:`

Runtime-stamped fields (never model-authored): `id=draft_id`, `run_id`, `request_id=request.request_id`, `phase="main"`, `source=PlanSource.DYNAMIC`, `goal=request.goal`, `as_of=context.data_context.as_of`, `mode=context.data_context.mode`, `context_snapshot_ref` (must satisfy `namespace=="main"` and `content_digest==context.content_digest`; mismatch raises before draft construction), `catalog_version`/`catalog_digest` from the snapshot, `schema_registry_digest` from the sealed registry, `approval_policy=request.approval_policy` (verbatim copy — the Planner never chooses), `debates=()`, `gates=()`, `reducers=()`, `stop_condition_refs=()`, `legacy_source_schema=None`, `legacy_source_config_digest=None`, `legacy_mapping_digest=None`. Model-authored fields pass through: nodes (each envelope node → `PlanNode` with Phase 1 defaults for every non-authored field), `sink_node_ids`, `universe` (each raw string through `normalize_symbol`; failure → shape issue `planner_universe_symbol_invalid`), budget request pair, `max_concurrency`.

Generation-side shape checks (each a canonical issue code inside one `PlannerShapeRejected`; the function never partially constructs):
`planner_unknown_worker` (worker_id not in catalog), `planner_worker_not_dynamic` (`selection_scope != "dynamic_allowed"` — also structurally excludes every `compat.*` worker before Phase 1 validation re-rejects it), `planner_budget_exceeds_remaining` (authored budget request exceeds supplied remaining figures when provided), `planner_universe_symbol_invalid`, `planner_duplicate_node_id` (pre-empting the Phase 1 structural error with a planner-attributed code).

**Required invariants:**

1. the returned object is a plain Phase 1 `PlanDraft` (schema_version "2") — `assemble_dynamic_draft` adds no subclass, wrapper, or extra field;
2. for any envelope, the assembled draft's `approval_policy` equals the request's; a REQUIRED request therefore always yields a draft that passes the two dynamic approval checks in `validate_plan_draft`;
3. shrink-only is structural: the assembled draft contains no capability/tool/path/prompt surface anywhere (asserted by walking the draft's `executable_projection()` for the hidden-authority key set);
4. `validate_plan_draft(draft, request=..., context=..., catalog=..., schema_registry=...)` on a well-formed envelope over the pilot triad returns `valid=True` with zero Phase 7 shims;
5. deliberately hostile envelopes (compat worker, unknown worker, forged digest fields — impossible by construction since digests are stamped) never reach Phase 1 validation with planner-chosen authority;
6. determinism: same inputs ⇒ byte-identical `executable_projection()` and identical `compute_candidate_plan_digest`.

- [ ] **Step 1: Write failing assembly tests**

Matrix: happy path over the pilot triad (valid draft + candidate digest stability); each shape issue code; context-ref mismatch; approval-policy copy; stamped-field immunity (envelope cannot influence `as_of`/`mode`/digests because those keys were already parser-rejected — assert end-to-end through `parse_planner_output` + `assemble_dynamic_draft`); universe normalization (dotted/bare/engine codes per Phase 3 grammar; garbage rejected); Phase 1 validator acceptance/rejection pass-through (`unknown_worker` when catalog swapped, `input_schema_mismatch` on a wrong dependency).

Run: `pytest tests/orchestration/test_orchestrator_assembly.py -v`

Expected: FAIL on missing `assemble_dynamic_draft`.

- [ ] **Step 2: Implement assembly**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_orchestrator_assembly.py tests/orchestration/test_orchestrator_contracts.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/orchestrator.py tests/orchestration/test_orchestrator_assembly.py
git commit -m "feat(orchestration): runtime-stamped dynamic MainPlanDraft assembly"
```

---

## Task 3: Fallback preset registry + materializer

**Files:**
- Create: `guanlan_v2/orchestration/plan_presets.py`
- Create: `config/orchestration/presets/main_research_baseline.json`
- Create: `tests/orchestration/golden/plan_preset_manifest_v1.json`
- Test: `tests/orchestration/test_plan_presets.py`

**Consumes:** Phase 1 `PlanNode`/`PlanDraft`/`OrchestrationRequest`/`ContextSnapshot`/catalog snapshot/`validate_plan_draft`; `content_digest`.

**Produces:**

- `class PlanPresetError(ValueError):`
- `class PlanPresetRecord(DigestModel):` — `schema_version: Literal["1"] = "1"`, `preset_id: LogicalId`, `version: NonEmptyStr`, `phase: Literal["main"] = "main"`, `description: NonEmptyStr`, `nodes: tuple[PlanNode, ...]` (non-empty), `sink_node_ids: tuple[LogicalId, ...]` (non-empty), `budget_request_tokens: NonNegativeInt`, `budget_request_llm_invocations: NonNegativeInt`, `max_concurrency: PositiveInt`. All semantic. Validator: node ids unique; every node has `gate_ids == ()`, no debate fields, `condition_ref is None`, `max_attempts == 1` (v1 presets must be admissible under the static profile).
- `class PlanPresetRegistry:` (not a pydantic model; mirrors `SchemaRegistry` shape) — `register(record: PlanPresetRecord) -> None` (duplicate `preset_id` with different content → `PlanPresetError`; identical re-register idempotent; after seal → error), `seal() -> None`, `get(preset_id: LogicalId) -> PlanPresetRecord` (unknown → `PlanPresetError`), `manifest() -> tuple[...]` (sorted by `(preset_id, version)`; entry = preset ref + semantic digest), `registry_digest` property (= `content_digest(list(manifest()))`), `sealed` property.
- `def load_preset_registry(root: Path) -> PlanPresetRegistry:` — strict loader over `config/orchestration/presets/*.json`: UTF-8 no-BOM, `extra="forbid"` via model validation, rejects duplicate `preset_id` across files, seals before returning. Physical paths never enter any record.
- `def materialize_fallback_draft(preset: PlanPresetRecord, *, request: OrchestrationRequest, context: ContextSnapshot, context_snapshot_ref: PayloadRef, catalog: WorkerCatalogSnapshot, schema_registry: SchemaRegistry, draft_id: LogicalId, run_id: NonEmptyStr) -> PlanDraft:` — requires `request.fallback_preset_id == preset.preset_id` (mismatch → `PlanPresetError`; this is THE request-level rule made executable); stamps the same runtime fields as Task 2 but with `source=PlanSource.PRESET_FALLBACK`; copies nodes/sinks/budget/concurrency from the preset; `approval_policy=request.approval_policy` (AUTO still dies in Phase 1 validation — no relaxation). Every preset worker must be `catalog_role="final"` in the bound catalog; a compat worker in a preset → `PlanPresetError` before validation (v1 presets never take the `StaticLegacyPlanAttestation` path; the Phase 1 builder `attest_static_legacy_plan` remains the only attestation producer and is simply unused here).
- The one reviewed v1 preset `main.research_baseline` (version `"1"`): pilot triad `text.sentiment → dec.research_mgr → dec.pm` with the Phase 2 pilot dependency/slot layout, sink `dec.pm` node, reviewed budget request.
- Golden `plan_preset_manifest_v1.json`: sealed `registry_digest` + per-preset semantic digests. Never auto-regenerated.

**Required invariants:**

1. no code path selects a preset other than by exact `request.fallback_preset_id` equality — there is no "default preset", no ordering-based pick, no model-supplied preset id;
2. an unknown/unregistered `fallback_preset_id` is an honest `PlanPresetError`, which Task 4 maps to `halted_no_fallback` (never a silent substitute);
3. materialized drafts pass Phase 1 validation against the Phase 7 catalog and produce a stable candidate digest for fixed `(request, context)`;
4. the sealed registry digest matches the golden; editing a preset file breaks the golden test (reviewed change);
5. registry mutation after seal is impossible.

- [ ] **Step 1: Write failing preset tests**

Cover: record matrix violations (debate field, gate id, `max_attempts=2`); registry register/seal/get/duplicate/idempotent; loader strictness (unknown key, BOM, duplicate id across files); materializer happy path (valid PRESET_FALLBACK draft, digest stability); preset-id mismatch; compat-worker preset rejection; golden manifest equality.

Run: `pytest tests/orchestration/test_plan_presets.py -v`

Expected: FAIL on missing `plan_presets.py` contracts.

- [ ] **Step 2: Implement registry + loader + materializer; author the reviewed preset file; freeze the golden**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_plan_presets.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/plan_presets.py config/orchestration/presets/main_research_baseline.json tests/orchestration/golden/plan_preset_manifest_v1.json tests/orchestration/test_plan_presets.py
git commit -m "feat(orchestration): sealed fallback plan-preset registry + PRESET_FALLBACK materializer"
```

---

## Task 4: Planner budget scope + bounded generation loop

**Files:**
- Modify: `guanlan_v2/orchestration/orchestrator.py`
- Modify: `guanlan_v2/orchestration/budget.py` (additive only)
- Test: `tests/orchestration/test_planner_loop.py`

**Consumes:** Phase 2 `BudgetLedger`/`BudgetTransitionCommand`/`AuthoritativeClock`, `PromptAssembler`/`ModelGateway`/`AssembledModelRequest` ports, `PayloadStore`; Phase 1 `BudgetReservation` (`scope_type="planner"` already in `BudgetScopeType`, context.py:76-78), `validate_plan_draft`; Tasks 1–3.

**Produces:**

- Additive ledger extension in `budget.py`: `reserve_planner(*, request_id: NonEmptyStr, request_digest: DigestHex, attempt: PositiveInt, tokens: NonNegativeInt, llm_invocations: NonNegativeInt, idempotency_key: NonEmptyStr) -> BudgetReservation` plus the new closed command operation `"reserve_planner"`. This is Phase 7's **one sanctioned Phase 2-owned source touch** (mirroring Phase 4's `events.py` ruling): the closed `BudgetTransitionCommand` vocabulary lives in Phase 2's `budget.py` and a parallel Planner-side ledger would break the one-ledger constraint, so the new closed op lands there strictly additively; no other Phase 2 module is modified. The reservation binds `scope_type="planner"`, `scope_id=f"planner.attempt.{attempt}"`, `reserved_concurrency=1`, and — per Task 0 clause (b) — `candidate_plan_digest = request_digest` (the request's semantic digest is the only stable pre-candidate digest; the scope type disambiguates). If Phase 2's implemented closed-operation set is frozen by an exact-equality test, flip that assertion additively (absence → presence-in-phase7), following the Phase 4 guard-flip ruling; never weaken any other budget test.
- Internal carrier `class PlannerResult(NamedTuple):` — `record: PlannerRunRecord`, `record_ref: TypedPayloadRef` (`schema_ref` pinned to `PlannerRunRecord@1`), `draft: PlanDraft | None`, `report: PlanValidationReport | None`.
- `def run_planner(*, request: OrchestrationRequest, context: ContextSnapshot, context_snapshot_ref: PayloadRef, catalog_runtime: CatalogRuntime, schema_registry: SchemaRegistry, planner_spec: PlannerSpec, presets: PlanPresetRegistry, budget: BudgetLedger, prompt_assembler: PromptAssembler, model_gateway: ModelGateway, payload_store: PayloadStore, clock: AuthoritativeClock, run_id: NonEmptyStr, draft_id: LogicalId) -> PlannerResult:`

Loop discipline per attempt `k = 1..planner_spec.max_generation_attempts`:
1. `reserve_planner` (tokens = `attempt_token_reservation`, llm_invocations = 1); ledger exhaustion → attempt recorded `budget_rejected` and the loop terminates immediately to the fallback/halt branch (no free retry);
2. assemble the prompt through the Phase 2 `PromptAssembler`: trusted channel = planner system prompt/SKILL/guardrail material resolved via `catalog_runtime` (exact digest match), the request goal, remaining-budget figures, the catalog worker roster projection (worker id, lane, persona, input/output schema refs, params schema ref — a deterministic serialization of `dynamic_allowed` `final` workers only), and on attempts `k > 1` the previous attempt's canonical issue codes; untrusted channel = ordered `PromptUntrustedBlockRef` blocks for ContextSnapshot-derived narrative text. One `PromptAssemblyRecord` persists in `main` before invocation;
3. exactly one `model_gateway.invoke(request=assembled, prompt_assembly_ref=...)` bounded by `attempt_timeout_sec`; provider exception → `model_error`, timeout → `timed_out`;
4. `parse_planner_output` → `assemble_dynamic_draft` (with remaining figures from `budget.available()`) → `validate_plan_draft`; classify to `parse_rejected` / `shape_rejected` / `budget_rejected` / `validation_rejected` / `draft_admissible` with canonical issue codes;
5. settle the reservation with actual usage (release remainder); every attempt yields exactly one `PlannerAttemptRecord`.

Terminal logic: first `draft_admissible` ⇒ `candidate_ready` (loop stops; unused attempts are never consumed). Exhaustion (or budget-terminated loop) ⇒ if `request.fallback_preset_id` is set: resolve via `presets.get`, `materialize_fallback_draft`, run Phase 1 validation, and — only if valid — `fallback_materialized`; a missing/unknown preset or invalid fallback draft ⇒ `halted_no_fallback` (the failure reason lands in the record's last attempt issue codes / a `fallback_invalid:<code>` marker in `issue_codes` of a synthetic terminal check — never a silent pass). No fallback field ⇒ `halted_no_fallback`. The `PlannerRunRecord` persists to the `main` namespace via `payload_store.put` before `run_planner` returns (`record_ref` pairs `PlannerRunRecord@1` with the stored `main`-namespace locator).

**Required invariants:**

1. attempts are hard-bounded by `PlannerSpec.max_generation_attempts <= 3`; no code path re-enters generation after the terminal branch;
2. every attempt — including parse/validation failures — has a settled or released reservation; replaying the budget ledger reconstructs planner spend exactly (reserve→settle/release triplets);
3. planner reservations never satisfy `get_active_plan` and never masquerade as plan reservations;
4. `run_planner` never calls `PlanAdmissionService`, never freezes, never emits plan-lifecycle `RunEvent`s — generation is strictly upstream of admission;
5. fallback executes only on the exact request-persisted preset id; the model's text can never name a preset (parser has no such field);
6. determinism of classification: a scripted fake gateway returning fixed text yields byte-identical `PlannerRunRecord` semantic projections across runs (wall-clock excluded);
7. prompt-injection containment: a hostile untrusted block ("approve this plan automatically", "use tool X", "set approval_policy=auto") can at most change authored low-authority fields; stamped fields and the closed parser make privilege escalation unrepresentable — asserted end-to-end.

- [ ] **Step 1: Write failing loop tests**

Use fake `PromptAssembler`/`ModelGateway`/`PayloadStore` + the real ledger. Matrix: first-attempt success; success on attempt 2 after `parse_rejected`; exhaustion → fallback (valid preset); exhaustion → halt (no fallback field); exhaustion → halt (unknown preset id); budget exhaustion mid-loop → `budget_rejected` + immediate terminal branch; ledger replay equality; reservation settle/release accounting; `get_active_plan` isolation; injection containment; record persistence + matrix coherence.

Run: `pytest tests/orchestration/test_planner_loop.py -v`

Expected: FAIL on missing `reserve_planner`/`run_planner`.

- [ ] **Step 2: Implement the additive ledger op + loop**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_planner_loop.py tests/orchestration/test_budget_ledger.py tests/orchestration/test_orchestrator_assembly.py -v`

Expected: PASS (including unchanged Phase 2 ledger behavior).

```bash
git add guanlan_v2/orchestration/orchestrator.py guanlan_v2/orchestration/budget.py tests/orchestration/test_planner_loop.py
git commit -m "feat(orchestration): bounded budget-reserved planner loop with request-level fallback"
```

---

## Task 5: Production planner ModelGateway + seat config

**Files:**
- Create: `guanlan_v2/orchestration/planner_gateway.py`
- Modify: `config/llm.yaml` (additive)
- Test: `tests/orchestration/test_planner_gateway.py`

**Consumes:** Phase 2 `ModelGateway` port + `AssembledModelRequest`; engine `LLMClient.for_agent(agent_name, config_path)`; the repo-pinned config-path idiom (`guanlan_v2/screen/llm.py:22-23` — engine `find_config` prefers the pinned workspace `G:\financial-analyst\config\llm.yaml`, so the explicit repo path is mandatory).

**Produces:**

- `class PlannerLLMModelGateway:` implementing the Phase 2 `ModelGateway` protocol — `__init__(self, *, payload_reader, seat: str = "planner", config_path: Path | None = None, timeout_sec: float = 300.0)` where `config_path=None` resolves to the repo `config/llm.yaml` (never `find_config`). `invoke(request: AssembledModelRequest, *, prompt_assembly_ref: PayloadRef)`: resolves the persisted `PromptAssemblyRecord` via `payload_reader`, rehashes the exact canonical request bytes against the record's request digest (mismatch → refuse before any provider bytes), performs exactly one single-shot completion on the `planner` seat with JSON output requested, and returns the provider text + usage in the Phase 2 result shape. No tool loop, no second invocation, no detached-prompt entry point exists.
- Thread discipline: the gateway's provider call is synchronous; any invocation reached from the 9999 event loop must be wrapped in `asyncio.to_thread` by the caller (documented; the console wiring in Task 8 and any autonomy wiring obey the same red line — no sync HTTP inside the loop).
- `config/llm.yaml` additive entry under `agent_overrides`: `planner: {model: deepseek-reasoner, max_tokens: 8192, timeout: 300}` (deep tier — plan synthesis is a judgment-dense seam per the tier doctrine at llm.yaml:29-30).

**Required invariants:**

1. the gateway refuses a request whose bytes do not rehash to the persisted assembly record (forged/detached prompts impossible);
2. seat resolution always passes the explicit repo config path; a monkeypatched `find_config` proves the pinned-workspace shadow cannot capture the planner seat;
3. exactly one provider call per `invoke`; provider failure surfaces as a typed error for the loop's `model_error` classification, never a fabricated response;
4. no engine file is modified.

- [ ] **Step 1: Write failing gateway tests**

Fake `LLMClient` transport (no network): byte-rehash refusal; single-call accounting; explicit config path assertion; provider-error propagation; seat name `"planner"`.

Run: `pytest tests/orchestration/test_planner_gateway.py -v`

Expected: FAIL on missing module.

- [ ] **Step 2: Implement gateway + seat entry**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_planner_gateway.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/planner_gateway.py config/llm.yaml tests/orchestration/test_planner_gateway.py
git commit -m "feat(orchestration): production planner ModelGateway on pinned planner seat"
```

---

## Task 6: Plan diff typed payload + deterministic renderer

**Files:**
- Create: `guanlan_v2/orchestration/plan_diff.py`
- Test: `tests/orchestration/test_plan_diff.py`

**Consumes:** Phase 1 `PlanDraft.executable_projection()` (the 23 executable fields, spec.py:157-181), `canonical_json`, `content_digest`, `PlanSource`, `ApprovalPolicy`, `PayloadRef`, `TypedPayloadRef`.

**Produces:**

- `class PlanDiffEntry(DigestModel):` — `schema_version: Literal["1"] = "1"`, `pointer: NonEmptyStr` (an executable field name, or `nodes.<node_id>` for per-node granularity), `change: Literal["added","removed","changed"]`, `baseline_json: NonEmptyStr | None`, `candidate_json: NonEmptyStr | None` (canonical JSON strings). Matrix: `added` ⇒ baseline `None` + candidate set; `removed` ⇒ inverse; `changed` ⇒ both set and unequal.
- `class PlanDiff(DigestModel):` — `schema_version: Literal["1"] = "1"`, `baseline_kind: Literal["fallback_preset","prior_plan","none"]`, `request_digest: DigestHex`, `candidate_plan_digest: DigestHex`, `baseline_plan_digest: DigestHex | None`, `baseline_preset_id: LogicalId | None`, `entries: tuple[PlanDiffEntry, ...]` (sorted by `(pointer, change)`), `nodes_added: tuple[LogicalId, ...]`, `nodes_removed: tuple[LogicalId, ...]`, `nodes_changed: tuple[LogicalId, ...]` (each sorted). Matrix: `baseline_kind="none"` ⇒ no baseline digest/preset id and every entry `added`; `"fallback_preset"` ⇒ preset id set; `"prior_plan"` ⇒ baseline digest set.
- `class PendingPlanApproval(DigestModel):` — `schema_version: Literal["1"] = "1"`, `request_id: NonEmptyStr`, `candidate_plan_digest: DigestHex`, `goal: NonEmptyStr`, `source: PlanSource` (validator: `DYNAMIC` or `PRESET_FALLBACK`), `approval_policy: ApprovalPolicy` (validator: `REQUIRED` — a pending human card for AUTO is unconstructible), `node_count: PositiveInt`, `worker_ids: tuple[LogicalId, ...]` (sorted, unique), `budget_request_tokens: NonNegativeInt`, `budget_request_llm_invocations: NonNegativeInt`, `plan_diff_ref: TypedPayloadRef` (validator: `schema_ref == PlanDiff@1`), `rendered_md: str`, `rendered_from_diff_digest: DigestHex`, `planner_rationale: NonEmptyStr | None`, `candidate_id: NonEmptyStr`, `requested_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"candidate_id","requested_at"})`.
- `def build_plan_diff(candidate: PlanDraft, *, request: OrchestrationRequest, candidate_plan_digest: DigestHex, baseline: PlanDraft | None, baseline_kind: Literal["fallback_preset","prior_plan","none"], baseline_plan_digest: DigestHex | None = None, baseline_preset_id: LogicalId | None = None) -> PlanDiff:` — pure; diffs the two `executable_projection()` dicts field-by-field over canonical JSON; `nodes` diffed per node id.
- `def render_plan_diff_md(diff: PlanDiff) -> str:` — pure, deterministic markdown (stable section order; per-node param tables; digests rendered in full).
- `def build_pending_plan_approval(*, draft: PlanDraft, request: OrchestrationRequest, candidate_plan_digest: DigestHex, diff: PlanDiff, plan_diff_ref: TypedPayloadRef, planner_rationale: str | None, candidate_id: NonEmptyStr, requested_at: UtcDateTime) -> PendingPlanApproval:` — computes `rendered_md = render_plan_diff_md(diff)` and seals `rendered_from_diff_digest = diff.semantic_digest()`; verifies `plan_diff_ref.schema_ref == PlanDiff@1` and `plan_diff_ref.payload_ref.content_digest == diff.semantic_digest()`.

**Required invariants:**

1. diff and rendering are pure functions of frozen inputs — same drafts ⇒ byte-identical markdown;
2. any executable-field change between baseline and candidate appears in exactly one entry; audit-only fields (`id/run_id/request_id`, `context_snapshot_ref` locator) never appear;
3. `rendered_md` is verifiably bound to the diff payload (recompute + compare digest on load path in Task 7);
4. a reviewer-facing card for a DYNAMIC v1 draft always shows `approval_policy=required` (structural);
5. no diff path mutates or re-validates the drafts.

- [ ] **Step 1: Write failing diff tests**

Matrix: none-baseline (all-added) diff; preset-baseline diff showing node param change + node added; prior-plan baseline; entry/matrix violations; renderer determinism (double render equality); pending-approval builder digest binding + tamper detection (edited markdown fails); AUTO pending card unconstructible.

Run: `pytest tests/orchestration/test_plan_diff.py -v`

Expected: FAIL on missing module.

- [ ] **Step 2: Implement diff + renderer + builder**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_plan_diff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/plan_diff.py tests/orchestration/test_plan_diff.py
git commit -m "feat(orchestration): typed plan diff + deterministic reviewer rendering"
```

---

## Task 7: Durable approval journal + coordinator

**Files:**
- Create: `guanlan_v2/orchestration/approval.py`
- Test: `tests/orchestration/test_approval_store.py`

**Consumes:** Phase 1 `PlanApproval`/`ApprovalDecision`; Phase 2 `PlanAdmissionService.record_approval`/`freeze_and_admit_candidate` and `AuthoritativeClock`; Phase 3's fail-closed admin ports (`AdminReviewVerifier`, `AuthenticatedAdminPrincipal` — Task 0 clause (c)); Task 6 `PendingPlanApproval`.

**Produces:**

- Errors: `class ApprovalDecisionConflict(Exception):`, `class UnknownPendingCandidate(KeyError):`, `class ApprovalStoreCorrupt(Exception):`.
- Internal journal row (unregistered): `class ApprovalJournalRow(ContractModel)` — `row_kind: Literal["pending","decision"]`, `seq: PositiveInt`, `payload: dict[str, Any]` (the typed model's dump), `row_digest: DigestHex` (over `(row_kind, seq, payload)` canonical JSON).
- `class PlanApprovalCoordinator:` — `__init__(self, journal_path: Path, *, admission: PlanAdmissionService, clock: AuthoritativeClock, verifier, console_emit: Callable[[str, dict], None] | None = None)`:
  - `register_pending(pending: PendingPlanApproval, *, idempotency_key: NonEmptyStr) -> PendingPlanApproval` — appends a `pending` row (fsync before return), then emits console event `plan_approval_request` when `console_emit` is wired. Idempotent by `(request_id, candidate_plan_digest)`: identical re-register returns the stored card; different semantic content raises `ApprovalDecisionConflict`.
  - `list_pending() -> tuple[PendingPlanApproval, ...]` — undecided cards in journal order.
  - `decide(*, request_id: NonEmptyStr, candidate_plan_digest: DigestHex, decision: ApprovalDecision, actor, reason: NonEmptyStr | None, idempotency_key: NonEmptyStr) -> tuple[PlanApproval, RunEvent]` — verifier authenticates `actor` fail-closed (unverified → refusal, nothing persisted); unknown card → `UnknownPendingCandidate`; constructs the Phase 1 `PlanApproval(request_id=..., candidate_plan_digest=..., decision=..., actor_id=<verified id>, decided_at=clock.now(), reason=...)`; appends the `decision` row FIRST (durability before publication), then calls `admission.record_approval(...)` with a deterministic idempotency key derived from `(request_id, candidate_plan_digest, decision)`, then emits `plan_approval_resolved`. Exactly one terminal decision per candidate: identical replay returns the stored pair; a differing decision raises `ApprovalDecisionConflict`.
  - `load_decision(request_id: NonEmptyStr, candidate_plan_digest: DigestHex) -> PlanApproval | None` — the durable-decision read consumed by Task 8's `GET /plan/approvals/status` for idempotent re-reads after a pending card resolves.
  - `@classmethod replay(cls, journal_path: Path, *, admission, clock, verifier, console_emit=None) -> "PlanApprovalCoordinator"` — folds the journal; a decision row lacking its admission event (crash between append and `record_approval`) is re-submitted idempotently during replay; the `rendered_from_diff_digest` of each pending card is re-verified against its `plan_diff_ref.payload_ref.content_digest` on fold.
- `def admit_after_approval(*, admission: PlanAdmissionService, candidate_id: NonEmptyStr, reservation_id: NonEmptyStr, approval_event_id: NonEmptyStr, idempotency_key: NonEmptyStr) -> tuple[Plan, PlanAdmitted]:` — a thin pass-through to `admission.freeze_and_admit_candidate` (no wrapper semantics; exists so the console handler has one reviewed call site).
- Journal file: `var/orchestration/plan_approvals.jsonl`, append-only, one JSON row per line, fsync per append. A torn (non-JSON) final line is dropped with a logged warning badge; any earlier malformed row or digest mismatch → `ApprovalStoreCorrupt` hard failure (no silent skip, no partial trust).

**Required invariants:**

1. approval state survives process death: kill-after-append / kill-before-`record_approval` / kill-after-both all recover to exactly one terminal decision via `replay` (unlike the console confirm gate's in-memory futures);
2. every decision is digest-bound: a decision naming a digest with no matching pending card fails; a re-validated draft with a new digest requires a new pending card and a new decision;
3. the coordinator writes nothing into any `Plan` — approval identity lives only in the journal + admission `RunEvent`s;
4. there is no auto-resolve: pending cards have no timeout and never self-approve or self-reject (the 600s console confirm timeout pattern is explicitly NOT reused);
5. persist-then-publish: journal append strictly precedes the admission event, which strictly precedes the console emit; a console-emit failure never rolls back the decision;
6. the verifier is fail-closed: `verifier=None` or an unverifiable actor means no decision path exists.

- [ ] **Step 1: Write failing coordinator tests**

Use a fake admission service recording `record_approval` calls + a temp journal path. Matrix: register/list/decide happy path (APPROVED and REJECTED); idempotent replays; conflicting re-decide; unknown candidate; unverified actor refusal; crash-recovery trio (simulate by constructing a new coordinator from the journal at each cut point); torn-tail tolerance vs mid-file corruption hard-fail; rendered-md digest re-verification on fold; ordering (journal row exists even when admission raises — and replay resubmits).

Run: `pytest tests/orchestration/test_approval_store.py -v`

Expected: FAIL on missing `approval.py`.

- [ ] **Step 2: Implement journal + coordinator**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_approval_store.py tests/orchestration/test_plan_diff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/approval.py tests/orchestration/test_approval_store.py
git commit -m "feat(orchestration): durable digest-bound plan-approval journal + coordinator"
```

---

## Task 8: Console carrier — API endpoints, event stream, UI card

**Files:**
- Modify: `guanlan_v2/console/api.py` (additive only)
- Create: `ui/console/console-plan-approval-card.jsx`
- Modify: the existing console page mount file (per Task 0 clause (d)) — mount + `?v` bump via `Edit`
- Test: `tests/orchestration/test_plan_approval_console.py`

**Consumes:** existing `build_console_router(store=None, agent_factory=None, ...)` (api.py:410-411), `_emit`/SSE plumbing (api.py:431-441, 841-876), `ConsoleStore.append_event` (store.py:99); Task 7 coordinator; the `POST /confirm` precedent (api.py:827-838) as *idiom*, not as implementation.

**Produces:**

- Additive `build_console_router` keyword: `plan_approval_coordinator: PlanApprovalCoordinator | None = None` (name mirrored to the Phase 3-implemented verifier-injection style per Task 0 clause (c)). When `None`, every plan-approval endpoint returns an honest 503 `{"ok": false, "reason": "plan approval surface not wired"}` — fail-closed, never fake-empty-success.
- `GET /plan/approvals` — returns `{"ok": true, "items": [...]}` where each item is the `PendingPlanApproval` public JSON (goal, source, digest, worker ids, budget request, the typed `plan_diff_ref` — a `TypedPayloadRef` naming `PlanDiff@1`, `rendered_md`, rationale, requested_at). Coordinator access wrapped in `asyncio.to_thread` (journal I/O off the 9999 loop — watchdog red line).
- `GET /plan/approvals/status` — query `request_id` + `candidate_plan_digest`; returns the durable decision via the coordinator's `load_decision` (in `to_thread`): decided → `{"ok": true, "decision": ..., "actor_id": ..., "decided_at": ...}` (idempotent re-read after the pending card resolves); registered-but-undecided → `{"ok": true, "decision": null, "pending": true}`; unknown pair → 404.
- `POST /plan/approvals/decide` — body `{request_id, candidate_plan_digest, decision: "approved"|"rejected", reason?}`; actor material passes to the coordinator's fail-closed verifier; on success returns `{"ok": true, "decision": ..., "candidate_plan_digest": ...}` and, for APPROVED candidates, subsequently invokes `admit_after_approval` (also in `to_thread`) so the admitted `Plan`/`PlanAdmitted` pair exists before the response reports `"admitted": true`. Conflict → 409 with the stored decision; unknown candidate → 404; unverified actor → 403. All error bodies honest and typed.
- Console event mirroring into a reserved console session `plan-approvals` (created idempotently through `ConsoleStore`): event `plan_approval_request` `{request_id, candidate_plan_digest, goal, source, node_count, rendered_md}` and `plan_approval_resolved` `{request_id, candidate_plan_digest, decision, actor_id, reason}` — new event *types* in events.jsonl/SSE; existing `confirm_request`/`confirm_resolved` semantics untouched (the turn-scoped, digest-free confirm gate is never reused for plan approval).
- `ui/console/console-plan-approval-card.jsx` — follows the `console-report-card.jsx`/ResearchLoopCard idiom (fold/poll/cleanup; components are copied per page — no cross-page import): collapsed header shows pending count; expanded list shows per-candidate goal, `source` badge (`动态` / `preset回落` — degradation displayed, never hidden), node/worker summary, full digest (monospace, copyable), diff `rendered_md` in a scrollable `<pre>`, labeled untrusted rationale, and 批准/拒绝 buttons POSTing to `/plan/approvals/decide` with a confirm dialog that repeats the digest. Polls `GET /plan/approvals` every 60s while open. Mounted beside the existing report card on the existing console surface; no new page, no navigation change.

**Required invariants:**

1. every endpoint is additive — existing console routes, event types, and the confirm gate behave byte-identically (regression: existing console tests stay green);
2. no synchronous journal/LLM/HTTP I/O executes on the event loop;
3. the decide endpoint cannot approve a digest that is not a registered pending card, cannot double-decide, and cannot bypass the verifier;
4. SSE subscribers on the `plan-approvals` session observe request→resolved pairs in order; events.jsonl is the durable console-side record (the orchestration journal remains the approval authority);
5. UI displays only server truth: no client-side approval state, no optimistic "approved" rendering before the 2xx response.

- [ ] **Step 1: Write failing endpoint tests**

FastAPI `TestClient` over `build_console_router(store=<tmp ConsoleStore>, plan_approval_coordinator=<real coordinator with fake admission + fake verifier>)`. Matrix: unwired 503; list happy path; decide approve (journal row + admission call + `admitted: true` + console events emitted in order); decide reject (reservation-release path delegated to admission); 404/409/403; status endpoint decided/pending/unknown triple (decided candidates re-readable via `load_decision` after resolution); existing console route regression (e.g. `/confirm` untouched); event payload digest fields present.

Run: `pytest tests/orchestration/test_plan_approval_console.py -v`

Expected: FAIL on missing endpoints/keyword.

- [ ] **Step 2: Implement endpoints + events; author the card; mount + bump `?v`**

- [ ] **Step 3: Run, verify visually, and commit**

Run: `pytest tests/orchestration/test_plan_approval_console.py tests/orchestration/test_approval_store.py -v`

Expected: PASS. Browser verification of the card (server restart on 9999 required to load the new backend) is recorded as an Execution Handoff checkpoint artifact, not a pytest gate.

```bash
git add guanlan_v2/console/api.py ui/console/console-plan-approval-card.jsx tests/orchestration/test_plan_approval_console.py
git commit -m "feat(console): digest-bound plan-approval card + decide endpoint on existing console surface"
```

(Include the mount-file edit in the same commit's pathspec once Task 0 clause (d) resolves the exact file.)

---

## Task 9: Phase 7 registry/catalog chain + planner materials + goldens

**Files:**
- Create: `guanlan_v2/orchestration/phase7_registry.py`
- Create: `config/orchestration/materials/planner/` (system prompt, `SKILL.md`, guardrail)
- Create: `tests/orchestration/golden/phase7_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase7_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase7_registry.py`

**Consumes:** Phase 6 chain nodes (`PHASE6_REGISTRY_DIGEST`/`PHASE6_CATALOG_DIGEST` + builders); Phase 1 `SchemaRegistry`, `build_catalog_snapshot`, `parse_skill_v1`, `catalog_material_digest`, `ContentManifestEntry`/`SkillManifest`/`ResolvedTextMaterial`; Tasks 1/3/6 public models.

**Produces:**

- `PHASE7_PUBLIC_MODELS` — exactly: `PlannerSpec`, `PlannerAttemptRecord`, `PlannerRunRecord`, `PlanPresetRecord`, `PlanDiffEntry`, `PlanDiff`, `PendingPlanApproval` (7 models).
- `PHASE7_INTERNAL_MODELS` — reviewed-reason map for `PlannerDraftEnvelope` (+ node/dependency envelopes), `PlannerResult`, `ApprovalJournalRow` (value carriers / recovery rows, never cross-boundary payloads).
- `def build_phase7_registry(expected_phase6_digest: DigestHex) -> SchemaRegistry:` — verifies the supplied digest equals `PHASE6_REGISTRY_DIGEST`, builds a fresh sealed cumulative registry = Phase 6 public models + `PHASE7_PUBLIC_MODELS`; inherited entries byte-identical. `PHASE7_REGISTRY_DIGEST: DigestHex` frozen constant.
- `def build_phase7_catalog_snapshot(phase6_snapshot: WorkerCatalogSnapshot, *, planner_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot:` — rejects any base other than `PHASE6_CATALOG_DIGEST`; adds ONLY the planner content/skill/guardrail manifest entries (source_identity `orchestrator.planner`; prompt kind `"prompt"`, skill via skill-v1 grammar — frontmatter + `Perfect for:` canonical-JSON trigger line + the exact `## ⚠️ CRITICAL: Data Source Priority` heading, guardrail kind `"guardrail"`). It adds **no `WorkerSpec`**: the Planner is deliberately not a selectable worker (a `final` spec would be `dynamic_allowed` and could recursively select itself; materials-without-worker is the reviewed containment). `PHASE7_CATALOG_DIGEST: DigestHex` frozen constant.
- `def build_phase7_planner_spec(catalog: WorkerCatalogSnapshot) -> PlannerSpec:` — derives `system_prompt_ref`/`skills`/`guardrail_refs` from the Phase 7 catalog manifests by exact id/version/digest (never a path), `model_tier="reasoner_deep"` (reviewed deviation from spec §10, which reserves `reasoner_deep` for the `dec.pm` seat: the Planner is not a lane worker, and plan-shaping quality dominates its once-per-run cost), `max_generation_attempts=2`, reviewed `attempt_token_reservation`. Its semantic digest is frozen as `planner_spec_digest` inside `phase7_catalog_manifest_v1.json`.
- Goldens: `phase7_schema_manifest_v1.json` (registry digest + per-schema digests, base = exact Phase 6 digest recorded) and `phase7_catalog_manifest_v1.json` (catalog digest, base digest, planner material digests, `planner_spec_digest`). Neither is auto-regenerated; upstream goldens untouched.

**Required invariants:**

1. chain linearity: `build_phase7_registry` refuses any digest other than the exact Phase 6 digest; no "latest" alias anywhere;
2. inherited schema JSON is byte-identical to every upstream manifest entry (walk all phases);
3. the Phase 7 catalog contains zero new workers and `count_final_workers` is unchanged from Phase 6; every planner material resolves with matching kind + digest;
4. `parse_skill_v1` accepts the planner `SKILL.md` byte-for-byte (canonical-JSON trigger arrays; UTF-8 no BOM);
5. new dynamic Plans bind `PHASE7_CATALOG_DIGEST` + `PHASE7_REGISTRY_DIGEST`; older digests remain resolvable for replay and are never rebound;
6. no `EventType` member was added and no Phase 1 absence/deferred-payload guard was touched (the `DecisionSchedule` guard belongs to Phase 6; nothing pins Phase 7 models).

- [ ] **Step 1: Write failing chain tests**

Matrix: wrong-base refusal; registry population exactness (7 new publics; internal/public partition disjoint + exhaustive for Phase 7 modules); byte-identical inheritance sweep; golden equality; catalog no-new-worker + material resolution; skill grammar acceptance; planner-spec derivation determinism + frozen digest; EventType frozen-set regression.

Run: `pytest tests/orchestration/test_phase7_registry.py -v`

Expected: FAIL on missing module/goldens.

- [ ] **Step 2: Implement chain module; author reviewed planner materials; freeze both goldens**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase7_registry.py tests/orchestration/test_phase7_handoff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/phase7_registry.py config/orchestration/materials/planner tests/orchestration/golden/phase7_schema_manifest_v1.json tests/orchestration/golden/phase7_catalog_manifest_v1.json tests/orchestration/test_phase7_registry.py
git commit -m "feat(orchestration): phase7 cumulative registry/catalog chain + catalog-owned planner materials"
```

---

## Task 10: End-to-end dynamic admission + red-line regression

**Files:**
- Test: `tests/orchestration/test_dynamic_e2e.py`

**Consumes:** everything above + Phase 2 `PlanAdmissionService`, `run_plan`, in-memory stores, fake `ModelGateway` (scripted planner JSON) and fake worker model gateway for the pilot triad.

**Produces:** the executable proof of the full Phase 7 sequence, no new source code. Scenarios:

1. **Dynamic happy path:** persisted `OrchestrationRequest(workflow="orchestrate_only", approval_policy=REQUIRED)` + Phase 5-style frozen ContextSnapshot → `run_planner` (scripted valid output) → `PlannerRunRecord.terminal_outcome="candidate_ready"` → `prepare_candidate`/`persist_and_reserve_candidate` → `build_plan_diff(baseline_kind="fallback_preset")` + `build_pending_plan_approval` → coordinator `register_pending` → console decide endpoint APPROVED → `record_approval` RunEvent (`PLAN_APPROVED`) → `admit_after_approval` → `verify_for_dispatch` → Phase 2 `run_plan` executes the DYNAMIC main DAG to `RunResult(completed)`. Assert: `Plan.plan_digest == candidate digest`, plan carries zero approver fields, budget ledger replay shows planner + plan + node reservations on ONE ledger.
2. **Fallback path:** scripted garbage model output ×2 attempts, request carries `fallback_preset_id="main.research_baseline"` → `fallback_materialized` → PRESET_FALLBACK draft admitted through the same approval carrier (source badge `preset_fallback` on the pending card) → executes.
3. **Honest halt:** same garbage, no fallback field → `halted_no_fallback`; no draft, no candidate, no reservation leak (planner reservations settled/released; ledger availability restored).
4. **Red lines:**
   - an unapproved dynamic candidate never reaches `freeze_and_admit_candidate` (missing approval event ⇒ admission refuses; `run_plan` unreachable);
   - `approval_policy=AUTO` on the request ⇒ the assembled draft fails Phase 1 validation for every source (no Phase 7 bypass);
   - a REJECTED decision releases the plan reservation and the candidate can never be admitted afterward;
   - editing any executable field after approval yields a new digest and the old `PlanApproval.authorizes_freeze` returns False (re-approval mandatory);
   - the Planner cannot schedule a `compat.*` worker or itself (`orchestrator.planner` is not a catalog worker id);
   - approval journal replay after simulated process death still admits exactly once (idempotent resubmission);
   - no Planner path writes memory/skill/code or emits an order/signal (import + capability sweep: `orchestrator.py`/`plan_presets.py`/`plan_diff.py`/`approval.py` import no seats/trade/memory-write modules).

**Required invariants:** every scenario runs on in-memory Phase 2 stores + temp journal; zero network; deterministic; the suite plus all previous Phase 7 suites and the full `tests/orchestration` tree pass together.

- [ ] **Step 1: Write failing e2e tests** (red = scenarios fail while any wiring gap remains)

Run: `pytest tests/orchestration/test_dynamic_e2e.py -v`

- [ ] **Step 2: Fix wiring gaps only (no new contracts)**

- [ ] **Step 3: Run the full tree and commit**

Run: `pytest tests/orchestration -v` and `python -m compileall -q guanlan_v2/orchestration`. If Ruff is available: `ruff check guanlan_v2/orchestration tests/orchestration`.

Expected: PASS across Phases 1–7 suites.

```bash
git add tests/orchestration/test_dynamic_e2e.py
git commit -m "test(orchestration): dynamic planner end-to-end admission + red-line regression"
```

---

## Phase 7 Exit Gates

Phase 7 is complete only when every gate below is checked by tests or a reviewed artifact.

### Upstream handoff and chain

- [ ] every Phase 1–6 Exit Gate remains green; no upstream source/test/golden overwritten;
- [ ] `test_phase7_handoff.py` proves the consumed ABI (validator/freeze/admission/ledger/chain) against implemented code;
- [ ] `PHASE7_REGISTRY_DIGEST`/`build_phase7_registry(expected_phase6_digest)` and `PHASE7_CATALOG_DIGEST`/`build_phase7_catalog_snapshot(...)` exist, refuse wrong bases, inherit byte-identically, and have their own reviewed goldens;
- [ ] no new `EventType` member; no Phase 1 guard flipped; no "latest" alias.

### Planner generation

- [ ] the Planner authors only the closed low-authority field set; every reserved/authority field key is parser-rejected with a canonical issue code;
- [ ] hidden-authority params keys (`handler/system_prompt/skills/tools/mcp/path`) are rejected at any depth;
- [ ] assembled drafts are plain Phase 1 `PlanDraft(phase="main", source=DYNAMIC)` accepted/rejected by the unmodified Phase 1 validator; `approval_policy` is copied from the request and the Planner cannot select `AUTO`;
- [ ] the Planner is not a catalog worker and cannot select itself or any `compat.*`/non-`dynamic_allowed` worker;
- [ ] attempts are bounded (≤3), each with a `scope_type="planner"` reservation on the single run ledger, settled/released honestly; ledger replay reconstructs planner spend; planner reservations never satisfy `get_active_plan`;
- [ ] prompt assembly persists one `PromptAssemblyRecord` per attempt with ContextSnapshot narrative confined to untrusted blocks; injection cannot escalate authority;
- [ ] `PlannerRunRecord` persists with a coherent terminal outcome for success/fallback/halt.

### Fallback and honest termination

- [ ] only the pre-persisted `OrchestrationRequest.fallback_preset_id` can materialize a fallback; no model/runtime-chosen preset path exists in code;
- [ ] the preset registry is sealed and golden-frozen; unknown/invalid preset ⇒ `halted_no_fallback`, never a substitute;
- [ ] fallback drafts are `PRESET_FALLBACK`, final-workers-only, validated by Phase 1, and still require the same REQUIRED approval.

### Approval carrier

- [ ] `PlanApproval` recording is durable: journal survives restart, replay yields exactly one terminal decision per `(request_id, candidate_plan_digest)`, crash at any cut point recovers idempotently;
- [ ] every decision is digest-bound; a changed draft requires a new approval; the stored decision feeds Phase 2 `record_approval` and `freeze_plan` consumes only `authorizes_freeze`-passing approvals;
- [ ] no `approved_by/at` field exists on `Plan` (structural test); approval events + journal are the sole source of truth;
- [ ] no auto-resolve/timeout decides a plan; the verifier is fail-closed; the console confirm gate is untouched and unused for plans;
- [ ] console endpoints are additive, honest on error (503/403/404/409), and off-loop for I/O; SSE mirrors request/resolved pairs in order;
- [ ] the reviewer card rides the existing console page (no new page), shows source/degradation badges, full digest, typed-diff `rendered_md` bound to the diff payload digest, and labeled untrusted rationale — reviewed screenshot artifact after 9999 restart.

### Plan diff

- [ ] `PlanDiff`/`PlanDiffEntry` cover exactly the executable projection (audit locators excluded); build + render are deterministic pure functions; tampered markdown is detected via `rendered_from_diff_digest`.

### End-to-end and red lines

- [ ] dynamic happy path, fallback path and honest halt all pass on in-memory stores with zero network;
- [ ] unapproved dynamic plans never execute; AUTO fails for every source; rejection releases budget; re-approval required after any digest change;
- [ ] the full `tests/orchestration` tree passes; `compileall` clean.

### Scope protection

- [ ] no second validate/freeze/digest/admission path, no new runner, no runtime-profile change, no debate/gate/reducer support added (Phase 8), no BOOTSTRAP generation (Phase 5 owns it), no trading/order/signal authority anywhere;
- [ ] `workflow/executor.run_graph` and the legacy engine are unchanged; `config/llm.yaml` change is additive only; the only Phase 2-owned source touched is `guanlan_v2/orchestration/budget.py` (Task 4's sanctioned additive `reserve_planner`);
- [ ] unrelated worktree changes are not staged (explicit pathspec commits verified in `git log --stat`).

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — exact imported upstream ABI/goldens and all seven correction clauses resolved against implemented code;
2. after Tasks 1–2 — closed authored grammar + runtime-stamped assembly accepted by the unmodified Phase 1 validator;
3. after Tasks 3–4 — sealed preset golden, bounded budget-reserved loop, fallback/halt terminal honesty;
4. after Task 5 — pinned planner seat + byte-rehash gateway (no engine changes);
5. after Tasks 6–7 — deterministic diff/render and durable digest-bound approval journal with crash-recovery evidence;
6. after Task 8 — additive console carrier; restart 9999 and record the reviewed card screenshot (真机);
7. after Tasks 9–10 — chain goldens, full-tree green, all Exit Gates checked with test evidence.

Do not begin Phase 8's dynamic-selection batches until every Phase 7 Exit Gate has test evidence. No execution method requires a particular optional skill package.
