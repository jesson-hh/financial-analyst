# Orchestration Phase 8 · 四车道目录 / skills / Lane D 有界辩论 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the skills-tree infrastructure, each migration batch, the runtime-profile-v2 task, the debate runtime and the final chain/e2e tasks. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Complete the final-24 worker catalog by migrating the remaining redesigned workers in explicit reviewed batches (Lane C → Lane B → Lane A → 跨切 → Lane D last), each batch shipping `WorkerSpec` + `SKILL.md` + guardrail + capability manifest + legacy-schema adapter regression; establish `guanlan_v2/orchestration/skills/` as the single skill source tree with `sync-skills.py` mirroring and `check.py` drift-lint; ship the honesty spine `honesty.py` (`classify_worker` incomplete determination, unsourced-number scan, badge logic) wired to the two 跨切 workers; build the Lane D bounded-debate runtime (immutable `DebateMessage` events, deterministic Plan-order reducer, one `dec.risk_debate` spec instantiated as multiple PlanNodes, ≤2 rounds, per-seat per-round invocation budget, `dec.pm` the only `reasoner_deep`); unlock debates/reducers/multi-writer slots/gate metrics/`max_attempts>1`+bounded schema repair via a new `StaticRuntimeProfile` version; define the `ModelTier`→`llm.yaml` bridge as catalog-owned config material; and seal the cumulative `PHASE8_REGISTRY_DIGEST`/`PHASE8_CATALOG_DIGEST` chain with its own goldens. (spec §3/§3.6, §8 debate validator rules + `DebateMessage`, §12.8, §13 typed 迁移批次)

**Architecture:** Phase 1 remains sole owner of `WorkerSpec`/catalog snapshot/skill-v1 grammar (`parse_skill_v1`, `SkillManifest`, catalog.py:504/156), Plan validation/freeze, `DebateCfg`/PlanNode debate fields and their validators (spec.py:301-406, 599-625) and the migration adapters (migration.py). Phase 2 remains owner of the runtime kernel; Phase 8 extends `runtime_support.py`/`dag.py`/`worker.py`/`pool.py`/`events.py` **additively** to execute what profile v2 admits — the Phase 2 profile v1 constant, golden and rejection behavior for v1-bound Plans are untouched. Phase 8 authors 18 new final WorkerSpecs, applies reviewed updates (not re-creation) to the 3 Phase 2 pilots (`text.sentiment`/`dec.research_mgr`/`dec.pm`), and inherits the 3 Lane 0 workers from Phase 5 — reconciling the final-24 count. `dec.trader` only emits `PortfolioTargetProposal` (Phase 6 owns that schema and the runtime intent envelope). Workers submit skill-change proposals only; no capability, handler or script can write the skills tree.

**Tech Stack:** Python ≥3.11, Pydantic v2, `asyncio`, `pytest` + `pytest-asyncio`. All modules `from __future__ import annotations`. Depends on implemented Phase 1 (+Amendment 1) contracts and the Phase 2–7 plan ABIs in `guanlan_v2/orchestration/`.

## Global Constraints

These extend, and never override, the Phase 1–7 Global Constraints and Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import Phase 1 models/builders (`WorkerSpec`, `ExecutionSpec`, `EvidencePolicy`, `SkillBinding`, `SkillManifest`, `parse_skill_v1`, `catalog_material_digest`, `build_catalog_snapshot`, `DebateCfg`, `PlanNode`, `ReducerCfg`, `GateCfg`, `GateResult`, `NumberAnchor`, `Artifact`, `NodeRun`, migration adapters), Phase 2 runtime services, Phase 3 data/memory ABI, Phase 6 `PortfolioTargetProposal` and Phase 7 planner surfaces from their owning modules. Never redefine canonical JSON, digests, candidate/plan digest, Plan validation/freeze, event semantics or the skill-v1 grammar.
- **Typed evidence pairs are `TypedPayloadRef(schema_ref, payload_ref)`** (Phase 1 Amendment 1); plain `PayloadRef` only for bare storage locators. Evidence tuples use the amendment's `validate_typed_ref_tuple` ordering rules.
- **Batch order is frozen:** Lane C → Lane B → Lane A → 跨切 → Lane D last. Each batch = WorkerSpec + SKILL.md + guardrail + capability manifest + legacy-schema adapter regression (reversible, per Phase 1 migration.py precedent). Frozen UNMAPPABLE verdicts (rating [-10,10] no 5-band binning; introspector `"med"`; ALL legacy sentiment scales; rotation stage) are **not** re-mapped here.
- **Lane literal for 跨切 is `"xcut"`** (`Lane` Literal, catalog.py:109); the Task-0 worker map's `cross` string is documentation only and must not appear in any WorkerSpec.
- **Skill single source.** All SKILL.md files live under `guanlan_v2/orchestration/skills/<worker_id>/SKILL.md`; `config/orchestration/materials/skills/` is a byte-identical derived mirror produced only by `sync-skills.py`; `check.py` drift-lint fails on any divergence. Trigger lines are Task-1 canonical JSON byte-for-byte (catalog.py:496-497); files are UTF-8 no-BOM (catalog.py:478-479); description is exactly 3 lines under a literal block scalar (catalog.py:543-559); first body heading is `## ⚠️ CRITICAL: Data Source Priority` (catalog.py:570-572).
- **WorkerSpec matrix discipline** (catalog.py:300-316, 370-453): LLM ⇒ `system_prompt_ref` + `model_tier` (no handler_ref); DETERMINISTIC ⇒ `handler_ref` (no tier/thinking budget); FORBIDDEN⇔empty allowlist, REQUIRED⇔non-empty; `can_emit_decision`⇔`advisory_only`; exactly one output named `primary`; final⇔`dynamic_allowed`⇔no `compat.` prefix. `dec.pm` is the **only** `reasoner_deep` worker (spec §0 决定 2).
- **Debate discipline** (spec §3.4 line 166, §8 lines 942-950): debate messages are immutable `(debate_id, round, turn, role)` events; the reducer folds by Plan order, never thread completion; ≤2 rounds for both the bull/bear and the three-seat risk debate; every seat×round is one LLM invocation reservation; budget is validated against the fully expanded invocation count; `DebateCfg.judge_node_id` must be a plan node.
- **Runtime profile v2 is a new `StaticRuntimeProfile` version.** Phase 2 v1 golden and v1-bound Plan behavior untouched; Phase 5's BOOTSTRAP profile version untouched. v2 unlocks exactly: debates, deterministic reducers/multi-writer slots, gate metrics, `max_attempts > 1` (cap 2) and bounded schema repair (≤1 per attempt). Conditions and stop conditions remain rejected before reservation.
- **Model tier bridge is catalog-owned config material.** No orchestration code calls `find_config`/`LLMClient` config discovery implicitly; the tier map is built from explicitly supplied `config/llm.yaml` bytes. A tier without a configured provider/model raises — never a silent default (红线: 绝不静默回落到未配置 vendor). Any test spawning the engine sets `FA_CONFIG_DIR=G:\guanlan-v2\config` (pinned `G:\financial-analyst\config\llm.yaml` has empty `agent_overrides` and would silently shadow bare invocations).
- **Red lines (spec §0):** draft-only + human review for adoption; LLM zero trading — `ADVISORY_ONLY`+`SHADOW_ONLY` structural (`dec.trader` emits only `PortfolioTargetProposal`; no worker capability can write orders/signals/`TargetPortfolioIntent`); PIT `available_at <= as_of` with `FutureDataRefused` never falling through; workers propose-never-write memory/skill/code; degradation always badged; unsourced load-bearing numbers ⇒ `[UNSOURCED]`.
- **Registry/catalog chain per CRIB 4.5:** `PHASE8_REGISTRY_DIGEST` + `build_phase8_registry(expected_phase7_digest)`; `PHASE8_CATALOG_DIGEST` + `build_phase8_catalog_snapshot(...)`; own goldens `tests/orchestration/golden/phase8_schema_manifest_v1.json` / `phase8_catalog_manifest_v1.json`; inherited schemas byte-identical; upstream goldens never regenerated; no "latest" alias. New EventType members are pure additions with absence-guard flips per the Phase 4 mechanism.
- **Executable red/green checkpoints.** Every "Write failing … tests" step runs the focused command shown and records the missing-contract/behavior failure before implementation; collection errors do not count. The PASS step reruns the same command plus listed regressions.
- **Git hygiene:** shared branch with concurrent sessions — `git status --short` first; explicit pathspec commits only; never `git add -A`. No placeholders, DRY, YAGNI, TDD. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## Task 0: Phase 2–7 handoff gate (mandatory before Task 1)

Phase 8 work starts only after the Phase 7 Exit Gates pass (and transitively Phases 2–6). Add `tests/orchestration/test_phase8_handoff.py` as an executable consumer test.

> Line-ref note: all `phase1-implemented-contracts` line references in this plan are pre-Amendment-1 coordinates (e.g. `BudgetScopeType` is now ~context.py:90); guard flips and citations are name-authoritative, never line-authoritative.

**Files:**
- Create: `tests/orchestration/test_phase8_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. the Phase 7 cumulative registry/catalog chain resolves: `PHASE7_REGISTRY_DIGEST`/`build_phase7_registry(...)` and `PHASE7_CATALOG_DIGEST`/`build_phase7_catalog_snapshot(...)` exist, their goldens pass, and the chain verifies back through Phase 6/5/4 to `PHASE3_FULL_REGISTRY_DIGEST`/`PHASE3_FULL_CATALOG_DIGEST`;
2. the Phase 7 catalog already contains as `catalog_role="final"`: the 3 Phase 2 pilots (`text.sentiment`, `dec.research_mgr`, `dec.pm`) and the 3 Phase 5 Lane 0 workers (`market.factor`, `market.regime`, `market.rotation`) — record their exact WorkerSpec semantic digests as the pre-update baseline;
3. Phase 1 debate contracts are intact and unchanged: `DebateCfg` (spec.py:384) with seats/turn_order-permutation/max_rounds/judge validators, PlanNode debate fields all-set-or-all-none (spec.py:333-347), plan-level `debate_missing_judge`/`undefined_debate`/`debate_role_not_seat`/`debate_round_out_of_range`/`duplicate_debate_turn`/`duplicate_debate_role` issue codes (spec.py:599-625);
4. skill-v1 grammar is intact: `parse_skill_v1`, `SkillManifest`, `SkillFormatError`, `_CRITICAL_HEADING_LINE == "## ⚠️ CRITICAL: Data Source Priority"` and canonical-JSON trigger enforcement all resolve from `catalog.py`;
5. Phase 6 exports `PortfolioTargetProposal` and `TargetPortfolioIntent` as registered schemas, and the runtime intent envelope builder is service-owned (no public constructor a worker payload could satisfy);
6. Phase 1 migration adapters and their frozen verdicts hold against `tests/orchestration/fixtures/legacy_contract_samples.json` (14 scalars, 24 planned workers, 1 graph): `migrate_rating` UNMAPPABLE, `migrate_confidence("med")` UNMAPPABLE, all sentiment sources UNMAPPABLE, `migrate_rotation_stage` per its Phase 5 status, and every adapter's reverse returns the exact original raw;
7. `BudgetScopeType` already contains `"schema_repair"` and `"retry"` (context.py:76-78) and `TextMaterialKind` already contains `"reducer"` and `"gate_metric"` (catalog.py:97-116) — Phase 8 needs no vocabulary change there;
8. the Phase 2 `StaticRuntimeProfile` v1 golden and the Phase 5 BOOTSTRAP profile resolve; BOOTSTRAP is `profile_id="bootstrap-runtime"` / version `"1"` — a `profile_id` distinct from `static-runtime`, so `"2"` is free for this plan (clause (f)); record both identity pairs;
9. no Phase 8 source/test path overwrites Phase 1–7 owned modules' goldens or frozen tests.

**Task 0 correction clauses** (binding on every later task; do not invent parallel semantics):

- **(a) Upstream chain symbols.** If the implemented Phase 4–7 registry/catalog chain exports differ in exact name or signature from the CRIB 4.5 recursion (`PHASE<N>_REGISTRY_DIGEST`, `build_phase<N>_registry(expected_phase<N-1>_digest)`, `PHASE<N>_CATALOG_DIGEST`, `build_phase<N>_catalog_snapshot(...)`), update this plan to the reviewed implemented API before writing Phase 8 code.
- **(b) EventType guards.** Phases 4/6 will already have flipped the Phase 1 absence guards (`tests/orchestration/test_events.py` exact-equality set, `tests/orchestration/test_contract_completeness.py` prefix sweep). Task 9 extends the **then-current reviewed expected set** additively; it never restores or regenerates an older set.
- **(c) Phase 3 data capability ids.** Capability-allowlist entries below name Phase 3 data capabilities by plan-ABI intent (`get_news`, `get_ohlcv`, `get_indicators`, `get_verified_snapshot`, `get_fundamentals`, `get_signal`). Bind each to the exact implemented Phase 3 `CapabilityRef` id/version at implementation time; if an id differs, update the batch table, not the semantics.
- **(d) Pilot WorkerSpec baselines.** The three pilots' implemented field values (e.g. `text.sentiment.model_tier`, its FORBIDDEN tool policy) are the baseline; Task 4/10 reviewed updates change only the fields each task names. If a pilot's implemented value already satisfies the target, the update is a no-op recorded in the task's test.
- **(e) Lane 0 skill materials.** If Phase 5 placed the Lane 0 SKILL.md sources outside `guanlan_v2/orchestration/skills/`, Task 1 relocates them into the tree with byte-identical content (digest-preserving, reviewed); if Phase 5 already used the tree, relocation is a no-op.
- **(f) Profile version strings — resolved, not an open collision.** Phase 5 mints a distinct profile identity: `profile_id="bootstrap-runtime"`, version `"1"`; this plan keeps `profile_id="static-runtime"`, `profile_version="2"` (`STATIC_RUNTIME_PROFILE_V2`). Task 0 asserts `bootstrap-runtime` is a `profile_id` distinct from `static-runtime`; v1 and BOOTSTRAP constants are never edited.
- **(g) llm.yaml values.** The initial tier→provider/model values in Task 3 come from the repo `config/llm.yaml` at implementation time; if its providers changed, record the reviewed current values — never hardcode a vendor that is absent from the file.
- **(h) Legacy pure functions.** Where a deterministic handler below names an existing function (e.g. `compute_pa_features`), verify it is import-safe and side-effect-free at implementation time; if it proves impure (network/global state), the handler instead consumes that computation's typed output as a declared input artifact/data result, and this plan is updated to the reviewed surface.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the test**

Record only exact digests and exported symbol signatures (Phase 7 registry/catalog digests, pilot + Lane 0 WorkerSpec semantic digests, profile version strings, fixture config digest); never local paths or mutable singleton identities.

- [ ] **Step 3: Run the full orchestration suite plus the gate**

Run: `pytest tests/orchestration -v`

Expected: every Phase 1–7 test plus `test_phase8_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase8_handoff.py
git commit -m "test(orchestration): gate phase8 on phase2-7 contracts"
```

---

## File Structure (created/modified in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/skilltree.py` | importable pure core: load/validate skill source tree, canonical trigger rendering, mirror planning/apply, drift-lint |
| `guanlan_v2/orchestration/skills/<worker_id>/SKILL.md` | single-source skill files (21 authored/relocated here; 3 Lane 0 relocations per clause (e)) |
| `guanlan_v2/orchestration/skills/sync-skills.py` | thin CLI: mirror source tree → `config/orchestration/materials/skills/` |
| `guanlan_v2/orchestration/skills/check.py` | thin CLI drift-lint: byte-identity + grammar + catalog digest cross-check; nonzero exit on drift |
| `guanlan_v2/orchestration/honesty.py` | `HonestyIssue`/`HonestyReport@1`, `classify_worker`, `scan_unsourced_numbers`, badge logic, attribution hook surface (spec §6.3) |
| `guanlan_v2/orchestration/model_tiers.py` | `ModelTierBinding`/`ModelTierMap@1`, `tier_map_from_llm_yaml`, `resolve_model_binding`; catalog guardrail material |
| `guanlan_v2/orchestration/lane_payloads.py` | 16 new registered lane output payload schemas + nested value objects |
| `guanlan_v2/orchestration/lane_catalog.py` | 18 new + 5 updated WorkerSpec builders, batch material manifests, `PHASE8_*` registry/catalog chain exports |
| `guanlan_v2/orchestration/debate.py` | `DebateMessage@1`, `DebateTranscript@1`, expansion verifier, deterministic fold reducer, invocation counting |
| Modify (additive): `guanlan_v2/orchestration/runtime_support.py` | `STATIC_RUNTIME_PROFILE_V2` + v2 support analyzers (debate/reducer/gate/retry-repair) |
| Modify (additive): `guanlan_v2/orchestration/events.py` | `DEBATE_MESSAGE_PUBLISHED` EventType member + visibility rule |
| Modify (additive): `guanlan_v2/orchestration/dag.py`, `pool.py`, `worker.py` | reducer execution at barrier, debate event emission, `max_attempts>1` retry loop, bounded schema repair |
| Modify (additive): `config/llm.yaml` | three `agent_overrides` alias keys `orchestration-fast/-reasoner/-reasoner-deep` |
| `config/orchestration/materials/` | mirrored skills + new prompt/guardrail/handler/reducer/gate-metric/tier-map physical materials |
| `tests/orchestration/golden/phase8_schema_manifest_v1.json` | Phase 8 cumulative registry golden |
| `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | Phase 8 cumulative catalog golden |
| `tests/orchestration/test_phase8_handoff.py` + per-task tests listed in each task | executable gates |

---
## Task 1: Skills single-source tree + sync-skills.py + check.py drift-lint

**Files:**
- Create: `guanlan_v2/orchestration/skilltree.py`
- Create: `guanlan_v2/orchestration/skills/sync-skills.py`, `guanlan_v2/orchestration/skills/check.py`
- Create: `guanlan_v2/orchestration/skills/text.sentiment/SKILL.md`, `.../dec.research_mgr/SKILL.md`, `.../dec.pm/SKILL.md` (pilot relocations, byte-identical to their Phase 2 material bytes) and Lane 0 relocations per clause (e)
- Test: `tests/orchestration/test_skilltree.py`

**Consumes:** Phase 1 `parse_skill_v1`, `SkillFormatError`, `SkillManifest`, `catalog_material_digest`, `canonical_json` (Task-1 canonical JSON for trigger lines); Phase 2 material root `config/orchestration/materials/`.

**Produces (exact signatures):**

- `class SkillSourceFile(NamedTuple): skill_id: str; source_identity: str; path_label: str; text: str` — `skill_id` is the owning worker id; `source_identity = "skill." + skill_id` (LogicalId).
- `def render_trigger_line(prefix: Literal["Perfect for: ", "Not ideal for: "], items: tuple[str, ...]) -> str` — renders via Phase 1 `canonical_json` so authors never hand-write trigger JSON; round-trips through `parse_skill_v1` by construction.
- `def load_skill_tree(root: Path) -> tuple[SkillSourceFile, ...]` — deterministic order by `skill_id`; rejects: BOM, non-UTF-8, duplicate skill ids, any file failing `parse_skill_v1`, any directory without exactly one `SKILL.md`.
- `class MirrorAction(NamedTuple): skill_id: str; target_label: str; op: Literal["create", "update", "noop"]; source_digest: DigestHex` and `def plan_mirror(tree: tuple[SkillSourceFile, ...], *, materials_root: Path) -> tuple[MirrorAction, ...]` / `def apply_mirror(actions: tuple[MirrorAction, ...], *, tree: tuple[SkillSourceFile, ...], materials_root: Path) -> None` — mirror target is `config/orchestration/materials/skills/<skill_id>.md`, bytes copied verbatim.
- `class DriftIssue(NamedTuple): code: Literal["missing_mirror", "orphan_mirror", "byte_drift", "grammar_error", "manifest_digest_mismatch"]; skill_id: str; detail: str` and `def lint_drift(tree: tuple[SkillSourceFile, ...], *, materials_root: Path, catalog: WorkerCatalogSnapshot | None = None) -> tuple[DriftIssue, ...]` — with a catalog supplied, every `SkillManifest` entry's `ref.content_digest` must equal the source file's `catalog_material_digest`, and every tree skill bound by any final worker must appear in the catalog `skill_manifest`.
- `sync-skills.py` — argv: `--root`, `--materials-root`, `--dry-run`; prints the MirrorAction list; writes only outside dry-run. `check.py` — argv: `--root`, `--materials-root`, optional `--catalog-golden tests/orchestration/golden/phase8_catalog_manifest_v1.json`; exit 0 iff `lint_drift` returns `()`. Both are thin wrappers importing `skilltree` (hyphenated filename runnable via `runpy`/subprocess, never imported as a module).

**Required invariants:**

1. the tree is the only human-edited location; the mirror is derived and byte-identical; `check.py` fails on any hand edit of the mirror;
2. trigger lines survive `parse_skill_v1` byte-for-byte (Task-1 canonical JSON; pretty-printed or re-ordered arrays are rejected at load, not at catalog build);
3. pilot/Lane 0 relocation is digest-preserving: relocated file bytes equal the Phase 2/5 material bytes, so their existing `ContentRef.content_digest`s are unchanged;
4. no orchestration capability, handler or worker-facing API can write under `guanlan_v2/orchestration/skills/` — skill changes are proposals through the existing Phase 3 proposal boundary + human git review; the plan adds a structural test asserting no catalog capability whose `operation` contains `skill`;
5. every run records skill digests via Phase 1 `Provenance.skill_refs` (no new mechanism).

**Normative SKILL.md skeleton** (every Phase 8 skill instantiates exactly this shape; shown for `text.news` — the two trigger arrays below are produced by `render_trigger_line`, byte-exact Task-1 canonical JSON, single line each):

```markdown
---
name: text.news
description: |
  A股快讯与全球要闻的证据化速读 playbook:何时读、读哪路源、如何交叉核对时间戳。
  Perfect for: ["盘中快讯速读","全球市场隔夜要闻","个股公告初筛"]
  Not ideal for: ["情绪打分","研报深读","政策解读"]
---

## ⚠️ CRITICAL: Data Source Priority

- 1. 经 runtime 预取的 `get_news` DataResult(带 `available_at`/PIT audit)是唯一事实源;绝不引用未入池文本。
- 2. 同一事件多源冲突时,以 `available_at` 最早且 vendor 链非降级者为准;降级源必须在 `coverage_note` 显形。
- 3. 缺源/空窗:诚实输出空 `items` + `coverage_note`,禁止补写记忆中的"常识新闻"。

## Playbook

(清单式方法论正文,按 CoALA 程序记忆写法;每条含触发条件与产出要求……)
```

Frontmatter is exactly `name` + `description` (literal block scalar `|`, exactly 3 description lines); the critical heading is the first body heading; later headings are free-form playbook content. `check.py` re-parses every file with Phase 1 `parse_skill_v1` — any deviation is a `grammar_error` DriftIssue.

- [ ] **Step 1: Write failing skilltree tests**

Cover: valid tree load ordering; BOM/dup/4th-description-line/folded-scalar/missing-critical-heading rejection routed to `SkillFormatError`/load errors; `render_trigger_line` round-trip; mirror create/update/noop; each `DriftIssue` code; relocation digest preservation against the recorded Phase 2 pilot digests (from Task 0 evidence); the no-skill-write structural test.

Run: `pytest tests/orchestration/test_skilltree.py -v`

Expected: FAIL on missing `skilltree` module/behaviors.

- [ ] **Step 2: Implement `skilltree.py`, the two CLIs and the relocations; run `sync-skills.py` once to seed the mirror**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_skilltree.py tests/orchestration/test_phase8_handoff.py -v`

Expected: PASS.

```bash
git add guanlan_v2/orchestration/skilltree.py "guanlan_v2/orchestration/skills/sync-skills.py" guanlan_v2/orchestration/skills/check.py \
  guanlan_v2/orchestration/skills/text.sentiment/SKILL.md guanlan_v2/orchestration/skills/dec.research_mgr/SKILL.md guanlan_v2/orchestration/skills/dec.pm/SKILL.md \
  config/orchestration/materials/skills/text.sentiment.md config/orchestration/materials/skills/dec.research_mgr.md config/orchestration/materials/skills/dec.pm.md \
  tests/orchestration/test_skilltree.py
# Only if clause (e) relocation applies (Phase 5 skills were outside the tree), additionally:
# git add guanlan_v2/orchestration/skills/market.factor/SKILL.md guanlan_v2/orchestration/skills/market.regime/SKILL.md guanlan_v2/orchestration/skills/market.rotation/SKILL.md \
#   config/orchestration/materials/skills/market.factor.md config/orchestration/materials/skills/market.regime.md config/orchestration/materials/skills/market.rotation.md
git commit -m "feat(orchestration): skills single-source tree with sync mirror and drift lint"
```

---

## Task 2: Honesty spine `honesty.py`

**Files:**
- Create: `guanlan_v2/orchestration/honesty.py`
- Test: `tests/orchestration/test_honesty.py`

**Consumes:** Phase 1 `WorkerSpec`, `EvidencePolicy`, `ToolCallRequirement`, `NodeRun` (amended: `tool_call_records` tuple), `Artifact`, `NumberAnchor`, `NodeStatus`, `DigestModel` strict types.

**Produces (exact signatures):**

- `UNSOURCED_BADGE: str = "[UNSOURCED]"`; `DEGRADED_BADGE: str = "degraded"`.
- `class HonestyIssue(DigestModel)`: `code: Literal["empty_output", "required_tools_zero_calls", "forbidden_tools_called", "missing_input_refs", "unsourced_number", "anchor_path_unresolved", "fabricated_number", "degraded_input"]`, `message: NonEmptyStr`, `pointer: NonEmptyStr | None = None`, `number_label: NonEmptyStr | None = None`.
- `class HonestyReport(DigestModel)`: `schema_version: Literal["1"] = "1"`, `worker_id: LogicalId`, `node_id: LogicalId`, `subject_content_digest: DigestHex | None`, `verdict: Literal["ok", "degraded", "incomplete"]`, `issues: tuple[HonestyIssue, ...] = ()`, `badges: tuple[NonEmptyStr, ...] = ()`. Registered payload (Task 11). Validator: `verdict="ok"` ⇔ no incomplete-class issues; `unsourced_number` issues force `UNSOURCED_BADGE` into `badges`.
- `def scan_unsourced_numbers(artifact: Artifact) -> tuple[HonestyIssue, ...]` — deterministic reconciliation of numeric leaves in the payload's semantic canonical JSON against `artifact.numbers`: an anchor whose `payload_path` resolves to no leaf ⇒ `anchor_path_unresolved`; an anchor whose `value` mismatches the resolved leaf ⇒ `fabricated_number`; an anchor with `is_unsourced=True` ⇒ `unsourced_number`. Leaves named `schema_version` or ending in `_digest` are exempt.
- `def classify_worker(*, worker: WorkerSpec, node_run: NodeRun, artifact: Artifact | None) -> HonestyReport` — closed rule matrix (spec 红线 "worker 编数/违反其 EvidencePolicy/空产出 → incomplete;合法无工具 worker 不因零调用被误杀"):
  1. terminal COMPLETED/DEGRADED with `artifact is None` or an empty primary payload ⇒ `empty_output` ⇒ incomplete;
  2. `tool_calls=REQUIRED` and `len(node_run.tool_call_records) == 0` ⇒ `required_tools_zero_calls` ⇒ incomplete; `FORBIDDEN` with `> 0` ⇒ `forbidden_tools_called` ⇒ incomplete; `OPTIONAL`/`FORBIDDEN` with zero calls is **never** an issue;
  3. `require_input_refs=True`, node has ≥1 dependency, and `artifact.input_refs == ()` ⇒ `missing_input_refs` ⇒ incomplete;
  4. `allow_unsourced_numbers=False` and any `unsourced_number`/`fabricated_number` issue ⇒ incomplete; `allow_unsourced_numbers=True` ⇒ verdict unchanged but `UNSOURCED_BADGE` mandatory;
  5. `optional_data_may_degrade=True` and NodeRun status DEGRADED ⇒ `degraded_input` issue, verdict `degraded`, `DEGRADED_BADGE`;
  6. otherwise verdict `ok`. The function is pure; it never mutates NodeRun/Artifact (runtime consumers map an incomplete verdict to `NodeStatus.INCOMPLETE`).
- **Attribution hook surface (spec §6.3):** `def attribution_candidates(reports: tuple[HonestyReport, ...]) -> tuple[HonestyReport, ...]` — deterministic, stable-ordered (by `node_id`) subset with verdict ≠ `ok`; this tuple, together with `Artifact.input_refs` citation chains and Task 9's `DebateTranscript`, is the frozen deterministic input the Phase 4 evaluator's attribution consumes before any LLM judging. The issue-`code` vocabulary above is closed for that reason.

**Required invariants:** rule matrix exhaustively tested per `EvidencePolicy` combination; `scan_unsourced_numbers` is order-independent and pure; a legal no-tool worker classifying `ok` is asserted explicitly; badge composition is idempotent.

- [ ] **Step 1: Write failing honesty tests** covering the full matrix (≥ one test per rule row and per issue code), anchor path resolution against a real `Artifact.build` fixture, and `attribution_candidates` ordering.

Run: `pytest tests/orchestration/test_honesty.py -v` — Expected: FAIL (missing module).

- [ ] **Step 2: Implement `honesty.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_honesty.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/honesty.py tests/orchestration/test_honesty.py
git commit -m "feat(orchestration): honesty spine with worker classification and number provenance scan"
```

---

## Task 3: ModelTier → llm.yaml bridge (catalog-owned config material)

The grounding map confirms **no code maps `ModelTier` → provider/model today** (NOT FOUND; catalog.py:108 only defines the Literal). Phase 8 defines the bridge.

**Files:**
- Create: `guanlan_v2/orchestration/model_tiers.py`
- Modify (additive): `config/llm.yaml` — add `agent_overrides` keys `orchestration-fast`, `orchestration-reasoner`, `orchestration-reasoner-deep`
- Create: `config/orchestration/materials/guardrails/model-tier-map.json` (physical material; bytes = canonical JSON of the reviewed `ModelTierMap`)
- Test: `tests/orchestration/test_model_tiers.py`

**Consumes:** Phase 1 `ModelTier` Literal, `ExecutionSpec`, `DigestModel`; engine convention `LLMClient.for_agent(agent_name)` reading `agent_overrides` (client.py:135) — consumed as documented convention only; orchestration code never imports engine config discovery.

**Produces (exact signatures):**

- `class ModelTierUnconfigured(ValueError)`.
- `class ModelTierBinding(DigestModel)`: `tier: ModelTier`, `provider: NonEmptyStr`, `model: NonEmptyStr`, `max_tokens: PositiveInt | None = None`, `timeout_sec: PositiveInt | None = None`.
- `class ModelTierMap(DigestModel)`: `schema_version: Literal["1"] = "1"`, `bindings: tuple[ModelTierBinding, ...]` — validator: exactly three bindings, one per tier, sorted by tier value (`fast` < `reasoner` < `reasoner_deep`). Registered payload (Task 11).
- `ORCHESTRATION_TIER_ALIASES: Mapping[ModelTier, str] = {"fast": "orchestration-fast", "reasoner": "orchestration-reasoner", "reasoner_deep": "orchestration-reasoner-deep"}` — the only join key between orchestration tiers and llm.yaml.
- `def tier_map_from_llm_yaml(yaml_text: str) -> ModelTierMap` — strict YAML (reuse Phase 1 `normalize_legacy_graph_config` loader discipline: duplicate keys/anchors rejected); requires all three alias keys under `agent_overrides` with explicit `provider` + `model`; any absence ⇒ `ModelTierUnconfigured` naming the tier — **no fallback to `default_provider`/`default_model`, ever** (silent-vendor red line).
- `def resolve_model_binding(execution: ExecutionSpec, *, tier_map: ModelTierMap) -> ModelTierBinding` — DETERMINISTIC execution ⇒ `ValueError`; returns the binding for `execution.model_tier`.
- `MODEL_TIER_MAP_MATERIAL_ID: LogicalId = "guardrail.model_tier_map"` — the map is a catalog `kind="guardrail"` material (Phase 2 precedent: descriptors/config are guardrail materials); its `ContentRef.content_digest` is the value recorded as `Provenance.model_config_digest` by every LLM node run under a Phase 8 catalog.

Initial reviewed values (per clause (g), from repo `config/llm.yaml` today): `fast → deepseek/deepseek-chat`; `reasoner → deepseek/deepseek-reasoner`; `reasoner_deep → deepseek/deepseek-reasoner, max_tokens=8192, timeout_sec=300`.

**Required invariants:**

1. removing any alias key from the YAML makes `tier_map_from_llm_yaml` raise `ModelTierUnconfigured` — proven for each tier;
2. the pinned-config trap is dead in tests: no test reads config via `find_config`; the fixture passes yaml bytes explicitly, and the one engine-integration test that spawns anything sets `FA_CONFIG_DIR=G:\guanlan-v2\config`;
3. the physical material bytes reproduce the reviewed `ModelTierMap` via `catalog_material_digest`, and Task 11's catalog build fails if the material drifts from the registered payload;
4. `dec.pm` is the only worker whose `ExecutionSpec.model_tier == "reasoner_deep"` — asserted here as a forward invariant over `lane_catalog` once Task 10 lands (marked `xfail` until then, converted in Task 10).

- [ ] **Step 1: Write failing tier-bridge tests** (map matrix, per-tier unconfigured raise, resolver, strict-YAML rejection, material digest reproduction).

Run: `pytest tests/orchestration/test_model_tiers.py -v` — Expected: FAIL (missing module).

- [ ] **Step 2: Implement `model_tiers.py`, add the three llm.yaml alias keys, write the material file**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_model_tiers.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/model_tiers.py config/llm.yaml config/orchestration/materials/guardrails/model-tier-map.json tests/orchestration/test_model_tiers.py
git commit -m "feat(orchestration): catalog-owned ModelTier to llm.yaml bridge"
```

---
## Migration batch discipline (applies to Tasks 4–7 and 10)

Every batch task delivers, for each named worker id:

1. **WorkerSpec** in `lane_catalog.py` via a pure builder `def build_<lane>_worker_specs(*, materials: tuple[ResolvedMaterial, ...]) -> tuple[WorkerSpec, ...]` — `catalog_role="final"`, `selection_scope="dynamic_allowed"`, no CompatibilityBinding, lane per the table (跨切 = `"xcut"`), exactly one output named `primary`, `supported_modes=("online","pit_replay")` unless the table narrows it;
2. **SKILL.md** under `guanlan_v2/orchestration/skills/<worker_id>/` (Task 1 grammar; playbook body distilled from the legacy source column, citing `memories_seed` playbooks where they exist) + mirror via `sync-skills.py`;
3. **guardrail materials** (kind `"guardrail"`) bound via `guardrail_refs`;
4. **capability manifest**: `capability_allowlist` rows as `CapabilityRef`s resolving in the cumulative catalog (Phase 3 data capabilities per clause (c)); FORBIDDEN ⇒ empty list;
5. **legacy schema adapter regression**: the batch test replays this batch's scalar rows from `tests/orchestration/fixtures/legacy_contract_samples.json` through the Phase 1 migration adapters, asserts the frozen MAPPED/UNMAPPABLE verdicts and that every reverse returns the exact original raw (reversibility preserved; no new mapping without an approved policy id);
6. **payload schemas** in `lane_payloads.py` (strict/frozen/extra-forbid `DigestModel`, `schema_version: Literal["1"]`, canonical tuple ordering, semantic/audit projections; registered only in Task 11's cumulative registry);
7. **prompt materials** (`prompt.<worker_id>`, kind `"prompt"`) for LLM workers, distilled from the legacy module-level `SYSTEM_PROMPT` constants; **handler materials** (`handler.<worker_id>`, kind `"handler"`) for deterministic workers, registered in the Phase 2 trusted handler registry keyed by full catalog ref identity.

Physical material paths (pinned by each batch's commit-block enumeration): skill mirror `config/orchestration/materials/skills/<skill_id>.md`; prompts `config/orchestration/materials/prompts/<worker_id>.md`; handlers `config/orchestration/materials/handlers/<worker_id>.py`; guardrails `config/orchestration/materials/guardrails/<hyphenated-name>.md` (matching the Task 3/8/9 hyphenated-file precedent).

Batch tests validate the batch's specs + materials through a standalone `build_catalog_snapshot` fixture (cumulative chain lands in Task 11). Deterministic handlers are total pure functions from declared typed inputs (upstream artifacts / Phase 3 DataResults in the frozen InputSnapshot) to the output payload — they never fetch; production data-method binding is Phase 9 adapter work and is not faked here.

---

## Task 4: Batch 1 · Lane C 文本 (5 workers — 4 new + 1 pilot reviewed update)

**Files:**
- Create: `guanlan_v2/orchestration/lane_payloads.py` (Lane C section), `guanlan_v2/orchestration/lane_catalog.py` (Lane C section)
- Create: `guanlan_v2/orchestration/skills/{text.news,text.research_report,text.policy,text.macro}/SKILL.md` (+ reviewed edit of `text.sentiment/SKILL.md`)
- Create: prompt/guardrail materials under `config/orchestration/materials/`
- Test: `tests/orchestration/test_lane_batch_text.py`

**Batch roster (worker id | exec/tier | tier(FSI) | legacy source | primary output | tools/capabilities | read_categories):**

| worker | exec | FSI | legacy source | primary output | tool policy · allowlist | read_categories |
|---|---|---|---|---|---|---|
| `text.news` | llm/`fast` | reader | `guanlan_v2/datafeed/kuaixun.py` + `news_marks` (帷幄, ~TA news-reader `tier1/news_reader.py`) | `NewsDigestReport@1` | REQUIRED · `get_news` | context, market_data |
| `text.sentiment` (pilot update) | llm/tier per clause (d) | critic | Phase 2 pilot; legacy `tier1/news_sentiment.py` + `guanlan_v2/datafeed/sentiment.py`; TA 反捏造 #557/#796 | `SentimentReport@1` (Phase 1) | FORBIDDEN · () — 不调工具、吃预取块 | context, upstream_artifacts |
| `text.research_report` | llm/`reasoner` | reader | 帷幄 Kimi 研报抽取 + 旧报降权 (`guanlan_v2/industry` pipeline) | `ResearchReportExtract@1` | OPTIONAL · `get_news` | context, market_data, upstream_artifacts |
| `text.policy` | llm/`fast` | reader | NEW (astock 政策/窗口指导 provenance) | `PolicyReport@1` | REQUIRED · `get_news` | context, market_data |
| `text.macro` | llm/`fast` | reader | `guanlan_v2/macro/pulse.py` + TA `get_prediction_markets` | `MacroPulseReport@1` | OPTIONAL · `get_signal` | context, market_data |

Pilot reviewed update (`text.sentiment`): rebind `skills` to the Task-1 relocated SKILL.md ref, add `guardrail.anti_fabrication` to `guardrail_refs`; all other implemented fields unchanged (clause (d)).

**Payload schemas (`lane_payloads.py`, all `DigestModel`, `schema_version: Literal["1"] = "1"`):**

- `NewsDigestItem`: `headline: NonEmptyStr`, `summary: NonEmptyStr`, `source_label: NonEmptyStr`, `published_at: UtcDateTime | None`, `available_at: UtcDateTime | None`, `codes: tuple[NonEmptyStr, ...] = ()`.
- `NewsDigestReport`: `as_of: UtcDateTime`, `scope: Literal["stock", "market", "both"]`, `items: tuple[NewsDigestItem, ...]`, `coverage_note: NonEmptyStr | None = None`.
- `ExtractedClaim`: `text: NonEmptyStr`, `kind: Literal["forecast", "fact", "opinion"]`, `anchored: bool`.
- `ResearchReportExtract`: `symbol: Symbol`, `as_of: UtcDateTime`, `source_report_label: NonEmptyStr`, `report_age_days: NonNegativeInt`, `staleness_downweight: FiniteFloat` (ge=0, le=1; 旧报降权), `claims: tuple[ExtractedClaim, ...]`.
- `PolicyEntry`: `title: NonEmptyStr`, `summary: NonEmptyStr`, `effective_hint: NonEmptyStr | None`, `source_label: NonEmptyStr`.
- `PolicyReport`: `as_of: UtcDateTime`, `stance: Literal["supportive", "neutral", "restrictive", "unknown"]`, `entries: tuple[PolicyEntry, ...]`.
- `PredictionMarketRead`: `market_label: NonEmptyStr`, `probability: FiniteFloat` (ge=0, le=1), `direction_hint: NonEmptyStr`.
- `MacroPulseReport`: `as_of: UtcDateTime`, `prediction_markets: tuple[PredictionMarketRead, ...]`, `board_temp: FiniteFloat | None`, `degradation: tuple[NonEmptyStr, ...] = ()`, `narrative: NonEmptyStr`.

**Guardrails:** `guardrail.untrusted_input_isolation` (FSI; all five), `guardrail.anti_fabrication` (text.sentiment, text.news), `guardrail.number_provenance` (all five; pairs with `EvidencePolicy.require_number_anchors=True`, `allow_unsourced_numbers=False`).

**Input bindings + EvidencePolicy** (exact `InputBinding(name, schema_ref, required, cardinality)` rows; data for REQUIRED/OPTIONAL-tool workers arrives via the Phase 3 data bridge against the allowlisted capability — never a handler-side fetch):

| worker | inputs | EvidencePolicy |
|---|---|---|
| `text.news` | () | tool_calls=REQUIRED, require_input_refs=True, require_number_anchors=True, allow_unsourced_numbers=False, optional_data_may_degrade=True |
| `text.sentiment` | pilot baseline unchanged (clause (d)) | pilot baseline (FORBIDDEN) unchanged |
| `text.research_report` | `news_digest` → `NewsDigestReport@1`, required=False, one | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `text.policy` | () | REQUIRED, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `text.macro` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |

**Legacy adapter regression rows:** `SRC_NEWS_SENTIMENT` categorical 利好/利空/中性 stays UNMAPPABLE; `SRC_PULSE_TEMP`/`SRC_ASTOCK_TEMP` temperature sentiment stays UNMAPPABLE; reverses exact.

**Required invariants:** `text.sentiment` keeps FORBIDDEN⇔empty allowlist; every LLM spec here carries `system_prompt_ref` + `model_tier`; `unknown` stance/`degradation` fields exist so coverage shortfalls are honest (UNAVAILABLE 不补零); no worker here has `can_emit_decision=True`.

- [ ] **Step 1: Write failing batch tests** — WorkerSpec matrix per roster row, payload schema matrices, skill grammar + mirror, adapter regression, pilot-update field diff limited to the two named fields.

Run: `pytest tests/orchestration/test_lane_batch_text.py -v` — Expected: FAIL (missing `lane_payloads`/`lane_catalog`).

- [ ] **Step 2: Implement Lane C payloads, specs, materials, skills; run `sync-skills.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane_batch_text.py tests/orchestration/test_skilltree.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/lane_payloads.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/test_lane_batch_text.py \
  guanlan_v2/orchestration/skills/text.news/SKILL.md guanlan_v2/orchestration/skills/text.research_report/SKILL.md \
  guanlan_v2/orchestration/skills/text.policy/SKILL.md guanlan_v2/orchestration/skills/text.macro/SKILL.md \
  guanlan_v2/orchestration/skills/text.sentiment/SKILL.md \
  config/orchestration/materials/skills/text.news.md config/orchestration/materials/skills/text.research_report.md \
  config/orchestration/materials/skills/text.policy.md config/orchestration/materials/skills/text.macro.md \
  config/orchestration/materials/skills/text.sentiment.md \
  config/orchestration/materials/prompts/text.news.md config/orchestration/materials/prompts/text.research_report.md \
  config/orchestration/materials/prompts/text.policy.md config/orchestration/materials/prompts/text.macro.md \
  config/orchestration/materials/guardrails/untrusted-input-isolation.md config/orchestration/materials/guardrails/anti-fabrication.md \
  config/orchestration/materials/guardrails/number-provenance.md
git commit -m "feat(orchestration): migrate lane C text workers (batch 1/5)"
```

---

## Task 5: Batch 2 · Lane B 量价几何 (3 workers)

**Files:**
- Modify: `guanlan_v2/orchestration/lane_payloads.py`, `lane_catalog.py` (Lane B sections)
- Create: `guanlan_v2/orchestration/skills/{pv.price_action,pv.technical,pv.microstructure}/SKILL.md` + materials
- Test: `tests/orchestration/test_lane_batch_pv.py`

**Batch roster:**

| worker | exec | FSI | legacy source | primary output | tool policy · allowlist | read_categories |
|---|---|---|---|---|---|---|
| `pv.price_action` | deterministic (`handler.pv.price_action`) | reader | `guanlan_v2/seats/price_action.py` `compute_pa_features` 15键 (clause (h)) + 可编辑方法论 (帷幄 EV-017~026) | `PriceActionFeatureReport@1` | OPTIONAL · `get_ohlcv` (bridge prefetch feeds the handler; deterministic ⇒ zero LLM reservations) | market_data |
| `pv.technical` | llm/`reasoner` | reader | TA `tier2/technical_analyst.py` (`TechnicalOutput`) + `get_verified_snapshot` 真值锚 | `TechnicalReport@1` | REQUIRED · `get_verified_snapshot`, `get_indicators` | context, market_data |
| `pv.microstructure` | deterministic (`handler.pv.microstructure`) | reader | `guanlan_v2/seats/live_book.py` + `market_tape`/`fundflow` (帷幄); TA `tier2/whale_analyst.py` per fixture node map | `MicrostructureReport@1` | OPTIONAL · `get_signal` (五档/逐笔/主力 are OPTIONAL `signal_data`-class methods — 端点常坏, 降级不 crash) | market_data |

**Input bindings + EvidencePolicy:**

| worker | inputs | EvidencePolicy |
|---|---|---|
| `pv.price_action` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `pv.technical` | `price_action` → `PriceActionFeatureReport@1`, required=False, one | REQUIRED, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `pv.microstructure` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |

**Payload schemas:**

- `PriceActionFeatureReport`: `symbol: Symbol`, `as_of: UtcDateTime`, `feature_set_version: Literal["pa-15key-v1"]`, `features: dict[NonEmptyStr, FiniteFloat]` (validator: exactly the 15 frozen PA keys of `compute_pa_features`), `methodology_ref: ContentRef | None = None` (可编辑方法论 material; default off, opt-in).
- `IndicatorReading`: `name: NonEmptyStr`, `value: FiniteFloat`, `note: NonEmptyStr | None = None`.
- `TechnicalReport`: `symbol: Symbol`, `as_of: UtcDateTime`, `indicators: tuple[IndicatorReading, ...]` (validator: 1–8 entries, unique names — ≤8 互补指标), `verified_anchor_digest: DigestHex | None` (content digest of the `VerifiedSnapshotDataResult` truth anchor), `bias: Literal["bullish", "bearish", "neutral", "unknown"]`, `summary: NonEmptyStr`.
- `MicrostructureReport`: `symbol: Symbol`, `as_of: UtcDateTime`, `l1_spread_bp: FiniteFloat | None`, `bid_ask_imbalance: FiniteFloat | None`, `break_ratio: FiniteFloat | None`, `whale_net_inflow: FiniteFloat | None`, `degradation: tuple[NonEmptyStr, ...] = ()`, `narrative: NonEmptyStr`.

**Guardrails:** `guardrail.untrusted_input_isolation` (pv.technical), `guardrail.number_provenance` (all three).

**Handler semantics:** `handler.pv.price_action` calls the existing pure `compute_pa_features` (前后端镜像逐位一致 red line — the handler must not reimplement the 15-key geometry) over an OHLCV input binding; `handler.pv.microstructure` is a pure projection over `l1 book / ticks / tape` DataResult inputs with explicit `degradation` rows for every absent optional feed (orderbook 空档降级 precedent).

**Legacy adapter regression rows:** none of this batch's legacy schemas has a Phase 1 scalar adapter; the batch regression asserts exactly that (no adapter invented) and pins the fixture's `whale-analyst → pv.microstructure` design-intent row.

**Required invariants:** deterministic specs carry `handler_ref` and no tier; `pv.technical` REQUIRED policy has non-empty allowlist and its zero-call runs will classify incomplete via Task 2 (`classify_worker` cross-test); 15-key bitwise equality with `compute_pa_features` output on a fixed fixture bar.

- [ ] **Step 1: Write failing batch tests** (roster matrices, 15-key equality, ≤8-indicator validator, degradation honesty, adapter-absence assertion).

Run: `pytest tests/orchestration/test_lane_batch_pv.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement; run `sync-skills.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane_batch_pv.py tests/orchestration/test_honesty.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/lane_payloads.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/test_lane_batch_pv.py \
  guanlan_v2/orchestration/skills/pv.price_action/SKILL.md guanlan_v2/orchestration/skills/pv.technical/SKILL.md \
  guanlan_v2/orchestration/skills/pv.microstructure/SKILL.md \
  config/orchestration/materials/skills/pv.price_action.md config/orchestration/materials/skills/pv.technical.md \
  config/orchestration/materials/skills/pv.microstructure.md \
  config/orchestration/materials/prompts/pv.technical.md \
  config/orchestration/materials/handlers/pv.price_action.py config/orchestration/materials/handlers/pv.microstructure.py
git commit -m "feat(orchestration): migrate lane B price-volume workers (batch 2/5)"
```

---

## Task 6: Batch 3 · Lane A 量化 (5 workers)

**Files:**
- Modify: `lane_payloads.py`, `lane_catalog.py` (Lane A sections)
- Create: `guanlan_v2/orchestration/skills/{quant.factor,quant.model,quant.backtest,quant.fundamentals,quant.factor_miner}/SKILL.md` + materials
- Test: `tests/orchestration/test_lane_batch_quant.py`

**Batch roster (all deterministic per the Task-0 worker map — handler_ref, no model_tier, zero LLM reservations):**

| worker | handler | legacy source | primary output | tool policy · allowlist | read_categories |
|---|---|---|---|---|---|
| `quant.factor` | `handler.quant.factor` | `guanlan_v2/screen/factor_ic.py` (帷幄 rescore/factor_ic) | `FactorICReport@1` | OPTIONAL · `get_signal` | market_data |
| `quant.model` | `handler.quant.model` | `guanlan_v2/screen/model_registry.py` (v4+DL 集成) | `ModelPredictionReport@1` | OPTIONAL · `get_signal` | market_data |
| `quant.backtest` | `handler.quant.backtest` | `guanlan_v2/screen/factor_vintage.py` (vintage/OOS/PBO backtest cards) | `BacktestEvidenceReport@1` | FORBIDDEN · () | upstream_artifacts |
| `quant.fundamentals` | `handler.quant.fundamentals` | TA `tier2/fundamental_analyst.py` (`FundamentalOutput`) + astock `get_profit_forecast` provenance | `FundamentalsReport@1` | OPTIONAL · `get_fundamentals` | market_data |
| `quant.factor_miner` | `handler.quant.factor_miner` | `guanlan_v2/research/loop.py` (Sharpe/robust 门) | `MinedFactorDraft@1` | FORBIDDEN · () | upstream_artifacts |

**Input bindings + EvidencePolicy:**

| worker | inputs | EvidencePolicy |
|---|---|---|
| `quant.factor` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `quant.model` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `quant.backtest` | `factor_ic` → `FactorICReport@1`, required=True, one; `model_predictions` → `ModelPredictionReport@1`, required=False, one | FORBIDDEN, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `quant.fundamentals` | () | OPTIONAL, input_refs=True, anchors=True, unsourced=False, degrade=True |
| `quant.factor_miner` | `factor_ic` → `FactorICReport@1`, required=False, one | FORBIDDEN, input_refs=True, anchors=True, unsourced=False, degrade=True |

**Payload schemas:**

- `FactorICRow`: `factor_id: LogicalId`, `ic: FiniteFloat`, `rank_ic: FiniteFloat | None`, `window: NonEmptyStr`, `oos: bool` (回看 IC must not masquerade as OOS — badge-adjacent honesty).
- `FactorICReport`: `as_of: UtcDateTime`, `rows: tuple[FactorICRow, ...]` (sorted by `factor_id`, dup-free).
- `ModelScoreRow`: `symbol: Symbol`, `score: FiniteFloat`, `rank: PositiveInt`.
- `ModelPredictionReport`: `as_of: UtcDateTime`, `model_id: NonEmptyStr`, `model_asof: UtcDateTime`, `rows: tuple[ModelScoreRow, ...]` (ranks unique/ascending), `stale_days: NonNegativeInt` (DL 断供显形 precedent).
- `BacktestEvidenceReport`: `as_of: UtcDateTime`, `subject: NonEmptyStr`, `vintage_ic: FiniteFloat | None`, `oos_verdict: NonEmptyStr | None`, `pbo: FiniteFloat | None` (ge=0, le=1), `caveats: tuple[NonEmptyStr, ...] = ()`.
- `FundamentalsReport`: `symbol: Symbol`, `as_of: UtcDateTime`, `valuation_score: FiniteFloat | None`, `mv_tier: NonEmptyStr | None`, `profit_forecast_note: NonEmptyStr | None`, `inputs_complete: bool`.
- `MinedFactorDraft`: `as_of: UtcDateTime`, `factor_expr: NonEmptyStr`, `rank_ic: FiniteFloat`, `sharpe: FiniteFloat | None`, `robust: FiniteFloat | None`, `passed_gate: bool`, `draft_only: Literal[True] = True` (structural draft red line — factorlib promotion stays human).

**Guardrails:** `guardrail.number_provenance` (all five); `guardrail.draft_only_advisory` (quant.factor_miner).

**Legacy adapter regression rows:** this batch's fixture scalars (`market_cycle@1#stage` via `migrate_rotation_stage`, confidence scalars via `migrate_confidence`) replay with frozen verdicts; reverses exact.

**Required invariants:** every spec deterministic⇔handler_ref, FORBIDDEN⇔empty allowlist; `MinedFactorDraft.draft_only` cannot be constructed False; `oos=False` rows never satisfy an OOS-labeled downstream gate (documented for Task 8's gate-metric materials); handlers per clause (h).

- [ ] **Step 1: Write failing batch tests**

Run: `pytest tests/orchestration/test_lane_batch_quant.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement; run `sync-skills.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane_batch_quant.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/lane_payloads.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/test_lane_batch_quant.py \
  guanlan_v2/orchestration/skills/quant.factor/SKILL.md guanlan_v2/orchestration/skills/quant.model/SKILL.md \
  guanlan_v2/orchestration/skills/quant.backtest/SKILL.md guanlan_v2/orchestration/skills/quant.fundamentals/SKILL.md \
  guanlan_v2/orchestration/skills/quant.factor_miner/SKILL.md \
  config/orchestration/materials/skills/quant.factor.md config/orchestration/materials/skills/quant.model.md \
  config/orchestration/materials/skills/quant.backtest.md config/orchestration/materials/skills/quant.fundamentals.md \
  config/orchestration/materials/skills/quant.factor_miner.md \
  config/orchestration/materials/handlers/quant.factor.py config/orchestration/materials/handlers/quant.model.py \
  config/orchestration/materials/handlers/quant.backtest.py config/orchestration/materials/handlers/quant.fundamentals.py \
  config/orchestration/materials/handlers/quant.factor_miner.py \
  config/orchestration/materials/guardrails/draft-only-advisory.md
git commit -m "feat(orchestration): migrate lane A quant workers (batch 3/5)"
```

---

## Task 7: Batch 4 · 跨切 xcut (2 workers, wired to honesty.py)

**Files:**
- Modify: `lane_payloads.py`, `lane_catalog.py` (xcut sections)
- Create: `guanlan_v2/orchestration/skills/{x.quality_gate,x.number_critic}/SKILL.md` + materials
- Test: `tests/orchestration/test_lane_batch_xcut.py`

**Batch roster (lane literal `"xcut"`, NOT the worker map's `cross`):**

| worker | exec | legacy source | primary output | read_categories |
|---|---|---|---|---|
| `x.quality_gate` | deterministic (`handler.x.quality_gate`) | `guanlan_v2/datafeed/health.py` (数据质量 ABCDF; astock provenance) | `DataQualityGrade@1` | upstream_artifacts, market_data |
| `x.number_critic` | deterministic (`handler.x.number_critic`) | TA `tier3/introspector.py` (`IntrospectionProposal`, `"med"` frozen UNMAPPABLE) + FSI 不可信输入隔离 | `HonestyReport@1` (Task 2) | upstream_artifacts |

**Payload schemas:**

- `QualityComponent`: `source_id: LogicalId`, `grade: Literal["A", "B", "C", "D", "F"]`, `reason: NonEmptyStr`.
- `DataQualityGrade`: `as_of: UtcDateTime`, `grade: Literal["A", "B", "C", "D", "F"]`, `components: tuple[QualityComponent, ...]` (sorted by `source_id`, dup-free).

**Input bindings** (dependency injection uses exact `SchemaRef` equality — spec.py:999-1000 — so a heterogeneous "all artifacts" binding is impossible; both workers declare explicit typed optional inputs and operate on whichever are wired per plan):

| worker | inputs (all required=False, cardinality=one unless noted) | EvidencePolicy |
|---|---|---|
| `x.number_critic` | `bull_case` → `BullCase@1`; `bear_case` → `BearCase@1`; `research_plan` → `ResearchPlan@1`; `portfolio_decision` → `PortfolioDecision@1`; `technical` → `TechnicalReport@1`; `fundamentals` → `FundamentalsReport@1` | FORBIDDEN, input_refs=True, anchors=False (it produces the anchor verdicts), unsourced=False, degrade=True |
| `x.quality_gate` | `news_digest` → `NewsDigestReport@1`; `macro_pulse` → `MacroPulseReport@1`; `microstructure` → `MicrostructureReport@1`; `model_predictions` → `ModelPredictionReport@1` | FORBIDDEN, input_refs=True, anchors=True, unsourced=False, degrade=True |

**Handler semantics:** `handler.x.number_critic` = `honesty.scan_unsourced_numbers` + `honesty.classify_worker` applied to each wired input Artifact, emitting one `HonestyReport` per subject; with multiple subjects the primary payload is the report for the first (by input name) violating subject and remaining reports ride `rendered_md` + issues — the 数字溯源门: every load-bearing number unsourced ⇒ `[UNSOURCED]` badge propagates to the offending artifact's consumers via the report. `handler.x.quality_gate` grades the wired reports' coverage/degradation/staleness fields (`degradation`, `stale_days`, `coverage_note`) into ABCDF components per source.

**Legacy adapter regression rows:** `IntrospectionProposal@1#confidence` `"med"` stays UNMAPPABLE (`migrate_confidence`), `low`/`high` MAPPED; reverses exact. The batch does not invent a `med→medium` mapping (fixture lines 204-232 freeze it).

**Required invariants:** both specs FORBIDDEN⇔empty allowlist, `can_emit_decision=False`; `x.number_critic` output is byte-stable under input artifact reordering; a fabricated-number fixture (anchor value ≠ payload leaf) yields verdict incomplete.

- [ ] **Step 1: Write failing batch tests** — Run: `pytest tests/orchestration/test_lane_batch_xcut.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement; run `sync-skills.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane_batch_xcut.py tests/orchestration/test_honesty.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/lane_payloads.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/test_lane_batch_xcut.py \
  guanlan_v2/orchestration/skills/x.quality_gate/SKILL.md guanlan_v2/orchestration/skills/x.number_critic/SKILL.md \
  config/orchestration/materials/skills/x.quality_gate.md config/orchestration/materials/skills/x.number_critic.md \
  config/orchestration/materials/handlers/x.quality_gate.py config/orchestration/materials/handlers/x.number_critic.py
git commit -m "feat(orchestration): migrate cross-cut xcut workers (batch 4/5)"
```

---
## Task 8: Runtime profile v2 — reducers / multi-writer slots / gate metrics / retry + bounded schema repair

**Files:**
- Modify (additive): `guanlan_v2/orchestration/runtime_support.py`, `guanlan_v2/orchestration/pool.py`, `guanlan_v2/orchestration/worker.py`, `guanlan_v2/orchestration/dag.py`
- Create: `config/orchestration/materials/gate_metrics/oos-rank-ic.json` (the one reviewed `kind="gate_metric"` material this phase ships: `gate_metric.oos_rank_ic`, a closed JSON-pointer projection reading `rank_ic` from a `FactorICReport@1`/`MinedFactorDraft@1` metrics artifact where `oos=true`/`passed_gate` applies; no expression language)
- Test: `tests/orchestration/test_runtime_profile_v2.py`

**Consumes:** Phase 2 `StaticRuntimeProfile@1` (v1 constant + golden untouched), `check_runtime_support`, `ArtifactPool.commit_layer`, `execute_node`, `run_plan`, `BudgetLedger` (`scope_type` values `"retry"`/`"schema_repair"` from Phase 1 `BudgetScopeType`), `RuntimeUnitOfWork`; Phase 1 `ReducerCfg`, `GateCfg`, `GateResult`, `TextMaterialKind` values `"reducer"`/`"gate_metric"`.

**Produces:**

- `STATIC_RUNTIME_PROFILE_V2: StaticRuntimeProfile` — `profile_id="static-runtime"`, `profile_version="2"` (clause (f)), feature matrix enabling exactly: `debates`, `reducers`, `multi_writer_slots`, `gate_metrics`, `max_attempts_limit=2`, `schema_repairs_per_attempt=1`. Conditions and stop conditions remain rejected before reservation under v2. Phase 2 v1 and Phase 5 BOOTSTRAP constants/goldens untouched; a v1-bound admitted Plan replays with v1 rejection behavior (registry resolution by exact digest, no reinterpretation).
- v2 support analyzers (pure, I/O-free, run before reservation like every Phase 2 analyzer):
  - `analyze_reducers(draft: PlanDraft, *, catalog: WorkerCatalogSnapshot) -> tuple[RuntimeSupportIssue, ...]` — every multi-writer slot has exactly one `ReducerCfg` whose `reducer_ref` resolves to a catalog `kind="reducer"` material and whose `producer_node_ids` equal the slot's writers; `output_schema_ref` registered;
  - `analyze_gates(draft, *, catalog) -> tuple[RuntimeSupportIssue, ...]` — every `GateCfg.metric` resolves to `kind="gate_metric"` material; blocking gates with `unavailable_policy="fail"` documented as run-blocking;
  - `analyze_retry_repair(draft, *, profile) -> tuple[RuntimeSupportIssue, ...]` — `max_attempts ≤ profile.max_attempts_limit`; LLM nodes reserve `llm_invocations = max_attempts × (1 + schema_repairs_per_attempt)` upper bound; deterministic nodes with `max_attempts>1` reserve zero LLM invocations.
- Runner/pool extensions (executor-owned, no public handler injection):
  - reducer execution at the layer barrier: when a layer's committed producers cover a multi-writer slot, the runner resolves the reducer material via `CatalogRuntime`, executes the trusted deterministic reducer handler over producer Artifacts **sorted by `PlanNode.id`** (spec §6.1 line 391), stages the reduced Artifact and commits it inside the same `commit_layer` UoW; thread completion order cannot change the reduced payload (property test with shuffled completion);
  - gate evaluation: after the metrics-producing node commits, the runner resolves the gate-metric material, evaluates `operator/threshold` against the observed value, builds Phase 1 `GateResult` (with `metrics_artifact_id`), and applies `blocking`/`unavailable_policy` — failed blocking gate ⇒ dependents BLOCKED, run terminal `partial`; `unavailable` follows the closed policy (`fail`/`degrade`/`skip`);
  - retry: on a retryable terminal failure with `attempt < max_attempts`, a new attempt with fresh `attempt_id`, child reservation `scope_type="retry"`, same logical idempotency key — at most one committed output per logical key (Phase 2 invariant preserved);
  - bounded schema repair: when an LLM node's primary payload fails registry validation, at most `schema_repairs_per_attempt` repair invocation(s) per attempt: a second `PromptAssemblyRecord` persists (validator errors enter only the untrusted data channel), reservation `scope_type="schema_repair"`, both invocations count against the run LLM budget (spec §8 line 947 / §10 line 968); repair failure ⇒ INCOMPLETE, no Artifact.

**Required invariants:**

1. v1-profile Plans still reject debates/reducers/gates/retries before reservation (regression against Phase 2 tests);
2. reducer output is a function of (Plan, committed producer payloads) only — shuffled completion property test;
3. no reducer ⇒ multi-writer slot still rejected at Phase 1 validation (spec.py multi-writer check) — v2 does not bypass it;
4. budget: a v2 Plan's reservation covers the retry/repair upper bound; settlement records actual invocations; crash between repair and settle replays idempotently;
5. staged-but-uncommitted repair attempts never become visible without `LayerCommitted`.

**Test matrix (each row one focused test):**

| # | case | expected |
|---|---|---|
| 1 | v1 profile + draft with reducer/gate/debate/`max_attempts=2` | rejected before reservation (Phase 2 behavior pinned) |
| 2 | v2 + multi-writer slot without ReducerCfg | Phase 1 `validate_plan_draft` rejects (v2 does not bypass) |
| 3 | v2 + reducer_ref of kind `"prompt"` | `analyze_reducers` issue |
| 4 | v2 + two producers, shuffled completion ×20 seeds | reduced Artifact semantic digest identical |
| 5 | gate `>=` passed / failed / `unavailable`×{fail,degrade,skip} | GateResult status + downstream BLOCKED/DEGRADED/SKIPPED per policy |
| 6 | `max_attempts=3` under v2 (`max_attempts_limit=2`) | `analyze_retry_repair` issue |
| 7 | LLM node, invalid payload once then valid | repair invocation with `scope_type="schema_repair"`, second `PromptAssemblyRecord`, COMPLETED |
| 8 | invalid payload twice (`schema_repairs_per_attempt=1`) | INCOMPLETE, no Artifact, reservations settled |
| 9 | crash between repair and settle, replay | idempotent — one settled reservation, no duplicate invocation billed |
| 10 | deterministic node `max_attempts=2`, first attempt raises | retry attempt with `scope_type="retry"`, zero LLM reservations, one committed output per logical key |

- [ ] **Step 1: Write failing profile-v2 tests** (analyzer matrices, reducer order property, gate policy matrix, retry/repair budget + idempotency, v1 regression pins — the matrix above).

Run: `pytest tests/orchestration/test_runtime_profile_v2.py -v` — Expected: FAIL (missing v2 profile/analyzers/behaviors).

- [ ] **Step 2: Implement additively; rerun the Phase 2 runtime suite to prove no v1 behavior change**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_runtime_profile_v2.py -v && pytest tests/orchestration -q` — Expected: PASS, full suite green.

```bash
git add guanlan_v2/orchestration/runtime_support.py guanlan_v2/orchestration/pool.py guanlan_v2/orchestration/worker.py guanlan_v2/orchestration/dag.py config/orchestration/materials/gate_metrics/oos-rank-ic.json tests/orchestration/test_runtime_profile_v2.py
git commit -m "feat(orchestration): static runtime profile v2 with reducers gates retry and bounded repair"
```

---

## Task 9: Lane D bounded-debate runtime (`debate.py` + debate event)

Phase 1 owns the debate **contracts** (DebateCfg, PlanNode debate fields, plan validators — spec.py:301-406, 599-625); the grounding map confirms **zero debate runtime/reducer/message code exists anywhere**. Task 9 builds it.

**Files:**
- Create: `guanlan_v2/orchestration/debate.py`
- Modify (additive): `guanlan_v2/orchestration/events.py` (+ absence-guard flips per clause (b)); `guanlan_v2/orchestration/runtime_support.py` (debate analyzer); `guanlan_v2/orchestration/dag.py`/`pool.py` (debate event emission at barrier)
- Create: `config/orchestration/materials/reducers/debate-transcript-reducer.py` (trusted reducer handler material)
- Test: `tests/orchestration/test_debate_runtime.py`

**Produces (exact signatures):**

- `class DebateMessage(DigestModel)` — spec §8 field names preserved, types upgraded to Phase 1 house rules: `schema_version: Literal["1"] = "1"`, `debate_id: LogicalId`, `round: PositiveInt`, `turn: PositiveInt`, `role: LogicalId`, `artifact_id: NonEmptyStr`, `created_at: UtcDateTime`. `SEMANTIC_EXCLUDE = frozenset({"created_at"})` (wall clock is audit); semantic identity is `(debate_id, round, turn, role, artifact_id)` — the immutable event of spec §3.4 line 166. Registered payload (Task 11).
- `class DebateTranscript(DigestModel)`: `schema_version: Literal["1"] = "1"`, `debate_id: LogicalId`, `max_rounds: PositiveInt`, `messages: tuple[DebateMessage, ...]` — validator: strictly ordered by `(round, turn)`, unique `(round, turn)` and `(round, role)`, every `round ≤ max_rounds`. Registered payload; the reducer output schema for debate slots.
- `def verify_debate_expansion(draft: PlanDraft, debate: DebateCfg) -> tuple[PlanValidationIssue, ...]` — the "完全展开" rule (spec §8 line 945) on top of Phase 1's structural checks: the debate's nodes must cover exactly rounds `1..R` for some `R ≤ max_rounds`, each round covering `turn_order` completely and in order (`debate_turn` = 1-based index in `turn_order`, `round_role` = the seat at that index); issue codes `debate_incomplete_round`, `debate_turn_order_mismatch`, `debate_rounds_exceed_max`.
- `def count_debate_invocations(draft: PlanDraft) -> NonNegativeInt` — expanded LLM invocation count over all debate nodes (每一席每一轮均计一次 LLM invocation/seat 预算); consumed by the v2 analyzer `analyze_debates(draft, *, catalog, profile)` which also requires: every debate node's worker is LLM; `judge_node_id` exists and is **not** itself a debate seat node of the same debate; `budget_request_llm_invocations ≥ count_debate_invocations(draft) + non-debate LLM node count` (per-attempt/repair multipliers from Task 8 stack on top).
- `def fold_debate_messages(*, debate: DebateCfg, nodes: tuple[PlanNode, ...], committed: Mapping[LogicalId, Artifact]) -> DebateTranscript` — the deterministic reducer core: message order derives **only** from Plan node debate fields sorted by `(debate_round, debate_turn)`, never thread completion (辩论消息按 `(debate_id,round,turn,role)` fold — spec §6.1 line 391); `artifact_id`/`created_at` taken from each committed Artifact. Registered as trusted reducer handler `DEBATE_TRANSCRIPT_REDUCER_ID: LogicalId = "debate.transcript_reducer"` (catalog `kind="reducer"` material) so a Plan wires it via `ReducerCfg(slot=<debate slot>, reducer_ref=…, producer_node_ids=<seat node ids>, output_schema_ref=DebateTranscript@1)`.
- Event: `EventType.DEBATE_MESSAGE_PUBLISHED = "DebateMessagePublished"` — pure additive member; visible event (`partition="main"`, payload `DebateMessage@1`), appended inside the same `commit_layer` UoW that publishes a debate node's Artifact (persist-then-publish; one event per committed debate node; idempotency key `(run_id, plan_digest, debate_id, round, turn, role)`). Flip the then-current expected-set guards additively (clause (b)).

**Required invariants:**

1. immutability: no mutable `DebateState` anywhere; a second message for the same `(debate_id, round, turn)` is an `IdempotencyConflict`, not an update;
2. fold determinism: property test — shuffled completion/commit order yields byte-identical `DebateTranscript` semantic digest;
3. expansion: a draft missing round-2's bear turn, or with turn order swapped, fails `verify_debate_expansion` with the exact issue code; Phase 1's own `duplicate_debate_turn`/`debate_round_out_of_range` still fire first where applicable (no duplicated implementation — Task 9 checks only what Phase 1 does not);
4. budget: a 2-seat×2-round + 3-seat×2-round plan counts 10 debate invocations; under-requesting is rejected before reservation;
5. ≤2 rounds: `max_rounds > 2` for any debate whose seats include Lane D workers is rejected by the v2 analyzer (profile constant `debate_max_rounds=2`, spec §0 决定 2);
6. `DebateMessagePublished` events replay to the same transcript as the pool fold (event-sourced view consistency).

**Test matrix (each row one focused test):**

| # | case | expected |
|---|---|---|
| 1 | DebateMessage construction/frozen/extra-forbid; `created_at` audit-only differential digest | semantic digest stable under `created_at` change |
| 2 | transcript with out-of-order / duplicate `(round,turn)` / duplicate `(round,role)` / `round > max_rounds` messages | each rejected with its validator error |
| 3 | 2×2 bull/bear expansion missing bear r2 | `debate_incomplete_round` |
| 4 | turn order (bear, bull) in round 1 nodes vs `turn_order=("bull","bear")` | `debate_turn_order_mismatch` |
| 5 | rounds 1..3 declared, `max_rounds=2` | Phase 1 `debate_round_out_of_range` fires (no Task 9 duplicate) |
| 6 | 3-seat×2-round + 2-seat×2-round draft | `count_debate_invocations == 10`; under-requested budget rejected pre-reservation |
| 7 | judge node also a seat node of the same debate | `analyze_debates` issue |
| 8 | deterministic worker as a seat | `analyze_debates` issue |
| 9 | fold with committed artifacts arriving in 20 shuffled orders | byte-identical `DebateTranscript` |
| 10 | same `(debate_id,round,turn)` staged twice with different content | `IdempotencyConflict` |
| 11 | replay `DebateMessagePublished` visible events | transcript equals pool fold |
| 12 | EventType expected-set guard | extended additively; upstream sets untouched |

- [ ] **Step 1: Write failing debate-runtime tests** covering every invariant above plus the guard flips (the matrix above).

Run: `pytest tests/orchestration/test_debate_runtime.py -v` — Expected: FAIL (missing `debate` module/event member).

- [ ] **Step 2: Implement `debate.py`, the event member + guard flips, analyzer and barrier emission**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_debate_runtime.py tests/orchestration/test_events.py tests/orchestration/test_contract_completeness.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/debate.py guanlan_v2/orchestration/events.py guanlan_v2/orchestration/runtime_support.py guanlan_v2/orchestration/dag.py guanlan_v2/orchestration/pool.py config/orchestration/materials/reducers/debate-transcript-reducer.py tests/orchestration/test_debate_runtime.py tests/orchestration/test_events.py tests/orchestration/test_contract_completeness.py
git commit -m "feat(orchestration): lane D bounded debate runtime with immutable messages and plan-order reducer"
```

---

## Task 10: Batch 5 · Lane D 决策/风控 (6 workers — 4 new + 2 pilot reviewed updates)

**Files:**
- Modify: `lane_payloads.py`, `lane_catalog.py` (Lane D sections)
- Create: `guanlan_v2/orchestration/skills/{dec.bull,dec.bear,dec.risk_debate,dec.trader}/SKILL.md` (+ reviewed edits of `dec.research_mgr/SKILL.md`, `dec.pm/SKILL.md`) + materials
- Test: `tests/orchestration/test_lane_batch_decision.py`

**Batch roster:**

| worker | exec/tier | FSI | legacy source | primary output | can_emit_decision | read_categories |
|---|---|---|---|---|---|---|
| `dec.bull` | llm/`reasoner` | critic | TA `tier3/bull_advocate.py` (`BullOutput`, V1–V9 anchors; `memories_seed/bull-advocate/playbook_V1_V10.md`) | `BullCase@1` | False | context, upstream_artifacts |
| `dec.bear` | llm/`reasoner` | critic | TA `tier3/bear_advocate.py` (`BearOutput`, F1–F14; `failure_modes_F1_F14.md`; bear 晚一波逐条反驳) | `BearCase@1` | False | context, upstream_artifacts |
| `dec.research_mgr` (pilot update) | llm/tier per clause (d) | writer | Phase 2 pilot; TA `tier3/report_writer.py` (`ReportOutput` — rating [-10,10] frozen UNMAPPABLE) | `ResearchPlan@1` (Phase 1) | True | context, upstream_artifacts |
| `dec.risk_debate` | llm/`fast` | critic | TA `tier3/risk_officer.py` (`RiskOutput`, single-seat in code → 激进/稳健/中性 three seats by PlanNode instancing; `hard_rules.md` GAME-CAPITAL VETO) | `RiskDebateStance@1` | False | context, upstream_artifacts |
| `dec.pm` (pilot update) | llm/`reasoner_deep` (the ONLY one) | writer | Phase 2 pilot; NEW A股约束终裁, 注入 PIT-safe past_context | `PortfolioDecision@1` (Phase 1) | True | context, upstream_artifacts, memory |
| `dec.trader` | llm/`reasoner` | writer | NEW; action-domain regression via `WatchRec`/`DecisionLeg` (`migrate_position_action`) | `PortfolioTargetProposal@1` (**Phase 6-owned schema; imported, never redefined**) | True | context, upstream_artifacts |

Pilot reviewed updates: `dec.research_mgr` — rebind skill ref to the Task-1 tree, add `guardrail.debate_rounds`, add optional input `bullbear_transcript` → `DebateTranscript@1` (the judge reads the folded debate view); `dec.pm` — assert/set `model_tier="reasoner_deep"`, add `read_categories` `memory` (PIT-safe past_context via the Phase 3 memory bridge; retrieval stays 0/0 tool bounds), add `guardrail.debate_rounds`, add optional input `riskdebate_transcript` → `DebateTranscript@1`. All other implemented pilot fields unchanged (clause (d)).

**Payload schemas:**

- `BullCase`: `symbol: Symbol`, `as_of: UtcDateTime`, `thesis_bullets: tuple[NonEmptyStr, ...]` (non-empty), `catalysts: tuple[NonEmptyStr, ...] = ()`, `target_price_high: PositivePrice | None`, `target_price_base: PositivePrice | None`, `disproof_signals: tuple[NonEmptyStr, ...] = ()`, `v_anchors: tuple[NonEmptyStr, ...] = ()`, `rebuttal_of: tuple[NonEmptyStr, ...] = ()` (round-2 逐条反驳 targets: opposing bullet texts).
- `BearCase`: `symbol: Symbol`, `as_of: UtcDateTime`, `thesis_bullets: tuple[NonEmptyStr, ...]` (non-empty), `valuation_concerns: tuple[NonEmptyStr, ...] = ()`, `technical_breakdown: tuple[NonEmptyStr, ...] = ()`, `target_price_low: PositivePrice | None`, `downside_pct: FiniteFloat | None`, `f_anchors: tuple[NonEmptyStr, ...] = ()`, `rebuttal_of: tuple[NonEmptyStr, ...] = ()`.
- `RiskDebateStance`: `symbol: Symbol`, `as_of: UtcDateTime`, `stance_role: Literal["aggressive", "steady", "neutral"]`, `risk_score: Annotated[int, Field(ge=-2, le=0)]` (legacy domain preserved — never positive), `position_sizing_advice: NonEmptyStr`, `veto_flags: tuple[NonEmptyStr, ...] = ()`, `blind_spots: tuple[NonEmptyStr, ...] = ()`, `conditional_approval: NonEmptyStr | None = None`, `rebuttal_of: tuple[NonEmptyStr, ...] = ()`.

**Canonical Lane D plan shape** (encoded as a reviewed test fixture, not a new preset module):

- Bull/bear debate `debate_id="bullbear"`: `seats=("bull","bear")`, `turn_order=("bull","bear")`, `max_rounds=2`, `judge_node_id=<dec.research_mgr node>`; 4 PlanNodes (`dec.bull` r1t1 → `dec.bear` r1t2 dep-on-bull-r1 → `dec.bull` r2t1 dep-on-bear-r1 → `dec.bear` r2t2 dep-on-bull-r2), all writing slot `slot.bullbear_transcript` with `ReducerCfg(reducer_ref=debate.transcript_reducer, output_schema_ref=DebateTranscript@1)`.
- Risk debate `debate_id="riskdebate"`: **one** `dec.risk_debate` WorkerSpec instantiated as **6** PlanNodes — `round_role ∈ {aggressive, steady, neutral}` × `debate_round ∈ {1,2}`, `turn_order=("aggressive","steady","neutral")`, `max_rounds=2`, `judge_node_id=<dec.pm node>`; `stance_role` in each node's `params` must equal its `round_role` (params validated against the worker's `params_schema_ref`); all six write `slot.riskdebate_transcript` via the same reducer.
- `dec.pm` depends on both transcripts + `ResearchPlan`; `dec.trader` depends on `PortfolioDecision` and is the only sink whose primary schema name is in `_DECISION_CLASS_SCHEMAS` beyond the pilots — sink authorization passes only because `can_emit_decision=True`.

**Guardrails:** `guardrail.debate_rounds` (all six — encodes ≤2 rounds + rebuttal discipline), `guardrail.advisory_shadow_only` (`dec.trader`: proposal-only wording; the runtime intent envelope is Phase 6's, and **no Phase 8 capability/material can construct `TargetPortfolioIntent`**), `guardrail.untrusted_input_isolation`, `guardrail.number_provenance` (all six).

**Input bindings + EvidencePolicy** (Lane D is pool-fed: every spec here is tool_calls=FORBIDDEN with empty allowlist, require_input_refs=True, require_number_anchors=True, allow_unsourced_numbers=False, optional_data_may_degrade=True; `dec.pm`'s past_context arrives via the memory bridge pre-input, not a capability call):

| worker | inputs |
|---|---|
| `dec.bull` | `fundamentals` → `FundamentalsReport@1` (opt); `technical` → `TechnicalReport@1` (opt); `sentiment` → `SentimentReport@1` (opt); `news_digest` → `NewsDigestReport@1` (opt); `opponent_case` → `BearCase@1` (opt — wired only on round-2 nodes for 逐条反驳) |
| `dec.bear` | same four evidence inputs (opt) + `opponent_case` → `BullCase@1`, required=True (bear 晚一波 always rebuts a bull case) |
| `dec.research_mgr` | pilot baseline + `bullbear_transcript` → `DebateTranscript@1` (opt; reviewed update) |
| `dec.risk_debate` | `research_plan` → `ResearchPlan@1`, required=True; `opponent_stances` → `RiskDebateStance@1`, required=False, cardinality=**many** (round-2 seats read all round-1 stances, injected in Plan dependency declaration order) |
| `dec.pm` | pilot baseline + `riskdebate_transcript` → `DebateTranscript@1` (opt; reviewed update) |
| `dec.trader` | `portfolio_decision` → `PortfolioDecision@1`, required=True |

`dec.risk_debate.params_schema_ref` binds a registered params model `RiskDebateParams@1` (`stance_role: Literal["aggressive","steady","neutral"]`) — the per-instance seat assignment validated against `round_role` by the Task 9 analyzer.

**Legacy adapter regression rows:** `ReportOutput@1#action` via `migrate_research_action` (identity MAPPED), `ReportOutput@1#rating` via `migrate_rating` (UNMAPPABLE, no 5-band binning invented), `WatchRec@1#action` via `migrate_position_action` (MAPPED), risk `confidence` scalars via `migrate_confidence`; reverses exact. `RiskOutput.risk_score` has **no** Phase 1 scalar adapter — the batch preserves its domain in `RiskDebateStance` and asserts adapter absence rather than inventing one.

**Required invariants:**

1. `dec.pm` is the only `reasoner_deep` spec across the whole `lane_catalog` (converts Task 3's xfail);
2. `dec.trader` outputs exactly `PortfolioTargetProposal@1` imported from Phase 6; no Phase 8 module defines a model by that name;
3. the canonical Lane D fixture passes Phase 1 `validate_plan_draft` + Task 9 `verify_debate_expansion` + Task 8/9 v2 analyzers, and mutations (missing turn, round 3, judge-as-seat, `stance_role`≠`round_role`, non-LLM seat) each fail with their exact issue code;
4. bull r2/bear r2 nodes depend on the opposing prior-round node (晚一波 rebuttal semantics live in Plan dependencies, not prose);
5. debate seats use only `fast`/`reasoner` tiers.

- [ ] **Step 1: Write failing batch tests** — Run: `pytest tests/orchestration/test_lane_batch_decision.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement Lane D payloads/specs/materials/skills + pilot updates; run `sync-skills.py`**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane_batch_decision.py tests/orchestration/test_debate_runtime.py tests/orchestration/test_model_tiers.py -v` — Expected: PASS (xfail converted).

```bash
git add guanlan_v2/orchestration/lane_payloads.py guanlan_v2/orchestration/lane_catalog.py tests/orchestration/test_lane_batch_decision.py \
  guanlan_v2/orchestration/skills/dec.bull/SKILL.md guanlan_v2/orchestration/skills/dec.bear/SKILL.md \
  guanlan_v2/orchestration/skills/dec.risk_debate/SKILL.md guanlan_v2/orchestration/skills/dec.trader/SKILL.md \
  guanlan_v2/orchestration/skills/dec.research_mgr/SKILL.md guanlan_v2/orchestration/skills/dec.pm/SKILL.md \
  config/orchestration/materials/skills/dec.bull.md config/orchestration/materials/skills/dec.bear.md \
  config/orchestration/materials/skills/dec.risk_debate.md config/orchestration/materials/skills/dec.trader.md \
  config/orchestration/materials/skills/dec.research_mgr.md config/orchestration/materials/skills/dec.pm.md \
  config/orchestration/materials/prompts/dec.bull.md config/orchestration/materials/prompts/dec.bear.md \
  config/orchestration/materials/prompts/dec.risk_debate.md config/orchestration/materials/prompts/dec.trader.md \
  config/orchestration/materials/guardrails/debate-rounds.md config/orchestration/materials/guardrails/advisory-shadow-only.md
git commit -m "feat(orchestration): migrate lane D decision workers with bounded debate wiring (batch 5/5)"
```

---
## Task 11: Phase 8 cumulative registry/catalog chain + goldens + final-24 reconciliation

**Files:**
- Modify: `guanlan_v2/orchestration/lane_catalog.py` (chain exports)
- Create: `tests/orchestration/golden/phase8_schema_manifest_v1.json`, `tests/orchestration/golden/phase8_catalog_manifest_v1.json`
- Test: `tests/orchestration/test_phase8_registry_catalog.py`

**Consumes:** Phase 7 chain (`PHASE7_REGISTRY_DIGEST`/`PHASE7_CATALOG_DIGEST` per clause (a)); every Phase 8 model/material from Tasks 1–10.

**Produces (CRIB 4.5 recursion, exact names):**

- `PHASE8_PUBLIC_MODELS: tuple[type[ContractModel], ...]` — exactly 21 new registered schemas: `HonestyReport`, `ModelTierMap`, `DebateMessage`, `DebateTranscript`, `RiskDebateParams` (params schema — must resolve for Phase 1 params validation), plus the 16 lane payloads (`NewsDigestReport`, `ResearchReportExtract`, `PolicyReport`, `MacroPulseReport`, `PriceActionFeatureReport`, `TechnicalReport`, `MicrostructureReport`, `FactorICReport`, `ModelPredictionReport`, `BacktestEvidenceReport`, `FundamentalsReport`, `MinedFactorDraft`, `DataQualityGrade`, `BullCase`, `BearCase`, `RiskDebateStance`). Nested value objects (`NewsDigestItem`, `ExtractedClaim`, `PolicyEntry`, `PredictionMarketRead`, `IndicatorReading`, `FactorICRow`, `ModelScoreRow`, `QualityComponent`, `ModelTierBinding`, `HonestyIssue`) join `PHASE8_INTERNAL_MODELS` with reviewed reason strings; the contract-completeness partition grows accordingly.
- `PHASE8_BASE_REGISTRY_DIGEST = PHASE7_REGISTRY_DIGEST`; `PHASE8_REGISTRY_DIGEST: DigestHex`; `def build_phase8_registry(expected_phase7_digest: DigestHex) -> SchemaRegistry` — verifies the Phase 7 manifest/digest first, registers cumulative Phase 2–7 publics + `PHASE8_PUBLIC_MODELS`, seals; inherited JSON Schemas byte-identical; rejects any other base digest; no "latest" alias.
- `PHASE8_CATALOG_DIGEST: DigestHex`; `def build_phase8_catalog_snapshot(phase7_snapshot: WorkerCatalogSnapshot, *, lane_worker_specs: tuple[WorkerSpec, ...], reviewed_worker_updates: tuple[WorkerSpec, ...], resolved_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot` — rejects any base but the exact Phase 7 catalog digest; applies the 5 reviewed updates (3 pilots from Tasks 4/10 + relocation-touched Lane 0 refs per clause (e), if any) by worker id (update, never duplicate); adds the 18 new finals, all skill/prompt/guardrail/handler/reducer/gate-metric/tier-map materials via Phase 1 `build_catalog_snapshot` (one-to-one ref/material coverage, skill-v1 re-parse, digest verification).
- Golden `phase8_schema_manifest_v1.json` (registry) and `phase8_catalog_manifest_v1.json` (catalog digest + per-worker semantic digests + skill manifest digests) — hand-frozen from a one-off verification run, reviewed, never auto-regenerated; upstream goldens untouched.

**Required invariants:**

1. **Final-24 reconciliation:** `count_final_workers(snapshot) == 24`; the id set equals exactly the spec §3 roster (`market.*`×3 from Phase 5 + 3 pilots + 18 authored here); no `compat.*` id counts; every lane count matches spec §3 (market 3 / quant 5 / pv 3 / text 5 / decision 6 / xcut 2);
2. wrong-base rejection both directions (Phase 6 digest as base fails; Phase 8 digest as its own base fails);
3. old Plans bound to Phase ≤7 digests remain resolvable via `SchemaRegistryResolver` without reinterpretation;
4. `check.py --catalog-golden` passes against the frozen catalog golden (skills tree ↔ mirror ↔ catalog digests all agree);
5. **market.rotation single-writer resolution** (grounding-map gotcha 2): the legacy `stock-deep-dive` design-intent maps TWO nodes (`mainline-classifier`, `sector-rotation-analyzer`) to `market.rotation`. Resolution (frozen here): worker **multi-instance, distinct slots** — a final-worker plan instantiates `market.rotation` twice with `writes_slot="slot.mainline_rotation"` and `writes_slot="slot.sector_rotation"`; no shared slot, hence no reducer and no single-writer violation (spec §3 运行时可多实例; spec §6.1 line 391 untouched). A reviewed fixture plan encodes this and passes `validate_plan_draft`; the alternative (one slot + reducer) is explicitly rejected as it would fuse two distinct evidence streams.

- [ ] **Step 1: Write failing chain tests** (reconciliation, wrong-base, resolver coexistence, golden reproduce, rotation fixture).

Run: `pytest tests/orchestration/test_phase8_registry_catalog.py -v` — Expected: FAIL (missing chain exports/goldens).

- [ ] **Step 2: Implement chain builders; record digests from a one-off run; hand-freeze both goldens; review**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase8_registry_catalog.py tests/orchestration/test_contract_completeness.py tests/orchestration/test_skilltree.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/lane_catalog.py tests/orchestration/golden/phase8_schema_manifest_v1.json tests/orchestration/golden/phase8_catalog_manifest_v1.json tests/orchestration/test_phase8_registry_catalog.py
git commit -m "feat(orchestration): seal phase8 cumulative registry and 24-worker catalog chain"
```

---

## Task 12: Lane D end-to-end + red-line regression

**Files:**
- Test: `tests/orchestration/test_phase8_e2e.py`

**Consumes:** everything above; Phase 2 admission/`run_plan` with `STATIC_RUNTIME_PROFILE_V2`; a deterministic fake `ModelGateway` (records assembled requests, returns typed fixtures per node).

**Scenario:** the canonical Lane D fixture plan (Task 10) — bull/bear 2 rounds + `dec.research_mgr` judge + risk 3-seat × 2 rounds + `dec.pm` + `dec.trader` — plus one `x.number_critic` node, admitted under the Phase 8 catalog/registry digests through the full validate→support(v2)→reserve→approve(REQUIRED)→freeze→dispatch→replay path.

**Fixture plan node roster** (LogicalId node ids; `(debate_id, round_role, debate_round, debate_turn)` all-set on seat nodes only):

| node id | worker | debate fields | writes_slot | key deps |
|---|---|---|---|---|
| `bull-r1` | `dec.bull` | (bullbear, bull, 1, 1) | `slot.bullbear_transcript` | evidence inputs |
| `bear-r1` | `dec.bear` | (bullbear, bear, 1, 2) | `slot.bullbear_transcript` | `opponent_case` ← bull-r1 |
| `bull-r2` | `dec.bull` | (bullbear, bull, 2, 1) | `slot.bullbear_transcript` | `opponent_case` ← bear-r1 |
| `bear-r2` | `dec.bear` | (bullbear, bear, 2, 2) | `slot.bullbear_transcript` | `opponent_case` ← bull-r2 |
| `research-mgr` | `dec.research_mgr` | — (judge of bullbear) | `slot.research_plan` | `bullbear_transcript` ← reduced slot |
| `risk-{aggressive,steady,neutral}-r1` | `dec.risk_debate` ×3 | (riskdebate, role, 1, 1..3) | `slot.riskdebate_transcript` | `research_plan` ← research-mgr |
| `risk-{aggressive,steady,neutral}-r2` | `dec.risk_debate` ×3 | (riskdebate, role, 2, 1..3) | `slot.riskdebate_transcript` | `opponent_stances` ← the three r1 nodes (many) |
| `pm` | `dec.pm` | — (judge of riskdebate) | `slot.portfolio_decision` | `riskdebate_transcript` ← reduced slot |
| `trader` | `dec.trader` | — | `slot.target_proposal` | `portfolio_decision` ← pm |
| `number-critic` | `x.number_critic` | — | `slot.honesty` | `bull_case`/`bear_case`/`research_plan`/`portfolio_decision` (opt) |

Budget arithmetic pinned by the test: debate seats 4 + 6 = 10, judges/sinks 3 (`research-mgr`, `pm`, `trader`), `number-critic` deterministic = 0 ⇒ base 13 LLM invocations; the reservation upper bound additionally covers Task 8's `max_attempts × (1 + schema_repairs_per_attempt)` multipliers for nodes that declare them.

**Required assertions:**

1. budget: plan reservation covers the expanded count (10 debate + judge/pm/trader/critic invocations per Task 8/9 rules); settlement equals actual; `AUTO` approval still rejected;
2. transcripts: both `DebateTranscript` artifacts are byte-identical under shuffled node completion (property rerun); `DebateMessagePublished` visible events replay to the same transcripts;
3. tiers: the fake gateway observed `reasoner_deep` binding exactly once, on the `dec.pm` node (via Task 3 resolver); every seat ran `fast`/`reasoner`;
4. honesty: an injected unsourced-number fixture on one seat yields `x.number_critic` verdict incomplete + `[UNSOURCED]` badge propagation; a REQUIRED-tools worker with zero calls classifies incomplete; the legal no-tool `text.sentiment` path classifies ok (cross-lane spot check);
5. red lines: `dec.trader`'s Artifact payload is `PortfolioTargetProposal@1` with no intent fields; no catalog capability can write orders/signals/`TargetPortfolioIntent`/skills/memory (structural sweep over the Phase 8 capability manifest); the artifact is draft-only (no promotion path exists in any Phase 8 export);
6. schema repair: one seat returning invalid payload once recovers via a single repair invocation (second `PromptAssemblyRecord`, budget settled with the extra invocation); twice-invalid yields INCOMPLETE with no Artifact;
7. audit replay reconstructs plan/node states/artifacts/transcripts with zero model calls.

- [ ] **Step 1: Write the failing e2e** — Run: `pytest tests/orchestration/test_phase8_e2e.py -v` — Expected: FAIL until wiring is complete (missing-behavior red, not collection error).

- [ ] **Step 2: Fix integration gaps surfaced by the e2e (in their owning modules, additively)**

- [ ] **Step 3: Run the full suite and commit**

Run: `pytest tests/orchestration -v` and `python -m compileall -q guanlan_v2/orchestration`; if Ruff is available: `ruff check guanlan_v2/orchestration tests/orchestration`.

Expected: PASS.

```bash
git add tests/orchestration/test_phase8_e2e.py
git commit -m "test(orchestration): lane D debate e2e with red-line regression"
```

---

## Appendix: Phase 8 material inventory (reviewed closure)

The Task 11 catalog build must cover exactly this material set (one-to-one ref/material coverage; orphans in either direction fail `build_catalog_snapshot`). The drift-lint golden and reviewer checklists key off this table.

| material id (source_identity / logical id) | kind | bound by |
|---|---|---|
| `skill.<worker_id>` ×21 (18 new + 3 pilot relocations; + Lane 0 relocations per clause (e)) | skill | each worker's `skills` (SkillBinding, required=True) |
| `prompt.text.news`, `prompt.text.research_report`, `prompt.text.policy`, `prompt.text.macro`, `prompt.pv.technical`, `prompt.dec.bull`, `prompt.dec.bear`, `prompt.dec.risk_debate`, `prompt.dec.trader` (9 new; pilots keep their implemented prompt refs unless the reviewed update names a change) | prompt | LLM workers' `system_prompt_ref` |
| `handler.pv.price_action`, `handler.pv.microstructure`, `handler.quant.factor`, `handler.quant.model`, `handler.quant.backtest`, `handler.quant.fundamentals`, `handler.quant.factor_miner`, `handler.x.quality_gate`, `handler.x.number_critic` (9) | handler | deterministic workers' `execution.handler_ref` + Phase 2 trusted handler registry |
| `guardrail.untrusted_input_isolation`, `guardrail.anti_fabrication`, `guardrail.number_provenance`, `guardrail.draft_only_advisory`, `guardrail.debate_rounds`, `guardrail.advisory_shadow_only` (6) | guardrail | per-batch `guardrail_refs` as tabulated |
| `guardrail.model_tier_map` (Task 3) | guardrail | catalog-owned config material; `Provenance.model_config_digest` source |
| `debate.transcript_reducer` (Task 9) | reducer | `ReducerCfg.reducer_ref` of every debate slot |
| `gate_metric.oos_rank_ic` (Task 8) | gate_metric | `GateCfg.metric` in v2 gate tests/plans |

Capability manifest closure (clause (c) ids): `get_news` (text.news REQUIRED, text.policy REQUIRED, text.research_report OPTIONAL), `get_signal` (text.macro, pv.microstructure, quant.factor, quant.model), `get_ohlcv` (pv.price_action), `get_verified_snapshot` + `get_indicators` (pv.technical REQUIRED), `get_fundamentals` (quant.fundamentals). All are Phase 3 data-adapter capabilities already in the cumulative catalog — Phase 8 grants them to workers but registers **no new capability**, and grants **no** write/order/signal/skill/memory capability to anyone.

---

## Phase 8 Exit Gates

Phase 8 is complete only when every gate below is checked by tests and reviewed artifacts.

### Upstream handoff and chain

- [ ] every Phase 2–7 Exit Gate remains green; `test_phase8_handoff.py` passes with recorded evidence;
- [ ] Phase 8 imports, never redefines, Phase 1 catalog/spec/skill-grammar/migration contracts, Phase 2 runtime services and the Phase 6 `PortfolioTargetProposal`;
- [ ] `PHASE8_REGISTRY_DIGEST`/`build_phase8_registry(expected_phase7_digest)` and `PHASE8_CATALOG_DIGEST`/`build_phase8_catalog_snapshot(...)` exist with their own goldens; inherited schemas byte-identical; wrong-base rejected; no "latest" alias; upstream goldens untouched (`git diff` empty on them);
- [ ] Task-0 correction clauses (a)–(h) each either confirmed or applied as a reviewed plan update;
- [ ] full suite green from repo root (`pytest tests/orchestration -v`), `python -m compileall -q guanlan_v2/orchestration` clean, and (if available) `ruff check guanlan_v2/orchestration tests/orchestration` clean;
- [ ] the 21 Phase 8 registered schemas and their internal value objects keep the contract-completeness partition exhaustive and disjoint (no unclassified model);
- [ ] every commit in this phase used explicit pathspec (spot-check `git log --stat` — no unrelated concurrent-session file swept in).

### Skills single source

- [ ] `guanlan_v2/orchestration/skills/` is the only human-edited skill location; `sync-skills.py` produces the byte-identical mirror; `check.py` exits nonzero on byte drift, orphan/missing mirror, grammar error and catalog digest mismatch;
- [ ] every SKILL.md passes Phase 1 `parse_skill_v1` (3-line description, canonical-JSON triggers, critical heading first);
- [ ] pilot/Lane 0 relocations preserved material digests;
- [ ] no capability/handler/script can write the tree; skill changes are proposals + human review; per-run skill digests ride `Provenance.skill_refs`.

### Honesty spine

- [ ] `classify_worker` matrix fully tested: 编数/违反 EvidencePolicy/空产出 ⇒ incomplete; REQUIRED-zero-calls incomplete; legal no-tool worker never killed; unsourced numbers ⇒ `[UNSOURCED]` badge and (when disallowed) incomplete; degradation always badged;
- [ ] `x.number_critic` and `x.quality_gate` run as `xcut`-lane final workers over honesty handlers; fabricated-number fixture caught;
- [ ] `attribution_candidates` + `DebateTranscript` + `input_refs` form the frozen deterministic attribution hook surface (spec §6.3) with a closed issue-code vocabulary.

### Migration batches and final-24

- [ ] batches landed in the frozen order Lane C → Lane B → Lane A → 跨切 → Lane D, each with WorkerSpec + SKILL.md + guardrails + capability manifest + adapter regression;
- [ ] `count_final_workers == 24` with the exact spec §3 id/lane roster; 3 Lane 0 (Phase 5) + 3 pilots (Phase 2, reviewed updates only) + 18 authored here; no `compat.*` counted;
- [ ] frozen UNMAPPABLE verdicts untouched (rating, `med`, all sentiment, rotation stage); every adapter reverse returns the exact original raw;
- [ ] `x.*` WorkerSpecs use lane `"xcut"`; `market.rotation` dual-node resolution = multi-instance distinct slots, fixture-proven;
- [ ] every batch's Input bindings/EvidencePolicy tables match the shipped WorkerSpecs field-for-field (a table drift is a plan defect, not an implementation liberty);
- [ ] `dec.trader` emits only `PortfolioTargetProposal@1`; `dec.pm` is the only `reasoner_deep`; debate seats are `fast`/`reasoner`.

### Debate runtime and profile v2

- [ ] `DebateMessage`/`DebateTranscript` registered; messages immutable with `(debate_id, round, turn, role)` identity; duplicate ⇒ `IdempotencyConflict`;
- [ ] reducer folds by Plan order only — shuffled-completion property tests byte-identical; event replay equals pool fold;
- [ ] full-expansion validation + per-seat per-round invocation budget enforced before reservation; `max_rounds ≤ 2`;
- [ ] `STATIC_RUNTIME_PROFILE_V2` unlocks exactly debates/reducers/multi-writer/gates/`max_attempts≤2`/repair≤1; conditions and stop conditions still rejected; v1-bound and BOOTSTRAP-profile Plans behave exactly as before (regression pins);
- [ ] retry/repair reservations use `scope_type="retry"`/`"schema_repair"`, count against the run LLM budget, and replay idempotently.

### Model tier bridge

- [ ] `ModelTierMap` material is catalog-owned; any unconfigured tier raises `ModelTierUnconfigured` — no silent vendor fallback;
- [ ] `Provenance.model_config_digest` equals the tier-map material digest on every Phase 8 LLM node run;
- [ ] no orchestration test depends on `find_config` resolution; engine-spawning tests pin `FA_CONFIG_DIR=G:\guanlan-v2\config`.

### spec §13 挂账 closure (typed 迁移批次)

- [ ] the per-batch roster with legacy sources (Tasks 4–7, 10 tables) discharges the §13 "逐批次名单" debt — every one of the 24 ids appears in exactly one owning phase (5/2/8) with its legacy source named;
- [ ] 兼容期 stance recorded: `compat.*` workers remain `static_legacy_only` and fully runnable throughout Phase 8 — old swarm presets keep working unchanged;
- [ ] 旧入口删除门槛 is explicitly **deferred to Phase 9** (its 红线/并发/恢复/e2e 全绿 gate list); Phase 8 removes no legacy entry point.

### Red lines and scope protection

- [ ] no order/signal/intent/memory-write/skill-write/code-write capability exists in the Phase 8 catalog (structural sweep);
- [ ] all Lane D outputs draft-only advisory; no promotion path; `TargetPortfolioIntent` construction remains Phase 6 runtime-only;
- [ ] `workflow/executor.run_graph` and `engine/financial_analyst/**` unchanged;
- [ ] no Lane 0 re-authoring, no optimizer/governor/planner/adapter work smuggled in (Phases 4/5/7/9 own those);
- [ ] unrelated worktree changes are not staged (explicit pathspec only).

---

## Execution Handoff

Execute with `superpowers:subagent-driven-development` (or `superpowers:executing-plans` in a dedicated session) task-by-task, in order, with review checkpoints:

1. **Checkpoint A (after Task 0):** reviewer confirms upstream evidence digests and every correction clause disposition before any Phase 8 code.
2. **Checkpoint B (after Task 1):** skills-tree grammar/mirror/lint reviewed; relocation digest preservation verified.
3. **Checkpoint C (after Tasks 2–3):** honesty matrix and tier bridge reviewed (red-line sensitive: silent-vendor and fabrication rules).
4. **Checkpoint D (after each batch Task 4/5/6/7):** per-batch roster vs spec §3 wording, adapter-regression verdicts, and skill/guardrail/capability manifests reviewed.
5. **Checkpoint E (after Tasks 8–9):** profile v2 additivity (v1/BOOTSTRAP regression pins) and debate determinism property tests reviewed.
6. **Checkpoint F (after Task 10):** Lane D wiring (expansion fixture, judge nodes, pilot update diffs, tier assignments) reviewed.
7. **Checkpoint G (after Tasks 11–12):** goldens hand-frozen and reviewed; final-24 reconciliation; full-suite + e2e evidence. Do not begin Phase 9 until every Phase 8 Exit Gate is checked with test evidence.

Commit after every green step with the exact pathspecs shown; a shared branch with concurrent sessions means `git status --short` before every commit and no branch switching.

Reviewer quick-reference for the frozen accounting: 24 finals = 3 Lane 0 (Phase 5) + 3 pilots (Phase 2, reviewed updates only) + 18 authored here; 21 new registered schemas; 9 new prompts, 9 handlers, 7 guardrail-kind materials (incl. tier map), 1 reducer, 1 gate metric; batch order Lane C → Lane B → Lane A → 跨切 → Lane D; `dec.pm` alone on `reasoner_deep`; debates capped at 2 rounds; `xcut` is the lane literal; `PortfolioTargetProposal` is imported from Phase 6, never redefined.




