# 统一深度学习集成层(Spec 1:层 + UI)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单源 FinCast B3 集成泛化成一个统一的多源 DL 集成层,让任意 DL 模型按统一契约的预测表加权混进 v4 排名,并在选股界面显形其参与。

**Architecture:** 新 `compute/dl_ensemble.py`(纯 pandas:DL 源契约 + `dl_mix_scores` 多源 z 混合 + `apply_dl_ensemble` 编排 + 泛化 provenance),复用 `v4_fincast.py` 的 `_zscore/recent_fc_icir/_adaptive_w_fc`。`v4.py` 加 `dl_sources` 参数调新层(无则回退现有 `fincast_path` 单源);`regen.py` 建源注册表 + 写 `v4_dl_provenance.json`;`screen/api.py` 读新 provenance(回退旧档);前端徽章泛化成多源。**producer-agnostic**:层只读 `var/dl_pred_<model_id>.parquet`(FinCast 沿用现有 `var/v4_fincast_pred.parquet`)。

**Tech Stack:** Python 3.13、pandas/numpy、pytest;React/JSX(screen-app.jsx)。**前置参考**:`docs/superpowers/specs/2026-06-22-dl-ensemble-layer-design.md`。

**全局坑**:
- **并行会话占用工作树 + 9999**:实施前确认工作树归属。Task 6 改 `ui/screen/screen-app.jsx` 与并行会话冲突——须协调/隔离(独立 worktree)。
- **GateGuard**:每个文件首改先报 facts(① 谁 import ② 受影响公共符号 ③ 读写数据文件字段 ④ 用户指令逐字)。
- **字节等价是硬约束**:单源 FinCast 经新层必须与旧 `b3_mix_scores`/`apply_fincast_ensemble` 输出 allclose,否则破坏已验证的 v4 排名。
- **引擎/serving 改动须重启 9999**;pytest 从仓根 `G:/guanlan-v2` 跑;测试顶部 prepend 仓内 `engine/`。
- **从 main 分支开实现分支**(main 已含本 spec)。

---

### Task 1: `dl_mix_scores` 多源 z 混合(+ 字节等价守护)

**Files:**
- Create: `guanlan_v2/strategy/compute/dl_ensemble.py`
- Test: `tests/test_dl_ensemble.py`

- [ ] **Step 1: 写失败测试** —— 新建 `tests/test_dl_ensemble.py`:

```python
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
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v` → FAIL（ModuleNotFoundError: dl_ensemble）。

- [ ] **Step 3: 实现** —— 新建 `guanlan_v2/strategy/compute/dl_ensemble.py`:

```python
# -*- coding: utf-8 -*-
"""统一深度学习集成层(多源)—— 把单源 FinCast B3 泛化成「N 个 DL 源加权 z 混合进 v4 score」。

**命门**(同 v4_fincast):只 pd.read_parquet 离线产出的预测表,绝不在此/任何 HTTP 请求里跑模型。
LGB 恒 ≥0.5 主导(总 DL 权重封顶 MAX_TOTAL_DL_W)。复用 v4_fincast 的 z/ICIR/自适应权重 helpers。
单源时与 v4_fincast.b3_mix_scores 字节等价(回归守护)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.v4_fincast import (
    _zscore, recent_fc_icir, _adaptive_w_fc, DEFAULT_W_FC, MIN_MATCH,
)

MAX_TOTAL_DL_W = 0.5   # 总 DL 权重封顶 → w_lgb = 1 - Σwᵢ ≥ 0.5,LGB 主导


@dataclass
class DLSource:
    model_id: str
    path: str
    score_col: str = "pred_ret_5d"
    weight_mode: str = "adaptive"          # "adaptive"(按近期 ICIR)| "fixed"
    fixed_w: Optional[float] = None


def dl_mix_scores(score_lgb: pd.Series, dl_scores: dict, weights: dict,
                  min_match: int = MIN_MATCH) -> Tuple[pd.Series, dict]:
    """多源 z 混合:mixed = w_lgb·z(LGB) + Σ wᵢ·z(DLᵢ)。

    dl_scores: {model_id: Series};weights: {model_id: float(已 clip 好)}。
    每源 reindex 到 LGB 索引;非空 < min_match 或权重 ≤0 → 退出(weight=0)。
    活跃源总权重 > MAX_TOTAL_DL_W → 按比例缩到和为 MAX_TOTAL_DL_W。
    返回 (mixed, info{active, w_lgb, sources:[{model_id,active,weight,n_has,reason}]})。
    单源时与 b3_mix_scores 字节等价。"""
    src_info = []
    active = {}
    for mid, raw in dl_scores.items():
        s = raw.reindex(score_lgb.index)
        n_has = int(s.notna().sum())
        w_raw = float(weights.get(mid, 0.0))
        if n_has < min_match or w_raw <= 0:
            src_info.append({"model_id": mid, "active": False, "weight": 0.0, "n_has": n_has,
                             "reason": (f"匹配 {n_has} < {min_match},退出" if n_has < min_match else "权重 0")})
        else:
            active[mid] = (s, w_raw, n_has)
    total = sum(w for _, w, _ in active.values())
    scale = (MAX_TOTAL_DL_W / total) if total > MAX_TOTAL_DL_W else 1.0
    if not active:
        return score_lgb.copy(), {"active": False, "w_lgb": 1.0, "sources": src_info}
    w_lgb = 1.0 - sum(w * scale for _, w, _ in active.values())
    mixed = w_lgb * _zscore(score_lgb)
    for mid, (s, w_raw, n_has) in active.items():
        w = w_raw * scale
        mixed = mixed + w * _zscore(s.fillna(s.mean()))
        src_info.append({"model_id": mid, "active": True, "weight": w, "n_has": n_has,
                         "reason": f"w={w:.3f}({n_has} 只匹配)"})
    return mixed, {"active": True, "w_lgb": w_lgb, "sources": src_info}
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v` → PASS（5 passed）。

- [ ] **Step 5: 提交**

```bash
git add tests/test_dl_ensemble.py guanlan_v2/strategy/compute/dl_ensemble.py
git commit -m "feat(dl-ensemble): dl_mix_scores 多源z混合(封顶0.5·per-source退化·单源与b3字节等价)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `_load_dl_for_date` + `default_dl_sources` + `apply_dl_ensemble`

**Files:**
- Modify: `guanlan_v2/strategy/compute/dl_ensemble.py`
- Test: `tests/test_dl_ensemble.py`(追加)

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_dl_ensemble.py` 末尾追加:

```python
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
    # 旧:apply_fincast_ensemble 无 data → DEFAULT_W_FC=0.4
    apply_fincast_ensemble(p1, pd.Timestamp("2026-03-10"), path)
    # 新:fixed_w=DEFAULT_W_FC 对齐
    apply_dl_ensemble(p2, pd.Timestamp("2026-03-10"),
                      [DLSource(model_id="fincast", path=path, weight_mode="fixed", fixed_w=DEFAULT_W_FC)])
    assert np.allclose(p1["score"].values, p2["score"].values, atol=1e-12)
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -k apply -v` → FAIL（ImportError: apply_dl_ensemble）。

- [ ] **Step 3: 实现** —— 在 `dl_ensemble.py` 末尾追加:

```python
def _load_dl_for_date(path: str, ld: pd.Timestamp, score_col: str = "pred_ret_5d"):
    """读 DL 预测 parquet → (当日 series[instrument→score], 全表 df, train_cutoff, reason_if_fail)。
    泛化 v4_fincast._load_fincast_for_date(列名 score_col 参数化)。缺文件/缺列/无当日/读失败 → None+reason。"""
    if not path or not os.path.exists(path):
        return None, None, None, "预测文件不存在,退出(离线产出:见 scripts/sync_fincast.py 同款工具)"
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        return None, None, None, f"预测 parquet 读取失败({type(e).__name__}),退出"
    need = {"eval_date", "instrument", score_col}
    if not need.issubset(df.columns):
        try:
            df = df.reset_index()
        except Exception:  # noqa: BLE001
            pass
    if not need.issubset(df.columns):
        return None, None, None, f"预测 parquet 缺 {need} 列,退出"
    cutoff = None
    if "train_cutoff" in df.columns and len(df):
        try:
            cutoff = str(pd.Timestamp(df["train_cutoff"].iloc[0]).date())
        except Exception:  # noqa: BLE001
            cutoff = None
    ev = pd.to_datetime(df["eval_date"]).dt.normalize()
    today = pd.Timestamp(ld).normalize()
    sub = df[ev == today]
    if sub.empty:
        return None, df, cutoff, f"无 {today.date()} 预测,退出"
    s = sub.set_index("instrument")[score_col]
    s = s[~s.index.duplicated(keep="last")]
    return s, df, cutoff, None


def default_dl_sources() -> list:
    """Phase 1 默认 DL 源注册表:仅 FinCast(沿用现有 var/v4_fincast_pred.parquet)。
    Phase 2/3 加 LSTM 等:在此 append 一个 DLSource(指向 var/dl_pred_<model_id>.parquet)即接入。"""
    from pathlib import Path
    var = Path(__file__).resolve().parents[3] / "var"
    return [
        DLSource(model_id="fincast", path=str(var / "v4_fincast_pred.parquet"),
                 score_col="pred_ret_5d", weight_mode="adaptive"),
        # Phase 2: DLSource(model_id="lstm", path=str(var / "dl_pred_lstm.parquet"), ...)
    ]


def apply_dl_ensemble(pred: pd.DataFrame, ld: pd.Timestamp, sources: list,
                      data: Optional[pd.DataFrame] = None, min_match: int = MIN_MATCH) -> dict:
    """对 build_v4 末日截面 pred(MultiIndex (instrument, datetime),含 'score')就地多源混合。
    只读每个源的 parquet;有效源加权 z 混合写回 pred['score'];无则诚实退纯 LGB。返回 provenance。"""
    info = {"date": str(pd.Timestamp(ld).date()), "active": False, "w_lgb": 1.0,
            "sources": [], "reason": None}
    inst = pred.index.get_level_values("instrument")
    lgb_by_inst = pd.Series(pred["score"].values, index=inst)
    dl_scores, weights, meta, missing = {}, {}, {}, []
    for src in sources:
        s, df, cutoff, fail = _load_dl_for_date(src.path, ld, src.score_col)
        if fail is not None:
            missing.append({"model_id": src.model_id, "active": False, "weight": 0.0,
                            "n_has": 0, "lookahead": None, "reason": fail})
            continue
        if src.weight_mode == "fixed" and src.fixed_w is not None:
            w, icir = float(src.fixed_w), None
        else:
            icir = None
            if data is not None and df is not None and "label" in getattr(data, "columns", []):
                icir = recent_fc_icir(df, data["label"], ld)
            w = _adaptive_w_fc(icir)
        look = (str(pd.Timestamp(ld).date()) <= cutoff) if cutoff is not None else None
        dl_scores[src.model_id] = s
        weights[src.model_id] = w
        meta[src.model_id] = {"lookahead": look, "fc_icir_recent": icir}
    if not dl_scores:
        info["sources"] = missing
        info["reason"] = "无可用 DL 源(全部缺文件/无当日预测),纯 LGB"
        return info
    mixed, mix = dl_mix_scores(lgb_by_inst, dl_scores, weights, min_match=min_match)
    for s in mix["sources"]:
        m = meta.get(s["model_id"], {})
        s["lookahead"] = m.get("lookahead")
        s["fc_icir_recent"] = m.get("fc_icir_recent")
    info["sources"] = mix["sources"] + missing
    info["w_lgb"] = mix["w_lgb"]
    info["active"] = mix["active"]
    if mix["active"]:
        pred["score"] = mixed.reindex(inst).values
        info["reason"] = ("DL 集成:LGB %.2f + " % mix["w_lgb"]) + "、".join(
            f"{s['model_id']} {s['weight']:.2f}" for s in mix["sources"] if s.get("active"))
    else:
        info["reason"] = "所有 DL 源退化,纯 LGB"
    return info
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v` → PASS（8 passed）。

- [ ] **Step 5: 提交**

```bash
git add tests/test_dl_ensemble.py guanlan_v2/strategy/compute/dl_ensemble.py
git commit -m "feat(dl-ensemble): _load_dl_for_date + default_dl_sources + apply_dl_ensemble 编排

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `v4.py` build_v4 接 `dl_sources`(回退 fincast_path)

**Files:**
- Modify: `guanlan_v2/strategy/compute/v4.py`(签名 :229;B3 块 :279-288)
- Test: `tests/test_dl_ensemble.py`(追加契约测试;真 build_v4 走真数据见 Task 7)

- [ ] **Step 1: 写失败测试** —— 追加(只验「dl_sources 进了签名」的接线):

```python
def test_build_v4_signature_has_dl_sources():
    import inspect
    from guanlan_v2.strategy.compute import v4
    sig = inspect.signature(v4.build_v4)
    assert "dl_sources" in sig.parameters
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py::test_build_v4_signature_has_dl_sources -v` → FAIL（'dl_sources' not in params）。

- [ ] **Step 3: 实现** —— 改 `v4.py`:

(a) 签名(:229-232)加 `dl_sources`:

```python
             fincast_path: Optional[str] = None, b3: Optional[dict] = None,
             dl_sources: Optional[list] = None,
             feature_cols: Optional[List[str]] = None,
```

(b) B3 块(:279-288)替换为「优先 dl_sources(多源新层)否则回退 fincast_path(单源旧路)」:

```python
    if dl_sources:
        try:
            from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble
            _b3info = apply_dl_ensemble(pred, ld, dl_sources, data=data)
            if b3 is not None:
                b3.update(_b3info)
            print(f"[v4] DL集成: {_b3info.get('reason')}", flush=True)
        except Exception as _e:  # noqa: BLE001 — DL 集成异常绝不拖垮排名,退纯 LGB
            if b3 is not None:
                b3.update({"active": False, "reason": f"DL 集成异常退纯 LGB:{type(_e).__name__}: {_e}"})
    elif fincast_path:
        try:
            from guanlan_v2.strategy.compute.v4_fincast import apply_fincast_ensemble
            _b3info = apply_fincast_ensemble(pred, ld, fincast_path, data=data)
            if b3 is not None:
                b3.update(_b3info)
            print(f"[v4] B3: {_b3info.get('reason')}", flush=True)
        except Exception as _e:  # noqa: BLE001
            if b3 is not None:
                b3.update({"active": False, "reason": f"B3 异常退纯 LGB:{type(_e).__name__}: {_e}"})
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v` → PASS（9 passed）。

- [ ] **Step 5: 提交**

```bash
git add tests/test_dl_ensemble.py guanlan_v2/strategy/compute/v4.py
git commit -m "feat(dl-ensemble): build_v4 接 dl_sources 调多源层(回退 fincast_path 向后兼容)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `regen.py` 建源注册表 + 写 `v4_dl_provenance.json`

**Files:**
- Modify: `guanlan_v2/strategy/compute/regen.py`(:177-194)

- [ ] **Step 1: 实现**(regen 是离线编排,无单测;改完用 Step 2 冒烟)。把 `regen.py:177-194` 的 FinCast 块替换为多源 DL 集成:

```python
        # DL 集成层:多源(Phase 1 仅 FinCast,沿用 var/v4_fincast_pred.parquet)。离线只读预测表混进 v4 score,
        #   无则诚实退纯 LGB(字节等价旧行为)。provenance 落 v4_dl_provenance.json 供 serving/UI 诚实徽章。
        from guanlan_v2.strategy.compute.dl_ensemble import default_dl_sources
        _b3: dict = {}
        _dl_sources = default_dl_sources()
        v4out = build_v4(provider_uri, end=end, codes=codes, date_str=end,
                         health=_health, dl_sources=_dl_sources, b3=_b3)
        _write_atomic(v4out, V4_RANKING_PARQUET, index=False)
        out["v4"] = (len(v4out), str(V4_RANKING_PARQUET))
        out["v4_dl"] = dict(_b3) if _b3 else {"active": False, "reason": "未启用"}
        try:   # DL provenance 旁路落盘(serving 读它判 纯 LGB vs 多源混合 + 每源 look-ahead)
            import json as _json
            _dl_side = V4_RANKING_PARQUET.parent / "v4_dl_provenance.json"
            _dl_side.write_text(_json.dumps({"date": end, **(_b3 or {})}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as _e:  # noqa: BLE001 — provenance 落盘失败不阻断再生
            print(f"  [warn] v4_dl provenance 落盘失败: {type(_e).__name__}: {_e}", flush=True)
        _act = _b3.get("active")
        print(f"  v4 {len(v4out)} 行 (顶200 v4_total notnull={int(v4out['v4_total'].notna().sum())}) "
              f"· DL {('混合 ' + _b3.get('reason', '')) if _act else '纯 LGB'} -> {V4_RANKING_PARQUET}", flush=True)
```

- [ ] **Step 2: 冒烟确认 import/语法**

Run: `cd G:/guanlan-v2 && python -c "import sys; sys.path.insert(0,'engine'); import guanlan_v2.strategy.compute.regen as r; print('regen import ok')"`
Expected: `regen import ok`(无语法/import 错;真 regen 跑见 Task 7）。

- [ ] **Step 3: 提交**

```bash
git add guanlan_v2/strategy/compute/regen.py
git commit -m "feat(dl-ensemble): regen 建 default_dl_sources + 写 v4_dl_provenance.json

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `screen/api.py` 读 `v4_dl_provenance.json`(回退旧档)

**Files:**
- Modify: `guanlan_v2/screen/api.py`(:846-861)

- [ ] **Step 1: 实现** —— 把 `screen/api.py:848-854` 的 provenance 读取替换为「优先新档,回退旧档」:

```python
    try:
        import json as _json
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET as _V4P
        _dlp = _V4P.parent / "v4_dl_provenance.json"
        if _dlp.exists():
            _b3prov = _json.loads(_dlp.read_text(encoding="utf-8"))
        else:   # 回退旧单源 FinCast provenance(过渡期 / regen 未重跑)
            _b3p = _V4P.parent / "v4_b3_provenance.json"
            _b3prov = _json.loads(_b3p.read_text(encoding="utf-8")) if _b3p.exists() else None
    except Exception:  # noqa: BLE001
        _b3prov = None
```

（`return JSONResponse({... "v4_provenance": _b3prov, ...})` 那行不动:键名仍 `v4_provenance`,值现在是多源结构 `{active, w_lgb, sources:[...]}` 或回退旧结构。）

- [ ] **Step 2: 冒烟确认语法**

Run: `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('guanlan_v2/screen/api.py',encoding='utf-8').read()); print('screen/api.py 语法 ok')"`
Expected: `screen/api.py 语法 ok`。

- [ ] **Step 3: 提交**

```bash
git add guanlan_v2/screen/api.py
git commit -m "feat(dl-ensemble): screen 读 v4_dl_provenance.json(多源·回退旧 b3 档)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 前端多源徽章(`screen-app.jsx`)⚠ 与并行会话冲突,先协调

**Files:**
- Modify: `ui/screen/screen-app.jsx`(:523-538 的 `!isVariant && result.v4_provenance` 徽章)

> **冲突警示**:`ui/screen/screen-app.jsx` 正被并行会话编辑。实施前确认工作树归属;若并行活动仍在,**隔离 worktree 或等其落定**再做本任务。先 Read 当前文件确认徽章块真实行号/内容。

- [ ] **Step 1: 实现** —— 把现有单 FinCast 徽章块(`!isVariant && result.v4_provenance` 那段,约 :523-538)替换为「认多源 `sources[]`(新)+ 回退旧单源 `w_fc`」:

```jsx
          {!isVariant && result.v4_provenance && (() => {
            const p = result.v4_provenance;   // 多源 {active,w_lgb,sources:[...]} 或旧单源 {active,w_fc,...}
            // 新多源 provenance
            if (Array.isArray(p.sources)) {
              const act = p.sources.filter(s => s.active);
              if (!act.length) {
                const why = (p.sources[0] && p.sources[0].reason) || '无当日 DL 预测';
                return <span className="mono" title={'排名口径:纯 LGB(' + why + ')。混入 DL 需离线产出当日预测 parquet。'}
                  style={{ fontSize: 10, color: 'var(--ink-3)', border: '1px dashed var(--line)', borderRadius: 5, padding: '2px 7px' }}>v4 · 纯 LGB</span>;
              }
              const anyLa = act.some(s => s.lookahead === true);
              const tip = '排名口径:LGB + DL 多源混合 · w_LGB=' + (+p.w_lgb).toFixed(2)
                + act.map(s => ' + ' + s.model_id + ' w=' + (+s.weight).toFixed(2)
                    + '(' + s.n_has + ' 只匹配'
                    + (s.fc_icir_recent != null ? '·ICIR ' + (+s.fc_icir_recent).toFixed(3) : '')
                    + (s.lookahead === true ? '·⚠前视' : '') + ')').join('')
                + (anyLa ? ' · ⚠ 含模型 look-ahead' : '');
              return <span className="mono" title={tip}
                style={{ fontSize: 10, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>
                v4 · LGB+{act.map(s => s.model_id + '(' + (+s.weight).toFixed(2) + ')').join('+')}{anyLa ? ' ⚠前视' : ''}</span>;
            }
            // 回退:旧单源 FinCast provenance
            const la = p.lookahead === true;
            if (p.active) {
              const tip = '排名口径:LGB + FinCast 混合(B3 集成)· w_LGB=' + (+p.w_lgb).toFixed(2) + ' + w_FC=' + (+p.w_fc).toFixed(2)
                + ' · ' + p.n_has_fc + '/' + p.n_total + ' 只匹配 FinCast 预测'
                + (la ? ' · ⚠ 该日含模型 look-ahead' : '');
              return <span className="mono" title={tip}
                style={{ fontSize: 10, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>
                v4 · LGB+FinCast w<sub style={{ fontSize: 7 }}>FC</sub>={(+p.w_fc).toFixed(2)}{la ? ' ⚠前视' : ''}</span>;
            }
            return <span className="mono" title={'排名口径:纯 LGB(' + (p.reason || '无当日 FinCast 预测') + ')'}
              style={{ fontSize: 10, color: 'var(--ink-3)', border: '1px dashed var(--line)', borderRadius: 5, padding: '2px 7px' }}>v4 · 纯 LGB</span>;
          })()}
```

- [ ] **Step 2: 验证** —— bump `screen-app.jsx` 的 `?v=` 缓存戳(若该文件用查询戳),浏览器开选股页确认徽章渲染(多源活跃→`v4 · LGB+fincast(0.x)`;无→`v4 · 纯 LGB`);0 JSX 解析错。(真机见 Task 7。)

- [ ] **Step 3: 提交**

```bash
git add "ui/screen/screen-app.jsx"
git commit -m "feat(dl-ensemble): 选股徽章泛化多源 DL 显形(回退旧单源 FinCast)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 真数据验证(重启 9999 + regen + 浏览器 + 字节等价回归)

**Files:** 无(验证)。

- [ ] **Step 1: 全量回归(字节等价硬约束)** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py tests/test_v4_fincast.py -v`
Expected: dl_ensemble 9 passed + 旧 test_v4_fincast 全 passed(FinCast 适配器不破)。

- [ ] **Step 2: 重启 9999** —— PowerShell:
```powershell
Get-NetTCPConnection -LocalPort 9999 -State Listen | Select-Object -Expand OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```
等看门狗 ~10s 拉起;`Invoke-WebRequest http://127.0.0.1:9999/factor/catalog -UseBasicParsing | Select StatusCode`(期望 200)。

- [ ] **Step 3: regen 产 v4_dl_provenance.json** —— 跑项目既有 regen(或点「拉取最新数据」);确认 `var/v4_dl_provenance.json` 产出,结构 `{date, active, w_lgb, sources:[...]}`。FinCast 有当日预测→active:true、fincast 源 weight>0;无→active:false 诚实「纯 LGB」(字节等价旧 v4 排名)。
  - **FinCast 复活(验证用,有 GPU 依赖)**:conda `stocks` 跑 `fincast_daily_predict.py --date <total_mv 覆盖日>` → `python scripts/sync_fincast.py` → regen。GPU 不可用则用现有(可能陈旧)预测表,active 取决于是否有当日预测。

- [ ] **Step 4: live /screen 验 provenance** —— 浏览器选股页 / `Invoke-WebRequest`:确认响应 `v4_provenance` 为多源结构;前端徽章渲染(`v4 · LGB+fincast(0.x)` 或 `v4 · 纯 LGB`),title 显每源 weight/ICIR/前视。

- [ ] **Step 5: 旧路径回归** —— 确认无 dl_sources/无预测时 v4 排名与纯 LGB **字节等价**(`v4_ranking_latest.parquet` 内容不因本改动变化)。

---

## Self-Review(已对 spec 核对)

- **Spec §4.1 dl_ensemble**:Task 1(dl_mix_scores)+ Task 2(_load_dl_for_date/default_dl_sources/apply_dl_ensemble)。✓
- **Spec §4.3 v4.py**:Task 3(build_v4 加 dl_sources,回退 fincast_path)。✓
- **Spec §4.4 regen**:Task 4(default_dl_sources + 写 v4_dl_provenance.json)。✓
- **Spec §4.5 screen**:Task 5(读新档回退旧档)。✓
- **Spec §4.6 前端**:Task 6(多源徽章,回退旧单源)。✓
- **Spec §6 权重封顶/§7 provenance**:Task 1(封顶+w_lgb)/ Task 2(provenance sources[])。✓
- **Spec §9 红线/§10 测试/§11 验证**:Task 1-2 测字节等价+退化、Task 7 回归+真数据。✓
- **占位扫描**:无 TBD;每代码步给完整代码;regen/screen 无单测的部分给冒烟命令(离线编排/路由读取的合理验证)。
- **类型一致**:`DLSource`/`dl_mix_scores`/`apply_dl_ensemble`/`default_dl_sources` 跨任务签名一致;`dl_mix_scores` 单源与 `b3_mix_scores` 字节等价(Task1 测)、`apply_dl_ensemble` 单 fincast 源与 `apply_fincast_ensemble` 字节等价(Task2 测)——双重守护 spec 硬约束。
- **范围**:仅 Phase 1(层+UI),producer-agnostic;FinCast 生成港进 guanlan = Spec 2(范围外)。✓
