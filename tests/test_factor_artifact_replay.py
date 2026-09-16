"""Lossless fitted-factor identity across workflow nodes."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd

from guanlan_v2.workflow import executor as ex
from guanlan_v2.workflow.factor_artifact import (
    FactorArtifactError,
    build_factor_artifact,
    materialize_factor_artifact,
)


def _signal(values, name="signal"):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-01-02", "2026-01-05"]), ["A", "B", "C"]],
        names=["datetime", "code"],
    )
    return pd.Series(values, index=index, dtype="float64", name=name)


def _artifact(values, kind="ml_prediction", label="model"):
    return build_factor_artifact(
        _signal(values), kind=kind, label=label,
        source={"fe": {"features": [f"rank({label})"]},
                "model": {"kind": label}},
    )


def test_factor_artifact_exact_replay_and_tamper_rejection():
    signal = _signal([0.1, 0.9, -0.4, 1.2, -0.2, 0.3])
    artifact = build_factor_artifact(
        signal, kind="pca", label="PC1",
        source={"fe": {"features": ["rank(pb)", "rank(roe)"]},
                "transform": {"loadings": [0.8, -0.6]}},
    )
    replay = materialize_factor_artifact(artifact)
    pd.testing.assert_series_equal(replay, signal.rename("PC1"))

    tampered = deepcopy(artifact)
    tampered["signal"]["rows"][0][2] = 999.0
    try:
        materialize_factor_artifact(tampered)
    except FactorArtifactError as exc:
        assert "digest" in str(exc)
    else:  # pragma: no cover - security contract
        raise AssertionError("tampered factor artifact was accepted")


def test_api_aligns_exact_sparse_prediction_without_raw_feature_substitution():
    from guanlan_v2.workflow.api import _artifact_factor_for_panel

    signal = _signal([0.1, np.nan, -0.4, 1.2, np.nan, 0.3])
    artifact = build_factor_artifact(
        signal, kind="ml_prediction", label="OOS prediction",
        source={"fe": {"features": ["rank(pb)"]}},
    )
    panel = SimpleNamespace(df=pd.DataFrame(index=signal.index))
    aligned, verified = _artifact_factor_for_panel(artifact, panel)

    expected = signal.dropna().rename("OOS prediction")
    pd.testing.assert_series_equal(aligned, expected)
    assert verified["id"] == artifact["id"]


def test_mf_two_models_consumes_both_real_prediction_artifacts():
    first = _artifact([1, 2, 4, 1, 2, 4], label="m1")
    second = _artifact([4, 1, 2, 2, 4, 1], label="m2")
    inputs = {
        "m1": {"_kind": "xgboost", "portfolio": {"sharpe": 1.0},
               "metrics": {"rank_ic": 0.2}, "factor_artifact": first},
        "m2": {"_kind": "lightgbm", "portfolio": {"sharpe": 0.8},
               "metrics": {"rank_ic": 0.1}, "factor_artifact": second},
    }
    out = ex._exec_mf(inputs, {"combine": "equal"}, {"all_exprs": []})["factor"]
    combined = materialize_factor_artifact(out["factor_artifact"])

    assert out["_kind"] == "multi_model"
    assert {row["artifact_id"] for row in out["weights"]} == {first["id"], second["id"]}
    assert len(out["members"]) == 2
    # Regression guard: the old implementation returned the first model report.
    first_values = materialize_factor_artifact(first).reindex(combined.index)
    assert not np.allclose(combined.to_numpy(), first_values.to_numpy())


def test_backtest_receives_fitted_artifact_instead_of_only_raw_features(monkeypatch):
    artifact = _artifact([1, 2, 4, 4, 1, 2], kind="spearman", label="weighted")
    seen = {}

    def fake_backtest(body):
        seen["artifact"] = body.factor_artifact
        seen["features"] = list(body.features or [])
        return {"ok": True, "status": "ok", "portfolio": {"sharpe": 1.0}}

    monkeypatch.setattr(ex, "_call_backtest", fake_backtest)
    ctx = {
        "universe": "csi_fast", "freq": "month", "oos_frac": 0.0,
        "start": "2026-01-01", "end": "2026-01-31", "codes": [],
        "benchmark": "", "leader": "", "force_rebalance": False,
    }
    factor = {
        "features": ["rank(weighted)"], "_label": "weighted",
        "factor_artifact": artifact, "composite": True,
    }
    result = ex._exec_backtest({"factor": factor}, {"topn": 2}, ctx)["result"]

    assert seen["artifact"]["id"] == artifact["id"]
    assert seen["features"] == ["rank(weighted)"]
    assert result["factor_artifact_id"] == artifact["id"]


def test_mf_two_models_without_artifacts_fails_closed_not_first_wins():
    inputs = {
        "m1": {"_kind": "xgboost", "portfolio": {"sharpe": 1.0}},
        "m2": {"_kind": "lightgbm", "portfolio": {"sharpe": 0.8}},
    }
    try:
        ex._exec_mf(inputs, {"combine": "equal"}, {"all_exprs": []})
    except ex.NodeError as exc:
        assert "factor_artifact" in str(exc) and "第一路" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("multi-model path silently selected the first model")
