# 市场风格 regime 条件化选股(因子族动态权重)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个纯价量因子族独立判 regime(jump-penalty walk-forward → p_fav),经激活闸认证后 opt-in 地倾斜选股页 α 混合通道的因子权重,交付相对静态基线的 OOS ΔrankIC 证明(或诚实的"无增量"结论)。

**Architecture:** 四层单向数据流:factor_ls(族多空 PIT 序列)→ factor_regime(jump-penalty walk-forward p_fav)→ regime_gate(walk-forward ΔIC 主判据 + BH-FDR + 安慰剂 + 代理池激活闸)→ screen/api opt-in 接线。v4.py 零改动;默认 /screen 路径逐字节不变;重物化走独立子进程(不占 regen 锁)。

**Tech Stack:** 纯 numpy/pandas(零新依赖)、FastAPI(现路由工厂)、pytest;复用 cpcv.py(make_splits/deflated_sharpe/_norm_cdf)、factor_ic.py 骨架、catalog.FACTOR_DEFS。

**Spec:** `docs/superpowers/specs/2026-07-02-regime-factor-weights-design.md`

---

## 执行环境注意(先读)

- **并行会话冲突**:另一会话在 `guanlan_v2/screen/api.py`、`guanlan_v2/strategy/compute/cpcv.py` 等有未提交改动。**动这两个文件的任务(Task 8)前必须 `git status` 核对**;若仍有未提交改动 → 停下与用户确认,或走 worktree(先例:GAT 在 `.claude/settings.local.json` `worktree.baseRef:"head"` 下建 worktree + 三条 junction)。
- **测试命令统一用**:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file> -q`(仓根 `G:\guanlan-v2` 执行;conftest 已顶层 prepend engine 路径)。
- **数据现状**:qlib 价量 bins(`G:/stocks` provider)可用 → Task 10 真机回填/闸不被 artifacts 清空事故阻塞;但 `/screen/run` v4 主路径依赖 `v4_ranking_latest.parquet`(等并行会话恢复)——Task 8 的端点测试已设计成两种环境都能跑(见任务内 skip 条款)。
- **提交纪律**:只 `git add` 本任务明列的文件,严禁 `add -A`;提交信息末尾 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- **人工激活语义**:闸(regime_gate)**只由人工 CLI 触发**,regen 绝不自动跑闸——activated 落盘这个动作本身就是人工确认(validate_dl_source 先例:passes 建议性、激活人工)。

## 文件结构总览

| 文件 | 职责 |
|---|---|
| `guanlan_v2/strategy/paths.py`(改) | 4 个产物路径常量 |
| `guanlan_v2/strategy/compute/jump_model.py`(新) | jump-penalty DP 求解器(纯 numpy,无业务) |
| `guanlan_v2/strategy/compute/factor_ls.py`(新) | 族多空 PIT 序列 + 因子框物化(与闸共用)+ 增量 + 子进程 CLI |
| `guanlan_v2/strategy/compute/factor_regime.py`(新) | 特征 → walk-forward regime → p_fav 产物 + 权重变换 + resolve 胶水 |
| `guanlan_v2/strategy/compute/regime_gate.py`(新) | 评估协议 + 激活闸 + CLI |
| `guanlan_v2/strategy/compute/regen.py`(改) | factor_ic 步后两个非阻断增量步 |
| `guanlan_v2/screen/api.py`(改) | ScreenIn.regimeWeights + 接线 + 徽章 + GET /screen/regime |
| `ui/screen/观澜 · 选股.html`(改) | opt-in toggle + 徽章 + 族 p_fav 展示 |
| `tests/test_jump_model.py` 等 5 个测试文件 | 见各任务 |

---

### Task 1: paths 常量 + jump_model.py(DP 求解器)

**Files:**
- Modify: `guanlan_v2/strategy/paths.py`
- Create: `guanlan_v2/strategy/compute/jump_model.py`
- Test: `tests/test_jump_model.py`

- [ ] **Step 1.1: 写失败测试**

```python
# tests/test_jump_model.py
# jump-penalty DP 门禁:全局最优(暴力对照)/分段还原/λ 压切换/确定性。
import numpy as np
import pytest
from guanlan_v2.strategy.compute.jump_model import (dp_states, fit_jump_model,
                                                    online_state, soft_prob, _objective)


def test_dp_optimal_vs_bruteforce():
    # T=8 全枚举 256 条路径,DP 结果目标值必须等于全局最优。
    rng = np.random.default_rng(0)
    X = rng.normal(size=(8, 2))
    C = np.array([[0.5, 0.0], [-0.5, 0.0]])
    lam = 0.3
    s = dp_states(X, C, lam)
    best = min(_objective(X, C, np.array([(b >> i) & 1 for i in range(8)]), lam)
               for b in range(2 ** 8))
    assert _objective(X, C, s, lam) == pytest.approx(best)


def test_fit_recovers_segmentation():
    # 两段清晰分离数据:恰 1 次切换,两段内各 ≥95% 同态(防塌缩单态)。
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(1.0, 0.3, (100, 2)), rng.normal(-1.0, 0.3, (100, 2))])
    C, s, obj = fit_jump_model(X, k=2, lam=5.0, seed=0)
    assert int((s[1:] != s[:-1]).sum()) == 1
    assert (s[:100] == s[0]).mean() >= 0.95 and (s[100:] == s[-1]).mean() >= 0.95


def test_lambda_suppresses_switching():
    # 纯噪声:λ→大 切换次数被压死;λ=0 切换远多(证据:jump penalty 抑 whipsaw)。
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, 2))
    _, s0, _ = fit_jump_model(X, k=2, lam=0.0, seed=0)
    _, s9, _ = fit_jump_model(X, k=2, lam=1e6, seed=0)
    assert int((s9[1:] != s9[:-1]).sum()) <= 1 < int((s0[1:] != s0[:-1]).sum())


def test_fit_deterministic_and_online_consistent():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(120, 3))
    a = fit_jump_model(X, lam=10.0, seed=7)
    b = fit_jump_model(X, lam=10.0, seed=7)
    assert np.array_equal(a[1], b[1]) and np.allclose(a[0], b[0])
    # 在线过滤与软概率:argmax(soft) == online_state(同一代价函数)
    st = online_state(X[-1], a[0], 10.0, prev_state=int(a[1][-2]))
    p = soft_prob(X[-1], a[0], 10.0, prev_state=int(a[1][-2]), temp=1.0)
    assert int(np.argmax(p)) == st and p.sum() == pytest.approx(1.0)
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_jump_model.py -q`
Expected: FAIL(ModuleNotFoundError: jump_model)

- [ ] **Step 1.3: 实现**

`guanlan_v2/strategy/paths.py` 末尾追加:

```python
# regime 因子族动态权重(2026-07-02 spec)四产物
FACTOR_LS_PARQUET = ARTIFACTS_DIR / "factor_ls_returns.parquet"
FACTOR_REGIME_PARQUET = ARTIFACTS_DIR / "factor_regime.parquet"
FACTOR_REGIME_META_JSON = ARTIFACTS_DIR / "factor_regime_meta.json"
FACTOR_REGIME_GATE_JSON = ARTIFACTS_DIR / "factor_regime_gate.json"
```

`guanlan_v2/strategy/compute/jump_model.py`(全文):

```python
# -*- coding: utf-8 -*-
"""jump-penalty 统计跳变模型(纯 numpy,零新依赖)。

目标:min Σ_t ‖x_t − μ_{s_t}‖² + λ·Σ_t 1[s_t≠s_{t−1}](Nystrup 型 statistical jump model)。
求解:质心固定 → 状态序列 DP 全局最优;状态固定 → 质心=均值;交替迭代,多初始化取最优。
证据(深研 3-0):jump penalty 抑制 whipsaw,年切换 ~0.8 次 vs 裸 HMM 2+。
"""
from __future__ import annotations

import numpy as np


def dp_states(X, centers, lam, prev_state=None):
    """给定质心求全局最优状态序列(动态规划)。X:(T,F) centers:(k,F) λ=切换罚。
    prev_state 非空 → 首日也按「从 prev_state 切换」计罚(在线续推口径)。"""
    X = np.asarray(X, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    T, k = len(X), len(C)
    d2 = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)   # (T,k) 逐点代价
    cost = np.full((T, k), np.inf)
    back = np.zeros((T, k), dtype=np.int64)
    if prev_state is None:
        cost[0] = d2[0]
    else:
        cost[0] = d2[0] + lam * (np.arange(k) != int(prev_state))
    for t in range(1, T):
        trans = cost[t - 1][:, None] + lam * (1.0 - np.eye(k))   # trans[j,s]
        back[t] = np.argmin(trans, axis=0)
        cost[t] = d2[t] + np.min(trans, axis=0)
    s = np.zeros(T, dtype=np.int64)
    s[-1] = int(np.argmin(cost[-1]))
    for t in range(T - 2, -1, -1):
        s[t] = back[t + 1][s[t + 1]]
    return s


def _objective(X, C, s, lam):
    X = np.asarray(X, dtype=np.float64)
    C = np.asarray(C, dtype=np.float64)
    s = np.asarray(s, dtype=np.int64)
    return float(((X - C[s]) ** 2).sum() + lam * int((s[1:] != s[:-1]).sum()))


def fit_jump_model(X, k=2, lam=100.0, n_init=6, max_iter=25, seed=0, warm_centers=None):
    """交替优化 + 多随机初始化(+可选 warm start)。返回 (centers(k,F), states(T,), obj)。
    质心行序无语义,有利态由调用方按特征维命名(factor_regime 按 sortino20 维)。"""
    X = np.asarray(X, dtype=np.float64)
    T = len(X)
    rng = np.random.default_rng(seed)
    inits = [warm_centers] if warm_centers is not None else []
    for _ in range(n_init):
        inits.append(X[rng.choice(T, size=k, replace=False)].copy())
    best = None
    for C0 in inits:
        C = np.asarray(C0, dtype=np.float64).copy()
        s = dp_states(X, C, lam)
        for _ in range(max_iter):
            C_new = np.vstack([X[s == j].mean(axis=0) if (s == j).any() else C[j]
                               for j in range(k)])
            s_new = dp_states(X, C_new, lam)
            done = np.array_equal(s_new, s) and np.allclose(C_new, C)
            C, s = C_new, s_new
            if done:
                break
        obj = _objective(X, C, s, lam)
        if best is None or obj < best[2]:
            best = (C, s, obj)
    return best


def online_state(x, centers, lam, prev_state):
    """在线过滤(月度重拟之间逐日用,O(k)):cost_s = ‖x−μ_s‖² + λ·1[s≠prev] → argmin。"""
    x = np.asarray(x, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    cost = ((C - x) ** 2).sum(axis=1) + lam * (np.arange(len(C)) != int(prev_state))
    return int(np.argmin(cost))


def soft_prob(x, centers, lam, prev_state, temp):
    """状态软概率 = softmax(−cost/temp);temp=拟合残差均值(调用方传),下限 1e-9 防除零。"""
    x = np.asarray(x, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    cost = ((C - x) ** 2).sum(axis=1) + lam * (np.arange(len(C)) != int(prev_state))
    z = -cost / max(float(temp), 1e-9)
    z -= z.max()
    e = np.exp(z)
    return e / e.sum()
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_jump_model.py -q`
Expected: 4 passed

- [ ] **Step 1.5: Commit**

```bash
git add guanlan_v2/strategy/paths.py guanlan_v2/strategy/compute/jump_model.py tests/test_jump_model.py
git commit -m "feat(regime): jump-penalty 统计跳变模型(纯numpy DP)+ 产物路径常量"
```

---

### Task 2: factor_ls.py 纯函数(族多空序列)

**Files:**
- Create: `guanlan_v2/strategy/compute/factor_ls.py`
- Test: `tests/test_factor_ls.py`

- [ ] **Step 2.1: 写失败测试**

```python
# tests/test_factor_ls.py
# 族多空序列门禁:L/S 手算值 / PIT available_date=t+1 / 末日不出行 / 截面太薄诚实缺席。
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import factor_ls as FL


def test_ls_series_value_and_pit():
    idx = pd.bdate_range("2025-01-01", periods=4)
    codes = list("ABCDE")
    close = pd.DataFrame(
        [[10, 10, 10, 10, 10],
         [11, 10, 10, 10, 9],    # t0→t1:A +10%,E −10%
         [11, 10, 10, 10, 9],
         [11, 10, 10, 10, 9]], index=idx, columns=codes, dtype=float)
    fac = pd.DataFrame([[5, 4, 3, 2, 1]] * 4, index=idx, columns=codes, dtype=float)
    out = FL.ls_series(fac, close, q=0.2, min_n=5)
    r0 = out[out["date"] == idx[0]].iloc[0]
    assert r0["ls_ret"] == pytest.approx(0.2)          # top=A(+10%) − bot=E(−10%)
    assert r0["available_date"] == idx[1]              # t+1 收盘才 realized(PIT)
    assert idx[-1] not in set(out["date"])             # 末日无次日收益 → 不出行


def test_ls_series_thin_cross_section_honest():
    idx = pd.bdate_range("2025-01-01", periods=5)
    close = pd.DataFrame(1.0, index=idx, columns=list("ABC"))
    fac = close.copy()
    assert FL.ls_series(fac, close).empty              # 默认 min_n=30 → 3 票诚实缺席


def test_load_family_ls_equal_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "ls.parquet")
    idx = pd.bdate_range("2025-01-01", periods=2)
    df = pd.DataFrame({
        "date": list(idx) * 2, "family": ["技术"] * 4,
        "factor_id": ["f1", "f1", "f2", "f2"],
        "ls_ret": [0.01, 0.02, 0.03, 0.04],
        "available_date": [idx[1], idx[1], idx[1], idx[1]]})
    df.to_parquet(tmp_path / "ls.parquet", index=False)
    g = FL.load_family_ls()
    assert g[g["date"] == idx[0]]["ls_ret"].iloc[0] == pytest.approx(0.02)   # (0.01+0.03)/2
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_ls.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 2.3: 实现(纯函数部分)**

`guanlan_v2/strategy/compute/factor_ls.py`(本任务先落纯函数 + 读取器,生产面 Task 3 补):

```python
# -*- coding: utf-8 -*-
"""因子族多空(L/S)收益序列:regime 层地基产物(PIT:available_date = t+1)。

- 白名单 = 6 个纯价量族(估值/财务/成长/规模依赖坏管线、情绪/资金面数据源未审计 → 均排除,spec §3);
- t 日按因子截面排序,top/bottom quintile 等权的 t→t+1 收益差 = 当日 L/S;族内成员等权平均;
- 下游 regime 特征在 t 只允许用 available_date ≤ t 的行(PIT 命门);
- 全量物化重(10-30min)→ 只走 __main__ 独立子进程 + 独立锁,不进 regen 锁临界区(评审前置条件);
  regen 内只跑日频增量(秒-分钟级)。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.paths import FACTOR_LS_PARQUET

WHITELIST_FAMILIES = ("动量反转", "技术", "波动率", "流动性", "共振", "跟随")
CSV_ID = "_csv"          # 市场截面收益离散度(连续机会空间代理,深研 3-0)伪因子行
CSV_FAMILY = "_market"   # 不参与族聚合
LS_Q = 0.2
LS_MIN_N = 30            # 截面最少票数,低于不算(诚实缺席)


def ls_series(fac_wide: pd.DataFrame, close_wide: pd.DataFrame, q: float = LS_Q,
              min_n: Optional[int] = None) -> pd.DataFrame:
    """单因子 L/S 日收益(值已预定向:高=看多)。行 index=t、available_date=t+1(PIT)。"""
    mn = LS_MIN_N if min_n is None else int(min_n)
    cw = close_wide.sort_index()
    fw = fac_wide.reindex(index=cw.index, columns=cw.columns)
    ret_next = cw.shift(-1) / cw - 1.0        # r_{t→t+1} 挂在 t 行
    dates = list(cw.index)
    rows = []
    for i, t in enumerate(dates[:-1]):        # 末日无次日收益 → 不出行
        f = fw.loc[t].dropna()
        r = ret_next.loc[t].reindex(f.index).dropna()
        f = f.reindex(r.index)
        if len(f) < mn:
            continue
        n_side = max(1, int(len(f) * q))
        order = f.sort_values()
        top = float(r.reindex(order.index[-n_side:]).mean())
        bot = float(r.reindex(order.index[:n_side]).mean())
        if not (np.isfinite(top) and np.isfinite(bot)):
            continue
        rows.append({"date": t, "ls_ret": top - bot, "available_date": dates[i + 1]})
    return pd.DataFrame(rows, columns=["date", "ls_ret", "available_date"])


def load_family_ls() -> pd.DataFrame:
    """族等权 L/S 长表(date, family, ls_ret, available_date);缺产物 → 空表(诚实缺席)。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.DataFrame(columns=["date", "family", "ls_ret", "available_date"])
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["family"] != CSV_FAMILY]
    g = (df.groupby(["date", "family"], as_index=False)
           .agg(ls_ret=("ls_ret", "mean"), available_date=("available_date", "max")))
    return g.sort_values(["family", "date"]).reset_index(drop=True)


def load_csv_series() -> pd.Series:
    """市场截面收益离散度序列(index=date);缺产物 → 空序列。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["factor_id"] == CSV_ID]
    return pd.Series(df["ls_ret"].values, index=pd.DatetimeIndex(df["date"])).sort_index()
```

- [ ] **Step 2.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_ls.py -q`
Expected: 3 passed

- [ ] **Step 2.5: Commit**

```bash
git add guanlan_v2/strategy/compute/factor_ls.py tests/test_factor_ls.py
git commit -m "feat(regime): 族多空 L/S 序列纯函数(PIT available_date=t+1)"
```

---

### Task 3: factor_ls.py 生产面(物化/增量/子进程锁)

**Files:**
- Modify: `guanlan_v2/strategy/compute/factor_ls.py`
- Test: `tests/test_factor_ls.py`(追加)

- [ ] **Step 3.1: 追加失败测试**

```python
# 追加到 tests/test_factor_ls.py
def test_incremental_idempotent(tmp_path, monkeypatch):
    # 增量:只补末日之后;同 end 重跑 0 行;无重复 (date,factor_id)。
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "ls.parquet")
    monkeypatch.setattr(FL, "LS_MIN_N", 3)
    idx = pd.bdate_range("2025-01-01", periods=40)
    codes = [f"C{i}" for i in range(6)]
    rng = np.random.default_rng(0)
    close = pd.DataFrame(100 + rng.normal(0, 1, (40, 6)).cumsum(axis=0),
                         index=idx, columns=codes)
    fac = close.pct_change(fill_method=None)

    def _mat(universe="csi800", start=None, end=None):
        e = pd.Timestamp(end) if end else idx[-1]
        return {"f1": fac.loc[:e]}, close.loc[:e], {"f1": "技术"}

    monkeypatch.setattr(FL, "materialize_factor_frames", _mat)
    assert FL.compute_factor_ls(end=str(idx[30].date())) > 0
    assert FL.update_factor_ls_incremental(end=str(idx[30].date())) == 0   # 幂等
    assert FL.update_factor_ls_incremental(end=str(idx[-1].date())) > 0
    assert FL.update_factor_ls_incremental(end=str(idx[-1].date())) == 0   # 再跑不重复
    df = pd.read_parquet(tmp_path / "ls.parquet")
    assert not df.duplicated(subset=["date", "factor_id"]).any()
    assert (df[df["factor_id"] == FL.CSV_ID]["family"] == FL.CSV_FAMILY).all()


def test_incremental_without_full_backfill_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "none.parquet")
    assert FL.update_factor_ls_incremental() == 0     # 无全量产物 → 0,不偷跑重活
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_ls.py -q`
Expected: 新 2 例 FAIL(AttributeError: materialize_factor_frames)

- [ ] **Step 3.3: 实现(追加到 factor_ls.py)**

```python
def materialize_factor_frames(universe: str = "csi800", start: str = "2016-01-01",
                              end: Optional[str] = None
                              ) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, Dict[str, str]]:
    """引擎面板 → 白名单因子 wide 值框(已预定向 ×dir)+ close wide(骨架照 factor_ic.py,
    与激活闸共用同一物化)。返回 (frames{fid:df(date×code)}, close_wide, fams{fid:family})。
    编译不过/全 NaN → 诚实跳过并打日志。重函数:只在子进程/闸里调,不进 regen 锁。"""
    from datetime import date as _date

    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.data.universe import resolve_universe_codes
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached

    from guanlan_v2.screen.catalog import FACTOR_DEFS

    end_s = end or _date.today().isoformat()
    codes = [str(c) for c in resolve_universe_codes(universe)]
    panel = load_panel_cached(get_default_loader(), codes, start, end_s, freq="day")
    try:
        from guanlan_v2.workflow.api import _inject_market_refs
        panel, _w = _inject_market_refs(panel, "csi300", None, start, end_s, freq="day")
        for _m in (_w or []):
            print(f"[factor_ls] 警告: {_m}", flush=True)   # 指数停更 → 共振/跟随族缺数显形
    except Exception:  # noqa: BLE001
        pass    # 注入失败 → 共振/跟随族算不出,诚实缺席,其余族不受影响

    def _wide(expr: str):
        s = compile_factor(expr)(panel)
        if s is None or not isinstance(s, pd.Series):
            return None
        w = s.unstack(level="code")
        w.index = pd.DatetimeIndex(w.index)
        return w.sort_index()

    close_wide = _wide("close")
    if close_wide is None or close_wide.empty:
        raise RuntimeError("factor_ls: 面板无 close")
    frames: Dict[str, pd.DataFrame] = {}
    fams: Dict[str, str] = {}
    for fid, meta in FACTOR_DEFS.items():
        fam, expr = meta.get("family"), meta.get("expr")
        if fam not in WHITELIST_FAMILIES or not expr:
            continue
        try:
            w = _wide(expr)
        except Exception:  # noqa: BLE001
            w = None
        if w is None or w.dropna(how="all").empty:
            print(f"[factor_ls] 跳过 {fid}({meta.get('short')}):算不出(诚实缺席)", flush=True)
            continue
        frames[fid] = w * float(meta.get("dir", 1) or 1)   # 预定向(legacy fa_distrib dir=-1)
        fams[fid] = fam
    return frames, close_wide, fams


def _csv_rows(close_wide: pd.DataFrame) -> pd.DataFrame:
    """市场截面收益离散度:当日截面 std,收盘即知(available_date=当日,非前瞻)。"""
    ret = close_wide.pct_change(fill_method=None)
    csv = ret.std(axis=1, ddof=0)
    df = pd.DataFrame({"date": csv.index, "ls_ret": csv.values})
    df = df[np.isfinite(df["ls_ret"])].copy()
    df["available_date"] = df["date"]
    df["factor_id"], df["family"] = CSV_ID, CSV_FAMILY
    return df


_COLS = ["date", "family", "factor_id", "ls_ret", "available_date"]


def compute_factor_ls(universe: str = "csi800", start: str = "2016-01-01",
                      end: Optional[str] = None) -> int:
    """全量物化 → factor_ls_returns.parquet(因子行 + _csv 行)。只允许子进程跑。"""
    frames, close_wide, fams = materialize_factor_frames(universe, start, end)
    parts = []
    for fid, fw in frames.items():
        df = ls_series(fw, close_wide)
        if df.empty:
            continue
        df["factor_id"], df["family"] = fid, fams[fid]
        parts.append(df)
    parts.append(_csv_rows(close_wide))
    out = pd.concat(parts, ignore_index=True)[_COLS]
    tmp = str(FACTOR_LS_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_LS_PARQUET))
    return len(out)


def update_factor_ls_incremental(end: Optional[str] = None,
                                 universe: str = "csi800") -> int:
    """日频增量(regen 非阻断步):只补产物末日之后(短窗 470 自然日重物化,分钟级);
    无全量产物 → 0 并提示(不在 regen 锁内偷跑重活)。幂等:同 end 重跑不重复。"""
    from datetime import date as _date, timedelta

    if not FACTOR_LS_PARQUET.exists():
        print("[factor_ls] 无全量产物,先 python -m guanlan_v2.strategy.compute.factor_ls 回填",
              flush=True)
        return 0
    old = pd.read_parquet(FACTOR_LS_PARQUET)
    last = pd.Timestamp(old["date"].max())
    end_s = end or _date.today().isoformat()
    if pd.Timestamp(end_s) <= last:
        return 0
    start = (last - timedelta(days=470)).date().isoformat()   # 目录最长回看 240 交易日热身
    frames, close_wide, fams = materialize_factor_frames(universe, start, end_s)
    parts = []
    for fid, fw in frames.items():
        df = ls_series(fw, close_wide)
        df = df[df["date"] > last]
        if df.empty:
            continue
        df["factor_id"], df["family"] = fid, fams[fid]
        parts.append(df)
    cdf = _csv_rows(close_wide)
    cdf = cdf[cdf["date"] > last]
    if len(cdf):
        parts.append(cdf)
    if not parts:
        return 0
    new = pd.concat(parts, ignore_index=True)[_COLS]
    out = (pd.concat([old, new], ignore_index=True)
             .drop_duplicates(subset=["date", "factor_id"], keep="first"))
    tmp = str(FACTOR_LS_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_LS_PARQUET))
    return len(new)


def _acquire_ls_lock():
    """独立锁(**非 regen 锁**,评审前置条件):防两个全量回填并发;>2h 残留可接管。"""
    import json
    import tempfile
    import time
    from pathlib import Path

    p = Path(tempfile.gettempdir()) / "guanlan_factor_ls.lock"
    if p.exists():
        try:
            age = time.time() - float(json.loads(p.read_text(encoding="utf-8")).get("ts", 0))
        except Exception:  # noqa: BLE001
            age = 1e9
        if age < 7200:
            raise RuntimeError("另一 factor_ls 全量回填进行中,拒绝并发")
    p.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
    return p


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="因子族 L/S 全量回填(独立子进程,10-30min)")
    ap.add_argument("--universe", default="csi800")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    _lock = _acquire_ls_lock()
    try:
        n = compute_factor_ls(a.universe, a.start, a.end)
        print(f"factor_ls 全量回填 {n} 行 -> {FACTOR_LS_PARQUET}", flush=True)
    finally:
        try:
            _lock.unlink()
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 3.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_ls.py -q`
Expected: 5 passed

- [ ] **Step 3.5: Commit**

```bash
git add guanlan_v2/strategy/compute/factor_ls.py tests/test_factor_ls.py
git commit -m "feat(regime): factor_ls 生产面(物化共用/日频增量幂等/子进程独立锁)"
```

---

### Task 4: factor_regime.py(特征 + walk-forward + 快照缓存)

**Files:**
- Create: `guanlan_v2/strategy/compute/factor_regime.py`
- Test: `tests/test_factor_regime.py`

- [ ] **Step 4.1: 写失败测试**

```python
# tests/test_factor_regime.py
# regime 层命门:截断不变性(PIT)/ 快照缓存等价 / 热身诚实缺席 / 中性恒等 / 倾斜夹逼。
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import factor_regime as FR
from guanlan_v2.strategy.compute.factor_regime import (apply_regime_weights,
                                                       regime_features,
                                                       walk_forward_regimes)


def _feat(n=700, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    ls = pd.Series(rng.normal(0.001, 0.01, n), index=idx)
    ls.iloc[n // 2:] -= 0.004          # 后半段下移 → 两个可辨 regime
    return regime_features(ls)


def test_truncation_invariance():
    # PIT 守护:删未来数据重跑,历史 regime 逐位不变(参数与状态都只依赖 ≤t)。
    feat = _feat()
    full, _ = walk_forward_regimes(feat, warmup=200, refit_every=21)
    part, _ = walk_forward_regimes(feat.iloc[:520], warmup=200, refit_every=21)
    assert len(part) > 0
    pd.testing.assert_frame_equal(full.iloc[:len(part)].reset_index(drop=True),
                                  part.reset_index(drop=True))


def test_snapshot_cache_equivalence():
    # 缓存复用(regen 快路径)与冷算逐位一致。
    feat = _feat()
    cold, snaps = walk_forward_regimes(feat, warmup=200, refit_every=21)
    cache = {sn["fit_asof"]: sn for sn in snaps}
    warm, _ = walk_forward_regimes(feat, warmup=200, refit_every=21, snapshot_cache=cache)
    pd.testing.assert_frame_equal(cold.reset_index(drop=True), warm.reset_index(drop=True))


def test_warmup_honest_absence_and_pfav_range():
    feat = _feat()
    empty, _ = walk_forward_regimes(feat.iloc[:100], warmup=200)
    assert empty.empty                                     # 热身不足 → 不出行
    df, _ = walk_forward_regimes(feat, warmup=200, refit_every=21)
    assert df["p_fav"].between(0.0, 1.0).all()
    yrs = len(df) / 244.0
    assert (df["state"].diff().abs().sum() / yrs) <= 3.0   # λ 定标后切换有界(宽松护栏)


def test_apply_regime_weights_neutral_identity_and_clip():
    sup = [("f1", 1.0), ("f2", 2.0)]
    fam_of = {"f1": "技术", "f2": "波动率"}
    out, info = apply_regime_weights(sup, fam_of, {"技术": 0.5, "波动率": 1.0},
                                     {"技术", "波动率"})
    assert out[0][1] == pytest.approx(1.0)          # p=0.5 → 中性不动(w_eff≡w)
    assert out[1][1] == pytest.approx(2.0 * 1.25)   # p=1 → tilt=1.5 → 乘子封顶 1.25
    out2, _ = apply_regime_weights(sup, fam_of, {"技术": 0.0}, {"技术"})
    assert out2[0][1] == pytest.approx(0.75)        # p=0 → 乘子地板 0.75
    assert out2[1][1] == pytest.approx(2.0)         # 未激活族原样
    assert info[0]["family"] == "技术" and "w_eff" in info[0]
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_regime.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 4.3: 实现**

`guanlan_v2/strategy/compute/factor_regime.py`(全文,生产 build/resolve 在 Task 5 追加):

```python
# -*- coding: utf-8 -*-
"""因子族 regime 层:族 L/S 序列 → jump-penalty walk-forward → p_fav 连续概率 → 权重倾斜。

PIT 双保证:参数每 REFIT_EVERY 交易日 expanding(仅 ≤t)重拟;其间在线过滤;
守护测试 = 截断不变性(删未来历史逐位不变)。输出连续 p_fav,不出硬开关;
倾斜 w_eff = w·((1−η)+η·clip(2·p_fav, lo, hi)),η=0.5 → 有效乘子 ∈[0.75,1.25]
(AQR 3-0:倾斜必须保守、向静态收缩、设上限)。η/clip 为可审计常数,不许运行期调。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.jump_model import (dp_states, fit_jump_model,
                                                    online_state, soft_prob)
from guanlan_v2.strategy.paths import (FACTOR_REGIME_GATE_JSON,
                                       FACTOR_REGIME_META_JSON,
                                       FACTOR_REGIME_PARQUET)

ETA = 0.5
TILT_LO, TILT_HI = 0.5, 1.5
WARMUP = 500
REFIT_EVERY = 21
LAM_GRID = (50.0, 100.0, 200.0)
MAX_SWITCH_PER_YEAR = 1.5     # λ 定标目标(深研 3-0:jump ~0.8 vs HMM 2+)
FRESH_MAX_LAG = 3             # 产物新鲜度:asof 距排名日 ≤3 交易日(评审收紧)
_FEAT_COLS = ("feat_dvol", "feat_sortino20", "feat_sortino60", "feat_csv")
SPEC = {"eta": ETA, "tilt": [TILT_LO, TILT_HI], "warmup": WARMUP,
        "refit_every": REFIT_EVERY, "lam_grid": list(LAM_GRID), "k": 2,
        "features": list(_FEAT_COLS)}
SPEC_HASH = hashlib.md5(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()[:10]


def regime_features(ls: pd.Series, csv: Optional[pd.Series] = None) -> pd.DataFrame:
    """族 L/S 序列 → 特征框(EWM 下行波动 hl=10 / EWM Sortino 20·60 / CSV 协变量)。
    全部 trailing EWM,t 行只含 ≤t 信息(PIT)。"""
    ls = ls.sort_index().astype(float)
    downside = ls.clip(upper=0.0)
    dvol = np.sqrt(downside.pow(2).ewm(halflife=10, min_periods=10).mean())

    def _sortino(hl: int) -> pd.Series:
        m = ls.ewm(halflife=hl, min_periods=hl).mean()
        d = np.sqrt(downside.pow(2).ewm(halflife=hl, min_periods=hl).mean())
        return m / (d + 1e-9)

    out = pd.DataFrame({"feat_dvol": dvol, "feat_sortino20": _sortino(20),
                        "feat_sortino60": _sortino(60)})
    if csv is not None and len(csv):
        out["feat_csv"] = csv.reindex(out.index).ffill()
    else:
        out["feat_csv"] = 0.0    # 无协变量 → 常数列(标准化后 z=0,不影响)
    return out.dropna()


def _pick_lambda(Xz: np.ndarray, lam_grid, seed: int):
    """λ 定标:取网格中(升序)首个「年切换 ≤MAX_SWITCH_PER_YEAR」的 λ(最灵敏且稳);
    都超 → 最大 λ 兜底。返回 (lam, centers, states, obj)。"""
    last = None
    for lam in sorted(lam_grid):
        C, s, obj = fit_jump_model(Xz, k=2, lam=lam, seed=seed)
        last = (float(lam), C, s, obj)
        yrs = max(len(Xz) / 244.0, 1e-9)
        if float((s[1:] != s[:-1]).sum()) / yrs <= MAX_SWITCH_PER_YEAR:
            return last
    return last


def walk_forward_regimes(feat: pd.DataFrame, warmup: int = WARMUP,
                         refit_every: int = REFIT_EVERY, lam_grid=LAM_GRID,
                         seed: int = 0, snapshot_cache: Optional[dict] = None):
    """逐日 PIT regime。i+1<warmup 不出行;每 refit_every 日 expanding 重拟
    (标准化 μσ 也只用 ≤t);其间 online_state 过滤。snapshot_cache({fit_asof: 快照})
    命中即免重拟(regen 快路径,等价性有测试守护)。
    返回 (df[date,p_fav,state,confirmed_since,fit_asof,lam], snapshots)。"""
    cols = [c for c in _FEAT_COLS if c in feat.columns]
    feat = feat[cols].sort_index()
    dates = list(feat.index)
    rows, snapshots = [], []
    cache = snapshot_cache or {}
    params = None
    prev_state: Optional[int] = None
    confirmed_since = None
    for i, t in enumerate(dates):
        if i + 1 < warmup:
            continue
        if params is None or (i + 1 - warmup) % refit_every == 0:
            asof = str(pd.Timestamp(t).date())
            sn = cache.get(asof)
            if sn and int(sn.get("n_obs", -1)) == i + 1:
                mu, sd = np.asarray(sn["mu"]), np.asarray(sn["sd"])
                C, lam = np.asarray(sn["centers"]), float(sn["lam"])
                fav, temp = int(sn["fav_state"]), float(sn["temp"])
                prev_state = int(sn["last_state"])
            else:
                hist = feat.iloc[: i + 1].values
                mu, sd = hist.mean(axis=0), hist.std(axis=0) + 1e-12
                lam, C, s_fit, obj = _pick_lambda((hist - mu) / sd, lam_grid, seed)
                fav = int(np.argmax(C[:, 1]))   # 标准化 sortino20 维更高的质心 = 有利态
                temp = max(obj / max(i + 1, 1), 1e-9)
                prev_state = int(s_fit[-1])
                sn = {"fit_asof": asof, "n_obs": i + 1, "lam": lam, "fav_state": fav,
                      "temp": temp, "last_state": prev_state,
                      "mu": mu.tolist(), "sd": sd.tolist(), "centers": C.tolist()}
            snapshots.append(sn)
            params = (mu, sd, C, lam, fav, temp, sn["fit_asof"])
        mu, sd, C, lam, fav, temp, fit_asof = params
        xz = (feat.iloc[i].values - mu) / sd
        st = online_state(xz, C, lam, prev_state)
        p = soft_prob(xz, C, lam, prev_state, temp)
        if confirmed_since is None or st != prev_state:
            confirmed_since = t                 # 先比 prev 再更新(状态连跑起点)
        prev_state = st
        rows.append({"date": t, "p_fav": float(p[fav]), "state": int(st == fav),
                     "confirmed_since": confirmed_since, "fit_asof": fit_asof,
                     "lam": float(lam)})
    return pd.DataFrame(rows), snapshots


def apply_regime_weights(sup: List[Tuple[str, float]], fam_of: Dict[str, str],
                         p_fav: Dict[str, float], activated: Set[str]):
    """纯函数:w_eff = w·((1−η)+η·clip(2·p_fav, lo, hi))。未激活族/无 p_fav → 原样。
    p_fav=0.5 → w_eff≡w(中性恒等,测试守护)。返回 (new_sup, per_factor 明细)。"""
    out, info = [], []
    for fid, w in sup:
        fam = fam_of.get(fid)
        p = p_fav.get(fam) if fam else None
        if fam in activated and p is not None:
            tilt = min(max(2.0 * float(p), TILT_LO), TILT_HI)
            w_eff = float(w) * ((1.0 - ETA) + ETA * tilt)
        else:
            w_eff = float(w)
        out.append((fid, w_eff))
        info.append({"id": fid, "family": fam, "w_user": float(w),
                     "w_eff": round(w_eff, 6),
                     "p_fav": (None if p is None else round(float(p), 4))})
    return out, info
```

- [ ] **Step 4.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_regime.py -q`
Expected: 5 passed(walk-forward 两例 ~1-3 分钟量级属正常:~24 次 expanding 拟合)

- [ ] **Step 4.5: Commit**

```bash
git add guanlan_v2/strategy/compute/factor_regime.py tests/test_factor_regime.py
git commit -m "feat(regime): 族 regime walk-forward(截断不变+快照缓存)+ 保守权重倾斜"
```

---

### Task 5: factor_regime 生产 build + resolve 胶水 + regen 接线

**Files:**
- Modify: `guanlan_v2/strategy/compute/factor_regime.py`
- Modify: `guanlan_v2/strategy/compute/regen.py`(factor_ic 步之后)
- Test: `tests/test_factor_regime.py`(追加)

- [ ] **Step 5.1: 追加失败测试**

```python
# 追加到 tests/test_factor_regime.py
import json


def _write_ls(tmp_path, monkeypatch, n=700):
    # 构造族 L/S 产物(经 factor_ls 模块常量 monkeypatch)
    from guanlan_v2.strategy.compute import factor_ls as FL
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "ls.parquet")
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2022-01-03", periods=n)
    df = pd.DataFrame({"date": idx, "family": "技术", "factor_id": "f1",
                       "ls_ret": rng.normal(0.001, 0.01, n),
                       "available_date": idx.shift(1)})
    df.loc[df.index[n // 2:], "ls_ret"] -= 0.004
    df.to_parquet(tmp_path / "ls.parquet", index=False)


def test_build_factor_regime_products_and_hindsight(tmp_path, monkeypatch):
    _write_ls(tmp_path, monkeypatch)
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_META_JSON", tmp_path / "rg_meta.json")
    monkeypatch.setattr(FR, "WARMUP", 200)
    n = FR.build_factor_regime()
    assert n > 0
    df = pd.read_parquet(tmp_path / "rg.parquet")
    assert set(df["family"]) == {"技术"}
    assert {"p_fav", "state", "state_hindsight", "confirmed_since", "source"} <= set(df.columns)
    assert (df["source"] == "factor-regime-jm").all()
    meta = json.loads((tmp_path / "rg_meta.json").read_text(encoding="utf-8"))
    assert meta["spec_hash"] == FR.SPEC_HASH and meta["trials"] >= 36
    # 同 spec 复跑幂等:trials 不涨
    FR.build_factor_regime()
    meta2 = json.loads((tmp_path / "rg_meta.json").read_text(encoding="utf-8"))
    assert meta2["trials"] == meta["trials"]


def test_build_without_ls_honest(tmp_path, monkeypatch):
    from guanlan_v2.strategy.compute import factor_ls as FL
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "none.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    assert FR.build_factor_regime() == 0               # 诚实缺席,不造数


def test_resolve_regime_weights_fallbacks_and_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(FR, "FACTOR_REGIME_GATE_JSON", tmp_path / "gate.json")
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    fx = [{"id": "fa_reversal", "w": 1.0}]              # catalog legacy:family=动量反转
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and b["applied"] is False and "闸产物缺失" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": "WRONG", "activated": ["动量反转"]}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "陈闸" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": FR.SPEC_HASH, "activated": []}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "0 族激活" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": FR.SPEC_HASH, "activated": ["动量反转"]}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "regime 产物缺失" in b["fallback_reason"]
    pd.DataFrame({"date": [pd.Timestamp("2026-07-01")], "family": ["动量反转"],
                  "p_fav": [1.0], "state": [1],
                  "confirmed_since": [pd.Timestamp("2026-06-20")]}
                 ).to_parquet(tmp_path / "rg.parquet", index=False)
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert b["applied"] is True and eff[0]["w"] == pytest.approx(1.25)   # p=1 封顶乘子
    eff, b = FR.resolve_regime_weights(fx, "2026-07-20")
    assert eff is None and "过期" in b["fallback_reason"]                 # 新鲜度 ≤3 交易日
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_regime.py -q`
Expected: 新 3 例 FAIL(AttributeError: build_factor_regime)

- [ ] **Step 5.3: 实现(追加到 factor_regime.py)**

```python
def _resolve_trials() -> int:
    """trials 持久化(评审纪律):同 spec 复跑幂等(不涨);spec 变更 → 累计 +36(整格预算:
    λ 3 × η 2 × 族 6)。DSR 按 max(36, 本值) deflate。"""
    if FACTOR_REGIME_META_JSON.exists():
        try:
            m = json.loads(FACTOR_REGIME_META_JSON.read_text(encoding="utf-8"))
            old = int(m.get("trials", 36))
            return old if m.get("spec_hash") == SPEC_HASH else old + 36
        except Exception:  # noqa: BLE001
            pass
    return 36


def build_factor_regime(end: Optional[str] = None) -> int:
    """族 L/S 产物 → 全族 walk-forward → factor_regime.parquet + meta。
    PIT 命门:特征索引 = available_date(t 行只用 available_date≤t 的 L/S)。
    快照缓存热路径:同 spec 下重放只拟合新到期的 refit 日(regen 内秒级)。
    另算全样本 hindsight 状态列(**仅诊断/whipsaw 护栏,绝不入权重**)。"""
    from guanlan_v2.strategy.compute.factor_ls import load_csv_series, load_family_ls

    fam_ls = load_family_ls()
    if fam_ls.empty:
        print("[factor_regime] 无 factor_ls 产物,诚实缺席(先全量回填)", flush=True)
        return 0
    csv = load_csv_series()
    old_snaps: Dict[str, list] = {}
    if FACTOR_REGIME_META_JSON.exists():
        try:
            m = json.loads(FACTOR_REGIME_META_JSON.read_text(encoding="utf-8"))
            if m.get("spec_hash") == SPEC_HASH:
                old_snaps = m.get("snapshots") or {}
        except Exception:  # noqa: BLE001
            old_snaps = {}
    parts, snaps_out = [], {}
    for fam, g in fam_ls.groupby("family"):
        s = pd.Series(g["ls_ret"].values,
                      index=pd.DatetimeIndex(g["available_date"])).sort_index()
        if end:
            s = s.loc[: pd.Timestamp(end)]
        feat = regime_features(s, csv)
        if len(feat) < WARMUP:
            print(f"[factor_regime] {fam} 热身不足({len(feat)}<{WARMUP}),诚实缺席", flush=True)
            continue
        cache = {sn["fit_asof"]: sn for sn in (old_snaps.get(fam) or [])}
        df, sn = walk_forward_regimes(feat, snapshot_cache=cache)
        if df.empty:
            continue
        # hindsight(全样本拟合 DP;非 PIT,仅供闸的吻合率护栏,绝不驱动权重)
        hist = feat.values
        mu, sd = hist.mean(axis=0), hist.std(axis=0) + 1e-12
        _lam, C_h, s_h, _obj = _pick_lambda((hist - mu) / sd, LAM_GRID, seed=0)
        fav_h = int(np.argmax(C_h[:, 1]))
        hs = pd.Series((s_h == fav_h).astype(int), index=feat.index)
        df["state_hindsight"] = hs.reindex(pd.DatetimeIndex(df["date"])).values
        df["family"] = fam
        df["source"] = "factor-regime-jm"
        parts.append(df)
        snaps_out[fam] = sn
    if not parts:
        return 0
    out = pd.concat(parts, ignore_index=True)
    tmp = str(FACTOR_REGIME_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_REGIME_PARQUET))
    meta = {"spec": SPEC, "spec_hash": SPEC_HASH,
            "asof": str(pd.Timestamp(out["date"].max()).date()),
            "families": sorted(snaps_out), "trials": _resolve_trials(),
            "snapshots": snaps_out}
    tmpj = str(FACTOR_REGIME_META_JSON) + ".tmp"
    with open(tmpj, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, default=str)
    os.replace(tmpj, str(FACTOR_REGIME_META_JSON))
    return len(out)


def resolve_regime_weights(factors, rdate: Optional[str]):
    """生产胶水(只在 ScreenIn.regimeWeights=True 时被调,默认路径零触碰):
    读闸+产物,双闸(activated 族 + 新鲜度 ≤FRESH_MAX_LAG 交易日)→
    (有效因子列表[{id,w}] 或 None, 徽章 dict)。任一环节缺 → 显式降级带 fallback_reason。"""
    from guanlan_v2.screen.catalog import FACTOR_DEFS

    badge = {"applied": False, "fallback_reason": None, "regime_asof": None,
             "per_factor": []}
    try:
        gate = (json.loads(FACTOR_REGIME_GATE_JSON.read_text(encoding="utf-8"))
                if FACTOR_REGIME_GATE_JSON.exists() else None)
        if not gate:
            badge["fallback_reason"] = "闸产物缺失(先人工跑 regime_gate)"
            return None, badge
        if gate.get("spec_hash") != SPEC_HASH:
            badge["fallback_reason"] = "闸 spec 指纹不符(陈闸,须重跑闸)"
            return None, badge
        activated = set(gate.get("activated") or [])
        if not activated:
            badge["fallback_reason"] = "0 族激活(合法结局:闸判无 OOS 增量)"
            return None, badge
        if not FACTOR_REGIME_PARQUET.exists():
            badge["fallback_reason"] = "regime 产物缺失"
            return None, badge
        df = pd.read_parquet(FACTOR_REGIME_PARQUET)
        asof = pd.Timestamp(df["date"].max())
        badge["regime_asof"] = str(asof.date())
        if rdate:
            lag = len(pd.bdate_range(asof, pd.Timestamp(rdate))) - 1
            if lag > FRESH_MAX_LAG:
                badge["fallback_reason"] = f"regime 产物过期({asof.date()} vs 排名 {rdate})"
                return None, badge
        last = df[df["date"] == df["date"].max()]
        p_fav = {str(r["family"]): float(r["p_fav"]) for _, r in last.iterrows()}
        sup, fam_of = [], {}
        for f in (factors or []):
            fid = getattr(f, "id", None) if not isinstance(f, dict) else f.get("id")
            fw = getattr(f, "w", 1.0) if not isinstance(f, dict) else f.get("w", 1.0)
            if not fid:
                continue
            sup.append((fid, float(fw)))
            fam_of[fid] = (FACTOR_DEFS.get(fid) or {}).get("family")
        new_sup, info = apply_regime_weights(sup, fam_of, p_fav, activated)
        badge.update({"applied": True, "per_factor": info})
        return [{"id": fid, "w": w} for fid, w in new_sup], badge
    except Exception as e:  # noqa: BLE001
        badge["fallback_reason"] = f"{type(e).__name__}: {e}"
        return None, badge
```

`guanlan_v2/strategy/compute/regen.py`:在 factor_ic 步骤的 try/except 块(L215-223)**之后**插入(照它的非阻断先例):

```python
        # 5b) 因子族 L/S 增量 + regime 层(非阻断;全量回填走独立子进程,不占本锁——评审前置)
        print("[regen] factor_ls → 族多空序列(日频增量)...", flush=True)
        try:
            from guanlan_v2.strategy.compute.factor_ls import update_factor_ls_incremental
            n_ls = update_factor_ls_incremental(end=end)
            out["factor_ls"] = n_ls
            print(f"  factor_ls +{n_ls} 行", flush=True)
        except Exception as e:  # noqa: BLE001
            out["factor_ls"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] factor_ls 失败(不阻断): {type(e).__name__}: {e}", flush=True)
        print("[regen] factor_regime → 族 regime(快照缓存重放)...", flush=True)
        try:
            from guanlan_v2.strategy.compute.factor_regime import build_factor_regime
            n_rg = build_factor_regime(end=end)
            out["factor_regime"] = n_rg
            print(f"  factor_regime {n_rg} 行", flush=True)
        except Exception as e:  # noqa: BLE001
            out["factor_regime"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] factor_regime 失败(不阻断): {type(e).__name__}: {e}", flush=True)
```

- [ ] **Step 5.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factor_regime.py -q`
Expected: 8 passed

- [ ] **Step 5.5: Commit**

```bash
git add guanlan_v2/strategy/compute/factor_regime.py guanlan_v2/strategy/compute/regen.py tests/test_factor_regime.py
git commit -m "feat(regime): 生产 build(hindsight诊断列+trials幂等)+ resolve 双闸胶水 + regen 非阻断接线"
```

---

### Task 6: regime_gate.py 统计件(NW-t / BH-FDR / eval_arms)

**Files:**
- Create: `guanlan_v2/strategy/compute/regime_gate.py`
- Test: `tests/test_regime_gate.py`

- [ ] **Step 6.1: 写失败测试**

```python
# tests/test_regime_gate.py
# 激活闸门禁:NW-t 反对称 / BH 手算 / 中性零差 / 阳性对照必过 / 阴性对照必拒 / 幂等。
import json

import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import regime_gate as RG
from guanlan_v2.strategy.compute.regime_gate import bh_fdr, eval_arms, nw_tstat


def test_nw_tstat_basic():
    rng = np.random.default_rng(0)
    x = rng.normal(0.5, 1.0, 200)
    t = nw_tstat(x)
    assert t is not None and t > 3.0
    assert nw_tstat(-x) == pytest.approx(-t)          # 反对称
    assert nw_tstat(np.ones(20)) is None              # 零方差 → 诚实 None
    assert nw_tstat(x[:5]) is None                    # 样本太少 → None


def test_bh_fdr_hand_case():
    keep = bh_fdr({"a": 0.001, "b": 0.02, "c": 0.5}, q=0.10)
    assert keep == {"a", "b"}                         # 阈:0.0333/0.0667/0.1
    assert bh_fdr({"a": None, "b": 0.9}) == set()


def _synth(seed=0, n_days=420, n_codes=40):
    """阳性对照:famA 载荷在 regime=1 段正向计价、regime=0 段反向;famB 恒正向。
    返回 (frames, close_wide, fams, pfav_true)。100 日块交替,信号强(噪声极小)。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_days + 1)
    codes = [f"C{i:02d}" for i in range(n_codes)]
    a, b = rng.normal(size=n_codes), rng.normal(size=n_codes)
    blocks = np.tile(np.repeat([1.0, 0.0], 100), n_days // 200 + 1)[:n_days]
    coefA = np.where(blocks > 0.5, 0.01, -0.01)
    ret = (coefA[:, None] * a[None, :] + 0.006 * b[None, :]
           + 0.0005 * rng.normal(size=(n_days, n_codes)))
    close = pd.DataFrame(
        100.0 * np.vstack([np.ones((1, n_codes)), np.exp(np.cumsum(ret, axis=0))]),
        index=idx, columns=codes)
    frames = {"fa": pd.DataFrame(np.tile(a, (n_days + 1, 1)), index=idx, columns=codes),
              "fb": pd.DataFrame(np.tile(b, (n_days + 1, 1)), index=idx, columns=codes)}
    fams = {"fa": "动量反转", "fb": "波动率"}
    pfav = {"动量反转": pd.Series(np.append(blocks, blocks[-1]), index=idx),
            "波动率": pd.Series(1.0, index=idx)}
    return frames, close, fams, pfav


def test_eval_arms_neutral_zero_delta():
    frames, close, fams, _ = _synth()
    pfav = {f: pd.Series(0.5, index=close.index) for f in set(fams.values())}
    res = eval_arms(frames, close, fams, pfav, close.index[0])
    assert res["ic_all"] == res["ic_static"]          # p=0.5 → 倾斜恒等 → 零差
```

- [ ] **Step 6.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_regime_gate.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 6.3: 实现(统计件 + eval_arms)**

`guanlan_v2/strategy/compute/regime_gate.py`(本任务落统计件与核心引擎,报告与 CLI 在 Task 7):

```python
# -*- coding: utf-8 -*-
"""regime 激活闸:walk-forward ΔrankIC 主判据 + CPCV 折块辅 + BH-FDR + 安慰剂 + 代理池 + whipsaw。

纪律复刻 cpcv.validate_dl_source 先例(GAT 全市场 −0.029 拒 / csi1000 +0.254 激活同一套):
- 闸只由人工 CLI 触发(regen 绝不自动跑)→ activated 落盘即人工确认动作;
- 0 族过闸 = 合法交付(结论:该范式在本仓因子上无 OOS 增量);
- 所有臂共用 walk-forward PIT 的 p_fav(逐日真 OOS);CPCV 档 = 按 make_splits test 折
  切块统计 Δ 分布(免「非连续 train 折上重拟切换罚模型」的统计不合法操作——评审镜头2)。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.factor_regime import (ETA, SPEC_HASH, TILT_HI,
                                                       TILT_LO, _resolve_trials)
from guanlan_v2.strategy.paths import FACTOR_REGIME_GATE_JSON

GATE_MIN_DIC = 0.005      # walk-forward mean ΔrankIC 门槛(spec §7 #1)
GATE_MIN_T = 2.0          # Newey-West t 门槛
GATE_Q = 0.10             # BH-FDR(spec §7 #2)
GATE_DSR = 0.5            # 同 cpcv.DL_GATE_DSR(spec §7 #4)
GATE_MAX_SWITCH = 2.0     # OOS 年均切换上限(spec §7 #7)
GATE_MIN_AGREE = 0.70     # 与 hindsight 状态吻合率下限
HORIZON = 5               # 非重叠换仓步长(与 factor_ic/strict_validate 口径一致)
N_PLACEBO = 20
PLACEBO_BLOCK = 63        # 季度块 shuffle(保自相关)
POOL_TOP = 200            # 代理候选池(近似生产 blend 真实作用面,评审必做修补)


def nw_tstat(x, lag: int = 5) -> Optional[float]:
    """Newey-West(Bartlett 核)均值 t;n<8 或方差退化 → None(诚实缺席)。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 8:
        return None
    e = x - x.mean()
    s = float(e @ e) / n
    for j in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - j / (lag + 1.0)) * float(e[j:] @ e[:-j]) / n
    if s <= 0:
        return None
    return float(x.mean() / np.sqrt(s / n))


def bh_fdr(pvals: Dict[str, Optional[float]], q: float = GATE_Q) -> set:
    """Benjamini-Hochberg:返回存活 key 集(单边 p;None/NaN 不参与)。"""
    items = sorted((p, k) for k, p in pvals.items()
                   if p is not None and np.isfinite(p))
    m = len(items)
    thr = 0
    for i, (p, _k) in enumerate(items, start=1):
        if p <= q * i / m:
            thr = i
    return {k for _p, k in items[:thr]}


def _zscore_cs(row: pd.Series) -> pd.Series:
    v = row.dropna()
    sd = v.std(ddof=0)
    if len(v) < 30 or not np.isfinite(sd) or sd <= 0:
        return pd.Series(dtype=float)
    return (v - v.mean()) / sd


def _rank_ic(score: pd.Series, ret: pd.Series) -> Optional[float]:
    df = pd.DataFrame({"s": score, "r": ret}).dropna()
    if len(df) < 30:
        return None
    ic = df["s"].rank().corr(df["r"].rank())
    return None if pd.isna(ic) else float(ic)


def eval_arms(frames: Dict[str, pd.DataFrame], close_wide: pd.DataFrame,
              fams: Dict[str, str], regime_pfav: Dict[str, pd.Series],
              warmup_date, horizon: int = HORIZON, pool_top: int = POOL_TOP,
              fam_arms: bool = True) -> dict:
    """核心引擎(纯,可注入合成数据):非重叠换仓日上算三类复合 rankIC——
    静态(等权基线)/ 逐族动态(仅该族倾斜,归因用)/ 全族动态;另出代理池(静态复合
    top-N 内)口径与动态 top-decile 多头超额(DSR 料)。regime_pfav 取「最后一个 ≤t」行
    (序列本身来自 walk-forward,PIT 已保证)。"""
    cw = close_wide.sort_index()
    fwd = cw.shift(-horizon) / cw - 1.0
    dates = [d for d in cw.index if d >= pd.Timestamp(warmup_date)]
    rb = dates[::horizon]
    families = sorted(set(fams.values()))
    res = {"dates": [], "ic_static": [], "ic_all": [], "ls_all": [],
           "ic_fam": {f: [] for f in families},
           "pool_static": [], "pool_all": []}
    for t in rb:
        if t not in fwd.index:
            continue
        r = fwd.loc[t]
        if r.dropna().empty:
            continue
        zs = {}
        for fid, fw_ in frames.items():
            if t in fw_.index:
                z = _zscore_cs(fw_.loc[t])
                if len(z):
                    zs[fid] = z
        if not zs:
            continue

        def _composite(weights: Dict[str, float]) -> pd.Series:
            wsum = sum(abs(w) for w in weights.values()) or 1.0
            acc = None
            for fid, z in zs.items():
                part = z * (weights.get(fid, 0.0) / wsum)
                acc = part if acc is None else acc.add(part, fill_value=0.0)
            return acc

        def _tilt_w(only_fam: Optional[str]) -> Dict[str, float]:
            w = {}
            for fid in zs:
                fam = fams[fid]
                p = None
                p_s = regime_pfav.get(fam)
                if p_s is not None:
                    sub = p_s.loc[:t].dropna()
                    p = float(sub.iloc[-1]) if len(sub) else None
                if p is None or (only_fam is not None and fam != only_fam):
                    w[fid] = 1.0
                else:
                    tilt = min(max(2.0 * p, TILT_LO), TILT_HI)
                    w[fid] = (1.0 - ETA) + ETA * tilt
            return w

        c_static = _composite({fid: 1.0 for fid in zs})
        ic_s = _rank_ic(c_static, r)
        if ic_s is None:
            continue
        c_all = _composite(_tilt_w(None))
        res["dates"].append(t)
        res["ic_static"].append(ic_s)
        res["ic_all"].append(_rank_ic(c_all, r))
        n_dec = max(1, int(len(c_all) * 0.1))
        top_d = c_all.sort_values(ascending=False).head(n_dec).index
        ls_v = float(r.reindex(top_d).mean() - r.reindex(c_all.index).mean())
        res["ls_all"].append(ls_v if np.isfinite(ls_v) else None)
        if fam_arms:
            for fam in families:
                res["ic_fam"][fam].append(_rank_ic(_composite(_tilt_w(fam)), r))
            top = c_static.sort_values(ascending=False).head(pool_top).index
            res["pool_static"].append(_rank_ic(c_static.reindex(top), r.reindex(top)))
            res["pool_all"].append(_rank_ic(c_all.reindex(top), r.reindex(top)))
    return res
```

- [ ] **Step 6.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_regime_gate.py -q`
Expected: 3 passed

- [ ] **Step 6.5: Commit**

```bash
git add guanlan_v2/strategy/compute/regime_gate.py tests/test_regime_gate.py
git commit -m "feat(regime): 闸统计件(NW-t/BH-FDR)+ eval_arms 三臂核心引擎"
```

---

### Task 7: regime_gate.py 报告(安慰剂/代理池/CPCV/DSR/护栏)+ CLI

**Files:**
- Modify: `guanlan_v2/strategy/compute/regime_gate.py`
- Test: `tests/test_regime_gate.py`(追加)

- [ ] **Step 7.1: 追加失败测试**

```python
# 追加到 tests/test_regime_gate.py
def test_gate_positive_control_activates():
    # 阳性对照(闸自证):真 regime 依赖信号 → 动量反转族必过闸(闸不是永拒的橡皮闸)。
    frames, close, fams, pfav = _synth()
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=None, n_trials=8, rng_seed=0)
    f = rep["families"]["动量反转"]
    assert f["d_ic_mean"] > RG.GATE_MIN_DIC and f["nw_t"] > RG.GATE_MIN_T
    assert f["bh_survive"] and "动量反转" in rep["activated"]
    assert rep["passes_gate"] is True
    assert rep["global"]["placebo_t"] is not None and rep["global"]["placebo_t"] >= 2.0
    assert rep["global"]["pool_d_ic"] is not None
    assert rep["global"]["cpcv_paths"] > 0 and rep["global"]["delay20_d_ic"] is not None


def test_gate_negative_control_rejects():
    # 阴性对照:p_fav 恒 0.5(无信息)→ 零差 → 全拒,activated 空(闸不是橡皮闸)。
    frames, close, fams, _ = _synth()
    pfav = {f: pd.Series(0.5, index=close.index) for f in set(fams.values())}
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=None, n_trials=8, rng_seed=0)
    assert rep["activated"] == [] and rep["passes_gate"] is False


def test_gate_idempotent_same_seed():
    frames, close, fams, pfav = _synth()
    r1 = RG.gate_report(frames, close, fams, pfav, close.index[0], None, 8, 0)
    r2 = RG.gate_report(frames, close, fams, pfav, close.index[0], None, 8, 0)
    assert json.dumps(r1, sort_keys=True, default=str) == \
           json.dumps(r2, sort_keys=True, default=str)


def test_gate_whipsaw_guardrail_blocks():
    # 过闸族若 switch_stats 超限(年切换>2 或吻合率<0.7)→ 被护栏拦下。
    frames, close, fams, pfav = _synth()
    ss = {"动量反转": {"switch_per_year": 9.0, "agree_hindsight": 0.5},
          "波动率": {"switch_per_year": 9.0, "agree_hindsight": 0.5}}
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=ss, n_trials=8, rng_seed=0)
    assert rep["activated"] == []
```

- [ ] **Step 7.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_regime_gate.py -q`
Expected: 新 4 例 FAIL(AttributeError: gate_report)

- [ ] **Step 7.3: 实现(追加到 regime_gate.py)**

```python
def _block_shuffle(s: pd.Series, rng, block: int = PLACEBO_BLOCK) -> pd.Series:
    """时间块打乱(保边际分布与块内自相关)——安慰剂臂料(嫁接自评审)。"""
    v = s.values
    blocks = [v[i:i + block] for i in range(0, len(v), block)]
    order = rng.permutation(len(blocks))
    out = np.concatenate([blocks[j] for j in order])[: len(v)]
    return pd.Series(out, index=s.index)


def _delta(a: List[Optional[float]], b: List[Optional[float]]) -> np.ndarray:
    x = np.array([np.nan if v is None else v for v in a], dtype=float)
    y = np.array([np.nan if v is None else v for v in b], dtype=float)
    d = x - y
    return d[np.isfinite(d)]


def gate_report(frames, close_wide, fams, regime_pfav, warmup_date,
                switch_stats: Optional[dict] = None, n_trials: Optional[int] = None,
                rng_seed: int = 0, n_placebo: int = N_PLACEBO,
                placebo_block: int = PLACEBO_BLOCK) -> dict:
    """全指标闸报告(纯、无时间戳 → 幂等;可注入合成数据自证)。
    switch_stats={family:{switch_per_year, agree_hindsight}}(生产由 run_gate 从
    regime 产物算好传入;None=跳过 whipsaw 护栏,仅合成测试用)。"""
    from guanlan_v2.strategy.compute.cpcv import _norm_cdf, deflated_sharpe, make_splits

    res = eval_arms(frames, close_wide, fams, regime_pfav, warmup_date)
    families = sorted(res["ic_fam"])
    out_fam: Dict[str, dict] = {}
    pvals: Dict[str, Optional[float]] = {}
    for fam in families:
        d = _delta(res["ic_fam"][fam], res["ic_static"])
        t = nw_tstat(d)
        p = (1.0 - _norm_cdf(t)) if t is not None else None
        pvals[fam] = p
        out_fam[fam] = {"n_rb": int(len(d)),
                        "d_ic_mean": (float(d.mean()) if len(d) else None),
                        "nw_t": t, "p": p}
    survivors = bh_fdr(pvals)

    # 全族臂 Δ + 安慰剂(block-shuffle p_fav;真臂须显著优于安慰剂——归因,spec §7 #5)
    d_all = _delta(res["ic_all"], res["ic_static"])
    real_all = float(d_all.mean()) if len(d_all) else None
    rng = np.random.default_rng(rng_seed)
    plac = []
    for _ in range(int(n_placebo)):
        shuf = {f: _block_shuffle(s, rng, placebo_block)
                for f, s in regime_pfav.items()}
        r2 = eval_arms(frames, close_wide, fams, shuf, warmup_date, fam_arms=False)
        dd = _delta(r2["ic_all"], r2["ic_static"])
        plac.append(float(dd.mean()) if len(dd) else np.nan)
    plac = np.array(plac, dtype=float)
    plac = plac[np.isfinite(plac)]
    placebo_t = None
    if real_all is not None and len(plac) >= 5 and plac.std(ddof=1) > 0:
        placebo_t = float((real_all - plac.mean()) / plac.std(ddof=1))

    # 代理池 do-no-harm(spec §7 #6,评审必做:闸认证与生产 blend 作用面同总体)
    d_pool = _delta(res["pool_all"], res["pool_static"])
    pool_d_ic = float(d_pool.mean()) if len(d_pool) else None

    # CPCV 折块(spec §7 #3):walk-forward Δ 序列按 make_splits test 折切块 → 路径分布
    d_by_date = {}
    for d_, a_, b_ in zip(res["dates"], res["ic_all"], res["ic_static"]):
        if a_ is not None and b_ is not None:
            d_by_date[d_] = a_ - b_
    paths = make_splits(res["dates"], n_groups=6, k=2, purge=HORIZON + 1, embargo=5)
    path_means = []
    for _tr, te in paths:
        vals = [d_by_date[d_] for d_ in te if d_ in d_by_date]
        if len(vals) >= 10:
            path_means.append(float(np.mean(vals)))
    cpcv_median = float(np.median(path_means)) if path_means else None
    cpcv_p05 = float(np.percentile(path_means, 5)) if path_means else None

    # DSR(spec §7 #4):动态全族臂 top-decile 多头超额(未年化,decile_metrics 口径)
    nt = int(n_trials if n_trials is not None else max(36, _resolve_trials()))
    dsr = deflated_sharpe([v for v in res["ls_all"] if v is not None], n_trials=nt)

    # 延迟敏感性(报告性,spec §7 末行):p_fav 滞后 20 交易日的 Δ
    lag_pfav = {f: s.shift(20) for f, s in regime_pfav.items()}
    r3 = eval_arms(frames, close_wide, fams, lag_pfav, warmup_date, fam_arms=False)
    d_lag = _delta(r3["ic_all"], r3["ic_static"])
    delay20_d_ic = float(d_lag.mean()) if len(d_lag) else None

    activated = []
    for fam in families:
        f = out_fam[fam]
        ok = (f["d_ic_mean"] is not None and f["d_ic_mean"] >= GATE_MIN_DIC
              and f["nw_t"] is not None and f["nw_t"] >= GATE_MIN_T
              and fam in survivors
              and placebo_t is not None and placebo_t >= 2.0
              and pool_d_ic is not None and pool_d_ic >= 0.0
              and cpcv_median is not None and cpcv_median > 0.0
              and cpcv_p05 is not None and cpcv_p05 > -0.005
              and dsr is not None and dsr >= GATE_DSR)
        if ok and switch_stats is not None:
            ss = switch_stats.get(fam) or {}
            ok = (ss.get("switch_per_year") is not None
                  and ss["switch_per_year"] <= GATE_MAX_SWITCH
                  and ss.get("agree_hindsight") is not None
                  and ss["agree_hindsight"] >= GATE_MIN_AGREE)
        f["bh_survive"] = fam in survivors
        f["pass"] = bool(ok)
        if ok:
            activated.append(fam)
    return {"spec_hash": SPEC_HASH, "n_trials": nt, "n_rb": len(res["dates"]),
            "families": out_fam,
            "global": {"d_ic_all": real_all, "placebo_t": placebo_t,
                       "placebo_mean": (float(plac.mean()) if len(plac) else None),
                       "pool_d_ic": pool_d_ic, "cpcv_median": cpcv_median,
                       "cpcv_p05": cpcv_p05, "cpcv_paths": len(path_means),
                       "dsr": dsr, "delay20_d_ic": delay20_d_ic},
            "switch_stats": switch_stats, "activated": activated,
            "passes_gate": bool(activated),
            "note": "passes 仅建议;闸只由人工 CLI 触发=人工确认;0 族过闸=合法结局。"}


def _switch_stats(rg: pd.DataFrame, warmup_date) -> dict:
    """whipsaw 护栏料:OOS 年均切换 + 与 hindsight 吻合率(hindsight 仅在此处消费)。"""
    out = {}
    for fam, g in rg.groupby("family"):
        g = g[g["date"] >= pd.Timestamp(warmup_date)].sort_values("date")
        if len(g) < 50:
            out[fam] = {"switch_per_year": None, "agree_hindsight": None}
            continue
        st = g["state"].to_numpy()
        sw = float((st[1:] != st[:-1]).sum()) / max(len(g) / 244.0, 1e-9)
        agree = None
        if "state_hindsight" in g.columns and g["state_hindsight"].notna().any():
            hh = g.dropna(subset=["state_hindsight"])
            agree = float((hh["state"] == hh["state_hindsight"]).mean())
        out[fam] = {"switch_per_year": sw, "agree_hindsight": agree}
    return out


def run_gate(universe: str = "csi800", start: str = "2016-01-01",
             end: Optional[str] = None) -> dict:
    """生产闸(人工 CLI 触发;重:物化因子框 + 20 次安慰剂重评,预计 30-60min)。"""
    from guanlan_v2.strategy.compute.factor_ls import materialize_factor_frames
    from guanlan_v2.strategy.paths import FACTOR_REGIME_PARQUET

    frames, close_wide, fams = materialize_factor_frames(universe, start, end)
    rg = pd.read_parquet(FACTOR_REGIME_PARQUET)
    regime_pfav = {str(fam): pd.Series(g["p_fav"].values,
                                       index=pd.DatetimeIndex(g["date"])).sort_index()
                   for fam, g in rg.groupby("family")}
    warmup_date = min(s.index.min() for s in regime_pfav.values())
    rep = gate_report(frames, close_wide, fams, regime_pfav, warmup_date,
                      switch_stats=_switch_stats(rg, warmup_date))
    rep["asof"] = str(pd.Timestamp(rg["date"].max()).date())
    rep["universe"], rep["start"] = universe, start
    tmp = str(FACTOR_REGIME_GATE_JSON) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, str(FACTOR_REGIME_GATE_JSON))
    return rep


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="regime 激活闸(人工触发=人工确认)")
    ap.add_argument("--universe", default="csi800")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    rep = run_gate(a.universe, a.start, a.end)
    brief = {"activated": rep["activated"], "asof": rep.get("asof"),
             "global": rep["global"],
             "families": {k: {kk: v.get(kk) for kk in ("d_ic_mean", "nw_t", "pass")}
                          for k, v in rep["families"].items()}}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
```

- [ ] **Step 7.4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_regime_gate.py -q`
Expected: 7 passed(阳性/幂等两例含 20+20 次安慰剂重评,合成数据下 ~1-2 分钟属正常)

- [ ] **Step 7.5: Commit**

```bash
git add guanlan_v2/strategy/compute/regime_gate.py tests/test_regime_gate.py
git commit -m "feat(regime): 激活闸全指标报告(安慰剂/代理池/CPCV折块/DSR/whipsaw)+ 人工CLI"
```

---

### Task 8: screen/api.py opt-in 接线 + GET /screen/regime

**⚠️ 动手前:`git status -- guanlan_v2/screen/api.py`,若并行会话有未提交改动 → 停下与用户确认。**

**Files:**
- Modify: `guanlan_v2/screen/api.py`(三处:ScreenIn / _screen_via_v4 / build_screen_router)
- Test: `tests/test_screen_api.py`(追加)

- [ ] **Step 8.1: 追加失败测试**

```python
# 追加到 tests/test_screen_api.py(沿用本文件既有 app/client 构造;若无,用下面的独立构造)
import json as _json

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _rg_client():
    from guanlan_v2.screen.api import build_screen_router
    app = FastAPI()
    app.include_router(build_screen_router())
    return TestClient(app)


def test_screen_run_default_path_untouched(monkeypatch):
    # 红线1硬回归:缺省请求绝不触碰 regime 代码,响应无 regime_weights 键。
    import guanlan_v2.strategy.compute.factor_regime as FR
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return None, {}

    monkeypatch.setattr(FR, "resolve_regime_weights", _spy)
    c = _rg_client()
    r = c.post("/screen/run", json={"topN": 5})
    assert r.status_code == 200
    assert called["n"] == 0
    assert "regime_weights" not in r.json()


def test_screen_run_optin_badge(monkeypatch):
    # opt-in:v4 路径可用时必带 regime_weights 徽章(applied 或 fallback_reason 二选一非空)。
    c = _rg_client()
    r = c.post("/screen/run", json={"topN": 5, "regimeWeights": True})
    assert r.status_code == 200
    j = r.json()
    if j.get("source") != "v4_ranking":
        pytest.skip("v4 产物不可用(artifacts 未恢复),opt-in 徽章仅在 v4 路径下发")
    assert "regime_weights" in j
    b = j["regime_weights"]
    assert b["applied"] is True or b["fallback_reason"]


def test_screen_regime_endpoint_honest(monkeypatch, tmp_path):
    # GET /screen/regime:缺产物 → ok:false;在位 → families+gate 下发。
    import guanlan_v2.strategy.compute.factor_regime as FR
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_GATE_JSON", tmp_path / "gate.json")
    c = _rg_client()
    assert c.get("/screen/regime").json()["ok"] is False
    pd.DataFrame({"date": [pd.Timestamp("2026-07-01")], "family": ["技术"],
                  "p_fav": [0.8], "state": [1],
                  "confirmed_since": [pd.Timestamp("2026-06-20")]}
                 ).to_parquet(tmp_path / "rg.parquet", index=False)
    (tmp_path / "gate.json").write_text(_json.dumps(
        {"spec_hash": FR.SPEC_HASH, "activated": ["技术"], "asof": "2026-07-01"}),
        encoding="utf-8")
    j = c.get("/screen/regime").json()
    assert j["ok"] is True and j["families"][0]["family"] == "技术"
    assert j["gate"]["activated"] == ["技术"] and j["gate"]["stale"] is False
```

- [ ] **Step 8.2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py -k "regime or default_path" -q`
Expected: FAIL(/screen/regime 404、徽章缺失)

- [ ] **Step 8.3: 实现(api.py 四处修改)**

① `ScreenIn`(L41-60)`model: str = "prod"` 行后加一行:

```python
    regimeWeights: bool = False  # regime 因子族动态权重(opt-in;默认 False=路径零触碰,须过闸+新鲜双闸)
```

② `_screen_via_v4` 中 `disp, regime, metrics = _panel_enrich(codes, body.freq, body.factors)`(L733)替换为:

```python
    # —— regime 条件化(opt-in;缺省 False 分支不执行任何新代码——红线1)——
    _rw_badge = None
    _factors_eff = body.factors
    if getattr(body, "regimeWeights", False):
        try:
            from guanlan_v2.strategy.compute.factor_regime import resolve_regime_weights
            _eff, _rw_badge = resolve_regime_weights(body.factors, rdate)
            if _eff is not None:
                _factors_eff = _eff
        except Exception as _e:  # noqa: BLE001
            _rw_badge = {"applied": False, "fallback_reason": f"{type(_e).__name__}: {_e}",
                         "regime_asof": None, "per_factor": []}
    disp, regime, metrics = _panel_enrich(codes, body.freq, _factors_eff)
```

③ 同函数返回的 `JSONResponse({...})` 里,`"panel_ok": bool(disp),` 之后加:

```python
        # regime 徽章:仅 opt-in 请求才带此键(缺省响应逐字节不变);降级带 fallback_reason 显形
        **({"regime_weights": _rw_badge} if _rw_badge is not None else {}),
```

④ `build_screen_router()` 内(`/factors` 路由旁)新增只读端点:

```python
    @router.get("/regime")
    def screen_regime():
        """因子族 regime 只读:各族 p_fav/confirmed_since + 激活闸状态(前端因子卡/帷幄消费)。
        缺产物 → ok:false 诚实缺席,不造数。"""
        try:
            import json as _json

            import pandas as _pd

            from guanlan_v2.strategy.compute.factor_regime import (
                FACTOR_REGIME_GATE_JSON as _GJ, FACTOR_REGIME_PARQUET as _RP, SPEC_HASH)
            if not _RP.exists():
                return {"ok": False, "reason": "regime 产物缺失(先全量回填)"}
            df = _pd.read_parquet(_RP)
            last = df[df["date"] == df["date"].max()]
            gate = (_json.loads(_GJ.read_text(encoding="utf-8")) if _GJ.exists() else None)
            return {"ok": True, "asof": str(_pd.Timestamp(df["date"].max()).date()),
                    "spec_hash": SPEC_HASH,
                    "families": [{"family": str(r["family"]),
                                  "p_fav": round(float(r["p_fav"]), 4),
                                  "state": int(r["state"]),
                                  "confirmed_since": str(_pd.Timestamp(r["confirmed_since"]).date())}
                                 for _, r in last.iterrows()],
                    "gate": ({"activated": gate.get("activated"), "asof": gate.get("asof"),
                              "stale": gate.get("spec_hash") != SPEC_HASH} if gate else None)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
```

注:端点从 factor_regime 模块命名空间导入两个路径常量(Task 4 已 import 进该模块),测试的 monkeypatch 才能生效——不要直接从 paths 导入。

- [ ] **Step 8.4: 跑测试确认通过 + 全量回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py -q`
Expected: 全部 passed(v4 产物缺失环境下 opt-in 例 skip 属预期)

- [ ] **Step 8.5: Commit**

```bash
git add guanlan_v2/screen/api.py tests/test_screen_api.py
git commit -m "feat(regime): /screen/run opt-in 接线(双闸+徽章,默认路径零触碰)+ GET /screen/regime"
```

---

### Task 9: 前端填充(现有选股页,不新建界面)

**Files:**
- Modify: `ui/screen/观澜 · 选股.html`

前端为单文件内联 JS。**红线:只填充现有布局,逐字保留其余部分;不新建/简化界面。**

- [ ] **Step 9.1: 定位控件区**:在该 html 内搜 `blend`(α 滑杆所在控件区)与 `/screen/run` 的请求组装处(cfg 对象)。

- [ ] **Step 9.2: 三处小改**(样式沿用同区现有 class):
  1. **toggle**:α 滑杆同区插入复选框 `<label class="…同区现有class…"><input type="checkbox" id="rgw"> 风格权重(regime)</label>`;请求组装处的 cfg 对象加 `regimeWeights: !!document.getElementById('rgw')?.checked`。
  2. **徽章**:渲染结果处读 `resp.regime_weights`——`applied:true` → 在结果头部现有徽章行追加 `风格权重·<regime_asof>`(title 悬浮显示 per_factor 明细);`applied:false` → 灰徽章显示 `fallback_reason`(诚实降级显形,绝不静默)。键不存在(未 opt-in)→ 不渲染。
  3. **族 p_fav**:页面加载时 `fetch('/screen/regime')`,`ok:true` 时在左栏因子卡族标题旁追加小字 `p_fav`(两位小数)+ 激活族打点;`ok:false` → 不渲染(诚实缺席)。

- [ ] **Step 9.3: bump 静态资源版本**:该页若有 `?v=` 查询串引用,用 Edit 递增(坑备忘:bump ?v 用 Edit)。

- [ ] **Step 9.4: 核验**:重启 9999 后浏览器打开选股页,确认默认无 toggle 勾选时页面与改前一致;勾选后请求体带 `regimeWeights:true` 且徽章出现(真机核验并入 Task 10)。

- [ ] **Step 9.5: Commit**

```bash
git add "ui/screen/观澜 · 选股.html"
git commit -m "feat(regime): 选股页 opt-in toggle + 诚实徽章 + 族 p_fav 展示(只填充)"
```

---

### Task 10: 全量回归 + 真机执行手册(生产数据操作,人工在场)

- [ ] **Step 10.1: 全量测试回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部 passed(已知环境性失败按会话惯例甄别,如 test_vendored_hashes_match)

- [ ] **Step 10.2: 真机回填(独立子进程,~10-30min,不占 regen 锁)**

```bash
G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.strategy.compute.factor_ls --start 2016-01-01
```
Expected: `factor_ls 全量回填 <N> 行 -> ...factor_ls_returns.parquet`(N ≈ 数万级)

- [ ] **Step 10.3: 构建 regime 层(冷算 <10min 目标)**

```bash
G:/financial-analyst/.venv/Scripts/python.exe -c "from guanlan_v2.strategy.compute.factor_regime import build_factor_regime; print(build_factor_regime())"
```
验收:打印行数 >0;逐族日志无静默;抽查 parquet 每族年均切换 0.8-1.5 次(λ 定标报告)。

- [ ] **Step 10.4: 人工跑闸(30-60min;跑闸=人工确认动作)**

```bash
G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.strategy.compute.regime_gate
```
验收:brief JSON 落终端 + factor_regime_gate.json 落盘;**如实向用户汇报每族
d_ic/nw_t/pass 与 global(placebo_t/pool/cpcv/dsr/delay20)——0 族过闸也是合法交付,
如实报告并保持开关关闭,不算失败。**

- [ ] **Step 10.5: 端到端核验(需 9999 + v4 产物在位)**:重启 9999(手动
  `Start-Process G:\financial-analyst\.venv\Scripts\python.exe guanlan_v2\server.py`,
  看门狗坑备忘)→ `GET /screen/regime` 下发各族 → 选股页勾选 toggle 跑一次,
  核对徽章 applied/fallback_reason 与 per_factor 的 w_eff 符合公式。

- [ ] **Step 10.6: 收尾**:如实汇报闸结论 → 用户决定是否保留激活;更新记忆
  (`regime-factor-weights.md` 状态行);走 superpowers:finishing-a-development-branch。

---

## Self-Review(已自查)

- **Spec 覆盖**:§5.1→Task 2/3;§5.2→Task 1;§5.3→Task 4/5;§5.4→Task 6/7(CPCV 档按
  spec §7 注 * 的降级条款,以「walk-forward Δ 按 test 折切块」实现——比原「train 折最长
  连续段重拟」更干净地满足意图:逐日仍真 OOS 且免评审镜头2 指出的非连续拟合缺陷,
  实施时在 gate 报告 note 里写明此口径);§5.5→Task 8;§5.6→Task 5;P4 图谱(spec 可选)
  →不在本计划,留挂账。
- **占位符扫描**:无 TBD/TODO;每步含完整代码/命令/预期输出。Task 9 前端为内联 HTML,
  给出插入内容与定位方法(文件过大不宜全文内嵌,属定位型步骤而非占位)。
- **类型一致性**:`materialize_factor_frames` 返回三元组在 Task 3/7 一致;
  `apply_regime_weights(sup, fam_of, p_fav, activated)` 在 Task 4/5 一致;
  `resolve_regime_weights → ([{id,w}]|None, badge)` 与 Task 8 调用点一致;
  常量(ETA/TILT_*/SPEC_HASH/FRESH_MAX_LAG)单一出处 factor_regime。
