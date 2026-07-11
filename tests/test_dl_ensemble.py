# tests/test_dl_ensemble.py
# 统一 DL 集成层门禁:多源 z 混合 + 总权重封顶 + per-source 退化 + 单源与旧 b3 字节等价。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.dl_ensemble import dl_mix_scores, MAX_TOTAL_DL_W  # noqa: E402
from guanlan_v2.strategy.compute.v4_fincast import b3_mix_scores  # noqa: E402


def _mk(n, seed):
    rng = np.random.RandomState(seed)
    idx = [f"SZ{300000 + i:06d}" for i in range(n)]
    return pd.Series(rng.randn(n), index=idx)


def test_single_source_byte_equivalent_to_b3():
    lgb = _mk(200, 1); fc = _mk(200, 2)
    b3_mixed, _ = b3_mix_scores(lgb, fc, w_fc=0.3)
    dl_mixed, info = dl_mix_scores(lgb, {"fincast": fc}, {"fincast": 0.3})
    assert np.allclose(b3_mixed.values, dl_mixed.values, atol=1e-12)
    assert info["active"] is True
    assert abs(info["w_lgb"] - 0.7) < 1e-12


def test_two_sources_weights_sum():
    lgb = _mk(200, 1); a = _mk(200, 2); b = _mk(200, 3)
    _, info = dl_mix_scores(lgb, {"a": a, "b": b}, {"a": 0.2, "b": 0.2})
    assert info["active"] is True
    assert abs(info["w_lgb"] - 0.6) < 1e-9
    ws = {s["model_id"]: s["weight"] for s in info["sources"] if s["active"]}
    assert abs(ws["a"] - 0.2) < 1e-9 and abs(ws["b"] - 0.2) < 1e-9


def test_total_weight_capped():
    lgb = _mk(200, 1); a = _mk(200, 2); b = _mk(200, 3)
    _, info = dl_mix_scores(lgb, {"a": a, "b": b}, {"a": 0.4, "b": 0.4})  # 和 0.8 > 0.5
    assert abs(info["w_lgb"] - (1.0 - MAX_TOTAL_DL_W)) < 1e-9   # w_lgb = 0.5
    ws = {s["model_id"]: s["weight"] for s in info["sources"] if s["active"]}
    assert abs(ws["a"] - 0.25) < 1e-9 and abs(ws["b"] - 0.25) < 1e-9  # 各缩到 0.25


def test_source_below_min_match_drops_out():
    lgb = _mk(200, 1); good = _mk(200, 2)
    thin = _mk(200, 3); thin.iloc[10:] = np.nan   # 仅 10 个非空 < 50
    mixed, info = dl_mix_scores(lgb, {"good": good, "thin": thin}, {"good": 0.3, "thin": 0.3}, min_match=50)
    by = {s["model_id"]: s for s in info["sources"]}
    assert by["thin"]["active"] is False and by["thin"]["weight"] == 0.0
    assert by["good"]["active"] is True
    assert abs(info["w_lgb"] - 0.7) < 1e-9   # 只剩 good 0.3


def test_all_sources_degrade_returns_pure_lgb():
    lgb = _mk(200, 1)
    thin = _mk(200, 3); thin.iloc[5:] = np.nan
    mixed, info = dl_mix_scores(lgb, {"thin": thin}, {"thin": 0.3}, min_match=50)
    assert info["active"] is False and info["w_lgb"] == 1.0
    assert np.allclose(mixed.values, lgb.values)   # 纯 LGB,原样


def _write_pred(tmp_path, name, eval_date, codes, vals, score_col="pred_ret_5d"):
    df = pd.DataFrame({"eval_date": pd.Timestamp(eval_date), "instrument": codes, score_col: vals})
    p = tmp_path / name
    df.to_parquet(p)
    return str(p)


def _mk_pred_frame(codes, scores):
    idx = pd.MultiIndex.from_product([codes, [pd.Timestamp("2026-03-10")]],
                                     names=["instrument", "datetime"])
    return pd.DataFrame({"score": scores}, index=idx)


def test_apply_dl_ensemble_single_source_writes_mixed(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    codes = [f"SZ{300000 + i:06d}" for i in range(120)]
    rng = np.random.RandomState(7)
    pred = _mk_pred_frame(codes, rng.randn(120))
    before = pred["score"].copy()
    path = _write_pred(tmp_path, "dl_pred_fincast.parquet", "2026-03-10", codes, rng.randn(120))
    src = DLSource(model_id="fincast", path=path, weight_mode="fixed", fixed_w=0.3)
    info = apply_dl_ensemble(pred, pd.Timestamp("2026-03-10"), [src])
    assert info["active"] is True
    assert info["sources"][0]["model_id"] == "fincast" and info["sources"][0]["active"] is True
    assert not np.allclose(pred["score"].values, before.values)   # score 被混合改写


def test_apply_dl_ensemble_missing_file_pure_lgb(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    codes = [f"SZ{300000 + i:06d}" for i in range(120)]
    pred = _mk_pred_frame(codes, np.arange(120, dtype=float))
    before = pred["score"].copy()
    src = DLSource(model_id="fincast", path=str(tmp_path / "__nope__.parquet"))
    info = apply_dl_ensemble(pred, pd.Timestamp("2026-03-10"), [src])
    assert info["active"] is False
    assert info["sources"][0]["active"] is False
    assert np.allclose(pred["score"].values, before.values)   # 纯 LGB,原样


def test_apply_dl_ensemble_equiv_to_apply_fincast(tmp_path):
    # 单 fincast 源(fixed_w)经新层 == 旧 apply_fincast_ensemble(同 w)
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    from guanlan_v2.strategy.compute.v4_fincast import apply_fincast_ensemble, DEFAULT_W_FC
    codes = [f"SZ{300000 + i:06d}" for i in range(120)]
    rng = np.random.RandomState(11)
    fcvals = rng.randn(120)
    path = _write_pred(tmp_path, "v4_fincast_pred.parquet", "2026-03-10", codes, fcvals)
    base = rng.randn(120)
    p1 = _mk_pred_frame(codes, base.copy()); p2 = _mk_pred_frame(codes, base.copy())
    apply_fincast_ensemble(p1, pd.Timestamp("2026-03-10"), path)   # 旧:无 data → DEFAULT_W_FC=0.4
    apply_dl_ensemble(p2, pd.Timestamp("2026-03-10"),
                      [DLSource(model_id="fincast", path=path, weight_mode="fixed", fixed_w=DEFAULT_W_FC)])
    assert np.allclose(p1["score"].values, p2["score"].values, atol=1e-12)


def test_build_v4_signature_has_dl_sources():
    import inspect
    from guanlan_v2.strategy.compute import v4
    sig = inspect.signature(v4.build_v4)
    assert "dl_sources" in sig.parameters


def test_default_dl_sources_includes_lstm():
    from guanlan_v2.strategy.compute.dl_ensemble import default_dl_sources
    srcs = default_dl_sources()
    ids = {s.model_id for s in srcs}
    assert "fincast" in ids and "lstm" in ids
    lstm = next(s for s in srcs if s.model_id == "lstm")
    assert lstm.path.endswith("dl_pred_lstm.parquet")
    assert lstm.score_col == "pred_ret_5d" and lstm.weight_mode == "adaptive"


def test_default_dl_sources_includes_gat():
    from guanlan_v2.strategy.compute.dl_ensemble import default_dl_sources
    srcs = default_dl_sources()
    ids = {s.model_id for s in srcs}
    assert {"fincast", "lstm", "gat"} <= ids        # gat 已注册,且不挤掉 fincast/lstm
    gat = next(s for s in srcs if s.model_id == "gat")
    assert gat.path.endswith("dl_pred_gat.parquet")
    assert gat.score_col == "pred_ret_5d" and gat.weight_mode == "adaptive"


def test_apply_dl_ensemble_gat_absent_byte_equivalent(tmp_path):
    # 加 gat(parquet 缺失)不扰动:有效源集合 / score / w_lgb 与不加 gat 完全一致
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    codes = [f"SZ{300000 + i:06d}" for i in range(120)]
    rng = np.random.RandomState(7)
    base = rng.randn(120)
    fc_path = _write_pred(tmp_path, "v4_fincast_pred.parquet", "2026-03-10", codes, rng.randn(120))
    fc = DLSource(model_id="fincast", path=fc_path, weight_mode="fixed", fixed_w=0.3)
    gat = DLSource(model_id="gat", path=str(tmp_path / "__no_gat__.parquet"),
                   score_col="pred_ret_5d", weight_mode="adaptive")
    p1 = _mk_pred_frame(codes, base.copy()); p2 = _mk_pred_frame(codes, base.copy())
    info1 = apply_dl_ensemble(p1, pd.Timestamp("2026-03-10"), [fc])
    info2 = apply_dl_ensemble(p2, pd.Timestamp("2026-03-10"), [fc, gat])
    assert np.allclose(p1["score"].values, p2["score"].values, atol=1e-12)
    assert abs(info1["w_lgb"] - info2["w_lgb"]) < 1e-12
    by = {s["model_id"]: s for s in info2["sources"]}
    assert by["gat"]["active"] is False              # gat 缺文件 → 诚实退出


def test_load_dl_for_date_cutoff_is_scored_dates_own(tmp_path):
    # 多日累积表:每日带自己的 train_cutoff;查某 eval_date 应取该日自己的 cutoff(非全表最旧日)
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    from guanlan_v2.strategy.compute.fincast_io import write_pred_rolling
    p = str(tmp_path / "dl_pred_gat.parquet")
    write_pred_rolling(p, "2026-01-10", ["A", "B"], [0.1, 0.2], keep_days=60, train_cutoff="2026-01-03")
    write_pred_rolling(p, "2026-01-20", ["A", "B"], [0.3, 0.4], keep_days=60, train_cutoff="2026-01-13")
    _, _, cutoff_late, _stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-01-20"))
    assert fail is None and cutoff_late == "2026-01-13"   # 取被评分日自己的 cutoff,非最旧 2026-01-03
    _, _, cutoff_early, _stale2, _ = _load_dl_for_date(p, pd.Timestamp("2026-01-10"))
    assert cutoff_early == "2026-01-03"


def _mk_pred(tmp_path, rows):
    """rows: list[(eval_date_str, instrument, pred)] → parquet 路径。"""
    p = str(tmp_path / "dl_pred_x.parquet")
    df = pd.DataFrame(rows, columns=["eval_date", "instrument", "pred_ret_5d"])
    df["eval_date"] = pd.to_datetime(df["eval_date"])
    df.to_parquet(p, index=False)
    return p


def test_load_dl_stale_within_window(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-06-30", "SH600000", 0.01), ("2026-06-30", "SZ000001", -0.02)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert fail is None and stale == 2                     # 旧 2 交易日(busday 兜底),窗内(≤3)
    assert abs(float(s["SH600000"]) - 0.01) < 1e-9 and len(s) == 2   # 用的是最近一期截面


def test_load_dl_stale_beyond_window(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-06-25", "SH600000", 0.01)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert s is None and "断供" in fail and "5" in fail     # 旧 5 交易日 > 3 → 诚实断供


def test_load_dl_stale_trading_days_across_holiday(tmp_path):
    """长假核心修:自然日 9 天但真日历下交易日距离 1 → 窗内不误报断供(原自然日≤4 必炸)。"""
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-09-30", "SH600000", 0.01), ("2026-09-30", "SZ000001", 0.02)])
    cal = pd.to_datetime(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-09"])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-10-09"), trade_cal=cal)
    assert fail is None and stale == 1 and len(s) == 2


def test_load_dl_no_cal_busday_fallback_is_conservative(tmp_path):
    """缺日历 → busday 工作日近似:跨长假(工作日 7 > 3)诚实断供(偏保守,不冒充有日历)。"""
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-09-30", "SH600000", 0.01)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-10-09"))
    assert s is None and "断供" in fail


def test_load_dl_same_day_stale_zero(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-07-02", "SH600000", 0.03)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert fail is None and stale == 0 and abs(float(s["SH600000"]) - 0.03) < 1e-9


def test_apply_dl_ensemble_stale_days_in_sources(tmp_path):
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    p = _mk_pred(tmp_path, [("2026-06-30", f"SH{600000+k}", 0.001 * k) for k in range(60)])  # 60 只 ≥ MIN_MATCH
    idx = pd.MultiIndex.from_tuples([(f"SH{600000+k}", pd.Timestamp("2026-07-02")) for k in range(60)],
                                    names=["instrument", "datetime"])
    pred = pd.DataFrame({"score": [float(k) for k in range(60)]}, index=idx)
    info = apply_dl_ensemble(pred, pd.Timestamp("2026-07-02"),
                             [DLSource(model_id="x", path=p, weight_mode="fixed", fixed_w=0.3)])
    src = next(s for s in info["sources"] if s["model_id"] == "x")
    assert src["active"] is True and src["stale_days"] == 2 and "旧2交易日" in src["reason"]


def test_apply_dl_ensemble_derives_trade_cal_from_panel(tmp_path):
    """apply_dl_ensemble 从面板 datetime 层现算交易日历(bins 口径零新依赖):
    跨长假(自然 9 日)DL 预测按真日历距离 1 交易日 → 仍窗内活跃。"""
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    codes = [f"SH{600000 + k}" for k in range(60)]                   # 60 只 ≥ MIN_MATCH
    p = _mk_pred(tmp_path, [("2026-09-30", c, 0.001 * i) for i, c in enumerate(codes)])
    idx = pd.MultiIndex.from_tuples([(c, pd.Timestamp("2026-10-09")) for c in codes],
                                    names=["instrument", "datetime"])
    pred = pd.DataFrame({"score": [float(k) for k in range(60)]}, index=idx)
    d_idx = pd.MultiIndex.from_product(                              # 面板:节前节后交易日,长假无行
        [codes, pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-09"])],
        names=["instrument", "datetime"])
    data = pd.DataFrame({"close": 1.0}, index=d_idx)
    info = apply_dl_ensemble(pred, pd.Timestamp("2026-10-09"),
                             [DLSource(model_id="x", path=p, weight_mode="fixed", fixed_w=0.3)],
                             data=data)
    src = next(s for s in info["sources"] if s["model_id"] == "x")
    assert src["active"] is True and src["stale_days"] == 1          # 真日历:1 交易日,不误报断供
