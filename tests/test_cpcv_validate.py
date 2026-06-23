# tests/test_cpcv_validate.py
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import cpcv


def _seed(tmp_path, monkeypatch, n_days=40, n_codes=150):
    from guanlan_v2.strategy import model_health as mh
    monkeypatch.setattr(mh, "SCORE_HISTORY_PARQUET", tmp_path / "score_hist.parquet")
    monkeypatch.setattr(mh, "VINTAGE_IC_PARQUET", tmp_path / "vintage.parquet")
    dates = pd.bdate_range("2026-01-05", periods=n_days); rng = np.random.default_rng(1)
    snap = [{"date": str(d.date()), "code": f"C{i:03d}", "lgb_pct": rng.random()}
            for d in dates for i in range(n_codes)]
    pd.DataFrame(snap).to_parquet(tmp_path / "score_hist.parquet", index=False)
    pd.DataFrame({"date": [str(d.date()) for d in dates], "ic": rng.normal(0.02, 0.05, n_days),
                  "n": n_codes}).to_parquet(tmp_path / "vintage.parquet", index=False)
    return dates


def test_quick_validate_returns_sharpe_dsr(tmp_path, monkeypatch):
    dates = _seed(tmp_path, monkeypatch, n_days=40)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(r.date, r.code): float(r.lgb_pct) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.quick_validate(model_id="prod")
    assert out["ready"] is True and "sharpe" in out and "dsr" in out and out["n_oos_days"] >= 10


def test_quick_validate_insufficient_days(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, n_days=5)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots", lambda hist, horizon=5: {})
    out = cpcv.quick_validate(model_id="prod")
    assert out["ready"] is False and "证据不足" in out["note"]


def test_retrain_core_tree_kind_predicts_test_rows():
    from guanlan_v2.strategy.compute import cpcv
    import numpy as np, pandas as pd
    idx = pd.MultiIndex.from_product(
        [pd.bdate_range("2022-01-03", periods=40), [f"C{i:02d}" for i in range(60)]],
        names=["datetime", "code"])
    rng = np.random.default_rng(0)
    fe = pd.DataFrame({"f1": rng.normal(size=len(idx)), "f2": rng.normal(size=len(idx))}, index=idx)
    label = pd.Series(fe["f1"].values * 0.5 + rng.normal(0, 0.1, len(idx)), index=idx, name="label")
    dts = idx.get_level_values("datetime")
    train_mask = pd.Index(dts).isin(set(dts[dts < pd.Timestamp("2022-02-01")]))
    test_dates = sorted(set(dts[dts >= pd.Timestamp("2022-02-01")]))
    pred = cpcv.retrain_core("lightgbm", {"_fe": fe, "_label": label, "params": {}},
                             train_mask=train_mask, test_dates=test_dates)
    assert isinstance(pred, pd.Series) and len(pred) > 0
    assert set(pred.index.get_level_values("datetime")).issubset(set(test_dates))


@pytest.mark.slow
def test_strict_validate_v4_real(monkeypatch, tmp_path):
    from guanlan_v2.strategy.compute import cpcv
    out = cpcv.strict_validate(model_id="prod", n_groups=6, k=2, universe="csi300", start="2024-06-01")
    assert out["ready"] is True and len(out["paths"]) == 15
    assert out["sharpe_dist"]["median"] is not None and "dsr" in out


def test_write_load_cpcv(tmp_path, monkeypatch):
    from guanlan_v2.strategy import model_health as mh
    monkeypatch.setattr(mh, "CPCV_DIR", tmp_path, raising=False)
    res = {"ready": True, "model_id": "prod", "dsr": 0.7,
           "sharpe_dist": {"median": 1.2, "std": 0.3, "p05": 0.5, "p95": 1.9}, "n_trials": 10}
    mh.write_cpcv("prod", res)
    s = mh.load_cpcv_summary("prod")
    assert s["ready"] is True and s["dsr"] == 0.7 and s["sharpe_dist"]["median"] == 1.2
    assert mh.load_cpcv_summary("nope") is None
