# tests/test_fund_flow_panel.py
# 资金面五档资金流接入 panel 的门禁:纯合并语义(精确日匹配·不 ffill·缺即 NaN·10列恒在)。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.panel import _apply_fund_flow, _FUND_FLOW_FIELDS  # noqa: E402


def _mk_panel(dates, codes):
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(dates), codes], names=["datetime", "code"]
    )
    return pd.DataFrame({"close": 1.0}, index=idx)


def test_fund_flow_fields_count():
    assert len(_FUND_FLOW_FIELDS) == 10


def test_apply_exact_match_and_nan():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000", "SZ000001"])
    ff = pd.DataFrame({
        "code": ["SH600000", "SH600000"],
        "trade_date": ["2026-06-17", "2026-06-18"],
        "main_net_pct": [1.5, -2.0],
        "main_net_amount": [100.0, -200.0],
    })
    _apply_fund_flow(panel, ff)
    for col in _FUND_FLOW_FIELDS:
        assert col in panel.columns
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 1.5
    assert panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_amount"] == -200.0
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-17"), "SZ000001"), "main_net_pct"])


def test_apply_no_ffill():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000"])
    ff = pd.DataFrame({"code": ["SH600000"], "trade_date": ["2026-06-17"], "main_net_pct": [3.0]})
    _apply_fund_flow(panel, ff)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 3.0
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_pct"])


def test_apply_empty_adds_nan_columns():
    panel = _mk_panel(["2026-06-17"], ["SH600000"])
    _apply_fund_flow(panel, pd.DataFrame())
    for col in _FUND_FLOW_FIELDS:
        assert col in panel.columns
        assert panel[col].isna().all()


def test_apply_preserves_index():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000", "SZ000001"])
    before = panel.index.tolist()
    _apply_fund_flow(panel, pd.DataFrame())
    assert panel.index.tolist() == before


def test_apply_dedup_keep_last():
    panel = _mk_panel(["2026-06-17"], ["SH600000"])
    ff = pd.DataFrame({
        "code": ["SH600000", "SH600000"],
        "trade_date": ["2026-06-17", "2026-06-17"],
        "main_net_pct": [1.0, 9.0],
    })
    _apply_fund_flow(panel, ff)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 9.0


def test_apply_integer_trade_date_matches():
    panel = _mk_panel(["2026-06-17"], ["SH600000"])
    ff = pd.DataFrame({"code": ["SH600000"], "trade_date": [20260617], "main_net_pct": [2.5]})
    _apply_fund_flow(panel, ff)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 2.5


def test_load_reads_filters_and_maps_instrument(tmp_path):
    from financial_analyst.factors.zoo.panel import _load_fund_flow_df
    raw = pd.DataFrame({
        "instrument": ["SH600000", "SH600000", "SZ000001"],
        "code": ["600000", "600000", "000001"],
        "trade_date": pd.to_datetime(["2026-06-17", "2026-06-10", "2026-06-17"]),
        "main_net_pct": [1.0, 2.0, 3.0],
        "main_net_amount": [10.0, 20.0, 30.0],
    })
    raw.to_parquet(tmp_path / "eastmoney_stock_fund_flow_daily.parquet")
    out = _load_fund_flow_df(["SH600000"], "2026-06-15", "2026-06-18", parquet_root=tmp_path)
    assert list(out["code"].unique()) == ["SH600000"]
    assert len(out) == 1
    assert float(out.iloc[0]["main_net_pct"]) == 1.0


def test_load_missing_file_returns_empty(tmp_path):
    from financial_analyst.factors.zoo.panel import _load_fund_flow_df
    out = _load_fund_flow_df(["SH600000"], None, None, parquet_root=tmp_path)
    assert len(out) == 0


def test_merge_fund_flow_end_to_end(tmp_path):
    from financial_analyst.factors.zoo.panel import _merge_fund_flow
    raw = pd.DataFrame({
        "instrument": ["SH600000"],
        "code": ["600000"],
        "trade_date": pd.to_datetime(["2026-06-17"]),
        "main_net_pct": [1.5],
    })
    raw.to_parquet(tmp_path / "eastmoney_stock_fund_flow_daily.parquet")
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000"])
    _merge_fund_flow(panel, None, ["SH600000"], "2026-06-15", "2026-06-18", parquet_root=tmp_path)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 1.5
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_pct"])
