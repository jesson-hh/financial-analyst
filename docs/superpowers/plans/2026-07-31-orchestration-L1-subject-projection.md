# Orchestration L1 · 标的→数据桥(subject→data-bridge projection,裁决 D-0 = 选项 (i))Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Build the L1 layer of the post-P10 re-freeze program (`docs/superpowers/specs/2026-07-29-post-p10-refreeze-design.md` §1.5): the CLOSED, reviewed projection that carries the run's committed `RunSubject@1` (code + session as-of) into the `data.runtime` bridge's param assembly, so the one reviewed prefetch row (`dec.pm` → `verified_snapshot`, param bindings `/asof_date→/as_of`, `/code→/symbols`) becomes **resolvable** for the first time in the kernel's history — healing defect H at its root rather than at its symptom. The plan also retires `StructurallyDeadRowDataProvider`'s dead-row empty-complete branch per its own tombstone (rows that are resolvable must go through the real provider path and **refuse loudly** while no production `DataRuntimeWorld` is bound — that unbound world is L2-b's business, not this plan's), consciously flips every defect-H regression pin from "declared-not-runnable" to "runnable-under-projection", and ends with a REAL production deep run whose pm node must exhibit the NEW refusal shape — *params resolved from the run subject, no world bound* — as live proof the projection works.

**The ruling (verbatim, decided — do not re-litigate):** D-0 = materialization-time param stamping from the committed `RunSubject@1`. At materialize time (`deep_decide.materialize_deep_decide_draft`, and the screening lane's equivalent), nodes whose worker carries reviewed data rows with `node_param` bindings get params stamped from the run's committed RunSubject (code → `/code`, as_of → `/asof_date`) via a CLOSED, reviewed projection — the sealed preset RECORD stays code-free (zero preset-digest movement; the validator's "no params in the sealed record" rule is untouched — stamping happens on the materialized DRAFT). Rationale recorded by the controller: heals defect H for dec.pm in the same stroke; leases bind the preset RECORD digest so batch/lease properties survive; candidate digests are already per-code. Cost honestly recorded: the materialized draft is no longer byte-identical across stocks (it already wasn't in identity terms).

**Base:** branch `report-evidence-pack` at `0c601b5` (== main). `tests/orchestration` baseline at the last task report: 5645 passed + 1 xfailed (counts drift with concurrent sessions — every task re-measures at start, never hardcodes).

---

## Ground truth (verified at source 2026-07-31 — re-verify anchors before editing; line refs drift)

| # | Fact | Anchor |
|---|---|---|
| G1 | The one reviewed grant is `dec.pm → verified_snapshot`; grants and prefetch rows must be one-to-one (`granted != rows` ⇒ raise) | `data/catalog.py:94-99`, `:792-799` |
| G2 | The row's param bindings are sealed bytes: `("/as_of","node_param","/asof_date")`, `("/symbols","node_param","/code")`; `ParamBinding.source_kind` is CLOSED to `node_param\|input_value\|const`; the three binding-model **class docstrings are digest-bearing** (pydantic docstring → JSON-Schema description; proven in `fix-pm-dead-binding-report.md` §4) | `data/catalog.py:105-143`, `:517-522` region |
| G3 | `dec.pm` has `params_schema_ref=None`; Phase-1 refuses ANY node params for such a worker (`params_not_allowed`); a sealed v2 preset forbids node params outright | `spec.py:951-954`, `pipeline/assembly.py:945-948` |
| G4 | `_assemble_params(row, node)` resolves `node_param` pointers from `node.params` only and raises `DataRuntimeError` with `_node_param_cause` naming the "until the subject->data projection is built" gap | `data/runtime.py:399-448` |
| G5 | `verified_snapshot`'s params class is `InstrumentUniverseParams`: `symbols: tuple[Symbol, ...]` (Symbol is a **structured DigestModel** — 6-digit code + exchange + board, not a string), `as_of: IsoAwareDateTime` (aware ISO string) | `data/source.py:148-159`, `:570-574`, `data/symbols.py:39-73` |
| G6 | Material digests are literal byte constants (`_PROVIDER_BYTES`/`_ANALYZER_BYTES`), NOT source-file hashes; comments and non-public-model docstrings are digest-free; the before/after digest-probe method and its six-digest table are in `fix-pm-dead-binding-report.md` §5 | `data/catalog.py:399-407` |
| G7 | `data_runtime_provider_factory` has **zero production callers** (AST-pinned); the deep lane registers `StructurallyDeadRowDataProvider` instead, whose docstring TOMBSTONE names this plan: "this provider must then be retired for the world-bound one and never widened" | `data/runtime.py:690-701`, `:746-923`; pin `tests/orchestration/data/test_data_catalog.py::TestVerifiedSnapshotRowIsDeclaredNotRunnable::test_the_data_bridge_provider_has_no_production_caller` |
| G8 | The production runner is a **per-run factory** `(*, admission, lane_bindings, run_context_factory, request_id, prompt_assembler) -> runner` = `assembly.build_production_plan_runner`; `_plan_executor` builds a per-dispatch `ExecutionRuntime(catalog=bundle.runtime, bridge_view=…, factories=bundle.factories, …)`; the bridge resolver obtains providers via `self._runtime.factories.handler_factory(rb.provider_ref)` | `pipeline/live_decide.py:66-70`, `:848-851`, `:1212-1223`; `pipeline/assembly.py:980-988`; `worker.py:1687`, `:1719` |
| G9 | The subject is run-scoped: `RunSubject(code, as_of)` committed via `_commit_run_subject` with `content_digest == subject.semantic_digest()`; it reaches seats only through `SubjectPromptAssembler` (per-run, closes over the subject, occupies the `prompt_assembler` injection seam) — the exact per-run threading precedent this plan reuses for data | `pipeline/live_decide.py:555-561`, `:751-752`, `:835`; screening: `pipeline/screening.py:829-839` |
| G10 | pm's data support summary licenses zero finalized calls (`min_finalized_tool_calls_on_success == 0`, row is `cache_or_invoke` + `success_requires_finalized_call=False`); current production shape: pm + memory bridges complete EMPTY, trunk = 6 LLM invocations, pv aux nodes fail `bridge_execution_error` and degrade | `.superpowers/sdd/task-pm-two-bridges-report.md` §2, §5; pins in `tests/orchestration/test_pm_two_bridges.py` |

## Design resolution (the plan's answers to the chartered scope questions)

**R1 — the params gate is reconciled OUT-OF-BAND; `spec.py` untouched; no new `source_kind`.** D-0's "stamping on the materialized DRAFT" cannot mean writing `PlanNode.params` — G3 refuses that for `dec.pm` in preset and dynamic plans alike, and weakening either guard is forbidden. The projection therefore targets the **bridge param assembly**: at materialize time the ONE reviewed recipe projects the committed subject into a closed two-key document (`SubjectParams`), carried beside the draft on the materialized composite (the `MaterializedDeepDecide` precedent — G9's "beside the draft" idiom), and `_assemble_params` gains `subject_params` as a second, closed source for `node_param` pointers. Honesty framing (recorded in code comments, which are digest-free per G6): the subject params ARE materialization-stamped node params in every semantic sense; they travel out-of-band solely because Phase-1's `params_not_allowed` guards the *untrusted in-band* channel (model/caller-supplied params), while this channel is service-stamped from a digest-committed artifact after validation. The sealed `ParamBinding` docstring bytes do not move. If implementation discovers this is impossible without a new `ParamBinding.source_kind` or any schema-fact movement — **STOP and escalate to the controller; do not choose silently.**

**R2 — threading reuses the per-run runner seam (the `prompt_assembler` precedent, G8/G9).** `build_production_plan_runner` gains `subject_params=None`; when bound, `_plan_executor` hands `ExecutionRuntime` a thin per-run **delegating view** over `bundle.factories` that overrides `handler_factory` for exactly the sealed `phase3_data_surface().provider_ref` key, returning a subject-bound provider factory. Everything else (memory recipe, experience pair, `cand.*` handlers, model/capability factories) delegates to the process-level registry untouched. When `subject_params` is None (screening today, dynamic planner plans, replay, every non-deep caller) the factories object passes through **identity-unchanged** — bitwise-identical behavior. **Chartered hand-off (seam review):** this seam is consumed and consciously flipped by **L2-b Task 5 (the L1↔L2-b integration seam)** — the view's override target moves there from `worldless_data_provider_factory(subject_params)` to the world-bound `production_data_provider_factory(subject_params)`, and the real `_DataRuntimeBridgeSession` gains the subject param source in the same task.

**R3 — the provider retires per its tombstone; three LOUD shapes, no empty-complete branch.** `StructurallyDeadRowDataProvider` (+ its fact helper, factory, registration recipe) is replaced by `WorldlessDataBridgeProvider`: (i) allowlisted worker without a reviewed row → loud (unchanged text, the pv-aux shape); (ii) rows + subject projection **bound** → actually run `_assemble_params` + `params_cls.model_validate` per row (proving resolvability against the REAL sealed row), then raise the **NEW typed refusal** naming the resolved subject (code + as_of) and the unbound world: "params resolved from the run subject projection … no production DataRuntimeWorld is bound (the chartered L2-b gap) — refusing rather than faking a data read"; (iii) rows + projection **unbound** → loud "subject projection not bound at the runner seam" (a wiring defect, no longer a structural fact — once `_assemble_params` accepts the subject source, 'structurally dead' ceases to exist as a category). Never an empty contribution for a row that could have been read.

**R4 — the screening lane gets the same stamp, honestly vacuous today.** The screening materialized composite gains the same `subject_params` stamping from its per-code committed subject (D-0 names "the screening lane's equivalent" explicitly), and its runner call threads it — plus an executable pin that TODAY zero workers in the screening preset carry data prefetch rows, so the thread is exercised structurally, not behaviorally. When a screening worker ever gains a grant (L3), the path is already true. **「控制器已裁」screening stays in L1** — the once-drafted option of deferring the screening half to L3 is deleted; this stamp and Task 4's screening thread stand as written.

**⚠ Production consequence, stated up front (not a surprise for the reviewer):** between L1 landing and L2-b landing, a production deep run's `dec.pm` node goes from "completes with an honest EMPTY data contribution" (today, 6 LLM trunk) back to **failing loudly at bridge execution** with the new resolved-but-no-world reason — trader is then blocked and the deep run fails; the fast chain continues to stand, `deep_outcome` records the failure honestly, the watcher is never blocked. This is the controller-chartered posture ("resolvable rows must then go through the REAL provider path — and refuse loudly if no world is bound"): completing empty over a row that SHOULD be read would be a silenced outage, exactly what the tombstone forbids widening into. The live verification task asserts this shape on 9998. **「控制器已裁」the release posture:** L1 → L2-b is the declared execution order, released as **ONE train** — L1 is verified live on 9998 (Task 6) and **9999 deploys only after L2-b lands** (L2-b's live task is the train's deployment point), so the honest-refusal window is never production-served; between the two landings the refusal shape exists only on the tree and on 9998.

---

## Global Constraints

These extend, never override, the Phase 1–10 constraints, the re-freeze charter's discipline section (§3), and spec §8 red lines (LLM 零买卖;advisory only;绝不 `git add -A`).

- **Zero sealed-digest movement — proven, not asserted.** Every task that touches `data/catalog.py`, `data/runtime.py` or any registered model runs the before/after digest probe (the `fix-pm-dead-binding-report.md` §5 method) over: `bridge.data_runtime.{provider,analyzer,prefetch,descriptor}` material digests, the prefetch `binding_digest`, `PHASE3_DATA_CATALOG_DIGEST`, and the JSON-Schema digests of `ParamBinding`/`DataPrefetchOperation`/`DataBridgePrefetchBinding`. The diff must be empty. `SubjectParams` and the factory view are **plain service objects** (dataclass / class), never registered models: `RUNTIME_PUBLIC_MODELS` stays `()` and `RUNTIME_INTERNAL_MODELS` stays `{}` — pinned. `spec.py` and `pipeline/assembly.py:945-948` are not edited (git-diff-clean gate).
- **TDD, RED first.** Every "write failing tests" step runs the focused command and records the failure kind before implementation; collection errors don't count as red. Every new load-bearing guard gets **mutate→red→revert** with the exact mutation and the exact tests that went red recorded in the task report.
- **Conscious flips only.** Every pin that this plan turns over (the defect-H class, the pm-two-bridges empty-complete/trunk pins, the tombstoned provider) is flipped **in the same commit** as the behavior change that flips it, with bidirectional evidence (old assertion red against new code; new assertion red against old code — demonstrated via stash or targeted revert) recorded in the task report. Nothing goes green "by the way".
- **Honest typed refusals over silent fallback.** No branch in this plan may fabricate a data read, an empty contribution over a resolvable row, or a default subject. Escalation triggers in R1/Task 1 are mandatory stops.
- **Explicit pathspec commits; never stage concurrent sessions' files.** The working tree carries long-lived uncommitted work: `guanlan_v2/{console,datafeed,fundflow,glmcp}/`, `guanlan_v2/server.py`, `guanlan_v2/strategy/_provenance.json`, `docs/README.md`, `docs/agent_data_interfaces.md`, `ui/**`, `tests/test_console_tools.py`, `tests/test_datafeed_*.py`, `tests/test_fundflow_pulse.py`, `tests/test_guanlan_mcp.py`, `.data/wisdom/**`, `scripts/`, `p6-rerank-badges.jpeg`. If the full suite shows the 2 `test_capability_manifest.py` reds, prove they are the concurrent session's by the isolation method (copy-out → `git checkout --` → rerun → restore) before claiming your run is clean.
- **Count honesty.** Re-measure the `tests/orchestration` baseline (foreground, split into two halves if the 10-min tool cap binds; `--collect-only` cross-check) at task start; assert deltas (+N your new tests), never absolute campaign numbers.
- **Production verification is REAL.** The 07-29..31 campaign found 13 defects invisible to a 5600-green tree. Task 6 is a real deep run with real LLM spend (tokens authorized), on 9998 first (9999 is watchdog-guarded; killing it triggers generational rotation). `GUANLAN_SEATS_DEEP` unset must remain bit-unchanged throughout (P10 regression rerun).
- Task reports go to `.superpowers/sdd/task-L1-<n>-<slug>-report.md`; the flip inventory and digest-probe outputs are appended there, and the final task updates `.superpowers/sdd/progress-orchestration.md`.

---

## File structure (created/modified)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/data/runtime.py` (modify) | `SubjectParams` carrier + closed pointer set + `_assemble_params` subject source (T1); `WorldlessDataBridgeProvider` replacing the dead-row provider + registration recipe (T3) |
| `guanlan_v2/orchestration/pipeline/deep_decide.py` (modify) | `project_subject_params` call-through: `materialize_deep_decide_draft(..., subject=…)` verifies the digest bond and stamps `MaterializedDeepDecide.subject_params` (T2) |
| `guanlan_v2/orchestration/pipeline/screening.py` (modify) | same stamp on the screening materialized composite (T2) |
| `guanlan_v2/orchestration/pipeline/assembly.py` (modify) | `build_production_plan_runner(subject_params=…)` + `_SubjectScopedFactories` per-run view (T4) |
| `guanlan_v2/orchestration/pipeline/live_decide.py` (modify) | pass `subject=` to the materializer, `subject_params=` to the runner factory; registration recipe caller rename (T3/T4) |
| `guanlan_v2/orchestration/data/catalog.py` (modify, comments only) | honesty notes updated from "DECLARED but NOT RUNNABLE" to the projection truth (T5) |
| `tests/orchestration/data/test_subject_projection.py` (new) | T1 projection + T3 provider shapes |
| `tests/orchestration/data/test_data_catalog.py` (modify) | conscious flip of `TestVerifiedSnapshotRowIsDeclaredNotRunnable` (T1) |
| `tests/orchestration/test_pipeline_deep_preset.py`, `test_pipeline_screening.py` (modify) | T2 stamping pins |
| `tests/orchestration/test_pm_two_bridges.py` (modify) | T3/T4 conscious flips of the empty-complete / trunk / seam pins |
| `tests/orchestration/test_pipeline_assembly.py`, `test_pipeline_live_decide.py` (modify) | T4 threading pins |

---

## Task 1: `SubjectParams` + the closed subject source in `_assemble_params`

**Files:** modify `guanlan_v2/orchestration/data/runtime.py`; new `tests/orchestration/data/test_subject_projection.py`; modify `tests/orchestration/data/test_data_catalog.py` (conscious flip, same commit).

**Interfaces — Produces (later tasks bind these exact names):**

- `@dataclass(frozen=True) class SubjectParams` in `data/runtime.py` — the closed subject-params document. Fields: `code_value: Any` (the value the sealed `/code` source pointer yields) and `asof_value: str` (aware ISO-8601, the `/asof_date` value). Constructor classmethod **`SubjectParams.project(*, code: str, as_of: datetime) -> SubjectParams`** — the ONE reviewed projection recipe (layering-clean: takes primitives, imports nothing from `pipeline/`). It refuses (typed `DataRuntimeError`): empty/non-canonical code, naive `as_of`. `as_document()` returns exactly `{"asof_date": asof_value, "code": code_value}` — a CLOSED two-key document, no extension point.
- `SUBJECT_PARAM_POINTERS: frozenset[str] = frozenset({"/asof_date", "/code"})` — the closed set of source pointers the subject may serve.
- `_assemble_params(row, node, *, subject_params: SubjectParams | None = None)` — resolution order for a `node_param` binding: (1) `node.params` (unchanged semantics for workers that legally carry params); (2) else, if `subject_params` is bound AND the pointer ∈ `SUBJECT_PARAM_POINTERS` → the subject document; (3) else the existing cause-named `DataRuntimeError`, with `_node_param_cause`'s no-params message **updated**: the projection now exists — the failure is "the run's subject projection was not bound at the runner seam", not "until the projection is built". **Conflict guard:** if `node.params` AND the subject document both supply the pointer and the values differ → loud `DataRuntimeError` (a node may never contradict the run subject).

**Binding investigation (step 1 below, before any test is written — outcomes recorded in the test docstring with file:line):**

1. **Value shapes must satisfy the REAL sealed row.** `verified_snapshot` ⇒ `InstrumentUniverseParams` (G5): `/symbols` needs `tuple[Symbol, ...]` where `Symbol` is a structured model (6-digit code, exchange, board). So `code_value` must be the **singleton instrument set** the run subject denotes — a one-element tuple/list of a Symbol-shaped mapping. Locate the existing reviewed code→Symbol constructor (search `data/symbols.py` and its tests for a parse/from-dotted/normalize helper; the pipeline's `normalize_symbol` output format is the input — G9's `canonical`). **Never re-derive board rules by hand** (the BJ-920 号段 lesson: 4/8/920 → BJ; a hand-rolled prefix table is a known defect class). If no reviewed constructor exists that maps the watcher-canonical code format into `Symbol`, or if `InstrumentUniverseParams.model_validate` cannot accept any honestly-shaped projection of one code + one aware datetime — **STOP, escalate to the controller** (that would mean the sealed row's shape is unsatisfiable without moving schema facts, i.e. D-0 option (iii) territory). **「控制器已裁」the singleton-universe semantic is RATIFIED** — `/code → /symbols` projects as the one-element instrument set via the EXISTING reviewed constructor this step locates, recorded as reviewed semantics in `SubjectParams.project`; the escalation trigger above stays live (it fires only if no reviewed constructor exists).
2. `asof_value` = the subject's `as_of` in aware ISO-8601 (satisfies `IsoAwareDateTime`).

- [ ] **Step 1:** Run the investigation; record the constructor name + shapes.
- [ ] **Step 2: Write failing tests** in `tests/orchestration/data/test_subject_projection.py`:
  - `SubjectParams.project` happy path; refusals (empty code, naive datetime); document closed (exactly two keys; frozen).
  - **The real-row round trip (the load-bearing test of this whole plan):** drive the REAL sealed row (`phase3_data_surface().prefetch_binding.operations[0]`) + a legally-shaped params-less `dec.pm` node through `_assemble_params(row, node, subject_params=SubjectParams.project(...))`, then `_BINDING_BY_METHOD["verified_snapshot"].params_cls.model_validate(...)` — must produce a valid `InstrumentUniverseParams` whose `symbols[0]` round-trips the subject code and whose `as_of` equals the projected instant. No fixture row, no fabricated binding.
  - Unbound-subject refusal keeps firing on the same real row, message names the runner-seam cause.
  - Conflict guard: node params carrying a *different* `/code` than the subject → loud refusal; carrying the *same* value → resolves.
  - Pointer outside `SUBJECT_PARAM_POINTERS` with subject bound → still refused (closure).

  Run: `python -m pytest tests/orchestration/data/test_subject_projection.py -v` — expected FAIL (missing names).
- [ ] **Step 3: Implement** in `data/runtime.py`. The honesty comment at `_assemble_params` records R1's framing (out-of-band stamped node params; why `spec.py`'s guard is untouched and un-weakened). ASCII-only messages.
- [ ] **Step 4: Conscious flip (same commit):** rewrite `tests/orchestration/data/test_data_catalog.py::TestVerifiedSnapshotRowIsDeclaredNotRunnable` → `TestVerifiedSnapshotRowRunnableOnlyUnderSubjectProjection`. Tests 1–3 (no params schema / `params_not_allowed` / sealed binding bytes) keep their assertions — those facts did not move. Test 4 splits both directions (without subject → raises with the NEW cause text; with subject → resolves + validates). Test 5 re-pins the source-text call `_assemble_params(row, req.node` in the live provider session — **this pin is a chartered CONSCIOUS FLIP owned by L2-b Task 5 (the L1↔L2-b integration seam), where the real session gains the subject param source; it is listed there by name.** Test 6 (`test_the_data_bridge_provider_has_no_production_caller`) **stays green untouched** — `data_runtime_provider_factory` remains without a production caller until L2-b. Class docstring rewritten: the row is RUNNABLE under the projection, not yet SERVABLE (no world — L2-b). Record bidirectional flip evidence.
- [ ] **Step 5: Mutations (each red → reverted byte-identical, record which tests redden):** (m1) widen `SUBJECT_PARAM_POINTERS` with `"/limit"` → closure test red; (m2) subject wins over node params unconditionally (drop the conflict raise) → conflict guard red; (m3) `project` accepts a naive datetime → refusal test red; (m4) subject source consulted even when `subject_params is None` via a module-level default → unbound-refusal test red.
- [ ] **Step 6: Digest probe** (Global Constraints method) before/after — empty diff mandatory; `RUNTIME_PUBLIC_MODELS == ()` pinned in the new test file.
- [ ] **Step 7: Run** `python -m pytest tests/orchestration/data -q` (expect all green) **and commit:**

```bash
git add guanlan_v2/orchestration/data/runtime.py tests/orchestration/data/test_subject_projection.py tests/orchestration/data/test_data_catalog.py
git commit -m "feat(orchestration): the closed subject->data param projection - the sealed dec.pm row resolves for the first time (L1/D-0, spec.py untouched, zero digest movement)"
```

---

## Task 2: Materialization-time stamping (deep + screening composites)

**Files:** modify `guanlan_v2/orchestration/pipeline/deep_decide.py`, `guanlan_v2/orchestration/pipeline/screening.py`; modify `tests/orchestration/test_pipeline_deep_preset.py`, `tests/orchestration/test_pipeline_screening.py`.

**Interfaces — Produces:**

- `MaterializedDeepDecide` gains `subject_params: SubjectParams | None = None` (default None so every pre-existing construction keeps meaning exactly what it meant — the module's own stated convention for `preset_id`).
- `materialize_deep_decide_draft(..., subject: RunSubject | None = None)` — new keyword. When provided: must be a `RunSubject` (typed `SubjectRefused` otherwise) whose **digest bond is verified**: `subject.semantic_digest() == subject_ref.payload_ref.content_digest` (the same equality `screening.py:839` already enforces — the stamped params provably come from THE committed artifact the ref names; mismatch ⇒ `SubjectRefused`). Then `subject_params = SubjectParams.project(code=subject.code, as_of=subject.as_of)` is stamped on the composite. When None: `subject_params=None` (legacy constructions; the bridge will refuse loudly downstream — honest, never defaulted). `import` direction: `deep_decide` → `pipeline.contracts` (`RunSubject`) and → `data.runtime` (`SubjectParams`); verify no cycle (`python -m compileall -q guanlan_v2/orchestration`).
- The **screening equivalent**: the screening materialized composite (the class at `screening.py:568-576` carrying `subject_ref` — bind to the implemented name at source) gains the same `subject_params` field, stamped from the per-code committed subject at the same site that verifies the commit digest (`:829-839`).
- The draft itself is untouched: `PlanNode.params` stays empty, the draft (identity fields aside) stays byte-identical across stocks — pinned (this is BETTER than D-0's recorded cost, and the pin proves the sealed-record rule never moved).

- [ ] **Step 1: Write failing tests** — deep: stamped composite equals `SubjectParams.project` of the committed subject; digest-bond mismatch refused (`SubjectRefused`); `subject=None` → `subject_params is None` and every existing materializer pin still green; draft nodes all `params=None`/empty after stamping (the sealed-record rule pin); refusal ORDER preserved (subject-ref checks still precede context checks). Screening: same stamp; plus the **vacuity pin** — iterate the screening preset's worker set against `phase3_data_surface().prefetch_binding.operations` and assert zero rows target them, with a docstring saying the thread is structural until an L3 grant lands. Run focused files — expected FAIL.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Mutations:** (m1) skip the digest-bond check → bond test red; (m2) stamp from `subject_ref` metadata instead of the verified subject object (e.g. fabricate code from goal text) → equality test red.
- [ ] **Step 4: Run** `python -m pytest tests/orchestration/test_pipeline_deep_preset.py tests/orchestration/test_pipeline_screening.py tests/orchestration/data/test_subject_projection.py -q` — PASS. **Commit:**

```bash
git add guanlan_v2/orchestration/pipeline/deep_decide.py guanlan_v2/orchestration/pipeline/screening.py tests/orchestration/test_pipeline_deep_preset.py tests/orchestration/test_pipeline_screening.py
git commit -m "feat(orchestration): materialization-time subject-params stamping on both lanes' composites (D-0 verbatim seam; sealed record stays code-free)"
```

---

## Task 3: Retire the dead-row provider — `WorldlessDataBridgeProvider`, three loud shapes

**Files:** modify `guanlan_v2/orchestration/data/runtime.py`, `guanlan_v2/orchestration/pipeline/live_decide.py` (registration caller), `tests/orchestration/data/test_subject_projection.py`, `tests/orchestration/test_pm_two_bridges.py` (conscious flips, same commit).

**Interfaces — Produces (R3):**

- `class WorldlessDataBridgeProvider` replaces `StructurallyDeadRowDataProvider`; `worldless_data_provider_factory(subject_params: SubjectParams | None = None)` replaces `structurally_dead_row_data_provider_factory`; `register_worldless_data_provider(*, factories, subject_params=None)` replaces `register_structurally_dead_row_data_provider` (update the sole production caller `live_decide.build_production_bindings:1189` — the process-level registration stays **unbound**, `subject_params=None`; Task 4's per-run view supplies the bound one). Old names, `_row_is_structurally_dead`, `structurally_dead_row_fact` and the empty-complete session are **deleted** — the tombstone is honored, and a repo-wide grep pin asserts `StructurallyDeadRowDataProvider` no longer exists outside docs/reports.
- `prepare_input`: bit-for-bit the world-bound provider's I/O-free empty prepare (unchanged from today's provider — token bridge/summary checks).
- `open_execution` — three shapes, all `DataRuntimeError`, none empty:
  1. no reviewed row for the allowlisted worker → unchanged loud text (the pv-aux shape; their `bridge_execution_error` + degrade behavior must be re-pinned unchanged);
  2. rows + `subject_params` bound → for EVERY row, run the real `_assemble_params(row, request.node, subject_params=…)` + `params_cls.model_validate` (resolvability proven, zero I/O, zero gateway begins), then raise the NEW shape whose message contains: the literal marker `params resolved from the run subject projection`, the subject's code and as-of values, the row's method id, and `no production DataRuntimeWorld is bound (the chartered L2-b gap)`. ASCII-only. This message IS the live-verification target — treat its marker substring as a frozen contract within this plan;
  3. rows + `subject_params` unbound → loud `subject projection not bound at the runner seam` (wiring defect).
  Plus the inherited guards unchanged: foreign bridge in config, drifted prepared handle.

**Conscious flips (same commit, bidirectional evidence):** in `test_pm_two_bridges.py` — `TestPmBridgeLayerCompletesEmpty` (pm no longer completes empty: unbound → shape 3; bound → shape 2), the trunk-shape/6-LLM expectation notes, the discrimination empty-branch tests (rewritten to shapes 2/3), the registration pins (isinstance → `WorldlessDataBridgeProvider`), `TestTheLiveFailureControl` stays (raw control still meaningful: an experience-only registry still yields the verbatim prepare failure). The memory rowless provider pins are untouched. Record every flip in the task report table. **Order-conditional (seam review):** the pm behavioural pins flipped here target the L1-only tree state and are flipped AGAIN by L2-b (Task 4 supersede + Task 5 integration seam) to the real-read semantics — write them guarded on the provider class actually registered at `phase3_data_surface().provider_ref` (asserting the worldless shapes while `WorldlessDataBridgeProvider` is the incumbent), so L2-b's landing flips them consciously under its correction clause (which names both class names and both pin sets), never reddens them by surprise.

- [ ] **Step 1: Write failing provider tests** in `test_subject_projection.py` (shapes 1/2/3 over the REAL reduced support report + real resolver bundle, the `test_pm_two_bridges` fixture idiom; zero gateway begins pinned with a poisoned gateway object for shapes 2/3). Run — expected FAIL.
- [ ] **Step 2: Implement + flip.** Docstring carries the successor tombstone, naming its retirers by task: **L2-b Task 4 supersedes the registration and L2-b Task 5 (the L1↔L2-b integration seam) re-targets the per-run view and deletes this class**; shape 3's runner-seam semantics survive inside the real session's `_assemble_params` refusal, and shape 1's fate (loud vs catalog-licensed EMPTY for rowless workers) is L2-b's conscious flip — neither is silently inherited.
- [ ] **Step 3: Mutations:** (m1) shape 2 returns an empty completed contribution instead of raising → the no-fake-read guards red (this is the single most load-bearing mutation of the plan — it recreates the silenced-outage the tombstone forbids); (m2) shape 2 raises WITHOUT running `_assemble_params` (message fabricated) → the resolvability-proof pin red (the test monkeypatch-counts `_assemble_params` calls or asserts the resolved values echo the subject); (m3) shape 3 downgraded to shape 2's message → wiring-defect discrimination red.
- [ ] **Step 4: Run** `python -m pytest tests/orchestration/data/test_subject_projection.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/data/test_data_catalog.py -q` — PASS. Digest probe again (provider bytes are catalog literals, untouched — prove it). **Commit:**

```bash
git add guanlan_v2/orchestration/data/runtime.py guanlan_v2/orchestration/pipeline/live_decide.py tests/orchestration/data/test_subject_projection.py tests/orchestration/test_pm_two_bridges.py
git commit -m "feat(orchestration): retire the dead-row provider per its tombstone - worldless provider refuses loudly on resolvable rows (never an empty read that should have happened)"
```

---

## Task 4: Per-run threading — the subject-scoped factories view + both call sites

**Files:** modify `guanlan_v2/orchestration/pipeline/assembly.py`, `guanlan_v2/orchestration/pipeline/live_decide.py`, `guanlan_v2/orchestration/pipeline/screening.py` (its runner/executor call site — locate at source); modify `tests/orchestration/test_pipeline_assembly.py`, `tests/orchestration/test_pipeline_live_decide.py`, `tests/orchestration/test_pm_two_bridges.py` (seam pins).

**Interfaces — Produces (R2):**

- `assembly.build_production_plan_runner(..., subject_params: SubjectParams | None = None)`. In `_plan_executor`: `factories = bundle.factories if subject_params is None else _SubjectScopedFactories(bundle.factories, provider_ref=phase3_data_surface().provider_ref, factory=worldless_data_provider_factory(subject_params))`. **The override target is chartered to move:** L2-b Task 5 (the L1↔L2-b integration seam) consciously re-targets it to `production_data_provider_factory(subject_params)` and deletes the worldless factory — cross-referenced there by name/number.
- `class _SubjectScopedFactories` — delegating view: overrides `handler_factory(ref)` for exactly the sealed provider ref key `(id, version, content_digest)`; `__getattr__` delegates everything else to the base registry. **Investigation first:** confirm at source that the ONLY consumption points are `worker.py:1687/:1719` (`handler_factory`) and that neither `ExecutionRuntime` nor the resolver isinstance-checks `TrustedFactoryRegistry` (G8; `ExecutionRuntime` is a dataclass — annotation only). If an isinstance/structural check exists, subclass `TrustedFactoryRegistry` sharing the base's internal state instead; if THAT requires touching `worker.py`/`catalog_runtime.py`, **STOP and escalate** (D-0 rejected the ABI-moving option).
- `live_decide`: the materializer call (`:758-770`) gains `subject=subject`; the runner call (`:848-851`) gains `subject_params=materialized.subject_params`; `plan_runner_factory` in `build_production_bindings` (`:1212-1223`) gains the passthrough kwarg (default None — every existing caller/fake unchanged unless it pins the exact signature, in which case: conscious flip).
- `screening`: thread the composite's `subject_params` into its runner invocation the same way (vacuous today per Task 2's pin — but the wire must be real, not decorative: pinned by a seam test that the kwarg arrives at `build_production_plan_runner`).

**Required invariants:**

1. `subject_params=None` ⇒ `factories is bundle.factories` — object identity, pinned (every non-deep caller bitwise unchanged);
2. with `subject_params` bound, the resolver over a captured REAL production bundle (`build_production_catalog_runtime`) constructs a `WorldlessDataBridgeProvider` whose shape-2 refusal echoes the subject's code — the pm-two-bridges seam-test idiom, extended;
3. the memory provider, experience pair and `cand.*` handlers resolve through the view **identically** (delegation pinned by ref-identity assertions);
4. the view rejects nothing the base accepts and accepts nothing the base rejects (delegated `register_handler` still refuses off-catalog ids — one negative pin);
5. `GUANLAN_SEATS_DEEP` unset: watcher behavior bit-unchanged (rerun the P10 regression pins in `test_pipeline_live_decide.py`).

- [ ] **Step 1: Write failing seam tests** (the invariants above; spy-wrap `build_production_plan_runner` through the real `build_production_decide_fn` chain as in `TestTheProductionRegistrationSeam`, asserting the per-run kwarg carries the stamped projection of THE run's committed subject). Run — expected FAIL.
- [ ] **Step 2: Implement.** Flip (same commit, recorded) any pm-two-bridges pin that asserted `ExecutionRuntime` receives `bundle.factories` by identity for the deep path — the new truth: identity when unbound, view-over-it when bound.
- [ ] **Step 3: Mutations:** (m1) view overrides ALL handler refs (returns the worldless factory unconditionally) → delegation pins red; (m2) `subject_params` dropped on the floor in `plan_runner_factory` (accepted, not passed) → the seam echo test red — this is the exact "half-wired kwarg" defect class the campaign kept catching; (m3) live_decide passes a freshly-projected subject instead of `materialized.subject_params` → the one-recipe provenance pin red.
- [ ] **Step 4: Run** `python -m pytest tests/orchestration/test_pipeline_assembly.py tests/orchestration/test_pipeline_live_decide.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/test_pipeline_screening.py -q` — PASS. **Commit:**

```bash
git add guanlan_v2/orchestration/pipeline/assembly.py guanlan_v2/orchestration/pipeline/live_decide.py guanlan_v2/orchestration/pipeline/screening.py tests/orchestration/test_pipeline_assembly.py tests/orchestration/test_pipeline_live_decide.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/test_pipeline_screening.py
git commit -m "feat(orchestration): per-run subject-scoped factories view - the projection reaches the data bridge through the ONE production runner seam"
```

---

## Task 5: Reconciliation sweep — comments, flip inventory, full-suite + digest gates

**Files:** modify `guanlan_v2/orchestration/data/catalog.py` (comments only), any residual digest-free docstrings in `data/runtime.py`/`deep_decide.py` still stating the pre-L1 world; task report with the consolidated flip table.

- [ ] **Step 1:** Update the `#:` note on `_REVIEWED_INTEGRATION_GRANTS` (`catalog.py:94-99`) and the block comment above the prefetch-binding construction (`:517` region): the row is RUNNABLE under the materialization-stamped subject projection (this plan, dated), SERVABLE only once L2-b binds a production world; rewriting the binding bytes still belongs to the re-freeze phase. Update `deep_decide.py`'s reality-4 prose ("threading … the instrument-param data prefetch is clause E2b" → landed here) and any lingering "subject->data projection … not built" phrasing found by `grep -rn "NOT RUNNABLE\|projection.*is built\|structurally dead" guanlan_v2/orchestration` — comments/docstrings on non-registered classes only; **registered-model docstrings are digest-bearing and must not be touched** (G2/G6).
- [ ] **Step 2: Digest probe, final:** before/after across Tasks 1–5 cumulatively (compare against the values in `fix-pm-dead-binding-report.md` §5 — they are the sealed truth) + golden test `test_matches_the_frozen_golden_manifest` + `python -m compileall -q guanlan_v2/orchestration`.
- [ ] **Step 3: Full foreground run:** `python -m pytest tests/orchestration -q` (two deterministic halves if the cap binds; `--collect-only` cross-check). Reconcile the count against the measured baseline + this plan's additions; prove any alien failures are the concurrent session's by isolation. Rerun `pytest tests/orchestration/data tests/orchestration/test_phase10_chain.py tests/orchestration/test_phase10_handoff.py -q` as the golden/chain gate.
- [ ] **Step 4:** Consolidate the **flip inventory** (every pin flipped in T1/T3/T4, old→new, with the bidirectional evidence pointers) into the task report. Verify `git diff main -- guanlan_v2/orchestration/spec.py` is empty and `assembly.py:945-948` is unedited.
- [ ] **Step 5: Commit:**

```bash
git add guanlan_v2/orchestration/data/catalog.py guanlan_v2/orchestration/data/runtime.py guanlan_v2/orchestration/pipeline/deep_decide.py
git commit -m "docs(orchestration): the honesty notes tell the post-L1 truth - runnable under the projection, servable only after L2-b"
```

(Adjust the pathspec to the files actually holding stale prose; never widen it.)

---

## Task 6: Live verification — a real deep run must show the NEW refusal shape

> The campaign's law: 13 defects were invisible to a 5600-green tree. This task is a REAL run — real storage, real admission, real LLM spend (deepseek tokens authorized), real `config/llm.yaml` seats — not a fixture. It runs on **9998** (9999 is watchdog-guarded; do not kill it casually — memory `watchdog-9999`: the environment snapshot poisons the generational chain, and `server.py` runs as a SCRIPT, so any module-level `import guanlan_v2.*` breaks production while the suite stays green — rerun `tests/desktop`/`test_server_script_launch.py` guards if the startup path was touched, which this plan does NOT touch).

- [ ] **Step 1:** Start 9998 from this branch (the campaign's Start-Server / lane0_driver handbook in `.superpowers/sdd/progress-orchestration.md` tail). Confirm a committed Lane-0 `ContextSnapshot` exists for today (run the Lane-0 driver CLI if needed — it is bound and proven as of 07-31).
- [ ] **Step 2:** Drive one deep-lane escalation on a **fresh code** for today (a spent identity family `{code, session, opt_in}` will replay its receipt — the campaign's lease手法: fresh code ⇒ fresh run identity immediately), reduced preset, strategy `deep_research` opt-in, bounded lease `max_admissions=1`.
- [ ] **Step 3: Assert, from the run's durable artifacts (not from logs alone):**
  1. the trunk executed: sentiment → bull → bear → research-mgr completed with real LLM output (4 invocations settled);
  2. **`dec.pm` failed at bridge EXECUTION** (`bridge_execution_error`), and the recorded reason contains the frozen marker `params resolved from the run subject projection`, **the run's own code and session as-of**, and the L2-b naming — the projection provably carried THE committed subject into the bridge (this is the plan's live exit criterion);
  3. pm consumed ZERO LLM invocations (the refusal fires before the model call) and zero capability invocations were charged;
  4. trader blocked honestly; the seats row records the deep failure arm (`deep_outcome` = the failed/refused arm actually taken — record WHICH, with the ledger line quoted; **「控制器已裁」no distinct outcome label for the L2-b window — the generic failed arm + the ledger note suffice**) and the fast verdict stands;
  5. the watcher's next tick is not starved (the durable failure receipt / budget behavior per charter §2.2-7 — observe, record; fixing any starvation found is OUT of scope, file it).
- [ ] **Step 4:** Rerun the same identity — the receipt/idempotency behavior must hold (no double spend).
- [ ] **Step 5:** Write the campaign-ledger entry in `.superpowers/sdd/progress-orchestration.md`: what this run PROVED (the projection resolves the sealed row from the committed subject in production; the refusal is the new honest shape) and what it did NOT prove (no data was read — L2-b remains; the ten-node preset remains refused — L3 remains). Update the tombstone expectations table if any observed arm differs from this plan's prediction — observed truth wins, prediction differences are findings, not embarrassments.
- [ ] **Step 6:** `.superpowers/` is **gitignored** — a commit of that path is structurally empty (seam-review finding). Copy the campaign entry to `docs/superpowers/ledgers/2026-07-31-L1-live-verification.md` (the in-git ledger home — the 07-26 post-merge precedent) and commit THAT path only, explicit pathspec, nothing else. 9999 is NOT deployed here: **「控制器已裁」the one-train release deploys 9999 only after L2-b lands** (L2-b's live task is the train's deployment point).

---

## Exit Gates

- [ ] The REAL sealed `dec.pm`/`verified_snapshot` row resolves through `_assemble_params` + `InstrumentUniverseParams.model_validate` from a projected committed subject (real-row round-trip test green) — and still refuses, cause-named, without the projection.
- [ ] `spec.py` untouched; `assembly.py:945-948` untouched; NO new `ParamBinding.source_kind`; `SubjectParams` unregistered (`RUNTIME_PUBLIC_MODELS == ()`); sealed digest table (provider/analyzer/prefetch/descriptor materials, `binding_digest`, `PHASE3_DATA_CATALOG_DIGEST`, the three binding-model JSON-Schema digests) byte-identical to `fix-pm-dead-binding-report.md` §5; zero preset-record digest movement; goldens byte-identical.
- [ ] `StructurallyDeadRowDataProvider` and its empty-complete branch are GONE (grep pin); the worldless provider's three shapes are all loud; mutation m1 of Task 3 (empty instead of raise) demonstrably reddens the guards.
- [ ] `data_runtime_provider_factory` still has zero production callers (the L2-b pin stays green untouched) — **unless L2-b has already landed by gate time, in which case the flip is expected and owned by L2-b Task 4** (under the one-train order that is the train's normal end state, not a violation).
- [ ] Every flipped pin is inventoried with bidirectional evidence; nothing went green silently.
- [ ] `subject_params=None` ⇒ factories object identity preserved; `GUANLAN_SEATS_DEEP` unset ⇒ watcher bit-unchanged (P10 regression pins green).
- [ ] Full `tests/orchestration` foreground green (count reconciled; alien failures isolated-and-proven); `compileall` clean.
- [ ] One REAL deep run on 9998 exhibits the new refusal shape carrying the run's own subject values, with zero pm LLM spend and honest degradation — recorded in the campaign ledger with proved/not-proved stated.

## Execution Handoff

Execute task-by-task with fresh implementer subagents; each brief carries its task section verbatim plus the Ground truth and Design resolution sections. HEAD moves under concurrent sessions — re-verify every `file:line` anchor by symbol name before editing; the briefed base may be stale (the pm-two-bridges forensics: never trust a briefed commit id you cannot `git cat-file`). Task order is strict 1→6 (each consumes the previous task's names).

**Controller rulings (已裁 — folded in at plan freeze; do not re-open):**

1. **The L1→L2-b window (formerly open question 1):** 「控制器已裁」L1 → L2-b is the declared execution order, released as **ONE train** — L1 is verified live on 9998 (Task 6); 9999 deploys only after L2-b lands. No honest-refusal production window exists.
2. **The singleton-universe semantic (formerly open question 2):** 「控制器已裁」**RATIFIED** — `/code → /symbols` projects as the one-element instrument set (`(Symbol(code),)`), recorded as reviewed semantics in `SubjectParams.project`, with the watcher-canonical code format mapped to `Symbol` via the existing reviewed constructor found in Task 1. The Task-1 escalation trigger stays live (it fires only if no reviewed constructor exists).
3. **`deep_outcome` vocabulary (formerly open question 3):** 「控制器已裁」no distinct outcome label for the L2-b window — the generic failed arm + the Task-6 ledger note suffice (the one-train release makes the window non-production anyway).
4. **Screening thread depth (formerly open question 4):** 「控制器已裁」screening stays in L1 — the deferral option is deleted; Task 2/4's screening stamping and threading stand, vacuity pin and all.

**Cross-plan seam (orientation):** L1's Test-5 source-text pin, the worldless factory as the per-run view's override target, and the pm behavioural pins are all consciously flipped by **L2-b Task 5 (the L1↔L2-b integration seam)** — each flip cross-referenced bidirectionally in both plans.
