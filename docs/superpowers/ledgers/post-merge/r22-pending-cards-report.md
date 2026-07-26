# R22 — pending approval-card registration + candidate-digest alignment

**Branch** `report-evidence-pack` · **commits** `b74f397` (R22) + `7b6b91e` (review round 2)
· **date** 2026-07-26
Started from HEAD `8fcfa8f`; the parent is `2deecc2` because a concurrent sibling session
committed its own R17 follow-up (`fix(seats): symmetric ensure_owned guard …`) while this
work was in flight. No branch switch, no push, no merge. Explicit pathspecs only.

---

## 1. The steer, validated at source — and why I deviated

> **Steer:** `/replay/start` seals the `ShadowExecutionConfig` (request input + deployment
> configuration) and registers both pending cards, so one human approval moment authorizes
> exactly what will run.

**Not buildable, and the reason is stronger than "the route lacks the inputs".**

### 1a. `/replay/start` cannot seal a `ShadowExecutionConfig`

`ShadowExecutionConfig` (`adapters/contracts.py:154-188`) has ten semantic fields. Against
what the route is given (`code`, `schedule_id`, `schedule_version`, `start_date`,
`end_date`, `strategy_id?`) plus `AdaptersRouterDeps` (`api.py:210-227`: schedule registry,
replay state store, replay bindings, clock, three request dicts):

| field | reachable at `/replay/start`? |
|---|---|
| `schedule_digest` | yes — the resolved `DecisionSchedule` |
| `matching_engine_version` | yes — the constant `SHADOW_MATCHING_ENGINE_VERSION` |
| `calendar_id` | arguably — via the schedule |
| `clock` (`ClockSpec`) | arguably — via `deps.clock` |
| `universe` | arguably — the single `code` |
| **`init_cash`** | **no** |
| **`data_snapshot_content_digest`** | **no** |
| **`vintage_manifest_digest`** | **no** |
| **`cost_model_digest`** | **no** |
| **`intrabar_exit_priority`** | **no** |

And the decisive fact: **no production code anywhere constructs a `ShadowExecutionConfig`.**
`grep -rn "ShadowExecutionConfig(" guanlan_v2/ tests/` returns four hits, all in tests.
`run_interval_replay` (`adapters/luozi.py:2542-2546`) takes the config as an *argument* and
has no production caller. The two snapshot digests are not merely absent from the route —
their run-level identity is an open modelling question (`build_replay_manifest` seals a
manifest **per decision point**, at that point's `as_of`; which of those N manifests is
"the run's" is not decided anywhere). Sealing a config at the door would mean inventing
five deployment values and answering an unanswered modelling question by fiat, producing a
card that approves a 口径 the run will not use — explicitly worse than the current honest
refusal, per the brief.

### 1b. The deeper finding: the coordinator's DEFAULT lane digests can never be decided

This is what actually shaped the design, and it invalidates the naive form of *both* the
steer and my own first alternative.

`ProductionReplayPlanCoordinator` defaults its two lane digests to synthetic identities
(`api.py:1256-1265`):

```python
content_digest({"domain": "shadow-replay-bootstrap-candidate-v1",
                "plan_candidate_digest": self._plan_candidate_digest})
```

Every `PlanApprovalCoordinator.decide` ends in `_ensure_event` →
`PlanAdmissionService.record_approval(candidate_id=…)`, and that service refuses
(`admission.py:475-477`):

```python
state = self._candidates.get(candidate_id)
if state is None:
    raise AdmissionRejected("no reserved candidate for this digest", code="unknown_candidate")
```

A synthetic domain digest is **never** one of the service's prepared candidates. So a card
registered under a default lane digest can be *listed* but **never decided** the moment the
coordinator is bound to the real admission service — which R21 already proved is the real
production shape. Registering cards for the defaults would have shipped something that
demos green against an admission fake and dies on the first real wiring.

Two further consequences of the same fact:

* the eventual freeze checks the *same* namespace —
  `freeze_and_admit_candidate` → `approval.authorizes_freeze(request_id=…,
  candidate_plan_digest=plan_digest)` (`admission.py:718`), where `plan_digest` is
  `compute_candidate_plan_digest(request, draft, context_content_digest)`
  (`spec.py:765-783`). Binding the approval to the real candidate digest makes **one**
  human decision satisfy **both** gates instead of two unrelated ones;
* a default digest binds `(request_id, schedule_digest, execution_config_digest)` but
  **not the plan**, so an approval under it would authorize any plan whatsoever. The real
  candidate digest binds the request, the entire executable draft projection and the
  ContextSnapshot content.

The sealed coordinator already anticipates exactly this: its constructor takes
`bootstrap_candidate_plan_digest=` / `main_candidate_plan_digest=` overrides
(`api.py:1221-1222`). Those parameters exist for a caller that has a *real* candidate
digest. R22 is that caller.

### 1c. What I built instead — the honest alternative

A **prepare step**, as the brief anticipated, but shipped as a production module rather
than a seventh route (see §6 for why not a route):

**`guanlan_v2/orchestration/adapters/replay_cards.py`** (new, 445 lines, pure addition).

```
REPLAY_LANES = ("bootstrap", "main")
LANE_CANDIDATE_DOMAINS          # the coordinator's fallback domains, transcribed
ReplayCardError / ReplayLaneUnknown / ReplayCardRefused
ReplayLanePlan                  # INPUT: one lane's already-prepared admission candidate
ReplayApprovalCards             # OUTPUT: the two cards + every identity the run needs
coordinator_default_lane_candidate_digests(*, plan_candidate_digest)
build_replay_lane_card(*, plan, request, payloads, registry_digest, requested_at, namespace="main")
register_replay_approval_cards(*, coordinator, request, schedule, execution_config,
                               lane_plans, payloads, registry_digest, requested_at=None)
```

`register_replay_approval_cards`:

1. requires **both** lanes together (one human moment authorizes both, or the run dies at
   its first decision point anyway);
2. **requires the sealed `ShadowExecutionConfig`** — this is the steer's ordering
   requirement expressed as a *parameter* rather than a comment: the approval moment
   structurally cannot precede the sealing of the config the run will use. The config is
   an input, not something this module invents (§1a);
3. re-verifies every lane's declared `candidate_plan_digest` against
   `compute_candidate_plan_digest(request, draft, context_content_digest)` and refuses a
   mismatch — **a card can never be minted under a digest that does not bind its own
   draft**, which is the single property that makes the approval mean something;
4. builds the `PlanDiff`, **really `put`s it into a real payload store** under a real
   registry digest, and builds the card through the reviewed
   `build_pending_plan_approval` (so `rendered_md` is sealed to the stored payload);
5. calls `coordinator.register_pending(...)` — the first production caller in the repo —
   with deterministic idempotency keys;
6. returns `ReplayApprovalCards`, whose `coordinator_kwargs()` is exactly
   `{bootstrap_candidate_plan_digest, main_candidate_plan_digest}` for the replay
   coordinator, plus `plan_candidate_digest` (the driver's interval reservation identity).

### 1d. What the approval binds — stated plainly

The human decision binds **the plan** (request + full executable draft + ContextSnapshot
content). It does **not** bind the `ShadowExecutionConfig`. That is not a hole, because the
config is enforced structurally on a different edge: `_reserve_bootstrap_child`
(`api.py:1293-1311`) resolves the driver's ONE interval plan reservation by
`derive_replay_plan_candidate_digest(request_id, schedule_digest,
execution_config_digest)` and raises `ShadowContractError` when there is none — so a run
under a different config finds no reservation and refuses before any node work. Both
identities are returned together on `ReplayApprovalCards` so a caller sees the pair.

One residual: `llm_proposal` performs no reservation, so the config check reaches it only
transitively (the driver always calls `bootstrap_context` for a point before
`llm_proposal`, so a config mismatch has already refused). Recorded as a carry.

---

## 2. The fate of `derive_replay_start_candidate_digest`

**Superseded as an approval identity; retained, unmodified, as the request-level
correlation id.** Not deleted, and `api.py` was not touched at all.

* It was never honoured by any gate. `_require_approval` looks up only the two lane
  digests; the console's `/console/plan/approvals/decide` would answer
  `UnknownPendingCandidate` for it because no card was ever registered under it. So today
  it is a *fail-closed dead end*, not a hole — R22 builds the live end elsewhere rather
  than making the dead end approvable.
* Making it approvable would be the exact thing the brief forbids: it exists precisely
  because "at start time no `ShadowExecutionConfig` exists yet" (correction N10-2,
  `api.py:184-188`). A card under it would authorize a run whose 口径 is not yet decided.
* Its remaining real job: it is the identity `/replay/start` publishes and stores in
  `replay_requests[request_id]["candidate_plan_digest"]`, i.e. the handle by which a
  caller names the request it just opened. Consumers: the route's own response and that
  record — nothing else in the repo reads it (grep-verified), so nothing breaks.
* The demotion is recorded **behaviourally**, not as a comment I could not add to a sealed
  file: `test_the_start_candidate_digest_authorizes_neither_lane` asserts the start digest
  differs from both lane digests and from the interval plan digest, that no decision
  exists for it, and that `_require_approval(start_digest, lane="bootstrap")` raises. If
  anyone ever wires it into an approval path, that test breaks.

The naming remains genuinely misleading (`candidate_plan_digest` in the route response is
not a candidate plan digest in the Phase-1 sense). Renaming it is a sealed-surface change
with a reviewed route-response contract behind it — carried, not done here.

---

## 3. Where the actor material comes from

R21's §11 carry: *nothing in the repo produces the actor material*, so
`console/api.py:999`'s `_resolve_actor(None)` refused.

**Shipped: `identity.declared_operator_actor(path=None) -> str`** (+42 lines in
`guanlan_v2/orchestration/adapters/identity.py`, R21's own file — a new module, not a
sealed Phase 1-9 one).

* It returns **the** id declared in `config/orchestration/operators.json`, loaded through
  R21's own `load_operator_allowlist`. Source and authority are therefore the *same
  declaration*, so an id it returns is by construction an id `ConfigOperatorVerifier.verify`
  accepts — pinned by `test_declared_operator_actor_returns_the_shipped_declaration`, which
  round-trips the shipped file through both.
* **Fail-closed on ambiguity**: more than one declared operator raises
  `OperatorAllowlistError` rather than picking one, because the server cannot know which
  human is at the console and a guess would stamp a durable approval with the wrong name.
  Zero operators / missing / malformed raise for R21's reasons.
* **Pass the callable, not its value.** `console/api.py::_resolve_actor` calls a callable,
  so `plan_approval_actor=declared_operator_actor` makes the declaration in force at
  *decision* time authoritative — the same per-use re-read `verify` already has.
* **Honesty statement, in the docstring and here:** the console has no authentication, so
  binding this means **any local caller of the console decide endpoint approves as the
  declared operator**. That is the truthful description of a single-user local workbench
  with a plaintext allowlist. It adds no weakness the verifier did not already document;
  it merely stops pretending the material comes from somewhere.

**Console wiring is a CARRY (constraint: `console/api.py` untouchable, and `server.py` is
owned by a concurrent sibling).** The one line needed, in `server.py` around :273:

```python
_console_kw = plan_approval_console_kwargs(coordinator=_p7_coord)
if _console_kw:
    _console_kw["plan_approval_actor"] = declared_operator_actor   # the callable
```

`plan_approval_console_kwargs` (`api.py:1496-1505`, sealed) still passes only the
coordinator. Either that function grows an `actor=` parameter or `server.py` adds the key
itself; both are one line, neither is mine to write.

---

## 4. Acceptance evidence

> R22 is closed when a human-approvable card exists for each digest the coordinator will
> actually demand, proven end-to-end … A test that stubs the approval lookup does not
> close this.

`test_r22_one_human_moment_authorizes_both_lanes_of_a_real_replay`, everything real:

| component | what is real |
|---|---|
| approval carrier | a real `PlanApprovalCoordinator`, built through the **production** `build_plan_approval_coordinator` (i.e. via `PlanApprovalCoordinator.replay`), on a real on-disk journal |
| verifier | a real `ConfigOperatorVerifier()` reading the **shipped** `config/orchestration/operators.json` |
| actor | `declared_operator_actor()` — the shipped declaration, not a literal |
| admission | a real Phase-2 `PlanAdmissionService` (real catalog, registry, payload store, budget ledger) |
| lane plans | two real drafts: the reviewed `PRESET_FALLBACK` `main.research_baseline` and the dynamic Planner's `DYNAMIC` candidate, each **prepared and reserved** through that service |
| replay coordinator | a real `ProductionReplayPlanCoordinator` over the frozen Task-2 PIT world |
| driver | a real three-point `run_interval_replay` |

Sequence asserted in one test:

1. register both cards → **before any decision**, the real run raises
   `ReplayCoordinatorApprovalRefused` (the refusal that blocks today);
2. one human moment: two `coord.decide(..., actor=declared_operator_actor())` calls; each
   returns `decision is APPROVED`, `actor_id == "human:ops"` and a real
   `EventType.PLAN_APPROVED` `RunEvent` from the real admission service;
3. the same three-point run now **completes**: `state.completed_points == 3`,
   `runner.lanes == ["bootstrap#1","llm#1","bootstrap#2","llm#2","bootstrap#3","llm#3"]`
   — both lanes, every point;
4. `_require_approval(...)` is then called directly for **both** lanes and returns an
   approved decision;
5. `coord._bootstrap_candidate` / `_main_candidate` are asserted equal to the digests the
   human decided — the gate looks up exactly what was approved.

Supporting proofs in the same suite (35 tests total):

* `test_the_coordinator_default_digests_can_never_be_decided` — the §1b finding, executed:
  a real `PlanAdmissionService.record_approval` on a default lane digest raises
  `AdmissionRejected(code="unknown_candidate")`;
* `test_default_lane_digests_reproduce_the_coordinators_own_defaults` — the helper is
  byte-identical to what the sealed coordinator computes, so the finding is about the real
  digests and not a lookalike;
* `test_forgetting_the_overrides_still_refuses_even_after_a_human_approved` — cards
  registered *and* approved, coordinator built without `coordinator_kwargs()` → still
  refuses. Fail-closed, never a silent run under an unapproved identity;
* `test_without_the_cards_the_same_run_raises_the_refusal_that_blocks_today` — the control;
* `test_a_rejected_card_refuses_its_lane` — REJECTED refuses with "not APPROVED";
* `test_the_decisions_survive_process_death` — a cold coordinator rebuilt from the journal
  alone still authorizes both lanes;
* `test_a_digest_that_does_not_bind_the_draft_is_refused` — forged digest **and** the two
  lanes' digests swapped, both refused;
* `test_the_plan_diff_payload_is_really_committed_and_rebinds` — `payloads.get` resolves
  the ref to an equal `PlanDiff` and `render_plan_diff_md(resolved) == card.rendered_md`;
* `test_a_refused_second_lane_leaves_no_half_registered_card` — registration is atomic
  with respect to the journal: if either lane cannot be carded, NEITHER is registered
  (a lone pending card would let a human decide one lane and believe the run was
  authorized);
* `test_two_lanes_naming_the_same_candidate_are_refused` /
  `test_an_execution_config_bound_to_another_schedule_is_refused` — two wiring traps
  refused with nothing registered;
* `test_replay_cards_never_self_approves` — an AST scan proving the module never calls
  `decide` / `register_and_try_lease` / `record_approval` / `freeze_and_admit_candidate` /
  `admit_after_approval` / `issue_lease`, does call `register_pending`, and (round 2)
  carries none of those names as a non-docstring string literal either;
* round 2 additions: `test_re_registering_after_a_decision_does_not_crash_and_names_the_lane`,
  `test_a_mixed_decided_and_pending_state_registers_only_the_pending_lane`,
  `test_a_drifted_pending_card_refuses_before_any_lane_is_registered`,
  `test_a_request_bound_to_another_schedule_is_refused`,
  `test_a_request_with_no_schedule_ref_is_refused` — see §8b.

---

## 5. TDD RED / GREEN

**RED** — test file written first:

```
$ python -m pytest tests/orchestration/test_replay_approval_cards.py -x -q
tests\orchestration\test_replay_approval_cards.py:48: in <module>
    from guanlan_v2.orchestration.adapters.identity import (
E   ImportError: cannot import name 'declared_operator_actor' from
    'guanlan_v2.orchestration.adapters.identity'
1 error in 0.49s
```

**RED → partial GREEN.** First run after implementing both files: **1 failed, 14 passed**.
The failure was the acceptance test, and it was *my harness being wrong*, not the
implementation: the dynamic-e2e env's `OrchestrationRequest` carries no
`decision_schedule_ref`, which `shadow.py:965` requires of any request producing a shadow
intent. Fixed in the test by swapping in one request that binds the registered schedule
**before** any draft is materialized (so every candidate digest binds that request) — not
by weakening anything.

**GREEN**, after adding the last four guards/tests:

```
$ python -m pytest tests/orchestration/test_replay_approval_cards.py -q
35 passed in 19.58s
```

Sealed-surface regression:

```
$ python -m pytest tests/orchestration/test_contract_completeness.py \
    tests/orchestration/test_phase9_registry_chain.py tests/orchestration/test_approval_store.py \
    tests/orchestration/test_approval_lease.py tests/orchestration/test_dynamic_e2e.py \
    tests/orchestration/test_adapters_api.py tests/orchestration/test_operator_identity.py \
    tests/orchestration/test_phase7_registry.py -q
280 passed in 33.02s
```

Full suite: see §8.

Goldens: `git diff --stat -- tests/orchestration/golden/` is **empty**;
`phase9_retirement_gates_v1.json` file sha256 still starts `5e2660f7…`.

---

## 6. Design choices worth naming

**No seventh route.** The brief's alternative ("a distinct prepare step") is shipped as a
module, not as `POST /orchestration/replay/prepare`. Reasons: (a) `ORCHESTRATION_ROUTE_PATHS`
is a reviewed closed surface with a snapshot test in a sealed Phase-9 test file, and adding
to it means editing `build_adapters_router`'s body; (b) the prepare step's inputs are two
**prepared admission candidates** — objects, not JSON — so an HTTP door could not carry
them without inventing a serialization for `PlanDraft` + a way to name a prepared
candidate; (c) there is no launcher yet to call such a route. When the launcher lands it
calls this function directly, in-process, which is also the house rule about coroutines
never self-calling HTTP.

**No `ContractModel`.** The module defines only frozen dataclasses and functions, so the
Phase-1 completeness disk-walk and the Phase-9 classification firewall stay inert
(`identity.py` precedent, verified again by
`test_replay_cards_defines_no_public_contract_model`). `PHASE9_MODULES` /
`PHASE9_CONTRACT_MODULES` untouched.

**`api.py` not modified at all.** The lane-digest domains are transcribed into
`LANE_CANDIDATE_DOMAINS` and pinned against the sealed source by
`test_the_content_digest_domains_match_the_sealed_coordinator`, which reads
`inspect.getsource(ProductionReplayPlanCoordinator)` and fails loudly if the coordinator's
strings ever move.

**`approval.py` consumed, never modified**, as required.

---

## 7. Files changed

Committed with **explicit pathspecs only** (never `git add -A`; the concurrent session's
15 modified/deleted paths under `guanlan_v2/console/`, `guanlan_v2/datafeed/`,
`guanlan_v2/glmcp/`, `ui/screen/`, `docs/README.md`, `.data/**` plus its untracked files
were never staged, and `guanlan_v2/server.py` / `guanlan_v2/orchestration/startup.py` were
never touched):

| file | status | lines |
|---|---|---|
| `guanlan_v2/orchestration/adapters/replay_cards.py` | **new** | 541 |
| `guanlan_v2/orchestration/adapters/identity.py` | modified | +42 (one function + one `__all__` entry; no existing line changed) |
| `tests/orchestration/test_replay_approval_cards.py` | **new** | 919 (35 tests) |

---

## 8. Suite results + contamination

```
baseline (measured before my files existed)   tests/orchestration -q → 4591 passed
after R22 (round 1)                           tests/orchestration -q → 4622 passed
after review round 2                          tests/orchestration -q → 4627 passed in 317.52s
tests/orchestration/test_replay_approval_cards.py → 35 tests
```

**Contamination, reported and NOT chased.** Round 1: `4591 + 30 = 4621`, but the tree
reported **4622**. The extra **+1 is not mine**: a concurrent sibling session is editing
`guanlan_v2/orchestration/adapters/luozi.py` and
`tests/orchestration/test_watcher_orchestrated_registration.py` right now — that file was
28 tests at R17's commit and collects **36** as I write this, i.e. it grew during my run
window. My baseline itself (4591) is likewise neither R17's 4545 nor R21's 4498, for the
same reason. Round 2 is clean: `4622 + 5 = 4627`, exactly the five review tests, no further
drift. Zero failures anywhere.

I did not touch `guanlan_v2/server.py`, `guanlan_v2/orchestration/startup.py`,
`adapters/luozi.py` or any sibling-owned test, and did not run the non-orchestration tree
(nothing here can reach it — `identity.py` has no consumer outside `tests/orchestration`).

---

## 8b. Review round 2 — two Importants + three Minors closed

Verdict received: **Approved**, deviation called "justified and strictly better than the
steer". Both Importants were the same species — a docstring promising more than the code
delivered — and both are now closed by changing the *code* where that was the honest fix
and the *claim* where it was not.

### I1 — re-registration after a decision (would have bitten R3/R18 on day one)

The docstring said registration was idempotent, full stop. It was idempotent only
*before* a decision: `register_pending` raises `ApprovalDecisionConflict` once the key
carries a terminal decision (`approval.py:467-471`), so the obvious launcher usage —
call this on every replay start, and again on restart — would crash **precisely when a
human had already approved**.

Fixed by making the function genuinely re-callable, which is what a launcher needs:

* a **pre-flight** now reads `coordinator.load_decision(...)` per lane. A decided lane is
  **skipped**, not re-registered, and named in the new
  `ReplayApprovalCards.already_decided` (lane -> `"approved"` / `"rejected"`), with
  `awaiting_human()` for the "does a person still have to act?" question;
* the docstring now states the contract in three explicit cases (pending-identical /
  pending-different / decided) instead of one flat claim, **including its honest limit**:
  a decided card is consumed out of the pending fold, so the non-digest-bound framing
  (`planner_rationale`, baseline choice) of what the human actually read can no longer be
  compared. Everything the digest binds is identical by construction, because the digest
  is re-derived and checked.

Three tests: `test_re_registering_after_a_decision_does_not_crash_and_names_the_lane`
(which also pins the hazard directly — a raw `register_pending` of a decided card still
raises `ApprovalDecisionConflict` — then shows the re-established identities driving a
real 3-point run), `test_a_mixed_decided_and_pending_state_registers_only_the_pending_lane`
(the half-decided state: only the pending lane is registered), and the round-1
idempotence test which still covers the pre-decision case.

### I2 — atomicity claimed more strongly than delivered

The comment said "if either lane cannot be carded, NEITHER is registered". True for
*build* failures, not for *register* failures. Closed from both ends:

* **code** — a pre-flight now compares each lane's built card against any
  already-pending card of the same identity and refuses **before the first append** if
  either has drifted. That removes the reachable register-time half-write
  (`ApprovalDecisionConflict` on the second lane);
* **claim** — the docstring now says plainly that this is *build-time and pre-flight*
  atomicity, **not a transaction**: `register_pending` fsyncs per call and this module
  cannot roll one back (`approval.py` is review-sealed, the journal append-only), so a
  mid-loop I/O failure can still leave lane one pending alone. That state is fail-closed
  (the unregistered lane has no decision, so the run refuses) and a re-call heals it.
  The build-atomicity test's docstring now says which half it covers and which it does
  not.

`test_a_drifted_pending_card_refuses_before_any_lane_is_registered` seeds a
semantically-different `main` card, then asserts the journal is byte-unchanged and no
`bootstrap` card exists.

### Minors

* **M1 taken** — `test_replay_cards_never_self_approves` now also scans every
  **non-docstring** string literal (docstring nodes are identified and excluded via the
  AST) for the banned names, so `getattr(coord, "decide")()` can no longer slip past the
  attribute-call scan.
* **M2 taken** — the drift door now also checks `request.decision_schedule_ref` against
  the supplied schedule on `(id, version, content_digest)`, and refuses an absent ref
  outright (`shadow.wrap_proposal_as_intent` requires it anyway, so refusing here beats
  failing three layers deeper mid-run). Two tests.
* **M4 noted, left as is** — the `PlanDiff` `put` precedes a possible
  `build_pending_plan_approval` raise, which can orphan a payload. It is inert: the
  payload store is content-addressed and idempotent, so an orphan is reachable only by
  its own content digest, references nothing and is referenced by nothing. Reversing the
  order is impossible (the card requires the ref the put returns). The code comment now
  says exactly this rather than just "inert".
* **M3 is not mine** — doc drift in
  `docs/superpowers/plans/2026-07-16-orchestration-phase9-adapters.md:773`, outside my
  three files; the coordinator is handling it.

---

## 9. Carries

1. **Console wiring for the actor material** (§3). One line in `server.py`, or an `actor=`
   parameter on the sealed `plan_approval_console_kwargs`. Until it lands,
   `/console/plan/approvals/decide` still refuses, and the only way to decide a card is
   in-process — which is what the acceptance test does.
2. **Nothing calls this yet.** R22 ships the producer; the launcher (R3/R18) is what
   prepares the two lane candidates, calls `register_replay_approval_cards`, and passes
   `coordinator_kwargs()` into `ProductionReplayPlanCoordinator`. Without that, the
   coordinator falls back to its unapprovable defaults and refuses — loudly, and now
   provably (`test_forgetting_the_overrides_still_refuses_even_after_a_human_approved`).
3. **The bootstrap-lane plan shape — ONE problem, not two.** The coordinator is ruling
   on this separately and the launcher owns the work; I was told not to attempt it. Two
   findings that look independent are the same knot, per the reviewer:
   * Phase-7's `PendingPlanApproval` accepts only `DYNAMIC` / `PRESET_FALLBACK`, while the
     Phase-5 Lane-0 bootstrap draft (`bootstrap.build_bootstrap_plan_draft`) is
     `PlanSource.PRESET`. The sealer refuses it **by name** rather than relabelling a
     preset as a "fallback";
   * the coordinator holds ONE `_bootstrap_candidate` for the whole interval while
     `_verify_bootstrap_return` expects a **per-point** `ContextSnapshot`. A per-point
     draft's per-point candidate digest therefore **structurally cannot** be a constant
     lane digest — and a per-point freeze attempted against the single approved lane
     digest would be refused `unknown_candidate`, i.e. **loud, never silently
     unapproved**.

   Settling it needs either a Phase-7 card-model amendment (sealed) or a decision that the
   replay bootstrap lane runs a different, card-eligible, non-per-point plan. It is the
   single largest unresolved question this work uncovered.
4. **`llm_proposal` has no reservation check**, so the execution-config binding reaches it
   only because the driver always runs `bootstrap_context` for the same point first (§1d).
5. **Two plan reservations per request.** The launcher will call
   `persist_and_reserve_candidate` (one `reserve_plan` per lane) *and* the driver mints its
   own interval `reserve_plan` under `derive_replay_plan_candidate_digest`. Three plan-scope
   reservations against one `RunBudget` — the budgets must be sized for it. Not exercised
   here (my acceptance test uses separate ledgers for admission and for the driver, exactly
   as the existing suites do).
6. **The route response still calls the start digest `candidate_plan_digest`** (§2).
   Misleading name on a sealed reviewed surface; renaming is a separate task.

---

## 10. Concerns

1. **This does not make the framework run.** R22 supplies the missing *card producer*.
   There is still no launcher, no `plan_runner` production binding, and no admission
   provider bound (`set_plan_approval_admission_provider` is still never called outside
   tests, so `bind_process_plan_approval_coordinator` still honestly returns `None`). Do
   not read "R22 closed" as "a replay can be started by a human today".
2. **The acceptance test's two lane drafts are the two card-eligible drafts this repo can
   actually produce** (the research-baseline fallback and the dynamic Planner's candidate),
   not the plans a replay's bootstrap/LLM lanes will really run — because those plans do
   not exist in production (carry 3). The *mechanism* is proven end-to-end with nothing
   synthesized; the *specific plans* are the launcher's.
3. **The approval does not bind the execution config** (§1d). I am satisfied the budget
   reservation covers it, but it is a two-edge argument rather than one digest, and it
   deserves a reviewer's eye.
4. **`declared_operator_actor` makes "whoever can reach the console" the declared
   operator.** Documented in three places, and no worse than the verifier it feeds — but it
   is the point at which R21's "weak by design" stops being theoretical.
