# R21 — production approval verifier (config-declared operator allowlist)

Branch `report-evidence-pack`, base HEAD `f501210`. No branch switch, no push, no merge.

---

## 1. What was built

Three new files, all pure additions. **No Phase 1–9 module was modified.** No golden moved.

| File | Role |
|---|---|
| `guanlan_v2/orchestration/adapters/identity.py` | NEW — the one production `verify(actor) -> principal` port |
| `config/orchestration/operators.json` | NEW — the committed operator declaration (one row: `human:ops`) |
| `tests/orchestration/test_operator_identity.py` | NEW — 59 tests incl. the R21 acceptance |

### Public surface of `identity.py`

```
DEFAULT_OPERATOR_ALLOWLIST_PATH : Path   # <repo>/config/orchestration/operators.json
VERIFIED_BY = "config-operator-allowlist"
OperatorIdentityError(Exception)         # base — every refusal
  OperatorAllowlistError                 # the DECLARATION is unusable
  OperatorNotAllowed                     # the ACTOR is not a declared operator
load_operator_allowlist(path=None) -> tuple[str, ...]
ConfigOperatorVerifier(*, allowlist_path=None, verified_by=VERIFIED_BY)
    .allowlist_path -> Path
    .verify(credential: Any) -> AuthenticatedAdminPrincipal
```

### Honesty statement (verbatim intent of the docstring)

It proves exactly one thing: *the actor id handed to `verify` is character-for-character
equal to an id declared on the operator list in this repository's local config file.*
The docstring then states, in plain words, that it

* does **NOT authenticate a human** — nobody proved they are the person behind `human:ops`;
* does **NOT prove possession of a credential** — no password, token, signature,
  challenge, session or second factor exists; the "secret" is a plaintext id in a
  committed file;
* does **NOT survive anyone who can write the local config** — filesystem write access
  to this repo *is* approval authority; there is deliberately no tamper seal, because a
  digest would only pretend to add a property the file's own mutability contradicts;
* is **not a security boundary** against any attacker already running code as this user.

What it *is* good for is stated too: making "a decision was taken under an operator id
this installation declared in advance" a checkable, durable fact, and making every other
actor id a hard refusal instead of a silent pass. The module names itself as the seam to
replace if real multi-party authority ever arrives — the coordinator above it needs no
change.

### Fail-closed matrix (all implemented, all tested)

| Input | Result |
|---|---|
| non-`str` actor (`None`, bytes, int, bool, list, dict, object) | `OperatorNotAllowed` |
| empty / whitespace-only actor | `OperatorNotAllowed` |
| near-miss (` human:ops`, `human:ops `, `HUMAN:OPS`) | `OperatorNotAllowed` — exact match, no trim, no case-fold |
| `*`, `any`, `default`, `admin`, `human:*` | `OperatorNotAllowed` — **no wildcard, no default operator** |
| `lease:*` actor | `OperatorNotAllowed` (see §3) |
| actor absent from the list | `OperatorNotAllowed` |
| missing config file | `OperatorAllowlistError` |
| config path is a directory / unreadable | `OperatorAllowlistError` |
| BOM-carrying or non-UTF-8 config | `OperatorAllowlistError` |
| non-JSON / `[]` / missing key / wrong `schema_version` / non-list / non-object row / extra key / renamed key / non-string `actor_id` | `OperatorAllowlistError` |
| **empty** `operators` list | `OperatorAllowlistError` — "an empty list means nobody can approve, never everybody" |
| duplicate declared id | `OperatorAllowlistError` |
| one unusable declared row (blank, padded, whitespace-bearing, control char, `*`, `lease:`/`LEASE:`) | `OperatorAllowlistError` for the **whole** declaration — no partial trust, mirroring the approval journal's fold |

Two other deliberate properties:

* **Construction never touches disk.** A broken declaration must break the *decision*,
  not the wiring — otherwise the coordinator would end up built with `verifier=None`,
  which is a different (and less informative) refusal.
* **The declaration is re-read on every `verify`.** The list in force at *decision* time
  is authoritative: revoking an operator takes effect immediately with no restart, and a
  config deleted or corrupted after start-up refuses the next decision instead of serving
  a stale in-memory copy. The file is a few hundred bytes and human decisions are rare,
  so there is no reason to cache. Proven by `test_declaration_is_reread_on_every_verify`.

---

## 2. Config convention chosen — and why

**Chosen: `config/orchestration/operators.json`**, located by a module constant
`Path(__file__).resolve().parents[3] / "config" / "orchestration" / "operators.json"`.

Rationale, all convention-derived rather than invented:

1. **Location.** Every orchestration component that reads config does exactly this
   `parents[N] / "config" / "orchestration" / …` walk — `catalog_runtime._CONFIG_ROOT`,
   `presets._CONFIG_ROOT`, `bootstrap` (lane0 inventory), `capability_manifest`,
   `lane_catalog._MATERIALS`, `phase7_registry`, `trial`, `skilltree`, `planner_gateway`
   (`config/llm.yaml`). Nothing in the package uses `var/`, `.data/`, or an env var for
   *declarations*.
2. **Top-level, not under `materials/`.** `config/orchestration/materials/**` is
   catalog-bound, digest-addressed prompt/guardrail content that flows into plan
   material refs. An operator list is neither prompt material nor catalog-bound, so it
   sits beside `catalogs/`, `materials/`, `presets/` as a peer — the same tier as
   `presets/`, which is the closest analogue (a reviewed local declaration consumed by a
   service).
3. **JSON, not YAML.** `presets/*.json` and `materials/guardrails/*.json` are JSON;
   `catalogs/*.yaml` are YAML because they are hand-authored multi-hundred-line
   documents. A ten-line allowlist follows the presets precedent.
4. **Loader strictness copied from `plan_presets.load_preset_registry`**: read bytes →
   reject BOM → strict UTF-8 decode → `extra="forbid"`, `strict=True` pydantic validate →
   typed error on anything malformed → reject duplicates. Same error shape, same
   "no silent skip" rule.
5. **No env-var override of the path.** An environment variable that can redirect the
   authority list would silently undo the one claim the module makes. A caller needing
   another location passes `allowlist_path=` explicitly (which is how every test runs).
   `var/secrets.env` is neither read nor referenced in code — the allowlist is a
   *declaration*, not a secret. An AST-level test enforces "no `os` import, no
   `environ`/`getenv` access" so a future edit cannot quietly add one.
6. **Committed, not generated.** `config/orchestration/**` is git-tracked and not
   gitignored (verified with `git check-ignore`), so the declaration ships with the repo
   and is reviewable in diff.

The config carries its own warning in the `note` field of the shipped row, so anyone
editing the file sees the limitation without opening the source.

---

## 3. Lease-actor decision: **`lease:*` is REFUSED** — as actor *and* as a declarable id

### The decision

`ConfigOperatorVerifier.verify` refuses any actor whose id starts with `lease:`
(case-insensitive) with `OperatorNotAllowed`, **before** it even reads the declaration.
`load_operator_allowlist` independently refuses a declaration containing such a row, so
a `lease:` id cannot be smuggled onto the list by someone with config write access.

### Rationale (three independent reasons, all verified at source)

1. **No production path needs the verifier to accept a lease actor.** The lease channel
   is deliberately verifier-free. `approval.py:740 register_and_try_lease` →
   `:811 _admit_under_lease` calls the shared `_record_terminal_decision` **directly**
   with `actor_id=f"{_LEASE_ACTOR_PREFIX}{lease_id}"`; the docstring at `:551-553` says so
   explicitly ("the lease path … stamps `lease:<lease_id>` — the standing authorization is
   the authority, so no verifier is consulted at consume time"). `_verifier.verify` is
   reached from exactly three places — `decide` (:522), `issue_lease` (:642),
   `revoke_lease` (:723) — and in all three the verified actor is the *human*
   (`issued_by`, `revocation.actor_id`). Refusing `lease:*` costs Lane 0 nothing.
2. **Accepting it would open a real hole.** `decide(actor="lease:<id>")` would mint a
   lease-signed `PlanApproval` with **no lease, no `lease_consumed` row and no envelope
   drawn** — bypassing `valid_from/valid_until`, `max_admissions` and
   `budget_cap_llm_invocations`, i.e. everything that makes a lease bounded. It gets
   worse on read-back: `register_and_try_lease`'s first idempotency branch (`:773-778`)
   does `existing.actor_id.startswith(_LEASE_ACTOR_PREFIX)` and returns
   `outcome="lease_admitted", lease_id=existing.actor_id[len(prefix):]` — so a forged
   actor would make the coordinator *report* an admission under a lease that never
   existed, and `list_leases` would never show it.
3. **`autonomy/playbooks.py:117`'s `actor=f"lease:{lease_id}"` does not reach the
   verifier.** It is handed to the `Lane0BootstrapService.admit_and_run` **port**, whose
   documented production route (`playbooks.py:100-106`) is
   `register_and_try_lease` → real `PlanApproval` → `admit_after_approval` → `run_plan`.
   That route is the verifier-free one. So the refusal does not break Lane 0 now, and
   any future implementation that tried to route a lease actor through `decide` *should*
   fail loudly — that would be the bug, not the guard.

### Proven by test, in the direction decided

* `test_lease_prefixed_actor_is_refused_by_the_verifier`
* `test_a_lease_actor_cannot_be_declared_into_the_allowlist` (declaration side, both via
  `load_operator_allowlist` and via a subsequent `verify` of the *good* row)
* `test_the_verifier_free_lease_path_still_admits_with_a_lease_actor` — a real coordinator
  built with `ConfigOperatorVerifier` issues a lease with `actor="human:ops"`, then
  `register_and_try_lease` admits with `approval.actor_id == f"lease:{lease.lease_id}"`,
  **and that very string is then shown to be refused by `verify`**. Both halves in one
  test, so the separation of the two paths is the assertion.
* `test_decide_with_a_forged_lease_actor_refuses_and_records_nothing` — the hole itself:
  journal bytes unchanged, `load_decision(...) is None`.
* `test_issue_and_revoke_lease_are_gated_by_the_same_verifier` — `issue_lease` stamps
  `issued_by == "human:ops"`, refuses `human:intruder`, `revoke_lease` stamps
  `actor_id == "human:ops"` and refuses `lease:<id>`.

---

## 4. The principal shape, and the `.actor` verification at source

`approval.py` reads **`.actor`**, three times:

```
:522  principal = self._verifier.verify(actor)
:523  verified_id = principal.actor          # decide
:642  principal = self._verifier.verify(actor)
:643  issued_by = principal.actor            # issue_lease
:723  principal = self._verifier.verify(actor)
:724  actor_id = principal.actor             # revoke_lease
```

The survey's trap is **confirmed real**: `tests/orchestration/test_adapters_api.py:1359-1361`

```python
class _StubVerifier:
    def verify(self, material):
        return SimpleNamespace(actor_id="human:ops")   # <-- .actor_id, never read
```

It is used only at `:1380` / `:1390`, both construction-only paths
(`build_plan_approval_coordinator`), so `.actor` is never dereferenced and nothing fails.
Left untouched (it is a Phase-9 review-sealed test file and the stub is correct *for what
it exercises*), but the mismatch is now pinned by a source-level test of my own:
`test_approval_py_reads_principal_dot_actor_at_source` asserts, for all three methods,
that the source contains `self._verifier.verify(actor)` and `principal.actor` and does
**not** contain `principal.actor_id`. If either side drifts, that test breaks loudly.

**Principal returned:** `guanlan_v2.orchestration.memory.models.AuthenticatedAdminPrincipal`
— the repo's existing canonical principal (`actor: str`, `verified_by: str`), the exact
type Phase 3's `AdminReviewVerifier` Protocol (`memory/proposals.py:81-85`) declares and
that every Phase-7 test double already returns. Reusing it rather than inventing a new
carrier means:

* `.actor` is right by construction and validated by pydantic;
* nothing new enters the contract surface (see §6);
* `memory/proposals.py:185`, the only *other* production consumer of a principal, also
  reads `principal.actor` — one shape serves both.

`verified_by` is the constant `"config-operator-allowlist"` — named after the *mechanism*
so anyone reading an audit trail is never misled into thinking a human was authenticated.
No digest is embedded (a digest over a freely-writable file would imply a tamper property
that does not exist).

---

## 5. Tests + results, with TDD evidence

### RED

Test file written first, before any implementation:

```
$ python -m pytest tests/orchestration/test_operator_identity.py -x -q
tests\orchestration\test_operator_identity.py:43: in <module>
    from guanlan_v2.orchestration.adapters.identity import (
E   ModuleNotFoundError: No module named 'guanlan_v2.orchestration.adapters.identity'
1 error in 0.46s
```

### RED → partial GREEN → GREEN

First run after implementing `identity.py` + `operators.json`: **2 failed, 57 passed**.
Both failures were *my tests being wrong*, and both were fixed in the test, not by
weakening the implementation:

1. `test_a_lease_actor_cannot_be_declared_into_the_allowlist` expected
   `OperatorAllowlistError` but got `OperatorNotAllowed`, because `verify` short-circuits
   the `lease:` prefix *before* reading config (correct ordering — never touch disk for an
   obviously-invalid actor). Rewritten to assert the declaration side via
   `load_operator_allowlist` directly, which is the honest assertion.
2. `test_identity_module_imports_nothing_that_writes_trades_or_memory` did a raw substring
   scan and tripped on the module **docstring** legitimately saying it never reads
   `var/secrets.env`. Replaced with an AST-based check (imports + `environ`/`getenv`
   attribute access), which is what the guard actually meant.

Final:

```
$ python -m pytest tests/orchestration/test_operator_identity.py -q
59 passed in 1.18s
```

### Sealed-surface regression (goldens, firewalls, approval)

```
$ python -m pytest tests/orchestration/test_contract_completeness.py \
      tests/orchestration/test_phase9_registry_chain.py \
      tests/orchestration/test_approval_store.py tests/orchestration/test_approval_lease.py \
      tests/orchestration/test_dynamic_e2e.py tests/orchestration/test_adapters_api.py -q
187 passed in 27.54s
```

### Full suite (FOREGROUND, before commit)

```
$ python -m pytest tests/orchestration -q
4492 passed in 312.67s (0:05:12)          # baseline 4433 + 59 new = 4492 exactly

$ python -m pytest tests -q --ignore=tests/orchestration
1496 passed in 275.35s (0:04:35)          # rest of the repo, untouched
```

Zero failures anywhere. `4433 → 4492` is `+59`, exactly the new test count — no pre-existing
test changed state.

### Golden immutability

`git diff --stat -- tests/orchestration/golden/` is **empty**. Spot-checked
`phase9_retirement_gates_v1.json` — its embedded `"gates_digest"` is still
`68568b49a6bada141ffd6c8f817351c700f6f37680772e41dda68d2ba2c80041`, unchanged. (Note for
the record: `68568b49…` is the *gates_digest field inside* that golden, not the file's
own sha256, which is `5e2660f7…`.) The two chain goldens and every upstream manifest are
byte-untouched and re-verified green by `test_phase9_registry_chain.py` and
`test_inherited_entries_are_byte_identical_across_every_upstream_manifest`.

### Test inventory (59)

Principal shape (3) · actor-side fail-closed (5 groups, 20 parametrised cases) ·
declaration-side fail-closed (10 malformed shapes + BOM + directory + duplicate + empty +
9 unusable-id cases) · re-read semantics + no-disk-on-construction (2) · shipped repo
declaration (2) · lease decision (4) · **R21 acceptance against the real coordinator (4)**
· **R21 acceptance against the real `PlanAdmissionService` (1)** · housekeeping firewall
guards (2).

---

## 6. Why nothing was registered, and why the module lists stayed put

The task's contingency ("if a new module trips the contract-completeness disk-walk, add it
to `PHASE9_MODULES` **and** `PHASE9_CONTRACT_MODULES`") **did not fire**, and that is a
verified fact rather than an assumption.

Both firewalls key on *public `ContractModel` subclasses*:

* `test_contract_completeness._modules_defining_public_contract_models()` walks the whole
  package with `pkgutil.walk_packages` and collects a module only if it defines a class
  that `issubclass(obj, ContractModel)` **and** `obj.__module__ == mod_info.name` **and**
  the name is not `_`-prefixed;
* `test_phase9_registry_chain._discover_phase9_contract_models()` applies the same filter
  over its own module tuple.

`identity.py` defines **no** `ContractModel` subclass. Its two config models are plain
`pydantic.BaseModel` **and** `_`-prefixed (`_OperatorRow`, `_OperatorAllowlistFile`) —
belt and braces, so it is excluded by two independent clauses. `AuthenticatedAdminPrincipal`
is *imported*, not defined, so the `__module__` clause excludes it. Consequently the
package walk imports the module (it does import cleanly, with no side effects) and finds
nothing to classify. `PHASE9_MODULES` / `PHASE9_CONTRACT_MODULES` are therefore **not
touched**, and the set-equality sync guard at `test_phase9_registry_chain.py:313` stays
satisfied trivially. No extra staged file.

This matches how the other Phase-9 **service ports** are already classified:
`EXPECTED_INTERNAL_NONCONTRACT_NAMES` (`test_phase9_registry_chain.py:92-95`) holds
`ReplayPointClock`, `PitReaderRawSource`, `LiveClientSource`, `ReplayRuntimeBindings`,
`WeiwoRuntimeBindings`, `WeiwoRunReceipt` — ports and carriers, none registered in the
cumulative schema registry. A verifier is the same species. The classification firewall
does not *demand* an entry for a module that contributes no models, so no reason string
was added anywhere; instead the property is locked by my own
`test_identity_module_defines_no_public_contract_model`, which fails if a future edit adds
a public contract to this module without going through the firewall.

`adapters/__init__.py` was **not** modified — it documents a deliberately empty re-export
surface, and `identity` follows `luozi`'s precedent of direct-module import.

---

## 7. Files changed

Committed with **explicit pathspecs only** (never `git add -A`; a concurrent session owns
uncommitted work in `guanlan_v2/console/`, `guanlan_v2/datafeed/`, `guanlan_v2/glmcp/`,
`ui/screen/`, `docs/README.md`, `.data/**` plus several untracked files — none staged):

Commit `ddccdd6` (parent `f501210`), 3 files, 851 insertions, 0 deletions:

```
config/orchestration/operators.json                  (new,   9 lines)
guanlan_v2/orchestration/adapters/identity.py        (new, 336 lines, ~130 of them the
                                                      honesty/lease/config docstring)
tests/orchestration/test_operator_identity.py        (new, 506 lines, 59 tests)
```

`git status` after the commit still shows the concurrent session's 15 modified/deleted
paths, untouched and unstaged.

Not committed: this report (`.superpowers/` is gitignored at `.gitignore:75`).

---

## 8. Findings that refine the survey's map

1. **The survey's trap is real and I reproduced it** — `_StubVerifier` returns `.actor_id`
   while `approval.py` reads `.actor`; it survives only because it is used on
   construction-only paths. Now pinned by a source-level test (§4).
2. **`68568b49…` is a field, not a file hash.** It is the `"gates_digest"` *inside*
   `phase9_retirement_gates_v1.json`; the file's own sha256 is `5e2660f7…`. Anyone
   checking "the golden didn't move" by sha256 against `68568b49…` will get a false alarm.
3. **The disk-walk contingency was a false alarm for this shape of module.** The walk only
   catches public `ContractModel` definitions, so a service port never trips it. Worth
   knowing before pre-emptively editing two frozen tuples.
4. **`4433` is the `tests/orchestration` count, not the whole `tests/` tree.** The repo has
   `1496` more tests outside it (total `5929` at baseline). Both were run green.
5. **Production wiring does not exist to be wired into.** `adapters/api.py:1478
   bind_process_plan_approval_coordinator` reads `binding.verifier` from
   `_ADMISSION_PROVIDER`, and that provider is `None` in production
   (`set_plan_approval_admission_provider` is never called outside tests) — the function
   returns `None` and honestly skips. So R21 closes the *"no verifier implementation
   exists"* hole; the remaining hole is *"no admission provider is bound"*, which belongs
   to the launcher task, not here. `identity.ConfigOperatorVerifier()` (default path, zero
   arguments) is exactly what that future binding should hand over as `binding.verifier`.
6. **`autonomy/playbooks.py:117`'s lease actor never reaches a verifier** — it goes to a
   `Protocol` port whose documented route is the verifier-free `register_and_try_lease`.
   The survey flagged this as "check whether `lease:*` actors must also verify"; the answer
   at source is a firm no (§3).

---

## 9. Concerns / carried forward

1. **This closes R21 only.** The framework still cannot *start*: no launcher, no
   `plan_runner` production binding, no bound admission provider. A verifier existing is a
   precondition, not a boot. Do not read "R21 closed" as "the framework runs".
2. **The mechanism is weak by design and that must not be forgotten.** Anyone who can
   write `config/orchestration/operators.json` can approve plans. The docstring and the
   shipped `note` say so; if this system ever gains a second user or a remote surface, this
   module must be replaced, not extended.
3. **`_StubVerifier` in `test_adapters_api.py` still returns the wrong attribute.** I did
   not touch it (Phase-9 review-sealed, and it is adequate for the construction-only paths
   it exercises), but it remains a landmine for anyone who copies it into a path that
   actually decides. My source-level test would catch the *coordinator* side drifting; it
   would not catch someone copying that stub. A one-line fix to that stub is a reasonable
   follow-up in a task that owns the file.
4. **Re-reading config per `verify` is a deliberate trade.** If some future caller loops
   `verify` at high frequency (nothing does today — decisions are human-paced), the
   per-call read becomes visible. The fix then is an mtime-guarded cache, not an
   unconditional one; a start-up snapshot would silently keep a revoked operator alive.
5. **No console/UI surface for the allowlist.** Editing the file is the only way to change
   operators, and there is no endpoint that shows who is declared. That is arguably the
   right default (an editable-over-HTTP authority list would be worse), but it means a
   misconfiguration is only discoverable by reading the refusal message.

---

## 10. Review round 1 — three Minors closed (commit 2)

Verdict received: **Approved, zero Critical, zero Important.** The reviewer independently
drove a real coordinator + real Phase-2 admission service to a durable on-disk decision
row, a real `PLAN_APPROVED` event, a frozen `Plan` and a cold journal replay, and
confirmed the lease ruling at source in both directions. Three Minors, all closed.

### Fix 1 — `verified_by=` knob **dropped** (chose removal, not pinning)

`ConfigOperatorVerifier.__init__` no longer takes `verified_by`; the principal always
carries the module constant `VERIFIED_BY = "config-operator-allowlist"`.

Why removal over "keep it and pin it in a test": the parameter had **zero** callers and
nothing in the repo reads `.verified_by` at all, so it bought nothing — while allowing a
caller to write `verified_by="password"` or `"sso"` into a durable principal when no
password and no SSO existed. That is exactly the class of misrepresentation this module
exists to refuse, and a knob whose only reachable use is misuse should not exist. Pinning
it in a test would have preserved the misleading surface and merely documented that we
don't use it. A comment at the constructor states the reasoning so nobody re-adds it as a
"harmless" convenience.

Guarded by `test_verified_by_is_the_module_constant_and_is_not_configurable`, which
asserts the stamped value, asserts the constructor signature is exactly
`{self, allowlist_path}`, and asserts `ConfigOperatorVerifier(verified_by="password")`
raises `TypeError`.

### Fix 2 — the success path now asserts the **durable** row and a **cold replay**

Two changes, because the reviewer's point had two halves:

* `test_real_coordinator_decide_succeeds_for_an_allowlisted_actor` now reads the journal
  file back off disk: row kinds are exactly `["pending", "decision"]`, the decision row's
  `payload["actor_id"]` is the verified operator, and the row is re-validated through
  `ApprovalJournalRow` with `.verify()` so its `row_digest` recomputes.
* New `test_a_cold_coordinator_replays_the_verified_decision_off_the_journal`: the
  deciding coordinator is deleted (simulated process death), a **fresh** coordinator is
  built with `PlanApprovalCoordinator.replay(journal, …)` and a **fresh** admission fake,
  and the operator-signed approval comes back equal to the original — plus
  `len(fresh_admission.calls) == 1` (replay's idempotent re-submission yields exactly one
  terminal admission effect) and `list_pending() == ()`.

That is the strongest property the work has, and it is now pinned rather than incidental.

### Fix 3 — a repeated JSON key is **refused**, not last-won

`load_operator_allowlist` now parses with
`json.loads(text, object_pairs_hook=_object_pairs_no_duplicates)` and only then hands the
result to pydantic (`model_validate` instead of `model_validate_json` — pydantic's own
JSON parser has no duplicate-key hook and would silently last-win). A repeated key **at
any depth** raises `_DuplicateJsonKey`, surfaced as `OperatorAllowlistError` whose message
contains "ambiguous". A comment records *why* it is worth refusing something that is not a
hole: the effective list is still fully validated and exact-matched, but a human auditing
the file could read the first `operators` block and believe something false about who may
approve — and this file **is** the audit surface for approval authority.

`config/orchestration/operators.json` needed no change (it has no duplicates), so the
commit touches only `identity.py` and its test.

Guarded by `test_a_repeated_json_key_refuses_instead_of_last_winning`, parametrised over
four documents: the reviewer's exact `operators`-twice trap, its wildcard-shown /
real-id-served mirror, a duplicated `schema_version`, and a duplicated `actor_id` **inside
a row object** (the nested case the "exactly one `operators` key" phrasing would have
missed).

### Results after the fixes

```
$ python -m pytest tests/orchestration/test_operator_identity.py -q
65 passed in 1.24s                                  # 59 -> 65 (+6: 1 + 4 params + 1)

$ python -m pytest tests/orchestration/test_contract_completeness.py \
      tests/orchestration/test_phase9_registry_chain.py \
      tests/orchestration/test_approval_store.py tests/orchestration/test_approval_lease.py \
      tests/orchestration/test_dynamic_e2e.py -q
127 passed in 17.00s

$ python -m pytest tests/orchestration -q          # FOREGROUND, pre-commit
4498 passed in 317.61s (0:05:17)                    # 4433 baseline + 65 = 4498 exactly
```

`git diff --stat -- tests/orchestration/golden/` is still empty — no golden moved.

**Contamination note:** the coordinator warned that a sibling agent is concurrently
editing `guanlan_v2/orchestration/adapters/api.py` and its test for R22 and that my count
might be contaminated. At the moment this suite ran, `git status` over
`guanlan_v2/orchestration` and `tests/orchestration` showed **only my two files** —
no R22 edits were on disk, so `4498` is uncontaminated and reconciles exactly
(`4433 + 65`). I did not touch `adapters/api.py` or `test_adapters_api.py`.

---

## 11. Carried gaps for the launcher brief (recorded, NOT fixed here)

Both are the coordinator's, recorded verbatim in intent so the launcher task inherits them.

1. **There is no production producer of the actor material.** `plan_approval_console_kwargs`
   (`adapters/api.py:1496-1505`) passes only `plan_approval_coordinator` and never
   `plan_approval_actor`, so `console/api.py:999` would call `_resolve_actor(None)` and
   refuse. Nothing anywhere in the repo produces the string `human:ops`. Whatever the
   launcher injects as the actor material **must match the shipped declaration** in
   `config/orchestration/operators.json` — if the launcher invents a different id, every
   approval refuses with `OperatorNotAllowed` and the failure will look like a verifier
   bug. Related: `bind_process_plan_approval_coordinator` (`api.py:1478`) reads
   `binding.verifier` from `_ADMISSION_PROVIDER`, which is `None` in production; the
   launcher should hand over `ConfigOperatorVerifier()` (default path, zero arguments).
2. **A future `Lane0BootstrapService.admit_and_run` must route through
   `register_and_try_lease`, never `coord.decide(actor="lease:<id>")`.** The latter is now
   correctly refused by design (§3) and would look like a verifier bug to whoever writes
   it. The verifier-free lease path is the only sanctioned route for a lease-authorized
   admission; `playbooks.py:100-106` already documents it as such.
