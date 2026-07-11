# -*- coding: utf-8 -*-
"""T2 变体附着五维评级 —— ``model_workflow._attach_v4_dims`` + train_promote 7 列契约测试。

契约:
  · rank_df(code/date/lgb_score/lgb_pct/lgb_rank)join prod dims 侧产物
    v4_dims_latest.parquet(code/layer/mc/fs/ts/vs/ud/eligible/date)→ 7 列 == V4_COLUMNS;
  · ms = rp=1-lgb_pct 阈值表(同 _score_top200),封 ±mc(大盘 mc=1 → rp<0.05 得 1 不是 2);
  · 只评变体自己的前 200(按 lgb_pct 降序);非前200 / 不在 dims / ineligible → NaN;
  · dims 缺失 / 日期不匹配 / 文件损坏 → (原样, {attached:False, reason})绝不抛——
    附着失败绝不 fail 训练(诚实降级为无评级榜)。
**注意**:全部合成 DataFrame/parquet,绝不真跑训练(_materialize_xy 要引擎数据)。
"""
import numpy as np
import pandas as pd
import pytest

from guanlan_v2.strategy import paths
from guanlan_v2.strategy.compute import model_workflow as mw
from guanlan_v2.strategy.ranking import V4_COLUMNS


def _rank_df(codes, pcts, date="2026-07-10"):
    """合成变体排名(train_promote 出 rank_df 的 5 列形态);score 与 pct 同序即可。"""
    df = pd.DataFrame({"code": codes, "date": date,
                       "lgb_score": pcts, "lgb_pct": pcts})
    df["lgb_rank"] = df["lgb_score"].rank(ascending=False, method="first").astype(int)
    return df


def _dims_df(rows, date="2026-07-10"):
    """合成 dims 侧产物;rows=[(code, layer, mc, fs, ts, vs, ud, eligible), ...]。"""
    df = pd.DataFrame(rows, columns=["code", "layer", "mc", "fs", "ts", "vs", "ud", "eligible"])
    df["eligible"] = df["eligible"].astype(bool)
    df["date"] = date
    return df


# ── 诚实降级三态:缺文件 / 跨日 / 损坏 ───────────────────────────────────────

def test_attach_missing_dims_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", tmp_path / "v4_dims_latest.parquet")
    rank = _rank_df(["SH600000"], [0.5])
    out, info = mw._attach_v4_dims(rank)
    assert info["attached"] is False and "缺失" in info["reason"]
    pd.testing.assert_frame_equal(out, rank)          # 原样返回,一列不多


def test_attach_date_mismatch_refuses(tmp_path, monkeypatch):
    """dims 日期 ≠ 变体排名日期 → 拒绝附着(绝不跨日冒充),reason 含两个日期。"""
    p = tmp_path / "v4_dims_latest.parquet"
    _dims_df([("SH600000", "中盘", 2, 1, 0, 0, 0, True)], date="2026-07-09").to_parquet(p, index=False)
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", p)
    rank = _rank_df(["SH600000"], [0.5], date="2026-07-10")
    out, info = mw._attach_v4_dims(rank)
    assert info["attached"] is False
    assert "2026-07-09" in info["reason"] and "2026-07-10" in info["reason"]
    pd.testing.assert_frame_equal(out, rank)


def test_attach_corrupt_parquet_never_raises(tmp_path, monkeypatch):
    """dims 文件损坏 → attached False + reason(异常类型显形),绝不抛出拖垮训练。"""
    p = tmp_path / "v4_dims_latest.parquet"
    p.write_bytes(b"not a parquet file at all")
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", p)
    rank = _rank_df(["SH600000"], [0.5])
    out, info = mw._attach_v4_dims(rank)               # 不抛即胜利
    assert info["attached"] is False and info["reason"]
    pd.testing.assert_frame_equal(out, rank)


# ── 附着成功:逐值手算 ───────────────────────────────────────────────────────

def test_attach_hand_computed_values(tmp_path, monkeypatch):
    """覆盖:ms 全阈值档 / 大盘 mc=1 上下双向封顶 / eligible=False 绝不入评级 /
    不在 dims 的票 NaN / 列序 == V4_COLUMNS。"""
    p = tmp_path / "v4_dims_latest.parquet"
    _dims_df([
        # code   layer  mc fs ts vs ud  eligible
        ("C1", "大盘", 1, 0, 1, 0, 0, True),    # rp=0.04<0.05→ms=2 封 mc=1 → 1
        ("C2", "中盘", 2, 1, 0, 1, 0, True),    # rp=0.10<0.15→ms=2
        ("C3", "小盘", 2, -1, 0, 0, -1, True),  # rp=0.25<0.3→ms=1
        ("C4", "小盘", 2, 0, 0, 0, 0, False),   # ineligible:lgb_pct 最高也绝不评级
        ("C5", "中小", 2, 2, -1, -1, 0, True),  # rp=0.60<0.7→ms=0
        ("C7", "小盘", 2, 0, 0, 0, 0, True),    # rp=0.95≥0.85→ms=-2
        ("C8", "小盘", 2, 0, 0, 0, 0, True),    # rp=0.80<0.85→ms=-1
        ("C9", "大盘", 1, 0, 0, 0, 0, True),    # rp=0.99→ms=-2 封 -mc=-1 → -1
    ]).to_parquet(p, index=False)
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", p)

    # C6 在榜但不在 dims(inner merge 掉)→ NaN;C4 ineligible 且分位最高 → 仍 NaN
    rank = _rank_df(["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"],
                    [0.96, 0.90, 0.75, 0.99, 0.40, 0.85, 0.05, 0.20, 0.01])
    out, info = mw._attach_v4_dims(rank)
    assert info == {"attached": True, "dims_date": "2026-07-10", "n_rated": 7}
    assert list(out.columns) == list(V4_COLUMNS)       # 7 列且顺序 == prod 契约
    assert list(out["code"]) == list(rank["code"])     # left-merge 保持原榜行序

    got = out.set_index("code")
    # v4_total = fs+ts+ms+vs+ud(逐票手算)
    assert got.loc["C1", "v4_total"] == 0 + 1 + 1 + 0 + 0        # ms 被 mc=1 封顶
    assert got.loc["C2", "v4_total"] == 1 + 0 + 2 + 1 + 0
    assert got.loc["C3", "v4_total"] == -1 + 0 + 1 + 0 - 1
    assert got.loc["C5", "v4_total"] == 2 - 1 + 0 - 1 + 0
    assert got.loc["C7", "v4_total"] == 0 + 0 - 2 + 0 + 0
    assert got.loc["C8", "v4_total"] == 0 + 0 - 1 + 0 + 0
    assert got.loc["C9", "v4_total"] == 0 + 0 - 1 + 0 + 0        # ms=-2 被 -mc=-1 封底
    assert got.loc["C1", "v4_layer"] == "大盘" and got.loc["C5", "v4_layer"] == "中小"
    # eligible=False / 不在 dims → 无评级(NaN),诚实不冒充
    assert pd.isna(got.loc["C4", "v4_total"]) and pd.isna(got.loc["C4", "v4_layer"])
    assert pd.isna(got.loc["C6", "v4_total"]) and pd.isna(got.loc["C6", "v4_layer"])
    # 原榜五列逐位不动(附着只加列)
    for c in ("lgb_score", "lgb_pct", "lgb_rank", "date"):
        pd.testing.assert_series_equal(out[c], rank[c], check_names=True)


def test_attach_only_top200_rated(tmp_path, monkeypatch):
    """>200 只 eligible 候选 → 恰好前 200(按变体自身 lgb_pct 降序)有评级,其余 NaN。"""
    n = 250
    codes = [f"SZ{300000 + i:06d}" for i in range(n)]
    p = tmp_path / "v4_dims_latest.parquet"
    _dims_df([(c, "中盘", 2, 0, 0, 0, 0, True) for c in codes]).to_parquet(p, index=False)
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", p)

    pcts = [(i + 1) / n for i in range(n)]             # 升序:末尾 200 只分位最高
    out, info = mw._attach_v4_dims(_rank_df(codes, pcts))
    assert info["attached"] is True and info["n_rated"] == 200
    assert out["v4_total"].notna().sum() == 200
    got = out.set_index("code")
    assert pd.isna(got.loc[codes[49], "v4_total"])     # 第 50 名(升序)未入前200
    assert pd.notna(got.loc[codes[50], "v4_total"])    # 恰在门槛上(250-200=50)
    assert pd.notna(got.loc[codes[-1], "v4_total"])    # 分位最高必有评级


# ── train_promote 集成:7 列入库 + meta/res 带 v4_rating ─────────────────────

def _fake_training(monkeypatch, saved):
    """桩掉真训三件套(_materialize_xy/_build_model/_holdout_oos_ic)+ 捕获 save_variant。
    面板 30 日 × 20 只(600 行过 len(X)>=500 硬底线),末日 = 2026-01-30。"""
    import guanlan_v2.screen.model_registry as reg
    import guanlan_v2.workflow.api as wapi

    def fake_mat(body, universe, feats, start, end):
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2026-01-01", periods=30), [f"SH60{i:04d}" for i in range(20)]],
            names=["datetime", "code"])
        fe = pd.DataFrame({"f1": range(len(idx))}, index=idx, dtype="float64")
        lab = pd.Series(range(len(idx)), index=idx, dtype="float64")
        return (None, fe, lab, ["f1"])

    class _M:
        def fit(self, X, y):
            return self

        def predict(self, X):
            return np.arange(len(X), dtype="float64")

    monkeypatch.setattr(wapi, "_materialize_xy", fake_mat)
    monkeypatch.setattr(wapi, "_build_model", lambda k, p: (_M(), {}))
    monkeypatch.setattr(mw, "_holdout_oos_ic", lambda *a, **kw: 0.05)
    monkeypatch.setattr(reg, "save_variant",
                        lambda vid, df, meta: saved.update(df=df, meta=meta))


def test_train_promote_attaches_dims_seven_columns(tmp_path, monkeypatch):
    saved = {}
    _fake_training(monkeypatch, saved)
    codes = [f"SH60{i:04d}" for i in range(20)]
    p = tmp_path / "v4_dims_latest.parquet"
    _dims_df([(c, "中盘", 2, 1, 0, 0, 0, True) for c in codes],
             date="2026-01-30").to_parquet(p, index=False)   # 与面板末日相同 → 附着
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", p)

    res = mw.train_promote({"variant_id": "m_t2", "name": "t2", "kind": "lightgbm",
                            "recipe": {"features": ["f1"], "universe": "csi_fast",
                                       "start": "2026-01-01", "end": "2026-01-30"}})
    assert res["ok"] is True
    assert res["v4_rating"] == {"attached": True, "dims_date": "2026-01-30", "n_rated": 20}
    assert saved["meta"]["v4_rating"] == res["v4_rating"]     # meta 同步落库
    df = saved["df"]
    assert list(df.columns) == list(V4_COLUMNS)               # 7 列契约 + 顺序
    # lgb_score=原始预测值 / lgb_rank=降序名次:pred=arange → 末只最高分名次 1
    got = df.set_index("code")
    assert got.loc["SH600019", "lgb_score"] == 19.0 and got.loc["SH600019", "lgb_rank"] == 1
    assert got.loc["SH600000", "lgb_rank"] == 20
    # 20 只全 <200 → 全评级;最高分位 rp=0 → ms=2 → total=1+0+2+0+0=3
    assert got.loc["SH600019", "v4_total"] == 3
    assert got.loc["SH600000", "v4_total"] == 1 + 0 - 2 + 0 + 0   # rp=0.95 → ms=-2
    assert got["v4_layer"].eq("中盘").all()


def test_train_promote_dims_missing_still_ok(tmp_path, monkeypatch):
    """dims 缺失 → 训练照常成功(5 列入库),v4_rating 诚实 attached=False。"""
    saved = {}
    _fake_training(monkeypatch, saved)
    monkeypatch.setattr(paths, "V4_DIMS_PARQUET", tmp_path / "nope.parquet")
    res = mw.train_promote({"variant_id": "m_t2b", "name": "t2b", "kind": "rf",
                            "recipe": {"features": ["f1"], "universe": "csi_fast",
                                       "start": "2026-01-01", "end": "2026-01-30"}})
    assert res["ok"] is True
    assert res["v4_rating"]["attached"] is False and "缺失" in res["v4_rating"]["reason"]
    assert "v4_total" not in saved["df"].columns              # 不冒充有评级
    assert {"code", "date", "lgb_score", "lgb_pct", "lgb_rank"} <= set(saved["df"].columns)


def test_retrain_variant_passes_through_v4_rating(monkeypatch):
    """T2-4:retrain res.update 只增键 → train_promote 的 v4_rating 原样透传。"""
    import guanlan_v2.screen.model_registry as reg
    rating = {"attached": True, "dims_date": "2026-07-10", "n_rated": 200}
    monkeypatch.setattr(reg, "variant_meta", lambda v: {
        "id": v, "name": "n", "kind": "lightgbm", "retrainable": True, "asof": "2026-07-10",
        "recipe": {"features": ["f1"], "universe": "csi300", "end": "2026-04-01"}})
    monkeypatch.setattr(mw, "train_promote",
                        lambda spec: {"ok": True, "variant_id": spec["variant_id"],
                                      "oos_ic": 0.04, "v4_rating": dict(rating)})
    out = mw.retrain_variant("m_rt2")
    assert out["ok"] is True and out["v4_rating"] == rating
