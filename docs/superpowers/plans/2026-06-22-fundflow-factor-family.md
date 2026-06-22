# 资金面订单流因子族 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把东财稠密五档资金流接进引擎 panel,新增 6 个截面订单流因子,走完整因子工作流(panel → DSL → `_FACTOR_CATALOG` → 选股因子库 → regen 算真 IC)。

**Architecture:** 一个共享地基喂多个出口,沿用 W1b 财务字段范式。`panel.py` 加 `_apply_fund_flow`(纯合并)+ `_load_fund_flow_df`(读盘)+ `_merge_fund_flow`(组合,在 `PanelData.build` 的 day 频分支调用);`expr.py` 注册 10 字段(compile 侧靠已有动态注入零改,只动白名单 + VOCAB);`_FACTOR_CATALOG` 加 6 因子,`screen/catalog.py` 自动复用。

**Tech Stack:** Python 3.13、pandas/numpy、pytest;引擎 `engine/financial_analyst`(zoo DSL),guanlan_v2 薄壳。

**前置参考:** 设计文档 `docs/superpowers/specs/2026-06-22-fundflow-factor-family-design.md`。

**全局坑(每个任务都适用):**
- **GateGuard**:每个文件首次编辑前先报 facts(① 谁 import 它 ② 受影响公共符号 ③ 读写的数据文件字段/结构 ④ 用户指令逐字)。
- **引擎 fork 路径**:测试文件顶部须把仓内 `engine/` prepend 进 `sys.path`(见 Task 1 模板),否则导入到 venv 旧分支。
- **改引擎须重启 9999**:改 `panel.py`/`expr.py` 后,杀 9999 监听进程,等看门狗 ~10s 拉新代码(Task 5)。
- **pytest 从仓根跑**:`cd G:/guanlan-v2 && python -m pytest ...`。

---

### Task 1: `_apply_fund_flow` 纯合并函数 + `_FUND_FLOW_FIELDS`

把一个长格式资金流 DataFrame 精确合并到 (datetime, code) 面板;**不 ffill**、缺即 NaN、10 列恒在。纯函数,易测。

**Files:**
- Modify: `engine/financial_analyst/factors/zoo/panel.py`(在 `_FINANCIAL_FIELDS`(:27)后加常量;在 `_merge_financials`(:117)后加函数)
- Test: `tests/test_fund_flow_panel.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fund_flow_panel.py`:

```python
# tests/test_fund_flow_panel.py
# 资金面五档资金流接入 panel 的门禁:纯合并语义(精确日匹配·不 ffill·缺即 NaN·10列恒在)。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.panel import _apply_fund_flow, _FUND_FLOW_FIELDS  # noqa: E402


def _mk_panel(dates, codes):
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(dates), codes], names=["datetime", "code"]
    )
    return pd.DataFrame({"close": 1.0}, index=idx)


def test_fund_flow_fields_count():
    assert len(_FUND_FLOW_FIELDS) == 10


def test_apply_exact_match_and_nan():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000", "SZ000001"])
    ff = pd.DataFrame({
        "code": ["SH600000", "SH600000"],
        "trade_date": ["2026-06-17", "2026-06-18"],
        "main_net_pct": [1.5, -2.0],
        "main_net_amount": [100.0, -200.0],
    })
    _apply_fund_flow(panel, ff)
    for col in _FUND_FLOW_FIELDS:
        assert col in panel.columns
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 1.5
    assert panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_amount"] == -200.0
    # 未匹配的票 → NaN
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-17"), "SZ000001"), "main_net_pct"])


def test_apply_no_ffill():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000"])
    ff = pd.DataFrame({"code": ["SH600000"], "trade_date": ["2026-06-17"], "main_net_pct": [3.0]})
    _apply_fund_flow(panel, ff)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 3.0
    # D=18 无数据 → 必须 NaN(绝不把 D=17 的流量前向填充)
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_pct"])


def test_apply_empty_adds_nan_columns():
    panel = _mk_panel(["2026-06-17"], ["SH600000"])
    _apply_fund_flow(panel, pd.DataFrame())
    for col in _FUND_FLOW_FIELDS:
        assert col in panel.columns
        assert panel[col].isna().all()


def test_apply_preserves_index():
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000", "SZ000001"])
    before = panel.index.tolist()
    _apply_fund_flow(panel, pd.DataFrame())
    assert panel.index.tolist() == before
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_panel.py -v`
Expected: FAIL —— `ImportError: cannot import name '_apply_fund_flow'`(或 `_FUND_FLOW_FIELDS`)。

- [ ] **Step 3: 写最小实现**

在 `engine/financial_analyst/factors/zoo/panel.py` 的 `_FINANCIAL_FIELDS = (...)`(约 :27-30)之后加常量:

```python
# 资金面:东财五档日频净流入(主力/超大/大/中/小单的净额与净占比)。
# day 频 EOD 可见(visible_ts = trade_date 23:59:59),与 volume/amount 同口径放置。
_FUND_FLOW_FIELDS = (
    "main_net_amount", "main_net_pct",
    "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct",
    "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",
)
```

在 `_merge_financials` 函数(以 :171 的 `panel[col] = ...` 结束)之后、`class PanelData`(:173)之前加:

```python
def _apply_fund_flow(panel: pd.DataFrame, ff_df) -> None:
    """把长格式东财资金流 ``ff_df`` 精确合并到 (datetime, code) 面板,IN PLACE。

    精确 (trade_date, code) 匹配 —— **不 ffill**(资金流是当日流量,缺失日保持
    NaN,绝不沿用陈旧流量)。无论 ff_df 是否为空,10 个 ``_FUND_FLOW_FIELDS`` 列
    都会建出(未匹配处 NaN),使 DSL 因子求值为 NaN 而非 NameError。PIT:数据当日
    EOD 可见,与 volume/amount 放置口径一致,不看未来。"""
    for col in _FUND_FLOW_FIELDS:
        panel[col] = np.nan
    if ff_df is None or len(ff_df) == 0:
        return
    ff = ff_df.copy()
    ff["__dt"] = pd.to_datetime(ff["trade_date"])
    ff = ff.set_index(["__dt", "code"])
    ff.index = ff.index.set_names(["datetime", "code"])
    ff = ff[~ff.index.duplicated(keep="last")]
    for col in _FUND_FLOW_FIELDS:
        if col in ff.columns:
            panel[col] = ff[col].reindex(panel.index)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_panel.py -v`
Expected: PASS(5 passed)。

- [ ] **Step 5: 提交**

```bash
git add tests/test_fund_flow_panel.py engine/financial_analyst/factors/zoo/panel.py
git commit -m "feat(fundflow): _apply_fund_flow 纯合并(精确日·不ffill·缺即NaN·10列恒在)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `_load_fund_flow_df` 读盘 + `_merge_fund_flow` 组合 + 接入 build

读东财 parquet(instrument→code、按 codes/窗口过滤、缺文件→空),组合成 `_merge_fund_flow`,在 `PanelData.build` 的 day 频分支调用。

**Files:**
- Modify: `engine/financial_analyst/factors/zoo/panel.py`(加 `_load_fund_flow_df` + `_merge_fund_flow`;在 :481 `_merge_financials(...)` 后加调用)
- Test: `tests/test_fund_flow_panel.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/test_fund_flow_panel.py` 末尾追加:

```python
def test_load_reads_filters_and_maps_instrument(tmp_path):
    from financial_analyst.factors.zoo.panel import _load_fund_flow_df
    raw = pd.DataFrame({
        "instrument": ["SH600000", "SH600000", "SZ000001"],
        "code": ["600000", "600000", "000001"],
        "trade_date": pd.to_datetime(["2026-06-17", "2026-06-10", "2026-06-17"]),
        "main_net_pct": [1.0, 2.0, 3.0],
        "main_net_amount": [10.0, 20.0, 30.0],
    })
    raw.to_parquet(tmp_path / "eastmoney_stock_fund_flow_daily.parquet")
    out = _load_fund_flow_df(["SH600000"], "2026-06-15", "2026-06-18", parquet_root=tmp_path)
    # instrument→code 命名;只留窗口内(06-17)的 SH600000,不含 06-10、不含 SZ000001
    assert list(out["code"].unique()) == ["SH600000"]
    assert len(out) == 1
    assert float(out.iloc[0]["main_net_pct"]) == 1.0


def test_load_missing_file_returns_empty(tmp_path):
    from financial_analyst.factors.zoo.panel import _load_fund_flow_df
    out = _load_fund_flow_df(["SH600000"], None, None, parquet_root=tmp_path)
    assert len(out) == 0


def test_merge_fund_flow_end_to_end(tmp_path):
    from financial_analyst.factors.zoo.panel import _merge_fund_flow
    raw = pd.DataFrame({
        "instrument": ["SH600000"],
        "code": ["600000"],
        "trade_date": pd.to_datetime(["2026-06-17"]),
        "main_net_pct": [1.5],
    })
    raw.to_parquet(tmp_path / "eastmoney_stock_fund_flow_daily.parquet")
    panel = _mk_panel(["2026-06-17", "2026-06-18"], ["SH600000"])
    _merge_fund_flow(panel, None, ["SH600000"], "2026-06-15", "2026-06-18", parquet_root=tmp_path)
    assert panel.loc[(pd.Timestamp("2026-06-17"), "SH600000"), "main_net_pct"] == 1.5
    assert np.isnan(panel.loc[(pd.Timestamp("2026-06-18"), "SH600000"), "main_net_pct"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_panel.py -k "load or end_to_end" -v`
Expected: FAIL —— `ImportError: cannot import name '_load_fund_flow_df'`。

- [ ] **Step 3: 写最小实现**

在 `panel.py` 的 `_apply_fund_flow` 之后加两个函数:

```python
def _load_fund_flow_df(codes, start, end, parquet_root=None):
    """读东财五档资金流长表,过滤到 ``codes`` ∩ [start, end]。

    返回列 [code, trade_date, *存在的 _FUND_FLOW_FIELDS],``code`` 为面板口径
    (qlib instrument,如 'SH600000')。parquet 缺失 / 无关键列 / 读失败 → **空表**
    (诚实无数据路径)。``parquet_root`` 缺省经 get_data_paths()。"""
    if parquet_root is None:
        try:
            from financial_analyst.data.paths import get_data_paths
            parquet_root = get_data_paths().parquet_root
        except Exception:
            return pd.DataFrame()
    path = os.path.join(str(parquet_root), "eastmoney_stock_fund_flow_daily.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    if "instrument" not in df.columns or "trade_date" not in df.columns:
        return pd.DataFrame()
    df = df.rename(columns={"instrument": "code"})
    df = df[df["code"].isin(set(codes))].copy()
    if len(df) == 0:
        return pd.DataFrame()
    df["__dt"] = pd.to_datetime(df["trade_date"])
    if start is not None:
        df = df[df["__dt"] >= pd.to_datetime(start)]
    if end is not None:
        df = df[df["__dt"] <= pd.to_datetime(end)]
    keep = ["code", "trade_date"] + [c for c in _FUND_FLOW_FIELDS if c in df.columns]
    return df[keep].copy()


def _merge_fund_flow(panel: pd.DataFrame, loader, codes: list, start: str, end: str,
                     parquet_root=None) -> None:
    """把东财五档日频资金流合并到 (datetime, code) 面板(仅 day 频调用)。
    ``loader`` 不用(留参以与 _merge_financials 等同形);文件缺 → 加 10 个 NaN 列。"""
    ff = _load_fund_flow_df(codes, start, end, parquet_root=parquet_root)
    _apply_fund_flow(panel, ff)
```

然后在 `PanelData.build` 的 day 频分支(:481 `_merge_financials(panel, loader, codes, start, end)` 之后)追加一行:

```python
            _merge_financials(panel, loader, codes, start, end)
            # 资金面:东财五档日频净流入(EOD PIT;缺即 NaN;对旧因子是 no-op 加列)。
            _merge_fund_flow(panel, loader, codes, start, end)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_panel.py -v`
Expected: PASS(8 passed)。

- [ ] **Step 5: 提交**

```bash
git add tests/test_fund_flow_panel.py engine/financial_analyst/factors/zoo/panel.py
git commit -m "feat(fundflow): 读盘 _load_fund_flow_df + _merge_fund_flow 接入 PanelData.build

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `expr.py` 注册 10 个资金面字段

让 `validate_expr` 接受资金面字段名(compile 侧靠 `expr.py:115` 动态注入,无需改命名空间)。

**Files:**
- Modify: `engine/financial_analyst/factors/zoo/expr.py`(`FACTOR_VOCAB` :11 加一段;`_FIELD_NAMES` :39 加 10 名)
- Test: `tests/test_fund_flow_expr.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fund_flow_expr.py`:

```python
# tests/test_fund_flow_expr.py
# 资金面字段已注册进 DSL 白名单 + VOCAB,validate_expr 不再误判为未知字段。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import (  # noqa: E402
    _FIELD_NAMES, validate_expr, FACTOR_VOCAB,
)

_FF = [
    "main_net_amount", "main_net_pct",
    "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct",
    "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",
]


def test_all_fund_flow_fields_whitelisted():
    for f in _FF:
        assert f in _FIELD_NAMES, f"{f} 未进 _FIELD_NAMES"


def test_validate_accepts_fund_flow_expr():
    # 之前会抛「未知字段」;注册后不抛。
    validate_expr("rank(ts_mean(main_net_pct,5))")
    validate_expr("rank((super_large_net_pct+large_net_pct)-(medium_net_pct+small_net_pct))")
    validate_expr("rank(ts_sum(sign(main_net_amount),10))")


def test_vocab_mentions_fund_flow():
    assert "main_net_pct" in FACTOR_VOCAB
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_expr.py -v`
Expected: FAIL —— `test_all_fund_flow_fields_whitelisted` 断言失败 + `validate_expr` 抛「未知字段」。

- [ ] **Step 3: 写最小实现**

在 `expr.py` 的 `_FIELD_NAMES = frozenset({...})`(:39-46)里,在 `"idx_ret", "ref_ret", "benchmark_close",` 行之前加:

```python
    "main_net_amount", "main_net_pct", "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct", "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",  # 资金面(东财五档·day频·EOD PIT)
```

在 `FACTOR_VOCAB`(:11-26)的财务字段段之后、参照字段段(`"字段(参照,壳注入,选配): ..."`)之前,插入一段:

```python
    "字段(资金面,day频·EOD PIT·缺则NaN): main_net_amount/pct super_large_net_amount/pct "
    "large_net_amount/pct medium_net_amount/pct small_net_amount/pct"
    "(主力/超大/大/中/小单净流入额与净占比) | "
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_expr.py -v`
Expected: PASS(3 passed)。

- [ ] **Step 5: 提交**

```bash
git add tests/test_fund_flow_expr.py engine/financial_analyst/factors/zoo/expr.py
git commit -m "feat(fundflow): expr.py 注册10个资金面字段(白名单+VOCAB)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `_FACTOR_CATALOG` 资金面族 6 因子 + 选股目录顺序

**Files:**
- Modify: `guanlan_v2/workflow/api.py`(`_FACTOR_CATALOG` :369 末尾加 6 条;`_FACTOR_CATS` :370 加 "资金面")
- Modify: `guanlan_v2/screen/catalog.py`(`FAMILY_ORDER` :107 加 "资金面")
- Test: `tests/test_fund_flow_catalog.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fund_flow_catalog.py`:

```python
# tests/test_fund_flow_catalog.py
# 资金面族 6 因子:进了 _FACTOR_CATALOG/_FACTOR_CATS,表达式合法、只用白名单名、能解析;
# 且在选股目录 FAMILY_ORDER 里浮现。
import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import validate_expr, _KNOWN_NAMES  # noqa: E402
from guanlan_v2.workflow.api import _FACTOR_CATALOG, _FACTOR_CATS  # noqa: E402


def _ff_factors():
    return [(n, e, c, d, desc) for (n, e, c, d, desc) in _FACTOR_CATALOG if c == "资金面"]


def test_six_fund_flow_factors_registered():
    assert len(_ff_factors()) == 6
    assert "资金面" in _FACTOR_CATS


def test_fund_flow_exprs_valid_and_whitelisted():
    for name, expr, cat, direction, desc in _ff_factors():
        validate_expr(expr)                       # 注册字段 + 无禁词
        compile(expr, f"<{name}>", "eval")        # python 语法可解析
        used = {n.id for n in ast.walk(ast.parse(expr, mode="eval"))
                if isinstance(n, ast.Name)}
        illegal = used - _KNOWN_NAMES
        assert not illegal, f"{name} 用了清单外名字: {illegal}"
        assert direction in ("正向", "反向"), f"{name} 方向标注不合规: {direction}"


def test_fund_flow_in_screen_family_order():
    from guanlan_v2.screen.catalog import FAMILY_ORDER
    assert "资金面" in FAMILY_ORDER
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_catalog.py -v`
Expected: FAIL —— `test_six_fund_flow_factors_registered`(0 != 6)。

- [ ] **Step 3: 写最小实现**

在 `guanlan_v2/workflow/api.py` 的 `_FACTOR_CATALOG` 列表里、最后一条(:368 「跟随稳定度」)之后、闭合 `]`(:369)之前,加 6 条:

```python
    # —— 资金面(东财五档日频净流入·截面 rank·方向为假设待实测IC验真) ——
    ("主力净流入强度", "rank(ts_mean(main_net_pct,5))", "资金面", "正向", "近5日主力净占比均值,主力持续流入(方向假设,IC验真)"),
    ("超大单倾向", "rank(ts_mean(super_large_net_pct,5))", "资金面", "正向", "近5日超大单净占比,机构/大资金方向(方向假设)"),
    ("主力净流入动量", "rank(ts_sum(main_net_pct,10))", "资金面", "正向", "近10日累计主力净占比(方向假设)"),
    ("连续净流入", "rank(ts_sum(sign(main_net_amount),10))", "资金面", "正向", "近10日主力净流入天数(sign 计净流入日;方向假设)"),
    ("资金集中度", "rank((super_large_net_pct+large_net_pct)-(medium_net_pct+small_net_pct))", "资金面", "正向", "大资金净流入 减 中小单净流入(大资金主导;方向假设)"),
    ("散户出逃", "rank(-ts_mean(small_net_pct,5))", "资金面", "反向", "近5日小单净流出(常伴主力吸筹;方向假设)"),
```

把 `_FACTOR_CATS`(:370)改为在末尾加 "资金面":

```python
_FACTOR_CATS = ["动量反转", "估值", "财务质量", "成长", "波动率", "流动性", "技术", "规模", "情绪", "反弹", "消息面", "共振", "跟随", "资金面"]
```

在 `guanlan_v2/screen/catalog.py` 的 `FAMILY_ORDER`(:107)里加 "资金面"(放在 "流动性" 之后),例如:

```python
FAMILY_ORDER = ["动量反转", "技术", "估值", "财务质量", "成长", "波动率", "流动性", "资金面",
```
(保留该列表其余项原样不变,只插入 "资金面" 一项。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/guanlan-v2 && python -m pytest tests/test_fund_flow_catalog.py -v`
Expected: PASS(3 passed)。

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd G:/guanlan-v2 && python -m pytest -q`
Expected: 全绿(新增 ~11 测,既有不破)。

```bash
git add tests/test_fund_flow_catalog.py guanlan_v2/workflow/api.py guanlan_v2/screen/catalog.py
git commit -m "feat(fundflow): _FACTOR_CATALOG 资金面族6因子 + 选股目录浮现

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 真数据验证(重启 9999 + 真面板算值 + 浏览器)

非 TDD,验证端到端在真数据上跑通、薄样本诚实显形。

**Files:** 无(验证);临时脚本 `var/_verify_fundflow.py`(scratch,可丢)。

- [ ] **Step 1: 重启 9999 拉新引擎代码**

```powershell
Get-NetTCPConnection -LocalPort 9999 -State Listen | Select-Object -Expand OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```
等 ~10s 看门狗自动拉起(`scripts/watchdog_9999.ps1`)。确认起来:`Invoke-WebRequest http://127.0.0.1:9999/factor/catalog -UseBasicParsing | Select-Object -Expand StatusCode`(期望 200)。

- [ ] **Step 2: 目录浮现校验**

```powershell
(Invoke-WebRequest "http://127.0.0.1:9999/factor/catalog" -UseBasicParsing).Content | python -c "import sys,json; d=json.load(sys.stdin); fs=d.get('factors',d) if isinstance(d,dict) else d; ff=[f for f in fs if isinstance(f,dict) and (f.get('cat')=='资金面' or f.get('family')=='资金面')]; print('资金面因子数:', len(ff)); [print(' -', f.get('name')) for f in ff]"
```
Expected: 「资金面因子数: 6」+ 6 个名字。(若 catalog 顶层结构不同,按 `guanlan_v2/workflow/api.py` 的 `/factor/catalog` 路由响应形调整解析。)

- [ ] **Step 3: 真面板算值(确认资金流列被真填充、6 因子算出非全 NaN)**

POST `/factor/preview`(W2 端点,入参形见 `guanlan_v2/workflow/api.py` 的 `FactorPreviewIn` 模型)对每个资金面因子在小池、覆盖 2026-06-17/18 的窗口求值。逐因子期望:`ok:true` 且 `coverage > 0`(证明真资金流数据被合并、因子在真数据上编译求值)。

例(先确认 `FactorPreviewIn` 的字段名再发):
```powershell
$body = '{"expr":"rank(ts_mean(main_net_pct,5))","universe":"csi300","start":"2026-06-10","end":"2026-06-19"}'
Invoke-WebRequest "http://127.0.0.1:9999/factor/preview" -Method POST -ContentType "application/json" -Body $body -UseBasicParsing | Select-Object -Expand Content
```
Expected: `ok:true`,样本非空,`coverage > 0`(06-17/18 宽覆盖日)。

- [ ] **Step 4: 真 IC(regen 因子 IC 步骤)**

跑项目既有 regen 的因子 IC 步骤(`guanlan_v2/strategy/compute/regen.py` 的 step 3.5)覆盖资金面 6 因子;若全量太重,按项目惯例只触发 csi300 因子 IC 重算。
Expected:6 因子各产出真 rank-IC / ICIR 或**诚实 NaN/「—」**(横截面薄 → 样本少时不显著、不造数)。记录实际值。

- [ ] **Step 5: 浏览器确认(选股因子库)**

用 chrome-devtools 打开选股页因子库,确认「资金面」族出现、含 6 因子、每个带真 IC 或诚实「—」。截图存档。

- [ ] **Step 6: 旧路径回归 + 收尾**

确认不引用资金面字段的既有因子 IC / 选股结果**不变**(`_merge_fund_flow` 对旧因子是 no-op 加列)。删除临时脚本 `var/_verify_fundflow.py`。本任务通常无代码提交;若验证中发现需修(方向取反、coverage 解析等),按 TDD 补测再改再提交。

---

## Self-Review(已对 spec 核对)

- **Spec §4/§5 地基**:Task 1+2 覆盖 `_merge_fund_flow`(panel.py,精确日·不 ffill·缺即 NaN·PIT)。✓
- **Spec §5.2 expr.py**:Task 3 覆盖 `_FIELD_NAMES` + `FACTOR_VOCAB`;compile 侧不改(动态注入)。✓
- **Spec §5.3/5.4/§6 目录+6因子**:Task 4 覆盖 `_FACTOR_CATALOG`+`_FACTOR_CATS`+`FAMILY_ORDER`,6 因子表达式与 spec §6 逐字一致。✓
- **Spec §5.5/§9 IC+验证**:Task 5 覆盖 regen IC + 浏览器 + 旧路径回归。✓
- **Spec §7 诚实/PIT 红线**:Task 1 测「不 ffill」「空→NaN」;Task 5 测薄 IC 诚实显形。✓
- **占位扫描**:无 TBD/TODO;每个代码步给完整代码。Task 5 的 `/factor/preview` 入参标「先确认字段名」属验证任务的合理探查,非实现占位。
- **类型一致**:`_FUND_FLOW_FIELDS`(10)在 Task 1 定义,Task 2/3/4 引用一致;`_apply_fund_flow`/`_load_fund_flow_df`/`_merge_fund_flow` 签名跨任务一致;6 因子表达式字段全在已注册的 10 字段内,`sign` 算子已存在。✓
