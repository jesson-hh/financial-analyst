# R3 + R18 — the launcher: the framework runs

**Branch** `report-evidence-pack` (never switched, never pushed, never merged) ·
**base** `71a6cf5` · **commits** `33f2b4f` (launcher) + `cafec65` (R3 wiring) + `c46a8a4` (review round) ·
**date** 2026-07-26 · **suite** `pytest tests/orchestration -q` → **4656 passed**
(4627 baseline + 19 + 7 + 3, exactly accounted for)

**Headline, in the framing the reviewer supplied and I endorse — my first version
claimed materially more than was earned:** the launcher's own composition and gating
code executed for real against the real durable store, and a restarted 9998 served the
persisted result — **but every input that produces a decision, including R18's own plan
runner and the approval gate, came from the Phase-9 e2e test fixtures, so the
sequencing is proven and the decision-making half is still missing.** Transcript and
the full stand-in inventory in §4; the classification in §3.2.

**And one thing it cannot do, discovered at source and refused rather than papered
over:** a multi-point interval cannot execute through `dag.run_plan`. §3.3.

---

## 1. What was built

| file | status | what |
|---|---|---|
| `guanlan_v2/orchestration/adapters/launcher.py` | **new**, ~640 lines | the launcher: `launch_interval_replay` + R18's `build_admitted_plan_runner` + the event-loop red line |
| `guanlan_v2/orchestration/startup.py` | modified, +150 | `bind_orchestration_launcher()` — R3's missing production call site |
| `guanlan_v2/server.py` | modified, +45 | `plan_approval_actor` (R21/R22 carry) + the opt-in launcher bind + `GET /orchestration/launcher_status` |
| `tests/orchestration/test_launcher.py` | **new**, 19 tests | |
| `tests/orchestration/test_launcher_wiring.py` | **new**, 7 tests | |

No Phase 1–9 module was modified. No foundation module (`identity.py`,
`replay_cards.py`, `orch_store_status.py`) was modified. `adapters/api.py`,
`adapters/luozi.py`, `approval.py`, `admission.py`, `dag.py` are **consumed only**.
`engine/`, `ui/`, `workflow/executor` untouched. `git diff --stat --
tests/orchestration/golden/` is empty; `phase9_retirement_gates_v1.json` file sha256
still `5e2660f7…`.

### `launcher.py` public surface

```
LAUNCH_LANES = ("bootstrap", "main")
LauncherError / LaunchRefused / PlanExecutionUnwired / MultiPointPlanExecutionRefused
LaneExecutionBinding            # lane -> (candidate digest, reservation id, approval event id)
ReplayLaunchOutcome             # the honest record, incl. .notes and .as_dict()
refuse_if_event_loop_running(what)
run_coroutine_blocking(factory)
pool_sink_artifact_resolver(pool)
build_dag_plan_executor(...)    # dag.run_plan behind the sync bridge
build_admitted_plan_runner(...) # R18 — the coordinator's plan_runner
launch_interval_replay(...)     # the composition + the gates
```

It defines no `ContractModel` (frozen dataclasses + functions only), so the Phase-1
completeness disk-walk and the Phase-9 classification firewall stay inert over it —
the `identity.py` / `replay_cards.py` precedent, re-pinned by a test of its own.
`PHASE9_MODULES` / `PHASE9_CONTRACT_MODULES` untouched.

### The gates, all structural

* `refuse_if_event_loop_running` — the 9999 red line as a refusal, not a comment.
  `run_interval_replay` is sync and `dag.run_plan` is a coroutine, so the launch must
  own a loop-free thread. Called at the top of `launch_interval_replay`, at the top of
  the plan runner, and inside `run_coroutine_blocking`. Deliberately **not** a
  "detect-a-loop-and-hop-to-a-worker" helper: hopping still blocks the caller's loop on
  `.result()`, so hopping would be the same bug with a nicer shape.
* AUTO refused at the door (before any reservation), rejected lane refused, a lane still
  awaiting a human returns an `awaiting_approval` outcome with **nothing started**.
* R22's named trap — a coordinator built without `coordinator_kwargs()` — is refused
  **up front** rather than at the first decision point after budget was reserved.
* Every stage the launch cannot reach becomes a NOTE on the outcome. A stage is never
  silently absent and never faked.

---

## 2. The Option-1 implementation

### 2.1 The fact that makes Option 1 buildable

`spec._EXECUTABLE_FIELDS` (`spec.py:157-181`) — the exact field set
`compute_candidate_plan_digest` binds — **excludes `id`, `run_id` and
`context_snapshot_ref`** and **includes `as_of`**. The digest is
`content_digest({domain, request_digest, context_content_digest, draft.executable_projection()})`.

Therefore:

* a **per-point run identity** (`id` / `run_id`) does **not** move the lane digest;
* a **per-point `as_of`** does, and so does a **per-point ContextSnapshot content**.

That is precisely why the approved plan's `as_of` and its ContextSnapshot must be sealed
at the **interval**, and why the point's PIT data must arrive as a **runtime input**.
Pinned by `test_a_per_point_run_identity_does_not_move_the_candidate_digest`, which
drives the real `materialize_fallback_draft` output through
`compute_candidate_plan_digest` three ways (per-point identity ⇒ same digest; moved
`as_of` ⇒ different; moved context content ⇒ different).

### 2.2 How the launcher expresses it

`build_admitted_plan_runner` takes **one `LaneExecutionBinding` per lane**, carrying the
interval-level candidate digest the R22 card was registered under. Per decision point it:

1. refuses on a running event loop;
2. matches the digest the coordinator's approval gate just approved against a declared
   binding — **an unbound digest is refused and never freeze-attempted**, so the seam
   structurally cannot substitute an identity (Task-12 carry (a), now enforced in
   production rather than only asserted in a test);
3. freezes (§2.3);
4. builds a **per-point `RunContext`** through `run_context_factory(lane, point, plan,
   data_context, memory_binding)` — this is where the point's PIT `DataContext` enters.
   The context is an input to the approved graph, not part of its identity;
5. executes and resolves the sink artifact.

`pool_sink_artifact_resolver` is the real resolver: `RunResult` carries five scalars and
no artifacts, so the produced artifact is read back with
`pool.committed_output(plan.sink_node_ids[0], "primary")`.

### 2.3 The coupled decision: **YES, the runner freezes per point** — with its scope stated precisely

Against the ONE interval lane digest. **Corrected after review: my first write-up of
this claimed a benefit that cannot occur and a mechanism that does not exist.** Both
are recorded here rather than quietly rewritten, because the wrong version is the kind
that would survive a skim.

**What is true — the idempotency is genuine.**
`PlanAdmissionService.freeze_and_admit_candidate` opens with an already-admitted
short-circuit (`admission.py:553-556`): a retry — *even under a fresh idempotency key* —
returns the existing `Plan` + persisted witness **before any `RuntimeBatch` is built**.
No double reservation, no duplicate `PlanFrozen` event, no second admitted payload, no
mutation of any kind.

**What is FALSE, and what I wrongly wrote.** I claimed "the freeze re-loads the approval
authority and re-checks `authorizes_freeze`, so a revocation or catalog reseal stops the
run at the **next** point". Two independent reasons that is wrong:

1. **The short-circuit returns too early to re-verify anything.** It is the *first*
   statement in the function — before the reservation load, before
   `_load_approval_event`, before `authorizes_freeze`, before `_detect_drift`. Only the
   **first** freeze does any of that. Every genuine re-verification lives in
   `verify_for_dispatch` inside `dag.run_plan`.
2. **There is never a next point.** The `MultiPointPlanExecutionRefused` ordinal check
   sits *before* the freeze call (`launcher.py:332-349`), and `executed_at[digest]` is
   set after the first execution — so point 2 is refused before it ever freezes. §2.3
   and §3.3 cannot both be operative, and §3.3 wins. Presenting both as live was the
   error.

**What the per-point cadence actually buys today.** The freeze sits inside the same seam
the coordinator's approval gate guards, adjacent in the code a reviewer reads; a digest
the admission service never prepared is refused `AdmissionRejected(code="unknown_candidate")`
— loud, terminal, never a silent unapproved run; and a re-verification failure
propagates **uncaught** out of `launch_interval_replay` (it raises; it never continues).
The cadence is the right shape for the day the interval-scope reshape (§3.4) lands. It
is not currently buying re-verification, and it should not be sold as if it were.

**The alternative I still reject:** freeze once at launch and hand the frozen `Plan` to
every point. It moves the freeze outside the seam the approval gate guards, so the two
checks would no longer be adjacent in the code a reviewer reads — and it would have to
be undone the moment the reshape makes re-verification reachable again.

---

## 3. What runs end to end — and what does not

### 3.1 Runs, for real

Approval gates → `run_interval_replay` over the real `ProductionReplayPlanCoordinator`
(per-point `build_replay_manifest` + `build_replay_data_context`, per-point approval
lookup, node reservations as children of the ONE interval plan reservation) → 3 intents
through the sole Phase-6 constructor → `build_dual_curves` on one attested
`ShadowBacktestRunner` → `fold_degraded_points_into_report_badges` →
`hand_off_dual_curves_to_feedback` (immature ⇒ parked, ledger untouched) →
`persist_replay_state` into the **production** `ReplayStateStore` over the real durable
store → `seats_rows_from_committed_decisions` → the live 9998 routes.

### 3.2 What was NOT production in the live run — full inventory, classified

**Corrected after review.** My first version said "three real inputs were stand-ins …
everything *between* them was the production article", which was too generous in two
specific ways I have to name:

* **R18's own plan runner was never exercised.** `scratchpad/launch_r3.py` passed
  `plan_runner=_E2ePlanRunner()` — the Phase-9 **test double** — directly into the
  coordinator. So `build_admitted_plan_runner`, the headline deliverable of this commit
  and the entire subject of §2.2/§2.3, did not run at all in the live launch: no
  `freeze_and_admit_candidate`, no digest-binding guard, no multi-point guard, no async
  bridge, no `dag.run_plan`. It is covered by unit tests only.
* **The approval gate was `_RecordingApproval()`**, the cards were an ad-hoc inline
  `_Cards` class rather than R22's real `ReplayApprovalCards`, and
  `_world` / `_request` / `_bindings` / `_coordinator` / `_FakePool` / `_SpyLedger`
  supplied nearly every remaining input.

Classified, because the two kinds are not the same problem:

**Substitutes for something that does not exist** (nothing to wire *to*):

1. **The plan-execution seam.** No production plan graph sinks
   `PortfolioTargetProposal@1` — which is what `wrap_proposal_as_intent` requires
   (`shadow.py:948-953`). The only worker that emits it is `dec.trader`
   (`lane_catalog.py:1113-1124`), a **lane-catalog declaration only**: no production
   plan node references it and there is no production `CatalogRuntime` loader for the
   Phase-8 lane catalog (only `load_pilot_catalog` / `load_compat_catalog`).
   `config/orchestration/presets/` holds **exactly one file**, whose sink is `dec.pm` →
   `PortfolioDecision@1`. Every worker in both YAML catalogs is `ExecutionKind.LLM`
   (`catalog_runtime.py:888`), and the repo's one production `ModelGateway`
   (`planner_gateway.PlannerLLMModelGateway`) returns `payload=None`, so it is unusable
   for worker nodes.
2. **The approval gate + cards as used in the live run.** A real
   `PlanApprovalCoordinator` + `ReplayApprovalCards` needs a process-level
   `PlanAdmissionService`, which no production assembler builds (C1).

**Scaffolds for things that exist but are unwired** (something to wire *to*, unwired):

3. **The PIT fixture world.** `PitReaderRawSource` is real and reads the engine
   pit_store; the live run used the frozen Phase-9 fixture reader instead. Nothing
   structural blocks the real one.
4. **The report artifact barrier.** A real `ArtifactPool` cannot validate an
   unregistered `DualCurveReport@1` — the Task-5 correction N5-4 that made
   `hand_off_dual_curves_to_feedback` take a staged→barrier sink. Registering the schema
   closes it. **Consequence observed live:** `/orchestration/replay/curves` badges
   `curve_report:unresolvable`, the badge doing exactly its job.

The original §3.2 text, kept because its source findings are the evidence for (1):

1. **The plan-execution backend.** `build_admitted_plan_runner` is complete and real, but
   its `plan_executor` / `sink_artifact_resolver` are the launcher's declared boundary,
   and with them unbound it raises `PlanExecutionUnwired` rather than synthesizing an
   artifact. The reason it cannot be bound to a real replay-lane plan today is
   structural, confirmed at source:
   * **no production plan graph produces a `PortfolioTargetProposal@1`**, which is what
     `wrap_proposal_as_intent` requires (`shadow.py:948-953`). The only worker that emits
     it is `dec.trader` (`lane_catalog.py:1113-1124`), and **no production plan node
     references it** — the five production `PlanDraft` builders sink to
     `PortfolioDecision@1` / `SentimentReport@1` / `ResearchPlan@1` /
     `RegimeReport@1`+`RotationReport@1`. `config/orchestration/presets/` holds exactly
     one preset and its sink is `dec.pm` → `PortfolioDecision@1`;
   * **there is no production `CatalogRuntime` loader for the Phase-8 lane catalog**
     (only `load_pilot_catalog` and `load_compat_catalog` exist), so `dec.trader` is not
     even reachable;
   * **every worker in both YAML catalogs is `ExecutionKind.LLM`**
     (`catalog_runtime.py:888` hardcodes it), and the repo's one production
     `ModelGateway` (`planner_gateway.PlannerLLMModelGateway`) returns `payload=None`, so
     it is unusable for worker nodes. There is no production `ExecutionRuntime`,
     `ArtifactPool` or `TrustedFactoryRegistry` assembler either.
2. **The `DualCurveReport` artifact barrier.** A real `ArtifactPool` cannot validate an
   unregistered `DualCurveReport@1` — the Task-5 correction N5-4 that made
   `hand_off_dual_curves_to_feedback` take a staged→barrier sink in the first place. The
   verification used the reviewed staged→commit stand-in. **Consequence observed live:**
   `/orchestration/replay/curves` badges `curve_report:unresolvable`, which is the
   badge doing exactly its job.

### 3.3 The structural finding — refused, not papered over

**A multi-point interval cannot execute one admitted plan N times.**

**The blocker is NOT the approval** — my first write-up said it was, and that was wrong
in a way that would have sent the next person at the wrong sealed surface. It also
contradicted my own §2.1 fact. Since `_EXECUTABLE_FIELDS` excludes `run_id`
(`spec.py:154-181`), N per-point drafts differing only in `run_id`/`id` share **one**
candidate digest — there is no "family" to authorize — and
`PlanApproval.authorizes_freeze` (`events.py:451-455`) binds only
`(request_id, candidate_plan_digest)`: it is run-agnostic, and the existing approval
**already authorizes freezing every one of them**. `freeze_plan` sets
`run_id=draft.run_id` with no cross-check. **No Phase-7 contract change is needed.**

What actually blocks it is four **digest-keyed singletons**:

| # | singleton | source |
|---|---|---|
| 1 | the run-scoped admitted-event short-circuit — one admitted `Plan` per candidate digest, hence one `run_id` | `admission.py:894-898` |
| 2 | the single-slot active-plan reservation index | `budget.py:969-974` |
| 3 | the Plan-scoped `ArtifactPool`, whose `stage()` raises `LateStageError` on an already-committed output key | `pool.py:291-293`, `:339-340` |
| 4 | `dag.run_plan`'s resume-by-`run_id` | `dag.py:510`, `:538` + the `layer_index in committed_layers` branch |

**Reviewer's sharpening, which I under-stated:** (3) fires *before* (4) — re-execution
dies at `LateStageError` before resume ever gets a say. My original write-up leaned only
on (4), so the refusal is even better founded than I argued.

Without the guard, decision point 2 would be served the artifacts point 1 committed — a
borrowed-snapshot PIT leak, exactly what the coordinator's `_verify_bootstrap_return`
exists to catch. `MultiPointPlanExecutionRefused` makes it a refusal **at the seam**,
with the causal chain in the message. `test_a_second_decision_point_is_refused_not_
silently_served_stale` pins it, and also pins that the *same* point re-asked is
idempotent (a retry is legitimate; a new point is not).

### 3.4 What the collision actually is, and the standing decision

**It does not falsify Option 1.** The collision is between Option 1 and the
**coordinator's per-point `plan_runner` seam**. Read literally, Option 1 describes an
interval-shaped graph run **once**, which fights none of the four singletons; this
module runs one plan **N times** only because the sealed `ReplayPlanCoordinator` port
calls the seam once per point per lane.

**Standing decision (coordinator's ruling — recorded, deliberately NOT implemented
here): (A) reshape the plan graph to interval scope and call `plan_runner` once per
interval.** The per-point `ContextSnapshot`s then come from per-point *nodes inside one
interval graph*, committed by node execution exactly as Option 1 says, with the seam
returning the slice for the requested point.

**(B) per-point admission scopes is rejected**: contract-legal, but it mints N approval
events and N run budgets and breaks the coordinator's one-interval-reservation
parentage.

(A) needs a production interval-shaped plan graph to exist — the same missing piece as
"no production graph emits `PortfolioTargetProposal@1`" (C3). Recorded in the module
docstring as well as here.

---

## 4. The live 9998 transcript

`python = D:\app\miniconda\python.exe`. **9999 was never touched** — it was already down
and its heartbeat 6.2+ days stale, so (following R23's discipline) I saved
`var/check_9999.heartbeat`'s mtime, freshened it for the duration so
`_checker_revive_loop` could not spawn a generation, and restored it **bit-exact**
afterwards (`639200859302093205` before and after, verified). `scripts/check_9999.ps1`
untouched. Store root pointed at the scratchpad, so `var/orchestration/` was never
created.

### 4.1 The routes go live (R3)

```
GUANLAN_PORT=9998 GUANLAN_ORCH_LAUNCHER=1 GUANLAN_ORCH_STORE_ROOT=…/orch_root \
  python -m guanlan_v2.server &
PYTHONPATH=G:/guanlan-v2 python scratchpad/probe_r3.py          # exit 0

GET /orchestration/store_status    -> 200 {"state":"bound","bound":true,
                                           "cell_namespace_count":14,"error_type":null}
GET /orchestration/launcher_status -> 200 {"state":"bound","replay_state_store":true,
                                           "replay_bindings":false,
                                           "shadow_wakeup_context":false,
                                           "schedule_count":0,"error_type":null,
                                           "notes":[…3 honest notes…]}
GET /orchestration/replay/state    -> 404 {"ok":false,"reason":"unknown_experiment"}
GET /orchestration/replay/curves   -> 404 {"ok":false,"reason":"unknown_experiment"}
ASSERTED OK
```

`unknown_experiment` instead of `replay_store_unwired` **is** R3's closure for the read
surface: the route is answering from a real store about a run that does not exist, rather
than confessing it has no store. The probe asserts (does not eyeball) `state=="bound"`,
`cell_namespace_count==14`, `replay_state_store is True`, `replay_bindings is False`, and
that neither GET carries `reason == "replay_store_unwired"`.

### 4.2 The framework runs

Server stopped by PID, then one production launch over the same store root
(`scratchpad/launch_r3.py`; the script's own docstring names exactly which three inputs
are stand-ins — §3.2):

```
[stores]   bound 14 namespaces
[launcher] bound replay_state_store= True
[outcome] {
  "status": "parked",
  "lane_candidate_digests": {"bootstrap": "0bfb8ff42cb79a1d…4168",
                             "main":      "2ca42645eb71ae7f…103a"},
  "plan_candidate_digest":   "18e76fe8225e05fb73d7cf3676f675a688c57d64cb24c17daeb6c376e691c9b5",
  "execution_config_digest": "e0f71cbadefe7f344819e115699004ca1ebf7e760166e16cd0c30769c4679867",
  "schedule_digest":         "0c26c7bf897d4506b6f2e5edc688f2f414701c60730d01f0fffeb9ded6be3eba",
  "run_id": "replay-run.req-replay",
  "experiment_id": "shadow-replay.replay-run.req-replay",
  "completed_points": 3, "total_points": 3,
  "intent_count": 3, "deterministic_target_count": 3,
  "degradation_badges": ["st_flags_unavailable"],
  "curve_report_ref":  "…DualCurveReport@1 main dualcurve.replay-run.req-replay 33c6989f…",
  "state_payload_ref": "…main payload-1 151265acf413166b06f5be6e82604c4eba16432b3fc522a759f54f0126ecf9b4",
  "seats_row_count": 3,
  "notes": []
}
[durable head] shadow-replay.replay-run.req-replay waiting_for_maturity 3 / 3
```

`"notes": []` is the load-bearing value: **every stage was reached.** The single
degradation badge (`st_flags_unavailable`) is a real fixture-feed verdict, badged, with
the run continuing — the degradation-always-badged red line, observed rather than
asserted.

### 4.3 It survives process death and the live server serves it

9998 restarted over the store the launch wrote:

```
GET /orchestration/store_status  -> 200 bound 14
GET /orchestration/replay/state?experiment_id=shadow-replay.replay-run.req-replay
  -> 200 {"ok":true,"state":{"experiment_id":"shadow-replay.replay-run.req-replay",
          "run_id":"replay-run.req-replay","status":"waiting_for_maturity",
          "completed_points":3,"total_points":3,
          "schedule_digest":"0c26c7bf…3eba","execution_config_digest":"e0f71cba…9867",
          "resume_after":"2026-07-09T01:30:00+00:00",
          "wakeup_key":"7fabe8720ea0a9ee054356aeecb82b207397bd1f9895356bd94a489bc39386f4",
          "has_curve_report":true,…}}
GET /orchestration/replay/curves?experiment_id=…
  -> 200 {"ok":true,"report":null,"status":"waiting_for_maturity",
          "resume_after":"2026-07-09T01:30:00+00:00",
          "badges":["source:orchestrated","status:waiting_for_maturity",
                    "waiting_for_maturity","curve_report:unresolvable"]}
```

An immature run answers `report: null` + `resume_after` — never a fabricated curve — and
the unresolvable report ref is **badged**, per §3.2(2).

### 4.4 What the 9998 run did NOT do, deliberately

* **The seats-compat rows were produced (3) but NOT appended to the production 台账.**
  `persist_replay_run_compat` writes `var/seats_runs.jsonl` / `var/seats_decisions.jsonl`
  — the user's real 落子 ledger. Polluting it during a verification run is not mine to
  do. The `/seats/runs` + `/seats/decisions` HTTP round-trip over tmp logs is covered by
  the reviewed `test_phase9_e2e.py::test_luozi_interval_e2e`.
* **The console approval decide endpoint was not exercised**, because it is still inert
  in production — see carry C1.
* **No real LLM call, no order, no signal write, no factorlib write.**

### 4.5 Teardown

Every 9998 process stopped by PID (`Get-NetTCPConnection -LocalPort 9998` →
`Stop-Process`); port clear. 9999 never targeted, still down. Heartbeat mtime restored
bit-exact and the temporary marker file deleted. `var/orchestration/` does not exist.
Nothing under `var/` staged.

---

## 5. TDD RED → GREEN

**Launcher, RED (test file written first):**

```
$ python -m pytest tests/orchestration/test_launcher.py -x -q
E   ImportError: cannot import name 'launcher' from 'guanlan_v2.orchestration.adapters'
1 error in 0.19s
```

**RED → partial GREEN:** first run after implementing `launcher.py` — **5 failed, 12
passed**. Four of the five were *my test harness being wrong*, and one was the
implementation being right in a way I had not planned for:

* `_refuse_forgotten_overrides` fired on my own fixture, because `_FakeCards` handed
  literal `"b"*64` digests while the coordinator held its real synthetic defaults. That
  is the R22 trap guard working. Fixed by making the cards name the coordinator's actual
  lane digests — **and then pinning the mismatch deliberately** with a new test
  (`test_a_coordinator_built_without_the_card_digests_is_refused_before_the_driver`), so
  the accident became a guarded property instead of a lucky coincidence.
* `assert "rejected" in …` vs the message's `REJECTED`; `ContractModel` imported from
  the wrong module (`schemas` → `digest`); a non-existent `_dynamic_env` helper (the real
  ones are `_build_env` + `_materialize_fallback`).

No implementation was weakened to make a test pass.

**GREEN:** `19 passed in 9.81s` (17 + the two added guards).

**Wiring, RED:**

```
$ python -m pytest tests/orchestration/test_launcher_wiring.py -q
E   AttributeError: module 'guanlan_v2.orchestration.startup' has no attribute
    'reset_launcher_status_for_tests'
7 errors in 0.87s
```

**GREEN:** `7 passed`. One intermediate failure was again my test: the AST guard looked
for a call named `bind_orchestration_launcher` while `server.py` imports it aliased, so
the guard was vacuous in the wrong direction — rewritten to resolve the `ImportFrom`
alias inside the `Try` node and match *that* name, which is what it always meant.

**Full suite, FOREGROUND, before the second commit:**

```
$ python -m pytest tests/orchestration -q
4653 passed in 333.48s (0:05:33)
```

`4627 + 19 + 7 = 4653` exactly — no pre-existing test changed state, no contamination
this round (the branch head did not move under me).

---

## 6. Carries consumed

| from | what I consumed | how |
|---|---|---|
| **R21** | `ConfigOperatorVerifier()` zero-arg; `lease:*` refused by design | not re-litigated; no launcher path calls `decide(actor="lease:…")` — the module's AST guard forbids `decide` / `record_approval` / `register_and_try_lease` / `issue_lease` / `admit_after_approval` outright |
| **R21 §11 · R22 §3** | *nothing produces the actor material*; `declared_operator_actor` shipped for me; the injected id **must match the shipped declaration** | closed in `server.py`: `_console_kw["plan_approval_actor"] = declared_operator_actor` — the **callable**, so the declaration in force at decision time is authoritative, and the id is the shipped one **by construction** (same loader, same file). Guarded by a call-site test |
| **R22** | `coordinator_kwargs()` overrides are mandatory; forgetting them refuses loudly; a decided lane's `cards[lane]` is the freshly-built card, not what the human read | `_refuse_forgotten_overrides` turns the loud-at-first-point refusal into a loud-before-the-driver refusal. The launcher **never renders a card** — it reads `awaiting_human()` / `already_decided` only, so it cannot show a rebuilt card as "what the human saw" |
| **R23/R24** | do NOT re-bind the process stores; the namespace set is frozen at construction; *a bound store ≠ a wired framework* | `bind_orchestration_launcher` reads `process_durable_stores()` and builds the `ReplayStateStore` **over it**; it never calls a store constructor. `stores=` exists only as a test seam |
| **R23** | the `/data/health` consumer is a two-line wiring I must NOT do (`datafeed/health.py` foreign-dirty) | not done. `health.py` never opened for writing |
| **R17** | in-process registry · exclusion binds only ticks that START after registration · needs a real `is_live_session` + the real `watcher` module | `is_live_session` is passed straight through `launch_interval_replay` to the driver, and both facts are stated in the launcher's own docstring. The 9998 run was historical, so it registered nothing — which is R17's *correct* behaviour, not a gap |

---

## 7. Carries created

| # | carry |
|---|---|
| **C1** | **The console decide endpoint is still inert in production**, and the actor line does not change that. `_console_kw` is non-empty only when `bind_process_plan_approval_coordinator()` returns a coordinator, which needs `set_plan_approval_admission_provider` — still never called in production, because a process-level `PlanAdmissionService` needs a run-scoped catalog/profile/bridge-view/run-budget composition that no production assembler builds. So the actor material is wired and correct, and currently unreachable. R22's carry 1 is **half** closed: the line exists; the coordinator it feeds does not |
| **C2** | **A multi-point interval cannot execute one admitted plan N times** (§3.3). **Not an approval problem and NOT a Phase-7 change** — `_EXECUTABLE_FIELDS` excludes `run_id`, so per-point drafts share one digest, and `authorizes_freeze` (`events.py:451-455`) is run-agnostic and already authorizes all of them. The blockers are four digest-keyed singletons: the admitted-event short-circuit (`admission.py:894-898`), the active-plan reservation index (`budget.py:969-974`), the Plan-scoped `ArtifactPool` (`pool.py:291-293`, `stage()` → `LateStageError` at `:339-340`) and resume-by-`run_id` (`dag.py:510`/`:538`). The standing resolution is ruling (A) in §3.4: an interval-shaped graph called once |
| **C3** | **No production plan graph produces a `PortfolioTargetProposal@1`** (§3.2). Until one exists — a `dec.trader` graph plus a production `CatalogRuntime` loader for the Phase-8 lane catalog — the LLM lane has nothing real to run |
| **C4** | **No production `ExecutionRuntime` / `ArtifactPool` / worker-seat `ModelGateway` assembler exists.** `PlannerLLMModelGateway` returns `payload=None` and is unusable for worker nodes; `register_model_factory` has zero production callers. `build_dag_plan_executor` is ready for them and refuses without them |
| **C5** | **`replay_bindings` and the shadow-wakeup context provider are still unbound**, so `POST /replay/wakeup` keeps its honest 503 and the maturity playbook has no production driver. A `ReplayRuntimeBindings` is run-scoped; binding a process-level one would be an invention. This needs the launcher to own a *registry of live runs*, which is a design decision, not a wiring line |
| **C6** | **The `DualCurveReport` payload has no registered schema in the artifact pool** (Task-5 N5-4), so a real run's `curve_report_ref` resolves to `curve_report:unresolvable` on `/replay/curves`. Observed live (§4.3). Registering `DualCurveReport@1` in the pool's registry would close it |
| **C7** | **`GET /orchestration/launcher_status` is new public surface, and it is a 7th path under a prefix documented as a closed six.** It registers in `server.py` *outside* the router, so it lands under the sealed prefix while evading the route-table snapshot guard — as `store_status` (R23) already did. Rather than edit the sealed `ORCHESTRATION_ROUTE_PATHS`, I **extended the guard to see them**: `test_no_unguarded_path_hides_under_the_sealed_orchestration_prefix` pins the server-registered set to exactly those two, each with a written reason, and asserts neither shadows a router path. **Correction to my first draft: "no path disclosure" was wrong** — the record carries `error: str(exc)`, which can embed the store root, exactly as `store_status` does. Read-only and no credentials, but not path-free. Drop the route if judged too much surface; `startup.orchestration_launcher_status()` remains the in-process accessor |
| **C9** | **`build_admitted_plan_runner` has never run outside a unit test.** R18's deliverable is covered by 8 focused tests and by nothing end to end — the live launch bypassed it entirely (§3.2). Whoever first binds a real `plan_executor` is also its first integration test |
| **C8** | **The launcher has no production trigger.** It is a function, not a route or a scheduler: `ORCHESTRATION_ROUTE_PATHS` is a sealed reviewed surface and adding to it means editing `build_adapters_router`'s body. Whoever adds one must run it on a **loop-free thread inside the server process** (R17) — `refuse_if_event_loop_running` will tell them if they do not |

---

## 8. Concerns

1. **"The framework ran" was an overclaim, and I made it.** The corrected framing is in
   the headline and §3.2: the launcher's **sequencing and gating** executed for real
   against the real durable store and a restarted 9998 served the persisted result, but
   every decision-producing input — including R18's own plan runner, which is this
   commit's headline deliverable and was *never in the live path* — came from the
   Phase-9 e2e fixtures. The sequencing is proven; the decision-making half is missing.
   The failure mode here was mine and worth naming: I wrote the honest inventory in
   §3.2 and then wrote a summary in §8 that was looser than the inventory it summarized.
2. **I read two private attributes of a sealed object.** `_refuse_forgotten_overrides`
   reads `coordinator._bootstrap_candidate` / `_main_candidate` to catch R22's trap
   before the driver starts. Coupling to a private name; a rename upstream would turn
   the guard into a NOTE rather than a false pass (it now says "the R22
   forgotten-overrides guard did not run for it" instead of passing silently — review
   Minor 6), but a public accessor on the coordinator would still be better, and that is
   a sealed-surface change.
2b. **The two fail-open paths the reviewer found are closed** (Minor 6): a cards object
   that cannot name a lane digest now **refuses** the launch (or is named, while
   awaiting a human) instead of being swallowed and the lane silently dropped from
   `lane_candidate_digests`; and a coordinator that hides its lane digests is **named**
   on the outcome. Both are pinned by tests.
3. **`GUANLAN_ORCH_LAUNCHER` is opt-in, which means the default is still "nothing runs".**
   Deliberate: turning a previously-refusing surface live inside the process that serves
   选股/落子/帷幄, while the launcher's own admission half is unfinished, should be a
   decision rather than a side effect of a restart. R3 is therefore closed *behind a
   flag*. **Narrowed claim (review Minor 5):** default-off keeps **the six
   `/orchestration` router routes** byte-unchanged — *not* "production is
   byte-unchanged". `GET /orchestration/launcher_status` registers on **every** boot (it
   must, or an operator could not ask why the subsystem is off), and `server.py`'s
   `plan_approval_actor` line runs whenever `_console_kw` is non-empty — inert today
   only because the Phase-7 coordinator is `None`, **not** because the flag is off.
3b. **Loop-lifetime hazard, recorded at the bridge (review Minor 7).** `asyncio.run`
   creates and closes a fresh loop per call, so a future `ModelGateway` holding a
   loop-bound `httpx.AsyncClient` would fail on the second call — this repo has been
   bitten by httpx binding to a loop before. Noted in `run_coroutine_blocking`'s and
   `build_dag_plan_executor`'s docstrings, where whoever binds such a gateway will read
   it; the remedies (construct per call, or own one long-lived loop on the launch
   thread) are named there.
4. **The single-execution guard is per-runner-instance, not durable.** It lives in a
   closure dict. A crashed-and-relaunched interval builds a fresh runner and would
   re-attempt point 1 — where `dag.run_plan`'s own resume semantics take over correctly,
   so the outcome is right, but the *refusal* for point 2 would only re-arm after point 1
   ran again. Correct in every direction I could construct; worth a reviewer's eye.
5. **`ReplayLaunchOutcome.notes` is prose.** It is the honest surface, and it is
   unstructured — a consumer cannot switch on it. If anything ever needs to *act* on a
   skipped stage, the notes should become typed reasons first.
6. **The 23-step survey map is now stale in a new place.** Its blockers 1/2/5/6 were
   closed by R21–R24; this pass closes the router-deps half of R3 and all of R18's seam,
   and adds C2/C3/C4 which the map did not contain. Re-derive at source, as I was told
   to and as I did.

---

## 9. Review round 1 — three fixes + four minors, all closed (commit `c46a8a4`)

Verdict: **Needs fixes, zero Critical.** Verified independently by the reviewer and
worth recording: the golden directory *tree object* is byte-identical at both ends
(stronger than the diffstat I cited); the heartbeat ticks `639200859302093205` are
exactly what I claimed to restore; the event-loop refusal holds at three call sites and
nothing on the route surface can block the loop; and the multi-point refusal is correct
and **under-stated** — `pool.stage()` raises `LateStageError` on an already-committed
output key (`pool.py:339-340`), so re-execution is structurally impossible *before*
resume gets a say.

| fix | where | what changed |
|---|---|---|
| **1** — C2 mis-located the blocker (it is not a Phase-7 change) | C2 + §3.3 + module docstring | rewritten around the four digest-keyed singletons; the "family of digests" claim retracted |
| **2** — §2.3 described an unreachable path and a wrong mechanism | §2.3 + module docstring | rewritten; the two properties that *are* true (genuine idempotency, uncaught propagation of a re-verification failure) kept and stated as such |
| **3** — "the framework ran" overclaimed; R18 was not in the live path | headline + §3.2 + §8.1 | replaced with the reviewer's framing; full stand-in inventory added and classified into *substitutes for what does not exist* vs *scaffolds for what exists unwired* |
| **4** — a 7th path under the sealed prefix | new test | `test_no_unguarded_path_hides_under_the_sealed_orchestration_prefix`; C7's "no path disclosure" corrected |
| **5** — byte-unchanged overclaimed | `startup.py`, test docstring, §8.3 | narrowed to the six router routes |
| **6** — two silent fail-opens in the R22 guard | `launcher.py` + 2 tests | a cards object that cannot name a lane digest now refuses; a coordinator hiding its digests is named |
| **7** — loop-lifetime hazard undocumented | `run_coroutine_blocking` + `build_dag_plan_executor` docstrings, §8.3b | recorded where whoever binds a gateway will read it |

**Ruling (A) recorded, not implemented** — §3.4 and the module docstring.

Suite after the fixes: `pytest tests/orchestration -q` → **4656 passed** in 333 s
(4653 + 3 new tests: two for Minor 6, one for Minor 4). Focused re-run of the launcher,
wiring, startup, R21 and R22 suites: 187 passed. No golden moved; no sealed module and
no foreign-dirty file touched; nothing from `var/`; no server started this round and
9999 never touched.
