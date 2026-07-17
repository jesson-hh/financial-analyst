# Orchestration Phase 5 · Bootstrap Lane 0 + 经验库 RegimeCase Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the handoff gate, the market-factor compute core, the experience retrieval/grader pair, the Lane 0 catalog + BOOTSTRAP profile, and the final bootstrap end-to-end task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not require an environment-specific execution skill that may not be installed.

**Goal:** Deliver the market-context layer the whole framework boots from: a fixed, versioned `BootstrapPlan` (Lane 0 static Plan `market.factor` → `market.regime` + `market.rotation`) admitted under a new BOOTSTRAP runtime profile, a deterministic market-factor worker computed strictly PIT (`UNAVAILABLE` never zero-filled), the PIT-safe experience library `RegimeCase`/`CaseMatured`/`CaseReviewed` with numeric nearest-neighbour retrieval over a versioned point-in-time scaler, a delayed deterministic grader (no LLM), cold-start historical replay seeding with a future-case-invariance acceptance, and the Phase 5 cumulative registry/catalog chain. The bootstrap run uses a `context_snapshot_id=None` `RunContext`; after the run a `ContextSnapshot` is committed and a **new** main `RunContext` is derived referencing it — no in-place mutation.

**Architecture:** Phase 1 (amended: `TypedPayloadRef`, 11-model golden) remains sole owner of digests, refs, `PlanDraft`/`Plan` validation/freeze, `ContextSnapshot`/`InputSnapshot`, `RunEvent` and the three reserved event types `CASE_CREATED`/`CASE_MATURED`/`CASE_REVIEWED` (`guanlan_v2/orchestration/events.py:119-121` — reused verbatim, **no enum change**). Phase 2 owns admission/eventstore/pool/executor/`run_plan` and `StaticRuntimeProfile`; Phase 3 owns PIT guard/errors/`TradingCalendar` protocol; Phase 4 owns Trial/Holdout, `TrialLedger`, `run_optimize` and the `PHASE4_*` chain this phase extends. Phase 5 adds three modules — `bootstrap.py`, `market/factors.py`, `memory/experience.py` — plus Lane 0 catalog materials. Everything is draft/advisory: Lane 0 workers have `can_emit_decision=False`; nothing here touches trading, memory-core writes, `workflow/executor.run_graph` or the market-temp shield constants.

**Tech Stack:** Python ≥3.11, Pydantic v2 strict/frozen/extra-forbid over Phase 1 `ContractModel`/`DigestModel`, `pytest`. Pure-Python numeric core (stdlib `math`/`statistics`; no new hard sklearn dependency — the scaler/NN are hand-rolled like `strategy/compute/jump_model.py`). All modules `from __future__ import annotations`. Run tests from repo root `G:\guanlan-v2` with `pytest`.

## Global Constraints

These extend, and never override, the Phase 1 (as amended), Phase 2, Phase 3 and Phase 4 Global Constraints and Exit Gates. Every task implicitly includes those documents.

- **Consume, do not fork.** Import `DigestModel`, strict types, `SchemaRef`/`ContentRef`/`CapabilityRef`/`PayloadRef`/`TypedPayloadRef`, `SchemaRegistry`, `WorkerSpec`/catalog builders, `PlanDraft`/`validate_plan_draft`/`freeze_plan`, `RunEvent`/`EventType`, `RunContext`/`ContextSnapshot`/`build_empty_memory_binding`, `DataMode`/`PlanSource`/`RotationStage`/`Confidence`/`ExperimentStatus` from their owning modules. Phase 5 defines no second digest, no second plan validator, no second event type enum, no second calendar protocol.
- **Typed refs.** Wherever a typed evidence pair (schema + payload locator) is meant, use Phase 1 `TypedPayloadRef(schema_ref, payload_ref)`; plain `PayloadRef` only as a bare storage locator inside it. Public/runtime evidence refs require `payload_ref.namespace == "main"`.
- **Registry/catalog chain is linear.** Phase 5 extends exactly `PHASE4_REGISTRY_DIGEST`/`PHASE4_CATALOG_DIGEST` (which themselves extend `PHASE3_FULL_*`). No "latest" alias; upstream goldens (`schema_manifest_v1.json`, `runtime_schema_manifest_v1.json`, `data_schema_manifest_v1.json`, `phase3_full_*`, `phase4_*`) are never regenerated; inherited JSON Schemas stay byte-identical.
- **PIT first, honesty always.** Every store/computation pushes `available_at <= as_of` down before any ranking, smoothing or retrieval; violations raise Phase 3 `FutureDataRefused` and never fall through. Missing history yields an explicit `unavailable` status with a reason — never a zero-fill, never a current snapshot masquerading as history (`market_tape` is snapshot-only; a `board_backfilled` snapshot is never a same-day observation).
- **Event-folded, append-only.** `pending/matured/reviewed` are views folded from `CASE_CREATED`/`CASE_MATURED`/`CASE_REVIEWED` events; no old object is ever edited in place. Persist-then-publish, deterministic idempotency keys, `IdempotencyConflict` on same-key/different-content.
- **Red lines.** LLM zero trading (Lane 0 emits no decision-class schema); draft-only with human review for `CaseReviewed`; workers propose-never-write memory/skill/code; no silent vendor fallback; every degradation carries a badge; `ApprovalPolicy.AUTO` stays rejected for every `PlanSource` including the bootstrap preset.
- **Frozen shield.** `screen/market_temp.py` gate constants (risk_off ≤25 / ≤-300亿, overheat ≥85 & ≥0.35) and the astock temperature coefficients are user-frozen; Phase 5 provides an upstream *projection with parity tests* and changes neither the constants nor the shield's cache-read wiring.
- **`run_graph` untouched.** `guanlan_v2/workflow/executor.py` (`_DISPATCH`, `_OUT_PORT`, `run_graph`) is not edited. "走 run_graph/PIT 口径" is satisfied by: pure deterministic compute over PIT-windowed inputs, content-digested reports enabling bit-identical replay, and inputs drawn from the same provider/regen artifacts the workflow layer consumes. Registering a canvas node type is an explicitly deferred reviewed 薄扩展 (out of scope here).
- **One numeric core.** MA/EMA/slope/z-score/percentile/drawdown helpers are implemented once in `market/factors.py` (pure, stdlib-only) and imported by the grader/seeder; the scaler/NN arithmetic lives once in `memory/experience.py`. No duplicate formula, no numpy/pandas dependency in the contract modules (the production loader may use pandas to read parquet — the pure cores must not).
- **Evidence flows through the owned ports.** Runtime evidence (selection, scaler, rendered block) is written only via `BridgeEvidenceWriter` with executor-minted ordinal tokens; refusals only via `EventRefusalAuditSink`; experience facts only via `ExperienceLog`. No provider-held raw `PayloadStore` write handle anywhere in Phase 5.
- **Executable red/green checkpoints.** Every step named "Write failing … tests" immediately runs the focused command shown in that task and records the expected missing-contract/behavior failure before implementation; collection errors do not count as the red checkpoint. The later PASS step reruns the same focused tests plus the listed upstream regressions.
- **Git hygiene.** Shared branch with a concurrent session: `git status --short` first, commit with **explicit pathspec only**, never `git add -A`/`.`/bare `-a`.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests from repo root `G:\guanlan-v2` with `pytest`.

## Design Decisions (frozen for this plan; each is testable, none is silent)

| # | Decision | Rationale / authority |
|---|---|---|
| D1 | The bootstrap graph is a **versioned PRESET** (`source=PlanSource.PRESET`, `phase="bootstrap"`); `PlanSource.BOOTSTRAP` stays dormant and rejected | Controller ruling (fixed versioned preset, AUTO still rejected, approval per request) |
| D2 | BOOTSTRAP unlock = a **distinct `StaticRuntimeProfile` identity** (`profile_id="bootstrap-runtime"`, `profile_version="1"`), exported by Phase 5; Phase 2 static-runtime v1 constant/digest/golden untouched (static-runtime v2 stays reserved for Phase 8); delta = exactly one admission widening | Controller ruling + P2:427/1036 ("remains a Phase 5 runtime profile"); clause C1 covers implemented mechanics |
| D3 | "N 交易日后" authority = **observed PIT trade-date list of the benchmark series**, packaged as a Phase 3 `TradingCalendar` implementation; never `np.busday`/`pd.bdate_range` | No shared trading-calendar module exists (grounding map §4); the bar-count/date-list-position idiom is the reviewed in-repo precedent |
| D4 | "走 run_graph/PIT 口径" = pure deterministic compute over PIT-windowed inputs from the same provider/regen artifacts, bit-replayable by digest; **no canvas node registration** in this phase | `workflow/executor.py` is frozen (`_DISPATCH` edit would violate the zero-change rule); a canvas node is a later reviewed 薄扩展 |
| D5 | Lane 0 → 温度护盾 "显式上游" = deterministic projection + bit-parity tests; the shield's constants, coefficients and cache-read wiring stay untouched; actual rewiring deferred to the Lane D phase | User-frozen 温度系数/冰点阈值 25 + gate constants (market_temp.py:49-57) |
| D6 | Unknown/degraded honesty of the ContextSnapshot is carried by `BootstrapContextManifest` payload badges + `None` report refs, **not** by extending the Phase 1 `ContextSnapshot` schema | Phase 1 owns `ContextSnapshot`; consume-don't-fork |
| D7 | Experience events append to a dedicated cross-run stream `EXPERIENCE_STREAM_ID="experience.lane0.v1"` (partition `main`), correlation-linked to producing runs | Cases outlive single runs; the Phase 2 dual-cursor journal is reused unmodified; clause C2 covers the implemented store's stream policy |
| D8 | 炸板率 = `break_rate = zb/(zt+zb)` (spec §5 定义); `break_ratio` 开板率 and `promotion_rate` are distinct 口径 and never substituted; 晋级率 reuses the implemented `promotion_rate` numerator (今日 `limit_days>=2`) until a pool history enables 严格首板口径 | market_tape.py:86-98 three-口径 coexistence; 2026-07-15 连板 bugfix is normative for `max_streak` |
| D9 | Units: `main_net` 元→亿 at the loader (market_temp.py:125 conversion normative); `north_net` stays 亿 (market_tape.py:101) | Unit discipline for 资金 factors |
| D10 | v1 realized-heat label is `None` with a reason (no realized-heat definition yet); grader thresholds are golden-frozen provisionals, tuned later only via Phase 4 `run_optimize` + sealed holdout | Spec §5 参数不拍脑袋 closed loop; spec §13 经验库标签阈值 挂账 |
| D11 | Legacy migration adapters stay untouched (`migrate_rotation_stage` remains UNMAPPABLE); Lane 0 emits native reports, no legacy collapse | Collapsing 冰点/逼空/发酵… into `RotationStage` needs an approved policy that does not exist |
| D12 | Seeded-case judgments come from a closed deterministic proxy rule at LOW confidence, marked by `links`; the LLM 打标+人审 alternative from spec §6.4 is not exercised in v1 | Determinism + zero-LLM cold start; spec allows either |

---

## Task 0: Upstream handoff gate (mandatory before Task 1)

Phase 5 work starts only after the Phase 1 Amendment 1, Phase 2, Phase 3 (Tasks 0–9 incl. memory facade) and Phase 4 Exit Gates pass. Add `tests/orchestration/test_phase5_handoff.py` as an executable consumer test rather than copying upstream assertions.

**Files:**
- Create: `tests/orchestration/test_phase5_handoff.py`

- [ ] **Step 1: Write the executable consumer gate**

The handoff test must prove:

1. the amended Phase 1 golden (`tests/orchestration/golden/schema_manifest_v1.json`, 11 entries) and digest vectors pass; `TypedPayloadRef`, `InputArtifactBinding`, `ContextRuntimeRequirements` resolve from Phase 1 modules; `ContextSnapshot` carries `memory_snapshot_ref`/`memory_selection_ref` as `TypedPayloadRef` and `runtime_requirements_ref=None` for the canonical empty-memory pair;
2. `EventType` already contains `CASE_CREATED="CaseCreated"`, `CASE_MATURED="CaseMatured"`, `CASE_REVIEWED="CaseReviewed"` (events.py:119-121) **and** the Phase 4 Trial/Holdout additions; Phase 5 adds no event type;
3. Phase 2 exports `BudgetLedger`, `EventStore`/`PayloadStore`/`RuntimeUnitOfWork`/`IdempotencyConflict`, `CatalogRuntime`, `PlanAdmissionService`, `run_plan`, `StaticRuntimeProfile` and `check_runtime_support`; the Phase 2 static profile v1 value still **rejects** a `phase="bootstrap"` / no-ContextSnapshot draft before any budget reservation (this is the fact Task 8 lawfully changes only via a *distinct* profile identity);
4. Phase 3 exports `FutureDataRefused`, `MissingAvailabilityRefused`, `PitGuard`, the `TradingCalendar` protocol + `TradingCalendarResolver`, `RenderedDataBlock`, and the memory facade (`MemoryProposal*`, `AdminReviewVerifier`); `PHASE3_FULL_REGISTRY_DIGEST`/`PHASE3_FULL_CATALOG_DIGEST` verify;
5. Phase 4 exports `PHASE4_REGISTRY_DIGEST` + `build_phase4_registry(...)` and `PHASE4_CATALOG_DIGEST` + `build_phase4_catalog_snapshot(...)` (per the frozen chain-naming convention), `TrialLedger`, `StudySpec`, `run_optimize`, `finalize_candidate`, and the `ExperimentStatus.WAITING_FOR_MATURITY` resume semantics (`resume_after`/`wakeup_key`) Phase 5's grader interoperates with; the Phase 2 three-worker pilot fixtures used for the Task 8 profile differential resolve and admit;
6. `tests/orchestration/test_contract_completeness.py` still guards `MarketFactorValue`, `MarketFactorReport`, `RegimeReport`, `RealizedRegime`, `RotationReport` as **absent** deferred payloads (its `DEFERRED_PHASE_PAYLOADS` Phase-5 block, tests lines 67-73) — Task 9 flips these guards from absence to presence-in-phase5-registry;
7. frozen read-only surfaces are named for the scope-protection audit: `guanlan_v2/workflow/executor.py`, `guanlan_v2/screen/market_temp.py`, `guanlan_v2/datafeed/market_tape.py`, `guanlan_v2/macro/astock.py` — non-modification is proven at exit by a **pathspec audit over the Phase 5 commit range** (no Phase 5 commit touches these paths), never by byte pins (concurrent sessions lawfully edit these hot files on the shared branch); the behavior-parity tests (shield projection, 口径/unit pins) are the real check;
8. the shield constants the parity work depends on are asserted at their sources: risk_off ≤25 / ≤-300亿, overheat ≥85 & break_rate ≥0.35 (`market_temp.py:49-57`), and `market_tape` derived keys `zt_count/zb_count/max_streak/break_ratio/break_rate/promotion_rate/ladder/north_net/north_scope/board_date/board_backfilled`;
9. no Phase 5 source/test path overwrites Phase 1–4 sources, tests or golden files.

**Task 0 correction clauses** (binding for every later task): if an exact field, builder, module path or constant name differs in the implemented upstream public API, update this plan to that reviewed API before writing Phase 5 code; do not invent an adapter with parallel semantics. Specifically:

- **C1 (profile mechanics):** the bootstrap profile is a **distinct profile identity** — `profile_id="bootstrap-runtime"`, `profile_version="1"` — minted on the same `StaticRuntimeProfile` schema, never a forked schema (Phase 2's static-runtime v1 untouched; static-runtime `"2"` stays reserved for Phase 8); if the implemented `StaticRuntimeProfile@1` pins `profile_id` to a Literal or expresses features other than as data fields, widen that mechanism additively with the static-runtime v1 constant/digest/golden bit-unchanged; if `check_runtime_support` cannot express bootstrap admission purely from profile data, extend `runtime_support.py` additively behind the new profile digest with static-runtime-v1 behavior bit-unchanged.
- **C2 (experience stream identity):** if the implemented Phase 2 `EventStore` constrains `run_id` to admitted runs, register the reviewed experience stream through the mechanism the implemented store provides instead of the well-known `EXPERIENCE_STREAM_ID`; the append-only/idempotency/PIT semantics of Task 4 are invariant either way.
- **C3 (preset attestation):** if implemented Phase 1/2 validation demands a `StaticLegacyPlanAttestation` for every PRESET-source draft (not only `compat.*` workers), route `build_bootstrap_plan_draft` through the reviewed preset path rather than weakening validation.
- **C4 (completeness firewall shape):** flip/extend `test_contract_completeness.py` exactly the way Phase 4 established for Trial events (absence → "exists and belongs to the phase registry"; module enumeration extended, Phase 1 golden untouched); adopt whatever fixture shape Phase 4 left behind.
- **C5 (Phase 4 maturity ABI):** if `WAITING_FOR_MATURITY` resume carriers have different reviewed names/fields in implemented Phase 4, Task 6 adopts them verbatim.

- [ ] **Step 2: Freeze the reviewed upstream evidence in the fixture**

Record only exact digests (`PHASE3_FULL_*`, `PHASE4_*`, Phase 1 registry digest) and exported symbol signatures; never local paths or mutable singleton identities.

- [ ] **Step 3: Run the upstream suites and the frozen gate**

Run from a repository state in which no Phase 5 module exists yet: `pytest tests/orchestration -v`. Also run the focused upstream anchors the later tasks lean on hardest: `pytest tests/orchestration/test_contract_completeness.py tests/orchestration/test_registry_population.py tests/orchestration/test_events.py -v`.

Expected: every Phase 1–4 test plus `test_phase5_handoff.py` PASS after the reviewed evidence is recorded. Any failure or fixture drift blocks Task 1; do not update expected digests from test code.

- [ ] **Step 4: Commit the gate independently**

```bash
git add tests/orchestration/test_phase5_handoff.py
git commit -m "test(orchestration): gate phase5 on phase1-4 contracts"
```

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/market/__init__.py` | package marker, re-exports |
| `guanlan_v2/orchestration/market/factors.py` | `MarketFactorPoint/Value/Definition/SetSpec/Report`, `RegimeReport`, `RotationReport`, pure factor compute core, PIT input loaders, market-temp upstream projection, deterministic worker handler |
| `guanlan_v2/orchestration/memory/experience.py` | `RegimeCase/CaseMatured/CaseReviewed/RealizedRegime`, `RegimeGraderSpec`, `ExperienceScalerSnapshot`, `ExperienceQuery/Neighbour/Selection`, event-folded `ExperienceLog`/views, PIT scaler + numeric NN retrieval, `ObservedTradeDateCalendar`, delayed grader, cold-start seeder |
| `guanlan_v2/orchestration/bootstrap.py` | `BootstrapPlan`, `BootstrapContextManifest`, `ExperiencePrefetchBinding`, bootstrap draft builder, bootstrap/main `RunContext` builders, ContextSnapshot assembly, case append hook, `BOOTSTRAP_RUNTIME_PROFILE`, Phase 5 registry/catalog chain |
| `config/orchestration/materials/lane0/` | Lane 0 prompt/SKILL/guardrail/handler physical materials (paths never enter Plan) |
| `tests/orchestration/golden/market_factor_set_v1.json` | reviewed v1 factor-set definition (ids/params/windows/min history) |
| `tests/orchestration/golden/regime_grader_policy_v1.json` | reviewed v1 grader thresholds/horizon/benchmark |
| `tests/orchestration/golden/phase5_schema_manifest_v1.json` | Phase 5 cumulative registry golden |
| `tests/orchestration/golden/phase5_catalog_manifest_v1.json` | Phase 5 cumulative catalog golden |
| `tests/orchestration/test_phase5_handoff.py` | executable Phase 1–4 → 5 ABI/golden gate |
| `tests/orchestration/test_market_factor_contracts.py` | Task 1 contracts |
| `tests/orchestration/test_lane0_reports.py` | Task 2 `RegimeReport`/`RotationReport` matrices |
| `tests/orchestration/test_market_factor_compute.py` | Task 3 compute/PIT/coverage/parity |
| `tests/orchestration/test_factor_report_render.py` | Task 3b ①§4 rendering contract |
| `tests/orchestration/test_experience_contracts.py` | Task 4 case contracts |
| `tests/orchestration/test_experience_store.py` | Task 4 event-folded views + PIT visibility |
| `tests/orchestration/test_experience_retrieval.py` | Task 5 scaler + NN retrieval |
| `tests/orchestration/test_regime_grader.py` | Task 6 grader/calendar/maturity |
| `tests/orchestration/test_experience_seed.py` | Task 7 seeding + future-case invariance |
| `tests/orchestration/test_lane0_catalog.py` | Task 8 WorkerSpecs/materials/bridge |
| `tests/orchestration/test_bootstrap_profile.py` | Task 8 profile admission matrix |
| `tests/orchestration/test_phase5_registry.py` | Task 9 chain + goldens + guard flips |
| `tests/orchestration/test_bootstrap_plan.py` | Task 10 draft builder/validation |
| `tests/orchestration/test_bootstrap_e2e.py` | Task 10 end-to-end bootstrap → ContextSnapshot → main RunContext |

### Registered-schema quick reference (all `@1`; frozen names per controller)

| Schema | Module | Primary consumer |
|---|---|---|
| `MarketFactorValue` / `MarketFactorSetSpec` / `MarketFactorReport` | `market/factors.py` | `market.factor` output; regime/rotation input; experience feature source |
| `RegimeReport` / `RotationReport` | `market/factors.py` | Lane 0 sinks; `RegimeCase.judgment`; ContextSnapshot manifest |
| `RegimeCase` / `CaseMatured` / `CaseReviewed` / `RealizedRegime` / `RegimeGraderSpec` | `memory/experience.py` | experience stream payloads; Phase 4 validation |
| `ExperienceScalerSnapshot` / `ExperienceQuery` / `ExperienceSelection` | `memory/experience.py` | retrieval capability I/O + evidence |
| `ExperiencePrefetchBinding` / `BootstrapPlan` / `BootstrapContextManifest` | `bootstrap.py` | bridge config; preset record; snapshot honesty carrier |

---

## Task 1: Market-factor contracts (`market/factors.py`, contracts half)

**Files:**
- Create: `guanlan_v2/orchestration/market/__init__.py`
- Create: `guanlan_v2/orchestration/market/factors.py`
- Create: `tests/orchestration/golden/market_factor_set_v1.json`
- Test: `tests/orchestration/test_market_factor_contracts.py`

**Consumes:** Phase 1 `DigestModel`, strict types (`DigestHex/UtcDateTime/FiniteFloat/NonNegativeInt/PositiveInt/NonEmptyStr`), `LogicalId`.

**Produces:**

- `class MarketFactorPoint(DigestModel)` (nested value object, no `schema_version`): `date: NonEmptyStr` (ISO `YYYY-MM-DD`, regex-validated), `value: FiniteFloat`, `aux: dict[NonEmptyStr, FiniteFloat] = {}` (per-date derived values — MA/EMA/slope/z — keys restricted to the owning definition's `aux_keys`).
- `class MarketFactorDefinition(DigestModel)` (nested): `factor_id: LogicalId`, `definition_version: NonEmptyStr`, `params: dict[NonEmptyStr, FiniteFloat | NonEmptyStr]`, `required_inputs: tuple[LogicalId, ...]` (canonically sorted, dup-free), `min_history_sessions: PositiveInt`, `aux_keys: tuple[NonEmptyStr, ...]` (sorted, dup-free).
- `class MarketFactorSetSpec(DigestModel)` (registered `MarketFactorSetSpec@1`): `schema_version: Literal["1"] = "1"`, `factor_set_version: NonEmptyStr`, `feature_schema_version: NonEmptyStr`, `universe: NonEmptyStr`, `frequency: Literal["day"] = "day"`, `definitions: tuple[MarketFactorDefinition, ...]` (sorted by `factor_id`, dup-free, non-empty), `content_digest: DigestHex` (SELF_DIGEST).
- **The reviewed v1 factor set** (hand-frozen in `market_factor_set_v1.json`; `factor_set_version="mfs-v1"`, `feature_schema_version="mfs-v1"`, `universe="all_a"`) — exactly **19 factor ids** mapping deliverable ①(`docs/superpowers/specs/2026-07-17-market-factor-report-schema.md`)§3's **17-row battery** (AMEND-2 四轮动扩容 included). Ruling R3: a ① table row is a factor **group**; the implemented granularity is one `factor_id` per series, so the two composite rows split — `breadth.limit_strength` → `breadth.limit_up_ema` + `breadth.break_rate`; `breadth.ladder` → `breadth.ladder_height` + `breadth.promotion_rate` — and the Task 3b rendering groups by `family`. ids/prefixes/params follow ①§3 (`breadth.nhnl`, `rot.*`, `flow.northbound`, `flow.main_pct`, `vol.rv`, `val.pct`); where ① marks a parameter ⏳/"待设计", the v1 value below is the reviewed provisional (tuning belongs to Phase 4 `run_optimize` over matured cases — "参数不拍脑袋" closed loop, out of scope here). The final id list (the R3-mandated清单):

| factor_id | ①§3 row | v1 params | aux_keys | required_inputs | min_history_sessions |
|---|---|---|---|---|---|
| `breadth.ad_ratio` | 涨跌家数比 | `ma_short=5, ma_long=20, slope_window=20` (①: 20日斜率) | `ma5, ma20, slope` | `updown` | 25 |
| `breadth.nhnl` | 新高新低差 | `window_short=20, window_long=60` | `nhnl20, nhnl60` | `closes_panel` | 61 |
| `breadth.limit_up_ema` | 涨停强度(`limit_strength` 拆分 · EMA 序列, R3) | `ema_span=3` | `ema3` | `limit_up_total` | 4 |
| `breadth.break_rate` | 涨停强度(`limit_strength` 拆分 · 炸板率序列, R3) | `-` (definitional: `zb/(zt+zb)`) | `-` | `break_counts` | 5 |
| `breadth.ladder_height` | 连板梯队(`ladder` 拆分 · 最高连板, R3) | `-` (最高连板) | `-` | `board_pools` | 5 |
| `breadth.promotion_rate` | 连板梯队(`ladder` 拆分 · 晋级率, R3/R4) | `numerator="limit_days>=2"` (今日≥2连板家数/昨全涨停池 — the implemented `market_tape.promotion_rate` 口径, reused as-is). Ruling R4: until the pool archive suffices, this factor carries a standing **DEGRADED 口径注记** (`reason` names the divergence from ①'s 严格首板晋级率); switching to the 首板 numerator once archive data suffices is a **new `definition_version`**, never a silent redefinition | `-` | `board_pools` | 5 |
| `breadth.divergence` | 广度背离 ★ | `ret_window=20, z_window=250, alert=1.5 ⏳` (①: z 用 250 日窗; `alert` is params metadata only) | `z_index, z_breadth` | `updown, closes_index` | 271 |
| `flow.northbound` | 北向趋势 | `cum_short=5, cum_long=20, pct_window=250` | `cum5, cum20, slope, pct250` | `north_net` | 250 |
| `flow.main_pct` | 主力净额分位 | `pct_window=250` | `pct250` | `main_net` | 250 |
| `rot.hhi` | 板块资金集中度 | `topk=3` | `hhi, top3_share` | `sector_flows` | 20 |
| `rot.diffusion` | 主线扩散度 | `topk=3` | `-` | `sector_flows, concept_membership, closes_panel` | 20 |
| `rot.dispersion` | 行业动量离散度 | `dispersion="cross_sectional_std"` (行业日收益截面 std; fid=f3 口径, 07-15 修复 normative) | `-` | `industry_returns` | 5 |
| `rot.ladder_theme` | 题材梯队占据度 (AMEND-2) | `topk=3` | `-` | `board_pools, limit_reasons` | 5 |
| `rot.leader_persist` | 主线龙头稳定性 (AMEND-2) | `win=5` | `-` | `sector_leaders` | 6 |
| `rot.flow_streak` | 主线连续净流入天数 (AMEND-2) | `-` | `-` | `sector_flows` | 5 |
| `rot.theme_burst` | 新题材首日爆发 (AMEND-2) | `-` | `-` | `universe_versions, board_pools` | 2 |
| `vol.rv` | 已实现波动率 | `window_short=5, window_long=20` (①: RV20 主值 + 短长比 RV5/RV20) | `rv_ratio` | `closes_index` | 21 |
| `val.pct` | 估值分位 | `pct_years=5` | `pe_pct, pb_pct` | `index_valuation` | 1150 |
| `temp.astock` | 打板温度 | `-` (已有 market_tape 温度公式产出的历史点) | `-` | `astock_temp` | 20 |

Expected v1 production outcome (documented, not hard-coded — the classification is data-probed at run time per Task 3): computable today — `breadth.ad_ratio`, `breadth.nhnl`, `breadth.limit_up_ema`, `breadth.divergence`, `vol.rv`; short-history/accreting — `temp.astock` (macro snapshots jsonl accretes since 2026-07-06); honest UNAVAILABLE until the ①§5 snapshot-archive deliverable lands — `breadth.break_rate`, `breadth.ladder_height`, `breadth.promotion_rate` (board-pool history absent; `market_tape` is snapshot-only), `flow.northbound` (no daily store; 北向 2024-08 停披), `flow.main_pct`, `rot.hhi`, `rot.diffusion`, `rot.dispersion`, `rot.ladder_theme`, `rot.leader_persist`, `rot.flow_streak` (the six archive-dependent rot factors), `rot.theme_burst` (universe-version-diff 起点), `val.pct` (ruling R5 — no verified index-percentile upstream; the cited `baidu_valuation_percentile` is per-stock only). Archive prerequisite, stated explicitly per ①§5: the six archive-dependent rot factors plus 炸板率/北向/主力分位 historical series depend on the **market_tape/fundflow snapshot-archive small deliverable** (the named owner; 建议与事件库小 phase 同批 — the macro monthly-rotation `72573b8` pattern is the in-repo precedent). Once archiving starts, these factors surface as short-series `DEGRADED` with `first_date` displayed — never backfilled, never fabricated; whatever lacks strict-replay coverage stays `UNAVAILABLE` (the spec §13 "Lane 0 数据覆盖" open item resolved by construction).
- `class MarketFactorValue(DigestModel)` (registered `MarketFactorValue@1`; field shape aligned to ①§2 `FactorSeries` — ① is the authoritative field list): `schema_version: Literal["1"] = "1"`, `factor_id: LogicalId`, `definition_version: NonEmptyStr`, `family: Literal["breadth","flow","rot","vol","val","temp"]` (①), `value: FiniteFloat | None` (spec field: the latest point's value; `None` iff `status == "UNAVAILABLE"`), `params: dict[NonEmptyStr, FiniteFloat | NonEmptyStr]`, `universe: NonEmptyStr`, `frequency: Literal["day"] = "day"`, `effective_at: UtcDateTime` (spec field: the newest contributing observation's session time; equals `available_at` when unavailable), `available_at: UtcDateTime`, `status: Literal["OK", "DEGRADED", "UNAVAILABLE"]` (①§2 three states — `DEGRADED` = short coverage / archive-young series with `reason` required; the earlier two-state "no third status" wording is superseded by ①), `coverage: FiniteFloat` (ge=0, le=1), `missing_policy: NonEmptyStr` (①: one-sentence missing-data semantics for this factor — no longer a closed Literal), `series: tuple[MarketFactorPoint, ...] = ()` (①/D1: ≤60 points, renamed from `points`; strictly increasing dates), `summary: FactorSummary | None` (nested `class FactorSummary(DigestModel)`: `latest: FiniteFloat`, `chg_5d: FiniteFloat | None`, `chg_20d: FiniteFloat | None`, `pct_250d: FiniteFloat | None` — `pct_250d` is `None` when 250-session coverage is insufficient, never hard-computed), `n_days: NonNegativeInt` (①), `first_date: NonEmptyStr | None` (①: the series' true archive start, honesty-displayed), `provenance: <sources + snapshot_refs>` (①: upstream data surfaces + snapshot refs; concrete nested carrier finalized at implementation, classified reviewed-internal), `reason: NonEmptyStr | None = None` (renamed from `unavailable_reason`; required iff `status != "OK"`), `content_digest: DigestHex` (SELF_DIGEST). Status matrix: `"OK"` ⇒ non-empty series, `value` equals the last point's value, `coverage == 1.0`, `reason` forbidden; `"DEGRADED"` ⇒ non-empty series, `0 < coverage <= 1` (short window / young archive), `reason` **required**; `"UNAVAILABLE"` ⇒ empty series, `value is None`, `summary is None`, `coverage == 0.0`, `reason` required.
- `class MarketFactorReport(DigestModel)` (registered `MarketFactorReport@1`; report envelope aligned to ①§1): `schema_version: Literal["1"] = "1"`, `as_of: UtcDateTime`, `clock_mode: Literal["eod", "intraday"]` (①), `universe_registry_version: NonEmptyStr` (①/AMEND-1: 题材/行业分类学版本 into provenance), `factor_set_version: NonEmptyStr`, `battery_digest: DigestHex` (① rename of `factor_set_digest`; still the bound `MarketFactorSetSpec.content_digest`), `feature_schema_version: NonEmptyStr`, `universe: NonEmptyStr`, `values: tuple[MarketFactorValue, ...]` (spec field name; sorted by `factor_id`; ids exactly equal the bound set's ids), `data_snapshot_hash: DigestHex` (spec field: digest of the exact PIT input snapshot the report was computed from — bound by Task 3's compute core to the windowed `MarketFactorInputs` content digest), `coverage: FiniteFloat` (ge=0, le=1; spec field: report-level coverage = mean of per-factor `coverage` over the bound set — formula pinned by tests), `coverage_summary: CoverageSummary` (①: nested reviewed-internal `{n_ok, n_degraded, n_unavailable}`, validator-tied to the `values` statuses), `feature_vector: dict[LogicalId, FiniteFloat]` (latest point per `OK`/`DEGRADED` factor — the exact dict the experience layer consumes; plan-supplement field retained for Tasks 4/5), `feature_coverage: dict[LogicalId, FiniteFloat]`, `missing_features: tuple[LogicalId, ...]` (sorted; exactly the `UNAVAILABLE` factor ids), `unavailable_factor_ids: tuple[LogicalId, ...]` (== `missing_features`; kept for report readers), `badges: tuple[NonEmptyStr, ...] = ()`, `content_digest: DigestHex` (SELF_DIGEST). Cross-consistency validators tie `feature_vector`/`feature_coverage`/`missing_features`/`coverage`/`coverage_summary` to the `values` tuple bit-for-bit.

**Required invariants:**

1. all models strict/frozen/extra-forbid, semantic digests move on any factor value/param change and do not move on payload relocation;
2. spec line-378 + §8 fields (`factor_id/definition_version/value/params/universe/frequency/effective_at/available_at/coverage/status/missing_policy/content_digest`) are all present on `MarketFactorValue` under exactly those names;
3. an `UNAVAILABLE` factor can never carry series points, a non-None `value`/`summary` or a nonzero coverage (zero-fill structurally impossible);
4. `MarketFactorReport.feature_vector` never contains a key listed in `missing_features` (no fabricated feature);
5. the golden factor set reproduces its own `content_digest` and is never auto-regenerated.

- [ ] **Step 1: Write failing contract tests**

Matrix:

- construction/frozen/extra-forbid for all five models; strict types reject bool-as-float and naive datetimes;
- `MarketFactorPoint` date regex; non-increasing point dates rejected; aux keys outside the definition's `aux_keys` rejected at report assembly;
- `MarketFactorValue` status matrix (①§2 three states) — each illegal combination (OK+empty series, OK+`value=None`, OK+coverage<1, OK with `reason`, DEGRADED+empty series, DEGRADED without `reason`, UNAVAILABLE with series points, UNAVAILABLE with non-None `value`/`summary`, UNAVAILABLE+coverage>0, UNAVAILABLE without `reason`, any string outside the closed `OK|DEGRADED|UNAVAILABLE` literal) rejected; each legal combination accepted; `summary.pct_250d=None` on insufficient 250-session coverage accepted (never hard-computed);
- spec field presence (line-378 + §8 names, incl. `value`/`effective_at`/`status`) under exact names (one reflective test over `model_fields`);
- `MarketFactorSetSpec` sorted/dup-free/non-empty definitions; golden digest reproduction (load `market_factor_set_v1.json`, rebuild, compare `content_digest`); the golden's 19 factor ids asserted verbatim (17 ① rows, R3 splits);
- `MarketFactorReport` cross-consistency both directions: a `feature_vector` key for an `UNAVAILABLE` factor rejected; an `OK`/`DEGRADED` factor absent from `feature_vector` rejected; `missing_features` ≠ `UNAVAILABLE` ids rejected; ids not matching the bound set rejected; report `coverage` deviating from the pinned mean-of-per-factor-coverage formula rejected; `coverage_summary` counts deviating from the per-status tally rejected;
- semantic-digest differentials (param change moves, relocation does not).

Run: `pytest tests/orchestration/test_market_factor_contracts.py -v`

Expected: FAIL on missing module/classes (import of `guanlan_v2.orchestration.market.factors` raises) — record; collection errors elsewhere do not count.

- [ ] **Step 2: Implement contracts + hand-freeze the golden**

Record the golden digest from a one-off verification run, review, freeze — never write it from test code.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_market_factor_contracts.py tests/orchestration/test_contract_completeness.py -v` — the completeness firewall stays green because Task 1 also registers the new module in the reviewed enumeration **without** flipping deferred-payload guards yet (the models exist but are asserted only through the Phase 5 classification introduced fully in Task 9; if the implemented firewall cannot represent this intermediate state, do the Task 9 guard flip for exactly `MarketFactorValue`/`MarketFactorReport` in this commit instead, keeping one reviewed mechanism — clause C4).

Expected: PASS.

```bash
git add guanlan_v2/orchestration/market/__init__.py guanlan_v2/orchestration/market/factors.py tests/orchestration/golden/market_factor_set_v1.json tests/orchestration/test_market_factor_contracts.py tests/orchestration/test_contract_completeness.py
git commit -m "feat(orchestration): market factor contracts + frozen v1 factor set"
```

---

## Task 2: `RegimeReport` + `RotationReport` (Lane 0 LLM output contracts)

**Files:**
- Modify: `guanlan_v2/orchestration/market/factors.py`
- Test: `tests/orchestration/test_lane0_reports.py`

**Consumes:** Phase 1 `Confidence`, `RotationStage` (enums.py:31 — `START="启动"`, `SPREAD="扩散"`, `DIVERGENCE="分化"`, `EBB="退潮"`, `UNKNOWN="unknown"`), Task 1 types, and deliverable ④§1 (`docs/superpowers/specs/2026-07-17-lane0-regime-rotation-skills.md`) as the authoritative output field list.

**Produces:**

- Phase-5-local axis enums (④§1; defined in `market/factors.py` — Phase 1 `enums.py` untouched; ruling R6 fixes the **Chinese trend values**, shared with the grader's realized vocabulary so 判读 vs realized calibration uses one 词表): `class TrendState(str, Enum)`: `BULL="牛"`, `BEAR="熊"`, `RANGE="震荡"`, `UNKNOWN="unknown"`; `class RiskState(str, Enum)`: `RISK_ON="risk_on"`, `RISK_OFF="risk_off"`, `NEUTRAL="neutral"`, `UNKNOWN="unknown"`; `class HeatState(str, Enum)`: `NORMAL="normal"`, `OVERHEAT="overheat"`, `UNKNOWN="unknown"`. Tolerance/gating constants `AXIS_SUM_TOLERANCE = 1e-8`, `UNKNOWN_ATTENTION_THRESHOLD = 0.25`, `HIGH_CONFIDENCE_UNKNOWN_MAX = 0.10`.
- `class EvidenceAnchor(ContractModel)` (nested; ④§1): `factor_id: NonEmptyStr` (must exist in the bound market_factor_report — machine-checked downstream, Task 10 e2e), `value: FiniteFloat` (逐字 from the Task 3b rendered block), `reading: NonEmptyStr` (one-sentence interpretation).
- `class RegimeReport(DigestModel)` (registered `RegimeReport@1`; field shape aligned to ④§1): `schema_version: Literal["1"] = "1"`, `as_of: UtcDateTime`, `factor_report_digest: DigestHex` (④: binds the exact `MarketFactorReport` read — input auditability), `trend: TrendState`, `risk_state: RiskState`, `heat_state: HeatState` (modal fields — plan supplement retained; ④ leaves字段 for implementation), `trend_probabilities: dict[TrendState, FiniteFloat]`, `risk_probabilities: dict[RiskState, FiniteFloat]`, `heat_probabilities: dict[HeatState, FiniteFloat]` (④ `*_probs` closed maps; D3), `confidence: Confidence` (④ — the **only** confidence carrier: the earlier `confidence_score` 0-1 float is **deleted** per ④/AMEND-7-3, LLM 自报浮点饱和), `evidence: tuple[EvidenceAnchor, ...]` (④: ≥1 — every load-bearing claim anchored), `conflicts: tuple[NonEmptyStr, ...] = ()` (④: explicit conflict list, never averaged away), `analog_case_ids: tuple[NonEmptyStr, ...] = ()` (④: experience analogs; cold start = empty), `drivers: tuple[NonEmptyStr, ...]` (spec field; sorted, dup-free), `evidence_factor_ids: tuple[LogicalId, ...]` (plan supplement; sorted, dup-free), `narrative: NonEmptyStr`, `unknown_reason: NonEmptyStr | None = None`, `content_digest: DigestHex` (SELF_DIGEST). Validators (plan modal/unknown gating retained as supplement — ④ "字段供实现期定稿"):
  1. each axis dict carries **exactly** its enum's member set (axis-specific labels only — a trend label can never appear on the heat axis by construction);
  2. every probability ∈ [0,1] and each axis sums to `1 ± AXIS_SUM_TOLERANCE`;
  3. each modal field (`trend`/`risk_state`/`heat_state`) equals its axis's highest-probability label (ties broken by the frozen enum declaration order);
  4. `unknown_reason` is **required** iff any axis has `unknown ≥ UNKNOWN_ATTENTION_THRESHOLD`, and **forbidden** otherwise (unknown must be coverage/evidence-driven, named, and cannot be decorative);
  5. `confidence == HIGH` requires `unknown ≤ HIGH_CONFIDENCE_UNKNOWN_MAX` on every axis, and any axis whose modal field is `unknown` forces `confidence == LOW` (a high-confidence narrative can never coexist with modal unknown);
  6. `evidence` is non-empty (④ ≥1) and `evidence_factor_ids` equals the sorted distinct anchor `factor_id`s.
- `class MainlineRead(ContractModel)` (nested; ④§1 — replaces the earlier `RotationMainline`): `name: NonEmptyStr` (主线名 from the universe taxonomy), `universe_key: NonEmptyStr` (④: key within `universe_registry_version`), `stage: RotationStage` (④/AMEND-4 §4.2: stage is **per mainline**; frozen enum, `unknown` lawful), `strength: FiniteFloat` (ge=0, le=10 — ④ [0,10], not [0,1]), `persistence: NonEmptyStr` (④: one-sentence persistence evidence — replaces `persistence_sessions`), `evidence: tuple[EvidenceAnchor, ...]` (per-mainline anchors), `chain_nodes: tuple[NonEmptyStr, ...] = ()` (④ when-supplied: empty without the industry-chain block, never improvised).
- `class RotationReport(DigestModel)` (registered `RotationReport@1`; field shape aligned to ④§1): `schema_version: Literal["1"] = "1"`, `as_of: UtcDateTime`, `factor_report_digest: DigestHex`, `mainlines: tuple[MainlineRead, ...]` (④: tuple order **is** the ranking — no separate rank field; names dup-free; **may be empty — 无主线=诚实合法**, no stage precondition: the earlier report-level `stage` field is deleted, staging lives on each mainline), `confidence: Confidence`, `conflicts: tuple[NonEmptyStr, ...] = ()`, `analog_case_ids: tuple[NonEmptyStr, ...] = ()`, `narrative: NonEmptyStr`, `evidence_factor_ids: tuple[LogicalId, ...]`, `unknown_reason: NonEmptyStr | None = None` (plan supplement: required iff `mainlines` is empty — name the driver, themeless tape or archive-young factors; forbidden otherwise), `content_digest: DigestHex` (SELF_DIGEST).
- Exported `SchemaRef` constants for downstream pinning (bridge config, manifest validators, worker output bindings): `MARKET_FACTOR_REPORT_SCHEMA_REF = SchemaRef(name="MarketFactorReport", version="1")`, `REGIME_REPORT_SCHEMA_REF = SchemaRef(name="RegimeReport", version="1")`, `ROTATION_REPORT_SCHEMA_REF = SchemaRef(name="RotationReport", version="1")` — one definition each, imported everywhere else.

**Required invariants:**

1. neither schema name appears in Phase 1 `_DECISION_CLASS_SCHEMAS` — Lane 0 outputs are never decision-class;
2. probability axes are closed and non-coercing (strict floats; bool rejected);
3. legacy `LegacyMarketCycleStage` is **not** collapsed into `RotationStage` here — `migration.migrate_rotation_stage` stays UNMAPPABLE and untouched (explicit non-goal).

- [ ] **Step 1: Write failing report tests**

Matrix:

- axis key sets: a trend label injected into `heat_probabilities` rejected; a missing label rejected; an extra key rejected (extra-forbid + Literal keys);
- sums: off by `2e-8` rejected on each axis independently; exactly `1 ± 1e-8` accepted; negative probability and `>1` rejected;
- modal fields: a `trend`/`risk_state`/`heat_state` not equal to its axis argmax rejected; an argmax tie resolved by frozen enum declaration order accepted; Chinese `TrendState` serialization pinned (`"牛"/"熊"/"震荡"` byte-exact in canonical JSON/digest — R6);
- unknown gating: `unknown = 0.25` requires `unknown_reason`; `unknown = 0.24` with a reason rejected (forbidden-when-low); modal unknown (`0.4` vs `0.3/0.2/0.1`) with `confidence=MEDIUM` rejected, with `LOW` accepted;
- HIGH confidence: `unknown = 0.11` on any single axis rejected; `0.10` on all axes accepted;
- `RegimeReport` ④ additions: missing/malformed `factor_report_digest` rejected; empty `evidence` rejected (≥1); `evidence_factor_ids` mismatching the sorted distinct anchor ids rejected;
- `RotationReport`/`MainlineRead`: duplicate mainline names rejected; tuple order is the ranking (no rank field); per-mainline `stage` (incl. `unknown`) accepted; `strength=10.0` accepted, `10.1`/negative rejected; empty `persistence` rejected; `chain_nodes` defaults to `()`; empty `mainlines` **with** `unknown_reason` accepted (无主线=诚实), empty without reason rejected, non-empty with `unknown_reason` rejected;
- neither schema name is in Phase 1 `_DECISION_CLASS_SCHEMAS` (reflective assertion);
- digest movement per semantic field; self-digest verification on load.

Run: `pytest tests/orchestration/test_lane0_reports.py -v` — Expected: FAIL (classes missing).

- [ ] **Step 2: Implement**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane0_reports.py tests/orchestration/test_market_factor_contracts.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/market/factors.py tests/orchestration/test_lane0_reports.py
git commit -m "feat(orchestration): regime/rotation report contracts with honest unknown gating"
```

---

## Task 3: Deterministic factor compute core + PIT input loaders + shield parity

**Files:**
- Modify: `guanlan_v2/orchestration/market/factors.py`
- Test: `tests/orchestration/test_market_factor_compute.py`

**Consumes:** Task 1 contracts; Phase 3 `FutureDataRefused`; existing read-only production surfaces (cited, not modified): `guanlan_v2/strategy/compute/eqw_market.py::load_eqw_ret` (eqw daily returns), the engine provider daily panel path used by `strategy/compute/regen.py:122/149`, `guanlan_v2/macro/pulse.py` snapshots jsonl (`var/macro_pulse/snapshots.jsonl`, astock_temp accretion), `guanlan_v2/datafeed/market_tape.read_tape` (today-only snapshot, `board_date`/`board_backfilled` badges), `guanlan_v2/screen/market_temp.py::build_market_temp` (parity target only).

**Produces:**

- Internal typed input rows (unregistered, reviewed-internal): `class DailyValueRow(DigestModel)`: `date: NonEmptyStr`, `value: FiniteFloat`, `available_at: UtcDateTime`; `class UpDownRow(DigestModel)`: `date: NonEmptyStr`, `up: NonNegativeInt`, `down: NonNegativeInt`, `total: PositiveInt`, `available_at: UtcDateTime`; `class TapePoint(DigestModel)`: `date: NonEmptyStr`, `zt_count: NonNegativeInt | None`, `zb_count: NonNegativeInt | None`, `max_streak: NonNegativeInt | None`, `break_rate: FiniteFloat | None`, `promotion_rate: FiniteFloat | None`, `backfilled: bool`, `available_at: UtcDateTime`.
- `class MarketFactorInputs(DigestModel)` (internal): optional named series, one field per `required_inputs` id in the v1 factor set — `updown: tuple[UpDownRow, ...] | None`, `closes_index: tuple[DailyValueRow, ...] | None` (eqw daily return series), `closes_panel: tuple[PanelCloseRow, ...] | None` (per-stock closes for NH-NL/diffusion; `class PanelCloseRow(DigestModel)`: `date`, `code: NonEmptyStr`, `close: FiniteFloat`, `available_at`), `limit_up_total: tuple[DailyValueRow, ...] | None`, `break_counts: tuple[DailyValueRow, ...] | None` (zt/zb pair encoded as two aux-free series or a small `BreakCountRow`; v1 chooses `class BreakCountRow(DigestModel)`: `date`, `zt: NonNegativeInt`, `zb: NonNegativeInt`, `available_at`), `board_pools: tuple[BoardPoolRow, ...] | None` (`date`, `max_streak: NonNegativeInt`, `promotion_rate: FiniteFloat | None`, `available_at`), `astock_temp: tuple[DailyValueRow, ...] | None`, `north_net: tuple[DailyValueRow, ...] | None`, `main_net: tuple[DailyValueRow, ...] | None`, `sector_flows: tuple[DailyValueRow, ...] | None`, `index_valuation: tuple[DailyValueRow, ...] | None`, `concept_membership: tuple[DailyValueRow, ...] | None` (概念归属, ww_live_text 口径 — replaces the earlier `mainline_membership`), `industry_returns: tuple[DailyValueRow, ...] | None`, `limit_reasons` / `sector_leaders` / `universe_versions` (①: 涨停原因归因 / 板块领涨股 / universe 版本 diff — string-valued row carriers typed at implementation, classified reviewed-internal; all three optional, `None` in v1 production), `today_tape: TapePoint | None`. Fields for sources with **no history store in this repo** simply stay `None` in production (⇒ honest UNAVAILABLE downstream); the compute core is total over every combination.
- `class PanelAvailabilityRule(DigestModel)` (internal, versioned): `rule_version: NonEmptyStr` ("avail-v1"), `session_close_utc: NonEmptyStr` ("07:05" — 15:05 Asia/Shanghai) — the versioned assumption stamping `available_at` on daily observations; recorded into every produced `MarketFactorValue.params` as `availability_rule`.
- `def compute_market_factors(inputs: MarketFactorInputs, *, spec: MarketFactorSetSpec, as_of: UtcDateTime, clock_mode: Literal["eod", "intraday"], universe_registry_version: NonEmptyStr) -> MarketFactorReport:` — pure, deterministic, clock-free. Behavior:
  1. defensively verifies `max(available_at) <= as_of` over every supplied row; any violation raises Phase 3 `FutureDataRefused` (never silently filtered here — windowing is the loader's job, the core refuses);
  2. per definition: if any `required_inputs` member is `None` or shorter than `min_history_sessions`, emit `UNAVAILABLE` with reason naming the missing input and observed length;
  3. otherwise compute the series per the frozen v1 definitions (MA/EMA/slope/z-score/percentile helpers implemented as small pure functions in-module), full window ⇒ `OK` at `coverage == 1.0`, partial-but-≥-min ⇒ `DEGRADED` at `coverage = observed/required` (reason required — ①§2 three-state honesty: short coverage / archive-young is `DEGRADED`, never silently `OK`);
  4. `today_tape` contributes only the current-session point and only when `backfilled is False` and `date` equals the `as_of` session — otherwise it is ignored with badge `tape_backfilled_ignored` (a backfilled snapshot is never a same-day observation);
  5. assembles `feature_vector`/`feature_coverage`/`missing_features` and `coverage_summary` {n_ok, n_degraded, n_unavailable}, stamps `clock_mode`/`universe_registry_version`/`battery_digest`, `data_snapshot_hash` (the windowed `MarketFactorInputs` content digest) and report-level `coverage` (the pinned mean formula), and the report digest.
- **Per-factor computation rules (v1, pinned by tests; formulas are closed):**
  - `breadth.ad_ratio`: daily `(up − down) / total`; aux `ma5`/`ma20` simple means, `slope` = (ma5[t] − ma5[t−slope_window]) / slope_window with `slope_window=20` (①: 20日斜率, replaces the earlier 5).
  - `breadth.nhnl`: per session, count of codes at a `window`-session close high minus count at a close low, normalized by panel width; aux for both windows.
  - `breadth.limit_up_ema`: EMA(span=3) over `limit_up_total`.
  - `breadth.break_rate`: `zb / (zt + zb)` per session from `break_counts`; a `zt + zb == 0` session yields a gap (no point), never 0.0 — 空池诚实 mirrors `market_tape._derive`.
  - `breadth.ladder_height` / `breadth.promotion_rate`: pass-through of `board_pools` fields (`max_streak` from per-stock `limit_days`, never zt_stat `ct` — the 2026-07-15 连板口径 bugfix is normative); a `promotion_rate=None` session is a gap; `breadth.promotion_rate` carries the standing R4 口径 DEGRADED annotation (Task 1 table).
  - `breadth.divergence`: `z(compounded 20-session index return) − z(20-session change of ad_ratio_ma20)` with both z-scores fitted on a trailing **250-session window** (`z_window=250`, ① — replaces the earlier expanding-z20; only data at or before the point enters the fit); aux exposes both z components; the `alert=1.5 ⏳` param is metadata for readers (the factor emits the raw spread — thresholding is the LLM/consumer's judgment, not baked in).
  - `flow.northbound`: 5/20-session cumulative sums, slope of cum5, and 250-session percentile of the daily net; unit is 亿 (the `market_tape.north_net` unit) — the loader converts and records `unit="yi"` in params. sgt 护栏 semantics recorded (①): the upstream `market_tape` guard (sgt 点密度<半数 ⇒ 当日置空) is already implemented — the loader passes the guarded-empty day through as a gap, never as zero.
  - `flow.main_pct`: 250-session percentile of whole-market main net inflow; unit discipline: fundflow `market["main_net"]` is 元 — the loader divides by 1e8 and records `unit="yi"` (the market_temp.py:125 conversion is normative).
  - `rot.hhi`: top-3 share of positive sector main-net inflow (or HHI when `method="hhi"`).
  - `rot.diffusion`: fraction of member codes (概念归属 via `concept_membership`) of the top-3 net-inflow concepts with a positive daily return (①: top3 净流入概念内上涨成分占比).
  - `rot.dispersion`: cross-sectional std of industry daily returns (fid=f3 口径 — the 2026-07-15 行业排名 fix is normative).
  - `rot.ladder_theme` (AMEND-2): 最高板+梯队人数按题材分布 → top-3 题材占据度 (from `board_pools` + `limit_reasons` 涨停原因归因).
  - `rot.leader_persist` (AMEND-2): top-3 主线领涨股 5-session identity 重合率 (from `sector_leaders`).
  - `rot.flow_streak` (AMEND-2): top-3 主线连续净流入天数 (from `sector_flows` history).
  - `rot.theme_burst` (AMEND-2): 新入 universe 题材首日 — 题材内涨停数/成交占比 (from `universe_versions` diff + `board_pools`).
  - `vol.rv`: RV20 = √(Σ r²) over 20 sessions annualized (×√250); aux `rv_ratio` = **RV5/RV20** (①: 短长比, replaces the earlier rv20/rv60).
  - `val.pct`: 5-year percentile of index PE (aux PB) — **UNAVAILABLE in v1 production** (ruling R5): the previously cited `baidu_valuation_percentile` source is a **per-stock** percentile (真机 600519 PE 1.1), an index PE/PB five-year percentile has **no verified upstream**; adding the source is挂 stocks 层 as a new-source deliverable — until真机核验+补源 the factor stays honestly `UNAVAILABLE` with this reason.
  - `temp.astock`: the accreted `astock_temp` series verbatim (the frozen temperature formula stays in `macro/astock.py`; this factor never recomputes it).
- `def load_market_factor_inputs(*, provider_uri: str, end: str, as_of: UtcDateTime, rule: PanelAvailabilityRule) -> MarketFactorInputs:` — the production loader (thin, I/O): stamps `available_at` per `rule`, windows every series to `available_at <= as_of`, and sources each field read-only as follows:

| Inputs field | Production source (read-only, cited) | v1 result |
|---|---|---|
| `updown`, `closes_panel`, `limit_up_total` | engine provider daily panel — the same provider access `strategy/compute/regen.py:122/149` uses (**not** the resid outputs) | populated |
| `closes_index` | `strategy/compute/eqw_market.load_eqw_ret()` (date/ret/n) | populated |
| `astock_temp` | `var/macro_pulse/snapshots.jsonl` (`macro/pulse.py:16`), each line's own pull `ts` as `available_at` | populated (short) |
| `today_tape` | `datafeed/market_tape.read_tape()` derived block + `board_date`/`board_backfilled` | populated intraday |
| `break_counts`, `board_pools` | none (market_tape is snapshot-only; forward accretion is a later reviewed job) | `None` |
| `north_net`, `main_net`, `sector_flows` | none (fundflow live/minute only; no daily store) | `None` |
| `index_valuation`, `concept_membership`, `industry_returns` | none in v1 (index-valuation upstream unverified — R5; concept panel and industry panel loaders deferred) | `None` |
| `limit_reasons`, `sector_leaders`, `universe_versions` | none in v1 (涨停原因归因 / fundflow 板块领涨股 / 概念 universe 版本 diff — forward archiving belongs to the ①§5 snapshot-archive deliverable) | `None` |

  This loader is an interim Phase 5 adapter until Phase 3 production data adapters exist; it performs no vendor fallback of its own, fabricates no history, and returning `None` fields is its honest steady state.
- `def shield_inputs_from_factor_report(report: MarketFactorReport) -> dict[str, float | None]:` — deterministic projection to the market-temp shield's inputs (`astock_temp`, `break_rate`, plus `main_net_yi=None` until a flow history exists), for the "显式上游" role. Parity contract: on the same underlying snapshot the projected `break_rate`/`astock_temp` equal `build_market_temp()`'s board/global block values bit-for-bit; the shield's own wiring, coefficients and gate constants are untouched (rewiring the shield to consume this projection is deferred to the Lane D phase).
- `def market_factor_handler(*, spec: MarketFactorSetSpec, inputs: MarketFactorInputs, as_of: UtcDateTime) -> MarketFactorReport:` — the trusted deterministic worker entry registered (Task 8) in the Phase 2 handler registry; a pure delegation to `compute_market_factors` (kept separate so the handler material digest pins the factor set).

**Required invariants:**

1. same inputs ⇒ bit-identical report digest (run twice, compare);
2. a future row anywhere raises `FutureDataRefused` and produces no report;
3. removing history below `min_history_sessions` flips that factor to `UNAVAILABLE`, never to a zero-filled series, and the report's `missing_features`/`coverage_summary` update consistently;
4. spec §5 炸板率 uses the `break_rate = zb/(zt+zb)` 口径 (never `break_ratio` 开板率; never `promotion_rate`) — pinned by a fixture that supplies all three and asserts which one moves the factor;
5. truncation invariance: computing at `as_of=T` from inputs windowed to `T` equals computing at `T` from longer inputs windowed by the loader to `T` (the walk-forward discipline the seeder relies on);
6. the compute core imports nothing from `screen/`, `datafeed/`, `macro/`, `strategy/` — only the loader does (purity boundary testable by import inspection);
7. the availability rule is versioned and visible: every produced `MarketFactorValue.params` carries `availability_rule="avail-v1"`, and changing the rule version moves every value/report digest (differential test) — the "when was a daily bar knowable" assumption can never drift silently;
8. `market_factor_handler` output equals `compute_market_factors` output bit-for-bit for the same `(spec, inputs, as_of)` — the handler adds identity, never arithmetic.

- [ ] **Step 1: Write failing compute tests**

Test matrix (one focused test per row minimum):

- per-factor happy path against hand-computed expected values (synthetic 80-session fixtures; every formula above pinned numerically);
- per-factor short-history ⇒ `UNAVAILABLE` with reason naming the input and lengths;
- per-factor partial window ⇒ `DEGRADED` with the exact coverage fraction + reason (①§2 three-state);
- future-row refusal: one row with `available_at > as_of` anywhere ⇒ `FutureDataRefused`, no report;
- backfilled/foreign-date `today_tape` ignored + `tape_backfilled_ignored` badge; non-backfilled same-day point included;
- 口径 pin: a fixture supplying `break_ratio`-style 开板 numbers, `promotion_rate` and `zb/(zt+zb)` proves only the latter moves `breadth.break_rate`;
- 空池诚实: `zt+zb == 0` session leaves a gap, never 0.0;
- unit pins: `main_net` 元→亿 conversion and `north_net` 亿 pass-through recorded in params;
- determinism (two runs bit-identical) and truncation invariance (window-at-T == longer-input-windowed-to-T);
- import-purity of the compute core; loader windowing over tmp jsonl/parquet fixtures with faked provider access;
- shield-projection parity against a recorded `build_market_temp` fixture (monkeypatched cache reads — no network).

Run: `pytest tests/orchestration/test_market_factor_compute.py -v` — Expected: FAIL on missing compute functions.

- [ ] **Step 2: Implement the pure core, then the loader**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_market_factor_compute.py tests/orchestration/test_market_factor_contracts.py tests/orchestration/test_lane0_reports.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/market/factors.py tests/orchestration/test_market_factor_compute.py
git commit -m "feat(orchestration): deterministic PIT market-factor compute core + honest UNAVAILABLE"
```

---

## Task 3b: Factor-report rendering contract (`render_for_prompt` — the only outlet feeding regime/rotation)

**Files:**
- Modify: `guanlan_v2/orchestration/market/factors.py`
- Test: `tests/orchestration/test_factor_report_render.py`

**Consumes:** Task 1 contracts (`MarketFactorReport` with `summary`/`series`/three-state `status`); deliverable ①§4 rendering contract; the Phase 3 `RenderedDataBlock` untrusted-data channel shape (wrapped by the Task 8 renderer material).

**Produces:**

- `def render_factor_report_for_prompt(report: MarketFactorReport) -> str:` — pure, deterministic, clock-free. ①§0/§4 red line: the LLM never sees raw scalars or the typed payload JSON — this rendered block is the **only** outlet feeding `market.regime`/`market.rotation`. Contract (①§4 verbatim intent):
  1. the whole output is an untrusted delimited block; the header declares `as_of / clock_mode / universe_registry_version / battery_digest 前8位`;
  2. every `OK`/`DEGRADED` factor renders one summary line (`latest | Δ5d | Δ20d | pct250`) plus its ≤60-session compact series (3 significant digits, one week per line — D1); factors are grouped by `family` (R3: rendering groups by族, ids stay per-series);
  3. **`UNAVAILABLE` factors must render an explicit line** `<factor_id>: UNAVAILABLE(<reason>)` — absence is information, never silently omitted;
  4. `DEGRADED` factors surface their `coverage` and `reason` on the summary line;
  5. bounded length with refusal on overflow (no truncation path — mirroring the Task 8 experience renderer discipline); rendered bytes bind to the report's `content_digest` (the header digest prefix is the audit hook Task 10's e2e checks against the downstream `factor_report_digest`).
- Handler material `lane0.factor_report.renderer` (registered in Task 8's material inventory): wraps this function as the `trust="untrusted_data"` renderer with `rendered_from_payload_digest` binding, exactly like `lane0.experience.renderer`.

**Required invariants:**

1. determinism: same report ⇒ byte-identical block (two-run comparison);
2. UNAVAILABLE display: every `missing_features` id appears as an explicit `UNAVAILABLE(<reason>)` line; a rendered block omitting one is impossible (fixture-pinned);
3. DEGRADED display: coverage + reason surface verbatim;
4. no number outside the report appears in the block; every summary/series number round-trips from the payload (3-significant-digit formatting pinned);
5. bounded: an oversized synthetic report refuses before any prompt assembly — no truncation path exists (both sides of the bound tested);
6. the header digest prefix equals the report `content_digest` prefix (the downstream `factor_report_digest` audit anchor).

- [ ] **Step 1: Write failing renderer tests**

Matrix: header fields present; per-family grouping; summary-line format pinned; week-per-line series layout; 3-significant-digit formatting; UNAVAILABLE explicit line (id + reason both present); DEGRADED coverage/reason; determinism (two runs byte-identical); overflow refusal both sides of the bound; header digest-prefix binding.

Run: `pytest tests/orchestration/test_factor_report_render.py -v` — Expected: FAIL (renderer missing).

- [ ] **Step 2: Implement the pure renderer**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_factor_report_render.py tests/orchestration/test_market_factor_compute.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/market/factors.py tests/orchestration/test_factor_report_render.py
git commit -m "feat(orchestration): factor-report rendering contract (untrusted block for lane0 prompts)"
```

---

## Task 4: Experience contracts + event-folded store (`memory/experience.py`)

**Files:**
- Create: `guanlan_v2/orchestration/memory/experience.py` (the `memory/` package exists from Phase 3 Task 9)
- Test: `tests/orchestration/test_experience_contracts.py`
- Test: `tests/orchestration/test_experience_store.py`

**Consumes:** Phase 1 `EventType.CASE_CREATED/CASE_MATURED/CASE_REVIEWED` (reused, no enum change), `RunEvent`, `TypedPayloadRef`; Phase 2 `EventStore`/`PayloadStore`/`RuntimeUnitOfWork`/`IdempotencyConflict`; Phase 3 `AdminReviewVerifier` (fail-closed admin authority port); Task 2 `RegimeReport`.

**Produces:**

- Namespace rule: every case/matured/reviewed payload is persisted in the `main` namespace (public facts — the whole point is later retrieval); `sealed`/`review`/`audit` namespaces are never used by this module. Sealed holdout metrics can therefore never masquerade as experience cases by construction.
- Contracts (spec §6.4 field names verbatim, types upgraded to house rules):
  - `class RegimeCase(DigestModel)` (registered `RegimeCase@1`): `schema_version: Literal["1"] = "1"`, `id: NonEmptyStr`, `as_of: UtcDateTime`, `available_at: UtcDateTime` (validator ≥ `as_of`), `feature_schema_version: NonEmptyStr`, `scaler_digest: DigestHex`, `features: dict[LogicalId, FiniteFloat]`, `feature_coverage: dict[LogicalId, FiniteFloat]`, `missing_features: tuple[LogicalId, ...] = ()` (sorted, dup-free, disjoint from `features` keys; `feature_coverage` keys ⊇ `features` keys), `judgment: RegimeReport` (validator `judgment.as_of == as_of`; the ④-aligned Task 2 shape — `factor_report_digest`/`evidence`/`conflicts`/`analog_case_ids` — embedded unchanged), `links: tuple[NonEmptyStr, ...] = ()`, `content_digest: DigestHex` (SELF_DIGEST).
  - `class RealizedRegime(DigestModel)` (registered `RealizedRegime@1`): `schema_version: Literal["1"] = "1"`, `case_as_of: UtcDateTime`, `horizon_trading_days: PositiveInt`, `entry_date: NonEmptyStr`, `exit_date: NonEmptyStr`, `forward_return: FiniteFloat`, `max_drawdown: FiniteFloat` (le=0), `realized_volatility: FiniteFloat` (ge=0), `realized_trend: Literal["牛","熊","震荡"]` (ruling R6: the ④ `TrendState` Chinese vocabulary minus `unknown` — 判读与 realized share one 词表 so the ④§4 calibration analysis is possible), `realized_risk: Literal["risk_on","risk_off","neutral"]`, `realized_heat: Literal["normal","overheat"] | None` (values match `RiskState`/`HeatState`), `heat_unavailable_reason: NonEmptyStr | None` (required iff `realized_heat is None`), `available_at: UtcDateTime` (spec field — the exit bar's availability time; data-driven PIT fact, never a wall clock), `data_snapshot_hash: DigestHex` (spec field — content digest of the exact PIT benchmark window used for grading; provenance binding), `grader_version: NonEmptyStr`, `grader_digest: DigestHex`, `benchmark_id: LogicalId`, `content_digest: DigestHex` (SELF_DIGEST). Realized labels have **no** `unknown` — grading is deterministic or it does not happen.
  - `class CaseMatured(DigestModel)` (registered `CaseMatured@1`): `schema_version: Literal["1"] = "1"`, `case_id: NonEmptyStr`, `realized: RealizedRegime`, `matured_at: UtcDateTime`, `available_at: UtcDateTime` (validator-tied `== realized.available_at`), `content_digest: DigestHex` (SELF_DIGEST) — append-only event payload, never edits `RegimeCase`.
  - `class CaseReviewed(DigestModel)` (registered `CaseReviewed@1`): `schema_version: Literal["1"] = "1"`, `case_id: NonEmptyStr`, `maturity_event_id: NonEmptyStr`, `lesson: NonEmptyStr`, `reviewed_at: UtcDateTime`, `available_at: UtcDateTime`, `content_digest: DigestHex` (SELF_DIGEST).
- Stream + log:
  - `EXPERIENCE_STREAM_ID = "experience.lane0.v1"` (subject to clause C2), partition `"main"`.
  - `class ExperienceLog:` (service, not pydantic) constructed over injected `EventStore` + `PayloadStore` + `AuthoritativeClock`; methods `append_case(case: RegimeCase, *, correlation_run_id: str | None, idempotency_key: str) -> RunEvent`, `append_matured(matured: CaseMatured, *, idempotency_key: str) -> RunEvent`, `append_reviewed(reviewed: CaseReviewed, *, actor: AuthenticatedAdminPrincipal, verifier: AdminReviewVerifier, idempotency_key: str) -> RunEvent`. Each append is one `RuntimeUnitOfWork` batch: exactly one registry-validated payload put (`main` namespace) + one typed `RunEvent` whose `event_type` is the reserved Phase 1 value, `partition="main"`, `payload_schema_ref` the Phase 5 schema and `payload_ref` the put's staged ref — persist-then-publish, all-or-none, no payload-only or event-only orphan under injected crash (the Phase 2 UoW guarantees consumed, not reimplemented). Deterministic idempotency keys (table):

| Append | Idempotency key | Guard before append |
|---|---|---|
| `append_case` | `case.created:{id}` | none (creation is the root fact) |
| `append_matured` | `case.matured:{case_id}:{grader_version}` | a visible `CASE_CREATED` for `case_id` must exist |
| `append_reviewed` | `case.reviewed:{case_id}:{maturity_event_id}` | `maturity_event_id` must resolve to a visible `CASE_MATURED` of the same case; fail-closed `AdminReviewVerifier` must authenticate the actor |

  `append_reviewed` is admin-gated — workers can only *propose* lessons through the Phase 3 memory proposal boundary; the reviewed append is the human/curator acceptance step ⑦ and is never reachable from a worker capability.
  - Read side: `def visible_case_events(self) -> tuple[RunEvent, ...]:` — the visible stream of the experience stream in `(visible_seq)` order, the sole input `fold_case_views` consumes in production (staged/journal-only events never reach the fold).
  - `append_matured` refuses (ValueError naming the case) when no `CASE_CREATED` for `case_id` is visible; `append_reviewed` refuses when `maturity_event_id` does not resolve to a visible `CASE_MATURED` for the same case (⑦ requires ⑤).
- Folded views:
  - `class CaseView(DigestModel)` (internal): `case: RegimeCase`, `state: Literal["pending","matured","reviewed"]`, `realized: RealizedRegime | None`, `lesson: NonEmptyStr | None`, `maturity_event_id: NonEmptyStr | None`.
  - `def fold_case_views(events: Sequence[RunEvent], *, resolve_payload: Callable[[TypedPayloadRef], object], as_of: UtcDateTime) -> tuple[CaseView, ...]:` — pure. Order of operations is frozen: (1) keep only visible events of the three case types; (2) resolve payloads; (3) **PIT-filter each payload by its own `available_at <= as_of` before any other consideration** (a matured fact whose `available_at > as_of` leaves the case visible-but-pending; a case whose `available_at > as_of` is entirely invisible even if its matured fact would qualify — impossible by validator but tested anyway); (4) fold to per-case state; (5) sort by `(as_of, id)`. Filter-then-fold, never fold-then-filter. Fold state machine (closed):

| Visible & PIT-passing facts for a case | Folded `state` | View carries |
|---|---|---|
| `CASE_CREATED` only | `pending` | case; no realized numbers |
| `CASE_CREATED` + `CASE_MATURED` | `matured` | case + `realized` + `maturity_event_id` |
| all three | `reviewed` | case + realized + `lesson` |
| `CASE_MATURED`/`CASE_REVIEWED` without their prerequisite | (dropped) | deterministic warning entry (defense in depth; the append guards make this unreachable through the log) |

**Required invariants:**

1. one committed `RegimeCase` per `id`; same-key/same-content replay returns the stored event, different content raises `IdempotencyConflict` (one case per session per feature schema — reruns with drifted judgments conflict loudly instead of silently duplicating);
2. `pending → matured → reviewed` is monotone in the fold; a `CaseReviewed` without visible `CaseMatured` is impossible to append and, if injected directly into a fixture stream, is dropped by the fold with a deterministic warning entry (defense in depth);
3. no in-place mutation anywhere: maturing/reviewing changes only the folded view, `RegimeCase.content_digest` of the old object is byte-stable;
4. Phase 1 `events.py` and its tests are untouched — reuse, not extension;
5. all case payloads live in `main`; an attempted non-main append is rejected by the log before any store call (namespace masquerade never reaches the Phase 2 validators, which would also reject it);
6. `fold_case_views` is pure and total — malformed/foreign event types in the input sequence are ignored, never raise (the fold is a read model, not a validator).

- [ ] **Step 1: Write failing contract + store tests**

Contract matrix: field presence under the exact spec §6.4 names; `available_at < as_of` rejected; features/missing disjointness both directions; `feature_coverage` superset rule; `judgment.as_of == as_of` pin; heat-None ⇔ reason; self-digest verification on load; frozen/extra-forbid; relocation invariance of any embedding parent.

Store matrix over the Phase 2 in-memory backend: append/fold happy path (pending → matured → reviewed across three folds); idempotent same-content replay returns the stored event; same-key different-content raises `IdempotencyConflict`; matured-before-created refusal; reviewed-before-matured refusal; reviewed-without-verifier refusal (fail-closed); injected mid-batch failure leaves neither payload nor event (UoW all-or-none); PIT visibility — query at `as_of` strictly between case `available_at` and matured `available_at` sees `pending`, at ≥ matured availability sees `matured`, before case availability sees nothing; fold purity (same events ⇒ same views tuple, digest-compared); direct-injection of an orphan `CaseReviewed` event into a fixture stream is dropped by the fold with a deterministic warning entry.

Run: `pytest tests/orchestration/test_experience_contracts.py tests/orchestration/test_experience_store.py -v` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement contracts, log, fold**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_experience_contracts.py tests/orchestration/test_experience_store.py tests/orchestration/test_events.py -v` — Expected: PASS (Phase 1 event tests untouched and green).

```bash
git add guanlan_v2/orchestration/memory/experience.py tests/orchestration/test_experience_contracts.py tests/orchestration/test_experience_store.py
git commit -m "feat(orchestration): PIT event-folded RegimeCase experience store"
```

---

## Task 5: Versioned point-in-time scaler + numeric nearest-neighbour retrieval

**Files:**
- Modify: `guanlan_v2/orchestration/memory/experience.py`
- Test: `tests/orchestration/test_experience_retrieval.py`

**Consumes:** Task 4 views; the in-repo expanding-scaler precedent `guanlan_v2/strategy/compute/factor_regime.py::walk_forward_regimes` — standardizer mu/sd fitted **only on ≤t history** (factor_regime.py:100-101) and persisted as versioned snapshots keyed by `fit_asof` including `{mu, sd, ...}` (factor_regime.py:106-108). Phase 5 copies that shape and its cache-equality test discipline; it does not reuse the per-factor-family artifacts.

**Produces:**

- `class ExperienceScalerSnapshot(DigestModel)` (registered `ExperienceScalerSnapshot@1`): `schema_version: Literal["1"] = "1"`, `feature_schema_version: NonEmptyStr`, `scaler_version: NonEmptyStr` ("expanding-zscore-v1"), `fit_as_of: UtcDateTime`, `n_obs: NonNegativeInt`, `mu: dict[LogicalId, FiniteFloat]`, `sd: dict[LogicalId, FiniteFloat]` (every value > 0; a degenerate feature is dropped from `mu`/`sd` and listed in `degenerate_features: tuple[LogicalId, ...]`), `content_digest: DigestHex` (SELF_DIGEST). The `scaler_digest` recorded on `RegimeCase` and `ExperienceSelection` is this snapshot's `content_digest`.
- `def fit_scaler(views: Sequence[CaseView], *, as_of: UtcDateTime, feature_schema_version: NonEmptyStr, scaler_version: NonEmptyStr = "expanding-zscore-v1") -> ExperienceScalerSnapshot:` — pure; uses only cases with `available_at <= as_of` **and** matching `feature_schema_version`; per-feature mu/sd over cases where the feature is present (missing never imputed as zero); `n_obs = 0` produces a valid empty snapshot (cold start) whose retrieval necessarily returns zero neighbours with a badge.
- **Scaler persistence:** the snapshot used for any retrieval that produces runtime evidence is persisted once in `main` (registered `ExperienceScalerSnapshot@1` payload) so every `scaler_digest` on a `RegimeCase`/`ExperienceSelection` is dereferenceable — at bootstrap runtime the Task 8 experience provider persists it through `BridgeEvidenceWriter` alongside the selection; in the Task 7 seeder the log persists it per refit. Same-content persists are idempotent (key `scaler:{feature_schema_version}:{fit_as_of_date}:{scaler_version}`).
- Query/selection payloads:
  - `class ExperienceQuery(DigestModel)` (registered `ExperienceQuery@1`): `schema_version: Literal["1"] = "1"`, `as_of: UtcDateTime`, `feature_schema_version: NonEmptyStr`, `features: dict[LogicalId, FiniteFloat]`, `k: PositiveInt` (le=20), `content_digest: DigestHex` (SELF_DIGEST).
  - `class ExperienceNeighbour(DigestModel)` (nested): `case_id: NonEmptyStr`, `case_as_of: UtcDateTime`, `distance: FiniteFloat` (ge=0), `feature_overlap: FiniteFloat` (ge=0, le=1), `state: Literal["pending","matured","reviewed"]`, `realized: RealizedRegime | None`, `lesson: NonEmptyStr | None`.
  - `class ExperienceSelection(DigestModel)` (registered `ExperienceSelection@1`): `schema_version: Literal["1"] = "1"`, `query_digest: DigestHex`, `scaler_digest: DigestHex`, `feature_schema_version: NonEmptyStr`, `neighbours: tuple[ExperienceNeighbour, ...]` (sorted by `(distance, case_id)`, len ≤ k), `visible_case_count: NonNegativeInt`, `badges: tuple[NonEmptyStr, ...] = ()`, `content_digest: DigestHex` (SELF_DIGEST).
- `MIN_FEATURE_OVERLAP = 0.6` (fraction of the query's feature keys that must be co-present in a candidate case after intersecting with the scaler's non-degenerate keys).
- `def retrieve_neighbours(query: ExperienceQuery, *, views: Sequence[CaseView], scaler: ExperienceScalerSnapshot) -> ExperienceSelection:` — pure. Closed order:
  1. reject (`ValueError`) on `feature_schema_version` mismatch between query and scaler;
  2. PIT: candidates = views already folded at the query's `as_of` (caller obligation, re-asserted: every candidate `case.available_at <= query.as_of`, else `FutureDataRefused`);
  3. same `feature_schema_version` cases only;
  4. distance: let `K` = keys co-present in query, candidate and the scaler's non-degenerate set; `feature_overlap = |K| / |query keys ∩ scaler keys|`; candidates with overlap < `MIN_FEATURE_OVERLAP` are excluded; `distance = sqrt(Σ_{f∈K} ((q_f − μ_f)/σ_f − (c_f − μ_f)/σ_f)²) / sqrt(|K|)` — the `1/sqrt(|K|)` normalization makes distances comparable across different overlap sizes (spec allows cosine or distance; v1 fixes normalized standardized Euclidean, recorded in `scaler_version` lineage);
  5. top-k by `(distance, case_id)`; neighbours embed each candidate's folded `state`/`realized`/`lesson` as of the query time (a pending neighbour shows no realized numbers — unmatured numbers never leak).
  Zero candidates ⇒ empty selection with badge `cold_start:0_neighbours` — a valid, honest result, never an error.

**Required invariants:**

1. scaler PIT: a case with `available_at > fit_as_of` cannot move `mu`/`sd` (differential test);
2. retrieval PIT: adding a future case (available after `query.as_of`) leaves `ExperienceSelection.content_digest` byte-identical (the retrieval half of the spec-mandated invariance; Task 7 proves the full pipeline);
3. cross-schema-version contamination structurally impossible (mismatch rejects; mixed-version stores retrieve only matching);
4. missing features are explicit: never imputed, always reflected in `feature_overlap`, exclusion below threshold tested both sides of 0.6;
5. determinism: distance ties broken by `case_id`, repeated calls bit-identical;
6. persisted scaler snapshots are idempotent per fit identity and every `scaler_digest` appearing on a case/selection dereferences to a persisted `ExperienceScalerSnapshot@1` whose recomputed digest matches.

- [ ] **Step 1: Write failing retrieval tests**

Matrix: scaler fit PIT differential (future case cannot move mu/sd); degenerate-feature drop; empty-store scaler validity; schema-version mismatch rejection; mixed-version store filtering; overlap threshold both sides of 0.6; missing-feature handling (never imputed); distance formula pinned numerically on a 3-case hand-computed fixture; tie-break by `case_id`; k-truncation; pending neighbour hides realized numbers; retrieval-PIT invariance (future case leaves `ExperienceSelection.content_digest` byte-identical); cold-start badge; determinism across repeated calls.

Run: `pytest tests/orchestration/test_experience_retrieval.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement scaler + NN**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_experience_retrieval.py tests/orchestration/test_experience_store.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/memory/experience.py tests/orchestration/test_experience_retrieval.py
git commit -m "feat(orchestration): versioned PIT scaler + numeric nearest-neighbour case retrieval"
```

---

## Task 6: Delayed deterministic grader + observed-trade-date calendar

**Files:**
- Modify: `guanlan_v2/orchestration/memory/experience.py`
- Create: `tests/orchestration/golden/regime_grader_policy_v1.json`
- Test: `tests/orchestration/test_regime_grader.py`

**Consumes:** Task 3 `DailyValueRow`; Task 4 log/views; Phase 3 `TradingCalendar` protocol; Phase 4 `ExperimentStatus.WAITING_FOR_MATURITY` resume semantics (clause C5). Reuse-by-citation maturity precedents (deterministic, no LLM):
- bar-count criterion `matured = (idx + hz) < len(rows)` — `guanlan_v2/seats/basket_perf.py:36` (exit bar must exist);
- position-in-unique-trade-date-list realized date `_realized_map` — `guanlan_v2/screen/factor_vintage.py:20-27`, with its read-side OOS gate `realized_date <= date` (factor_vintage.py:138);
- fully-matured-only distillation gate `_pair_matured` (`matured_n == n`) — `guanlan_v2/console/tools.py:1117-1126` (`rerank_distill_impl`).

**Produces:**

- **Calendar decision (explicit, since no shared trading-calendar module exists — grounding map §4):** Phase 5 does **not** mint an exchange calendar and never uses `np.busday`/`pd.bdate_range` for maturity (those are the honest-approximation idioms of `datafeed/health.py:39` and `factor_regime.py:247`, both unfit for realized-label arithmetic). The authority for "N 个交易日后" is the **observed PIT trade-date list of the grading benchmark series itself** — the dominant in-repo delayed-labeling idiom (bar counts in the instrument's own series / positions in the observed date list; grounding map §4 items 1–2), which handles long holidays by construction and never consults a wall clock. It is packaged as `class ObservedTradeDateCalendar:` implementing the Phase 3 `TradingCalendar` protocol — `calendar_id = "cn-ashare-observed-v1"`, `material_ref: ContentRef` (id `calendar.cn_ashare_observed`, version = list digest prefix), `is_session(date) -> bool`, `sessions_between(start, end) -> int` — built by `def build_observed_calendar(dates: tuple[NonEmptyStr, ...], *, version: NonEmptyStr) -> ObservedTradeDateCalendar:` (strictly increasing ISO dates required). In PIT_REPLAY the list is frozen by digest; in ONLINE it only extends forward (a changed historical prefix is a refusal, not a re-derivation). Where a Phase 1 `ClockSpec.calendar_id` is needed for the bootstrap `DataContext`, this `calendar_id` is the value bound.
- `class RegimeGraderSpec(DigestModel)` (registered `RegimeGraderSpec@1`): `schema_version: Literal["1"] = "1"`, `grader_version: NonEmptyStr` ("regime-grader-v1"), `horizon_trading_days: PositiveInt` (v1: 20; spelled to match `RealizedRegime`), `benchmark_id: LogicalId` ("eqw_all_a"), `bull_min_return: FiniteFloat` (0.03), `bear_max_return: FiniteFloat` (−0.03), `risk_off_max_drawdown: FiniteFloat` (−0.05), `risk_on_min_return: FiniteFloat` (0.02), `risk_on_min_drawdown: FiniteFloat` (−0.03), `content_digest: DigestHex` (SELF_DIGEST). v1 value hand-frozen in `regime_grader_policy_v1.json` (thresholds are provisional; tuning belongs to Phase 4 `run_optimize` over matured cases with the sealed-holdout discipline — explicitly out of scope here).
- Label rules (closed, documented in the spec model's docstring and pinned by tests): entry = first session strictly after the case's `as_of` session; exit = the `horizon_trading_days`-th following session in the date list; `forward_return` = ∏(1+ret)−1 over `(entry, exit]`; `max_drawdown` = min running drawdown of the compounded path; trend: `≥ bull_min_return` ⇒ `牛`, `≤ bear_max_return` ⇒ `熊`, else `震荡` (R6: realized labels use the ④ `TrendState` Chinese vocabulary — grader 词表 synced with the judgment enums); risk: `max_drawdown ≤ risk_off_max_drawdown` ⇒ risk_off; else `forward_return ≥ risk_on_min_return and max_drawdown > risk_on_min_drawdown` ⇒ risk_on; else neutral; heat: `None` in v1 with `heat_unavailable_reason="no_realized_heat_definition_v1"` (honest — a realized-heat definition needs the temperature history Lane 0 does not yet have).
- `class CaseMaturityPending(NamedTuple):` `resume_after: UtcDateTime`, `wakeup_key: NonEmptyStr` (deterministic: `case.mature:{case_id}:{grader_version}`) — the value handed to the Phase 4 `WAITING_FOR_MATURITY` machinery.
- `def grade_case(case: RegimeCase, *, bench: tuple[DailyValueRow, ...], calendar: ObservedTradeDateCalendar, grader: RegimeGraderSpec) -> RealizedRegime | CaseMaturityPending:` — pure. Closed steps: (1) locate the case's session in the calendar (the last session ≤ the `as_of` date; a case dated on a non-session grades from the previous session — documented, tested); (2) entry = next session, exit = the `horizon_trading_days`-th session after entry **by list position** (the `_realized_map` idiom); (3) if the exit session is absent from the observed list (the basket_perf criterion inverted), return `CaseMaturityPending` with `resume_after` = entry date + `horizon_trading_days` list-positions extrapolated by the mean recent session spacing, clamped to ≥ the last observed session's close per the availability rule — a scheduling hint only, never a correctness input (wakeup re-checks maturity); (4) otherwise compound returns over `(entry, exit]`, derive drawdown/vol and the labels, stamping `available_at` = the exit bar's `available_at` and `data_snapshot_hash` = the content digest of the exact `(entry, exit]` bench window graded. Bench rows carry `available_at`, so grading at a historical vantage point cannot see rows beyond it by construction.
- Golden documentation obligation: `regime_grader_policy_v1.json` embeds, next to the frozen values, a `notes` array restating each label rule in one sentence — the golden is the single human-readable authority a reviewer signs off (mirroring the `limit_rule_policy_v1.json` pattern).
- `def mature_pending_cases(*, views: Sequence[CaseView], bench: tuple[DailyValueRow, ...], calendar: ObservedTradeDateCalendar, grader: RegimeGraderSpec, log: ExperienceLog, clock: AuthoritativeClock) -> tuple[CaseMatured, ...]:` — grades every `pending` view, appends `CaseMatured` for matured ones (idempotent via the Task 4 keys; re-run appends nothing new), leaves the rest pending. `CaseMatured.available_at` = `realized.available_at`, the **exit bar's** availability time per the rule (data-driven, never `clock.now()` — `matured_at` is the wall-clock audit fact, `available_at` is the PIT fact; for live grading they may coincide, for replay they must not be conflated).
- Downstream gate (consumed by Phase 4 validation and Task 7's seed report): only `state == "matured"`/`"reviewed"` views may enter validation/distillation — the `_pair_matured` rule transplanted: a batch is usable iff every sampled case is matured (`matured_n == n`), pending cases never leak numbers into feedback. Exposed as `def matured_only(views: Sequence[CaseView]) -> tuple[CaseView, ...]:` which **raises** (`ValueError` naming the pending ids) rather than silently filtering when the caller requested a validation batch — silent drops are the calibration.py behavior we deliberately do not copy for feedback paths.
- **Phase 4 interop:** `CaseMaturityPending` is the value a Phase 4 experiment wrapper folds into `ExperimentStatus.WAITING_FOR_MATURITY` with the same `resume_after`/`wakeup_key` fields; on wakeup only already-matured batches are processed (idempotent by the Task 4 keys). Phase 5 provides the carrier and the deterministic wakeup key grammar; the experiment ledger/state machine itself stays Phase 4-owned (clause C5).

**Required invariants:**

1. maturity counts **bars in the observed benchmark list**, never calendar days (a fixture with a 9-day holiday gap proves the exit lands on the 20th session, not day 20);
2. grading is deterministic and LLM-free (module imports no model/gateway symbol — asserted by import inspection);
3. an unmatured case yields `CaseMaturityPending` and **no** event; repeated maturation runs are idempotent;
4. `available_at` of `CaseMatured` is data-driven (differential test: shifting the wall clock changes `matured_at` only, and the folded PIT view at historical `as_of` is unchanged);
5. grader thresholds live only in the golden-frozen `RegimeGraderSpec` — no literal thresholds in functions.

- [ ] **Step 1: Write failing grader tests**

Matrix: label rules at every threshold boundary, both sides (牛/熊/震荡 — R6 vocabulary; risk_off-by-drawdown precedence over risk_on; neutral fall-through); forward-return/drawdown/vol pinned numerically on a hand-computed path; holiday-gap fixture (9-calendar-day gap ⇒ exit on the 20th *session*); entry-session convention (first session strictly after `as_of`'s session); unmatured ⇒ `CaseMaturityPending` with deterministic `wakeup_key` and no event; maturation batch idempotency (second run appends zero); `available_at` data-driven differential (shifted wall clock moves `matured_at` only; folded historical views byte-stable); heat-None-with-reason in v1; golden digest reproduction; calendar protocol conformance (`sessions_between` vs list positions, `is_session` membership); LLM-free import assertion; matured-only downstream gate (a batch containing one pending case is refused for validation use).

Run: `pytest tests/orchestration/test_regime_grader.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement calendar, grader, maturation batch**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_regime_grader.py tests/orchestration/test_experience_store.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/memory/experience.py tests/orchestration/golden/regime_grader_policy_v1.json tests/orchestration/test_regime_grader.py
git commit -m "feat(orchestration): delayed deterministic regime grader on observed trade-date calendar"
```

---

## Task 7: Cold-start historical replay seeding + future-case invariance

**Files:**
- Modify: `guanlan_v2/orchestration/memory/experience.py`
- Test: `tests/orchestration/test_experience_seed.py`

**Consumes:** Tasks 3–6. Time conventions (versioned constants, part of the seed identity): `QUERY_AS_OF_UTC = "01:30"` (09:30 Asia/Shanghai session open) and `CASE_AVAILABLE_UTC = "07:05"` (15:05 session close) — a case judged on session D is retrievable only from D's close, so a same-day query never sees its own case.

**Produces:**

- `SEED_LINK = "seed:lane0-replay-v1"` (recorded into `RegimeCase.links`).
- `def seed_judgment_proxy(report: MarketFactorReport) -> RegimeReport:` — deterministic PIT-clean proxy judgment for seeded cases (no LLM). Closed v1 rule (constants in-module, pinned by tests): let `d` = the day's `breadth.divergence` value, `ad20` = `breadth.ad_ratio` ma20 aux, `rv` = `vol.rv` (B9: values are read from the summary/series side of the 19-id battery — `summary.latest` and the series aux — per the Task 1 aligned shape); trend mass — `ad20 ≥ +0.10 ⇒ 牛 0.6`, `ad20 ≤ −0.10 ⇒ 熊 0.6`, else `震荡 0.6` (R6 Chinese `TrendState` values; mirroring the regime-lite spirit of `strategy/market_status.py:158-163` without importing it), remainder split evenly over the other non-unknown labels minus the unknown share; risk mass — `d ≥ 1.5 ⇒ risk_off 0.5` else `neutral 0.5`; heat mass — `temp.astock ≥ 85 ⇒ overheat 0.5` else `normal 0.5`; on every axis `unknown` = `min(0.9, missing_share)` where `missing_share = len(missing_features)/len(spec ids)`, non-unknown masses rescaled to `1 − unknown`; modal `trend`/`risk_state`/`heat_state` follow from the masses per Task 2 validator 3; always `confidence=LOW` (the `confidence_score` float no longer exists per Task 2/④); proxy output follows the Task 2 aligned shape — `factor_report_digest` = the computed report's `content_digest`, `evidence` = one anchor per rule-referenced factor present that day (factor_id + value + fixed-template reading), `conflicts=()`, `analog_case_ids=()`; `drivers`/`evidence_factor_ids` = the rule-referenced factor ids present that day; narrative a fixed template naming the rule version; `unknown_reason` populated from `missing_features` whenever Task 2's gating demands it. A factor referenced by the rule but missing simply pushes its axis toward unknown — structurally coverage-driven. Seeds never look at forward data for the judgment — the *value* of seeds lies in their later deterministic `CaseMatured` labels, not their judgments.
- `class SeedReport(DigestModel)` (internal): `start: NonEmptyStr`, `end: NonEmptyStr`, `cases_created: NonNegativeInt`, `cases_skipped_existing: NonNegativeInt`, `matured_appended: NonNegativeInt`, `coverage_gap_dates: tuple[NonEmptyStr, ...]`, `feature_schema_version: NonEmptyStr`, `scaler_digest_last: DigestHex | None`.
- `def seed_experience_from_history(*, inputs: MarketFactorInputs, spec: MarketFactorSetSpec, grader: RegimeGraderSpec, calendar: ObservedTradeDateCalendar, log: ExperienceLog, start: NonEmptyStr, end: NonEmptyStr, clock: AuthoritativeClock) -> SeedReport:` — strict **time-ordered** walk over sessions in `[start, end]`: for each session D — (1) window inputs to D's availability and run `compute_market_factors` at D's `as_of`; skip D with a coverage-gap entry when the feature vector is empty; (2) fold views at D and `fit_scaler` on strictly-before-D cases; (3) build the deterministic case `id = f"rc.{feature_schema_version}.{yyyymmdd}"`, judgment from the proxy, `scaler_digest` from step (2), `available_at` = D close per convention; (4) `append_case` (idempotent — re-seeding skips); (5) after the walk, run `mature_pending_cases` once — every `CaseMatured.available_at` is the exit bar's availability, **never** the batch wall clock.

**Required invariants (the spec §6.4 acceptance, verbatim intent):**

1. **future-case invariance:** seed `[2019, 2021]`, record for a probe date Q∈2020 the triple (folded views digest, scaler digest at Q, `ExperienceSelection.content_digest` for a fixed query at Q); then seed the additional year 2022 into the same store; the recorded triple is **byte-identical** — later cases/realized/lessons, even batch-generated, are invisible at earlier `as_of`;
2. re-seeding the same range is a no-op (idempotency keys; `cases_skipped_existing == cases_created` of the first run);
3. a seeded case's judgment uses only ≤D data (differential test: truncating inputs after D does not change case D's digest);
4. batch wall-clock independence: running the seeder under two different injected clocks yields byte-identical case/matured payload digests (only event `occurred_at`/`matured_at` audit facts differ);
5. seeded and live cases are indistinguishable to retrieval except for the `links` marker.

- [ ] **Step 1: Write failing seed tests**

Matrix: the five invariants over a synthetic 3-year fixture (deterministic pseudo-random-walk inputs, fixed seed constant in the test); the future-case-invariance triple recorded before/after seeding a later year and compared byte-for-byte; re-seed no-op counts; ≤D-only judgment differential; two-clock byte-identity of payload digests; coverage-gap honesty (a month of missing inputs appears in `coverage_gap_dates`, produces no fabricated cases, and the following month's scaler `n_obs` reflects the gap); proxy-judgment rule pins (each branch of the trend/risk/heat rule, the unknown-mass rescale, and a fully-missing day yielding modal-unknown LOW); seeded-vs-live indistinguishability except `links`.

Run: `pytest tests/orchestration/test_experience_seed.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement the seeder**

Implementation order inside the walk is load-bearing (retrieve-scaler-before-append, append-before-advance); keep the per-session body a single pure function `def _seed_one_session(...)` so the invariance tests can bisect failures to a session.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_experience_seed.py tests/orchestration/test_experience_retrieval.py tests/orchestration/test_regime_grader.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/memory/experience.py tests/orchestration/test_experience_seed.py
git commit -m "feat(orchestration): time-ordered cold-start seeding with future-case invariance"
```

---

## Task 8: Lane 0 worker catalog materials + experience bridge + BOOTSTRAP runtime profile

**Files:**
- Modify: `guanlan_v2/orchestration/bootstrap.py` (create — contracts/profile half)
- Create: `config/orchestration/materials/lane0/` (prompt/SKILL/guardrail/handler files)
- Test: `tests/orchestration/test_lane0_catalog.py`
- Test: `tests/orchestration/test_bootstrap_profile.py`

**Consumes:** Phase 1 catalog ABI (`WorkerSpec`, `ExecutionSpec`, `EvidencePolicy`, `SkillBinding`, `InputBinding`/`OutputBinding`, `build_catalog_snapshot`, skill-v1 grammar incl. the exact `## ⚠️ CRITICAL: Data Source Priority` heading and canonical-JSON trigger lines, UTF-8 no-BOM materials); Phase 2 `StaticRuntimeProfile`/`check_runtime_support`/trusted handler registry/`ExecutionBridgeDescriptor@1` pattern; Phase 3 `RenderedDataBlock` + `DataBridgePrefetchBinding` shape (mirrored, not reused); Tasks 1–5 schemas.

**Produces:**

- **Three final WorkerSpecs** (ids frozen by CRIB/spec §3.0; all `catalog_role="final"`, `selection_scope="dynamic_allowed"`, `lane="market"`, `can_emit_decision=False`, `decision_authority="none"`, `supported_modes=("online","pit_replay")`):
  - `market.factor`: `tier=READER`, `ExecutionSpec(kind=DETERMINISTIC, handler_ref=ContentRef(id="lane0.market.factor.handler", ...))` — the handler material's bytes pin `factor_set_version`/digest so the catalog digest moves with the factor set; `read_categories=("market_data",)`, `inputs=()`, `outputs=(OutputBinding(name="primary", schema_ref=MarketFactorReport@1),)`, `evidence_policy=EvidencePolicy(tool_calls=FORBIDDEN, require_input_refs=False, require_number_anchors=False)` (a deterministic report is its own evidence), empty capability allowlist.
  - `market.regime`: `tier=WRITER`, `ExecutionSpec(kind=LLM, model_tier="reasoner", thinking_budget=0)`, `system_prompt_ref`, one `SkillBinding` (skill `lane0.regime.skill`), one guardrail (`lane0.honesty.guardrail` — mandates: numbers only from the factor report with NumberAnchors; missing factor report ⇒ all-unknown LOW-confidence output; never invent history), `capability_allowlist=(experience.retrieve,)`, `tool_calls=REQUIRED`, `read_categories=("experience_cases","upstream_artifacts")`, `inputs=(InputBinding(name="market_factor_report", schema_ref=MarketFactorReport@1, required=False, cardinality="one"),)` (`required=False` is load-bearing: the Task 10 dependency feeds this input under `policy=DEGRADE`, and Phase 1 spec.py:1006 `dependency_weakens_evidence` rejects a required input fed by a degrade dependency; the honesty guardrail — absence ⇒ all-unknown output — carries the contract instead; additionally the bound report reaches the prompt **only as the Task 3b rendered untrusted block** — renderer material `lane0.factor_report.renderer`, `trust="untrusted_data"`, `rendered_from_payload_digest` binding, ①§0: the LLM never sees the raw typed payload, and NumberAnchors cite values from that block), `outputs=(primary → RegimeReport@1)`, `evidence_policy(require_input_refs=True, require_number_anchors=True, allow_unsourced_numbers=False, optional_data_may_degrade=True)`.
  - `market.rotation`: same shape as `market.regime` (including the `required=False` `market_factor_report` binding) with `outputs=(primary → RotationReport@1)` and skill `lane0.rotation.skill` (mainline ranking + per-mainline `RotationStage` stage discipline; the industry-chain block is **when-supplied** per ④ — absent block ⇒ `chain_nodes` empty, never improvised; the Not-ideal-for list no longer claims 产业链未绑定).
- **Experience capability + bridge** (the lawful static-runtime channel for "读 experience"):
  - `CapabilityDescriptor` `experience.retrieve@1`: `capability_kind="data_adapter"`, `transport="in_process"`, `operation="experience.retrieve"`, `input_schema_ref=ExperienceQuery@1`, `output_schema_ref=ExperienceSelection@1`; trusted provider handler material `lane0.experience.provider` wraps Task 5 `retrieve_neighbours` over the folded store (read-only; grading/appending is never reachable from a worker).
  - `class ExperiencePrefetchBinding(DigestModel)` (registered `ExperiencePrefetchBinding@1`, in `bootstrap.py`): `schema_version: Literal["1"] = "1"`, `bridge_id: LogicalId` ("experience.bridge"), `worker_id: LogicalId`, `capability_ref: CapabilityRef`, `invocation_mode: Literal["always_invoke"] = "always_invoke"`, `success_requires_finalized_call: Literal[True] = True`, `feature_vector_pointer: NonEmptyStr` ("/feature_vector" into the bound `market_factor_report` input payload), `feature_schema_version_pointer: NonEmptyStr` ("/feature_schema_version"), `k: PositiveInt` (v1: 5, le=20), `content_digest: DigestHex` (SELF_DIGEST). Closed JSON-pointer projections only — no expressions, no model-generated params (the Phase 3 `DataBridgePrefetchBinding` discipline mirrored).
  - One `ExecutionBridgeDescriptor@1` material `experience.bridge`: activation = the `experience.retrieve` capability ref **or** read category `experience_cases`; `pre_input_kind="none"`, `lifecycle="static_prefetch_v1"`; config = the binding above (both registered as catalog `kind="guardrail"` materials, provider + support analyzer as distinct `kind="handler"` materials, per the Phase 2 rule). Support analyzer bounds: `max_capability_invocations=1`, `min_finalized_tool_calls_on_success=1` — an **empty** `ExperienceSelection` is still one successful finalized call (cold start satisfies REQUIRED honestly). On `LLM` nodes the session renders the selection once into a `RenderedDataBlock` (untrusted-data channel; renderer = handler material `lane0.experience.renderer` — pure function of the selection payload, `trust="untrusted_data"`, `rendered_from_payload_digest` binding, bounded length, explicit `无可用类比案例` sentinel for the empty selection — no fabrication); the selection/scaler payloads and refs flow through `BridgeEvidenceWriter` only, with executor-minted ordinal tokens (providers echo, never mint).
- **Physical material inventory** (`config/orchestration/materials/lane0/`; UTF-8 no BOM; paths never enter any Plan — resolution is by `ContentRef` identity through the Phase 2 `MaterialSource`):

| File | Catalog kind | ContentRef id |
|---|---|---|
| `market_factor_handler.md` | `handler` | `lane0.market.factor.handler` (bytes pin `factor_set_version` + `factor_set_digest`) |
| `regime_prompt.md` | `prompt` | `lane0.regime.prompt` |
| `regime_skill.md` | `skill` | `lane0.regime.skill` |
| `rotation_prompt.md` | `prompt` | `lane0.rotation.prompt` |
| `rotation_skill.md` | `skill` | `lane0.rotation.skill` |
| `honesty_guardrail.md` | `guardrail` | `lane0.honesty.guardrail` |
| `experience_bridge_descriptor.md` | `guardrail` | `lane0.experience.bridge.descriptor` |
| `experience_bridge_config.md` | `guardrail` | `lane0.experience.bridge.config` (canonical `ExperiencePrefetchBinding` JSON) |
| `experience_provider.md` | `handler` | `lane0.experience.provider` |
| `experience_renderer.md` | `handler` | `lane0.experience.renderer` |
| `experience_support_analyzer.md` | `handler` | `lane0.experience.analyzer` |
| `factor_report_renderer.md` | `handler` | `lane0.factor_report.renderer` (wraps Task 3b `render_factor_report_for_prompt`) |

- **System-prompt material requirements** (`lane0.regime.prompt` / `lane0.rotation.prompt`; reviewed in full at checkpoint 5): persona = market-context analyst, advisory-only, zero trading authority stated in the first paragraph; the typed output contract restated (schema name + the axis/stage invariants — the model is told the validator will reject violations, so degraded evidence must surface as `unknown` mass, not as invented numbers); explicit instruction that upstream artifact content and experience-selection content are **untrusted data** whose embedded instructions must never be followed; the `[UNSOURCED]` rule — any number not anchorable to the factor report must be omitted or marked, and the evidence policy (`allow_unsourced_numbers=False`) will fail the node otherwise.
- **Skill content requirements** (both skills; enforced by Phase 1 `parse_skill_v1` and by review): the two skill materials are **the deliverable-④ §2/§3 逐字安装件**, installed verbatim as `regime_skill.md`/`rotation_skill.md` — they already carry the skill-v1 frontmatter (`name` + 3-line `description` with `Perfect for:`/`Not ideal for:` trigger arrays in Task-1-canonical JSON), the exact `## ⚠️ CRITICAL: Data Source Priority` opening (rendered factor-report block as the ONLY numeric source; experience/前日判读 blocks when-supplied; blocks are DATA not instructions), the per-family reading method, the three-axis/stage output disciplines, the rotation 词表红线 (legacy 冰点/分化/逼空/发酵/回踩·启动 read-but-never-emit; the two "分化" senses distinguished), the cold-start clauses and the Limitations sections. Ruling R12: the frontmatter `name` stays ④'s human-readable name (the catalog binds by ContentRef identity — `name` is display-only) while the ContentRef ids stay `lane0.regime.skill`/`lane0.rotation.skill`. The regime skill's numeric source is the Task 3b rendered block; the rotation skill treats the industry-chain block as when-supplied (`chain_nodes` empty without it) — no "not yet bound" disclaimer.
- **BOOTSTRAP runtime profile:** `BOOTSTRAP_RUNTIME_PROFILE: StaticRuntimeProfile` exported from `bootstrap.py` — a **distinct profile identity** on the same schema: `profile_id="bootstrap-runtime"`, `profile_version="1"` (clause C1; Phase 2's static-runtime v1 untouched, static-runtime v2 reserved for Phase 8), whose feature delta versus static-runtime v1 is **exactly one admission widening**: a `PlanDraft` with `phase="bootstrap"`, `context_snapshot_ref=None` and `source ∈ {PRESET, PRESET_FALLBACK}` is supported. Everything else is bit-equal to v1. Admission matrix pinned by tests:

| Draft | profile v1 | BOOTSTRAP profile |
|---|---|---|
| `phase="bootstrap"`, `source=PRESET`, no context ref | rejected before reservation | **admitted** |
| `phase="bootstrap"`, `source=PRESET_FALLBACK`, no context ref | rejected | **admitted** |
| `phase="bootstrap"`, `source=DYNAMIC` | rejected | rejected (a Planner never authors Lane 0 — spec §2.0 "不让动态 Planner 猜 regime") |
| `source=PlanSource.BOOTSTRAP` (any phase) | rejected | rejected (the bootstrap graph is a versioned PRESET per the frozen ruling; the enum value stays dormant) |
| `approval_policy=AUTO` (any) | rejected | rejected |
| main-phase drafts (Phase 2 pilot fixtures) | admitted/rejected as today | **identical outcomes** (differential test) |
| conditions/reducers/debates/gates/stop-conditions/`max_attempts>1`/multi-writer | rejected | rejected |

  The Phase 2 profile v1 constant, digest and golden are untouched; old admitted Plans re-verify against their original profile digest.

**Required invariants:**

1. every material passes Phase 1 skill-v1/material digest verification (`parse_skill_v1`, `catalog_material_digest`); trigger lines are Task-1-canonical JSON byte-for-byte;
2. WorkerSpec matrix holds (det⇔handler/no-tier; llm⇔prompt+tier; REQUIRED⇔non-empty allowlist; exactly one `primary` output; sorted tuples);
3. the experience provider is reachable only through `CapabilityGateway` with the analyzer-bound summary — a second call (`max+1`) fails before backend I/O;
4. profile admission matrix: bootstrap-preset draft admitted under the new profile and rejected under v1; main-phase drafts behave identically under both (differential test over the Phase 2 pilot fixtures, which Task 0 verified exist);
5. no worker gains `memory.propose` or any write capability (Lane 0 proposes lessons only through Phase 4 attribution → Phase 3 proposal boundary, later phases);
6. the renderer is pure, bounded and sentinel-honest: byte/length overflow fails before prompt assembly (no truncation), the empty selection renders the explicit no-analogy sentinel, and rendered bytes bind to the selection payload digest.

- [ ] **Step 1: Write failing catalog/profile tests**

`test_lane0_catalog.py` matrix:

- three WorkerSpecs pass the Phase 1 matrix (det⇔handler_ref/no-tier; llm⇔prompt+tier; REQUIRED⇔non-empty allowlist; exactly one `primary`; sorted `supported_modes`/allowlists; `can_emit_decision=False`⇔`decision_authority="none"`);
- material resolution round-trip via `build_catalog_snapshot` over the Lane 0 additions: every ContentRef/CapabilityRef resolves with matching kind + digest; an orphan material and a dangling ref each fail in the correct direction;
- skill grammar: wrong heading (missing `⚠️`), non-canonical trigger JSON (pretty-printed array), duplicate trigger entries — each raises `SkillFormatError`; both real skills parse;
- handler material bytes pin the factor set (changing `factor_set_digest` in the material moves the catalog digest);
- bridge triple (descriptor / config / provider / renderer / analyzer) one-to-one coverage; duplicate `bridge_id` rejected; analyzer bounds `max=1, min=1`; a second `begin` (max+1) fails before backend I/O on a fake gateway; empty `ExperienceSelection` still finalizes one successful call;
- no worker holds `memory.propose` or any write capability.

`test_bootstrap_profile.py`: the admission matrix table above, row by row, plus profile-identity distinctness (`profile_id="bootstrap-runtime"`, `profile_version="1"`) from static-runtime v1 and v1-golden untouched assertion.

Run: `pytest tests/orchestration/test_lane0_catalog.py tests/orchestration/test_bootstrap_profile.py -v` — Expected: FAIL.

- [ ] **Step 2: Write materials + specs + profile, register trusted handlers**

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_lane0_catalog.py tests/orchestration/test_bootstrap_profile.py tests/orchestration/test_catalog.py -v` — Expected: PASS (Phase 1 catalog tests untouched).

```bash
git add guanlan_v2/orchestration/bootstrap.py \
  config/orchestration/materials/lane0/market_factor_handler.md \
  config/orchestration/materials/lane0/regime_prompt.md \
  config/orchestration/materials/lane0/regime_skill.md \
  config/orchestration/materials/lane0/rotation_prompt.md \
  config/orchestration/materials/lane0/rotation_skill.md \
  config/orchestration/materials/lane0/honesty_guardrail.md \
  config/orchestration/materials/lane0/experience_bridge_descriptor.md \
  config/orchestration/materials/lane0/experience_bridge_config.md \
  config/orchestration/materials/lane0/experience_provider.md \
  config/orchestration/materials/lane0/experience_renderer.md \
  config/orchestration/materials/lane0/experience_support_analyzer.md \
  config/orchestration/materials/lane0/factor_report_renderer.md \
  tests/orchestration/test_lane0_catalog.py tests/orchestration/test_bootstrap_profile.py
git commit -m "feat(orchestration): lane0 worker catalog + experience bridge + bootstrap runtime profile"
```

---

## Task 9: Phase 5 registry/catalog chain + goldens + guard flips

**Files:**
- Modify: `guanlan_v2/orchestration/bootstrap.py`
- Create: `config/orchestration/materials/lane0/factor_miner_prompt.md` + `factor_miner_skill.md` (#25 placeholder materials — ruling R9)
- Create: `tests/orchestration/golden/phase5_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase5_catalog_manifest_v1.json`
- Modify: `tests/orchestration/test_contract_completeness.py` (guard flips — the only permitted upstream-test edit, clause C4)
- Test: `tests/orchestration/test_phase5_registry.py`

**Consumes:** Phase 4 chain (`PHASE4_REGISTRY_DIGEST`/`build_phase4_registry`, `PHASE4_CATALOG_DIGEST`/`build_phase4_catalog_snapshot`); every Phase 5 registered model from Tasks 1–8. The two bootstrap payload models (`BootstrapPlan`, `BootstrapContextManifest`) are **defined in this task** (contracts + focused construction tests inside `test_phase5_registry.py`) so the registry freeze covers them; Task 10 adds only builders/integration on top of the frozen schemas — the golden changes exactly once.

**Produces:**

- `PHASE5_PUBLIC_MODELS`: exactly (`MarketFactorValue`, `MarketFactorSetSpec`, `MarketFactorReport`, `RegimeReport`, `RotationReport`, `RegimeCase`, `RealizedRegime`, `CaseMatured`, `CaseReviewed`, `RegimeGraderSpec`, `ExperienceScalerSnapshot`, `ExperienceQuery`, `ExperienceSelection`, `ExperiencePrefetchBinding`, `BootstrapPlan`, `BootstrapContextManifest`) — 16 payloads, each `schema_version="1"`. `PHASE5_INTERNAL_MODELS`: reviewed-reason map for `MarketFactorPoint`, `MarketFactorDefinition`, `FactorSummary`, `EvidenceAnchor`, `MainlineRead`, `ExperienceNeighbour` (nested), the three Phase-5 axis enums `TrendState`/`RiskState`/`HeatState` (④§1; Chinese trend values per R6), `DailyValueRow`/`UpDownRow`/`TapePoint`/`MarketFactorInputs`/`PanelAvailabilityRule` (compute carriers), `CaseView`/`SeedReport` (derived views), `ExperienceLog`/`ObservedTradeDateCalendar`/`CaseMaturityPending` (service ports/carriers).
- `PHASE5_BASE_REGISTRY_DIGEST = PHASE4_REGISTRY_DIGEST`; `PHASE5_REGISTRY_DIGEST`; `def build_phase5_registry(expected_phase4_digest: DigestHex) -> SchemaRegistry:` — fresh sealed cumulative registry = Phase 4 public models + `PHASE5_PUBLIC_MODELS`; verifies the Phase 4 manifest/digest first; inherited JSON Schemas byte-identical; rejects any other base digest; golden `phase5_schema_manifest_v1.json` (hand-frozen, reviewed).
- `PHASE5_BASE_CATALOG_DIGEST = PHASE4_CATALOG_DIGEST`; `PHASE5_CATALOG_DIGEST`; `def build_phase5_catalog_snapshot(phase4_snapshot: WorkerCatalogSnapshot, *, lane0_workers: tuple[WorkerSpec, ...], experience_bridge_descriptor_material: ResolvedMaterial, experience_prefetch_binding: ExperiencePrefetchBinding, resolved_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot:` — verifies the base digest, adds exactly the three Lane 0 finals **+ the #25 `market.factor_miner` placeholder WorkerSpec (ruling R9)** + experience capability/bridge materials, rejects any other base; golden `phase5_catalog_manifest_v1.json`. Earlier digests stay resolvable for old Plans but are not alternative Phase 6 bases; no "latest" alias.
- **#25 `market.factor_miner` placeholder assembly (ruling R9 — the WorkerSpec 装配 belongs to Phase 5's catalog chain):** `WorkerSpec(worker_id="market.factor_miner", lane="market", catalog_role="final", selection_scope="static")` — **not** `dynamic_allowed`, so the dynamic Planner structurally can never select it; `can_emit_decision=False`, `decision_authority="none"`, `ExecutionSpec(kind=LLM, model_tier="reasoner")`, empty capability allowlist, `tool_calls=FORBIDDEN`; minimal reviewed prompt/skill materials `lane0.factor_miner.prompt`/`lane0.factor_miner.skill` state the boundary in text: **offline research lane** (never in the bootstrap preset graph nor any daily main DAG — AMEND-3 红线⑤), **draft-only** — primary output pinned to `MarketFactorSetSpec@1` as a battery-revision draft (miner draft → 人审 → registry bump per ①§6; the richer lifecycle-proposal schema belongs to the curator phase), no write capability. 真跑 stays deferred until the experience library matures: this is 占位装配 only — no runtime handler wiring, no graph node, no seeder/e2e participation.
- Package exports: `guanlan_v2/orchestration/market/__init__.py` re-exports the factor/report models; `bootstrap.py` exports the chain constants/builders and `BOOTSTRAP_RUNTIME_PROFILE`; nothing is added to the Phase 1 package-level `__init__.py` lazy surface (Phase 5 symbols are imported from their owning modules, matching the Phase 1 convention that non-payload models are not re-exported at package level).
- **Guard flips** in `test_contract_completeness.py` (pure-addition mechanism established by Phase 4): the five Phase-5 names in `DEFERRED_PHASE_PAYLOADS` (`MarketFactorValue`, `MarketFactorReport`, `RegimeReport`, `RealizedRegime`, `RotationReport`) flip from "absent everywhere" to "exists and is registered in the Phase 5 registry"; the module-enumeration firewall (test_contract_completeness.py:149-166 pattern) gains `guanlan_v2.orchestration.market.factors`, `guanlan_v2.orchestration.memory.experience`, `guanlan_v2.orchestration.bootstrap` under the Phase 5 partition. Phase-6/8 deferred names (`TargetPosition`, `PortfolioTargetProposal`, `TargetPortfolioIntent`, `DecisionSchedule`, `DebateMessage`) stay guarded absent. Phase 1 golden `schema_manifest_v1.json` is not touched.

**Required invariants:**

1. `build_phase5_registry(PHASE4_REGISTRY_DIGEST).registry_digest == PHASE5_REGISTRY_DIGEST` and equals the golden; a wrong base digest raises before any registration;
2. every inherited entry byte-identical to its Phase ≤4 golden bytes;
3. `SchemaRegistryResolver` resolves Phase 1/2/3-data/3-full/4/5 digests simultaneously; a Phase ≤4 Plan replays unreinterpreted;
4. catalog golden lists exactly +4 final workers vs Phase 4 (3 Lane 0 + the #25 placeholder — R9) and `count_final_workers` grows by 4; `compat.*` untouched; the #25 spec pins `selection_scope` non-`dynamic_allowed`, `can_emit_decision=False` and a draft-only primary — the dynamic Planner structurally cannot select it and no graph references it;
5. completeness firewall green with the flipped guards; no Phase 5 public model escapes the partition.

- [ ] **Step 1: Write failing chain tests**

Matrix:

- `build_phase5_registry(PHASE4_REGISTRY_DIGEST)` seals, matches `PHASE5_REGISTRY_DIGEST` and the golden; a wrong/garbage base digest raises before any registration; calling twice yields independent equal instances;
- golden shape: exactly the Phase 4 entry count + 16 new entries, sorted by key, every inherited entry byte-identical to its upstream golden bytes (loaded and compared literally, not recomputed);
- resolver coexistence: Phase 1/2/3-data/3-full/4/5 digests resolve simultaneously; a Plan bound to a Phase ≤4 digest replays without reinterpretation;
- catalog: `build_phase5_catalog_snapshot` verifies the Phase 4 base, adds exactly 4 finals (3 Lane 0 + #25 placeholder; +`count_final_workers` differential), rejects other bases; #25 non-dynamic/draft-only pins asserted; golden reproduction; `compat.*` subset unchanged;
- guard flips: the five Phase-5 names exist, are registered in the Phase 5 registry, and remain absent from Phase ≤4 registries; Phase-6/8 names still absent everywhere;
- module firewall: the three Phase 5 modules are enumerated in the reviewed partition and no Phase 5 public `ContractModel` escapes classification (registered ∪ reviewed-internal is exhaustive and disjoint).

Run: `pytest tests/orchestration/test_phase5_registry.py tests/orchestration/test_contract_completeness.py -v` — Expected: FAIL on missing chain exports / still-guarded payloads.

- [ ] **Step 2: Implement builders; hand-freeze both goldens from a reviewed one-off run** (B11: the goldens reflect the 19-id battery, the ①§2 three-state statuses and the ④-aligned report shapes — still hand-frozen, reviewer-signed)

- [ ] **Step 3: Run and commit**

Run: `pytest tests/orchestration/test_phase5_registry.py tests/orchestration/test_contract_completeness.py tests/orchestration/test_registry_population.py -v` — Expected: PASS.

```bash
git add guanlan_v2/orchestration/bootstrap.py config/orchestration/materials/lane0/factor_miner_prompt.md config/orchestration/materials/lane0/factor_miner_skill.md tests/orchestration/golden/phase5_schema_manifest_v1.json tests/orchestration/golden/phase5_catalog_manifest_v1.json tests/orchestration/test_phase5_registry.py tests/orchestration/test_contract_completeness.py
git commit -m "feat(orchestration): phase5 cumulative registry/catalog chain + deferred-guard flips"
```

---

## Task 10: `bootstrap.py` runtime — fixed BootstrapPlan, admission, ContextSnapshot, derived main RunContext

**Files:**
- Modify: `guanlan_v2/orchestration/bootstrap.py`
- Test: `tests/orchestration/test_bootstrap_plan.py`
- Test: `tests/orchestration/test_bootstrap_e2e.py`

**Consumes:** Phase 1 `OrchestrationRequest`/`PlanDraft`/`validate_plan_draft`/`freeze_plan`/`RunContext`/`ContextSnapshot.build`/`build_empty_memory_binding`/`TypedPayloadRef`; Phase 2 `PlanAdmissionService`/`BudgetLedger` (`BudgetScopeType` includes `"bootstrap"`)/`run_plan`/`EventType.CONTEXT_SNAPSHOT_FROZEN`; Phase 3 `build_data_context`; Tasks 1–9.

**Produces:**

- `BOOTSTRAP_PRESET_ID = "bootstrap.lane0"`.
- (The two payload models below are schema-frozen and registered in Task 9's commit; this task implements the builders and runtime glue against them — their full field contracts are stated here because this is where they are exercised.)
- `class BootstrapPlan(DigestModel)` (registered `BootstrapPlan@1`): `schema_version: Literal["1"] = "1"`, `preset_id: Literal["bootstrap.lane0"] = "bootstrap.lane0"`, `preset_version: NonEmptyStr` (v1: "1"), `factor_set_version: NonEmptyStr`, `factor_set_digest: DigestHex`, `grader_digest: DigestHex`, `experience_k: PositiveInt` (le=20), `node_timeout_sec: PositiveInt` (v1: 300), `budget_request_tokens: NonNegativeInt`, `budget_request_llm_invocations: NonNegativeInt` (v1: 2 — regime + rotation; factor is deterministic and reserves zero), `content_digest: DigestHex` (SELF_DIGEST). This is the fixed, versioned, auditable preset record; changing any field is a new preset version.
- `class BootstrapContextManifest(DigestModel)` (registered `BootstrapContextManifest@1`): `schema_version: Literal["1"] = "1"`, `context_snapshot_digest: DigestHex`, `bootstrap_plan_digest: DigestHex` (the frozen Plan's candidate digest), `bootstrap_run_id: NonEmptyStr` (SEMANTIC_EXCLUDE — audit), `market_factor_report_ref: TypedPayloadRef` (schema pinned to `MarketFactorReport@1`, main namespace), `regime_report_ref: TypedPayloadRef | None` (pinned `RegimeReport@1`), `rotation_report_ref: TypedPayloadRef | None` (pinned `RotationReport@1`), `degradation_badges: tuple[NonEmptyStr, ...] = ()`, `content_digest: DigestHex` (SELF_DIGEST). Validator: each `None` report ref requires a matching badge (`regime_missing`/`rotation_missing`) — the explicit `unknown/degraded` ContextSnapshot of spec §2.0, carried as payload honesty because Phase 1 owns the `ContextSnapshot` schema and is not extended here.
- `def build_bootstrap_plan_draft(preset: BootstrapPlan, *, request: OrchestrationRequest, as_of: UtcDateTime, mode: DataMode, catalog: WorkerCatalogSnapshot, schema_registry_digest: DigestHex, draft_id: LogicalId, run_id: NonEmptyStr) -> PlanDraft:` — pure. Emits the fixed three-node static graph:

| node id | worker_id | writes_slot | dependencies | timeout |
|---|---|---|---|---|
| `lane0.factor` | `market.factor` | `market_factor_report` | — | `preset.node_timeout_sec` |
| `lane0.regime` | `market.regime` | `regime_report` | `Dependency(upstream_node_id="lane0.factor", artifact_slot="market_factor_report", inject_as="market_factor_report", policy=DEGRADE, accept_statuses=frozenset({COMPLETED, DEGRADED}))` | `preset.node_timeout_sec` |
| `lane0.rotation` | `market.rotation` | `rotation_report` | same dependency shape as `lane0.regime` | `preset.node_timeout_sec` |

  A degraded factor report still reaches the LLMs (accept_statuses includes DEGRADED); an outright failed factor node leaves the LLM nodes to run with the input omitted and the guardrail forces all-unknown output. This pairing is lawful only because the Task 8 `market_factor_report` binding is `required=False` — Phase 1 spec.py:1006 `dependency_weakens_evidence` rejects a required input fed by a `policy=DEGRADE` dependency; the honesty contract lives in the guardrail (absence ⇒ all-unknown), never in a required flag the validator would refuse. Draft envelope: `sink_node_ids=("lane0.regime","lane0.rotation")`; `phase="bootstrap"`, `source=PlanSource.PRESET` (the frozen ruling: the bootstrap graph is a versioned preset; `PlanSource.BOOTSTRAP` is not used), `context_snapshot_ref=None`, `approval_policy=REQUIRED` (AUTO impossible), `max_attempts=1` everywhere, params `{}` on every node (the factor set is pinned in the handler material, the retrieval k in the bridge config — no hidden authority through params), budget fields from the preset, `catalog_version`/`catalog_digest`/`schema_registry_digest` bound to the Phase 5 chain, legacy tuple all-None (clause C3).
- `def build_bootstrap_run_context(*, run_id: NonEmptyStr, data: DataContext, budget: RunBudget, cancellation_token_id: NonEmptyStr) -> RunContext:` — `context_snapshot_id=None`, `memory_snapshot_hash` = the canonical empty-memory hash from `build_empty_memory_binding()`. Immutable by construction (frozen model).
- `def build_context_snapshot_from_bootstrap(*, run_result: RunResult, pool: ArtifactPool, data_context: DataContext, payload_store: PayloadStore, registry: SchemaRegistry, clock: AuthoritativeClock) -> tuple[ContextSnapshot, BootstrapContextManifest]:` — persists the canonical empty-memory pair (typed refs), builds `ContextSnapshot` via the Phase 1 builder (`runtime_requirements_ref=None` — lawful because memory is canonically empty), reads the committed Lane 0 artifacts from the pool (`committed_output(node_id, "primary")`), persists nothing twice (refs point at the pool's committed payloads), assembles the manifest with badges from the run's NodeRun statuses (DEGRADED nodes ⇒ `*_degraded` badges), and appends `CONTEXT_SNAPSHOT_FROZEN` + the manifest payload in one UoW on the bootstrap run's stream.
- `def derive_main_run_context(bootstrap_ctx: RunContext, *, snapshot: ContextSnapshot, main_run_id: NonEmptyStr, budget: RunBudget, cancellation_token_id: NonEmptyStr) -> RunContext:` — a **new** `RunContext` with `context_snapshot_id=snapshot.snapshot_id`, `memory_snapshot_hash=snapshot.memory_snapshot_hash`, same `DataContext` (same frozen `as_of` — every main worker sees the one snapshot universe, spec §2.0), `replays_run_id=None`; refuses (`ValueError`) a snapshot whose `data_context` differs from the bootstrap context's (a drifted-as_of derivation is a bug, not a feature); the bootstrap context object is never modified (frozen — asserted anyway).
- `def append_regime_case_from_run(*, run_result: RunResult, pool: ArtifactPool, log: ExperienceLog, spec: MarketFactorSetSpec, clock: AuthoritativeClock) -> RegimeCase | None:` — lifecycle step ④: after commit, deterministically builds the day's `RegimeCase` from the committed `RegimeReport` artifact (the ④-aligned Task 2 shape — `factor_report_digest`/`evidence`/`conflicts`/`analog_case_ids` — embedded unchanged) + the factor report's `feature_vector`/`feature_coverage`/`missing_features` + the `scaler_digest` carried in the run's `ExperienceSelection` evidence payload; `id = f"rc.{feature_schema_version}.{yyyymmdd}"`, `available_at` per the Task 7 convention; `links` carries the producing artifact ids (`artifact:{artifact_id}` entries) so the case is traceable to its evidence chain; returns `None` with no append when the regime artifact is absent (badged in the manifest instead — no case without a judgment). This is service code, not worker code: the LLM never writes the experience store; it only produced the judgment artifact the service folds in.

**Required invariants:**

0a. budget: the bootstrap plan reservation flows through the one Phase 2 `BudgetLedger` (reserve → settle/release events, bound to request + candidate digest); the deterministic `lane0.factor` node reserves zero LLM invocations, the two LLM nodes one each; the `"bootstrap"` `BudgetScopeType` value is used for the bootstrap-level envelope where the implemented ledger exposes scope typing (clause C1's sibling — adopt the reviewed ledger surface);
0b. the `CONTEXT_SNAPSHOT_FROZEN` event and the manifest payload commit in one UoW on the bootstrap run's main partition; replaying the append is idempotent; the event's `payload_ref` dereferences to the exact manifest;
1. the draft passes Phase 1 `validate_plan_draft` (bootstrap⇒no context ref) and the full Phase 2 admission order under `BOOTSTRAP_RUNTIME_PROFILE`: validation → support report → same-digest reservation → REQUIRED approval → freeze → `PlanAdmitted` → dispatch; AUTO and unapproved dispatch fail;
2. e2e (fake gateways, in-memory stores): deterministic factor handler + scripted LLM gateway ⇒ `run_plan` commits three artifacts; ContextSnapshot + manifest build; `derive_main_run_context` yields a main context whose `context_snapshot_id` resolves; a main-phase `PlanDraft` binding that snapshot passes validation — the full spec §2.0 sequence `Request → BootstrapPlan → ContextSnapshot → main RunContext`;
3. degraded path: factor inputs emptied ⇒ factor node DEGRADED/UNAVAILABLE-heavy report ⇒ scripted regime output is all-unknown LOW (guardrail contract) ⇒ manifest carries badges — 先 unknown/degraded 诚实运行 (spec §12.5), no fabricated regime;
4. LLM-failure path: regime node FAILED ⇒ manifest `regime_report_ref=None` + badge; ContextSnapshot still commits (the dynamic Planner downstream must see honest absence, never a guessed regime);
5. same-day bootstrap rerun: `append_regime_case_from_run` is idempotent (same case id/content replays; drifted judgment content conflicts loudly);
6. red-line regression: no Lane 0 output schema is decision-class; the bootstrap run reserves budget under the ledger with deterministic-zero LLM reservation for the factor node; no code path registers anything on a real order/signal bus (grep-level assertion on module imports).

- [ ] **Step 1: Write failing plan/e2e tests**

`test_bootstrap_plan.py` matrix: preset model construction/golden-style digest stability; draft passes `validate_plan_structure` + `validate_plan_draft` against the Phase 5 catalog; wrong catalog digest ⇒ validation issue; AUTO draft ⇒ `auto_approval_rejected`; a tampered node (extra param, fourth node, changed dependency policy) moves the candidate digest (fixed-preset auditability); `build_bootstrap_run_context` yields `context_snapshot_id=None` + canonical empty-memory hash.

`test_bootstrap_e2e.py` scenarios (fake gateways, in-memory Phase 2 stores, deterministic clock):

1. **happy path**: admission (validate → support under `BOOTSTRAP_RUNTIME_PROFILE` → reserve → approve → freeze → `PlanAdmitted`) → `run_plan` → three committed artifacts → `build_context_snapshot_from_bootstrap` → manifest with three refs, no badges → `derive_main_run_context` → a main-phase `PlanDraft` binding the snapshot passes Phase 1 validation; prompt honesty (Task 3b/B8): the fake gateway's captured prompts for both LLM nodes contain the rendered factor-report block (header incl. the `battery_digest` prefix) and never the raw `MarketFactorReport` typed-payload JSON; every `EvidenceAnchor.factor_id` in the committed regime/rotation artifacts exists in the bound factor report, and each artifact's `factor_report_digest` equals the injected report's `content_digest`;
2. **degraded factors**: emptied inputs ⇒ UNAVAILABLE-heavy report, still injected through the DEGRADE dependency ⇒ scripted regime output all-unknown LOW ⇒ badged manifest; ContextSnapshot still commits; a variant with the factor node outright FAILED omits the optional (`required=False`) input entirely and the scripted guardrail-conformant output is still all-unknown LOW;
3. **regime failure**: scripted gateway exception on `lane0.regime` ⇒ `regime_report_ref=None` + `regime_missing` badge; rotation ref intact (its own optional input binding unaffected);
4. **experience wiring**: the scripted run records exactly one finalized `experience.retrieve` ToolCallRecord per LLM node with the analyzer-bound token; empty store ⇒ empty selection, still COMPLETED;
5. **case append**: step ④ appends the day's `RegimeCase` with the run's `scaler_digest`; rerun same day idempotent; drifted judgment conflicts;
6. **unapproved dispatch**: skipping approval ⇒ dispatch refused; budget reservation released on rejection;
7. **red lines**: no decision-class schema among outputs; deterministic node reserved zero LLM invocations; module-import assertion (no order/signal-bus symbol reachable from `bootstrap.py`).

Run: `pytest tests/orchestration/test_bootstrap_plan.py tests/orchestration/test_bootstrap_e2e.py -v` — Expected: FAIL on missing builders.

- [ ] **Step 2: Implement builders + integration glue**

Implementation order: contracts (`BootstrapPlan`/`BootstrapContextManifest` were registered in Task 9 — only builders remain) → draft builder → run-context builders → snapshot assembly → case append hook → e2e wiring. The e2e composes only public Phase 2/3/5 surfaces; if a needed seam is missing upstream, that is a Task 0 correction-clause event, not a local shim.

- [ ] **Step 3: Run the full orchestration suite and commit**

Run: `pytest tests/orchestration -v` and `python -m compileall -q guanlan_v2/orchestration`; if Ruff is available: `ruff check guanlan_v2/orchestration tests/orchestration`.

Expected: PASS (net growth; zero upstream regressions).

```bash
git add guanlan_v2/orchestration/bootstrap.py tests/orchestration/test_bootstrap_plan.py tests/orchestration/test_bootstrap_e2e.py
git commit -m "feat(orchestration): fixed versioned BootstrapPlan runtime + derived main RunContext"
```

---

## Out of Scope (deferred with named owners — building any of these here is a defect)

- **Dynamic Planner consumption** of `ContextSnapshot`/`BootstrapContextManifest` (worker 配比, per-worker injection) — Phase 7.
- **Evaluator regime-stratified reporting** (spec §3.0 consumption ④: returns/drawdown/calibration by frozen regime, no mechanical discounting for "wrong" labels) — Phase 4/9 evaluator surface consuming Phase 5's `RealizedRegime`.
- **Factor/threshold parameter optimization** (validation IC / hit-rate / stratified robustness over matured cases, `family_id`/TrialLedger/sealed holdout) — Phase 4 `run_optimize`; Phase 5 ships golden-frozen provisionals only.
- **Attribution + Reflexion proposal for cases** (lifecycle step ⑥) — Phase 4 `evaluator`/`governor` + Phase 3 proposal boundary; Phase 5 ships only the admin-gated `CaseReviewed` append (step ⑦).
- **Market-temp shield rewiring** to consume the Lane 0 projection, and PM/仓位 feeding — Lane D phase; Phase 5 ships the projection + parity tests (D5).
- **market_tape/fundflow snapshot-archive small deliverable (①§5)** that would eventually flip the UNAVAILABLE factors (the six archive-dependent rot factors + 炸板率/北向/主力分位 historical series) — the named owner is that archiving deliverable, 建议与事件库小 phase 同批 (the macro monthly-rotation `72573b8` pattern is the precedent); after its start these factors surface as short-series `DEGRADED` with `first_date` displayed — never backfilled retroactively, never fabricated. The index-valuation percentile upstream for `val.pct` is a separate stocks-layer new-source deliverable (R5).
- **Canvas `run_graph` node registration** for market factors (D4) — later reviewed 薄扩展 of the workflow layer.
- **Industry-chain framework material** for `market.rotation` and the remaining 21 workers' migration — Phase 8.
- **`PortfolioTargetProposal`/`TargetPortfolioIntent`/`DecisionSchedule`/`DebateMessage`** — Phases 6/8; their absence guards stay green.
- **Real vendor scrapers / durable cross-process store backends** — later reviewed work (Phase 3's stubs remain the boundary).

---

## Phase 5 Exit Gates

Phase 5 is complete only when every gate below is checked by tests and reviewed artifacts.

### Upstream handoff and chain integrity

- [ ] every Phase 1 (amended), Phase 2, Phase 3 and Phase 4 Exit Gate remains green; no upstream source/test/golden overwritten (sole permitted edit: the Task 9 guard flips in `test_contract_completeness.py`);
- [ ] Phase 5 imports, rather than redefines, digests/refs/`TypedPayloadRef`/plan validation/freeze/event types/calendar protocol;
- [ ] `PHASE5_REGISTRY_DIGEST`/`build_phase5_registry(expected_phase4_digest)` and `PHASE5_CATALOG_DIGEST`/`build_phase5_catalog_snapshot(...)` verify their exact Phase 4 bases, reject others, reproduce their own goldens, and inherit upstream schemas byte-identically; no "latest" alias exists;
- [ ] the five deferred Phase-5 payload guards are flipped to presence-in-phase5-registry; Phase-6/8 deferred names remain guarded absent;
- [ ] `EventType` is unchanged — `CASE_CREATED`/`CASE_MATURED`/`CASE_REVIEWED` are consumed, not re-added.

### Market factors and PIT honesty

- [ ] every `MarketFactorValue` carries the spec metadata fields (`value`/`effective_at`/`status` included) under exact spec spellings plus the ①§2 alignment fields (`family`/`series`/`summary`/`n_days`/`first_date`/`provenance`/`reason`); `status` is the closed ①§2 literal `OK|DEGRADED|UNAVAILABLE` (DEGRADED reason-carrying); `UNAVAILABLE` structurally excludes series points/non-None `value`/`summary`/nonzero coverage; zero-fill impossible;
- [ ] a future `available_at` row raises `FutureDataRefused` and never falls through;
- [ ] the v1 factor set golden pins the 19 factor ids (①§3's 17 battery rows, composite rows split per series — R3) with reviewed params; 炸板率 uses `break_rate = zb/(zt+zb)` (never `break_ratio`/`promotion_rate` — fixture-pinned); `max_streak` comes from per-stock `limit_days`, never zt_stat `ct`;
- [ ] unit pins hold (`main_net` 元→亿 at the loader, `north_net` 亿 pass-through) and the availability rule is versioned into every value's params;
- [ ] `market_tape` snapshot contributes only a same-day non-backfilled point; history never fabricated from snapshots;
- [ ] compute is deterministic and truncation-invariant; the compute core is import-pure (no `screen`/`datafeed`/`macro`/`strategy` imports);
- [ ] the factor report reaches the Lane 0 LLMs only through the Task 3b rendered untrusted block (header digest-bound; UNAVAILABLE factors rendered as explicit lines, never silently omitted; overflow refuses, no truncation);
- [ ] shield projection parity holds bit-for-bit against `build_market_temp` fixtures and no shield constant/coefficient/wiring changed.

### Experience library, retrieval and grading

- [ ] `RegimeCase`/`CaseMatured`/`CaseReviewed` fold to pending/matured/reviewed views by events only; PIT filter precedes every fold/rank; no in-place mutation;
- [ ] `RegimeReport` carries the ④-aligned shape (`trend_probabilities`/`risk_probabilities`/`heat_probabilities` over the `TrendState`/`RiskState`/`HeatState` enums — Chinese trend values per R6, modal `trend`/`risk_state`/`heat_state`, `factor_report_digest`, `evidence` anchors ≥1, `conflicts`/`analog_case_ids`, `drivers`; no `confidence_score` float); axes carry axis-specific labels only, each summing to 1±1e-8; modal fields equal their axis argmax; unknown is coverage/evidence-gated and modal-unknown forces LOW confidence;
- [ ] the scaler is fitted only on `available_at <= fit_as_of` cases and versioned; retrieval requires matching `feature_schema_version`, handles missing features explicitly, and is deterministic;
- [ ] grading is deterministic and LLM-free, cites-and-copies the bar-count/position maturity idioms, uses the observed-trade-date calendar (holiday-gap fixture green), returns `CaseMaturityPending(resume_after, wakeup_key)` for unmatured cases, and appends idempotently with data-driven `available_at`;
- [ ] only fully matured/reviewed cases can enter validation/distillation (the `_pair_matured` gate transplanted);
- [ ] cold-start seeding is time-ordered, idempotent, wall-clock-independent, and the **future-case invariance triple** (views digest, scaler digest, selection digest at a past probe date) is byte-identical after seeding a later year;
- [ ] `CaseReviewed` requires an existing maturity event and a fail-closed admin verifier — workers cannot reach the reviewed append.

### Bootstrap admission and runtime

- [ ] `BOOTSTRAP_RUNTIME_PROFILE` is a distinct reviewed profile identity (`profile_id="bootstrap-runtime"`, `profile_version="1"`); Phase 2 static-runtime v1 constant/golden untouched; bootstrap drafts rejected under v1 and admitted under the new profile; main-phase behavior identical under both;
- [ ] the fixed `BootstrapPlan` preset emits `phase="bootstrap"`, `source=PRESET`, `context_snapshot_ref=None`, REQUIRED approval; AUTO and `PlanSource.BOOTSTRAP` stay rejected; the full validate→support→reserve→approve→freeze→admit→dispatch order holds;
- [ ] the bootstrap `RunContext` has `context_snapshot_id=None`; the main `RunContext` is a newly derived object referencing the committed `ContextSnapshot`; no field backfill on the original;
- [ ] the bootstrap reservation is candidate-digest-bound through the one `BudgetLedger`; the deterministic node reserves zero LLM invocations; rejection releases the reservation;
- [ ] `CONTEXT_SNAPSHOT_FROZEN` + manifest commit atomically and idempotently;
- [ ] missing/failed LLM outputs produce a badged manifest with `None` refs — explicit unknown/degraded, never a guessed regime; the e2e degraded path runs to a committed ContextSnapshot;
- [ ] the day's `RegimeCase` is appended deterministically from committed artifacts (step ④), idempotent across reruns;
- [ ] Lane 0 catalog: three final workers with complete reviewed prompt/SKILL/guardrail/handler materials; experience retrieval flows only through `CapabilityGateway` under analyzer-verified bounds (max 1, min 1; empty selection = honest success);
- [ ] the #25 `market.factor_miner` placeholder is registered per R9 (`selection_scope` non-`dynamic_allowed`, offline research lane, draft-only primary, zero graph participation); its真跑 stays deferred until经验库成熟 (AMEND-3).

### Determinism and replay

- [ ] every Phase 5 payload is a strict/frozen/extra-forbid `DigestModel` with closed `schema_version` and verified self-digests; audit-only facts (`bootstrap_run_id`, wall-clock `matured_at`, event `occurred_at`) never move semantic digests (differential tests);
- [ ] the factor report, scaler snapshot, selection, realized regime and manifest are all bit-reproducible from the same inputs under two different injected clocks;
- [ ] every experience append is one all-or-none UoW; crash/replay leaves no payload-only or event-only orphan; dual cursors recover;
- [ ] the four Phase 5 goldens (`market_factor_set_v1.json`, `regime_grader_policy_v1.json`, `phase5_schema_manifest_v1.json`, `phase5_catalog_manifest_v1.json`) are hand-frozen, reviewed, reproduced by tests and never written from test code.

### Spec traceability (owned sections → evidence)

- [ ] spec §2.0 bootstrap paragraphs — fixed frozen auditable BootstrapPlan; unknown/degraded ContextSnapshot on missing data; `context_snapshot_id=None` → derived main RunContext; Planner never guesses regime (Task 10 e2e scenarios 1–3 + profile matrix row `source=DYNAMIC`);
- [ ] spec §3.0 — three Lane 0 workers with the table's exact ids/kinds/outputs; `market.factor` deterministic, not LLM-transcribed; 温度护盾显式上游 projection (Tasks 3/8/10);
- [ ] spec §5 / 交付物①§3 — the 17-row battery mapped onto the 19 frozen factor ids (R3 splits) with the line-378 metadata; coverage-driven UNAVAILABLE; scaler fitted pre-query and versioned; 参数不拍脑袋 deferred to the Phase 4 loop with golden provisionals (Tasks 1/3/5, D10);
- [ ] spec §6.4 — verbatim field names; lifecycle ①–⑤/⑦ implemented, ⑥ deferred to Phase 4 by name; numeric-NN-no-embedding; same-`feature_schema_version`; future-case-invariance acceptance (Tasks 4–7);
- [ ] spec §12.5 — "先 unknown/degraded 诚实运行" is the Task 10 degraded e2e scenario, green.

### Red lines and scope protection

- [ ] no Lane 0 worker can emit a decision-class schema; `can_emit_decision=False` everywhere; nothing registers on a real order/signal bus;
- [ ] workers propose-never-write: no memory/skill/code write capability granted; lesson acceptance is human/curator-gated;
- [ ] `workflow/executor.py`, `engine/financial_analyst/**`, `screen/market_temp.py`, `datafeed/market_tape.py`, `macro/*`, `strategy/*` are untouched by Phase 5 — pathspec audit over the Phase 5 commit range (`git log --name-only <task0-sha>..<task10-sha> -- <paths>` is empty: no Phase 5 commit touches these paths; concurrent sessions may lawfully edit them on the shared branch), with the behavior-parity tests (shield projection, 口径/unit pins) as the real check; no canvas node type added;
- [ ] legacy migration adapters untouched (`migrate_rotation_stage` stays UNMAPPABLE);
- [ ] no Phase 6+ contract smuggled in (`PortfolioTargetProposal`/`TargetPortfolioIntent`/`DecisionSchedule`/`DebateMessage` absent); no dynamic Planner; no real vendor scraper;
- [ ] unrelated worktree changes are not staged (explicit pathspec on every commit).

---

## Execution Handoff

Commit map (one commit per task, explicit pathspec, messages fixed):

| Task | Commit message |
|---|---|
| 0 | `test(orchestration): gate phase5 on phase1-4 contracts` |
| 1 | `feat(orchestration): market factor contracts + frozen v1 factor set` |
| 2 | `feat(orchestration): regime/rotation report contracts with honest unknown gating` |
| 3 | `feat(orchestration): deterministic PIT market-factor compute core + honest UNAVAILABLE` |
| 3b | `feat(orchestration): factor-report rendering contract (untrusted block for lane0 prompts)` |
| 4 | `feat(orchestration): PIT event-folded RegimeCase experience store` |
| 5 | `feat(orchestration): versioned PIT scaler + numeric nearest-neighbour case retrieval` |
| 6 | `feat(orchestration): delayed deterministic regime grader on observed trade-date calendar` |
| 7 | `feat(orchestration): time-ordered cold-start seeding with future-case invariance` |
| 8 | `feat(orchestration): lane0 worker catalog + experience bridge + bootstrap runtime profile` |
| 9 | `feat(orchestration): phase5 cumulative registry/catalog chain + deferred-guard flips` |
| 10 | `feat(orchestration): fixed versioned BootstrapPlan runtime + derived main RunContext` |

Implement in task order. Mandatory review checkpoints (each reviews the commit range of its tasks, `<sha>~1..<sha>` per commit):

1. after Task 0 — exact imported upstream ABI/goldens and the recorded correction-clause resolutions (C1–C5);
2. after Tasks 1–3b — factor contracts, the reviewed v1 factor-set golden (reviewer signs off the 19 definitions/params table), PIT compute honesty, the ①§4 rendering contract, 口径/unit pins and shield parity;
3. after Tasks 4–5 — event-folded case store, UoW/idempotency evidence, PIT scaler/NN retrieval and the retrieval-side invariance test;
4. after Tasks 6–7 — the D3 calendar decision as implemented, grader golden sign-off, and the recorded before/after bytes of the future-case-invariance triple;
5. after Tasks 8–9 — Lane 0 materials (reviewer reads both skills and the guardrail in full), BOOTSTRAP profile admission matrix, and the frozen Phase 5 registry/catalog goldens with their guard flips;
6. after Task 10 — bootstrap end-to-end incl. both degraded honesty paths, red-line regression, full-suite green (`pytest tests/orchestration -v` output attached).

Do not begin the Phase 6 shadow-consumer plan (or any consumer of `ContextSnapshot`-derived main runs) until every Phase 5 Exit Gate is checked with test evidence. No execution method requires a particular optional skill package.
