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
