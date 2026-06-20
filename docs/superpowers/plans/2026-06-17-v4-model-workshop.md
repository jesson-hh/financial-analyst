# v4 模型工坊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户从「v4 基础特征 + 我的库因子」统一勾选子集,训练命名 v4 变体,在选股页用变体选股,按留出 OOS IC 对比多组变体,生产 v4 全程不动。

**Architecture:** `model_train` = 参数化的 `build_v4`(抽 `feature_cols` + `extra_factor_panel` + `holdout`,默认零行为变化)。变体产物落 `vendor/artifacts/models/<id>/`(7 列同 schema + meta.json),`load_v4_ranking(model_id)` 选源,`ScreenIn.model` 透传,前端顶栏模型下拉 + 模型工坊抽屉(异步训练,照搬 regen 进度范式)。

**Tech Stack:** Python(pandas/lightgbm)、FastAPI、引擎 `financial_analyst.factors.zoo`(compile_factor/load_panel_cached)、React(text/babel jsx)。

**Spec:** `docs/superpowers/specs/2026-06-17-v4-model-workshop-design.md`

**测试约定**:Python 用 pytest(`PYTHONPATH=engine;repo`,见 `tests/conftest.py`)。前端 jsx 本仓无单测框架,按既有实践=改完浏览器实机验证(改 `?v` + reload);前端任务的"验证"步即浏览器核对。每个 Task 末尾 commit。

---

## Task 1: 纯函数 `holdout_split`(留出切分)

**Files:**
- Create: `guanlan_v2/strategy/compute/model_train.py`
- Test: `tests/test_model_train.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_model_train.py
import pandas as pd
from guanlan_v2.strategy.compute import model_train as mt


def test_holdout_split_reserves_last_k_labeled_days():
    dates = pd.to_datetime([f"2026-01-{d:02d}" for d in range(1, 13)])
    ld = dates.max()
    train_cut, holdout = mt.holdout_split(dates, ld, horizon=5, k=3)
    assert len(holdout) == 3
    assert train_cut < min(holdout)                      # train 截止 < 留出最早日(无重叠)
    assert max(holdout) <= ld - pd.Timedelta(days=5)     # 留出都在"有 label"区
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_train.py::test_holdout_split_reserves_last_k_labeled_days -v`
Expected: FAIL（`module ... has no attribute 'holdout_split'`）

- [ ] **Step 3: 写最小实现**

```python
# guanlan_v2/strategy/compute/model_train.py
"""v4 模型工坊:参数化训练变体(选因子)+ 留出 OOS IC。
不碰生产 v4(只写 models/<id>/);复用 build_v4 / compile_factor / 现成 IC 公式。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def holdout_split(dates, ld, horizon: int = 5, k: int = 20) -> Tuple[pd.Timestamp, List[pd.Timestamp]]:
    """返回 (train_cutoff, holdout_dates)。label=未来 horizon 日收益 → 末 horizon 天无 label 不可用;
    有 label 的最近 k 个交易日留作 OOS,train 截止 = 这些留出日的前一交易日。数据太短 → holdout 空。"""
    uniq = sorted(pd.Index(pd.to_datetime(pd.Series(dates))).unique())
    ld = pd.Timestamp(ld)
    labeled = [d for d in uniq if d <= ld - pd.Timedelta(days=horizon)]
    if len(labeled) <= k:
        return (labeled[-1] if labeled else ld), []
    return labeled[-k - 1], labeled[-k:]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_train.py::test_holdout_split_reserves_last_k_labeled_days -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/compute/model_train.py tests/test_model_train.py
git commit -m "feat(model-workshop): holdout_split for OOS IC"
```

---

## Task 2: 纯函数 `resolve_feature_cols`

**Files:**
- Modify: `guanlan_v2/strategy/compute/model_train.py`
- Test: `tests/test_model_train.py`

- [ ] **Step 1: 写失败测试**

```python
def test_resolve_feature_cols():
    available = ["rev_20", "vol_20", "breakout_20", "log_mv", "label", "pe_ttm"]
    cols = mt.resolve_feature_cols(available, base_features=["rev_20", "vol_20"], factor_ids=["c_28f035"])
    assert "rev_20" in cols and "vol_20" in cols
    assert "breakout_20" not in cols                  # 未选基础特征剔除
    assert "label" not in cols and "pe_ttm" not in cols
    cols2 = mt.resolve_feature_cols(available, base_features=["nope"], factor_ids=["log_mv"])
    assert cols2 == ["log_mv"]                          # 不存在的列丢弃


def test_resolve_feature_cols_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        mt.resolve_feature_cols(["rev_20", "label"], base_features=[], factor_ids=[])
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_train.py -k resolve_feature_cols -v`
Expected: FAIL

- [ ] **Step 3: 写实现(追加到 model_train.py)**

```python
NON_FEATURE = {"label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw"}


def resolve_feature_cols(available, base_features: List[str], factor_ids: List[str]) -> List[str]:
    """最终训练特征列 = (选中基础 ∪ 选中库因子),必须在 available 且非 label/估值原始列。
    顺序稳定(基础在前);全空 → ValueError(至少选 1 个)。"""
    av = set(available)
    picked = [c for c in base_features if c in av and c not in NON_FEATURE]
    picked += [c for c in factor_ids if c in av and c not in picked]
    if not picked:
        raise ValueError("至少选 1 个可用因子(基础或库因子)")
    return picked
```

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_train.py -k resolve_feature_cols -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/compute/model_train.py tests/test_model_train.py
git commit -m "feat(model-workshop): resolve_feature_cols"
```

---

## Task 3: 重构 `build_v4` 接受 `feature_cols`/`extra_factor_panel`/`holdout`(默认零行为变化)

**Files:**
- Modify: `guanlan_v2/strategy/compute/v4.py`（签名 :218、mf 行 :242、train 切分 :246-247、health 块后 :288）
- Test: `tests/test_model_train.py`

- [ ] **Step 1: 写失败测试(纯函数 `_select_mf`,免跑全市场 LGB)**

```python
from guanlan_v2.strategy.compute.v4 import _select_mf

def test_select_mf_default_unchanged():
    cols = ["rev_20", "vol_20", "label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw", "log_mv"]
    assert set(_select_mf(cols, None)) == {"rev_20", "vol_20", "log_mv"}   # 旧语义
    assert _select_mf(cols, ["rev_20", "log_mv"]) == ["rev_20", "log_mv"]  # 显式取交集保序
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_train.py::test_select_mf_default_unchanged -v`
Expected: FAIL（no attribute `_select_mf`）

- [ ] **Step 3: v4.py 抽 `_select_mf` 并替换 mf 行**

build_v4 之前新增:
```python
def _select_mf(columns, feature_cols=None):
    """模型特征列。feature_cols=None → 旧语义(除 label/估值原始列外全列);否则=显式列表∩现有列(保序)。"""
    if feature_cols is None:
        return [x for x in columns if x not in ("label", "pe_ttm", "pb", "total_mv", "ps_ttm_raw")]
    return [c for c in feature_cols if c in set(columns)]
```

build_v4 签名（[v4.py:218](../../../guanlan_v2/strategy/compute/v4.py)）加 3 参:
```python
             fincast_path: Optional[str] = None, b3: Optional[dict] = None,
             feature_cols: Optional[List[str]] = None,
             extra_factor_panel: Optional["pd.DataFrame"] = None,
             holdout: Optional[dict] = None) -> pd.DataFrame:
```

`data = add_breadth_resid(data)`（[:238](../../../guanlan_v2/strategy/compute/v4.py)）之后、mf 行之前注入库因子列:
```python
    if extra_factor_panel is not None and len(extra_factor_panel.columns):
        data = data.join(extra_factor_panel, how="left")
```

`mf = [x for x in data.columns if x not in (...)]`（[:242](../../../guanlan_v2/strategy/compute/v4.py)）替换为:
```python
    mf = _select_mf(list(data.columns), feature_cols)
```

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_train.py::test_select_mf_default_unchanged -v`
Expected: PASS

- [ ] **Step 5: 加真留出切分 + OOS IC(默认 holdout=None 时零变化)**

train 切分（[v4.py:246-247](../../../guanlan_v2/strategy/compute/v4.py)）替换为:
```python
    _train_hi = ld - timedelta(days=5)
    if holdout is not None:
        from guanlan_v2.strategy.compute.model_train import holdout_split as _hs
        _tc, _ = _hs(dates, ld, horizon=int(holdout.get("horizon", 5)), k=int(holdout.get("k", 20)))
        _train_hi = min(_train_hi, _tc)
    train = data[(dates >= pd.Timestamp("2022-01-01")) & (dates <= _train_hi)].dropna(subset=["label"]).copy()
```
（`timedelta` 已在 build_v4 内 import,见 [v4.py:245](../../../guanlan_v2/strategy/compute/v4.py)。）

health 块之后（[v4.py:288](../../../guanlan_v2/strategy/compute/v4.py)）追加 OOS IC(复用同款 rank-IC):
```python
    if holdout is not None:
        try:
            from guanlan_v2.strategy.compute.model_train import holdout_split
            _tc, _hd = holdout_split(dates, ld, horizon=int(holdout.get("horizon", 5)),
                                     k=int(holdout.get("k", 20)))
            hd = data[dates.isin(_hd)].dropna(subset=["label"]).copy()
            ics = []
            if len(hd):
                hd = hd.assign(_score=model.predict(hd[mf].fillna(0).values))
                for _d, g in hd.groupby(level="datetime"):
                    if len(g) >= 50:
                        ic = g["_score"].rank().corr(g["label"].rank())
                        if pd.notna(ic):
                            ics.append(float(ic))
            s = pd.Series(ics)
            holdout["oos_ic"] = float(s.mean()) if len(ics) else None
            holdout["oos_icir"] = float(s.mean() / s.std()) if len(ics) and s.std() > 0 else None
            holdout["n_holdout"] = int(len(ics))
        except Exception as e:  # noqa: BLE001
            holdout["error"] = f"{type(e).__name__}: {e}"
```

- [ ] **Step 6: 跑全 Task3 测试**

Run: `python -m pytest tests/test_model_train.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/strategy/compute/v4.py tests/test_model_train.py
git commit -m "refactor(v4): build_v4 accepts feature_cols/extra_factor_panel/holdout (default unchanged)"
```

---

## Task 4: 库因子求值器 `evaluate_library_factors`

**Files:**
- Modify: `guanlan_v2/strategy/compute/model_train.py`
- Test: `tests/test_model_train.py`

- [ ] **Step 1: 写失败测试(monkeypatch 薄封装,免跑引擎)**

```python
def test_evaluate_library_factors(monkeypatch):
    import numpy as np
    idx = pd.MultiIndex.from_product(
        [["SH600519", "SZ000001"], pd.to_datetime(["2026-01-05", "2026-01-06"])],
        names=["instrument", "datetime"])
    fake_defs = {"c_aaa": {"expr": "rank(close)", "short": "甲"}, "c_bad": {"expr": ""}}
    monkeypatch.setattr(mt, "_factor_defs", lambda: fake_defs)
    monkeypatch.setattr(mt, "_compile_factor", lambda expr: (lambda panel: pd.Series(
        np.arange(len(idx), dtype=float), index=idx)))
    monkeypatch.setattr(mt, "_load_panel", lambda codes, start, end: "PANEL")
    panel, unsup = mt.evaluate_library_factors(["SH600519", "SZ000001"],
                                               ["c_aaa", "c_bad", "c_missing"], "2026-01-01", "2026-01-06")
    assert list(panel.columns) == ["c_aaa"]
    assert set(unsup) == {"c_bad", "c_missing"}
    assert panel.index.names == ["instrument", "datetime"]
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_train.py::test_evaluate_library_factors -v`
Expected: FAIL

- [ ] **Step 3: 实现(追加 model_train.py;薄封装便于 monkeypatch)**

```python
def _factor_defs():
    from guanlan_v2.screen.catalog import FACTOR_DEFS
    return FACTOR_DEFS


def _compile_factor(expr):
    from financial_analyst.factors.zoo.expr import compile_factor
    return compile_factor(expr)


def _load_panel(codes, start, end):
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    return load_panel_cached(get_default_loader(), codes, start, end, freq="day")


def evaluate_library_factors(codes, factor_ids, start, end):
    """选中库因子 → (DataFrame[列=factor_id, index=instrument×datetime], unsupported列表)。
    复用 factor_ic.py 同款 compile_factor;无 expr/不在目录/求值失败 → unsupported,诚实缺席。"""
    defs = _factor_defs()
    panel = None
    cols, unsup = {}, []
    for fid in factor_ids:
        expr = (defs.get(fid) or {}).get("expr")
        if not expr:
            unsup.append(fid); continue
        if panel is None:
            panel = _load_panel([str(c) for c in codes], start, end)
        try:
            s = _compile_factor(expr)(panel)
            if s is None or not hasattr(s, "index"):
                unsup.append(fid); continue
            cols[fid] = s
        except Exception:  # noqa: BLE001
            unsup.append(fid)
    if not cols:
        return pd.DataFrame(), unsup
    out = pd.DataFrame(cols)
    out.index = out.index.set_names(["instrument", "datetime"])
    return out, unsup
```

> 执行注:`load_panel_cached` 的 index 实名可能是 `(code, datetime)` 或 `(instrument, datetime)`;实现时先核对真实 index 名,确保与 build_feature_panel(reorder 成 instrument×datetime,[v4.py:169-170](../../../guanlan_v2/strategy/compute/v4.py))join 对齐,必要时调 `set_names` 顺序。

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_train.py::test_evaluate_library_factors -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/compute/model_train.py tests/test_model_train.py
git commit -m "feat(model-workshop): evaluate_library_factors via compile_factor"
```

---

## Task 5: 训练编排 `train_variant` + CLI 入口

**Files:**
- Modify: `guanlan_v2/strategy/compute/model_train.py`
- Test: `tests/test_model_train.py`(monkeypatch build_v4/注册表,验编排不跑真 LGB)

- [ ] **Step 1: 写失败测试**

```python
def test_train_variant_writes_product_and_meta(tmp_path, monkeypatch):
    def fake_build_v4(provider_uri, start, end, codes=None, feature_cols=None,
                      extra_factor_panel=None, holdout=None, **kw):
        if holdout is not None:
            holdout.update({"oos_ic": 0.04, "oos_icir": 0.8, "n_holdout": 15})
        return pd.DataFrame({"code": ["SH600519"], "lgb_score": [1.0], "lgb_pct": [0.9],
                             "lgb_rank": [1], "v4_total": [5], "v4_layer": ["大盘"], "date": ["2026-06-17"]})
    monkeypatch.setattr(mt, "_build_v4", fake_build_v4)
    monkeypatch.setattr(mt, "evaluate_library_factors", lambda c, f, s, e: (pd.DataFrame(), []))
    monkeypatch.setattr(mt, "_base_feature_names", lambda: ["rev_20", "vol_20"])
    monkeypatch.setattr(mt, "_list_codes", lambda uni: ["SH600519"])
    monkeypatch.setattr(mt, "_latest_date", lambda: "2026-06-17")
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    res = mt.train_variant(variant_id="m_test1", name="测试变体", factor_ids=[],
                           base_features=["rev_20"], universe="all", created="2026-06-17T10:00:00")
    assert res["ok"] is True
    meta = reg.variant_meta("m_test1")
    assert meta["name"] == "测试变体" and meta["oos_ic"] == 0.04 and meta["n_holdout"] == 15
    assert (tmp_path / "models" / "m_test1" / "v4_ranking.parquet").exists()
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_train.py::test_train_variant_writes_product_and_meta -v`
Expected: FAIL

- [ ] **Step 3: 实现 train_variant + 薄封装 + CLI**

```python
DEFAULT_PROVIDER = "G:/stocks/stock_data/cn_data"


def _build_v4(*a, **kw):
    from guanlan_v2.strategy.compute.v4 import build_v4
    return build_v4(*a, **kw)


def _latest_date():
    from guanlan_v2.strategy.compute.regen import _latest_trade_date
    return _latest_trade_date(DEFAULT_PROVIDER)


def _list_codes(universe):
    if universe in ("all", "", None):
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        return list_all_instruments(DEFAULT_PROVIDER)
    from financial_analyst.data.universe import resolve_universe_codes
    return [str(c) for c in resolve_universe_codes(universe)]


def _base_feature_names():
    """v4 基础特征名(供前端〈v4 基础特征〉组)。小宇宙跑 build_feature_panel 取列名 + 注入列。"""
    from guanlan_v2.strategy.compute.v4 import _select_mf, build_feature_panel
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    end = _latest_date()
    start = (pd.Timestamp(end) - pd.Timedelta(days=400)).date().isoformat()
    panel = build_feature_panel(QlibBinaryLoader(DEFAULT_PROVIDER),
                                ["SH600519", "SZ000001", "SH600036"], start, end)
    base = set(_select_mf(list(panel.columns), None)) | {"ind_turnover", "lu_resid_pct60", "amt_resid_pct60"}
    return sorted(base)


def train_variant(variant_id, name, factor_ids, base_features, universe="all",
                  created="", holdout_k=20) -> dict:
    from guanlan_v2.screen import model_registry as reg
    end = _latest_date()
    start = "2022-01-01"
    codes = _list_codes(universe)
    extra, unsup = evaluate_library_factors(codes, factor_ids, start, end)
    feature_cols = resolve_feature_cols(
        list(_base_feature_names()) + list(extra.columns), base_features, list(extra.columns))
    hd = {"k": holdout_k, "horizon": 5}
    df = _build_v4(DEFAULT_PROVIDER, start, end, codes=codes, feature_cols=feature_cols,
                   extra_factor_panel=(extra if len(extra.columns) else None), holdout=hd)
    meta = {"id": variant_id, "name": name, "factor_ids": list(factor_ids),
            "base_features": list(base_features), "n_features": len(feature_cols),
            "unsupported_factors": unsup, "universe": universe,
            "oos_ic": hd.get("oos_ic"), "oos_icir": hd.get("oos_icir"), "n_holdout": hd.get("n_holdout"),
            "asof": str(df["date"].iloc[0]) if len(df) else end, "created": created,
            "train_rows": int(len(df)), "error": hd.get("error")}
    reg.save_variant(variant_id, df, meta)
    return {"ok": True, "variant_id": variant_id, "meta": meta}


if __name__ == "__main__":   # 子进程入口:python -m ...model_train <spec.json>
    import json, sys
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(f"[model_train] variant={spec['variant_id']} factors={len(spec.get('factor_ids', []))} ...", flush=True)
    r = train_variant(**spec)
    print(f"[model_train] done oos_ic={r['meta'].get('oos_ic')}", flush=True)
```

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_train.py -v`
Expected: PASS(全绿)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/compute/model_train.py tests/test_model_train.py
git commit -m "feat(model-workshop): train_variant orchestration + CLI"
```

---

## Task 6: 变体注册表 `model_registry.py` + paths.MODELS_DIR

**Files:**
- Modify: `guanlan_v2/strategy/paths.py`（`ARTIFACTS_DIR` 之后加 MODELS_DIR）
- Create: `guanlan_v2/screen/model_registry.py`
- Test: `tests/test_model_registry.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_model_registry.py
import pandas as pd, pytest
from guanlan_v2.screen import model_registry as reg

_DF = pd.DataFrame({"code": ["SH600519"], "lgb_score": [1.0], "lgb_pct": [0.9],
                    "lgb_rank": [1], "v4_total": [5], "v4_layer": ["大盘"], "date": ["2026-06-17"]})

def test_save_list_get_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_a", _DF, {"id": "m_a", "name": "甲", "oos_ic": 0.05})
    reg.save_variant("m_b", _DF, {"id": "m_b", "name": "乙", "oos_ic": 0.02})
    assert [v["id"] for v in reg.list_variants()] == ["m_a", "m_b"]    # oos_ic 降序
    assert reg.variant_meta("m_a")["name"] == "甲"
    assert reg.variant_ranking_path("m_a").exists()
    reg.delete_variant("m_a")
    assert [v["id"] for v in reg.list_variants()] == ["m_b"]

def test_delete_prod_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    with pytest.raises(ValueError):
        reg.delete_variant("prod")
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_registry.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

paths.py（[paths.py:9](../../../guanlan_v2/strategy/paths.py) `ARTIFACTS_DIR` 之后）加:
```python
MODELS_DIR = ARTIFACTS_DIR / "models"
```

`guanlan_v2/screen/model_registry.py`:
```python
"""v4 变体注册表:models/<id>/{v4_ranking.parquet, meta.json}。生产 v4=prod(只读老路径,不在此)。"""
from __future__ import annotations
import json, os, shutil
from typing import Any, Dict, List
from guanlan_v2.strategy.paths import MODELS_DIR


def _dir(vid): return MODELS_DIR / vid
def variant_ranking_path(vid): return _dir(vid) / "v4_ranking.parquet"


def variant_meta(vid) -> Dict[str, Any]:
    p = _dir(vid) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_variant(vid, ranking_df, meta) -> None:
    d = _dir(vid); d.mkdir(parents=True, exist_ok=True)
    pq = variant_ranking_path(vid); tmp = str(pq) + ".tmp"
    ranking_df.to_parquet(tmp, index=False); os.replace(tmp, str(pq))
    mp = d / "meta.json"; mtmp = str(mp) + ".tmp"
    open(mtmp, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=1))
    os.replace(mtmp, str(mp))


def list_variants() -> List[Dict[str, Any]]:
    if not MODELS_DIR.exists():
        return []
    out = [variant_meta(d.name) for d in MODELS_DIR.iterdir()
           if d.is_dir() and (d / "meta.json").exists()]
    out.sort(key=lambda m: (m.get("oos_ic") if m.get("oos_ic") is not None else -1e9), reverse=True)
    return out


def delete_variant(vid) -> None:
    if vid == "prod":
        raise ValueError("生产 v4(prod)不可删")
    d = _dir(vid)
    if d.exists():
        shutil.rmtree(d)
```

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/paths.py guanlan_v2/screen/model_registry.py tests/test_model_registry.py
git commit -m "feat(model-workshop): variant registry + MODELS_DIR"
```

---

## Task 7: `load_v4_ranking(model_id)` / `ranking_date(model_id)` 参数化

**Files:**
- Modify: `guanlan_v2/strategy/ranking.py:35,46`
- Test: `tests/test_model_registry.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_load_v4_ranking_by_model(tmp_path, monkeypatch):
    from guanlan_v2.strategy import ranking as R
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_x", _DF, {"id": "m_x"})
    assert list(R.load_v4_ranking(model_id="m_x")["code"]) == ["SH600519"]
    assert R.ranking_date(model_id="m_x") == "2026-06-17"
    with pytest.raises(FileNotFoundError):
        R.load_v4_ranking(model_id="nope")
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_model_registry.py::test_load_v4_ranking_by_model -v`
Expected: FAIL

- [ ] **Step 3: 改 ranking.py**

```python
def load_v4_ranking(model_id=None):
    """读 v4 排名;model_id 缺省/"prod" → 生产老路径;否则 models/<id>/v4_ranking.parquet。缺文件 → FileNotFoundError。"""
    import pandas as pd
    if model_id and model_id != "prod":
        from guanlan_v2.screen.model_registry import variant_ranking_path
        p = variant_ranking_path(model_id)
        if not p.exists():
            raise FileNotFoundError(f"v4 变体产物缺失: {p}")
        return pd.read_parquet(p)
    if not V4_RANKING_PARQUET.exists():
        raise FileNotFoundError(f"v4 排名产物缺失: {V4_RANKING_PARQUET}")
    return pd.read_parquet(V4_RANKING_PARQUET)


def ranking_date(model_id=None) -> str:
    try:
        df = load_v4_ranking(model_id=model_id)
    except FileNotFoundError:
        return ""
    return str(df["date"].iloc[0]) if "date" in df.columns and len(df) else ""
```

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_model_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/strategy/ranking.py tests/test_model_registry.py
git commit -m "feat(model-workshop): load_v4_ranking/ranking_date accept model_id"
```

---

## Task 8: `ScreenIn.model` + `_screen_via_v4` 选模型(坏 id 回落 prod)

**Files:**
- Modify: `guanlan_v2/screen/api.py`（ScreenIn :56;_screen_via_v4 :570,574）
- Test: `tests/test_screen_api.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_run_uses_model_variant(monkeypatch):
    from guanlan_v2.strategy import ranking as R
    calls, real = {}, R.load_v4_ranking
    def spy(model_id=None):
        calls["model_id"] = model_id
        return real()
    monkeypatch.setattr(R, "load_v4_ranking", spy)
    j = _client().post("/screen/run", json={**_CFG, "model": "prod"}).json()
    assert j["ok"] is True and calls.get("model_id") in (None, "prod")

def test_run_bad_model_falls_back():
    j = _client().post("/screen/run", json={**_CFG, "model": "does_not_exist"}).json()
    assert j["ok"] is True and j["source"] == "v4_ranking"      # 回落 prod,不 500
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_screen_api.py -k "model_variant or bad_model" -v`
Expected: FAIL

- [ ] **Step 3: 改 api.py**

ScreenIn 加（[api.py:56](../../../guanlan_v2/screen/api.py) `universe` 旁）:
```python
    model: str = "prod"          # v4 模型:prod=生产 / 变体 id(读 models/<id>)
```

`_screen_via_v4` 里 `rank = S.load_v4_ranking()` / `rdate = S.ranking_date()`（[api.py:570,574](../../../guanlan_v2/screen/api.py)）替换为:
```python
    _mid = getattr(body, "model", "prod") or "prod"
    try:
        rank = S.load_v4_ranking(model_id=_mid)
    except FileNotFoundError:
        rank = S.load_v4_ranking(); _mid = "prod"        # 变体不可用 → 诚实回落 prod
    rdate = S.ranking_date(model_id=_mid)
```
返回体加 `"model": _mid`（[api.py:791](../../../guanlan_v2/screen/api.py) 返回 dict 内,供前端知实际用了哪个/是否回落）。

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_screen_api.py -v`
Expected: PASS(全绿)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/api.py tests/test_screen_api.py
git commit -m "feat(model-workshop): /screen/run model-aware with prod fallback"
```

---

## Task 9: 训练端点(异步,镜像 regen)+ 列表/删除/基础特征

**Files:**
- Modify: `guanlan_v2/screen/api.py`（_MODEL_STATE/runner/5 端点)
- Test: `tests/test_screen_api.py`(追加,monkeypatch runner)

- [ ] **Step 1: 写失败测试**

```python
def test_model_endpoints(monkeypatch, tmp_path):
    import pandas as pd, guanlan_v2.screen.api as api
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_seed", pd.DataFrame({"code":["SH600519"],"lgb_score":[1.0],"lgb_pct":[0.9],
        "lgb_rank":[1],"v4_total":[5],"v4_layer":["大盘"],"date":["2026-06-17"]}),
        {"id":"m_seed","name":"种子","oos_ic":0.03})
    c = _client()
    assert any(v["id"]=="m_seed" for v in c.get("/screen/models").json()["variants"])
    monkeypatch.setattr(api, "_run_model_train_subprocess", lambda spec: None)
    assert c.post("/screen/model/train", json={"name":"t","factor_ids":[],"base_features":["rev_20"]}).json()["started"] is True
    assert c.post("/screen/model/train", json={"name":"t","factor_ids":[],"base_features":[]}).json()["ok"] is False
    assert c.post("/screen/model/delete", json={"id":"prod"}).json()["ok"] is False
    assert c.post("/screen/model/delete", json={"id":"m_seed"}).json()["ok"] is True
    assert c.get("/screen/base_features").json()["ok"] is True
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_screen_api.py::test_model_endpoints -v`
Expected: FAIL

- [ ] **Step 3: 实现(api.py)**

模块级(regen state 附近)加 model 训练 state + runner + 时间助手(镜像 [_run_regen_subprocess](../../../guanlan_v2/screen/api.py)):
```python
_MODEL_LOCK = _threading.Lock()
_MODEL_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _time_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _model_public_state() -> Dict[str, Any]:
    import time as _t
    with _MODEL_LOCK:
        s = dict(_MODEL_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_model_train_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    repo = _P(__file__).resolve().parents[2]
    sf = _P(tempfile.gettempdir()) / f"mtrain_{spec['variant_id']}.json"
    sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.model_train", str(sf)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    rc, err = None, None
    try:
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _MODEL_LOCK:
                _MODEL_STATE["lines"].append(line)
                if "[model_train]" in line or "build_feature_panel" in line:
                    _MODEL_STATE["phase"], _MODEL_STATE["label"], _MODEL_STATE["step"] = ("train", "训练中(LGB)", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _MODEL_LOCK:
            _MODEL_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})
```

build_screen_router 内(挨着 regen 端点)加 5 端点:
```python
    @router.get("/models")
    def screen_models():
        from guanlan_v2.screen.model_registry import list_variants
        return JSONResponse({"ok": True, "variants": list_variants()})

    @router.get("/model/status")
    def screen_model_status():
        return JSONResponse({"ok": True, "state": _model_public_state()})

    @router.get("/base_features")
    def screen_base_features():
        from guanlan_v2.strategy.compute.model_train import _base_feature_names
        try:
            return JSONResponse({"ok": True, "features": _base_feature_names()})
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(e).__name__}: {e}", "features": []})

    @router.post("/model/train")
    def screen_model_train(body: dict = Body(default={})):
        import time as _t, uuid
        name = str(body.get("name") or "").strip() or "未命名变体"
        fids, base = list(body.get("factor_ids") or []), list(body.get("base_features") or [])
        if not fids and not base:
            return JSONResponse({"ok": False, "reason": "至少选 1 个因子"})
        with _MODEL_LOCK:
            if _MODEL_STATE["running"]:
                return JSONResponse({"ok": False, "reason": "已有训练在跑", "state": _model_public_state()})
            vid = "m_" + uuid.uuid4().hex[:10]
            _MODEL_STATE.update({"running": True, "phase": "starting", "label": "启动训练子进程…",
                "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                "variant_id": vid, "lines": []})
        spec = {"variant_id": vid, "name": name, "factor_ids": fids, "base_features": base,
                "universe": str(body.get("universe") or "all"), "created": _time_iso()}
        _threading.Thread(target=lambda: _safe(lambda: _run_model_train_subprocess(spec)), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "variant_id": vid, "state": _model_public_state()})

    @router.post("/model/delete")
    def screen_model_delete(body: dict = Body(default={})):
        from guanlan_v2.screen.model_registry import delete_variant
        try:
            delete_variant(str(body.get("id") or "")); return JSONResponse({"ok": True})
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": str(e)})
```
确认 `Body` 已 import(文件已用 Body)。

- [ ] **Step 4: 跑确认通过**

Run: `python -m pytest tests/test_screen_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/api.py tests/test_screen_api.py
git commit -m "feat(model-workshop): async train + models/status/delete/base_features endpoints"
```

---

## Task 10: 前端数据层 — model 透传 + fetch 助手

**Files:**
- Modify: `ui/screen/screen-data.jsx`、`ui/screen/观澜 · 选股.html`（`?v`）

- [ ] **Step 1: 加 fetch 助手(IIFE 内挂 window)**

```javascript
  window.xgModels = async (API) => (await (await fetch((API||'')+'/screen/models')).json());
  window.xgBaseFeatures = async (API) => (await (await fetch((API||'')+'/screen/base_features')).json());
  window.xgTrain = async (API, spec) => (await (await fetch((API||'')+'/screen/model/train',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json());
  window.xgTrainStatus = async (API) => (await (await fetch((API||'')+'/screen/model/status')).json());
  window.xgDeleteModel = async (API, id) => (await (await fetch((API||'')+'/screen/model/delete',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})).json());
```
（`xgBuildBackend` 已整发 cfg → `model` 字段自动上送,无需改。）

- [ ] **Step 2: bump** `观澜 · 选股.html`:`screen-data.jsx?v=20260610d` → `?v=20260617a`。

- [ ] **Step 3: 验证(浏览器 console)**

reload,`await window.xgModels(window.GUANLAN_BACKEND)` → `{ok:true, variants:[]}`;`await window.xgBaseFeatures(window.GUANLAN_BACKEND)` → `{ok:true, features:[...]}`。

- [ ] **Step 4: Commit**

```bash
git add "ui/screen/screen-data.jsx" "ui/screen/观澜 · 选股.html"
git commit -m "feat(model-workshop): screen-data model fetch helpers"
```

---

## Task 11: 前端 — 顶栏模型下拉

**Files:**
- Modify: `ui/screen/screen-app.jsx`、`ui/screen/观澜 · 选股.html`（`?v`）

- [ ] **Step 1: cfg 加 model**

`defaultCfg()`（[screen-app.jsx:37](../../../ui/screen/screen-app.jsx)）return 加 `model: 'prod'`;take('screen') 合并块加 `...(c0.model ? { model: c0.model } : {})`。

- [ ] **Step 2: 拉变体 + 顶栏下拉**

XuanguApp 内:
```javascript
const [models, setModels] = useState([{id:'prod', name:'生产 v4', oos_ic:null}]);
const reloadModels = () => { if(API&&window.xgModels) window.xgModels(API).then(j=>{
  if(j&&j.ok) setModels([{id:'prod',name:'生产 v4',oos_ic:null}].concat(j.variants||[])); }); };
useEffect(reloadModels, []);
```
TopBar 接 `models`、`cfg.model`、`onModel=(v)=>{setF({model:v}); refresh();}`,在「评级池 v4 · N」([screen-app.jsx:272](../../../ui/screen/screen-app.jsx))旁渲染 `<select>`(选项 prod + 变体名 + OOS IC);若 `result.model && result.model !== cfg.model` 显示「⚠ 变体不可用,已用生产 v4」小字。

- [ ] **Step 3: bump** → `screen-app.jsx?v=20260617b`。

- [ ] **Step 4: 验证(浏览器)**

reload;顶栏出现模型下拉默认「生产 v4」,选它选股=现状;构造坏 id 验回落提示。截图留证。

- [ ] **Step 5: Commit**

```bash
git add "ui/screen/screen-app.jsx" "ui/screen/观澜 · 选股.html"
git commit -m "feat(model-workshop): screen topbar model picker"
```

---

## Task 12: 前端 — 模型工坊抽屉(选因子 + 训练 + 变体列表)

**Files:**
- Modify: `ui/screen/screen-app.jsx`（新增 `ModelWorkshop` 组件 + 顶栏「⚙ 模型工坊」入口）、`ui/screen/观澜 · 选股.html`（`?v`）

- [ ] **Step 1: 实现 ModelWorkshop(完整交互逻辑)**

组件契约(执行者按此写,复用既有 Toggle/RailSection 样式 + regen 的 `_pollRegen` 进度范式):
- props:`{API, models, reloadModels, onPick, onClose}`。
- state:`baseFeats`(从 `xgBaseFeatures` 拉)、`selBase`(Set,默认 = 全部 baseFeats)、`selLib`(Set,默认空)、`name`、`train`(进度态 {busy,phase,label,step,elapsed})。
- 布局(右侧 fixed 抽屉):
  1. 头:命名 input + 「🔨 训练」按钮(`selBase.size + selLib.size === 0` → disabled + title「至少选 1 个」)。
  2. 〈v4 基础特征〉组:`baseFeats` 每项 checkbox(`selBase`),组顶「全选/全不选」。
  3. 〈我的因子库〉组:`window.XG_FACTORS`(已是 /screen/factors 目录)每项 checkbox(`selLib`,存 factor id)。
  4. 训练区:点按钮 → `xgTrain(API,{name, factor_ids:[...selLib], base_features:[...selBase], universe:'all'})` → setInterval 轮询 `xgTrainStatus`(渲染 phase/label/step/elapsed,同 regen 进度条)→ status.running=false 时停轮询 + `reloadModels()` + flash(ok?「变体已训好」:「训练失败:」+error)。
  5. 〈已训变体〉列表:`models` 过滤掉 prod,每行 = 名 · `n_features`因子 · `oos_ic?.toFixed(3)??'—'` 留出OOS · asof;点行 → `onPick(id)` + onClose;行尾「×」→ confirm → `xgDeleteModel(API,id)` + reloadModels。诚实小字「留出验证 OOS · 非未来实盘」。
- 训练中(train.busy)禁用「训练」按钮 + 显进度。

- [ ] **Step 2: 顶栏入口 + 挂载**

XuanguApp:`const [showWs,setShowWs]=useState(false)`;TopBar 右侧加「⚙ 模型工坊」span → `setShowWs(true)`;`{showWs && <ModelWorkshop API={API} models={models} reloadModels={reloadModels} onPick={(id)=>{setF({model:id}); refresh();}} onClose={()=>setShowWs(false)} />}`。

- [ ] **Step 3: bump** → `screen-app.jsx?v=20260617c`。

- [ ] **Step 4: 验证(浏览器,实机端到端)**

reload → 开工坊 → 取消全部基础、只勾 1–2 个库因子(或反之)→ 命名「估值实验」→ 训练 → 进度跑完(真训,数分钟)→ 变体进列表带 OOS IC → 点它 → 顶栏切到该变体、清单刷新成它选的票。空选时按钮 disabled 验证。截图留证。

- [ ] **Step 5: Commit**

```bash
git add "ui/screen/screen-app.jsx" "ui/screen/观澜 · 选股.html"
git commit -m "feat(model-workshop): model workshop drawer (select/train/variants)"
```

---

## Task 13: 端到端验证 + 全量测试

- [ ] **Step 1: 重启 9999**(杀监听 PID,watchdog 拉新码;见记忆 watchdog-9999)。

- [ ] **Step 2: 全量 pytest**

Run: `python -m pytest tests/test_model_train.py tests/test_model_registry.py tests/test_screen_api.py -v`
Expected: 全绿。

- [ ] **Step 3: 命令行真训一个变体**

Run:
```bash
python -c "from guanlan_v2.strategy.compute.model_train import train_variant; import json; print(json.dumps(train_variant('m_smoke','冒烟',[],['rev_20','vol_20','breakout_20'],'all','2026-06-17T00:00:00'),ensure_ascii=False,default=str)[:400])"
```
Expected: `ok:true` + meta 带数值 oos_ic + `vendor/artifacts/models/m_smoke/v4_ranking.parquet` 生成。

- [ ] **Step 4: 选股用变体**

`POST /screen/run {model:"m_smoke", factors:[], pool:"all", topN:20}` → `ok:true`、`source:v4_ranking`、清单非空;与 prod 同参对比排名集可不同(变体真生效)。

- [ ] **Step 5: 浏览器走查 + 清理**

确认重启后线上一致(Task11/12 验证覆盖);`POST /screen/model/delete {id:"m_smoke"}` 清冒烟变体。

- [ ] **Step 6: Commit(收尾)**

```bash
git add -A && git commit -m "test(model-workshop): e2e verification"
```

---

## Self-Review 记录

- **Spec 覆盖**:§2.1 训练器=Task3/4/5;§2.2 注册表=Task6;§2.3 选股集成=Task7/8+前端Task10/11;§3 因子选择交互=Task12;§4 meta=Task5/6;§5 训练流程=Task5/9;§5 base_features 来源=Task9(/screen/base_features 端点);§7 OOS IC=Task1/3;§8 错误处理=Task5(meta.error)/Task8(回落)/Task9(单飞+空校验);§9 范围=全任务限定 v1;§10 红线=Task6/7(不碰 prod)/Task3(默认零变化)。无遗漏。
- **占位扫描**:Task12 组件以"契约要点"给出(状态/接口/分支/默认全勾·可全取消·≥1 校验完整),按仓内 jsx 实践浏览器验证,非 TBD。
- **类型一致**:`load_v4_ranking(model_id=)`(T7)↔`_screen_via_v4`(T8);`save_variant/variant_meta/variant_ranking_path/list_variants/delete_variant`(T6)↔ 调用方(T5/7/9);`holdout` 出参键 `oos_ic/oos_icir/n_holdout/error`(T3)↔ train_variant 读(T5);`build_v4(..., feature_cols, extra_factor_panel, holdout)`(T3)↔`_build_v4`(T5);`_base_feature_names`(T5)↔ 端点(T9)。一致。
