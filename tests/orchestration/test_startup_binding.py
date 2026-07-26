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
    assert len(found) == 16, sorted(found)  # 14 union + 2 declared non-cells
    for literal, reason in REVIEWED_NON_CELL_LITERALS.items():
        assert literal in found, f"{literal!r} no longer exists — drop the exclusion"
        assert literal not in st.PRODUCTION_CELL_NAMESPACES
        assert len(reason) > 40, "an exclusion must carry a real reason"


# --------------------------------------------------------------------------- #
# The resolver: Phase-1 + Phase-2 + Phase-9 cumulative                          #
# --------------------------------------------------------------------------- #
def test_production_resolver_holds_phase1_phase2_and_phase9():
    from guanlan_v2.orchestration.adapters import chain
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    resolver, digests = st.build_production_resolver()
    phase1 = default_registry().registry_digest
    phase2 = phase2_runtime_registry(phase1).registry_digest
    phase9 = chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST).registry_digest

    assert digests == (phase1, phase2, phase9)
    assert len(set(digests)) == 3
    for digest in digests:
        assert resolver.resolve(digest).registry_digest == digest


def test_phase9_only_schema_resolves_only_through_the_phase9_registry():
    """The concrete R23 registry proof: a Phase-9 payload write can now resolve."""
    from guanlan_v2.orchestration.refs import SchemaRef
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    resolver, (_p1, phase2, phase9) = st.build_production_resolver()
    ref = SchemaRef(name="ShadowReplayRunState", version="1")
    assert resolver.resolve(phase9).resolve(ref) is not None
    with pytest.raises(Exception):
        # the old two-registry resolver could not have resolved it
        phase2_runtime_registry(default_registry().registry_digest).resolve(ref)


# --------------------------------------------------------------------------- #
# The bind itself                                                              #
# --------------------------------------------------------------------------- #
def test_bind_seals_the_store_with_the_full_union_and_all_three_registries(tmp_path):
    status = st.bind_orchestration_stores(root=tmp_path)

    assert status["state"] == "bound"
    assert status["bound"] is True
    stores = durable_mod.process_durable_stores()
    assert stores is not None
    # assert the ACTUAL frozen value on the store, not the intended input
    assert stores.cells.allowed_namespaces == frozenset(EXPECTED_UNION)
    assert status["cell_namespaces"] == list(EXPECTED_UNION)
    assert status["cell_namespace_count"] == 14
    assert len(status["registry_digests"]) == 3


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
    after = src.split("except _BootRefused:", 1)[1]
    assert after.lstrip().startswith("raise"), "the strict refusal must re-raise bare"
    assert "except Exception as _e:" in after, (
        "every non-strict failure must also be caught AT the call site (defence in "
        "depth): a bug in startup.py must never kill create_app()")
