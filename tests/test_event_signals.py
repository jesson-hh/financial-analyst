"""SP-B.2 事件信号: cross 算子 + 事件研究引擎 + I/O + REST。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import financial_analyst.factors.zoo  # noqa: F401  (注册 alpha families)
from financial_analyst.factors.zoo import operators as ops
from financial_analyst.factors.zoo.panel import PanelData


def _series(code, vals):
    dates = pd.date_range("2024-01-02", periods=len(vals), freq="B")
    idx = pd.MultiIndex.from_product([dates, [code]], names=["datetime", "code"])
    return pd.Series(vals, index=idx, dtype=float)


def test_cross_up_and_down():
    a = _series("A", [1, 1, 3, 3, 1])
    b = _series("A", [2, 2, 2, 2, 2])
    up = ops.cross(a, b)            # a 上穿 b
    assert list(up.values) == [0.0, 0.0, 1.0, 0.0, 0.0]   # 仅 idx2 上穿
    down = ops.cross(b, a)          # 死叉 = 反向
    assert down.iloc[4] == 1.0 and down.iloc[2] == 0.0


from financial_analyst.factors.eval.config import EvalConfig


def _event_panel():
    """A 涨/B 跌/C 微涨, 触发(volume>1.5e6)只在 d0 的 A、B → 2 个事件, h=1 收益可手算。"""
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    rows = {
        "A": [10, 12, 12, 12, 12],   # d0→d1 +20%
        "B": [10, 8, 8, 8, 8],       # d0→d1 -20%
        "C": [10, 11, 11, 11, 11],   # d0→d1 +10% (非事件)
    }
    frames = []
    for code, close in rows.items():
        idx = pd.MultiIndex.from_product([dates, [code]], names=["datetime", "code"])
        vol = [2e6 if (code in ("A", "B")) and i == 0 else 1e6 for i in range(len(dates))]
        frames.append(pd.DataFrame({"open": close, "high": [c * 1.01 for c in close],
                                    "low": [c * 0.99 for c in close], "close": close,
                                    "volume": vol}, index=idx))
    return PanelData(pd.concat(frames).sort_index())


def test_build_event_report_known_returns():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    trigger = lambda panel: (panel.volume > 1.5e6).astype(float)
    rpt = build_event_report(p, trigger, EvalConfig(universe="test"),
                             factor_label="volspike", horizons=(1,))
    assert rpt.status == "ok"
    assert rpt.n_events == 2                       # (d0,A),(d0,B)
    h1 = rpt.horizons[0]
    assert h1.h == 1 and h1.n == 2
    assert h1.mean_ret == pytest.approx(0.0, abs=1e-9)    # (+0.2 - 0.2)/2
    assert h1.win_rate == pytest.approx(0.5)
    # 市场调整: 同日全市场均值 = (0.2-0.2+0.1)/3 = +0.0333 → excess 均值 = -0.0333
    assert h1.mean_excess == pytest.approx(-1 / 30, abs=1e-6)
    assert h1.mean_ret != pytest.approx(h1.mean_excess)   # 证明减了市场
    assert rpt.car_curve == [(1, pytest.approx(-1 / 30, abs=1e-6))]


def test_build_event_report_no_events():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    rpt = build_event_report(p, lambda panel: (panel.close < 0).astype(float),
                             EvalConfig(universe="test"))
    assert rpt.status == "no_events" and rpt.n_events == 0


def test_build_event_report_high_rate_warns():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    rpt = build_event_report(p, lambda panel: (panel.close > 0).astype(float),  # 恒触发
                             EvalConfig(universe="test"), horizons=(1,))
    assert rpt.status == "ok"
    assert any("更像连续因子" in w for w in rpt.warnings)


def test_build_event_report_compute_error():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    def boom(panel):
        raise RuntimeError("synthetic boom")
    rpt = build_event_report(p, boom, EvalConfig(universe="test"))
    assert rpt.status == "compute_error" and "synthetic boom" in rpt.error


def _stub_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return StubLoader()


def _patch_data(monkeypatch, codes=("SH600519", "SZ000858", "SH600036", "SZ300750")):
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes", lambda u: list(codes))
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())


def test_event_report_export_and_ok(monkeypatch):
    from financial_analyst.factors.eval import event_report, EventReport  # 导出可见
    _patch_data(monkeypatch)
    rpt = event_report("cross(close, sma(close,20))", EvalConfig(universe="csi300"), horizons=(1, 5))
    assert isinstance(rpt, EventReport)
    assert rpt.status in ("ok", "no_events")   # stub 随机, 可能不触发
    assert rpt.n_codes == 4


def test_event_report_empty_universe(monkeypatch):
    from financial_analyst.factors.eval import event_report
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes", lambda u: [])
    rpt = event_report("cross(close, sma(close,20))", EvalConfig(universe="nope"))
    assert rpt.status == "empty_universe"


def test_event_report_tool(monkeypatch):
    from financial_analyst.buddy import tools as T
    _patch_data(monkeypatch)
    res = T._tool_event_report("cross(close, sma(close,20))", universe="csi300", horizons="1,5")
    assert not res.is_error
    assert "事件研究" in res.content
    # 工具在 TOOL_REGISTRY 注册
    assert any(getattr(t, "name", None) == "event_report" for t in T.TOOL_REGISTRY)
