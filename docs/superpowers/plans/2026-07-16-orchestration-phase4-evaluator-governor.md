# Orchestration Phase 4 · Evaluator-Optimizer / Governor Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the trial contracts, the event-vocabulary flip, the sealed-holdout stack, the optimize state machine and the closing research-adapter regression. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Generalize the proven `research/loop.run_research_loop` state machine into a contract-governed Evaluator-Optimizer: strict Trial/Holdout/Study contracts (the Phase 1 Task 11 landing), a four-layer evaluator (L0 honesty gate → L1 deterministic validation metrics → L2 overfitting governance → L3 attribution feedback), a governor that derives study families from canonical digests and extracts the repo's existing overfitting statistics as pure functions, an event-sourced cross-run `TrialLedger` over the Phase 2 stores, a capability-isolated sealed result store with a one-shot `(family_id, holdout_window_id)` lease, and `run_optimize`/`finalize_candidate` with persisted `WAITING_FOR_MATURITY` idempotent wakeup. Close with an adapter regression proving the existing factor-research loop's behavior is preserved through the new state machine. **Draft-only products, human review for adoption, and zero LLM trading authority throughout; sealed holdout metrics never reach `improve`, L3, memory or the public pool.**

**Architecture:** Phase 1 (as amended by `docs/superpowers/plans/2026-07-16-orchestration-phase1-amendment.md`) remains the sole owner of canonical JSON, digests, `DigestModel`, refs (`PayloadRef`, composite `TypedPayloadRef`), the schema registry, `WorkerSpec`/catalog, Plan validation/freeze and the `RunEvent` model. Phase 2 owns the stores (`PayloadStore`, `EventStore`, `RuntimeStateCellStore`, `RuntimeUnitOfWork`, `BudgetLedger`, `AuthoritativeClock`); Phase 3 owns data/PIT and the memory facade. Phase 4 adds contracts and services around those: `trial.py` (contracts + Phase 4 registry/catalog chain), `governor.py` (pure statistics + family identity), `trial_ledger.py` (event-sourced ledger), `sealed.py` (sealed store/gateway/lease), `evaluator.py` (L0–L3), `optimize.py` (state machine). The only permitted Phase 1 source touch is the additive `EventType` extension in `events.py` plus the two absence-guard test flips mandated by the controller CRIB. Existing `workflow/executor.run_graph`, `research/loop.py` runtime behavior and the legacy engine stay unchanged except the two named pure-function extractions from `workflow/api.py`, which leave exact-behavior delegates behind.

**Tech Stack:** Python ≥3.11, Pydantic v2, `pytest`; `numpy` inside `governor.py` only (heavier `strategy.compute.cpcv` imports are function-local). All new modules `from __future__ import annotations`. Depends on Phase 1 contracts in `guanlan_v2/orchestration/` and the Phase 2/3 runtime/data modules as implemented.

## Global Constraints

These extend, and never override, the Phase 1 (amended), Phase 2 and Phase 3 Global Constraints and Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import Phase 1 models/builders (`DigestModel`, `content_digest`, `PayloadRef`, `TypedPayloadRef`, `SchemaRef`, `ContentRef`, `ExperimentStatus`, `Confidence`, `RunEvent.build`, catalog/spec types) and Phase 2/3 services from their owning modules. Phase 4 must not redefine canonical JSON, digests, WorkerSpec, catalog snapshot, schema registry, Artifact, RunEvent semantics, Plan validation/freeze, `PitGuard` or the memory facade.
- **Typed evidence pairs are `TypedPayloadRef(schema_ref, payload_ref)`;** plain `PayloadRef` is used only as a bare storage locator. Every candidate/split/window/curve reference that carries schema meaning uses `TypedPayloadRef`.
- **Frozen vocabulary.** `StudySpec`, `StudyFamily`, `HoldoutWindow`, `TrialRecord`, `OptimizeRunState`, `OptimizeResult`, `HoldoutReceipt`, `HoldoutLease`, `SealedEvaluationRecord`, `SealedCapability`, `TrialLedger`, `SealedResultStore`, `SealedEvaluatorGateway`, `run_optimize`, `finalize_candidate`, `WAITING_FOR_MATURITY` are used verbatim; spec §8 field spellings are kept while types upgrade to Phase 1 strict types.
- **Registry/catalog chain.** Phase 4 extends exactly `PHASE3_FULL_REGISTRY_DIGEST`/`build_phase3_full_registry` and `PHASE3_FULL_CATALOG_DIGEST`/`build_phase3_full_catalog` outputs, exporting `PHASE4_REGISTRY_DIGEST` + `build_phase4_registry(expected_phase3_full_digest)` and `PHASE4_CATALOG_DIGEST` + `build_phase4_catalog_snapshot(...)` with their own golden manifests. Inherited schemas stay byte-identical; no upstream golden is regenerated; no "latest" alias exists.
- **Sealed isolation is structural.** Detailed holdout metrics/curves exist only as `sealed`-namespace payloads readable through `SealedResultStore.get` with a valid `SealedCapability`. Public events carry only `HoldoutReceipt`. `optimize.py` and `evaluator.py` never import `SealedEvaluationRecord` or `SealedResultStore`; tests assert this by module inspection.
- **One-shot holdout.** Each `(family_id, holdout_window_id)` admits exactly one candidate, reserved atomically before any data read; `revealed`, `failed`, `timed_out` and `inconclusive` all terminate the lease; recovery returns the original receipt and never reopens.
- **Family identity is governor-derived.** `family_id` comes only from the domain-tagged digest of `objective_digest/label_digest/universe_digest/frequency/split_policy_digest`; caller display names/free text never enter identity; renaming cannot reset trial/holdout budgets; lineage requires `parent_family_id` + `change_reason` + verifiable governor attestation.
- **Honesty over scores.** L2 returns `status="unavailable"` with reasons instead of fabricating governance numbers; L1 metrics use `None` for honestly-absent values, never zero-filled; L3 returns `ambiguous=True` rather than forcing attribution; the optimizer terminates honestly on propose/improve failure and on stall, never a template fallback.
- **Draft-only, propose-never-write.** Every research product routes to draft status for human review; nothing auto-promotes; workers/optimizer never modify code, prompts, skills or guardrails and hold no holdout reader capability.
- **PIT discipline.** All maturity/window reasoning uses `available_at <= as_of`/`matured_at` semantics; `FutureDataRefused` never falls through; holdout windows must be non-overlapping, already-matured OOT data.
- **Event discipline.** Ledger and state changes are persist-then-publish through the Phase 2 `EventStore`/`RuntimeUnitOfWork` with strict idempotency (`IdempotencyConflict` on same-key/different-content); Trial events follow the additive per-type payload rules in Task 3; reservation writes fail loudly (no best-effort bool swallowing).
- **Executable red/green checkpoints.** Every step named "Write failing … tests" immediately runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation; collection errors do not count as red. The later PASS step reruns the same focused tests plus listed upstream regressions.
- **Explicit pathspec commits only** (shared branch with concurrent sessions); never `git add -A`. No placeholders, DRY, YAGNI, TDD. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 4 work starts only after the Phase 1 (amended), Phase 2 and Phase 3 Exit Gates pass. Add `tests/orchestration/test_phase4_handoff.py` as an executable consumer test rather than copying upstream assertions.

**Files:**
- Create: `tests/orchestration/test_phase4_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. the Phase 1 amended golden (`schema_manifest_v1.json`, 11 registered schemas incl. `TypedPayloadRef@1`, `InputArtifactBinding@1`, `ContextRuntimeRequirements@1`) and digest golden vectors pass; `default_registry()` is sealed;
2. `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)` resolves from `guanlan_v2.orchestration.refs` and plain `PayloadRef` is unchanged;
3. `ExperimentStatus` (with `WAITING_FOR_MATURITY`, `PASSED_VALIDATION`, `SEALED_EVALUATING`) resolves from `enums.py`; `EventType.EXPERIMENT_STATE_CHANGED` exists while `TrialReserved`/`TrialRevealed`/`TrialExhausted` are still absent (this assertion is deleted by Task 3's flip and its removal is part of that task's diff);
4. Phase 2 exports resolve: `PayloadStore.put/get`, `EventStore.append/journal/visible`, `RuntimeStateCellStore` + `StateCellCompareAndSwapCommand`, `RuntimeUnitOfWork`, `IdempotencyConflict`, `AuthoritativeClock`, `EventRefusalAuditSink`; `refs.py` payload namespaces include `"sealed"` with `PUBLIC_PAYLOAD_NAMESPACES == frozenset({"main"})`;
5. Phase 3 exports resolve: `PHASE3_FULL_REGISTRY_DIGEST`, `build_phase3_full_registry`, `PHASE3_FULL_CATALOG_DIGEST`, `build_phase3_full_catalog`, `PHASE3_MEMORY_STATE_CELL_NAMESPACES`, `FutureDataRefused`;
6. reuse surface facts still hold: `guanlan_v2.research.loop.run_research_loop` signature `(run_id, goal, max_rounds, min_rank_ic, universe, freq, start, end, progress)`; `loop._gate`, `loop.GATE_OOS_OK == "robust"`, `loop._EVAL_OOS_FRAC == 0.3`; `workflow.executor.run_graph(graph, overrides=None, on_node=None, prefer_model_terminal=False)` and `metrics_of_terminal` six-key shape; `workflow.api._cscv_pbo` and `workflow.api._oos_verdict` exist; `strategy.compute.cpcv.deflated_sharpe(returns, n_trials, sharpes_std=None)` and `make_splits(dates, n_groups=6, k=2, purge=5, embargo=5)` exist; `screen.factor_vintage.cs_vintage_from_frame` exists; `factorlib.api._VALID_SAVE_STATUS == {"", "draft"}` (no `"rejected"` status is assumed anywhere in this plan);
7. the Phase 1 amendment's evidence surface holds: `Provenance.data_result_refs`/`execution_evidence_refs` are `tuple[TypedPayloadRef, ...]` and `ContextSnapshot` carries `memory_snapshot_ref: TypedPayloadRef` + `runtime_requirements_ref` (pre-amendment field names must not appear anywhere in Phase 4 code);
8. no Phase 4 source/test path overwrites a Phase 1/2/3-owned module, test or golden file other than the Task 3 additive `events.py` extension and its two named guard-test flips.

**Task 0 correction clauses** (binding on every later task):

- If an exact field, builder or export name differs in the implemented Phase 2/3 public API (e.g. store method keywords, state-cell namespace constant, catalog builder signature), update this plan to that reviewed API before writing runtime code; do not invent an adapter with parallel semantics.
- The literal values of `PHASE3_FULL_REGISTRY_DIGEST`/`PHASE3_FULL_CATALOG_DIGEST` are unknowable until Phase 3 lands; Task 9 freezes its goldens against the reviewed implemented values, never against guesses.
- The absence-guard tests are flipped by mechanism ("absent" → "absent from Phase 1 `default_registry()` AND present in the Phase 4 registry"), not by line number; if the amendment moved them, locate them by test name (`test_event_type_set_excludes_trial_and_holdout`, `test_no_trial_or_holdout_type_registered`, `test_no_trial_or_holdout_type_classified_or_defined`) and apply the same transformation.
- If the implemented Phase 2 `EventStore` requires event-type registration beyond the enum (e.g. a per-type visibility table), extend that reviewed mechanism additively instead of the validator sketch in Task 3.
- `TrialLedger.effective_trial_stats` returns `dict` per the frozen spec signature; if review prefers a typed model, add it as a new registered contract in a follow-up amendment rather than silently changing the signature.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture**

Record only exact digests and exported symbol signatures (Phase 1 amended registry digest, `PHASE3_FULL_*` digests, reuse-surface function signatures); never record local paths or mutable singleton identities.

- [ ] **Step 3: Run the upstream suites and the frozen handoff gate**

Run from a repository state in which no Phase 4 module exists yet: `pytest tests/orchestration -v` and `pytest tests/test_research_loop.py -v`.

Expected: every upstream test plus `test_phase4_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase4_handoff.py
git commit -m "test(orchestration): gate phase4 on phase1-3 contracts and reuse surface"
```

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/trial.py` | Trial/Holdout/Study strict contracts (Phase 1 Task 11 landing) + `PHASE4_PUBLIC_MODELS`/`PHASE4_INTERNAL_MODELS` + Phase 4 registry/catalog chain exports |
| `guanlan_v2/orchestration/governor.py` | pure overfitting statistics (extracted `cscv_pbo`/`oos_verdict`, DSR wiring, `effective_n_trials`, complexity), study-family derivation + attestation, `Governor` (L2) |
| `guanlan_v2/orchestration/trial_ledger.py` | event-sourced cross-run `TrialLedger` over Phase 2 stores + holdout-window registry + Phase 4 state-cell namespaces |
| `guanlan_v2/orchestration/sealed.py` | `SealedResultStore`, `SealedEvaluatorGateway`, one-shot `HoldoutLease` issuance/verification, `SealedCapability` verification |
| `guanlan_v2/orchestration/evaluator.py` | four layers: L0 honesty gates, L1 metrics normalizer, L2 delegate, L3 attribution port + honest ambiguity |
| `guanlan_v2/orchestration/optimize.py` | `run_optimize`/`resume_optimize`/`finalize_candidate`, `MaturityPending`, round/experiment persistence ports |
| `guanlan_v2/research/optimize_adapter.py` | factor-research adapter binding the new state machine to the existing loop's seams (closing regression) |
| `guanlan_v2/workflow/api.py` (modified) | `_cscv_pbo`/`_oos_verdict` become exact-behavior delegates to `governor.py` |
| `guanlan_v2/orchestration/events.py` (modified, additive only) | +3 `EventType` members and per-type Trial payload rules |
| `config/orchestration/materials/optimize/joint_gate_v1.md` | reviewed `gate_metric` material describing the research joint gate |
| `tests/orchestration/golden/phase4_schema_manifest_v1.json` | Phase 4 cumulative registry golden (separate from all upstream goldens) |
| `tests/orchestration/golden/phase4_catalog_manifest_v1.json` | Phase 4 cumulative catalog golden |
| `tests/orchestration/test_phase4_handoff.py` | executable Phase 1/2/3 → 4 ABI/golden gate |
| `tests/orchestration/test_trial_contracts.py` | Task 1+2 contract tests |
| `tests/orchestration/test_trial_events.py` | Task 3 additive event vocabulary tests |
| `tests/orchestration/test_governor.py` | Task 4 pure-statistics and family-identity tests |
| `tests/orchestration/test_trial_ledger.py` | Task 5 ledger tests |
| `tests/orchestration/test_sealed_holdout.py` | Task 6 sealed store/gateway/lease tests |
| `tests/orchestration/test_evaluator.py` | Task 7 four-layer tests |
| `tests/orchestration/test_optimize.py` | Task 8 state-machine tests |
| `tests/orchestration/test_phase4_registry.py` | Task 9 registry/catalog chain tests |
| `tests/test_research_optimize_adapter.py` | Task 10 adapter regression (beside `tests/test_research_loop.py`, reusing its seam pattern) |

## Contract Inventory (authoritative for Tasks 1, 2 and 9)

| Contract | Module | Registered as | Origin of name |
|---|---|---|---|
| `StudySpec` | `trial.py` | `StudySpec@1` | spec §8 (fields spec:839-843) |
| `StudyFamily` | `trial.py` | `StudyFamily@1` | spec §8 (fields spec:844-849) |
| `SplitSpec` | `trial.py` | `SplitSpec@1` | Phase 4-defined (spec names only `split_spec`) |
| `OptimizeCandidate` | `trial.py` | `OptimizeCandidate@1` | Phase 4-defined (spec placeholder `Cand`) |
| `ValidationMetrics` | `trial.py` | `ValidationMetrics@1` | Phase 4-defined (spec placeholder `Metrics`) |
| `GateResult` (evaluation gate) | `trial.py` | — internal, nested in `OptimizeRound` | spec §2.2 name; fields Phase 4-defined per CRIB |
| `Feedback` | `trial.py` | `Feedback@1` | spec §2.2 name; fields from spec §7 L3 output |
| `GovernanceReport` | `trial.py` | `GovernanceReport@1` | Phase 4-defined (spec §7 L2 output) |
| `HonestyGateReport` | `trial.py` | — internal, nested | Phase 4-defined (spec §7 L0 output) |
| `OptimizeRound` | `trial.py` | `OptimizeRound@1` | Phase 4-defined (append-only round archive) |
| `HoldoutWindow` | `trial.py` | `HoldoutWindow@1` | spec §8 verbatim |
| `TrialRecord` | `trial.py` | `TrialRecord@1` | spec §8 verbatim |
| `OptimizeRunState` | `trial.py` | `OptimizeRunState@1` | spec §8 verbatim |
| `OptimizeResult` | `trial.py` | `OptimizeResult@1` | spec §8 verbatim |
| `HoldoutReceipt` | `trial.py` | `HoldoutReceipt@1` | spec §8 verbatim |
| `HoldoutLease` | `trial.py` | `HoldoutLease@1` | spec §8 verbatim |
| `SealedEvaluationRecord` | `trial.py` | `SealedEvaluationRecord@1` | spec §8 verbatim |
| `SealedCapability` | `trial.py` | `SealedCapability@1` | spec §8 verbatim |
| `TrialLedger` / `SealedResultStore` / `SealedEvaluatorGateway` / `Governor` | services | — internal (not pydantic payloads) | spec §8 pseudocode classes |

Sixteen registered payloads; every service/nested/exception surface carries a reviewed internal reason in `PHASE4_INTERNAL_MODELS` (Task 9).

---

## Task 1: Study identity, candidate and split contracts (`trial.py`, part 1)

**Files:**
- Create: `guanlan_v2/orchestration/trial.py`
- Test: `tests/orchestration/test_trial_contracts.py`

**Consumes:** Phase 1 `DigestModel`, `ContractModel`, `content_digest`, strict types (`DigestHex`, `UtcDateTime`, `FiniteFloat`, `NonNegativeInt`, `PositiveInt`, `NonEmptyStr`), `LogicalId`, `Confidence`.

**Produces** (all strict/frozen/extra-forbid `DigestModel`, `schema_version: Literal["1"] = "1"`, spec §8 field spellings kept):

- `StudySpec`: `objective: NonEmptyStr`, `objective_digest: DigestHex`, `label_definition: NonEmptyStr`, `label_digest: DigestHex`, `universe_digest: DigestHex`, `frequency: NonEmptyStr`, `split_policy_digest: DigestHex`, `parent_family_id: LogicalId | None = None`, `change_reason: NonEmptyStr | None = None`. `objective`/`label_definition` are display text; the five digest/frequency fields are the registry-resolved identity handles. Validator: `parent_family_id` and `change_reason` are all-set-or-all-none. Method `identity_projection(self) -> dict[str, Any]` returning exactly `{"domain": "study-family-v1", "objective_digest", "label_digest", "universe_digest", "frequency", "split_policy_digest"}` — display fields deliberately excluded so free text can never perturb family identity. Revision-family identity convention (AMEND-5 red line ①, spec:124): successive revisions of one governed asset (a `factor_id`/`pattern_id` lineage) must construct the **same** family — `objective_digest` anchors the asset lineage and the revision expression enters only the `OptimizeCandidate`, never study identity; a genuine identity change routes through `parent_family_id` + `change_reason`.
- `StudyFamily`: `family_id: LogicalId`, `identity_digest: DigestHex`, `objective_digest/label_digest/universe_digest: DigestHex`, `frequency: NonEmptyStr`, `split_policy_digest: DigestHex`, `parent_family_id: LogicalId | None = None`, `change_reason: NonEmptyStr | None = None`, `governor_attestation: DigestHex`. Validator: `family_id == "fam." + identity_digest[:16]` (satisfies the `LogicalId` grammar; construction outside `governor.derive_study_family` with a mismatched id fails).
- `SplitSpec`: `scheme: Literal["cpcv", "walk_forward", "oos_fraction"]`, `n_groups: PositiveInt | None = None`, `k: PositiveInt | None = None`, `purge: NonNegativeInt = 0`, `embargo: NonNegativeInt = 0`, `label_horizon: PositiveInt`, `oos_frac: FiniteFloat | None = None` (validator: when present `0 < oos_frac < 1`), `nested: bool = False`. Matrix: `cpcv` requires `n_groups` and `k` (with `k < n_groups`) and forbids `oos_frac`; `oos_fraction` requires `oos_frac` and forbids `n_groups/k`; `walk_forward` forbids `oos_frac`. Property `effective_purge -> int` = `max(purge, label_horizon + 1)`, encoding the `strict_validate` widening rule (`strategy/compute/cpcv.py:293`) so a split spec can never under-purge overlapping labels.
- `OptimizeCandidate`: `candidate_kind: Literal["workflow_graph", "pattern_definition"]` (AMEND-6: pattern-replay candidates admitted into v1 before the Task 9 golden freeze, per the R1 reconcile ruling — no `@2` bump for the curator phase), `graph: dict[str, Any]` (strict JSON-shaped, same acceptance rule as `PlanNode.params`), `params: dict[str, Any]`, `parent_trial_id: NonEmptyStr | None = None`, `display_name: NonEmptyStr | None = None`, `candidate_hash: DigestHex`. `SEMANTIC_EXCLUDE = frozenset({"display_name"})`; `SELF_DIGEST_FIELDS = frozenset({"candidate_hash"})`. Classmethod builder `build(cls, **fields) -> OptimizeCandidate` canonicalizes `graph` before sealing: nodes reduced to `{"id", "type", "params"}` sorted by id, edges sorted — layout `x/y` and any unknown node keys are dropped, preserving the "positions don't count" property of `workflow.executor.graph_signature` (executor.py:108-118) under the Phase 1 sha256 digest. Module constant `CANDIDATE_HASH_DOMAIN = "optimize-candidate-v1"`; `candidate_hash = content_digest({"domain": CANDIDATE_HASH_DOMAIN, "candidate_kind": ..., "graph": <canonical graph>, "params": ...})`.
- `ValidationMetrics`: `rank_ic/sharpe/ann_return/max_drawdown/turnover/win_rate/tail_ratio: FiniteFloat | None = None`, `coverage: FiniteFloat | None = None` (0..1 when present), `oos_verdict: Literal["robust", "degraded", "overfit", "insufficient", "na"] | None = None`, `n_dates: NonNegativeInt | None = None`, `factor: NonEmptyStr | None = None`, `profit_loss_ratio: FiniteFloat | None = None`, `n_occurrences: NonNegativeInt | None = None` (AMEND-6: pattern-replay metrics — profit/loss ratio and occurrence count; hit/win rates ride the existing fields), `source: Literal["run_graph", "shadow_backend", "case_grader", "pattern_replay"]`. `None` means honestly unavailable; no field is ever zero-filled by builders.
- `GateResult` (Phase 4 evaluation-gate outcome; deliberately distinct from and never imported into Phase 1 `spec.py`, whose Plan-gate `GateResult` at spec.py:370 stays untouched and internal): `status: Literal["passed", "failed", "unavailable"]`, `min_rank_ic: FiniteFloat`, `oos_required: Literal["robust"] = "robust"`, `sharpe_required: bool = True`, `observed_rank_ic: FiniteFloat | None = None`, `observed_sharpe: FiniteFloat | None = None`, `observed_oos_verdict: NonEmptyStr | None = None`, `reason: NonEmptyStr`. Field names extend the existing loop gate's return keys (`loop.py:284-292`: `passed/min_rank_ic/oos_required/sharpe_required`).
- `Feedback`: `target_worker_ids: tuple[LogicalId, ...] = ()`, `evidence_artifact_ids: tuple[NonEmptyStr, ...] = ()`, `allowed_changes: tuple[NonEmptyStr, ...] = ()`, `reason: NonEmptyStr`, `confidence: Confidence`, `ambiguous: bool = False`, `source: Literal["llm", "deterministic", "rule"]`. Matrix: `ambiguous=True` ⇒ `target_worker_ids == ()`; `ambiguous=False` ⇒ `1 <= len(target_worker_ids) <= 2` (spec §7 L3: attribution narrows to 1–2 workers or honestly returns ambiguous).
- `GovernanceReport`: `status: Literal["ok", "unavailable"]`, `family_id: LogicalId`, `raw_trial_count: NonNegativeInt`, `effective_n_trials: NonNegativeInt | None = None`, `deflated_sharpe: FiniteFloat | None = None`, `pbo: FiniteFloat | None = None`, `pbo_enabled: bool = False`, `pbo_reason: NonEmptyStr | None = None`, `complexity_score: FiniteFloat | None = None`, `trial_budget_remaining: NonNegativeInt`, `peek_budget_remaining: NonNegativeInt`, `reasons: tuple[NonEmptyStr, ...] = ()`. Matrix: `status="unavailable"` ⇒ `deflated_sharpe is None and pbo is None` and `reasons` non-empty; `effective_n_trials <= raw_trial_count` when present; `pbo_enabled=False` ⇒ `pbo is None` and `pbo_reason` set.
- `HonestyGateReport` (internal, embedded): `passed: bool`, `reasons: tuple[NonEmptyStr, ...] = ()`, `checked: tuple[NonEmptyStr, ...]` (canonically sorted, duplicate-free names of executed checks). `passed=False` ⇒ `reasons` non-empty.
- `OptimizeRound` (append-only round archive): `experiment_id: NonEmptyStr`, `round_index: NonNegativeInt`, `candidate_hash: DigestHex`, `trial_id: NonEmptyStr | None = None`, `reused_from_trial_id: NonEmptyStr | None = None`, `l0: HonestyGateReport | None = None`, `metrics: ValidationMetrics | None = None`, `gate: GateResult | None = None`, `governance: GovernanceReport | None = None`, `feedback: Feedback | None = None`, `stall_retry: bool = False`, `error: NonEmptyStr | None = None`, `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"created_at"})`.

**Required invariants:**

1. every model is strict/frozen/extra-forbid with closed `schema_version`; naive datetimes, NaN/Inf floats and unknown fields are rejected by Phase 1 base types;
2. `StudySpec.identity_projection` output is byte-stable under display-text changes: two specs differing only in `objective`/`label_definition` produce identical projections;
3. `OptimizeCandidate.build` produces identical `candidate_hash` for graphs differing only in node `x/y`/layout keys or node/edge ordering, and different hashes when any `type`/`params`/edge differs;
4. `SplitSpec.effective_purge` equals `max(purge, label_horizon + 1)` for a parameterized matrix including `purge=5, label_horizon=5 → 6`;
5. `Feedback` ambiguity matrix and `GovernanceReport` unavailable matrix hold bidirectionally;
6. direct construction of a self-sealed `OptimizeCandidate` with a wrong `candidate_hash` fails validation.

- [ ] **Step 1: Write failing contract tests**

Test matrix (each row is at least one test):

| Case | Expectation |
|---|---|
| `StudySpec` with `parent_family_id` and no `change_reason` | `ValidationError` |
| two `StudySpec`s differing only in `objective`/`label_definition` | identical `identity_projection()` dicts and identical `content_digest` thereof |
| `identity_projection()` key set | exactly `{"domain", "objective_digest", "label_digest", "universe_digest", "frequency", "split_policy_digest"}` |
| `StudyFamily` with `family_id != "fam." + identity_digest[:16]` | `ValidationError` |
| `SplitSpec(scheme="cpcv")` without `k` / with `oos_frac` | `ValidationError` both ways |
| `SplitSpec(scheme="oos_fraction", oos_frac=1.0)` and `oos_frac=0.0` | `ValidationError` (open interval) |
| `SplitSpec(purge=5, label_horizon=5).effective_purge` | `6`; `(purge=9, label_horizon=5)` → `9` |
| `OptimizeCandidate.build` on the same graph with shuffled node order + `x/y` keys | identical `candidate_hash` |
| same graph, one node param changed | different `candidate_hash` |
| direct `OptimizeCandidate(...)` with wrong `candidate_hash` | `ValidationError` (self-digest verification) |
| fixed vector: `graph={"nodes":[{"id":"a","type":"formula","params":{"expr":"x"},"x":1}],"edges":[]}, params={}` | one stable `candidate_hash` hex recorded in-test as a golden literal |
| `ValidationMetrics(coverage=1.5)` / `rank_ic=float("nan")` | `ValidationError` via Phase 1 strict types |
| `Feedback(ambiguous=True, target_worker_ids=("a",))` | `ValidationError`; `ambiguous=False` with 0 or 3 targets also fails |
| `GovernanceReport(status="unavailable", deflated_sharpe=0.4)` | `ValidationError`; `status="unavailable"` with empty `reasons` fails |
| `GovernanceReport(effective_n_trials=9, raw_trial_count=5)` | `ValidationError` |
| `HonestyGateReport(passed=False, reasons=())` | `ValidationError` |
| `OptimizeRound` semantic digest with two different `created_at` values | identical (`SEMANTIC_EXCLUDE`) |
| every model, extra unknown field / naive datetime | `ValidationError` (extra-forbid / `UtcDateTime`) |

Run now: `pytest tests/orchestration/test_trial_contracts.py -v`

Expected: FAIL on missing `guanlan_v2.orchestration.trial` contracts; collection errors elsewhere do not count.

- [ ] **Step 2: Implement the contracts**

Order the module: shared constants → identity models → candidate/split → evaluation-fact models. No service code in this task.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_trial_contracts.py tests/orchestration/test_phase4_handoff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/trial.py tests/orchestration/test_trial_contracts.py
git commit -m "feat(orchestration): add study/candidate/split and evaluation-fact contracts (phase4 task 1)"
```

---

## Task 2: Trial, holdout and sealed record contracts (`trial.py`, part 2)

**Files:**
- Modify: `guanlan_v2/orchestration/trial.py`
- Test: `tests/orchestration/test_trial_contracts.py` (extend)

**Consumes:** Task 1 contracts; Phase 1 `ExperimentStatus`, `TypedPayloadRef`.

**Produces** (spec §8 verbatim field spellings, Phase 1 strict types, `list` → `tuple`):

- `HoldoutWindow`: `holdout_window_id: NonEmptyStr`, `family_identity_digest: DigestHex`, `start_at: UtcDateTime`, `end_at: UtcDateTime`, `matured_at: UtcDateTime`, `data_snapshot_id: NonEmptyStr`, `vintage_manifest_digest: DigestHex`, `prior_window_ids: tuple[NonEmptyStr, ...] = ()`, `non_overlap_attestation: DigestHex`. `SEMANTIC_EXCLUDE = frozenset({"data_snapshot_id"})` (locator, mirroring `DataContext`). Validators: `start_at < end_at <= matured_at`; `prior_window_ids` duplicate-free and excluding `holdout_window_id`.
- `TrialRecord`: `trial_id: NonEmptyStr`, `family_id: LogicalId`, `candidate_hash: DigestHex`, `parent_trial_id: NonEmptyStr | None = None`, `data_snapshot_hash: DigestHex`, `split_spec_hash: DigestHex`, `code_prompt_model_hash: DigestHex`, `metrics_revealed: tuple[NonEmptyStr, ...] = ()`, `stage: Literal["validation", "sealed_holdout"]`, `status: Literal["reserved", "revealed", "failed", "timed_out", "inconclusive"]`, `validation_result_artifact_id: NonEmptyStr | None = None`, `result_digest: DigestHex | None = None`, `holdout_window_id: NonEmptyStr | None = None`, `holdout_lease_id: NonEmptyStr | None = None`, `lease_state: Literal["none", "reserved", "consumed", "exhausted"] = "none"`, `revealed_at: UtcDateTime | None = None`, `idempotency_key: NonEmptyStr`, `reused_from_trial_id: NonEmptyStr | None = None`, `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"trial_id", "created_at", "revealed_at"})`. Matrix validators:
  - `stage="validation"` ⇒ `holdout_window_id/holdout_lease_id is None` and `lease_state == "none"`;
  - `stage="sealed_holdout"` ⇒ `validation_result_artifact_id is None` and `metrics_revealed == ()` and `holdout_window_id/holdout_lease_id` present and `lease_state != "none"` (the spec §7 public-record emptiness rule enforced at the model, so no code path can publish a metrics-bearing holdout record);
  - `status="reserved"` ⇒ `result_digest is None`, `revealed_at is None`, `metrics_revealed == ()`, and for holdout `lease_state == "reserved"`;
  - `status="revealed"` + `stage="validation"` ⇒ `validation_result_artifact_id`, `result_digest`, `revealed_at` present and `metrics_revealed` non-empty;
  - `status="revealed"` + `stage="sealed_holdout"` ⇒ `lease_state == "consumed"`; `status in {"failed","timed_out","inconclusive"}` + holdout ⇒ `lease_state == "exhausted"` (spec 运行不变量 line 937).
- `OptimizeRunState`: `experiment_id: NonEmptyStr`, `family_id: LogicalId`, `status: ExperimentStatus`, `candidate_hash: DigestHex | None = None`, `resume_after: UtcDateTime | None = None`, `wakeup_key: NonEmptyStr | None = None`, `updated_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"updated_at"})`. Matrix: `status == ExperimentStatus.WAITING_FOR_MATURITY` ⇔ (`resume_after` present and `wakeup_key` present); any other status forbids both.
- `OptimizeResult`: `state: OptimizeRunState`, `best_candidate_artifact_id: NonEmptyStr | None = None`, `validation_trial_ids: tuple[NonEmptyStr, ...] = ()`, `stop_reason: NonEmptyStr | None = None`. Matrix: `state.status == ExperimentStatus.PASSED_VALIDATION` ⇒ `best_candidate_artifact_id` present; terminal failure statuses ⇒ `stop_reason` present.
- `HoldoutReceipt`: `trial_id: NonEmptyStr`, `family_id: LogicalId`, `holdout_window_id: NonEmptyStr`, `status: Literal["revealed", "failed", "timed_out", "inconclusive"]`, `result_digest: DigestHex | None = None`, `revealed_at: UtcDateTime | None = None`. Matrix: `status="revealed"` ⇒ `result_digest` and `revealed_at` present. Structural guarantee: the model has no `PayloadRef`/`TypedPayloadRef`/artifact-ref field — a receipt can never dereference sealed content.
- `HoldoutLease`: `lease_id: NonEmptyStr`, `trial_id: NonEmptyStr`, `candidate_hash: DigestHex`, `holdout_window_id: NonEmptyStr`, `issued_at: UtcDateTime`, `expires_at: UtcDateTime` (validator: `> issued_at`), `nonce: NonEmptyStr`, `signature: DigestHex`.
- `SealedEvaluationRecord`: `trial_id: NonEmptyStr`, `result_artifact_id: NonEmptyStr`, `result_digest: DigestHex`, `metrics_payload: dict[str, Any]` (strict JSON-shaped), `curve_ref: TypedPayloadRef | None = None` (validator: when present `curve_ref.payload_ref.namespace == "sealed"`), `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"created_at"})`.
- `SealedCapability`: `token_id: NonEmptyStr`, `scope: Literal["final_report", "human_review"]`, `principal_id: NonEmptyStr`, `expires_at: UtcDateTime`, `signature: DigestHex`.

**Required invariants:**

1. every matrix above holds bidirectionally under parameterized tests;
2. a holdout `TrialRecord` carrying any metric name or a `validation_result_artifact_id` cannot be constructed at all;
3. `HoldoutReceipt` field set is exactly the six spec fields (asserted via `model_fields` equality) — the public surface cannot grow silently;
4. `SealedEvaluationRecord` with a `main`-namespace `curve_ref` is rejected;
5. semantic digests of `TrialRecord`/`OptimizeRunState` are invariant to `trial_id`/`created_at`/`revealed_at`/`updated_at` changes.

- [ ] **Step 1: Write failing tests** — extend `test_trial_contracts.py`:

| Case | Expectation |
|---|---|
| `HoldoutWindow(start_at >= end_at)` / `end_at > matured_at` | `ValidationError` both |
| `HoldoutWindow.prior_window_ids` containing its own id or a duplicate | `ValidationError` |
| holdout `TrialRecord` with `metrics_revealed=("rank_ic",)` | `ValidationError` (unconstructible, not merely filtered) |
| holdout `TrialRecord` with `validation_result_artifact_id` set | `ValidationError` |
| validation `TrialRecord` with `holdout_window_id`/`lease_state="reserved"` | `ValidationError` both |
| `status="reserved"` with `result_digest`/`revealed_at`/non-empty `metrics_revealed` | `ValidationError` each |
| validation `status="revealed"` missing any of artifact id / result_digest / revealed_at / metrics names | `ValidationError` each |
| holdout `status="revealed", lease_state="exhausted"` | `ValidationError`; `status="failed", lease_state="consumed"` also fails |
| `TrialRecord` semantic digest across changed `trial_id`/`created_at`/`revealed_at` | unchanged |
| `OptimizeRunState(status=WAITING_FOR_MATURITY)` missing `resume_after` or `wakeup_key` | `ValidationError`; `status=RUNNING` with either present also fails |
| `OptimizeResult(state.status=PASSED_VALIDATION, best_candidate_artifact_id=None)` | `ValidationError`; `state.status=FAILED, stop_reason=None` fails |
| `HoldoutReceipt(status="revealed")` without `result_digest`/`revealed_at` | `ValidationError` |
| `set(HoldoutReceipt.model_fields) - {"schema_version"}` | exactly the six spec field names (public surface frozen) |
| `HoldoutLease(expires_at <= issued_at)` | `ValidationError` |
| `SealedEvaluationRecord.curve_ref` with `namespace="main"` | `ValidationError`; `namespace="sealed"` passes |
| `SealedCapability(scope="optimizer")` | `ValidationError` (closed Literal) |

Run now: `pytest tests/orchestration/test_trial_contracts.py -v` — Expected: FAIL on missing classes/validators.

- [ ] **Step 2: Implement** the eight record models in `trial.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_trial_contracts.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/trial.py tests/orchestration/test_trial_contracts.py
git commit -m "feat(orchestration): add trial/holdout/sealed record contracts (phase4 task 2)"
```

---

## Task 3: Additive `EventType` extension + absence-guard flips

The only permitted Phase 1 source touch. Mechanism fixed by the controller CRIB: pure addition to `events.py` (new enum members + per-type payload rules), and the two absence guards flip from "does not exist" to "absent from the Phase 1 `default_registry()` and present in the Phase 4 registry". The Phase 1 golden manifest is never regenerated.

**Files:**
- Modify: `guanlan_v2/orchestration/events.py` (additive only)
- Modify: `tests/orchestration/test_events.py` (flip `test_event_type_set_excludes_trial_and_holdout`, lines ~124-153)
- Modify: `tests/orchestration/test_contract_completeness.py` (flip the Trial/Holdout guards, lines ~245-262)
- Modify: `tests/orchestration/test_phase4_handoff.py` (delete the Step-1 point-3 "still absent" assertion)
- Test: `tests/orchestration/test_trial_events.py`

**Consumes:** Phase 1 `EventType`, `RunEvent`, `EventPartition`; the reserved-name comments at `events.py:48-50`.

**Produces:**

- Three new `EventType` members appended without renumbering or reordering existing members: `TRIAL_RESERVED = "TrialReserved"`, `TRIAL_REVEALED = "TrialRevealed"`, `TRIAL_EXHAUSTED = "TrialExhausted"` (exactly the names reserved in the Phase 1 deferral comments).
- Additive per-type payload rules in the existing `RunEvent` validator chain, expressed over `payload_schema_ref.name` strings (never importing `trial.py` — the completeness sweep forbids Trial/Holdout names inside Phase 1 modules):
  - `TrialReserved` ⇒ `payload_schema_ref.name == "TrialRecord"`;
  - `TrialRevealed` ⇒ `payload_schema_ref.name in {"TrialRecord", "HoldoutReceipt"}` (validation reveal vs holdout reveal);
  - `TrialExhausted` — partition-conditional: `main` partition ⇒ `payload_schema_ref.name == "HoldoutReceipt"` only (spec 运行不变量 ~line 938 scopes the metrics restriction to the public partition); `audit` partition ⇒ `payload_schema_ref.name in {"HoldoutReceipt", "TrialRecord"}`, so the ledger's full terminal holdout `TrialRecord@1` audit copy (event ↔ payload table row 4; consumed by Task 5) is constructible.
  - Existing partition/namespace-masquerade rules apply unchanged; a `main`-partition trial event referencing a `sealed` payload remains rejected by the Phase 1 validator.
- Guard flips:
  - `test_events.py::test_event_type_set_excludes_trial_and_holdout` → renamed `test_event_type_set_is_exactly_phase4_frozen`: the exact-equality set grows 20 → 23; the "building `TrialReserved` raises" assertion becomes "building `TrialReserved` with a `TrialRecord`-named schema ref succeeds and with any other schema name fails".
  - `test_contract_completeness.py::test_no_trial_or_holdout_type_registered` → asserts Trial/Holdout-prefixed names remain absent from Phase 1 `default_registry()` **and** (delegating to a lazy import of `trial.py`) that `TrialRecord`/`HoldoutWindow`/`HoldoutReceipt`/`HoldoutLease` are members of `PHASE4_PUBLIC_MODELS`;
  - `test_contract_completeness.py::test_no_trial_or_holdout_type_classified_or_defined` → keeps sweeping `PHASE1_MODULES` for leaked Trial/Holdout module-level names (enum members are class attributes and do not trip it; the assertion now documents that Phase 4 types live only in `trial.py`).

**Required invariants:**

1. all 20 pre-existing `EventType` values and their semantics are byte-identical; the Phase 1 golden manifest file is untouched (assert file digest unchanged in `test_trial_events.py`);
2. `RunEvent.build` accepts each new type only with its allowed payload schema name(s);
3. `ArtifactStaged`/`LayerCommitted` visibility rules are unaffected;
4. a `main`-partition trial event cannot reference a non-public namespace (existing rule regression).

Event ↔ payload mapping frozen by this task (consumed by Tasks 5–6):

| EventType | Partition | Allowed payload schema | Emitted by |
|---|---|---|---|
| `TrialReserved` | `main` | `TrialRecord@1` (validation or metrics-empty holdout reservation) | `TrialLedger.reserve_validation` / `reserve_holdout` |
| `TrialRevealed` | `main` | `TrialRecord@1` (validation reveal) or `HoldoutReceipt@1` (holdout success) | `TrialLedger.reveal_validation` / `exhaust_holdout(status="revealed")` |
| `TrialExhausted` | `main` | `HoldoutReceipt@1` only | `TrialLedger.exhaust_holdout(status in failed/timed_out/inconclusive)` |
| `TrialRevealed`/`TrialExhausted` | `audit` | full terminal holdout `TrialRecord@1` | ledger audit trail |
| `ExperimentStateChanged` (pre-existing) | `main` | `OptimizeRunState@1` | `ExperimentStateStore.save` |

- [ ] **Step 1: Write failing tests** — `test_trial_events.py`:

1. `set(EventType)` equals the exact 23-value frozen set (20 Phase 1 values byte-identical, in original order, plus the three new members);
2. `RunEvent.build(event_type="TrialReserved", payload_schema_ref=SchemaRef(name="TrialRecord", version="1"), ...)` succeeds; the same with `name="HoldoutReceipt"` fails; a `main`-partition `TrialExhausted` accepts only `HoldoutReceipt` while an `audit`-partition `TrialExhausted` with `TrialRecord` succeeds (partition-conditional rule); `TrialRevealed` accepts exactly the two names;
3. a `main`-partition `TrialRevealed` with a `sealed`-namespace `payload_ref` is rejected by the unchanged Phase 1 namespace-masquerade validator (regression);
4. `ArtifactStaged` journal-only and `LayerCommitted` visible-seq rules are unaffected (regression);
5. the Phase 1 golden manifest file bytes are unchanged (sha256 of `tests/orchestration/golden/schema_manifest_v1.json` equals the reviewed constant);
6. guard flips applied in the same change: the renamed exact-equality test, the registry-absence test extended with the phase4-presence half (lazily importing `trial.py`), and the module sweep with its updated docstring — so the red run shows the *old* guards failing against the new expectations.

Run now: `pytest tests/orchestration/test_trial_events.py tests/orchestration/test_events.py tests/orchestration/test_contract_completeness.py -v`

Expected: FAIL on missing enum members / old exact-equality set; not collection errors.

- [ ] **Step 2: Implement** the additive extension in `events.py`.

- [ ] **Step 3: Run the full orchestration suite and commit**

Run: `pytest tests/orchestration -v` — Expected: PASS (all Phase 1/2/3 tests green with flipped guards).

```bash
git add guanlan_v2/orchestration/events.py tests/orchestration/test_events.py tests/orchestration/test_contract_completeness.py tests/orchestration/test_trial_events.py tests/orchestration/test_phase4_handoff.py
git commit -m "feat(orchestration): add Trial/Holdout event types with flipped absence guards (phase4 task 3)"
```

---

## Task 4: Governor — pure statistics extraction + study-family identity (`governor.py`)

**Files:**
- Create: `guanlan_v2/orchestration/governor.py`
- Modify: `guanlan_v2/workflow/api.py` (delegate bodies only)
- Test: `tests/orchestration/test_governor.py`

**Consumes:** the exact existing implementations cited by the grounding map — `workflow/api.py:3115 _cscv_pbo`, `workflow/api.py:2334 _oos_verdict`, `strategy/compute/cpcv.py:106 deflated_sharpe` / `cpcv.py:18 make_splits`, `screen/factor_ic.py:28 compute_catalog_ic` (read-only reference for IC conventions), `screen/factor_vintage.py:138` realized-date gate (read-only reference); Task 1 `StudySpec`/`StudyFamily`/`GovernanceReport`/`OptimizeCandidate`/`SplitSpec`.

**Produces:**

- Module constants: `GOVERNOR_VERSION = "governor-v1"`, `STUDY_FAMILY_DOMAIN = "study-family-v1"`, `FAMILY_ATTESTATION_DOMAIN = "study-family-attestation-v1"`.
- **Moved pure functions** (bodies relocated verbatim, Web/API-free; the originals in `workflow/api.py` become one-line delegates so every existing caller and test keeps exact behavior):
  - `def cscv_pbo(perf_matrix: "np.ndarray", n_blocks: int = 8) -> dict[str, Any]:` — identical return shape `{"enabled", "pbo", "n_combos", "n_candidates", "n_blocks", "note"}` or `{"enabled": False, "reason": ...}`; identical preconditions (`N>=2`, `S>=4` even blocks; the ≥16-rebalance-date guard stays at the `_factor_compose` call site).
  - `def oos_verdict(is_v: float | None, oos_v: float | None, n_oos: int, min_n: int = 6) -> str:` — identical thresholds (decay ≥0.6 → `"robust"`, ≥0.2 → `"degraded"`, else `"overfit"`; `n_oos < min_n` → `"insufficient"`; IS ≤0/NaN → `"na"`).
  - Wrapper `def dsr(returns: Sequence[float], n_trials: int, sharpes_std: float | None = None) -> float | None:` importing `deflated_sharpe` **function-locally** from `guanlan_v2.strategy.compute.cpcv` (keeps the orchestration import graph light). No `cpcv_splits` wrapper is added — nothing in Phase 4 consumes CPCV splits directly; any L2 binding that needs splits imports `strategy.compute.cpcv.make_splits(dates, n_groups=6, k=2, purge=5, embargo=5)` directly (the Task 0 reuse surface) and passes `purge=split_spec.effective_purge, embargo=split_spec.embargo` at that call site. Neither algorithm is copied.
- **Family identity (pure):**
  - `def derive_study_family(study: StudySpec, *, governor_version: str = GOVERNOR_VERSION) -> StudyFamily:` — `identity_digest = content_digest(study.identity_projection())`; `family_id = "fam." + identity_digest[:16]`; `governor_attestation = content_digest({"domain": FAMILY_ATTESTATION_DOMAIN, "identity_digest": identity_digest, "governor_version": governor_version})`; lineage fields copied from the study. Docstring records the Task 1 revision-family identity convention (AMEND-5 red line ①): every revision of the same governed asset derives the same `family_id`, so trial/holdout budgets cannot be reset by re-proposing a revision as a "new" study.
  - `def verify_family_attestation(family: StudyFamily, *, governor_version: str = GOVERNOR_VERSION) -> None:` — recomputes both digests; raises `FamilyAttestationError(ValueError)` on any mismatch (forged/renamed/caller-minted families are rejected deterministically; v1 attestations are domain-tagged recomputable digests, not cryptographic secrets — documented honestly in the docstring).
- **Trial statistics (pure):**
  - `def effective_n_trials(raw_trial_count: int, distinct_candidate_count: int, block_rank_ic: Mapping[str, Sequence[float]] | None = None, *, corr_threshold: float = 0.9) -> int:` — when per-candidate rank-IC block vectors are supplied, clusters candidates whose pairwise Pearson correlation ≥ `corr_threshold` and returns `max(2, cluster_count)`; when absent, returns `max(2, raw_trial_count)` (the most conservative choice for DSR deflation — more trials, more deflation) and the caller badges `"correlation_unadjusted"`. Always `2 <= result <= max(2, raw_trial_count)`.
  - `def complexity_score(candidate: OptimizeCandidate) -> float:` — deterministic `n_nodes + n_edges + 0.1 * total_param_count` over the canonical graph; **informational only** in v1 (surfaced in `GovernanceReport.complexity_score`, never subtracted from real returns — spec §7 "不改写真实收益").
- **`class Governor:`** — `def __init__(self, *, trial_budget: PositiveInt, peek_budget: PositiveInt, governor_version: str = GOVERNOR_VERSION):`
  - `def resolve_family(self, study: StudySpec) -> StudyFamily:` (delegates to `derive_study_family`);
  - `def govern(self, *, family: StudyFamily, stats: Mapping[str, Any], returns: tuple[float, ...] | None, perf_matrix: "np.ndarray | None", candidate: OptimizeCandidate, split_spec: SplitSpec) -> GovernanceReport:` — pure over inputs (`stats` is the ledger's `effective_trial_stats` dict; no I/O inside): raw count = `stats["raw_trial_count"]` (audit upper bound from the global TrialLedger); DSR only when `returns` has ≥10 samples (mirroring `deflated_sharpe`'s own floor) else `None` + reason; PBO only when `perf_matrix` meets `cscv_pbo` preconditions else `pbo_enabled=False` + reason; budgets: `trial_budget_remaining = max(0, trial_budget - raw)`, `peek_budget_remaining = max(0, peek_budget - stats["reveal_count"])`; `status="unavailable"` whenever no governance number could be computed — never a fabricated score; `status="ok"` requires at least one of DSR/PBO present.
  - `def should_stop(self, report: GovernanceReport) -> str | None:` — returns `"trial_budget_exhausted"` / `"peek_budget_exhausted"` when the respective remaining hits 0, else `None`.

**Required invariants:**

1. delegate equivalence: for a shared vector set, `workflow.api._cscv_pbo(m) == governor.cscv_pbo(m)` and `workflow.api._oos_verdict(...) == governor.oos_verdict(...)` (identity of dict/str outputs), and the existing workflow test suite stays green;
2. `derive_study_family` is display-text-invariant and deterministic; `verify_family_attestation` rejects a family whose `family_id`, `identity_digest` or attestation was tampered;
3. renaming a study (new display text, same digests) yields the same `family_id` — budgets cannot be reset by renaming;
4. changing any of the five identity handles yields a new family; constructing it via a `StudySpec` with `parent_family_id` but no `change_reason` fails at the contract;
5. `govern` with 3 return samples and no perf matrix yields `status="unavailable"`, `deflated_sharpe=None`, `pbo=None`, non-empty `reasons` — no fabricated numbers;
6. `effective_n_trials` never exceeds its raw bound and equals `max(2, raw)` without correlation evidence;
7. `governor.py` performs no network/file/store I/O (asserted by import-surface inspection: module imports limited to stdlib, numpy, Phase 1/4 contracts).

- [ ] **Step 1: Write failing tests** covering all seven invariants plus:

`oos_verdict` threshold matrix (behavioral vectors recorded in-test, lifted from `workflow/api.py:2334-2350`):

| `is_v` | `oos_v` | `n_oos` | verdict |
|---|---|---|---|
| 0.10 | 0.08 | 10 | `"robust"` (decay 0.8 ≥ 0.6) |
| 0.10 | 0.03 | 10 | `"degraded"` (0.3 ≥ 0.2) |
| 0.10 | 0.01 | 10 | `"overfit"` |
| 0.10 | 0.08 | 5 | `"insufficient"` (`n_oos < 6`) |
| 0.0 / NaN | any | 10 | `"na"` |

`cscv_pbo` precondition matrix: `(1, 8)` matrix → `enabled=False` (N<2); `(3, 5)` → `enabled=False` (odd/insufficient blocks); `(3, 8)` well-formed → `enabled=True` with `n_combos == C(8,4)` and `0 <= pbo <= 1`; delegate identity: `workflow.api._cscv_pbo(m)` returns the same dict as `governor.cscv_pbo(m)` and `workflow.api._oos_verdict` the same strings.

`effective_n_trials`: `(raw=7, distinct=7, block_rank_ic=None)` → 7; three candidates with pairwise-correlated (>0.9) block vectors plus one independent → cluster count 2 → result `max(2, 2) = 2`; `(raw=1, distinct=1, None)` → 2 (floor); result never exceeds `max(2, raw)`.

Family identity: rename-only study pair → same `family_id`; each of the five identity handles perturbed → new `family_id`; tampered `governor_attestation`/`identity_digest`/`family_id` each → `FamilyAttestationError`.

`govern` honesty: 3-sample returns + no matrix → `status="unavailable"`, both stats `None`, `reasons` ⊇ {DSR-insufficient, PBO-precondition}; 30-sample returns → `deflated_sharpe` finite, `status="ok"`; `should_stop` returns the right reason string exactly when a remaining budget hits 0.

Run now: `pytest tests/orchestration/test_governor.py -v` — Expected: FAIL on missing module.

- [ ] **Step 2: Implement** `governor.py`; replace the two `workflow/api.py` function bodies with delegates importing from `guanlan_v2.orchestration.governor`.

The delegate diff is exactly two function bodies:

```python
def _cscv_pbo(perf_matrix, n_blocks: int = 8):
    from guanlan_v2.orchestration.governor import cscv_pbo
    return cscv_pbo(perf_matrix, n_blocks=n_blocks)

def _oos_verdict(is_v, oos_v, n_oos, min_n: int = 6):
    from guanlan_v2.orchestration.governor import oos_verdict
    return oos_verdict(is_v, oos_v, n_oos, min_n=min_n)
```

Function-local imports keep `workflow/api.py`'s import time unchanged and avoid a module-level `workflow → orchestration` edge. No other line of `workflow/api.py` moves; `_factor_compose`'s ≥16-rebalance-date pre-check and its result attachment (`composite["pbo"]`, api.py:3264-3269) stay where they are.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_governor.py tests/orchestration/test_trial_contracts.py -v` plus the existing workflow regression most adjacent to the delegates: `pytest tests -k "cscv or oos or compose" -v` (adjust to the reviewed existing test names if different — Task 0 clause).

Expected: PASS.

```bash
git add guanlan_v2/orchestration/governor.py guanlan_v2/workflow/api.py tests/orchestration/test_governor.py
git commit -m "feat(orchestration): extract pure governance statistics + governor family identity (phase4 task 4)"
```

---

## Task 4b: D6 revision-throttle governance primitive (`governor.py` extension)

AMEND-5 overfitting red line ③ (spec:124) belongs with red lines ① (same-family TrialLedger accounting, Task 5) and ② (sealed one-shot reveal, Task 6): the rule is frozen once at the governor, so the curator phases never each hand-copy a drifting N value.

**Files:**
- Modify: `guanlan_v2/orchestration/governor.py`
- Test: `tests/orchestration/test_governor.py` (extend)

**Consumes:** Task 1 `StudySpec.frequency` semantics; Task 4 `GOVERNOR_VERSION`.

**Produces:**

- Module constant `REVISION_THROTTLE_MIN_MATURED = {"monthly": 3, "daily": 20}` — the D6 ruling values (spec:289); the unit is the family's own frequency's matured observation span (monthly = periods, daily = trading days). Frozen in the same batch as `GOVERNOR_VERSION`.
- Pure function `def revision_throttle_check(*, frequency: str, matured_observation_count: int) -> tuple[bool, str]:` — returns `(allowed, reason)`; the previous `definition_version` having fewer than N matured observations ⇒ blocked with the threshold named in the reason; **unknown `frequency` ⇒ rejected, never a silent default-allow** — the same honesty construction as L2 `status="unavailable"`. The matured observation count is supplied by the caller under PIT matured semantics (data source = the Phase 5 matured-case grader); Phase 4 ships the rule only and never binds the data (the same timing-honesty pattern as spec:77).
- Deliberately **not** wired into `Governor.should_stop` or `run_optimize`: the throttle is an admission pre-check on revision proposals (a revision = a new study in the same family), not an optimize-round predicate.

**Required invariants:**

1. boundary matrix holds: monthly 2 → blocked / 3 → allowed; daily 19 → blocked / 20 → allowed / 21 → allowed;
2. unknown frequency (e.g. `"weekly"`, `""`) is rejected with a named reason — no default-allow path exists;
3. the function is pure and I/O-free; the Task 4 invariant-7 import-surface inspection covers the extended module unchanged.

- [ ] **Step 1: Write failing throttle tests** in `test_governor.py` covering the 2/3 and 19/20/21 boundary matrix, unknown-frequency rejection with a named reason, and reason-string presence on every blocked path.

Run now: `pytest tests/orchestration/test_governor.py -v` — Expected: FAIL on missing constant/function.

- [ ] **Step 2: Implement** `REVISION_THROTTLE_MIN_MATURED` and `revision_throttle_check` in `governor.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_governor.py -v` — Expected: PASS (including the re-run import-surface inspection).

```bash
git add guanlan_v2/orchestration/governor.py tests/orchestration/test_governor.py
git commit -m "feat(orchestration): add D6 revision-throttle governance primitive (phase4 task 4b)"
```

---

## Task 5: Event-sourced `TrialLedger` over Phase 2 stores (`trial_ledger.py`)

**Files:**
- Create: `guanlan_v2/orchestration/trial_ledger.py`
- Test: `tests/orchestration/test_trial_ledger.py`

**Consumes:** Phase 2 `EventStore`, `PayloadStore`, `RuntimeStateCellStore` + `StateCellCompareAndSwapCommand`, `RuntimeUnitOfWork`, `IdempotencyConflict`, `AuthoritativeClock`; Task 2 records; Task 3 event types; Task 4 `derive_study_family`/`verify_family_attestation`.

**Produces:**

- State-cell namespaces (per the Phase 2 "extend the reviewed startup namespace set" rule and the Phase 3 sealed-union pattern):
  - `PHASE4_TRIAL_STATE_CELL_NAMESPACES = ("trial.experiment_head.v1", "trial.family_head.v1", "trial.holdout_lease.v1", "trial.window_head.v1")`;
  - `PHASE4_STATE_CELL_NAMESPACES` = canonical lexicographic union of `PHASE3_MEMORY_STATE_CELL_NAMESPACES` + the four above; the Phase 2 store is sealed at startup with exactly this new union (a new sealed union, not a reseal of a live store).
- Errors: `TrialLedgerError(Exception)`, `FamilyAttestationError` (re-export from governor), `HoldoutWindowOverlapError(TrialLedgerError)`, `HoldoutLeaseExhaustedError(TrialLedgerError)`, `TrialReuseViolation(TrialLedgerError)`, `UnknownTrialError(TrialLedgerError)`.
- `TRIAL_TRIPLE_DOMAIN = "trial-triple-v1"`; `def trial_triple_key(*, family_id: str, candidate_hash: str, data_snapshot_hash: str, split_spec_hash: str, code_prompt_model_hash: str) -> DigestHex:` — the idempotent-reuse key.
- `class TrialLedger:` — `def __init__(self, *, event_store, payload_store, state_cells, registry, clock, uow_factory, id_factory: Callable[[], str] | None = None):` (`id_factory` defaults to a uuid-hex factory; ids are audit-only). Event streams use `run_id = family_id` — the ledger is cross-run and cross-experiment by construction; every accepted transition is one `RuntimeUnitOfWork` (payload put + `RunEvent` append + any state-cell CAS, all-or-none). With the Phase 2 in-memory backend this proves same-process semantics only; cross-process durability arrives with a later durable backend (same honest caveat as Phase 3). AMEND-8 future-landing note (docstring): Lane D seat-weight accounting (spec:261 — seats start equal-weight, matured later weighted by seat historical accuracy) lands in this ledger via the catalog-assembly phase through an additive chained-registry extension plus a Task 3-style guard flip; this phase implements none of it and pre-reserves no keys or event names for it (R2 has not named the seat events — only the mechanism is declared here). Methods (spec §8 verbatim names/arities; typed refs made explicit):
  - `def resolve_family(self, study: StudySpec) -> StudyFamily:` — pure derive + read-through of the family head cell; never mints an id itself.
  - `def register_holdout_window(self, study: StudySpec, window: HoldoutWindow, *, idempotency_key: str) -> HoldoutWindow:` — validates `window.family_identity_digest` matches the derived family; recomputes `non_overlap_attestation = content_digest({"domain": "holdout-window-v1", "family_identity_digest": ..., "start_at": ..., "end_at": ..., "prior_window_ids": sorted})` and rejects mismatch; enforces `prior_window_ids` == all currently registered window ids of the family, `start_at >= max(prior end_at)` (non-overlapping later OOT data — a boundary-touching `start_at == prior end_at` is allowed; no overlap, no re-slicing existing history) and `matured_at <= clock.now()` (only already-matured data); CAS on `trial.window_head.v1`.
  - `def reserve_validation(self, study, candidate_ref: TypedPayloadRef, split_ref: TypedPayloadRef, *, data_snapshot_hash: DigestHex, code_prompt_model_hash: DigestHex, idempotency_key: str) -> TrialRecord:` — resolves family (registering the family head + `StudyFamily` payload inside the same UoW on first use); dereferences candidate/split payloads to obtain `candidate_hash`/`split_spec_hash`; computes the triple key; **if a revealed trial with the same triple exists, returns a new `TrialRecord` with `status="revealed"`, `reused_from_trial_id` set and the original `metrics_revealed`/`result_digest` copied — idempotent reuse without re-reveal**; otherwise persists a `reserved` validation `TrialRecord` + `TrialReserved` main event. Reservation failures raise loudly (never a best-effort bool — the research store's swallow-and-return-bool contract is explicitly not reused here).
  - `def reserve_holdout(self, study, candidate_ref: TypedPayloadRef, window_ref: TypedPayloadRef, *, data_snapshot_hash: DigestHex, code_prompt_model_hash: DigestHex, idempotency_key: str) -> TrialRecord:` — one-shot lease: CAS `trial.holdout_lease.v1` cell keyed by `content_digest((family_id, holdout_window_id))` from expected `None`; a second candidate (different `candidate_hash`) on the same window raises `HoldoutLeaseExhaustedError` **before any data read**; the reserved holdout `TrialRecord` (metrics-empty by the Task 2 matrix) + `TrialReserved` main event + lease CAS commit atomically. Identical-key replay returns the original record.
  - `def reveal_validation(self, trial_id: str, result_artifact_id: str, result_digest: DigestHex) -> TrialRecord:` — `reserved→revealed` with `metrics_revealed` fixed to the closed L1 metric-name tuple; revealing a reused record with additional metric names raises `TrialReuseViolation` ("may reuse cached results but never reveal more metrics through a new run"); appends `TrialRevealed` (main, `TrialRecord` payload).
  - `def exhaust_holdout(self, trial_id: str, *, status: Literal["revealed", "failed", "timed_out", "inconclusive"], result_digest: DigestHex | None = None) -> HoldoutReceipt:` — atomic terminal transition per spec 937: `revealed→lease consumed`, others→`lease exhausted`; main partition gets **only** a `HoldoutReceipt` payload (`TrialRevealed` for `revealed`, `TrialExhausted` otherwise); the full terminal holdout `TrialRecord` is appended to the `audit` partition for fold/audit; repeated calls return the original receipt idempotently — no reopen transition exists.
  - `def effective_trial_stats(self, study: StudySpec) -> dict:` — closed key set `{"family_id", "raw_trial_count", "distinct_candidate_count", "reveal_count", "holdout_windows_registered", "holdout_windows_exhausted", "last_revealed_at"}`; `raw_trial_count` counts distinct revealed validation triples (the audit upper bound the governor consumes). The closed key set is not touched by the AMEND-8 note above — no seat-weight key is pre-reserved.
  - `def get_trial(self, trial_id: str) -> TrialRecord:`; `def replay(self, family_id: str) -> "TrialLedger":` — fold reconstruction from the event journal proving append-only cross-restart semantics.

**Required invariants:**

1. reserve→reveal for the same triple across two "processes" (two ledger instances over the same stores) yields one raw trial: the second `reserve_validation` returns a reused record and `raw_trial_count` stays 1;
2. same idempotency key + same content replays the stored record; same key + different content raises `IdempotencyConflict`;
3. holdout one-shot: second candidate on the same `(family, window)` fails before any evaluation callable runs; same candidate replay returns the original reservation;
4. every terminal holdout status closes the lease; `exhaust_holdout` twice returns byte-identical receipts; no API can move `exhausted → reserved`;
5. window registration rejects overlap, missing prior acknowledgment, unmatured `matured_at`, and attestation mismatch;
6. caller-minted family: passing a hand-built `StudyFamily` is impossible (the ledger only accepts `StudySpec` and derives internally); a tampered family head cell payload fails `verify_family_attestation` on read;
7. main-partition events for holdout terminals carry only `HoldoutReceipt` (asserted by scanning the journal payload schema refs); the full holdout record exists only in the `audit` partition — appended as an `audit`-partition `TrialRevealed`/`TrialExhausted` with `TrialRecord@1` payload, which Task 3's partition-conditional rule admits;
8. `replay` reproduces `effective_trial_stats` and lease states exactly.

Holdout lease state machine frozen by this task (spec 运行不变量 line 937):

| From (`status`/`lease_state`) | To | Trigger | Notes |
|---|---|---|---|
| — | `reserved`/`reserved` | `reserve_holdout` (CAS from `None`) | before any data read |
| `reserved`/`reserved` | `revealed`/`consumed` | `exhaust_holdout(status="revealed")` | via `SealedResultStore.put` |
| `reserved`/`reserved` | `failed`/`exhausted` | `exhaust_holdout(status="failed")` | window spent |
| `reserved`/`reserved` | `timed_out`/`exhausted` | `exhaust_holdout(status="timed_out")` | window spent |
| `reserved`/`reserved` | `inconclusive`/`exhausted` | `exhaust_holdout(status="inconclusive")` | window spent |
| any terminal | same terminal | repeated `exhaust_holdout` | idempotent original receipt; **no reopen edge exists** |

- [ ] **Step 1: Write failing ledger tests** covering the eight invariants with the Phase 2 in-memory stores and a fixed deterministic clock, plus:

1. reserve → reveal happy path emits exactly `TrialReserved` then `TrialRevealed` in the `main` journal of `run_id == family_id`, each with the Task 3 payload schema;
2. second ledger instance over the same stores (`replay`) sees the revealed triple and `reserve_validation` returns `reused_from_trial_id` without a new `TrialReserved` raw trial;
3. `reveal_validation` on a reused record with a metric name outside the recorded tuple → `TrialReuseViolation`;
4. `reserve_holdout` race: two candidates, same window — first wins the CAS, second raises `HoldoutLeaseExhaustedError` with zero evaluator involvement;
5. every terminal `exhaust_holdout` path leaves the `main` journal free of any `TrialRecord`-payload holdout terminal (schema-ref scan) while the `audit` partition holds the full `TrialRecord@1` — both events constructible under Task 3's partition-conditional payload rule;
6. window registration matrix: overlap with a prior window / `prior_window_ids` missing an existing id / `matured_at > clock.now()` / attestation digest mismatch — each rejected with its named error; a valid non-overlapping later window (boundary-touching `start_at == prior end_at` included) registers and `effective_trial_stats["holdout_windows_registered"]` increments;
7. `effective_trial_stats` closed key set equality and value correctness after a scripted sequence (2 distinct triples revealed, 1 reuse, 1 holdout exhausted);
8. same idempotency key + different semantic content on any method → `IdempotencyConflict`; injected UoW failure mid-`reserve_holdout` leaves no lease cell, no payload, no event (all-or-none).

Run now: `pytest tests/orchestration/test_trial_ledger.py -v` — Expected: FAIL on missing module/behavior.

- [ ] **Step 2: Implement** `trial_ledger.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_trial_ledger.py tests/orchestration/test_trial_events.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/trial_ledger.py tests/orchestration/test_trial_ledger.py
git commit -m "feat(orchestration): event-sourced cross-run TrialLedger with one-shot holdout lease (phase4 task 5)"
```

---

## Task 6: Sealed result store + sealed evaluator gateway (`sealed.py`)

**Files:**
- Create: `guanlan_v2/orchestration/sealed.py`
- Test: `tests/orchestration/test_sealed_holdout.py`

**Consumes:** Task 2 `HoldoutLease`/`SealedEvaluationRecord`/`SealedCapability`/`HoldoutReceipt`; Task 5 `TrialLedger`; Phase 2 `PayloadStore` (with `namespace="sealed"`), `AuthoritativeClock`; Phase 1 `PUBLIC_PAYLOAD_NAMESPACES`.

**Produces:**

- Domain constants: `LEASE_SIGNATURE_DOMAIN = "holdout-lease-v1"`, `CAPABILITY_SIGNATURE_DOMAIN = "sealed-capability-v1"`. v1 signatures are domain-tagged recomputable digests over the record's semantic fields + `nonce`/`token_id` (documented: deterministic verification, not cryptographic secrecy; a later reviewed change may swap in keyed signing without ABI change).
- Errors: `SealedAccessError(Exception)`, `LeaseVerificationError(SealedAccessError)`, `CapabilityVerificationError(SealedAccessError)`.
- `def issue_holdout_lease(*, trial: TrialRecord, clock: AuthoritativeClock, ttl_seconds: PositiveInt, nonce: NonEmptyStr) -> HoldoutLease:` — only callable with a `stage="sealed_holdout", status="reserved"` record; signature computed over `{lease_id, trial_id, candidate_hash, holdout_window_id, issued_at, expires_at, nonce}`.
- `def verify_holdout_lease(lease: HoldoutLease, *, trial: TrialRecord, clock: AuthoritativeClock) -> None:` — recomputes the signature, checks `trial_id`/`candidate_hash`/`holdout_window_id` binding and expiry against the authoritative clock.
- `def issue_sealed_capability(*, scope: Literal["final_report", "human_review"], principal_id: NonEmptyStr, clock: AuthoritativeClock, ttl_seconds: PositiveInt) -> SealedCapability:` and `def verify_sealed_capability(capability: SealedCapability, *, clock: AuthoritativeClock) -> None:`.
- `class SealedResultStore:` — `def __init__(self, *, payload_store, ledger: TrialLedger, clock, registry):`
  - `def put(self, lease_id: str, record: SealedEvaluationRecord) -> HoldoutReceipt:` — verifies the lease is the live reservation for `record.trial_id`; persists the `SealedEvaluationRecord` payload with `namespace="sealed"` (and its curve payload, when present, also `sealed`); then calls `ledger.exhaust_holdout(trial_id, status="revealed", result_digest=record.result_digest)` in the same UoW; returns the receipt. A second `put` for the same trial returns the original receipt without writing.
  - `def get(self, trial_id: str, *, capability: SealedCapability) -> SealedEvaluationRecord:` — the **only** read path; verifies scope/expiry/signature; any other dereference attempt of a sealed ref through public plumbing is refused by the Phase 1/2 namespace rules.
- `class SealedEvaluatorGateway:` — process/authority-separated from the Optimizer (spec §2.2): `def __init__(self, *, study: StudySpec, ledger: TrialLedger, sealed_store: SealedResultStore, evaluate_holdout: Callable[[TypedPayloadRef, HoldoutWindow], ValidationMetrics], clock, lease_ttl_seconds: PositiveInt, timeout_seconds: PositiveInt, data_snapshot_hash: DigestHex, code_prompt_model_hash: DigestHex):`
  - `def reserve_and_lease(self, frozen_candidate_ref: TypedPayloadRef, *, window_ref: TypedPayloadRef, idempotency_key: str) -> tuple[TrialRecord, HoldoutLease]:` — reservation + TrialRecord write strictly precede any window data read (spec §7);
  - `def evaluate_once(self, frozen_candidate_ref, *, holdout_reservation_id, lease_token: HoldoutLease) -> HoldoutReceipt:` — spec-verbatim signature. Verifies the lease against the reserved record; if the trial is already terminal, returns the original receipt (idempotent recovery, no reopen); runs the deterministic `evaluate_holdout` callable bounded by `timeout_seconds`; success → build `SealedEvaluationRecord` (metrics payload = the full `ValidationMetrics` dump + any extra curves, sealed-only) → `sealed_store.put`; evaluator exception → `exhaust_holdout(status="failed")`; timeout → `"timed_out"`; metrics with no decidable content (all-`None` core) → `"inconclusive"`. Every path returns a `HoldoutReceipt` and every path exhausts the window.
- Public-pool rejection helper test surface: `def assert_no_sealed_refs(refs: Iterable[PayloadRef | TypedPayloadRef]) -> None` — raises `SealedAccessError` on any non-public namespace; used by adapters before staging anything into the Phase 2 `ArtifactPool` (defense-in-depth on top of the Phase 1/2 validators).

**Required invariants:**

1. Optimizer-side code holds no read path: `optimize.py`/`evaluator.py` (Tasks 7–8) import neither `SealedResultStore` nor `SealedEvaluationRecord` (module-inspection test lives here and re-runs after Tasks 7–8);
2. `evaluate_once` with a forged/expired/mismatched lease fails before the evaluation callable runs (spy callable asserts zero invocations);
3. failure/timeout/inconclusive each exhaust the window: a subsequent `reserve_and_lease` for a different candidate on the same window raises `HoldoutLeaseExhaustedError`;
4. crash recovery: re-calling `evaluate_once` after a terminal state returns the byte-identical original receipt and does not re-run the callable;
5. sealed payloads are invisible publicly: the main-partition journal contains no `sealed`-namespace `payload_ref`; `PayloadStore.get` of the sealed record via public expected-schema paths is refused; `SealedResultStore.get` with a `human_review` capability succeeds and with an expired one fails;
6. the receipt's `result_digest` matches the sealed record's `result_digest` — public opaque digest, no dereference.

Sealed access-control matrix frozen by this task:

| Reader | Path | Result |
|---|---|---|
| Optimizer / `improve` / L3 | any | structurally impossible (no import, no capability) |
| public journal subscriber | `main` events | sees `HoldoutReceipt` only (opaque status + digest) |
| `PayloadStore.get` without capability | sealed ref | refused (namespace rules) |
| `ArtifactPool.history/subscribe/get_typed` | sealed ref | refused; `assert_no_sealed_refs` additionally guards adapter staging |
| `SealedResultStore.get` + valid `final_report` capability | trial_id | full `SealedEvaluationRecord` |
| `SealedResultStore.get` + valid `human_review` capability | trial_id | full `SealedEvaluationRecord` |
| `SealedResultStore.get` + expired/forged capability | trial_id | `CapabilityVerificationError` |

- [ ] **Step 1: Write failing tests** for the six invariants with scripted evaluator callables (success / raise / sleep-past-timeout / all-None-metrics → `inconclusive`), the access-control matrix above row by row, and lease verification vectors: tampered `candidate_hash`, tampered `signature`, expired lease (clock advanced past `expires_at`), lease bound to a different `trial_id` — each refused before the spy evaluator runs.

Run now: `pytest tests/orchestration/test_sealed_holdout.py -v` — Expected: FAIL on missing module.

- [ ] **Step 2: Implement** `sealed.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_sealed_holdout.py tests/orchestration/test_trial_ledger.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/sealed.py tests/orchestration/test_sealed_holdout.py
git commit -m "feat(orchestration): sealed result store + one-shot sealed evaluator gateway (phase4 task 6)"
```

---

## Task 7: Evaluator four layers (`evaluator.py`)

**Files:**
- Create: `guanlan_v2/orchestration/evaluator.py`
- Test: `tests/orchestration/test_evaluator.py`

**Consumes:** Task 1 `ValidationMetrics`/`GateResult`/`Feedback`/`HonestyGateReport`/`OptimizeCandidate`; Task 4 `Governor`; the `run_graph` outcome shape (`{"ok", "reason", "terminal", "metrics", "exprs", "has_ml", "node_results", "node_errors", "warnings", "elapsed_sec"}`, executor.py:698-701) and `metrics_of_terminal` six keys (executor.py:121-136).

**Produces:**

- **L0 (pre/post honesty gates, deterministic, run before/around the expensive evaluation):**
  - `def l0_candidate_gate(candidate: OptimizeCandidate) -> HonestyGateReport:` — cheap structural refusal before any evaluation spend: non-empty nodes, edges reference known node ids, params JSON-shaped and finite, a classifiable terminal exists (dish taxonomy parity with `loop._pick_dish`: backtest / compose(≥2 exprs) / single-expr report; unclassifiable → refuse).
  - `def l0_run_gate(outcome: Mapping[str, Any], *, required_chain_node_ids: tuple[str, ...]) -> HonestyGateReport:` — post-run, pre-reveal: `ok` must be true with a present terminal; any `node_errors` entry on the required chain (the terminal's ancestry, including the recipe's ML node — fidelity-guard parity with loop.py:180-186) refuses metric acceptance; `incomplete/failed/blocked`-style required-chain hits are refused rather than scored. Refusal means the trial is revealed as `status="failed"` with no metrics — never silently scored.
- **L1 (deterministic metrics normalization):** `def l1_normalize_run_graph_metrics(outcome: Mapping[str, Any]) -> ValidationMetrics:` — maps the six `metrics_of_terminal` keys onto `ValidationMetrics(source="run_graph")`; missing/NaN inputs become `None` (honest absence); no key is invented; extra diagnostic terminals are ignored ("照跑存档不过门").
- **L2 (governance delegate):** `def l2_govern(*, governor: Governor, family, stats, returns, perf_matrix, candidate, split_spec) -> GovernanceReport:` — thin pass-through to `Governor.govern` so the four layers present one module surface; contains no additional logic (asserted: same object as calling governor directly).
- **L3 (attribution feedback):**
  - `class AttributionPort(Protocol): def attribute(self, *, goal: str, metrics: ValidationMetrics, candidate: OptimizeCandidate, constraints: str) -> Feedback: ...` — the injectable LLM seam; production binding arrives in Task 10 (research adapter) via the existing daemon-thread self-POST critique bridge; nothing in this module performs HTTP.
  - `def l3_feedback(*, goal: str, metrics: ValidationMetrics, candidate: OptimizeCandidate, evidence_artifact_ids: tuple[str, ...], port: AttributionPort | None, constraints: str = "") -> Feedback:` — deterministic pre-narrowing first: if the candidate graph cannot be narrowed to ≤2 responsible node ids from `metrics`/structure alone and no port is supplied, return `Feedback(ambiguous=True, source="deterministic", ...)` honestly; with a port, the port's answer is validated against the contract matrix (a port returning >2 targets or unsourced evidence ids is coerced to ambiguous with a reason, never trusted blindly); port failure → `Feedback(source="rule", ...)` with the rule-fallback visibility badge text `"(规则兜底·非 LLM)"` embedded in `reason` (parity with loop.py:392-395). L3 reads only validation metrics/curves/explicit artifact refs — the function signature admits no sealed types, and the module import test (Task 6) pins it.

**Required invariants:**

1. L0 refusal is cheaper than evaluation: `l0_candidate_gate` never touches the evaluation callable (pure function of the candidate);
2. a required-chain node error refuses metrics even when the terminal parsed (fidelity-guard parity vector);
3. L1 never fabricates: an outcome missing `sharpe` yields `sharpe=None`, not 0.0; NaN rank_ic yields `None`;
4. L2 is a pure delegate (identical `GovernanceReport` object/value as direct governor call);
5. L3 honest ambiguity: un-narrowable + no port → `ambiguous=True`; port raising → `source="rule"` fallback with visible badge text; port returning 3 targets → coerced ambiguous;
6. no sealed import anywhere in `evaluator.py` (`sys.modules`/AST inspection).

- [ ] **Step 1: Write failing tests** for the six invariants with scripted outcomes and a scripted `AttributionPort`, including:

1. `l0_candidate_gate` refusal vectors: empty nodes; edge to unknown node id; NaN param; graph with no classifiable terminal (no backtest/compose/expr) — each `passed=False` with a named reason and `checked` listing every executed check;
2. `l0_run_gate` vectors: `ok=False` outcome refused; `node_errors` naming a required-chain node (the terminal's ancestor / recipe ML node) refused even though `metrics` parsed; `node_errors` on an off-chain diagnostic node accepted (照跑存档不过门);
3. `l1_normalize_run_graph_metrics` vectors: full six-key outcome maps 1:1; missing `sharpe` → `None`; `rank_ic=float("nan")` in the raw dict → `None` (the strict contract would reject NaN, so the normalizer must convert first); `oos_verdict` absent → `None`;
4. L2 delegate identity: `l2_govern(...)` value-equals `Governor.govern(...)` on the same inputs;
5. L3 matrix: no port + un-narrowable graph → `ambiguous=True, source="deterministic"`; port raising → `source="rule"` with `"(规则兜底·非 LLM)"` in `reason`; port returning 3 targets → coerced ambiguous with coercion reason; port returning a valid 1-target `Feedback` → passed through unmodified;
6. AST/import scan: `evaluator.py` contains no `SealedEvaluationRecord`/`SealedResultStore`/`sealed` module import.

Run now: `pytest tests/orchestration/test_evaluator.py -v` — Expected: FAIL on missing module.

- [ ] **Step 2: Implement** `evaluator.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_evaluator.py tests/orchestration/test_sealed_holdout.py -v` — Expected: PASS (including the re-run sealed-isolation module inspection).

```bash
git add guanlan_v2/orchestration/evaluator.py tests/orchestration/test_evaluator.py
git commit -m "feat(orchestration): four-layer evaluator with honest L0/L2/L3 refusals (phase4 task 7)"
```

---

## Task 8: Optimize state machine (`optimize.py`)

**Files:**
- Create: `guanlan_v2/orchestration/optimize.py`
- Test: `tests/orchestration/test_optimize.py`

**Consumes:** Tasks 1–7; Phase 1 `DataContext`, `ExperimentStatus`; Phase 2 `PayloadStore`, `EventStore` (`EXPERIMENT_STATE_CHANGED`), `RuntimeStateCellStore` (`trial.experiment_head.v1`), `AuthoritativeClock`. Generalizes `research/loop.py:297 run_research_loop` — stall guard (loop.py:373-391), honest termination (loop.py:310-323), draft-only routing boundary, append-only rounds (loop.py:351).

**Produces:**

- `OPTIMIZE_MAX_ROUNDS = 8` — loop-level hard clamp (`max_rounds = max(1, min(max_rounds, OPTIMIZE_MAX_ROUNDS))` inside `run_optimize` itself, closing the "endpoint clamps are bypassable" seam of the `POST /research/loop/start` clamps — name-authoritative, lines approximate: the clamp doc sits at research/api.py:39 and the clamping happens in the endpoint body; locate by endpoint name per the Task 0 correction clause); adapters may clamp tighter.
- `class MaturityPending(Exception):` — `resume_after: UtcDateTime`, `wakeup_key: NonEmptyStr`; raised by an `evaluate_validation` binding (e.g. the Phase 5 matured-case grader) whose data has not matured.
- `class OptimizeRoundStore(Protocol):` — `def append_round(self, round: OptimizeRound, *, idempotency_key: str) -> None:` (raises on failure — loud, unlike the research jsonl store's bool swallow); `def rounds(self, experiment_id: str) -> tuple[OptimizeRound, ...]:`. Production implementation `PayloadRoundStore` persists each round as a `main` payload + `ExperimentStateChanged`-correlated audit trail; rounds are append-only and never rewritten.
- `class ExperimentStateStore:` — `def save(self, state: OptimizeRunState, *, idempotency_key: str) -> None:` (payload put + `EXPERIMENT_STATE_CHANGED` main event + `trial.experiment_head.v1` CAS in one UoW); `def load(self, experiment_id: str) -> OptimizeRunState | None:`; `def load_by_wakeup_key(self, wakeup_key: str) -> OptimizeRunState | None:`.
- ```python
  def run_optimize(*, seed: OptimizeCandidate, ctx: DataContext, study: StudySpec,
                   split_spec: SplitSpec, max_rounds: int, governor: Governor,
                   evaluate_validation: Callable[[OptimizeCandidate, DataContext], ValidationMetrics],
                   gate: Callable[[ValidationMetrics], GateResult],
                   improve: Callable[[OptimizeCandidate, ValidationMetrics, Feedback], OptimizeCandidate],
                   ledger: TrialLedger, rounds: OptimizeRoundStore, states: ExperimentStateStore,
                   payload_store: PayloadStore, experiment_id: str, clock: AuthoritativeClock,
                   code_prompt_model_hash: DigestHex,
                   attribution: AttributionPort | None = None,
                   goal: str = "") -> OptimizeResult: ...
  ```
  Spec-frozen parameter names (`seed, ctx, study, split_spec, max_rounds, governor, evaluate_validation, gate, improve`) plus keyword-only service dependencies. Per-round sequence:
  1. persist the candidate payload idempotently by `candidate_hash` (its `PayloadRef.object_id` is the honest "candidate artifact id" — a payload object, not a Phase 2 run Artifact; documented);
  2. **stall guard**: if `candidate_hash` equals the previous round's, re-run `improve` once with the stall-warning constraint appended (parity with `【停滞警告】`, recorded as `stall_retry=True`); still identical → terminate `stop_reason="stalled"`, status `FAILED`, honest interruption;
  3. **L0** `l0_candidate_gate` — refusal archives the round (`l0` report, no trial) and terminates `stop_reason="l0_refused"` unless `improve` can be consulted with a deterministic refusal `Feedback`;
  4. `ledger.reserve_validation(...)` with `data_snapshot_hash=ctx.data_snapshot_content_digest`; a reused revealed triple skips evaluation and reuses the recorded result (idempotent, no re-reveal — asserted);
  5. `evaluate_validation(cand, ctx)`; `MaturityPending` → persist `OptimizeRunState(status=WAITING_FOR_MATURITY, resume_after, wakeup_key, candidate_hash)` and return an `OptimizeResult` carrying that state (no busy waiting, process frees); evaluation exception → reveal-as-failed round archived, continue to next round (parity with loop.py:159-169 eval-fail-continues);
  6. **post L0** `l0_run_gate` where the binding supplies a raw outcome; refusal → trial `failed`, round archived, continue;
  7. `ledger.reveal_validation(trial_id, result_artifact_id=<metrics payload object id>, result_digest=<metrics semantic digest>)` after persisting the `ValidationMetrics` payload;
  8. `gate(metrics)`: `passed` → status `PASSED_VALIDATION`, `best_candidate_artifact_id` = winning candidate payload id, stop; `unavailable` → recorded distinctly, treated as not-passed for progression;
  9. **L2** `l2_govern(...)` with `stats=ledger.effective_trial_stats(study)`; `governor.should_stop(report)` → terminate `stop_reason` = the budget reason;
  10. **L3** `l3_feedback(...)`; `improve(cand, metrics, feedback)`; improve failure → honest termination `stop_reason="improve_failed"` (never a template fallback);
  11. archive `OptimizeRound` (append-only) and persist `OptimizeRunState(status=RUNNING)` each round.
  Best-round selection on exhaustion (parity loop.py:399-408): first gate-passed round wins outright, else max observed rank_ic among revealed rounds; zero revealed rounds → status `FAILED`, `stop_reason="no_revealed_rounds"`.
- `def resume_optimize(*, wakeup_key: str, seed, ctx, study, split_spec, max_rounds, governor, evaluate_validation, gate, improve, ledger, rounds, states, payload_store, experiment_id, clock, code_prompt_model_hash, attribution=None, goal="") -> OptimizeResult:` — loads state by `wakeup_key`; unknown key → `TrialLedgerError`; state not `WAITING_FOR_MATURITY` → rebuild and return the persisted terminal result idempotently (no new rounds); `clock.now() < resume_after` → return the waiting state unchanged; otherwise continue the loop from the archived round index — only matured batches are processed, and a second concurrent/duplicate wakeup with the same key returns the identical result (idempotent via the experiment head CAS).
- `def finalize_candidate(*, optimized: OptimizeResult, sealed_evaluator: SealedEvaluatorGateway) -> HoldoutReceipt:` — spec-verbatim signature. Requires `optimized.state.status == ExperimentStatus.PASSED_VALIDATION`; transitions the persisted state to `SEALED_EVALUATING`; asks the gateway for `reserve_and_lease(frozen_candidate_ref, window_ref, idempotency_key)` (candidate ref rebuilt from `best_candidate_artifact_id` + recorded schema — a `TypedPayloadRef`), then `evaluate_once(...)`; persists terminal state `COMPLETED` (receipt status `revealed`) or `FAILED` otherwise, and returns the receipt. Holdout metrics never enter the returned `OptimizeResult`, any round archive, `improve`, L3 or memory: the only holdout fact `optimize.py` ever holds is the opaque `HoldoutReceipt`.

**Required invariants:**

1. scripted three-round convergence: weak → weak → pass yields 3 archived rounds, 3 validation trials, `PASSED_VALIDATION`, correct best id;
2. stall: identical improve output twice → exactly one `stall_retry=True` round then honest `stalled` stop; param-only change (same ids, new params) does **not** count as stall (candidate_hash differs — parity with graph-signature semantics);
3. idempotent reuse: re-running the same experiment with identical seed/ctx/study/split re-uses revealed triples — the evaluation callable is invoked zero times on the replay run and `raw_trial_count` does not grow;
4. `MaturityPending` → persisted `WAITING_FOR_MATURITY` with `resume_after`/`wakeup_key`; duplicate `resume_optimize` before maturity returns the same waiting state; after maturity, exactly one continuation processes and a concurrent duplicate returns the identical terminal result;
5. governor budget exhaustion stops the loop with the budget `stop_reason` before another evaluation call (spy assert);
6. improve/propose failure and L0 refusal terminate honestly with archived evidence — no fallback candidate is fabricated;
7. `max_rounds=99` is clamped to `OPTIMIZE_MAX_ROUNDS` inside the loop;
8. `finalize_candidate` on a non-passed result raises before any gateway call; on a passed result, the receipt is returned and no sealed metric value appears in any round/state/result payload (deep scan of persisted payloads for the sealed metric sentinel value used by the scripted holdout evaluator).

`ExperimentStatus` transitions frozen by this task (persisted through `ExperimentStateStore`, folded from `EXPERIMENT_STATE_CHANGED` events):

| From | To | Trigger |
|---|---|---|
| — | `RUNNING` | `run_optimize` first round |
| `RUNNING` | `RUNNING` | round archived, loop continues |
| `RUNNING` | `WAITING_FOR_MATURITY` | `MaturityPending` raised by `evaluate_validation` |
| `WAITING_FOR_MATURITY` | `RUNNING` | matured `resume_optimize(wakeup_key)` |
| `RUNNING` | `PASSED_VALIDATION` | gate passed |
| `RUNNING` | `FAILED` | stalled / budgets / improve failure / L0 refusal / no revealed rounds |
| `PASSED_VALIDATION` | `SEALED_EVALUATING` | `finalize_candidate` entry |
| `SEALED_EVALUATING` | `COMPLETED` | receipt `status="revealed"` |
| `SEALED_EVALUATING` | `FAILED` | receipt `failed`/`timed_out`/`inconclusive` |

No other edge exists; `ExperimentStateStore.save` rejects an undeclared transition via the `trial.experiment_head.v1` CAS (expected-head mismatch).

- [ ] **Step 1: Write failing state-machine tests** with scripted `evaluate_validation`/`gate`/`improve` callables over Phase 2 in-memory stores and a deterministic clock, covering the eight required invariants plus:

1. the full transition table above (parameterized: every legal edge reachable, one illegal edge — `COMPLETED → RUNNING` — rejected);
2. round archives are append-only: after a terminal state, `rounds(experiment_id)` is stable across a duplicate `resume_optimize` call and no round is ever rewritten (object equality of the tuple);
3. gate `unavailable` (metrics all-`None`) is archived distinctly (`gate.status == "unavailable"`) and does not count as passed;
4. `MaturityPending` mid-round leaves no half-revealed trial: the reserved trial stays `reserved`, and the matured continuation reveals it exactly once;
5. candidate payload idempotency: two rounds with the same `candidate_hash` (stall retry) persist one payload object;
6. `finalize_candidate` happy path drives `PASSED_VALIDATION → SEALED_EVALUATING → COMPLETED` with exactly one gateway `reserve_and_lease` and one `evaluate_once` (spy counts), and re-invocation returns the identical receipt without new gateway calls.

Run now: `pytest tests/orchestration/test_optimize.py -v` — Expected: FAIL on missing module.

- [ ] **Step 2: Implement** `optimize.py`.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_optimize.py tests/orchestration/test_sealed_holdout.py tests/orchestration/test_evaluator.py -v` — Expected: PASS (sealed-isolation inspection re-runs green over the finished `optimize.py`).

```bash
git add guanlan_v2/orchestration/optimize.py tests/orchestration/test_optimize.py
git commit -m "feat(orchestration): run_optimize state machine with maturity wakeup and sealed finalize (phase4 task 8)"
```

---

## Task 9: Cumulative Phase 4 registry/catalog + goldens

**Files:**
- Modify: `guanlan_v2/orchestration/trial.py` (chain exports)
- Create: `config/orchestration/materials/optimize/joint_gate_v1.md`
- Create: `tests/orchestration/golden/phase4_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase4_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase4_registry.py`

**Consumes:** Phase 3 `PHASE3_FULL_REGISTRY_DIGEST`/`build_phase3_full_registry`, `PHASE3_FULL_CATALOG_DIGEST`/`build_phase3_full_catalog` outputs; Phase 1 `SchemaRegistry`, `build_catalog_snapshot`, `catalog_material_digest`, `ResolvedTextMaterial`.

**Produces:**

- `PHASE4_PUBLIC_MODELS` — exactly 16 registered contracts: `StudySpec`, `StudyFamily`, `SplitSpec`, `OptimizeCandidate`, `ValidationMetrics`, `GovernanceReport`, `Feedback`, `OptimizeRound`, `HoldoutWindow`, `TrialRecord`, `OptimizeRunState`, `OptimizeResult`, `HoldoutReceipt`, `HoldoutLease`, `SealedEvaluationRecord`, `SealedCapability`.
- `PHASE4_INTERNAL_MODELS` — reviewed-reason map for the deliberately unregistered surface: `GateResult` (nested round component; named after spec §2.2, distinct from the Phase 1 Plan-gate `GateResult` which stays internal in `spec.py` — neither is registered, so no `SchemaConflictError` is possible; a later phase registering either requires a reviewed rename decision recorded here), `HonestyGateReport` (nested component), `Governor`/`TrialLedger`/`SealedResultStore`/`SealedEvaluatorGateway`/`ExperimentStateStore` (services), `AttributionPort`/`OptimizeRoundStore` (ports), `MaturityPending` (exception).
- `PHASE4_BASE_REGISTRY_DIGEST` (declared equal to the reviewed implemented `PHASE3_FULL_REGISTRY_DIGEST` value) + `PHASE4_REGISTRY_DIGEST` + `def build_phase4_registry(expected_phase3_full_digest: DigestHex) -> SchemaRegistry:` — verifies the Phase 3 full manifest/digest first (any other base digest rejected), registers the Phase 3 cumulative set + `PHASE4_PUBLIC_MODELS`, seals, returns a fresh sealed instance per call. Golden `phase4_schema_manifest_v1.json` frozen by review; inherited entries byte-identical to the Phase 3 full golden; no upstream golden regenerated; no "latest" alias. The Task 1 AMEND-6 pattern-replay extension (`candidate_kind="pattern_definition"`, `source="pattern_replay"`, `profit_loss_ratio`/`n_occurrences`) is already part of `OptimizeCandidate@1`/`ValidationMetrics@1` at this freeze — the curator phase needs no `@2` bump.
- `PHASE4_BASE_CATALOG_DIGEST` (= reviewed `PHASE3_FULL_CATALOG_DIGEST`) + `PHASE4_CATALOG_DIGEST` + `def build_phase4_catalog_snapshot(phase3_full_snapshot: WorkerCatalogSnapshot, *, joint_gate_material: ResolvedTextMaterial, resolved_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot:` — rejects any base snapshot whose `catalog_digest != PHASE4_BASE_CATALOG_DIGEST`; adds exactly one `ContentManifestEntry` of `kind="gate_metric"` (`id="optimize.joint_gate"`, version `"1"`) sourced from `config/orchestration/materials/optimize/joint_gate_v1.md`, documenting the reviewed research joint-gate semantics (`rank_ic >= min_rank_ic` AND `oos_verdict == "robust"` AND `sharpe > 0`); workers/capabilities unchanged in Phase 4. Golden `phase4_catalog_manifest_v1.json`.
- Per Task 5, `PHASE4_STATE_CELL_NAMESPACES` is asserted here as the canonical sealed startup union (Phase 3 seven + Phase 4 four, lexicographic).

**Required invariants:**

1. `build_phase4_registry` with a wrong expected digest raises before any registration;
2. every inherited schema's JSON-schema digest is byte-identical to its Phase 3 full golden entry;
3. `PHASE4_REGISTRY_DIGEST` matches the reviewed golden and is registration-order independent;
4. the Phase 4 public/internal partition over `trial.py`/`governor.py`/`trial_ledger.py`/`sealed.py`/`evaluator.py`/`optimize.py` is exhaustive and disjoint (mirror of the Phase 1 completeness firewall, scoped to Phase 4 modules);
5. catalog build with a tampered material byte fails digest verification; the material is UTF-8 no-BOM;
6. old Plans binding upstream digests remain resolvable through `SchemaRegistryResolver` alongside the Phase 4 digest (no reinterpretation).

- [ ] **Step 1: Write failing chain tests** covering:

1. `build_phase4_registry(expected_phase3_full_digest="0"*64)` raises before any registration; the correct digest builds a sealed registry whose `registry_digest == PHASE4_REGISTRY_DIGEST`;
2. golden equality: the built manifest matches `phase4_schema_manifest_v1.json` entry-for-entry; every key inherited from the Phase 3 full golden has a byte-identical `json_schema_digest`; the Phase 1/2/3 golden files' bytes are unchanged (sha256 pins);
3. registration-order independence: two builds with shuffled registration order produce the same digest;
4. Phase 4 completeness firewall: every public `ContractModel` subclass defined in the six Phase 4 modules appears in exactly one of `PHASE4_PUBLIC_MODELS`/`PHASE4_INTERNAL_MODELS` (exhaustive + disjoint), and no `PHASE4_PUBLIC_MODELS` member lacks a closed `schema_version`;
5. `build_phase4_catalog_snapshot` with a base snapshot whose digest ≠ `PHASE4_BASE_CATALOG_DIGEST` raises; the correct base plus the joint-gate material yields `PHASE4_CATALOG_DIGEST` matching `phase4_catalog_manifest_v1.json`; one flipped byte in the material file fails `catalog_material_digest` verification; the material parses as UTF-8 without BOM;
6. the new `ContentManifestEntry` is exactly `(kind="gate_metric", id="optimize.joint_gate", version="1")` and the worker/capability manifests are unchanged from the Phase 3 full snapshot;
7. `SchemaRegistryResolver` resolves the Phase 1, Phase 2, Phase 3 data-only, Phase 3 full and Phase 4 digests simultaneously; a Plan bound to an upstream digest is not reinterpreted;
8. `PHASE4_STATE_CELL_NAMESPACES` equals the sorted union of the reviewed Phase 3 seven and Phase 4 four, with no duplicates and no `"latest"`-style alias anywhere in the module namespace.

Run now: `pytest tests/orchestration/test_phase4_registry.py -v` — Expected: FAIL on missing exports/goldens.

- [ ] **Step 2: Implement chain exports + material; freeze goldens by review** (record the computed digests once, hand-review, commit; tests never regenerate them).
- [ ] **Step 3: Run the full orchestration suite and commit**

Run: `pytest tests/orchestration -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/trial.py config/orchestration/materials/optimize/joint_gate_v1.md tests/orchestration/golden/phase4_schema_manifest_v1.json tests/orchestration/golden/phase4_catalog_manifest_v1.json tests/orchestration/test_phase4_registry.py
git commit -m "feat(orchestration): cumulative phase4 registry/catalog chain with reviewed goldens (phase4 task 9)"
```

---

## Task 10: Factor-research adapter regression (closing)

Prove the existing factor-research loop's behavior is preserved through the new state machine as an adapter, and be honest about what stays on the old path.

**Files:**
- Create: `guanlan_v2/research/optimize_adapter.py`
- Test: `tests/test_research_optimize_adapter.py` (beside `tests/test_research_loop.py`, reusing its `_wire` monkeypatch seam pattern)

**Consumes:** `research/loop.py` seams (`_call_generate`, `_call_critique`, `_run_graph_eval`, `_gate`, `_route_product`, `_write_lesson`, `_pick_dish`, `GATE_OOS_OK`, `_EVAL_OOS_FRAC`, `_CRITIQUE_CONSTRAINTS`), `research/store.py` display rows, `workflow.executor.run_graph`/`metrics_of_terminal`, Tasks 4–8.

**Produces:**

- `def candidate_from_graph(graph: dict[str, Any], *, params: dict[str, Any], parent_trial_id: str | None = None) -> OptimizeCandidate:` — wraps `OptimizeCandidate.build`; test proves its stall decisions match `wex.graph_signature` equality on the same graph pairs (positions ignored, param changes counted).
- `def research_study(goal: str, *, universe: str, freq: str, min_rank_ic: float) -> StudySpec:` — objective/label/universe/split digests from canonical dicts of the research parameters; display `objective=goal`.
- `def research_split_spec(freq: str) -> SplitSpec:` — `scheme="oos_fraction"`, `oos_frac=0.3` (the forced `_EVAL_OOS_FRAC` — without it `oos_verdict` never exists and the gate can never pass), `label_horizon=5`.
- `def research_evaluate_validation(cand: OptimizeCandidate, ctx: DataContext) -> ValidationMetrics:` — calls `wex.run_graph(graph, overrides={"universe", "freq", "oos_frac": 0.3, "start", "end"}, on_node=..., prefer_model_terminal=True)` exactly as `loop._run_graph_eval` does (canvas `/workflow/run` keeps `prefer_model_terminal=False` — zero behavior change), then `evaluator.l0_run_gate` + `l1_normalize_run_graph_metrics`.
- `def research_gate(metrics: ValidationMetrics, *, min_rank_ic: float) -> GateResult:` — joint gate parity with `loop._gate` (loop.py:284-292): NaN-checked `rank_ic >= min_rank_ic` AND `oos_verdict == GATE_OOS_OK` AND `sharpe > 0`.
- `def research_improve(cand: OptimizeCandidate, metrics: ValidationMetrics, feedback: Feedback) -> OptimizeCandidate:` — binds the existing `loop._call_critique` self-POST bridge with `_CRITIQUE_CONSTRAINTS` (+ stall-warning suffix when `feedback.reason` carries the stall marker); rule-fallback source visibility preserved. **Red line unchanged:** this seam is sync self-HTTP and therefore runs only in a daemon thread, never inside a coroutine (loop.py:4-8 rationale).
- `def run_research_optimize(run_id: str, goal: str, max_rounds: int, min_rank_ic: float, universe: str, freq: str, start: Optional[str], end: Optional[str], progress: Callable[..., None]) -> Dict[str, Any]:` — signature-identical to `run_research_loop`; drives `run_optimize` with in-memory Phase 2 stores, a per-run `TrialLedger`/`Governor(trial_budget=OPTIMIZE_MAX_ROUNDS, peek_budget=OPTIMIZE_MAX_ROUNDS)`, `code_prompt_model_hash = content_digest({"adapter": "research_loop", "constraints_digest": content_digest(_CRITIQUE_CONSTRAINTS), "generate_path": "/workflow/generate", "critique_path": "/workflow/critique"})` and `data_snapshot_hash` from a compatibility-grade `ctx` built over `content_digest({"universe", "freq", "start", "end"})` (badged `"legacy_data_binding"` — a full Phase 3 `build_data_context` binding is deferred to the Phase 9 帷幄 adapter); on gate pass routes products through the existing `loop._route_product` (draft-only three-channel routing untouched, including the ML fidelity guard and the `save_failed: 因子名已存在` duplicate-name expectation on same-(run, round) retries) and writes lessons via `loop._write_lesson`; appends display rows to the existing jsonl `research/store.py` for UI parity (best-effort bool is acceptable *there* because the authoritative record is the ledger).

**Honesty inventory — what stays on the old path (explicit, reviewed):** `POST /research/loop/start` and its 1..5/0..0.2 clamps (clamp doc at research/api.py:39, clamping applied in the endpoint body — name-authoritative, lines approximate per the Task 0 correction clause) continue to call `run_research_loop` unchanged; production cutover to the adapter is a later reviewed change with its own plan (Phase 9-adjacent), gated on live parity evidence. The old loop keeps its sha1 `graph_signature` stall key internally; the adapter's `candidate_hash` is proven decision-equivalent by test, not by patching the old loop. LLM propose (`_call_generate`) remains a pre-loop step the adapter also calls before seeding (`propose failure → honest termination, no template fallback` — parity). factorlib has no `"rejected"` status and none is introduced (`_VALID_SAVE_STATUS == {"", "draft"}`); holdout-consumed candidates are tracked in the ledger, not as a factorlib state.

**Required invariants (behavior-equivalence matrix, scripted seams shared with `tests/test_research_loop.py`'s `_wire` pattern):**

1. first-round pass: both paths stop at round 0, route one draft product with the same channel decision, same gate verdict;
2. stall: identical critique graph twice → both paths emit exactly one stall retry then honest interruption; param-change-only critique → neither path stalls;
3. rounds exhausted without pass: both paths archive the same number of rounds and pick the same best round (max rank_ic, gate-passed priority);
4. propose failure → both terminate honestly with a lesson and no product; eval failure mid-run → both continue to the next round;
5. rule-critique fallback prefix visibility appears in both paths;
6. adapter-only additions hold: every revealed round has a `TrialRecord` (`raw_trial_count` == revealed rounds), reused triples on a replay run skip evaluation, and `run_graph` is never called with `prefer_model_terminal` unset;
7. existing suites untouched and green: `pytest tests/test_research_loop.py tests/test_research_api.py tests/test_research_store.py -v`.

Seam wiring table (old loop seam → adapter binding; every left-hand symbol is patched identically in both suites so the equivalence tests script one behavior and assert two paths):

| `research/loop.py` seam | Adapter binding |
|---|---|
| `_call_generate(goal)` (loop.py:59-78) | pre-loop propose → `candidate_from_graph(...)` seed; failure → honest termination before `run_optimize` |
| `_run_graph_eval(graph, p, progress, k, max_rounds)` (loop.py:81-91) | `research_evaluate_validation` (same overrides incl. `oos_frac=0.3`, `prefer_model_terminal=True`) |
| `_gate(metrics, min_rank_ic)` (loop.py:284-292) | `research_gate` → Phase 4 `GateResult` (same three-way joint predicate) |
| `_call_critique(goal, metrics, graph, constraints)` (loop.py:59-78) | `research_improve` via `AttributionPort`-fed `improve` (constraints = `_CRITIQUE_CONSTRAINTS` + stall suffix) |
| stall: `wex.graph_signature` equality (loop.py:373-391) | `candidate_hash` equality inside `run_optimize` |
| `_route_product(...)` (loop.py:170-230) | called unchanged on gate pass (draft-only, three channels, fidelity guard) |
| `_write_lesson(goal, summary)` (loop.py:243-253) | called unchanged at wrap-up |
| `rstore.append_round/append_run` (store.py:29-34) | display rows appended unchanged; authoritative record = `TrialLedger`/`OptimizeRoundStore` |

- [ ] **Step 1: Write failing adapter tests** implementing the seven-point matrix with monkeypatched seams: replicate the `_wire(monkeypatch, tmp_path, evals, critique, generate)` helper from `tests/test_research_loop.py:44-60` into a shared fixture that wires both `run_research_loop` and `run_research_optimize` from one script, then assert pairwise-equal decisions (round counts, gate verdicts, product channel, lesson writes, honest-stop errors) for each scenario.

Run now: `pytest tests/test_research_optimize_adapter.py -v` — Expected: FAIL on missing adapter module.

- [ ] **Step 2: Implement** `optimize_adapter.py`.

- [ ] **Step 3: Run the closing regression set and commit**

Run: `pytest tests/test_research_optimize_adapter.py tests/test_research_loop.py tests/test_research_api.py tests/test_research_store.py tests/orchestration -v`

Also run: `python -m compileall -q guanlan_v2/orchestration guanlan_v2/research`

Expected: PASS.

```bash
git add guanlan_v2/research/optimize_adapter.py tests/test_research_optimize_adapter.py
git commit -m "feat(research): factor-research adapter over run_optimize with behavior-equivalence regression (phase4 task 10)"
```

---

## Phase 4 Exit Gates

Phase 4 is complete only when every gate below is checked by tests or a reviewed artifact.

### Upstream handoff and scope protection

- [ ] every Phase 1 (amended), Phase 2 and Phase 3 Exit Gate remains green; `test_phase4_handoff.py` passes with frozen reviewed evidence;
- [ ] Phase 4 imports, rather than redefines, digests/refs/registry/catalog/Plan/store/PIT/memory contracts; `TypedPayloadRef` is used for every typed evidence pair and plain `PayloadRef` only as a bare locator;
- [ ] the only upstream source touch is the additive `events.py` extension; no upstream golden was regenerated; `workflow/executor.run_graph` behavior and canvas `prefer_model_terminal` default are unchanged; the two `workflow/api.py` delegates are behavior-identical under existing tests;
- [ ] no dynamic Planner, Bootstrap Lane 0, shadow intent, debate, retry/repair or real trading authority was added; no curator workers #25/#26/#27, no pattern dictionary/registry (AMEND-6a), no seat-weight accounting (AMEND-8) and no judge swap-double-run audit (D12) were added; unrelated worktree changes are not staged.

### Contracts and identity

- [ ] all 16 registered + reviewed internal Phase 4 contracts pass their field/matrix tests; spec §8 field spellings verified verbatim;
- [ ] `candidate_hash` ignores layout and ordering, moves on type/params/edge changes;
- [ ] family identity is display-text-invariant; forged/renamed attestation is rejected; lineage requires parent + change_reason; budgets survive renames;
- [ ] a metrics-bearing public holdout record is unconstructible; `HoldoutReceipt` carries no dereferenceable ref and its field set is frozen.

### Events and guards

- [ ] `EventType` is exactly the 23-value Phase 4 set; per-type Trial payload rules enforced; pre-existing 20 values byte-identical;
- [ ] both absence guards are flipped to "absent from Phase 1 registry AND present in Phase 4 registry"; the completeness sweep still bars Trial/Holdout names from Phase 1 modules.

### Governor and TrialLedger

- [ ] `cscv_pbo`/`oos_verdict` extraction is delegate-equivalent; DSR/`make_splits` are imported, not copied; `effective_purge` encodes the label-horizon widening;
- [ ] L2 returns `unavailable` with reasons on insufficient samples/splits — never a fabricated governance score; `effective_n_trials <= raw_trial_count` always; trial/peek budget exhaustion stops the loop;
- [ ] reserve-before-read holds for every trial; identical candidate/data/split triples reuse idempotently without re-revealing more metrics; reservation failures raise loudly;
- [ ] one candidate per `(family, window)`; failed/timed_out/inconclusive all exhaust; recovery returns the original receipt; no reopen transition exists; window registration enforces matured, non-overlapping, later OOT data;
- [ ] ledger replay reproduces stats/lease states; the sealed startup state-cell union is exactly the reviewed Phase 3+4 namespace set.

### Sealed holdout isolation

- [ ] sealed metrics/curves exist only in the `sealed` namespace; the public journal and `ArtifactPool`/history/subscribe cannot dereference them; only `final_report`/`human_review` capabilities read via `SealedResultStore.get`;
- [ ] `optimize.py`/`evaluator.py` import no sealed type (module inspection); holdout metrics provably never reach `improve`, L3, round archives, states, results or memory (deep payload scan with sealed sentinel);
- [ ] forged/expired/mismatched leases fail before evaluation; main-partition holdout terminals carry only `HoldoutReceipt`.

### Evaluator and optimize state machine

- [ ] L0 refuses before expensive evaluation and on required-chain node errors; L1 never zero-fills; L3 honest ambiguity/rule-fallback visibility hold;
- [ ] stall guard, honest termination, draft-only routing and append-only rounds survive generalization; loop-level `OPTIMIZE_MAX_ROUNDS` clamp holds;
- [ ] `WAITING_FOR_MATURITY` persists `resume_after`/`wakeup_key`; wakeup is idempotent under duplication and pre-maturity calls; `finalize_candidate` requires `PASSED_VALIDATION` and always yields exactly one receipt per window.

### Registry/catalog chain and adapter regression

- [ ] `PHASE4_REGISTRY_DIGEST`/`PHASE4_CATALOG_DIGEST` build only from the exact Phase 3 full digests, match reviewed goldens, inherit byte-identical schemas, and introduce no "latest" alias;
- [ ] the seven-point research behavior-equivalence matrix passes; existing research suites (`test_research_loop.py`, `test_research_api.py`, `test_research_store.py`) are untouched and green; the honesty inventory of what stays on the old path is present in the adapter module docstring;
- [ ] `POST /research/loop/start` still routes to `run_research_loop` (production cutover explicitly deferred with its own reviewed plan); the adapter is reachable only by direct import;
- [ ] the adapter never patches the old loop: sha1 `graph_signature` remains in `research/loop.py` while `candidate_hash` equivalence is proven by test vectors;
- [ ] red lines re-proven: draft-only products, no auto-promotion, no `"rejected"` factorlib status assumed, LLM zero trading authority, no silent vendor fallback, sync self-HTTP confined to daemon threads, degradation badged (`legacy_data_binding`, `correlation_unadjusted`).

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — exact upstream ABI/digest evidence (blocks everything on Phase 2/3 exit gates);
2. after Tasks 1–2 — contract field/matrix review against spec §8 verbatim spellings;
3. after Task 3 — the additive event flip diff (the only Phase 1 touch) reviewed line-by-line;
4. after Tasks 4–5 — delegate equivalence + ledger reserve/reveal/reuse/exhaust semantics;
5. after Task 6 — sealed isolation evidence (namespace scans, capability matrix);
6. after Tasks 7–8 — four-layer honesty and state-machine invariants incl. maturity wakeup;
7. after Task 9 — golden freeze review (digests recorded once, never test-regenerated);
8. after Task 10 — behavior-equivalence matrix + honesty inventory, then all Exit Gates.

Phase 5 (Bootstrap Lane 0 + 经验库) binds its delayed grader to this phase's `MaturityPending`/`WAITING_FOR_MATURITY` and TrialLedger semantics and consumes `PHASE4_REGISTRY_DIGEST`/`PHASE4_CATALOG_DIGEST` as its sole chain base; Phase 6 continues the linear chain from `PHASE5_REGISTRY_DIGEST`/`PHASE5_CATALOG_DIGEST` and only pins the shadow consumer contracts — the dual shadow curves themselves are owned and produced by Phase 9's adapters, and Phase 4's contribution is the `evaluate_validation` seam they bind to. Cross-phase D6 seam: the catalog-assembly phase's #26/#27 revision proposals must pass Task 4b's `revision_throttle_check` and the TrialLedger's same-family trial accounting before admission; the throttle's matured-observation count is supplied by the Phase 5 matured-case grader — the rule lives in Phase 4, the data in Phase 5, the enforcement in the catalog-assembly phase.

Do not begin Phase 5 until every Phase 4 Exit Gate is checked with test evidence. No execution method requires a particular optional skill package.
