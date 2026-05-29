"""SP-E 研究档案确定性单测 (注入 tmp_path 作可写根, 不碰全局注册表)。"""
from __future__ import annotations

import json

import pytest

from financial_analyst.factors.compose.compose import ComposeResult
from financial_analyst.factors.eval.ic import IcResult
from financial_analyst.factors.eval.portfolio import PortfolioResult
from financial_analyst.factors.eval.report import FactorChar, FactorReport, ReportMeta
from financial_analyst.factors.research.archive import (
    ResearchArchive,
    RunRecord,
    record_from_compose,
    record_from_report,
)


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------
def _archive(tmp_path):
    return ResearchArchive(root=tmp_path)


def _minimal_report(factor="rev_20", universe="csi300", freq="month",
                    start="2024-01-01", end="2025-12-31") -> FactorReport:
    """直接构造最小 FactorReport (无需合成面板)。"""
    meta = ReportMeta(
        factor=factor, family="custom", universe=universe, freq=freq,
        start=start, end=end, n_dates=24, n_codes=300, fwd_days=20,
    )
    ic = IcResult(
        ic_mean=0.03, ic_std=0.10, icir=0.30, ic_tstat=1.5,
        ic_win_rate=0.55, rank_ic_mean=0.04, rank_icir=0.40,
    )
    pf = PortfolioResult(
        ann_return=0.12, sharpe=1.1, max_drawdown=-0.20,
        volatility=0.11, turnover=0.30, win_rate=0.58, calmar=0.6,
    )
    ch = FactorChar(coverage=0.95, autocorr_1=0.8, half_life=5.0)
    return FactorReport(meta=meta, ic=ic, quantile=None, portfolio=pf,
                        characteristics=ch, warnings=[], status="ok", error="")


def _minimal_compose(method="lgbm", members=("rev_20", "vol_60")) -> ComposeResult:
    composite = _minimal_report(factor=f"composite[{method}]", freq="month")
    return ComposeResult(
        method=method, members=list(members), weights={members[0]: 0.6, members[1]: 0.4},
        train_frac=0.6, n_train_dates=14, n_test_dates=10,
        composite=composite, member_oos=[], verdict="综合分 OOS Sharpe 1.10 vs 最佳单成员 0.90 → 增益 (+0.20)",
        warnings=[], status="ok", error="",
    )


# ---------------------------------------------------------------------------
# 1) append → load 往返
# ---------------------------------------------------------------------------
def test_append_load_roundtrip(tmp_path):
    arc = _archive(tmp_path)
    r1 = arc.append(RunRecord(id="", timestamp="", kind="report", target="rev_20",
                              formula="rev_20", universe="csi300", freq="month",
                              start="2024-01-01", end="2025-12-31",
                              metrics={"ic_mean": 0.03, "sharpe": 1.1}, note="first"))
    r2 = arc.append(RunRecord(id="", timestamp="", kind="report", target="vol_60",
                              formula="vol_60", universe="csi300", freq="month",
                              start="2024-01-01", end="2025-12-31",
                              metrics={"ic_mean": 0.01, "sharpe": 0.7}))
    assert r1.id == "r0001"
    assert r2.id == "r0002"
    assert r1.timestamp  # 自动补 timestamp
    loaded = arc.load()
    assert len(loaded) == 2
    assert [r.id for r in loaded] == ["r0001", "r0002"]
    # 字段保真
    assert loaded[0].target == "rev_20"
    assert loaded[0].note == "first"
    assert loaded[0].metrics["sharpe"] == 1.1
    assert loaded[1].kind == "report"


def test_load_missing_file_returns_empty(tmp_path):
    assert _archive(tmp_path).load() == []


def test_append_preserves_explicit_id(tmp_path):
    arc = _archive(tmp_path)
    stored = arc.append(RunRecord(id="rXXXX", timestamp="2020-01-01T00:00:00",
                                  kind="report", target="t", formula="t",
                                  universe="u", freq="day", start="s", end="e",
                                  metrics={}))
    assert stored.id == "rXXXX"
    assert stored.timestamp == "2020-01-01T00:00:00"


# ---------------------------------------------------------------------------
# 2) record_from_report
# ---------------------------------------------------------------------------
def test_record_from_report(tmp_path):
    rep = _minimal_report(factor="rev_20", universe="csi300", freq="month")
    rec = record_from_report(rep, note="looks good", tags=("momentum", "v2"))
    assert rec.kind == "report"
    assert rec.target == "rev_20"
    assert rec.formula == "rev_20"
    assert rec.universe == "csi300"
    assert rec.freq == "month"
    assert rec.start == "2024-01-01"
    assert rec.note == "looks good"
    assert rec.tags == ["momentum", "v2"]
    # 指标键齐全
    for k in ("ic_mean", "icir", "rank_ic_mean", "rank_icir", "ic_tstat",
              "sharpe", "ann_return", "max_drawdown", "turnover", "win_rate",
              "coverage"):
        assert k in rec.metrics, f"missing metric {k}"
    assert rec.metrics["ic_mean"] == 0.03
    assert rec.metrics["sharpe"] == 1.1
    assert rec.metrics["coverage"] == 0.95
    # id/timestamp 留给 append 填
    assert rec.id == ""
    assert rec.timestamp == ""
    # append 后能填上
    stored = _archive(tmp_path).append(rec)
    assert stored.id == "r0001"


def test_record_from_report_guards_none_subobjects(tmp_path):
    """ic/portfolio/characteristics 为 None 不崩, 跳过对应组。"""
    meta = ReportMeta(factor="broken", family="?", universe="csi300", freq="day",
                      start="2024-01-01", end="2024-02-01", n_dates=0, n_codes=0, fwd_days=5)
    rep = FactorReport(meta=meta, ic=None, quantile=None, portfolio=None,
                       characteristics=None, status="compute_error")
    rec = record_from_report(rep)
    assert rec.kind == "report"
    assert rec.target == "broken"
    # 无 ic/pf/ch → 这些键不存在, 但不崩
    assert "ic_mean" not in rec.metrics
    assert "sharpe" not in rec.metrics
    assert "coverage" not in rec.metrics


# ---------------------------------------------------------------------------
# 3) record_from_compose
# ---------------------------------------------------------------------------
def test_record_from_compose(tmp_path):
    res = _minimal_compose(method="lgbm", members=("rev_20", "vol_60"))
    rec = record_from_compose(res, note="ensemble try")
    assert rec.kind == "compose"
    assert "lgbm" in rec.target
    assert "rev_20" in rec.target
    assert "vol_60" in rec.target
    assert rec.target == "lgbm:[rev_20,vol_60]"
    assert rec.note == "ensemble try"
    # composite 指标抽到了
    assert rec.metrics["sharpe"] == 1.1
    assert rec.metrics["ic_mean"] == 0.03
    # compose 专属字段
    assert "verdict" in rec.metrics
    assert "增益" in rec.metrics["verdict"]
    assert rec.metrics["members"] == ["rev_20", "vol_60"]
    assert rec.metrics["weights"] == {"rev_20": 0.6, "vol_60": 0.4}
    # 配置来自 composite.meta
    assert rec.universe == "csi300"
    assert rec.freq == "month"


def test_record_from_compose_no_composite(tmp_path):
    """composite=None (合成失败) → metrics 仅 verdict/members/weights, 不崩。"""
    res = ComposeResult(method="equal", members=["a", "b"], weights={},
                        train_frac=0.6, n_train_dates=0, n_test_dates=0,
                        composite=None, verdict="数据不足", status="fit_error",
                        error="boom")
    rec = record_from_compose(res)
    assert rec.kind == "compose"
    assert rec.target == "equal:[a,b]"
    assert rec.metrics["verdict"] == "数据不足"
    assert rec.metrics["members"] == ["a", "b"]
    assert rec.metrics["weights"] == {}
    # 无 composite → 无数值指标
    assert "sharpe" not in rec.metrics
    assert rec.universe == ""
    # 可正常 append
    stored = _archive(tmp_path).append(rec)
    assert stored.id == "r0001"


# ---------------------------------------------------------------------------
# 4) history
# ---------------------------------------------------------------------------
def test_history_filters_and_orders(tmp_path):
    arc = _archive(tmp_path)
    arc.append(RunRecord(id="", timestamp="2024-01-01T00:00:00", kind="report",
                         target="rev_20", formula="rev_20", universe="csi300",
                         freq="month", start="s", end="e", metrics={"sharpe": 0.5}))
    arc.append(RunRecord(id="", timestamp="2024-01-01T00:00:00", kind="report",
                         target="vol_60", formula="vol_60", universe="csi300",
                         freq="month", start="s", end="e", metrics={"sharpe": 0.3}))
    arc.append(RunRecord(id="", timestamp="2024-03-01T00:00:00", kind="report",
                         target="rev_20", formula="rev_20", universe="csi300",
                         freq="month", start="s", end="e", metrics={"sharpe": 0.9}))
    hist = arc.history("rev_20")
    assert len(hist) == 2
    assert all(r.target == "rev_20" for r in hist)
    # 时间升序
    assert hist[0].timestamp == "2024-01-01T00:00:00"
    assert hist[1].timestamp == "2024-03-01T00:00:00"
    assert hist[0].metrics["sharpe"] == 0.5
    assert hist[1].metrics["sharpe"] == 0.9


def test_list_filters(tmp_path):
    arc = _archive(tmp_path)
    arc.append(record_from_report(_minimal_report(factor="rev_20")))
    arc.append(record_from_compose(_minimal_compose(method="lgbm", members=("rev_20", "vol_60"))))
    assert len(arc.list()) == 2
    assert len(arc.list(kind="report")) == 1
    assert len(arc.list(kind="compose")) == 1
    # target 子串
    assert len(arc.list(target="rev_20")) == 2  # report target == rev_20, compose target 含 rev_20
    assert len(arc.list(kind="report", target="rev_20")) == 1


# ---------------------------------------------------------------------------
# 5) compare
# ---------------------------------------------------------------------------
def test_compare_metric_diffs(tmp_path):
    arc = _archive(tmp_path)
    a = arc.append(RunRecord(id="", timestamp="", kind="report", target="rev_20",
                             formula="rev_20", universe="csi300", freq="month",
                             start="s", end="e",
                             metrics={"sharpe": 1.0, "ic_mean": 0.02, "verdict": "x"}))
    b = arc.append(RunRecord(id="", timestamp="", kind="report", target="rev_20",
                             formula="rev_20", universe="csi300", freq="month",
                             start="s", end="e",
                             metrics={"sharpe": 1.5, "ic_mean": 0.05, "verdict": "y"}))
    out = arc.compare(a.id, b.id)
    assert "error" not in out
    assert out["targets"] == ("rev_20", "rev_20")
    # b - a
    assert out["metric_diffs"]["sharpe"] == pytest.approx(0.5)
    assert out["metric_diffs"]["ic_mean"] == pytest.approx(0.03)
    # 非数值键 (verdict 字符串) 不出现在 diff
    assert "verdict" not in out["metric_diffs"]
    assert out["a"]["id"] == a.id
    assert out["b"]["id"] == b.id


def test_compare_missing_id_no_raise(tmp_path):
    arc = _archive(tmp_path)
    arc.append(RunRecord(id="", timestamp="", kind="report", target="t",
                         formula="t", universe="u", freq="day", start="s", end="e",
                         metrics={"sharpe": 1.0}))
    out = arc.compare("r0001", "r9999")
    assert "error" in out
    assert "r9999" in out["error"]
    # 两个都缺也不抛
    out2 = arc.compare("rAAAA", "rBBBB")
    assert "error" in out2


# ---------------------------------------------------------------------------
# 6) 坏 JSONL 行容错
# ---------------------------------------------------------------------------
def test_load_tolerates_malformed_line(tmp_path):
    arc = _archive(tmp_path)
    good = {
        "id": "r0001", "timestamp": "2024-01-01T00:00:00", "kind": "report",
        "target": "rev_20", "formula": "rev_20", "universe": "csi300",
        "freq": "month", "start": "s", "end": "e", "metrics": {"sharpe": 1.0},
        "note": "", "tags": [],
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    arc.path.write_text(
        json.dumps(good, ensure_ascii=False) + "\n"
        + "{this is not valid json,,,\n"
        + "\n",  # 空行也跳过
        encoding="utf-8",
    )
    loaded = arc.load()  # 不应抛
    assert len(loaded) == 1
    assert loaded[0].id == "r0001"
    assert loaded[0].metrics["sharpe"] == 1.0


def test_load_tolerates_bad_schema_line(tmp_path):
    """字段不匹配 RunRecord (缺必填 / 多余 key) 的行也跳过, 不崩。"""
    arc = _archive(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    arc.path.write_text(
        json.dumps({"id": "r0001", "what": "ever"}) + "\n"  # 缺必填字段
        + json.dumps({
            "id": "r0002", "timestamp": "t", "kind": "report", "target": "x",
            "formula": "x", "universe": "u", "freq": "day", "start": "s",
            "end": "e", "metrics": {},
        }) + "\n",
        encoding="utf-8",
    )
    loaded = arc.load()
    assert len(loaded) == 1
    assert loaded[0].id == "r0002"
