"""Isolated regression checks: no live training, model promotion or data writes."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from guanlan_v2.strategy.compute import cpcv, model_train, model_workflow, v4
from guanlan_v2.screen import model_registry as reg


def _xy(n_dates=100, n_codes=60, horizon=5):
    dates = pd.bdate_range("2026-01-01", periods=n_dates)
    codes = [f"C{i:03}" for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    feature = np.repeat(np.arange(n_dates), n_codes) + np.tile(np.arange(n_codes), n_dates) / 1000
    x = pd.DataFrame({"f1": feature}, index=idx)
    y = pd.Series(feature / 100, index=idx, name="__label__")
    y.loc[y.index.get_level_values("datetime").isin(dates[-horizon:])] = np.nan
    return dates, x, y


def test_holdout_span6_excludes_label_endpoints_from_test():
    dates = pd.bdate_range("2026-06-01", "2026-09-04")
    cutoff, test = model_train.holdout_split(dates, dates[-1], horizon=5, k=20, label_span=6)
    assert len(test) == 20
    assert test[-1] == dates[-7]
    assert dates[dates.get_loc(cutoff) + 6] < test[0]


def test_short_holdout_cannot_fall_back_to_leaking_first_row():
    dates = pd.bdate_range("2026-01-01", periods=15)
    cutoff, test = model_train.holdout_split(dates, dates[-1], horizon=5, k=6, label_span=6)
    assert test and cutoff < dates[0]


def test_v4_uses_separate_holdout_and_latest_mature_refit(monkeypatch):
    import lightgbm
    from financial_analyst.data.loaders import qlib_binary
    dates, x, label = _xy(n_dates=100, horizon=6)
    data = x.rename(columns={"f1": "rev_5"}).assign(label=label)
    data = data.rename_axis(index={"code": "instrument"}).reorder_levels(["instrument", "datetime"])
    fitted = []

    class Model:
        def __init__(self, number):
            self.number = number
            self.predict_rows = []

        def predict(self, values):
            self.predict_rows.append(len(values))
            return values[:, 0] * self.number

    def train(params, dataset, **kwargs):
        model = Model(len(fitted) + 1)
        fitted.append((model, dataset))
        return model

    monkeypatch.setattr(lightgbm, "Dataset", lambda values, label: (values, label))
    monkeypatch.setattr(lightgbm, "train", train)
    monkeypatch.setattr(qlib_binary, "QlibBinaryLoader", lambda *a: SimpleNamespace(
        _read_bin=lambda *a: pd.Series(10., index=dates)))
    monkeypatch.setattr(v4, "build_feature_panel", lambda *a: data.copy())
    monkeypatch.setattr(v4, "add_ind_turnover", lambda data, *a: data)
    monkeypatch.setattr(v4, "add_breadth_resid", lambda data: data)
    monkeypatch.setattr(v4, "_load_name_map", lambda: {})
    monkeypatch.setattr(v4, "_score_top200", lambda *a: pd.DataFrame(columns=["code", "total", "layer"]))
    holdout, model_out = {"k": 20}, {}
    result = v4.build_v4("unused", codes=["C000"], feature_cols=["rev_5"],
                         holdout=holdout, model_out=model_out)
    assert len(fitted) == 2
    assert len(fitted[0][1][0]) < len(fitted[1][1][0])
    assert holdout["n_holdout"] == 20
    assert holdout["final_fit"]["end"] == str(dates[-7].date())
    assert holdout["test_end"] == str(dates[-7].date())
    assert fitted[0][0].predict_rows == [20 * 60]
    assert fitted[1][0].predict_rows == [60]
    assert model_out["estimator"] is fitted[1][0]
    assert set(result["date"]) == {str(dates[-1].date())}
    assert model_out["provenance"]["strict_pit"] is False


def test_workflow_holdout_purges_matured_label_boundary(monkeypatch):
    import guanlan_v2.workflow.api as api
    dates, x, y = _xy()
    fits = []

    class Model:
        def fit(self, values, labels):
            fits.append(values)

        def predict(self, values):
            return values[:, 0]

    monkeypatch.setattr(api, "_build_model", lambda *a: (Model(), {}))
    evidence = {}
    value = model_workflow._holdout_oos_ic("lightgbm", {}, x, y, fwd_days=5, evidence=evidence)
    assert value == 1.0
    test_first = pd.Timestamp(evidence["test_start"])
    train_last = pd.Timestamp(evidence["train"]["end"])
    assert dates[dates.get_loc(train_last) + 5] < test_first
    assert evidence["n_test_dates_scored"] == 19


def test_workflow_holdout_uses_sparse_per_code_label_endpoints(monkeypatch):
    import guanlan_v2.workflow.api as api
    from financial_analyst.factors.eval.report import forward_simple_returns

    dates, x, _ = _xy()
    # A data provider may omit suspended days. Five observations for this code
    # then span more than five market days and cross the chronological holdout.
    sparse = ((x.index.get_level_values("code") == "C000")
              & x.index.get_level_values("datetime").isin(dates[65:76]))
    x = x[~sparse]
    close = x["f1"] + 10
    y = forward_simple_returns(SimpleNamespace(close=close), 5)
    endpoints = pd.Series(x.index.get_level_values("datetime"), index=x.index)
    endpoints = endpoints.groupby(level="code").shift(-5)
    fits = []

    class Model:
        def fit(self, values, labels):
            fits.append(values)

        def predict(self, values):
            return values[:, 0]

    monkeypatch.setattr(api, "_build_model", lambda *a: (Model(), {}))
    evidence = {}
    value = model_workflow._holdout_oos_ic(
        "lightgbm", {}, x, y, fwd_days=5, evidence=evidence,
        observation_index=x.index,
    )
    assert value is not None and len(fits) == 1
    boundary = pd.Timestamp(evidence["test_start"])
    # This observation passes a naive global-calendar purge but its actual
    # fifth future stock observation is in the test period.
    leaking_row = (dates[64], "C000")
    assert dates[69] < boundary <= endpoints.loc[leaking_row]
    assert x.loc[leaking_row, "f1"] not in fits[0][:, 0]
    trained = x["f1"].isin(fits[0][:, 0])
    assert (endpoints[trained] < boundary).all()
    assert pd.Timestamp(evidence["split"]["train_label_end"]) < boundary


@pytest.mark.parametrize("returns, expected", [([-0.5, 1.0], -0.5),
                                               ([-0.001, 0.0], -0.001),
                                               ([0.1, 0.1], 0.0)])
def test_portfolio_drawdown_includes_initial_capital(returns, expected):
    from financial_analyst.factors.eval.portfolio import portfolio_stats

    stats = portfolio_stats(pd.Series(returns), ppy=12)
    assert stats["max_drawdown"] == pytest.approx(expected)


def test_promote_missing_latest_feature_stops_before_fit(monkeypatch):
    import guanlan_v2.workflow.api as api
    dates, x, y = _xy()
    x.loc[x.index.get_level_values("datetime") == dates[-1], "f1"] = np.nan
    monkeypatch.setattr(api, "_materialize_xy", lambda *a: (None, x, y, ["f1"]))
    monkeypatch.setattr(api, "_build_model", lambda *a: pytest.fail("must not fit stale latest features"))
    result = model_workflow.train_promote({"variant_id": "m_never_saved", "kind": "lightgbm",
                                          "recipe": {"features": ["amihud_20"], "end": "2026-09-04"}})
    assert result["ok"] is False
    assert result["missing_latest_features"] == ["f1"]


def test_custom_label_requires_declared_span_before_materialization():
    result = model_workflow.train_promote({"variant_id": "m_never_saved", "kind": "lightgbm",
                                          "recipe": {"features": ["close"], "label": "delay(close,-20)"}})
    assert result["ok"] is False and "label_span" in result["reason"]


def test_training_persists_full_manifest_before_metadata_commit(tmp_path, monkeypatch):
    import hashlib
    import guanlan_v2.workflow.api as api
    from financial_analyst.factors.zoo.panel import _technical_provenance_summary

    _, x, y = _xy()
    manifest = {"by_code": {"C000": {"definition_hash": "d" * 64,
                 "schema": "causal-v1", "source_file_signatures": {"close": {"mtime_ns": 7}}}},
                 "errors": {"C001": "source unavailable"}}
    summary = _technical_provenance_summary(manifest, 60)
    panel_df = x.copy()
    panel_df.attrs["technical_provenance"] = summary
    panel = SimpleNamespace(df=panel_df, technical_provenance_manifest=manifest)
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(api, "_materialize_xy", lambda *a: (panel, x, y, ["f1"]))
    monkeypatch.setattr(api, "_build_model", lambda *a: (SimpleNamespace(
        fit=lambda *a: None, predict=lambda values: values[:, 0]), {}))
    monkeypatch.setattr(model_workflow, "_attach_v4_dims", lambda rank: (rank, {}))
    monkeypatch.setattr(model_workflow, "_holdout_oos_ic", lambda *a, **k: .02)
    seen = []

    def save(vid, ranking, meta, **kwargs):
        ref = meta["training_provenance"]["technical_provenance_manifest"]
        path = tmp_path / vid / ref["file"]
        payload = path.read_bytes()
        assert json.loads(payload) == manifest
        assert hashlib.sha256(payload).hexdigest() == ref["sha256"] == summary["manifest_sha256"]
        seen.append(ref)

    monkeypatch.setattr(reg, "save_variant", save)
    spec = {"variant_id": "m_manifest", "kind": "lightgbm", "status": "draft",
            "recipe": {"features": ["close"], "end": "2026-09-04"}}
    result = model_workflow.train_promote(spec)
    assert result["ok"] is True and seen[0]["available"] is True
    assert not list((tmp_path / "m_manifest").glob("*.tmp"))
    # A wrapper holding only compact attrs must not invent a full manifest.
    panel.technical_provenance_manifest = None
    monkeypatch.setattr(reg, "save_variant", lambda *a, **k: None)
    result = model_workflow.train_promote({**spec, "variant_id": "m_summary_only"})
    assert result["training_provenance"]["technical_provenance_manifest"]["available"] is False
    assert not (tmp_path / "m_summary_only").exists()


def test_manifest_save_failure_cannot_commit_new_model_metadata(tmp_path, monkeypatch):
    import guanlan_v2.workflow.api as api
    _, x, y = _xy()
    panel = SimpleNamespace(df=x, technical_provenance_manifest={"by_code": {}, "errors": {}})
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(api, "_materialize_xy", lambda *a: (panel, x, y, ["f1"]))
    monkeypatch.setattr(api, "_build_model", lambda *a: (SimpleNamespace(
        fit=lambda *a: None, predict=lambda values: values[:, 0]), {}))
    monkeypatch.setattr(model_workflow, "_attach_v4_dims", lambda rank: (rank, {}))
    monkeypatch.setattr(model_workflow, "_holdout_oos_ic", lambda *a, **k: .02)
    monkeypatch.setattr(reg, "save_variant", lambda *a, **k: pytest.fail("metadata must not commit"))

    def fail_replace(*args):
        raise OSError("injected manifest commit failure")

    monkeypatch.setattr("os.replace", fail_replace)
    result = model_workflow.train_promote({"variant_id": "m_failure", "kind": "lightgbm",
            "recipe": {"features": ["close"], "end": "2026-09-04"}})
    assert result["ok"] is False and "manifest commit failure" in result["reason"]
    assert not list((tmp_path / "m_failure").glob("*.tmp"))


@pytest.mark.parametrize("variant_id", ["prod", "..", ""])
def test_manifest_cannot_write_outside_variant_directory(tmp_path, monkeypatch, variant_id):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    panel = SimpleNamespace(technical_provenance_manifest={"by_code": {}, "errors": {}})
    with pytest.raises(ValueError, match="path"):
        model_workflow._persist_technical_manifest(variant_id, panel, {})
    assert not (tmp_path / "models").exists()


def test_mutated_manifest_cannot_be_saved_under_old_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    panel = SimpleNamespace(technical_provenance_manifest={"by_code": {}, "errors": {}})
    with pytest.raises(ValueError, match="changed since panel summary"):
        model_workflow._persist_technical_manifest("m_changed", panel, {"manifest_sha256": "0" * 64})
    assert not (tmp_path / "m_changed").exists()


def test_model_bundle_persists_estimator_and_version_tracks_content(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    ranking = pd.DataFrame({"code": ["A", "B"], "date": ["2026-09-04"] * 2, "lgb_pct": [.5, 1.]})
    meta = {"id": "m_one", "kind": "lightgbm", "recipe": {"features": ["close"]}}
    reg.save_variant("m_one", ranking, meta, estimator=SimpleNamespace(test_weight=2),
                     preprocessing={"feature_names": ["close"]})
    saved = reg.variant_meta("m_one")
    assert saved["estimator_artifact"]["available"]
    assert (tmp_path / "m_one" / saved["estimator_artifact"]["file"]).is_file()
    version = reg.current_model_version("m_one")
    assert version == saved["model_version"]
    ranking.loc[0, "lgb_pct"] = .7
    ranking.to_parquet(reg.variant_ranking_path("m_one"), index=False)
    assert reg.current_model_version("m_one") != version


def test_versioned_validation_cannot_survive_model_change(tmp_path, monkeypatch):
    from guanlan_v2.strategy import model_health as mh
    monkeypatch.setattr(mh, "CPCV_DIR", tmp_path)
    monkeypatch.setattr(reg, "current_model_version", lambda mid: "mv_new")
    mh.write_cpcv("m_one", {"ready": True, "model_version": "mv_old", "dsr": .9, "n_paths": 15})
    result = mh.load_cpcv_summary("m_one")
    assert result["ready"] is False and result["dsr"] is None
    assert result["previous_dsr"] == .9 and result["evidence_status"] == "stale"


def test_failed_metadata_commit_keeps_previous_complete_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    ranking = pd.DataFrame({"code": ["A"], "date": ["2026-09-04"], "lgb_pct": [.5]})
    reg.save_variant("m_atomic", ranking, {"id": "m_atomic"}, estimator=SimpleNamespace(weight=1))
    old_meta = reg.variant_meta("m_atomic")
    old_ranking = reg.variant_ranking_path("m_atomic")
    replace = reg.os.replace

    def fail_commit(src, dst):
        if str(dst).endswith("meta.json"):
            raise OSError("injected metadata commit failure")
        return replace(src, dst)

    monkeypatch.setattr(reg.os, "replace", fail_commit)
    with pytest.raises(OSError, match="injected"):
        reg.save_variant("m_atomic", ranking.assign(lgb_pct=1.), {"id": "m_atomic"},
                         estimator=SimpleNamespace(weight=2))
    assert reg.variant_meta("m_atomic") == old_meta
    assert reg.variant_ranking_path("m_atomic") == old_ranking
    assert pd.read_parquet(old_ranking)["lgb_pct"].tolist() == [.5]
    assert reg.current_model_version("m_atomic") == old_meta["model_version"]


def test_duplicate_cpcv_test_dates_do_not_inflate_diagnostic_sample():
    dates = pd.bdate_range("2025-01-01", periods=100)
    unique = [(d, .002 + np.sin(i) * .01) for i, d in enumerate(dates)]
    a = cpcv.crossfit_return_diagnostics(unique, dates, 5, 31)
    b = cpcv.crossfit_return_diagnostics(unique * 5, dates, 5, 31)
    assert a["n_nonoverlap"] == b["n_nonoverlap"] == 20
    assert b["n_raw_test_returns"] == 500
    assert b["n_unique_dates"] == 100
    assert a["diagnostic_dsr"] == pytest.approx(b["diagnostic_dsr"])


def test_strict_zero_prediction_paths_is_not_ready(monkeypatch):
    dates, x, y = _xy()
    monkeypatch.setattr(cpcv, "_materialize_panel", lambda *a: ("lightgbm", {
        "_fe": x, "_label": y, "params": {}, "horizon": 5, "label_span": 5}))
    monkeypatch.setattr(cpcv, "retrain_core", lambda *a: pd.Series(dtype=float))
    monkeypatch.setattr(reg, "current_model_version", lambda *a: "mv_test")
    monkeypatch.setattr("guanlan_v2.strategy.compute.regen._latest_trade_date", lambda *a: "2026-09-04")
    result = cpcv.strict_validate("prod")
    assert result["ready"] is False and result["n_paths"] == 0
    assert result["dsr"] is None


@pytest.mark.parametrize("versions, expected", [(["mv_one", "mv_one"], True),
                                                (["mv_one", "mv_two"], False),
                                                ([None, None], False)])
def test_strict_direct_ready_requires_unchanged_model_version(monkeypatch, versions, expected):
    dates, x, y = _xy()
    monkeypatch.setattr(cpcv, "_materialize_panel", lambda *a: ("lightgbm", {
        "_fe": x, "_label": y, "params": {}, "horizon": 5, "label_span": 5,
        "_observation_index": x.index}))
    monkeypatch.setattr(cpcv, "retrain_core", lambda kind, ctx, mask, test:
                        ctx["_fe"].loc[ctx["_fe"].index.get_level_values("datetime").isin(test), "f1"])
    version_values = iter(versions)
    monkeypatch.setattr(reg, "current_model_version", lambda *a: next(version_values))
    monkeypatch.setattr("guanlan_v2.strategy.compute.regen._latest_trade_date", lambda *a: "2026-09-04")
    result = cpcv.strict_validate("prod", n_groups=4, k=1, n_trials=3)
    assert result["n_paths"] > 0
    assert result["ready"] is expected
    assert result["measurement_ready"] is expected
    assert result["model_version_matches"] is expected


@pytest.mark.parametrize("k, expected_splits", [(1, 4), (2, 6)])
def test_strict_sparse_label_intervals_do_not_overlap_test_or_embargo(monkeypatch, k, expected_splits):
    from financial_analyst.factors.eval.report import forward_simple_returns

    dates, x, _ = _xy()
    missing = ((x.index.get_level_values("code") == "C000")
               & x.index.get_level_values("datetime").isin(list(dates[20:40]) + list(dates[50:65])))
    x = x[~missing]
    y = forward_simple_returns(SimpleNamespace(close=x["f1"] + 10), 5)
    observed = pd.Series(x.index.get_level_values("datetime"), index=x.index)
    ends = observed.groupby(level="code").shift(-5).groupby(level="datetime").max()
    monkeypatch.setattr(cpcv, "_materialize_panel", lambda *a: ("lightgbm", {
        "_fe": x, "_label": y, "params": {}, "horizon": 5, "label_span": 5,
        "_observation_index": x.index}))
    train_test = []

    def predict(kind, ctx, mask, test):
        train_test.append((set(x[mask].index.get_level_values("datetime")), test))
        return x.loc[x.index.get_level_values("datetime").isin(test), "f1"]

    monkeypatch.setattr(cpcv, "retrain_core", predict)
    monkeypatch.setattr(reg, "current_model_version", lambda *a: "mv_one")
    monkeypatch.setattr("guanlan_v2.strategy.compute.regen._latest_trade_date", lambda *a: "2026-09-04")
    result = cpcv.strict_validate("prod", n_groups=4, k=k, embargo=2, n_trials=3)
    assert result["ready"] is True and len(train_test) == expected_splits
    saw_later_train = False
    for train, test in train_test:
        saw_later_train |= any(d > max(test) for d in train)
        for d in train:
            if pd.isna(ends[d]):
                continue
            for t in test:
                if pd.isna(ends[t]):
                    continue
                # Purge the actual test label interval; embargo begins only
                # after its true endpoint, not the possibly earlier signal.
                embargo_end = dates[min(len(dates) - 1, dates.get_loc(ends[t]) + 2)]
                assert ends[d] < t or d > embargo_end, (d, ends[d], t, embargo_end)
    assert saw_later_train, "CPCV must not be reduced to a before-first-test walk-forward split"


def test_bootstrap_rejects_already_loaded_foreign_engine(monkeypatch, tmp_path):
    import sys
    from guanlan_v2.strategy.compute.training_runtime import bootstrap_training_engine
    foreign = SimpleNamespace(__file__=str(tmp_path / "other" / "financial_analyst" / "__init__.py"))
    monkeypatch.setitem(sys.modules, "financial_analyst", foreign)
    monkeypatch.delenv("GUANLAN_FA_SRC", raising=False)
    with pytest.raises(RuntimeError, match="engine conflict"):
        bootstrap_training_engine()
