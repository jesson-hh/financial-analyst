# CPCV + Deflated Sharpe 验证层 Implementation Plan(①)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给"任意 registry 模型"加一个独立验证层——快速档(读冻结快照·秒级)+ 严格档(全历史 retrain-CPCV·~1h),产出夏普分布 + Deflated Sharpe(DSR)+ RankIC 分布,纯测量、不碰交易信号。

**Architecture:** 新 `compute/cpcv.py`(纯引擎:CPCV 切分 + purge/embargo + 标准化多头超额组合指标 + DSR + 单模型子区间一致性 + `quick_validate`/`strict_validate` + `retrain_core` 按 kind 分派 + `__main__`)。**不改 `v4.py`**(只 import 其 `build_feature_panel`/`add_ind_turnover`/`add_breadth_resid`/`_select_mf`/`LGB_PARAMS` 等 primitive,build_v4 字节不动——避开并发 dl-ensemble 会话对 v4.py 的编辑 + 满足红线)。`model_health.py` 加 `write_cpcv`/`load_cpcv_summary`;`screen/api.py` 加 `/model/validate`(快速档内联秒级 + 严格档异步子进程)+ `/status`。前端 TopBar 体检卡显分布 + 模型工坊「严格验证」按钮 + 每变体 DSR 徽章。

**Tech Stack:** Python 3.13 / pandas / numpy / scipy.stats(正态 CDF/PPF;缺则 erf+Acklam 近似)/ LightGBM(经 v4 primitive)/ FastAPI / React(JSX)/ pytest。

**Spec:** `docs/superpowers/specs/2026-06-22-cpcv-deflated-sharpe-validation-design.md`(**以 §12 ②衔接为最新口径**)。

**并发**:本计划在基于 `feat/cpcv-validation` 的**独立 git worktree** 实施(`compute/model_workflow.py`/registry/ranking 等 ② 产物已在该分支)。不占主工作树(让给并发 dl-ensemble 会话);真机 live 验证(端点/UI)在主树 9999 上协调进行。

**红线:** PIT 不看未来(快速档快照冻结;严格档 purge 覆盖 5 日标签窗 + embargo);`build_v4`/`v4.py` 零改;产物只读(只新写 `model_cpcv*`,绝不改 model_health 三产物);不碰 `/screen` 选股算法;诚实缺席(样本不足 → `ready=False`/None,绝不编数);严格档子进程沿用 9999 看门狗。

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `guanlan_v2/strategy/compute/cpcv.py` | 验证引擎全部:splits/purge/embargo、组合指标、DSR、子区间一致性、quick/strict、retrain_core、__main__ | **新建** |
| `guanlan_v2/strategy/model_health.py` | `write_cpcv` / `load_cpcv_summary`(落 `model_cpcv_<id>.json` 摘要) | 加函数 |
| `guanlan_v2/screen/api.py` | `POST /model/validate`(quick 内联 / strict 异步子进程)+ `GET /model/validate/status` | 加端点 |
| `ui/screen/screen-app.jsx` | 模型工坊「快验/严格验证」按钮 + 进度轮询 + 每变体 DSR 徽章;TopBar 体检卡加分布 | 填充现有 UI |
| `tests/test_cpcv_engine.py` | 引擎纯函数单测(splits/指标/DSR) | 新建 |
| `tests/test_cpcv_validate.py` | quick/strict/retrain_core/存读/端点 单测 | 新建 |

**指标口径(spec §7,锁定)**:标准化组合 = 按 `lgb_pct` 取 **top decile 等权多头**,**非重叠每 5 交易日换仓**;头条 = **多头超额** = top-decile 收益 − 全域(当日有 lgb_pct 的全部票)等权收益;另附 **多空价差**(top−bottom decile)诊断;**RankIC** = lgb_pct 与未来 5 日收益的截面 spearman。夏普按 5 日持有期年化(×√(252/5))。

---

# Phase G — CPCV 引擎纯函数

### Task G1:CPCV 切分 + purge/embargo

**Files:** Create `guanlan_v2/strategy/compute/cpcv.py`; Create `tests/test_cpcv_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cpcv_engine.py
import pandas as pd
from guanlan_v2.strategy.compute import cpcv


def test_splits_count_and_no_overlap():
    dates = pd.bdate_range("2022-01-03", periods=300)
    splits = cpcv.make_splits(dates, n_groups=6, k=2, purge=5, embargo=5)
    assert len(splits) == 15
    for tr, te in splits:
        assert set(tr).isdisjoint(set(te))
        assert len(te) > 0 and len(tr) > 0


def test_purge_embargo_removes_boundary_train_dates():
    dates = pd.bdate_range("2022-01-03", periods=120)
    splits = cpcv.make_splits(dates, n_groups=6, k=1, purge=5, embargo=5)
    for tr, te in splits:
        te_sorted = sorted(te); trs = set(tr)
        lo, hi = te_sorted[0], te_sorted[-1]
        pre = [d for d in dates if d < lo][-5:]
        assert all(d not in trs for d in pre), "purge 未覆盖标签窗"
        post = [d for d in dates if d > hi][:5]
        assert all(d not in trs for d in post), "embargo 未生效"
```

- [ ] **Step 2: 跑测试确认失败** — `python -m pytest tests/test_cpcv_engine.py -k splits -v` → FAIL(no attribute make_splits)

- [ ] **Step 3: 实现 `make_splits`** — Create `guanlan_v2/strategy/compute/cpcv.py`:

```python
# -*- coding: utf-8 -*-
"""CPCV + Deflated Sharpe 验证引擎(纯测量,不碰交易信号)。

快速档:读 model_health 冻结快照 → 组合收益分布 + DSR(秒级,零看未来)。
严格档:全历史按组合净化交叉验证(CPCV)重训 → 路径分布 + DSR(~1h)。
不改 v4.py:严格档复用 v4 面板 primitive + workflow 的 _materialize_xy/_build_model 做掩码 fit/predict。"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ANNUALIZE = (252.0 / 5.0) ** 0.5   # 5 日持有期年化


def make_splits(dates, n_groups: int = 6, k: int = 2, purge: int = 5, embargo: int = 5):
    """有序唯一交易日切 n_groups 连续段,枚举 C(n_groups,k) 组合当测试段;每个测试段做
    purge(挖其前 purge 个交易日:训练样本 horizon 标签窗会探入测试段)+ embargo(剔其后 embargo 个)。
    返回 [(train_dates:list, test_dates:list), ...]。"""
    uniq = list(pd.DatetimeIndex(sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique())))
    n = len(uniq)
    if n < n_groups * 2:
        return []
    bounds = [round(i * n / n_groups) for i in range(n_groups + 1)]
    groups = [uniq[bounds[i]:bounds[i + 1]] for i in range(n_groups)]
    pos = {d: i for i, d in enumerate(uniq)}
    out = []
    for combo in itertools.combinations(range(n_groups), k):
        test = [d for gi in combo for d in groups[gi]]
        drop = set(pos[d] for d in test)
        for tp in list(drop):
            for j in range(1, purge + 1):
                drop.add(tp - j)
            for j in range(1, embargo + 1):
                drop.add(tp + j)
        train = [uniq[i] for i in range(n) if i not in drop]
        out.append((train, sorted(test)))
    return out
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_cpcv_engine.py -k "splits or purge" -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_engine.py
git commit -m "feat(cpcv): make_splits — CPCV 组合切分 + purge/embargo(防标签泄漏)"
```

---

### Task G2:标准化组合指标(多头超额夏普 + RankIC)

**Files:** Modify `cpcv.py`; Modify `tests/test_cpcv_engine.py`

- [ ] **Step 1: 写失败测试**

```python
def test_decile_metrics_long_excess_and_ic():
    from guanlan_v2.strategy.compute import cpcv
    rows = []
    for d in pd.bdate_range("2022-01-03", periods=3):
        for i in range(100):
            rows.append({"date": d, "code": f"C{i:03d}", "lgb_pct": i / 99.0, "fwd": i / 99.0 * 0.1})
    m = cpcv.decile_metrics(pd.DataFrame(rows))
    assert m["rank_ic_mean"] > 0.9
    assert m["long_excess_ret"][0] > 0
    assert m["n"] == 3
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_engine.py -k decile -v` → FAIL

- [ ] **Step 3: 实现 `decile_metrics` + `sharpe`** — append to `cpcv.py`:

```python
def decile_metrics(panel: pd.DataFrame, decile: float = 0.1) -> Dict[str, Any]:
    """panel: 长表 [date, code, lgb_pct, fwd](fwd=该 date 起未来5日收益,已 PIT)。
    每换仓日:top/bottom decile 等权 → 多头超额(top−全域等权)、多空价差(top−bottom)、截面 rank-IC。
    截面<20 跳过(诚实)。"""
    le, ls, ics, used = [], [], [], []
    for d, g in panel.dropna(subset=["lgb_pct", "fwd"]).groupby("date"):
        if len(g) < 20:
            continue
        q_hi = g["lgb_pct"].quantile(1 - decile); q_lo = g["lgb_pct"].quantile(decile)
        top = g[g["lgb_pct"] >= q_hi]["fwd"]; bot = g[g["lgb_pct"] <= q_lo]["fwd"]
        if not len(top):
            continue
        le.append(float(top.mean() - g["fwd"].mean()))
        if len(bot):
            ls.append(float(top.mean() - bot.mean()))
        ic = g["lgb_pct"].rank().corr(g["fwd"].rank())
        if pd.notna(ic):
            ics.append(float(ic))
        used.append(pd.Timestamp(d))
    return {"long_excess_ret": le, "long_short_ret": ls, "rank_ic": ics,
            "rank_ic_mean": float(np.mean(ics)) if ics else None,
            "dates": [str(x.date()) for x in used], "n": len(le)}


def sharpe(returns: List[float], annualize: float = ANNUALIZE) -> Optional[float]:
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    if len(r) < 3 or r.std(ddof=1) == 0:
        return None
    return float(r.mean() / r.std(ddof=1) * annualize)
```

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_engine.py -k decile -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_engine.py
git commit -m "feat(cpcv): decile_metrics(多头超额/多空价差/RankIC)+ sharpe"
```

---

### Task G3:Deflated Sharpe Ratio(DSR)

**Files:** Modify `cpcv.py`; Modify `tests/test_cpcv_engine.py`

- [ ] **Step 1: 写失败测试**

```python
def test_dsr_basic_properties():
    from guanlan_v2.strategy.compute import cpcv
    import numpy as np
    rng = np.random.default_rng(0)
    good = list(rng.normal(0.02, 0.01, 60)); noise = list(rng.normal(0.0, 0.02, 60))
    dg = cpcv.deflated_sharpe(good, n_trials=10); dn = cpcv.deflated_sharpe(noise, n_trials=10)
    assert 0.0 <= dg <= 1.0 and 0.0 <= dn <= 1.0 and dg > dn
    assert cpcv.deflated_sharpe(good, n_trials=1000) <= cpcv.deflated_sharpe(good, n_trials=2) + 1e-9


def test_dsr_insufficient_returns_none():
    from guanlan_v2.strategy.compute import cpcv
    assert cpcv.deflated_sharpe([0.01, 0.02], n_trials=5) is None
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_engine.py -k dsr -v` → FAIL

- [ ] **Step 3: 实现 `deflated_sharpe`**(Bailey & López de Prado)— append to `cpcv.py`:

```python
def _norm_cdf(x: float) -> float:
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    try:
        from scipy.stats import norm
        return float(norm.ppf(p))
    except Exception:  # noqa: BLE001 — Acklam 近似(避免硬依赖 scipy)
        import math
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
        pl, ph = 0.02425, 1 - 0.02425
        if p < pl:
            q = math.sqrt(-2 * math.log(p))
            return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p > ph:
            q = math.sqrt(-2 * math.log(1 - p))
            return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


_EULER = 0.5772156649015329


def deflated_sharpe(returns: List[float], n_trials: int, sharpes_std: Optional[float] = None):
    """DSR = P(真夏普 > SR0),SR0 = N 次试验下期望最大夏普(噪声基准)。返回 [0,1];
    样本<10 或零波动 → None。夏普口径=每周期(未年化)。sharpes_std=各试验夏普标准差(缺→1)。"""
    import math
    r = np.asarray([x for x in returns if x == x], dtype="float64")
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return None
    sr = r.mean() / r.std(ddof=1)
    g3 = float(pd.Series(r).skew())
    g4 = float(pd.Series(r).kurtosis()) + 3.0          # pandas 超额峰度 → 普通峰度
    N = max(2, int(n_trials)); v = sharpes_std if (sharpes_std and sharpes_std > 0) else 1.0
    sr0 = v * ((1 - _EULER) * _norm_ppf(1 - 1.0 / N) + _EULER * _norm_ppf(1 - 1.0 / (N * math.e)))
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4.0 * sr * sr))
    return float(_norm_cdf((sr - sr0) * math.sqrt(T - 1) / denom))
```

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_engine.py -k dsr -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_engine.py
git commit -m "feat(cpcv): deflated_sharpe(Bailey-LdP·N次试验期望最大夏普deflation·诚实None)"
```

---

# Phase H — 快速档(读冻结快照)

### Task H1:`quick_validate`

**Files:** Modify `cpcv.py`; Create `tests/test_cpcv_validate.py`

- [ ] **Step 1: 写失败测试(合成快照 parquet,桩掉前向收益取数)**

```python
# tests/test_cpcv_validate.py
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import cpcv


def _seed(tmp_path, monkeypatch, n_days=40, n_codes=150):
    from guanlan_v2.strategy import model_health as mh
    monkeypatch.setattr(mh, "SCORE_HISTORY_PARQUET", tmp_path / "score_hist.parquet")
    monkeypatch.setattr(mh, "VINTAGE_IC_PARQUET", tmp_path / "vintage.parquet")
    dates = pd.bdate_range("2026-01-05", periods=n_days); rng = np.random.default_rng(1)
    snap = [{"date": str(d.date()), "code": f"C{i:03d}", "lgb_pct": rng.random()}
            for d in dates for i in range(n_codes)]
    pd.DataFrame(snap).to_parquet(tmp_path / "score_hist.parquet", index=False)
    pd.DataFrame({"date": [str(d.date()) for d in dates], "ic": rng.normal(0.02, 0.05, n_days),
                  "n": n_codes}).to_parquet(tmp_path / "vintage.parquet", index=False)
    return dates


def test_quick_validate_returns_sharpe_dsr(tmp_path, monkeypatch):
    dates = _seed(tmp_path, monkeypatch, n_days=40)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(r.date, r.code): float(r.lgb_pct) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.quick_validate(model_id="prod")
    assert out["ready"] is True and "sharpe" in out and "dsr" in out and out["n_oos_days"] >= 10


def test_quick_validate_insufficient_days(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, n_days=5)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots", lambda hist, horizon=5: {})
    out = cpcv.quick_validate(model_id="prod")
    assert out["ready"] is False and "证据不足" in out["note"]
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_validate.py -k quick -v` → FAIL

- [ ] **Step 3: 实现** — append to `cpcv.py`:

```python
MIN_OOS_DAYS = 10


def _fwd_returns_for_snapshots(hist: pd.DataFrame, horizon: int = 5) -> Dict[Tuple[str, str], float]:
    """对快照 (date,code) 算真 horizon 日前向收益(引擎 close bins,PIT:只取已实现)。单测桩掉。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
    ld = QlibBinaryLoader(DEFAULT_PROVIDER)
    probe = ld._read_bin("SH600519", "close")
    if probe is None or probe.dropna().empty:
        return {}
    last = pd.Timestamp(probe.dropna().index[-1])
    cal = pd.DatetimeIndex([d for d in ld._load_calendar("day") if pd.Timestamp(d) <= last])
    by_code = {c: ld._read_bin(str(c), "close") for c in hist["code"].astype(str).unique()}
    out: Dict[Tuple[str, str], float] = {}
    for d in sorted(hist["date"].astype(str).unique()):
        ts = pd.Timestamp(d); posn = cal.searchsorted(ts)
        if posn >= len(cal) or cal[posn] != ts or posn + horizon >= len(cal):
            continue
        t1 = cal[posn + horizon]
        for c in hist[hist["date"] == d]["code"].astype(str):
            s = by_code.get(c)
            if s is None:
                continue
            c0, c1 = s.get(ts), s.get(t1)
            if c0 and c1 and pd.notna(c0) and pd.notna(c1) and float(c0) > 0:
                out[(d, c)] = float(c1) / float(c0) - 1.0
    return out


def _registry_trials() -> int:
    try:
        from guanlan_v2.screen.model_registry import list_variants
        return max(2, len(list_variants()))
    except Exception:  # noqa: BLE001
        return 2


def quick_validate(model_id: Optional[str] = None, n_trials: Optional[int] = None) -> Dict[str, Any]:
    """读 model_health 冻结快照 → 多头超额夏普 + DSR + RankIC 分布。秒级零看未来;不足→ready=False。
    仅 prod 当前积累快照;变体暂无 → ready=False(诚实)。"""
    from guanlan_v2.strategy import model_health as mh
    if not mh.SCORE_HISTORY_PARQUET.exists():
        return {"ready": False, "model_id": model_id or "prod",
                "note": "证据不足:无快照(仅生产 v4 在 regen 时积累)"}
    hist = pd.read_parquet(mh.SCORE_HISTORY_PARQUET)
    fwd = _fwd_returns_for_snapshots(hist)
    hist = hist.assign(fwd=[fwd.get((str(r.date), str(r.code))) for r in hist.itertuples()])
    realized = hist.dropna(subset=["fwd"])
    n_days = int(realized["date"].nunique())
    if n_days < MIN_OOS_DAYS:
        return {"ready": False, "model_id": model_id or "prod", "n_oos_days": n_days,
                "note": f"证据不足:已实现 OOS 仅 {n_days} 天(<{MIN_OOS_DAYS}),随 regen 变厚"}
    m = decile_metrics(realized)
    n_trials = n_trials or _registry_trials()
    ic_dist = m["rank_ic"]
    if mh.VINTAGE_IC_PARQUET.exists():
        v = pd.read_parquet(mh.VINTAGE_IC_PARQUET)
        if len(v):
            ic_dist = [float(x) for x in v["ic"].tolist()]
    return {"ready": True, "model_id": model_id or "prod", "n_oos_days": n_days,
            "sharpe": sharpe(m["long_excess_ret"]),
            "dsr": deflated_sharpe(m["long_excess_ret"], n_trials=n_trials),
            "ic_mean": (float(np.mean(ic_dist)) if ic_dist else None),
            "ic_dist": [round(x, 4) for x in ic_dist], "n_trials": n_trials,
            "note": "快速档:复用已积累真OOS快照(零看未来);PBO跨变体需严格档"}
```

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_validate.py -k quick -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_validate.py
git commit -m "feat(cpcv): quick_validate(读冻结快照→多头超额夏普+DSR+IC分布·诚实ready门)"
```

---

# Phase I — 严格档(retrain-CPCV)

### Task I1:`retrain_core` 按 kind 分派

**Files:** Modify `cpcv.py`; Modify `tests/test_cpcv_validate.py`

- [ ] **Step 1: 写失败测试(树模型分支·小合成面板)**

```python
def test_retrain_core_tree_kind_predicts_test_rows():
    from guanlan_v2.strategy.compute import cpcv
    import numpy as np, pandas as pd
    idx = pd.MultiIndex.from_product(
        [pd.bdate_range("2022-01-03", periods=40), [f"C{i:02d}" for i in range(60)]],
        names=["datetime", "code"])
    rng = np.random.default_rng(0)
    fe = pd.DataFrame({"f1": rng.normal(size=len(idx)), "f2": rng.normal(size=len(idx))}, index=idx)
    label = pd.Series(fe["f1"].values * 0.5 + rng.normal(0, 0.1, len(idx)), index=idx, name="label")
    dts = idx.get_level_values("datetime")
    train_mask = pd.Index(dts).isin(set(dts[dts < pd.Timestamp("2022-02-01")]))
    test_dates = sorted(set(dts[dts >= pd.Timestamp("2022-02-01")]))
    pred = cpcv.retrain_core("lightgbm", {"_fe": fe, "_label": label, "params": {}},
                             train_mask=train_mask, test_dates=test_dates)
    assert isinstance(pred, pd.Series) and len(pred) > 0
    assert set(pred.index.get_level_values("datetime")).issubset(set(test_dates))
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_validate.py -k retrain_core -v` → FAIL

- [ ] **Step 3: 实现** — append to `cpcv.py`:

```python
def retrain_core(kind, panel_ctx, train_mask, test_dates):
    """train_mask 行 fit、test_dates 行 predict → test 行预测分 Series(MultiIndex datetime,code)。
    panel_ctx 含已物化 `_fe`(特征)+ `_label`;v4-lgb 用 LGB_PARAMS,tree 用 workflow._build_model。
    不改 v4.py / model_workflow.py。"""
    fe, label = panel_ctx["_fe"], panel_ctx["_label"]
    Xtr = fe[train_mask].dropna()
    ytr = label.reindex(Xtr.index).dropna()
    Xtr = Xtr.reindex(ytr.index)
    if len(Xtr) < 200:
        return pd.Series(dtype="float64")
    if kind == "v4-lgb":
        import lightgbm as lgb
        from guanlan_v2.strategy.compute.v4 import LGB_PARAMS
        model = lgb.train(LGB_PARAMS, lgb.Dataset(Xtr.values, label=ytr.values), num_boost_round=500)
        predict = model.predict
    else:
        from guanlan_v2.workflow.api import _build_model
        model, _ = _build_model(kind, panel_ctx.get("params", {}))
        model.fit(Xtr.values, ytr.values)
        predict = model.predict
    dts = fe.index.get_level_values("datetime")
    Xte = fe[pd.Index(dts).isin(set(pd.to_datetime(test_dates)))].dropna()
    if Xte.empty:
        return pd.Series(dtype="float64")
    return pd.Series(predict(Xte.values), index=Xte.index, name="pred")
```

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_validate.py -k retrain_core -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_validate.py
git commit -m "feat(cpcv): retrain_core 按kind分派(v4-lgb / tree复用_build_model)掩码fit-predict"
```

---

### Task I2:`strict_validate`(面板物化一次→15路径)+ `__main__`

**Files:** Modify `cpcv.py`; Modify `tests/test_cpcv_validate.py`

- [ ] **Step 1: 写失败测试(slow·真引擎 csi300)**

```python
@pytest.mark.slow
def test_strict_validate_v4_real(monkeypatch, tmp_path):
    from guanlan_v2.strategy.compute import cpcv
    out = cpcv.strict_validate(model_id="prod", n_groups=6, k=2, universe="csi300", start="2024-06-01")
    assert out["ready"] is True and len(out["paths"]) == 15
    assert out["sharpe_dist"]["median"] is not None and "dsr" in out
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_validate.py -k strict -v` → FAIL

- [ ] **Step 3: 实现 `_materialize_panel` + `strict_validate` + `__main__`** — append to `cpcv.py`:

```python
def _materialize_panel(model_id, universe, start, end):
    """按模型 kind 物化一次面板(贵·复用所有路径)→ (kind, ctx{_fe,_label,params}) 或 (kind, None)。
    v4-lgb:复用 v4 的 build_feature_panel+add_ind_turnover+add_breadth_resid(read-only,不改 v4)。
    tree:复用 workflow._materialize_xy(recipe→ModelTrainIn)。"""
    from guanlan_v2.screen.model_registry import variant_meta
    meta = variant_meta(model_id) if (model_id and model_id != "prod") else {"kind": "v4-lgb", "recipe": {}}
    kind = meta.get("kind", "v4-lgb")
    if kind == "v4-lgb":
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        from financial_analyst.data.universe import resolve_universe_codes
        from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        from guanlan_v2.strategy.compute.v4 import (build_feature_panel, add_ind_turnover,
                                                    add_breadth_resid, _select_mf)
        ld = QlibBinaryLoader(DEFAULT_PROVIDER)
        codes = ([str(c) for c in resolve_universe_codes(universe)]
                 if universe not in ("all", "", None) else list_all_instruments(DEFAULT_PROVIDER))
        data = add_breadth_resid(add_ind_turnover(build_feature_panel(ld, codes, start, end),
                                                  ld, codes, start, end))
        if "label" not in data.columns:
            return kind, None
        mf = _select_mf(list(data.columns), None)
        return kind, {"_fe": data[mf], "_label": data["label"], "params": {}}
    recipe = meta.get("recipe") or {}
    if not recipe.get("features"):
        return kind, None
    from guanlan_v2.workflow.api import ModelTrainIn, _materialize_xy
    body = ModelTrainIn(kind=kind, features=list(recipe["features"]), label=recipe.get("label") or "fwd_ret",
                        fwd_days=int(recipe.get("fwd_days") or 5), universe=recipe.get("universe") or universe,
                        start=recipe.get("start") or start, end=end,
                        params=dict(recipe.get("params") or {}), winsorize=True, standardize=True)
    mat = _materialize_xy(body, body.universe, body.features, body.start, body.end)
    if not isinstance(mat, tuple):
        return kind, None
    _p, fe_df, label_s, _n = mat
    return kind, {"_fe": fe_df, "_label": label_s.rename("label"), "params": dict(recipe.get("params") or {})}


def strict_validate(model_id=None, n_groups=6, k=2, purge=5, embargo=5,
                    universe="all", start="2022-01-01", horizon=5, n_trials=None, progress=None):
    """全历史 retrain-CPCV:面板物化一次 → 15 路径各 retrain_core → 多头超额组合 → 分布+DSR。
    retrainable=False / 物化失败 → ready=False(诚实)。"""
    from guanlan_v2.screen.model_registry import variant_meta
    mid = model_id or "prod"
    if mid != "prod" and not variant_meta(mid).get("retrainable", False):
        return {"ready": False, "model_id": mid, "note": "不可重训(无 recipe)→ 只可快速档"}
    from guanlan_v2.strategy.compute.regen import _latest_trade_date
    from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
    end = _latest_trade_date(DEFAULT_PROVIDER)
    kind, ctx = _materialize_panel(mid, universe, start, end)
    if ctx is None:
        return {"ready": False, "model_id": mid, "note": "面板物化失败"}
    fe, label = ctx["_fe"], ctx["_label"]
    dts = pd.DatetimeIndex(sorted(set(fe.index.get_level_values("datetime"))))
    splits = make_splits(dts, n_groups, k, purge, embargo)
    if not splits:
        return {"ready": False, "model_id": mid, "note": "交易日不足以切分"}
    paths, all_excess = [], []
    for i, (train_dates, test_dates) in enumerate(splits):
        if progress:
            progress(i + 1, len(splits))
        train_mask = pd.Index(fe.index.get_level_values("datetime")).isin(set(train_dates))
        pred = retrain_core(kind, ctx, train_mask, test_dates)
        if pred.empty:
            continue
        panel = pd.DataFrame({"date": pred.index.get_level_values("datetime"),
                              "code": pred.index.get_level_values("code"),
                              "lgb_pct": pd.Series(pred.values).rank(pct=True).values,
                              "fwd": label.reindex(pred.index).values})
        rb = sorted(panel["date"].unique())[::horizon]            # 非重叠 5 日换仓
        m = decile_metrics(panel[panel["date"].isin(rb)])
        paths.append({"test_groups": i, "sharpe": sharpe(m["long_excess_ret"]),
                      "ic": m["rank_ic_mean"], "n": m["n"]})
        all_excess += m["long_excess_ret"]
    sps = [p["sharpe"] for p in paths if p["sharpe"] is not None]
    ics = [p["ic"] for p in paths if p["ic"] is not None]
    n_trials = n_trials or _registry_trials()

    def _dist(xs):
        a = np.asarray(xs, dtype="float64")
        return ({"median": float(np.median(a)), "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
                 "p05": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95))} if len(a) else None)
    return {"ready": True, "model_id": mid, "kind": kind, "n_paths": len(paths), "paths": paths,
            "sharpe_dist": _dist(sps), "ic_dist": _dist(ics),
            "dsr": deflated_sharpe(all_excess, n_trials=n_trials,
                                   sharpes_std=(float(np.std(sps, ddof=1)) if len(sps) > 1 else None)),
            "n_trials": n_trials, "asof": str(end),
            "note": "严格档:全历史 retrain-CPCV(purge+embargo);DSR 按 registry 变体数 deflate"}


if __name__ == "__main__":   # python -m guanlan_v2.strategy.compute.cpcv <spec.json>(严格档子进程)
    import json, sys
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[cpcv] strict validate model={spec.get('model_id')} ...", flush=True)
    res = strict_validate(**spec)
    from guanlan_v2.strategy import model_health as mh
    mh.write_cpcv(spec.get("model_id") or "prod", res)
    print(f"[cpcv] done ready={res.get('ready')} dsr={res.get('dsr')}", flush=True)
    sys.exit(0 if res.get("ready") else 1)
```

- [ ] **Step 4: 跑 slow 真验证** — `python -m pytest tests/test_cpcv_validate.py -k strict -v -m slow`
Expected: PASS(~分钟级)。若索引/列名不符 → 读 v4.py `build_feature_panel` 确认返回含 `label` 列 + MultiIndex(datetime,code),据实修 `_materialize_panel`。

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_cpcv_validate.py
git commit -m "feat(cpcv): strict_validate(面板物化一次→15路径retrain-CPCV→分布+DSR)+ __main__ 子进程"
```

---

# Phase J — model_health 存/读

### Task J1:`write_cpcv` / `load_cpcv_summary`

**Files:** Modify `guanlan_v2/strategy/model_health.py`; Modify `tests/test_cpcv_validate.py`

- [ ] **Step 1: 写失败测试**

```python
def test_write_load_cpcv(tmp_path, monkeypatch):
    from guanlan_v2.strategy import model_health as mh
    monkeypatch.setattr(mh, "CPCV_DIR", tmp_path, raising=False)
    res = {"ready": True, "model_id": "prod", "dsr": 0.7,
           "sharpe_dist": {"median": 1.2, "std": 0.3, "p05": 0.5, "p95": 1.9}, "n_trials": 10}
    mh.write_cpcv("prod", res)
    s = mh.load_cpcv_summary("prod")
    assert s["ready"] is True and s["dsr"] == 0.7 and s["sharpe_dist"]["median"] == 1.2
    assert mh.load_cpcv_summary("nope") is None
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_validate.py -k write_load_cpcv -v` → FAIL

- [ ] **Step 3: 实现**(在 `model_health.py` 加,沿用其 `os`/`ARTIFACTS_DIR`)

```python
CPCV_DIR = ARTIFACTS_DIR     # cpcv 摘要与既有产物同目录(独立文件名,不碰三产物)


def _cpcv_path(model_id: str):
    return CPCV_DIR / f"model_cpcv_{model_id}.json"


def write_cpcv(model_id: str, result: Dict[str, Any]) -> None:
    """cpcv 结果落 model_cpcv_<id>.json(原子写)。只新写,绝不改三产物。"""
    import json
    p = _cpcv_path(model_id); tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False))
    os.replace(tmp, str(p))


def load_cpcv_summary(model_id: str) -> Optional[Dict[str, Any]]:
    """读 cpcv 摘要;缺/坏 → None(诚实)。"""
    import json
    p = _cpcv_path(model_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
```

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_validate.py -k write_load_cpcv -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/model_health.py tests/test_cpcv_validate.py
git commit -m "feat(model_health): write_cpcv/load_cpcv_summary(独立 model_cpcv_<id>.json·不碰三产物)"
```

---

# Phase K — 端点 + 子进程

### Task K1:`POST /model/validate` + `GET /model/validate/status`

**Files:** Modify `guanlan_v2/screen/api.py`; Modify `tests/test_cpcv_validate.py`

- [ ] **Step 1: 写失败测试**

```python
def test_validate_endpoint_quick(monkeypatch):
    monkeypatch.setattr("guanlan_v2.strategy.compute.cpcv.quick_validate",
                        lambda model_id=None: {"ready": True, "dsr": 0.6, "sharpe": 1.1, "model_id": model_id or "prod"})
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    j = TestClient(app).post("/screen/model/validate", json={"id": "prod", "tier": "quick"}).json()
    assert j["ok"] is True and j["result"]["ready"] is True and j["result"]["dsr"] == 0.6


def test_validate_endpoint_strict_starts(monkeypatch):
    import guanlan_v2.screen.api as api
    monkeypatch.setattr(api, "_run_validate_subprocess", lambda spec: None)
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    j = c.post("/screen/model/validate", json={"id": "prod", "tier": "strict"}).json()
    assert j["ok"] is True and j["started"] is True
    s = c.get("/screen/model/validate/status").json()
    assert s["ok"] is True and "state" in s
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_cpcv_validate.py -k endpoint -v` → FAIL(404)

- [ ] **Step 3: 实现**(`screen/api.py` 模块级加状态机 + helper,镜像 `_MODEL_STATE`/`_run_model_train_subprocess`;router 内加两端点)

模块级:

```python
_VALIDATE_LOCK = _threading.Lock()
_VALIDATE_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 15, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "model_id": None, "lines": []}


def _validate_public_state() -> Dict[str, Any]:
    import time as _t
    with _VALIDATE_LOCK:
        s = dict(_VALIDATE_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_validate_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"cpcv_{spec['model_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.Popen([_sys.executable, "-m", "guanlan_v2.strategy.compute.cpcv", str(sf)],
            cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line:
                with _VALIDATE_LOCK:
                    _VALIDATE_STATE["lines"].append(line)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _VALIDATE_LOCK:
            _VALIDATE_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 15})
```

router 内(`/models` 附近):

```python
    @router.post("/model/validate")
    def screen_model_validate(body: dict = Body(default={})):
        import time as _t
        from guanlan_v2.strategy.compute import cpcv
        mid = str(body.get("id") or "prod"); tier = str(body.get("tier") or "quick")
        if tier == "quick":
            return JSONResponse({"ok": True, "result": cpcv.quick_validate(model_id=mid)})
        with _VALIDATE_LOCK:
            if _VALIDATE_STATE["running"]:
                return JSONResponse({"ok": False, "reason": "已有验证在跑", "state": _validate_public_state()})
            _VALIDATE_STATE.update({"running": True, "phase": "starting", "label": "启动严格验证…",
                "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                "model_id": mid, "lines": []})
        spec = {"model_id": mid, "n_groups": int(body.get("n_groups") or 6), "k": int(body.get("k") or 2),
                "purge": int(body.get("purge") or 5), "embargo": int(body.get("embargo") or 5)}
        _threading.Thread(target=lambda: _run_validate_subprocess(spec), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "model_id": mid, "state": _validate_public_state()})

    @router.get("/model/validate/status")
    def screen_model_validate_status():
        from guanlan_v2.strategy import model_health as mh
        st = _validate_public_state()
        if not st["running"] and st.get("model_id"):
            st["result"] = mh.load_cpcv_summary(st["model_id"])
        return JSONResponse({"ok": True, "state": st})
```

(`_threading`/`Body`/`JSONResponse`/`Dict`/`Any` 在 screen/api.py 已 import。`/screen/model/validate` 因 screen router prefix=/screen。)

- [ ] **Step 4: 跑确认通过** — `python -m pytest tests/test_cpcv_validate.py -k endpoint -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/api.py tests/test_cpcv_validate.py
git commit -m "feat(screen): POST /model/validate(quick内联/strict异步)+ /validate/status"
```

---

# Phase L — 前端

> 只填充现有 UI;无单测,改后 bump `?v` + 浏览器验真(M1)。读 `ui/screen/screen-app.jsx` 找 ModelWorkshop 抽屉变体行 + TopBar 体检卡(`model_health` 渲染处)+ 真实 `_get`/`_post`/`flash`/badge class。

### Task L1:模型工坊「快验/严格验证」按钮 + DSR 徽章

**Files:** Modify `ui/screen/screen-app.jsx`

- [ ] **Step 1: 实现**(adapt 真实 helper/class)

```jsx
async function runValidate(id, tier) {
  const r = await _post('/screen/model/validate', { id, tier });
  if (tier === 'quick') {
    if (r && r.ok) flash('快验', () => `DSR ${r.result.dsr ?? '—'} · 夏普 ${r.result.sharpe ?? '—'}${r.result.ready ? '' : '(证据不足)'}`);
    return;
  }
  if (!r || !r.ok) { flash('严格验证', () => (r && r.reason) || '启动失败'); return; }
  flash('严格验证', () => '已起(~分钟级),完成回灌');
  const t = setInterval(async () => {
    const s = (await _get('/screen/model/validate/status')).state || {};
    if (!s.running && s.phase === 'done') {
      clearInterval(t);
      flash('严格验证', () => s.ok ? `完成 DSR ${s.result?.dsr ?? '—'} · 夏普中位 ${s.result?.sharpe_dist?.median ?? '—'}` : ('失败:' + (s.error || '')));
    }
  }, 4000);
}
// 变体行内:<button onClick={()=>runValidate(m.id,'quick')}>快验</button>
//           <button onClick={()=>runValidate(m.id,'strict')}>严格验证</button>
// TopBar 体检卡:若 /screen/run 响应附带 prod 的 load_cpcv_summary(可选)→ 显 "CPCV 夏普中位 X·DSR Y";缺则不显(诚实)。
```

- [ ] **Step 2: 提交**

```bash
git add ui/screen/screen-app.jsx
git commit -m "feat(workshop-ui): 模型工坊快验/严格验证按钮+轮询+DSR提示"
```

---

# Phase M — 验证

### Task M1:全量回归 + 真机 e2e

- [ ] **Step 1** 全量非慢测:`python -m pytest -q -m "not slow" 2>&1 | tail -8` → 0 failed。
- [ ] **Step 2** slow 真验证:`python -m pytest tests/test_cpcv_validate.py -k strict -v -m slow` → PASS。
- [ ] **Step 3** 真机 e2e(主树 9999,与并发会话协调):bump `screen-app.jsx ?v`;杀 9999 待 watchdog;`POST /screen/model/validate {id:prod,tier:quick}` 秒级回 DSR;`{tier:strict}` 起异步→`/validate/status` 轮询到 done→回灌摘要;浏览器模型工坊点两按钮 0 报错。
- [ ] **Step 4** 提交(worktree 内,只 ① 文件):`git add -A && git commit -m "test(cpcv): 全量回归绿 + 真机 e2e 快/严两档"`

---

## 自审清单

1. **spec 覆盖**:快/严两档(H/I)、DSR(G3)、CPCV+purge/embargo(G1)、组合指标(G2)、存读(J)、端点(K)、前端(L)、验证(M) —— 对齐 spec §12(验证任意 registry 模型 + retrain_core 按 kind)。✔
2. **占位符**:无 TBD;DSR/CPCV 给真实公式与代码。
3. **类型一致**:`make_splits`/`decile_metrics`/`sharpe`/`deflated_sharpe`/`quick_validate`/`retrain_core`/`_materialize_panel`/`strict_validate`/`write_cpcv`/`load_cpcv_summary`/`/model/validate` 跨任务一致;ctx 键 `_fe`/`_label`/`params` 全程统一。
4. **红线**:不改 v4.py(只 import primitive)、产物只读(独立 model_cpcv_<id>.json)、PIT(快照冻结 + purge/embargo 单测)、不碰 /screen 选股、诚实 ready/None。✔

> **挂账(① 之后)**:**跨变体 PBO(CSCV)**——需 ≥2 变体各缓存严格档逐路径收益矩阵,再算 IS-best 的 OOS 中位数以下概率;本期 strict 给单模型 15 路径分布 + DSR(诚实,不冒充 PBO)。让 regen 给选中变体积累逐日快照(快速档惠及变体)。前端体检卡 CPCV 迷你直方图。
