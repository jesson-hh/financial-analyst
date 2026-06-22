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
