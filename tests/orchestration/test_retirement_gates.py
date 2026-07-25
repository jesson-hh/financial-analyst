# -*- coding: utf-8 -*-
"""Phase 9 · Task 11 — measurable retirement gates for the three legacy entry points.

**This phase removes NOTHING.** The gates exist to make a FUTURE removal decision
measurable and evidence-based: each legacy entry point carries a frozen, digest-pinned
description of *what would have to be true* before a removal commit may be written, and
that commit (outside this plan) must cite the green
:class:`RetirementReadinessReport` digest in its message.

Three test families:

* **Golden pin** — ``default_retirement_gates()`` is compared field-for-field AND
  digest-for-digest against the HAND-FROZEN
  ``golden/phase9_retirement_gates_v1.json``. The test RECOMPUTES-AND-COMPARES; it
  never regenerates. Editing a criterion (or reordering the criteria — the gate's own
  digest is order-sensitive because ``EntryPointRetirementGate.criteria`` is
  unique-but-NOT-sorted) silently fails the golden.
* **Evaluator matrix** — ``evaluate_retirement_gate`` folds supplied
  :class:`RetirementCriterionResult` s into a report: all-green ⇒ ready; one red or one
  unavailable ⇒ NOT ready with the reasons surfaced (fail-closed); a missing / extra /
  duplicated result ⇒ typed ``RetirementCoverageError`` (a ``ValueError``). Purity is
  proven structurally (an AST scan: the module imports no I/O-capable module and calls
  no I/O builtin) and behaviourally (the same results in any order produce a
  digest-identical report; ``evaluated_at`` is audit-only).
  Every green path also calls the Task-1 external checker
  :func:`validate_retirement_readiness` — the report holds only ``gate_digest``, not
  the gate, so exact criterion-id coverage is ONLY provable through that checker.
* **Structural no-removal guard** — all three legacy seams still resolve, unmodified:
  console ``_spawn_bg`` → ``_run_report_bg`` → ``_call_buddy_report`` (+ the ETF twin),
  engine ``swarm.loader.load_preset`` with its ``cli.py``/``tui.py`` consumers (at the
  ENGINE PACKAGE ROOT, not under ``swarm/``), and
  ``POST /research/loop/start`` → ``run_research_loop``. ``guanlan_v2/console/api.py``
  is inspected as SOURCE TEXT only (it is owned by a concurrent session and its
  background seams are nested inside the router factory — never importable in
  isolation); this mirrors the proven Task-0 handoff formulation.

Run: ``pytest tests/orchestration/test_retirement_gates.py -v``
"""
from __future__ import annotations

import ast
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import adapters as adapters_pkg
from guanlan_v2.orchestration.adapters import chain as ch
from guanlan_v2.orchestration.adapters import retirement as rt
from guanlan_v2.orchestration.adapters.contracts import (
    EntryPointRetirementGate,
    RetirementCriterion,
    RetirementCriterionResult,
    RetirementReadinessReport,
    validate_retirement_readiness,
)
from guanlan_v2.orchestration.digest import ContractModel, content_digest

TESTS_DIR = Path(__file__).resolve().parent
GOLDEN = TESTS_DIR / "golden" / "phase9_retirement_gates_v1.json"
ADAPTERS_DIR = Path(adapters_pkg.__file__).resolve().parent
REPO_ROOT = ADAPTERS_DIR.parent.parent.parent

AT = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

#: the three legacy entry points, in the reviewed gate order.
ENTRY_POINTS = (
    "console.report_subprocess",
    "swarm.load_preset_cli",
    "research.loop_direct",
)


def _load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _green(criterion_id: str, seed: str = "a") -> RetirementCriterionResult:
    """A synthetic green result. The evidence digest is OBVIOUSLY synthetic (a repeated
    hex nibble) — these fixtures prove the folding logic, never real evidence."""
    return RetirementCriterionResult(
        criterion_id=criterion_id,
        status="green",
        evidence_digest=content_digest({"synthetic": criterion_id, "seed": seed}),
    )


def _all_green(gate: EntryPointRetirementGate) -> tuple[RetirementCriterionResult, ...]:
    return tuple(_green(c.criterion_id) for c in gate.criteria)


def _gate(entry_point: str) -> EntryPointRetirementGate:
    for g in rt.default_retirement_gates():
        if g.entry_point == entry_point:
            return g
    raise AssertionError(f"no gate for {entry_point}")


# =========================================================================== #
# Shape of the three reviewed gates                                            #
# =========================================================================== #
def test_default_retirement_gates_are_the_three_reviewed_instances():
    gates = rt.default_retirement_gates()
    assert isinstance(gates, tuple) and len(gates) == 3
    assert tuple(g.entry_point for g in gates) == ENTRY_POINTS
    for g in gates:
        assert isinstance(g, EntryPointRetirementGate)
        assert g.schema_version == "1"
        assert g.removal_allowed_without_gate is False
        assert g.criteria, "a gate with no criteria would permit removal for free"


def test_default_retirement_gates_cover_the_whole_legacy_entry_point_vocabulary():
    """No legacy entry point may lack a gate — otherwise it could be removed ungated."""
    from guanlan_v2.orchestration.adapters import contracts as ct
    import typing

    vocabulary = frozenset(typing.get_args(ct.LegacyEntryPoint))
    assert vocabulary == frozenset(ENTRY_POINTS)
    assert frozenset(g.entry_point for g in rt.default_retirement_gates()) == vocabulary


def test_default_retirement_gates_returns_equal_value_on_every_call():
    a, b = rt.default_retirement_gates(), rt.default_retirement_gates()
    assert [g.semantic_digest() for g in a] == [g.semantic_digest() for g in b]


def test_criterion_ids_are_unique_within_each_gate():
    for g in rt.default_retirement_gates():
        ids = [c.criterion_id for c in g.criteria]
        assert len(set(ids)) == len(ids), f"{g.entry_point} has a duplicate criterion id"


def test_criteria_shared_across_gates_are_byte_identical():
    """``redline-suite-green`` / ``concurrency-recovery-green`` recur across gates; a
    shared id that meant two different things would make the gates incomparable."""
    by_id: dict[str, set[str]] = {}
    for g in rt.default_retirement_gates():
        for c in g.criteria:
            by_id.setdefault(c.criterion_id, set()).add(c.semantic_digest())
    for cid, digests in by_id.items():
        assert len(digests) == 1, f"criterion {cid} has divergent definitions: {digests}"
    # and the two shared ids really are shared (the reviewed instances say so).
    shared = {cid for cid, _ in by_id.items()
              if sum(any(c.criterion_id == cid for c in g.criteria)
                     for g in rt.default_retirement_gates()) > 1}
    assert shared == {"redline-suite-green", "concurrency-recovery-green"}


def test_every_criterion_carries_a_closed_evidence_kind_and_a_selector():
    kinds = {"pytest_suite", "parity_fixture", "reviewed_artifact", "operational_run_log"}
    for g in rt.default_retirement_gates():
        for c in g.criteria:
            assert isinstance(c, RetirementCriterion)
            assert c.evidence_kind in kinds
            assert c.evidence_selector.strip()
            assert c.description.strip()


# =========================================================================== #
# Golden pin — recompute-and-compare, never regenerate                         #
# =========================================================================== #
def test_golden_file_exists_and_declares_its_hand_frozen_provenance():
    assert GOLDEN.exists(), f"missing hand-frozen golden: {GOLDEN}"
    doc = _load_golden()
    assert doc["golden_id"] == "phase9_retirement_gates_v1"
    assert doc["algorithm"] == "sha256+cjson-v1"
    assert "HAND-FROZEN" in doc["description"]
    assert len(doc["gates"]) == 3


def test_default_gates_match_the_frozen_golden_field_for_field():
    doc = _load_golden()
    gates = rt.default_retirement_gates()
    assert [g.entry_point for g in gates] == [e["entry_point"] for e in doc["gates"]]
    for gate, entry in zip(gates, doc["gates"]):
        assert gate.replacement == entry["replacement"]
        assert entry["removal_allowed_without_gate"] is False
        assert len(gate.criteria) == len(entry["criteria"]), (
            f"{gate.entry_point}: criterion count drifted from the golden"
        )
        # order-sensitive on purpose: the gate's own digest folds criteria in order.
        for got, want in zip(gate.criteria, entry["criteria"]):
            assert got.criterion_id == want["criterion_id"]
            assert got.description == want["description"]
            assert got.evidence_kind == want["evidence_kind"]
            assert got.evidence_selector == want["evidence_selector"]
            assert got.semantic_digest() == want["semantic_digest"]


def test_default_gates_semantic_digests_match_the_frozen_golden():
    doc = _load_golden()
    for gate, entry in zip(rt.default_retirement_gates(), doc["gates"]):
        assert gate.semantic_digest() == entry["semantic_digest"], (
            f"gate {gate.entry_point} digest drifted — a reviewed contract change must "
            f"re-freeze the golden by hand"
        )


def test_gates_digest_matches_the_frozen_golden():
    doc = _load_golden()
    recomputed = content_digest(
        [g.semantic_digest() for g in rt.default_retirement_gates()]
    )
    assert recomputed == doc["gates_digest"]
    assert rt.RETIREMENT_GATES_DIGEST == doc["gates_digest"]


def test_editing_a_criterion_silently_fails_the_golden(monkeypatch):
    """Invariant 1, proven — not asserted. Patch one criterion's description and the
    golden comparison must go red."""
    doc = _load_golden()
    original = rt.default_retirement_gates()
    gate = original[0]
    edited_criteria = list(gate.criteria)
    edited_criteria[0] = edited_criteria[0].model_copy(
        update={"description": edited_criteria[0].description + " (tampered)"}
    )
    tampered = gate.model_copy(update={"criteria": tuple(edited_criteria)})

    monkeypatch.setattr(
        rt, "default_retirement_gates", lambda: (tampered,) + original[1:]
    )
    assert tampered.semantic_digest() != doc["gates"][0]["semantic_digest"]
    with pytest.raises(AssertionError):
        test_default_gates_semantic_digests_match_the_frozen_golden()
    with pytest.raises(AssertionError):
        test_default_gates_match_the_frozen_golden_field_for_field()
    with pytest.raises(AssertionError):
        test_gates_digest_matches_the_frozen_golden()


def test_reordering_criteria_changes_the_gate_digest(monkeypatch):
    """``EntryPointRetirementGate.criteria`` is unique-but-NOT-sorted, so the gate's own
    digest IS order-sensitive: the golden fixes ONE deliberate criteria order."""
    original = rt.default_retirement_gates()
    gate = original[0]
    assert len(gate.criteria) >= 2
    swapped = list(gate.criteria)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    reordered = gate.model_copy(update={"criteria": tuple(swapped)})
    assert reordered.criterion_ids == gate.criterion_ids     # same id SET
    assert reordered.semantic_digest() != gate.semantic_digest()  # different DIGEST

    monkeypatch.setattr(
        rt, "default_retirement_gates", lambda: (reordered,) + original[1:]
    )
    with pytest.raises(AssertionError):
        test_default_gates_semantic_digests_match_the_frozen_golden()


def test_golden_criteria_order_is_recorded_as_deliberate():
    doc = _load_golden()
    assert doc["criteria_order_is_semantic"] is True


# =========================================================================== #
# Evaluator matrix                                                             #
# =========================================================================== #
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_all_green_is_ready_and_binds_the_gate(entry_point):
    gate = _gate(entry_point)
    report = rt.evaluate_retirement_gate(
        gate, results=_all_green(gate), evaluated_at=AT
    )
    assert isinstance(report, RetirementReadinessReport)
    assert report.ready is True
    assert report.entry_point == gate.entry_point
    assert report.gate_digest == gate.semantic_digest()
    assert report.evaluated_at == AT
    ids = [r.criterion_id for r in report.results]
    assert ids == sorted(ids), "the report contract requires criterion_id-sorted results"
    # THE coverage proof — the report holds only gate_digest, so exact criterion-id
    # coverage is provable ONLY through the Task-1 external checker.
    validate_retirement_readiness(gate, report)


@pytest.mark.parametrize("bad_status", ["red", "unavailable"])
def test_one_non_green_is_not_ready_and_surfaces_the_reason(bad_status):
    """Fail-closed: an ``unavailable`` criterion is as blocking as a ``red`` one."""
    gate = _gate("console.report_subprocess")
    results = list(_all_green(gate))
    blocked_id = results[0].criterion_id
    results[0] = RetirementCriterionResult(
        criterion_id=blocked_id, status=bad_status, reason=f"synthetic {bad_status}"
    )
    report = rt.evaluate_retirement_gate(gate, results=tuple(results), evaluated_at=AT)
    assert report.ready is False
    blocking = [r for r in report.results if r.status != "green"]
    assert [r.criterion_id for r in blocking] == [blocked_id]
    assert blocking[0].reason == f"synthetic {bad_status}"
    assert blocking[0].evidence_digest is None
    validate_retirement_readiness(gate, report)          # still covers exactly
    assert rt.blocking_reasons(report) == ((blocked_id, f"synthetic {bad_status}"),)


def test_all_unavailable_is_not_ready():
    gate = _gate("research.loop_direct")
    results = tuple(
        RetirementCriterionResult(
            criterion_id=c.criterion_id, status="unavailable", reason="no evidence yet"
        )
        for c in gate.criteria
    )
    report = rt.evaluate_retirement_gate(gate, results=results, evaluated_at=AT)
    assert report.ready is False
    assert len(rt.blocking_reasons(report)) == len(gate.criteria)


def test_missing_result_is_a_typed_coverage_error():
    gate = _gate("swarm.load_preset_cli")
    partial = _all_green(gate)[:-1]
    with pytest.raises(rt.RetirementCoverageError) as exc:
        rt.evaluate_retirement_gate(gate, results=partial, evaluated_at=AT)
    assert isinstance(exc.value, ValueError)          # the brief's ValueError contract
    assert "missing" in str(exc.value)


def test_extra_result_is_a_typed_coverage_error():
    gate = _gate("swarm.load_preset_cli")
    extra = _all_green(gate) + (_green("not-a-criterion-of-this-gate"),)
    with pytest.raises(rt.RetirementCoverageError) as exc:
        rt.evaluate_retirement_gate(gate, results=extra, evaluated_at=AT)
    assert "extra" in str(exc.value)


def test_duplicate_result_is_a_typed_coverage_error():
    gate = _gate("swarm.load_preset_cli")
    dupe = _all_green(gate) + (_green(gate.criteria[0].criterion_id, seed="b"),)
    with pytest.raises(rt.RetirementCoverageError) as exc:
        rt.evaluate_retirement_gate(gate, results=dupe, evaluated_at=AT)
    assert "duplicate" in str(exc.value)


def test_empty_results_is_a_typed_coverage_error():
    gate = _gate("research.loop_direct")
    with pytest.raises(rt.RetirementCoverageError):
        rt.evaluate_retirement_gate(gate, results=(), evaluated_at=AT)


def test_results_from_another_gate_are_a_coverage_error():
    gate = _gate("console.report_subprocess")
    other = _gate("research.loop_direct")
    with pytest.raises(rt.RetirementCoverageError):
        rt.evaluate_retirement_gate(gate, results=_all_green(other), evaluated_at=AT)


def test_result_order_does_not_change_the_report_digest():
    gate = _gate("console.report_subprocess")
    forward = _all_green(gate)
    backward = tuple(reversed(forward))
    a = rt.evaluate_retirement_gate(gate, results=forward, evaluated_at=AT)
    b = rt.evaluate_retirement_gate(gate, results=backward, evaluated_at=AT)
    assert a.semantic_digest() == b.semantic_digest()


def test_evaluated_at_is_audit_only_so_the_cited_digest_is_wall_clock_free():
    """The future removal commit cites a readiness digest — it must not change just
    because the gate was evaluated a second later."""
    gate = _gate("console.report_subprocess")
    results = _all_green(gate)
    a = rt.evaluate_retirement_gate(gate, results=results, evaluated_at=AT)
    b = rt.evaluate_retirement_gate(
        gate, results=results, evaluated_at=AT + timedelta(seconds=1)
    )
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_evaluator_never_flips_ready_on_a_tampered_gate():
    """A gate whose criteria were edited yields a report bound to the EDITED digest, so
    the checker refuses to bind it to the reviewed gate — a tampered gate cannot borrow
    a green report."""
    reviewed = _gate("console.report_subprocess")
    tampered_criteria = list(reviewed.criteria)[:1]
    tampered = reviewed.model_copy(update={"criteria": tuple(tampered_criteria)})
    report = rt.evaluate_retirement_gate(
        tampered, results=_all_green(tampered), evaluated_at=AT
    )
    assert report.ready is True
    with pytest.raises(ValueError):
        validate_retirement_readiness(reviewed, report)


# =========================================================================== #
# Purity — no I/O of any kind                                                  #
# =========================================================================== #
_FORBIDDEN_IMPORTS = frozenset({
    "os", "io", "sys", "json", "pathlib", "subprocess", "socket", "shutil",
    "tempfile", "sqlite3", "asyncio", "threading", "urllib", "http", "requests",
    "httpx", "pickle", "importlib", "csv", "configparser", "logging",
})
_FORBIDDEN_CALLS = frozenset({"open", "exec", "eval", "compile", "__import__", "input"})


def test_retirement_module_imports_nothing_io_capable():
    tree = ast.parse(Path(rt.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            imported.add((node.module or "").split(".")[0])
    leaked = imported & _FORBIDDEN_IMPORTS
    assert not leaked, f"retirement.py must be pure — it imports {sorted(leaked)}"


def test_retirement_module_calls_no_io_builtin():
    tree = ast.parse(Path(rt.__file__).read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
    leaked = called & _FORBIDDEN_CALLS
    assert not leaked, f"retirement.py must be pure — it calls {sorted(leaked)}"
    for io_ish in ("read_text", "write_text", "read_bytes", "write_bytes", "run",
                   "urlopen", "get", "post", "connect"):
        assert io_ish not in called, f"retirement.py must be pure — it calls {io_ish}()"


# =========================================================================== #
# Registry / firewall bindings — Phase 9 registers nothing new here            #
# =========================================================================== #
def test_the_gate_and_report_are_already_registered_phase9_payloads():
    """Task 9 owns the cumulative registry and already registered both contracts, so
    Task 11 registers nothing (and must never re-open the sealed chain)."""
    names = {m.__name__ for m in ch.PHASE9_PUBLIC_MODELS}
    assert "EntryPointRetirementGate" in names
    assert "RetirementReadinessReport" in names


def test_retirement_module_defines_no_new_contract_model():
    """A public ContractModel defined HERE would need a Task-9 partition entry — which
    is sealed. This module builds instances; it declares no contract."""
    defined = [
        obj for obj in vars(rt).values()
        if isinstance(obj, type)
        and issubclass(obj, ContractModel)
        and obj.__module__ == rt.__name__
    ]
    assert defined == [], f"retirement.py must declare no ContractModel: {defined}"


# =========================================================================== #
# Selector hygiene — forward references are declared, typos are not            #
# =========================================================================== #
def _selector_tokens(kind: str) -> list[tuple[str, str, str]]:
    out = []
    for g in rt.default_retirement_gates():
        for c in g.criteria:
            if c.evidence_kind != kind:
                continue
            for token in c.evidence_selector.split():
                out.append((g.entry_point, c.criterion_id, token))
    return out


def _resolves(token: str) -> bool:
    path, _, node = token.partition("::")
    target = REPO_ROOT / path
    if not target.is_file():
        return False
    if not node:
        return True
    tree = ast.parse(target.read_text(encoding="utf-8"))
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == node
        for n in tree.body
    )


def test_every_pytest_selector_either_resolves_today_or_is_a_declared_forward_reference():
    """A criterion describes a FUTURE condition, so a selector naming a not-yet-written
    suite is legitimate — but it must be declared, so a typo in a selector that *should*
    resolve today fails loudly."""
    for entry_point, cid, token in _selector_tokens("pytest_suite"):
        assert _resolves(token) or token in rt.FORWARD_REFERENCE_SELECTORS, (
            f"{entry_point}/{cid}: selector {token!r} neither resolves on disk nor is "
            f"declared in FORWARD_REFERENCE_SELECTORS"
        )


def test_every_parity_fixture_selector_is_a_declared_forward_reference_or_resolves():
    for entry_point, cid, token in _selector_tokens("parity_fixture"):
        assert (REPO_ROOT / token).is_file() or token in rt.FORWARD_REFERENCE_SELECTORS, (
            f"{entry_point}/{cid}: parity fixture {token!r} is undeclared"
        )


def test_forward_reference_selectors_are_all_actually_referenced():
    """No stale entry may hide in the declared set (it would mask a real typo)."""
    used = {t for _, _, t in _selector_tokens("pytest_suite")}
    used |= {t for _, _, t in _selector_tokens("parity_fixture")}
    assert rt.FORWARD_REFERENCE_SELECTORS <= used, (
        "FORWARD_REFERENCE_SELECTORS contains tokens no criterion names: "
        f"{sorted(rt.FORWARD_REFERENCE_SELECTORS - used)}"
    )


def test_no_pytest_selector_remains_a_forward_reference():
    """Task 12 landed both suites, so the two pytest forward references were PRUNED —
    only the three parity fixtures (a post-phase carry) remain declared."""
    assert {t for t in rt.FORWARD_REFERENCE_SELECTORS
            if t.endswith(".py") or "::" in t} == set()
    assert rt.FORWARD_REFERENCE_SELECTORS == {
        "tests/orchestration/fixtures/console_report_parity_v1.json",
        "tests/orchestration/fixtures/radar_presets_attestation_v1.json",
        "tests/orchestration/fixtures/research_draft_parity_v1.json",
    }
    # …and the two Task-12 suites really do resolve now (the reason for the prune).
    assert _resolves("tests/orchestration/test_redline_regression.py")
    assert _resolves("tests/orchestration/test_phase9_e2e.py::test_recovery")


# =========================================================================== #
# Structural no-removal guard — NOTHING is removed in this phase               #
# =========================================================================== #
def _read(path: Path) -> str:
    assert path.is_file(), f"legacy entry-point file removed: {path}"
    return path.read_text(encoding="utf-8")


def test_no_removal_console_report_subprocess_seams_still_present():
    # console/api.py is FOREIGN-DIRTY (a concurrent session owns uncommitted edits) and
    # its background seams are nested inside the router factory — inspect SOURCE TEXT,
    # never import the router. Mirrors the proven Task-0 handoff formulation.
    src = _read(REPO_ROOT / "guanlan_v2" / "console" / "api.py")
    for seam in ("def _call_buddy_report", "def _spawn_bg", "def _run_report_bg",
                 "def _run_etf_report_bg"):
        assert seam in src, f"console legacy seam removed: {seam}"
    assert "subprocess" in src, "the legacy subprocess report lane is gone"


def test_no_removal_swarm_load_preset_and_its_cli_tui_consumers():
    from financial_analyst.swarm import loader as swarm_loader

    assert callable(swarm_loader.load_preset)
    # the consumers live at the ENGINE PACKAGE ROOT (not under swarm/) — Task-0 fix.
    engine_root = Path(swarm_loader.__file__).resolve().parents[1]
    for consumer in ("cli.py", "tui.py"):
        assert "load_preset" in _read(engine_root / consumer), (
            f"engine {consumer} no longer consumes load_preset"
        )


def test_no_removal_research_loop_start_route_and_runner():
    rloop = importlib.import_module("guanlan_v2.research.loop")
    assert callable(rloop.run_research_loop)
    api_src = _read(REPO_ROOT / "guanlan_v2" / "research" / "api.py")
    assert "/research/loop/start" in api_src
    assert "run_research_loop" in api_src


def test_the_no_removal_guard_covers_every_gated_entry_point():
    """A new gate without a matching structural guard would let its entry point be
    removed while every test stayed green."""
    guarded = {
        "console.report_subprocess": test_no_removal_console_report_subprocess_seams_still_present,
        "swarm.load_preset_cli": test_no_removal_swarm_load_preset_and_its_cli_tui_consumers,
        "research.loop_direct": test_no_removal_research_loop_start_route_and_runner,
    }
    assert set(guarded) == {g.entry_point for g in rt.default_retirement_gates()}


def test_nothing_in_this_phase_marks_a_legacy_entry_point_deprecated():
    """NO REMOVAL, NO DEPRECATION MARKS: the gates permit a future decision; they do not
    warn, reroute or disable anything today."""
    src = Path(rt.__file__).read_text(encoding="utf-8")
    for marker in ("DeprecationWarning", "PendingDeprecationWarning", "warnings.warn",
                   "@deprecated"):
        assert marker not in src, f"retirement.py must not deprecate anything: {marker}"
