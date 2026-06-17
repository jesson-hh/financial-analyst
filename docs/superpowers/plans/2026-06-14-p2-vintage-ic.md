# P2 量化卡 vintage IC 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 逐任务执行。步骤用 `- [ ]`。
> 本仓**无 git**——「提交」= 跑 pytest 全绿(`G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`)。G:\stocks 只读。改 python 须重启 9999(杀监听 PID 等 ~10s 看门狗拉新)。改 jsx 必 bump `?v`(用 Edit 非 sed)。诚实降级不算 mock。GateGuard:每次 Write/Edit 前陈述四事实。

**Goal:** 把因子卡的 IC 从「静态 60 日截面 rank-IC、算到最新日(回测时看未来)」换成「逐日 vintage IC(as-of 决策日 D、真 OOS、PIT)」,离线批算缓存、decide as-of 查表喂 agent、前端显形;截面(csi300 全 catalog)+ 单票 tsic(watch-pool×factorlib56)两种口径。

**Architecture:** 一次面板加载 + 一遍因子编译,同时产两张逐日序列表(cs / tsic),每行带 `realized_date`(fwd 实现日)作 OOS 闸门。`vintage_asof(D)` 只取 `realized_date≤D` 的 trailing 窗求 IC,`<min_n` 诚实 None。挂 regen step 3.6;decide resolve 因子→catalog id 后 as-of 查表入 prompt + 落盘;前端料库显最新 vintage、RunDecCard 显 as-of vintage。

**Tech Stack:** Python 3.13 / pandas / pyarrow(parquet);引擎 `compile_factor`/`ic_analysis`/`load_panel_cached`/`_inject_market_refs`;FastAPI(9999);no-build React UMD(`ui/seats/*.jsx`)。

**复用基准(实现时现场 Read 对齐口径)**:
- `guanlan_v2/screen/factor_ic.py:28-103` `compute_catalog_ic` —— 面板加载 + idx_ret 注入 + fwd 算法 + 逐日截面 rank-IC 循环(P2 cs 直接照搬内层,只改「不 mean、全留 + realized_date」)。
- `guanlan_v2/strategy/model_health.py:64-121` `update_vintage_ic` —— vintage 真 OOS 范式(realized 判据 + 原子写)。
- `engine/financial_analyst/factors/eval/ic.py` `ic_analysis`(逐日 rank-IC)、`.../eval/report.py:61` `forward_simple_returns`。
- `guanlan_v2/screen/catalog.py` `FACTOR_DEFS`(catalog,含 factorlib 并入;`{fid:{short,family,expr,dir,...}}`)。
- `guanlan_v2/strategy/compute/regen.py:199-208` step 3.5(在其后挂 3.6)。
- `guanlan_v2/seats/api.py` decide rf_line(≈1143-1156)+ 落盘 dict(≈1223-1238)。
- `ui/seats/luozi-data.jsx:183-206` `recipeForStrategy`、`ui/seats/luozi-panels.jsx` `RunDecCard`、料库 factorlib 列表(`/factorlib/list` 或 `/screen/factors` 消费处)。
- `guanlan_v2/strategy/paths.py` `ARTIFACTS_DIR`;`var/` 路径取 `Path(__file__).resolve().parents[N]/"var"`(对齐 `_DEC_LOG` 写法 seats/api.py:161)。

---

### Task 1: factor_vintage.py — 截面 vintage IC 计算 + as-of 查表(TDD)

**Files:**
- Create: `guanlan_v2/screen/factor_vintage.py`
- Test: `tests/test_factor_vintage.py`

纯函数优先:把「as-of 取数」与「批算落盘」拆开,as-of 逻辑可纯内存测(不碰引擎/磁盘)。

- [ ] **Step 1: 写 as-of 纯函数失败测试** `tests/test_factor_vintage.py`

```python
import pandas as pd
from guanlan_v2.screen.factor_vintage import cs_vintage_from_frame

def _frame():
    # 逐日截面 IC 序列(单因子);realized_date = fwd 实现日
    return pd.DataFrame([
        {"id": "mom_20", "date": "2026-01-05", "ic": 0.10, "n": 250, "realized_date": "2026-01-12"},
        {"id": "mom_20", "date": "2026-01-06", "ic": 0.20, "n": 250, "realized_date": "2026-01-13"},
        {"id": "mom_20", "date": "2026-01-07", "ic": -0.30, "n": 250, "realized_date": "2026-02-20"},  # 未来才实现
    ])

def test_cs_vintage_only_realized():
    # D=2026-01-15:只有前两行 realized_date≤D,第三行 2026-02-20>D 必须排除
    r = cs_vintage_from_frame(_frame(), "mom_20", "2026-01-15", window=60, horizon=5, min_n=2)
    assert r is not None
    assert abs(r["ic"] - 0.15) < 1e-9   # mean(0.10,0.20),绝不含 -0.30
    assert r["n"] == 2

def test_cs_vintage_min_n_honest_none():
    # 同 D 但 min_n=3:只有 2 条已实现 < 3 → 诚实 None
    assert cs_vintage_from_frame(_frame(), "mom_20", "2026-01-15", window=60, horizon=5, min_n=3) is None

def test_cs_vintage_trailing_window():
    f = pd.DataFrame([{"id": "x", "date": f"2026-01-{d:02d}", "ic": 0.01 * i, "n": 200,
                       "realized_date": f"2026-01-{d:02d}"} for i, d in enumerate(range(1, 11), 0)])
    # window=3 → 只取 date 最近 3 条(已实现),均值=最后三 ic 的均值
    r = cs_vintage_from_frame(f, "x", "2026-02-01", window=3, horizon=0, min_n=1)
    assert r["n"] == 3
    assert abs(r["ic"] - (0.07 + 0.08 + 0.09) / 3) < 1e-9

def test_cs_vintage_missing_factor_none():
    assert cs_vintage_from_frame(_frame(), "no_such", "2026-01-15", min_n=1) is None
```

- [ ] **Step 2: 跑测试确认失败** `…python.exe -m pytest tests/test_factor_vintage.py -q` → ImportError / fail。

- [ ] **Step 3: 写 `cs_vintage_from_frame` 纯函数**

```python
# guanlan_v2/screen/factor_vintage.py  (顶部)
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

CS_IC_PARQUET = Path(__file__).resolve().parents[2] / "var" / "factor_vintage_cs_ic.parquet"
TSIC_PARQUET = Path(__file__).resolve().parents[2] / "var" / "factor_vintage_tsic.parquet"

# tsic 单票口径范围(落子固定盘;动态票未列则诚实无 tsic)
SEATS_POOL_CODES = ["SZ300750", "SH600519", "SZ002594", "SZ300308",
                    "SH601012", "SH600036", "SH605358"]


def cs_vintage_from_frame(df, factor_id: str, date: str, window: int = 60,
                          horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    """截面 vintage IC as-of(纯函数)。df 列 [id,date,ic,n,realized_date]。
    只取 realized_date≤date 的真 OOS 行,date 最近 window 条求均值;<min_n → None。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[df["id"].astype(str) == str(factor_id)].copy()
    if sub.empty:
        return None
    sub = sub[sub["realized_date"].astype(str) <= str(date)]      # OOS 闸门:绝不取 >D
    if sub.empty:
        return None
    sub = sub.sort_values("date").tail(int(window))               # trailing 窗
    if len(sub) < int(min_n):
        return None
    ics = sub["ic"].astype(float)
    m = float(ics.mean())
    return {"ic": round(m, 4), "n": int(len(sub)), "dir": (1 if m >= 0 else -1),
            "asof": str(sub["date"].iloc[-1])}
```

- [ ] **Step 4: 跑测试确认通过** `…pytest tests/test_factor_vintage.py -q`(4 例绿)。

- [ ] **Step 5: 加 `load_cs_vintage`(mtime 缓存读盘)+ `cs_vintage_asof`(读盘版包装)**

```python
_cs_cache = {"mtime": None, "df": None}

def load_cs_vintage():
    """读 cs vintage 表 → DataFrame;缺文件 → None。mtime 缓存。"""
    import pandas as pd
    p = CS_IC_PARQUET
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _cs_cache["mtime"] != mt:
        try:
            _cs_cache["df"] = pd.read_parquet(p)
            _cs_cache["mtime"] = mt
        except Exception:  # noqa: BLE001
            return None
    return _cs_cache["df"]

def cs_vintage_asof(factor_id: str, date: str, window: int = 60,
                    horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    return cs_vintage_from_frame(load_cs_vintage(), factor_id, date, window, horizon, min_n)
```

- [ ] **Step 6: 加缓存/缺文件测试**(`load_cs_vintage` 缺文件返 None;可选写临时 parquet 验证读取)。跑全绿。

- [ ] **Step 7: commit**(pytest 全绿即「提交」)。

---

### Task 2: 单票 tsic-vintage 计算 + as-of 查表(TDD)

**Files:**
- Modify: `guanlan_v2/screen/factor_vintage.py`
- Test: `tests/test_factor_vintage.py`(追加)

- [ ] **Step 1: 写 tsic as-of 纯函数失败测试**

```python
from guanlan_v2.screen.factor_vintage import tsic_vintage_from_frame

def _tsic_frame():
    import pandas as pd
    rows = []
    vals = [(1.0, 0.01), (2.0, 0.02), (3.0, 0.03), (4.0, 0.04), (5.0, 0.05)]  # fval↑ fwd↑ → +1
    for i, (fv, fw) in enumerate(vals):
        d = f"2026-01-{i+1:02d}"
        rows.append({"code": "SH605358", "id": "mom_20", "date": d, "fval": fv,
                     "fwd": fw, "realized_date": d})
    rows.append({"code": "SH605358", "id": "mom_20", "date": "2026-01-20", "fval": 99.0,
                 "fwd": -9.0, "realized_date": "2026-03-01"})  # 未来实现,必排除
    return pd.DataFrame(rows)

def test_tsic_perfect_positive_pit():
    r = tsic_vintage_from_frame(_tsic_frame(), "SH605358", "mom_20", "2026-01-31",
                                window=60, horizon=5, min_n=4)
    assert r is not None and abs(r["ic"] - 1.0) < 1e-9   # 单调正 → Spearman=1,且不含 03-01 那行
    assert r["n"] == 5

def test_tsic_scoped_code_miss():
    assert tsic_vintage_from_frame(_tsic_frame(), "SZ000001", "mom_20", "2026-01-31", min_n=1) is None

def test_tsic_min_n_none():
    assert tsic_vintage_from_frame(_tsic_frame(), "SH605358", "mom_20", "2026-01-31", min_n=10) is None
```

- [ ] **Step 2: 跑测试确认失败。**

- [ ] **Step 3: 写 `tsic_vintage_from_frame` + `load_tsic_vintage` + `tsic_vintage_asof`**

```python
def tsic_vintage_from_frame(df, code: str, factor_id: str, date: str, window: int = 60,
                            horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    """单票 tsic vintage as-of(纯函数)。df 列 [code,id,date,fval,fwd,realized_date]。
    取本票本因子 realized_date≤date 的 trailing window 行,Spearman(fval,fwd);<min_n → None。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[(df["code"].astype(str) == str(code)) & (df["id"].astype(str) == str(factor_id))].copy()
    if sub.empty:
        return None
    sub = sub[sub["realized_date"].astype(str) <= str(date)].sort_values("date").tail(int(window))
    sub = sub.dropna(subset=["fval", "fwd"])
    if len(sub) < int(min_n):
        return None
    ic = sub["fval"].rank().corr(sub["fwd"].rank())   # Spearman
    if pd.isna(ic):
        return None
    return {"ic": round(float(ic), 4), "n": int(len(sub)),
            "dir": (1 if ic >= 0 else -1), "asof": str(sub["date"].iloc[-1])}
```
`load_tsic_vintage`/`tsic_vintage_asof` 同 Task1 第 5 步 mtime 缓存范式(指向 `TSIC_PARQUET`,签名 `tsic_vintage_asof(code, factor_id, date, …)`)。

- [ ] **Step 4: 跑测试确认通过(含 Task1 共 7+ 例)。**

- [ ] **Step 5: commit。**

---

### Task 3: 批算落盘 `compute_factor_vintage` + 挂 regen step 3.6(TDD/smoke)

**Files:**
- Modify: `guanlan_v2/screen/factor_vintage.py`(加 `compute_factor_vintage` + `_realized_map`)
- Modify: `guanlan_v2/strategy/compute/regen.py`(step 3.5 块之后加 step 3.6)
- Test: `tests/test_factor_vintage.py`(追加 `_realized_map` 单测)

实现时 **Read `factor_ic.py:28-103` 照搬**面板加载块,只改:① 历史窗 `start = end - ~2年`;② 内层逐日 IC **全留**(不 `[-days:]`、不 mean)并算 `realized_date`;③ 同 panel 切 `SEATS_POOL_CODES × factorlib56` 的 (fval,fwd) 进 tsic 表。

- [ ] **Step 1: 写 `_realized_map` 单测**

```python
from guanlan_v2.screen.factor_vintage import _realized_map

def test_realized_map_horizon_shift():
    dts = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]
    m = _realized_map(dts, horizon=2)
    assert m["2026-01-05"] == "2026-01-07"     # +2 交易日
    assert m["2026-01-06"] == "2026-01-08"
    assert "2026-01-09" not in m and "2026-01-12" not in m   # 尾部 horizon 天无实现日 → 不入
```

- [ ] **Step 2: 跑确认失败。**

- [ ] **Step 3: 写 `_realized_map` + `compute_factor_vintage`**

```python
def _realized_map(uniq_dates, horizon: int) -> dict:
    """逐日 → 其 fwd 实现日(date 列表的 +horizon 位);尾部 horizon 天无实现日则不入。"""
    ds = [str(d)[:10] for d in uniq_dates]
    out = {}
    for i, d in enumerate(ds):
        if i + horizon < len(ds):
            out[d] = ds[i + horizon]
    return out


def compute_factor_vintage(universe: str = "csi300", years: float = 2.0, horizon: int = 5,
                           end: Optional[str] = None, pool_codes=None) -> dict:
    """全 catalog 逐日截面 vintage IC + watch-pool×factorlib56 tsic → 两 parquet。
    返回 {cs_rows, tsic_rows}。一次面板加载 + 一遍因子编译同产两表。"""
    import pandas as pd
    from datetime import date, timedelta
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.data.universe import resolve_universe_codes
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    from guanlan_v2.screen.catalog import FACTOR_DEFS

    pool = [str(c) for c in (pool_codes or SEATS_POOL_CODES)]
    end_d = date.fromisoformat(end) if end else date.today()
    start = (end_d - timedelta(days=int(365 * years) + 260)).isoformat()  # +260 热身
    end_s = end_d.isoformat()

    codes = sorted(set([str(c) for c in resolve_universe_codes(universe)] + pool))  # 并入 pool 保证有本票列
    loader = get_default_loader()
    panel = load_panel_cached(loader, codes, start, end_s, freq="day")
    try:
        from guanlan_v2.workflow.api import _inject_market_refs
        panel, _w = _inject_market_refs(panel, "csi300", None, start, end_s, freq="day")
    except Exception:  # noqa: BLE001
        pass

    close = compile_factor("close")(panel)
    fwd = close.groupby(level="code").shift(-horizon) / close - 1.0

    # tsic 限 factorlib 因子(catalog 内来源标记;Read catalog.py 后定 fl 判定,宁宽不漏)
    fl_ids = {fid for fid, m in FACTOR_DEFS.items()
              if str(m.get("family", "")).find("库") >= 0 or m.get("source") in ("workflow", "mined", "base")}

    cs_rows, tsic_rows = [], []
    for fid, meta in FACTOR_DEFS.items():
        expr = meta.get("expr")
        if not expr:
            continue
        try:
            fac = compile_factor(expr)(panel)
            if fac is None or not isinstance(fac, pd.Series):
                continue
            d = pd.DataFrame({"f": fac, "r": fwd, "c": fac.index.get_level_values("code"),
                              "t": fac.index.get_level_values("datetime")}).dropna(subset=["f", "r"])
            if d.empty:
                continue
            uniq = sorted(pd.Index(d["t"]).unique())
            rmap = _realized_map([str(x)[:10] for x in uniq], horizon)
            _dir = float(meta.get("dir", 1) or 1)
            for t in uniq:
                ts = str(t)[:10]
                if ts not in rmap:        # 尾部未实现 → 跳(诚实:无 realized_date 不落)
                    continue
                sub = d[d["t"] == t]
                if len(sub) >= 30:
                    ic_t = sub["f"].rank().corr(sub["r"].rank())
                    if pd.notna(ic_t):
                        cs_rows.append({"id": fid, "date": ts, "ic": round(_dir * float(ic_t), 4),
                                        "n": int(len(sub)), "realized_date": rmap[ts]})
            if fid in fl_ids:   # tsic:仅 factorlib 因子 × pool 票
                for code in pool:
                    sc = d[d["c"].astype(str) == code]
                    for _, row in sc.iterrows():
                        ts = str(row["t"])[:10]
                        if ts in rmap:
                            tsic_rows.append({"code": code, "id": fid, "date": ts,
                                              "fval": float(row["f"]), "fwd": float(row["r"]),
                                              "realized_date": rmap[ts]})
        except Exception:  # noqa: BLE001
            continue

    for rows, p in ((cs_rows, CS_IC_PARQUET), (tsic_rows, TSIC_PARQUET)):
        out = pd.DataFrame(rows)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        out.to_parquet(tmp, index=False)
        os.replace(tmp, str(p))
    return {"cs_rows": len(cs_rows), "tsic_rows": len(tsic_rows)}
```
> `fl_ids` 的 factorlib 判定按 `catalog.py` 真实标记调整(Read 后定;宁可宽松多算 tsic,pool×N 行量可控)。

- [ ] **Step 4: 跑 `_realized_map` 测试通过。**

- [ ] **Step 5: 挂 regen step 3.6**(`regen.py` step 3.5 块之后、step 4 `provenance` 之前):

```python
        # 3.6) 因子 vintage IC(逐日截面 + 单票 tsic 真 OOS;失败不阻断三产物)
        print("[regen] factor_vintage → 逐日 vintage IC(截面 csi300 + 单票 tsic)...", flush=True)
        try:
            from guanlan_v2.screen.factor_vintage import compute_factor_vintage, CS_IC_PARQUET
            n_v = compute_factor_vintage(end=end)
            out["factor_vintage"] = (n_v, str(CS_IC_PARQUET))
            print(f"  factor_vintage cs={n_v['cs_rows']} tsic={n_v['tsic_rows']} -> {CS_IC_PARQUET}", flush=True)
        except Exception as e:  # noqa: BLE001
            out["factor_vintage"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] factor_vintage 失败(不阻断): {type(e).__name__}: {e}", flush=True)
```

- [ ] **Step 6: pytest 全绿**(真批算慢留 Task6 收口跑)。**Read 提醒**:测引擎相关改动须子进程指仓内 engine(`tests/conftest.py` 已 prepend);批脚本**别** `python -`(stdin)跑(memory:多进程 `<stdin>` OSError)——用 `-c` 或文件。

- [ ] **Step 7: commit。**

---

### Task 4: decide 接线 — 因子→id resolve + vintage as-of 入 prompt + 落盘(TDD)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(rf_line 构建 ≈1143-1156;落盘 dict ≈1223-1238;新增 resolve helper + import)
- Test: `tests/test_seats_vintage_wire.py`(新)

- [ ] **Step 1: 写 resolve + rf_line 失败测试**(纯函数,monkeypatch cs/tsic_vintage_asof 避免碰盘)

```python
import guanlan_v2.seats.api as api

def test_resolve_factor_id_by_expr_then_name():
    idx = {"by_expr": {"rank(mom_20)": "mom_20"}, "by_name": {"动量20": "mom_20", "mom_20": "mom_20"}}
    assert api._resolve_factor_id({"id": "mom_20"}, idx) == "mom_20"            # 显式 id 优先
    assert api._resolve_factor_id({"expr": "rank(mom_20)"}, idx) == "mom_20"    # expr 次之
    assert api._resolve_factor_id({"name": "动量20"}, idx) == "mom_20"          # name 兜底
    assert api._resolve_factor_id({"name": "不存在"}, idx) is None

def test_rf_vintage_prefers_tsic_then_cs(monkeypatch):
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof",
                        lambda code, fid, date, **k: {"ic": 0.12, "n": 40, "dir": 1, "asof": date} if code == "SH605358" else None)
    monkeypatch.setattr(api, "cs_vintage_asof",
                        lambda fid, date, **k: {"ic": 0.05, "n": 55, "dir": 1, "asof": date})
    line, vint = api._rf_vintage_line([{"name": "动量20"}], "SH605358", "2026-03-01")
    assert "本票" in line and "IC@" in line and "0.12" in line       # tsic 优先
    assert vint[0]["kind"] == "tsic" and vint[0]["ic"] == 0.12

def test_rf_vintage_falls_to_cs_then_honest(monkeypatch):
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof", lambda *a, **k: None)
    monkeypatch.setattr(api, "cs_vintage_asof", lambda fid, date, **k: None)   # 样本不足
    line, vint = api._rf_vintage_line([{"name": "动量20"}], "SZ000001", "2026-03-01")
    assert "样本不足" in line and vint[0]["ic"] is None
```

- [ ] **Step 2: 跑确认失败。**

- [ ] **Step 3: 写 helpers + 改 rf_line**。模块顶部 import:
```python
from guanlan_v2.screen.factor_vintage import cs_vintage_asof, tsic_vintage_asof
from guanlan_v2.screen.catalog import FACTOR_DEFS
```
helper(模块级,带一次性缓存):
```python
_fid_index_cache = {"v": None}
def _factor_id_index() -> dict:
    if _fid_index_cache["v"] is None:
        by_expr, by_name = {}, {}
        for fid, m in FACTOR_DEFS.items():
            if m.get("expr"): by_expr[str(m["expr"])] = fid
            if m.get("short"): by_name[str(m["short"])] = fid
            by_name[str(fid)] = fid
        _fid_index_cache["v"] = {"by_expr": by_expr, "by_name": by_name}
    return _fid_index_cache["v"]

def _resolve_factor_id(rf: dict, index: dict):
    if rf.get("id") and rf["id"] in index["by_name"]:        # 显式 id(id 也登记进 by_name)
        return index["by_name"][rf["id"]]
    if rf.get("expr") and str(rf["expr"]) in index["by_expr"]:
        return index["by_expr"][str(rf["expr"])]
    if rf.get("name") and str(rf["name"]) in index["by_name"]:
        return index["by_name"][str(rf["name"])]
    return None

def _rf_vintage_line(recipe_factors, code: str, asof: str):
    """每因子 resolve→优先 tsic 退 cs vintage as-of;返回 (prompt 行, [vintage 记录])。"""
    idx = _factor_id_index()
    parts, vint = [], []
    for rf in (recipe_factors or [])[:8]:
        if not rf or not rf.get("name"):
            continue
        fid = _resolve_factor_id(rf, idx)
        r, kind = None, None
        if fid:
            r = tsic_vintage_asof(code, fid, asof)
            kind = "tsic" if r else None
            if not r:
                r = cs_vintage_asof(fid, asof); kind = "cs" if r else None
        if r:
            tag = "本票" if kind == "tsic" else "截面"
            parts.append(f"{rf['name']}(IC@{r['asof']}={r['ic']}·OOS·n={r['n']}·{tag})")
            vint.append({"name": rf["name"], "id": fid, "ic": r["ic"], "n": r["n"],
                         "kind": kind, "asof": r["asof"]})
        else:
            parts.append(f"{rf['name']}(IC 样本不足)")
            vint.append({"name": rf["name"], "id": fid, "ic": None, "n": 0,
                         "kind": None, "asof": asof})
    return ("; ".join(parts) or "无"), vint
```
decide 内:`rf_line, _rf_vint = _rf_vintage_line(recipe_factors, c, asof)` **替换**原 `rf_line` 构建块(≈1143-1156);prompt 标签从「未做确定性回测」改「vintage OOS IC·供研判参考·不进信号(P3)」。落盘 dict 加 `"recipe_factors_vintage": _rf_vint,`。

- [ ] **Step 4: 跑测试通过。**

- [ ] **Step 5: 全量 pytest 无回归(`-q`)。commit。**

---

### Task 5: 前端 — RunDecCard 显 as-of vintage + recipeForStrategy 传 id(+料库尽力)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(`recipeForStrategy` 因子项加 `id`)
- Modify: `ui/seats/luozi-app.jsx`(`runDecs` 透传 `recipe_factors_vintage`)
- Modify: `ui/seats/luozi-panels.jsx`(`RunDecCard` 加 vintage 字段)
- Modify: `ui/seats/观澜 · 落子.html`(bump 改动文件 `?v=20260614c`)

- [ ] **Step 1: `recipeForStrategy` 因子项加 `id`**(`luozi-data.jsx:183-206`):
```javascript
if (a.type === 'factor')
  factors.push({ id: a.id, name: a.title, ic: (a.ic || ''), expr: (a.expr || '') });  // +id 供后端 resolve vintage
```

- [ ] **Step 2: `runDecs` 透传**(`luozi-app.jsx`,同 P1 regime_asof 行附近):
```javascript
recipe_factors_vintage: r.recipe_factors_vintage || [],
```

- [ ] **Step 3: `RunDecCard` 加 vintage 字段**(panels.jsx,「配方因子(供参考·未回测)」附近):
```jsx
{(dec.recipe_factors_vintage || []).length > 0 && (
  <Field label="配方因子 vintage IC(as-of·真OOS)">
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {dec.recipe_factors_vintage.map((f, i) => (
        <span key={i} className="mono" style={{ fontSize: 8.5, color: f.ic == null ? 'var(--ink-3)' : 'var(--yin)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>
          {f.name}{f.ic == null ? ' · 样本不足' : ` · IC@${f.asof}=${f.ic} · n${f.n} · ${f.kind === 'tsic' ? '本票' : '截面'}`}
        </span>
      ))}
    </div>
  </Field>
)}
```

- [ ] **Step 4: 料库显最新 vintage(尽力,超界挂账)**:Grep 定位料库 factorlib 渲染处。**最小实现**:新增后端 `GET /factor/vintage_latest`(读 cs 表每 id 末日做 `cs_vintage_asof(id, 最新日)` → `{id:{ic,n,asof}}`)+ 前端列表合并显「vintage OOS·csi300」。若工作量超出本任务范围,**本步只做 Step1-3**,料库显形记 README 挂账 P2b(不留半成品)。

- [ ] **Step 5: bump `?v`**(改动的 jsx → `20260614c`,用 Edit)。

- [ ] **Step 6: 浏览器载入 0 console error + 字节级确认改动**(Task6 完整 e2e)。

---

### Task 6: 收口 — pytest + 真跑 regen + 重启 + 浏览器 e2e + 文档 + memory

- [ ] **Step 1: 全量 pytest 绿**(`…python.exe -m pytest -q`,预期 262 + vintage/wire 新增)。
- [ ] **Step 2: 真跑批算**(子进程,非 stdin):`…python.exe -c "from guanlan_v2.screen.factor_vintage import compute_factor_vintage; print(compute_factor_vintage(end='2026-06-09'))"` → 产 `var/factor_vintage_cs_ic.parquet` + `factor_vintage_tsic.parquet`,打印 cs/tsic 行数(cs 数千~万、tsic pool×56×日)。
- [ ] **Step 3: 重启 9999**(杀监听 PID 等 ~10s 看门狗拉新 api.py)。
- [ ] **Step 4: 后端 live PIT 验证**:立昂微 SH605358 绑动量类因子,POST `/seats/decide`(mode=fast,带 run_id)两个相隔较远的决策日;读 `var/seats_decisions.jsonl` diff `recipe_factors_vintage`——**早日 IC≠晚日 IC(或早日样本不足),且任一笔 IC 的 asof ≤ 决策日(PIT 铁证)**。验完删该验证 run 行(自注入测试产物,清理,沿用 P1 收口做法)。
- [ ] **Step 5: 浏览器 e2e**:`观澜·落子.html?v=20260614c` 0 console error;选 run 看 RunDecCard「配方因子 vintage IC」字段显形(IC@asof/本票或截面/样本不足)。截图存档。
- [ ] **Step 6: 文档**:`ui/seats/README.md` 加 2026-06-14 P2 条目(vintage 双口径 + regen 3.6 + decide as-of + realized_date OOS 闸门 + 验证铁证 + 料库显形是否挂账)。
- [ ] **Step 7: memory**:`backtest-cards-design.md` P2 改「已交付」+ 关键坑;`MEMORY.md` 索引行更新。
- [ ] **Step 8: 最终 code review**(整 P2:`ecc:python-reviewer` 审 `factor_vintage.py` + decide 接线;`ecc:react-reviewer` 审前端三处)。

---

## Self-Review(写完计划自查)

- **spec 覆盖**:截面 vintage(T1)✓ 单票 tsic(T2)✓ regen 批算(T3)✓ decide as-of 接线(T4)✓ 前端显形(T5)✓ realized_date OOS 闸门(T1/T2/T3 贯穿)✓ 诚实样本不足(T1/T2/T4)✓ 删伪 IC(P1 已删,T5 核查)✓。
- **类型一致**:`cs_vintage_asof(id,date,…)→{ic,n,dir,asof}`、`tsic_vintage_asof(code,id,date,…)→{ic,n,dir,asof}`、`compute_factor_vintage→{cs_rows,tsic_rows}`、落盘/前端键 `recipe_factors_vintage:[{name,id,ic,n,kind,asof}]` 全程一致;`_rf_vintage_line(recipe_factors,code,asof)→(str,list)`。
- **占位扫描**:T5 Step4(料库显形)标「超界则挂账 P2b」=有意降级非占位;`fl_ids` 判定标「Read catalog.py 后定」=实现期细化非 TODO。
- **PIT 红线**:realized_date≤D 闸门在 cs/tsic 纯函数 + 批算 `_realized_map` 三处守;decide 用 asof(决策日)查表;测试显式造 realized_date>D 行验证排除。
