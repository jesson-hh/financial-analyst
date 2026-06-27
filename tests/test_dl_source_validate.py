# tests/test_dl_source_validate.py
# DL 源 CPCV 闸门禁:复用 ① 原语对真已实现 fwd5d 算 DSR;桩掉引擎前向收益(同 test_cpcv_validate 套路)。
import numpy as np
import pandas as pd
from guanlan_v2.strategy.compute import cpcv


def _write_src(tmp_path, n_days=40, n_codes=60, score_col="pred_ret_5d"):
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    rng = np.random.default_rng(1)
    rows = [{"eval_date": d, "instrument": f"C{i:03d}", score_col: float(rng.normal())}
            for d in dates for i in range(n_codes)]
    p = tmp_path / "dl_pred_gat.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return str(p)


def test_validate_dl_source_positive_passes_gate(tmp_path, monkeypatch):
    path = _write_src(tmp_path)
    # fwd 与 score 正相关 → top-by-score 多头超额为正 → DSR 高
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(str(r.date), str(r.code)): float(r.score) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.validate_dl_source(path)
    assert out["ready"] is True and out["n_oos_days"] >= 10
    assert out["dsr"] is not None and out["passes_gate"] is True
    assert out["sharpe"] is not None and "ic_mean" in out


def test_validate_dl_source_reverse_fails_gate(tmp_path, monkeypatch):
    path = _write_src(tmp_path)
    # fwd 与 score 负相关 → 多头超额为负 → DSR 低 → 不过闸
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(str(r.date), str(r.code)): -float(r.score) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.validate_dl_source(path)
    assert out["ready"] is True
    assert out["dsr"] is not None and out["dsr"] < cpcv.DL_GATE_DSR   # 真低 DSR 拒绝,非诚实缺席 None
    assert out["passes_gate"] is False


def test_validate_dl_source_missing_file():
    out = cpcv.validate_dl_source("___nope___.parquet")
    assert out["ready"] is False and "不存在" in out["note"]


def test_validate_dl_source_insufficient_days(tmp_path, monkeypatch):
    path = _write_src(tmp_path, n_days=5)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots", lambda hist, horizon=5: {})
    out = cpcv.validate_dl_source(path)
    assert out["ready"] is False and "证据不足" in out["note"]


def test_validate_dl_source_n_trials_deflates_gate(tmp_path, monkeypatch):
    # n_trials 真接进闸:同一适中信号(每日方向因子有正有负→DSR 不饱和),试验数↑→DSR↓(deflation 生效)+ 闸与阈值自洽。
    path = _write_src(tmp_path, n_days=60, n_codes=80)
    df = pd.read_parquet(path)
    rng = np.random.default_rng(7)
    dates = df["eval_date"].dt.strftime("%Y-%m-%d").unique()
    dfac = {d: float(rng.normal()) for d in dates}                       # 每日方向因子(有正有负)
    noise = {(pd.Timestamp(r.eval_date).strftime("%Y-%m-%d"), str(r.instrument)): float(rng.normal(0, 0.2))
             for r in df.itertuples()}
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(str(r.date), str(r.code)):
                                                 0.1 * dfac[str(r.date)] * float(r.score)
                                                 + noise[(str(r.date), str(r.code))]
                                                 for r in hist.itertuples()})
    lo = cpcv.validate_dl_source(path, n_trials=2)
    hi = cpcv.validate_dl_source(path, n_trials=100000)
    assert lo["dsr"] is not None and hi["dsr"] is not None
    assert hi["dsr"] < lo["dsr"]                                          # 试验数↑ → DSR↓(deflation 真接进闸)
    assert lo["passes_gate"] == (lo["dsr"] >= cpcv.DL_GATE_DSR)           # 闸 = DSR≥阈值,自洽
    assert hi["passes_gate"] == (hi["dsr"] >= cpcv.DL_GATE_DSR)
