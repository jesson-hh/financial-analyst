from guanlan_v2.workflow.api import validate_graph


def test_validator_rejects_multi_node_cycle():
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "close"}},
            {"id": "a", "type": "analysis", "params": {}},
        ],
        # The second edge is deliberately port-invalid as well; a structural
        # cycle must still be reported explicitly instead of reaching runtime.
        "edges": [
            {"from": ["f", "out"], "to": ["a", "factor"]},
            {"from": ["a", "report"], "to": ["f", "missing"]},
        ],
    }

    ok, errors = validate_graph(graph, require_complete=True)

    assert ok is False
    assert any("环" in error for error in errors)


def test_validator_rejects_terminal_with_missing_required_input():
    graph = {
        "nodes": [{"id": "a", "type": "analysis", "params": {}}],
        "edges": [],
    }

    ok, errors = validate_graph(graph, require_complete=True)

    assert ok is False
    assert any("a.factor" in error and "缺" in error for error in errors)


def test_backtest_accepts_either_factor_or_portfolio_input():
    graph = {
        "nodes": [
            {"id": "f", "type": "formula", "params": {"expr": "close"}},
            {"id": "b", "type": "backtest", "params": {}},
        ],
        "edges": [{"from": ["f", "out"], "to": ["b", "factor"]}],
    }

    ok, errors = validate_graph(graph)

    assert ok is True, errors
