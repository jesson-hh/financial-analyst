# Orchestration Phase 1 · Amendment 1 — typed evidence refs + snapshot ABI (Implementation Plan)

> **Execution note:** this amendment re-opens the frozen Phase 1 contract layer to match the user-amended Phase 1 spec (`docs/superpowers/plans/2026-07-15-orchestration-phase1-contracts.md`, frozen 2026-07-16 14:34). Authoritative gap analysis: `.superpowers/sdd/phase1-amendment-delta.md`. Implement task-by-task, TDD, review checkpoint per task + one whole-amendment review at the end. Phase 2 execution resumes only after this amendment's exit gates pass.

**Goal:** bring `guanlan_v2/orchestration/` up to the amended Phase 1 spec: a new composite `TypedPayloadRef` evidence reference, the `ToolCallRecord`/`Provenance`/`NodeRun` evidence overhaul, the `ContextSnapshot`/`InputSnapshot` ABI redesign, two new registered context facts (`ContextRuntimeRequirements@1`, `InputArtifactBinding@1`), and a single reviewed golden re-freeze (8 → 11 registered schemas).

**User rulings (final, binding):**
1. `TypedPayloadRef(schema_ref: SchemaRef, payload_ref: PayloadRef)` is a **new Phase 1 composite model**; the plain locator `PayloadRef` is untouched. Doc occurrences have been disambiguated accordingly.
2. Amended spec doc (working tree, frozen 14:34 + TypedPayloadRef restore) governs over both the implemented code and any older plan text.
3. `InputSnapshot.attempt` is audit (docline 853); `NodeRun.attempt` stays semantic (per-model, not a contradiction).

## Global Constraints

- Phase 1 Global Constraints continue to apply (strict/frozen/extra-forbid `ContractModel`/`DigestModel`, canonical `sha256+cjson-v1`, TDD red→green with recorded evidence, no placeholders).
- **`digest_vectors_v1.json` must not change** (algorithm-level vectors; confirmed model-independent). Any diff to it is a bug.
- **One golden re-freeze.** `tests/orchestration/golden/schema_manifest_v1.json` changes exactly once, in Task C/D, hand-written and reviewed — never regenerated from test code.
- `engine/financial_analyst/**` and `guanlan_v2/workflow/executor.py` untouched. `spec.py`/`migration.py`/`catalog.py` logic untouched (delta §3: only digest *values* shift).
- Cross-object invariants (evidence-tuple equality with Artifact Provenance, capability SchemaRef matching, admitted-Plan binding equality) are **documented and test-demonstrated** in Phase 1, **enforced** by Phase 2 runtime services. Do not invent Phase 1 stores/gateways.
- Git hygiene: shared branch with a concurrent session — `git status --short` first, commit with **explicit pathspec only**, never `git add -A`/`.`/bare `-a`.
- Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task A: Composite `TypedPayloadRef` + shared typed-ref tuple validation

**Files:**
- Modify: `guanlan_v2/orchestration/refs.py`
- Modify: `guanlan_v2/orchestration/schema_registry.py` (INTERNAL_MODELS classification only — promotion to registered happens in Task C/D)
- Test: `tests/orchestration/test_refs.py`

**Produces:**

- `TypedPayloadRef(DigestModel)`: `schema_version: Literal["1"]="1"`, `schema_ref: SchemaRef`, `payload_ref: PayloadRef`. Semantic projection = schema_version + full SchemaRef + payload namespace/content digest; `payload_ref.object_id` stays audit-only via the nested `PayloadRef` projection (no new excludes needed — prove it). No namespace constraint **on the type itself**: per amended spec Task 3 (docline 425), owners that expose public/runtime evidence additionally require `namespace="main"`.
- `typed_ref_sort_key(ref) -> tuple`: the canonical typed semantic projection key `(schema_ref.name, schema_ref.version, payload_ref.namespace, payload_ref.content_digest)` (docline 709).
- `validate_typed_ref_tuple(refs, *, require_main: bool, field_name: str) -> None`: shared helper enforcing canonical order by `typed_ref_sort_key`, duplicate-freedom, and (when `require_main`) `payload_ref.namespace == "main"` on every element. Raises `ValueError` naming `field_name`. This is the single implementation Tasks B and C reuse (delta §4 coupling 3).
- Classification: add `TypedPayloadRef` to `INTERNAL_MODELS` with a reason noting scheduled promotion in this amendment's registry task (keeps the completeness firewall green without an early golden change).

**Required tests (write first, observe red):**
construction/frozen/extra-forbid; garbage `content_digest` rejected through nested `PayloadRef`; parent-embedding relocation test (changing only `payload_ref.object_id` leaves an embedding parent's semantic digest unchanged; changing schema_ref/namespace/content changes it); sort-key ordering; `validate_typed_ref_tuple` accepts canonical/rejects out-of-order/duplicate/non-main-when-required; completeness firewall still green.

Run: `pytest tests/orchestration/test_refs.py tests/orchestration/test_contract_completeness.py -v`

Commit (pathspec): `guanlan_v2/orchestration/refs.py guanlan_v2/orchestration/schema_registry.py tests/orchestration/test_refs.py` — `feat(orchestration): add composite TypedPayloadRef evidence reference`

---

## Task B: Evidence overhaul — `ToolCallRecord` / `Provenance` / `NodeRun`

**Files:**
- Modify: `guanlan_v2/orchestration/schemas.py`
- Test: `tests/orchestration/test_artifact.py`, `tests/orchestration/test_node_run.py`

All three models stay INTERNAL (unregistered) — **zero golden impact** (delta §4 Task B). `Artifact` model itself is unchanged; its reproducibility digest shifts automatically through the nested `Provenance`.

**`ToolCallRecord` (amended per docline 708):**
- ADD `call_ordinal: PositiveInt` (semantic; service-issued; canonical ordering key).
- ADD `request_ref: TypedPayloadRef`, `result_ref: TypedPayloadRef` (semantic; validator: both `payload_ref.namespace == "main"`).
- REMOVE `request_digest`, `result_digest` (replaced by the refs' content digests).
- REMOVE `status` — the model is by definition a *successful finalized* call ("cannot represent pending/rejected/cache-only work"); pending/rejected work has no record. `ToolCallStatus` enum stays in `enums.py` untouched (frozen enum surface; Phase 2 refusal paths may use it).
- ADD audit-only `provider_call_id: NonEmptyStr | None = None` (SEMANTIC_EXCLUDE, joining call_id/started_at/finished_at).
- Keep clock-coherence validator. Capability request/output SchemaRef matching (docline 708) is cross-object → Phase 2 CapabilityGateway; Phase 1 test documents shape only.

**`Provenance` (amended per doclines 709–710):**
- RENAME `tool_calls` → `tool_call_records`; validator: ordered by `call_ordinal`, strictly increasing (dup-free).
- RETYPE `data_result_digests: tuple[DigestHex,...]` → `data_result_refs: tuple[TypedPayloadRef,...]`; `validate_typed_ref_tuple(require_main=True)`.
- ADD `execution_evidence_refs: tuple[TypedPayloadRef,...]` — same helper, main-only, canonical, dup-free.
- ADD cross-match validator (docline 709): any `data_result_refs` element whose `payload_ref.content_digest` equals some `tool_call_records[i].result_ref.payload_ref.content_digest` must equal that exact `result_ref` (full semantic equality); a cache hit may appear only in `data_result_refs`.
- `SEMANTIC_EXCLUDE` unchanged (`model_response_id`).

**`NodeRun` (amended per docline 711):**
- ADD the same three evidence tuples with identical validators (reuse the helper; same ordering/dup/main rules).
- REMOVE `tool_call_count` (derived truth = `len(tool_call_records)`; a worker cannot self-report a count).
- Retention rule: the three tuples are valid and preserved on **every** terminal status — tests must prove non-empty evidence on INCOMPLETE/FAILED/TIMED_OUT/CANCELLED constructs successfully (evidence cannot disappear because later work failed).
- `NodeRun.attempt` stays **semantic** (user ruling 3). Status matrix otherwise unchanged (`NodeStatus` already complete — no enum change).
- Cross-object equality with a successful Artifact's Provenance tuples (docline 711) is Phase 2 executor duty; add one Phase 1 test *demonstrating* the equal-construction pattern.

**Required tests:** update the ~14 `Provenance(`/`ToolCallRecord(` fixture sites + `test_node_run` fixtures; new: call_ordinal ordering/dup rejection; non-main request/result/data/evidence ref rejection; cross-match accept/reject; retention on all four failure statuses; differential digest tests (each new semantic field moves reproducibility digest of an embedding Artifact; provider_call_id does not).

Run: `pytest tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py tests/orchestration/test_refs.py -v`, then full `pytest tests/orchestration -q` (must stay green — B is internal-only).

Commit (pathspec): `guanlan_v2/orchestration/schemas.py tests/orchestration/test_artifact.py tests/orchestration/test_node_run.py` — `feat(orchestration): typed evidence refs in tool-call/provenance/node-run contracts`

---

## Task C/D: Snapshot ABI redesign + two new context facts + single golden re-freeze (ONE atomic commit)

**Files:**
- Modify: `guanlan_v2/orchestration/context.py`
- Modify: `guanlan_v2/orchestration/schema_registry.py` (+3 registered: promote `TypedPayloadRef@1`; add `InputArtifactBinding@1`, `ContextRuntimeRequirements@1`)
- Modify: `guanlan_v2/orchestration/__init__.py` (exports)
- Modify: `tests/orchestration/golden/schema_manifest_v1.json` (hand re-freeze: 2 changed entries + 3 new + new registry_digest)
- Test: `tests/orchestration/test_context.py`, `test_registry_population.py`, `test_contract_completeness.py`, `test_phase2_handoff.py`, plus fixture ripple in `test_spec.py:179`, `test_plan_catalog_validation.py:224`, `test_migration.py` (if any fixture builds a ContextSnapshot)

Atomicity rationale (delta §4 couplings 1–2, 4): model JSON schemas derive the manifest; the handoff gate and the two shared plan-validation fixtures break the moment the models change. They must move in the same commit.

**`ContextSnapshot` (amended per doclines 840–844):**
- ADD `memory_snapshot_ref: TypedPayloadRef` — validator: `payload_ref.namespace=="main"`, `payload_ref.content_digest == memory_snapshot_hash`.
- UPGRADE `memory_selection_ref: PayloadRef` → `TypedPayloadRef` — validator becomes `payload_ref.namespace=="main"`, `payload_ref.content_digest == past_context_hash`.
- ADD `runtime_requirements_ref: TypedPayloadRef | None` — `None` **iff** `(memory_snapshot_hash, past_context_hash)` equal the canonical empty digests from `build_empty_memory_binding()`; when present: main namespace + `schema_ref` == `ContextRuntimeRequirements@1`.
- `SEMANTIC_EXCLUDE` unchanged; relocation-invariance (object_id churn cannot move content_digest) proven by test.
- Update `build_empty_memory_binding()` / `EmptyMemoryBinding` to produce the two exact typed refs (`EmptyMemorySnapshot@1` / `EmptyMemorySelection@1` schema refs) so a no-memory runtime can populate the new fields (docline 843).

**NEW `ContextRuntimeRequirements@1`** (registered; docline 842): fields per delta §1.7 (`context_subject_digest`, `required_schema_registry_digest`, `required_catalog_digest`, `required_runtime_material_refs: tuple[ContentRef,...]`, `required_capability_refs: tuple[CapabilityRef,...]`, `required_bridge_ids: tuple[LogicalId,...]`, self-sealed `requirements_digest`), canonical/dup-free tuple validators, `build()` self-seal. Plus module helpers: `compute_context_subject_digest(data_context, memory_snapshot_ref, memory_selection_ref, memory_session_id)` and `verify_context_runtime_requirements(snapshot, requirements)` (shape/digest equality only — Phase 2 admission enforces authority; docline 842).

**NEW `InputArtifactBinding@1`** (registered; docline 850): `input_name: NonEmptyStr`, `cardinality: Literal["one","many"]`, `artifact_refs: tuple[ArtifactRef,...]` (full refs; import from schemas — verify no import cycle; if cyclic, place the model in `schemas.py` and re-export from `context.py`). Model-level: `one` ⇒ ≤1 ref; the "exactly 1" bound is enforced at `InputSnapshot.readiness=="ready"`.

**`InputSnapshot@1` redesign** (doclines 851–854): field matrix per delta §1.5 —
`snapshot_id`(audit)/`run_id`(audit)/`plan_id`(audit)/`plan_digest`(semantic)/`node_id`(semantic)/`layer_index: NonNegativeInt`(semantic)/`attempt: PositiveInt`(**audit**)/`context_snapshot_ref: TypedPayloadRef`(main + `schema_ref`==`ContextSnapshot@1`; replaces `context_snapshot_hash`)/`artifact_inputs: tuple[InputArtifactBinding,...]`(WorkerSpec declaration order — structural checks only in Phase 1)/`data_result_refs: tuple[TypedPayloadRef,...]`(shared helper, main, canonical, dup-free)/`memory_record_refs`(unchanged rules)/`readiness: Literal["ready","terminal_partial"]`/`missing_input_names: tuple[NonEmptyStr,...]`(canonical sorted, dup-free)/`content_digest`.
Validators: `ready` ⇒ `missing_input_names==()` and every `one` binding has exactly one ref; `terminal_partial` ⇒ non-executable marker (Phase 2 gateways reject it), missing names exact. `SEMANTIC_EXCLUDE = {snapshot_id, run_id, plan_id, attempt, built_at}`.

**Registry + golden + gates (same commit):**
- `_load_population` public tuple +3 → **11 registered**: promote `TypedPayloadRef@1` (drop its INTERNAL entry), add `InputArtifactBinding@1` + `ContextRuntimeRequirements@1` (docline 1465 mandates all three registered).
- Hand-regenerate `schema_manifest_v1.json`: `ContextSnapshot@1` + `InputSnapshot@1` digests move; +3 entries; new `registry_digest`. Record digests from a one-off verification run, review, freeze — never auto-write from test code.
- `test_registry_population.py`: imports, expected key set, `InputSnapshot.SEMANTIC_EXCLUDE` assertion (line 166) → new set, golden reproduce.
- `test_contract_completeness.py`: partition/discovery grows by 3.
- `test_phase2_handoff.py` re-baseline (delta C3 — re-pinning the new frozen baseline, not weakening): Point-1 golden expectations, Point-5 fixtures (`_input_snapshot_fields`, `context_snapshot_hash` param → new fields), Point-7 `ContextSnapshot.build` fixtures, and the stale line-24-25 comment ("the type the Phase 2 plan calls TypedPayloadRef" — now a real distinct type).
- `__init__.py`: export `TypedPayloadRef`, `InputArtifactBinding`, `ContextRuntimeRequirements` (lazy pattern as existing).
- Fixture ripple: `test_spec.py:179`, `test_plan_catalog_validation.py:224`, `test_context.py` (all `ContextSnapshot.build`/`InputSnapshot.build` sites), `test_migration.py` if applicable.

**Required tests:** new-model matrices (construction/frozen/extra-forbid/canonical/dup/self-seal/subject-digest cross-match both directions); ContextSnapshot new-field validators + relocation invariance + None-iff-empty-pair both directions; InputSnapshot readiness matrix (ready-with-missing rejected, one-binding cardinality bound, terminal_partial exact names), attempt-is-audit differential digest test, context_snapshot_ref schema pinning; golden reproduce; handoff gate green.

Run: `pytest tests/orchestration -v` (full suite; expect net growth) + `python -m compileall -q guanlan_v2/orchestration` + `ruff check guanlan_v2/orchestration tests/orchestration` if available.

Commit (pathspec, one commit): `guanlan_v2/orchestration/context.py guanlan_v2/orchestration/schema_registry.py guanlan_v2/orchestration/__init__.py tests/orchestration/golden/schema_manifest_v1.json tests/orchestration/test_context.py tests/orchestration/test_registry_population.py tests/orchestration/test_contract_completeness.py tests/orchestration/test_phase2_handoff.py tests/orchestration/test_spec.py tests/orchestration/test_plan_catalog_validation.py` (+`tests/orchestration/test_migration.py` if touched) — `feat(orchestration): amend snapshot ABI + register context runtime facts (golden re-freeze)`

---

## Exit gates (whole-amendment review checks these)

- [ ] Full suite green from repo root; no test weakened — every previously asserted behavior either still asserted or superseded by a stricter amended assertion.
- [ ] `digest_vectors_v1.json` byte-identical to before the amendment.
- [ ] `schema_manifest_v1.json` changed exactly once; 11 entries; registry_digest recomputable by the reproduce test; 6 untouched entries byte-identical (`MemoryRecordRef@1`, `EmptyMemorySnapshot@1`, `EmptyMemorySelection@1`, `PortfolioDecision@1`, `ResearchPlan@1`, `SentimentReport@1`).
- [ ] No second digest/canonicalization implementation; `TypedPayloadRef` is the only new ref type; plain `PayloadRef` unchanged.
- [ ] `spec.py`/`migration.py`/`catalog.py`/`events.py` logic diffs are zero (only tests/fixtures may touch their test files).
- [ ] `engine/`, `workflow/executor.py` untouched; no concurrent-session file swept into any commit.
- [ ] Amended-spec doclines 425/431/708–711/840–854/1465 each traceable to a committed assertion (reviewer spot-checks).

## Execution handoff

Task order A → B → C/D, opus implementer + opus reviewer per task (per-commit review range `<sha>~1..<sha>`), then one whole-amendment review over the three commits. After the amendment: resume Phase 2 from Task 1 (its Task-0 handoff gate re-baselined here stays the Phase 2 entry gate).
