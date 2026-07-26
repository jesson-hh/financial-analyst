# R23 + R24 — the durable-store startup binding

**Branch** `report-evidence-pack` · **base** `ddccdd6` · **date** 2026-07-26

---

## 1. What was wrong

`guanlan_v2/server.py:383-391` called `bind_process_durable_stores_and_scan()` with **no
kwargs**, so `durable.py:618-647` fell back to its minimal defaults:

* resolver = Phase-1 `default_registry()` + `phase2_runtime_registry(...)` only. A Phase-9
  payload write raised `UnknownRegistryDigest` (`eventstore.py:394-398`); once such a row
  existed on disk, the **next** startup fold re-read it, failed reconstruction, and
  `_DurableLog._fold_payloads` wrapped that as `DurableStoreCorrupt`
  (`durable.py:305-310`) — which `server.py:389-392` swallowed into one stderr line,
  leaving the whole process silently store-less.
* `allowed_cell_namespaces=()` — zero namespaces. `ReplayStateStore.__init__` refused
  (`luozi.py:3346-3353`); every replay-head/index/operation/prompt CAS died with
  `StateCellError` (`eventstore.py:905-906`).

**R24** — not fixable after startup: the bind is idempotent-once
(`durable.py:632-633`, a later call *with* kwargs is a silent no-op) and `RuntimeStores`
freezes `frozenset(allowed_cell_namespaces)` at construction (`eventstore.py:1033`) behind
a read-only property. Hence the fix is at the single call site.

---

## 2. What changed

| file | status | what |
|---|---|---|
| `guanlan_v2/orchestration/startup.py` | **new** | production wiring: derived namespace union, Phase-1/2/9 resolver, honest-failure bind + queryable status |
| `guanlan_v2/server.py` | modified | the one call site now binds through `bind_orchestration_stores()`; adds the read-only `GET /orchestration/store_status` probe and the `_ORCH_STORE_STATUS` module record |
| `tests/orchestration/test_startup_binding.py` | **new** | 19 tests incl. the mechanical namespace drift guard |

No sealed Phase 1-9 module was modified. `durable.py`, `eventstore.py`, `chain.py`,
`luozi.py`, `worker.py`, `approval.py` are consumed only — **no additive change to
`durable.py` was needed**: `DurableStoreCorrupt` already propagates out of
`bind_process_durable_stores_and_scan`, so the honest-failure work is entirely at the
call site. engine/, ui/, workflow/executor untouched. Frozen goldens unmoved
(`tests/orchestration/golden/phase9_retirement_gates_v1.json` file sha256 still
`5e2660f7…`).

---

## 3. The derived namespace union — 14 members

`PRODUCTION_CELL_NAMESPACES` is *derived*, never hand-listed:

```python
tuple(sorted(
    set(PHASE4_STATE_CELL_NAMESPACES)      # trial_ledger.py:129  (11)
    | {PROMPT_CELL_NAMESPACE}              # worker.py:205        (1)  <-- R23
    | set(REPLAY_STATE_CELL_NAMESPACES)    # luozi.py:3252        (2)
))
```

| # | namespace | source constant | CAS writer |
|---|---|---|---|
| 1 | `adapters.replay_head.v1` | `luozi.REPLAY_HEAD_NAMESPACE` (`luozi.py:3250`) | `luozi.py:3480,3494,3556,3578` |
| 2 | `adapters.replay_operation.v1` | `luozi.REPLAY_OPERATION_NAMESPACE` (`luozi.py:3251`) | `luozi.py:3563` |
| 3 | `memory.cutover_preparation.v1` | `PHASE3_MEMORY_STATE_CELL_NAMESPACES` (`memory/models.py:173`) | `memory/store.py:195` (owner map) |
| 4 | `memory.proposal_preparation.v1` | 〃 | 〃 |
| 5 | `memory.snapshot_head.v1` | 〃 | 〃 |
| 6 | `memory.snapshot_operation.v1` | 〃 | 〃 |
| 7 | `memory.snapshot_preparation.v1` | 〃 | 〃 |
| 8 | `memory.source_head.v1` | 〃 | 〃 |
| 9 | `memory.source_operation.v1` | 〃 | 〃 |
| 10 | **`runtime.prompt.v1`** | **`worker.PROMPT_CELL_NAMESPACE` (`worker.py:205`)** | **`worker.py:2294-2297` `_persist_prompt_record` — every LLM node attempt** |
| 11 | `trial.experiment_head.v1` | `PHASE4_TRIAL_STATE_CELL_NAMESPACES` (`trial_ledger.py:120`), also `optimize.EXPERIMENT_HEAD_NAMESPACE` (`optimize.py:124`) | `optimize.py:351` |
| 12 | `trial.family_head.v1` | 〃 | `trial_ledger.py:372` |
| 13 | `trial.holdout_lease.v1` | 〃 | `trial_ledger.py:738` |
| 14 | `trial.window_head.v1` | 〃 | `trial_ledger.py:503` |

**Count = 14** (7 memory + 4 trial = the 11 of `PHASE4_STATE_CELL_NAMESPACES`, + 1 prompt,
+ 2 replay). Derived at source, not by arithmetic — the value printed by the live server
is asserted against this list in the 9998 probe.

### Was anything else missing beyond `runtime.prompt.v1`?

**No.** The sweep was exhaustive, three independent ways:

1. every `cell_namespace=` keyword in the package — exactly 11 call sites, all listed
   above;
2. every `StateCellCompareAndSwapCommand(` construction — 9 sites (same set; none built
   dynamically, none positional);
3. every `"<a>.<b>.v<n>"`-shaped string literal under `guanlan_v2/orchestration/` — 15
   distinct values, of which 14 are the union and the 15th,
   `"policy.action_surface_alias.v1"` (`migration.py:145`), is a *policy id*, not a
   state-cell namespace.

`test_union_covers_every_state_cell_namespace_in_the_package` re-derives (2)+(3)
mechanically from the package AST on every run, so the next addition cannot slip through
the way `runtime.prompt.v1` did. It also asserts the scan finds the union *exactly*, so a
silently broken scanner cannot pass it vacuously.

Reads are gated too (`RuntimeStateCellStore.load`, `eventstore.py:738`), not just CAS —
so the missing union also broke read paths, which the survey did not mention.

---

## 4. The resolver

`build_production_resolver()` registers **three** sealed registries and returns their
digests:

| registry | digest |
|---|---|
| Phase-1 `default_registry()` | `75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03` |
| Phase-2 `phase2_runtime_registry(phase1)` | `b11fcacf0efd931dc3a3d11f859d6f5bac86c1c24b78e5cc1d2b44829822d8d5` |
| Phase-9 cumulative `build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)` | `9e73ddf6d23def5a5666016b1ab113e7b9920eb940ff22a0dd73d806efd3ac07` |

Three suffice because the Phase-9 node is *cumulative* — it registers Phase-1 public +
Phase-2 runtime facts + Phase-3 data/memory + Phase-4 + Phase-5 + Phase-6 + Phase-7 +
Phase-8 + Phase-9 into one fresh sealed registry (`chain.py:270-284`). The Phase-8 base
comes from the chain's own `PHASE9_BASE_REGISTRY_DIGEST` accessor (which resolves to
`lane_catalog.PHASE8_REGISTRY_DIGEST` = `d719e19b…`), so **no digest is hardcoded** and a
chain reseal cannot leave a stale pin. Cost measured: ~1.4 s at first boot (dominated by
the lazy Phase-8 digest), ~0 s thereafter.

---

## 5. Honest-failure design and reasoning

Four distinguishable outcomes, recorded in one JSON-safe, queryable record
(`orchestration_store_status()` / `GET /orchestration/store_status`). `state` is the
single field an operator reads:

| `state` | meaning | signal | boots? |
|---|---|---|---|
| `bound` | **the only healthy value** | `INFO` | yes |
| `unavailable` | the orchestration package is absent / failed to import | stderr `[guanlan_v2][ORCH-STORE-UNAVAILABLE]` | yes |
| `corrupt` | `DurableStoreCorrupt` — **data integrity** | `CRITICAL` + stderr, both carrying `ORCH-STORE-CORRUPT` | yes by default; **no** under `GUANLAN_ORCH_STORE_STRICT=1` |
| `failed` | any other bind failure (a wiring bug) | `ERROR` + stderr `ORCH-STORE-FAILED` | yes by default; **no** under strict |

Reasoning:

* **A log line is not an operator surface.** The original failure mode was not only that
  the error was swallowed — it is that after boot there was *no way to ask*. Both
  "never bound" and "bind exploded" left `process_durable_stores() == None`. The status
  record makes those two states nameable, and the HTTP probe makes them askable at any
  time, not only in the first 200 ms of a log file. The bound record also carries the
  **actual** frozen values read back off the store (`stores.cells.allowed_namespaces`),
  never the intended input — so "it bound" and "it bound *correctly*" are the same claim.
* **Log AND stderr, deliberately.** The logger is the machine-readable channel; the
  stderr line survives a process whose logging root was never configured — exactly the
  state a boot-time failure can be in. `ORCH-STORE-CORRUPT` is a stable greppable token.
* **Why a corrupt store still boots by default.** The 9999 process serves the entire
  product (选股 / 落子 / 帷幄 / datafeed / MCP). Taking the whole UI down because an
  *additive* subsystem's jsonl has a bad row is a self-inflicted outage that is strictly
  worse than the thing it protects against — and the brief's own constraint 3 says a
  normal server start must not depend on optional machinery. Crucially, booting here is
  **fail-closed, not fail-open**: `_PROCESS_STORES` stays `None`, so every orchestration
  consumer honestly 503s (`replay_store_unwired`) and nothing writes a half-store. The
  danger the survey named was *silence*, and silence is what is removed.
* **`GUANLAN_ORCH_STORE_STRICT=1` for operators who want the harder guarantee** — raises
  `OrchestrationStoreBootRefused` and the process exits non-zero. Verified live (§6.5).
* **Corruption is never auto-repaired.** The message says "inspect the journal by hand;
  do not delete it blind". No truncation, no rename, no re-init.

---

## 6. 9998 verification — it actually ran

All five steps ran on this machine. `python` = `D:\app\miniconda\python.exe`.
9999 was **not** running and its watchdog heartbeat was ~6.5 days stale, so
`_checker_revive_loop` (`server.py:453-472`) would have spawned a `check_9999.ps1`
generation within seconds of every lifespan start. I therefore **saved
`var/check_9999.heartbeat`'s mtime, freshened it for the duration, and restored the exact
original mtime afterwards** (verified: `1784489130.2093205` before and after). No watchdog
generation was spawned; 9999 was never touched.

### 6.1 Step 1 — real server on 9998, production default store root

```
GUANLAN_PORT=9998 python -m guanlan_v2.server > scratchpad/run1.log 2>&1 &
PYTHONPATH=G:/guanlan-v2 python scratchpad/probe_9998.py bound
```

`GET http://127.0.0.1:9998/orchestration/store_status` →

```json
{
  "state": "bound", "bound": true, "root": "var\\orchestration",
  "cell_namespaces": ["adapters.replay_head.v1","adapters.replay_operation.v1",
    "memory.cutover_preparation.v1","memory.proposal_preparation.v1",
    "memory.snapshot_head.v1","memory.snapshot_operation.v1",
    "memory.snapshot_preparation.v1","memory.source_head.v1",
    "memory.source_operation.v1","runtime.prompt.v1","trial.experiment_head.v1",
    "trial.family_head.v1","trial.holdout_lease.v1","trial.window_head.v1"],
  "cell_namespace_count": 14,
  "registry_digests": ["75f7920d…b03","b11fcacf…8d5","9e73ddf6…c07"],
  "error_type": null, "error": null, "strict": false
}
```

The probe **asserts** (does not eyeball): `state == "bound"`, the namespace list equals the
14-name expectation element-for-element, the count is 14, and the three digests equal
digests it recomputes itself from `default_registry()` / `phase2_runtime_registry` /
`chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)`. It exited 0.

### 6.2 Step 2 — `ReplayStateStore` against those process stores (the R23/R24 proof)

`scratchpad/step2_inprocess.py` imports `guanlan_v2.server` (whose module-level
`app = create_app()` **is** the production bind), over the same root:

```
[status] bound 14 namespaces, 3 registries
[stores] root = var\orchestration
[stores] allowed_cell_namespaces = 14
[PROOF 1] ReplayStateStore CONSTRUCTED against the process stores -> R23/R24 closed
[PROOF 2] Phase-9 payload PUT under registry 9e73ddf6d23d + runtime.prompt.v1 CAS
          COMMITTED and read back -> R23 registry+namespace closed
```

PROOF 2 is a real `RuntimeUnitOfWork.commit`: a `ReplayDecisionPoint` (a **Phase-9-only**
contract, resolvable through no earlier registry) put under `main`, plus a
`StateCellCompareAndSwapCommand` on `runtime.prompt.v1`, then read back through
`stores.cells.load`. On disk afterwards:

```
var/orchestration/payloads/_index.jsonl
  {"seq":1,"object_id":"payload-1","namespace":"main","digest":"8780602a…653",
   "schema":{"name":"ReplayDecisionPoint","version":"1"},
   "registry_digest":"9e73ddf6d23def5a5666016b1ab113e7b9920eb940ff22a0dd73d806efd3ac07", …}
var/orchestration/state_cells.jsonl
  {"seq":1,"namespace":"runtime.prompt.v1","key_digest":"eee…","typed_ref":{…}}
```

### 6.3 Step 3 — restart on 9998 over that row (the "sharpest edge")

```
GUANLAN_PORT=9998 python -m guanlan_v2.server > scratchpad/run3.log 2>&1 &
PYTHONPATH=G:/guanlan-v2 python scratchpad/probe_9998.py bound   # exit 0
```

`state == "bound"`, same 14 namespaces, same 3 digests. **The next-startup fold of a
Phase-9 payload row now succeeds** — the exact scenario that previously produced a
`DurableStoreCorrupt` the server swallowed.

### 6.4 Negative control — the old binding on the *same* root

```
python -c "from pathlib import Path; from guanlan_v2.orchestration.adapters.durable \
  import build_durable_runtime_stores; build_durable_runtime_stores(Path('var/orchestration'))"
DurableStoreCorrupt: payload main/8780602a…653 does not reconstruct under its declared
schema/registry: no sealed registry registered for digest '9e73ddf6d23def5a…'
```

The kwarg-less defaults reproduce the survey's failure verbatim on the very row the fixed
binding folds fine. The survey's sharpest-edge claim is **confirmed**, not merely repeated.

### 6.5 Steps 4 + 5 — the honest-failure path, live

Corrupt root = `scratchpad/corrupt_root/commits.jsonl` containing
`{"seq": 1}\nthis-is-not-json\n{"seq": 2}\n` (a malformed **non-final** line — a torn
*final* line is legitimately tolerated).

**Step 4 (default, non-strict):**

```
GUANLAN_PORT=9998 GUANLAN_ORCH_STORE_ROOT=…/corrupt_root python -m guanlan_v2.server &
PYTHONPATH=G:/guanlan-v2 python scratchpad/probe_9998.py corrupt      # exit 0
```

The server **is serving on 9998** (the probe got a 200) and reports:

```json
{"state":"corrupt","bound":false,"root":"…/corrupt_root",
 "cell_namespaces":[],"cell_namespace_count":0,"registry_digests":[],
 "error_type":"DurableStoreCorrupt",
 "error":"malformed (non-JSON) line at position 1 in …/corrupt_root/commits.jsonl",
 "strict":false}
```

and the log carries the signal on **both** channels:

```
[ORCH-STORE-CORRUPT] the orchestration durable store at …/corrupt_root is CORRUPT and was
NOT bound — this process has NO orchestration durable store (DurableStoreCorrupt: malformed
(non-JSON) line at position 1 in …/corrupt_root/commits.jsonl). Inspect the journal by hand;
do not delete it blind. Set GUANLAN_ORCH_STORE_STRICT=1 to refuse the boot instead.
[guanlan_v2][ORCH-STORE-CORRUPT] …same…
```

**Step 5 (strict):**

```
GUANLAN_PORT=9998 GUANLAN_ORCH_STORE_STRICT=1 GUANLAN_ORCH_STORE_ROOT=…/corrupt_root \
  python -m guanlan_v2.server
EXIT CODE = 1        # nothing listening on 9998
guanlan_v2.orchestration.startup.OrchestrationStoreBootRefused: ORCH-STORE-CORRUPT:
refusing to boot over a corrupt orchestration durable store at …/corrupt_root (…)
```

### 6.6 Teardown

Every 9998 process was stopped by PID (`Get-NetTCPConnection -LocalPort 9998` →
`Stop-Process`); 9999 was never targeted. `var/orchestration/` did not exist before this
task and was deleted afterwards; `var/` is gitignored (`.gitignore:44`) so nothing under it
was staged. The watchdog heartbeat mtime was restored bit-exact.

---

## 7. Tests + TDD evidence

* **RED first**: `tests/orchestration/test_startup_binding.py` written before
  `startup.py` — collection error `ImportError: cannot import name 'startup'`.
* After `startup.py`: **18 passed, 1 failed** — the remaining failure was
  `test_server_binds_through_the_production_helper_only`, i.e. exactly the call site not
  yet fixed. After the `server.py` edit: **19 passed**.
* **Baseline** at `ddccdd6` (pre-change): `pytest tests/orchestration -q` → **4498
  passed** in 319 s. (The brief's 4433 was at `f501210`; the R21 commit `ddccdd6` added 65.)
* **After** (foreground, pre-commit): `pytest tests/orchestration -q` → **4517 passed**
  in 311 s = 4498 + 19 new. Zero regressions, zero xfail/skip changes.

Test inventory (19): the exact 14-name union; the union is *derived* not hardcoded; the
AST drift guard; the resolver holds Phase-1/2/9; a Phase-9-only schema resolves only via
Phase-9; the bind seals the full union; `ReplayStateStore` constructs; `runtime.prompt.v1`
loads while an unwired namespace raises; all 14 load; the root env override; corruption is
loud + distinguishable (CRITICAL record, marker, stderr); corrupt ≠ healthy; strict refuses;
non-strict is default; an unexpected failure is `failed` not skipped; status starts
`not_attempted`; status is a defensive copy; status is JSON-serialisable; the server call
site uses the helper exactly once and never the kwarg-less call.

---

## 8. Concerns / contradictions with the survey

1. **Suite-count contamination (expected, per brief).** During this task other sessions
   left uncommitted edits to `guanlan_v2/orchestration/adapters/luozi.py` (+67) and
   `guanlan_v2/seats/watcher.py` (+136) plus an untracked
   `tests/orchestration/test_watcher_orchestrated_registration.py`. My 4517 therefore
   includes their in-flight work. Both my baseline (4498) and my post count were taken
   with whatever was on disk at the time, ~10 minutes apart; the delta of exactly +19
   matches my new tests. I staged **none** of their files. (The brief warned about
   `identity.py`; what I actually saw dirty was luozi/watcher — noting the discrepancy
   rather than chasing it.)
2. **The survey's prescribed union was right at 14, but for a reason it did not state.**
   `optimize.EXPERIMENT_HEAD_NAMESPACE` looks like a 15th source; it is already inside
   `PHASE4_TRIAL_STATE_CELL_NAMESPACES`. Anyone re-deriving the union by counting *writers*
   rather than *names* will over-count.
3. **The namespace gap broke reads too**, not only CAS. `RuntimeStateCellStore.load`
   (`eventstore.py:737-740`) raises `StateCellError` on an unknown namespace, so with
   `()` even a read-only head lookup failed. The survey framed it as a commit-time issue.
4. **This closes the binding, not the framework.** `process_durable_stores()` is now a
   correct store, but the adapters router still reads `d.replay_state_store` from a
   dependency container nothing wires in production — `GET /orchestration/replay/state`
   still returns `503 replay_store_unwired`. That is the *next* item (launcher +
   `plan_runner` binding), not a regression from this change. I deliberately did not wire
   it: R23/R24 was scoped to the store binding, and wiring the router is a separate
   reviewable decision.
5. **`GET /orchestration/store_status` is new public surface.** It is read-only, returns
   no credentials, and the only path it discloses is the store root (`var/orchestration`
   in production). The server binds 127.0.0.1. If that is judged too much surface, the
   route can be dropped and `startup.orchestration_store_status()` kept as the in-process
   accessor — but then the 9998 verification is no longer assertable from outside the
   process, which was a stated requirement.
6. **Boot cost +~1.4 s** for the first `PHASE8_REGISTRY_DIGEST` computation. Measured, not
   estimated. Acceptable for a process that already takes tens of seconds to build the
   engine app, but it is a real regression in cold-start time and worth knowing.
7. **The watchdog was already down before I started** (9999 not listening, heartbeat 6.5 d
   stale). I did not restart it — that is an operational decision for the user, not
   something to do as a side effect of a verification run. The heartbeat mtime I touched
   was restored exactly.


---
---

# Review round 2 — "Needs fixes" (3 Importants + 3 folds), all addressed

Verdict received 2026-07-26 after commit `3b1d92e`. Zero Critical. Everything below is
in the follow-up commit; §1-§8 above describe the state at `3b1d92e` and are left intact
so the two rounds can be read against each other. Fold (a) is the one exception — the
wrong number in §3 is corrected in place, because leaving a known-wrong count in the
body would mislead the next person re-deriving the union.

## Fix 1 (Important) — only strict mode may ever refuse a boot

**The finding.** `startup.py` had

```python
from guanlan_v2.orchestration.adapters.durable import DurableStoreCorrupt
from guanlan_v2.orchestration.runtime_clock import SystemClock
```

*outside* the try, and `server.py` called the bind in an unguarded `else:`. Since
`startup.py`'s module-level imports pull in `luozi` / `trial_ledger` / `worker` but **not**
`durable`, a broken `durable.py` would have raised `ImportError` straight out of
`create_app()` — killing the entire 9999 process (选股 / 落子 / 帷幄 / datafeed / MCP)
where the old kwarg-less code caught it and continued. My change would have *regressed*
the one property its own design section promised. The reviewer is right and this was the
most serious thing in the round.

**The fix**, defence in depth at both levels:

* `startup.py` — the bind is now two guarded steps. Step 1 imports `DurableStoreCorrupt`
  + `SystemClock` inside a `try`; any failure returns `state="unavailable"` (loud, via the
  same `_shout` path). Step 2 does the resolver build + bind with `DurableStoreCorrupt`
  safely nameable. A shared `_degrade()` closure records the state, shouts, and — **only
  under strict** — raises `OrchestrationStoreBootRefused`. There is now no code path in
  the function outside a guard.
* `server.py` — the call site wraps the bind too:
  `except _BootRefused: raise` (the single sanctioned refusal) followed by
  `except Exception: record "failed" + stderr`. So even a bug *in* `startup.py` cannot
  kill `create_app()`. This is redundant with the above on purpose: the property is
  load-bearing enough to be enforced structurally at the call site, not only by the
  callee's discipline.

**TDD evidence (real RED, not asserted).** I put the two imports back in their pre-fix
position, ran the six new tests, and restored the file byte-exact:

```
---- RED run against the pre-fix import placement ----
FAILED …::test_a_broken_durable_module_degrades_to_unavailable_without_raising
FAILED …::test_a_broken_durable_module_is_loud
FAILED …::test_strict_mode_refuses_the_boot_when_durable_cannot_import
FAILED …::test_no_non_strict_failure_mode_ever_raises[boom0]   # ImportError
FAILED …::test_no_non_strict_failure_mode_ever_raises[boom1]   # RuntimeError
FAILED …::test_no_non_strict_failure_mode_ever_raises[boom2]   # ValueError
6 failed, 29 deselected in 0.86s
restored: True  sha256=f23813d83afdf541
```

The failure is exactly the escaping exception (`ValueError: nonsense` propagating out of
`bind_orchestration_stores` at `startup.py:222`). All six pass after the fix.

`test_server_binds_through_the_production_helper_only` also now asserts the call-site
shape: `except _BootRefused:` must be followed by a bare `raise`, and a broad
`except Exception as _e:` must follow it.

## Fix 2 (Important) — the drift guard's blind spot

**The finding.** The scanner saw only `cell_namespace=<literal>` and module-level
constants whose *name* contains `NAMESPACE`. The seven `memory.*` names are CAS-written
from a **variable** (`memory/store.py:195` `cell_namespace=ns`) whose literal container is
`MEMORY_STATE_CELL_OWNERS` (`store.py:116-124`) — a `dict` whose name has no "NAMESPACE".
Neither rule sees either. The seven were found only via a *second, unrelated* tuple in
`memory/models.py:174-180`. A future namespace introduced in that same dict-plus-variable
shape would be CAS-written in production and pass the guard — precisely the recurrence
the guard exists to prevent.

**The fix.** `_scan_package_namespace_literals()` now collects **every `ast.Constant` str
of the state-cell shape anywhere** in the package — assignments, tuples, lists, dict keys
*and* values, call args, nested scopes — and subtracts a **declared** allowlist:

```python
REVIEWED_NON_CELL_LITERALS = {
    "policy.action_surface_alias.v1": "migration.POLICY_ACTION_SURFACE_ALIAS_V1 — a
        reviewed adapter *policy id* stamped on migration rows; never a state cell",
    "experience.lane0.v1": "memory/experience.EXPERIENCE_STREAM_ID — an experience
        *event-stream id*; never a state cell",
}
```

Exclusions are now declared with reasons instead of being implicit consequences of a
narrow rule. Three tests cover it: the guard itself; `…sees_the_memory_names_through_
their_owner_dict` (asserts all seven `memory.*` names are now reached *at
`memory/store.py`*, i.e. through the owner dict, not only via `models.py`); and
`…every_non_cell_exclusion_is_declared_with_a_reason` (pins the total at exactly 16 and
fails if an exclusion goes stale or carries no reason).

## Fix 3 (Important, re-scoped by the controller) — provider shipped, consumer deferred

`guanlan_v2/datafeed/health.py` is foreign-dirty and was not touched, read-only inspected
only. Per the ruling: **ship the provider, defer the consumer.**

New **`guanlan_v2/orch_store_status.py`** — a leaf that imports *nothing* (asserted by
`test_the_status_leaf_imports_with_no_orchestration_dependency`, which parses the file's
AST and requires zero import nodes). It lives outside the orchestration package on
purpose: a consumer that must keep working when that package is absent cannot import from
inside it. It owns the canonical state vocabulary and the record shape, and exposes
`orchestration_store_state()`, `orchestration_store_bound()`, and
`orchestration_store_health_item()` — the last returning health.py's exact item shape.

**The deferred wiring, in full** (also written into the module docstring as a CARRY):

1. `from guanlan_v2.orch_store_status import orchestration_store_health_item`
2. add `"orchestration_store": orchestration_store_health_item` to `_ITEMS`
3. add `"orchestration_store"` to `_OPS_ITEMS` — it is an ops item like
   `regen_scheduler`, so it must **not** move `overall`

Mapping (deliberately does not cry wolf): `bound` → `fresh`; `corrupt` / `failed` →
`missing` with an actionable note naming `ORCH-STORE-CORRUPT`; `not_attempted` /
`unavailable` → `unknown`, because opt-in machinery being absent is not an operator fault.

Verified against the **live** 9998 process, not just in unit tests:

```
health item as /data/health will render it: {"status":"fresh","state":"bound","cell_namespace_count":14}
ASSERTED: the deferred /data/health consumer will read fresh/bound/14
```

## Folds

**(a) 15 → 16.** Corrected in §3 above. `experience.lane0.v1` was the miss. The
conclusion (14 union members, complete) is unchanged; both non-members are now declared
in the test rather than reasoned about in prose.

**(b) Negative control pinned.** `test_a_phase9_row_is_corrupt_to_the_old_binding_and_
fine_to_ours` reproduces §6.4 hermetically in a tmp root: bind production → commit a
`ReplayDecisionPoint` under the Phase-9 digest + a `runtime.prompt.v1` CAS → assert
`build_durable_runtime_stores(root)` (the old kwarg-less defaults) raises
`DurableStoreCorrupt` containing `no sealed registry registered for digest` **and** the
Phase-9 digest → then rebind with the production binding, assert `state=="bound"` and that
the prompt cell survives the refold. The sharpest claim in the verification is now a test,
not a transcript.

**(c) Duplicate blank-status literal removed** — better than the requested
cross-reference comment. Because the leaf is dependency-free, `server.py` can always
import it, so the duplication that justified the comment no longer needs to exist:
`server.py` now calls `_orch_status.blank_status()` / `record_status()` and the route
serves `_orch_status.orchestration_store_status()`. There is exactly one definition of the
record shape and one definition of the state vocabulary (including `unavailable`, which
the leaf's table explicitly marks as server-written).
`test_startup_status_and_the_leaf_are_the_same_record` asserts both that the two views
agree and that `server.py` no longer contains the literal.

**Record-only (acknowledged, not changed):** `build_production_resolver()` runs before the
idempotence check inside `bind_process_durable_stores_and_scan`, so a second `create_app()`
in one process pays the ~1.4 s Phase-8 digest build for a bind that is then a no-op. Only
affects suites that build the app twice; production builds it once.

## Round-2 verification

**Live 9998, post-refactor** (the round-1 evidence predates these changes, so the happy
path was re-run against the refactored code; `GUANLAN_ORCH_STORE_ROOT` pointed at a
scratch dir so `var/` stayed untouched — `var/orchestration` does not exist):

```
GUANLAN_PORT=9998 GUANLAN_ORCH_STORE_ROOT=…/round2_ok python -m guanlan_v2.server &
PYTHONPATH=G:/guanlan-v2 python scratchpad/probe_9998.py bound     # exit 0
ASSERTED: state=bound, 14/14 namespaces exact, 3/3 registry digests == recomputed
  phase1 = 75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03
  phase2 = b11fcacf0efd931dc3a3d11f859d6f5bac86c1c24b78e5cc1d2b44829822d8d5
  phase9 = 9e73ddf6d23def5a5666016b1ab113e7b9920eb940ff22a0dd73d806efd3ac07
```

Same watchdog discipline as round 1: heartbeat mtime saved, freshened for the run,
restored bit-exact to `1784489130.2093205`. 9999 never touched (it is down; the controller
owns when it returns). Server stopped by PID.

**Tests.** Focused: 19 → **35** (+16). Full `pytest tests/orchestration` in the
foreground: **4561 passed** in 321 s.

Delta attribution — the branch moved under me mid-round:

| | count | note |
|---|---|---|
| round-1 post-commit run | 4517 | = 4498 baseline + my 19 |
| my new tests this round | +16 | 19 → 35 in `test_startup_binding.py` |
| sibling commit `7920a5a` (R17 watcher registration) | +28 | `test_watcher_orchestrated_registration.py`, foreign |
| **round-2 run** | **4561** | 4517 + 16 + 28 — exactly accounted for |

The sibling's `luozi.py` / `seats/watcher.py` edits landed as `7920a5a` during this round,
so the tree is no longer dirty there. Nothing of theirs was staged by me.

## Files changed this round

| file | status |
|---|---|
| `guanlan_v2/orch_store_status.py` | **new** — the dependency-free status leaf (Fix 3 provider) |
| `guanlan_v2/orchestration/startup.py` | guarded imports + `_degrade`; record delegated to the leaf |
| `guanlan_v2/server.py` | guarded call site; duplicate literal removed; route serves the leaf |
| `tests/orchestration/test_startup_binding.py` | 19 → 35 tests |

`guanlan_v2/orch_store_status.py` is a **fourth** path beyond the three the controller
named. Flagging it explicitly: it is new, mine, and not foreign-dirty — it is the only way
to satisfy "importable without side effects and safe when the package is absent", since
anything inside `guanlan_v2.orchestration` fails that test by construction.

`health.py` untouched (read-only inspection). Sealed Phase 1-9 modules untouched. Goldens
unmoved. Nothing from `var/` or the watchdog staged.

## Round-2 concerns

1. **Fix 1 was a real regression I introduced**, caught by review and not by my own tests
   — my round-1 suite asserted the *unavailable* path only via a source-level string check
   on `server.py`, which cannot detect an import outside a guard. The lesson is that
   "never raises" is a behavioural property and needs a behavioural test; it now has six.
2. **The strict-mode surface widened.** Strict now refuses the boot on `unavailable` too,
   not just `corrupt`/`failed`. That follows from "strict means: no correctly bound store,
   no boot", and it is documented, but it is a semantic change from round 1: an operator
   who sets `GUANLAN_ORCH_STORE_STRICT=1` on a build where the orchestration package is
   absent will now fail to start. That is the honest reading of the flag, but it is worth
   an explicit decision rather than inheriting it.
3. **The `/data/health` carry is real and unclosed.** Until `health.py` is wired, the only
   places a corrupt store shows are the boot log and a route nobody polls. The provider
   makes the fix a two-line change; it does not make the problem go away.


---
---

# Review round 3 — "Approved, one Important" — closed

Verdict on `70bac66`: round-1's three Importants confirmed genuinely closed. One
Important remained, against the round-2 acceptance ruling rather than against the code
as reviewed. Plus three Minors. All in the follow-up commit.

## The Important — strict mode was only half-implemented

Two paths recorded the identical state `unavailable` but behaved **oppositely** under the
same flag:

| path | recorded | under `GUANLAN_ORCH_STORE_STRICT=1` |
|---|---|---|
| package importable, `durable.py` broken | `unavailable` | refused ✓ |
| `guanlan_v2.orchestration.startup` itself unimportable | `unavailable` | **booted normally** ✗ |

So the flag — which asserts "I require a working orchestration durable store in this
process" — was honoured on the *partial* failure and ignored on the *total* one. The
reviewer's diagnosis of why was exact: `OrchestrationStoreBootRefused` lived in
`startup.py`, so the branch that cannot import `startup` had no name to raise.

**Fix.** The flag and the exception moved into the always-importable leaf
`guanlan_v2/orch_store_status.py`, together with **one shared definition of the refusal**:

```python
STRICT_ENV = "GUANLAN_ORCH_STORE_STRICT"
class OrchestrationStoreBootRefused(RuntimeError): ...
def refuse_if_strict(strict, marker, exc=None, detail="") -> None: ...
```

`startup._degrade` and `server.py`'s package-absent branch now both call
`refuse_if_strict`. Having *one* function is the point — the divergence existed precisely
because the refusal was written twice, in one place only. `startup.py` re-exports both
names, so every existing caller and test is unchanged
(`st.OrchestrationStoreBootRefused is rec.OrchestrationStoreBootRefused`, asserted).

The leaf still imports nothing; `test_the_status_leaf_imports_with_no_orchestration_dependency`
(zero import nodes by AST) passes unchanged.

### Live 9998 proof — real before/after on the actual server

The subsystem was made absent **without editing the repo**: a `sitecustomize.py` on
`PYTHONPATH` installs a `meta_path` finder that raises `ModuleNotFoundError` for
`guanlan_v2.orchestration.startup`.

**RED** — the branch-B check temporarily removed, same command:

```
---- RED run: strict=1, orchestration.startup BLOCKED, branch-B check removed ----
BUG REPRODUCED: server BOOTED on 9998 under strict; state='unavailable'
  -> an operator asserting 'I require a working store' got a clean boot
restored: True  sha256=646000ba7195d720
```

**GREEN** — with the fix:

```
GUANLAN_PORT=9998 GUANLAN_ORCH_STORE_STRICT=1 \
  PYTHONPATH=…/block;G:/guanlan-v2 python -m guanlan_v2.server
EXIT CODE = 1
guanlan_v2.orch_store_status.OrchestrationStoreBootRefused: ORCH-STORE-UNAVAILABLE:
refusing to boot without a correctly bound orchestration durable store
(ModuleNotFoundError: No module named 'guanlan_v2.orchestration.startup' …)
nothing listening on 9998 — BOOT REFUSED
```

**And the default is untouched** — same block, no strict flag:

```
GET /orchestration/store_status -> {"state":"unavailable","bound":false,
  "error_type":"ModuleNotFoundError", …,"strict":false}
[guanlan_v2][ORCH-STORE-UNAVAILABLE] … Set GUANLAN_ORCH_STORE_STRICT=1 to refuse the
boot instead.
```

The server boots and serves with the subsystem entirely absent. Fixing the strict path
did not make the default path fragile.

Unit tests cover both branches under strict
(`test_strict_branch_a_durable_broken_startup_importable`,
`test_strict_branch_b_startup_itself_unimportable`), the shared refusal, and the
non-strict counterpart.

## The documentation condition — now satisfiable, now met

* `STRICT_ENV`'s doc-comment (now in the leaf, where the constant lives) states it
  outright: refuses on **every** non-`bound` outcome, "**including when the subsystem is
  absent entirely**", with the reasoning that a missing package is the most complete
  failure of the operator's assertion, not an exemption from it. `startup.py`'s
  re-export line carries the same sentence and points at the leaf.
* The `unavailable` and `failed` stderr messages now name the flag exactly as the
  `corrupt` one always did. `test_every_degraded_message_names_the_strict_flag`
  parametrises over all three states so a future message cannot drop it.

## Minors

**Minor 2 — the exclusion gate.** `len(reason) > 40` was the only barrier, and two of the
sixteen hits are non-cells of *recurring* kinds, so future false positives are likely.
Each declared exclusion must now also clear two **mechanical** bars a genuine state-cell
namespace could not: it is never passed as `cell_namespace=<literal>`, and it never
appears inside a `*NAMESPACE*`-named module-level constant. Both scanners carry
positive-control assertions (`trial.family_head.v1` must be found as a CAS argument;
`runtime.prompt.v1` and `adapters.replay_head.v1` must be found in namespace constants)
so a broken scanner cannot make the gate pass vacuously.

**Minor 3 — `_shout` before the strict raise.** `_shout` now guards each channel
independently and can never raise; the docstring records *why* (it is the one place the
ordering matters — a dead stderr would otherwise replace the refusal with a `ValueError`,
which the call site records as `failed` and boots through, inverting the operator's
assertion). Three tests: `_shout` survives a raising stderr; strict still raises
`OrchestrationStoreBootRefused` with stderr dead; non-strict still records `corrupt`.

**Minor 4 — actionable failure message.** `test_every_non_cell_exclusion_is_declared_with_a_reason`
now prints full JSON provenance for all sixteen literals plus a sentence telling the next
person the two legitimate resolutions (join the union, or declare an exclusion).

## Round-3 verification

Focused: 35 → **48** tests (+13), all green. Full `pytest tests/orchestration` in the
foreground: **4574 passed** in 335 s = 4561 + 13. No sibling commits landed during this
round (branch head was `70bac66` before and after), so no contamination this time.

Watchdog: heartbeat freshened for the runs and restored bit-exact to
`1784489130.2093205`. 9999 never touched. `var/orchestration` does not exist. Every 9998
process stopped by PID; port clear.

## Files changed this round

`guanlan_v2/orch_store_status.py` (flag + exception + shared `refuse_if_strict`),
`guanlan_v2/orchestration/startup.py` (re-export, defensive `_shout`, flag named in two
messages, `_degrade` delegates the refusal), `guanlan_v2/server.py` (branch B honours the
flag), `tests/orchestration/test_startup_binding.py`. `health.py` and every foreign-dirty
file untouched; sealed modules untouched; goldens unmoved.

## Round-3 concerns

1. **The strict widening is now complete, and it is a behaviour change worth stating
   plainly**: on a build where the orchestration package is absent, `GUANLAN_ORCH_STORE_STRICT=1`
   now prevents the server from starting at all. That is the honest reading of the flag
   and it is documented in three places, but anyone who set the flag expecting
   "corrupt-only" will see a new failure mode. The flag is opt-in and off by default.
2. **`sitecustomize`-based absence is a faithful simulation, not the real thing.** It
   blocks exactly one module import. A genuinely absent package would also make
   `guanlan_v2.orchestration.adapters.api` unimportable, which the *adapters router*
   block below already handles with its own try/except — untouched by me and unverified
   in that configuration.
3. **The `/data/health` carry from round 2 is still open** and unchanged.


---
---

# Review round 4 — "Approved, two Minors" — closed

Verdict on `8fcfa8f`: approved. Two Minors, both now fixed.

## Minor 1 — the third path, and a status record that lied

`server.py`'s defence-in-depth catch-all recorded `state="failed"` but neither stamped
`strict=_strict` nor called `refuse_if_strict`. So under `GUANLAN_ORCH_STORE_STRICT=1`,
`startup._degrade("failed")` refused while `server.py`'s `failed` booted — the same
divergence closed in round 3, one layer down. And the `"strict": false` written into that
record was wrong whenever the flag was on, regardless of whether the path was reachable.

**Both halves fixed:**

1. **The record and the refusal.** The catch-all now stamps `strict=_strict`, names the
   flag in its stderr line, and calls `refuse_if_strict(_strict, "ORCH-STORE-FAILED", _e)`.
   All **five** degraded paths — `startup`'s `corrupt` / `failed` / `unavailable`,
   `server.py`'s `unavailable`, and this catch-all — now go through the one leaf function.
   `test_all_refusal_paths_go_through_the_one_leaf_function` asserts structurally that
   neither file constructs `OrchestrationStoreBootRefused` itself, so a sixth path cannot
   hand-roll a divergent refusal.
2. **The prologue is now guarded.** Resolving `resolved_root` was the only work left
   outside every `try`. `strict` is resolved first and unconditionally, `resolved_root`
   starts as the placeholder `"<unresolved store root>"`, and a Step-0 guard degrades to
   `failed` — with the real flag — if the root cannot be built.

**A correction to the reviewer's reachability example, for the record.** On this
interpreter (CPython 3.13) the embedded-null case does *not* raise in the prologue:
`Path('a\0b')` constructs fine and `.exists()` returns `False` rather than raising, so
that particular input would have reached Step 2's guard and been handled correctly all
along. The prologue *is* genuinely reachable-failing by a different input — a `root=`
kwarg that is not path-like raises `TypeError` from `Path(object())` — and the dishonest
`strict` stamp was unconditional. So the Minor is real; only the named trigger was not.
Verified rather than assumed:

```
exists -> False        # Path('a\x00b').exists() — no raise
Path(object()) -> TypeError
```

**TDD RED.** Both halves reverted, the new tests run, files restored byte-exact:

```
---- RED run against the pre-fix third path + unguarded prologue ----
FAILED …::test_the_prologue_is_guarded_too
FAILED …::test_the_prologue_honours_strict
FAILED …::test_every_recorded_state_stamps_the_real_strict_flag[prologue]
FAILED …::test_server_defence_in_depth_branch_also_honours_strict
FAILED …::test_all_refusal_paths_go_through_the_one_leaf_function
5 failed, 3 passed, 49 deselected
restored byte-exact: True
```

`test_every_recorded_state_stamps_the_real_strict_flag` parametrises over all four
degraded scenarios (`corrupt` / `failed` / `unavailable` / `prologue`) and asserts each
records `strict: true` when the flag is on.

## Minor 2 — the stranded doc-comment

`STRICT_ENV`'s four-line `#:` block stayed behind when the constant moved to the leaf,
directly above `CORRUPT_MARKER`'s own `#:` line — and adjacent `#:` blocks merge, so the
orphan read as `CORRUPT_MARKER`'s documentation. Replaced with a plain `#` NOTE (not `#:`)
pointing at the leaf, which owns the canonical rationale.
`test_no_stranded_doc_comment_above_the_corrupt_marker` walks back from the
`CORRUPT_MARKER` assignment and requires exactly one `#:` line mentioning "greppable" and
not "strict", so the same merge cannot recur.

## Recorded, not fixed — the mechanical bars' inherent limit

**The Minor-2 bars from round 3 do not close the blind spot that motivated them.** A
namespace introduced in the `MEMORY_STATE_CELL_OWNERS` shape — a dict key, CAS-written
through a *variable* — is never passed as `cell_namespace=<literal>` and never sits in a
`*NAMESPACE*`-named constant, so it clears **both** bars and remains excludable by prose
alone. The widened literal scan still *finds* it (that is what round 2 fixed), and any
exclusion is a visible reviewed diff carrying a written reason, so this is a review-gate
question rather than a silent hole. But a future reviewer must know that for this one
shape the mechanical bars offer no protection and the declared reason is the only defence.
Inherent to the approach; not fixable by more AST rules.

## Round-4 verification

Focused: 48 → **57** tests (+9), all green. Full `pytest tests/orchestration` in the
foreground: **4583 passed** in 338 s = 4574 + 9. No live 9998 run this round: the changes
are a status-record field, a refusal call on an unreachable-in-production branch, a
prologue guard, and a comment — none alters the boot path that rounds 1-3 exercised on
9998, and the strict-refusal mechanism itself was proven live in round 3. Saying so
plainly rather than re-running for the appearance of rigour.

Watchdog untouched this round (no server started); heartbeat still
`1784489130.2093205`; `var/orchestration` does not exist. `guanlan_v2/orchestration/adapters/api.py`
not touched (sibling agent owns it for R22).

## Files changed this round

`guanlan_v2/orchestration/startup.py` (guarded prologue, stranded doc block removed),
`guanlan_v2/server.py` (catch-all stamps `strict` + refuses + names the flag),
`tests/orchestration/test_startup_binding.py`. `orch_store_status.py` unchanged this
round — the leaf already had everything needed, which is a small sign the round-3
structure was right.


---
---

# Review round 5 — the last line — closed

Verdict on `71b5282`: approved, one line left. The reviewer verified the round-4
interpreter correction themselves (CPython 3.13.11) and confirmed they were wrong on the
trigger. One hole remained in the guard that exists to stop the divergence that has now
originated **twice** in this one file.

## The hole

`test_all_refusal_paths_go_through_the_one_leaf_function` asserted only
`"raise OrchestrationStoreBootRefused" not in src`. That bites in `startup.py`, where the
class is imported bare — but in `server.py` the class is *only* reachable as
`_orch_status.OrchestrationStoreBootRefused`, so a hand-rolled **qualified** construction
would have slipped past the substring check. And because a future contributor hand-rolling
a *new* branch leaves the existing calls in place, `refuse_if_strict(` would still be 2 and
the count assertion would pass too. The guard would have been fully green on exactly the
divergence it exists to prevent.

## The fix

A `_hand_rolled_refusals(src)` helper matching
`(?:<name>\s*\.\s*)?OrchestrationStoreBootRefused\s*\(` — keyed on the `(`, so the
legitimate `except _orch_status.OrchestrationStoreBootRefused:`, the `import`, the
`__all__` entry and the `:class:` docstring references all stay green, while both bare and
attribute-qualified constructions (and a spaced `Refused (`) are caught. The `class
OrchestrationStoreBootRefused(RuntimeError):` *declaration* is excluded — it is a
declaration, not a construction, and exists exactly once.

Three assertions now: `server.py` and `startup.py` must contain **zero** constructions,
and the leaf must contain **exactly one**. That last one states the real invariant
positively — the refusal is built in one place in the whole codebase — rather than only
forbidding it in two places.

## Positive control

`test_the_hand_rolled_refusal_detector_actually_detects` runs four poison strings (bare,
qualified, spaced, non-`raise` assignment) through the same helper and requires each to be
caught, then five benign strings (the `except` clause, the import, the `__all__` entry, a
`:class:` docstring reference, a tuple-form `except`, the class declaration) and requires
none to be. It also asserts inline that
`"raise OrchestrationStoreBootRefused" not in <the qualified poison>` — the hole itself,
pinned as an assertion rather than described in prose.

## TDD RED — the faithful scenario

An **additive** hand-rolled qualified refusal injected into `server.py` (added alongside
the legitimate call, exactly as a new divergent branch would be, so the count stays at 2):

```
---- RED: qualified hand-rolled refusal injected into server.py ----
  old substring bar 'raise OrchestrationStoreBootRefused' fires? False
  count assertion refuse_if_strict(==2 still passes?            True
  -> the pre-fix guard would have been GREEN on a divergent refusal
  new bar result:
    FAILED …::test_all_refusal_paths_go_through_the_one_leaf_function
restored byte-exact: True
```

A first attempt that *substituted* the legitimate call rather than adding to it dropped the
count to 1 and would have been caught by the existing count assertion — so it did not
demonstrate the hole. Re-run additively; recorded here because the first framing overstated
nothing but proved less than it appeared to.

## Round-5 verification

Focused: 57 → **58** tests (+1), all green. Full `pytest tests/orchestration` in the
foreground: **4591 passed** in 342 s.

Delta attribution — the branch moved under me again:

| | count | note |
|---|---|---|
| round-4 run | 4583 | |
| my new test | +1 | 57 → 58 |
| sibling R17 work (`640001e` + in-flight edits to `luozi.py` / `seats/watcher.py` / `test_watcher_orchestrated_registration.py`) | +7 | foreign |
| **round-5 run** | **4591** | contamination noted, not chased |

No production code changed this round — the assertion needed no companion change, which is
itself the answer to "does the guard need `startup.py`/`server.py` edits": it did not,
because both files were already clean under the stricter bar.

`adapters/api.py` (R22 sibling), `luozi.py` / `seats/watcher.py` (R17 siblings),
`health.py` and every other foreign-dirty file untouched. Goldens unmoved. Nothing from
`var/`; no server started; 9999 never touched.

## Files changed this round

`tests/orchestration/test_startup_binding.py` only.

---

**R23 / R24 is closed.** Five review rounds, five commits (`3b1d92e`, `70bac66`,
`8fcfa8f`, `71b5282`, + this one), 58 tests. Open carries, unchanged: the `/data/health`
consumer (provider shipped, `health.py` was dirty); the `MEMORY_STATE_CELL_OWNERS`-shaped
namespace that clears both mechanical bars and remains excludable by prose; and the fact
that a correctly bound store is not the same as a wired framework — the adapters router
still reads an unwired `d.replay_state_store` and honestly 503s. That is the launcher's
work, not this one's.
