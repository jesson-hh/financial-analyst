# 工作流 LSTM 升格为生产 DL 源(Spec 3)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作流里的 LSTM 研究节点升格成离线生产 DL 源 —— 训练→产 `var/dl_pred_lstm.parquet`→经 DL 集成层混进 v4,并像 LGB 一样在工作流页(「发布为 DL 源」按钮 + 异步端点)和选股页(Spec 1 多源徽章自动显)两界面打通。

**Architecture:** 纯函数 `lstm_io.py`(序列窗 + PIT 标签闸 + 截面预测·可 TDD·无 torch)被生产器 `lstm_predict.py`(guanlan 主 env·torch CPU·复用 `build_feature_panel` + `fincast_io.write_pred_rolling`)消费;`lstm_workflow.py` 是「发布」入口(训练→regen 链);workflow `/model/publish_dl`(镜像 `/model/promote` 异步子进程 + 单飞锁)起 `lstm_workflow`;前端 `PublishDlPanel`(镜像 `PromoteModelPanel`)挂 lstm 节点。DL 集成层(Spec 1)不改,只在 `default_dl_sources()` append lstm。

**Tech Stack:** Python 3.13(guanlan 主 env·`python -m pytest` 从仓根)、pandas/numpy、torch 2.10+cpu、pytest、FastAPI、React(browser-Babel JSX)。**前置参考**:`docs/superpowers/specs/2026-06-24-lstm-dl-source-design.md`。

## Global Constraints

- **PIT 无前视**:`train_cutoff = eval_date − horizon`(最后一个前向收益已实现的标签日,< eval_date)→ provenance `lookahead = (eval_date ≤ cutoff) = False`。务必传 `eval_date − horizon`,绝不传 eval_date 本身。
- **LGB ≥0.5 主导**:`MAX_TOTAL_DL_W=0.5`(`dl_mix_scores` 已强制);lstm + fincast 双源共享该封顶,本 Spec 不动。
- **serving 零推理**:训练/推理只在离线子进程(workflow 端点起);9999 请求路径绝不训练/加载模型。
- **输出契约**:`eval_date/instrument/pred_ret_5d`(+ 可选 `train_cutoff`),与 Spec 1 `_load_dl_for_date` 读的逐字一致;`instrument` = qlib 形 `SH######`。
- **诚实失败**:parquet 缺/匹配 < `MIN_MATCH=50` → 源 inactive,纯 LGB,不冒充。
- **计算 env**:guanlan 主 env(torch 2.10+cpu 已装);测试从仓根 `G:/guanlan-v2` 跑;引擎 fork 路径 = 测试顶 prepend 仓内 `engine/`。
- **不打架(本机并行会话约束)**:每次 `git commit` 前先 `git branch --show-current` 确认在 `main`;若被切到 `feat/cpcv-validation` 则**停**报用户。
- **GateGuard**:每文件首次 Write/Edit + 每会话首次 Bash 前先报 facts。

---

### Task 1: `lstm_io.py` 纯函数(前向收益 + PIT 序列 + 截面预测)· TDD

抽 `_lstm_eval`(workflow/api.py:1339-1383)的序列/标签逻辑成可测纯函数(只 numpy/pandas,无 torch)。

**Files:**
- Create: `guanlan_v2/strategy/compute/lstm_io.py`
- Test: `tests/test_lstm_io.py`

**Interfaces:**
- Produces:
  - `add_forward_return(panel: pd.DataFrame, horizon: int, close_col="close", out_col="__fwd_ret__") -> pd.DataFrame`(MultiIndex (instrument,datetime) 面板,逐 instrument 加前向 horizon 日收益列;末 horizon 行 NaN)
  - `build_sequences(panel, feature_cols: list, label_col: str, seq_len: int, cutoff) -> (np.ndarray[N,seq_len,F] float32, np.ndarray[N] float32, list[tuple])`(PIT 闸:label_date ≤ cutoff & 窗口/标签有限才入选)
  - `predict_index(panel, feature_cols: list, seq_len: int, eval_date) -> (np.ndarray[M,seq_len,F] float32, list[str])`(每 instrument 截 ≤eval_date 的末 seq_len 窗;不足/末值非有限剔)

- [ ] **Step 1: 写失败测试** —— 新建 `tests/test_lstm_io.py`:

```python
# tests/test_lstm_io.py
# LSTM 港移纯函数门禁:前向收益 horizon 对齐;PIT 序列闸(label_date≤cutoff);截面预测末窗。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.lstm_io import (  # noqa: E402
    add_forward_return, build_sequences, predict_index,
)


def _panel(codes, dates, fcols):
    """造 MultiIndex (instrument,datetime) 面板:每特征列 = 行序号(可预测),close = 100+行序号。"""
    rows = []
    idx = []
    for c in codes:
        for i, d in enumerate(dates):
            idx.append((c, pd.Timestamp(d)))
            rows.append([float(i)] * len(fcols) + [100.0 + i])
    df = pd.DataFrame(rows, columns=list(fcols) + ["close"],
                      index=pd.MultiIndex.from_tuples(idx, names=["instrument", "datetime"]))
    return df.sort_index()


def test_add_forward_return_horizon():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    out = add_forward_return(p, horizon=5)
    s = out.xs("SH600000", level="instrument")["__fwd_ret__"]
    # close = 100..109;t=0 → close[5]/close[0]-1 = 105/100-1 = 0.05
    assert abs(float(s.iloc[0]) - 0.05) < 1e-6
    # 末 horizon 行无前向收益 → NaN
    assert bool(np.isnan(s.iloc[-1]))


def test_build_sequences_shape_and_pit_gate():
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    p = _panel(["SH600000", "SZ000001"], dates, ["f1", "f2"])
    p = add_forward_return(p, horizon=5)
    cutoff = pd.Timestamp(dates[20])      # 只收 label_date ≤ dates[20]
    X, y, idx = build_sequences(p, ["f1", "f2"], "__fwd_ret__", seq_len=4, cutoff=cutoff)
    assert X.dtype == np.float32 and X.ndim == 3 and X.shape[1:] == (4, 2)
    assert len(y) == len(idx) == X.shape[0]
    # PIT 闸:无样本 label_date > cutoff
    assert all(d <= cutoff for d, _c in idx)
    # 窗口右端 = label_date;f1 = 行序号 → 末步 = label_date 的行序号
    pos = {pd.Timestamp(d): i for i, d in enumerate(dates)}
    d0, _c0 = idx[0]
    assert abs(float(X[0, -1, 0]) - float(pos[d0])) < 1e-6


def test_build_sequences_drops_unrealized_label():
    dates = pd.date_range("2026-01-01", periods=12, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    p = add_forward_return(p, horizon=5)
    # cutoff 放最后一天:末 horizon 行 label NaN → 必被剔(不入训)
    X, y, idx = build_sequences(p, ["f1"], "__fwd_ret__", seq_len=3, cutoff=pd.Timestamp(dates[-1]))
    assert all(d <= pd.Timestamp(dates[-6]) for d, _c in idx)   # 末5行label NaN
    assert np.isfinite(y).all()


def test_predict_index_last_window_per_code():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000", "SZ000001"], dates, ["f1"])
    X, codes = predict_index(p, ["f1"], seq_len=4, eval_date=pd.Timestamp(dates[-1]))
    assert X.shape == (2, 4, 1) and X.dtype == np.float32
    assert set(codes) == {"SH600000", "SZ000001"}
    # 末窗右端 = eval_date 的行序号(=9)
    assert abs(float(X[0, -1, 0]) - 9.0) < 1e-6


def test_predict_index_cuts_future_and_skips_short():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    # eval_date 取中间 → 末窗右端 = 该日;且不含未来
    X, codes = predict_index(p, ["f1"], seq_len=3, eval_date=pd.Timestamp(dates[5]))
    assert X.shape == (1, 3, 1)
    assert abs(float(X[0, -1, 0]) - 5.0) < 1e-6
    # seq_len 比可用历史长 → 该 code 被跳
    X2, codes2 = predict_index(p, ["f1"], seq_len=20, eval_date=pd.Timestamp(dates[-1]))
    assert X2.shape[0] == 0 and codes2 == []
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_lstm_io.py -v`
  Expected: FAIL（ModuleNotFoundError: lstm_io）。

- [ ] **Step 3: 实现** —— 新建 `guanlan_v2/strategy/compute/lstm_io.py`:

```python
# -*- coding: utf-8 -*-
"""LSTM 港移纯函数 helper:前向收益标签 + PIT 序列窗 + 截面预测输入。
无 torch/无引擎依赖(只 numpy/pandas),可 TDD。逻辑抽自 workflow/api.py:_lstm_eval。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd


def add_forward_return(panel: pd.DataFrame, horizon: int, close_col: str = "close",
                       out_col: str = "__fwd_ret__") -> pd.DataFrame:
    """逐 instrument 加前向 horizon 日收益列 close[t+h]/close[t]−1(末 horizon 行 NaN)。
    panel: MultiIndex (instrument, datetime)。返回带新列的副本。"""
    panel = panel.copy()
    fwd = panel[close_col].groupby(level="instrument").transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    panel[out_col] = fwd
    return panel


def build_sequences(panel: pd.DataFrame, feature_cols: List[str], label_col: str,
                    seq_len: int, cutoff) -> Tuple[np.ndarray, np.ndarray, list]:
    """逐 instrument 按日期排序滑窗:每 label_date t 取前 seq_len 期特征窗 + label[t]。
    PIT 闸:仅 label_date ≤ cutoff & 窗口全有限 & label 有限 的样本入选。
    返回 (X[N,seq_len,F] float32, y[N] float32, index[(datetime,instrument)])。"""
    cutoff = pd.Timestamp(cutoff)
    X: List[np.ndarray] = []
    y: List[float] = []
    idx: list = []
    for code, g in panel.groupby(level="instrument"):
        g = g.sort_index(level="datetime")
        dts = g.index.get_level_values("datetime")
        feat = g[feature_cols].to_numpy("float64")
        lab = g[label_col].to_numpy("float64")
        m = feat.shape[0]
        for t in range(seq_len - 1, m):
            if dts[t] > cutoff:
                continue
            win = feat[t - seq_len + 1: t + 1]
            yv = lab[t]
            if not np.isfinite(win).all() or not np.isfinite(yv):
                continue
            X.append(win)
            y.append(float(yv))
            idx.append((dts[t], code))
    if not X:
        return (np.empty((0, seq_len, len(feature_cols)), dtype=np.float32),
                np.empty((0,), dtype=np.float32), [])
    return (np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32), idx)


def predict_index(panel: pd.DataFrame, feature_cols: List[str], seq_len: int,
                  eval_date) -> Tuple[np.ndarray, List[str]]:
    """每 instrument 取截至 ≤ eval_date 的末 seq_len 期特征窗为预测输入(不看未来)。
    历史不足 seq_len 或末窗含非有限值 → 跳该 code。返回 (X[M,seq_len,F] float32, codes)。"""
    eval_ts = pd.Timestamp(eval_date)
    X: List[np.ndarray] = []
    codes: List[str] = []
    for code, g in panel.groupby(level="instrument"):
        g = g.sort_index(level="datetime")
        g = g[g.index.get_level_values("datetime") <= eval_ts]
        if len(g) < seq_len:
            continue
        win = g[feature_cols].to_numpy("float64")[-seq_len:]
        if not np.isfinite(win).all():
            continue
        X.append(win)
        codes.append(code)
    if not X:
        return np.empty((0, seq_len, len(feature_cols)), dtype=np.float32), []
    return np.asarray(X, dtype=np.float32), codes
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_lstm_io.py -v`
  Expected: PASS（5 passed）。

- [ ] **Step 5: 提交**(先 `git branch --show-current` = main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add tests/test_lstm_io.py guanlan_v2/strategy/compute/lstm_io.py
git commit -m "feat(lstm-dl): lstm_io 纯函数(前向收益+PIT序列窗+截面预测·TDD)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `write_pred_rolling` 加可选 `train_cutoff` 列 · TDD

让 DL 预测表能带 `train_cutoff`(LSTM 诚实显形;FinCast 不传 → 列不变·字节等价)。`_load_dl_for_date` 已读该列(dl_ensemble.py:84-89)。

**Files:**
- Modify: `guanlan_v2/strategy/compute/fincast_io.py`(`write_pred_rolling`)
- Test: `tests/test_fincast_io.py`(追加 2 测)

**Interfaces:**
- Consumes(Task 1 无关)
- Produces: `write_pred_rolling(out_path, eval_date, chosen, preds, keep_days=60, train_cutoff=None) -> pd.DataFrame`(`train_cutoff` 非 None → 加常量 datetime64 列;None → 无该列,与现有字节等价)

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_fincast_io.py` 末尾追加:

```python
def test_write_pred_rolling_with_train_cutoff(tmp_path):
    p = str(tmp_path / "dl_pred_lstm.parquet")
    df = write_pred_rolling(p, "2026-06-22", ["SH600000", "SZ000001"], [0.01, -0.02],
                            keep_days=60, train_cutoff="2026-06-15")
    out = pd.read_parquet(p)
    assert "train_cutoff" in out.columns
    assert pd.api.types.is_datetime64_any_dtype(out["train_cutoff"])
    assert (pd.to_datetime(out["train_cutoff"]) == pd.Timestamp("2026-06-15")).all()


def test_write_pred_rolling_without_cutoff_unchanged(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    df = write_pred_rolling(p, "2026-06-22", ["SH600000"], [0.01], keep_days=60)
    out = pd.read_parquet(p)
    assert list(out.columns) == ["eval_date", "instrument", "pred_ret_5d"]   # 无 train_cutoff
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_fincast_io.py -k train_cutoff -v`
  Expected: FAIL（write_pred_rolling 不接受 train_cutoff / 无该列）。

- [ ] **Step 3: 实现** —— 编辑 `guanlan_v2/strategy/compute/fincast_io.py`。

(3a) 签名加 `train_cutoff=None`:

```python
def write_pred_rolling(out_path: str, eval_date, chosen: List[str], preds,
                       keep_days: int = 60, train_cutoff=None) -> pd.DataFrame:
```

(3b) `new_df` 构造块(`insts = list(chosen)` 那段)改为按需加列:

```python
    ed = pd.Timestamp(eval_date)
    insts = list(chosen)
    # 显式 [ed]*n 广播:pandas 2.1 不会把标量 Timestamp 在 dict 构造里广播到数组长度;显式列表跨版本稳。
    cols = {"eval_date": [ed] * len(insts), "instrument": insts,
            "pred_ret_5d": np.asarray(preds, dtype=np.float32)}
    if train_cutoff is not None:                       # LSTM 等训练源诚实显形 train_cutoff
        cols["train_cutoff"] = [pd.Timestamp(train_cutoff)] * len(insts)
    new_df = pd.DataFrame(cols)
```

(3c) rolling-keep 合并旧表的列选择(原 `old = old[["eval_date","instrument","pred_ret_5d"]]` 那段)改为动态保留 train_cutoff:

```python
        keep_cols = ["eval_date", "instrument", "pred_ret_5d"]
        if "train_cutoff" in old.columns and train_cutoff is not None:
            keep_cols.append("train_cutoff")
        old = old[[c for c in keep_cols if c in old.columns]]
        frames = [f for f in (old, new_df) if not f.empty]     # 防空帧 concat 的 FutureWarning
        combined = pd.concat(frames, ignore_index=True) if frames else new_df
```

(3d) 写盘前 `combined["eval_date"] = pd.to_datetime(combined["eval_date"])` 之后补一行(防 train_cutoff 落 object,承 Spec 2 跨 pandas/pyarrow 坑):

```python
    combined["eval_date"] = pd.to_datetime(combined["eval_date"])
    if "train_cutoff" in combined.columns:
        combined["train_cutoff"] = pd.to_datetime(combined["train_cutoff"])
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_fincast_io.py -v`
  Expected: PASS（8 passed:原 6 + 新 2）。

- [ ] **Step 5: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/strategy/compute/fincast_io.py tests/test_fincast_io.py
git commit -m "feat(lstm-dl): write_pred_rolling 加可选 train_cutoff 列(训练源诚实显形·FinCast不传字节等价)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `default_dl_sources()` 注册 lstm 源 · TDD

让 regen 的 DL 集成层把 lstm 作为第二源(parquet 缺则诚实 inactive,不破 FinCast)。

**Files:**
- Modify: `guanlan_v2/strategy/compute/dl_ensemble.py`(`default_dl_sources`)
- Test: `tests/test_dl_ensemble.py`(追加 1 测)

**Interfaces:**
- Consumes: `DLSource`、`default_dl_sources`(已存在)
- Produces: `default_dl_sources()` 返回含 `model_id="fincast"` 与 `model_id="lstm"` 两源(后者 path 指 `var/dl_pred_lstm.parquet`)

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_dl_ensemble.py` 末尾追加:

```python
def test_default_dl_sources_includes_lstm():
    from guanlan_v2.strategy.compute.dl_ensemble import default_dl_sources
    srcs = default_dl_sources()
    ids = {s.model_id for s in srcs}
    assert "fincast" in ids and "lstm" in ids
    lstm = next(s for s in srcs if s.model_id == "lstm")
    assert lstm.path.endswith("dl_pred_lstm.parquet")
    assert lstm.score_col == "pred_ret_5d" and lstm.weight_mode == "adaptive"
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -k lstm -v`
  Expected: FAIL（lstm not in ids）。

- [ ] **Step 3: 实现** —— 编辑 `guanlan_v2/strategy/compute/dl_ensemble.py` 的 `default_dl_sources`,把 Phase 2 注释行换成真源:

```python
    return [
        DLSource(model_id="fincast", path=str(var / "v4_fincast_pred.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
        DLSource(model_id="lstm", path=str(var / "dl_pred_lstm.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
    ]
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v`
  Expected: PASS（10 passed:原 9 + 新 1)。**确认现有 9 测不破**(lstm parquet 缺 → 该源在 `apply_dl_ensemble` 走 missing 分支,fincast 单源行为不变)。

- [ ] **Step 5: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/strategy/compute/dl_ensemble.py tests/test_dl_ensemble.py
git commit -m "feat(lstm-dl): default_dl_sources 注册 lstm 源(parquet缺则诚实inactive)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `lstm_predict.py` 生产器模块(torch CPU 训练 + 预测 + 写出)

复用 `build_feature_panel`(全市场 38 因子 PIT 面板)+ `lstm_io` + torch(镜像 `_lstm_eval` 训练)→ 写 `var/dl_pred_lstm.parquet`(带 train_cutoff)+ 存 `model.pt`。**放 compute/ 作模块**(`-m` 可跑 + 被 lstm_workflow import·比 scripts/ 更适合子进程链)。torch 训练为集成验(Task 8);本任务给完整代码 + 语法/import 冒烟。

**Files:**
- Create: `guanlan_v2/strategy/compute/lstm_predict.py`

**Interfaces:**
- Consumes: `lstm_io.{add_forward_return,build_sequences,predict_index}`、`v4.{build_feature_panel,_select_mf}`、`fincast_io.write_pred_rolling`、`regen.{DEFAULT_PROVIDER,_latest_trade_date}`、`breadth.list_all_instruments`、`financial_analyst.data.universe.resolve_universe_codes`、`QlibBinaryLoader`
- Produces: `train_and_predict(provider, eval_date, universe="csi800", seq_len=10, hidden=32, layers=1, lr=1e-3, epochs=40, horizon=5, sample_cap=6000, history_days=730, out_path=OUT, model_path=MODEL_PT) -> dict`;`main()`(argparse·`python -m guanlan_v2.strategy.compute.lstm_predict --date ...`)

- [ ] **Step 1: 实现** —— 新建 `guanlan_v2/strategy/compute/lstm_predict.py`:

```python
# -*- coding: utf-8 -*-
"""LSTM 生产 DL 源:全市场 38 因子 PIT 面板 → 序列训练(torch CPU) → 截面预测 5 日收益
→ 直写 var/dl_pred_lstm.parquet(带 train_cutoff)+ 存 model.pt。

guanlan 主 env 跑(torch 2.10+cpu);也被 lstm_workflow(发布端点)import。
    python -m guanlan_v2.strategy.compute.lstm_predict --date 2026-06-22 --universe csi800
**命门**:训练/推理离线;9999 请求路径绝不跑。PIT:train_cutoff = eval_date − horizon。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
from financial_analyst.data.universe import resolve_universe_codes
from guanlan_v2.strategy.compute.breadth import list_all_instruments
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date
from guanlan_v2.strategy.compute.v4 import build_feature_panel, _select_mf
from guanlan_v2.strategy.compute.lstm_io import (
    add_forward_return, build_sequences, predict_index,
)
from guanlan_v2.strategy.compute.fincast_io import write_pred_rolling

_REPO = Path(__file__).resolve().parents[3]
OUT = str(_REPO / "var" / "dl_pred_lstm.parquet")
MODEL_PT = str(_REPO / "var" / "models" / "lstm" / "latest.pt")
LABEL_COL = "__fwd_ret__"


def _train_lstm(X: np.ndarray, y: np.ndarray, n_features: int, hidden: int,
                layers: int, lr: float, epochs: int):
    """镜像 _lstm_eval 的 torch 训练(CPU·seed 固定·nn.LSTM→Linear·Adam/MSE)。返回 net。"""
    import torch
    from torch import nn
    torch.manual_seed(0)
    torch.set_num_threads(4)
    Xtr = torch.tensor(X)                       # (N,L,F) float32
    ytr = torch.tensor(y).view(-1, 1)

    class _LSTMNet(nn.Module):
        def __init__(self, nf, hid, nl):
            super().__init__()
            self.lstm = nn.LSTM(nf, hid, nl, batch_first=True)
            self.head = nn.Linear(hid, 1)

        def forward(self, x):
            out, _h = self.lstm(x)
            return self.head(out[:, -1, :])

    net = _LSTMNet(n_features, hidden, layers)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    lossfn = nn.MSELoss()
    net.train()
    bs, N = 256, int(Xtr.shape[0])
    for _ep in range(epochs):
        perm = torch.randperm(N)
        for b in range(0, N, bs):
            sel = perm[b:b + bs]
            opt.zero_grad()
            loss = lossfn(net(Xtr[sel]), ytr[sel])
            loss.backward()
            opt.step()
    net.eval()
    return net


def _predict(net, X: np.ndarray) -> np.ndarray:
    import torch
    with torch.no_grad():
        out = net(torch.tensor(X)).view(-1).cpu().numpy()
    return np.asarray(out, dtype=np.float32)


def train_and_predict(provider: str = DEFAULT_PROVIDER, eval_date: Optional[str] = None,
                      universe: str = "csi800", seq_len: int = 10, hidden: int = 32,
                      layers: int = 1, lr: float = 1e-3, epochs: int = 40, horizon: int = 5,
                      sample_cap: int = 6000, history_days: int = 730,
                      out_path: str = OUT, model_path: str = MODEL_PT) -> dict:
    eval_date = eval_date or _latest_trade_date(provider)
    eval_ts = pd.Timestamp(eval_date)
    start = (eval_ts - pd.Timedelta(days=history_days)).date().isoformat()
    print(f"[lstm_predict] eval_date {eval_date} · universe {universe} · provider {provider}", flush=True)

    loader = QlibBinaryLoader(provider)
    pred_codes = list_all_instruments(provider)
    print(f"[lstm_predict] 全市场 {len(pred_codes)} 码,build_feature_panel ...", flush=True)
    panel = build_feature_panel(loader, pred_codes, start, eval_date)
    panel = add_forward_return(panel, horizon)
    feat_cols = _select_mf(list(panel.columns), None)        # 38 v4 因子

    dates = sorted(pd.DatetimeIndex(panel.index.get_level_values("datetime")).unique())
    if len(dates) <= horizon:
        raise RuntimeError(f"面板交易日 {len(dates)} ≤ horizon {horizon};历史太短")
    cutoff = dates[-(horizon + 1)]                            # = eval_date − horizon 交易日 < eval_date

    train_codes = set(str(c) for c in resolve_universe_codes(universe))
    tr_mask = panel.index.get_level_values("instrument").isin(train_codes)
    train_panel = panel[tr_mask]
    X, y, _idx = build_sequences(train_panel, feat_cols, LABEL_COL, seq_len, cutoff)
    if len(X) < 10:
        raise RuntimeError(f"训练序列不足 ({len(X)}<10);universe/seq_len 调整")
    if len(X) > sample_cap:                                   # 定种子下采样守 CPU 时延
        rng = np.random.RandomState(0)
        sel = rng.choice(len(X), sample_cap, replace=False)
        X, y = X[sel], y[sel]
    print(f"[lstm_predict] 训练样本 {len(X)} · 特征 {len(feat_cols)} · cutoff {cutoff.date()} · 训练 ...", flush=True)

    t0 = time.time()
    net = _train_lstm(X, y, len(feat_cols), hidden, layers, lr, epochs)
    print(f"[lstm_predict] 训练完 {time.time()-t0:.1f}s · 截面预测 ...", flush=True)

    Xp, codes = predict_index(panel, feat_cols, seq_len, eval_date)
    if not codes:
        raise RuntimeError("截面预测无有效标的(末窗不足/含非有限)")
    preds = _predict(net, Xp)
    out = write_pred_rolling(out_path, eval_date, codes, preds, keep_days=60, train_cutoff=cutoff)

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    import torch
    torch.save(net.state_dict(), model_path)
    print(f"[lstm_predict] 已写 {out_path}({len(codes)} 只 · cutoff {cutoff.date()} · "
          f"mean {float(np.mean(preds)):+.4f})+ model.pt", flush=True)
    return {"eval_date": str(eval_ts.date()), "train_cutoff": str(cutoff.date()),
            "n_train": int(len(X)), "n_pred": int(len(codes)), "out": out_path}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=最新交易日)")
    ap.add_argument("--universe", default="csi800", help="训练池(预测恒全市场)")
    ap.add_argument("--seq-len", type=int, default=10)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=1)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--sample-cap", type=int, default=6000)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    a = ap.parse_args()
    train_and_predict(provider=a.provider, eval_date=(a.date or None), universe=a.universe,
                      seq_len=a.seq_len, hidden=a.hidden, layers=a.layers, lr=a.lr,
                      epochs=a.epochs, horizon=a.horizon, sample_cap=a.sample_cap)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法 + import 冒烟**(主 env·不真训练)

Run: `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('guanlan_v2/strategy/compute/lstm_predict.py',encoding='utf-8').read()); print('ast ok')"`
Run: `cd G:/guanlan-v2 && python -c "import sys; from pathlib import Path; sys.path.insert(0,str(Path('engine').resolve())); from guanlan_v2.strategy.compute.lstm_predict import train_and_predict, main; print('import ok')"`
Expected: `ast ok` + `import ok`（真训练留 Task 8）。

- [ ] **Step 3: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/strategy/compute/lstm_predict.py
git commit -m "feat(lstm-dl): lstm_predict 生产器(全市场38因子PIT序列·torch CPU训练·写dl_pred_lstm)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `lstm_workflow.py` 发布入口(训练 → regen 链)

「发布为 DL 源」子进程跑的 `__main__`:读 spec.json → `train_and_predict` → `regen <date>`。镜像 `model_workflow`(promote 子进程跑的模块)。打 `[lstm_publish]` 标记供端点解析阶段。

**Files:**
- Create: `guanlan_v2/strategy/compute/lstm_workflow.py`

**Interfaces:**
- Consumes: `lstm_predict.train_and_predict`、`regen`(`python -m ...regen <date>` 子进程)
- Produces: `run(spec: dict) -> int`、`main()`(`python -m guanlan_v2.strategy.compute.lstm_workflow <spec.json>`),spec = `{date, universe, params:{seq_len,hidden,layers,lr,epochs,horizon}}`

- [ ] **Step 1: 实现** —— 新建 `guanlan_v2/strategy/compute/lstm_workflow.py`:

```python
# -*- coding: utf-8 -*-
"""「发布 LSTM 为 DL 源」子进程入口(workflow /model/publish_dl 起):
读 spec.json → train_and_predict(写 var/dl_pred_lstm.parquet) → regen(折进 v4)。
打 [lstm_publish] 阶段标记供端点状态机解析。镜像 strategy/compute/model_workflow。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from guanlan_v2.strategy.compute.lstm_predict import train_and_predict
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date

_REPO = Path(__file__).resolve().parents[3]


def run(spec: dict) -> int:
    provider = spec.get("provider") or DEFAULT_PROVIDER
    date = spec.get("date") or _latest_trade_date(provider)
    p = dict(spec.get("params") or {})
    print(f"[lstm_publish] 阶段1 训练 LSTM · date {date}", flush=True)
    res = train_and_predict(
        provider=provider, eval_date=date, universe=(spec.get("universe") or "csi800"),
        seq_len=int(p.get("seq_len", 10)), hidden=int(p.get("hidden", 32)),
        layers=int(p.get("layers", 1)), lr=float(p.get("lr", 1e-3)),
        epochs=int(p.get("epochs", 40)), horizon=int(p.get("horizon", 5)))
    print(f"[lstm_publish] 训练完 {res}", flush=True)
    print(f"[lstm_publish] 阶段2 regen 折进 v4 · {date}", flush=True)
    rc = subprocess.call([sys.executable, "-m", "guanlan_v2.strategy.compute.regen", date],
                         cwd=str(_REPO))
    print(f"[lstm_publish] regen exit {rc}", flush=True)
    return int(rc)


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if len(sys.argv) > 1 else {}
    sys.exit(run(spec))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法 + import 冒烟**

Run: `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('guanlan_v2/strategy/compute/lstm_workflow.py',encoding='utf-8').read()); print('ast ok')"`
Run: `cd G:/guanlan-v2 && python -c "import sys; from pathlib import Path; sys.path.insert(0,str(Path('engine').resolve())); from guanlan_v2.strategy.compute.lstm_workflow import run, main; print('import ok')"`
Expected: `ast ok` + `import ok`。

- [ ] **Step 3: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/strategy/compute/lstm_workflow.py
git commit -m "feat(lstm-dl): lstm_workflow 发布入口(训练→regen链·[lstm_publish]阶段标记)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `/model/publish_dl`(+ status)端点 · workflow/api.py(镜像 promote)

异步子进程跑 `lstm_workflow` + 单飞锁 + 轮询状态。镜像 `_PROMOTE_*` / `_run_promote_subprocess`(workflow/api.py:43-87, 6054-6083)。

**Files:**
- Modify: `guanlan_v2/workflow/api.py`(模块级加状态机;路由工厂内加 2 路由 —— 与 `/model/promote` 同作用域)

**Interfaces:**
- Consumes: `lstm_workflow`(子进程 `-m`)、`_threading`、`JSONResponse`、`Body`(均 workflow/api.py 已 import)
- Produces: `POST /model/publish_dl`、`GET /model/publish_dl/status`

- [ ] **Step 1: 加模块级状态机** —— 在 workflow/api.py 的 `_run_promote_subprocess`(~line 88,`finally` 块之后)插入(镜像 promote,锁/状态独立):

```python
# ── LSTM「发布为 DL 源」异步子进程状态机(镜像 _PROMOTE_*;训练→regen 链)──────────
_PUBLISH_DL_LOCK = _threading.Lock()
_PUBLISH_DL_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _publish_dl_public_state() -> Dict[str, Any]:
    import time as _t
    with _PUBLISH_DL_LOCK:
        s = dict(_PUBLISH_DL_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_publish_dl_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"lstmpub_{spec['variant_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.lstm_workflow", str(sf)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _PUBLISH_DL_LOCK:
                _PUBLISH_DL_STATE["lines"].append(line)
                if "阶段1" in line:
                    _PUBLISH_DL_STATE["phase"], _PUBLISH_DL_STATE["label"], _PUBLISH_DL_STATE["step"] = \
                        ("train", "LSTM 训练中…", 1)
                elif "阶段2" in line:
                    _PUBLISH_DL_STATE["phase"], _PUBLISH_DL_STATE["label"], _PUBLISH_DL_STATE["step"] = \
                        ("regen", "折进 v4(regen)…", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _PUBLISH_DL_LOCK:
            _PUBLISH_DL_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})
```

- [ ] **Step 2: 加 2 路由** —— 先 grep 确认路由工厂函数名 + `/model/promote/status` 路由的真实位置(`grep -n "model/promote/status\|@router.post(\"/model/promote\")\|def make_router\|def create_router\|return router" guanlan_v2/workflow/api.py`),在 `/model/promote/status` 路由(~line 6083)之后、`return router` 之前插入:

```python
    @router.post("/model/publish_dl")
    def model_publish_dl(body: dict = Body(default={})):
        """发布 LSTM 为生产 DL 源:起子进程(训练 → 写 var/dl_pred_lstm.parquet → regen 折进 v4)。
        异步立即返回 + 轮询 /model/publish_dl/status。kind 限 lstm(首期)。"""
        import time as _t, uuid
        kind = str(body.get("kind") or "lstm").strip()
        if kind != "lstm":
            return JSONResponse({"ok": False, "reason": f"kind '{kind}' 暂不支持发布为 DL 源(首期 lstm)"})
        with _PUBLISH_DL_LOCK:
            already = _PUBLISH_DL_STATE["running"]
            if not already:
                vid = "dl_" + uuid.uuid4().hex[:10]
                _PUBLISH_DL_STATE.update({"running": True, "phase": "starting", "label": "启动发布…",
                    "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                    "variant_id": vid, "lines": []})
        if already:
            return JSONResponse({"ok": False, "reason": "已有发布在跑", "state": _publish_dl_public_state()})
        spec = {"variant_id": vid, "kind": "lstm",
                "date": str(body.get("date") or "").strip() or None,
                "universe": str((body.get("recipe") or {}).get("universe") or body.get("universe") or "csi800"),
                "params": dict((body.get("recipe") or {}).get("params") or body.get("params") or {})}
        _threading.Thread(target=lambda: _run_publish_dl_subprocess(spec), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "variant_id": vid,
                             "state": _publish_dl_public_state()})

    @router.get("/model/publish_dl/status")
    def model_publish_dl_status():
        return JSONResponse({"ok": True, "state": _publish_dl_public_state()})
```

(若工厂函数/`router` 变量名与 promote 处不同,以 grep 实际为准逐字对齐;两路由必须与 `/model/promote` 在同一 `router` 作用域。)

- [ ] **Step 3: 语法冒烟 + 路由计数守护**

Run: `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('guanlan_v2/workflow/api.py',encoding='utf-8').read()); print('ast ok')"`
Run: `cd G:/guanlan-v2 && python -c "import sys; from pathlib import Path; sys.path.insert(0,str(Path('engine').resolve())); import guanlan_v2.workflow.api as A; r=[fn for fn in dir(A) if 'publish_dl' in fn]; print('module syms', r)"`
Expected: `ast ok` + `module syms` 含 `_publish_dl_public_state`/`_run_publish_dl_subprocess`(路由在工厂内,真实挂载验于 Task 8 重启 9999 后 curl)。

- [ ] **Step 4: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/workflow/api.py
git commit -m "feat(lstm-dl): /model/publish_dl 端点(镜像promote·异步训练→regen·单飞锁+status)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 前端「发布为 DL 源 ⤓」按钮 · workflow.jsx(镜像 PromoteModelPanel)

LSTM 节点新增发布按钮 → POST `/model/publish_dl` → 轮询 `/model/publish_dl/status`。镜像 `PromoteModelPanel`(workflow.jsx:1773-1808)。

**Files:**
- Modify: `ui/factor/workflow.jsx`(加 `PublishDlPanel` 组件 + 在 lstm 节点渲染处挂载 + bump `?v`)

**Interfaces:**
- Consumes: `_post`、`_get`、`useState`、`useRef`、`useEffect`、`deriveRecipeForNode`、`SPECS`(workflow.jsx 已有,同 PromoteModelPanel 用)
- Produces: `PublishDlPanel({ node, nodes, edges, onNotify })`(仅 `node.type==='lstm'` 渲染)

- [ ] **Step 1: 加组件** —— 在 `PromoteModelPanel` 组件结束(workflow.jsx:1808 `}` 之后)插入:

```jsx
// LSTM 节点「发布为 DL 源」: 据上游 recipe(universe/params)→ POST /model/publish_dl
// (后端起子进程:训练 → 写 var/dl_pred_lstm.parquet → regen 折进 v4,异步)→ 轮询 status。仅 lstm 节点渲染。
function PublishDlPanel({ node, nodes, edges, onNotify }) {
  const [busy, setBusy] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);
  const notify = (t, b) => { if (onNotify) onNotify(t, b, 6500); };
  const publish = async (e) => {
    e.stopPropagation();
    if (busy) return;
    const recipe = deriveRecipeForNode(node, nodes, edges);
    setBusy(true);
    try {
      const name = String((node.params && node.params.name) || '').trim() || 'LSTM·DL源';
      const r = await _post('/model/publish_dl', { kind: 'lstm', name, recipe });
      if (!r || !r.ok) { setBusy(false); notify('发布失败', (r && r.reason) || '后端拒绝'); return; }
      notify('已起 LSTM 发布(分钟级)', '训练 → 折进 v4(regen);完成后选股页徽章多一源 lstm');
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(async () => {
        const s = (await _get('/model/publish_dl/status')) || {};
        const st = s.state || {};
        if (!st.running && st.phase === 'done') {
          clearInterval(timerRef.current); timerRef.current = null; setBusy(false);
          notify(st.ok ? '已发布 DL 源 ✓' : '发布失败',
                 st.ok ? '已折进 v4 · 重启 9999 后选股页徽章显 lstm 源' : (st.error || ''));
        }
      }, 3000);
    } catch (err) { setBusy(false); notify('发布失败', String((err && err.message) || err)); }
  };
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div onClick={publish} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.55 : 1, fontSize: 11, fontWeight: 500, color: 'var(--jin)', border: '1px solid var(--jin)', background: 'rgba(138,111,63,0.06)' }}>{busy ? '发布中(训练+regen)…' : '发布为 DL 源 ⤓'}</div>
      <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.4 }}>全市场 38 因子 PIT 训练 → 混进 v4(LGB 仍主导)→ 选股页徽章多一源(异步·分钟级)。</div>
    </div>
  );
}
```

- [ ] **Step 2: 在 lstm 节点挂载** —— grep `PromoteModelPanel node=` 找渲染处,在该行之后按节点类型分流加 lstm:

```jsx
        {node.type === 'lstm' && <PublishDlPanel node={node} nodes={nodes} edges={edges} onNotify={onNotify} />}
```

(props 名 `node/nodes/edges/onNotify` 以该处 `PromoteModelPanel` 实际传入为准,逐字对齐。)

- [ ] **Step 3: bump 缓存版本** —— `grep -n "?v=2026" ui/factor/workflow.jsx` 找 `?v` 串,日期段 bump(如 `?v=20260624a`),让浏览器拉新代码。

- [ ] **Step 4: 语法/JSX 冒烟**(node 侧 babel 不在仓内则只查可读 + 括号配平)

Run: `cd G:/guanlan-v2 && node -e "require('fs').readFileSync('ui/factor/workflow.jsx','utf8');console.log('read ok')"`
然后人工核 `PublishDlPanel` 与挂载行括号配平(真编译在 Task 8 浏览器 reload 验)。

- [ ] **Step 5: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add ui/factor/workflow.jsx
git commit -m "feat(lstm-dl): 工作流 LSTM 节点加「发布为 DL 源」按钮(镜像存入模型库·轮询status)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 真机集成验证(CPU 训练 → regen 双源 → live /screen → UI 一键)

**Files:** 无(验证)。需 guanlan 主 env(torch CPU)+ 真数据 + var/v4_fincast_pred.parquet 已在(Spec 2 产)。

- [ ] **Step 1: 纯函数 + 单元回归** —— `cd G:/guanlan-v2 && python -m pytest tests/test_lstm_io.py tests/test_fincast_io.py tests/test_dl_ensemble.py -q`
  Expected: 全绿(lstm_io 5 + fincast_io 8 + dl_ensemble 10)。

- [ ] **Step 2: 跑生产器(CPU 真训练)**
```bash
cd /g/guanlan-v2 && python -m guanlan_v2.strategy.compute.lstm_predict --date 2026-06-22 --universe csi800 2>&1 | tail -12
python -c "
import pandas as pd, numpy as np
d = pd.read_parquet('var/dl_pred_lstm.parquet')
x = d[pd.to_datetime(d.eval_date)==pd.Timestamp('2026-06-22')]
print('cols', list(d.columns), '| rows', len(x), '| finite', bool(np.isfinite(x.pred_ret_5d).all()))
print('train_cutoff', pd.to_datetime(x.train_cutoff).iloc[0].date(), '(应 = 06-22 减 5 交易日 <06-22)')
"
```
Expected: 产 `var/dl_pred_lstm.parquet`,列含 `eval_date/instrument/pred_ret_5d/train_cutoff`,当日 ~数千只、值有限、`train_cutoff < 2026-06-22`。

- [ ] **Step 3: regen + 验双源 active + lookahead:false**
```bash
cd /g/guanlan-v2 && python -m guanlan_v2.strategy.compute.regen 2026-06-22 > var/_regen_lstm.log 2>&1; tail -4 var/_regen_lstm.log
cat guanlan_v2/strategy/vendor/artifacts/v4_dl_provenance.json
```
Expected: `v4_dl_provenance.json` `sources` 含 `fincast` + `lstm` 双源、各 `weight>0`、`w_lgb ≥ 0.5`、lstm `lookahead:false`(诚实非 null)、`n_has` 合理。

- [ ] **Step 4: 重启 9999 + live /screen 三源**
重启 9999(杀监听 PID 等看门狗~10s);POST `/screen/run`(空 body)→ `v4_provenance.sources[].model_id` 含 `lstm` & `fincast`、`chosen` 20 股、`source=v4_ranking`、`panel_ok:true`;`publish_dl` 路由真挂载验:`curl -s -X POST http://127.0.0.1:9999/model/publish_dl/status` 返 `{ok:true,state:...}`。

- [ ] **Step 5: UI 一键端到端**(可选·浏览器)
工作流页 LSTM 节点点「发布为 DL 源 ⤓」→ 轮询走完两阶段(训练→regen)→ done notify;选股页 reload(bump 后)看顶栏徽章 `v4 · LGB+fincast(..)+lstm(..)`。

- [ ] **Step 6: 清理 + 全量回归** —— 删 `var/_regen_lstm.log`;`cd G:/guanlan-v2 && python -m pytest tests/test_lstm_io.py tests/test_fincast_io.py tests/test_dl_ensemble.py tests/test_screen_api.py tests/test_strategy_ranking.py tests/test_strategy_provenance.py tests/test_v4_fincast.py -q` 全绿。

---

## Self-Review(已对 spec 核对)

- **Spec §4.1 lstm_io 纯函数**:Task 1(add_forward_return/build_sequences/predict_index·5 测·PIT 闸)。✓
- **Spec §4.2 生产器**:Task 4(`lstm_predict.train_and_predict`·复用 build_feature_panel/_select_mf/lstm_io/write_pred_rolling)。**注**:spec 写 `scripts/lstm_predict.py`,plan 落为 `guanlan_v2/strategy/compute/lstm_predict.py` 模块(`-m` 可跑 + 被 lstm_workflow import·比 scripts/ 更适合子进程链),验证命令相应用 `-m`。✓
- **Spec §4.3 write_pred_rolling train_cutoff**:Task 2(可选列·FinCast 不传字节等价·8 测)。✓
- **Spec §4.4 default_dl_sources 注册**:Task 3(lstm 源·缺 parquet 诚实 inactive·10 测)。✓
- **Spec §4.5 /model/publish_dl 端点**:Task 6(镜像 promote·锁/状态/子进程·2 路由)。✓
- **Spec §4.6 前端发布按钮**:Task 7(PublishDlPanel 镜像 PromoteModelPanel·仅 lstm 渲染)。✓
- **Spec §4.7 选股侧零新前端**:不需任务(Spec 1 多源徽章已建)。✓
- **Spec §5 PIT/红线**:cutoff = eval_date−horizon(Task 4)→ lookahead:false(Task 8 Step3 验);serving 零推理(训练只在子进程);LGB≥0.5(MAX_TOTAL_DL_W 不动)。✓
- **Spec §6 测试 / §7 验证**:Task 1-3 纯函数/单元 TDD;Task 8 集成(CPU 训练→regen→live→UI)。✓
- **占位扫描**:无 TBD;纯函数(Task1-3)给完整测试+实现;torch 生产器(Task4)给完整代码 + 语法/import 冒烟(真训练性质上是集成验 Task8);端点/前端(Task6-7)给完整代码 + 现场对齐注记(工厂函数名/挂载行写法以 grep 实际为准 —— 黑盒接缝的合理对齐点,非占位)。✓
- **类型一致**:`build_sequences(panel,feature_cols,label_col,seq_len,cutoff)→(X,y,idx)`、`predict_index(panel,feature_cols,seq_len,eval_date)→(X,codes)`、`add_forward_return(panel,horizon,...)→panel`、`write_pred_rolling(...,train_cutoff=None)→df`、`train_and_predict(...)→dict` —— 跨任务签名一致,契约列 `eval_date/instrument/pred_ret_5d(+train_cutoff)` 与 Spec 1 `_load_dl_for_date` 一致。✓
- **不打架**:每 Task Step 5/提交前 `git branch --show-current` 确认 main(Global Constraints)。✓
