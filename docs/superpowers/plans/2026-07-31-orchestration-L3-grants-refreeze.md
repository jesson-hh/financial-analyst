# Orchestration L3 · 补授权 + 一次性重冻 (grants + the one-shot re-freeze) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Grant `pv.technical` and `text.news` their reviewed data authorization + one-to-one prefetch rows so the **full ten-node deep-decide preset** (and the screening lane, which also schedules `text.news`) passes `check_runtime_support` for real — then move the P3→P10 sealed digest chain in **exactly one inventoried, re-reviewed re-freeze operation**, consciously flip every test that today pins the support-refusal as the honest state, align the sealed material texts that still state pre-C3 invariants, convert the rowless-experience-worker late failure into an early admission refusal, and prove the whole thing with a **real LLM, real-data, ten-node production run** whose `pv.technical` and `text.news` seats perform genuine data reads. The reduced eight-node preset (`pipeline.luozi_deep_decide_reduced`, badge `reduced_evidence_preset_v1`) survives as the **cheap tier / fallback**, not as a workaround.

**Charter:** `docs/superpowers/specs/2026-07-29-post-p10-refreeze-design.md` — §1 facts A–E, §1.5 facts F/G/H + the L-layering, §1.6 fact I, §2.1 items 1–6, §2.2 as explicit include/defer decisions, §4 exit gates verbatim. This plan is **L3 only**. Nine phases of "上游 golden 零位移" discipline end here by explicit charter: this is the **only** plan ever authorized to move the sealed chain, and it must do so once, as a unit, with a per-digest written reason.

**Hard dependency — DO NOT START before L1 and L2-b land** (they release as ONE train, L1 → L2-b, 「控制器已裁」). The grants are meaningless without (a) the L1 subject→data projection (ruling D-0 **= option (i), decided**: materialization-time stamping from the committed `RunSubject@1` through the CLOSED projection — the sealed binding bytes stay untouched; the `node_param` pointers are served **out-of-band** from `SUBJECT_PARAM_POINTERS`; the same ruling that healed defect H's DECLARED-but-NOT-RUNNABLE `dec.pm` row), and (b) the L2-b production `DataRuntimeWorld` (real sources registered by source id, provider factory bound into the executor; the retired `StructurallyDeadRowDataProvider`'s worldless successor was itself retired via L2-b's Task-4 supersede + Task-5 integration seam, which carries the run subject into the real session). Granting rows that structurally cannot bind params, or that bind into a world with no adapters, would recreate defect H twice over — the exact class of dead reviewed row this program just spent a task pinning out of existence. Task 1 is an executable gate on both.

## Ground truth this plan builds on (measured, not guessed)

All line numbers re-verified at HEAD `0c601b5` (branch `report-evidence-pack` == main + ~20 campaign commits; the spec's §1 line numbers predate two honesty-note insertions and are noted where drifted).

- **The one grant:** `guanlan_v2/orchestration/data/catalog.py:97-99` — `_REVIEWED_INTEGRATION_GRANTS = {"dec.pm": ("verified_snapshot",)}`. Its row was DECLARED-but-NOT-RUNNABLE (defect H, commit `ff26c23` pins it; L1 heals it).
- **One-to-one invariant:** `catalog.py:792-799` (spec cites :766-773 pre-drift) — `granted != rows` → `CatalogError` naming both directions. `granted` derives from worker `capability_allowlist` ∩ data capability refs, so it is **per (worker, capability)**, not per worker.
- **The two workers' P8 allowlists** (`guanlan_v2/orchestration/lane_catalog.py`): `text.news` `capability_methods=("news","fundamentals")`, `tool_calls=REQUIRED` (:203-206); `pv.technical` `capability_methods=("verified_snapshot","indicators")`, `tool_calls=REQUIRED` (:424-427). Literal one-to-one therefore forces **FOUR rows** — this is ruling **D-1** (Task 1 gate).
- **ParamBinding closed kinds:** `catalog.py:105-143` — `node_param | input_value | const`, closed by Literal + validator. Both workers are params-less in a sealed v2 preset (`pipeline/assembly.py:945-948` "structurally code-free"). Under ruling D-0 = option (i), a `node_param` pointer at a `params_schema_ref=None` worker is the **HEALED** shape, not defect H: the sealed bindings stay byte-untouched and are served **out-of-band** by L1's subject projection. Defect H was a pointer with **no serving source** (`spec.py:951-954` made in-band params impossible; pre-L1 nothing served them out-of-band). So every new row's `node_param` pointers must be **⊆ L1's `SUBJECT_PARAM_POINTERS` (`{"/asof_date", "/code"}`)** — the closed set the projection serves — in the same shape as the healed `dec.pm` row. A pointer outside the closed set has no source and would recreate defect H; serving it means **L1-module movement** (widening `SUBJECT_PARAM_POINTERS` + the projection recipe), which enters the Task-1 inventory and the D-1 packet, never improvised.
- **`always_invoke` arithmetic:** `catalog.py:152-155` (docstring: never inferred from `tool_calls=REQUIRED`) + `row_min_finalized` :188-193 (`1` only for `always_invoke` + `success_requires_finalized_call=True`). A REQUIRED worker clears `tool_calls_required_unmet` (`pipeline/api.py:1187`, `pipeline/assembly.py:469`) only via such a row — this is ruling **D-2** (Task 1 gate).
- **Blast radius (2026-07-29 recon, spec §1):** **11 digests, 42 frozen literals, 16 files (9 golden manifests + 7 handoff tests)**. Chain, each base == the prior result: P2 `b41bf223` **does not move** → P3-data `ba708692` → P3-full `c13294e5` → P4 `aefe0cf3` → P5 `42af2460` → P6 (identity) → P7 `c760df02` → P8 `7f00dde4` → P9 `0c48db78` → P10 `ff4cdc61`. **Does not move:** the 7 `data_capabilities`, the four data-bridge material digests (`3a1e727b…` provider / `3f93fdf2…` analyzer / prefetch / descriptor **only if D-1/D-2 leave the binding untouched — they will not; see Task 5's inventory**), renderer + source handles, `planner_spec_digest`, and the 10 schema manifests **provided no registered model schema is touched** — in particular the three digest-bearing class docstrings of `ParamBinding` / `DataPrefetchOperation` / `DataBridgePrefetchBinding` (`catalog.py:889-893` registration; pydantic class docstring == JSON-Schema `description`) are **untouchable**.
- **Support-refusal pins to flip (item 4):** Task-11's "honestly-unsupported set EXACTLY `{pv.technical, text.news}`" pins, the `test_pipeline_deep_preset.py` support-refused pins, and the reduced preset's raison-d'être tests (`pipeline/deep_decide.py` ~:322-334: "the full lane's honest refusal" direction) — every one flips **consciously, with bidirectional evidence, in the re-freeze commit sequence**, never "顺带就绿了".
- **Sealed material texts stating pre-C3 invariants (item 5):** `bootstrap.py:463` + `memory/catalog.py:270` byte-frozen handler texts (state the OPPOSITE of the ruled rows==0 behavior; P10 could only record the drift in comments); the regime prompt material (unsorted drivers + unconditional-anchor wording — the model-side symptom was observed live 07-31); the deep preset description's `pv.microstructure` overstatement (inside golden `d7c5092f`). All material-byte changes ride THIS re-freeze.
- **Rowless experience worker (items 2+6):** facts at `bootstrap.py:278-316`/`:477-481`, `runtime_support.py:676-679`; `ExperiencePrefetchBinding@1` needs a feature-vector pointer no worker has; `tool_calls=FORBIDDEN` conflicts with the row's Literal-pinned min=1. Campaign ruling `4ee43ca` already made a non-granted worker freeze honestly EMPTY on the deep runner path; the DYNAMIC-planner late-failure-at-gateway residue (Task-11 minor #1) is what item 6 moves to admission time.
- **Live campaign facts (07-29..31):** reduced eight-node preset ran END-TO-END COMPLETED with real deepseek (run `deep-b580b7c90a2429cb`, 五粮液 000858, eight nodes, ledger `source="orchestrated"`, citation chain closed, zero fabrication); 13 defects invisible to a 5600-green tree were found only by real runs; Lane-0 driver CLI (`lane0_driver` propose/approve/run, `--attempt N`), lease scripting, and one-session-identity-per-day are sedimented method. **Every plan in this program ends with a real run. Tokens are authorized.**

## Global Constraints

These extend, never override, the Phase 1–10 Global Constraints, the integration spec §8 red lines, and the re-freeze spec §3 discipline. Every task implicitly includes those documents.

- **TDD, RED first.** Every "Write failing tests" step runs the focused command and records the expected failure before implementation; collection/environment errors do not count as red.
- **Mutate→red→revert** for every load-bearing guard this plan adds or flips (the extended one-to-one check both directions, the D-2 arithmetic, the early admission refusal, the flipped support pins, the durable failure receipt). Sources restored byte-identical and sha256-verified. A RED that proves nothing is worse than no RED (2d2a792 lesson) — each mutation must redden exactly the guards that own it.
- **Honest typed refusals over silent fallback.** This plan turns refusals into support — never into silence (spec §2.4). No existing "诚实拒绝优先于静默降级" gate is relaxed.
- **Zero sealed-golden movement OUTSIDE the chartered window.** Tasks 1–2 and 6–7 move nothing sealed. Tasks 3–4 open a **red-window** in which the FULL `tests/orchestration` suite may be red **only** on the Task-1-inventoried pin set (any red outside the inventory = STOP, escalate). Task 5 closes the window with the one-shot regeneration; from its final commit onward the whole suite is green and any later golden movement is a regression.
- **Conscious flips.** Any test pinned on "the gap exists" flips explicitly with before/after evidence in the flipping commit's message and the task report. No pin silently disappears.
- **Explicit-pathspec commits.** The branch carries concurrent sessions' 13-day-old uncommitted work. Never stage: `guanlan_v2/console/*`, `guanlan_v2/datafeed/*`, `guanlan_v2/glmcp/*`, `guanlan_v2/server.py`, `ui/*`, `.data/wisdom/*`, `docs/README.md`, `guanlan_v2/strategy/_provenance.json`, `tests/test_console_tools.py`, `tests/test_datafeed_*.py`, `tests/test_guanlan_mcp.py`, `tests/test_agent_interface_doc.py`, `scripts/gen_agent_interface_doc.py`, `guanlan_v2/datafeed/prewarm.py`, `tests/test_datafeed_prewarm.py`, `docs/agent_data_interfaces.md`, `EV-*.md`, `p6-rerank-badges.jpeg`, `guanlan_v2/factorlib/mined/*`, `docs/superpowers/plans/2026-07-06-*.md`. `git add -A` / `git add .` / bare `git commit -a` forbidden.
- **LLM never buys/sells.** The deep chain's product remains advisory (`PortfolioDecision@1` → Phase-6 position bands); nothing in this plan touches order execution.
- **Real-run verification is mandatory.** The campaign proved 13 defects a 5600-green tree could not see. Task 7 is a REAL production run (real durable stores, real LLM seats, real data reads), not a fixture drive, and the ledger records what the run proved and did not prove.
- **Production runs as a script.** `server.py` is launched as a script — module-level `import guanlan_v2.*` kills 9999 while the suite stays green (watchdog-9999 坑④). If any task touches the startup path, `test_server_script_launch.py` runs in its verification step. Verify on 9998 before restarting 9999.
- No placeholders, DRY, YAGNI, frequent commits.

---

## Task 1: Preconditions gate + blast-radius inventory + the D-1/D-2 ruling packet (CONTROLLER GATE)

The L1/L2-b handoff is an executable test, the 16-file inventory becomes a committed checklist, and the two rulings this plan may NOT make itself are put in front of the controller. **No later task starts until the controller answers D-1 and D-2.**

**Files:**
- Create: `tests/orchestration/test_l3_refreeze_gate.py`
- Create: `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md`

**Steps:**

- [ ] **Step 1: Executable L1/L2-b gate.** The test must prove, against the real tree (no fixtures standing in):
  1. **L1 landed:** the `dec.pm` / `verified_snapshot` row is RUNNABLE — the real D-0 option (i) projection path (`SubjectParams` / `SUBJECT_PARAM_POINTERS`) resolves its param bindings from a committed `RunSubject@1` without `DataRuntimeError: param source pointer … does not resolve`; the `ff26c23` DECLARED-but-NOT-RUNNABLE pins have been consciously flipped by the L1 plan (assert the flip happened — if those pins still assert not-runnable, STOP: L1 has not landed).
  2. **L2-b landed:** a production `DataRuntimeWorld` construction exists outside `tests/` (the `data_runtime_provider_factory` zero-production-callers AST pin must have been consciously flipped by L2-b); `StructurallyDeadRowDataProvider` is retired per its tombstone AND its worldless successor `WorldlessDataBridgeProvider` is retired too (L2-b Task 5, the L1↔L2-b integration seam): the registered `bridge.data_runtime.provider@1` factory on the production bundle is the production provider, and the per-run view's override target is `production_data_provider_factory(subject_params)` — L3 granting rows would otherwise route real reads into a provider that fabricates emptiness or refuses worldlessly.
  3. **Adapter inventory (feeds D-1):** enumerate, by source id, which of the 7 data methods (`instrument_names`, `ohlcv`, `indicators`, `verified_snapshot`, `fundamentals`, `news`, `signals`) have a REAL registered backend adapter in the L2-b world at gate time. Record the list verbatim in the ruling packet. Do not assume the spec's 07-29 statement ("indicators/fundamentals 无后端适配器") still holds — L2-b may have built them.
  4. **Baseline digests:** capture the current values of every chain digest (P3-data → P10) and assert they equal the spec §1 chain values — if ANY differs, the 07-29 blast-radius inventory is stale and the whole plan's counts must be re-derived before proceeding (this is the plan's riskiest assumption made executable).
  5. **Pointer-set satisfiability (feeds D-1 — seam-review finding 4):** for every candidate granted method under the D-1 options, prove executable that its `params_cls` is satisfiable from L1's closed `SUBJECT_PARAM_POINTERS` via the subject projection (the L1 real-row round-trip idiom: `_assemble_params` + `params_cls.model_validate` over a candidate-shaped row and a projected subject). A method whose params need a pointer outside the closed set = **L1-module movement** (widen the pointer set + the projection recipe): record it in the inventory and as a named cost in the D-1 packet — never improvised at Task 3.
- [ ] **Step 2: Build the inventory checklist** (`docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md`): grep the frozen chain-digest literals across `tests/orchestration/` and enumerate the exact **16 files / 42 literals / 11 digests** — name every golden manifest file, every handoff test, every literal with file:line, and every support-refusal pin that Task 5 will flip (`test_pipeline_deep_preset.py`, `test_pipeline_reduced_deep_preset.py`, the Task-11 "EXACTLY {pv.technical, text.news}" pins, `pipeline/api.py:1187`-adjacent doc pins). **Sibling-plan pins, enumerated by grep (seam-review finding 5 — the allowed-red set is incomplete without them):** `aux_data_ungranted` (L2-b's pv-aux typed-refusal pins in `tests/orchestration/test_pv_aux_nodes.py` — they flip iff D-1 grants the aux workers rows); L2-b's supported-methods guard (`granted ⊆ {verified_snapshot, news, ohlcv}` in `tests/orchestration/test_data_world_recipe.py` — any grant of a method outside `LiveClientSource`'s supported set reddens it FIRST, by design); and L2-b's route-coverage pins (the per-sealed-row route-equality and all-seven-method default-route pins in the same file — every new row enters their iteration). Grep the literal strings, list every hit with file:line, and mark each as flip vs stays-green-must-verify. This document is Task 3–4's allowed-red set and Task 5's regeneration checklist. If the count differs from 16/42/11, record the measured truth and why (L1/L2-b may have added pins) — the measured set governs, the spec's counts are the cross-check.
- [ ] **Step 3: Write the D-1 ruling packet** (into the inventory ledger, section "RULING D-1 — one-to-one grant granularity"). Present all three options with the gate's measured adapter inventory attached; the controller decides:
  - **Option A — narrow the two allowlists** to exactly the capabilities with real adapters, so the literal per-capability invariant forces only adapter-backed rows. Cost: `pv.technical`/`text.news` WorkerSpec digests move — **P8 worker digests are already inside this plan's re-freeze scope**, so the movement rides Task 5 at zero extra blast radius. Risk: narrows a reviewed P8 grant (e.g. dropping `fundamentals` from a news reader that promises `ww_f10`) — the narrowing must be argued per capability, not blanket.
  - **Option B — build the missing adapters** (L2-b extension: `indicators` and/or `fundamentals` backends). Cost: real backend work outside L3's fence, delays this plan; benefit: the P8 review stands untouched and all four rows are honest.
  - **Option C — relax the one-to-one invariant** (e.g. grants-without-rows tolerated for named capabilities). **Recommended against**: the invariant at `catalog.py:792-799` is the least-privilege guarantee that made rows==0 unambiguous for ruling C3, it is mutation-proven load-bearing, and relaxing it re-opens the silent-grant hole in both directions.
  - **Executable cost per option (seam-review finding 5):** the packet states, for each option, what it does to L2-b's supported-methods guard (`granted ⊆ {verified_snapshot, news, ohlcv}`): Option A trips nothing only if the narrowed grants land inside the supported set (assert which); Option B's executable cost IS that guard — the adapter must grow (and the guard flip consciously) before the grant goes live; Option C leaves the guard standing but no longer protecting (grants without rows bypass it). Attach the Step-1-item-5 pointer-set satisfiability result per candidate method — a method that is adapter-backed but pointer-unsatisfiable is L1-module work, priced separately.
  - **Recommendation: A where the adapter is missing at gate time, B only if L2-b already shipped the backend** (then the row is free). Whatever is ruled, the invariant itself stays byte-identical.
- [ ] **Step 4: Write the D-2 ruling packet** (section "RULING D-2 — always_invoke and outage semantics"). Both workers are `tool_calls=REQUIRED`; the support arithmetic clears `tool_calls_required_unmet` only via `always_invoke` + `success_requires_finalized_call=True` (never inferred — `catalog.py:152-155`). State the production consequence plainly: **a data outage becomes a hard node failure** for a news/technical reader. Options:
  - **Option A — accept always_invoke + required-finalized on the new rows.** Honest: a reader whose entire value is the data must not claim success having read nothing. Consequence bounded: in the full deep preset both workers are AUX evidence nodes flowing into bull/bear with degrade policy — an outage degrades the debate loudly (aux node hard-fail in the **post-L2-b failure vocabulary: a gateway/adapter execution failure raised from the delegated session's dispatch — NOT `bridge_execution_error`, which L2-b retired on this path** — plus degrade badge) and the trunk `sentiment→research-mgr→pm→trader` completes; Task 5 must PIN this degradation path for both presets so the claim is executable, not asserted. **Same-family precedent (「控制器已裁」, cross-referenced from L2-b):** `verified_snapshot` outage ⇒ pm node hard-fail ⇒ deep run refuses honestly was ACCEPTED for L2-b's phase (recorded at its Task 3); Option A extends the same posture to the aux readers.
  - **Option B — flip the two workers' `tool_calls` REQUIRED→OPTIONAL** (P8 digest movement, also already in scope) and grant `cache_or_invoke` rows. Consequence: outage = node "success" with stale-or-empty evidence — the silent-degradation shape this program refuses on principle.
  - **Recommendation: A.** It is the only shape consistent with "把拒绝变成支持,不是把拒绝变成沉默" (spec §2.4).
- [ ] **Step 5: Run** `pytest tests/orchestration/test_l3_refreeze_gate.py -v` — all green (or STOP recorded). Full `tests/orchestration` green (window not yet open).
- [ ] **Step 6: Commit, then STOP for the controller's D-1/D-2 rulings.** Record the rulings verbatim in the inventory ledger before dispatching Task 2.

```bash
git add tests/orchestration/test_l3_refreeze_gate.py docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "test(orchestration): L3 gate - L1/L2-b handoff proven, re-freeze inventory frozen, D-1/D-2 ruling packets tabled"
```

---

## Task 2: Rowless experience worker — early admission refusal + the research_mgr experience ruling (§2.1 items 6 + 2)

Green-preserving (no digest movement) — lands BEFORE the window opens so its guards protect the window's live verification.

**Files:**
- Modify: `guanlan_v2/orchestration/runtime_support.py` (or `admission.py` — wherever the support analysis that admission consumes lives; the implementer binds to the real seam, correction-clause style)
- Test: `tests/orchestration/test_experience_rowless_admission.py` (create)
- Modify: `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` (ruling record)

**Steps:**

- [ ] **Step 1: Re-verify current behavior at HEAD first.** The charter facts predate campaign commit `4ee43ca` (non-granted worker freezes honestly EMPTY on the deep-runner path). Drive a DYNAMIC (planner-sourced) plan carrying a worker that declares the experience read category but has no reviewed experience row through real admission + real gateway: record where it fails TODAY (late `max_capability_invocations=0` gateway failure per Task-11 minor #1, or already honest-empty post-4ee43ca). The task's shape depends on the answer; both branches below.
- [ ] **Step 2 (RED): pin the required end state** — a rowless experience worker in a DYNAMIC plan is refused **at admission** with a typed, named refusal (issue code naming the worker and the missing row), never admitted-then-gateway-failed. If Step 1 found the late failure: RED = the new admission test fails because admission passes. If Step 1 found honest-empty: the refusal applies only to the shape that STILL fails late (a worker whose declared tool requirements make empty structurally un-runnable) — pin that discrimination and pin honest-empty as the preserved behavior for the C3 rowless-reader shape (do not regress ruling C3: rowless reader ≠ refusal).
- [ ] **Step 3: Implement the early refusal.** Additive check in the support analysis; no sealed material bytes, no schema, no golden. Mutate→red→revert: silence the check → exactly the new admission pins red; the late-failure pin (kept as a control) proves the gateway path is now unreachable for admitted plans.
- [ ] **Step 4: Record the item-2 ruling** (inventory ledger, section "RULING — dec.research_mgr experience row"). **「控制器已裁」(pre-ruled at plan freeze): Option A is the ruling** — rowless-EMPTY is the reviewed end state for `dec.research_mgr`'s experience this phase (consistent with C3 / `4ee43ca`); the real-experience-row derivation stays on the post-L3 ledger. This step RECORDS the ruling; Option B stays below as the documented road-not-taken (no controller round-trip needed at execution time):
  - **Option A (RULED — 控制器已裁): rowless-EMPTY is the reviewed end state for this phase.** Consistent with C3 and `4ee43ca`; the live reduced run proved research-mgr completes honestly with an empty contribution (4 LLM invocations settled). Deriving a real row remains blocked on facts the worker does not have (`ExperiencePrefetchBinding@1` feature-vector pointer) and a Literal conflict (`tool_calls=FORBIDDEN` vs min=1) — resolving those is a contract change beyond L3's fence.
  - **Option B: derive the row this phase** — requires ruling where the feature-vector pointer comes from (widen worker capability vs change the binding contract), moves additional digests, grows the window. Only take if the controller wants experience-grounded research-mgr output before the next phase.
  - Whichever is ruled, the FORBIDDEN↔min=1 conflict ruling lands in the ledger (§2.1 item 2's "冲突裁决落文档" is satisfied by Option A + this record).
- [ ] **Step 5: Run** focused file + `pytest tests/orchestration` — FULL GREEN (window still closed).
- [ ] **Step 6: Commit.**

```bash
git add guanlan_v2/orchestration/runtime_support.py tests/orchestration/test_experience_rowless_admission.py docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "feat(orchestration): rowless experience worker refused at admission, not at the gateway - and the research_mgr experience ruling recorded"
```

*(pathspec adjusted to the real seam file if it is `admission.py`)*

---

## Task 3: The two grants + their one-to-one rows (§2.1 item 1) — **the red-window opens**

The semantic change. From this task's commit until Task 5's final commit, `tests/orchestration` is red on the inventoried pin set ONLY.

**Files:**
- Modify: `guanlan_v2/orchestration/data/catalog.py` (`_REVIEWED_INTEGRATION_GRANTS` + prefetch-binding construction + reviewed worker updates path)
- Modify: `guanlan_v2/orchestration/lane_catalog.py` (ONLY if D-1 ruled Option A allowlist narrowing, or D-2 ruled Option B)
- Test: `tests/orchestration/data/test_data_catalog.py` (extend), `tests/orchestration/test_l3_grants.py` (create)

**Steps:**

- [ ] **Step 1 (RED): write the grant pins.** For each granted (worker, capability) pair per ruling D-1: the built data surface carries a prefetch row; the row's `invocation_mode`/`success_requires_finalized_call` match ruling D-2 exactly; param bindings are `node_param` pointers **⊆ `SUBJECT_PARAM_POINTERS`** served out-of-band by the L1 subject projection (assert BOTH: shape equality against the healed `dec.pm` row's binding kinds, AND every pointer ∈ the closed set — plus the Task-1-item-5 satisfiability round-trip per new row: `_assemble_params` + `params_cls.model_validate` from a projected subject; the executable guards against re-creating defect H); the route's sources cross-resolve to L2-b-registered adapters (the gate's adapter inventory, asserted by source id). A pointer outside the closed set at this stage = STOP — L1-module movement was priced in the D-1 packet, never improvised here. RED: rows absent.
- [ ] **Step 2: implement the grants** through the existing reviewed entry (`_REVIEWED_INTEGRATION_GRANTS` + the surface's prefetch construction + `build_phase3_catalog`'s `reviewed_worker_updates` path — NOT a new mechanism, NOT a second descriptor for `bridge.data_runtime` (fact C forbids it, `catalog_runtime.py:640-644`)). **Fact-A reconciliation, binding:** `pv.technical`/`text.news` are Phase-8 lane workers, not Phase-2 workers — `build_phase3_catalog` refuses updates naming non-P2 workers (:744-745) and `phase3_data_catalog_snapshot` StopIterations on them (fact A). The implementer must bind to the real seam where grants meet the FULL catalog build (the P3-full → P8 fold where `granted` is derived from lane-worker allowlists) and record the exact mechanics in the task report; if the only honest seam requires widening a P3 builder signature, that is a correction-clause plan amendment, not an improvisation.
- [ ] **Step 3: mutation-prove the extended one-to-one guard BOTH directions** at the new scale: (a) grant without row → `CatalogError` naming the missing row; (b) row without grant → `CatalogError` naming the ungranted row; (c) D-2 arithmetic: flip a new row's `success_requires_finalized_call` to False → exactly the `tool_calls_required_unmet`-clearing pins red. Each mutation reverted byte-identical.
- [ ] **Step 4: support flips — measure, do not touch.** Run the full-catalog support check for the ten-node deep draft and the screening draft: `support_report.supported is True` for both (this is the semantic payoff; pin it in `test_l3_grants.py`). Run FULL `pytest tests/orchestration`; diff the red set against the Task-1 inventory: **red ⊆ inventory required**. Record the actual red list in the inventory ledger (it becomes Task 5's worklist). Any red outside the inventory = STOP, escalate to controller.
- [ ] **Step 5: Commit** (focused tests green; the window's red set recorded, goldens NOT regenerated).

```bash
git add guanlan_v2/orchestration/data/catalog.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/data/test_data_catalog.py tests/orchestration/test_l3_grants.py docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "feat(orchestration): pv.technical + text.news reviewed data grants with one-to-one prefetch rows per rulings D-1/D-2 - re-freeze window OPEN"
```

---

## Task 4: Sealed material texts aligned with post-ruling reality (§2.1 item 5)

Material-byte changes that were forbidden for ten phases because they move digests — they ride this window or never.

**Files:**
- Modify: `guanlan_v2/orchestration/bootstrap.py` (:463 handler text)
- Modify: `guanlan_v2/orchestration/memory/catalog.py` (:270 handler text)
- Modify: the regime prompt material file under `config/orchestration/materials/` (exact path from the material manifest — the prompt whose wording produces unsorted drivers + an unconditional anchor)
- Modify: `config/orchestration/presets/v2/luozi_deep_decide_v1.json` (`pv.microstructure` description overstatement — golden `d7c5092f` moves; already inventoried)
- Test: `tests/orchestration/test_l3_material_texts.py` (create)

**Steps:**

- [ ] **Step 1 (RED): pin the corrected invariants as text assertions** — the bootstrap and memory handler texts must STATE the C3 rows==0 behavior (rowless reader → honest EMPTY, rows>1/activation-drift LOUD), not its opposite; the drift-recording comments P10 left behind (bootstrap.py precedent) must be REMOVED in the same commit (they exist only because the bytes could not move — leaving both would state the invariant twice, once wrong). RED: current bytes state the pre-C3 invariant.
- [ ] **Step 2: regime prompt** — require sorted drivers and a conditional anchor (anchor stated only when its condition holds). The 07-31 live observation ("drivers 未排序=模型侧") means the fix is prompt-material wording, not code; pin the material's new text, and record in the task report that model compliance is verified live in Task 7, not claimed here.
- [ ] **Step 3: `pv.microstructure` description** corrected to what the worker actually does (zero dependents — Task-6 review fact — so behavior moves nowhere).
- [ ] **Step 4:** FULL suite red-diff against inventory again (the moved-material pins join the recorded red set; still ⊆ inventory). Focused tests green. Commit.

```bash
git add guanlan_v2/orchestration/bootstrap.py guanlan_v2/orchestration/memory/catalog.py config/orchestration/materials config/orchestration/presets/v2/luozi_deep_decide_v1.json tests/orchestration/test_l3_material_texts.py docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "fix(orchestration): sealed material texts state the post-C3 invariants - handler texts, regime prompt drivers/anchor, pv.microstructure description"
```

---

## Task 5: THE ONE-SHOT RE-FREEZE (§2.1 items 3 + 4) — **the window closes**

The only golden-moving operation ever authorized. One inventoried commit sequence; every moved digest has a written reason; every flipped pin has bidirectional evidence; the suite exits FULL GREEN.

**Files:**
- Modify: the 9 golden manifests + 7 handoff tests + 42 frozen literals per the Task-1 inventory (exact list in `2026-07-31-L3-refreeze-inventory.md` — the checklist IS the pathspec source)
- Modify: `tests/orchestration/test_pipeline_deep_preset.py`, `tests/orchestration/test_pipeline_reduced_deep_preset.py`, the Task-11 "EXACTLY {pv.technical, text.news}" pins (conscious flips), and `tests/orchestration/test_pv_aux_nodes.py` (seam-review finding 5: if D-1 granted the aux workers rows, its `aux_data_ungranted` pins flip to real-read semantics; if not, they are re-asserted against the post-refreeze catalog and stay — either way the file is in the flip audit, never incidentally green or red)
- Modify: `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` (the 对照表)

**Steps:**

- [ ] **Step 1: Regenerate the chain in order** — P3-data → P3-full → P4 → P5 → (P6 identity re-derived, not assumed) → P7 → P8 → P9 → P10 — each phase's base asserted == the prior phase's fresh result before regenerating the next (the chain's own discipline, now run forward once). For every one of the 11 digests, append a row to the 对照表: old value → new value → the exact Task-3/4 change that moved it. A digest that moved with NO attributable cause = STOP (something outside the charter moved).
- [ ] **Step 2: Verify the not-moving set byte-identical**: P2 `b41bf223`, the 7 `data_capabilities`, renderer/source handles, `planner_spec_digest`, all 10 schema manifests, and (per the D-1/D-2 outcome) whichever of the four data-bridge material digests the rulings left untouched. Assert with a script, record output in the ledger. If a schema manifest moved, a registered model was touched — STOP, that is outside every ruling.
- [ ] **Step 3: Conscious pin flips, one commit, both directions evidenced.** For each pin in the inventory's flip list: capture its red output at the pre-flip tree (钉住缺口 proven still failing), flip it to pin SUPPORT (`support_report.supported is True` for the ten-node preset; the honestly-unsupported set is now EMPTY — pin that exactly, not "no longer contains"), capture green. The reduced preset's raison-d'être tests are REWRITTEN, not deleted: reduced is now the **cheap tier/fallback** — its badges (`reduced_evidence_preset_v1`, `reduced_evidence_missing:*`), its eight-worker set, and its refusal to impersonate the full preset all stay pinned; only the "because the full lane is support-refused" justification text/assertions flip to "as the cheap tier beside a supported full lane". `GUANLAN_SEATS_DEEP_PRESET=reduced` keeps selecting it; default (unset) selects the full ten-node preset.
- [ ] **Step 4: D-2 consequence pinned executable** (per Task 1 Step 4 Option A, if ruled): a simulated data outage on `pv.technical`/`text.news` (world adapter raising) → aux node hard-fails LOUDLY **with the post-L2-b failure reason — the delegated session's gateway/adapter failure; assert the typed reason observed AND assert `bridge_execution_error` ABSENT (L2-b retired that vocabulary on this path)** — degrade badge present, trunk completes, run COMPLETED — in the full deep preset AND the screening lane. Mutate the degrade policy → red.
- [ ] **Step 5:** `pytest tests/orchestration` FOREGROUND, full run to completion (~15 min; run in halves if the tool cap requires, per campaign method) — **ZERO failures, zero unexplained xfails**; `compileall` clean; `pytest tests/` (rest of tree) green modulo the pre-existing concurrent-session capability-manifest reds (record count, prove not-yours by the ff26c23 isolation method if they appear).
- [ ] **Step 6: Commit sequence** (regeneration → flips → ledger), explicit pathspecs enumerated from the inventory file. Window CLOSED. Any later golden movement anywhere is a regression.

```bash
# pathspecs enumerated from docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md - no wildcards over shared directories
git add tests/orchestration/golden/<each of the 9 manifests> tests/orchestration/<each of the 7 handoff tests> <literal-bearing test files from the inventory>
git commit -m "chore(orchestration): the one-shot re-freeze - P3-data..P10 chain regenerated, 11 digests moved with written reasons, window CLOSED"
git add tests/orchestration/test_pipeline_deep_preset.py tests/orchestration/test_pipeline_reduced_deep_preset.py <Task-11 pin files>
git commit -m "test(orchestration): conscious flips - full deep preset pinned SUPPORTED, reduced preset re-chartered as the cheap tier"
git add docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "docs(orchestration): re-freeze 对照表 - every moved digest, its reason, and the not-moved proof"
```

**Mandatory independent review checkpoint** after this task (spec §3.1 "带完整再审"): a reviewer re-derives at least the P3-data and P10 endpoints of the chain independently, audits the 对照表 row-by-row, and audits every flipped pin's bidirectional evidence. Ready-to-proceed verdict required before Task 6.

---

## Task 6: Production robustness ride-alongs (§2.2 — explicit include/defer decisions)

The spec's §2.2 items are "真跑之前的生产健壮性". The split below is **CONFIRMED by controller pre-ruling 「控制器已裁」** (item 7 included as the live-run precondition; item 8 deferred; item 9 measured by BOTH plans — L2-b's live task measures the eight-node preset, this plan's Task 7 the ten-node — with the user deciding on the numbers):

- **INCLUDED — item 7, durable deep-failure receipt (both sides).** Without it, Task 7's live campaign risks the known storm: persistent deep failure → re-escalation every tick until the shared budget exhausts, AND per-tick re-`note_llm_use` drains the 24/day watcher pool, starving the FAST lane at the tick gate (final-review加严项). This is a live-run precondition, so it rides this plan.
- **DEFERRED — item 8, approval-journal / `state_cells.jsonl` compaction.** Pure O(growth) performance, zero golden coupling, zero live-run blocker at today's journal sizes (measured: the replay-skip WARNING volume is O(journal) per construction — annoying, not wrong). Lands any time post-L3; recorded on the backlog with the measurement.
- **DEFERRED + SURFACE TO USER — item 9, provider-seam serialization (LLM throughput ceiling).** One lock/one loop ⇒ LLM nodes execute one-at-a-time; the full ten-node preset's 8 LLM seats will feel it first. Not a correctness item; unblocking it touches the executor seam (not L3's fence). Task 7's report must MEASURE the serialization on the real run (wall-clock per seat) so the user decides with numbers (L2-b's live task records the same datum on the eight-node preset — two data points, per the ruling).

**Files:**
- Modify: `guanlan_v2/orchestration/pipeline/live_decide.py` (failure receipt beside the existing completed-run receipt dedup)
- Test: `tests/orchestration/test_pipeline_live_decide.py` (extend)

**Steps:**

- [ ] **Step 1 (RED):** pin: a deep run failing after admission writes a **durable failure receipt** keyed by the run identity family (code, session date, trigger); a subsequent tick with the same identity does NOT re-escalate (fast result + `deep_outcome` naming the prior failure + receipt reference) and does NOT re-charge `note_llm_use`; a FRESH identity (new day / new code / explicit operator retry) escalates normally. RED against current behavior (per-tick re-escalation).
- [ ] **Step 2:** implement; the receipt is honest (records the true failure reason, never converts failure to silence — the fast lane still runs and the deep outcome stays visible). Mutate→red→revert: drop the receipt write → the no-re-escalation pin red; drop the pool-side guard → the single-charge pin red.
- [ ] **Step 3:** `GUANLAN_SEATS_DEEP` unset bit-unchanged regression re-run (the P10 pin suite for the watcher, byte-untouched); full `tests/orchestration` green.
- [ ] **Step 4: Commit.**

```bash
git add guanlan_v2/orchestration/pipeline/live_decide.py tests/orchestration/test_pipeline_live_decide.py
git commit -m "feat(orchestration): durable deep-failure receipt - no per-tick re-escalation, no double pool charge, fresh identities unaffected"
```

---

## Task 7: LIVE VERIFICATION — the full ten-node preset runs a real deep chain end-to-end

The campaign's law: 13 defects were invisible to a 5600-green tree. This plan is not done when the suite is green; it is done when the FULL preset has produced a real decision with real technical/news evidence on the production assembly, and the ledger says what that proved.

**Files:**
- Modify: `.superpowers/sdd/progress-orchestration.md` (campaign ledger entry)
- Modify: `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` (live-evidence section)
- (No production source files — any defect found live is fixed via a dispatched fix task with its own TDD cycle and pathspec, campaign style, never a drive-by.)

**Steps:**

- [ ] **Step 1: Serve the new tree.** Verify on **9998** first (script launch, store_status=bound, 14 namespaces, launcher_status); then restart **9999** (watchdog generation rotation; confirm the log's bind port — watchdog-9999 坑⑤). The run must load this plan's commits, not stale code (the campaign's first live contradiction was exactly a not-yet-committed fix).
- [ ] **Step 2: Support check live:** the ten-node draft's `support_report.supported is True` on the PRODUCTION catalog runtime (not a test fixture) — the exit-gate fact, observed on the serving process.
- [ ] **Step 3: Lane 0 first** (it is the `ContextSnapshot`'s only producer): today's session via the `lane0_driver` CLI (propose/approve/run; `--attempt N` for identity burn; one session identity per day). Confirm the committed snapshot + the regime prompt's Task-4 wording effect (drivers sorted? anchor conditional?) — record honestly if the model still misbehaves (prompt fixed ≠ compliance proven).
- [ ] **Step 4: The real run.** A code with `deep_research` opt-in (temporary strategy opt-in idiom: take a fresh identity, run, delete), `GUANLAN_SEATS_DEEP=1`, **`GUANLAN_SEATS_DEEP_PRESET` UNSET** (default = full ten-node). Expected shape: **ten nodes COMPLETED, 8 LLM seats** (reduced's 6 + `pv.technical` reasoner + `text.news` fast), and — the whole point — **`pv.technical` and `text.news` seats REAL**: ToolCallRecords with ≥1 finalized call each (the D-2 arithmetic live), evidence blocks carrying real indicator/news bytes from the L2-b world, untrusted-input isolation intact, citation chain closed (research-mgr quotes the aux evidence verbatim or abstains honestly — the 000858 standard). Ledger row `source="orchestrated"`, full badge set, zero fabrication.
- [ ] **Step 5: The reduced preset re-verified as the fallback tier:** one run with `GUANLAN_SEATS_DEEP_PRESET=reduced` on a second code — still COMPLETED, still badged `reduced_evidence_preset_v1` — proving the cheap tier survived its re-chartering.
- [ ] **Step 6: The A-line live (「控制器已裁」— screening live verification belongs to THIS plan's exit gates, moved in from L2-b):** one real screening run on the production assembly whose scheduled `text.news` seat performs a genuine data read — `ToolCallRecord` with ≥1 finalized call, real news bytes in the evidence block, untrusted-input isolation intact, the screening product committed. Record what it proved / did not.
- [ ] **Step 7: Negative controls, live:** (a) `GUANLAN_SEATS_DEEP` unset → watcher behavior bit-unchanged (fast lane only, zero orchestration writes for the tick); (b) the Task-6 failure receipt: if any deep failure occurs during the campaign (likely — that is why we run), confirm no re-escalation storm and no double pool charge on the next tick; do not manufacture a failure in production if none occurs — the suite pin covers the mechanism, the live check is opportunistic.
- [ ] **Step 8: Measure and record** the provider-seam serialization (wall-clock per LLM seat, total run time) for the user's §2.2-item-9 decision.
- [ ] **Step 9: Ledger write-up** — what this run PROVED (support green in production; the two granted seats read real data; trunk + aux + degrade + budget arithmetic live) and what it DID NOT prove (model-side regime compliance if still off; outage semantics if no outage occurred). The screening lane is covered by Step 6's run — record its proof separately, never implied by the deep run; if Step 6 was blocked, this plan does not close (the A-line gate is an exit gate). Tokens: expect a comparable spend to the campaign (~10-25 LLM calls including retries); authorized.
- [ ] **Step 10: Commit** the ledger entries (docs pathspec only).

```bash
git add docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md
git commit -m "docs(orchestration): L3 live verification - full ten-node preset COMPLETED with real technical+news evidence; what it proved and did not"
```

---

## Exit Gates (spec §4 verbatim; owner noted per gate)

- [ ] `pv.technical` / `text.news` 的授权与预取行审下并一对一;`build_phase3_catalog` 通过; **(L3 Task 3)**
- [ ] `dec.research_mgr` 的经验行诚实成立(冲突裁决落文档); **(L3 Task 2 — satisfied by the recorded ruling: Option A, 「控制器已裁」rowless-EMPTY is the reviewed end state this phase; the real-experience-row derivation stays on the post-L3 ledger)**
- [ ] 深链 preset 的 `support_report.supported is True`,承载缺口的 strict-xfail **已有意识翻面**; **(L3 Tasks 3+5)**
- [ ] 生产深链一次真跑产出真决策产物(非测试夹具),且台账写明证明了什么; **(L3 Task 7 — full ten-node; the reduced-preset run of 07-31 already proved the trunk, this gate demands the FULL preset)**
- [ ] A 线(选股 screening)真跑验证:一次真实 screening 运行中其 `text.news` 座位完成真实数据读取; **(L3 Task 7 Step 6 — 「控制器已裁」自 L2-b 移入本计划的 exit gates)**
- [ ] 位移的 golden 全部逐条再审并附对照表;此后套件全绿; **(L3 Task 5 + its review checkpoint)**
- [ ] 密封 material 文本与裁决后行为一致(不再靠注释记录漂移); **(L3 Task 4)**
- [ ] rowless 经验 worker 在准入期即被拒(不再晚失败); **(L3 Task 2)**
- [ ] 深链失败有耐久回执,且 24/日池子侧不会饿死快线; **(L3 Task 6)**
- [ ] `GUANLAN_SEATS_DEEP` 未设时 watcher 行为仍逐位不变(P10 回归重跑)。 **(L3 Tasks 6+7)**

Gates owned upstream, restated so no one marks them here by mistake: the subject→data projection (L1 / ruling D-0) and the production `DataRuntimeWorld` + provider binding (L2-b) are **preconditions verified by Task 1**, not deliverables of this plan.

## Execution Handoff

Implement in task order. **Nothing starts before the L1 and L2-b plans have landed and Task 1's gate passes against the real tree** — if either has not landed, the correct action is to STOP and say so, not to fixture around it. Mandatory checkpoints:

1. after Task 1 — the controller answers **D-1 and D-2** (the two rulings this plan surfaces but must not make); the §2.2 include/defer split is already pre-ruled 「控制器已裁」(Task 6) — re-open it only if D-1/D-2 change the live-run shape;
2. after Task 2 — the item-2 experience ruling is recorded (Option A, 「控制器已裁」— recording only, no round-trip);
3. after Tasks 3–4 — red-window audit: recorded red set ⊆ inventory, zero unexplained reds;
4. after Task 5 — the **independent re-freeze review** (chain re-derivation, 对照表 row-by-row, flip evidence both directions) with an explicit ready-to-proceed verdict;
5. after Task 7 — the campaign-ledger write-up; the controller decides merge/push (user drives integration; the branch carries concurrent sessions' work — merge mechanics per the Task-10 worktree/CAS precedent).

Known collisions to carry: the BJ-920 xfail in `test_pipeline_candidates.py` flips when `great-meitner` (`296bd02`) merges — unrelated to this plan but it shares `tests/orchestration/`, so Task 5's "zero unexplained reds" audit must recognize it if the merge lands mid-plan. The capability-manifest pair stays red in a clean checkout until the 2026-07-16 datafeed session commits its `console/tools.py` — not ours, prove by isolation if it appears.
