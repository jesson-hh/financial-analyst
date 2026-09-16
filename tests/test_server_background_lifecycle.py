from __future__ import annotations

import ast
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "guanlan_v2" / "server.py"
_SCHEDULER_CALLS = {
    "start_market_status_scheduler",
    "start_regen_daily_scheduler",
}


def _nearest_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return ""


def test_background_gate_is_exact_zero_and_defaults_on() -> None:
    # Importing the module builds the app but no longer starts the schedulers;
    # the helper itself is pure and documents the production/test boundary.
    from guanlan_v2.server import _server_background_enabled

    assert _server_background_enabled({}) is True
    assert _server_background_enabled({"GUANLAN_SERVER_BACKGROUND": "1"}) is True
    assert _server_background_enabled({"GUANLAN_SERVER_BACKGROUND": "0"}) is False
    assert _server_background_enabled({"GUANLAN_SERVER_BACKGROUND": "false"}) is True


def test_schedulers_are_called_only_inside_asgi_lifespan() -> None:
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    owners: dict[str, list[str]] = {name: [] for name in _SCHEDULER_CALLS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id in owners:
            owners[node.func.id].append(_nearest_function(node, parents))

    assert owners == {
        "start_market_status_scheduler": ["_composed_lifespan"],
        "start_regen_daily_scheduler": ["_composed_lifespan"],
    }


def test_deferred_orchestration_bind_obeys_background_gate() -> None:
    source = _SERVER.read_text(encoding="utf-8")
    assert "if _background_on and _deferred_orch_bind is not None:" in source


def test_pit_time_lease_maintainer_is_lifespan_owned_and_cancelled() -> None:
    source = _SERVER.read_text(encoding="utf-8")
    assert "_pit_time = _aio.create_task(_maintain_pit_time())" in source
    assert "if _pit_time is not None:" in source
    assert "_pit_time.cancel()" in source
