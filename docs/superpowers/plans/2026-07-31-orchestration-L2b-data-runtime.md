# Orchestration L2-b · 生产数据运行时(the production DataRuntimeWorld)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task brief extracted from this plan must be executable standalone: file paths, interfaces and commit pathspecs are in the task section itself.

**Charter:** `docs/superpowers/specs/2026-07-29-post-p10-refreeze-design.md` §1.5 layer **L2-b** (recon facts F/G/H) + §1.6 fact I + §2.2 item 9. Base: branch `report-evidence-pack` == `main` == `0c601b5`, `tests/orchestration` foreground baseline **5645 passed 1 xfailed** (per `.superpowers/sdd/task-pm-two-bridges-report.md`; re-measure at Task 0 — the branch has moved since that report and concurrent sessions may move it again).

**L2-a disposition:** L2-a (Lane-0 production driver) delivered 2026-07-31, see `.superpowers/sdd/task-L2a-lane0-driver-report.md` — this plan consumes it.

**Goal:** `DataRuntimeWorld` is today constructed **only** in `tests/orchestration/data/test_runtime_integration.py`, and `data_runtime_provider_factory` (`guanlan_v2/orchestration/data/runtime.py:690`) has **zero production callers** (recon fact F, re-verified 2026-07-31 by the AST pin `test_data_catalog.py::test_the_data_bridge_provider_has_no_production_caller`). This plan builds the production world: (1) a deterministic **production data-world recipe** — sealed source registry / routing / source-config / calendar / policy resolver whose digests are reproducible across processes; (2) the reviewed **source adapter** registered under the sealed `guanlan.datafeed` identity, wrapping the existing reviewed `LiveClientSource` (`adapters/live_data.py`) which wraps `guanlan_v2/datafeed/live_client.py` — adapters WRAP reviewed readers, never re-implement them; (3) the **per-run world resolver + production data provider** bound into `live_decide.build_production_bindings` under the exact sealed `bridge.data_runtime.provider@1` identity, superseding — per the Task-4 correction clause — whichever provider is registered at that identity at landing time (under the declared L1→L2-b order: `WorldlessDataBridgeProvider`, L1's successor to the retired `StructurallyDeadRowDataProvider`), **plus the L1↔L2-b integration seam (Task 5): the per-run subject view's override target becomes the subject-bound production factory and the real session gains the run-subject param source**; (4) the **Lane-0 driver's DataContext upgraded** from `presets.pilot_data_context` (placeholder `"a"*64` digests, `presets.py:168-177`) to the recipe's real quartet, so the deep lane's context-binding verification (`data/runtime.py::_DataRuntimeBridgeSession._verify_context_binding`) can ever pass; (5) the two **pv aux nodes** carried past `bridge_execution_error` to an honest catalog-licensed outcome; (6) a **real-machine live verification** — the campaign of 2026-07-29..31 found 13 defects invisible to a 5600-green tree; this plan does not close without its own live run.

**What this plan is NOT:** it moves **zero sealed goldens**. It does not touch `_REVIEWED_INTEGRATION_GRANTS` (`data/catalog.py:97-99` — that is L3), does not add `ParamBinding` source kinds or rewrite the sealed `dec.pm` bindings (`data/catalog.py:519-524` — L1 ruling D-0 healed them out-of-band, bytes untouched), and does not mint any new `DataMethodSpec` id.

**Dependency on L1 — declared order, enumerated collisions (seam-review finding 3, 「控制器已裁」):** L1 → L2-b is the declared execution order, released as **ONE train**: this plan starts on a tree where L1 has landed (Task 0 verifies it; L1 was verified live on 9998 only), and 9999 deploys only after THIS plan lands (Task 9) — no honest-refusal production window. The concrete L1 collisions this plan carries, owned task-by-task:

- **Provider class:** L1 retired `StructurallyDeadRowDataProvider` and registered `WorldlessDataBridgeProvider` in its place. Task 4 therefore supersedes **whichever provider is registered at `phase3_data_surface().provider_ref` at landing time** — under the declared order that is `WorldlessDataBridgeProvider` / `register_worldless_data_provider`; Task 4's correction clause names both incumbents.
- **Helper deletion:** `_row_is_structurally_dead` and `structurally_dead_row_fact` were deleted by L1 together with the dead-row class — their fate was decided ONCE, in L1. Nothing in this plan may load-bear on them or resurrect them; post-L1 the "structurally dead row" category does not exist.
- **pm shape pins:** `test_pm_two_bridges.py`'s pm behavioural pins arrive here already flipped by L1 to the worldless shapes (subject-bound refusal / runner-seam refusal). Tasks 4–5 flip them again — order-conditionally, guarded on the registered provider class — to the real-read semantics.
- **The unowned seam, now owned:** L1 left the per-run `_SubjectScopedFactories` view overriding the sealed provider ref with the worldless factory, and the real `_DataRuntimeBridgeSession` without a subject source. **Task 5 (the L1↔L2-b integration seam)** owns both flips, including the conscious flip of L1's Test-5 source-text pin.

**Tech stack:** Python ≥3.11, Pydantic v2, `pytest`. All new modules `from __future__ import annotations`. Run tests from repo root `G:\guanlan-v2`. The full `tests/orchestration` foreground run takes ~15 min and exceeds the 10-min tool cap — run it as the two deterministic halves the pm-two-bridges task used, and reconcile totals with `--collect-only`.

## Global Constraints

These extend, never override, the nine phases' constraints, the P10 plan's Global Constraints, and the re-freeze charter §3.

- **Zero sealed golden movement.** The following must be byte-identical before and after every task (probe-script them, the fix-pm-dead-binding idiom): `tests/orchestration/golden/data_catalog_manifest_v1.json` (provider `3a1e727b…`, analyzer `3f93fdf2…`, prefetch `0c9d38a7…`, descriptor `79706448…`, binding_digest `96d24137…`, catalog digest `ba708692…`), `data_source_manifest_v1.json`, `data_schema_manifest_v1.json`, the chain goldens through `9e73ddf6`/`0c48db78`, and every phase manifest. This plan adds at most **new additive** goldens; it regenerates none.
- **The sealed identity is the registration key, never new bytes.** The production provider registers under `phase3_data_surface().provider_ref` (the byte-frozen `_PROVIDER_BYTES` handler material, `data/catalog.py:401-403`) — the same key the incumbent registration uses (at the 0c601b5 base: `register_structurally_dead_row_data_provider`, `data/runtime.py:910-923`; at landing time under the declared L1→L2-b order: L1's `register_worldless_data_provider`). Handler material bytes stay frozen; rulings live in code (the C3 discipline).
- **Adapters wrap reviewed readers.** The only ways bytes reach a `RawFetch` are the existing `guanlan_v2/datafeed/live_client.py` facade (via the existing `adapters/live_data.py::LiveClientSource`) and — post-L3, out of this plan — the PIT stores via `adapters/replay_data.py`. No new vendor client, no re-implemented probe, no fabricated row, no fabricated `available_at` (PitGuard is the single PIT authority; `LiveClientSource` already forwards `pulled_at` or `None`).
- **Honest typed refusal over silent fallback.** Source down ⇒ the Phase-3 dispatch taxonomy decides (`data/registry.py` module docstring): `NotConfiguredError`/`RateLimitError` advance the frozen chain; optional-method exhaustion ⇒ named `UNAVAILABLE` result; core-method exhaustion ⇒ raise. `cache_or_invoke` rows degrade per that matrix and **never fabricate**. Nothing in this plan converts a refusal into silence.
- **Refuse BEFORE the lease draw.** The 2026-07-31 campaign burned an `ApprovalLease` on a post-admission construction failure (the analyzer-map defect, `live_decide.py:1128-1136` comment). Any new structural precondition this plan introduces (stale pilot context, recipe/world digest drift) must refuse in `make_orchestrated_decide` **before** `register_and_try_lease`, or at binding construction — never after a lease is consumed.
- **TDD, RED first.** Every task writes failing tests, runs the focused command, records the failure shape, then implements. **Mutate→red→revert** for every load-bearing guard (row-count partition, digest verification, backend staging, pre-flight refusal, the integration-seam subject echo); each mutation's red must be the guard's own test, then revert byte-identical and re-run green.
- **Explicit pathspec commits.** The working tree permanently carries concurrent sessions' work: never stage `guanlan_v2/console/`, `guanlan_v2/datafeed/`, `guanlan_v2/glmcp/`, `guanlan_v2/server.py`, `ui/`, `tests/test_console_tools.py`, `tests/test_datafeed_*`, `tests/test_guanlan_mcp.py`, `.data/wisdom/`, `docs/README.md`, `p6-rerank-badges.jpeg`, `guanlan_v2/fundflow/`, `guanlan_v2/strategy/`. `git add -A` / `git add .` / `git commit -a` are forbidden. Exception: Task 7's driver change touches `guanlan_v2/orchestration/lane0_driver.py` only — still by explicit path.
- **LLM 零买卖.** Nothing in this plan gives any model a write capability; every data method spec is `read_only=True` by frozen contract (red line §10, `adapters/live_data.py:57-60`). Tasks 0–8 consume **zero** LLM tokens. Task 9 (live verification) is token-authorized.
- **Production launch discipline.** Production runs `server.py` as a *script* — a module-level `import guanlan_v2.*` anywhere on the server import path breaks 9999 while the whole suite stays green (watchdog-9999 pit ④, and the R23/R24 incident). This plan's imports live inside orchestration modules loaded lazily by `build_production_bindings`; any task that nevertheless touches the server path must re-run `tests/test_server_script_launch.py`. Verify on **9998 first**, then restart 9999.
- **Count honesty.** No test pins today's suite total as a literal; baselines are re-measured, deltas asserted.

---

## Task 0: Recon gate — pin the upstream ABIs this layer stands on (mandatory before Task 1)

The recon behind this plan verified most seams at source, but four ABI details were NOT fully read and each one shapes an interface below. This gate is an executable consumer test in the fix-pm-dead-binding idiom: pin facts with `file:line` evidence, and **correct this plan's interfaces to the implemented names before any later task writes code** (binding correction clauses D-A..D-E below).

**Files:**
- Create: `tests/orchestration/test_l2b_handoff.py`

- [ ] **Step 1: Write the executable gate.** It must prove, against the real modules (no fakes):
  1. **Fact F still holds:** `data_runtime_provider_factory` has no production caller (re-run the AST-walk idiom of `test_data_catalog.py::test_the_data_bridge_provider_has_no_production_caller` — this plan flips it at Task 4, so record the pre-flip state here); **and the L1 landing state (the declared-order gate):** `WorldlessDataBridgeProvider` is the registered factory at `phase3_data_surface().provider_ref`, `StructurallyDeadRowDataProvider` / `_row_is_structurally_dead` / `structurally_dead_row_fact` exist nowhere outside docs/reports (grep), and the pm pins are L1's flipped worldless set. If instead the pre-L1 incumbent is found, the declared one-train order was broken — STOP and escalate; Task 4's correction clause (both incumbents named) then governs the supersede;
  2. **The route-equality obligation:** for every row in `phase3_data_surface().prefetch_binding.operations`, the row's `frozen_route` is exactly one `RouteEntry(source_ref=surface.source_ref, capability_ref=spec.capability_ref)` where `surface.source_ref` is `guanlan.datafeed@1` with the surface descriptor digest (`data/catalog.py:466-480, 511-525`). Therefore any production registry whose `default_route(method).entries` differ is refused by `_DataRuntimeBridgeSession._frozen_route_for` (`data/runtime.py:534-554`) — pin this by driving `_frozen_route_for` against a deliberately-different registry and asserting the `DataRuntimeError`;
  3. **The context-equality obligation:** `_verify_context_binding` (`data/runtime.py:514-524`) compares the admitted `ContextSnapshot.data_context` for **full equality** with `world.ctx` — pin with a one-field-different context;
  4. **The `LiveClientSource` echo:** `LiveClientSource.fetch` builds `RawFetch` with `source_ref=scope.frozen_route.entries[0].source_ref` (`adapters/live_data.py:408-414`) — i.e. it can lawfully serve under the sealed `guanlan.datafeed` identity; and it serves exactly `{verified_snapshot, news, ohlcv}`, raising `RoutingConfigurationError` for the other four method ids (`:350-356`);
  5. **The BJ-920 identity gate (inherited defect, L1 Task 1 review Important #1 — MUST be green before any world binds):** the orchestration fork's `data/symbols.py::normalize_symbol` maps leading `8`/`4` → BJ but lets `920xxx` fall through to SZ/main (`symbols.py:150-159` at L1-review time). L1's `SubjectParams.project` leans on this constructor, so once a world is bound a 920 code would perform a wrong-market read with a well-formed but FALSE `Symbol` identity. The gate asserts `normalize_symbol("920799").exchange == "BJ"` (and board accordingly). If RED: the fix already exists on branch `great-meitner` (`296bd02`, the shared `is_bj_code` predicate; merging it also flips the known `test_pipeline_candidates` BJ-920 xfail + the `candidates.py:41-44` docstring — the tracked merge item) — STOP this plan at Task 0 and escalate to the controller for the merge/port decision; never hand-roll the 4/8/920 prefix rule here (the exact defect class the D-0 ruling forbids).
  5. **D-A (capability backend registration ABI):** pin at source how the Phase-2 `CapabilityGateway` resolves the trusted backend for the seven data capabilities — `TrustedFactoryRegistry.register_capability_backend(ref, factory)` / `capability_backend_factory` (`catalog_runtime.py:494-517`), and the shape `test_runtime_integration.py:459` registers (`lambda **kw: backend` returning the world's ONE `DataSourceCapabilityBackend`). Record whether the factory is invoked per-node or per-runtime — this decides Task 4's backend-sharing shape;
  6. **D-B (policy-resolver elapsed branch):** the surface's freshness policy is elapsed-based (`policy.freshness.default-elapsed`, `data/catalog.py:438-443`), and `DataPolicyResolver.resolve_method` on an elapsed policy **requires at least one registered limit policy carrying a calendar identity** (`data/source.py:1088-1099` — else `ValueError`). Pin the exact `LimitRulePolicy` build fields (read `data/source.py` at the class) — Task 1 must register one;
  7. **D-C (PitGuard clock contract):** read `data/pit.py::PitGuard.from_context` and pin what it requires of the injected `clock` for `mode=ONLINE` (advancing `SystemClock` vs a frozen clock at `ctx.as_of`). Task 4's world binds whichever the implemented contract requires;
  8. **D-D (manifest persistability):** whether `DataSnapshotManifest` (and `DataRoutingSnapshot` / `DataSourceConfigSnapshot`) are registered payload schemas in the production chain registry (`build_phase9_registry` lineage — check `PHASE3_PUBLIC_MODELS` membership and `stores.payloads.put` acceptance against a real `Phase2RuntimeRegistry`). If `DataSnapshotManifest@1` is registered, Task 7 persists the per-run manifest through `stores.payloads.put`; if not, Task 7 uses the driver-archive channel (a canonical-JSON file under the driver's archive dir keyed by content digest) and this plan is corrected here, not silently. **「控制器已裁」no pre-ruling fork: this empirical result governs the channel — and whichever channel wins, its idempotency keys are run-scoped for run-varying content** (the campaign's key discipline); Task 7's `persist_capture` follows this;
  9. **D-E (deterministic-handler registration ABI):** how `cand.*` deterministic handler factories are registered on `ProductionCatalogRuntime` (`pipeline/assembly.py` Task-11 idiom, `TrustedFactoryRegistry.register_handler`) and whether `handler.pv.price_action` / `handler.pv.microstructure` have any production registration today (expected: none). Record the handler ABI ("two-stage", `pipeline/candidates.py:12`) for Task 6.
- [ ] **Step 2: Run** `python -m pytest tests/orchestration/test_l2b_handoff.py tests/orchestration/data -q` — expected PASS (this gate pins existing behavior; nothing is implemented yet). Also re-measure the two-half foreground baseline of `tests/orchestration` and record the numbers in the progress ledger.
- [ ] **Step 3: Commit**

```bash
git add tests/orchestration/test_l2b_handoff.py
git commit -m "test(orchestration): L2-b handoff gate - pin the data-runtime ABIs the production world stands on"
```

---

## File Structure (created/modified in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/adapters/data_world.py` | Tasks 1–5: production data-world recipe (sealed registry, routing, config, calendar, policy resolver), the `guanlan.datafeed` adapter binding, per-run capture (manifest + DataContext), persistence, `ProductionDataWorldResolver`, `ProductionDataProvider`, `register_production_data_provider`, `production_data_provider_factory(subject_params)` (Task 5) |
| `config/orchestration/materials/data/cn-a-share-sessions-2026.json` | Task 1: the committed, digest-sealed 2026 A-share session calendar material |
| `guanlan_v2/orchestration/data/runtime.py` (modify) | Task 5: the real `_DataRuntimeBridgeSession` gains the subject param source; the worldless incumbent (`WorldlessDataBridgeProvider` + factory + registration recipe) deleted — the dead-row class AND its helpers were already deleted by L1 (their fate was decided once, there) |
| `guanlan_v2/orchestration/pipeline/live_decide.py` (modify) | Task 4: `build_production_bindings` registers the production data provider + capability backends; Task 7: pre-lease stale-context pre-flight in `make_orchestrated_decide` |
| `guanlan_v2/orchestration/pipeline/assembly.py` (modify) | Task 5: the per-run `_SubjectScopedFactories` override re-targeted to the production factory; Task 6: the two pv handler factories registered |
| `guanlan_v2/orchestration/lane0_driver.py` (modify) | Task 7: recipe-built DataContext replaces `presets.pilot_data_context`; per-run capture persisted |
| `tests/orchestration/test_l2b_handoff.py` | Task 0 gate |
| `tests/orchestration/test_data_world_recipe.py` | Tasks 1–2 |
| `tests/orchestration/golden/production_data_registry_manifest_v1.json` | Task 1: NEW additive golden — the production source-registry manifest, inventoried |
| `tests/orchestration/test_production_data_provider.py` | Tasks 3–5 |
| `tests/orchestration/test_pm_two_bridges.py` (modify — conscious flips) | Tasks 4–5 |
| `tests/orchestration/data/test_data_catalog.py` (modify) | Task 4: flip the no-production-caller pin; Task 5: flip L1's Test-5 source-text pin |
| `tests/orchestration/test_pv_aux_nodes.py` | Task 6 |
| `tests/orchestration/test_lane0_driver.py` (modify — bind exact name at source) | Task 7 |
| `tests/orchestration/test_pipeline_live_decide.py` (modify, additive) | Task 7 pre-flight |
| `.superpowers/sdd/progress-orchestration.md` (append) | Tasks 8–9 ledger entries |

---

## Task 1: The production data-world recipe (deterministic sealed registry + routing + config + calendar + policy)

The world's frozen half. **The controlling constraint** (Task 0 items 2–3): the deep lane's session verifies (a) `registry.default_route(m).entries == row.frozen_route` per sealed row, and (b) `world.ctx == snapshot.data_context` by full equality, where the snapshot is committed by Lane 0 **in a different process**. Therefore every recipe component must be **byte-deterministic across processes** — derived only from module constants, `phase3_data_surface()`, and committed repo material files. This determinism is itself pinned by test (build the recipe twice in two subprocesses, compare digests).

**Files:**
- Create: `guanlan_v2/orchestration/adapters/data_world.py`, `config/orchestration/materials/data/cn-a-share-sessions-2026.json`, `tests/orchestration/test_data_world_recipe.py`, `tests/orchestration/golden/production_data_registry_manifest_v1.json`

**Interfaces (produced):**
- `PRODUCTION_DATA_REGISTRY_VERSION = "prod-data-v1"`, `PRODUCTION_ROUTING_AUDIT_ID = "prod-data-routing-v1"` — frozen constants.
- `@dataclass(frozen=True) class ProductionDataWorldRecipe:` `registry: DataSourceRegistry` (sealed), `source_config: DataSourceConfigSnapshot`, `routing: DataRoutingSnapshot`, `calendar: ImmutableTradingCalendar`, `policy_resolver: DataPolicyResolver`, plus the derived digests as properties (`source_registry_digest`, `routing_snapshot_digest`, `source_config_digest`).
- `def production_data_recipe() -> ProductionDataWorldRecipe` — cached (module-level, `phase3_data_surface` idiom), pure, no I/O beyond reading the committed calendar material file.

**Steps:**

- [ ] **Step 1: Write failing tests** (`test_data_world_recipe.py`): registry seals; **route equality**: for every sealed prefetch row, `recipe.registry.default_route(row.method_ref.id).entries == tuple(row.frozen_route)` — this is the recon-flagged check generalized: also assert a default route exists for **all seven** method ids (`ohlcv, indicators, verified_snapshot, fundamentals, news, signals, instrument_names`), each the single entry `(surface.source_ref, spec.capability_ref)`, so a future L3 grant of `indicators`/`news` freezes the same route without re-opening this module; **method-spec identity**: the registry registers `phase3_data_surface().method_specs` **unchanged** (spec digests equal — the sealed rows' `method_ref` cross-resolution at `_frozen_route_for` depends on it; the test-suite idiom of re-building specs with a session freshness ref is exactly what production must NOT do); **policy resolution**: `recipe.policy_resolver.resolve_method(spec, ctx=...)` succeeds for `verified_snapshot` under an ONLINE context (per Task-0 D-B this requires the registered `LimitRulePolicy` naming `cn_a_share` + the committed calendar material ref); **calendar honesty**: material file digest-verified (`ImmutableTradingCalendar` raises on tamper), `coverage` spans 2026, `is_session` true on a known trading day and false on a known holiday (spot-check 春节 2026-02-17 absent, and a normal Tuesday present); **cross-process determinism**: `python -c` subprocess builds the recipe and prints `(source_registry_digest, routing_snapshot_digest, source_config_digest)`; equal to the in-process values; **new golden**: `recipe.registry.manifest()` matches `tests/orchestration/golden/production_data_registry_manifest_v1.json` (write the golden by hand from the first verified build; it is NEW and additive — the existing `data_source_manifest_v1.json` is the Task-6 fixture's and must remain byte-identical, asserted).
  Run: `python -m pytest tests/orchestration/test_data_world_recipe.py -q` — expected FAIL (module missing).
- [ ] **Step 2: Build the calendar material.** Generate `config/orchestration/materials/data/cn-a-share-sessions-2026.json` ONCE from the engine's full trading calendar (the reviewed reader behind `guanlan_v2/seats/api.py::_trading_calendar`, `seats/api.py:530` — run it offline, take the 2026 sessions, write sorted ISO dates + `calendar_id: "cn_a_share"`). This file is **committed and thereafter frozen** — the recipe reads bytes, never re-derives (cross-process determinism). Coverage ends 2026-12-31: a session date outside coverage is an honest refusal downstream (calendar contract `data/calendar.py:110-119`), and extending into 2027 is a reviewed one-line material bump chartered to whoever hits it (leave a dated note in the module docstring). **「控制器已裁」the committed frozen calendar file is CONFIRMED:** determinism over auto-derivation; the annual extension stays a reviewed one-line material bump; a date past coverage refuses honestly.
- [ ] **Step 3: Implement the recipe** in `adapters/data_world.py`: register the surface's seven method specs unchanged, the surface `source_descriptor` (`guanlan.datafeed@1`), one explicit single-entry default route per method (route_policy_ref = a frozen deterministic `ContentRef` constant — `_frozen_route_for` compares entries only, pinned at Task 0), the surface freshness policy, and one `LimitRulePolicy` (fields per Task-0 D-B) binding `cn_a_share` + the material ref; seal; build `DataSourceConfigSnapshot` (its builder is in `data/snapshot.py` — bind the implemented fields; the one configured source is `guanlan.datafeed`); `registry.build_routing_snapshot(audit_id=PRODUCTION_ROUTING_AUDIT_ID, schema_registry_digest=<the production chain registry digest — parameter, not read from a global>, source_config=...)`; `TradingCalendarResolver([calendar])` inside a `DataPolicyResolver` over the sealed snapshot.
- [ ] **Step 4: Run + mutate.** Focused green, then mutations each red→revert: (m1) change one route to two entries → route-equality test red; (m2) rebuild a method spec with a different freshness ref → method-spec identity red; (m3) drop the limit policy → policy-resolution red; (m4) edit one byte of the calendar material file → digest-verification red (restore byte-identical from git).
- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/adapters/data_world.py config/orchestration/materials/data/cn-a-share-sessions-2026.json tests/orchestration/test_data_world_recipe.py tests/orchestration/golden/production_data_registry_manifest_v1.json
git commit -m "feat(orchestration): L2-b production data-world recipe - deterministic sealed registry, frozen routes equal the sealed rows, committed calendar material"
```

---

## Task 2: The reviewed source adapter under the sealed identity + the ONE production backend

Bind `LiveClientSource` under source id `guanlan.datafeed` and settle the concurrency model of the backend (charter §2.2 item 9 — the provider seam is the flagged throughput seam; this task states and pins the model instead of leaving it to chance).

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/data_world.py`
- Test: extend `tests/orchestration/test_data_world_recipe.py`

**Interfaces (produced):**
- `def production_data_adapters() -> Mapping[str, DataSource]` — exactly `{"guanlan.datafeed": LiveClientSource()}` (the facade-default construction; tests inject fakes via the existing `probe_fn`/`resolve_source_fn`/`known_sources_fn` ports, `adapters/live_data.py:292-307`).
- `class ThreadConfinedDataBackend(DataSourceCapabilityBackend)` — the ONE process-stable production backend: `_staged` becomes **thread-local**. Rationale, stated in the docstring and pinned by test: the dag executor runs nodes concurrently in `asyncio.to_thread` worker threads (`assembly.py:86-94`, `runtime_limit=4` at `live_decide.py:1222`); the base class's single staged slot would interleave across nodes (`stage`→`invoke`→`clear` is same-thread synchronous, `data/runtime.py:287-296`), and the Task-0 D-A finding forces ONE backend instance shared by every per-run world (the capability-backend factory is registered once at binding-build time). Thread-local staging makes cross-node interleaving structurally impossible without a lock; the underlying `live_client.probe` is a subprocess per call (thread-safe by isolation). **Concurrency statement for the record:** the data provider does NOT serialize; N concurrent nodes may probe concurrently. The LLM ceiling remains the `WorkerSeatModelGateway`'s single-loop lock — that seam is charter §2.2/9's user-surfaced item and is NOT changed here.
- `def production_data_backend() -> ThreadConfinedDataBackend` — cached singleton over `production_data_adapters()`.

**Steps:**

- [ ] **Step 1: Failing tests:** (a) adapter map has exactly the sealed source id; (b) a `fetch` through an injected fake probe returns a `RawFetch` whose `source_ref` **is** `phase3_data_surface().source_ref` when staged with the recipe's frozen route (the echo, Task-0 item 4, now proven under the production identity); (c) unsupported method (`indicators`) raises `RoutingConfigurationError` — and a **guard test**: every method id granted a row in the sealed prefetch binding is in `LiveClientSource`'s supported set (today: `verified_snapshot` ⊆ `{verified_snapshot, news, ohlcv}`), so an L3 grant of an unsupported method fails THIS test first instead of failing live; (d) **interleave pin**: two threads each stage+invoke on the shared backend with different sources; both complete, each `RawFetch` matches its own thread's staged source (red on the base class by monkeypatching thread-local away — that IS the mutation for this guard); (e) staging misuse still loud: invoke-without-stage raises `DataRuntimeError` on the calling thread.
- [ ] **Step 2: Implement.** `ThreadConfinedDataBackend` overrides only the staged-slot storage; `invoke`'s verification chain (adapter present, `RawFetch` type, source echo, request digest — `data/runtime.py:180-207`) is inherited, never copied.
- [ ] **Step 3: Run focused + mutate (d)'s guard; revert. Commit**

```bash
git add guanlan_v2/orchestration/adapters/data_world.py tests/orchestration/test_data_world_recipe.py
git commit -m "feat(orchestration): L2-b guanlan.datafeed adapter binding + thread-confined production data backend"
```

---

## Task 3: `ProductionDataWorldResolver` + `ProductionDataProvider` (per-run world; rowless workers stay licensed-EMPTY)

The provider that replaces the worldless stopgap. Design forced by verified constraints: the world's `ctx` must equal the **admitted** snapshot's `data_context` (Task 0 item 3), and the admitted snapshot is chosen per run — so the world is resolved **per opened session**, from the session's own `request.input_snapshot.context_snapshot_ref`, never a process-global frozen at binding time.

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/data_world.py`
- Create: `tests/orchestration/test_production_data_provider.py`

**Interfaces (produced):**
- `class ProductionDataWorldResolver:` constructed with `(*, stores, recipe, schema_resolver, clock, refusal_audit_sink_factory)`; method `world_for(request) -> DataRuntimeWorld`:
  1. read the admitted `ContextSnapshot` through `request.reader` + `request.input_snapshot.context_snapshot_ref` (the same resolution `_verify_context_binding` performs);
  2. **digest verification, loud and typed** (`DataRuntimeError`, each message naming the failing digest pair): `ctx.source_registry_digest == recipe.source_registry_digest`, `ctx.routing_snapshot_digest == recipe.routing_snapshot_digest`, `ctx.source_config_digest == recipe.source_config_digest`. A pilot-era context (`"b"*64` etc.) fails here with a message naming the remedy: *"the committed ContextSnapshot predates the production data world (L2-b); re-run Lane 0"*;
  3. load the run's `DataSnapshotManifest` by `ctx.data_snapshot_content_digest` through the Task-0 D-D channel (payload store scan or driver archive); absent ⇒ typed refusal (*"manifest not persisted — the context producer did not run the L2-b recipe"*), never a rebuilt stand-in (a rebuilt manifest could not honestly carry the run-start boundary);
  4. return `DataRuntimeWorld(source_registry=recipe.registry, routing=recipe.routing, manifest=<loaded>, source_config=recipe.source_config, ctx=snapshot.data_context, schema_resolver=…, policy_resolver=recipe.policy_resolver, calendar=recipe.calendar, clock=<per Task-0 D-C>, cache=ProductionNoCache(), catalog_runtime=<the bound bundle runtime>, refusal_audit_sink=…, backend=production_data_backend())`.
- `class ProductionNoCache:` `get_verified(...) -> None` — an honest no-cache: `cache_or_invoke` rows always probe their first route entry; results still persist as evidence through the reader/writer path. Docstring names this a reviewed stopgap (a verified `DataCache` is post-L2-b work, not silently absent).
- `class ProductionDataProvider:` two-stage provider under the sealed identity. `prepare_input` = the I/O-free empty prepare, **delegated** (construct-and-forward to `DataRuntimeBridgeProvider.prepare_input`'s logic or the identical inherited implementation — never a third copy; the pm-two-bridges report already proved prepare-mirroring bit-for-bit, keep that pin). `open_execution` partitions on row COUNT only — post-L1 the "structurally dead row" category does not exist (L1 deleted `_row_is_structurally_dead` / `structurally_dead_row_fact` together with the dead-row class; their fate was decided ONCE, in L1 — **correction clause:** finding the helpers still present at landing time means the declared L1→L2-b order was broken, STOP per Task 0's gate; never resurrect them here):
  - **zero rows** (both pv aux workers): a session whose freeze completes with an EMPTY `BridgeContribution` — this is now *catalog-licensed* (the analyzer summed bounds over zero rows = 0/0; empty is exactly what the sealed summary licenses), not a refusal;
  - **rows present** (today: `dec.pm`'s one healed row): resolve the world via `world_for(request)` and delegate the WHOLE session to the real machinery — `DataRuntimeBridgeProvider(bridge=…, summary=…, world=…).open_execution(request)` (public class, `data/runtime.py:454-494`; zero re-implementation). Param resolution inside the real session comes from `node.params` (workers that legally carry params) or from the run-subject source that **Task 5 (the L1↔L2-b integration seam)** threads in; until Task 5 lands, a delegated `dec.pm` session refuses with L1's runner-seam cause — an honest intra-train intermediate that never reaches production (order-conditional pin, flipped by Task 5). Never an EMPTY freeze over a row that could have been read.
- `def register_production_data_provider(*, factories, stores, schema_resolver, clock, catalog_runtime) -> None` — the ONE registration recipe: binds the provider factory under `phase3_data_surface().provider_ref` AND registers the capability backends — for each of the seven `data_capability_refs()` (`data/catalog.py:380-385`), `factories.register_capability_backend(cap_ref, lambda **kw: production_data_backend())` (the Task-0 D-A shape). Sole intended production caller: `live_decide.build_production_bindings` (Task 4).

**Steps:**

- [ ] **Step 1: Failing tests** (`test_production_data_provider.py`), driven over a REAL `build_production_catalog_runtime` bundle + the real reduced-preset support report (the `test_pm_two_bridges.py` harness idiom — reuse its fixtures by import or faithful copy, recorded):
  - resolver digest checks: a pilot context refuses with the re-run-Lane-0 message; a recipe-built context passes;
  - manifest loading both arms (present/absent) through the Task-0 D-D channel;
  - pm (rows present, subject source not yet threaded — that is Task 5): the delegated real session refuses with L1's runner-seam cause, zero gateway begins — an **order-conditional pin**, consciously flipped by Task 5 to the real-read shape;
  - pv-shaped rowless worker freezes EMPTY (completed, not error) — RED today;
  - a synthetic live-row worker (fabricated catalog in the `test_runtime_integration.py` idiom, `params_schema_ref` present) executes an end-to-end read through the delegated real session over an injected fake probe: one `ToolCallRecord`, PIT-audited result, rendered untrusted block for an LLM kind — proving the delegation seam carries the whole Phase-3 machinery;
  - helper-absence guard: `_row_is_structurally_dead` / `structurally_dead_row_fact` exist nowhere in `guanlan_v2/orchestration` (grep pin — their fate was decided once, in L1; this plan never resurrects them);
  - **PIT honesty at the seam**: with the fake probe returning `pulled_at` after `ctx.as_of`, the read ends `FutureDataRefused` (the guard, not the adapter, refuses); with `pulled_at` absent, `MissingAvailabilityRefused` — available_at windows enforced by the single authority, pinned here once for the production wiring;
  - degradation matrix: probe `status:planned` on the (single-entry) chain ⇒ optional method exhausts to a named `UNAVAILABLE` result / core method raises — both arms pinned against the real dispatch. **「控制器已裁」the production consequence is ACCEPTED for this phase: a `verified_snapshot` outage ⇒ pm node hard-fail ⇒ the deep run fails/refuses honestly (the fast chain stands) — cross-referenced into L3's D-2 ruling packet as the same-family precedent.**
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Mutations**, each red→revert: (m1) downgrade the rows-present arm to an EMPTY freeze (the silenced-outage shape every tombstone in this lineage forbids) → the delegation/refusal pins red; (m2) convert the zero-rows arm into a refusal → the licensed rowless-EMPTY pin red; (m3) skip digest verification step 2 → pilot-context test red; (m4) fabricate a manifest instead of refusing → absent-manifest pin red.
- [ ] **Step 4: Run focused + the untouched sealed-golden probe. Commit**

```bash
git add guanlan_v2/orchestration/adapters/data_world.py tests/orchestration/test_production_data_provider.py
git commit -m "feat(orchestration): L2-b per-run world resolver + production data provider - rowless workers freeze licensed-EMPTY, rows run the real Phase-3 machinery"
```

---

## Task 4: Bind the world into production assembly (the ONE-recipe seam) + retire the worldless stopgap

Every campaign fix followed the ONE-recipe discipline (`live_decide.py:1139-1189` — experience via `register_lane0_experience_factories`, memory via `register_phase3_memory_provider_factory`). The data bridge now follows it: `build_production_bindings` supersedes **whichever provider registration is bound at `phase3_data_surface().provider_ref` at landing time** with `register_production_data_provider`. **Correction clause (seam-review finding 1 — both incumbents named):** under the declared L1→L2-b order the incumbent is `WorldlessDataBridgeProvider` / `register_worldless_data_provider` (L1 Task 3's successor to the retired `StructurallyDeadRowDataProvider`, whose own tombstone names this plan's Tasks 4–5 as its retirers), and the pm-shape pins arriving here are L1's worldless set (`test_pm_two_bridges.py` shape-2/3 pins + `test_subject_projection.py` provider shapes); if the order was broken and the incumbent is still `StructurallyDeadRowDataProvider` / `register_structurally_dead_row_data_provider` with the pre-L1 pm pin set (empty-complete + trunk pins), STOP at Task 0's gate first — the supersede then targets that incumbent under the same discipline. The **class deletion of the incumbent happens in Task 5, not here**: the per-run `_SubjectScopedFactories` view still constructs the worldless factory until Task 5 re-targets it (deleting it here would leave a dangling caller).

**Files:**
- Modify: `guanlan_v2/orchestration/pipeline/live_decide.py` (the registration block only), `tests/orchestration/test_pm_two_bridges.py`, `tests/orchestration/data/test_data_catalog.py` — the incumbent class itself is deleted in Task 5, when its last caller (the per-run view) is re-targeted, `tests/orchestration/test_experience_provider_discrimination.py` (its unguarded `isinstance(..., WorldlessDataBridgeProvider)` pin at ~:370 — an L1 Task-3 pathspec widening; its docstring names THIS task as the re-target owner; flip it here or it reddens by surprise — L1 Task 3 review Minor #1)
- Extend: `tests/orchestration/test_production_data_provider.py`

**Conscious flips (charter §3 rule 2 — every flip named, both directions evidenced):**
1. `test_data_catalog.py::test_the_data_bridge_provider_has_no_production_caller` — recon fact F's AST pin. Flips from "the only occurrence is the `def`" to "the occurrences are the `def` **and** the delegation call inside `adapters/data_world.py`" (positive enumeration, same discriminating AST idiom — never deleted, never loosened to a substring scan).
2. `test_pm_two_bridges.py::TestTheProductionRegistrationSeam` — the bundle-identity/ref-identity/ordering triple (a/b/c) re-targets `ProductionDataProvider`; the isinstance pins flip class **from the registered incumbent, order-conditionally per the correction clause** (`WorldlessDataBridgeProvider` under the declared order; `StructurallyDeadRowDataProvider` only if the order was broken — both named).
3. `test_pm_two_bridges.py` pv-aux pins — "still fail loudly `bridge_execution_error` naming the L2-b gap" flips to "freeze EMPTY completed" (Task 3's rowless arm). The OLD failure string (`LIVE_DATA_FAILURE_REASON` raw control) is **kept as a negative pin**: it must no longer occur on the bound path.
4. The incumbent's unit pins (under the declared order: L1's worldless shape pins in `test_subject_projection.py` and the L1-flipped pm pins in `test_pm_two_bridges.py`) migrate or die with the class **when Task 5 deletes it**; the rowless-EMPTY semantics pins survive against `ProductionDataProvider`'s zero-rows arm. The dead-row semantics pins (empty freeze + logged fact) do NOT survive — that category died in L1; assert the helper names absent.

**Steps:**

- [ ] **Step 1: RED.** Flip the four pin groups first; run `python -m pytest tests/orchestration/test_pm_two_bridges.py tests/orchestration/data/test_data_catalog.py -q` — expected FAIL with the old wiring (record shapes).
- [ ] **Step 2: Implement.** In `build_production_bindings`: build `recipe = production_data_recipe()` and `register_production_data_provider(factories=bundle.factories, stores=stores, schema_resolver=stores.resolver, clock=clock, catalog_runtime=bundle.runtime)` — **at binding construction**, so any recipe failure (missing calendar material, unsealed registry) kills the deep lane at server startup, loudly, before any lease exists (the burned-lease precedent). **Do not delete the incumbent class here** — Task 5 deletes it when the per-run view stops constructing it. Comment block at the registration site updated to name this plan and the Task-5 residue (the per-run subject seam still hands out the worldless provider until Task 5 re-targets it).
- [ ] **Step 3: Registration-seam regression** (the demanded a/b/c triple, re-proven): one bundle, `view.resolve("data.runtime").provider_ref == phase3_data_surface().provider_ref`, registration precedes binding return; plus NEW: all seven capability backends resolve on `bundle.factories` to the ONE `production_data_backend()` instance.
- [ ] **Step 4: Mutations:** (m1) drop the data registration line → seam pins red; (m2) drop the capability-backend loop → backend pins red; (m3) register under a fabricated ref → ref-identity pin red. Revert each.
- [ ] **Step 5: Run** the focused set + `tests/orchestration/test_pipeline_reduced_deep_preset.py tests/orchestration/test_pipeline_live_decide.py tests/orchestration/data -q` + the sealed-golden probe (byte-identical). **Commit**

```bash
git add guanlan_v2/orchestration/pipeline/live_decide.py guanlan_v2/orchestration/adapters/data_world.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/data/test_data_catalog.py tests/orchestration/test_production_data_provider.py tests/orchestration/test_experience_provider_discrimination.py
git commit -m "feat(orchestration): L2-b world bound through the ONE recipe in build_production_bindings; registered incumbent superseded with conscious flips (class deletion rides Task 5)"
```

---

## Task 5: The L1↔L2-b integration seam — the per-run subject source reaches the real session (the seam-review's unowned seam, owned HERE)

The seam review's blocking finding 2: L1 built the per-run subject projection and left two hand-off ends dangling by design — the `_SubjectScopedFactories` view (`pipeline/assembly.py`, L1 Task 4) overrides the sealed `phase3_data_surface().provider_ref` with `worldless_data_provider_factory(subject_params)`, and the real `_DataRuntimeBridgeSession` still calls `_assemble_params(row, req.node)` with no subject source (pinned by L1's Test 5, which L1 cross-references to exactly this task). This task owns the seam: after it, a deep run's per-run view hands the bridge a **subject-bound production provider**, and the real session resolves pm's healed row from the run's committed subject.

**Files:**
- Modify: `guanlan_v2/orchestration/adapters/data_world.py` (subject-bound factory), `guanlan_v2/orchestration/data/runtime.py` (session subject source + worldless deletion), `guanlan_v2/orchestration/pipeline/assembly.py` (view override re-target)
- Extend: `tests/orchestration/test_production_data_provider.py`, `tests/orchestration/test_pipeline_assembly.py`
- Modify (conscious flips): `tests/orchestration/test_pm_two_bridges.py`, `tests/orchestration/data/test_data_catalog.py`, `tests/orchestration/data/test_subject_projection.py`

**Interfaces (produced):**
- `production_data_provider_factory(subject_params: SubjectParams | None = None)` in `adapters/data_world.py` — the subject-bound construction of Task 3's `ProductionDataProvider` (same world resolver, same backend; exact wiring bound at source). **It becomes the per-run view's override target:** in `pipeline/assembly.py`, `_SubjectScopedFactories`' override for the sealed provider ref flips from `worldless_data_provider_factory(subject_params)` to `production_data_provider_factory(subject_params)`; every other view property (identity-when-None, delegation ref-identity, the negative `register_handler` pin — L1 Task 4's invariants) is re-run unchanged.
- The real `_DataRuntimeBridgeSession` gains the subject param source: `ProductionDataProvider` carries `subject_params` into the delegated `DataRuntimeBridgeProvider` session, and the session's param assembly becomes `_assemble_params(row, req.node, subject_params=…)`.

**Conscious flips (each in the same commit as its behavior change, bidirectional evidence):**
1. **L1's Test-5 source-text pin** — `test_data_catalog.py::TestVerifiedSnapshotRowRunnableOnlyUnderSubjectProjection` test 5 pins the literal call text `_assemble_params(row, req.node` in the live provider session. It flips HERE (chartered by L1 to this task by name): the new pin asserts the subject-carrying call text.
2. The pm behavioural pins (Task 3's order-conditional runner-seam-refusal pin; L1's worldless shape pins in `test_subject_projection.py` and `test_pm_two_bridges.py`) flip to the end state both plans exist for: the delegated session resolves pm's row from the run subject and executes a real read against an injected fake probe — one `ToolCallRecord`, a `params_cls`-validated `InstrumentUniverseParams` echoing the subject's code/as-of, PIT-audited result, rendered untrusted block.
3. `WorldlessDataBridgeProvider` + `worldless_data_provider_factory` + `register_worldless_data_provider` are **deleted** (their last caller — the per-run view — is re-targeted in this same commit; L1's successor tombstone names this task). Shape 3's wiring-guard semantics survive inside the real path (`_assemble_params`'s runner-seam refusal — pinned); a repo-wide grep pin asserts the worldless names no longer exist outside docs/reports.

**Steps:**

- [ ] **Step 1: RED.** Flip the pins first; run `python -m pytest tests/orchestration/test_production_data_provider.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/data/test_data_catalog.py tests/orchestration/data/test_subject_projection.py tests/orchestration/test_pipeline_assembly.py -q` — expected FAIL against the Task-4 wiring (record shapes).
- [ ] **Step 2: Implement** (factory, view re-target, session subject source, worldless deletion). The L1 view invariants (identity-when-None, delegation ref-identity, `register_handler` negative pin, `GUANLAN_SEATS_DEEP`-unset bit-stability) re-run green unchanged.
- [ ] **Step 3: Mutations**, each red→revert: (m1) the view re-target drops `subject_params` (constructs a subject-less production provider) → the subject-echo pin red (the campaign's half-wired-kwarg defect class); (m2) the session ignores its subject source (calls `_assemble_params` without it) → the flipped Test-5 pin AND the real-read pin red; (m3) the worldless deletion leaves the registration recipe behind → the grep pin red.
- [ ] **Step 4: Run** the Step-1 set + `tests/orchestration/test_pipeline_live_decide.py -q` + the sealed-golden probe (byte-identical). **Commit**

```bash
git add guanlan_v2/orchestration/adapters/data_world.py guanlan_v2/orchestration/data/runtime.py guanlan_v2/orchestration/pipeline/assembly.py tests/orchestration/test_production_data_provider.py tests/orchestration/test_pipeline_assembly.py tests/orchestration/test_pm_two_bridges.py tests/orchestration/data/test_data_catalog.py tests/orchestration/data/test_subject_projection.py
git commit -m "feat(orchestration): L2-b the L1 integration seam - per-run view hands out the subject-bound production provider, the real session resolves pm's row from the run subject, worldless successor retired"
```

---

## Task 6: The two pv aux nodes — past the bridge, to an honest catalog-licensed outcome

Sealed WorkerSpecs (`lane_catalog.py:404-442`): `pv.price_action` — DETERMINISTIC, allowlist `("ohlcv",)`, `tool_calls=OPTIONAL`, `inputs=()`; `pv.microstructure` — DETERMINISTIC, allowlist `("signals","verified_snapshot","indicators")`, OPTIONAL, `inputs=()`. Neither has a reviewed prefetch row (grants = L3, out of scope). **What real data they get in L2-b: none via the bridge — and that is the honest answer.** Their data bridge now freezes EMPTY (Task 3 rowless arm, analyzer bounds 0/0 — licensed). The next seam is their deterministic handlers (`handler.pv.price_action` / `handler.pv.microstructure` catalog materials), which per Task-0 D-E have no production factory registration today.

**Ruling encoded here (honest-refusal over plausible-empty):** with zero plan-fed inputs and zero granted data rows, a handler that emitted an empty `PriceActionFeatureReport` would read downstream as "computed: no patterns found" — fabrication-adjacent. Instead the two handlers, when they have nothing to compute over, raise a **typed refusal naming the L3 grant gap** (`aux_data_ungranted`: "pv.price_action holds cap.data.ohlcv but the sealed prefetch binding grants it no row (L3); no data reached this node; refusing to emit a report computed over nothing"). The node FAILS with that reason, the run **degrades without blocking** (already proven: aux nodes are non-trunk), and the inter-node inliner states the absence in bull/bear prompts (`status="absent"` + `TRUSTED_UPSTREAM_ABSENT_TEXT` — the 0c601b5 machinery, pinned there). Post-L3, rows exist, the bridge feeds real blocks/results, and the same handlers compute for real — that flip belongs to L3's exit gate, named here.

**Files:**
- Modify: `guanlan_v2/orchestration/pipeline/assembly.py` (register the two pv handler factories in `build_production_catalog_runtime`, the Task-11 `cand.*` idiom per Task-0 D-E) — plus the handler implementations at the location the D-E recon dictates (if the reviewed handler modules exist as importable code, wrap them; if only as material bytes, the factory binds a thin trusted wrapper module under `guanlan_v2/orchestration/pipeline/` — bind at source, never `exec` material bytes)
- Create: `tests/orchestration/test_pv_aux_nodes.py`

**Steps:**

- [ ] **Step 1: Failing tests:** (a) the production bundle resolves handler factories for both pv handler refs (RED: unregistered); (b) driven through the real execution runtime with the reduced-preset catalog: pv node's bridge layer completes EMPTY and the node fails `aux_data_ungranted` (not `bridge_execution_error`, not `handler_unresolved`) — assert on the typed reason and that the OLD reason string does not occur; (c) trunk unharmed: sentiment→…→pm→trader ordering unchanged, aux failure degrades (reuse the reduced-preset e2e fixture, additive assertions only); (d) the absence statement reaches bull/bear per the inliner pins (additive assertion on the existing harness, not a re-implementation).
- [ ] **Step 2: Implement + mutate** (silence the typed refusal into an empty report → (b) red; unregister one factory → (a) red). Revert each.
- [ ] **Step 3: Run** focused + `tests/orchestration/test_pipeline_reduced_deep_preset.py tests/orchestration/test_internode_prompt_inlining.py -q`. **Commit**

```bash
git add guanlan_v2/orchestration/pipeline/assembly.py tests/orchestration/test_pv_aux_nodes.py
git commit -m "feat(orchestration): L2-b pv aux nodes - handler factories registered, honest aux_data_ungranted refusal replaces bridge_execution_error"
```

(If the handler wrapper needs its own module, add it to the pathspec explicitly.)

---

## Task 7: Lane 0 commits the real DataContext + the deep lane's pre-lease pre-flight

The other half of the ONE recipe: the **producer**. `lane0_driver.py:1247` builds `_presets.pilot_data_context(as_of=…)` — placeholder digests the driver itself flags as "an upstream gap" (`lane0_driver.py:105-111`, note at `:1124-1126`). This task closes that gap with the Task-1 recipe, and adds the deep-side pre-flight so a stale (pilot-era) committed snapshot refuses **before** any lease is drawn.

**Files:**
- Modify: `guanlan_v2/orchestration/lane0_driver.py`, `guanlan_v2/orchestration/adapters/data_world.py` (per-run capture + persist helpers), `guanlan_v2/orchestration/pipeline/live_decide.py` (pre-flight), `tests/orchestration/test_lane0_driver.py` (exact filename bound at source), `tests/orchestration/test_pipeline_live_decide.py` (additive)

**Interfaces (produced in `data_world.py`):**
- `def build_production_capture(*, clock, data_snapshot_id) -> tuple[DataSnapshotManifest, DataContext]` — wraps `adapters/live_data.py::build_online_capture_manifest` + `build_online_data_context` over the recipe (ONLINE/LIVE; `build_data_context` reads the clock exactly once — the driver passes its session-frozen clock, `_session_as_of` at `lane0_driver.py:1247`); `data_snapshot_id` is audit-only (the run id).
- `def persist_capture(*, stores_or_archive, registry_digest, manifest) -> <ref/locator>` — the Task-0 D-D channel; idempotent by content digest.

**Steps:**

- [ ] **Step 1: Failing driver tests:** the committed `ContextSnapshot.data_context` carries the recipe digests (not `"b"*64`); the manifest is loadable back by `ctx.data_snapshot_content_digest` through the same channel the resolver reads; the driver's `notes` no longer claim the pilot-context gap (flip the note pin at `lane0_driver.py:1124-1126` consciously — old text asserted absent, new note names the recipe); driver behavior otherwise byte-stable (existing driver suite green unmodified except the flipped note pin).
- [ ] **Step 2: Failing pre-flight tests** (`test_pipeline_live_decide.py`, additive): `make_orchestrated_decide` with a latest snapshot whose `data_context.source_registry_digest != production_data_recipe().source_registry_digest` returns the fast result with `deep_outcome="refused"`, reason `context_predates_data_world`, and — the load-bearing half — **zero lease interaction** (spy on `register_and_try_lease`: never called). Bind the check right after `snapshot_pair = bindings.latest_snapshot_fn()` resolves (`live_decide.py:742` region). A matching context proceeds unchanged (existing deep-path tests stay green).
- [ ] **Step 3: Implement both.** Driver: replace `pilot_data_context` with `build_production_capture` + `persist_capture`; keep `build_empty_memory_context` consumption identical (`presets.py:180-213` — same ContextSnapshot sealing, only the `data_context` argument changes).
- [ ] **Step 4: Mutations:** (m1) pre-flight deleted → lease-spy test red; (m2) driver silently keeps pilot context → digest test red. Revert.
- [ ] **Step 5: Run** focused + full driver suite + `test_bootstrap_e2e` family + sealed-golden probe. **Commit**

```bash
git add guanlan_v2/orchestration/lane0_driver.py guanlan_v2/orchestration/adapters/data_world.py guanlan_v2/orchestration/pipeline/live_decide.py tests/orchestration/test_lane0_driver.py tests/orchestration/test_pipeline_live_decide.py
git commit -m "feat(orchestration): L2-b lane0 commits the recipe DataContext + deep pre-lease stale-context pre-flight"
```

---

## Task 8: Whole-layer regression + full-tree gate

**Files:** none new — this is the gate before the live run.

- [ ] **Step 1:** `python -m compileall -q guanlan_v2/orchestration` — OK.
- [ ] **Step 2:** Full `tests/orchestration` foreground in the two deterministic halves; reconcile with `--collect-only`. Expected: Task-0 baseline + exactly this plan's additions, zero failures, 1 xfailed (the BJ-920 pin unless great-meitner merged). Any capability-manifest reds must be re-proven as the known concurrent-session drift (isolation proof, the fix-pm-dead-binding §6 idiom) — never absorbed silently.
- [ ] **Step 3:** Sealed-golden probe across every digest in Global Constraints — byte-identical, diff empty, output pasted into the ledger.
- [ ] **Step 4:** Watcher bit-stability: the `GUANLAN_SEATS_DEEP`-unset regression set (`tests/orchestration/test_pipeline_live_decide.py` watcher suites) byte-unchanged-behavior green; `rest of tests/` (seats/console-adjacent suites that this plan must not touch) green at their pre-plan counts.
- [ ] **Step 5:** Ledger entry in `.superpowers/sdd/progress-orchestration.md` (append). `.superpowers/` is **gitignored** — a commit of that path is structurally empty (the L1 seam-review finding); any copy that must live in git goes under `docs/superpowers/ledgers/` and THAT path is committed, explicit pathspec.

---

## Task 9: LIVE verification — a real deep run reads the real world (tokens authorized)

The campaign's lesson, verbatim from the charter: *测试树全绿,真机第一脚就踩到了测试永远踩不到的东西*. This task is mandatory; the plan does not close without it. Budget: one Lane-0 run + one deep run (~6 LLM invocations, deepseek seats per `config/llm.yaml`) + retries within the watcher's daily pool. All lease handling follows the campaign's sedimented 租约手法 (see `.superpowers/sdd/task-L2a-lane0-driver-report.md` + the ledger's reduced-preset runs).

- [ ] **Step 1: Stage.** Verify on **9998** first (start a server instance against the same durable stores is NOT safe — use the driver CLI + a 9998 process per the R3/R18 procedure), then restart 9999 so it loads this plan's commits. **This restart is the ONE-train deployment point (「控制器已裁」): 9999 first serves L1+L2-b together — L1's own live task ran on 9998 only.** Confirm `GET /orchestration/store_status` = bound; confirm the deep binding constructs (server log shows the data-provider registration, no recipe failure at startup).
- [ ] **Step 2: Lane 0 for real.** `python -m guanlan_v2.orchestration.lane0_driver propose` → `approve` → `run`. Assert from the archive/stores: the committed `ContextSnapshot.data_context.source_registry_digest` equals `production_data_recipe().source_registry_digest` (probe script); the manifest loads back by digest; the driver notes name the recipe. **This is the first production ContextSnapshot with real data digests — say so in the ledger.**
- [ ] **Step 3: Deep run on a FRESH code** (a code not yet spent for today's identity family — campaign rule: `deep-<digest>` is spent per `{code, session-date, opt_in}`). Trigger through the real watcher escalation path with `GUANLAN_SEATS_DEEP=1` + `GUANLAN_SEATS_DEEP_PRESET=reduced`. Assert, from `var/orchestration` (read-only copies, the campaign's forensics discipline):
  - the run reaches pm through `ProductionDataProvider` (log line + the absence of `LIVE_DATA_FAILURE_REASON` and of `bridge_preparation_failed`);
  - **the healed shape (L1 landed first — declared order):** pm's bridge runs the REAL delegated session (Step 4's read assertions — the structurally-dead EMPTY shape no longer exists post-L1); trunk completes sentiment→bull→bear→research-mgr→pm→trader, 6 LLM invocations, `PortfolioDecision@1` + `PortfolioTargetProposal@1` committed, ledger `orchestrated` row present;
  - **pv aux nodes:** outcome is `aux_data_ungranted` (Task 6's typed reason) with degradation stated in bull/bear prompt bytes — NOT `bridge_execution_error`;
  - wall-clock: record data-bridge prepare/freeze duration vs LLM time in the ledger (the §2.2/9 throughput datum, measured not guessed).
- [ ] **Step 4: the real read (the train's whole point — mandatory, no longer conditional).** L1 has landed by the declared one-train order (Task 0's gate proved it). In the Step-3 run assert: pm's session executes a real `verified_snapshot` read **resolved from the run's own committed subject** — one `ToolCallRecord`, a `VerifiedSnapshotDataResult` with a real quote row whose `available_at ≤ ctx.as_of` (PIT audit `passed`), the rendered untrusted block present in pm's `PromptAssemblyRecord`, and pm's prose citing the quote. If Task 0's gate had found L1 absent, the train was broken and this plan STOPPED there — this plan never closes with Step 4 open.
- [ ] **Step 5: Ledger.** Write what the run proved and what it did NOT prove (the §3 rule-5 discipline), including any defect found — the campaign averaged >1 defect per live attempt; finding none is itself a claim that needs the evidence pasted.

**Scope note (「控制器已裁」):** the A-line (screening) live verification is NOT this plan's — it belongs to L3's exit gates (L3 Task 7); this plan's live run is the deep lane only.

---

## Exit Gates

- [ ] `production_data_recipe()` is byte-deterministic across processes; its registry's default routes equal every sealed prefetch row's frozen route; new golden `production_data_registry_manifest_v1.json` inventoried; **all pre-existing sealed goldens byte-identical** (probe diff empty).
- [ ] `ProductionDataProvider` is bound in `build_production_bindings` under the exact sealed `bridge.data_runtime.provider@1` identity, with all seven data capability backends registered to the ONE thread-confined backend; the registered incumbent superseded (Task 4) and the worldless successor deleted (Task 5) — `StructurallyDeadRowDataProvider`, `WorldlessDataBridgeProvider` and their factories/helpers exist nowhere outside docs/reports (grep pin); every flip conscious with both-direction evidence.
- [ ] The integration seam is owned end-to-end (seam-review finding 2): the per-run view hands out `production_data_provider_factory(subject_params)`, the real session resolves pm's healed row from the run's committed subject (L1's Test-5 pin consciously flipped), and rows execute the real Phase-3 dispatch/PitGuard/render machinery via delegation (proven end-to-end against an injected fake probe, and mutation-proofed against widening); rowless workers freeze licensed-EMPTY.
- [ ] Honest degradation pinned at the production seam: FutureData/MissingAvailability refusals, optional-UNAVAILABLE vs core-raise, no fabricated rows, no fabricated cache.
- [ ] Lane 0 commits ContextSnapshots carrying the recipe digests + a loadable per-run manifest; the deep lane refuses a pilot-era snapshot **before** any lease draw.
- [ ] pv aux nodes: bridge layer completes; nodes fail `aux_data_ungranted` (typed, naming L3) and degrade with stated absence — `bridge_execution_error` no longer occurs on this path.
- [ ] Full `tests/orchestration` green at baseline+delta; watcher `GUANLAN_SEATS_DEEP`-unset behavior bit-unchanged; server script-launch guard green.
- [ ] LIVE: one real Lane-0 run + one real deep run verified per Task 9 Steps 2–4 — including the mandatory real `verified_snapshot` read resolved from the run's own subject — ledgered; 9999 deployed only at Task 9 (the one-train point).

## Execution Handoff

Recommended order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Tasks 1–2 are parallelizable after 0; Task 6 (pv aux) is independent of 4/5/7 after 3 lands its rowless arm; Task 5 (the integration seam) strictly follows 4. **「控制器已裁」this plan runs on a tree where L1 has already landed — L1 → L2-b is the declared execution order, released as ONE train; Task 0's gate verifies the L1 state, Task 4's correction clause names both possible incumbents, and 9999 deploys only at Task 9.** Interaction with the sibling plans: L1 owned the retirement of the dead-row provider AND its helpers (decided once, there) and chartered the per-run subject seam's flip to Task 5 here (cross-referenced bidirectionally in both plans); L3 (grants + re-freeze) will add rows for `pv.technical`/`text.news` (and possibly the pv aux workers) — Task 2's supported-methods guard test is the tripwire that forces the adapter to grow before any such grant goes live, and L3's Task-1 inventory grep-enumerates this plan's `aux_data_ungranted`, supported-set and route-coverage pins as part of its allowed-red set. Each task brief must carry: the task section verbatim, the Global Constraints block, the do-not-stage list, and the current branch/baseline numbers from Task 0's ledger entry.
