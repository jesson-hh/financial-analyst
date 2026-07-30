# -*- coding: utf-8 -*-
"""R23 + R24 — the production durable-store startup binding.

Why this file exists
--------------------
``guanlan_v2/server.py`` used to call ``bind_process_durable_stores_and_scan()``
with **no kwargs**, which fail-closed but fatally under-wired the one process-wide
store the whole orchestration framework runs on:

* the resolver held only the Phase-1 + Phase-2 registries — a Phase-9 payload write
  raised ``UnknownRegistryDigest``, and at the *next* startup fold the same row came
  back as ``DurableStoreCorrupt``, which ``server.py`` swallowed into a stderr line
  (the store silently vanished from that process);
* ``allowed_cell_namespaces`` defaulted to ``()`` — ``ReplayStateStore`` refused to
  construct (``ShadowContractError``) and every replay-head / index / operation CAS
  died with ``StateCellError``.

**R24**: neither half is fixable after startup. ``bind_process_durable_stores_and_scan``
is idempotent per process (a later call *with* kwargs is a silent no-op) and
``RuntimeStores`` freezes ``frozenset(allowed_cell_namespaces)`` at construction behind
a read-only property. So the binding must be right at the single call site.

**R23**: the union the earlier survey prescribed omitted ``worker.PROMPT_CELL_NAMESPACE``
(``runtime.prompt.v1``), which is CAS-written on **every** LLM node attempt and appears in
no Phase-3/Phase-4 union. :func:`test_union_covers_every_state_cell_namespace_in_the_package`
is the drift guard that would have caught it mechanically.

Run: ``python -m pytest tests/orchestration/test_startup_binding.py -v``
"""
from __future__ import annotations

import ast
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from guanlan_v2 import orch_store_status as rec
from guanlan_v2.orchestration import startup as st
from guanlan_v2.orchestration.adapters import durable as durable_mod

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ORCH_PKG = _REPO_ROOT / "guanlan_v2" / "orchestration"

#: the exact reviewed production union (14 names). Any change here is a contract
#: change and must be justified against the CAS writers in the package.
EXPECTED_UNION: tuple[str, ...] = (
    "adapters.replay_head.v1",          # luozi.REPLAY_HEAD_NAMESPACE
    "adapters.replay_operation.v1",     # luozi.REPLAY_OPERATION_NAMESPACE
    "memory.cutover_preparation.v1",    # PHASE3_MEMORY_STATE_CELL_NAMESPACES
    "memory.proposal_preparation.v1",
    "memory.snapshot_head.v1",
    "memory.snapshot_operation.v1",
    "memory.snapshot_preparation.v1",
    "memory.source_head.v1",
    "memory.source_operation.v1",
    "runtime.prompt.v1",                # worker.PROMPT_CELL_NAMESPACE  <-- R23
    "trial.experiment_head.v1",         # PHASE4_TRIAL_STATE_CELL_NAMESPACES
    "trial.family_head.v1",
    "trial.holdout_lease.v1",
    "trial.window_head.v1",
)

#: shape of a state-cell namespace (``<domain>.<name>.v<n>``) — deliberately does NOT
#: match the payload namespaces ``main``/``sealed``/``review``/``audit``.
_CELL_NS_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+\.v[0-9]+$")


@pytest.fixture(autouse=True)
def _unbind_process_stores():
    """Every test starts from an unbound process (the bind is idempotent-once)."""
    durable_mod._PROCESS_STORES = None
    st.reset_status_for_tests()
    yield
    durable_mod._PROCESS_STORES = None
    st.reset_status_for_tests()


# --------------------------------------------------------------------------- #
# The derived namespace union                                                  #
# --------------------------------------------------------------------------- #
def test_production_union_is_the_exact_reviewed_fourteen():
    assert st.PRODUCTION_CELL_NAMESPACES == EXPECTED_UNION
    assert len(st.PRODUCTION_CELL_NAMESPACES) == 14
    # canonically sorted + duplicate-free (the store freezes it into a frozenset)
    assert list(st.PRODUCTION_CELL_NAMESPACES) == sorted(set(st.PRODUCTION_CELL_NAMESPACES))


def test_production_union_is_derived_not_hardcoded():
    """The union must be composed from the owning modules' own constants."""
    from guanlan_v2.orchestration.adapters.luozi import REPLAY_STATE_CELL_NAMESPACES
    from guanlan_v2.orchestration.trial_ledger import PHASE4_STATE_CELL_NAMESPACES
    from guanlan_v2.orchestration.worker import PROMPT_CELL_NAMESPACE

    expected = set(PHASE4_STATE_CELL_NAMESPACES) | {PROMPT_CELL_NAMESPACE} | set(
        REPLAY_STATE_CELL_NAMESPACES)
    assert set(st.PRODUCTION_CELL_NAMESPACES) == expected
    # and the R23 member specifically: it is in NEITHER phase union
    assert PROMPT_CELL_NAMESPACE not in set(PHASE4_STATE_CELL_NAMESPACES)
    assert PROMPT_CELL_NAMESPACE not in set(REPLAY_STATE_CELL_NAMESPACES)
    assert PROMPT_CELL_NAMESPACE in st.PRODUCTION_CELL_NAMESPACES


#: Namespace-shaped literals in the package that are deliberately NOT state cells.
#: Every exclusion is DECLARED here with its reason — an undeclared namespace-shaped
#: literal fails the drift guard, so a new one cannot be waved through implicitly.
REVIEWED_NON_CELL_LITERALS: dict[str, str] = {
    "policy.action_surface_alias.v1":
        "migration.POLICY_ACTION_SURFACE_ALIAS_V1 — a reviewed adapter *policy id* "
        "stamped on migration rows; never a state-cell namespace",
    "experience.lane0.v1":
        "memory/experience.EXPERIENCE_STREAM_ID — an experience *event-stream id*; "
        "never a state-cell namespace",
}


def _scan_package_namespace_literals() -> dict[str, list[str]]:
    """EVERY namespace-shaped string literal in the orchestration package.

    Deliberately maximal, because the two obvious rules both have blind spots that
    already exist in-repo: the seven ``memory.*`` names are CAS-written from a *variable*
    (``memory/store.py:195`` ``cell_namespace=ns``) whose literal container is
    ``MEMORY_STATE_CELL_OWNERS`` — a module-level ``dict`` whose *name* contains no
    "NAMESPACE". A rule that looks only at ``cell_namespace=<literal>`` plus
    ``*NAMESPACE*`` constants sees neither, so a future namespace introduced in that same
    dict-plus-variable shape would be CAS-written in production and still pass the guard.

    So: every ``ast.Constant`` str of the state-cell shape, anywhere (assignments, tuples,
    lists, **dict keys and values**, call arguments, nested scopes), minus the declared
    :data:`REVIEWED_NON_CELL_LITERALS`. No import side effects.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(_ORCH_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _CELL_NS_RE.match(node.value)):
                found.setdefault(node.value, []).append(f"{rel}:{node.lineno}")
    return found


def test_union_covers_every_state_cell_namespace_in_the_package():
    """Drift guard: a new CAS namespace anywhere must be added to the startup union.

    This is the mechanical check that would have caught ``runtime.prompt.v1``.
    """
    found = _scan_package_namespace_literals()
    candidates = {k: v for k, v in found.items() if k not in REVIEWED_NON_CELL_LITERALS}
    missing = sorted(set(candidates) - set(st.PRODUCTION_CELL_NAMESPACES))
    assert not missing, (
        "namespace-shaped literals in the package that are neither in "
        "PRODUCTION_CELL_NAMESPACES nor declared in REVIEWED_NON_CELL_LITERALS: "
        + json.dumps({m: candidates[m] for m in missing}, indent=2, ensure_ascii=False)
    )
    # and the scan really did find the whole union (guards a broken scanner)
    assert set(candidates) == set(st.PRODUCTION_CELL_NAMESPACES)


def test_the_scan_sees_the_memory_names_through_their_owner_dict():
    """The blind spot this widening closes: ``MEMORY_STATE_CELL_OWNERS`` dict KEYS.

    Its name has no "NAMESPACE" and its CAS site feeds a variable, so the narrow rules
    saw the seven ``memory.*`` names only by luck (a second, unrelated tuple constant in
    ``memory/models.py``). Assert the dict itself is now a source the scan reaches.
    """
    found = _scan_package_namespace_literals()
    owners = "guanlan_v2/orchestration/memory/store.py"
    seen_in_owner_dict = [
        ns for ns in found
        if ns.startswith("memory.") and any(w.startswith(owners) for w in found[ns])
    ]
    assert len(seen_in_owner_dict) == 7, sorted(seen_in_owner_dict)
    assert set(seen_in_owner_dict) <= set(st.PRODUCTION_CELL_NAMESPACES)


def test_every_non_cell_exclusion_is_declared_with_a_reason():
    found = _scan_package_namespace_literals()
    assert len(found) == 16, (
        "the package's namespace-shaped literal count moved (expected 14 union + 2 "
        "declared non-cells). A NEW one is either a state-cell namespace that must join "
        "PRODUCTION_CELL_NAMESPACES, or a non-cell that must be declared in "
        "REVIEWED_NON_CELL_LITERALS with a reason. Full provenance:\n"
        + json.dumps(found, indent=2, sort_keys=True, ensure_ascii=False))
    for literal, reason in REVIEWED_NON_CELL_LITERALS.items():
        assert literal in found, f"{literal!r} no longer exists — drop the exclusion"
        assert literal not in st.PRODUCTION_CELL_NAMESPACES
        assert len(reason) > 40, "an exclusion must carry a real reason"


def _scan_cell_namespace_arguments() -> dict[str, list[str]]:
    """Literals passed DIRECTLY as ``cell_namespace=<str>`` — i.e. proven CAS writes."""
    found: dict[str, list[str]] = {}
    for path in sorted(_ORCH_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if (isinstance(node, ast.keyword) and node.arg == "cell_namespace"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                found.setdefault(node.value.value, []).append(
                    f"{rel}:{node.value.lineno}")
    return found


def _scan_namespace_named_constants() -> dict[str, list[str]]:
    """Literals bound to (or inside) a module-level constant whose NAME says NAMESPACE."""
    found: dict[str, list[str]] = {}
    for path in sorted(_ORCH_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not any("NAMESPACE" in n for n in names) or node.value is None:
                continue
            elts = ([node.value] if isinstance(node.value, ast.Constant)
                    else getattr(node.value, "elts", []))
            for elt in elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    found.setdefault(elt.value, []).append(f"{rel}:{elt.lineno} {names[0]}")
    return found


def test_declared_exclusions_are_structurally_not_cell_namespaces():
    """Minor 2 — a 41-character reason must not be able to silence a real cell namespace.

    ``len(reason) > 40`` is a prose gate, and two of today's sixteen hits are already
    non-cells of *recurring* kinds (a row-stamped policy id, an event-stream id), so
    future false positives are likely rather than hypothetical. Each exclusion must
    therefore also clear two MECHANICAL bars that a genuine state-cell namespace could
    not: it is never passed as ``cell_namespace=``, and it never appears inside a
    ``*NAMESPACE*``-named module-level constant.
    """
    cas_args = _scan_cell_namespace_arguments()
    ns_consts = _scan_namespace_named_constants()

    # the scanners must actually work, or the gate below passes vacuously
    assert "trial.family_head.v1" in cas_args, sorted(cas_args)
    assert "runtime.prompt.v1" in ns_consts, sorted(ns_consts)
    assert "adapters.replay_head.v1" in ns_consts, sorted(ns_consts)

    for literal, reason in REVIEWED_NON_CELL_LITERALS.items():
        assert literal not in cas_args, (
            f"{literal!r} IS passed as cell_namespace= at {cas_args.get(literal)} — it is "
            f"a real state-cell namespace, not an exclusion. Declared reason was: {reason}")
        assert literal not in ns_consts, (
            f"{literal!r} appears in a *NAMESPACE*-named constant at "
            f"{ns_consts.get(literal)} — it walks like a state-cell namespace. "
            f"Declared reason was: {reason}")


# --------------------------------------------------------------------------- #
# The resolver: Phase-1 + Phase-2 + Phase-9 cumulative + pipeline side regs     #
# --------------------------------------------------------------------------- #
def test_production_resolver_holds_phase1_phase2_phase9_and_the_side_registries():
    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.pipeline.api import _pipeline_registry
    from guanlan_v2.orchestration.pipeline.live_decide import _subject_registry
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    resolver, digests = st.build_production_resolver()
    phase1 = default_registry().registry_digest
    phase2 = phase2_runtime_registry(phase1).registry_digest
    phase9 = chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST).registry_digest
    subject = _subject_registry().registry_digest
    pipeline = _pipeline_registry().registry_digest

    assert digests == (phase1, phase2, phase9, subject, pipeline)
    assert len(set(digests)) == 5
    for digest in digests:
        assert resolver.resolve(digest).registry_digest == digest


def test_phase9_only_schema_resolves_only_through_the_phase9_registry():
    """The concrete R23 registry proof: a Phase-9 payload write can now resolve."""
    from guanlan_v2.orchestration.refs import SchemaRef
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    resolver, (_p1, phase2, phase9, _subj, _pipe) = st.build_production_resolver()
    ref = SchemaRef(name="ShadowReplayRunState", version="1")
    assert resolver.resolve(phase9).resolve(ref) is not None
    with pytest.raises(Exception):
        # the old two-registry resolver could not have resolved it
        phase2_runtime_registry(default_registry().registry_digest).resolve(ref)


# --------------------------------------------------------------------------- #
# The pipeline side registries — the 2026-07-31 restart wedge                  #
# --------------------------------------------------------------------------- #
#: The sealed digests of the two run-time side registries. These are PINNED as
#: hex literals deliberately: the production store at ``var/orchestration``
#: ALREADY carries durable payload rows declaring the first digest (payload
#: ``main/c1d144aa…``, the 2026-07-30 deep run's ``RunSubject@1``), so a recipe
#: drift that moved either digest would wedge every existing store on its next
#: restart — exactly the class of failure this section exists to prevent.
_SUBJECT_SIDE_REGISTRY_DIGEST = (
    "cae973b48096a0deb51c1d32b8fcaadb6748056a15eed41b9939193e77fa5d3f")
_PIPELINE_SIDE_REGISTRY_DIGEST = (
    "f3a5cfdd7fae87b39e9c11f24b600a3438adf41d6f8fe194bc9a54a9526eb90d")


def test_the_run_time_side_registries_are_sealed_pinned_and_resolvable():
    """The startup resolver must hold BOTH side registries the run-time commits use.

    ``live_decide._commit_run_subject`` and ``api.build_stores_subject_committer``
    / ``api._commit_candidate_slate`` write durable payloads under their own
    sealed side registries, registered into the LIVE process's resolver at run
    time. That keeps the committing process alive — and wedges every FRESH
    process, whose ``build_production_resolver`` set is all the fold has.
    """
    from guanlan_v2.orchestration.pipeline.api import _pipeline_registry
    from guanlan_v2.orchestration.pipeline.live_decide import _subject_registry

    # the recipes still seal to the digests the on-disk rows declare …
    assert _subject_registry().registry_digest == _SUBJECT_SIDE_REGISTRY_DIGEST
    assert _pipeline_registry().registry_digest == _PIPELINE_SIDE_REGISTRY_DIGEST
    # … and the production resolver resolves both WITHOUT any run-time help.
    resolver, digests = st.build_production_resolver()
    for digest in (_SUBJECT_SIDE_REGISTRY_DIGEST, _PIPELINE_SIDE_REGISTRY_DIGEST):
        assert resolver.resolve(digest).registry_digest == digest
        assert digest in digests


def test_a_store_carrying_run_time_committed_pipeline_payloads_binds_on_restart(tmp_path):
    """The exact 2026-07-31 live failure, end-to-end (RED before the fix).

    Process 1 (yesterday's live server): production-bound, then the deep lane
    commits its ``RunSubject@1`` through the REAL ``live_decide`` side-registry
    path and the slate lane commits through the REAL ``api`` committer — both
    survive because ``stores.resolver.register(...)`` patched the LIVE resolver.
    Process 2 (the restart): a FRESH production resolver folds the same root.
    Before the fix the fold refused the ENTIRE store as ``DurableStoreCorrupt``
    ("no sealed registry registered for digest 'cae973b4…'") — one 9999 restart
    after any deep run killed the whole orchestration store binding.
    """
    from datetime import datetime, timezone

    from guanlan_v2.orchestration.pipeline import api as pipeline_api
    from guanlan_v2.orchestration.pipeline import live_decide
    from guanlan_v2.orchestration.pipeline.contracts import RunSubject
    from guanlan_v2.orchestration.pipeline.screening import RUN_SUBJECT_SCHEMA_REF

    as_of = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
    assert st.bind_orchestration_stores(root=tmp_path)["state"] == "bound"
    stores = durable_mod.process_durable_stores()
    deep_ref = live_decide._commit_run_subject(
        stores, RunSubject(code="300750", as_of=as_of), "deep-restartwedge0001")
    slate_ref = pipeline_api.build_stores_subject_committer(stores)(
        RunSubject(code="600519", as_of=as_of))

    # the restart: fresh process, fresh resolver, same root.
    durable_mod._PROCESS_STORES = None
    st.reset_status_for_tests()
    status = st.bind_orchestration_stores(root=tmp_path)
    assert status["state"] == "bound", (status["error_type"], status["error"])
    assert status["cell_namespace_count"] == 14
    fresh = durable_mod.process_durable_stores()
    for ref in (deep_ref, slate_ref):
        subject = fresh.payloads.get(
            ref.payload_ref, expected_schema_ref=RUN_SUBJECT_SCHEMA_REF)
        assert isinstance(subject, RunSubject)


#: Every reviewed ``<bound stores>.resolver.register(<arg>)`` site in the package,
#: as ``{file relative to the orchestration package: {argument source text}}``.
#: A registration into an ALREADY-BOUND store's resolver keeps only THAT process
#: alive; the durable rows it licenses must fold in a fresh process too, so every
#: recipe named here must be covered by ``build_production_resolver``. A new site
#: (new file, or new argument spelling) fails the guard until it is BOTH reviewed
#: here AND its registry is held by the startup resolver.
REVIEWED_BOUND_RESOLVER_REGISTRATIONS: dict[str, set[str]] = {
    # the Phase-9 cumulative chain registry — build_production_resolver's d9
    "lane0_driver.py": {"registry"},
    # _pipeline_registry() (RunSubject+CandidateSlate) + the Phase-9 cumulative
    "pipeline/api.py": {"_pipeline_registry()", "registry"},
    # _subject_registry() (RunSubject) + the Phase-9 cumulative
    "pipeline/live_decide.py": {"_subject_registry()", "registry"},
}


def _scan_bound_resolver_registrations() -> dict[str, set[str]]:
    """Every ``<expr>.resolver.register(<arg>)`` call in the orchestration package.

    Deliberately shaped: the ``.resolver.`` attribute hop matches exactly the
    "register into an already-bound store's resolver at run time" idiom, and NOT
    the constructor-time ``resolver.register(...)`` calls in ``startup.py`` /
    ``adapters/durable.py`` (bare-name receivers), which build the fresh resolver
    itself and cannot license a row the fold will not hold.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(_ORCH_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_ORCH_PKG).as_posix()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "resolver"
                    and node.args):
                found.setdefault(rel, set()).add(ast.unparse(node.args[0]))
    return found


def test_every_bound_resolver_registration_site_is_reviewed_and_covered():
    """Drift guard: a NEW run-time resolver registration is a restart wedge by
    construction until its registry also lives in ``build_production_resolver``."""
    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.pipeline.api import _pipeline_registry
    from guanlan_v2.orchestration.pipeline.live_decide import _subject_registry

    found = _scan_bound_resolver_registrations()
    assert found == REVIEWED_BOUND_RESOLVER_REGISTRATIONS, (
        "run-time `.resolver.register(...)` sites moved. Each one licenses durable "
        "rows a FRESH process must also fold, so the registry it names must be "
        "held by startup.build_production_resolver — review the new/changed site, "
        "add its registry to the startup resolver, then update the reviewed map.\n"
        + json.dumps({k: sorted(v) for k, v in found.items()}, indent=2))

    # the semantic half: every recipe the reviewed sites name resolves under the
    # production resolver (`registry` at all three sites is the Phase-9 cumulative).
    resolver, _digests = st.build_production_resolver()
    for registry in (
            chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST),
            _subject_registry(), _pipeline_registry()):
        digest = registry.registry_digest
        assert resolver.resolve(digest).registry_digest == digest


# --------------------------------------------------------------------------- #
# The bind itself                                                              #
# --------------------------------------------------------------------------- #
def test_bind_seals_the_store_with_the_full_union_and_all_five_registries(tmp_path):
    status = st.bind_orchestration_stores(root=tmp_path)

    assert status["state"] == "bound"
    assert status["bound"] is True
    stores = durable_mod.process_durable_stores()
    assert stores is not None
    # assert the ACTUAL frozen value on the store, not the intended input
    assert stores.cells.allowed_namespaces == frozenset(EXPECTED_UNION)
    assert status["cell_namespaces"] == list(EXPECTED_UNION)
    assert status["cell_namespace_count"] == 14
    assert len(status["registry_digests"]) == 5


def test_replay_state_store_constructs_against_the_bound_stores(tmp_path):
    """The concrete R23/R24 closure proof (was ``ShadowContractError`` before)."""
    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.adapters.luozi import ReplayStateStore
    from guanlan_v2.orchestration.runtime_clock import SystemClock

    st.bind_orchestration_stores(root=tmp_path)
    stores = durable_mod.process_durable_stores()
    registry = chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)

    store = ReplayStateStore(
        payload_store=stores.payloads, state_cells=stores.cells, registry=registry,
        clock=SystemClock(), uow_factory=lambda: stores.unit_of_work,
        event_store=stores.events,
    )
    assert store.load_head("exp-does-not-exist") is None


def test_prompt_cell_namespace_is_readable_on_the_bound_store(tmp_path):
    """R23: ``runtime.prompt.v1`` is gated on read AND on CAS — both must pass."""
    from guanlan_v2.orchestration.eventstore import StateCellError
    from guanlan_v2.orchestration.worker import PROMPT_CELL_NAMESPACE

    st.bind_orchestration_stores(root=tmp_path)
    stores = durable_mod.process_durable_stores()
    assert stores.cells.load(PROMPT_CELL_NAMESPACE, "0" * 64) is None
    with pytest.raises(StateCellError):
        stores.cells.load("runtime.definitely_not_wired.v1", "0" * 64)


def test_every_union_namespace_is_readable_on_the_bound_store(tmp_path):
    st.bind_orchestration_stores(root=tmp_path)
    stores = durable_mod.process_durable_stores()
    for namespace in EXPECTED_UNION:
        assert stores.cells.load(namespace, "0" * 64) is None


def test_bind_honours_the_store_root_env_override(tmp_path, monkeypatch):
    root = tmp_path / "orch-root"
    monkeypatch.setenv("GUANLAN_ORCH_STORE_ROOT", str(root))
    status = st.bind_orchestration_stores()
    assert status["state"] == "bound"
    assert Path(status["root"]) == root


# --------------------------------------------------------------------------- #
# Honest failure                                                               #
# --------------------------------------------------------------------------- #
def _corrupt_root(tmp_path: Path) -> Path:
    """A store root whose commit journal has a malformed NON-final line."""
    root = tmp_path / "corrupt"
    root.mkdir(parents=True, exist_ok=True)
    (root / "commits.jsonl").write_text(
        '{"seq": 1}\nthis-is-not-json\n{"seq": 2}\n', encoding="utf-8")
    return root


def test_corruption_is_reported_loudly_and_distinguishably(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG, logger=st.__name__)
    status = st.bind_orchestration_stores(root=_corrupt_root(tmp_path))

    assert status["state"] == "corrupt"
    assert status["bound"] is False
    assert status["error_type"] == "DurableStoreCorrupt"
    assert status["error"]
    # nothing is bound — fail-closed, no half-store
    assert durable_mod.process_durable_stores() is None
    # operator-visible: a CRITICAL record carrying the stable marker …
    critical = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical, "a corrupt durable store must log at CRITICAL"
    assert any(st.CORRUPT_MARKER in r.getMessage() for r in critical)
    # … and the same marker on stderr (survives an unconfigured logging root)
    assert st.CORRUPT_MARKER in capsys.readouterr().err


def test_corruption_status_is_distinguishable_from_a_healthy_bind(tmp_path):
    healthy = st.bind_orchestration_stores(root=tmp_path / "ok")
    durable_mod._PROCESS_STORES = None
    st.reset_status_for_tests()
    corrupt = st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    assert healthy["state"] != corrupt["state"]
    assert healthy["bound"] and not corrupt["bound"]
    assert st.orchestration_store_status()["state"] == "corrupt"


def test_strict_mode_refuses_the_boot_on_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv(st.STRICT_ENV, "1")
    with pytest.raises(st.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    assert st.orchestration_store_status()["state"] == "corrupt"
    assert durable_mod.process_durable_stores() is None


def test_non_strict_is_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv(st.STRICT_ENV, raising=False)
    status = st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    assert status["strict"] is False


def test_an_unexpected_bind_failure_is_reported_as_failed_not_skipped(tmp_path, monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("wiring exploded")

    monkeypatch.setattr(st, "_bind_process_stores", _boom)
    status = st.bind_orchestration_stores(root=tmp_path)
    assert status["state"] == "failed"
    assert status["error_type"] == "RuntimeError"
    assert "wiring exploded" in status["error"]


def test_status_starts_out_not_attempted():
    assert st.orchestration_store_status()["state"] == "not_attempted"
    assert st.orchestration_store_status()["bound"] is False


def test_status_is_a_defensive_copy(tmp_path):
    st.bind_orchestration_stores(root=tmp_path)
    st.orchestration_store_status()["state"] = "tampered"
    assert st.orchestration_store_status()["state"] == "bound"


def test_status_is_json_serialisable(tmp_path):
    status = st.bind_orchestration_stores(root=tmp_path)
    assert json.loads(json.dumps(status))["state"] == "bound"


# --------------------------------------------------------------------------- #
# Fix 1 — ONLY strict mode may ever refuse a boot                              #
# --------------------------------------------------------------------------- #
_DURABLE = "guanlan_v2.orchestration.adapters.durable"


def _break_import(monkeypatch, dotted: str, exc: Exception) -> None:
    """Make ``from <dotted> import ...`` raise, as a broken/absent module would."""
    import builtins

    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == dotted:
            raise exc
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_a_broken_durable_module_degrades_to_unavailable_without_raising(
        tmp_path, monkeypatch, capsys):
    """A broken ``durable.py`` must NOT kill ``create_app()``.

    ``startup``'s own top-level imports do not pull in ``durable``, so with the two
    imports outside the guard an ``ImportError`` would propagate out of ``create_app()``
    and take 选股/落子/帷幄/datafeed/MCP down — strictly worse than the kwarg-less code
    it replaced, which caught exactly that and continued.
    """
    _break_import(monkeypatch, _DURABLE, ImportError("simulated broken durable.py"))
    status = st.bind_orchestration_stores(root=tmp_path)  # must NOT raise
    assert status["state"] == "unavailable"
    assert status["bound"] is False
    assert status["error_type"] == "ImportError"
    assert "ORCH-STORE-UNAVAILABLE" in capsys.readouterr().err


def test_a_broken_durable_module_is_loud(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=st.__name__)
    _break_import(monkeypatch, _DURABLE, ImportError("simulated broken durable.py"))
    st.bind_orchestration_stores(root=tmp_path)
    assert any(r.levelno >= logging.ERROR and "ORCH-STORE-UNAVAILABLE" in r.getMessage()
               for r in caplog.records)


def test_strict_mode_refuses_the_boot_when_durable_cannot_import(tmp_path, monkeypatch):
    monkeypatch.setenv(st.STRICT_ENV, "1")
    _break_import(monkeypatch, _DURABLE, ImportError("simulated broken durable.py"))
    with pytest.raises(st.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=tmp_path)
    assert st.orchestration_store_status()["state"] == "unavailable"


@pytest.mark.parametrize("boom", [
    ImportError("broken durable.py"),
    RuntimeError("exploded at import time"),
    ValueError("nonsense"),
])
def test_no_non_strict_failure_mode_ever_raises(tmp_path, monkeypatch, boom):
    """The property in one test: non-strict, nothing escapes."""
    _break_import(monkeypatch, _DURABLE, boom)
    status = st.bind_orchestration_stores(root=tmp_path)
    assert status["state"] in {"unavailable", "failed"}
    assert status["bound"] is False


# --------------------------------------------------------------------------- #
# Fold (b) — the negative control, pinned as a test                            #
# --------------------------------------------------------------------------- #
def test_a_phase9_row_is_corrupt_to_the_old_binding_and_fine_to_ours(tmp_path):
    """Why the Phase-9 registry MUST be bound — the sharpest edge, made permanent.

    Writes exactly what a Phase-9 run writes, then folds the same root twice: once with
    the old kwarg-less defaults (Phase-1 + Phase-2, zero namespaces) and once with the
    production binding. The first is ``DurableStoreCorrupt`` — which the old
    ``server.py`` swallowed into one stderr line, silently losing the whole store.
    """
    from datetime import datetime, timedelta, timezone

    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.adapters.contracts import ReplayDecisionPoint
    from guanlan_v2.orchestration.adapters.durable import (
        DurableStoreCorrupt,
        build_durable_runtime_stores,
    )
    from guanlan_v2.orchestration.eventstore import (
        PayloadPutCommand,
        RuntimeBatch,
        StagedPayloadKey,
        StagedTypedPayloadRef,
        StateCellCompareAndSwapCommand,
    )
    from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
    from guanlan_v2.orchestration.worker import PROMPT_CELL_NAMESPACE

    st.bind_orchestration_stores(root=tmp_path)
    stores = durable_mod.process_durable_stores()
    phase9 = chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)

    as_of = datetime(2026, 7, 26, 9, 30, tzinfo=timezone.utc)
    point = ReplayDecisionPoint(
        schedule_ref=ContentRef(id="sched.r23", version="1", content_digest="c" * 64),
        schedule_digest="d" * 64, point_ordinal=1, scheduled_for=as_of,
        cutoff_at=as_of - timedelta(minutes=1), decision_as_of=as_of,
        eligible_execution_at=as_of + timedelta(minutes=1),
        execution_price_field="close", bar_frequency="1d",
    )
    schema_ref = SchemaRef(name="ReplayDecisionPoint", version="1")
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="neg-control",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="p9"), schema_ref=schema_ref,
            namespace="main",
            payload_template={n: getattr(point, n) for n in type(point).model_fields},
            registry_digest=phase9.registry_digest, idempotency_key="neg-control-pa"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=PROMPT_CELL_NAMESPACE, cell_key_digest="e" * 64,
            expected_value=None,
            new_target=StagedTypedPayloadRef(
                staged_key=StagedPayloadKey(key="p9"), schema_ref=schema_ref,
                namespace="main")),),
    ))

    # (a) the OLD kwarg-less defaults cannot fold that row.
    with pytest.raises(DurableStoreCorrupt) as excinfo:
        build_durable_runtime_stores(tmp_path)
    assert "no sealed registry registered for digest" in str(excinfo.value)
    assert phase9.registry_digest in str(excinfo.value)

    # (b) the production binding folds it, and the prompt cell survives the restart.
    durable_mod._PROCESS_STORES = None
    st.reset_status_for_tests()
    again = st.bind_orchestration_stores(root=tmp_path)
    assert again["state"] == "bound"
    refolded = durable_mod.process_durable_stores()
    assert refolded.cells.load(PROMPT_CELL_NAMESPACE, "e" * 64) is not None


# --------------------------------------------------------------------------- #
# Fix 3 — the /data/health provider (consumer deferred: health.py is dirty)    #
# --------------------------------------------------------------------------- #
def test_the_status_leaf_imports_with_no_orchestration_dependency():
    """The provider must be importable from a consumer that cannot assume the package."""
    src = (_REPO_ROOT / "guanlan_v2" / "orch_store_status.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))
               and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    assert not imports, (
        "the status leaf must import NOTHING — it is the safe-anywhere provider")


def test_health_item_shape_matches_the_datafeed_gate(tmp_path):
    """``status`` is the key ``collect_data_health`` ranks on; it must always exist."""
    assert rec.orchestration_store_health_item()["status"] == "unknown"  # not_attempted
    st.bind_orchestration_stores(root=tmp_path)
    item = rec.orchestration_store_health_item()
    assert item["status"] == "fresh" and item["state"] == "bound"
    assert item["cell_namespace_count"] == 14
    assert json.loads(json.dumps(item))["status"] == "fresh"


def test_health_item_reports_a_corrupt_store_as_missing(tmp_path):
    st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    item = rec.orchestration_store_health_item()
    assert item["status"] == "missing"
    assert item["state"] == "corrupt"
    assert "ORCH-STORE-CORRUPT" in item["note"]


def test_health_item_does_not_cry_wolf_when_the_subsystem_is_merely_absent():
    """``unavailable`` / ``not_attempted`` are opt-in machinery, not an operator fault."""
    rec.record_status(dict(rec.blank_status(), state="unavailable"))
    assert rec.orchestration_store_health_item()["status"] == "unknown"


def test_state_accessors_are_the_one_line_a_consumer_needs(tmp_path):
    assert rec.orchestration_store_state() == "not_attempted"
    assert rec.orchestration_store_bound() is False
    st.bind_orchestration_stores(root=tmp_path)
    assert rec.orchestration_store_state() == rec.HEALTHY_STATE == "bound"
    assert rec.orchestration_store_bound() is True


def test_the_state_vocabulary_is_closed_and_complete():
    assert set(rec.STATES) == {
        "not_attempted", "bound", "unavailable", "corrupt", "failed"}
    with pytest.raises(ValueError):
        rec.record_status({"state": "invented"})


def test_startup_status_and_the_leaf_are_the_same_record(tmp_path):
    """No second copy of the record — fold (c): the duplicated literal is gone."""
    st.bind_orchestration_stores(root=tmp_path)
    assert st.orchestration_store_status() == rec.orchestration_store_status()
    server_src = (_REPO_ROOT / "guanlan_v2" / "server.py").read_text(encoding="utf-8")
    assert '"cell_namespace_count": 0' not in server_src, (
        "server.py must not re-declare the blank-status literal — the leaf owns it")


# --------------------------------------------------------------------------- #
# Round 3 — strict must behave IDENTICALLY on both "unavailable" branches      #
# --------------------------------------------------------------------------- #
def test_the_refusal_and_the_flag_live_in_the_always_importable_leaf():
    """Why the second branch could not honour the flag before: unimportable names.

    ``server.py``'s package-not-importable branch cannot import anything from
    ``startup``. Both the flag and the exception therefore live in the leaf, and
    ``startup`` re-exports them so existing callers are unchanged.
    """
    assert rec.OrchestrationStoreBootRefused is st.OrchestrationStoreBootRefused
    assert rec.STRICT_ENV == st.STRICT_ENV == "GUANLAN_ORCH_STORE_STRICT"
    assert st.OrchestrationStoreBootRefused.__module__ == "guanlan_v2.orch_store_status"


def test_refuse_if_strict_is_the_single_shared_definition():
    assert rec.refuse_if_strict(False, "ANY-MARKER") is None
    with pytest.raises(rec.OrchestrationStoreBootRefused) as exc:
        rec.refuse_if_strict(True, "ORCH-STORE-UNAVAILABLE", ImportError("no package"),
                             "at var/orchestration")
    msg = str(exc.value)
    assert "ORCH-STORE-UNAVAILABLE" in msg and "refusing to boot" in msg
    assert "ImportError: no package" in msg and "var/orchestration" in msg
    assert isinstance(exc.value.__cause__, ImportError)


def test_strict_branch_a_durable_broken_startup_importable(tmp_path, monkeypatch):
    """Branch A: package importable, ``durable.py`` broken → strict refuses."""
    monkeypatch.setenv(st.STRICT_ENV, "1")
    _break_import(monkeypatch, _DURABLE, ImportError("simulated broken durable.py"))
    with pytest.raises(rec.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=tmp_path)
    assert rec.orchestration_store_state() == "unavailable"


def test_strict_branch_b_startup_itself_unimportable(monkeypatch, capsys):
    """Branch B: ``startup`` itself not importable → strict must ALSO refuse.

    Before this fix ``server.py`` recorded the identical ``unavailable`` state, printed,
    and fell through with no strict check — so an operator asserting "I require a working
    orchestration store" got a clean boot on the machine where the package was entirely
    missing, the most complete failure of that assertion.

    This replays ``server.py``'s branch exactly: the same recorded state, the same stderr
    line, and the same ``refuse_if_strict`` call the call site makes.
    """
    boom = ModuleNotFoundError("No module named 'guanlan_v2.orchestration.startup'")
    strict = True
    rec.record_status(dict(
        rec.blank_status(), state="unavailable", strict=strict,
        error_type=type(boom).__name__, error=str(boom)))
    print(f"[guanlan_v2][ORCH-STORE-UNAVAILABLE] ... Set {rec.STRICT_ENV}=1 to refuse "
          f"the boot instead.", file=sys.stderr)
    with pytest.raises(rec.OrchestrationStoreBootRefused):
        rec.refuse_if_strict(strict, "ORCH-STORE-UNAVAILABLE", boom)
    assert rec.orchestration_store_state() == "unavailable"
    assert "ORCH-STORE-UNAVAILABLE" in capsys.readouterr().err


def test_branch_b_still_boots_when_not_strict():
    boom = ModuleNotFoundError("no startup module")
    rec.record_status(dict(rec.blank_status(), state="unavailable", strict=False,
                           error_type=type(boom).__name__, error=str(boom)))
    assert rec.refuse_if_strict(False, "ORCH-STORE-UNAVAILABLE", boom) is None
    assert rec.orchestration_store_bound() is False


def test_server_honours_the_flag_on_the_package_absent_branch():
    """Structural guard on branch B at the real call site (the live proof is on 9998)."""
    src = (_REPO_ROOT / "guanlan_v2" / "server.py").read_text(encoding="utf-8")
    branch = src.split("except Exception as _e:", 1)[1].split("else:", 1)[0]
    assert 'state="unavailable"' in branch
    assert "refuse_if_strict(_strict" in branch, (
        "the package-absent branch must honour GUANLAN_ORCH_STORE_STRICT — it used to "
        "record 'unavailable' and fall through, booting normally under strict")
    assert "_orch_status.STRICT_ENV" in src, "the flag must be read from the leaf"


@pytest.mark.parametrize("flag_named_in", ["corrupt", "failed", "unavailable"])
def test_every_degraded_message_names_the_strict_flag(tmp_path, monkeypatch, capsys,
                                                      flag_named_in):
    """The corrupt message named the flag; the other two did not. Now all three do."""
    if flag_named_in == "corrupt":
        st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    elif flag_named_in == "unavailable":
        _break_import(monkeypatch, _DURABLE, ImportError("broken"))
        st.bind_orchestration_stores(root=tmp_path)
    else:
        monkeypatch.setattr(st, "_bind_process_stores",
                            lambda **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        st.bind_orchestration_stores(root=tmp_path)
    assert st.STRICT_ENV in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Minor 3 — a broken stderr must not swallow the strict refusal                #
# --------------------------------------------------------------------------- #
class _ClosedStream:
    def write(self, *_a, **_k):
        raise ValueError("I/O operation on closed file")

    def flush(self, *_a, **_k):
        raise ValueError("I/O operation on closed file")


def test_shout_never_raises_even_with_a_dead_stderr(monkeypatch):
    monkeypatch.setattr(sys, "stderr", _ClosedStream())
    st._shout(logging.CRITICAL, "MARKER", "message")  # must not raise


def test_a_dead_stderr_cannot_replace_the_strict_refusal(tmp_path, monkeypatch):
    """``_degrade`` shouts BEFORE it refuses — the one place the ordering matters.

    With an unguarded ``_shout`` the ``ValueError`` would escape instead of
    ``OrchestrationStoreBootRefused``, and ``server.py`` would record ``failed`` and
    boot through — silently inverting the operator's strict assertion.
    """
    monkeypatch.setenv(st.STRICT_ENV, "1")
    monkeypatch.setattr(sys, "stderr", _ClosedStream())
    with pytest.raises(rec.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    assert rec.orchestration_store_state() == "corrupt"


def test_a_dead_stderr_does_not_break_the_non_strict_degradation(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", _ClosedStream())
    status = st.bind_orchestration_stores(root=_corrupt_root(tmp_path))
    assert status["state"] == "corrupt"


# --------------------------------------------------------------------------- #
# Round 4 — the third path, and honest `strict` stamping everywhere            #
# --------------------------------------------------------------------------- #
def test_the_prologue_is_guarded_too(tmp_path):
    """An unresolvable root must degrade HERE, not escape to the call site.

    Resolving ``resolved_root`` was the only work left outside every try, so a ``root=``
    that is not path-like raised ``TypeError`` straight out of ``bind_orchestration_stores``
    into ``server.py``'s defence-in-depth branch — which recorded ``failed`` without the
    strict flag and booted through.
    """
    status = st.bind_orchestration_stores(root=object())  # must NOT raise
    assert status["state"] == "failed"
    assert status["error_type"] == "TypeError"
    assert status["root"] == "<unresolved store root>"
    assert durable_mod.process_durable_stores() is None


def test_the_prologue_honours_strict(monkeypatch):
    monkeypatch.setenv(st.STRICT_ENV, "1")
    with pytest.raises(rec.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=object())
    assert rec.orchestration_store_state() == "failed"
    assert rec.orchestration_store_status()["strict"] is True


@pytest.mark.parametrize("scenario", ["corrupt", "failed", "unavailable", "prologue"])
def test_every_recorded_state_stamps_the_real_strict_flag(tmp_path, monkeypatch,
                                                          scenario):
    """A record saying ``strict: false`` while the flag is on is a small lie."""
    monkeypatch.setenv(st.STRICT_ENV, "1")
    if scenario == "corrupt":
        target = _corrupt_root(tmp_path)
    elif scenario == "unavailable":
        _break_import(monkeypatch, _DURABLE, ImportError("broken"))
        target = tmp_path
    elif scenario == "prologue":
        target = object()
    else:
        monkeypatch.setattr(st, "_bind_process_stores",
                            lambda **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        target = tmp_path
    with pytest.raises(rec.OrchestrationStoreBootRefused):
        st.bind_orchestration_stores(root=target)
    recorded = rec.orchestration_store_status()
    assert recorded["strict"] is True, recorded
    assert recorded["state"] != "bound"


def test_server_defence_in_depth_branch_also_honours_strict():
    """The third path: ``server.py``'s catch-all recorded ``failed`` and booted anyway.

    Same divergence as round 3, one layer down — ``startup._degrade("failed")`` refused
    while this branch's ``failed`` booted, under the same flag.
    """
    src = (_REPO_ROOT / "guanlan_v2" / "server.py").read_text(encoding="utf-8")
    refusal = "except _orch_status.OrchestrationStoreBootRefused:"
    branch = src.split(refusal, 1)[1]
    assert 'state="failed", strict=_strict' in branch, (
        "the catch-all must stamp the operator's real flag, not a hardcoded false")
    assert 'refuse_if_strict(_strict, "ORCH-STORE-FAILED"' in branch, (
        "the catch-all must honour GUANLAN_ORCH_STORE_STRICT like every other path")


#: A CONSTRUCTION of the refusal — ``OrchestrationStoreBootRefused(...)``, bare or
#: attribute-qualified. Deliberately keyed on the ``(``: the legitimate
#: ``except _orch_status.OrchestrationStoreBootRefused:`` and the plain ``import`` /
#: ``__all__`` / docstring mentions carry no paren and stay green.
_REFUSAL_CONSTRUCTION_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?OrchestrationStoreBootRefused\s*\(")


def _hand_rolled_refusals(src: str) -> list[str]:
    """Lines that CONSTRUCT the refusal instead of delegating to ``refuse_if_strict``.

    The ``class OrchestrationStoreBootRefused(RuntimeError):`` definition itself is a
    declaration, not a construction, and is excluded (it exists exactly once, in the leaf).
    """
    return [f"{i}: {ln.strip()}" for i, ln in enumerate(src.splitlines(), 1)
            if _REFUSAL_CONSTRUCTION_RE.search(ln)
            and not ln.strip().startswith("class ")]


def test_the_hand_rolled_refusal_detector_actually_detects(tmp_path):
    """Positive control — the bar below must not be able to pass vacuously.

    The earlier version asserted only ``"raise OrchestrationStoreBootRefused" not in
    src``. In ``startup.py`` the class is imported bare so that bit; in ``server.py`` it
    is only reachable as ``_orch_status.OrchestrationStoreBootRefused``, so a hand-rolled
    qualified construction would have slipped past the substring check **and** left
    ``refuse_if_strict(`` at 2, passing the count assertion too. Both spellings must be
    caught, and the legitimate mentions must not be.
    """
    caught = (
        'raise OrchestrationStoreBootRefused("bare")',
        '            raise _orch_status.OrchestrationStoreBootRefused("qualified")',
        "raise rec.OrchestrationStoreBootRefused (spaced)",
        "exc = OrchestrationStoreBootRefused(msg)",
    )
    for poison in caught:
        assert _hand_rolled_refusals(poison), f"detector missed: {poison!r}"
    # the bare-name substring check ALONE would have missed the qualified spelling —
    # this is the hole being closed, asserted rather than described.
    assert "raise OrchestrationStoreBootRefused" not in caught[1]

    allowed = (
        "        except _orch_status.OrchestrationStoreBootRefused:",
        "    OrchestrationStoreBootRefused,",
        '    "OrchestrationStoreBootRefused",',
        "    raises :class:`OrchestrationStoreBootRefused` so the boot fails",
        "except (st.OrchestrationStoreBootRefused, ValueError):",
        "class OrchestrationStoreBootRefused(RuntimeError):",  # the declaration itself
    )
    for benign in allowed:
        assert not _hand_rolled_refusals(benign), f"detector false-positived: {benign!r}"


def test_all_refusal_paths_go_through_the_one_leaf_function():
    """Structural: no path may hand-roll its own refusal (that is how they diverged).

    The divergence has now originated **twice** in this one file — round 3 (the
    package-absent branch) and round 4 (the defence-in-depth catch-all) — so the guard
    matters more than the two fixes it protects.
    """
    server_src = (_REPO_ROOT / "guanlan_v2" / "server.py").read_text(encoding="utf-8")
    startup_src = (_REPO_ROOT / "guanlan_v2" / "orchestration"
                   / "startup.py").read_text(encoding="utf-8")
    leaf_src = (_REPO_ROOT / "guanlan_v2" / "orch_store_status.py").read_text(
        encoding="utf-8")

    for name, src in (("server.py", server_src), ("startup.py", startup_src)):
        assert "raise OrchestrationStoreBootRefused" not in src, (
            f"{name} constructs the refusal itself — it must call "
            "orch_store_status.refuse_if_strict, the single definition")
        hand_rolled = _hand_rolled_refusals(src)
        assert not hand_rolled, (
            f"{name} CONSTRUCTS OrchestrationStoreBootRefused (bare or qualified) at "
            f"{hand_rolled} — every refusal must delegate to "
            "orch_store_status.refuse_if_strict so the paths cannot diverge again")
    assert server_src.count("refuse_if_strict(") == 2, "both server.py branches"
    assert startup_src.count("refuse_if_strict(") == 1, "the one _degrade call"
    # …and the leaf is where the one construction lives.
    assert len(_hand_rolled_refusals(leaf_src)) == 1, _hand_rolled_refusals(leaf_src)


def test_no_stranded_doc_comment_above_the_corrupt_marker():
    """Minor 2: ``STRICT_ENV`` moved to the leaf but its ``#:`` block stayed behind.

    Adjacent ``#:`` blocks merge, so the orphan read as documentation for
    ``CORRUPT_MARKER``. The canonical rationale lives in the leaf.
    """
    lines = (_REPO_ROOT / "guanlan_v2" / "orchestration"
             / "startup.py").read_text(encoding="utf-8").splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("CORRUPT_MARKER"))
    doc = []
    for ln in reversed(lines[:idx]):
        if not ln.startswith("#:"):
            break
        doc.append(ln)
    assert len(doc) == 1, f"CORRUPT_MARKER's #: block absorbed a stranded one: {doc}"
    assert "greppable" in doc[0]
    joined = "\n".join(doc)
    assert "strict" not in joined.lower() and "STRICT_ENV" not in joined


# --------------------------------------------------------------------------- #
# The single call site in server.py                                            #
# --------------------------------------------------------------------------- #
def test_server_binds_through_the_production_helper_only():
    """Source-level guard on the one call site (R24: a second bind is a no-op).

    The live proof is the 9998 run; this keeps the file from regressing to the
    kwarg-less call.
    """
    src = (_REPO_ROOT / "guanlan_v2" / "server.py").read_text(encoding="utf-8")
    assert "bind_orchestration_stores" in src
    assert "bind_process_durable_stores_and_scan()" not in src, (
        "server.py must not call the kwarg-less bind (empty registry set + zero "
        "namespaces — R23/R24)")
    assert src.count("_bind_orch_stores()") == 1, "exactly one bind call site"
    assert "/orchestration/store_status" in src, "the operator-visible status route"
    # Fix 1: the ONLY exception allowed to escape the call site is the strict refusal.
    refusal = "except _orch_status.OrchestrationStoreBootRefused:"
    assert refusal in src, "the strict refusal must be caught by its leaf-owned name"
    after = src.split(refusal, 1)[1]
    assert after.lstrip().startswith("raise"), "the strict refusal must re-raise bare"
    assert "except Exception as _e:" in after, (
        "every non-strict failure must also be caught AT the call site (defence in "
        "depth): a bug in startup.py must never kill create_app()")
