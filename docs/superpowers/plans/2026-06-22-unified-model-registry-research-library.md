# 统一模型注册表 + 研究库双向通道 Implementation Plan(②)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"工作流训的模型"和"工坊训的 v4 变体"统一进同一个 `model_registry`(排名产物契约 + provenance),工作流模型可「存入模型库」后在工坊/`/screen` 选股,工坊变体出现在工作流研究库。

**Architecture:** 统一到排名契约 `v4_ranking.parquet(code/date/lgb_pct)`;`model_registry` meta 加 `source/kind/recipe/retrainable`;新 `compute/model_workflow.py` 把工作流小规模训练器升到生产规模(全市场+全窗口)出排名;`POST /model/promote` 异步子进程(镜像 `/screen/model/train`);前端「因子库」模态扩成「研究库」(因子 tab + 模型 tab)+ 新 `model` 节点 + 「存入模型库」按钮。首期树模型(v4/lgbm/xgb/rf)。

**Tech Stack:** Python 3.13 / FastAPI / pandas / LightGBM·XGBoost·sklearn(已装,引擎 fork 路径)/ React(JSX,`ui/`)/ pytest。

**Spec:** `docs/superpowers/specs/2026-06-22-unified-model-registry-research-library-design.md`

**红线:** prod 只读零改;入库前校验排名形状(诚实失败不冒充可选股模型);不碰 `/screen` 选股算法;promote 失败显形不留半成品;沿用 9999 看门狗。

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `guanlan_v2/screen/model_registry.py` | 注册表:provenance 默认值 + 排名契约校验 + 读写归一 | 修改 |
| `guanlan_v2/strategy/compute/model_workflow.py` | 工作流生产训练器(全市场全窗口树模型→排名)+ 子进程入口 | **新建** |
| `guanlan_v2/workflow/api.py` | `POST /model/promote` + `GET /model/promote/status`(异步状态机) | 修改 |
| `guanlan_v2/screen/api.py` | `/models` 已回 `list_variants()`,Phase A 后自动带 provenance;加 `GET /model/ranking`(model 节点用) | 修改/测试 |
| `ui/factor/workflow.jsx` | 因子库模态→研究库(模型 tab)+ `model` 节点 + 「存入模型库」按钮 | 修改 |
| `ui/screen/screen-app.jsx` | 变体列表 source 徽章 | 修改 |
| `tests/test_model_registry_provenance.py` | A 单测 | 新建 |
| `tests/test_model_workflow_promote.py` | B/C/D 单测 | 新建 |

---

# Phase A — 注册表泛化(provenance + 排名契约)

### Task A1:provenance 默认值 + 读写归一

**Files:**
- Modify: `guanlan_v2/screen/model_registry.py`
- Test: `tests/test_model_registry_provenance.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_model_registry_provenance.py
import pandas as pd
import pytest
from guanlan_v2.screen import model_registry as reg


def _ranking_df():
    # 200 票的最小合格排名(code/date/lgb_pct)
    codes = [f"SZ{300000 + i:06d}" for i in range(200)]
    return pd.DataFrame({"code": codes, "date": "2026-06-19",
                         "lgb_pct": [i / 199 for i in range(200)]})


def test_save_fills_provenance_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    reg.save_variant("m_test1", _ranking_df(), {"id": "m_test1", "name": "变体1"})
    m = reg.variant_meta("m_test1")
    assert m["source"] == "workshop"        # 缺省兜底
    assert m["kind"] == "v4-lgb"
    assert m["retrainable"] is False        # 无 recipe → 不可重训
    assert m["recipe"] == {}


def test_save_keeps_explicit_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    reg.save_variant("m_wf1", _ranking_df(),
                     {"id": "m_wf1", "name": "工作流模型", "source": "workflow",
                      "kind": "lightgbm", "recipe": {"features": ["close/Ref(close,5)"]},
                      "retrainable": True})
    m = reg.variant_meta("m_wf1")
    assert m["source"] == "workflow"
    assert m["kind"] == "lightgbm"
    assert m["retrainable"] is True


def test_list_variants_normalizes_old_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    # 模拟老 meta(无 provenance 字段)直接落盘
    d = tmp_path / "m_old"; d.mkdir(parents=True)
    (d / "v4_ranking.parquet").write_bytes(b"")  # 占位,不被 list 读
    (d / "meta.json").write_text('{"id":"m_old","name":"老变体","oos_ic":0.01}',
                                 encoding="utf-8")
    rows = reg.list_variants()
    old = [r for r in rows if r["id"] == "m_old"][0]
    assert old["source"] == "workshop" and old["kind"] == "v4-lgb"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_registry_provenance.py -v`
Expected: FAIL(`KeyError: 'source'` —— 当前 meta 无 provenance)

- [ ] **Step 3: 实现 provenance 归一**

在 `guanlan_v2/screen/model_registry.py` 顶部常量后加:

```python
RANKING_FILE = "v4_ranking.parquet"   # 排名契约文件名(沿用,保 loader/prod 后兼容)
_PROVENANCE_DEFAULTS = {"source": "workshop", "kind": "v4-lgb",
                        "recipe": {}, "retrainable": False}


def _normalize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """补 provenance 缺省(老 meta / 缺字段兜底);不覆盖已有值。"""
    out = dict(meta or {})
    for k, v in _PROVENANCE_DEFAULTS.items():
        out.setdefault(k, v.copy() if isinstance(v, dict) else v)
    return out
```

把 `variant_meta` 改为读后归一:

```python
def variant_meta(vid) -> Dict[str, Any]:
    p = _dir(vid) / "meta.json"
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _normalize_meta(raw) if raw else {}
```

把 `save_variant` 改为写前归一(并用 `RANKING_FILE` 常量):

```python
def variant_ranking_path(vid): return _dir(vid) / RANKING_FILE


def save_variant(vid, ranking_df, meta) -> None:
    d = _dir(vid); d.mkdir(parents=True, exist_ok=True)
    pq = variant_ranking_path(vid); tmp = str(pq) + ".tmp"
    ranking_df.to_parquet(tmp, index=False); os.replace(tmp, str(pq))
    mp = d / "meta.json"; mtmp = str(mp) + ".tmp"
    with open(mtmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(_normalize_meta(meta), ensure_ascii=False, indent=1))
    os.replace(mtmp, str(mp))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_registry_provenance.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/model_registry.py tests/test_model_registry_provenance.py
git commit -m "feat(registry): meta provenance(source/kind/recipe/retrainable) 默认值兜底+读写归一"
```

---

### Task A2:排名契约校验(入库前)

**Files:**
- Modify: `guanlan_v2/screen/model_registry.py`
- Test: `tests/test_model_registry_provenance.py`

- [ ] **Step 1: 写失败测试(追加)**

```python
def test_validate_ranking_rejects_missing_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    bad = pd.DataFrame({"code": ["SZ300001"], "date": ["2026-06-19"]})  # 缺 lgb_pct
    with pytest.raises(ValueError, match="lgb_pct"):
        reg.save_variant("m_bad", bad, {"id": "m_bad", "name": "坏"})


def test_validate_ranking_rejects_thin_cross_section(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    thin = pd.DataFrame({"code": ["SZ300001", "SZ300002"], "date": "2026-06-19",
                         "lgb_pct": [0.1, 0.9]})  # 截面仅 2 票 < 阈值
    with pytest.raises(ValueError, match="截面"):
        reg.save_variant("m_thin", thin, {"id": "m_thin", "name": "薄"})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_registry_provenance.py -k validate -v`
Expected: FAIL(当前 save_variant 不校验,无 ValueError)

- [ ] **Step 3: 实现校验**

在 `model_registry.py` 加(放在 `save_variant` 上方):

```python
MIN_CROSS_SECTION = 100      # 最新截面最少票数(沿用 model_health 的 <100 诚实缺席阈值)
_RANKING_REQUIRED = ("code", "date", "lgb_pct")


def validate_ranking(df) -> None:
    """入库前校验排名契约;不合格抛 ValueError(诚实失败,不冒充可选股模型)。"""
    if df is None or not hasattr(df, "columns"):
        raise ValueError("ranking 非 DataFrame")
    missing = [c for c in _RANKING_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"ranking 缺列: {missing}(必含 lgb_pct)")
    last = df[df["date"] == df["date"].max()]
    if int(last["code"].nunique()) < MIN_CROSS_SECTION:
        raise ValueError(f"最新截面票数 {last['code'].nunique()} < {MIN_CROSS_SECTION}(截面太薄)")
```

在 `save_variant` 写盘前调用(函数第一行):

```python
def save_variant(vid, ranking_df, meta) -> None:
    validate_ranking(ranking_df)
    d = _dir(vid); d.mkdir(parents=True, exist_ok=True)
    ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_registry_provenance.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/model_registry.py tests/test_model_registry_provenance.py
git commit -m "feat(registry): 入库前校验排名契约(code/date/lgb_pct+截面厚度),诚实失败"
```

---

# Phase B — 工作流生产训练器

### Task B1:`train_promote` 出全截面排名 + 入库

**Files:**
- Create: `guanlan_v2/strategy/compute/model_workflow.py`
- Test: `tests/test_model_workflow_promote.py`

- [ ] **Step 1: 写失败测试(用小股池守速度;真引擎数据,标 slow)**

```python
# tests/test_model_workflow_promote.py
import pandas as pd
import pytest
from guanlan_v2.screen import model_registry as reg


@pytest.mark.slow
def test_train_promote_produces_ranking_and_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    from guanlan_v2.strategy.compute import model_workflow as mw
    spec = {
        "variant_id": "m_wf_test", "name": "工作流lgbm测试", "kind": "lightgbm",
        "recipe": {
            "features": ["close/Ref(close,20)-1", "(close-Ref(close,5))/Ref(close,5)"],
            "label": "fwd_ret", "fwd_days": 5,
            "universe": "csi300",          # 测试用 csi300(够厚 ≥100),非 all(守时间)
            "start": "2024-01-01", "params": {"leaves": 31, "lr": 0.05},
        },
        "created": "2026-06-22T00:00:00",
    }
    out = mw.train_promote(spec)
    assert out["ok"] is True
    m = reg.variant_meta("m_wf_test")
    assert m["source"] == "workflow" and m["kind"] == "lightgbm"
    assert m["retrainable"] is True and m["recipe"]["features"]
    rank = pd.read_parquet(reg.variant_ranking_path("m_wf_test"))
    assert set(["code", "date", "lgb_pct"]).issubset(rank.columns)
    assert rank["code"].nunique() >= 100           # 最新截面够厚
    assert rank["lgb_pct"].between(0, 1).all()      # 分位归一
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_workflow_promote.py -k produces -v`
Expected: FAIL(`ModuleNotFoundError: model_workflow`)

- [ ] **Step 3: 实现生产训练器**

```python
# guanlan_v2/strategy/compute/model_workflow.py
"""工作流模型「存入模型库」生产训练器:把工作流小规模训练器(workflow.api._materialize_xy
+ _build_model)升到生产规模(全市场/全窗口·树模型)→ 出全截面每日排名 → 入 model_registry。
首期 kind ∈ {lightgbm, xgboost, rf}(v4-lgb 走老 model_train,不在此)。不碰 /screen 选股算法。"""
from __future__ import annotations

from typing import Any, Dict


_TREE_KINDS = ("lightgbm", "xgboost", "rf")


def train_promote(spec: Dict[str, Any]) -> Dict[str, Any]:
    """spec={variant_id,name,kind,recipe:{features,label,fwd_days,universe,start,end,params},created}
    → 全窗口 fit → 最新截面预测 → lgb_pct 分位排名 → save_variant(source=workflow)。
    失败返回 {ok:False, reason}(不入库,诚实)。"""
    import pandas as pd
    from guanlan_v2.screen import model_registry as reg
    from guanlan_v2.workflow.api import ModelTrainIn, _materialize_xy, _build_model

    kind = str(spec.get("kind") or "").strip()
    if kind not in _TREE_KINDS:
        return {"ok": False, "reason": f"kind '{kind}' 暂不支持生产入库(首期树模型 {_TREE_KINDS})"}
    recipe = dict(spec.get("recipe") or {})
    feats = list(recipe.get("features") or [])
    if not feats:
        return {"ok": False, "reason": "recipe.features 为空"}

    body = ModelTrainIn(
        kind=kind, features=feats, label=recipe.get("label") or "fwd_ret",
        fwd_days=int(recipe.get("fwd_days") or 5),
        universe=str(recipe.get("universe") or "all"),
        start=recipe.get("start") or "2022-01-01", end=recipe.get("end"),
        params=dict(recipe.get("params") or {}), winsorize=True, standardize=True,
    )
    if not body.end:   # 缺 end → 引擎最新交易日(同 model_train 口径)
        from guanlan_v2.strategy.compute.regen import _latest_trade_date
        from guanlan_v2.strategy.compute.model_train import DEFAULT_PROVIDER
        body.end = _latest_trade_date(DEFAULT_PROVIDER)

    mat = _materialize_xy(body, body.universe, feats, body.start, body.end)
    if not isinstance(mat, tuple):     # _materialize_xy 失败时返回 JSONResponse
        return {"ok": False, "reason": "materialize 失败(universe/特征/标签求值)"}
    panel, fe_df, label_s, feature_names = mat

    X = fe_df.dropna()
    y = label_s.reindex(X.index).dropna()
    X = X.reindex(y.index)
    if len(X) < 500:
        return {"ok": False, "reason": f"训练样本太少({len(X)})"}

    model, hyper = _build_model(kind, body.params)
    model.fit(X.values, y.values)

    # —— 最新截面预测 → 分位排名 ——
    dts = fe_df.index.get_level_values("datetime")
    last = dts.max()
    x_last = fe_df[dts == last].dropna()
    if x_last.empty:
        return {"ok": False, "reason": "最新截面无可预测样本"}
    pred = pd.Series(model.predict(x_last.values), index=x_last.index)
    codes = x_last.index.get_level_values("code") if "code" in x_last.index.names \
        else x_last.index.get_level_values(-1)
    rank_df = pd.DataFrame({
        "code": [str(c) for c in codes],
        "date": pd.Timestamp(last).date().isoformat(),
        "lgb_pct": pred.rank(pct=True).values,
    })

    oos_ic = _oos_rank_ic(model, fe_df, label_s, frac=0.2)
    meta = {
        "id": spec["variant_id"], "name": spec.get("name") or "工作流模型",
        "source": "workflow", "kind": kind, "recipe": recipe, "retrainable": True,
        "oos_ic": oos_ic, "n_features": len(feature_names),
        "universe": body.universe, "asof": rank_df["date"].iloc[0],
        "created": spec.get("created") or "", "hyper": hyper,
    }
    reg.save_variant(spec["variant_id"], rank_df, meta)   # 内含 validate_ranking
    return {"ok": True, "variant_id": spec["variant_id"], "oos_ic": oos_ic}


def _oos_rank_ic(model, fe_df, label_s, frac: float = 0.2):
    """末 frac 调仓日做 OOS:逐日 spearman(pred, fwd) 取均值。失败/无样本 → None(诚实)。"""
    import numpy as np, pandas as pd
    try:
        dts = pd.DatetimeIndex(sorted(set(fe_df.index.get_level_values("datetime"))))
        if len(dts) < 10:
            return None
        cut = dts[int(len(dts) * (1 - frac))]
        ics = []
        for d in dts[dts >= cut]:
            xi = fe_df[fe_df.index.get_level_values("datetime") == d].dropna()
            yi = label_s.reindex(xi.index).dropna()
            xi = xi.reindex(yi.index)
            if len(xi) < 30:
                continue
            p = pd.Series(model.predict(xi.values), index=xi.index)
            ic = p.rank().corr(yi.rank())
            if np.isfinite(ic):
                ics.append(float(ic))
        return round(float(np.mean(ics)), 4) if ics else None
    except Exception:  # noqa: BLE001
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_workflow_promote.py -k produces -v -m slow`
Expected: PASS(真 LGB 在 csi300 上训出排名并入库)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/strategy/compute/model_workflow.py tests/test_model_workflow_promote.py
git commit -m "feat(model_workflow): 工作流模型生产训练器(全窗口树模型→最新截面lgb_pct排名→入库)"
```

---

### Task B2:子进程入口(`__main__`)

**Files:**
- Modify: `guanlan_v2/strategy/compute/model_workflow.py`

- [ ] **Step 1: 加子进程入口(无独立单测,C 阶段端到端覆盖)**

在 `model_workflow.py` 末尾加:

```python
if __name__ == "__main__":   # python -m guanlan_v2.strategy.compute.model_workflow <spec.json>
    import json, sys
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[model_promote] variant={spec['variant_id']} kind={spec.get('kind')} ...", flush=True)
    r = train_promote(spec)
    print(f"[model_promote] done ok={r.get('ok')} oos_ic={r.get('oos_ic')} reason={r.get('reason')}",
          flush=True)
    sys.exit(0 if r.get("ok") else 1)     # 失败非零退出码(供父进程状态机判 ok)
```

- [ ] **Step 2: 手动冒烟(可选)**

Run: `printf '%s' '{"variant_id":"m_smoke","name":"x","kind":"lightgbm","recipe":{"features":["close/Ref(close,20)-1"],"universe":"csi300","start":"2024-06-01"}}' > /tmp/sp.json && python -m guanlan_v2.strategy.compute.model_workflow /tmp/sp.json`
Expected: 打印 `[model_promote] done ok=True ...`

- [ ] **Step 3: 提交**

```bash
git add guanlan_v2/strategy/compute/model_workflow.py
git commit -m "feat(model_workflow): 子进程入口(spec.json→train_promote,失败非零退出码)"
```

---

# Phase C — promote 异步端点

### Task C1:`POST /model/promote` + `GET /model/promote/status`

**Files:**
- Modify: `guanlan_v2/workflow/api.py`
- Test: `tests/test_model_workflow_promote.py`

- [ ] **Step 1: 写失败测试(端点校验 + 状态机,桩掉子进程)**

```python
def test_promote_rejects_empty_recipe():
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    r = c.post("/model/promote", json={"name": "x", "kind": "lightgbm", "recipe": {"features": []}})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_promote_starts_and_status(monkeypatch):
    import guanlan_v2.workflow.api as wapi
    monkeypatch.setattr(wapi, "_run_promote_subprocess", lambda spec: None)   # 桩掉子进程
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    r = c.post("/model/promote",
               json={"name": "x", "kind": "lightgbm",
                     "recipe": {"features": ["close/Ref(close,5)-1"], "universe": "csi300"}})
    j = r.json()
    assert j["ok"] is True and j["variant_id"].startswith("m_")
    s = c.get("/model/promote/status").json()
    assert s["ok"] is True and "state" in s
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_workflow_promote.py -k promote -v`
Expected: FAIL(404 /model/promote 不存在)

- [ ] **Step 3: 实现端点 + 状态机(镜像 screen `_run_model_train_subprocess`)**

在 `guanlan_v2/workflow/api.py` 模块级加(确保 `from fastapi import Body`、`from fastapi.responses import JSONResponse` 已 import):

```python
import threading as _threading

_PROMOTE_LOCK = _threading.Lock()
_PROMOTE_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _promote_public_state() -> Dict[str, Any]:
    import time as _t
    with _PROMOTE_LOCK:
        s = dict(_PROMOTE_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_promote_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"mpromote_{spec['variant_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.model_workflow", str(sf)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _PROMOTE_LOCK:
                _PROMOTE_STATE["lines"].append(line)
                if "[model_promote]" in line:
                    _PROMOTE_STATE["phase"], _PROMOTE_STATE["label"], _PROMOTE_STATE["step"] = \
                        ("train", "生产重训中…", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _PROMOTE_LOCK:
            _PROMOTE_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})
```

在工作流 router 注册处(与现有 `/model/{kind}` 同 router)加:

```python
@router.post("/model/promote")
def model_promote(body: dict = Body(default={})):
    """工作流模型「存入模型库」:校验 recipe → 单飞抢锁 → 起子进程生产重训(全市场全窗口)
    → 落 models/<vid>(source=workflow)。立即返回(异步)。"""
    import time as _t, uuid, datetime
    kind = str(body.get("kind") or "").strip()
    recipe = dict(body.get("recipe") or {})
    if not recipe.get("features"):
        return JSONResponse({"ok": False, "reason": "recipe.features 为空"})
    if kind not in ("lightgbm", "xgboost", "rf"):
        return JSONResponse({"ok": False, "reason": f"kind '{kind}' 首期不支持入库(树模型 lgbm/xgb/rf)"})
    with _PROMOTE_LOCK:
        if _PROMOTE_STATE["running"]:
            return JSONResponse({"ok": False, "reason": "已有入库在跑", "state": _promote_public_state()})
        vid = "m_" + uuid.uuid4().hex[:10]
        _PROMOTE_STATE.update({"running": True, "phase": "starting", "label": "启动生产重训…",
            "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
            "variant_id": vid, "lines": []})
    spec = {"variant_id": vid, "name": str(body.get("name") or "工作流模型"),
            "kind": kind, "recipe": recipe,
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    _threading.Thread(target=lambda: _run_promote_subprocess(spec), daemon=True).start()
    return JSONResponse({"ok": True, "started": True, "variant_id": vid, "state": _promote_public_state()})


@router.get("/model/promote/status")
def model_promote_status():
    return JSONResponse({"ok": True, "state": _promote_public_state()})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_workflow_promote.py -k promote -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/workflow/api.py tests/test_model_workflow_promote.py
git commit -m "feat(workflow): POST /model/promote 异步子进程入库(镜像 model_train 状态机)+ /status"
```

---

# Phase D — `/models` provenance + model 节点只读排名

### Task D1:`/screen/models` 回 provenance(研究库共读)

**Files:**
- Test: `tests/test_model_workflow_promote.py`

- [ ] **Step 1: 写测试(`/screen/models` 带 provenance 字段)**

```python
def test_screen_models_returns_provenance(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    df = pd.DataFrame({"code": [f"SZ{300000+i:06d}" for i in range(120)],
                       "date": "2026-06-19", "lgb_pct": [i/119 for i in range(120)]})
    reg.save_variant("m_wf2", df, {"id": "m_wf2", "name": "wf", "source": "workflow",
                                   "kind": "rf", "recipe": {"features": ["x"]}, "retrainable": True})
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    j = TestClient(app).get("/screen/models").json()
    wf = [v for v in j["variants"] if v["id"] == "m_wf2"][0]
    assert wf["source"] == "workflow" and wf["kind"] == "rf" and wf["retrainable"] is True
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_model_workflow_promote.py -k provenance -v`
Expected: PASS(`/screen/models`→`list_variants()`→A1 已归一,无需改后端)

- [ ] **Step 3: 提交**

```bash
git add tests/test_model_workflow_promote.py
git commit -m "test(models): /screen/models 回 provenance(工作流研究库共读契约)"
```

### Task D2:`GET /model/ranking`(model 节点下游求值用)

**Files:**
- Modify: `guanlan_v2/screen/api.py`
- Test: `tests/test_model_workflow_promote.py`

- [ ] **Step 1: 写测试**

```python
def test_model_ranking_endpoint(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    df = pd.DataFrame({"code": [f"SZ{300000+i:06d}" for i in range(120)],
                       "date": "2026-06-19", "lgb_pct": [i/119 for i in range(120)]})
    reg.save_variant("m_r1", df, {"id": "m_r1", "name": "r"})
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    j = TestClient(app).get("/screen/model/ranking?id=m_r1").json()
    assert j["ok"] is True and len(j["rows"]) == 120 and "score" in j["rows"][0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_workflow_promote.py -k ranking_endpoint -v`
Expected: FAIL(404)

- [ ] **Step 3: 实现端点**

在 `guanlan_v2/screen/api.py` router 加(`/models` 附近):

```python
@router.get("/model/ranking")
def screen_model_ranking(id: str):
    """某 registry 模型最新截面 (code→lgb_pct),供工作流 model 节点下游求值/回测。"""
    import pandas as pd
    from guanlan_v2.screen.model_registry import variant_ranking_path
    p = variant_ranking_path(id)
    if not p.exists():
        return JSONResponse({"ok": False, "reason": "模型不存在"})
    df = pd.read_parquet(p)
    last = df[df["date"] == df["date"].max()]
    return JSONResponse({"ok": True, "date": str(last["date"].iloc[0]),
        "rows": [{"code": str(r.code), "score": float(r.lgb_pct)} for r in last.itertuples()]})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_workflow_promote.py -k ranking_endpoint -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/api.py tests/test_model_workflow_promote.py
git commit -m "feat(screen): GET /model/ranking 只读最新截面排名(工作流 model 节点用)"
```

---

# Phase E — 前端(研究库 + model 节点 + 来源徽章)

> 前端按项目惯例**只填充现有 UI 不重建**;无单测,改后 `bump ?v`(用 Edit 非 sed)+ 浏览器 preview 验真(Task E4)。

### Task E1:工作流「因子库」模态 → 「研究库」(加模型 tab)

**Files:** Modify `ui/factor/workflow.jsx`(`FactorLibModal` 约 1545-1610)

- [ ] **Step 1: 模态加 tab(因子/模型),模型 tab 拉 `/screen/models`**

```jsx
const [libTab, setLibTab] = React.useState('factor');   // 'factor' | 'model'
const [models, setModels] = React.useState([]);
React.useEffect(() => {
  if (libTab !== 'model') return;
  _get('/screen/models').then(j => { if (j && j.ok) setModels(j.variants || []); })
    .catch(() => setModels([]));
}, [libTab]);
```

```jsx
// 标题下 tab 条:
<div style={{display:'flex', gap:8, marginBottom:10}}>
  <button onClick={()=>setLibTab('factor')} className={libTab==='factor'?'on':''}>因子</button>
  <button onClick={()=>setLibTab('model')} className={libTab==='model'?'on':''}>模型</button>
</div>
// 模型 tab 列表(因子 tab 保留原渲染):
{libTab==='model' && (
  <div className="lib-list">
    {models.length===0 && <div className="muted">暂无模型(在工作流训好后点「存入模型库」)</div>}
    {models.map(m => (
      <div key={m.id} className="lib-row" onClick={()=>onPickModel && onPickModel(m)}>
        <b>{m.name}</b>
        <span className="badge">{m.source==='workflow'?'来自工作流':'本工坊'}</span>
        <span className="badge">{m.kind}</span>
        {m.oos_ic!=null && <span className="muted">OOS IC {m.oos_ic}</span>}
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 2: 提交**

```bash
git add ui/factor/workflow.jsx
git commit -m "feat(workflow-ui): 因子库模态扩成研究库(因子/模型 tab,模型读 /screen/models 带来源徽章)"
```

---

### Task E2:新 `model` 节点(引用 registry 模型 → 输出排名)

**Files:** Modify `ui/factor/workflow.jsx`(SPECS 约 22-56;NODE_EXEC 约 302+)

- [ ] **Step 1: SPECS 加 `model` 节点规格**

```jsx
// SPECS 对象里(factorlib 节点附近):
model:{ title:'模型', cat:'io', inputs:[], outputs:[{id:'out', label:'排名', dt:'series'}],
  params:[{id:'model_id', label:'已选模型', type:'text', value:''},
          {id:'model_name', label:'模型名', type:'text', value:''}] },
```

- [ ] **Step 2: NODE_EXEC 加 `model` 求值器(调 D2 端点)**

```jsx
model: async (inputs, params, ctx) => {
  const id = (params.model_id||'').trim();
  if (!id) throw new Error('模型节点:未选模型 —— 点节点里「研究库」选一个');
  const j = await _get('/screen/model/ranking?id='+encodeURIComponent(id));
  if (!j || !j.ok) throw new Error('模型节点:'+((j&&j.reason)||'排名不可达'));
  return { out: { kind:'ranking', model_id:id, date:j.date, rows:j.rows,
                  series:Object.fromEntries(j.rows.map(r=>[r.code, r.score])) } };
},
```

- [ ] **Step 3: 研究库模型 tab「选用」回写 model 节点 params**

E1 的 `onPickModel(m)` → 写当前 model 节点 `params.model_id=m.id; params.model_name=m.name`(复用因子库 `onPick` 写 params 同款逻辑)。

- [ ] **Step 4: 提交**

```bash
git add ui/factor/workflow.jsx
git commit -m "feat(workflow): model 节点(引用 registry 模型,调 /screen/model/ranking 输出排名)"
```

---

### Task E3:「存入模型库」按钮(ML 模型节点 → /model/promote)

**Files:** Modify `ui/factor/workflow.jsx`(lgbm/xgb/rf 节点渲染 / 运行报告区)

- [ ] **Step 1: 加按钮 + 轮询**

```jsx
async function promoteNode(node) {
  const recipe = exportRecipeFromNode(node);   // 复用「图→因子调用」导出:{features,label,fwd_days,universe,params}
  const kindMap = {lgbm:'lightgbm', xgb:'xgboost', rf:'rf'};
  const r = await _post('/model/promote',
    {name: node.params.name || (node.type+'·入库'), kind: kindMap[node.type], recipe});
  if (!r || !r.ok) { flash('存入失败:'+((r&&r.reason)||'')); return; }
  flash('已起生产重训(分钟级),完成后在研究库/工坊可见'); pollPromote();
}
function pollPromote() {
  const t = setInterval(async () => {
    const s = (await _get('/model/promote/status')).state || {};
    if (!s.running && s.phase==='done') {
      clearInterval(t); flash(s.ok ? '入库完成 ✓' : ('入库失败:'+(s.error||'')));
    }
  }, 3000);
}
```

> `exportRecipeFromNode` 复用文件内「从画布导出真因子调用」(约 254-264):导出上游特征表达式 + universe;label 取节点 label 参数缺省 `fwd_ret`;params 取本节点超参。

- [ ] **Step 2: 提交**

```bash
git add ui/factor/workflow.jsx
git commit -m "feat(workflow-ui): ML 模型节点「存入模型库」按钮(/model/promote 异步+轮询)"
```

---

### Task E4:工坊变体来源徽章 + 浏览器验真

**Files:** Modify `ui/screen/screen-app.jsx`(`ModelWorkshop` 变体列表 213-229)

- [ ] **Step 1: 变体行显示 source 徽章**

```jsx
<span className="badge">{m.source==='workflow' ? '来自工作流' : '本工坊'}</span>
{m.kind && m.kind!=='v4-lgb' && <span className="badge">{m.kind}</span>}
```

- [ ] **Step 2: bump ?v + 浏览器验真(端到端)**

`ui/factor/workflow.jsx`、`ui/screen/screen-app.jsx` 引用处 `?v=` bump(用 Edit)。然后:
1. 杀 9999 监听(等看门狗拉新代码 ~10s)。
2. 工作流页:搭「多因子→lgbm」DAG → 跑 → 点「存入模型库」→ 等完成。
3. 选股页模型工坊:新模型在列、带「来自工作流」徽章 → 选它 → `/screen/run` 出票。
4. 工作流研究库「模型」tab:看到工坊 v4 变体 + 刚入库模型 → 拖成 model 节点。
5. 控制台 0 报错。

- [ ] **Step 3: 提交**

```bash
git add ui/screen/screen-app.jsx ui/factor/workflow.jsx
git commit -m "feat(workshop-ui): 变体来源徽章(本工坊/来自工作流)+ ?v bump"
```

---

# 收尾

### Task F1:全量回归 + prod 兜底核验

- [ ] **Step 1: 全量 pytest**

Run: `python -m pytest -q`
Expected: 全绿(新 A/B/C/D 测试;`-m slow` 的 B1 真训按需单跑)

- [ ] **Step 2: prod 兜底核验**

Run: `python -m pytest tests/test_model_registry_provenance.py tests/test_model_workflow_promote.py -v`
Expected: PASS;`/screen/models` 里 `prod` 仍 `source=workshop/kind=v4-lgb`、可被 `/screen` 选股(不回归)。

- [ ] **Step 3: 提交(若有微调)**

```bash
git add -A && git commit -m "test(unified-registry): 全量回归绿 + prod 兜底核验"
```

---

## 自审清单(执行者开工前过一遍)

1. **spec 覆盖**:registry 泛化(A)/生产训练器(B)/promote(C)/共享列表+ranking(D)/研究库+model 节点+徽章(E)—— 对齐 spec 第 6 节六组件。✔
2. **占位符**:无 TBD;每步给真实代码/命令/期望。✔
3. **类型一致**:`RANKING_FILE`/`validate_ranking`/`_normalize_meta`/`train_promote`/`_run_promote_subprocess`/`_PROMOTE_STATE`/`/model/ranking` 跨任务命名一致;ranking 契约列 `code/date/lgb_pct` 全程统一。✔
4. **红线**:prod 只读零改(A1 兜底)、入库校验(A2)、promote 失败非零退出+显形(B2/C1)、不碰 /screen 选股算法、9999 看门狗(C1 子进程)。✔

> **挂账(② 之后)**:① CPCV 严格档加 `retrain_core(kind, recipe, mask)` 分派器(用 B 的 `_materialize_xy`+`_build_model` 按掩码重训)验证任意 registry 模型;③ 验证节点接「运行测试」。lstm/mlp/svm 入库待 MVP-B。
