# tests/test_model_train.py
import pandas as pd
from guanlan_v2.strategy.compute import model_train as mt


def test_holdout_split_reserves_last_k_labeled_days():
    dates = pd.to_datetime([f"2026-01-{d:02d}" for d in range(1, 13)])
    ld = dates.max()
    train_cut, holdout = mt.holdout_split(dates, ld, horizon=5, k=3)
    assert len(holdout) == 3
    assert train_cut < min(holdout)                      # train 截止 < 留出最早日(无重叠)
    assert max(holdout) <= ld - pd.Timedelta(days=5)     # 留出都在"有 label"区


def test_holdout_split_uses_trading_days_not_calendar_days():
    # 含周末跳空的交易日序列(2026-02-02 Mon 起,跳周末)
    days = ["2026-02-02","2026-02-03","2026-02-04","2026-02-05","2026-02-06",
            "2026-02-09","2026-02-10","2026-02-11","2026-02-12","2026-02-13","2026-02-16"]
    dates = pd.to_datetime(days)
    ld = dates.max()
    train_cut, holdout = mt.holdout_split(dates, ld, horizon=5, k=3)
    last5_trading = set(dates[-5:])                       # 最后 5 个【交易日】= 未标注尾,绝不能进 holdout
    assert not (set(holdout) & last5_trading)             # 无 look-ahead 泄漏
    assert len(holdout) == 3
    assert train_cut < min(holdout)


def test_resolve_feature_cols():
    available = ["rev_20", "vol_20", "breakout_20", "log_mv", "label", "pe_ttm"]
    cols = mt.resolve_feature_cols(available, base_features=["rev_20", "vol_20"], factor_ids=["c_28f035"])
    assert "rev_20" in cols and "vol_20" in cols
    assert "breakout_20" not in cols                  # 未选基础特征剔除
    assert "label" not in cols and "pe_ttm" not in cols
    cols2 = mt.resolve_feature_cols(available, base_features=["nope"], factor_ids=["log_mv"])
    assert cols2 == ["log_mv"]                          # 不存在的列丢弃


def test_resolve_feature_cols_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        mt.resolve_feature_cols(["rev_20", "label"], base_features=[], factor_ids=[])
