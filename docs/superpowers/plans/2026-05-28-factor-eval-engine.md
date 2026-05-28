# 因子评测引擎 (Factor Evaluation Engine · SP-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `financial-analyst` 包内自包含地实现单因子业内标准评测 (IC 全套 + 分位回测 + 多空组合净值 + 去极值/标准化), 给一个因子 (已注册名或表达式) + 股票池/频率/区间, 产出结构化 `FactorReport`, 并暴露为 `factor_report` 对话工具.

**Architecture:** 新建 `factors/eval/` 纯计算引擎 (preprocess/ic/quantile/portfolio/report), 复用现有 `PanelData` + `operators`。计算层接受已建好的 `PanelData` (100% 可单测, 无 I/O); 编排层 `factor_report()` 负责解析 universe→加载→调用计算层。两个 behavior-preserving 小重构: 抽 `compile_factor`/`FACTOR_VOCAB` → `factors/zoo/expr.py`, 抽 universe 解析 → `data/universe.py` (+f10 指数成分回退)。

**Tech Stack:** Python 3.10+, numpy, pandas (无新增重依赖), pytest。

**Spec:** `docs/superpowers/specs/2026-05-28-factor-eval-engine-design.md`

---

## 关键现状事实 (实现者必读)

- `PanelData(df)`: df 是 MultiIndex `(datetime, code)`, 列 `open/high/low/close/volume` (+vwap/amount 自动合成)。属性 `.close/.open/.high/.low/.volume/.vwap/.amount/.returns` 返回 Series; `.codes()/.dates()/.n_codes()/.n_dates()`。直接 `PanelData(df)` 构造 → 单测用合成面板最方便 (见 `tests/test_factor_zoo.py:_make_panel`)。
- 算子在 `financial_analyst.factors.zoo.operators` (rank/delta/ts_mean/correlation/...)。
- `financial_analyst.factors.zoo.bench_runner` 已有 `_forward_returns(panel, n)` (log 收益) + `bench_one` + `classify_factor` — 本引擎**改用简单收益**, 自己写前瞻收益, 不复用 `_forward_returns`。
- `financial_analyst.factors.zoo.registry`: `AlphaSpec(name,family,description,formula_text,compute,paper,tags)`, `get(name)` (KeyError if缺), `list_alphas(family)`。
- 因子表达式编译现在在 `buddy/tools.py:1271 _factor_compute` + `:1262 _FACTOR_VOCAB` (Task 1 抽走)。
- universe 解析现在在 `buddy/tools.py:1238 _resolve_universe_codes` (Task 2 抽走)。指数成分回退用 `financial_analyst.data.updaters.f10.resolve_universe(parquet_root, universe)` (支持 csi300/csi500/csi800/all), `parquet_root` 来自 `financial_analyst.data.paths.get_data_paths().parquet_root` (见 `data_cli.py:309` 用法)。
- 工具注册: `buddy/tools.py` 里 `Tool(name=..., description=..., input_schema={...}, run=fn, cost_hint="minutes", confirm_required=True)`; 工具函数返回 `ToolResult(text, is_error=bool)`。
- 测试: 扁平 `tests/test_*.py`, pytest, `tests/conftest.py` 有 autouse fixture (CI 安全, 不影响纯合成面板测试)。**spec 里写的 `tests/factors/eval/` 路径用扁平 `tests/test_factor_eval.py` 代替** (随仓库约定)。
- 运行测试: `cd /g/financial-analyst && python -m pytest <file> -v` (conftest 已把 src 加进 path)。

## 文件结构

**新建:**
- `src/financial_analyst/factors/zoo/expr.py` — `FACTOR_VOCAB`, `validate_expr()`, `compile_factor()`
- `src/financial_analyst/data/universe.py` — `resolve_universe_codes()` (+f10 回退)
- `src/financial_analyst/factors/eval/__init__.py` — 导出 `EvalConfig`, `FactorReport`, `factor_report`, `build_report`
- `src/financial_analyst/factors/eval/config.py` — `EvalConfig`
- `src/financial_analyst/factors/eval/preprocess.py` — `winsorize()`, `zscore()`, `neutralize()`(stub)
- `src/financial_analyst/factors/eval/ic.py` — `IcResult`, `ic_analysis()`
- `src/financial_analyst/factors/eval/quantile.py` — `QuantileResult`, `quantile_backtest()`
- `src/financial_analyst/factors/eval/portfolio.py` — `PortfolioResult`, `long_short_portfolio()`, `portfolio_stats()`
- `src/financial_analyst/factors/eval/report.py` — `ReportMeta`, `FactorChar`, `FactorReport`, `factor_characteristics()`, `build_report()`, `factor_report()`
- `tests/test_factor_expr.py`, `tests/test_universe_resolve.py`, `tests/test_factor_eval.py`, `tests/test_factor_report_tool.py`

**修改:**
- `src/financial_analyst/buddy/tools.py` — 改 import `expr`/`universe`; 加 `_tool_factor_report` + 注册 `factor_report` Tool

---

### Task 1: 抽取因子表达式编译 → `factors/zoo/expr.py`

Behavior-preserving 重构: 把 `buddy/tools.py` 里 `_FACTOR_VOCAB` + `_factor_compute` 抽到共享模块, 引擎和 buddy 共用。

**Files:**
- Create: `src/financial_analyst/factors/zoo/expr.py`
- Create: `tests/test_factor_expr.py`
- Modify: `src/financial_analyst/buddy/tools.py:1262-1293` (替换为 import)

- [ ] **Step 1: 写失败测试** `tests/test_factor_expr.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo import PanelData


def _panel():
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    codes = ["A", "B", "C", "D"]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    np.random.seed(1)
    close = pd.Series(50 + np.random.randn(len(idx)).cumsum() * 0.1 + 5, index=idx).abs() + 1
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": pd.Series(1e6, index=idx),
    })
    return PanelData(df)


def test_vocab_lists_fields_and_ops():
    assert "close" in FACTOR_VOCAB and "rank" in FACTOR_VOCAB and "delta" in FACTOR_VOCAB


def test_validate_rejects_forbidden_tokens():
    for bad in ["__import__('os')", "import os", "lambda x: x", ""]:
        with pytest.raises(ValueError):
            validate_expr(bad)


def test_validate_accepts_normal_expr():
    validate_expr("rank(-delta(close,5))")  # no raise


def test_compile_factor_runs_on_panel():
    fn = compile_factor("rank(-delta(close,5))")
    out = fn(_panel())
    assert isinstance(out, pd.Series)
    assert out.index.names == ["datetime", "code"]


def test_compile_factor_matches_legacy_namespace():
    """Regression: same expr → identical Series as the old buddy _factor_compute."""
    from financial_analyst.buddy import tools as _t
    p = _panel()
    a = compile_factor("rank(close) * 2 - delta(volume, 3)")(p)
    b = _t._factor_compute("rank(close) * 2 - delta(volume, 3)")(p)
    pd.testing.assert_series_equal(a, b)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_expr.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_analyst.factors.zoo.expr`

- [ ] **Step 3: 实现 `src/financial_analyst/factors/zoo/expr.py`**

```python
"""因子表达式编译 — 把白名单 DSL 字符串编译成 PanelData->Series 的 compute 函数。

从 buddy/tools.py 抽出, 供因子评测引擎与 buddy 的 factor_test/alpha_compare 共用。
受限 eval (无 builtins), 字段+算子白名单见 FACTOR_VOCAB。
"""
from __future__ import annotations

FACTOR_VOCAB = (
    "字段: close open high low volume vwap amount returns industry | "
    "算子: rank ts_rank delta delay ts_mean ts_sum ts_max ts_min ts_argmax ts_argmin "
    "stddev correlation(x,y,n) covariance decay_linear sma wma signedpower(x,p) "
    "log sign abs power(x,p) scale indneutralize(x,industry) max_pair min_pair filter_where | "
    "运算: + - * / ** 比较 ()"
)

_FORBIDDEN = ("__", "import", "lambda")


def validate_expr(expr: str) -> None:
    """Raise ValueError if expr is empty or contains a forbidden token."""
    if not expr or not str(expr).strip():
        raise ValueError("空因子表达式")
    if any(tok in expr for tok in _FORBIDDEN):
        raise ValueError("表达式含非法 token (__ / import / lambda)")


def compile_factor(expr: str):
    """Build a PanelData->Series compute function from a whitelisted expression.

    Does NOT validate — call validate_expr() first if the source is untrusted.
    """
    from financial_analyst.factors.zoo import operators as _ops

    def compute(p):
        ns = {
            "close": p.close, "open": p.open, "high": p.high, "low": p.low,
            "volume": p.volume, "vwap": p.vwap, "amount": p.amount,
            "returns": p.returns, "industry": p.industry,
            "rank": _ops.rank, "scale": _ops.scale, "ts_sum": _ops.ts_sum,
            "ts_mean": _ops.ts_mean, "stddev": _ops.stddev, "ts_max": _ops.ts_max,
            "ts_min": _ops.ts_min, "ts_argmax": _ops.ts_argmax, "ts_argmin": _ops.ts_argmin,
            "ts_rank": _ops.ts_rank, "delta": _ops.delta, "delay": _ops.delay,
            "correlation": _ops.correlation, "covariance": _ops.covariance,
            "decay_linear": _ops.decay_linear, "sma": _ops.sma, "wma": _ops.wma,
            "signedpower": _ops.signedpower, "log": _ops.log, "sign": _ops.sign,
            "abs": _ops.abs_, "abs_": _ops.abs_, "product": _ops.product,
            "power": _ops.power, "indneutralize": _ops.indneutralize,
            "max_pair": _ops.max_pair, "min_pair": _ops.min_pair,
            "filter_where": _ops.filter_where,
        }
        return eval(expr, {"__builtins__": {}}, ns)  # restricted namespace
    return compute
```

- [ ] **Step 4: 改 `buddy/tools.py` 委托到 expr.py**

把 `buddy/tools.py:1262-1293` 的 `_FACTOR_VOCAB = (...)` 整段 + `def _factor_compute(expr): ...` 整段删除, 替换为 (放在原位置):

```python
from financial_analyst.factors.zoo.expr import FACTOR_VOCAB as _FACTOR_VOCAB, compile_factor as _factor_compute
```

(`_tool_factor_test` / `_tool_alpha_compare` 里对 `_FACTOR_VOCAB` 和 `_factor_compute(...)` 的调用保持不变; 内联的 `"__" in expr` 检查也保持不变。)

- [ ] **Step 5: 跑测试确认通过 + buddy 回归**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_expr.py tests/test_buddy.py -v`
Expected: PASS (新测试全绿; buddy 现有测试不回归)

- [ ] **Step 6: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/zoo/expr.py tests/test_factor_expr.py src/financial_analyst/buddy/tools.py && git commit -m "refactor(factors): extract factor-expression DSL to factors/zoo/expr.py"
```

---

### Task 2: 抽取 universe 解析 → `data/universe.py` (+f10 回退)

把 `buddy/tools.py:_resolve_universe_codes` 抽到共享模块并加指数成分回退, 解锁 `csi300/csi500/csi800/all`。

**Files:**
- Create: `src/financial_analyst/data/universe.py`
- Create: `tests/test_universe_resolve.py`
- Modify: `src/financial_analyst/buddy/tools.py:1238-1259` (替换为 import)

- [ ] **Step 1: 写失败测试** `tests/test_universe_resolve.py`

```python
from __future__ import annotations
import pandas as pd
import pytest

from financial_analyst.data.universe import resolve_universe_codes


def test_resolves_bundled_csi300_active():
    codes = resolve_universe_codes("csi300_active")
    assert isinstance(codes, list) and len(codes) > 0
    assert all(isinstance(c, str) for c in codes)


def test_explicit_file_path(tmp_path):
    f = tmp_path / "my_uni.txt"
    f.write_text("SH600519\nSZ000858  # 五粮液\n\n", encoding="utf-8")
    codes = resolve_universe_codes(str(f))
    assert codes == ["SH600519", "SZ000858"]


def test_f10_fallback_for_index_universe(tmp_path, monkeypatch):
    """csi500 has no bundled .txt → falls back to f10 index-constituent parquet."""
    import financial_analyst.data.universe as uni
    monkeypatch.setattr(uni, "_f10_codes", lambda universe: ["SH600000", "SZ000001"] if universe == "csi500" else [])
    codes = resolve_universe_codes("csi500")
    assert codes == ["SH600000", "SZ000001"]


def test_unknown_returns_empty(monkeypatch):
    import financial_analyst.data.universe as uni
    monkeypatch.setattr(uni, "_f10_codes", lambda universe: [])
    assert resolve_universe_codes("totally_unknown_xyz") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_universe_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_analyst.data.universe`

- [ ] **Step 3: 实现 `src/financial_analyst/data/universe.py`**

```python
"""股票池 (universe) 名 → 代码列表。

解析链: 显式文件路径 → find_config('universes/<name>.txt') (走 workspace/
~/.financial-analyst/config/cwd/bundled) → ~/.financial-analyst/universes/<name>.txt
→ bundled_config_dir()/universes/<name>.txt → f10 指数成分回退 (csi300/csi500/csi800/all)。

从 buddy/tools.py 抽出供因子引擎与 buddy 工具共用。
"""
from __future__ import annotations
from pathlib import Path
from typing import List


def _read_codes(path: Path) -> List[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _f10_codes(universe: str) -> List[str]:
    """指数成分回退: 从 index_constituents.parquet 解析 csi300/csi500/csi800/all。
    缺 parquet 或非指数名 → []。"""
    try:
        from financial_analyst.data.paths import get_data_paths
        from financial_analyst.data.updaters.f10 import resolve_universe as _f10_resolve
        return list(_f10_resolve(get_data_paths().parquet_root, universe))
    except Exception:
        return []


def resolve_universe_codes(universe: str) -> List[str]:
    """Resolve a universe label (or file path) to a list of qlib codes. [] if unresolved."""
    p = Path(universe)
    cands: List[Path] = []
    if p.exists():
        cands.append(p)
    else:
        from financial_analyst._config import bundled_config_dir, find_config
        try:
            fc = find_config(f"universes/{universe}.txt")
            if fc is not None:
                cands.append(Path(fc))
        except Exception:
            pass
        cands.append(Path.home() / ".financial-analyst" / "universes" / f"{universe}.txt")
        cands.append(bundled_config_dir() / "universes" / f"{universe}.txt")
    for c in cands:
        try:
            if c.exists():
                codes = _read_codes(c)
                if codes:
                    return codes
        except Exception:
            continue
    return _f10_codes(universe)
```

- [ ] **Step 4: 改 `buddy/tools.py` 委托到 universe.py**

把 `buddy/tools.py:1238-1259` 的 `def _resolve_universe_codes(universe): ...` 整段删除, 替换为:

```python
from financial_analyst.data.universe import resolve_universe_codes as _resolve_universe_codes
```

(`_tool_factor_test` / `_tool_alpha_compare` 里 `_resolve_universe_codes(universe)` 调用不变。)

- [ ] **Step 5: 跑测试确认通过 + buddy 回归**

Run: `cd /g/financial-analyst && python -m pytest tests/test_universe_resolve.py tests/test_buddy.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/data/universe.py tests/test_universe_resolve.py src/financial_analyst/buddy/tools.py && git commit -m "refactor(data): extract universe resolver to data/universe.py + f10 index fallback"
```

---

### Task 3: `EvalConfig` + 预处理 (config.py + preprocess.py)

**Files:**
- Create: `src/financial_analyst/factors/eval/__init__.py` (本任务先留最小导出)
- Create: `src/financial_analyst/factors/eval/config.py`
- Create: `src/financial_analyst/factors/eval/preprocess.py`
- Create: `tests/test_factor_eval.py` (本任务起逐步追加)

- [ ] **Step 1: 写失败测试** `tests/test_factor_eval.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.preprocess import winsorize, zscore, neutralize


def _xs_series(n_dates=4, codes=("A", "B", "C", "D", "E")):
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    np.random.seed(3)
    return pd.Series(np.random.randn(len(idx)) * 10 + 50, index=idx)


def test_config_defaults():
    c = EvalConfig()
    assert c.universe == "csi500" and c.freq == "month" and c.n_groups == 10
    assert c.cost_bps == 0.0 and c.standardize is True


def test_effective_fwd_days_by_freq():
    assert EvalConfig(freq="day").effective_fwd_days() == 1
    assert EvalConfig(freq="week").effective_fwd_days() == 5
    assert EvalConfig(freq="month").effective_fwd_days() == 21
    assert EvalConfig(freq="month", fwd_days=10).effective_fwd_days() == 10


def test_periods_per_year():
    assert EvalConfig(freq="day").periods_per_year() == 252
    assert EvalConfig(freq="week").periods_per_year() == 52
    assert EvalConfig(freq="month").periods_per_year() == 12


def test_winsorize_clamps_to_quantile_per_date():
    s = _xs_series()
    # inject an extreme outlier on the first date
    d0 = s.index.get_level_values("datetime")[0]
    s.loc[(d0, "A")] = 1e6
    w = winsorize(s, q=0.2)
    # outlier must be pulled down to the per-date 0.8 quantile of the ORIGINAL
    assert w.loc[(d0, "A")] < 1e6
    assert w.loc[(d0, "A")] <= s.drop((d0, "A")).xs(d0, level="datetime").max() + 1


def test_zscore_per_date_mean0_std1():
    s = _xs_series()
    z = zscore(s)
    for d, sub in z.groupby(level="datetime"):
        assert abs(float(sub.mean())) < 1e-9
        assert abs(float(sub.std()) - 1.0) < 1e-6


def test_neutralize_is_stub():
    with pytest.raises(NotImplementedError):
        neutralize(_xs_series(), industry=None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_analyst.factors.eval`

- [ ] **Step 3: 实现 config.py / preprocess.py / __init__.py**

`src/financial_analyst/factors/eval/__init__.py`:
```python
"""单因子业内标准评测引擎 (SP-A)。"""
from financial_analyst.factors.eval.config import EvalConfig

__all__ = ["EvalConfig"]
```

`src/financial_analyst/factors/eval/config.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Tuple

_FWD_BY_FREQ = {"day": 1, "week": 5, "month": 21}
_PPY_BY_FREQ = {"day": 252, "week": 52, "month": 12}


@dataclass
class EvalConfig:
    universe: str = "csi500"
    freq: str = "month"               # day / week / month
    start: Optional[str] = None       # None → 今天 - 2y
    end: Optional[str] = None         # None → 今天
    fwd_days: Optional[int] = None    # None → 按 freq (1/5/21)
    n_groups: int = 10
    cost_bps: float = 0.0
    winsorize_q: float = 0.01
    standardize: bool = True
    neutralize: bool = False          # A.2; True 时 build_report 会进 warnings 并跳过
    decay_horizons: Tuple[int, ...] = (1, 3, 5, 10, 21, 42)

    def effective_fwd_days(self) -> int:
        if self.fwd_days is not None:
            return int(self.fwd_days)
        return _FWD_BY_FREQ.get(self.freq, 21)

    def periods_per_year(self) -> int:
        return _PPY_BY_FREQ.get(self.freq, 12)
```

`src/financial_analyst/factors/eval/preprocess.py`:
```python
"""截面预处理: 去极值 / 标准化 / 中性化(A.2 占位)。每个函数对同一日期横截面操作。"""
from __future__ import annotations
import pandas as pd


def winsorize(x: pd.Series, q: float = 0.01) -> pd.Series:
    """Per-date clip to [quantile(q), quantile(1-q)]."""
    def _clip(s: pd.Series) -> pd.Series:
        lo, hi = s.quantile(q), s.quantile(1 - q)
        return s.clip(lo, hi)
    return x.groupby(level="datetime", group_keys=False).transform(_clip)


def zscore(x: pd.Series) -> pd.Series:
    """Per-date (x - mean) / std. Zero-std dates → NaN."""
    g = x.groupby(level="datetime")
    mean = g.transform("mean")
    std = g.transform("std")
    return (x - mean) / std.where(std > 0)


def neutralize(x: pd.Series, industry=None, mktcap=None) -> pd.Series:
    """行业 + 市值中性化。A.2 实现, 本期占位。"""
    raise NotImplementedError("neutralize() 留到 SP-A.2 (行业+市值中性化)")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/eval/ tests/test_factor_eval.py && git commit -m "feat(eval): EvalConfig + cross-sectional preprocess (winsorize/zscore)"
```

---

### Task 4: IC 分析 (ic.py)

**Files:**
- Create: `src/financial_analyst/factors/eval/ic.py`
- Modify: `tests/test_factor_eval.py` (追加)

- [ ] **Step 1: 追加失败测试到 `tests/test_factor_eval.py`**

```python
from financial_analyst.factors.eval.ic import ic_analysis, IcResult


def _aligned_alpha_fwd(relation="perfect", n_dates=30, codes=tuple("ABCDEFGH"), seed=5):
    """Build aligned (alpha, fwd) series on the same (date, code) index.
    relation: 'perfect' (alpha == fwd), 'reversed' (alpha == -fwd), 'random'."""
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    rng = np.random.default_rng(seed)
    fwd = pd.Series(rng.standard_normal(len(idx)) * 0.02, index=idx)
    if relation == "perfect":
        alpha = fwd.copy()
    elif relation == "reversed":
        alpha = -fwd
    else:
        alpha = pd.Series(rng.standard_normal(len(idx)), index=idx)
    return alpha, fwd


def test_ic_perfect_factor_near_one():
    alpha, fwd = _aligned_alpha_fwd("perfect")
    r = ic_analysis(alpha, fwd)
    assert isinstance(r, IcResult)
    assert r.ic_mean > 0.95
    assert r.icir > 3
    assert r.ic_win_rate > 0.95
    assert len(r.ic_series) == 30


def test_ic_reversed_factor_near_minus_one():
    alpha, fwd = _aligned_alpha_fwd("reversed")
    r = ic_analysis(alpha, fwd)
    assert r.ic_mean < -0.95
    assert r.rank_ic_mean < -0.9


def test_ic_random_factor_near_zero():
    alpha, fwd = _aligned_alpha_fwd("random")
    r = ic_analysis(alpha, fwd)
    assert abs(r.ic_mean) < 0.1


def test_ic_decay_one_row_per_horizon():
    alpha, fwd = _aligned_alpha_fwd("perfect")
    # horizon→fwd dict; reuse same fwd for the test (decay shape, not values)
    decay_fwd = {1: fwd, 5: fwd, 21: fwd}
    r = ic_analysis(alpha, fwd, fwd_by_horizon=decay_fwd)
    assert [h for h, _, _ in r.ic_decay] == [1, 5, 21]
    assert all(ic > 0.9 for _, ic, _ in r.ic_decay)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k ic -v`
Expected: FAIL — `ImportError: cannot import name 'ic_analysis'`

- [ ] **Step 3: 实现 `src/financial_analyst/factors/eval/ic.py`**

```python
"""IC 分析: 截面 Pearson/Spearman IC, ICIR, t值, 命中率, IC 序列, IC 衰减。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class IcResult:
    ic_mean: float
    ic_std: float
    icir: float
    ic_tstat: float
    ic_win_rate: float
    rank_ic_mean: float
    rank_icir: float
    ic_series: List[Tuple[str, float]] = field(default_factory=list)
    ic_decay: List[Tuple[int, float, float]] = field(default_factory=list)


def _daily_corr(joined: pd.DataFrame, rank: bool) -> pd.Series:
    df = joined
    if rank:
        df = joined.groupby(level="datetime").rank()
    return df.groupby(level="datetime").apply(lambda d: d["a"].corr(d["f"]))


def ic_analysis(alpha: pd.Series, fwd: pd.Series,
                fwd_by_horizon: Optional[Dict[int, pd.Series]] = None) -> IcResult:
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    nan = float("nan")
    if joined.empty:
        return IcResult(nan, nan, nan, nan, nan, nan, nan, [], [])

    ic = _daily_corr(joined, rank=False).dropna()
    ric = _daily_corr(joined, rank=True).dropna()
    n = len(ic)
    ic_mean = float(ic.mean()) if n else nan
    ic_std = float(ic.std()) if n else nan
    icir = ic_mean / ic_std if ic_std and ic_std > 0 else nan
    ic_tstat = ic_mean / ic_std * np.sqrt(n) if ic_std and ic_std > 0 else nan
    ic_win = float((np.sign(ic) == np.sign(ic_mean)).mean()) if n else nan
    rank_ic_mean = float(ric.mean()) if len(ric) else nan
    rank_ic_std = float(ric.std()) if len(ric) else nan
    rank_icir = rank_ic_mean / rank_ic_std if rank_ic_std and rank_ic_std > 0 else nan
    ic_series = [(str(pd.Timestamp(d).date()), float(v)) for d, v in ic.items()]

    decay: List[Tuple[int, float, float]] = []
    if fwd_by_horizon:
        for h in sorted(fwd_by_horizon):
            jh = pd.concat([alpha.rename("a"), fwd_by_horizon[h].rename("f")], axis=1).dropna()
            if jh.empty:
                decay.append((int(h), nan, nan))
                continue
            ich = float(_daily_corr(jh, rank=False).mean())
            rich = float(_daily_corr(jh, rank=True).mean())
            decay.append((int(h), ich, rich))

    return IcResult(ic_mean, ic_std, icir, ic_tstat, ic_win,
                    rank_ic_mean, rank_icir, ic_series, decay)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k ic -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/eval/ic.py tests/test_factor_eval.py && git commit -m "feat(eval): IC analysis (IC/ICIR/t-stat/win-rate/series/decay)"
```

---

### Task 5: 分位回测 (quantile.py)

**Files:**
- Create: `src/financial_analyst/factors/eval/quantile.py`
- Modify: `tests/test_factor_eval.py` (追加)

- [ ] **Step 1: 追加失败测试**

```python
from financial_analyst.factors.eval.quantile import quantile_backtest, QuantileResult


def test_quantile_perfect_factor_monotonic():
    alpha, fwd = _aligned_alpha_fwd("perfect", n_dates=40)
    r = quantile_backtest(alpha, fwd, n_groups=5, ppy=12)
    assert isinstance(r, QuantileResult)
    # group 0 = lowest factor = lowest return; group 4 = highest
    assert r.group_ann_return[-1] > r.group_ann_return[0]
    assert r.monotonicity > 0.9
    assert r.long_short_spread > 0
    assert len(r.group_nav) == len(r.group_ann_return)


def test_quantile_reversed_factor_negative_spread():
    alpha, fwd = _aligned_alpha_fwd("reversed", n_dates=40)
    r = quantile_backtest(alpha, fwd, n_groups=5, ppy=12)
    assert r.long_short_spread < 0
    assert r.monotonicity < -0.9


def test_quantile_random_factor_flat():
    alpha, fwd = _aligned_alpha_fwd("random", n_dates=60)
    r = quantile_backtest(alpha, fwd, n_groups=5, ppy=12)
    assert abs(r.monotonicity) < 0.7  # not strongly monotonic
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k quantile -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `src/financial_analyst/factors/eval/quantile.py`**

```python
"""分位(十分位)回测: 每个调仓日按因子值分 N 组, 算组前瞻收益/组净值/单调性/多空价差。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd


@dataclass
class QuantileResult:
    n_groups: int
    group_ann_return: List[float] = field(default_factory=list)
    group_nav: List[List[float]] = field(default_factory=list)
    monotonicity: float = float("nan")
    long_short_spread: float = float("nan")


def _assign_groups(a: pd.Series, n_groups: int) -> pd.Series:
    """Per-date qcut into n_groups (label 0=lowest .. n-1=highest). Few-distinct → fewer bins."""
    def _q(s: pd.Series) -> pd.Series:
        try:
            return pd.qcut(s, n_groups, labels=False, duplicates="drop")
        except (ValueError, IndexError):
            return pd.Series(np.nan, index=s.index)
    return a.groupby(level="datetime", group_keys=False).transform(_q)


def quantile_backtest(alpha: pd.Series, fwd: pd.Series,
                      n_groups: int = 10, ppy: int = 12) -> QuantileResult:
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if joined.empty:
        return QuantileResult(n_groups)
    joined["g"] = _assign_groups(joined["a"], n_groups)
    joined = joined.dropna(subset=["g"])
    if joined.empty:
        return QuantileResult(n_groups)

    dt = joined.index.get_level_values("datetime")
    grp_ret = joined.groupby([dt, joined["g"]])["f"].mean()
    wide = grp_ret.unstack("g").sort_index()   # rows=date, cols=group label
    cols = sorted(wide.columns)
    nper = len(wide)

    group_ann: List[float] = []
    group_nav: List[List[float]] = []
    for g in cols:
        col = wide[g].fillna(0.0)
        nav = (1 + col).cumprod()
        group_nav.append([float(v) for v in nav.values])
        navend = float(nav.iloc[-1]) if nper else float("nan")
        ann = navend ** (ppy / nper) - 1 if nper > 0 else float("nan")
        group_ann.append(float(ann))

    if len(group_ann) >= 2:
        gi = pd.Series(range(len(group_ann)), dtype=float)
        ga = pd.Series(group_ann, dtype=float)
        monotonicity = float(gi.corr(ga, method="spearman"))
        long_short_spread = float(group_ann[-1] - group_ann[0])
    else:
        monotonicity = float("nan")
        long_short_spread = float("nan")

    return QuantileResult(n_groups, group_ann, group_nav, monotonicity, long_short_spread)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k quantile -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/eval/quantile.py tests/test_factor_eval.py && git commit -m "feat(eval): quantile backtest (group returns/nav/monotonicity/spread)"
```

---

### Task 6: 多空组合 (portfolio.py)

**Files:**
- Create: `src/financial_analyst/factors/eval/portfolio.py`
- Modify: `tests/test_factor_eval.py` (追加)

- [ ] **Step 1: 追加失败测试** (含手算断言)

```python
from financial_analyst.factors.eval.portfolio import (
    long_short_portfolio, portfolio_stats, PortfolioResult,
)


def test_portfolio_stats_hand_computed():
    # 4 periods of +10% each, monthly (ppy=12)
    ls = pd.Series([0.1, 0.1, 0.1, 0.1],
                   index=pd.date_range("2024-01-31", periods=4, freq="ME"))
    st = portfolio_stats(ls, ppy=12)
    # nav_end = 1.1**4 = 1.4641; ann = 1.4641**(12/4) - 1
    assert st["ann_return"] == pytest.approx(1.4641 ** 3 - 1, rel=1e-6)
    assert st["max_drawdown"] == pytest.approx(0.0, abs=1e-9)  # monotonic up
    assert st["win_rate"] == pytest.approx(1.0)
    assert st["volatility"] == pytest.approx(0.0, abs=1e-9)  # zero variance


def test_portfolio_stats_drawdown():
    ls = pd.Series([0.2, -0.5, 0.1],
                   index=pd.date_range("2024-01-31", periods=3, freq="ME"))
    st = portfolio_stats(ls, ppy=12)
    # nav = [1.2, 0.6, 0.66]; peak 1.2 → trough 0.6 → dd = 0.6/1.2 - 1 = -0.5
    assert st["max_drawdown"] == pytest.approx(-0.5, rel=1e-6)
    assert st["win_rate"] == pytest.approx(2 / 3, rel=1e-6)


def test_long_short_perfect_factor_positive_sharpe():
    alpha, fwd = _aligned_alpha_fwd("perfect", n_dates=40)
    r = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=0.0)
    assert isinstance(r, PortfolioResult)
    assert r.ann_return > 0
    assert r.sharpe > 0
    assert len(r.nav_series) >= 1


def test_long_short_cost_reduces_return():
    alpha, fwd = _aligned_alpha_fwd("random", n_dates=60)
    gross = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=0.0)
    net = long_short_portfolio(alpha, fwd, n_groups=5, ppy=12, cost_bps=50.0)
    assert net.ann_return <= gross.ann_return + 1e-9
    assert gross.turnover >= 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k portfolio -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `src/financial_analyst/factors/eval/portfolio.py`**

```python
"""多空组合: 多 top 组 / 空 bottom 组等权, 按调仓日算净值 + 年化/Sharpe/回撤/换手/胜率。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from financial_analyst.factors.eval.quantile import _assign_groups


@dataclass
class PortfolioResult:
    nav_series: List[Tuple[str, float]] = field(default_factory=list)
    benchmark_nav: Optional[List[Tuple[str, float]]] = None
    ann_return: float = float("nan")
    sharpe: float = float("nan")
    max_drawdown: float = float("nan")
    volatility: float = float("nan")
    turnover: float = float("nan")
    win_rate: float = float("nan")
    calmar: float = float("nan")


def portfolio_stats(ls: pd.Series, ppy: int) -> Dict[str, float]:
    """Annualized stats from a per-period return series (chronological)."""
    ls = ls.dropna()
    n = len(ls)
    nan = float("nan")
    if n == 0:
        return {"ann_return": nan, "volatility": nan, "sharpe": nan,
                "max_drawdown": nan, "calmar": nan, "win_rate": nan}
    nav = (1 + ls).cumprod()
    ann = float(nav.iloc[-1] ** (ppy / n) - 1)
    vol = float(ls.std() * np.sqrt(ppy)) if n > 1 else 0.0
    sharpe = float(ls.mean() * ppy / vol) if vol and vol > 0 else nan
    mdd = float((nav / nav.cummax() - 1).min())
    calmar = float(ann / abs(mdd)) if mdd < 0 else nan
    win = float((ls > 0).mean())
    return {"ann_return": ann, "volatility": vol, "sharpe": sharpe,
            "max_drawdown": mdd, "calmar": calmar, "win_rate": win}


def long_short_portfolio(alpha: pd.Series, fwd: pd.Series,
                         n_groups: int = 10, ppy: int = 12,
                         cost_bps: float = 0.0) -> PortfolioResult:
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if joined.empty:
        return PortfolioResult()
    joined["g"] = _assign_groups(joined["a"], n_groups)
    joined = joined.dropna(subset=["g"])
    if joined.empty:
        return PortfolioResult()

    dates = sorted(joined.index.get_level_values("datetime").unique())
    ls_vals: List[float] = []
    turns: List[float] = []
    prev_top: set = set()
    for d in dates:
        sl = joined.xs(d, level="datetime")
        gmax = sl["g"].max()
        top = sl[sl["g"] == gmax]
        bot = sl[sl["g"] == 0]
        if len(top) == 0 or len(bot) == 0:
            continue
        gross = float(top["f"].mean() - bot["f"].mean())
        top_codes = set(top.index)
        if prev_top:
            turn = len(top_codes ^ prev_top) / (2 * max(len(top_codes), 1))
        else:
            turn = 0.0
        net = gross - turn * (cost_bps / 1e4) * 2
        ls_vals.append(net)
        turns.append(turn)
        prev_top = top_codes

    ls = pd.Series(ls_vals, index=pd.Index(dates[:len(ls_vals)], name="datetime"))
    st = portfolio_stats(ls, ppy)
    nav = (1 + ls).cumprod()
    nav_series = [(str(pd.Timestamp(d).date()), float(v)) for d, v in nav.items()]
    return PortfolioResult(
        nav_series=nav_series, benchmark_nav=None,
        ann_return=st["ann_return"], sharpe=st["sharpe"],
        max_drawdown=st["max_drawdown"], volatility=st["volatility"],
        turnover=float(np.mean(turns)) if turns else float("nan"),
        win_rate=st["win_rate"], calmar=st["calmar"],
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k portfolio -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/eval/portfolio.py tests/test_factor_eval.py && git commit -m "feat(eval): long-short portfolio (nav/ann/sharpe/mdd/turnover/win)"
```

---

### Task 7: 报告编排 (report.py)

`FactorReport` 组装 + 因子特征 + `build_report` (纯, 给 PanelData) + `factor_report` (I/O: 解析 universe + 加载)。

**Files:**
- Create: `src/financial_analyst/factors/eval/report.py`
- Modify: `src/financial_analyst/factors/eval/__init__.py` (加导出)
- Modify: `tests/test_factor_eval.py` (追加)

- [ ] **Step 1: 追加失败测试**

```python
from financial_analyst.factors.eval.report import (
    build_report, FactorReport, factor_characteristics, rebalance_dates,
    forward_simple_returns,
)
from financial_analyst.factors.zoo import PanelData


def _signal_panel(n_dates=80, codes=tuple("ABCDEFGH"), relation="perfect", seed=11):
    """Panel where next-day return is (perfect|reversed|random) wrt a known factor
    embedded in close. We make close a random walk and define the 'factor' as the
    realized fwd return baked in, so the compute fn can recover it."""
    dates = pd.date_range("2023-01-02", periods=n_dates, freq="B")
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    rng = np.random.default_rng(seed)
    close = pd.Series(rng.lognormal(0, 0.02, len(idx)).groupby(
        idx.get_level_values("code")).cumprod().values, index=idx) * 50 + 10
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": pd.Series(1e6, index=idx),
    })
    return PanelData(df)


def test_rebalance_dates_month():
    dates = pd.date_range("2024-01-01", "2024-03-31", freq="B")
    reb = rebalance_dates(list(dates), "month")
    # one per calendar month (the last business day in each)
    assert len(reb) == 3


def test_forward_simple_returns_basic():
    p = _signal_panel(n_dates=10, codes=("A",))
    fwd = forward_simple_returns(p, 1)
    close = p.close
    # fwd(t) = close(t+1)/close(t) - 1, last row NaN
    a = close.xs("A", level="code")
    f = fwd.xs("A", level="code")
    assert f.iloc[0] == pytest.approx(a.iloc[1] / a.iloc[0] - 1, rel=1e-9)
    assert pd.isna(f.iloc[-1])


def test_build_report_perfect_factor_ok():
    p = _signal_panel(n_dates=80)
    # factor = -1*fwd1 baked: use a momentum proxy. Simpler: use close-based factor.
    from financial_analyst.factors.eval.config import EvalConfig
    compute = lambda panel: panel.close.groupby(level="code").pct_change()  # 1d momentum
    cfg = EvalConfig(freq="week", standardize=True)
    rpt = build_report(p, compute, cfg, factor_label="mom1", family="custom")
    assert isinstance(rpt, FactorReport)
    assert rpt.status == "ok"
    assert rpt.meta.factor == "mom1" and rpt.meta.freq == "week"
    assert rpt.ic is not None and rpt.quantile is not None and rpt.portfolio is not None
    assert -1.0 <= rpt.characteristics.coverage <= 1.0
    assert rpt.portfolio.benchmark_nav is not None  # equal-weight benchmark filled


def test_build_report_compute_error_no_raise():
    p = _signal_panel(n_dates=40)
    from financial_analyst.factors.eval.config import EvalConfig

    def boom(panel):
        raise RuntimeError("synthetic boom")

    rpt = build_report(p, boom, EvalConfig(freq="week"), factor_label="boom", family="custom")
    assert rpt.status == "compute_error"
    assert "synthetic boom" in rpt.error


def test_build_report_bad_output_status():
    p = _signal_panel(n_dates=40)
    from financial_analyst.factors.eval.config import EvalConfig
    rpt = build_report(p, lambda panel: 123, EvalConfig(freq="week"),
                       factor_label="bad", family="custom")
    assert rpt.status == "bad_output"


def test_factor_characteristics_coverage():
    p = _signal_panel(n_dates=30, codes=tuple("ABCDE"))
    alpha = p.close.groupby(level="code").pct_change()
    reb = p.dates()
    ch = factor_characteristics(alpha, n_codes=5)
    assert 0.0 <= ch.coverage <= 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -k "build_report or rebalance or forward or characteristics" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `src/financial_analyst/factors/eval/report.py`**

```python
"""报告编排: FactorReport 组装 + 因子特征 + build_report(纯) + factor_report(I/O)。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.preprocess import winsorize, zscore
from financial_analyst.factors.eval.ic import ic_analysis, IcResult
from financial_analyst.factors.eval.quantile import quantile_backtest, QuantileResult
from financial_analyst.factors.eval.portfolio import long_short_portfolio, PortfolioResult


@dataclass
class ReportMeta:
    factor: str
    family: str
    universe: str
    freq: str
    start: str
    end: str
    n_dates: int
    n_codes: int
    fwd_days: int
    preprocess: dict = field(default_factory=dict)


@dataclass
class FactorChar:
    coverage: float = float("nan")
    autocorr_1: float = float("nan")
    half_life: float = -1.0
    top_group_turnover: float = float("nan")


@dataclass
class FactorReport:
    meta: ReportMeta
    ic: Optional[IcResult] = None
    quantile: Optional[QuantileResult] = None
    portfolio: Optional[PortfolioResult] = None
    characteristics: FactorChar = field(default_factory=FactorChar)
    warnings: List[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def rebalance_dates(all_dates: List, freq: str) -> List:
    """Resample a sorted daily date list to rebalance dates (last per period)."""
    s = pd.Series(1, index=pd.DatetimeIndex(sorted(pd.to_datetime(all_dates))))
    if freq == "day":
        return list(s.index)
    rule = {"week": "W", "month": "ME"}.get(freq, "ME")
    last = s.groupby(s.index.to_period({"week": "W", "month": "M"}.get(freq, "M"))).apply(
        lambda g: g.index.max())
    return list(pd.DatetimeIndex(last.values))


def forward_simple_returns(panel, n: int) -> pd.Series:
    """Simple n-day forward return per code: close(t+n)/close(t) - 1."""
    close = panel.close
    fwd_close = close.groupby(level="code", group_keys=False).shift(-n)
    return fwd_close / close - 1.0


def _restrict(s: pd.Series, dates) -> pd.Series:
    keep = pd.DatetimeIndex(dates)
    return s[s.index.get_level_values("datetime").isin(keep)]


def _benchmark_nav(fwd_r: pd.Series) -> List:
    """Equal-weight universe nav from per-date mean forward return."""
    by = fwd_r.groupby(level="datetime").mean().dropna().sort_index()
    nav = (1 + by).cumprod()
    return [(str(pd.Timestamp(d).date()), float(v)) for d, v in nav.items()]


def factor_characteristics(alpha: pd.Series, n_codes: int) -> FactorChar:
    a = alpha.dropna()
    if a.empty or n_codes <= 0:
        return FactorChar()
    per_date_cov = a.groupby(level="datetime").size() / float(n_codes)
    coverage = float(per_date_cov.mean())

    dates = sorted(a.index.get_level_values("datetime").unique())

    def _xs_autocorr(lag: int) -> float:
        vals = []
        for i in range(lag, len(dates)):
            cur = a.xs(dates[i], level="datetime")
            prev = a.xs(dates[i - lag], level="datetime")
            common = cur.index.intersection(prev.index)
            if len(common) < 3:
                continue
            c = cur.loc[common].corr(prev.loc[common], method="spearman")
            if c == c:
                vals.append(c)
        return float(np.mean(vals)) if vals else float("nan")

    autocorr_1 = _xs_autocorr(1)
    half_life = -1.0
    for lag in (1, 2, 3, 5, 8, 13, 21):
        ac = _xs_autocorr(lag)
        if ac == ac and ac < 0.5:
            half_life = float(lag)
            break
    return FactorChar(coverage=coverage, autocorr_1=autocorr_1, half_life=half_life)


def build_report(panel, compute: Callable, config: EvalConfig,
                 factor_label: str, family: str) -> FactorReport:
    fwd_days = config.effective_fwd_days()
    meta = ReportMeta(
        factor=factor_label, family=family, universe=config.universe,
        freq=config.freq,
        start=str(pd.Timestamp(panel.dates().min()).date()),
        end=str(pd.Timestamp(panel.dates().max()).date()),
        n_dates=0, n_codes=panel.n_codes(), fwd_days=fwd_days,
        preprocess={"winsorize_q": config.winsorize_q,
                    "standardize": config.standardize,
                    "neutralize": False},
    )
    warnings: List[str] = []
    if config.neutralize:
        warnings.append("中性化 (neutralize=True) 暂未实现 (SP-A.2), 已跳过。")

    try:
        alpha = compute(panel)
    except Exception as e:
        meta_err = meta
        return FactorReport(meta_err, status="compute_error", error=f"{type(e).__name__}: {e}",
                            warnings=warnings)
    if not isinstance(alpha, pd.Series):
        return FactorReport(meta, status="bad_output",
                            error=f"compute returned {type(alpha).__name__}, expected pd.Series",
                            warnings=warnings)

    if config.winsorize_q and config.winsorize_q > 0:
        alpha = winsorize(alpha, config.winsorize_q)
    if config.standardize:
        alpha = zscore(alpha)

    reb = rebalance_dates(list(panel.dates()), config.freq)
    fwd = forward_simple_returns(panel, fwd_days)
    alpha_r = _restrict(alpha, reb)
    fwd_r = _restrict(fwd, reb)
    meta.n_dates = len(pd.Index(alpha_r.dropna().index.get_level_values("datetime")).unique())

    fwd_by_h = {h: _restrict(forward_simple_returns(panel, h), reb) for h in config.decay_horizons}
    ic = ic_analysis(alpha_r, fwd_r, fwd_by_h)
    q = quantile_backtest(alpha_r, fwd_r, config.n_groups, config.periods_per_year())
    pf = long_short_portfolio(alpha_r, fwd_r, config.n_groups, config.periods_per_year(), config.cost_bps)
    pf.benchmark_nav = _benchmark_nav(fwd_r)
    ch = factor_characteristics(alpha_r, panel.n_codes())
    ch.top_group_turnover = pf.turnover

    if meta.n_dates < 12:
        warnings.append(f"样本太短 (有效调仓期 {meta.n_dates} < 12), 结论不稳健。")
    if ch.coverage == ch.coverage and ch.coverage < 0.5:
        warnings.append(f"因子覆盖率低 ({ch.coverage:.0%}).")
    if ic.rank_ic_mean == ic.rank_ic_mean and ic.rank_ic_mean < 0:
        warnings.append("RankIC 为负 — 因子方向为反向 (高分→未来跌)。")

    return FactorReport(meta, ic, q, pf, ch, warnings, "ok", "")


def factor_report(spec_or_expr: str, config: Optional[EvalConfig] = None) -> FactorReport:
    """I/O 编排: 解析 universe → 加载日频面板 → 取因子(注册名或表达式) → build_report。"""
    from financial_analyst.factors.eval.config import EvalConfig as _EC
    config = config or _EC()
    from financial_analyst.data.universe import resolve_universe_codes

    codes = resolve_universe_codes(config.universe)
    if not codes:
        empty_meta = ReportMeta(spec_or_expr, "?", config.universe, config.freq,
                                config.start or "", config.end or "", 0, 0,
                                config.effective_fwd_days())
        return FactorReport(empty_meta, status="empty_universe",
                            error=f"universe '{config.universe}' 解析为空 (试 fa data bootstrap 或换 csi300_active)。")

    end = config.end or date.today().isoformat()
    start = config.start or (date.today() - timedelta(days=365 * 2)).isoformat()

    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.zoo.panel import PanelData
    loader = get_default_loader()
    try:
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
        ind_loader = IndustryLoader() if industry_map_path().exists() else None
    except Exception:
        ind_loader = None
    panel = PanelData.from_loader(loader, codes, start, end, freq="day", industry_loader=ind_loader)

    from financial_analyst.factors.zoo.registry import get as _get_alpha
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    try:
        spec = _get_alpha(spec_or_expr)
        compute, family, label = spec.compute, spec.family, spec_or_expr
    except KeyError:
        validate_expr(spec_or_expr)
        compute, family, label = compile_factor(spec_or_expr), "custom", spec_or_expr
    return build_report(panel, compute, config, label, family)
```

更新 `src/financial_analyst/factors/eval/__init__.py`:
```python
"""单因子业内标准评测引擎 (SP-A)。"""
from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import FactorReport, build_report, factor_report

__all__ = ["EvalConfig", "FactorReport", "build_report", "factor_report"]
```

- [ ] **Step 4: 跑测试确认通过 (全 eval 文件)**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_eval.py -v`
Expected: PASS (所有 IC/quantile/portfolio/report 测试全绿)

- [ ] **Step 5: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/eval/ tests/test_factor_eval.py && git commit -m "feat(eval): FactorReport orchestration (build_report + factor_report I/O)"
```

---

### Task 8: `factor_report` 对话工具 + 注册

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (加 `_tool_factor_report` + 注册 `Tool`)
- Create: `tests/test_factor_report_tool.py`

- [ ] **Step 1: 写失败测试** `tests/test_factor_report_tool.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest


def _stub_panel_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "volume": np.full(len(dates), 1e6),
            }, index=dates)
            df.index.name = "datetime"
            return df
    return StubLoader()


def test_factor_report_tool_runs(monkeypatch):
    from financial_analyst.buddy import tools as t
    # NOTE: engine's factor_report() imports resolve_universe_codes + get_default_loader
    # from their home modules (local imports), so patch THOSE, not buddy's aliases.
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader",
                        lambda: _stub_panel_loader())

    res = t._tool_factor_report(expr_or_name="rank(-delta(close,5))", universe="csi500", freq="week")
    assert res.is_error is False
    assert "RankIC" in res.text or "IC" in res.text
    assert "Sharpe" in res.text or "夏普" in res.text


def test_factor_report_tool_bad_expr(monkeypatch):
    from financial_analyst.buddy import tools as t
    res = t._tool_factor_report(expr_or_name="import os", universe="csi500", freq="week")
    assert res.is_error is True


def test_factor_report_registered():
    from financial_analyst.buddy.tools import TOOLS
    names = {tool.name for tool in TOOLS}
    assert "factor_report" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_report_tool.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_tool_factor_report'`

- [ ] **Step 3: 在 `buddy/tools.py` 加 `_tool_factor_report`**

放在 `_tool_alpha_compare` 之后 (约 `:1430` 后):

```python
def _tool_factor_report(expr_or_name: str, universe: str = "csi500",
                        freq: str = "month", start: str = None, end: str = None) -> ToolResult:
    """完整单因子评测报告: IC全套 + 十分位 + 多空组合净值/Sharpe/回撤 (复用 factors.eval 引擎)。"""
    expr_or_name = (expr_or_name or "").strip()
    if not expr_or_name:
        return ToolResult("factor_report: 缺少 expr_or_name (因子名或表达式)。", is_error=True)
    if "__" in expr_or_name or "import" in expr_or_name or "lambda" in expr_or_name:
        return ToolResult("factor_report: 表达式含非法 token (__ / import / lambda)。", is_error=True)
    try:
        from financial_analyst.factors.eval import EvalConfig, factor_report
        cfg = EvalConfig(universe=universe, freq=freq, start=start, end=end)
        rpt = factor_report(expr_or_name, cfg)
    except Exception as e:
        return ToolResult(f"factor_report 失败: {type(e).__name__}: {e}", is_error=True)

    if rpt.status != "ok":
        return ToolResult(f"因子评测未完成 (status={rpt.status}): {rpt.error}", is_error=True)

    def f(x, d=3):
        import math
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    ic, q, pf, ch, m = rpt.ic, rpt.quantile, rpt.portfolio, rpt.characteristics, rpt.meta
    lines = [
        f"# 因子评测 · {m.factor}",
        f"池 {m.universe} ({m.n_codes} 只) · {m.freq}频 · {m.start}~{m.end} · {m.n_dates} 期 · fwd={m.fwd_days}d",
        "",
        f"IC = {f(ic.ic_mean,4)} | ICIR = {f(ic.icir)} | t = {f(ic.ic_tstat,2)} | 命中 = {f(ic.ic_win_rate,2)}",
        f"RankIC = {f(ic.rank_ic_mean,4)} | RankICIR = {f(ic.rank_icir)}",
        f"十分位单调性 = {f(q.monotonicity,2)} | 多空价差(年化) = {f(q.long_short_spread,3)}",
        f"多空组合: 年化 = {f(pf.ann_return,3)} | Sharpe = {f(pf.sharpe,2)} | 最大回撤 = {f(pf.max_drawdown,3)} | 换手 = {f(pf.turnover,2)} | 胜率 = {f(pf.win_rate,2)}",
        f"覆盖率 = {f(ch.coverage,2)} | 自相关 = {f(ch.autocorr_1,2)} | 半衰期 = {ch.half_life:.0f} 期",
    ]
    if rpt.warnings:
        lines.append("")
        lines += [f"⚠ {w}" for w in rpt.warnings]
    return ToolResult("\n".join(lines))
```

- [ ] **Step 4: 注册 `factor_report` Tool**

在 `buddy/tools.py` 的 `TOOLS = [...]` 列表里, `alpha_compare` Tool (约 `:1713`) 之后插入:

```python
    Tool(
        name="factor_report",
        description=(
            "对一个因子 (已注册名如 alpha019, 或表达式如 rank(-delta(close,5))) 跑【完整单因子评测】: "
            "IC/ICIR/t值/命中率 + IC衰减 + 十分位单调性 + 多空组合净值(年化/Sharpe/最大回撤/换手/胜率)。"
            "比 factor_test 更全 (factor_test 只给 IC, 这个给组合回测)。用于「这因子到底行不行」「给我一份完整评测」。"
            "默认 csi500 月频近 2 年。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expr_or_name": {"type": "string", "description": "因子名或表达式, 如 alpha019 或 rank(-delta(close,5))"},
                "universe": {"type": "string", "default": "csi500", "description": "csi300/csi500/csi800/all/csi300_active 或自选 .txt"},
                "freq": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                "start": {"type": "string", "description": "起始日 YYYY-MM-DD, 缺省今天往前 2 年"},
                "end": {"type": "string", "description": "结束日 YYYY-MM-DD, 缺省今天"},
            },
            "required": ["expr_or_name"],
        },
        run=_tool_factor_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `cd /g/financial-analyst && python -m pytest tests/test_factor_report_tool.py tests/test_buddy.py tests/test_factor_eval.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /g/financial-analyst && git add src/financial_analyst/buddy/tools.py tests/test_factor_report_tool.py && git commit -m "feat(buddy): factor_report tool — full single-factor evaluation report"
```

---

## 收尾 (全部任务后)

- [ ] 全量测试一遍: `cd /g/financial-analyst && python -m pytest tests/ -q` (确认无回归)
- [ ] Dispatch final code-reviewer (整个 SP-A diff)
- [ ] 用 `superpowers:finishing-a-development-branch` 收尾 (merge / PR 决策)

## 自检 (写计划时已过)

**Spec 覆盖:** IC全套(T4) / IC衰减(T4) / 分位+单调性+多空价差(T5) / 多空净值+Sharpe/年化/回撤/换手/胜率(T6) / 去极值+标准化(T3) / 因子特征(T7) / 错误处理结构化(T7) / universe解锁(T2) / 表达式DSL复用(T1) / factor_report工具(T8) / 中性化占位(T3 neutralize stub)。✅ 全覆盖。中性化实现=A.2 (spec 已明确 out-of-scope)。

**占位符扫描:** 无 TBD/TODO; 每个改代码的 step 都有完整代码 + 精确命令 + 预期输出。✅

**类型一致性:** `EvalConfig` 字段在 T3 定义, T6/T7 用 `.periods_per_year()`/`.effective_fwd_days()`/`.cost_bps`/`.n_groups`/`.winsorize_q`/`.standardize`/`.decay_horizons` 一致; `IcResult`/`QuantileResult`/`PortfolioResult`/`FactorChar`/`ReportMeta`/`FactorReport` 字段在 T4-T7 定义并被 T7/T8 一致引用; `_assign_groups` 在 T5 定义, T6 import 复用; `compile_factor`/`validate_expr`(T1)、`resolve_universe_codes`(T2) 被 T7/T8 一致引用。✅
