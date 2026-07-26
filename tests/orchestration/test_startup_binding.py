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


def _scan_package_cell_namespaces() -> dict[str, list[str]]:
    """Every state-cell namespace name the orchestration package can CAS-write.

    Two mechanical sources, no import side effects:
      1. module-level ``*NAMESPACE*`` constants (str, or tuple/list of str) whose
         value has the state-cell shape;
      2. any ``cell_namespace=<str literal>`` keyword argument.
    """
    found: dict[str, list[str]] = {}

    def _record(value: str, where: str) -> None:
        if _CELL_NS_RE.match(value):
            found.setdefault(value, []).append(where)

    for path in sorted(_ORCH_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "cell_namespace":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    _record(node.value.value, f"{rel}:{node.value.lineno} cell_namespace=")
        for node in tree.body:  # module-level constants only
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not any("NAMESPACE" in n for n in names):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                _record(value.value, f"{rel}:{node.lineno} {names[0]}")
            elif isinstance(value, (ast.Tuple, ast.List)):
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        _record(elt.value, f"{rel}:{elt.lineno} {names[0]}")
    return found


def test_union_covers_every_state_cell_namespace_in_the_package():
    """Drift guard: a new CAS namespace anywhere must be added to the startup union.

    This is the mechanical check that would have caught ``runtime.prompt.v1``.
    """
    found = _scan_package_cell_namespaces()
    missing = sorted(set(found) - set(st.PRODUCTION_CELL_NAMESPACES))
    assert not missing, (
        "state-cell namespaces CAS-written in the package but absent from "
        f"PRODUCTION_CELL_NAMESPACES: "
        + json.dumps({m: found[m] for m in missing}, indent=2, ensure_ascii=False)
    )
    # and the scan really did find the whole union (guards a broken scanner)
    assert set(found) == set(st.PRODUCTION_CELL_NAMESPACES)


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
    assert src.count("bind_orchestration_stores(") == 1, "exactly one bind call site"
    assert "/orchestration/store_status" in src, "the operator-visible status route"
