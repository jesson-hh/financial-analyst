"""Regressions for preserved context and fail-closed model composition (no market I/O)."""
from copy import deepcopy

import pytest

import guanlan_v2.workflow.executor as ex
from guanlan_v2.workflow.migrations import context_source_for_node, migrate_graph


def node(nid, kind, **params):
    return {"id": nid, "type": kind, "params": params}


def edge(a, ap, b, bp):
    return {"from": [a, ap], "to": [b, bp]}


def legacy_graph():
    return {"nodes": [
        node("unrelated", "source", universe="csi800", start="2000-01-01"),
        node("source", "source", universe="csi500", start="2023-01-01",
             end="2026-06-18", codes=["600000.SH"], benchmark="000300.SH",
             leader="600667.SH", oos_frac=0.3, wf_refit="是"),
        node("factor", "formula", expr="rank(close)"),
        node("features", "feature", tag="IC"), node("analysis", "analysis"),
        node("backtest", "backtest"),
    ], "edges": [edge("source", "data", "features", "src"),
                  edge("factor", "out", "features", "feat"),
                  edge("features", "fe", "analysis", "factor"),
                  edge("features", "fe", "backtest", "factor")]}


def v2_graph_and_history():
    graph = legacy_graph()
    graph["schema_version"] = 2
    graph["edges"][2] = edge("factor", "out", "analysis", "factor")
    history = {"changes": [{"code": "bypass_single_feature_matrix",
                            "feature_node_id": "features", "terminal_node_id": "analysis",
                            "producer_node_id": "factor", "producer_type": "formula"}]}
    return graph, history


def test_new_migration_preserves_complete_source_semantics_without_first_source_guess():
    graph = legacy_graph()
    original = deepcopy(graph)
    before = ex.universe_for_node("analysis", graph["nodes"], graph["edges"])
    migrated, report = migrate_graph(graph)
    after = ex.universe_for_node("analysis", migrated["nodes"], migrated["edges"])
    assert before == after
    assert after["universe"] == "csi500"
    assert next(n for n in migrated["nodes"] if n["id"] == "analysis")["params"] == {
        "context_source_id": "source"}
    assert report["changed"] and report["runnable_candidate"]
    assert migrated["schema_version"] == 3
    assert graph == original
    again, second_report = migrate_graph(migrated)
    assert again == migrated and not second_report["changed"]


def test_v2_restore_requires_and_uses_exact_migration_provenance():
    graph, history = v2_graph_and_history()
    original = deepcopy(graph)
    assert not ex.universe_for_node("analysis", graph["nodes"], graph["edges"])["wired"]
    untouched, no_evidence = migrate_graph(graph)
    assert untouched["nodes"] == original["nodes"]
    assert not no_evidence["changed"]
    repaired, report = migrate_graph(graph, previous_migration=history)
    expected = ex.universe_for_node("features", graph["nodes"], graph["edges"])
    assert ex.universe_for_node("analysis", repaired["nodes"], repaired["edges"]) == expected
    assert repaired["edges"] == graph["edges"]
    assert graph == original
    assert any(c["code"] == "restore_v2_source_context" for c in report["changes"])


def test_historical_reference_never_overwrites_user_context_choice():
    graph, history = v2_graph_and_history()
    target = next(n for n in graph["nodes"] if n["id"] == "analysis")
    target["params"]["context_source_id"] = "unrelated"
    repaired, report = migrate_graph(graph, previous_migration=history)
    assert repaired["nodes"] == graph["nodes"]
    assert not report["changed"]


def test_changed_v2_graph_is_preserved_and_marked_for_review():
    graph, history = v2_graph_and_history()
    graph["edges"][2] = edge("other", "out", "analysis", "factor")
    graph["nodes"].append(node("other", "formula", expr="rank(volume)"))
    repaired, report = migrate_graph(graph, previous_migration=history)
    assert repaired["nodes"] == graph["nodes"]
    assert repaired["edges"] == graph["edges"]
    assert not report["runnable_candidate"]
    assert report["needs_review"][0]["code"] == "migration_context_provenance_mismatch"


def test_ambiguous_source_blocks_bypass_not_edge_order_dependent():
    graph = legacy_graph()
    graph["edges"].append(edge("unrelated", "data", "features", "src"))
    repaired, report = migrate_graph(graph)
    assert repaired["edges"] == graph["edges"]
    assert not report["runnable_candidate"]
    assert any(r["code"] == "ambiguous_source_context" for r in report["needs_review"])
    with pytest.raises(ValueError, match="多个"):
        context_source_for_node("features", graph["nodes"], graph["edges"])
    with pytest.raises(ValueError, match="多个"):
        context_source_for_node("features", graph["nodes"], list(reversed(graph["edges"])))


@pytest.mark.parametrize("ref", ["missing", "factor", "", 123])
def test_bad_explicit_source_reference_fails_closed(ref):
    graph, _ = v2_graph_and_history()
    next(n for n in graph["nodes"] if n["id"] == "analysis")["params"]["context_source_id"] = ref
    with pytest.raises(ValueError, match="context_source_id"):
        context_source_for_node("analysis", graph["nodes"], graph["edges"])


def test_context_reference_is_inherited_by_downstream_node():
    graph, history = v2_graph_and_history()
    graph, _ = migrate_graph(graph, previous_migration=history)
    graph["nodes"].append(node("next", "risk"))
    graph["edges"].append(edge("analysis", "report", "next", "factor"))
    assert ex.universe_for_node("next", graph["nodes"], graph["edges"])["start"] == "2023-01-01"


def ml_graph():
    graph = legacy_graph()
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] not in {"analysis", "backtest"}]
    graph["nodes"] += [node("ml", "xgb"), node("mf", "mf"), node("analysis", "analysis")]
    graph["edges"] = graph["edges"][:2] + [edge("features", "fe", "ml", "fe"),
        edge("ml", "model", "mf", "m1"), edge("features", "fe", "mf", "f1"),
        edge("mf", "factor", "analysis", "factor")]
    graph["nodes"].append(node("factor2", "formula", expr="rank(volume)"))
    graph["edges"].append(edge("factor2", "out", "features", "feat"))
    return graph


@pytest.mark.parametrize("setting, expected", [("是", True), (True, True), ("否", False), (False, False)])
def test_durable_ml_receives_source_walkforward_flag(monkeypatch, setting, expected):
    graph = ml_graph()
    next(n for n in graph["nodes"] if n["id"] == "source")["params"]["wf_refit"] = setting
    seen = {}

    def capture(body, kind):
        seen.update(body.model_dump())
        return {"ok": True, "ic": {"rank_ic_mean": 0.01}}

    monkeypatch.setattr(ex, "_call_train", capture)
    outcome = ex.run_graph(graph)
    assert outcome["ok"] and not outcome["node_errors"]
    assert seen["wf_refit"] is expected
    assert seen["start"] == "2023-01-01"
    assert seen["oos_frac"] == 0.3


def test_failed_model_blocks_raw_factor_fallback_and_downstream(monkeypatch):
    graph = ml_graph()
    monkeypatch.setattr(ex, "_call_train", lambda *a: {"ok": False, "reason": "training failed"})

    def must_not_compose(*a):
        pytest.fail("failed model must never fall back to raw feature composition")

    monkeypatch.setattr(ex, "_call_compose", must_not_compose)
    outcome = ex.run_graph(graph)
    assert not outcome["ok"] and outcome["terminal"] is None
    assert {e["nid"] for e in outcome["node_errors"]} == {"ml", "mf", "analysis"}


def test_one_of_two_models_fails_does_not_silently_become_single_model(monkeypatch):
    graph = ml_graph()
    graph["nodes"].append(node("ml2", "lgbm"))
    graph["edges"] += [edge("features", "fe", "ml2", "fe"), edge("ml2", "model", "mf", "m2")]
    monkeypatch.setattr(ex, "_call_train", lambda body, kind:
                        {"ok": False, "reason": "failed"} if kind == "lightgbm"
                        else {"ok": True, "ic": {"rank_ic_mean": 0.01}})
    outcome = ex.run_graph(graph)
    assert not outcome["ok"]
    assert outcome["node_results"]["ml"]["ok"] is True
    assert outcome["node_results"]["mf"]["ok"] is False


def test_connected_model_without_report_is_not_a_raw_factor(monkeypatch):
    graph = ml_graph()
    monkeypatch.setattr(ex, "_call_train", lambda *a: {"ok": True})
    outcome = ex.run_graph(graph)
    assert not outcome["ok"]
    assert any(e["nid"] == "mf" and "拒绝回退" in e["error"] for e in outcome["node_errors"])


def test_bad_reference_is_node_error_and_does_not_call_market_engine(monkeypatch):
    graph = {"nodes": [node("f", "formula", expr="rank(close)"),
                       node("a", "analysis", context_source_id="missing")],
             "edges": [edge("f", "out", "a", "factor")]}
    monkeypatch.setattr(ex, "_call_report2", lambda *a: pytest.fail("invalid context must stop compute"))
    outcome = ex.run_graph(graph)
    assert not outcome["ok"]
    assert "context_source_id" in outcome["node_errors"][0]["error"]


def test_unwired_computation_in_multi_source_graph_cannot_use_first_pool(monkeypatch):
    graph = {"nodes": [node("s1", "source", universe="csi500"),
                       node("s2", "source", universe="csi800"),
                       node("f", "formula", expr="rank(close)"), node("a", "analysis")],
             "edges": [edge("f", "out", "a", "factor")]}
    monkeypatch.setattr(ex, "_call_report2", lambda *a: pytest.fail("ambiguous scope must stop compute"))
    outcome = ex.run_graph(graph)
    assert not outcome["ok"]
    assert "第一个数据源" in outcome["node_errors"][0]["error"]


def test_explicit_run_universe_can_scope_an_unwired_multi_source_graph(monkeypatch):
    graph = {"nodes": [node("s1", "source", universe="csi500"),
                       node("s2", "source", universe="csi800"),
                       node("f", "formula", expr="rank(close)"), node("a", "analysis")],
             "edges": [edge("f", "out", "a", "factor")]}
    seen = {}

    def report(body):
        seen["universe"] = body.universe
        return {"ok": True, "ic": {"rank_ic_mean": 0.01}}

    monkeypatch.setattr(ex, "_call_report2", report)
    outcome = ex.run_graph(graph, overrides={"universe": "csi_fast"})
    assert outcome["ok"] and seen["universe"] == "csi_fast"
