# -*- coding: utf-8 -*-
"""引擎原生「v4 排名」(L1)—— 把 qlib 版 v4_ranking.py 迁到引擎二进制读取。

**不依赖 qlib 包**(py3.13 可跑)。GPU-LGB 非确定性 → 目标是**统计等价**
(top-200 重合度 + lgb_pct 秩相关),非逐位。

复刻 v4_ranking.py 的:
* 38 个 qlib 因子表达式(Ref/Mean/Std/Max/Min/Corr/If/Abs)—— 逐个在 pandas 单股序列上实现;
* ind_turnover(行业平均换手,迁自 v4 + 行业映射)、market_breadth 残差 broadcast(复用已迁 breadth);
* label = 未来 5 日收益 Ref(close,-6)/Ref(close,-1)-1;
* LGB 训练(同超参,device 默认 cpu)→ 预测末日 score;
* 自适应因子择时 + final_score;顶 200 五维评分(factor/technical/model/volume/utility);
* 导出 7 列:code/lgb_score/lgb_pct/lgb_rank/v4_total/v4_layer/date(== v4_ranking_latest.parquet)。

同 breadth/mainline 的保真课:close/total_mv 走 float32 复刻 qlib dtype;ffill 复牌。
FinCast(B3 集成)默认关(qlib 无当日 FinCast 预测时同样退化纯 LGB)。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from guanlan_v2.strategy.paths import STOCK_BASIC_PARQUET, MARKET_BREADTH_PARQUET
from guanlan_v2.strategy.compute.units import normalize_frame_units

START_DEFAULT = "2022-01-01"

# 自适应择时因子 + 方向(照搬 v4_ranking.py)
TF = ["rev_20", "amt_cv", "updown_vol_ratio", "vol_dry", "breakout_20", "stock_sharpe_60", "vol_20"]
TD = {"rev_20": +1, "amt_cv": -1, "updown_vol_ratio": -1, "vol_dry": +1,
      "breakout_20": -1, "stock_sharpe_60": -1, "vol_20": -1}

LGB_PARAMS = {
    "objective": "regression", "metric": "mse", "device": "cpu",
    "learning_rate": 0.03, "num_leaves": 128, "max_depth": 7,
    "subsample": 0.85, "colsample_bytree": 0.85, "lambda_l1": 10,
    "lambda_l2": 50, "min_child_samples": 100, "verbose": -1, "seed": 42,
}
UTILITY_KW = ("电力", "水务", "燃气", "公用", "高速", "机场", "港口", "供热", "环保")


# ── qlib 单股算子(单调序列,index=datetime)──────────────────────────────────
def _ref(x, n):
    return x.shift(n)


def _mean(x, n):
    return x.rolling(n, min_periods=1).mean()


def _std(x, n):
    return x.rolling(n, min_periods=1).std()


def _rmax(x, n):
    return x.rolling(n, min_periods=1).max()


def _rmin(x, n):
    return x.rolling(n, min_periods=1).min()


def _corr(x, y, n):
    return x.rolling(n, min_periods=1).corr(y)


def _if(cond, a, b):
    return pd.Series(np.where(cond, a, b), index=cond.index)


def _factors_one(df: pd.DataFrame) -> pd.DataFrame:
    """单股 OHLCV+基本面 → 38 列因子(含 label)。df index=datetime,float32 close。"""
    c, o, h, low = df["close"], df["open"], df["high"], df["low"]
    v, amt = df["volume"], df["amount"]
    pe, pb, mv = df["pe_ttm"], df["pb"], df["total_mv"]
    to, ps, dv = df["turnover_rate"], df["ps_ttm"], df["dv_ttm"]
    c1 = _ref(c, 1)
    dret = c / c1 - 1  # 日收益

    out = pd.DataFrame(index=df.index)
    out["rev_5"] = _ref(c, 5) / c - 1
    out["rev_10"] = _ref(c, 10) / c - 1
    out["rev_20"] = _ref(c, 20) / c - 1
    out["vol_ratio_5_20"] = _mean(v, 5) / (_mean(v, 20) + 1e-8)
    out["volatility_20"] = _std(dret, 20)
    out["amihud_20"] = _mean((dret).abs() / (v + 1e-8), 20)
    out["bias_ma20"] = c / _mean(c, 20) - 1
    out["bias_ma60"] = c / _mean(c, 60) - 1
    gain = _if(c > c1, c - c1, 0.0)
    out["rsi_approx"] = _mean(gain, 14) / (_mean((c - c1).abs(), 14) + 1e-8)
    out["avg_amplitude_20"] = _mean((h - low) / (c + 1e-8), 20)
    out["amount_ratio_5_20"] = _mean(amt, 5) / (_mean(amt, 20) + 1e-8)
    out["bias_ma5"] = c / _mean(c, 5) - 1
    out["vol_trend_5_60"] = _mean(v, 5) / (_mean(v, 60) + 1e-8)
    out["corr_close_vol_20"] = _corr(c, v, 20)
    out["close_pos"] = (c - low) / (h - low + 1e-8)
    out["pe_ttm"] = pe
    out["pb"] = pb
    out["total_mv"] = mv
    out["amt_cv"] = _std(amt, 20) / (_mean(amt, 20) + 1e-8)
    out["big_up_freq"] = _mean(_if(dret > 0.03, 1.0, 0.0), 20)
    out["gap_dn_freq"] = _mean(_if(o < c1 * 0.99, 1.0, 0.0), 20)
    out["obv_slope"] = _mean(_if(c > c1, v, 0.0), 20) - _mean(_if(c < c1, v, 0.0), 20)
    out["big_dn_minus_up"] = _mean(_if(dret < -0.03, 1.0, 0.0), 20) - _mean(_if(dret > 0.03, 1.0, 0.0), 20)
    out["breakout_20"] = _if(c >= _rmax(h, 20), 1.0, 0.0)
    out["new_high_freq"] = _mean(_if(c >= _rmax(h, 5), 1.0, 0.0), 20)
    out["pullback_3d"] = _if(c / _ref(c, 20) - 1 > 0, _ref(c, 3) / c - 1, 0.0)
    out["quiet_dip"] = (1 - _mean(v, 5) / (_mean(v, 20) + 1e-8)) * (1 + c / _ref(c, 5) - 1)
    out["vol_dry"] = _rmin(v, 20) / (_mean(v, 20) + 1e-8)
    up_ret = _if(c > c1, dret, 0.0)
    dn_ret = _if(c < c1, dret, 0.0)
    out["updown_vol_ratio"] = _std(up_ret, 20) / (_std(dn_ret, 20) + 1e-8)
    out["max_gain_20"] = _rmax(dret, 20)
    out["vol_spike"] = _rmax(v, 20) / (_mean(v, 20) + 1e-8)
    out["price_density_20"] = _mean((h - low) / (c + 1e-8), 20)
    win = _if(c > c1, dret, 0.0)
    loss = _if(c < c1, c1 / c - 1, 0.0)
    out["win_loss_ratio"] = _mean(win, 20) / (_mean(loss, 20) + 1e-8)
    out["stock_sharpe_60"] = _mean(dret, 60) / (_std(dret, 60) + 1e-8)
    out["label"] = _ref(c, -6) / _ref(c, -1) - 1
    out["turnover_pct_60"] = to / (_mean(to, 60) + 1e-8) - 1
    out["ps_ttm_raw"] = ps
    out["dv_ttm"] = dv
    return out


def build_feature_panel(loader, codes: List[str], start: str, end: str) -> pd.DataFrame:
    """逐股读 9+3 字段 → 38 列因子面板,MultiIndex (instrument, datetime)。"""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    fields = {
        "close": "close", "open": "open", "high": "high", "low": "low",
        "volume": "volume", "amount": "amount", "pe_ttm": "pe_ttm", "pb": "pb",
        "total_mv": "total_mv", "turnover_rate": "turnover_rate",
        "ps_ttm": "ps_ttm", "dv_ttm": "dv_ttm",
    }
    frames = []
    for code in codes:
        cols = {}
        for out_name, bin_name in fields.items():
            s = loader._read_bin(code, bin_name)
            if s is not None:
                cols[out_name] = s
        if "close" not in cols:
            continue
        df = pd.DataFrame(cols)
        for need in fields:
            if need not in df.columns:
                df[need] = np.nan
        df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        if len(df) < 60:
            continue
        # 量纲校准(2026-06-12):污染批次 vol=手/amount=千元 → 校准为 股/元,
        # 否则 vol_trend/amihud/obv 等量价因子跨批次失真(见 compute/units.py)
        df = normalize_frame_units(df, vol_col="volume")
        # float32 复刻 qlib dtype(close→涨停/动量舍入,total_mv→log_mv);ffill 复牌
        df["close"] = df["close"].astype("float32").ffill()
        df["total_mv"] = df["total_mv"].astype("float32")
        fac = _factors_one(df)
        fac["instrument"] = code
        fac = fac.set_index("instrument", append=True)
        frames.append(fac)

    if not frames:
        raise RuntimeError("build_feature_panel: 无可读股票")
    data = pd.concat(frames, axis=0)
    data.index = data.index.set_names(["datetime", "instrument"])
    data = data.reorder_levels(["instrument", "datetime"]).sort_index()
    data["log_mv"] = np.log(data["total_mv"].clip(lower=1) + 1)
    data["pe_clip"] = data["pe_ttm"].clip(-200, 500)
    data["ps_clip"] = data["ps_ttm_raw"].clip(-100, 200)
    return data


def add_ind_turnover(data: pd.DataFrame, loader, codes: List[str],
                     start: str, end: str, imap_path: Optional[Path] = None) -> pd.DataFrame:
    """行业平均换手率(date×industry 的 turnover 均值,回填个股)。"""
    from guanlan_v2.strategy.compute.mainline import load_stock_industry_map
    imap = load_stock_industry_map(imap_path)
    ind_of = dict(zip(imap["instrument"], imap["industry"]))

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    tos = []
    for code in codes:
        s = loader._read_bin(code, "turnover_rate")
        if s is None:
            continue
        s = s.loc[(s.index >= start_ts) & (s.index <= end_ts)]
        if s.empty:
            continue
        d = s.to_frame("turnover").astype("float64")
        d["instrument"] = code
        d["industry"] = ind_of.get(code)
        tos.append(d)
    tdf = pd.concat(tos, axis=0)
    tdf.index.name = "datetime"
    tdf = tdf.reset_index()
    tdf["ind_turnover"] = tdf.groupby(["datetime", "industry"])["turnover"].transform("mean")
    key = tdf.set_index(["instrument", "datetime"])["ind_turnover"]
    data["ind_turnover"] = key.reindex(data.index)
    data["ind_turnover"] = data["ind_turnover"].fillna(0.0)
    return data


def add_breadth_resid(data: pd.DataFrame, resid_path: Optional[Path] = None) -> pd.DataFrame:
    """broadcast lu_resid_pct60 / amt_resid_pct60(读已迁 breadth 残差;按日 ffill)。"""
    p = Path(resid_path) if resid_path else MARKET_BREADTH_PARQUET
    br = pd.read_parquet(p)
    dts = data.index.get_level_values("datetime")
    for col in ("lu_resid_pct60", "amt_resid_pct60"):
        series = br[col].reindex(pd.Index(dts.unique())).ffill()
        data[col] = dts.map(series)
    return data


def build_v4(provider_uri: str, start: str = START_DEFAULT, end: str = "2026-06-05",
             codes: Optional[List[str]] = None, date_str: Optional[str] = None,
             health: Optional[dict] = None,
             fincast_path: Optional[str] = None, b3: Optional[dict] = None) -> pd.DataFrame:
    """引擎原生 v4 排名 → 7 列(== v4_ranking_latest.parquet)。

    ``health``(可选出参,模型体检):传入 dict 时,顺带用**刚训完的同一模型**对近 ~60 个
    有标签交易日(标签=真实未来5日收益)逐日算截面 rank-IC,填 ``health["ic_series"]``
    =[(date,ic)...]。⚠ 这些日子在训练窗内 → 偏乐观,仅作衰减趋势监控(model_health.py 落盘
    并标注口径)。默认 None = 行为与迁移验证版逐位一致,排名输出不受任何影响。"""
    import lightgbm as lgb
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from guanlan_v2.strategy.compute.breadth import list_all_instruments

    loader = QlibBinaryLoader(provider_uri)
    if codes is None:
        codes = list_all_instruments(provider_uri)

    data = build_feature_panel(loader, codes, start, end)
    data = add_ind_turnover(data, loader, codes, start, end)
    data = add_breadth_resid(data)

    name_map = _load_name_map()

    mf = [x for x in data.columns if x not in ("label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw")]
    dates = data.index.get_level_values("datetime")
    ld = dates.max()
    from datetime import timedelta
    train = data[(dates >= pd.Timestamp("2022-01-01")) &
                 (dates <= ld - timedelta(days=5))].dropna(subset=["label"]).copy()
    pred = data[dates == ld].copy()
    train[mf] = train[mf].fillna(0)
    pred[mf] = pred[mf].fillna(0)

    dt_train = lgb.Dataset(train[mf].values, label=train["label"].values)
    model = lgb.train(LGB_PARAMS, dt_train, num_boost_round=500)
    pred["score"] = model.predict(pred[mf].values)

    # ─── #7 B3 集成:LGB + FinCast(离线 GPU 批算产出的 parquet,只读;有当日预测且匹配≥50→z-score
    #     加权混合进 score,无则诚实退纯 LGB)。同 vendor/v4_ranking.py:195-273 口径。**命门**:本处只
    #     pd.read_parquet,绝不跑 GPU 模型;build_v4 由 regen 离线再生时调用(非请求路径)。
    #     fincast_path=None(默认)→ 整块跳过 = 与「FinCast 默认关」字节等价(向后兼容)。
    if fincast_path:
        try:
            from guanlan_v2.strategy.compute.v4_fincast import apply_fincast_ensemble
            _b3info = apply_fincast_ensemble(pred, ld, fincast_path, data=data)
            if b3 is not None:
                b3.update(_b3info)
            print(f"[v4] B3: {_b3info.get('reason')}", flush=True)
        except Exception as _e:  # noqa: BLE001 — B3 异常绝不拖垮排名,退纯 LGB
            if b3 is not None:
                b3.update({"active": False, "reason": f"B3 异常退纯 LGB:{type(_e).__name__}: {_e}"})

    # —— 模型体检(可选出参,不影响排名输出)——
    # 同模型对近 ~75 个有标签日逐日截面 rank-IC(score vs 真实未来5日收益);训练窗内,偏乐观,
    # 只作衰减趋势监控。失败只记 error 不抛(体检绝不拖垮排名再生)。
    if health is not None:
        try:
            hist_dates = sorted(d for d in dates.unique() if d < ld)[-75:]
            hd = data[dates.isin(hist_dates)].dropna(subset=["label"]).copy()
            hd_scores = model.predict(hd[mf].fillna(0).values)
            hd = hd.assign(_score=hd_scores)
            ics = []
            for d_, g in hd.groupby(level="datetime"):
                if len(g) >= 50:
                    ic_d = g["_score"].rank().corr(g["label"].rank())
                    if pd.notna(ic_d):
                        ics.append((str(pd.Timestamp(d_).date()), float(ic_d)))
            health["ic_series"] = ics[-60:]
        except Exception as e:  # noqa: BLE001
            health["error"] = f"{type(e).__name__}: {e}"

    # 自适应因子择时(照搬)
    recent = sorted(dates[dates < ld].unique())[-20:]
    fw = {}
    for fn in TF:
        if fn not in data.columns:
            continue
        ics = []
        for rd in recent:
            try:
                dd = data.xs(rd, level="datetime")[[fn, "label"]].dropna()
                if len(dd) >= 30:
                    ics.append(dd[fn].rank().corr(dd["label"].rank()))
            except Exception:
                pass
        fw[fn] = max(0, np.mean(ics) * TD[fn]) if len(ics) >= 5 else 0
    tw = sum(fw.values())
    if tw > 0:
        fw = {k: v / tw for k, v in fw.items()}
    else:
        fw = {k: 1.0 / len(TF) for k in TF if k in data.columns}
    ascore = pd.Series(0.0, index=pred.index)
    for fn, w in fw.items():
        if fn not in pred.columns or w == 0:
            continue
        p = pred[fn].rank(pct=True)
        if TD[fn] < 0:
            p = 1 - p
        ascore += w * p
    pred["adaptive"] = ascore
    pred["final_score"] = 0.5 * pred["score"].rank(pct=True) + 0.5 * pred["adaptive"].rank(pct=True)

    # 末日收盘价(qlib 用 close_latest 单独 join,不入模型特征)→ 仅给 top200 价格过滤用
    cl = {}
    for code in pred.index.get_level_values("instrument").unique():
        s = loader._read_bin(code, "close")
        if s is not None and len(s):
            cl[code] = float(s.iloc[-1])
    pred["close"] = [cl.get(i, np.nan) for i in pred.index.get_level_values("instrument")]

    df_top = _score_top200(pred, name_map)

    # 导出 7 列(== v4_ranking_latest.parquet)
    pred_codes = list(pred.index.get_level_values("instrument"))
    rank = pd.DataFrame({"code": pred_codes, "lgb_score": pred["score"].values}).dropna(subset=["lgb_score"])
    rank["lgb_pct"] = rank["lgb_score"].rank(pct=True)
    rank = rank.sort_values("lgb_score", ascending=False).reset_index(drop=True)
    rank["lgb_rank"] = range(1, len(rank) + 1)
    v4 = df_top[["code", "total", "layer"]].rename(columns={"total": "v4_total", "layer": "v4_layer"})
    out = rank.merge(v4, on="code", how="left")
    out["date"] = (date_str or (ld.strftime("%Y-%m-%d") if hasattr(ld, "strftime") else str(ld)[:10]))
    return out


def _load_name_map() -> dict:
    try:
        df = pd.read_parquet(STOCK_BASIC_PARQUET)[["ts_code", "name"]]
        out = {}
        for r in df.itertuples():
            ts = str(r.ts_code)
            if "." in ts:
                num, mk = ts.split(".")
                out[f"{mk}{num}"] = str(r.name)
        return out
    except Exception:
        return {}


def _score_top200(pred: pd.DataFrame, name_map: dict) -> pd.DataFrame:
    """顶 200 五维评分(factor/technical/model/volume/utility),照搬 v4_ranking.py。"""
    pred = pred.copy()
    pred["mv_billion"] = pred["total_mv"] / 1e4
    codes = pred.index.get_level_values("instrument")
    names = [name_map.get(c, "?") for c in codes]
    st_codes = {c for c, nm in zip(codes, names) if "ST" in nm}
    mask = ((pred["mv_billion"] > 30) & (pred["close"] > 3) & (pred["close"] < 500) &
            pred["score"].notna() & (~pd.Series(codes, index=pred.index).isin(st_codes)))
    filtered = pred[mask].sort_values("final_score", ascending=False).copy()

    results = []
    for idx, row in filtered.head(200).iterrows():
        code = idx[0] if isinstance(idx, tuple) else idx
        name = name_map.get(code, "?")
        mv = row["mv_billion"]
        utility = any(kw in name for kw in UTILITY_KW)
        if mv >= 1000:
            mv_layer, fc, mc = "大盘", 0, 1
        elif mv >= 300:
            mv_layer, fc, mc = "中盘", 1, 2
        elif mv >= 100:
            mv_layer, fc, mc = "中小", 2, 2
        else:
            mv_layer, fc, mc = "小盘", 2, 2

        r20p = (pred["rev_20"] < row["rev_20"]).mean() if pd.notna(row.get("rev_20")) else 0.5
        acp = (pred["amt_cv"] < row["amt_cv"]).mean() if pd.notna(row.get("amt_cv")) else 0.5
        vp = (pred["volatility_20"] < row["volatility_20"]).mean() if pd.notna(row.get("volatility_20")) else 0.5
        bp = (pred["big_up_freq"] < row["big_up_freq"]).mean() if pd.notna(row.get("big_up_freq")) else 0.5
        fs = 0
        if r20p > 0.7: fs += 1
        if r20p < 0.3: fs -= 1
        if acp < 0.3: fs += 1
        if acp > 0.7: fs -= 1
        if vp < 0.3: fs += 1
        if vp > 0.7: fs -= 1
        if bp < 0.3: fs += 0.5
        if bp > 0.7: fs -= 0.5
        fs = int(max(-fc, min(fc, round(fs))))

        rsi = row.get("rsi_approx", 0.5)
        ts = 0
        if rsi < 0.3: ts += 1
        if rsi > 0.7: ts -= 1
        b20 = row.get("bias_ma20", 0)
        if b20 < -0.05: ts += 0.5
        if b20 > 0.05: ts -= 0.5
        ts = int(max(-2, min(2, round(ts))))

        rp = (pred["score"] > row["score"]).mean()
        if rp < 0.05: ms = 2
        elif rp < 0.15: ms = 2
        elif rp < 0.3: ms = 1
        elif rp < 0.5: ms = 1
        elif rp < 0.7: ms = 0
        elif rp < 0.85: ms = -1
        else: ms = -2
        ms = max(-mc, min(mc, ms))

        vs = 0
        vr = row.get("vol_ratio_5_20", 1)
        if vr < 0.7: vs = 1
        elif vr > 1.5: vs = -1

        ud = -1 if utility else 0
        total = fs + ts + ms + vs + ud
        results.append({"code": code, "layer": mv_layer, "total": total})

    if not results:
        return pd.DataFrame(columns=["code", "layer", "total"])
    return pd.DataFrame(results).sort_values("total", ascending=False)
