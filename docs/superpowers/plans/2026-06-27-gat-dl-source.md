# GAT 深度学习源 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 GAT(图注意力)作为 DL 集成层第 3 个源接入 v4——离线 GPU 训练产 `var/dl_pred_gat.parquet`,经 ① CPCV 闸验证后插拔式混进 v4 score。

**Architecture:** 纯函数数据准备(`gat_io`,无 torch)+ 纯 PyTorch 掩码图注意力模型(`gat_model`,CPU 可单测)+ GPU 编排脚本(`gat_predict.py`)+ ① 闸的轻量入口(`cpcv.validate_dl_source`,复用既有统计原语)+ 注册一行(`dl_ensemble.default_dl_sources`)。集成点(v4/regen/apply_dl_ensemble/徽章)全已就位,零改。

**Tech Stack:** Python, numpy/pandas, PyTorch(主 env 2.10 CPU 单测;conda stocks GPU 跑生产), pytest, FastAPI(不涉改).

## Global Constraints

每个任务都隐含遵守以下(逐字取自 spec):

- **工作树**:全部改动在 worktree `G:\guanlan-v2\.claude\worktrees\gat-dl-source`,分支 `worktree-gat-dl-source`。**Bash cwd 可能回落主树 `G:\guanlan-v2`**——所有 git/pytest/文件操作必须 pin 到 worktree(绝对路径 / `git -C <WT>` / 从 WT 跑 pytest);**提交前必 `git -C <WT> branch --show-current` 确认是 `worktree-gat-dl-source`**;绝不碰主树或 worktree 外文件。
- **PIT 无前视**:`gat_io` 一律 `close_panel.loc[:date]` 截断后算;`forward_label` 未来收益只在训练日(已实现)取;`train_cutoff` 诚实落盘,`lookahead` 恒 False。
- **诚实缺席**:缺文件/不足/无当日预测 → `ready=False` / 源退出 / 退纯 LGB,**绝不编造数字**。
- **`v4.py` / `v4_fincast.py` 零改**:只 import 其 primitive;不改 `build_v4`/`retrain_core`/`strict_validate`。
- **LGB 恒主导 ≥ 0.5**:沿用 `MAX_TOTAL_DL_W=0.5`,不改。
- **不碰 Spec3(LSTM)**:不改 `default_dl_sources()` 里的 `lstm` 行、不改任何 LSTM 代码/产物。
- **`gat_io.py` 必须 torch-free**(只 numpy/pandas):主 env 与 conda stocks 都能 import。
- **parquet 契约**:`var/dl_pred_gat.parquet` 扁平列 `eval_date`(datetime64) / `instrument`(str) / `pred_ret_5d`(float32) / `train_cutoff`(datetime64);写盘统一用 `fincast_io.write_pred_rolling`。
- **离线推理**:GPU 训练/推理只在 `scripts/gat_predict.py`(conda stocks);9999 请求路径绝不跑模型。
- 每个 commit message 末行:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 跑测命令:从 worktree 根跑 `python -m pytest <file> -q`(repo 的 `tests/conftest.py` 已 prepend engine 到 sys.path)。

---

### Task 1: `gat_io.py` — PIT 数据准备纯函数

**Files:**
- Create: `guanlan_v2/strategy/compute/gat_io.py`
- Test: `tests/test_gat_io.py`

**Interfaces:**
- Consumes: 无(纯 numpy/pandas)。
- Produces:
  - `DEFAULT_GAT_FACTORS: tuple[str,...]` = `("mom_5","mom_20","mom_60","rev_1","vol_20","ma_gap","turn","amihud_20")`
  - `compute_node_features(close_panel: pd.DataFrame, volume_panel: Optional[pd.DataFrame], date, *, factors=DEFAULT_GAT_FACTORS) -> Tuple[List[str], np.ndarray]`(返回 `(codes, (N,F) float32)`,逐因子横截面 z)
  - `build_corr_graph(close_panel, date, codes, *, window=60, topk=20) -> np.ndarray`(返回 `(N,N) float32` 0/1 邻接 + 自环,对称)
  - `forward_label(close_panel, date, codes, *, horizon=5) -> np.ndarray`(返回 `(N,) float32`,未实现置 nan)
  - `rebalance_dates(panel_index, *, horizon=5, start=None) -> List[pd.Timestamp]`

- [ ] **Step 1: 写失败测试** `tests/test_gat_io.py`

```python
# tests/test_gat_io.py
# GAT 数据准备纯函数门禁:横截面 z / PIT 截断 / 相关图对称自环 / 前向标签 PIT。
import numpy as np
import pandas as pd
from guanlan_v2.strategy.compute import gat_io


def _close_panel(n_days=120, codes=("A", "B", "C", "D", "E"), seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    data = {c: 10.0 * np.cumprod(1 + rng.normal(0.001, 0.02, n_days)) for c in codes}
    return pd.DataFrame(data, index=idx)


def test_node_features_cross_sectional_z_and_shape():
    cp = _close_panel()
    vp = cp * 1000.0
    codes, X = gat_io.compute_node_features(cp, vp, cp.index[-1])
    assert X.shape == (len(codes), len(gat_io.DEFAULT_GAT_FACTORS))
    assert X.dtype == np.float32
    # 每列横截面近似零均值(z-score)
    assert np.allclose(X.mean(axis=0), 0.0, atol=1e-4)


def test_node_features_pit_no_future():
    cp = _close_panel()
    d = cp.index[60]
    codes1, X1 = gat_io.compute_node_features(cp, None, d)
    cp2 = cp.copy()
    cp2.iloc[70:] = cp2.iloc[70:] * 5.0   # 篡改 d 之后的未来
    codes2, X2 = gat_io.compute_node_features(cp2, None, d)
    assert codes1 == codes2
    assert np.allclose(X1, X2)             # 未来变化不影响 d 的特征(PIT)


def test_corr_graph_symmetric_selfloop_degree():
    cp = _close_panel()
    codes, _ = gat_io.compute_node_features(cp, None, cp.index[-1])
    A = gat_io.build_corr_graph(cp, cp.index[-1], codes, window=60, topk=2)
    n = len(codes)
    assert A.shape == (n, n)
    assert np.allclose(A, A.T)                       # 对称
    assert np.allclose(np.diag(A), 1.0)              # 自环
    assert ((A == 0) | (A == 1)).all()               # 0/1


def test_corr_graph_short_window_returns_identity():
    cp = _close_panel(n_days=120)
    codes, _ = gat_io.compute_node_features(cp, None, cp.index[-1])
    A = gat_io.build_corr_graph(cp, cp.index[3], codes, window=60, topk=2)  # 仅 ~4 日历史 < 5
    assert np.allclose(A, np.eye(len(codes)))        # 诚实退单位阵


def test_forward_label_value_and_pit_tail():
    cp = _close_panel()
    codes = list(cp.columns)
    d = cp.index[10]
    y = gat_io.forward_label(cp, d, codes, horizon=5)
    expected = cp.loc[cp.index[15], codes].values / cp.loc[d, codes].values - 1.0
    assert np.allclose(y, expected, atol=1e-6)
    # 末日附近无未来标签 → 全 nan
    y_tail = gat_io.forward_label(cp, cp.index[-1], codes, horizon=5)
    assert np.isnan(y_tail).all()


def test_rebalance_dates_nonoverlap_and_realized():
    cp = _close_panel(n_days=100)
    rd = gat_io.rebalance_dates(cp.index, horizon=5, start=None)
    assert len(rd) > 0
    # 非重叠 5 日步长
    pos = [cp.index.get_loc(d) for d in rd]
    assert all(b - a == 5 for a, b in zip(pos, pos[1:]))
    # 每个换仓日标签已实现(d + horizon ≤ 末日)
    assert all(cp.index.get_loc(d) + 5 <= len(cp.index) - 1 for d in rd)
```

- [ ] **Step 2: 跑测验证失败**

Run: `python -m pytest tests/test_gat_io.py -q`
Expected: FAIL(`ModuleNotFoundError: gat_io` / 函数未定义)

- [ ] **Step 3: 实现 `guanlan_v2/strategy/compute/gat_io.py`**

```python
# -*- coding: utf-8 -*-
"""GAT 源数据准备纯函数:close/volume 面板 → PIT 节点特征 / 收益相关图 / 前向标签 / 换仓日。
无 torch、无引擎依赖(只 numpy/pandas),guanlan 主 env 与 conda stocks 都可 import(同 fincast_io 约束)。
**PIT 命门**:一律对面板 `.loc[:date]` 截断后再算,绝不看未来;标签未来收益只在训练日(已实现)取用。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_GAT_FACTORS = ("mom_5", "mom_20", "mom_60", "rev_1", "vol_20", "ma_gap", "turn", "amihud_20")


def _zscore_cols(df: pd.DataFrame) -> pd.DataFrame:
    """逐列横截面 z-score(std=0 或全 NaN → 0)。"""
    mu = df.mean(axis=0)
    sd = df.std(axis=0, ddof=0).replace(0.0, np.nan)
    return ((df - mu) / sd).fillna(0.0)


def compute_node_features(close_panel: pd.DataFrame, volume_panel: Optional[pd.DataFrame],
                          date, *, factors=DEFAULT_GAT_FACTORS) -> Tuple[List[str], np.ndarray]:
    """date 横截面 PIT 价量因子快照(只用 ≤date 数据)→ (codes, (N,F) float32),逐因子横截面 z。
    入选 = close 末值非空;volume 缺/空 → turn/amihud 置 0。"""
    cp = close_panel.loc[:pd.Timestamp(date)]
    ret1 = cp.pct_change()
    last = cp.iloc[-1]
    feat = {
        "mom_5": last / cp.shift(5).iloc[-1] - 1.0,
        "mom_20": last / cp.shift(20).iloc[-1] - 1.0,
        "mom_60": last / cp.shift(60).iloc[-1] - 1.0,
        "rev_1": -(last / cp.shift(1).iloc[-1] - 1.0),
        "vol_20": ret1.tail(20).std(axis=0, ddof=0),
        "ma_gap": last / cp.tail(20).mean(axis=0) - 1.0,
    }
    has_vol = volume_panel is not None and not volume_panel.empty
    if has_vol:
        vp = volume_panel.loc[:pd.Timestamp(date)].reindex(columns=cp.columns)
        vma20 = vp.tail(20).mean(axis=0).replace(0.0, np.nan)
        feat["turn"] = vp.iloc[-1] / vma20
        feat["amihud_20"] = (ret1.abs() / (cp * vp).replace(0.0, np.nan)).tail(20).mean(axis=0)
    else:
        feat["turn"] = pd.Series(0.0, index=cp.columns)
        feat["amihud_20"] = pd.Series(0.0, index=cp.columns)
    fdf = pd.DataFrame({k: feat[k] for k in factors})
    fdf = fdf.loc[cp.columns[last.notna().values]]               # 入选:close 末值非空
    fdf = fdf.replace([np.inf, -np.inf], np.nan)
    z = _zscore_cols(fdf)
    return list(fdf.index), z.to_numpy(dtype=np.float32)


def build_corr_graph(close_panel: pd.DataFrame, date, codes, *, window: int = 60, topk: int = 20) -> np.ndarray:
    """≤date 末 window 日日收益 → codes 两两 Pearson 相关 → 每节点 topk 最相关邻居(|corr|, 排自身)
    → 对称 0/1 邻接 + 自环。窗口不足(<5 日)/全空 → 单位阵(只自注意,诚实退化)。返回 (N,N) float32。"""
    n = len(codes)
    eye = np.eye(n, dtype=np.float32)
    cp = close_panel.loc[:pd.Timestamp(date), list(codes)]
    rets = cp.pct_change().tail(window)
    if len(rets) < 5:
        return eye
    C = rets.corr().to_numpy()
    if not np.isfinite(C).any():
        return eye
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 0.0)
    k = min(topk, n - 1)
    if k <= 0:
        return eye
    A = np.zeros((n, n), dtype=np.float32)
    order = np.argsort(-np.abs(C), axis=1)[:, :k]
    A[np.repeat(np.arange(n), k), order.reshape(-1)] = 1.0
    A = np.maximum(A, A.T)               # 对称化
    np.fill_diagonal(A, 1.0)             # 自环
    return A


def forward_label(close_panel: pd.DataFrame, date, codes, *, horizon: int = 5) -> np.ndarray:
    """codes 在 date 起未来 horizon 交易日收益 close[t+h]/close[t]-1(仅训练日可用:t+h ≤ 面板末日)。
    缺失/无未来 置 nan。返回 (N,) float32。"""
    idx = close_panel.index
    ts = pd.Timestamp(date)
    out = np.full(len(codes), np.nan, dtype=np.float32)
    pos = idx.searchsorted(ts)
    if pos >= len(idx) or idx[pos] != ts or pos + horizon >= len(idx):
        return out
    c0 = close_panel.loc[ts, list(codes)].to_numpy(dtype="float64")
    c1 = close_panel.loc[idx[pos + horizon], list(codes)].to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = c1 / c0 - 1.0
    r[~np.isfinite(r)] = np.nan
    out[:] = r.astype(np.float32)
    return out


def rebalance_dates(panel_index, *, horizon: int = 5, start=None) -> List[pd.Timestamp]:
    """从 start(缺省=首日)到 末日-horizon 的非重叠 horizon 日换仓训练日(标签已实现)。"""
    idx = pd.DatetimeIndex(panel_index)
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if len(idx) <= horizon:
        return []
    return list(idx[:-horizon][::horizon])
```

- [ ] **Step 4: 跑测验证通过**

Run: `python -m pytest tests/test_gat_io.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/gat_io.py tests/test_gat_io.py
git commit -m "feat(gat-dl): gat_io PIT 数据准备纯函数(节点特征/相关图/标签/换仓日)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `gat_model.py` — 纯 PyTorch 掩码图注意力 + 训练/推理

**Files:**
- Create: `guanlan_v2/strategy/compute/gat_model.py`
- Test: `tests/test_gat_model.py`

**Interfaces:**
- Consumes: 无(只 torch/numpy)。
- Produces:
  - `class _GATLayer(nn.Module)`:`forward(h: Tensor(N,in), mask: Tensor(N,N)) -> Tensor(N,out)`
  - `class GAT(nn.Module)`:`__init__(in_dim, hidden=32)`;`forward(X: Tensor(N,F), A: Tensor(N,N)) -> Tensor(N,)`
  - `train_gat(X_list, A_list, y_list, *, device="cpu", epochs=60, lr=1e-3, hidden=32, seed=0, return_losses=False) -> GAT | (GAT, list[float])`(`X_list[i]` np `(N,F)`,`A_list[i]` np `(N,N)`,`y_list[i]` np `(N,)` 含 nan)
  - `predict_gat(model, X: np.ndarray, A: np.ndarray, *, device="cpu") -> np.ndarray`(返回 `(N,)`)

- [ ] **Step 1: 写失败测试** `tests/test_gat_model.py`

```python
# tests/test_gat_model.py
# 纯 torch 掩码图注意力门禁:形状 + 掩码生效(图注意力命门) + 训练有效。
import numpy as np
import torch

from guanlan_v2.strategy.compute.gat_model import _GATLayer, GAT, train_gat, predict_gat


def test_gat_forward_shape_finite():
    torch.manual_seed(0)
    N, F = 8, 5
    X = torch.randn(N, F)
    A = torch.eye(N)
    A[0, 1] = A[1, 0] = 1.0
    out = GAT(F, hidden=16)(X, A)
    assert out.shape == (N,)
    assert torch.isfinite(out).all()


def test_gat_layer_masks_non_neighbors():
    """单层掩码命门:改非邻居节点输入,目标节点输出不变(注意力只看邻居)。"""
    torch.manual_seed(0)
    layer = _GATLayer(3, 4)
    N = 5
    X = torch.randn(N, 3)
    mask = torch.eye(N)
    mask[0, 1] = mask[1, 0] = 1.0           # 0<->1 互为邻居;node3 是 node0 的非邻居
    out1 = layer(X, mask)
    X2 = X.clone()
    X2[3] = torch.randn(3)                   # 改 node3(node0 的非邻居)
    out2 = layer(X2, mask)
    assert torch.allclose(out1[0], out2[0], atol=1e-6)     # node0 不受非邻居影响
    assert not torch.allclose(out1[3], out2[3], atol=1e-6) # node3 自身变了


def test_train_gat_loss_decreases_and_predict():
    rng = np.random.default_rng(0)
    X_list, A_list, y_list = [], [], []
    N, F = 40, 4
    w = rng.normal(size=F)
    for _ in range(8):                       # 8 个"日"图,y 为 X 的线性可学函数
        X = rng.normal(size=(N, F)).astype(np.float32)
        A = np.eye(N, dtype=np.float32)
        y = (X @ w + rng.normal(0, 0.1, N)).astype(np.float32)
        X_list.append(X); A_list.append(A); y_list.append(y)
    model, losses = train_gat(X_list, A_list, y_list, device="cpu", epochs=40, return_losses=True)
    assert losses[-1] < losses[0]            # 训练有效
    p = predict_gat(model, X_list[0], A_list[0], device="cpu")
    assert p.shape == (N,) and np.isfinite(p).all()
```

- [ ] **Step 2: 跑测验证失败**

Run: `python -m pytest tests/test_gat_model.py -q`
Expected: FAIL(`ModuleNotFoundError: gat_model`)

- [ ] **Step 3: 实现 `guanlan_v2/strategy/compute/gat_model.py`**

```python
# -*- coding: utf-8 -*-
"""纯 PyTorch 掩码图注意力(GAT)模型 + 训练/推理。主 env(torch CPU)可单测;GPU 脚本 import 之。
关系维度:节点=个股,边=收益相关图(gat_io.build_corr_graph 的 0/1 邻接 + 自环)。无引擎依赖。"""
from __future__ import annotations

from typing import List

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


def _zscore_1d(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype="float64")
    sd = a.std()
    return (a - a.mean()) / sd if sd > 0 else (a - a.mean())


class _GATLayer(nn.Module):
    """单头图注意力:e_ij = LeakyReLU(a_src·Wh_i + a_dst·Wh_j),非邻居 -inf 掩码 + 行 softmax + 邻居加权。"""

    def __init__(self, in_dim: int, out_dim: int, *, alpha: float = 0.2):
        super().__init__()
        self.W = nn.Linear(in_dim, out_dim, bias=False)
        self.a_src = nn.Linear(out_dim, 1, bias=False)
        self.a_dst = nn.Linear(out_dim, 1, bias=False)
        self.leaky = nn.LeakyReLU(alpha)

    def forward(self, h: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        Wh = self.W(h)                                   # (N, out)
        e = self.a_src(Wh) + self.a_dst(Wh).transpose(0, 1)   # (N, N): e_ij = a_src(Wh_i)+a_dst(Wh_j)
        e = self.leaky(e)
        e = e.masked_fill(mask <= 0, torch.finfo(e.dtype).min)  # 非邻居 -inf
        att = torch.softmax(e, dim=1)                    # 每节点对其邻居归一
        return att @ Wh                                  # (N, out)


class GAT(nn.Module):
    def __init__(self, in_dim: int, hidden: int = 32):
        super().__init__()
        self.l1 = _GATLayer(in_dim, hidden)
        self.l2 = _GATLayer(hidden, hidden)
        self.head = nn.Linear(hidden, 1)

    def forward(self, X: torch.Tensor, A: torch.Tensor) -> torch.Tensor:
        h = F.elu(self.l1(X, A))
        h = F.elu(self.l2(h, A))
        return self.head(h).squeeze(-1)                  # (N,)


def train_gat(X_list: List[np.ndarray], A_list: List[np.ndarray], y_list: List[np.ndarray], *,
              device: str = "cpu", epochs: int = 60, lr: float = 1e-3, hidden: int = 32,
              seed: int = 0, return_losses: bool = False):
    """每日一个图 (X,A,y);仅 finite-label 节点入损失(横截面 z 标签 MSE);Adam。返回训练好的 GAT。"""
    torch.manual_seed(seed)
    graphs = []
    for X, A, y in zip(X_list, A_list, y_list):
        m = np.isfinite(y)
        if int(m.sum()) < 20:
            continue
        graphs.append((
            torch.tensor(X, dtype=torch.float32, device=device),
            torch.tensor(A, dtype=torch.float32, device=device),
            torch.tensor(m, device=device),
            torch.tensor(_zscore_1d(y[m]), dtype=torch.float32, device=device),
        ))
    if not graphs:
        raise ValueError("无可训练图(每日 finite 标签 < 20)")
    model = GAT(X_list[0].shape[1], hidden=hidden).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    losses: List[float] = []
    for _ in range(epochs):
        tot, nb = 0.0, 0
        for Xt, At, m, yz in graphs:
            opt.zero_grad()
            pred = model(Xt, At)[m]
            loss = F.mse_loss(pred, yz)
            loss.backward()
            opt.step()
            tot += float(loss); nb += 1
        losses.append(tot / max(1, nb))
    return (model, losses) if return_losses else model


def predict_gat(model: GAT, X: np.ndarray, A: np.ndarray, *, device: str = "cpu") -> np.ndarray:
    model.eval()
    with torch.no_grad():
        Xt = torch.tensor(X, dtype=torch.float32, device=device)
        At = torch.tensor(A, dtype=torch.float32, device=device)
        return model(Xt, At).detach().cpu().numpy()
```

- [ ] **Step 4: 跑测验证通过**

Run: `python -m pytest tests/test_gat_model.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/gat_model.py tests/test_gat_model.py
git commit -m "feat(gat-dl): gat_model 纯PyTorch掩码图注意力 + train/predict(CPU可单测掩码命门)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `cpcv.validate_dl_source` — ① 闸的 DL 源验证入口

**Files:**
- Modify: `guanlan_v2/strategy/compute/cpcv.py`(纯新增,不动既有函数)
- Test: `tests/test_dl_source_validate.py`

**Interfaces:**
- Consumes(① 已有,本任务复用、不改):`_fwd_returns_for_snapshots(hist, horizon=5)`、`decile_metrics(panel)`、`sharpe(returns)`、`deflated_sharpe(returns, n_trials)`、`MIN_OOS_DAYS`。
- Produces:
  - `DL_GATE_DSR = 0.5`、`DL_SOURCE_TRIALS = 8`
  - `validate_dl_source(path: str, score_col: str = "pred_ret_5d", n_trials: int = DL_SOURCE_TRIALS) -> dict`(键:`ready, path, n_oos_days?, sharpe?, dsr?, ic_mean?, ic_dist?, n_trials?, passes_gate?, note`)

- [ ] **Step 1: 写失败测试** `tests/test_dl_source_validate.py`

```python
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
    assert out["passes_gate"] is False


def test_validate_dl_source_missing_file():
    out = cpcv.validate_dl_source("___nope___.parquet")
    assert out["ready"] is False and "不存在" in out["note"]


def test_validate_dl_source_insufficient_days(tmp_path, monkeypatch):
    path = _write_src(tmp_path, n_days=5)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots", lambda hist, horizon=5: {})
    out = cpcv.validate_dl_source(path)
    assert out["ready"] is False and "证据不足" in out["note"]
```

- [ ] **Step 2: 跑测验证失败**

Run: `python -m pytest tests/test_dl_source_validate.py -q`
Expected: FAIL(`AttributeError: module ... has no attribute 'validate_dl_source'`)

- [ ] **Step 3: 在 `cpcv.py` 末尾(`if __name__` 之前)新增**(不动任何既有函数)

```python
DL_GATE_DSR = 0.5        # 激活建议门槛:DSR ≥ 0.5(真夏普>噪声基准概率过半)
DL_SOURCE_TRIALS = 8     # DSR deflate 的试验数(DL 架构候选 fincast/lstm/gat + 调参,保守取 8)


def validate_dl_source(path, score_col: str = "pred_ret_5d", n_trials: int = DL_SOURCE_TRIALS) -> Dict[str, Any]:
    """读 DL 源预测表 → 用真已实现 fwd5d(PIT)算 多头超额夏普 + DSR + RankIC(复用 ① 原语,零新算法)。
    缺文件/缺列/不足 → ready=False(诚实)。passes_gate = DSR ≥ DL_GATE_DSR(建议性,激活仍人工)。"""
    import os
    if not path or not os.path.exists(path):
        return {"ready": False, "path": path, "note": "证据不足:预测文件不存在"}
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "path": path, "note": f"读取失败({type(e).__name__})"}
    need = {"eval_date", "instrument", score_col}
    if not need.issubset(df.columns):
        try:
            df = df.reset_index()
        except Exception:  # noqa: BLE001
            pass
    if not need.issubset(df.columns):
        return {"ready": False, "path": path, "note": f"缺 {need} 列"}
    hist = pd.DataFrame({"date": pd.to_datetime(df["eval_date"]).dt.strftime("%Y-%m-%d"),
                         "code": df["instrument"].astype(str),
                         "score": df[score_col].astype(float)})
    fwd = _fwd_returns_for_snapshots(hist)   # 真函数只读 date/code 列(score 列无害);传完整 hist 便于桩测复用 score
    hist = hist.assign(fwd=[fwd.get((r.date, r.code)) for r in hist.itertuples()])
    realized = hist.dropna(subset=["fwd"]).copy()
    n_days = int(realized["date"].nunique())
    if n_days < MIN_OOS_DAYS:
        return {"ready": False, "path": path, "n_oos_days": n_days,
                "note": f"证据不足:已实现 OOS 仅 {n_days} 天(<{MIN_OOS_DAYS})"}
    realized["lgb_pct"] = realized.groupby("date")["score"].rank(pct=True)   # 把"被排名分"塞进 lgb_pct 列复用 decile_metrics
    m = decile_metrics(realized[["date", "code", "lgb_pct", "fwd"]])
    dsr = deflated_sharpe(m["long_excess_ret"], n_trials=n_trials)
    ic_dist = m["rank_ic"]
    return {"ready": True, "path": path, "n_oos_days": n_days,
            "sharpe": sharpe(m["long_excess_ret"]), "dsr": dsr,
            "ic_mean": (float(np.mean(ic_dist)) if ic_dist else None),
            "ic_dist": [round(x, 4) for x in ic_dist], "n_trials": n_trials,
            "passes_gate": bool(dsr is not None and dsr >= DL_GATE_DSR),
            "note": f"DL 源验证(PIT 真已实现 fwd5d);DSR≥{DL_GATE_DSR} 建议激活,激活仍人工"}
```

> **注意**:`_fwd_returns_for_snapshots` 入参 `hist` 只用 `date`/`code` 列(① 实现里 `hist["code"]`/`hist["date"]`)。本函数传完整 `hist`(含 `score` 列,真函数忽略之),`date` 为 `YYYY-MM-DD` 字符串(同 quick_validate 口径),返回键 `(date_str, code_str)`。

- [ ] **Step 4: 跑测验证通过**

Run: `python -m pytest tests/test_dl_source_validate.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: 跑既有 cpcv 测试确认未破坏**

Run: `python -m pytest tests/test_cpcv_engine.py tests/test_cpcv_validate.py -q -m "not slow"`
Expected: PASS(既有全绿;`slow` 跳过)

- [ ] **Step 6: 提交**

```bash
git add guanlan_v2/strategy/compute/cpcv.py tests/test_dl_source_validate.py
git commit -m "feat(gat-dl): cpcv.validate_dl_source(①闸验证DL源·复用decile/DSR原语·passes_gate)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `dl_ensemble.default_dl_sources` 注册 gat(+1 行,字节安全)

**Files:**
- Modify: `guanlan_v2/strategy/compute/dl_ensemble.py:100-110`(`default_dl_sources` 内追加 gat 行;**不改 fincast/lstm 行**)
- Test: `tests/test_dl_ensemble.py`(追加 2 个测试,不动既有)

**Interfaces:**
- Consumes: `DLSource`、`apply_dl_ensemble`、`default_dl_sources`(均已存在)。
- Produces: `default_dl_sources()` 多含一个 `model_id="gat"` 的 `DLSource`(`path` 以 `dl_pred_gat.parquet` 结尾,`score_col="pred_ret_5d"`,`weight_mode="adaptive"`)。

- [ ] **Step 1: 追加失败测试到 `tests/test_dl_ensemble.py` 末尾**

```python
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
```

- [ ] **Step 2: 跑测验证失败**

Run: `python -m pytest tests/test_dl_ensemble.py::test_default_dl_sources_includes_gat -q`
Expected: FAIL(gat 不在注册表)

- [ ] **Step 3: 在 `default_dl_sources()` 的 lstm 行后追加 gat 行**

`guanlan_v2/strategy/compute/dl_ensemble.py`,`default_dl_sources` 的 return 列表中,紧跟 `lstm` 那项后加:

```python
        DLSource(model_id="gat", path=str(var / "dl_pred_gat.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
```

改后该函数 return 列表为(参考,**fincast/lstm 两项逐字不动**):

```python
    return [
        DLSource(model_id="fincast", path=str(var / "v4_fincast_pred.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
        DLSource(model_id="lstm", path=str(var / "dl_pred_lstm.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
        DLSource(model_id="gat", path=str(var / "dl_pred_gat.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
    ]
```

- [ ] **Step 4: 跑测验证通过(含既有全套 dl_ensemble 测试)**

Run: `python -m pytest tests/test_dl_ensemble.py -q`
Expected: PASS(既有 + 2 新,全绿)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/dl_ensemble.py tests/test_dl_ensemble.py
git commit -m "feat(gat-dl): default_dl_sources 注册 gat 源(+1行·字节安全·缺文件诚实退出)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 编排脚本 `gat_predict.py`(GPU)+ `gat_validate.py`(薄壳)

**Files:**
- Create: `scripts/gat_predict.py`
- Create: `scripts/gat_validate.py`
- Test: `tests/test_gat_scripts.py`(轻量:`gat_predict --help` 退 0;`gat_validate` 在合成 parquet 上跑通)

**Interfaces:**
- Consumes: `gat_io.*`、`gat_model.train_gat/predict_gat`、`fincast_io.write_pred_rolling`、`cpcv.validate_dl_source`、引擎 `QlibBinaryLoader`/`list_all_instruments`/`_latest_trade_date`/`DEFAULT_PROVIDER`。
- Produces: `var/dl_pred_gat.parquet`(脚本真跑时);`gat_validate.py` 打印闸 JSON。

- [ ] **Step 1: 写失败测试** `tests/test_gat_scripts.py`

```python
# tests/test_gat_scripts.py
# 脚本轻量门禁:gat_predict 可 import/--help(argparse);gat_validate 端到端跑合成 parquet 出 JSON。
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent


def test_gat_predict_help_exits_zero():
    r = subprocess.run([sys.executable, str(_REPO / "scripts" / "gat_predict.py"), "--help"],
                       capture_output=True, text=True, cwd=str(_REPO))
    assert r.returncode == 0
    assert "--date" in r.stdout and "--device" in r.stdout


def test_gat_validate_runs_on_synthetic(tmp_path, monkeypatch):
    # 造合成 gat parquet,桩掉引擎前向收益,直接调函数(脚本同一入口)验证闸可跑
    from guanlan_v2.strategy.compute import cpcv
    dates = pd.bdate_range("2026-01-05", periods=40)
    rng = np.random.default_rng(0)
    rows = [{"eval_date": d, "instrument": f"C{i:03d}", "pred_ret_5d": float(rng.normal())}
            for d in dates for i in range(60)]
    p = tmp_path / "dl_pred_gat.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(str(r.date), str(r.code)): float(r.score) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.validate_dl_source(str(p))
    assert out["ready"] is True and "dsr" in out and "passes_gate" in out
```

- [ ] **Step 2: 跑测验证失败**

Run: `python -m pytest tests/test_gat_scripts.py -q`
Expected: FAIL(`gat_predict.py` 不存在 → 子进程非 0 / FileNotFound)

- [ ] **Step 3: 实现 `scripts/gat_predict.py`**

```python
# -*- coding: utf-8 -*-
"""GAT 关系图模型每日训练 + 推理(guanlan 自有·conda stocks GPU)。

跑法:
    D:/app/miniconda/envs/stocks/python.exe scripts/gat_predict.py --date 2026-06-27

读 close/volume(QlibBinaryLoader 直读二进制)→ gat_io 每日 (X,A,y) → gat_model 训练
→ eval_date 前向 → pred_ret_5d → 写 var/dl_pred_gat.parquet(DL 集成层契约;train_cutoff 诚实落盘)。
**命门**:GPU 训练/推理离线;9999 请求路径绝不跑模型。
"""
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "engine"))

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader   # noqa: E402
from guanlan_v2.strategy.compute.breadth import list_all_instruments       # noqa: E402
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date  # noqa: E402
from guanlan_v2.strategy.compute.gat_io import (                            # noqa: E402
    compute_node_features, build_corr_graph, forward_label, rebalance_dates)
from guanlan_v2.strategy.compute.gat_model import train_gat, predict_gat    # noqa: E402
from guanlan_v2.strategy.compute.fincast_io import write_pred_rolling       # noqa: E402

HORIZON = 5
OUT = str(_REPO / "var" / "dl_pred_gat.parquet")


def _read_panels(loader, codes, eval_date):
    """逐码读 close/volume bins → (close_panel, volume_panel),截到 ≤ eval_date(不看未来)。"""
    close, vol = {}, {}
    for c in codes:
        try:
            s = loader._read_bin(c, "close")
            if s is not None and len(s):
                close[c] = s
            v = loader._read_bin(c, "volume")
            if v is not None and len(v):
                vol[c] = v
        except Exception:   # noqa: BLE001 — 单码读失败跳过
            continue
    if not close:
        raise RuntimeError("无任何可读 close(检查 provider_uri)")
    cp = pd.DataFrame(close).sort_index(); cp.index = pd.DatetimeIndex(cp.index)
    cp = cp.loc[:pd.Timestamp(eval_date)]
    vp = None
    if vol:
        vp = pd.DataFrame(vol).sort_index(); vp.index = pd.DatetimeIndex(vp.index)
        vp = vp.loc[:pd.Timestamp(eval_date)]
    return cp, vp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=最新交易日)")
    ap.add_argument("--device", default="cuda", help="cuda|cpu(无卡自动退 cpu)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--train-start", default="2022-01-01")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--topk", type=int, default=20)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    args = ap.parse_args()

    import torch
    device = args.device if (args.device != "cuda" or torch.cuda.is_available()) else "cpu"
    eval_date = args.date or _latest_trade_date(args.provider)
    print(f"评估日 {eval_date} · device {device} · provider {args.provider}", flush=True)

    loader = QlibBinaryLoader(args.provider)
    codes = list_all_instruments(args.provider)
    print(f"全市场 {len(codes)} 码,读 close/volume 面板 ...", flush=True)
    cp, vp = _read_panels(loader, codes, eval_date)

    rdates = [d for d in rebalance_dates(cp.index, horizon=HORIZON, start=args.train_start)
              if d < pd.Timestamp(eval_date)]
    X_list, A_list, y_list, cutoff = [], [], [], None
    t0 = time.time()
    for d in rdates:
        node_codes, X = compute_node_features(cp, vp, d)
        if len(node_codes) < 50:
            continue
        y = forward_label(cp, d, node_codes, horizon=HORIZON)
        if int(np.isfinite(y).sum()) < 50:
            continue
        A = build_corr_graph(cp, d, node_codes, window=args.window, topk=args.topk)
        X_list.append(X); A_list.append(A); y_list.append(y); cutoff = d
    if len(X_list) < 10:
        print(f"训练样本不足({len(X_list)} 日),退出(不产文件 → 下游诚实退纯 LGB)", flush=True)
        return
    print(f"训练日 {len(X_list)} · 末标签日 {cutoff.date()} · 训练 GAT(epochs={args.epochs}) ...", flush=True)
    model = train_gat(X_list, A_list, y_list, device=device, epochs=args.epochs)

    e_codes, Xe = compute_node_features(cp, vp, eval_date)
    Ae = build_corr_graph(cp, eval_date, e_codes, window=args.window, topk=args.topk)
    preds = predict_gat(model, Xe, Ae, device=device)
    out = write_pred_rolling(OUT, eval_date, e_codes, np.asarray(preds, dtype=np.float32),
                             keep_days=60, train_cutoff=str(cutoff.date()))
    print(f"已写 {OUT}({len(out)} 条 · {pd.to_datetime(out['eval_date']).nunique()} 日 · {time.time() - t0:.1f}s)",
          flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 实现 `scripts/gat_validate.py`**

```python
# -*- coding: utf-8 -*-
"""GAT 源 CPCV 闸薄壳:对 var/dl_pred_gat.parquet 跑 cpcv.validate_dl_source 并打印结果(主 env 即可,无需 GPU)。

跑法:  python scripts/gat_validate.py            # 默认 var/dl_pred_gat.parquet
       python scripts/gat_validate.py <path>     # 指定预测表
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "engine"))

from guanlan_v2.strategy.compute.cpcv import validate_dl_source   # noqa: E402

OUT = str(_REPO / "var" / "dl_pred_gat.parquet")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else OUT
    res = validate_dl_source(path)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测验证通过**

Run: `python -m pytest tests/test_gat_scripts.py -q`
Expected: PASS(2 passed)

- [ ] **Step 6: 提交**

```bash
git add scripts/gat_predict.py scripts/gat_validate.py tests/test_gat_scripts.py
git commit -m "feat(gat-dl): gat_predict GPU编排脚本 + gat_validate闸薄壳(离线推理·train_cutoff诚实)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 全量回归 + 红线核验

**Files:**
- 无新增;只跑验证。

**Interfaces:** 无。

- [ ] **Step 1: 全量回归**

Run: `python -m pytest -q -m "not slow"`
Expected: 全绿,**唯一允许失败** = `test_vendored_hashes_match`(worktree 经 junction 读主树再生 breadth parquet 的已知环境性失败,与 GAT 无关;若失败需在主树或单独核验它非 GAT 引入)。

- [ ] **Step 2: 红线核验 — `v4.py` / `v4_fincast.py` 零改**

Run: `git -C <WT> diff --stat b4693ab -- guanlan_v2/strategy/compute/v4.py guanlan_v2/strategy/compute/v4_fincast.py`
Expected: 空输出(零改动)。

- [ ] **Step 3: 红线核验 — 未碰 LSTM / Spec3**

Run: `git -C <WT> diff b4693ab -- guanlan_v2/strategy/compute/dl_ensemble.py`
Expected: diff 只见新增 gat 那 1 个 `DLSource` 块;fincast/lstm 行无改动。

- [ ] **Step 4: 提交(若有未提交的核验笔记;通常无改动则跳过)**

```bash
git -C <WT> status --short   # 应为干净
```

---

## 真机实验(计划外·实施完成后单独阶段)

> 不属本计划编码任务;实施 + 评审通过后,在 conda stocks GPU 跑(详见 spec §7 集成测):
> 1. `D:/app/miniconda/envs/stocks/python.exe scripts/gat_predict.py --date <最新交易日>` → 产 `var/dl_pred_gat.parquet`(看耗时/条数)。
> 2. `python scripts/gat_validate.py` → 看 `dsr` / `passes_gate`。
> 3. 过闸(DSR≥0.5)→ 保留 parquet → 跑 `regen` → `/screen` 看徽章 "LGB + … + gat(w)";不过 → 删 parquet(GAT 休眠,选股字节回退现状)。
