# -*- coding: utf-8 -*-
"""Phase 9 · Task 11 — the measurable retirement gates for the three legacy entry points.

**This module removes NOTHING.** It neither deletes, disables, deprecates, warns on nor
reroutes a single legacy code path. Its whole purpose is to make a *future* removal
decision measurable and evidence-based: for each legacy entry point it freezes a
reviewed, digest-pinned description of **what would have to be true** before a removal
commit may be written — and that commit (outside this plan) must cite the green
:class:`~guanlan_v2.orchestration.adapters.contracts.RetirementReadinessReport` digest in
its message.

The three gated entry points and their replacements (spec §12.9 — 红线/并发/恢复/e2e 全绿
后才逐步下线旧入口; spec §12.8 — 预算/模型档位与旧入口隔离):

======================================  =====================================================
legacy entry point                      replacement
======================================  =====================================================
``console.report_subprocess``           orchestrated report lane (Phase 8 lane workers via kernel)
``swarm.load_preset_cli``               attested PRESET Plans (Phase 2 presets + Phase 8 catalog)
``research.loop_direct``                Phase 4 ``run_optimize`` via the 帷幄 adapter
======================================  =====================================================

**Name reconciliation (Task 1 ↔ Task 11).** Task 1 shipped
:func:`~guanlan_v2.orchestration.adapters.contracts.validate_retirement_readiness` — a
pure cross-object **checker** (``gate, report -> None``) that proves a report binds its
gate's digest and covers its criterion ids exactly. The brief's
``evaluate_retirement_gate`` is the complementary **builder**
(``gate, results, evaluated_at -> report``): it folds supplied criterion results into a
report and then calls the Task-1 checker on its own output. They are not two spellings
of one function — the builder produces what the checker verifies, and the builder is the
only place that can refuse a coverage mismatch *before* a report exists.

**Criteria order is semantic.** ``EntryPointRetirementGate.criteria`` is validated
unique-but-NOT-sorted, so the gate's own ``semantic_digest()`` is order-sensitive. The
instances below therefore fix ONE deliberate order — the reviewed plan's listing order
for each gate — and ``tests/orchestration/golden/phase9_retirement_gates_v1.json``
freezes it. Reordering the criteria is a reviewed contract change that must re-freeze the
golden by hand.

**Purity.** Every function here is pure: no file read, no subprocess, no network, no
clock. The criteria are *descriptions of evidence*; this module never goes looking for
that evidence. A caller (a human, or a future operational tool) supplies
:class:`RetirementCriterionResult` s and the evaluator only folds them — fail-closed:
``unavailable`` blocks exactly as hard as ``red``.

**Registers nothing.** ``EntryPointRetirementGate`` / ``RetirementReadinessReport`` are
already among the nine reviewed ``PHASE9_PUBLIC_MODELS`` sealed by Task 9's cumulative
registry chain node; this module declares no contract of its own (a public
``ContractModel`` defined here would need a Task-9 partition entry, and that chain is
sealed).

**Selector conventions.** ``evidence_selector`` is a concrete pointer whose shape follows
the ``evidence_kind``:

* ``pytest_suite`` — one or more whitespace-separated pytest paths / node ids;
* ``parity_fixture`` — the repo-relative path of the reviewed fixture file;
* ``operational_run_log`` — a ``run_record:<structured query>`` selector over the run
  records the operational lane emits;
* ``reviewed_artifact`` — a ``review:<slug>`` pointer to the human review record.

Some selectors are **forward references**: they name evidence Task 12 (the whole-framework
e2e + red-line regression suite) and the future parity fixtures will make resolvable. That
is by design — a gate describes a FUTURE condition, and its validity never depends on its
selectors resolving today. :data:`FORWARD_REFERENCE_SELECTORS` declares exactly which
tokens were unresolvable at authoring time, so a *typo* in a selector that should resolve
today fails loudly instead of hiding among them.
"""
from __future__ import annotations

from collections.abc import Iterable

from guanlan_v2.orchestration.adapters.contracts import (
    EntryPointRetirementGate,
    RetirementCriterion,
    RetirementCriterionResult,
    RetirementReadinessReport,
    validate_retirement_readiness,
)
from guanlan_v2.orchestration.digest import UtcDateTime, content_digest

__all__ = [
    "RetirementCoverageError",
    "default_retirement_gates",
    "evaluate_retirement_gate",
    "blocking_reasons",
    "FORWARD_REFERENCE_SELECTORS",
    "RETIREMENT_GATES_DIGEST",
]


class RetirementCoverageError(ValueError):
    """The supplied results do not cover a gate's criteria exactly.

    A ``ValueError`` subclass on purpose: the plan's contract for
    :func:`evaluate_retirement_gate` is "missing/extra ⇒ ``ValueError``", and a typed
    subclass lets a caller distinguish a coverage refusal from an unrelated validation
    error without weakening that contract.
    """


# =========================================================================== #
# The reviewed criteria                                                        #
# =========================================================================== #
# Two criteria recur across gates. They are defined ONCE and shared, so a given
# criterion id means exactly one thing everywhere it appears (proven by test).
_REDLINE_SUITE_GREEN = RetirementCriterion(
    criterion_id="redline-suite-green",
    description=(
        "spec §11 红线回归 is green end to end: the Phase-9 red-line regression suite "
        "passes in full — no LLM-reachable capability writes orders or signals; a shadow "
        "intent cannot be promoted in place; an unapproved DYNAMIC plan never executes "
        "and leaks no reservation; a planner failure without an explicit preset is an "
        "honest terminal failure (no silent fallback); factorlib drafts never "
        "auto-promote; no worker writes memory/skill/code; every degradation is badged; "
        "AUTO approval is rejected on every PlanSource; the replay boundary stays "
        "honestly UNAVAILABLE instead of zero-filled."
    ),
    evidence_kind="pytest_suite",
    evidence_selector="tests/orchestration/test_redline_regression.py",
)

_CONCURRENCY_RECOVERY_GREEN = RetirementCriterion(
    criterion_id="concurrency-recovery-green",
    description=(
        "并发/恢复 is green: the Phase-9 whole-framework kill/replay case plus the "
        "Phase-2 crash/replay barrier suite ids — a crash before a layer/UoW barrier "
        "exposes none of the batch, a replay after it exposes all of it, and a duplicate "
        "recovery applies each target exactly once."
    ),
    evidence_kind="pytest_suite",
    evidence_selector=(
        "tests/orchestration/test_phase9_e2e.py::test_recovery "
        "tests/orchestration/test_dag.py::"
        "test_crash_before_layer_commit_leaves_no_visible_artifact_then_replays "
        "tests/orchestration/test_dag.py::test_crash_after_layer_commit_resumes_next_layer "
        "tests/orchestration/test_eventstore.py::"
        "test_crash_before_layer_committed_batch_exposes_no_artifact_replay_after_exposes_all "
        "tests/orchestration/test_pool.py::"
        "test_crash_before_barrier_exposes_none_then_replay_after_commit_exposes_all"
    ),
)


# --------------------------------------------------------------------------- #
# 1. console.report_subprocess                                                  #
# --------------------------------------------------------------------------- #
# The legacy lane: console ``_spawn_bg`` -> ``_run_report_bg`` -> the blocking
# ``_call_buddy_report`` engine subprocess (and its ETF twin ``_run_etf_report_bg``).
_CONSOLE_GATE = EntryPointRetirementGate(
    entry_point="console.report_subprocess",
    replacement="orchestrated report lane (Phase 8 lane workers via kernel)",
    criteria=(
        _REDLINE_SUITE_GREEN,
        _CONCURRENCY_RECOVERY_GREEN,
        RetirementCriterion(
            criterion_id="report-parity",
            description=(
                "Report parity on a fixed (code, as_of): ONE orchestrated report Artifact "
                "covers the legacy subprocess report's reviewed section list, with every "
                "number anchored/sourced (no unsourced figure, no section silently "
                "dropped). The reviewed comparison fixture's digest is recorded with the "
                "verdict."
            ),
            evidence_kind="parity_fixture",
            evidence_selector="tests/orchestration/fixtures/console_report_parity_v1.json",
        ),
        RetirementCriterion(
            criterion_id="production-streak",
            description=(
                "At least 10 CONSECUTIVE orchestrated production report runs completed "
                "with ZERO fallback to the subprocess lane, evidenced by the run-event "
                "digests of those runs (a single fallback resets the streak)."
            ),
            evidence_kind="operational_run_log",
            evidence_selector=(
                "run_record:lane=console.report;window=consecutive_10;"
                "fallback_to_subprocess=0;evidence=run_event_digests"
            ),
        ),
        RetirementCriterion(
            criterion_id="console-consumes-kernel",
            description=(
                "Reviewed: the existing console report card renders the orchestrated "
                "result (kernel-produced Artifact) — the EXISTING UI is filled with real "
                "data, never rebuilt, and no card is left reading the subprocess lane."
            ),
            evidence_kind="reviewed_artifact",
            evidence_selector="review:console-report-card-renders-orchestrated-results",
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 2. swarm.load_preset_cli                                                      #
# --------------------------------------------------------------------------- #
# The legacy lane: engine ``swarm.loader.load_preset`` reached from the engine-root
# ``cli.py`` / ``tui.py`` consumers; frozen legacy semantics live in
# docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md.
_SWARM_GATE = EntryPointRetirementGate(
    entry_point="swarm.load_preset_cli",
    replacement="attested PRESET Plans (Phase 2 presets + Phase 8 catalog)",
    criteria=(
        RetirementCriterion(
            criterion_id="deep-dive-equivalence-green",
            description=(
                "The attested stock-deep-dive execution-equivalence suite is green: the "
                "legacy static graph's hard/soft dependency terminal states, degraded "
                "paths and crash/replay behaviour are reproduced by the attested PRESET "
                "Plan through the kernel."
            ),
            evidence_kind="pytest_suite",
            evidence_selector="tests/orchestration/test_engine_equivalence.py",
        ),
        RetirementCriterion(
            criterion_id="radar-presets-attested",
            description=(
                "The two remaining radar presets — mainline-radar and overseas-radar — "
                "are mapped through migrate_legacy_graph-family evidence onto attested "
                "Plans with EQUIVALENT dependency terminal states (hard-dep death and "
                "soft-dep degradation preserved node for node)."
            ),
            evidence_kind="parity_fixture",
            evidence_selector="tests/orchestration/fixtures/radar_presets_attestation_v1.json",
        ),
        RetirementCriterion(
            criterion_id="cli-kernel-path",
            description=(
                "Reviewed: every documented CLI/TUI invocation either routes through the "
                "kernel or carries a reviewed sunset notice — no invocation is left "
                "silently on load_preset with no successor and no notice."
            ),
            evidence_kind="reviewed_artifact",
            evidence_selector="review:swarm-cli-routes-via-kernel-or-sunset-notice",
        ),
        _REDLINE_SUITE_GREEN,
        _CONCURRENCY_RECOVERY_GREEN,
    ),
)


# --------------------------------------------------------------------------- #
# 3. research.loop_direct                                                       #
# --------------------------------------------------------------------------- #
# The legacy lane: ``POST /research/loop/start`` -> ``run_research_loop`` called directly.
_RESEARCH_GATE = EntryPointRetirementGate(
    entry_point="research.loop_direct",
    replacement="Phase 4 run_optimize via the 帷幄 (weiwo) adapter",
    criteria=(
        RetirementCriterion(
            criterion_id="factor-adapter-regression-green",
            description=(
                "The Phase-4 factor-research adapter regression is green: "
                "run_research_optimize is decision-equivalent to run_research_loop "
                "(round counts, gate verdicts, product channel, lesson writes and honest "
                "stops all pairwise-equal) over the reviewed scenario matrix."
            ),
            evidence_kind="pytest_suite",
            evidence_selector="tests/test_research_optimize_adapter.py",
        ),
        RetirementCriterion(
            criterion_id="draft-parity",
            description=(
                "Draft parity: the same candidate inputs produce factorlib DRAFTS with "
                "equal recipe digests through the old loop and the new adapter path — "
                "same draft-only landing, no promotion on either side."
            ),
            evidence_kind="parity_fixture",
            evidence_selector="tests/orchestration/fixtures/research_draft_parity_v1.json",
        ),
        RetirementCriterion(
            criterion_id="stagnation-honesty-parity",
            description=(
                "The stagnation guard and honest termination behave identically on both "
                "paths: an identical candidate twice aborts honestly with the same error, "
                "a stall retry that progresses continues, a parameter-structure change is "
                "NOT a stall, and a propose failure terminates honestly before the "
                "optimizer starts."
            ),
            evidence_kind="pytest_suite",
            evidence_selector=(
                "tests/test_research_optimize_adapter.py::"
                "test_stall_identical_twice_honest_stop_equivalence "
                "tests/test_research_optimize_adapter.py::"
                "test_stall_retry_then_progress_equivalence "
                "tests/test_research_optimize_adapter.py::"
                "test_param_structure_change_is_not_a_stall_equivalence "
                "tests/test_research_optimize_adapter.py::"
                "test_propose_failure_honest_stop_equivalence"
            ),
        ),
        RetirementCriterion(
            criterion_id="budget-isolation",
            description=(
                "spec §12.8 budget isolation holds: the old direct loop and kernel runs "
                "never share or duplicate a reservation — the legacy loop holds no kernel "
                "reservation at all, a kernel run's reservations settle exactly once "
                "against the ledger, and an unapproved kernel plan leaks none."
            ),
            evidence_kind="pytest_suite",
            # RE-FROZEN by Task 12 (the Task-11 review carry): the previous selector
            # named only the kernel-side ledger suites, so the criterion's OWN first
            # clause — "the legacy loop holds no kernel reservation at all" — was
            # clearable without anyone ever checking `research/loop.py`. The bespoke
            # old-loop-vs-kernel test now leads the selector; the kernel-side suites
            # stay as the reservation-semantics evidence they always were.
            evidence_selector=(
                "tests/orchestration/test_redline_regression.py::"
                "test_budget_isolation_old_loop_holds_no_kernel_reservation "
                "tests/orchestration/test_budget.py "
                "tests/orchestration/test_budget_ledger.py "
                "tests/orchestration/test_weiwo_adapter.py::"
                "test_unapproved_dynamic_never_executes"
            ),
        ),
        _REDLINE_SUITE_GREEN,
    ),
)


#: the three reviewed gates, in the reviewed order. Frozen instances — never mutated.
_DEFAULT_GATES: tuple[EntryPointRetirementGate, ...] = (
    _CONSOLE_GATE,
    _SWARM_GATE,
    _RESEARCH_GATE,
)

#: The selector tokens that were NOT resolvable when these gates were frozen, because
#: they name evidence a later task produces. ``test_redline_regression.py`` and
#: ``test_phase9_e2e.py::test_recovery`` are Task 12's two suites; the three parity
#: fixtures are produced with their reviewed comparison verdicts. Declaring them keeps a
#: *typo* in a should-resolve-today selector from hiding among the legitimate futures.
#: PRUNED by Task 12: the two pytest forward references
#: (``test_redline_regression.py`` and ``test_phase9_e2e.py::test_recovery``) now
#: RESOLVE on disk, so declaring them would be a stale entry masking a real typo. Only
#: the three parity fixtures remain — a controller-ruled POST-PHASE carry owned by
#: whoever runs the parity comparison before a removal commit. This frozenset is NOT
#: part of the gate digest, so pruning it needs no golden re-freeze.
FORWARD_REFERENCE_SELECTORS: frozenset[str] = frozenset({
    "tests/orchestration/fixtures/console_report_parity_v1.json",
    "tests/orchestration/fixtures/radar_presets_attestation_v1.json",
    "tests/orchestration/fixtures/research_draft_parity_v1.json",
})

#: the order-sensitive digest of the whole reviewed gate set; pinned by the golden.
RETIREMENT_GATES_DIGEST = content_digest([g.semantic_digest() for g in _DEFAULT_GATES])


def default_retirement_gates() -> tuple[EntryPointRetirementGate, ...]:
    """The three reviewed retirement gates, in the reviewed order.

    The returned tuple and its members are frozen contract records, so handing out the
    module-level instances is safe (no caller can mutate them). Their digests are pinned
    by ``tests/orchestration/golden/phase9_retirement_gates_v1.json``: any edit to a
    criterion — text, kind, selector, or ORDER — silently fails that golden until it is
    re-frozen by review.
    """
    return _DEFAULT_GATES


def evaluate_retirement_gate(
    gate: EntryPointRetirementGate,
    *,
    results: Iterable[RetirementCriterionResult],
    evaluated_at: UtcDateTime,
) -> RetirementReadinessReport:
    """Fold supplied criterion results into a :class:`RetirementReadinessReport`.

    Pure and fail-closed. The evaluator performs **no I/O of any kind**: it never looks
    for the evidence a criterion describes, it only folds the results a caller supplies.

    * ``ready`` is ``True`` **iff** every result is ``green``; any ``red`` OR
      ``unavailable`` result forces ``ready=False`` (an absent verdict is never
      optimistically treated as a pass).
    * the results must cover the gate's criterion ids **exactly** — a missing, extra or
      duplicated result raises :class:`RetirementCoverageError` (a ``ValueError``).
    * the results are sorted by ``criterion_id`` (the report contract requires it), so
      the report's semantic digest is independent of the input order; ``evaluated_at`` is
      audit-only, so the digest a future removal commit cites is wall-clock free.

    Before returning, the report is passed through the Task-1 cross-object checker
    :func:`validate_retirement_readiness` — the report itself carries only
    ``gate_digest``, so exact coverage + digest binding are provable only there.
    """
    ordered = tuple(results)
    ids = [r.criterion_id for r in ordered]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        raise RetirementCoverageError(
            f"results contain a duplicate criterion_id for gate {gate.entry_point!r}: "
            f"{duplicates}"
        )
    supplied = frozenset(ids)
    missing = sorted(gate.criterion_ids - supplied)
    extra = sorted(supplied - gate.criterion_ids)
    if missing or extra:
        raise RetirementCoverageError(
            f"results must cover the criteria of gate {gate.entry_point!r} exactly "
            f"(missing={missing}, extra={extra})"
        )

    sorted_results = tuple(sorted(ordered, key=lambda r: r.criterion_id))
    report = RetirementReadinessReport(
        gate_digest=gate.semantic_digest(),
        entry_point=gate.entry_point,
        results=sorted_results,
        ready=all(r.status == "green" for r in sorted_results),
        evaluated_at=evaluated_at,
    )
    validate_retirement_readiness(gate, report)
    return report


def blocking_reasons(
    report: RetirementReadinessReport,
) -> tuple[tuple[str, str], ...]:
    """The ``(criterion_id, reason)`` pairs blocking readiness, in report order.

    Empty **iff** ``report.ready`` — the non-green half of the contract's biconditional,
    surfaced for a human reading a not-ready verdict. Pure; a projection, not a decision.
    """
    return tuple(
        (r.criterion_id, r.reason or "")
        for r in report.results
        if r.status != "green"
    )
