# 基本面字段进 DSL (SP-B.1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 PanelData + 因子 DSL 加 7 个 daily_basic 字段 (pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate), 让估值/股息/规模因子可表达, forge/factor_test/factor_report/alpha_compare 全自动受益。

**Architecture:** 数据 + 取数 (`loader.fetch_daily_basic`) 已就绪。Task 1 给 `PanelData` 加可选字段属性 + `from_loader` 在 day 频**合并 daily_basic** (后置合并, 仿 industry/benchmark)。Task 2 扩 `expr.py` DSL 词表/命名空间 + 改 `forge._SYSTEM` 措辞。

**Tech Stack:** numpy/pandas, pytest。无新增依赖。

**Spec:** `docs/superpowers/specs/2026-05-29-dsl-fundamentals-design.md`

---

## 关键现状事实 (实现者必读)
- `loader.fetch_daily_basic(code, start, end) -> pd.DataFrame` 是 `BaseLoader` 接口 (`base.py:24`); `QlibBinaryLoader` 实现 (`qlib_binary.py:249`) 返回 7 字段 (pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate), **day 频**, 经 `_build_df` → **trade_date 列 + RangeIndex** (注意: 不是 datetime 索引)。测试 stub loader 通常返回 **datetime 索引** 的 df。合并逻辑要同时容这两种形状。
- `PanelData.from_loader` (`panel.py:133-206`): 逐 code `fetch_quote` → 规范成 (datetime, code) MultiIndex → `pd.concat` → **后置** industry (`:187`) / benchmark (`:194`) 合并 → `return cls(panel)`。**daily_basic 合并照此后置范式加在 benchmark 之后、return 之前。**
- `PanelData.industry` (`panel.py:78`) / `.benchmark_close` (`:88`): 列缺失返回填充 series。新基本面属性照此。
- `PanelData.__init__` 要求 open/high/low/close/volume, 合成 vwap/amount, **额外列 (pe_ttm 等) 自由保留** — 所以合并后的列能被属性读到。
- `expr.py`: `FACTOR_VOCAB` (字符串) + `compile_factor` 的 `ns` dict (字段名→`p.<field>`)。forge 的 `_SYSTEM` 内嵌 FACTOR_VOCAB。
- 测试: `cd /g/financial-analyst && python -m pytest <file> -v`。**dev pandas 2.3.3, 包声明 pandas>=2** → 别用 2.2-only API (`include_groups=` / `freq="ME"`)。**绝不在测试里 `_clear_registry_for_tests()`** (会清空全局 alpha 注册表污染 test_factor_zoo)。

## 文件结构
**修改:**
- `src/financial_analyst/factors/zoo/panel.py` — `_optional_col` 助手 + 7 个属性 + 模块级 `_DAILY_BASIC_FIELDS` + `_merge_daily_basic` + `from_loader` 调用
- `src/financial_analyst/factors/zoo/expr.py` — `FACTOR_VOCAB` + `compile_factor` ns
- `src/financial_analyst/factors/forge/forge.py` — `_SYSTEM` out_of_vocab 措辞
- `tests/test_panel_fundamentals.py` (新) — PanelData 属性 + from_loader 合并
- `tests/test_dsl_fundamentals.py` (新) — expr 词表/编译 + forge 不再 out_of_vocab

---

### Task 1: PanelData 基本面字段属性 + from_loader 合并

**Files:**
- Modify: `src/financial_analyst/factors/zoo/panel.py`
- Create: `tests/test_panel_fundamentals.py`

- [ ] **Step 1: 写失败测试** `tests/test_panel_fundamentals.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.zoo import PanelData


def _df_with(cols_extra: dict):
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C"]], names=["datetime", "code"])
    base = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1e6}
    data = {k: pd.Series(v, index=idx) for k, v in base.items()}
    for k, v in cols_extra.items():
        data[k] = pd.Series(v, index=idx)
    return pd.DataFrame(data)


def test_fundamental_property_returns_column_when_present():
    p = PanelData(_df_with({"pe_ttm": 15.0, "dv_ttm": 2.5, "total_mv": 5e6}))
    assert (p.pe_ttm == 15.0).all()
    assert (p.dv_ttm == 2.5).all()
    assert (p.total_mv == 5e6).all()


def test_fundamental_property_nan_when_absent():
    p = PanelData(_df_with({}))  # only OHLCV
    for name in ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate"]:
        s = getattr(p, name)
        assert isinstance(s, pd.Series)
        assert s.index.equals(p.df.index)
        assert s.isna().all()


def _stub_loader(daily_basic_shape="trade_date_col", db_empty=False):
    """Stub BaseLoader: fetch_quote returns datetime-indexed OHLCV;
    fetch_daily_basic returns either trade_date-column shape (real-loader-like)
    or empty (to test the missing path)."""
    class Stub:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            if db_empty:
                return pd.DataFrame()
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            db = pd.DataFrame({
                "pe_ttm": np.linspace(10, 30, len(dates)),
                "pb": np.linspace(1, 3, len(dates)),
                "ps_ttm": np.linspace(2, 5, len(dates)),
                "dv_ttm": np.linspace(0.5, 4, len(dates)),
                "total_mv": np.linspace(1e6, 5e6, len(dates)),
                "circ_mv": np.linspace(8e5, 4e6, len(dates)),
                "turnover_rate": np.linspace(0.5, 3, len(dates)),
            }, index=dates)
            db.index.name = "trade_date"
            if daily_basic_shape == "trade_date_col":
                return db.reset_index()   # trade_date as a COLUMN (real-loader shape)
            db.index.name = "datetime"
            return db                      # datetime-indexed (stub shape)
    return Stub()


def test_from_loader_merges_daily_basic_trade_date_col():
    panel = PanelData.from_loader(_stub_loader("trade_date_col"),
                                  ["SH600519", "SZ000858", "SH600036"], "2024-01-01", "2024-02-01")
    assert "close" in panel.df.columns and "pe_ttm" in panel.df.columns
    assert panel.pe_ttm.notna().any()
    assert panel.dv_ttm.notna().any()


def test_from_loader_merges_daily_basic_datetime_index():
    panel = PanelData.from_loader(_stub_loader("datetime_index"),
                                  ["SH600519", "SZ000858"], "2024-01-01", "2024-02-01")
    assert panel.pe_ttm.notna().any()


def test_from_loader_daily_basic_missing_ok():
    panel = PanelData.from_loader(_stub_loader(db_empty=True),
                                  ["SH600519", "SZ000858"], "2024-01-01", "2024-02-01")
    assert "close" in panel.df.columns
    assert panel.pe_ttm.isna().all()  # absent → NaN, no crash


def test_from_loader_intraday_skips_daily_basic():
    # 5min freq: daily_basic is day-only; should not attempt/merge, no crash.
    class StubIntraday:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02 09:30", periods=20, freq="5min")
            close = np.full(len(dates), 50.0)
            df = pd.DataFrame({"open": close, "high": close, "low": close,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df
        def fetch_daily_basic(self, code, start, end):
            raise AssertionError("fetch_daily_basic must NOT be called for intraday freq")
    panel = PanelData.from_loader(StubIntraday(), ["SH600519"], "2024-01-02", "2024-01-03", freq="5min")
    assert "close" in panel.df.columns
```

- [ ] **Step 2: 跑测试确认失败** — `cd /g/financial-analyst && python -m pytest tests/test_panel_fundamentals.py -v` → FAIL (`AttributeError: 'PanelData' object has no attribute 'pe_ttm'`).

- [ ] **Step 3: 实现** — in `src/financial_analyst/factors/zoo/panel.py`:

(a) Add a module-level constant near the top (after imports):
```python
_DAILY_BASIC_FIELDS = ("pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate")
```

(b) Add the helper + 7 properties to the `PanelData` class (place after the `industry`/`benchmark_close`/`benchmark_returns` properties, before `returns`):
```python
    def _optional_col(self, name: str) -> pd.Series:
        """Return column ``name`` if present, else an all-NaN Series on the panel
        index. Used by optional daily_basic fundamental fields (may be absent when
        no daily_basic data was loaded for these codes)."""
        if name in self.df.columns:
            return self.df[name]
        return pd.Series(float("nan"), index=self.df.index, dtype=float)

    @property
    def pe_ttm(self) -> pd.Series:
        return self._optional_col("pe_ttm")

    @property
    def pb(self) -> pd.Series:
        return self._optional_col("pb")

    @property
    def ps_ttm(self) -> pd.Series:
        return self._optional_col("ps_ttm")

    @property
    def dv_ttm(self) -> pd.Series:
        """股息率 (%). NaN when daily_basic absent."""
        return self._optional_col("dv_ttm")

    @property
    def total_mv(self) -> pd.Series:
        """总市值 (万元)."""
        return self._optional_col("total_mv")

    @property
    def circ_mv(self) -> pd.Series:
        """流通市值 (万元)."""
        return self._optional_col("circ_mv")

    @property
    def turnover_rate(self) -> pd.Series:
        """换手率 (%)."""
        return self._optional_col("turnover_rate")
```

(c) Add a module-level merge helper (after the class, or before `from_loader` — module level):
```python
def _merge_daily_basic(panel: pd.DataFrame, loader, codes: list, start: str, end: str) -> None:
    """Merge each code's daily_basic fields onto the (datetime, code) panel in place.

    Robust to both real-loader shape (trade_date column) and stub shape
    (datetime index). Guarded: a loader without the data, or a code missing
    daily_basic, simply yields NaN columns rather than raising.
    """
    frames = []
    for code in codes:
        try:
            db = loader.fetch_daily_basic(code, start, end)
        except Exception:
            continue
        if db is None or len(db) == 0:
            continue
        db = db.copy()
        if "trade_date" in db.columns:
            db = db.set_index("trade_date")
        try:
            db.index = pd.DatetimeIndex(db.index)
        except Exception:
            continue
        db["code"] = code
        db = db.set_index("code", append=True)
        db.index = db.index.set_names(["datetime", "code"])
        keep = [c for c in _DAILY_BASIC_FIELDS if c in db.columns]
        if keep:
            frames.append(db[keep])
    if not frames:
        return
    db_all = pd.concat(frames)
    db_all = db_all[~db_all.index.duplicated(keep="last")]
    for col in _DAILY_BASIC_FIELDS:
        if col in db_all.columns:
            panel[col] = db_all[col].reindex(panel.index)
```

(d) In `from_loader`, right before `return cls(panel)` (after the benchmark block), add:
```python
        # SP-B.1b: 合并 daily_basic 基本面字段 (仅 day 频; daily_basic 只有 day 频)。
        if freq == "day":
            _merge_daily_basic(panel, loader, codes, start, end)

        return cls(panel)
```
(Replace the existing bare `return cls(panel)` with the guarded merge + return.)

- [ ] **Step 4: 跑测试确认通过** — `cd /g/financial-analyst && python -m pytest tests/test_panel_fundamentals.py -v` → all pass.

- [ ] **Step 5: 回归** — `cd /g/financial-analyst && python -m pytest tests/test_factor_zoo.py tests/test_factor_eval.py -q` → no regression (PanelData change must not break existing panel/bench/eval).

- [ ] **Step 6: Commit**
```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/zoo/panel.py tests/test_panel_fundamentals.py && git commit -m "feat(panel): expose daily_basic fundamental fields + merge in from_loader (day freq)"
```

---

### Task 2: expr DSL 词表/命名空间 + forge 措辞

**Files:**
- Modify: `src/financial_analyst/factors/zoo/expr.py`
- Modify: `src/financial_analyst/factors/forge/forge.py`
- Create: `tests/test_dsl_fundamentals.py`

- [ ] **Step 1: 写失败测试** `tests/test_dsl_fundamentals.py`

```python
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, compile_factor
from financial_analyst.factors.zoo import PanelData


def _fund_panel():
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(2)
    close = pd.Series(50.0, index=idx)
    df = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close,
        "volume": pd.Series(1e6, index=idx),
        "pe_ttm": pd.Series(rng.uniform(8, 40, len(idx)), index=idx),
        "pb": pd.Series(rng.uniform(0.8, 5, len(idx)), index=idx),
        "dv_ttm": pd.Series(rng.uniform(0, 5, len(idx)), index=idx),
        "total_mv": pd.Series(rng.uniform(1e6, 5e7, len(idx)), index=idx),
    })
    return PanelData(df)


def test_vocab_lists_fundamentals():
    for f in ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate"]:
        assert f in FACTOR_VOCAB


@pytest.mark.parametrize("expr", [
    "rank(-pe_ttm)",          # 低估值
    "rank(dv_ttm)",           # 高股息
    "rank(-total_mv)",        # 小盘
    "rank(-pb) * rank(dv_ttm)",  # 低估值×高股息
])
def test_compile_fundamental_expr(expr):
    fn = compile_factor(expr)
    out = fn(_fund_panel())
    assert isinstance(out, pd.Series)
    assert out.index.names == ["datetime", "code"]
    assert out.notna().any()


def test_forge_fundamental_not_out_of_vocab():
    """A 高股息 idea now compiles (dv_ttm is in the DSL) — proves the field
    reached compile_factor's namespace, not just the vocab string."""
    from financial_analyst.factors.forge.forge import forge_factor
    good = json.dumps({"expr": "rank(dv_ttm)", "parsed": [{"k": "方向", "v": "高股息"}],
                       "name": "usr_divyield", "rationale": "股息率排序", "out_of_vocab": False})
    r = forge_factor("高股息", complete_fn=lambda messages: good)
    assert r.compile_ok is True
    assert r.out_of_vocab is False
    assert r.expr == "rank(dv_ttm)"
```

- [ ] **Step 2: 跑测试确认失败** — `cd /g/financial-analyst && python -m pytest tests/test_dsl_fundamentals.py -v` → FAIL (`pe_ttm` not in FACTOR_VOCAB / NameError in compile).

- [ ] **Step 3: 实现.**

(a) `src/financial_analyst/factors/zoo/expr.py` — READ the current `FACTOR_VOCAB` and replace it with (adds a 基本面 field segment):
```python
FACTOR_VOCAB = (
    "字段(价量): close open high low volume vwap amount returns industry | "
    "字段(基本面,day频): pe_ttm pb ps_ttm dv_ttm(股息率%) total_mv circ_mv(总/流通市值,万元) turnover_rate(换手%) | "
    "算子: rank ts_rank delta delay ts_mean ts_sum ts_max ts_min ts_argmax ts_argmin "
    "stddev correlation(x,y,n) covariance decay_linear sma wma signedpower(x,p) "
    "log sign abs power(x,p) scale indneutralize(x,industry) max_pair min_pair filter_where | "
    "运算: + - * / ** 比较 ()"
)
```

(b) `expr.py` `compile_factor` — in the `ns` dict, after the existing field entries (`"returns": p.returns, "industry": p.industry,`), add the 7 fundamentals:
```python
            "pe_ttm": p.pe_ttm, "pb": p.pb, "ps_ttm": p.ps_ttm, "dv_ttm": p.dv_ttm,
            "total_mv": p.total_mv, "circ_mv": p.circ_mv, "turnover_rate": p.turnover_rate,
```

- [ ] **Step 4: 改 forge 措辞** — in `src/financial_analyst/factors/forge/forge.py`, READ `_SYSTEM`. Find the out_of_vocab sentence (currently mentions "基本面 pe/pb/股息/ROE/市值"). Replace ONLY that sentence with one that reflects fundamentals are now supported:
```python
    "估值(pe_ttm/pb/ps_ttm)、股息(dv_ttm)、规模(total_mv/circ_mv)、换手(turnover_rate) 已支持。"
    "若想法需要表中没有的字段 (财报字段如 ROE/净利润/负债率, 需财报数据; 或'连续/金叉/突破'这类事件条件), "
    "把 out_of_vocab 设 true 并在 rationale 里说明缺什么, expr 留空。\n"
```
(Keep the rest of `_SYSTEM` — the JSON-output instruction, the no-builtins line — unchanged. Optionally add one few-shot pair to `_FEWSHOT`: user "高股息" → assistant `{"expr":"rank(dv_ttm)","parsed":[{"k":"方向","v":"股息率高→看好"}],"name":"usr_divyield","rationale":"股息率排序","out_of_vocab":false}`.)

- [ ] **Step 5: 跑测试确认通过 + 回归** — `cd /g/financial-analyst && python -m pytest tests/test_dsl_fundamentals.py tests/test_factor_forge.py tests/test_factor_expr.py tests/test_buddy.py -q` → all pass.

- [ ] **Step 6: Commit**
```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/zoo/expr.py src/financial_analyst/factors/forge/forge.py tests/test_dsl_fundamentals.py && git commit -m "feat(dsl): add daily_basic fundamentals to FACTOR_VOCAB + compile ns + forge prompt"
```

---

## 收尾 (全部任务后)
- [ ] 全量回归: `cd /g/financial-analyst && python -m pytest tests/test_panel_fundamentals.py tests/test_dsl_fundamentals.py tests/test_factor_forge.py tests/test_factor_expr.py tests/test_factor_eval.py tests/test_factor_zoo.py tests/test_buddy.py -q` (含 test_factor_zoo 确认无注册表污染)
- [ ] Dispatch final code-reviewer (整个 SP-B.1b diff)
- [ ] `superpowers:finishing-a-development-branch` 收尾

## 自检 (写计划时已过)
**Spec 覆盖:** PanelData 属性(T1) / from_loader day 频合并 + 缺数据 NaN + 两种 db 形状 + intraday 跳过(T1) / FACTOR_VOCAB + compile ns 7 字段(T2) / forge 措辞(T2) / forge 不再 out_of_vocab(T2 test) / 单位注释(T2 vocab) / 回归不污染注册表(T1+T2 跑 test_factor_zoo)。✅ 全覆盖。财报字段/事件/UI 明确 out-of-scope。

**占位符扫描:** 无 TBD/TODO; 每个改代码 step 有完整代码 + 命令 + 预期。forge `_SYSTEM` / expr `FACTOR_VOCAB` 让实现者先 READ 再替换 (字符串可能微调过), 给了精确新内容。✅

**类型一致:** `_DAILY_BASIC_FIELDS` 元组在 T1 定义, `_merge_daily_basic` + `_optional_col` 用它; 7 属性名 (pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate) 在 panel 属性(T1) / compile ns(T2) / 测试 一致; `compile_factor` ns 的 `p.pe_ttm` 等引用 T1 的属性。✅
