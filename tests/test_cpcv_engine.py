# tests/test_cpcv_engine.py
import pandas as pd
from guanlan_v2.strategy.compute import cpcv


def test_splits_count_and_no_overlap():
    dates = pd.bdate_range("2022-01-03", periods=300)
    splits = cpcv.make_splits(dates, n_groups=6, k=2, purge=5, embargo=5)
    assert len(splits) == 15
    for tr, te in splits:
        assert set(tr).isdisjoint(set(te))
        assert len(te) > 0 and len(tr) > 0


def test_purge_embargo_removes_boundary_train_dates():
    dates = pd.bdate_range("2022-01-03", periods=120)
    splits = cpcv.make_splits(dates, n_groups=6, k=1, purge=5, embargo=5)
    for tr, te in splits:
        te_sorted = sorted(te); trs = set(tr)
        lo, hi = te_sorted[0], te_sorted[-1]
        pre = [d for d in dates if d < lo][-5:]
        assert all(d not in trs for d in pre), "purge 未覆盖标签窗"
        post = [d for d in dates if d > hi][:5]
        assert all(d not in trs for d in post), "embargo 未生效"


def test_decile_metrics_long_excess_and_ic():
    from guanlan_v2.strategy.compute import cpcv
    rows = []
    for d in pd.bdate_range("2022-01-03", periods=3):
        for i in range(100):
            rows.append({"date": d, "code": f"C{i:03d}", "lgb_pct": i / 99.0, "fwd": i / 99.0 * 0.1})
    m = cpcv.decile_metrics(pd.DataFrame(rows))
    assert m["rank_ic_mean"] > 0.9
    assert m["long_excess_ret"][0] > 0
    assert m["n"] == 3


def test_dsr_basic_properties():
    from guanlan_v2.strategy.compute import cpcv
    import numpy as np
    rng = np.random.default_rng(0)
    good = list(rng.normal(0.02, 0.01, 60)); noise = list(rng.normal(0.0, 0.02, 60))
    dg = cpcv.deflated_sharpe(good, n_trials=10); dn = cpcv.deflated_sharpe(noise, n_trials=10)
    assert 0.0 <= dg <= 1.0 and 0.0 <= dn <= 1.0 and dg > dn
    assert cpcv.deflated_sharpe(good, n_trials=1000) <= cpcv.deflated_sharpe(good, n_trials=2) + 1e-9


def test_dsr_insufficient_returns_none():
    from guanlan_v2.strategy.compute import cpcv
    assert cpcv.deflated_sharpe([0.01, 0.02], n_trials=5) is None
