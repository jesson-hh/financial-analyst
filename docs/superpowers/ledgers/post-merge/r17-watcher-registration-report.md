# R17 — the watcher's orchestrated-code REGISTRATION half

**Date** 2026-07-26 · **Branch** `report-evidence-pack` (never switched, never pushed, never merged)
**Parent** `3b1d92e` (the sibling R23/R24 startup-binding commit landed mid-task; my work sits on top of it)

---

## 1. The defect, restated from source

`guanlan_v2/seats/watcher.py:82` defined `_ORCHESTRATED_CODES`. It was read by
`orchestrated_codes()` (`:85-88`) and consulted by `tick` (`:419`) to skip codes an
orchestrated run owns. A repo-wide search found **exactly one non-definition reference —
the read**. Nothing ever wrote it. The Phase-9 driver's seats seam carried only
`load_state` and `note_external_llm_use`, and the skip's only proof
(`tests/orchestration/test_luozi_replay.py:564`) **monkeypatched `orchestrated_codes`
itself**, so the test proved the consumption half against a fake reader and proved nothing
about the seam being alive.

Consequence once the driver is wired to production: on a live tick the same stock is judged
twice — once by the 盯盘 loop, once by orchestration. Not a budget over-draw
(`note_external_llm_use` books into the same daily counts) but duplicated 研判 on
money-adjacent decisions.

## 2. What I built

**`guanlan_v2/seats/watcher.py` (+136 / −0 — purely additive, verified `git diff` has zero
`-` lines):**

| name | what it is |
|---|---|
| `register_orchestrated_codes(codes) -> OrchestratedRegistration` | takes ownership of a set of codes; returns a **single-use handle** |
| `release_orchestrated_codes(registration)` | hands ownership back; `None` is a no-op, a foreign object is a `TypeError`, a second release of the same handle is a no-op |
| `OrchestratedRegistration` | the handle; it is **itself a context manager** (`__exit__` releases), so `with register_orchestrated_codes(...)` is the intended shape |
| `_ORCHESTRATED_REFS: dict[str, int]` | code → owner count; `_ORCHESTRATED_CODES` is always a snapshot of its key set |
| `_ORCHESTRATED_LOCK` | writer lock |
| `_orchestrated_aliases(code)` | syntactic alias expansion (below) |

**`guanlan_v2/orchestration/adapters/luozi.py` (+78 / −0 under `git diff -w`):**
`_OrchestratedUniverseOwnership`, a small context manager, and one `with` around
`run_interval_replay`'s point walk. **No existing line was rewritten** — the only change to
existing code is indentation, which is why `git diff -w` on that file reports
`1 file changed, 78 insertions(+)` and zero deletions.

**`tests/orchestration/test_watcher_orchestrated_registration.py`** — 28 new tests. No
pre-existing test file was touched.

## 3. The five design questions

### 3.1 Where does registration belong?

**The interval driver (`run_interval_replay`), via `bindings.seats_budget_seam` — confirmed,
not assumed.** I checked the alternatives at source:

* the driver is the *only* thing that runs the per-point lanes (`bootstrap_context` →
  `llm_proposal` → `deterministic_targets`), so the contention window with the watcher is
  exactly the driver call's lifetime — no earlier, no later;
* `ReplayRuntimeBindings.seats_budget_seam` is already documented as "the
  `guanlan_v2.seats.watcher` **module surface**" (`luozi.py:2406-2408`), so the registration
  API is reachable through the seam that already exists — no new binding field, no new port;
* putting it in a *caller* above the driver (a future launcher) was rejected: that is exactly
  how the seam died the first time. A primitive nobody is obliged to call is a dead seam
  again;
* putting it inside the watcher's own `tick` is impossible — the watcher cannot know what
  orchestration is about to do.

The codes come from `execution_config.universe` (`tuple[Symbol, ...]`), registered in the
exchange-qualified `engine_code` form.

**Ownership window — REVISED after review. Acquired lazily at the run's FIRST live
point, held to the end of the run.**

My first cut owned the universe for the whole run whenever `is_live_session is not None`,
and justified not narrowing it with a claim that narrowing "would need the predicate
evaluated over all points before the loop, which changes the call ordering of a
caller-supplied callable". **That was a strawman and the reviewer was right to reject
it**: `live_point` is already computed per point inside the loop, so a narrower window
costs zero extra predicate calls and zero reordering. Having removed the false reason, the
real tradeoff is:

* **whole-run** — a caller whose predicate answers `False` for every point still silences
  盯盘 for its entire universe for the entire run. That is not hypothetical: the shape a
  launcher produces on *every backfill* is "a real `is_live_session` predicate + an
  interval whose points are all historical". A multi-year replay invokes the LLM lane per
  point, so this is minutes to hours of the watcher not judging live positions — a
  **missed-研判 harm on real money**, systematic, and entirely avoidable;
* **per-point** — eliminates that, but drops ownership in every inter-point gap, and each
  re-acquisition re-opens the stale-snapshot race of §3.2.1 (a tick that starts in a gap
  carries an empty snapshot through the next point's lanes);
* **lazy-from-first-live-point, held to the end** — what I shipped. It eliminates the
  false skip completely (no live point ⇒ no registration at all) *and* never re-opens the
  gap, so it strictly dominates per-point on the race dimension.

The reason the third option costs nothing is that **points are strictly ascending in time
and live points are the TAIL of a run** — and that is VALIDATOR-enforced, not incidental
(the re-review established the citations, which I had only asserted from
`resolve_decision_points`' body): the `rebalance_dates` selector is checked
sorted-ascending (`shadow.py:530`) and unique (`:532`), and the daily/weekly branch walks
a calendar whose sessions are checked canonically ascending and duplicate-free
(`data/calendar.py:89-90`, with `build_trading_calendar` sorting at `:233`). Nothing here
*depends* on the premise for correctness: a future selector branch that bypassed both
validators could only cause **bounded over-ownership within one run**, never a leak and
never a missed release. "Hold from the first live point to the end" therefore covers essentially only the
live tail. A predicate that answers `True` early and `False` later over-owns for the
remainder — the conservative direction, bounded by the run, and documented on the class.

I also considered and **rejected** extending ownership to a point's
`eligible_execution_at`, which the review raised as arguably still-owned. It is an instant
on the *modelled* timeline; a shadow run executes nothing (`SHADOW_ONLY` /
`ADVISORY_ONLY`, intents merely staged into the ledger), and for a live point it postdates
the driver's return. Holding a registration past the owner's lifetime is exactly the leak
shape this whole design exists to prevent — nobody would be alive to release it.

A coherence gain the re-review pointed out and I had not claimed: ownership is now
governed by the **same `live_point` flag** that already gates pool debiting and
settlement, so the driver has ONE notion of "live" instead of two — and the `seam=None`
warning consequently fires only on runs that are actually live.

Consequences, all pinned by test: `is_live_session=None` registers nothing
(`test_a_historical_replay_registers_nothing`, and Task-4 bit-identity falls out of it); a
predicate false everywhere registers nothing
(`test_a_predicate_false_for_every_point_registers_nothing`); ownership begins at the
first live point (`test_ownership_starts_at_the_first_live_point`); it is not dropped
between points (`test_ownership_is_held_to_the_end_of_the_run_not_dropped_per_point`); and
N live points take exactly ONE registration
(`test_many_live_points_take_exactly_one_registration`, spying the real
`register_orchestrated_codes`).

### 3.1.1 LIMIT — the exclusion binds only ticks that START after registration

`tick` snapshots `orchestrated = orchestrated_codes()` **once**, at `watcher.py:555`, and
then spends the rest of the tick inside `quote_fn` (network) and `decide_fn` (LLM) —
seconds to tens of seconds per code. A tick already in flight when the driver registers
keeps that stale, empty snapshot for its whole duration, so **a driver that registers at
t1 and judges at t2 can still be double-判'd by that in-flight tick**. Mutual exclusion is
therefore *"binds every tick that STARTS after registration"*, not *"the same stock is
never judged twice"* — which is how the class docstring and the R17 row were originally
worded, and both have been corrected.

I did not fix the race. Fixing it means moving the read inside `tick`'s per-code loop,
which modifies `tick` and breaks the "bit-unchanged for non-orchestrated codes"
constraint this task was given. It is recorded as R17's second limit, in the same bold
form as the in-process one, in the class docstring, in the watcher's own comment block and
in the R17 row — because a launcher author will otherwise assume a hard guarantee.

### 3.2 Release on every exit path

**A context manager, not a bare `try/finally` at the call site** — and the handle returned by
`register_orchestrated_codes` is itself one, so *no* caller can hold ownership without a
`with`-shaped release available.

The driver uses `with _OrchestratedUniverseOwnership(...)`, whose `__exit__` runs on the
success path, the exception path and the `return` inside the block (Python guarantees
`__exit__` before the value is handed back). Justification for the CM over `try/finally`:

* the resource has *two* moving parts (resolve the seam + hold the handle); a `try/finally`
  at the call site would have to duplicate the "did we actually register?" check in both
  halves, which is exactly where a leak hides;
* `__exit__` returns `False` unconditionally, so it can never swallow the run's own
  exception;
* a failure *inside* release is caught and logged (`logging.exception`) rather than raised —
  a release failure must never mask the error that caused the unwind. It is logged loudly
  because it means the watcher stays silenced for those codes until the process restarts;
* the branch where the seam took `register_orchestrated_codes` but exposes **no**
  `release_orchestrated_codes` is the genuinely dangerous one — ownership taken with no way
  to hand it back — so it logs at **ERROR** naming the leak
  (`test_a_seam_that_cannot_release_logs_the_leak`). The harmless missing-*register* case
  warns; the harmful missing-*release* case errors;
* `__enter__` refuses a second entry on the same instance
  (`test_the_ownership_context_manager_is_single_use`) and `ensure_owned()` carries the
  **symmetric** guard — refused before entry and after exit
  (`test_ensure_owned_is_refused_outside_the_with_block`). Both are unreachable from the
  driver today, but either would take ownership with nobody left to release it, and the
  cost of a leak is a permanently silenced stock. The asymmetry (guarded `__enter__`,
  unguarded `ensure_owned`) was the kind that survives a refactor.

The three early exits *before* the loop (`ReplayApprovalRefused` on AUTO, the empty-points
`ValueError`, `SnapshotBindingRefused`) are all outside or inside the block correctly: the
first two happen before registration (nothing to release), the third unwinds through
`__exit__`.

Proven: `test_a_live_run_releases_its_universe_on_the_exception_path` (the fake coordinator
raises in the deterministic lane; the test first observes the code IS owned mid-run, then
asserts `orchestrated_codes() == set()` after the raise) and
`test_registration_handle_is_a_context_manager_and_releases_on_exception`.

### 3.3 Crash safety

**Confirmed at source: nothing persists it, so a hard crash self-heals.**

* `_ORCHESTRATED_CODES` / `_ORCHESTRATED_REFS` are module-level Python objects; the only
  disk surface in the module is `STATE_PATH`, and `save_state` writes exactly the dict
  `load_state` produces (`enabled` / `daily_budget` / `counts` / `last_tick`). No writer
  anywhere puts ownership into it — asserted in
  `test_the_registry_is_never_persisted_and_dies_with_the_process` both by inspecting the
  state file's text and by asserting `set(load_state()) <= {enabled, daily_budget, counts,
  last_tick}`.
* the same test spawns a **real subprocess** that imports the watcher and prints
  `orchestrated_codes()` while the parent holds a live registration; it prints `[]`. That is
  the actual claim — a fresh process starts with an empty registry.

The failure direction is **fail-open**: a crash means the watcher resumes judging, never that
it is wedged shut. That is why in-memory is the right storage and durability would be
actively wrong — a durable registration surviving a crash would silence 盯盘 forever with no
owner alive to release it.

**The honest cost, stated because it bounds the whole feature:** the registry is
**process-local**. `run_loop` is started inside the 9999 server process
(`server.py:365-367`) and `tick` runs in a `to_thread` worker of that same process, so the
mutual exclusion exists only if the orchestrated run also executes in that process — which is
how R3/R18 would wire it (router / playbooks in the same app). An out-of-process orchestrated
run is outside this seam's reach and would need a durable registry with a lease/TTL, which is
a different design and not what the exit gate asks for.

### 3.4 Overlap semantics

**Refcount + single-use handles.** Chosen over the alternatives:

* *naive `set.discard`* — rejected, and it is the exact failure the question names: run B
  releases and frees a code run A still owns, silently re-enabling double 研判 for the
  remainder of A's run;
* *refuse overlap* — rejected: two live runs legitimately share a name (the universe is not
  partitioned by run), and refusing would turn a benign overlap into a run failure;
* *per-run ownership keys* — this is what the handle IS, but keyed by the registration rather
  than by a run id, so the watcher never needs to know what a "run" is and the same run can
  re-enter (nested/repeated registration stacks cleanly).

Rules, each with a test:

| rule | test |
|---|---|
| two owners of one code → one release keeps it owned | `test_two_owners_of_the_same_code_are_refcounted` |
| a handle is single-use → a double (or triple) release cannot free another owner | `test_a_double_release_cannot_free_another_owner` |
| duplicates *within one call* (including mutual aliases) collapse to one count, so one release frees exactly what one call took | `test_duplicates_inside_one_registration_collapse` |
| release only ever touches the keys **that handle** incremented | structural (`OrchestratedRegistration.keys`), exercised by the two above |

Concurrency: writers mutate the refcount table under `_ORCHESTRATED_LOCK` and then **rebind**
`_ORCHESTRATED_CODES` to a freshly built `set`. The pre-existing reader `orchestrated_codes()`
is therefore **byte-untouched and still correct**: the object it copies is never mutated after
publication, so `set(...)` can never race a concurrent `add`/`discard` (which would otherwise
be a live `RuntimeError` risk, since `tick` runs in a worker thread).

**Alias expansion** (an overlap question in disguise): `tick` compares the raw string from a
strategy's `bind` list, whose form we do not control (today's `var/archive/strat_*.json` all
hold bare six-digit codes, but nothing enforces that). So an exchange-qualified registration
(`SZ300750` or `300750.SZ`) also owns the bare code and the other qualified form; a **bare**
code owns only itself, because inferring an exchange needs a 号段 table and this module will
not carry a second copy of a rule that can drift from
`orchestration.data.symbols.normalize_symbol`. The driver therefore registers `engine_code`
(`SH600519`), the fully-qualified form, so the expansion always covers all three.

### 3.5 Empty / None / bad input

| input | behaviour | test |
|---|---|---|
| `register_orchestrated_codes([])` | legal; an empty handle; register and release are both no-ops | `test_empty_registration_is_legal_and_a_noop_both_ways` |
| `codes=None` | `TypeError` (caller error) | `test_a_non_iterable_of_str_fails_loudly[None]` |
| `codes="300750"` (a bare `str`/`bytes`) | `TypeError` — **the classic trap**: iterating a str would silently register six one-character "codes" | same test, 3 params |
| a non-`str` element (`int`, `None`, `bool`, `bytes`) | `TypeError`, **and nothing is partially registered** (validation completes before any mutation) | `test_a_non_string_code_fails_loudly` |
| an empty / whitespace-only element | `ValueError`, nothing partially registered | `test_an_empty_code_fails_loudly` |
| duplicate codes in one call | collapse to one count | `test_duplicates_inside_one_registration_collapse` |
| `release_orchestrated_codes(None)` | no-op — a caller's `finally` may legitimately hold nothing | `test_release_of_none_is_a_noop` |
| `release_orchestrated_codes("SZ300750")` | `TypeError` | `test_releasing_a_foreign_object_fails_loudly` |
| a seam without the registration half | run continues, **loud WARNING** naming the unregistered universe | `test_a_seam_without_the_registration_half_does_not_break_the_run` |

The last row is the one judgement call: a missing seam has never been a replay failure
(`_load_seats_state` already tolerates it, and every existing test injects a partial
`SimpleNamespace` seam), but an unregistered *live* run is precisely the duplicated-研判
hazard, so it degrades loudly instead of silently.

## 4. Acceptance evidence

The exit-gate clause is *"watcher skips orchestrated codes and is otherwise bit-unchanged"*.

1. **A real `tick` skips a really-registered code — nothing monkeypatched.** The word
   `orchestrated_codes` never appears as a monkeypatch target anywhere in the new file.
   * `test_real_tick_skips_a_really_registered_code`: register `SZ300750` for real → a real
     `tick` over two watched codes returns `skipped["300750"] == "orchestrated"`,
     `judged == ["600519"]`, and the injected decide kernel was called for `600519` only.
   * `test_a_live_run_owns_its_universe_and_a_real_tick_skips_it` — **the acceptance case**: a
     real `run_interval_replay` over three decision points, with the real `watcher` module as
     the seats seam, runs a **real `tick` from inside the run** (at the moment the LLM lane
     would judge the point). All three observations show
     `skipped["600519"] == "orchestrated"` while `300750` is judged exactly as before; after
     the run `orchestrated_codes() == set()` and a further tick judges `600519` again.
2. **Release on success** — same test's post-run assertions;
   `test_after_release_the_same_tick_judges_the_code_again`.
   **Release on exception** — `test_a_live_run_releases_its_universe_on_the_exception_path`
   (asserts the code *was* owned mid-run first, so the assertion cannot pass vacuously).
3. **Overlap semantics proven** — §3.4's table.
4. **Pre-existing watcher tests untouched and green** — `tests/test_seats_watcher.py` has no
   diff; 11 tests green. The Task-4 pair in `tests/orchestration/test_luozi_replay.py`
   (including the old monkeypatching one, left as-is) is green.
5. **`tick` bit-unchanged for non-orchestrated codes** — no line of `tick` was modified
   (`git diff guanlan_v2/seats/watcher.py` has zero `-` lines);
   `test_tick_with_no_registration_is_bit_unchanged` asserts the whole return value equals
   `{"judged": [...], "skipped": {}}` with an empty registry.

### TDD RED → GREEN

* **RED** — the 28 tests were written and run before any source change:
  `26 failed, 2 passed, 28 errors in 4.85s` (the errors are the autouse isolation fixture
  touching `watcher._ORCHESTRATED_REFS`, which did not exist yet — i.e. the registry itself
  was absent).
* **GREEN** — after the watcher + driver changes: `28 passed in 4.80s`.
  (One intermediate failure was a bug in my own assertion helper — `r.message % r.args` on an
  already-formatted record — fixed to `r.getMessage()`; no source change.)

**Second RED/GREEN — the review pass (Fix 2 + minors).** The seven new/changed tests were
written first, then both source files were **temporarily reverted to the committed
pre-fix state** (`git checkout HEAD --` on exactly those two paths, working copies saved
aside first):

* **RED** — `5 failed, 30 passed in 5.26s`, and the five are exactly the review's targets:
  `test_a_predicate_false_for_every_point_registers_nothing` and
  `test_ownership_starts_at_the_first_live_point` (Fix 2's false skip),
  `test_a_missing_seam_on_a_live_run_warns_instead_of_going_silent` (minor 5),
  `test_a_seam_that_cannot_release_logs_the_leak` (minor 4) and
  `test_the_ownership_context_manager_is_single_use` (minor 6).
  `test_ownership_is_held_to_the_end_of_the_run_not_dropped_per_point` and
  `test_many_live_points_take_exactly_one_registration` passed under the OLD code too —
  correctly, and stated plainly: they pin behaviour Fix 2 *preserved*, and they are the
  guard that the new lazy window did not silently become per-point.
* sources restored from the saved copies and verified byte-for-byte with `sha256sum -c`.
* **GREEN** — `35 passed in 4.64s`.

### Suite

| scope | result |
|---|---|
| `tests/orchestration/test_watcher_orchestrated_registration.py` | **36 passed** (28 + 7 review pass + 1 re-review) |
| `tests/test_seats_watcher.py` + `test_luozi_replay` + `test_adapters_api` + `test_shadow_redlines` + `test_redline_regression` + `test_phase9_e2e` | **161 passed** |
| `tests/orchestration` (first pass) | **4545 passed** (4517 before this task + 28) |
| `tests/orchestration` (after the review pass, foreground) | **4590 passed** — my file is 35; the other +38 since the first pass are the two sibling agents' new tests |
| `tests` (non-orchestration) | **1496 passed** |
| `tests` (whole repo, split runs) | **6041 passed** |
| `tests` (whole repo, single run **after** the commit) | **6042 passed, 1 failed** — see the contamination note |

**Contamination note (observed, not chased).** The sibling R23/R24 agent's work landed as
commits (`ddccdd6`, `5998844`, `3b1d92e`) while I worked, so HEAD moved under me from
`f501210` to `3b1d92e`. The prior recorded orchestration count of 4498 (R21 minor fixes)
+ 19 from R23/R24 = 4517, exactly my pre-task baseline — the numbers reconcile with no
unexplained drift.

The confirmatory post-commit full run reported
`tests/orchestration/test_startup_binding.py::test_server_binds_through_the_production_helper_only`
FAILED. That is the sibling's file, caught mid-write: at that moment `git status` showed it
concurrently editing `guanlan_v2/server.py`, `guanlan_v2/orchestration/startup.py` and
`tests/orchestration/test_startup_binding.py`. **Re-running that file alone immediately
afterwards: 35 passed.** It is a transient snapshot of another agent's in-flight edit, it
touches none of my three files, and I did not chase it. (The total also moved 6041 → 6043
between runs, which is the same agent adding tests.)

The other uncommitted dirt in the tree (`console/`, `datafeed/`, `glmcp/`, `ui/screen/`,
`docs/README.md`, `.data/`) belongs to a different concurrent session and was never staged —
the commit's pathspec was three explicit files, verified with `git diff --cached --name-only`
before committing and `git show --stat` after.

### Seals and goldens

* `phase9_retirement_gates_v1.json` sha256 still `5e2660f7…` (its `gates_digest` field
  `68568b49…` untouched). No golden moved.
* `luozi.__all__` unchanged → the sealed closed-surface guard
  (`test_luozi_all_is_the_reviewed_closed_surface`) needed no flip;
  `_OrchestratedUniverseOwnership` is private.
* The sealed lexical guard (`test_phase9_machinery_names_absent_from_phase6_modules`) is
  green: no new identifier in `luozi.py` contains `evaluator` / `resume` / `wakeup` (or any
  other `_PHASE9_NAME_TOKENS` entry), and the only new import is a function-local
  `import logging`.
* `engine/`, `ui/`, `workflow/executor` untouched. Port 9999 never touched.
  `var/secrets.env` never read.

## 5. Files changed

| file | change |
|---|---|
| `G:\guanlan-v2\guanlan_v2\seats\watcher.py` | **+136 / −0**. `import threading`; the registration block (`_ORCHESTRATED_LOCK`, `_ORCHESTRATED_REFS`, `_ORCH_CODE_RE`, `_orchestrated_aliases`, `_publish_orchestrated`, `OrchestratedRegistration`, `register_orchestrated_codes`, `release_orchestrated_codes`) inserted after `note_external_llm_use`. No existing line modified. |
| `G:\guanlan-v2\guanlan_v2\orchestration\adapters\luozi.py` | `_OrchestratedUniverseOwnership` (now with `ensure_owned()`), one `with` wrapping `run_interval_replay`'s point walk, and one 4-line `if live_point:` block inside the loop. Under `git diff -w` the pre-existing lines show **only inserts** — no pre-existing line is altered, they are re-indented. |
| `G:\guanlan-v2\tests\orchestration\test_watcher_orchestrated_registration.py` | new, **35 tests**. |
| `G:\guanlan-v2\.superpowers\sdd\p9-task-12-report.md` | gate + roll-ups + R17 row (below). |
| `G:\guanlan-v2\.superpowers\sdd\progress-orchestration.md` | one appended R17 entry. |
| `G:\guanlan-v2\.superpowers\sdd\r17-watcher-registration-report.md` | this file. |

## 6. What I changed in the Phase-9 records, and why

I marked the gate **green**, because the clause it fails on is now true: the watcher skips
orchestrated codes, driven by a real registration from the driver, proven by a real `tick`
with no monkeypatching, and it is otherwise bit-unchanged.

* **§5 "Schedule replay and no-retroactive-intent", 4th bullet** — ⚠ PARTIAL → ✅ green, with
  the mechanism named and a pointer to the R17 row's two limits.
* **§5 summary**, **§7 Honesty bullet**, **§8 Concern 1** — "FOUR gates short of green" →
  "THREE", each annotated with the date and which gate closed, so a reader of the roll-up
  alone cannot undercount *or* overcount.
* **§6 residual table, row R17** — the original text struck through (kept verbatim: it is the
  record of what was wrong) and followed by `CLOSED 2026-07-26` with the semantics, plus the
  two limits that are explicitly *not* this gate: the registry is in-process, and nothing in
  production calls the driver yet (**R3/R18**, unchanged).
* **§6 cross-check paragraph** — FOUR → THREE with R17 named as closed.
* **§6 rows R3 and R18** — each gained one sentence pointing back to R17's three limits.
  Those are the rows the launcher author actually reads; if they run the driver
  out-of-process the skip silently stops applying and **no test goes red**, so the
  constraint had to be stated where it binds rather than buried in R17.
* **A dated "Post-phase amendment" section** appended, stating exactly what moved and what did
  not (no other gate, no golden, no `__all__`), and explicitly preserving the historical
  "N1 — the roll-up sentences now match" narrative, which describes an *earlier* pass and
  would be falsified by editing.
* **`progress-orchestration.md`** — one appended entry in the file's existing style, carrying
  the overlap decision, the limits and the suite numbers, and updating the remaining order to
  `R22 → R3/R18 launcher → 9998 full verification`.

What I did **not** touch: `docs/superpowers/plans/2026-07-16-orchestration-phase9-adapters.md`
and `.superpowers/sdd/task-12-brief.md` carry the same clause as an unticked `- [ ]` checklist
item, but every gate in those checklists is unticked — they are the plan as written, not a
live status board, so ticking one row would misrepresent them.

## 7. Concerns

1. **In-process only.** The strongest honest statement is: *within the 9999 server process*, a
   live orchestrated run and the 盯盘 loop can no longer judge the same stock in the same
   window. An orchestrated run in another process is not covered. Anyone who later moves
   orchestration out of the server process must revisit this (durable registry + lease/TTL, so
   a dead owner cannot silence 盯盘 forever). **Cross-referenced from R3 and R18** so the
   launcher author meets it where they are reading, not buried in R17.
2. **Nothing registers in production today**, because nothing in production calls
   `run_interval_replay` (R3/R18 — no launcher). The seam is alive and proven; it is not yet
   *exercised* by a production path. Whoever writes the launcher gets this for free provided
   they call the driver with a real `is_live_session` predicate and the real `watcher` module
   as `seats_budget_seam` — and provided the driver runs in the server process (see 1).
3. **The exclusion is not absolute — it binds only ticks that START after registration**
   (§3.1.1). Unfixable under the bit-unchanged constraint; recorded in three places. Anyone
   who later gets permission to touch `tick` should move the `orchestrated_codes()` read
   inside its per-code loop, which closes it completely for one extra set lookup per code.
4. **A predicate that answers `True` early and `False` later over-owns** for the rest of the
   run. Bounded by the run and in the conservative direction, but it is the one shape where
   the lazy window is looser than per-point. Documented on
   `_OrchestratedUniverseOwnership`.
5. **The old monkeypatching test still exists**
   (`test_luozi_replay.py::test_watcher_skips_orchestrated_codes`). I left it untouched per the
   "pre-existing tests untouched" constraint. It is no longer the *only* proof, but a future
   reader could mistake it for one; the new file's module docstring says so explicitly.
6. **Alias expansion is syntactic and deliberately incomplete for bare codes.** If a strategy
   ever binds `SZ300750` while a caller registers the bare `300750`, the skip will not fire.
   The driver always registers the qualified form, so this only bites a hand-written caller.
   An assertion-level fix would mean duplicating the 号段 table into the seats module, which
   would drift; if this ever matters, the right move is to normalize `bind` values at write
   time in `ww_seats_bind`, not to guess at read time.
7. **Indentation-only diff on a sealed body.** `run_interval_replay`'s 140-line point walk
   moved right by four spaces. `git diff -w` proves no logic changed (+78/−0), but a reviewer
   reading the raw diff will see a large hunk; use `-w`.
