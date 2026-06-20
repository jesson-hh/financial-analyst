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


def test_holdout_split_embargo_purges_horizon():
    dates = pd.bdate_range("2026-01-01", periods=80)   # 80 个工作日,数据充足
    ld = dates.max()
    tc, hd = mt.holdout_split(dates, ld, horizon=5, k=10)
    labeled = [d for d in dates if d <= ld][:-5]        # 有 label 的交易日
    gap = labeled.index(min(hd)) - labeled.index(tc)    # train_cutoff 到留出最早日之间的交易日间隔
    assert gap == 5 + 1                                  # purge 了 horizon(5)个交易日 + 本身的 1
    assert tc < min(hd)


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


from guanlan_v2.strategy.compute.v4 import _select_mf


def test_select_mf_default_unchanged():
    cols = ["rev_20", "vol_20", "label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw", "log_mv"]
    assert set(_select_mf(cols, None)) == {"rev_20", "vol_20", "log_mv"}   # 旧语义
    assert _select_mf(cols, ["rev_20", "log_mv"]) == ["rev_20", "log_mv"]  # 显式取交集保序


def test_evaluate_library_factors(monkeypatch):
    import numpy as np
    idx = pd.MultiIndex.from_product(
        [["SH600519", "SZ000001"], pd.to_datetime(["2026-01-05", "2026-01-06"])],
        names=["instrument", "datetime"])
    fake_defs = {"c_aaa": {"expr": "rank(close)", "short": "甲"}, "c_bad": {"expr": ""}}
    monkeypatch.setattr(mt, "_factor_defs", lambda: fake_defs)
    monkeypatch.setattr(mt, "_compile_factor", lambda expr: (lambda panel: pd.Series(
        np.arange(len(idx), dtype=float), index=idx)))
    monkeypatch.setattr(mt, "_load_panel", lambda codes, start, end: "PANEL")
    panel, unsup = mt.evaluate_library_factors(["SH600519", "SZ000001"],
                                               ["c_aaa", "c_bad", "c_missing"], "2026-01-01", "2026-01-06")
    assert list(panel.columns) == ["c_aaa"]
    assert set(unsup) == {"c_bad", "c_missing"}
    assert panel.index.names == ["instrument", "datetime"]


def test_evaluate_library_factors_normalizes_real_panel_index(monkeypatch):
    import numpy as np
    real_idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-01-05", "2026-01-06"]), ["SH600519", "SZ000001"]],
        names=["datetime", "code"])                       # 引擎真实顺序/级名
    monkeypatch.setattr(mt, "_factor_defs", lambda: {"c_aaa": {"expr": "rank(close)"}})
    monkeypatch.setattr(mt, "_compile_factor", lambda expr: (lambda panel: pd.Series(
        np.arange(len(real_idx), dtype=float), index=real_idx)))
    monkeypatch.setattr(mt, "_load_panel", lambda codes, start, end: "PANEL")
    panel, unsup = mt.evaluate_library_factors(["SH600519", "SZ000001"], ["c_aaa"],
                                               "2026-01-01", "2026-01-06")
    assert panel.index.names == ["instrument", "datetime"]                       # 归一成 instrument×datetime
    assert set(panel.index.get_level_values("instrument")) == {"SH600519", "SZ000001"}  # code 值进 instrument 级
    assert list(panel.columns) == ["c_aaa"]
