# -*- coding: utf-8 -*-
"""T1 五维分项侧产物 —— ``compute_dims``(全截面四个非模型维)测试。

契约(``guanlan_v2.strategy.compute.v4.compute_dims``):
  · 把 ``_score_top200`` 的逐行五维规则**向量化推广到全截面**,返回
    code/layer/mc/fs/ts/vs/ud/eligible 八列(不含 ms —— model 维是变体自己的分位);
  · 分位语义与循环版逐位一致:``pctl = 严格小于个数 / 总行数 n``(NaN 行含分母),
    自身 NaN → 0.5;rsi/b20/vr 原始值比较,NaN 比较恒 False → 贡献 0;
  · ``build_v4(dims=None)`` 缺省 → 一行新代码都不执行(prod 排名逐字节不变是红线)。
**注意**:全部纯合成 DataFrame,不碰引擎二进制(G:/stocks)。
"""
import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.v4 import UTILITY_KW, build_v4, compute_dims

DIM_COLS = ["code", "layer", "mc", "fs", "ts", "vs", "ud", "eligible"]


# ── 手工小截面:逐值断言 ──────────────────────────────────────────────────────

def _manual_frame():
    """11 行手工截面(MultiIndex),覆盖:大盘 fc=0 封零 / NaN 因子→0.5 分位 /
    ST·小市值·低价·市值 NaN 四种 ineligible / 公用事业 ud / 银行家舍入 0.5→0。"""
    codes = [f"SH6000{i:02d}" for i in range(11)]
    dt = pd.Timestamp("2026-07-10")
    idx = pd.MultiIndex.from_product([codes, [dt]], names=["instrument", "datetime"])
    nan = np.nan
    df = pd.DataFrame({
        # rev_20 升序(行4=NaN):count_less/11 → 行0-3 <0.3 减1;行9-10 >0.7 加1;其余 0
        "rev_20":         [0.00, 0.01, 0.02, 0.03, nan, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10],
        # 三列全 NaN → 全截面分位取 0.5 → 对 fs 零贡献(NaN 中性化)
        "amt_cv":         [nan] * 11,
        "volatility_20":  [nan] * 11,
        "big_up_freq":    [nan] * 11,
        "rsi_approx":     [0.2, 0.8, nan, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        "bias_ma20":      [-0.06, 0.06, nan, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.10, 0.0],
        "vol_ratio_5_20": [0.5, 2.0, 1.0, nan, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        # 万元:mv_billion = /1e4 → 1500/500/150/50/50/20/100/50/NaN/400/120 亿
        "total_mv": [1500e4, 500e4, 150e4, 50e4, 50e4, 20e4, 100e4, 50e4, nan, 400e4, 120e4],
        "close":    [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 2.0, 10.0, 10.0, 10.0],
    }, index=idx)
    name_map = {f"SH6000{i:02d}": f"好票{i}" for i in range(10)}   # 行10 缺失 → "?"
    name_map["SH600006"] = "ST退市"
    name_map["SH600009"] = "华能电力"
    assert any(kw in "华能电力" for kw in UTILITY_KW)   # 关键词表守卫(电力在表内)
    return df, name_map


def test_manual_cross_section_exact_values():
    df, name_map = _manual_frame()
    out = compute_dims(df, name_map)
    assert list(out.columns) == DIM_COLS
    assert list(out["code"]) == [f"SH6000{i:02d}" for i in range(11)]
    assert list(out["layer"]) == ["大盘", "中盘", "中小", "小盘", "小盘", "小盘",
                                  "中小", "小盘", "小盘", "中盘", "中小"]
    assert list(out["mc"]) == [1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
    # fs:行0 raw=-1 但大盘 fc=0 → 封 0;行1-3 -1;行4 NaN→0.5 分位零贡献 → 0;
    #     行9 raw=+1 中盘 fc=1 → 1;行10 raw=+1 中小 fc=2 → 1
    assert list(out["fs"]) == [0, -1, -1, -1, 0, 0, 0, 0, 0, 1, 1]
    # ts:行0 rsi0.2(+1)+b20-0.06(+0.5)=1.5→round 2;行1 -1.5→-2;行2 双 NaN→0;
    #     行9 b20-0.10(+0.5)=0.5→银行家舍入 0
    assert list(out["ts"]) == [2, -2, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    # vs:行0 vr0.5<0.7→1;行1 vr2.0>1.5→-1;行3 NaN→0
    assert list(out["vs"]) == [1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    # ud:行9 名含「电力」→ -1;行6 ST 名无公用词 → 0;行10 名缺失 "?" → 0
    assert list(out["ud"]) == [0, 0, 0, 0, 0, 0, 0, 0, 0, -1, 0]
    # eligible:行5 mv=20亿≤30 / 行6 ST / 行7 close=2≤3 / 行8 total_mv NaN → False
    assert list(out["eligible"]) == [True, True, True, True, True,
                                     False, False, False, False, True, True]
    assert out["eligible"].dtype == bool


def test_flat_index_code_extraction():
    """非 MultiIndex(扁平 instrument index)→ code 直取 index,数值同 MultiIndex 版。"""
    df, name_map = _manual_frame()
    flat = df.copy()
    flat.index = df.index.get_level_values("instrument")
    out_flat = compute_dims(flat, name_map)
    out_mi = compute_dims(df, name_map)
    pd.testing.assert_frame_equal(out_flat, out_mi)


def test_empty_cross_section_returns_empty_frame():
    """空截面 → 空 DataFrame(列齐全),不崩(n=0 除零守卫)。"""
    out = compute_dims(pd.DataFrame(), {})
    assert list(out.columns) == DIM_COLS
    assert len(out) == 0


# ── 等价性:随机截面 vs「参考循环实现」(逐行照抄 _score_top200 语义)──────────

def _reference_dims(pred: pd.DataFrame, name_map: dict) -> pd.DataFrame:
    """参考实现:逐行照抄 _score_top200 的分位/打分语义(防向量化漂移的金标准)。"""
    rows = []
    for idx, row in pred.iterrows():
        code = idx[0] if isinstance(idx, tuple) else idx
        name = name_map.get(code, "?")
        mv = row["total_mv"] / 1e4
        utility = any(kw in name for kw in UTILITY_KW)
        if mv >= 1000:
            layer, fc, mc = "大盘", 0, 1
        elif mv >= 300:
            layer, fc, mc = "中盘", 1, 2
        elif mv >= 100:
            layer, fc, mc = "中小", 2, 2
        else:  # 含 mv=NaN(比较恒 False)→ 小盘,反正 ineligible
            layer, fc, mc = "小盘", 2, 2

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

        vs = 0
        vr = row.get("vol_ratio_5_20", 1)
        if vr < 0.7: vs = 1
        elif vr > 1.5: vs = -1

        ud = -1 if utility else 0
        eligible = bool(pd.notna(row["total_mv"]) and pd.notna(row["close"])
                        and mv > 30 and 3 < row["close"] < 500 and "ST" not in name)
        rows.append({"code": code, "layer": layer, "mc": mc, "fs": fs, "ts": ts,
                     "vs": vs, "ud": ud, "eligible": eligible})
    return pd.DataFrame(rows)


def test_vectorized_matches_reference_loop():
    """随机 50 行(含 NaN 注入 + 并列值)→ 向量化输出与参考循环逐位一致,且不改 pred。"""
    rs = np.random.RandomState(7)
    n = 50
    codes = [f"SZ{300000 + i:06d}" for i in range(n)]
    dt = pd.Timestamp("2026-07-10")
    idx = pd.MultiIndex.from_product([codes, [dt]], names=["instrument", "datetime"])

    def _with_nan(arr, frac=0.2):
        arr = arr.astype(float)
        arr[rs.rand(len(arr)) < frac] = np.nan
        return arr

    rev = rs.randn(n) * 0.1
    rev[10:15] = rev[10]   # 并列值:考验 rank(method="min") 与「严格小于」计数一致
    pred = pd.DataFrame({
        "rev_20": _with_nan(rev),
        "amt_cv": _with_nan(rs.rand(n)),
        "volatility_20": _with_nan(rs.rand(n) * 0.05),
        "big_up_freq": _with_nan(rs.rand(n) * 0.3),
        "rsi_approx": _with_nan(rs.rand(n)),
        "bias_ma20": _with_nan(rs.randn(n) * 0.08),
        "vol_ratio_5_20": _with_nan(rs.rand(n) * 2.5),
        "total_mv": _with_nan(rs.uniform(1e4, 2e7, n), frac=0.1),   # mv_billion 1~2000
        "close": _with_nan(rs.uniform(1.0, 600.0, n), frac=0.1),
    }, index=idx)
    name_map = {}
    for i, c in enumerate(codes):
        if i % 11 == 10:
            continue   # 每 11 只缺名 → "?"
        if i % 7 == 3:
            name_map[c] = f"ST烂票{i}"
        elif i % 5 == 2:
            name_map[c] = f"某某电力{i}"
        else:
            name_map[c] = f"好票{i}"

    before = pred.copy()
    got = compute_dims(pred, name_map).reset_index(drop=True)
    exp = _reference_dims(pred, name_map)
    pd.testing.assert_frame_equal(got, exp, check_dtype=False)
    pd.testing.assert_frame_equal(pred, before)   # 红线:绝不改 pred


# ── build_v4 缺省 dims=None → 零新行为 ───────────────────────────────────────

def test_build_v4_dims_default_none_no_new_behavior():
    """dims 缺省 None,且 compute_dims 唯一调用点在 `if dims is not None` 守卫内
    (缺省路径一行新代码都不执行 → prod 排名逐字节不变;不真跑 build_v4——那要引擎二进制)。"""
    import inspect
    sig = inspect.signature(build_v4)
    assert "dims" in sig.parameters
    assert sig.parameters["dims"].default is None
    src = inspect.getsource(build_v4)
    assert "if dims is not None" in src
    head = src.split("if dims is not None", 1)[0]
    assert "compute_dims(" not in head          # 守卫之前无任何调用
    assert src.count("compute_dims(") == 1      # 守卫之内恰一次调用
