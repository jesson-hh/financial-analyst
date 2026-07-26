# -*- coding: utf-8 -*-
"""R3 — the production wiring: the ``/orchestration`` routes stop answering 503.

``bind_orchestration_launcher`` is the one call site that turns the adapters
router's honest ``*_unwired`` refusals into a real, read-only surface over the
process durable stores. It is **opt-in** (``GUANLAN_ORCH_LAUNCHER=1``): default-off
keeps production byte-identical, which is the same idiom every other additive
subsystem in ``server.py`` uses.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from guanlan_v2.orchestration import startup as st
from guanlan_v2.orchestration.adapters import api as adapters_api


@pytest.fixture(autouse=True)
def _isolate():
    """Never leave a process-level binding behind for a sibling suite."""
    before = adapters_api.process_adapters_router_deps()
    st.reset_launcher_status_for_tests()
    yield
    adapters_api.set_adapters_router_deps(before)
    st.reset_launcher_status_for_tests()


def _bound_stores(tmp_path):
    from guanlan_v2.orchestration.adapters import durable

    resolver, _digests = st.build_production_resolver()
    from guanlan_v2.orchestration.runtime_clock import SystemClock

    return durable.build_durable_runtime_stores(
        tmp_path / "orch", resolver=resolver, clock=SystemClock(),
        allowed_cell_namespaces=st.PRODUCTION_CELL_NAMESPACES)


def test_the_binding_is_opt_in_and_off_by_default(monkeypatch):
    monkeypatch.delenv(st.LAUNCHER_ENV, raising=False)
    record = st.bind_orchestration_launcher()
    assert record["state"] == "disabled"
    assert adapters_api.process_adapters_router_deps().replay_state_store is None


def test_enabled_but_store_less_is_unavailable_not_a_lie(monkeypatch):
    monkeypatch.setenv(st.LAUNCHER_ENV, "1")
    record = st.bind_orchestration_launcher(stores=None)
    assert record["state"] == "unavailable"
    assert adapters_api.process_adapters_router_deps().replay_state_store is None


def test_enabled_binds_the_router_over_the_process_stores(monkeypatch, tmp_path):
    monkeypatch.setenv(st.LAUNCHER_ENV, "1")
    stores = _bound_stores(tmp_path)
    record = st.bind_orchestration_launcher(stores=stores)
    assert record["state"] == "bound"
    deps = adapters_api.process_adapters_router_deps()
    assert deps.replay_state_store is not None
    assert deps.clock is not None
    assert deps.schedule_registry is not None
    assert deps.replay_requests == {}
    # the run-scoped bindings are deliberately NOT bound, and that is SAID
    assert deps.replay_bindings is None
    assert record["replay_bindings"] is False
    assert any("replay_bindings" in note for note in record["notes"])


def test_a_bound_store_really_serves_the_read_only_state_route(monkeypatch, tmp_path):
    """The whole point of R3: /orchestration/replay/state stops 503-ing."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv(st.LAUNCHER_ENV, "1")
    st.bind_orchestration_launcher(stores=_bound_stores(tmp_path))
    app = FastAPI()
    app.include_router(adapters_api.build_adapters_router())
    client = TestClient(app)
    resp = client.get("/orchestration/replay/state",
                      params={"experiment_id": "replay.nothing.here"})
    assert resp.status_code != 503, resp.text
    body = resp.json()
    assert body.get("reason") != "replay_store_unwired"


def test_the_unbound_route_still_answers_its_honest_503(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.delenv(st.LAUNCHER_ENV, raising=False)
    adapters_api.set_adapters_router_deps(adapters_api.AdaptersRouterDeps())
    app = FastAPI()
    app.include_router(adapters_api.build_adapters_router())
    resp = TestClient(app).get("/orchestration/replay/state",
                               params={"experiment_id": "x"})
    assert resp.status_code == 503
    assert resp.json()["reason"] == "replay_store_unwired"


def test_the_binding_never_raises_out_of_a_boot(monkeypatch):
    """server.py calls this at startup; a bug here must never kill the process."""
    monkeypatch.setenv(st.LAUNCHER_ENV, "1")
    record = st.bind_orchestration_launcher(stores=object())   # nonsense input
    assert record["state"] == "failed"
    assert record["error_type"]


def test_server_binds_the_console_actor_and_the_launcher(monkeypatch):
    """The two one-line R21/R22 carries, asserted at the call site."""
    src = Path(__import__("guanlan_v2.server", fromlist=["x"]).__file__).read_text(
        encoding="utf-8")
    assert "plan_approval_actor" in src
    assert "declared_operator_actor" in src
    assert "bind_orchestration_launcher" in src
    # and the launcher binding is inside a guard (never a bare call at module scope)
    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        bound: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom):
                bound |= {a.asname or a.name for a in inner.names
                          if a.name == "bind_orchestration_launcher"}
        if not bound:
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                if name in bound:
                    guarded = True
    assert guarded, "bind_orchestration_launcher must be called inside a try/except"
