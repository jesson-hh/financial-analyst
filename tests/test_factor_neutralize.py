"""#1 因子中性化(行业 + 市值)纯函数测试 — 逐日截面 OLS 残差。

契约(``guanlan_v2.workflow.factor_neutralize.neutralize_factor``):
  · 逐日把因子对「行业哑变量 + log 市值」做最小二乘回归,取残差替代原值;
  · 残差天然正交于行业(各行业内残差均值≈0)与 log 市值(corr≈0);
  · 某日有效样本不足(n < 设计列数+2 或 < min_obs)/设计奇异 → 该日残差全 NaN(诚实不伪造);
  · 行业全缺(None)或市值全缺(全 NaN)→ 退化为单控制项中性化,不崩;
  · 行业与市值皆为 None → 原样返回(无可中性化项);
  · 多日各自独立中性化;输出索引与输入一致。
这些是真实横截面残差行为(非 mock):构造已知「行业基差 + 规模倾斜 + 噪声」的因子,
验证中性化后基差/倾斜被回归吸收、只剩噪声序。
"""
import numpy as np
import pandas as pd
import pytest

from guanlan_v2.workflow.factor_neutralize import neutralize_factor


def _ser(dates, codes, values, dtype=float):
    """构造 (datetime, code) MultiIndex Series。values 按 product(dates, codes) 顺序。"""
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(dates), list(codes)], names=["datetime", "code"]
    )
    return pd.Series(list(values), index=idx, dtype=dtype)


def test_removes_industry_offset():
    """纯行业基差(金融≈10 / 科技≈1)+ 行业内小变动 → 中性化后各行业残差均值≈0,组内序保留。"""
    dates = ["2024-01-01"]
    codes = list("abcdef")
    ind = _ser(dates, codes, ["金融", "金融", "金融", "科技", "科技", "科技"], dtype=object)
    fac = _ser(dates, codes, [10.5, 9.5, 10.0, 1.3, 0.7, 1.0])
    res = neutralize_factor(fac, industry=ind, mktcap=None)
    g = res.groupby(ind.values).mean()
    assert abs(g["金融"]) < 1e-9
    assert abs(g["科技"]) < 1e-9
    # 行业内相对排序未被破坏(a 仍高于 b)
    assert res.iloc[0] > res.iloc[1]


def test_removes_size_tilt_no_industry():
    """因子 = 2·log市值 + 小噪声、单一行业(无 industry)→ 中性化后与 log 市值正交。"""
    dates = ["2024-01-01"]
    codes = list("abcdef")
    mv = _ser(dates, codes, [1e6, 2e6, 4e6, 8e6, 16e6, 32e6])
    base = 2.0 * np.log(mv.values)
    fac = _ser(dates, codes, base + np.array([0.1, -0.1, 0.05, -0.05, 0.2, -0.2]))
    res = neutralize_factor(fac, industry=None, mktcap=mv)
    corr = np.corrcoef(res.values, np.log(mv.values))[0, 1]
    assert abs(corr) < 1e-6


def test_joint_orthogonal_to_both():
    """因子 = 2·log市值 + 行业基差 + 噪声 → 联合中性化后对 log 市值与行业同时正交。"""
    dates = ["2024-01-01"]
    codes = list("abcdefgh")
    mv = _ser(dates, codes, [1e6, 2e6, 3e6, 4e6, 5e6, 6e6, 7e6, 8e6])
    ind = _ser(dates, codes, ["金融"] * 4 + ["科技"] * 4, dtype=object)
    base = 2.0 * np.log(mv.values)
    indeff = np.array([3, 3, 3, 3, -3, -3, -3, -3], dtype=float)
    noise = np.array([0.1, -0.1, 0.05, -0.05, 0.2, -0.2, 0.15, -0.15])
    fac = _ser(dates, codes, base + indeff + noise)
    res = neutralize_factor(fac, industry=ind, mktcap=mv)
    assert abs(np.corrcoef(res.values, np.log(mv.values))[0, 1]) < 1e-6
    g = res.groupby(ind.values).mean()
    assert abs(g["金融"]) < 1e-9 and abs(g["科技"]) < 1e-9


def test_degenerate_date_is_nan():
    """某日只有 2 票却分属 2 行业(设计列=2,n=2 < 列+2)→ 该日残差全 NaN(诚实不伪造)。"""
    dates = ["2024-01-01"]
    codes = list("ab")
    ind = _ser(dates, codes, ["金融", "科技"], dtype=object)
    fac = _ser(dates, codes, [1.0, 2.0])
    res = neutralize_factor(fac, industry=ind, mktcap=None)
    assert res.isna().all()


def test_no_controls_returns_unchanged():
    """行业与市值皆 None → 无可中性化项 → 原样返回(不去均值、不伪造)。"""
    dates = ["2024-01-01"]
    codes = list("abcd")
    fac = _ser(dates, codes, [1.0, 2.0, 3.0, 4.0])
    res = neutralize_factor(fac, industry=None, mktcap=None)
    pd.testing.assert_series_equal(res, fac)


def test_all_nan_mktcap_falls_back_to_industry():
    """市值全 NaN(daily_basic 缺)→ 退化为纯行业中性化,不崩,残差仍正交于行业。"""
    dates = ["2024-01-01"]
    codes = list("abcdef")
    ind = _ser(dates, codes, ["金融"] * 3 + ["科技"] * 3, dtype=object)
    mv = _ser(dates, codes, [np.nan] * 6)
    fac = _ser(dates, codes, [10.5, 9.5, 10.0, 1.3, 0.7, 1.0])
    res = neutralize_factor(fac, industry=ind, mktcap=mv)
    g = res.groupby(ind.values).mean()
    assert abs(g["金融"]) < 1e-9 and abs(g["科技"]) < 1e-9
    assert res.notna().all()


def test_unknown_industry_excluded_not_a_real_bucket():
    """"未知"(未分类)不是真行业:这些股票应被排除出行业回归(残差 NaN),既不污染真行业组、
    也不被冒充「已中性化」。否则会把未分类股当成一个无经济含义的垃圾桶去均值(对抗复核 Finding 4)。"""
    dates = ["2024-01-01"]
    codes = list("abcdefgh")
    ind = _ser(dates, codes, ["金融", "金融", "金融", "科技", "科技", "科技", "未知", "未知"], dtype=object)
    fac = _ser(dates, codes, [10.5, 9.5, 10.0, 1.3, 0.7, 1.0, 5.0, 6.0])
    res = neutralize_factor(fac, industry=ind, mktcap=None)
    # 两只「未知」→ 残差 NaN(诚实排除,不冒充已中性化)
    assert pd.isna(res.iloc[6]) and pd.isna(res.iloc[7])
    # 真行业组不被未知污染:各组残差均值≈0
    real = res.iloc[:6]
    rind = ind.values[:6]
    g = real.groupby(rind).mean()
    assert abs(g["金融"]) < 1e-9 and abs(g["科技"]) < 1e-9


def test_size_gate_uses_regression_sample_not_gross_count():
    """市值是否纳入,须按【真正进回归的候选行】(因子有限)的覆盖率判,而非含 NaN-因子行的全截面行数。
    构造:6 只有因子值但其中仅 1 只有市值;另 6 只无因子值却都有市值(全截面市值覆盖看似过半,
    但候选行里市值覆盖仅 1/6)。正确行为=该日弃市值、仅按行业中性化 6 只候选,而非误纳市值致回归样本=1
    把整日 NaN 掉(对抗复核 Finding 2/4)。"""
    dates = ["2024-01-01"]
    codes = list("abcdefghijkl")  # 12 只
    ind = _ser(dates, codes, ["金融", "金融", "金融", "科技", "科技", "科技",
                              "科技", "科技", "科技", "科技", "科技", "科技"], dtype=object)
    # 因子:a-f 有值,g-l NaN
    fac = _ser(dates, codes, [10.5, 9.5, 10.0, 1.3, 0.7, 1.0,
                              np.nan, np.nan, np.nan, np.nan, np.nan, np.nan])
    # 市值:仅 a(候选行)+ g,h,i,j,k(NaN-因子行)有 → 全截面 6/12 过半,但候选行 a-f 只 1/6
    mv = _ser(dates, codes, [2e6, np.nan, np.nan, np.nan, np.nan, np.nan,
                             3e6, 4e6, 5e6, 6e6, 7e6, np.nan])
    res = neutralize_factor(fac, industry=ind, mktcap=mv)
    # 候选 6 只(a-f)应被行业中性化(非整日 NaN):各行业残差均值≈0
    assert res.iloc[:6].notna().all()
    g = res.iloc[:6].groupby(ind.values[:6]).mean()
    assert abs(g["金融"]) < 1e-9 and abs(g["科技"]) < 1e-9
    # NaN-因子行仍 NaN
    assert res.iloc[6:].isna().all()


def test_preserves_index_and_per_date_independence():
    """多日各自独立中性化:输出索引不变,且每日每行业内残差均值≈0。"""
    dates = ["2024-01-01", "2024-01-02"]
    codes = list("abcdef")
    ind = _ser(dates, codes, (["金融"] * 3 + ["科技"] * 3) * 2, dtype=object)
    # 两日因子不同(第二日整体抬升 + 不同噪声)
    fac = _ser(dates, codes, [10.5, 9.5, 10.0, 1.3, 0.7, 1.0,
                              20.2, 19.8, 20.0, 5.5, 4.5, 5.0])
    res = neutralize_factor(fac, industry=ind, mktcap=None)
    assert res.index.equals(fac.index)
    for d in dates:
        sub = res.xs(pd.Timestamp(d), level="datetime")
        isub = ind.xs(pd.Timestamp(d), level="datetime")
        for lab in pd.unique(isub.values):
            assert abs(sub[isub.values == lab].mean()) < 1e-9
