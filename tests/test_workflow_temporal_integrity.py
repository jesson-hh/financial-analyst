from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.temporal_split import label_horizon, purged_dates, next_open_period_returns
from guanlan_v2.workflow.factor_artifact import build_factor_artifact, combine_factor_artifacts, FactorArtifactError


def test_purge_uses_daily_label_endpoints_not_weekly_sample_count():
    calendar = pd.bdate_range("2026-01-01", periods=40)
    candidates = calendar[[0, 5, 10, 15, 20]]
    train, audit = purged_dates(calendar, candidates, calendar[[25, 30, 35]], 5)
    assert train == set(calendar[[0, 5, 10, 15]])
    assert audit["train_label_end"] < audit["test_start"]
    assert audit["purged_dates"] == 1


def test_custom_label_cannot_assume_forward_span():
    with pytest.raises(ValueError, match="自定义标签"):
        label_horizon(SimpleNamespace(label="shift(close,-10)/close-1", fwd_days=5))


def test_sparse_observation_endpoint_overrides_dense_calendar():
    calendar = pd.bdate_range("2026-01-01", periods=20)
    sparse = pd.MultiIndex.from_product([calendar[::3], ["A"]], names=["datetime", "code"])
    train, proof = purged_dates(calendar, calendar[[0, 3]], calendar[[9]], 3, observation_index=sparse)
    assert not train  # A's t+3 is day 9, not dense-calendar day 3.
    assert proof["endpoint_source"] == "per_code_observations"


def test_next_open_execution_follows_actual_rebalance_calendar():
    dates = pd.bdate_range("2026-01-01", periods=12)
    idx = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["datetime", "code"])
    frame = pd.DataFrame({"open": np.repeat(np.arange(10, 22), 2)}, index=idx)
    panel = SimpleNamespace(df=frame, dates=lambda: dates)
    result = next_open_period_returns(panel, dates[[0, 4, 9]])
    assert result.loc[(dates[0], "A")] == pytest.approx(15 / 11 - 1)
    assert result.loc[(dates[4], "A")] == pytest.approx(20 / 15 - 1)
    assert dates[9] not in result.index.get_level_values("datetime")


def test_weights_cannot_use_same_test_ic():
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2026-01-01"]), ["A", "B", "C"]], names=["datetime", "code"])
    a = build_factor_artifact(pd.Series([1, 2, 4], index=idx), kind="ml_prediction", label="a")
    b = build_factor_artifact(pd.Series([2, 4, 1], index=idx), kind="ml_prediction", label="b")
    with pytest.raises(FactorArtifactError, match="独立校准"):
        combine_factor_artifacts([a, b], method="ic", metrics=[{"rank_ic": 1}, {"rank_ic": -1}])
    assert combine_factor_artifacts([a, b])["source"]["method"] == "equal"


def test_selection_never_filters_using_future_returns_and_charges_initial_cost():
    from guanlan_v2.workflow.api import _topn_portfolio
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2026-01-01"]), ["A", "B"]], names=["datetime", "code"])
    signal = pd.Series([2, 1], index=idx)
    returns = pd.Series([np.nan, .1], index=idx)
    with pytest.raises(ValueError, match="退出行情"):
        _topn_portfolio(signal, returns, 1, 252)
    returns.iloc[0] = .02
    _, trades, _, cost = _topn_portfolio(signal, returns, 1, 252, commission=.001)
    assert cost == pytest.approx(.001)
    assert trades[0]["period_return"] == pytest.approx(.019)


def test_context_reference_must_point_to_source():
    from guanlan_v2.workflow.api import validate_graph
    graph = {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "close"}},
                       {"id": "a", "type": "analysis", "params": {"context_source_id": "f"}}],
             "edges": [{"from": ["f", "out"], "to": ["a", "factor"]}]}
    ok, errors = validate_graph(graph, require_complete=True)
    assert not ok and any("context_source_id" in e for e in errors)


def _mock_panel():
    dates = pd.bdate_range("2025-01-01", periods=100)
    idx = pd.MultiIndex.from_product([dates, list("ABCDE")], names=["datetime", "code"])
    rng = np.random.RandomState(42)
    frame = pd.DataFrame({"close": 100 + rng.normal(size=len(idx)).cumsum(), "open": 100}, index=idx)
    features = pd.DataFrame(rng.normal(size=(len(idx), 2)), columns=["f1", "f2"], index=idx)
    label = pd.Series(rng.normal(size=len(idx)), index=idx, name="__label__")
    return SimpleNamespace(df=frame, close=frame["close"], dates=lambda: dates), features, label, ["f1", "f2"]


def test_walkforward_artifact_is_exact_stitched_purged_signal(monkeypatch):
    import json
    from sklearn.tree import DecisionTreeRegressor
    from guanlan_v2.workflow import api
    from guanlan_v2.workflow.factor_artifact import materialize_factor_artifact
    import financial_analyst.factors.eval.report as reports

    mat = _mock_panel()
    monkeypatch.setattr(api, "_materialize_xy", lambda *a: mat)
    monkeypatch.setattr(api, "_build_model", lambda *a: (DecisionTreeRegressor(max_depth=2, random_state=0), {}))
    monkeypatch.setattr(reports, "build_report", lambda *a: None)
    body = api.ModelTrainIn(features=["close", "open"], wf_refit=True, wf_folds=3, fwd_days=5)
    output = json.loads(api._train_eval(body, "rf").body)
    assert output["ok"], output
    artifact = output["factor_artifact"]
    assert artifact["source"]["split"]["prediction_scope"] == "purged_walkforward_oos"
    assert output["walkforward_refit"]["folds"] == 3
    for segment in output["walkforward_refit"]["segments"]:
        assert segment["train_label_end"] < segment["test_start"]
    signal = materialize_factor_artifact(artifact)
    assert len(signal) == output["n_test"]
    assert signal.index.get_level_values("datetime").min() < mat[0].dates()[70]


@pytest.mark.parametrize("kind", ["pca", "spearman"])
def test_fitted_transforms_only_emit_test_period(monkeypatch, kind):
    from guanlan_v2.workflow import api

    mat = _mock_panel()
    monkeypatch.setattr(api, "_materialize_xy", lambda *a: mat)
    monkeypatch.setattr(api, "_factor_eval", lambda *a: {"signal": a[3], "extra": a[-1]})
    cls, func = ((api.PCAFactorIn, api._pca_factor) if kind == "pca" else (api.SpearmanFactorIn, api._spearman_factor))
    output = func(cls(features=["close", "open"]))
    assert output["signal"].index.get_level_values("datetime").min() == mat[0].dates()[70]
    transform = output["extra"]["factor_artifact"]["source"]["transform"]
    assert transform["split"]["train_end"] < transform["split"].get("test_start")


@pytest.mark.parametrize("method", ["ic", "icir"])
def test_supervised_factor_weights_only_evaluate_oos(method):
    from guanlan_v2.workflow.api import _combine_alpha
    panel, frame, _, cols = _mock_panel()
    signal, weights, note = _combine_alpha(frame, cols, panel, 5, method=method)
    assert signal.index.get_level_values("datetime").min() == panel.dates()[60]
    split = signal.attrs["weight_calibration"]
    assert split["train_label_end"] < split["test_start"]
    assert len(weights) == 2 and note


def test_backtest_kpi_never_resurrects_unaligned_benchmark_ir():
    import inspect
    from guanlan_v2.workflow import api
    source = inspect.getsource(api)
    assert '"information_ratio": pf_dict["information_ratio"]' in source
    assert '"information_ratio_unavailable_reason": pf_dict["information_ratio_unavailable_reason"]' in source
