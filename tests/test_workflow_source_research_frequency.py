"""Research frequency propagates independently of legacy source quote frequency."""
import pytest

from guanlan_v2.workflow import executor as ex


def branch(prefix="", universe="csi300_active", research_freq=None):
    params = {"universe": universe, "freq": "day"}
    if research_freq is not None:
        params["research_freq"] = research_freq
    nodes = [
        {"id": prefix + "s", "type": "source", "params": params},
        {"id": prefix + "f", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": prefix + "fe", "type": "feature", "params": {}},
        {"id": prefix + "m", "type": "lgbm", "params": {}},
        {"id": prefix + "mf", "type": "mf", "params": {}},
        {"id": prefix + "a", "type": "analysis", "params": {}},
        {"id": prefix + "b", "type": "backtest", "params": {}},
    ]
    edges = [{"from": [prefix + src, out], "to": [prefix + dst, port]}
             for src, out, dst, port in [
        ("s", "data", "fe", "src"), ("f", "out", "fe", "feat"),
        ("fe", "fe", "m", "fe"), ("m", "model", "mf", "m1"),
        ("mf", "factor", "a", "factor"), ("mf", "factor", "b", "factor"),
    ]]
    return {"nodes": nodes, "edges": edges}


@pytest.fixture
def measured(monkeypatch):
    calls = {"model": [], "analysis": [], "backtest": []}

    def train(body, kind):
        calls["model"].append(body)
        return {"ok": True, "ic": {"rank_ic_mean": .01},
                "fe": {"features": list(body.features)},
                "factor_artifact": {"id": "test_" + body.universe, "kind": "test"}}

    def report(body):
        calls["analysis"].append(body)
        return {"ok": True, "headline_ic": {"rank_ic": .01}}

    def backtest(body):
        calls["backtest"].append(body)
        return {"ok": True, "backtest": {"net_ann": .01}}

    monkeypatch.setattr(ex, "_call_train", train)
    monkeypatch.setattr(ex, "_call_report2", report)
    monkeypatch.setattr(ex, "_call_backtest", backtest)
    return calls


@pytest.mark.parametrize("research,override,expected", [
    ("week", None, "week"), ("week", "day", "day"), ("day", "month", "month"),
])
def test_explicit_research_frequency_reaches_model_analysis_and_backtest(measured, research, override, expected):
    value = branch(research_freq=research)
    result = ex.run_graph(value, overrides={"freq": override} if override else None)
    assert result["ok"] is True and result["node_errors"] == []
    assert measured["model"][0].freq == expected
    assert measured["analysis"][0].freq == expected
    assert measured["backtest"][0].freq == expected
    assert measured["backtest"][0].rebalance == expected


@pytest.mark.parametrize("has_quote_frequency", [True, False])
def test_old_graph_keeps_model_day_and_analysis_backtest_month_defaults(measured, has_quote_frequency):
    value = branch()
    if not has_quote_frequency:
        value["nodes"][0]["params"].pop("freq")
    result = ex.run_graph(value)
    assert result["ok"] is True and result["node_errors"] == []
    assert measured["model"][0].freq == "day"
    assert measured["analysis"][0].freq == "month"
    assert measured["backtest"][0].freq == "day"
    assert measured["backtest"][0].rebalance == "month"


def test_each_source_branch_keeps_its_own_research_frequency(measured):
    weekly = branch("w", "csi300_active", "week")
    daily = branch("d", "csi800", "day")
    value = {"nodes": daily["nodes"] + weekly["nodes"],
             "edges": weekly["edges"] + daily["edges"]}
    result = ex.run_graph(value)
    assert result["ok"] is True and result["node_errors"] == []
    expected = {"csi300_active": "week", "csi800": "day"}
    for kind in measured:
        assert {body.universe: body.freq for body in measured[kind]} == expected
    assert {body.universe: body.rebalance for body in measured["backtest"]} == expected


def test_unwired_source_research_frequency_is_not_guessed(measured):
    value = branch(research_freq="week")
    value["edges"] = [edge for edge in value["edges"] if edge["from"][0] != "s"]
    result = ex.run_graph(value)
    assert result["ok"] is True
    assert measured["model"][0].freq == "day"
    assert measured["analysis"][0].freq == "month"
    assert measured["backtest"][0].rebalance == "month"


def test_node_specific_evaluation_and_rebalance_choices_keep_existing_precedence(measured):
    value = branch(research_freq="week")
    value["nodes"][-2]["params"]["rebal"] = "day"
    value["nodes"][-1]["params"]["rebalance"] = "month"
    result = ex.run_graph(value)
    assert result["ok"] is True
    assert measured["model"][0].freq == "week"
    assert measured["analysis"][0].freq == "day"
    assert measured["backtest"][0].freq == "week"
    assert measured["backtest"][0].rebalance == "month"
