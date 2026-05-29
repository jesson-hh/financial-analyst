# 事件信号 (SP-B.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让事件触发型因子可在 DSL 表达 (`cross` 算子) 且被事件研究 (event study) 正确评测, 经 agent 工具 + `POST /factor/event` 暴露。

**Architecture:** `cross` 加进 `operators.py`/`expr.py` 白名单。新 `factors/eval/event.py` 提供纯 `build_event_report` (事件后 horizon 前向收益, 原始+市场调整 excess, CAR, 逐年) + I/O `event_report` (镜像 `factor_report`)。agent 工具 + REST 端点复用引擎。永不抛, 结构化错误态。

**Tech Stack:** Python / pandas / numpy / FastAPI TestClient。无新依赖 (复用 `forward_simple_returns`)。

**纪律:** 测试用 `D:\app\miniconda` python (pandas 2.3.3) 在 `G:\financial-analyst` 跑 `python -m pytest`; 不用 pandas≥2.2-only API; 不用 `_clear_registry_for_tests` (用 `unregister`); 控制端自己复跑不轻信 subagent。

---

## File Structure

- **Create** `src/financial_analyst/factors/eval/event.py` — 事件研究引擎 (EventHorizon/EventReport + build_event_report 纯 + event_report I/O)。
- **Modify** `src/financial_analyst/factors/zoo/operators.py` — 加 `cross`。
- **Modify** `src/financial_analyst/factors/zoo/expr.py` — `FACTOR_VOCAB` + `compile_factor` ns 加 `cross`。
- **Modify** `src/financial_analyst/factors/eval/__init__.py` — 导出 `EventReport`/`event_report`/`build_event_report`。
- **Modify** `src/financial_analyst/buddy/tools.py` — 加 `event_report` 工具 + Tool 注册。
- **Modify** `src/financial_analyst/factors/forge/forge.py` — `_SYSTEM` out_of_vocab 措辞改指向 event_report。
- **Modify** `src/financial_analyst/buddy/server.py` — `EventReq` + `POST /factor/event`。
- **Create** `tests/test_event_signals.py` — cross + 引擎 + I/O + REST 全套。

---

## Task 1: DSL `cross` 算子

**Files:**
- Create: `tests/test_event_signals.py`
- Modify: `src/financial_analyst/factors/zoo/operators.py` (加于 `filter_where` 之后, 文件末)
- Modify: `src/financial_analyst/factors/zoo/expr.py:8-15` (FACTOR_VOCAB) + `:50-53` (ns)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_event_signals.py`:

```python
"""SP-B.2 事件信号: cross 算子 + 事件研究引擎 + I/O + REST。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import financial_analyst.factors.zoo  # noqa: F401  (注册 alpha families)
from financial_analyst.factors.zoo import operators as ops
from financial_analyst.factors.zoo.panel import PanelData


def _series(code, vals):
    dates = pd.date_range("2024-01-02", periods=len(vals), freq="B")
    idx = pd.MultiIndex.from_product([dates, [code]], names=["datetime", "code"])
    return pd.Series(vals, index=idx, dtype=float)


def test_cross_up_and_down():
    a = _series("A", [1, 1, 3, 3, 1])
    b = _series("A", [2, 2, 2, 2, 2])
    up = ops.cross(a, b)            # a 上穿 b
    assert list(up.values) == [0.0, 0.0, 1.0, 0.0, 0.0]   # 仅 idx2 上穿
    down = ops.cross(b, a)          # 死叉 = 反向
    assert down.iloc[4] == 1.0 and down.iloc[2] == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_signals.py::test_cross_up_and_down -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'cross'`)

- [ ] **Step 3: 实现 `cross`**

在 `operators.py` 末尾 (filter_where 之后) 加:

```python
def cross(a, b):
    """a 上穿 b: a[t-1] <= b[t-1] 且 a[t] > b[t] → 1.0, 否则 0.0 (逐 code)。

    金叉 = cross(dif, dea) / 突破均线 = cross(close, sma(close,20)); 死叉 = cross(b, a)。
    a, b 为同 panel 索引的 Series (delay 已逐 code shift)。
    """
    prev_a = delay(a, 1) if hasattr(a, "index") else a
    prev_b = delay(b, 1) if hasattr(b, "index") else b
    up = (a > b) & (prev_a <= prev_b)
    return up.astype(float)
```

- [ ] **Step 4: 注册到 DSL 白名单**

`expr.py` `FACTOR_VOCAB` 算子段 (`:13` `max_pair min_pair filter_where` 行) 改为追加 `cross(x,y)`:

```python
            "log sign abs power(x,p) scale indneutralize(x,industry) max_pair min_pair filter_where cross(x,y) | "
```

`expr.py` `compile_factor` ns (`:51-52` `filter_where` 行) 追加:

```python
            "max_pair": _ops.max_pair, "min_pair": _ops.min_pair,
            "filter_where": _ops.filter_where, "cross": _ops.cross,
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_event_signals.py::test_cross_up_and_down -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/factors/zoo/operators.py src/financial_analyst/factors/zoo/expr.py tests/test_event_signals.py
git -C G:\financial-analyst commit -m "feat(dsl): cross(a,b) operator for crossover/breakout event triggers"
```

## Task 2: 事件研究引擎 `event.py` (build_event_report 纯)

**Files:**
- Create: `src/financial_analyst/factors/eval/event.py`
- Test: `tests/test_event_signals.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_event_signals.py` (含一个可手算的 planted-trigger 面板):

```python
from financial_analyst.factors.eval.config import EvalConfig


def _event_panel():
    """A 涨/B 跌/C 微涨, 触发(volume>1.5e6)只在 d0 的 A、B → 2 个事件, h=1 收益可手算。"""
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    rows = {
        "A": [10, 12, 12, 12, 12],   # d0→d1 +20%
        "B": [10, 8, 8, 8, 8],       # d0→d1 -20%
        "C": [10, 11, 11, 11, 11],   # d0→d1 +10% (非事件)
    }
    frames = []
    for code, close in rows.items():
        idx = pd.MultiIndex.from_product([dates, [code]], names=["datetime", "code"])
        vol = [2e6 if (code in ("A", "B")) and i == 0 else 1e6 for i in range(len(dates))]
        frames.append(pd.DataFrame({"open": close, "high": [c * 1.01 for c in close],
                                    "low": [c * 0.99 for c in close], "close": close,
                                    "volume": vol}, index=idx))
    return PanelData(pd.concat(frames).sort_index())


def test_build_event_report_known_returns():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    trigger = lambda panel: (panel.volume > 1.5e6).astype(float)
    rpt = build_event_report(p, trigger, EvalConfig(universe="test"),
                             factor_label="volspike", horizons=(1,))
    assert rpt.status == "ok"
    assert rpt.n_events == 2                       # (d0,A),(d0,B)
    h1 = rpt.horizons[0]
    assert h1.h == 1 and h1.n == 2
    assert h1.mean_ret == pytest.approx(0.0, abs=1e-9)    # (+0.2 - 0.2)/2
    assert h1.win_rate == pytest.approx(0.5)
    # 市场调整: 同日全市场均值 = (0.2-0.2+0.1)/3 = +0.0333 → excess 均值 = -0.0333
    assert h1.mean_excess == pytest.approx(-1 / 30, abs=1e-6)
    assert h1.mean_ret != pytest.approx(h1.mean_excess)   # 证明减了市场
    assert rpt.car_curve == [(1, pytest.approx(-1 / 30, abs=1e-6))]


def test_build_event_report_no_events():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    rpt = build_event_report(p, lambda panel: (panel.close < 0).astype(float),
                             EvalConfig(universe="test"))
    assert rpt.status == "no_events" and rpt.n_events == 0


def test_build_event_report_high_rate_warns():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    rpt = build_event_report(p, lambda panel: (panel.close > 0).astype(float),  # 恒触发
                             EvalConfig(universe="test"), horizons=(1,))
    assert rpt.status == "ok"
    assert any("更像连续因子" in w for w in rpt.warnings)


def test_build_event_report_compute_error():
    from financial_analyst.factors.eval.event import build_event_report
    p = _event_panel()
    def boom(panel):
        raise RuntimeError("synthetic boom")
    rpt = build_event_report(p, boom, EvalConfig(universe="test"))
    assert rpt.status == "compute_error" and "synthetic boom" in rpt.error
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_signals.py -k build_event_report -v`
Expected: FAIL (`ModuleNotFoundError: ...factors.eval.event`)

- [ ] **Step 3: 实现 `event.py`**

写 `src/financial_analyst/factors/eval/event.py`:

```python
"""事件信号研究 (SP-B.2) — 把触发型因子当事件做 event study。

截面 IC/十分位对稀疏布尔触发是错口径; 这里统计每次触发后 horizon 日的前向收益
(原始 + 市场调整 excess)、CAR 曲线、逐年稳定性。build_event_report 纯 (合成面板
可单测); event_report 做 I/O。永不抛, 结构化错误态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, List, Optional, Tuple

import pandas as pd

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import forward_simple_returns

_PRIMARY_H = 5  # by_year 用的主 horizon


@dataclass
class EventHorizon:
    h: int
    n: int
    mean_ret: float = float("nan")
    mean_excess: float = float("nan")
    win_rate: float = float("nan")
    t_stat: float = float("nan")


@dataclass
class EventReport:
    factor: str
    universe: str
    start: str
    end: str
    n_dates: int
    n_codes: int
    n_events: int
    event_rate: float = float("nan")
    horizons: List[EventHorizon] = field(default_factory=list)
    car_curve: List[Tuple[int, float]] = field(default_factory=list)
    by_year: List[Tuple[str, int, float]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def _excess(fwd: pd.Series) -> pd.Series:
    """逐日减等权全市场前向收益 → 市场调整 (abnormal)。"""
    mkt = fwd.groupby(level="datetime").transform("mean")
    return fwd - mkt


def _stats(raw: pd.Series, exc: pd.Series):
    n = int(raw.shape[0])
    if n == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    mean_ret, mean_exc, win = float(raw.mean()), float(exc.mean()), float((raw > 0).mean())
    if n >= 2:
        sd = float(exc.std(ddof=1))
        t = mean_exc / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    else:
        t = float("nan")
    return n, mean_ret, mean_exc, win, t


def build_event_report(panel, compute: Callable, config: EvalConfig,
                       factor_label: str = "", horizons=(1, 5, 10, 20)) -> EventReport:
    dates = list(panel.dates())
    rpt = EventReport(
        factor=factor_label, universe=config.universe,
        start=str(pd.Timestamp(min(dates)).date()) if dates else (config.start or ""),
        end=str(pd.Timestamp(max(dates)).date()) if dates else (config.end or ""),
        n_dates=len(dates), n_codes=panel.n_codes(), n_events=0,
    )
    try:
        sig = compute(panel)
    except Exception as e:
        rpt.status, rpt.error = "compute_error", f"{type(e).__name__}: {e}"
        return rpt
    if not isinstance(sig, pd.Series):
        rpt.status, rpt.error = "compute_error", f"compute 返回 {type(sig).__name__}, 需 pd.Series"
        return rpt

    valid = sig.astype(float).dropna()
    fired = valid[valid > 0]
    rpt.n_events = int(fired.shape[0])
    rpt.event_rate = float(fired.shape[0] / valid.shape[0]) if valid.shape[0] else float("nan")
    if rpt.n_events == 0:
        rpt.status, rpt.error = "no_events", "触发表达式从未 firing (信号恒 ≤0/NaN)。"
        return rpt
    if rpt.event_rate > 0.5:
        rpt.warnings.append(f"事件率 {rpt.event_rate:.0%} 偏高 — 这更像连续因子而非事件触发, 截面评测请用 factor_report。")

    ev_idx = fired.index
    max_h = max(horizons)
    for d in range(1, max_h + 1):
        e = _excess(forward_simple_returns(panel, d)).reindex(ev_idx).dropna()
        if e.shape[0] > 0:
            rpt.car_curve.append((d, float(e.mean())))

    for h in horizons:
        fwd, exc = forward_simple_returns(panel, h), None
        exc = _excess(fwd)
        sub = pd.DataFrame({"raw": fwd.reindex(ev_idx), "exc": exc.reindex(ev_idx)}).dropna()
        n, mr, me, win, t = _stats(sub["raw"], sub["exc"])
        rpt.horizons.append(EventHorizon(h=h, n=n, mean_ret=mr, mean_excess=me, win_rate=win, t_stat=t))

    exc_p = _excess(forward_simple_returns(panel, _PRIMARY_H)).reindex(ev_idx).dropna()
    if exc_p.shape[0] > 0:
        yrs = exc_p.index.get_level_values("datetime").year
        by = pd.Series(exc_p.values, index=yrs)
        for y, g in by.groupby(level=0):
            rpt.by_year.append((str(int(y)), int(g.shape[0]), float(g.mean())))

    if rpt.n_events < 30:
        rpt.warnings.append(f"事件样本少 ({rpt.n_events} < 30), 结论不稳健。")
    rpt.warnings.append("overlapping 事件 t 值可能膨胀 (v1 未做去重/Newey-West)。")
    return rpt


def event_report(spec_or_expr: str, config: Optional[EvalConfig] = None,
                 horizons=(1, 5, 10, 20)) -> EventReport:
    """I/O 编排: universe → 加载日频面板 → 取触发(注册名或表达式) → build_event_report。"""
    config = config or EvalConfig()
    from financial_analyst.data.universe import resolve_universe_codes
    codes = resolve_universe_codes(config.universe)
    if not codes:
        return EventReport(spec_or_expr, config.universe, config.start or "", config.end or "",
                           0, 0, 0, status="empty_universe",
                           error=f"universe '{config.universe}' 解析为空 (试 fa data bootstrap 或换 csi300_active)。")
    end = config.end or date.today().isoformat()
    start = config.start or (date.today() - timedelta(days=365 * 2)).isoformat()
    from financial_analyst.factors.zoo.panel import PanelData
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind = None
        panel = PanelData.from_loader(loader, codes, start, end, freq="day", industry_loader=ind)
    except Exception as e:
        return EventReport(spec_or_expr, config.universe, start, end, 0, len(codes), 0,
                           status="load_error", error=f"{type(e).__name__}: {e}")
    from financial_analyst.factors.zoo.registry import get as _get_alpha
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    try:
        compute, label = _get_alpha(spec_or_expr).compute, spec_or_expr
    except KeyError:
        validate_expr(spec_or_expr)
        compute, label = compile_factor(spec_or_expr), spec_or_expr
    return build_event_report(panel, compute, config, factor_label=label, horizons=horizons)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_event_signals.py -k build_event_report -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/factors/eval/event.py tests/test_event_signals.py
git -C G:\financial-analyst commit -m "feat(eval): event-study engine (build_event_report — abnormal returns/CAR/by-year)"
```

## Task 3: `event_report` I/O + eval 导出

**Files:**
- Modify: `src/financial_analyst/factors/eval/__init__.py`
- Test: `tests/test_event_signals.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_event_signals.py` (stub loader, 仿 test_factor_rest):

```python
def _stub_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return StubLoader()


def _patch_data(monkeypatch, codes=("SH600519", "SZ000858", "SH600036", "SZ300750")):
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes", lambda u: list(codes))
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())


def test_event_report_export_and_ok(monkeypatch):
    from financial_analyst.factors.eval import event_report, EventReport  # 导出可见
    _patch_data(monkeypatch)
    rpt = event_report("cross(close, sma(close,20))", EvalConfig(universe="csi300"), horizons=(1, 5))
    assert isinstance(rpt, EventReport)
    assert rpt.status in ("ok", "no_events")   # stub 随机, 可能不触发
    assert rpt.n_codes == 4


def test_event_report_empty_universe(monkeypatch):
    from financial_analyst.factors.eval import event_report
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes", lambda u: [])
    rpt = event_report("cross(close, sma(close,20))", EvalConfig(universe="nope"))
    assert rpt.status == "empty_universe"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_signals.py::test_event_report_export_and_ok -v`
Expected: FAIL (`ImportError: cannot import name 'event_report' from ...factors.eval`)

- [ ] **Step 3: 加导出**

把 `src/financial_analyst/factors/eval/__init__.py` 改为:

```python
"""单因子业内标准评测引擎 (SP-A) + 事件研究 (SP-B.2)。"""
from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import FactorReport, build_report, factor_report
from financial_analyst.factors.eval.event import (
    EventReport, EventHorizon, build_event_report, event_report)

__all__ = ["EvalConfig", "FactorReport", "build_report", "factor_report",
           "EventReport", "EventHorizon", "build_event_report", "event_report"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_event_signals.py -k event_report -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/factors/eval/__init__.py tests/test_event_signals.py
git -C G:\financial-analyst commit -m "feat(eval): export event_report I/O wrapper (universe->load->trigger)"
```

## Task 4: agent 工具 `event_report` + forge 提示

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (加 `_tool_event_report` 于 `_tool_factor_report` 之后 `:1480` 区; Tool 注册于 factor_report Tool 之后 `:2064`)
- Modify: `src/financial_analyst/factors/forge/forge.py:37-38`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_event_signals.py`:

```python
def test_event_report_tool(monkeypatch):
    from financial_analyst.buddy import tools as T
    _patch_data(monkeypatch)
    res = T._tool_event_report("cross(close, sma(close,20))", universe="csi300", horizons="1,5")
    assert not res.is_error
    assert "事件研究" in res.text
    # 工具在 TOOL_REGISTRY 注册
    assert any(getattr(t, "name", None) == "event_report" for t in T.TOOL_REGISTRY)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_signals.py::test_event_report_tool -v`
Expected: FAIL (`AttributeError: module ... has no attribute '_tool_event_report'`)

- [ ] **Step 3: 实现工具函数**

在 `tools.py` `_tool_factor_report` 之后加:

```python
def _tool_event_report(expr_or_name: str, universe: str = "csi300_active",
                       start: str = None, end: str = None, horizons: str = "1,5,10,20") -> ToolResult:
    """事件研究: 把触发表达式 (cross/比较/ts_* 求值为布尔信号) 当事件, 统计事件后
    各 horizon 的前向收益 (原始+市场调整) / 胜率 / t值 / CAR / 逐年。截面因子请用 factor_report。"""
    expr_or_name = (expr_or_name or "").strip()
    if not expr_or_name:
        return ToolResult("event_report: 缺少 expr_or_name (触发表达式或因子名)。", is_error=True)
    if "__" in expr_or_name or "import" in expr_or_name or "lambda" in expr_or_name:
        return ToolResult("event_report: 表达式含非法 token (__ / import / lambda)。", is_error=True)
    try:
        hs = tuple(int(x) for x in str(horizons).split(",") if x.strip())
    except ValueError:
        hs = (1, 5, 10, 20)
    try:
        from financial_analyst.factors.eval import EvalConfig, event_report
        rpt = event_report(expr_or_name, EvalConfig(universe=universe, start=start, end=end), horizons=hs or (1, 5, 10, 20))
    except Exception as e:
        return ToolResult(f"event_report 失败: {type(e).__name__}: {e}", is_error=True)
    if rpt.status != "ok":
        return ToolResult(f"事件研究未完成 (status={rpt.status}): {rpt.error}", is_error=True)

    import math
    def f(x, d=3):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"
    lines = [
        f"# 事件研究 · {rpt.factor}",
        f"池 {rpt.universe} ({rpt.n_codes} 只) · {rpt.start}~{rpt.end} · 事件数 {rpt.n_events} · 事件率 {rpt.event_rate:.1%}",
        "",
        "horizon | n | 平均收益 | 超额(减市场) | 胜率 | t值",
    ]
    for h in rpt.horizons:
        lines.append(f"{h.h}d | {h.n} | {f(h.mean_ret)} | {f(h.mean_excess)} | {f(h.win_rate,2)} | {f(h.t_stat,2)}")
    if rpt.by_year:
        lines += ["", "逐年(主5d超额): " + " · ".join(f"{y}:{f(m)}({n})" for y, n, m in rpt.by_year)]
    if rpt.warnings:
        lines += [""] + [f"⚠ {w}" for w in rpt.warnings]
    return ToolResult("\n".join(lines))
```

- [ ] **Step 4: 注册 Tool**

在 `tools.py` 的 `factor_report` Tool 条目 (`:2039-2064`, 以 `confirm_required=True,` + `),` 结束) **之后** 插入:

```python
    Tool(
        name="event_report",
        description=(
            "对一个【事件触发表达式】(用 cross/比较/ts_* 求值为布尔信号, 如 "
            "cross(close, sma(close,20)) 突破20日线, 或 ts_min((close>delay(close,1))*1.0,3) 连续3天涨) "
            "跑【事件研究】: 每次触发后 1/5/10/20 日的平均收益 + 市场调整超额 + 胜率 + t值 + 逐年。"
            "事件型因子 (金叉/突破/连续/放量) 用这个; 连续打分因子用 factor_report。默认 csi300_active 近2年。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expr_or_name": {"type": "string", "description": "触发表达式或注册名, 如 cross(close, sma(close,20))"},
                "universe": {"type": "string", "default": "csi300_active", "description": "csi300/csi500/csi800/all/csi300_active"},
                "start": {"type": "string", "description": "起始日 YYYY-MM-DD, 缺省今天往前 2 年"},
                "end": {"type": "string", "description": "结束日 YYYY-MM-DD, 缺省今天"},
                "horizons": {"type": "string", "default": "1,5,10,20", "description": "逗号分隔的前向天数"},
            },
            "required": ["expr_or_name"],
        },
        run=_tool_event_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
```

- [ ] **Step 5: 更新 forge out_of_vocab 提示**

`forge.py:37-38` 这两行:

```python
    "若想法需要表中没有的字段 (财报字段如 ROE/净利润/负债率, 需财报数据; 或'连续/金叉/突破'这类事件条件), "
    "把 out_of_vocab 设 true 并在 rationale 里说明缺什么, expr 留空。\n"
```

改为 (金叉/突破/连续现在可表达 → 不再一律 out_of_vocab; 仅财报字段 out):

```python
    "事件条件可表达: 金叉/突破用 cross(a,b), 连续N天用 ts_min((cond)*1.0,N), 放量用 volume>ts_mean(volume,N)*k。"
    "(注: 事件触发因子应交给 event_report 工具做事件研究, 不是截面打分。) "
    "若想法需要表中没有的字段 (财报字段如 ROE/净利润/负债率, 需财报数据), "
    "把 out_of_vocab 设 true 并在 rationale 里说明缺什么, expr 留空。\n"
```

- [ ] **Step 6: 跑测试确认通过 + 工具套件不回归**

Run: `python -m pytest tests/test_event_signals.py::test_event_report_tool tests/test_buddy_tools.py -v`
Expected: PASS (event_report 工具测试 + 现有 buddy 工具测试全绿; 若 test_buddy_tools 有"工具数量"断言, 同步 +1)

- [ ] **Step 7: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/tools.py src/financial_analyst/factors/forge/forge.py tests/test_event_signals.py
git -C G:\financial-analyst commit -m "feat(buddy): event_report agent tool + forge prompt allows cross/event triggers"
```

## Task 5: REST `POST /factor/event`

**Files:**
- Modify: `src/financial_analyst/buddy/server.py` (`EventReq` 于 SaveReq 旁; 端点于 `/factor/save` 后, `return app` 前)
- Test: `tests/test_event_signals.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_event_signals.py`:

```python
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


def test_rest_factor_event(monkeypatch):
    _patch_data(monkeypatch)
    client = TestClient(build_app())
    r = client.post("/factor/event", json={
        "expr_or_name": "cross(close, sma(close,20))", "universe": "csi300", "horizons": [1, 5]})
    assert r.status_code == 200
    assert "NaN" not in r.text and "Infinity" not in r.text   # _jsonable 生效
    body = r.json()
    assert "n_events" in body and "horizons" in body and "car_curve" in body
    assert body["status"] in ("ok", "no_events")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_signals.py::test_rest_factor_event -v`
Expected: FAIL (404)

- [ ] **Step 3: 加 `EventReq` 模型**

在 `server.py` `SaveReq` (`:95` 附近) 之后加:

```python
class EventReq(BaseModel):
    expr_or_name: str
    universe: str = "csi300_active"
    start: Optional[str] = None
    end: Optional[str] = None
    horizons: list = [1, 5, 10, 20]
    archive: bool = False
    note: str = ""
```

- [ ] **Step 4: 加端点**

在 `server.py` `factor_save_ep` 之后、`return app` 之前加:

```python
    @app.post("/factor/event")
    async def factor_event_ep(req: EventReq):
        """事件研究: 触发表达式/名 → 事件后各 horizon 前向收益 (原始+市场调整) + CAR + 逐年。

        触发型因子 (金叉/突破/连续/放量) 专用; 截面打分因子用 /factor/report。
        archive 字段 v1 暂忽略 (档案 schema 是 report/compose; 事件 metrics 不同)。
        """
        try:
            from financial_analyst.factors.eval import EvalConfig
            from financial_analyst.factors import eval as _eval_mod
            cfg = EvalConfig(universe=req.universe, start=req.start, end=req.end)
            hs = tuple(int(x) for x in req.horizons) or (1, 5, 10, 20)
            rpt = _eval_mod.event_report(req.expr_or_name, cfg, horizons=hs)
            return _jsonable(_asdict(rpt))
        except Exception as exc:
            return JSONResponse(status_code=500,
                                content={"error": f"{type(exc).__name__}: {exc}"})
```

(注: `_eval_mod.event_report` 经模块属性访问 — `from financial_analyst.factors import eval as _eval_mod` 已在 `/factor/report` 端点用过; `factors.eval.__init__` Task 3 已导出 event_report。)

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_event_signals.py::test_rest_factor_event -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/server.py tests/test_event_signals.py
git -C G:\financial-analyst commit -m "feat(rest): POST /factor/event — direct event-study endpoint"
```

## Task 6: 全量回归

- [ ] **Step 1: 跑 B.2 全套 + 关联套件**

Run: `python -m pytest tests/test_event_signals.py tests/test_factor_rest.py tests/test_factor_eval.py tests/test_factor_zoo.py tests/test_factor_expr.py -v`
Expected: 全绿 (B.2 ~11 测 + 现有 eval/zoo/expr/rest 不回归)

- [ ] **Step 2: 全量后端回归 (控制端 miniconda)**

Run: `python -m pytest tests/ -q`
Expected: 无新增失败 (基线 945 passed/1 skip → 现 ~956 passed/1 skip)。失败先排查环境 (litellm/mcp) 非代码。

- [ ] **Step 3: 终审自检**

- `grep -rn "out_of_vocab=true" forge.py` 确认提示已更新 (不再一律拒绝事件)。
- 确认 `event_report` 在 `factors.eval` + `TOOL_REGISTRY` + `/factor/event` 三处可达。
- `git -C G:\financial-analyst status` 工作区干净 (除会话前已存在的未跟踪项)。

- [ ] **Step 4: 最终提交 (如有零散改动)**

```bash
git -C G:\financial-analyst add -A
git -C G:\financial-analyst commit -m "test(event): full regression green for SP-B.2"
```

---

## Self-Review (作者已过一遍)

**Spec 覆盖:** DSL cross (T1) ✓; WHERE 已存在 (filter_where, 文档示例 T1 Step4) ✓; 事件研究引擎 raw+excess/CAR/by_year/event_rate/no_events/高事件率警告 (T2) ✓; I/O event_report + 4错误态 (T3, empty_universe 测; load_error/compute_error 同 factor_report 路径 + T2 测 compute_error) ✓; agent 工具 + forge 提示 (T4) ✓; REST POST /factor/event + _jsonable (T5) ✓; 测试 (T1-T5 内) ✓。

**类型一致:** `EventReport`/`EventHorizon` 字段在 T2 定义, T3/T4/T5 按名引用 (n_events/event_rate/horizons[].{h,n,mean_ret,mean_excess,win_rate,t_stat}/car_curve/by_year/status/factor/n_codes) 一致; `build_event_report(panel,compute,config,factor_label,horizons)` 与 `event_report(spec_or_expr,config,horizons)` 签名跨任务一致; `cross(a,b)` T1 定义 T2/T3/T5 测试调用一致; 工具 `_tool_event_report` + Tool name "event_report" + 端点 `event_report` 模块属性访问一致。

**已知简化 (spec 标注):** overlapping 事件 t 值膨胀 (未去重/Newey-West); 公司事件日期出 v1; archive 字段保留但忽略。

**环境备注:** 此环境无行情数据, `event_report`/REST 真跑会 load_error — 单测全走 stub loader (`_patch_data`) 不依赖真数据; build_event_report 走合成 PanelData。
