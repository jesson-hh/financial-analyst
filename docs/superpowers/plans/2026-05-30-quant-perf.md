# 量化提速 (并行加载 + 面板缓存 + 快测池) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把交互式因子评测从「每点一次 48-86s」降到「首次 ~13s、同池之后每个因子 ~0.5s」, 纯 Python 不丢依赖/测试。

**Architecture:** A 把 `PanelData.from_loader` 的逐只加载循环改 `ThreadPoolExecutor` 并行 (文件 I/O 释放 GIL, 实测 6.6x); B 新增 `panel_cache.py` 的 LRU `load_panel_cached`, REST 三热点 (report/compose/bench) 改调它 (同池第 2 因子 209x); C 加 ~100 只快测池设 UI 默认。

**Tech Stack:** Python 3.11+, 标准库 `concurrent.futures.ThreadPoolExecutor` / `threading.Lock` / `collections.OrderedDict` / `hashlib`; pandas; pytest。`cd /g/financial-analyst && python -m pytest`。

**实测基线 (csi300_active 868 只 × 2yr = 420,112 行):** 顺序加载 85.8s / 因子计算 0.55s / 并行加载 13.0s / 缓存复用 0.41s。

---

## File Structure

- `src/financial_analyst/data/loaders/qlib_binary.py` — 改: `_load_calendar` 加 `threading.Lock` (显式线程安全)。
- `src/financial_analyst/factors/zoo/panel.py` — 改: `from_loader` + `_merge_daily_basic` 并行化。
- `src/financial_analyst/factors/zoo/panel_cache.py` — 新: `load_panel_cached` + `clear_panel_cache` (LRU)。
- `src/financial_analyst/factors/eval/report.py:195` / `factors/compose/compose.py:164` / `buddy/server.py:1254` — 改: `from_loader(...)` → `load_panel_cached(...)`。
- `src/financial_analyst/config/universes/csi_fast.txt` — 新: ~100 大盘股。
- `src/financial_analyst/ui/quant.jsx` + `ui/quant.html` — 改: POOLS 加「快测」设默认 + bump `?v=`。
- `tests/test_panel_parallel_load.py` / `tests/test_panel_cache.py` — 新; `tests/test_universe_resolve.py` — 加 1 例。

---

## Task 1: 日历缓存线程安全 (qlib_binary.py)

**Files:**
- Modify: `src/financial_analyst/data/loaders/qlib_binary.py` (`__init__` ~119, `_load_calendar` ~125-141)
- Test: `tests/test_panel_parallel_load.py` (新建, 本任务先放一个 calendar 并发测)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_panel_parallel_load.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import pytest


def test_calendar_concurrent_load_is_consistent(tmp_path):
    """16 线程并发首次加载日历, 结果一致且不崩 (幂等竞争防御)。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    (tmp_path / "calendars").mkdir()
    (tmp_path / "calendars" / "day.txt").write_text(
        "\n".join(f"2024-01-{d:02d}" for d in range(1, 21)), encoding="utf-8")
    (tmp_path / "features").mkdir()
    loader = QlibBinaryLoader(str(tmp_path))
    with ThreadPoolExecutor(max_workers=16) as ex:
        cals = list(ex.map(lambda _: loader._load_calendar("day"), range(64)))
    assert all(len(c) == 20 for c in cals)
    assert cals[0] == cals[-1]
```

- [ ] **Step 2: 跑测试看现状**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_parallel_load.py::test_calendar_concurrent_load_is_consistent -v`
Expected: 现状很可能已 PASS (良性竞争); 仍继续加锁让线程安全显式化 (本任务目的是契约化)。

- [ ] **Step 3: 给 `_load_calendar` 加锁**

`__init__` 末尾 (在 `self._calendars: Dict[...] = {}` 之后) 加:
```python
        import threading
        self._calendar_lock = threading.Lock()
```
`_load_calendar` 改为双重检查锁:
```python
    def _load_calendar(self, freq: str = "day") -> List[pd.Timestamp]:
        """Return the calendar for *freq*, loading from disk on first call (thread-safe)."""
        cached = self._calendars.get(freq)
        if cached is not None:
            return cached
        with self._calendar_lock:
            cached = self._calendars.get(freq)   # 双重检查
            if cached is not None:
                return cached
            if freq not in self._roots:
                raise ValueError(
                    f"freq={freq!r} not configured in provider_uri "
                    f"(available: {list(self._roots)})"
                )
            cal_fname = _CALENDAR_FILE.get(freq)
            if cal_fname is None:
                raise ValueError(f"Unknown freq: {freq!r}")
            cal_path = self._roots[freq] / "calendars" / cal_fname
            with open(cal_path, "r", encoding="utf-8") as f:
                stamps = [pd.Timestamp(line.strip()) for line in f if line.strip()]
            self._calendars[freq] = stamps
            return stamps
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_parallel_load.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/loaders/qlib_binary.py tests/test_panel_parallel_load.py
git commit -m "perf(loader): _load_calendar 双重检查锁, 显式线程安全 (并行加载前置)"
```

---

## Task 2: from_loader 并行化 (panel.py)

**Files:**
- Modify: `src/financial_analyst/factors/zoo/panel.py` (顶部 import; `from_loader` ~219-285; `_merge_daily_basic` ~30-60)
- Test: `tests/test_panel_parallel_load.py` (加正确性测)

- [ ] **Step 1: 写失败测试 — 并行结果 == 顺序参考 + 部分失败仍 skip**

```python
# 追加到 tests/test_panel_parallel_load.py
import numpy as np
import pandas as pd


def _stub_loader(fail_codes=()):
    class L:
        def fetch_quote(self, code, start, end, freq="day"):
            if code in fail_codes:
                raise RuntimeError("boom")
            dates = pd.date_range("2024-01-02", periods=30, freq="B")
            base = abs(hash(code)) % 100 + 1
            df = pd.DataFrame({
                "open": np.arange(30) + base, "high": np.arange(30) + base + 1,
                "low": np.arange(30) + base - 1, "close": np.arange(30) + base,
                "volume": np.full(30, 1e6),
            }, index=dates)
            df.index.name = "datetime"
            return df
        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return L()


def test_parallel_equals_sequential():
    from financial_analyst.factors.zoo.panel import PanelData
    codes = [f"SH60{i:04d}" for i in range(40)]
    panel = PanelData.from_loader(_stub_loader(), codes, "2024-01-01", "2024-03-01", freq="day")
    df = panel.df
    assert df.index.names == ["datetime", "code"]
    assert df.index.get_level_values("code").nunique() == 40
    assert df.shape[0] == 40 * 30
    panel2 = PanelData.from_loader(_stub_loader(), codes, "2024-01-01", "2024-03-01", freq="day")
    pd.testing.assert_frame_equal(df, panel2.df)


def test_parallel_skips_failures():
    from financial_analyst.factors.zoo.panel import PanelData
    codes = [f"SH60{i:04d}" for i in range(10)]
    fail = {codes[3], codes[7]}
    panel = PanelData.from_loader(_stub_loader(fail), codes, "2024-01-01", "2024-03-01", freq="day")
    got = set(panel.df.index.get_level_values("code").unique())
    assert got == set(codes) - fail
```

- [ ] **Step 2: 跑确认现状语义 PASS (顺序版应满足)**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_parallel_load.py -v`
Expected: 顺序版应 PASS (语义不变); 本任务把实现换成并行且保持 PASS。

- [ ] **Step 3: 并行化 `from_loader`**

`panel.py` 顶部确保有:
```python
import os
from concurrent.futures import ThreadPoolExecutor
```
把 `from_loader` 里 `for code in codes:` 那段逐只处理抽成内嵌 worker, 用线程池跑 (其余 industry/benchmark/_merge_daily_basic/return 不变):
```python
        def _load_one(code):
            try:
                df = loader.fetch_quote(code, start, end, freq=freq)
            except Exception as e:
                return None, (code, str(e)[:80])
            if df is None or len(df) == 0:
                return None, (code, "empty")
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level="code" if "code" in df.index.names else 0, drop=True)
            df = df.copy()
            if "trade_date" in df.columns:
                df = df.set_index("trade_date")
                df.index = pd.DatetimeIndex(df.index)
                df = df[~df.index.duplicated(keep="last")]
            df["code"] = code
            df = df.set_index("code", append=True)
            df.index = df.index.set_names(["datetime", "code"])
            return df, None

        frames = []
        skipped: list[tuple[str, str]] = []
        _workers = min(16, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=_workers) as _ex:
            for _df, _skip in _ex.map(_load_one, codes):
                if _skip is not None:
                    skipped.append(_skip)
                else:
                    frames.append(_df)
```
(下接原有 `if not frames: raise ...` / `panel = pd.concat(frames).sort_index()` / industry / benchmark / `_merge_daily_basic` / `return cls(panel)` — 全不动。)

- [ ] **Step 4: 并行化 `_merge_daily_basic` 的逐只 fetch**

把 `_merge_daily_basic` 里 `for code in codes:` 逐只 `loader.fetch_daily_basic(code, ...)` 抽成 worker 并行 (其余拼接/reindex 不变):
```python
    def _one(code):
        try:
            db = loader.fetch_daily_basic(code, start, end)
        except Exception:
            return None
        if db is None or len(db) == 0:
            return None
        db = db.copy()
        if "trade_date" in db.columns:
            db = db.set_index("trade_date")
        try:
            db.index = pd.DatetimeIndex(db.index)
        except Exception:
            return None
        db["code"] = code
        db = db.set_index("code", append=True)
        db.index = db.index.set_names(["datetime", "code"])
        keep = [c for c in _DAILY_BASIC_FIELDS if c in db.columns]
        return db[keep] if keep else None

    import os as _os
    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=min(16, (_os.cpu_count() or 4) * 2)) as _ex:
        frames = [f for f in _ex.map(_one, codes) if f is not None]
    n_ok = len(frames)
```
(下接原有 `if not frames: return` / `db_all = pd.concat(frames)` / 去重 / reindex 赋列 — 不动。)

- [ ] **Step 5: 跑测试 + 关键回归**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_parallel_load.py tests/test_factor_report_tool.py tests/test_compose.py tests/test_factor_zoo.py -v`
Expected: 全 PASS (test_factor_zoo 必须 28 全过 = 注册表无污染)。

- [ ] **Step 6: 提交**

```bash
git add src/financial_analyst/factors/zoo/panel.py tests/test_panel_parallel_load.py
git commit -m "perf(panel): from_loader + _merge_daily_basic 线程池并行 (85s->13s, 结果不变)"
```

---

## Task 3: panel_cache 模块 (新)

**Files:**
- Create: `src/financial_analyst/factors/zoo/panel_cache.py`
- Test: `tests/test_panel_cache.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_panel_cache.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import pytest


def _stub_loader(counter):
    class L:
        def fetch_quote(self, code, start, end, freq="day"):
            counter["n"] += 1
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            df = pd.DataFrame({"open": np.arange(20), "high": np.arange(20),
                               "low": np.arange(20), "close": np.arange(20),
                               "volume": np.full(20, 1e6)}, index=dates)
            df.index.name = "datetime"
            return df
        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return L()


@pytest.fixture(autouse=True)
def _clear():
    from financial_analyst.factors.zoo.panel_cache import clear_panel_cache
    clear_panel_cache(); yield; clear_panel_cache()


def test_hit_reuses_panel():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}; loader = _stub_loader(cnt); codes = ["SH600000", "SZ000001"]
    p1 = load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    p2 = load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    assert p1 is p2
    assert cnt["n"] == 2


def test_miss_on_different_window():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}; loader = _stub_loader(cnt); codes = ["SH600000"]
    load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    load_panel_cached(loader, codes, "2024-01-01", "2024-03-01")
    assert cnt["n"] == 2


def test_lru_evicts_oldest():
    from financial_analyst.factors.zoo import panel_cache as pc
    pc.clear_panel_cache()
    cnt = {"n": 0}; loader = _stub_loader(cnt)
    for i in range(pc._MAXSIZE + 1):
        pc.load_panel_cached(loader, [f"SH60{i:04d}"], "2024-01-01", "2024-02-01")
    n_before = cnt["n"]
    pc.load_panel_cached(loader, ["SH600000"], "2024-01-01", "2024-02-01")
    assert cnt["n"] == n_before + 1


def test_concurrent_no_crash():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}; loader = _stub_loader(cnt); codes = ["SH600000", "SZ000001"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda _: load_panel_cached(loader, codes, "2024-01-01", "2024-02-01"), range(32)))
    assert all(r is not None for r in res)
```

- [ ] **Step 2: 跑确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_cache.py -v`
Expected: FAIL (`No module named ...panel_cache`)

- [ ] **Step 3: 实现 panel_cache.py**

```python
# src/financial_analyst/factors/zoo/panel_cache.py
"""LRU 面板缓存 — 同一 (codes, 窗口, freq) 只加载一次, 交互式多因子复用。

线程安全 (server 把 sync 端点跑在线程池)。慢加载在锁外执行, 仅 OrderedDict
读写持锁; 同 key 并发首次可能重复加载一次 (幂等, 不影响正确性)。
"""
from __future__ import annotations
import hashlib
import threading
from collections import OrderedDict
from typing import List

from financial_analyst.factors.zoo.panel import PanelData

_MAXSIZE = 3                       # 每面板 ~50-100MB → 上限 ~300MB
_cache: "OrderedDict[tuple, PanelData]" = OrderedDict()
_lock = threading.Lock()


def _key(codes: List[str], start: str, end: str, freq: str, with_industry: bool) -> tuple:
    h = hashlib.md5(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    return (h, start, end, freq, with_industry)


def load_panel_cached(loader, codes: List[str], start: str, end: str,
                      freq: str = "day", industry_loader=None) -> PanelData:
    """命中则复用缓存面板, 否则 PanelData.from_loader 加载并存入 (LRU)。"""
    k = _key(codes, start, end, freq, industry_loader is not None)
    with _lock:
        hit = _cache.get(k)
        if hit is not None:
            _cache.move_to_end(k)
            return hit
    panel = PanelData.from_loader(loader, codes, start, end, freq=freq,
                                  industry_loader=industry_loader)
    with _lock:
        _cache[k] = panel
        _cache.move_to_end(k)
        while len(_cache) > _MAXSIZE:
            _cache.popitem(last=False)
    return panel


def clear_panel_cache() -> None:
    with _lock:
        _cache.clear()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_cache.py -v`
Expected: PASS (4 测)

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/factors/zoo/panel_cache.py tests/test_panel_cache.py
git commit -m "perf(cache): panel_cache LRU 面板缓存 (load_panel_cached + clear, 线程安全)"
```

---

## Task 4: 三热点接入缓存 (report / compose / bench)

**Files:**
- Modify: `src/financial_analyst/factors/eval/report.py:195`
- Modify: `src/financial_analyst/factors/compose/compose.py:164-166`
- Modify: `src/financial_analyst/buddy/server.py:1254-1255`
- Test: `tests/test_panel_cache.py` (加端到端命中测)

- [ ] **Step 1: 写失败测试 — factor_report 同参第 2 次不重新加载**

```python
# 追加到 tests/test_panel_cache.py
def test_factor_report_uses_cache(monkeypatch):
    import financial_analyst.data.universe as univ
    import financial_analyst.data.loader_factory as lf
    from financial_analyst.factors.zoo.panel_cache import clear_panel_cache
    from financial_analyst.factors.eval.report import factor_report
    from financial_analyst.factors.eval.config import EvalConfig
    clear_panel_cache()
    cnt = {"n": 0}
    monkeypatch.setattr(univ, "resolve_universe_codes", lambda u: ["SH600000", "SZ000001", "SH600036"])
    monkeypatch.setattr(lf, "get_default_loader", lambda: _stub_loader(cnt))
    cfg = EvalConfig(universe="x", freq="day", start="2024-01-01", end="2024-03-01")
    factor_report("rank(close)", cfg)
    n1 = cnt["n"]
    factor_report("rank(-close)", cfg)
    assert cnt["n"] == n1
```

- [ ] **Step 2: 跑确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_cache.py::test_factor_report_uses_cache -v`
Expected: FAIL (现 factor_report 直调 from_loader, 第二次 cnt 仍增加)

- [ ] **Step 3: 三处 `PanelData.from_loader(...)` → `load_panel_cached(...)`**

每处签名完全一致, 仅换函数名并加局部 import。

`report.py` (~195, factor_report 内 from_loader 调用前):
```python
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        panel = load_panel_cached(loader, codes, start, end, freq="day", industry_loader=ind_loader)
```
`compose.py` (~164-166):
```python
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        panel = load_panel_cached(
            loader, codes, start, end, freq="day", industry_loader=ind_loader
        )
```
`server.py` (~1254-1255, bench 端点内):
```python
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            panel = load_panel_cached(loader, codes, since, until,
                                      freq="day", industry_loader=ind)
```
(局部 import 与现有 monkeypatch-friendly 风格一致; `load_panel_cached` 内部仍走 `PanelData.from_loader`, stub 计数测有效。)

- [ ] **Step 4: 跑测试 + REST/compose 回归**

Run: `cd /g/financial-analyst && python -m pytest tests/test_panel_cache.py tests/test_factor_rest.py tests/test_compose.py tests/test_factor_report_tool.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/factors/eval/report.py src/financial_analyst/factors/compose/compose.py src/financial_analyst/buddy/server.py tests/test_panel_cache.py
git commit -m "perf(cache): report/compose/bench 三热点接入 load_panel_cached (同池第2因子秒出)"
```

---

## Task 5: 快测池 (C, 可独立)

**Files:**
- Create: `src/financial_analyst/config/universes/csi_fast.txt`
- Modify: `src/financial_analyst/ui/quant.jsx` (~38-40; 三处 `useState('csi300')`)
- Modify: `src/financial_analyst/ui/quant.html` (bump `?v=`)
- Test: `tests/test_universe_resolve.py` (加 1 例)

- [ ] **Step 1: 生成 csi_fast.txt (csi300 前 100)**

```bash
cd /g/financial-analyst && python -c "
from financial_analyst.data.universe import resolve_universe_codes
codes = resolve_universe_codes('csi300')[:100]
open('src/financial_analyst/config/universes/csi_fast.txt','w',encoding='utf-8').write('\n'.join(codes)+'\n')
print('wrote', len(codes), 'codes')
"
```
Expected: `wrote 100 codes`

- [ ] **Step 2: 写测试**

```python
# 追加到 tests/test_universe_resolve.py
def test_resolves_csi_fast_pool():
    codes = resolve_universe_codes("csi_fast")
    assert 80 <= len(codes) <= 100
    assert all(c[:2] in ("SH", "SZ", "BJ") for c in codes)
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_universe_resolve.py -v`
Expected: PASS (csi_fast.txt 走 find_config / bundled 路径解析)

- [ ] **Step 4: UI 加「快测」池设默认**

`quant.jsx` ~38-40:
```javascript
const POOLS = ['快测', 'csi300', 'csi500', 'csi800', 'all'];
const POOL_DEFAULT = 'csi300_active';
const poolParam = (p) => (p === '快测' ? 'csi_fast' : (p === 'csi300' ? POOL_DEFAULT : p));
```
三处 `const [pool, setPool] = useState('csi300');` 改 `useState('快测')`。
`quant.html` 把 `quant.jsx?v=20260530-1` bump 成 `?v=20260530-2`。

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/config/universes/csi_fast.txt src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html tests/test_universe_resolve.py
git commit -m "perf(ui): 快测池 csi_fast (~100 大盘) 设默认, 首次评测几秒"
```

---

## Task 6: 全量回归 + 时延 sanity

**Files:** 无 (仅验证)

- [ ] **Step 1: 全量回归**

Run: `cd /g/financial-analyst && python -m pytest tests/ -q`
Expected: `97x passed` (970 基线 + 本计划新增测), 0 fail。

- [ ] **Step 2: 时延 sanity (真实数据, 非 pytest)**

```bash
cd /g/financial-analyst && NO_PROXY=* python -c "
import time
from financial_analyst.factors.eval.report import factor_report
from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.zoo.panel_cache import clear_panel_cache
clear_panel_cache()
cfg = EvalConfig(universe='csi300_active', freq='day', start='2024-05-30', end='2026-05-30')
t=time.time(); factor_report('alpha003', cfg); print(f'first  {time.time()-t:.1f}s')
t=time.time(); factor_report('alpha006', cfg); print(f'cached {time.time()-t:.1f}s')
"
```
Expected: `first ~13s` (并行), `cached <1s` (缓存命中)。

- [ ] **Step 3: 推送**

```bash
cd /g/financial-analyst && git push origin main
```

---

## 备注
- A 对所有 14 处 `from_loader` 调用透明提速; B 只接入 3 个 REST 热点 (其余 cli/snapshot/event/tools 不变, 非交互热路径)。
- 缓存不可变契约: build_report/compose 只读面板 (产新 Series, 不 in-place 改); Task 4 如发现某调用方原地改面板, 该处先 `panel.df.copy()`。
- 量化逻辑零改动 → A 的并行结果等价性 (Task 2 测) 是「全量 970 不变」的保证。
