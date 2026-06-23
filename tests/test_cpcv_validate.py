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
