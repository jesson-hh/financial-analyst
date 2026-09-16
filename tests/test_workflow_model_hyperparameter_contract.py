"""Exercise real estimators across the canvas -> executor -> API boundary."""
import importlib.util

import numpy as np
import pytest

from guanlan_v2.workflow.api import _build_model
from guanlan_v2.workflow.executor import _hp


@pytest.mark.parametrize("node_kind,model_kind,library,canvas,expected", [
    ("rf", "rf", "sklearn", {"trees": 10}, {"n_estimators": 10}),
    ("xgb", "xgboost", "xgboost", {"trees": 7, "depth": 2, "lr": 0.07, "sub": 0.6},
     {"n_estimators": 7, "max_depth": 2, "learning_rate": 0.07, "subsample": 0.6}),
    ("lgbm", "lightgbm", "lightgbm", {"leaves": 9, "lr": 0.08},
     {"num_leaves": 9, "learning_rate": 0.08}),
    ("svm", "svm", "sklearn", {"c": 0.25}, {"C": 0.25}),
])
def test_canvas_params_reach_real_estimator(node_kind, model_kind, library, canvas, expected):
    if importlib.util.find_spec(library) is None:
        pytest.skip(f"{library} is not installed")
    request_params = _hp(node_kind, canvas)
    model, reported = _build_model(model_kind, request_params)
    actual = model.get_params()
    for key, value in expected.items():
        assert reported[key] == value
        assert actual[key] == value


def test_requested_ten_rf_trees_are_really_fitted_not_default_two_hundred():
    model, reported = _build_model("rf", _hp("rf", {"trees": 10}))
    x = np.arange(120, dtype=float).reshape(60, 2)
    y = np.sin(x[:, 0] / 9.0)
    model.fit(x, y)
    assert len(model.estimators_) == 10
    assert model.n_estimators == reported["n_estimators"] == 10


@pytest.mark.parametrize("kind,canonical,legacy", [
    ("rf", {"n_estimators": 11}, {"trees": 11}),
    ("xgboost", {"n_estimators": 9, "max_depth": 4, "learning_rate": 0.03, "subsample": 0.7},
     {"trees": 9, "depth": 4, "lr": 0.03, "sub": 0.7}),
    ("lightgbm", {"n_estimators": 13, "num_leaves": 17, "learning_rate": 0.02},
     {"trees": 13, "leaves": 17, "lr": 0.02}),
    ("svm", {"C": 0.7}, {"c": 0.7}),
])
def test_legacy_aliases_match_canonical_arguments(kind, canonical, legacy):
    library = {"rf": "sklearn", "svm": "sklearn", "xgboost": "xgboost", "lightgbm": "lightgbm"}[kind]
    if importlib.util.find_spec(library) is None:
        pytest.skip(f"{library} is not installed")
    first, first_report = _build_model(kind, canonical)
    second, second_report = _build_model(kind, legacy)
    assert first_report == second_report
    for key in canonical:
        assert first.get_params()[key] == second.get_params()[key]


def test_canonical_key_precedence_is_explicit_and_reported():
    model, report = _build_model("rf", {"n_estimators": 7, "trees": 99})
    assert model.n_estimators == report["n_estimators"] == 7
